"""シナリオ読み込み: `Scenario.load` の mixin(本体。要素ごとのパースは parse_*.py)。"""
from __future__ import annotations

import json
import os
from .model import (BackgroundSpec, Channel, DeviceTrack, EventItem,
    EventState, Pan, ScenarioEvent, _is_num, check_node_color,
    check_node_pos)
from .constants import (CH_CENTER, DEFAULT_PAN, END_CHANNEL, END_COND,
    END_DURATION, END_NONE, END_ONCE, END_PLAYS, MODE_SEQUENTIAL,
    TRACK_LINEAR, VALID_CHANNELS, VALID_TRACK_TYPES, WHEN_ALL_CHANNELS,
    WHEN_CHOICE, WHEN_COND, WHEN_STATE_TIME)
from .tracks import load_script_source, track_range_ms, wav_duration_ms
from .video import migrate_video_node, raw_video_channel
from ..i18n import tr

from .load_context import LoadContext
from .parse_channels import (check_script_channels, check_video_channels,
    has_content_channels, parse_channel, parse_device, parse_end,
    parse_event_duration_range, parse_seek_channel)
from .parse_flow import parse_event_end, parse_next, parse_state
from .parse_items import parse_bgm, resolve
from .parse_vars import (parse_conds, parse_numref, parse_ops, parse_vars,
    parse_watch)


class _ScenarioLoadMixin:
    """Scenario の mixin(=301 分割・=306 で入れ子関数をモジュール関数へ)。

    JSON の読み込みと検証(`Scenario.load`)。各要素のパースは parse_vars /
    parse_items / parse_channels / parse_flow の関数に、LoadContext を先頭
    引数 `ctx` として渡して行う。
    """

    @classmethod
    def load(cls, path: str) -> "Scenario":
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        base_dir = os.path.dirname(os.path.abspath(path))
        load_warnings: list[str] = []   # =130 読み込み警告(エラーにしない)
        ctx = LoadContext(base_dir=base_dir, load_warnings=load_warnings)

        # =252: デバイス連動フラグ(省略=True=従来どおり)。bool以外はエラー
        device_enabled = data.get("device_enabled", True)
        if not isinstance(device_enabled, bool):
            raise ValueError(
                tr("device_enabled は true/false で指定してください"))

        # =256: BGM機能フラグ(省略=False)。bool以外はエラー
        bgm_enabled = data.get("bgm_enabled", False)
        if not isinstance(bgm_enabled, bool):
            raise ValueError(
                tr("bgm_enabled は true/false で指定してください"))

        # =299: イベント図の配置モード(表示専用)。省略=auto
        map_mode = data.get("map_mode", "auto")
        if map_mode not in ("auto", "manual"):
            raise ValueError(
                tr('map_mode は "auto" か "manual" で指定してください'))

        # ---------------- 背景イラスト(=262) ----------------
        # 文字列("path")と辞書({"file","dim"})の2書式。dim省略=40。
        background = None
        raw_bg = data.get("background")
        if raw_bg is not None:
            if isinstance(raw_bg, str):
                raw_bg = {"file": raw_bg}
            if not isinstance(raw_bg, dict):
                raise ValueError(
                    tr("background は文字列かオブジェクトで指定してください"))
            bg_file = raw_bg.get("file")
            if not isinstance(bg_file, str) or not bg_file.strip():
                raise ValueError(tr("background: file を指定してください"))
            bg_dim = raw_bg.get("dim", 40)
            if isinstance(bg_dim, bool) or not isinstance(bg_dim, (int, float)) \
                    or not (0 <= bg_dim <= 100):
                raise ValueError(
                    tr("background: dim は 0〜100 の数値で指定してください"))
            background = BackgroundSpec(file=resolve(ctx, bg_file),
                                        dim=int(round(bg_dim)))

        # ---------------- 変数(vars) ----------------

        var_decls = parse_vars(ctx, data.get("vars"))
        ctx.var_decls = var_decls   # 以降の変数参照チェック(_require_var)が見る

        events: dict[str, ScenarioEvent] = {}

        if "events" in data:
            for event_id, raw in data["events"].items():
                where = tr("イベント '{0}'").format(event_id)

                next_str, next_rule, next_choice, next_cond, next_input = \
                    parse_next(ctx, raw.get("next"), where)
                # =123 すごろく(advance)
                adv_raw = raw.get("advance")
                advance = advance_ref = None
                if adv_raw is not None:
                    if isinstance(adv_raw, dict):
                        _c, advance_ref = parse_numref(ctx,
                            adv_raw, tr("{0} advance").format(where))
                    elif (_is_num(adv_raw) and float(adv_raw).is_integer()
                          and adv_raw >= 1):
                        advance = int(adv_raw)
                    else:
                        raise ValueError(
                            tr('{0}: advance(すごろく)は1以上の整数か {{"var": 変数名}} で指定してください').format(where))
                    if (next_str is None and next_rule is None
                            and next_cond is None and next_choice is None
                            and next_input is None):
                        raise ValueError(
                            tr("{0}: advance(すごろく)が指定されていますが、次のイベントがありません").format(where))
                    if next_choice is not None or next_input is not None:
                        raise ValueError(
                            tr("{0}: advance(すごろく)は選択肢/数値入力の遷移には使えません").format(where))
                # =124 ノードの着色(表示専用) / =299 手動配置の座標
                check_node_color(raw, where)
                check_node_pos(raw, where)
                on_start_ops = parse_ops(ctx, raw.get("on_start"),
                                         tr("{0} on_start").format(where))
                on_end_ops = parse_ops(ctx, raw.get("on_end"),
                                       tr("{0} on_end").format(where))

                if "states" in raw:
                    # --- マルチステート形式 ---
                    if raw.get("video") is not None:
                        raise ValueError(
                            tr('{0}: ステート形式では video は各ステートに指定してください').format(where))
                    if raw.get("bgm") is not None:
                        # =256: BGMもチャンネルと同じく各ステートの持ち物
                        raise ValueError(
                            tr('{0}: ステート形式では bgm は各ステートに指定してください').format(where))
                    states: dict[str, EventState] = {}
                    for state_id, st_raw in raw["states"].items():
                        states[state_id] = parse_state(ctx,
                            state_id, st_raw, tr("{0} ステート'{1}'").format(where, state_id))
                    if not states:
                        raise ValueError(tr('{0}: states が空です').format(where))

                    start_state = raw.get("start")
                    if not start_state or start_state not in states:
                        raise ValueError(tr('{0}: start ステートが見つかりません').format(where))

                    # =99: イベント終了 duration の範囲抽選(min_seconds/
                    # max_seconds)をステート形式でも許可(通常イベントと同じ)
                    st_end_raw = raw.get("end")
                    st_dur_range = None
                    if (isinstance(st_end_raw, dict)
                            and st_end_raw.get("type") == END_DURATION
                            and ("min_seconds" in st_end_raw
                                 or "max_seconds" in st_end_raw)):
                        st_dur_range = parse_event_duration_range(ctx,
                            st_end_raw, tr("{0} end").format(where))
                        (etype, count, ms, ev_c_ref, ev_d_refs,
                         ev_end_conds, ev_end_states) = (
                            END_DURATION, 1, 0, None, None, (), ())
                    else:
                        (etype, count, ms, ev_c_ref, ev_d_refs,
                         ev_end_conds, ev_end_states) = parse_event_end(ctx,
                            st_end_raw, where)

                    if etype == END_PLAYS and not any(
                            has_content_channels(ctx, st.channels)
                            for st in states.values()):
                        # =63: plays は音声/動画の本数だけを数えるので、
                        # どのステートにもコンテンツが無いと永久に成立しない
                        raise ValueError(
                            tr('{0}: スクリプト専用チャンネルだけのイベントに「N回の再生で次へ」は使えません(再生回数は音声・動画のみ数えます)').format(where))

                    # end states の存在チェック
                    for s in ev_end_states:
                        if s not in states:
                            raise ValueError(
                                tr("{0}: end states の '{1}' が存在しません").format(where, s))

                    # 移行先の参照整合性
                    for st in states.values():
                        if st.transition:
                            for t in st.transition.targets:
                                if t not in states:
                                    raise ValueError(
                                        tr("{0} ステート'{1}': 移行先 '{2}' が存在しません").format(where, st.state_id, t))
                            et = st.transition.else_to
                            if et is not None and et not in states:
                                raise ValueError(
                                    tr("{0} ステート'{1}': 移行先(else) '{2}' が存在しません").format(where, st.state_id, et))
                            # =77: 全候補実行済み時の固定移行先も存在チェック
                            xt = st.transition.exhausted_to
                            if xt is not None and xt not in states:
                                raise ValueError(
                                    tr("{0} ステート'{1}': 移行先(実行済み時) '{2}' が存在しません").format(where, st.state_id, xt))
                            # cond / all_channels / state_time(=62) 形式は
                            # チャンネルを見ないので検証を省く
                            if (st.transition.when_type
                                    not in (WHEN_COND, WHEN_ALL_CHANNELS,
                                            WHEN_STATE_TIME, WHEN_CHOICE)
                                    and st.transition.channel not in st.channels):
                                raise ValueError(
                                    tr("{0} ステート'{1}': transition の channel '{2}' がこのステートにありません").format(where, st.state_id, st.transition.channel))

                    events[event_id] = ScenarioEvent(
                        event_id=event_id,
                        states=states,
                        start_state=start_state,
                        end_type=etype, end_count=count, end_duration_ms=ms,
                        end_count_ref=ev_c_ref, end_duration_refs=ev_d_refs,
                        end_conds=ev_end_conds, end_states=ev_end_states,
                        **(st_dur_range or {}),
                        next=next_str, next_rule=next_rule, next_choice=next_choice,
                        next_cond=next_cond, next_input=next_input,
                        advance=advance, advance_ref=advance_ref,
                        on_start=on_start_ops,
                        on_end=on_end_ops,
                    )

                elif "channels" in raw or "video" in raw or "items" not in raw:
                    # --- 単一ステート(チャンネル形式) → "main"ステートに正規化 ---
                    if raw.get("video") is not None and "items" in raw:
                        raise ValueError(
                            tr('{0}: video と items 直書き形式は併用できません(channels を使ってください)').format(where))
                    # channels 省略(itemsも無し)/空dict かつ動画なし =
                    # 音声なしイベント(分岐・変数操作だけの即時通過ノード)。
                    if not raw.get("channels") and raw.get("video") is None:
                        # =62(フェーズ2): 終了条件に「経過時間(duration)」を
                        # 指定すると**無音待機ノード**になる(指定秒だけ何も
                        # 再生せずに待ってから次へ)。それ以外の終了条件は、
                        # 成立させる主体(チャンネル)が居ないため引き続きエラー。
                        # ※ cond は待機中に変数を書き換える実行主体が存在せず
                        #   永久待機になるので許可しない(=62でユーザー決定)。
                        raw_end = raw.get("end")
                        etype, count, ms = END_ONCE, 1, 0
                        ev_d_refs = None
                        silent_range = None
                        if raw_end is not None:
                            if (not isinstance(raw_end, dict)
                                    or raw_end.get("type") != END_DURATION):
                                raise ValueError(
                                    tr('{0}: 音声なし(チャンネルなし)のイベントの終了条件は経過時間(duration)のみ指定できます').format(where))
                            if ("min_seconds" in raw_end
                                    or "max_seconds" in raw_end):
                                silent_range = parse_event_duration_range(ctx,
                                    raw_end, tr("{0} end").format(where))
                                etype = END_DURATION
                            else:
                                (etype, count, ms, _c_ref,
                                 ev_d_refs) = parse_end(ctx,
                                    raw_end, MODE_SEQUENTIAL, where)
                        if raw.get("device"):
                            raise ValueError(
                                tr('{0}: 音声なし(チャンネルなし)に device は指定できません').format(where))
                        state = EventState(
                            state_id="main", channels={}, device_map={},
                            seek_channel=parse_seek_channel(ctx,
                                raw.get("seek_channel"), {}, where),
                            # =290: 音声なしイベントも bgm(指定/引き継ぐ/オフ)を
                            # 持てる。従来は読み落としていて、選択肢だけの
                            # イベントで「BGMオフ」が効かなかった(ユーザー報告)
                            bgm=parse_bgm(ctx, raw.get("bgm"), where))
                        events[event_id] = ScenarioEvent(
                            event_id=event_id,
                            states={"main": state}, start_state="main",
                            end_type=etype, end_count=count, end_duration_ms=ms,
                            end_duration_refs=ev_d_refs,
                            next=next_str, next_rule=next_rule,
                            next_choice=next_choice,
                            next_cond=next_cond, next_input=next_input,
                            advance=advance, advance_ref=advance_ref,
                            on_start=on_start_ops,
                            on_end=on_end_ops,
                            **(silent_range or {}),
                        )
                        continue
                    # 旧形式(直下 video)は動画チャンネルへ移行してから解析
                    # する(=52)。end 未指定の非ループ動画には
                    # end=channel(動画ch) が補われる(旧挙動の再現)。
                    raw = migrate_video_node(raw, where)
                    has_video_ch = bool(raw_video_channel(raw))
                    event_end_raw = raw.get("end")
                    end_channel = None
                    end_conds = ()
                    is_duration = (isinstance(event_end_raw, dict)
                                   and event_end_raw.get("type") == END_DURATION)
                    # =168: 無限。イベント自身の終了条件を持たない
                    is_infinite = (isinstance(event_end_raw, dict)
                                   and event_end_raw.get("type") == END_NONE)
                    if (isinstance(event_end_raw, dict)
                            and event_end_raw.get("type") == END_CHANNEL):
                        end_channel = event_end_raw.get("channel")
                        if not end_channel:
                            raise ValueError(tr('{0}: end channel には channel を指定してください').format(where))
                    elif (isinstance(event_end_raw, dict)
                            and event_end_raw.get("type") == END_COND):
                        end_conds = parse_conds(ctx, event_end_raw.get("when"),
                                                tr("{0} end").format(where))
                    # 判定式・合計時間(duration)終了は全チャンネル無限再生を許可
                    # (条件成立/指定秒の経過まで再生を続ける)。
                    # 動画イベントも許可(動画終了/終了条件がイベントを終える)
                    allow_infinite = (end_channel is not None or bool(end_conds)
                                      or is_duration or has_video_ch
                                      or is_infinite)
                    # duration の範囲抽選(min_seconds/max_seconds)を先に解析
                    dur_range = None
                    if is_duration and ("min_seconds" in event_end_raw
                                        or "max_seconds" in event_end_raw):
                        dur_range = parse_event_duration_range(ctx,
                            event_end_raw, tr("{0} end").format(where))

                    channels: dict[str, Channel] = {}
                    for ch_id, ch_raw in (raw.get("channels") or {}).items():
                        if ch_id not in VALID_CHANNELS:
                            raise ValueError(
                                tr("{0}: チャンネル '{1}' は不正です(有効: {2})").format(where, ch_id, ', '.join(VALID_CHANNELS))
                            )
                        channels[ch_id] = parse_channel(ctx,
                            ch_id, ch_raw, tr('{0} チャンネル{1}').format(where, ch_id),
                            allow_infinite=allow_infinite)
                    vcid = check_video_channels(ctx, channels, where)
                    if (event_end_raw is None and vcid
                            and channels[vcid].end_type == END_NONE):
                        # 旧「ループ動画には end が必要」の後継(=52)。
                        # 動画chが無限なのにイベント側にも終了条件が無いと、
                        # そのイベントは永久に終わらない。
                        raise ValueError(
                            tr('{0}: 動画チャンネルの終了条件が「無限」のときは、イベントの終了条件(合計時間・変数条件・指定チャンネル終了など)が必要です').format(where))
                    # 動画イベントの device 既定は「担当なし」(チャンネルは
                    # デバイスを駆動しない)。明示指定した種別のみチャンネル駆動。
                    if vcid and raw.get("device") is None:
                        device_map = {}
                    else:
                        device_map = parse_device(ctx, raw.get("device"), channels,
                                                  where)
                    check_script_channels(ctx, channels, device_map, where)
                    if vcid and raw.get("seek_channel") is not None:
                        raise ValueError(
                            tr('{0}: 動画があるときシークバーは動画に固定されるため seek_channel は指定できません').format(where))

                    if end_channel is not None:
                        if end_channel not in channels:
                            raise ValueError(
                                tr("{0}: end のチャンネル '{1}' が存在しません").format(where, end_channel))
                        etype, count, ms, ev_c_ref, ev_d_refs = END_CHANNEL, 1, 0, None, None
                    elif end_conds:
                        etype, count, ms, ev_c_ref, ev_d_refs = END_COND, 1, 0, None, None
                    elif dur_range is not None:
                        # duration 範囲抽選(値は dur_range 経由で ScenarioEvent へ)
                        etype, count, ms, ev_c_ref, ev_d_refs = END_DURATION, 1, 0, None, None
                    elif is_infinite:
                        # =168: 無限(イベント自身は終わらない)
                        etype, count, ms, ev_c_ref, ev_d_refs = END_NONE, 1, 0, None, None
                    elif event_end_raw is not None:
                        etype, count, ms, ev_c_ref, ev_d_refs = parse_end(ctx,
                            event_end_raw, MODE_SEQUENTIAL, where)
                        if etype == END_PLAYS \
                                and not has_content_channels(ctx, channels):
                            # =63: plays は音声/動画の本数だけを数えるので、
                            # スクリプト専用chだけでは永久に成立しない
                            raise ValueError(
                                tr('{0}: スクリプト専用チャンネルだけのイベントに「N回の再生で次へ」は使えません(再生回数は音声・動画のみ数えます)').format(where))
                    else:
                        # イベント終了未指定 = 全チャンネルが終了したらイベント終了。
                        # (旧: linear担当chの終了条件を継承していたが、時間指定chが
                        #  他chを打ち切る直感に反する挙動になるため廃止。END_ONCEは
                        #  event_end_met で早期終了せず、全chの自然終了を待つ。)
                        etype, count, ms = END_ONCE, 1, 0
                        ev_c_ref, ev_d_refs = None, None

                    state = EventState(
                        state_id="main", channels=channels, device_map=device_map,
                        seek_channel=parse_seek_channel(ctx,
                            raw.get("seek_channel"), channels, where),
                        video_channel=vcid,
                        bgm=parse_bgm(ctx, raw.get("bgm"), where))
                    events[event_id] = ScenarioEvent(
                        event_id=event_id,
                        states={"main": state}, start_state="main",
                        end_type=etype, end_count=count, end_duration_ms=ms,
                        end_count_ref=ev_c_ref, end_duration_refs=ev_d_refs,
                        end_channel=end_channel, end_conds=end_conds,
                        next=next_str, next_rule=next_rule, next_choice=next_choice,
                        next_cond=next_cond, next_input=next_input,
                        advance=advance, advance_ref=advance_ref,
                        on_start=on_start_ops,
                        on_end=on_end_ops,
                        **(dur_range or {}),
                    )
                else:
                    # --- items直書き形式 → Cチャンネル1つの"main"ステートに正規化 ---
                    ch = parse_channel(ctx, CH_CENTER, raw, where)
                    check_script_channels(ctx,
                        {CH_CENTER: ch},
                        {t: CH_CENTER for t in VALID_TRACK_TYPES}, where)
                    state = EventState(
                        state_id="main", channels={CH_CENTER: ch},
                        device_map={t: CH_CENTER for t in VALID_TRACK_TYPES},
                        bgm=parse_bgm(ctx, raw.get("bgm"), where))
                    events[event_id] = ScenarioEvent(
                        event_id=event_id,
                        states={"main": state}, start_state="main",
                        end_type=ch.end_type, end_count=ch.end_count,
                        end_duration_ms=ch.end_duration_ms,
                        end_count_ref=ch.end_count_ref,
                        end_duration_refs=ch.end_duration_refs,
                        end_duration_range=ch.end_duration_range,
                        end_duration_min_ms=ch.end_duration_min_ms,
                        end_duration_max_ms=ch.end_duration_max_ms,
                        end_duration_min_ref=ch.end_duration_min_ref,
                        end_duration_max_ref=ch.end_duration_max_ref,
                        next=next_str, next_rule=next_rule, next_choice=next_choice,
                        next_cond=next_cond, next_input=next_input,
                        advance=advance, advance_ref=advance_ref,
                        on_start=on_start_ops,
                        on_end=on_end_ops,
                    )

        elif "nodes" in data:
            # v1 ノード形式 → 音声1件のsequentialイベント(Cチャンネル)に変換
            for node_id, raw in data["nodes"].items():
                next_str, next_rule, next_choice, next_cond, next_input = \
                    parse_next(ctx, raw.get("next"), tr("イベント '{0}'").format(node_id))
                on_start_ops = ()
                audio = resolve(ctx, raw.get("audio"))
                if not audio:
                    raise ValueError(tr("ノード '{0}' に audio がありません").format(node_id))
                funscript = resolve(ctx, raw.get("funscript"))
                tracks = [DeviceTrack(TRACK_LINEAR, funscript)] if funscript else []
                ch = Channel(
                    channel_id=CH_CENTER, mode=MODE_SEQUENTIAL,
                    items=[EventItem(audio=audio, tracks=tracks)],
                    end_type=END_ONCE, pan=Pan(*DEFAULT_PAN[CH_CENTER]),
                )
                state = EventState(
                    state_id="main", channels={CH_CENTER: ch},
                    device_map={t: CH_CENTER for t in VALID_TRACK_TYPES})
                events[node_id] = ScenarioEvent(
                    event_id=node_id,
                    states={"main": state}, start_state="main",
                    end_type=END_ONCE,
                    next=next_str, next_rule=next_rule, next_choice=next_choice,
                    next_cond=next_cond, next_input=next_input,
                    on_start=on_start_ops,
                )
        else:
            raise ValueError(tr("シナリオファイルに events (または nodes) がありません"))

        start = data.get("start")
        if not start or start not in events:
            raise ValueError(tr("start イベントが見つかりません"))

        watches = parse_watch(ctx, data.get("watch"))

        # 参照整合性チェック
        errors = []
        # =262: 背景イラストの存在チェック(音声等と同じくエラー扱い)
        if background is not None and not os.path.exists(background.file):
            errors.append(
                tr("背景画像ファイルが見つかりません: {0}").format(
                    background.file))
        for i, w in enumerate(watches):
            if w.to not in events:
                errors.append(
                    tr("watch[{0}] の to '{1}' が存在しません").format(i, w.to))
        for ev in events.values():
            # =168: 終了条件が「無限」のイベントは自分では終わらないので、
            # 「固定」(next文字列)や「分岐」(random)は永久に発火しない。
            # 出口になりうるのは なし(遷移なし)/選択肢/数値入力/
            # 判定式(再生中に常時監視)だけ。
            if ev.end_type == END_NONE:
                if ev.next:
                    errors.append(
                        tr("イベント '{0}': 終了条件が「無限」のときは固定の遷移先を指定できません(選択肢・数値入力・判定式・遷移なしのいずれかにしてください)").format(ev.event_id))
                if ev.next_rule:
                    errors.append(
                        tr("イベント '{0}': 終了条件が「無限」のときはランダム分岐を指定できません(選択肢・数値入力・判定式・遷移なしのいずれかにしてください)").format(ev.event_id))
            if ev.next and ev.next not in events:
                errors.append(tr("イベント '{0}' の next '{1}' が存在しません").format(ev.event_id, ev.next))
            if ev.next_rule or ev.next_choice or ev.next_cond or ev.next_input:
                targets = []
                if ev.next_rule:
                    targets += [c[0] for c in ev.next_rule.candidates]
                    if ev.next_rule.exhausted_to:
                        targets.append(ev.next_rule.exhausted_to)
                    if ev.next_rule.else_to:
                        targets.append(ev.next_rule.else_to)
                if ev.next_choice:
                    targets += [e.to for e in ev.next_choice.entries]
                    if ev.next_choice.default_to:
                        targets.append(ev.next_choice.default_to)
                if ev.next_cond:
                    targets += [to for _conds, to in ev.next_cond.rows]
                    if ev.next_cond.else_to:
                        targets.append(ev.next_cond.else_to)
                if ev.next_input:
                    targets.append(ev.next_input.to)
                for to in targets:
                    if to not in events:
                        errors.append(tr("イベント '{0}' の next '{1}' が存在しません").format(ev.event_id, to))
            # =275: ステート移行の選択肢のイベント宛て行き先と、イベント側の
            # 選択肢/数値入力との併用制限(表示枠は1つ=同時表示を避ける)
            if ev.has_state_choice:
                for st in ev.states.values():
                    for to in st.transition.event_targets if st.transition else ():
                        if to not in events:
                            errors.append(
                                tr("イベント '{0}' ステート'{1}': 選択肢の遷移先イベント '{2}' が存在しません").format(ev.event_id, st.state_id, to))
                rule = ev.next_choice or ev.next_input
                if rule is not None:
                    if ev.end_type == END_NONE:
                        errors.append(
                            tr("イベント '{0}': ステート移行に選択肢があるときは、終了条件が「無限」のイベントにイベント側の選択肢/数値入力を併用できません").format(ev.event_id))
                    elif rule.show_mode != "end":
                        errors.append(
                            tr("イベント '{0}': ステート移行に選択肢があるときは、イベント側の選択肢/数値入力の表示タイミングは「イベント終了条件の達成時」のみ使えます").format(ev.event_id))
            for st in ev.states.values():
              # =256: BGMアイテムの存在チェック(通常アイテムと同じ扱い)
              if st.bgm is not None:
                  for it in st.bgm.items:
                      if not os.path.exists(it.audio):
                          errors.append(
                              tr('BGMの音声ファイルが見つかりません: {0}').format(it.audio))
              for ch in st.channels.values():
                for item in ch.items:
                    if item.audio and not os.path.exists(item.audio):
                        errors.append(tr('音声ファイルが見つかりません: {0}').format(item.audio))
                    if item.video and not os.path.exists(item.video):
                        errors.append(
                            tr('動画ファイルが見つかりません: {0}').format(item.video))
                    missing = False
                    for track in item.tracks:
                        if not os.path.exists(track.funscript):
                            missing = True
                            errors.append(
                                tr('funscriptファイルが見つかりません({0}): {1}').format(track.type, track.funscript)
                            )
                    # =59: 区間指定が素材の長さを超えていないかを検証する
                    # (ユーザー決定: 音声・スクリプトは長さが分かるので
                    #  読み込み時にエラー。動画だけは mpv が無いと分からない
                    #  ので従来どおり再生時の警告=54に任せる)。
                    if (item.audio and item.has_range
                            and os.path.exists(item.audio)):
                        adur = wav_duration_ms(item.audio)
                        if adur > 0 and item.start_s * 1000 >= adur:
                            errors.append(
                                tr('区間の開始({0:g}秒)が音声の長さ({1:g}秒)を超えています: {2}').format(
                                    item.start_s, adur / 1000,
                                    os.path.basename(item.audio)))
                    for track in item.tracks:
                        if not track.has_range or missing:
                            continue
                        try:
                            src = load_script_source(track.funscript)
                        except Exception:
                            continue    # 読み込み失敗は下の一括検証で報告
                        if (src.duration_ms > 0
                                and track.start_s * 1000 >= src.duration_ms):
                            errors.append(
                                tr('区間の開始({0:g}秒)がスクリプトの長さ({1:g}秒)を超えています: {2}').format(
                                    track.start_s, src.duration_ms / 1000,
                                    os.path.basename(track.funscript)))
                    if not item.audio and not item.video and not missing:
                        # スクリプトのみアイテム: 長さ0(=高速空回りの原因)を
                        # 読み込み時に検出する。再生時間は全トラックの最大長。
                        # 動画アイテム(=52)は再生長を動画が決めるので対象外
                        # (トラック0本=デバイスを動かさない動画も許容する)。
                        # =59: 区間指定があるときは**切り出した後**で判定する。
                        # =121: 区間終了の明示で duration_ms は区間の長さに
                        # なる(末尾の停止区間を含む)ため、「動作なし」は
                        # 長さでなく**アクション/ステップの有無**で判定する
                        # (動作0のままアイテムが走ると初期指令が無く、直前の
                        # 動作が続いてしまう事故のもと)。
                        max_dur = 0
                        any_motion = False
                        broken = False
                        for track in item.tracks:
                            try:
                                src = load_script_source(track.funscript)
                                lo, hi = track_range_ms(item, track)
                                if lo > 0 or hi is not None:
                                    src = src.sliced(lo, hi)
                                max_dur = max(max_dur, src.duration_ms)
                                if (getattr(src, "actions", None)
                                        or getattr(src, "steps", None)):
                                    any_motion = True
                            except Exception:
                                broken = True
                                errors.append(
                                    tr('スクリプトを読み込めません({0}): {1}').format(track.type, track.funscript))
                        if not broken and (max_dur <= 0 or not any_motion):
                            if item.has_range or any(t.has_range
                                                     for t in item.tracks):
                                errors.append(
                                    tr('指定した区間にスクリプトの動作がありません: {0}').format(
                                        os.path.basename(item.tracks[0].funscript)))
                            else:
                                errors.append(
                                    tr('スクリプトの長さが0です(アクションがありません): {0}').format(item.tracks[0].funscript))
                    # =74: weight の 0/負は許容(実行時に「抽選に出さない」候補。
                    # 全アイテム0以下はチャンネル自然終了)。正数チェックは廃止
        if errors:
            raise ValueError(tr("シナリオファイルにエラーがあります:\n") + "\n".join(errors))

        return cls(
            title=data.get("title", os.path.basename(path)),
            detail=str(data.get("detail", "") or ""),
            start=start,
            events=events,
            path=path,
            var_decls=var_decls,
            watches=tuple(watches),
            load_warnings=load_warnings,
            device_enabled=device_enabled,
            bgm_enabled=bgm_enabled,
            background=background,
        )
