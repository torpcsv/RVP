"""シナリオ編集: チャンネル枠(ChannelSection)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import tkinter as tk
from ..scenario import DEFAULT_PAN
from ..i18n import tr

from .common import (BOX_BG, BOX_BORDER, CTkOptionMenu, INFINITE_CHOICE,
    MUTED, TEXT_MUTED, VIDEO_EXTS, VIDEO_FILETYPES, _dialog_initialdir,
    _has_varref, _remember_dialog_dir, _script_track_type)
from .fields import VarRefField
from .items import ItemRow
from .paths import _safe_relpath
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg


class ChannelSection(ctk.CTkFrame):
    """チャンネル1つ分の編集セクション。

    state_mode=True のとき、終了条件に「無限(移行/終了まで)」が選べる。
    無限は end キーを書かないことで表現する(scenario側の既定挙動)。
    """

    END_CHOICES = (tr("1周で終了"), tr("N周で終了"), tr("N秒で終了"), tr("N回再生で終了"))

    def __init__(self, master, ch_id: str, base_dir: str, owner=None,
                 compact: bool = False):
        super().__init__(master, corner_radius=10, fg_color=BOX_BG,
                         border_width=1, border_color=BOX_BORDER)
        self.ch_id = ch_id
        self.base_dir = base_dir
        self.owner = owner   # ScenarioEditor(変数コンテキスト用)。Noneも可
        self.orig = {}      # 元のチャンネルdict(未対応フィールド保持)
        self.rows: list[ItemRow] = []
        self._pool: list[ItemRow] = []   # 使い回し用に隠した余剰行(破棄しない)
        self.state_mode = False
        # 「無限(移行/終了まで)」を選べるか。state_mode か、または通常イベントで
        # 「指定チャンネル終了」の対象外チャンネル(=対象chが終わるまでループ可)。
        self.allow_infinite = False
        # =63: 終了条件を「無限」だけに絞るか(スクリプト専用chのみのイベント)
        self.infinite_only = False
        self.compact = compact   # True: 幅約320pxに収めるため見出しを縦積み

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 2))

        # compact時は「有効化」の下に「再生方式+終了条件」を1行で置く。
        # (=58: 以前は終了条件をさらに1段下げていたが、編集画面が1360px幅に
        #  なって横に余裕ができたので1行にまとめ、縦を約32px節約する。
        #  アイテム行を見ながら編集できるようにするためのユーザー要望。)
        # 通常時は従来どおり横1列。
        row_a = ctk.CTkFrame(head, fg_color="transparent")
        row_a.pack(fill="x")
        row_b = ctk.CTkFrame(head, fg_color="transparent") if compact else row_a
        row_c = row_b
        if compact:
            row_b.pack(fill="x", pady=(4, 0))

        self.enabled_var = tk.BooleanVar(value=False)
        self.enable_check = ctk.CTkCheckBox(
            row_a, text=tr('チャンネル {0}').format(ch_id), variable=self.enabled_var,
            font=ctk.CTkFont(size=13, weight="bold"),
            checkbox_width=18, checkbox_height=18,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_toggle,
        )
        self.enable_check.pack(side="left")

        # 詳細(シーク追従/パン/区間/インターバル)トグル。row_a の右端。
        # =68: **枠なしのテキストリンク調**へ(ユーザー要望)。チャンネル見出しに
        # 枠付きボタンが2個×3ch並ぶと、頻度の高い「＋音声」等と同じ強さで
        # 主張して見た目が複雑になっていた。開閉は▾▴を消して**文字色**で示す
        # (開=アクセント色 / 閉=灰。テキストは常に「詳細設定」)。
        self.detail_open = False
        self.detail_btn = ctk.CTkButton(
            row_a, text=tr("詳細設定"), width=64, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=0,
            text_color=TEXT_MUTED, hover_color=("gray85", "gray25"),
            command=self._toggle_detail)
        self.detail_btn.pack(side="right")

        # 他のイベント/ステート/チャンネルから内容を丸ごとコピーしてくるボタン。
        # 押下で owner がモーダルを開く。見た目は「詳細設定」と同じテキスト調。
        self.copy_from_btn = ctk.CTkButton(
            row_a, text=tr("他からコピー"), width=76, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=0,
            text_color=TEXT_MUTED, hover_color=("gray85", "gray25"),
            command=self._on_copy_from)
        self.copy_from_btn.pack(side="right", padx=(0, 2))

        # =76: 「ランダム(重複なし)」追加に伴い幅を広げた(compact 116→165。
        # EN "Random (no repeat)"=130px が矢印込みで収まる幅。row_b には
        # 終了条件も並ぶが1360px幅の編集画面では収まる=スクリーンショットで
        # 確認済み)。
        self.mode_var = tk.StringVar(value=tr("順番に再生"))
        self.mode_menu = CTkOptionMenu(
            row_b, variable=self.mode_var, width=165 if compact else 170,
            height=26,
            values=[tr("順番に再生"), tr("ランダム再生"),
                    tr("ランダム(重複なし)")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_mode_change(),
        )
        self.mode_menu.pack(side="left", padx=(0, 6) if compact else 10)

        self.end_var = tk.StringVar(value=tr("1周で終了"))
        self.end_menu = CTkOptionMenu(
            row_c, variable=self.end_var, width=104 if compact else 110,
            height=26,
            values=list(self.END_CHOICES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_end_ui(),
        )
        self.end_menu.pack(side="left", padx=(0, 4) if compact else 4)
        self.end_field = VarRefField(row_c, width=54, placeholder=tr("値"),
                                     on_change=self._update_end_ui)
        self.end_field.pack(side="left", padx=2)
        self.end_unit_label = ctk.CTkLabel(row_c, text="", font=ctk.CTkFont(size=11),
                                           text_color=TEXT_MUTED)
        self.end_unit_label.pack(side="left")
        # =99: 「N秒で終了」の範囲抽選([min]秒 〜 [max]秒)。当初はコンボの
        # 右に置く案(コンボ幅を狭めて捻出)だったが、実測で EN 表示と
        # 変数トグル(x)/変数メニュー表示時に行へ収まらず(tkのpackは
        # 収まらない末尾ウィジェットを非表示にする)、**コンボの下の専用行**
        # に置く方式へ変更した。この行は「N秒で終了」選択時のみ表示され、
        # 他の終了条件では高さを消費しない。
        self._row_b = row_b
        self.end_range_row = ctk.CTkFrame(head, fg_color="transparent")
        self.end_rmin = VarRefField(self.end_range_row, width=54,
                                    placeholder=tr("min"))
        self.end_rmin.pack(side="left", padx=(2, 0))
        self.end_rmin_unit = ctk.CTkLabel(
            self.end_range_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.end_rmin_unit.pack(side="left")
        ctk.CTkLabel(self.end_range_row, text="〜",
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED).pack(
            side="left", padx=2)
        self.end_rmax = VarRefField(self.end_range_row, width=54,
                                    placeholder=tr("max"))
        self.end_rmax.pack(side="left", padx=(0, 0))
        self.end_rmax_unit = ctk.CTkLabel(
            self.end_range_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.end_rmax_unit.pack(side="left")
        self.end_range_hint = ctk.CTkLabel(
            self.end_range_row, text=tr("(範囲は抽選)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.end_range_hint.pack(side="left", padx=6)

        # ---- 詳細: チャンネル既定パン(上書き) / インターバル ----
        # detail_frame(=チャンネルのパン上書き)は head 内。
        # 「パン詳細」トグルON時のみ表示。既定OFF。
        self.detail_frame = ctk.CTkFrame(head, fg_color="transparent")
        # シークバー追従ラジオ(L/C/Rで1つを選ぶ。変数は owner.seek_var を共有)
        self.seek_radio = None
        seek_var = getattr(owner, "seek_var", None) if owner is not None else None
        if seek_var is not None:
            seek_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
            seek_row.pack(fill="x", pady=(4, 0))
            self.seek_radio = ctk.CTkRadioButton(
                seek_row, text=tr("シークバー追従"), variable=seek_var,
                value=ch_id, font=ctk.CTkFont(size=11),
                radiobutton_width=16, radiobutton_height=16,
                fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER)
            self.seek_radio.pack(side="left")
        pan_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        pan_row.pack(fill="x", pady=(4, 0))
        self.pan_override_var = tk.BooleanVar(value=False)
        self.pan_check = ctk.CTkCheckBox(
            pan_row, text=tr("パン上書き(L/R %)"), variable=self.pan_override_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_ch_pan_toggle)
        self.pan_check.pack(side="left")
        self.pan_entry_box = ctk.CTkFrame(pan_row, fg_color="transparent")
        self.pan_l_var = tk.StringVar(value="")
        self.pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.pan_entry_box, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_l_entry = ctk.CTkEntry(
            self.pan_entry_box, width=42, height=24, textvariable=self.pan_l_var,
            font=ctk.CTkFont(size=11))
        self.pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.pan_entry_box, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_r_entry = ctk.CTkEntry(
            self.pan_entry_box, width=42, height=24, textvariable=self.pan_r_var,
            font=ctk.CTkFont(size=11))
        self.pan_r_entry.pack(side="left", padx=(2, 0))

        # =64: インターバルも「詳細設定」の中へ移動(ユーザー要望)。
        # detail_frame の中に置くので、トグルONのときだけ現れる。
        iv_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        iv_row.pack(fill="x", pady=(4, 0))
        self.iv_row = iv_row
        self.iv_var = tk.BooleanVar(value=False)
        self.iv_check = ctk.CTkCheckBox(
            iv_row, text=tr("インターバル"), variable=self.iv_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_iv_toggle)
        self.iv_check.pack(side="left")
        # =67: 「下限[__]〜[__]上限」→「[__]秒 〜 [__]秒」(単位を各欄に添える)。
        # チェックのラベルから「(秒)」を外せるので、compact幅にも余裕が出る。
        self.iv_box = ctk.CTkFrame(iv_row, fg_color="transparent")
        self.iv_min_var = tk.StringVar(value="")
        self.iv_max_var = tk.StringVar(value="")
        self.iv_min_entry = ctk.CTkEntry(
            self.iv_box, width=48, height=24, textvariable=self.iv_min_var,
            font=ctk.CTkFont(size=11))
        self.iv_min_entry.pack(side="left", padx=(2, 2))
        ctk.CTkLabel(self.iv_box, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(self.iv_box, text="〜", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(4, 4))
        self.iv_max_entry = ctk.CTkEntry(
            self.iv_box, width=48, height=24, textvariable=self.iv_max_var,
            font=ctk.CTkFont(size=11))
        self.iv_max_entry.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(self.iv_box, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=10, pady=(0, 8))

        # height=0 にしないと、音声0件の空フレームが CTkFrame 既定の200pxを
        # 確保してしまい、空チャンネルだけ縦に間延びする(中身が入れば追従)。
        self.items_frame = ctk.CTkFrame(self.body, fg_color="transparent",
                                        height=0)
        self.items_frame.pack(fill="x")

        # =58: 表示名を「＋音声」「＋動画」「＋スクリプト」へ短縮し、
        # この順に並べる(ユーザー決定)。3チャンネル分が横に並ぶため、
        # 短いラベルの方が枠内に収まりやすい。
        btn_row = ctk.CTkFrame(self.body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(6, 0))
        self.add_audio_btn = ctk.CTkButton(
            btn_row, text=tr("＋音声"), width=84, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_items,
        )
        self.add_audio_btn.pack(side="left")
        # 動画アイテムの追加(=52 フェーズ3-B)。動画はチャンネルの中身に
        # なったので、音声と同じ「＋追加」で並べられる。音声/スクリプトとの
        # 混在は禁止のため _update_add_buttons が相互排他する。
        # 動画を置けるチャンネルは1つだけ(owner が他chを無効化する)。
        self.add_video_btn = ctk.CTkButton(
            btn_row, text=tr("＋動画"), width=84, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_videos,
        )
        self.add_video_btn.pack(side="left", padx=(6, 0))
        # スクリプトのみアイテムの追加(スクリプト専用チャンネル用)。
        # 音声アイテムとの混在は禁止のため、_update_add_buttons が相互排他する。
        self.add_script_btn = ctk.CTkButton(
            btn_row, text=tr("＋スクリプト"), width=104, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_scripts,
        )
        self.add_script_btn.pack(side="left", padx=(6, 0))

        # =160: **D&Dできることの控えめな告知**(ユーザー決定)。
        # 音声/動画/funscript/CSV は =44/=52/=65 からドラッグ&ドロップで
        # 追加できるが、画面上に手がかりが無く**知らないと気づけない**
        # ままだった。追加ボタンの並びの右に、薄い小さな文字で「(D&D可)」と
        # だけ置く。**行そのものは既にあるので縦の高さは増えず**、
        # レイアウトは動かない。
        # ・幅が足りないときに削られるのは最後に置いたこのラベル
        #   (packの取り分は後ろから削られる=159)。ボタンは無傷で残る。
        # ・tkinterdnd2 が無い環境ではD&D自体が効かないので**出さない**
        #   (嘘の案内にしない)。owner の `_dnd_ok` を見るが、__init__ での
        #   D&D初期化は _build_ui より**後**なので、最初のパネルの分は
        #   ScenarioEditor 側の `_refresh_dnd_hints()` が後から出す。
        self.dnd_hint = ctk.CTkLabel(
            btn_row, text=tr("(D&D可)"), font=ctk.CTkFont(size=11),
            text_color=MUTED, anchor="w",
        )
        self.set_dnd_hint(bool(getattr(self.owner, "_dnd_ok", False)))

        self._update_end_ui()
        self._update_enabled()
        self._update_add_buttons()

    def set_dnd_hint(self, on: bool):
        """「(D&D可)」の表示/非表示(=160)。D&Dが使える環境でだけ出す。"""
        try:
            if on:
                if not self.dnd_hint.winfo_ismapped():
                    self.dnd_hint.pack(side="left", padx=(10, 0))
            else:
                self.dnd_hint.pack_forget()
        except Exception:
            pass

    # ---- 詳細(パン/インターバル) ----

    def _toggle_detail(self):
        self._set_detail(not self.detail_open)

    def _on_copy_from(self):
        """「他からコピー」= 他のイベント/ステート/チャンネルの内容を取り込む。"""
        if self.owner is not None:
            self.owner._open_channel_copy(self.ch_id)

    def _set_detail(self, flag: bool):
        """詳細設定の開閉。シーク追従・パン上書き・区間・インターバル(=64)を
        チャンネルと各アイテム行でまとめて出し入れする。"""
        self.detail_open = bool(flag)
        if self.detail_open:
            # シークバー追従・パン上書き・区間・インターバル(=64)をまとめて出す
            self.detail_frame.pack(fill="x", pady=(2, 0))
            # =68: 開閉は文字色で示す(▾▴の記号は廃止=ユーザー要望)
            self.detail_btn.configure(text_color=_clr.ACCENT_TEXT)
        else:
            self.detail_frame.pack_forget()
            self.detail_btn.configure(text_color=TEXT_MUTED)
        for r in self.rows:
            r.set_detail(self.detail_open)

    def _default_pan_pct(self) -> tuple[int, int]:
        """このチャンネルのデフォルトパン(%)。"""
        l, r = DEFAULT_PAN.get(self.ch_id, (1.0, 1.0))
        return int(round(l * 100)), int(round(r * 100))

    def _effective_pan_pct(self) -> tuple[int, int]:
        """行のpan上書き初期値に使う、chの実効パン(%)。上書きON時はその値。"""
        if self.pan_override_var.get():
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                return lp, rp
            except ValueError:
                pass
        return self._default_pan_pct()

    def _on_ch_pan_toggle(self):
        if self.pan_override_var.get():
            dl, dr = self._default_pan_pct()
            if not self.pan_l_var.get():
                self.pan_l_var.set(str(dl))
            if not self.pan_r_var.get():
                self.pan_r_var.set(str(dr))
            self.pan_entry_box.pack(side="left", padx=(6, 0))
        else:
            self.pan_entry_box.pack_forget()
        self._apply_row_correlations()

    def _on_iv_toggle(self):
        if self.iv_var.get():
            if not self.iv_min_var.get():
                self.iv_min_var.set("0.0")
            if not self.iv_max_var.get():
                self.iv_max_var.set("0.0")
            self.iv_box.pack(side="left", padx=(6, 0))
        else:
            self.iv_box.pack_forget()

    def _sync_detail_visibility(self):
        """var の状態に合わせて入力欄の表示だけ更新する(値は変えない)。"""
        if self.pan_override_var.get():
            self.pan_entry_box.pack(side="left", padx=(6, 0))
        else:
            self.pan_entry_box.pack_forget()
        if self.iv_var.get():
            self.iv_box.pack(side="left", padx=(6, 0))
        else:
            self.iv_box.pack_forget()

    def _apply_row_correlations(self):
        """各音声行へ相関情報(weight表示可否・pan詳細の開閉・pan上書き初期値)を伝える。"""
        is_rand = self._is_random()
        dl, dr = self._effective_pan_pct()
        for r in self.rows:
            r.set_random(is_rand)
            r.set_channel_default_pan(dl, dr)
            r.set_detail(self.detail_open)

    @staticmethod
    def _parse_interval_secs(raw) -> tuple[float, float]:
        """interval指定(秒 or {min,max})を (min, max) 秒で返す。未指定は(0,0)。"""
        if raw is None:
            return 0.0, 0.0
        if isinstance(raw, (int, float)):
            return float(raw), float(raw)
        if isinstance(raw, dict):
            try:
                mn = float(raw.get("min", 0) or 0)
                mx = float(raw.get("max", raw.get("min", 0)) or 0)
                return mn, mx
            except (TypeError, ValueError):
                return 0.0, 0.0
        return 0.0, 0.0

    # ---- 読み込み/書き出し ----

    def _is_random(self) -> bool:
        # =76: 「ランダム(重複なし)」もランダム扱い(終了条件の相関・
        # 重み欄の表示可否はランダム再生と同じ。=98: 終了条件のみ
        # 「N周で終了」が追加で選べる=_is_bag)。
        return self.mode_var.get() in (tr("ランダム再生"),
                                       tr("ランダム(重複なし)"))

    def _is_bag(self) -> bool:
        # =98: 「ランダム(重複なし)」だけの相関(N周=袋N回で終了)。
        return self.mode_var.get() == tr("ランダム(重複なし)")

    def _default_finite_end(self) -> str:
        """無限が使えなくなった時の既定終了条件(モードに合う有効な値)。"""
        # ランダム再生は「1周で終了」「N周で終了」が使えないため「N秒で終了」を既定に
        return tr("N秒で終了") if self._is_random() else tr("1周で終了")

    def _end_menu_values(self) -> list:
        """相関制御: 再生方式と無限可否に応じた終了条件の選択肢。

        - ランダム再生: 「1周で終了」「N周で終了」は無効(リストを順に回る概念が
          無い)ので外し、「N秒で終了」「N回再生で終了」のみ。
        - ランダム(重複なし): 上に加えて「N周で終了」も可(=98。袋方式では
          1周=袋を1回使い切る、が明確に定義できるため)。
        - 順番に再生: 「N秒で終了」「N回再生で終了」は無効(順番再生はリストの
          周回=1周/N周で終了を表す)ので外し、「1周で終了」「N周で終了」のみ。
        無限は state_mode か allow_infinite のときだけ出す。
        """
        if self._is_random():
            vals = [tr("N秒で終了"), tr("N回再生で終了")]
            if self._is_bag():
                vals.append(tr("N周で終了"))
        else:
            vals = [tr("1周で終了"), tr("N周で終了")]
        if self.state_mode or self.allow_infinite:
            vals.append(INFINITE_CHOICE)
            if getattr(self, "infinite_only", False):
                # =63: スクリプト専用chだけのイベントで終了条件が
                # 「合計時間/変数条件」のときは無限のみ(有限だとスクリプトが
                # 終わった時点でイベントも終わってしまい紛らわしいため)
                return [INFINITE_CHOICE]
        return vals

    def _refresh_end_menu(self):
        """終了条件メニューの選択肢を現在の相関(方式/無限可否)に合わせて更新。

        現在の選択が選択肢から外れたら、モードに合う既定値へ寄せる。
        (変数指定でロック中は触らない)
        """
        vals = self._end_menu_values()
        self.end_menu.configure(values=vals)
        if getattr(self, "end_locked", False):
            return
        # 変数指定の終了条件(N分/N回等)はJSONから読み込んだ既存のバインドを
        # 壊さないよう、相関で選択肢外になっても保持する(新規選択のみ制限)。
        if getattr(self.end_field, "use_var", False):
            return
        if self.end_var.get() not in vals:
            self.end_var.set(INFINITE_CHOICE if vals == [INFINITE_CHOICE]
                             else self._default_finite_end())
            self._update_end_ui()

    def _on_mode_change(self):
        """再生方式が変わったとき、終了条件メニューと行の相関を更新する。"""
        self._refresh_end_menu()
        # =286: 方式を変えたら終了条件をその方式の既定へ(順番=1周で終了、
        # ランダム系=N秒で終了)。「無限」のままにしない(ユーザー依頼)。
        # 変数指定でロック中/変数参照中は従来どおり触らない。
        if not getattr(self, "end_locked", False) \
                and not getattr(self.end_field, "use_var", False) \
                and self.end_var.get() == INFINITE_CHOICE \
                and self._default_finite_end() in self._end_menu_values():
            self.end_var.set(self._default_finite_end())
            self._update_end_ui()
        self._apply_row_correlations()   # weight表示可否を各行へ反映

    def set_state_mode(self, flag: bool, allow_infinite: bool | None = None):
        """ステート内チャンネルか(終了条件に「無限」を追加)。

        allow_infinite を明示すると、通常イベントでも「無限」を選べる
        (指定チャンネル終了の対象外ch用)。None なら従来どおり state_mode に従う。
        """
        self.state_mode = flag
        if allow_infinite is not None:
            self.allow_infinite = allow_infinite
        self._refresh_end_menu()

    def set_allow_infinite(self, flag: bool):
        """通常イベントでの「無限」選択可否を切り替える(指定ch終了の対象外ch)。"""
        if flag == self.allow_infinite:
            return
        self.allow_infinite = flag
        self._refresh_end_menu()

    def set_infinite_only(self, flag: bool):
        """終了条件を「無限」だけに絞る(=63・スクリプト専用chのみのイベント)。"""
        flag = bool(flag)
        if flag == getattr(self, "infinite_only", False):
            return
        self.infinite_only = flag
        self._refresh_end_menu()

    def is_script_only(self) -> bool:
        """このチャンネルがスクリプト専用ch(音声も動画も無い)か(=63)。"""
        if not self.rows:
            return False
        return all(getattr(r, "is_script", False) for r in self.rows)

    def _numeric_names(self) -> list[str]:
        return self.owner._numeric_var_names() if self.owner else []

    def load(self, ch_raw: dict | None, state_mode: bool = False,
             allow_infinite: bool = False):
        self.set_state_mode(state_mode, allow_infinite)
        inf_ok = state_mode or allow_infinite
        # 音声行(ItemRow)は destroy せず _set_rows で使い回す(生成コスト削減)
        self.end_locked = False
        self.end_field.set_names(self._numeric_names())
        self.end_rmin.set_names(self._numeric_names())
        self.end_rmax.set_names(self._numeric_names())
        if ch_raw is None:
            self.orig = {}
            self.enabled_var.set(False)
            self.mode_var.set(tr("順番に再生"))
            # =286: 新しく有効にするchの既定は「1周で終了」(ステート内でも。
            # 従来は無限が許される文脈なら「無限」だった=ユーザー依頼で変更)
            self.end_var.set(tr("1周で終了"))
            self.end_field.set("")
            self.end_rmin.set("")
            self.end_rmax.set("")
        else:
            self.orig = dict(ch_raw)
            self.enabled_var.set(True)
            raw_mode = ch_raw.get("mode")
            self.mode_var.set(
                tr("ランダム再生") if raw_mode == "random"
                else tr("ランダム(重複なし)") if raw_mode == "random_bag"
                else tr("順番に再生"))
            end = ch_raw.get("end")
            etype = (end or {}).get("type")
            # 時間指定は秒のみ(minutesは廃止=読まない)
            has_ref = isinstance(end, dict) and _has_varref(
                end.get("count"), end.get("seconds"))
            self.end_rmin.set("")
            self.end_rmax.set("")
            if has_ref and etype == "duration" and isinstance(end.get("seconds"), dict):
                self.end_var.set(tr("N秒で終了"))
                self.end_rmin.set(end.get("seconds"))
                self.end_rmax.set(end.get("seconds"))   # min==max(単一値)
            elif has_ref and etype in ("repeat", "plays"):   # count が変数
                self.end_var.set(tr("N周で終了") if etype == "repeat"
                                 else tr("N回再生で終了"))
                self.end_field.set(end.get("count"))
            elif end is None and inf_ok:
                # 無限が許可される文脈(ステート内 or 指定ch終了の対象外ch)で
                # end 省略 = 移行/イベント終了まで無限
                self.end_var.set(INFINITE_CHOICE)
                self.end_field.set("")
            elif etype == "none" and inf_ok:
                self.end_var.set(INFINITE_CHOICE)
                self.end_field.set("")
            elif etype == "repeat":
                self.end_var.set(tr("N周で終了"))
                self.end_field.set(str(end.get("count", 1)))
            elif etype == "plays":
                self.end_var.set(tr("N回再生で終了"))
                self.end_field.set(str(end.get("count", 1)))
            elif etype == "duration":
                # 秒のみ。旧minutesは無視するので、seconds無し=空欄(再入力を促す)
                # =99: 範囲(min_seconds/max_seconds)は2欄へ、単一 seconds は
                # min==max として両欄に同じ値を入れる。各値は定数 or 変数参照
                self.end_var.set(tr("N秒で終了"))
                if "min_seconds" in end or "max_seconds" in end:
                    lo = end.get("min_seconds", 0)
                    hi = end.get("max_seconds", lo)
                else:
                    lo = hi = end.get("seconds")
                def _dsec(v):
                    if isinstance(v, dict):
                        return v
                    return f"{float(v):g}" if isinstance(v, (int, float)) else ""
                self.end_rmin.set(_dsec(lo))
                self.end_rmax.set(_dsec(hi))
            else:
                self.end_var.set(tr("1周で終了"))
                self.end_field.set("")
        # 詳細: パン上書き / インターバルを反映
        pan = (ch_raw or {}).get("pan")
        if isinstance(pan, dict) and "left" in pan and "right" in pan:
            self.pan_override_var.set(True)
            self.pan_l_var.set(str(int(round(float(pan["left"]) * 100))))
            self.pan_r_var.set(str(int(round(float(pan["right"]) * 100))))
        else:
            self.pan_override_var.set(False)
            self.pan_l_var.set("")
            self.pan_r_var.set("")
        iv_min, iv_max = self._parse_interval_secs((ch_raw or {}).get("interval"))
        if iv_max > 0 or iv_min > 0:
            self.iv_var.set(True)
            self.iv_min_var.set(f"{iv_min:g}")
            self.iv_max_var.set(f"{iv_max:g}")
        else:
            self.iv_var.set(False)
            self.iv_min_var.set("")
            self.iv_max_var.set("")
        # 「パン詳細」は既定OFF(データにpan上書きがあっても畳んだまま。
        #  値は保持され、パン詳細を開けば表示される)。現在の開閉状態は維持する。
        self._set_detail(self.detail_open)
        self._sync_detail_visibility()
        # 音声行を使い回して設定(件数差分だけ生成/破棄)
        self._set_rows([] if ch_raw is None else ch_raw.get("items", []))
        self._refit_items()   # 0件なら枠を縮める(空チャンネルの間延び防止)
        # 各行へ相関(weight表示可否・pan上書き初期値)を反映
        self._apply_row_correlations()
        # 音声/スクリプト追加ボタンの相互排他(混在禁止)を反映
        self._update_add_buttons()
        # 読み込んだ再生方式に合わせて終了条件メニューの選択肢を相関更新
        self._refresh_end_menu()
        self._update_end_ui()
        self._update_enabled()
        if self.end_locked:
            self.end_menu.configure(state="disabled")
            self.end_field.set_state("disabled")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")
        else:
            self.end_menu.configure(state="normal")

    def collect(self) -> dict | None:
        """有効なら編集内容をチャンネルdictへ、無効ならNone。"""
        if not self.enabled_var.get():
            return None
        ch = dict(self.orig)
        m = self.mode_var.get()
        ch["mode"] = ("random" if m == tr("ランダム再生")
                      else "random_bag" if m == tr("ランダム(重複なし)")
                      else "sequential")
        self._collect_pan_interval(ch)
        choice = self.end_var.get()
        if getattr(self, "end_locked", False):
            # UIで表せない変数指定の終了条件は元のendをそのまま保持する
            ch["items"] = [r.collect() for r in self.rows]
            return ch
        ch.pop("end", None)
        use_var = self.end_field.use_var
        raw = self.end_field.get_raw()
        if choice == tr("N周で終了"):
            ch["end"] = {"type": "repeat",
                         "count": raw if use_var else int(float(raw or 1))}
        elif choice == tr("N回再生で終了"):
            ch["end"] = {"type": "plays",
                         "count": raw if use_var else int(float(raw or 1))}
        elif choice == tr("N秒で終了"):
            # 時間指定は秒のみ(minutesは廃止)。=99: min==max は単一 seconds、
            # 異なれば範囲 min_seconds/max_seconds で保存(イベント終了と同じ)
            lo_var, hi_var = self.end_rmin.use_var, self.end_rmax.use_var
            lo = (self.end_rmin.get_raw() if lo_var
                  else float(self.end_rmin.get_text() or 0))
            if hi_var:
                hi = self.end_rmax.get_raw()
            elif self.end_rmax.get_text().strip():
                hi = float(self.end_rmax.get_text())
            else:
                hi = lo          # max空欄=minと同じ(minが変数なら同じ変数)
                hi_var = lo_var
            same = (lo_var == hi_var) and (
                (lo_var and lo.get("var") == hi.get("var"))
                or (not lo_var and lo == hi))
            if same:
                ch["end"] = {"type": "duration", "seconds": lo}
            else:
                if not lo_var and not hi_var and hi < lo:
                    lo, hi = hi, lo
                ch["end"] = {"type": "duration",
                             "min_seconds": lo, "max_seconds": hi}
        elif choice == INFINITE_CHOICE:
            # **通常イベントでは end を省略すると「1周で終了」(sequential の
            # 既定)になってしまう**ため、無限は必ず {"type":"none"} を明示する
            # (=55の不具合修正。ステート内は省略でも無限だが、読んだときに
            #  意図が分かるよう同じく明示する)。
            ch["end"] = {"type": "none"}
        else:
            ch["end"] = {"type": "once"}
        ch["items"] = [r.collect() for r in self.rows]
        return ch

    def _collect_pan_interval(self, ch: dict):
        """チャンネル既定パン / インターバルを ch dict へ書く(未使用ならキー削除)。"""
        ch.pop("pan", None)
        if self.pan_override_var.get():
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                ch["pan"] = {"left": lp / 100.0, "right": rp / 100.0}
            except ValueError:
                pass
        ch.pop("interval", None)
        if self.iv_var.get():
            try:
                mn = round(float(self.iv_min_var.get() or 0), 1)
                mx = round(float(self.iv_max_var.get() or 0), 1)
                if mx > 0 or mn > 0:
                    ch["interval"] = mn if mn == mx else {"min": mn, "max": mx}
            except ValueError:
                pass

    def _mark(self, widget, kind="error"):
        """検証エラーの原因ウィジェットを owner(編集画面)へ記録する。"""
        if self.owner is not None:
            self.owner._want_mark(widget, kind)

    def validate(self) -> str | None:
        if not self.enabled_var.get():
            return None
        if not self.rows:
            self._mark(self.enable_check, "error")
            return tr('チャンネル{0}: 音声が1つもありません').format(self.ch_id)
        for r in self.rows:
            err = r.validate()
            if err:
                return tr('チャンネル{0}: {1}').format(self.ch_id, err)
        choice = self.end_var.get()
        if choice == tr("N秒で終了") \
                and not getattr(self, "end_locked", False):
            # =99: min秒〜max秒の範囲(max空欄=min)。検証規則はイベント終了の
            # 範囲(evend)と同じ: 各欄は正の数 or 変数、min≤0はminのみ0可
            lo_var, hi_var = self.end_rmin.use_var, self.end_rmax.use_var
            try:
                if lo_var:
                    if not self.end_rmin.get_raw().get("var"):
                        raise ValueError
                    lo = None
                else:
                    lo = float(self.end_rmin.get_text())
                if hi_var:
                    if not self.end_rmax.get_raw().get("var"):
                        raise ValueError
                    hi = None
                elif self.end_rmax.get_text().strip():
                    hi = float(self.end_rmax.get_text())
                else:
                    hi = lo          # max空欄=minと同じ
                    hi_var = lo_var
            except ValueError:
                self._mark(self.end_rmin, "error")
                self._mark(self.end_rmax, "error")
                return tr('チャンネル{0}: 終了条件の値(min/max)が不正です').format(self.ch_id)
            if not lo_var and not hi_var:
                l, h = (lo, hi) if hi >= lo else (hi, lo)
                if l < 0 or h <= 0:
                    self._mark(self.end_rmin, "error")
                    self._mark(self.end_rmax, "error")
                    return tr('チャンネル{0}: 終了条件の秒数は0以上(最大は正の数)にしてください').format(self.ch_id)
        elif choice in (tr("N周で終了"), tr("N回再生で終了")) \
                and not getattr(self, "end_locked", False):
            if self.end_field.use_var:
                if not self.end_field.get_raw().get("var"):
                    self._mark(self.end_field, "error")
                    return tr('チャンネル{0}: 終了条件の変数を選択してください').format(self.ch_id)
            else:
                try:
                    v = float(self.end_field.get_text())
                    if v <= 0:
                        raise ValueError
                except ValueError:
                    self._mark(self.end_field, "error")
                    return tr('チャンネル{0}: 終了条件の値が不正です').format(self.ch_id)
        if self._is_bag():
            # =98: ランダム(重複なし)は「N周で終了」(袋N回)も可
            if self.state_mode or self.allow_infinite:
                if choice not in (tr("N秒で終了"), tr("N回再生で終了"),
                                  tr("N周で終了"), INFINITE_CHOICE):
                    self._mark(self.end_menu, "error")
                    return (tr('チャンネル{0}: ランダム(重複なし)の終了条件は「N秒で終了」「N回再生で終了」「N周で終了」「{1}」のいずれかにしてください').format(self.ch_id, INFINITE_CHOICE))
            elif choice not in (tr("N秒で終了"), tr("N回再生で終了"),
                                tr("N周で終了")):
                self._mark(self.end_menu, "error")
                return (tr('チャンネル{0}: ランダム(重複なし)の終了条件は「N秒で終了」「N回再生で終了」「N周で終了」のいずれかにしてください').format(self.ch_id))
        elif self._is_random():
            if self.state_mode or self.allow_infinite:
                if choice not in (tr("N秒で終了"), tr("N回再生で終了"), INFINITE_CHOICE):
                    self._mark(self.end_menu, "error")
                    return (tr('チャンネル{0}: ランダム再生の終了条件は「N秒で終了」「N回再生で終了」「{1}」のいずれかにしてください').format(self.ch_id, INFINITE_CHOICE))
            elif choice not in (tr("N秒で終了"), tr("N回再生で終了")):
                self._mark(self.end_menu, "error")
                return (tr('チャンネル{0}: ランダム再生の終了条件は「N秒で終了」か「N回再生で終了」にしてください').format(self.ch_id))
        elif not getattr(self, "end_locked", False) and not self.end_field.use_var:
            # 順番に再生(変数指定は既存バインド保持のため対象外)
            if self.state_mode or self.allow_infinite:
                if choice not in (tr("1周で終了"), tr("N周で終了"), INFINITE_CHOICE):
                    self._mark(self.end_menu, "error")
                    return (tr('チャンネル{0}: 順番に再生の終了条件は「1周で終了」「N周で終了」「{1}」のいずれかにしてください').format(self.ch_id, INFINITE_CHOICE))
            elif choice not in (tr("1周で終了"), tr("N周で終了")):
                self._mark(self.end_menu, "error")
                return (tr('チャンネル{0}: 順番に再生の終了条件は「1周で終了」か「N周で終了」にしてください').format(self.ch_id))
        # 詳細: パン上書き(0〜100) / インターバル(下限≤上限、0以上)
        if self.pan_override_var.get():
            for var, w in ((self.pan_l_var, self.pan_l_entry),
                           (self.pan_r_var, self.pan_r_entry)):
                try:
                    v = int(float(var.get()))
                    if not (0 <= v <= 100):
                        raise ValueError
                except ValueError:
                    self._mark(w, "error")
                    return tr('チャンネル{0}: パンは0〜100で指定してください').format(self.ch_id)
        if self.iv_var.get():
            try:
                mn = float(self.iv_min_var.get() or 0)
                mx = float(self.iv_max_var.get() or 0)
                if mn < 0 or mx < 0 or mx < mn:
                    raise ValueError
            except ValueError:
                self._mark(self.iv_min_entry, "error")
                return tr('チャンネル{0}: インターバルは「下限≤上限」かつ0以上で指定してください').format(self.ch_id)
        return None

    # ---- 内部 ----

    def _refit_items(self):
        """音声が0件のとき items_frame を潰す。

        空になった直後のフレームは pack_propagate では縮まず(最後のサイズを
        保持する)、音声のないチャンネルだけ枠が縦に伸びたままになる。
        件数に応じて propagate を切り替え、0件なら高さ1に固定して縮める。
        """
        if self.rows:
            self.items_frame.pack_propagate(True)
        else:
            self.items_frame.pack_propagate(False)
            self.items_frame.configure(height=1)

    def _set_rows(self, items_raw):
        """音声行を件数に合わせて設定する。ItemRow を使い回し、余りは破棄せず
        プールへ隠す(CTkウィジェット生成が重いため、切替のたびに作り直さない)。"""
        items_raw = list(items_raw)
        for i, raw in enumerate(items_raw):
            if i < len(self.rows):
                self.rows[i].reload(raw)          # 表示中の行を使い回す
            elif self._pool:
                row = self._pool.pop()            # プールの隠し行を復帰
                row.reload(raw)
                self.items_frame.pack_propagate(True)
                row.pack(fill="x", pady=2)
                self.rows.append(row)
            else:
                self._append_row(raw)             # 足りなければ新規生成
        while len(self.rows) > len(items_raw):    # 余りはプールへ退避(隠す)
            row = self.rows.pop()
            row.pack_forget()
            self._pool.append(row)

    def _append_row(self, raw_item):
        # 追加時は中身に追従して伸びるよう propagate を戻す
        self.items_frame.pack_propagate(True)
        row = ItemRow(self.items_frame, raw_item, self.base_dir,
                      self._delete_row, owner=self.owner, compact=self.compact)
        row.reorder_host = self   # =282: 並び替えD&Dの受け口
        row.pack(fill="x", pady=2)
        self.rows.append(row)

    # ---- 並び替え D&D(=282) ----
    # ItemRow の取っ手/ファイル名を縦ドラッグ → ポインタが隣の行の中心を
    # 越えたら即座に入れ替える(挿入線を別に描かず、行そのものが動く)。
    # rows の順序が JSON の items の順序になる(collect はそのまま)。

    def begin_row_drag(self, row):
        row.set_dragging(True)

    def drag_row_motion(self, row, y_root: int):
        if row not in self.rows:
            return
        others = [r for r in self.rows if r is not row]
        cur = self.rows.index(row)
        new = 0
        for r in others:
            try:
                mid = r.winfo_rooty() + r.winfo_height() / 2
            except Exception:
                continue
            if y_root > mid:
                new += 1
        if new != cur:
            self.move_row(cur, new)

    def end_row_drag(self, row):
        row.set_dragging(False)

    def move_row(self, src: int, dst: int):
        """rows[src] を dst の位置へ移し、表示(pack順)も追従させる。"""
        if src == dst or not (0 <= src < len(self.rows)) \
                or not (0 <= dst < len(self.rows)):
            return
        row = self.rows.pop(src)
        self.rows.insert(dst, row)
        row.pack_forget()
        if dst + 1 < len(self.rows):
            row.pack(fill="x", pady=2, before=self.rows[dst + 1])
        else:
            row.pack(fill="x", pady=2)

    def _delete_row(self, row):
        was_video = getattr(row, "is_video", False)
        self.rows.remove(row)
        row.destroy()
        self._refit_items()
        self._update_add_buttons()
        if was_video and not self.has_video():
            # 動画chでなくなったので他chの相関(動画ロック・device担当)を戻す
            self._notify_video_changed()

    def _add_items(self):
        paths = _pkg().filedialog.askopenfilenames(
            title=tr("音声ファイルを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("音声"), "*.wav *.mp3"), (tr("すべて"), "*.*")],
            parent=self,
        )
        if paths:
            _remember_dialog_dir(paths[0])
        for p in paths:
            rel = _safe_relpath(p, self.base_dir)   # 別ドライブでも落ちない
            self._append_row(rel)
        if paths:
            self._apply_row_correlations()   # 新規行へ相関(weight/pan)を反映
            self._update_add_buttons()
        if paths and not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
            # =40の相関制御(音声なし絞り込み)にも追従させる
            if self.owner is not None \
                    and hasattr(self.owner, "_on_channel_enabled"):
                self.owner._on_channel_enabled()

    def add_dropped_audio(self, paths):
        """D&Dで落とされた音声ファイル群をこのチャンネルへ登録する(=44)。

        対応拡張子(.wav/.mp3)以外・実在しないファイルは黙って無視(仕様=
        失敗時は画面上何も起こさない)。無効チャンネルは自動で有効化する
        (ユーザー確認済み)。パスの相対化・相関反映は「＋音声」と同じ。
        スクリプト専用ch・動画ch(混在禁止)への音声D&Dも黙って無視する。
        """
        if any(getattr(r, "is_script", False) or getattr(r, "is_video", False)
               for r in self.rows):
            return
        ok = [p for p in paths
              if os.path.isfile(p)
              and os.path.splitext(p)[1].lower() in (".wav", ".mp3")]
        if not ok:
            return
        for p in ok:
            self._append_row(_safe_relpath(p, self.base_dir))
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
            if self.owner is not None \
                    and hasattr(self.owner, "_on_channel_enabled"):
                self.owner._on_channel_enabled()

    def _add_scripts(self):
        """スクリプトのみアイテム(funscript/CSV)を追加する。

        スクリプト専用チャンネル(音声を持たず、デバイス担当分のスクリプトを
        独立したリズムで実行するチャンネル)を作るための追加ボタン。種別は
        ファイル名タグ(=44のD&Dと同じ規則: ufo/a10/vib、タグ無しfunscript=
        linear、タグ無しcsv=rotate_ufo)で初期判定し、行内で変更できる。
        """
        paths = _pkg().filedialog.askopenfilenames(
            title=tr("スクリプトファイルを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("funscript / CSV"), "*.funscript *.csv"),
                       ("funscript", "*.funscript"), ("CSV", "*.csv"),
                       (tr("すべて"), "*.*")],
            parent=self,
        )
        if paths:
            _remember_dialog_dir(paths[0])
        added = False
        for p in paths:
            ttype = _script_track_type(p)
            if ttype is None:
                continue
            rel = _safe_relpath(p, self.base_dir)
            self._append_row({"tracks": [{"type": ttype, "funscript": rel}]})
            added = True
        if not added:
            return
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        # =40の相関制御(音声なし絞り込み)と =63(スクリプトのみイベントの
        # 終了条件絞り込み)へ追従させる
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()

    def add_dropped_script(self, paths) -> bool:
        """D&Dで落とされた funscript/CSV をスクリプトのみアイテムとして登録(=65)。

        =46の既知の小制約「チャンネル枠へのfunscript D&D未対応」の解消。
        アイテム枠の**外**(チャンネル枠)へ落としたときの動作で、「＋スクリプト」
        と同じ規則(ファイル名タグで種別判定・1ファイル=1アイテム)で追加する
        (ユーザー決定 2026-07-27)。アイテム枠の上へ落としたときは従来どおり
        そのアイテムのトラックになる(ItemRow.bind_dropped_fs)。

        音声/動画のアイテムがあるチャンネル(=52の混在禁止)・実在しない
        ファイル・対象外拡張子は黙って無視する(=44の仕様=失敗時は画面上
        何も起こさない)。戻り値: 1件でも登録したか。
        """
        if not bool(getattr(self.owner, "device_enabled", True)):
            return False    # =252: デバイス連動OFFでは追加しない
        if any(r.audio_rel or getattr(r, "is_video", False)
               for r in self.rows):
            return False
        added = False
        for p in paths:
            if not os.path.isfile(p):
                continue
            ttype = _script_track_type(p)
            if ttype is None:
                continue
            rel = _safe_relpath(p, self.base_dir)
            self._append_row({"tracks": [{"type": ttype, "funscript": rel}]})
            added = True
        if not added:
            return False
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        # =40 / =63 の相関制御へ追従させる(「＋スクリプト」と同じ)
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()
        return True

    def _add_videos(self):
        """動画アイテムを追加する(=52)。複数選べば抽選/プレイリストになる。"""
        paths = _pkg().filedialog.askopenfilenames(
            title=tr("動画ファイルを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=list(VIDEO_FILETYPES),
            parent=self,
        )
        if not paths:
            return
        _remember_dialog_dir(paths[0])
        for p in paths:
            self._append_row({"video": _safe_relpath(p, self.base_dir)})
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        self._notify_video_changed()

    def add_dropped_video(self, paths) -> bool:
        """D&Dで落とされた動画ファイル群をこのチャンネルへ登録する(=52)。

        音声/スクリプトのアイテムがある(混在禁止)チャンネルへは登録しない。
        戻り値: 1件でも登録したか。
        """
        if any(r.audio_rel or getattr(r, "is_script", False)
               for r in self.rows):
            return False
        ok = [p for p in paths
              if os.path.isfile(p)
              and os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        if not ok:
            return False
        for p in ok:
            self._append_row({"video": _safe_relpath(p, self.base_dir)})
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        self._notify_video_changed()
        return True

    def _notify_video_changed(self):
        """動画アイテムの増減を owner へ通知する(相関制御の再計算)。"""
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()

    def has_video(self) -> bool:
        """動画アイテムを1つ以上持つか(=動画チャンネル)。"""
        return any(getattr(r, "is_video", False) for r in self.rows)

    def video_device_types(self) -> set:
        """このチャンネルの動画アイテムが担当するデバイス種別(種別一意性用)。"""
        out = set()
        for r in self.rows:
            if getattr(r, "is_video", False):
                out |= r.device_types()
        return out

    def is_script_only(self) -> bool:
        """スクリプト専用チャンネルか(アイテムが1つ以上あり全てスクリプト)。"""
        return bool(self.rows) and all(getattr(r, "is_script", False)
                                       for r in self.rows)

    def set_seek_radio_suppressed(self, flag: bool):
        """シーク追従ラジオの強制非表示(動画イベント=シークは動画に固定)。"""
        self._seek_suppressed = bool(flag)
        self._update_add_buttons()

    def _update_add_buttons(self):
        """音声/スクリプト追加ボタンの相互排他とシーク追従ラジオを更新する。

        チャンネルは「音声ch」「動画ch」「スクリプト専用ch」のどれか1つ
        (混在禁止)なので、いずれかのアイテムがある間は他の追加ボタンを
        無効化する。動画は同時に1本なので、他のチャンネルが動画chのときも
        「＋動画」を無効化する(_video_lock。owner が設定)。
        シークバー追従ラジオは動画ch(シークは動画に固定)と抑止時のみ隠す。
        **=63でスクリプト専用chも追従対象に選べる**ようになった(音声が無い
        場合はスクリプトの進行が経過時間になる)。
        """
        has_audio = any(r.audio_rel for r in self.rows)
        has_script = any(getattr(r, "is_script", False) for r in self.rows)
        has_video = self.has_video()
        other = has_script or has_video
        self.add_audio_btn.configure(
            state="disabled" if other else "normal")
        self.add_script_btn.configure(
            state="disabled" if (has_audio or has_video) else "normal")
        self.add_video_btn.configure(
            state="disabled" if (has_audio or has_script
                                 or getattr(self, "_video_lock", False))
            else "normal")
        if self.seek_radio is not None:
            if has_video or getattr(self, "_seek_suppressed", False):
                self.seek_radio.pack_forget()
            elif not self.seek_radio.winfo_ismapped():
                self.seek_radio.pack(side="left")

    def set_device_visible(self, on: bool):
        """デバイス連動(=252)のON/OFFをこのチャンネルへ反映する。

        OFF: 「＋スクリプト」ボタンを隠し、各アイテム行のトラックUI
        (自動/手動メニュー・トラック領域)を隠す。行そのものは残す
        (スクリプト専用アイテムも=AQ6)。裏のデータは保持したままなので、
        ONに戻せばそのまま復活する。
        """
        if on:
            if not self.add_script_btn.winfo_manager():
                if self.dnd_hint.winfo_manager():
                    self.add_script_btn.pack(side="left", padx=(6, 0),
                                             before=self.dnd_hint)
                else:
                    self.add_script_btn.pack(side="left", padx=(6, 0))
        else:
            self.add_script_btn.pack_forget()
        for row in self.rows:
            row._refresh_device_ui()
            row._refresh_review_btn()
            row._update_mode_ui()

    def set_video_lock(self, flag: bool):
        """他のチャンネルが動画chのとき「＋動画」を無効化する(=52)。

        「動画を置けるチャンネルは1つだけ」(ユーザー決定)を、保存時の
        検証エラーではなく編集時の相関制御で先回り防止する。
        """
        self._video_lock = bool(flag)
        self._update_add_buttons()

    def _update_end_ui(self):
        """終了条件に応じて値欄と単位ラベルを出し入れする。

        =67(ユーザー要望): **値が要らない終了条件(「1周で終了」「無限」)では
        入力欄と変数トグル「x」を欄ごと隠す**。以前は無効化した空欄と単位なしの
        ラベルが残っていて、何を入れる欄なのか分からなかった。
        値が要るときは単位(周/回/秒)を必ず添える。
        """
        choice = self.end_var.get()
        unit = {tr("N周で終了"): tr("周"),
                tr("N回再生で終了"): tr("回"),
                # 時間指定は秒のみ(分/秒の単位切替は廃止)
                tr("N秒で終了"): tr("秒")}.get(choice, "")
        is_range = (choice == tr("N秒で終了"))
        # 並び順([値][単位])が崩れないよう、いったん外してから戻す
        self.end_field.pack_forget()
        self.end_unit_label.pack_forget()
        self.end_unit_label.configure(text=unit)
        if is_range:
            # =99: 値はコンボ下の専用行([min]秒 〜 [max]秒)で編集する。
            # インラインの単一値欄は隠す(構築時コメント参照)。
            # min欄が空なら単一値欄の値を引き継ぐ(=99以前は1つの欄を
            # 全終了条件で共用していたため、N周/N回→N秒の切替で値が
            # 残っていた。その連続性を維持=切替直後の検証エラー防止)
            if (not self.end_rmin.use_var
                    and not self.end_rmin.get_text().strip()
                    and not self.end_field.use_var
                    and self.end_field.get_text().strip()):
                self.end_rmin.set(self.end_field.get_text())
            self.end_range_row.pack(fill="x", pady=(2, 0), after=self._row_b)
            state = "normal" if self.enabled_var.get() else "disabled"
            self.end_rmin.set_state(state)
            self.end_rmax.set_state(state)
            self.end_field.set_state("disabled")
        elif unit:
            # 逆方向(N秒→N周/N回)も同様に、単一値欄が空ならmin欄の値を継ぐ
            if (not self.end_field.use_var
                    and not self.end_field.get_text().strip()
                    and not self.end_rmin.use_var
                    and self.end_rmin.get_text().strip()):
                self.end_field.set(self.end_rmin.get_text())
            self.end_range_row.pack_forget()
            self.end_field.pack(side="left", padx=2)
            self.end_unit_label.pack(side="left")
            self.end_field.set_state("normal")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")
        else:
            self.end_range_row.pack_forget()
            self.end_field.set_state("disabled")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")

    def _update_enabled(self):
        state = "normal" if self.enabled_var.get() else "disabled"
        self.mode_menu.configure(state=state)
        self.end_menu.configure(state=state)
        self.detail_btn.configure(state=state)
        for w in (self.pan_check, self.pan_l_entry, self.pan_r_entry,
                  self.iv_check, self.iv_min_entry, self.iv_max_entry):
            w.configure(state=state)
        # 値欄の出し入れは終了条件だけで決まる(=67)。無効チャンネルでも
        # 「1周で終了」なら欄は出さない=見た目が揃う。
        self._update_end_ui()
        if not self.enabled_var.get():
            self.end_field.set_state("disabled")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")

    def _on_toggle(self):
        """ユーザーが有効化チェックを操作したとき(load中は呼ばれない)。"""
        self._update_enabled()
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()
