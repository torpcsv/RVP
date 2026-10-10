"""メイン画面: 選択肢カード・数値入力カード(自動選択を含む)(RVPApp の mixin)。"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from ..i18n import tr
from .. import apptheme, appfont
from .common import HAS_PIL as _HAS_PIL, PILImage as _PILImage, \
    PILImageTk as _PILImageTk
try:
    from PIL import ImageDraw as _PILDraw
except Exception:                       # pragma: no cover
    _PILDraw = None

from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _RVPAppInteractMixin:
    """RVPApp の mixin(=301 分割)。選択肢カード・数値入力カード(自動選択を含む)"""

    def _build_choice_buttons(self, grid, labels, command, colors=None) -> list:
        """選択肢ボタンを grid へ並べて返す(①のカード)。

        =363: colors[i] が "#RRGGBB" ならその色で塗り、文字色は明るさから
        黒系/白系を自動で選ぶ(マウスを乗せると少し暗く)。""=既定色。
        """
        cols = self.CHOICE_COLS.get(len(labels), 3)
        # =163: **前回の列設定を必ず落としてから**新しい列を設定する。
        # grid の列設定はウィジェットに残り続けるので、一度3列にした
        # choice_grid は、次に2択を出しても「3列目(空)」が幅を取り続ける
        # =ボタンが画面の2/3で止まる。しかもカードは作り直さないので、
        # シナリオを開き直しても再起動まで直らなかった(ユーザー報告)。
        for c in range(max(self.CHOICE_COLS.values())):
            grid.grid_columnconfigure(c, weight=0, uniform="")
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="choice")
        colors = list(colors or [])
        out = []
        for i, label in enumerate(labels):
            col = colors[i] if i < len(colors) else ""
            if col:
                # 色はテーマの色写像に巻き込まれない値へ(user_color)
                kw = dict(fg_color=apptheme.user_color(col),
                          hover_color=apptheme.user_color(
                              apptheme.shade(col, 0.85)),
                          text_color=apptheme.ink_for(col))
            else:
                kw = dict(fg_color=("gray78", "gray28"),
                          hover_color=_clr.ACCENT_HOVER,
                          text_color=("gray12", "gray92"))
            btn = ctk.CTkButton(
                grid, text=label, height=46,
                font=ctk.CTkFont(size=14),
                command=lambda i=i: command(i), **kw)
            btn.grid(row=i // cols, column=i % cols, sticky="ew",
                     padx=4, pady=4)
            out.append(btn)
        return out

    def _bg_focus_widgets(self) -> list:
        """=373: 背景イラストを薄く重ねるウィジェット(選択肢ボタン・数値入力)。

        表示されていないもの(カードが外れている・別ページ)は
        BackgroundArt 側が winfo_viewable で除く。
        """
        out = list(getattr(self, "choice_buttons", None) or [])
        for name in ("input_entry", "input_submit_btn"):
            w = getattr(self, name, None)
            if w is not None:
                out.append(w)
        return out

    def _bg_focus_refresh(self) -> None:
        art = getattr(self, "bg_art", None)
        if art is not None:
            art.refresh_focus()

    def _show_choice_card(self, labels: list[str], colors=None):
        for b in self.choice_buttons:
            b.destroy()
        self.choice_buttons = []
        self._choice_labels = list(labels)
        self._choice_colors = list(colors or [])          # =363
        # 見出し/グリッドを一旦外し、[チェック][見出し][グリッド]の順で確実に戻す
        self.choice_head.pack_forget()
        self.choice_grid.pack_forget()
        self.choice_buttons = self._build_choice_buttons(
            self.choice_grid, labels, self._on_choice, self._choice_colors)
        self.choice_head.pack(fill="x", pady=(6, 0))
        self.choice_grid.pack(fill="x", pady=(8, 0))
        self._pack_play_overlay(self.choice_card)
        # =351 Q6 → =356 Q6: ②画像のみで新しい選択肢が出たら③画像と選択肢へ
        # (以前は①へ戻していた)。③はそのまま、①も①のまま。
        if self.bg_solo_stage == 2:
            self._bg_solo_stage = 3
        self._render_solo_choice()
        self._bg_focus_refresh()          # =373

    def _exit_bg_solo(self):
        """=351: 「イラストのみ表示」中なら UI を戻す(数値入力の出現時)。"""
        art = getattr(self, "bg_art", None)
        if art is not None:
            art.exit_solo()

    # ---- =363 ③画像と選択肢: 覆いの絵へ半透明で描き込む ----
    # Tk の部品は半透明にできないので、③の選択肢カードは覆い(tk.Canvas)の
    # 絵の該当範囲を PIL で切り出し、カードの地(不透明度 SOLO_CARD_ALPHA)と
    # ボタン(SOLO_BTN_ALPHA)を合成した画像として置く。文字はキャンバスの
    # テキストで上から描く(くっきり)。クリックは覆いのクリックで位置判定、
    # マウスを乗せたボタンは枠を明るく太くする(Q4)。
    SOLO_CARD_ALPHA = 0.60
    SOLO_BTN_ALPHA = 0.85

    def _solo_reset_layout(self):
        self._solo_layout = None
        self._solo_photo = None
        self._solo_hover = None

    def _solo_rgb(self, widget, color) -> tuple:
        """色指定(タプル=ライト/ダーク・色名・#RRGGBB)を (r, g, b) へ。"""
        if isinstance(color, (tuple, list)):
            color = color[0] if ctk.get_appearance_mode() == "Light" \
                else color[1]
        try:
            r, g, b = widget.winfo_rgb(color)
            return (r // 257, g // 257, b // 257)
        except Exception:
            return (128, 128, 128)

    @staticmethod
    def _hex(rgb) -> str:
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    def _render_solo_choice(self):
        """=356/=363: ③画像と選択肢の最下部の選択肢カードを描き直す/消す。

        画像の大きさ・位置は①②と同じで、カードは画像の上に半透明で重なる
        (=356 Q2 A・=363 Q4 A)。並び・見出し・残り時間は①のカードと同じ。
        選択肢が出ていない、または③でないときは何も描かない(=356 Q1)。
        """
        art = getattr(self, "bg_art", None)
        cover = art.solo_cover if art is not None else None
        if cover is None:
            self._solo_reset_layout()
            return
        try:
            cover.delete("solochoice")
        except tk.TclError:
            return
        self._solo_reset_layout()
        labels = getattr(self, "_choice_labels", None) or []
        if self.bg_solo_stage != 3 or not labels or not self.choice_buttons:
            return
        if not _HAS_PIL:
            return
        try:
            s = float(ctk.ScalingTracker.get_widget_scaling(cover))
        except Exception:
            s = 1.0
        W, H = cover.winfo_width(), cover.winfo_height()
        if W < 60 or H < 60:
            return

        def px(v):
            return int(round(v * s))
        n = len(labels)
        cols = self.CHOICE_COLS.get(n, 3)
        rows = (n + cols - 1) // cols
        bw = max(1, px(2))                 # カードの枠
        head_h, btn_h, cell_pad = px(28), px(46), px(4)
        card_h = bw * 2 + px(10) + px(6) + head_h + px(8) \
            + rows * (btn_h + cell_pad * 2) + px(12)
        cx0, cx1 = px(12), W - px(12)
        cy1 = H - px(12)
        cy0 = max(0, cy1 - card_h)
        cw, ch = cx1 - cx0, cy1 - cy0
        ix0 = cx0 + bw + px(16)
        ix1 = cx1 - bw - px(16)
        head_cy = cy0 + bw + px(10) + px(6) + head_h // 2
        gy0 = cy0 + bw + px(10) + px(6) + head_h + px(8)
        col_w = (ix1 - ix0) / float(cols)
        colors = list(getattr(self, "_choice_colors", None) or [])
        default_btn = self._solo_rgb(cover, ("gray78", "gray28"))
        btn_rects, btn_rgbs = [], []
        for i in range(n):
            r, c = divmod(i, cols)
            x0 = int(ix0 + c * col_w) + cell_pad
            x1 = int(ix0 + (c + 1) * col_w) - cell_pad
            y0 = gy0 + r * (btn_h + cell_pad * 2) + cell_pad
            btn_rects.append((x0, y0, x1, y0 + btn_h))
            col = colors[i] if i < len(colors) else ""
            btn_rgbs.append(self._solo_rgb(cover, col) if col else default_btn)

        # ---- 絵(画像の該当範囲 + 半透明のカード地・ボタン) ----
        base, bx = art.solo_base_image()
        region = _PILImage.new("RGB", (cw, ch), (0, 0, 0))
        if base is not None:
            try:
                src = base if base.mode == "RGB" else base.convert("RGB")
                region.paste(src, (int(bx) - cx0, -cy0))
            except Exception:
                pass
        layer = _PILImage.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = _PILDraw.Draw(layer)
        card_rgb = self._solo_rgb(cover, _clr.CARD_COLOR)
        accent_rgb = self._solo_rgb(cover, _clr.ACCENT)
        d.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=px(10),
                            fill=card_rgb + (int(255 * self.SOLO_CARD_ALPHA),),
                            outline=accent_rgb + (230,), width=bw)
        for (x0, y0, x1, y1), rgb in zip(btn_rects, btn_rgbs):
            d.rounded_rectangle([x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0],
                                radius=px(6),
                                fill=rgb + (int(255 * self.SOLO_BTN_ALPHA),))
        img = _PILImage.alpha_composite(region.convert("RGBA"), layer)
        photo = _PILImageTk.PhotoImage(img.convert("RGB"))
        self._solo_photo = photo                     # 参照を保持
        cover.create_image(cx0, cy0, anchor="nw", image=photo,
                           tags=("solochoice",))

        # ---- 文字(見出し・残り時間・ボタン) ----
        fam = appfont.FAMILY or ctk.CTkFont().cget("family")
        f_head = (fam, -px(14), "bold")
        f_timer = (fam, -px(13), "bold")
        f_btn = (fam, -px(14))
        cover.create_text(ix0, head_cy, anchor="w", text=tr("選択してください"),
                          font=f_head, tags=("solochoice",),
                          fill=self._hex(self._solo_rgb(cover, _clr.ACCENT_TEXT)))
        timer_id = cover.create_text(
            ix1, head_cy, anchor="e", text=self.choice_timer_label.cget("text"),
            font=f_timer, tags=("solochoice",),
            fill=self._hex(self._solo_rgb(cover, _clr.WARN_TEXT)))
        disabled = any(self._btn_disabled(b) for b in self.choice_buttons)
        hl_ids = []
        hl_w = max(2, px(3))
        for i, ((x0, y0, x1, y1), rgb) in enumerate(zip(btn_rects, btn_rgbs)):
            ink = "#8a8a8a" if disabled else apptheme.ink_for(self._hex(rgb))
            cover.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=labels[i],
                              font=f_btn, fill=ink, width=max(10, x1 - x0 - px(8)),
                              justify="center", tags=("solochoice",))
            hl_ids.append(cover.create_rectangle(
                x0 + 1, y0 + 1, x1 - 1, y1 - 1, outline="", width=hl_w,
                tags=("solochoice",)))
        self._solo_layout = {
            "card": (cx0, cy0, cx1, cy1), "buttons": btn_rects,
            "labels": list(labels), "rgbs": btn_rgbs, "timer": timer_id,
            "hl": hl_ids, "disabled": disabled,
        }

    @staticmethod
    def _btn_disabled(b) -> bool:
        try:
            return b.cget("state") == "disabled"
        except Exception:
            return False

    def _solo_hit(self, x, y):
        """③の位置判定: ボタンの添字 / "card"(カードの地)/ None(画像)。"""
        lay = getattr(self, "_solo_layout", None)
        if not lay:
            return None
        for i, (x0, y0, x1, y1) in enumerate(lay["buttons"]):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return i
        cx0, cy0, cx1, cy1 = lay["card"]
        if cx0 <= x <= cx1 and cy0 <= y <= cy1:
            return "card"
        return None

    def _solo_choose(self, i: int):
        """③のボタンで選ぶ(①のボタンと同じ処理。選んだ後は押せない)。"""
        lay = getattr(self, "_solo_layout", None)
        if not lay or lay["disabled"]:
            return
        self._on_choice(i)
        self._render_solo_choice()        # 押せない表示(灰色の文字)へ

    def _on_solo_cover_motion(self, e):
        hit = self._solo_hit(e.x, e.y) if self.bg_solo_stage == 3 else None
        self._solo_set_hover(hit if isinstance(hit, int) else None)

    def _solo_set_hover(self, i):
        """マウスを乗せたボタンの枠を明るく太く(③は塗りを変えない=Q4)。"""
        lay = getattr(self, "_solo_layout", None)
        art = getattr(self, "bg_art", None)
        cover = art.solo_cover if art is not None else None
        if not lay or cover is None:
            self._solo_hover = None
            return
        if lay["disabled"]:
            i = None
        if i == getattr(self, "_solo_hover", None):
            return
        self._solo_hover = i
        hl = self._hex(self._solo_rgb(cover, _clr.ACCENT)) \
            if ctk.get_appearance_mode() == "Light" else "#ffffff"
        try:
            for k, rid in enumerate(lay["hl"]):
                cover.itemconfigure(rid, outline=hl if k == i else "")
        except tk.TclError:
            pass

    def _set_choice_timer_text(self, text: str):
        """選択肢の残り時間の表示(①のカードと③の両方・=356)。"""
        self._apply(self.choice_timer_label, text=text)
        lay = getattr(self, "_solo_layout", None)
        art = getattr(self, "bg_art", None)
        if lay and art is not None and art.solo_cover is not None:
            try:
                art.solo_cover.itemconfigure(lay["timer"], text=text)
            except tk.TclError:
                pass

    def _hide_choice_card(self):
        self.choice_card.pack_forget()
        for b in self.choice_buttons:
            b.destroy()
        self.choice_buttons = []
        self._choice_labels = []
        self._bg_focus_refresh()          # =373
        self._choice_colors = []
        self._render_solo_choice()      # =356: ③の選択肢も消す(③のまま)

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
        show = bool(self._scenario_has_choices) and not self._play_ctrl(
            "hide_autoselect")
        # =366: シナリオが「自動選択」チェックを隠す指定なら、このシナリオの
        # 再生中は自動選択をオフとして扱う(見えないまま勝手に選ばれないよう)。
        # 視聴者がオンにしていた値は覚えておき、隠さないシナリオで戻す。
        if self._play_ctrl("hide_autoselect"):
            if self.auto_select_var.get():
                self._autoselect_suspended = True
                self.auto_select_var.set(False)
                self._cancel_autoselect()
        elif getattr(self, "_autoselect_suspended", False):
            self._autoselect_suspended = False
            self.auto_select_var.set(True)
        if show and not self.auto_select_check.winfo_manager():
            # before=transport: 先にパックしないと右端ではなく
            # 再生ボタン行の下になってしまう(操作バーが1行ぶん高くなる)
            self.auto_select_check.pack(side="right", padx=(8, 2),
                                        before=self.play_transport_row)
        elif not show and self.auto_select_check.winfo_manager():
            self.auto_select_check.pack_forget()

    def _play_ctrl(self, key: str) -> bool:
        """=366: 読み込み中のシナリオの play_controls の指定(未読込=False)。"""
        pc = getattr(getattr(self, "scenario", None), "play_controls", None)
        return bool(pc is not None and getattr(pc, key, False))

    def _sync_event_skip_visible(self):
        """=366: ◀◀/▶▶ の行をシナリオの指定で隠す/戻す(隠すと下を詰める)。"""
        row = self.event_btn_row
        hide = self._play_ctrl("hide_event_skip")
        if hide and row.winfo_manager():
            row.pack_forget()
        elif not hide and not row.winfo_manager():
            row.pack(pady=(6, 0), after=self.play_row1)

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
        self._exit_bg_solo()      # =351 Q6: 数値入力も同じ(フォーカスより先に)
        self.input_entry.focus_set()
        self._bg_focus_refresh()  # =373

    def _hide_input_card(self):
        self.input_card.pack_forget()
        self._bg_focus_refresh()  # =373

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
