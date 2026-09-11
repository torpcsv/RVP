"""再生エンジン本体(ScenarioPlayer: 開始/停止/一時停止・イベント/ステートの進行・遷移先の決定・シーク)。"""
from __future__ import annotations

import asyncio
import pygame
import random
import time
from ..scenario import (END_CHANNEL, END_COND, END_DURATION, END_NONE,
    END_PLAYS, END_STATES, END_TRANSITIONS, Scenario, TRACK_ROTATE_A10,
    WHEN_ALL_CHANNELS, WHEN_CHANNEL_END, WHEN_CHOICE, WHEN_COND,
    WHEN_STATE_TIME)
from ..intiface_client import IntifaceClient
from ..i18n import tr

from .clock import PlaybackClock
from .common import (SILENT_LOOP_LIMIT, SILENT_WAIT_RESET_MS, _BGM_SLOT,
    _CH_SLOT, logger)
from .sp_audio import _ScenarioPlayerAudioMixin
from .sp_channel import _ScenarioPlayerChannelMixin
from .sp_devices import _ScenarioPlayerDevicesMixin
from .sp_graph import _ScenarioPlayerGraphMixin
from .sp_interact import _ScenarioPlayerInteractMixin
from .sp_log import _ScenarioPlayerLogMixin
from .sp_video import _ScenarioPlayerVideoMixin


class ScenarioPlayer(_ScenarioPlayerLogMixin, _ScenarioPlayerInteractMixin, _ScenarioPlayerDevicesMixin, _ScenarioPlayerAudioMixin, _ScenarioPlayerChannelMixin, _ScenarioPlayerVideoMixin, _ScenarioPlayerGraphMixin):
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

    SOFT_START_SEC = 2.0       # スマートスタート期間

    SOFT_MIN_FRAC = 0.15       # 期間開始直後の速度上限(通常上限に対する割合)

    SOFT_BASE_MS = 250         # 速度制限「なし」設定時に期間中だけ使う基準時間

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
