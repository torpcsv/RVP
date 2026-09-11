"""シナリオ読み込み: チャンネル(終了条件・interval・デバイス担当・スクリプト専用/動画チャンネルの検査・seek_channel)のパース。"""
from __future__ import annotations

from .constants import (CH_CENTER, DEFAULT_PAN, END_DURATION, END_NONE,
    END_ONCE, END_PLAYS, END_REPEAT, MODE_RANDOM, MODE_RANDOM_BAG,
    MODE_SEQUENTIAL, VALID_TRACK_TYPES)
from .model import Channel, Pan
from .tracks import normalize_track_type
from ..i18n import tr

from .parse_items import parse_item, parse_pan
from .parse_vars import parse_numref


def parse_end(ctx, raw_end, mode, where, allow_infinite=False):
    """終了条件を解析して (type, count, ms) を返す。

    allow_infinite=True の場合、random で end 未指定を END_NONE
    (イベント終了まで無限再生)として許可する。
    """
    if raw_end is None:
        if mode in (MODE_RANDOM, MODE_RANDOM_BAG):
            if allow_infinite:
                return END_NONE, 1, 0, None, None
            raise ValueError(tr('{0}: random には end (duration) の指定が必要です').format(where))
        return END_ONCE, 1, 0, None, None
    etype = raw_end.get("type")
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
    if etype not in (END_ONCE, END_REPEAT, END_DURATION, END_NONE,
                     END_PLAYS):
        raise ValueError(tr("{0}: end type '{1}' は不正です").format(where, etype))
    if etype == END_NONE and not allow_infinite:
        raise ValueError(
            tr("{0}: end 'none' はイベントの end が channel 指定の場合のみ使えます").format(where))
    if etype == END_REPEAT and c_ref is None and count < 1:
        raise ValueError(tr('{0}: repeat の count は1以上にしてください').format(where))
    if etype == END_PLAYS and c_ref is None and count < 1:
        raise ValueError(tr('{0}: plays の count は1以上にしてください').format(where))
    if etype == END_DURATION and dur_refs is None and ms <= 0:
        raise ValueError(tr('{0}: duration には seconds を指定してください').format(where))
    if mode == MODE_RANDOM \
            and etype not in (END_DURATION, END_NONE, END_PLAYS):
        raise ValueError(
            tr('{0}: random の end は duration / plays (または none) のみ対応です').format(where))
    # =98: random_bag は repeat(N周=袋をN回使い切ったら終了)も可
    if mode == MODE_RANDOM_BAG \
            and etype not in (END_DURATION, END_NONE, END_PLAYS,
                              END_REPEAT):
        raise ValueError(
            tr('{0}: random_bag の end は duration / plays / repeat (または none) のみ対応です').format(where))
    return (etype, count, ms,
            c_ref if etype in (END_REPEAT, END_PLAYS) else None,
            dur_refs if etype == END_DURATION else None)


def parse_event_duration_range(ctx, raw_end, where):
    """イベント終了 duration の範囲抽選(min_seconds〜max_seconds)を解析する。

    min/max はそれぞれ 0以上の定数 または {"var":名}(数値変数)。
    入場ごとに [min, max] の一様乱数(秒)を抽選し、その秒数を超えたら終了。
    戻り値は ScenarioEvent の range 用 kwargs。
    """
    lo_v, lo_ref = parse_numref(ctx, raw_end.get("min_seconds", 0), where)
    hi_raw = raw_end.get("max_seconds", raw_end.get("min_seconds", 0))
    hi_v, hi_ref = parse_numref(ctx, hi_raw, where)
    lo_ms = (lo_v or 0) * 1000 if lo_ref is None else 0.0
    hi_ms = (hi_v or 0) * 1000 if hi_ref is None else 0.0
    # 定数のときは 0以上・max>0 を検証(変数は入場時に解決・クランプ)
    if lo_ref is None and lo_ms < 0:
        raise ValueError(
            tr('{0}: duration の min 秒は0以上にしてください').format(where))
    if lo_ref is None and hi_ref is None:
        if hi_ms < lo_ms:
            lo_ms, hi_ms = hi_ms, lo_ms
        if hi_ms <= 0:
            raise ValueError(
                tr('{0}: duration の秒数(min/max)は正の数にしてください').format(where))
    elif hi_ref is None and hi_ms < 0:
        raise ValueError(
            tr('{0}: duration の max 秒は0以上にしてください').format(where))
    return dict(end_duration_range=True,
                end_duration_min_ms=lo_ms, end_duration_max_ms=hi_ms,
                end_duration_min_ref=lo_ref, end_duration_max_ref=hi_ref)


def parse_interval(ctx, raw, where):
    """interval指定を (min_ms, max_ms) で返す。

    指定形式:
      "interval": {"min": 0.5, "max": 1.0}   秒単位
      "interval": 0.5                        単一値(min=max=0.5)
    未指定は (0, 0) = 待機なし。
    """
    if raw is None:
        return 0, 0
    if isinstance(raw, (int, float)):
        ms = int(round(float(raw) * 1000 / 100)) * 100
        ms = max(0, ms)
        return ms, ms
    if isinstance(raw, dict):
        try:
            lo = float(raw.get("min", 0))
            hi = float(raw.get("max", lo))
        except (TypeError, ValueError):
            raise ValueError(tr('{0}: interval の min/max は数値で指定してください').format(where))
        lo_ms = max(0, int(round(lo * 1000 / 100)) * 100)  # 0.1秒に丸め
        hi_ms = max(0, int(round(hi * 1000 / 100)) * 100)
        if hi_ms < lo_ms:
            lo_ms, hi_ms = hi_ms, lo_ms
        return lo_ms, hi_ms
    raise ValueError(tr('{0}: interval の指定が不正です').format(where))


def parse_channel(ctx, ch_id, raw, where, allow_infinite=False,
                  default_infinite=False) -> Channel:
    mode = raw.get("mode", MODE_SEQUENTIAL)
    if mode not in (MODE_SEQUENTIAL, MODE_RANDOM, MODE_RANDOM_BAG):
        raise ValueError(tr("{0}: mode '{1}' は不正です").format(where, mode))
    items = [parse_item(ctx, r, where) for r in raw.get("items", [])]
    if not items:
        raise ValueError(tr('{0}: items が空です').format(where))
    # チャンネルは「音声ch」「動画ch」「スクリプト専用ch」のどれか1つ
    # (混在禁止=ユーザー決定。動画chは =52 フェーズ3-Bで追加)
    kinds = {("video" if it.video else "audio" if it.audio else "script")
             for it in items}
    if len(kinds) > 1:
        names = {"audio": tr('音声'), "video": tr('動画'),
                 "script": tr('スクリプトのみ')}
        raise ValueError(
            tr('{0}: {1} のアイテムは同じチャンネルに混在できません(チャンネル単位でどれか1種類にしてください)').format(
                where, tr('と').join(names[k] for k in sorted(kinds))))
    raw_end = raw.get("end")
    ch_range = None
    if raw_end is None and default_infinite:
        # ステート内チャンネルの既定: 移行/イベント終了まで無限に再生
        etype, count, ms, c_ref, d_refs = END_NONE, 1, 0, None, None
    elif (isinstance(raw_end, dict)
            and raw_end.get("type") == END_DURATION
            and ("min_seconds" in raw_end or "max_seconds" in raw_end)):
        # =99: duration の範囲抽選(min_seconds/max_seconds)。書式・
        # 検証はイベント終了 duration の範囲と同じ。全モードで使える
        # (durationが使えるモードなら範囲も使える)
        ch_range = parse_event_duration_range(ctx,
            raw_end, tr("{0} end").format(where))
        etype, count, ms, c_ref, d_refs = END_DURATION, 1, 0, None, None
    else:
        etype, count, ms, c_ref, d_refs = parse_end(ctx,
            raw_end, mode, where, allow_infinite)
    pan = parse_pan(ctx, raw.get("pan"), where) or Pan(*DEFAULT_PAN[ch_id])
    imin, imax = parse_interval(ctx, raw.get("interval"), where)
    return Channel(
        channel_id=ch_id, mode=mode, items=items,
        end_type=etype, end_count=count, end_duration_ms=ms,
        end_count_ref=c_ref, end_duration_refs=d_refs, pan=pan,
        interval_min_ms=imin, interval_max_ms=imax,
        **(ch_range or {}),
    )


def parse_device(ctx, raw, channels, where) -> dict[str, str]:
    """device指定を {デバイス種別: チャンネルID} のマップへ解析する。

    指定形式:
      "device": "C"                          全種別をCが担当(後方互換)
      "device": {"linear":"L","rotate":"R"}  種別ごとに担当を指定
    未指定はCが担当(Cが無ければ最初のチャンネル)。
    """
    if raw is None:
        default_ch = CH_CENTER if CH_CENTER in channels else next(iter(channels))
        return {t: default_ch for t in VALID_TRACK_TYPES}
    if isinstance(raw, str):
        ch = raw if raw in channels else next(iter(channels))
        return {t: ch for t in VALID_TRACK_TYPES}
    if isinstance(raw, dict):
        dmap = {}
        for ttype, ch in raw.items():
            ttype = normalize_track_type(ttype)
            if ttype not in VALID_TRACK_TYPES:
                raise ValueError(
                    tr("{0}: device の種別 '{1}' は不正です(有効: {2})").format(where, ttype, ', '.join(VALID_TRACK_TYPES)))
            if ch not in channels:
                raise ValueError(
                    tr("{0}: device の '{1}' に指定されたチャンネル '{2}' が存在しません").format(where, ttype, ch))
            dmap[ttype] = ch
        return dmap
    raise ValueError(tr('{0}: device の指定が不正です').format(where))


def check_script_channels(ctx, channels: dict, device_map: dict, where,
                          has_video: bool = False):
    """スクリプト専用チャンネルの制約を検証する。

    **=63でコンテンツ必須の制約は撤廃**した(ユーザー要望: 音声・動画の
    素材が無い状況もありうるため)。スクリプト専用チャンネルだけの
    イベント/ステートを作れる。残る制約は「スクリプト専用chはデバイス
    担当が無いと何も起きない」ことだけで、どのデバイス種別の担当にも
    なっていない場合はエラーにする。

    =130(警告): チャンネルが担当するデバイス種別のトラックを持つ
    アイテムが1つも無い場合、そのチャンネルはデバイスを一切動かさない
    (再生時は担当外トラックが無音でスキップされる=46)。トラック種別の
    指定漏れの典型(自動紐づけの既定が rotate_ufo のため、a10担当chに
    rotate_ufo型トラックが入る事故が実例)なので警告する。担当外
    トラックの同居自体は正当(コピー由来の予備等)なのでエラーにしない。
    """
    if not channels:
        return
    script_chs = [cid for cid, ch in channels.items()
                  if not ch.is_content]
    assigned = set(device_map.values())
    for cid in script_chs:
        if cid not in assigned:
            raise ValueError(
                tr("{0}: スクリプト専用チャンネル '{1}' がどのデバイス種別の担当にもなっていません(device で担当を指定してください)").format(where, cid))
    # =130 警告: 担当種別のトラックが皆無のチャンネル
    for ttype, cid in device_map.items():
        ch = channels.get(cid)
        if ch is None:
            continue
        if any(t.type == ttype for item in ch.items
               for t in item.tracks):
            continue
        ctx.load_warnings.append(
            tr("{0}: チャンネル{1}は {2} の担当ですが、その種別のトラックを持つアイテムが1つもありません(このチャンネルはデバイスを動かしません)").format(
                where, cid, ttype))


def has_content_channels(ctx, channels: dict) -> bool:
    """音声または動画のチャンネルが1つでもあるか(=63)。

    スクリプト専用chだけのイベントは「N回再生(plays)」を満たせない
    (plays は音声/動画の本数だけを数えるため)。その検証に使う。
    """
    return any(ch.is_content for ch in (channels or {}).values())


def check_video_channels(ctx, channels: dict, where) -> str:
    """動画チャンネルを1つに制限し、そのIDを返す(無ければ "")。

    合意事項7「動画は同時に最大1本」をチャンネル形式でも維持する
    (ユーザー回答: 2ch以上に動画を置いたら読み込みエラー)。
    """
    vids = [cid for cid, ch in channels.items() if ch.has_video]
    if len(vids) > 1:
        raise ValueError(
            tr('{0}: 動画を置けるチャンネルは1つだけです(動画は同時に1本。{1} に指定されています)').format(
                where, ', '.join(sorted(vids))))
    return vids[0] if vids else ""


def parse_seek_channel(ctx, raw_seek, channels: dict, where: str):
    """seek_channel(シークバー追従チャンネル指定)の読取と検証。

    None=無指定(自動)。指定時は定義済みチャンネルのみ許可する。
    """
    if raw_seek is None:
        return None
    if not isinstance(raw_seek, str) or raw_seek not in channels:
        raise ValueError(
            tr("{0}: seek_channel は定義済みのチャンネル(L/C/R)を"
               "指定してください").format(where))
    # =63: スクリプト専用チャンネルも追従対象に選べる(ユーザー決定)。
    # 音声が無い場合は経過時間=スクリプトの進行として表示・シークする。
    return raw_seek
