"""アイテムレビュー: パターンパレット・Fキー割り当て/配置・時間補正・右クリックメニュー(mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
import warnings
from .. import appfont
from ..i18n import load_config, save_config, tr

from .common import MSG_WARN
from .fields import Tooltip
from .user_patterns import UserPatternDialog
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _ItemReviewPatternsMixin:
    """ItemReviewDialog の mixin(=301 分割)。パターンパレット・Fキー割り当て/配置・時間補正・右クリックメニュー"""

    def _on_point_btn(self):
        self._set_edit_tool("point")

    def _on_invert_btn(self):
        """インバート(仕様 5.2)。独立した ON/OFF。配置時に pos を反転。"""
        self._edit_invert = not self._edit_invert
        if self._edit_invert:
            self.edit_invert_btn.configure(fg_color=_clr.ACCENT, border_width=0,
                                           text_color=("white", "white"),
                                           hover_color=_clr.ACCENT_HOVER)
        else:
            self.edit_invert_btn.configure(fg_color="transparent",
                                           border_width=1,
                                           text_color=("gray20", "gray85"),
                                           hover_color=("gray85", "gray25"))
        self._set_edit_tool(self._edit_tool)   # 配置ツールへ反転を反映

    def _on_scale_btn(self, value: float):
        """縮尺配置(=194)。x2〜x0.5 の排他トグル。配置時の長さに掛かる。"""
        self._edit_scale = value
        self._refresh_scale_btns()
        self.edit_graph.place_scale = value
        self.edit_graph.redraw()               # ゴーストへ即反映

    def _refresh_scale_btns(self):
        for v, b in self.edit_scale_btns.items():
            on = abs(v - self._edit_scale) < 1e-9
            b.configure(fg_color=_clr.ACCENT if on else "transparent",
                        border_width=0 if on else 1,
                        text_color=("white", "white") if on
                        else ("gray20", "gray85"),
                        hover_color=_clr.ACCENT_HOVER if on
                        else ("gray85", "gray25"))

    def _user_kind(self) -> str:
        """ユーザーパターンの種別(linear / twist / rotate_ufo /
        rotate_a10cyclonesa / vibration)。編集中のトラックの種別そのもの。"""
        from .. import script_edit
        t = getattr(self, "_edit_type", "linear")
        return t if t in script_edit.USER_PAT_PREFIX else "linear"

    def _user_key(self, slot: int) -> str:
        """枠番号(1〜20) → いまの種別での枠の名前(L1 / T1 / U1 …)。"""
        from .. import script_edit
        return script_edit.user_pat_key(self._user_kind(), slot)

    def _refresh_user_pattern_btns(self):
        """枠ボタンの見た目を登録状態に合わせる(=205 / =231 種別ごと)。

        登録済み=波形アイコン+押せる/未登録=グレーの文字ボタンで
        押しても何も起きない(disabled)。**文字は種別ごとの名前**
        (L1〜L20 / T1〜T20 / U1〜U20 / A1〜A20 / V1〜V20)。
        """
        for slot, btn in self.edit_user_btns.items():
            ukey = self._user_key(slot)
            shape = self._user_patterns.get(ukey)
            if shape:
                icon = self._pattern_icon(shape)
                self._user_icons[slot] = icon
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    btn.configure(image=icon, text="", state="normal")
            else:
                self._user_icons.pop(slot, None)
                btn.configure(image=None, text=ukey, state="disabled")

    @staticmethod
    def _time_adj_clamp(v: float) -> float:
        from .. import script_edit
        step = script_edit.TIME_ADJ_STEP
        v = round(round(v / step) * step, 2)
        return max(script_edit.TIME_ADJ_MIN, min(0.0, v))

    @staticmethod
    def _time_adj_label(v: float) -> str:
        return f"{v:.2f}"

    def _load_time_adj(self) -> float:
        try:
            return self._time_adj_clamp(float(
                load_config().get(self.CFG_TIME_ADJ, 0.0)))
        except Exception:
            return 0.0

    def _time_adj_value(self) -> float:
        """入力欄の現在値(秒)。読めないときは 0。"""
        try:
            return self._time_adj_clamp(float(self.time_adj_var.get()))
        except (ValueError, TypeError):
            return 0.0

    def _time_adj_ms(self) -> float:
        return self._time_adj_value() * 1000.0

    def _time_adj_commit(self):
        """Enter / フォーカスアウト: 0.01秒へ丸め・0.00〜-1.00へクランプ。
        数値として読めない入力は直前の有効値へ戻す。"""
        try:
            v = self._time_adj_clamp(float(self.time_adj_var.get()))
        except (ValueError, TypeError):
            v = getattr(self, "_time_adj_last", 0.0)
        self._time_adj_set(v)

    def _time_adj_set(self, v: float):
        v = self._time_adj_clamp(v)
        self._time_adj_last = v
        self.time_adj_var.set(self._time_adj_label(v))
        try:
            cfg = load_config()
            if cfg.get(self.CFG_TIME_ADJ) != v:
                cfg[self.CFG_TIME_ADJ] = v
                save_config(cfg)
        except Exception:
            pass

    def _time_adj_press(self, delta: float):
        """▲▼の押下: 1回進めて、押しっぱなしなら連続増減(=242)。"""
        self._time_adj_release()
        self._time_adj_set(self._time_adj_value() + delta)
        self._time_adj_job = self.after(
            self.TIME_ADJ_REPEAT_FIRST,
            lambda: self._time_adj_repeat(delta))

    def _time_adj_repeat(self, delta: float):
        self._time_adj_set(self._time_adj_value() + delta)
        self._time_adj_job = self.after(
            self.TIME_ADJ_REPEAT_MS, lambda: self._time_adj_repeat(delta))

    def _time_adj_release(self):
        if self._time_adj_job is not None:
            try:
                self.after_cancel(self._time_adj_job)
            except Exception:
                pass
            self._time_adj_job = None

    def _on_fkey_press(self, fk: str):
        """F キーの押下。単押しは =213 のまま(今の再生位置へ1つ置く)。
        押しっぱなしのときは、置いたパターンの**終端**に再生位置が届くたび、
        その終端を起点に次を置いて数珠つなぎにする(=222)。"""
        if self._fkey_release_job is not None:
            # 自動リピートの Release が来ていた=押しっぱなしの継続
            try:
                self.after_cancel(self._fkey_release_job)
            except Exception:
                pass
            self._fkey_release_job = None
        if self._fkey_held == fk:
            return "break"              # OS の自動リピートは無視
        if self._fkey_held is not None:
            # 押しっぱなし中に別のキー=**数珠つなぎは続けたまま形だけ交代**
            # (ユーザー決定)。ここでは置かない=次の終端から新しい形になる
            if self._fkey_map.get(fk) is None:
                return "break"
            self._fkey_held = fk
            return "break"
        r = self._on_fkey(fk)
        if r == "break" and self._fkey_map.get(fk) is not None:
            self._fkey_held = fk
            # 置けた区間の終端だけを次の起点にする(失敗時は None のまま)
            rng = getattr(self.edit_model, "last_place_range", None)
            self._fkey_chain_end = rng[1] if rng else None
            self._chain_stop_job()
            if self._fkey_chain_end is not None:
                self._fkey_job = self.after(self.CHAIN_POLL_MS,
                                            self._fkey_chain_tick)
        return r

    def _fkey_chain_tick(self):
        """再生位置が直前のパターンの終端に届いたら、そこから次を置く。"""
        self._fkey_job = None
        if self._fkey_held is None or self._fkey_chain_end is None:
            return
        try:
            if not self.winfo_exists() or not self.edit_mode:
                return
        except Exception:
            return
        if self._now_ms() >= self._fkey_chain_end:
            # 起点は**素材時刻ちょうど**(時間補正は最初の1つだけに掛ける)
            self._on_fkey(self._fkey_held, at0=self._fkey_chain_end)
            # 置けなかった回は last_place_range が None=そこで連続を止める
            rng = getattr(self.edit_model, "last_place_range", None)
            if not rng or rng[1] <= self._fkey_chain_end:
                self._chain_stop()      # 進まない=無限ループ防止
                return
            self._fkey_chain_end = rng[1]
        self._fkey_job = self.after(self.CHAIN_POLL_MS, self._fkey_chain_tick)

    def _on_fkey_release(self, fk: str):
        if self._fkey_held != fk:
            return "break"
        if self._fkey_release_job is not None:
            try:
                self.after_cancel(self._fkey_release_job)
            except Exception:
                pass
        self._fkey_release_job = self.after(self.FKEY_RELEASE_MS,
                                            self._chain_stop)
        return "break"

    def _chain_stop_job(self):
        if self._fkey_job is not None:
            try:
                self.after_cancel(self._fkey_job)
            except Exception:
                pass
            self._fkey_job = None

    def _chain_stop(self):
        self._fkey_release_job = None
        self._fkey_held = None
        self._fkey_chain_end = None
        self._chain_stop_job()

    def _key_target_is_entry(self) -> bool:
        """フォーカスが文字入力欄にあるか(数字キーは入力を優先する)。"""
        try:
            w = self.focus_get()
        except Exception:
            return False
        return isinstance(w, (tk.Entry, tk.Text))

    def _on_pos_key(self, pos: int):
        """0〜9 と + キー: 今の再生位置(+時間補正)へ点を打つ(=223)。

        pos は 0/10/…/90/100(csv は 2 倍=297)。時間[at]グリッドへ吸着する。重なるパターンは
        丸ごと消して打つ(ユーザー決定=要望1と同じ「後から置くものを優先」)。
        再生は止めない。1回の打点が UNDO 1ステップ。
        """
        from .. import script_edit
        if not self.edit_mode or not self._edit_built or \
                self.edit_model is None:
            return None
        if self._key_target_is_entry():
            return None                 # at/pos 欄へ数字を入れている最中
        g = self.edit_graph
        at = script_edit.snap(max(0.0, self._now_ms() + self._time_adj_ms()),
                              g.grid_at)
        # =297: csv(分解能 200)では 0〜9/+ を 0/20/…/200(=速度 -100〜+100
        # の 20 刻み)にする(ユーザー決定)
        pos = int(round(int(pos) * self._edit_pos_max() / 100.0))
        r = self.edit_model.place_point_over(int(at), int(pos))
        if r == "ok":
            g.sel_pattern = None
            self._edit_on_change()
            self._edit_on_select()
            g.redraw()
        return "break"

    def _on_fkey(self, fk: str, at0: float | None = None):
        """F1〜F9: 割り当てたパターンを**今の再生位置**(+時間補正)へ
        左端を合わせて上書き配置する(=213)。再生は止めない。
        失敗は edit_msg へ短く出すだけ。
        **=227: 再生中でないときは「グラフ上のマウス位置」へ置く**
        (マウスがグラフの外にあるときは従来どおり再生位置)。
        at0 を渡すと**その素材時刻**を起点にする(=222 の数珠つなぎ)。"""
        from .. import script_edit
        if not self.edit_mode or not self._edit_built or \
                self.edit_model is None:
            return None
        ref = self._fkey_map.get(fk)
        if ref is None:
            return None
        if ref[0] == "user":
            ukey = self._user_key(ref[1])      # =231: 枠番号→今の種別の枠
            shape = self._user_patterns.get(ukey)
            if not shape:
                return None
            name, base = ukey, None
        else:
            try:
                name, shape = self._std_patterns()[int(ref[1])]
            except (IndexError, ValueError, TypeError):
                return None
            # =226: 離散的なスクリプトは **定義どおりの高さで固定**
            # (pos50 基準へずらすのは linear/twist のときだけ)
            base = None if self.edit_graph.pat_center is not None else 50
        if self._edit_invert:
            shape = script_edit.invert_shape(shape)
        g = self.edit_graph
        pos0 = None
        if at0 is None:
            # =227: **再生中でないときは、グラフ上のマウス位置へ置く**
            # (ユーザー要望2。マウスがグラフの外なら従来どおり再生位置)。
            mp = None if self._playing else g.mouse_place_at()
            if mp is not None:
                at0, pos0 = mp
            else:
                at0 = self._now_ms() + self._time_adj_ms()
        if pos0 is not None and base is not None:
            # linear/twist はクリックと同じく「マウスの高さ」を基準にする
            base = max(0, min(100, int(round(pos0))))
        r = self.edit_model.place_pattern_over(
            shape, at0, base, g.grid_at, g.grid_pos, name,
            scale=self._edit_scale)
        if r == "ok":
            g.sel_pattern = None
            self._edit_on_change()
            g.redraw()
        else:
            self._edit_paste_warned = True
            if r == "edge":
                msg = tr("パターンの端のすぐ近くには点を置けないため、"
                         "貼り付けできませんでした")
            else:
                msg = tr("この位置にはパターンを配置できません")
            self.edit_msg.configure(text=msg, text_color=MSG_WARN)
        return "break"

    def _palette_ref_of(self, widget):
        """ウィジェット(とその親)がパレットのどのボタンか → ref。
        ("std", i) | ("user", "U3") | None。未登録のU枠は None。"""
        w = widget
        for _ in range(6):
            if w is None:
                return None
            for i, b in enumerate(self.edit_pattern_btns):
                if w is b:
                    return ("std", i)
            for slot, b in self.edit_user_btns.items():
                if w is b:
                    return ("user", slot) \
                        if self._user_key(slot) in self._user_patterns \
                        else None
            try:
                w = w.master
            except Exception:
                return None
        return None

    def _on_palette_right_click(self, event):
        if not self._edit_built or not self.edit_mode:
            return None
        ref = self._palette_ref_of(getattr(event, "widget", None))
        if ref is None:
            return None
        self._open_fkey_menu(ref, event.x_root, event.y_root)
        return "break"

    def _open_fkey_menu(self, ref, x_root: int, y_root: int):
        """F1〜F9+「割り当て解除」のメニューを出す(=212)。"""
        from .. import script_edit
        if self._fkey_menu is not None:
            try:
                self._fkey_menu.destroy()
            except Exception:
                pass
        menu = tk.Menu(self, tearoff=0)
        cur = script_edit.fkey_of(self._fkey_map, ref)
        for fk in script_edit.FKEYS:
            other = self._fkey_map.get(fk)
            label = fk
            if fk == cur:
                label = "● " + fk
            elif other is not None:
                label = fk + "  (" + self._ref_label(other) + ")"
            menu.add_command(label=label,
                             command=lambda k=fk, r=ref: self.assign_fkey(k, r))
        menu.add_separator()
        menu.add_command(label=tr("割り当て解除"),
                         state="normal" if cur else "disabled",
                         command=lambda r=ref: self.assign_fkey(None, r))
        self._fkey_menu = menu
        try:
            menu.tk_popup(int(x_root), int(y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return menu

    def _ref_label(self, ref) -> str:
        from .. import script_edit
        if ref[0] == "user":
            return self._user_key(ref[1])      # =231: 種別ごとの名前
        try:
            return tr(self._std_patterns()[int(ref[1])][0])
        except Exception:
            return str(ref[1])

    def assign_fkey(self, fkey, ref):
        """ref へ fkey を割り当てる(fkey=None は解除)。排他=同じ ref を
        持つ他のキーは外れて移動する。config へ即保存。"""
        from .. import script_edit
        cfg = load_config()
        if fkey is None:
            cur = script_edit.fkey_of(self._fkey_map, ref)
            if cur is None:
                return
            script_edit.save_fkey_map(cfg, cur, None)
        else:
            script_edit.save_fkey_map(cfg, fkey, tuple(ref))
        save_config(cfg)
        self._fkey_map = script_edit.load_fkey_map(cfg)
        self._refresh_fkey_badges()

    def _badge_colors(self):
        dark = ctk.get_appearance_mode() != "Light"
        return (_clr.ACCENT, "white") if dark else ("#5245c9", "white")

    def _refresh_fkey_badges(self):
        """全ボタンのバッジを割り当てに合わせて更新する(=212)。"""
        from .. import script_edit
        want = {}
        for fk, ref in self._fkey_map.items():
            if ref[0] == "user" and \
                    self._user_key(ref[1]) not in self._user_patterns:
                continue          # 今の種別で未登録の枠にはバッジを出さない
            want[tuple(ref)] = fk
        for ref, lbl in list(self._fkey_badges.items()):
            if ref not in want:
                try:
                    lbl.destroy()
                except Exception:
                    pass
                del self._fkey_badges[ref]
        bg, fg = self._badge_colors()
        for ref, fk in want.items():
            btn = self.edit_pattern_btns[ref[1]] if ref[0] == "std" \
                else self.edit_user_btns.get(ref[1])
            if btn is None:
                continue
            lbl = self._fkey_badges.get(ref)
            if lbl is None or not lbl.winfo_exists():
                lbl = tk.Label(btn, text=fk, bd=0, padx=2, pady=0,
                               font=(appfont.FAMILY, 7, "bold"))
                # バッジの上のクリックもボタンとして働かせる
                lbl.bind("<Button-1>", lambda _e, r=ref:
                         self._set_edit_tool(r[1] if r[0] == "std" else r))
                self._fkey_badges[ref] = lbl
            lbl.configure(text=fk, bg=bg, fg=fg)
            lbl.place(relx=1.0, x=-1, y=1, anchor="ne")
            lbl.lift()

    def _on_user_patterns_saved(self):
        """ユーザーパターン編集ポップアップの保存後(=205)。"""
        from .. import script_edit
        self._user_patterns = script_edit.load_user_patterns(
            load_config(), self._user_kind())          # =231: 種別ごと
        self._refresh_user_pattern_btns()
        self._refresh_fkey_badges()       # =212: 画像ラベルより前面へ戻す
        # 使用中のツールが更新された枠なら、新しい形を配置ツールへ反映する
        if isinstance(self._edit_tool, tuple) and \
                self._edit_tool[0] == "user":
            self._set_edit_tool(self._edit_tool)

    def _open_user_pattern_editor(self):
        """ユーザーパターン編集ポップアップを開く(=205)。同時に1つだけ。"""
        dlg = getattr(self, "_userpat_dlg", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    # =231: 種別が変わっていたら開き直す(枠が別物のため)
                    if getattr(dlg, "kind", None) != self._user_kind():
                        dlg.destroy()
                        self._userpat_dlg = None
                    else:
                        dlg.deiconify()
                        dlg.lift()
                        dlg.focus_set()
                        return dlg
            except Exception:
                pass
        self._userpat_dlg = UserPatternDialog(self, owner=self,
                                             kind=self._user_kind())
        return self._userpat_dlg

    def _set_edit_tool(self, key):
        """[点] とパターンの排他トグル(仕様 2.4)。
        key="point" | 0〜19(標準=217) | **("user", 枠番号1〜20)**
        (=205/=218。**=231 で "U1" 等の文字列から枠番号へ変更**)。
        ※=179のコピー配置ツールは=186で廃止(Ctrl+C/Vに一本化)。"""
        from .. import script_edit
        is_user = isinstance(key, tuple) and key[:1] == ("user",)
        if is_user and self._user_key(key[1]) not in self._user_patterns:
            key = "point"                 # 未登録の枠(押せないはずの保険)
            is_user = False
        self._edit_tool = key
        is_point = key == "point"
        self.edit_point_btn.configure(
            fg_color=_clr.ACCENT if is_point else "transparent",
            border_width=0 if is_point else 1,
            text_color=("white", "white") if is_point
            else ("gray20", "gray85"),
            hover_color=_clr.ACCENT_HOVER if is_point else ("gray85", "gray25"))
        for i, btn in enumerate(self.edit_pattern_btns):
            on = (not is_point) and (not is_user) and i == key
            btn.configure(fg_color=_clr.ACCENT if on else "transparent",
                          border_width=0 if on else 1,
                          hover_color=_clr.ACCENT_HOVER if on
                          else ("gray85", "gray25"))
        for slot, btn in self.edit_user_btns.items():
            on = is_user and slot == key[1]
            btn.configure(fg_color=_clr.ACCENT if on else "transparent",
                          border_width=0 if on else 1,
                          hover_color=_clr.ACCENT_HOVER if on
                          else ("gray85", "gray25"))
        # =232: 配置ツールは**出ているグラフ全部**へ載せる(どちらの
        # グラフをクリックしても置ける。クリックした方がアクティブになる)
        if is_point:
            tool = (None, None)
        elif is_user:
            ukey = self._user_key(key[1])
            shape = self._user_patterns[ukey]
            if self._edit_invert:
                shape = script_edit.invert_shape(shape)
            tool = (shape, ukey)
        else:
            name, shape = self._std_patterns()[key]
            if self._edit_invert:
                shape = script_edit.invert_shape(shape)
            tool = (shape, name)
        for g in self.edit_graphs:
            g.set_place_tool(*tool)

    def _pattern_icon(self, shape, w: int = 40, h: int = 22,
                      step: bool | None = None):
        """点列から波形の絵を tk.PhotoImage へ描く(仕様 2.4。Pillow不使用)。

        線分を1pxずつサンプリングして打つ(=110/=111 と同じ手法)。色は
        ライト/ダーク両方で見える中間のグレー1色にする(テーマ切替で
        描き直さなくて済む)。
        **=226: 離散的なスクリプトでは斜め線ではなく直角(階段)で描く**
        (step=None なら今の編集対象から判断する)。
        """
        if step is None:
            # 組み立ての途中(グラフがまだ無い)では従来どおり斜め線
            step = bool(getattr(getattr(self, "edit_graph", None),
                                "step", False))
        img = tk.PhotoImage(width=w, height=h, master=self)
        length = max(1, shape[-1][0])
        col = "#7a7a7a"

        def xy(t, p):
            x = int(round((w - 3) * t / length)) + 1
            y = int(round((h - 3) * (100 - p) / 100.0)) + 1
            return x, y

        def line(x0, y0, x1, y1):
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for st in range(steps + 1):
                x = x0 + (x1 - x0) * st // steps
                y = y0 + (y1 - y0) * st // steps
                img.put(col, (x, y))
                if y + 1 < h:
                    img.put(col, (x, y + 1))

        for i in range(len(shape) - 1):
            x0, y0 = xy(*shape[i])
            x1, y1 = xy(*shape[i + 1])
            if step:                     # 直角(横→縦)
                line(x0, y0, x1, y0)
                line(x1, y0, x1, y1)
            else:
                line(x0, y0, x1, y1)
        return img

    def _rebuild_pattern_icons(self):
        """=226: パターンモードに合わせて標準/ユーザーのボタンの絵を作り直す
        (カタログの入れ替え+斜め線↔直角の描き分け)。"""
        if not getattr(self, "_edit_built", False):
            return
        cat = self._std_patterns()
        self._pat_icons = []
        for i, btn in enumerate(self.edit_pattern_btns):
            if i >= len(cat):
                continue
            name, shape = cat[i]
            icon = self._pattern_icon(shape)
            self._pat_icons.append(icon)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                btn.configure(image=icon)
            Tooltip(btn, lambda n=name: tr(n))
        self._refresh_user_pattern_btns()
        self._refresh_fkey_badges()

    def _edit_menu(self, event, ctx):
        """右クリックメニュー(=186で削減)。

        残す機能は グループ化 / 解除 / 削除 / 上下反転(配置済みの反転=
        唯一の手段)のみ。コピー・切り取り・貼り付けはショートカットキー
        (Ctrl+C/X/V)に一本化し、=179のコピー配置ツールは廃止。
        メニューを出す場面が無ければ何も出さない(空欄のダブル右クリック=
        再生位置セット=192 が効くように)。
        """
        model = self.edit_model
        pat = ctx.get("pattern")
        multi = bool(ctx.get("multi"))
        center = self.edit_graph.pat_center   # None=linear/twist / 中央 / 0
        # =297: rotate 系の中心は funscript=50 / csv=100。「0 でない」=rotate
        rotate = center is not None and center > 0
        menu = tk.Menu(self, tearoff=0)
        if pat is not None and not multi:
            # パターン上の右クリック(仕様 3c/6b。=241 で反転を再編)
            if center is None:
                menu.add_command(label=tr("上下反転(全幅)"),
                                 command=lambda: self._menu_flip_v(True))
                menu.add_command(
                    label=tr("上下反転(パターン内)"),
                    command=lambda: self._menu_flip_v(False))
            elif rotate:
                # rotate系: 回転方向の反転(速度は維持)=全幅のみ
                menu.add_command(label=tr("上下反転"),
                                 command=lambda: self._menu_flip_v(True))
            menu.add_command(label=tr("左右反転"),
                             command=self._menu_flip_h)
            menu.add_command(label=tr("グループ解除"),
                             command=lambda: self._menu_pat_ungroup(pat))
            menu.add_command(label=tr("削除"),
                             command=lambda: self._menu_pat_delete(pat))
        elif model.selection or model.pattern_selection:
            # =278: 選択中のパターンの構成点も数に入れる(点+パターン混在の
            # グループ化=選択中のパターンは解除して1つにまとめる)
            if len(model.group_candidate_ats()) >= 2:
                menu.add_command(label=tr("グループ化"),
                                 command=self._menu_group)
            if model.pattern_selection:
                menu.add_command(label=tr("グループ解除"),
                                 command=self._menu_ungroup_selected)
            if center is None:
                menu.add_command(label=tr("上下反転(全幅)"),
                                 command=lambda: self._menu_flip_v(True))
                menu.add_command(
                    label=tr("上下反転(選択内)"),
                    command=lambda: self._menu_flip_v(False))
            elif rotate:
                menu.add_command(label=tr("上下反転"),
                                 command=lambda: self._menu_flip_v(True))
            menu.add_command(label=tr("左右反転"),
                             command=self._menu_flip_h)
            menu.add_command(label=tr("削除"), command=self._menu_delete)
        else:
            return                          # 何も出さない(=186)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _menu_flip_v(self, full: bool):
        """=241: 上下反転。full=True は全幅(軸=中央。=297: csv は pos100)、
        False は選択全体の min〜max の中点が軸。空振りの理由を表示する。"""
        res = self.edit_model.flip_vertical(
            self._edit_pos_max() / 2.0 if full else None)
        if res == "ok":
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()
        elif res == "shared":
            self.edit_msg.configure(
                text=tr("未選択のパターンと共有している点が軸の上に"
                        "ないため、上下反転できませんでした"),
                text_color=MSG_WARN)
        elif res == "range":
            self.edit_msg.configure(
                text=tr("上下反転すると位置が0〜100に収まらないため、"
                        "反転できませんでした"),
                text_color=MSG_WARN)

    def _menu_flip_h(self):
        """=241: 左右反転(軸=選択の時間範囲の中心)。理由を表示する。"""
        res = self.edit_model.flip_horizontal()
        if res == "ok":
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()
        elif res == "inside":
            self.edit_msg.configure(
                text=tr("選択範囲の中に選択していない点やパターンが"
                        "あるため、左右反転できませんでした"),
                text_color=MSG_WARN)
        elif res == "ends":
            self.edit_msg.configure(
                text=tr("未選択のパターンと接しているため、両端の位置"
                        "(pos)が同じときだけ左右反転できます"),
                text_color=MSG_WARN)

    def _menu_pat_ungroup(self, idx: int):
        """解除=普通の点の集まりに戻す(仕様 6b)。"""
        if self.edit_model.ungroup_pattern(idx):
            self.edit_graph.sel_pattern = None
            self.edit_model.pattern_selection = set()
            self._edit_on_change()
            self.edit_graph.redraw()

    def _menu_ungroup_selected(self):
        """=278: 選択中のパターンをまとめて解除(構成点は選択に残る)。"""
        if self.edit_model.ungroup_selected():
            self.edit_graph.sel_pattern = None
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()

    def _menu_pat_delete(self, idx: int):
        if self.edit_model.delete_pattern(idx):
            self.edit_graph.sel_pattern = None
            self.edit_model.pattern_selection = set()
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()

    def _menu_group(self):
        """グループ化(仕様 5.4)。拒否の理由は edit_msg へ表示する。"""
        res = self.edit_model.group_selection()
        if res == "ok":
            self.edit_graph.sel_pattern = len(self.edit_model.patterns) - 1
            self.edit_model.pattern_selection = {
                self.edit_graph.sel_pattern}
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()
        elif res == "has_pattern":
            self.edit_msg.configure(
                text=tr("パターンが含まれています。先に解除してください"),
                text_color=MSG_WARN)
        elif res == "unselected_in_range":
            self.edit_msg.configure(
                text=tr("範囲内に選択していない点があるため、"
                        "グループ化できません"),
                text_color=MSG_WARN)

    def _menu_delete(self):
        """メニューの「削除」。確認なしで直接削除する(=171 実機FB2。
        Delete キーと同じ挙動。取り消しは Ctrl+Z)。=241: 選択に
        パターンが含まれていてもまとめて消す(delete_selected_any)。"""
        if self.edit_model.delete_selected_any():
            self.edit_graph.sel_pattern = None
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()
