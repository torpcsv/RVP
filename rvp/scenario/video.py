"""動画ノードの補助(動画チャンネルの検出・v1 形式からの移行)。"""
from __future__ import annotations

from ..i18n import tr

from .constants import (CH_CENTER, CH_LEFT, CH_RIGHT, END_CHANNEL, END_NONE,
    END_ONCE, MODE_SEQUENTIAL)


def raw_video_channel(raw: dict) -> str:
    """raw の channels から動画チャンネルのIDを返す(無ければ "")。"""
    for cid, ch in (raw.get("channels") or {}).items():
        if not isinstance(ch, dict):
            continue
        for it in ch.get("items") or []:
            if isinstance(it, dict) and it.get("video") is not None:
                return cid
    return ""


def free_video_channel(raw: dict) -> str:
    """動画チャンネルに使える空きチャンネルID(C→L→R優先)。無ければ ""。"""
    used = set((raw.get("channels") or {}).keys())
    for cid in (CH_CENTER, CH_LEFT, CH_RIGHT):
        if cid not in used:
            return cid
    return ""


def migrate_video_node(raw: dict, where: str = "", is_state: bool = False) -> dict:
    """イベント/ステート直下の "video" を動画チャンネルへ移行した dict を返す。

    - 動画は空きチャンネル(C→L→R優先)へ「動画アイテム1つのチャンネル」として入る。
    - ループ☑は終了条件へ一本化する(ユーザー決定):
        loop なし → チャンネル終了条件 "once"(1周で終了。動画が終わったら終わる)
        loop あり → "none"(無限。イベント/ステート側の終了条件で抜ける)
    - 通常イベントで end 未指定 かつ 非ループ だった場合は、旧挙動
      「動画が終わったらイベント終了」を保つため end を
      {"type":"channel","channel":動画ch} にする。
    - 空きチャンネルが無い(L/C/R すべて使用中)場合はエラー。ユーザー決定に
      より動画は L/C/R のどれか1つを占有するため、1ch空ける必要がある。
    """
    rv = raw.get("video")
    if rv is None:
        return raw
    cid = free_video_channel(raw)
    if not cid:
        raise ValueError(
            tr('{0}: 動画を置くチャンネルの空きがありません(動画は L/C/R のどれか1つを使います。どれか1チャンネルを空けてください)').format(where))

    loop = False
    item: dict = {}
    if isinstance(rv, str):
        item["video"] = rv
    elif isinstance(rv, dict):
        loop = bool(rv.get("loop", False))
        vid: dict = {"file": rv.get("file")}
        for key in ("start", "end"):
            if rv.get(key) is not None:
                vid[key] = rv[key]
        item["video"] = vid
        for key in ("tracks", "funscript"):
            if key in rv:
                item[key] = rv[key]
        # 旧形式で保持していた未知キーは動画アイテム側へ引き継ぐ
        for key, val in rv.items():
            if key not in ("file", "loop", "start", "end", "tracks", "funscript"):
                item[key] = val
    else:
        raise ValueError(tr('{0}: video の指定が不正です').format(where))

    out = dict(raw)
    out.pop("video", None)
    channels = dict(out.get("channels") or {})
    channels[cid] = {"mode": MODE_SEQUENTIAL, "items": [item],
                     "end": {"type": END_NONE if loop else END_ONCE}}
    out["channels"] = channels
    if not is_state and out.get("end") is None and not loop:
        # 旧挙動「動画が終わったらイベント終了」を明示的な終了条件で再現する
        out["end"] = {"type": END_CHANNEL, "channel": cid}
    return out
