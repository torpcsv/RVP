"""シナリオ編集: 背景ブロック(=347。ノードごとの背景・フェード)(mixin)。"""
from __future__ import annotations

import os
import tkinter as tk

import customtkinter as ctk

from ..i18n import tr
from . import common as _clr
from .common import (BOX_BG, BOX_BORDER, CTkOptionMenu, TEXT_HEAD, TEXT_MUTED,
                     _dialog_initialdir, _remember_dialog_dir)
from .paths import _safe_relpath
from ._hooks import _pkg


BG_FILETYPES_EXT = "*.png *.jpg *.jpeg *.webp *.bmp *.gif"
BG_FADE_DEFAULT = 0.5


class _ScenarioEditorBackgroundMixin:
    """ScenarioEditor の mixin。背景ブロック(=347)。

    BGM(=256)と同じ作法: トップレベルの「背景：ON/OFF」
    (`background_enabled`)で出し入れし、各ノード(通常イベント/各ステート)に
    「前の背景を引き継ぐ/背景を指定/背景オフ」を持たせる。
    OFF の間もブロックは隠れるだけで、読み込んだ値は保持して書き戻す。
    """

    # ---------------- 構築 ----------------

    def _build_bg_box(self, parent):
        """パネル最下部(BGM ブロックの下)の背景ブロックを作る。"""
        self.bg_box = ctk.CTkFrame(parent, corner_radius=8, fg_color=BOX_BG,
                                   border_width=1, border_color=BOX_BORDER)
        row = ctk.CTkFrame(self.bg_box, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(row, text=tr("背景:"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_HEAD).pack(side="left")
        self.bg_mode_var = tk.StringVar(value=self.BG_INHERIT)
        self.bg_mode_menu = CTkOptionMenu(
            row, variable=self.bg_mode_var, width=190, height=26,
            values=[self.BG_INHERIT, self.BG_SET, self.BG_OFF],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_bg_ui())
        self.bg_mode_menu.pack(side="left", padx=(6, 0))
        self.bg_hint = ctk.CTkLabel(
            row, text=tr("(背景はイベント/ステートをまたいで表示され続けます)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.bg_hint.pack(side="left", padx=8)

        # 「指定」の行: 画像を選ぶ・ファイル名・暗さ
        self.bg_set_row = ctk.CTkFrame(self.bg_box, fg_color="transparent")
        self.bg_choose_btn = ctk.CTkButton(
            self.bg_set_row, text=tr("画像を選ぶ…"), width=110, height=28,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._bg_choose)
        self.bg_choose_btn.pack(side="left")
        self.bg_file_label = ctk.CTkLabel(
            self.bg_set_row, text=tr("(なし)"), font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED, anchor="w")
        self.bg_file_label.pack(side="left", padx=(8, 12))
        ctk.CTkLabel(self.bg_set_row, text=tr("暗さ(%)"),
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.bg_dim_var = tk.StringVar(value="40")
        self.bg_dim_entry = ctk.CTkEntry(
            self.bg_set_row, textvariable=self.bg_dim_var, width=48,
            height=26, justify="right", font=ctk.CTkFont(size=12))
        self.bg_dim_entry.pack(side="left", padx=(4, 12))
        # フェード(秒)は「指定」「オフ」の両方で出すので別フレーム
        self.bg_fade_box = ctk.CTkFrame(self.bg_box, fg_color="transparent")
        ctk.CTkLabel(self.bg_fade_box, text=tr("切り替えのフェード(秒)"),
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.bg_fade_var = tk.StringVar(value="0.5")
        self.bg_fade_entry = ctk.CTkEntry(
            self.bg_fade_box, textvariable=self.bg_fade_var, width=48,
            height=26, justify="right", font=ctk.CTkFont(size=12))
        self.bg_fade_entry.pack(side="left", padx=(4, 6))
        ctk.CTkLabel(self.bg_fade_box, text=tr("0=即時"),
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self._bg_file = ""

    # ---------------- 表示 ----------------

    def _update_bg_ui(self):
        """3択に応じて「指定」の行・フェード欄を出し入れする。"""
        mode = self.bg_mode_var.get()
        self.bg_set_row.pack_forget()
        self.bg_fade_box.pack_forget()
        if mode == self.BG_SET:
            self.bg_set_row.pack(fill="x", padx=14, pady=(0, 4))
        if mode in (self.BG_SET, self.BG_OFF):
            self.bg_fade_box.pack(fill="x", padx=14, pady=(0, 6))

    def _refresh_bg_file_label(self):
        if self._bg_file:
            self.bg_file_label.configure(
                text=os.path.basename(self._bg_file), text_color=TEXT_HEAD)
        else:
            self.bg_file_label.configure(text=tr("(なし)"),
                                         text_color=TEXT_MUTED)

    def _bg_choose(self):
        """画像を選ぶ(パスは base_dir 基準の相対で保持=BGM と同じ)。"""
        kwargs = {}
        start = self._bg_file if os.path.isabs(self._bg_file) \
            else os.path.join(self.base_dir, self._bg_file)
        init = os.path.dirname(start) if self._bg_file \
            else _dialog_initialdir(self.base_dir)
        if init and os.path.isdir(init):
            kwargs["initialdir"] = init
        path = _pkg().filedialog.askopenfilename(
            parent=self, title=tr("背景画像を選択"),
            filetypes=[(tr("画像ファイル"), BG_FILETYPES_EXT),
                       (tr("すべてのファイル"), "*.*")],
            **kwargs)
        if not path:
            return
        _remember_dialog_dir(path)
        self._bg_file = _safe_relpath(os.path.normpath(path), self.base_dir)
        self._refresh_bg_file_label()

    # ---------------- 読み書き ----------------

    def _load_background(self, raw):
        """ノードの "background" 値をブロックへ反映する。

        None=引き継ぐ / {"off": true} / {"file", "dim"} / 文字列(=file)。
        """
        self._bg_file = ""
        self.bg_dim_var.set("40")
        self.bg_fade_var.set(self._fmt_fade(BG_FADE_DEFAULT))
        if isinstance(raw, str):
            raw = {"file": raw}
        if not isinstance(raw, dict):
            self.bg_mode_var.set(self.BG_INHERIT)
        else:
            fade = raw.get("fade", BG_FADE_DEFAULT)
            if isinstance(fade, (int, float)) and not isinstance(fade, bool):
                self.bg_fade_var.set(self._fmt_fade(fade))
            if raw.get("off"):
                self.bg_mode_var.set(self.BG_OFF)
            else:
                self.bg_mode_var.set(self.BG_SET)
                f = raw.get("file")
                self._bg_file = f if isinstance(f, str) else ""
                d = raw.get("dim", 40)
                if isinstance(d, (int, float)) and not isinstance(d, bool):
                    self.bg_dim_var.set(str(int(round(d))))
        self._refresh_bg_file_label()
        self._update_bg_ui()

    @staticmethod
    def _fmt_fade(v) -> str:
        v = float(v)
        return str(int(v)) if v == int(v) else ("%g" % v)

    def _collect_background(self, where: str):
        """ブロックの内容を JSON 値へ。(エラー文|None, 値|None)。

        値: None=引き継ぐ(キー省略) / {"off": true[, "fade"]} /
        {"file", "dim"[, "fade"]}。fade は既定 0.5 のとき書かない。
        """
        mode = self.bg_mode_var.get()
        if mode not in (self.BG_SET, self.BG_OFF):
            return None, None
        try:
            fade = float(self.bg_fade_var.get().strip() or BG_FADE_DEFAULT)
            if not (0 <= fade <= 10):
                raise ValueError
        except ValueError:
            self._want_mark(self.bg_fade_entry, "error")
            return (tr("{0}: 背景のフェードは 0〜10 の秒数で指定してください"
                       ).format(where), None)
        out = {}
        if mode == self.BG_OFF:
            out["off"] = True
        else:
            if not self._bg_file:
                self._want_mark(self.bg_choose_btn, "error")
                return (tr("{0}: 背景を「指定」にしていますが、画像がありません"
                           ).format(where), None)
            try:
                dim = int(self.bg_dim_var.get().strip())
                if not (0 <= dim <= 100):
                    raise ValueError
            except ValueError:
                self._want_mark(self.bg_dim_entry, "error")
                return (tr("{0}: 背景の暗さ(%)は 0〜100 の整数で指定してください"
                           ).format(where), None)
            out["file"] = self._bg_file
            out["dim"] = dim
        if abs(fade - BG_FADE_DEFAULT) > 1e-9:
            out["fade"] = int(fade) if fade == int(fade) else fade
        return None, out

    # ---------------- ON/OFF ----------------

    def _bg_btn_text(self) -> str:
        return tr("背景：ON") if self.background_enabled else tr("背景：OFF")

    def _toggle_background_enabled(self):
        """背景機能の ON/OFF(BGM と同じ作法。保存で JSON へ)。"""
        self.background_enabled = not self.background_enabled
        self._apply_background_enabled()
        self._hist_check()

    def _apply_background_enabled(self):
        self.bg_toggle_btn.configure(text=self._bg_btn_text())
        if self.background_enabled:
            if not self.bg_box.winfo_manager():
                after = self.bgm_box if self.bgm_box.winfo_manager() \
                    else self.chan_grid
                self.bg_box.pack(fill="x", pady=(2, 4), after=after)
        else:
            self.bg_box.pack_forget()

    # ---------------- 旧形式の移し替え ----------------

    @staticmethod
    def _migrate_top_background(data: dict) -> bool:
        """=347: 旧形式のトップレベル background を開始ノードへ移す。

        開始イベント(ステート形式ならその開始ステート)に background が
        無ければそこへ入れ、トップレベルからは消す。`background_enabled` が
        無ければ true にする(ユーザー決定 Q1/Q2)。移したら True。
        """
        raw = data.get("background")
        if raw is None:
            return False
        if isinstance(raw, str):
            raw = {"file": raw}
        data.pop("background", None)
        data.setdefault("background_enabled", True)
        events = data.get("events")
        ev = events.get(data.get("start")) if isinstance(events, dict) \
            else None
        if not isinstance(ev, dict):
            return True
        node = ev
        if isinstance(ev.get("states"), dict):
            node = ev["states"].get(ev.get("start"))
            if not isinstance(node, dict):
                return True
        if node.get("background") is None and isinstance(raw, dict):
            node["background"] = dict(raw)
        return True
