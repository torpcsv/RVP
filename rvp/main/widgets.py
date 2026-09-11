"""メイン画面の小部品: FixedBtn / TabView / ZoneBar / RangeSlider。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from .. import apptheme

from .common import (MUTED, OK_COLOR, TAB_HEIGHT, TAB_HOVER_COLOR,
    TAB_IDLE_COLOR, TAB_OVERLAP, TAB_WIDTH, logger)
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


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
        self.panel = ctk.CTkFrame(self, corner_radius=14, fg_color=_clr.PANEL_COLOR)
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
                    fg_color=_clr.PANEL_COLOR, hover_color=_clr.PANEL_COLOR,
                    text_color=_clr.ACCENT_TEXT,
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
        self.fill_color = _clr.ACCENT if mode == "linear" else OK_COLOR
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
    RANGE_COLOR = _clr.ACCENT
    HANDLE_COLOR = _clr.ACCENT
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


apptheme.register(globals())   # テーマ追従する色定数・クラス属性を登録(apptheme)
