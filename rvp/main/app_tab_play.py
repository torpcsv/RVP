"""メイン画面: 再生タブのウィジェット構築(_build_tab_play)(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from ..scenario import TRACK_ROTATE_A10
from ..i18n import tr

from .common import (COMBO_TEXT, COMBO_TEXT_DISABLED, ERROR_TEXT, LABEL,
    MUTED, WARN_TEXT)
from .device_graph import DeviceGraph
from .widgets import FixedBtn, RangeSlider, ZoneBar
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _RVPAppTabPlayMixin:
    """RVPApp の mixin(=301 分割)。再生タブのウィジェット構築(_build_tab_play)"""

    def _build_tab_play(self, tab):
        # =60: 縦スクロールバーを廃止(ユーザー要望)。常設の操作バーと
        # ◀▶切替領域で高さを分け合い、どのページも必ず画面内に収める。
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=8, pady=8)
        self.play_wrap = wrap

        # ============ 常設の操作バー(=60 ユーザー要望) ============
        # 経過/全長・シークバー・↺10・再生/一時停止・↻10・音量・◀◀/▶▶ は
        # ページ切替と関係なく**常に**表示する(=57で①再生ページへ入れたが、
        # 使ってみると常時操作できる方がよいという判断)。
        bar = ctk.CTkFrame(wrap, corner_radius=12, fg_color=_clr.CARD_COLOR,
                           border_width=1, border_color=_clr.CARD_BORDER)
        bar.pack(fill="x")
        self.transport_card = bar
        tp = ctk.CTkFrame(bar, fg_color="transparent")
        tp.pack(fill="x", padx=16, pady=(10, 12))

        # 音声シークバー(再生秒数/合計時間)
        self.time_label = ctk.CTkLabel(
            tp, text="00:00.0 / 00:00.0", text_color=LABEL,
            font=ctk.CTkFont(size=12),
        )
        self.time_label.pack(anchor="w", pady=(6, 0))
        self.seek_slider = ctk.CTkSlider(
            tp, from_=0, to=1000, number_of_steps=1000,
            height=18, progress_color=_clr.ACCENT,
            button_color=_clr.ACCENT, button_hover_color=_clr.ACCENT_HOVER,
            state="disabled",
        )
        self.seek_slider.set(0)
        self.seek_slider.pack(fill="x", pady=(2, 0))
        self._seek_dragging = False
        self.seek_slider.bind("<Button-1>", self._on_seek_press)
        self.seek_slider.bind("<ButtonRelease-1>", self._on_seek_release)

        # 再生ボタン類(シナリオ終了ボタンは廃止: 誤操作による意図しない終了を防ぐ)
        # 1行目: 10秒巻き戻し / 再生・一時停止 / 10秒早送り / 音量スライダー
        # 2行目: ◀◀現在のイベントを巻き戻す / ▶▶現在のイベントをスキップする
        # 1行目: ↺10・再生/一時停止・↻10・音量を中央、右端に
        # 「自動選択(ランダム)」チェック(=61)。チェックはもともと選択肢
        # カードの中にあったが、そのカードが待機中も常に出ていて切替領域を
        # 削っていたため操作バーへ移した(2行目は◀◀/▶▶の文字が長く、
        # 幅690では入りきらないので1行目に置く)。
        row1 = ctk.CTkFrame(tp, fg_color="transparent")
        row1.pack(fill="x", pady=(10, 0))
        self.auto_select_var = tk.BooleanVar(value=False)
        self.auto_select_check = ctk.CTkCheckBox(
            row1, text=tr("自動選択（ランダム）"), variable=self.auto_select_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_autoselect_toggle)
        transport = ctk.CTkFrame(row1, fg_color="transparent")
        transport.pack()
        self.play_transport_row = transport

        # =228: 幅が文字で変わらない FixedBtn(押すたびに横幅が動く不具合)
        def small_btn(text, command, width=52, height=52, font_size=14):
            return FixedBtn(
                transport, text=text, width=width, height=height,
                corner_radius=height // 2, font=ctk.CTkFont(size=font_size),
                fg_color="transparent", border_width=1,
                border_color=MUTED, text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=command, state="disabled",
            )

        # 渦を巻いた矢印+10: 左回転=10秒巻き戻し / 右回転=10秒早送り
        # =229: 幅は**高さより広い錠剤形**になる値(FB1: 正円は不可)
        self.btn_seek_back = small_btn("↺10", self.on_seek_back10, width=82)
        self.btn_seek_back.pack(side="left", padx=6)

        # =228: 「▶」⇔「❚❚」で幅が変わらないよう FixedBtn。
        # 幅 92 × 高さ 72(角丸36)= **横長の錠剤形**(=229 FB1)
        self.btn_play = FixedBtn(
            transport, text="▶", width=92, height=72,
            corner_radius=36, font=ctk.CTkFont(size=24),
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self.on_play_pause, state="disabled",
        )
        self.btn_play.pack(side="left", padx=6)

        self.btn_seek_fwd = small_btn("↻10", self.on_seek_fwd10, width=82)
        self.btn_seek_fwd.pack(side="left", padx=6)

        # 音量スライダー(右端=最大 / 左端=消音)。常時操作可能
        vol_box = ctk.CTkFrame(transport, fg_color="transparent")
        vol_box.pack(side="left", padx=(14, 0))
        ctk.CTkLabel(vol_box, text="🔊", font=ctk.CTkFont(size=14),
                     text_color=LABEL).pack(side="left", padx=(0, 4))
        # =61: 「自動選択」チェックを同じ行へ入れた分、幅を150→104へ
        self.volume_slider = ctk.CTkSlider(
            vol_box, from_=0, to=100, number_of_steps=100, width=104,
            height=16, progress_color=_clr.ACCENT,
            button_color=_clr.ACCENT, button_hover_color=_clr.ACCENT_HOVER,
            command=self._on_volume_change,
        )
        self.volume_slider.set(100)
        self.volume_slider.pack(side="left")

        event_row = ctk.CTkFrame(tp, fg_color="transparent")
        event_row.pack(pady=(6, 0))
        self.event_btn_row = event_row

        def event_btn(text, command):
            return ctk.CTkButton(
                event_row, text=text, height=30, corner_radius=15,
                font=ctk.CTkFont(size=12),
                fg_color="transparent", border_width=1,
                border_color=MUTED, text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=command, state="disabled",
            )

        self.btn_back = event_btn(tr("◀◀ 現在のイベントを巻き戻す"),
                                  self.on_back_event)
        self.btn_back.pack(side="left", padx=8)
        self.btn_skip = event_btn(tr("▶▶ 現在のイベントをスキップする"),
                                  self.on_skip_event)
        self.btn_skip.pack(side="left", padx=8)

        # ============ ◀▶で巡回する単一領域(=57) ============
        # 以前は「上部=再生まわり(常時)」「下部=◀▶で①②③を切替」の2領域
        # 構成だったが、1600x900のデスクトップでは縦が足りないため、再生
        # まわりも切替対象の1ページにして領域を1つへ統合した(ユーザー要望)。
        # ページ(=150で並び替え): ①再生 / ②イベント遷移 / ③変数・イベントログ /
        # ④デバイス調整 / ⑤グラフ表示。デバイス関連の2枚を末尾へ寄せてあるのは、
        # デバイス連動なしのシナリオで**末尾2枚をまとめて外して3画面**にできる
        # ようにするため(=150)。
        area = ctk.CTkFrame(wrap, fg_color="transparent")
        area.pack(fill="both", expand=True, pady=(8, 0))
        self.play_area = area
        # =60: 縦スクロールバーを廃止した分、◀▶を押しやすく広げる
        # =89: =85のドット(●○○○○)は「小さくて現在位置の把握が難しい」
        # という実機フィードバックで廃止し、画面下部のバーランプへ置き換えた
        # (◀▶ボタンは=84以前の直接packへ戻した)。
        # =110: 枠線(border_width=1)は従来どおり。中の◀▶だけを
        # **文字から描いた三角へ**差し替える(=109で枠線を消したのは
        # 依頼の読み違い。ユーザーの意図は「[◀]の四角い字形をやめて
        # ただの三角にする」で、枠線は残す)。詳細は _triangle_photo。
        # 画像はGCされると消えるので self に参照を残す。テーマ切替では
        # _refresh_arrow_icons() で描き直す(色が明暗で変わるため)。
        self._arrow_photos: dict = {}
        self.play_prev_btn = ctk.CTkButton(
            area, text="", width=self.PAGE_BTN_W,
            fg_color="transparent", border_width=1, border_color=_clr.CARD_BORDER,
            text_color=LABEL, hover_color=("gray85", "gray25"),
            command=lambda: self._switch_play_page(-1))
        self.play_prev_btn.pack(side="left", fill="y", padx=(0, 4))
        self.play_next_btn = ctk.CTkButton(
            area, text="", width=self.PAGE_BTN_W,
            fg_color="transparent", border_width=1, border_color=_clr.CARD_BORDER,
            text_color=LABEL, hover_color=("gray85", "gray25"),
            command=lambda: self._switch_play_page(+1))
        self.play_next_btn.pack(side="right", fill="y", padx=(4, 0))
        self._refresh_arrow_icons()
        # 領域は「操作バー・選択肢カード・メッセージを引いた残り全部」。
        # height=1 + pack_propagate(False) で中身の高さに引きずられなくする
        # (=60。以前は下限の固定値に合わせていた)。中身は上詰めの
        # まま=余白ができても要素を均等配置し直さない(ユーザー指定)。
        content = ctk.CTkFrame(area, fg_color="transparent", height=1)
        content.pack(side="left", fill="both", expand=True)
        content.pack_propagate(False)
        self.play_content = content

        # ============ ①再生: 音源再生まわり ============
        upper = ctk.CTkFrame(content, corner_radius=12, fg_color=_clr.CARD_COLOR,
                             border_width=1, border_color=_clr.CARD_BORDER)
        self.play_card = upper
        up = ctk.CTkFrame(upper, fg_color="transparent")
        up.pack(fill="x", padx=16, pady=(12, 14))

        # 選択中シナリオ
        ctk.CTkLabel(
            up, text=tr("選択中のシナリオファイル"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w")
        self.play_scenario_label = ctk.CTkLabel(
            up, text=tr("(未選択)"),
            font=ctk.CTkFont(size=14, weight="bold"),
            wraplength=540, justify="left",
        )
        self.play_scenario_label.pack(anchor="w", pady=(2, 8))

        # ステータス
        self.status_label = ctk.CTkLabel(
            up, text=tr("待機中"),
            font=ctk.CTkFont(size=20, weight="bold"), text_color=LABEL,
        )
        self.status_label.pack(anchor="w")

        # イベント名+経過 / ステート+経過 / 音声(L/C/R)
        self.event_label = ctk.CTkLabel(
            up, text=tr("イベント: -"), text_color=LABEL,
            font=ctk.CTkFont(size=12), wraplength=540, justify="left",
        )
        self.event_label.pack(anchor="w", pady=(6, 0))
        self.state_label = ctk.CTkLabel(
            up, text=tr("ステート: -"), text_color=LABEL,
            font=ctk.CTkFont(size=12), wraplength=540, justify="left",
        )
        self.state_label.pack(anchor="w")
        # =289: 変数の現在値の行(「変数: HP=3 ...」)は廃止(ユーザー決定)。
        # 変数は③変数・イベントログのページでだけ確認する。
        # 再生中のチャンネル(L/C/R)。=68: 1行の「音声: ★C: a.wav / L: b.wav」
        # から**チャンネルごとの行**へ変更した(ユーザー要望=①再生ページの
        # 空きスペースの活用)。行数は常に3で固定なので、名前の長さや
        # チャンネル数が変わっても下の領域が動かない。
        ctk.CTkLabel(
            up, text=tr("再生中のチャンネル"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(10, 2))
        self.play_ch_card = ctk.CTkFrame(up, corner_radius=8,
                                         fg_color=_clr.CARD_COLOR)
        self.play_ch_card.pack(fill="x")
        self.ch_mark_labels = {}
        self.ch_name_labels = {}
        self.play_ch_rows = []          # =159: 高さを凍結するために持っておく
        for _cid in ("L", "C", "R"):
            _row = ctk.CTkFrame(self.play_ch_card, fg_color="transparent")
            _row.pack(fill="x", padx=12, pady=4)
            self.play_ch_rows.append(_row)
            ctk.CTkLabel(_row, text=_cid, width=16, anchor="w",
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=LABEL).pack(side="left")
            _mark = ctk.CTkLabel(_row, text="", width=58, anchor="w",
                                 font=ctk.CTkFont(size=11), text_color=_clr.ACCENT_TEXT)
            _mark.pack(side="left")
            _name = ctk.CTkLabel(_row, text="—", anchor="w", height=20,
                                 font=ctk.CTkFont(size=13), text_color=MUTED,
                                 wraplength=420, justify="left")
            _name.pack(side="left", fill="x", expand=True)
            self.ch_mark_labels[_cid] = _mark
            self.ch_name_labels[_cid] = _name
        self.play_star_label = ctk.CTkLabel(
            up, text=tr("★=シークバーが追従しているチャンネル"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w",
        )
        self.play_star_label.pack(anchor="w", pady=(2, 0))

        # =159: **「R行と★行だけが動く」のはなぜか**(ユーザー質問への回答)。
        # 3行は上のループで**まったく同じ設定**から作っており、R行に特別な
        # 実装は無い。違うのは**並び順だけ**で、pack は容れ物の高さが足りない
        # とき**最後に置いた子から取り分を削る**。だから縦を縮めると
        # R行 → ★行 の順に潰れ、先に置いたL行・C行は動かないように見える。
        # 高さを固定しても(`configure(height=…)`+`pack_propagate(False)`)、
        # 親が割り当てる区画そのものが小さくなるので**結果は変わらない**
        # (=159で実測して確認)。**根本の対策は「足りない状況を作らない」**
        # ことなので、最小の高さを 700 → `MIN_WIN_H`(790) へ上げた。


        # ============ 選択肢領域(選択肢がアクティブな間だけ表示) ============
        # =60: ◀▶切替領域の**外**(操作バーの下)へ移した。どのページを見て
        # いてもボタンが必ず全部見えるので、スクロールが要らなくなる
        # (=57ではページの中に入れて①へ自動切替していた)。
        self.choice_card = ctk.CTkFrame(wrap, corner_radius=12,
                                        fg_color=_clr.CARD_COLOR,
                                        border_width=2, border_color=_clr.ACCENT)
        ch_in = ctk.CTkFrame(self.choice_card, fg_color="transparent")
        ch_in.pack(fill="x", padx=16, pady=(10, 12))
        # =61: 自動選択チェックは操作バーへ移した(このカードは選択肢が
        # 実際に出ているときだけ表示する)。
        ch_head = ctk.CTkFrame(ch_in, fg_color="transparent")
        ch_head.pack(fill="x")
        self.choice_head = ch_head
        ctk.CTkLabel(ch_head, text=tr("選択してください"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=_clr.ACCENT_TEXT).pack(side="left")
        self.choice_timer_label = ctk.CTkLabel(
            ch_head, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=WARN_TEXT)
        self.choice_timer_label.pack(side="right")
        self.choice_grid = ctk.CTkFrame(ch_in, fg_color="transparent")
        self.choice_grid.pack(fill="x", pady=(8, 0))
        self.choice_buttons: list = []
        self._choice_sig = None

        # ============ 数値入力領域(入力要求がアクティブな間だけ表示) ============
        self.input_card = ctk.CTkFrame(wrap, corner_radius=12,
                                       fg_color=_clr.CARD_COLOR,
                                       border_width=2, border_color=_clr.ACCENT)
        in_in = ctk.CTkFrame(self.input_card, fg_color="transparent")
        in_in.pack(fill="x", padx=16, pady=(10, 12))
        in_head = ctk.CTkFrame(in_in, fg_color="transparent")
        in_head.pack(fill="x")
        self.input_prompt_label = ctk.CTkLabel(
            in_head, text=tr("数値を入力してください"),
            font=ctk.CTkFont(size=14, weight="bold"), text_color=_clr.ACCENT_TEXT)
        self.input_prompt_label.pack(side="left")
        self.input_range_label = ctk.CTkLabel(
            in_head, text="", font=ctk.CTkFont(size=12), text_color=LABEL)
        self.input_range_label.pack(side="left", padx=(10, 0))
        in_row = ctk.CTkFrame(in_in, fg_color="transparent")
        in_row.pack(fill="x", pady=(8, 0))
        self.input_var = tk.StringVar(value="")
        self.input_entry = ctk.CTkEntry(
            in_row, textvariable=self.input_var, width=140, height=36,
            justify="right", font=ctk.CTkFont(size=15))
        self.input_entry.pack(side="left")
        self.input_entry.bind("<Return>", lambda _e: self._on_input_submit())
        self.input_submit_btn = ctk.CTkButton(
            in_row, text=tr("決定"), width=90, height=36,
            font=ctk.CTkFont(size=14),
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_input_submit)
        self.input_submit_btn.pack(side="left", padx=(8, 0))
        self.input_error_label = ctk.CTkLabel(
            in_row, text="", font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ERROR_TEXT)
        self.input_error_label.pack(side="left", padx=(12, 0))
        self._input_sig = None
        self._input_bounds = (None, None)

        # ---- ②デバイス調整 ----
        lower = ctk.CTkFrame(content, corner_radius=12, fg_color=_clr.CARD_COLOR,
                             border_width=1, border_color=_clr.CARD_BORDER)
        self.lower_card = lower
        low = ctk.CTkFrame(lower, fg_color="transparent")
        # =88: TWIST常時表示で5グループが恒常になり、=79時点から7px
        # はみ出していたため、カード内の上下余白を(12,14)→(8,10)へ詰めて
        # 690x820に5グループが収まるようにした(test_play_layoutで検証)。
        low.pack(fill="x", padx=16, pady=(8, 10))

        # LINEAR / ROTATE / VIBRATION
        # gridで列を揃え、3本のスライダー幅を完全に一致させる。
        # 列0=スライダー・駆動値バー(伸縮) / 列1=反転・強度数値 / 列2=動作タイミング
        # 各グループ: 見出し行 → スライダー行(+反転+タイミング) → 駆動値バー行
        self.offset_vars: dict[str, tk.StringVar] = {}
        grid = ctk.CTkFrame(low, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1)

        def head_row(text, value_text):
            head = ctk.CTkFrame(grid, fg_color="transparent")
            title = ctk.CTkLabel(head, text=text,
                                 font=ctk.CTkFont(size=11, weight="bold"),
                                 text_color=LABEL)
            title.pack(side="left")
            value = ctk.CTkLabel(head, text=value_text,
                                 font=ctk.CTkFont(size=11, weight="bold"),
                                 text_color=_clr.ACCENT_TEXT)
            value.pack(side="left", padx=(10, 0))
            return value, head, title

        def invert_cell(var, command):
            return ctk.CTkCheckBox(
                grid, text=tr("反転"), variable=var,
                font=ctk.CTkFont(size=11), text_color=LABEL,
                checkbox_width=18, checkbox_height=18, width=54,
                fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                command=command)

        def value_cell(text=tr("停止")):
            return ctk.CTkLabel(
                grid, text=text, width=54, height=14, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"), text_color=LABEL)

        # トラックのグループ(並び替え・未接続の灰色表示用)
        self.track_groups: dict = {}

        def register_group(key, head, title, head_value, slider, invert,
                           offset_cell, bar, value):
            self.track_groups[key] = {
                "head": head, "title": title, "head_value": head_value,
                "slider": slider, "invert": invert, "offset": offset_cell,
                "bar": bar, "value": value}

        # --- LINEAR ---
        self.range_label, lin_head, lin_title = head_row(
            "LINEAR", tr("区間補正0～100"))
        # 速度制限(安全機構): フルストロークにかける最短時間で上限速度を決める
        self.speed_limit_var = tk.StringVar(value=tr("中"))
        self.speed_limit_menu = ctk.CTkOptionMenu(
            lin_head, variable=self.speed_limit_var, width=76, height=22,
            font=ctk.CTkFont(size=11),
            values=[tr("なし"), tr("弱"), tr("中"), tr("強")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=lambda _v: self._on_speed_limit_change())
        self.speed_limit_menu.pack(side="right")
        # 用語: 旧「速度制限」→「急動作防止」(2026-07-22ユーザー決定。
        # 区間補正(動く範囲)との違いが分かるよう目的ベースの名前に)
        ctk.CTkLabel(lin_head, text=tr("急動作防止"),
                     font=ctk.CTkFont(size=11), text_color=LABEL
                     ).pack(side="right", padx=(0, 4))
        self.range_slider = RangeSlider(
            grid, from_=0, to=100, step=5, command=self._on_range_change)
        self.invert_var = tk.BooleanVar(value=False)
        self.invert_check = invert_cell(self.invert_var,
                                        self._on_invert_change)
        lin_offset = self._build_offset_cell(grid, "linear")
        self.pos_bar = ZoneBar(grid, mode="linear", height=12)
        self.pos_value_label = value_cell(text="")
        register_group("linear", lin_head, lin_title, self.range_label,
                       self.range_slider, self.invert_check, lin_offset,
                       self.pos_bar, self.pos_value_label)

        # --- TWIST(=79・2軸linearデバイスの2軸目。表示は接続タブのチェックで切替) ---
        self.twist_range_label, twist_head, twist_title = head_row(
            "TWIST", tr("区間補正0～100"))
        self.twist_range_slider = RangeSlider(
            grid, from_=0, to=100, step=5, command=self._on_twist_range_change)
        self.twist_invert_var = tk.BooleanVar(value=False)
        self.twist_invert_check = invert_cell(self.twist_invert_var,
                                              self._on_twist_invert_change)
        twist_offset = self._build_offset_cell(grid, "twist")
        self.twist_bar = ZoneBar(grid, mode="linear", height=12)
        self.twist_value_label = value_cell(text="")
        register_group("twist", twist_head, twist_title,
                       self.twist_range_label, self.twist_range_slider,
                       self.twist_invert_check, twist_offset,
                       self.twist_bar, self.twist_value_label)

        # --- ROTATE ---
        self.rotate_scale_label, rot_head, rot_title = head_row(
            "ROTATE(ufo)", tr("出力補正0～100%"))
        self.rotate_scale_slider = RangeSlider(
            grid, from_=0, to=100, step=5,
            command=self._on_rotate_range_change)
        self.rotate_invert_var = tk.BooleanVar(value=False)
        self.rotate_invert_check = invert_cell(self.rotate_invert_var,
                                               self._on_rotate_invert_change)
        rot_offset = self._build_offset_cell(grid, "rotate")
        self.rotate_bar = ZoneBar(grid, mode="output", height=12)
        self.rotate_value_label = value_cell()
        register_group("rotate", rot_head, rot_title, self.rotate_scale_label,
                       self.rotate_scale_slider, self.rotate_invert_check,
                       rot_offset, self.rotate_bar, self.rotate_value_label)

        # --- ROTATE(a10cyclonesa): A10サイクロンSA専用のrotate ---
        self.a10_scale_label, a10_head, a10_title = head_row(
            "ROTATE(a10cyclonesa)", tr("出力補正0～100%"))
        self.a10_scale_slider = RangeSlider(
            grid, from_=0, to=100, step=5,
            command=self._on_a10_range_change)
        self.a10_invert_var = tk.BooleanVar(value=False)
        self.a10_invert_check = invert_cell(self.a10_invert_var,
                                            self._on_a10_invert_change)
        a10_offset = self._build_offset_cell(grid, TRACK_ROTATE_A10)
        self.a10_bar = ZoneBar(grid, mode="output", height=12)
        self.a10_value_label = value_cell()
        register_group(TRACK_ROTATE_A10, a10_head, a10_title,
                       self.a10_scale_label, self.a10_scale_slider,
                       self.a10_invert_check, a10_offset,
                       self.a10_bar, self.a10_value_label)

        # ROTATEレーンの「左右反転」チェック(ufotwが割り当たった時だけ表示)。
        # タイプB(5列CSV)の左右chを入れ替える。両ロータへ同一補正がかかる。
        self.rotate_swap_var = tk.BooleanVar(value=False)
        self.rotate_swap_check = ctk.CTkCheckBox(
            rot_head, text=tr("左右反転"), variable=self.rotate_swap_var,
            font=ctk.CTkFont(size=11), text_color=LABEL,
            checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=lambda: self._on_rotate_swap_change("ufo"))
        self.a10_swap_var = tk.BooleanVar(value=False)
        self.a10_swap_check = ctk.CTkCheckBox(
            a10_head, text=tr("左右反転"), variable=self.a10_swap_var,
            font=ctk.CTkFont(size=11), text_color=LABEL,
            checkbox_width=16, checkbox_height=16,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=lambda: self._on_rotate_swap_change("a10"))
        self._swap_checks = {
            "ufo": (self.rotate_swap_check, self.rotate_swap_var),
            "a10": (self.a10_swap_check, self.a10_swap_var)}
        self._swap_vis = {"ufo": False, "a10": False}
        self._retarget_job = None   # =71 停止中の駆動区間追従(間引き用)
        self._twist_retarget_job = None   # =79 TWIST版
        # 未接続プレビュー用: 読込中シナリオが2ch(タイプBのCSV)のrotate内容を
        # 持つレーン。接続デバイスが無くても左右分割表示・左右反転チェックを
        # 出せるようにするための判定に使う(_load_scenario で更新)。
        self._scenario_split_lanes = {"ufo": False, "a10": False}

        # --- VIBRATION ---
        self.vibration_scale_label, vib_head, vib_title = head_row(
            "VIBRATION", tr("出力補正0～100%"))
        self.vibration_scale_slider = RangeSlider(
            grid, from_=0, to=100, step=5,
            command=self._on_vibration_range_change)
        # vibrationに反転は無い(列1は空のまま=列幅は他行で確保)
        vib_offset = self._build_offset_cell(grid, "vibration")
        self.vibration_bar = ZoneBar(grid, mode="output", height=12)
        self.vibration_value_label = value_cell()
        register_group("vibration", vib_head, vib_title,
                       self.vibration_scale_label,
                       self.vibration_scale_slider, None, vib_offset,
                       self.vibration_bar, self.vibration_value_label)

        # 既定順で配置し、接続状況(最初は全て未接続=灰色)を反映する
        self._track_order: list | None = None
        self._track_conn: dict = {}
        self._track_shown: tuple = ()   # =79 表示中のトラック欄(TWIST切替の検知)
        self._grid_track_groups(list(self.TRACK_ORDER))
        self._update_track_conn()

        # ---- ③グラフ表示(=69。実行中スクリプトの波形。◀▶で切替) ----
        # デバイス種別ごとに「いつ・どれくらいの強さで・どう動くか」を
        # 時間軸で見る。視聴中の予見と、編集時のfunscript紐づけ確認が目的。
        self.graph_card = ctk.CTkFrame(content, corner_radius=12,
                                       fg_color=_clr.CARD_COLOR,
                                       border_width=1, border_color=_clr.CARD_BORDER)
        gp = ctk.CTkFrame(self.graph_card, fg_color="transparent")
        gp.pack(fill="both", expand=True, padx=10, pady=(8, 6))
        self.graph_view = DeviceGraph(gp)
        self.graph_view.on_seek = self._on_graph_seek      # =237
        self.graph_view.pack(fill="both", expand=True)
        # =102: 下部のヒント行に「1枚表示⇄個別表示」の切替ボタンを置く
        # (グラフ上に浮かせると凡例・追従停止表示と重なるため)。
        # ボタンの文言は「押すと切り替わる先」を示す。
        graph_hint_row = ctk.CTkFrame(gp, fg_color="transparent")
        graph_hint_row.pack(fill="x", pady=(4, 0))
        self.graph_overlay_btn = ctk.CTkButton(
            graph_hint_row, text=tr("1枚表示"), width=78, height=22,
            font=ctk.CTkFont(size=11),
            fg_color=("gray78", "gray30"), text_color=("gray15", "gray90"),
            hover_color=("gray70", "gray38"),
            command=self._toggle_graph_overlay)
        self.graph_overlay_btn.pack(side="right")
        ctk.CTkLabel(
            graph_hint_row,
            text=tr("ホイール=時間の拡大縮小 ／ ドラッグ=前後を見る ／ クリック=再生位置へ戻る"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # ---- ④イベント遷移(表示専用の図。◀▶で切替) ----
        # 実行中イベントを黄緑、◀◀で戻れる履歴経路を緑線で表示する。
        # ステート形式イベント実行中は上下半分に分割し、下半分にステート図。
        self.map_card = ctk.CTkFrame(content, corner_radius=12,
                                     fg_color=_clr.CARD_COLOR,
                                     border_width=1, border_color=_clr.CARD_BORDER)
        mp = ctk.CTkFrame(self.map_card, fg_color="transparent")
        mp.pack(fill="both", expand=True, padx=10, pady=10)
        mp.grid_columnconfigure(0, weight=1)
        mp.grid_rowconfigure(0, weight=1)
        self._map_grid = mp
        ev_wrap = ctk.CTkFrame(mp, corner_radius=10,
                               fg_color=("gray82", "gray24"))
        ev_wrap.grid(row=0, column=0, sticky="nsew")
        ev_wrap.grid_columnconfigure(0, weight=1)
        ev_wrap.grid_rowconfigure(0, weight=1)
        # 高さ: イベント図230/ステート図190(ステート領域を広げる=2026-07-22
        # ユーザー要望でイベント側-50/ステート側+50)
        self.map_canvas = tk.Canvas(ev_wrap, height=230,
                                    highlightthickness=0, bd=0)
        self.map_canvas.grid(row=0, column=0, sticky="nsew",
                             padx=(8, 0), pady=(8, 0))
        map_vs = ctk.CTkScrollbar(ev_wrap, orientation="vertical",
                                  command=self.map_canvas.yview)
        map_vs.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        map_hs = ctk.CTkScrollbar(ev_wrap, orientation="horizontal",
                                  command=self.map_canvas.xview)
        map_hs.grid(row=1, column=0, sticky="ew", padx=(8, 0))
        self.map_canvas.configure(yscrollcommand=map_vs.set,
                                  xscrollcommand=map_hs.set)
        self._install_map_drag_pan(self.map_canvas)   # =287
        # 下半分: ステート図(ステート形式イベント実行中のみ grid する)
        st_wrap = ctk.CTkFrame(mp, corner_radius=10,
                               fg_color=("gray82", "gray24"))
        st_wrap.grid_columnconfigure(0, weight=1)
        st_wrap.grid_rowconfigure(0, weight=1)
        self.map_state_wrap = st_wrap
        # =287: ステート図は1行(scrollregion 高さ130)なので 140 に詰め、
        # 上下分割でも均等割りせず(row1 weight=0)イベント図に残りを回す
        # (従来190・weight=1 で下半分に余白が出ていた=ユーザー報告)
        self.map_state_canvas = tk.Canvas(st_wrap, height=self.STATE_MAP_H,
                                          highlightthickness=0, bd=0)
        self.map_state_canvas.grid(row=0, column=0, sticky="nsew",
                                   padx=(8, 0), pady=(8, 0))
        # イベント図と統一の縦横スクロールバー(将来のステート図拡張にも備える)
        # =287: CTkScrollbar の既定高さ(200)が行の最小高さになるので canvas に合わせる
        map_svs = ctk.CTkScrollbar(st_wrap, orientation="vertical",
                                   height=self.STATE_MAP_H,
                                   command=self.map_state_canvas.yview)
        map_svs.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        map_shs = ctk.CTkScrollbar(st_wrap, orientation="horizontal",
                                   command=self.map_state_canvas.xview)
        map_shs.grid(row=1, column=0, sticky="ew", padx=(8, 0))
        self.map_state_canvas.configure(xscrollcommand=map_shs.set,
                                        yscrollcommand=map_svs.set)
        self._install_map_drag_pan(self.map_state_canvas)   # =287
        # ---- ④変数・イベントログ(テキスト形式・表示専用。◀▶で切替) ----
        # 上段=変数の現在値(key: value)、区切り線、下段=通過したイベント/
        # ステートの追記式ログ(音声・funscript/CSV情報は記載しない)。
        self.log_card = ctk.CTkFrame(content, corner_radius=12,
                                     fg_color=_clr.CARD_COLOR,
                                     border_width=1, border_color=_clr.CARD_BORDER)
        lg = ctk.CTkFrame(self.log_card, fg_color="transparent")
        lg.pack(fill="both", expand=True, padx=12, pady=10)
        ctk.CTkLabel(lg, text=tr("変数(key: value)"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=LABEL, anchor="w").pack(fill="x")
        self.log_vars_box = ctk.CTkTextbox(
            lg, height=92, font=ctk.CTkFont(size=12), wrap="none",
            fg_color=("gray98", "gray14"))
        self.log_vars_box.pack(fill="x", pady=(2, 0))
        self.log_vars_box.configure(state="disabled")
        # 区切り(仕様の「---」に相当)
        ctk.CTkFrame(lg, height=2, fg_color=_clr.CARD_BORDER).pack(
            fill="x", pady=(8, 8))
        ctk.CTkLabel(lg, text=tr("イベントログ(通過したイベント・ステート)"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=LABEL, anchor="w").pack(fill="x")
        self.log_events_box = ctk.CTkTextbox(
            lg, font=ctk.CTkFont(size=12), wrap="none",
            fg_color=("gray98", "gray14"))
        self.log_events_box.pack(fill="both", expand=True, pady=(2, 0))
        self.log_events_box.configure(state="disabled")
        self._log_sig = None         # ③の差分更新用シグネチャ
        self._log_len = 0            # ログ欄へ書き出し済みのエントリ数

        # =150: ページは**番号ではなくID**で扱う(PLAY_PAGE_ORDER)。
        # self._play_pages = 現在表示中のページID列(巡回順)、
        # self._play_page  = その中での位置(0起点)。
        # 表示中のページIDは self.play_page_id で取る。
        # 最後に開いていたページの保存は廃止(ユーザー決定)なので、
        # 起動時は常に①再生から始まる。
        self._play_pages = list(self.PLAY_PAGE_ORDER)
        self._play_page = 0
        # (=89 バーランプはこの後で生成されるため、初期点灯は生成直後に行う)
        self._map_data = None        # 図の描画用: 読込シナリオの生JSON dict
        self._map_sig = None         # 差分更新用シグネチャ
        self._map_split = False      # ステート図(下半分)を表示中か

        self.msg_label = ctk.CTkLabel(
            wrap, text="", text_color=ERROR_TEXT,
            font=ctk.CTkFont(size=12), wraplength=540, justify="left",
        )
        # =89: バーランプの高さを捻出するため pady (8,0)→(4,0)
        self.msg_label.pack(anchor="w", pady=(4, 0))

        # =89: ページ位置のバーランプ(ユーザー依頼・=85ドットの置き換え)。
        # 画面下部にページ数ぶんに分割したバーを常設し、現在ページを黄緑
        # (点灯)・他を消灯(黒)で表す。文字や記号は描かない(ユーザー指定)。
        # =150: セグメントは最大数(5)を作っておき、表示中のページ数に
        # 合わせて末尾を pack_forget する(3画面なら3分割になる)。
        # セグメントのクリックでそのページへ直接ジャンプできる(おまけ)。
        # 高さの収支: ランプ14+pady4=18px は、タブ全体の下余白
        # (tabs.pack の pady 18→4)+メッセージ上余白(8→4)の計18pxで
        # 相殺=5ページの表示域は不変(②デバイス調整は空き+1pxしか
        # ないため必須の設計)。
        lamp_row = ctk.CTkFrame(wrap, fg_color="transparent",
                                height=self.PAGE_LAMP_H)
        lamp_row.pack(fill="x", pady=(4, 0))
        lamp_row.pack_propagate(False)
        self.page_lamp_row = lamp_row
        self.page_lamps = []
        for i in range(self.PLAY_PAGE_COUNT):
            seg = tk.Frame(lamp_row, bg=self.PAGE_LAMP_OFF,
                           height=self.PAGE_LAMP_H, bd=0,
                           highlightthickness=0, cursor="hand2")
            seg.pack(side="left", fill="both", expand=True,
                     padx=(0 if i == 0 else 6, 0))
            seg.bind("<Button-1>", lambda _e, p=i: self._goto_play_page(p))
            self.page_lamps.append(seg)
        self._update_page_lamp()     # 起動時は①が点灯
        # 初期ページ(①再生)を表示(=61: pack ではなく place)
        self._play_scroll = 0
        self.play_card.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        # 領域の高さが変わったら(選択肢カードの出し入れ等)配置し直す
        content.bind("<Configure>", lambda _e: self._refresh_play_card())
        for card in (self.play_card, self.lower_card, self.graph_card,
                     self.map_card, self.log_card):
            card.bind("<Configure>", lambda _e: self._refresh_play_card())
        # ホイール: アプリ全体に張って、ハンドラ側で領域内かを判定する
        # (Enter/Leave だと子ウィジェットへ移った瞬間に外れてしまうため)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._on_play_wheel, add="+")
