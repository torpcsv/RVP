"""メイン画面の共通: 色定数(apptheme.register)・ロガー・表示名/三角アイコン/paced_delay・AsyncRunner。"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import tkinter as tk
from .. import apptheme



# =262: 背景イラスト表示(Pillow)。CustomTkinter の必須依存なので実環境では
# 常に存在するが、作法として optional 扱い(失敗時は機能を静かに無効化)。
try:
    from PIL import Image as PILImage
    from PIL import ImageTk as PILImageTk
    HAS_PIL = True
except Exception:       # pragma: no cover - CTk環境ではPillowは必ず入る
    PILImage = PILImageTk = None
    HAS_PIL = False


# =155: main.py にはロガーが無く、=151 で足した例外ログ(_refresh_theme_widgets
# 等)が**実際に例外を拾ったときだけ NameError になる**状態だった。
# player.py と同じ流儀で用意する(握りつぶす側の処理が壊れないように)。
logger = logging.getLogger("rvp.main")


DEFAULT_INTIFACE_URL = "ws://127.0.0.1:12345"


# ---- カラーパレット ----
ACCENT = "#7c6cf0"        # メインアクセント(紫)


ACCENT_HOVER = "#6a5ae0"


OK_COLOR = "#3ddc84"      # 接続済み・再生中


WARN_COLOR = "#f5a623"    # 一時停止


NEG_COLOR = "#f06292"     # 逆回転の%表示(ピンク)


ERROR_COLOR = "#e05a5a"


# =114: 上の5色は**ダーク背景の上での見え方**で選んだ明るい色なので、
# ライトの薄い背景(gray92〜94)に文字や細線として置くとコントラストが
# 足りず沈む(実測コントラスト比 on gray94: 緑#3ddc84=1.6 / 橙#f5a623=1.8 /
# 桃#f06292=2.7 / 赤#e05a5a=3.2 / 紫#7c6cf0=3.5)。
# **面(ボタン・スライダー・ゾーンバーの塗り)は従来色のまま**にして、
# **文字と細線だけ**ライト用の濃い色を持つ (ライト, ダーク) タプルへ
# 差し替える。**ダーク側は従来値=ダークの見た目は完全に不変**。
ACCENT_TEXT = ("#5245c9", ACCENT)      # 紫 6.0:1


OK_TEXT = ("#1a7c43", OK_COLOR)        # 緑 4.6:1


WARN_TEXT = ("#a06000", WARN_COLOR)    # 橙 4.4:1


NEG_TEXT = ("#c2447f", NEG_COLOR)      # 桃 4.2:1


ERROR_TEXT = ("#c93a3a", ERROR_COLOR)  # 赤 4.4:1


MUTED = "gray62"          # 補助テキスト(枠線・ステータスピル用)


# ラベル文字色: ライト時は黒でくっきり、ダーク時は従来のgray62を維持する。
LABEL = ("black", "gray62")


# 編集画面/コンボの文字色: ライト時は黒、ダーク時はCTk既定の明色を維持する。
COMBO_TEXT = ("black", "#DCE4EE")


# =169(不具合修正): 無効化したコンボの文字色。CTk既定 ("gray74", "gray60") は
# ライトの面(gray75/gray80)と同化して読めない(コントラスト1.02:1)。
COMBO_TEXT_DISABLED = ("gray28", "gray60")


# ---- タブの外観 ----
TAB_HEIGHT = 46          # タブボタンの高さ


TAB_WIDTH = 120          # タブボタンの幅


TAB_OVERLAP = 12         # タブとパネルの重なり量(一体感を出す)


PANEL_COLOR = ("gray88", "gray16")   # パネル＝選択中タブの色


CARD_COLOR = ("gray94", "gray20")     # 再生/デバイス領域のカード面


CARD_BORDER = ("gray70", "gray32")    # カードの枠線


def scenario_display_name(path: str) -> str:
    """画面に出すシナリオの表示名=**拡張子を除いたファイル名**(=153)。

    シナリオの `title` は、もともとファイル名とは別の概念だったが、設計変更で
    **編集画面がファイル名から自動で決める**ようになった(editor._title_from_path。
    タイトル欄も読み取り専用)。その名残で「タイトル — ファイル名.json」と
    二重に出ていたのをやめ、1つに統一する(2026-08-15ユーザー決定)。
    どのファイルを開いているかはシナリオタブのパス表示に残る。

    `sc.title` ではなくパスから作るのは、手書きJSONで title がファイル名と
    食い違っていても表示がぶれないようにするため。
    """
    return os.path.splitext(os.path.basename(path or ""))[0]


def _triangle_photo(direction: str, px: int = 13, dark: bool = True):
    """◀▶の代わりに使う三角アイコン(tk.PhotoImage)を描く。

    direction は "left" / "right"。4x4のスーパーサンプリングで被覆率を求め、
    縁の画素だけ背景色へ寄せた中間色にする(簡易アンチエイリアス)。
    三角の外側は transparency_set で透過させ、ホバー時の背景が透ける
    ようにする。**戻り値は呼び出し側で参照を保持すること**(tkの画像は
    参照が切れると消える)。
    """
    fg = (158, 158, 158) if dark else (0, 0, 0)      # 明=黒 / 暗=gray62相当
    bg = (43, 43, 43) if dark else (235, 235, 235)   # 縁をなじませる背景色
    ss = 4
    n = px * ss
    cy = (n - 1) / 2.0
    x0, x1 = 0.14 * n, 0.86 * n
    img = tk.PhotoImage(width=px, height=px)
    rows = []
    clear = []
    for y in range(px):
        cells = []
        for x in range(px):
            hit = 0
            for sy in range(ss):
                fy = y * ss + sy + 0.5
                t = min(1.0, abs(fy - cy) / (n / 2.0))
                edge = (x0 + (x1 - x0) * t) if direction == "left" \
                    else (x1 - (x1 - x0) * t)
                for sx in range(ss):
                    fx = x * ss + sx + 0.5
                    if (edge <= fx <= x1) if direction == "left" \
                            else (x0 <= fx <= edge):
                        hit += 1
            cov = hit / float(ss * ss)
            if cov <= 0.02:
                cells.append("#%02x%02x%02x" % bg)   # 後で透過にする
                clear.append((x, y))
            elif cov >= 0.98:
                cells.append("#%02x%02x%02x" % fg)
            else:
                cells.append("#%02x%02x%02x" % tuple(
                    int(round(b + (f - b) * cov)) for f, b in zip(fg, bg)))
        rows.append("{" + " ".join(cells) + "}")
    img.put(" ".join(rows))
    for x, y in clear:
        try:
            img.transparency_set(x, y, True)
        except Exception:
            break            # 古いTkで未対応でも背景色のままで実害は小さい
    return img


TAB_IDLE_COLOR = ("gray78", "gray23")


TAB_HOVER_COLOR = ("gray74", "gray26")


def paced_delay(target_ms: int, cost_ms: float) -> int:
    """=229: 次のフレームまでの待ち [ms](再生タブ・レビュー画面で共通)。

    `after()` の待ちは**描画が終わってから**数えるので、target をそのまま
    渡すと実際の周期は `target + 描画時間` になる(60fps 設定でも
    描画に 10ms かかれば 26ms 周期=38fps にしかならない)。そこで

    - **フレームの開始基準**で target を狙う(`target - 描画時間`)
    - ただし**待ちは描画時間を下回らない**(CPU の半分より多くを描画に
      使わない。重い縮尺・遅いPCでタイマーが詰まり、ボタンやスライダーの
      応答まで道連れになるのを防ぐ=104 のコスト適応ペーシングの後継)

    → 描画が target の半分以内で終わる環境では**設定どおりの fps**、
    重い環境では滑らかに落ちる。
    """
    return max(1, int(max(float(target_ms) - cost_ms, cost_ms)))


class AsyncRunner:
    """バックグラウンドスレッドで asyncio イベントループを回す。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


# =134: 色定数・クラス属性(DeviceGraph.C_BG等)のテーマ差し替え登録。
# クラス定義より後(=モジュール末尾)で呼ぶ。
apptheme.register(globals())
