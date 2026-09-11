"""アイテムレビュー画面(ItemReviewDialog)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import threading
import tkinter as tk
from ..funscript import Funscript
from ..winstate import WindowMemory
from ..i18n import load_config, save_config, tr
from ..rotate_source import load_rotate_source

from .common import (BOX_BG, BOX_BORDER, CTkOptionMenu, MSG_WARN, MUTED,
    TEXT_MUTED, _front_window, _place_popup)
from .fields import Tooltip
from .review_edit import _ItemReviewEditMixin
from .review_patterns import _ItemReviewPatternsMixin
from .review_playback import _ItemReviewPlaybackMixin
from .review_support import (REVIEW_ROTATE_TYPES, REVIEW_SLOT, SPEED_CHOICES,
    WAVE_MODE_KEYS, _review_segments, compute_wave_env)
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class ItemReviewDialog(_ItemReviewPlaybackMixin, _ItemReviewEditMixin, _ItemReviewPatternsMixin, ctk.CTkToplevel):
    """アイテムレビュー画面(=164)。非モーダル・同時に1つまで。

    編集画面(ScenarioEditor.open_item_review)から開く。中身の差し替えは
    `load(spec)` で行い、ウィンドウは作り直さない。
    """

    REFRESH_MS = 33          # 表示の更新間隔(約30fps)

    WIN_W = 1380             # =211: レビュー画面の既定の幅(720→1380)

    MIN_WIN_H = 220          # 下限(実際の高さは reqheight を測って決める)

    GRAPH_ROW_H = 87         # DeviceGraph.ROW_H と同じ

    GRAPH_AXIS_H = 18        # DeviceGraph.AXIS_H と同じ

    GRAPH_ROWS_MAX = 4       # これ以上はドラッグで送って見る(再生タブと同じ)

    EDIT_WIN_W = 1380        # =170: 編集モードのウィンドウ幅(=211: 960→1380)

    EDIT_WIN_H = 780         # =211: 編集モードの高さの既定=下限

    CFG_REVIEW_WIN = "review_win"   # =211: 編集モードの高さの記憶先(config)

    EDIT_GRAPH_H = 300       # =170: 編集用グラフの高さ(1本だけを大きく)

    EDIT_SUB_H = 90          # =227: サブ表示ONのときの各グラフの要求高さ

    #                          (余りは expand で等分=上下半々になる)
    # =215: パレットのボタン幅。絵柄(40px)つきのボタンは実寸が 54 になる
    # (画像+内部余白)ため、文字だけの未登録U枠・edit も同じ幅に揃える
    PAT_BTN_W = 54

    def __init__(self, master, spec: dict, host=None, owner=None, row=None):
        super().__init__(master)
        # host: 本編を止めて初期音量をもらうための相手(main.RVPApp)。None可。
        self.host = host
        # =170: owner=ScenarioEditor(上書き警告の走査・保存先の既定に使う)。
        # row=このレビューの元になった ItemRow(新規保存でトラックへ紐づける
        # 相手)。どちらも None 可(テストから直接生成する経路)。
        self.owner = owner
        self._row = row
        self._row_audio_rel = getattr(row, "audio_rel", None)
        self.spec: dict = {}
        self._sound = None            # 区間切り出し済みのSound(シークの原本)
        self._segments: list = []
        self._duration_ms = 0
        self._clock = None
        self._playing = False
        self._dragging = False
        self._tick_job = None
        # =229: 表示の更新間隔(設定 graph_fps)と、直前フレームの描画コスト
        self.refresh_ms = self._load_refresh_ms()
        self._draw_cost_ms = 0.0
        self._time_text = None       # =229: 時刻ラベルの差分ガード
        self._seek_step = -1         # =229: シークバーの差分ガード(1/1000)
        self._last_draw = None
        self._volume = 1.0
        self._placed = False
        # =203: 動画レビュー(mpv 単独再生+同期)
        self._video = ""              # 動画ファイル(空=音声/スクリプトのみ)
        self._video_lo = 0.0          # 区間の開始(ms)
        self._video_hi = None         # 区間の終了(ms|None)
        self._video_len = 0.0         # mpv 実測の全長(ms。0=未取得)
        self._script_ms = 0           # スクリプトの最終点(レビュー用)
        self._mpv = None              # _EditorMpv(エディタと共有)
        self._mpv_cmd_t = 0.0         # 自分が出したコマンドの時刻(採用ガード)
        # =170: スクリプト編集モードの状態
        self.edit_mode = False
        self._edit_built = False
        # =232: 5列csv(UFO TW)は**左右2本を同時に編集**するので、モデルと
        # グラフはリストで持つ。`edit_model` / `edit_graph` は
        # **いま触っている方**を返すプロパティ(既存の呼び出しはそのまま動く)。
        self._edit_models: list = []   # script_edit.ScriptEditModel(遅延生成)
        self._edit_graphs: list = []
        self._edit_active = 0         # 0=上(左) / 1=下(右)
        self._edit_pair = False       # 左右2本を同時に編集しているか
        self._edit_tracks: list = []  # 編集対象候補 [(type, path, lo, hi)]
        self._edit_index = -1         # 選択中の候補(新規= -1)
        self._edit_new = False        # True=空のグラフから新規作成
        self._edit_path = ""          # 上書き保存先(新規= "")
        self._edit_extra = None       # 元funscriptの actions 以外のキー保持用
        self._edit_type = "linear"    # 編集中のトラック種別
        # =224: 編集中のファイルの種類。"funscript" / "csv"(ROTATE)
        self._edit_kind = "funscript"
        self._edit_cols = 0           # csv の列数(3 / 5)
        self._edit_csv_ch = 0         # csv の編集中チャンネル(0=左 / 1=右)
        self._edit_csv_other = []     # 5列csv のもう一方(触らずに保持する)
        self._csv_pat_warned = False  # csv 保存時のパターン記憶の案内は1回
        self._edit_sound = None       # 区間を無視した素材全体の Sound
        # =243: 音声波形。素材全体のSoundからエンベロープ(10msバケットの
        # ピーク列)を別スレッドで作り、after のポーリングで受け取る。
        self._wave_src = None         # (path, 素材全体のSound)
        self._wave_env = None         # 計算済みエンベロープ(パスでキャッシュ)
        self._wave_env_path = None
        self._wave_gen = 0            # 読み直しで古い結果を捨てる世代番号
        self._wave_result = None      # スレッド→UIの受け渡し(gen,path,env)
        self._wave_poll_job = None

        self.title(tr("アイテムのレビュー - RVP"))
        self.minsize(560, 220)
        # =181: 表示位置・大きさを記憶する(=115のWindowMemory)。
        # 復元できたら以後の自動リサイズは「足りない分を広げるだけ」にし、
        # ユーザーの選んだ大きさを縮めない。
        self.winmem = WindowMemory(self, "review")
        self._user_geom = self.winmem.restore()
        if self._user_geom:
            self._placed = True
        self.winmem.watch()
        self.winmem.install_close_hook()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        # 編集画面を閉じるなど、こちらを経由せず破棄されても音を止める
        self.bind("<Destroy>", self._on_destroy)
        self.load(spec)
        _front_window(self)

    # =232: いま触っているモデル / グラフ(左右同時編集の入口)
    @property
    def edit_model(self):
        ms = self._edit_models
        return ms[min(self._edit_active, len(ms) - 1)] if ms else None

    @property
    def edit_graph(self):
        gs = self._edit_graphs
        return gs[min(self._edit_active, len(gs) - 1)] if gs else None

    @property
    def edit_models(self) -> list:
        """いま編集中のモデル(1本 or 左右2本)。"""
        return self._edit_models[:2] if self._edit_pair \
            else self._edit_models[:1]

    @property
    def edit_graphs(self) -> list:
        """いま編集中のグラフ(1本 or 左右2本)。"""
        return self._edit_graphs[:2] if self._edit_pair \
            else self._edit_graphs[:1]

    def _set_active_graph(self, i: int):
        """=232: 触ったグラフをアクティブにして、枠の強調を更新する。"""
        if not self._edit_pair:
            i = 0
        if i == self._edit_active and self._edit_graphs:
            return
        self._edit_active = max(0, min(i, len(self._edit_graphs) - 1))
        for j, g in enumerate(self._edit_graphs):
            g.set_active(j == self._edit_active, peer_mode=self._edit_pair)
        self._edit_on_select()

    def _build_ui(self):
        from ..main import DeviceGraph, FixedBtn   # 遅延import(循環を避ける)

        self.name_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w", justify="left")
        self.name_label.pack(fill="x", padx=14, pady=(12, 0))
        self.info_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            anchor="w", justify="left", wraplength=self.WIN_W - 40)
        self.info_label.pack(fill="x", padx=14, pady=(2, 0))
        self.warn_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11), text_color=MSG_WARN,
            anchor="w", justify="left", wraplength=self.WIN_W - 40)

        # ---- 操作バー(再生タブと同じ並び) ----
        card = ctk.CTkFrame(self, corner_radius=10, fg_color=BOX_BG,
                            border_width=1, border_color=BOX_BORDER)
        card.pack(fill="x", padx=14, pady=(8, 0))
        self.transport_card = card
        tp = ctk.CTkFrame(card, fg_color="transparent")
        tp.pack(fill="x", padx=14, pady=(8, 10))
        self.time_label = ctk.CTkLabel(
            tp, text="00:00.0 / 00:00.0", font=ctk.CTkFont(size=12),
            anchor="w")
        self.time_label.pack(anchor="w")
        self.seek_slider = ctk.CTkSlider(
            tp, from_=0, to=1000, number_of_steps=1000, height=18,
            progress_color=_clr.ACCENT, button_color=_clr.ACCENT,
            button_hover_color=_clr.ACCENT_HOVER)
        self.seek_slider.set(0)
        self.seek_slider.pack(fill="x", pady=(2, 0))
        self.seek_slider.bind("<Button-1>", self._on_seek_press)
        self.seek_slider.bind("<ButtonRelease-1>", self._on_seek_release)

        row = ctk.CTkFrame(tp, fg_color="transparent")
        row.pack(pady=(8, 0))
        self.transport_row = row

        # =229: 幅が文字で変わらない FixedBtn(再生タブと同じ対処)。
        # 幅は**高さより広い錠剤形**(角丸=高さの半分)になる値にする
        # (=229 FB1: 正円ではなく従来の横長のままがよい)。
        def small_btn(text, command, width=52):
            return FixedBtn(
                row, text=text, width=width, height=44, corner_radius=22,
                font=ctk.CTkFont(size=13), fg_color="transparent",
                border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"), command=command)

        self.btn_back10 = small_btn("↺10", self.seek_back10, width=72)
        self.btn_back10.pack(side="left", padx=6)
        # =229: 「▶」⇔「❚❚」で幅が変わらないよう FixedBtn。
        # 幅 74 × 高さ 56(角丸28)= **横長の錠剤形**(=229 FB1)
        self.btn_play = FixedBtn(
            row, text="▶", width=74, height=56, corner_radius=28,
            font=ctk.CTkFont(size=20), fg_color=_clr.ACCENT,
            hover_color=_clr.ACCENT_HOVER, command=self.toggle_play)
        self.btn_play.pack(side="left", padx=6)
        self.btn_fwd10 = small_btn("↻10", self.seek_fwd10, width=72)
        self.btn_fwd10.pack(side="left", padx=6)

        vol_box = ctk.CTkFrame(row, fg_color="transparent")
        vol_box.pack(side="left", padx=(14, 0))
        self.vol_box = vol_box        # =203: 動画モードでは隠す
        ctk.CTkLabel(vol_box, text="🔊", font=ctk.CTkFont(size=14)
                     ).pack(side="left", padx=(0, 4))
        self.volume_slider = ctk.CTkSlider(
            vol_box, from_=0, to=100, number_of_steps=100, width=104,
            height=16, progress_color=_clr.ACCENT, button_color=_clr.ACCENT,
            button_hover_color=_clr.ACCENT_HOVER, command=self._on_volume_change)
        self.volume_slider.set(100)
        self.volume_slider.pack(side="left")
        # ---- =214: 再生速度(音量の右。動画モードでも出す=mpv の speed) ----
        self._speed = 1.0
        self._rate_cache = None       # (id(sound), rate, Sound)
        self.speed_box = ctk.CTkFrame(row, fg_color="transparent")
        self.speed_box.pack(side="left", padx=(14, 0))
        ctk.CTkLabel(self.speed_box, text=tr("速度:"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=(0, 4))
        self._speed_map = {self._speed_label(v): v for v in SPEED_CHOICES}
        self.speed_var = tk.StringVar(value=self._speed_label(1.0))
        self.speed_menu = CTkOptionMenu(
            self.speed_box, variable=self.speed_var, width=74, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._speed_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_speed_change())
        self.speed_menu.pack(side="left")
        Tooltip(self.speed_menu,
                lambda: tr("再生速度（音声は音の高さも変わります。"
                           "時間補正は素材の時間のまま）"))

        # =231: **スペースキー=再生/一時停止**(レビュー状態でも編集モード
        # でも効く)。at/pos などの入力欄にカーソルがあるときは普通に空白を
        # 入れる(=223 の数字キーと同じ除外)。
        self.bind("<KeyPress-space>", self._on_space_key, add="+")

        # ---- グラフ(スクリプトがあるときだけ出す) ----
        self.graph_box = ctk.CTkFrame(self, corner_radius=10, fg_color=BOX_BG,
                                      border_width=1, border_color=BOX_BORDER)
        gp = ctk.CTkFrame(self.graph_box, fg_color="transparent")
        gp.pack(fill="both", expand=True, padx=10, pady=(8, 6))
        # =243: 音声波形の表示モード(グラフの上のコントロール列。
        # 編集モードのコンボと同じ設定 audio_wave_mode を共有する)
        wrow = ctk.CTkFrame(gp, fg_color="transparent")
        wrow.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(wrow, text=tr("音声波形:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self._wave_labels = {"off": "OFF", "mono": tr("モノラル"),
                             "stereo": tr("ステレオ"),
                             "stereo_rev": tr("ステレオ(逆)")}
        self._wave_mode_key = self._load_wave_mode()
        self.wave_var = tk.StringVar(
            value=self._wave_labels[self._wave_mode_key])
        self.wave_menu = CTkOptionMenu(
            wrow, variable=self.wave_var, width=110, height=24,
            font=ctk.CTkFont(size=11),
            values=[self._wave_labels[k] for k in WAVE_MODE_KEYS],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=self._on_wave_mode)
        self.wave_menu.pack(side="left", padx=(4, 0))
        Tooltip(self.wave_menu,
                lambda: tr("音声の振幅(音量の形)をグラフの背景へ薄く表示"
                           "します（ステレオ=上がL・下がR。動画のみの"
                           "アイテムでは表示されません）"))
        # 1枚表示・マイナス表示は出さない(個別表示だけでよい=ユーザー決定)
        self.graph_view = DeviceGraph(gp)
        # =237: 右ダブルクリック=再生位置をそこへ(編集画面=192の横展開)。
        # レビュー画面のグラフの時間軸は再生位置そのものなので、
        # 受け取った ms をそのまま seek へ渡せる。
        self.graph_view.on_seek = self.seek
        self.graph_view.pack(fill="both", expand=True)
        ctk.CTkLabel(
            gp,
            text=tr("ホイール=時間の拡大縮小 ／ ドラッグ=前後を見る ／ "
                    "クリック=再生位置へ戻る ／ 右ダブルクリック=再生位置をここへ"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w",
        ).pack(fill="x", pady=(4, 0))

        # =219: ボタン行は **side="bottom" で最初に場所を確保**する。
        # pack は「先に詰めた子から順に場所を配る」ので、最後に詰めていた
        # 従来の順序だと、ウィンドウを縦に縮めたときに余りが尽きて
        # **ボタン自身の高さが押し潰される**(30px → 13px。ユーザー報告)。
        # 先にここを確保しておけば、縮むのは上のグラフ枠(expand側)だけに
        # なり、「閉じる」はどこまで縮めても押せる高さのまま残る。
        # このため graph_box / edit_wrap の pack から `before=` を外した
        # (縦位置は side="bottom" が保証するので順序に頼らなくてよい)。
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(side="bottom", fill="x", padx=14, pady=(8, 12))
        self.btn_row = btns
        ctk.CTkButton(btns, text=tr("閉じる"), width=90, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.close).pack(side="right")
        # =170: スクリプト編集モードへの切り替え(レビュー画面の中で切り替える。
        # 別ウィンドウにしない=仕様 2.1)。編集中は「レビューに戻る」になる。
        self.edit_btn = ctk.CTkButton(
            btns, text=tr("スクリプト編集"), width=120, height=30,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._toggle_edit_mode)
        self.edit_btn.pack(side="left")

    def load(self, spec: dict, row=None, autoplay: bool = True) -> bool:
        """レビュー対象を差し替える(=164。ウィンドウは作り直さない)。

        =170: 編集モード中に別アイテムのレビューを開こうとしたときは
        未保存確認(保存する/破棄する/やめる)を挟む。「やめる」なら中身を
        変えずに False を返す。row は新規保存でトラックを紐づける相手。
        """
        # =229: 表示の更新間隔(設定 graph_fps)を読み直す
        self.refresh_ms = self._load_refresh_ms()
        self._time_text = None       # 差分ガードを捨てる(中身が変わるので)
        self._seek_step = -1
        if self.edit_mode:
            if not self._resolve_unsaved():
                return False
            self._leave_edit_ui()
        if row is not None:
            self._row = row
            self._row_audio_rel = getattr(row, "audio_rel", None)
        self._stop_audio()
        self._cancel_tick()
        self._playing = False
        self.spec = dict(spec or {})
        warn = list(self.spec.get("warn") or [])
        # =252: デバイス連動OFFのアイテムは音声試聴のみ(AQ7)。
        # スクリプト編集への導線を隠す(tracksは空で渡ってくる)。
        if self.spec.get("device_off"):
            self.edit_btn.pack_forget()
        elif not self.edit_btn.winfo_manager():
            self.edit_btn.pack(side="left")

        # 本編の再生を止め、初期音量をもらう(音が重なるのを防ぐ)
        vol = self._pause_main()
        if vol is not None:
            self._volume = max(0.0, min(1.0, float(vol)))
            self.volume_slider.set(int(round(self._volume * 100)))

        # ---- 音声 ----
        self._sound = None
        self._wave_src = None            # =243: 波形の元(音声が無ければ無し)
        duration = 0
        audio = self.spec.get("audio") or ""
        if audio:
            snd, err = self._load_audio(audio, self.spec.get("audio_lo") or 0.0,
                                        self.spec.get("audio_hi"))
            if err:
                warn.append(err)
            else:
                self._sound = snd
                duration = int(snd.get_length() * 1000)

        # ---- 動画(=203: mpv 単独再生) ----
        self._video = self.spec.get("video") or ""
        self._video_lo = float(self.spec.get("audio_lo") or 0.0)
        self._video_hi = self.spec.get("audio_hi")
        self._video_len = 0.0
        if self._video:
            # 実長は mpv がロードするまで不明。区間の終了があれば暫定全長に
            # する(実長は _video_tick で反映)
            if self._video_hi is not None:
                duration = max(duration,
                               int(max(0.0, self._video_hi - self._video_lo)))
            err = self._video_open(self._video_lo)
            if err:
                warn.append(err)
        self._refresh_vol_visibility()

        # ---- スクリプト(funscript / csv) ----
        self._segments, script_ms, errs = self._load_tracks(
            self.spec.get("tracks") or [])
        warn.extend(errs)
        self._script_ms = script_ms
        self._duration_ms = max(duration, script_ms)
        # 波形を素材の終わりで切る。rotate/vibration は「次の指示まで同じ値」
        # なので、右端(x1)を決めないと画面の右端まで伸び続けて、どこで
        # 終わるのか分からなくなる(再生タブでは「次の断片の開始位置」が
        # 右端になる。レビューは1アイテムだけなので終端をそこに使う)。
        for seg in self._segments:
            seg["x1"] = float(self._duration_ms)

        self.name_label.configure(text=self.spec.get("name") or "")
        self.info_label.configure(text=self._info_text())
        if warn:
            self.warn_label.configure(text="\n".join(warn))
            self.warn_label.pack(fill="x", padx=14, pady=(4, 0),
                                 before=self.transport_card)
        else:
            self.warn_label.pack_forget()

        self._apply_graph_layout()
        self._start_wave_env()           # =243: 音声波形(別スレッドで計算)
        self._reset_clock()
        self._update_time(0.0, force=True)
        self._schedule_tick()
        # ユーザー決定: 開いたら自動的に再生する(=170: 編集モードから
        # レビューへ戻ったときは自動再生しない)
        if autoplay and self._duration_ms > 0 and not self._video:
            self.play()
        else:
            self._sync_buttons()
        return True

    def _load_audio(self, path: str, lo_ms: float, hi_ms):
        """音声を読み込み、区間(=59)を切り出す。戻り値 (Sound|None, エラー文言)。"""
        if not os.path.isfile(path):
            return None, tr("音声ファイルが見つかりません: {0}").format(
                os.path.basename(path))
        try:
            import pygame
            from ..player import ScenarioPlayer as _SP
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            if pygame.mixer.get_num_channels() <= REVIEW_SLOT:
                pygame.mixer.set_num_channels(REVIEW_SLOT + 1)
            snd = pygame.mixer.Sound(path)
            # =243: 音声波形のエンベロープは**素材全体**から作る(区間の
            # 切り出し前を控える。レビュー参照モードはオフセットで合わせ、
            # 編集モードは素材時間そのままなので 0)。
            self._wave_src = (path, snd)
            if lo_ms > 0 or hi_ms is not None:
                snd = _SP._sliced_sound(snd, lo_ms, hi_ms)
            if snd is None:
                raise RuntimeError("mixer unavailable")
        except Exception as e:
            # =249: メモリ系の失敗は原因と対処(44.1kHzへの変換など)を添える
            from ..player import describe_audio_load_error
            extra = describe_audio_load_error(path, e)
            return None, (tr("音声を再生できません: {0}").format(e)
                          + (("\n" + extra) if extra else ""))
        return snd, ""

    CFG_WAVE_MODE = "audio_wave_mode"

    def _load_wave_mode(self) -> str:
        try:
            v = str(load_config().get(self.CFG_WAVE_MODE, "off"))
        except Exception:
            v = "off"
        return v if v in WAVE_MODE_KEYS else "off"

    def _on_wave_mode(self, label: str):
        """コンボの操作(参照モード・編集モードのどちらから来ても同じ)。"""
        key = next((k for k, v in self._wave_labels.items() if v == label),
                   "off")
        self._wave_mode_key = key
        try:
            cfg = load_config()
            if cfg.get(self.CFG_WAVE_MODE) != key:
                cfg[self.CFG_WAVE_MODE] = key
                save_config(cfg)
        except Exception:
            pass
        self._push_wave_mode()

    def _push_wave_mode(self):
        """表示モードを両方のコンボと全グラフへ反映して描き直す。"""
        label = self._wave_labels[self._wave_mode_key]
        if self.wave_var.get() != label:
            self.wave_var.set(label)
        if getattr(self, "edit_wave_var", None) is not None and \
                self.edit_wave_var.get() != label:
            self.edit_wave_var.set(label)
        gv = self.graph_view
        gv.wave_mode = self._wave_mode_key
        gv._redraw()
        if self._edit_built:
            for gi, g in enumerate(self._edit_graphs):
                g.wave_mode = self._wave_mode_key
                g.wave_channel = gi if self._edit_pair else None
            self.edit_sub_graph.wave_mode = self._wave_mode_key
            self.edit_sub_graph.wave_channel = None
            for g in self._edit_graphs + [self.edit_sub_graph]:
                # 非表示のグラフは描かない(redraw の _sync_mirror が
                # 古い表示範囲をアクティブ側へ押し戻してしまうため)
                try:
                    if g.winfo_ismapped():
                        g.redraw()
                except Exception:
                    pass

    def _start_wave_env(self):
        """エンベロープの計算を始める(=243)。同じファイルはキャッシュを
        使い、無ければ別スレッドで計算して after のポーリングで受け取る
        (Tk はメインスレッドからしか触らない)。"""
        if self._wave_poll_job is not None:
            try:
                self.after_cancel(self._wave_poll_job)
            except Exception:
                pass
            self._wave_poll_job = None
        self._wave_gen += 1
        self._wave_result = None
        src = self._wave_src
        if not src:
            self._apply_wave_env(None)
            return
        path, snd = src
        if path == self._wave_env_path and self._wave_env:
            self._apply_wave_env(self._wave_env)
            return
        self._apply_wave_env(None)       # 計算が終わるまで波形なしで表示
        try:
            import pygame
            init = pygame.mixer.get_init()
        except Exception:
            init = None
        if not init:
            return
        freq, fmt, chs = init
        gen = self._wave_gen

        def work():
            try:
                # =249: get_raw()の全複製をやめ、ゼロコピーのビューから計算
                from ..player import sound_byte_view
                env = compute_wave_env(sound_byte_view(snd), freq, fmt, chs)
            except Exception:
                env = None
            self._wave_result = (gen, path, env)   # 属性書き込みのみ

        threading.Thread(target=work, daemon=True,
                         name="rvp-wave-env").start()
        self._wave_poll_job = self.after(200, self._wave_poll)

    def _wave_poll(self):
        self._wave_poll_job = None
        if not self.winfo_exists():
            return
        res = self._wave_result
        if res is None:
            self._wave_poll_job = self.after(200, self._wave_poll)
            return
        self._wave_result = None
        gen, path, env = res
        if gen != self._wave_gen:
            return                       # 読み直し後の古い結果は捨てる
        if env is not None:
            self._wave_env, self._wave_env_path = env, path
        self._apply_wave_env(env)

    def _apply_wave_env(self, env):
        """エンベロープを全グラフへ配って描き直す。"""
        gv = self.graph_view
        gv.wave_env = env
        gv.wave_offset = float(self.spec.get("audio_lo") or 0.0)
        if self._edit_built:
            for g in self._edit_graphs + [self.edit_sub_graph]:
                g.wave_env = env
                g.wave_offset = 0.0
        self._push_wave_mode()

    def _load_tracks(self, tracks) -> tuple:
        """トラックを読み込み、(グラフ断片, 最長の長さms, エラー文言) を返す。"""
        segs, longest, errs = [], 0, []
        for ttype, path, lo, hi in tracks:
            if not os.path.isfile(path):
                errs.append(tr("スクリプトが見つかりません: {0}").format(
                    os.path.basename(path)))
                continue
            try:
                if ttype in REVIEW_ROTATE_TYPES:
                    src = load_rotate_source(path)
                else:
                    src = Funscript.load(path)
                if lo > 0 or hi is not None:
                    src = src.sliced(lo, hi)
            except Exception as e:
                errs.append(tr("スクリプトを読み込めません({0}): {1}").format(
                    os.path.basename(path), e))
                continue
            longest = max(longest, int(src.duration_ms))
            segs.extend(_review_segments(ttype, src))
        return segs, longest, errs

    def _info_text(self) -> str:
        """区間とトラックの一覧を1行にまとめる。"""
        parts = []
        lo = float(self.spec.get("audio_lo") or 0.0)
        hi = self.spec.get("audio_hi")
        if lo > 0 or hi is not None:
            parts.append(tr("区間 {0}").format(self._range_text(lo, hi)))
        for ttype, path, t_lo, t_hi in self.spec.get("tracks") or []:
            txt = "{0}={1}".format(ttype, os.path.basename(path))
            if (t_lo, t_hi) != (lo, hi):
                txt += "({0})".format(self._range_text(t_lo, t_hi))
            parts.append(txt)
        if not parts:
            parts.append(tr("(区間指定なし)"))
        return "  /  ".join(parts)

    @staticmethod
    def _range_text(lo_ms: float, hi_ms) -> str:
        start = tr("先頭") if not lo_ms else "{0:g}".format(lo_ms / 1000.0)
        end = tr("末尾") if hi_ms is None else "{0:g}".format(hi_ms / 1000.0)
        return tr("{0}〜{1}").format(start, end)

    def _graph_rows(self) -> int:
        """グラフの行数(=表示する種別の数。2ch csv は左右で2行)。"""
        return len({s["key"] for s in self._segments})

    def _to_logical(self, px: int) -> int:
        """実ピクセル → CTkの論理ピクセル(=157の高DPIの罠への対処)。

        `winfo_reqheight()` は実ピクセル、`geometry()` は論理ピクセルなので、
        割り戻さずに渡すと125%スケールで二重に拡大される。
        """
        try:
            return int(round(self._reverse_window_scaling(px)))
        except Exception:
            return int(px)

    def _apply_graph_layout(self):
        """グラフの有無・行数に合わせて枠とウィンドウの高さを決める。

        高さは**実際に必要な高さ(reqheight)を測って**決める。注意書きの
        行数や言語(日本語/英語で折り返しが変わる)で変動するため、
        固定値だと下端の「閉じる」が切れることがある。
        """
        rows = self._graph_rows()
        if rows:
            # 1本あたりの高さは再生タブと同じ固定値。上限を超えたぶんは
            # 縦ドラッグで送って見る(DeviceGraph の作法をそのまま使う)。
            self.graph_view.configure(
                height=self.GRAPH_ROW_H * min(rows, self.GRAPH_ROWS_MAX)
                + self.GRAPH_AXIS_H)
            if not self.graph_box.winfo_ismapped():
                # =219: 「閉じる」の行は side="bottom" で先に確保済みなので、
                # ここは普通に(=順序としては後ろへ)詰めてよい。縦位置は
                # side が決めるので `before=` は不要=むしろ付けると
                # ボタン行より先に場所を取ってしまい、潰れが再発する。
                self.graph_box.pack(fill="both", expand=True, padx=14,
                                    pady=(8, 0))
            self.graph_view.follow = True
            self.graph_view.view_y = 0.0
        else:
            self.graph_box.pack_forget()
        try:
            self.update_idletasks()
        except Exception:
            pass
        win_h = max(self.MIN_WIN_H, self._to_logical(self.winfo_reqheight()))
        if self._user_geom:
            # =181: 記憶した大きさを尊重。内容が収まらないときだけ広げる
            cw = self._to_logical(self.winfo_width())
            ch = self._to_logical(self.winfo_height())
            if cw > 1 and ch > 1 and (cw < self.WIN_W or ch < win_h):
                self.geometry("{0}x{1}".format(max(cw, self.WIN_W),
                                               max(ch, win_h)))
        else:
            self.geometry("{0}x{1}".format(self.WIN_W, win_h))
        if not self._placed:
            self._placed = True
            _place_popup(self, self.master, self.WIN_W, win_h)

    def _pause_main(self):
        """本編が再生中なら止める。戻り値=初期音量(0.0〜1.0)or None。"""
        fn = getattr(self.host, "pause_for_review", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:
            return None

    def refresh_theme(self):
        """=151: メイン画面のテーマ切替に追従する(グラフは自前描画)。"""
        try:
            self.graph_view._redraw()
        except Exception:
            pass
        if self._edit_built:
            try:
                self.edit_graph.redraw()
            except Exception:
                pass
            try:
                self._refresh_fkey_badges()       # =212: バッジの色
            except Exception:
                pass

    def _on_destroy(self, event=None):
        # 子ウィジェットの破棄でも飛んでくるので自分自身のときだけ処理する
        if event is not None and event.widget is not self:
            return
        self._save_edit_height()          # =211(エディタごと閉じた場合)
        if self._edit_built:
            self._chain_stop()            # =222
        # =203: 動画は一時停止して残す(mpv はエディタが生きている間は
        # プロセス維持=合意事項3と同型。エディタを閉じると quit される)
        if self._video and self._mpv is not None:
            try:
                self._mpv.set_pause(True)
            except Exception:
                pass
        self._cancel_tick()
        self._stop_audio()

    def front(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_set()
        except Exception:
            pass

    def close(self):
        # =170: 未保存の編集がある状態で閉じるときは確認を出す(仕様 14b)。
        # 「やめる」なら閉じない。
        if self.edit_mode and not self._resolve_unsaved():
            return
        self._save_edit_height()          # =211
        try:
            self.winmem.save_now()        # =181 の記憶(閉じる直前の配置)
        except Exception:
            pass
        self._cancel_tick()
        self._stop_audio()
        self.destroy()

    # =242: 0.01秒刻み・0.00〜-1.00。config("time_adjust_sec")へ保存する。
    CFG_TIME_ADJ = "time_adjust_sec"

    TIME_ADJ_REPEAT_FIRST = 400     # 長押しの初回ディレイ(ms)

    TIME_ADJ_REPEAT_MS = 80         # 以降の連続増減の間隔(ms)

    CHAIN_POLL_MS = 30        # 終端に来たかを見る間隔

    FKEY_RELEASE_MS = 60      # X11 の自動リピート(Release/Press連打)対策
