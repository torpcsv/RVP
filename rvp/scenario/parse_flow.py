"""シナリオ読み込み: 遷移(next/選択肢/数値入力/判定式)・ステート移行・イベント終了条件のパース。"""
from __future__ import annotations

from .model import (Channel, ChoiceEntry, ChoiceRule, EventState, InputRule,
    NextRule, StateTransition, _is_num, check_node_color)
from .constants import (END_COND, END_DURATION, END_NONE, END_PLAYS,
    END_STATES, END_TRANSITIONS, VALID_CHANNELS, WHEN_ALL_CHANNELS,
    WHEN_CHANNEL_COUNT, WHEN_CHANNEL_END, WHEN_CHANNEL_TIME_REMOVED,
    WHEN_CHOICE, WHEN_COND, WHEN_STATE_TIME)
from .video import migrate_video_node
from ..i18n import tr

from .parse_channels import (check_script_channels, check_video_channels,
    parse_channel, parse_device, parse_seek_channel)
from .parse_items import parse_bgm
from .parse_vars import (_require_var, parse_cond_next, parse_conds,
    parse_numref, parse_ops)


def parse_show(ctx, raw, where):
    """choice/input共通の show(表示タイミング)指定を解析する。"""
    if raw in ("start", "end"):
        return raw, 0
    if isinstance(raw, dict):
        # 時間指定は秒のみ(minutesは廃止=読まない)
        show_ms = int(float(raw.get("seconds", 0)) * 1_000)
        if show_ms < 0:
            raise ValueError(
                tr("{0}: show の時間指定が不正です").format(where))
        return "ms", show_ms
    raise ValueError(
        tr('{0}: show は "start" / "end" / {{"seconds"}} にしてください').format(where))


def parse_choice_to(ctx, v, where, state_mode: bool):
    """=275: choice の行き先を (to, to_event) へ解析する。

    通常(イベントの next): 文字列=イベントID のみ。
    ステート移行(state_mode): 文字列=ステートID、
    {"event": "イベントID"}=イベントを終了して飛ぶ。
    """
    if isinstance(v, str) and v:
        return v, False
    if state_mode and isinstance(v, dict) \
            and isinstance(v.get("event"), str) and v["event"]:
        return v["event"], True
    if state_mode:
        raise ValueError(
            tr('{0}: 行き先はステートIDの文字列か {{"event": "イベントID"}} で指定してください').format(where))
    raise ValueError(
        tr("{0}: 行き先はイベントIDの文字列で指定してください").format(where))


def parse_choice(ctx, raw, where, state_mode: bool = False):
    """next の choice 形式を ChoiceRule へ解析する。

    {"choice": [{"label": "...", "to": "..."}, ...],   1〜9件
     "timeout": {"seconds": 30},  省略=無制限
     "default": "random" | {"to": "..."},  タイムアウト/▶▶スキップ時の行き先。
                省略=先頭候補。旧形式のtimeout.toも受理(defaultと併記はエラー)
     "skip": "default"(既定) | "stay",  =274: "stay"=選択必須(▶▶で飛ばさない)
     "show": "start" | "end"(既定) | {"seconds": 90}}

    =275: state_mode=True(ステート移行の選択肢)では、各 to と
    default.to に「ステートID」または {"event": "イベントID"} を
    受理する。show の意味はステート基準(start=ステート開始時 /
    end=ステート内の全チャンネル終了時 / seconds=ステート開始から)。
    """
    entries_raw = raw.get("choice")
    if not isinstance(entries_raw, list) or not (1 <= len(entries_raw) <= 9):
        raise ValueError(
            tr("{0}: choice は1〜9件のリストで指定してください").format(where))
    entries = []
    for i, ent in enumerate(entries_raw):
        if not isinstance(ent, dict) or ent.get("to") is None:
            raise ValueError(
                tr("{0}: choice[{1}] には label と to が必要です").format(where, i))
        to, to_event = parse_choice_to(ctx,
            ent["to"], tr("{0} choice[{1}]").format(where, i), state_mode)
        label = str(ent.get("label", "") or "").strip()
        if not label:
            raise ValueError(
                tr("{0}: choice[{1}] の label が空です").format(where, i))
        ops = parse_ops(ctx, ent.get("ops"),
                        tr("{0} choice[{1}]").format(where, i))
        entries.append(ChoiceEntry(label=label, to=to, ops=ops,
                                   to_event=to_event))

    on_timeout = parse_ops(ctx, raw.get("on_timeout"),
                           tr("{0} on_timeout").format(where))

    timeout_ms, legacy_to = None, None
    traw = raw.get("timeout")
    if traw is not None:
        if not isinstance(traw, dict):
            raise ValueError(
                tr("{0}: timeout は seconds のオブジェクトで指定してください").format(where))
        # 時間指定は秒のみ(minutesは廃止=読まない)
        ms = int(float(traw.get("seconds", 0)) * 1_000)
        if ms <= 0:
            raise ValueError(
                tr("{0}: timeout には seconds を指定してください").format(where))
        timeout_ms = ms
        if traw.get("to") is not None:   # 旧形式(timeout.to)の互換受理
            if not isinstance(traw["to"], str):
                raise ValueError(
                    tr("{0}: timeout の to はイベントIDの文字列にしてください").format(where))
            legacy_to = traw["to"]

    # デフォルト遷移先(タイムアウト時・▶▶スキップ時共通)
    default_mode, default_to = "first", None
    default_to_event = False
    draw = raw.get("default")
    if draw is not None:
        if legacy_to is not None:
            raise ValueError(
                tr('{0}: "default" と timeout の to は同時に指定できません').format(where))
        if draw == "random":
            default_mode = "random"
        elif isinstance(draw, dict) and draw.get("to") is not None:
            default_to, default_to_event = parse_choice_to(ctx,
                draw["to"], tr("{0} default").format(where), state_mode)
            default_mode = "to"
        else:
            raise ValueError(
                tr('{0}: default は "random" か {{"to": "..."}} で指定してください').format(where))
    elif legacy_to is not None:
        default_mode, default_to = "to", legacy_to

    show_mode, show_ms = parse_show(ctx, raw.get("show", "end"), where)

    # =274: 選択必須(▶▶で飛ばさない)。"default"/"stay" 以外はエラー
    skip_raw = raw.get("skip", "default")
    if skip_raw not in ("default", "stay"):
        raise ValueError(
            tr('{0}: skip は "default" か "stay" で指定してください').format(where))

    return ChoiceRule(entries=entries, timeout_ms=timeout_ms,
                      on_timeout=on_timeout,
                      default_mode=default_mode, default_to=default_to,
                      skip_stay=(skip_raw == "stay"),
                      show_mode=show_mode, show_ms=show_ms,
                      default_to_event=default_to_event)


def parse_input(ctx, raw, where):
    """next の input 形式を InputRule へ解析する。

    {"input": {"var": "min_len", "label": "再生時間(分)",
               "to": "main_play", "min": 1, "max": 30},
     "show": "start" | "end"(既定) | {"seconds"}}

    min/max は受理する入力範囲(省略時は変数宣言のmin/max)。
    宣言のmin/maxの外へ広げる指定は読み込みエラー。
    """
    iraw = raw.get("input")
    if not isinstance(iraw, dict):
        raise ValueError(
            tr("{0}: input はオブジェクトで指定してください").format(where))
    decl = _require_var(ctx, iraw.get("var"),
                        tr("{0} input").format(where))
    if not decl.is_number:
        raise ValueError(
            tr("{0}: 変数 '{1}' は数値変数ではありません").format(
                tr("{0} input").format(where), decl.name))
    to = iraw.get("to")
    if not isinstance(to, str) or not to:
        raise ValueError(
            tr("{0}: input の to はイベントIDの文字列で指定してください").format(where))
    label = str(iraw.get("label", "") or "").strip()

    def _bound(key):
        if key not in iraw:
            return None
        v = iraw[key]
        if not _is_num(v):
            raise ValueError(
                tr("{0}: input の min/max は数値で指定してください").format(where))
        return float(v)

    mn, mx = _bound("min"), _bound("max")
    if mn is not None and mx is not None and mn > mx:
        raise ValueError(
            tr("{0}: input の min は max 以下にしてください").format(where))
    # 宣言のmin/maxより広い入力範囲は矛盾(セット時にクランプされてしまう)
    if ((mn is not None and decl.vmin is not None and mn < decl.vmin)
            or (mx is not None and decl.vmax is not None and mx > decl.vmax)):
        raise ValueError(
            tr("{0}: input の min/max が変数 '{1}' の宣言範囲の外です").format(where, decl.name))
    vmin = mn if mn is not None else decl.vmin
    vmax = mx if mx is not None else decl.vmax
    show_mode, show_ms = parse_show(ctx, raw.get("show", "end"), where)
    skip_raw = raw.get("skip", "default")
    if skip_raw not in ("default", "stay"):
        raise ValueError(
            tr("{0}: skip は \"default\" か \"stay\" で指定してください").format(where))
    return InputRule(var=decl.name, to=to, label=label,
                     vmin=vmin, vmax=vmax,
                     show_mode=show_mode, show_ms=show_ms,
                     skip_stay=(skip_raw == "stay"))


def parse_transition(ctx, raw, where) -> StateTransition | None:
    """transition指定を解析する。

    {"when": {"type": "state_time",
              "min_seconds": 60, "max_seconds": 90},   # or "seconds": 30
     "to": "S2"}                                        # or {"random": ["S1","S2"]}
    {"when": {"type": "channel_count", "channel": "R",
              "min": 10, "max": 15},                    # or "count": 10
     "to": ...}
    """
    if raw is None:
        return None
    when = raw.get("when")
    to = raw.get("to")
    wtype = when.get("type") if isinstance(when, dict) else None
    if wtype == WHEN_CHOICE:
        # =275: 選択肢でステート移行。to は持たず、choice/timeout/
        # default/skip/show を transition 直下に置く(next choice と
        # 同じ文法。行き先はステートID or {"event": ID})。
        if to is not None:
            raise ValueError(
                tr('{0}: 選択肢(choice)のステート移行では to は指定できません(行き先は各選択肢に書きます)').format(where))
        rule = parse_choice(ctx, raw, tr("{0} transition").format(where),
                            state_mode=True)
        return StateTransition(
            when_type=WHEN_CHOICE, channel="",
            min_value=0, max_value=0, candidates=[],
            choice=rule)
    if not isinstance(when, dict) or to is None:
        raise ValueError(tr('{0}: transition には when と to が必要です').format(where))

    def _parse_candidates():
        """to を (candidates, else_to, visitedオプション, cond_rows) へ解析する(=73/=125)。

        candidates の要素は (state_id, weight定数|None, weight変数名|None)。
        文字列候補は重み1の定数。weight は 0/負も許容(実行時に
        0以下=出さない。=26のイベント版と同じ規則)。
        =77: 第3戻り値は StateTransition へ渡す visited系kwargs
        (visited_exclude / exhausted_mode / exhausted_to)。文法は
        =26の next と同じ "visited" / "when_exhausted" キー(toのdict内)。
        =125: 第4戻り値は判定式(cond)形式の行 [(conds, to)]。
        {"cond": [{"when": [...], "to": "S1"}, ...], "else": "Sx"}。
        書式はイベント next の cond と同じ。**else は省略可**で、
        省略時(または null)は「どの行も成立しなければ移行しない」
        (イベント版の else 必須とは意図的に異なる=ユーザー決定)。
        random と cond は同時指定不可。
        """
        if isinstance(to, str):
            return [(to, 1.0, None)], None, {}, []
        if isinstance(to, dict) and isinstance(to.get("cond"), list):
            if to.get("random") is not None:
                raise ValueError(
                    tr("{0}: to の random と cond は同時に指定できません").format(where))
            rows_raw = to["cond"]
            if not rows_raw:
                raise ValueError(
                    tr("{0}: cond は1件以上のリストで指定してください").format(where))
            rows = []
            for i, row in enumerate(rows_raw):
                w = tr("{0} cond[{1}]").format(where, i)
                if not isinstance(row, dict) \
                        or not isinstance(row.get("to"), str):
                    raise ValueError(
                        tr("{0}: when と to が必要です").format(w))
                rows.append((parse_conds(ctx, row.get("when"), w),
                             row["to"]))
            else_to = to.get("else")
            if else_to is not None and not isinstance(else_to, str):
                raise ValueError(
                    tr('{0}: transition の else はステートIDの文字列で指定してください').format(where))
            return [], else_to, {}, rows
        if isinstance(to, dict) and isinstance(to.get("random"), list) \
                and to["random"]:
            cands = []
            for i, ent in enumerate(to["random"]):
                if isinstance(ent, str):
                    cands.append((ent, 1.0, None))
                elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                    w_const, w_ref = parse_numref(ctx,
                        ent.get("weight", 1.0),
                        tr("{0}: transition random[{1}] の weight").format(where, i))
                    cands.append((ent["to"], w_const,
                                  w_ref.var if w_ref is not None else None))
                else:
                    raise ValueError(
                        tr("{0}: transition random[{1}] の要素が不正です").format(where, i))
            else_to = to.get("else")
            if else_to is not None and not isinstance(else_to, str):
                raise ValueError(
                    tr('{0}: transition の else はステートIDの文字列で指定してください').format(where))
            visited = to.get("visited", "include")
            if visited not in ("include", "exclude"):
                raise ValueError(
                    tr("{0}: transition の visited は include か exclude にしてください").format(where))
            ex = to.get("when_exhausted", "all")
            mode, ex_to = "all", None
            if ex in ("all", "reset"):
                mode = ex
            elif isinstance(ex, dict) and isinstance(ex.get("to"), str):
                mode, ex_to = "to", ex["to"]
            else:
                raise ValueError(
                    tr('{0}: transition の when_exhausted は "all" / "reset" / {{"to": "..."}} にしてください').format(where))
            vk = {"visited_exclude": visited == "exclude",
                  "exhausted_mode": mode, "exhausted_to": ex_to}
            return cands, else_to, vk, []
        raise ValueError(tr('{0}: to は文字列か {{"random": [...]}} か {{"cond": [...]}} で指定してください').format(where))

    # 判定式(cond)形式: チャンネル/閾値は不要、変数の判定式で移行
    if wtype == WHEN_COND:
        conds = parse_conds(ctx, when.get("conds"),
                            tr("{0} transition").format(where))
        cands, else_to, vk, crows = _parse_candidates()
        return StateTransition(
            when_type=WHEN_COND, channel="",
            min_value=0, max_value=0, candidates=cands,
            else_to=else_to, conds=conds, cond_rows=crows, **vk)

    # 全チャンネル終了形式: チャンネル/閾値不要
    if wtype == WHEN_ALL_CHANNELS:
        cands, else_to, vk, crows = _parse_candidates()
        return StateTransition(
            when_type=WHEN_ALL_CHANNELS, channel="",
            min_value=0, max_value=0, candidates=cands,
            else_to=else_to, cond_rows=crows, **vk)

    # 指定チャンネル終了形式: channel のみ必要(閾値不要)
    if wtype == WHEN_CHANNEL_END:
        channel = when.get("channel")
        if channel not in VALID_CHANNELS:
            raise ValueError(tr("{0}: when の channel '{1}' は不正です").format(where, channel))
        cands, else_to, vk, crows = _parse_candidates()
        return StateTransition(
            when_type=WHEN_CHANNEL_END, channel=channel,
            min_value=0, max_value=0, candidates=cands,
            else_to=else_to, cond_rows=crows, **vk)

    channel = when.get("channel")
    # =165: channel_time は廃止(state_time と役割が重複するため)。
    # 気づかず動き続けるより明確に落とすほうが安全なので専用の
    # メッセージでエラーにする(ユーザー決定 2026-08-16=B案)。
    if wtype == WHEN_CHANNEL_TIME_REMOVED:
        raise ValueError(
            tr("{0}: when type 'channel_time' は廃止しました。"
               "'state_time'(経過時間)を使ってください").format(where))
    if wtype not in (WHEN_CHANNEL_COUNT, WHEN_STATE_TIME):
        raise ValueError(tr("{0}: when type '{1}' は不正です(有効: {2}, {3}, {4}, {5}, {6}, {7})").format(where, wtype, WHEN_CHANNEL_COUNT, WHEN_STATE_TIME, WHEN_COND, WHEN_ALL_CHANNELS, WHEN_CHANNEL_END, WHEN_CHOICE))
    if wtype == WHEN_STATE_TIME:
        # =62: ステート滞在時間はチャンネルを見ない(音声なしでも使える)
        channel = ""
    elif channel not in VALID_CHANNELS:
        raise ValueError(tr("{0}: when の channel '{1}' は不正です").format(where, channel))

    lo_ref = hi_ref = None
    if wtype == WHEN_STATE_TIME:
        if "seconds" in when:
            v, r = parse_numref(ctx, when["seconds"], where)
            if r is not None:
                lo_ref = hi_ref = r
                lo = hi = 0.0
            else:
                lo = hi = v * 1000
        else:
            lo_v, lo_ref = parse_numref(ctx, when.get("min_seconds", 0), where)
            hi_v, hi_ref = parse_numref(ctx,
                when.get("max_seconds",
                         when.get("min_seconds", 0)), where)
            lo = (lo_v or 0) * 1000 if lo_ref is None else 0.0
            hi = (hi_v or 0) * 1000 if hi_ref is None else 0.0
        if lo_ref is None and hi_ref is None:
            if hi < lo:
                lo, hi = hi, lo
            if hi <= 0:
                raise ValueError(tr('{0}: {1} には秒数を指定してください').format(where, wtype))
    else:
        if "count" in when:
            v, r = parse_numref(ctx, when["count"], where)
            if r is not None:
                lo_ref = hi_ref = r
                lo = hi = 1
            else:
                lo = hi = int(v)
        else:
            lo_v, lo_ref = parse_numref(ctx, when.get("min", 0), where)
            hi_v, hi_ref = parse_numref(ctx,
                when.get("max", when.get("min", 0)), where)
            lo = int(lo_v) if lo_ref is None else 1
            hi = int(hi_v) if hi_ref is None else 1
        if lo_ref is None and hi_ref is None:
            if hi < lo:
                lo, hi = hi, lo
            if hi < 1:
                raise ValueError(tr('{0}: channel_count には1以上の回数を指定してください').format(where))

    cands, else_to, vk, crows = _parse_candidates()

    return StateTransition(
        when_type=wtype, channel=channel,
        min_value=lo, max_value=hi, candidates=cands,
        else_to=else_to, cond_rows=crows,
        min_ref=lo_ref, max_ref=hi_ref, **vk,
    )


def parse_state(ctx, state_id, raw, where) -> EventState:
    # 旧形式(直下 video)は動画チャンネルへ移行してから解析する(=52)
    raw = migrate_video_node(raw, where, is_state=True)
    # =124 ノードの着色(表示専用)
    check_node_color(raw, where)
    channels: dict[str, Channel] = {}
    for ch_id, ch_raw in (raw.get("channels") or {}).items():
        if ch_id not in VALID_CHANNELS:
            raise ValueError(tr("{0}: チャンネル '{1}' は不正です").format(where, ch_id))
        channels[ch_id] = parse_channel(ctx,
            ch_id, ch_raw, tr('{0} チャンネル{1}').format(where, ch_id),
            allow_infinite=True, default_infinite=True)
    vcid = check_video_channels(ctx, channels, where)
    # channels 省略/空dict = 音声なしステート(即時通過ノード)。
    # ただし「チャンネルがあるのに items 空」等は parse_channel が
    # 従来どおりエラーにする(作りかけの検出は維持)。
    if not channels:
        if raw.get("device"):
            raise ValueError(
                tr('{0}: 音声なし(チャンネルなし)に device は指定できません').format(where))
        transition = parse_transition(ctx, raw.get("transition"), where)
        # =62: 経過時間(state_time)= 無音待機ノード。チャンネルを見ない
        # 移行条件だけが使える(channel_* は対象chが無いので不可)。
        # =275: 選択肢(choice)も可(選択されるまで無音で待機する)
        if transition is not None and transition.when_type not in (
                WHEN_COND, WHEN_STATE_TIME, WHEN_CHOICE):
            raise ValueError(
                tr('{0}: 音声なしステートの移行条件は「なし」「経過時間」「判定式(cond)」「選択肢」のみ使用できます').format(where))
        on_start = parse_ops(ctx, raw.get("on_start"),
                             tr("{0} on_start").format(where))
        on_end = parse_ops(ctx, raw.get("on_end"),
                           tr("{0} on_end").format(where))
        seek_ch = parse_seek_channel(ctx, raw.get("seek_channel"),
                                     channels, where)
        return EventState(
            state_id=state_id, channels={}, device_map={},
            transition=transition,
            on_start=on_start, on_end=on_end,
            seek_channel=seek_ch,
            bgm=parse_bgm(ctx, raw.get("bgm"), where),
        )
    # 動画ステートの device 既定は「担当なし」(チャンネルはデバイスを
    # 駆動しない=全種別が動画側)。明示指定した種別のみチャンネル駆動。
    if vcid and raw.get("device") is None:
        device_map = {}
    else:
        device_map = parse_device(ctx, raw.get("device"), channels, where)
    check_script_channels(ctx, channels, device_map, where)
    if vcid and raw.get("seek_channel") is not None:
        raise ValueError(
            tr('{0}: 動画があるときシークバーは動画に固定されるため seek_channel は指定できません').format(where))
    transition = parse_transition(ctx, raw.get("transition"), where)
    on_start = parse_ops(ctx, raw.get("on_start"),
                         tr("{0} on_start").format(where))
    on_end = parse_ops(ctx, raw.get("on_end"),
                       tr("{0} on_end").format(where))
    seek_ch = parse_seek_channel(ctx, raw.get("seek_channel"),
                                 channels, where)
    return EventState(
        state_id=state_id, channels=channels,
        device_map=device_map, transition=transition,
        on_start=on_start, on_end=on_end,
        seek_channel=seek_ch, video_channel=vcid,
        bgm=parse_bgm(ctx, raw.get("bgm"), where),
    )


def parse_event_end(ctx, raw_end, where):
    """イベントレベル(マルチステート)の終了条件を解析する。

    戻り値: (etype, count, ms, count_ref, dur_refs, end_conds, end_states)。
    end_conds は type=="cond"、end_states は type=="states" のときのみ非空。
    """
    if raw_end is None:
        raise ValueError(
            tr('{0}: ステートを持つイベントには end (duration/plays/transitions/cond/states/none) が必要です').format(where))
    etype = raw_end.get("type")
    if etype == END_COND:
        conds = parse_conds(ctx, raw_end.get("when"),
                            tr("{0} end").format(where))
        return (END_COND, 1, 0, None, None, conds, ())
    if etype == END_STATES:
        states_raw = raw_end.get("states")
        if not isinstance(states_raw, list) or not states_raw:
            raise ValueError(
                tr('{0}: end states には終了ステートIDを1つ以上指定してください').format(where))
        end_states = tuple(str(s) for s in states_raw)
        return (END_STATES, 1, 0, None, None, (), end_states)
    if etype == END_NONE:
        # =168: 無限。イベント自身は終わらない(出口は「なし」=終わらない/
        # 選択肢/数値入力/判定式の常時監視のいずれか)。
        return (END_NONE, 1, 0, None, None, (), ())
    c_val, c_ref = parse_numref(ctx, raw_end.get("count", 1), where)
    # 時間指定は秒のみ(minutesは廃止=読まない。旧minutesは無効)
    s_val, s_ref = parse_numref(ctx, raw_end.get("seconds", 0), where)
    count = int(c_val) if c_ref is None else 1
    ms = 0
    dur_refs = None
    if s_ref is not None:
        dur_refs = (None, s_ref)
    else:
        ms = int((s_val or 0) * 1_000)
    if etype not in (END_DURATION, END_PLAYS, END_TRANSITIONS):
        raise ValueError(
            tr('{0}: ステートを持つイベントの end は duration/plays/transitions/cond/states/none のみです').format(where))
    if etype == END_DURATION and dur_refs is None and ms <= 0:
        raise ValueError(tr('{0}: duration には seconds を指定してください').format(where))
    if etype in (END_PLAYS, END_TRANSITIONS) and c_ref is None and count < 1:
        raise ValueError(tr('{0}: {1} の count は1以上にしてください').format(where, etype))
    return (etype, count, ms,
            c_ref if etype in (END_PLAYS, END_TRANSITIONS) else None,
            dur_refs if etype == END_DURATION else None,
            (), ())


def parse_next(ctx, raw, where):
    """next指定を (next_str, NextRule, ChoiceRule, CondRule, InputRule) へ解析する。

    対応形式:
      null / 省略        → 遷移なし(シナリオ終了)
      "eventB"           → 単純遷移(後方互換)
      {"random": [...]}  → 重み付きランダム分岐
         候補は "B" または {"to": "B", "weight": 2}
         "visited": "include"(既定) / "exclude"(実行済みを除外)
         "when_exhausted": "all"(既定) / "reset" / {"to": "id"}
      {"choice": ...}    → 選択肢
      {"cond": ..., "else": ...} → 変数分岐
      {"input": ...}     → 数値入力
    """
    if raw is None:
        return None, None, None, None, None
    if isinstance(raw, str):
        return raw, None, None, None, None
    if not isinstance(raw, dict):
        raise ValueError(tr("{0}: next の指定が不正です").format(where))
    forms = [k for k in ("random", "choice", "cond", "input") if k in raw]
    if len(forms) > 1:
        raise ValueError(
            tr("{0}: next の random / choice / cond / input は同時に指定できません").format(where))
    if "cond" in raw:
        return None, None, None, parse_cond_next(ctx, raw, where), None
    if "input" in raw:
        return None, None, None, None, parse_input(ctx, raw, where)
    if "choice" in raw:
        return None, None, parse_choice(ctx, raw, where), None, None
    if not isinstance(raw.get("random"), list) or not raw["random"]:
        raise ValueError(
            tr("{0}: next のオブジェクト形式には random (1件以上のリスト) が必要です").format(where))
    candidates = []
    for i, ent in enumerate(raw["random"]):
        if isinstance(ent, str):
            candidates.append((ent, 1.0, None))
        elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
            # weight は定数 or 変数参照。0/負も許容(実行時に0以下=出さない)。
            w_const, w_ref = parse_numref(ctx,
                ent.get("weight", 1.0),
                tr("{0}: next random[{1}] の weight").format(where, i))
            candidates.append((ent["to"], w_const,
                               w_ref.var if w_ref is not None else None))
        else:
            raise ValueError(
                tr("{0}: next random[{1}] の要素が不正です").format(where, i))
    else_to = raw.get("else")
    if else_to is not None and not isinstance(else_to, str):
        raise ValueError(
            tr('{0}: next の else はイベントIDの文字列で指定してください').format(where))
    visited = raw.get("visited", "include")
    if visited not in ("include", "exclude"):
        raise ValueError(
            tr("{0}: next の visited は include か exclude にしてください").format(where))
    ex = raw.get("when_exhausted", "all")
    mode, ex_to = "all", None
    if ex in ("all", "reset"):
        mode = ex
    elif isinstance(ex, dict) and isinstance(ex.get("to"), str):
        mode, ex_to = "to", ex["to"]
    else:
        raise ValueError(
            tr('{0}: next の when_exhausted は "all" / "reset" / {{"to": "..."}} にしてください').format(where))
    rule = NextRule(candidates=candidates,
                    visited_exclude=(visited == "exclude"),
                    exhausted_mode=mode, exhausted_to=ex_to,
                    else_to=else_to)
    return None, rule, None, None, None
