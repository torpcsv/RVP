"""スクリプト編集: 配置/移動/伸縮の計画(ゴースト・当たり判定・ハンドル・近傍追従)(mixin)。"""
from __future__ import annotations


from .common import snap
from .patterns import (K_MAX, K_MIN, K_STRETCH_MAX, S_MAX, S_MIN, _plan,
    plan_connect_both, plan_connect_side, plan_normal, shape_ds, shape_len)


class _ScriptEditGraphPlanMixin:
    """ScriptEditGraph の mixin(=301 分割)。配置/移動/伸縮の計画(ゴースト・当たり判定・ハンドル・近傍追従)"""

    def _point_ghost_at(self, x: int, y: int):
        """点モードのゴースト位置 (at, pos)。打点できない位置は None。"""
        x0, top, x1, bot = self._plot()
        if not (x0 <= x <= x1 and top <= y <= bot):
            return None
        if self.hit_point(x, y) is not None:
            return None                     # 既存の点の上=選択操作
        if self._pattern_hit(x, y) is not None:
            return None                     # パターンの上=選択・移動操作
        if self.sel_pattern is not None and \
                self._handle_hit(x, y) is not None:
            return None
        at = snap(max(0.0, self.ms_of(x)), self.grid_at)
        pos = max(0, min(self.pos_max, snap(self.pos_of_y(y), self.grid_pos)))
        if self.model.at_blocked(at) or at in self.model.pattern_ats():
            return None         # パターンの時間範囲・端点の禁止帯=打点不可
        if self.model.pos_of(at) == pos:
            return None                     # 既存点と完全に同じ位置
        return at, pos

    def _plan_at(self, ms: float, pos: float | None = None):
        """クリック/カーソル位置の配置 plan(仕様 5.5「発動のしかた」)。

        ①両側接続 → ②近いほうの片側接続 → ③通常配置 の順に試し、
        衝突判定まで通った最初の plan を返す(すべて不成立なら None)。
        **=226: 離散的なスクリプト(pat_connect=False)では接続配置を試さず、
        常に③(=定義どおりの高さ)で置く。**
        =177: 通常配置はクリック位置の**高さ**へ縦にずらして置ける
        (基準線=パターンの端の高さがクリックした pos に来る。0〜100 に
        収まるようクランプ。0-50 のパターンなら最高で 50-100 まで)。
        """
        shape = self.place_shape
        if shape is None:
            return None
        # =194: 縮尺配置。時間軸へ縮尺を掛けた形を「元の形」として扱う
        # (接続の片側接続にも効く。両側接続は両端で長さが決まるため実質
        # 対象外=幾何は変わらない)
        if abs(self.place_scale - 1.0) > 1e-9:
            shape = tuple((t * self.place_scale, p) for t, p in shape)
        # =180: 吸着は半グリッド以内だけ(隣のグリッドを狙ったクリックまで
        # 吸着してしまわないように)
        tol = max(1.0, self.grid_at * 0.5)
        eps = self.model.pattern_endpoints() if self.pat_connect else []
        near = None
        for ep in eps:
            d = abs(ep[1] - ms)
            if d <= tol and (near is None or d < abs(near[1] - ms)):
                near = ep
        cands = []
        if near is not None:
            _i, a, q, _side = near
            if ms >= a:
                # 左端を near へ接続して右側へ。両側候補=右にある最寄り端点
                rights = [e for e in eps if e[1] > a]
                if rights:
                    er = min(rights, key=lambda e: e[1] - a)
                    cands.append(plan_connect_both(
                        shape, a, q, er[1], er[2],
                        self.grid_at, self.grid_pos,
                        round_interior=self.round_interior,
                        pos_max=self.pos_max))
                cands.append(plan_connect_side(
                    shape, "left", a, q, 1.0, self.grid_at, self.grid_pos,
                    round_interior=self.round_interior,
                    pos_max=self.pos_max))
            else:
                lefts = [e for e in eps if e[1] < a]
                if lefts:
                    el = min(lefts, key=lambda e: a - e[1])
                    cands.append(plan_connect_both(
                        shape, el[1], el[2], a, q,
                        self.grid_at, self.grid_pos,
                        round_interior=self.round_interior,
                        pos_max=self.pos_max))
                cands.append(plan_connect_side(
                    shape, "right", a, q, 1.0, self.grid_at, self.grid_pos,
                    round_interior=self.round_interior,
                    pos_max=self.pos_max))
        # ③通常配置(接続不成立時のフォールバック=仕様 4-6)。
        # =177: クリックの高さを基準線(端の高さ)にする(グリッド吸着+
        # パターン全体が 0〜100 に収まる範囲へクランプ)
        p0 = shape[0][1]
        plast = shape[-1][1]
        pmin = min(p for _t, p in shape)
        pmax = max(p for _t, p in shape)
        if self.pat_center is not None:
            # =226: 離散的なスクリプトは **定義どおりの高さで固定**して置く
            # (クリックした pos では縦にずらせない。要望4/8)
            off = 0
        else:
            target = p0 if pos is None else \
                max(0, min(self.pos_max, snap(pos, self.grid_pos)))
            off = max(-pmin, min(self.pos_max - pmax, target - p0))
        cands.append(plan_normal(shape, snap(max(0.0, ms), self.grid_at),
                                 self.grid_at, self.grid_pos,
                                 b0=p0 + off, b1=plast + off,
                                 round_interior=self.round_interior,
                                 pos_max=self.pos_max))
        for plan in cands:
            if plan is None:
                continue
            ok, _shared = self.model.check_pattern_points(plan["points"])
            if ok:
                return plan
        # =221: どの候補も衝突で通らないときは **③通常配置を上書き方式で置く**
        # (ユーザー決定=「後から置くパターンを優先」。Ctrl+V と同じ考え方)。
        # 接続(端点の共有)が成立するときは従来どおりそちらが優先されるので、
        # 上書きになるのは「本当に重なっている」ときだけ。
        last = cands[-1]
        if last is not None:
            plan = dict(last)
            plan["overwrite"] = True
            return plan
        return None

    def _pattern_vals(self, rec) -> list:
        """パターンの各点の pos(canonical=丸める前の値)。"""
        shape = rec["shape"]
        length = shape_len(shape)
        if length <= 0:
            return [float(rec["b0"])]
        b0, b1 = rec["b0"], rec["b1"]
        k = rec["k"]
        return [b0 + (b1 - b0) * (t / length) + k * d
                for (t, _p), d in zip(shape, shape_ds(shape))]

    def _pattern_band(self, idx: int):
        """パターンの外形 (lo, hi, 上辺(左,右), 下辺(左,右))。

        - linear/twist: =227 までは**平行四辺形**(基準線 b0→b1 に沿う。
          仕様 5.3)だったが、=280 で矩形へ統一(下記)。
        - **=228: 離散的なスクリプト(`pat_center` あり)は矩形**(要望1)。
          両端の pos が違うパターンでも枠が斜めにならないよう、
          上辺・下辺とも一定(全点の最大 / 最小)にする。階段で描く
          スクリプトでは基準線に意味が無く、拡縮も中心対称(=226)なので、
          **見た目・当たり判定・ハンドルとも矩形が実際の挙動と一致する**。
        """
        rec = self.model.patterns[idx]
        if not rec["ats"]:
            return None
        lo, hi = rec["ats"][0], rec["ats"][-1]
        # =280: linear/twist も**矩形**(全点の最大/最小)にする(実機FB3。
        # 従来は基準線 b0→b1 に沿う平行四辺形=両端の pos が違うと枠が
        # 斜めに見えた)。縦の拡縮は _resize_plan の「掴んだ辺の反対側を
        # 軸に全点を f 倍」で、枠の見た目・当たり判定・効き方が一致する。
        vals = self._pattern_vals(rec)
        vmax, vmin = max(vals), min(vals)
        return lo, hi, (vmax, vmax), (vmin, vmin)

    def _pattern_hit(self, x: int, y: int) -> int | None:
        """外形(平行四辺形 / =228 離散は矩形)の内側判定。

        あとに置いたものを優先する。"""
        ms = self.ms_of(x)
        pos = self.pos_of_y(y)
        margin = self.HIT_PX * float(self.pos_max) / max(
            1, (self.y_of(0) - self.y_of(self.pos_max)))
        for idx in range(len(self.model.patterns) - 1, -1, -1):
            band = self._pattern_band(idx)
            if band is None:
                continue
            lo, hi, top, bot = band
            if not (lo <= ms <= hi) or hi <= lo:
                continue
            u = (ms - lo) / (hi - lo)
            t = top[0] + (top[1] - top[0]) * u
            b = bot[0] + (bot[1] - bot[0]) * u
            if b - margin <= pos <= t + margin:
                return idx
        return None

    def _handles(self, idx: int):
        """拡縮ハンドル {code: (x, y)}(四隅+辺の中点=仕様 5.4)。"""
        band = self._pattern_band(idx)
        if band is None:
            return {}
        lo, hi, top, bot = band
        xl, xr = self.x_of(lo), self.x_of(hi)
        ytl, ytr = self.y_of(top[0]), self.y_of(top[1])
        ybl, ybr = self.y_of(bot[0]), self.y_of(bot[1])
        return {
            "nw": (xl, ytl), "ne": (xr, ytr),
            "sw": (xl, ybl), "se": (xr, ybr),
            "n": ((xl + xr) / 2, (ytl + ytr) / 2),
            "s": ((xl + xr) / 2, (ybl + ybr) / 2),
            "w": (xl, (ytl + ybl) / 2), "e": (xr, (ytr + ybr) / 2),
        }

    def _handle_hit(self, x: int, y: int) -> str | None:
        if self.sel_pattern is None or \
                self.sel_pattern >= len(self.model.patterns):
            return None
        for code, (hx, hy) in self._handles(self.sel_pattern).items():
            if abs(x - hx) <= self.HIT_PX and abs(y - hy) <= self.HIT_PX:
                return code
        return None

    def _patmove_plan(self, event):
        """移動 D&D の plan(s 不変=仕様 5.4)。片側/両側接続も試す。

        =226 では離散的なスクリプト(pat_center あり)は時間方向だけだったが、
        **=320: 離散も縦に平行移動できる**(ユーザー決定)。縦の Δ は掴んだ
        位置に最も近いパターンの点がグリッド線に乗る量(端で止める)。
        """
        idx = self.sel_pattern
        if idx is None or idx >= len(self.model.patterns):
            return None
        d = self._drag
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        dat = snap((event.x - d["x"]) * (span / (x1 - x0)), self.grid_at)
        rec = self.model.patterns[idx]
        raw = self.pos_of_y(event.y) - self.pos_of_y(d["y"])
        if self.pat_center is not None:
            vals = [self.model.pos_of(a) for a in rec["ats"]]
            vals = [v for v in vals if v is not None]
            if vals:
                anchor = min(
                    ((a, self.model.pos_of(a)) for a in rec["ats"]
                     if self.model.pos_of(a) is not None),
                    key=lambda ap: (self.x_of(ap[0]) - d["x"]) ** 2
                    + (self.y_of(ap[1]) - d["y"]) ** 2)[1]
                lo, hi = min(vals), max(vals)
                raw = max(-lo, min(self.pos_max - hi, raw))
                g = self.grid_pos if self.grid_pos > 1 else 1
                dpos = snap(anchor + raw, g) - anchor
                while dpos > 0 and hi + dpos > self.pos_max:
                    dpos -= g
                while dpos < 0 and lo + dpos < 0:
                    dpos += g
            else:
                dpos = 0
        else:
            dpos = snap(raw, self.grid_pos)
        shape = rec["shape"]
        lo, hi = rec["ats"][0] + dat, rec["ats"][-1] + dat
        # =180: 端点そのものへの吸着は半グリッド以内だけ
        # 隣接パターン(端を共有)は「追従」で扱うので、新規接続の吸着相手
        # からは外す(=175。外さないと磁石が追従と綱引きになる)
        left_nb, right_nb = self.model.pattern_neighbors(idx)
        nb_set = {j for j in (left_nb, right_nb) if j is not None}
        eps = [e for e in self.model.pattern_endpoints(exclude=idx)
               if e[0] not in nb_set] if self.pat_connect else []
        tol = max(1.0, self.grid_at * 0.5)

        def _pat_range(j):
            ats = self.model.patterns[j]["ats"]
            return ats[0], ats[-1]

        # 接続スナップの範囲(=174 実機FB2で拡大): 従来の
        # 「端点から±(atグリッド1つ分)」に加え、**相手パターンの半分へ
        # 落とした場合も吸着**する。左端の接続=相手の右半分に lo が
        # 入ったら相手の右端へ / 右端の接続=相手の左半分に hi が入ったら
        # 相手の左端へ。重なる置き方はどうせ衝突で置けないので、吸着だけが
        # 成立する(自由移動を妨げない)。
        def _find_anchor(end_ms, want_side):
            best = None
            for ep in eps:
                j, a, _q, side = ep
                d = abs(a - end_ms)
                cand = d <= tol
                if not cand and side == want_side:
                    jlo, jhi = _pat_range(j)
                    mid = (jlo + jhi) / 2.0
                    if want_side == "R":
                        cand = mid <= end_ms <= jhi + tol
                    else:
                        cand = jlo - tol <= end_ms <= mid
                if cand and (best is None or d < abs(best[1] - end_ms)):
                    best = ep
            return best

        nl = _find_anchor(lo, "R")     # 左端は相手の右端へ繋ぐ
        nr = _find_anchor(hi, "L")     # 右端は相手の左端へ繋ぐ
        cands = []
        s = rec["s"]
        length = shape_len(shape)
        kcap = max(K_MAX, rec["k"])     # 広げた振幅を接続で戻さない(=178)
        if nl is not None and nr is not None and \
                abs((nr[1] - nl[1]) - s * length) <= tol:
            # 隙間が s·L とぴったり合い両端が同時にスナップ → 両側接続
            cands.append(plan_connect_both(shape, nl[1], nl[2],
                                           nr[1], nr[2],
                                           self.grid_at, self.grid_pos,
                                           k_cap=kcap,
                                           pos_max=self.pos_max))
        if nl is not None:
            cands.append(plan_connect_side(shape, "left", nl[1], nl[2], s,
                                           self.grid_at, self.grid_pos,
                                           k_cap=kcap,
                                           pos_max=self.pos_max))
        if nr is not None:
            cands.append(plan_connect_side(shape, "right", nr[1], nr[2], s,
                                           self.grid_at, self.grid_pos,
                                           k_cap=kcap,
                                           pos_max=self.pos_max))
        cands.append(self.model.move_pattern(idx, dat, dpos))
        for plan in cands:
            if plan is None:
                continue
            # =182: 移動は方向で追従を切り替える(離れる側ははがれる)
            plans = self._with_neighbors(idx, plan, directional=True)
            if plans is not None:
                return plans
        return None

    def _with_neighbors(self, idx: int, plan: dict,
                        directional: bool = False):
        """plan に隣接パターンの追従(=175)を合成した {index: plan} を返す。

        追従できない(縮尺・振幅・丸めの制約を満たせない/衝突する)ときは
        **接続を外して単独で動かす**(従来の磁石挙動へフォールバック=
        禁止を増やさない)。それも衝突するなら None。
        directional=True(移動 D&D)では離れる側の隣は追従しない(=182)。
        **=226: 離散的なスクリプト(pat_follow=False)では追従しない**
        (なめらかに繋ぐ必要が無いため。ユーザー決定)。
        """
        if not self.pat_follow:
            plans = {idx: plan}
            return plans if self.model.check_plans(plans) else None
        nb = self.model.neighbor_follow_plans(idx, plan,
                                              self.grid_at, self.grid_pos,
                                              directional=directional)
        if nb is not None:
            plans = {idx: plan}
            plans.update(nb)
            if self.model.check_plans(plans):
                return plans
        plans = {idx: plan}
        if self.model.check_plans(plans):
            return plans
        return None

    def _resize_plan(self, event, handle: str):
        """拡縮 D&D の plan(仕様 5.4/5.5)。

        左右(w/e)=時間だけ / 上下(n/s)=振幅だけ / 四隅=両方。
        ドラッグした辺は端点へスナップして片側接続(反対側が別パターンと
        接していれば両側接続)。内側の点はグリッドへ吸着する。
        **=226: 離散的なスクリプト(pat_center あり)では、縦は中心
        (ROTATE=50 / VIBRATION=0)を軸にした対称の拡縮になる**(要望5/8)。
        """
        idx = self.sel_pattern
        if idx is None or idx >= len(self.model.patterns):
            return None
        rec = self.model.patterns[idx]
        shape = rec["shape"]
        length = shape_len(shape)
        A = rec["A"]
        s = rec["s"]
        k = rec["k"]
        b0, b1 = rec["b0"], rec["b1"]
        R = A + s * length
        ms = self.ms_of(event.x)
        pos = self.pos_of_y(event.y)
        tol = max(1.0, self.grid_at * 0.5)   # =180: 吸着は半グリッド
        left_nb, right_nb = self.model.pattern_neighbors(idx)
        nb_set = {j for j in (left_nb, right_nb) if j is not None}
        eps = [e for e in self.model.pattern_endpoints(exclude=idx)
               if e[0] not in nb_set] if self.pat_connect else []
        snap_ep = None

        if handle in ("e", "ne", "se", "w", "nw", "sw"):
            drag_right = handle in ("e", "ne", "se")
            target = snap(ms, self.grid_at)
            for ep in eps:
                if abs(ep[1] - ms) <= tol:
                    snap_ep = ep
                    target = ep[1]
                    break
            if drag_right:
                s = (target - A) / length
            else:
                s = (R - target) / length
                A = target
            if not (S_MIN - 1e-9 <= s <= S_MAX + 1e-9):
                return None
        if handle in ("n", "s", "nw", "ne", "sw", "se"):
            # 振幅の変更(=174 実機FB1): **反対側の辺を固定**し、ドラッグした
            # 辺がカーソルへ追従する。上辺のD&D=下辺固定で上下する/
            # 下辺のD&D=上辺固定で上下する(下から縮められる)。
            # k と一緒に基準線(b0/b1)も平行にずらして両立させる。
            ds = shape_ds(shape)
            dmax, dmin = max(ds), min(ds)
            height = dmax - dmin
            if self.pat_center is not None:
                # =226: 離散的なスクリプトは **中心(ROTATE=50 / VIB=0)を
                # 軸にした対称の拡縮**だけ(要望5/8)。
                # pos' = c + (pos - c)*f。canonical では b0/b1 の中心からの
                # ずれと k を同じ倍率で伸ばせばよい。
                c = float(self.pat_center)
                cur = max(0.0, min(float(self.pos_max), pos))
                # =228: 外形は**矩形**(_pattern_band)なので、つかんだ辺は
                # ドラッグ位置に依らず **全点の最大(上辺)/ 最小(下辺)**。
                # ここを掴んだ x の基準線から取ると、枠の見た目と拡縮の
                # 効き方がずれる(=227まではずれていた)。
                vals = [b0 + (b1 - b0) * (t / length) + k * dd
                        for (t, _p3), dd in zip(shape, shape_ds(shape))]
                ref = max(vals) if handle in ("n", "nw", "ne") else min(vals)
                if abs(ref - c) < 1e-6:
                    return None          # 中心に張り付いた辺では伸縮できない
                f = (cur - c) / (ref - c)
                if f <= 1e-6:
                    return None          # 反対側へ折り返す拡縮は不成立
                # 全点が 0〜100 に収まる最大倍率でクランプ
                for v in vals:
                    d = v - c
                    if d > 1e-9:
                        f = min(f, (self.pos_max - c) / d)
                    elif d < -1e-9:
                        f = min(f, (0.0 - c) / d)
                f = max(0.05, f)
                b0 = c + (b0 - c) * f
                b1 = c + (b1 - c) * f
                k = k * f
                if not (K_MIN - 1e-9 <= k <= K_STRETCH_MAX + 1e-9):
                    return None
            elif height > 1e-9:
                # =280: 枠は矩形(全点の max/min)。**掴んだ辺の反対側の辺を
                # 軸に全点を f 倍**する(=226 の中心対称を「辺基準」にした
                # もの)。掴んだ辺がカーソルへ正確に追従し、両端の pos も
                # 同じ倍率で動く。canonical は b0/b1 を軸からの距離で、
                # k を同じ倍率で伸ばせばよい(pos' = c + (pos - c)·f)。
                vals = [b0 + (b1 - b0) * (t / length) + k * dd
                        for (t, _p3), dd in zip(shape, shape_ds(shape))]
                vmax, vmin = max(vals), min(vals)
                if vmax - vmin < 1e-9:
                    return None          # 平らな形は縦に伸ばせない
                cur = max(0.0, min(float(self.pos_max), pos))
                if handle in ("n", "nw", "ne"):
                    c, ref = vmin, vmax          # 下辺固定・上辺=カーソル
                else:
                    c, ref = vmax, vmin          # 上辺固定・下辺=カーソル
                f = (cur - c) / (ref - c)
                if f <= 1e-6:
                    return None          # 反対側へ折り返す拡縮は不成立
                for v in vals:            # 全点が 0〜100 に収まる最大倍率
                    d = v - c
                    if d > 1e-9:
                        f = min(f, (self.pos_max - c) / d)
                    elif d < -1e-9:
                        f = min(f, (0.0 - c) / d)
                k2 = k * f
                if not (K_MIN - 1e-9 <= k2 <= K_STRETCH_MAX + 1e-9):
                    return None
                b0 = c + (b0 - c) * f
                b1 = c + (b1 - c) * f
                k = k2

        # ドラッグした辺がスナップしたら接続配置として解き直す
        # (=226: 離散的なスクリプトでは接続配置そのものを使わない)
        if snap_ep is not None and self.pat_connect:
            drag_right = handle in ("e", "ne", "se")
            # 反対側に隣接パターンがある(端を共有)なら、その接続点は
            # 動かさない=両側接続として解く(隣は追従不要のまま)
            opp_nb = left_nb if drag_right else right_nb
            kcap = max(K_MAX, rec["k"])     # 広げた振幅を接続で戻さない
            if opp_nb is not None:
                if drag_right:
                    a1, q1 = rec["ats"][0], self.model.pos_of(rec["ats"][0])
                    a2, q2 = snap_ep[1], snap_ep[2]
                else:
                    a1, q1 = snap_ep[1], snap_ep[2]
                    a2, q2 = rec["ats"][-1], \
                        self.model.pos_of(rec["ats"][-1])
                plan = plan_connect_both(shape, a1, q1, a2, q2,
                                         self.grid_at, self.grid_pos,
                                         k_cap=kcap, pos_max=self.pos_max)
            else:
                side = "right" if drag_right else "left"
                plan = plan_connect_side(shape, side, snap_ep[1],
                                         snap_ep[2], s,
                                         self.grid_at, self.grid_pos,
                                         k_cap=kcap, pos_max=self.pos_max)
            return self._with_neighbors(idx, plan) if plan else None
        # 変換後の全点が 0〜100 に収まらない拡縮は拒否する(_plan の
        # クランプで形が崩れたまま確定されるのを防ぐ)
        for (t, _p2), dd in zip(shape, shape_ds(shape)):
            v = b0 + (b1 - b0) * (t / length) + k * dd
            if v < -1e-6 or v > self.pos_max + 1e-6:
                return None
        plan = _plan(shape, A, s, k, b0, b1, self.grid_at, self.grid_pos,
                     fix_left=True, fix_right=True,
                     round_interior=self.round_interior,
                     pos_max=self.pos_max)
        return self._with_neighbors(idx, plan) if plan else None

    def _checked(self, plan, exclude: int):
        if plan is None:
            return None
        ok, _sh = self.model.check_pattern_points(plan["points"],
                                                  exclude=exclude)
        return plan if ok else None

    def _group_box_range(self):
        """剛体の掴み領域(=202)。複数選択(点+パターンで合計2つ以上)の
        とき、選択範囲の (at最小, at最大) を返す。それ以外は None。"""
        m = self.model
        pats = [i for i in m.pattern_selection
                if 0 <= i < len(m.patterns) and m.patterns[i]["ats"]]
        if len(m.selection) + len(pats) < 2:
            return None
        ats = list(m.selection)
        for i in pats:
            ats.append(m.patterns[i]["ats"][0])
            ats.append(m.patterns[i]["ats"][-1])
        if not ats:
            return None
        return min(ats), max(ats)

    def _group_box_hit(self, x: float, y: float) -> bool:
        """(x,y) が掴み領域(点線の長方形=atの範囲×pos全域)の中か。"""
        rng = self._group_box_range()
        if rng is None:
            return False
        x0, top, x1, bot = self._plot()
        if not (top <= y <= bot):
            return False
        gx0, gx1 = self.x_of(rng[0]), self.x_of(rng[1])
        return gx0 - 2 <= x <= gx1 + 2

    def _anchor(self) -> float:
        """view_ms を置く水平位置(=198/=201)。**初期表示だけ左寄り
        (VIEW_ANCHOR=0.12)**で、一度でも再生したら以後は一時停止中も
        含めて中央(0.5)。initial_view() でリセットされる。

        =298(要望5): **追従の有無でアンカーを変えない**。以前は右ドラッグで
        追従が止まった瞬間に 0.5→0.12 へ切り替わり、表示範囲が 0.38 幅ぶん
        右へ飛んで再生位置の線が左端へ瞬間移動していた。パンは view_ms の
        差分だけで動くので、アンカーを固定すれば画面は連続する。"""
        return 0.5 if (self.playing or self._played) else self.VIEW_ANCHOR
