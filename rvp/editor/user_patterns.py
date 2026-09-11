"""ユーザーパターン編集ポップアップ(UserPatternDialog)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from ..i18n import load_config, save_config, tr

from .common import (CTkOptionMenu, MSG_ERROR, MSG_OK, MUTED, TEXT_MUTED,
    _front_window, _place_popup)
from .review_support import SCRIPT_EDIT_STEP_TYPES
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg


class UserPatternDialog(ctk.CTkToplevel):
    """ユーザーパターンの編集ポップアップ(=205・仕様 6.1)。

    - 20枠をボタンで切り替え、同じ描画エディタ(ScriptEditGraph)で
      **点だけ**を打って「保存」する(パターンの配置・グループ化は不可)。
    - **=231: 枠は種別ごと**(linear=L1〜L20 / twist=T1〜T20 /
      rotate_ufo=U1〜U20 / rotate_a10cyclonesa=A1〜A20 /
      vibration=V1〜V20)。この画面は**開いたときの対象の種別に固定**
      (ユーザー決定)。
    - 名前は固定。基準長=最終 at(保存時に先頭 at を 0 へ正規化)。
    - 保存先は RVP のコンフィグ(アプリ全体で共有・シナリオ非依存)。
    - **=231 の既定**: 縮尺=1秒 / 位置[pos]=5単位 / 時間[at]=0.05秒単位。
      rotate系・vibration では**階段**で描く。
    """

    GRAPH_H = 300
    LEVEL_1S = 3              # =231: 縮尺表示が「1秒」になる段(LEVELS)
    GRID_POS_DEF = 5          # =231: 位置[pos]の既定
    GRID_AT_DEF = 50          # =231: 時間[at]の既定(0.05秒)

    def __init__(self, master, owner=None, kind: str = "linear"):
        super().__init__(master)
        from .. import script_edit
        self._se = script_edit
        self.owner = owner            # ItemReviewDialog(保存後の反映先)
        self.kind = kind if kind in script_edit.USER_PAT_PREFIX else "linear"
        self.title(tr("ユーザーパターン編集") + f"（{self.kind}）")
        self.resizable(True, False)
        self._patterns = script_edit.load_user_patterns(load_config(),
                                                        self.kind)
        self._keys = script_edit.user_pattern_keys(self.kind)
        # =231 要望4: 既定は**未登録のうち一番小さい枠**(全部埋まって
        # いれば最後の枠=X20)
        self._key = self._keys[
            script_edit.first_free_slot(self._patterns, self.kind) - 1]
        self.model = script_edit.ScriptEditModel()

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=(12, 12))

        # ---- 1段目: 枠の切り替え ----
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text=tr("編集する枠:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left", anchor="n", padx=(0, 6), pady=(2, 0))
        # =218: 20枠になったので **2段×10枠** の grid で並べる(ユーザー決定。
        # 1列20個だと popup の幅が 1400 必要になる)。
        slots = ctk.CTkFrame(row1, fg_color="transparent")
        slots.pack(side="left")
        self.slot_btns = {}
        for j, ukey in enumerate(self._keys):
            b = ctk.CTkButton(
                slots, text=ukey, width=46, height=26,
                font=ctk.CTkFont(size=12), fg_color="transparent",
                border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=lambda k=ukey: self._select_slot(k))
            b.grid(row=j // 10, column=j % 10, padx=(0, 4), pady=(0, 4),
                   sticky="w")
            self.slot_btns[ukey] = b
        self.state_label = ctk.CTkLabel(row1, text="",
                                        font=ctk.CTkFont(size=11),
                                        text_color=TEXT_MUTED)
        self.state_label.pack(side="left", anchor="n", padx=(10, 0),
                              pady=(4, 0))

        # ---- 2段目: グリッド(編集モードと同じ選択肢) ----
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(6, 0))
        none_lbl = tr("なし")
        ctk.CTkLabel(row2, text=tr("位置[pos]:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self._grid_pos_map = {script_edit.grid_label(g, none_lbl): g
                              for g in script_edit.GRID_POS_CHOICES}
        self.grid_pos_var = tk.StringVar(value=script_edit.grid_label(
            self.GRID_POS_DEF, none_lbl))          # =231: 既定は 5単位
        CTkOptionMenu(
            row2, variable=self.grid_pos_var, width=84, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_pos_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change()
        ).pack(side="left", padx=(4, 10))
        ctk.CTkLabel(row2, text=tr("時間[at]:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self._grid_at_map = {script_edit.grid_at_label(g, none_lbl): g
                             for g in script_edit.GRID_AT_CHOICES}
        self.grid_at_var = tk.StringVar(value=script_edit.grid_at_label(
            self.GRID_AT_DEF, none_lbl))           # =231: 既定は 0.05秒
        CTkOptionMenu(
            row2, variable=self.grid_at_var, width=100, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_at_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change()
        ).pack(side="left", padx=(4, 10))

        # ---- グラフ(点だけの編集) ----
        self.graph = script_edit.ScriptEditGraph(
            inner, self.model, on_change=self._on_change,
            on_select=lambda: None, on_menu=self._menu,
            height=self.GRAPH_H)
        self.graph.plain = True       # =206: 再生位置の線・追従バッジなし
        # =231 要望5: rotate系・vibration は**階段**で描く(次の指示まで
        # 値を保つ=編集画面と同じ見え方)。ヒートマップも出さない。
        self.graph.step = self.kind in SCRIPT_EDIT_STEP_TYPES
        self.graph.heat = not self.graph.step
        self.graph.pack(fill="both", expand=True, pady=(6, 0))
        ctk.CTkLabel(
            inner,
            text=tr("点だけで編集します（パターンは使えません）。"
                    "最後の点の時間が基準長になります"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w",
        ).pack(fill="x", pady=(4, 0))

        # ---- 下段: 保存/メッセージ ----
        row3 = ctk.CTkFrame(inner, fg_color="transparent")
        row3.pack(fill="x", pady=(8, 0))
        self.save_btn = ctk.CTkButton(
            row3, text=tr("保存"), width=96, height=30, fg_color=_clr.ACCENT,
            hover_color=_clr.ACCENT_HOVER, command=self._save)
        self.save_btn.pack(side="left")
        self.msg_label = ctk.CTkLabel(row3, text="",
                                      font=ctk.CTkFont(size=11),
                                      text_color=TEXT_MUTED, anchor="w")
        self.msg_label.pack(side="left", padx=(10, 0), fill="x", expand=True)

        # =218: 枠が20個(2段)になったぶん少しだけ縦を足す(幅は据え置き)
        _place_popup(self, master, 840, 500)
        _front_window(self)
        self._load_slot(self._key)
        self._on_grid_change()

    # ---- 枠の切り替え ----

    def _select_slot(self, key: str):
        if key == self._key:
            return
        if self.model.dirty and not self._confirm_discard():
            return
        self._load_slot(key)

    def _confirm_discard(self) -> bool:
        """未保存の編集がある枠から離れる確認。テスト用の自動応答つき。"""
        if _pkg().USER_PAT_UNSAVED_AUTO is not None:
            return bool(_pkg().USER_PAT_UNSAVED_AUTO)
        win = ctk.CTkToplevel(self)
        win.title("RVP")
        result = {"ok": False}
        ctk.CTkLabel(win, justify="left",
                     text=tr("保存していない編集があります。\n"
                             "破棄して切り替えますか？"),
                     font=ctk.CTkFont(size=13)).pack(padx=24, pady=(18, 10))
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(pady=(0, 14))

        def done(ok):
            result["ok"] = ok
            win.destroy()
        ctk.CTkButton(btns, text=tr("破棄する"), width=90, height=28,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=lambda: done(True)).pack(side="left", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=28,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      command=lambda: done(False)).pack(side="left", padx=4)
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return result["ok"]

    def _load_slot(self, key: str):
        self._key = key
        shape = self._patterns.get(key)
        self.model.load(list(shape) if shape else [])
        span = shape[-1][0] if shape else 0
        # =206: initial_view は view_ms を now_ms から作るので、now_ms は
        # 常に 0 のままにする(=205 では now_ms を画面外へ飛ばしていたため、
        # 2回目以降の枠切り替えで view ごと画面外へ飛び、表示も打点も
        # できなくなっていた)。再生位置の線は plain フラグで消す。
        self.graph.now_ms = 0.0
        self.graph.initial_view(span)
        # =231 要望2: 既定の拡大率は**縮尺1秒**(initial_view は素材長から
        # 段を選ぶが、ユーザーパターンは短いので拡大側で固定する)
        self.graph.set_level(self.LEVEL_1S)
        self.graph.view_ms = 0.0
        self.graph.follow = False
        self.graph.redraw()
        for ukey, b in self.slot_btns.items():
            on = ukey == key
            b.configure(fg_color=_clr.ACCENT if on else "transparent",
                        border_width=0 if on else 1,
                        text_color=("white", "white") if on
                        else ("gray20", "gray85"),
                        hover_color=_clr.ACCENT_HOVER if on
                        else ("gray85", "gray25"))
        self.state_label.configure(
            text=tr("登録済み（基準長 {0}ms）").format(span) if shape
            else tr("未登録"))
        self.msg_label.configure(text="", text_color=TEXT_MUTED)

    # ---- 編集 ----

    def _on_grid_change(self):
        self.graph.grid_pos = self._grid_pos_map.get(
            self.grid_pos_var.get(), self.GRID_POS_DEF)
        self.graph.grid_at = self._grid_at_map.get(
            self.grid_at_var.get(), self.GRID_AT_DEF)
        self.graph.redraw()

    def _on_change(self):
        self.msg_label.configure(text="", text_color=TEXT_MUTED)

    def _menu(self, event, ctx):
        """右クリックメニュー: 点の削除だけ(グループ化・パターンは無し)。"""
        if ctx.get("at") is None or not self.model.selection:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("削除"), command=self._menu_delete)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _menu_delete(self):
        if self.model.delete_selected():
            self.graph.redraw()
            self._on_change()

    # ---- 保存 ----

    def _save(self):
        shape = self._se.normalize_user_shape(self.model.points)
        if shape is None:
            self.msg_label.configure(
                text=tr("2点以上を打ってください（幅0は登録できません）"),
                text_color=MSG_ERROR)
            return
        cfg = load_config()
        self._se.save_user_pattern(cfg, self._key, shape)
        save_config(cfg)
        self._patterns[self._key] = shape
        # 正規化(先頭atの0ずらし)の結果を画面へも反映する
        self.model.load(list(shape))
        self.model.dirty = False
        self.graph.redraw()
        self.state_label.configure(
            text=tr("登録済み（基準長 {0}ms）").format(shape[-1][0]))
        self.msg_label.configure(text=tr("保存しました") + f": {self._key}",
                                 text_color=MSG_OK)
        if self.owner is not None:
            try:
                self.owner._on_user_patterns_saved()
            except Exception:
                pass
