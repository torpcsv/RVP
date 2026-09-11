"""シナリオ編集: 入力部品(Tooltip / VarRefField / CondListEditor / 区間欄の検証)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from .. import appfont
from ..i18n import tr

from .common import CTkOptionMenu, MUTED, _COND_OPS, _parse_num_text
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class Tooltip:
    """マウスオーバーで全文を表示する簡易ツールチップ(=101)。

    text_getter が空文字を返す間は表示しない(=省略されていない時は
    出さない)。ウィジェットの使い回し(ItemRow.reload)にも追従する。
    """

    def __init__(self, widget, text_getter, delay_ms: int = 450):
        self.widget = widget
        self.text_getter = text_getter
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _e=None):
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay_ms, self._show)
        except Exception:
            pass

    def _on_leave(self, _e=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        try:
            text = self.text_getter() or ""
        except Exception:
            text = ""
        if not text or self._tip is not None:
            return
        try:
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 18
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            tk.Label(tip, text=text, justify="left",
                     background="#2b2b2b", foreground="#f2f2f2",
                     relief="solid", borderwidth=1,
                     font=(appfont.FAMILY, 10), padx=6, pady=3).pack()
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class VarRefField(ctk.CTkFrame):
    """数値欄+変数指定トグル。

    値は「数値(テキスト)」または変数参照 {"var": 名前}。
    「x」トグルON で入力欄が変数選択メニューに切り替わる。
    利用可能な変数が無いときはトグル自体を表示しない(密集化対策)。
    """

    def __init__(self, master, width=54, placeholder="", on_change=None):
        super().__init__(master, fg_color="transparent")
        self.on_change = on_change
        self.use_var = False
        self.names: list[str] = []
        self._state = "normal"
        self.text_var = tk.StringVar(value="")
        self.entry = ctk.CTkEntry(self, textvariable=self.text_var,
                                  width=width, height=26,
                                  placeholder_text=placeholder)
        self.sel_var = tk.StringVar(value="")
        # =64(起動短縮): 変数選択メニューとトグルボタンは**使うときに作る**。
        # 編集画面には VarRefField が数十個並ぶが、その多くは変数を使わない
        # (=メニュー不要)。CTkOptionMenu/CTkButton の生成は1個あたり
        # 10ms前後かかるため、遅延生成が開く速度に直接効く。
        self._width = width
        self._menu = None
        self._toggle_btn = None
        self._sync()

    # ---- 遅延生成(=64) ----

    @property
    def menu(self):
        """変数選択メニュー(初回アクセス時に生成)。"""
        if self._menu is None:
            self._menu = CTkOptionMenu(
                self, variable=self.sel_var,
                width=max(self._width + 40, 90), height=26,
                values=self.names or [""],
                fg_color=("gray75", "gray28"),
                button_color=("gray70", "gray33"))
            if self._state != "normal":
                self._menu.configure(state=self._state)
        return self._menu

    @property
    def toggle_btn(self):
        """変数指定トグル(初回アクセス時に生成)。"""
        if self._toggle_btn is None:
            self._toggle_btn = ctk.CTkButton(
                self, text="x", width=26, height=26,
                font=ctk.CTkFont(size=12, slant="italic", weight="bold"),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray30", "gray70"),
                hover_color=("gray85", "gray28"),
                command=self._toggle)
            if self._state != "normal":
                self._toggle_btn.configure(state=self._state)
        return self._toggle_btn

    # ---- API ----

    def set_names(self, names: list[str]):
        """選択できる変数名を設定する。空なら静的入力のみ(トグル非表示)。"""
        self.names = list(names)
        if self.names:
            if self._menu is not None:
                self._menu.configure(values=self.names)
            if self.sel_var.get() not in self.names:
                self.sel_var.set(self.names[0])
        else:
            self.use_var = False
        self._sync()

    def set(self, value):
        """値を反映する。dict={"var":..}なら変数モード、それ以外は静的テキスト。"""
        if isinstance(value, dict):
            self.use_var = True
            name = value.get("var", "")
            if name and name not in self.names:
                # 宣言に無い名前でも表示は維持する(保存時の検証で捕捉)
                self.names.append(name)
                if self._menu is not None:
                    self._menu.configure(values=self.names)
            self.sel_var.set(name)
        else:
            self.use_var = False
            if value is None:
                self.text_var.set("")
            elif isinstance(value, float):
                self.text_var.set(f"{value:g}")
            else:
                self.text_var.set(str(value))
        self._sync()

    def get_raw(self):
        """{"var": 名前}(変数モード) または 入力テキスト(str) を返す。"""
        if self.use_var:
            return {"var": self.sel_var.get()}
        return self.text_var.get()

    def get_text(self) -> str:
        return self.text_var.get()

    def set_state(self, state: str):
        self._state = state
        self.entry.configure(state=state)
        # 未生成のものは生成時に反映する(=64)
        if self._menu is not None:
            self._menu.configure(state=state)
        if self._toggle_btn is not None:
            self._toggle_btn.configure(state=state)

    # ---- 内部 ----

    def _toggle(self):
        self.use_var = not self.use_var
        self._sync()
        if self.on_change:
            self.on_change()

    def _sync(self):
        # =64: 未生成のメニュー/トグルは「隠れている」のと同じなので触らない
        # =282: 並びは常に「[入力欄 or 変数メニュー] [x]」(x は右)。従来は
        # 入力欄/メニューを pack_forget→pack し直すたびに pack 順の末尾へ
        # 回り、x を1回押した後は x が左へ移動していた(1回目のトグルで
        # 初めて生成される x は入力欄の後ろに並ぶが、以後の入れ替えで
        # 入力欄/メニューが x の後ろへ回る)。x を必ず一度外して最後に
        # 付け直すことで並びを固定する。
        if self._toggle_btn is not None:
            self._toggle_btn.pack_forget()
        if self.use_var and self.names:
            self.entry.pack_forget()
            self.menu.pack(side="left")
            self.toggle_btn.configure(fg_color=_clr.ACCENT, border_color=_clr.ACCENT,
                                      text_color="white")
        else:
            if self._menu is not None:
                self._menu.pack_forget()
            self.entry.pack(side="left")
            if self._toggle_btn is not None:
                self._toggle_btn.configure(fg_color="transparent",
                                           border_color=MUTED,
                                           text_color=("gray30", "gray70"))
        if self.names:
            self.toggle_btn.pack(side="left", padx=(3, 0))


class CondListEditor(ctk.CTkFrame):
    """判定式(AND条件)リストの編集部品。watch・変数分岐(cond)で共用。

    各行: [変数▼] [演算子▼] [値(VarRefField)] [✕]。最低1行。
    """

    def __init__(self, master, min_rows: int = 1):
        super().__init__(master, fg_color="transparent")
        self.min_rows = min_rows
        self.rows: list[dict] = []
        self.all_names: list[str] = []
        self.rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_frame.pack(fill="x")
        self.add_btn = ctk.CTkButton(
            self, text=tr("＋AND条件"), width=90, height=22,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self.add_row())
        self.add_btn.pack(anchor="w", pady=(2, 0))

    def set_names(self, names: list[str]):
        self.all_names = list(names)
        for r in self.rows:
            r["var_menu"].configure(values=self.all_names or [""])
            r["value"].set_names(self.all_names)

    def load(self, conds_raw: list):
        for r in self.rows:
            r["frame"].destroy()
        self.rows = []
        for c in conds_raw or []:
            if isinstance(c, dict):
                self.add_row(c)
        while len(self.rows) < self.min_rows:
            self.add_row()

    def add_row(self, raw: dict | None = None):
        raw = raw or {}
        row = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        var_var = tk.StringVar(
            value=raw.get("var") or (self.all_names[0] if self.all_names else ""))
        var_menu = CTkOptionMenu(
            row, variable=var_var, width=110, height=24,
            values=self.all_names or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        var_menu.pack(side="left")
        op_var = tk.StringVar(value=raw.get("op") if raw.get("op") in _COND_OPS
                              else "==")
        CTkOptionMenu(
            row, variable=op_var, width=58, height=24, values=list(_COND_OPS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
        ).pack(side="left", padx=4)
        value = VarRefField(row, width=70, placeholder=tr("値"))
        value.set_names(self.all_names)
        value.set(raw.get("value") if "value" in raw else "")
        value.pack(side="left")
        entry = {"frame": row, "var_var": var_var, "var_menu": var_menu,
                 "op_var": op_var, "value": value}
        ctk.CTkButton(row, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_row(e)
                      ).pack(side="left", padx=(4, 0))
        self.rows.append(entry)

    def _delete_row(self, entry):
        if len(self.rows) <= self.min_rows:
            return
        self.rows.remove(entry)
        entry["frame"].destroy()

    def collect(self, where: str, string_vars: set | None = None, mark=None):
        """(エラーメッセージ, 判定式リスト) を返す。

        string_vars: 文字列型の変数名集合(値をテキストのまま保持する判定に使う)。
        mark: 不正フィールドを着色するコールバック(通常 ScenarioEditor._want_mark)。
              変数未選択なら変数メニュー、値が不正なら値欄(VarRefField)を渡す。
        """
        string_vars = string_vars or set()
        out = []
        for i, r in enumerate(self.rows):
            name = r["var_var"].get()
            if not name:
                if mark:
                    mark(r["var_menu"], "error")
                return tr("{0}: 条件{1}の変数を選択してください").format(where, i + 1), None
            v = r["value"].get_raw()
            if isinstance(v, str):
                if name in string_vars:
                    pass  # 文字列変数はテキストをそのまま比較値にする
                else:
                    try:
                        v = _parse_num_text(v)
                    except ValueError:
                        if mark:
                            mark(r["value"], "error")
                        return tr("{0}: 条件{1}の値が不正です").format(where, i + 1), None
            out.append({"var": name, "op": r["op_var"].get(), "value": v})
        if not out:
            return tr("{0}: 条件を1つ以上指定してください").format(where), None
        return None, out


def _range_disp(v) -> str:
    """区間欄の表示文字列。None/非数値は空欄(=指定なし)。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return ""
    return f"{float(v):g}"


def _raw_range(raw) -> tuple[str, str]:
    """dict の "range" を (開始秒の表示, 終了秒の表示) にする(=59)。"""
    r = raw.get("range") if isinstance(raw, dict) else None
    if not isinstance(r, dict):
        return "", ""
    return _range_disp(r.get("start")), _range_disp(r.get("end"))


def _validate_range_fields(start_var, end_var, start_widget, end_widget,
                           disp_name: str, owner) -> str | None:
    """区間欄(開始秒/終了秒)を検証する(=59。アイテム/トラック共通)。

    空欄=指定なし。数値でない/開始が負/終了<=開始 はエラーにして、
    原因のフィールドを赤くする(=8の標準動作)。
    """
    vals = {}
    for key, var, widget in (("start", start_var, start_widget),
                             ("end", end_var, end_widget)):
        txt = var.get().strip()
        if not txt:
            vals[key] = None
            continue
        try:
            vals[key] = float(txt)
        except ValueError:
            if owner is not None:
                owner._want_mark(widget, "error")
            return tr("{0}: 区間は秒数(数値)で指定してください").format(disp_name)
    if vals["start"] is not None and vals["start"] < 0:
        if owner is not None:
            owner._want_mark(start_widget, "error")
        return tr("{0}: 区間の開始は0以上にしてください").format(disp_name)
    if vals["end"] is not None and vals["end"] <= (vals["start"] or 0.0):
        if owner is not None:
            owner._want_mark(end_widget, "error")
        return tr("{0}: 区間の終了は開始より後にしてください").format(disp_name)
    return None


def _range_dict(start_var, end_var) -> dict | None:
    """区間欄の値を JSON の "range" 辞書にする(=59)。空欄/0だけなら None。"""
    out = {}
    for key, var in (("start", start_var), ("end", end_var)):
        txt = var.get().strip()
        if not txt:
            continue
        try:
            v = float(txt)
        except ValueError:
            continue
        if key == "start" and v == 0:
            continue          # 0=先頭は既定なのでキーを書かない
        out[key] = v
    return out or None
