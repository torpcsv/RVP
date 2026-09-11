"""メイン画面: 選択肢カード・数値入力カード(自動選択を含む)(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
from ..i18n import tr

from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _RVPAppInteractMixin:
    """RVPApp の mixin(=301 分割)。選択肢カード・数値入力カード(自動選択を含む)"""

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
                fg_color=("gray78", "gray28"), hover_color=_clr.ACCENT_HOVER,
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
