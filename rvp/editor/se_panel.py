"""シナリオ編集: イベント/ステート編集パネルのウィジェット構築(mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from ..scenario_map import CANVAS_BG
from ..i18n import tr

from .channel import ChannelSection
from .common import (BOX_BG, BOX_BORDER, CHANNEL_IDS, CTkOptionMenu,
    DEVICE_TYPES, MUTED, TEXT_HEAD, TEXT_MUTED, menu_device_types)
from .fields import CondListEditor, VarRefField
from .items import BgmItemRow
from .state_choice import StateChoiceEditor
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _ScenarioEditorPanelMixin:
    """ScenarioEditor の mixin(=301 分割)。イベント/ステート編集パネルのウィジェット構築"""

    @staticmethod
    def _align_label_column(labels):
        """先頭ラベルの幅を揃え、右側の入力欄の左端を縦一直線にする(=66)。

        ユーザー要望「コンボボックスやテキストボックスの縦の位置を揃えたい」。
        固定値を決め打ちにすると英語UI(=36)で足りなくなるので、**実際の
        要求幅の最大値**を採用する。`anchor="w"` を必ず添えること
        (CTkLabel は既定で中央寄せなので、幅だけ広げると文字が右へずれる)。
        """
        try:
            widths = [w.winfo_reqwidth() for w in labels if w is not None]
            if not widths:
                return
            col = max(widths)
            for w in labels:
                if w is not None:
                    w.configure(width=col, anchor="w")
        except Exception:
            pass

    def _build_panel_widgets(self):
        p = self.panel

        # ===== 上段: [イベント] [ステート] を丸角の灰色枠で2列に並べる =====
        top = ctk.CTkFrame(p, fg_color="transparent")
        top.pack(fill="x", pady=(2, 4))
        top.grid_columnconfigure(0, weight=1, uniform="pane")
        top.grid_columnconfigure(1, weight=1, uniform="pane")

        self.event_box = ctk.CTkFrame(top, corner_radius=10, fg_color=BOX_BG,
                                      border_width=1, border_color=BOX_BORDER)
        self.event_box.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.state_box = ctk.CTkFrame(top, corner_radius=10, fg_color=BOX_BG,
                                      border_width=1, border_color=BOX_BORDER)
        self.state_box.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # --- イベントボックスの中身 ---
        ev = ctk.CTkFrame(self.event_box, fg_color="transparent")
        ev.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        ctk.CTkLabel(ev, text=tr("■ イベント"),
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_HEAD, anchor="w").pack(fill="x")

        # =66: 先頭ラベルは幅を揃えて入力欄の左端を縦一直線にする
        # (最後に _align_label_column でまとめて幅を決める)
        ev_col_labels = []

        head = ctk.CTkFrame(ev, fg_color="transparent")
        head.pack(fill="x", pady=(4, 6))
        lbl = ctk.CTkLabel(head, text=tr("イベント名:"),
                           font=ctk.CTkFont(size=15, weight="bold"),
                           text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        # 見出しをインライン編集(Enter/フォーカス外れで確定→参照も自動追従)
        self.event_id_var = tk.StringVar(value="")
        self.event_id_entry = ctk.CTkEntry(
            head, textvariable=self.event_id_var, width=200, height=30,
            font=ctk.CTkFont(size=15, weight="bold"))
        self.event_id_entry.pack(side="left", padx=(6, 0))
        self.event_id_entry.bind("<Return>", lambda _e: self._commit_event_rename())
        self.event_id_entry.bind("<FocusOut>", lambda _e: self._commit_event_rename())
        # 参照キーとしての互換用(表示更新は _load_panel で行う)
        self.event_title_label = self.event_id_entry

        # 「開始イベントにする」はイベント名入力欄のすぐ右に置く
        self.start_var = tk.BooleanVar(value=False)
        self.start_check = ctk.CTkCheckBox(
            head, text=tr("開始イベントにする"), variable=self.start_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER)
        self.start_check.pack(side="left", padx=(10, 0))

        # イベント終了条件(イベント名の下・次のイベントの上)
        evend_row = ctk.CTkFrame(ev, fg_color="transparent")
        evend_row.pack(fill="x", pady=(4, 0))
        self.evend_row = evend_row
        lbl = ctk.CTkLabel(evend_row, text=tr("イベント終了条件:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.evend_var = tk.StringVar(value=self.EVEND_ALL)
        self.evend_menu = CTkOptionMenu(
            evend_row, variable=self.evend_var, width=210, height=26,
            # 選択肢は _load_panel で相関更新(通常=全/指定、ステート形式=委譲のみ)
            values=[self.EVEND_ALL, self.EVEND_CHANNEL],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_evend_ui(),
        )
        self.evend_menu.pack(side="left", padx=(6, 0))
        # 音声なしイベント(有効チャンネル0)のときだけ pack するヒント
        self.evend_noaudio_hint = ctk.CTkLabel(
            evend_row, text=tr("(音声なし=即座に次へ)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.evend_ch_label = ctk.CTkLabel(
            evend_row, text=tr("対象ch:"), font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED)
        self.evend_ch_var = tk.StringVar(value="C")
        self.evend_ch_menu = CTkOptionMenu(
            evend_row, variable=self.evend_ch_var, width=70, height=26,
            values=list(CHANNEL_IDS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._refresh_channel_infinite(),
        )
        # evend_ch_label / evend_ch_menu は「指定チャンネル」時のみ pack

        # 「合計時間が経過した時」の秒数欄。EVEND_DURATION 選択時のみ pack。
        # min秒〜max秒(0以上)。入場ごとに [min,max] の一様乱数を抽選し、その秒数を
        # 超えたら終了(ステート移行の channel_time と同じ範囲抽選)。max空欄=min。
        # 各欄は定数 or 数値変数参照(VarRefField)。
        self.evend_secs_min = VarRefField(
            evend_row, width=54, placeholder=tr("min"))
        # =67: 単位は min / max の**両方**に添える(ユーザー要望)
        self.evend_secs_min_label = ctk.CTkLabel(
            evend_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.evend_secs_tilde = ctk.CTkLabel(
            evend_row, text="〜", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.evend_secs_max = VarRefField(
            evend_row, width=54, placeholder=tr("max"))
        self.evend_secs_label = ctk.CTkLabel(
            evend_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        # =67: min/max の両方に単位を付けたぶん行が伸びるので、ヒントは
        # 短くする(長いままだとイベント枠からはみ出して切れる)
        self.evend_secs_hint = ctk.CTkLabel(
            evend_row, text=tr("(累積・範囲は抽選)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # 「変数条件で終了」時の判定式(AND)。EVEND_COND 選択時のみ pack
        self.evend_cond_frame = ctk.CTkFrame(ev, fg_color="transparent")
        ctk.CTkLabel(self.evend_cond_frame, text=tr("終了する判定式(すべて成立で終了):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.evend_cond = CondListEditor(self.evend_cond_frame)
        self.evend_cond.pack(fill="x", padx=(12, 0))

        # 次のイベント(チェック+重みで分岐)
        row1 = ctk.CTkFrame(ev, fg_color="transparent")
        row1.pack(fill="x", pady=(4, 0))
        lbl = ctk.CTkLabel(row1, text=tr("次のイベント:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.next_mode_var = tk.StringVar(value=self.NEXT_FIXED)
        self.next_mode_menu = CTkOptionMenu(
            row1, variable=self.next_mode_var, width=130, height=26,
            values=[self.NEXT_FIXED, self.NEXT_BRANCH, self.NEXT_CHOICE,
                    self.NEXT_NONE],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_next_mode(),
        )
        self.next_mode_menu.pack(side="left", padx=(6, 0))
        # choiceオブジェクトの編集対象外キーの保持
        self._choice_extra = {}
        # 幅の狭いイベント枠に収めるため、ヒントは次行で折り返し表示する
        self.next_hint_label = ctk.CTkLabel(
            ev, text=tr("(チェックなし=再生終了、複数チェック=重み付き抽選)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            justify="left", anchor="w", wraplength=440)
        self.next_hint_label.pack(fill="x", padx=(2, 0))

        # 変数操作(イベント開始時)。変数宣言があるときだけ表示する
        self.ev_ops_row = ctk.CTkFrame(ev, fg_color="transparent")
        lbl = ctk.CTkLabel(self.ev_ops_row, text=tr("変数操作:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self._ev_ops: list = []
        self._ev_end_ops: list = []
        self.ev_ops_btn = ctk.CTkButton(
            self.ev_ops_row, text="", width=140, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_event_ops)
        self.ev_ops_btn.pack(side="left", padx=6)
        ctk.CTkLabel(self.ev_ops_row,
                     text=tr("(◀◀での再実行でも発火します)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")

        # 分岐/選択肢/変数分岐/数値入力の切替コンテナ(=133で「分岐」へ改名)
        # =220: height=0 が要る。遷移方法が「なし」のときは中身(各inner)を
        # すべて pack_forget するので**空のCTkFrameになり、CTk既定の高さ
        # 200px(実測268px)ぶんの空白がイベント枠に居座っていた**
        # (ユーザー報告=新規作成・next:null の1イベントシナリオで、
        # チャンネルを見るのにスクロールが要る)。空のときは
        # `_collapse_if_empty` が height=1 まで潰す。
        next_area = ctk.CTkFrame(ev, fg_color="transparent", height=0)
        next_area.pack(fill="x")
        self.next_area = next_area
        self.random_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        self.random_inner.pack(fill="x")
        self.choice_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        self.cond_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        self.input_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        # =166「固定」: 遷移先を1つだけ選ぶ。JSONは "next": "eventB"(文字列)。
        # 「分岐」で1件だけチェックした場合と保存形式は同じなので、読み込みは
        # 常に「固定」として開く(ユーザー決定 2026-08-16)。
        self.fixed_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        fx_row = ctk.CTkFrame(self.fixed_inner, fg_color="transparent")
        fx_row.pack(fill="x", pady=(2, 4))
        lbl = ctk.CTkLabel(fx_row, text=tr("遷移先:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.next_fixed_var = tk.StringVar(value="")
        self.next_fixed_menu = CTkOptionMenu(
            fx_row, variable=self.next_fixed_var, width=200, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.next_fixed_menu.pack(side="left", padx=(6, 0))
        # 各inner は _update_next_mode で pack/pack_forget

        # =123 すごろく(advance): チェックONで「進む歩数」欄を表示。
        # next解決をN回繰り返し、途中のイベントは再生せず通過する。
        # 選択肢/数値入力の遷移では使えない(相関制御で行ごと非表示)。
        self.sugoroku_row = ctk.CTkFrame(ev, fg_color="transparent")
        self.sugoroku_row.pack(fill="x", pady=(2, 0))
        self.sugoroku_var = tk.BooleanVar(value=False)
        self.sugoroku_check = ctk.CTkCheckBox(
            self.sugoroku_row, text=tr("すごろく"), variable=self.sugoroku_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            command=self._update_sugoroku_ui)
        self.sugoroku_check.pack(side="left")
        self.sugoroku_steps_label = ctk.CTkLabel(
            self.sugoroku_row, text=tr("進む歩数:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.advance_field = VarRefField(self.sugoroku_row, width=54,
                                         placeholder="2")
        self.sugoroku_hint = ctk.CTkLabel(
            self.sugoroku_row,
            text=tr("(N歩先まで進み、通過するイベントは再生されません)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self._update_sugoroku_ui()

        # 遷移候補(イベントごとのチェック+重み)。_rebuild_next_ui で動的構築
        self.next_targets_frame = ctk.CTkFrame(self.random_inner,
                                               fg_color="transparent")
        self.next_targets_frame.pack(fill="x", pady=(2, 0))
        self.next_target_vars: dict[str, tk.BooleanVar] = {}
        # eid -> VarRefField(重み。定数 or 変数参照。0/負=出さない)
        self.next_weight_vars: dict = {}

        # 分岐オプション(未実行優先・全消化時の挙動)
        opt_row = ctk.CTkFrame(self.random_inner, fg_color="transparent")
        opt_row.pack(fill="x", pady=(2, 4))
        lbl = ctk.CTkLabel(opt_row, text=tr("分岐先の候補:"),
                           font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.next_visited_var = tk.StringVar(value=tr("毎回すべて候補"))
        CTkOptionMenu(
            opt_row, variable=self.next_visited_var, width=190, height=26,
            values=[tr("毎回すべて候補"), tr("未実行イベントのみ候補")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_next_ui(),
        ).pack(side="left", padx=6)
        # 「全候補が実行済みのとき:」は**2行目**に置く(=66・ユーザー要望)。
        # 1行に並べると幅690の枠では行き先メニューがはみ出して見えなかった。
        self.next_exh_row = ctk.CTkFrame(self.random_inner,
                                         fg_color="transparent")
        self.next_exh_label = ctk.CTkLabel(
            self.next_exh_row, text=tr("全候補が実行済みのとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.next_exhausted_var = tk.StringVar(
            value=tr("以後、毎回すべて候補"))
        # =133: 幅280=最長の「リセットして再び、未実行イベントのみ候補」対応
        self.next_exh_menu = CTkOptionMenu(
            self.next_exh_row, variable=self.next_exhausted_var,
            width=280, height=26,
            values=[tr("以後、毎回すべて候補"),
                    tr("リセットして再び、未実行イベントのみ候補"),
                    tr("指定イベントへ")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_next_ui(),
        )
        self.next_exh_to_var = tk.StringVar(value="")
        self.next_exh_to_menu = CTkOptionMenu(
            self.next_exh_row, variable=self.next_exh_to_var,
            width=140, height=26, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))

        # 全候補の重みが0以下(=出せる候補なし)のときの行き先(else)。
        # 「(終了)」=遷移なし=シナリオ終了。重みを変数にした時のフォールバック用。
        opt_row2 = ctk.CTkFrame(self.random_inner, fg_color="transparent")
        opt_row2.pack(fill="x", pady=(0, 4))
        self.next_else_row = opt_row2   # next_exh_row の pack 基準(=66)
        self.next_else_label = ctk.CTkLabel(
            opt_row2, text=tr("重みが全て0のとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.next_else_label.pack(side="left")
        self.NEXT_ELSE_END = tr("(終了)")
        self.next_else_var = tk.StringVar(value=self.NEXT_ELSE_END)
        self.next_else_menu = CTkOptionMenu(
            opt_row2, variable=self.next_else_var, width=160, height=26,
            values=[self.NEXT_ELSE_END],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.next_else_menu.pack(side="left", padx=6)
        ev_col_labels += [self.next_exh_label, self.next_else_label]
        # 入力欄の左端を縦一直線に揃える(=66・ユーザー要望)
        self._align_label_column(ev_col_labels)

        # ===== 選択肢エディタ =====
        self.choice_rows_frame = ctk.CTkFrame(self.choice_inner,
                                              fg_color="transparent")
        self.choice_rows_frame.pack(fill="x", pady=(2, 0))
        self.choice_rows: list[dict] = []   # {frame,label_var,to_var}

        add_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        add_row.pack(fill="x", pady=(2, 0))
        self.choice_add_btn = ctk.CTkButton(
            add_row, text=tr("＋ 選択肢を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_choice_row)
        self.choice_add_btn.pack(side="left")
        ctk.CTkLabel(add_row, text=tr("(最小1・最大9)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        tlim_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        tlim_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(tlim_row, text=tr("タイムリミット:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.choice_tlim_var = tk.StringVar(value=tr("無制限"))
        CTkOptionMenu(
            tlim_row, variable=self.choice_tlim_var, width=110, height=26,
            values=[tr("無制限"), tr("時間指定")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_choice_ui(),
        ).pack(side="left", padx=6)
        self.choice_tmin_var = tk.StringVar(value="0")
        self.choice_tmin_entry = ctk.CTkEntry(
            tlim_row, textvariable=self.choice_tmin_var, width=42, height=26,
            justify="right")
        self.choice_tmin_label = ctk.CTkLabel(
            tlim_row, text=tr("分"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.choice_tsec_var = tk.StringVar(value="30")
        self.choice_tsec_entry = ctk.CTkEntry(
            tlim_row, textvariable=self.choice_tsec_var, width=42, height=26,
            justify="right")
        self.choice_tsec_label = ctk.CTkLabel(
            tlim_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # デフォルト遷移先(タイムアウト時・▶▶スキップ時の共通の行き先)
        dflt_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        dflt_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(dflt_row, text=tr("デフォルト遷移先:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.choice_dflt_var = tk.StringVar(value=tr("先頭の選択肢へ"))
        CTkOptionMenu(
            dflt_row, variable=self.choice_dflt_var, width=210, height=26,
            values=[tr("先頭の選択肢へ"), tr("選択肢から等確率で抽選"),
                    tr("指定イベントへ")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_choice_ui(),
        ).pack(side="left", padx=6)
        self.choice_dflt_to_var = tk.StringVar(value="")
        self.choice_dflt_to_menu = CTkOptionMenu(
            dflt_row, variable=self.choice_dflt_to_var, width=140, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        ctk.CTkLabel(dflt_row, text=tr("(タイムアウト時・▶▶スキップ時の行き先)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="right")

        # =274: 選択必須(▶▶で飛ばさない)。ONのとき "skip": "stay" で保存。
        # タイムリミット併用時のタイムアウト遷移は従来どおり進む(仕様)。
        stay_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        stay_row.pack(fill="x", pady=(4, 0))
        self.choice_stay_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            stay_row, text=tr("▶▶で飛ばさない(選択されるまで待機)"),
            variable=self.choice_stay_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
        ).pack(side="left")
        ctk.CTkLabel(stay_row,
                     text=tr("(音声中の▶▶は音声だけ打ち切る。タイムアウトは進む)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        show_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        show_row.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(show_row, text=tr("表示タイミング:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.choice_show_var = tk.StringVar(value=tr("イベント終了条件の達成時"))
        # =168: 終了条件が「無限」のときは候補を絞るのでメニューを保持する
        self.choice_show_menu = CTkOptionMenu(
            show_row, variable=self.choice_show_var, width=210, height=26,
            values=list(self.SHOW_TIMES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_choice_ui(),
        )
        self.choice_show_menu.pack(side="left", padx=6)
        self.choice_smin_var = tk.StringVar(value="0")
        self.choice_smin_entry = ctk.CTkEntry(
            show_row, textvariable=self.choice_smin_var, width=42, height=26,
            justify="right")
        self.choice_smin_label = ctk.CTkLabel(
            show_row, text=tr("分"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.choice_ssec_var = tk.StringVar(value="30")
        self.choice_ssec_entry = ctk.CTkEntry(
            show_row, textvariable=self.choice_ssec_var, width=42, height=26,
            justify="right")
        self.choice_ssec_label = ctk.CTkLabel(
            show_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # 選択肢の変数連携(各行のopsボタンは行内、on_timeoutはここ)
        self._choice_timeout_ops: list = []
        self.choice_toops_btn = ctk.CTkButton(
            tlim_row, text="", width=150, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_choice_timeout_ops)

        # ===== 変数分岐(cond)エディタ =====
        ctk.CTkLabel(self.cond_inner,
                     text=tr("上から順に評価し、最初に成立した行へ遷移します。行内の条件はAND。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, anchor="w"
                     ).pack(fill="x", pady=(2, 0))
        self.cond_rows_frame = ctk.CTkFrame(self.cond_inner,
                                            fg_color="transparent")
        self.cond_rows_frame.pack(fill="x", pady=(2, 0))
        self.cond_rows: list[dict] = []
        cond_add_row = ctk.CTkFrame(self.cond_inner, fg_color="transparent")
        cond_add_row.pack(fill="x", pady=(2, 0))
        ctk.CTkButton(
            cond_add_row, text=tr("＋ 条件行を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_cond_row()).pack(side="left")
        else_row = ctk.CTkFrame(self.cond_inner, fg_color="transparent")
        else_row.pack(fill="x", pady=(4, 4))
        # =168: 終了条件が「無限」のときは else が成立しえないので行ごと隠す
        self.cond_else_row = else_row
        ctk.CTkLabel(else_row, text=tr("どの行も成立しないとき(else):"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.cond_else_var = tk.StringVar(value=tr("再生終了"))
        self.cond_else_menu = CTkOptionMenu(
            else_row, variable=self.cond_else_var, width=150, height=26,
            values=[tr("再生終了")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.cond_else_menu.pack(side="left", padx=6)

        # ===== 数値入力(input)エディタ =====
        in_row1 = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_row1.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(in_row1, text=tr("入力先の変数:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.input_var_var = tk.StringVar(value="")
        self.input_var_menu = CTkOptionMenu(
            in_row1, variable=self.input_var_var, width=120, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.input_var_menu.pack(side="left", padx=6)
        ctk.CTkLabel(in_row1, text=tr("見出し:"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", padx=(10, 0))
        self.input_label_var = tk.StringVar(value="")
        ctk.CTkEntry(in_row1, textvariable=self.input_label_var, width=220,
                     height=26, placeholder_text=tr("省略時「数値を入力してください」")
                     ).pack(side="left", padx=6)
        ctk.CTkLabel(in_row1, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        self.input_to_var = tk.StringVar(value="")
        self.input_to_menu = CTkOptionMenu(
            in_row1, variable=self.input_to_var, width=140, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.input_to_menu.pack(side="left", padx=6)
        in_row2 = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_row2.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(in_row2, text=tr("入力範囲:"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        self.input_min_var = tk.StringVar(value="")
        self.input_min_entry = ctk.CTkEntry(
            in_row2, textvariable=self.input_min_var, width=60,
            height=26, justify="right", placeholder_text="min")
        self.input_min_entry.pack(side="left", padx=(6, 2))
        ctk.CTkLabel(in_row2, text="〜", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        self.input_max_var = tk.StringVar(value="")
        self.input_max_entry = ctk.CTkEntry(
            in_row2, textvariable=self.input_max_var, width=60,
            height=26, justify="right", placeholder_text="max")
        self.input_max_entry.pack(side="left", padx=2)
        ctk.CTkLabel(in_row2, text=tr("(空欄=変数宣言の最小/最大。決定した値は変数へセットされ即遷移)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)
        # =288: 入力必須(▶▶で飛ばさない)。ONのとき "skip": "stay" で保存。
        # 新規の数値入力では ON が既定(入力が前提のことが多い=ユーザー決定)。
        # JSONに skip が無い既存のものは OFF(=従来どおり飛ばせる)で開く。
        in_stay = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_stay.pack(fill="x", pady=(4, 0))
        self.input_stay_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            in_stay, text=tr("▶▶で飛ばさない(入力されるまで待機)"),
            variable=self.input_stay_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
        ).pack(side="left")
        ctk.CTkLabel(in_stay,
                     text=tr("(音声中の▶▶は音声だけ打ち切る)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)
        in_row3 = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_row3.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(in_row3, text=tr("表示タイミング:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.input_show_var = tk.StringVar(value=tr("イベント終了条件の達成時"))
        # =168: 終了条件が「無限」のときは候補を絞るのでメニューを保持する
        self.input_show_menu = CTkOptionMenu(
            in_row3, variable=self.input_show_var, width=210, height=26,
            values=list(self.SHOW_TIMES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_input_ui(),
        )
        self.input_show_menu.pack(side="left", padx=6)
        self.input_smin_var = tk.StringVar(value="0")
        self.input_smin_entry = ctk.CTkEntry(
            in_row3, textvariable=self.input_smin_var, width=42, height=26,
            justify="right")
        self.input_smin_label = ctk.CTkLabel(
            in_row3, text=tr("分"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.input_ssec_var = tk.StringVar(value="30")
        self.input_ssec_entry = ctk.CTkEntry(
            in_row3, textvariable=self.input_ssec_var, width=42, height=26,
            justify="right")
        self.input_ssec_label = ctk.CTkLabel(
            in_row3, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self._input_extra = {}       # inputオブジェクトの編集対象外キー保持
        self._input_next_extra = {}  # next直下の編集対象外キー保持

        # ===== ステートボックスの中身 =====
        st = ctk.CTkFrame(self.state_box, fg_color="transparent")
        st.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        st_title_row = ctk.CTkFrame(st, fg_color="transparent")
        st_title_row.pack(fill="x")
        ctk.CTkLabel(st_title_row, text=tr("■ ステート"),
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_HEAD, anchor="w").pack(side="left")
        # 通常イベント⇔ステート形式の変換ボタン(ステートボックスの見出し右)
        self.states_toggle_btn = ctk.CTkButton(
            st_title_row, text=tr("ステート形式に変換"), width=150, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._toggle_states_mode)
        self.states_toggle_btn.pack(side="right")
        # 通常イベント時の案内(ステート未使用のとき states_inner の代わりに表示)
        self.states_hint = ctk.CTkLabel(
            st, text=tr("このイベントはステートを使っていません。\n"
                        "「ステート形式に変換」で複数ステートの\n"
                        "編集ができます。"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, justify="left",
            anchor="w")

        # ステート領域(ステート形式のイベント選択時のみ内容を表示)
        self.states_area = st
        self.states_inner = ctk.CTkFrame(self.states_area, fg_color="transparent")
        # states_inner / states_hint は _show_states_ui で pack/pack_forget する

        # イベント終了条件(ステート形式で必須)
        ev_end_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        ev_end_row.pack(fill="x", pady=(2, 4))
        st_col_labels = []
        lbl = ctk.CTkLabel(ev_end_row, text=tr("イベント終了条件:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        st_col_labels.append(lbl)
        self.ev_end_var = tk.StringVar(value=self.EV_END_CHOICES[0])
        self.ev_end_menu = CTkOptionMenu(
            ev_end_row, variable=self.ev_end_var, width=160, height=26,
            values=list(self.EV_END_CHOICES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_ev_end_menu_change(),
        )
        self.ev_end_menu.pack(side="left", padx=6)
        self.ev_end_field = VarRefField(ev_end_row, width=48,
                                        placeholder=tr("値"),
                                        on_change=lambda: self._update_ev_end_ui())
        self.ev_end_field.pack(side="left", padx=2)
        self.ev_end_unit_label = ctk.CTkLabel(
            ev_end_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.ev_end_unit_label.pack(side="left")
        # =99: 「合計N秒で次へ」の範囲抽選(min〜max)。duration 選択時のみ
        # [min]秒 〜 [max]秒 の2欄になる(通常イベントの evend と同じ書式)
        self.ev_end_tilde_label = ctk.CTkLabel(
            ev_end_row, text="〜", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.ev_end_field2 = VarRefField(ev_end_row, width=48,
                                         placeholder=tr("max"),
                                         on_change=lambda: self._update_ev_end_ui())
        self.ev_end_unit2_label = ctk.CTkLabel(
            ev_end_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.ev_end_hint_label = ctk.CTkLabel(
            ev_end_row, text=tr("(累積)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.ev_end_hint_label.pack(side="left", padx=6)
        self.ev_end_row = ev_end_row

        # 「変数条件で次へ」時の判定式(AND)。EV_END_COND 選択時のみ pack
        self.ev_end_cond_frame = ctk.CTkFrame(
            self.states_inner, fg_color="transparent")
        ctk.CTkLabel(self.ev_end_cond_frame,
                     text=tr("終了する判定式(すべて成立で終了):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.ev_end_cond = CondListEditor(self.ev_end_cond_frame)
        self.ev_end_cond.pack(fill="x", padx=(12, 0))

        # 「指定ステートが終了したら次へ」時のステート選択(チェックで複数指定)。
        # EV_END_STATES 選択時のみ pack。チェックしたステートのどれかが終了したら終了。
        self.ev_end_states_frame = ctk.CTkFrame(
            self.states_inner, fg_color="transparent")
        ctk.CTkLabel(self.ev_end_states_frame,
                     text=tr("終了とみなすステート(複数可):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(
                         side="left", padx=(12, 0))
        self.ev_end_states_inner = ctk.CTkFrame(
            self.ev_end_states_frame, fg_color="transparent")
        self.ev_end_states_inner.pack(side="left", padx=6)
        self.ev_end_state_vars: dict = {}
        self.ev_end_state_checks: list = []

        # ステートマシン図
        st_canvas_wrap = ctk.CTkFrame(self.states_inner, corner_radius=10,
                                      fg_color=("gray82", "gray24"))
        st_canvas_wrap.pack(fill="x", pady=(2, 2))   # =100②: 4→2
        st_head = ctk.CTkFrame(st_canvas_wrap, fg_color="transparent")
        st_head.pack(fill="x", padx=8, pady=(4, 0))   # =100②: 6→4
        ctk.CTkLabel(st_head, text=tr("ステート図(○で選択)"),
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_MUTED
                     ).pack(side="left")
        ctk.CTkButton(st_head, text=tr("＋追加"), width=64, height=26,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._add_state).pack(side="right", padx=2)
        ctk.CTkButton(st_head, text=tr("削除"), width=56, height=26,
                      fg_color="transparent", border_width=1,
                      border_color="#e05a5a", text_color="#e05a5a",
                      hover_color=("gray85", "gray25"),
                      command=self._delete_state).pack(side="right", padx=2)
        ctk.CTkButton(st_head, text=tr("コピー"), width=56, height=26,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._copy_state).pack(side="right", padx=2)

        # =100②: 高さ130→118(図の実内容=○+開始ラベル+下向きの弧は
        # 約115pxに収まる実測。詰めた分でパネル下端の行の欠けを防ぐ)
        self.state_canvas = tk.Canvas(st_canvas_wrap, height=118, bg=CANVAS_BG,
                                      highlightthickness=0)
        self.state_canvas.pack(fill="x", padx=8, pady=(4, 2))   # =100②: 6→4
        # =108: ステートが増えると図が右へ伸びて画面外の○が選べなくなる
        # (ユーザー報告)。イベント遷移図と同じ CTkScrollbar を横に付ける。
        # ステート図は1行に並ぶだけなので横だけでよい。**はみ出している
        # ときだけ表示**して、少ないときは従来どおりの高さを保つ。
        self.state_hbar = ctk.CTkScrollbar(
            st_canvas_wrap, orientation="horizontal", height=12,
            command=self.state_canvas.xview)
        self.state_canvas.configure(xscrollcommand=self._on_state_xscroll)
        # =276: ホイールはステート図を横に動かさず、編集パネル全体の縦スクロール
        # に任せる(ユーザー依頼。=108で付けた「ホイール=横」は、○が2〜3個で
        # はみ出していなくても図が右へずれて見えたため廃止)。パネル
        # (CTkScrollableFrame)は bind_all で子孫上のホイールを拾うので、
        # ここで何も bind しなければ縦スクロールになる。はみ出しているときの
        # 横移動は横スクロールバー(=108)と Shift+ホイールで行う。
        self.state_canvas.bind("<Shift-MouseWheel>", self._on_state_shift_wheel)
        self.state_canvas.bind(   # Linux(X11)のホイールは Button-4/5
            "<Shift-Button-4>",
            lambda e: self._on_state_shift_wheel(e, step=-1))
        self.state_canvas.bind(
            "<Shift-Button-5>",
            lambda e: self._on_state_shift_wheel(e, step=1))
        self.trans_summary_label = ctk.CTkLabel(
            st_canvas_wrap, text="", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED, justify="left", anchor="w")
        self.trans_summary_label.pack(fill="x", padx=10, pady=(0, 4))   # =100②

        # 選択ステートの見出し(インライン編集で名前を変更できる)
        st_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        st_row.pack(fill="x", pady=(2, 0))   # =100②: 4→2
        lbl = ctk.CTkLabel(st_row, text=tr("ステート名:"),
                           font=ctk.CTkFont(size=14, weight="bold"),
                           text_color=_clr.ACCENT_TEXT)
        lbl.pack(side="left")
        st_col_labels.append(lbl)
        self.state_id_var = tk.StringVar(value="")
        self.state_id_entry = ctk.CTkEntry(
            st_row, textvariable=self.state_id_var, width=150, height=28,
            font=ctk.CTkFont(size=14, weight="bold"))
        self.state_id_entry.pack(side="left", padx=(6, 0))
        self.state_id_entry.bind("<Return>", lambda _e: self._commit_state_rename())
        self.state_id_entry.bind("<FocusOut>", lambda _e: self._commit_state_rename())
        self.state_title_label = self.state_id_entry
        self.state_start_var = tk.BooleanVar(value=False)
        self.state_start_check = ctk.CTkCheckBox(
            st_row, text=tr("開始ステートにする"), variable=self.state_start_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER)
        self.state_start_check.pack(side="left", padx=16)
        # ステート開始時の変数操作(変数宣言があるときだけ表示)
        self._st_ops: list = []
        self._st_end_ops: list = []
        self.state_ops_btn = ctk.CTkButton(
            st_row, text="", width=150, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_state_ops)

        # 移行条件
        trans_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        trans_row.pack(fill="x", pady=(2, 0))   # =100②: 4→2
        lbl = ctk.CTkLabel(trans_row, text=tr("ステート移行:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        st_col_labels.append(lbl)
        self._align_label_column(st_col_labels)
        self.trans_type_var = tk.StringVar(value=tr("ステート移行なし"))
        self.trans_type_menu = CTkOptionMenu(
            trans_row, variable=self.trans_type_var, width=170, height=26,
            values=list(self.TRANS_CHOICES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_trans_type_menu_change(),
        )
        self.trans_type_menu.pack(side="left", padx=6)
        self.trans_row = trans_row
        self.trans_ch_label = ctk.CTkLabel(
            trans_row, text=tr("対象ch:"), font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED)
        self.trans_ch_label.pack(side="left", padx=(8, 2))
        self.trans_ch_var = tk.StringVar(value="C")
        self.trans_ch_menu = CTkOptionMenu(
            trans_row, variable=self.trans_ch_var, width=60, height=26,
            values=list(CHANNEL_IDS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.trans_ch_menu.pack(side="left")
        self.trans_min_field = VarRefField(trans_row, width=54,
                                           placeholder="min")
        self.trans_min_field.pack(side="left", padx=(10, 2))
        # =67: 単位は min / max の両方に添える(ユーザー要望)
        self.trans_unit_min_label = ctk.CTkLabel(
            trans_row, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.trans_unit_min_label.pack(side="left")
        self.trans_tilde_label = ctk.CTkLabel(
            trans_row, text="〜", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_tilde_label.pack(side="left", padx=(4, 2))
        self.trans_max_field = VarRefField(trans_row, width=54,
                                           placeholder="max")
        self.trans_max_field.pack(side="left", padx=2)
        self.trans_unit_label = ctk.CTkLabel(
            trans_row, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.trans_unit_label.pack(side="left")
        # 音声なしステート(有効チャンネル0)のときだけ pack するヒント
        self.trans_noaudio_hint = ctk.CTkLabel(
            trans_row, text=tr("(音声なし=即座に通過)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # 「判定式でステート移行」時の判定式(AND)。TRANS_COND 選択時のみ pack
        self.trans_cond_frame = ctk.CTkFrame(
            self.states_inner, fg_color="transparent")
        ctk.CTkLabel(self.trans_cond_frame,
                     text=tr("移行する判定式(すべて成立で移行):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.trans_cond = CondListEditor(self.trans_cond_frame)
        self.trans_cond.pack(fill="x", padx=(12, 0))
        # =275: 「選択肢でステート移行」の編集ブロック。TRANS_CHOICE 選択時のみ
        # trans_row の直後へ pack し、移行先行(to_row)・分岐オプションは隠す
        self.state_choice = StateChoiceEditor(self.states_inner, self)

        # 移行先(チェックで複数=抽選。=73で各候補に重み欄=定数 or 変数参照)
        to_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        to_row.pack(fill="x", pady=(2, 2))   # =100②: 4→2
        self.trans_to_row = to_row           # =125: cond箱のpack位置基準
        self.trans_to_label = ctk.CTkLabel(
            to_row, text=tr("ステート移行先(複数チェックで抽選):"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_to_label.pack(side="left")
        # =125: 移行先の決め方(分岐 / 変数分岐)。「変数分岐」は
        # 変数宣言があるときだけ選択肢に出す(イベントの遷移方法と同じ相関制御)
        # =167: 「固定」(移行先が1つ)を追加。JSONは "to": "S2"(文字列)で、
        # 「分岐」を1つだけチェックした場合と同じ形。読み込みは
        # 常に「固定」として開く(イベントの遷移方法=166と同じ扱い)。
        # =169: 名称を「チェックで抽選」→「分岐」へ(ユーザー決定)。
        # イベントの遷移方法の「分岐」と同じ概念なので言葉を揃える。
        self.TRANS_TOMODE_FIXED = tr("固定")
        self.TRANS_TOMODE_PICK = tr("分岐")
        self.TRANS_TOMODE_COND = tr("変数分岐")
        self.trans_tomode_var = tk.StringVar(value=self.TRANS_TOMODE_FIXED)
        self.trans_tomode_menu = CTkOptionMenu(
            to_row, variable=self.trans_tomode_var, width=130, height=26,
            values=[self.TRANS_TOMODE_FIXED, self.TRANS_TOMODE_PICK],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_trans_tomode_ui())
        self.trans_tomode_menu.pack(side="left", padx=(6, 0))
        # =167: 「固定」の移行先メニュー(同じ行に置く)
        self.trans_fixed_var = tk.StringVar(value="")
        self.trans_fixed_menu = CTkOptionMenu(
            to_row, variable=self.trans_fixed_var, width=160, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.trans_targets_frame = ctk.CTkFrame(to_row, fg_color="transparent")
        self.trans_targets_frame.pack(side="left", padx=8)
        self.trans_target_vars: dict[str, tk.BooleanVar] = {}
        self.trans_target_checks: list[ctk.CTkCheckBox] = []
        self.trans_weight_fields: dict[str, VarRefField] = {}

        # =77: 分岐オプション(未実行ステートのみ候補・全消化時の挙動)。
        # =133: 文言を「分岐先の候補:」「〜のみ候補」へ(仕様どおり=除外)。
        # =26のイベント版(next_visited_var/next_exh_row)と同じ構成・同じ
        # 2行方式(「全候補が実行済みのとき:」は行ごと出し入れする)。
        tv_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        tv_row.pack(fill="x", pady=(0, 2))   # =100②: 4→2
        self.trans_visited_row = tv_row
        self.trans_visited_label = ctk.CTkLabel(
            tv_row, text=tr("分岐先の候補:"), font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED)
        self.trans_visited_label.pack(side="left")
        self.trans_visited_var = tk.StringVar(value=tr("毎回すべて候補"))
        self.trans_visited_menu = CTkOptionMenu(
            tv_row, variable=self.trans_visited_var, width=190, height=26,
            values=[tr("毎回すべて候補"), tr("未実行ステートのみ候補")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_trans_visited_ui(),
        )
        self.trans_visited_menu.pack(side="left", padx=6)
        self.trans_exh_row = ctk.CTkFrame(self.states_inner,
                                          fg_color="transparent")
        self.trans_exh_label = ctk.CTkLabel(
            self.trans_exh_row, text=tr("全候補が実行済みのとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_exhausted_var = tk.StringVar(
            value=tr("以後、毎回すべて候補"))
        self.trans_exh_menu = CTkOptionMenu(
            self.trans_exh_row, variable=self.trans_exhausted_var,
            width=280, height=26,
            values=[tr("以後、毎回すべて候補"),
                    tr("リセットして再び、未実行ステートのみ候補"),
                    tr("指定ステートへ")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_trans_visited_ui(),
        )
        self.trans_exh_to_var = tk.StringVar(value="")
        self.trans_exh_to_menu = CTkOptionMenu(
            self.trans_exh_row, variable=self.trans_exh_to_var,
            width=140, height=26, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))

        # =73: 全候補の重みが0以下(=出せる候補なし)のときの移行先(else)。
        # 「(イベント終了)」=指定なし。重みを変数にした時のフォールバック用
        # (=26のイベント版ランダム分岐と同じ作法)。
        else_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        else_row.pack(fill="x", pady=(0, 2))   # =100②: 4→2
        self.trans_else_row = else_row
        self.trans_else_label = ctk.CTkLabel(
            else_row, text=tr("重みが全て0のとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_else_label.pack(side="left")
        self.TRANS_ELSE_END = tr("(イベント終了)")
        self.trans_else_var = tk.StringVar(value=self.TRANS_ELSE_END)
        self.trans_else_menu = CTkOptionMenu(
            else_row, variable=self.trans_else_var, width=160, height=26,
            values=[self.TRANS_ELSE_END],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.trans_else_menu.pack(side="left", padx=6)

        # =125: 移行先の判定式(cond)エディタ。「変数分岐」選択時のみ
        # to_row の直後へ pack する(イベントの変数分岐と同じ見た目)。
        self.strans_cond_box = ctk.CTkFrame(self.states_inner,
                                            fg_color="transparent")
        ctk.CTkLabel(self.strans_cond_box,
                     text=tr("上から順に評価し、最初に成立した行へ移行します。行内の条件はAND。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x", pady=(2, 0))
        self.strans_rows_frame = ctk.CTkFrame(self.strans_cond_box,
                                              fg_color="transparent")
        self.strans_rows_frame.pack(fill="x", pady=(2, 0))
        self.strans_rows: list[dict] = []
        strans_add = ctk.CTkFrame(self.strans_cond_box,
                                  fg_color="transparent")
        strans_add.pack(fill="x", pady=(2, 0))
        ctk.CTkButton(
            strans_add, text=tr("＋ 条件行を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_strans_row()).pack(side="left")
        strans_else = ctk.CTkFrame(self.strans_cond_box,
                                   fg_color="transparent")
        strans_else.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(strans_else, text=tr("どの行も成立しないとき(else):"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.STRANS_ELSE_NONE = tr("(移行しない)")
        self.strans_else_var = tk.StringVar(value=self.STRANS_ELSE_NONE)
        self.strans_else_menu = CTkOptionMenu(
            strans_else, variable=self.strans_else_var, width=150, height=26,
            values=[self.STRANS_ELSE_NONE],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.strans_else_menu.pack(side="left", padx=6)

        # ===== ここまでステート領域 =====

        # device担当
        # =107: 「見落としやすい」というユーザー指摘への対応。透明フレームの
        # 1行だったものを、イベント/ステートボックスと同じ丸角+枠線の
        # 独立ブロックにして、上のステート領域・下のチャンネル3列から
        # 視覚的に切り離す。中身(ラベル+種別ごとのメニュー)は従来どおり。
        self.device_box = ctk.CTkFrame(p, corner_radius=8, fg_color=BOX_BG,
                                       border_width=1, border_color=BOX_BORDER)
        self.device_box.pack(fill="x", pady=(6, 4))
        row2 = ctk.CTkFrame(self.device_box, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(row2, text=tr("デバイス担当:"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_HEAD).pack(side="left")
        self.device_vars: dict[str, tk.StringVar] = {}
        self.device_menus: dict[str, ctk.CTkOptionMenu] = {}
        # =88: TWIST常時表示化により show_types は常に全種別
        # (=79のサブ機能スイッチによる出し入れは廃止)。
        show_types = menu_device_types()
        for ttype in DEVICE_TYPES:
            shown = ttype in show_types
            lbl = ctk.CTkLabel(row2, text=f"{ttype}→", font=ctk.CTkFont(size=12),
                               text_color=TEXT_MUTED)
            if shown:
                lbl.pack(side="left", padx=(10, 2))
            var = tk.StringVar(value=tr("なし"))
            menu = CTkOptionMenu(
                row2, variable=var, width=70, height=26,
                values=[tr("なし"), "L", "C", "R"],
                fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            )
            if shown:
                menu.pack(side="left")
            self.device_vars[ttype] = var
            self.device_menus[ttype] = menu

        # 動画(外部mpv再生)は =52 でチャンネルの中身になったため、
        # ここにあった専用の VideoRow は廃止した(ユーザー要望=動画対応
        # 以前と同じレイアウトへ戻す)。動画は各チャンネルの
        # 「＋動画」から動画アイテムとして並べる。

        # ===== 下段: チャンネル L/C/R を丸角の灰色枠で3列に並べる =====
        chan_grid = ctk.CTkFrame(p, fg_color="transparent")
        chan_grid.pack(fill="x", pady=(2, 4))
        # =252: デバイス連動OFF→ONで device_box を戻すときの pack 先アンカー
        self.chan_grid = chan_grid
        for i in range(3):
            chan_grid.grid_columnconfigure(i, weight=1, uniform="chan")
        # シークバー追従チャンネル(L/C/Rで1つ。各セクションのラジオで共有)
        self.seek_var = tk.StringVar(value="C")
        self.channel_sections: dict[str, ChannelSection] = {}
        for i, ch_id in enumerate(CHANNEL_IDS):
            sec = ChannelSection(chan_grid, ch_id, self.base_dir, owner=self,
                                 compact=True)
            # sticky=new: 横は3等分で伸ばし、縦は各チャンネルの中身の高さのまま
            # 上端揃え(音声数が違ってもボタンが下端に落ちない)
            sec.grid(row=0, column=i, sticky="new",
                     padx=(0, 6) if i < 2 else 0, pady=5)
            self.channel_sections[ch_id] = sec

        # ===== BGM(=256): ノード(イベント/ステート)の一番下のブロック =====
        # bgm_enabled ONのときだけ _apply_bgm_enabled が chan_grid の直後へ
        # packする。3択(引き継ぐ/指定/オフ)+「指定」時のみアイテム一覧・
        # ＋BGM・順序・既定パンを展開する。
        self.bgm_box = ctk.CTkFrame(p, corner_radius=8, fg_color=BOX_BG,
                                    border_width=1, border_color=BOX_BORDER)
        brow = ctk.CTkFrame(self.bgm_box, fg_color="transparent")
        brow.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(brow, text=tr("BGM:"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_HEAD).pack(side="left")
        self.bgm_mode_var = tk.StringVar(value=self.BGM_INHERIT)
        self.bgm_mode_menu = CTkOptionMenu(
            brow, variable=self.bgm_mode_var, width=190, height=26,
            values=[self.BGM_INHERIT, self.BGM_SET, self.BGM_OFF],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_bgm_ui(),
        )
        self.bgm_mode_menu.pack(side="left", padx=(6, 0))
        self.bgm_hint = ctk.CTkLabel(
            brow, text=tr("(BGMはイベント/ステートをまたいで再生され続けます)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.bgm_hint.pack(side="left", padx=8)

        self.bgm_body = ctk.CTkFrame(self.bgm_box, fg_color="transparent")
        # 空のCTkFrameは既定高さ(200px)を保持して間延びするため、height=0で
        # 生成し、行数に応じて _bgm_refit_items が propagate を切り替える
        # (ChannelSection.items_frame の =既知対処と同じ)。
        self.bgm_items_frame = ctk.CTkFrame(self.bgm_body,
                                            fg_color="transparent", height=0)
        self.bgm_items_frame.pack(fill="x", padx=6)
        self._bgm_rows: list[BgmItemRow] = []
        bctl = ctk.CTkFrame(self.bgm_body, fg_color="transparent")
        bctl.pack(fill="x", padx=6, pady=(4, 4))
        self.bgm_add_btn = ctk.CTkButton(
            bctl, text=tr("＋BGM"), width=84, height=28,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._bgm_add_items)
        self.bgm_add_btn.pack(side="left")
        self.bgm_order_var = tk.StringVar(value=tr("順番に再生"))
        self.bgm_order_menu = CTkOptionMenu(
            bctl, variable=self.bgm_order_var, width=150, height=26,
            values=[tr("順番に再生"), tr("ランダム再生")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.bgm_order_menu.pack(side="left", padx=8)
        self.bgm_pan_var = tk.BooleanVar(value=False)
        self.bgm_pan_check = ctk.CTkCheckBox(
            bctl, text=tr("パン上書き(L/R %)"), variable=self.bgm_pan_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_bgm_pan_toggle)
        self.bgm_pan_check.pack(side="left", padx=(10, 0))
        self.bgm_pan_box = ctk.CTkFrame(bctl, fg_color="transparent")
        self.bgm_pan_l_var = tk.StringVar(value="")
        self.bgm_pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.bgm_pan_box, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.bgm_pan_l_entry = ctk.CTkEntry(
            self.bgm_pan_box, width=42, height=24,
            textvariable=self.bgm_pan_l_var, font=ctk.CTkFont(size=11))
        self.bgm_pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.bgm_pan_box, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.bgm_pan_r_entry = ctk.CTkEntry(
            self.bgm_pan_box, width=42, height=24,
            textvariable=self.bgm_pan_r_var, font=ctk.CTkFont(size=11))
        self.bgm_pan_r_entry.pack(side="left", padx=(2, 0))
