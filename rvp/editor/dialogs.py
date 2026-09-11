"""シナリオ編集: 小ダイアログ群(変数操作/紹介文/背景/変数一覧/他からコピー/インポート)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import tkinter as tk
from ..winstate import WindowMemory
from ..i18n import tr

from .common import (CTkOptionMenu, MSG_ERROR, MUTED, TEXT_MUTED, _COND_OPS,
    _dialog_initialdir, _front_window, _parse_num_text, _place_popup,
    _remember_dialog_dir)
from .fields import CondListEditor, VarRefField
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg


class OpsDialog(ctk.CTkToplevel):
    """変数操作リストの編集ダイアログ。

    sections: [(見出し, キー, 操作リストraw)] を並べて編集する
    (アイテムのようにon_play/on_completeをまとめて1ダイアログで扱える)。
    OKで self.result = {キー: 操作リスト}(空リスト=削除の意)。キャンセルはNone。
    """

    def __init__(self, master, title: str, sections, all_names, numeric_names,
                 string_vars: set):
        super().__init__(master)
        self.title(tr("変数操作 - {0}").format(title))
        self.geometry("640x460")
        _place_popup(self, master, 640, 460)
        self.result = None
        self.all_names = list(all_names)
        self.numeric_names = list(numeric_names)
        self.string_vars = set(string_vars)
        self.sections: list[dict] = []

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        ctk.CTkLabel(body, text=tr("上から順に実行されます。加算/乗算/乱数は数値変数のみ、負の値で減算。乱数は最小〜最大の整数を代入。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x")
        # =126: 条件式(eval)の説明
        ctk.CTkLabel(body, text=tr("条件式は成立で1、不成立で0を代入します(対象は数値変数のみ)。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x", pady=(0, 4))

        for sec_title, key, ops_raw in sections:
            sec = {"key": key, "rows": []}
            ctk.CTkLabel(body, text=sec_title,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w").pack(fill="x", pady=(6, 0))
            sec["rows_frame"] = ctk.CTkFrame(body, fg_color="transparent")
            sec["rows_frame"].pack(fill="x")
            ctk.CTkButton(
                body, text=tr("＋ 操作を追加"), width=110, height=24,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
                command=lambda s=sec: self._add_row(s)
            ).pack(anchor="w", pady=(2, 2))
            self.sections.append(sec)
            for op in ops_raw or []:
                if isinstance(op, dict):
                    self._add_row(sec, op)
            self._refit_section(sec)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("OK"), width=90, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._ok).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=4)
        # 入力エラーはダイアログ内にインライン表示(メッセージボックスは使わない)
        self.err_label = ctk.CTkLabel(
            btns, text="", font=ctk.CTkFont(size=12), text_color=MSG_ERROR,
            justify="left", anchor="w", wraplength=380)
        self.err_label.pack(side="left", padx=4)

        _front_window(self)
        self.grab_set()

    def _add_row(self, sec, raw: dict | None = None):
        raw = raw or {}
        if "roll" in raw:
            kind = "roll"
        elif "add" in raw:
            kind = "add"
        elif "mul" in raw:
            kind = "mul"
        elif "eval" in raw:
            kind = "eval"
        else:
            kind = "set"
        target = raw.get(kind) or ""
        row = ctk.CTkFrame(sec["rows_frame"], fg_color="transparent")
        row.pack(fill="x", pady=1)
        kind_label = {"add": tr("加算"), "mul": tr("乗算"),
                      "roll": tr("乱数"),
                      "eval": tr("条件式")}.get(kind, tr("セット"))
        kind_var = tk.StringVar(value=kind_label)
        entry = {"frame": row, "kind_var": kind_var}
        CTkOptionMenu(
            row, variable=kind_var, width=84, height=24,
            values=[tr("セット"), tr("加算"), tr("乗算"), tr("乱数"),
                    tr("条件式")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v, e=entry: self._update_row(e),
        ).pack(side="left")
        tgt_var = tk.StringVar(
            value=target or (self.all_names[0] if self.all_names else ""))
        tgt_menu = CTkOptionMenu(
            row, variable=tgt_var, width=110, height=24,
            values=self.all_names or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        tgt_menu.pack(side="left", padx=4)
        ctk.CTkLabel(row, text="←", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        # 値エリア: set/add=単一の値 / roll=min〜max の2欄(種別で出し分け)
        val_area = ctk.CTkFrame(row, fg_color="transparent")
        val_area.pack(side="left", padx=4)
        value = VarRefField(val_area, width=80, placeholder=tr("値"))
        value.set_names(self.all_names)
        value.set(raw.get("value") if "value" in raw else "")
        roll_min = VarRefField(val_area, width=64, placeholder=tr("最小"))
        roll_min.set_names(self.numeric_names)
        roll_min.set(raw.get("min") if "min" in raw else "")
        roll_sep = ctk.CTkLabel(val_area, text="〜", font=ctk.CTkFont(size=12),
                                text_color=TEXT_MUTED)
        roll_max = VarRefField(val_area, width=64, placeholder=tr("最大"))
        roll_max.set_names(self.numeric_names)
        roll_max.set(raw.get("max") if "max" in raw else "")
        # =126 条件式(eval): [変数▼][演算子▼][値] の3点(値欄は共用)
        when = raw.get("when") if isinstance(raw.get("when"), dict) else {}
        cond_var = tk.StringVar(
            value=when.get("var")
            or (self.all_names[0] if self.all_names else ""))
        cond_menu = CTkOptionMenu(
            val_area, variable=cond_var, width=100, height=24,
            values=self.all_names or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        cond_op = tk.StringVar(value=when.get("op")
                               if when.get("op") in _COND_OPS else "==")
        cond_op_menu = CTkOptionMenu(
            val_area, variable=cond_op, width=58, height=24,
            values=list(_COND_OPS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        if kind == "eval" and "value" in when:
            value.set(when.get("value"))
        entry.update({"tgt_var": tgt_var, "tgt_menu": tgt_menu, "value": value,
                      "roll_min": roll_min, "roll_sep": roll_sep,
                      "roll_max": roll_max,
                      "cond_var": cond_var, "cond_menu": cond_menu,
                      "cond_op": cond_op, "cond_op_menu": cond_op_menu})
        ctk.CTkButton(row, text="↑", width=24, height=24,
                      fg_color="transparent", text_color=TEXT_MUTED,
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry, s=sec: self._move_up(s, e)
                      ).pack(side="left", padx=(6, 0))
        ctk.CTkButton(row, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry, s=sec: self._delete_row(s, e)
                      ).pack(side="left")
        sec["rows"].append(entry)
        self._update_row(entry)
        self._refit_section(sec)

    def _row_kind(self, entry) -> str:
        v = entry["kind_var"].get()
        if v == tr("加算"):
            return "add"
        if v == tr("乗算"):
            return "mul"
        if v == tr("乱数"):
            return "roll"
        if v == tr("条件式"):
            return "eval"
        return "set"

    def _update_row(self, entry):
        """加算/乗算/乱数/条件式は数値変数のみ対象。roll時は min〜max、
        eval時は [変数][演算子][値]、それ以外は単一値欄。"""
        kind = self._row_kind(entry)
        if kind in ("add", "mul", "roll", "eval"):
            names = self.numeric_names or [""]
            entry["tgt_menu"].configure(values=names)
            if entry["tgt_var"].get() not in names:
                entry["tgt_var"].set(names[0])
        else:
            entry["tgt_menu"].configure(values=self.all_names or [""])
        for w in (entry["value"], entry["roll_min"], entry["roll_sep"],
                  entry["roll_max"], entry["cond_menu"],
                  entry["cond_op_menu"]):
            w.pack_forget()
        if kind == "roll":
            entry["roll_min"].pack(side="left")
            entry["roll_sep"].pack(side="left", padx=2)
            entry["roll_max"].pack(side="left")
        elif kind == "eval":
            # =126: x ← (a op 値) の3点
            entry["cond_menu"].pack(side="left")
            entry["cond_op_menu"].pack(side="left", padx=2)
            entry["value"].pack(side="left")
        else:
            entry["value"].pack(side="left")

    def _move_up(self, sec, entry):
        i = sec["rows"].index(entry)
        if i == 0:
            return
        sec["rows"][i - 1], sec["rows"][i] = sec["rows"][i], sec["rows"][i - 1]
        for e in sec["rows"]:
            e["frame"].pack_forget()
        for e in sec["rows"]:
            e["frame"].pack(fill="x", pady=1)

    def _refit_section(self, sec):
        """操作0件のセクションの rows_frame を潰す(空フレームの余白を防ぐ)。

        VarsDialog の _refit_vars/_refit_watch と同一手法。見出しと
        「＋操作を追加」ボタンの間に空フレームの既定高さぶんの隙間が出るのを防ぐ。
        """
        if sec["rows"]:
            sec["rows_frame"].pack_propagate(True)
        else:
            sec["rows_frame"].pack_propagate(False)
            sec["rows_frame"].configure(height=1)

    def _delete_row(self, sec, entry):
        sec["rows"].remove(entry)
        entry["frame"].destroy()
        self._refit_section(sec)

    def collect(self):
        """(エラーメッセージ, {キー: 操作リスト}) を返す。"""
        result = {}
        for sec in self.sections:
            ops = []
            for i, e in enumerate(sec["rows"]):
                kind = self._row_kind(e)
                name = e["tgt_var"].get()
                if not name:
                    return tr("操作{0}: 対象の変数を選択してください").format(i + 1), None
                if kind == "roll":
                    def _numval(field, lbl):
                        raw = field.get_raw()
                        if isinstance(raw, str):
                            return _parse_num_text(raw)   # ValueError で捕捉
                        return raw
                    try:
                        mn = _numval(e["roll_min"], "min")
                        mx = _numval(e["roll_max"], "max")
                    except ValueError:
                        return tr("操作{0}: 乱数の最小/最大が不正です").format(i + 1), None
                    ops.append({"roll": name, "min": mn, "max": mx})
                    continue
                if kind == "eval":
                    # =126 条件式: when={"var","op","value"} を組み立てる
                    cv = e["cond_var"].get()
                    if not cv:
                        return tr("操作{0}: 条件式の変数を選択してください").format(i + 1), None
                    v = e["value"].get_raw()
                    if isinstance(v, str):
                        if cv in self.string_vars:
                            pass   # 文字列変数との比較はテキストのまま
                        else:
                            try:
                                v = _parse_num_text(v)
                            except ValueError:
                                return tr("操作{0}: 条件式の値が不正です").format(i + 1), None
                    ops.append({"eval": name,
                                "when": {"var": cv, "op": e["cond_op"].get(),
                                         "value": v}})
                    continue
                v = e["value"].get_raw()
                if isinstance(v, str):
                    if kind == "set" and name in self.string_vars:
                        pass   # 文字列変数へのセットはテキストのまま
                    else:
                        try:
                            v = _parse_num_text(v)
                        except ValueError:
                            return tr("操作{0}: 値が不正です").format(i + 1), None
                ops.append({kind: name, "value": v})
            result[sec["key"]] = ops
        return None, result

    def _ok(self):
        err, result = self.collect()
        if err:
            self.err_label.configure(text=err)
            _pkg().MESSAGE_LOG.append(("error", (tr("編集エラー"), err)))
            return
        self.result = result
        self.destroy()


class DetailDialog(ctk.CTkToplevel):
    """紹介文(シナリオ選択時に表示される説明文)の編集ダイアログ(=250)。

    常設だった編集画面上部の説明テキストボックスを、ツールバーの
    「紹介文」ボタンから開くダイアログへ移した(ユーザー要望=記入後は
    ほぼ触らずデッドスペースになっていたため)。

    - モーダル(変数/監視と同じ。非モーダルだと未確定テキストと
      ファイル保存の競合が生まれる=AQ2)。
    - 「保存」=**メモリへの反映のみ**(AQ1)。JSONファイルへは従来どおり
      編集画面の「保存」で書き出す。「キャンセル」「✕」は破棄。
    - 位置・サイズは WindowMemory("detail") で記憶する(他画面と同じ)。
    """

    def __init__(self, master, text: str):
        super().__init__(master)
        self.title(tr("紹介文"))
        self.result: str | None = None   # None=キャンセル / str=保存
        self.geometry("560x360")
        # 位置・サイズの記憶(=115の機構)。保存値が無ければ従来の
        # ダイアログ配置(編集画面の中央やや左上=132)へ。
        self.winmem = WindowMemory(self, "detail")
        if not self.winmem.restore():
            _place_popup(self, master, 560, 360)
        self.winmem.watch()

        hint = ctk.CTkLabel(
            self, text=tr("シナリオ選択時に表示される紹介文です(改行可)"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED, anchor="w")
        hint.pack(fill="x", padx=14, pady=(12, 4))
        # シナリオタブの「シナリオ内容」と同じテキストエリア(編集可)
        self.detail_box = ctk.CTkTextbox(
            self, font=ctk.CTkFont(size=12), wrap="word")
        self.detail_box.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.detail_box.insert("1.0", text or "")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("保存"), width=90, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._on_save).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._on_cancel).pack(side="right", padx=4)
        # ✕はキャンセル扱い(ウィンドウ記憶だけ保存して閉じる)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        _front_window(self)
        self.grab_set()
        self.detail_box.focus_set()

    def _close(self):
        try:
            self.winmem.save_now()
        except Exception:
            pass
        self.destroy()

    def _on_save(self):
        self.result = self.detail_box.get("1.0", "end-1c")
        self._close()

    def _on_cancel(self):
        self.result = None
        self._close()


class BackgroundDialog(ctk.CTkToplevel):
    """背景イラスト(=262)の設定ダイアログ(モーダル)。

    再生画面全体の背景に敷くイラスト(トップレベル "background")を
    ファイル選択・クリア・暗さ(%)で編集する。「保存」=メモリ(self.data)への
    反映のみで、JSONファイルへは編集画面の「保存」で書き出す(紹介文と
    同じ作法)。キャンセル/✕は破棄。

    self.result:
      None                        = キャンセル(変更なし)
      {"background": None}        = クリア(キーごと削除)
      {"background": {"file": p, "dim": n}} = 設定
    """

    FILETYPES_EXT = "*.png *.jpg *.jpeg *.webp *.bmp *.gif"

    def __init__(self, master, raw, base_dir: str):
        super().__init__(master)
        self.title(tr("背景イラスト"))
        self.result = None
        self.base_dir = base_dir
        self.geometry("560x240")
        _place_popup(self, master, 560, 240)
        self.transient(master)

        # 現在値を分解(文字列/辞書の2書式。dim省略=40)
        self._file = ""
        dim = 40
        if isinstance(raw, str):
            self._file = raw
        elif isinstance(raw, dict):
            f = raw.get("file")
            self._file = f if isinstance(f, str) else ""
            d = raw.get("dim", 40)
            if isinstance(d, (int, float)) and not isinstance(d, bool):
                dim = int(round(d))

        hint = ctk.CTkLabel(
            self, text=tr("再生画面全体の背景に表示するイラストです(png/jpg等)。"
                          "表示のON/OFFは視聴する人がメイン画面の設定で"
                          "切り替えられます。"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED,
            wraplength=520, justify="left", anchor="w")
        hint.pack(fill="x", padx=14, pady=(12, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=14)
        self.file_label = ctk.CTkLabel(
            row, text="", font=ctk.CTkFont(size=12), anchor="w",
            wraplength=300, justify="left")
        self.file_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text=tr("クリア(背景なし)"), width=120, height=28,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._clear).pack(side="right", padx=(6, 0))
        ctk.CTkButton(row, text=tr("画像を選ぶ…"), width=110, height=28,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._choose).pack(side="right")

        dim_row = ctk.CTkFrame(self, fg_color="transparent")
        dim_row.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(dim_row, text=tr("暗さ(%)"), width=70, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.dim_var = tk.StringVar(value=str(dim))
        self.dim_entry = ctk.CTkEntry(
            dim_row, textvariable=self.dim_var, width=64, height=28,
            justify="right", font=ctk.CTkFont(size=13))
        self.dim_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            dim_row,
            text=tr("0=画像を最も強く表示 〜 100=真っ黒(既定40。40より下げるほど画像の主張が強くなります)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            wraplength=360, justify="left", anchor="w",
        ).pack(side="left", fill="x", expand=True)

        self.err_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e05a5a", anchor="w")
        self.err_label.pack(fill="x", padx=14, pady=(4, 0))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(4, 12), side="bottom")
        ctk.CTkButton(btns, text=tr("保存"), width=90, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._on_save).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._on_cancel).pack(side="right", padx=4)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._refresh_file_label()
        _front_window(self)
        self.grab_set()

    def _refresh_file_label(self):
        if self._file:
            self.file_label.configure(
                text=os.path.basename(self._file), text_color=TEXT_MUTED)
        else:
            self.file_label.configure(text=tr("(なし)"),
                                      text_color=TEXT_MUTED)

    def _choose(self):
        kwargs = {}
        start = self._file if os.path.isabs(self._file) \
            else os.path.join(self.base_dir, self._file)
        # =270: 優先順は「設定済みファイルのフォルダ > 前回選択フォルダ
        # (編集画面共有・永続) > シナリオフォルダ」。差し替え時は元画像の
        # 場所が開くほうが自然なため、設定済みフォルダを最優先にする。
        init = os.path.dirname(start) if self._file \
            else _dialog_initialdir(self.base_dir)
        if init and os.path.isdir(init):
            kwargs["initialdir"] = init
        path = _pkg().filedialog.askopenfilename(
            parent=self, title=tr("背景画像を選択"),
            filetypes=[(tr("画像ファイル"), self.FILETYPES_EXT),
                       (tr("すべてのファイル"), "*.*")],
            **kwargs)
        if not path:
            return
        _remember_dialog_dir(path)
        self._file = os.path.normpath(path)
        self.err_label.configure(text="")
        self._refresh_file_label()

    def _clear(self):
        self._file = ""
        self.err_label.configure(text="")
        self._refresh_file_label()

    def _on_save(self):
        if not self._file:
            self.result = {"background": None}
            self.destroy()
            return
        raw = self.dim_var.get().strip()
        try:
            dim = int(raw)
            if not (0 <= dim <= 100):
                raise ValueError
        except ValueError:
            self.err_label.configure(
                text=tr("暗さ(%)は 0〜100 の整数で指定してください"))
            return
        self.result = {"background": {"file": self._file, "dim": dim}}
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


class VarsDialog(ctk.CTkToplevel):
    """変数宣言と監視(watch)の編集ダイアログ。

    OKで self.result = {"vars": {...} or None, "watch": [...] or None}。
    キャンセルはNone。
    """

    def __init__(self, master, vars_raw: dict, watch_raw: list, event_ids):
        super().__init__(master)
        self.title(tr("変数と監視の管理"))
        self.geometry("760x640")
        _place_popup(self, master, 760, 640)
        self.result = None
        self.event_ids = list(event_ids)
        self.var_rows: list[dict] = []
        self.watch_rows: list[dict] = []

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self.body = body

        ctk.CTkLabel(body, text=tr("変数宣言"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(
            body,
            text=tr("型は「数値/文字列」で選択。最小/最大は数値のみ(全操作の結果がこの範囲に収まる)。\n"
                    "名前の変更・削除は、その変数を使う操作・条件を自動では直しません(保存時の検証でエラーになります)。"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, justify="left",
            anchor="w").pack(fill="x", pady=(0, 4))
        hdr = ctk.CTkFrame(body, fg_color="transparent")
        hdr.pack(fill="x")
        for text, w in ((tr("名前"), 140), (tr("型"), 90), (tr("初期値"), 90),
                        (tr("最小"), 70), (tr("最大"), 70)):
            ctk.CTkLabel(hdr, text=text, width=w, anchor="w",
                         font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                         ).pack(side="left", padx=(0, 6))
        # height=0: 変数0件の空フレームが CTkFrame 既定の200pxを確保して
        # 「＋変数を追加」ボタンが下に押し下げられる(余白)のを防ぐ。
        self.vars_frame = ctk.CTkFrame(body, fg_color="transparent", height=0)
        self.vars_frame.pack(fill="x")
        ctk.CTkButton(
            body, text=tr("＋ 変数を追加"), width=110, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_var_row()
        ).pack(anchor="w", pady=(4, 10))

        ctk.CTkLabel(body, text=tr("監視(watch)"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(
            body,
            text=tr("変数条件が成立した瞬間に指定イベントへ遷移します(シナリオ全体で有効)。\n"
                    "条件は変数操作の直後に評価され、遷移先のイベント再生中は評価されません。"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, justify="left",
            anchor="w").pack(fill="x", pady=(0, 4))
        self.watch_frame = ctk.CTkFrame(body, fg_color="transparent", height=0)
        self.watch_frame.pack(fill="x")
        self.watch_add_btn = ctk.CTkButton(
            body, text=tr("＋ 監視を追加"), width=110, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_watch_row())
        self.watch_add_btn.pack(anchor="w", pady=(4, 4))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("OK"), width=90, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._ok).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=4)
        # 入力エラーはダイアログ内にインライン表示(メッセージボックスは使わない)
        self.err_label = ctk.CTkLabel(
            btns, text="", font=ctk.CTkFont(size=12), text_color=MSG_ERROR,
            justify="left", anchor="w", wraplength=440)
        self.err_label.pack(side="left", padx=4)

        for name, decl in (vars_raw or {}).items():
            self._add_var_row(name, decl)
        for w in (watch_raw or []):
            if isinstance(w, dict):
                self._add_watch_row(w)
        # 0件なら空フレームを潰して「追加」ボタンを直下へ寄せる
        self._refit_vars()
        self._refit_watch()

        _front_window(self)
        self.grab_set()

    def _refit_vars(self):
        """変数0件のとき vars_frame を潰す(空フレームの200px余白を防ぐ)。"""
        if self.var_rows:
            self.vars_frame.pack_propagate(True)
        else:
            self.vars_frame.pack_propagate(False)
            self.vars_frame.configure(height=1)

    def _refit_watch(self):
        if self.watch_rows:
            self.watch_frame.pack_propagate(True)
        else:
            self.watch_frame.pack_propagate(False)
            self.watch_frame.configure(height=1)

    # ---- 変数宣言 ----

    def _add_var_row(self, name: str = "", decl=None):
        if isinstance(decl, dict):
            init, vmin, vmax = decl.get("init"), decl.get("min"), decl.get("max")
            extra = {k: v for k, v in decl.items()
                     if k not in ("init", "min", "max")}
        else:
            init, vmin, vmax, extra = decl, None, None, {}
        is_str = isinstance(init, str)
        row = ctk.CTkFrame(self.vars_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        name_var = tk.StringVar(value=name)
        ctk.CTkEntry(row, textvariable=name_var, width=140, height=26
                     ).pack(side="left", padx=(0, 6))
        type_var = tk.StringVar(value=tr("文字列") if is_str else tr("数値"))
        entry = {"frame": row, "name_var": name_var, "type_var": type_var,
                 "extra": extra}
        CTkOptionMenu(
            row, variable=type_var, width=90, height=26,
            values=[tr("数値"), tr("文字列")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v, e=entry: self._update_var_row(e),
        ).pack(side="left", padx=(0, 6))
        init_var = tk.StringVar(
            value="" if init is None else
            (f"{init:g}" if isinstance(init, float) else str(init)))
        ctk.CTkEntry(row, textvariable=init_var, width=90, height=26
                     ).pack(side="left", padx=(0, 6))
        min_var = tk.StringVar(value="" if vmin is None else
                               (f"{vmin:g}" if isinstance(vmin, float) else str(vmin)))
        min_entry = ctk.CTkEntry(row, textvariable=min_var, width=70, height=26,
                                 placeholder_text=tr("なし"))
        min_entry.pack(side="left", padx=(0, 6))
        max_var = tk.StringVar(value="" if vmax is None else
                               (f"{vmax:g}" if isinstance(vmax, float) else str(vmax)))
        max_entry = ctk.CTkEntry(row, textvariable=max_var, width=70, height=26,
                                 placeholder_text=tr("なし"))
        max_entry.pack(side="left", padx=(0, 6))
        entry.update({"init_var": init_var, "min_var": min_var,
                      "max_var": max_var, "min_entry": min_entry,
                      "max_entry": max_entry})
        ctk.CTkButton(row, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_var_row(e)
                      ).pack(side="left")
        self.var_rows.append(entry)
        self._update_var_row(entry)
        self._refit_vars()

    def _update_var_row(self, entry):
        state = "disabled" if entry["type_var"].get() == tr("文字列") else "normal"
        entry["min_entry"].configure(state=state)
        entry["max_entry"].configure(state=state)

    def _delete_var_row(self, entry):
        self.var_rows.remove(entry)
        entry["frame"].destroy()
        self._refit_vars()
        self._refresh_cond_names()

    def _current_names(self) -> list[str]:
        return [e["name_var"].get().strip() for e in self.var_rows
                if e["name_var"].get().strip()]

    def _refresh_cond_names(self):
        names = self._current_names()
        for w in self.watch_rows:
            w["conds"].set_names(names)

    # ---- 監視(watch) ----

    def _add_watch_row(self, raw: dict | None = None):
        raw = raw or {}
        box = ctk.CTkFrame(self.watch_frame, corner_radius=8,
                           fg_color=("gray85", "gray20"))
        box.pack(fill="x", pady=3)
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=6)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=tr("条件(AND):"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", anchor="n")
        conds = CondListEditor(top)
        conds.set_names(self._current_names())
        conds.load(raw.get("when") or [])
        conds.pack(side="left", fill="x", expand=True, padx=(6, 0))
        entry = {"frame": box, "conds": conds,
                 "extra": {k: v for k, v in raw.items()
                           if k not in ("when", "to", "mode", "once")}}
        ctk.CTkButton(top, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_watch_row(e)
                      ).pack(side="right", anchor="n")
        bottom = ctk.CTkFrame(inner, fg_color="transparent")
        bottom.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(bottom, text=tr("成立で→"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        to_var = tk.StringVar(
            value=raw.get("to") if raw.get("to") in self.event_ids
            else (self.event_ids[0] if self.event_ids else ""))
        CTkOptionMenu(
            bottom, variable=to_var, width=140, height=26,
            values=self.event_ids or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
        ).pack(side="left", padx=6)
        mode_var = tk.StringVar(
            value=tr("即時打ち切りで遷移") if raw.get("mode") == "interrupt"
            else tr("再生中の音声を待って遷移"))
        CTkOptionMenu(
            bottom, variable=mode_var, width=190, height=26,
            values=[tr("再生中の音声を待って遷移"), tr("即時打ち切りで遷移")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
        ).pack(side="left", padx=6)
        once_var = tk.BooleanVar(value=raw.get("once", True) is not False)
        ctk.CTkCheckBox(
            bottom, text=tr("1回の再生につき1度だけ"), variable=once_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER).pack(side="left", padx=8)
        entry.update({"to_var": to_var, "mode_var": mode_var,
                      "once_var": once_var})
        self.watch_rows.append(entry)
        self._refit_watch()

    def _delete_watch_row(self, entry):
        self.watch_rows.remove(entry)
        entry["frame"].destroy()
        self._refit_watch()

    # ---- 収集 ----

    def collect(self):
        """(エラーメッセージ, {"vars": .., "watch": ..}) を返す。"""
        vars_out = {}
        for i, e in enumerate(self.var_rows):
            name = e["name_var"].get().strip()
            if not name:
                return tr("変数{0}: 名前が空です").format(i + 1), None
            if name in vars_out:
                return tr("変数名 '{0}' が重複しています").format(name), None
            is_str = e["type_var"].get() == tr("文字列")
            init_text = e["init_var"].get()
            if is_str:
                init = init_text
            else:
                try:
                    init = _parse_num_text(init_text or "0")
                except ValueError:
                    return tr("変数 '{0}': 初期値が数値ではありません").format(name), None
            vmin = vmax = None
            if not is_str:
                try:
                    if e["min_var"].get().strip():
                        vmin = _parse_num_text(e["min_var"].get())
                    if e["max_var"].get().strip():
                        vmax = _parse_num_text(e["max_var"].get())
                except ValueError:
                    return tr("変数 '{0}': 最小/最大が数値ではありません").format(name), None
                if vmin is not None and vmax is not None and vmin > vmax:
                    return tr("変数 '{0}': 最小は最大以下にしてください").format(name), None
                if ((vmin is not None and init < vmin)
                        or (vmax is not None and init > vmax)):
                    return tr("変数 '{0}': 初期値が最小/最大の範囲外です").format(name), None
            if vmin is None and vmax is None and not e["extra"]:
                vars_out[name] = init
            else:
                d = {"init": init}
                if vmin is not None:
                    d["min"] = vmin
                if vmax is not None:
                    d["max"] = vmax
                d.update(e["extra"])
                vars_out[name] = d

        string_vars = {n for n, d in vars_out.items()
                       if isinstance(d.get("init") if isinstance(d, dict) else d,
                                     str)}
        watch_out = []
        for i, e in enumerate(self.watch_rows):
            where = tr("監視{0}").format(i + 1)
            err, conds = e["conds"].collect(where, string_vars)
            if err:
                return err, None
            to = e["to_var"].get()
            if to not in self.event_ids:
                return tr("{0}: 遷移先のイベントを選択してください").format(where), None
            w = {"when": conds, "to": to}
            if e["mode_var"].get() == tr("即時打ち切りで遷移"):
                w["mode"] = "interrupt"
            if not e["once_var"].get():
                w["once"] = False
            w.update(e["extra"])
            watch_out.append(w)
        if watch_out and not vars_out:
            return tr("監視(watch)を使うには変数を1つ以上宣言してください"), None
        return None, {"vars": vars_out or None, "watch": watch_out or None}

    def _ok(self):
        err, result = self.collect()
        if err:
            self.err_label.configure(text=err)
            _pkg().MESSAGE_LOG.append(("error", (tr("編集エラー"), err)))
            return
        self.result = result
        self.destroy()


class ChannelCopyDialog(ctk.CTkToplevel):
    """チャンネル内容のコピー元を選ぶモーダルダイアログ。

    イベント/ステート/チャンネルの3コンボを横並びで連動表示する。音声ch・
    スクリプト専用chのみ選べる(=65。動画chは除外)。OKで
    on_ok(ev_id, st_key, cid) を呼ぶ。
    st_key はステートID、通常イベントは None。キャンセルは何もしない。

    sources: [(ev_id, st_id|None, cid), ...] の選択可能なコピー元一覧。
    """

    NO_STATE_LABEL = None   # __init__ で tr() 済みを入れる

    def __init__(self, master, sources, on_ok):
        super().__init__(master)
        self.title(tr("他からコピー"))
        self.geometry("560x180")
        _place_popup(self, master, 560, 180)
        self._on_ok = on_ok
        self.NO_STATE_LABEL = tr("(ステートなし)")
        self.sources = list(sources)

        # イベント順を保ちつつ重複排除
        self.event_ids = []
        for ev_id, _st, _cid in self.sources:
            if ev_id not in self.event_ids:
                self.event_ids.append(ev_id)

        wrap = ctk.CTkFrame(self, corner_radius=10)
        wrap.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        ctk.CTkLabel(
            wrap, text=tr("コピー元のチャンネルを選んでください(音声ch・スクリプトchのみ)。"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED, anchor="w",
            justify="left", wraplength=520).pack(fill="x", padx=10, pady=(10, 6))

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 8))

        def col(title):
            c = ctk.CTkFrame(row, fg_color="transparent")
            c.pack(side="left", padx=(0, 10))
            ctk.CTkLabel(c, text=title, font=ctk.CTkFont(size=11),
                         text_color=TEXT_MUTED, anchor="w").pack(fill="x")
            return c

        ce = col(tr("イベント"))
        self.ev_var = tk.StringVar()
        self.ev_menu = CTkOptionMenu(
            ce, variable=self.ev_var, width=150, height=28,
            values=self.event_ids or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_event_change())
        self.ev_menu.pack()

        cs = col(tr("ステート"))
        self.st_var = tk.StringVar()
        self.st_menu = CTkOptionMenu(
            cs, variable=self.st_var, width=140, height=28, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_state_change())
        self.st_menu.pack()

        cc = col(tr("チャンネル"))
        self.cid_var = tk.StringVar()
        self.cid_menu = CTkOptionMenu(
            cc, variable=self.cid_var, width=90, height=28, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.cid_menu.pack()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("OK"), width=90, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._ok).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=4)

        if self.event_ids:
            self.ev_var.set(self.event_ids[0])
            self._on_event_change()

        _front_window(self)
        self.grab_set()

    # ---- コンボの連動 ----
    def _state_label(self, st_id):
        return self.NO_STATE_LABEL if st_id is None else st_id

    def _states_for(self, ev_id):
        """(label, st_id) を順序保持で返す。"""
        out = []
        for e, st, _cid in self.sources:
            if e != ev_id:
                continue
            lbl = self._state_label(st)
            if lbl not in [x[0] for x in out]:
                out.append((lbl, st))
        return out

    def _chans_for(self, ev_id, st_id):
        return [cid for e, st, cid in self.sources
                if e == ev_id and st == st_id]

    def _on_event_change(self):
        ev_id = self.ev_var.get()
        states = self._states_for(ev_id)
        labels = [lbl for lbl, _st in states] or [""]
        self.st_menu.configure(values=labels)
        self.st_var.set(labels[0])
        self._on_state_change()

    def _on_state_change(self):
        ev_id = self.ev_var.get()
        st_id = self._selected_state_id()
        cids = self._chans_for(ev_id, st_id) or [""]
        self.cid_menu.configure(values=cids)
        self.cid_var.set(cids[0])

    def _selected_state_id(self):
        ev_id = self.ev_var.get()
        lbl = self.st_var.get()
        for l, st in self._states_for(ev_id):
            if l == lbl:
                return st
        return None

    def _ok(self):
        ev_id = self.ev_var.get()
        st_id = self._selected_state_id()
        cid = self.cid_var.get()
        if not ev_id or not cid:
            self.destroy()
            return
        cb = self._on_ok
        self.destroy()
        cb(ev_id, st_id, cid)


class ImportDialog(ctk.CTkToplevel):
    """他のシナリオ(.json)からイベントを取り込むモーダルダイアログ。

    取り込み元のイベント一覧をチェックボックスで複数選択し、「取り込み」で
    owner._perform_import(src_data, src_dir, ids) を呼ぶ(ids は元ファイルの
    定義順)。「参照先も選択」はチェック済みイベントから next 遷移先
    (random/choice/cond/input/timeout/default/else/when_exhausted 含む)を
    再帰的に辿り、取り込み元に存在するものを追加選択する閉包補助。
    """

    def __init__(self, master, src_data, src_dir, src_name):
        super().__init__(master)
        self.owner = master
        self.src = src_data
        self.src_dir = src_dir
        self.title(tr("イベントの取り込み"))
        self.geometry("560x560")
        _place_popup(self, master, 560, 560)
        self.transient(master)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(head, text=tr("取り込み元: {0}").format(src_name),
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(side="left")

        sel_row = ctk.CTkFrame(self, fg_color="transparent")
        sel_row.pack(fill="x", padx=14, pady=(0, 4))
        for text, cmd in ((tr("全選択"), self._select_all),
                          (tr("全解除"), self._clear_all),
                          (tr("参照先も選択"), self._select_closure)):
            ctk.CTkButton(sel_row, text=text, width=92, height=26,
                          fg_color="transparent", border_width=1,
                          border_color=MUTED,
                          text_color=("gray20", "gray85"),
                          hover_color=("gray85", "gray25"),
                          command=cmd).pack(side="left", padx=(0, 6))

        self.list_frame = ctk.CTkScrollableFrame(self, corner_radius=8)
        self.list_frame.pack(fill="both", expand=True, padx=14, pady=4)

        # (ev_id, BooleanVar) を元ファイルの定義順で保持
        self.rows: list = []
        for ev_id, ev in (src_data.get("events") or {}).items():
            row = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=False)
            ctk.CTkCheckBox(row, text=ev_id, variable=var,
                            font=ctk.CTkFont(size=12), width=180,
                            checkbox_width=18, checkbox_height=18
                            ).pack(side="left")
            detail = ""
            if isinstance(ev, dict) and isinstance(ev.get("detail"), str):
                detail = ev["detail"].splitlines()[0][:40]
            if detail:
                ctk.CTkLabel(row, text=detail,
                             font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                             ).pack(side="left", padx=(8, 0))
            self.rows.append((ev_id, var))

        self.warn_label = ctk.CTkLabel(
            self, text=tr("取り込むイベントを選択してください"),
            font=ctk.CTkFont(size=11), text_color="#e05a5a")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(btn_row, text=tr("キャンセル"), width=100, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_row, text=tr("取り込み"), width=110, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._do_import).pack(side="right")

        self.after(100, self._try_grab)

    def _try_grab(self):
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _select_all(self):
        for _ev_id, var in self.rows:
            var.set(True)

    def _clear_all(self):
        for _ev_id, var in self.rows:
            var.set(False)

    def _select_closure(self):
        """チェック済みイベントの遷移先を再帰的に辿って追加選択する。"""
        from ..scenario_map import next_targets
        events = self.src.get("events") or {}
        sel = {ev_id for ev_id, var in self.rows if var.get()}
        changed = True
        while changed:
            changed = False
            for ev_id in list(sel):
                ev = events.get(ev_id)
                if not isinstance(ev, dict):
                    continue
                for t in next_targets(ev):
                    if t in events and t not in sel:
                        sel.add(t)
                        changed = True
        for ev_id, var in self.rows:
            if ev_id in sel:
                var.set(True)

    def _do_import(self):
        ids = [ev_id for ev_id, var in self.rows if var.get()]
        if not ids:
            self.warn_label.pack(pady=(0, 2))
            return
        owner = self.owner
        src, src_dir = self.src, self.src_dir
        self.destroy()
        owner._perform_import(src, src_dir, ids)
