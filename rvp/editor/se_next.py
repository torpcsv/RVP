"""シナリオ編集: 遷移方法(固定/分岐/選択肢/変数分岐/数値入力/すごろく)とイベント終了条件の UI・相関制御(mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from .. import scenario_map as _smap
from ..i18n import tr

from .common import (CHANNEL_IDS, CTkOptionMenu, MUTED, TEXT_MUTED,
    _num_disp, _parse_num_text)
from .fields import CondListEditor, VarRefField
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _ScenarioEditorNextMixin:
    """ScenarioEditor の mixin(=301 分割)。遷移方法(固定/分岐/選択肢/変数分岐/数値入力/すごろく)とイベント終了条件の UI・相関制御"""

    @staticmethod
    def _branch_weight_is_zero(w) -> bool:
        """重み欄が 0/空/数値でない(=未指定扱い)か。変数参照は「0ではない」。"""
        raw = w.get_raw()
        if isinstance(raw, dict):
            return False
        txt = raw.strip()
        if txt == "":
            return True
        try:
            return _parse_num_text(txt) == 0
        except ValueError:
            return True

    def _on_branch_check(self, var, w):
        """=284: チェックONで重みが0/空なら「1」を入れる。OFFは重みを触らない。"""
        if var.get() and self._branch_weight_is_zero(w):
            self._branch_syncing = True
            try:
                w.set("1")
            finally:
                self._branch_syncing = False

    def _on_branch_weight(self, var, w):
        """=284: 重みに0以外(変数参照を含む)が入ったら自動でチェックON。"""
        if getattr(self, "_branch_syncing", False):
            return
        if not var.get() and not self._branch_weight_is_zero(w):
            var.set(True)

    @staticmethod
    def _next_primary(ev) -> str | None:
        """レイアウト用の代表遷移先(実体は scenario_map.next_primary)。"""
        return _smap.next_primary(ev)

    @staticmethod
    def _next_targets(ev) -> list[str]:
        """矢印描画用の全遷移先(実体は scenario_map.next_targets)。"""
        return _smap.next_targets(ev)

    def _rebuild_next_ui(self, event_id: str):
        """遷移候補チェック群と分岐オプションを現在のイベントで再構築する。"""
        ev = self.data["events"][event_id]
        nxt_raw = ev.get("next")
        # =123 すごろく(advance)の読込
        adv = ev.get("advance")
        self.sugoroku_var.set(adv is not None)
        self.advance_field.set_names(self._numeric_var_names())
        self.advance_field.set(adv if adv is not None else "")
        self._update_sugoroku_ui()
        self._choice_extra = {}
        self._choice_timeout_ops = []
        # 遷移方法の選択肢: 変数宣言があるときだけ変数分岐/数値入力を出す。
        # =166: 「固定」(遷移先が1つ)と「なし」(遷移しない)を追加。
        # 「固定」は選べる相手が居るときだけ出す(自分自身へは遷移させない)。
        others = [e for e in self.data["events"] if e != event_id]
        modes = []
        if others:
            modes.append(self.NEXT_FIXED)
        modes += [self.NEXT_BRANCH, self.NEXT_CHOICE]
        if self._has_vars():
            modes += [self.NEXT_COND, self.NEXT_COND_WATCH, self.NEXT_INPUT]
        modes.append(self.NEXT_NONE)
        self.next_mode_menu.configure(values=modes)
        self.next_fixed_menu.configure(values=others or [""])
        if isinstance(nxt_raw, dict) and "cond" in nxt_raw:
            # =168: 無限イベントでは「判定式(常に監視)」という別名で見せる
            self.next_mode_var.set(
                self.NEXT_COND_WATCH if self._infinite_event()
                else self.NEXT_COND)
            self._clear_choice_rows()
            self._load_cond_ui(nxt_raw)
            self._load_input_ui(None)
        elif isinstance(nxt_raw, dict) and "input" in nxt_raw:
            self.next_mode_var.set(self.NEXT_INPUT)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(nxt_raw)
        elif isinstance(nxt_raw, dict) and "choice" in nxt_raw:
            self.next_mode_var.set(self.NEXT_CHOICE)
            self._load_choice_ui(nxt_raw)
            self._clear_cond_rows()
            self._load_input_ui(None)
        elif isinstance(nxt_raw, str) and nxt_raw in others:
            # 文字列形式=遷移先が1つ → 「固定」として開く
            self.next_mode_var.set(self.NEXT_FIXED)
            self.next_fixed_var.set(nxt_raw)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(None)
        elif nxt_raw is None:
            self.next_mode_var.set(self.NEXT_NONE)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(None)
        else:
            self.next_mode_var.set(self.NEXT_BRANCH)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(None)
        if self.next_fixed_var.get() not in others:
            self.next_fixed_var.set(others[0] if others else "")
        if self.next_mode_var.get() not in modes:
            # 遷移先が無くなった等で選べない状態になったら「なし」へ寄せる
            self.next_mode_var.set(self.NEXT_NONE)
        self._update_next_mode()
        for w in self.next_targets_frame.winfo_children():
            w.destroy()
        self.next_target_vars = {}
        self.next_weight_vars = {}

        # 現在の設定を解釈
        nxt = ev.get("next")
        checked: dict[str, object] = {}   # eid -> 生の重み(数値 or {"var":..})
        visited_exclude = False
        exh_mode, exh_to = "all", None
        else_to = None
        if isinstance(nxt, str):
            checked[nxt] = 1.0
        elif isinstance(nxt, dict):
            for ent in nxt.get("random") or []:
                if isinstance(ent, str):
                    checked[ent] = 1.0
                elif isinstance(ent, dict) and ent.get("to"):
                    checked[ent["to"]] = ent.get("weight", 1.0)
            visited_exclude = nxt.get("visited") == "exclude"
            ex = nxt.get("when_exhausted", "all")
            if ex == "reset":
                exh_mode = "reset"
            elif isinstance(ex, dict) and ex.get("to"):
                exh_mode, exh_to = "to", ex["to"]
            if isinstance(nxt.get("else"), str):
                else_to = nxt["else"]

        nums = self._numeric_var_names()
        per_row = 3
        for i, eid in enumerate(others):
            cell = ctk.CTkFrame(self.next_targets_frame, fg_color="transparent")
            cell.grid(row=i // per_row, column=i % per_row,
                      sticky="w", padx=(0, 14), pady=1)
            var = tk.BooleanVar(value=eid in checked)
            label = eid if len(eid) <= 14 else eid[:13] + "…"
            # 重みは定数 or 変数参照(VarRefField)。数値変数が無ければ素の数値欄。
            wfield = VarRefField(cell, width=48, placeholder="1")
            wfield.set_names(nums)
            # =284(A案): 未チェック行は重み「0」で表示(従来「1」)。チェックON
            # で重みが0/空なら「1」に、重みに0以外を入れたら自動でチェックON。
            # チェックOFFは重みを触らず、重み0のままチェックONも許す(変数で
            # 重みを動かす用途)。未チェック行の重みは従来どおり無視される。
            wfield.set(checked[eid] if eid in checked else "0")
            ctk.CTkCheckBox(
                cell, text=label, variable=var,
                font=ctk.CTkFont(size=12), checkbox_width=16, checkbox_height=16,
                width=20, fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                command=lambda v=var, w=wfield: self._on_branch_check(v, w),
            ).pack(side="left")
            wfield.pack(side="left", padx=(4, 0))
            wfield.on_change = (lambda v=var, w=wfield:
                                self._on_branch_weight(v, w))
            wfield.text_var.trace_add(
                "write", lambda *_a, v=var, w=wfield: self._on_branch_weight(v, w))
            wfield.sel_var.trace_add(
                "write", lambda *_a, v=var, w=wfield: self._on_branch_weight(v, w))
            self.next_target_vars[eid] = var
            self.next_weight_vars[eid] = wfield

        self._branch_syncing = False
        # 候補が0件のときの空きスペースを潰す(=66・ユーザー報告)。
        # **CTkFrame は中身が無いと既定で200pxの高さを要求する**ため、
        # イベントが1つだけのシナリオでは「次のイベント」と「分岐」の間に
        # 200pxの空白が空いていた(=58の CANVAS_H と同じ落とし穴)。
        if others:
            self.next_targets_frame.pack_propagate(True)
        else:
            self.next_targets_frame.pack_propagate(False)
            self.next_targets_frame.configure(height=1)

        # else(重み全0時の行き先)メニューを構築
        else_vals = [self.NEXT_ELSE_END] + others
        self.next_else_menu.configure(values=else_vals)
        self.next_else_var.set(else_to if else_to in others else self.NEXT_ELSE_END)

        self.next_visited_var.set(tr("未実行イベントのみ候補") if visited_exclude
                                  else tr("毎回すべて候補"))
        self.next_exhausted_var.set(
            tr("リセットして再び、未実行イベントのみ候補") if exh_mode == "reset"
            else tr("指定イベントへ") if exh_mode == "to"
            else tr("以後、毎回すべて候補"))
        all_ids = list(self.data["events"].keys())
        self.next_exh_to_menu.configure(values=all_ids or [""])
        self.next_exh_to_var.set(exh_to if exh_to in self.data["events"]
                                 else (all_ids[0] if all_ids else ""))
        self._update_next_ui()

    def _update_next_mode(self):
        """遷移方法(固定/分岐/選択肢/変数分岐/数値入力/なし)の表示切替。"""
        mode = self.next_mode_var.get()
        inners = {self.NEXT_FIXED: self.fixed_inner,
                  self.NEXT_CHOICE: self.choice_inner,
                  self.NEXT_COND: self.cond_inner,
                  self.NEXT_COND_WATCH: self.cond_inner,   # =168
                  self.NEXT_INPUT: self.input_inner}
        # 「なし」は入力欄そのものが無い(どのinnerも出さない)
        active = inners.get(mode, None if mode == self.NEXT_NONE
                            else self.random_inner)
        for w in (self.random_inner, self.choice_inner, self.cond_inner,
                  self.input_inner, self.fixed_inner):
            if w is not active:
                w.pack_forget()
        if active is not None:
            active.pack(fill="x")
        # =220: どのinnerも出さない「なし」では空フレームが CTk 既定の
        # 200px を要求してしまうので、明示的に潰す(中身が戻ったら
        # pack_propagate が高さを取り戻す)。
        self.next_area.configure(height=1 if active is None else 0)
        if mode == self.NEXT_FIXED:
            self.next_hint_label.configure(
                text=tr("(再生が終わると、指定したイベントへ進みます)"))
        elif mode == self.NEXT_NONE:
            self.next_hint_label.configure(
                text=tr("(遷移しません。再生はここで終わります)"))
        elif mode == self.NEXT_CHOICE:
            self.next_hint_label.configure(
                text=tr("(ボタンで選ばれた遷移先へ進みます)"))
            if not self.choice_rows:
                self._add_choice_row()
            self._update_choice_ui()
        elif mode in (self.NEXT_COND, self.NEXT_COND_WATCH):
            self.next_hint_label.configure(
                text=tr("(再生中ずっと判定し、成立した行へ進みます)")
                if mode == self.NEXT_COND_WATCH
                else tr("(変数の条件で遷移先を決めます)"))
            if not self.cond_rows:
                self._add_cond_row()
        elif mode == self.NEXT_INPUT:
            self.next_hint_label.configure(
                text=tr("(数値を入力させて変数へセットし、遷移します)"))
            self._update_input_menus()
            self._update_input_ui()
        else:
            self.next_hint_label.configure(
                text=tr("(チェックなし=再生終了、複数チェック=重み付き抽選)"))
        # =123: すごろくは選択肢/数値入力/なしの遷移では使えない(行ごと非表示)
        if mode in (self.NEXT_CHOICE, self.NEXT_INPUT, self.NEXT_NONE,
                    self.NEXT_COND_WATCH):
            self.sugoroku_row.pack_forget()
        else:
            self.sugoroku_row.pack(fill="x", pady=(2, 0))

    def _update_sugoroku_ui(self):
        """=123: すごろくチェックに応じて歩数欄の表示を切り替える。"""
        if self.sugoroku_var.get():
            self.sugoroku_steps_label.pack(side="left", padx=(10, 2))
            self.advance_field.pack(side="left")
            self.advance_field.set_names(self._numeric_var_names())
            self.sugoroku_hint.pack(side="left", padx=(8, 0))
        else:
            self.sugoroku_steps_label.pack_forget()
            self.advance_field.pack_forget()
            self.sugoroku_hint.pack_forget()

    def _collect_advance(self):
        """=123: すごろく欄からadvance値を組み立てる。(エラー, 値|None)。"""
        if not self.sugoroku_var.get():
            return None, None
        if self.next_mode_var.get() in (self.NEXT_CHOICE, self.NEXT_INPUT,
                                        self.NEXT_NONE,
                                        self.NEXT_COND_WATCH):
            return None, None   # 行ごと非表示のモード=無効(保存しない)
        raw = self.advance_field.get_raw()
        if isinstance(raw, dict):
            if not raw.get("var"):
                return tr("すごろくの歩数の変数を選択してください"), None
            return None, raw
        txt = str(raw).strip()
        try:
            v = float(txt)
            if not v.is_integer() or v < 1:
                raise ValueError
        except ValueError:
            return tr("すごろくの歩数は1以上の整数か変数で指定してください"), None
        return None, int(v)

    def _event_id_choices(self):
        return list(self.data["events"].keys()) or [""]

    def _add_choice_row(self, label: str = "", to: str | None = None,
                        raw: dict | None = None):
        if len(self.choice_rows) >= 9:
            return
        row = ctk.CTkFrame(self.choice_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        num = ctk.CTkLabel(row, text="", width=22, font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        num.pack(side="left")
        label_var = tk.StringVar(value=label)
        label_entry = ctk.CTkEntry(row, textvariable=label_var, width=260,
                                   height=26,
                                   placeholder_text=tr("ボタンの表示テキスト"))
        label_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        ids = self._event_id_choices()
        to_var = tk.StringVar(value=to if to in self.data["events"] else ids[0])
        to_menu = CTkOptionMenu(row, variable=to_var, width=150, height=26,
                                    values=ids,
                                    fg_color=("gray75", "gray28"),
                                    button_color=("gray70", "gray33"))
        to_menu.pack(side="left", padx=6)
        entry = {"frame": row, "num": num, "label_var": label_var,
                 "label_entry": label_entry, "to_var": to_var,
                 "to_menu": to_menu, "raw": dict(raw or {})}
        if self._has_vars():
            ops_btn = ctk.CTkButton(
                row, text="", width=86, height=26,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
                command=lambda e=entry: self._edit_choice_row_ops(e))
            ops_btn.pack(side="left", padx=(6, 0))
            entry["ops_btn"] = ops_btn
            self._update_choice_ops_btn(entry)
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_choice_row(e)
                      ).pack(side="left", padx=(4, 0))
        self.choice_rows.append(entry)
        self._renumber_choice_rows()

    def _update_choice_ops_btn(self, entry):
        if entry.get("ops_btn"):
            n = len(entry["raw"].get("ops") or [])
            entry["ops_btn"].configure(text=tr("変数({0})").format(n))

    def set_choice_row_ops(self, entry, result: dict):
        """選択肢1件の変数操作(ops=選択時のみ発火)を反映する。"""
        if result.get("ops"):
            entry["raw"]["ops"] = result["ops"]
        else:
            entry["raw"].pop("ops", None)
        self._update_choice_ops_btn(entry)

    def _edit_choice_row_ops(self, entry):
        idx = self.choice_rows.index(entry) + 1
        self.open_ops_dialog(
            tr("選択肢{0}").format(idx),
            [(tr("この選択肢が選ばれた時(ops)"), "ops",
              entry["raw"].get("ops") or [])],
            lambda result: self.set_choice_row_ops(entry, result))

    def set_choice_timeout_ops(self, result: dict):
        """choiceのon_timeout(タイムアウト確定時のみ発火)を反映する。"""
        self._choice_timeout_ops = result.get("on_timeout") or []
        self._update_choice_toops_btn()

    def _update_choice_toops_btn(self):
        self.choice_toops_btn.configure(
            text=tr("時間切れ時の変数操作({0})").format(
                len(self._choice_timeout_ops)))

    def _edit_choice_timeout_ops(self):
        self.open_ops_dialog(
            tr("タイムアウト時"),
            [(tr("タイムアウト確定時のみ(on_timeout)"), "on_timeout",
              self._choice_timeout_ops)],
            self.set_choice_timeout_ops)

    def _delete_choice_row(self, entry):
        if len(self.choice_rows) <= 1:
            self._report("warn", tr("削除できません"), tr("選択肢は最低1つ必要です"))
            return
        self.choice_rows.remove(entry)
        entry["frame"].destroy()
        self._renumber_choice_rows()

    def _renumber_choice_rows(self):
        for i, e in enumerate(self.choice_rows):
            e["num"].configure(text=f"{i + 1}.")
        state = "normal" if len(self.choice_rows) < 9 else "disabled"
        self.choice_add_btn.configure(state=state)

    def _clear_choice_rows(self):
        for e in self.choice_rows:
            e["frame"].destroy()
        self.choice_rows = []

    def _update_choice_ui(self):
        """タイムリミット/表示タイミングの付随入力の表示切替。"""
        if self.choice_tlim_var.get() == tr("時間指定"):
            # 時間指定は秒のみ(分欄は廃止)
            self.choice_tsec_entry.pack(side="left", padx=(6, 2))
            self.choice_tsec_label.pack(side="left")
            if self._has_vars():
                self._update_choice_toops_btn()
                self.choice_toops_btn.pack(side="left", padx=(10, 0))
            else:
                self.choice_toops_btn.pack_forget()
        else:
            for w in (self.choice_tmin_entry, self.choice_tmin_label,
                      self.choice_tsec_entry, self.choice_tsec_label,
                      self.choice_toops_btn):
                w.pack_forget()
        if self.choice_dflt_var.get() == tr("指定イベントへ"):
            self.choice_dflt_to_menu.pack(side="left", padx=(6, 0))
            self.choice_dflt_to_menu.configure(values=self._event_id_choices())
            if self.choice_dflt_to_var.get() not in self.data["events"]:
                self.choice_dflt_to_var.set(self._event_id_choices()[0])
        else:
            self.choice_dflt_to_menu.pack_forget()
        if self.choice_show_var.get() == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            self.choice_ssec_entry.pack(side="left", padx=(6, 2))
            self.choice_ssec_label.pack(side="left")
        else:
            for w in (self.choice_smin_entry, self.choice_smin_label,
                      self.choice_ssec_entry, self.choice_ssec_label):
                w.pack_forget()

    def _load_choice_ui(self, nxt: dict):
        """next(choice形式)をエディタへ読み込む。"""
        self._clear_choice_rows()
        self._choice_extra = {k: v for k, v in nxt.items()
                              if k not in ("choice", "timeout", "default",
                                           "show", "on_timeout", "skip")}
        self._choice_timeout_ops = list(nxt.get("on_timeout") or [])
        # =274: 選択必須(skip=stay)
        self.choice_stay_var.set(nxt.get("skip") == "stay")
        for ent in nxt.get("choice") or []:
            self._add_choice_row(str(ent.get("label", "")), ent.get("to"),
                                 raw=ent if isinstance(ent, dict) else None)
        if not self.choice_rows:
            self._add_choice_row()
        traw = nxt.get("timeout")
        if isinstance(traw, dict):
            self.choice_tlim_var.set(tr("時間指定"))
            # 時間指定は秒のみ(minutesは廃止=読まない)
            self.choice_tsec_var.set(f"{float(traw.get('seconds', 0)):g}")
        else:
            self.choice_tlim_var.set(tr("無制限"))
        # デフォルト遷移先(旧形式timeout.toは「指定イベントへ」として読み込む)
        draw = nxt.get("default")
        legacy_to = traw.get("to") if isinstance(traw, dict) else None
        if draw == "random":
            self.choice_dflt_var.set(tr("選択肢から等確率で抽選"))
        elif isinstance(draw, dict) and draw.get("to"):
            self.choice_dflt_var.set(tr("指定イベントへ"))
            self.choice_dflt_to_var.set(draw["to"])
        elif legacy_to:
            self.choice_dflt_var.set(tr("指定イベントへ"))
            self.choice_dflt_to_var.set(legacy_to)
        else:
            self.choice_dflt_var.set(tr("先頭の選択肢へ"))
        sraw = nxt.get("show", "end")
        if sraw == "start":
            self.choice_show_var.set(tr("イベント開始時"))
        elif isinstance(sraw, dict):
            self.choice_show_var.set(tr("イベント開始から指定時間後"))
            # 時間指定は秒のみ(minutesは廃止=読まない)
            self.choice_ssec_var.set(f"{float(sraw.get('seconds', 0)):g}")
        else:
            self.choice_show_var.set(tr("イベント終了条件の達成時"))

    def _collect_choice(self, event_id: str):
        """選択肢UIからnext(choice形式)を組み立てる。(err, value)。"""
        entries = []
        for i, e in enumerate(self.choice_rows):
            label = e["label_var"].get().strip()
            to = e["to_var"].get()
            if not label:
                self._want_mark(e.get("label_entry"), "error")
                return tr("イベント {0}: 選択肢{1}の表示テキストが空です").format(event_id, i + 1), None
            if to not in self.data["events"]:
                self._want_mark(e.get("to_menu"), "error")
                return tr("イベント {0}: 選択肢{1}の遷移先が不正です").format(event_id, i + 1), None
            extra = {k: v for k, v in (e.get("raw") or {}).items()
                     if k not in ("label", "to")}
            entries.append({"label": label, "to": to, **extra})
        if not 1 <= len(entries) <= 9:
            return tr("イベント {0}: 選択肢は1〜9件にしてください").format(event_id), None
        value = {"choice": entries}
        if self.choice_tlim_var.get() == tr("時間指定"):
            # 時間指定は秒のみ(分欄は廃止)
            try:
                total = float(self.choice_tsec_var.get() or 0)
            except ValueError:
                total = 0
            if total <= 0:
                return tr("イベント {0}: タイムリミットの時間が不正です").format(event_id), None
            value["timeout"] = {"seconds": round(total, 3)}
        # デフォルト遷移先(先頭=既定なので省略。旧timeout.toはdefaultへ移行して保存)
        dflt = self.choice_dflt_var.get()
        if dflt == tr("選択肢から等確率で抽選"):
            value["default"] = "random"
        elif dflt == tr("指定イベントへ"):
            to = self.choice_dflt_to_var.get()
            if to not in self.data["events"]:
                return tr("イベント {0}: デフォルト遷移先のイベントを選択してください").format(event_id), None
            value["default"] = {"to": to}
        show = self.choice_show_var.get()
        if show == tr("イベント開始時"):
            value["show"] = "start"
        elif show == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            try:
                total = float(self.choice_ssec_var.get() or 0)
            except ValueError:
                return tr("イベント {0}: 表示タイミングの時間が不正です").format(event_id), None
            value["show"] = {"seconds": round(total, 3)}
        # "end"は既定なので省略
        # =274: 選択必須(ON=stayを明示。OFF=既定なのでキーを書かない)
        if self.choice_stay_var.get():
            value["skip"] = "stay"
        if self._choice_timeout_ops:
            value["on_timeout"] = self._choice_timeout_ops
        # 編集対象外キーを保持する
        for k, v in self._choice_extra.items():
            value.setdefault(k, v)
        return None, value

    def _add_cond_row(self, raw: dict | None = None):
        raw = raw or {}
        row = ctk.CTkFrame(self.cond_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=2)
        num = ctk.CTkLabel(row, text="", width=22, font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        num.pack(side="left", anchor="n", pady=4)
        conds = CondListEditor(row)
        conds.set_names(self._var_names())
        conds.load(raw.get("when") or [])
        conds.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", anchor="n", pady=4)
        ids = self._event_id_choices()
        to_var = tk.StringVar(
            value=raw.get("to") if raw.get("to") in self.data["events"]
            else ids[0])
        CTkOptionMenu(row, variable=to_var, width=140, height=26,
                          values=ids,
                          fg_color=("gray75", "gray28"),
                          button_color=("gray70", "gray33")
                          ).pack(side="left", padx=6, anchor="n", pady=2)
        entry = {"frame": row, "num": num, "conds": conds, "to_var": to_var}
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_cond_row(e)
                      ).pack(side="left", padx=(4, 0), anchor="n", pady=2)
        self.cond_rows.append(entry)
        self._renumber_cond_rows()

    def _delete_cond_row(self, entry):
        if len(self.cond_rows) <= 1:
            self._report("warn", tr("削除できません"), tr("条件行は最低1つ必要です"))
            return
        self.cond_rows.remove(entry)
        entry["frame"].destroy()
        self._renumber_cond_rows()

    def _renumber_cond_rows(self):
        for i, e in enumerate(self.cond_rows):
            e["num"].configure(text=f"{i + 1}.")

    def _clear_cond_rows(self):
        for e in self.cond_rows:
            e["frame"].destroy()
        self.cond_rows = []
        self._cond_extra = {}

    def _load_cond_ui(self, nxt: dict):
        """next(cond形式)をエディタへ読み込む。"""
        self._clear_cond_rows()
        self._cond_extra = {k: v for k, v in nxt.items()
                            if k not in ("cond", "else")}
        for row in nxt.get("cond") or []:
            if isinstance(row, dict):
                self._add_cond_row(row)
        if not self.cond_rows:
            self._add_cond_row()
        ids = [tr("再生終了")] + list(self.data["events"].keys())
        self.cond_else_menu.configure(values=ids)
        else_to = nxt.get("else")
        self.cond_else_var.set(else_to if else_to in self.data["events"]
                               else tr("再生終了"))

    def _collect_cond(self, event_id: str):
        """変数分岐UIからnext(cond形式)を組み立てる。(err, value)。"""
        string_vars = self._string_var_names()
        rows = []
        for i, e in enumerate(self.cond_rows):
            where = tr("イベント {0} 条件行{1}").format(event_id, i + 1)
            err, conds = e["conds"].collect(where, string_vars,
                                            mark=self._want_mark)
            if err:
                return err, None
            to = e["to_var"].get()
            if to not in self.data["events"]:
                return tr("{0}: 遷移先が不正です").format(where), None
            rows.append({"when": conds, "to": to})
        if not rows:
            return tr("イベント {0}: 条件行を1つ以上指定してください").format(event_id), None
        else_choice = self.cond_else_var.get()
        value = {"cond": rows,
                 "else": else_choice if else_choice in self.data["events"]
                 else None}
        for k, v in getattr(self, "_cond_extra", {}).items():
            value.setdefault(k, v)
        return None, value

    def _update_input_menus(self):
        nums = self._numeric_var_names()
        self.input_var_menu.configure(values=nums or [""])
        if self.input_var_var.get() not in nums:
            self.input_var_var.set(nums[0] if nums else "")
        ids = self._event_id_choices()
        self.input_to_menu.configure(values=ids)
        if self.input_to_var.get() not in self.data["events"]:
            self.input_to_var.set(ids[0])

    def _update_input_ui(self):
        if self.input_show_var.get() == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            self.input_ssec_entry.pack(side="left", padx=(6, 2))
            self.input_ssec_label.pack(side="left")
        else:
            for w in (self.input_smin_entry, self.input_smin_label,
                      self.input_ssec_entry, self.input_ssec_label):
                w.pack_forget()

    def _load_input_ui(self, nxt: dict | None):
        """next(input形式)をエディタへ読み込む(None=初期化)。"""
        self._input_extra = {}
        self._input_next_extra = {}
        self._update_input_menus()
        if not isinstance(nxt, dict):
            self.input_label_var.set("")
            self.input_min_var.set("")
            self.input_max_var.set("")
            self.input_show_var.set(tr("イベント終了条件の達成時"))
            self.input_stay_var.set(True)   # =288: 新規は入力必須が既定
            self._update_input_ui()
            return
        iraw = nxt.get("input") or {}
        self._input_extra = {k: v for k, v in iraw.items()
                             if k not in ("var", "label", "to", "min", "max")}
        self._input_next_extra = {k: v for k, v in nxt.items()
                                  if k not in ("input", "show", "skip")}
        self.input_stay_var.set(nxt.get("skip") == "stay")   # =288
        if iraw.get("var"):
            nums = self._numeric_var_names()
            if iraw["var"] not in nums:
                nums = nums + [iraw["var"]]
                self.input_var_menu.configure(values=nums)
            self.input_var_var.set(iraw["var"])
        self.input_label_var.set(str(iraw.get("label", "") or ""))
        if iraw.get("to") in self.data["events"]:
            self.input_to_var.set(iraw["to"])
        self.input_min_var.set(_num_disp(iraw["min"]) if "min" in iraw else "")
        self.input_max_var.set(_num_disp(iraw["max"]) if "max" in iraw else "")
        sraw = nxt.get("show", "end")
        if sraw == "start":
            self.input_show_var.set(tr("イベント開始時"))
        elif isinstance(sraw, dict):
            self.input_show_var.set(tr("イベント開始から指定時間後"))
            # 時間指定は秒のみ(minutesは廃止=読まない)
            self.input_ssec_var.set(f"{float(sraw.get('seconds', 0)):g}")
        else:
            self.input_show_var.set(tr("イベント終了条件の達成時"))
        self._update_input_ui()

    def _collect_input(self, event_id: str):
        """数値入力UIからnext(input形式)を組み立てる。(err, value)。"""
        var = self.input_var_var.get()
        if var not in self._numeric_var_names():
            self._want_mark(self.input_var_menu, "error")
            return tr("イベント {0}: 入力先の数値変数を選択してください").format(event_id), None
        to = self.input_to_var.get()
        if to not in self.data["events"]:
            self._want_mark(self.input_to_menu, "error")
            return tr("イベント {0}: 数値入力の遷移先が不正です").format(event_id), None
        iraw = {"var": var}
        label = self.input_label_var.get().strip()
        if label:
            iraw["label"] = label
        iraw["to"] = to
        mn_text = self.input_min_var.get().strip()
        mx_text = self.input_max_var.get().strip()
        try:
            mn = _parse_num_text(mn_text) if mn_text else None
            mx = _parse_num_text(mx_text) if mx_text else None
        except ValueError:
            self._want_mark(self.input_min_entry, "error")
            self._want_mark(self.input_max_entry, "error")
            return tr("イベント {0}: 入力範囲(min/max)が不正です").format(event_id), None
        if mn is not None and mx is not None and mn > mx:
            self._want_mark(self.input_min_entry, "error")
            self._want_mark(self.input_max_entry, "error")
            return tr("イベント {0}: 入力範囲はmin≦maxにしてください").format(event_id), None
        if mn is not None:
            iraw["min"] = mn
        if mx is not None:
            iraw["max"] = mx
        iraw.update(self._input_extra)
        value = {"input": iraw}
        show = self.input_show_var.get()
        if show == tr("イベント開始時"):
            value["show"] = "start"
        elif show == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            try:
                total = float(self.input_ssec_var.get() or 0)
            except ValueError:
                return tr("イベント {0}: 表示タイミングの時間が不正です").format(event_id), None
            value["show"] = {"seconds": round(total, 3)}
        # =288: 入力必須(ON=stayを明示。OFF=既定なのでキーを書かない)
        if self.input_stay_var.get():
            value["skip"] = "stay"
        for k, v in self._input_next_extra.items():
            value.setdefault(k, v)
        return None, value

    def _update_next_ui(self):
        """分岐オプションの表示/非表示を更新する。

        =66: 「全候補が実行済みのとき:」は行ごと2行目(`next_exh_row`)に
        分けた。1行に並べると「指定イベントへ」の行き先メニューが枠から
        はみ出して見えなかった(ユーザー報告)。
        """
        exclude = self.next_visited_var.get() == tr("未実行イベントのみ候補")
        if exclude:
            if not self.next_exh_row.winfo_ismapped():
                self.next_exh_row.pack(fill="x", pady=(0, 2),
                                       before=self.next_else_row)
            self.next_exh_label.pack(side="left")
            self.next_exh_menu.pack(side="left", padx=(6, 0))
            if self.next_exhausted_var.get() == tr("指定イベントへ"):
                self.next_exh_to_menu.pack(side="left", padx=(6, 0))
            else:
                self.next_exh_to_menu.pack_forget()
        else:
            self.next_exh_row.pack_forget()
            self.next_exh_label.pack_forget()
            self.next_exh_menu.pack_forget()
            self.next_exh_to_menu.pack_forget()

    def _collect_next(self, event_id: str):
        """遷移UIからnext値を組み立てる。(エラーメッセージ, 値) を返す。"""
        mode = self.next_mode_var.get()
        if mode == self.NEXT_CHOICE:
            return self._collect_choice(event_id)
        if mode in (self.NEXT_COND, self.NEXT_COND_WATCH):
            # =168: 「判定式(常に監視)」も保存形式は cond。無限イベント
            # との組み合わせでのみ出る名前違いのモード
            return self._collect_cond(event_id)
        if mode == self.NEXT_INPUT:
            return self._collect_input(event_id)
        if mode == self.NEXT_NONE:
            # =166: 遷移なし(シナリオはここで終わる)
            return None, None
        if mode == self.NEXT_FIXED:
            # =166: 遷移先が1つ。保存形式は従来の文字列next(後方互換)
            to = self.next_fixed_var.get()
            if to not in self.data["events"] or to == event_id:
                self._want_mark(self.next_fixed_menu, "error")
                return tr("イベント {0}: 遷移先を選択してください").format(
                    event_id), None
            return None, to
        checked = []   # (eid, 重み: 数値 or {"var":..})
        for eid, var in self.next_target_vars.items():
            if not var.get() or eid not in self.data["events"]:
                continue
            raw = self.next_weight_vars[eid].get_raw()
            if isinstance(raw, str):
                txt = raw.strip()
                if txt == "":
                    w = 1.0
                else:
                    try:
                        w = _parse_num_text(txt)   # 0/負も許容(=出さない面)
                    except ValueError:
                        return tr("イベント {0}: 重みが不正です").format(event_id), None
            else:
                w = raw   # {"var": 変数名}
            checked.append((eid, w))
        if not checked:
            return None, None
        exclude = self.next_visited_var.get() == tr("未実行イベントのみ候補")
        # else(重み全0時の行き先)。(終了)=指定なし。
        else_choice = self.next_else_var.get()
        else_to = (else_choice if else_choice != self.NEXT_ELSE_END
                   and else_choice in self.data["events"] else None)
        # 1件・定数重み1.0・非exclude・else無し → 従来の文字列形式(後方互換)
        if (len(checked) == 1 and not exclude and else_to is None
                and checked[0][1] == 1.0):
            return None, checked[0][0]
        value = {"random": [
            eid if w == 1.0 else {"to": eid, "weight": w}
            for eid, w in checked]}
        if exclude:
            value["visited"] = "exclude"
            exh = self.next_exhausted_var.get()
            if exh == tr("リセットして再び、未実行イベントのみ候補"):
                value["when_exhausted"] = "reset"
            elif exh == tr("指定イベントへ"):
                to = self.next_exh_to_var.get()
                if to not in self.data["events"]:
                    return tr("イベント {0}: 「指定イベントへ」の行き先を選択してください").format(event_id), None
                value["when_exhausted"] = {"to": to}
        if else_to is not None:
            value["else"] = else_to
        return None, value

    def _evend_ch_candidates(self) -> list[str]:
        """指定チャンネル終了の対象ch候補(有効なチャンネルのみ)。"""
        enabled = [c for c in CHANNEL_IDS
                   if self.channel_sections[c].enabled_var.get()]
        return enabled or list(CHANNEL_IDS)

    def _infinite_event(self) -> bool:
        """=168: このイベントの終了条件が「無限」か(通常/ステート形式の両対応)。"""
        ev = self.data["events"].get(self.selected or "")
        if isinstance(ev, dict) and "states" in ev:
            return self.ev_end_var.get() == self.EV_END_INFINITE
        return self.evend_var.get() == self.EVEND_INFINITE

    def _update_infinite_correlation(self):
        """=168: 終了条件が「無限」のときの遷移方法・表示タイミングの相関制御。

        無限のイベントは**自分では終わらない**ので、終了時に評価される
        「固定」「分岐」は永久に発火しない。出口になりうるのは
        **なし / 選択肢 / 数値入力 / 判定式(常に監視)** だけ。
        「判定式」は通常の変数分岐と実装が別(再生中に監視する)なので
        名前も分ける。選択肢・数値入力の表示タイミングも
        「イベント終了条件の達成時」が永久に来ないため2択に絞る。
        """
        if not hasattr(self, "next_mode_menu"):
            return
        infinite = self._infinite_event()
        others = [e for e in self.data["events"] if e != self.selected]
        show_times = list(self.SHOW_TIMES)
        # =275: ステート移行に選択肢があるイベントでは、イベント側の
        # 選択肢/数値入力は「イベント終了条件の達成時」のみ(同時表示を避ける)。
        # 無限イベントではその達成時が来ないので選択肢/数値入力ごと出さない
        state_choice = self._event_has_state_choice()
        if infinite:
            modes = [self.NEXT_NONE] + ([] if state_choice
                                        else [self.NEXT_CHOICE])
            if self._has_vars():
                modes += ([] if state_choice else [self.NEXT_INPUT]) \
                    + [self.NEXT_COND_WATCH]
            show_times = show_times[1:]      # 「終了条件の達成時」は来ない
        else:
            modes = ([self.NEXT_FIXED] if others else []) \
                + [self.NEXT_BRANCH, self.NEXT_CHOICE]
            if self._has_vars():
                modes += [self.NEXT_COND, self.NEXT_INPUT]
            modes.append(self.NEXT_NONE)
            if state_choice:
                show_times = show_times[:1]  # 「終了条件の達成時」のみ
        self.next_mode_menu.configure(values=modes)
        cur = self.next_mode_var.get()
        if cur not in modes:
            # 無限⇔有限の切り替えで選べなくなったモードは読み替える
            swap = {self.NEXT_COND: self.NEXT_COND_WATCH,
                    self.NEXT_COND_WATCH: self.NEXT_COND}
            self.next_mode_var.set(swap.get(cur) if swap.get(cur) in modes
                                   else self.NEXT_NONE)
            self._update_next_mode()
        for menu, var in ((getattr(self, "choice_show_menu", None),
                           getattr(self, "choice_show_var", None)),
                          (getattr(self, "input_show_menu", None),
                           getattr(self, "input_show_var", None))):
            if menu is None or var is None:
                continue
            menu.configure(values=show_times)
            if var.get() not in show_times:
                var.set(show_times[0])
        if hasattr(self, "cond_else_row"):
            # 無限では else(どの行も成立しないとき)が成立しえないので隠す
            if infinite:
                self.cond_else_row.pack_forget()
            elif not self.cond_else_row.winfo_ismapped():
                self.cond_else_row.pack(fill="x", pady=(4, 4))
        if hasattr(self, "_update_choice_ui"):
            self._update_choice_ui()

    def _event_has_state_choice(self) -> bool:
        """=275: 選択中イベントのいずれかのステートが「選択肢でステート移行」か。

        保存前のUI状態も見る(編集中のステートのコンボが選択肢なら True)。
        """
        ev = (self.data.get("events") or {}).get(self.selected or "")
        if not isinstance(ev, dict) or "states" not in ev:
            return False
        cur = self.sel_state
        for sid, st in ev["states"].items():
            if sid == cur and hasattr(self, "trans_type_var"):
                if self.trans_type_var.get() == self.TRANS_CHOICE:
                    return True
                continue
            t = st.get("transition") if isinstance(st, dict) else None
            if isinstance(t, dict) and isinstance(t.get("when"), dict) \
                    and t["when"].get("type") == "choice":
                return True
        return False

    def _update_state_choice_correlation(self):
        """=275: ステート移行の選択肢の有無をイベント側の遷移UIへ反映する。"""
        if hasattr(self, "next_mode_menu"):
            self._update_infinite_correlation()

    def _update_evend_ui(self):
        """イベント終了条件コンボの選択に応じて対象ch欄の表示を更新する。"""
        cond = self.evend_var.get()
        if cond == self.EVEND_CHANNEL:
            cands = self._evend_ch_candidates()
            self.evend_ch_menu.configure(values=cands)
            if self.evend_ch_var.get() not in cands:
                self.evend_ch_var.set(cands[0])
            self.evend_ch_label.pack(side="left", padx=(10, 2))
            self.evend_ch_menu.pack(side="left")
        else:
            self.evend_ch_label.pack_forget()
            self.evend_ch_menu.pack_forget()
        if cond == self.EVEND_DURATION:
            nums = self._numeric_var_names()
            self.evend_secs_min.set_names(nums)
            self.evend_secs_max.set_names(nums)
            self.evend_secs_min.pack(side="left", padx=(10, 2))
            self.evend_secs_min_label.pack(side="left")
            self.evend_secs_tilde.pack(side="left", padx=(4, 2))
            self.evend_secs_max.pack(side="left", padx=2)
            self.evend_secs_label.pack(side="left")
            self.evend_secs_hint.pack(side="left", padx=(6, 0))
        else:
            self.evend_secs_min.pack_forget()
            self.evend_secs_min_label.pack_forget()
            self.evend_secs_tilde.pack_forget()
            self.evend_secs_max.pack_forget()
            self.evend_secs_label.pack_forget()
            self.evend_secs_hint.pack_forget()
        if cond == self.EVEND_COND:
            self.evend_cond.set_names(self._var_names())
            if not self.evend_cond.rows:
                self.evend_cond.add_row()
            self.evend_cond_frame.pack(fill="x", pady=(2, 0), after=self.evend_row)
        else:
            self.evend_cond_frame.pack_forget()
        self._refresh_noaudio_hints()
        self._refresh_channel_infinite()
        self._update_infinite_correlation()   # =168

    def _update_ev_end_ui(self):
        # =168: 「無限」の相関(遷移方法・表示タイミング)はどの分岐でも通す
        self._update_infinite_correlation()
        choice = self.ev_end_var.get()
        num_widgets = (self.ev_end_field, self.ev_end_unit_label,
                       self.ev_end_tilde_label, self.ev_end_field2,
                       self.ev_end_unit2_label, self.ev_end_hint_label)
        if choice == self.EV_END_COND:
            # 変数条件: 数値欄/単位/累積ヒントを隠し、判定式エディタを出す
            for w in num_widgets:
                w.pack_forget()
            self.ev_end_states_frame.pack_forget()
            self.ev_end_cond.set_names(self._var_names())
            if not self.ev_end_cond.rows:
                self.ev_end_cond.add_row()
            self.ev_end_cond_frame.pack(fill="x", pady=(0, 4),
                                        after=self.ev_end_row)
            return
        if choice == self.EV_END_STATES:
            # 指定ステート終了: 数値欄/単位/ヒント/判定式を隠し、ステート選択を出す
            for w in num_widgets:
                w.pack_forget()
            self.ev_end_cond_frame.pack_forget()
            self.ev_end_states_frame.pack(fill="x", pady=(0, 4),
                                          after=self.ev_end_row)
            return
        if choice == self.EV_END_INFINITE:
            # =168 無限: 入力欄は何も要らない
            for w in num_widgets:
                w.pack_forget()
            self.ev_end_cond_frame.pack_forget()
            self.ev_end_states_frame.pack_forget()
            return
        # 数値系: 判定式エディタを隠し、数値欄/単位/ヒントを [欄][単位][ヒント]
        # の順で確実に戻す(cond から戻る際の順序崩れを防ぐ)
        self.ev_end_cond_frame.pack_forget()
        self.ev_end_states_frame.pack_forget()
        for w in num_widgets:
            w.pack_forget()
        is_duration = (choice == tr("合計N秒で次へ"))
        self.ev_end_field.pack(side="left", padx=2)
        self.ev_end_unit_label.pack(side="left")
        if is_duration:
            # =99: [min]秒 〜 [max]秒(max空欄=min)。入場ごとに範囲から抽選
            self.ev_end_tilde_label.pack(side="left", padx=2)
            self.ev_end_field2.pack(side="left", padx=2)
            self.ev_end_unit2_label.pack(side="left")
            self.ev_end_hint_label.configure(text=tr("(累積・範囲は抽選)"))
        else:
            self.ev_end_hint_label.configure(text=tr("(累積)"))
        self.ev_end_hint_label.pack(side="left", padx=6)
        # 時間指定は秒のみ(分/秒の単位切替は廃止)
        if choice == tr("変数指定"):
            self.ev_end_unit_label.configure(text="")
        else:
            self.ev_end_unit_label.configure(
                text=tr("秒") if is_duration else tr("回"))

    @staticmethod
    def _field_is_blank(f) -> bool:
        raw = f.get_raw()
        return isinstance(raw, str) and raw.strip() == ""

    def _on_ev_end_menu_change(self):
        choice = self.ev_end_var.get()
        if choice == tr("合計N秒で次へ"):
            if self._field_is_blank(self.ev_end_field) \
                    and self._field_is_blank(self.ev_end_field2):
                lo, hi = self.EV_END_DEFAULTS["duration"]
                self.ev_end_field.set(lo)
                self.ev_end_field2.set(hi)
        elif choice in (tr("合計N回の再生で次へ"), tr("N回のステート移行で次へ")):
            if self._field_is_blank(self.ev_end_field):
                key = "plays" if choice == tr("合計N回の再生で次へ") else "transitions"
                self.ev_end_field.set(self.EV_END_DEFAULTS[key])
        self._update_ev_end_ui()
