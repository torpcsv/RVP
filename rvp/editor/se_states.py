"""シナリオ編集: ステート形式(ステート一覧・移行条件・移行先・ステート操作・通常形式への変換)(mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import json
import os
import shutil
import tkinter as tk
from ..scenario import auto_bind_tracks
from ..i18n import tr

from .common import (CHANNEL_IDS, CTkOptionMenu, DEFAULT_STATE,
    INFINITE_CHOICE, TEXT_MUTED, _num_disp, _parse_num_text, _raw_has_video)
from .fields import CondListEditor, VarRefField
from .paths import _map_item_paths, _safe_relpath, _same_file_content
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _ScenarioEditorStatesMixin:
    """ScenarioEditor の mixin(=301 分割)。ステート形式(ステート一覧・移行条件・移行先・ステート操作・通常形式への変換)"""

    def _update_trans_summary(self):
        """図の下に移行条件の要約テキストを表示する。"""
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev:
            self.trans_summary_label.configure(text="")
            return
        lines = []
        for sid, st in ev["states"].items():
            t = st.get("transition")
            if not t:
                lines.append(tr('{0}: ステート移行なし(イベント終了まで)').format(sid))
                continue
            when = t.get("when", {})
            ch = when.get("channel", "?")
            if when.get("type") == "choice":
                # =275: 選択肢(1)A 2)B) で → S2/イベント:X
                ents = [e for e in (t.get("choice") or [])
                        if isinstance(e, dict)]
                items = " ".join("{0}){1}".format(i + 1, e.get("label", ""))
                                 for i, e in enumerate(ents))
                dests = []
                for e in ents:
                    to = e.get("to")
                    d = (tr("イベント:{0}").format(to.get("event"))
                         if isinstance(to, dict) else str(to))
                    if d not in dests:
                        dests.append(d)
                lines.append(tr('{0}: {1} で → {2}').format(
                    sid, tr('選択肢({0})').format(items), "/".join(dests)))
                continue
            if when.get("type") == "cond":
                parts = []
                for c in when.get("conds") or []:
                    rhs = c.get("value")
                    if isinstance(rhs, dict):
                        rhs = tr("変数{0}").format(rhs.get("var"))
                    parts.append("{0} {1} {2}".format(
                        c.get("var"), c.get("op"), rhs))
                cond = tr('判定式({0})').format(tr("、").join(parts))
            elif when.get("type") == "all_channels":
                cond = tr('全チャンネル終了')
            elif when.get("type") == "channel_end":
                cond = tr('{0}ch終了').format(ch)
            elif when.get("type") == "channel_count":
                lo = _num_disp(when.get("count", when.get("min", "?")))
                hi = _num_disp(when.get("count", when.get("max",
                               when.get("min", "?"))))
                cond = tr('{0}chの{1}回').format(ch, lo) if lo == hi else tr('{0}chの{1}〜{2}回').format(ch, lo, hi)
            elif when.get("type") == "state_time":
                # =62: 経過時間(このステートに入ってからの秒数)
                lo = _num_disp(when.get("seconds", when.get("min_seconds", "?")))
                hi = _num_disp(when.get("seconds", when.get("max_seconds",
                               when.get("min_seconds", "?"))))
                cond = tr('経過{0}秒').format(lo) if lo == hi \
                    else tr('経過{0}〜{1}秒').format(lo, hi)
            else:
                lo = _num_disp(when.get("seconds", when.get("min_seconds", "?")))
                hi = _num_disp(when.get("seconds", when.get("max_seconds",
                               when.get("min_seconds", "?"))))
                cond = tr('{0}chの合計{1}秒').format(ch, lo) if lo == hi else tr('{0}chの合計{1}〜{2}秒').format(ch, lo, hi)
            to = t.get("to")
            if isinstance(to, str):
                dest = to
            else:
                # =73: 候補は文字列 or {"to","weight"}。重み・elseも要約に出す
                parts = []
                for ent in (to.get("random", []) if isinstance(to, dict) else []):
                    if isinstance(ent, str):
                        parts.append(ent)
                    elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                        w = ent.get("weight", 1.0)
                        if isinstance(w, dict):
                            w = tr("変数{0}").format(w.get("var"))
                        else:
                            w = _num_disp(w)
                        parts.append(tr("{0}(重み{1})").format(ent["to"], w))
                dest = "(" + "/".join(parts) + tr(" から抽選)")
                if isinstance(to, dict) and to.get("visited") == "exclude":
                    # =77: 未実行優先と全消化時の挙動も要約に出す
                    ex = to.get("when_exhausted", "all")
                    if ex == "reset":
                        ex_txt = tr("消化でリセット")
                    elif isinstance(ex, dict):
                        ex_txt = tr("消化で{0}へ").format(ex.get("to"))
                    else:
                        ex_txt = tr("消化で全候補")
                    dest += tr("(未実行優先・{0})").format(ex_txt)
                if isinstance(to, dict) and isinstance(to.get("else"), str):
                    dest += tr("(重み全0→{0})").format(to["else"])
            lines.append(tr('{0}: {1} で → {2}').format(sid, cond, dest))
        self.trans_summary_label.configure(text="\n".join(lines))

    def _edit_state_ops(self):
        def on_ok(result):
            self.set_state_ops(result)
        self.open_ops_dialog(
            tr("ステート {0}").format(self.sel_state),
            [(tr("ステート開始時・再入ごと(on_start)"), "on_start",
              self._st_ops),
             (tr("ステート終了時(on_end)"), "on_end", self._st_end_ops)], on_ok)

    def _refresh_state_ops_btn(self):
        self.state_ops_btn.configure(text=tr("開始{0}・終了{1}").format(
            len(self._st_ops), len(self._st_end_ops)))

    def set_state_ops(self, result: dict):
        self._st_ops = result.get("on_start") or []
        self._st_end_ops = result.get("on_end") or []
        self._refresh_state_ops_btn()

    def _show_states_ui(self, show: bool):
        if show:
            self.states_hint.pack_forget()
            self.states_inner.pack(fill="x", pady=(6, 0))
        else:
            self.states_inner.pack_forget()
            self.states_hint.pack(fill="x", pady=(8, 0))

    def _select_state(self, state_id: str):
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev or state_id not in ev["states"]:
            return
        # =100①: 入力途中のステート名を先にコミットする(ステート図の○は
        # クリックしてもフォーカスが移らず FocusOut が発火しないため)
        self._commit_state_rename()
        if state_id not in ev["states"]:
            return   # クリックした○が直前のリネームで旧名になった(=自分自身)
        if self.sel_state and self.sel_state != state_id \
                and self.sel_state in ev["states"]:
            err = self._apply_state_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        self._load_state_panel(state_id)

    def _rebuild_target_checks(self, ev):
        for cb in self.trans_target_checks:
            cb.master.destroy()   # =73: セル(チェック+重み欄)ごと破棄
        self.trans_target_checks = []
        self.trans_target_vars = {}
        self.trans_weight_fields = {}
        nums = self._numeric_var_names()
        # =287: 横一列だとステートが増えたとき画面外に出て選べない(ユーザー
        # 報告)。TRANS_TARGET_COLS 列で折り返す(grid)。
        for i, sid in enumerate(ev["states"]):
            cell = ctk.CTkFrame(self.trans_targets_frame,
                                fg_color="transparent")
            cell.grid(row=i // self.TRANS_TARGET_COLS,
                      column=i % self.TRANS_TARGET_COLS,
                      sticky="w", padx=(0, 8), pady=1)
            var = tk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(
                cell, text=sid, variable=var,
                font=ctk.CTkFont(size=12), checkbox_width=16, checkbox_height=16,
                width=20, fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER)
            cb.pack(side="left")
            # =73: 重み欄(定数 or 変数参照)。数値変数が無ければ素の数値欄
            wfield = VarRefField(cell, width=48, placeholder="1")
            wfield.set_names(nums)
            wfield.set("1")
            wfield.pack(side="left", padx=(4, 0))
            self.trans_target_vars[sid] = var
            self.trans_target_checks.append(cb)
            self.trans_weight_fields[sid] = wfield
        # =73: else(重み全0時の移行先)メニューの候補を作り直す
        self.trans_else_menu.configure(
            values=[self.TRANS_ELSE_END] + list(ev["states"].keys()))
        self.trans_else_var.set(self.TRANS_ELSE_END)
        # =77: 分岐オプションも初期状態へ(「指定ステートへ」の候補を作り直す)
        sids = list(ev["states"].keys())
        self.trans_exh_to_menu.configure(values=sids or [""])
        self.trans_exh_to_var.set("")
        self.trans_visited_var.set(tr("毎回すべて候補"))
        self.trans_exhausted_var.set(tr("以後、毎回すべて候補"))
        # =125: 移行先の決め方も初期化(変数宣言がある時だけ「変数分岐」)
        # =167: 「固定」は他ステートが居るときだけ出す(自分自身へは移行しない)
        others = [s for s in sids if s != self.sel_state]
        vals = ([self.TRANS_TOMODE_FIXED] if others else []) \
            + [self.TRANS_TOMODE_PICK]
        if self._has_vars():
            vals.append(self.TRANS_TOMODE_COND)
        self.trans_tomode_menu.configure(values=vals)
        self.trans_tomode_var.set(vals[0])
        self.trans_fixed_menu.configure(values=others or [""])
        self.trans_fixed_var.set(others[0] if others else "")
        self._clear_strans_rows()
        self.strans_else_menu.configure(
            values=[self.STRANS_ELSE_NONE] + sids)
        self.strans_else_var.set(self.STRANS_ELSE_NONE)
        self._update_trans_visited_ui()

    def _update_trans_visited_ui(self):
        """=77: 「未実行ステートのみ候補」の相関表示(=26の_update_next_uiと同じ)。

        「全候補が実行済みのとき:」の行(trans_exh_row)は exclude 選択時のみ
        else行の上へ pack し、「指定ステートへ」のときだけ行き先メニューを出す。
        """
        exclude = self.trans_visited_var.get() == tr("未実行ステートのみ候補")
        if exclude:
            if not self.trans_exh_row.winfo_ismapped():
                self.trans_exh_row.pack(fill="x", pady=(0, 4),
                                        before=self.trans_else_row)
            self.trans_exh_label.pack(side="left")
            self.trans_exh_menu.pack(side="left", padx=(6, 0))
            if self.trans_exhausted_var.get() == tr("指定ステートへ"):
                self.trans_exh_to_menu.pack(side="left", padx=(6, 0))
            else:
                self.trans_exh_to_menu.pack_forget()
        else:
            self.trans_exh_row.pack_forget()
            self.trans_exh_label.pack_forget()
            self.trans_exh_menu.pack_forget()
            self.trans_exh_to_menu.pack_forget()

    def _trans_load_targets(self, t: dict):
        """transition raw の to/else を移行先UI(チェック・重み・else)へ反映する。

        =73: 候補は文字列(重み1)または {"to","weight"}。呼び出し前に
        _rebuild_target_checks 済み(チェックOFF・重み1・else終了が初期状態)。
        """
        to = t.get("to")
        # =125: 判定式(cond)形式なら「変数分岐」モードへ切り替えて行を読む
        if isinstance(to, dict) and isinstance(to.get("cond"), list):
            self.trans_tomode_var.set(self.TRANS_TOMODE_COND)
            self._clear_strans_rows()
            for row in to["cond"]:
                if isinstance(row, dict):
                    self._add_strans_row(row)
            if not self.strans_rows:
                self._add_strans_row()
            sids = self._state_id_choices()
            self.strans_else_menu.configure(
                values=[self.STRANS_ELSE_NONE] + sids)
            et = to.get("else")
            self.strans_else_var.set(
                et if et in sids else self.STRANS_ELSE_NONE)
            self._update_trans_tomode_ui()
            return
        checked: dict[str, object] = {}
        else_to = None
        if isinstance(to, str):
            # =167: 移行先が1つ=「固定」として開く(「分岐」で1つ
            # だけ選んだ場合と保存形式が同じなので区別できない)
            if to in self.trans_fixed_menu.cget("values"):
                self.trans_tomode_var.set(self.TRANS_TOMODE_FIXED)
                self.trans_fixed_var.set(to)
                self._update_trans_tomode_ui()
                return
            checked[to] = 1.0
        elif isinstance(to, dict):
            for ent in to.get("random") or []:
                if isinstance(ent, str):
                    checked[ent] = 1.0
                elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                    checked[ent["to"]] = ent.get("weight", 1.0)
            if isinstance(to.get("else"), str):
                else_to = to["else"]
        # ここまで来たら「分岐」(または不整合な文字列)
        if self.trans_tomode_var.get() == self.TRANS_TOMODE_FIXED:
            self.trans_tomode_var.set(self.TRANS_TOMODE_PICK)
        for sid, var in self.trans_target_vars.items():
            var.set(sid in checked)
            if sid in checked:
                self.trans_weight_fields[sid].set(checked[sid])
        if else_to in self.trans_target_vars:
            self.trans_else_var.set(else_to)
        # =77: visited / when_exhausted の反映(文法は=26のnextと同じ)
        if isinstance(to, dict):
            self.trans_visited_var.set(
                tr("未実行ステートのみ候補") if to.get("visited") == "exclude"
                else tr("毎回すべて候補"))
            ex = to.get("when_exhausted", "all")
            if ex == "reset":
                self.trans_exhausted_var.set(
                    tr("リセットして再び、未実行ステートのみ候補"))
            elif isinstance(ex, dict) and isinstance(ex.get("to"), str):
                self.trans_exhausted_var.set(tr("指定ステートへ"))
                if ex["to"] in self.trans_target_vars:
                    self.trans_exh_to_var.set(ex["to"])
            else:
                self.trans_exhausted_var.set(tr("以後、毎回すべて候補"))
            self._update_trans_visited_ui()

    def _collect_trans_to(self, where: str, ev) -> tuple:
        """移行先UIから transition の to 値を組み立てる(=73)。

        戻り値: (エラーメッセージ, to値)。重みは定数 or {"var":..}、
        空欄=1、0/負も許容(=出さない候補)。従来形式との後方互換:
        1候補・重み1・else無し=文字列 / 全候補重み1・else無し=
        {"random": [ID...]}(旧来の均等抽選と同じ書式)で保存する。
        """
        # =125: 「変数分岐」モードは判定式(cond)形式で保存する
        if self.trans_tomode_var.get() == self.TRANS_TOMODE_COND:
            return self._collect_strans_cond(where, ev)
        # =167: 「固定」モードは移行先1つ=文字列で保存する
        if self.trans_tomode_var.get() == self.TRANS_TOMODE_FIXED:
            to = self.trans_fixed_var.get()
            if to not in ev["states"] or to == self.sel_state:
                self._want_mark(self.trans_fixed_menu, "error")
                return tr("{0}: 移行先を選択してください").format(where), None
            return None, to
        checked = []   # (sid, 重み: 数値 or {"var":..})
        for sid, var in self.trans_target_vars.items():
            if not var.get() or sid not in ev["states"]:
                continue
            raw = self.trans_weight_fields[sid].get_raw()
            if isinstance(raw, str):
                txt = raw.strip()
                if txt == "":
                    w = 1.0
                else:
                    try:
                        w = _parse_num_text(txt)   # 0/負も許容(=出さない候補)
                    except ValueError:
                        self._want_mark(self.trans_weight_fields[sid], "error")
                        return tr('{0}: 移行先 {1} の重みが不正です').format(where, sid), None
            else:
                w = raw   # {"var": 変数名}
            checked.append((sid, w))
        if not checked:
            for cb in self.trans_target_checks:
                self._want_mark(cb, "error")
            return tr('{0}: 移行先を1つ以上チェックしてください').format(where), None
        else_choice = self.trans_else_var.get()
        else_to = (else_choice if else_choice != self.TRANS_ELSE_END
                   and else_choice in ev["states"] else None)
        # =77: 未実行優先。exclude時は後方互換の簡易書式を使わない
        exclude = (self.trans_visited_var.get()
                   == tr("未実行ステートのみ候補"))
        all_one = all(w == 1.0 for _s, w in checked)
        if all_one and else_to is None and not exclude:
            if len(checked) == 1:
                return None, checked[0][0]
            return None, {"random": [sid for sid, _w in checked]}
        value = {"random": [
            sid if w == 1.0 else {"to": sid, "weight": w}
            for sid, w in checked]}
        if exclude:
            value["visited"] = "exclude"
            exh = self.trans_exhausted_var.get()
            if exh == tr("リセットして再び、未実行ステートのみ候補"):
                value["when_exhausted"] = "reset"
            elif exh == tr("指定ステートへ"):
                to = self.trans_exh_to_var.get()
                if to not in ev["states"]:
                    return tr('{0}: 「指定ステートへ」の行き先を選択してください').format(where), None
                value["when_exhausted"] = {"to": to}
        if else_to is not None:
            value["else"] = else_to
        return None, value

    def _state_id_choices(self) -> list[str]:
        ev = self.data["events"].get(self.selected or "")
        if isinstance(ev, dict) and isinstance(ev.get("states"), dict):
            return list(ev["states"].keys())
        return []

    def _add_strans_row(self, raw: dict | None = None):
        """=125: 移行先判定式の条件行(イベントの _add_cond_row と同型)。"""
        raw = raw or {}
        row = ctk.CTkFrame(self.strans_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=2)
        num = ctk.CTkLabel(row, text="", width=22,
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        num.pack(side="left", anchor="n", pady=4)
        conds = CondListEditor(row)
        conds.set_names(self._var_names())
        conds.load(raw.get("when") or [])
        conds.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", anchor="n",
                                                 pady=4)
        ids = self._state_id_choices() or [""]
        to_var = tk.StringVar(
            value=raw.get("to") if raw.get("to") in ids else ids[0])
        CTkOptionMenu(row, variable=to_var, width=120, height=26,
                      values=ids,
                      fg_color=("gray75", "gray28"),
                      button_color=("gray70", "gray33")
                      ).pack(side="left", padx=6, anchor="n", pady=2)
        entry = {"frame": row, "num": num, "conds": conds, "to_var": to_var}
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_strans_row(e)
                      ).pack(side="left", padx=(4, 0), anchor="n", pady=2)
        self.strans_rows.append(entry)
        self._renumber_strans_rows()

    def _delete_strans_row(self, entry):
        if len(self.strans_rows) <= 1:
            self._report("warn", tr("削除できません"),
                         tr("条件行は最低1つ必要です"))
            return
        self.strans_rows.remove(entry)
        entry["frame"].destroy()
        self._renumber_strans_rows()

    def _renumber_strans_rows(self):
        for i, e in enumerate(self.strans_rows):
            e["num"].configure(text=f"{i + 1}.")

    def _clear_strans_rows(self):
        for e in self.strans_rows:
            e["frame"].destroy()
        self.strans_rows = []

    def _collect_strans_cond(self, where: str, ev) -> tuple:
        """=125: 変数分岐UIから transition の to(cond形式)を組み立てる。

        書式はイベント next の cond と同じ。**else は省略可**で、
        「(移行しない)」選択時は else キー自体を書かない
        (=どの行も成立しなければ移行しない。2026-08-13ユーザー決定)。
        """
        string_vars = self._string_var_names()
        rows = []
        for i, e in enumerate(self.strans_rows):
            w = tr("{0} 条件行{1}").format(where, i + 1)
            err, conds = e["conds"].collect(w, string_vars,
                                            mark=self._want_mark)
            if err:
                return err, None
            to = e["to_var"].get()
            if to not in ev["states"]:
                return tr("{0}: 移行先が不正です").format(w), None
            rows.append({"when": conds, "to": to})
        if not rows:
            return tr("{0}: 条件行を1つ以上指定してください").format(where), None
        value = {"cond": rows}
        else_choice = self.strans_else_var.get()
        if else_choice != self.STRANS_ELSE_NONE                 and else_choice in ev["states"]:
            value["else"] = else_choice
        return None, value

    def _update_trans_tomode_ui(self):
        """=125: 移行先の決め方(抽選/変数分岐)の表示切替。

        「変数分岐」×移行あり のときだけ cond箱を出し、抽選系の
        チェック・分岐オプション・重みelse行を隠す。「移行なし」時は
        従来どおり抽選レイアウト(無効化)で表示する。
        """
        active = self.trans_type_var.get() != self.TRANS_NONE
        if self.trans_type_var.get() == self.TRANS_CHOICE:
            # =275: 選択肢モードでは移行先の決め方UI一式を出さない
            self.strans_cond_box.pack_forget()
            self.trans_targets_frame.pack_forget()
            self.trans_visited_row.pack_forget()
            self.trans_exh_row.pack_forget()
            self.trans_else_row.pack_forget()
            self.trans_fixed_menu.pack_forget()
            self._update_state_choice_correlation()
            return
        self._update_state_choice_correlation()
        cond_mode = (self.trans_tomode_var.get() == self.TRANS_TOMODE_COND
                     and active)
        # =167: 「固定」は「移行なし」でもレイアウトを保つ(無効化して見せる)。
        # コンボが「固定」なのにチェック欄が出ていると分かりにくいため。
        fixed_mode = self.trans_tomode_var.get() == self.TRANS_TOMODE_FIXED
        if fixed_mode:
            # =167: 移行先が1つ。チェック・分岐オプション・else行は出さない
            self.strans_cond_box.pack_forget()
            self.trans_to_label.configure(text=tr("ステート移行先:"))
            self.trans_targets_frame.pack_forget()
            self.trans_visited_row.pack_forget()
            self.trans_exh_row.pack_forget()
            self.trans_else_row.pack_forget()
            if not self.trans_fixed_menu.winfo_ismapped():
                self.trans_fixed_menu.pack(side="left", padx=(6, 0))
            return
        self.trans_fixed_menu.pack_forget()
        if cond_mode:
            self.trans_to_label.configure(text=tr("ステート移行先(判定式):"))
            self.trans_targets_frame.pack_forget()
            self.trans_visited_row.pack_forget()
            self.trans_exh_row.pack_forget()
            self.trans_else_row.pack_forget()
            if not self.strans_rows:
                self._add_strans_row()
            if not self.strans_cond_box.winfo_ismapped():
                self.strans_cond_box.pack(fill="x", pady=(0, 2),
                                          after=self.trans_to_row)
        else:
            self.strans_cond_box.pack_forget()
            self.trans_to_label.configure(
                text=tr("ステート移行先(複数チェックで抽選):"))
            if not self.trans_targets_frame.winfo_ismapped():
                self.trans_targets_frame.pack(side="left", padx=8)
            if not self.trans_visited_row.winfo_ismapped():
                self.trans_visited_row.pack(fill="x", pady=(0, 2),
                                            after=self.trans_to_row)
            if not self.trans_else_row.winfo_ismapped():
                self.trans_else_row.pack(fill="x", pady=(0, 2),
                                         after=self.trans_visited_row)
            self._update_trans_visited_ui()

    def _refresh_ev_end_state_checks(self, ev, rename=None):
        """=285: ステートの追加/削除/コピー/リネーム後にチェック一覧を作り直す。

        従来はイベントパネルの読み込み時にしか作らず、ステートを増やしたり
        名前を変えても「S1」しか選べなかった(ユーザー報告)。チェック状態は
        引き継ぎ、rename=(old,new) なら名前を付け替える。
        """
        checked = {sid for sid, v in self.ev_end_state_vars.items() if v.get()}
        if rename:
            old, new = rename
            checked = {new if x == old else x for x in checked}
        self._rebuild_ev_end_state_checks(ev)
        for sid in checked:
            if sid in self.ev_end_state_vars:
                self.ev_end_state_vars[sid].set(True)

    def _rebuild_ev_end_state_checks(self, ev):
        """イベント終了「指定ステートが終了」の対象ステートチェックを再構築する。"""
        for cb in self.ev_end_state_checks:
            cb.destroy()
        self.ev_end_state_checks = []
        self.ev_end_state_vars = {}
        for sid in ev.get("states", {}):
            var = tk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(
                self.ev_end_states_inner, text=sid, variable=var,
                font=ctk.CTkFont(size=12), checkbox_width=16, checkbox_height=16,
                width=60, fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER)
            cb.pack(side="left", padx=4)
            self.ev_end_state_vars[sid] = var
            self.ev_end_state_checks.append(cb)

    def _load_state_panel(self, state_id: str):
        _r = self._begin_render()
        try:
            self._load_state_panel_body(state_id)
        finally:
            self._end_render(_r)
        self._hist_normalize(lambda: self._apply_state_panel_body())   # =277
        self._hist_check()   # =277

    def _load_state_panel_body(self, state_id: str):
        ev = self.data["events"][self.selected]
        st = ev["states"][state_id]
        self.sel_state = state_id
        self.state_id_var.set(state_id)
        self.state_start_var.set(ev.get("start") == state_id)

        channels = st.get("channels", {})
        self._set_device_vars(st.get("device"), channels,
                              has_video=_raw_has_video(channels))
        for ch_id, sec in self.channel_sections.items():
            sec.load(channels.get(ch_id), state_mode=True)
        self.seek_var.set(st.get("seek_channel")
                          or self._auto_seek_channel(channels))
        self._load_bgm(st.get("bgm"))   # =256

        # ステート開始時の変数操作(変数宣言があるときだけ表示)
        self._st_ops = list(st.get("on_start") or [])
        self._st_end_ops = list(st.get("on_end") or [])
        if self._has_vars():
            self._refresh_state_ops_btn()
            self.state_ops_btn.pack(side="left", padx=8)
        else:
            self.state_ops_btn.pack_forget()

        # 移行条件
        self._rebuild_target_checks(ev)
        self.state_choice.set_state_ids(list(ev["states"]))   # =275
        self.state_choice.load(None)   # =275: 他形式から切り替えた時の初期状態
        nums = self._numeric_var_names()
        self.trans_min_field.set_names(nums)
        self.trans_max_field.set_names(nums)
        # 「判定式でステート移行」は変数宣言がある時だけ選択肢に出す(相関制御)
        trans_choices = list(self.TRANS_CHOICES)
        if self._has_vars():
            trans_choices.append(self.TRANS_COND)
        self.trans_type_menu.configure(values=trans_choices)
        t = st.get("transition")
        if not t:
            self.trans_type_var.set(tr("ステート移行なし"))
            self.trans_min_field.set("")
            self.trans_max_field.set("")
        elif t.get("when", {}).get("type") == "cond":
            self.trans_type_var.set(self.TRANS_COND)
            self.trans_cond.set_names(self._var_names())
            self.trans_cond.load(t.get("when", {}).get("conds") or [])
            self._trans_load_targets(t)
        elif t.get("when", {}).get("type") == "all_channels":
            self.trans_type_var.set(self.TRANS_ALL_CH)
            self._trans_load_targets(t)
        elif t.get("when", {}).get("type") == "choice":
            # =275: 選択肢でステート移行
            self.trans_type_var.set(self.TRANS_CHOICE)
            self.state_choice.set_state_ids(list(ev["states"]))
            self.state_choice.load(t)
        elif t.get("when", {}).get("type") == "state_time":
            # =62: 経過時間(このステートに入ってからの秒数)。対象chは無し
            when = t.get("when", {})
            self.trans_type_var.set(self.TRANS_STATE_TIME)

            def _load_sec(v):
                if isinstance(v, dict):
                    return v
                return f"{float(v):g}"

            if "seconds" in when:
                lo = hi = when["seconds"]
            else:
                lo = when.get("min_seconds", 0)
                hi = when.get("max_seconds", lo)
            self.trans_min_field.set(_load_sec(lo))
            self.trans_max_field.set(_load_sec(hi))
            self._trans_load_targets(t)
        elif t.get("when", {}).get("type") == "channel_end":
            self.trans_type_var.set(self.TRANS_CHANNEL_END)
            self.trans_ch_var.set(t.get("when", {}).get("channel", "C"))
            self._trans_load_targets(t)
        else:
            when = t.get("when", {})

            def _load_bound(v, count_mode):
                if isinstance(v, dict):
                    return v
                return str(int(v)) if count_mode else f"{float(v):g}"

            if when.get("type") == "channel_count":
                self.trans_type_var.set(self.TRANS_CHANNEL_COUNT)
                lo = when.get("count", when.get("min", 1))
                hi = when.get("count", when.get("max", lo))
                self.trans_min_field.set(_load_bound(lo, True))
                self.trans_max_field.set(_load_bound(hi, True))
            else:
                # =165: 廃止した channel_time は「経過時間」へ読み替えて開く。
                # 秒数と移行先を捨てずに直せるようにするため(そのまま保存し直せば
                # state_time になる)。読み替えたことは画面に出す(黙って変えない)。
                self.trans_type_var.set(self.TRANS_STATE_TIME)
                if "seconds" in when:
                    lo = hi = when["seconds"]
                else:
                    lo = when.get("min_seconds", 0)
                    hi = when.get("max_seconds", lo)
                self.trans_min_field.set(_load_bound(lo, False))
                self.trans_max_field.set(_load_bound(hi, False))
                if when.get("type") == "channel_time":
                    self._report(
                        "warn", tr("移行条件を読み替えました"),
                        tr("ステート {0}: 廃止した「チャンネル時間でステート移行」を"
                           "「経過時間でステート移行」として開きました。"
                           "保存し直すと新しい形式になります。").format(state_id))
            self.trans_ch_var.set(when.get("channel", "C"))
            self._trans_load_targets(t)
        self.trans_type_menu.configure(state="normal")
        self._update_trans_ui()
        self._update_noaudio_correlation()
        self._update_video_correlation()
        self._redraw_state_canvas()
        self._update_trans_summary()

    def _apply_state_panel(self, lenient_channel: str | None = None) -> str | None:
        """=277: 成功したら履歴チェックポイント(本体は _apply_state_panel_body)。"""
        err = self._apply_state_panel_body(lenient_channel)
        if err is None:
            self._hist_check()
        return err

    def _apply_state_panel_body(self, lenient_channel: str | None = None) -> str | None:
        """パネルの内容を選択中ステートへ書き戻す。エラーメッセージ or None。"""
        self._pending_marks = []
        ev = self.data["events"].get(self.selected or "")
        sid = self.sel_state
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return None
        where = tr('イベント {0} ステート {1}').format(self.selected, sid)

        # 検証
        # 有効チャンネル0=音声なしステート(即時通過ノード)として保存できる
        enabled = [c for c, s in self.channel_sections.items()
                   if s.enabled_var.get()]
        for ch_id, sec in self.channel_sections.items():
            if ch_id == lenient_channel:
                continue   # =129: コピーで丸ごと置換される対象chは検証免除
            err = sec.validate()
            if err:
                return f"{where}: {err}"
        # 音声なし(有効ch0)は device 自体を保存しないため検証をスキップ
        for ttype, var in (self.device_vars.items() if enabled else ()):
            ch = var.get()
            if ch != tr("なし") and ch not in enabled:
                self._want_mark(self.device_menus.get(ttype), "error")
                return (tr('{0}: デバイス担当 {1}→{2} は無効なチャンネルを指しています').format(where, ttype, ch))

        transition = None
        ttype_choice = self.trans_type_var.get()
        if ttype_choice == self.TRANS_CHOICE:
            # =275: 選択肢でステート移行(行き先はステート or イベント)
            self.state_choice.set_state_ids(list(ev["states"]))
            err, transition = self.state_choice.collect(where)
            if err:
                return err
        elif ttype_choice == self.TRANS_COND:
            err, conds = self.trans_cond.collect(
                tr('{0} 移行条件').format(where), self._string_var_names(),
                mark=self._want_mark)
            if err:
                return err
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err
            transition = {"when": {"type": "cond", "conds": conds}, "to": to}
        elif ttype_choice == self.TRANS_ALL_CH:
            # 全チャンネル終了で移行: 有効チャンネルは全て有限(無限以外)が必要
            for cid in enabled:
                if self.channel_sections[cid].end_var.get() == INFINITE_CHOICE:
                    self._want_mark(self.channel_sections[cid].end_menu, "error")
                    return tr('{0}: 全チャンネル終了で移行するには、全チャンネルに有限の終了条件(無限以外)を設定してください').format(where)
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err
            transition = {"when": {"type": "all_channels"}, "to": to}
        elif ttype_choice == self.TRANS_CHANNEL_END:
            # 指定チャンネル終了で移行: 対象chは有効かつ有限(無限以外)が必要
            ch = self.trans_ch_var.get()
            if ch not in enabled:
                self._want_mark(self.trans_ch_menu, "error")
                return tr('{0}: 移行条件の対象チャンネル {1} が有効ではありません').format(where, ch)
            tsec = self.channel_sections[ch]
            if tsec.end_var.get() == INFINITE_CHOICE:
                self._want_mark(tsec.end_menu, "error")
                return tr('{0}: 指定チャンネル終了で移行するには、対象チャンネル {1} に有限の終了条件(無限以外)を設定してください').format(where, ch)
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err
            transition = {"when": {"type": "channel_end", "channel": ch}, "to": to}
        elif ttype_choice != self.TRANS_NONE:
            # =62: 経過時間はチャンネルを見ない(音声なしステートでも使える)
            state_time = (ttype_choice == self.TRANS_STATE_TIME)
            ch = self.trans_ch_var.get()
            if not state_time and ch not in enabled:
                self._want_mark(self.trans_ch_menu, "error")
                return tr('{0}: 移行条件の対象チャンネル {1} が有効ではありません').format(where, ch)
            count_mode = (ttype_choice == self.TRANS_CHANNEL_COUNT)

            lo_var = self.trans_min_field.use_var
            hi_var = self.trans_max_field.use_var
            try:
                if lo_var:
                    lo = self.trans_min_field.get_raw()
                else:
                    lo = float(self.trans_min_field.get_text())
                if hi_var:
                    hi = self.trans_max_field.get_raw()
                elif self.trans_max_field.get_text().strip():
                    hi = float(self.trans_max_field.get_text())
                else:
                    hi = lo   # max空欄=minと同じ(minが変数なら同じ変数)
                    hi_var = lo_var
            except ValueError:
                self._want_mark(self.trans_min_field, "error")
                self._want_mark(self.trans_max_field, "error")
                return tr('{0}: 移行条件の値(min/max)が不正です').format(where)
            if not lo_var and not hi_var:
                if hi < lo:
                    lo, hi = hi, lo
                if count_mode and hi < 1:
                    self._want_mark(self.trans_min_field, "error")
                    self._want_mark(self.trans_max_field, "error")
                    return tr('{0}: 移行条件の回数は1以上にしてください').format(where)
                if not count_mode and hi <= 0:
                    self._want_mark(self.trans_min_field, "error")
                    self._want_mark(self.trans_max_field, "error")
                    return tr('{0}: 移行条件の秒数は正の数にしてください').format(where)
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err

            def _out(v, is_var):
                if is_var:
                    return v
                return int(v) if count_mode else v

            if count_mode:
                when = {"type": "channel_count", "channel": ch,
                        "min": _out(lo, lo_var), "max": _out(hi, hi_var)}
            else:
                # =165: 時間系の移行条件は経過時間(state_time)だけになった
                when = {"type": "state_time",
                        "min_seconds": _out(lo, lo_var),
                        "max_seconds": _out(hi, hi_var)}
            transition = {"when": when, "to": to}

        # 書き戻し(未対応フィールドは保持)
        st = dict(ev["states"][sid])
        channels = {}
        for ch_id in CHANNEL_IDS:
            collected = self.channel_sections[ch_id].collect()
            if collected is not None:
                channels[ch_id] = collected
        if channels:
            st["channels"] = channels
            st["device"] = {t: v.get() for t, v in self.device_vars.items()
                            if v.get() != tr("なし")}
        else:
            # 音声なし/動画のみステート: channels キー省略で保存
            st.pop("channels", None)
            st.pop("device", None)
        # 動画は =52 でチャンネルのアイテムになったため、直下の video は
        # 書かない(旧形式で読んだものは _load_raw が移行済み)
        st.pop("video", None)
        # 動画chのシークバーは動画に固定=seek_channel は保存しない
        sk = None if _raw_has_video(channels) \
            else self._seek_channel_out(channels)
        if sk:
            st["seek_channel"] = sk
        else:
            st.pop("seek_channel", None)
        # =256: BGM(引き継ぐ=キー省略 / オフ / 指定)
        err, bgm = self._collect_bgm(where)
        if err:
            return err
        if bgm is not None:
            st["bgm"] = bgm
        else:
            st.pop("bgm", None)
        if transition:
            st["transition"] = transition
        else:
            st.pop("transition", None)
        if self._st_ops:
            st["on_start"] = self._st_ops
        else:
            st.pop("on_start", None)
        if self._st_end_ops:
            st["on_end"] = self._st_end_ops
        else:
            st.pop("on_end", None)
        ev["states"][sid] = st
        if self.state_start_var.get():
            ev["start"] = sid
        self._redraw_state_canvas()
        self._update_trans_summary()
        return None

    def _on_trans_type_menu_change(self):
        if self.trans_type_var.get() == self.TRANS_CHANNEL_COUNT:
            if self._field_is_blank(self.trans_min_field) \
                    and self._field_is_blank(self.trans_max_field):
                lo, hi = self.TRANS_COUNT_DEFAULT
                self.trans_min_field.set(lo)
                self.trans_max_field.set(hi)
        self._update_trans_ui()

    def _update_trans_ui(self):
        choice = self.trans_type_var.get()
        active = choice != self.TRANS_NONE
        is_cond = (choice == self.TRANS_COND)
        is_all_ch = (choice == self.TRANS_ALL_CH)
        is_ch_end = (choice == self.TRANS_CHANNEL_END)
        is_state_time = (choice == self.TRANS_STATE_TIME)
        is_choice = (choice == self.TRANS_CHOICE)   # =275
        # 判定式移行では ch/min/max/単位 を隠し、判定式エディタを出す
        num_widgets = (self.trans_ch_label, self.trans_ch_menu,
                       self.trans_min_field, self.trans_unit_min_label,
                       self.trans_tilde_label,
                       self.trans_max_field, self.trans_unit_label)
        # =275: 選択肢ブロックと移行先行(to_row)の出し入れ
        if is_choice:
            self.trans_to_row.pack_forget()
            if not self.state_choice.winfo_manager():
                self.state_choice.pack(fill="x", pady=(0, 2),
                                       after=self.trans_row)
            self.state_choice.update_ui()
        else:
            self.state_choice.pack_forget()
            if not self.trans_to_row.winfo_manager():
                self.trans_to_row.pack(
                    fill="x", pady=(2, 2),
                    after=(self.trans_cond_frame
                           if self.trans_cond_frame.winfo_manager()
                           else self.trans_row))
        if is_choice:
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_unit_label.configure(text="")
            self.trans_unit_min_label.configure(text="")
        elif is_cond:
            for w in num_widgets:
                w.pack_forget()
            self.trans_cond.set_names(self._var_names())
            if not self.trans_cond.rows:
                self.trans_cond.add_row()
            self.trans_cond_frame.pack(fill="x", pady=(0, 2),
                                       after=self.trans_row)
        elif is_all_ch:
            # 全チャンネル終了: 追加入力なし(移行先チェックのみ)
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
        elif is_ch_end:
            # 指定チャンネル終了: 対象chのみ(min/max/単位なし)
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_ch_label.pack(side="left", padx=(8, 2))
            self.trans_ch_menu.pack(side="left")
            self.trans_ch_menu.configure(state="normal")
        elif is_state_time:
            # =62 経過時間: 対象ch欄は無し。min〜max(秒)だけ出す
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_min_field.pack(side="left", padx=(10, 2))
            self.trans_unit_min_label.pack(side="left")
            self.trans_tilde_label.pack(side="left", padx=(4, 2))
            self.trans_max_field.pack(side="left", padx=2)
            self.trans_unit_label.pack(side="left")
            self.trans_min_field.set_state("normal")
            self.trans_max_field.set_state("normal")
            self.trans_unit_label.configure(text=tr("秒"))
            self.trans_unit_min_label.configure(text=tr("秒"))
        elif not active:
            # =67: 「ステート移行なし」でも対象ch/min/max/単位を欄ごと隠す
            # (無効化した空欄を残さない=チャンネルの終了条件と同じ方針)
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_unit_label.configure(text="")
            self.trans_unit_min_label.configure(text="")
        else:
            self.trans_cond_frame.pack_forget()
            # ch/min/〜/max/単位 を元の並びで戻す
            state = "normal" if active else "disabled"
            for w in num_widgets:
                w.pack_forget()
            self.trans_ch_label.pack(side="left", padx=(8, 2))
            self.trans_ch_menu.pack(side="left")
            self.trans_min_field.pack(side="left", padx=(10, 2))
            self.trans_unit_min_label.pack(side="left")
            self.trans_tilde_label.pack(side="left", padx=(4, 2))
            self.trans_max_field.pack(side="left", padx=2)
            self.trans_unit_label.pack(side="left")
            self.trans_ch_menu.configure(state=state)
            self.trans_min_field.set_state(state)
            self.trans_max_field.set_state(state)
            unit = "" if not active else tr("回")
            self.trans_unit_label.configure(text=unit)
            self.trans_unit_min_label.configure(text=unit)
        # 移行先チェックは cond でも有効(移行なし時のみ無効)。
        # =73: 重み欄・elseメニューもチェックと同じ扱いにする
        for cb in self.trans_target_checks:
            cb.configure(state="normal" if active else "disabled")
        for f in self.trans_weight_fields.values():
            f.set_state("normal" if active else "disabled")
        self.trans_else_menu.configure(
            state="normal" if active else "disabled")
        # =167: 「固定」の移行先メニューも「移行なし」では無効化
        self.trans_fixed_menu.configure(
            state="normal" if active else "disabled")
        # =77: 分岐オプションも「移行なし」では無効化
        st_ = "normal" if active else "disabled"
        self.trans_visited_menu.configure(state=st_)
        self.trans_exh_menu.configure(state=st_)
        self.trans_exh_to_menu.configure(state=st_)
        # =125: 移行先の決め方メニューも「移行なし」では無効化し、
        # 表示レイアウト(抽選/変数分岐)を現在のモードへ合わせる
        self.trans_tomode_menu.configure(state=st_)
        self._update_trans_tomode_ui()
        self._refresh_noaudio_hints()

    def _link_last_state(self, ev: dict, new_sid: str):
        """末尾のステートから新しいステートへ「固定」でつなぐ(=167)。

        イベント側の「＋イベント追加」と同じ考え方(ユーザー決定 2026-08-16):
        - 「末尾」= **ステート一覧の定義順の末尾**(ステート図の並び順)。
          ステートは網目状で一意の終端が決まらないため、順序で決める。
        - **移行条件が未設定(ステート移行なし)のときだけ**つなぐ
          (既にある移行の設定は壊さない)。
        - 自動で入れる移行条件は **「経過時間でステート移行」**(既定60秒)。
          秒数は仮の値なので、ユーザーが実際の長さに合わせて直す前提。
        - **新しく足したステート自身は「移行なし」のまま**(そこから先は
          まだ決められないため)。
        """
        order = [s for s in ev.get("states", {}) if s != new_sid]
        if not order:
            return
        last = order[-1]
        if ev["states"][last].get("transition"):
            return
        ev["states"][last]["transition"] = {
            "when": {"type": "state_time",
                     "min_seconds": self.AUTO_TRANS_SECONDS,
                     "max_seconds": self.AUTO_TRANS_SECONDS},
            "to": new_sid}

    def _add_state(self):
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev:
            return
        err = self._apply_state_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        n = 1
        while f"S{n}" in ev["states"]:
            n += 1
        sid = f"S{n}"
        ev["states"][sid] = json.loads(json.dumps(DEFAULT_STATE))
        self._link_last_state(ev, sid)
        self._refresh_ev_end_state_checks(ev)   # =285
        self._load_state_panel(sid)

    def _delete_state(self):
        ev = self.data["events"].get(self.selected or "")
        sid = self.sel_state
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return
        if len(ev["states"]) <= 1:
            self._report(
                "warn", tr("削除できません"),
                tr("最後のステートは削除できません。\n"
                "ステートをやめる場合は「ステート形式を解除」を使ってください。"))
            return
        self._confirm(tr("ステート '{0}' を削除しますか？").format(sid),
                      lambda s=sid: self._perform_delete_state(s),
                      yes_text=tr("削除する"), warn=True)

    def _perform_delete_state(self, sid):
        self._clear_message()
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return
        if len(ev["states"]) <= 1:
            return
        del ev["states"][sid]
        # 参照修復: 他ステートの移行先から取り除く
        for st in ev["states"].values():
            t = st.get("transition")
            if not t:
                continue
            if isinstance(t.get("when"), dict) \
                    and t["when"].get("type") == "choice":
                # =275: 該当ステート宛ての選択肢を除去。尽きたら移行ごと削除
                ents = [e for e in t.get("choice") or []
                        if not (isinstance(e, dict) and e.get("to") == sid)]
                if not ents:
                    st.pop("transition", None)
                else:
                    t["choice"] = ents
                    draw = t.get("default")
                    if isinstance(draw, dict) and draw.get("to") == sid:
                        t.pop("default", None)
                continue
            to = t.get("to")
            if isinstance(to, str):
                if to == sid:
                    st.pop("transition", None)
            elif isinstance(to, dict):
                # =125: 判定式(cond)形式は該当行を除去。行が尽きたら移行ごと削除
                if isinstance(to.get("cond"), list):
                    rows = [r for r in to["cond"]
                            if not (isinstance(r, dict)
                                    and r.get("to") == sid)]
                    if not rows:
                        st.pop("transition", None)
                    else:
                        to["cond"] = rows
                        if to.get("else") == sid:
                            to.pop("else", None)
                    continue
                lst = [x for x in to.get("random", []) if x != sid]
                if not lst:
                    st.pop("transition", None)
                elif len(lst) == 1:
                    t["to"] = lst[0]
                else:
                    to["random"] = lst
        # イベント終了「指定ステート」から削除ステートを取り除く
        end = ev.get("end")
        if isinstance(end, dict) and end.get("type") == "states":
            end["states"] = [x for x in end.get("states", []) if x != sid]
        if ev.get("start") not in ev["states"]:
            ev["start"] = next(iter(ev["states"]))
        self.sel_state = None
        self._refresh_ev_end_state_checks(ev)   # =285
        self._load_state_panel(ev["start"])

    def _copy_state(self):
        """選択中のステートを複製する(ステート形式イベントのみ)。

        - 除外: ステートの transition(移行先)のみ。それ以外(全チャンネル/音声/
          トラック・on_start/on_end・device)はすべて複製する。
        - 接続先: =167から「＋追加」と同じく**末尾のステートから「固定」で
          つなぐ**(末尾の移行条件が未設定のときだけ。移行条件は
          「全チャンネル終了でステート移行」)。
        - コピー後はコピー先を選択する。編集内容にエラーがあればコピーしない。
        """
        ev = self.data["events"].get(self.selected or "")
        sid = self.sel_state
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return
        err = self._apply_state_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        base = self._copy_base(sid)
        new_sid = self._unique_copy_id(ev["states"], base)
        dup = json.loads(json.dumps(ev["states"][sid]))
        dup.pop("transition", None)   # 移行先はコピーしない(ループ防止)
        ev["states"][new_sid] = dup
        self._link_last_state(ev, new_sid)
        self._refresh_ev_end_state_checks(ev)   # =285
        self._load_state_panel(new_sid)

    def _toggle_states_mode(self):
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        ev = self.data["events"][ev_id]

        if "states" not in ev:
            # 通常イベント → ステート形式(現在の内容をS1に移す)
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
            st = {"channels": ev.pop("channels", {})}
            device = ev.pop("device", None)
            if device:
                st["device"] = device
            # =256: BGMは開始ステートへ移す(Q7。他ステートは既定の
            # 「引き継ぐ」なので聴感は変わらない)
            bgm = ev.pop("bgm", None)
            if bgm is not None:
                st["bgm"] = bgm
            # 動画は =52 でチャンネルの中身になったので、channels ごと
            # 移動すれば追従する(=49の video キー移動は不要になった)
            ev["states"] = {"S1": st}
            ev["start"] = "S1"
            end = ev.get("end")
            if not (isinstance(end, dict) and end.get("type") == "duration"):
                # ステート形式のイベント終了はduration/plays/transitionsのみ。
                # 引き継げない終了条件は既定の60秒にする(=287。旧300秒)。
                ev["end"] = {"type": "duration", "seconds": 60}   # =287: 300→60
        else:
            # ステート形式 → 通常イベント(ステート1つ・移行なしのみ)
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
            states = ev["states"]
            if len(states) != 1:
                self._report(
                    "error", tr("解除できません"),
                    tr("ステートが1つのときだけ解除できます。\n"
                    "先に不要なステートを削除してください。"))
                return
            sid = next(iter(states))
            st = states[sid]
            if st.get("transition"):
                self._report(
                    "error", tr("解除できません"),
                    tr("移行条件を「移行なし」にしてから解除してください。"))
                return
            ev.pop("states")
            ev.pop("start", None)
            ev.pop("end", None)   # 通常イベントの終了はチャンネル側で管理
            channels = st.get("channels", {})
            # 無限(end省略)チャンネルは通常イベントでは使えないため補正する
            for ch in channels.values():
                if "end" not in ch or (ch.get("end") or {}).get("type") == "none":
                    ch.pop("end", None)
                    if ch.get("mode") == "random":
                        ch["end"] = {"type": "duration", "seconds": 300}
                    else:
                        ch["end"] = {"type": "once"}
            ev["channels"] = channels
            if st.get("device"):
                ev["device"] = st["device"]
            # =256: ステートのBGMをイベントへ戻す
            if st.get("bgm") is not None:
                ev["bgm"] = st["bgm"]
            else:
                ev.pop("bgm", None)

        self.sel_state = None
        self._load_panel(ev_id)
        self._redraw_canvas()

    def _commit_state_rename(self):
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev:
            return
        old = self.sel_state
        if not old or old not in ev["states"]:
            return
        new = self.state_id_var.get().strip()
        if new == old:
            self.state_id_var.set(old)
            return
        err = self._validate_new_id(
            new, set(ev["states"]) - {old}, tr("ステート名"))
        if err:
            self._report("error", tr("リネームできません"), err)
            self.state_id_var.set(old)
            return
        applied = self._apply_state_panel()
        if applied:
            self._report("error", tr("編集エラー"), applied)
            self.state_id_var.set(old)
            return
        self._rename_state(ev, old, new)
        self._clear_message()

    def _rename_state(self, ev: dict, old: str, new: str):
        ev["states"] = {(new if k == old else k): v
                        for k, v in ev["states"].items()}
        if ev.get("start") == old:
            ev["start"] = new
        for st in ev["states"].values():
            t = st.get("transition")
            if not isinstance(t, dict):
                continue
            to = t.get("to")
            if isinstance(to, str):
                if to == old:
                    t["to"] = new
            elif isinstance(to, dict):
                if "random" in to:
                    to["random"] = [new if x == old else x
                                    for x in to.get("random", [])]
                # =125: 判定式(cond)形式の行き先・elseも追従
                for row in to.get("cond") or []:
                    if isinstance(row, dict) and row.get("to") == old:
                        row["to"] = new
                if to.get("else") == old:
                    to["else"] = new
            # =275: 選択肢(choice)のステート宛て行き先・既定も追従
            for ent in t.get("choice") or []:
                if isinstance(ent, dict) and ent.get("to") == old:
                    ent["to"] = new
            draw = t.get("default")
            if isinstance(draw, dict) and draw.get("to") == old:
                draw["to"] = new
        # イベント終了「指定ステート」の参照も更新
        end = ev.get("end")
        if isinstance(end, dict) and end.get("type") == "states":
            end["states"] = [new if x == old else x
                             for x in end.get("states", [])]
        self.sel_state = None
        self._refresh_ev_end_state_checks(ev, rename=(old, new))   # =285
        self._load_state_panel(new)

    def _confirm_flatten(self, path: str):
        """相対化(素材コピー)の内容を説明し、最終確認する。"""
        save_dir = os.path.dirname(os.path.abspath(path))
        externals = self._external_paths_for_save(save_dir)
        if not externals:            # 警告表示中に編集で解消された場合
            self._clear_message()
            self._do_save(path)
            return
        lines = [
            tr("相対パスに書き換えて保存します。次の処理を行います:"),
            tr("外部の素材ファイル{0}個を、シナリオファイル(.json)と同じ"
               "フォルダへコピーします。").format(len(externals)),
            tr("音声(wav)・動画には、同じフォルダで自動紐づけされる"
               "funscript/CSVがあれば、それも一緒にコピーします。"),
            tr("シナリオ内の参照パスを相対パスに書き換えます"
               "(元のファイルは削除しません)。"),
            tr("コピー先に同名で内容の異なるファイルが既にある場合は、"
               "何もせず中止します(内容が同一ならコピー不要として"
               "スキップします)。"),
        ]
        # 動画は容量が大きい(=50)。コピー量を事前に見せて事故を防ぐ。
        lines.append(self._externals_size_text(externals))
        self._render_message(
            "confirm", tr("素材をコピーして相対パスで保存"), lines,
            buttons=[
                (tr("はい(コピーして保存する)"),
                 lambda: self._flatten_and_save(path), "primary"),
                (tr("いいえ(保存しない)"), self._clear_message, "ghost"),
            ])

    def _flatten_and_save(self, path: str):
        """外部素材を保存先フォルダへコピーし、参照を相対化して保存する。"""
        self._clear_message()
        # 警告表示中の編集に備え、パネルを反映し直してから最新の状態で処理
        err = self._apply_panel() if self.selected else None
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        save_dir = os.path.dirname(os.path.abspath(path))
        externals = self._external_paths_for_save(save_dir)
        if not externals:
            self._do_save(path)
            return

        # 1) コピー計画: 参照されている外部素材 + wavの自動紐づけ同伴ファイル
        plan: dict[str, str] = {}    # normcase(src) -> src絶対パス

        def add(src):
            src = os.path.normpath(src)
            plan.setdefault(os.path.normcase(src), src)

        for kind, src in externals:
            add(src)
            if kind in ("audio", "video"):
                # 外部wav/動画と同フォルダの自動紐づけfunscript/CSVも同伴コピー
                # (コピー後も同じ命名ルールで保存先フォルダから解決される)
                # 動画も命名ルールで自動紐づけする(=48)ため対象に含める(=50)
                for _t, fs_abs in auto_bind_tracks(src):
                    add(fs_abs)

        # 2) 衝突チェック(1つでも衝突があれば何もコピーせず中止)
        dest_names: dict[str, str] = {}   # normcase(basename) -> src
        conflicts: list[str] = []
        for src in plan.values():
            name = os.path.basename(src)
            key = os.path.normcase(name)
            other = dest_names.get(key)
            if other is not None:
                conflicts.append(tr("{0} と {1} が同名です").format(other, src))
                continue
            dest_names[key] = src
            dest = os.path.join(save_dir, name)
            if os.path.exists(dest):
                if _same_file_content(src, dest):
                    continue     # 同一実体/同一内容=コピー不要(衝突ではない)
                conflicts.append(tr("{0} が既にあります").format(dest))
        if conflicts:
            self._report(
                "error", tr("コピーできません(同名ファイル)")
                + tr("({0}件)").format(len(conflicts)),
                tr("コピー先に同名のファイルがあるため中止しました。"
                   "ファイル名を変更するか、既存ファイルを整理してください:")
                + "\n" + "\n".join(conflicts))
            return

        # 3) コピー実行
        copied = []
        try:
            for src in plan.values():
                dest = os.path.join(save_dir, os.path.basename(src))
                if os.path.exists(dest):
                    if _same_file_content(src, dest):
                        continue    # 既に同一内容がある=コピー不要
                shutil.copy2(src, dest)
                copied.append(os.path.basename(src))
        except OSError as e:
            self._report(
                "error", tr("コピーできません"),
                tr("素材のコピーに失敗したため保存を中止しました"
                   "(コピー済みのファイルは残りますが、シナリオの参照は"
                   "書き換えていません):\n{0}").format(e))
            return

        # 4) self.data の参照をコピー先へ書き換え(base_dir基準の相対 or 絶対)
        def rewrite(_kind, p):
            abs_p = p if os.path.isabs(p) \
                else os.path.normpath(os.path.join(self.base_dir, p))
            if os.path.normcase(os.path.normpath(abs_p)) in plan:
                dest = os.path.join(save_dir, os.path.basename(abs_p))
                return _safe_relpath(dest, self.base_dir)
            return p

        _map_item_paths(self.data, rewrite)
        # 表示中パネルへ反映(選択イベントの音声/トラック表示を更新)
        if self.selected and self.selected in self.data["events"]:
            self._load_panel(self.selected)

        # 5) 保存(付け替え後はすべて保存先の内=相対パスで書き出される)
        if self._do_save(path) and copied:
            self._report(
                "ok", tr("保存しました(素材をコピー)")
                + tr("({0}件)").format(len(copied)),
                tr("{0}個の素材を保存先フォルダへコピーし、"
                   "参照を相対パスに書き換えました:").format(len(copied))
                + "\n" + "\n".join(sorted(copied)))
