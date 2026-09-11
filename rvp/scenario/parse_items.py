"""シナリオ読み込み: 素材パスの解決・区間・パン・トラック・アイテム(音声/動画)・BGM のパース。"""
from __future__ import annotations

import os
from .model import BgmItem, BgmSpec, DeviceTrack, EventItem, Pan
from .constants import (MODE_RANDOM, MODE_SEQUENTIAL, TRACK_LINEAR,
    VALID_TRACK_TYPES)
from .tracks import auto_bind_tracks, normalize_track_type
from ..i18n import tr

from .parse_vars import parse_numref, parse_ops


def resolve(ctx, p: str | None) -> str | None:
    if not p:
        return None
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ctx.base_dir, p))


def parse_range(ctx, raw_range, where, label="range"):
    """区間指定(start / end)を秒で解析する(=51の動画→=59で汎用化)。

    戻り値: (start秒, end秒 or None)。省略・null は「指定なし」。
    bool は数値として受理しない(True が 1 と解釈される事故の防止)。
    0.1秒未満の細かい指定も可(小数で書ける)。
    """
    if raw_range is None:
        return 0.0, None
    if not isinstance(raw_range, dict):
        raise ValueError(
            tr('{0}: {1} はオブジェクトで指定してください').format(where, label))

    def num(key, default):
        v = raw_range.get(key)
        if v is None:
            return default
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(
                tr('{0}: {1} の {2} は秒数(数値)で指定してください').format(
                    where, label, key))
        return float(v)

    start_s = num("start", 0.0)
    end_s = num("end", None)
    if start_s < 0:
        raise ValueError(
            tr('{0}: {1} の start は0以上にしてください').format(where, label))
    if end_s is not None and end_s <= start_s:
        raise ValueError(
            tr('{0}: {1} の end は start より後にしてください').format(
                where, label))
    return start_s, end_s


def parse_pan(ctx, raw, where: str) -> Pan | None:
    """pan指定を解析する。{"left":1.0,"right":0.2} 形式。未指定はNone。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(tr('{0}: pan はオブジェクトで指定してください').format(where))
    try:
        left = float(raw["left"])
        right = float(raw["right"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(tr('{0}: pan には left と right を数値で指定してください').format(where))
    left = max(0.0, min(1.0, left))
    right = max(0.0, min(1.0, right))
    return Pan(left=left, right=right)


def parse_tracks(ctx, raw: dict, audio_abs: str, where: str) -> list[DeviceTrack]:
    """item辞書から DeviceTrack のリストを構築する。

    指定方法(優先順):
      1. "tracks": [{"type":"linear","funscript":"..."}, ...]  複数デバイス対応
      2. "funscript": "..." / null                          linear単一(後方互換)
      3. 未指定    命名ルールで自動紐づけ(auto_bind_tracks参照。
                   linear=wav名を含む同名系 / rotate=+"ufo" /
                   rotate_a10cyclonesa=+"a10" / vibration=+"vib")
    """
    tracks: list[DeviceTrack] = []

    if "tracks" in raw:
        for i, t in enumerate(raw["tracks"]):
            if not isinstance(t, dict):
                raise ValueError(tr('{0}: tracks[{1}] はオブジェクトで指定してください').format(where, i))
            ttype = normalize_track_type(t.get("type", TRACK_LINEAR))
            if ttype not in VALID_TRACK_TYPES:
                raise ValueError(
                    tr("{0}: tracks[{1}] の type '{2}' は不正です(有効: {3})").format(where, i, ttype, ', '.join(VALID_TRACK_TYPES))
                )
            fs = resolve(ctx, t.get("funscript"))
            if not fs:
                raise ValueError(tr('{0}: tracks[{1}] に funscript がありません').format(where, i))
            # =59: トラック個別の区間(省略時はアイテムの区間に連動)
            t_start, t_end = parse_range(ctx,
                t.get("range"), tr("{0} tracks[{1}]").format(where, i))
            tracks.append(DeviceTrack(type=ttype, funscript=fs,
                                      start_s=t_start, end_s=t_end))
        return tracks

    if "funscript" in raw:
        fs = resolve(ctx, raw["funscript"])  # null なら無効化(トラックなし)
        if fs:
            tracks.append(DeviceTrack(type=TRACK_LINEAR, funscript=fs))
        return tracks

    # 未指定 → 命名ルールで自動紐づけ(linearは同名、他種別はタグ)
    for ttype, fs in auto_bind_tracks(audio_abs):
        tracks.append(DeviceTrack(type=ttype, funscript=fs))
    return tracks


def parse_video_item(ctx, raw: dict, where: str) -> EventItem:
    """動画アイテム(フェーズ3-B=52)を解析する。

    指定形式:
      {"video": "scene1.mp4"}                       … 自動紐づけ
      {"video": {"file": "...", "start": 60, "end": 90},
       "tracks": [...] / "funscript": ...}          … 明示指定

    トラックの指定規則は音声アイテムと同じ(tracks / funscript /
    未指定=命名ルールで自動紐づけ)。動画クロックに完全同期する。
    start/end は区間指定(=51)。区間の先頭が0秒として扱われるため、
    funscript は区間ごとに0秒起点で作れる。
    """
    if raw.get("audio"):
        raise ValueError(
            tr('{0}: 同じアイテムに audio と video は指定できません').format(where))
    rv = raw.get("video")
    start_s, end_s = 0.0, None
    if isinstance(rv, str):
        if not rv:
            raise ValueError(
                tr('{0}: video のファイルを指定してください').format(where))
        vfile = resolve(ctx, rv)
    elif isinstance(rv, dict):
        f = rv.get("file")
        if not f or not isinstance(f, str):
            raise ValueError(
                tr('{0}: video の file を指定してください').format(where))
        vfile = resolve(ctx, f)
        # 旧形式(=51〜=58): video 辞書の中に start/end を書いていた。
        # =59でアイテム共通の "range" へ移したので、range が無いときの
        # 後方互換として読む(編集画面で保存し直すと range になる)。
        start_s, end_s = parse_range(ctx,
            {k: rv.get(k) for k in ("start", "end")}, where, "video")
    else:
        raise ValueError(tr('{0}: video の指定が不正です').format(where))
    if raw.get("range") is not None:
        start_s, end_s = parse_range(ctx, raw.get("range"), where)
    tracks = parse_tracks(ctx, raw, vfile, where)
    # =74: 重みは定数 or {"var":..}(0/負も受理=実行時に出さない)
    w_const, w_ref = parse_numref(ctx,
        raw.get("weight", 1.0),
        tr("{0} アイテムの weight").format(where))
    return EventItem(audio="", video=vfile, tracks=tracks,
                     weight=(1.0 if w_ref is not None else w_const),
                     weight_var=(w_ref.var if w_ref is not None else None),
                     on_play=parse_ops(ctx, raw.get("on_play"),
                                       tr("{0} on_play").format(where)),
                     on_complete=parse_ops(ctx,
                         raw.get("on_complete"),
                         tr("{0} on_complete").format(where)),
                     start_s=start_s, end_s=end_s)


def parse_item(ctx, raw, where: str) -> EventItem:
    if isinstance(raw, dict) and raw.get("video") is not None:
        return parse_video_item(ctx, raw, where)
    if isinstance(raw, str):
        audio = resolve(ctx, raw)
        tracks = [DeviceTrack(t, fs)
                  for t, fs in auto_bind_tracks(audio)]
        return EventItem(audio=audio, tracks=tracks)
    if isinstance(raw, dict):
        # =59: 音声・スクリプトのみアイテムにも区間指定を導入
        start_s, end_s = parse_range(ctx, raw.get("range"), where)
        audio = resolve(ctx, raw.get("audio"))
        if not audio:
            # スクリプトのみアイテム: audio 省略は tracks/funscript の
            # 明示指定がある場合のみ許可(作りかけの検出は維持)。
            # 自動紐づけは音声ファイル名が基準のため使えない。
            if "tracks" not in raw and "funscript" not in raw:
                raise ValueError(tr('{0}: audio がありません').format(where))
            audio = ""
            tracks = parse_tracks(ctx, raw, "", where)
            if not tracks:
                raise ValueError(
                    tr('{0}: スクリプトのみのアイテムには funscript/CSV のトラックが1つ以上必要です').format(where))
        else:
            tracks = parse_tracks(ctx, raw, audio, where)
        # =74: 重みは定数 or {"var":..}(0/負も受理=実行時に出さない)
        w_const, w_ref = parse_numref(ctx,
            raw.get("weight", 1.0),
            tr("{0} アイテムの weight").format(where))
        pan = parse_pan(ctx, raw.get("pan"), where)
        on_play = parse_ops(ctx, raw.get("on_play"),
                            tr("{0} on_play").format(where))
        on_complete = parse_ops(ctx, raw.get("on_complete"),
                                tr("{0} on_complete").format(where))
        return EventItem(audio=audio, tracks=tracks,
                         weight=(1.0 if w_ref is not None else w_const),
                         weight_var=(w_ref.var if w_ref is not None
                                     else None),
                         pan=pan, on_play=on_play,
                         on_complete=on_complete,
                         start_s=start_s, end_s=end_s)
    raise ValueError(tr('{0}: items の要素が不正です').format(where))


def parse_bgm(ctx, raw, where: str) -> BgmSpec | None:
    """ノードの "bgm" キーを解析する(=256)。

    省略/None = 「前のBGMを引き継ぐ」(None)。
    {"off": true} = BGM停止。
    {"items": [...], "order": ..., "pan": ...} = BGM指定。
    items は "path" か {"audio": "path", "pan": {...}}。
    指定なのにアイテム0件はエラー(Q12=ユーザー確定)。
    """
    if raw is None:
        return None
    w = tr("{0} bgm").format(where)
    if not isinstance(raw, dict):
        raise ValueError(
            tr("{0}: オブジェクトで指定してください").format(w))
    if "off" in raw:
        if raw["off"] is not True:
            raise ValueError(
                tr('{0}: off は true のみ指定できます'
                   '(引き継ぐ場合はキーごと省略します)').format(w))
        return BgmSpec(mode="off")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise ValueError(
            tr('{0}: items(1件以上)か "off": true を'
               '指定してください').format(w))
    items = []
    for i, it in enumerate(items_raw):
        wi = tr("{0} items[{1}]").format(w, i)
        pan = None
        if isinstance(it, str):
            audio = resolve(ctx, it)
        elif isinstance(it, dict):
            audio = resolve(ctx, it.get("audio"))
            pan = parse_pan(ctx, it.get("pan"), wi)
        else:
            raise ValueError(
                tr("{0}: 要素が不正です").format(wi))
        if not audio:
            raise ValueError(
                tr("{0}: audio がありません").format(wi))
        items.append(BgmItem(audio=audio, pan=pan))
    order = raw.get("order", MODE_SEQUENTIAL)
    if order not in (MODE_SEQUENTIAL, MODE_RANDOM):
        raise ValueError(
            tr("{0}: order は sequential / random の"
               "いずれかにしてください").format(w))
    pan = parse_pan(ctx, raw.get("pan"), w)
    return BgmSpec(mode="set", items=tuple(items), order=order,
                   pan=pan)
