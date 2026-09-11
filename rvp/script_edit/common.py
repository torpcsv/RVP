"""スクリプト編集の共通: 定数(ユーザーパターン/Fキー/グリッド/Undo)・波形帯・目盛り・スナップ。"""
from __future__ import annotations

from ..i18n import tr



# グリッドの選択肢(仕様 4.5)。「なし」= 1単位。
# =204: funscript へのパターン記憶(仕様 6.2)
RVP_KEY = "rvp"


RVP_VERSION = 1


# =205: ユーザーパターン(仕様 6.1)。名前は固定・改名機能なし。
# =207: 10枠へ拡張(U1〜U10) → =218: 20枠へ拡張(U1〜U20)。
# **=231: 種別ごとに別々の20枠**(ユーザー決定)。枠の名前は
#   linear=L1〜L20 / twist=T1〜T20 / rotate_ufo=U1〜U20 /
#   rotate_a10cyclonesa=A1〜A20 / vibration=V1〜V20。
# 保存先は **config の "script_edit.user_patterns_v2"**(=231 で新設)。
# 旧 "user_patterns"(種別で分かれていない U1〜U20)は**読まない**
# =ユーザー決定「全部捨てて未登録から」。新しい U1〜U20 は UFO 用なので、
# 旧データをそのまま読むと別の種別の絵が入ってしまうため、キーを分けた。
USER_PAT_SLOTS = 20


USER_PAT_PREFIX = {"linear": "L", "twist": "T", "rotate_ufo": "U",
                   "rotate_a10cyclonesa": "A", "vibration": "V"}


USER_PAT_KINDS = tuple(USER_PAT_PREFIX)


USER_PAT_CFG_KEY = "user_patterns_v2"


def user_pat_prefix(kind: str) -> str:
    """種別 → 枠の接頭辞(未知の種別は linear 扱い)。"""
    return USER_PAT_PREFIX.get(kind, "L")


def user_pattern_keys(kind: str = "linear") -> tuple:
    """その種別の枠の名前 (X1 … X20)。"""
    pre = user_pat_prefix(kind)
    return tuple(f"{pre}{i}" for i in range(1, USER_PAT_SLOTS + 1))


def user_pat_key(kind: str, slot: int) -> str:
    """(種別, 枠番号 1〜20) → 枠の名前。"""
    return f"{user_pat_prefix(kind)}{int(slot)}"


# 全種別ぶんの枠名(config の検証用)
ALL_USER_PATTERN_KEYS = tuple(k for kind in USER_PAT_KINDS
                              for k in user_pattern_keys(kind))


# 互換: 旧名(linear の枠を指す)。新しいコードは user_pattern_keys() を使う。
USER_PATTERN_KEYS = user_pattern_keys("linear")


# =212: リアルタイム配置のファンクションキー(F10 は使わない)
FKEYS = tuple(f"F{i}" for i in range(1, 10))


# =213/=242: 時間補正(秒)。押し遅れを遡るマイナス方向のみ。=242 で
# コンボ(0.1刻み)から**上下ボタンつきの入力欄(0.01刻み)**へ変更した。
TIME_ADJ_MIN = -1.0


TIME_ADJ_STEP = 0.01


# =230: pos に 2単位 / at に 0.02秒(20ms)単位を追加(ユーザー要望)。
# **csv は従来どおり 100ms 以上だけ**(_apply_edit_kind が絞り込む)。
GRID_POS_CHOICES = (20, 10, 5, 2, 1)


GRID_AT_CHOICES = (1000, 500, 200, 100, 50, 20, 10, 1)


GRID_POS_DEFAULT = 10


GRID_AT_DEFAULT = 100


UNDO_LIMIT = 100          # 仕様 4.8: 100ステップを超えたら古いものから捨てる


EMPTY_VIEW_MS = 30_000    # 仕様 4.9: 音声もスクリプトも無いときの初期表示幅


# =243: 音声波形の帯の色(ライト系=背景よりやや濃い / ダーク=やや薄い)。
# 基準線(C_GRID_POS_SUB)より控えめで、点・線・基準線の背面に描く。
WAVE_BAND_COLOR = ("#dcdcdc", "#3f3f3f")


WAVE_BAND_STEP_PX = 2       # 帯を作る横方向の刻み(px)


def draw_audio_band(canvas, chan, bucket_ms: int, peak: int,
                    px0: float, px1: float, ms_of_px, cy: float,
                    half_h: float, color: str) -> None:
    """音声波形(振幅エンベロープ)の帯を1本描く(=243)。

    chan: 10ms などのバケットごとのピーク列(array)。cy を中央線として
    上下対称の帯(create_polygon)にする。素材範囲の外は描かない。
    ms_of_px(x) はそのグラフの x → **素材時刻(ms)** の写像
    (レビュー参照モードは区間の開始ぶんのオフセットを含む)。
    """
    n = len(chan)
    if n <= 0 or peak <= 0 or half_h <= 1:
        return
    dur = n * bucket_ms
    scale = float(half_h) / float(peak)
    step = WAVE_BAND_STEP_PX
    tops: list = []
    bots: list = []

    def _flush():
        if len(tops) >= 4:
            flat = list(tops)
            for bx, by in reversed(bots):
                flat += [bx, by]
            canvas.create_polygon(*flat, fill=color, outline="",
                                  tags="audio_wave")
        tops.clear()
        bots.clear()

    x = px0
    while x <= px1:
        t0 = ms_of_px(x)
        t1 = ms_of_px(x + step)
        if t1 <= 0 or t0 >= dur:
            _flush()
            x += step
            continue
        b0 = max(0, int(t0 // bucket_ms))
        b1 = min(n, max(b0 + 1, -(-int(t1) // bucket_ms)))
        v = max(chan[b0:b1]) if b1 > b0 else 0
        y = v * scale
        tops += [x, cy - y]
        bots.append((x, cy + y))
        x += step
    _flush()


def grid_label(step: int, unit_none: str) -> str:
    """グリッドコンボの表示文字列。1単位は「なし」。"""
    return unit_none if step <= 1 else tr("{0}単位").format(step)


def scale_shape(shape, pos_max: int):
    """=297: 0〜100 で定義されたパターン(標準/ユーザー)を pos_max の
    分解能へ縦に伸ばす(csv=200 なら 2 倍・中心 50→100)。100 のときは
    そのまま返す。"""
    f = int(pos_max) / 100.0
    if abs(f - 1.0) < 1e-9:
        return tuple(shape)
    return tuple((t, int(round(p * f))) for t, p in shape)


def grid_at_label(step: int, unit_none: str) -> str:
    """時間[at]グリッドの表示文字列(=208: 秒表記で分かりやすく)。

    1000→「1秒単位」/ 500→「0.5秒単位」/ … / 10→「0.01秒単位」。
    1ms は「なし」(グリッドなし相当)。
    """
    if step <= 1:
        return unit_none
    return tr("{0}秒単位").format("{0:g}".format(step / 1000.0))


def snap(value: float, grid: int) -> int:
    """最寄りの格子へ丸める(grid<=1 は1ms/1pos単位=丸めなし相当)。"""
    g = max(1, int(grid))
    return int(round(float(value) / g)) * g


def moved_points(points, selection, dat: float, dpos: float,
                 grid_at: int, grid_pos: int, pos_max: int = 100):
    """選択中の点を (dat, dpos) だけ動かした結果を計算する純関数。

    移動の**結果**を最寄りの格子へ丸める(仕様 4.5。元から格子外の点も
    「動かしたときだけ」吸着する)。戻り値は (mapping, ok):
      mapping = {旧at: (新at, 新pos)}
      ok      = False なら衝突(移動先に他の点がある/移動同士で重複)
    ドラッグ中のゴースト表示と確定(ScriptEditModel.move_selected)の両方が
    これを使う=見えている位置がそのまま確定位置になる。
    """
    sel = set(selection)
    mapping = {}
    new_ats = set()
    others = {at for at, _ in points if at not in sel}
    ok = True
    for at, pos in points:
        if at not in sel:
            continue
        na = snap(max(0.0, at + dat), grid_at)
        np_ = max(0, min(pos_max, snap(pos + dpos, grid_pos)))
        mapping[at] = (na, np_)
        if na in others or na in new_ats:
            ok = False
        new_ats.add(na)
    return mapping, ok
