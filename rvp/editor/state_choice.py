"""ステート移行の選択肢エディタ(StateChoiceEditor)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from ..i18n import tr

from .common import CTkOptionMenu, MUTED, TEXT_MUTED


class StateChoiceEditor(ctk.CTkFrame):
    """=275: 「選択肢でステート移行」の編集ブロック。

    イベントの選択肢エディタ(ScenarioEditor.choice_*)と同じ項目構成だが、
    行き先が「ステート」または「イベント」になる(行ごとに種別を切り替える)。
    transition raw との往復: load(t) / collect(where) → (err, transition)。
    表示タイミングはステート基準(開始時/全チャンネル終了時/開始から指定秒)。
    """

    KIND_STATE = tr("ステート")
    KIND_EVENT = tr("イベント")
    TLIM_NONE = tr("無制限")
    TLIM_SEC = tr("時間指定")
    DFLT_FIRST = tr("先頭の選択肢へ")
    DFLT_RANDOM = tr("選択肢から等確率で抽選")
    DFLT_STATE = tr("指定ステートへ")
    DFLT_EVENT = tr("指定イベントへ")
    SHOW_START = tr("ステート開始時")
    SHOW_END = tr("ステート内の全チャンネル終了時")
    SHOW_SEC = tr("ステート開始から指定時間後")

    def __init__(self, master, owner):
        super().__init__(master, fg_color="transparent")
        self.owner = owner
        self.rows: list[dict] = []
        self.extra: dict = {}
        self.timeout_ops: list = []
        self._state_ids: list[str] = []

        self.rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_frame.pack(fill="x", pady=(2, 0))
        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.pack(fill="x", pady=(2, 0))
        self.add_btn = ctk.CTkButton(
            add_row, text=tr("＋ 選択肢を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self.add_row())
        self.add_btn.pack(side="left")
        ctk.CTkLabel(add_row, text=tr("(最小1・最大9)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        tlim_row = ctk.CTkFrame(self, fg_color="transparent")
        tlim_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(tlim_row, text=tr("タイムリミット:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.tlim_var = tk.StringVar(value=self.TLIM_NONE)
        CTkOptionMenu(
            tlim_row, variable=self.tlim_var, width=110, height=26,
            values=[self.TLIM_NONE, self.TLIM_SEC],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self.update_ui()).pack(side="left", padx=6)
        self.tsec_var = tk.StringVar(value="30")
        self.tsec_entry = ctk.CTkEntry(tlim_row, textvariable=self.tsec_var,
                                       width=42, height=26, justify="right")
        self.tsec_label = ctk.CTkLabel(tlim_row, text=tr("秒"),
                                       font=ctk.CTkFont(size=11),
                                       text_color=TEXT_MUTED)
        self.toops_btn = ctk.CTkButton(
            tlim_row, text="", width=150, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_timeout_ops)

        dflt_row = ctk.CTkFrame(self, fg_color="transparent")
        dflt_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(dflt_row, text=tr("デフォルト遷移先:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.dflt_var = tk.StringVar(value=self.DFLT_FIRST)
        CTkOptionMenu(
            dflt_row, variable=self.dflt_var, width=210, height=26,
            values=[self.DFLT_FIRST, self.DFLT_RANDOM,
                    self.DFLT_STATE, self.DFLT_EVENT],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self.update_ui()).pack(side="left", padx=6)
        self.dflt_to_var = tk.StringVar(value="")
        self.dflt_to_menu = CTkOptionMenu(
            dflt_row, variable=self.dflt_to_var, width=140, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        ctk.CTkLabel(dflt_row, text=tr("(タイムアウト時の行き先)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="right")

        stay_row = ctk.CTkFrame(self, fg_color="transparent")
        stay_row.pack(fill="x", pady=(4, 0))
        self.stay_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            stay_row, text=tr("▶▶で飛ばさない(選択されるまで待機)"),
            variable=self.stay_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
        ).pack(side="left")
        ctk.CTkLabel(stay_row,
                     text=tr("(選択肢の表示中は▶▶を無効にする。タイムアウトは進む)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        show_row = ctk.CTkFrame(self, fg_color="transparent")
        show_row.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(show_row, text=tr("表示タイミング:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.show_var = tk.StringVar(value=self.SHOW_START)
        CTkOptionMenu(
            show_row, variable=self.show_var, width=230, height=26,
            values=[self.SHOW_START, self.SHOW_END, self.SHOW_SEC],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self.update_ui()).pack(side="left", padx=6)
        self.ssec_var = tk.StringVar(value="30")
        self.ssec_entry = ctk.CTkEntry(show_row, textvariable=self.ssec_var,
                                       width=42, height=26, justify="right")
        self.ssec_label = ctk.CTkLabel(show_row, text=tr("秒"),
                                       font=ctk.CTkFont(size=11),
                                       text_color=TEXT_MUTED)
        ctk.CTkLabel(show_row,
                     text=tr("(選択/タイムアウトで即座に移行。全チャンネルが終わっても選ばれるまで待機)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

    # ---- 候補の名前 ----

    def set_state_ids(self, ids: list[str]):
        self._state_ids = list(ids) or [""]

    def _ids_for(self, kind: str) -> list[str]:
        if kind == self.KIND_EVENT:
            return self.owner._event_id_choices()
        return list(self._state_ids) or [""]

    # ---- 行 ----

    def add_row(self, label: str = "", to=None, raw: dict | None = None):
        if len(self.rows) >= 9:
            return
        to_event = isinstance(to, dict)
        to_id = to.get("event") if to_event else to
        row = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        num = ctk.CTkLabel(row, text="", width=22, font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        num.pack(side="left")
        label_var = tk.StringVar(value=label)
        label_entry = ctk.CTkEntry(row, textvariable=label_var, width=220,
                                   height=26,
                                   placeholder_text=tr("ボタンの表示テキスト"))
        label_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        kind_var = tk.StringVar(
            value=self.KIND_EVENT if to_event else self.KIND_STATE)
        entry = {"frame": row, "num": num, "label_var": label_var,
                 "label_entry": label_entry, "kind_var": kind_var,
                 "raw": dict(raw or {})}
        kind_menu = CTkOptionMenu(
            row, variable=kind_var, width=96, height=26,
            values=[self.KIND_STATE, self.KIND_EVENT],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v, e=entry: self._on_kind_change(e))
        kind_menu.pack(side="left", padx=(6, 0))
        ids = self._ids_for(kind_var.get())
        to_var = tk.StringVar(value=to_id if to_id in ids else ids[0])
        to_menu = CTkOptionMenu(row, variable=to_var, width=150, height=26,
                                values=ids,
                                fg_color=("gray75", "gray28"),
                                button_color=("gray70", "gray33"))
        to_menu.pack(side="left", padx=6)
        entry.update({"to_var": to_var, "to_menu": to_menu,
                      "kind_menu": kind_menu})
        if self.owner._has_vars():
            ops_btn = ctk.CTkButton(
                row, text="", width=86, height=26,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
                command=lambda e=entry: self._edit_row_ops(e))
            ops_btn.pack(side="left", padx=(6, 0))
            entry["ops_btn"] = ops_btn
            self._update_ops_btn(entry)
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_row(e)
                      ).pack(side="left", padx=(4, 0))
        self.rows.append(entry)
        self._renumber()

    def _on_kind_change(self, entry):
        ids = self._ids_for(entry["kind_var"].get())
        entry["to_menu"].configure(values=ids)
        if entry["to_var"].get() not in ids:
            entry["to_var"].set(ids[0])

    def _update_ops_btn(self, entry):
        if entry.get("ops_btn"):
            n = len(entry["raw"].get("ops") or [])
            entry["ops_btn"].configure(text=tr("変数({0})").format(n))

    def _edit_row_ops(self, entry):
        idx = self.rows.index(entry) + 1

        def on_ok(result):
            if result.get("ops"):
                entry["raw"]["ops"] = result["ops"]
            else:
                entry["raw"].pop("ops", None)
            self._update_ops_btn(entry)
        self.owner.open_ops_dialog(
            tr("選択肢{0}").format(idx),
            [(tr("この選択肢が選ばれた時(ops)"), "ops",
              entry["raw"].get("ops") or [])], on_ok)

    def _edit_timeout_ops(self):
        def on_ok(result):
            self.timeout_ops = result.get("on_timeout") or []
            self._update_toops_btn()
        self.owner.open_ops_dialog(
            tr("タイムアウト時"),
            [(tr("タイムアウト確定時のみ(on_timeout)"), "on_timeout",
              self.timeout_ops)], on_ok)

    def _update_toops_btn(self):
        self.toops_btn.configure(
            text=tr("時間切れ時の変数操作({0})").format(len(self.timeout_ops)))

    def _delete_row(self, entry):
        if len(self.rows) <= 1:
            self.owner._report("warn", tr("削除できません"),
                               tr("選択肢は最低1つ必要です"))
            return
        self.rows.remove(entry)
        entry["frame"].destroy()
        self._renumber()

    def _renumber(self):
        for i, e in enumerate(self.rows):
            e["num"].configure(text=f"{i + 1}.")
        self.add_btn.configure(
            state="normal" if len(self.rows) < 9 else "disabled")

    def clear_rows(self):
        for e in self.rows:
            e["frame"].destroy()
        self.rows = []

    # ---- 付随入力の表示切替 ----

    def update_ui(self):
        if self.tlim_var.get() == self.TLIM_SEC:
            self.tsec_entry.pack(side="left", padx=(6, 2))
            self.tsec_label.pack(side="left")
            if self.owner._has_vars():
                self._update_toops_btn()
                self.toops_btn.pack(side="left", padx=(10, 0))
            else:
                self.toops_btn.pack_forget()
        else:
            for w in (self.tsec_entry, self.tsec_label, self.toops_btn):
                w.pack_forget()
        d = self.dflt_var.get()
        if d in (self.DFLT_STATE, self.DFLT_EVENT):
            ids = self._ids_for(self.KIND_EVENT if d == self.DFLT_EVENT
                                else self.KIND_STATE)
            self.dflt_to_menu.configure(values=ids)
            if self.dflt_to_var.get() not in ids:
                self.dflt_to_var.set(ids[0])
            if not self.dflt_to_menu.winfo_manager():
                self.dflt_to_menu.pack(side="left", padx=(6, 0))
        else:
            self.dflt_to_menu.pack_forget()
        if self.show_var.get() == self.SHOW_SEC:
            self.ssec_entry.pack(side="left", padx=(6, 2))
            self.ssec_label.pack(side="left")
        else:
            self.ssec_entry.pack_forget()
            self.ssec_label.pack_forget()

    # ---- 往復 ----

    def load(self, t: dict | None):
        """transition raw(when.type==choice)を読み込む。None=初期状態。"""
        t = t if isinstance(t, dict) else {}
        self.clear_rows()
        self.extra = {k: v for k, v in t.items()
                      if k not in ("when", "choice", "timeout", "default",
                                   "show", "on_timeout", "skip")}
        self.timeout_ops = list(t.get("on_timeout") or [])
        self.stay_var.set(t.get("skip") == "stay")
        for ent in t.get("choice") or []:
            if isinstance(ent, dict):
                self.add_row(str(ent.get("label", "")), ent.get("to"), raw=ent)
        if not self.rows:
            self.add_row()
        traw = t.get("timeout")
        if isinstance(traw, dict):
            self.tlim_var.set(self.TLIM_SEC)
            self.tsec_var.set(f"{float(traw.get('seconds', 0)):g}")
        else:
            self.tlim_var.set(self.TLIM_NONE)
        draw = t.get("default")
        if draw == "random":
            self.dflt_var.set(self.DFLT_RANDOM)
        elif isinstance(draw, dict) and isinstance(draw.get("to"), dict) \
                and draw["to"].get("event"):
            self.dflt_var.set(self.DFLT_EVENT)
            self.dflt_to_var.set(draw["to"]["event"])
        elif isinstance(draw, dict) and isinstance(draw.get("to"), str):
            self.dflt_var.set(self.DFLT_STATE)
            self.dflt_to_var.set(draw["to"])
        else:
            self.dflt_var.set(self.DFLT_FIRST)
        sraw = t.get("show", "start" if not t else "end")
        if sraw == "start":
            self.show_var.set(self.SHOW_START)
        elif isinstance(sraw, dict):
            self.show_var.set(self.SHOW_SEC)
            self.ssec_var.set(f"{float(sraw.get('seconds', 0)):g}")
        else:
            self.show_var.set(self.SHOW_END)
        self.update_ui()

    def collect(self, where: str) -> tuple:
        """UIから transition raw を組み立てる。(err, value)。"""
        mark = self.owner._want_mark
        events = self.owner.data["events"]
        entries = []
        for i, e in enumerate(self.rows):
            label = e["label_var"].get().strip()
            to = e["to_var"].get()
            is_event = e["kind_var"].get() == self.KIND_EVENT
            if not label:
                mark(e.get("label_entry"), "error")
                return tr("{0}: 選択肢{1}の表示テキストが空です").format(where, i + 1), None
            ok = (to in events) if is_event else (to in self._state_ids)
            if not ok:
                mark(e.get("to_menu"), "error")
                return tr("{0}: 選択肢{1}の行き先が不正です").format(where, i + 1), None
            extra = {k: v for k, v in (e.get("raw") or {}).items()
                     if k not in ("label", "to")}
            entries.append({"label": label,
                            "to": {"event": to} if is_event else to,
                            **extra})
        if not 1 <= len(entries) <= 9:
            return tr("{0}: 選択肢は1〜9件にしてください").format(where), None
        value = {"when": {"type": "choice"}, "choice": entries}
        if self.tlim_var.get() == self.TLIM_SEC:
            try:
                total = float(self.tsec_var.get() or 0)
            except ValueError:
                total = 0
            if total <= 0:
                mark(self.tsec_entry, "error")
                return tr("{0}: タイムリミットの時間が不正です").format(where), None
            value["timeout"] = {"seconds": round(total, 3)}
        d = self.dflt_var.get()
        if d == self.DFLT_RANDOM:
            value["default"] = "random"
        elif d == self.DFLT_STATE:
            to = self.dflt_to_var.get()
            if to not in self._state_ids:
                mark(self.dflt_to_menu, "error")
                return tr("{0}: デフォルト遷移先のステートを選択してください").format(where), None
            value["default"] = {"to": to}
        elif d == self.DFLT_EVENT:
            to = self.dflt_to_var.get()
            if to not in events:
                mark(self.dflt_to_menu, "error")
                return tr("{0}: デフォルト遷移先のイベントを選択してください").format(where), None
            value["default"] = {"to": {"event": to}}
        show = self.show_var.get()
        if show == self.SHOW_START:
            value["show"] = "start"
        elif show == self.SHOW_SEC:
            try:
                total = float(self.ssec_var.get() or 0)
            except ValueError:
                mark(self.ssec_entry, "error")
                return tr("{0}: 表示タイミングの時間が不正です").format(where), None
            value["show"] = {"seconds": round(total, 3)}
        # "end"(全チャンネル終了時)は既定なので省略
        if self.stay_var.get():
            value["skip"] = "stay"
        if self.timeout_ops:
            value["on_timeout"] = self.timeout_ops
        for k, v in self.extra.items():
            value.setdefault(k, v)
        return None, value

    def targets(self) -> tuple:
        """現在のUI上の (ステート宛てID集合, イベント宛てID集合)。"""
        st, ev = set(), set()
        for e in self.rows:
            (ev if e["kind_var"].get() == self.KIND_EVENT else st).add(
                e["to_var"].get())
        return st, ev
