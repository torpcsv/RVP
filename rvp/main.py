"""RVP - Random Voice Player (最小構成版 / CustomTkinter UI)

音声ファイル(.wav) / funscript(.funscript) / シナリオファイル(.json) を素材に、
音声再生と Intiface Central 経由の linear デバイス制御を同期実行するプレーヤー。

起動: python -m rvp.main
依存: pip install customtkinter pygame-ce buttplug-py
"""

import asyncio
import bisect
import logging
import os
import sys
import threading
import time
import tkinter as tk
import warnings
from tkinter import filedialog, messagebox

# ---- 起動時間の計測(=92) ----
# 「python -m rvp.main」から画面表示まで実機で14秒かかるという報告の調査用。
# マークの収集は常時行う(perf_counter1回=実質ゼロコスト)。ログファイル
# (カレントディレクトリの rvp_startup_log.txt)への書き出しは
# 「--startup-log」引数か環境変数 RVP_STARTUP_LOG があるときだけ。
# 注意: python.exe 自体の起動(インタプリタ初期化)はこの計測の外
# (main.py の実行開始が起点)。ログは開発者向けなので i18n 対象外。
_STARTUP_T0 = time.perf_counter()
_STARTUP_MARKS: list = []


def _startup_mark(label: str):
    # =93: 繰り返し呼ばれる場所(履歴チャンク等)にもマークを置いたので、
    # 長時間セッションでリストが育たないよう上限を設ける(起動調査には十分)
    if len(_STARTUP_MARKS) < 400:
        _STARTUP_MARKS.append((label, time.perf_counter() - _STARTUP_T0))


def _startup_log_wanted() -> bool:
    return ("--startup-log" in sys.argv
            or bool(os.environ.get("RVP_STARTUP_LOG")))


# =94: スタックサンプラ用のファイルハンドル(--startup-log 時のみ開く)
_STACKS_FILE = None


def _start_stack_sampler():
    """=94: 2.5秒毎に全スレッドのスタックを rvp_startup_stacks.txt へ書く。

    =93のログで「メインスレッドが約8秒×2回、イベントループごと完全に固まる」
    ことが確定した(250msのハートビートすら発火しない)。固まっている最中に
    メインスレッドが何を実行しているかを、faulthandler の watchdog スレッド
    (C実装=メインスレッドがブロック中でも動く)で直接採取する。=83の
    クラッシュダンプで真因特定に成功したのと同じ手法。
    """
    global _STACKS_FILE
    try:
        import faulthandler
        _STACKS_FILE = open("rvp_startup_stacks.txt", "w", encoding="utf-8")
        _STACKS_FILE.write(
            "RVP 起動スタックサンプル (=96)\n"
            "2.5秒毎の全スレッドスタック。『Thread 0x...(most recent call "
            "first)』のうちMainThreadの最上段が、その瞬間に実行していた場所。\n\n")
        _STACKS_FILE.flush()
        faulthandler.dump_traceback_later(2.5, repeat=True, file=_STACKS_FILE)
    except Exception as e:
        print("stack sampler failed:", e)


def _stop_stack_sampler():
    global _STACKS_FILE
    try:
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        if _STACKS_FILE is not None:
            _STACKS_FILE.close()
            print("stack samples -> rvp_startup_stacks.txt")
    except Exception:
        pass
    _STACKS_FILE = None


def _write_startup_log():
    """収集済みマークを rvp_startup_log.txt へ書き出す(累積秒と区間秒)。

    =93: 250ms毎のハートビートは、**予定どおり発火したもの(区間0.35秒未満)
    は「♥×N回 正常」へ圧縮**し、遅れて発火したもの(=その間イベントループが
    ブロックされていた)だけを1行で残す。
    """
    try:
        lines = ["RVP 起動時間ログ (=96)",
                 f"python {sys.version.split()[0]} / {sys.platform}",
                 "累積秒  (+区間秒)  区間"]
        prev = 0.0
        ok_beats = 0

        def flush_beats(upto):
            nonlocal ok_beats
            if ok_beats:
                lines.append(f"{upto:8.3f}  (+     )  ♥×{ok_beats}回 "
                             "(250ms間隔で正常に発火=ループは動いていた)")
                ok_beats = 0

        for label, t in _STARTUP_MARKS:
            delta = t - prev
            if label == "♥heartbeat" and delta < 0.35:
                ok_beats += 1
                prev = t
                continue
            flush_beats(prev)
            if label == "♥heartbeat":
                lines.append(f"{t:8.3f}  (+{delta:6.3f})  ♥heartbeat遅延 "
                             "(この区間イベントループがブロックされていた)")
            else:
                lines.append(f"{t:8.3f}  (+{delta:6.3f})  {label}")
            prev = t
        flush_beats(prev)
        with open("rvp_startup_log.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("startup log -> rvp_startup_log.txt")
    except Exception as e:      # ログ機構自体で起動を壊さない
        print("startup log write failed:", e)
    _stop_stack_sampler()       # =94: サンプラもここで停止・ファイルを閉じる


_startup_mark("main.py 実行開始(標準ライブラリ import 済み)")

import customtkinter as ctk
_startup_mark("import customtkinter")
import pygame
_startup_mark("import pygame")

from . import scenario_map
from . import appfont
from . import apptheme
from .i18n import LANG, load_config, save_config, set_language, tr
from . import __version__            # =292 公開バージョン
_startup_mark("import scenario_map/i18n")
from .intiface_client import IntifaceClient, probe_ws_port
_startup_mark("import intiface_client(buttplug)")
from . import tcode_client
from .tcode_client import TCodeClient
_startup_mark("import tcode_client(pyserial)")
from .player import ScenarioPlayer
from .rotate_source import load_rotate_source
from .scenario import Scenario, TRACK_ROTATE_A10, TRACK_ROTATE_UFO
from . import winstate
from .winstate import WindowMemory
_startup_mark("import player/scenario ほか")

# =262: 背景イラスト表示(Pillow)。CustomTkinter の必須依存なので実環境では
# 常に存在するが、作法として optional 扱い(失敗時は機能を静かに無効化)。
try:
    from PIL import Image as PILImage
    from PIL import ImageTk as PILImageTk
    HAS_PIL = True
except Exception:       # pragma: no cover - CTk環境ではPillowは必ず入る
    PILImage = PILImageTk = None
    HAS_PIL = False
_startup_mark("import PIL(Pillow)")

logging.basicConfig(level=logging.INFO)
# =155: main.py にはロガーが無く、=151 で足した例外ログ(_refresh_theme_widgets
# 等)が**実際に例外を拾ったときだけ NameError になる**状態だった。
# player.py と同じ流儀で用意する(握りつぶす側の処理が壊れないように)。
logger = logging.getLogger("rvp.main")

# OSからのドラッグ&ドロップ(=270: シナリオタブへのシナリオjson D&D)。
# 編集画面(editor.py =44)と同じ流儀: tkinterdnd2 が無い環境では静かに無効。
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:       # pragma: no cover - 未導入環境では機能ごと無効
    TkinterDnD = None
    DND_FILES = "DND_Files"
_startup_mark("import tkinterdnd2")

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


# =110/=111: ページ切替の◀▶を「文字」ではなく「描いた三角」にする。
#
# customtkinter の既定フォントは **Roboto** で、Roboto は
# U+25C0/U+25B6(◀▶)はもちろん ◄► ▲▼ も一切持っていない(cmap実測)。
# そのためWindowsでは字形が別のフォント(絵文字系)へ落ち、**四角の中に
# 三角がある**見た目になっていた(ユーザー報告)。文字を差し替えても
# 同じフォント事情に左右されるので、三角そのものを画像として描く。
#
# **=111: Pillowは使わない**。=110では PIL で描いたが、**customtkinter 6.0.0 は
# Pillow を必須依存にしていない**ため、ユーザー環境(PIL未導入)で
# `ModuleNotFoundError: No module named 'PIL'` で起動不能になった。
# tkinter標準の PhotoImage へ手で画素を書き込む方式に変更(依存ゼロ)。
# CTkButton は CTkImage 以外の画像もそのまま表示できる(6.0.0で確認)。


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


class FixedBtn(ctk.CTkButton):
    """**幅が文字で変わらない** CTkButton(=228・ユーザーFB2)。

    CTkButton の実体は「キャンバス+文字ラベル」をグリッドで並べた
    tk.Frame で、既定では **ラベルの必要幅がフレームの要求幅を押し広げる**。
    しかも左右の余白列に `corner_radius` ぶんの minsize が入るため、
    実際の幅は **`width=` ではなく `角丸×2 + 文字幅`** になっていた。
    そのため、

    - 再生ボタンは「▶」⇔「❚❚」で幅が変わる(丸→横長の錠剤に伸びる)
    - ↺10 / ↻10 は文字幅・フォント・言語で幅が変わり、狭いときは
      文字が潰れる

    という揺れが起きる(CTkBaseClass は `<Configure>` で実寸を拾って
    描き直すので、**押すたび・レイアウトが動くたびに絵まで変わる**)。

    ここでは
    ①`grid_propagate(False)` でフレームを `width=`/`height=` に固定し、
    ②左右の余白列の minsize を 0 にして**文字を角丸の内側まで使わせる**
    (①だけだと角丸ぶんに挟まれて文字が潰れる)。
    `_create_grid()` は CTkButton 側が角丸・フォント・画像の変更で
    呼び直すので、**そこで上書きする**のが確実。

    注意: 幅は完全に固定なので、`width=` は**一番長い文字が収まる幅**に
    しておくこと(はみ出すぶんは描かれない)。
    """

    def _create_grid(self):
        super()._create_grid()
        self.grid_columnconfigure(0, minsize=0)
        self.grid_columnconfigure(4, minsize=0)
        self.grid_propagate(False)


class TabView(ctk.CTkFrame):
    """左寄せ・大きめのタブバーと、選択タブが一体化するパネルを持つ自作タブ。

    選択中のタブボタンをパネルと同色にし、ボタン下部をパネルに重ねる
    ことで「タブがパネルにつながっている」見た目を実現する。
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        # =155: タブが切り替わったときに呼ぶコールバック(RVPApp が使う)。
        # 引数は切り替わった**後**のタブ名。同じタブを選び直したときは
        # 呼ばれない(set() が先頭で弾く)。
        self.on_change = None

        # パネル(先に作ってタブボタンを上に重ねる)
        # packで上部に (TAB_HEIGHT - TAB_OVERLAP) の余白を空け、
        # その余白+パネル上端に跨るようにタブボタンをplaceする。
        self.panel = ctk.CTkFrame(self, corner_radius=14, fg_color=PANEL_COLOR)
        self.panel.pack(fill="both", expand=True, pady=(TAB_HEIGHT - TAB_OVERLAP, 0))

        self._frames: dict[str, ctk.CTkFrame] = {}
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._current: str | None = None
        self._next_x = 14  # 左端からの開始位置

    def add(self, name: str) -> ctk.CTkFrame:
        btn = ctk.CTkButton(
            self, text=name,
            width=TAB_WIDTH, height=TAB_HEIGHT, corner_radius=12,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=TAB_IDLE_COLOR, hover_color=TAB_HOVER_COLOR,
            text_color=MUTED,
            command=lambda n=name: self.set(n),
        )
        btn.place(x=self._next_x, y=0)
        self._next_x += TAB_WIDTH + 6

        frame = ctk.CTkFrame(self.panel, fg_color="transparent")
        self._frames[name] = frame
        self._buttons[name] = btn

        if self._current is None:
            self.set(name)
        return frame

    def set(self, name: str) -> None:
        if name not in self._frames:
            return
        changed = name != self._current      # =155
        for frame in self._frames.values():
            frame.pack_forget()
        self._frames[name].pack(
            fill="both", expand=True,
            padx=18, pady=(TAB_OVERLAP + 10, 16),
        )
        for n, btn in self._buttons.items():
            if n == name:
                btn.configure(
                    fg_color=PANEL_COLOR, hover_color=PANEL_COLOR,
                    text_color=ACCENT_TEXT,
                )
            else:
                btn.configure(
                    fg_color=TAB_IDLE_COLOR, hover_color=TAB_HOVER_COLOR,
                    text_color=MUTED,
                )
        self._current = name
        # =155: 通知は状態を更新し切ってから(コールバックの中で set() が
        # 呼ばれても矛盾しないように)。失敗してもタブ切替自体は成立させる。
        if changed and callable(self.on_change):
            try:
                self.on_change(name)
            except Exception:
                logger.exception("tab on_change failed: %s", name)


class ZoneBar(tk.Canvas):
    """動作可能域と現在の駆動値を表すバー。

    トラックは黒=スライダー補正内の動作可能域、灰=補正で動作しない領域。

    mode:
      "linear" : 位置ドメイン0-100。set_range=駆動区間。
                 青の塗りが[下限→現在位置]。灰の領域は塗らない。
      "output" : 出力ドメイン0-100%(rotate/vibration用)。左端●=停止マーカー
                 (停止中は緑で点灯)。緑の塗りが[下限→実出力]。灰の領域は塗らない。
                 rotateの回転方向はバーでは表現せず、隣の%表示
                 (正=緑「50%」/負=ピンク「-50%」)で表す。
    """

    INACTIVE = "#5a5a5a"   # 動作しない領域(灰)
    ACTIVE = "#1c1c1c"     # 動作可能域(黒)
    MARKER = "#a0a0a0"     # 停止マーカー(動作中)
    BG = "#333333"         # カード面(dark gray20)に合わせる
    # 未接続トラックのプレビュー表示色。デバイス未所持でも動作イメージを見せる
    # ため動きは描くが、色は不活性の灰系に抑えて「繋がっていない」ことを表す
    # (接続時の青(ACCENT)/緑(OK_COLOR)のような明度は使わない)。
    INACTIVE_FILL = "#8a8a8a"    # 未接続時の駆動値の塗り(灰)
    INACTIVE_MARKER = "#8a8a8a"  # 未接続時の停止マーカー(灰。接続時は緑)

    def __init__(self, master, mode: str, height: int = 12, **kwargs):
        super().__init__(master, height=height, bg=self.BG,
                         highlightthickness=0, **kwargs)
        self.mode = mode
        self.lo = 0
        self.hi = 100
        # linear: 0-100の位置 / output: 0.0〜1.0の実出力(None=停止)
        self.value = None if mode != "linear" else 0
        # 分割表示(ufotwの2ロータ)用の右ロータ値。split=Trueの時のみ使用。
        self.value_r = None
        self.split = False      # True=上下2分割(上=左ロータ/下=右ロータ)
        # False=未接続トラック。動きは描くが塗り/マーカーを灰系にして
        # デバイスが繋がっていないことを表す(プレビュー用)。
        self.enabled = True
        self.fill_color = ACCENT if mode == "linear" else OK_COLOR
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_range(self, lo: int, hi: int):
        lo = max(0, min(100, int(lo)))
        hi = max(lo, min(100, int(hi)))
        if (lo, hi) == (self.lo, self.hi):
            return
        self.lo, self.hi = lo, hi
        self._redraw()

    def set_value(self, value):
        if not self.split and value == self.value:
            return
        self.split = False
        self.value = value
        self._redraw()

    def set_split_values(self, left, right):
        """上下2分割で左右ロータの実出力を表示する(output専用)。"""
        if self.split and left == self.value and right == self.value_r:
            return
        self.split = True
        self.value = left
        self.value_r = right
        self._redraw()

    def set_enabled(self, flag: bool):
        """接続デバイスの有無を反映する(False=灰一色・塗りなし)。"""
        flag = bool(flag)
        if flag == self.enabled:
            return
        self.enabled = flag
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 8:
            return

        x0 = 8 if self.mode == "output" else 1
        span = w - 1 - x0

        # 未接続でも動作イメージのプレビューとして動きは描く。塗り/マーカーの
        # 色だけ _draw_band が enabled で切り替える(未接続=灰系)。

        if self.mode == "output" and self.split:
            # 上下2分割(上=左ロータ / 下=右ロータ)。縦幅は広げない。
            mid = h // 2
            self._draw_band(x0, span, w, 1, mid - 1, self.value)
            self._draw_band(x0, span, w, mid + 1, h - 1, self.value_r)
        else:
            self._draw_band(x0, span, w, 1, h - 1, self.value)

    def _draw_band(self, x0, span, w, y0, y1, value):
        """縦帯 [y0,y1] に1本ぶんのバー(動作可能域+塗り+停止マーカー)を描く。"""
        if y1 <= y0:
            return

        # 接続時は青/緑の明色、未接続時は灰系(動きは見せるが繋がっていない
        # ことが分かるように)。
        fill = self.fill_color if self.enabled else self.INACTIVE_FILL
        stop_marker = OK_COLOR if self.enabled else self.INACTIVE_MARKER

        def zone(a, b, color):
            if b > a:
                self.create_rectangle(a, y0, b, y1, fill=color, outline="")

        def x_of(pct):
            return x0 + span * pct / 100

        zone(x0, x_of(self.lo), self.INACTIVE)
        zone(x_of(self.lo), x_of(self.hi), self.ACTIVE)
        zone(x_of(self.hi), w - 1, self.INACTIVE)

        if self.mode == "linear":
            pos = max(self.lo, min(self.hi, float(value or 0)))
            zone(x_of(self.lo), x_of(pos), fill)
            return
        # output
        stopped = not value
        if not stopped:
            pct = max(0.0, min(1.0, abs(float(value)))) * 100
            pct = max(self.lo, min(self.hi, pct))
            zone(x_of(self.lo), x_of(pct), fill)
        mcolor = stop_marker if stopped else self.MARKER
        self.create_oval(1, y0, 7, y1, fill=mcolor, outline="")


class DeviceGraph(tk.Canvas):
    """③グラフ表示(=69): 実行中スクリプトの波形を共通の時間軸に描く。

    横軸=イベント入場からの経過時間 / 縦軸=pos(0-100)。デバイス種別ごとに
    1本のグラフを縦に並べ、時間の縮尺と縦の基準線・時間ラベルは全体で共有する。

    - linear は始点・終点の間も動き続けるので**斜め線**(三角波のような見た目)
    - rotate / vibration は次の指示まで同じ強さを保つので**直角の線**(矩形波)
    - ホイールで時間縮尺を9段階に拡大縮小 / ドラッグで自動追従を外して移動 /
      単クリックで自動追従へ復帰。
    """

    # (細線, 中線, 太線) の間隔(秒)。表示幅は常に「太線×2.5」= 細線×25。
    # 縮尺段階(=176で細分化)。各段は (細線, 中線, 太線) の秒数で、
    # 表示幅 = 太線×2.5。従来の9段の**それぞれの間に中間段**を足し、
    # 最大拡大側へさらに1段(0.5=従来の2倍拡大)を追加した。
    # どの段も (太線/10, 太線/2, 太線) の関係(従来と同じ規則)。
    # =226: 中ほどの刻み(=縮尺表示に出る副線の間隔)を
    # **1.5秒→2秒 / 2.5秒→3秒 / 3.5秒→4秒** へ(ユーザー要望9)。
    # LEVELS は (coarse/10, coarse/2, coarse) なので、coarse を
    # 3→4 / 5→6 / 7→8 に変えると副線が 1.5/2.5/3.5 → 2/3/4 になる。
    # 編集グラフ(script_edit)も再生タブもこの LEVELS を共有している。
    # =231: 最大拡大の縮尺表示を **0.25秒 → 0.2秒**(coarse 0.5 → 0.4)
    _COARSE = (0.4, 1.0, 1.5, 2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0,
               30.0, 40.0, 60.0, 100.0, 200.0, 300.0, 600.0, 900.0)
    LEVELS = tuple((c / 10.0, c / 2.0, c) for c in _COARSE)
    DEFAULT_LEVEL = 7          # [1秒/5秒/10秒] = 表示幅25秒(従来と同じ)

    # 表示順(上から)。実行中の種別だけを表示する。
    ORDER = ("linear", "twist", "rotate_ufo", "rotate_ufo_r",
             "rotate_a10", "rotate_a10_r", "vibration")

    GUTTER = 30      # 左端の位置目盛(100/50/0)欄
    HEAD_H = 15      # 各グラフの見出し行(種別名)
    AXIS_H = 18      # 下端の時間軸
    ROW_GAP = 6      # グラフ間の余白
    # グラフ1本ぶんの高さ(**固定**。ユーザー決定=本数で高さを変えない)。
    # 690x820 のウィンドウで4本がちょうど収まる値。5本になったときは
    # はみ出すので、上下方向のドラッグで送って見る(=横のドラッグと同じ操作)。
    ROW_H = 87
    CLICK_PX = 5     # この移動量未満の押下は「単クリック」

    # 色(ライト, ダーク)
    C_BG = ("gray94", "#333333")
    # =191: ライト系(ライト+カラーテーマ6色)で線が視認できないため、
    # ライト側だけ一段ずつ濃くした(細=旧中 / 中=旧太 / 太=さらに一段濃く。
    # ただし再生位置の線 C_PLAYHEAD #404040 より薄いこと)。ダーク側は不変。
    C_GRID_POS = ("#9e9e9e", "#6e6e6e")     # 位置の基準線 0/50/100
    C_GRID_POS_SUB = ("#c6c6c6", "#484848")  # 位置の基準線 25/75
    # 時間の基準線(細, 中, 太)
    C_GRID_T = (("#d2d2d2", "#3d3d3d"), ("#bcbcbc", "#4e4e4e"),
                ("#a6a6a6", "#666666"))
    C_HEAD = ("gray35", "gray70")
    C_AXIS_TEXT = ("gray30", "gray72")
    C_PLAYHEAD = ("#404040", "#d8d8d8")

    # =102: 1枚表示(集約)のデバイス色(ライト, ダーク)。全種別を同一平面に
    # 重ねるため、種別ごとに色相を分けて識別できるようにする。
    # =114: **ライト側だけ**さらに濃くした(ユーザー指摘「ライトのグラフの線が
    # 背景に近くて見づらい」)。背景 gray94(#f0f0f0)に対するコントラスト比を
    # 全色 4.5:1 前後へ揃える(旧: 橙3.0 / 黄3.1 / 緑3.6 / 青3.8 と、
    # 基準線(#b4b4b4=1.9)との差が小さく波形が浮き上がっていなかった)。
    # **ダーク側は従来値のまま**。色相の割り当ても不変。
    C_KEY = {
        "linear":       ("#5245c9", "#8f84ff"),   # 紫 6.0:1
        "twist":        ("#0e6fa8", "#4db8ff"),   # 青 4.8:1
        "rotate_ufo":   ("#9c6000", "#ffb340"),   # 橙 4.5:1
        "rotate_ufo_r": ("#6b6f00", "#d8dd45"),   # 黄 4.7:1
        "rotate_a10":   ("#b03a72", "#ff7ab8"),   # 桃 5.0:1
        "rotate_a10_r": ("#9c382a", "#ff8a70"),   # 赤茶 6.1:1
        "vibration":    ("#1a7c43", "#3ddc84"),   # 緑 4.6:1
    }
    # =114: 個別表示の波形色。従来は ACCENT(紫)/OK_COLOR(緑)の
    # **ダーク用の明るい色をライトでもそのまま**使っていたため、緑が
    # 1.6:1(ほぼ背景と同化)・紫が3.5:1しかなかった。ライトだけ濃くする
    # (色は C_KEY の linear/vibration と同値=1枚表示と印象を揃える)。
    C_WAVE_POS = ("#5245c9", ACCENT)     # linear / twist
    C_WAVE_SPEED = ("#1a7c43", OK_COLOR)  # rotate / vibration

    def __init__(self, master, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, height=1, **kwargs)
        self.level = self.DEFAULT_LEVEL
        self.overlay = False          # =102: True=1枚表示(全種別を重ねる)
        self.minus = False            # =103: True=再生位置より右(将来)を描かない
        self.follow = True            # 再生位置を中央に追従させる
        self.view_ms = 0.0            # 画面中央の時刻(ms)
        self.view_y = 0.0             # 縦の送り量(5本ではみ出したときだけ)
        self._over_y = 0.0            # 縦のはみ出し量(0=全部見えている)
        self.snapshot = {"now_ms": 0.0, "segments": []}
        self._drag = None
        # =237: 右ダブルクリック=再生位置をそこへ(編集グラフ=192の横展開)。
        # 呼び出し側が「グラフの時間軸のms」を受け取るコールバックを差す。
        # None のままなら何も起きない。
        self.on_seek = None
        # =243: 音声波形の帯(レビュー画面だけが設定する。再生タブは None)。
        # wave_env={"bucket_ms","chans","mono","peak"} / wave_mode=
        # "off"/"mono"/"stereo"/"stereo_rev" / wave_offset=グラフ 0ms の
        # 素材時刻(レビューは音声区間の開始)。ステレオは各行を上下半分に
        # 分けて上=L・下=R。**左右2行の組(rotate_ufo と rotate_ufo_r 等)が
        # 両方あるときは、上の行=L・下の行=R を全高で**描く(ユーザー指定。
        # 「(逆)」はどちらも割り当てを入れ替える)。
        self.wave_env = None
        self.wave_mode = "off"
        self.wave_offset = 0.0
        self.configure(bg=self._c(self.C_BG))
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-3>", self._on_double3)

    # ---- 小道具 ----

    @staticmethod
    def _dark() -> bool:
        return ctk.get_appearance_mode() != "Light"

    def _c(self, pair):
        return pair[1] if self._dark() else pair[0]

    @staticmethod
    def fmt_time(sec: float) -> str:
        """時間ラベル「Y分M秒」。"""
        s = int(round(abs(sec)))
        return ("-" if sec < 0 else "") + tr("{0}分{1}秒").format(s // 60, s % 60)

    def span_ms(self) -> float:
        """画面の横幅が表す時間(ms)= 太線間隔×2.5。"""
        return self.LEVELS[self.level][2] * 2.5 * 1000.0

    def visible_keys(self) -> list:
        """表示するグラフの種別(実行中/実行済みの種別のみ)。"""
        present = {s["key"] for s in self.snapshot.get("segments", ())}
        return [k for k in self.ORDER if k in present]

    def key_label(self, key: str, keys) -> str:
        if key == "linear":
            return "LINEAR"
        if key == "twist":
            return "TWIST"
        if key == "vibration":
            return "VIBRATION"
        base = "ROTATE(ufo)" if key.startswith("rotate_ufo") else "ROTATE(a10)"
        if key.endswith("_r"):
            return base + " " + tr("右")
        if key + "_r" in keys:
            return base + " " + tr("左")
        return base

    # ---- 外部API ----

    def set_snapshot(self, snap: dict):
        self.snapshot = snap
        if self.follow:
            self.view_ms = float(snap.get("now_ms", 0.0))
        self._redraw()

    def set_level(self, level: int):
        level = max(0, min(len(self.LEVELS) - 1, int(level)))
        if level != self.level:
            self.level = level
            self._redraw()

    def set_overlay(self, flag: bool):
        """=102: 1枚表示(集約)⇄個別表示を切り替える。"""
        flag = bool(flag)
        if flag != self.overlay:
            self.overlay = flag
            self.view_y = 0.0
            self._redraw()

    # =127: マイナス表示では再生位置(基準線)を右端付近へ寄せる。
    # 右側(将来)を描かないため、中央配置だと右半分が空白になっていた。
    # 1.0=右端ぴったりではなく、少し余裕を残す(ユーザー要望)。
    MINUS_ANCHOR = 0.92

    def set_minus(self, flag: bool):
        """=103: マイナス表示=再生位置より右(将来)の波形を描かない。

        実行済みの軌跡だけがリアルタイムに残り、現在値は縦線上で上下に
        動き続けて見える(=127で縦線は右端付近)。基準線・時間軸・凡例は
        通常どおり描く。
        """
        flag = bool(flag)
        if flag != self.minus:
            self.minus = flag
            self._redraw()

    # ---- 操作 ----

    def _on_wheel(self, event):
        up = getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4
        # 上=拡大(細かい縮尺へ) / 下=縮小
        self.set_level(self.level + (-1 if up else 1))
        return "break"

    def _on_press(self, event):
        self._drag = {"x": event.x, "y": event.y, "view": self.view_ms,
                      "vy": self.view_y, "moved": 0}

    def _on_drag(self, event):
        if self._drag is None:
            return
        dx = event.x - self._drag["x"]
        dy = event.y - self._drag["y"]
        self._drag["moved"] = max(self._drag["moved"], abs(dx), abs(dy))
        if self._drag["moved"] < self.CLICK_PX:
            return
        w = max(1, self.winfo_width() - self.GUTTER - 4)
        self.follow = False
        self.view_ms = self._drag["view"] - dx * (self.span_ms() / w)
        # 縦: グラフが5本ではみ出しているときだけ送れる(はみ出し0なら無効)
        self.view_y = max(0.0, min(self._over_y, self._drag["vy"] - dy))
        self._redraw()

    def _on_release(self, _event):
        if self._drag is not None and self._drag["moved"] < self.CLICK_PX:
            # 単クリック=自動追従へ復帰(縦の送りも先頭へ戻す)
            self.follow = True
            self.view_ms = float(self.snapshot.get("now_ms", 0.0))
            self.view_y = 0.0
            self._redraw()
        self._drag = None

    def ms_of(self, x: float) -> float:
        """画面のx座標が指すグラフの時刻(ms)。=237

        描画(`_redraw` の `x_of`)の逆写像。マイナス表示のときの基準位置
        (MINUS_ANCHOR)も含めて同じ式を使う。
        """
        plot_w = max(1.0, float(self.winfo_width() - self.GUTTER - 4))
        span = self.span_ms()
        anchor = self.MINUS_ANCHOR if self.minus else 0.5
        t_left = self.view_ms - span * anchor
        return t_left + (float(x) - self.GUTTER) * (span / plot_w)

    def _on_double3(self, event):
        """=237: 右ダブルクリック=再生位置をそこへ(編集グラフ=192と同じ操作)。

        グラフの時間軸の ms を `on_seek` へ渡すだけで、実際に何を動かすかは
        呼び出し側が決める(レビュー画面=そのまま絶対位置 / 再生タブ=
        現在位置との差分ぶんの相対シーク)。**自動追従の状態は変えない**
        (追従停止中に押しても停止のまま=ユーザー決定)。
        """
        self._drag = None            # パンのドラッグ状態は捨てる
        if callable(self.on_seek):
            self.on_seek(self.ms_of(event.x))
        return "break"

    # ---- 描画 ----

    def _redraw(self):
        self.delete("all")
        self.configure(bg=self._c(self.C_BG))
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 40:
            return

        keys = self.visible_keys()
        rows = keys or [""]          # 未再生でも基準線だけのグラフを1本出す
        visible_h = h - self.AXIS_H
        if self.overlay:
            # =102: 1枚表示=画面全体を1つのグラフが覆う(縦の送りなし)
            row_h = 0
            self._over_y = 0.0
            self.view_y = 0.0
            axis_top = visible_h
        else:
            # 1本あたりの高さは**固定**(本数で変えない=ユーザー決定)。5本に
            # なって入りきらないときだけ、縦のドラッグで送って見る。
            row_h = self.ROW_H
            content_h = row_h * len(rows)
            self._over_y = max(0.0, content_h - visible_h)
            self.view_y = max(0.0, min(self._over_y, self.view_y))
            axis_top = min(content_h - self.view_y, visible_h)
        x0 = self.GUTTER
        plot_w = w - self.GUTTER - 4
        span = self.span_ms()
        # =127: view_ms(追従時=now)を置く位置。通常=中央 / マイナス表示=
        # 右端付近(右の将来側を描かないぶん、過去の軌跡へ幅を使う)
        anchor = self.MINUS_ANCHOR if self.minus else 0.5
        t_left = self.view_ms - span * anchor
        ppm = plot_w / span              # px / ms

        def x_of(t):
            return x0 + (t - t_left) * ppm

        fine, mid, coarse = self.LEVELS[self.level]

        # ---- =243: 音声波形の帯(基準線・波形の背面=最初に描く) ----
        if self.wave_env and self.wave_mode != "off" and not self.overlay:
            self._draw_audio_rows(rows, row_h, t_left, ppm,
                                  axis_top, x0, w)

        # ---- 時間の基準線(細→中→太の順に上書き) ----
        for step_s, color in ((fine, self._c(self.C_GRID_T[0])),
                              (mid, self._c(self.C_GRID_T[1])),
                              (coarse, self._c(self.C_GRID_T[2]))):
            step = step_s * 1000.0
            k = int(t_left // step)
            t = k * step
            while t <= t_left + span + 1:
                x = x_of(t)
                if x0 - 1 <= x <= w:
                    self.create_line(x, 0, x, axis_top, fill=color)
                t += step

        # ---- 各グラフ ----
        if self.overlay:
            self._draw_overlay(keys, x_of, x0, w, plot_w, t_left, span,
                               axis_top)
            rows = []      # 個別行は描かない(以降の共通処理だけ実行)
        for i, key in enumerate(rows):
            top = i * row_h - self.view_y
            p_top = top + self.HEAD_H
            p_bot = top + row_h - self.ROW_GAP
            if p_bot - p_top < 8 or p_bot < 0 or p_top > axis_top:
                continue        # 画面の外(縦の送りで隠れている行)

            def y_of(pos, _t=p_top, _b=p_bot):
                return _b - (max(0.0, min(100.0, pos)) / 100.0) * (_b - _t)

            # 位置の基準線(25/75 は最も薄く、0/50/100 はその次)
            for pos in (25, 75):
                y = y_of(pos)
                self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS_SUB))
            for pos in (0, 50, 100):
                y = y_of(pos)
                self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS))

            # 見出し(種別名)と位置目盛
            if key:
                self.create_text(2, top + self.HEAD_H / 2, anchor="w",
                                 text=self.key_label(key, keys),
                                 fill=self._c(self.C_HEAD),
                                 font=(appfont.FAMILY, 9, "bold"))
            gx = self.GUTTER - 4
            for pos, anc in ((100, "ne"), (50, "e"), (0, "se")):
                self.create_text(gx, y_of(pos), anchor=anc, text=str(pos),
                                 fill=self._c(self.C_AXIS_TEXT), font=(appfont.FAMILY, 8))

            if key:
                color = self._c(self.C_WAVE_POS if key in ("linear", "twist")
                                else self.C_WAVE_SPEED)
                for seg in self.snapshot.get("segments", ()):
                    if seg["key"] != key:
                        continue
                    self._draw_wave(seg, x_of, y_of, x0, w, plot_w,
                                    t_left, span, color)

        # はみ出した行が時間軸へかぶらないように下端を塗り潰す
        if axis_top < h:
            self.create_rectangle(0, axis_top, w, h,
                                  fill=self._c(self.C_BG), outline="")

        # ---- 再生位置(現在のデバイス位置を示す縦線) ----
        now_x = x_of(float(self.snapshot.get("now_ms", 0.0)))
        if x0 <= now_x <= w:
            # =112: 太さ2→1(ユーザー要望「1pxのほうがいい感じ」)。色は不変。
            self.create_line(now_x, 0, now_x, axis_top,
                             fill=self._c(self.C_PLAYHEAD), width=1)

        # ---- 時間軸(太線の位置にラベル) ----
        step = coarse * 1000.0
        t = int(t_left // step) * step
        while t <= t_left + span + 1:
            if t >= 0:
                x = x_of(t)
                if x0 <= x <= w:
                    self.create_text(x, axis_top + 2, anchor="n",
                                     text=self.fmt_time(t / 1000.0),
                                     fill=self._c(self.C_AXIS_TEXT),
                                     font=(appfont.FAMILY, 8))
            t += step

        if not self.follow:
            # =102: 1枚表示では上端が凡例行のため、その下に出す
            fy = self.HEAD_H + 2 if self.overlay else 2
            self.create_text(w - 4, fy, anchor="ne",
                             text=tr("追従停止中（クリックで戻る）"),
                             fill=self._c(WARN_TEXT), font=(appfont.FAMILY, 8, "bold"))

        # グラフが5本で入りきらないときの上下の送り(ドラッグ)の目印
        if self._over_y > 0:
            arrow = ("▲" if self.view_y > 0 else "") + \
                    ("▼" if self.view_y < self._over_y else "")
            if arrow:
                self.create_text(w - 2, axis_top + 2, anchor="ne", text=arrow,
                                 fill=self._c(self.C_HEAD), font=(appfont.FAMILY, 9))

    def _draw_audio_rows(self, rows, row_h, t_left, ppm,
                         axis_top, x0, w):
        """=243: 各行の背景へ音声波形の帯を描く(基準線より先に呼ぶ)。

        ステレオは行を上下半分に分けて上=L・下=R。**左右2行の組**
        (例: rotate_ufo と rotate_ufo_r が両方出ている)は、上の行=L・
        下の行=R を**全高で**描く。「(逆)」はどちらも入れ替え。モノラルは
        合成chを全高で描く。はみ出しは軸下の塗り潰し(既存)が隠す。
        """
        from . import script_edit as _se     # 遅延import(循環回避)
        env = self.wave_env
        color = self._c(_se.WAVE_BAND_COLOR)
        bucket, peak, chans = env["bucket_ms"], env["peak"], env["chans"]
        mode = self.wave_mode
        rev = mode == "stereo_rev"
        off = float(self.wave_offset)

        def ms_of_px(x):
            return t_left + (x - x0) / ppm + off

        # 左右2行の組(key と key+"_r" が両方出ている)を集める
        pair_l = {k for k in rows if k and not k.endswith("_r")
                  and (k + "_r") in rows}
        pair_r = {k + "_r" for k in pair_l}
        for i, key in enumerate(rows):
            top = i * row_h - self.view_y
            p_top = top + self.HEAD_H
            p_bot = top + row_h - self.ROW_GAP
            if p_bot - p_top < 8 or p_bot < 0 or p_top > axis_top:
                continue
            if mode == "mono" or len(chans) < 2:
                _se.draw_audio_band(self, env["mono"], bucket, peak,
                                    x0, w, ms_of_px, (p_top + p_bot) / 2.0,
                                    (p_bot - p_top) / 2.0, color)
                continue
            a, b = (1, 0) if rev else (0, 1)
            if key in pair_l or key in pair_r:
                # 上の行(…)=L / 下の行(…_r)=R を全高で
                ch = chans[b if key in pair_r else a]
                _se.draw_audio_band(self, ch, bucket, peak, x0, w,
                                    ms_of_px, (p_top + p_bot) / 2.0,
                                    (p_bot - p_top) / 2.0, color)
                continue
            mid = (p_top + p_bot) / 2.0
            half = (p_bot - p_top) / 4.0
            _se.draw_audio_band(self, chans[a], bucket, peak, x0, w,
                                ms_of_px, (p_top + mid) / 2.0, half, color)
            _se.draw_audio_band(self, chans[b], bucket, peak, x0, w,
                                ms_of_px, (mid + p_bot) / 2.0, half, color)

    def _draw_overlay(self, keys, x_of, x0, w, plot_w, t_left, span,
                      axis_top):
        """=102: 1枚表示。全種別の波形を1つの座標平面へ色分けして重ねる。

        位置の基準線・位置目盛は1組だけ描き、上端に色分けの凡例を置く。
        線の色は C_KEY(種別ごとに色相を分ける)。描画そのものは個別表示と
        同じ _draw_wave を共有する(=71のB案=窓で重なりを防ぐ、も共通)。
        """
        p_top = self.HEAD_H + 2
        p_bot = axis_top - 4
        if p_bot - p_top < 8:
            return

        def y_of(pos):
            return p_bot - (max(0.0, min(100.0, pos)) / 100.0) * (p_bot - p_top)

        # 位置の基準線(25/75 は最も薄く、0/50/100 はその次)
        for pos in (25, 75):
            y = y_of(pos)
            self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS_SUB))
        for pos in (0, 50, 100):
            y = y_of(pos)
            self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS))
        gx = self.GUTTER - 4
        for pos, anc in ((100, "ne"), (50, "e"), (0, "se")):
            self.create_text(gx, y_of(pos), anchor=anc, text=str(pos),
                             fill=self._c(self.C_AXIS_TEXT), font=(appfont.FAMILY, 8))
        # 凡例(種別名を各色で左上に横並び)
        lx = x0
        for key in keys:
            item = self.create_text(
                lx, self.HEAD_H / 2, anchor="w",
                text=self.key_label(key, keys),
                fill=self._c(self.C_KEY.get(key, self.C_HEAD)),
                font=(appfont.FAMILY, 9, "bold"))
            box = self.bbox(item)
            lx = (box[2] if box else lx + 60) + 12
        # 波形(種別ごとの色で同一平面へ重ねる)
        for key in keys:
            color = self._c(self.C_KEY.get(key, self.C_HEAD))
            for seg in self.snapshot.get("segments", ()):
                if seg["key"] != key:
                    continue
                self._draw_wave(seg, x_of, y_of, x0, w, plot_w,
                                t_left, span, color)

    def _draw_wave(self, seg, x_of, y_of, x0, w, plot_w, t_left, span, color):
        """1つのスクリプト断片を描く(表示範囲だけを切り出して描画)。

        =71: 切り出しは**点の番号ではなく時間(x)で行い、窓の境界をまたぐ線は
        補間して繋ぐ**。以前は番号で挟み込んでいたため、シーク位置が2点の
        あいだに落ちると手前の点ごと捨てられ、次の点までの線が丸ごと消えて
        「波形が欠けた」ように見えていた(CSVのように点がまばらだと顕著)。
        窓は seg["x0"] 〜 seg["x1"](同じ行の次の断片の開始位置)で、
        断片どうしの重なりはこれで防ぐ。
        """
        pts = seg["points"]
        times = seg.get("times") or [p[0] for p in pts]
        t0 = seg["t0"]
        # 描いてよい時間の窓(イベント経過)を表示範囲と突き合わせる
        wl = t_left if seg.get("x0") is None else max(t_left, seg["x0"])
        wr = t_left + span if seg.get("x1") is None \
            else min(t_left + span, seg["x1"])
        if self.minus:
            # =103: マイナス表示=窓の右端を再生位置で打ち切る(将来を描かない)。
            # 窓の境界は=71の補間で繋がるため、波形はちょうど再生位置の
            # 現在値で終わり、リアルタイムに描き足されていくように見える
            wr = min(wr, float(self.snapshot.get("now_ms", 0.0)))
        if wr <= wl:
            return
        lo_t, hi_t = wl - t0, wr - t0          # スクリプト時間へ換算
        # 境界をまたぐ線を残すため前後1点を含めて切り出す
        lo = max(0, bisect.bisect_right(times, lo_t) - 1)
        hi = min(len(pts), bisect.bisect_left(times, hi_t) + 1)
        view = pts[lo:hi]
        if not view:
            return
        step = (seg["kind"] == "step")
        # =104: 前段の粗間引き。可視点が1pxあたり6点を超えるときはストライドで
        # 落としてから処理する(境界補間・エンベロープの入力として1pxに
        # 約6サンプルあれば見た目は変わらない)。これで _clip_view と
        # エンベロープのPythonループが縮尺によらず plot_w*6 前後で頭打ちになり、
        # 60fps(=104)でも最大縮尺で1フレーム数msに収まる。
        cap = max(64, int(plot_w) * 6)
        if len(view) > cap:
            stride = len(view) // cap
            if stride > 1:
                last = view[-1]
                view = view[::stride]
                if view[-1] is not last:
                    view.append(last)   # 右端の点は保持(末尾延長・補間用)
        view = self._clip_view(view, lo_t, hi_t, step)
        if len(view) < 2:
            return

        if len(view) > plot_w * 3:
            # 点が多すぎるときは1px単位の最小/最大の帯(エンベロープ)にする。
            # =104: x_of/y_of(クロージャ)を点ごとに呼ばず、算術をインライン化
            # +バケツは生のposで持ち、y変換はピクセルごとに2回だけにする
            ppm = plot_w / span
            base = x0 + (t0 - t_left) * ppm
            buckets = {}
            bget = buckets.get
            for t, p in view:
                px = int(base + t * ppm)
                b = bget(px)
                if b is None:
                    buckets[px] = [p, p]
                elif p < b[0]:
                    b[0] = p
                elif p > b[1]:
                    b[1] = p
            coords = []
            for px in sorted(buckets):
                pmin, pmax = buckets[px]
                coords += [px, y_of(pmax), px, y_of(pmin)]
            if len(coords) >= 4:
                self.create_line(*coords, fill=color, width=1)
            return

        coords = []
        prev_y = None
        for t, p in view:
            x = x_of(t0 + t)
            y = y_of(p)
            if step and prev_y is not None:
                coords += [x, prev_y]     # 次の指示までは同じ強さ(直角の線)
            coords += [x, y]
            prev_y = y
        if len(coords) >= 4:
            self.create_line(*coords, fill=color, width=2,
                             capstyle="round", joinstyle="round")

    @staticmethod
    def _clip_view(view, lo_t, hi_t, step):
        """点列を [lo_t, hi_t] の窓へ切り詰める(境界の値は補間で作る)。

        step(rotate/vibration)は「次の指示まで同じ値」なので手前の点の値を
        そのまま境界へ持ってくる。linear は2点を結ぶ直線上の値を求める。
        """
        out = []
        prev = None
        for t, p in view:
            if t < lo_t:
                prev = (t, p)
                continue
            if prev is not None:            # 窓の左端を作る
                pt, pp = prev
                if step or t == pt:
                    out.append((lo_t, pp))
                else:
                    r = (lo_t - pt) / (t - pt)
                    out.append((lo_t, pp + (p - pp) * r))
                prev = None
            if t > hi_t:                    # 窓の右端を作って終わり
                if out:
                    lt, lp = out[-1]
                    if step or t == lt:
                        out.append((hi_t, lp))
                    else:
                        r = (hi_t - lt) / (t - lt)
                        out.append((hi_t, lp + (p - lp) * r))
                return out
            out.append((t, p))
        if out and out[-1][0] < hi_t and step:
            # 最後の指示は次が来るまで続く(実行中の断片の右端まで伸ばす)
            out.append((hi_t, out[-1][1]))
        return out


class RangeSlider(tk.Canvas):
    """1本のバーに下限・上限の2つのハンドルを持つレンジスライダー。

    ---○-------○----  のように、区間をひとつのバーで指定する。
    値は step 刻みにスナップされる。

    操作(=87・ユーザー依頼3):
      - ○(ハンドル)をつかむ … その端だけを動かす(従来どおり)
      - ハンドルの間のバーをつかむ … **下限と上限の間隔を維持したまま
        区間全体を平行移動**する(0-50 → 20-70 → 40-90 のように)
      - 区間の外側をクリック … 近い方のハンドルがそこへ動く(従来どおり)
    """

    TRACK_COLOR = "#3d3d3d"
    RANGE_COLOR = ACCENT
    HANDLE_COLOR = ACCENT
    HANDLE_OUTLINE = "#d9d4ff"
    # 背景色: ダークは従来の暗色。ライトは周囲のカード面(CARD_COLOR明=gray94)に
    # 溶け込ませて、補正スライダーが浮いて見えないようにする。
    BG_DARK = "#292929"
    BG_LIGHT = "gray94"

    def _bg_color(self):
        return self.BG_LIGHT if ctk.get_appearance_mode() == "Light" \
            else self.BG_DARK

    def __init__(self, master, from_=0, to=100, step=5,
                 command=None, height=26, **kwargs):
        super().__init__(
            master, height=height, bg=self._bg_color(),
            highlightthickness=0, bd=0, **kwargs,
        )
        self.from_ = from_
        self.to = to
        self.step = step
        self.command = command
        self.val_min = from_
        self.val_max = to
        self.enabled = True     # False=未接続トラック(灰色表示・操作不可)
        self._drag = None       # "min" / "max" / None
        self._radius = 8        # ハンドル半径
        self._pad = self._radius + 2

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_enabled(self, flag: bool):
        """接続デバイスの有無を見た目に反映する(False=灰色表示)。

        操作(ドラッグ)は enabled に関わらず常に可能。未接続でも灰色のまま
        補正区間を調整できる(デバイス未所持でのプレビュー用)。
        """
        flag = bool(flag)
        if flag == self.enabled:
            return
        self.enabled = flag
        self._redraw()

    # ---- 座標変換 ----

    def _val_to_x(self, val):
        w = max(self.winfo_width(), 1)
        usable = w - self._pad * 2
        return self._pad + (val - self.from_) / (self.to - self.from_) * usable

    def _x_to_val(self, x):
        w = max(self.winfo_width(), 1)
        usable = max(w - self._pad * 2, 1)
        ratio = (x - self._pad) / usable
        raw = self.from_ + ratio * (self.to - self.from_)
        snapped = round(raw / self.step) * self.step
        return max(self.from_, min(self.to, int(snapped)))

    # ---- 描画 ----

    def _redraw(self):
        self.configure(bg=self._bg_color())   # テーマ切替時の背景追従
        self.delete("all")
        h = self.winfo_height()
        cy = h // 2
        x0 = self._pad
        x1 = self.winfo_width() - self._pad
        xmin = self._val_to_x(self.val_min)
        xmax = self._val_to_x(self.val_max)
        range_color = self.RANGE_COLOR if self.enabled else "#5a5a5a"
        handle_color = self.HANDLE_COLOR if self.enabled else "#6a6a6a"
        outline = self.HANDLE_OUTLINE if self.enabled else "#8a8a8a"

        # トラック(全体)
        self.create_line(x0, cy, x1, cy, fill=self.TRACK_COLOR,
                         width=5, capstyle="round")
        # 選択区間
        self.create_line(xmin, cy, xmax, cy, fill=range_color,
                         width=5, capstyle="round")
        # ハンドル
        r = self._radius
        for x in (xmin, xmax):
            self.create_oval(x - r, cy - r, x + r, cy + r,
                             fill=handle_color,
                             outline=outline, width=1)

    # ---- 操作 ----

    def _on_press(self, event):
        # enabled は「見た目(接続状況の灰色表示)」だけを制御し、操作は常に可能。
        # 未接続でも灰色のまま補正区間をドラッグ調整できる(プレビュー用)。
        xmin = self._val_to_x(self.val_min)
        xmax = self._val_to_x(self.val_max)
        # =87: ハンドルの上(半径+3px)はハンドルドラッグ、ハンドル間の
        # バーの上は「平行移動」ドラッグ、区間の外側は従来どおり近い方の
        # ハンドルを掴んでそこへ動かす。
        grab = self._radius + 3
        near = min(abs(event.x - xmin), abs(event.x - xmax))
        if near > grab and xmin < event.x < xmax:
            self._drag = "bar"
            self._bar_anchor = (event.x, self.val_min, self.val_max)
            return          # 平行移動は押した瞬間には動かさない
        # 近い方のハンドルを掴む(同位置なら動かせる方向で判定)
        if abs(event.x - xmin) <= abs(event.x - xmax):
            self._drag = "min" if not (xmin == xmax and event.x > xmax) else "max"
        else:
            self._drag = "max"
        self._on_drag(event)

    def _on_drag(self, event):
        if not self._drag:
            return
        if self._drag == "bar":
            # =87: 間隔を維持したまま平行移動(端に当たったら止まる)
            x0, vmin0, vmax0 = self._bar_anchor
            w = max(self.winfo_width(), 1)
            usable = max(w - self._pad * 2, 1)
            dval = (event.x - x0) / usable * (self.to - self.from_)
            width = vmax0 - vmin0
            lo = int(round((vmin0 + dval) / self.step) * self.step)
            lo = max(self.from_, min(self.to - width, lo))
            if (lo, lo + width) == (self.val_min, self.val_max):
                return
            self.val_min, self.val_max = lo, lo + width
        else:
            val = self._x_to_val(event.x)
            if self._drag == "min":
                self.val_min = min(val, self.val_max)
            else:
                self.val_max = max(val, self.val_min)
        self._redraw()
        if self.command:
            self.command(self.val_min, self.val_max)

    def _on_release(self, _event):
        self._drag = None
        self._bar_anchor = None

    def set_values(self, val_min, val_max):
        """値を外部から設定する(クランプ・スナップして再描画、commandも呼ぶ)。

        コンフィグ復元用。min>maxは入れ替える。
        """
        def snap(v):
            v = int(round(float(v) / self.step) * self.step)
            return max(self.from_, min(self.to, v))
        lo, hi = snap(val_min), snap(val_max)
        if hi < lo:
            lo, hi = hi, lo
        self.val_min, self.val_max = lo, hi
        self._redraw()
        if self.command:
            self.command(self.val_min, self.val_max)

    # ---- API ----

    def get(self):
        return self.val_min, self.val_max

    def set(self, val_min, val_max):
        self.val_min = max(self.from_, min(self.to, int(val_min)))
        self.val_max = max(self.from_, min(self.to, int(val_max)))
        if self.val_min > self.val_max:
            self.val_min, self.val_max = self.val_max, self.val_min
        self._redraw()


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


def bg_fit_geometry(region_w: int, region_h: int,
                    img_w: int, img_h: int) -> tuple[int, int, int]:
    """=262: 背景イラストの配置計算(縦フィット・中央寄せ・左右見切れ)。

    ユーザー仕様(Q4):
    - 画像は領域の**縦方向にフィット**(アスペクト比維持)。
    - 横は中央寄せ。領域より広ければ左右が見切れ、横へ広げると隠れていた
      部分が現れる。領域の方が広ければ左右に黒帯(黒はキャンバスのbg)。
    戻り値: (x0, 拡縮後の幅, 拡縮後の高さ)。x0 は領域内での画像左端
    (負=左が見切れている)。
    """
    region_w = max(1, int(region_w))
    region_h = max(1, int(region_h))
    img_w = max(1, int(img_w))
    img_h = max(1, int(img_h))
    sw = max(1, round(img_w * region_h / img_h))
    return (region_w - sw) // 2, sw, region_h


class BackgroundArt:
    """=262〜=265: 背景イラスト(クリック透過オーバーレイ方式)。

    縁なしの画像ウィンドウ(イラスト+左右黒帯)を**rootのクライアント領域
    (タイトルバーの下)だけに**重ね、薄いalphaで表示する。Windowsでは
    WS_EX_TRANSPARENT(+LAYERED/NOACTIVATE)で**クリック透過**にするので、
    下のUIはそのまま操作できる。合成結果は「UIを半透明化して背後に画像」
    (=263/=264方式)と数学的に同一(image*a + UI*(1-a))だが、rootのalphaを
    一切触らないため**タイトルバーは完全に不透過**になり(=265ユーザー要望)、
    最小化復帰などrootの見た目への副作用もない。

    クリック透過を設定できない環境(Windows以外)では、従来どおり
    「rootのalphaを下げて画像を真下に敷く」**アンダーレイ方式へ自動
    フォールバック**する(mode="underlay")。

    - 適用条件(AND): シナリオが background を持つ / 設定「表示する」ON /
      **現在タブ=再生**(Q1)。外れたら非表示(従来表示と完全一致)。
    - 画像: dim%(黒ブレンド=Q5)焼き込み後、**クライアント領域の高さへ
      縦フィット**・中央寄せ・左右見切れ・画像より広い分は黒(Q4)。
    - 透け具合: 弱/中/強。UI不透明度 0.88/0.76/0.62 に相当
      (オーバーレイ側のalphaは 1-値 = 0.12/0.24/0.38)。
    - ちらつき対策(=264): event.widget is root フィルタ(bindtagの罠)・
      描画/ジオメトリのキャッシュ・約120msフェード・非表示はウィンドウの
      alpha0(withdrawしない。最小化時のみwithdraw)。
    - 起動直後の位置ずれ対策(=265): rootの位置がWMで確定する前に表示が
      走ると画像が画面左上に張り付く(実機FB)。表示後 60/200/500ms に
      再同期をリトライし、root <Configure> 後にも遅延再同期を仕込む
      (キャッシュ比較なので確定済みなら何もしない)。
    """

    ALPHA_LEVELS = {"weak": 0.88, "mid": 0.76, "strong": 0.62}
    # =269: dim<40では画像の主張を線形に強化し、dim=0でこの値(画像側alpha)
    # に達する。dim>=40は従来どおり(1-ALPHA_LEVELS)のまま=既定の見た目不変。
    IMG_ALPHA_DIM0 = {"weak": 0.28, "mid": 0.48, "strong": 0.62}
    REBUILD_DELAY_MS = 150
    FADE_STEPS = 4
    FADE_INTERVAL_MS = 30
    RESYNC_DELAYS_MS = (60, 200, 500)

    def __init__(self, app, root):
        self.app = app
        self.root = root
        self.available = HAS_PIL
        self.user_enabled = True          # アプリ設定「背景イラスト 表示する」
        self.alpha_level = "mid"          # アプリ設定「透け具合」weak/mid/strong
        self.spec = None                  # scenario.BackgroundSpec | None
        self.shown = False
        self.mode = None                  # "overlay"(クリック透過) / "underlay"
        self._under = None                # 画像ウィンドウ(tk.Toplevel)
        self._canvas = None
        self._img_item = None             # キャンバス上の画像アイテムid
        self._drawn = None                # (id(photo), x0) 描画済みキャッシュ
        self._last_geo = None             # (w,h,x,y) 同期済みキャッシュ
        self._src = None                  # dim焼き込み済みPIL画像(原寸)
        self._src_key = None              # (path, dim)
        self._photo = None                # ImageTk.PhotoImage(拡縮後)
        self._photo_h = 0
        self._x0 = 0
        self._rebuild_job = None
        self._fade_jobs = {}              # win -> after id
        self._hwnd = None                 # 画像窓のWin32ハンドル(=266)
        try:
            root.bind("<Configure>", self._on_root_configure, add="+")
            root.bind("<Map>", self._on_root_map, add="+")
            root.bind("<Unmap>", self._on_root_unmap, add="+")
            root.bind("<FocusIn>", self._on_root_focus, add="+")
            root.bind("<FocusOut>", self._on_root_focus_out, add="+")
        except Exception:
            pass

    # ---------------- 外部API ----------------

    def set_scenario(self, spec) -> None:
        """シナリオ読み込み時に呼ぶ(spec=BackgroundSpec|None)。"""
        self.spec = spec
        self._apply()

    def set_user_enabled(self, flag: bool) -> None:
        """アプリ設定(表示ON/OFF)の反映。"""
        self.user_enabled = bool(flag)
        self._apply()

    def set_alpha_level(self, level: str) -> None:
        """アプリ設定(透け具合 weak/mid/strong)の反映。"""
        if level in self.ALPHA_LEVELS:
            self.alpha_level = level
            if self.shown:
                self._restack()           # =267: Settings操作直後の順序ずれ対策
                if self.mode == "overlay":
                    self._fade_to(self._under, self._img_alpha())
                else:
                    self._fade_to(self.root, self._ui_opacity())

    def on_tab_changed(self) -> None:
        """タブ切替時に呼ぶ(再生タブ以外では表示しない=Q1)。"""
        self._apply()

    def _img_alpha(self) -> float:
        """画像側のalpha。基準=1-UI不透明度(弱0.12/中0.24/強0.38)。

        =269: シナリオのdimが40未満のときは、dim=0で IMG_ALPHA_DIM0
        (弱0.28/中0.48/強0.62)に達するよう線形に強化する(「暗さ0のとき
        画像の主張を強くしたい」ユーザー要望)。dim>=40は従来どおり。
        """
        base = round(1.0 - self.ALPHA_LEVELS[self.alpha_level], 2)
        dim = self.spec.dim if self.spec is not None else 40
        if dim >= 40:
            return base
        t = (40 - max(0, dim)) / 40.0
        top = self.IMG_ALPHA_DIM0[self.alpha_level]
        return round(base + (top - base) * t, 3)

    def _ui_opacity(self) -> float:
        """アンダーレイ方式でのroot側alpha(=1-画像側alpha)。"""
        return round(1.0 - self._img_alpha(), 3)

    # ---------------- 表示/非表示 ----------------

    def _play_tab_visible(self) -> bool:
        tabs = getattr(self.app, "tabs", None)
        return tabs is not None and \
            getattr(tabs, "_current", None) == tr("再生")

    def _want_shown(self) -> bool:
        return bool(self.available and self.user_enabled
                    and self.spec is not None and self._play_tab_visible())

    def _apply(self) -> None:
        want = self._want_shown()
        if want and not self._load_source():
            want = False
        if want:
            self._show()
        else:
            self._hide()

    def _load_source(self) -> bool:
        """dim焼き込み済みの原寸画像を用意する。失敗はFalse(機能無効)。"""
        key = (self.spec.file, self.spec.dim)
        if key == self._src_key and self._src is not None:
            return True
        try:
            img = PILImage.open(self.spec.file)
            img = img.convert("RGB")
            dim = max(0, min(100, int(self.spec.dim)))
            if dim > 0:
                black = PILImage.new("RGB", img.size, (0, 0, 0))
                img = PILImage.blend(img, black, dim / 100.0)
            self._src = img
            self._src_key = key
            self._photo_h = 0            # 再拡縮を強制
            return True
        except Exception as e:           # 壊れた画像等は静かに無効化
            logging.getLogger(__name__).warning(
                "background image load failed: %s", e)
            self._src = None
            self._src_key = None
            return False

    def _ensure_win(self) -> None:
        if self._under is not None:
            return
        under = tk.Toplevel(self.root)
        under.overrideredirect(True)      # 縁なし・タスクバー非表示
        under.withdraw()
        # =268: =267のTk transient(owned化)は撤去。Tkのtransient管理が
        # ラッパーHWNDの再生成を誘発し、最大化/復元でEXSTYLEと保持HWNDが
        # 失われた(クリック素通し消失・追従停止の実機FB)。owned関係は
        # Win32の GWLP_HWNDPARENT で直接設定する(_apply_click_through)。
        self._set_win_alpha(under, 0.0)   # 出現時のちらつき防止(=264)
        canvas = tk.Canvas(under, bd=0, highlightthickness=0, bg="black")
        canvas.pack(fill="both", expand=True)
        self._under = under
        self._canvas = canvas

    def _setup_click_through(self, win) -> bool:
        """Windows: クリック透過+オーナー設定(オーバーレイ方式の初回判定)。

        成功=True(オーバーレイ方式が使える)。Windows以外や失敗時はFalse
        (アンダーレイ方式へフォールバック)。実体は _apply_click_through。
        """
        if sys.platform != "win32":
            return False
        return self._apply_click_through()

    def _resolve_hwnd(self, win):
        """トップレベルの実HWNDを解決する(wm_frame優先=Tkラッパー窓)。"""
        try:
            h = int(win.wm_frame(), 16)
            if h:
                return h
        except (tk.TclError, ValueError):
            pass
        try:
            import ctypes
            h = win.winfo_id()
            parent = ctypes.windll.user32.GetParent(h)
            return parent or h
        except Exception:
            return None

    def _apply_click_through(self) -> bool:
        """=268: 画像窓へ EXSTYLE(クリック透過)+Win32オーナーを(再)適用。

        - WS_EX_TRANSPARENT|LAYERED|NOACTIVATE: マウスを下のUIへ素通し。
        - GWLP_HWNDPARENT=rootのHWND: owned windowのZ帯域(rootの直上に
          保たれ、Settings等の別窓操作で下へ落ちない=旧=267のtransientの
          代替。Tkのtransient管理を使わないのでラッパー再生成を誘発しない)。
        - SWP_FRAMECHANGED: スタイル変更をシステムへ確定(=266)。
        適用先HWNDは毎回解決し直して self._hwnd へ保持する(Tkが何らかの
        理由でラッパーを作り直しても _ensure_click_through が検知して
        ここへ戻ってくる=自己修復)。
        """
        if sys.platform != "win32" or self._under is None:
            return False
        try:
            import ctypes
            GWL_EXSTYLE = -20
            GWLP_HWNDPARENT = -8
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020
            self._under.update_idletasks()
            hwnd = self._resolve_hwnd(self._under)
            if not hwnd:
                return False
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            root_hwnd = self._resolve_hwnd(self.root)
            if root_hwnd:
                set_ptr = getattr(user32, "SetWindowLongPtrW",
                                  user32.SetWindowLongW)
                set_ptr(hwnd, GWLP_HWNDPARENT, root_hwnd)
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            self._hwnd = hwnd
            return True
        except Exception:
            return False

    def _ensure_click_through(self) -> bool:
        """=268: ラッパーHWNDの再生成を検知したらスタイル等を再適用する。

        最大化/復元などでTkが画像窓の実HWNDを作り直すと、外部から書いた
        EXSTYLE・オーナー・保持HWNDが全て失われる(実機FB: 最大化中は
        クリック不能・復元後は追従停止)。同期のたびに現HWNDを確認し、
        変わっていたら適用し直す。戻り値=再適用したか。
        """
        if self.mode != "overlay" or sys.platform != "win32":
            return False
        cur = self._resolve_hwnd(self._under) if self._under else None
        if cur and cur != self._hwnd:
            self._apply_click_through()
            return True
        return False

    def _win32_move(self, x, y, w, h) -> bool:
        """=266: 画像窓の移動/リサイズをWin32 SetWindowPosで直接行う。

        EXSTYLE書き換え後の窓はTkの geometry() の**位置指定が効かなくなる**
        ことがある(実機: サイズは変わるのに座標が固定される)。overlay方式では
        こちらを正とし、失敗時のみTk geometryへフォールバックする。
        """
        if self._hwnd is None or sys.platform != "win32":
            return False
        try:
            import ctypes
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            return bool(ctypes.windll.user32.SetWindowPos(
                self._hwnd, 0, int(x), int(y), int(w), int(h),
                SWP_NOZORDER | SWP_NOACTIVATE))
        except Exception:
            return False

    def _set_win_alpha(self, win, value: float) -> None:
        try:
            win.attributes("-alpha", value)
        except tk.TclError:
            pass

    def _show(self) -> None:
        """表示する(冪等。起動直後などは=265のリトライで収束させる)。"""
        self._ensure_win()
        first = not self.shown
        self.shown = True
        try:
            root_ready = self.root.winfo_ismapped() and \
                self.root.winfo_width() >= 2
        except tk.TclError:
            root_ready = False
        if root_ready:
            self._sync(force=True)
            self._rebuild()
            try:
                self._under.deiconify()
            except tk.TclError:
                pass
            if self.mode is None:
                # 初回だけ方式を決める(HWNDが要るので表示後)
                self.mode = ("overlay"
                             if self._setup_click_through(self._under)
                             else "underlay")
            self._restack()
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass
            if self.mode == "overlay":
                # rootは触らない(タイトルバー完全不透過=265)。画像側を上に
                # 薄く重ねる(合成結果は=263/=264と同一)。
                self._fade_to(self._under, self._img_alpha())
            else:
                self._set_win_alpha(self._under, 1.0)
                self._fade_to(self.root, self._ui_opacity())
        if first or not root_ready:
            # =265: 起動直後はrootの位置がWMで未確定のことがある(画像が
            # 画面左上に張り付く実機FB)。少し遅らせて再同期・再表示する。
            for delay in self.RESYNC_DELAYS_MS:
                try:
                    self.root.after(delay, self._resync_later)
                except Exception:
                    pass

    def _resync_later(self) -> None:
        """=265: 遅延再同期。位置が確定済みなら(キャッシュ比較で)何もしない。"""
        if not self.shown:
            return
        try:
            if not self.root.winfo_ismapped():
                return
        except tk.TclError:
            return
        if self.mode is None or self._photo is None \
                or self._photo_h != self.root.winfo_height():
            self._show()                  # 初回が不発だった(未確定で戻った)
        else:
            self._sync()

    def _hide(self) -> None:
        if not self.shown:
            return
        self.shown = False
        if self.mode == "overlay":
            # rootは元から触っていない。画像側を消すだけ。
            self._fade_to(self._under, 0.0)
            return

        # アンダーレイ方式: alphaを戻し切ってから画像を消す(=264 順序)
        def after_fade():
            if self.shown:                # フェード中に再表示された
                return
            if self._under is not None:
                self._set_win_alpha(self._under, 0.0)

        self._fade_to(self.root, 1.0, done=after_fade)

    def _fade_to(self, win, target: float, done=None) -> None:
        """winのalphaを約120msかけて段階的に変える(=264 ちらつき緩和)。"""
        job = self._fade_jobs.pop(win, None)
        if job is not None:
            try:
                win.after_cancel(job)
            except Exception:
                pass
        try:
            cur = float(win.attributes("-alpha"))
        except (tk.TclError, ValueError):
            cur = 1.0
        steps = self.FADE_STEPS
        if abs(cur - target) < 0.01:
            self._set_win_alpha(win, target)
            if done is not None:
                done()
            return

        def step(i):
            self._fade_jobs.pop(win, None)
            v = cur + (target - cur) * (i / steps)
            self._set_win_alpha(win, v)
            if i >= steps:
                if done is not None:
                    done()
                return
            try:
                self._fade_jobs[win] = win.after(
                    self.FADE_INTERVAL_MS, lambda: step(i + 1))
            except Exception:
                pass

        step(1)

    # ---------------- 同期・描画 ----------------

    def _sync(self, force: bool = False) -> None:
        """画像窓をrootのクライアント領域へ重ねる(変化時のみ)。

        位置基準はクライアント領域(winfo_rootx/rooty)なので、**タイトル
        バーは画像の表示領域に含まれない**(=265)。
        """
        if not self.shown or self._under is None:
            return
        try:
            if not self.root.winfo_ismapped():
                return
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w < 2 or h < 2:
                return
            if self._ensure_click_through():
                force = True              # =268: HWNDが変わった→位置も再適用
            geo = (w, h, x, y)
            if not force and geo == self._last_geo:
                return
            self._last_geo = geo
            if not self._win32_move(x, y, w, h):
                self._under.geometry(f"{w}x{h}+{x}+{y}")
            self._restack()
        except tk.TclError:
            pass

    def _restack(self) -> None:
        """画像窓の重ね順を整える。

        overlay: rootの**直上**(rootのダイアログや編集画面はさらに上に
        来るので覆わない)。underlay: rootの**直下**。
        """
        if self._under is None or self.mode is None:
            return
        try:
            if self.mode == "overlay":
                self._under.lift(self.root)
                # =267: rootの子トップレベル(Settings・ヘルプ・編集画面等)を
                # オーバーレイの上へ再整列する。Settingsはtransient(owned)の
                # ためrootの直上に保たれ、素朴なlift(root)だとオーバーレイが
                # その上へ割り込んで薄衣がかかっていた(実機FB)。
                for w in self.root.winfo_children():
                    if w is self._under or not isinstance(w, tk.Toplevel):
                        continue
                    try:
                        if w.winfo_viewable():
                            w.lift(self._under)
                    except tk.TclError:
                        pass
            else:
                self._under.lower(self.root)
        except tk.TclError:
            pass

    def _on_root_configure(self, e=None) -> None:
        # =264: bindtagの仕様でこのハンドラは**全子孫のConfigure**でも呼ばれる。
        # root自身のイベントだけを扱う(でないとページ切替のたびに再描画が
        # 連打されて画像が点滅する=実機ちらつきの主因)。
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        if not self.shown:
            return
        self._sync()
        # =265: Configure直後はwinfo値が古いことがある→遅延再同期
        # (キャッシュ比較なので変化がなければ何もしない)
        try:
            self.root.after(80, self._resync_later)
        except Exception:
            pass
        h = self.root.winfo_height()
        if h != self._photo_h:
            self._schedule_rebuild()       # 縦が変わった=再拡縮(デバウンス)
        else:
            self._recompute_x0()
            self._redraw()

    def _on_root_map(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        if self.shown and self._under is not None:
            try:
                self._under.deiconify()
            except tk.TclError:
                pass
            if self.mode == "overlay":
                self._set_win_alpha(self._under, self._img_alpha())
            elif self.mode == "underlay":
                self._set_win_alpha(self._under, 1.0)
            self._sync(force=True)

    def _on_root_unmap(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        # 最小化に追従(shownフラグは維持=復帰で再表示)
        if self.shown and self._under is not None:
            try:
                self._under.withdraw()
            except tk.TclError:
                pass

    def _on_root_focus(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        # 他アプリの窓を挟んだ後の復帰: 重ね順だけ直す
        if self.shown:
            self._restack()

    def _on_root_focus_out(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        # =267: Settings等の別窓へフォーカスが移った直後、OSのowned再整列で
        # 順序がずれることがある→少し遅らせて直す(保険)。
        if self.shown:
            try:
                self.root.after(80, self._restack)
            except Exception:
                pass

    def _schedule_rebuild(self, immediate: bool = False) -> None:
        if self._rebuild_job is not None:
            try:
                self.root.after_cancel(self._rebuild_job)
            except Exception:
                pass
            self._rebuild_job = None
        delay = 1 if immediate else self.REBUILD_DELAY_MS
        try:
            self._rebuild_job = self.root.after(delay, self._rebuild)
        except Exception:
            pass

    def _rebuild(self) -> None:
        self._rebuild_job = None
        if not self.shown or self._src is None:
            return
        h = self.root.winfo_height()
        w = self.root.winfo_width()
        if h < 2 or w < 2:
            return
        _x0, sw, sh = bg_fit_geometry(w, h, self._src.width, self._src.height)
        if sh != self._photo_h or self._photo is None:
            try:
                scaled = self._src.resize((sw, sh),
                                          PILImage.Resampling.LANCZOS)
                self._photo = PILImageTk.PhotoImage(scaled)
                self._photo_h = sh
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "background resize failed: %s", e)
                return
        self._recompute_x0()
        self._redraw()

    def _recompute_x0(self) -> None:
        if self._photo is None:
            return
        w = self.root.winfo_width()
        self._x0 = (max(1, w) - self._photo.width()) // 2

    def _redraw(self) -> None:
        """描画は(photo, x0)が変わった時だけ(=264)。移動はcoordsで済ます。"""
        if self._canvas is None or self._photo is None:
            return
        key = (id(self._photo), self._x0)
        if key == self._drawn:
            return
        try:
            if (self._img_item is None or self._drawn is None
                    or self._drawn[0] != id(self._photo)):
                self._canvas.delete("all")
                self._img_item = self._canvas.create_image(
                    self._x0, 0, anchor="nw", image=self._photo)
            else:
                self._canvas.coords(self._img_item, self._x0, 0)
            self._drawn = key
        except tk.TclError:
            pass


class RVPApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        root.title("RVP - Random Voice Player")
        # =57: 1600x900 のデスクトップでも収まるよう高さ985→820へ
        # (ユーザー要望)。最小高もそれに合わせて下げる。
        # =159: 最小の高さは 700 → **790**(ユーザー指定)。700では①再生ページの
        # 「再生中のチャンネル」の R行が潰れて文字が欠け、その下の
        # 「★=シークバーが追従しているチャンネル」が消えていた。
        root.minsize(580, self._min_win_h())
        # 幅690: ◀▶切替ボタンの分デバイス領域が狭まり文字が隠れたため+50px
        # (2026-07-22ユーザー要望)
        # =115: 前回終了時のサイズ・位置を復元する(マルチディスプレイ対応)。
        # 保存が無い/保存位置がどのモニタにも載っていない場合だけ既定配置。
        self.winmem = WindowMemory(root, "main")
        if not self.winmem.restore():
            self._place_window(690, 820)
        self.winmem.watch()
        _startup_mark("RVPApp: ウィンドウ配置")

        pygame.mixer.init(channels=2)  # ステレオ(パン制御に必要)
        _startup_mark("RVPApp: pygame.mixer.init")

        self.runner = AsyncRunner()
        self.intiface = IntifaceClient(DEFAULT_INTIFACE_URL)
        self.tcode = TCodeClient()   # =80 TCode直接出力(シリアル)
        self.player = ScenarioPlayer(self.intiface)
        self.player.tcode = self.tcode
        self.scenario: Scenario | None = None
        # 選択肢UIの状態(再生タブ)
        self._scenario_has_choices = False   # 読込中シナリオに選択肢イベントがあるか
        # =150: 読込中シナリオがデバイストラック(funscript/csv)を持つか。
        # False かつ シナリオ読込済みのとき、再生タブは3画面構成になる。
        # 未読込(scenario is None)のときは判定できないので5画面のまま。
        self._scenario_has_device = True
        self._choice_display = None           # None / "hidden" / "placeholder" / "active"
        self._video_front = False             # 動画中カード表示でtopmost化しているか
        self._autoselect_after = None         # 自動選択タイマーの after id
        self._autoselect_sig = None           # スケジュール時の選択肢シグネチャ
        self._last_dir: str | None = None   # 前回シナリオを開いたフォルダ
        self.appearance_mode = "dark"       # 外観テーマ(dark/light)。ボタンで即時切替
        # =134: パステルカラーテーマ名(""=なし)。適用自体は main() が
        # 起動時に行う(反映は再起動後)。ここは設定メニューと保存の状態だけ。
        self.color_theme = apptheme.ACTIVE or ""
        # 自動再接続の状態管理
        self._want_connected = False   # 一度接続に成功したか(ピル文言の出し分け用)
        self._reconnect_fut = None     # 実行中の接続 future(None=非実行)
        self._probe_fut = None         # 実行中のポートプローブ future(=78)
        self._reconnect_after = 0.0    # 次の自動接続試行を許可する時刻(monotonic)
        self._manual_connecting = False  # 手動「接続」実行中は自動接続を止める(=78)
        # =272: 「接続中デバイス」欄の描画済みスナップショット
        # (intiface接続有無, デバイス名tuple, has_linear, COM接続有無)。
        # 接続状況が変わるたびに欄を描き直し、古い表示が残らないようにする。
        # None=未描画(次の更新で必ず描く)。エラー文を出した直後は
        # 「現在のスナップショット」を控えて、状態が変わるまで上書きしない。
        self._device_box_snap = None
        _startup_mark("RVPApp: クライアント類の生成")

        self._build_ui()
        _startup_mark("RVPApp: _build_ui 完了")
        # =157: タブとヘッダーを同じ帯に置けるだけの幅を確保する
        # (足りないと =156までの2段レイアウトへ自動で戻ってしまう)。
        self._ensure_header_width()
        self._apply_saved_config()
        _startup_mark("RVPApp: _apply_saved_config 完了")
        # 起動時に前回シナリオ(履歴先頭)を自動で開く(=43 案1)。
        # =81: 直接呼ばず**最初の描画が済んでから**開く(after遅延)。
        # ウィンドウ実体化前に _load_scenario の大量のUI更新(再生タブ遷移
        # 含む)を走らせると、Windows の customtkinter(font_shapes描画)が
        # RecursionError の嵐で起動不能になる実機事例が出たため
        # (2026-08-06 ユーザー報告。履歴あり=自動オープン時のみ発生)。
        # 遅延により手動オープンと同じタイミング条件になる。
        self.root.after(150, self._auto_open_last_scenario)
        # スペースキーで再生/一時停止(=43 案2)。メインウィンドウ内のみ
        # (編集画面は別Toplevelなので影響しない)
        self.root.bind("<space>", self._on_space_key)
        self._poll_state()
        self._animate_linear()
        self._animate_graph()
        _startup_mark("RVPApp: __init__ 完了")

    # 初期表示位置: 画面左上ぴったり(=58 ユーザー決定。=57では20pxだった)。
    # 以前は「横中央・上から16px」だったが、1600x900のような狭い画面では
    # 左上に寄せた方が他のウィンドウと並べやすい。
    WIN_MARGIN = 0

    def _place_window(self, w: int, h: int):
        """ウィンドウを画面左上から WIN_MARGIN px の位置に配置する。"""
        self.root.update_idletasks()
        m = self.WIN_MARGIN
        self.root.geometry(f"{w}x{h}+{m}+{m}")

    # ================= UI 構築 =================

    def _build_ui(self):
        self._build_header()

        # ===== タブ =====
        self.tabs = TabView(self.root)
        # =89: 下paddingを18→4へ(再生タブ下部のバーランプの高さを、ページの
        # 表示域を削らずにここから捻出する)
        # =157: ヘッダーを place で浮かせたので、タブ行は**ウィンドウ上端から
        # TABS_TOP(28px)** の位置から始める(=156までは pack されたヘッダー行の
        # 下=56px だった)。差の28pxはそのまま各タブの表示領域になる。
        self._tabs_compact = None       # =157: 現在の配置(None=未確定)
        self.tabs.pack(fill="both", expand=True, padx=20,
                       pady=(self.TABS_TOP, 4))

        self.tab_conn = self.tabs.add(tr("接続"))
        self.tab_scenario = self.tabs.add(tr("シナリオ"))
        self.tab_play = self.tabs.add(tr("再生"))
        # =155: タブの切替を拾う(再生タブへ移った時に履歴へ記録する)。
        # add() の中で最初の set() が走るため、**タブを作り終えてから**繋ぐ。
        self.tabs.on_change = self._on_tab_changed

        _startup_mark("build: ヘッダー+タブ枠")
        self._build_tab_connection(self.tab_conn)
        _startup_mark("build: 接続タブ")
        self._build_tab_scenario(self.tab_scenario)
        _startup_mark("build: シナリオタブ(履歴の先頭チャンク含む)")
        self._build_tab_play(self.tab_play)
        # =262/=263: 背景イラスト(表示は再生タブ表示中のみ=Q1)。
        # =263でウィンドウアルファ+アンダーレイ方式になり、基準はrootへ。
        self.bg_art = BackgroundArt(self, self.root)
        self.bg_art.set_user_enabled(self.show_bg_var.get())
        _startup_mark("build: 再生タブ")
        # 起動時の初期タブは「シナリオ」(=42 ユーザー要望)。シナリオを選んで
        # 再生する流れが基本のため。接続は自動再接続が背景で走るので
        # 接続タブを最初に見せる必要はない。
        self.tabs.set(tr("シナリオ"))

        # =157: ヘッダーは TabView より先に作られている=Tkの生成順では下に
        # なるので、ここで持ち上げる(タブ行の右側の空きに重ねて描く)。
        try:
            self.header.lift()
        except Exception:
            logger.exception("header lift failed")

        # =157: 幅が足りるかを判定して配置を決める。以後はウィンドウの
        # リサイズでも追従する(モードが変わったときだけ pack し直す)。
        self._apply_tabs_top()
        self.root.bind("<Configure>", self._on_root_configure, add="+")

        # =270: OSからのシナリオファイルD&D。**シナリオタブのフレーム**を
        # ドロップ先として登録する(tkdndは落下点直下から親方向へ登録済み
        # ウィジェットを探すため、タブ内のどこへ落としてもここで受かる)。
        # 注意: drop_target_register は tkinterdnd2 が tkinter.BaseWidget へ
        # 注入するメソッドなので、ルート(Tk/CTk)には存在しない=ルートへは
        # 登録できない。タブのフレームなら BaseWidget 由来で登録できる。
        # 編集画面(=44)と同じ流儀: 未導入・登録失敗は静かに無効。ハンドラ側
        # でも現在タブ=シナリオを確認する(タブ切替直後の取りこぼし保険)。
        self._dnd_ok = False
        if TkinterDnD is not None:
            try:
                TkinterDnD._require(self.tab_scenario)
                self.tab_scenario.drop_target_register(DND_FILES)
                self.tab_scenario.dnd_bind("<<Drop>>",
                                           self._on_scenario_dnd_drop)
                self._dnd_ok = True
            except Exception:
                self._dnd_ok = False

    # ---- =157 ヘッダーとタブ行の同居 ----

    def _measure_header_max_width(self) -> int:
        """接続ピルが**いちばん広い文言**のときのヘッダー幅(px)を実測する。

        文言によって幅が変わるので、狭いほうで判定すると「接続した瞬間に
        タブへ食い込む」ことになる。いったん最長の文言を入れて測り、元へ戻す
        (起動時に1回だけ。表示は一瞬たりとも変わらない=描画前に戻すため)。
        """
        pill = self.conn_pill
        keep = pill.cget("text")
        best = 0
        try:
            for t in self._pill_texts:
                pill.configure(text=t)
                self.root.update_idletasks()
                best = max(best, self.header.winfo_reqwidth())
        except Exception:
            logger.exception("header width measure failed")
        finally:
            try:
                pill.configure(text=keep)
                self.root.update_idletasks()
            except Exception:
                logger.exception("header text restore failed")
        return best or self.header.winfo_reqwidth()

    def _header_needed_width(self) -> int:
        """タブとヘッダーを同じ帯に置くのに必要なウィンドウ幅(px)。

        タブの右端(左余白20＋最後のタブ)＋隙間＋ヘッダー幅＋右余白。
        タブ幅もヘッダー幅も**言語で変わる**ので実測から求める
        (日本語/英語でボタン幅とピルの文字数が違う)。
        """
        tabs_end = 20 + max(0, self.tabs._next_x - 6)
        hw = getattr(self, "_header_max_w", 0) or self.header.winfo_reqwidth()
        return tabs_end + self.HEADER_GAP + hw + self.HEADER_RIGHT

    def _apply_tabs_top(self) -> bool:
        """タブ行の上端を決める(compact=ヘッダーと同じ帯 / classic=その下)。

        狭いウィンドウでヘッダーがタブに重なると、タブの右端が押しボタンで
        隠れて押せなくなる。**幅が足りないときだけ**=156までと同じ
        「ヘッダー行の下にタブ」へ自動で戻す(見た目は従来どおりで安全)。
        戻り値は compact かどうか。
        """
        try:
            w = self.root.winfo_width()
            # =158: マップ前は Windows だと 1 が返る。ここで「狭い」と決めると
            # 起動直後だけ classic に落ちてしまうので**判断を保留**する
            # (マップ後の <Configure> でもう一度呼ばれる)。
            if w <= 1:
                return True if self._tabs_compact is None else self._tabs_compact
            compact = w >= self._header_needed_width()
        except Exception:
            logger.exception("tabs top calc failed")
            compact = False
        if compact == self._tabs_compact:
            return compact
        self._tabs_compact = compact
        top = self.TABS_TOP if compact else self.TABS_TOP_CLASSIC
        self.tabs.pack_configure(pady=(top, 4))
        return compact

    def _ensure_header_width(self):
        """タブとヘッダーが同居できる幅を最小幅として保証する(=157)。

        必要幅は**言語とフォントで変わる**ので実測から決める(日本語 約714px /
        英語 約733px)。=156までの既定幅690pxではわずかに足りないので、
        **minsize を上げるだけ**にする。Tkは最小幅より小さいウィンドウを
        その場で広げてくれる(=幅だけが変わり、高さと位置には触らない)。

        **=158の不具合の教訓**: ここで `geometry()` を呼んではいけない。
        呼ぶと**サイズだけを上書き**するので、=115で復元した「前回の大きさ」を
        毎回つぶしてしまう(位置は残るので「位置だけ記憶される」ように見える)。
        しかも起動直後のウィンドウはまだ地図に載っておらず、Windowsでは
        `winfo_width()/winfo_height()` が **1** を返す=「狭すぎる」と誤判定して
        必ず走り、高さ1px→最小高700pxへ丸められて固定サイズ化していた。
        (Xvfbでは実サイズを返すため、コンテナのテストでは見えなかった。)
        幅が足りるかどうかの判定は、**マップ後の `<Configure>`** に任せる
        (`_apply_tabs_top`)。
        """
        try:
            self.root.update_idletasks()
            self._header_max_w = self._measure_header_max_width()
            need = self._header_needed_width()          # 実ピクセル
            # **単位に注意**: winfo_* は実ピクセル、CTkの minsize() は
            # **論理ピクセル**(内部で window scaling を掛けてから Tk へ渡す)。
            # 高DPI(125%等)で実ピクセルのまま渡すと二重に拡大されてしまう
            # ので、渡す前に割り戻す(等倍環境では素通り)。
            # 高さの最小値は従来どおり700(この改修で必要量は増えない)。
            self.root.minsize(max(580, self._to_logical(need)),
                              self._min_win_h())
            self._apply_tabs_top()
        except Exception:
            logger.exception("ensure header width failed")

    # =159: ウィンドウの最小の高さ(論理ピクセル)。=57からの700では①再生ページの
    # チャンネル3行目(R)が潰れ、その下の★の説明行が消えていた(実機報告)。
    # 実測では日本語Windowsで770px前後から欠け始めるので、余裕をみて790。
    MIN_WIN_H = 790
    # 画面が低い端末(1366x768など)ではウィンドウが画面からはみ出してしまうので、
    # 作業領域に収まる範囲へ丸める。この場合は下端が切れるが、掴めないほど
    # 大きいウィンドウよりはまし(=115の fit_into_monitor と同じ考え方)。
    MIN_WIN_H_FLOOR = 620

    def _min_win_h(self) -> int:
        """最小の高さ。画面が低ければその範囲へ丸める(論理ピクセル)。"""
        try:
            avail = self._to_logical(self.root.winfo_screenheight() - 60)
            return max(self.MIN_WIN_H_FLOOR, min(self.MIN_WIN_H, avail))
        except Exception:
            logger.exception("min window height calc failed")
            return self.MIN_WIN_H

    def _to_logical(self, px: int) -> int:
        """実ピクセル → CTkの論理ピクセル(window scaling を割り戻す)。

        倍率そのもの(`__window_scaling`)は名前修飾されていて触りにくいので、
        CTkが持つ変換メソッド `_reverse_window_scaling` / `_apply_window_scaling`
        を使う。**切り捨てで1px足りなくなることがある**ので、掛け直して
        元に届かなければ1つ足す(足りないと最小幅の意味が無くなる)。
        等倍環境・メソッドが無い環境ではそのまま返す。
        """
        rev = getattr(self.root, "_reverse_window_scaling", None)
        app = getattr(self.root, "_apply_window_scaling", None)
        if not callable(rev):
            return int(px)
        try:
            v = int(rev(px))
            if callable(app) and int(app(v)) < px:
                v += 1
            return v
        except Exception:
            logger.exception("window scaling reverse failed")
            return int(px)

    def _on_root_configure(self, event=None):
        """ウィンドウのリサイズ。**メインウィンドウ自身の変化だけ**を見る。"""
        if event is not None and event.widget is not self.root:
            return
        self._apply_tabs_top()

    # =157: ヘッダー(ヘルプ/Settings/接続ピル)の配置。pack ではなく place で
    # **右上へ浮かせる**ので、タブ行はその下ではなく**同じ帯の左側**へ入れる。
    # HEADER_TOP  : ウィンドウ上端からヘッダーまでの余白(=156までと同じ18px)
    # HEADER_RIGHT: 右端からの余白(タブ枠の padx=20 と揃える)
    # TABS_TOP    : ウィンドウ上端からタブ行の上端まで(=156までは56px)
    # TABS_TOP_CLASSIC: 幅が足りないときの逃げ道(=156までと同じ「ヘッダー行の
    #              下にタブ」= HEADER_TOP + ヘッダー高28 + 余白10)
    # HEADER_GAP : タブの右端とヘッダーの間に最低限空ける隙間
    HEADER_TOP = 18
    HEADER_RIGHT = 20
    TABS_TOP = 28
    TABS_TOP_CLASSIC = 56
    HEADER_GAP = 8

    def _build_header(self):
        # =157: **pack をやめて place で右上に固定する**(2026-08-16 ユーザー決定)。
        # ヘッダーを pack で流し込むと、その行の高さ(28px)+上下余白(18/10px)を
        # タブ行が丸ごと下へずれる形で負担する=**タブの左側は空いているのに
        # 上に56pxの帯ができる**。ヘッダーの中身は右端に寄っているので、
        # place で浮かせてタブ行を同じ帯の左側へ入れれば、見た目を変えずに
        # 28px を表示領域へ返せる(ユーザー提示の完成イメージどおり)。
        # 重なるのは「タブ行の右側の空き」だけで、パネル(タブの中身)には
        # かからない(タブ上端28 + TAB_HEIGHT-TAB_OVERLAP=34 → パネル上端62px、
        # ヘッダーは18〜46px)。
        # **stacking**: place した兄弟でも、後から生成された TabView のほうが
        # 上に来る(Tkは生成順)。`_build_ui` の最後で `header.lift()` を呼ぶ
        # (透明フレームでも実体は不透明なので、上げないとボタンが隠れる)。
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.place(relx=1.0, x=-self.HEADER_RIGHT, y=self.HEADER_TOP,
                     anchor="ne")
        self.header = header

        # =156: **アプリ名の表示(「RVP」+「Random Voice Player」)を廃止**
        # (2026-08-16 ユーザー決定)。最初のバージョンから置いていたが、
        # 自分の名前を画面内で自己説明するアプリは珍しく、無いほうが
        # スタイリッシュ、という判断。**Windowsのタイトルバー
        # (root.title = "RVP - Random Voice Player")とアイコンは従来どおり**
        # 残すので、タスクバー・Alt+Tabでの識別性は落ちない。
        # ヘッダー行の高さはボタン(ヘルプ/Settings/接続ピル)に従って自然に
        # 縮み、タブ以下の表示領域がその分広がる(ユーザー選択)。

        # =157: 接続ピルの文言は「未接続 / 接続中... / 接続済み / 再接続中...」
        # と変わり、そのたびにヘッダー全体の幅が伸び縮みする(右寄せなので
        # 左へ伸びる)。タブと同じ帯に置くようになったので、**いちばん広い
        # 文言のときでもタブに掛からない幅**を確保する必要がある。文言の
        # 一覧をここに持っておき、`_ensure_header_width` が最大幅を実測する。
        self._pill_texts = [tr("● 未接続"), tr("● 接続中..."),
                            tr("● 接続済み"), tr("● 再接続中...")]
        self.conn_pill = ctk.CTkLabel(
            header, text=tr("● 未接続"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED,
            fg_color=("gray85", "gray20"),
            corner_radius=99, padx=12, pady=4,
        )
        self.conn_pill.pack(side="right")

        # =105: 設定はボタン1つに集約(旧: ☀/☾ボタン+言語コンボを廃止し
        # ポップアップへ移動。グラフ更新頻度=104もここに同居)。
        # 各StringVarはダイアログを開かなくても存在する(テスト・設定復元用)。
        self.lang_var = tk.StringVar(value="日本語" if LANG == "ja" else "English")
        self.appearance_var = tk.StringVar(value="")
        self.graph_fps_var = tk.StringVar(value="60fps")
        # =262: 背景イラストの表示ON/OFF(視聴側設定。既定=表示する)。
        self.show_bg_var = tk.BooleanVar(value=True)
        # =263: 背景イラストの透け具合(弱/中/強。既定=中)。
        self.bg_alpha_var = tk.StringVar(value=tr("中"))
        # =119: UIフォント。値は表示文言(「システム標準」or フォント名)。
        # 実際の適用は main() 起動時の appfont.apply()=**再起動で反映**
        # (CTkFontはウィジェット生成時にfamilyを確定するため。言語と同じ扱い)。
        self.font_var = tk.StringVar(value=appfont.DEFAULT_FAMILY)   # =293
        self._graph_interval_ms = 16          # =104: 既定60fps
        self._settings_win = None
        # =113: 表示は歯車記号「⚙」ではなく文言(記号はRobotoに字形が無く
        # 絵文字系フォントへ落ちる=112④)。
        # =135: 表記は**日本語表示でも英語 "Settings" に固定**(ユーザー決定)。
        # 日本語を読めない人が「設定」を読めず言語切替に辿り着けないため、
        # 言語切替への導線(このボタン→Language行)だけは常に英語にする。
        # 幅は言語によらず一定(CTkButtonは幅を自動調整しない=113)。
        self.settings_btn = ctk.CTkButton(
            header, text="Settings",
            width=74, height=26,
            font=ctk.CTkFont(size=13),
            fg_color=("gray80", "gray25"), hover_color=("gray72", "gray30"),
            # CTkButtonの既定文字色は明色(gray98)なので、ライトの
            # fg_color=gray80 の上だと文字が沈む(=112③と同じ話。記号1文字なら
            # 形で読めたが、文言にすると読みづらさが目立つ)。ライトだけ濃く。
            text_color=("gray10", "gray90"),
            command=self._open_settings,
        )
        self.settings_btn.pack(side="right", padx=(0, 10))

        # =118: ヘルプは編集画面のツールバーから**メイン画面のヘッダーへ移動**
        # (ユーザー決定=「設定」の左隣。視聴だけの人にも届く場所にする)。
        # 見た目は「設定」と揃える。side="right" は後にpackしたものが左へ来る。
        self._help_dlg = None
        self._editor_win = None      # =152: 編集画面は1つまで
        # =155: 履歴へ記録済みのシナリオパス(再生タブを出入りしても
        # 記録し直さないための印)。シナリオを読み込むと None へ戻る。
        self._recorded_path = None
        self.help_btn = ctk.CTkButton(
            header, text=tr("ヘルプ"),
            width=(66 if LANG == "ja" else 60), height=26,
            font=ctk.CTkFont(size=13),
            fg_color=("gray80", "gray25"), hover_color=("gray72", "gray30"),
            text_color=("gray10", "gray90"),
            command=self._open_help,
        )
        self.help_btn.pack(side="right", padx=(0, 8))
        self._update_theme_btn()

    def _open_help(self):
        """=118: ヘルプ画面(非モーダル)を開く。2つ目は開かず前面化する。

        HelpDialog は editor.py にあるので**押した時に遅延importする**
        (起動時に編集画面のモジュールを読み込まないため=起動時間対策)。
        """
        dlg = self._help_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.deiconify()
                    dlg.lift()
                    dlg.focus_set()
                    return
            except Exception:
                pass
            self._help_dlg = None
        from .editor import HelpDialog
        self._help_dlg = HelpDialog(self.root)

    def _open_settings(self):
        """=105: 設定ポップアップ(外観/言語/描画更新頻度)を開く。

        非モーダルの小窓。既に開いていれば前面へ出すだけ。

        =108: 外観(ダーク/ライト)を切り替えると、**customtkinter が
        Windowsでタイトルバーの色を塗り替えるために小窓を withdraw し、
        5ms後に deiconify で戻す**(CTkToplevel._windows_set_titlebar_color)。
        この往復の最中に withdraw がもう一度走ると CTk は
        「ユーザーが閉じたのだ」と解釈して**畳んだまま復帰させない**。
        すると winfo_exists() は真のままなので、設定ボタンを押しても lift() する
        だけで何も出てこなくなる(ユーザー報告=「二度と反応しなくなる」)。
        対策として、生きている小窓でも**実際に見える状態かどうか**を確認し、
        見えないなら deiconify、それでも駄目なら作り直す。
        """
        win = self._settings_win
        if win is not None:
            try:
                alive = bool(win.winfo_exists())
            except Exception:
                alive = False
            if alive:
                try:
                    if win.state() != "normal":
                        win.deiconify()
                    win.lift()
                    win.focus_set()
                    win.update_idletasks()
                    if win.winfo_viewable():
                        return
                except Exception:
                    pass
                # 見える状態にできない=CTkのテーマ切替に巻き込まれた残骸。
                # 破棄して作り直す(ボタンが無反応にならないことを優先)。
                try:
                    win.destroy()
                except Exception:
                    pass
            self._settings_win = None
        win = ctk.CTkToplevel(self.root)
        self._settings_win = win
        win.title("Settings")   # =135: 言語切替導線は常に英語表記
        win.resizable(False, False)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", self._close_settings)
        # 設定ボタンの下あたりへ表示
        try:
            x = max(0, self.settings_btn.winfo_rootx() - 240)
            y = self.settings_btn.winfo_rooty() + 34
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(padx=16, pady=12)

        def row(label):
            r = ctk.CTkFrame(body, fg_color="transparent")
            r.pack(fill="x", pady=4)
            ctk.CTkLabel(r, text=label, width=130, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(side="left")
            return r

        # =152: 幅は他のコンボと揃える(150→130)。=136で表示名から「パステル」を
        # 外して最長ラベルが4文字になったのに、幅が広いままだった名残の解消。
        ctk.CTkOptionMenu(
            row(tr("外観")), variable=self.appearance_var, width=130, height=26,
            values=[tr("ダーク"), tr("ライト"),
                    *[tr(apptheme.THEME_LABELS[n]) for n in apptheme.THEMES]],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,     # =114: ライトは黒
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_appearance_select).pack(side="left")
        ctk.CTkOptionMenu(
            # =135: 行ラベルも常に英語 "Language"(選択肢は従来から
            # 各言語の自称表記=「日本語」/"English" で据え置き)
            row("Language"), variable=self.lang_var, width=130, height=26,
            values=["日本語", "English"],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_language_change).pack(side="left")
        # =119: UIフォント。選択肢は「システム標準」+ BIZ UDGothic
        # (Windows 10 1809以降に標準搭載・SIL OFL)。反映は再起動後(言語と同じ)。
        ctk.CTkOptionMenu(
            row(tr("フォント")), variable=self.font_var, width=130, height=26,
            values=[tr("システム標準"), *appfont.FONT_CHOICES],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_font_change).pack(side="left")
        # =104/=106: 描画更新頻度(グラフ+駆動値バー)。60fps=なめらか(推奨)/
        # 30fps=負荷半減。
        # 重い縮尺ではコスト適応ペーシングが自動で間隔を広げる
        ctk.CTkOptionMenu(
            row(tr("描画更新頻度")), variable=self.graph_fps_var,
            width=130, height=26, values=["60fps", "30fps"],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_graph_fps_change).pack(side="left")
        # =262: 背景イラストの表示ON/OFF。シナリオが背景を指定していても、
        # 視聴する人がここでOFFにできる(最終決定は視聴側)。
        # =263: UI全体を半透明化する方式になったため「透け具合」も選べる。
        bg_row = row(tr("背景イラスト"))
        ctk.CTkSwitch(
            bg_row, text=tr("表示する"),
            variable=self.show_bg_var, width=110,
            font=ctk.CTkFont(size=12),
            progress_color=ACCENT,
            command=self._on_show_bg_change).pack(side="left")
        ctk.CTkLabel(bg_row, text=tr("透け具合"),
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(10, 4))
        ctk.CTkOptionMenu(
            bg_row, variable=self.bg_alpha_var, width=70, height=26,
            values=[tr("弱"), tr("中"), tr("強")],
            font=ctk.CTkFont(size=12),
            command=self._on_bg_alpha_change).pack(side="left")
        # =139: ライト/カラーテーマで gray62 は沈む(ユーザー指摘)→
        # ライト側は黒、ダーク側は従来の gray62(=LABEL と同じ組)。
        self._settings_note = ctk.CTkLabel(
            body, text="", font=ctk.CTkFont(size=11), text_color=LABEL,
            justify="left", anchor="w")
        self._settings_note.pack(fill="x", pady=(6, 0))

    def _close_settings(self):
        """設定ポップアップを閉じる(×ボタン)。参照を必ず落とす(=108)。"""
        win = self._settings_win
        self._settings_win = None
        try:
            if win is not None:
                win.destroy()
        except Exception:
            pass

    def _reassert_settings_win(self):
        """=108: 外観切替でCTkが畳んだままにした小窓を、見える状態へ戻す。

        CTk側の deiconify は after(5) で走るので、それより後に確認する。
        """
        win = self._settings_win
        if win is None:
            return
        try:
            if not win.winfo_exists():
                self._settings_win = None
                return
            if win.state() != "normal" or not win.winfo_viewable():
                win.deiconify()
                win.lift()
        except Exception:
            pass

    def _on_appearance_select(self, choice: str):
        """=105/=134/=151: 設定の外観メニュー(ダーク/ライト/カラー6色)。

        **=151で全て即時反映**になった(再起動は不要)。6色は「ライトの他
        バリエーション」なので、8つは排他選択: 色を選べばライト基調のその色、
        ダーク/ライトを選べばカラーテーマは解除される。
        """
        picked = None
        for name in apptheme.THEMES:
            if choice == tr(apptheme.THEME_LABELS[name]):
                picked = name
                break
        if picked is not None:
            if picked == self.color_theme:
                return
            self._apply_theme_now("light", picked)
            return
        # ダーク/ライトが選ばれた=カラーテーマは解除
        mode = "dark" if choice == tr("ダーク") else "light"
        if mode == self.appearance_mode and not self.color_theme:
            return
        self._apply_theme_now(mode, "")

    def _show_settings_note(self, note: str):
        """設定ポップアップ内の案内欄へ表示する(無ければダイアログ)。"""
        lbl = getattr(self, "_settings_note", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=note)
        else:
            messagebox.showinfo("Settings", note, parent=self.root)

    def _on_show_bg_change(self):
        """=262: 背景イラストの表示ON/OFFを反映し設定へ保存する。"""
        self.bg_art.set_user_enabled(self.show_bg_var.get())
        self.save_app_config()

    # =263: 透け具合の表示名(弱/中/強)と保存値(weak/mid/strong)の相互変換。
    _BG_ALPHA_LABELS = (("弱", "weak"), ("中", "mid"), ("強", "strong"))

    def _bg_alpha_level(self) -> str:
        """現在の透け具合コンボの表示名 → 保存値(weak/mid/strong)。"""
        v = self.bg_alpha_var.get()
        for ja, level in self._BG_ALPHA_LABELS:
            if v == tr(ja):
                return level
        return "mid"

    def _on_bg_alpha_change(self, _choice=None):
        """=263: 背景イラストの透け具合を反映し設定へ保存する。"""
        self.bg_art.set_alpha_level(self._bg_alpha_level())
        self.save_app_config()

    def _on_graph_fps_change(self, choice: str):
        """=104/=106: 描画更新頻度(60fps/30fps)を反映し設定へ保存する。

        =229: **開いているアイテムレビュー画面にも即反映**する
        (レビュー画面も同じ設定に従うようになったため)。
        """
        self._graph_interval_ms = 33 if "30" in str(choice) else 16
        self.save_app_config()
        try:
            win = getattr(self, "_editor_win", None)
            dlg = getattr(win, "_review_dlg", None) if win else None
            if dlg is not None and dlg.winfo_exists():
                dlg.refresh_ms = self._graph_interval_ms
        except Exception:
            pass

    def _toggle_theme(self):
        """外観を ダーク⇄ライト で即時切替する。

        =151: カラーテーマ選択中に呼ばれたら**解除して**素のダーク/ライトへ。
        (6色は「ライトの他バリエーション」=2026-08-15ユーザー決定)
        """
        mode = "light" if self.appearance_mode == "dark" else "dark"
        self._apply_theme_now(mode, "")

    def _refresh_arrow_icons(self):
        """ページ切替◀▶の三角アイコンを現在のテーマ色で描き直す(=111)。

        tk.PhotoImage は CTkImage と違って明暗の切替を自前で行う必要がある。
        画像への参照は self._arrow_photos に残しておく(参照が切れると
        tk 側で破棄されてボタンが空になる)。
        """
        # ctk.get_appearance_mode() は起動直後だとまだ "System" 判定のことが
        # あるので、RVPが持っている現在値を正とする。
        dark = getattr(self, "appearance_mode", "dark") == "dark"
        for name, btn in (("left", getattr(self, "play_prev_btn", None)),
                          ("right", getattr(self, "play_next_btn", None))):
            if btn is None:
                continue
            try:
                img = _triangle_photo(name, dark=dark)
                self._arrow_photos[name] = img      # 参照を保持
                # CTkImage以外を渡すと「HighDPIで拡縮できない」旨の警告が
                # 毎回出る。13pxの小さな三角なので実害はなく、テーマ切替の
                # たびにコンソールが荒れるほうが困るので黙らせる。
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    btn.configure(image=img)
            except Exception:
                # 万一描けなくても操作性は落とさない(文字へフォールバック)
                try:
                    btn.configure(text="<" if name == "left" else ">")
                except Exception:
                    pass

    def _refresh_slider_theme(self):
        """テーマ切替時に補正スライダー(tk.Canvas)の背景を再描画で追従させる。

        CTkウィジェットは set_appearance_mode で自動追従するが、素の tk.Canvas
        (RangeSlider)は自前で背景色を切り替えるため明示的に再描画する。
        """
        for g in getattr(self, "track_groups", {}).values():
            for key in ("slider", "bar"):     # =151 駆動値バー(ZoneBar)も
                w = g.get(key)
                if w is not None:
                    try:
                        w._redraw()
                    except Exception:
                        pass
        gv = getattr(self, "graph_view", None)
        if gv is not None:
            gv._redraw()          # =69 グラフも自前で色を切り替える

    def _refresh_theme_widgets(self):
        """=151: テーマ切替のあと、CTk任せにできない部分を追従させる。

        `apptheme.switch()` は全**CTk**ウィジェットを塗り直すが、素の
        tk.Canvas に自前描画しているもの(補正スライダー・駆動値バー・
        グラフ・イベント遷移図)と tk.PhotoImage(◀▶の三角)は対象外なので、
        ここで明示的に描き直す。開いている別ウィンドウ(編集画面など)にも
        `refresh_theme()` があれば波及させる。
        """
        try:
            self._refresh_slider_theme()
            self._refresh_arrow_icons()
        except Exception:
            logger.exception("refresh theme widgets failed")
        # イベント遷移図/ステート図。表示中でなければ次に開いたときに
        # _on_play_page_shown() が強制再描画するので、ここは表示中だけでよい。
        try:
            self._map_sig = None
            self._update_event_map()
        except Exception:
            logger.exception("refresh event map failed")
        # 開いている Toplevel(編集画面・ヘルプ等)へ波及させる(=149の
        # 全Toplevelアイコン適用と同じ考え方)。
        for w in list(self.root.winfo_children()):
            fn = getattr(w, "refresh_theme", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    logger.exception("refresh theme of %r failed", w)

    def _apply_theme_now(self, mode: str, theme: str):
        """=151: 外観(ダーク/ライト/カラーテーマ)を**その場で**適用する。

        カラーテーマは「ライトの他バリエーション」として扱う(2026-08-15
        ユーザー決定)。したがって theme を選べばライト基調になり、
        ダーク/ライトを選べばカラーテーマは解除される。
        """
        theme = theme or ""
        mode = "light" if theme else mode
        self.color_theme = theme
        self.appearance_mode = mode
        # 順番: 先にモードを合わせてから配色を切り替える(どちらも全
        # ウィジェットの塗り直しを走らせるので、逆だと一瞬素の色が見える)。
        if ctk.get_appearance_mode().lower() != mode:
            ctk.set_appearance_mode(mode)
        apptheme.switch(theme)
        self._refresh_theme_widgets()
        self._update_theme_btn()
        self.save_app_config()
        # =108: CTkがタイトルバー色の塗り替えで小窓を withdraw→deiconify
        # することがある(Windowsのみ)。設定窓の見え方を後追いで確認する。
        try:
            self.root.after(60, self._reassert_settings_win)
        except Exception:
            pass

    def _update_theme_btn(self):
        # =105: 旧☀/☾ボタンは廃止。設定ダイアログの外観メニューへ現在値を反映
        var = getattr(self, "appearance_var", None)
        if var is not None:
            if self.color_theme:      # =134: パステル選択中はテーマ名を表示
                var.set(tr(apptheme.THEME_LABELS.get(self.color_theme, "")))
            else:
                var.set(tr("ダーク") if self.appearance_mode == "dark"
                        else tr("ライト"))

    def _on_language_change(self, choice: str):
        new_lang = "ja" if choice == "日本語" else "en"
        if new_lang == LANG:
            return
        set_language(new_lang)
        note = ("言語設定を保存しました。アプリの再起動後に反映されます。\n"
                "Language preference saved. "
                "It takes effect after restarting the app.")
        # =105: 設定ダイアログが開いていればインラインで案内(モーダル回避)
        lbl = getattr(self, "_settings_note", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=note)
        else:
            messagebox.showinfo("Language / 言語", note, parent=self.root)

    def _on_font_change(self, choice: str):
        """=119: 設定のフォントメニュー。保存のみ行い、反映は再起動後。

        CTkFont はウィジェット生成時に family を確定するため実行中は
        切り替えられない(言語設定と同じ扱い)。選んだフォントがこの端末に
        無い場合はその旨を案内する(保存はする=別の端末では有効になり得る)。
        """
        fam = "" if choice == tr("システム標準") else choice
        cfg = load_config()
        # =293: キーが無い(初回起動=既定 BIZ UDPGothic を表示中)ときは
        # 選択値を必ず保存して明示化する(システム標準を選んでも次回
        # 既定へ戻らないように)。
        if "font_family" in cfg and cfg.get("font_family") == fam:
            return
        # OptionMenu経由なら var 設定済みだが、直接呼ばれても成立するように
        self.font_var.set(choice)
        self.save_app_config()
        note = tr("フォント設定を保存しました。アプリの再起動後に反映されます。")
        if fam and not appfont.resolve(self.root, fam):
            note += "\n" + tr("(このフォントはこの端末に見つかりません。"
                              "見つかるまでは標準フォントで表示します)")
        lbl = getattr(self, "_settings_note", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=note)
        else:
            messagebox.showinfo("Settings", note, parent=self.root)

    # ---------- 接続タブ ----------

    def _build_tab_connection(self, tab):
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="x", padx=8, pady=8)

        ctk.CTkLabel(
            wrap, text=tr("Intiface Central サーバーURL"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w")

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        self.url_var = tk.StringVar(value=DEFAULT_INTIFACE_URL)
        self.url_entry = ctk.CTkEntry(
            row, textvariable=self.url_var,
            placeholder_text="ws://127.0.0.1:12345", height=36,
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_connect = ctk.CTkButton(
            row, text=tr("接続"), width=100, height=36,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self.on_connect,
        )
        self.btn_connect.pack(side="left")

        # =78: Intiface Central への自動接続(既定ON・configへ保存)。
        # 未接続の間はポートプローブ(5秒毎)でサーバーの起動を静かに待ち、
        # 開いていれば接続を試みる。切断後の自動再接続もこの1つのチェックが担う
        # (旧「接続が切れたら自動で再接続する」を統合)。
        self.auto_connect_var = tk.BooleanVar(value=True)
        self.auto_connect_check = ctk.CTkCheckBox(
            wrap, text=tr("Intiface Centralへ自動で接続する"),
            variable=self.auto_connect_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_auto_connect_toggle,
        )
        self.auto_connect_check.pack(anchor="w", pady=(10, 0))

        # =88: TWISTのサブ機能スイッチ(=79)は廃止。TWIST欄・エディタの
        # twist 種別は常時表示になった(相関制御が分かりにくいという
        # ユーザー判断。旧コンフィグの twist_enabled は読み捨てる)。

        # =80: TCodeデバイス(シリアル直結)。FunSR1/OSR2/SR6 などのtwist軸(R0)は
        # buttplug経由では駆動できない(LinearCmd→L{N}写像のみ)ため、
        # 対象デバイスはCOMポートへTCodeコマンドを直接送る。接続中は
        # linear/twist の送信先がIntifaceからこちらへ替わる。
        ctk.CTkLabel(
            wrap, text=tr("TCodeデバイス(シリアル直結)"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(20, 4))
        tc_row = ctk.CTkFrame(wrap, fg_color="transparent")
        tc_row.pack(fill="x")
        self.tcode_port_var = tk.StringVar(value="")
        self.tcode_port_menu = ctk.CTkOptionMenu(
            tc_row, variable=self.tcode_port_var, width=170, height=32,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED)   # =114: ライトは黒(COM6等が薄かった)
        self.tcode_port_menu.pack(side="left")
        self.tcode_refresh_btn = ctk.CTkButton(
            tc_row, text="⟳", width=34, height=32,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._refresh_tcode_ports)
        self.tcode_refresh_btn.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(tc_row, text=tr("ボーレート"),
                     font=ctk.CTkFont(size=12), text_color=LABEL,
                     ).pack(side="left", padx=(12, 4))
        self.tcode_baud_var = tk.StringVar(value=str(tcode_client.DEFAULT_BAUD))
        self.tcode_baud_entry = ctk.CTkEntry(
            tc_row, textvariable=self.tcode_baud_var, width=80, height=32)
        self.tcode_baud_entry.pack(side="left")
        self.tcode_connect_btn = ctk.CTkButton(
            tc_row, text=tr("接続"), width=70, height=32,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self.on_tcode_connect)
        self.tcode_connect_btn.pack(side="left", padx=(10, 0))
        if tcode_client.HAS_SERIAL:
            # =91: 説明文は削除(ユーザー依頼7=接続タブの簡素化。詳細は
            # README_DEV/ヘルプ参照)。ラベル自体は接続ステータス表示に使う。
            tc_note = ""
        else:
            tc_note = tr("pyserialがインストールされていません(pip install pyserial で有効化)")
            self.tcode_connect_btn.configure(state="disabled")
        self.tcode_status_label = ctk.CTkLabel(
            wrap, text=tc_note,
            font=ctk.CTkFont(size=11), text_color=MUTED,
            anchor="w", justify="left", wraplength=560)
        self.tcode_status_label.pack(anchor="w", pady=(4, 0))
        _startup_mark("build: 接続タブ(TCodeポート列挙の前)")
        self._refresh_tcode_ports()
        # =92: Windowsではシリアルポート列挙(特にBluetooth仮想COM)が
        # 数秒かかる事例が知られているため、個別に計測する
        _startup_mark("build: TCodeポート列挙(pyserial comports)")

        # 動画プレーヤー(mpv)のパス設定(動画対応=48)。mpvはオプショナル:
        # 動画つきシナリオの再生時にだけ使われる(音声のみのユーザーは設定不要)
        # =107: 動画を使わない人には常時ノイズになるため、見出しを
        # 「▸ 動画プレーヤー(mpv)の設定」のテキストリンク調トグルにして
        # 折りたたむ(既定=閉じる・開閉状態はコンフィグ保存)。動画つき
        # シナリオを読み込んだときは自動で開く(_load_scenario)。
        self.mpv_toggle_btn = ctk.CTkButton(
            wrap, text="", width=240, height=24, anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent", border_width=0, text_color=LABEL,
            hover_color=("gray85", "gray25"),
            command=self._toggle_mpv_section)
        self.mpv_toggle_btn.pack(anchor="w", pady=(20, 2))
        # 折りたたみの中身。pack/pack_forget で出し入れするので、
        # 再表示のときに「接続中デバイス」より前へ戻せるよう before= を使う。
        self.mpv_body = ctk.CTkFrame(wrap, fg_color="transparent")
        mpv_row = ctk.CTkFrame(self.mpv_body, fg_color="transparent")
        mpv_row.pack(fill="x")
        self.mpv_path_var = tk.StringVar(value="")
        self.mpv_path_entry = ctk.CTkEntry(
            mpv_row, textvariable=self.mpv_path_var, height=32,
            placeholder_text=tr("(空欄=自動で探す)"))
        self.mpv_path_entry.pack(side="left", fill="x", expand=True)
        self.mpv_path_entry.bind(
            "<FocusOut>", lambda _e: self._on_mpv_path_change())
        self.mpv_browse_btn = ctk.CTkButton(
            mpv_row, text=tr("参照..."), width=70, height=32,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self.on_browse_mpv)
        self.mpv_browse_btn.pack(side="left", padx=(6, 0))
        self.mpv_test_btn = ctk.CTkButton(
            mpv_row, text=tr("テスト"), width=60, height=32,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self.on_test_mpv)
        self.mpv_test_btn.pack(side="left", padx=(6, 0))
        # =91: 既定の説明文は削除(ユーザー依頼7)。ラベルは「テスト」の
        # 結果表示に使うため空文字で残す。
        self.mpv_status_label = ctk.CTkLabel(
            self.mpv_body, text="",
            font=ctk.CTkFont(size=11), text_color=MUTED,
            anchor="w", justify="left", wraplength=560)
        self.mpv_status_label.pack(anchor="w", pady=(2, 0))

        self.conn_devices_label = ctk.CTkLabel(
            wrap, text=tr("接続中デバイス"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        )
        self.conn_devices_label.pack(anchor="w", pady=(20, 4))
        # 既定は閉じた状態。コンフィグ復元は _apply_saved_config が行う。
        self._mpv_open = False
        self._apply_mpv_open()

        self.device_box = ctk.CTkTextbox(
            wrap, height=140, corner_radius=10,
            font=ctk.CTkFont(size=13),
        )
        self.device_box.pack(fill="x")
        self.device_box.insert("1.0", tr("(未接続)"))
        self.device_box.configure(state="disabled")

        # ROTATEデバイスの割り当て(ufo/a10レーンの付け替え)。
        # 回転デバイスが接続された時だけ行を出す(未接続時は見出しごと非表示)。
        self.rotate_assign_label = ctk.CTkLabel(
            wrap, text=tr("ROTATEデバイスの割り当て"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        )
        self.rotate_assign_hint = ctk.CTkLabel(
            wrap,
            text=tr("各回転デバイスを ROTATE(ufo) / ROTATE(a10cyclonesa) のどちらで動かすか選べます。"
                    "2ロータ機はロータごとに割り当てを分けられます(左右を入れ替えたい時は割り当てを入れ替えます)。"),
            font=ctk.CTkFont(size=11), text_color=LABEL,
            wraplength=520, justify="left",
        )
        self.rotate_assign_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        # 現在表示中のデバイス割り当て行(名前 → ウィジェット群)。
        self._rotate_assign_rows: dict = {}
        self._rotate_assign_sig = None   # 直近の表示シグネチャ(再構築の抑制)

        # =91: 「※ Intiface Central を起動し…」の案内文は削除(ユーザー
        # 依頼7)。ただし _conn_note はデバイス一覧等の pack(before=) の
        # アンカーとして使われているため、見えない1pxフレームとして残す
        # (空のCTkFrameは200px要求するので pack_propagate(False)+height=1)。
        self._conn_note = ctk.CTkFrame(wrap, fg_color="transparent", height=1)
        self._conn_note.pack_propagate(False)
        self._conn_note.pack(fill="x")
        self._refresh_rotate_assign_ui()

    # ---------- シナリオタブ ----------

    def _build_tab_scenario(self, tab):
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=8, pady=8)

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x")
        self.btn_open = ctk.CTkButton(
            row, text=tr("シナリオファイルを開く"), width=180, height=36,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self.on_open_scenario,
        )
        self.btn_open.pack(side="left")

        # =45: 起動時の前回シナリオ自動読込(=43)で「未選択→編集画面」という
        # 新規作成の入口が塞がれたため、明示的な新規作成ボタンを追加
        self.btn_new = ctk.CTkButton(
            row, text=tr("新規作成"), width=100, height=36,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self.on_new_scenario,
        )
        self.btn_new.pack(side="left", padx=(8, 0))

        self.btn_edit = ctk.CTkButton(
            row, text=tr("編集画面"), width=100, height=36,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self.on_edit_scenario,
        )
        self.btn_edit.pack(side="left", padx=(8, 0))

        self.scenario_title_label = ctk.CTkLabel(
            wrap, text=tr("(シナリオ未選択)"),
            font=ctk.CTkFont(size=15, weight="bold"),
            wraplength=540, justify="left",
        )
        self.scenario_title_label.pack(anchor="w", pady=(16, 2))
        self.scenario_path_label = ctk.CTkLabel(
            wrap, text="", text_color=LABEL,
            font=ctk.CTkFont(size=11), wraplength=540, justify="left",
        )
        self.scenario_path_label.pack(anchor="w")

        # ---- 最近のシナリオ(履歴) ----
        ctk.CTkLabel(
            wrap, text=tr("最近のシナリオ"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(16, 4))
        # =139: ウィンドウを縦へ伸ばしたとき広がるのは**この履歴一覧**
        # (ユーザー決定。=57の「シナリオ内容が余りを吸収する」から変更)。
        self.history_frame = ctk.CTkScrollableFrame(
            wrap, height=132, corner_radius=10, fg_color=("gray92", "gray17"))
        self.history_frame.pack(fill="both", expand=True)
        self.history_empty_label = ctk.CTkLabel(
            self.history_frame, text=tr("(まだ履歴はありません)"),
            text_color=LABEL, font=ctk.CTkFont(size=11))

        ctk.CTkLabel(
            wrap, text=tr("シナリオ内容"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(14, 4))

        # =139: シナリオ内容は高さ固定(縦の余りは「最近のシナリオ」が吸収)。
        # 高さはフォントの実測linespaceから動的に求める(WindowsのBIZ UD等、
        # 環境でメトリクスが変わるため)。内側テキストの上下余白=
        # corner_radius×2+1。
        # =140: Windows実機では=139の「4行分」が2行強にしか見えなかった
        # (ユーザー報告)→**倍の8行分**へ(「この倍の高さがちょうどよい」)。
        self.scenario_box = ctk.CTkTextbox(
            wrap, corner_radius=10, height=170, font=ctk.CTkFont(size=12),
        )
        try:
            import tkinter.font as _tkfont
            _ls = _tkfont.Font(
                font=self.scenario_box._textbox.cget("font")
            ).metrics("linespace")
            self.scenario_box.configure(height=_ls * 8 + 21)
        except Exception:
            pass   # 実測に失敗しても既定170pxで動く
        self.scenario_box.pack(fill="x")
        self.scenario_box.insert("1.0", tr("シナリオファイルを開くと、ここに説明が表示されます。"))
        self.scenario_box.configure(state="disabled")

    # ---------- 再生タブ ----------

    def _build_tab_play(self, tab):
        # =60: 縦スクロールバーを廃止(ユーザー要望)。常設の操作バーと
        # ◀▶切替領域で高さを分け合い、どのページも必ず画面内に収める。
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=8, pady=8)
        self.play_wrap = wrap

        # ============ 常設の操作バー(=60 ユーザー要望) ============
        # 経過/全長・シークバー・↺10・再生/一時停止・↻10・音量・◀◀/▶▶ は
        # ページ切替と関係なく**常に**表示する(=57で①再生ページへ入れたが、
        # 使ってみると常時操作できる方がよいという判断)。
        bar = ctk.CTkFrame(wrap, corner_radius=12, fg_color=CARD_COLOR,
                           border_width=1, border_color=CARD_BORDER)
        bar.pack(fill="x")
        self.transport_card = bar
        tp = ctk.CTkFrame(bar, fg_color="transparent")
        tp.pack(fill="x", padx=16, pady=(10, 12))

        # 音声シークバー(再生秒数/合計時間)
        self.time_label = ctk.CTkLabel(
            tp, text="00:00.0 / 00:00.0", text_color=LABEL,
            font=ctk.CTkFont(size=12),
        )
        self.time_label.pack(anchor="w", pady=(6, 0))
        self.seek_slider = ctk.CTkSlider(
            tp, from_=0, to=1000, number_of_steps=1000,
            height=18, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            state="disabled",
        )
        self.seek_slider.set(0)
        self.seek_slider.pack(fill="x", pady=(2, 0))
        self._seek_dragging = False
        self.seek_slider.bind("<Button-1>", self._on_seek_press)
        self.seek_slider.bind("<ButtonRelease-1>", self._on_seek_release)

        # 再生ボタン類(シナリオ終了ボタンは廃止: 誤操作による意図しない終了を防ぐ)
        # 1行目: 10秒巻き戻し / 再生・一時停止 / 10秒早送り / 音量スライダー
        # 2行目: ◀◀現在のイベントを巻き戻す / ▶▶現在のイベントをスキップする
        # 1行目: ↺10・再生/一時停止・↻10・音量を中央、右端に
        # 「自動選択(ランダム)」チェック(=61)。チェックはもともと選択肢
        # カードの中にあったが、そのカードが待機中も常に出ていて切替領域を
        # 削っていたため操作バーへ移した(2行目は◀◀/▶▶の文字が長く、
        # 幅690では入りきらないので1行目に置く)。
        row1 = ctk.CTkFrame(tp, fg_color="transparent")
        row1.pack(fill="x", pady=(10, 0))
        self.auto_select_var = tk.BooleanVar(value=False)
        self.auto_select_check = ctk.CTkCheckBox(
            row1, text=tr("自動選択（ランダム）"), variable=self.auto_select_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_autoselect_toggle)
        transport = ctk.CTkFrame(row1, fg_color="transparent")
        transport.pack()
        self.play_transport_row = transport

        # =228: 幅が文字で変わらない FixedBtn(押すたびに横幅が動く不具合)
        def small_btn(text, command, width=52, height=52, font_size=14):
            return FixedBtn(
                transport, text=text, width=width, height=height,
                corner_radius=height // 2, font=ctk.CTkFont(size=font_size),
                fg_color="transparent", border_width=1,
                border_color=MUTED, text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=command, state="disabled",
            )

        # 渦を巻いた矢印+10: 左回転=10秒巻き戻し / 右回転=10秒早送り
        # =229: 幅は**高さより広い錠剤形**になる値(FB1: 正円は不可)
        self.btn_seek_back = small_btn("↺10", self.on_seek_back10, width=82)
        self.btn_seek_back.pack(side="left", padx=6)

        # =228: 「▶」⇔「❚❚」で幅が変わらないよう FixedBtn。
        # 幅 92 × 高さ 72(角丸36)= **横長の錠剤形**(=229 FB1)
        self.btn_play = FixedBtn(
            transport, text="▶", width=92, height=72,
            corner_radius=36, font=ctk.CTkFont(size=24),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self.on_play_pause, state="disabled",
        )
        self.btn_play.pack(side="left", padx=6)

        self.btn_seek_fwd = small_btn("↻10", self.on_seek_fwd10, width=82)
        self.btn_seek_fwd.pack(side="left", padx=6)

        # 音量スライダー(右端=最大 / 左端=消音)。常時操作可能
        vol_box = ctk.CTkFrame(transport, fg_color="transparent")
        vol_box.pack(side="left", padx=(14, 0))
        ctk.CTkLabel(vol_box, text="🔊", font=ctk.CTkFont(size=14),
                     text_color=LABEL).pack(side="left", padx=(0, 4))
        # =61: 「自動選択」チェックを同じ行へ入れた分、幅を150→104へ
        self.volume_slider = ctk.CTkSlider(
            vol_box, from_=0, to=100, number_of_steps=100, width=104,
            height=16, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            command=self._on_volume_change,
        )
        self.volume_slider.set(100)
        self.volume_slider.pack(side="left")

        event_row = ctk.CTkFrame(tp, fg_color="transparent")
        event_row.pack(pady=(6, 0))
        self.event_btn_row = event_row

        def event_btn(text, command):
            return ctk.CTkButton(
                event_row, text=text, height=30, corner_radius=15,
                font=ctk.CTkFont(size=12),
                fg_color="transparent", border_width=1,
                border_color=MUTED, text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=command, state="disabled",
            )

        self.btn_back = event_btn(tr("◀◀ 現在のイベントを巻き戻す"),
                                  self.on_back_event)
        self.btn_back.pack(side="left", padx=8)
        self.btn_skip = event_btn(tr("▶▶ 現在のイベントをスキップする"),
                                  self.on_skip_event)
        self.btn_skip.pack(side="left", padx=8)

        # ============ ◀▶で巡回する単一領域(=57) ============
        # 以前は「上部=再生まわり(常時)」「下部=◀▶で①②③を切替」の2領域
        # 構成だったが、1600x900のデスクトップでは縦が足りないため、再生
        # まわりも切替対象の1ページにして領域を1つへ統合した(ユーザー要望)。
        # ページ(=150で並び替え): ①再生 / ②イベント遷移 / ③変数・イベントログ /
        # ④デバイス調整 / ⑤グラフ表示。デバイス関連の2枚を末尾へ寄せてあるのは、
        # デバイス連動なしのシナリオで**末尾2枚をまとめて外して3画面**にできる
        # ようにするため(=150)。
        area = ctk.CTkFrame(wrap, fg_color="transparent")
        area.pack(fill="both", expand=True, pady=(8, 0))
        self.play_area = area
        # =60: 縦スクロールバーを廃止した分、◀▶を押しやすく広げる
        # =89: =85のドット(●○○○○)は「小さくて現在位置の把握が難しい」
        # という実機フィードバックで廃止し、画面下部のバーランプへ置き換えた
        # (◀▶ボタンは=84以前の直接packへ戻した)。
        # =110: 枠線(border_width=1)は従来どおり。中の◀▶だけを
        # **文字から描いた三角へ**差し替える(=109で枠線を消したのは
        # 依頼の読み違い。ユーザーの意図は「[◀]の四角い字形をやめて
        # ただの三角にする」で、枠線は残す)。詳細は _triangle_photo。
        # 画像はGCされると消えるので self に参照を残す。テーマ切替では
        # _refresh_arrow_icons() で描き直す(色が明暗で変わるため)。
        self._arrow_photos: dict = {}
        self.play_prev_btn = ctk.CTkButton(
            area, text="", width=self.PAGE_BTN_W,
            fg_color="transparent", border_width=1, border_color=CARD_BORDER,
            text_color=LABEL, hover_color=("gray85", "gray25"),
            command=lambda: self._switch_play_page(-1))
        self.play_prev_btn.pack(side="left", fill="y", padx=(0, 4))
        self.play_next_btn = ctk.CTkButton(
            area, text="", width=self.PAGE_BTN_W,
            fg_color="transparent", border_width=1, border_color=CARD_BORDER,
            text_color=LABEL, hover_color=("gray85", "gray25"),
            command=lambda: self._switch_play_page(+1))
        self.play_next_btn.pack(side="right", fill="y", padx=(4, 0))
        self._refresh_arrow_icons()
        # 領域は「操作バー・選択肢カード・メッセージを引いた残り全部」。
        # height=1 + pack_propagate(False) で中身の高さに引きずられなくする
        # (=60。以前は下限の固定値に合わせていた)。中身は上詰めの
        # まま=余白ができても要素を均等配置し直さない(ユーザー指定)。
        content = ctk.CTkFrame(area, fg_color="transparent", height=1)
        content.pack(side="left", fill="both", expand=True)
        content.pack_propagate(False)
        self.play_content = content

        # ============ ①再生: 音源再生まわり ============
        upper = ctk.CTkFrame(content, corner_radius=12, fg_color=CARD_COLOR,
                             border_width=1, border_color=CARD_BORDER)
        self.play_card = upper
        up = ctk.CTkFrame(upper, fg_color="transparent")
        up.pack(fill="x", padx=16, pady=(12, 14))

        # 選択中シナリオ
        ctk.CTkLabel(
            up, text=tr("選択中のシナリオファイル"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w")
        self.play_scenario_label = ctk.CTkLabel(
            up, text=tr("(未選択)"),
            font=ctk.CTkFont(size=14, weight="bold"),
            wraplength=540, justify="left",
        )
        self.play_scenario_label.pack(anchor="w", pady=(2, 8))

        # ステータス
        self.status_label = ctk.CTkLabel(
            up, text=tr("待機中"),
            font=ctk.CTkFont(size=20, weight="bold"), text_color=LABEL,
        )
        self.status_label.pack(anchor="w")

        # イベント名+経過 / ステート+経過 / 音声(L/C/R)
        self.event_label = ctk.CTkLabel(
            up, text=tr("イベント: -"), text_color=LABEL,
            font=ctk.CTkFont(size=12), wraplength=540, justify="left",
        )
        self.event_label.pack(anchor="w", pady=(6, 0))
        self.state_label = ctk.CTkLabel(
            up, text=tr("ステート: -"), text_color=LABEL,
            font=ctk.CTkFont(size=12), wraplength=540, justify="left",
        )
        self.state_label.pack(anchor="w")
        # =289: 変数の現在値の行(「変数: HP=3 ...」)は廃止(ユーザー決定)。
        # 変数は③変数・イベントログのページでだけ確認する。
        # 再生中のチャンネル(L/C/R)。=68: 1行の「音声: ★C: a.wav / L: b.wav」
        # から**チャンネルごとの行**へ変更した(ユーザー要望=①再生ページの
        # 空きスペースの活用)。行数は常に3で固定なので、名前の長さや
        # チャンネル数が変わっても下の領域が動かない。
        ctk.CTkLabel(
            up, text=tr("再生中のチャンネル"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(10, 2))
        self.play_ch_card = ctk.CTkFrame(up, corner_radius=8,
                                         fg_color=CARD_COLOR)
        self.play_ch_card.pack(fill="x")
        self.ch_mark_labels = {}
        self.ch_name_labels = {}
        self.play_ch_rows = []          # =159: 高さを凍結するために持っておく
        for _cid in ("L", "C", "R"):
            _row = ctk.CTkFrame(self.play_ch_card, fg_color="transparent")
            _row.pack(fill="x", padx=12, pady=4)
            self.play_ch_rows.append(_row)
            ctk.CTkLabel(_row, text=_cid, width=16, anchor="w",
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=LABEL).pack(side="left")
            _mark = ctk.CTkLabel(_row, text="", width=58, anchor="w",
                                 font=ctk.CTkFont(size=11), text_color=ACCENT_TEXT)
            _mark.pack(side="left")
            _name = ctk.CTkLabel(_row, text="—", anchor="w", height=20,
                                 font=ctk.CTkFont(size=13), text_color=MUTED,
                                 wraplength=420, justify="left")
            _name.pack(side="left", fill="x", expand=True)
            self.ch_mark_labels[_cid] = _mark
            self.ch_name_labels[_cid] = _name
        self.play_star_label = ctk.CTkLabel(
            up, text=tr("★=シークバーが追従しているチャンネル"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w",
        )
        self.play_star_label.pack(anchor="w", pady=(2, 0))

        # =159: **「R行と★行だけが動く」のはなぜか**(ユーザー質問への回答)。
        # 3行は上のループで**まったく同じ設定**から作っており、R行に特別な
        # 実装は無い。違うのは**並び順だけ**で、pack は容れ物の高さが足りない
        # とき**最後に置いた子から取り分を削る**。だから縦を縮めると
        # R行 → ★行 の順に潰れ、先に置いたL行・C行は動かないように見える。
        # 高さを固定しても(`configure(height=…)`+`pack_propagate(False)`)、
        # 親が割り当てる区画そのものが小さくなるので**結果は変わらない**
        # (=159で実測して確認)。**根本の対策は「足りない状況を作らない」**
        # ことなので、最小の高さを 700 → `MIN_WIN_H`(790) へ上げた。


        # ============ 選択肢領域(選択肢がアクティブな間だけ表示) ============
        # =60: ◀▶切替領域の**外**(操作バーの下)へ移した。どのページを見て
        # いてもボタンが必ず全部見えるので、スクロールが要らなくなる
        # (=57ではページの中に入れて①へ自動切替していた)。
        self.choice_card = ctk.CTkFrame(wrap, corner_radius=12,
                                        fg_color=CARD_COLOR,
                                        border_width=2, border_color=ACCENT)
        ch_in = ctk.CTkFrame(self.choice_card, fg_color="transparent")
        ch_in.pack(fill="x", padx=16, pady=(10, 12))
        # =61: 自動選択チェックは操作バーへ移した(このカードは選択肢が
        # 実際に出ているときだけ表示する)。
        ch_head = ctk.CTkFrame(ch_in, fg_color="transparent")
        ch_head.pack(fill="x")
        self.choice_head = ch_head
        ctk.CTkLabel(ch_head, text=tr("選択してください"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=ACCENT_TEXT).pack(side="left")
        self.choice_timer_label = ctk.CTkLabel(
            ch_head, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=WARN_TEXT)
        self.choice_timer_label.pack(side="right")
        self.choice_grid = ctk.CTkFrame(ch_in, fg_color="transparent")
        self.choice_grid.pack(fill="x", pady=(8, 0))
        self.choice_buttons: list = []
        self._choice_sig = None

        # ============ 数値入力領域(入力要求がアクティブな間だけ表示) ============
        self.input_card = ctk.CTkFrame(wrap, corner_radius=12,
                                       fg_color=CARD_COLOR,
                                       border_width=2, border_color=ACCENT)
        in_in = ctk.CTkFrame(self.input_card, fg_color="transparent")
        in_in.pack(fill="x", padx=16, pady=(10, 12))
        in_head = ctk.CTkFrame(in_in, fg_color="transparent")
        in_head.pack(fill="x")
        self.input_prompt_label = ctk.CTkLabel(
            in_head, text=tr("数値を入力してください"),
            font=ctk.CTkFont(size=14, weight="bold"), text_color=ACCENT_TEXT)
        self.input_prompt_label.pack(side="left")
        self.input_range_label = ctk.CTkLabel(
            in_head, text="", font=ctk.CTkFont(size=12), text_color=LABEL)
        self.input_range_label.pack(side="left", padx=(10, 0))
        in_row = ctk.CTkFrame(in_in, fg_color="transparent")
        in_row.pack(fill="x", pady=(8, 0))
        self.input_var = tk.StringVar(value="")
        self.input_entry = ctk.CTkEntry(
            in_row, textvariable=self.input_var, width=140, height=36,
            justify="right", font=ctk.CTkFont(size=15))
        self.input_entry.pack(side="left")
        self.input_entry.bind("<Return>", lambda _e: self._on_input_submit())
        self.input_submit_btn = ctk.CTkButton(
            in_row, text=tr("決定"), width=90, height=36,
            font=ctk.CTkFont(size=14),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_input_submit)
        self.input_submit_btn.pack(side="left", padx=(8, 0))
        self.input_error_label = ctk.CTkLabel(
            in_row, text="", font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ERROR_TEXT)
        self.input_error_label.pack(side="left", padx=(12, 0))
        self._input_sig = None
        self._input_bounds = (None, None)

        # ---- ②デバイス調整 ----
        lower = ctk.CTkFrame(content, corner_radius=12, fg_color=CARD_COLOR,
                             border_width=1, border_color=CARD_BORDER)
        self.lower_card = lower
        low = ctk.CTkFrame(lower, fg_color="transparent")
        # =88: TWIST常時表示で5グループが恒常になり、=79時点から7px
        # はみ出していたため、カード内の上下余白を(12,14)→(8,10)へ詰めて
        # 690x820に5グループが収まるようにした(test_play_layoutで検証)。
        low.pack(fill="x", padx=16, pady=(8, 10))

        # LINEAR / ROTATE / VIBRATION
        # gridで列を揃え、3本のスライダー幅を完全に一致させる。
        # 列0=スライダー・駆動値バー(伸縮) / 列1=反転・強度数値 / 列2=動作タイミング
        # 各グループ: 見出し行 → スライダー行(+反転+タイミング) → 駆動値バー行
        self.offset_vars: dict[str, tk.StringVar] = {}
        grid = ctk.CTkFrame(low, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1)

        def head_row(text, value_text):
            head = ctk.CTkFrame(grid, fg_color="transparent")
            title = ctk.CTkLabel(head, text=text,
                                 font=ctk.CTkFont(size=11, weight="bold"),
                                 text_color=LABEL)
            title.pack(side="left")
            value = ctk.CTkLabel(head, text=value_text,
                                 font=ctk.CTkFont(size=11, weight="bold"),
                                 text_color=ACCENT_TEXT)
            value.pack(side="left", padx=(10, 0))
            return value, head, title

        def invert_cell(var, command):
            return ctk.CTkCheckBox(
                grid, text=tr("反転"), variable=var,
                font=ctk.CTkFont(size=11), text_color=LABEL,
                checkbox_width=18, checkbox_height=18, width=54,
                fg_color=ACCENT, hover_color=ACCENT_HOVER,
                command=command)

        def value_cell(text=tr("停止")):
            return ctk.CTkLabel(
                grid, text=text, width=54, height=14, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"), text_color=LABEL)

        # トラックのグループ(並び替え・未接続の灰色表示用)
        self.track_groups: dict = {}

        def register_group(key, head, title, head_value, slider, invert,
                           offset_cell, bar, value):
            self.track_groups[key] = {
                "head": head, "title": title, "head_value": head_value,
                "slider": slider, "invert": invert, "offset": offset_cell,
                "bar": bar, "value": value}

        # --- LINEAR ---
        self.range_label, lin_head, lin_title = head_row(
            "LINEAR", tr("区間補正0～100"))
        # 速度制限(安全機構): フルストロークにかける最短時間で上限速度を決める
        self.speed_limit_var = tk.StringVar(value=tr("中"))
        self.speed_limit_menu = ctk.CTkOptionMenu(
            lin_head, variable=self.speed_limit_var, width=76, height=22,
            font=ctk.CTkFont(size=11),
            values=[tr("なし"), tr("弱"), tr("中"), tr("強")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=lambda _v: self._on_speed_limit_change())
        self.speed_limit_menu.pack(side="right")
        # 用語: 旧「速度制限」→「急動作防止」(2026-07-22ユーザー決定。
        # 区間補正(動く範囲)との違いが分かるよう目的ベースの名前に)
        ctk.CTkLabel(lin_head, text=tr("急動作防止"),
                     font=ctk.CTkFont(size=11), text_color=LABEL
                     ).pack(side="right", padx=(0, 4))
        self.range_slider = RangeSlider(
            grid, from_=0, to=100, step=5, command=self._on_range_change)
        self.invert_var = tk.BooleanVar(value=False)
        self.invert_check = invert_cell(self.invert_var,
                                        self._on_invert_change)
        lin_offset = self._build_offset_cell(grid, "linear")
        self.pos_bar = ZoneBar(grid, mode="linear", height=12)
        self.pos_value_label = value_cell(text="")
        register_group("linear", lin_head, lin_title, self.range_label,
                       self.range_slider, self.invert_check, lin_offset,
                       self.pos_bar, self.pos_value_label)

        # --- TWIST(=79・2軸linearデバイスの2軸目。表示は接続タブのチェックで切替) ---
        self.twist_range_label, twist_head, twist_title = head_row(
            "TWIST", tr("区間補正0～100"))
        self.twist_range_slider = RangeSlider(
            grid, from_=0, to=100, step=5, command=self._on_twist_range_change)
        self.twist_invert_var = tk.BooleanVar(value=False)
        self.twist_invert_check = invert_cell(self.twist_invert_var,
                                              self._on_twist_invert_change)
        twist_offset = self._build_offset_cell(grid, "twist")
        self.twist_bar = ZoneBar(grid, mode="linear", height=12)
        self.twist_value_label = value_cell(text="")
        register_group("twist", twist_head, twist_title,
                       self.twist_range_label, self.twist_range_slider,
                       self.twist_invert_check, twist_offset,
                       self.twist_bar, self.twist_value_label)

        # --- ROTATE ---
        self.rotate_scale_label, rot_head, rot_title = head_row(
            "ROTATE(ufo)", tr("出力補正0～100%"))
        self.rotate_scale_slider = RangeSlider(
            grid, from_=0, to=100, step=5,
            command=self._on_rotate_range_change)
        self.rotate_invert_var = tk.BooleanVar(value=False)
        self.rotate_invert_check = invert_cell(self.rotate_invert_var,
                                               self._on_rotate_invert_change)
        rot_offset = self._build_offset_cell(grid, "rotate")
        self.rotate_bar = ZoneBar(grid, mode="output", height=12)
        self.rotate_value_label = value_cell()
        register_group("rotate", rot_head, rot_title, self.rotate_scale_label,
                       self.rotate_scale_slider, self.rotate_invert_check,
                       rot_offset, self.rotate_bar, self.rotate_value_label)

        # --- ROTATE(a10cyclonesa): A10サイクロンSA専用のrotate ---
        self.a10_scale_label, a10_head, a10_title = head_row(
            "ROTATE(a10cyclonesa)", tr("出力補正0～100%"))
        self.a10_scale_slider = RangeSlider(
            grid, from_=0, to=100, step=5,
            command=self._on_a10_range_change)
        self.a10_invert_var = tk.BooleanVar(value=False)
        self.a10_invert_check = invert_cell(self.a10_invert_var,
                                            self._on_a10_invert_change)
        a10_offset = self._build_offset_cell(grid, TRACK_ROTATE_A10)
        self.a10_bar = ZoneBar(grid, mode="output", height=12)
        self.a10_value_label = value_cell()
        register_group(TRACK_ROTATE_A10, a10_head, a10_title,
                       self.a10_scale_label, self.a10_scale_slider,
                       self.a10_invert_check, a10_offset,
                       self.a10_bar, self.a10_value_label)

        # ROTATEレーンの「左右反転」チェック(ufotwが割り当たった時だけ表示)。
        # タイプB(5列CSV)の左右chを入れ替える。両ロータへ同一補正がかかる。
        self.rotate_swap_var = tk.BooleanVar(value=False)
        self.rotate_swap_check = ctk.CTkCheckBox(
            rot_head, text=tr("左右反転"), variable=self.rotate_swap_var,
            font=ctk.CTkFont(size=11), text_color=LABEL,
            checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=lambda: self._on_rotate_swap_change("ufo"))
        self.a10_swap_var = tk.BooleanVar(value=False)
        self.a10_swap_check = ctk.CTkCheckBox(
            a10_head, text=tr("左右反転"), variable=self.a10_swap_var,
            font=ctk.CTkFont(size=11), text_color=LABEL,
            checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=lambda: self._on_rotate_swap_change("a10"))
        self._swap_checks = {
            "ufo": (self.rotate_swap_check, self.rotate_swap_var),
            "a10": (self.a10_swap_check, self.a10_swap_var)}
        self._swap_vis = {"ufo": False, "a10": False}
        self._retarget_job = None   # =71 停止中の駆動区間追従(間引き用)
        self._twist_retarget_job = None   # =79 TWIST版
        # 未接続プレビュー用: 読込中シナリオが2ch(タイプBのCSV)のrotate内容を
        # 持つレーン。接続デバイスが無くても左右分割表示・左右反転チェックを
        # 出せるようにするための判定に使う(_load_scenario で更新)。
        self._scenario_split_lanes = {"ufo": False, "a10": False}

        # --- VIBRATION ---
        self.vibration_scale_label, vib_head, vib_title = head_row(
            "VIBRATION", tr("出力補正0～100%"))
        self.vibration_scale_slider = RangeSlider(
            grid, from_=0, to=100, step=5,
            command=self._on_vibration_range_change)
        # vibrationに反転は無い(列1は空のまま=列幅は他行で確保)
        vib_offset = self._build_offset_cell(grid, "vibration")
        self.vibration_bar = ZoneBar(grid, mode="output", height=12)
        self.vibration_value_label = value_cell()
        register_group("vibration", vib_head, vib_title,
                       self.vibration_scale_label,
                       self.vibration_scale_slider, None, vib_offset,
                       self.vibration_bar, self.vibration_value_label)

        # 既定順で配置し、接続状況(最初は全て未接続=灰色)を反映する
        self._track_order: list | None = None
        self._track_conn: dict = {}
        self._track_shown: tuple = ()   # =79 表示中のトラック欄(TWIST切替の検知)
        self._grid_track_groups(list(self.TRACK_ORDER))
        self._update_track_conn()

        # ---- ③グラフ表示(=69。実行中スクリプトの波形。◀▶で切替) ----
        # デバイス種別ごとに「いつ・どれくらいの強さで・どう動くか」を
        # 時間軸で見る。視聴中の予見と、編集時のfunscript紐づけ確認が目的。
        self.graph_card = ctk.CTkFrame(content, corner_radius=12,
                                       fg_color=CARD_COLOR,
                                       border_width=1, border_color=CARD_BORDER)
        gp = ctk.CTkFrame(self.graph_card, fg_color="transparent")
        gp.pack(fill="both", expand=True, padx=10, pady=(8, 6))
        self.graph_view = DeviceGraph(gp)
        self.graph_view.on_seek = self._on_graph_seek      # =237
        self.graph_view.pack(fill="both", expand=True)
        # =102: 下部のヒント行に「1枚表示⇄個別表示」の切替ボタンを置く
        # (グラフ上に浮かせると凡例・追従停止表示と重なるため)。
        # ボタンの文言は「押すと切り替わる先」を示す。
        graph_hint_row = ctk.CTkFrame(gp, fg_color="transparent")
        graph_hint_row.pack(fill="x", pady=(4, 0))
        self.graph_overlay_btn = ctk.CTkButton(
            graph_hint_row, text=tr("1枚表示"), width=78, height=22,
            font=ctk.CTkFont(size=11),
            fg_color=("gray78", "gray30"), text_color=("gray15", "gray90"),
            hover_color=("gray70", "gray38"),
            command=self._toggle_graph_overlay)
        self.graph_overlay_btn.pack(side="right")
        ctk.CTkLabel(
            graph_hint_row,
            text=tr("ホイール=時間の拡大縮小 ／ ドラッグ=前後を見る ／ クリック=再生位置へ戻る"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # ---- ④イベント遷移(表示専用の図。◀▶で切替) ----
        # 実行中イベントを黄緑、◀◀で戻れる履歴経路を緑線で表示する。
        # ステート形式イベント実行中は上下半分に分割し、下半分にステート図。
        self.map_card = ctk.CTkFrame(content, corner_radius=12,
                                     fg_color=CARD_COLOR,
                                     border_width=1, border_color=CARD_BORDER)
        mp = ctk.CTkFrame(self.map_card, fg_color="transparent")
        mp.pack(fill="both", expand=True, padx=10, pady=10)
        mp.grid_columnconfigure(0, weight=1)
        mp.grid_rowconfigure(0, weight=1)
        self._map_grid = mp
        ev_wrap = ctk.CTkFrame(mp, corner_radius=10,
                               fg_color=("gray82", "gray24"))
        ev_wrap.grid(row=0, column=0, sticky="nsew")
        ev_wrap.grid_columnconfigure(0, weight=1)
        ev_wrap.grid_rowconfigure(0, weight=1)
        # 高さ: イベント図230/ステート図190(ステート領域を広げる=2026-07-22
        # ユーザー要望でイベント側-50/ステート側+50)
        self.map_canvas = tk.Canvas(ev_wrap, height=230,
                                    highlightthickness=0, bd=0)
        self.map_canvas.grid(row=0, column=0, sticky="nsew",
                             padx=(8, 0), pady=(8, 0))
        map_vs = ctk.CTkScrollbar(ev_wrap, orientation="vertical",
                                  command=self.map_canvas.yview)
        map_vs.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        map_hs = ctk.CTkScrollbar(ev_wrap, orientation="horizontal",
                                  command=self.map_canvas.xview)
        map_hs.grid(row=1, column=0, sticky="ew", padx=(8, 0))
        self.map_canvas.configure(yscrollcommand=map_vs.set,
                                  xscrollcommand=map_hs.set)
        self._install_map_drag_pan(self.map_canvas)   # =287
        # 下半分: ステート図(ステート形式イベント実行中のみ grid する)
        st_wrap = ctk.CTkFrame(mp, corner_radius=10,
                               fg_color=("gray82", "gray24"))
        st_wrap.grid_columnconfigure(0, weight=1)
        st_wrap.grid_rowconfigure(0, weight=1)
        self.map_state_wrap = st_wrap
        # =287: ステート図は1行(scrollregion 高さ130)なので 140 に詰め、
        # 上下分割でも均等割りせず(row1 weight=0)イベント図に残りを回す
        # (従来190・weight=1 で下半分に余白が出ていた=ユーザー報告)
        self.map_state_canvas = tk.Canvas(st_wrap, height=self.STATE_MAP_H,
                                          highlightthickness=0, bd=0)
        self.map_state_canvas.grid(row=0, column=0, sticky="nsew",
                                   padx=(8, 0), pady=(8, 0))
        # イベント図と統一の縦横スクロールバー(将来のステート図拡張にも備える)
        # =287: CTkScrollbar の既定高さ(200)が行の最小高さになるので canvas に合わせる
        map_svs = ctk.CTkScrollbar(st_wrap, orientation="vertical",
                                   height=self.STATE_MAP_H,
                                   command=self.map_state_canvas.yview)
        map_svs.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        map_shs = ctk.CTkScrollbar(st_wrap, orientation="horizontal",
                                   command=self.map_state_canvas.xview)
        map_shs.grid(row=1, column=0, sticky="ew", padx=(8, 0))
        self.map_state_canvas.configure(xscrollcommand=map_shs.set,
                                        yscrollcommand=map_svs.set)
        self._install_map_drag_pan(self.map_state_canvas)   # =287
        # ---- ④変数・イベントログ(テキスト形式・表示専用。◀▶で切替) ----
        # 上段=変数の現在値(key: value)、区切り線、下段=通過したイベント/
        # ステートの追記式ログ(音声・funscript/CSV情報は記載しない)。
        self.log_card = ctk.CTkFrame(content, corner_radius=12,
                                     fg_color=CARD_COLOR,
                                     border_width=1, border_color=CARD_BORDER)
        lg = ctk.CTkFrame(self.log_card, fg_color="transparent")
        lg.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(lg, text=tr("変数(key: value)"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=LABEL, anchor="w").pack(fill="x")
        self.log_vars_box = ctk.CTkTextbox(
            lg, height=92, font=ctk.CTkFont(size=12), wrap="none",
            fg_color=("gray98", "gray14"))
        self.log_vars_box.pack(fill="x", pady=(2, 0))
        self.log_vars_box.configure(state="disabled")
        # 区切り(仕様の「---」に相当)
        ctk.CTkFrame(lg, height=2, fg_color=CARD_BORDER).pack(
            fill="x", pady=(8, 8))
        ctk.CTkLabel(lg, text=tr("イベントログ(通過したイベント・ステート)"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=LABEL, anchor="w").pack(fill="x")
        self.log_events_box = ctk.CTkTextbox(
            lg, font=ctk.CTkFont(size=12), wrap="none",
            fg_color=("gray98", "gray14"))
        self.log_events_box.pack(fill="both", expand=True, pady=(2, 0))
        self.log_events_box.configure(state="disabled")
        self._log_sig = None         # ③の差分更新用シグネチャ
        self._log_len = 0            # ログ欄へ書き出し済みのエントリ数

        # =150: ページは**番号ではなくID**で扱う(PLAY_PAGE_ORDER)。
        # self._play_pages = 現在表示中のページID列(巡回順)、
        # self._play_page  = その中での位置(0起点)。
        # 表示中のページIDは self.play_page_id で取る。
        # 最後に開いていたページの保存は廃止(ユーザー決定)なので、
        # 起動時は常に①再生から始まる。
        self._play_pages = list(self.PLAY_PAGE_ORDER)
        self._play_page = 0
        # (=89 バーランプはこの後で生成されるため、初期点灯は生成直後に行う)
        self._map_data = None        # 図の描画用: 読込シナリオの生JSON dict
        self._map_sig = None         # 差分更新用シグネチャ
        self._map_split = False      # ステート図(下半分)を表示中か

        self.msg_label = ctk.CTkLabel(
            wrap, text="", text_color=ERROR_TEXT,
            font=ctk.CTkFont(size=12), wraplength=540, justify="left",
        )
        # =89: バーランプの高さを捻出するため pady (8,0)→(4,0)
        self.msg_label.pack(anchor="w", pady=(4, 0))

        # =89: ページ位置のバーランプ(ユーザー依頼・=85ドットの置き換え)。
        # 画面下部にページ数ぶんに分割したバーを常設し、現在ページを黄緑
        # (点灯)・他を消灯(黒)で表す。文字や記号は描かない(ユーザー指定)。
        # =150: セグメントは最大数(5)を作っておき、表示中のページ数に
        # 合わせて末尾を pack_forget する(3画面なら3分割になる)。
        # セグメントのクリックでそのページへ直接ジャンプできる(おまけ)。
        # 高さの収支: ランプ14+pady4=18px は、タブ全体の下余白
        # (tabs.pack の pady 18→4)+メッセージ上余白(8→4)の計18pxで
        # 相殺=5ページの表示域は不変(②デバイス調整は空き+1pxしか
        # ないため必須の設計)。
        lamp_row = ctk.CTkFrame(wrap, fg_color="transparent",
                                height=self.PAGE_LAMP_H)
        lamp_row.pack(fill="x", pady=(4, 0))
        lamp_row.pack_propagate(False)
        self.page_lamp_row = lamp_row
        self.page_lamps = []
        for i in range(self.PLAY_PAGE_COUNT):
            seg = tk.Frame(lamp_row, bg=self.PAGE_LAMP_OFF,
                           height=self.PAGE_LAMP_H, bd=0,
                           highlightthickness=0, cursor="hand2")
            seg.pack(side="left", fill="both", expand=True,
                     padx=(0 if i == 0 else 6, 0))
            seg.bind("<Button-1>", lambda _e, p=i: self._goto_play_page(p))
            self.page_lamps.append(seg)
        self._update_page_lamp()     # 起動時は①が点灯
        # 初期ページ(①再生)を表示(=61: pack ではなく place)
        self._play_scroll = 0
        self.play_card.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        # 領域の高さが変わったら(選択肢カードの出し入れ等)配置し直す
        content.bind("<Configure>", lambda _e: self._refresh_play_card())
        for card in (self.play_card, self.lower_card, self.graph_card,
                     self.map_card, self.log_card):
            card.bind("<Configure>", lambda _e: self._refresh_play_card())
        # ホイール: アプリ全体に張って、ハンドラ側で領域内かを判定する
        # (Enter/Leave だと子ウィジェットへ移った瞬間に外れてしまうため)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._on_play_wheel, add="+")

    # ---- 再生タブのページ切替(=150でID化) ----
    #
    # ①再生 / ②イベント遷移 / ③変数・イベントログ / ④デバイス調整 /
    # ⑤グラフ表示。=149以前は 0〜4 の**番号をベタ書き**して判定していたが、
    # 並び替え(=150)と3画面モードで番号が動くため、IDで解決する形にした。
    # 番号(self._play_page)は「表示中のページ列の中での位置」でしかない。

    # ページ切替(◀▶)ボタンの幅。=60で縦スクロールバーを廃止した分を
    # ここへ回して押しやすくした(26→44)。
    PAGE_BTN_W = 44

    # ページID(巡回順)。=150でユーザー依頼により並び替えた。
    # 変更前(=149まで): play → device → graph → map → log
    # デバイス関連(device/graph)を**末尾へ寄せて**あるので、デバイス連動
    # なしのシナリオでは末尾2枚を落とすだけで3画面構成になる。
    PLAY_PAGE_ORDER = ("play", "map", "log", "device", "graph")

    # デバイス連動なしのシナリオで巡回から外すページ(=150)。
    DEVICE_PAGES = ("device", "graph")

    # ページ数の**上限**(=85。バーランプのセグメント生成に使う)。ランプの
    # 生成は _play_cards() の各カードより先に走るため定数で持つ。実際に
    # 表示中の数は len(self._play_pages)(=150で可変になった)。
    PLAY_PAGE_COUNT = len(PLAY_PAGE_ORDER)

    def _play_card_of(self, page_id: str):
        """ページIDに対応するカードウィジェットを返す(=150)。"""
        return {
            "play": self.play_card,      # ①選択中のシナリオ・再生中チャンネル
            "map": self.map_card,        # ②イベント遷移図/ステート図
            "log": self.log_card,        # ③変数・イベントログ
            "device": self.lower_card,   # ④デバイス調整
            "graph": self.graph_card,    # ⑤グラフ表示
        }[page_id]

    def _play_cards(self):
        """表示中のページのカード列(巡回順。=150で可変長になった)。"""
        return tuple(self._play_card_of(p) for p in self._play_pages)

    @property
    def play_page_id(self) -> str:
        """現在表示中のページID(=150)。ページ判定は番号でなくこれで行う。"""
        try:
            return self._play_pages[self._play_page]
        except (IndexError, AttributeError):
            return ""

    # ---- ページ位置のバーランプ(=89。=85のドットを置き換え) ----
    #
    # 画面下部のページ数分割バー。現在ページ=黄緑(点灯)・他=消灯(黒)。
    # 文字・記号は描かない(ユーザー指定)。点灯色は遷移図の「現在実行中」
    # と同じ黄緑(scenario_map.CURRENT_FILL)、消灯色は駆動値バー
    # (ZoneBar.ACTIVE)と同じ黒=既存の配色に合わせた。ダーク/ライトの
    # 切替でも固定色(ZoneBarと同じ方針)。

    PAGE_LAMP_H = 14                 # バーの高さ(px)
    PAGE_LAMP_ON = "#9ccc3c"         # 点灯=scenario_map.CURRENT_FILL と同色
    PAGE_LAMP_OFF = "#1c1c1c"        # 消灯=ZoneBar.ACTIVE と同色

    def _update_page_lamp(self):
        """バーランプを現在ページに合わせて点灯し直す(=89)。"""
        for i, seg in enumerate(self.page_lamps):
            seg.configure(bg=self.PAGE_LAMP_ON if i == self._play_page
                          else self.PAGE_LAMP_OFF)

    def _relayout_page_lamps(self):
        """表示中のページ数に合わせてバーの分割数を合わせる(=150)。

        セグメントは常に PLAY_PAGE_COUNT 個あり、余った末尾を pack_forget
        する。expand=True なので、残ったセグメントが自動で幅を分け合う
        (3画面なら3分割)。戻すときは昇順に pack するので並び順は保たれる。
        """
        n = len(self._play_pages)
        for i, seg in enumerate(self.page_lamps):
            if i < n:
                if not seg.winfo_manager():
                    seg.pack(side="left", fill="both", expand=True,
                             padx=(0 if i == 0 else 6, 0))
            elif seg.winfo_manager():
                seg.pack_forget()
        self._update_page_lamp()

    # ---- ページ構成(5画面/3画面)の切り替え(=150) ----

    def _scan_scenario_has_device(self, sc) -> bool:
        """シナリオがデバイストラック(funscript/csv)を1つでも持つか(=150)。

        1つも無ければ「デバイス連動なしのシナリオ」とみなし、再生タブから
        ④デバイス調整・⑤グラフ表示を外す(この2画面は見ても何も起きない)。
        判定は**シナリオの内容だけ**で行い、デバイスの接続状態は見ない
        (2026-08-15ユーザー決定。挙動を予測しやすくするため)。
        走査に失敗したときは**5画面のまま**にする(隠しすぎない側に倒す)。

        =252: シナリオの「デバイス連動」フラグ(device_enabled)が OFF の
        ときは、トラックの有無にかかわらず False(=④⑤を隠す)。
        トラックの紐づけ定義がJSONに残っていても駆動しないため。
        """
        if not getattr(sc, "device_enabled", True):
            return False
        try:
            for ev in sc.events.values():
                for st in ev.states.values():
                    for ch in st.channels.values():
                        for item in ch.items:
                            if item.tracks:
                                return True
        except Exception:
            logger.exception("scan device tracks failed")
            return True          # 判定できなければ隠さない
        return False

    def _apply_play_pages(self):
        """デバイス連動の有無で再生タブのページ構成を切り替える(=150)。

        シナリオ未読込(起動直後・読み込み失敗)のときは判定できないので
        5画面のまま(2026-08-15ユーザー決定)。
        """
        hide = self.scenario is not None and not self._scenario_has_device
        self._set_play_pages([p for p in self.PLAY_PAGE_ORDER
                              if not (hide and p in self.DEVICE_PAGES)])

    def _set_play_pages(self, pages):
        """表示するページ列を差し替える(=150)。

        表示中のページが消える場合は①再生へ戻す(残ったページの中で番号を
        クランプすると、意図しない画面へ飛んで分かりにくいため)。
        """
        pages = [p for p in self.PLAY_PAGE_ORDER if p in pages] or ["play"]
        if pages == self._play_pages:
            return
        cur = self.play_page_id
        self._play_card_of(cur).place_forget()
        self._play_pages = pages
        self._play_page = pages.index(cur) if cur in pages else 0
        self._play_scroll = 0
        self._place_sig = None
        self._relayout_page_lamps()
        self._refresh_play_card()
        self._on_play_page_shown()

    # ---- 切替領域のホイールスクロール(=61) ----
    #
    # スクロールバーは出さない(=60でユーザーが削除を希望)が、選択肢/数値入力
    # カードが出て領域が縮んだときにページの下端が見えなくなるため、
    # **はみ出しているときだけ**マウスホイールで動かせるようにする。
    # カードは pack ではなく place で置き、y にスクロール量を反映する。

    WHEEL_STEP = 40      # ホイール1ノッチあたりの移動量(px)

    def _play_scroll_max(self) -> int:
        """現在のページがはみ出している量(0=収まっている)。"""
        card = self._play_cards()[self._play_page]
        return max(0, card.winfo_reqheight() - self.play_content.winfo_height())

    def _refresh_play_card(self):
        """現在のページを切替領域へ配置し直す(スクロール量をクランプ)。

        place で高さを変えると <Configure> が返ってきて再入するため、
        同じ配置なら何もしない+再入ガードで無限ループを防ぐ。
        """
        if getattr(self, "_placing", False):
            return
        card = self._play_cards()[self._play_page]
        avail = self.play_content.winfo_height()
        over = max(0, card.winfo_reqheight() - avail)
        self._play_scroll = max(0, min(self._play_scroll, over))
        sig = (self._play_page, over, self._play_scroll)
        if sig == getattr(self, "_place_sig", None):
            return
        self._placing = True
        try:
            # CTkウィジェットの place は width/height を受け付けないので、
            # 収まるときは relheight=1(領域を埋める)、はみ出すときは高さ指定
            # なし(=カードの自然高さ)にして y をずらす。
            if over > 0:
                card.place(x=0, y=-self._play_scroll, relwidth=1.0)
            else:
                card.place(x=0, y=0, relwidth=1.0, relheight=1.0)
            self._place_sig = sig
        finally:
            self._placing = False

    def _on_play_wheel(self, event):
        """再生タブの切替領域上でのホイール操作(はみ出し時のみ効く)。"""
        try:
            if event.widget.winfo_toplevel() is not self.root:
                return          # 編集画面など別ウィンドウのホイールは無視
        except Exception:
            return
        if self.tabs._current != tr("再生"):
            return
        if self.play_page_id == "graph":
            return          # =69 グラフ表示ページのホイールは時間縮尺に使う
        w = self.play_content
        if not w.winfo_ismapped():
            return
        # ポインタが切替領域の中にあるときだけ
        x, y = event.x_root, event.y_root
        x0, y0 = w.winfo_rootx(), w.winfo_rooty()
        if not (x0 <= x <= x0 + w.winfo_width()
                and y0 <= y <= y0 + w.winfo_height()):
            return
        # ③変数・イベントログのテキスト欄は自前でスクロールするので譲る
        t = event.widget
        while t is not None and t is not w:
            if t in (self.log_vars_box, self.log_events_box):
                return
            t = getattr(t, "master", None)
        if self._play_scroll_max() <= 0:
            return
        num = getattr(event, "num", 0)
        delta = getattr(event, "delta", 0)
        step = -1 if (delta > 0 or num == 4) else 1
        self._play_scroll += step * self.WHEEL_STEP
        self._refresh_play_card()

    def _goto_play_page(self, page: int):
        """指定ページ(表示中のページ列での位置)を表示する。"""
        if page != self._play_page:
            self._switch_play_page(page - self._play_page)

    def _goto_play_page_id(self, page_id: str):
        """指定ページIDを表示する(=150。非表示のIDなら何もしない)。"""
        if page_id in self._play_pages:
            self._goto_play_page(self._play_pages.index(page_id))

    def _pack_play_overlay(self, card, focus: bool = True):
        """選択肢/数値入力カードを操作バーの下(切替領域の外)へ表示する(=60)。

        ページ切替と無関係に常に見えるので、=57の「①再生ページへ強制的に
        切り替える」処理は不要になった(focus は呼び出し側の互換のため残す)。
        カードが出た分、下の切替領域が自動で縮む(pack の割り当て順)。
        """
        card.pack(fill="x", pady=(8, 0), before=self.play_area)

    def _switch_play_page(self, delta: int):
        """◀▶ボタンで再生タブのページを巡回切替する。

        ①再生 ⇄ ②イベント遷移 ⇄ ③変数・イベントログ ⇄ ④デバイス調整
        ⇄ ⑤グラフ表示(=150で並び替え)。▶で先頭へ巻き戻り、◀で逆順。
        デバイス連動なしのシナリオでは④⑤が外れ、①②③の3画面で巡回する。
        """
        cards = self._play_cards()
        old = self._play_page
        new = (old + delta) % len(cards)
        if new == old:
            return
        cards[old].place_forget()
        self._play_page = new
        self._play_scroll = 0
        self._place_sig = None
        self._update_page_lamp()     # =89 バーランプを追従
        self._refresh_play_card()
        self._on_play_page_shown()

    def _on_play_page_shown(self):
        """ページが切り替わった直後の初回描画(=150でID判定へ)。

        表示していない間は描画コストを払わない作りなので、出た瞬間に
        1回だけ強制更新する。
        """
        pid = self.play_page_id
        if pid == "graph":
            self._update_graph()                  # =69 グラフを即描画
        elif pid == "map":
            self._map_sig = None                  # 強制再描画
            self._update_event_map(center=True)
        elif pid == "log":
            self._log_sig = None                  # 強制更新
            self._update_run_log()

    # =102/=103: グラフ表示モードの巡回順(overlay, minus)。
    # 個別 → 1枚 → 個別-(将来非表示) → 1枚-(将来非表示) → 個別 → …
    GRAPH_MODES = ((False, False), (True, False),
                   (False, True), (True, True))

    def _toggle_graph_overlay(self):
        """=102/=103: グラフの表示モードを1つ進める(設定へ保存)。"""
        cur = (self.graph_view.overlay, self.graph_view.minus)
        try:
            i = self.GRAPH_MODES.index(cur)
        except ValueError:
            i = -1
        self._set_graph_mode(*self.GRAPH_MODES[(i + 1) % len(self.GRAPH_MODES)])
        self.save_app_config()

    def _set_graph_mode(self, overlay: bool, minus: bool):
        """=102/=103: 表示モードを反映し、ボタンの文言を「切替先」に合わせる。

        「-」サフィックス=マイナス表示(再生位置より右の将来を描かない)。
        """
        self.graph_view.set_overlay(bool(overlay))
        self.graph_view.set_minus(bool(minus))
        i = self.GRAPH_MODES.index(
            (self.graph_view.overlay, self.graph_view.minus))
        nov, nmi = self.GRAPH_MODES[(i + 1) % len(self.GRAPH_MODES)]
        label = (tr("1枚表示") if nov else tr("個別表示")) + ("-" if nmi else "")
        self.graph_overlay_btn.configure(text=label)

    def _update_graph(self):
        """⑤グラフ表示を更新する(=69。約30fpsで呼ばれる)。

        表示中のページでないときは何もしない(描画コストを払わない)。
        """
        if self.play_page_id != "graph" or not self.graph_card.winfo_ismapped():
            return
        self.graph_view.set_snapshot(self.player.graph_snapshot())

    def _animate_graph(self):
        t0 = time.perf_counter()
        try:
            self._update_graph()
        except Exception:
            logger.exception("graph update failed")
        # =104: 既定60fps(16ms)。設定で30fps(33ms)へ切替可。
        # =229: 待ちの決め方は `paced_delay()` へ集約(フレーム開始基準+
        # 描画時間を下回らない=CPUの半分より多くを描画に使わない)
        cost_ms = (time.perf_counter() - t0) * 1000.0
        # =229: フレーム開始基準のペーシングへ(paced_delay の docstring)。
        # 設定 60fps が実際に 60fps で回るようになる。
        self.root.after(paced_delay(self._graph_interval_ms, cost_ms),
                        self._animate_graph)

    def _update_event_map(self, center: bool = False):
        """②イベント遷移の図を差分更新する(_poll_stateから毎回呼ばれる)。

        現在イベント=緑コーナー枠(=124)、◀◀で戻れる履歴経路=緑線、
        実行済み=減光、すごろく通過=0.5秒の明度アップ。イベントが切り替わった
        時だけ現在ノードへ自動センタリングし、それ以外は手動スクロールを尊重。
        """
        if self.play_page_id != "map":
            return
        st = self.player.state
        trail_ids = tuple(st.get("event_trail") or ())
        cur = trail_ids[-1] if trail_ids else ""
        cur_state = st.get("state_id") or ""
        state_trail = tuple(st.get("state_trail") or ())
        # =124: すごろく通過の明度アップ(0.5秒)と実行済みノードの減光。
        # グローの点灯/消灯は sig の変化として扱う(消灯は次のpollで気づく)
        now = time.monotonic()
        glow = tuple(sorted({e for (e, ts) in (st.get("advance_glow") or ())
                             if now - ts < 0.5}))
        visited = tuple(sorted(st.get("visited_events") or ()))
        data = self._map_data
        sig = (id(data), trail_ids, cur, cur_state, state_trail,
               glow, visited, ctk.get_appearance_mode())
        if sig == self._map_sig and not center:
            return
        prev_cur = self._map_sig[2] if self._map_sig else None
        self._map_sig = sig

        c = self.map_canvas
        if not data or not isinstance(data.get("events"), dict):
            c.configure(bg=scenario_map.canvas_bg())
            c.delete("all")
            c.create_text(
                16, 28, anchor="w",
                text=tr("(シナリオを読み込むとイベント図を表示します)"),
                fill="gray55", font=(appfont.FAMILY, 11))
            c.configure(scrollregion=(0, 0, 300, 60))
            self._set_map_split(False)
            return

        pairs = set(zip(trail_ids, trail_ids[1:]))
        positions = scenario_map.draw_event_map(
            c, data, current=cur or None, trail=pairs,
            glow=glow, visited=visited, on_click=None)

        # ステート形式イベント実行中(および停止後の余韻)は下半分にステート図
        ev_raw = data["events"].get(cur) if cur else None
        is_states = bool(ev_raw and isinstance(ev_raw.get("states"), dict))
        self._set_map_split(is_states)
        if is_states:
            scenario_map.draw_state_map(
                self.map_state_canvas, ev_raw,
                current=cur_state or None, trail=set(state_trail),
                on_click=None)

        # イベントが切り替わった時だけセンタリング(手動スクロールを邪魔しない)
        if (center or cur != prev_cur) and cur:
            self._center_map_on(cur, positions)

    def _update_run_log(self):
        """③変数・イベントログを差分更新する(_poll_stateから毎回呼ばれる)。

        上段=変数の現在値(全書き換え)、下段=通過ログ(追記式。縮んだら
        =再生開始/シナリオ切替のリセットなので全消去してやり直す)。
        """
        if self.play_page_id != "log":
            return
        st = self.player.state
        vars_ = st.get("vars") or {}
        run_log = tuple(st.get("run_log") or ())
        sig = (tuple(vars_.items()), len(run_log))
        if sig == self._log_sig:
            return
        self._log_sig = sig

        # 上段: 変数の現在値
        def _fmt_var(v):
            if isinstance(v, float):
                return f"{v:g}"
            return str(v)
        vbox = self.log_vars_box
        vbox.configure(state="normal")
        vbox.delete("1.0", "end")
        if vars_:
            vbox.insert("1.0", "\n".join(
                f"{k}: {_fmt_var(v)}" for k, v in vars_.items()))
        else:
            vbox.insert("1.0", tr("(変数なし)"))
        vbox.configure(state="disabled")

        # 下段: イベントログ(追記式)
        lbox = self.log_events_box
        if len(run_log) < self._log_len:
            lbox.configure(state="normal")
            lbox.delete("1.0", "end")
            lbox.configure(state="disabled")
            self._log_len = 0
        new_entries = run_log[self._log_len:]
        if new_entries:
            # 末尾を見ている時だけ自動スクロール(過去行の閲覧を邪魔しない)
            at_bottom = lbox.yview()[1] > 0.98
            lbox.configure(state="normal")
            for kind, name in new_entries:
                if kind == "event":
                    line = tr("イベント: {0}").format(name)
                elif kind == "state":
                    line = "  " + tr("ステート: {0}").format(name)
                elif kind == "chan":
                    # =56: 解釈後のチャンネル設定(再生方式/終了条件/件数)。
                    # JSONを読まなくても「アプリがどう解釈したか」が分かる。
                    line = "  ・" + name
                elif kind == "item":
                    # =54: 再生を開始したアイテム(chID: ファイル名[ 区間])。
                    # 順番/ランダム再生が効いているかを後から確認できる。
                    line = "    ▶ " + name
                elif kind == "var":
                    # =70: 変数操作(名前: 変更前 → 変更後 (種別 値) [発火場所])
                    line = "    ✎ " + name
                elif kind in ("choice", "input"):
                    # =70: 選択肢/数値入力を表示したこと(候補・制限時間・既定)
                    line = "    ☰ " + name
                elif kind == "warn":
                    line = "    ⚠ " + name
                elif kind == "bgm":
                    # =256: BGMの開始/停止(ノード入場で変化したときだけ記録)
                    line = "    ♪ " + name
                elif kind == "end":
                    # =56: なぜ次へ進んだか(終了条件/移行条件/選択肢の理由)
                    line = "  → " + name
                else:
                    line = "  " + str(name)
                lbox.insert("end", line + "\n")
            lbox.configure(state="disabled")
            self._log_len = len(run_log)
            if at_bottom:
                lbox.see("end")

    STATE_MAP_H = 140   # =287: 再生タブのステート図の高さ(図の実高さ130+余白)

    # ---- =287: 図をマウスドラッグで掴んで移動(スクロールバーの補助) ----
    # 再生タブの図は表示専用(クリック操作なし)なので、左ドラッグをそのまま
    # スクロールに使う。Tk canvas の scan_mark/scan_dragto(gain=1)。
    # スクロール範囲より図が小さい方向は Tk 側で動かないので判定は不要。

    def _install_map_drag_pan(self, canvas):
        canvas.configure(cursor="fleur")
        canvas.bind("<ButtonPress-1>", lambda e, c=canvas: self._map_pan_press(c, e))
        canvas.bind("<B1-Motion>", lambda e, c=canvas: self._map_pan_drag(c, e))
        canvas.bind("<ButtonRelease-1>", lambda e, c=canvas: self._map_pan_release(c, e))

    def _map_pan_press(self, canvas, e):
        canvas.scan_mark(e.x, e.y)
        canvas._rvp_pan = (e.x, e.y)

    def _map_pan_drag(self, canvas, e):
        if getattr(canvas, "_rvp_pan", None) is None:
            return
        canvas.scan_dragto(e.x, e.y, gain=1)

    def _map_pan_release(self, canvas, _e):
        canvas._rvp_pan = None

    def _set_map_split(self, split: bool):
        """②の上下分割(ステート図)の表示/非表示を切り替える。"""
        if split == self._map_split:
            return
        self._map_split = split
        if split:
            self._map_grid.grid_rowconfigure(1, weight=0)   # =287: 固定高さ
            self.map_state_wrap.grid(row=1, column=0, sticky="nsew",
                                     pady=(8, 0))
        else:
            self.map_state_wrap.grid_forget()
            self._map_grid.grid_rowconfigure(1, weight=0)

    def _center_map_on(self, ev_id: str, positions: dict):
        """イベント図のスクロール位置を、指定ノードが中央に来るよう動かす。"""
        if ev_id not in positions:
            return
        c = self.map_canvas
        sr = (c.cget("scrollregion") or "").split()
        if len(sr) != 4:
            return
        total_w, total_h = float(sr[2]), float(sr[3])
        vw = max(c.winfo_width(), 1)
        vh = max(c.winfo_height(), 1)
        x, y = positions[ev_id]
        if total_w > vw:
            c.xview_moveto(max(0.0, min(1.0, (x - vw / 2) / total_w)))
        else:
            c.xview_moveto(0.0)
        if total_h > vh:
            c.yview_moveto(max(0.0, min(1.0, (y - vh / 2) / total_h)))
        else:
            c.yview_moveto(0.0)

    # ================= イベントハンドラ =================

    def on_connect(self):
        self.intiface.url = self.url_var.get().strip()
        self._set_pill(tr("● 接続中..."), WARN_TEXT)
        self.btn_connect.configure(state="disabled")
        self._manual_connecting = True   # =78: 手動接続中は自動接続を止める

        fut = self.runner.submit(self.intiface.connect())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_connect, f))

    def _after_connect(self, fut):
        self.btn_connect.configure(state="normal")
        self._manual_connecting = False
        try:
            fut.result()
            self._want_connected = True   # 以後、切断されたら自動再接続の対象
            self._reconnect_after = 0.0
            self._set_pill(tr("● 接続済み"), OK_TEXT)
            # =272: デバイス欄の描画は _refresh_device_box へ一元化
            self._refresh_device_box(force=True)
            self._refresh_rotate_assign_ui()
        except Exception as e:
            # メッセージボックスは出さず「接続中デバイス」欄に警告を表示する
            # (OK押下の手間を省く)。
            self._set_pill(tr("● 未接続"), MUTED)
            self._set_device_box(
                tr('⚠ 接続できませんでした: {0}\nIntiface Central を起動し、サーバーを開始してから再度お試しください。').format(e))
            # =272: エラー文は接続状況が変わるまで残す(pollに消させない)
            self._device_box_snap = self._device_box_snapshot()

    # ---- =272: 「接続中デバイス」欄の一元描画 ----

    def _device_box_snapshot(self):
        """欄の描画内容を決める状態の組。変化したときだけ描き直す。"""
        try:
            connected = bool(self.intiface.connected)
        except Exception:
            connected = False
        names = tuple(self.intiface.device_names()) if connected else ()
        try:
            has_linear = bool(self.intiface.has_linear) if connected else False
        except Exception:
            has_linear = False
        return (connected, names, has_linear, bool(self.tcode.connected))

    def _refresh_device_box(self, force: bool = False):
        """「接続中デバイス」欄を現在の接続状況で描き直す(=272)。

        Intiface(Bluetooth)・COM(TCode)いずれの接続状況が変わっても
        _poll_state 経由でここに来て、古い表示(切断済みデバイス名や
        解消済みの注意書き)が残らないようにする。
        linear非対応の注意は **COMポート接続中は表示しない**(COM経由で
        linear/twistデバイスが動くため。「linear接続に失敗した」ように
        読めてしまうという実機フィードバックへの対応)。
        """
        snap = self._device_box_snapshot()
        if not force and snap == self._device_box_snap:
            return
        self._device_box_snap = snap
        connected, names, has_linear, tcode_on = snap
        if not connected:
            text = tr("(未接続)")
            if tcode_on:
                # Intiface未接続でもCOMは生きていることが分かるようにする
                text += "\n" + tr("(COMポートは接続済: linear/twistはCOMポートへ送られます)")
        else:
            text = "\n".join(f"・{n}" for n in names) if names \
                else tr("(デバイスが見つかりません)")
            if not has_linear and not tcode_on:
                text += "\n\n" + tr("⚠ linear対応デバイスが見つかりません。音声のみで再生されます。")
        self._set_device_box(text)

    # ---- 自動接続(=78。旧: 自動再接続) ----

    AUTO_CONNECT_INTERVAL = 5.0   # プローブ間隔・失敗後のバックオフ(秒)

    def _on_auto_connect_toggle(self):
        """自動接続チェックの操作。OFF→ONで即試行できるよう猶予をリセットし、保存。"""
        if self.auto_connect_var.get():
            self._reconnect_after = 0.0
        self.save_app_config()

    # ---- TCodeデバイス(シリアル直結・=80) ----

    def _refresh_tcode_ports(self):
        """シリアルポート一覧を取り直してメニューへ反映する。"""
        ports = tcode_client.available_ports()
        values = [p[0] for p in ports] or [""]
        self.tcode_port_menu.configure(values=values)
        cur = self.tcode_port_var.get()
        if cur not in values:
            self.tcode_port_var.set(values[0])

    def on_tcode_connect(self):
        """TCodeの接続/切断トグル。接続は同期(ポートを開くだけで速い)。"""
        if self.tcode.connected:
            self.tcode.disconnect()
            self.tcode_connect_btn.configure(text=tr("接続"))
            self._set_tcode_status(tr("切断しました"), MUTED)
            self._track_conn = {}   # linear/twist の接続表示を再評価
            self._refresh_device_box()   # =272: 注意書きの出し分けを即反映
            return
        port = self.tcode_port_var.get().strip()
        try:
            baud = int(self.tcode_baud_var.get().strip() or
                       tcode_client.DEFAULT_BAUD)
        except ValueError:
            self._set_tcode_status(tr("ボーレートが数値ではありません"), WARN_TEXT)
            return
        if not port:
            self._set_tcode_status(tr("ポートを選択してください"), WARN_TEXT)
            return
        # =273: 「接続中...」→(成功)→「接続済」の2段階で表示する。
        # ポートを開くのは通常一瞬だが、Windowsではドライバ次第で
        # 数秒かかることがあるため、開いている間の状態を正しく見せる。
        self._set_tcode_status(tr("接続中..."), WARN_TEXT)
        try:
            self.root.update_idletasks()   # 同期接続の前にラベルを描画する
        except Exception:
            pass
        try:
            self.tcode.connect(port, baud)
        except Exception as e:
            self._set_tcode_status(
                tr("接続できません: {0}").format(e), WARN_TEXT)
            return
        self.tcode_connect_btn.configure(text=tr("切断"))
        # =273: 完了後の文言は「接続済」(旧「接続中」は認識の最中と誤読
        # されるという実機フィードバックへの対応)。
        self._set_tcode_status(
            tr("接続済: {0} (linear/twistはこのポートへ送られます)").format(port),
            OK_TEXT)
        self._track_conn = {}
        self._refresh_device_box()   # =272: linear注意書きを即座に取り下げる
        self.save_app_config()

    def _set_tcode_status(self, text: str, color) -> None:
        self._apply(self.tcode_status_label, text=text, text_color=color)

    def _check_auto_connect(self):
        """Intiface Central への自動接続(_poll_state=メインスレッドから毎回呼ぶ)。

        =78: 未接続の間、5秒毎にポートプローブ(probe_ws_port=1ソケットの
        開閉のみ)でサーバーの起動を静かに待ち、ポートが開いていたら本接続を
        試みる。プローブ失敗中はピル表示を変えない(Intifaceを使わない視聴を
        邪魔しない)。接続後に切断された場合も同じ経路で自動再接続になる。
        コルーチンは AsyncRunner(別スレッド)へ投げ、完了は future の
        ポーリングでメインスレッド側で回収する(Tkのクロススレッド呼び出しを
        避け、挙動を予測可能にする)。
        音声再生はそのまま継続し、接続できれば次アイテムからデバイス出力が乗る。
        """
        # 実行中の本接続の完了を回収(メインスレッドで結果を反映)
        if self._reconnect_fut is not None and self._reconnect_fut.done():
            fut, self._reconnect_fut = self._reconnect_fut, None
            self._finish_auto_connect(fut)
        # 実行中のプローブの完了を回収
        if self._probe_fut is not None and self._probe_fut.done():
            fut, self._probe_fut = self._probe_fut, None
            self._finish_probe(fut)
        if not self.auto_connect_var.get():
            return
        if self.intiface.connected or self._manual_connecting:
            return
        if self._reconnect_fut is not None or self._probe_fut is not None:
            return   # 接続 or プローブの実行中
        if time.monotonic() < self._reconnect_after:
            return
        # URL欄の現在値でプローブ(手動接続と同じ対象を見る)
        url = self.url_var.get().strip()
        self._probe_fut = self.runner.submit(probe_ws_port(url))

    def _finish_probe(self, fut):
        """プローブ future の回収。ポートが開いていれば本接続を開始する。"""
        try:
            open_ = bool(fut.result())
        except Exception:
            open_ = False
        if not open_:
            # サーバー不在: 次のプローブまで待つ(表示は変えない=静かに待機)
            self._reconnect_after = time.monotonic() + self.AUTO_CONNECT_INTERVAL
            return
        if self.intiface.connected or self._manual_connecting:
            return   # プローブ中に手動接続が成立した等
        # ポートが開いている → 本接続(一度接続済みなら「再接続中」表示)
        self._set_pill(tr("● 再接続中...") if self._want_connected
                       else tr("● 接続中..."), WARN_TEXT)
        self.intiface.url = self.url_var.get().strip()
        self._reconnect_fut = self.runner.submit(self.intiface.connect())

    def _finish_auto_connect(self, fut):
        """自動接続 future の結果をUIへ反映する(メインスレッド)。"""
        try:
            fut.result()
            self._want_connected = True
            self._set_pill(tr("● 接続済み"), OK_TEXT)
            # =272: 手動接続と同じ一元描画へ(従来この経路はlinear非対応の
            # 注意書きを出しておらず、手動接続と表示が食い違っていた)
            self._refresh_device_box(force=True)
            self._refresh_rotate_assign_ui()
            self._reconnect_after = 0.0
        except Exception:
            # 失敗(サーバーは居るが接続不成立=他アプリ占有など):
            # 次の試行まで待つ。一度も接続していなければピルを未接続へ戻す
            # (「接続中...」で点滅し続けない)。切断復帰待ちは再接続中のまま。
            if self._want_connected:
                self._set_pill(tr("● 再接続中..."), WARN_TEXT)
            else:
                self._set_pill(tr("● 未接続"), MUTED)
            self._reconnect_after = time.monotonic() + self.AUTO_CONNECT_INTERVAL

    def _set_pill(self, text: str, color: str):
        self.conn_pill.configure(text=text, text_color=color)

    def _set_device_box(self, text: str):
        self.device_box.configure(state="normal")
        self.device_box.delete("1.0", "end")
        self.device_box.insert("1.0", text)
        self.device_box.configure(state="disabled")

    # ---- ROTATEデバイスの割り当てUI ----

    def _refresh_rotate_assign_ui(self):
        """接続中の回転デバイスに合わせて割り当て行を作り直す。

        デバイスの集合が変わった時だけ再構築し(シグネチャ比較)、レーンの
        値は毎回現在値へ更新する(再接続で復元された割り当ての反映)。
        回転デバイスが無い時は見出しごと隠す。
        """
        devs = self.intiface.rotate_devices() if self.intiface else []
        sig = tuple((d["name"], d.get("rotors", 1)) for d in devs)
        if not devs:
            # 回転デバイス無し: 見出し・ヒント・行を隠す
            if self._rotate_assign_sig is not None:
                self.rotate_assign_label.pack_forget()
                self.rotate_assign_hint.pack_forget()
                self.rotate_assign_frame.pack_forget()
                for row in self._rotate_assign_rows.values():
                    row["frame"].destroy()
                self._rotate_assign_rows.clear()
                self._rotate_assign_sig = None
            return

        if sig != self._rotate_assign_sig:
            # デバイス集合が変わった → 行を作り直す
            for row in self._rotate_assign_rows.values():
                row["frame"].destroy()
            self._rotate_assign_rows.clear()
            # 見出し・ヒント・枠を接続注記の前に配置
            self.rotate_assign_label.pack(anchor="w", pady=(18, 2),
                                          before=self._conn_note)
            self.rotate_assign_hint.pack(anchor="w", pady=(0, 4),
                                         before=self._conn_note)
            self.rotate_assign_frame.pack(fill="x", before=self._conn_note)
            for d in devs:
                self._build_rotate_assign_row(d)
            self._rotate_assign_sig = sig
        else:
            # 集合は同じ: 現在レーンをメニューへ反映(=120: ロータ別)
            for d in devs:
                row = self._rotate_assign_rows.get(d["name"])
                if row is not None:
                    lanes = d.get("lanes") or [d["lane"]]
                    for i, var in enumerate(row["vars"]):
                        lane = lanes[i] if i < len(lanes) else lanes[-1]
                        var.set(self._lane_label(lane))

    def _lane_label(self, lane: str) -> str:
        """レーン識別子 → 表示ラベル。"""
        return ("ROTATE(a10cyclonesa)"
                if lane == self.intiface.LANE_A10 else "ROTATE(ufo)")

    def _build_rotate_assign_row(self, dev: dict):
        """1デバイス分の割り当て行を作る(=120: 2ロータ機はロータ別2コンボ)。

        1ロータ機: 「名前 ─── [ufo/a10]」(従来)。
        2ロータ機: 「名前(2ロータ) ─ ロータ1→[ufo/a10] ロータ2→[ufo/a10]」。
        両ロータを別レーンへ割り当てる=分割(左右を変えたい時は割り当てを
        入れ替える。分割中は「左右反転」チェックが意味を失うので非表示になる)。
        """
        name = dev["name"]
        row = ctk.CTkFrame(self.rotate_assign_frame, fg_color="transparent")
        row.pack(fill="x", pady=2)
        rotors = dev.get("rotors", 1)
        suffix = tr("(2ロータ)") if rotors >= 2 else ""
        ctk.CTkLabel(
            row, text=f"{name}{suffix}", text_color=LABEL,
            font=ctk.CTkFont(size=12), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        lanes = dev.get("lanes") or [dev["lane"]] * rotors
        vars_, menus = [], []
        if rotors >= 2:
            for i in range(rotors):
                if i > 0:
                    pass  # ラベルが区切りを兼ねる
                ctk.CTkLabel(
                    row, text=f'{tr("ロータ")}{i + 1}→', text_color=LABEL,
                    font=ctk.CTkFont(size=11),
                ).pack(side="left", padx=(10, 2))
                var = tk.StringVar(value=self._lane_label(lanes[i]))
                menu = ctk.CTkOptionMenu(
                    row, variable=var, width=190, height=26,
                    font=ctk.CTkFont(size=11),
                    values=["ROTATE(ufo)", "ROTATE(a10cyclonesa)"],
                    fg_color=("gray75", "gray28"),
                    button_color=("gray70", "gray33"),
                    text_color=COMBO_TEXT,
                    text_color_disabled=COMBO_TEXT_DISABLED,
                    command=lambda _v, n=name, ix=i, rt=rotors:
                        self._on_rotate_rotor_change(n, ix, rt),
                )
                menu.pack(side="left")
                vars_.append(var)
                menus.append(menu)
        else:
            var = tk.StringVar(value=self._lane_label(lanes[0]))
            menu = ctk.CTkOptionMenu(
                row, variable=var, width=190, height=26,
                font=ctk.CTkFont(size=11),
                values=["ROTATE(ufo)", "ROTATE(a10cyclonesa)"],
                fg_color=("gray75", "gray28"),
                button_color=("gray70", "gray33"),
                text_color=COMBO_TEXT,
                text_color_disabled=COMBO_TEXT_DISABLED,
                command=lambda _v, n=name: self._on_rotate_assign_change(n),
            )
            menu.pack(side="right")
            vars_.append(var)
            menus.append(menu)
        self._rotate_assign_rows[name] = {"frame": row, "vars": vars_,
                                          "menus": menus,
                                          "var": vars_[0], "menu": menus[0]}

    def _lane_from_label(self, label: str) -> str:
        """表示ラベル → レーン識別子。"""
        return (self.intiface.LANE_A10
                if label == "ROTATE(a10cyclonesa)" else self.intiface.LANE_UFO)

    def _on_rotate_assign_change(self, name: str):
        """割り当てメニュー操作(1ロータ機): 反映・保存・トラック表示更新。"""
        row = self._rotate_assign_rows.get(name)
        if row is None:
            return
        self.intiface.set_rotate_assign(
            name, self._lane_from_label(row["vars"][0].get()))
        self._after_rotate_assign_change()

    def _on_rotate_rotor_change(self, name: str, index: int, rotors: int):
        """割り当てメニュー操作(=120: 2ロータ機のロータ別)。"""
        row = self._rotate_assign_rows.get(name)
        if row is None or index >= len(row["vars"]):
            return
        self.intiface.set_rotate_rotor_assign(
            name, index, self._lane_from_label(row["vars"][index].get()),
            rotors)
        self._after_rotate_assign_change()

    def _after_rotate_assign_change(self):
        """割り当て変更の共通後処理: 保存・接続表示・分割/反転チェック更新。"""
        self.save_app_config()
        # 接続中デバイス欄のタグ表示とトラックの接続状況を更新する
        # (=272: 一元描画へ。レーンタグ入りのデバイス名で描き直す)
        self._refresh_device_box(force=True)
        self._track_conn = {}     # 次の _update_track_conn で強制再評価
        self._update_track_conn()
        self._update_swap_visibility()   # ufotwの割当先変更を反映

    # ---- ROTATEの左右反転(ufotw) ----

    def _lane_has_ufotw(self, lane: str) -> bool:
        """指定レーンに2ロータ揃った機(ufotw)が割り当たっているか。

        =120: ロータ単位割り当てに対応。分割割り当て(片ロータずつ別レーン)の
        デバイスはどちらのレーンでも「2ロータ揃っていない」=False になり、
        単一バー表示+左右反転チェック非表示になる。
        """
        try:
            return any(d.get("lanes", []).count(lane) >= 2
                       for d in self.intiface.rotate_devices())
        except Exception:
            return False

    def _lane_is_split(self, lane: str) -> bool:
        """このレーンを左右分割(ufotw)で表示すべきか。

        接続デバイスがこのレーンに居る場合は「このレーンに2ロータ揃った機が
        居るか」で判定(=120: 分割割り当てなら単一バー)。
        レーンに接続デバイスが無い(未接続プレビュー)場合は、シナリオ内の
        2ch(タイプBのCSV)内容の有無で判定して、デバイスが無くても左右分割
        バーと左右反転チェックを出す。
        """
        try:
            lane_connected = any(lane in d.get("lanes", [])
                                 for d in self.intiface.rotate_devices())
        except Exception:
            lane_connected = False
        if lane_connected:
            return self._lane_has_ufotw(lane)
        return self._scenario_split_lanes.get(lane, False)

    def _update_swap_visibility(self):
        """ufotwが割り当たったレーンの「左右反転」チェックを出し入れする。"""
        for lane, (chk, _var) in getattr(self, "_swap_checks", {}).items():
            want = self._lane_is_split(lane)
            if want != self._swap_vis.get(lane):
                if want:
                    chk.pack(side="right", padx=(0, 6))
                else:
                    chk.pack_forget()
                self._swap_vis[lane] = want

    def _on_rotate_swap_change(self, lane: str):
        """左右反転チェックの操作: player へ反映して保存する。"""
        var = self.rotate_swap_var if lane == "ufo" else self.a10_swap_var
        self.player.rotate_swap[lane] = bool(var.get())
        self.save_app_config()

    def _rotor_disp(self, lane: str, val) -> tuple:
        """(clockwise, frac) を レーンのレンジ/反転を適用した (speed, positive) に。"""
        cw, frac = val
        inv = self.intiface._lane_invert(lane)
        positive = (not cw) if inv else cw
        if frac <= 0:
            return 0.0, positive
        rmin, rmax = self.intiface._lane_range(lane)
        speed = max(0.0, min(1.0, rmin + frac * (rmax - rmin)))
        return speed, positive

    def _show_rotate_split(self, lane, ch_key, bar, label, connected):
        """ufotwの左右2ロータを上下分割バー+「左X% 右Y%」で表示する。"""
        gray = self.TRACK_DISABLED_COLOR
        ch = self.player.state.get(ch_key) or [(True, 0.0)]
        swap = self.player.rotate_swap.get(lane, False)
        if len(ch) >= 2:
            src_l, src_r = ch[0], ch[1]
        else:
            src_l = src_r = ch[0]
        if swap:                       # デバイスのロータ視点で左右入替
            src_l, src_r = src_r, src_l
        ls, lp = self._rotor_disp(lane, src_l)
        rs, rp = self._rotor_disp(lane, src_r)
        bar.set_split_values(ls if ls > 0 else None, rs if rs > 0 else None)

        def part(sp, positive):
            if sp <= 0:
                return "0"
            pct = int(round(sp * 100))
            return f"{pct}" if positive else f"-{pct}"

        # 表示は「左/右」を横幅を取らない「30/30」形式にする(逆回転は負符号)。
        txt = f"{part(ls, lp)}/{part(rs, rp)}"
        color = LABEL if (ls <= 0 and rs <= 0) else OK_TEXT
        self._apply(label, text=txt,
                    text_color=color if connected else gray)

    # ================= コンフィグ保存・復元 =================
    #
    # 保存先は ~/.rvp_config.json (言語設定と共用)。保存タイミングは
    # アプリ終了時+シナリオ選択時。復元は起動時(_apply_saved_config)。
    # 対象: 音量、LINEAR/ROTATE/VIBRATIONの補正レンジと反転、動作タイミング、
    # 前回シナリオを開いたフォルダ。

    # ---- mpv欄の折りたたみ(=107) ----

    def _apply_mpv_open(self):
        """現在の開閉状態を画面へ反映する(見出しの▸/▾と本体の出し入れ)。"""
        mark = "▾ " if self._mpv_open else "▸ "
        self.mpv_toggle_btn.configure(
            text=mark + tr("動画プレーヤー(mpv)の設定"))
        if self._mpv_open:
            # 「接続中デバイス」見出しより前へ戻す(pack の順序は
            # pack_forget で失われるため before= で位置を指定する)。
            self.mpv_body.pack(fill="x", before=self.conn_devices_label)
        else:
            self.mpv_body.pack_forget()

    def _set_mpv_open(self, open_: bool, save: bool = True):
        """mpv欄の開閉を設定する。save=False はこの場で設定ファイルを
        書かないだけの指定(起動時の復元・シナリオ読み込み時の自動
        オープン用。後者は _load_scenario 末尾の save_app_config で
        結果的に保存され、次回起動時も開いた状態で始まる)。"""
        open_ = bool(open_)
        if open_ == self._mpv_open:
            return
        self._mpv_open = open_
        self._apply_mpv_open()
        if save:
            self.save_app_config()

    def _toggle_mpv_section(self):
        self._set_mpv_open(not self._mpv_open)

    def _scenario_has_video(self, sc) -> bool:
        """シナリオが動画チャンネルを1つでも持つか(=107の自動オープン用)。"""
        try:
            for ev in sc.events.values():
                for st in ev.states.values():
                    if st.has_video:
                        return True
        except Exception:
            pass
        return False

    def _on_mpv_path_change(self):
        """mpvパス欄の確定(フォーカスアウト/参照)。プレーヤーへ反映して保存。"""
        path = self.mpv_path_var.get().strip()
        self.player.mpv_path = path or None
        try:
            self.save_app_config()
        except Exception:
            pass

    def on_browse_mpv(self):
        path = filedialog.askopenfilename(
            title=tr("mpv の実行ファイルを選択"),
            filetypes=[(tr("実行ファイル"), "*.exe"), (tr("すべて"), "*.*")])
        if path:
            self.mpv_path_var.set(path)
            self._on_mpv_path_change()

    def on_test_mpv(self):
        """mpvが見つかるかの簡易チェック(起動はしない)。"""
        from .mpv_client import find_mpv
        import shutil as _sh
        path = self.mpv_path_var.get().strip() or find_mpv()
        if path and (os.path.isfile(path) or _sh.which(path)):
            self.mpv_status_label.configure(
                text=tr("mpv が見つかりました: {0}").format(path),
                text_color=OK_TEXT)
        else:
            self.mpv_status_label.configure(
                text=tr("mpv が見つかりません。パスを指定してください"),
                text_color=WARN_TEXT)

    def _apply_saved_config(self):
        """保存済みコンフィグをUIとプレーヤーへ復元する(不正値は無視)。"""
        cfg = load_config()
        mp = cfg.get("mpv_path")
        if isinstance(mp, str) and mp:
            self.mpv_path_var.set(mp)
            self.player.mpv_path = mp
        # =107: mpv欄の開閉(既定=閉じる)。
        self._set_mpv_open(bool(cfg.get("mpv_open", False)), save=False)

        def num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        def rng(key):
            v = cfg.get(key)
            if (isinstance(v, list) and len(v) == 2
                    and num(v[0]) and num(v[1])):
                lo = max(0, min(100, int(v[0])))
                hi = max(0, min(100, int(v[1])))
                if lo <= hi:
                    return lo, hi
            return None

        vol = cfg.get("volume")
        if num(vol):
            vol = max(0, min(100, int(vol)))
            self.volume_slider.set(vol)
            self._on_volume_change(vol)
        r = rng("linear_range")
        if r:
            self.range_slider.set_values(*r)
        r = rng("rotate_range")
        if r:
            self.rotate_scale_slider.set_values(*r)
        r = rng("rotate_a10_range")
        if r:
            self.a10_scale_slider.set_values(*r)
        r = rng("vibration_range")
        if r:
            self.vibration_scale_slider.set_values(*r)
        if isinstance(cfg.get("linear_invert"), bool):
            self.invert_var.set(cfg["linear_invert"])
            self._on_invert_change()
        if isinstance(cfg.get("rotate_invert"), bool):
            self.rotate_invert_var.set(cfg["rotate_invert"])
            self._on_rotate_invert_change()
        if isinstance(cfg.get("rotate_a10_invert"), bool):
            self.a10_invert_var.set(cfg["rotate_a10_invert"])
            self._on_a10_invert_change()
        offsets = cfg.get("offsets")
        if isinstance(offsets, dict):
            for dtype in ("linear", "twist", "rotate",
                          TRACK_ROTATE_A10, "vibration"):
                v = offsets.get(dtype)
                if num(v):
                    self.offset_vars[dtype].set(f"{float(v):.1f}")
                    self._commit_offset(dtype)
        sl = cfg.get("speed_limit")
        if sl in self.SPEED_LIMIT_MS:
            key2label = {v: k for k, v in self._speed_limit_labels().items()}
            self.speed_limit_var.set(key2label[sl])
            self._on_speed_limit_change()
        # =102/=103: グラフ表示モード(1枚表示・マイナス表示)を復元
        ov, mi = cfg.get("graph_overlay"), cfg.get("graph_minus")
        if isinstance(ov, bool) or isinstance(mi, bool):
            self._set_graph_mode(bool(ov) if isinstance(ov, bool) else False,
                                 bool(mi) if isinstance(mi, bool) else False)
        # =104: グラフ更新頻度(30/60fps)を復元
        gfps = cfg.get("graph_fps")
        if gfps in (30, 60):
            self.graph_fps_var.set(f"{gfps}fps")
            self._graph_interval_ms = 33 if gfps == 30 else 16
        # =262: 背景イラストの表示ON/OFFを復元
        if isinstance(cfg.get("show_background"), bool):
            self.show_bg_var.set(cfg["show_background"])
            self.bg_art.set_user_enabled(cfg["show_background"])
        # =263: 透け具合(weak/mid/strong)を復元
        level = cfg.get("bg_alpha")
        for ja, lv in self._BG_ALPHA_LABELS:
            if level == lv:
                self.bg_alpha_var.set(tr(ja))
                self.bg_art.set_alpha_level(lv)
                break
        # 回転デバイスの割り当てを復元(名前→レーン。有効なレーン値のみ採用)
        assign = cfg.get("rotate_assign")
        if isinstance(assign, dict):
            for name, lane in assign.items():
                if isinstance(name, str):
                    # =120: 値は "ufo"/"a10" またはロータ順リスト。
                    # 検証は set_rotate_assign 側(不正値は無視)。
                    self.intiface.set_rotate_assign(name, lane)
        # ROTATEレーンの左右反転を復元(player と チェックへ反映)
        swap = cfg.get("rotate_swap")
        if isinstance(swap, dict):
            for lane, var in (("ufo", self.rotate_swap_var),
                              ("a10", self.a10_swap_var)):
                if isinstance(swap.get(lane), bool):
                    var.set(swap[lane])
                    self.player.rotate_swap[lane] = swap[lane]
        # =78: Intiface Central への自動接続(既定ON。設定があれば復元)
        if isinstance(cfg.get("auto_connect"), bool):
            self.auto_connect_var.set(cfg["auto_connect"])
        # =80: TCode直結のポート/ボーレート
        if isinstance(cfg.get("tcode_port"), str) and cfg["tcode_port"]:
            vals = list(self.tcode_port_menu.cget("values"))
            if cfg["tcode_port"] not in vals:
                vals = [v for v in vals if v] + [cfg["tcode_port"]]
                self.tcode_port_menu.configure(values=vals)
            self.tcode_port_var.set(cfg["tcode_port"])
        if isinstance(cfg.get("tcode_baud"), str) and cfg["tcode_baud"]:
            self.tcode_baud_var.set(cfg["tcode_baud"])
        # =79: TWIST補正値(=88: twist_enabled は廃止・旧コンフィグの値は読み捨て)
        tr_ = rng("twist_range")
        if tr_:
            self.twist_range_slider.set_values(*tr_)
            self._on_twist_range_change(*tr_)
        if isinstance(cfg.get("twist_invert"), bool):
            self.twist_invert_var.set(cfg["twist_invert"])
            self._on_twist_invert_change()
        last_dir = cfg.get("last_dir")
        if isinstance(last_dir, str) and os.path.isdir(last_dir):
            self._last_dir = last_dir
        # =119: UIフォントの選択を復元(設定メニューの表示用。適用は起動時)
        fam = appfont.configured_family(cfg)   # =293 キー欠落は初期値
        self.font_var.set(fam if fam else tr("システム標準"))
        # 外観テーマを復元(dark/light=即時反映。=134: パステルはライト基調
        # +色の適用は起動時にmain()が実施済みなので、ここでは状態と表示のみ)
        appr = cfg.get("appearance")
        if appr in apptheme.THEMES:
            self.color_theme = appr
            self.appearance_mode = "light"
            ctk.set_appearance_mode("light")
            self._update_theme_btn()
            self._refresh_slider_theme()
            self._refresh_arrow_icons()
        elif appr in ("dark", "light"):
            self.appearance_mode = appr
            ctk.set_appearance_mode(appr)
            self._update_theme_btn()
            self._refresh_slider_theme()
            self._refresh_arrow_icons()   # =111: tk.PhotoImageは自前で追従
        # 履歴リストを復元して表示
        self._refresh_history_list()
        # =57: 再生タブの表示ページの保存/復元は廃止(ユーザー決定)。
        # 領域統合で①が「再生」になったため、起動直後は常に再生画面を
        # 出すのが自然になった。古いコンフィグの "bottom_page" は無視する。

    def save_app_config(self):
        """現在の設定を ~/.rvp_config.json へ保存する(言語等の既存キーは保持)。"""
        cfg = load_config()
        # =88: 廃止したTWISTサブ機能スイッチの旧キーは保存時に取り除く
        cfg.pop("twist_enabled", None)
        cfg.update({
            "volume": int(self.volume_slider.get()),
            "linear_range": [self.range_slider.val_min,
                             self.range_slider.val_max],
            "linear_invert": bool(self.invert_var.get()),
            # =80 TCode直結のポート/ボーレート(接続状態は保存しない=手動接続)
            "tcode_port": self.tcode_port_var.get().strip(),
            "tcode_baud": self.tcode_baud_var.get().strip(),
            # =79 TWIST(2軸目)のサブ機能スイッチと補正値
            "twist_range": [self.twist_range_slider.val_min,
                            self.twist_range_slider.val_max],
            "twist_invert": bool(self.twist_invert_var.get()),
            "rotate_range": [self.rotate_scale_slider.val_min,
                             self.rotate_scale_slider.val_max],
            "rotate_invert": bool(self.rotate_invert_var.get()),
            "rotate_a10_range": [self.a10_scale_slider.val_min,
                                 self.a10_scale_slider.val_max],
            "rotate_a10_invert": bool(self.a10_invert_var.get()),
            "vibration_range": [self.vibration_scale_slider.val_min,
                                self.vibration_scale_slider.val_max],
            "offsets": {d: self.player.offsets_ms[d] / 1000.0
                        for d in ("linear", "twist", "rotate",
                                  TRACK_ROTATE_A10, "vibration")},
            "speed_limit": self._speed_limit_labels().get(
                self.speed_limit_var.get(), "mid"),
            # =134: パステル選択中はテーマ名を保存(dark/lightと同じキー)
            "appearance": self.color_theme or self.appearance_mode,
            # 回転デバイスの割り当て(名前→レーン)。ユーザー上書き分のみ保存。
            "rotate_assign": dict(self.intiface.rotate_assign),
            # ROTATEレーンの左右反転(ufotwタイプBのch入替)。
            "rotate_swap": {"ufo": bool(self.rotate_swap_var.get()),
                            "a10": bool(self.a10_swap_var.get())},
            # 動画プレーヤー(mpv)のパス(空=自動探索)。
            "mpv_path": self.mpv_path_var.get().strip(),
            # =107: 接続タブのmpv欄の開閉(既定=閉じる)。
            "mpv_open": bool(self._mpv_open),
            # =78: Intiface Central への自動接続。
            "auto_connect": bool(self.auto_connect_var.get()),
            # =102: グラフの1枚表示(集約)モード。=103: マイナス表示。
            "graph_overlay": bool(self.graph_view.overlay),
            "graph_minus": bool(self.graph_view.minus),
            # =104: グラフ更新頻度(30/60fps)。
            "graph_fps": 30 if self._graph_interval_ms >= 33 else 60,
            # =119: UIフォント(""=システム標準)。適用は起動時=再起動で反映。
            "font_family": ("" if self.font_var.get() == tr("システム標準")
                            else self.font_var.get()),
            # =262: 背景イラストの表示ON/OFF(視聴側設定)。
            "show_background": bool(self.show_bg_var.get()),
            # =263: 背景イラストの透け具合(weak/mid/strong)。
            "bg_alpha": self._bg_alpha_level(),
        })
        if self._last_dir:
            cfg["last_dir"] = self._last_dir
        save_config(cfg)

    def on_open_scenario(self):
        kwargs = {}
        if self._last_dir and os.path.isdir(self._last_dir):
            kwargs["initialdir"] = self._last_dir
        path = filedialog.askopenfilename(
            title=tr("シナリオファイルを選択"),
            filetypes=[(tr("シナリオファイル"), "*.json"), (tr("すべてのファイル"), "*.*")],
            **kwargs,
        )
        if not path:
            return
        self._load_scenario(path)

    def _on_scenario_dnd_drop(self, event):
        """OSからのシナリオファイルD&D(=270)。

        シナリオタブのフレームへの落下で呼ばれ、「シナリオファイルを開く」と
        同じ読み込みを行う。受け付け条件:
          - 現在タブ=シナリオ のときだけ(登録先がシナリオタブのフレーム
            なので通常ここに来るのはシナリオタブ表示中だが、保険で確認)
          - .json のみ。複数落とされたら先頭の .json だけ読む
        対象外拡張子・読み込み失敗は編集画面のD&D(=44)と同じ流儀で
        黙って無視する(読み込みエラー自体は _load_scenario がダイアログを
        出すのでそれに任せる)。
        """
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            return
        try:
            if self.tabs._current != tr("シナリオ"):
                return
        except Exception:
            return
        js = [p for p in paths
              if os.path.splitext(p)[1].lower() == ".json"]
        if not js:
            return
        try:
            self._load_scenario(js[0])
        except Exception:
            logger.exception("scenario D&D load failed: %s", js[0])

    def _scan_scenario_split_lanes(self, sc) -> dict:
        """未接続プレビュー用: シナリオ内に2ch(タイプBのCSV)のrotate/rotate_a10
        トラックがあるレーンを検出する。

        接続デバイスが無くても、その内容が ufotw(左右2ロータ独立)向けなら
        左右分割バー+左右反転チェックをプレビュー表示できるようにする。
        funscript は必ず1chなので、拡張子が .csv のトラックだけ調べる。
        """
        lanes = {"ufo": False, "a10": False}
        lane_of_type = {TRACK_ROTATE_UFO: "ufo", TRACK_ROTATE_A10: "a10"}
        try:
            for ev in sc.events.values():
                for st in ev.states.values():
                    for ch in st.channels.values():
                        for item in ch.items:
                            for trk in item.tracks:
                                lane = lane_of_type.get(trk.type)
                                if lane is None or lanes[lane]:
                                    continue
                                path = trk.funscript or ""
                                if not path.casefold().endswith(".csv"):
                                    continue   # funscriptは常に1ch
                                try:
                                    if load_rotate_source(path).channels >= 2:
                                        lanes[lane] = True
                                except Exception:
                                    pass
                    if lanes["ufo"] and lanes["a10"]:
                        return lanes
        except Exception:
            pass
        return lanes

    def _load_scenario(self, path: str, goto_play: bool = False):
        """シナリオを読み込む。goto_play=True なら成功時に再生タブへ自動遷移。

        =154: **既定を False に変えた**(ユーザー決定)。=43で「シナリオを選ぶと
        再生タブへ自動で移る」導線を入れたが、実使用のフィードバックで
        ①勝手な画面遷移が普通でない動きで煩わしい
        ②「最近のシナリオ」を行き来してシナリオ内容(detail)を見比べたいときに
        不便、となったため、**手で選んだときはタブを動かさない**。
        自動遷移が残るのは**起動時の自動オープンだけ**(そもそも画面が動く
        わけではなく、起動直後の表示タブが再生になるだけ=最短で視聴に入れる)。
        """
        try:
            self.scenario = Scenario.load(path)
            self._failed_load_path = None
        except Exception as e:
            self.scenario = None
            # =284: 読み込みに失敗しても「編集」からは**このファイルを**開ける
            # ようにパスを覚えておく(素材ファイルを消した後もシナリオ編集で
            # そのアイテムを外せる=ユーザー要望)。編集画面は生のJSONを読む
            # ので素材の有無に関係なく開け、保存時の検証で欠落を報告する。
            self._failed_load_path = path
            self._scenario_split_lanes = {"ufo": False, "a10": False}
            self._map_data = None
            self._map_sig = None
            self._scenario_has_device = True     # =150 未読込は5画面へ戻す
            self._apply_play_pages()
            self.bg_art.set_scenario(None)       # =262 背景イラストを外す
            messagebox.showerror(
                "RVP",
                tr('シナリオファイルの読み込みに失敗しました:\n{0}').format(e)
                + "\n\n" + tr("「編集」ボタンでこのシナリオを編集画面で開けます(素材の抜けは保存時に確認できます)。"))
            return

        sc = self.scenario
        # =130→=131: 読み込み警告(load_warnings)は**ここでは表示しない**。
        # シナリオ選択のたびに警告音つきダイアログが出るのは驚かせるため、
        # 表示は編集画面の保存時(_report=画面内メッセージ・音なし)に一本化
        # した(2026-08-13ユーザー決定)。
        # 選択肢のあるシナリオか(=選択肢イベントが1つでもあるか)を判定し、
        # 選択肢カードの常時表示/非表示を切り替える(次の_poll_stateで反映)
        self._scenario_has_choices = any(
            ev.next_choice or ev.has_state_choice   # =275 ステート移行の選択肢
            for ev in sc.events.values())
        self._sync_autoselect_visible()      # =61: 操作バーのチェック
        # =150: デバイストラックが皆無のシナリオなら、再生タブを3画面
        # (①再生/②イベント遷移/③変数・イベントログ)構成へ切り替える。
        self._scenario_has_device = self._scan_scenario_has_device(sc)
        self._apply_play_pages()
        # =262: 背景イラスト(シナリオ指定×視聴側設定のANDで表示)
        self.bg_art.set_scenario(sc.background)
        # 未接続プレビュー用に、2ch(タイプBのCSV)rotate内容を持つレーンを検出
        self._scenario_split_lanes = self._scan_scenario_split_lanes(sc)
        # =107: 動画つきシナリオなら接続タブのmpv欄を自動で開く(停止状態の
        # このタイミング=再生前に、パス設定が必要なことに気づけるように)。
        # (ここでは書かず、この関数の末尾の save_app_config にまとめる)
        if self._scenario_has_video(sc):
            self._set_mpv_open(True, save=False)
        self._choice_sig = None
        self._choice_display = None
        self._cancel_autoselect()
        # ②イベント状態ビュー用に生JSONを読み込む(編集画面の図と同じデータ源。
        # editor._load_raw を使い、v1 nodes形式/items直書きも図示できる形へ変換)。
        # シナリオ切替で軌跡・図はリセットされる(_map_sig=None で強制再描画)。
        try:
            from .editor import _load_raw
            raw = _load_raw(sc.path)
            self._map_data = raw if isinstance(raw, dict) \
                and isinstance(raw.get("events"), dict) else None
        except Exception:
            self._map_data = None
        self._map_sig = None
        # 別シナリオを読み込んだら、再生中/一時停止中でも停止して
        # イベント・ステート・音声を完全にリセットし、再生待機状態にする
        # (シナリオ履歴からの選択・編集画面の上書き保存の双方をカバー)。
        self.runner.submit(self.player.stop_and_reset(sc.title))
        # =152: イベント数の表示は廃止(ユーザー決定)。視聴前に分岐や長さの
        # ボリュームが推測できてしまい、ネタバレになるため。構成を知りたい
        # ときは編集画面で見る。
        # =153: 表示名は**拡張子を除いたファイル名**に統一(ユーザー決定)。
        name = scenario_display_name(sc.path)
        self.scenario_title_label.configure(text=name)
        self.scenario_path_label.configure(text=sc.path)
        self.play_scenario_label.configure(text=name)
        self._render_scenario_content(sc)
        self.btn_play.configure(state="normal")
        # 前回フォルダを記憶し、即時保存(異常終了でも失われないように)
        self._last_dir = os.path.dirname(os.path.abspath(sc.path))
        self.save_app_config()
        # 履歴へ記録(開いた時=読み込み成功時)
        # =155: 履歴への記録は**ここでは行わない**。シナリオを選んだだけで
        # 「最近のシナリオ」の並びが変わると、見比べている最中に行が動いて
        # しまうため(ユーザー決定)。記録は再生タブへ切り替えた時
        # (_on_tab_changed)に行う。読み込み直後は「まだ記録していない」印。
        self._recorded_path = None
        # =43→=154: 再生タブへの自動遷移は**起動時の自動オープンだけ**に限定。
        # 手で選んだとき(ファイルを開く/履歴クリック)はタブを動かさない。
        if goto_play:
            self.tabs.set(tr("再生"))

    # ---- シナリオ履歴 ----

    # =90: 上限10→100(ユーザー依頼5)。履歴は path/title/ts のメタ情報だけを
    # コンフィグに保存しており、**シナリオファイル本体は行クリック時にしか
    # 読み込まない**(依頼6の懸念=起動時に各シナリオを読む、は元々ない)。
    # 起動時の負荷は「行ウィジェットの生成」(100件で約300個のCTkウィジェット)
    # なので、_refresh_history_list をチャンク分割の遅延構築にして起動を
    # 止めないようにした(下記 HISTORY_CHUNK)。
    HISTORY_MAX = 100
    # 1チャンクで作る行数。先頭チャンクだけ同期で作り(画面には即座に
    # 表示される)、残りは after でバックグラウンド的に継ぎ足す。
    HISTORY_CHUNK = 10

    def _auto_open_last_scenario(self, _retry: int = 0):
        """起動時に前回シナリオ(履歴先頭)を自動で開く(=43 案1・常時有効)。

        成功すると _load_scenario 経由で再生タブへ自動遷移し、「再生」
        1クリックで視聴を開始できる(=154で自動遷移が残るのはここだけ。
        手で選んだときはタブを動かさない)。ファイルが移動/削除されていたり
        壊れていたら**静かに見送り**(起動時にエラーダイアログを出さない・
        履歴からも消さない)、従来どおりシナリオタブから始める。

        =81: ウィンドウがまだ実体化していなければ少し待って再試行する
        (最大2秒=テスト等のwithdraw環境では待ち切って通常どおり開く)。
        UI更新中の想定外の例外でも起動自体は続行する。
        """
        # =93: after(150)のコールバックが「いつ実際に発火したか」を記録する
        # (v92ログでは発火自体が7.7秒遅れていた=それまでループがブロック)
        if _retry == 0:
            _startup_mark("自動オープン: afterコールバック発火")
        try:
            viewable = bool(self.root.winfo_viewable())
            # withdraw/iconify 中(テスト環境や最小化起動)は待っても
            # 実体化しないので、待たずにそのまま開く
            hidden = self.root.wm_state() in ("withdrawn", "iconic")
        except Exception:
            viewable, hidden = True, False
        if not viewable and not hidden and _retry < 20:
            if _retry == 0:
                _startup_mark("自動オープン: ウィンドウ実体化待ちへ")
            self.root.after(100,
                            lambda: self._auto_open_last_scenario(_retry + 1))
            return
        _startup_mark(f"自動オープン: 実体化確認OK(retry={_retry})")
        hist = self._load_history()
        _startup_mark("自動オープン: 履歴読込")
        if not hist:
            return
        path = hist[0]["path"]
        if not os.path.isfile(path):
            return
        _startup_mark("自動オープン: isfile確認(前回シナリオのドライブ応答)")
        try:
            Scenario.load(path)   # 事前検証(失敗はダイアログを出さず見送る)
        except Exception:
            return
        _startup_mark("自動オープン: Scenario.load(事前検証)")
        try:
            # =154: 自動遷移が残るのはここだけ(起動直後の表示タブを再生に
            # する=「再生」1クリックで視聴を始められる状態にする)。
            self._load_scenario(path, goto_play=True)
        except Exception:
            # =81: 自動オープンのUI更新で何が起きても起動は続行する
            import traceback
            traceback.print_exc()
        _startup_mark("自動オープン: _load_scenario 完了")

    def _on_space_key(self, _event=None):
        """スペースキーで再生/一時停止をトグル(=43 案2)。

        文字入力を妨げないよう Entry/Text 系にフォーカスがある時は無効。
        再生ボタンが無効(シナリオ未読込など)の時も何もしない。
        """
        try:
            w = self.root.focus_get()
        except Exception:
            w = None
        if w is not None and w.winfo_class() in ("Entry", "Text", "TEntry"):
            return
        if str(self.btn_play.cget("state")) == "disabled":
            return
        self.on_play_pause()

    def _load_history(self) -> list:
        """設定から履歴リストを読み込む(壊れたエントリは除外)。"""
        cfg = load_config()
        hist = cfg.get("history")
        out = []
        if isinstance(hist, list):
            for e in hist:
                if (isinstance(e, dict) and isinstance(e.get("path"), str)
                        and e["path"]):
                    out.append({
                        "path": e["path"],
                        "title": e.get("title") if isinstance(e.get("title"), str)
                        else "",
                        "ts": e.get("ts") if isinstance(e.get("ts"), (int, float))
                        else 0,
                    })
        return out

    def _save_history(self, hist: list):
        cfg = load_config()
        cfg["history"] = hist[:self.HISTORY_MAX]
        save_config(cfg)

    def _on_tab_changed(self, name: str):
        """タブが切り替わったときの処理(=155)。

        **再生タブへ移った時に、読み込み済みシナリオを履歴の先頭へ記録する。**
        =154までは読み込み時点で記録していたが、シナリオを選んだだけで
        「最近のシナリオ」の並びが変わってしまい、説明文を見比べている最中に
        行が動いて不便だった(ユーザー決定)。「視聴するために再生タブを開いた」
        タイミングを記録の合図にする。

        同じシナリオのまま再生タブを出入りしても記録し直さない
        (`_recorded_path`)。並びも更新日時も変わらないので、re-render と
        コンフィグ書き込みを無駄に走らせないため。
        """
        # =263: 背景イラストは「再生タブ表示中」だけ出す(入る=表示/出る=解除)
        self.bg_art.on_tab_changed()
        if name != tr("再生"):
            return
        sc = self.scenario
        if sc is None or not sc.path:
            return
        if self._recorded_path == os.path.abspath(sc.path):
            return
        self._record_history(sc)

    def _record_history(self, sc: Scenario):
        """開いたシナリオを履歴の先頭へ記録する(同一パスは繰り上げ・重複なし)。"""
        path = os.path.abspath(sc.path)
        self._recorded_path = path      # =155 二重記録の抑止
        hist = self._load_history()
        hist = [e for e in hist if os.path.abspath(e["path"]) != path]
        hist.insert(0, {"path": path, "title": sc.title or os.path.basename(path),
                        "ts": int(time.time())})
        hist = hist[:self.HISTORY_MAX]
        self._save_history(hist)
        self._refresh_history_list()

    def _remove_history(self, path: str):
        hist = [e for e in self._load_history()
                if os.path.abspath(e["path"]) != os.path.abspath(path)]
        self._save_history(hist)
        self._refresh_history_list()

    def _open_from_history(self, path: str):
        """履歴の行クリック。存在すれば読み込み、無ければ一覧から除去。"""
        if not os.path.isfile(path):
            messagebox.showwarning(
                "RVP",
                tr('ファイルが見つかりません(移動・削除された可能性があります):\n{0}').format(path))
            self._remove_history(path)
            return
        self._load_scenario(path)

    def _refresh_history_list(self):
        """履歴フレームの行を作り直す。新しい順・最大 HISTORY_MAX 件。

        =90: 100件ぶんを一気に作ると起動・シナリオ切替のたびに数百msの
        UI停止が起きるため、**チャンク分割の遅延構築**にした。先頭
        HISTORY_CHUNK 件だけ同期で作り(スクロール枠の見えている範囲は
        即座に埋まる)、残りは after(15ms) で1チャンクずつ継ぎ足す。
        """
        # 進行中の遅延構築があればキャンセル(作り直しの重複防止)
        if getattr(self, "_hist_job", None) is not None:
            try:
                self.root.after_cancel(self._hist_job)
            except Exception:
                pass
            self._hist_job = None
        for w in self.history_frame.winfo_children():
            if w is not self.history_empty_label:
                w.destroy()
        hist = self._load_history()
        if not hist:
            self.history_empty_label.pack(anchor="w", padx=6, pady=4)
            return
        self.history_empty_label.pack_forget()
        self._hist_pending = hist[:self.HISTORY_MAX]
        self._build_history_chunk()

    def _build_history_chunk(self):
        """保留中の履歴行を1チャンクぶん生成する(=90)。"""
        self._hist_job = None
        chunk = self._hist_pending[:self.HISTORY_CHUNK]
        self._hist_pending = self._hist_pending[self.HISTORY_CHUNK:]
        for e in chunk:
            self._make_history_row(e)
        # =93: Windowsでのチャンクあたりの所要(フォント実測を含む)を見る
        _startup_mark(f"履歴チャンク構築 {len(chunk)}行(残り{len(self._hist_pending)})")
        if self._hist_pending:
            self._hist_job = self.root.after(15, self._build_history_chunk)

    def _make_history_row(self, e: dict):
        """履歴1件ぶんの行ウィジェットを作る(=90でくくり出し)。"""
        path = e["path"]
        exists = os.path.isfile(path)
        row = ctk.CTkFrame(self.history_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        when = ""
        if e.get("ts"):
            try:
                when = time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(e["ts"]))
            except (ValueError, OSError):
                when = ""
        # =153: 履歴の行も表示名(拡張子を除いたファイル名)だけにする。
        # 記録時の title は使わない=ファイルを改名しても表示が追従する。
        label = scenario_display_name(path)
        if when:
            label += f"   {when}"
        if not exists:
            label += tr("  (見つかりません)")
        # =66: 「✕」を**先に**パックして幅を確保する。行のボタンを先に
        # pack(expand=True) すると、ファイル名が長いときに要求幅が勝って
        # ✕が枠外へ押し出され、押せなくなっていた(ユーザー報告)。
        ctk.CTkButton(
            row, text="✕", width=26, height=28,
            fg_color="transparent", text_color="#e05a5a",
            hover_color=("gray85", "gray28"),
            command=lambda p=path: self._remove_history(p)).pack(side="right")
        # =82: width を明示して**要求幅を固定**する。テキストの長さが
        # 要求幅に反映されると「テキスト変更→レイアウト変化→<Configure>
        # →また省略計算」の発振経路ができ、Windows実機で
        # RecursionError の嵐=起動不能に至った(2026-08-06)。実際の
        # 表示幅は pack(fill=x, expand=True) が与えるので見た目は不変。
        btn = ctk.CTkButton(
            row, text=label, anchor="w", height=28, width=120,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            text_color=(MUTED if not exists else ("gray20", "gray85")),
            hover_color=("gray85", "gray28"),
            command=lambda p=path: self._open_from_history(p))
        btn.pack(side="left", fill="x", expand=True)
        # 収まらないときは中間を「…」で省略し、末尾の日時を残す(=66)
        btn._rvp_full_text = label
        btn.bind("<Configure>", lambda _e, b=btn: self._fit_history_label(b))
        self._fit_history_label(btn)

    @staticmethod
    def _ellipsize(text: str, font, avail: int) -> str:
        """avail ピクセルに収まるよう文字列の**中間**を「…」で省略する(=66)。

        末尾(更新日時)を残したいので中間を削る。
        =96: Windowsでは font.measure 1回が遅い実機がある(=94/=95の
        スタックサンプルで起動時の主要コストと確定)ため、二分探索
        (毎回O(log n)回のmeasure)をやめ、**全体幅から平均文字幅を出して
        「残せる文字数」を推定し、その近傍だけ実測**する方式にした
        (典型3〜5回のmeasureで確定)。
        """
        try:
            if avail <= 0:
                return text
            full_w = font.measure(text)
            if full_w <= avail:
                return text
            ell = "…"
            ell_w = font.measure(ell)
            if ell_w > avail:
                return ""

            def cand(k):
                head = k // 2
                tail = k - head
                return (text[:head] + ell
                        + (text[len(text) - tail:] if tail else ""))

            # 平均文字幅から「残せる文字数」を推定
            avg = max(1.0, full_w / max(1, len(text)))
            k = max(0, min(len(text) - 1, int((avail - ell_w) / avg)))
            # 収まるまで縮める(推定が甘かった場合。1割+1文字ずつ)
            while k > 0 and font.measure(cand(k)) > avail:
                k = min(k - 1, int(k * 0.9))
            best = cand(max(k, 0))
            # 収まったら1文字ずつ伸ばして限界を探す(推定が固かった場合)
            while k < len(text) - 1:
                nxt = cand(k + 1)
                if font.measure(nxt) <= avail:
                    k += 1
                    best = nxt
                else:
                    break
            return best
        except Exception:
            return text

    def _fit_history_label(self, btn):
        """履歴行のラベルを現在のボタン幅に合わせて省略表示する(=66)。

        =82: `<Configure>` ハンドラの中で同期的に configure(text=) すると、
        テキスト変更が新たな <Configure> を**同期的に**誘発してスタックが
        育ち、Windows実機で RecursionError の嵐=起動不能になった。
        実処理は遅延実行へ逃がし(1ボタンにつき保留は1件)、
        イベントの入れ子を構造的に不可能にする。

        =96: after_idle → **after(60ms)** へ変更。after_idle だと起動時の
        最初の update()(CTkのmainloop冒頭)の中で全行ぶん同期実行されて
        初回描画をブロックする(=95後の実機スタックサンプルで確定)。
        60msのタイマーなら①初回描画が先に出る ②起動時の過渡的な
        <Configure>(幅が数回変わる)が1回の計算に集約される、の両得。
        """
        if getattr(btn, "_rvp_fit_pending", False):
            return
        btn._rvp_fit_pending = True
        try:
            self.root.after(60, lambda: self._do_fit_history_label(btn))
        except Exception:
            btn._rvp_fit_pending = False

    def _do_fit_history_label(self, btn):
        """省略表示の実体(after_idle経由でのみ呼ばれる)。

        起動直後はまだレイアウト前(幅1)なので何もしない。実際の幅は
        `<Configure>` で通知されるので、そのたびに計算し直す。
        同じ幅で二度計算しないようにして再入・ちらつきを防ぐ。
        """
        try:
            btn._rvp_fit_pending = False
            if not btn.winfo_exists():
                return
            w = btn.winfo_width()
            if w <= 20:
                return
            if getattr(btn, "_rvp_fit_w", None) == w:
                return
            btn._rvp_fit_w = w
            full = getattr(btn, "_rvp_full_text", "")
            btn.configure(text=self._ellipsize(full, btn.cget("font"), w - 16))
        except Exception:
            pass

    def _render_scenario_content(self, sc: Scenario):
        """シナリオの説明(detail)を表示する。

        イベント/ステート構成の一覧表示はユーザー要望で廃止(2026-07-16)。
        視聴前に見るのは説明文のみとし、構成の確認・変更は編集画面で行う。
        """
        text = sc.detail if sc.detail else tr("(このシナリオには説明がありません)")
        self.scenario_box.configure(state="normal")
        self.scenario_box.delete("1.0", "end")
        self.scenario_box.insert("1.0", text)
        self.scenario_box.configure(state="disabled")

    def _front_editor(self):
        """=152: 既に開いている編集画面があれば前面化して返す(無ければ None)。

        編集画面は**1つまで**(ユーザー決定)。2つ開くと、同じシナリオを別々に
        編集して後から保存したほうが勝つ=編集内容が黙って消える事故になる。
        破棄は winfo_exists() で検出するので、編集画面側に後始末は要らない
        (=118のヘルプ画面と同じ作法)。
        """
        win = getattr(self, "_editor_win", None)
        if win is None:
            return None
        try:
            if win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_set()
                return win
        except Exception:
            pass
        self._editor_win = None
        return None

    def pause_for_review(self) -> float:
        """編集画面のアイテムレビューが始まる前に本編を一時停止する(=164)。

        レビュー画面は pygame.mixer の別チャンネルで鳴らすので技術的には
        重ねられるが、音が混ざって確認にならないため止める(ユーザー決定)。
        戻り値=レビュー画面の初期音量(本編のマスター音量。0.0〜1.0)。
        """
        try:
            if self.player.state.get("status") == "playing":
                self.runner.submit(self.player.pause())
        except Exception:
            logger.exception("pause for review failed")
        try:
            return float(self.player.master_volume)
        except Exception:
            return 1.0

    def _open_editor(self, path):
        """編集画面を開く(既に開いていれば前面化するだけ)。=152"""
        win = self._front_editor()
        if win is not None:
            return win
        from .editor import ScenarioEditor
        try:
            self._editor_win = ScenarioEditor(
                self.root, path,
                on_saved=self._on_editor_saved,
                review_host=self)
            return self._editor_win
        except Exception as e:
            self._editor_win = None
            messagebox.showerror(
                "RVP",
                tr('シナリオファイルの読み込みに失敗しました:\n{0}').format(e))
            return None

    def _on_editor_saved(self, path: str):
        """編集画面で保存されたときの後始末(=236)。

        従来どおり読み込み直した上で、**「最近のシナリオ」の先頭へ記録する**
        (=236 ユーザー要望)。=155 で記録の合図を「再生タブへ移った時」に
        一本化したが、**保存は「そのシナリオを触った」ことが確実**なので、
        並びが動いて困る場面ではない(新規作成した .json が一覧に出ない・
        編集したシナリオが先頭に来ない、というのが要望の理由)。

        記録の日時は**保存した時刻**になる(ユーザー決定)。`_record_history`
        が `_recorded_path` も更新するので、このあと再生タブへ移っても
        二重には記録されない。
        """
        self._load_scenario(path, goto_play=False)
        sc = self.scenario
        # 読み込みに失敗した場合(scenario=None)や、別のシナリオが載って
        # いる場合は記録しない。
        if sc is not None and sc.path \
                and os.path.abspath(sc.path) == os.path.abspath(path):
            self._record_history(sc)

    def on_edit_scenario(self):
        """選択中のシナリオを編集画面で開く(=152: 2つ目は開かない)。"""
        path = self.scenario.path if self.scenario else \
            getattr(self, "_failed_load_path", None)   # =284
        return self._open_editor(path)

    def on_new_scenario(self):
        """新しいシナリオを編集画面で作る(=45)。

        現在選択中のシナリオには影響しない(空の編集画面を開くだけ)。
        新規シナリオが名前を付けて保存されると on_saved 経由で読み込まれ、
        履歴にも記録される(タブは動かさない=編集作業の途中のため)。
        戻り値は編集画面インスタンス(テスト用)。
        =152: 編集画面が既に開いていれば、それを前面化するだけ(2つ開かない)。
        """
        return self._open_editor(None)

    def on_play_pause(self):
        """再生⇔一時停止のトグル。停止/終了後は最初から再生し直す。"""
        status = self.player.state["status"]
        if status == "playing":
            self.runner.submit(self.player.pause())
        elif status == "paused":
            self.runner.submit(self.player.resume())
        else:
            if self.scenario and not self.player.is_playing:
                self.runner.submit(self.player.play(self.scenario))

    def on_seek_back10(self):
        """再生中の音声を10秒巻き戻す(先頭より前は0にクランプ)。"""
        self.runner.submit(self.player.seek_relative(-10_000))

    def on_seek_fwd10(self):
        """再生中の音声を10秒早送りする(末尾付近にクランプ)。"""
        self.runner.submit(self.player.seek_relative(10_000))

    def _on_volume_change(self, value):
        """音量スライダー(0=消音〜100=最大)。再生中にも即時反映される。"""
        self.player.set_master_volume(float(value) / 100.0)

    # linear速度制限: 表示ラベル→コンフィグキー→フルストローク最短時間(ms)
    SPEED_LIMIT_MS = {"none": 0, "low": 150, "mid": 250, "high": 400}

    def _speed_limit_labels(self) -> dict:
        return {tr("なし"): "none", tr("弱"): "low",
                tr("中"): "mid", tr("強"): "high"}

    def _on_speed_limit_change(self):
        """linear速度制限の変更を即時反映する(再生中も有効)。"""
        key = self._speed_limit_labels().get(self.speed_limit_var.get(), "mid")
        self.player.linear_speed_limit_ms = self.SPEED_LIMIT_MS[key]

    def on_skip_event(self):
        self.runner.submit(self.player.skip_event())

    def on_back_event(self):
        self.runner.submit(self.player.back_event())

    def _on_graph_seek(self, ms: float):
        """=237: ③グラフの右ダブルクリック=そこへ再生位置を移す。

        **相対シークで実現する**(ユーザー決定)。グラフの横軸は「イベント
        入場からの経過時間」、シークバーは「シークバー追従チャンネルの経過
        時間」で**原点が違う**ため、絶対値では合わせられない。今の再生位置
        (グラフの縦線=`snapshot["now_ms"]`)との差分だけ ↺10/↻10 と同じ
        `seek_relative` を投げると、**クリックした波形の点がちょうど縦線の
        位置まで来る**(断片の描画位置 t0 が同じ量だけ逆へ動くため)。

        停止中は何も起きない(`seek_relative` が playing/paused 以外を弾く)。
        """
        if self.player.state["status"] not in ("playing", "paused"):
            return
        now = float(self.graph_view.snapshot.get("now_ms", 0.0))
        delta = int(round(float(ms) - now))
        if delta == 0:
            return
        self.runner.submit(self.player.seek_relative(delta))

    def _on_seek_press(self, _event):
        if self.player.state["status"] in ("playing", "paused"):
            self._seek_dragging = True

    def _on_seek_release(self, _event):
        if not self._seek_dragging:
            return
        self._seek_dragging = False
        duration = self.player.state.get("duration_ms", 0)
        if duration > 0 and self.player.state["status"] in ("playing", "paused"):
            ms = int(self.seek_slider.get() / 1000 * duration)
            self.runner.submit(self.player.seek(ms))

    def _on_range_change(self, rmin, rmax):
        """駆動区間スライダーの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_linear_range(rmin, rmax)
        self._apply(self.range_label, text=tr('区間補正{0}～{1}').format(rmin, rmax))
        self._linear_zone = (int(rmin), int(rmax))
        self._update_linear_bar_range()
        self._request_linear_retarget()

    # ---- 停止中の駆動区間追従(=71) ----
    #
    # 停止中に区間を変えると、同じ funscript の pos でも実位置が変わる
    # (例: pos=50 は区間0-100で50、区間50-100で75)。再生中はスクリプトが
    # すぐ次の指示を出すので不要だが、停止中はデバイスが取り残されるため、
    # ここで追いかけさせる。スライダーはドラッグ中に細かく発火するので、
    # RETARGET_MS ごとに間引き、離したあとの最終値も必ず1回送る。

    RETARGET_MS = 100

    def _request_linear_retarget(self):
        """区間変更をデバイスへ反映する(間引きつき)。"""
        if not self._linear_retarget_allowed():
            return
        if getattr(self, "_retarget_job", None) is not None:
            return          # 予約済み。最後の値はタイマー発火時に読む
        self._retarget_job = self.root.after(self.RETARGET_MS,
                                             self._fire_linear_retarget)

    def _linear_retarget_allowed(self) -> bool:
        """停止中(=再生していない)かつ接続済みのときだけ追従させる。

        =80: TCode直結中も追従の対象(送信先の判断はplayer側)。
        """
        if not (self.intiface.connected or self.tcode.connected):
            return False
        return self.player.state.get("status") not in ("playing", "paused")

    def _fire_linear_retarget(self):
        self._retarget_job = None
        if not self._linear_retarget_allowed():
            return
        try:
            self.runner.submit(self.player.retarget_linear())
        except Exception:
            pass

    def _update_linear_bar_range(self):
        """LINEARバーの動作可能域を更新する。

        =86: 反転は区間の入れ替え(区間内の折り返し)なので、動作可能域は
        反転ON/OFFで変わらない(旧仕様では100-xミラーで域も移動していた)。
        """
        lo, hi = getattr(self, "_linear_zone", (0, 100))
        self.pos_bar.set_range(lo, hi)

    def _on_invert_change(self):
        self.intiface.invert = self.invert_var.get()
        # バーの動作可能域・現在位置表示もミラーする(実位置表示)
        self._update_linear_bar_range()

    # ---- TWIST(=79。ハンドラ群はLINEARと同型・レーンだけ独立) ----

    def _on_twist_range_change(self, rmin, rmax):
        """TWIST駆動区間スライダーの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_twist_range(rmin, rmax)
        self._apply(self.twist_range_label,
                    text=tr('区間補正{0}～{1}').format(rmin, rmax))
        self._twist_zone = (int(rmin), int(rmax))
        self._update_twist_bar_range()
        self._request_twist_retarget()

    def _request_twist_retarget(self):
        """TWIST区間変更をデバイスへ反映する(=71と同じ間引き)。"""
        if not self._linear_retarget_allowed():
            return
        if getattr(self, "_twist_retarget_job", None) is not None:
            return
        self._twist_retarget_job = self.root.after(
            self.RETARGET_MS, self._fire_twist_retarget)

    def _fire_twist_retarget(self):
        self._twist_retarget_job = None
        if not self._linear_retarget_allowed():
            return
        try:
            self.runner.submit(self.player.retarget_twist())
        except Exception:
            pass

    def _update_twist_bar_range(self):
        """TWISTバーの動作可能域を更新する(=86: 反転で域は変わらない)。"""
        lo, hi = getattr(self, "_twist_zone", (0, 100))
        self.twist_bar.set_range(lo, hi)

    def _twist_disp(self, v):
        """TWIST表示値: 反転ONなら区間内で折り返した実位置を表す(=86)。"""
        if not self.twist_invert_var.get():
            return v
        lo, hi = getattr(self, "_twist_zone", (0, 100))
        return lo + hi - v

    def _on_twist_invert_change(self):
        self.intiface.twist_invert = self.twist_invert_var.get()
        self._update_twist_bar_range()

    def _on_rotate_invert_change(self):
        self.intiface.rotate_invert = self.rotate_invert_var.get()

    def _build_offset_cell(self, parent, dtype: str) -> ctk.CTkFrame:
        """スライダー行の右側に置く「動作タイミング」入力セルを作る。"""
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(cell, text=tr("動作タイミング"),
                     font=ctk.CTkFont(size=11), text_color=LABEL,
                     ).pack(side="left", padx=(0, 4))
        var = tk.StringVar(value="0.0")
        self.offset_vars[dtype] = var
        entry = ctk.CTkEntry(cell, textvariable=var, width=52, height=26,
                             justify="right")
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e, d=dtype: self._commit_offset(d))
        entry.bind("<FocusOut>", lambda _e, d=dtype: self._commit_offset(d))
        btns = ctk.CTkFrame(cell, fg_color="transparent")
        btns.pack(side="left", padx=(2, 0))
        ctk.CTkButton(btns, text="▲", width=22, height=13,
                      font=ctk.CTkFont(size=8),
                      fg_color=("gray75", "gray30"),
                      hover_color=("gray70", "gray35"),
                      command=lambda d=dtype: self._step_offset(d, +0.1),
                      ).pack()
        ctk.CTkButton(btns, text="▼", width=22, height=13,
                      font=ctk.CTkFont(size=8),
                      fg_color=("gray75", "gray30"),
                      hover_color=("gray70", "gray35"),
                      command=lambda d=dtype: self._step_offset(d, -0.1),
                      ).pack(pady=(1, 0))
        return cell

    def _commit_offset(self, dtype: str):
        """入力値を検証・クランプしてプレーヤーへ反映する。"""
        raw = self.offset_vars[dtype].get().replace("＋", "+").replace("−", "-")
        try:
            v = float(raw)
        except ValueError:
            v = self.player.offsets_ms[dtype] / 1000.0
        v = max(-2.0, min(2.0, round(v * 10) / 10))
        self.offset_vars[dtype].set(f"{v:+.1f}" if v else "0.0")
        self.player.set_offset(dtype, v)

    def _step_offset(self, dtype: str, delta: float):
        try:
            v = float(self.offset_vars[dtype].get().replace("＋", "+")
                      .replace("−", "-"))
        except ValueError:
            v = self.player.offsets_ms[dtype] / 1000.0
        self.offset_vars[dtype].set(f"{v + delta:.1f}")
        self._commit_offset(dtype)

    # ================= トラックの並び替え・接続状況表示 =================

    # 同順位のときの固定順(トラック名の優先度)
    TRACK_ORDER = ("linear", "twist", "rotate", TRACK_ROTATE_A10, "vibration")
    TRACK_DISABLED_COLOR = "gray45"   # 未接続トラックの文字色

    def _grid_track_groups(self, order):
        """トラックのグループを指定順に配置し直す(各グループ3行)。

        =79: order に含まれないグループ(TWIST非表示時)は grid から外す。
        """
        for key, g in self.track_groups.items():
            if key in order:
                continue
            for w in ("head", "slider", "invert", "offset", "bar", "value"):
                if g[w] is not None:
                    g[w].grid_forget()
        # =79: TWIST表示時は5グループになり従来の行間でははみ出すため、
        # 5グループ以上のときだけ縦の余白を詰める(4グループ以下は従来どおり)。
        # =88: TWIST常時表示化で5グループが恒常になったため row_pad をさらに
        # 1px詰めた(2→1)。=79時点の実測でも5グループは7pxはみ出しており、
        # これで690x820に5グループが収まる(test_play_layoutで検証)。
        compact = len(order) >= 5
        head_pad = 5 if compact else 12
        row_pad = 2 if compact else 4
        for idx, key in enumerate(order):
            g = self.track_groups[key]
            base = idx * 3
            g["head"].grid(row=base, column=0, columnspan=3, sticky="ew",
                           pady=(head_pad if idx else 0, 0))
            g["slider"].grid(row=base + 1, column=0, sticky="ew",
                             pady=(row_pad, 0))
            if g["invert"] is not None:
                g["invert"].grid(row=base + 1, column=1, sticky="w",
                                 padx=(10, 0))
            g["offset"].grid(row=base + 1, column=2, sticky="w", padx=(10, 0))
            g["bar"].grid(row=base + 2, column=0, sticky="ew",
                          pady=(row_pad, 0))
            g["value"].grid(row=base + 2, column=1, sticky="w", padx=(10, 0))
        self._track_order = list(order)

    def _set_widget_tree_state(self, widget, state: str):
        for ch in widget.winfo_children():
            if isinstance(ch, (ctk.CTkEntry, ctk.CTkButton, ctk.CTkOptionMenu)):
                try:
                    ch.configure(state=state)
                except Exception:
                    pass
            self._set_widget_tree_state(ch, state)

    def _update_track_conn(self):
        """トラックの接続状況を反映する。

        並び順: 接続デバイスあり→なし、同順位は
        linear→rotate→rotate(a10cyclonesa)→vibration。
        未接続トラックは全体を灰色表示にし、操作も無効化する
        (デバイスへ機能することはないことを表す)。
        """
        iface = self.intiface
        tcode_on = self.tcode.connected   # =80: TCode直結はL0/R0=linear/twist
        conn = {"linear": iface.has_linear or tcode_on,
                "twist": bool(getattr(iface, "has_twist", False)) or tcode_on,
                "rotate": iface.has_rotate,
                TRACK_ROTATE_A10: iface.has_rotate_a10,
                "vibration": iface.has_vibrate}
        # =88: TWIST欄は常時表示(=79のサブ機能スイッチは廃止)
        shown = tuple(self.TRACK_ORDER)
        if conn == self._track_conn and shown == self._track_shown:
            return
        self._track_conn = conn
        self._track_shown = shown
        order = sorted(shown,
                       key=lambda k: (not conn[k],
                                      self.TRACK_ORDER.index(k)))
        self._grid_track_groups(order)
        for key, g in self.track_groups.items():
            on = conn[key]
            # 見出しの文字色は接続状況を表す(未接続=灰)。ただし操作は接続の
            # 有無に関わらず常に可能にする。デバイス未所持のユーザーでも、
            # 補正スライダー・反転・左右反転・動作タイミング・速度制限を触って
            # 動作イメージをプレビューできるようにするため。
            g["title"].configure(
                text_color=LABEL if on else self.TRACK_DISABLED_COLOR)
            g["head_value"].configure(
                text_color=ACCENT_TEXT if on else self.TRACK_DISABLED_COLOR)
            # 補正スライダーは接続状況を色で表す(未接続=灰色)。ただし操作は
            # 常に可能(未接続でも灰色のままドラッグ調整できる=プレビュー用)。
            g["slider"].set_enabled(on)
            # バーは常に動作を描く。色だけ接続状況で切替(未接続=灰系)。
            g["bar"].set_enabled(on)
            if g["invert"] is not None:
                g["invert"].configure(state="normal")
            self._set_widget_tree_state(g["offset"], "normal")
            self._set_widget_tree_state(g["head"], "normal")

    def _on_rotate_range_change(self, rmin, rmax):
        """回転強度レンジの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_rotate_range(rmin, rmax)
        self._apply(self.rotate_scale_label, text=tr('出力補正{0}～{1}%').format(rmin, rmax))
        self.rotate_bar.set_range(rmin, rmax)

    def _on_a10_range_change(self, rmin, rmax):
        """rotate(A10)強度レンジの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_rotate_a10_range(rmin, rmax)
        self._apply(self.a10_scale_label,
                    text=tr('出力補正{0}～{1}%').format(rmin, rmax))
        self.a10_bar.set_range(rmin, rmax)

    def _on_a10_invert_change(self):
        self.intiface.rotate_a10_invert = self.a10_invert_var.get()

    def _on_vibration_range_change(self, rmin, rmax):
        """振動強度レンジの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_vibration_range(rmin, rmax)
        self._apply(self.vibration_scale_label, text=tr('出力補正{0}～{1}%').format(rmin, rmax))
        self.vibration_bar.set_range(rmin, rmax)

    # ================= 状態ポーリング =================

    STATUS_TEXT = {
        "idle": (tr("待機中"), MUTED),
        "playing": (tr("再生中"), OK_TEXT),
        "paused": (tr("一時停止"), WARN_TEXT),
        "stopped": (tr("シナリオ終了"), MUTED),
        "finished": (tr("シナリオ終了"), MUTED),
        "error": (tr("エラー"), ERROR_TEXT),
    }

    def _apply(self, widget, **kwargs):
        """値が前回から変わった項目だけ configure する(チラつき防止)。

        CTkウィジェットは configure のたびに再描画されるため、
        毎ポーリングで無条件に呼ぶとボタン等が高速で明滅する。
        """
        if not hasattr(self, "_ui_cache"):
            self._ui_cache = {}
        wid = id(widget)
        cache = self._ui_cache.setdefault(wid, {})
        changed = {k: v for k, v in kwargs.items() if cache.get(k) != v}
        if changed:
            widget.configure(**changed)
            cache.update(changed)

    # ---------------- 選択肢UI ----------------

    CHOICE_COLS = {1: 1, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 7: 3, 8: 3, 9: 3}

    def _show_choice_card(self, labels: list[str]):
        for b in self.choice_buttons:
            b.destroy()
        self.choice_buttons = []
        # 見出し/グリッドを一旦外し、[チェック][見出し][グリッド]の順で確実に戻す
        self.choice_head.pack_forget()
        self.choice_grid.pack_forget()
        cols = self.CHOICE_COLS.get(len(labels), 3)
        # =163: **前回の列設定を必ず落としてから**新しい列を設定する。
        # grid の列設定はウィジェットに残り続けるので、一度3列にした
        # choice_grid は、次に2択を出しても「3列目(空)」が幅を取り続ける
        # =ボタンが画面の2/3で止まる。しかもカードは作り直さないので、
        # シナリオを開き直しても再起動まで直らなかった(ユーザー報告)。
        for c in range(max(self.CHOICE_COLS.values())):
            self.choice_grid.grid_columnconfigure(c, weight=0, uniform="")
        for c in range(cols):
            self.choice_grid.grid_columnconfigure(c, weight=1, uniform="choice")
        for i, label in enumerate(labels):
            btn = ctk.CTkButton(
                self.choice_grid, text=label, height=46,
                font=ctk.CTkFont(size=14),
                fg_color=("gray78", "gray28"), hover_color=ACCENT_HOVER,
                text_color=("gray12", "gray92"),
                command=lambda i=i: self._on_choice(i))
            btn.grid(row=i // cols, column=i % cols, sticky="ew",
                     padx=4, pady=4)
            self.choice_buttons.append(btn)
        self.choice_head.pack(fill="x", pady=(6, 0))
        self.choice_grid.pack(fill="x", pady=(8, 0))
        self._pack_play_overlay(self.choice_card)

    def _hide_choice_card(self):
        self.choice_card.pack_forget()
        for b in self.choice_buttons:
            b.destroy()
        self.choice_buttons = []

    def _show_choice_placeholder(self):
        """選択肢のあるシナリオで、選択肢が非アクティブな間の待機表示。

        =61: **カードは出さない**。選択肢の存在は操作バーの「自動選択
        (ランダム)」チェックが表示されていることで分かるため、待機中に
        カードで切替領域を削らない(以前は46px削っていて、②デバイス調整と
        ④ログの下端が常に切れていた=ユーザー報告)。
        """
        self._hide_choice_card()

    def _sync_autoselect_visible(self):
        """「自動選択(ランダム)」チェックの表示/非表示を更新する(=61)。

        選択肢のあるシナリオを読み込んでいる間だけ操作バーの右端に出す。
        """
        show = bool(self._scenario_has_choices)
        if show and not self.auto_select_check.winfo_manager():
            # before=transport: 先にパックしないと右端ではなく
            # 再生ボタン行の下になってしまう(操作バーが1行ぶん高くなる)
            self.auto_select_check.pack(side="right", padx=(8, 2),
                                        before=self.play_transport_row)
        elif not show and self.auto_select_check.winfo_manager():
            self.auto_select_check.pack_forget()

    def _on_autoselect_toggle(self):
        """自動選択チェックの切替。選択肢がアクティブなら即スケジュール/取消。"""
        if self.auto_select_var.get():
            if self._choice_sig is not None and self.choice_buttons:
                self._schedule_autoselect(self._choice_sig, len(self.choice_buttons))
        else:
            self._cancel_autoselect()

    def _schedule_autoselect(self, sig, n: int):
        """選択肢表示後0.8秒でランダム自動選択するタイマーを張る。

        0.8秒は、タイムリミットが1秒でも自動選択が勝つようにするため。
        """
        self._cancel_autoselect()
        if not self.auto_select_var.get() or n <= 0:
            return
        self._autoselect_sig = sig
        self._autoselect_after = self.root.after(800, self._do_autoselect)

    def _cancel_autoselect(self):
        if self._autoselect_after is not None:
            try:
                self.root.after_cancel(self._autoselect_after)
            except Exception:
                pass
            self._autoselect_after = None
        self._autoselect_sig = None

    def _do_autoselect(self):
        self._autoselect_after = None
        # スケジュール時と同じ選択肢がまだアクティブな時だけ選ぶ
        if self._choice_sig is None or self._choice_sig != self._autoselect_sig:
            return
        n = len(self.choice_buttons)
        if n <= 0:
            return
        import random
        self._on_choice(random.randrange(n))

    def _on_choice(self, index: int):
        self._cancel_autoselect()
        for b in self.choice_buttons:
            b.configure(state="disabled")
        self.runner.submit(self.player.choose(index))

    # ---------------- 数値入力UI ----------------

    @staticmethod
    def _fmt_num(v) -> str:
        return f"{v:g}"

    def _show_input_card(self, info: dict):
        """数値入力カードを表示する(入力要求ごとに初期化)。"""
        label = (info.get("label") or "").strip()
        self._apply(self.input_prompt_label,
                    text=label if label else tr("数値を入力してください"))
        mn, mx = info.get("min"), info.get("max")
        self._input_bounds = (mn, mx)
        if mn is not None and mx is not None:
            hint = tr("(入力範囲: {0}〜{1})").format(
                self._fmt_num(mn), self._fmt_num(mx))
        elif mn is not None:
            hint = tr("(入力範囲: {0}以上)").format(self._fmt_num(mn))
        elif mx is not None:
            hint = tr("(入力範囲: {0}以下)").format(self._fmt_num(mx))
        else:
            hint = ""
        self._apply(self.input_range_label, text=hint)
        self.input_var.set("")
        self._apply(self.input_error_label, text="")
        self.input_entry.configure(state="normal")
        self.input_submit_btn.configure(state="normal")
        self._pack_play_overlay(self.input_card)
        self.input_entry.focus_set()

    def _hide_input_card(self):
        self.input_card.pack_forget()

    def _on_input_submit(self):
        """決定ボタン(またはEnter)。数値検証してplayerへサブミットする。

        非数値・範囲外は赤いエラー表示で再入力を求める(カードは閉じない)。
        """
        if self._input_sig is None:
            return
        text = self.input_var.get().strip()
        try:
            value = float(text)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError
        except ValueError:
            self._apply(self.input_error_label, text=tr("数値を入力してください"))
            return
        mn, mx = self._input_bounds
        if (mn is not None and value < mn) or (mx is not None and value > mx):
            if mn is not None and mx is not None:
                msg = tr("{0}〜{1}の数値を入力してください").format(
                    self._fmt_num(mn), self._fmt_num(mx))
            elif mn is not None:
                msg = tr("{0}以上の数値を入力してください").format(self._fmt_num(mn))
            else:
                msg = tr("{0}以下の数値を入力してください").format(self._fmt_num(mx))
            self._apply(self.input_error_label, text=msg)
            return
        self._apply(self.input_error_label, text="")
        self.input_entry.configure(state="disabled")
        self.input_submit_btn.configure(state="disabled")
        event_id = self._input_sig[0]
        self.runner.submit(self.player.submit_input(value, event_id))

    def _linear_disp(self, v):
        """LINEAR表示値: 反転ONなら区間内で折り返した実位置を表す(=86)。"""
        if not self.invert_var.get():
            return v
        lo, hi = getattr(self, "_linear_zone", (0, 100))
        return lo + hi - v

    def _update_seek_slider(self, elapsed: float, duration: float):
        """シークバーの位置を反映する(ドラッグ中はユーザー操作を優先)。

        =286: 値は 1000分率の小数(number_of_steps=1000 で丸まる)。
        _poll_state(100ms)と _animate_linear(設定fps)の両方から呼ばれる。
        """
        if self._seek_dragging:
            return
        value = (elapsed / duration * 1000.0) if duration > 0 else 0.0
        value = max(0.0, min(1000.0, value))
        value = round(value, 1)
        if getattr(self, "_last_seek_value", None) != value:
            self.seek_slider.set(value)
            self._last_seek_value = value

    def _animate_linear(self):
        """LINEAR/TWISTバーを補間更新し、他の駆動値バーも同じ頻度で更新する。

        playerが記録した進行中の移動(from→to, 所要dur秒)を単純線形補間で描く。
        一時停止中もデバイスは送信済みコマンドを完遂するため、補間は継続して
        to位置で自然に停止する。反転ON時はミラー表示(実位置)になる。
        =106: 更新間隔は設定「描画更新頻度」(graph_fps。既定60fps)に従い、
        ROTATE/VIBRATIONバー(_update_motion_bars)もここから同頻度で更新する
        (従来は_poll_stateの100ms)。バー/ラベルは値が変わらない限り
        再描画しない(ZoneBar.set_value/_applyの差分ガード)ため、
        アイドル時の60fpsは実質無負荷。
        """
        st = self.player.state
        mv = st.get("linear_move")
        if mv and st["status"] in ("playing", "paused"):
            frac = (time.monotonic() - mv["t0"]) / max(mv["dur"], 0.001)
            frac = max(0.0, min(1.0, frac))
            cur = mv["from"] + (mv["to"] - mv["from"]) * frac
            self.pos_bar.set_value(round(self._linear_disp(cur), 1))
        else:
            self.pos_bar.set_value(self._linear_disp(st.get("linear_pos", 0)))
        # =79 TWISTバーも同じ補間で更新
        tmv = st.get("twist_move")
        if tmv and st["status"] in ("playing", "paused"):
            frac = (time.monotonic() - tmv["t0"]) / max(tmv["dur"], 0.001)
            frac = max(0.0, min(1.0, frac))
            cur = tmv["from"] + (tmv["to"] - tmv["from"]) * frac
            self.twist_bar.set_value(round(self._twist_disp(cur), 1))
        else:
            self.twist_bar.set_value(self._twist_disp(st.get("twist_pos", 0)))
        # =106: ROTATE(ufo/a10)・VIBRATIONのバーも同じ頻度で更新
        self._update_motion_bars()
        # =286: シークバーも同じ頻度で滑らかに動かす(ユーザー報告: 100ms刻み
        # だと丸がカクつく)。player の時計から直接読む(live_elapsed_ms)
        if st["status"] == "playing" and st["duration_ms"] > 0:
            self._update_seek_slider(self.player.live_elapsed_ms(),
                                     st["duration_ms"])
        self.root.after(self._graph_interval_ms, self._animate_linear)

    def _update_motion_bars(self):
        """ROTATE(ufo/a10)・VIBRATIONの駆動値バーと数値ラベルを更新する。

        =106で_poll_state(100ms)から切り出し、_animate_linear(設定fps)から
        呼ぶ。_poll_stateからも従来どおり呼ばれる(表示構造の変化に対する
        保険+既存テストの作法「state書き換え+_poll_state()」の維持)。
        """
        st = self.player.state

        def show_rotate(pos, speed, invert, bar, label, connected):
            """rotate系の表示。反転ON時は方向(緑⇔ピンク・符号)を入れ替える。"""
            gray = self.TRACK_DISABLED_COLOR
            if pos == 50 or speed <= 0:
                bar.set_value(None)
                self._apply(label, text=tr("停止"),
                            text_color=LABEL if connected else gray)
                return
            positive = (pos > 50)
            if invert:
                positive = not positive   # 反転=回転方向が入れ替わる(大きさ維持)
            bar.set_value(speed)
            pct = int(round(speed * 100))
            if positive:
                self._apply(label, text=f"{pct}",
                            text_color=OK_TEXT if connected else gray)
            else:
                self._apply(label, text=f"-{pct}",
                            text_color=NEG_TEXT if connected else gray)

        # rotate(ufo)表示: ufotwが居れば上下分割(左/右)、居なければ単一バー
        r_on = self._track_conn.get("rotate", False)
        if self._lane_is_split("ufo"):
            self._show_rotate_split("ufo", "rotate_ufo_ch",
                                    self.rotate_bar, self.rotate_value_label,
                                    r_on)
        else:
            rpos = st.get("rotate_pos", 50)
            speed, _cw = self.intiface.map_rotate_speed(rpos)
            show_rotate(rpos, speed, self.rotate_invert_var.get(),
                        self.rotate_bar, self.rotate_value_label, r_on)

        # rotate(a10cyclonesa)表示: 同様(ufotwを付け替えた場合も分割対応)
        a_on = self._track_conn.get(TRACK_ROTATE_A10, False)
        if self._lane_is_split("a10"):
            self._show_rotate_split("a10", "rotate_a10_ch",
                                    self.a10_bar, self.a10_value_label, a_on)
        else:
            apos = st.get("rotate_a10_pos", 50)
            aspeed, _acw = self.intiface.map_rotate_a10_speed(apos)
            show_rotate(apos, aspeed, self.a10_invert_var.get(),
                        self.a10_bar, self.a10_value_label, a_on)

        # vibration表示: バー + 実出力強度(強度レンジ適用後)の数値
        vib_on = self._track_conn.get("vibration", False)
        vpos = st.get("vibration_pos", 0)
        vspeed = self.intiface.map_vibration_speed(vpos)
        if vpos <= 0 or vspeed <= 0:
            self.vibration_bar.set_value(None)
            self._apply(self.vibration_value_label, text=tr("停止"),
                        text_color=LABEL if vib_on
                        else self.TRACK_DISABLED_COLOR)
        else:
            self.vibration_bar.set_value(vspeed)
            self._apply(self.vibration_value_label,
                        text=f"{int(round(vspeed * 100))}",
                        text_color=OK_TEXT if vib_on
                        else self.TRACK_DISABLED_COLOR)

    def _poll_state(self):
        # 接続ロストの検知と自動再接続(音声再生には影響しない)
        self._check_auto_connect()
        # =272: 接続状況(Intiface/COM/デバイス増減)が変わったら
        # 「接続中デバイス」欄を描き直す(変化がなければ何もしない)
        self._refresh_device_box()

        st = self.player.state
        status = st["status"]

        text, color = self.STATUS_TEXT.get(status, (status, MUTED))
        self._apply(self.status_label, text=text, text_color=color)

        # 再生中のチャンネル表示(=68でチャンネルごとの行に変更)。
        # 動画ch(=52)は「▶動画」、シークバーが追従しているchは「★」を付ける。
        ch_audio = st.get("channel_audio", {})
        vch = st.get("video_channel") or ""
        dev_ch = st.get("seek_channel") or st.get("device_channel", "")
        for ch_id in ("L", "C", "R"):
            raw = ch_audio.get(ch_id) or ""
            if not ch_audio and ch_id == dev_ch and st.get("audio_file"):
                # 旧経路の保険(ch別が1つも無いときだけ代表を出す)。=286: ch別が
                # あるときは終わったchを「—」に戻したいので代表で埋めない
                raw = st["audio_file"]
            marks = ("★" if raw and ch_id == dev_ch else "") + \
                    (tr("▶動画") if raw and ch_id == vch else "")
            self._apply(self.ch_mark_labels[ch_id], text=marks)
            self._apply(self.ch_name_labels[ch_id],
                        text=os.path.basename(raw) if raw else "—",
                        text_color=LABEL if raw else MUTED)
        def fmt_mmss(ms):
            s = ms / 1000
            return f"{int(s // 60):02d}:{int(s % 60):02d}"

        ev_id = st['event_id'] or '-'
        ev_text = tr('イベント: {0}').format(ev_id)
        if st['event_id']:
            ev_text += tr('  ／  経過 {0}').format(fmt_mmss(st['event_elapsed_ms']))
        self._apply(self.event_label, text=ev_text)

        if st.get("state_id"):
            state_text = (tr('ステート: {0}  ／  経過 {1}').format(st['state_id'], fmt_mmss(st.get('state_elapsed_ms', 0))))
        else:
            state_text = tr("ステート: -")
        self._apply(self.state_label, text=state_text)

        # (=289: ①の変数行は廃止。変数は③のページで表示する)

        def fmt(ms):
            s = ms / 1000
            return f"{int(s // 60):02d}:{s % 60:04.1f}"

        elapsed = st["elapsed_ms"]
        duration = st["duration_ms"]
        self._apply(self.time_label, text=f"{fmt(elapsed)} / {fmt(duration)}")

        # シークバー(ドラッグ中はユーザー操作を優先して上書きしない)
        seekable = status in ("playing", "paused") and duration > 0
        self._apply(self.seek_slider, state="normal" if seekable else "disabled")
        # 10秒送り/戻しボタンはシーク可能なときだけ有効
        seek_state = "normal" if seekable else "disabled"
        self._apply(self.btn_seek_back, state=seek_state)
        self._apply(self.btn_seek_fwd, state=seek_state)
        self._update_seek_slider(elapsed, duration)

        # linear: バーの塗りは_animate_linear(設定fps=106)が補間更新する。
        # ラベルは補正後の指示値「from→to」を緑で表示(停止中は非表示)
        # トラックの接続状況(並び替え・灰色表示)を反映
        self._update_track_conn()
        # 回転デバイスの増減を割り当てUIへ反映(集合が変わった時だけ再構築)
        self._refresh_rotate_assign_ui()
        lin_on = self._track_conn.get("linear", False)

        mv = st.get("linear_move")
        if mv and status in ("playing", "paused"):
            # 反転ON時はミラー表示(デバイス実位置)
            d_from = int(round(self._linear_disp(mv['from'])))
            d_to = int(round(self._linear_disp(mv['to'])))
            self._apply(self.pos_value_label,
                        text=f"{d_from}→{d_to}",
                        text_color=OK_TEXT if lin_on
                        else self.TRACK_DISABLED_COLOR)
        else:
            self._apply(self.pos_value_label, text="")
        # =79 TWIST: バーは_animate_linear(設定fps)が補間更新。ラベルはlinearと同型
        tmv = st.get("twist_move")
        if tmv and status in ("playing", "paused"):
            t_on = self._track_conn.get("twist", False)
            d_from = int(round(self._twist_disp(tmv['from'])))
            d_to = int(round(self._twist_disp(tmv['to'])))
            self._apply(self.twist_value_label,
                        text=f"{d_from}→{d_to}",
                        text_color=OK_TEXT if t_on
                        else self.TRACK_DISABLED_COLOR)
        else:
            self._apply(self.twist_value_label, text="")
        self._apply(self.msg_label, text=st["message"])

        # 選択肢カード
        ch = st.get("choice")
        if ch and status in ("playing", "paused"):
            sig = (ch.get("event_id"), tuple(ch.get("labels") or ()))
            if sig != self._choice_sig:
                self._choice_sig = sig
                self._choice_display = "active"
                self._show_choice_card(list(sig[1]))
                self._schedule_autoselect(sig, len(sig[1]))
            rem = st.get("choice_remaining_ms")
            if rem is not None:
                s = max(0, rem) / 1000
                self._apply(self.choice_timer_label,
                            text=tr("残り {0}:{1:02d}").format(int(s // 60), int(s % 60)))
            else:
                self._apply(self.choice_timer_label, text="")
        else:
            # 選択肢が非アクティブ: 選択肢ありシナリオなら■■■を常時表示、無ければ隠す
            if self._choice_sig is not None:
                self._choice_sig = None
                self._cancel_autoselect()
            want = "placeholder" if self._scenario_has_choices else "hidden"
            if self._choice_display != want:
                self._choice_display = want
                if want == "placeholder":
                    self._show_choice_placeholder()
                else:
                    self._hide_choice_card()

        # 数値入力カード
        inp = st.get("input")
        if inp and status in ("playing", "paused"):
            sig = (inp.get("event_id"), inp.get("var"))
            if sig != self._input_sig:
                self._input_sig = sig
                self._show_input_card(inp)
        elif self._input_sig is not None:
            self._input_sig = None
            self._hide_input_card()

        # 動画イベント中に選択肢/入力カードが出たらRVPを最前面へ出す
        # (フルスクリーン動画の上でも操作できるように。解決したら解除する。
        #  フェーズ2=ユーザー決定。動画のないイベントでは何もしない)
        front = (bool(st.get("video_file"))
                 and status in ("playing", "paused")
                 and (self._choice_sig is not None
                      or self._input_sig is not None))
        if front != self._video_front:
            self._video_front = front
            try:
                self.root.attributes("-topmost", front)
                if front:
                    self.root.lift()
            except Exception:
                pass

        # ufotwが割り当たったレーンは左右反転チェックの表示を更新する
        self._update_swap_visibility()

        # =106: 駆動値バー本体の更新は _update_motion_bars へ切り出し、
        # _animate_linear(設定fps)からも呼ばれる。ここでも呼ぶのは
        # 表示構造の変化(接続・分割)への保険+既存テストの作法の維持。
        self._update_motion_bars()

        active = status in ("playing", "paused")
        if status == "playing":
            self._apply(self.btn_play, text="❚❚", state="normal")
        elif status == "paused":
            self._apply(self.btn_play, text="▶", state="normal")
        else:
            self._apply(self.btn_play, text="▶",
                        state="normal" if self.scenario else "disabled")
        self._apply(self.btn_skip, state="normal" if active else "disabled")
        self._apply(self.btn_back, state="normal" if active else "disabled")

        # ②イベント状態ビュー/③変数・イベントログ(表示中のみ、差分更新)
        self._update_event_map()
        self._update_run_log()

        self.root.after(100, self._poll_state)


def _install_ctk_reentrancy_guard():
    """=83/=84: customtkinter 6.0.0 の同期再帰バグを遮断する(main()起動時のみ)。

    =84(真因・実機クラッシュダンプで確定): **CTkScrollbar._draw が
    update_idletasks() を呼ぶ**(ctk_scrollbar.py:168)ため、
    canvasのスクロール連携 → set → _draw → update_idletasks →
    (Tclが保留中のスクロール更新を同期処理) → また set → … の
    **真の無限再帰**になる。履歴リスト(CTkScrollableFrame)の行数が
    一定以上でスクロール領域の再計算が連鎖する条件を踏むと発症し、
    起動不能に至っていた(6件○/7件✕の境界はこの発症条件)。
    再帰は必ず set を通るので、set の再入を1段で打ち切れば完全に切れる。
    再入時は値の更新だけ行い描画しない(1フレームの表示遅れのみ・
    次のスクロールイベントで追いつく)。

    =83(保険として維持): _update_dimensions_event の同一ウィジェット
    再入ガード。

    =95(起動14秒問題の真因対応): =84のsetガードは**クラッシュ(無限再帰)**
    は止めたが、_draw が呼ぶ update_idletasks() 自体は残っていた。この呼び出しは
    「スクロールバーを1回描くたびに、溜まっている**全ウィジェットの保留描画を
    同期処理する**」ため、起動直後やシナリオ読込直後(保留描画が数百件)には
    別インスタンスのスクロールバー同士で入れ子になり、**メインスレッドが
    7〜8秒完全停止**していた(=94の実機スタックサンプルで確定。
    mainloop→set→_draw→update_idletasks→set→_draw→update_idletasks→…が
    そのまま写っている)。対策=**_draw の実行中だけ canvas.update_idletasks を
    無効化**する。効果は「スクロールバーの描画が次の自然なidle処理まで
    最大1フレーム遅れる」だけで、上流バグの副作用(全保留描画の強制処理)を
    根本から断つ。
    """
    try:
        from customtkinter.windows.widgets.ctk_scrollbar import CTkScrollbar
        orig_set = CTkScrollbar.set

        def guarded_set(self, start_value, end_value):
            if getattr(self, "_rvp_in_set", False):
                # 再入(=_draw内のupdate_idletasksから同期的に呼び戻された):
                # 値だけ最新化して描画は外側に任せ、再帰を断つ
                self._start_value = float(start_value)
                self._end_value = float(end_value)
                return
            self._rvp_in_set = True
            try:
                return orig_set(self, start_value, end_value)
            finally:
                self._rvp_in_set = False

        CTkScrollbar.set = guarded_set

        # =95: _draw 実行中は canvas.update_idletasks を無効化する
        orig_draw = CTkScrollbar._draw

        def _noop_update_idletasks():
            pass

        def draw_without_idletasks(self, *args, **kwargs):
            canvas = getattr(self, "_canvas", None)
            if canvas is None:
                return orig_draw(self, *args, **kwargs)
            # インスタンス属性でクラスメソッドを一時的に遮蔽する
            canvas.update_idletasks = _noop_update_idletasks
            try:
                return orig_draw(self, *args, **kwargs)
            finally:
                try:
                    del canvas.update_idletasks   # クラスの実装へ戻す
                except AttributeError:
                    pass

        CTkScrollbar._draw = draw_without_idletasks
    except Exception:
        pass
    try:
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class \
            import CTkBaseClass
        orig = CTkBaseClass._update_dimensions_event

        class _Dims:
            """保留分の流し直し用。実サイズ(winfo)をそのまま渡す。"""

            __slots__ = ("width", "height")

            def __init__(self, w, h):
                self.width, self.height = w, h

        def guarded(self, event):
            if getattr(self, "_rvp_in_dims", False):
                # =108: 再入を**捨てる**と、そのウィジェットは古いサイズを
                # 覚えたまま(_current_width/_current_height)になり、canvas は
                # 旧サイズで描かれ続ける=コンボの角丸や▼が途中で切れた
                # ような崩れになる(Windows実機で報告)。あとで流し直す。
                self._rvp_dims_pending = True
                return
            self._rvp_in_dims = True
            try:
                r = orig(self, event)
                # 保留があれば「そのときの実サイズ」で1回流し直す。古い
                # イベントを再生すると順序次第で逆戻りするので、必ず winfo
                # の現物を使う(連鎖しても3回で打ち切り=無限再帰の遮断と
                # いう本来の目的は維持する)。
                for _ in range(3):
                    if not getattr(self, "_rvp_dims_pending", False):
                        break
                    self._rvp_dims_pending = False
                    try:
                        dims = _Dims(self.winfo_width(), self.winfo_height())
                    except Exception:
                        break
                    r = orig(self, dims)
                return r
            finally:
                self._rvp_in_dims = False
                self._rvp_dims_pending = False

        CTkBaseClass._update_dimensions_event = guarded
    except Exception:
        pass


def _install_crash_diagnostics(root):
    """=83: Tkコールバック例外の初回に全スタックを rvp_crashdump.txt へ記録。

    RecursionError の嵐では標準の例外表示が浅いトレースしか出せない
    (深い部分は**呼び出し側**のスタックにあり、表示自体も失敗する)。
    report_callback_exception は最深部で呼ばれるので、その時点の
    スレッド全体のスタックを faulthandler(C実装=再帰制限の影響を
    受けない)でダンプすれば、繰り返しているフレーム=真犯人が分かる。
    2回目以降の例外表示は5回で打ち切り、コンソールの嵐も止める。
    """
    import faulthandler
    state = {"n": 0}

    def report(exc, val, tb):
        state["n"] += 1
        if state["n"] == 1:
            try:
                with open("rvp_crashdump.txt", "w", encoding="utf-8") as fp:
                    fp.write("RVP crash diagnostics (=83)\n")
                    fp.write(f"rvp_version={__version__}\n")   # =292
                    fp.write(f"python={sys.version}\n")
                    fp.write(f"frozen={bool(getattr(sys, 'frozen', False))}\n")
                    try:
                        fp.write(f"customtkinter={ctk.__version__}\n")
                        from customtkinter import ScalingTracker
                        fp.write("widget_scaling="
                                 f"{ScalingTracker.get_widget_scaling(root)}\n")
                        fp.write("window_scaling="
                                 f"{ScalingTracker.get_window_scaling(root)}\n")
                    except Exception as e:
                        fp.write(f"ctk info error: {e}\n")
                    try:
                        fp.write(f"tk scaling={root.tk.call('tk', 'scaling')}\n")
                    except Exception:
                        pass
                    fp.write(f"exception={getattr(exc, '__name__', exc)}: "
                             f"{val}\n")
                    fp.write("\n== この瞬間のスレッド全スタック"
                             "(faulthandler・最深部から) ==\n")
                    fp.flush()
                    faulthandler.dump_traceback(file=fp, all_threads=False)
                    fp.write("\n== 例外のトレースバック(浅い側) ==\n")
                    t = tb
                    while t is not None:
                        f = t.tb_frame
                        fp.write(f"{f.f_code.co_filename}:{t.tb_lineno}:"
                                 f"{f.f_code.co_name}\n")
                        t = t.tb_next
                print("rvp_crashdump.txt へ診断情報を記録しました")
            except Exception:
                pass
        if state["n"] <= 5:
            try:
                import traceback
                traceback.print_exception(exc, val, tb)
            except Exception:
                print("Exception in Tkinter callback (表示失敗)")
        elif state["n"] == 6:
            print("(以降のコールバック例外の表示は省略します。"
                  "rvp_crashdump.txt を参照)")

    root.report_callback_exception = report


# =144 タスクバーアイコン対策。Windowsのタスクバーはウィンドウを
# AppUserModelID 単位でグループ化してアイコンを決めるため、python.exe から
# 起動するとタスクバーには Python のアイコンが出る(タイトルバーは iconbitmap
# が効く)。独自IDを宣言するとウィンドウ自身のアイコン(=143)が使われる。
# exe化(PyInstaller)後も無害。
APP_USER_MODEL_ID = "RVP.RVP"


def _set_win_app_id():
    """main() 起動時のみ・ウィンドウ生成より前に呼ぶ。戻り値=適用できたか。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID)
        return True
    except Exception:
        return False


# =149 一度LoadImageしたHICONの使い回しキャッシュ(プロセス寿命・破棄しない)。
# {"done": True, "big": HICON|None, "small": HICON|None}
_WIN_ICON_CACHE = {}


def _win_user32():
    """user32を取得し、64bitでのHICON/HWND切り詰め防止の型宣言を試みる
    (テストのスタブ相手では失敗してよい)。"""
    import ctypes
    user32 = ctypes.windll.user32
    try:
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_void_p]
    except Exception:
        pass
    return user32


def _win_icon_handles():
    """(big, small) の HICON。初回だけ LoadImage し以後はキャッシュを返す。
    サイズはシステムメトリクス(DPIスケーリング反映済み)に合わせる。"""
    if _WIN_ICON_CACHE.get("done"):
        return (_WIN_ICON_CACHE.get("big"), _WIN_ICON_CACHE.get("small"))
    _WIN_ICON_CACHE["done"] = True
    ico = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "assets", "rvp.ico")
    if not os.path.isfile(ico):
        return (None, None)
    try:
        user32 = _win_user32()
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        # (キー, metric, フォールバック): SM_CXICON=11 / SM_CXSMICON=49
        for key, metric, fb in (("big", 11, 32), ("small", 49, 16)):
            cx = user32.GetSystemMetrics(metric) or fb
            h = user32.LoadImageW(None, ico, IMAGE_ICON, cx, cx,
                                  LR_LOADFROMFILE)
            _WIN_ICON_CACHE[key] = h or None
    except Exception:
        pass
    return (_WIN_ICON_CACHE.get("big"), _WIN_ICON_CACHE.get("small"))


def _apply_win_icons(widget):
    """=147/=149 対象ウィンドウ(root/Toplevel)へ WM_SETICON で高精細アイコンを
    直接セットする。戻り値=適用できたか。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        big, small = _win_icon_handles()
        if not (big or small):
            return False
        user32 = _win_user32()
        widget.update_idletasks()
        hwnd = user32.GetAncestor(widget.winfo_id(), 2)  # 2=GA_ROOT
        if not hwnd:
            return False
        WM_SETICON = 0x0080
        applied = False
        for which, h in ((1, big), (0, small)):  # 1=ICON_BIG / 0=ICON_SMALL
            if h:
                user32.SendMessageW(hwnd, WM_SETICON, which, h)
                applied = True
        return applied
    except Exception:
        return False


def _set_win_taskbar_icons(root):
    """=147 タスクバー/タイトルバーのぼやけ対策。実行中のタスクバーボタンや
    タイトルバーは「ウィンドウのアイコン」を使うが、Tk の iconbitmap はそれを
    固定の小サイズで生成するため拡大時に滲む。root へ高精細アイコンを直接
    セットし、=149: 以後開く**すべての Toplevel(編集画面・ヘルプ・設定等)にも
    <Map> フックで自動適用**する(適用済みは _rvp_win_icons 印で二重適用防止)。
    main() 起動時のみ・_set_app_icon() の後に呼ぶ。戻り値=rootへ適用できたか。
    HICON はプロセス寿命まで使うので破棄しない。"""
    if not sys.platform.startswith("win"):
        return False
    applied = _apply_win_icons(root)

    def _on_map(event):
        w = getattr(event, "widget", None)
        if isinstance(w, tk.Toplevel) and not getattr(w, "_rvp_win_icons",
                                                      False):
            w._rvp_win_icons = True
            try:
                _apply_win_icons(w)
            except Exception:
                pass

    try:
        root.bind_all("<Map>", _on_map, add="+")
    except Exception:
        pass
    return applied


def _set_app_icon(root):
    """=143 アプリアイコン(rvp/assets/)。main() 起動時のみ呼ぶ
    (winstate / appfont / apptheme と同じ「テストを汚染しない」流儀)。

    - Windows: iconbitmap(default=rvp.ico) で全 Toplevel(編集画面・ヘルプ等)へ
      既定として波及させる。CustomTkinter は CTk/CTkToplevel の生成200ms後に
      自前のCTkロゴを iconbitmap で押し付ける(_windows_set_titlebar_icon)ため、
      CTkToplevel 側のそれを無効化して default= の継承を守る
      (root側は iconbitmap 呼び出しで _iconbitmap_method_called が立ち抑止される)。
    - その他OS: iconphoto(True, rvp.png)。True=以後の Toplevel の既定にもなる。
      PhotoImage はGCされると消えるので root へ参照を残す。
    失敗してもアイコンが出ないだけなので起動は続行する。戻り値=適用できたか。
    """
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    try:
        if sys.platform.startswith("win"):
            ico = os.path.join(base, "rvp.ico")
            if os.path.isfile(ico):
                root.iconbitmap(default=ico)
                try:
                    ctk.CTkToplevel._windows_set_titlebar_icon = (
                        lambda self: None)
                except Exception:
                    pass
                return True
        png = os.path.join(base, "rvp.png")
        if os.path.isfile(png):
            img = tk.PhotoImage(file=png)
            root.iconphoto(True, img)
            root._rvp_icon_img = img  # 参照保持(GC防止)
            return True
    except Exception:
        pass
    return False


# =134: 色定数・クラス属性(DeviceGraph.C_BG等)のテーマ差し替え登録。
# クラス定義より後(=モジュール末尾)で呼ぶ。
apptheme.register(globals())


def main():
    _startup_mark("main() 開始")
    _install_ctk_reentrancy_guard()       # =83 起動不能対策(CTk同期入れ子の遮断)
    _set_win_app_id()                     # =144 タスクバーアイコン(窓生成より前)
    winstate.enable()                     # =115 ウィンドウ位置の記憶(実行時のみ)
    ctk.set_appearance_mode("dark")       # "dark" / "light" / "system"
    ctk.set_default_color_theme("blue")
    # =134: パステルカラーテーマ。CTkの色はウィジェット生成時に確定する
    # ので、rootを含む全ウィジェットより前に1回だけ適用する(ライト基調)。
    # テストは main() を通らないので汚染しない(appfont/winstateと同じ流儀)。
    _theme = load_config().get("appearance")
    if isinstance(_theme, str) and _theme in apptheme.THEMES:
        ctk.set_appearance_mode("light")
        apptheme.set_active(_theme)
    _startup_mark("カラーテーマ適用")

    root = ctk.CTk()
    _startup_mark("Tkルートウィンドウ生成")
    # =119: UIフォントの適用。CTkFontはウィジェット生成時にfamilyを確定する
    # ので、RVPApp(=全ウィジェット)より前に1回だけ行う。端末に無ければ
    # 何もしない=システム標準のまま。テストはmain()を通らないので汚染しない
    # (winstate / CTk再入ガードと同じ流儀)。
    _fam = appfont.configured_family(load_config())   # =293 初期値 BIZ UDPGothic
    if _fam:
        appfont.apply(root, _fam)
    _startup_mark("フォント適用")
    _set_app_icon(root)                   # =143 アプリアイコン(main()時のみ)
    _set_win_taskbar_icons(root)          # =147 タスクバー用の高精細アイコン
    _startup_mark("アイコン適用")
    _install_crash_diagnostics(root)      # =83 万一の際は rvp_crashdump.txt へ記録
    app = RVPApp(root)

    # =92: ウィンドウが実際に画面へ出た瞬間を記録(<Map>は最初の表示で発火)
    def _on_first_map(_e=None):
        root.unbind("<Map>", map_bind)
        _startup_mark("★ウィンドウ表示(<Map>)")
    map_bind = root.bind("<Map>", _on_first_map, add="+")
    if _startup_log_wanted():
        # =93: ハートビート。250ms毎のタイマーが「いつ実際に発火したか」を
        # 記録する。イベントループ(メインスレッド)がブロックされている間は
        # タイマーが発火できないので、**ログ上のハートビートの間隔が開いて
        # いる場所=ループが止まっていた区間**として犯人の時間帯を特定できる
        # (v92のログで<Map>→自動オープンの間に約7.7秒の空白があり、その
        # 内側を見るための増設)。
        def _heartbeat():
            _startup_mark("♥heartbeat")
            if time.perf_counter() - _STARTUP_T0 < 12.0:
                root.after(250, _heartbeat)
        root.after(250, _heartbeat)
        _start_stack_sampler()   # =94: ブロック中のスタックを2.5秒毎に採取
        # 自動オープンや履歴の遅延構築、ハートビート全部を含めてから書き出す
        # (ブロックで遅れて発火してもよい=遅れた分のスタックも採れる)
        root.after(13000, _write_startup_log)

    def on_close():
        try:
            app.save_app_config()
        except Exception:
            pass
        try:
            app.winmem.save_now()      # =115: 終了時のサイズ・位置を記憶
        except Exception:
            pass
        try:
            app.runner.submit(app.player.stop())
            # mpvはRVP終了時のみ閉じる(合意事項)
            app.runner.submit(app.player.shutdown_mpv())
            app.runner.submit(app.intiface.disconnect())
            app.tcode.disconnect()   # =80 シリアルポートを閉じる
        except Exception:
            pass
        root.after(300, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
