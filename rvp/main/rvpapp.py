"""メイン画面本体(RVPApp: __init__ と _build_ui)。"""
from __future__ import annotations

import customtkinter as ctk
import pygame
from ._hooks import DND_FILES
from ..intiface_client import IntifaceClient
from ..scenario import Scenario, TRACK_ROTATE_A10
from ..player import ScenarioPlayer
from ..tcode_client import TCodeClient
from ..winstate import WindowMemory
from .. import apptheme
from ..i18n import tr

from .app_config import _RVPAppConfigMixin
from .app_header import _RVPAppHeaderMixin
from .app_interact import _RVPAppInteractMixin
from .app_play_controls import _RVPAppPlayControlsMixin
from .app_play_pages import _RVPAppPlayPagesMixin
from .app_poll import _RVPAppPollMixin
from .app_tab_connection import _RVPAppTabConnectionMixin
from .app_tab_play import _RVPAppTabPlayMixin
from .app_tab_scenario import _RVPAppTabScenarioMixin
from .background_art import BackgroundArt
from .common import (AsyncRunner, DEFAULT_INTIFACE_URL, ERROR_TEXT, MUTED,
    OK_TEXT, WARN_TEXT, logger)
from .startup import _startup_mark
from .widgets import TabView
from ._hooks import _pkg


class RVPApp(_RVPAppHeaderMixin, _RVPAppTabConnectionMixin, _RVPAppConfigMixin, _RVPAppTabScenarioMixin, _RVPAppTabPlayMixin, _RVPAppPlayPagesMixin, _RVPAppPlayControlsMixin, _RVPAppInteractMixin, _RVPAppPollMixin):

    def __init__(self, root: ctk.CTk):
        self.root = root
        root.title("RVP - Random Voice Player")
        # =57: 1600x900 のデスクトップでも収まるよう高さ985→820へ
        # (ユーザー要望)。最小高もそれに合わせて下げる。
        # =159: 最小の高さは 700 → **790**(ユーザー指定)。700では①再生ページの
        # 「再生中のチャンネル」の R行が潰れて文字が欠け、その下の
        # 「★=シークバーが追従しているチャンネル」が消えていた。
        root.minsize(580, self._min_win_h())
        # 幅690: ◀▶切替ボタンの分デバイス領域が狭まり文字が隠れたため+50px
        # (2026-07-22ユーザー要望)
        # =115: 前回終了時のサイズ・位置を復元する(マルチディスプレイ対応)。
        # 保存が無い/保存位置がどのモニタにも載っていない場合だけ既定配置。
        self.winmem = WindowMemory(root, "main")
        if not self.winmem.restore():
            self._place_window(690, 820)
        self.winmem.watch()
        _startup_mark("RVPApp: ウィンドウ配置")

        pygame.mixer.init(channels=2)  # ステレオ(パン制御に必要)
        _startup_mark("RVPApp: pygame.mixer.init")

        self.runner = AsyncRunner()
        self.intiface = IntifaceClient(DEFAULT_INTIFACE_URL)
        self.tcode = TCodeClient()   # =80 TCode直接出力(シリアル)
        self.player = ScenarioPlayer(self.intiface)
        self.player.tcode = self.tcode
        self.scenario: Scenario | None = None
        # 選択肢UIの状態(再生タブ)
        self._scenario_has_choices = False   # 読込中シナリオに選択肢イベントがあるか
        # =150: 読込中シナリオがデバイストラック(funscript/csv)を持つか。
        # False かつ シナリオ読込済みのとき、再生タブは3画面構成になる。
        # 未読込(scenario is None)のときは判定できないので5画面のまま。
        self._scenario_has_device = True
        self._choice_display = None           # None / "hidden" / "placeholder" / "active"
        self._video_front = False             # 動画中カード表示でtopmost化しているか
        self._autoselect_after = None         # 自動選択タイマーの after id
        self._autoselect_sig = None           # スケジュール時の選択肢シグネチャ
        self._last_dir: str | None = None   # 前回シナリオを開いたフォルダ
        self.appearance_mode = "dark"       # 外観テーマ(dark/light)。ボタンで即時切替
        # =134: パステルカラーテーマ名(""=なし)。適用自体は main() が
        # 起動時に行う(反映は再起動後)。ここは設定メニューと保存の状態だけ。
        self.color_theme = apptheme.ACTIVE or ""
        # 自動再接続の状態管理
        self._want_connected = False   # 一度接続に成功したか(ピル文言の出し分け用)
        self._reconnect_fut = None     # 実行中の接続 future(None=非実行)
        self._probe_fut = None         # 実行中のポートプローブ future(=78)
        self._reconnect_after = 0.0    # 次の自動接続試行を許可する時刻(monotonic)
        self._manual_connecting = False  # 手動「接続」実行中は自動接続を止める(=78)
        # =272: 「接続中デバイス」欄の描画済みスナップショット
        # (intiface接続有無, デバイス名tuple, has_linear, COM接続有無)。
        # 接続状況が変わるたびに欄を描き直し、古い表示が残らないようにする。
        # None=未描画(次の更新で必ず描く)。エラー文を出した直後は
        # 「現在のスナップショット」を控えて、状態が変わるまで上書きしない。
        self._device_box_snap = None
        _startup_mark("RVPApp: クライアント類の生成")

        self._build_ui()
        _startup_mark("RVPApp: _build_ui 完了")
        # =157: タブとヘッダーを同じ帯に置けるだけの幅を確保する
        # (足りないと =156までの2段レイアウトへ自動で戻ってしまう)。
        self._ensure_header_width()
        self._apply_saved_config()
        _startup_mark("RVPApp: _apply_saved_config 完了")
        # 起動時に前回シナリオ(履歴先頭)を自動で開く(=43 案1)。
        # =81: 直接呼ばず**最初の描画が済んでから**開く(after遅延)。
        # ウィンドウ実体化前に _load_scenario の大量のUI更新(再生タブ遷移
        # 含む)を走らせると、Windows の customtkinter(font_shapes描画)が
        # RecursionError の嵐で起動不能になる実機事例が出たため
        # (2026-08-06 ユーザー報告。履歴あり=自動オープン時のみ発生)。
        # 遅延により手動オープンと同じタイミング条件になる。
        self.root.after(150, self._auto_open_last_scenario)
        # スペースキーで再生/一時停止(=43 案2)。メインウィンドウ内のみ
        # (編集画面は別Toplevelなので影響しない)
        self.root.bind("<space>", self._on_space_key)
        self._poll_state()
        self._animate_linear()
        self._animate_graph()
        _startup_mark("RVPApp: __init__ 完了")

    # 初期表示位置: 画面左上ぴったり(=58 ユーザー決定。=57では20pxだった)。
    # 以前は「横中央・上から16px」だったが、1600x900のような狭い画面では
    # 左上に寄せた方が他のウィンドウと並べやすい。
    WIN_MARGIN = 0

    def _build_ui(self):
        self._build_header()

        # ===== タブ =====
        self.tabs = TabView(self.root)
        # =89: 下paddingを18→4へ(再生タブ下部のバーランプの高さを、ページの
        # 表示域を削らずにここから捻出する)
        # =157: ヘッダーを place で浮かせたので、タブ行は**ウィンドウ上端から
        # TABS_TOP(28px)** の位置から始める(=156までは pack されたヘッダー行の
        # 下=56px だった)。差の28pxはそのまま各タブの表示領域になる。
        self._tabs_compact = None       # =157: 現在の配置(None=未確定)
        self.tabs.pack(fill="both", expand=True, padx=20,
                       pady=(self.TABS_TOP, 4))

        self.tab_conn = self.tabs.add(tr("接続"))
        self.tab_scenario = self.tabs.add(tr("シナリオ"))
        self.tab_play = self.tabs.add(tr("再生"))
        # =155: タブの切替を拾う(再生タブへ移った時に履歴へ記録する)。
        # add() の中で最初の set() が走るため、**タブを作り終えてから**繋ぐ。
        self.tabs.on_change = self._on_tab_changed

        _startup_mark("build: ヘッダー+タブ枠")
        self._build_tab_connection(self.tab_conn)
        _startup_mark("build: 接続タブ")
        self._build_tab_scenario(self.tab_scenario)
        _startup_mark("build: シナリオタブ(履歴の先頭チャンク含む)")
        self._build_tab_play(self.tab_play)
        # =262/=263: 背景イラスト(表示は再生タブ表示中のみ=Q1)。
        # =263でウィンドウアルファ+アンダーレイ方式になり、基準はrootへ。
        self.bg_art = BackgroundArt(self, self.root)
        self.bg_art.set_user_enabled(self.show_bg_var.get())
        _startup_mark("build: 再生タブ")
        # 起動時の初期タブは「シナリオ」(=42 ユーザー要望)。シナリオを選んで
        # 再生する流れが基本のため。接続は自動再接続が背景で走るので
        # 接続タブを最初に見せる必要はない。
        self.tabs.set(tr("シナリオ"))

        # =157: ヘッダーは TabView より先に作られている=Tkの生成順では下に
        # なるので、ここで持ち上げる(タブ行の右側の空きに重ねて描く)。
        try:
            self.header.lift()
        except Exception:
            logger.exception("header lift failed")

        # =157: 幅が足りるかを判定して配置を決める。以後はウィンドウの
        # リサイズでも追従する(モードが変わったときだけ pack し直す)。
        self._apply_tabs_top()
        self.root.bind("<Configure>", self._on_root_configure, add="+")

        # =270: OSからのシナリオファイルD&D。**シナリオタブのフレーム**を
        # ドロップ先として登録する(tkdndは落下点直下から親方向へ登録済み
        # ウィジェットを探すため、タブ内のどこへ落としてもここで受かる)。
        # 注意: drop_target_register は tkinterdnd2 が tkinter.BaseWidget へ
        # 注入するメソッドなので、ルート(Tk/CTk)には存在しない=ルートへは
        # 登録できない。タブのフレームなら BaseWidget 由来で登録できる。
        # 編集画面(=44)と同じ流儀: 未導入・登録失敗は静かに無効。ハンドラ側
        # でも現在タブ=シナリオを確認する(タブ切替直後の取りこぼし保険)。
        self._dnd_ok = False
        if _pkg().TkinterDnD is not None:
            try:
                _pkg().TkinterDnD._require(self.tab_scenario)
                self.tab_scenario.drop_target_register(DND_FILES)
                self.tab_scenario.dnd_bind("<<Drop>>",
                                           self._on_scenario_dnd_drop)
                self._dnd_ok = True
            except Exception:
                self._dnd_ok = False

    # =159: ウィンドウの最小の高さ(論理ピクセル)。=57からの700では①再生ページの
    # チャンネル3行目(R)が潰れ、その下の★の説明行が消えていた(実機報告)。
    # 実測では日本語Windowsで770px前後から欠け始めるので、余裕をみて790。
    MIN_WIN_H = 790

    # 画面が低い端末(1366x768など)ではウィンドウが画面からはみ出してしまうので、
    # 作業領域に収まる範囲へ丸める。この場合は下端が切れるが、掴めないほど
    # 大きいウィンドウよりはまし(=115の fit_into_monitor と同じ考え方)。
    MIN_WIN_H_FLOOR = 620

    # =157: ヘッダー(ヘルプ/Settings/接続ピル)の配置。pack ではなく place で
    # **右上へ浮かせる**ので、タブ行はその下ではなく**同じ帯の左側**へ入れる。
    # HEADER_TOP  : ウィンドウ上端からヘッダーまでの余白(=156までと同じ18px)
    # HEADER_RIGHT: 右端からの余白(タブ枠の padx=20 と揃える)
    # TABS_TOP    : ウィンドウ上端からタブ行の上端まで(=156までは56px)
    # TABS_TOP_CLASSIC: 幅が足りないときの逃げ道(=156までと同じ「ヘッダー行の
    #              下にタブ」= HEADER_TOP + ヘッダー高28 + 余白10)
    # HEADER_GAP : タブの右端とヘッダーの間に最低限空ける隙間
    HEADER_TOP = 18

    HEADER_RIGHT = 20

    TABS_TOP = 28

    TABS_TOP_CLASSIC = 56

    HEADER_GAP = 8

    # =263: 透け具合の表示名(弱/中/強)と保存値(weak/mid/strong)の相互変換。
    _BG_ALPHA_LABELS = (("弱", "weak"), ("中", "mid"), ("強", "strong"))

    # ページ切替(◀▶)ボタンの幅。=60で縦スクロールバーを廃止した分を
    # ここへ回して押しやすくした(26→44)。
    PAGE_BTN_W = 44

    # ページID(巡回順)。=150でユーザー依頼により並び替えた。
    # 変更前(=149まで): play → device → graph → map → log
    # デバイス関連(device/graph)を**末尾へ寄せて**あるので、デバイス連動
    # なしのシナリオでは末尾2枚を落とすだけで3画面構成になる。
    PLAY_PAGE_ORDER = ("play", "map", "log", "device", "graph")

    # デバイス連動なしのシナリオで巡回から外すページ(=150)。
    DEVICE_PAGES = ("device", "graph")

    # ページ数の**上限**(=85。バーランプのセグメント生成に使う)。ランプの
    # 生成は _play_cards() の各カードより先に走るため定数で持つ。実際に
    # 表示中の数は len(self._play_pages)(=150で可変になった)。
    PLAY_PAGE_COUNT = len(PLAY_PAGE_ORDER)

    PAGE_LAMP_H = 14                 # バーの高さ(px)

    PAGE_LAMP_ON = "#9ccc3c"         # 点灯=scenario_map.CURRENT_FILL と同色

    PAGE_LAMP_OFF = "#1c1c1c"        # 消灯=ZoneBar.ACTIVE と同色

    WHEEL_STEP = 40      # ホイール1ノッチあたりの移動量(px)

    # =102/=103: グラフ表示モードの巡回順(overlay, minus)。
    # 個別 → 1枚 → 個別-(将来非表示) → 1枚-(将来非表示) → 個別 → …
    GRAPH_MODES = ((False, False), (True, False),
                   (False, True), (True, True))

    STATE_MAP_H = 140   # =287: 再生タブのステート図の高さ(図の実高さ130+余白)

    AUTO_CONNECT_INTERVAL = 5.0   # プローブ間隔・失敗後のバックオフ(秒)

    # =90: 上限10→100(ユーザー依頼5)。履歴は path/title/ts のメタ情報だけを
    # コンフィグに保存しており、**シナリオファイル本体は行クリック時にしか
    # 読み込まない**(依頼6の懸念=起動時に各シナリオを読む、は元々ない)。
    # 起動時の負荷は「行ウィジェットの生成」(100件で約300個のCTkウィジェット)
    # なので、_refresh_history_list をチャンク分割の遅延構築にして起動を
    # 止めないようにした(下記 HISTORY_CHUNK)。
    HISTORY_MAX = 100

    # 1チャンクで作る行数。先頭チャンクだけ同期で作り(画面には即座に
    # 表示される)、残りは after でバックグラウンド的に継ぎ足す。
    HISTORY_CHUNK = 10

    # linear速度制限: 表示ラベル→コンフィグキー→フルストローク最短時間(ms)
    SPEED_LIMIT_MS = {"none": 0, "low": 150, "mid": 250, "high": 400}

    RETARGET_MS = 100

    # 同順位のときの固定順(トラック名の優先度)
    TRACK_ORDER = ("linear", "twist", "rotate", TRACK_ROTATE_A10, "vibration")

    TRACK_DISABLED_COLOR = "gray45"   # 未接続トラックの文字色

    STATUS_TEXT = {
        "idle": (tr("待機中"), MUTED),
        "playing": (tr("再生中"), OK_TEXT),
        "paused": (tr("一時停止"), WARN_TEXT),
        "stopped": (tr("シナリオ終了"), MUTED),
        "finished": (tr("シナリオ終了"), MUTED),
        "error": (tr("エラー"), ERROR_TEXT),
    }

    CHOICE_COLS = {1: 1, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 7: 3, 8: 3, 9: 3}
