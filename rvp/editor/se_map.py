"""シナリオ編集: イベント遷移図・ステート図(描画・折りたたみ・高さ・ドッキング解除・手動配置・ノード着色)(mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from ..scenario_map import CANVAS_BG
from ..winstate import WindowMemory
from .. import appfont, scenario_map as _smap, winstate
from ..i18n import load_config, save_config, tr

from .common import MUTED, NODE_PALETTE, TEXT_MUTED, _canvas_bg, _toolbar_sep
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _ScenarioEditorMapMixin:
    """ScenarioEditor の mixin(=301 分割)。イベント遷移図・ステート図(描画・折りたたみ・高さ・ドッキング解除・手動配置・ノード着色)"""

    # ---- =300: 2段目バー+イベント遷移図(ドッキング/別ウィンドウ共用) ----
    #
    # Tk はウィジェットの親を付け替えられないので、ドッキング解除/ドッキングの
    # たびに**バーと図を作り直す**。self.undo_btn / map_toggle_btn / map_fit_btn /
    # map_mode_btn / canvas / canvas_wrap / map_sash などの参照は現在の側を
    # 指すよう付け替える(既存ロジックは self.canvas 経由なのでそのまま動く)。
    # 履歴(=277)は編集画面に1本なので、どちらのウィンドウで Ctrl+Z しても
    # 同じ順で戻る。
    def _build_map_area(self, parent, docked: bool):
        before = {}
        if docked and getattr(self, "panel_wrap", None) is not None:
            before = {"before": self.panel_wrap}   # 再ドッキング時の差し込み位置
        # 2段目: イベント操作+インポート+図の折りたたみ(=255でmap_bar統合)
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        if docked:
            bar.pack(fill="x", padx=14, pady=(0, 0), **before)
        else:
            bar.pack(fill="x", padx=8, pady=(6, 0))
        ctk.CTkButton(bar, text=tr("＋ イベント追加"), width=110, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._add_event).pack(side="left", padx=4)
        ctk.CTkButton(bar, text=tr("イベント削除"), width=110, height=30,
                      fg_color="transparent", border_width=1,
                      border_color="#e05a5a", text_color="#e05a5a",
                      hover_color=("gray85", "gray25"),
                      command=self._delete_event).pack(side="left", padx=4)
        ctk.CTkButton(bar, text=tr("イベントコピー"), width=110, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._copy_event).pack(side="left", padx=4)
        self.tool_sep = _toolbar_sep(bar)
        ctk.CTkButton(bar, text=tr("インポート"), width=100, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._open_import_dialog).pack(side="left", padx=4)
        # =277: 元に戻す/やり直す(履歴が無いときは無効表示)
        _toolbar_sep(bar)
        self.undo_btn = ctk.CTkButton(
            bar, text=tr("↶ 元に戻す"), width=100, height=30,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._undo)
        self.undo_btn.pack(side="left", padx=4)
        self.redo_btn = ctk.CTkButton(
            bar, text=tr("↷ やり直す"), width=100, height=30,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._redo)
        self.redo_btn.pack(side="left", padx=4)
        # Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z。Toplevel への bind は配下の全
        # ウィジェット(bindtags にトップレベルを含む)で効く。=300: 別
        # ウィンドウ側にも同じものを束ねる(履歴は編集画面と共通の1本)
        top = parent.winfo_toplevel()
        top.bind("<Control-z>", lambda e: self._undo() or "break")
        top.bind("<Control-y>", lambda e: self._redo() or "break")
        top.bind("<Control-Z>", lambda e: self._redo() or "break")
        self._hist_update_buttons()
        # 図の折りたたみトグル(=58)+自動フィット(=72)。=255でこの段へ統合
        # (旧・図直上の細い行 map_bar は廃止。表記も「▼ 折りたたみ」/
        # 「▶ 折りたたみ中」へ変更=Q3)。区切り線は挟まない(テキストリンク調
        # で見た目が違うため=Q5)。canvas_wrap の pack アンカーとして
        # self.map_bar 名は維持する。
        self.map_bar = bar
        self.map_toggle_btn = ctk.CTkButton(
            bar, text="", width=110, height=18,
            font=ctk.CTkFont(size=11), anchor="w",
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray85", "gray25"),
            command=self._toggle_map)
        if docked:
            self.map_toggle_btn.pack(side="left", padx=(10, 0))
        self.map_fit_btn = ctk.CTkButton(
            bar, text=tr("図の高さに合わせる"), width=120, height=18,
            font=ctk.CTkFont(size=11), anchor="w",
            fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
            hover_color=("gray85", "gray25"),
            command=self._toggle_map_fit)
        # =299: 配置モード(自動/手動)。「図の高さに合わせる」の右隣
        self.map_mode_btn = ctk.CTkButton(
            bar, text="", width=96, height=18,
            font=ctk.CTkFont(size=11), anchor="w",
            fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
            hover_color=("gray85", "gray25"),
            command=self._toggle_map_mode)
        if not docked:
            # =300: 別ウィンドウでは折りたたみ/自動フィット/取っ手は無し。
            # 「配置」だけ出し、右端に「ドッキング」
            self.map_mode_btn.pack(side="left", padx=(8, 0))
            ctk.CTkButton(
                bar, text=tr("ドッキング"), width=96, height=18,
                font=ctk.CTkFont(size=11), anchor="e",
                fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
                hover_color=("gray85", "gray25"),
                command=self._dock_map).pack(side="right", padx=(0, 4))
        else:
            self.map_undock_btn = ctk.CTkButton(
                bar, text=tr("ドッキング解除"), width=110, height=18,
                font=ctk.CTkFont(size=11), anchor="w",
                fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
                hover_color=("gray85", "gray25"),
                command=self._undock_map)

        # (=255: タイトルラベルは1段目 head_bar 内へ、折りたたみトグルと
        #  「図の高さに合わせる」は2段目 bar 内へ統合済み)

        # キャンバス(イベントの数珠つなぎ)。イベントが増えて図が画面外に
        # 伸びても見られるよう、縦横スクロールバーを付ける(grid配置)。
        # 高さは=58で170→110へ(1600x900のデスクトップ対応)。
        # CTkFrame は既定で 200px の高さを要求するので、grid_propagate(False)
        # + 明示の height で「キャンバス高さ+スクロールバー」に固定する
        # (=58。これをしないと CANVAS_H を下げても枠が縮まない)。
        canvas_wrap = ctk.CTkFrame(parent, corner_radius=10,
                                   height=self.CANVAS_H + 36)
        if docked:
            canvas_wrap.pack(fill="x", padx=14, pady=4, **before)
            canvas_wrap.grid_propagate(False)
        else:
            # =300: 別ウィンドウでは図が全面(ウィンドウの大きさに追従)
            canvas_wrap.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        canvas_wrap.grid_rowconfigure(0, weight=1)
        canvas_wrap.grid_columnconfigure(0, weight=1)
        self.canvas_wrap = canvas_wrap
        self.canvas = tk.Canvas(canvas_wrap, height=self.CANVAS_H, bg=CANVAS_BG,
                                highlightthickness=0)
        # スクロールバーは下部の編集パネル(CTkScrollableFrame)と見た目を
        # 揃えるため、素の tk.Scrollbar ではなく customtkinter の
        # CTkScrollbar(角丸のモダン表示)を tk.Canvas に接続して使う。
        hbar = ctk.CTkScrollbar(canvas_wrap, orientation="horizontal",
                                command=self.canvas.xview)
        vbar = ctk.CTkScrollbar(canvas_wrap, orientation="vertical",
                                command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=(6, 0))
        vbar.grid(row=0, column=1, sticky="ns", pady=(6, 2), padx=(2, 4))
        hbar.grid(row=1, column=0, sticky="ew", padx=(6, 0), pady=(2, 6))
        self.canvas_hbar = hbar
        self.canvas_vbar = vbar
        # =285: 高さ変更の取っ手(図の枠の直下・上下ドラッグ)。=300: 別
        # ウィンドウでは無し(map_sash は None)
        self.map_sash = None
        if docked:
            self.map_sash = ctk.CTkFrame(parent, height=7, corner_radius=3,
                                         fg_color=("gray80", "gray28"),
                                         cursor="sb_v_double_arrow")
            self.map_sash.pack(fill="x", padx=200, pady=(0, 2),
                               after=canvas_wrap)
            for w in (self.map_sash,):
                w.bind("<ButtonPress-1>", self._on_map_sash_press)
                w.bind("<B1-Motion>", self._on_map_sash_drag)
                w.bind("<ButtonRelease-1>", self._on_map_sash_release)
        self._sash_y0 = None
        # マウスホイール: 通常=縦、Shift+ホイール=横
        self.canvas.bind(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind(
            "<Shift-MouseWheel>",
            lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind(   # Linux(X11)のホイールは Button-4/5
            "<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind(
            "<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _init_map_toggle(self):
        """コンフィグから開閉状態を復元してトグル表示を整える。"""
        cfg = load_config()
        v = cfg.get(self.MAP_CFG_KEY)
        # 既定は「開く」。bool以外の値は無視する(壊れたコンフィグ対策)
        self._map_open = True if not isinstance(v, bool) else v
        # =72: 自動フィット。既定はOFF(従来どおり110px固定)
        f = cfg.get(self.MAP_FIT_CFG_KEY)
        self._map_fit = False if not isinstance(f, bool) else f
        self._apply_map_fit_style()
        self._apply_map_open()

    def _toggle_map(self):
        self._map_open = not self._map_open
        self._apply_map_open()
        cfg = load_config()
        cfg[self.MAP_CFG_KEY] = bool(self._map_open)
        save_config(cfg)
        if self._map_open:
            # 隠している間の変更が反映されていないので描き直す
            self._redraw_canvas()

    def _apply_map_open(self):
        """図の表示/非表示とトグルのラベルを現在の状態に合わせる。"""
        if self._map_undocked:
            return                # =300: 別ウィンドウ中は常に表示(折りたたみ無し)
        if self._map_open:
            # =255: 表記を「▼ 折りたたみ」/「▶ 折りたたみ中」へ変更(Q3)
            self.map_toggle_btn.configure(text=tr("▼ 折りたたみ"))
            if not self.canvas_wrap.winfo_manager():
                self.canvas_wrap.pack(fill="x", padx=14, pady=4,
                                      after=self.map_bar)
                self.map_sash.pack(fill="x", padx=200, pady=(0, 2),
                                   after=self.canvas_wrap)   # =285
            # =72: 自動フィットの切替は図が開いているときだけ意味を持つ
            if not self.map_fit_btn.winfo_manager():
                self.map_fit_btn.pack(side="left", padx=(8, 0))
            if not self.map_mode_btn.winfo_manager():     # =299
                self.map_mode_btn.pack(side="left", padx=(8, 0))
            if not self.map_undock_btn.winfo_manager():   # =300
                self.map_undock_btn.pack(side="left", padx=(8, 0))
        else:
            self.map_toggle_btn.configure(text=tr("▶ 折りたたみ中"))
            self.canvas_wrap.pack_forget()
            self.map_sash.pack_forget()   # =285
            self.map_fit_btn.pack_forget()
            self.map_mode_btn.pack_forget()
            self.map_undock_btn.pack_forget()

    def _destroy_map_area(self):
        for w in (getattr(self, "map_sash", None),
                  getattr(self, "canvas_wrap", None),
                  getattr(self, "map_bar", None)):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.map_sash = None

    def _undock_map(self, *, save: bool = True):
        if self._map_undocked:
            return
        self._map_undocked = True
        self._destroy_map_area()
        win = ctk.CTkToplevel(self)
        win.title(tr("イベント遷移図") + " - " + (self.title_var.get() or ""))
        self.map_win = win
        self.map_winmem = WindowMemory(win, self.MAP_WIN_KEY)
        if not self.map_winmem.restore():
            try:
                x = self.winfo_rootx() + 40
                y = self.winfo_rooty() + 40
            except Exception:
                x, y = 60, 60
            win.geometry(f"{self.MAP_WIN_W}x{self.MAP_WIN_H}+{x}+{y}")
        win.minsize(420, 240)
        self._build_map_area(win, docked=False)
        self.map_winmem.watch()
        self.map_winmem.install_close_hook(self._dock_map)   # ×=ドッキング
        self._refresh_map_mode_btn()
        self._redraw_canvas()
        if save:
            cfg = load_config()
            cfg[self.MAP_UNDOCK_CFG_KEY] = True
            save_config(cfg)

    def _dock_map(self, *, save: bool = True):
        if not self._map_undocked:
            return
        self._map_undocked = False
        win = getattr(self, "map_win", None)
        mem = getattr(self, "map_winmem", None)
        if mem is not None:
            try:
                mem.save_now()
            except Exception:
                pass
        self._destroy_map_area()
        self.map_win = None
        self.map_winmem = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        self._build_map_area(self, docked=True)
        self._apply_map_fit_style()
        self._apply_map_open()
        self._refresh_map_mode_btn()
        self._redraw_canvas()
        self._apply_map_height()
        if save:
            cfg = load_config()
            cfg[self.MAP_UNDOCK_CFG_KEY] = False
            save_config(cfg)

    def _init_map_undock(self):
        """コンフィグに「解除したまま」が残っていれば起動時に別ウィンドウで開く。"""
        cfg = load_config()
        if cfg.get(self.MAP_UNDOCK_CFG_KEY) is True:
            self._undock_map(save=False)

    def _toggle_map_fit(self):
        self._map_fit = not self._map_fit
        self._apply_map_fit_style()
        self._apply_map_height()
        cfg = load_config()
        cfg[self.MAP_FIT_CFG_KEY] = bool(self._map_fit)
        save_config(cfg)

    def _apply_map_fit_style(self):
        """ONのとき文字色をアクセント色にする(=68の詳細設定と同じ作法)。"""
        self.map_fit_btn.configure(
            text_color=_clr.ACCENT_TEXT if self._map_fit else TEXT_MUTED)

    def _map_content_height(self) -> int:
        """描画済みの図の実高さ(scrollregion の下端)。未描画なら下限値。"""
        try:
            sr = str(self.canvas.cget("scrollregion")).split()
            return int(float(sr[3]))
        except (IndexError, ValueError, tk.TclError):
            return self.CANVAS_H

    def _map_height_limit(self) -> int:
        """キャンバス高さの天井=ウィンドウ高さの80%に収まる高さ。

        図の枠(canvas_wrap)の上端からウィンドウの80%位置までを使える高さ。
        36=横スクロールバーぶん、12=下側の余白ぶん。ウィンドウがまだ
        実寸を持たない起動直後は既定サイズ(WIN_H)から概算する。
        """
        try:
            win_h = self.winfo_height()
            top = self.canvas_wrap.winfo_y() \
                if getattr(self, "_map_open", True) else 0
            if win_h > 1 and top > 0:
                return max(self.MAP_MIN_H,
                           int(win_h * self.MAP_MAX_RATIO) - top - 36 - 12)
        except tk.TclError:
            pass
        return max(self.MAP_MIN_H, int(self.WIN_H * self.MAP_MAX_RATIO) - 150)

    def _apply_map_height(self):
        """現在の設定(手動高さ/自動フィット)に合わせて図の高さを反映する。"""
        if getattr(self, "_map_undocked", False):
            return                # =300: 別ウィンドウは fill=both で追従
        h = getattr(self, "_map_manual_h", None) or self.CANVAS_H
        if getattr(self, "_map_fit", False):
            h = max(self.CANVAS_H, self._map_content_height())
        h = max(self.MAP_MIN_H, min(h, self._map_height_limit()))
        if int(self.canvas.cget("height")) != h:
            self.canvas.configure(height=h)
            self.canvas_wrap.configure(height=h + 36)

    def _init_map_manual_height(self):
        cfg = load_config()
        v = cfg.get(self.MAP_H_CFG_KEY)
        self._map_manual_h = int(v) if isinstance(v, (int, float)) and v > 0 \
            else None

    def _on_map_sash_press(self, e):
        self._sash_y0 = e.y_root
        self._sash_h0 = int(self.canvas.cget("height"))

    def _on_map_sash_drag(self, e):
        if getattr(self, "_sash_y0", None) is None:
            return
        h = self._sash_h0 + (e.y_root - self._sash_y0)
        h = max(self.MAP_MIN_H, min(int(h), self._map_height_limit()))
        if self._map_fit:
            self._map_fit = False
            self._apply_map_fit_style()
            cfg = load_config()
            cfg[self.MAP_FIT_CFG_KEY] = False
            save_config(cfg)
        self._map_manual_h = h
        self._apply_map_height()

    def _on_map_sash_release(self, _e):
        if getattr(self, "_sash_y0", None) is None:
            return
        self._sash_y0 = None
        cfg = load_config()
        cfg[self.MAP_H_CFG_KEY] = int(self._map_manual_h or self.CANVAS_H)
        save_config(cfg)

    def _on_win_configure(self, e):
        """ウィンドウのリサイズに自動フィットの天井を追従させる。

        <Configure> は子ウィジェットからも上がってくるので自分(Toplevel)の
        ぶんだけ拾い、高さが変わったときだけ再計算する(再入・空振り防止)。
        """
        if e.widget is not self:
            return
        if e.height != getattr(self, "_last_win_h", None):
            self._last_win_h = e.height
            # =285: 手動高さも80%の天井で切り詰めるので常に再計算する
            if getattr(self, "_map_open", True):
                self._apply_map_height()

    def _chain_order(self) -> tuple[list[str], list[str]]:
        """startからnextを辿った順序と、到達できないイベントを返す。"""
        return _smap.chain_order(self.data)

    def _layout_tree(self) -> dict:
        """イベントを左→右の木として配置し {ev_id: (x, y)} を返す。

        実体は scenario_map.layout_tree(共通描画モジュール)。
        """
        return _smap.layout_tree(self.data)

    # ---- =299: イベント図の配置モード(自動/手動) ----
    def _map_manual(self) -> bool:
        return _smap.is_manual(self.data)

    def _toggle_map_mode(self):
        """自動⇄手動。手動→自動は自動配置で描くが、各イベントの "pos" は
        **残す**(次に手動へ戻すと前の配置が復活。ユーザー決定)。"""
        if self._map_manual():
            self.data.pop("map_mode", None)
        else:
            self.data["map_mode"] = "manual"
        self._redraw_canvas()

    def _refresh_map_mode_btn(self):
        manual = self._map_manual()
        self.map_mode_btn.configure(
            text=tr("配置：手動") if manual else tr("配置：自動"),
            text_color=_clr.ACCENT_TEXT if manual else TEXT_MUTED)

    def _on_node_moved(self, ev_id: str, xy):
        """=299: ノードを D&D で離した(格子へ吸着・重なり回避済み)。"""
        ev = self.data["events"].get(ev_id)
        if not isinstance(ev, dict):
            return
        ev["pos"] = [int(xy[0]), int(xy[1])]
        self._redraw_canvas()

    def _redraw_canvas(self):
        # =299: 手動配置なら座標を確定(pos の無いイベントは自動配置から
        # 格子へ吸着し、重なるなら下へずらした位置を書き戻す)
        positions = on_move = None
        if _smap.is_manual(self.data):
            positions = _smap.manual_positions(self.data)
            for ev_id, (x, y) in positions.items():
                ev = self.data["events"].get(ev_id)
                if isinstance(ev, dict) and _smap.stored_pos(ev) != (x, y):
                    ev["pos"] = [int(x), int(y)]
            on_move = self._on_node_moved
        if hasattr(self, "map_mode_btn"):
            self._refresh_map_mode_btn()
        # =277: 構造操作(追加/削除/リネーム/コピー/変数・監視/背景 等)は
        # 最後に必ずここを通るので、履歴チェックポイントを置く
        self._hist_check()
        # =58: 折りたたみ中は描かない(開いたときに _toggle_map が描き直す)。
        # =300: 別ウィンドウ中は常に描く
        if not getattr(self, "_map_open", True) \
                and not getattr(self, "_map_undocked", False):
            return
        # 実体は scenario_map.draw_event_map(再生タブの表示専用ビューと共用)
        _smap.draw_event_map(
            self.canvas, self.data,
            selected=self.selected,
            on_click=self._select, on_rclick=self._on_event_node_rclick,
            positions=positions, on_move=on_move)
        # =72: 描画後の scrollregion が図の実高さなので、ここで反映する
        # (イベントの追加・削除・分岐の変更にも自動で追従する)
        self._apply_map_height()

    def _on_state_shift_wheel(self, event, step: int | None = None):
        """=276: Shift+ホイールでステート図を横に動かす(はみ出し時のみ)。"""
        try:
            first, last = self.state_canvas.xview()
        except Exception:
            return
        if float(first) <= 0.0 and float(last) >= 1.0:
            return          # はみ出していなければ動かさない(左寄せ固定)
        if step is None:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        self.state_canvas.xview_scroll(step, "units")

    def _on_state_xscroll(self, first, last):
        """ステート図の横スクロール位置をバーへ渡し、要否も判定する(=108)。"""
        try:
            self.state_hbar.set(first, last)
        except Exception:
            pass
        self._update_state_hbar(float(first), float(last))

    def _update_state_hbar(self, first: float = 0.0, last: float = 1.0):
        """はみ出しているときだけ横スクロールバーを出す(=108)。

        ステートが2〜3個しかない普段の編集で高さを食わないようにする。
        pack は「要約ラベルの前」へ差し込む(pack_forget で順序が失われるため
        before= の指定が必須。=107のmpv欄と同じ作法)。
        """
        bar = getattr(self, "state_hbar", None)
        if bar is None or getattr(self, "_state_hbar_busy", False):
            return
        need = (last - first) < 0.999
        mapped = bool(bar.winfo_ismapped())
        if need == mapped:
            return
        # pack/pack_forget は再びスクロール通知を呼びうるので1段で止める
        self._state_hbar_busy = True
        try:
            if need:
                bar.pack(fill="x", padx=8, pady=(0, 2),
                         before=self.trans_summary_label)
            else:
                bar.pack_forget()
        finally:
            self._state_hbar_busy = False

    def _scroll_state_into_view(self, positions: dict):
        """選択中のステートが画面外なら、見える位置まで横スクロールする(=108)。"""
        sid = self.sel_state
        if not sid or sid not in (positions or {}):
            return
        c = self.state_canvas
        try:
            sr = str(c.cget("scrollregion")).split()
            total = float(sr[2]) if len(sr) == 4 else 0.0
            vis = c.winfo_width()
            if total <= 0 or vis <= 1 or total <= vis:
                return
            x = positions[sid][0]
            left = c.canvasx(0)
            if left <= x - 40 and x + 40 <= left + vis:
                return                       # すでに見えている
            c.xview_moveto(max(0.0, (x - vis / 2)) / total)
        except Exception:
            pass

    def _redraw_state_canvas(self):
        ev = self.data["events"].get(self.selected or "")
        c = self.state_canvas
        c.configure(bg=_canvas_bg())   # テーマに応じて背景色を追従
        if not ev or "states" not in ev:
            c.delete("all")
            c.configure(scrollregion=(0, 0, 0, 0))
            self._update_state_hbar(0.0, 1.0)
            return
        # 実体は scenario_map.draw_state_map(再生タブの表示専用ビューと共用)
        pos = _smap.draw_state_map(
            c, ev, selected=self.sel_state,
            on_click=self._select_state, on_rclick=self._on_state_node_rclick)
        # =108: 図の幅が変わるのでスクロールバーの要否と位置を更新する
        self._scroll_state_into_view(pos)
        try:
            self._update_state_hbar(*[float(v) for v in c.xview()])
        except Exception:
            pass
        return pos

    def _on_event_node_rclick(self, ev_id: str, e):
        """イベント図のノード右クリック → カラーパレットを開く。"""
        if ev_id not in self.data["events"]:
            return
        self._open_node_palette(
            e.x_root, e.y_root,
            lambda col: self._set_node_color(ev_id, None, col))

    def _on_state_node_rclick(self, sid: str, e):
        """ステート図のノード右クリック → カラーパレットを開く。"""
        ev = self.data["events"].get(self.selected or "")
        if not isinstance(ev, dict) or sid not in (ev.get("states") or {}):
            return
        self._open_node_palette(
            e.x_root, e.y_root,
            lambda col, ev_id=self.selected: self._set_node_color(
                ev_id, sid, col))

    def _set_node_color(self, ev_id: str, state_id: str | None, color):
        """ノード色をモデル(raw dict)へ書き込む。color=None で既定色へ戻す。

        _apply_panel はイベントdictをin-placeで更新するため、ここで書いた
        "color" キーは保存(_do_save)までそのまま生き残る。
        """
        ev = self.data["events"].get(ev_id)
        if not isinstance(ev, dict):
            return
        target = ev if state_id is None \
            else (ev.get("states") or {}).get(state_id)
        if not isinstance(target, dict):
            return
        if color:
            target["color"] = color
        else:
            target.pop("color", None)
        self._redraw_canvas()
        if state_id is not None:
            self._redraw_state_canvas()

    def _close_node_palette(self):
        top = getattr(self, "_palette_win", None)
        self._palette_win = None
        if top is not None:
            try:
                top.grab_release()
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass

    def _open_node_palette(self, x_root: int, y_root: int, apply_cb):
        """右クリック位置へ色パレットのポップアップを開く。

        クリックで着色、「リセット」で既定色へ、パレット外クリック/Escで
        閉じる(grabで外クリックを捕まえる)。overrideredirect の素の
        Toplevel なので =108③のCTk withdraw バグの影響は受けない。
        """
        self._close_node_palette()
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        self._palette_win = top
        dark = ctk.get_appearance_mode() != "Light"
        bg = "#2b2b2b" if dark else "#f5f5f5"
        bd = "#777777" if dark else "#888888"
        fg = "#e8e8e8" if dark else "#111111"
        frame = tk.Frame(top, bg=bg, highlightthickness=1,
                         highlightbackground=bd)
        frame.pack(fill="both", expand=True)
        sw, gap, pad = 22, 4, 8
        cols = len(NODE_PALETTE[0])
        rows = len(NODE_PALETTE)
        w = pad * 2 + cols * sw + (cols - 1) * gap
        h = pad * 2 + rows * sw + (rows - 1) * gap
        cv = tk.Canvas(frame, width=w, height=h, bg=bg,
                       highlightthickness=0, bd=0)
        cv.pack()
        cells = []
        for ri, row in enumerate(NODE_PALETTE):
            for ci, col in enumerate(row):
                x0 = pad + ci * (sw + gap)
                y0 = pad + ri * (sw + gap)
                cv.create_rectangle(x0, y0, x0 + sw, y0 + sw,
                                    fill=col, outline=bd, width=1)
                cells.append((x0, y0, x0 + sw, y0 + sw, col))
        self._palette_cells = cells      # テストからのヒット判定用

        def on_swatch(e):
            for (x0, y0, x1, y1, col) in cells:
                if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                    apply_cb(col)
                    break
            self._close_node_palette()
            return "break"
        cv.bind("<Button-1>", on_swatch)
        tk.Button(frame, text=tr("リセット"), bg=bg, fg=fg,
                  activebackground=bd, relief="flat", bd=0,
                  font=(appfont.FAMILY, 10),
                  command=lambda: (apply_cb(None),
                                   self._close_node_palette())
                  ).pack(fill="x", padx=pad, pady=(0, 6))

        # 配置(画面外へはみ出さない)+外クリック/Escで閉じる。
        # =132: クランプ先は「右クリックした点のあるモニタ」。従来の
        # winfo_screenwidth はWindowsではプライマリモニタの幅なので、
        # サブディスプレイ上の右クリック位置がメイン側へ引き戻されていた。
        top.update_idletasks()
        tw = max(top.winfo_reqwidth(), w + 2)
        th = top.winfo_reqheight()
        mons = winstate.monitor_rects(top)
        fallback = (0, 0, int(top.winfo_screenwidth()),
                    int(top.winfo_screenheight()))
        x, y = winstate.clamp_point_popup(int(x_root), int(y_root),
                                          tw, th, mons, fallback)
        top.geometry(f"+{x}+{y}")
        top.bind("<Escape>", lambda _e: self._close_node_palette())

        def on_press(e):
            # grab中は外側のクリックもここへ届く。ポップアップの
            # 矩形外なら閉じる(内側はスウォッチ/ボタンに任せる)
            if not (0 <= e.x_root - top.winfo_rootx() <= top.winfo_width()
                    and 0 <= e.y_root - top.winfo_rooty()
                    <= top.winfo_height()):
                self._close_node_palette()
        top.bind("<Button-1>", on_press)
        try:
            top.grab_set()
        except Exception:
            pass
