"""シナリオ編集: 再生タブの表示制限(=358 再生時間を隠す・=359 シーク禁止)(mixin)。"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from ..i18n import tr
from .common import BOX_BG, BOX_BORDER, TEXT_HEAD


class _ScenarioEditorPlayUiMixin:
    """ScenarioEditor の mixin。ノードごとの再生タブの表示制限(=358/=359)。

    通常イベント/各ステート(=ノード)に「再生時間を隠す」(`hide_time`)と
    「シーク操作を禁止」(`no_seek`)のチェックを持たせる(ユーザー決定 Q10)。
    前のノードから引き継がない・既定オフ(false はキーを書かない)。
    置き場所はパネル最下部(BGM・背景ブロックの下)の1行(Q13)。
    音声なしノード(有効チャンネル0)では行を隠し、保存でも書かない。
    """

    PLAYUI_KEYS = ("hide_time", "no_seek")

    def _build_playui_row(self, parent):
        """パネル最下部(BGM・背景ブロックの下)の1行を作って pack する。

        BGM/背景ブロックは後から `after=chan_grid`/`after=bgm_box` で差し込まれる
        ので、この行は常にそれらの下になる。
        """
        self.playui_row = ctk.CTkFrame(parent, corner_radius=8, fg_color=BOX_BG,
                                       border_width=1, border_color=BOX_BORDER)
        row = ctk.CTkFrame(self.playui_row, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(row, text=tr("再生タブ:"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_HEAD).pack(side="left")
        self.hide_time_var = tk.BooleanVar(value=False)
        self.hide_time_check = ctk.CTkCheckBox(
            row, text=tr("再生時間を隠す"), variable=self.hide_time_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18)
        self.hide_time_check.pack(side="left", padx=(10, 0))
        self.no_seek_var = tk.BooleanVar(value=False)
        self.no_seek_check = ctk.CTkCheckBox(
            row, text=tr("シーク操作を禁止"), variable=self.no_seek_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18)
        self.no_seek_check.pack(side="left", padx=(16, 0))
        self.playui_row.pack(fill="x", pady=(2, 4))

    def _update_playui_visibility(self, noaudio: bool):
        """音声なしノードでは行を隠す(_update_noaudio_correlation から呼ぶ)。"""
        r = getattr(self, "playui_row", None)
        if r is None:
            return
        if noaudio:
            r.pack_forget()
        elif not r.winfo_manager():
            r.pack(fill="x", pady=(2, 4))

    def _playui_shown(self) -> bool:
        r = getattr(self, "playui_row", None)
        return r is not None and bool(r.winfo_manager())

    def _load_playui(self, node: dict):
        """ノード(通常イベント/ステート)の値をチェックへ反映する。"""
        node = node if isinstance(node, dict) else {}
        self.hide_time_var.set(node.get("hide_time") is True)
        self.no_seek_var.set(node.get("no_seek") is True)

    def _collect_playui(self, node: dict, noaudio: bool):
        """チェックの値をノードへ書く(既定 false と音声なしはキーを書かない)。"""
        for key, var in (("hide_time", self.hide_time_var),
                         ("no_seek", self.no_seek_var)):
            if var.get() and not noaudio:
                node[key] = True
            else:
                node.pop(key, None)
