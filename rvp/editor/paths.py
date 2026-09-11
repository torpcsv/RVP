"""シナリオ編集: ファイル/パスのヘルパー(素材の相対化・コピー・=33/=41)。"""
from __future__ import annotations

import filecmp
import os



def _same_file_content(a: str, b: str) -> bool:
    """2つのパスが同一ファイル(同一実体 or 内容が完全一致)ならTrue。

    コピー済み素材の再相対化で「同名だが中身は同じ」を衝突にしないための判定。
    比較に失敗した場合はFalse(=衝突側に倒す)。
    """
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        pass
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _safe_relpath(path: str, base: str) -> str:
    """base からの相対パスを返す。別ドライブ等で不可能なら絶対パスを返す。

    Windows で保存フォルダと別ドライブのファイルを選んでも os.path.relpath が
    ValueError で落ちないようにするための安全版。
    """
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return os.path.abspath(path)


def _rebase_scenario_path(stored: str, old_base: str, new_base: str) -> str:
    """保存済みパス(old_base基準の相対 or 絶対)を new_base 基準へ付け替える。

    - まず絶対パスへ解決(相対は old_base 起点)。
    - new_base 配下(サブフォルダ含む)なら new_base 基準の相対パスにする(可搬)。
    - new_base の外・別ドライブなら絶対パスのまま残す(移動しても解決できる)。
    """
    if not stored:
        return stored
    abs_p = stored if os.path.isabs(stored) \
        else os.path.normpath(os.path.join(old_base, stored))
    try:
        rel = os.path.relpath(abs_p, new_base)
    except ValueError:
        return abs_p    # 別ドライブ → 絶対で保存
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return abs_p    # 保存先の外 → 絶対で保存(移動時の破綻を避ける)
    return rel


def _format_bytes(n: int) -> str:
    """バイト数を人間向けの短い表記にする(=50。素材コピーの容量表示用)。"""
    n = max(0, int(n))
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def _map_item_paths(data: dict, fn) -> None:
    """シナリオdict内の全アイテムの音声/funscript/CSV/動画パスへ fn を適用する。

    fn(kind, value) -> 新しい値。kind は "audio"(音声) / "fs"(funscript/CSV)
    / "video"(動画=50)。data を **in-place** で書き換える(呼び出し側が
    必要ならdeepcopyする)。走査対象は _rebased_data_for_save と同一
    (通常イベント/ステートの channels の items と、直下の video・bgm=256)。
    """
    def do_video(container):
        """イベント/ステート直下の video(=48)を走査する(=50で追加)。

        文字列形式("video": "a.mp4")と辞書形式({"file","tracks"/"funscript"})
        の両方に対応する。これが無いと =33 の外部素材警告/素材コピー相対化と
        =41 の取り込み時パス絶対化が動画に効かない(=50で修正)。
        """
        if not isinstance(container, dict):
            return
        v = container.get("video")
        if isinstance(v, str):
            container["video"] = fn("video", v)
        elif isinstance(v, dict):
            if isinstance(v.get("file"), str):
                v["file"] = fn("video", v["file"])
            if isinstance(v.get("funscript"), str):
                v["funscript"] = fn("fs", v["funscript"])
            tracks = v.get("tracks")
            if isinstance(tracks, list):
                for t in tracks:
                    if isinstance(t, dict) and \
                            isinstance(t.get("funscript"), str):
                        t["funscript"] = fn("fs", t["funscript"])

    def do_channels(channels):
        if not isinstance(channels, dict):
            return
        for ch in channels.values():
            if not isinstance(ch, dict):
                continue
            items = ch.get("items")
            if not isinstance(items, list):
                continue
            for i, it in enumerate(items):
                if isinstance(it, str):          # 文字列item=音声パス
                    items[i] = fn("audio", it)
                elif isinstance(it, dict):
                    if isinstance(it.get("audio"), str):
                        it["audio"] = fn("audio", it["audio"])
                    # 動画アイテム(=52): 文字列 or {"file","start","end"}
                    rv = it.get("video")
                    if isinstance(rv, str):
                        it["video"] = fn("video", rv)
                    elif isinstance(rv, dict) and isinstance(rv.get("file"), str):
                        rv["file"] = fn("video", rv["file"])
                    if isinstance(it.get("funscript"), str):
                        it["funscript"] = fn("fs", it["funscript"])
                    tracks = it.get("tracks")
                    if isinstance(tracks, list):
                        for t in tracks:
                            if isinstance(t, dict) and \
                                    isinstance(t.get("funscript"), str):
                                t["funscript"] = fn("fs", t["funscript"])

    def do_bgm(container):
        """ノード直下の bgm(=256)を走査する。

        items は文字列("a.wav")と辞書({"audio","pan"})の両方に対応する。
        これが無いと外部素材警告/素材コピー相対化/取り込み時の絶対化が
        BGMに効かない。
        """
        if not isinstance(container, dict):
            return
        bgm = container.get("bgm")
        if not isinstance(bgm, dict):
            return
        items = bgm.get("items")
        if not isinstance(items, list):
            return
        for i, it in enumerate(items):
            if isinstance(it, str):
                items[i] = fn("audio", it)
            elif isinstance(it, dict) and isinstance(it.get("audio"), str):
                it["audio"] = fn("audio", it["audio"])

    def do_background(root):
        """トップレベルの background(=262)を走査する。

        文字列("bg.png")と辞書({"file","dim"})の両方に対応する。これが
        無いと外部素材警告/素材コピー相対化/保存時のパス付け替えが背景
        イラストに効かない。kind は "image"。
        """
        bg = root.get("background")
        if isinstance(bg, str):
            root["background"] = fn("image", bg)
        elif isinstance(bg, dict) and isinstance(bg.get("file"), str):
            bg["file"] = fn("image", bg["file"])

    do_background(data)
    events = data.get("events")
    if isinstance(events, dict):
        for ev in events.values():
            if not isinstance(ev, dict):
                continue
            if isinstance(ev.get("states"), dict):
                for st in ev["states"].values():
                    if isinstance(st, dict):
                        do_channels(st.get("channels"))
                        do_video(st)
                        do_bgm(st)
            do_channels(ev.get("channels"))
            do_video(ev)
            do_bgm(ev)
