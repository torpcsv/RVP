"""=371: イベント遷移図の「カギ線」(直交)ルーティング。

円と円を**縦・横の線分だけ**で結び、直角に曲げる。線は円の上・下・左・右
(4つの口)からだけ出入りする。添え字(Nステート/選択肢/動画)のある円の
下の口は、添え字の**さらに下**から出入りする(文字の上を線が通らない)。

経路の良さ(ユーザー要望: 曲がる回数は 0 → 1 → 2 → … の順に優先、他の円の
上を通りそうなら迂回):
  1. 他の円とその添え字を**跨がない**(最優先・別枠で数える)
  2. 評価値 = 曲がり×W_BEND + 関係のない線との重なり×W_OVER
             + 交差×W_CROSS + 束ねの重なり×W_SOFT + 長さ×W_LEN
     「関係のない線」= 同じ円から出る線どうし/同じ円へ入る線どうし(束ね)
     以外。行きと帰りの線が重なると片方が見えなくなるので重く数える。

探索は「曲がる回数」ごとに段階的に行う。曲がる回数 b の経路は自由座標が
b-1 個(縦横交互)なので、候補座標(円の外側の通り道・円と円の間の中央・
口から出てすぐの位置)を組み合わせて試す。ある段で「跨がず・関係のない線と
重ならない」経路が見つかれば、それより曲がる経路は探さない(曲がる回数を
優先)。見つからなければ MAX_BENDS まで探し、評価値が最良のものを取る。

線は短い順に1本ずつ決め、決まった線は後の線の「重なり・交差」の判定に使う。
入力が同じなら結果も同じ(描き直すたびに形が変わらない)。結果は入力ごとに
キャッシュする(再生タブは遷移のたびに描き直すため)。
"""
from __future__ import annotations

import bisect

R = 26          # 円の半径(scenario_map.NODE_R と同じ)
CLEAR = 9       # 線と他の円の間に空ける余白
STUB = 12       # 円から出て最初に曲がるまでの最短距離
LABEL_H = 11    # 添え字1段の高さ(scenario_map と同じ)
LABEL_PAD = 6   # 添え字の下の余白
LABEL_HALF_W = 34  # 添え字の横幅の半分(「▶動画 12.3秒」程度まで)
STUB_IN = 18    # 最後に曲がってから円へ入るまでの最短距離(矢頭のぶん長め)
LANE = 8        # 平行に並ぶ線の間隔
MAX_BENDS = 4
# 評価の重み(円を跨がないことは別枠で最優先)
W_BEND = 100     # 曲がり1回
W_OVER = 4       # 関係のない線と重なる長さ(px あたり)
W_CROSS = 25     # 他の線との交差1か所
W_SOFT = 0.3     # 同じ円から出る/入る線どうしの重なり(px あたり)
W_LEN = 0.5      # 長さ(px あたり)
EDGE_MIN = 4     # 図の左端・上端より外へは線を出さない
_K = (0, 16, 16, 7, 5)   # 曲がる回数ごとに、自由座標1つあたり試す候補の数

DIRV = {"R": (1, 0), "L": (-1, 0), "U": (0, -1), "D": (0, 1)}
OPP = {"R": "L", "L": "R", "U": "D", "D": "U"}


def port_point(x, y, d, r=R):
    dx, dy = DIRV[d]
    return (x + dx * r, y + dy * r)


# ---------------------------------------------------------------- 幾何
def _seg_hits_circle(a, b, c, rad):
    """軸平行の線分 a-b が中心 c・半径 rad の円に触れるか。"""
    (x1, y1), (x2, y2) = a, b
    cx, cy = c
    if y1 == y2:
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        px = min(max(cx, lo), hi)
        return (px - cx) ** 2 + (y1 - cy) ** 2 < rad * rad
    lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
    py = min(max(cy, lo), hi)
    return (x1 - cx) ** 2 + (py - cy) ** 2 < rad * rad


def _seg_hits_rect(a, b, rect):
    (x1, y1), (x2, y2) = a, b
    l, t, rr, bt = rect
    if y1 == y2:
        lo, hi = min(x1, x2), max(x1, x2)
        return t < y1 < bt and lo < rr and hi > l
    lo, hi = min(y1, y2), max(y1, y2)
    return l < x1 < rr and lo < bt and hi > t


def _overlap(a, b, c, d, tol):
    """2本の軸平行線分が平行に tol 未満で並ぶ長さ(重なり)。"""
    if a[1] == b[1] and c[1] == d[1]:            # 両方水平
        if abs(a[1] - c[1]) >= tol:
            return 0.0
        lo = max(min(a[0], b[0]), min(c[0], d[0]))
        hi = min(max(a[0], b[0]), max(c[0], d[0]))
        return max(0.0, hi - lo)
    if a[0] == b[0] and c[0] == d[0]:            # 両方垂直
        if abs(a[0] - c[0]) >= tol:
            return 0.0
        lo = max(min(a[1], b[1]), min(c[1], d[1]))
        hi = min(max(a[1], b[1]), max(c[1], d[1]))
        return max(0.0, hi - lo)
    return 0.0


def _crosses(a, b, c, d):
    """水平と垂直の線分が(端点以外で)交差するか。"""
    if a[1] == b[1] and c[0] == d[0]:
        h1, h2, v1, v2 = a, b, c, d
    elif a[0] == b[0] and c[1] == d[1]:
        h1, h2, v1, v2 = c, d, a, b
    else:
        return False
    x, y = v1[0], h1[1]
    return (min(h1[0], h2[0]) < x < max(h1[0], h2[0])
            and min(v1[1], v2[1]) < y < max(v1[1], v2[1]))


def path_length(pts):
    return sum(abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def split_half(pts):
    """折れ線を長さの中点で2つに分ける((前半, 後半)。中点を両方に含む)。"""
    total = path_length(pts)
    half = total / 2.0
    acc = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        ln = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if acc + ln >= half and ln > 0:
            t = (half - acc) / ln
            if t >= 1.0 - 1e-9:            # 中点がちょうど角: 同じ点を重ねない
                return pts[:i + 2], pts[i + 1:]
            m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            return pts[:i + 1] + [m], [m] + pts[i + 1:]
        acc += ln
    return pts, [pts[-1]]


# ---------------------------------------------------------------- 候補
def _axis_cands(centers, r):
    """1軸ぶんの候補座標: 円の外側の通り道と、円と円の間の中央。"""
    vals = set()
    edge = r + CLEAR
    for c in centers:
        vals.add(c)
        for k in range(3):
            vals.add(c - edge - k * LANE)
            vals.add(c + edge + k * LANE)
    cs = sorted(set(centers))
    for c1, c2 in zip(cs, cs[1:]):
        if c2 - c1 > 2 * edge:
            m = (c1 + c2) / 2.0
            for k in (-2, -1, 0, 1, 2):
                vals.add(m + k * LANE)
    return sorted(vals)


class _Router:
    def __init__(self, positions, r=R, labels=None):
        self.pos = dict(positions)
        self.r = r
        self.labels = dict(labels or {})          # id -> 添え字の段数
        xs = [p[0] for p in self.pos.values()]
        ys = [p[1] for p in self.pos.values()]
        self.xc = _axis_cands(xs, r)
        self.yc = _axis_cands(ys, r)
        self.rects = {}
        self.dport = {}           # 下の口の y ずらし(添え字の下から出入りする)
        extra = set()
        for n, k in self.labels.items():
            if k > 0 and n in self.pos:
                x, y = self.pos[n]
                bottom = y + r + LABEL_PAD + LABEL_H * k
                self.rects[n] = (x - LABEL_HALF_W, y + r, x + LABEL_HALF_W, bottom)
                self.dport[n] = bottom - y
                extra.update(bottom + CLEAR + k2 * LANE for k2 in range(3))
        self.yc = sorted(set(self.yc) | extra)
        self.routed = []          # [(edge_key, (src, tgt), [segments], bbox)]
        self._segcache = {}
        # 当たり判定用: 円を y 順・x 順に並べておき、線分の近くだけ二分探索で引く
        self._by_y = sorted((p[1], n) for n, p in self.pos.items())
        self._by_x = sorted((p[0], n) for n, p in self.pos.items())
        self._ys = [v for v, _n in self._by_y]
        self._xs = [v for v, _n in self._by_x]

    def _near(self, a, b):
        """線分 a-b の近くにある円の id(下の添え字の枠も含めて当たりうるもの)。"""
        rc = self.r + CLEAR
        if a[1] == b[1]:                     # 水平: 円の y が近いものだけ
            y = a[1]
            lo = bisect.bisect_left(self._ys, y - rc - 6 - LABEL_H * 4)
            hi = bisect.bisect_right(self._ys, y + rc)
            x0, x1 = min(a[0], b[0]) - rc, max(a[0], b[0]) + rc
            pos = self.pos
            return [n for _v, n in self._by_y[lo:hi] if x0 < pos[n][0] < x1]
        x = a[0]                             # 垂直: 円の x が近いものだけ
        lo = bisect.bisect_left(self._xs, x - rc)
        hi = bisect.bisect_right(self._xs, x + rc)
        y0 = min(a[1], b[1]) - rc - 6 - LABEL_H * 4
        y1 = max(a[1], b[1]) + rc
        pos = self.pos
        return [n for _v, n in self._by_x[lo:hi] if y0 < pos[n][1] < y1]

    def _seg_info(self, a, b):
        """線分ごとの当たり判定(キャッシュ): (余白込みで触れる円, 円そのものに
        触れる円, 添え字の枠に触れる円)。"""
        key = (a, b)
        v = self._segcache.get(key)
        if v is not None:
            return v
        r = self.r
        clear, tight, rect = set(), set(), set()
        for n in self._near(a, b):
            c = self.pos[n]
            if _seg_hits_circle(a, b, c, r + CLEAR):
                clear.add(n)
                if _seg_hits_circle(a, b, c, r + 2):
                    tight.add(n)
            rc = self.rects.get(n)
            if rc is not None and _seg_hits_rect(a, b, rc):
                rect.add(n)
        v = (frozenset(clear), frozenset(tight), frozenset(rect))
        self._segcache[key] = v
        self._segcache[(b, a)] = v
        return v

    def port(self, n, d):
        x, y = self.pos[n]
        if d == "D" and n in self.dport:
            return (x, y + self.dport[n])
        return port_point(x, y, d, self.r)

    # ---- 経路の評価
    def _score(self, pts, src, tgt, bends, best):
        """評価値(タプル)。best より悪いと分かった時点で None を返す。"""
        r = self.r
        segs = list(zip(pts, pts[1:]))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        m = r + CLEAR + 1
        bx0, bx1 = min(xs) - m, max(xs) + m
        by0, by1 = min(ys) - m, max(ys) + m
        limit = best[0] if best is not None else None
        bad = set()
        last = len(segs) - 1
        for i, (a, b) in enumerate(segs):
            clear, tight, rect = self._seg_info(a, b)
            for n in clear:
                if n == src:
                    # 自分の円: 最初の線分以外が触れたら跨いだことになる
                    if i > 0 and n in tight:
                        bad.add(n)
                elif n == tgt:
                    if i < last and n in tight:
                        bad.add(n)
                else:
                    bad.add(n)
            for n in rect:
                if (n == src or n == tgt) and (i == 0 or i == last):
                    continue
                bad.add(("label", n))
        hits = len(bad)
        if limit is not None and hits > limit[0]:
            return None
        hard = 0.0
        soft = 0.0
        cross = 0
        tol = LANE * 0.75
        for _k, (s2, t2), osegs, (ox0, oy0, ox1, oy1) in self.routed:
            if ox1 < bx0 or ox0 > bx1 or oy1 < by0 or oy0 > by1:
                continue
            # 同じ円から出る線どうし/同じ円へ入る線どうし(束ね)だけは軽い扱い。
            # 行きと帰りが重なると片方が見えなくなるので重い扱い
            shares = (s2 == src) or (t2 == tgt)
            for a, b in segs:
                for c, d in osegs:
                    ov = _overlap(a, b, c, d, tol)
                    if ov > 0.5:
                        if shares:
                            soft += ov
                        else:
                            hard += ov
                    elif _crosses(a, b, c, d):
                        cross += 1
            if limit is not None and hits == limit[0] and \
                    bends * W_BEND + hard * W_OVER > limit[1]:
                return None
        cost = (bends * W_BEND + hard * W_OVER + cross * W_CROSS
                + soft * W_SOFT + path_length(pts) * W_LEN)
        return (hits, round(cost, 3), round(hard))

    # ---- 経路の組み立て
    def _valid(self, pts, ds, dt):
        """向きと最短長の制約(出口=ds 方向、入口=dt の逆方向)。"""
        if len(pts) < 2:
            return False
        a, b = pts[0], pts[1]
        dx, dy = DIRV[ds]
        if (b[0] - a[0]) * dx + (b[1] - a[1]) * dy < STUB - 1e-6:
            return False
        if dx == 0 and b[0] != a[0] or dy == 0 and b[1] != a[1]:
            return False
        a, b = pts[-2], pts[-1]
        ix, iy = DIRV[OPP[dt]]
        if (b[0] - a[0]) * ix + (b[1] - a[1]) * iy < STUB_IN - 1e-6:
            return False
        if ix == 0 and b[0] != a[0] or iy == 0 and b[1] != a[1]:
            return False
        for i in range(1, len(pts) - 2):
            p, q = pts[i], pts[i + 1]
            if abs(q[0] - p[0]) + abs(q[1] - p[1]) < 4:
                return False          # 細かすぎる段差
        return True

    def _nearest(self, vals, target, k, lo=None, hi=None):
        if lo is not None:
            vals = [v for v in vals if lo <= v <= hi]
        vals = sorted(vals, key=lambda v: (abs(v - target), v))
        return vals[:k]

    def _candidates(self, p0, ds, p1, dt, bends):
        """曲がる回数 bends の経路候補(点列)を列挙する。"""
        h0 = ds in ("L", "R")
        h1 = dt in ("L", "R")
        n = bends + 1                       # 線分の数
        # 線分 i の向き: 偶数番は出口と同じ軸
        last_h = h0 if (n - 1) % 2 == 0 else not h0
        if last_h != h1:
            return
        if bends == 0:
            if (h0 and p0[1] == p1[1]) or (not h0 and p0[0] == p1[0]):
                yield [p0, p1]
            return
        if bends == 1:
            c = (p1[0], p0[1]) if h0 else (p0[0], p1[1])
            yield [p0, c, p1]
            return
        # 自由座標: 角 1..bends-1 で決まる座標(x/y 交互)。最後の角は p1 に揃う
        k = _K[min(bends, len(_K) - 1)]
        span_x = (min(p0[0], p1[0]) - 160, max(p0[0], p1[0]) + 160)
        span_y = (min(p0[1], p1[1]) - 160, max(p0[1], p1[1]) + 160)
        mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
        ex = (p0[0] + DIRV[ds][0] * STUB, p1[0] + DIRV[dt][0] * STUB_IN)
        ey = (p0[1] + DIRV[ds][1] * STUB, p1[1] + DIRV[dt][1] * STUB_IN)
        # 中点に近い候補 k 個+「口から出てすぐ」の位置(U字で外側を回る用)
        xs = self._nearest(self.xc + [mx], mx, k, *span_x)
        ys = self._nearest(self.yc + [my], my, k, *span_y)
        for e, d in ((ex[0], DIRV[ds][0]), (ex[1], DIRV[dt][0])):
            if d:
                xs += [e + d * j * LANE for j in range(3)]
        for e, d in ((ey[0], DIRV[ds][1]), (ey[1], DIRV[dt][1])):
            if d:
                ys += [e + d * j * LANE for j in range(3)]
        xs = sorted({v for v in xs if v >= EDGE_MIN})
        ys = sorted({v for v in ys if v >= EDGE_MIN})
        nfree = bends - 1
        # 自由座標の軸: 角1 は出口が水平なら x、以後交互
        axes = []
        hx = h0
        for _i in range(nfree):
            axes.append("x" if hx else "y")
            hx = not hx

        def rec(i, acc):
            if i == nfree:
                yield acc
                return
            for v in (xs if axes[i] == "x" else ys):
                yield from rec(i + 1, acc + [v])

        for free in rec(0, []):
            pts = [p0]
            cur = p0
            horiz = h0
            for v in free:
                cur = (v, cur[1]) if horiz else (cur[0], v)
                pts.append(cur)
                horiz = not horiz
            # 最後の角: 今の線分の軸で p1 に揃える
            cur = (p1[0], cur[1]) if horiz else (cur[0], p1[1])
            pts.append(cur)
            pts.append(p1)
            yield pts

    def route(self, src, tgt, key):
        (x1, y1), (x2, y2) = self.pos[src], self.pos[tgt]
        best = None
        for bends in range(MAX_BENDS + 1):
            # この段の候補を「下限(曲がり+長さ)」の小さい順に評価し、下限が
            # 今の最良を超えたら打ち切る(重なり・交差は加点しかしないため)
            cands = []
            seen = set()
            for ds in ("R", "L", "U", "D"):
                p0 = self.port(src, ds)
                for dt in ("L", "R", "U", "D"):
                    p1 = self.port(tgt, dt)
                    for pts in self._candidates(p0, ds, p1, dt, bends):
                        if not self._valid(pts, ds, dt):
                            continue
                        pts = _simplify(pts)
                        if len(pts) - 2 != bends:
                            continue
                        t = tuple(pts)
                        if t in seen:
                            continue
                        seen.add(t)
                        lb = bends * W_BEND + path_length(pts) * W_LEN
                        cands.append((lb, t))
            cands.sort()
            for lb, t in cands:
                if best is not None and best[0][0] == 0 and lb >= best[0][1]:
                    break
                sc = self._score(list(t), src, tgt, bends, best)
                if sc is not None and (best is None or sc[:2] < best[0][:2]):
                    best = (sc, list(t))
            if best is not None and best[0][0] == 0 and (
                    best[0][2] == 0 or best[0][1] <= (bends + 1) * W_BEND):
                # 跨がず・関係のない線とも重ならない経路があれば、それより
                # 曲がる経路は探さない(曲がる回数を優先=ユーザー要望)
                break
        if best is None:                  # 念のため: 中心どうしの L 字
            pts = [(x1, y1), (x2, y1), (x2, y2)]
            best = ((99, 0.0, 0), pts)
        pts = best[1]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.routed.append((key, (src, tgt), list(zip(pts, pts[1:])),
                            (min(xs), min(ys), max(xs), max(ys))))
        return pts


def _simplify(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if p == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == p[0]) or (a[1] == b[1] == p[1]):
                out[-1] = p
                continue
        out.append(p)
    return out


def self_loop_points(x, y, r=R):
    """自己ループ: 右の口から出て上を回り、上の口へ入る四角い輪。"""
    return [(x + r, y), (x + r + 14, y), (x + r + 14, y - r - 16),
            (x, y - r - 16), (x, y - r)]


_CACHE: dict = {}


def route_edges(positions, pairs, *, r=R, labels=None):
    """pairs [(src, tgt), ...] の経路 {(src, tgt): [点列]} を返す。

    自己ループは self_loop_points。結果は入力ごとにキャッシュする
    (再生タブは遷移のたびに描き直すため)。
    """
    key = (tuple(sorted((k, tuple(v)) for k, v in positions.items())),
           tuple(pairs), r,
           tuple(sorted((labels or {}).items())))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    rt = _Router(positions, r, labels)
    out = {}
    order = sorted(
        (p for p in pairs if p[0] != p[1]),
        key=lambda p: (abs(positions[p[0]][0] - positions[p[1]][0])
                       + abs(positions[p[0]][1] - positions[p[1]][1]),
                       str(p)))
    for p in pairs:
        if p[0] == p[1]:
            pts = self_loop_points(*positions[p[0]], r)
            out[p] = pts
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            rt.routed.append((p, p, list(zip(pts, pts[1:])),
                              (min(xs), min(ys), max(xs), max(ys))))
    for p in order:
        out[p] = rt.route(p[0], p[1], p)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = out
    return out
