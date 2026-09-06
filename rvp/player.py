"""再生エンジン(チャンネル対応)。

- PlaybackClock: 一時停止を考慮した再生位置クロック。
- ScenarioPlayer: シナリオを進行させる。イベントは最大3チャンネル(L/C/R)を
  並行再生し、各チャンネルは独立した mode(順番/ランダム)・終了条件を持つ。
  funscript(デバイス連動)は担当チャンネル(device_channel)のアイテムのみが実行する。

音声再生は pygame.mixer.Sound + Channel を使い、チャンネルごとにパン
(左右の音量バランス)を適用する。funscript は担当チャンネルの音声再生に
同期したクロックを基準にスケジューリングする。
"""

import asyncio
import bisect
import logging
import os
import random
import time
import wave

import pygame

from .i18n import tr
from .funscript import Funscript
from .rotate_source import RotateTimeline, load_rotate_source
from .intiface_client import IntifaceClient
from .mpv_client import MpvClient, MpvError, find_mpv
from .scenario import (
    track_range_ms,
    CH_CENTER,
    END_CHANNEL,
    END_COND,
    END_PLAYS,
    END_TRANSITIONS,
    END_STATES,
    WHEN_CHANNEL_COUNT,
    WHEN_COND,
    WHEN_ALL_CHANNELS,
    WHEN_CHANNEL_END,
    WHEN_STATE_TIME,
    WHEN_CHOICE,
    END_DURATION,
    END_NONE,
    END_REPEAT,
    MODE_RANDOM,
    MODE_RANDOM_BAG,
    MODE_SEQUENTIAL,
    TRACK_LINEAR,
    TRACK_TWIST,
    TRACK_ROTATE,
    TRACK_ROTATE_A10,
    TRACK_VIBRATION,
    DEFAULT_PAN,
    Pan,
    Scenario,
    VarOp,
)

logger = logging.getLogger("rvp.player")

# pygame mixer のチャンネル割り当て(L/C/R を固定スロットに割り当てる)
_CH_SLOT = {"L": 0, "C": 1, "R": 2}

# =256: BGM専用スロット。_stop_all_audio(スロット0〜2)の対象外にすることで、
# ステート/イベント境界をまたいで再生を継続する。フェード時間は
# ブツ切りのクリックノイズ回避用(Q10=300ms。フェードインはなし)。
_BGM_SLOT = 3
BGM_FADEOUT_MS = 300

# 音声なしイベント/ステート(即時通過ノード)の連続通過の上限。
# この回数連続で「音声を1つも再生しない通過」が起きたら無限ループとみなし
# status="error" で再生を停止する(テストが参照するため定数で公開)。
SILENT_LOOP_LIMIT = 100

# =69(グラフ表示): 1イベント内で保持するグラフ用スクリプト断片の上限。
# 過去の波形も残す仕様なので、アイテム数の多い長いイベントで無制限に
# 溜まらないよう古いものから捨てる(表示は直近だけで十分)。
GRAPH_SEG_LIMIT = 60

# =62(音声なしフェーズ2): 無音待機ノードが「実際に待った」とみなす時間。
# これ以上待ったら上の連続通過カウンタをリセットする(N秒待つノードを
# 100個以上たどる正当なシナリオを誤検出しないため)。
SILENT_WAIT_RESET_MS = 1000


def _audio_duration_ms(path: str) -> int:
    """音声ファイルの長さ(ミリ秒)を取得する。wavは高速なwaveモジュールで読む。"""
    try:
        with wave.open(path, "rb") as w:
            return int(w.getnframes() / w.getframerate() * 1000)
    except Exception:
        pass
    try:
        return int(pygame.mixer.Sound(path).get_length() * 1000)
    except Exception:
        logger.warning(tr("音声の長さを取得できません: %s"), path)
        return 0


class PlaybackClock:
    """一時停止対応の単調クロック(ミリ秒)。

    =214: 再生速度(rate)に対応。now_ms() の進みが rate 倍になる
    (既定 1.0=従来どおり。player 本体は 1.0 のまま使う)。
    """

    def __init__(self):
        self._start: float | None = None
        self._pause_start: float | None = None
        self._paused_total: float = 0.0
        self._rate: float = 1.0

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        """速度を変える(=214)。今の位置を基準に取り直すので位置は飛ばない。
        一時停止状態も維持する。"""
        rate = float(rate)
        if rate <= 0:
            rate = 1.0
        if self._start is not None:
            cur = self.now_ms()
            self._rate = rate
            self.set_ms(cur)
        else:
            self._rate = rate

    def start(self) -> None:
        self._start = time.monotonic()
        self._pause_start = None
        self._paused_total = 0.0

    def pause(self) -> None:
        if self._start is not None and self._pause_start is None:
            self._pause_start = time.monotonic()

    def resume(self) -> None:
        if self._pause_start is not None:
            self._paused_total += time.monotonic() - self._pause_start
            self._pause_start = None

    @property
    def paused(self) -> bool:
        return self._pause_start is not None

    def now_ms(self) -> float:
        if self._start is None:
            return 0.0
        base = self._pause_start if self.paused else time.monotonic()
        return (base - self._start - self._paused_total) * 1000.0 * self._rate

    def set_ms(self, ms: float) -> None:
        """再生位置を指定時刻(ミリ秒)へジャンプさせる。一時停止状態は維持する。"""
        now = time.monotonic()
        self._paused_total = 0.0
        self._start = now - ms / 1000.0 / self._rate
        if self.paused:
            self._pause_start = now


def sound_byte_view(sound) -> memoryview:
    """Soundの生PCMを**コピーせず**バイト単位のmemoryviewで返す(=249)。

    従来の get_raw() はデータ全体をbytesへ複製するため、巨大な音声
    (例: 60分超のwav=700MB級)ではシーク・区間切り出しのたびに
    一時コピーが積み上がっていた。pygame-ce の Sound はバッファ
    プロトコル対応なので、memoryview → cast("B") でゼロコピーの
    ビューが取れる(スライスもビューのままコピーなし)。
    ビューは元Soundのバッファを指すので**書き込み禁止**・元Soundより
    長く持たない。バッファ非対応の実装(テスト用スタブ等)だけ
    get_raw() へフォールバックする。
    """
    try:
        return memoryview(sound).cast("B")
    except (TypeError, ValueError):
        return memoryview(sound.get_raw())


def describe_audio_load_error(path: str, exc: Exception) -> str:
    """音声読み込み失敗の詳しい説明(=249)。該当しなければ ""。

    SDL2は、ミキサー形式(既定 44100Hz/16bit/2ch)と違うwavを読み込む
    ときに形式変換を行うが、その変換バッファ長がint(2^31)管理のため、
    **データ部が約512MiB(48kHz→44.1kHzの実測値。変換内容によっては
    さらに小さい)を超えると空きメモリと無関係に "Out of memory" で
    失敗する**。エラーがメモリ系で、wavヘッダが読めた場合だけ、
    形式と対処(44.1kHzへの変換 or メモリ確保)を具体的に案内する。
    """
    if "memory" not in str(exc).lower():
        return ""
    try:
        with wave.open(path, "rb") as w:
            rate = int(w.getframerate())
            bits = int(w.getsampwidth()) * 8
            ch = int(w.getnchannels())
            data_mib = (w.getnframes() * w.getsampwidth() * ch
                        // (1024 * 1024))
    except Exception:
        return ""
    init = pygame.mixer.get_init() or (44100, -16, 2)
    mrate, mfmt, mch = int(init[0]), abs(int(init[1])), int(init[2])
    src = f"{rate}Hz/{bits}bit/{ch}ch"
    dst = f"{mrate}Hz/{mfmt}bit/{mch}ch"
    if (rate, bits, ch) != (mrate, mfmt, mch):
        return tr(
            "このwavは{0}のため、読み込み時に形式変換が必要です。"
            "変換はデータ部が約512MiBを超えると空きメモリに関係なく"
            "失敗します(SDLの上限)。{1}のwavへ変換すると読み込めます"
            "(このファイルのデータ部: 約{2}MiB)。").format(
                src, dst, data_mib)
    return tr(
        "メモリが不足しています(このwavの展開には約{0}MiB必要です)。"
        "ほかのアプリを閉じるか、音声ファイルを分割してください。").format(
            data_mib)


def resample_frames(raw: bytes, frame_bytes: int, rate: float) -> bytes:
    """=214: 再生速度 rate のための最近傍リサンプリング(音の高さも変わる
    テープ早回し式)。rate>1 で短く(間引き)・rate<1 で長く(複製)。

    numpy/audioop に頼らない(Python 3.13 で audioop が消えるため)。
    rate を分数 p/q(q≦20)に近似し、「入力 p フレーム → 出力 q フレーム」
    のブロック規則を **array の拡張スライス代入 q 回** で一気に作る
    (1フレームずつの Python ループを避ける=数分の音声でも数十ms)。
    フレーム幅が array の型に合わないときだけ素朴なループへ落ちる。
    """
    from fractions import Fraction
    from array import array
    rate = float(rate)
    if rate <= 0 or abs(rate - 1.0) < 1e-9 or frame_bytes <= 0:
        return raw
    fr = Fraction(rate).limit_denominator(20)
    p, q = fr.numerator, fr.denominator
    n_in = len(raw) // frame_bytes
    n_blocks = n_in // p
    n_out = n_blocks * q
    if n_out <= 0:
        return b""
    tc = None
    for cand in ("B", "H", "I", "L", "Q"):
        try:
            if array(cand).itemsize == frame_bytes:
                tc = cand
                break
        except ValueError:
            continue
    if tc is None:
        out = bytearray()
        for j in range(n_out):
            i = (j * p) // q
            out += raw[i * frame_bytes:(i + 1) * frame_bytes]
        return bytes(out)
    src = array(tc)
    src.frombytes(raw[:n_blocks * p * frame_bytes])
    out = array(tc, bytes(n_out * frame_bytes))
    for k in range(q):
        off = (k * p) // q
        out[k::q] = src[off::p][:n_blocks]
    return out.tobytes()


class ScenarioPlayer:
    """シナリオ全体の再生を管理する。asyncio ループ上で動作する。"""

    def __init__(self, intiface: IntifaceClient):
        self.intiface = intiface
        # =252: 再生中(および直近)のシナリオ。デバイス連動フラグの参照用。
        self.scenario: Scenario | None = None
        # 主担当チャンネルの同期基準クロック(シーク・経過表示用)。
        # イベント再生中は主担当チャンネルのクロックを指す。
        self.clock = PlaybackClock()
        # イベント内でアクティブな全クロック(チャンネルごと)。一時停止で一括制御。
        self._active_clocks: list[PlaybackClock] = []
        # END_CHANNEL 終了条件の成立後: 各チャンネルは再生中の音声を
        # 終えたら新しい再生を始めずに終了する。
        self._winding_down = False
        self._stop_requested = False
        self._playing = False
        self._paused = False
        self._jump = None            # "next" / "back" / None
        self._visited_events: set[str] = set()
        # =77: このイベント実行中に実行したステート(transitionのvisited:
        # exclude用)。イベントに入るたびリセット(ユーザー決定)。
        self._visited_states: set[str] = set()
        # 音声なしイベント/ステートの「連続通過」カウンタ(無限ループ検出用)。
        # 音声つきステートの実行、または選択肢/数値入力の待機でリセットされる。
        self._silent_streak = 0
        # =63: シークバー追従先がスクリプト専用チャンネルか
        self._seek_script = False
        self._choice_clock: PlaybackClock | None = None
        self._choice_entries: list = []          # ChoiceEntryのリスト
        self._choice_result: str | None = None
        self._choice_result_ops: tuple = ()      # 選択されたエントリのops(1回だけ発火)
        self._choice_result_index: int | None = None   # =70 ログ用の選択番号
        # =275: 表示中の選択肢の持ち主("event"=イベントの next choice /
        # "state"=ステート移行の選択肢)。表示枠は1つなので、監視側は自分の
        # 持ち主の選択肢だけを解決する(他方の結果を誤って消費しない)。
        self._choice_owner = "event"
        self._choice_result_event = False   # 選択/既定の行き先がイベントか
        # =275: ステート移行の選択肢で「イベント宛て」が選ばれた=イベントを
        # 自然終了させてから飛ぶ遷移先。_play_event の自然終了フローで消費
        self._state_choice_event_to: str | None = None
        # =275: ステート移行(transition)によるワインドダウン中か。イベント
        # の next choice/input の show="end"(イベント終了条件の達成時)は
        # ステート移行の巻き取りでは表示しない(従来は _winding_down だけを
        # 見ていたためステート移行でも出ていた=文書の仕様と不一致)。
        self._transition_winding = False
        self._sc_rule = None   # 再生中ステートの選択肢ルール(WHEN_CHOICE)
        self._input_result: float | None = None  # 数値入力の確定値(未確定=None)
        # シナリオ変数(vars)。再生開始で初期値にリセット。◀◀/▶▶で巻き戻さない。
        self.vars: dict = {}
        self._var_decls: dict = {}
        # 監視(watch): 変数条件の常時監視トリガー。変数操作の直後と
        # イベント開始時に評価する。interruptは_jump=("goto",to)で即時、
        # gracefulは_watch_gotoに保留してワインドダウン後に遷移する
        self._watches: tuple = ()
        self._watch_fired: set[int] = set()     # once=Trueの発火済みインデックス
        self._watch_goto: str | None = None     # gracefulの保留遷移先
        self._fs_resync = False
        # デバイス動作タイミングのオフセット(ms)。種別ごとに独立。
        # 正の値=音声より遅らせる / 負の値=音声より早める(funscriptを先読み)。
        # UIスレッドから直接書き換えられる(読み書きは各ループで都度参照)。
        self.offsets_ms = {"linear": 0, "twist": 0, "rotate": 0,
                           TRACK_ROTATE_A10: 0, "vibration": 0}
        # ROTATEレーンごとの左右反転(タイプBの左右ch入替。フェーズ3のUIで設定)。
        self.rotate_swap = {"ufo": False, "a10": False}
        # =80: TCode直接出力クライアント(main が設定。None=未使用)。
        # 接続中は linear/twist の送信先が Intiface から TCode(L0/R0)へ替わる。
        self.tcode = None
        # =71: 直近に送った funscript 座標の pos(レンジ調整の追従に使う)
        self._last_linear_raw: int | None = None
        # =79 TWIST軸の並行状態(linearの _dev_move/_dev_known_pos/_last_*_raw と対)
        self._twist_move: dict | None = None
        self._twist_known_pos: float | None = None
        self._last_twist_raw: int | None = None
        # ---- グラフ表示(=69) ----
        # 実行中/実行済みのスクリプト(funscript・CSV)を「イベント入場からの
        # 経過時間」を共通の横軸にして UI へ渡すための断片リスト。
        # イベント入場でクリアし、同一イベント内は過去の波形も残す。
        self.graph_segments: list = []
        # イベント経過クロック(グラフの時間基準)。_play_event が設定する。
        self._event_clock: PlaybackClock | None = None
        # マスター音量(0.0=消音〜1.0=最大)。パンに乗算して適用する。
        # UIスレッドから set_master_volume() で変更される。
        self.master_volume = 1.0
        self._slot_pan: dict[int, tuple] = {}  # slot -> (left, right) 再生中のパン
        self._slot_sound: dict[int, object] = {}  # slot -> 再生中アイテムの原音(シーク用)
        # ---- linear安全機構 ----
        # 速度制限: フルストローク(0→100)にかける最短時間(ms)。0=制限なし。
        # UIスレッドから直接書き換えられる(既定=「中」0.25秒)
        self.linear_speed_limit_ms = 250
        # スマートスタート: 位置が飛ぶ操作(再生開始/再開/シーク/イベントジャンプ等)
        # の後2秒間、速度上限を大幅に下げて徐々に通常へ戻す
        self._soft_until = 0.0                 # 終了時刻(monotonic秒)
        # デバイス実座標(invert適用後0-100)での直近の移動と既知位置。
        # 速度制限の距離計算に使う(invert切替やイベント跨ぎでも正しい距離になる)
        self._dev_move: dict | None = None     # {"from","to","t0","dur"}
        self._dev_known_pos: float | None = None   # None=不明(次の移動を保守的に制限)
        self._event_base_ms = 0.0
        self._event_plays = 0
        # イベント終了条件の解決値(イベント入場時にresolve_endで固定)
        self._ev_end_count = 1
        self._ev_end_ms = 0
        # ---- 動画(外部mpv)連携(PLAN_VIDEO.md フェーズ1) ----
        # mpv_path は main がコンフィグから設定する(None=自動探索)。
        # mpv はオプショナル: 動画イベントの再生時に初めて起動し、
        # RVP終了(shutdown_mpv)まで維持する(合意事項)。
        self.mpv = None                    # MpvClient(遅延生成)
        self.mpv_path: str | None = None
        self.mpv_extra_args: tuple = ()    # テスト用(--vo=null等)
        self._video_clock: PlaybackClock | None = None  # 動画再生中のみ非None
        self._mpv_fail_times: list = []   # mpv自動復旧の時刻(連続クラッシュ検出)
        self._mpv_recovering = False      # 復旧中はmpvイベントを同期に使わない
        self._mpv_quiet = False           # 区間終端の一時停止など、RVPへ伝播
                                          # させたくないmpv操作の間だけTrue
        # 区間指定(=51 フェーズ3)。動画クロック・シークバー・トラック同期は
        # 「区間の先頭=0秒」で扱う(ユーザー決定)。動画なし時は 0.0 / None。
        self._video_start_s = 0.0
        self._video_end_s: float | None = None
        # 再生中の動画アイテム(=52 フェーズ3-B)。動画がチャンネルの
        # アイテムになったため、mpvの復旧・区間の判定はこのアイテムを見る。
        self._video_item = None
        self._video_seamless = False      # mpv側でループ中(ab-loop/loop-file)
        # =239: いま実行中のイベント/ステートで **device 担当が指定されている
        # 種別**の集合。動画トラックを鳴らすかどうかの判定に使う
        # (担当が別のチャンネルにある種別は、動画のトラックを鳴らさない)。
        self._assigned_types: set = set()
        # 直前に mpv へ loadfile した動画(同じファイルなら読み直さずシークで
        # 繋いで、つなぎの黒フレームを避ける=ユーザー決定)
        self._mpv_loaded_file = ""

        # mixerのチャンネル数を確保
        try:
            if pygame.mixer.get_init():
                pygame.mixer.set_num_channels(8)
                # =256: スロット0〜3(L/C/R+BGM)を予約し、Sound.play()等の
                # 自動チャンネル選択(find_channel)に奪われないようにする
                pygame.mixer.set_reserved(4)
        except Exception:
            pass

        # ---- BGM(=256) ----
        # ステート/イベントのライフサイクルから独立した専用スロット(3)で
        # 再生する。ノード入場時に _apply_bgm が「指定/引き継ぐ/オフ」を
        # 判定するだけで、引き継ぎは「何もしない」ことで実現する。
        self._bgm_task = None        # 複数曲の送り込みタスク(1曲はloops=-1)
        self._bgm_active = False     # いま鳴っている(ログ「BGM停止」の要否)
        self._bgm_sounds = {}        # path -> Sound(同一シナリオ内キャッシュ)
        self._bgm_gen = 0            # 世代(フェード後掃除の取り違え防止)

        self.state = {
            "status": "idle",
            "scenario_title": "",
            "event_id": "",
            "state_id": "",
            # 再生タブ「イベント状態」ビュー用の履歴トレイル。
            # event_trail = ◀◀で戻れる履歴スタック+現在イベント(タプル)。
            #   ◀◀で戻ると縮む=表示の緑線は常に「戻れる経路」と同期する。
            # state_trail = 現イベント滞在中に通ったステート移行 (from,to) の列。
            #   イベント入場(再入場含む)でリセット。
            # どちらも再生開始でリセットし、停止/終了後も保持する
            # (前回の軌跡を見直せるように。シナリオ切替でクリア)。
            "event_trail": (),
            "state_trail": (),
            # ③変数・イベントログ用の追記式ログ。("event"|"state", id) の列。
            # trail と違い◀◀で縮まない=通過した順の完全な記録。
            # 再生開始でリセット、停止/終了後も保持、シナリオ切替でクリア。
            "run_log": (),
            "audio_file": "",         # 担当チャンネルの再生中音声(代表表示)
            "channel_audio": {},      # {"L": "xxx.wav", ...} 各チャンネルの再生中音声
            "elapsed_ms": 0,
            "duration_ms": 0,
            "event_elapsed_ms": 0,
            "state_elapsed_ms": 0,
            "linear_pos": 0,
            # 進行中のlinear移動(UIアニメーション用): 補正後の指示値ベース
            # {"from": 40, "to": 80, "t0": monotonic秒, "dur": 秒} / None=停止
            "linear_move": None,
            # =79 TWIST軸(2軸目)の表示状態。意味論はlinearと同一
            "twist_pos": 0,
            "twist_move": None,
            # 表示中の選択肢: None or {"event_id","labels":[...]}
            "choice": None,
            "choice_remaining_ms": None,   # タイムリミット残り(ms)。None=無制限
            # 表示中の数値入力: None or {"event_id","var","label","min","max"}
            "input": None,
            "vars": {},                    # シナリオ変数の現在値(表示用コピー)
            "rotate_pos": 50,
            "rotate_a10_pos": 50,    # rotate(A10サイクロンSA): 直近のpos(50=停止)
            # レーン別・チャンネル別の直近値 [(clockwise, frac), ...](フェーズ3の
            # 左右分離表示用)。1ch=[(cw,frac)] / 2ch(タイプB)=[左, 右]。
            "rotate_ufo_ch": [(True, 0.0)],
            "rotate_a10_ch": [(True, 0.0)],
            "vibration_pos": 0,      # vibration: 直近のpos(0=停止)
            "device_channel": "",
            "seek_channel": "",       # シークバー追従チャンネル(表示・シーク対象)
            "video_file": "",         # 再生中の動画(動画chのあるときのみ)
            "video_channel": "",      # 動画チャンネルのID(=52。""=動画なし)
            "message": "",
        }

    # ================= 制御 =================

    async def play(self, scenario: Scenario) -> None:
        if self._playing:
            return
        self._playing = True
        self.scenario = scenario   # =252: デバイス連動フラグ等の参照用
        self._bgm_sounds = {}      # =256: BGMキャッシュはシナリオ単位で捨てる
        self._stop_requested = False
        self._paused = False
        self.state["scenario_title"] = scenario.title
        self.state["status"] = "playing"
        self.state["message"] = ""
        # シナリオ変数を初期値にリセット
        self._var_decls = scenario.var_decls
        self.vars = {name: d.init for name, d in scenario.var_decls.items()}
        self.state["vars"] = dict(self.vars)
        # 監視(watch)をリセット
        self._watches = scenario.watches
        self._watch_fired = set()
        self._watch_goto = None
        # 再生開始も「位置が飛ぶ」操作としてスマートスタートで始める
        self.start_soft_start()
        try:
            history: list[str] = []
            # このシナリオ再生中に実行したイベント(next分岐のvisited: exclude用)。
            # 再生開始でリセット。◀◀での再実行もカウントされる。
            self._visited_events = set()
            # 音声なしノードの連続通過カウンタも再生開始でリセット
            self._silent_streak = 0
            # 履歴トレイル(イベント状態ビュー用)とイベントログも再生開始でリセット
            self.state["event_trail"] = ()
            self.state["state_trail"] = ()
            self.state["run_log"] = ()
            # =124: 図の減光(実行済み)/明度アップ(すごろく通過)用
            self.state["visited_events"] = ()
            self.state["advance_glow"] = ()
            self.graph_segments = []       # =69 グラフも再生開始でクリア
            event_id = scenario.start
            while event_id and not self._stop_requested:
                event = scenario.events[event_id]
                self._visited_events.add(event_id)
                # =124: 実行済みノードの減光表示用に図側へ公開する
                self.state["visited_events"] = tuple(self._visited_events)
                # ◀◀履歴スタック+現在イベント=辿った経路(◀◀で戻ると縮む)
                self.state["event_trail"] = tuple(history) + (event_id,)
                jump = await self._play_event(event)
                self._clear_choice()
                self._clear_input()
                if jump == "back":
                    event_id = history.pop() if history else event_id
                elif isinstance(jump, tuple) and jump[0] == "goto":
                    # 選択肢/数値入力の確定・監視(watch)による即時イベント切替も
                    # 位置が飛ぶ操作としてスマートスタート対象にする
                    self.start_soft_start()
                    history.append(event_id)
                    event_id = jump[1]
                else:
                    history.append(event_id)
                    event_id = self._pick_next(event)
                    # =123 すごろく(advance): next解決を合計N回まで繰り返し、
                    # N番目の着地イベントだけ再生する。途中は「通過」=
                    # 再生せず on_start/on_end/監視も発火せず、visited にも
                    # 数えない(通過抽選そのもの(重み変数・cond・random_bag)は
                    # 通過イベント自身の next規則で行う)。
                    steps = self._resolve_advance(event)
                    while steps > 1 and event_id:
                        mid = scenario.events.get(event_id)
                        if mid is None:
                            break
                        if mid.next_choice is not None or mid.next_input is not None:
                            # 選択肢/数値入力のマス=そこに止まる(残り歩数は捨てる)
                            self._log("end", tr("すごろく: {0} に止まる(選択肢/数値入力)").format(event_id))
                            break
                        if (mid.next is None and mid.next_rule is None
                                and mid.next_cond is None):
                            # 遷移の無いマス=ゴール。着地して再生し、
                            # 再生後に next=無し でシナリオが自然に終わる
                            self._log("end", tr("すごろく: ゴール {0} に到達").format(event_id))
                            break
                        nxt = self._pick_next(mid)
                        if nxt is None:
                            # 動的な行き止まり(重み全0でelse無し等)=あがり扱いで終了
                            self._log("end", tr("すごろく: {0} で行き止まり(あがり扱いで終了)").format(event_id))
                            event_id = None
                            break
                        self._log("end", tr("すごろく: {0} を通過").format(event_id))
                        # =124: 通過マスを図で0.5秒だけ明度アップ表示する。
                        # 期限切れは表示側(main)が判定するのでここでは
                        # 直近1秒より古いものだけ間引く
                        now = time.monotonic()
                        glow = [g for g in self.state.get("advance_glow", ())
                                if now - g[1] < 1.0]
                        glow.append((event_id, now))
                        self.state["advance_glow"] = tuple(glow)
                        event_id = nxt
                        steps -= 1
            self.state["status"] = "stopped" if self._stop_requested else "finished"
        except Exception as e:
            logger.exception(tr("再生中にエラー"))
            self.state["status"] = "error"
            self.state["message"] = str(e)
        finally:
            self._playing = False
            self._graph_freeze()      # =69 グラフの時間も止める
            self._stop_all_audio()
            # =256: シナリオの終わり(自然終了・停止・エラー)でBGMも止める
            # (フェードアウト付き。境界をまたぐのはノード間だけ)
            try:
                await self._bgm_stop(fade=True)
            except Exception:
                pass
            try:
                await self.intiface.stop_all()
            except Exception:
                pass

    async def choose(self, index: int) -> None:
        """選択肢のボタン押下(UIスレッドからサブミットされる)。"""
        if (self.state["choice"] is None or self._watch_goto is not None
                or isinstance(self._jump, tuple)):
            return
        if 0 <= index < len(self._choice_entries):
            ent = self._choice_entries[index]
            self._choice_result_ops = ent.ops
            self._choice_result_index = index      # =70 ログ用(番号とラベル)
            self._choice_result_event = bool(getattr(ent, "to_event", False))
            self._choice_result = ent.to

    async def submit_input(self, value: float, event_id: str | None = None) -> None:
        """数値入力の決定ボタン押下(UIスレッドからサブミットされる)。

        受理範囲外の値は無視する(UI側が事前に検証してエラー表示する)。
        event_id 指定時は表示中の入力と一致するときだけ受理する
        (遷移直後に届いた遅延サブミットの誤適用を防ぐ)。
        """
        st = self.state["input"]
        if (st is None or self._watch_goto is not None
                or isinstance(self._jump, tuple)):
            return
        if event_id is not None and st.get("event_id") != event_id:
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if value != value or value in (float("inf"), float("-inf")):
            return   # NaN/無限大は受理しない
        mn, mx = st.get("min"), st.get("max")
        if (mn is not None and value < mn) or (mx is not None and value > mx):
            return
        self._input_result = value

    def _log(self, kind: str, text: str) -> None:
        """⑤変数・イベントログへ1行追記する(=54でアイテム/警告に拡充)。

        kind: "event" / "state" / "chan"(解釈後のチャンネル設定) /
        "item"(再生したアイテム) / "warn"(警告) / "end"(次へ進んだ理由) /
        "var"(=70 変数操作) / "choice"・"input"(=70 選択肢/数値入力の表示)。
        表示は main._update_run_log が担当する。
        """
        self.state["run_log"] = self.state["run_log"] + ((kind, text),)

    def _log_item(self, channel_id: str, item) -> None:
        """再生を開始したアイテムをログへ残す。

        「順番に再生/ランダム再生が効いているか」「どの動画のどの区間が
        再生されたか」を後から確認できるようにするため(ユーザー要望)。
        """
        path = item.audio or item.video
        label = os.path.basename(path) if path else tr("(スクリプト)")
        # =59: 区間は音声・動画・スクリプトのすべてに付くようになった
        if item.has_range:
            start = f"{item.start_s:g}"
            end = "" if item.end_s is None else f"{item.end_s:g}"
            label = f"{label} {start}〜{end}s"
        self._log("item", f"{channel_id}: {label}")

    def _log_manual_jump(self, jump) -> None:
        """手動の▶▶スキップ/◀◀巻き戻しを遷移理由としてログへ残す(=57)。

        自動の終了条件と区別できるよう「手動」と明記する。
        jump が ("goto", to) のときは選択肢/監視(watch)側で理由を
        記録済みなので何もしない。
        """
        if jump == "next":
            self._log("end", tr("イベント終了: 手動でスキップ(▶▶)"))
        elif jump == "back":
            self._log("end", tr("イベント終了: 手動で巻き戻し(◀◀)"))

    @staticmethod
    def _fmt_val(v) -> str:
        """ログ表示用の値の整形(小数は余分な0を出さない)。"""
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    @classmethod
    def _fmt_signed(cls, v) -> str:
        """加算の値を符号つきで表す(+5 / -20)。"""
        try:
            return ("+" if float(v) >= 0 else "") + cls._fmt_val(v)
        except (TypeError, ValueError):
            return cls._fmt_val(v)

    def _log_var(self, name: str, src: str, before, after, raw,
                 detail: str) -> None:
        """変数操作1件をログへ残す(=70)。

        形は「名前: 変更前 → 変更後 (種別 値) [発火場所]」。値が変わらなかった
        場合も残す(操作されたこと自体が分かるように=ユーザー決定)。宣言の
        min/max で丸められたときはその旨も添える。
        """
        txt = "{0}: {1} → {2} ({3})".format(
            name, self._fmt_val(before), self._fmt_val(after), detail)
        if raw != after:
            try:
                over = float(raw) > float(after)
            except (TypeError, ValueError):
                over = False
            txt += " (" + (tr("上限で丸め") if over else tr("下限で丸め")) + ")"
        if src:
            txt += " [" + src + "]"
        self._log("var", txt)

    def _apply_ops(self, ops, src: str = "") -> None:
        """変数操作を順に実行する(宣言のmin/maxでクランプ)。

        src(=70) は「どこで発火したか」のログ用ラベル(イベント開始時など)。
        """
        if not ops:
            return
        for op in ops:
            decl = self._var_decls.get(op.name)
            if decl is None:
                continue   # 読み込み時に検証済みのため通常到達しない
            before = self.vars.get(op.name)
            refs = []
            if op.kind == "roll":
                # [min, max] の一様乱数(整数, 両端含む)を代入。min>max は入替。
                lo = self.vars[op.value_var] if op.value_var is not None \
                    else op.value
                hi = self.vars[op.value2_var] if op.value2_var is not None \
                    else op.value2
                if op.value_var is not None:
                    refs.append(op.value_var)
                if op.value2_var is not None:
                    refs.append(op.value2_var)
                lo, hi = int(round(float(lo))), int(round(float(hi)))
                if lo > hi:
                    lo, hi = hi, lo
                raw = random.randint(lo, hi)
                detail = tr("乱数 {0}〜{1}").format(lo, hi)
            elif op.kind == "eval":
                # =126 条件式: 成立=1 / 不成立=0 を代入(クランプは共通処理)
                c = op.cond
                rhs = c.value_var if c.value_var is not None else c.value
                raw = 1 if c.eval(self.vars) else 0
                detail = tr("条件式 {0} → {1}").format(
                    f"{c.name} {c.op} {self._fmt_val(rhs)}", raw)
                if c.value_var is not None:
                    refs.append(c.value_var)
            else:
                value = self.vars[op.value_var] if op.value_var is not None \
                    else op.value
                if op.value_var is not None:
                    refs.append(op.value_var)
                if op.kind == "add":
                    detail = tr("加算 {0}").format(self._fmt_signed(value))
                    raw = self.vars[op.name] + value
                elif op.kind == "mul":
                    # =75: 乗算。フラグに ×1/×0 を掛けて「全yes判定」等に使う
                    detail = tr("乗算 {0}").format(self._fmt_val(value))
                    raw = self.vars[op.name] * value
                else:
                    detail = tr("代入 {0}").format(self._fmt_val(value))
                    raw = value
            if refs:
                detail += " ←" + ",".join(refs)
            self.vars[op.name] = decl.clamp(raw)
            self._log_var(op.name, src, before, self.vars[op.name], raw, detail)
        self.state["vars"] = dict(self.vars)
        self._check_watches()

    def _check_watches(self) -> None:
        """監視(watch)を宣言順に評価し、最初に成立した1件を発火する。

        - 遷移先(to)のイベント再生中はその監視を評価しない(自明なループ防止)
        - once=True は1回の再生につき1度だけ発火
        - graceful: ワインドダウン(再生中の音声を終えてから)でイベントを終了し遷移
        - interrupt: 即時打ち切りで遷移(▶▶スキップと同様)
        いずれも遷移は履歴に積まれ、◀◀で元のイベントへ戻れる。
        """
        if not self._watches or not self._playing or self._stop_requested:
            return
        current = self.state.get("event_id")
        for i, w in enumerate(self._watches):
            if w.once and i in self._watch_fired:
                continue
            if w.to == current:
                continue
            if not all(c.eval(self.vars) for c in w.conds):
                continue
            if w.mode == "interrupt":
                if self._jump is not None:
                    continue   # 既存のジャンプを上書きしない
                self._watch_fired.add(i)
                self._jump = ("goto", w.to)
                self._stop_all_audio()
            else:
                if self._watch_goto is not None:
                    continue   # 先に成立した保留を優先
                self._watch_fired.add(i)
                self._watch_goto = w.to
                self._winding_down = True
            return

    def _show_choice(self, event, rule=None, owner: str = "event",
                     state_id: str = "") -> None:
        """選択肢を表示する(表示済みなら何もしない)。

        =275: rule/owner/state_id を渡すとステート移行の選択肢として表示する
        (UI側の識別子 event_id は「イベントID/ステートID」にして、同じ
        イベント内でイベント側の選択肢と区別できるようにする)。
        """
        if self.state["choice"] is not None:
            return
        if rule is None:
            rule = event.next_choice
        self._choice_owner = owner
        self._choice_entries = list(rule.entries)
        self._choice_result = None
        self._choice_result_event = False
        clock = PlaybackClock()
        clock.start()
        if self._paused:
            clock.pause()
        self._choice_clock = clock
        self.state["choice_remaining_ms"] = rule.timeout_ms
        ui_id = event.event_id if owner == "event" \
            else "{0}/{1}".format(event.event_id, state_id)
        self.state["choice"] = {"event_id": ui_id,
                                "labels": [e.label for e in rule.entries]}
        self._log_choice_show(rule)
        self._video_osd(tr("選択肢が表示されています(RVPウィンドウで選択)"))

    # ---- 選択肢のログ(=70) ----

    @staticmethod
    def _entry_desc(index: int, entries) -> str:
        """選択肢1件を「番号)ラベル」で表す。"""
        if entries is None or not (0 <= index < len(entries)):
            return ""
        return "{0}){1}".format(index + 1, entries[index].label)

    def _to_desc(self, to, entries, index=None, to_event=None) -> str:
        """遷移先を「番号)ラベル → イベントID」で表す(該当なしはIDのみ)。

        =275: ステート移行の選択肢でイベント宛て(to_event)なら
        「→ イベント:X」と表す(ステートIDと区別する)。
        """
        if index is None:
            index = next((i for i, e in enumerate(entries or ())
                          if e.to == to
                          and (to_event is None or e.to_event == to_event)),
                         None)
        desc = self._entry_desc(index, entries) if index is not None else ""
        if to_event is None and index is not None and entries:
            to_event = entries[index].to_event
        target = tr("イベント:{0}").format(to) if to_event else str(to)
        return (desc + " → " + target) if desc else target

    def _default_desc(self, rule) -> str:
        """タイムアウト/▶▶での既定遷移先の説明(抽選前なので方式で示す)。"""
        if rule.default_mode == "random":
            return tr("ランダム")
        if rule.default_mode == "to" and rule.default_to:
            if rule.default_to_event:
                return tr("イベント:{0}").format(rule.default_to)
            return rule.default_to
        return self._entry_desc(0, rule.entries) or "-"

    def _log_choice_show(self, rule) -> None:
        """選択肢を表示したことをログへ残す(候補一覧・制限時間・既定)。"""
        items = " ".join(self._entry_desc(i, rule.entries)
                         for i in range(len(rule.entries)))
        extras = []
        if rule.timeout_ms is not None:
            extras.append(tr("制限{0:g}秒").format(rule.timeout_ms / 1000.0))
        extras.append(tr("既定={0}").format(self._default_desc(rule)))
        self._log("choice", tr("選択肢を表示: {0}").format(items)
                  + " (" + " / ".join(extras) + ")")

    def _clear_choice(self) -> None:
        if self.state["choice"] is not None:
            self._video_osd("")
        self.state["choice"] = None
        self.state["choice_remaining_ms"] = None
        self._choice_clock = None
        self._choice_entries = []
        self._choice_result = None
        self._choice_result_ops = ()
        self._choice_result_index = None
        self._choice_result_event = False
        self._choice_owner = "event"

    def _choice_resolution(self, rule, owner: str = "event") -> str | None:
        """選択済み/タイムアウトなら遷移先を返す(変数操作もここで発火)。未解決はNone。

        =275: owner が表示中の選択肢の持ち主と違えば何もしない(イベント側の
        監視がステート移行の選択肢の結果を消費しないため)。行き先が
        イベントか(ステート移行の選択肢)は _choice_result_event に残す。
        """
        if self.state["choice"] is not None and self._choice_owner != owner:
            return None
        if self._choice_result:
            self._apply_ops(self._choice_result_ops, tr("選択時"))
            self._choice_result_ops = ()
            if self._watch_goto is not None or isinstance(self._jump, tuple):
                return None   # 監視(watch)が発火 → 選択は破棄
            self._log("end", tr("選択肢を選択: {0}").format(
                self._to_desc(self._choice_result, self._choice_entries,
                              self._choice_result_index)))
            return self._choice_result
        if (self.state["choice"] is not None and rule.timeout_ms is not None
                and self._choice_clock is not None):
            remaining = rule.timeout_ms - self._choice_clock.now_ms()
            self.state["choice_remaining_ms"] = max(0, int(remaining))
            if remaining <= 0:
                self._apply_ops(rule.on_timeout, tr("タイムアウト時"))
                if self._watch_goto is not None or isinstance(self._jump, tuple):
                    return None   # 監視(watch)が発火 → デフォルト遷移は破棄
                to, to_event = rule.pick_default_info()
                self._choice_result_event = to_event
                self._log("end", tr("選択肢がタイムアウト: {0}").format(
                    self._to_desc(to, rule.entries, to_event=to_event)))
                return to
        return None

    async def _monitor_choice(self, event, event_clock) -> None:
        """再生中の選択肢の表示タイミング監視と、選択/タイムアウトの検出。

        解決したら _jump=("goto", to) でイベントを中断して遷移する。
        (再生中の音声は打ち切り。▶▶スキップと同じ即時遷移の挙動)
        """
        rule = event.next_choice
        while not self._stop_requested and self._jump is None:
            if self.state["choice"] is None:
                if rule.show_mode == "start":
                    self._show_choice(event)
                elif rule.show_mode == "ms" and event_clock.now_ms() >= rule.show_ms:
                    self._show_choice(event)
                elif (rule.show_mode == "end" and self._winding_down
                        and not self._transition_winding):
                    # =275: ステート移行の巻き取り中は「イベント終了」ではない
                    self._show_choice(event)
            else:
                to = self._choice_resolution(rule, owner="event")
                if to is not None:
                    if self._jump is None:   # 監視(watch)の発火を上書きしない
                        self._jump = ("goto", to)
                    return
            await asyncio.sleep(0.05)

    async def _monitor_cond(self, event) -> None:
        """=168 判定式(常に監視): 再生中に next の判定式を評価し続ける。

        終了条件が「無限」のイベント専用。通常の変数分岐は**イベントが
        終わった瞬間**にしか評価されないため、無限のイベントでは永久に
        発火しない。そこで、選択肢の監視(_monitor_choice)と同じように
        再生中に評価し、**どれかの行が成立したらイベントを打ち切って**
        その行の遷移先へ進む(ユーザー決定 2026-08-16)。

        else は「どの行も成立しないとき」なので、監視では使わない
        (成立するまで待ち続ける=イベントは終わらない)。
        """
        rule = event.next_cond
        while not self._stop_requested and self._jump is None:
            for conds, to in rule.rows:
                if conds and all(c.eval(self.vars) for c in conds):
                    if self._jump is None:   # 監視(watch)の発火を上書きしない
                        self._log("end", tr("判定式が成立: {0}へ").format(to))
                        self._jump = ("goto", to)
                    return
            await asyncio.sleep(0.05)

    async def _wait_choice(self, event) -> str | None:
        """イベント自然終了後の待機フェーズ。選択/タイムアウトまで待つ。"""
        rule = event.next_choice
        self._show_choice(event)   # 未表示ならここで表示
        while not self._stop_requested:
            if self._watch_goto is not None or isinstance(self._jump, tuple):
                return None   # 監視(watch)の発火が選択肢より優先
            if self._jump == "back":
                return None
            if self._jump == "next":
                self._jump = None
                if rule.skip_stay:
                    # =274: 選択必須=▶▶では飛ばさない。押下は消費して
                    # 待機を続ける(何度押しても進行しない)
                    self._log("choice", tr("▶▶は無効: 選択されるまで待機します(選択必須)"))
                    continue
                # 待機中の▶▶はデフォルト遷移先(first/random/指定イベント)へ
                to = rule.pick_default()
                self._log("end", tr("選択肢を手動でスキップ(▶▶): {0}").format(
                    self._to_desc(to, rule.entries)))
                return to
            to = self._choice_resolution(rule)
            if to is not None:
                return to
            await asyncio.sleep(0.05)
        return None

    # ---------------- 数値入力(next.input) ----------------

    def _show_input(self, event) -> None:
        if self.state["input"] is not None:
            return
        rule = event.next_input
        self._input_result = None
        self.state["input"] = {"event_id": event.event_id,
                               "var": rule.var,
                               "label": rule.label,
                               "min": rule.vmin,
                               "max": rule.vmax}
        self._log_input_show(rule)
        self._video_osd(tr("数値入力が表示されています(RVPウィンドウで入力)"))

    def _log_input_show(self, rule) -> None:
        """数値入力を表示したことをログへ残す(=70。対象変数・受理範囲・遷移先)。"""
        txt = tr("数値入力を表示: {0}").format(rule.var)
        if rule.vmin is not None or rule.vmax is not None:
            txt += " (" + tr("範囲 {0}〜{1}").format(
                self._fmt_val(rule.vmin) if rule.vmin is not None else "",
                self._fmt_val(rule.vmax) if rule.vmax is not None else "") + ")"
        self._log("input", txt + " → " + str(rule.to))

    def _clear_input(self) -> None:
        if self.state["input"] is not None:
            self._video_osd("")
        self.state["input"] = None
        self._input_result = None

    def _input_resolution(self, rule) -> str | None:
        """入力確定なら変数へセットして遷移先を返す。未確定はNone。

        整数値はintとして格納する(JSONの整数と型を揃える)。セットは
        通常の変数操作として扱われ、宣言のmin/maxクランプと監視(watch)の
        評価が働く。watchが発火した場合は入力遷移より優先される。
        """
        if self._input_result is None:
            return None
        v = self._input_result
        self._input_result = None
        v = int(v) if float(v).is_integer() else float(v)
        self._apply_ops((VarOp(kind="set", name=rule.var, value=v),),
                        tr("数値入力"))
        if self._watch_goto is not None or isinstance(self._jump, tuple):
            return None   # 監視(watch)が発火 → 入力遷移は破棄
        self._log("end", tr("数値入力を確定: {0} → {1}").format(
            self._fmt_val(v), rule.to))
        return rule.to

    async def _monitor_input(self, event, event_clock) -> None:
        """再生中の数値入力の表示タイミング監視と、決定の検出。

        決定したら _jump=("goto", to) でイベントを中断して遷移する。
        (再生中の音声は打ち切り。選択肢ボタンと同じ即時遷移の挙動)
        """
        rule = event.next_input
        while not self._stop_requested and self._jump is None:
            if self.state["input"] is None:
                if rule.show_mode == "start":
                    self._show_input(event)
                elif rule.show_mode == "ms" and event_clock.now_ms() >= rule.show_ms:
                    self._show_input(event)
                elif (rule.show_mode == "end" and self._winding_down
                        and not self._transition_winding):
                    self._show_input(event)
            else:
                to = self._input_resolution(rule)
                if to is not None:
                    if self._jump is None:   # 監視(watch)の発火を上書きしない
                        self._jump = ("goto", to)
                    return
            await asyncio.sleep(0.05)

    async def _wait_input(self, event) -> str | None:
        """イベント自然終了後の待機フェーズ。数値入力の決定まで待つ。"""
        rule = event.next_input
        self._show_input(event)   # 未表示ならここで表示
        while not self._stop_requested:
            if self._watch_goto is not None or isinstance(self._jump, tuple):
                return None   # 監視(watch)の発火が入力より優先
            if self._jump == "back":
                return None
            if self._jump == "next":
                self._jump = None
                if rule.skip_stay:
                    # =288: 入力必須=▶▶では飛ばさない。押下は消費して待機を続ける
                    self._log("choice", tr("▶▶は無効: 入力されるまで待機します(入力必須)"))
                    continue
                # 待機中の▶▶は変数を変更せずに遷移先へ
                self._log("end",
                          tr("数値入力を手動でスキップ(▶▶) → {0}").format(rule.to))
                return rule.to
            to = self._input_resolution(rule)
            if to is not None:
                return to
            await asyncio.sleep(0.05)
        return None

    def _resolve_advance(self, event) -> int:
        """=123 すごろく: このイベントの「進む歩数」を解決する(既定=1)。

        変数参照は遷移時点の現在値で解決。数値でない・0以下は1に丸め、
        上限999でクランプ(有界なので無限ループ検出は不要)。
        """
        n = 1
        try:
            if getattr(event, "advance_ref", None) is not None:
                n = int(event.advance_ref.resolve(self.vars))
            elif getattr(event, "advance", None) is not None:
                n = int(event.advance)
        except Exception:
            n = 1
        return max(1, min(999, n))

    def _pick_next(self, event) -> str | None:
        """イベント終了後の遷移先を決める。

        単純next(文字列)ならそのまま。NextRuleなら重み付き抽選。
        visited_exclude時は実行済みイベントを候補から除外し、
        全候補が実行済みなら exhausted_mode に従う:
          all   → 除外をやめて全候補から抽選
          reset → 候補分の訪問履歴を消して再一巡(この抽選から全候補が対象)
          to    → 指定イベントへ
        """
        if event.next_choice:
            # ▶▶スキップ等で選択を経ずに抜ける場合はデフォルト遷移先へ
            return event.next_choice.pick_default()
        if event.next_input:
            # ▶▶スキップ等で決定を経ずに抜ける場合は変数を変更せず遷移先へ
            return event.next_input.to
        if event.next_cond:
            # 変数分岐: 上から順に評価し最初に成立した行へ。全滅ならelse
            return event.next_cond.pick(self.vars)
        rule = event.next_rule
        if rule is None:
            return event.next
        cands = rule.candidates   # [(to, w_const|None, w_var|None)]
        if rule.visited_exclude:
            remaining = [c for c in cands if c[0] not in self._visited_events]
            if not remaining:
                if rule.exhausted_mode == "to":
                    return rule.exhausted_to
                if rule.exhausted_mode == "reset":
                    for c in cands:
                        self._visited_events.discard(c[0])
                remaining = cands
            cands = remaining
        # 重みを解決(変数参照は現在値)。0以下の候補は「出さない」ので除外。
        targets, weights = [], []
        for to, w_const, w_var in cands:
            w = self.vars.get(w_var) if w_var is not None else w_const
            try:
                w = float(w)
            except (TypeError, ValueError):
                w = 0.0
            if w > 0:
                targets.append(to)
                weights.append(w)
        if not targets:
            # 出せる候補が無い → else(フォールバック先)。未指定なら遷移なし=終了。
            return rule.else_to
        return random.choices(targets, weights=weights, k=1)[0]

    async def pause(self) -> None:
        if self.state["status"] != "playing":
            return
        self._paused = True
        for c in self._active_clocks:
            c.pause()
        if self._choice_clock:
            self._choice_clock.pause()
        # =256: BGMスロット(3)も一緒に止める
        for slot in (*_CH_SLOT.values(), _BGM_SLOT):
            try:
                pygame.mixer.Channel(slot).pause()
            except Exception:
                pass
        self.state["status"] = "paused"
        try:
            await self.intiface.stop_all()
        except Exception:
            pass
        # 動画再生中は mpv も一時停止(双方向同期のRVP→mpv側。
        # status を先に "paused" にしているため on_mpv_pause は無反応=ループしない)
        if self._video_clock is not None and self.mpv is not None:
            try:
                await self.mpv.set_pause(True)
            except Exception:
                pass

    async def resume(self) -> None:
        if self.state["status"] != "paused":
            return
        self._paused = False
        for c in self._active_clocks:
            c.resume()
        if self._choice_clock:
            self._choice_clock.resume()
        # =256: BGMスロット(3)も一緒に再開する
        for slot in (*_CH_SLOT.values(), _BGM_SLOT):
            try:
                pygame.mixer.Channel(slot).unpause()
            except Exception:
                pass
        # 再開もスマートスタート(停止中にデバイス位置がずれている可能性)
        self.start_soft_start()
        self.state["status"] = "playing"
        # 動画再生中は mpv も再開(RVP→mpv側)
        if self._video_clock is not None and self.mpv is not None:
            try:
                await self.mpv.set_pause(False)
            except Exception:
                pass

    async def stop(self) -> None:
        self._stop_requested = True
        for c in self._active_clocks:
            if c.paused:
                c.resume()
        self._paused = False
        self._stop_all_audio()

    def _reset_state_ready(self, scenario_title: str = "") -> None:
        """再生状態を「待機(ready)」へ戻す。イベント/ステート/音声/デバイス
        表示値をクリアし、新シナリオの再生待機として整える。"""
        # =69: グラフも空にして時間を0へ戻す。stop_and_reset は再生ループを
        # 抜けさせるためにクロックを resume するので、ここで基準時刻そのものを
        # 手放さないと「再生していないのにグラフが流れる」状態になる。
        self.graph_segments = []
        self._event_clock = None
        s = self.state
        s["status"] = "idle"
        s["scenario_title"] = scenario_title
        s["message"] = ""
        s["event_id"] = ""
        s["state_id"] = ""
        s["audio_file"] = ""
        s["channel_audio"] = {}
        s["elapsed_ms"] = 0
        s["duration_ms"] = 0
        s["event_elapsed_ms"] = 0
        s["state_elapsed_ms"] = 0
        s["linear_pos"] = 0
        s["linear_move"] = None
        s["twist_pos"] = 0
        s["twist_move"] = None
        s["choice"] = None
        s["choice_remaining_ms"] = None
        s["input"] = None
        s["vars"] = {}
        s["rotate_pos"] = 50
        s["rotate_a10_pos"] = 50
        s["rotate_ufo_ch"] = [(True, 0.0)]
        s["rotate_a10_ch"] = [(True, 0.0)]
        s["vibration_pos"] = 0
        s["device_channel"] = ""
        s["seek_channel"] = ""
        s["video_file"] = ""
        s["video_channel"] = ""
        s["event_trail"] = ()
        s["state_trail"] = ()
        s["run_log"] = ()

    async def stop_and_reset(self, scenario_title: str = "") -> None:
        """再生を停止し、状態を待機(ready)へリセットする。

        シナリオ履歴からの選択・編集画面の上書き保存など「別のシナリオを
        読み込む」際に呼ぶ。再生中/一時停止中でも音声・デバイスを止め、
        再生ループの終了を待ってから状態をリセットする(競合回避)。
        """
        self._stop_requested = True
        for c in self._active_clocks:
            if c.paused:
                c.resume()
        self._paused = False
        self._stop_all_audio()
        # =256: 再生ループ外(待機中など)から呼ばれてもBGMを確実に止める
        try:
            await self._bgm_stop(fade=False)
        except Exception:
            pass
        try:
            await self.intiface.stop_all()
        except Exception:
            pass
        # 再生ループ(play)の終了を待つ。最大~2秒でタイムアウト。
        for _ in range(200):
            if not self._playing:
                break
            await asyncio.sleep(0.01)
        self._reset_state_ready(scenario_title)

    # ================= linear安全機構 =================

    SOFT_START_SEC = 2.0       # スマートスタート期間
    SOFT_MIN_FRAC = 0.15       # 期間開始直後の速度上限(通常上限に対する割合)
    SOFT_BASE_MS = 250         # 速度制限「なし」設定時に期間中だけ使う基準時間

    def start_soft_start(self) -> None:
        """スマートスタートを開始する(2秒かけて速度上限を通常へ戻す)。

        再生開始/再開/シーク/イベントジャンプなど、再生位置が不連続に
        飛ぶ操作の直後に呼ぶ。速度制限「なし」設定でも期間中は機能する。
        """
        self._soft_until = time.monotonic() + self.SOFT_START_SEC

    def _current_speed_limit_ms(self) -> float:
        """現在有効なフルストローク最短時間(ms)を返す。0以下=制限なし。

        スマートスタート期間中は上限速度をSOFT_MIN_FRAC倍から線形に
        通常値へ戻す(最短時間としては 基準/割合 で長くなる)。
        """
        limit = self.linear_speed_limit_ms
        remain = self._soft_until - time.monotonic()
        if remain > 0:
            base = limit if limit > 0 else self.SOFT_BASE_MS
            frac = max(self.SOFT_MIN_FRAC, 1.0 - remain / self.SOFT_START_SEC)
            return base / frac
        return limit

    def _estimate_device_pos(self) -> float | None:
        """直近の送信履歴からデバイスの現在位置(実座標0-100)を推定する。

        進行中の移動は線形補間、完了後は到達位置。不明ならNone。
        """
        return self._estimate_axis(self._dev_move, self._dev_known_pos)

    @staticmethod
    def _estimate_axis(mv: dict | None, known: float | None) -> float | None:
        """軸の送信履歴(move/known)から現在位置を推定する(=79で汎用化)。"""
        if mv:
            frac = (time.monotonic() - mv["t0"]) / max(mv["dur"], 0.001)
            frac = max(0.0, min(1.0, frac))
            return mv["from"] + (mv["to"] - mv["from"]) * frac
        return known

    def _limit_linear_duration(self, duration_ms: int,
                               device_target: float) -> int:
        """速度制限を適用した所要時間(ms)を返す。

        移動距離(実座標)に比例した最短時間を下回る指示は引き伸ばす
        (移動は行うが最高速度以下になる)。現在位置が不明な場合は
        フルストローク相当の距離とみなして保守的に制限する。
        """
        return self._limit_axis_duration(duration_ms, device_target,
                                         self._estimate_device_pos())

    def _limit_axis_duration(self, duration_ms: int, device_target: float,
                             est: float | None) -> int:
        """軸の推定現在位置から速度制限後の所要時間を返す(=79で汎用化)。

        急動作防止の設定(linear_speed_limit_ms+スマートスタート)は
        linear/twist共通(ユーザー決定=79)。
        """
        limit = self._current_speed_limit_ms()
        if limit <= 0:
            return duration_ms
        dist = 100.0 if est is None else abs(device_target - est)
        return max(duration_ms, int(dist / 100.0 * limit))

    def _tcode_active(self) -> bool:
        """TCode直接出力(=80)が接続中か。接続中は linear/twist をそちらへ送る。"""
        return self.tcode is not None and getattr(self.tcode, "connected", False)

    def _device_link(self) -> bool:
        """linear/twist の送信先が1つでも生きているか(Intiface or TCode)。"""
        return self.intiface.connected or self._tcode_active()

    async def _send_linear_pos(self, raw_pos: int, duration_ms: int) -> None:
        """funscript座標の pos を駆動区間へ写して送り、表示用の状態も更新する。

        =71: funscript実行とレンジ調整時の追従で共用する。安全機構(速度制限+
        スマートスタート)はデバイス実座標(invert適用後)の移動距離で測る。
        =80: TCode接続中は Intiface の代わりに L0 軸へ直接送る(補正・
        安全機構・表示は完全に共通=最終段の送信だけが替わる)。
        """
        mapped_to = self.intiface.map_pos(raw_pos)
        invert = bool(getattr(self.intiface, "invert", False))
        # =86: 反転は区間内での折り返し(min+max - x)。自己逆写像なので、
        # デバイス座標→funscript座標の逆変換(ui_from)にも同じ式を使える。
        rlo = getattr(self.intiface, "range_min", 0)
        rhi = getattr(self.intiface, "range_max", 100)
        device_to = (rlo + rhi - mapped_to) if invert else mapped_to
        dev_from = self._estimate_device_pos()
        duration = self._limit_linear_duration(max(int(duration_ms), 20),
                                               device_to)
        if self._tcode_active():
            await self.tcode.send_axis("L0", device_to, duration)
        else:
            await self.intiface.send_linear(duration, raw_pos)
        now = time.monotonic()
        self._dev_move = {
            "from": dev_from if dev_from is not None else device_to,
            "to": device_to, "t0": now, "dur": duration / 1000.0,
        }
        self._dev_known_pos = device_to
        # UI表示はfunscript座標系(現在のinvert設定で戻す)
        ui_from = mapped_to if dev_from is None else \
            ((rlo + rhi - dev_from) if invert else dev_from)
        self.state["linear_move"] = {
            "from": ui_from, "to": mapped_to, "t0": now,
            "dur": duration / 1000.0,
        }
        self.state["linear_pos"] = mapped_to
        # レンジ調整の追従(=71)で使うため、写す前の生の pos を覚えておく
        self._last_linear_raw = raw_pos

    async def retarget_linear(self) -> bool:
        """駆動区間・反転の変更にあわせて、停止中のデバイスを追従させる(=71)。

        一度も指示を送っていない間はデバイスの実位置が分からない
        (buttplugは位置を読み出せない)ので**何もしない**。所要時間は
        急動作防止に任せる(大きく動くときほどゆっくり動く)。
        """
        raw = self._last_linear_raw
        if raw is None or not self._device_link():
            return False
        try:
            await self._send_linear_pos(raw, 20)
        except Exception:
            logger.warning(tr("linear位置の追従に失敗しました"))
            return False
        return True

    async def _send_twist_pos(self, raw_pos: int, duration_ms: int) -> None:
        """TWIST軸へfunscript座標の pos を送り、表示用の状態も更新する(=79)。

        _send_linear_pos のtwist版。急動作防止(速度制限+スマートスタート)は
        linearと共通設定を、twist軸自身の移動距離で適用する。
        """
        iface = self.intiface
        mapped_to = iface.map_twist_pos(raw_pos)
        invert = bool(getattr(iface, "twist_invert", False))
        # =86: 反転は区間内での折り返し(_send_linear_pos と同じ)
        tlo = getattr(iface, "twist_min", 0)
        thi = getattr(iface, "twist_max", 100)
        device_to = (tlo + thi - mapped_to) if invert else mapped_to
        dev_from = self._estimate_axis(self._twist_move, self._twist_known_pos)
        duration = self._limit_axis_duration(max(int(duration_ms), 20),
                                             device_to, dev_from)
        if self._tcode_active():
            await self.tcode.send_axis("R0", device_to, duration)
        else:
            await iface.send_twist(duration, raw_pos)
        now = time.monotonic()
        self._twist_move = {
            "from": dev_from if dev_from is not None else device_to,
            "to": device_to, "t0": now, "dur": duration / 1000.0,
        }
        self._twist_known_pos = device_to
        ui_from = mapped_to if dev_from is None else \
            ((tlo + thi - dev_from) if invert else dev_from)
        self.state["twist_move"] = {
            "from": ui_from, "to": mapped_to, "t0": now,
            "dur": duration / 1000.0,
        }
        self.state["twist_pos"] = mapped_to
        self._last_twist_raw = raw_pos

    async def retarget_twist(self) -> bool:
        """TWISTの駆動区間・反転の変更にあわせた停止中の追従(=79。=71と同型)。"""
        raw = self._last_twist_raw
        if raw is None or not self._device_link():
            return False
        try:
            await self._send_twist_pos(raw, 20)
        except Exception:
            logger.warning(tr("twist位置の追従に失敗しました"))
            return False
        return True

    def set_master_volume(self, volume: float) -> None:
        """マスター音量(0.0〜1.0)を設定し、再生中の音声へ即時反映する。

        各チャンネルのパン(左右バランス)に乗算して適用する。
        pygameのChannel.set_volumeは軽量なため、UIスレッドから直接呼んでよい。
        設定はUI側でコンフィグ保存され、次回起動時に復元される。
        """
        volume = max(0.0, min(1.0, float(volume)))
        self.master_volume = volume
        for slot, (left, right) in list(self._slot_pan.items()):
            try:
                pygame.mixer.Channel(slot).set_volume(left * volume,
                                                      right * volume)
            except Exception:
                pass

    async def seek_relative(self, delta_ms: int) -> None:
        """現在の再生位置から相対シークする(10秒送り/戻しボタン用)。

        先頭より前は0、末尾より後は末尾付近にクランプされる(seekと同じ)。
        音声も実際の途中位置から再生される。
        """
        if self.state["status"] not in ("playing", "paused"):
            return
        await self.seek(int(self.state["elapsed_ms"]) + int(delta_ms))

    @staticmethod
    def _sliced_sound(sound, ms: float, end_ms: float | None = None):
        """途中位置(ms)から(end_msまで)のSoundを作る。

        Soundのrawデータはmixer出力形式へ変換済みのため、
        サンプル位置でバイト列を切り出せば正確な途中再生になる。
        end_ms は=59の区間指定で使う(None=末尾まで)。
        =249: get_raw()(全体をbytesへ複製)をやめ、ゼロコピーの
        memoryview経由に変更。巨大な音声でもコピーは新Soundの
        切り出し範囲1回だけになる(以前はシークのたびに全体×2〜3)。
        """
        init = pygame.mixer.get_init()
        if not init:
            return None
        freq, fmt, channels = init
        frame_bytes = abs(fmt) // 8 * channels
        raw = sound_byte_view(sound)
        off = int(freq * ms / 1000.0) * frame_bytes
        if off >= len(raw):
            off = max(0, len(raw) - frame_bytes)
        if end_ms is None:
            return pygame.mixer.Sound(buffer=raw[off:])
        end = int(freq * end_ms / 1000.0) * frame_bytes
        end = max(off + frame_bytes, min(end, len(raw)))
        return pygame.mixer.Sound(buffer=raw[off:end])

    @staticmethod
    def _rated_sound(sound, rate: float):
        """=214: 速度 rate で鳴らすための Sound(リサンプリング済み)。
        rate=1.0 は元の Sound をそのまま返す。mixer 未初期化は None。"""
        init = pygame.mixer.get_init()
        if not init:
            return None
        if abs(float(rate) - 1.0) < 1e-9:
            return sound
        freq, fmt, channels = init
        frame_bytes = abs(fmt) // 8 * channels
        # =249: get_raw()の全複製をやめ、ゼロコピーのビューから読む
        raw = resample_frames(sound_byte_view(sound), frame_bytes, rate)
        if not raw:
            return None
        return pygame.mixer.Sound(buffer=raw)

    def _load_track_src(self, item, track, rotate: bool = False):
        """トラックのスクリプトを読み込み、区間指定(=59)を適用して返す。

        適用する区間は**トラック個別 > アイテム**(`track_range_ms`)。
        `sliced()` が区間の先頭を0msへずらすので、実行側(_run_funscript 等)は
        クロックをそのまま使えて無改修で済む。
        """
        if rotate:
            src = self._load_rotate_safe(track.funscript)
        else:
            src = Funscript.load(track.funscript)
        if src is None:
            return None
        lo, hi = track_range_ms(item, track)
        if lo > 0 or hi is not None:
            src = src.sliced(lo, hi)
        return src

    async def seek(self, ms: int) -> None:
        """シークバー追従チャンネルの音声をシークする。

        音声は実際の途中位置(ms)から再生される(rawバイト切り出し方式)。
        """
        if self.state["status"] not in ("playing", "paused"):
            return
        duration = self.state["duration_ms"]
        if duration <= 0:
            return
        # シーク(シークバー・10秒送り/戻し)はスマートスタート対象
        self.start_soft_start()
        ms = max(0, min(int(ms), max(duration - 200, 0)))
        # 動画イベント中はシークを mpv へ転送する(シークバー=動画に固定)。
        # クロックは即時 set_ms し、以後は time-pos のドリフト補正が維持する。
        if self._video_clock is not None and self.mpv is not None:
            try:
                # 区間指定(=51): シークバーは区間内の相対位置なので、
                # mpv へは区間の開始秒を足した絶対位置を渡す
                await self.mpv.seek(self._video_start_s + ms / 1000.0)
            except Exception as e:
                logger.warning(tr("シーク失敗: %s"), e)
                return
            self._video_clock.set_ms(ms)
            self.state["elapsed_ms"] = ms
            self._fs_resync = True
            return
        if self._seek_script:
            # =63: スクリプト専用チャンネル追従。音声が無いのでクロックを
            # 動かすだけでよい(funscript は _fs_resync で追従する)。
            self.clock.set_ms(ms)
            self.state["elapsed_ms"] = ms
            self._fs_resync = True
            return
        slot = _CH_SLOT.get(self.state["seek_channel"]
                            or self.state["device_channel"], 1)
        ch = pygame.mixer.Channel(slot)
        # 元の(完全な)音声から切り出す。連続シークでも劣化しないよう、
        # 再生中のスライスではなくアイテム開始時に記録した原音を使う
        snd = self._slot_sound.get(slot) or ch.get_sound()
        if snd is None:
            return
        was_paused = self.clock.paused
        try:
            sliced = self._sliced_sound(snd, ms)
            if sliced is None:
                return
            ch.play(sliced)
            # 音量(パン×マスター)はチャンネルに再適用する
            pan = self._slot_pan.get(slot)
            if pan:
                ch.set_volume(pan[0] * self.master_volume,
                              pan[1] * self.master_volume)
        except Exception as e:
            logger.warning(tr("シーク失敗: %s"), e)
            return
        if was_paused:
            ch.pause()
        self.clock.set_ms(ms)
        self.state["elapsed_ms"] = ms
        self._fs_resync = True

    def live_elapsed_ms(self) -> int:
        """=286: 表示用の滑らかな経過(ms)。

        state["elapsed_ms"] は再生ループの30ms刻みでしか更新されず、UIの
        60fps描画にはカクつくため、追従中の時計(動画なら _video_clock、
        音声なら primary の self.clock)から直接読む。状態辞書の値と大きく
        食い違う(別アイテムの時計・インターバル中など)ときは状態辞書を信じる。
        """
        st = self.state
        base = int(st.get("elapsed_ms", 0))
        dur = int(st.get("duration_ms", 0))
        if st.get("status") != "playing" or dur <= 0:
            return base
        try:
            clk = self._video_clock if self._video_clock is not None \
                else self.clock
            ms = int(clk.now_ms())
        except Exception:
            return base
        if ms < 0 or abs(ms - base) > 500:
            return base
        return min(ms, dur)

    async def skip_event(self) -> None:
        await self._request_jump("next")

    async def back_event(self) -> None:
        await self._request_jump("back")

    def _state_choice_stay_active(self) -> bool:
        """=275: 選択必須(skip=stay)のステート移行選択肢が表示中か。"""
        return (self.state["choice"] is not None
                and self._choice_owner == "state"
                and bool(self._sc_rule is not None and self._sc_rule.skip_stay))

    async def _request_jump(self, direction: str) -> None:
        if self.state["status"] not in ("playing", "paused"):
            return
        if direction == "next" and self._state_choice_stay_active():
            # =275: ステート移行の選択肢が「選択必須」で表示中は ▶▶ を無効に
            # する(音声も止めない。イベント側の =274 と違い、ステートは
            # 選ばれるまでそのまま再生を続ける)
            self._log("choice",
                      tr("▶▶は無効: 選択されるまで待機します(選択必須)"))
            return
        # イベント巻き戻し/スキップはスマートスタート対象
        self.start_soft_start()
        self._jump = direction
        # 一時停止中のジャンプは一時停止状態を維持する。
        # ジャンプ先イベントの先頭で止まったまま待機し、再生ボタンで再生開始する。
        self._stop_all_audio()

    @property
    def is_playing(self) -> bool:
        return self._playing

    def _stop_all_audio(self) -> None:
        for slot in _CH_SLOT.values():
            try:
                pygame.mixer.Channel(slot).stop()
            except Exception:
                pass

    # ================= BGM(=256) =================
    #
    # BGMはステート/イベントのライフサイクルから独立した専用スロット
    # (_BGM_SLOT=3)で鳴らす。_stop_all_audio(スロット0〜2)の対象外なので、
    # イベント境界の全音声停止を生き延びる。ノード入場時に _apply_bgm が
    # 「指定/引き継ぐ/オフ」を判定するだけで、「引き継ぐ」は何もしない
    # ことで実現する(遷移パターンを個別に扱う必要がない)。

    def _bgm_on(self) -> bool:
        """シナリオのBGM機能フラグ(bgm_enabled)。属性ごと無い場合はFalse。"""
        return bool(getattr(getattr(self, "scenario", None),
                            "bgm_enabled", False))

    def _bgm_load(self, path: str):
        """BGM音声を読み込む(同一シナリオ再生中はキャッシュ)。

        失敗は None を返し、警告ログへ原因(=249のメモリ系ガイダンス含む)を
        添える。
        """
        snd = self._bgm_sounds.get(path)
        if snd is not None:
            return snd
        try:
            snd = pygame.mixer.Sound(path)
        except Exception as e:
            logger.error(tr("BGMの読み込み失敗: %s (%s)"), path, e)
            extra = describe_audio_load_error(path, e)
            self._log("warn",
                      tr("BGMの読み込みに失敗しました: {0}").format(
                          os.path.basename(path))
                      + ((" " + extra) if extra else ""))
            return None
        self._bgm_sounds[path] = snd
        return snd

    def _bgm_set_pan(self, item, spec) -> None:
        """再生中BGMのパンを適用する(item個別 > BGM既定 > 等倍)。

        _slot_pan へ登録するので、以後の set_master_volume にも追従する。
        """
        pan = item.pan or spec.pan
        left, right = (pan.left, pan.right) if pan is not None else (1.0, 1.0)
        self._slot_pan[_BGM_SLOT] = (left, right)
        try:
            pygame.mixer.Channel(_BGM_SLOT).set_volume(
                left * self.master_volume, right * self.master_volume)
        except Exception:
            pass

    async def _apply_bgm(self, spec) -> None:
        """ノード(ステート)入場時のBGM適用(=256)。

        spec=None は「前のBGMを引き継ぐ」= 何もしない(鳴りっぱなし)。
        "off" は停止、"set" は**同じ構成でも必ず先頭から再生し直す**(Q3)。
        bgm_enabled=False のシナリオでは定義があっても一切鳴らさない
        (device_enabled と同じ「データは保持・出力だけ抑制」方式)。
        """
        if spec is None or not self._bgm_on():
            return
        was = self._bgm_active
        await self._bgm_stop(fade=True)
        if spec.mode == "off":
            if was:
                self._log("bgm", tr("BGM停止"))
            return
        self._bgm_start(spec)

    def _bgm_start(self, spec) -> None:
        """BGM再生を開始する(読み込めない曲はスキップ・全滅なら警告のみ)。"""
        items = [it for it in spec.items
                 if self._bgm_load(it.audio) is not None]
        if not items:
            self._log("warn", tr("BGMを再生できません(読み込めた音声がありません)"))
            return
        self._bgm_gen += 1
        self._bgm_active = True
        self._log("bgm", tr("BGM開始: {0}").format(spec.describe()))
        if len(items) == 1:
            snd = self._bgm_load(items[0].audio)
            self._bgm_set_pan(items[0], spec)
            try:
                ch = pygame.mixer.Channel(_BGM_SLOT)
                ch.play(snd, loops=-1)   # 1曲はミキサー任せのギャップレスループ
                if self._paused:
                    ch.pause()
            except Exception:
                pass
            return
        self._bgm_task = asyncio.ensure_future(self._bgm_feed(items, spec))

    def _bgm_iter(self, items, order):
        """BGMの再生順を無限に生成する。

        順番(sequential)=リストを順に周回。ランダム(random)=袋方式
        (1周分をシャッフルして出し切り、周回の境目で同じ曲が連続しない
        よう先頭を入れ替える=Q4)。
        """
        import random as _r
        if order != MODE_RANDOM or len(items) == 1:
            i = 0
            while True:
                yield items[i % len(items)]
                i += 1
        last = None
        while True:
            bag = list(items)
            _r.shuffle(bag)
            if len(bag) >= 2 and bag[0] is last:
                bag[0], bag[-1] = bag[-1], bag[0]
            for it in bag:
                yield it
                last = it

    def _bgm_queue_sound(self, item, cur_snd):
        """予約用のSoundを返す。直前と同一オブジェクトなら独立品を作る。

        切替検知は get_sound() のオブジェクト比較で行うため、同じファイルを
        連続で並べるとキャッシュの同一Soundでは切替が見えない。その場合だけ
        キャッシュ外の独立したSoundを読み直して予約する。
        """
        snd = self._bgm_load(item.audio)
        if snd is None:
            return None
        if snd is cur_snd:
            try:
                snd = pygame.mixer.Sound(item.audio)
            except Exception:
                pass
        return snd

    async def _bgm_feed(self, items, spec) -> None:
        """複数曲BGMの送り込みループ(=256)。

        pygameの Channel.queue() で次曲を予約し、再生中Soundの入れ替わり
        (get_sound() のオブジェクト変化)を検知したら「その曲のパン適用+
        さらに次を予約」する。切り替えはミキサー側で行われるためほぼ
        ギャップレス。**get_queued() は pygame-ce 2.5系に存在しない**ので
        使わない(コンテナ実測: AttributeError)。
        """
        ch = pygame.mixer.Channel(_BGM_SLOT)
        it = self._bgm_iter(items, spec.order)
        cur = next(it)
        nxt = next(it)
        cur_snd = self._bgm_load(cur.audio)
        nxt_snd = self._bgm_queue_sound(nxt, cur_snd)
        self._bgm_set_pan(cur, spec)
        try:
            ch.play(cur_snd)
            if nxt_snd is not None:
                ch.queue(nxt_snd)
            if self._paused:
                ch.pause()
        except Exception:
            return
        while True:
            await asyncio.sleep(0.1)
            try:
                busy = ch.get_busy()
                snd = ch.get_sound()
            except Exception:
                return
            if self._paused:
                continue
            if not busy:
                # 送り込みが間に合わず完全に止まった(重い処理等)。次曲から
                # 鳴らし直す
                cur, cur_snd = nxt, nxt_snd
                nxt = next(it)
                nxt_snd = self._bgm_queue_sound(nxt, cur_snd)
                self._bgm_set_pan(cur, spec)
                try:
                    ch.play(cur_snd)
                    if nxt_snd is not None:
                        ch.queue(nxt_snd)
                except Exception:
                    return
            elif snd is not cur_snd:
                # 予約曲へ切り替わった: パン適用+さらに次を予約
                cur, cur_snd = nxt, nxt_snd
                nxt = next(it)
                nxt_snd = self._bgm_queue_sound(nxt, cur_snd)
                self._bgm_set_pan(cur, spec)
                try:
                    if nxt_snd is not None:
                        ch.queue(nxt_snd)
                except Exception:
                    return

    async def _bgm_stop(self, fade: bool = True) -> None:
        """BGMを停止する(=256)。fade=True で300msフェードアウト。

        フェード後に残る予約曲(queue)を掃除するため、フェード完了の少し後に
        stop() を撃つ小タスクを流す。その間に新しいBGMが始まった場合は
        世代カウンタ(_bgm_gen)の不一致で何もしない。
        """
        task, self._bgm_task = self._bgm_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._bgm_active = False
        self._bgm_gen += 1
        gen = self._bgm_gen
        self._slot_pan.pop(_BGM_SLOT, None)
        try:
            ch = pygame.mixer.Channel(_BGM_SLOT)
            if fade and ch.get_busy() and not self._paused:
                ch.fadeout(BGM_FADEOUT_MS)

                async def _kill():
                    await asyncio.sleep(BGM_FADEOUT_MS / 1000 + 0.05)
                    if self._bgm_gen == gen:   # 新しいBGMが始まっていない
                        try:
                            pygame.mixer.Channel(_BGM_SLOT).stop()
                        except Exception:
                            pass

                asyncio.ensure_future(_kill())
            else:
                ch.stop()
        except Exception:
            pass

    # ================= イベント再生 =================

    async def _play_event(self, event) -> str | None:
        """イベントをステートマシンとして再生する。

        イベントレベルの累積(滞在時間クロック・合計実行回数・移行回数)は
        ステートを何度移行してもリセットされない。終了条件(duration/plays/
        transitions/channel)の成立でワインドダウンし、次のイベントへ進む。

        戻り値: "next"/"back"(ジャンプ) / None(自然終了・停止)
        """
        self.state["event_id"] = event.event_id
        self.state["event_elapsed_ms"] = 0
        self.state["state_elapsed_ms"] = 0
        self.state["linear_move"] = None
        self.state["channel_audio"] = {}
        # =69: グラフの横軸はイベント入場が0秒。過去の波形もイベント単位で捨てる
        self.graph_segments = []
        # ステート移行トレイルはイベント入場(◀◀での再入場含む)でリセット
        self.state["state_trail"] = ()
        # イベントログへ記録(◀◀/gotoでの再入場も1回として追記される)。
        # 終了条件は解決後(=56)に追記するので、ここでは仮に名前だけ入れる。
        self.state["run_log"] = \
            self.state["run_log"] + (("event", event.event_id),)
        self._winding_down = False
        self._transition_winding = False
        self._state_choice_event_to = None
        self._sc_rule = None
        self.state["video_file"] = ""   # 動画chの入場で再設定される
        self.state["video_channel"] = ""
        # イベント開始時の変数操作(◀◀での再実行でも発火する)。
        # 操作が無くても、入場時点で監視(watch)条件が成立していれば即発火する
        self._apply_ops(event.on_start, tr("イベント開始時"))
        self._check_watches()
        # 終了条件の数値をこの時点の変数値で解決して固定
        # (直前イベントの選択肢opsやon_startでセットした値が反映される)
        self._ev_end_count, self._ev_end_ms = event.resolve_end(self.vars)
        # 解決後の終了条件をログのイベント行へ反映する(=56)。
        # 「今回は87秒」のような抽選結果まで見えるようにするのが目的。
        log = self.state["run_log"]
        if log and log[-1][0] == "event":
            self.state["run_log"] = log[:-1] + (
                ("event", tr("{0}  [終了条件: {1}]").format(
                    event.event_id,
                    event.describe_end_resolved(self._ev_end_count,
                                                self._ev_end_ms))),)

        # イベントレベルの累積カウンタ
        event_clock = PlaybackClock()
        event_clock.start()
        if self._paused:
            event_clock.pause()
        self._event_clock = event_clock   # =69 グラフの時間基準
        self._event_plays = 0       # 全チャンネル合計の実行回数
        transitions = 0             # ステート移行回数

        self._clear_choice()
        self._clear_input()
        choice_task = None
        if event.next_choice:
            choice_task = asyncio.ensure_future(
                self._monitor_choice(event, event_clock))
        elif event.next_input:
            choice_task = asyncio.ensure_future(
                self._monitor_input(event, event_clock))
        elif event.end_type == END_NONE and event.next_cond:
            # =168: 「判定式(常に監視)」= 無限のイベントの出口。再生中に
            # 判定式を評価し、成立したらイベントを打ち切って分岐する
            choice_task = asyncio.ensure_future(self._monitor_cond(event))

        state_id = event.start_state
        # =77: 実行済みステートの履歴はイベントに入るたびリセット(ユーザー
        # 決定)。◀◀での再入も新しいイベント実行なのでまっさらから始まる。
        self._visited_states = set()
        try:
            while not self._stop_requested and self._jump is None:
                st = event.states[state_id]
                self._visited_states.add(state_id)
                outcome = await self._play_state(event, st, event_clock)
                if outcome is None:
                    break  # 停止/ジャンプ
                if self._watch_goto is not None:
                    break  # watch成立(graceful含む) → イベント終了して遷移
                if outcome == "__event_end__":
                    break
                # 指定ステートが終了したらイベント終了(そのステートの再生完了後)
                if (event.end_type == END_STATES
                        and state_id in event.end_states):
                    self._log("end", tr("イベント終了: ステート{0}が終了").format(
                        state_id))
                    break
                # ステート移行
                transitions += 1
                if (event.end_type == END_TRANSITIONS
                        and transitions >= self._ev_end_count):
                    self._log("end",
                              tr("イベント終了: ステート移行{0}回に到達").format(
                                  transitions))
                    break
                # 実際に移行するときだけトレイルへ (from, to) を積む
                self.state["state_trail"] = \
                    self.state["state_trail"] + ((state_id, outcome),)
                state_id = outcome
        finally:
            self._winding_down = False
            self._active_clocks = []
            self._stop_all_audio()
            if choice_task:
                choice_task.cancel()
                try:
                    await choice_task
                except asyncio.CancelledError:
                    pass
            try:
                await self.intiface.stop_all()
            except Exception:
                pass

        if self._jump:
            if (self._jump == "next" and event.next_choice is not None
                    and event.next_choice.skip_stay
                    and not self._stop_requested):
                # =274: 選択必須(skip=stay)の選択肢イベントでは、音声再生中の
                # ▶▶は「音声を打ち切って選択肢の待機へ進む」だけで、選択肢は
                # 飛ばさない(このまま下の自然終了フローへ落ちる=on_endも
                # 発火し、_wait_choice で選択/タイムアウトを待つ)。
                self._jump = None
                self._log("choice",
                          tr("▶▶: 音声を打ち切って選択肢の待機へ(選択必須)"))
            elif (self._jump == "next" and event.next_input is not None
                    and event.next_input.skip_stay
                    and not self._stop_requested):
                # =288: 入力必須(skip=stay)の数値入力も同じ(音声を打ち切って
                # 入力の待機へ。_wait_input で入力を待つ)
                self._jump = None
                self._log("choice",
                          tr("▶▶: 音声を打ち切って数値入力の待機へ(入力必須)"))
            else:
                jump, self._jump = self._jump, None
                # =57: 手動操作(▶▶/◀◀)も遷移理由としてログへ残す(ユーザー要望)。
                # tuple の ("goto", to) は選択肢/watch 側で理由を記録済みなので
                # ここでは触らない。
                self._log_manual_jump(jump)
                return jump
        # イベント自然終了時の変数操作(停止・手動▶▶/◀◀では上の return で発火しない)。
        # graceful watch/終了条件成立など自然に抜けた場合は発火する
        # (on_complete と同じ流儀)。ops が空なら _apply_ops は即 return=挙動不変。
        if not self._stop_requested:
            self._apply_ops(event.on_end, tr("イベント終了時"))
        # 監視(watch)のgraceful発火は選択肢の待機より優先して遷移する
        if self._watch_goto and not self._stop_requested:
            to, self._watch_goto = self._watch_goto, None
            return ("goto", to)
        # =275: ステート移行の選択肢で「イベント宛て」が選ばれた。イベントは
        # 自然終了(on_end 発火済み)として、そのイベントへ飛ぶ(イベント側の
        # 選択肢/数値入力の待機は挟まない)
        if self._state_choice_event_to and not self._stop_requested:
            to, self._state_choice_event_to = self._state_choice_event_to, None
            return ("goto", to)
        # 自然終了: 選択肢/数値入力が未解決なら待機フェーズ(無音で待つ)
        if (event.next_choice or event.next_input) and not self._stop_requested:
            # 待機=人の操作を挟むので、音声なしノードの連続通過カウンタを解除
            self._silent_streak = 0
            if event.next_choice:
                to = await self._wait_choice(event)
            else:
                to = await self._wait_input(event)
            if self._jump == "back":
                # 待機中の◀◀は履歴どおり一つ前のイベントへ。
                # ここで"back"を返さないと、経由イベントが_visited_eventsに
                # 積まれてしまう(visited: excludeの抽選を汚染する)
                self._jump = None
                self._log_manual_jump("back")
                return "back"
            if isinstance(self._jump, tuple):
                # 待機中に監視(watch)がinterrupt発火した場合は選択肢より優先
                jump, self._jump = self._jump, None
                return jump
            if self._watch_goto:
                to2, self._watch_goto = self._watch_goto, None
                return ("goto", to2)
            if to is not None:
                return ("goto", to)
        return None

    async def _play_state(self, event, st, event_clock) -> str | None:
        """ステート1つを移行条件/イベント終了条件の成立まで再生する。

        戻り値: 移行先ステートID / "__event_end__" / None(停止・ジャンプ)
        """
        self.state["state_id"] = st.state_id if event.is_multi_state else ""
        self.state["channel_audio"] = {}   # =286: ステート入場で表示をリセット
        if event.is_multi_state:
            # イベントログへ記録(通常イベントの内部"main"ステートは記録しない)
            label = st.state_id
            if st.transition is not None:
                label = tr("{0}  [移行条件: {1}]").format(
                    label, st.transition.describe())
            self._log("state", label)
        self.state["device_channel"] = st.device_channel
        self.state["seek_channel"] = st.seek_follow_channel()

        # ステート開始時の変数操作(再入のたびに発火する)
        self._apply_ops(st.on_start, tr("ステート開始時"))

        # =256: BGMの適用(指定=先頭から再生し直し / 引き継ぐ=何もしない /
        # オフ=停止)。通常イベントは "main" ステートの入場がイベント入場。
        await self._apply_bgm(st.bgm)

        # ステート滞在時間クロック(再入でゼロから。一時停止で他クロックと一括停止)
        state_clock = PlaybackClock()
        state_clock.start()
        if self._paused:
            state_clock.pause()
        self.state["state_elapsed_ms"] = 0

        # 移行閾値はステートに入るたびに抽選し直す
        threshold = st.transition.pick_threshold(self.vars) if st.transition else None
        # =275: 選択肢でステート移行。表示タイミング(start/ms/end=全チャンネル
        # 終了)で表示し、選択/タイムアウトで**即座に**(音声を打ち切って)
        # 移行する。チャンネルが全部終わっても選ばれるまで無音で待つ。
        # イベント終了条件が成立したら選択肢は待たず閉じる(ユーザー決定)。
        sc_rule = (st.transition.choice
                   if st.transition is not None
                   and st.transition.when_type == WHEN_CHOICE else None)
        self._sc_rule = sc_rule
        sc_shown = False
        sc_resolved = None      # (to, to_event) 選択/タイムアウトで確定

        # ステート内のチャンネル別統計(時間はインターバル込み、再入時ゼロから)
        stats = {ch_id: {"time_ms": 0.0, "count": 0} for ch_id in st.channels}

        # チャンネルごとの担当デバイス種別
        ch_device_types = {ch_id: set() for ch_id in st.channels}
        for ttype, ch_id in st.device_map.items():
            if ch_id in ch_device_types:
                ch_device_types[ch_id].add(ttype)
        # =239: 「どこかのチャンネルが担当している種別」。動画のトラックは
        # **担当が別のチャンネルにある種別だけ鳴らさない**(担当なし=従来どおり
        # 動画が鳴らす)。これで動画つきイベントでも linear などを
        # 音声チャンネルへ付け替えられる。
        self._assigned_types = {t for t, c in st.device_map.items()
                                if c in st.channels}

        # ---- 動画(外部mpv)のセットアップ(=52 フェーズ3-B) ----
        # 動画は「動画アイテムを持つチャンネル」になったので、専用の再生系は
        # 持たない。動画chは seek_follow_channel が必ず主担当に選ぶため、
        # そのチャンネルクロックがそのまま動画クロック(mpv従属)になる。
        has_video = st.has_video
        self.state["video_channel"] = st.video_channel
        if has_video:
            await self._ensure_mpv()
            self.state["duration_ms"] = 0   # mpvのduration到着で更新
            self.state["elapsed_ms"] = 0

        # シークバー追従チャンネル(動画ch > 指定 > 自動C→L→R)を主担当にする。
        # デバイス駆動は ch_device_types(device_map)側で決まるため影響しない。
        primary = self.state["seek_channel"]
        # =63: 追従先がスクリプト専用チャンネル(音声なし)のときは、シーク時に
        # 切り出す音声が無いのでクロックだけを動かす(funscriptは再同期する)。
        pch = st.channels.get(primary)
        self._seek_script = bool(pch is not None and not pch.has_audio
                                 and not pch.has_video)

        # クロック管理(イベント/ステートクロック + 担当チャンネルクロック)
        self._active_clocks = [event_clock, state_clock]

        # 各チャンネルの「解釈後の設定」をログへ(=56)。JSONを読まなくても
        # 再生方式・終了条件・アイテム数・担当デバイスが目で確かめられる。
        # =99: resolve_end はここで1回だけ呼び、結果を _play_channel へ渡す
        # (範囲抽選が2回引かれてログと実挙動がずれるのを防ぐ)。
        ch_end_resolved = {}
        for ch_id in sorted(st.channels):
            channel = st.channels[ch_id]
            ch_end_resolved[ch_id] = channel.resolve_end(self.vars)
            desc = channel.describe(*ch_end_resolved[ch_id])
            extra = []
            dt = sorted(ch_device_types.get(ch_id, set()))
            if dt:
                extra.append(tr("{0}担当").format("/".join(dt)))
            if ch_id == primary:
                extra.append(tr("シークバー追従"))
            if extra:
                desc = f"{desc}  ({'・'.join(extra)})"
            self._log("chan", f"{ch_id}: {desc}")

        tasks = {}
        for ch_id, channel in st.channels.items():
            dtypes = ch_device_types.get(ch_id, set())
            is_primary = (ch_id == primary)
            clock = None
            if dtypes or is_primary or channel.has_video:
                clock = PlaybackClock()
                self._active_clocks.append(clock)
            if is_primary and clock is not None:
                self.clock = clock
            if channel.has_video:
                # このクロックを mpv の time-pos に従属させる(_on_mpv_time_pos)
                self._video_clock = clock
            tasks[ch_id] = asyncio.ensure_future(
                self._play_channel(channel, dtypes, clock, is_primary,
                                   stats[ch_id],
                                   end_resolved=ch_end_resolved.get(ch_id))
            )

        pending = None  # "transition" / "event_end"
        finished: set = set()   # 自然終了したチャンネルID(累積)
        # =56: 「なぜ次へ進んだか」をログに残すための理由文字列(1要素リスト=
        # 内側の関数から書き換えるため)
        reason = [""]

        def transition_met() -> bool:
            if st.transition is None:
                return False
            wt = st.transition.when_type
            if wt == WHEN_CHOICE:
                return False   # =275: 選択肢はループ内で別途解決する
            if wt == WHEN_COND:
                met = bool(st.transition.conds) and all(
                    c.eval(self.vars) for c in st.transition.conds)
            elif wt == WHEN_ALL_CHANNELS:
                met = len(finished) >= len(st.channels)
            elif wt == WHEN_CHANNEL_END:
                met = st.transition.channel in finished
            elif wt == WHEN_STATE_TIME:
                # =62: このステートに入ってからの経過時間(一時停止中は進まない)
                met = state_clock.now_ms() >= threshold
            else:
                met = stats[st.transition.channel]["count"] >= threshold
            if not met:
                return False
            # =125: 移行先が判定式(cond)形式で、どの行も成立せず else も
            # 無いなら「移行しない」=発火自体を見送る(transitionなしと
            # 同じ挙動でステートの再生とイベント終了条件の判定を続ける)
            return st.transition.cond_resolves(self.vars)

        def event_end_met() -> bool:
            """イベント終了条件の成立判定。成立時は reason[0] に理由を残す。"""
            if event.end_type == END_DURATION:
                if event_clock.now_ms() >= self._ev_end_ms:
                    reason[0] = tr("合計時間 {0:g}秒に到達").format(
                        round(self._ev_end_ms / 1000, 1))
                    return True
            if event.end_type == END_PLAYS:
                if self._event_plays >= self._ev_end_count:
                    reason[0] = tr("合計{0}回の再生に到達").format(
                        self._ev_end_count)
                    return True
            if event.end_type == END_COND:
                if event.end_conds and all(
                        c.eval(self.vars) for c in event.end_conds):
                    reason[0] = tr("変数条件が成立")
                    return True
            return False

        # =62(音声なしフェーズ2): チャンネルが1つも無いノードでも、
        # 「イベントの終了条件=経過時間」または「移行条件=経過時間」が
        # あるときは**時間が来るまで無音で待つ**。それ以外(即時通過)は
        # 従来どおりループを一度も回さずに抜ける。
        # ※ 待つ間に変数を書き換える実行主体は居ないので、cond だけの
        #   ノードを待機モードにしてはいけない(永久待機になる)。
        silent_wait = (not st.channels) and (
            event.end_type == END_DURATION
            or (st.transition is not None
                and st.transition.when_type == WHEN_STATE_TIME))
        # =168: 終了条件が「無限」のイベントは、チャンネルが全部終わっても
        # イベントを終わらせない(無音のまま待ち続ける)。出口は選択肢/
        # 数値入力/判定式の常時監視/手動操作のいずれか。ステート形式では
        # 移行条件が成立すれば次のステートへ進む。
        never_end = (event.end_type == END_NONE)

        try:
            while ((tasks or silent_wait or never_end or sc_rule is not None)
                    and not self._stop_requested and self._jump is None):
                self.state["event_elapsed_ms"] = int(event_clock.now_ms())
                self.state["state_elapsed_ms"] = int(state_clock.now_ms())

                if has_video and self._video_item is not None:
                    # 経過・全長表示は動画に追従(シークバーの元データ)。
                    # 区間指定(=51)では「区間の長さ」を全長として表示する。
                    if self._video_clock is not None:
                        self.state["elapsed_ms"] = int(self._video_clock.now_ms())
                    dur = self.mpv.state.get("duration")
                    if dur:
                        end_s = self._video_end_s
                        fin = dur if end_s is None else min(end_s, dur)
                        seg = max(0.0, fin - self._video_start_s)
                        if self.state["duration_ms"] != int(seg * 1000):
                            self.state["duration_ms"] = int(seg * 1000)
                    if ((not self.mpv.connected or not self.mpv.alive)
                            and not self._winding_down):
                        # クラッシュ/手動クローズ→自動復旧(フェーズ2)。
                        # ワインドダウン中は復旧しない(直後に離脱するため。
                        # 次の動画イベントの _ensure_mpv が新規起動する)。
                        # チャンネルタスクではなくここで await するのは、
                        # 連続クラッシュの RuntimeError を play() まで
                        # 伝播させるため(タスク内だと握り潰される)。
                        await self._recover_mpv()

                done = [c for c, t in tasks.items() if t.done()]
                finished.update(done)   # 自然終了したチャンネルを累積(移行判定用)

                if not self._winding_down:
                    # イベント終了条件(累積)を優先して判定
                    if event_end_met():
                        pending = "event_end"
                        self._winding_down = True
                    # 単一ステートの channel 終了条件(旧仕様)
                    elif (event.end_type == END_CHANNEL
                          and event.end_channel in done):
                        pending = "event_end"
                        reason[0] = tr("チャンネル{0}が終了").format(
                            event.end_channel)
                        self._winding_down = True
                    # ステート移行条件
                    elif transition_met():
                        pending = "transition"
                        reason[0] = st.transition.describe()
                        self._winding_down = True
                        self._transition_winding = True

                for c in done:
                    del tasks[c]

                # =275: 選択肢でステート移行
                if sc_rule is not None and self._winding_down:
                    # イベント終了条件/監視(watch)の成立=選択肢は待たない
                    if sc_shown and self._choice_owner == "state":
                        self._log("choice",
                                  tr("選択肢を閉じる(イベント終了条件が成立)"))
                        self._clear_choice()
                    sc_rule = None
                    self._sc_rule = None
                elif sc_rule is not None:
                    if not sc_shown:
                        show = (sc_rule.show_mode == "start"
                                or (sc_rule.show_mode == "ms"
                                    and state_clock.now_ms() >= sc_rule.show_ms)
                                or (sc_rule.show_mode == "end" and not tasks))
                        if show and self.state["choice"] is None:
                            self._show_choice(event, rule=sc_rule,
                                              owner="state",
                                              state_id=st.state_id)
                            sc_shown = True
                            # 待機=人の操作を挟むので連続通過カウンタを解除
                            self._silent_streak = 0
                    if sc_shown and self.state["choice"] is not None:
                        to = self._choice_resolution(sc_rule, owner="state")
                        if to is not None:
                            sc_resolved = (to, self._choice_result_event)
                            self._clear_choice()
                            break   # 即移行(finally でチャンネルを打ち切る)
                if (silent_wait or never_end or sc_rule is not None) \
                        and pending is not None and not tasks:
                    # 待つべきタスクが無いので、条件が成立した時点で抜ける
                    # (ワインドダウンする対象も無い)。=168の「無限」も同じ:
                    # 音が鳴り終わって待っているだけの状態で移行条件が
                    # 成立したら、そこで次のステートへ進む。
                    break
                if self._watch_goto is not None and not tasks:
                    # =288: 監視(watch・graceful)が成立してワインドダウンし、
                    # 全チャンネルが止まった。イベント終了が「無限」(never_end)
                    # のときは pending が立たないため、従来ここで永久に待って
                    # いた(ユーザー報告: カウント0で再生が止まる)。監視の遷移先
                    # へ進むために抜ける(戻り値は _watch_goto から解決される)。
                    break
                await asyncio.sleep(0.05)
        finally:
            for t in tasks.values():
                t.cancel()
            for t in tasks.values():
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            if has_video:
                # 動画のあるコンテンツから離れる時は一時停止して残す
                # (ユーザー決定。次も動画なら loadfile が置き換える。
                #  同じファイルなら _load_video がシークだけで繋ぐ=51)
                self._video_clock = None
                self._video_item = None
                self._video_seamless = False
                self._video_start_s = 0.0
                self._video_end_s = None
                try:
                    await self.mpv.set_pause(True)
                except Exception:
                    pass
            self._winding_down = False
            self._transition_winding = False
            # =275: 停止/ジャンプでステート移行の選択肢が残っていれば閉じる
            if self.state["choice"] is not None \
                    and self._choice_owner == "state":
                self._clear_choice()
            self._sc_rule = None
            # イベントクロックは残し、チャンネルクロックのみ解放
            self._active_clocks = [event_clock]

        if not st.channels:
            # 音声なしステート(即時通過)の安全機構:
            # ①連続通過カウンタ。閾値到達で無限ループとみなし error 停止する
            #  (音声つきステートの実行、選択肢/入力の待機でリセット)。
            # ②1通過ごとに小休止を入れ、UIの応答性(停止操作・表示追従)を保つ。
            # =62: 無音待機ノードとして**実際に一定時間待った**場合は暴走では
            # ないのでカウンタをリセットする(N秒待つノードを100個以上たどる
            # 正当なシナリオを誤検出しないため)。
            if state_clock.now_ms() >= SILENT_WAIT_RESET_MS:
                self._silent_streak = 0
            else:
                self._silent_streak += 1
                if self._silent_streak >= SILENT_LOOP_LIMIT:
                    raise RuntimeError(
                        tr("音声なしイベントの無限ループの可能性があります"))
            await asyncio.sleep(0.02)
        else:
            self._silent_streak = 0

        if self._stop_requested or self._jump:
            return None
        # ここから先は自然終了パス(停止・手動▶▶/◀◀では上で return 済み)。
        # ステートを自然に抜ける(移行/イベント終了)ときの変数操作を発火する。
        # 通常イベントの main ステートは on_end 空なので no-op(=挙動不変)。
        self._apply_ops(st.on_end, tr("ステート終了時"))
        if self._watch_goto:
            # 監視(watch)のgraceful発火: ステート移行せずイベントを終了する
            self._log("end", tr("イベント終了: 監視(watch)が成立"))
            return "__event_end__"
        if sc_resolved is not None:
            # =275: 選択肢の行き先へ。イベント宛てはイベントを自然終了させて
            # から飛ぶ(_play_event が on_end 発火後に ("goto", to) を返す)
            to, to_event = sc_resolved
            if to_event:
                self._state_choice_event_to = to
                self._log("end", tr("イベント終了: 選択肢の遷移先イベント {0} へ").format(to))
                return "__event_end__"
            self._log("end", tr("ステート移行: {0} → {1}").format(
                tr("選択肢"), to))
            return to
        if pending == "transition":
            # =73: 重みは移行が成立した瞬間の変数値で解決(ユーザー決定)
            to, used_else = st.transition.pick_target_info(self.vars, self._visited_states)
            if to is None:
                # =125: cond形式はワインドダウン中に変数が変わった稀な場合のみ
                self._log("end", tr("イベント終了: 移行先が決まらない(else未指定)")
                          if st.transition.cond_rows
                          else tr("イベント終了: 移行先の重みが全て0(else未指定)"))
                return "__event_end__"
            label = reason[0] or tr("移行条件が成立")
            if used_else:
                label = tr("{0}(どの条件も不成立 → else)").format(label) \
                    if st.transition.cond_rows \
                    else tr("{0}(重みが全て0 → else)").format(label)
            self._log("end", tr("ステート移行: {0} → {1}").format(label, to))
            return to
        if pending == "event_end":
            self._log("end", tr("イベント終了: {0}").format(
                reason[0] or tr("終了条件が成立")))
            return "__event_end__"
        # 全チャンネルが自然終了した場合:
        # 移行条件を満たしていれば移行、そうでなければイベント終了
        if st.transition and transition_met():
            # =73: 重みは移行が成立した瞬間の変数値で解決(ユーザー決定)
            to, used_else = st.transition.pick_target_info(self.vars, self._visited_states)
            if to is None:
                self._log("end", tr("イベント終了: 移行先が決まらない(else未指定)")
                          if st.transition.cond_rows
                          else tr("イベント終了: 移行先の重みが全て0(else未指定)"))
                return "__event_end__"
            label = st.transition.describe()
            if used_else:
                label = tr("{0}(どの条件も不成立 → else)").format(label) \
                    if st.transition.cond_rows \
                    else tr("{0}(重みが全て0 → else)").format(label)
            self._log("end", tr("ステート移行: {0} → {1}").format(label, to))
            return to
        # ここが「終了条件を待たずに次へ進んだ」ときの経路(=56で可視化)。
        # チャンネルが全部終わればイベント終了条件より先にイベントが終わる。
        self._log("end", tr("イベント終了: 全チャンネルが終了"))
        return "__event_end__"

    async def _play_channel(self, channel, device_types: set, clock,
                            is_primary: bool, stats: dict,
                            end_resolved: tuple | None = None) -> None:
        """1チャンネルを終了条件まで再生する(本体は _play_channel_body)。

        =286: 終了条件で先に終わったチャンネルの「再生中の音声」表示は
        消す(finally で channel_audio から外す)。従来はステートが変わっても
        前のステートで鳴らしたチャンネルのファイル名が残っていた(ユーザー
        報告: S1→S2→S3 で C→L→R と鳴らすと S3 中も C/L が表示される)。
        """
        try:
            await self._play_channel_body(channel, device_types, clock,
                                          is_primary, stats, end_resolved)
        finally:
            self.state["channel_audio"].pop(channel.channel_id, None)

    async def _play_channel_body(self, channel, device_types: set, clock,
                                 is_primary: bool, stats: dict,
                                 end_resolved: tuple | None = None) -> None:
        """1チャンネルを終了条件まで再生する。

        device_types: このチャンネルが担当するデバイス種別の集合。
        stats: ステート内統計 {"time_ms": float, "count": int}。
               アイテム再生ごとに時間(音声+インターバル)と回数を加算する。
        END_NONE のチャンネルはワインドダウン/停止/ジャンプまで無限に再生する。
        終了条件の数値(count/duration)はステート入場時の変数値で解決される。
        end_resolved: 入場時に解決済みの (count, duration_ms)。=99の範囲抽選は
        resolve_end のたびに引き直されるため、ログ(=56)に出した値と実際に使う
        値が一致するよう、呼び出し側で解決した結果を受け取る(None=ここで解決)。
        """
        end_count, end_duration_ms = (end_resolved if end_resolved is not None
                                      else channel.resolve_end(self.vars))
        ch_elapsed_ms = 0.0

        def interrupted():
            return self._stop_requested or self._jump or self._winding_down

        # 動画チャンネル(=52): アイテムが1つだけで終了条件が「N秒」「無限」の
        # ときは mpv 側でループさせて継ぎ目を無くす(従来の「ループ動画」に
        # 相当。RVPが毎回シークし直すより mpv の ab-loop/loop-file が正確)。
        seamless_video = (channel.has_video and len(channel.items) == 1
                          and channel.end_type in (END_NONE, END_DURATION))

        async def play_one(item) -> None:
            nonlocal ch_elapsed_ms
            if item.video:
                limit = None
                if channel.end_type == END_DURATION:
                    limit = max(0.0, end_duration_ms - ch_elapsed_ms)
                dur = await self._play_video_item(
                    channel, item, device_types, clock, is_primary,
                    seamless=seamless_video, limit_ms=limit)
            else:
                dur = await self._play_channel_item(
                    channel, item, device_types, clock, is_primary)
            stats["count"] += 1
            # イベント終了条件「N回再生」(plays)は音声の再生本数のみ数える
            # (ユーザー決定)。スクリプトのみアイテムはチャンネル自身の終了
            # 条件(N回/N周)や channel_count 移行では従来どおりカウントされる
            # (stats側は無条件加算)。=52: 動画もコンテンツなので数える。
            if item.audio or item.video:
                self._event_plays += 1
            interval = await self._wait_interval(channel, device_types)
            total = dur + interval
            ch_elapsed_ms += total
            stats["time_ms"] += total

        plays_done = 0

        if channel.mode == MODE_SEQUENTIAL:
            if channel.end_type == END_PLAYS:
                # 順番にN回再生して終了(Nがアイテム数を超える場合は周回)
                i = 0
                while plays_done < end_count:
                    if interrupted():
                        return
                    await play_one(channel.items[i % len(channel.items)])
                    plays_done += 1
                    i += 1
                return
            passes = end_count if channel.end_type == END_REPEAT else 1
            loop_forever = channel.end_type == END_NONE
            while True:
                for _ in range(passes if not loop_forever else 1):
                    for item in channel.items:
                        if interrupted():
                            return
                        await play_one(item)
                        if (channel.end_type == END_DURATION
                                and ch_elapsed_ms >= end_duration_ms):
                            return
                if not loop_forever:
                    return
        else:  # MODE_RANDOM / MODE_RANDOM_BAG
            # =76 ランダム(重複なし): 「袋」=この周回でまだ再生していない
            # アイテムのリスト。袋から重み付きで引き、引けるものが無くなったら
            # (=一巡した or 残りが全て重み0以下)全アイテムで補充する。
            # 重みは従来どおり抽選のたびに解決する(=74)。
            bag = list(channel.items)
            bags_done = 0        # =98: 使い切った袋の数(repeat用)
            bag_limit_hit = False

            def draw():
                nonlocal bag, bags_done, bag_limit_hit
                if channel.mode != MODE_RANDOM_BAG:
                    return self._weighted_choice(channel.items)
                item = self._weighted_choice(bag)
                if item is None:
                    # 袋から引けない=1周消化(何か引いた後に限る。開始時から
                    # 全アイテム重み0以下のときは周と数えず=74の自然終了へ)。
                    # 早めの補充(残りが全て重み0以下)も1周と数える(=98
                    # ユーザー決定)。
                    if len(bag) < len(channel.items):
                        bags_done += 1
                        if (channel.end_type == END_REPEAT
                                and bags_done >= end_count):
                            # =98: 袋をN回使い切ったら終了(補充しない)
                            bag_limit_hit = True
                            return None
                    # 補充=全アイテムから抽選し直す。直前アイテムの回避は
                    # しない(=97でユーザー決定により撤去。アイテム数2〜3
                    # では境界回避が抽選を機械的な交互にしてしまうため)。
                    item = self._weighted_choice(channel.items)
                    if item is None:
                        return None   # 全アイテム重み0以下(=74と同じ扱い)
                    self._log("chan", tr(
                        "チャンネル{0}: 重複なしの候補を使い切ったため補充").format(
                            channel.channel_id))
                    bag = [it for it in channel.items if it is not item]
                else:
                    bag = [it for it in bag if it is not item]
                return item

            while not interrupted():
                if (channel.end_type == END_DURATION
                        and ch_elapsed_ms >= end_duration_ms):
                    return
                item = draw()
                if item is None:
                    if bag_limit_hit:
                        # =98: N周(袋N回)の消化による通常終了。ログは他の
                        # 終了条件と同様に出さない(理由はイベント側の→行)
                        return
                    # =74: 全アイテムの重みが0以下=出せるものが無い →
                    # チャンネル自然終了(ユーザー決定)。無限chでもここで終わる
                    self._log("end", tr(
                        "チャンネル{0}: 全アイテムの重みが0のため終了").format(
                            channel.channel_id))
                    return
                await play_one(item)
                plays_done += 1
                if (channel.end_type == END_PLAYS
                        and plays_done >= end_count):
                    return

    async def _wait_interval(self, channel, device_types: set) -> float:
        """チャンネルのインターバルを抽選し、その時間だけ待機する。

        待機時間(ms)を返す。一時停止中はカウントを止め、
        停止/ジャンプ/ワインドダウンで即中断する。
        """
        interval_ms = channel.pick_interval_ms()
        if interval_ms <= 0:
            return 0.0
        # 担当チャンネルのインターバル中は担当デバイスを停止(funscript間の空白)
        if TRACK_ROTATE in device_types:
            try:
                await self.intiface.send_rotate(50)
                self.state["rotate_pos"] = 50
            except Exception:
                pass
        if TRACK_ROTATE_A10 in device_types:
            try:
                await self.intiface.send_rotate_a10(50)
                self.state["rotate_a10_pos"] = 50
            except Exception:
                pass
        if TRACK_VIBRATION in device_types:
            try:
                await self.intiface.send_vibration(0)
                self.state["vibration_pos"] = 0
            except Exception:
                pass
        waited = 0.0
        step = 0.05
        while waited < interval_ms / 1000.0:
            if self._stop_requested or self._jump or self._winding_down:
                break
            if self._paused:
                await asyncio.sleep(step)
                continue
            await asyncio.sleep(step)
            waited += step
        return interval_ms

    def _device_output_on(self) -> bool:
        """デバイス駆動が有効か(=252 デバイス連動フラグ)。

        シナリオの device_enabled=False のときはトラックのランナーを一切
        起動しない(=デバイスへ送信しない・グラフ断片も作らない)。
        スクリプト専用アイテムの**再生時間はそのまま**(長さ計算はランナーと
        独立)なので、フラグを切り替えてもシナリオの進行タイミングは
        変わらない。シナリオ未読込は True(従来どおり)。
        属性ごと無い場合(テストが __new__ で部分構築するケース)も True。
        """
        return bool(getattr(getattr(self, "scenario", None),
                            "device_enabled", True))

    async def _play_channel_item(self, channel, item, device_types: set,
                                 clock, is_primary: bool) -> float:
        """チャンネル内の1アイテムを再生し、実際の再生時間(ms)を返す。

        担当種別(device_types)に含まれるトラックのfunscriptのみ実行する。
        """
        if not item.audio:
            # スクリプトのみアイテム(スクリプト専用チャンネル)
            return await self._play_script_item(channel, item, device_types,
                                                clock, is_primary)
        self._log_item(channel.channel_id, item)
        slot = _CH_SLOT[channel.channel_id]
        try:
            sound = pygame.mixer.Sound(item.audio)
        except Exception as e:
            logger.error(tr("音声の読み込み失敗: %s (%s)"), item.audio, e)
            # =249: メモリ系の失敗は原因と対処(44.1kHzへの変換など)を添える
            extra = describe_audio_load_error(item.audio, e)
            self._log("warn", tr("音声の読み込みに失敗しました: {0}").format(
                os.path.basename(item.audio))
                + ((" " + extra) if extra else ""))
            return 0.0

        duration_ms = _audio_duration_ms(item.audio)
        # =59: 区間指定があれば、その範囲だけを鳴らす Sound に差し替える。
        # 経過表示・シークバー・funscript同期はすべて**区間の先頭が0秒**
        # (動画=51と同じ方針)。シーク元(_slot_sound)も切り出し後を持たせて、
        # シーク操作が区間の外へ出ないようにする。
        if item.has_range:
            hi = None if item.end_s is None else item.end_s * 1000.0
            sliced = self._sliced_sound(sound, item.start_s * 1000.0, hi)
            if sliced is not None:
                sound = sliced
                duration_ms = int(sound.get_length() * 1000)

        # パン: item個別 > チャンネル既定
        pan = item.pan or channel.pan or Pan(*DEFAULT_PAN[channel.channel_id])

        py_channel = pygame.mixer.Channel(slot)
        py_channel.play(sound)
        # 再生開始時の変数操作(一時停止中に開始したアイテムでも発火する)
        self._apply_ops(item.on_play, tr("アイテム再生時"))
        self._slot_pan[slot] = (pan.left, pan.right)
        self._slot_sound[slot] = sound   # シーク時の切り出し元(原音)
        try:
            py_channel.set_volume(pan.left * self.master_volume,
                                  pan.right * self.master_volume)
        except Exception:
            pass
        # 一時停止中に開始したアイテム(一時停止中のジャンプ先など)は、
        # 音声もクロックも一時停止状態で始める。再生ボタン(resume)で動き出す。
        if self._paused:
            try:
                py_channel.pause()
            except Exception:
                pass

        self.state["channel_audio"][channel.channel_id] = item.audio

        fs_tasks = []
        graph_segs = []            # =69 グラフ表示用
        had_rotate = False
        had_rotate_a10 = False
        had_vibration = False
        if clock is not None:
            clock.start()
            if self._paused:
                clock.pause()
        if is_primary:
            self.state["audio_file"] = item.audio
            self.state["duration_ms"] = duration_ms

        if device_types and clock is not None and self._device_output_on():
            # トラックは _load_track_src が区間(=59)を適用して返す
            if TRACK_LINEAR in device_types:
                linear = item.tracks_of(TRACK_LINEAR)
                if linear:
                    fs = self._load_track_src(item, linear[0])
                    if fs is not None and fs.actions:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_funscript(fs, clock)))
                        graph_segs += self._graph_add_track(
                            TRACK_LINEAR, fs, clock)
            if TRACK_TWIST in device_types:
                twist = item.tracks_of(TRACK_TWIST)
                if twist:
                    tfs = self._load_track_src(item, twist[0])
                    if tfs is not None and tfs.actions:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_funscript(tfs, clock, twist=True)))
                        graph_segs += self._graph_add_track(
                            TRACK_TWIST, tfs, clock)
            if TRACK_ROTATE in device_types:
                rotate = item.tracks_of(TRACK_ROTATE)
                if rotate:
                    had_rotate = True
                    tl = self._load_track_src(item, rotate[0], rotate=True)
                    if tl is not None and tl.steps:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_rotate_timeline(tl, clock, "ufo")))
                        graph_segs += self._graph_add_track(
                            TRACK_ROTATE, tl, clock)
            if TRACK_ROTATE_A10 in device_types:
                rotate_a10 = item.tracks_of(TRACK_ROTATE_A10)
                if rotate_a10:
                    had_rotate_a10 = True
                    tl = self._load_track_src(item, rotate_a10[0], rotate=True)
                    if tl is not None and tl.steps:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_rotate_timeline(tl, clock, "a10")))
                        graph_segs += self._graph_add_track(
                            TRACK_ROTATE_A10, tl, clock)
            if TRACK_VIBRATION in device_types:
                vib = item.tracks_of(TRACK_VIBRATION)
                if vib:
                    had_vibration = True
                    vfs = self._load_track_src(item, vib[0])
                    if vfs is not None and vfs.actions:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_vibration(vfs, clock)))
                        graph_segs += self._graph_add_track(
                            TRACK_VIBRATION, vfs, clock)

        # 音声終了を待つ
        try:
            while not self._stop_requested and self._jump is None:
                if clock is not None and is_primary:
                    self.state["elapsed_ms"] = int(clock.now_ms())
                if not py_channel.get_busy() and not self._paused:
                    break
                await asyncio.sleep(0.03)
        finally:
            self._graph_finish(graph_segs)
            for t in fs_tasks:
                t.cancel()
            for t in fs_tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            # rotate/vibrationを実行したアイテムの終了時は動作を止めておく
            # (次のアイテムが同種トラックを持たない場合の動きっぱなし防止)
            if had_rotate and not self._stop_requested:
                try:
                    await self.intiface.send_rotate(50)
                    self.state["rotate_pos"] = 50
                except Exception:
                    pass
            if had_rotate_a10 and not self._stop_requested:
                try:
                    await self.intiface.send_rotate_a10(50)
                    self.state["rotate_a10_pos"] = 50
                except Exception:
                    pass
            if had_vibration and not self._stop_requested:
                try:
                    await self.intiface.send_vibration(0)
                    self.state["vibration_pos"] = 0
                except Exception:
                    pass

        # 自然完了時のみ発火する変数操作(▶▶スキップ・選択肢確定・停止・
        # 監視(interrupt)による打ち切りでは発火しない。
        # ワインドダウン(graceful監視含む)で最後まで再生された場合は発火)
        if not self._stop_requested and self._jump is None:
            self._apply_ops(item.on_complete, tr("アイテム完了時"))

        return duration_ms

    async def _play_video_item(self, channel, item, device_types: set,
                               clock, is_primary: bool,
                               seamless: bool = False,
                               limit_ms: float | None = None) -> float:
        """動画アイテム1つを外部mpvで再生し、再生時間(ms)を返す(=52)。

        音声アイテム(_play_channel_item)の動画版。チャンネルの再生方式
        (順番/ランダム)・終了条件・インターバルは _play_channel が共通で
        面倒を見るので、ここは「1本を頭出しして終わりまで待つ」だけを担う。

        seamless: mpv側でループさせる(継ぎ目なし)。この場合 eof/区間終端では
                  戻らず、limit_ms(チャンネルの残り時間)まで再生し続ける。
        limit_ms: END_DURATION チャンネルの残り時間(ms)。None=制限なし。

        clock は動画クロック(_on_mpv_time_pos が mpv の time-pos に従属
        させる)。**区間の先頭が0秒**なので funscript は区間ごとに0秒起点。
        """
        # 復旧(_recover_mpv)と区間判定が参照する「いま再生中の動画」
        self._video_item = item
        self._video_seamless = seamless
        self._video_start_s = item.video_start_s
        self._video_end_s = item.video_end_s
        self.state["video_file"] = item.video
        self.state["channel_audio"][channel.channel_id] = item.video
        if clock is not None:
            clock.start()
            if self._paused:
                clock.pause()
        # **経過時間は動画クロックとは別に測る**(重要): 動画クロックは
        # mpv の time-pos に従属する「再生位置」なので、シークや ab-loop の
        # 折り返しで巻き戻る。チャンネルの終了条件「N秒」は実経過で数える
        # 必要があるため、専用のクロックを立てる(一時停止に追従させるため
        # _active_clocks に登録する)。
        elapsed = PlaybackClock()
        elapsed.start()
        if self._paused:
            elapsed.pause()
        self._active_clocks.append(elapsed)
        if is_primary:
            self.state["audio_file"] = item.video
            self.state["duration_ms"] = 0   # mpvのduration到着で更新
            self.state["elapsed_ms"] = 0

        await self._load_video(item, seamless=seamless)
        if self._paused:
            # 一時停止中に入場(一時停止中の◀◀/▶▶)は動画も停止で待機
            await self.mpv.set_pause(True)
        self._log_item(channel.channel_id, item)
        # **区間の開始が動画の長さを超えていないか**(=54)。
        # 動画の実長は mpv がないと分からないため読み込み時に検証できない。
        # 超えているとシーク先が終端の外になり、mpv が即座に「再生し終わった」
        # 状態になるので、そのアイテムが一瞬で飛ばされる(原因が分かりにくい
        # ため、ここでログに警告を残す)。
        vdur = self.mpv.state.get("duration")
        if vdur and item.video_start_s >= vdur:
            msg = tr("区間の開始({0:g}秒)が動画の長さ({1:.1f}秒)を超えています: {2}").format(
                item.video_start_s, vdur, os.path.basename(item.video))
            logger.warning(msg)
            self._log("warn", msg)
        # 再生開始時の変数操作(音声アイテムと同じタイミング)
        self._apply_ops(item.on_play, tr("アイテム再生時"))

        fs_tasks, had, graph_segs = self._start_video_tracks(
            item, clock, device_types)
        completed = False
        try:
            while not self._stop_requested and self._jump is None:
                if clock is not None and is_primary:
                    self.state["elapsed_ms"] = int(clock.now_ms())
                if self._winding_down:
                    # 動画は音声のようにグレースフルに流し切らない
                    # (=48からの挙動。終了条件が成立したら即座に離脱する)
                    break
                if limit_ms is not None and elapsed.now_ms() >= limit_ms:
                    completed = True
                    break
                if not seamless:
                    if self.mpv.state.get("eof"):
                        completed = True
                        break
                    if self._video_segment_over():
                        # 区間終端では eof が立たないため mpv を明示的に
                        # 止める(止めないと次のアイテムへ移るまでの間に
                        # 区間を越えて再生が続く)。RVP全体の一時停止へは
                        # 伝播させない(_mpv_quiet ガード)。
                        completed = True
                        await self._pause_mpv_quiet()
                        break
                await asyncio.sleep(0.03)
        finally:
            self._graph_finish(graph_segs)
            for t in fs_tasks:
                t.cancel()
            for t in fs_tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            await self._stop_video_devices(had)
            try:
                self._active_clocks.remove(elapsed)
            except ValueError:
                pass

        if completed and not self._stop_requested and self._jump is None:
            self._apply_ops(item.on_complete, tr("アイテム完了時"))
        return elapsed.now_ms()

    async def _play_script_item(self, channel, item, device_types: set,
                                clock, is_primary: bool = False) -> float:
        """スクリプトのみアイテム(audioなし)を実行し、再生時間(ms)を返す。

        再生時間 = 全トラックの duration_ms の最大。=121: 区間終了(end)を
        明示したトラックは duration_ms が**区間の長さ**になる(最終アクション
        時刻ではない)ため、末尾の停止・保持区間も最後まで実行される。
        区間指定なし・終了省略のときは従来どおり最終アクション時刻まで。
        音声は再生せず、チャンネルクロックで経過を計る。担当種別
        (device_types)に含まれるトラックのみ実行する(音声アイテムと同じ規則)。

        音声アイテムとの意図的な違い(ユーザー決定):
        - ワインドダウン(イベント終了・ステート移行の成立)では最後まで
          流さず「即時中断」する(長尺スクリプトがイベント終了を遅らせない)。
        - on_complete は最後まで流れた(クロックが長さに達した)場合のみ発火。
        """
        self._log_item(channel.channel_id, item)
        # トラックの読み込みと長さの解決(種別ごとに先勝ち=音声アイテムの
        # tracks_of(...)[0] と同じ規則)
        loaded: dict = {}
        duration_ms = 0.0
        for t in item.tracks:
            if t.type in loaded:
                continue
            try:
                src = self._load_track_src(
                    item, t, rotate=t.type in (TRACK_ROTATE, TRACK_ROTATE_A10))
                if src is None:
                    continue
            except Exception as e:
                logger.error(tr("スクリプトの読み込み失敗: %s (%s)"),
                             t.funscript, e)
                continue
            loaded[t.type] = src
            duration_ms = max(duration_ms, float(src.duration_ms))
        if duration_ms <= 0:
            # 長さ0(読込失敗含む)の高速空回り防止。読み込み時検証があるため
            # 通常は起きない(再生中のファイル削除など)。1秒として扱う。
            logger.warning(tr("スクリプトの長さが0のため1秒として扱います: %s"),
                           item.tracks[0].funscript if item.tracks else "")
            duration_ms = 1000.0

        if clock is None:
            # 防御: 読み込み時検証によりスクリプト専用chは必ずデバイス担当を
            # 持つ(=クロックが渡る)が、万一に備えローカルクロックで計る。
            clock = PlaybackClock()
        clock.start()
        if self._paused:
            clock.pause()

        # 再生タブの表示(音声欄)にはスクリプトファイル名を出す
        self.state["channel_audio"][channel.channel_id] = \
            item.tracks[0].funscript if item.tracks else ""
        if is_primary:
            # =63: スクリプト専用chがシークバー追従チャンネルのときは、
            # スクリプトの長さを全長として表示・シークできるようにする
            self.state["audio_file"] = \
                item.tracks[0].funscript if item.tracks else ""
            self.state["duration_ms"] = duration_ms

        # 再生開始時の変数操作(音声アイテムと同じタイミング)
        self._apply_ops(item.on_play, tr("アイテム再生時"))

        fs_tasks = []
        graph_segs = []            # =69 グラフ表示用
        had_rotate = False
        had_rotate_a10 = False
        had_vibration = False
        for ttype, src in loaded.items():
            if not self._device_output_on():
                break        # =252: デバイス連動OFF=ランナーを起動しない
            if ttype not in device_types:
                continue
            if ttype == TRACK_LINEAR and src.actions:
                fs_tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock)))
                graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_TWIST and src.actions:
                fs_tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock, twist=True)))
                graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_ROTATE:
                had_rotate = True
                if src.steps:
                    fs_tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "ufo")))
                    graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_ROTATE_A10:
                had_rotate_a10 = True
                if src.steps:
                    fs_tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "a10")))
                    graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_VIBRATION and src.actions:
                had_vibration = True
                fs_tasks.append(asyncio.ensure_future(
                    self._run_vibration(src, clock)))
                graph_segs += self._graph_add_track(ttype, src, clock)

        completed = False
        try:
            while not self._stop_requested and self._jump is None:
                if self._winding_down:
                    break   # スクリプトは即時中断(ユーザー決定)
                if clock.now_ms() >= duration_ms:
                    completed = True
                    break
                await asyncio.sleep(0.03)
        finally:
            self._graph_finish(graph_segs)
            for t in fs_tasks:
                t.cancel()
            for t in fs_tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            # 実行した種別の動作を止めておく(音声アイテムと同じ後始末)
            if had_rotate and not self._stop_requested:
                try:
                    await self.intiface.send_rotate(50)
                    self.state["rotate_pos"] = 50
                except Exception:
                    pass
            if had_rotate_a10 and not self._stop_requested:
                try:
                    await self.intiface.send_rotate_a10(50)
                    self.state["rotate_a10_pos"] = 50
                except Exception:
                    pass
            if had_vibration and not self._stop_requested:
                try:
                    await self.intiface.send_vibration(0)
                    self.state["vibration_pos"] = 0
                except Exception:
                    pass

        # 最後まで流れた場合のみ on_complete を発火(ワインドダウンでの
        # 即時中断・停止・ジャンプでは発火しない=「流れ切った」セマンティクス)
        if completed and not self._stop_requested and self._jump is None:
            self._apply_ops(item.on_complete, tr("アイテム完了時"))

        return duration_ms

    # ================= 動画(外部mpv)連携 =================

    async def _ensure_mpv(self):
        """mpvを(必要なら)起動してIPC接続する。

        動画イベント/ステートに入った時に呼ばれる(遅延起動=合意事項)。
        見つからない/接続できない場合は RuntimeError(play() の包括catchで
        status="error"+メッセージ表示になる)。
        """
        if self.mpv is not None and self.mpv.connected and self.mpv.alive:
            return
        path = self.mpv_path or find_mpv()
        if not path:
            raise RuntimeError(
                tr("mpv が見つかりません。設定で mpv のパスを指定してください"))
        if self.mpv is None or not self.mpv.alive:
            self.mpv = MpvClient(path)
            self.mpv.on_time_pos = self._on_mpv_time_pos
            self.mpv.on_pause = self._on_mpv_pause
            self._mpv_loaded_file = ""   # 新プロセス=読み込み済み動画は無効
        try:
            await self.mpv.start(extra_args=self.mpv_extra_args)
        except MpvError as e:
            raise RuntimeError(str(e))

    # ---- 動画の読み込み・区間指定(=51 フェーズ3) ----

    async def _load_video(self, item, seamless: bool = False):
        """動画アイテムを mpv に用意する(=52。区間指定=51に対応)。

        seamless=True のときは mpv 側でループさせる(1アイテムだけの動画
        チャンネルで、終了条件が時間/無限のとき=従来の「ループ動画」)。

        **同じ動画ファイルが既に読み込まれていれば loadfile を省き、区間の
        先頭へシークするだけで繋ぐ**(ユーザー決定。=48で許容していた
        「ステート切替時につなぎが一瞬黒くなる」問題を解消する)。
        ループの実現方法は区間の有無で使い分ける:
          - 区間なし + ループ → mpv の `loop-file=inf`(従来どおり)
          - 区間あり + ループ → mpv の `ab-loop-a/b`(区間だけを繰り返す。
            loop-file だとファイル先頭に戻ってしまうため)
        """
        seg_loop = seamless and item.video_has_range
        # 古い ab-loop が残っていると seek 直後に飛ばされるので先にクリアする
        await self._set_ab_loop(None, None)
        await self.mpv.set_loop(seamless and not seg_loop)
        same = (self._mpv_loaded_file == item.video
                and self.mpv.connected and self.mpv.alive)
        if same:
            # つなぎの黒フレーム回避: 読み直さず頭出しだけ行う
            # (=52 では次のアイテムが同じファイルの別区間のときにも効く)
            await self.mpv.seek(item.video_start_s)
            await self.mpv.set_pause(False)
            # eof のクリアはシーク/再開の**後**に行う(前の再生で立った
            # eof-reached が遅れて届いても、次のアイテムを誤って終端と
            # 判定しないようにするため)
            self.mpv.state["eof"] = False
        else:
            await self.mpv.loadfile(item.video)
            self._mpv_loaded_file = item.video
            # **ロード完了(duration の観測)を必ず待つ**。=52で重要になった:
            # アイテムを次々に切り替えるとき、前の動画の eof-reached=true が
            # loadfile の後に遅れて届くと、次のアイテムが「開始直後に終端」と
            # 誤判定されて一瞬で飛ばされる(=複数動画が順番に再生されない)。
            # ロード完了まで待ってから eof を落とし直すことで、遅れて届いた
            # 前の動画のイベントを確実に無効化する。
            # (=49以来、start>0 のときは seek の失敗回避のためにも必要)
            await self._wait_video_loaded()
            self.mpv.state["eof"] = False
            if item.video_start_s > 0:
                await self.mpv.seek(item.video_start_s)
        if seg_loop:
            await self._set_ab_loop(item.video_start_s, item.video_end_s)

    async def _wait_video_loaded(self, timeout: float = 5.0):
        """mpv が duration を観測する(=ロード完了)まで待つ。"""
        deadline = time.monotonic() + timeout
        while (self.mpv.state.get("duration") is None
               and self.mpv.alive and time.monotonic() < deadline):
            await asyncio.sleep(0.05)

    async def _set_ab_loop(self, a, b):
        """mpv の A-Bループ(区間ループ)を設定/解除する。

        a/b が None なら解除("no")。ab-loop は mpv の古くからある機能で、
        区間の終端に達すると mpv 側が正確に a へ戻す(RVPの0.05s監視ループで
        seekし直すよりループの継ぎ目が正確)。失敗は無視する
        (未対応ビルドでも区間再生自体は成立させたいため)。
        """
        for prop, val in (("ab-loop-a", a), ("ab-loop-b", b)):
            try:
                await self.mpv.command("set_property", prop,
                                       "no" if val is None else float(val))
            except Exception:
                pass

    def _video_segment_over(self) -> bool:
        """区間の終端(end)に達したか。end 未指定なら常に False。"""
        if self._video_end_s is None:
            return False
        t = self.mpv.state.get("time_pos") if self.mpv is not None else None
        return t is not None and t >= self._video_end_s

    async def _pause_mpv_quiet(self):
        """mpvだけを一時停止する(RVP全体の一時停止へ伝播させない)。

        区間の終端で使う。通常の動画終了(eof)は keep-open の自動 pause を
        `_sync_pause_from_mpv` が eof フラグで弾いてくれるが、区間終端では
        eof が立たないため、明示的にガードする必要がある。
        """
        self._mpv_quiet = True
        try:
            await self.mpv.set_pause(True)
        except Exception:
            pass
        # _sync_pause_from_mpv は 0.08s 待ってから判定するので、それより
        # 長く待ってからガードを解除する
        await asyncio.sleep(0.15)
        self._mpv_quiet = False

    def _on_mpv_time_pos(self, t: float):
        """mpvの再生位置で動画クロックを従属させる(ドリフト補正)。

        50ms超のズレのときだけ set_ms する(フェーズ0で実測検証済み)。
        mpv側のシーク(ユーザー操作)もこの経路で自動追従し、funscriptは
        _fs_resync でbisect再同期する。
        """
        clock = self._video_clock
        if clock is None or clock.paused \
                or self._mpv_recovering or self._mpv_quiet:
            return
        # 区間指定(=51): 区間の先頭を0秒として扱う(ユーザー決定)。
        # これにより funscript は区間ごとに0秒起点で作れる。
        target = max(0.0, (t - self._video_start_s) * 1000.0)
        if abs(clock.now_ms() - target) > 50:
            clock.set_ms(target)
            self._fs_resync = True

    def _on_mpv_pause(self, flag: bool):
        """mpv側の一時停止/再開をRVP全体の一時停止に双方向同期する。

        RVP発の pause/resume は先に status が変わっているため無限ループに
        ならない。即時反映せず少し待って再確認する(_sync_*_from_mpv)のは、
        ①keep-open の末尾到達で mpv が自動的に pause になる(=「一時停止」で
        なく「動画終了」。eof-reached イベントが pause より後に届くことがある)
        ②loadfile 直後の内部的な pause 切替(過渡状態)を、ユーザー操作と
        誤認しないため。
        """
        if self._video_clock is None \
                or self._mpv_recovering or self._mpv_quiet:
            return
        if flag:
            asyncio.ensure_future(self._sync_pause_from_mpv())
        else:
            asyncio.ensure_future(self._sync_resume_from_mpv())

    async def _sync_pause_from_mpv(self):
        await asyncio.sleep(0.08)   # eof-reached の到着を待ってから判定
        if (self.mpv is None or self.mpv.state.get("eof")
                or not self.mpv.state.get("pause")
                or self._mpv_recovering or self._mpv_quiet):
            return
        if self._video_clock is None:
            return
        if self.state["status"] == "playing" and not self._paused:
            await self.pause()

    async def _sync_resume_from_mpv(self):
        await asyncio.sleep(0.08)   # loadfile等の過渡的な切替を除外
        if (self.mpv is None or self.mpv.state.get("pause")
                or self._mpv_recovering or self._mpv_quiet):
            return
        if self._video_clock is None:
            return
        if self.state["status"] == "paused" and self._paused:
            await self.resume()

    def _start_video_tracks(self, item, clock, device_types=None):
        """動画アイテムのトラックを動画クロックで実行開始する。

        種別ごと先勝ち(音声アイテムと同じ規則)。

        =239: **担当(device)が別のチャンネルにある種別のトラックは鳴らさない**。
        判定は「その種別がどこかのチャンネルの担当になっていて、かつ
        この動画チャンネルの担当ではない」。担当が指定されていない種別は
        **従来どおり動画が鳴らす**(=旧シナリオは device を書いていないので
        全トラックがそのまま動く)。これにより、動画に linear のトラックが
        あっても `device: {"linear": "L"}` として**音声チャンネル側へ
        付け替える**ことができる(ユーザー要望=239)。
        戻り値: (タスクのリスト, 後始末フラグdict, グラフ用断片のリスト)
        """
        tasks = []
        segs = []
        had = {"rotate": False, "a10": False, "vib": False}
        if not self._device_output_on():
            return tasks, had, segs   # =252: デバイス連動OFF
        seen = set()
        mine = set(device_types or ())
        for t in item.tracks:
            if t.type in seen:
                continue
            seen.add(t.type)
            if t.type not in mine and t.type in self._assigned_types:
                # =239: 担当が別のチャンネルにある = そちらが駆動源
                continue
            try:
                # =59: 動画トラックも動画の区間に連動させる(ユーザー決定)。
                # =51〜=58 は「区間の先頭を0秒とした別funscript」前提だったが、
                # 音声と規則を1つに揃えた(区間ごとに別スクリプトを当てたい
                # ときはトラック側の区間欄で上書きする)。
                src = self._load_track_src(
                    item, t, rotate=t.type in (TRACK_ROTATE, TRACK_ROTATE_A10))
                if src is None:
                    continue
            except Exception as e:
                logger.error(tr("スクリプトの読み込み失敗: %s (%s)"),
                             t.funscript, e)
                continue
            if t.type == TRACK_LINEAR and src.actions:
                tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock)))
                segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_TWIST and src.actions:
                tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock, twist=True)))
                segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_ROTATE:
                had["rotate"] = True
                if src.steps:
                    tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "ufo")))
                    segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_ROTATE_A10:
                had["a10"] = True
                if src.steps:
                    tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "a10")))
                    segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_VIBRATION and src.actions:
                had["vib"] = True
                tasks.append(asyncio.ensure_future(
                    self._run_vibration(src, clock)))
                segs += self._graph_add_track(t.type, src, clock)
        return tasks, had, segs

    async def _stop_video_devices(self, had: dict):
        """動画トラックが動かしていたデバイスを停止する(アイテムと同じ後始末)。"""
        if had.get("rotate") and not self._stop_requested:
            try:
                await self.intiface.send_rotate(50)
                self.state["rotate_pos"] = 50
            except Exception:
                pass
        if had.get("a10") and not self._stop_requested:
            try:
                await self.intiface.send_rotate_a10(50)
                self.state["rotate_a10_pos"] = 50
            except Exception:
                pass
        if had.get("vib") and not self._stop_requested:
            try:
                await self.intiface.send_vibration(0)
                self.state["vibration_pos"] = 0
            except Exception:
                pass

    async def _recover_mpv(self) -> None:
        """動画再生中に mpv が落ちた/閉じられたとき、再起動して復帰する。

        フェーズ2(ユーザー決定=自動復旧): mpvを再起動→同じ動画をロード→
        直前の再生位置へシーク→一時停止状態を復元して継続する。
        直前位置は動画クロックから取る(mpv従属クロックのため、切断時点の
        値がそのまま残っている)。
        無限再起動を避けるため、60秒以内に3回目の復旧が必要になったら
        RuntimeError でエラー停止する(繰り返しクラッシュ=環境異常とみなす)。
        """
        now = time.monotonic()
        self._mpv_fail_times = [t for t in self._mpv_fail_times
                                if now - t < 60.0]
        self._mpv_fail_times.append(now)
        if len(self._mpv_fail_times) >= 3:
            raise RuntimeError(
                tr("mpv が繰り返し終了するため停止しました"))
        logger.warning(
            tr("mpv との接続が切れました。再起動して復帰します"))
        pos_s = 0.0
        if self._video_clock is not None:
            pos_s = max(0.0, self._video_clock.now_ms() / 1000.0)
        paused = self._paused
        # 復旧中の loadfile/pause 切替イベントをユーザー操作と誤同期しない
        # (特に一時停止中の復旧: loadfileは再生開始状態になるため、
        #  過渡的な pause=False で RVP を再開してしまわないようにする)
        self._mpv_recovering = True
        try:
            if self.mpv is not None:
                try:
                    await self.mpv.close()
                except Exception:
                    pass
            self._mpv_loaded_file = ""   # プロセスが変わるので読み直す
            await self._ensure_mpv()
            item = self._video_item
            seg_loop = self._video_seamless and item.video_has_range
            await self.mpv.set_loop(self._video_seamless and not seg_loop)
            await self.mpv.loadfile(item.video)
            self._mpv_loaded_file = item.video
            # ロード完了(durationの観測)を待ってから位置を復元する
            # (loadfile直後のseekはファイル未ロードで失敗することがある)
            await self._wait_video_loaded()
            # pos_s は区間先頭を0とした相対位置(=51)なので、mpv へは
            # 区間の開始秒を足した絶対位置でシークする
            abs_s = item.video_start_s + pos_s
            if abs_s > 0.5:
                try:
                    await self.mpv.seek(abs_s)
                except Exception:
                    pass
            if seg_loop:
                await self._set_ab_loop(item.video_start_s, item.video_end_s)
            if paused:
                await self.mpv.set_pause(True)
        finally:
            self._mpv_recovering = False

    def _video_osd(self, text: str) -> None:
        """mpvの画面にオーバーレイ文字を出す/消す(選択肢・入力の案内)。

        フェーズ2(ユーザー決定): フルスクリーン動画中でも選択肢/数値入力
        カードの存在に気づけるように、mpv の show-text OSD で案内する。
        空文字=クリア。案内目的のみなので失敗は無視する。
        動画のあるイベントの最中(または直後の待機フェーズ=state["video_file"]
        が残っている間)だけ表示する。
        """
        if self.mpv is None or not self.mpv.connected:
            return
        # 表示は動画のあるイベントの間だけ。クリアは接続中なら常に送る
        # (イベント開始時のリセットは video_file クリア後に呼ばれるため)
        if text and self._video_clock is None \
                and not self.state.get("video_file"):
            return

        async def _send():
            try:
                if text:
                    await self.mpv.command("show-text", text, 3600000)
                else:
                    await self.mpv.command("show-text", "", 1)
            except Exception:
                pass
        asyncio.ensure_future(_send())

    async def shutdown_mpv(self) -> None:
        """mpvを終了する。RVPの終了時のみ呼ぶ(合意事項)。"""
        if self.mpv is not None:
            try:
                await self.mpv.quit()
            except Exception:
                pass
            self.mpv = None
            self._mpv_loaded_file = ""   # 次回は必ず loadfile から(=51)

    def _weighted_choice(self, items):
        """ランダム再生chの1アイテムを抽選する(=74)。

        重みは**抽選のたび**に解決する(定数 or 変数参照=現在の変数値)。
        0以下の候補は抽選に出さない。出せる候補が無ければ None
        (=呼び出し側でチャンネル自然終了として扱う)。
        """
        cands, weights = [], []
        for item in items:
            w = self.vars.get(item.weight_var) if item.weight_var \
                else item.weight
            try:
                w = float(w)
            except (TypeError, ValueError):
                w = 0.0
            if w > 0:
                cands.append(item)
                weights.append(w)
        if not cands:
            return None
        return random.choices(cands, weights=weights, k=1)[0]

    def set_offset(self, device_type: str, seconds: float) -> None:
        """デバイス動作タイミングのオフセットを設定する(UIスレッドから呼び出し可)。

        正の値=遅らせる / 負の値=早める。-2.0〜+2.0秒にクランプ。
        """
        seconds = max(-2.0, min(2.0, seconds))
        self.offsets_ms[device_type] = int(round(seconds * 1000))
        # 送信済みのlinearコマンドは旧基準のままなので再送させる
        self._fs_resync = True

    # ================= グラフ表示(=69) =================
    #
    # 再生タブ③グラフ表示のためのデータ提供。実行ロジックには一切干渉せず、
    # 「いま動かしているスクリプトの中身」と「イベント経過時間のどこに
    # 置かれているか」を UI が読めるようにするだけ。
    #
    # 横軸はイベント入場からの経過時間(全デバイス共通)。各断片の開始時刻
    # t0 は **その場で計算する**(t0 = イベント経過 - チャンネルクロック +
    # 動作タイミングのオフセット)。こうするとシーク・動画のab-loop・
    # ドリフト補正でチャンネルクロックが動いても自動で追従し、
    # 「再生ヘッド(中央線)の位置＝いま実行中のアクション」が常に一致する。
    # 実行が終わった断片はその時点の t0 を凍結して残す。

    @staticmethod
    def _graph_points_funscript(fs) -> list:
        """funscript を (時刻ms, pos0-100) の列にする。"""
        return [(float(a.at), float(a.pos)) for a in fs.actions]

    def _graph_trim(self) -> None:
        while len(self.graph_segments) > GRAPH_SEG_LIMIT:
            for i, s in enumerate(self.graph_segments):
                if not s["live"]:
                    del self.graph_segments[i]
                    break
            else:
                del self.graph_segments[0]

    @staticmethod
    def _graph_points_rotate(tl, index: int) -> list:
        """RotateTimeline の1ロータ分を (時刻ms, pos0-100) の列にする。

        50=停止 / 100=正回転最大 / 0=逆回転最大(再生タブのバー表示と同じ写像)。
        """
        out = []
        for st in tl.steps:
            cw, frac = st.vals[index]
            f = max(0.0, min(1.0, float(frac)))
            out.append((float(st.at), 50.0 + f * 50.0 if cw else 50.0 - f * 50.0))
        return out

    def _graph_add(self, base: str, kind: str, points: list, clock,
                   offset_key: str, lane: str = "", rotor=None):
        if not points or clock is None:
            return None
        seg = {"base": base, "kind": kind, "points": points,
               "times": [p[0] for p in points], "clock": clock,
               "offset_key": offset_key, "lane": lane, "rotor": rotor,
               "t0": 0.0, "live": True,
               # =71: 再生を開始したイベント経過時刻。**履歴なので動かさない**。
               # 描画はこの x0 から「次の断片の x0」までの窓に限る(重なり防止)。
               # 窓の中でスクリプトの中身は t0 に従って左右へスライドする。
               "x0": self._graph_now_ms()}
        self.graph_segments.append(seg)
        self._graph_trim()
        return seg

    def _graph_add_track(self, ttype: str, src, clock) -> list:
        """実行を開始したトラックをグラフ用に登録する(戻り値=断片のリスト)。"""
        segs = []
        if ttype == TRACK_LINEAR:
            segs.append(self._graph_add(
                "linear", "linear", self._graph_points_funscript(src),
                clock, "linear"))
        elif ttype == TRACK_TWIST:
            segs.append(self._graph_add(
                "twist", "linear", self._graph_points_funscript(src),
                clock, "twist"))
        elif ttype == TRACK_VIBRATION:
            segs.append(self._graph_add(
                "vibration", "step", self._graph_points_funscript(src),
                clock, "vibration"))
        elif ttype in (TRACK_ROTATE, TRACK_ROTATE_A10):
            ufo = (ttype == TRACK_ROTATE)
            lane = "ufo" if ufo else "a10"
            base = "rotate_ufo" if ufo else "rotate_a10"
            okey = "rotate" if ufo else TRACK_ROTATE_A10
            if getattr(src, "channels", 1) >= 2:
                # タイプB(5列CSV)=ufotwの左右独立。ロータごとに1本ずつ。
                for rotor in (0, 1):
                    segs.append(self._graph_add(
                        base, "step", self._graph_points_rotate(src, rotor),
                        clock, okey, lane=lane, rotor=rotor))
            else:
                segs.append(self._graph_add(
                    base, "step", self._graph_points_rotate(src, 0),
                    clock, okey, lane=lane))
        return [s for s in segs if s]

    def _graph_now_ms(self) -> float:
        return self._event_clock.now_ms() if self._event_clock is not None else 0.0

    def _graph_t0(self, seg, now_ms: float) -> float:
        if not seg["live"] or seg["clock"] is None:
            return seg["t0"]
        return (now_ms - seg["clock"].now_ms()
                + self.offsets_ms.get(seg["offset_key"], 0))

    def _graph_finish(self, segs) -> None:
        """実行の終わった断片の位置を凍結する(過去の波形として残す)。"""
        now = self._graph_now_ms()
        for s in segs or ():
            if s["live"]:
                s["t0"] = self._graph_t0(s, now)
                s["live"] = False
                s["clock"] = None

    def _graph_freeze(self) -> None:
        """再生終了・停止でグラフの時間を止める(=69の不具合修正)。

        イベント経過クロックを止めないと、再生が終わったあともグラフだけが
        右から左へ流れ続けてしまう。表示は「直前の状態を残す」仕様なので、
        クロックを一時停止して現在位置の縦線ごと固定する。
        """
        self._graph_finish([s for s in self.graph_segments if s["live"]])
        if self._event_clock is not None:
            self._event_clock.pause()

    def _graph_key(self, seg) -> str:
        """表示上のグラフ種別キー。2ロータは左右反転を反映して振り分ける。"""
        if seg["rotor"] is None:
            return seg["base"]
        swap = bool(self.rotate_swap.get(seg["lane"], False))
        is_right = (seg["rotor"] == 1) != swap
        return seg["base"] + ("_r" if is_right else "")

    def graph_snapshot(self) -> dict:
        """グラフ表示用のスナップショット(UIスレッドから毎フレーム呼ぶ)。

        =71: 断片は**常にスクリプト全体**を持つ。重なりは「描画してよい x の窓」
        (x0 〜 同じ行の次の断片の x0)で防ぐ。窓は毎回その場で計算するので、
        巻き戻して次の断片の開始位置が右へ動けば、切り詰められていた過去の
        波形が自動的に戻ってくる。
        """
        now = self._graph_now_ms()
        segs = [{"key": self._graph_key(s), "kind": s["kind"],
                 "points": s["points"], "times": s["times"],
                 "x0": s["x0"], "x1": None,
                 "t0": self._graph_t0(s, now), "live": s["live"]}
                for s in list(self.graph_segments)]
        # 行(=表示種別)ごとに開始位置順へ並べ、次の断片の開始位置を右端にする
        rows: dict = {}
        for seg in segs:
            rows.setdefault(seg["key"], []).append(seg)
        for row in rows.values():
            row.sort(key=lambda s: s["x0"])
            for a, b in zip(row, row[1:]):
                a["x1"] = b["x0"]
        return {"now_ms": now, "segments": segs}

    # ================= funscript実行 =================

    async def _run_funscript(self, funscript: Funscript, clock: PlaybackClock,
                             twist: bool = False) -> None:
        """クロックの現在時刻から実行区間を逆算してlinear/twistコマンドを送る。

        =79: twist=True で送信先が TWIST軸(2軸目)・オフセットが "twist" レーン
        になる。ループ構造・シーク/一時停止の再同期(_fs_resync)は完全に共通。
        """
        actions = funscript.actions
        ats = [a.at for a in actions]
        n = len(actions)
        last_sent = None
        was_paused = False
        offset_key = "twist" if twist else "linear"
        send = self._send_twist_pos if twist else self._send_linear_pos

        while True:
            if clock.paused:
                was_paused = True
                await asyncio.sleep(0.05)
                continue
            if was_paused or self._fs_resync:
                was_paused = False
                self._fs_resync = False
                last_sent = None

            now = clock.now_ms() - self.offsets_ms[offset_key]
            idx = bisect.bisect_right(ats, now) - 1
            if idx >= n - 1:
                break

            if last_sent != idx:
                target = actions[idx + 1]
                try:
                    await send(target.pos, max(int(target.at - now), 20))
                except Exception:
                    logger.warning(tr("接続ロストのためfunscript実行を中断します"))
                    return
                last_sent = idx

            await asyncio.sleep(0.01)

    def _load_rotate_safe(self, path: str):
        """rotate動作元(funscript/CSV)を安全に読み込む。

        壊れたCSV/funscriptでも再生全体を止めず、そのトラックだけスキップする
        (警告ログのみ)。読み込めた場合は RotateTimeline を返す。
        """
        try:
            return load_rotate_source(path)
        except Exception as e:
            logger.warning(tr("rotate動作元の読み込みに失敗しました: %s"), e)
            return None

    async def _run_rotate(self, funscript: Funscript, clock: PlaybackClock,
                          a10: bool = False) -> None:
        """後方互換: 既読funscriptを RotateTimeline 化して実行する。

        (旧API。直接呼ぶテスト用。通常の再生経路は _run_rotate_timeline。)
        """
        tl = RotateTimeline.from_funscript(funscript)
        await self._run_rotate_timeline(tl, clock, "a10" if a10 else "ufo")

    @staticmethod
    def _display_pos(vals) -> int:
        """表示用の代表pos(0-100)。2chは速度率の大きいチャンネル(=OR)を使う。"""
        if not vals:
            return 50
        cw, frac = max(vals, key=lambda v: v[1])   # 同率はch0(左)優先
        off = int(round(max(0.0, min(1.0, frac)) * 50))
        return 50 + off if cw else 50 - off

    async def _run_rotate_timeline(self, timeline: RotateTimeline,
                                   clock: PlaybackClock, lane: str) -> None:
        """RotateTimeline(funscript/CSV)を実行する。

        lane: "ufo" / "a10"。funscript由来(1ch)は従来の pos 送信API
        (send_rotate/send_rotate_a10)を使い、モック互換と整数精度を保つ。
        CSV由来は send_rotate_lane でロータ個別・float精度で送出する
        (ufotwの2ロータ独立=タイプB対応)。
        """
        lane_ufo = (lane != "a10")
        offset_key = "rotate" if lane_ufo else TRACK_ROTATE_A10
        state_key = "rotate_pos" if lane_ufo else "rotate_a10_pos"
        ch_key = "rotate_ufo_ch" if lane_ufo else "rotate_a10_ch"
        use_pos_api = (timeline.source_kind == "funscript")
        steps = timeline.steps
        ats = timeline.ats
        n = len(steps)
        last_sent = None
        was_paused = False
        last_swap = bool(self.rotate_swap.get(lane, False))

        async def send_vals(vals):
            if use_pos_api:
                pos = self._display_pos(vals)
                if lane_ufo:
                    await self.intiface.send_rotate(pos)
                else:
                    await self.intiface.send_rotate_a10(pos)
            else:
                # =71: 左右反転は**送信のたびに読み直す**。トラック開始時に
                # 固定すると、再生中にチェックを操作しても次のアイテムまで
                # 反映されない(レンジ/反転/オフセットは元から都度読み直し)。
                swap = bool(self.rotate_swap.get(lane, False))
                await self.intiface.send_rotate_lane(lane, list(vals), swap=swap)

        stop_vals = tuple((True, 0.0) for _ in range(timeline.channels))
        if n == 0:
            return

        while True:
            if clock.paused:
                if not was_paused:
                    try:
                        await send_vals(stop_vals)
                    except Exception:
                        return
                    self.state[state_key] = 50
                    self.state[ch_key] = list(stop_vals)
                was_paused = True
                await asyncio.sleep(0.05)
                continue
            if was_paused or self._fs_resync:
                was_paused = False
                last_sent = None
            # =71: 左右反転が切り替わったら同じ行でも送り直す。CSVは行の間隔が
            # 長いことがあり、次の行を待つと反映が数十秒遅れてしまう。
            cur_swap = bool(self.rotate_swap.get(lane, False))
            if cur_swap != last_swap:
                last_swap = cur_swap
                last_sent = None

            now = clock.now_ms() - self.offsets_ms[offset_key]
            idx = bisect.bisect_right(ats, now) - 1
            if idx < 0:
                # 再生位置が最初の行/アクションの時刻に達する前は**停止**。
                # CSV: 従来どおり(例: 1行目 300,1,20 → 0〜30秒はUFO停止)。
                # funscript: =271 でCSVと同じ停止へ変更。従来は先頭
                # アクションのposを先取り保持していた(1行目 at=5000,pos=80
                # だと最初の5秒間も80で回ってしまう)。at=0 の指示が無い
                # ときは既定 pos,at=50,0(停止)として扱う(ユーザー依頼)。
                if last_sent != "pre":
                    try:
                        await send_vals(stop_vals)
                    except Exception:
                        logger.warning(tr("接続ロストのためrotate実行を中断します"))
                        return
                    self.state[state_key] = 50
                    self.state[ch_key] = list(stop_vals)
                    last_sent = "pre"
                await asyncio.sleep(0.01)
                continue
            if idx >= n:
                break

            if last_sent != idx:
                vals = steps[idx].vals
                try:
                    await send_vals(vals)
                    self.state[state_key] = self._display_pos(vals)
                    self.state[ch_key] = list(vals)
                except Exception:
                    logger.warning(tr("接続ロストのためrotate実行を中断します"))
                    return
                last_sent = idx

            await asyncio.sleep(0.01)

    async def _run_vibration(self, funscript: Funscript, clock: PlaybackClock) -> None:
        """vibration用funscript実行。pos=0停止/100最大振動。

        rotateと同じステップ方式: 各区間の開始アクションのposを
        その区間中ずっと維持する。
        """
        actions = funscript.actions
        ats = [a.at for a in actions]
        n = len(actions)
        last_sent = None
        was_paused = False

        while True:
            if clock.paused:
                if not was_paused:
                    try:
                        await self.intiface.send_vibration(0)
                    except Exception:
                        return
                    self.state["vibration_pos"] = 0
                was_paused = True
                await asyncio.sleep(0.05)
                continue
            if was_paused or self._fs_resync:
                was_paused = False
                last_sent = None

            now = clock.now_ms() - self.offsets_ms["vibration"]
            idx = bisect.bisect_right(ats, now) - 1
            if idx < 0:
                # =271: 最初のアクションの時刻に達する前は**停止**(pos=0)。
                # at=0 の指示が無いときは既定 pos,at=0,0(停止)として扱う
                # (従来は先頭アクションのposを先取り保持していた=rotateと
                # 同じ問題。ユーザー依頼で変更)。
                if last_sent != "pre":
                    try:
                        await self.intiface.send_vibration(0)
                    except Exception:
                        logger.warning(tr("接続ロストのためvibration実行を中断します"))
                        return
                    self.state["vibration_pos"] = 0
                    last_sent = "pre"
                await asyncio.sleep(0.01)
                continue
            if idx >= n:
                break

            if last_sent != idx:
                pos = actions[idx].pos
                try:
                    await self.intiface.send_vibration(pos)
                    self.state["vibration_pos"] = pos
                except Exception:
                    logger.warning(tr("接続ロストのためvibration実行を中断します"))
                    return
                last_sent = idx

            await asyncio.sleep(0.01)
