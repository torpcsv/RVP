"""標準パターンと配置計画の純関数(振幅/縮尺の解決・transform・plan_*)。"""
from __future__ import annotations


from .common import snap


K_MIN, K_MAX = 0.1, 1.0        # 振幅の許容範囲(仕様 5.5=配置時の上限)


# =178: 配置後の拡縮(上下辺ドラッグ)では k を 2.0 まで広げられる。
# =177で標準パターンの振幅が半分(pos 0〜50)になったので、k=2.0 で
# フルスケール(0〜100)まで戻せる。配置時の解き直し(接続)は従来どおり
# K_MAX=1.0 が上限(勝手に定義より大きく配置されない)。
K_STRETCH_MAX = 2.0


S_MIN, S_MAX = 0.2, 5.0        # 時間の縮尺の許容範囲(20%〜500%)


# 標準パターン20種(仕様 5.1)。基準長 1000ms。
# =217(ユーザー提供): ユーザーが作成した funscript「標準パターン0120」を
# **1秒ごとに切り出して** 20個の標準パターンとして採用した(0〜1秒=STD1、
# 1〜2秒=STD2 … 19〜20秒=STD20)。旧19種(「山」「ギザギザ(粗)」など)は
# すべて置き換え・名前も廃止(=217②)。
# 名前は **STD1〜STD20**(ユーザー決定。ユーザーパターンの U1〜U20 と
# 並べたときに「標準」であることが分かる形)。**i18n の対象ではない**
# (日英共通の識別子なので tr() を通しても素通りする)。
# 振幅は STD1〜STD19 が pos 0〜50・始点/終点とも pos=0。**STD20 だけ
# 終点が pos=50**(意図的=右上がりに終わる形。ユーザー確認済み)。
# 配置時はクリック位置(Fキーなら 50)の高さへ縦にずらす(=177 の規則)。
STD_PATTERNS: tuple = (
    ("STD1", ((0, 0), (500, 50), (1000, 0))),
    ("STD2", ((0, 0), (250, 50), (1000, 0))),
    ("STD3", ((0, 0), (750, 50), (1000, 0))),
    ("STD4", ((0, 0), (100, 20), (400, 50), (1000, 0))),
    ("STD5", ((0, 0), (600, 50), (900, 20), (1000, 0))),
    ("STD6", ((0, 0), (250, 50), (350, 30), (550, 10), (1000, 0))),
    ("STD7", ((0, 0), (200, 40), (500, 50), (700, 10), (1000, 0))),
    ("STD8", ((0, 0), (250, 50), (500, 50), (1000, 0))),
    ("STD9", ((0, 0), (500, 50), (750, 50), (1000, 0))),
    ("STD10", ((0, 0), (250, 50), (500, 50), (750, 0), (1000, 0))),
    ("STD11", ((0, 0), (200, 40), (450, 10), (650, 50), (1000, 0))),
    ("STD12", ((0, 0), (150, 30), (250, 10), (400, 40), (500, 20), (650, 50),
               (1000, 0))),
    ("STD13", ((0, 0), (100, 20), (150, 10), (350, 50), (1000, 0))),
    ("STD14", ((0, 0), (400, 50), (450, 40), (500, 50), (550, 40), (600, 50),
               (1000, 0))),
    ("STD15", ((0, 0), (50, 10), (100, 0), (150, 10), (200, 0), (250, 10),
               (300, 0), (650, 50), (1000, 0))),
    ("STD16", ((0, 0), (100, 20), (200, 32), (300, 42), (400, 47), (500, 50),
               (600, 47), (700, 42), (800, 32), (900, 20), (1000, 0))),
    ("STD17", ((0, 0), (100, 20), (200, 32), (300, 42), (400, 47), (500, 50),
               (600, 30), (700, 18), (800, 8), (900, 3), (1000, 0))),
    ("STD18", ((0, 0), (100, 3), (200, 8), (300, 18), (400, 30), (500, 50),
               (600, 30), (700, 18), (800, 8), (900, 3), (1000, 0))),
    ("STD19", ((0, 0), (50, 10), (100, 0), (150, 10), (200, 0), (250, 10),
               (300, 0), (350, 10), (400, 0), (450, 10), (500, 0), (550, 10),
               (600, 0), (650, 10), (700, 0), (750, 10), (800, 0), (850, 10),
               (900, 0), (950, 10), (1000, 0))),
    ("STD20", ((0, 0), (53, 12), (105, 4), (158, 16), (211, 8), (263, 21),
               (316, 13), (368, 25), (421, 17), (474, 29), (526, 21),
               (579, 33), (632, 25), (684, 37), (737, 29), (789, 42),
               (842, 34), (895, 46), (947, 38), (1000, 50))),
)


# =226/=315: **離散的なスクリプト用の標準パターン**。
# linear/twist(=217 の STD_PATTERNS・1秒)とは考え方が違うので**別カタログ**。
#
# - ROTATE 用(csv・rotate系funscript 共通): pos 50=停止 / 上=正回転 /
#   下=逆回転。**配置時は定義どおりの pos で固定**(=226 要望4)。
#   =315: ユーザー提供「ufo新規基本パターン.csv」(実用重視・シンプル)から
#   切り出し。STD1〜5 は 0.8 秒(速度 35/45/55/65/75 → 停止)、STD6〜10 は
#   2 秒、STD11〜20 は STD1〜10 の反転(逆回転)。**終端は必ず停止(50)**。
#   速度が奇数(35 など)の pos は 67.5 のように**小数のまま定義**し、
#   置くときに分解能へ丸める(csv=200 なら 135 で正確、rotate funscript=100
#   では切り上げて 68=速度 36。scale_shape)。
STD_PATTERNS_ROTATE: tuple = (
    ("STD1", ((0, 67.5), (800, 50))),
    ("STD2", ((0, 72.5), (800, 50))),
    ("STD3", ((0, 77.5), (800, 50))),
    ("STD4", ((0, 82.5), (800, 50))),
    ("STD5", ((0, 87.5), (800, 50))),
    ("STD6", ((0, 85), (1200, 75), (2000, 50))),
    ("STD7", ((0, 85), (400, 82.5), (800, 80), (1200, 77.5), (1600, 75),
              (2000, 50))),
    ("STD8", ((0, 75), (300, 80), (700, 85), (1300, 80), (1700, 75),
              (2000, 50))),
    ("STD9", ((0, 85), (1200, 25), (2000, 50))),
    ("STD10", ((0, 85), (700, 75), (1000, 15), (1700, 25), (2000, 50))),
    ("STD11", ((0, 32.5), (800, 50))),
    ("STD12", ((0, 27.5), (800, 50))),
    ("STD13", ((0, 22.5), (800, 50))),
    ("STD14", ((0, 17.5), (800, 50))),
    ("STD15", ((0, 12.5), (800, 50))),
    ("STD16", ((0, 15), (1200, 25), (2000, 50))),
    ("STD17", ((0, 15), (400, 17.5), (800, 20), (1200, 22.5), (1600, 25),
               (2000, 50))),
    ("STD18", ((0, 25), (300, 20), (700, 15), (1300, 20), (1700, 25),
               (2000, 50))),
    ("STD19", ((0, 15), (1200, 75), (2000, 50))),
    ("STD20", ((0, 15), (700, 25), (1000, 85), (1700, 75), (2000, 50))),
)


# - VIBRATION 用: 0=停止 〜 100=最大。=316: ユーザー提供
#   「vib_新規基本パターン.funscript」から切り出し。STD1〜8 は 0.8 秒
#   (強さ 10〜80 → 停止)、STD9〜15 は 1 秒、STD16〜20 は 2 秒。
#   **終端は必ず停止(0)**。
STD_PATTERNS_VIB: tuple = (
    ("STD1", ((0, 10), (800, 0))),
    ("STD2", ((0, 20), (800, 0))),
    ("STD3", ((0, 30), (800, 0))),
    ("STD4", ((0, 40), (800, 0))),
    ("STD5", ((0, 50), (800, 0))),
    ("STD6", ((0, 60), (800, 0))),
    ("STD7", ((0, 70), (800, 0))),
    ("STD8", ((0, 80), (800, 0))),
    ("STD9", ((0, 50), (250, 0), (500, 50), (750, 0), (1000, 0))),
    ("STD10", ((0, 50), (200, 0), (310, 50), (530, 0), (660, 50), (860, 0),
               (1000, 0))),
    ("STD11", ((0, 60), (500, 15), (1000, 0))),
    ("STD12", ((0, 60), (250, 15), (500, 60), (750, 15), (1000, 0))),
    ("STD13", ((0, 60), (250, 45), (500, 30), (750, 15), (1000, 0))),
    ("STD14", ((0, 15), (250, 30), (500, 45), (750, 60), (1000, 0))),
    ("STD15", ((0, 15), (250, 40), (500, 60), (750, 40), (1000, 0))),
    ("STD16", ((0, 40), (250, 0), (300, 0), (500, 40), (750, 0),
               (1000, 60), (1750, 0), (2000, 0))),
    ("STD17", ((0, 60), (1000, 60), (1250, 45), (1500, 30), (1750, 15),
               (2000, 0))),
    ("STD18", ((0, 15), (200, 30), (450, 45), (700, 60), (2000, 0))),
    ("STD19", ((0, 60), (250, 0), (500, 50), (750, 0), (1000, 35),
               (1250, 0), (1500, 20), (1750, 0), (2000, 0))),
    ("STD20", ((0, 60), (750, 15), (1400, 60), (2000, 0))),
)


# パターンモード → (カタログ, 配置の基準 pos)。基準 pos が None でない=
# **定義どおりの高さで固定して置く**(縦にずらせない)モード。
PAT_MODE_LINEAR = "linear"     # linear / twist(従来どおり自由な高さ)


PAT_MODE_ROTATE = "rotate"     # csv・rotate系funscript(中心 50)


PAT_MODE_VIB = "vib"           # vibration(基準 0)


PAT_CENTERS = {PAT_MODE_LINEAR: None, PAT_MODE_ROTATE: 50,
               PAT_MODE_VIB: 0}


def std_patterns(mode: str = PAT_MODE_LINEAR) -> tuple:
    """パターンモードに対応する標準パターンのカタログ(=226)。"""
    if mode == PAT_MODE_ROTATE:
        return STD_PATTERNS_ROTATE
    if mode == PAT_MODE_VIB:
        return STD_PATTERNS_VIB
    return STD_PATTERNS


def invert_shape(shape) -> tuple:
    """インバート = pos のフルスケール反転(仕様 5.2)。"""
    return tuple((t, 100 - p) for t, p in shape)


def shape_len(shape) -> int:
    return shape[-1][0]


def _endline(shape, u: float) -> float:
    """パターン自身の両端を結ぶ線の u(0〜1) 地点の pos。"""
    p0 = shape[0][1]
    return p0 + (shape[-1][1] - p0) * u


def shape_ds(shape) -> list:
    """各点の「両端を結ぶ線からのふくらみ」d_i(仕様 5.5)。"""
    length = shape_len(shape)
    return [p - _endline(shape, t / length) for t, p in shape]


def solve_k(shape, b0: float, b1: float, pos_max: int = 100) -> float:
    """変換後の全点が 0〜pos_max に収まる最大の k(上限 K_MAX)。

    基準線 = b0→b1(u の一次)。d はパターン自身の端線からのふくらみ。
    d=0 の点は制約を課さない。判定の基準は 0〜100 全体(仕様 5.5)。
    """
    length = shape_len(shape)
    k = K_STRETCH_MAX
    for (t, _p), d in zip(shape, shape_ds(shape)):
        base = b0 + (b1 - b0) * (t / length)
        if d > 1e-9:
            k = min(k, (float(pos_max) - base) / d)
        elif d < -1e-9:
            k = min(k, (0.0 - base) / d)
    return k


def transform_pattern(shape, A: float, s: float, k: float,
                      b0: float, b1: float, grid_at: int, grid_pos: int,
                      fix_left: bool = False, fix_right: bool = False,
                      round_interior: bool = False, pos_max: int = 100):
    """パターンを配置座標へ変換する(仕様 5.5 の式)。

    - 端点は基準線の両端 (A, b0) / (A+s·L, b1) にちょうど載る(d=0)。
    - **=179(仕様 5.5 の丸め規則を改定)**: パターン内の点はグリッドへ
      丸めず、**可能な限り厳密**(整数化のみ)に置く。グリッド吸着は
      マウスで決める側(配置位置 A・拡縮の辺の行き先・移動量)にだけ掛かる。
      例: 山を s=1.0→0.9 に縮めると2点目は 500→450(400 へ丸めない)。
    - **=224(csv 用)**: `round_interior=True` のときは、内側の点の **at だけ**
      を時間グリッドへ丸める(csv の時刻は 100ms 単位のため。pos は丸めない=
      速度は pos がそのまま効く)。丸めで同じ時刻へ潰れた点は**次の格子へ
      前送り**して単調増加を保ち、末尾(端点)に届いてしまう=詰め切れない
      ときだけ不成立にする。
    - pos は 0〜pos_max でクランプ(=297: csv は 200)。整数化の結果 at が
      重複したら None。
    戻り値: [(at, pos)](at 昇順)。
    """
    length = shape_len(shape)
    if length <= 0:
        return None
    ds = shape_ds(shape)
    n = len(shape)
    out = []
    for i, ((t, _p), d) in enumerate(zip(shape, ds)):
        u = t / length
        at_f = A + s * t
        pos_f = b0 + (b1 - b0) * u + k * d
        fixed = (i == 0 and fix_left) or (i == n - 1 and fix_right)
        if fixed or not round_interior:
            at = int(round(at_f))
        else:
            at = snap(at_f, grid_at)
        pos = int(round(pos_f))
        pos = max(0, min(int(pos_max), pos))
        out.append((at, pos))
    if round_interior and len(out) > 2:
        # =224: 丸めで潰れた内側の点を次の格子へ前送りする(単調増加)
        step = max(1, int(grid_at))
        for i in range(1, len(out) - 1):
            if out[i][0] <= out[i - 1][0]:
                out[i] = (out[i - 1][0] + step, out[i][1])
        if out[-2][0] >= out[-1][0]:
            return None                 # 端まで詰まった=この長さでは置けない
    ats = [a for a, _ in out]
    if ats[0] < 0 or len(set(ats)) != len(ats) or ats != sorted(ats):
        return None
    return out


def _plan(shape, A, s, k, b0, b1, grid_at, grid_pos,
          fix_left=False, fix_right=False, round_interior=False,
          pos_max=100):
    pts = transform_pattern(shape, A, s, k, b0, b1, grid_at, grid_pos,
                            fix_left, fix_right, round_interior,
                            pos_max=pos_max)
    if pts is None:
        return None
    return {"points": pts, "A": float(A), "s": float(s), "k": float(k),
            "b0": float(b0), "b1": float(b1), "shape": tuple(shape)}


def plan_normal(shape, A: float, grid_at: int, grid_pos: int,
                s: float = 1.0, k: float = 1.0,
                b0: float | None = None, b1: float | None = None,
                round_interior: bool = False, pos_max: int = 100):
    """通常配置(仕様 5.3/5.5)。初期サイズは**定義どおり**(内側の点は
    グリッドへ丸めない=仕様 5.3)。b0/b1 を渡すと基準線を上下へずらした
    自由配置(移動 D&D 用)。"""
    if not (S_MIN - 1e-9 <= s <= S_MAX + 1e-9):
        return None
    if b0 is None:
        b0 = float(shape[0][1])
    if b1 is None:
        b1 = float(shape[-1][1])
    if not (K_MIN - 1e-9 <= k <= K_STRETCH_MAX + 1e-9):
        return None
    # 全点が 0〜pos_max に収まること(基準線をずらした自由配置のはみ出しを拒否)
    length = shape_len(shape)
    for (t, _p), d in zip(shape, shape_ds(shape)):
        base = b0 + (b1 - b0) * (t / length)
        v = base + k * d
        if v < -1e-6 or v > pos_max + 1e-6:
            return None
    return _plan(shape, A, s, k, b0, b1, grid_at, grid_pos,
                 round_interior=round_interior, pos_max=pos_max)


def plan_connect_side(shape, side: str, a: float, q: float, s: float,
                      grid_at: int, grid_pos: int,
                      k_cap: float = K_MAX,
                      round_interior: bool = False, pos_max: int = 100):
    """片側接続(仕様 5.5)。base=q(水平)、d=接続端からのずれ、k を解く。

    k_cap: k の上限(既定=K_MAX)。隣接追従・拡縮では**現在の k を保つ**
    ために現在値を渡す(=178。0〜100 に収まらないときだけ縮む)。
    """
    if not (S_MIN - 1e-9 <= s <= S_MAX + 1e-9):
        return None
    length = shape_len(shape)
    p0, plast = shape[0][1], shape[-1][1]
    # 片側接続の d は「接続端の pos からのずれ」。これは
    # b0=q / b1=q+k(反対端-接続端) の基準線と等価(端線 d へ変換できる)
    if side == "left":
        dref = p0
        A = a
    else:
        dref = plast
        A = a - s * length
    # k: base は水平 q、 d_i = p_i - dref
    k = k_cap
    for _t, p in shape:
        d = p - dref
        if d > 1e-9:
            k = min(k, (float(pos_max) - q) / d)
        elif d < -1e-9:
            k = min(k, (0.0 - q) / d)
    if k < K_MIN - 1e-9:
        return None
    k = min(k, k_cap)
    # 端線基準の canonical 形へ換算(端は必ず基準線に載る):
    #   left:  b0=q, b1=q+k(plast-p0) / right: b1=q, b0=q+k(p0-plast)
    if side == "left":
        b0, b1 = q, q + k * (plast - p0)
    else:
        b0, b1 = q + k * (p0 - plast), q
    return _plan(shape, A, s, k, b0, b1, grid_at, grid_pos,
                 fix_left=(side == "left"), fix_right=(side == "right"),
                 round_interior=round_interior, pos_max=pos_max)


def plan_connect_both(shape, a1: float, q1: float, a2: float, q2: float,
                      grid_at: int, grid_pos: int,
                      k_cap: float = K_MAX,
                      round_interior: bool = False, pos_max: int = 100):
    """両側接続(仕様 5.5)。base=q1→q2 の傾いた基準線、s=隙間へぴったり。

    k_cap: k の上限(既定=K_MAX)。隣接追従では現在の k を渡して
    **振幅を保ったまま**追従させる(=178)。
    """
    length = shape_len(shape)
    if a2 <= a1:
        return None
    s = (a2 - a1) / length
    if not (S_MIN - 1e-9 <= s <= S_MAX + 1e-9):
        return None
    k = solve_k(shape, q1, q2, pos_max)
    if k < K_MIN - 1e-9:
        return None
    k = min(k, k_cap)
    return _plan(shape, a1, s, k, q1, q2, grid_at, grid_pos,
                 fix_left=True, fix_right=True,
                 round_interior=round_interior, pos_max=pos_max)
