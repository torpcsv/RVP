"""スクリプト編集機能(=170〜)P1: funscript の打点編集。

アイテムレビュー画面(editor.ItemReviewDialog)の「スクリプト編集」モードから
使われる。editor.py が既に11,000行を超えているため、編集ロジックと編集用
グラフはこのモジュールへ切り出す(仕様書 7.1)。

構成:
  - 純ロジック(Tk非依存): snap / moved_points / ScriptEditModel /
    dump_funscript / load_funscript_raw / write_funscript
  - ScriptEditGraph(tk.Canvas): 編集用グラフ。main.DeviceGraph の考え方
    (LEVELS の縮尺段階・座標変換・時間グリッド)を流用した**新規クラス**
    (DeviceGraph はスナップショットを描くだけの読み取り専用のため継承しない)。

P1 の対象は linear / twist の funscript のみ。点の打点・移動・選択・削除・
矩形選択・コピー/切取/貼付・UNDO/REDO・グリッド吸着・ズーム/パン。
パターン(P2)・ユーザーパターン(P3)は未実装だが、UNDO スナップショットや
描画は後から足せる形にしてある。

**このモジュールはデバイスへ一切送信しない**(=164 の方針を編集モードにも
適用する。test_script_edit_ui の回帰テストがソースを検査する)。
"""

from __future__ import annotations

import json
import os
import tkinter as tk

from .i18n import tr
from . import appfont

# ---------------------------------------------------------------------------
# 定数(グリッド・UNDO)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# P2: 標準パターンと変換モデル「基準線 + 振幅 k」(仕様 5.1 / 5.5・=173)
# ---------------------------------------------------------------------------

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


# =226: **離散的なスクリプト用の標準パターン**(ユーザー提供
# 「標準パターン_ufo.csv」を **2秒ごと**に切り出した20個)。
# linear/twist(=217 の STD_PATTERNS・1秒)とは考え方が違うので**別カタログ**。
#
# - ROTATE 用(csv・rotate系funscript 共通): pos 50=停止 / 上=正回転 /
#   下=逆回転。**配置時は定義どおりの pos で固定**(=226 要望4)。
#   基準長 2000ms。速度はすべて偶数なので写像の丸めは起きない。
#   **=227: 2秒時点(最後の点)は必ず「停止」**(ROTATE=50 / VIBRATION=0)。
#   パターン単体で置いても回りっぱなしにならず、数珠つなぎの境目も
#   「いったん止まる」形になる(ユーザー決定)。
STD_PATTERNS_ROTATE: tuple = (
    ("STD1", ((0, 75), (1000, 25), (2000, 50))),
    ("STD2", ((0, 75), (500, 70), (1000, 25), (1500, 30), (2000, 50))),
    ("STD3", ((0, 75), (500, 71), (1000, 67), (1500, 63), (2000, 50))),
    ("STD4", ((0, 75), (600, 50), (1000, 75), (1600, 50), (2000, 50))),
    ("STD5", ((0, 75), (600, 50), (1000, 30), (1600, 50), (1800, 50),
              (2000, 50))),
    ("STD6", ((0, 75), (300, 50), (500, 75), (800, 50), (1000, 75),
              (1300, 50), (1500, 75), (1800, 50), (2000, 50))),
    ("STD7", ((0, 75), (200, 75), (300, 50), (500, 75), (800, 50),
              (1000, 30), (1300, 50), (1500, 30), (1800, 50), (2000, 50))),
    ("STD8", ((0, 75), (1700, 35), (2000, 50))),
    ("STD9", ((0, 75), (1000, 35), (1500, 50), (2000, 50))),
    ("STD10", ((0, 75), (300, 35), (1700, 50), (2000, 50))),
    ("STD11", ((0, 80), (200, 79), (400, 78), (600, 77), (800, 76),
               (1000, 74), (1200, 72), (1400, 70), (1600, 68), (1800, 66),
               (2000, 50))),
    ("STD12", ((0, 80), (200, 78), (400, 76), (600, 74), (800, 72),
               (1000, 20), (1200, 22), (1400, 24), (1600, 28), (1800, 30),
               (2000, 50))),
    ("STD13", ((0, 70), (200, 72), (400, 74), (600, 76), (800, 78),
               (1000, 80), (1200, 78), (1400, 76), (1600, 74), (1800, 72),
               (2000, 50))),
    ("STD14", ((0, 70), (200, 72), (400, 74), (600, 76), (800, 78),
               (1000, 20), (1200, 22), (1400, 24), (1600, 26), (1800, 28),
               (2000, 50))),
    ("STD15", ((0, 66), (200, 68), (400, 70), (600, 72), (800, 74),
               (1000, 76), (1200, 77), (1400, 78), (1600, 79), (1800, 80),
               (2000, 50))),
    ("STD16", ((0, 80), (1000, 77), (1200, 74), (1400, 71), (1600, 68),
               (1800, 65), (2000, 50))),
    ("STD17", ((0, 65), (200, 68), (400, 71), (600, 74), (800, 78),
               (1000, 80), (2000, 50))),
    ("STD18", ((0, 80), (200, 78), (400, 76), (600, 74), (800, 72),
               (1000, 80), (1200, 78), (1400, 76), (1600, 74), (1800, 72),
               (2000, 50))),
    ("STD19", ((0, 80), (200, 79), (400, 78), (600, 77), (800, 76),
               (1000, 75), (1700, 50), (2000, 50))),
    ("STD20", ((0, 80), (200, 79), (400, 78), (600, 77), (800, 76),
               (1000, 50), (1400, 74), (1600, 76), (1800, 78), (2000, 50))),
)

# - VIBRATION 用: ROTATE 版から **|pos-50|×2**(=速度の大きさ)で作る
#   (ユーザー決定 =226 要望7)。0=停止 〜 100=最大。基準長 2000ms。
STD_PATTERNS_VIB: tuple = (
    ("STD1", ((0, 50), (1000, 50), (2000, 0))),
    ("STD2", ((0, 50), (500, 40), (1000, 50), (1500, 40), (2000, 0))),
    ("STD3", ((0, 50), (500, 42), (1000, 34), (1500, 26), (2000, 0))),
    ("STD4", ((0, 50), (600, 0), (1000, 50), (1600, 0), (2000, 0))),
    ("STD5", ((0, 50), (600, 0), (1000, 40), (1600, 0), (1800, 0), (2000, 0))),
    ("STD6", ((0, 50), (300, 0), (500, 50), (800, 0), (1000, 50), (1300, 0),
              (1500, 50), (1800, 0), (2000, 0))),
    ("STD7", ((0, 50), (200, 50), (300, 0), (500, 50), (800, 0), (1000, 40),
              (1300, 0), (1500, 40), (1800, 0), (2000, 0))),
    ("STD8", ((0, 50), (1700, 30), (2000, 0))),
    ("STD9", ((0, 50), (1000, 30), (1500, 0), (2000, 0))),
    ("STD10", ((0, 50), (300, 30), (1700, 0), (2000, 0))),
    ("STD11", ((0, 60), (200, 58), (400, 56), (600, 54), (800, 52),
               (1000, 48), (1200, 44), (1400, 40), (1600, 36), (1800, 32),
               (2000, 0))),
    ("STD12", ((0, 60), (200, 56), (400, 52), (600, 48), (800, 44),
               (1000, 60), (1200, 56), (1400, 52), (1600, 44), (1800, 40),
               (2000, 0))),
    ("STD13", ((0, 40), (200, 44), (400, 48), (600, 52), (800, 56),
               (1000, 60), (1200, 56), (1400, 52), (1600, 48), (1800, 44),
               (2000, 0))),
    ("STD14", ((0, 40), (200, 44), (400, 48), (600, 52), (800, 56),
               (1000, 60), (1200, 56), (1400, 52), (1600, 48), (1800, 44),
               (2000, 0))),
    ("STD15", ((0, 32), (200, 36), (400, 40), (600, 44), (800, 48),
               (1000, 52), (1200, 54), (1400, 56), (1600, 58), (1800, 60),
               (2000, 0))),
    ("STD16", ((0, 60), (1000, 54), (1200, 48), (1400, 42), (1600, 36),
               (1800, 30), (2000, 0))),
    ("STD17", ((0, 30), (200, 36), (400, 42), (600, 48), (800, 56),
               (1000, 60), (2000, 0))),
    ("STD18", ((0, 60), (200, 56), (400, 52), (600, 48), (800, 44),
               (1000, 60), (1200, 56), (1400, 52), (1600, 48), (1800, 44),
               (2000, 0))),
    ("STD19", ((0, 60), (200, 58), (400, 56), (600, 54), (800, 52),
               (1000, 50), (1700, 0), (2000, 0))),
    ("STD20", ((0, 60), (200, 58), (400, 56), (600, 54), (800, 52),
               (1000, 0), (1400, 48), (1600, 52), (1800, 56), (2000, 0))),
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


class EditLink:
    """=298: 5列csv(UFOTW)の左右2本の編集モデルを束ねる(ユーザー要望2/3)。

    - **クリップボードの共有**: どちらでコピー/切り取りしても、全モデルの
      clipboard へ同じ内容が入る(片方でコピー→もう片方へ貼り付け)。
    - **UNDO/REDO の一本化**: 変更の発生順を `order` に記録し、Ctrl+Z は
      どのグラフにフォーカスがあっても**最後に変更したモデル**を1つ戻す
      (左→右→左→右と打点したら、右→左→右→左の順に戻る)。REDO も同じ。
      各モデルの `_undo`/`_redo` はそのまま使い、順番だけをここで持つ。
    """

    def __init__(self, models=()):
        self.models: list = list(models)
        self.order: list = []          # 変更した順のモデル(UNDO 用)
        self.redo_order: list = []     # 戻した順のモデル(REDO 用)
        for m in self.models:
            m.link = self

    def reset(self) -> None:
        self.order = []
        self.redo_order = []

    def record(self, model) -> None:
        """model._push() から呼ばれる: 変更順を積み、REDO を捨てる。"""
        self.order.append(model)
        if len(self.order) > UNDO_LIMIT * max(1, len(self.models)):
            del self.order[0]
        self.redo_order = []

    def share_clipboard(self, src) -> None:
        for m in self.models:
            if m is not src:
                m.clipboard = list(src.clipboard) \
                    if src.clipboard is not None else None
                m.clipboard_patterns = [dict(pc) for pc in
                                        src.clipboard_patterns]

    def undo(self):
        """最後に変更したモデルを1つ戻す。戻したモデル(無ければ None)。"""
        while self.order:
            m = self.order.pop()
            if m._undo_local():
                self.redo_order.append(m)
                return m
        return None

    def redo(self):
        while self.redo_order:
            m = self.redo_order.pop()
            if m._redo_local():
                self.order.append(m)
                return m
        return None


class ScriptEditModel:

    """編集中の点列と選択・クリップボード・UNDO を持つ(Tk非依存)。

    - points: [(at, pos)] を at 昇順・at一意で保持
    - selection: 選択中の点の at の集合(at が一意なので識別子に使える)
    - UNDO は**点列のスナップショットを積むだけ**の素朴な方式(仕様 4.8。
      funscript は数千点でも軽い)。パターン(P2)は同じスナップショットへ
      一覧を足す形で拡張する。
    """

    def __init__(self):
        self.points: list[tuple[int, int]] = []
        self.selection: set[int] = set()
        self.clipboard: list[tuple[int, int]] | None = None
        # P2(=173): 配置済みパターンの一覧。各要素は dict:
        #   name(表示名|None=無名) / shape(正規化点列・インバート適用後) /
        #   A / s / k / b0 / b1(変換パラメータ=canonical 端線基準) /
        #   ats(構成点の at のタプル) / owned(このパターンが挿入した at。
        #   共有された点は含まない=移動・削除で消してよい点)
        self.patterns: list[dict] = []
        # =185: パターンのクリップボード(点と合わせた混在コピー)。
        # 各要素 {"rel": 先頭からの相対at, "shape": ((t,pos),...), "name"}
        self.clipboard_patterns: list[dict] = []
        # =185: 複数選択中のパターン index の集合(点の selection と併存)
        self.pattern_selection: set[int] = set()
        # =183: 端点の禁止帯の幅(端点±edge_tol へは打点・移動・貼り付け
        # 不可)。グラフが grid_at 変更時に max(1, atグリッド×0.5) へ更新
        self.edge_tol: float = max(1.0, GRID_AT_DEFAULT * 0.5)
        # =222: 直前に place_pattern_over で置いた区間 (at開始, at終端)。
        # Fキー長押しの数珠つなぎが次の起点に使う
        self.last_place_range = None
        # =224: csv 編集では**パターン内側の点もグリッドへ丸める**
        # (csv の時刻は 100ms 単位なので、内側も格子に乗せる必要がある)
        self.round_interior = False
        # =233: 離散的なスクリプトの中心(ROTATE=50 / VIBRATION=0)。
        # 上下反転の軸に使う(None=linear/twist は従来どおり基準線が軸)。
        self.pat_center = None
        # =226: 階段(離散)として扱うか。上書き配置の境目の規則が変わる
        # (前のパターンを消さず、境目の点だけ未来側のパターンへ譲る)
        self.step_mode = False
        # =297: pos の上限(分解能)。funscript=100 / csv=200(速度1刻み:
        # 下端=逆回転100 / 中央100=停止 / 上端=正回転100)。
        self.pos_max = 100
        # =298: 左右2本を束ねる EditLink(None=単独)。
        self.link = None
        self.last_undone = None        # 直前の undo/redo で戻したモデル
        self._undo: list = []
        self._redo: list = []
        self.dirty = False

    # ---- 状態 ----

    def load(self, actions) -> None:
        """点列を読み込み、選択・UNDO・dirty をリセットする。

        actions: [(at,pos)] または funscript の action dict の列。
        同一 at は後勝ちで1点に集約する(funscript 側の保証が無いため)。
        """
        pts = {}
        for a in actions or ():
            if isinstance(a, dict):
                try:
                    at, pos = int(a["at"]), int(a["pos"])
                except (KeyError, TypeError, ValueError):
                    continue
            else:
                at, pos = int(a[0]), int(a[1])
            if at >= 0:
                pts[at] = max(0, min(self.pos_max, pos))
        self.points = sorted(pts.items())
        self.selection = set()
        self.clipboard = None
        self.clipboard_patterns = []
        self.pattern_selection = set()
        self.patterns = []
        self._undo = []
        self._redo = []
        self.dirty = False
        if self.link is not None:          # =298: 読み直しで順番も捨てる
            self.link.reset()

    def duration_ms(self) -> int:
        return self.points[-1][0] if self.points else 0

    # ---- パターン記憶(=204・仕様 6.2) ----

    def export_patterns(self):
        """funscript へ埋め込む "rvp" 辞書を作る。パターンが無ければ None
        (キー自体を書かない)。

        仕様 6.2 の at/s/k/base/shape に加えて、**実現された点列 `pts` と
        所有点 `owned` も書く**(=204での拡張)。読み込み時の照合を
        「保存時のグリッド設定に依存せず」厳密にでき、共有端の所有関係も
        正確に往復できるため。
        """
        recs = []
        pos_map = dict(self.points)
        for rec in self.patterns:
            if not rec["ats"]:
                continue
            pts = [[int(a), int(pos_map.get(a, 0))] for a in rec["ats"]]
            recs.append({
                "name": rec.get("name"),
                "at": float(rec["A"]), "s": float(rec["s"]),
                "k": float(rec["k"]),
                "base": [float(rec["b0"]), float(rec["b1"])],
                "shape": [[float(t), float(p)] for t, p in rec["shape"]],
                "pts": pts,
                "owned": [int(a) for a in rec["owned"]],
            })
        if not recs:
            return None
        return {"version": RVP_VERSION, "patterns": recs}

    def import_patterns(self, raw) -> tuple[int, int]:
        """"rvp" 辞書からパターンを復元する(=204)。戻り値 (復元数, 破棄数)。

        self.points は load() 済みが前提。照合に失敗したパターンは
        **その情報だけを捨て、点は actions のまま残す**(仕様 6.2 =
        他アプリで編集された結果を壊さない)。
        """
        self.patterns = []
        if not isinstance(raw, dict):
            return 0, 0
        items = raw.get("patterns")
        if not isinstance(items, list):
            return 0, 0
        if raw.get("version") != RVP_VERSION:
            return 0, len(items)
        pos_map = dict(self.points)
        restored = dropped = 0
        claimed: set = set()
        ranges: list = []
        for it in items:
            rec = self._import_one_pattern(it, pos_map, claimed, ranges)
            if rec is None:
                dropped += 1
                continue
            self.patterns.append(rec)
            claimed.update(rec["ats"])
            ranges.append((rec["ats"][0], rec["ats"][-1]))
            restored += 1
        return restored, dropped

    def _import_one_pattern(self, it, pos_map, claimed, ranges):
        """1パターンぶんの照合と復元。不正・不一致は None(=破棄)。"""
        try:
            shape = tuple((float(t), float(p)) for t, p in it["shape"])
            pts = [(int(a), int(p)) for a, p in it["pts"]]
            A = float(it["at"])
            sc = float(it["s"])
            k = float(it["k"])
            b0, b1 = (float(x) for x in it["base"])
            name = it.get("name")
        except (KeyError, TypeError, ValueError):
            return None
        if name is not None and not isinstance(name, str):
            return None
        if len(shape) < 2 or len(pts) != len(shape):
            return None
        ats = [a for a, _ in pts]
        if len(ats) < 2 or ats != sorted(set(ats)):
            return None
        # 照合1: パターンの全点が actions に(at,pos そのまま)ある
        for a, p in pts:
            if pos_map.get(a) != p:
                return None
        # 照合2: 範囲の内側に無関係の点が挟まっていない
        aset = set(ats)
        lo, hi = ats[0], ats[-1]
        for a, _p in self.points:
            if lo < a < hi and a not in aset:
                return None
        # 照合3: 復元済みのパターンと内側で重ならない(端の共有は可)
        for plo, phi in ranges:
            if lo < phi and plo < hi:
                return None
        owned = None
        if isinstance(it.get("owned"), list):
            try:
                owned = tuple(int(a) for a in it["owned"] if int(a) in aset)
            except (TypeError, ValueError):
                owned = None
        if owned is None:
            # 旧データ等で owned が無ければ「先に復元したパターンと
            # 共有していない点」を所有とみなす
            owned = tuple(a for a in ats if a not in claimed)
        return {"name": name, "shape": shape, "A": A, "s": sc, "k": k,
                "b0": b0, "b1": b1, "ats": tuple(ats), "owned": owned}

    def pos_of(self, at: int):
        for a, p in self.points:
            if a == at:
                return p
        return None

    def _sorted_set(self, pts: dict) -> None:
        self.points = sorted(pts.items())

    # ---- UNDO ----

    def _snapshot(self):
        return (list(self.points), [dict(p) for p in self.patterns])

    def _restore(self, snap) -> None:
        self.points, pats = snap
        self.patterns = [dict(p) for p in pats]

    def _push(self) -> None:
        self._undo.append(self._snapshot())
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo = []
        self.dirty = True
        if self.link is not None:
            self.link.record(self)

    def undo(self) -> bool:
        """=298: link があれば**最後に変更したモデル**(自分とは限らない)を
        戻す。戻したモデルは `last_undone` に入る(グラフの再描画用)。"""
        if self.link is not None:
            m = self.link.undo()
            self.last_undone = m
            return m is not None
        self.last_undone = self if self._undo_local() else None
        return self.last_undone is not None

    def redo(self) -> bool:
        if self.link is not None:
            m = self.link.redo()
            self.last_undone = m
            return m is not None
        self.last_undone = self if self._redo_local() else None
        return self.last_undone is not None

    def _undo_local(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        ats = {a for a, _ in self.points}
        self.selection &= ats
        self.pattern_selection = set()      # index が変わり得るため(=185)
        self.dirty = True
        return True

    def _redo_local(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        ats = {a for a, _ in self.points}
        self.selection &= ats
        self.pattern_selection = set()
        self.dirty = True
        return True

    # ---- 選択 ----

    def select_only(self, at: int) -> None:
        ok = self.pos_of(at) is not None and at not in self.pattern_ats()
        self.selection = {at} if ok else set()

    def toggle_select(self, at: int) -> None:
        if self.pos_of(at) is None or at in self.pattern_ats():
            return
        if at in self.selection:
            self.selection.discard(at)
        else:
            self.selection.add(at)

    def clear_selection(self) -> None:
        self.selection = set()

    def select_rect(self, at0: float, at1: float,
                    pos0: float, pos1: float) -> None:
        """矩形選択(仕様 4.3/=185/=281)。

        - 点: 矩形に**完全に入った**点(パターンの構成点は除く)。
        - パターン(グループ): =281 で「構成点のすべてが矩形の中」から
          **「グループの矩形(at 範囲×pos の最小〜最大)が矩形に一部でも
          触れている/含まれている」**へ緩和(実機FB。当てやすくする)。
          点の個別選択はできないままだが、パターン単位では複数選択して
          コピー・削除・グループ化・グループ解除ができる。
        """
        lo_a, hi_a = min(at0, at1), max(at0, at1)
        lo_p, hi_p = min(pos0, pos1), max(pos0, pos1)
        pat = self.pattern_ats()
        self.selection = {a for a, p in self.points
                          if lo_a <= a <= hi_a and lo_p <= p <= hi_p
                          and a not in pat}
        self.pattern_selection = set()
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            vals = [self.pos_of(a) for a in rec["ats"]]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            plo, phi = rec["ats"][0], rec["ats"][-1]
            pmin, pmax = min(vals), max(vals)
            if plo <= hi_a and phi >= lo_a and pmin <= hi_p and pmax >= lo_p:
                self.pattern_selection.add(i)

    def toggle_pattern_select(self, index: int) -> None:
        """Ctrl+クリックによるパターンの追加選択・解除(=185)。"""
        if not (0 <= index < len(self.patterns)):
            return
        if index in self.pattern_selection:
            self.pattern_selection.discard(index)
        else:
            self.pattern_selection.add(index)

    # ---- 編集 ----

    def add_point(self, at: int, pos: int) -> bool:
        """打点。同じ at に既存の(普通の)点があるときは**上書き**する
        (=176 実機FB。クリックした pos へ打点し直せる)。
        パターンの構成点・パターンの時間範囲の内側は従来どおり拒否。"""
        at = int(at)
        pos = max(0, min(self.pos_max, int(pos)))
        if at < 0 or self.at_blocked(at) or at in self.pattern_ats():
            return False
        if self.pos_of(at) == pos:
            return False        # 同一なら何もしない(UNDOを積まない)
        self._push()
        pts = dict(self.points)
        pts[at] = pos
        self._sorted_set(pts)
        self.selection = {at}
        return True

    def delete_selected(self) -> bool:
        if not self.selection:
            return False
        self._push()
        self.points = [(a, p) for a, p in self.points
                       if a not in self.selection]
        self.selection = set()
        return True

    def move_selected(self, dat: float, dpos: float,
                      grid_at: int, grid_pos: int) -> bool:
        """選択中の点をまとめて移動する(結果をグリッドへ吸着)。

        移動先で衝突するとき(他の点と同じ at / 移動同士で重複)は
        **何も起きない**(仕様 10b)。移動量0(丸めた結果が同一)なら
        UNDO ステップを積まない。
        """
        if not self.selection:
            return False
        mapping, ok = moved_points(self.points, self.selection,
                                   dat, dpos, grid_at, grid_pos,
                                   pos_max=self.pos_max)
        if not ok:
            return False
        if any(self.at_blocked(na) for na, _ in mapping.values()):
            return False
        if all(mapping[a] == (a, self.pos_of(a)) for a in mapping):
            return False
        self._push()
        pts = dict(self.points)
        for old in mapping:
            del pts[old]
        for old, (na, np_) in mapping.items():
            pts[na] = np_
        self._sorted_set(pts)
        self.selection = {na for na, _ in mapping.values()}
        return True

    def set_point(self, at: int, new_at: int, new_pos: int) -> bool:
        """(at,pos) 数値入力欄からの確定(仕様 4.6)。

        グリッドへは**丸めない**(明示的に入力された値をそのまま使う)。
        移動先に別の点があれば False(呼び出し側が表示を元へ戻す)。
        """
        if self.pos_of(at) is None:
            return False
        new_at = int(new_at)
        new_pos = max(0, min(self.pos_max, int(new_pos)))
        if new_at < 0:
            return False
        if new_at != at and self.pos_of(new_at) is not None:
            return False
        if new_at != at and self.at_blocked(new_at):
            return False
        if (new_at, new_pos) == (at, self.pos_of(at)):
            return False
        self._push()
        pts = dict(self.points)
        del pts[at]
        pts[new_at] = new_pos
        self._sorted_set(pts)
        self.selection = {new_at}
        return True

    # ---- パターン(P2 =173) ----

    def pattern_ats(self) -> set:
        """全パターンの構成点の at 集合(=個別選択できない点)。"""
        out = set()
        for p in self.patterns:
            out.update(p["ats"])
        return out

    def at_in_pattern(self, at: float) -> bool:
        """at がいずれかのパターンの時間範囲の**内側**にあるか(=174)。

        パターンの範囲は一体として扱うので、範囲内へ普通の点を打つ・
        動かす・貼ることはできない(端の at は既存点の衝突判定に任せる)。
        """
        for rec in self.patterns:
            if rec["ats"] and rec["ats"][0] < at < rec["ats"][-1]:
                return True
        return False

    def at_blocked(self, at: float) -> bool:
        """普通の点を置けない at か(=183)。

        パターンの範囲の内側(=174)に加え、**始端・終端の±edge_tol
        (atグリッド半分・最低1ms)の禁止帯**も拒否する。端点がグリッド外
        にあるとき、真上をクリックするとグリッド吸着で数ms隣へ「見た目は
        端点に重なる点」がいつのまにか置かれてしまう問題への対処。
        打点・点の移動・数値入力・貼り付けの全経路がこれを使う。
        """
        tol = self.edge_tol
        for rec in self.patterns:
            if rec["ats"] and \
                    rec["ats"][0] - tol <= at <= rec["ats"][-1] + tol:
                return True
        return False

    def shared_endpoints(self) -> set:
        """共有されている端点の at の集合(=184: ◎で描く箇所)。

        端点のうち **owned に無いもの**=配置時にすでに存在していた点
        (通常の点への接続、または別パターンの端点との接続)。
        グループ化で作ったパターンは自分の点を owned に持つので、
        端点は共有扱いにならない(後から別パターンが繋がれば、その
        パターン側の端点が owned 外となり共有になる)。
        """
        out = set()
        for rec in self.patterns:
            if rec["ats"]:
                for a in (rec["ats"][0], rec["ats"][-1]):
                    if a not in rec["owned"]:
                        out.add(a)
        return out

    def pattern_of_at(self, at: int) -> int | None:
        for i, p in enumerate(self.patterns):
            if at in p["ats"]:
                return i
        return None

    def pattern_endpoints(self, exclude: int | None = None) -> list:
        """接続相手になれる端点 [(pattern_idx, at, pos, side)](仕様 4-4)。"""
        out = []
        for i, p in enumerate(self.patterns):
            if i == exclude or not p["ats"]:
                continue
            lo, hi = p["ats"][0], p["ats"][-1]
            pl, ph = self.pos_of(lo), self.pos_of(hi)
            if pl is not None:
                out.append((i, lo, pl, "L"))
            if ph is not None:
                out.append((i, hi, ph, "R"))
        return out

    def check_pattern_points(self, pts, exclude: int | None = None):
        """パターン配置の衝突判定(仕様 5.3/決定10・11)。

        - 時間範囲の**内側**に既存点が1つでもあれば拒否
        - 両端: 同じ at の既存点は **pos が一致すれば「点を共有」**、違えば拒否
        exclude を渡すと、そのパターンが**単独所有する点**を無視する
        (移動・拡縮のとき自分自身と衝突しないため)。
        戻り値: (ok, shared_ats)
        """
        if not pts:
            return False, set()
        ignore = set()
        if exclude is not None:
            others = set()
            for i, p in enumerate(self.patterns):
                if i != exclude:
                    others.update(p["ats"])
            ignore = {a for a in self.patterns[exclude]["owned"]
                      if a not in others}
        existing = {a: p for a, p in self.points if a not in ignore}
        lo, hi = pts[0][0], pts[-1][0]
        for a in existing:
            if lo < a < hi:
                return False, set()
        shared = set()
        for a, p in (pts[0], pts[-1]):
            if a in existing:
                if existing[a] != p:
                    return False, set()
                shared.add(a)
        return True, shared

    def _apply_pattern(self, plan: dict, name, index: int | None,
                       shared: set) -> None:
        """plan の点列を points へ反映し、パターン記録を作る/置き換える。"""
        pts = dict(self.points)
        if index is not None:
            others = set()
            for i, p in enumerate(self.patterns):
                if i != index:
                    others.update(p["ats"])
            for a in self.patterns[index]["owned"]:
                if a not in others:
                    pts.pop(a, None)
        new_ats = []
        for a, p in plan["points"]:
            new_ats.append(a)
            pts[a] = p
        self._sorted_set(pts)
        # =279: 端点が既存の**通常の点**に重なったら、その点はパターンが
        # 取り込む(owned に含める=以後パターンと一緒に動き、パターンを
        # 消せば消える)。従来は「共有=◎」として通常の点を残していたが、
        # 取り残された点がパターンのD&D・拡縮の邪魔になるため(実機FB)。
        # パターン同士の境目は =230 のとおり両方が自分の点として持つ。
        rec = {"name": name, "shape": tuple(plan["shape"]),
               "A": plan["A"], "s": plan["s"], "k": plan["k"],
               "b0": plan["b0"], "b1": plan["b1"],
               "ats": tuple(new_ats), "owned": tuple(new_ats)}
        if index is None:
            self.patterns.append(rec)
        else:
            rec["name"] = self.patterns[index]["name"]
            self.patterns[index] = rec

    def overwrite_preview(self, base: int, end: int, first_pos=None,
                          last_pos=None):
        """=221: [base, end] を明け渡したときに**消えるもの**を先に知る。

        `_overwrite_place` の「明け渡し」と同じ規則で判定するだけで、
        モデルは一切変えない(ゴーストの赤い予告に使う)。
        戻り値: (消えるパターンの index 集合, 消える点の at 集合)。
        """
        drop = set()
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            plo, phi = rec["ats"][0], rec["ats"][-1]
            if max(plo, base) < min(phi, end):
                drop.add(i)
            elif phi == base and first_pos is not None \
                    and self.pos_of(phi) != first_pos:
                drop.add(i)
            elif plo == end and last_pos is not None \
                    and self.pos_of(plo) != last_pos:
                drop.add(i)
        surv_ats = set()
        for i, rec in enumerate(self.patterns):
            if i not in drop:
                surv_ats.update(rec["ats"])
        ats = set()
        for i in drop:
            ats.update(a for a in self.patterns[i]["owned"]
                       if a not in surv_ats)
        for a, _p in self.points:
            if base <= a <= end and a not in surv_ats:
                ats.add(a)
        return drop, ats

    def place_plan_overwrite(self, plan: dict, name) -> str:
        """=221: 検証済みの plan を **上書き方式**で置く(Ctrl+V と同じ規則)。

        区間と重なる点・パターンを明け渡してから配置する。戻り値は
        paste_clip と同じ "ok" / "range" / "edge"。
        """
        if plan is None:
            return "range"
        pts = plan["points"]
        return self._overwrite_place(pts[0][0], pts[-1][0], [],
                                     [{"points": pts, "name": name}],
                                     pts[0][1])

    def place_point_over(self, at: int, pos: int) -> str:
        """=223: 数字キーの打点(上書き方式)。

        at を含む・端の禁止帯に掛かるパターンは**丸ごと削除**してから点を
        置く(ユーザー決定=「後から置くものを優先」)。同じ at の普通の点は
        pos を書き換える。戻り値 "ok" / "range" / "same"。
        """
        at = int(at)
        pos = max(0, min(self.pos_max, int(pos)))
        if at < 0:
            return "range"
        tol = self.edge_tol
        drop = set()
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            lo, hi = rec["ats"][0], rec["ats"][-1]
            if lo <= at <= hi or abs(at - lo) <= tol or abs(at - hi) <= tol:
                drop.add(i)
        if not drop and self.pos_of(at) == pos:
            return "same"               # 変化なし(UNDOを積まない)
        self._push()
        survivors = [rec for i, rec in enumerate(self.patterns)
                     if i not in drop]
        surv_ats = set()
        for rec in survivors:
            surv_ats.update(rec["ats"])
        pts = dict(self.points)
        for i in drop:
            for a in self.patterns[i]["owned"]:
                if a not in surv_ats:
                    pts.pop(a, None)
        self.patterns = survivors
        pts[at] = pos
        self._sorted_set(pts)
        self.selection = {at}
        self.pattern_selection = set()
        return "ok"

    def place_pattern(self, plan: dict, name) -> bool:
        """パターンの新規配置。拒否は False(何も起きない=仕様 10b)。"""
        if plan is None:
            return False
        ok, shared = self.check_pattern_points(plan["points"])
        if not ok:
            return False
        self._push()
        self._apply_pattern(plan, name, None, shared)
        self.selection = set()
        return True

    def pattern_neighbors(self, index: int):
        """端点を共有して隣接するパターン (left_idx|None, right_idx|None)。"""
        rec = self.patterns[index]
        if not rec["ats"]:
            return None, None
        lo, hi = rec["ats"][0], rec["ats"][-1]
        left = right = None
        for j, other in enumerate(self.patterns):
            if j == index or not other["ats"]:
                continue
            if other["ats"][-1] == lo:
                left = j
            if other["ats"][0] == hi:
                right = j
        return left, right

    @staticmethod
    def _scaled_pos(pos: float, scale) -> int:
        """=234: 中心 c を軸に f 倍した pos(0〜100 は呼び出し側で検証)。"""
        c, f = scale
        return int(round(c + (float(pos) - c) * f))

    def group_move_plan(self, dat: float, dpos: float,
                        grid_at: int, grid_pos: int, scale=None):
        """複数選択(点+パターン)の剛体移動プラン(=188)。

        **選択集合をそのまま平行移動**する。Δをグリッドへ丸め(相対配置は
        保つ=剛体)、全員が置けるときだけ (dat_q, dpos_q) を返す。1つでも
        置けない(選択外との衝突・0〜100/時間0のはみ出し・選択外パターンの
        禁止帯=183)なら None=全体が動かない。接続スナップと隣接追従
        (=182)は働かせない。選択外パターンと共有していた端点は、はがれて
        元の点が残る(=182の単独移動と同じ規則)。

        **=234: `scale=(中心, 倍率)` を渡すと、縦は平行移動ではなく
        「中心の線を軸にした拡縮」**になる(rotate系: 回転速度0=pos50 が軸)。
        点だけの選択でも、点とパターンが混ざった選択でも同じように効く。
        """
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and self.patterns[i]["ats"])
        pts_sel = set(self.selection)
        if not pats and not pts_sel:
            return None
        dat_q = round(dat / grid_at) * grid_at if grid_at > 1 \
            else int(round(dat))
        if scale is not None:
            dpos_q = 0                      # =234: 縦は拡縮で表す
        else:
            dpos_q = round(dpos / grid_pos) * grid_pos if grid_pos > 1 \
                else int(round(dpos))
        dat_q, dpos_q = int(dat_q), int(dpos_q)
        if dat_q == 0 and dpos_q == 0 and \
                (scale is None or abs(scale[1] - 1.0) < 1e-9):
            return (0, 0)
        static_pats = [rec for i, rec in enumerate(self.patterns)
                       if i not in pats and rec["ats"]]
        static_pat_ats = set()
        for rec in static_pats:
            static_pat_ats.update(rec["ats"])
        # 移動で消える元の点: 選択点 + 移動パターンの owned のうち
        # 選択外パターンに共有されていないもの(共有端・元から在った
        # 普通の点は残る=はがれる)
        removed = set(pts_sel)
        for i in pats:
            removed.update(a for a in self.patterns[i]["owned"]
                           if a not in static_pat_ats)
        static_ats = {a for a, _ in self.points if a not in removed}
        tol = self.edge_tol
        # 点の移動先の検証
        for a in pts_sel:
            p = self.pos_of(a)
            na = a + dat_q
            np_ = self._scaled_pos(p, scale) if scale is not None \
                else p + dpos_q
            if na < 0 or np_ < 0 or np_ > self.pos_max or na in static_ats:
                return None
            for rec in static_pats:
                if rec["ats"][0] - tol <= na <= rec["ats"][-1] + tol:
                    return None             # 選択外パターンの範囲・禁止帯
        # パターンの移動先の検証
        for i in pats:
            ats = self.patterns[i]["ats"]
            nlo, nhi = ats[0] + dat_q, ats[-1] + dat_q
            if nlo < 0:
                return None
            for a in ats:
                np_ = self._scaled_pos(self.pos_of(a), scale) \
                    if scale is not None else self.pos_of(a) + dpos_q
                na = a + dat_q
                if np_ < 0 or np_ > self.pos_max or na in static_ats:
                    return None
            for rec in static_pats:
                if max(nlo, rec["ats"][0]) < min(nhi, rec["ats"][-1]):
                    return None             # 範囲の重なり
            for a, _p in self.points:
                if a not in removed and nlo < a < nhi:
                    return None             # 範囲内に選択外の点
        return (dat_q, dpos_q)

    def apply_group_move(self, dat: int, dpos: int, scale=None) -> bool:
        """group_move_plan で検証済みの移動を確定する(UNDO 1ステップ)。

        =234: `scale=(中心, 倍率)` のときは縦を拡縮する。
        """
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and self.patterns[i]["ats"])
        pts_sel = set(self.selection)
        if not pats and not pts_sel:
            return False
        if dat == 0 and dpos == 0 and \
                (scale is None or abs(scale[1] - 1.0) < 1e-9):
            return True                     # クリック相当(何もしない)
        self._push()
        static_pat_ats = set()
        for i, rec in enumerate(self.patterns):
            if i not in pats:
                static_pat_ats.update(rec["ats"])
        # 移動パターンの構成点の pos を先に控える(削除前)
        member_pos = {}
        for i in pats:
            for a in self.patterns[i]["ats"]:
                member_pos[a] = self.pos_of(a)
        sel_pos = {a: self.pos_of(a) for a in pts_sel}
        pts = dict(self.points)
        for a in pts_sel:
            pts.pop(a, None)
        for i in pats:
            for a in self.patterns[i]["owned"]:
                if a not in static_pat_ats:
                    pts.pop(a, None)
        def _np(pos):
            return self._scaled_pos(pos, scale) if scale is not None \
                else pos + dpos

        new_sel = set()
        for a in pts_sel:
            pts[a + dat] = _np(sel_pos[a])
            new_sel.add(a + dat)
        for i in pats:
            rec = self.patterns[i]
            ats = []
            for a in rec["ats"]:
                na = a + dat
                ats.append(na)
                pts[na] = _np(member_pos[a])
            owned = list(ats)          # =279: 構成点はすべて自分の点
            if scale is not None:
                # =234: canonical も中心の線で f 倍する(k はそのまま。
                # shape の pos が f 倍されるので、ふくらみ d も f 倍になる)
                c, f = scale
                shape = tuple((t, c + (p - c) * f) for t, p in rec["shape"])
                b0 = c + (rec["b0"] - c) * f
                b1 = c + (rec["b1"] - c) * f
            else:
                shape = tuple((t, p + dpos) for t, p in rec["shape"])
                b0, b1 = rec["b0"] + dpos, rec["b1"] + dpos
            self.patterns[i] = {
                **rec, "shape": shape, "A": rec["A"] + dat,
                "b0": b0, "b1": b1,
                "ats": tuple(ats), "owned": tuple(owned)}
        self._sorted_set(pts)
        self.selection = new_sel
        return True

    def neighbor_follow_plans(self, index: int, plan: dict,
                              grid_at: int, grid_pos: int,
                              directional: bool = False):
        """index の新しい姿(plan)に合わせた隣接パターンの追従plan(=175)。

        隣接パターンは**隣り合う側の端を新しい (at,pos) へ**、隣り合わない
        側の端は固定のまま、両側接続として解き直す(=矩形→平行四辺形)。
        固定端は動かないので**隣の隣へは影響しない**。
        戻り値: {j: plan}(端が動いていない隣は含めない)/
        None(追従できない=縮尺・振幅・丸めの制約を満たせない)。

        directional=True(=182・中央ドラッグ=移動のみ): **離れる側の隣は
        追従しない**(接続が外れて「はがれる」)。右へ動かす=左隣を除外・
        右隣は追従(変形)/左へ動かす=鏡像。上下のみ(時間移動ゼロ)は
        従来どおり両隣が追従する。拡縮は従来どおり(directional=False)。
        """
        left, right = self.pattern_neighbors(index)
        rec = self.patterns[index]
        old_lo, old_hi = rec["ats"][0], rec["ats"][-1]
        old_qlo, old_qhi = self.pos_of(old_lo), self.pos_of(old_hi)
        nlo, qlo = plan["points"][0]
        nhi, qhi = plan["points"][-1]
        # =182: 移動方向(中心の時間シフト)。接続スナップで s が変わる
        # plan でも中心の差なら向きを取り違えない
        shift = ((nlo + nhi) - (old_lo + old_hi)) / 2.0
        if directional:
            if shift > 0:
                left = None      # 右へ移動=左隣ははがれる(追従しない)
            elif shift < 0:
                right = None     # 左へ移動=右隣ははがれる
        out = {}
        if left is not None and (nlo, qlo) != (old_lo, old_qlo):
            o = self.patterns[left]
            a_far = o["ats"][0]
            q_far = self.pos_of(a_far)
            if not a_far < nlo:
                return None
            p = plan_connect_both(o["shape"], a_far, q_far, nlo, qlo,
                                  grid_at, grid_pos, k_cap=o["k"],
                                  pos_max=self.pos_max)
            if p is None:
                return None
            out[left] = p
        if right is not None and (nhi, qhi) != (old_hi, old_qhi):
            o = self.patterns[right]
            a_far = o["ats"][-1]
            q_far = self.pos_of(a_far)
            if not nhi < a_far:
                return None
            p = plan_connect_both(o["shape"], nhi, qhi, a_far, q_far,
                                  grid_at, grid_pos, k_cap=o["k"],
                                  pos_max=self.pos_max)
            if p is None:
                return None
            out[right] = p
        return out

    def check_plans(self, plans: dict) -> bool:
        """複数パターンの同時置き換え(=175)の衝突判定。

        involved なパターンの単独所有点を除いた既存点に対して、各 plan の
        範囲の内側・端の共有(pos一致)を検証し、plan どうしは端の一致
        (接続点)以外で範囲が重ならないことを確認する。
        """
        involved = set(plans)
        others_ats = set()
        for i, p in enumerate(self.patterns):
            if i not in involved:
                others_ats.update(p["ats"])
        ignore = set()
        for i in involved:
            ignore.update(a for a in self.patterns[i]["owned"]
                          if a not in others_ats)
        existing = {a: p for a, p in self.points if a not in ignore}
        combined = {}
        ranges = []
        for _i, plan in plans.items():
            pts = plan["points"]
            lo, hi = pts[0][0], pts[-1][0]
            for a in existing:
                if lo < a < hi:
                    return False
            for a, p in (pts[0], pts[-1]):
                if a in existing and existing[a] != p:
                    return False
            for a, p in pts:
                if a in combined and combined[a] != p:
                    return False
                combined[a] = p
            ranges.append((lo, hi))
        for x in range(len(ranges)):
            for y in range(x + 1, len(ranges)):
                if max(ranges[x][0], ranges[y][0]) < \
                        min(ranges[x][1], ranges[y][1]):
                    return False
        return True

    def replace_patterns(self, plans: dict) -> bool:
        """複数パターンの同時置き換え(=175・UNDOは1ステップ)。"""
        if not plans:
            return False
        for i, plan in plans.items():
            if plan is None or not (0 <= i < len(self.patterns)):
                return False
        if not self.check_plans(plans):
            return False
        self._push()
        involved = set(plans)
        others_ats = set()
        for i, p in enumerate(self.patterns):
            if i not in involved:
                others_ats.update(p["ats"])
        pts = dict(self.points)
        for i in involved:
            for a in self.patterns[i]["owned"]:
                if a not in others_ats:
                    pts.pop(a, None)
        for i in sorted(involved):
            plan = plans[i]
            ats = []
            for a, p in plan["points"]:
                ats.append(a)
                pts[a] = p
            owned = list(ats)          # =279: 端点が重なった通常の点も取り込む
            self.patterns[i] = {
                "name": self.patterns[i]["name"],
                "shape": tuple(plan["shape"]),
                "A": plan["A"], "s": plan["s"], "k": plan["k"],
                "b0": plan["b0"], "b1": plan["b1"],
                "ats": tuple(ats), "owned": tuple(owned)}
        self._sorted_set(pts)
        return True

    def replace_pattern(self, index: int, plan: dict) -> bool:
        """移動・拡縮の確定(単独)。拒否は False(元のまま)。"""
        if plan is None:
            return False
        return self.replace_patterns({index: plan})

    def move_pattern(self, index: int, dat: int, dpos: int) -> dict | None:
        """平行移動の plan を作る(点は現在値を平行移動=再計算しない)。

        s は不変(仕様 5.4)。at<0 / pos が 0〜100 を出る / 移動先の衝突は
        None。確定は replace_pattern で行う。
        """
        if not (0 <= index < len(self.patterns)):
            return None
        rec = self.patterns[index]
        pts = []
        for a in rec["ats"]:
            p = self.pos_of(a)
            if p is None:
                return None
            na, np_ = a + dat, p + dpos
            if na < 0 or np_ < 0 or np_ > self.pos_max:
                return None
            pts.append((na, np_))
        return {"points": pts, "A": rec["A"] + dat, "s": rec["s"],
                "k": rec["k"], "b0": rec["b0"] + dpos,
                "b1": rec["b1"] + dpos, "shape": rec["shape"]}

    # ---- =241: 反転(上下・左右) ----

    def _flip_targets(self):
        """選択中の (パターンindex昇順, 点のat集合) を返す(=241)。"""
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and self.patterns[i]["ats"])
        pts = {a for a in self.selection if self.pos_of(a) is not None}
        return pats, pts

    def _static_pat_ats(self, pats) -> set:
        """選択外パターンの構成点の at 集合。"""
        out = set()
        for i, rec in enumerate(self.patterns):
            if i not in pats:
                out.update(rec["ats"])
        return out

    def flip_vertical(self, axis=None) -> str:
        """選択中の点+パターンの上下反転(=241。UNDOは1ステップ)。

        `pos' = 2c - pos`(at は変わらない)。軸 c は:
          axis=数値  … その pos の線(「上下反転(全幅)」= 50)。
          axis=None … **選択全体**の min(pos)〜max(pos) の中点
                      (「上下反転(パターン内)/(選択内)」)。
        速度(点どうしの pos 差の絶対値)は変わらない。at が動かないため
        条件は1つだけ: **選択外のパターンと共有している点は、軸の上に
        あるときだけ反転できる**(動かないので共有が保たれる)。
        パターンの canonical は invert_shape + 基準線の折り返しで表す
        (実現値がちょうど 2c - pos になる。=233 の rotate と同じ式)。

        戻り値 "ok" / "empty"(選択なし) / "shared"(軸上にない共有点) /
        "range"(0〜100 を出る。全幅・選択内とも原理上は出ない=保険)。
        """
        pats, pts = self._flip_targets()
        if not pats and not pts:
            return "empty"
        involved = set(pts)
        for i in pats:
            involved.update(self.patterns[i]["ats"])
        pos_map = dict(self.points)
        if axis is None:
            vals = [pos_map[a] for a in involved]
            axis = (min(vals) + max(vals)) / 2.0
        c2 = 2.0 * float(axis)

        def _m(p):
            return int(round(c2 - p))

        static_ats = self._static_pat_ats(pats)
        changed = False
        for a in involved:
            np_ = _m(pos_map[a])
            if np_ < 0 or np_ > self.pos_max:
                return "range"
            if np_ != pos_map[a]:
                changed = True
                if a in static_ats:
                    return "shared"
        if not changed:
            return "ok"                    # 全点が軸の上=何も起きない
        self._push()
        for a in involved:
            pos_map[a] = _m(pos_map[a])
        for i in pats:
            rec = self.patterns[i]
            # invert_shape は d を符号反転し、基準線の折り返しと合わせて
            # 実現値が 2c - pos になる(shape の値域 0〜100 も保たれる)
            self.patterns[i] = {**rec, "shape": invert_shape(rec["shape"]),
                                "b0": c2 - rec["b0"], "b1": c2 - rec["b1"]}
        self._sorted_set(pos_map)
        return "ok"

    def flip_horizontal(self) -> str:
        """選択中の点+パターンの左右反転(=241。UNDOは1ステップ)。

        軸=選択全体の at の min〜max の中心。`at' = (min+max) - at`、
        pos は変わらない(整数・csv の 100ms 単位も自動的に保たれる)。
        条件:
          (a) 選択範囲の内側(端を除く)に選択外の点・共有点が無いこと。
          (b) 端(min/max)を選択外のパターンと共有しているときは
              pos(min) == pos(max) であること(範囲の左右の中身が
              そっくり入れ替わるため)。
        戻り値 "ok" / "empty" / "inside"(範囲内に選択外) / "ends"(端の
        pos が不一致)。1点だけの選択は何も起きない("ok")。
        """
        pats, pts = self._flip_targets()
        if not pats and not pts:
            return "empty"
        involved = set(pts)
        for i in pats:
            involved.update(self.patterns[i]["ats"])
        lo, hi = min(involved), max(involved)
        if lo == hi:
            return "ok"                    # 1点だけ=何も起きない
        pos_map = dict(self.points)
        static_ats = self._static_pat_ats(pats)
        for a in pos_map:
            if a not in involved and lo < a < hi:
                return "inside"            # 範囲内に選択外の点
        for a in involved:
            if a in static_ats and lo < a < hi:
                return "inside"            # 範囲内で選択外パターンと共有
        if (lo in static_ats or hi in static_ats) and \
                pos_map[lo] != pos_map[hi]:
            return "ends"
        M = lo + hi
        self._push()
        moved = {a: pos_map[a] for a in involved}
        for a in involved:
            if a not in static_ats:        # 共有端は残す(値は下で上書き)
                pos_map.pop(a, None)
        for a, p in moved.items():
            pos_map[M - a] = p
        for i in pats:
            rec = self.patterns[i]
            shape = rec["shape"]
            length = shape_len(shape)
            nshape = tuple((length - t, p) for t, p in reversed(shape))
            self.patterns[i] = {
                **rec, "shape": nshape,
                "A": M - (rec["A"] + rec["s"] * length),
                "b0": rec["b1"], "b1": rec["b0"],
                "ats": tuple(sorted(M - a for a in rec["ats"])),
                "owned": tuple(sorted(M - a for a in rec["owned"]))}
        # =230 の作法: 端の共有点の所有者が反転で入れ替わった結果、
        # 誰も所有しない共有端が生じたら、選択外パターンへ引き継ぐ
        for a in (lo, hi):
            if a not in static_ats:
                continue
            owned = any(a in rec["owned"] for rec in self.patterns
                        if rec["ats"] and a in (rec["ats"][0],
                                                rec["ats"][-1]))
            if owned:
                continue
            for i, rec in enumerate(self.patterns):
                if rec["ats"] and a in (rec["ats"][0], rec["ats"][-1]):
                    self.patterns[i] = dict(rec)
                    self.patterns[i]["owned"] = tuple(rec["owned"]) + (a,)
                    break
        self._sorted_set(pos_map)
        self.selection = {M - a for a in pts}
        return "ok"

    def delete_pattern(self, index: int) -> bool:
        """パターンとその**単独所有の点**を削除する。

        =230: 残るパターンがまだ使っている点は消せないが、その点の
        **所有権は引き継ぐ**(そのパターンの `owned` へ入れる)。
        こうしないと、数珠つなぎで繋がったパターンを順に消したときに
        **境目の点だけが単独で取り残される**(=230 ユーザー報告の画像2)。
        =230 以降に置いたぶんは両方が持ち主なので元から取り残されないが、
        **それ以前に作ったスクリプトもこれで救える**。
        """
        if not (0 <= index < len(self.patterns)):
            return False
        self._push()
        others = set()
        for i, p in enumerate(self.patterns):
            if i != index:
                others.update(p["ats"])
        gone = {a for a in self.patterns[index]["owned"] if a not in others}
        # 残る側へ所有権を引き継ぐ(消せなかった自分の持ち点)
        keep = [a for a in self.patterns[index]["owned"] if a in others]
        if keep:
            for i, rec in enumerate(self.patterns):
                if i == index or not rec["ats"]:
                    continue
                add = [a for a in keep
                       if a in (rec["ats"][0], rec["ats"][-1])
                       and a not in rec["owned"]]
                if add:
                    self.patterns[i] = dict(rec)
                    self.patterns[i]["owned"] = tuple(rec["owned"]) + \
                        tuple(add)
        self.points = [(a, p) for a, p in self.points if a not in gone]
        del self.patterns[index]
        return True

    def ungroup_pattern(self, index: int) -> bool:
        """解除=パターンを普通の点の集まりに戻す(点は残す。仕様 6b)。"""
        if not (0 <= index < len(self.patterns)):
            return False
        self._push()
        del self.patterns[index]
        return True

    def copy_pattern(self, index: int) -> bool:
        """パターン1つのコピー(=180→=185で混在クリップボードへ統合)。

        pattern_selection をそのパターンだけにして copy_selected を呼ぶ
        (Ctrl+C の単独パターンコピーと同じ結果になる)。
        """
        if not (0 <= index < len(self.patterns)):
            return False
        if len(self.patterns[index]["ats"]) < 2:
            return False
        self.pattern_selection = {index}
        self.selection = set()
        return self.copy_selected()

    def most_future_pattern(self) -> int | None:
        """右端(最大at)が最も未来のパターンの index。"""
        best = None
        for i, p in enumerate(self.patterns):
            if p["ats"] and (best is None or
                             p["ats"][-1] >
                             self.patterns[best]["ats"][-1]):
                best = i
        return best

    def _selected_pattern_indices(self) -> list:
        return sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns))

    def group_candidate_ats(self) -> set:
        """=278: グループ化の対象になる点の at 集合
        (選択中の点+選択中のパターンの構成点)。"""
        out = set(self.selection)
        for i in self._selected_pattern_indices():
            out.update(self.patterns[i]["ats"])
        return out

    def ungroup_selected(self) -> bool:
        """=278: 選択中のパターンをすべて解除する(点は残り、選択中の点に
        加わる=そのままグループ化し直せる)。1段のUNDO。"""
        idxs = self._selected_pattern_indices()
        if not idxs:
            return False
        self._push()
        freed = set()
        for i in reversed(idxs):
            freed.update(self.patterns[i]["ats"])
            del self.patterns[i]
        self.pattern_selection = set()
        self.selection = set(self.selection) | freed
        return True

    def group_selection(self) -> str:
        """グループ化(仕様 5.4)。戻り値 "ok"/"too_few"/"has_pattern"/
        "unselected_in_range"。範囲=選択の最小 at〜最大 at。

        =278: 選択にパターンが含まれていれば、それらを**いったん解除して
        構成点を選択に含め**、全体を1つのパターンにする(1段のUNDO)。
        選択していないパターンと端点を共有していてもよい(その端点は
        新パターンの owned に入れない=共有端点◎のまま)。範囲の内側に
        選択していないパターンの点があれば従来どおり拒否。
        """
        pat_sel = self._selected_pattern_indices()
        sel_ats = self.group_candidate_ats()
        if len(sel_ats) < 2:
            return "too_few"
        lo, hi = min(sel_ats), max(sel_ats)
        other = set()
        for i, p in enumerate(self.patterns):
            if i not in pat_sel:
                other.update(p["ats"])
        for a, _p in self.points:
            if lo <= a <= hi and a in other and a not in sel_ats:
                return "has_pattern"
            if lo < a < hi and a in other:
                return "has_pattern"     # 内側で他パターンと重なる
            if lo <= a <= hi and a not in sel_ats:
                return "unselected_in_range"
        sel = sorted((a, self.pos_of(a)) for a in sel_ats)
        shape = tuple((a - lo, p) for a, p in sel)
        if shape_len(shape) <= 0:
            return "too_few"
        self._push()
        for i in reversed(pat_sel):
            del self.patterns[i]
        self.patterns.append({
            "name": None, "shape": shape, "A": float(lo), "s": 1.0,
            "k": 1.0, "b0": float(shape[0][1]), "b1": float(shape[-1][1]),
            "ats": tuple(a for a, _ in sel),
            "owned": tuple(a for a, _ in sel)})   # =279: 全点を自分の点に
        self.selection = set()
        self.pattern_selection = set()
        return "ok"

    # ---- クリップボード(=185: 点+パターンの混在・上書き貼り付け) ----

    def copy_selected(self) -> bool:
        """選択(点+パターン)を先頭 at を 0 とした相対配置で格納する。

        =185: 点だけでなく pattern_selection のパターンも一緒にコピー
        できる。パターンは**今の形**(移動・拡縮・反転後の現在の点列)を
        そのまま持つ(=179のコピーと同じ考え方)。
        """
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and len(self.patterns[i]["ats"]) >= 2)
        if not self.selection and not pats:
            return False
        bases = [a for a in self.selection]
        bases += [self.patterns[i]["ats"][0] for i in pats]
        base = min(bases)
        sel = sorted((a, p) for a, p in self.points if a in self.selection)
        self.clipboard = [(a - base, p) for a, p in sel]
        plist = []
        for i in pats:
            ats = self.patterns[i]["ats"]
            lo = ats[0]
            plist.append({
                "rel": lo - base,
                "shape": tuple((a - lo, self.pos_of(a)) for a in ats),
                "name": self.patterns[i]["name"]})
        self.clipboard_patterns = plist
        if self.link is not None:          # =298: 左右で共有
            self.link.share_clipboard(self)
        return True

    def cut_selected(self) -> bool:
        """切り取り=コピー+削除。貼り付けできなくても切り取りは成立する。"""
        if not self.copy_selected():
            return False
        return self.delete_selected_any()

    def has_clipboard(self) -> bool:
        return bool(self.clipboard) or bool(self.clipboard_patterns)

    def _clip_len(self) -> int:
        """クリップボードの全長(先頭 rel=0〜最後の要素の末尾)。"""
        end = 0
        for rel, _p in (self.clipboard or ()):
            end = max(end, rel)
        for pc in self.clipboard_patterns:
            end = max(end, int(round(pc["rel"] + pc["shape"][-1][0])))
        return end

    def clip_first_pos(self):
        """クリップボードの先頭(rel=0)の要素の pos(高さ合わせの基準)。"""
        for rel, p in (self.clipboard or ()):
            if rel == 0:
                return p
        for pc in self.clipboard_patterns:
            if pc["rel"] == 0:
                return pc["shape"][0][1]
        return None

    def paste_clip(self, base_at: float, voff: int = 0) -> str:
        """上書き貼り付け(=185)。base_at にクリップボードの先頭が載る。

        貼り付け内容の先頭〜末尾の**閉区間を明け渡す**方式(Q3=a):
        区間内にある既存の通常の点は削除し、区間と(真に)重なる既存
        パターンは**丸ごと**削除してから、点とパターンを配置する。
        端でぴったり接するパターンは残り、接する端点の pos が一致すれば
        端点を共有する(=接続)。pos が食い違うときはそのパターンも削除
        する(貼り付けるものを優先)。voff は縦の平行移動(パターンを
        含む貼り付けでアンカーの高さへ合わせる=180の踏襲)。

        戻り値: "ok" / "no_clipboard" / "range"(0〜100やat<0をはみ出す)
        / "edge"(残るパターンの端点の禁止帯=183に掛かる)。
        UNDO は1ステップ。成功時は貼った点が selection、貼ったパターンが
        pattern_selection になる(続けて Ctrl+V で数珠つなぎにできる)。
        """
        if not self.has_clipboard():
            return "no_clipboard"
        base = int(round(base_at))
        if base < 0:
            return "range"
        # 貼り付ける中身(絶対座標)を作る
        new_pts = []
        for rel, p in (self.clipboard or ()):
            np_ = p + voff
            if np_ < 0 or np_ > self.pos_max:
                return "range"
            new_pts.append((base + rel, np_))
        new_pats = []
        for pc in self.clipboard_patterns:
            lo = base + int(round(pc["rel"]))
            pts = []
            for t, p in pc["shape"]:
                np_ = int(round(p + voff))
                if np_ < 0 or np_ > self.pos_max:
                    return "range"
                pts.append((lo + int(round(t)), np_))
            new_pats.append({"points": pts, "name": pc["name"]})
        end = base + self._clip_len()
        first_pos = self.clip_first_pos()
        first_pos = first_pos + voff if first_pos is not None else None
        return self._overwrite_place(base, end, new_pts, new_pats, first_pos)

    def place_pattern_over(self, shape, at0: float, base_pos: float | None,
                           grid_at: int, grid_pos: int, name,
                           scale: float = 1.0) -> str:
        """=213: リアルタイム配置(Fキー)。at0 を**左端**にして shape を
        Ctrl+V と同じ上書き規則(_overwrite_place)で置く。クリップボードは
        汚さない。選択状態も変えない(再生中の操作を邪魔しない)。

        base_pos: 標準パターン=50(端の高さを 50 へ。0〜100 に収まるよう
        クランプ=通常配置 =177 と同じ規則)/ None=形のまま(ユーザー
        パターン)。scale=時間軸の縮尺(=194)。
        戻り値は paste_clip と同じ("ok"/"range"/"edge")+"grid"(配置不能)。
        """
        # =222: 失敗した回の値が残らないよう、毎回まず消す(数珠つなぎは
        # 「置けた区間」だけを次の起点にする)
        self.last_place_range = None
        if not shape or len(shape) < 2:
            return "grid"
        shape = scale_shape(shape, self.pos_max)   # =297: 0〜100 定義→分解能
        if abs(scale - 1.0) > 1e-9:
            shape = tuple((t * scale, p) for t, p in shape)
        p0 = shape[0][1]
        plast = shape[-1][1]
        if base_pos is None:
            off = 0
        else:
            pmin = min(p for _t, p in shape)
            pmax = max(p for _t, p in shape)
            target = max(0, min(self.pos_max, base_pos))
            off = max(-pmin, min(self.pos_max - pmax, target - p0))
        A = snap(max(0.0, at0), grid_at)
        plan = plan_normal(shape, A, grid_at, grid_pos,
                           b0=p0 + off, b1=plast + off,
                           round_interior=self.round_interior,
                           pos_max=self.pos_max)
        if plan is None:
            return "grid"
        pts = plan["points"]
        sel, psel = set(self.selection), set(self.pattern_selection)
        r = self._overwrite_place(pts[0][0], pts[-1][0], [],
                                  [{"points": pts, "name": name}],
                                  pts[0][1])
        if r == "ok":
            # 配置前の選択を保つ(消えた点・パターンは除く)
            pat = self.pattern_ats()
            self.selection = {a for a in sel
                              if a not in pat and self.pos_of(a) is not None}
            self.pattern_selection = set()
            # =222: 置いた区間を控える(Fキー長押しの数珠つなぎが、この
            # **終端**を次の起点にする)
            self.last_place_range = (pts[0][0], pts[-1][0])
        return r

    def _overwrite_place(self, base: int, end: int, new_pts, new_pats,
                         first_pos) -> str:
        """上書き配置の共通部(=185 の paste_clip から =213 で抽出)。
        [base, end] の閉区間を明け渡して点とパターンを置く。UNDO 1ステップ。
        成功時は置いた点が selection・パターンが pattern_selection。"""
        # 明け渡し: 区間と真に重なるパターンは丸ごと削除。端で接する
        # パターンは、接する端点の pos が貼り付け側と一致すれば残す
        last_pos = None
        for a, p in new_pts:
            if a == end:
                last_pos = p
        for rec in new_pats:
            if rec["points"][-1][0] == end:
                last_pos = rec["points"][-1][1]
            if rec["points"][0][0] == base:
                first_pos = rec["points"][0][1]
        drop = set()
        # =226(離散): **隣り合わせを許容し、境目の pos は「未来側の
        # パターンの始端」を採用する**(ユーザー決定)。階段では前の
        # パターンの最後の点は見た目に影響しないため、前のパターンは
        # 消さずに残し、境目の点だけ譲る。
        yield_at = None      # 前のパターンから明け渡してもらう境目の at
        keep_pos = {}        # 未来側のパターンの始端=書き換えない点
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            plo, phi = rec["ats"][0], rec["ats"][-1]
            if max(plo, base) < min(phi, end):
                drop.add(i)                     # 真の重なり=丸ごと削除
            elif phi == base and first_pos is not None \
                    and self.pos_of(phi) != first_pos:
                if self.step_mode:
                    yield_at = base             # 境目は新しい側の値になる
                else:
                    drop.add(i)                 # 接する端の pos 不一致
            elif plo == end and last_pos is not None \
                    and self.pos_of(plo) != last_pos:
                if self.step_mode:
                    keep_pos[end] = self.pos_of(plo)   # 未来側が勝つ
                else:
                    drop.add(i)
        survivors = [rec for i, rec in enumerate(self.patterns)
                     if i not in drop]
        surv_ats = set()
        for rec in survivors:
            surv_ats.update(rec["ats"])
        if yield_at is not None:
            # 境目の点の**値**は新しいパターンのものにする(階段では前の
            # パターンの最後の点は見た目に影響しないため)。いったん外して
            # 置き直させる。
            # **=235: 所有権は前のパターンにも残す**(=230 と同じ「両方が
            # 持ち主」)。手放させると `shared_endpoints()` が拾って
            # ◎(共有端点)で描かれ、**数珠つなぎの境目に打点が残る**
            # ように見えていた(ユーザー報告)。
            surv_ats.discard(yield_at)
        # 残るパターンの端点の禁止帯(=183)に掛かる通常の点は拒否
        # (端点そのものへの合流=共有は許す)
        tol = self.edge_tol
        for a, _p in new_pts:
            for rec in survivors:
                if not rec["ats"]:
                    continue
                for ep in (rec["ats"][0], rec["ats"][-1]):
                    if a != ep and abs(a - ep) <= tol:
                        return "edge"
        self._push()
        # 削除: 落とすパターンの単独所有点(残る側に共有されていないもの)
        pts = dict(self.points)
        for i in drop:
            for a in self.patterns[i]["owned"]:
                if a not in surv_ats:
                    pts.pop(a, None)
        self.patterns = survivors
        # 削除: 閉区間内の通常の点(残るパターンの構成点は残す)
        for a in list(pts):
            if base <= a <= end and a not in surv_ats:
                del pts[a]
        # 配置: 点 → パターン(端点の共有=すでにある点は owned に含めない)
        for a, p in new_pts:
            pts[a] = p
        # =230: **パターン同士の境目は「共有点」にしない**(ユーザー要望)。
        # 数珠つなぎ(Fキー長押し)や接続配置で、新しいパターンの端点が
        # **別のパターンの端点**にちょうど重なるときは、新しい側も
        # その点を**自分のもの**として持つ。結果:
        #   ・◎(二重丸=共有端点)にならず、普通のパターンの点として描かれる
        #   ・両方のパターンを消せば、その点も一緒に消える(取り残しが出ない)
        # 波形(点の位置・値)は一切変わらない。**通常の点(ユーザーが打った
        # 点)へ繋いだときは従来どおり共有=◎**(=184 の意味は残す)。
        sel_pats = set()
        for rec in new_pats:
            ats = []
            for a, p in rec["points"]:
                ats.append(a)
                pts[a] = p
            owned = list(ats)   # =279: 通常の点へ繋いだ端点も自分の点にする
            lo = ats[0]
            shape = tuple((a - lo, p) for a, p in rec["points"])
            self.patterns.append({
                "name": rec["name"], "shape": shape, "A": float(lo),
                "s": 1.0, "k": 1.0, "b0": float(rec["points"][0][1]),
                "b1": float(rec["points"][-1][1]),
                "ats": tuple(ats), "owned": tuple(owned)})
            sel_pats.add(len(self.patterns) - 1)
        # =226(離散): 未来側のパターンの始端に重なった境目は、その値を戻す
        for a, p in keep_pos.items():
            if a in pts:
                pts[a] = p
        self._sorted_set(pts)
        pat = self.pattern_ats()
        self.selection = {a for a, _ in new_pts if a not in pat}
        self.pattern_selection = sel_pats
        return "ok"

    def paste_over_anchor(self) -> str:
        """Ctrl+V の貼り付け(点のみのクリップボード=172の踏襲)。

        基準=**選択中で最も at が大きい点(アンカー)**。クリップボードの
        先頭(rel=0)がアンカーの at にちょうど載る(グリッド吸着はしない)。
        =185で上書き方式になり、アンカーだけでなく**貼り付け区間の既存の
        点はすべて上書き**される。pos は絶対値のまま(縦は動かさない)。

        戻り値: "ok" / "no_clipboard" / "no_selection" / "range" / "edge"
        """
        if not self.has_clipboard():
            return "no_clipboard"
        if not self.selection:
            return "no_selection"
        return self.paste_clip(float(max(self.selection)), 0)

    def delete_selected_any(self) -> bool:
        """選択中の点+パターンをまとめて削除する(=185・UNDOは1ステップ)。

        パターンは単独所有の点ごと消える(delete_pattern と同じ規則)。
        """
        pats = sorted((i for i in self.pattern_selection
                       if 0 <= i < len(self.patterns)), reverse=True)
        if not pats and not self.selection:
            return False
        self._push()
        keep_ats = set()
        for i, rec in enumerate(self.patterns):
            if i not in pats:
                keep_ats.update(rec["ats"])
        drop_ats = set(self.selection)
        for i in pats:
            drop_ats.update(a for a in self.patterns[i]["owned"]
                            if a not in keep_ats)
        self.points = [(a, p) for a, p in self.points
                       if a not in drop_ats]
        for i in pats:
            del self.patterns[i]
        self.selection = set()
        self.pattern_selection = set()
        return True


# ---------------------------------------------------------------------------
# funscript の読み書き(仕様 14c)
# ---------------------------------------------------------------------------

def normalize_user_shape(points):
    """ユーザーパターンの点列 [(at,pos)] を shape へ正規化する(=205)。

    先頭の at を 0 へずらす(基準長=最終 at・仕様 6.1)。2点未満・
    幅0は None(登録できない)。
    """
    pts = sorted({int(a): max(0, min(100, int(p)))
                  for a, p in points or ()}.items())
    if len(pts) < 2:
        return None
    t0 = pts[0][0]
    shape = tuple((a - t0, p) for a, p in pts)
    if shape[-1][0] <= 0:
        return None
    return shape


def _valid_user_shape(v):
    try:
        shape = tuple((int(t), int(p)) for t, p in v)
    except (TypeError, ValueError):
        return None
    if len(shape) < 2 or shape[0][0] != 0 or shape[-1][0] <= 0:
        return None
    last = -1
    for t, p in shape:
        if t <= last or not (0 <= p <= 100):
            return None
        last = t
    return shape


def load_user_patterns(cfg: dict, kind: str | None = None) -> dict:
    """config から {"L1": shape, ...} を読む(=205 / =231 種別ごと)。

    kind を渡すとその種別の20枠だけ、None なら全種別ぶんを返す。
    不正な枠は無視。**旧 "user_patterns" は読まない**(=231)。
    """
    out = {}
    raw = (cfg.get("script_edit") or {}).get(USER_PAT_CFG_KEY) or {}
    if not isinstance(raw, dict):
        return out
    keys = user_pattern_keys(kind) if kind else ALL_USER_PATTERN_KEYS
    for key in keys:
        shape = _valid_user_shape(raw.get(key) or ())
        if shape is not None:
            out[key] = shape
    return out


def save_user_pattern(cfg: dict, key: str, shape) -> dict:
    """config 辞書へユーザーパターンを書き込む(=205。保存は呼び出し側)。"""
    se = cfg.setdefault("script_edit", {})
    if not isinstance(se, dict):
        se = cfg["script_edit"] = {}
    up = se.setdefault(USER_PAT_CFG_KEY, {})
    if not isinstance(up, dict):
        up = se[USER_PAT_CFG_KEY] = {}
    up[key] = [[int(t), int(p)] for t, p in shape]
    return cfg


def first_free_slot(patterns: dict, kind: str) -> int:
    """=231: 未登録のうち一番小さい枠番号(全部埋まっていれば 20)。"""
    for i, key in enumerate(user_pattern_keys(kind), start=1):
        if key not in patterns:
            return i
    return USER_PAT_SLOTS


# ---- =212: Fキー割り当て(config "script_edit.fkeys") ----
# 参照 ref = ("std", index) | ("user", slot)。1キー=1パターン・
# 1パターン=1キー(排他)。
# **=231: ユーザーパターンは「枠番号(1〜20)」で覚える**(ユーザー決定)。
# 種別を切り替えると、その種別の同じ番号の枠を指す(標準パターンが
# カタログごとに別物なのと同じ考え方=226)。

def _valid_fkey_ref(v, n_std: int):
    if not isinstance(v, dict):
        return None
    kind = v.get("kind")
    if kind == "std":
        try:
            idx = int(v.get("index"))
        except (TypeError, ValueError):
            return None
        return ("std", idx) if 0 <= idx < n_std else None
    if kind == "user":
        slot = v.get("slot")
        if slot is None:
            # 旧形式 {"kind":"user","key":"U3"} は枠番号へ読み替える(=231)
            key = str(v.get("key") or "")
            slot = key[1:] if key[:1].isalpha() else key
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return None
        return ("user", slot) if 1 <= slot <= USER_PAT_SLOTS else None
    return None


def load_fkey_map(cfg: dict) -> dict:
    """config から {"F1": ref, ...} を読む(=212)。不正なキー・参照は無視。
    同じ参照が複数キーにあれば若いキーだけ残す(排他の保険)。"""
    out = {}
    raw = (cfg.get("script_edit") or {}).get("fkeys") or {}
    if not isinstance(raw, dict):
        return out
    seen = set()
    for fk in FKEYS:
        ref = _valid_fkey_ref(raw.get(fk), len(STD_PATTERNS))
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        out[fk] = ref
    return out


def save_fkey_map(cfg: dict, fkey: str, ref) -> dict:
    """config 辞書へ割り当てを書く(=212。保存は呼び出し側)。
    ref=None は解除。排他: 同じ ref を持つ他のキーは外す(=移動)。"""
    se = cfg.setdefault("script_edit", {})
    if not isinstance(se, dict):
        se = cfg["script_edit"] = {}
    cur = load_fkey_map(cfg)
    if fkey not in FKEYS:
        return cfg
    if ref is None:
        cur.pop(fkey, None)
    else:
        for k in [k for k, r in cur.items() if r == ref]:
            del cur[k]
        cur[fkey] = tuple(ref)
    se["fkeys"] = {k: ({"kind": "std", "index": int(r[1])} if r[0] == "std"
                       else {"kind": "user", "slot": int(r[1])})
                   for k, r in cur.items()}
    return cfg


def fkey_of(fmap: dict, ref):
    """ref に割り当てられている F キー名(無ければ None)。"""
    for k, r in fmap.items():
        if tuple(r) == tuple(ref):
            return k
    return None


def load_funscript_raw(path: str) -> tuple[dict, list[tuple[int, int]]]:
    """funscript を**dictごと**読み込む(actions 以外のキーを保存で保持する
    ため)。読み込みは従来どおり utf-8-sig。戻り値 (rawdict, [(at,pos)])。
    壊れた JSON・dictでない JSON は ValueError。
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("funscript is not a JSON object")
    pts = {}
    for a in data.get("actions") or []:
        try:
            at, pos = int(a["at"]), int(a["pos"])
        except (KeyError, TypeError, ValueError):
            continue
        if at >= 0:
            pts[at] = max(0, min(100, pos))
    return data, sorted(pts.items())


def dump_funscript(points, extra: dict | None, rvp: dict | None = None) -> str:
    """書き出し用の JSON 文字列を作る。

    - `actions` 以外のキー(metadata 等)は**元の順序のまま保持**する
    - at = 整数ms・pos = 整数0〜100・at 昇順
    - ensure_ascii=False(UTF-8。BOM は書き込み側で付けない)
    - =204: `rvp` はパターン記憶(仕様 6.2)。**現在の編集状態が真実**なので
      元の "rvp" キーは常に置き換える(None なら削除=パターンなし)。
      位置は元のキー順を保ち、無ければ末尾に足す。
    """
    actions = [{"at": int(a), "pos": max(0, min(100, int(p)))}
               for a, p in sorted(points)]
    out = {}
    placed = False
    placed_rvp = False
    for k, v in (extra or {}).items():
        if k == "actions":
            out[k] = actions
            placed = True
        elif k == RVP_KEY:
            if rvp is not None:
                out[k] = rvp
                placed_rvp = True
        else:
            out[k] = v
    if not placed:
        out["actions"] = actions
    if rvp is not None and not placed_rvp:
        out[RVP_KEY] = rvp
    return json.dumps(out, ensure_ascii=False)


def write_funscript(path: str, points, extra: dict | None,
                    rvp: dict | None = None) -> None:
    """tmp へ書いてから os.replace(ScenarioEditor._do_save と同じ作法)。

    UTF-8(BOMなし)。OSError は呼び出し側が握って画面へ出す。
    """
    payload = dump_funscript(points, extra, rvp)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# =224: CSV(ROTATE 用)の読み書き
# ---------------------------------------------------------------------------
#
# csv は **階段(次の行までその値を保つ)** のタイムラインで、時刻は
# **100ms単位**の整数、値は「方向(0=逆/1=正)+速度(0〜100)」。
# 編集モデルは funscript と同じ (at, pos) だが、**=297: csv は分解能 200**
# (ScriptEditModel.pos_max=200)で扱う: **pos 100=停止 / 200=正回転(方向1)
# 最大 / 0=逆回転(方向0)最大**。pos が 1 違うと速度が 1 違う(=224 の
# 0〜100 写像では速度が 2 刻みになり、5 単位以下の調整ができなかった)。
# レビュー画面の再生グラフ(player._graph_points_rotate)は表示専用なので
# 従来の 0〜100 写像のまま。

CSV_AT_UNIT = 100          # csv の時刻の単位(ms)
CSV_POS_MAX = 200          # =297: csv 編集モデルの pos 上限(分解能)
CSV_STOP_POS = 100         # 停止の pos(=中央)


def csv_pos_of(cw: bool, frac: float) -> int:
    """(方向, 速度率0.0〜1.0) → pos 0〜200(=297)。"""
    f = max(0.0, min(1.0, float(frac)))
    v = CSV_STOP_POS + f * 100.0 if cw else CSV_STOP_POS - f * 100.0
    return int(round(v))


def csv_val_of(pos: int) -> tuple[int, int]:
    """pos 0〜200 → (方向 0/1, 速度 0〜100)。停止(pos=100)の方向は 1。"""
    d = int(max(0, min(CSV_POS_MAX, int(pos)))) - CSV_STOP_POS
    return (1 if d >= 0 else 0, int(abs(d)))


def csv_hold_pos(points, at: int) -> int:
    """階段の保持値: at の時点で有効な pos(最初の行の前は停止)。"""
    cur = CSV_STOP_POS
    for a, p in points:
        if a > at:
            break
        cur = p
    return cur


def load_csv_points(path: str, ch: int = 0):
    """csv を読み、(列数(3/5), 指定チャンネルの点列, 全チャンネルの点列) を返す。

    列数・数値が不正なファイルは rotate_source と同じ ValueError。
    """
    from .rotate_source import load_csv
    tl = load_csv(path)
    cols = 5 if tl.channels >= 2 else 3
    all_pts = []
    for i in range(tl.channels):
        all_pts.append([(int(st.at), csv_pos_of(*st.vals[i]))
                        for st in tl.steps])
    if not all_pts:                       # 空ファイル
        all_pts = [[]]
        cols = 3
    idx = ch if 0 <= ch < len(all_pts) else 0
    return cols, list(all_pts[idx]), all_pts


def dump_csv(points, cols: int = 3, other=None, ch: int = 0) -> str:
    """csv の本文を作る。

    - `points` = 編集したチャンネルの (at, pos)。at は 100ms へ丸める
      (同じ時刻になったら後ろ勝ち)。
    - 5列(2ch)のときは `other`(もう一方のチャンネルの (at,pos))の行も
      残し、**各行に両チャンネルの「その時点の保持値」を書く**
      (csv は1行で両方を指定する形式のため)。
    - `ch` = 編集したチャンネルの位置(0=左/1=右)。
    """
    mine = {}
    for a, p in points:
        mine[int(round(a / CSV_AT_UNIT)) * CSV_AT_UNIT] = \
            max(0, min(CSV_POS_MAX, int(p)))
    mine = sorted(mine.items())
    if cols == 3 or other is None:
        rows = [(a, csv_val_of(p)) for a, p in mine]
        return "".join("{0},{1},{2}\n".format(a // CSV_AT_UNIT, d, s)
                       for a, (d, s) in rows)
    oth = {}
    for a, p in other:
        oth[int(round(a / CSV_AT_UNIT)) * CSV_AT_UNIT] = \
            max(0, min(CSV_POS_MAX, int(p)))
    oth = sorted(oth.items())
    ats = sorted({a for a, _p in mine} | {a for a, _p in oth})
    out = []
    for a in ats:
        pm = csv_hold_pos(mine, a)
        po = csv_hold_pos(oth, a)
        vals = [csv_val_of(pm), csv_val_of(po)]
        if ch == 1:
            vals.reverse()
        out.append("{0},{1},{2},{3},{4}\n".format(
            a // CSV_AT_UNIT, vals[0][0], vals[0][1],
            vals[1][0], vals[1][1]))
    return "".join(out)


def write_csv(path: str, points, cols: int = 3, other=None,
              ch: int = 0) -> None:
    """tmp へ書いてから os.replace(write_funscript と同じ作法)。"""
    payload = dump_csv(points, cols, other, ch)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# 編集用グラフ
# ---------------------------------------------------------------------------

class ScriptEditGraph(tk.Canvas):
    """funscript 編集用のグラフ(1トラックだけを大きく表示)。

    操作の割り当て(仕様 4.3):
      左クリック(空欄)=打点 / 左クリック(点)=単独選択 /
      Ctrl+左クリック=選択に追加・解除 / 左ドラッグ(点)=移動 /
      左ドラッグ(空欄)=矩形選択 / 右ドラッグ=左右パン /
      右クリック(静止)=メニュー / ホイール=時間の拡大縮小 /
      Delete=削除 / Ctrl+C/X/V=コピー/切取/貼付 / Ctrl+Z/Y=UNDO/REDO

    縮尺段階(LEVELS)・クリック判定(CLICK_PX)・色は main.DeviceGraph から
    借りる(遅延import。tk.PhotoImage 等のモジュールレベルキャッシュは
    持たない=2つ目の Tk() を作るテストで TclError になるため)。
    """

    GUTTER = 30
    AXIS_H = 30   # =195: ヒートマップの帯(pos=0の下)+時間ラベルのぶん
                  # (=247: 帯を点と重ならない位置へ下げたぶん+4px)
    TOP_PAD = 10
    SCALE_H = 20  # =210: 縮尺表示の帯(左上・pos「100」ラベルの上)
    HIT_PX = 6            # 点のヒット判定の半径(px)
    # view_ms(追従時=再生位置)を置く水平位置(=176)。0.5=中央 →
    # 0.12=左寄り(初期表示で0秒が左端近くに来る。再生タブのマイナス表示の
    # アンカーと同じ考え方で、左に少し余裕を残す)
    VIEW_ANCHOR = 0.12
    # =184: パターンの線と構成点の色(青の波形と見分けやすい緑。
    # ライト/ダーク)。ゴーストの緑破線とは線種(実線/破線)で区別する
    PATTERN_GREEN = ("#149a43", "#3ddc84")
    # =195: ヒートマップ(速い順)。しきい値は speed=|Δpos|÷Δt(ms)×60。
    # =247: 紫は「警告色として目立たない」ため廃止し、色を1段ずつスライド。
    # また青より弱く感じる水色を最弱側へ移した(いずれもユーザー指定)。
    # (下限(含む), 色)。24以上=赤(最高段階) / 20-24=オレンジ /
    # 16-20=黄 / 12-16=緑 / 9-12=青 / 6-9=水色 / 0超-6=水色。
    # 速度0は無色(=None)。TFGのしきい値(▲危険=20/△注意=16)は不変。
    HEAT_LEVELS = (
        (24.0, ("#df1b1b", "#ff5b5b")),     # 赤
        (20.0, ("#e87c00", "#ffa040")),     # オレンジ
        (16.0, ("#cfae00", "#e6d34a")),     # 黄
        (12.0, ("#1c9e4d", "#3ddc84")),     # 緑
        (9.0, ("#2b62d9", "#6ba3ff")),      # 青
        (6.0, ("#0999b8", "#3fd2ee")),      # 水色
        (0.0, ("#0999b8", "#3fd2ee")),      # 水色(0超〜6未満)
    )

    def _heat_color(self, a0, p0, a1, p1):
        """区間 (a0,p0)→(a1,p1) のヒートマップ色(=195)。無色は None。"""
        dt = a1 - a0
        dp = abs(p1 - p0)
        if dt <= 0 or dp == 0:
            return None                     # 速度0(水平)は無色
        speed = (dp / dt) * 60.0
        for lo, col in self.HEAT_LEVELS:
            if speed >= lo and speed > 0:
                return col
        return None

    def __init__(self, master, model: ScriptEditModel,
                 on_change=None, on_select=None, on_menu=None, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, **kwargs)
        from .main import DeviceGraph as _DG    # 遅延import(循環回避)
        self._DG = _DG
        self.LEVELS = _DG.LEVELS
        self.CLICK_PX = _DG.CLICK_PX
        self.model = model
        self.on_change = on_change      # 編集が確定した(dirty・全長の更新)
        self.on_select = on_select      # 選択が変わった((at,pos)欄の更新)
        self.on_menu = on_menu          # 右クリック(静止)= メニューを出す
        self.on_paste_reject = None     # Ctrl+V が重なりで拒否された(=171)
        self.on_seek = None             # 右ダブルクリック=再生位置(=192)
        self.place_scale = 1.0          # =194: 配置時の縮尺(x2〜x0.5)

        self.level = _DG.DEFAULT_LEVEL
        self.follow = True              # 再生位置へ追従(パンで解除)
        self.playing = False            # =198: 再生中は追従アンカー=中央
        self._played = False            # =201: 一度再生したら停止中も中央
        self.plain = False              # =206: 再生位置の線・追従バッジを
        #                                 描かない(ユーザーパターン編集用)
        self.view_ms = 0.0              # 画面中央の時刻(ms)
        self.now_ms = 0.0               # 再生位置
        self.grid_at = GRID_AT_DEFAULT  # property(=183: edge_tol を同期)
        self.grid_pos = GRID_POS_DEFAULT
        self.tool = "point"             # "point" / "pattern"(P2 =173)
        # P2(=173): パターン配置ツールの状態
        self.place_shape = None         # 配置する正規化点列(インバート適用後)
        self.place_name = None          # パターン名(i18nキー)
        self.sel_pattern = None         # 選択中の配置済みパターンの index
        self._ghost = None              # 配置ゴーストの plan(仕様 4-2)
        self._point_ghost = None        # 点モードのゴースト (at, pos)(=176)
        # =233: マウス位置の十字ガイド (at, pos|None)。pos=None は
        # 「縦線だけ」= 仲間のグラフ(サブ・左右のもう片方)へ映したもの。
        self.cross = None
        # =243: 音声波形(背景の帯)。wave_env は編集画面が渡す
        # {"bucket_ms", "chans"(ch別ピーク列), "mono"(合成), "peak"}。
        # wave_mode: "off" / "mono" / "stereo" / "stereo_rev"。
        # wave_channel: None=1本のグラフ(ステレオは上下半分に分割) /
        #   0 or 1=UFOTW の左右2本(そのchを全高で描く。rev で入れ替え)。
        # wave_offset: グラフの 0ms が指す素材時刻(編集モードは常に 0)。
        self.wave_env = None
        self.wave_mode = "off"
        self.wave_channel = None
        self.wave_offset = 0.0
        # =233: 縦軸の表記。"speed" は csv の回転速度(下から -100 / 0 / 100)
        self.pos_axis = "pos"
        self.on_placed = None           # パターン配置成功の通知(=179)
        # 区間の目印(仕様 4.2)。編集モードは区間を適用しないが、指定が
        # あるアイテムでは開始・終了の位置へ縦の破線を描く。
        self.region = (0.0, None, False)     # (lo_ms, hi_ms|None, 指定あり)

        # =224: csv(ROTATE)の編集モード。
        #  step  : 階段で描く(次の点まで同じ値・最初の点の前は停止=50)
        #  heat  : ヒートマップ(linear のストローク速度の警告)を出すか
        #  round_interior: パターン内側の点もグリッドへ丸める
        #          (csv は時刻が 100ms 単位なので、内側も格子へ乗せる)
        self.step = False
        self.heat = True
        self.round_interior = False
        # =226: 離散的なスクリプト(csv・rotate系・vibration)のパターン規則。
        #  pat_center : None=従来(クリックした高さへ縦にずらせる) /
        #               50=ROTATE(停止が中心) / 0=VIBRATION(停止が下端)。
        #               None でないときは **定義どおりの高さで固定**して置き、
        #               縦の拡縮は中心を軸に対称、縦移動は禁止。
        #  pat_follow : 隣接パターンの追従(=175/=182)。離散では False。
        #  pat_connect: 接続配置(端点の共有)。離散では False。
        self.pat_center = None
        self.pat_follow = True
        self.pat_connect = True
        # =227: サブ表示(参考用の別トラック)。
        #  readonly : 入力を一切受けない(見るだけ)
        #  sub_bg   : 背景を分ける(ライト/カラーテーマ=灰色 / ダーク=明るめ)
        #  mirror   : 自分の表示範囲・再生位置を映す相手(サブ側のグラフ)
        self.readonly = False
        self.sub_bg = False
        self.mirror = None

        self._drag = None
        self.configure(bg=self.bg_color())
        self.bind("<Configure>", self._on_configure)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        # =229: _plot() の1フレームキャッシュ(<Configure> と redraw で捨てる)
        self._plot_cache = None
        # =232: 5列csv の左右同時編集。表示(縮尺・位置・再生位置)を揃える
        #  仲間のグラフ / 「いま触っている方」の枠の強調 / 右上の見出し。
        self.peers = []
        self._syncing = False
        self.active = True          # 単独のときは常にアクティブ
        self.peer_mode = False      # 2本以上を同時に編集しているか
        self.active_color = "#1f6aa5"
        self.on_activate = None     # クリックで「アクティブになった」通知
        self.corner_text = ""       # 右上の見出し(「左（ロータ1）」など)
        # =298(要望4): 周辺表示の出し分け。5列csv の左右2本を縦に詰めるため、
        # 縮尺表示・「追従停止中」は上のグラフだけ、時間ラベルは下のグラフ
        # だけに出す。サブ表示(=227)は全部出さない。False にしたぶんの
        # 余白(SCALE_H / AXIS_H)は _plot が詰める。
        self.show_scale = True
        self.show_time = True
        self.show_follow_hint = True
        # =227: グラフ上のマウス位置(停止中の F キー配置に使う)
        self._mouse_xy = None
        self.bind("<ButtonPress-1>", self._on_press1)
        self.bind("<B1-Motion>", self._on_drag1)
        self.bind("<ButtonRelease-1>", self._on_release1)
        self.bind("<ButtonPress-3>", self._on_press3)
        self.bind("<B3-Motion>", self._on_drag3)
        self.bind("<ButtonRelease-3>", self._on_release3)
        self.bind("<Double-Button-3>", self._on_double3)
        self.bind("<Delete>", self._on_delete_key)
        for seq, fn in (("<Control-c>", self._key_copy),
                        ("<Control-x>", self._key_cut),
                        ("<Control-v>", self._key_paste),
                        ("<Control-z>", self._key_undo),
                        ("<Control-y>", self._key_redo)):
            self.bind(seq, fn)
            self.bind(seq.replace(seq[-2], seq[-2].upper()), fn)
        # =216: 矢印キー(テンキーの矢印も)で選択中の点・パターンを移動。
        # 単押し=1グリッド / 0.5秒以上の長押し=0.1秒ごとに連続移動
        self._nudge_held = None         # 押しっぱなし中の (dat_step, dpos_step)
        self._nudge_job = None          # 連続移動の after id
        self._nudge_release_job = None  # KeyRelease の確定待ち(自動リピート対策)
        for names, step in ((("Up", "KP_Up"), (0, 1)),
                            (("Down", "KP_Down"), (0, -1)),
                            (("Left", "KP_Left"), (-1, 0)),
                            (("Right", "KP_Right"), (1, 0))):
            for nm in names:
                self.bind("<KeyPress-" + nm + ">",
                          lambda _e, st=step: self._on_nudge_press(st))
                self.bind("<KeyRelease-" + nm + ">",
                          lambda _e, st=step: self._on_nudge_release(st))

    # ---- =216: 矢印キーによる移動 ----

    NUDGE_HOLD_MS = 500       # 長押しと見なすまで
    NUDGE_REPEAT_MS = 100     # 連続移動の間隔

    def nudge(self, dat_step: int, dpos_step: int) -> bool:
        """選択中の点・パターン(複数なら全部=剛体)を at/pos のグリッド
        1単位ぶん動かす(グリッド「なし」=1)。置けなければ False で
        何も変えない(描画もしない)。UNDO 1ステップ。"""
        if self.readonly:            # =227: サブ表示は見るだけ
            return False
        self._sync_pat_sel()
        m = self.model
        if not m.selection and not m.pattern_selection:
            return False
        dat = int(dat_step) * (self.grid_at if self.grid_at > 1 else 1)
        dpos = int(dpos_step) * (self.grid_pos if self.grid_pos > 1 else 1)
        if dpos and self.pat_center is not None and m.pattern_selection:
            return False        # =226: 離散はパターンの縦移動を許さない
        if dat == 0 and dpos == 0:
            return False
        plan = m.group_move_plan(dat, dpos, self.grid_at, self.grid_pos)
        if plan is None or plan == (0, 0):
            return False
        if not m.apply_group_move(*plan):
            return False
        self._notify_change()
        self._notify_select()
        self.redraw()
        return True

    def _on_nudge_press(self, step):
        if self._nudge_release_job is not None:
            # 自動リピート(X11 は Release/Press を繰り返す)=押しっぱなし継続
            try:
                self.after_cancel(self._nudge_release_job)
            except Exception:
                pass
            self._nudge_release_job = None
        if self._nudge_held == step:
            return "break"              # OS の自動リピートは無視(自前で刻む)
        self._nudge_stop()
        self._nudge_held = step
        self.nudge(*step)
        self._nudge_job = self.after(self.NUDGE_HOLD_MS, self._nudge_repeat)
        return "break"

    def _nudge_repeat(self):
        self._nudge_job = None
        if self._nudge_held is None:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.nudge(*self._nudge_held)
        self._nudge_job = self.after(self.NUDGE_REPEAT_MS, self._nudge_repeat)

    def _on_nudge_release(self, step):
        if self._nudge_held != step:
            return "break"
        if self._nudge_release_job is not None:
            try:
                self.after_cancel(self._nudge_release_job)
            except Exception:
                pass
        self._nudge_release_job = self.after(60, self._nudge_stop)
        return "break"

    def _nudge_stop(self):
        self._nudge_release_job = None
        self._nudge_held = None
        if self._nudge_job is not None:
            try:
                self.after_cancel(self._nudge_job)
            except Exception:
                pass
            self._nudge_job = None

    # ---- =297: pos の分解能はモデルが持つ(funscript=100 / csv=200) ----

    @property
    def pos_max(self) -> int:
        return int(getattr(self.model, "pos_max", 100) or 100)

    @property
    def pos_center(self) -> float:
        return self.pos_max / 2.0

    # ---- グリッド(=183: 端点の禁止帯をモデルへ同期) ----

    @property
    def grid_at(self) -> int:
        return self._grid_at

    @grid_at.setter
    def grid_at(self, value: int) -> None:
        self._grid_at = value
        # 端点の禁止帯=atグリッド半分・最低1ms(=183)。テストが
        # graph.grid_at へ直接代入しても同期されるよう property にする
        if getattr(self, "model", None) is not None:
            self.model.edge_tol = max(1.0, value * 0.5)

    # ---- パターン配置・操作(P2 =173) ----

    def set_place_tool(self, shape, name) -> None:
        """パターン配置ツールへ切り替える(shape=None で「点」へ戻る)。

        =297: shape は 0〜100 定義(標準/ユーザーパターン)。モデルの分解能
        (csv=200)へ縦に伸ばして持つ。"""
        self.place_shape = scale_shape(shape, self.pos_max) if shape \
            else None
        self.place_name = name if shape else None
        self.tool = "pattern" if shape else "point"
        self.sel_pattern = None
        self._ghost = None
        self.redraw()

    def _clear_ghost(self) -> None:
        if self._ghost is not None or self._point_ghost is not None:
            self._ghost = None
            self._point_ghost = None
            self.redraw()

    def _set_cross(self, cross, from_peer: bool = False) -> bool:
        """=233: 十字ガイドを更新し、**仲間のグラフへは縦線だけ**を配る。

        戻り値=自分の表示が変わったか(呼び出し側で redraw するため)。
        """
        changed = cross != self.cross
        self.cross = cross
        if not from_peer:
            tc = (cross[0], None) if cross else None
            subs = [g for g in (list(self.peers) + [self.mirror])
                    if g is not None and g is not self]
            for g in subs:
                try:
                    if not g.winfo_exists() or not g.winfo_ismapped():
                        continue
                except Exception:
                    continue
                if g.cross != tc:
                    g.cross = tc
                    g.redraw()
        return changed

    def _on_leave(self, _event=None):
        self._mouse_xy = None
        if self._set_cross(None):            # =233: 十字ガイドも消す
            self.redraw()
        self._clear_ghost()

    def mouse_place_at(self):
        """=227: グラフ上のマウス位置 (at, pos)。グラフの外なら None。"""
        if self._mouse_xy is None:
            return None
        x, y = self._mouse_xy
        x0, top, x1, bot = self._plot()
        if not (x0 <= x <= x1 and top <= y <= bot):
            return None
        return self.ms_of(x), self.pos_of_y(y)

    def _on_configure(self, _event=None):
        """=229: 実寸が変わったので _plot() の控えを捨てて描き直す。"""
        self._plot_cache = None
        self.redraw()

    def set_active(self, flag: bool, peer_mode: bool | None = None):
        """=232: 「いま触っているグラフ」の枠を強調する。

        peer_mode=False(単独)では枠を出さない。
        """
        self.active = bool(flag)
        if peer_mode is not None:
            self.peer_mode = bool(peer_mode)
        try:
            if not self.peer_mode:
                self.configure(highlightthickness=0)
            else:
                col = self.active_color if self.active else self.bg_color()
                self.configure(highlightthickness=2,
                               highlightbackground=col, highlightcolor=col)
        except Exception:
            pass

    def _guard(self, _event=None):
        """=227: サブ表示(readonly)は入力を受けない。"""
        return "break"

    def make_readonly(self, main=None):
        """=227: このグラフを**サブ表示(見るだけ)**にする。

        入力のバインドを外し、地の色を変える。`main` を渡すと、
        ホイールだけは本体へ回して**サブの上でも拡大縮小できる**ようにする
        (表示範囲は本体から映されるので、サブ側では動かさない)。
        """
        self.readonly = True
        self.sub_bg = True
        self.plain = False
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>",
                    "<ButtonPress-3>", "<B3-Motion>", "<ButtonRelease-3>",
                    "<Double-Button-3>", "<Delete>", "<Motion>", "<Leave>",
                    "<MouseWheel>", "<Button-4>", "<Button-5>",
                    "<Control-c>", "<Control-x>", "<Control-v>",
                    "<Control-z>", "<Control-y>",
                    "<Control-C>", "<Control-X>", "<Control-V>",
                    "<Control-Z>", "<Control-Y>"):
            try:
                self.unbind(seq)
            except Exception:
                pass
        for names in (("Up", "KP_Up"), ("Down", "KP_Down"),
                      ("Left", "KP_Left"), ("Right", "KP_Right")):
            for nm in names:
                for pre in ("<KeyPress-", "<KeyRelease-"):
                    try:
                        self.unbind(pre + nm + ">")
                    except Exception:
                        pass
        if main is not None:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.bind(seq, main._on_wheel)
        self.configure(bg=self.bg_color())

    def _sync_mirror(self):
        """=227/=232: 仲間のグラフへ**表示範囲と再生位置だけ**を映す。

        サブ表示(=227)は見るだけなので、時間軸(縮尺・位置)と再生位置・
        区間の目印を本体と揃えて描き直す(モデルと種別ごとの作法は相手の
        まま)。**=232: 5列csv の左右2本も同じ仕組みで揃える**
        (`peers`)。相手の redraw から戻ってくる無限再帰は
        `_syncing` で止める。
        """
        if self._syncing:
            return
        subs = [g for g in (list(self.peers) + [self.mirror])
                if g is not None and g is not self]
        if not subs:
            return
        self._syncing = True
        try:
            for sub in subs:
                try:
                    if not sub.winfo_exists() or not sub.winfo_ismapped():
                        continue
                except Exception:
                    continue
                changed = (sub.level != self.level
                           or sub.view_ms != self.view_ms
                           or sub.now_ms != self.now_ms
                           or sub.playing != self.playing
                           or sub.follow != self.follow
                           or sub.region != self.region)
                sub.level = self.level
                sub.view_ms = self.view_ms
                sub.now_ms = self.now_ms
                sub.playing = self.playing
                sub._played = self._played
                sub.follow = self.follow
                sub.region = self.region
                if changed:
                    sub._syncing = True
                    try:
                        sub.redraw()
                    finally:
                        sub._syncing = False
        finally:
            self._syncing = False

    def _on_motion(self, event):
        """カーソル位置の予定(ゴースト)を見せる。

        パターン配置ツール=予定形(仕様 4-2)/点モード=打点できる位置
        だけに小さな丸(=176。点の上・パターンの範囲では出さない)。
        """
        self._mouse_xy = (event.x, event.y)
        # =233: 十字ガイド。グリッドへ吸着した位置(=打点される位置)を通る
        x0, top, x1, bot = self._plot()
        if x0 <= event.x <= x1 and top <= event.y <= bot:
            cross = (snap(max(0.0, self.ms_of(event.x)), self.grid_at),
                     max(0, min(self.pos_max, snap(self.pos_of_y(event.y),
                                          self.grid_pos))))
        else:
            cross = None
        cross_changed = self._set_cross(cross)
        if self._drag is not None:
            self._clear_ghost()
            return
        if self.place_shape is not None:
            self._ghost = self._plan_at(self.ms_of(event.x),
                                        self.pos_of_y(event.y))
            self.redraw()
            return
        g = self._point_ghost_at(event.x, event.y)
        if g != self._point_ghost or cross_changed:
            self._point_ghost = g
            self.redraw()

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

        **=226: 離散的なスクリプト(pat_center あり)では時間方向だけ動く**
        (縦に動かすと速度・強さが変わってしまうため。要望6/8)。
        """
        idx = self.sel_pattern
        if idx is None or idx >= len(self.model.patterns):
            return None
        d = self._drag
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        dat = snap((event.x - d["x"]) * (span / (x1 - x0)), self.grid_at)
        dpos = 0 if self.pat_center is not None else \
            snap(self.pos_of_y(event.y) - self.pos_of_y(d["y"]),
                 self.grid_pos)
        rec = self.model.patterns[idx]
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

    # ---- 小道具(DeviceGraph と同じ流儀) ----

    @staticmethod
    def _dark() -> bool:
        import customtkinter as ctk
        return ctk.get_appearance_mode() != "Light"

    def _c(self, pair):
        return pair[1] if self._dark() else pair[0]

    def bg_color(self):
        """キャンバスの地の色。=227: サブ表示は地の色を変えて区別する
        (ライト/カラーテーマ=灰色 / ダーク=通常より明るめ)。"""
        if self.sub_bg:
            return self._c(("#d9d9d9", "#4a4a4a"))
        return self._c(self._DG.C_BG)

    def span_ms(self) -> float:
        return self.LEVELS[self.level][2] * 2.5 * 1000.0

    def set_level(self, level: int):
        level = max(0, min(len(self.LEVELS) - 1, int(level)))
        if level != self.level:
            self.level = level
            self.redraw()

    def set_now(self, ms: float):
        self.now_ms = float(ms)
        if self.follow:
            self.view_ms = self.now_ms
        self.redraw()

    def initial_view(self, duration_ms: int):
        """全長に応じた初期表示。空(全長0)は表示幅30秒相当の縮尺にする。
        =201: 初期表示は左寄りアンカーへ戻す(_played リセット)。"""
        self._played = bool(self.playing)
        if duration_ms <= 0:
            for i, lv in enumerate(self.LEVELS):
                if lv[2] * 2.5 * 1000.0 >= EMPTY_VIEW_MS:
                    self.level = i
                    break
        else:
            self.level = self._DG.DEFAULT_LEVEL
        self.follow = True
        self.view_ms = self.now_ms
        self.redraw()

    @staticmethod
    def fmt_time_ms(ms: float) -> str:
        """時間軸ラベル(=210: **hh:mm:ss.FFF**・日英共通。
        =179 のミリ秒精度はそのまま)。"""
        neg = ms < 0
        ms = int(round(abs(ms)))
        h, rem = divmod(ms, 3600_000)
        m, rem = divmod(rem, 60_000)
        s, f = divmod(rem, 1000)
        return ("-" if neg else "") + f"{h:02d}:{m:02d}:{s:02d}.{f:03d}"

    @staticmethod
    def fmt_scale_s(sec: float) -> str:
        """縮尺表示の文言(=210)。副線(mid)の間隔を「0.25秒」「7分30秒」
        のように短く(1分未満=秒・小数はそのまま / 1分以上=分+秒)。"""
        sec = float(sec)
        if sec < 60.0:
            return tr("{0}秒").format(f"{sec:g}")
        m = int(sec // 60)
        s = sec - m * 60
        if s <= 0:
            return tr("{0}分").format(m)
        return tr("{0}分{1}秒").format(m, f"{s:g}")

    def label_px(self) -> int:
        """時間ラベル1つぶんの幅(px)。=210: hh:mm:ss.FFF の実測。
        フォントは1度だけ作って使い回す。"""
        fnt = getattr(self, "_lbl_font", None)
        if fnt is None:
            import tkinter.font as tkfont
            fnt = tkfont.Font(root=self, family=appfont.FAMILY, size=8)
            self._lbl_font = fnt
        return int(fnt.measure("00:00:00.000"))

    # ---- 座標変換 ----

    def _plot(self):
        """描画領域 (x0, top, x1, bot)。=210: 上端に縮尺表示の帯
        (SCALE_H)を確保し、グラフ本体はその下から始まる。

        **=229: 1フレームぶんキャッシュする**。`winfo_width()` /
        `winfo_height()` は Tk への往復で、`x_of` / `y_of` 経由で
        1フレームに数千回呼ばれるため、**描画時間の3〜4割**をここで
        使っていた(60fps 化の最大のボトルネック)。実寸が変わるのは
        `<Configure>` のときだけなので、そこと `redraw()` の先頭で
        捨てれば十分。
        """
        p = self._plot_cache
        if p is None:
            w = self.winfo_width()
            h = self.winfo_height()
            # =298: 縮尺表示を出さないグラフは上端を詰める(「100」ラベル・
            # 見出し・時間グリッドの張り出し(top-12)ぶんの 14px は残す)
            top = self.TOP_PAD + self.SCALE_H if self.show_scale else 14
            # 時間ラベルを出さないグラフは下端も詰める(ヒートマップの帯が
            # 出るときはその高さぶんだけ残す)
            bottom = self.AXIS_H if self.show_time else \
                (16 if self.heat else 6)
            p = (self.GUTTER, top, max(self.GUTTER + 1, w - 4),
                 max(top + 1, h - bottom))
            self._plot_cache = p
        return p

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

    def set_playing(self, playing: bool):
        """再生状態の通知(=198/=201)。再生を開始したら追従アンカーは
        中央になり、一時停止しても中央のまま(画面は動かない)。"""
        playing = bool(playing)
        if playing:
            self._played = True
        if playing == self.playing:
            return
        self.playing = playing
        self.redraw()

    def _draw_audio_wave(self, x0, top, x1, bot, left, span):
        """=243: 音声波形(振幅の帯)を背景へ描く。

        モノラル=合成chを全高で1本。ステレオ=上半分に L・下半分に R
        (「(逆)」で入れ替え)。wave_channel が 0/1 のとき(UFOTW の
        左右2本)は、そのchを全高で描く(「(逆)」で入れ替え)。
        """
        env = self.wave_env
        mode = self.wave_mode
        if not env or mode == "off" or x1 - x0 < 8 or bot - top < 8:
            return
        color = self._c(WAVE_BAND_COLOR)
        ppm = (x1 - x0) / span
        off = float(self.wave_offset)

        def ms_of_px(x):
            return left + (x - x0) / ppm + off

        bucket = env["bucket_ms"]
        peak = env["peak"]
        chans = env["chans"]
        if mode == "mono" or len(chans) < 2:
            draw_audio_band(self, env["mono"], bucket, peak, x0, x1,
                            ms_of_px, (top + bot) / 2.0,
                            (bot - top) / 2.0, color)
            return
        rev = mode == "stereo_rev"
        a, b = (1, 0) if rev else (0, 1)
        if self.wave_channel is not None:
            # 0=上(左)→L / 1=下(右)→R。「(逆)」は割り当てを入れ替える
            ch = chans[(int(self.wave_channel) + (1 if rev else 0)) % 2]
            draw_audio_band(self, ch, bucket, peak, x0, x1, ms_of_px,
                            (top + bot) / 2.0, (bot - top) / 2.0, color)
            return
        mid = (top + bot) / 2.0
        half = (bot - top) / 4.0
        draw_audio_band(self, chans[a], bucket, peak, x0, x1, ms_of_px,
                        (top + mid) / 2.0, half, color)
        draw_audio_band(self, chans[b], bucket, peak, x0, x1, ms_of_px,
                        (mid + bot) / 2.0, half, color)

    def x_of(self, ms: float) -> float:
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        left = self.view_ms - span * self._anchor()
        return x0 + (ms - left) * ((x1 - x0) / span)

    def ms_of(self, x: float) -> float:
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        left = self.view_ms - span * self._anchor()
        return left + (x - x0) * (span / (x1 - x0))

    def y_of(self, pos: float) -> float:
        _x0, top, _x1, bot = self._plot()
        pm = float(self.pos_max)
        return bot - (max(0.0, min(pm, pos)) / pm) * (bot - top)

    def pos_of_y(self, y: float) -> float:
        _x0, top, _x1, bot = self._plot()
        if bot <= top:
            return 0.0
        pm = float(self.pos_max)
        return max(0.0, min(pm, (bot - y) / (bot - top) * pm))

    def _out_of_range(self, x: float, y: float) -> bool:
        """=279: 打点できない場所(at<0 / pos<0 / pos>100)か。

        グリッド枠の外(上下の余白・0:00 より左)へのクリックは打点せず、
        **選択解除**として扱う(実機FB1)。
        """
        _x0, top, _x1, bot = self._plot()
        if bot <= top:
            return True
        # 枠線上のクリックは打点扱い(1px の許容)
        return (y < top - 1 or y > bot + 1 or x < self.x_of(0) - 1)

    def _min_view_ms(self) -> float:
        """=279: 手動スクロール(パン/ホイール)で許す view_ms の下限。

        左端が **0:00 より初期表示ぶん(VIEW_ANCHOR)** より左へ行かないよう
        にする(マイナス時間を表示する必要はない=実機FB2)。再生追従は
        対象外(再生位置を中央に置く動きは従来どおり)。
        """
        span = self.span_ms()
        return span * self._anchor() - span * self.VIEW_ANCHOR

    def _clamp_view(self) -> None:
        self.view_ms = max(self._min_view_ms(), self.view_ms)

    def hit_point(self, x: float, y: float):
        """(x,y) に最も近い点(HIT_PX 以内)の at を返す。無ければ None。"""
        best, best_d = None, None
        for at, pos in self.model.points:
            dx = self.x_of(at) - x
            dy = self.y_of(pos) - y
            d = (dx * dx + dy * dy) ** 0.5
            if d <= self.HIT_PX and (best_d is None or d < best_d):
                best, best_d = at, d
        return best

    # ---- 操作 ----

    def _on_wheel(self, event):
        """ホイール=時間の拡大縮小。=197: **マウス位置を基準**にする
        (カーソル下の時刻が同じ画面位置に留まるよう view_ms を合わせる)。
        再生中の追従では次のフレームで再生位置基準に戻る。"""
        up = getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4
        ex = getattr(event, "x", None)
        ms = self.ms_of(ex) if ex is not None else None
        old = self.level
        self.set_level(self.level + (-1 if up else 1))
        if ms is not None and self.level != old and \
                not (self.follow and self.playing):
            x0, _t, x1, _b = self._plot()
            span = self.span_ms()
            frac = (ex - x0) / max(1.0, (x1 - x0))
            self.view_ms = ms - (frac - self._anchor()) * span
            self._clamp_view()       # =279
            self.redraw()
        return "break"

    def _ctrl(self, event) -> bool:
        return bool(getattr(event, "state", 0) & 0x0004)

    def _pattern_side_of(self, at: int, x: float):
        """=230: `at` が2つのパターンの**境目**なら、クリックした x の側の
        パターンを返す(左寄りなら手前・右寄りなら未来)。境目でなければ
        従来どおり `pattern_of_at()`。

        数珠つなぎで作った並びでは、境目の点は前後2つのパターンの端点を
        兼ねる。従来は常に**手前側**が選ばれていたため、未来側のパターンを
        掴みたいときに掴めなかった(=230 ユーザー報告「移動しづらい」)。
        """
        left = right = None
        for j, rec in enumerate(self.model.patterns):
            if not rec["ats"]:
                continue
            if rec["ats"][-1] == at:
                left = j
            if rec["ats"][0] == at:
                right = j
        if left is not None and right is not None:
            return right if x >= self.x_of(at) else left
        return self.model.pattern_of_at(at)

    def _on_press1(self, event):
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        self.focus_set()
        if self.on_activate is not None:      # =232: 触った方がアクティブ
            self.on_activate()
        if self.place_shape is not None:
            # パターン配置ツール(P2): クリックで配置。ドラッグはしない。
            self._drag = {"kind": "place", "x": event.x, "y": event.y,
                          "moved": 0}
            return
        hit = self.hit_point(event.x, event.y)
        hit_pat = self._pattern_side_of(hit, event.x) \
            if hit is not None else None
        if self._ctrl(event):
            # Ctrl+左クリック=選択に追加/解除。点は従来どおり、
            # パターンも追加選択できる(=185: 混在コピーの入口)
            if hit is not None and hit_pat is None:
                self.model.toggle_select(hit)
                self._notify_select()
                self.redraw()
            else:
                pat = hit_pat if hit_pat is not None else \
                    self._pattern_hit(event.x, event.y)
                if pat is not None:
                    self.model.toggle_pattern_select(pat)
                    self.sel_pattern = pat \
                        if pat in self.model.pattern_selection else None
                    self._notify_select()
                    self.redraw()
            self._drag = None
            return
        # 選択中パターンの拡縮ハンドル(仕様 5.4)
        handle = self._handle_hit(event.x, event.y)
        if handle is not None:
            self._drag = {"kind": "resize", "handle": handle,
                          "x": event.x, "y": event.y, "moved": 0,
                          "plan": None}
            return
        # =202: 剛体の掴み領域。複数選択(合計2つ以上)のときは選択範囲の
        # 帯(点線の長方形)の中の**ドラッグ**を剛体移動にする(=188の
        # 「選択済みの要素を掴む」判定を包含する)。**クリック**(動かさず
        # 離す)は従来の操作へフォールバック(_group_click_fallback)=
        # 打点・選択の操作は失われない
        if self._group_box_hit(event.x, event.y):
            self._drag = {"kind": "groupmove", "x": event.x,
                          "y": event.y, "moved": 0, "plan": None,
                          "click_fb": True}
            return
        if hit_pat is None and hit is not None:
            # 普通の点(P1 と同じ)
            self.sel_pattern = None
            self.model.pattern_selection = set()
            if hit not in self.model.selection:
                self.model.select_only(hit)
                self._notify_select()
            self._drag = {"kind": "move", "x": event.x, "y": event.y,
                          "moved": 0, "dat": 0.0, "dpos": 0.0}
        else:
            pat = hit_pat if hit_pat is not None else                 self._pattern_hit(event.x, event.y)
            if pat is not None:
                # パターンの選択と移動(点の個別選択はできない=仕様 6a)
                self.sel_pattern = pat
                self.model.pattern_selection = {pat}
                self.model.clear_selection()
                self._notify_select()
                self._drag = {"kind": "patmove", "x": event.x,
                              "y": event.y, "moved": 0, "plan": None}
            else:
                self.sel_pattern = None
                self.model.pattern_selection = set()
                self._drag = {"kind": "rect", "x": event.x, "y": event.y,
                              "moved": 0, "cur": (event.x, event.y)}
        self.redraw()

    def _on_drag1(self, event):
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        d = self._drag
        if d is None:
            return
        d["moved"] = max(d["moved"], abs(event.x - d["x"]),
                         abs(event.y - d["y"]))
        if d["kind"] == "move":
            x0, _t, x1, _b = self._plot()
            span = self.span_ms()
            d["dat"] = (event.x - d["x"]) * (span / (x1 - x0))
            d["dpos"] = self.pos_of_y(event.y) - self.pos_of_y(d["y"])
        elif d["kind"] == "patmove":
            d["plan"] = self._patmove_plan(event)
        elif d["kind"] == "groupmove":
            # =188: 剛体移動。Δをグリッドへ丸めた (dat,dpos) が plan
            x0, _t, x1, _b = self._plot()
            span = self.span_ms()
            dat = (event.x - d["x"]) * (span / (x1 - x0))
            dpos = self.pos_of_y(event.y) - self.pos_of_y(d["y"])
            scale = None
            if self.pat_center is not None:
                # =234: 離散(rotate系)は縦を**中心の線を軸にした拡縮**に
                # する(=226 の「縦の平行移動は許さない」の置き換え)。
                # 掴んだ高さが中心からどれだけ動いたか=倍率。
                c = float(self.pat_center)
                p0 = self.pos_of_y(d["y"])
                p1 = self.pos_of_y(event.y)
                f = 1.0 if abs(p0 - c) < 1.0 else (p1 - c) / (p0 - c)
                f = max(0.0, f)
                # パターンの拡縮(=226)と同じく、**全部が 0〜100 に収まる
                # 最大倍率でクランプ**する(はみ出す位置では何も起きない、
                # ではなく端で止まる)
                vals = [self.model.pos_of(a) for a in self.model.selection]
                for gi in self.model.pattern_selection:
                    if 0 <= gi < len(self.model.patterns):
                        vals += [self.model.pos_of(a)
                                 for a in self.model.patterns[gi]["ats"]]
                for v in vals:
                    if v is None:
                        continue
                    dv = v - c
                    if dv > 1e-9:
                        f = min(f, (self.pos_max - c) / dv)
                    elif dv < -1e-9:
                        f = min(f, (0.0 - c) / dv)
                scale = (c, max(0.0, f))
                dpos = 0.0
            d["scale"] = scale
            d["plan"] = self.model.group_move_plan(
                dat, dpos, self.grid_at, self.grid_pos, scale=scale)
        elif d["kind"] == "resize":
            d["plan"] = self._resize_plan(event, d["handle"])
        elif d["kind"] == "rect":
            d["cur"] = (event.x, event.y)
        self.redraw()

    def _on_release1(self, event):
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d["kind"] == "place":
            if d["moved"] < self.CLICK_PX:
                plan = self._plan_at(self.ms_of(event.x),
                                     self.pos_of_y(event.y))
                # =221: overwrite フラグ付き=重なるものを明け渡してから置く
                if plan is not None and plan.get("overwrite"):
                    done = self.model.place_plan_overwrite(
                        plan, self.place_name) == "ok"
                else:
                    done = plan is not None and \
                        self.model.place_pattern(plan, self.place_name)
                if done:
                    self._notify_change()
                    self._notify_select()
                    # =179: 配置に成功したら点モードへ戻る(ダイアログが
                    # ツールのトグル状態を戻し、set_place_tool(None) を呼ぶ)
                    if callable(self.on_placed):
                        self.on_placed()
            self._clear_ghost()
            self.redraw()
            return
        if d["kind"] == "patmove":
            if d["moved"] >= self.CLICK_PX and d.get("plan") and \
                    self.sel_pattern is not None:
                if self.model.replace_patterns(d["plan"]):
                    self._notify_change()
            self.redraw()
            return
        if d["kind"] == "groupmove":
            # =188: 剛体移動の確定(UNDO 1ステップ)
            if d["moved"] < self.CLICK_PX and d.get("click_fb"):
                # =202: 掴み領域の中のクリック=従来の操作(打点・選択)
                self._group_click_fallback(event)
                self.redraw()
                return
            if d["moved"] >= self.CLICK_PX and d.get("plan") is not None:
                gdat, gdpos = d["plan"]
                gsc = d.get("scale")
                if (gdat or gdpos or
                        (gsc is not None and abs(gsc[1] - 1.0) > 1e-9)) and \
                        self.model.apply_group_move(gdat, gdpos, scale=gsc):
                    self._notify_change()
                    self._notify_select()
            self.redraw()
            return
        if d["kind"] == "resize":
            if d.get("plan") and self.sel_pattern is not None:
                if self.model.replace_patterns(d["plan"]):
                    self._notify_change()
            self.redraw()
            return
        if d["kind"] == "move":
            if d["moved"] >= self.CLICK_PX:
                # 移動の確定(離した時点で1ステップ=仕様 4.8)
                if self.model.move_selected(d["dat"], d["dpos"],
                                            self.grid_at, self.grid_pos):
                    self._notify_change()
                self._notify_select()
            self.redraw()
            return
        # 空欄からのドラッグ
        if d["moved"] < self.CLICK_PX:
            if self._out_of_range(event.x, event.y):
                # =279: 枠の外のクリック=打点せず選択解除
                self._deselect_all()
                self.redraw()
                return
            # 単クリック=打点(点モード・グリッド吸着)
            if self.tool == "point":
                at = snap(max(0.0, self.ms_of(event.x)), self.grid_at)
                pos = max(0, min(self.pos_max, snap(self.pos_of_y(event.y),
                                           self.grid_pos)))
                if self.model.add_point(at, pos):
                    self._notify_change()
                self._notify_select()
        else:
            # 矩形選択(矩形に完全に入った点を選択)
            a0 = self.ms_of(d["x"])
            a1 = self.ms_of(event.x)
            p0 = self.pos_of_y(d["y"])
            p1 = self.pos_of_y(event.y)
            self.model.select_rect(a0, a1, p0, p1)
            self._notify_select()
        self.redraw()

    def _on_press3(self, event):
        if not self.readonly and self.on_activate is not None:
            self.on_activate()                # =232: 触った方がアクティブ
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        self.focus_set()
        self._drag = {"kind": "pan", "x": event.x, "y": event.y,
                      "view": self.view_ms, "moved": 0}

    def _on_drag3(self, event):
        d = self._drag
        if d is None or d.get("kind") != "pan":
            return
        d["moved"] = max(d["moved"], abs(event.x - d["x"]),
                         abs(event.y - d["y"]))
        if d["moved"] < self.CLICK_PX:
            return
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        self.follow = False
        self.view_ms = d["view"] - (event.x - d["x"]) * (span / (x1 - x0))
        self._clamp_view()           # =279: 0:00 より過去へはパンしない
        self.redraw()

    def _on_release3(self, event):
        d = self._drag
        self._drag = None
        if d is None or d.get("kind") != "pan":
            return
        if d["moved"] < self.CLICK_PX and callable(self.on_menu):
            hit = self.hit_point(event.x, event.y)
            hit_pat = self.model.pattern_of_at(hit) \
                if hit is not None else None
            if hit_pat is None:
                hit_pat = self._pattern_hit(event.x, event.y)
            if hit_pat is not None:
                # =241: 複数選択に含まれるパターンの上なら選択を保ち、
                # **選択全体のメニュー**を出す(multi=True)。
                m = self.model
                multi = hit_pat in m.pattern_selection and \
                    (len(m.pattern_selection) + len(m.selection)) >= 2
                if not multi:
                    # パターン上の右クリック=パターンを選択してメニュー(P2)
                    self.sel_pattern = hit_pat
                    m.pattern_selection = {hit_pat}
                    m.clear_selection()
                    self._notify_select()
                    self.redraw()
                self.on_menu(event, {"at": None, "pattern": hit_pat,
                                     "multi": multi,
                                     "ms": max(0.0, self.ms_of(event.x)),
                                     "pos": self.pos_of_y(event.y)})
                return
            if hit is None:
                # =199: 空欄ではメニューを出さない(選択があっても)。
                # ダブル右クリック=192が削除メニューに邪魔されないように
                return
            if hit not in self.model.selection:
                self.model.select_only(hit)
                self._notify_select()
                self.redraw()
            self.on_menu(event, {"at": hit, "pattern": None,
                                 "ms": max(0.0, self.ms_of(event.x)),
                                 "pos": self.pos_of_y(event.y)})

    def _on_double3(self, event):
        """右ダブルクリック=再生位置をそこへセットする(=192)。

        1回目の右クリックでメニューが開いた場合(パターン上・選択あり)は
        2回目のクリックがメニュー側へ行くためこのイベントは来ない=
        実質「メニューが出ない場所」でだけ効く(案A)。パンのドラッグ状態は
        ここで破棄する(release3 が何もしないように)。
        """
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        self._drag = None
        if callable(self.on_seek):
            self.on_seek(max(0.0, self.ms_of(event.x)))
        return "break"

    def _group_click_fallback(self, event):
        """掴み領域の中で動かさずに離した(=クリック)ときの従来操作(=202)。

        空欄=打点(点モード)/選択外の点=単独選択/選択外のパターン=
        単独選択。選択済みの点・パターンのクリックは選択を保つ(従来の
        =188 のクリックと同じ)。
        """
        hit = self.hit_point(event.x, event.y)
        hit_pat = self.model.pattern_of_at(hit) if hit is not None else None
        if hit_pat is None and hit is not None:
            if hit not in self.model.selection:
                self.sel_pattern = None
                self.model.pattern_selection = set()
                self.model.select_only(hit)
                self._notify_select()
            return
        pat = hit_pat if hit_pat is not None else \
            self._pattern_hit(event.x, event.y)
        if pat is not None:
            if pat not in self.model.pattern_selection:
                self.sel_pattern = pat
                self.model.pattern_selection = {pat}
                self.model.clear_selection()
                self._notify_select()
            else:
                self.sel_pattern = pat
            return
        # 空欄=打点(点モードのみ。配置ツール中は press で place になる
        # ため、ここへは来ない)
        if self._out_of_range(event.x, event.y):
            self._deselect_all()     # =279
            return
        if self.tool == "point":
            self.sel_pattern = None
            self.model.pattern_selection = set()
            at = snap(max(0.0, self.ms_of(event.x)), self.grid_at)
            pos = max(0, min(self.pos_max, snap(self.pos_of_y(event.y),
                                       self.grid_pos)))
            if self.model.add_point(at, pos):
                self._notify_change()
            self._notify_select()

    def _deselect_all(self):
        """=279: 点・パターンの選択をすべて解除する。"""
        self.sel_pattern = None
        self.model.pattern_selection = set()
        self.model.clear_selection()
        self._notify_select()

    def _sync_pat_sel(self):
        """sel_pattern を pattern_selection へ畳み込む(=185)。

        通常のクリック経路では常に同期しているが、グループ化直後など
        コード側から sel_pattern だけが設定される経路の保険。
        """
        if self.sel_pattern is not None and \
                0 <= self.sel_pattern < len(self.model.patterns):
            self.model.pattern_selection.add(self.sel_pattern)

    def _on_delete_key(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        # =185: 選択中の点+パターンをまとめて1ステップで削除
        self._sync_pat_sel()
        had_pat = bool(self.model.pattern_selection)
        if self.model.delete_selected_any():
            if had_pat:
                self.sel_pattern = None
            self._notify_change()
            self._notify_select()
            self.redraw()
        return "break"

    def _key_copy(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        # =185: 点+パターンの混在コピー(選択がそのまま入る。
        # 単独パターンの Ctrl+C=旧=180 も pattern_selection 経由で同じ)
        self._sync_pat_sel()
        self.model.copy_selected()
        return "break"

    def _key_cut(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        self._sync_pat_sel()
        had_pat = bool(self.model.pattern_selection)
        if self.model.cut_selected():
            if had_pat:
                self.sel_pattern = None
            self._notify_change()
            self._notify_select()
            self.redraw()
        return "break"

    def _paste_anchor(self):
        """Ctrl+V の基準(=185)。選択中の最も未来の端の (at, pos)。

        点なら点の at、パターンなら右端。両方選択中なら未来のほう。
        何も選んでいなければ最も未来のパターンの右端(=180 の踏襲)。
        """
        m = self.model
        best = None                      # (at, pos)
        if m.selection:
            a = max(m.selection)
            best = (a, m.pos_of(a))
        for i in m.pattern_selection:
            if 0 <= i < len(m.patterns) and m.patterns[i]["ats"]:
                hi = m.patterns[i]["ats"][-1]
                if best is None or hi > best[0]:
                    best = (hi, m.pos_of(hi))
        if best is None and self.sel_pattern is not None and \
                self.sel_pattern < len(m.patterns) and \
                m.patterns[self.sel_pattern]["ats"]:
            hi = m.patterns[self.sel_pattern]["ats"][-1]
            best = (hi, m.pos_of(hi))
        if best is None:
            mf = m.most_future_pattern()
            if mf is not None:
                hi = m.patterns[mf]["ats"][-1]
                best = (hi, m.pos_of(hi))
        return best

    def _key_paste(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        # =185: 混在クリップボードの上書き貼り付け。
        # パターンを含む=アンカーの端に先頭が載り、高さもアンカーへ
        # 平行移動(=180 の踏襲)/点のみ=選択中で最も at が大きい点に
        # 先頭が載り、高さは絶対値のまま(=172 の踏襲)。
        m = self.model
        if not m.has_clipboard():
            return "break"
        if m.clipboard_patterns:
            anchor = self._paste_anchor()
            if anchor is None:
                return "break"          # 置き場所が決まらない=無音(10b)
            first = m.clip_first_pos()
            # =227: 離散的なスクリプトでは **高さは固定**(直前のパターンへ
            # 合わせる「連続性」は付けない。ユーザー要望4)
            if self.pat_center is not None:
                voff = 0
            else:
                voff = (anchor[1] - first) if (first is not None
                                               and anchor[1] is not None) \
                    else 0
            result = m.paste_clip(float(anchor[0]), voff)
        else:
            result = m.paste_over_anchor()
        if result == "ok":
            # 続けて Ctrl+V で数珠つなぎできるよう、貼ったパターンの
            # うち最も未来のものを選択状態にする
            if m.pattern_selection:
                self.sel_pattern = max(
                    m.pattern_selection,
                    key=lambda i: m.patterns[i]["ats"][-1])
            self._notify_change()
            self._notify_select()
            self.redraw()
        elif result in ("no_selection", "range", "edge") and \
                callable(self.on_paste_reject):
            # 拒否の理由をダイアログ側へ知らせる(=171/=172/=185)
            self.on_paste_reject(result)
        return "break"

    def _key_undo(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        if self.model.undo():
            self._after_history()
        return "break"

    def _key_redo(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        if self.model.redo():
            self._after_history()
        return "break"

    def _after_history(self):
        """=298: UNDO/REDO 後の後始末。戻したのが**仲間のモデル**(左右
        共有の UNDO)なら、そのグラフの選択パターン表示も畳んで描き直す。"""
        target = self.model.last_undone
        for g in [self] + [p for p in self.peers if p is not None]:
            if g.model is target or g is self:
                g.sel_pattern = None
                g.redraw()
        self._notify_change()
        self._notify_select()

    def _notify_change(self):
        if callable(self.on_change):
            self.on_change()

    def _notify_select(self):
        if callable(self.on_select):
            self.on_select()

    # ---- 描画 ----

    def redraw(self):
        _DG = self._DG
        self._plot_cache = None       # =229: 実寸はフレームごとに1回だけ読む
        self.delete("all")
        self.configure(bg=self.bg_color())
        self._sync_mirror()
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 40:
            return
        x0, top, x1, bot = self._plot()
        span = self.span_ms()
        left = self.view_ms - span * self._anchor()
        fine, mid, coarse = self.LEVELS[self.level]

        # =243: 音声波形の帯(基準線・点・線の背面=最初に描く)
        self._draw_audio_wave(x0, top, x1, bot, left, span)

        # 時間の基準線。=176: グリッド[at]が「なし(1)」以外のときは、
        # **細線をatグリッドの線に置き換える**(縦横の薄い線の交点=
        # 打点できる位置、が成り立つように)。1(なし)のときは従来どおり
        # 縮尺段階の細線を描く。グリッド線が詰まりすぎる縮尺(4px未満)では
        # 細線へフォールバックする。
        grid_px = self.grid_at * ((x1 - x0) / span)
        use_grid_lines = self.grid_at > 1 and grid_px >= 4
        if use_grid_lines:
            t = int(max(0.0, left) // self.grid_at) * self.grid_at
            col = self._c(_DG.C_GRID_T[0])
            while t <= left + span + 1:
                x = self.x_of(t)
                if x0 - 1 <= x <= w and t >= 0:
                    self.create_line(x, top, x, bot, fill=col)
                t += self.grid_at
        steps = [(mid, self._c(_DG.C_GRID_T[1])),
                 (coarse, self._c(_DG.C_GRID_T[2]))]
        if not use_grid_lines:
            steps.insert(0, (fine, self._c(_DG.C_GRID_T[0])))
        for step_s, color in steps:
            step = step_s * 1000.0
            t = int(left // step) * step
            while t <= left + span + 1:
                x = self.x_of(t)
                if x0 - 1 <= x <= w:
                    self.create_line(x, top - 12, x, bot, fill=color)
                t += step

        # 位置の基準線。posグリッド(10単位など)を薄く、0/中央/上端 を濃く。
        # =297: csv は 0〜200(中央 100)。
        pm = self.pos_max
        pc = int(self.pos_center)
        if self.grid_pos > 1:
            p = 0
            while p <= pm:
                if p not in (0, pc, pm):
                    y = self.y_of(p)
                    self.create_line(x0, y, w, y,
                                     fill=self._c(_DG.C_GRID_POS_SUB))
                p += self.grid_pos
        for p in (0, pc, pm):
            y = self.y_of(p)
            self.create_line(x0, y, w, y, fill=self._c(_DG.C_GRID_POS))
        gx = self.GUTTER - 4
        # =233: csv(回転速度)では **下から -100 / 0 / 100** と書く
        # (=297: 中央 pos100=停止・上=正回転・下=逆回転。速度 1 刻み)
        _axis = ({pm: "100", pc: "0", 0: "-100"}
                 if self.pos_axis == "speed" else None)
        for p, anc in ((pm, "ne"), (pc, "e"), (0, "se")):
            self.create_text(gx, self.y_of(p), anchor=anc,
                             text=(_axis[p] if _axis else str(p)),
                             fill=self._c(_DG.C_AXIS_TEXT),
                             font=(appfont.FAMILY, 8))

        # 区間の目印(仕様 4.2: 縦の破線2本。編集はできない)
        lo, hi, given = self.region
        if given:
            col = self._c(_DG.C_HEAD)
            for t in (lo, hi):
                if t is None:
                    continue
                x = self.x_of(float(t))
                if x0 <= x <= w:
                    self.create_line(x, top, x, bot, fill=col, dash=(4, 3))

        # 波形(移動ドラッグ中は選択点へ暫定の移動を適用したゴーストを描く)
        pts = list(self.model.points)
        sel = set(self.model.selection)
        drag = self._drag if (self._drag and
                              self._drag.get("kind") == "move" and
                              self._drag["moved"] >= self.CLICK_PX) else None
        ghost_map = {}
        if drag:
            ghost_map, _ok = moved_points(pts, sel, drag["dat"], drag["dpos"],
                                          self.grid_at, self.grid_pos,
                                          pos_max=self.pos_max)
        disp = []
        for at, pos in pts:
            if at in ghost_map:
                disp.append((ghost_map[at][0], ghost_map[at][1], at in sel))
            else:
                disp.append((at, pos, at in sel))
        disp.sort(key=lambda t: t[0])

        wave = self._c(_DG.C_WAVE_POS)
        acc = self._c(("#c9451a", "#ff8a3d"))     # 選択(オレンジ系の赤)
        patc = self._c(self.PATTERN_GREEN)        # パターンの線と点(=184)
        # =184: パターンの時間範囲の内側の線分は緑で描く(青の波形と
        # 一目で見分けられるように)。範囲=各パターンの (先頭at, 末尾at)
        # =189/=196: 選択中のものは線も赤にする。判定は「両端とも選択集合
        # (選択中の点 ∪ 選択中パターンの構成点)に入る線分」。これで
        # パターン内の線分に加え、**選択中の点と選択中パターンの端点を結ぶ
        # 線分**・選択中パターンどうしをつなぐ線分も赤になる(=196)
        sel = self.model.selection
        sel_pat_idx = set(self.model.pattern_selection)
        if self.sel_pattern is not None:
            sel_pat_idx.add(self.sel_pattern)
        pat_ranges = []
        sel_pat_ats = set()
        for i, rec in enumerate(self.model.patterns):
            if not rec["ats"]:
                continue
            pat_ranges.append((rec["ats"][0], rec["ats"][-1]))
            if i in sel_pat_idx:
                sel_pat_ats.update(rec["ats"])
        selset = sel | sel_pat_ats
        # 線(linear/twist は隣り合う点を斜め線で結ぶ)
        # =224: csv(step)は**階段**で結ぶ(次の点まで同じ値を保つ)
        # **=229: 同じ色が続くぶんは1本のポリラインにまとめる**
        # (1フレームのキャンバスアイテム数が描画時間をほぼ決めるため。
        #  色の変わり目・表示範囲外で切る。見た目は従来と同じ)
        _run = []
        _run_col = None

        def _flush_run():
            nonlocal _run, _run_col
            if _run_col is not None and len(_run) >= 4:
                self.create_line(*_run, fill=_run_col, width=2, tags="wave")
            _run, _run_col = [], None

        for i in range(len(disp) - 1):
            a0, p0, _s0 = disp[i]
            a1, p1, _s1 = disp[i + 1]
            if a1 < left - span or a0 > left + span * 1.5:
                _flush_run()
                continue
            if a0 in selset and a1 in selset:
                col = acc
            elif any(lo <= a0 and a1 <= hi for lo, hi in pat_ranges):
                col = patc
            else:
                col = wave
            xa, ya = self.x_of(a0), self.y_of(p0)
            xb, yb = self.x_of(a1), self.y_of(p1)
            if col != _run_col:
                _flush_run()
                _run_col = col
                _run = [xa, ya]
            if self.step:
                _run += [xb, ya, xb, yb]
            else:
                _run += [xb, yb]
        _flush_run()
        # =224: 階段の「最初の点の前=停止」と「最後の点のあと=保持」も描く
        if self.step and self.model.points:
            fa, fp = self.model.points[0]
            la, lp = self.model.points[-1]
            # =297: 停止の高さは中心(csv=100 / rotate funscript=50 /
            # vibration=0)。pat_center が無ければ従来の定数。
            ys = self.y_of(self.pat_center if self.pat_center is not None
                           else self.pos_center)
            if fa > left:
                self.create_line(max(x0, self.x_of(left)), ys,
                                 min(w, self.x_of(fa)), ys,
                                 fill=wave, width=2, tags="wave")
                self.create_line(self.x_of(fa), ys, self.x_of(fa),
                                 self.y_of(fp), fill=wave, width=2,
                                 tags="wave")
            if self.x_of(la) < w:
                self.create_line(max(x0, self.x_of(la)), self.y_of(lp),
                                 w, self.y_of(lp),
                                 fill=wave, width=2, tags="wave")
        # 点(選択中は大きく・色を変える)。パターンの構成点は緑の丸○
        # (=184。=176のひし形◇は撤回)。端点が共有されている箇所=
        # 二重丸◎(外側緑・内側白)。選択中のパターンの構成点は赤(=189)
        pat_ats = self.model.pattern_ats()
        shared_eps = self.model.shared_endpoints()
        for at, pos, selected in disp:
            x, y = self.x_of(at), self.y_of(pos)
            if x < x0 - 8 or x > w + 8:
                continue
            if at in pat_ats:
                pcol = acc if at in sel_pat_ats else patc
                if at in shared_eps:
                    self.create_oval(x - 5, y - 5, x + 5, y + 5,
                                     fill=pcol, outline="", tags="wave")
                    self.create_oval(x - 2, y - 2, x + 2, y + 2,
                                     fill="#ffffff", outline="",
                                     tags="wave")
                else:
                    self.create_oval(x - 3, y - 3, x + 3, y + 3,
                                     fill=pcol, outline="", tags="wave")
                continue
            r = 5 if selected else 3
            self.create_oval(x - r, y - r, x + r, y + r,
                             fill=acc if selected else wave,
                             outline="", tags="wave")

        # 配置済みパターンの外形(仕様 5.3。linear/twist=平行四辺形 /
        # =228 離散的なスクリプト=矩形)+ハンドル。
        # =176: 実線・1px太く。=180/=181: さらに薄い灰色
        pat_col = self._c(("#d1d1d1", "#4e4e4e"))   # =181: =180のさらに半分
        for idx in range(len(self.model.patterns)):
            band = self._pattern_band(idx)
            if band is None:
                continue
            plo, phi, ptop, pbot = band
            if phi < left - span or plo > left + span * 1.5:
                continue
            xl, xr = self.x_of(plo), self.x_of(phi)
            corners = (xl, self.y_of(ptop[0]), xr, self.y_of(ptop[1]),
                       xr, self.y_of(pbot[1]), xl, self.y_of(pbot[0]))
            selected = (idx == self.sel_pattern
                        or idx in self.model.pattern_selection)
            self.create_polygon(*corners, fill="", outline=pat_col,
                                width=3 if selected else 2)
            if selected and self._drag is None:
                for _code, (hx, hy) in self._handles(idx).items():
                    self.create_rectangle(hx - 3, hy - 3, hx + 3, hy + 3,
                                          fill=pat_col, outline="")
        # =181: 波形(青線)と点をパターンの枠より前面へ
        self.tag_raise("wave")

        # =202: 剛体の掴み領域(点線の長方形)。複数選択(2つ以上)のとき、
        # atの範囲×pos全域を赤の点線で囲む=この中はどこを掴んでも剛体移動
        grng = self._group_box_range()
        if grng is not None and self._drag is None:
            gx0 = max(x0 - 1, self.x_of(grng[0]))
            gx1 = min(w + 1, self.x_of(grng[1]))
            if gx1 > x0 and gx0 < w:
                self.create_rectangle(gx0, top, gx1, bot,
                                      outline=acc, dash=(4, 3), width=1)

        # 移動・拡縮ドラッグ中のゴースト(仕様 4-2)。=175以降は
        # {index: plan} の複数plan(隣接パターンの追従を含む)を全部描く
        gplans = []
        if self._drag and self._drag.get("kind") in ("patmove", "resize"):
            dp = self._drag.get("plan")
            if dp:
                gplans = list(dp.values())
        elif self._drag and self._drag.get("kind") == "groupmove":
            gm = self._drag.get("plan")
            gsc = self._drag.get("scale")
            if gm is not None and \
                    (gm[0] or gm[1] or
                     (gsc is not None and abs(gsc[1] - 1.0) > 1e-9)) and \
                    self._drag["moved"] >= self.CLICK_PX:
                gdat, gdpos = gm

                def _gp(a):                      # =234: 拡縮つき
                    p = self.model.pos_of(a)
                    return (self.model._scaled_pos(p, gsc)
                            if gsc is not None else p + gdpos)

                for gi in sorted(self.model.pattern_selection):
                    if 0 <= gi < len(self.model.patterns) and \
                            self.model.patterns[gi]["ats"]:
                        gplans.append({"points": [
                            (a + gdat, _gp(a))
                            for a in self.model.patterns[gi]["ats"]]})
                for ga in sorted(self.model.selection):
                    gplans.append({"points": [(ga + gdat, _gp(ga))]})
        elif self._ghost is not None:
            gplans = [self._ghost]
            # =221: 上書きになる配置は、**消えるもの**を赤で予告する
            # (クリックする前に何が失われるか分かるように。ユーザー決定)
            if self._ghost.get("overwrite"):
                gp = self._ghost["points"]
                dpats, dats = self.model.overwrite_preview(
                    gp[0][0], gp[-1][0], gp[0][1], gp[-1][1])
                # 選択の色(オレンジ系の赤)とは別の、はっきりした赤にする
                rcol = self._c(("#df1b1b", "#ff5b5b"))
                for pi in sorted(dpats):
                    rec = self.model.patterns[pi]
                    ats = rec["ats"]
                    for i in range(len(ats) - 1):
                        x_a, x_b = self.x_of(ats[i]), self.x_of(ats[i + 1])
                        y_a = self.y_of(self.model.pos_of(ats[i]))
                        y_b = self.y_of(self.model.pos_of(ats[i + 1]))
                        if self.step:
                            # =227: 階段のグラフでは赤い予告も直角で描く
                            # (斜め線になっていた不具合)
                            self.create_line(x_a, y_a, x_b, y_a,
                                             fill=rcol, width=3)
                            self.create_line(x_b, y_a, x_b, y_b,
                                             fill=rcol, width=3)
                        else:
                            self.create_line(x_a, y_a, x_b, y_b,
                                             fill=rcol, width=3)
                for a in sorted(dats):
                    p = self.model.pos_of(a)
                    if p is None:
                        continue
                    x, y = self.x_of(a), self.y_of(p)
                    self.create_oval(x - 4, y - 4, x + 4, y + 4,
                                     fill=rcol, outline=rcol)
        if gplans:
            gcol = self._c(("#1f8a4d", "#4dd68a"))
            for dplan in gplans:
                gp = dplan["points"]
                for i in range(len(gp) - 1):
                    if self.step:       # =224: ゴーストも階段で描く
                        self.create_line(self.x_of(gp[i][0]),
                                         self.y_of(gp[i][1]),
                                         self.x_of(gp[i + 1][0]),
                                         self.y_of(gp[i][1]),
                                         fill=gcol, width=2, dash=(4, 3))
                        self.create_line(self.x_of(gp[i + 1][0]),
                                         self.y_of(gp[i][1]),
                                         self.x_of(gp[i + 1][0]),
                                         self.y_of(gp[i + 1][1]),
                                         fill=gcol, width=2, dash=(4, 3))
                        continue
                    self.create_line(self.x_of(gp[i][0]),
                                     self.y_of(gp[i][1]),
                                     self.x_of(gp[i + 1][0]),
                                     self.y_of(gp[i + 1][1]),
                                     fill=gcol, width=2, dash=(4, 3))
                for a, p in gp:
                    x, y = self.x_of(a), self.y_of(p)
                    self.create_oval(x - 3, y - 3, x + 3, y + 3,
                                     fill="", outline=gcol)
        # =233: マウス位置の十字ガイド(1px)。グラフの端まで伸ばし、
        # 仲間のグラフ(左右のもう片方・サブ)には**縦線だけ**を映す。
        if self.cross is not None and self._drag is None:
            ca, cp = self.cross
            ccol = self._c(("#1f8a4d", "#4dd68a"))
            cx = self.x_of(ca)
            if x0 <= cx <= w:
                self.create_line(cx, top, cx, bot, fill=ccol, width=1)
            if cp is not None:
                cy = self.y_of(cp)
                self.create_line(x0, cy, w, cy, fill=ccol, width=1)

        # 点モードのゴースト(=176: 打点できる位置だけに出る)
        if self._point_ghost is not None and self._drag is None:
            ga, gp_ = self._point_ghost
            x, y = self.x_of(ga), self.y_of(gp_)
            gcol = self._c(("#1f8a4d", "#4dd68a"))
            self.create_oval(x - 4, y - 4, x + 4, y + 4,
                             outline=gcol, width=2, fill="")

        # 矩形選択のラバーバンド
        if self._drag and self._drag.get("kind") == "rect" \
                and self._drag["moved"] >= self.CLICK_PX:
            cx, cy = self._drag["cur"]
            self.create_rectangle(self._drag["x"], self._drag["y"], cx, cy,
                                  outline=self._c(_DG.C_PLAYHEAD),
                                  dash=(3, 2))

        # =232: 右上の見出し(「左（ロータ1）」など。同時編集の目印)
        if self.corner_text:
            self.create_text(w - 6, top - 12, anchor="ne",
                             text=self.corner_text,
                             fill=self._c(_DG.C_AXIS_TEXT),
                             font=(appfont.FAMILY, 9, "bold"))

        # 再生位置(=206: plain モードでは描かない)
        if not self.plain:
            nx = self.x_of(self.now_ms)
            if x0 <= nx <= w:
                self.create_line(nx, top - 12, nx, bot,
                                 fill=self._c(_DG.C_PLAYHEAD), width=1)

        # ヒートマップ(=195): 隣り合う点の区間の速度を色の帯で警告する。
        # speed = |Δpos| ÷ Δt(ms) × 60(TFGと同じ式)。速度0と点の無い
        # 区間は無色。位置は pos=0 の線のすぐ下・時間ラベルの上。
        # =224: csv(ROTATE)は「速度そのもの」を描いているので、
        # ストローク速度の警告は意味を持たない=出さない
        pts_all = self.model.points if self.heat else []
        # =247: 帯の中心を bot+4 → bot+8 へ下げ、pos=0 の打点と重ならない
        # ようにする(最大太さ4px=帯上端 bot+6。選択中の点は半径5px=下端
        # bot+5 なので、1px の隙間で接触しない)。時間ラベルも +4px 下げる。
        hy = bot + 8
        # =209: 高速の警告を明確にするため速度で帯を太くする(=247で色は
        # スライドしたが、しきい値基準は不変: speed≥20=2px太く(赤・
        # オレンジ)/12-20=1px太く(黄・緑)/それ未満=2px)
        heat_w = {}
        for hidx, (_thr, hc) in enumerate(self.HEAT_LEVELS):
            heat_w.setdefault(hc, 4 if hidx <= 1 else (3 if hidx <= 3 else 2))
        # =229: 帯も**同じ色が続くぶんは1本**にまとめる(アイテム数を減らす)
        _hrun = []
        _hcol = None

        def _flush_heat():
            nonlocal _hrun, _hcol
            if _hcol is not None and len(_hrun) >= 4:
                self.create_line(*_hrun, fill=self._c(_hcol),
                                 width=heat_w.get(_hcol, 2))
            _hrun, _hcol = [], None

        for i in range(len(pts_all) - 1):
            a0, p0 = pts_all[i]
            a1, p1 = pts_all[i + 1]
            if a1 < left - span or a0 > left + span * 1.5:
                _flush_heat()
                continue
            hcol = self._heat_color(a0, p0, a1, p1)
            if hcol is None:
                _flush_heat()
                continue
            xa = max(x0, self.x_of(a0))
            xb = min(w, self.x_of(a1))
            if hcol != _hcol:
                _flush_heat()
                _hcol = hcol
                _hrun = [xa, hy]
            _hrun += [xb, hy]
        _flush_heat()

        # 時間軸(=190: 主線に加えて副線の位置にもラベルを出す。
        # 主線=coarse は mid の倍数なので mid 刻みで全部に出る)
        # =210: hh:mm:ss.FFF(12文字)はラベル幅が広いので、ラベル幅+余白が
        # 刻み幅(px)を超えるときは刻みを2倍ずつ粗くして**間引く**
        # (縮尺表示があるので間隔は読み取れる)
        step = mid * 1000.0
        px_per_ms = (x1 - x0) / span
        need = self.label_px() + 8
        while step * px_per_ms < need and step < span:
            step *= 2.0
        self._label_step_ms = step
        t = int(left // step) * step
        while t <= left + span + 1 and self.show_time:
            if t >= 0:
                x = self.x_of(t)
                if x0 <= x <= w:
                    # =247: ヒートマップの帯を下げたぶんラベルも +4px
                    self.create_text(x, bot + 12, anchor="n",
                                     text=self.fmt_time_ms(t),
                                     fill=self._c(_DG.C_AXIS_TEXT),
                                     font=(appfont.FAMILY, 8))
            t += step

        # 縮尺表示(=210): 左上(pos「100」の上)に副線(mid)の間隔を
        # 「0.25秒┗━┛」のように描く。ブラケットの横幅=実際の mid 間隔ぶん
        # (Google Map の縮尺と同じ考え方=長さそのものが縮尺の実感)
        # 基線は「100」ラベル(top の上側に約12px)のさらに上
        if self.show_scale:
            sy = top - 14
            scol = self._c(_DG.C_AXIS_TEXT)
            tid = self.create_text(4, sy, anchor="sw",
                                   text=self.fmt_scale_s(mid), fill=scol,
                                   font=(appfont.FAMILY, 8),
                                   tags=("scale_text",))
            bx0 = self.bbox(tid)[2] + 4
            bw = mid * 1000.0 * px_per_ms
            bx1 = min(float(w - 4), bx0 + bw)
            self.create_line(bx0, sy - 4, bx0, sy, bx1, sy, bx1, sy - 4,
                             fill=scol, width=1, tags=("scale_bar",))

        if not self.follow and not self.plain and self.show_follow_hint:
            self.create_text(w - 4, 2, anchor="ne",
                             text=tr("追従停止中（再生で戻る）"),
                             fill=self._c(("#8f6300", "#e0a23a")),
                             font=(appfont.FAMILY, 8, "bold"))
