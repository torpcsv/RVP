"""シナリオ編集: アイテム行(TrackRowsMixin / ItemRow / BgmItemRow)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import tkinter as tk
import tkinter.font as tkfont
from ..scenario import CSV_TRACK_TYPES, auto_bind_tracks, normalize_track_type
from .. import appfont
from ..i18n import tr

from .common import (CTkOptionMenu, DEVICE_TYPES, MUTED, TEXT_MUTED,
    _dialog_initialdir, _ellipsize_middle, _remember_dialog_dir,
    _script_track_type, menu_device_types)
from .fields import (Tooltip, VarRefField, _range_dict, _raw_range,
    _validate_range_fields)
from .paths import _safe_relpath
from .review_support import _review_range_ms
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg


class TrackRowsMixin:
    """トラック行(種別+funscript/CSV+✕)のリスト編集を提供する共有部品(=50)。

    =50で ItemRow と VideoRow の編集体験を揃えるために切り出した共有部品。
    =52 で動画がチャンネルのアイテムになり VideoRow は廃止されたが、
    トラック行の生成・選択・D&D紐づけ・検証・書き出しはこのまま使う。
    利用側は以下を用意する:

      self.base_dir / self.owner / self.compact / self.mode / self.mode_var
      self.is_script(省略可=False) / self._update_mode_ui()

    `_build_tracks_area(parent)` でウィジェット一式を作り、`_update_mode_ui`
    から `self.tracks_area` の pack/pack_forget を制御する。
    """

    def _build_tracks_area(self, parent):
        """手動モードのトラック行リスト(枠+＋追加ボタン+注記)を生成する。

        height=0: トラック0本(funscript:null等)の空フレームが CTkFrame 既定の
        200pxを確保して、行が縦に間延びするのを防ぐ(中身が入れば追従)。
        """
        if not hasattr(self, "track_rows"):
            self.track_rows = []
        self.tracks_area = ctk.CTkFrame(parent, fg_color="transparent")
        self.tracks_frame = ctk.CTkFrame(self.tracks_area,
                                         fg_color="transparent", height=0)
        self.tracks_frame.pack(fill="x")
        foot = ctk.CTkFrame(self.tracks_area, fg_color="transparent")
        foot.pack(fill="x")
        self.add_track_btn = ctk.CTkButton(
            foot, text=tr("＋トラック追加"), width=110, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_track_row())
        self.add_track_btn.pack(side="left", pady=(2, 0))
        self.no_track_label = ctk.CTkLabel(
            foot, text=tr("(トラックなし=デバイス動作なし)"),
            font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self.no_track_label.pack(side="left", padx=8)

    def _on_tracks_changed(self):
        """トラック構成(本数/種別)が変わったときの通知フック(既定=何もしない)。

        (=50では VideoRow がオーバーライドしていた。=52で廃止。)
        """

    def _track_type_capacity(self) -> int:
        """追加できるトラック行の上限(=79: twist非表示時は4)。"""
        used = {e["type_var"].get() for e in self.track_rows}
        return len(set(menu_device_types()) | used)

    def _update_tracks_ui(self):
        """手動モードのトラック領域(追加ボタンの可否・注記・高さ)を更新する。"""
        self.add_track_btn.configure(
            state="normal" if len(self.track_rows) < self._track_type_capacity()
            else "disabled")
        if self.track_rows:
            self.no_track_label.pack_forget()
        else:
            self.no_track_label.pack(side="left", padx=8)
        self._refit_tracks()

    def _refit_tracks(self):
        """トラック0本のとき tracks_frame を潰す(空フレームの間延び防止)。"""
        if self.track_rows:
            self.tracks_frame.pack_propagate(True)
        else:
            self.tracks_frame.pack_propagate(False)
            self.tracks_frame.configure(height=1)

    # ---- トラック行 ----

    def _add_track_row(self, ttype: str = "", fs: str = "",
                       start: str = "", end: str = ""):
        if len(self.track_rows) >= self._track_type_capacity():
            return
        if not ttype:
            used = {e["type_var"].get() for e in self.track_rows}
            free = [t for t in menu_device_types() if t not in used]
            ttype = free[0] if free else DEVICE_TYPES[0]
        # 追加時は中身に追従して伸びるよう propagate を戻す
        self.tracks_frame.pack_propagate(True)
        row = ctk.CTkFrame(self.tracks_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        # compact時は「種別+✕」を上段、funscript選択を下段(全幅)に分ける
        r_top = ctk.CTkFrame(row, fg_color="transparent")
        r_top.pack(fill="x")
        r_bot = ctk.CTkFrame(row, fg_color="transparent") if self.compact else r_top
        if self.compact:
            r_bot.pack(fill="x", pady=(2, 0))
        type_var = tk.StringVar(
            value=ttype if ttype in DEVICE_TYPES else "linear")
        type_menu = CTkOptionMenu(
            r_top, variable=type_var, width=150 if self.compact else 170, height=24,
            font=ctk.CTkFont(size=11),
            values=menu_device_types(ttype),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        type_menu.pack(side="left")
        entry = {"frame": row, "type_var": type_var, "type_menu": type_menu,
                 "fs_path": fs or ""}
        # 種別を変えたら未選択時のプレースホルダ(funscript / CSV)を更新する
        type_menu.configure(
            command=lambda _v, e=entry: (self._update_track_row(e),
                                         self._on_tracks_changed()))
        ctk.CTkButton(r_top, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_track_row(e)
                      ).pack(side="right", padx=(2, 0))
        fs_btn = ctk.CTkButton(
            r_bot, text="", width=180, height=24, anchor="w",
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
            command=lambda e=entry: self._pick_track_fs(e))
        fs_btn.pack(side="left", padx=(0, 4) if self.compact else 4,
                    fill="x", expand=True)
        entry["fs_btn"] = fs_btn
        # ---- トラック個別の区間(=59)。「詳細設定」ON のときだけ表示する ----
        # 空欄=アイテムの区間に連動。ここに入れるとそちらが優先される。
        r_rng = ctk.CTkFrame(row, fg_color="transparent")
        entry["range_row"] = r_rng
        ctk.CTkLabel(r_rng, text=tr("区間:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        entry["start_var"] = tk.StringVar(value=start or "")
        entry["start_entry"] = ctk.CTkEntry(
            r_rng, width=52, height=22, textvariable=entry["start_var"],
            font=ctk.CTkFont(size=11), placeholder_text=tr("連動"))
        entry["start_entry"].pack(side="left", padx=(4, 2))
        # =67: 区切りは「[値]秒 〜 [値]秒」で他の min〜max 行と揃える
        ctk.CTkLabel(r_rng, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(r_rng, text="〜", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(4, 2))
        entry["end_var"] = tk.StringVar(value=end or "")
        entry["end_entry"] = ctk.CTkEntry(
            r_rng, width=52, height=22, textvariable=entry["end_var"],
            font=ctk.CTkFont(size=11), placeholder_text=tr("連動"))
        entry["end_entry"].pack(side="left", padx=(4, 2))
        ctk.CTkLabel(r_rng, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")   # =67 単位を両方に
        self.track_rows.append(entry)
        self._update_track_row(entry)
        self._refresh_track_ranges()
        self._update_mode_ui()

    def _refresh_track_ranges(self):
        """トラックの区間欄を「詳細設定」の状態に合わせて出し入れする(=59)。"""
        show = bool(getattr(self, "_detail", False))
        for e in getattr(self, "track_rows", []):
            row = e.get("range_row")
            if row is None:
                continue
            if show:
                row.pack(fill="x", pady=(2, 0))
            else:
                row.pack_forget()

    def _update_track_row(self, entry):
        # ROTATE系(ufo / a10)は funscript の他に CSV も選べるため、
        # 未選択時のプレースホルダも「funscript または CSV」を示す。
        ttype = entry["type_var"].get()
        # =79: 行の種別に応じて候補を更新(twist行はOFFでも自分の種別を保持)
        entry["type_menu"].configure(values=menu_device_types(ttype))
        if ttype in ("rotate_ufo", "rotate_a10cyclonesa"):
            placeholder = tr("(funscriptまたはCSVを選択...)")
        else:
            placeholder = tr("(funscriptを選択...)")
        entry["fs_btn"].configure(
            text=os.path.basename(entry["fs_path"]) or placeholder)

    def _pick_track_fs(self, entry):
        # ROTATE系トラックは funscript の他に CSV も選べる。
        ttype = entry["type_var"].get()
        if ttype in ("rotate_ufo", "rotate_a10cyclonesa"):
            filetypes = [(tr("funscript / CSV"), "*.funscript *.csv"),
                         ("funscript", "*.funscript"), ("CSV", "*.csv"),
                         (tr("すべて"), "*.*")]
            title = tr("funscript または CSV を選択")
        else:
            filetypes = [("funscript", "*.funscript"), (tr("すべて"), "*.*")]
            title = tr("funscriptを選択")
        path = _pkg().filedialog.askopenfilename(
            title=title,
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=filetypes, parent=self,
        )
        if not path:
            return
        _remember_dialog_dir(path)
        entry["fs_path"] = _safe_relpath(path, self.base_dir)
        self._update_track_row(entry)

    def dropped_track_hit(self, x: int, y: int):
        """=278: 落下点(スクリーン座標)が手動トラック行の上ならその種別を返す。

        行の矩形(トラック行フレーム)で判定する。表示中のもののみ。
        """
        for e in self.track_rows:
            w = e.get("frame")
            try:
                if w is None or not w.winfo_ismapped():
                    continue
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                if (wx <= x < wx + w.winfo_width()
                        and wy <= y < wy + w.winfo_height()):
                    return e["type_var"].get()
            except Exception:
                continue
        return None

    def bind_dropped_fs(self, path, track_type: str | None = None):
        """D&Dで落とされた funscript/CSV をこのアイテム/動画へ手動紐づけする(=44)。

        種別はファイル名タグの既存命名ルール(ufo→rotate_ufo /
        a10→rotate_a10cyclonesa / vib→vibration)で決める。
        .funscript のタグ無しは linear、.csv はROTATE系のみで
        タグ無しは rotate_ufo(ユーザー確認済みの既定。a10はタグで指定)。
        自動モードなら手動モードへ切り替え(自動解決結果はトラック行として
        引き継がれる)、同種別のトラックがあれば差し替え、無ければ追加する。
        対象外拡張子・空きトラックなし・失敗は黙って無視(仕様)。

        =278: track_type を渡すと(=特定のトラック行の上へ落とした)、
        ファイル名タグによらず**その種別のトラック**へ紐づける
        (twist 行へ .funscript を落とせば twist に、linear 行へ
        .twist.funscript を落とせば linear になる。クリック→ダイアログ
        選択と同じ結果)。.csv はROTATE系の行以外へは落とせない
        (その場合は従来のタグ規則へ戻す)。
        """
        if not self._device_on():
            return          # =252: デバイス連動OFFでは紐づけを受け付けない
        if not os.path.isfile(path):
            return
        ttype = _script_track_type(path)
        if ttype is None:
            return
        if track_type is not None and track_type in DEVICE_TYPES:
            ext = os.path.splitext(path)[1].lower()
            if ext != ".csv" or track_type in CSV_TRACK_TYPES:
                ttype = track_type
        if self.mode != "manual":
            self.mode_var.set(tr("手動"))
            self._on_mode_change()
        rel = _safe_relpath(path, self.base_dir)
        for e in self.track_rows:
            if e["type_var"].get() == ttype:
                e["fs_path"] = rel
                self._update_track_row(e)
                self._on_tracks_changed()
                return
        if len(self.track_rows) >= self._track_type_capacity():
            return
        self._add_track_row(ttype, rel)
        self._update_mode_ui()
        self._on_tracks_changed()

    def _delete_track_row(self, entry):
        if getattr(self, "is_script", False) and len(self.track_rows) <= 1:
            return   # スクリプトのみアイテムはトラック0本にできない
        self.track_rows.remove(entry)
        entry["frame"].destroy()
        self._update_mode_ui()
        self._on_tracks_changed()

    # ---- 読み書き ----

    def _sync_track_rows(self, init_tracks):
        """トラック行を init_tracks [(種別, パス, 開始, 終了)] に合わせる。

        行は使い回す。区間(=59)は文字列("" = アイテムの区間に連動)。
        """
        for i, tr_def in enumerate(init_tracks):
            ttype, fs = tr_def[0], tr_def[1]
            start = tr_def[2] if len(tr_def) > 2 else ""
            end = tr_def[3] if len(tr_def) > 3 else ""
            if i < len(self.track_rows):
                e = self.track_rows[i]
                e["type_var"].set(ttype if ttype in DEVICE_TYPES else "linear")
                e["fs_path"] = fs or ""
                e["start_var"].set(start)
                e["end_var"].set(end)
                self._update_track_row(e)
            else:
                self._add_track_row(ttype, fs, start, end)
        while len(self.track_rows) > len(init_tracks):
            self.track_rows.pop()["frame"].destroy()
        self._refresh_track_ranges()

    @staticmethod
    def _tracks_from_raw(raw: dict):
        """dict の tracks/funscript を (モード, [(種別, パス)]) へ解釈する。

        戻り値のモードは "manual"(明示指定あり)/"auto"(未指定)。
        scenario.parse_tracks と同じ優先順(tracks > funscript > 自動)。
        """
        tracks_raw = raw.get("tracks")
        if isinstance(tracks_raw, list):
            return "manual", [
                (normalize_track_type(t.get("type", "linear")),
                 t.get("funscript", "")) + _raw_range(t)
                for t in tracks_raw if isinstance(t, dict)]
        if "funscript" in raw:
            fs = raw["funscript"]
            return "manual", ([("linear", fs, "", "")] if fs else [])
        return "auto", []

    def _validate_track_rows(self, disp_name: str):
        """手動モードのトラック行を検証する(エラー文言 or None)。"""
        used = set()
        for i, e in enumerate(self.track_rows):
            ttype = e["type_var"].get()
            if ttype in used:
                if self.owner is not None:
                    self.owner._want_mark(e["type_menu"], "error")
                return tr("{0}: デバイス種別 '{1}' が重複しています").format(
                    disp_name, ttype)
            used.add(ttype)
            if not e["fs_path"]:
                if self.owner is not None:
                    self.owner._want_mark(e["fs_btn"], "error")
                return tr("{0}: トラック{1}のfunscriptを選択してください").format(
                    disp_name, i + 1)
            err = _validate_range_fields(
                e["start_var"], e["end_var"], e["start_entry"], e["end_entry"],
                tr("{0} トラック{1}").format(disp_name, i + 1), self.owner)
            if err:
                return err
        return None

    def _write_tracks(self, out: dict):
        """手動モードのトラックを out へ書き出す(0本は "funscript": null)。"""
        if not self.track_rows:
            out["funscript"] = None
            return out
        rows = []
        for e in self.track_rows:
            t = {"type": e["type_var"].get(), "funscript": e["fs_path"]}
            rng = _range_dict(e["start_var"], e["end_var"])
            if rng:
                t["range"] = rng      # =59: 空欄ならキーを書かない(=連動)
            rows.append(t)
        out["tracks"] = rows
        return out

    def track_types(self) -> set:
        """現在のトラック行が担当するデバイス種別の集合(手動モード時)。"""
        return {e["type_var"].get() for e in self.track_rows}


class ItemRow(TrackRowsMixin, ctk.CTkFrame):
    """チャンネル内の音声1件の編集行(複数トラック対応)。

    モード:
      自動 = JSONにキーを書かず、命名ルール(auto_bind_tracks)で紐づける。
             解決結果をその場に表示(linear=wav名を含む同名系 / rotate=+"ufo" /
             rotate_a10cyclonesa=+"a10" / vibration=+"vib")
      手動 = tracksを明示指定。トラック行(種別+funscript+✕)のリストで編集し、
             最大4本・種別重複不可。0本=デバイスなし("funscript": null)
    """

    def __init__(self, master, raw_item, base_dir, on_delete, owner=None,
                 compact: bool = False):
        super().__init__(master, fg_color=("gray82", "gray24"), corner_radius=8)
        self.base_dir = base_dir
        self.on_delete = on_delete
        self.owner = owner   # ScenarioEditor(変数操作の編集用)。Noneも可
        self.compact = compact   # 幅の狭いチャンネル枠向けに要素を縮める
        self.orig = {}
        self.audio_rel = ""
        self.mode = "auto"
        self.track_rows: list[dict] = []

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(6, 2))
        self.head = head

        self.audio_label = ctk.CTkLabel(
            head, text="", font=ctk.CTkFont(size=12), anchor="w",
            width=110 if compact else 200,
        )

        # 削除✕(compactでも常に右端に置く)。
        # =101: ✕/メニューを**ラベルより先にpack**する(packは後のスレーブから
        # 場所を失う=長いファイル名でラベルが幅を食い尽くすと✕やメニューが
        # 消えていた。=66の「履歴✕は先にpack」と同じ落とし穴)。
        del_btn = ctk.CTkButton(
            head, text="✕", width=26, height=26,
            fg_color="transparent", text_color="#e05a5a",
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_delete(self))
        del_btn.pack(side="right", padx=(2, 0))

        # 自動/手動 メニュー。compactは幅を詰めて、選択肢ラベルも短縮表示。
        auto_txt = tr("自動") if compact else tr("自動(ルールで紐づけ)")
        self.mode_var = tk.StringVar(value=auto_txt)
        self.mode_menu = CTkOptionMenu(
            head, variable=self.mode_var, width=96 if compact else 150, height=26,
            values=[auto_txt, tr("手動")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_mode_change(),
        )
        self.mode_menu.pack(side="right", padx=4)

        # =282: 並び替えの取っ手(≡)。「順番に再生」のチャンネルでだけ出す
        # (ランダム系では順序に意味が無い)。取っ手かファイル名を縦に
        # ドラッグすると行が入れ替わる(ボタンでなくD&D=ユーザー決定)。
        # 実体の入れ替えは ChannelSection(reorder_host)が行う。
        self.reorder_host = None
        self.grip = ctk.CTkLabel(
            head, text="≡", width=14, font=ctk.CTkFont(size=13),
            text_color=TEXT_MUTED, cursor="fleur")
        self._grip_shown = False
        for w in (self.grip, self.audio_label):
            w.bind("<ButtonPress-1>", self._on_drag_press)
            w.bind("<B1-Motion>", self._on_drag_motion)
            w.bind("<ButtonRelease-1>", self._on_drag_release)
        self._drag_armed = False
        self._drag_active = False
        self._drag_y0 = 0

        # ラベルは最後にpack=右側の操作類を必ず残し、余り幅だけを使う
        self.audio_label.pack(side="left", fill="x", expand=True)

        # =101: 長いファイル名は「先頭…末尾」に省略し、ホバーで全文を
        # ツールチップ表示する。fit計算は<Configure>からafter(60ms)へ集約
        # (=96と同じ作法。同期でやると発振・起動ブロックの温床になる)。
        self._full_name = ""
        self._name_fit_after = None
        self._name_font = tkfont.Font(family=appfont.FAMILY, size=-12)
        self._name_truncated = False
        head.bind("<Configure>", lambda _e: self._schedule_name_fit())
        self._name_tooltip = Tooltip(
            self.audio_label,
            lambda: self._full_name if self._name_truncated else "")

        # 変数操作(on_play/on_complete)。変数宣言があるときだけ表示。
        # 行の使い回し時に変数の有無が変わりうるため、生成/表示は
        # _ensure_ops_btn() へ集約(_apply_item から毎回呼ぶ)。
        self.ops_btn = None
        self._ensure_ops_btn()

        # =164: アイテムレビュー(音声の試聴+スクリプトのグラフ)。
        # 動画アイテムでは出さない(動画は対象外=ユーザー決定)。表示の
        # 出し入れは _refresh_review_btn()(_apply_item から毎回呼ぶ)。
        self.review_btn = ctk.CTkButton(
            head, text=tr("レビュー"), width=58 if compact else 68, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
            command=self._open_review)

        # ---- weight / pan(アイテム個別) ----
        # weight: ランダム再生chのみ有効な抽選の重み(既定1.0)。
        #         ChannelSection.set_random で表示/非表示を切り替える。
        # pan   : チャンネル既定パンの上書き(左右各0〜100%)。上書きON時のみ書き出す。
        #         表示はチャンネルの「パン詳細」トグルに従う(既定OFF=非表示)。
        # 並びは _refresh_opts_ui() で [重み][パン上書き][L/R] の順に再構築する。
        self._random = False
        self._detail = False               # チャンネルの「詳細設定」ON/OFF
        self.ch_default_pan = (100, 100)   # 上書きON時の初期値(chの実効パン)
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=16, pady=(0, 0))
        self.opts_row = opts
        self.weight_box = ctk.CTkFrame(opts, fg_color="transparent")
        ctk.CTkLabel(self.weight_box, text=tr("重み"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        # =74: 重みは定数 or 変数参照(VarRefField)。数値変数が無ければ
        # 素の数値欄(従来同等)。weight_var は互換エイリアス(テストが
        # .get()/.set() で定数値を読み書きする)。
        self.weight_field = VarRefField(self.weight_box, width=46,
                                        placeholder="1")
        self.weight_field.set("1")
        self.weight_field.pack(side="left", padx=(3, 0))
        self.weight_var = self.weight_field.text_var
        self.pan_var = tk.BooleanVar(value=False)
        self.pan_check = ctk.CTkCheckBox(
            opts, text=tr("パン上書き"), variable=self.pan_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_pan_toggle)
        self.pan_fields = ctk.CTkFrame(opts, fg_color="transparent")
        self.pan_l_var = tk.StringVar(value="")
        self.pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.pan_fields, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_l_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_l_var,
            font=ctk.CTkFont(size=11))
        self.pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.pan_fields, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_r_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_r_var,
            font=ctk.CTkFont(size=11))
        self.pan_r_entry.pack(side="left", padx=(2, 0))

        # ---- アイテムの区間指定 ----
        # =51で動画に導入 → =52でアイテムへ移設 → **=59で音声・スクリプトへ
        # 拡張し、「詳細設定」ON のときだけ表示する**(ユーザー要望=既定は
        # 非表示にしてUIを簡素化)。1本の長尺素材を「開始秒〜終了秒」で
        # 切り分ける。空欄=先頭/末尾。トラックは既定でこの区間に連動する。
        self.range_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.range_row, text=tr("区間:"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.vstart_var = tk.StringVar(value="")
        self.vstart_entry = ctk.CTkEntry(
            self.range_row, width=56, height=24, textvariable=self.vstart_var,
            font=ctk.CTkFont(size=11), placeholder_text=tr("先頭"))
        self.vstart_entry.pack(side="left", padx=(4, 2))
        ctk.CTkLabel(self.range_row, text=tr("秒"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")
        ctk.CTkLabel(self.range_row, text="〜",
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=(4, 2))
        self.vend_var = tk.StringVar(value="")
        self.vend_entry = ctk.CTkEntry(
            self.range_row, width=56, height=24, textvariable=self.vend_var,
            font=ctk.CTkFont(size=11), placeholder_text=tr("末尾"))
        self.vend_entry.pack(side="left", padx=(4, 2))
        # =67: 単位「秒」は**常に**出す(従来は説明文の頭に付いていたため、
        # compact な編集画面では終了欄に単位が無いように見えていた)
        ctk.CTkLabel(self.range_row, text=tr("秒"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.range_hint = ctk.CTkLabel(
            self.range_row, text=tr("(空欄=素材全体・区間の先頭が0秒)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        if not compact:
            self.range_hint.pack(side="left", padx=(2, 0))

        # ---- 自動モード: 解決結果の表示 ----
        self.auto_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            anchor="w", justify="left", wraplength=280 if compact else 520)

        # ---- 手動モード: トラック行リスト(=50で TrackRowsMixin へ共通化) ----
        self._build_tracks_area(self)

        # アイテムのデータ(音声/動画/モード/トラック)を反映(reloadと共通処理)
        self._apply_item(raw_item)
        self._refresh_grip()   # =282: 既定(順番に再生)では取っ手を出す

    def _apply_item(self, raw_item):
        """アイテムのデータをウィジェットへ反映する。

        __init__ と reload() で共通。ウィジェット自体は再利用し、可変部分
        (トラック行)だけ作り直す。CTkウィジェットの生成コストが高いため、
        イベント切替時は ChannelSection がこの reload で行を使い回す。
        """
        # 元のdict(weight等の未対応フィールド保持用)。文字列itemはdict化。
        self.orig = {"audio": raw_item} if isinstance(raw_item, str) \
            else dict(raw_item)
        audio = self.orig.get("audio", "")
        self.audio_rel = audio
        # 動画アイテム(=52 フェーズ3-B): "video" が文字列 or {file,start,end}。
        # 音声とは同じチャンネルに混在できない(チャンネル単位で1種類)。
        self.video_rel, vstart, vend = self._video_from_raw(self.orig)
        self.is_video = bool(self.video_rel)
        # =59: 区間はアイテム共通の "range" へ。旧形式(video辞書内の
        # start/end)は range が無いときだけ読む(保存し直すと range になる)。
        r_start, r_end = _raw_range(self.orig)
        if r_start or r_end:
            vstart, vend = r_start, r_end
        self.vstart_var.set(vstart)
        self.vend_var.set(vend)
        # スクリプトのみアイテム(audio/videoなし+tracks/funscript明示)か。
        # スクリプト専用チャンネルの行。音声は再生せず、トラックの
        # スクリプト長ぶんデバイスを動かす。
        self.is_script = (not audio and not self.is_video and
                          ("tracks" in self.orig or "funscript" in self.orig))
        # =101: 全文は保持し、表示は幅に合わせて省略(_fit_name)
        self._full_name = self._disp_name()
        self._name_truncated = False
        self.audio_label.configure(text=self._full_name)
        self._schedule_name_fit()
        # モードの解釈
        self.mode, init_tracks = self._tracks_from_raw(self.orig)
        if self.is_script:
            # スクリプトのみアイテムは自動紐づけ(音声名基準)が使えない=手動固定
            self.mode = "manual"
        auto_txt = tr("自動") if self.compact else tr("自動(ルールで紐づけ)")
        self.mode_var.set(tr("手動") if self.mode == "manual" else auto_txt)
        self.mode_menu.configure(
            state="disabled" if self.is_script else "normal")
        # weight / pan を反映(行の使い回し時に前アイテムの値が残らないよう毎回設定)
        self._apply_weight_pan()
        # 変数の有無に応じてボタンを遅延生成/表示切替(行の使い回し対応)
        self._ensure_ops_btn()
        # =252: デバイス連動OFFでは自動/手動メニューを隠す(行の使い回し対応)
        self._refresh_device_ui()
        # =164: レビューボタンの表示可否(動画アイテムでは出さない)
        self._refresh_review_btn()
        # トラック行も使い回す(差分だけ生成/破棄)。生成コストが高いため。
        self._sync_track_rows(init_tracks)
        self._update_mode_ui()

    def reload(self, raw_item):
        """既存の行ウィジェットを使い回して別アイテムを表示する(高速化)。"""
        self._apply_item(raw_item)

    @staticmethod
    def _video_from_raw(raw: dict) -> tuple[str, str, str]:
        """item dict の "video" を (相対パス, 開始秒, 終了秒) の文字列で返す。

        動画アイテムでなければ ("", "", "")。秒は欄に入れる文字列
        (未指定/不正は空欄=先頭/末尾)。
        """
        def _sec(v) -> str:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return ""
            return f"{float(v):g}"

        rv = raw.get("video")
        if isinstance(rv, str) and rv:
            return rv, "", ""
        if isinstance(rv, dict):
            f = rv.get("file")
            if isinstance(f, str) and f:
                return f, _sec(rv.get("start")), _sec(rv.get("end"))
        return "", "", ""

    def _disp_name(self) -> str:
        """エラー文言・ダイアログ見出し用のアイテム表示名。"""
        if self.audio_rel:
            return os.path.basename(self.audio_rel)
        if getattr(self, "is_video", False):
            return os.path.basename(self.video_rel)
        if getattr(self, "is_script", False):
            return tr("(スクリプト)")
        return tr("(音声未指定)")

    # ---- ファイル名の省略表示(=101) ----

    def _schedule_name_fit(self):
        """ラベル幅に合わせた省略計算を after(60ms) へ集約して予約する。"""
        if self._name_fit_after is not None:
            return
        try:
            self._name_fit_after = self.after(60, self._fit_name)
        except Exception:
            self._name_fit_after = None

    def _fit_name(self):
        self._name_fit_after = None
        try:
            avail = self.audio_label.winfo_width() - 6
        except Exception:
            return
        if avail <= 12 or not self._full_name:
            return
        fitted = _ellipsize_middle(self._full_name, self._name_font, avail)
        self._name_truncated = (fitted != self._full_name)
        if self.audio_label.cget("text") != fitted:
            self.audio_label.configure(text=fitted)

    # ---- weight / pan(アイテム個別) ----

    def _apply_weight_pan(self):
        """orig の weight / pan をウィジェットへ反映する(_apply_item から呼ぶ)。"""
        # =74: 変数参照トグルのため、利用できる数値変数名を先に流し込む
        if self.owner is not None and hasattr(self.owner, "_numeric_var_names"):
            self.weight_field.set_names(self.owner._numeric_var_names())
        w = self.orig.get("weight", 1.0)
        if isinstance(w, dict):
            self.weight_field.set(w)          # {"var": 名前}=変数モード
        else:
            try:
                self.weight_field.set(f"{float(w):g}")
            except (TypeError, ValueError):
                self.weight_field.set("1")
        pan = self.orig.get("pan")
        if isinstance(pan, dict) and "left" in pan and "right" in pan:
            self.pan_var.set(True)
            self.pan_l_var.set(str(int(round(float(pan["left"]) * 100))))
            self.pan_r_var.set(str(int(round(float(pan["right"]) * 100))))
        else:
            self.pan_var.set(False)
            self.pan_l_var.set("")
            self.pan_r_var.set("")
        self._refresh_opts_ui()

    def _refresh_opts_ui(self):
        """[重み][パン上書き][L/R] を状態に応じて並べ直す。

        重み = ランダム再生chのみ。パン上書き = チャンネルのパン詳細ON時のみ。
        L/R入力 = パン詳細ON かつ 上書きON時のみ。
        表示要素が無いときは opts 枠を潰す(空CTkFrameが既定200pxを確保して
        アイテム行が縦に間延びするのを防ぐ。items_frame/tracks_frameと同じ対処)。
        """
        # =59: 区間欄は音声/動画/スクリプトのすべてに出すが、
        # 「詳細設定」ON のときだけ表示する(既定は非表示=ユーザー要望)。
        if self._detail:
            self.range_row.pack(fill="x", padx=16, pady=(2, 0),
                                before=self.opts_row)
        else:
            self.range_row.pack_forget()
        self._refresh_track_ranges()
        for w in (self.weight_box, self.pan_check, self.pan_fields):
            w.pack_forget()
        any_shown = False
        if self._random:
            self.weight_box.pack(side="left")
            any_shown = True
        # パン上書きはスクリプトのみ/動画アイテムでは無意味なので出さない
        # (動画の音声は mpv 側の管理=合意事項4)
        if self._detail and not getattr(self, "is_script", False) \
                and not getattr(self, "is_video", False):
            self.pan_check.pack(side="left", padx=(10, 4))
            any_shown = True
            if self.pan_var.get():
                self.pan_fields.pack(side="left")
        if any_shown:
            self.opts_row.pack_propagate(True)
        else:
            self.opts_row.pack_propagate(False)
            self.opts_row.configure(height=1)

    def _on_pan_toggle(self):
        """パン上書きチェックの操作。ONで既定値を埋めて入力欄を表示。"""
        if self.pan_var.get():
            if not self.pan_l_var.get():
                self.pan_l_var.set(str(self.ch_default_pan[0]))
            if not self.pan_r_var.get():
                self.pan_r_var.set(str(self.ch_default_pan[1]))
        self._refresh_opts_ui()

    def set_random(self, flag: bool):
        """チャンネルの再生方式(ランダム=weight有効)を反映する。

        ランダム再生chのみ weight 欄を表示する(相関制御)。sequentialでは非表示。
        """
        self._random = bool(flag)
        self._refresh_opts_ui()
        self._refresh_grip()

    # ---- 並び替え D&D(=282) ----

    def _refresh_grip(self):
        """順番に再生のときだけ取っ手(≡)を出す。"""
        show = not self._random
        if show and not self._grip_shown:
            self.grip.pack(side="left", padx=(0, 4), before=self.audio_label)
            self.audio_label.configure(cursor="fleur")
        elif not show and self._grip_shown:
            self.grip.pack_forget()
            self.audio_label.configure(cursor="")
        self._grip_shown = show

    def _on_drag_press(self, event):
        self._drag_armed = bool(self._grip_shown and
                                self.reorder_host is not None)
        self._drag_active = False
        self._drag_y0 = event.y_root

    def _on_drag_motion(self, event):
        if not self._drag_armed:
            return
        if not self._drag_active:
            # 4px 動くまではクリック扱い(ツールチップ等を邪魔しない)
            if abs(event.y_root - self._drag_y0) < 4:
                return
            self._drag_active = True
            self.reorder_host.begin_row_drag(self)
        self.reorder_host.drag_row_motion(self, event.y_root)

    def _on_drag_release(self, _event):
        if self._drag_active:
            self.reorder_host.end_row_drag(self)
        self._drag_armed = False
        self._drag_active = False

    def set_dragging(self, on: bool):
        """ドラッグ中の見た目(アクセント色の枠)。"""
        if on:
            self.configure(border_width=1, border_color=_clr.ACCENT)
        else:
            self.configure(border_width=0)

    def set_detail(self, flag: bool):
        """チャンネルの「詳細設定」ON/OFFを反映する(=59で改名)。

        ONで「区間」欄(アイテム・トラック)と「パン上書き」が現れる。
        """
        self._detail = bool(flag)
        self._refresh_opts_ui()

    def set_channel_default_pan(self, left_pct: int, right_pct: int):
        """パン上書きON時の初期値(=チャンネルの実効パン)を受け取る。"""
        self.ch_default_pan = (int(left_pct), int(right_pct))

    # ---- 変数操作 ----

    def _ensure_ops_btn(self):
        """変数宣言の有無に応じて、変数操作ボタンを生成/表示切替する。

        行を破棄せず使い回すため、生成後に変数が増減しても正しく追従する。
        変数ありでボタン未生成なら生成し、右端(削除✕/モードの左)へ配置。
        変数なしなら pack_forget で隠す(オブジェクトは保持=再利用)。
        """
        has_vars = self.owner is not None and bool(self.owner._var_names())
        if has_vars and self.ops_btn is None:
            self.ops_btn = ctk.CTkButton(
                self.head, text="", width=72 if self.compact else 86, height=26,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
                command=self._edit_ops)
        if self.ops_btn is None:
            return
        if has_vars:
            if not self.ops_btn.winfo_ismapped():
                # =101: ラベルより先のpack順にする(before=)。ラベルの後だと
                # 幅が足りないときにこのボタンから消える
                self.ops_btn.pack(side="right", padx=2,
                                  before=self.audio_label)
            self._update_ops_btn()
        else:
            self.ops_btn.pack_forget()

    def _update_ops_btn(self):
        if self.ops_btn:
            n = len(self.orig.get("on_play") or []) \
                + len(self.orig.get("on_complete") or [])
            self.ops_btn.configure(text=tr("変数({0})").format(n))

    # ---- アイテムレビュー(=164) ----

    def _refresh_review_btn(self):
        """レビューボタンの表示を更新する(行の使い回しに追従)。

        =203: **動画アイテムにも出す**(=164の「動画は対象外」を撤回。
        mpv 単独再生+再生/一時停止の同期でレビューできる)。
        レビュー画面を開くのは編集画面なので、owner が無いときも出さない。
        """
        show = (self.owner is not None
                and callable(getattr(self.owner, "open_item_review", None)))
        # =252: デバイス連動OFFのスクリプト専用アイテムはレビュー対象が無い
        # (音声なし+スクリプト非表示=AQ7)ためボタン自体を出さない
        if getattr(self, "is_script", False) and not self._device_on():
            show = False
        if show:
            if not self.review_btn.winfo_ismapped():
                # =101と同じ作法: ラベルより先のpack順にして、幅が足りない
                # ときに削られるのがラベル側になるようにする
                self.review_btn.pack(side="right", padx=2,
                                     before=self.audio_label)
        else:
            self.review_btn.pack_forget()

    def _open_review(self):
        if self.owner is not None:
            self.owner.open_item_review(self)

    def review_spec(self) -> dict:
        """レビュー画面へ渡す「いま入力されている内容」を組み立てる(=164)。

        **保存前の値**が対象(ユーザー決定)。トラックは自動モードなら
        命名ルールの解決結果、手動モードならトラック行の内容。区間は
        =59の規則(トラック個別 > アイテム)で解決して ms で渡す。
        """
        warn = []
        lo, hi, _given, ok = _review_range_ms(self.vstart_var, self.vend_var)
        if not ok:
            warn.append(tr("区間の指定が不正なので、素材全体を対象にします"))
        tracks = []
        # =252: デバイス連動OFFではレビューを音声試聴のみにする(AQ7)。
        # スクリプトのグラフを渡さず、レビュー画面側は "device_off" で
        # 「スクリプト編集」ボタンも隠す。
        if not self._device_on():
            audio = os.path.join(self.base_dir, self.audio_rel) \
                if self.audio_rel else ""
            video = os.path.join(self.base_dir, self.video_rel) \
                if getattr(self, "is_video", False) and self.video_rel else ""
            return {"name": self._disp_name(), "audio": audio, "video": video,
                    "audio_lo": lo, "audio_hi": hi,
                    "tracks": [], "warn": warn, "device_off": True}
        if self.mode == "auto":
            for ttype, path in self._resolve_auto():
                tracks.append((ttype, path, lo, hi))
        else:
            bad_track = False
            for e in self.track_rows:
                if not e["fs_path"]:
                    continue
                t_lo, t_hi, given, t_ok = _review_range_ms(e["start_var"],
                                                           e["end_var"])
                bad_track = bad_track or not t_ok
                if not given:
                    t_lo, t_hi = lo, hi     # 既定=アイテムの区間に連動(=59)
                tracks.append((e["type_var"].get(),
                               os.path.join(self.base_dir, e["fs_path"]),
                               t_lo, t_hi))
            if bad_track:
                warn.append(tr("トラックの区間の指定が不正なので、"
                               "アイテムの区間に連動させます"))
        audio = os.path.join(self.base_dir, self.audio_rel) \
            if self.audio_rel else ""
        video = os.path.join(self.base_dir, self.video_rel) \
            if getattr(self, "is_video", False) and self.video_rel else ""
        return {"name": self._disp_name(), "audio": audio, "video": video,
                "audio_lo": lo, "audio_hi": hi,
                "tracks": tracks, "warn": warn}

    def set_ops(self, result: dict):
        """変数操作ダイアログの結果(on_play/on_complete)を反映する。"""
        for key in ("on_play", "on_complete"):
            if result.get(key):
                self.orig[key] = result[key]
            else:
                self.orig.pop(key, None)
        self._update_ops_btn()

    def _edit_ops(self):
        self.owner.open_ops_dialog(
            self._disp_name(),
            [(tr("再生開始時(on_play)"), "on_play",
              self.orig.get("on_play") or []),
             (tr("自然完了時のみ(on_complete)"), "on_complete",
              self.orig.get("on_complete") or [])],
            self.set_ops)

    # ---- 自動/手動モード ----

    def _resolve_auto(self) -> list[tuple[str, str]]:
        """命名ルールでの自動紐づけ結果 [(種別, 絶対パス)] を返す。

        動画アイテム(=52)は動画ファイル名を基準にする(音声と同じ規則)。
        """
        rel = self.video_rel if getattr(self, "is_video", False) \
            else self.audio_rel
        if not rel:
            return []
        return auto_bind_tracks(os.path.join(self.base_dir, rel))

    def _on_mode_change(self):
        new_mode = "manual" if self.mode_var.get() == tr("手動") else "auto"
        if new_mode == self.mode:
            return
        self.mode = new_mode
        if new_mode == "manual" and not self.track_rows:
            # 自動→手動: 現在の自動解決結果を明示トラックとして引き継ぐ
            for ttype, fs_abs in self._resolve_auto():
                self._add_track_row(
                    ttype, _safe_relpath(fs_abs, self.base_dir))
        self._update_mode_ui()

    def _device_on(self) -> bool:
        """デバイス連動(=252)が有効か。owner(編集画面)のフラグを見る。"""
        return bool(getattr(self.owner, "device_enabled", True))

    def _refresh_device_ui(self):
        """自動/手動メニューの表示をデバイス連動フラグへ追従させる(=252)。

        OFFではメニューを隠す(トラック領域は _update_mode_ui が隠す)。
        ONへ戻すときは pack順(✕→メニュー→レビュー→ラベル)を保つため、
        レビューを一旦外してから入れ直す(review は _refresh_review_btn が
        before=audio_label で再配置する)。
        """
        if self._device_on():
            if not self.mode_menu.winfo_manager():
                self.review_btn.pack_forget()
                self.mode_menu.pack(side="right", padx=4,
                                    before=self.audio_label)
        else:
            self.mode_menu.pack_forget()

    def _update_mode_ui(self):
        # =252: デバイス連動OFFではトラック関連の表示を出さない
        # (裏のモード・トラック行は保持=collect/保存は従来どおり通る)
        if not self._device_on():
            self.tracks_area.pack_forget()
            self.auto_label.pack_forget()
            return
        if self.mode == "auto":
            self.tracks_area.pack_forget()
            resolved = self._resolve_auto()
            if resolved:
                text = tr("自動: {0}").format("  ".join(
                    f"{t}={os.path.basename(p)}" for t, p in resolved))
            else:
                text = tr("自動: 紐づくfunscriptなし")
            self.auto_label.configure(text=text)
            self.auto_label.pack(fill="x", padx=16, pady=(0, 6))
        else:
            self.auto_label.pack_forget()
            self.tracks_area.pack(fill="x", padx=16, pady=(0, 6))
            self._update_tracks_ui()

    # ---- 検証・書き出し ----

    def validate(self) -> str | None:
        # weight(ランダム再生chのみ有効): 数値または変数参照であること。
        # =74: 0/負は許容(実行時に「抽選に出さない」候補になる)
        if self._random and not self.weight_field.use_var:
            txt = self.weight_field.get_text().strip()
            if txt:
                try:
                    float(txt)
                except ValueError:
                    if self.owner is not None:
                        self.owner._want_mark(self.weight_field, "error")
                    return tr("{0}: 重みが不正です").format(
                        self._disp_name())
        # pan上書き: 左右とも0〜100
        if self.pan_var.get():
            for var, w in ((self.pan_l_var, self.pan_l_entry),
                           (self.pan_r_var, self.pan_r_entry)):
                try:
                    v = int(float(var.get()))
                    if not (0 <= v <= 100):
                        raise ValueError
                except ValueError:
                    if self.owner is not None:
                        self.owner._want_mark(w, "error")
                    return tr("{0}: パンは0〜100で指定してください").format(
                        self._disp_name())
        # 区間指定(=59で音声/スクリプトへ拡張): 数値・開始>=0・終了>開始
        err = self._validate_range()
        if err:
            return err
        if self.mode == "auto":
            return None
        return self._validate_track_rows(self._disp_name())

    def _validate_range(self) -> str | None:
        """アイテムの区間欄(開始秒/終了秒)を検証する(=59で全種別が対象)。"""
        return _validate_range_fields(
            self.vstart_var, self.vend_var, self.vstart_entry, self.vend_entry,
            self._disp_name(), self.owner)

    def _mark_owner(self, widget):
        if self.owner is not None:
            self.owner._want_mark(widget, "error")

    def device_types(self) -> set:
        """このアイテムが担当するデバイス種別の集合(相関制御用)。

        手動=トラック行の種別 / 自動=命名ルールで解決できた種別。
        動画アイテムの種別一意性(動画トラック vs チャンネルのdevice担当)を
        エディタ側で先回り防止するために使う(=50の VideoRow から移設)。
        """
        if self.mode == "manual":
            return self.track_types()
        return {t for t, _p in self._resolve_auto()}

    def collect(self) -> dict:
        """編集内容をitem dictへ書き戻す(未対応フィールドは保持)。

        自動モード=キー省略(読み込み時に命名ルールで解決)。
        手動モード=tracksを明示(0本は "funscript": null)。
        """
        item = dict(self.orig)
        # =59: 区間はアイテム共通の "range" キーへ一本化(音声/動画/スクリプト)。
        # 旧形式(video辞書内の start/end)はここで落とすので、開いて保存し直せば
        # 新形式に揃う。
        item.pop("range", None)
        rng = _range_dict(self.vstart_var, self.vend_var)
        if rng:
            item["range"] = rng
        if self.is_video:
            # 動画アイテム(=52): audio は書かず video を書く(常に文字列)
            item.pop("audio", None)
            item["video"] = self.video_rel
        elif self.is_script:
            # スクリプトのみアイテム: audio キーを書かない
            item.pop("audio", None)
            item.pop("video", None)
        else:
            item["audio"] = self.audio_rel
            item.pop("video", None)
        item.pop("tracks", None)
        item.pop("funscript", None)
        # weight(ランダム再生chのみ。既定1.0は書かない)。
        # =74: 変数参照は {"var":..}、定数は 0/負も含め 1.0 以外を書く
        item.pop("weight", None)
        if self._random:
            raw_w = self.weight_field.get_raw()
            if isinstance(raw_w, dict):
                item["weight"] = raw_w
            else:
                txt = raw_w.strip()
                if txt:
                    try:
                        wv = float(txt)
                        if wv != 1.0:
                            item["weight"] = wv
                    except ValueError:
                        pass
        # pan上書き(ONのときだけ書く。0〜100%→0.0〜1.0)
        item.pop("pan", None)
        if self.pan_var.get() and not self.is_video:
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                item["pan"] = {"left": lp / 100.0, "right": rp / 100.0}
            except ValueError:
                pass
        if self.mode == "auto":
            return item
        return self._write_tracks(item)


class BgmItemRow(ctk.CTkFrame):
    """BGMチャンネルの1曲の編集行(=256)。

    通常のItemRowと違い、内容は**ファイル+アイテム個別パンのみ**
    (Q6=最小構成。重み・区間・変数操作・トラックは対象外)。並べ替えは
    ▲▼ボタン(BGMは再生順が意味を持つが、行ドラッグ機構を持ち込むほどの
    件数にならない想定)。レビューは音声試聴のみ(device_off 指定で
    ItemReviewDialog を流用=252のOFF時と同じ画面)。
    """

    def __init__(self, master, raw_item, base_dir, on_delete, on_move,
                 owner=None):
        super().__init__(master, fg_color=("gray82", "gray24"),
                         corner_radius=8)
        self.base_dir = base_dir
        self.owner = owner
        self.on_delete = on_delete
        self.on_move = on_move
        self.is_video = False    # open_item_review の動画(mpv必須)ガード用
        self.orig = {"audio": raw_item} if isinstance(raw_item, str) \
            else dict(raw_item)
        self.audio_rel = self.orig.get("audio", "")

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=4)
        self.head = head

        # ✕/▲▼/レビュー/パンを**ラベルより先に**pack(=101の作法: 長い
        # ファイル名がラベル幅を食い尽くしても操作類が消えないように)
        del_btn = ctk.CTkButton(
            head, text="✕", width=26, height=26,
            fg_color="transparent", text_color="#e05a5a",
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_delete(self))
        del_btn.pack(side="right", padx=(2, 0))
        self.down_btn = ctk.CTkButton(
            head, text="▼", width=26, height=26,
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_move(self, +1))
        self.down_btn.pack(side="right")
        self.up_btn = ctk.CTkButton(
            head, text="▲", width=26, height=26,
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_move(self, -1))
        self.up_btn.pack(side="right", padx=(6, 0))
        self.review_btn = ctk.CTkButton(
            head, text=tr("レビュー"), width=68, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
            command=self._open_review)
        self.review_btn.pack(side="right", padx=(6, 2))

        # パン(アイテム個別・0〜100%)。上書きON時のみL/R欄を出す
        self.pan_fields = ctk.CTkFrame(head, fg_color="transparent")
        self.pan_l_var = tk.StringVar(value="")
        self.pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.pan_fields, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_l_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_l_var,
            font=ctk.CTkFont(size=11))
        self.pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.pan_fields, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_r_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_r_var,
            font=ctk.CTkFont(size=11))
        self.pan_r_entry.pack(side="left", padx=(2, 0))
        self.pan_var = tk.BooleanVar(value=False)
        self.pan_check = ctk.CTkCheckBox(
            head, text=tr("パン上書き"), variable=self.pan_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_pan_toggle)
        self.pan_check.pack(side="right", padx=(10, 0))

        # ラベルは最後にpack=右側の操作類を必ず残し、余り幅だけを使う
        self.audio_label = ctk.CTkLabel(
            head, text="", font=ctk.CTkFont(size=12), anchor="w")
        self.audio_label.pack(side="left", fill="x", expand=True)

        # ファイル名の省略表示(=101のItemRowと同じ作法)
        self._full_name = self.audio_rel
        self._name_fit_after = None
        self._name_font = tkfont.Font(family=appfont.FAMILY, size=-12)
        self._name_truncated = False
        self.audio_label.configure(text=self._full_name)
        head.bind("<Configure>", lambda _e: self._schedule_name_fit())
        self._name_tooltip = Tooltip(
            self.audio_label,
            lambda: self._full_name if self._name_truncated else "")

        # パンの初期値
        pan = self.orig.get("pan")
        if isinstance(pan, dict):
            self.pan_var.set(True)
            try:
                self.pan_l_var.set(
                    str(int(round(float(pan.get("left", 1.0)) * 100))))
                self.pan_r_var.set(
                    str(int(round(float(pan.get("right", 1.0)) * 100))))
            except (TypeError, ValueError):
                pass
        self._on_pan_toggle()

    # ---- ファイル名の省略表示(ItemRow=101と同じ) ----

    def _schedule_name_fit(self):
        if self._name_fit_after is not None:
            return
        try:
            self._name_fit_after = self.after(60, self._fit_name)
        except Exception:
            self._name_fit_after = None

    def _fit_name(self):
        self._name_fit_after = None
        try:
            avail = self.audio_label.winfo_width() - 6
        except Exception:
            return
        if avail <= 12 or not self._full_name:
            return
        fitted = _ellipsize_middle(self._full_name, self._name_font, avail)
        self._name_truncated = (fitted != self._full_name)
        if self.audio_label.cget("text") != fitted:
            self.audio_label.configure(text=fitted)

    def _on_pan_toggle(self):
        if self.pan_var.get():
            if not self.pan_fields.winfo_manager():
                self.pan_fields.pack(side="right", padx=(4, 0))
            if not self.pan_l_var.get() and not self.pan_r_var.get():
                self.pan_l_var.set("100")
                self.pan_r_var.set("100")
        else:
            self.pan_fields.pack_forget()

    def _open_review(self):
        if self.owner is not None:
            self.owner.open_item_review(self)

    def review_spec(self) -> dict:
        """レビュー画面へ渡す内容(音声試聴のみ=device_off)。"""
        audio = os.path.join(self.base_dir, self.audio_rel) \
            if self.audio_rel else ""
        return {"name": os.path.basename(self.audio_rel or ""),
                "audio": audio, "video": "",
                "audio_lo": 0.0, "audio_hi": None,
                "tracks": [], "warn": [], "device_off": True}

    def collect(self):
        """JSONアイテムへ。パン上書きが無ければ簡潔な文字列書式で返す。"""
        if self.pan_var.get():
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                return {"audio": self.audio_rel,
                        "pan": {"left": lp / 100.0, "right": rp / 100.0}}
            except ValueError:
                pass
        return self.audio_rel
