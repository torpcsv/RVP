"""シナリオ編集: メッセージ領域・フィールド着色・確認ダイアログ(mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import time
from ..i18n import tr

from .common import (FIELD_ERR_BG, FIELD_ERR_EDGE, FIELD_WARN_BG,
    FIELD_WARN_EDGE, MSG_ERROR, MSG_INFO, MSG_OK, MSG_WARN, MUTED,
    TEXT_MUTED)
from .fields import VarRefField
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg


class _ScenarioEditorMessagesMixin:
    """ScenarioEditor の mixin(=301 分割)。メッセージ領域・フィールド着色・確認ダイアログ"""

    def _build_msg_area(self):
        self._pending_confirm = None
        self._msg_line_labels: list = []
        self._msg_buttons: list = []
        self._marks: list = []          # 着色中の不正フィールド
        self._pending_marks: list = []  # 検証中に記録する (widget, kind)

        self.msg_area = ctk.CTkFrame(self, corner_radius=10,
                                     fg_color=("gray92", "gray17"))
        # 生成時は非表示。_report / _confirm で pack する。
        head = ctk.CTkFrame(self.msg_area, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(6, 0))
        self.msg_icon = ctk.CTkLabel(
            head, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=MSG_INFO, anchor="w")
        self.msg_icon.pack(side="left")
        self.msg_close_btn = ctk.CTkButton(
            head, text="✕", width=24, height=22, font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray82", "gray28"), command=self._clear_message)
        self.msg_close_btn.pack(side="right")

        self.msg_body = ctk.CTkFrame(self.msg_area, fg_color="transparent")
        self.msg_body.pack(fill="x", padx=12, pady=(0, 2))
        self.msg_btn_row = ctk.CTkFrame(self.msg_area, fg_color="transparent")
        # ボタン行は確認時のみ pack する
        self._install_msg_autodismiss()   # =286

    def _clear_message(self):
        """メッセージ領域を空にして隠す(着色フィールドも元に戻す)。"""
        self._pending_confirm = None
        self._msg_kind = None   # =286
        self._clear_field_marks()
        for w in self._msg_line_labels:
            w.destroy()
        self._msg_line_labels = []
        for w in self._msg_buttons:
            w.destroy()
        self._msg_buttons = []
        try:
            self.msg_btn_row.pack_forget()
            self.msg_area.pack_forget()
        except Exception:
            pass

    def _render_message(self, kind: str, title: str, lines, buttons=None):
        """メッセージ領域を描画する。buttons=[(text, cmd, style), ...]。"""
        color = {"error": MSG_ERROR, "warn": MSG_WARN,
                 "ok": MSG_OK, "info": MSG_INFO,
                 "confirm": _clr.ACCENT_TEXT}.get(kind, MSG_INFO)
        # 既存の行/ボタンを消す
        for w in self._msg_line_labels:
            w.destroy()
        self._msg_line_labels = []
        for w in self._msg_buttons:
            w.destroy()
        self._msg_buttons = []

        self.msg_icon.configure(text=title, text_color=color)
        self._msg_kind = kind                  # =286: 自動消去の判定に使う
        self._msg_shown_at = time.monotonic()  # =286: 表示直後の同じ操作を無視
        # =58: 折り返し幅はウィンドウ幅に追従させる(固定780だと1360幅の
        # ウィンドウで無駄に折り返し、行数が増えてボタンが押し出される)
        try:
            wrap_px = max(560, self.winfo_width() - 80)
        except Exception:
            wrap_px = 780
        for ln in lines:
            lbl = ctk.CTkLabel(self.msg_body, text=("• " + ln) if len(lines) > 1
                               else ln,
                               font=ctk.CTkFont(size=12), text_color=color,
                               justify="left", anchor="w", wraplength=wrap_px)
            lbl.pack(fill="x", anchor="w")
            self._msg_line_labels.append(lbl)

        if buttons:
            for text, cmd, style in buttons:
                # 長い表記(3択保存警告など)が切れないよう、文字量で幅を広げる
                bw = max(96, sum(13 if ord(c) > 0x2000 else 8
                                 for c in text) + 24)
                if style == "danger":
                    b = ctk.CTkButton(
                        self.msg_btn_row, text=text, width=bw, height=28,
                        fg_color="#c0392b", hover_color="#a93226", command=cmd)
                elif style == "primary":
                    b = ctk.CTkButton(
                        self.msg_btn_row, text=text, width=bw, height=28,
                        fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER, command=cmd)
                else:
                    b = ctk.CTkButton(
                        self.msg_btn_row, text=text, width=bw, height=28,
                        fg_color="transparent", border_width=1, border_color=MUTED,
                        text_color=("gray20", "gray85"),
                        hover_color=("gray85", "gray25"), command=cmd)
                b.pack(side="right", padx=4)
                self._msg_buttons.append(b)
            self.msg_btn_row.pack(fill="x", padx=12, pady=(2, 8))
        else:
            self.msg_btn_row.pack_forget()

        self._pack_msg_area()

    def _pack_msg_area(self):
        """メッセージ領域を最下部へ表示する(=58 不具合修正)。

        **パネルより先に pack しないとボタン行がはみ出す**。パネルは
        `expand=True` で余白を全部取るため、後から pack したメッセージ
        領域には行数分の高さが回ってこず、下端のボタンが画面外へ切れて
        いた(ユーザー報告=「素材をコピーして相対パスで保存」の確認)。
        pack の `before=` で順序を先に差し込むと、メッセージ領域が必要な
        高さを先に確保し、パネル側が縮む。
        """
        try:
            self.msg_area.pack(side="bottom", fill="x", padx=14,
                               pady=(0, 10), before=self.panel_wrap)
        except Exception:
            try:
                self.msg_area.pack(side="bottom", fill="x", padx=14,
                                   pady=(0, 10))
            except Exception:
                pass

    def _report(self, kind: str, title: str, body):
        """検証エラー・警告・情報を画面内領域へ表示する(モーダルなし)。

        kind: "error" / "warn" / "ok" / "info"。
        body: 文字列(改行で複数行の一覧になる)または文字列のリスト。
        """
        self._pending_confirm = None
        if isinstance(body, str):
            lines = [s for s in body.split("\n") if s.strip() != ""]
        else:
            lines = [str(s) for s in body if str(s).strip() != ""]
        if not lines:
            lines = [title]
        self._render_message(kind, title, lines)
        # 検証で記録された不正フィールドを着色(error/warnのみ)
        if kind in ("error", "warn"):
            self._apply_field_marks()
        else:
            self._pending_marks = []
        # テスト観測用(messagebox 互換の記録)
        compat = {"error": "error", "warn": "warning",
                  "ok": "info", "info": "info"}.get(kind, "info")
        _pkg().MESSAGE_LOG.append((compat, (title, "\n".join(lines))))

    def _want_mark(self, widget, kind="error"):
        """検証中に不正フィールドを記録する(着色は _apply_field_marks で)。"""
        if widget is not None:
            self._pending_marks.append((widget, kind))

    def _apply_field_marks(self):
        self._clear_field_marks()
        for widget, kind in self._pending_marks:
            self._mark_field(widget, kind)
        self._pending_marks = []

    def _mark_field(self, widget, kind):
        # VarRefField は実体の入力欄/メニューへ委譲
        if isinstance(widget, VarRefField):
            widget = widget.menu if widget.use_var else widget.entry
        try:
            if widget is None or not widget.winfo_exists():
                return
        except Exception:
            return
        if getattr(widget, "_rvp_marked", False):
            return
        cls = widget.__class__.__name__
        bg = FIELD_ERR_BG if kind == "error" else FIELD_WARN_BG
        edge = FIELD_ERR_EDGE if kind == "error" else FIELD_WARN_EDGE
        saved = {}
        try:
            if cls == "CTkEntry":
                saved["fg_color"] = widget.cget("fg_color")
                widget.configure(fg_color=bg)
                seq = "<KeyRelease>"
            elif cls == "CTkOptionMenu":
                saved["fg_color"] = widget.cget("fg_color")
                widget.configure(fg_color=bg)
                seq = "<Button-1>"
            elif cls == "CTkCheckBox":
                saved["border_color"] = widget.cget("border_color")
                saved["text_color"] = widget.cget("text_color")
                widget.configure(border_color=edge, text_color=edge)
                seq = "<Button-1>"
            elif cls == "CTkButton":
                saved["fg_color"] = widget.cget("fg_color")
                saved["border_color"] = widget.cget("border_color")
                widget.configure(fg_color=bg, border_color=edge)
                seq = "<Button-1>"
            else:
                return
        except Exception:
            return
        widget._rvp_marked = True
        widget._rvp_saved = saved
        # 修正操作で自動的にメッセージ+着色を消す(要件2)。1度だけ束縛する。
        if not getattr(widget, "_rvp_bound", False):
            try:
                widget.bind(seq, lambda _e=None: self._on_field_edited(),
                            add="+")
                widget._rvp_bound = True
            except Exception:
                pass
        self._marks.append(widget)

    def _clear_field_marks(self):
        for widget in self._marks:
            try:
                if widget.winfo_exists():
                    widget.configure(**getattr(widget, "_rvp_saved", {}))
                widget._rvp_marked = False
            except Exception:
                pass
        self._marks = []

    def _on_field_edited(self):
        """着色中フィールドが修正されたらメッセージ+着色を消す。"""
        if self._marks:
            self._clear_message()

    def _install_msg_autodismiss(self):
        self._msg_kind = None
        self._msg_shown_at = 0.0
        self.bind_all("<Button-1>", self._on_any_click_for_msg, add="+")
        self.bind_all("<KeyRelease>", self._on_any_key_for_msg, add="+")

    def _msg_event_in_editor(self, e) -> bool:
        """イベントがこの編集画面内(メッセージ領域の外)で起きたか。"""
        try:
            if not self.winfo_exists() or not self.msg_area.winfo_manager():
                return False
            w = e.widget
            if not hasattr(w, "winfo_toplevel"):
                return False
            if w.winfo_toplevel() is not self:
                return False
            if str(w).startswith(str(self.msg_area)):
                return False
        except Exception:
            return False
        return time.monotonic() - self._msg_shown_at > 0.3

    def _msg_widget_is_input(self, w) -> bool:
        """クリックされたウィジェット(内部の canvas/label 含む)が入力系か。"""
        cur = w
        for _ in range(3):
            if cur is None:
                return False
            if cur.__class__.__name__ in self._EDIT_WIDGET_CLASSES:
                return True
            cur = getattr(cur, "master", None)
        return False

    def _on_any_click_for_msg(self, e):
        kind = getattr(self, "_msg_kind", None)
        if kind is None or self._pending_confirm is not None \
                or self._msg_buttons:      # ボタン付き(確認/選択)は消さない
            return
        if not self._msg_event_in_editor(e):
            return
        if kind in ("ok", "info") or self._msg_widget_is_input(e.widget):
            self._clear_message()

    def _on_any_key_for_msg(self, e):
        kind = getattr(self, "_msg_kind", None)
        if kind is None or self._pending_confirm is not None \
                or self._msg_buttons:
            return
        if not self._msg_event_in_editor(e):
            return
        try:
            is_text = e.widget.winfo_class() in ("Entry", "Text", "TEntry")
        except Exception:
            is_text = False
        if kind in ("ok", "info") or is_text:
            self._clear_message()

    def _confirm(self, message: str, on_yes, yes_text=None,
                 title=None, warn=False):
        """はい/いいえの確認を画面内領域で行う(モーダルaskyesnoの代替)。

        [実行]でon_yesを呼ぶ。[キャンセル]で領域を閉じる。
        """
        self._pending_confirm = on_yes
        _pkg().MESSAGE_LOG.append(("confirm", (title or tr("確認"), message)))
        if _pkg().AUTO_CONFIRM:
            on_yes()
            return
        self._render_message(
            "confirm", title or tr("確認"), [message],
            buttons=[(yes_text or tr("実行"), on_yes, "danger" if warn else "primary"),
                     (tr("キャンセル"), self._clear_message, "ghost")])
