"""シナリオ編集ウィンドウ。

対応範囲:
- チャンネル L/C/R の有効/無効、mode(順番/ランダム)、終了条件(1周/N周/N分/無限)
- イベントの device 担当(種別ごと)、次のイベント
- 音声の追加/削除、音声ごとのデバイス種別、funscript(同名自動/ファイル指定)
- イベントの追加/削除、タイトル/開始イベントの変更
- **ステート編集**: イベントパネル内にステートマシン図を表示し、○クリックで
  ステートを選択・編集する。ステートの追加/削除、開始ステート、移行条件
  (経過時間/回数、min-max抽選)、移行先(固定/複数チェックで均等抽選)、
  イベント終了条件(duration/plays/transitions)に対応。
  通常イベント⇔ステート形式の相互変換ボタンあり。
- **変数(vars)編集**: ツールバー「変数…」ボタンで宣言・監視(watch)の管理
  ダイアログを開く。変数が1つも宣言されていない間は、イベントパネル側の
  変数関連UI(操作ボタン・変数分岐・数値入力・変数指定トグル)は一切表示しない
  (密集化対策のゲーティング)。宣言があると:
  - 変数操作: イベント/ステートon_start・アイテムon_play/on_complete・
    選択肢ops・choice on_timeoutを「変数操作(n)」ボタン+ポップアップで編集
  - 遷移方法メニューに「変数分岐」(cond)と「数値入力」(input)が追加され編集可能
  - 終了条件・transitionの数値欄に「x」トグルが付き、変数指定へ切替できる
    (変数指定のdurationは分/秒の単位を選択)

編集対象外のフィールド(weight/pan/interval等)は保存時にそのまま保持される。
UIで表せない変数指定の組み合わせ(duration分+秒の併記等)は「変数指定」の
読み取り専用表示となり、保存時にそのまま保持される。
"""

import asyncio
import json
import os
import sys
import copy
import filecmp
import shutil
import threading
import time
import tkinter as tk
import warnings
import tkinter.font as tkfont
from tkinter import filedialog

import customtkinter as ctk

from .i18n import tr, load_config, save_config
from . import __version__            # =292 公開バージョン
from . import appfont
from . import winstate
from .winstate import WindowMemory
from .scenario import (Scenario, auto_bind_tracks, DEFAULT_PAN,
                       normalize_track_type, AUTO_FS_TAGS, CSV_TRACK_TYPES,
                       migrate_video_node)
# =164 アイテムレビュー: スクリプトの読み込み(再生側と同じ入口を使う)
from .funscript import Funscript
from .rotate_source import load_rotate_source

# OSからのドラッグ&ドロップ(=44)。tkinterdnd2(MIT・内包tkdndはBSD系)が
# 未導入の環境では D&D 機能だけ静かに無効になり、他は従来どおり動く。
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = "DND_Files"


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

ACCENT = "#7c6cf0"
ACCENT_HOVER = "#6a5ae0"
# =114: ボタンの面(fg_color)は従来の明るい紫のまま、**文字として使う紫**は
# ライトの薄い背景で沈むのでライトだけ濃くする(main.py の ACCENT_TEXT と同値)。
ACCENT_TEXT = ("#5245c9", ACCENT)
MUTED = "gray62"
# =112: ライトモードの文字が薄くて読みづらい、というユーザー指摘への対応。
# **枠線用の MUTED はそのまま**(黒枠にすると輪郭がきつくなる)にして、
# **文字色だけ**をライト時に濃くする。ダーク時は従来どおり gray62。
TEXT_MUTED = ("gray10", "gray62")
# 見出し(■ イベント / ■ ステート など)も同様に濃くする
TEXT_HEAD = ("gray10", "gray75")
# 図の配色・背景は共通描画モジュール(scenario_map)を単一の定義元とする
from .scenario_map import (OK_COLOR, CANVAS_BG, CANVAS_BG_LIGHT, NODE_FILL,
                           NODE_STATES, EDGE_COLOR, STATE_EDGE_COLOR)
from . import scenario_map as _smap
# =124: 選択ノードは青塗りをやめ緑コーナー枠(scenario_map)へ移行した。
# 定数は互換のため残置(未使用)。
NODE_SELECTED = ACCENT

# コンボボックス(オプションメニュー)の文字色: ライト時は黒でくっきり、
# ダーク時はCTk既定の明色を維持する。
COMBO_TEXT = ("black", "#DCE4EE")
# =169(不具合修正): 無効化(state="disabled")したときの文字色。
# CTk既定は ("gray74", "gray60") だが、RVPのコンボは面が ("gray75", "gray28")
# なので**ライト側は gray74 の文字が gray75 の面に埋もれて読めなくなる**
# (コントラスト1.02:1)。ユーザー報告=ステート形式イベントの
# 「ステートのイベント終了に委譲」がダーク以外で消えて見える。
# ライト側だけ濃くして読めるようにし(4.9:1)、ダーク側は既定のまま
# (gray28の面に対して十分なコントラストがあるため)。
COMBO_TEXT_DISABLED = ("gray28", "gray60")

# =134: パステルカラーテーマの色差し替え(ACCENT系)。編集画面は遅延import
# されるため、ここでの register が「テーマ適用後のimport」も拾う。
from . import apptheme
apptheme.register(globals())

# =124 ノード着色パレット: 縦=色相7段(赤・橙・黄・緑・青・藍・紫)×
# 横=PCCSトーン近似5列(左からP・lt・V・dp・dk)。最下段は彩度0%(白〜黒)。
# HLSからの機械生成値(色相0/28/52/135/205/230/282°)。
NODE_PALETTE = (
    ("#efc8c8", "#e88787", "#ff0000", "#a00d0d", "#581313"),   # 赤
    ("#efdac8", "#e8b487", "#ff7700", "#a0520d", "#583313"),   # 橙
    ("#efeac8", "#e8db87", "#ffdd00", "#a08d0d", "#584f13"),   # 黄
    ("#c8efd1", "#87e89f", "#00ff40", "#0da032", "#135824"),   # 緑
    ("#c8dfef", "#87c0e8", "#0095ff", "#0d63a0", "#133b58"),   # 青
    ("#c8ceef", "#8797e8", "#002bff", "#0d26a0", "#131e58"),   # 藍
    ("#e3c8ef", "#cb87e8", "#b300ff", "#740da0", "#431358"),   # 紫
    ("#ffffff", "#cccccc", "#999999", "#666666", "#333333"),   # 彩度0%
)


def _canvas_bg():
    """イベント図/ステート図キャンバスの背景色を現在のテーマから返す。"""
    return _smap.canvas_bg()


class CTkOptionMenu(ctk.CTkOptionMenu):
    """ライトモードで文字色を黒にしてくっきり見せるオプションメニュー。

    __class__.__name__ は基底と同じ "CTkOptionMenu" のままなので、検証エラー
    着色(クラス名で分岐)や isinstance 判定はそのまま機能する。

    =169: 無効化したときの文字色も既定を差し替える(CTk既定の gray74 は
    ライトの面 gray75 と同化して読めないため)。
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("text_color", COMBO_TEXT)
        kwargs.setdefault("text_color_disabled", COMBO_TEXT_DISABLED)
        super().__init__(*args, **kwargs)

# イベント/ステート/チャンネルを囲う丸角の灰色枠(背景色+枠線色)
BOX_BG = ("gray90", "gray21")
BOX_BORDER = ("gray65", "gray34")


def _toolbar_sep(parent, side="left"):
    """ツールバーのボタン群を分ける縦の区切り線(=107)。

    空の CTkFrame は既定で 200x200 を要求するので、width/height を明示し
    pack_propagate(False) で潰しておく(進捗メモの既知の落とし穴)。
    =255: 右寄せ群(タイトル行)でも使えるよう side を指定できる。
    """
    sep = ctk.CTkFrame(parent, width=2, height=24, corner_radius=0,
                       fg_color=("gray60", "gray45"))
    sep.pack_propagate(False)
    sep.pack(side=side, padx=8, pady=3)
    return sep

# 検証エラー/警告の原因ウィジェットを着色する色(明/暗)。
# 入力欄・メニューは背景色、チェックボックスは枠/文字色で示す。
FIELD_ERR_BG = ("#f4bcbc", "#5e2626")
FIELD_WARN_BG = ("#f1dc9e", "#5e4d1e")
FIELD_ERR_EDGE = "#e05a5a"
FIELD_WARN_EDGE = "#e0a23a"

# 画面内メッセージ領域の色(種別ごと)。モーダルダイアログ+警告音の代替。
# =114: ライトの薄い背景では元の明るい色が沈む(緑1.6:1・橙2.2:1・赤3.2:1)
# ため、文字色として使うこの3色も (ライト, ダーク) タプルにする。
# **枠線用の FIELD_*_EDGE は据え置き**(面の縁取りなので視認できる)。
MSG_ERROR = ("#c93a3a", "#e05a5a")
MSG_WARN = ("#8f6300", "#e0a23a")
MSG_OK = ("#1a7c43", OK_COLOR)
MSG_INFO = ("gray10", "gray62")   # =112: ライトでは濃く

# テスト観測用のメッセージ記録(kind, (title, body))。実UIは _report が描画する。
#   kind は messagebox 互換で "error" / "warning" / "info" / "confirm"。
MESSAGE_LOG: list = []

# テスト用フック: True のとき _confirm は確認UIを出さず即座に「実行」を選ぶ
# (旧 messagebox.askyesno=lambda:True の置き換え)。実行時は常に False。
AUTO_CONFIRM = False

DEVICE_TYPES = ("linear", "twist", "rotate_ufo", "rotate_a10cyclonesa",
                "vibration")


def menu_device_types(current: str = "") -> list:
    """種別メニューへ出すデバイス種別。

    =88: TWISTのサブ機能スイッチ(twist_enabled)は廃止され常時表示に
    なったため、常に全種別を返す(相関制御が分かりにくいというユーザー判断)。
    引数 current は=79当時の名残(呼び出し側の互換のため残す)。
    """
    return list(DEVICE_TYPES)
CHANNEL_IDS = ("L", "C", "R")

# 動画(外部mpv再生)。=52 で動画がチャンネルのアイテムになったため、
# 拡張子/ダイアログのフィルタをモジュール定数へ移した(旧 VideoRow の定義)。
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".avi", ".wmv", ".mov", ".m4v",
              ".mpg", ".mpeg", ".ts", ".flv", ".ogv")
VIDEO_FILETYPES = [(tr("動画ファイル"), "*.mp4 *.mkv *.webm *.avi *.wmv *.mov"),
                   (tr("すべて"), "*.*")]

INFINITE_CHOICE = tr("無限(移行/終了まで)")


def _front_window(win):
    """ウィンドウを確実に前面へ出す。

    CTkToplevelは内部で withdraw→約200ms後に deiconify し、さらに元の
    フォーカスウィジェット(=親ウィンドウ側)へフォーカスを戻すため、
    Windowsでは親ウィンドウが手前に出てしまう。内部タイマーが収まった後
    にも再前面化する(topmostは一時的に付けてすぐ外す)。
    """
    def once():
        try:
            if not win.winfo_exists():
                return
            win.lift()
            win.attributes("-topmost", True)
            win.after(50, release)
            win.focus_force()
        except Exception:
            pass

    def release():
        try:
            if win.winfo_exists():
                win.attributes("-topmost", False)
        except Exception:
            pass

    once()
    try:
        win.after(250, once)
        win.after(600, once)
    except Exception:
        pass


def _has_varref(*vals) -> bool:
    """終了条件やtransitionの数値欄に変数参照({"var": ...})が含まれるか。"""
    return any(isinstance(v, dict) for v in vals)


def _num_disp(v) -> str:
    """数値欄の表示文字列(変数参照は「変数:名前」)。"""
    if isinstance(v, dict):
        return tr("変数:{0}").format(v.get("var", "?"))
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)

def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parse_num_text(text: str):
    """数値文字列をint/floatへ。不正はValueError。整数値はintで返す。"""
    v = float(text)
    return int(v) if v.is_integer() else v


_COND_OPS = ("==", "!=", ">=", "<=", ">", "<")


# =128: 新規イベントは全チャンネルOFF(=音声なしイベント)で作る
# (以前は Cチャンネル有効+device linear→C が既定だった。ユーザー要望)。
DEFAULT_EVENT = {
    "next": None,
}

# ステート追加時の既定(end省略=移行/イベント終了まで無限)。
# =285: device は空dict=全種別「なし」(ユーザー依頼。未指定だと再生側の
# 既定=全種別C になり、デバイス連動を使わない人には保存時の警告の元)
# =286: end は「1周で終了」(省略=無限だった。ユーザー依頼で順番に再生の既定を
# 1周で終了に。ランダム系へ切り替えると N秒で終了 が既定=_on_mode_change)
DEFAULT_STATE = {
    "channels": {"C": {"mode": "sequential", "items": [],
                       "end": {"type": "once"}}},
    "device": {},
}


def _load_raw(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    # v1 nodes形式 → channels形式へ変換
    if "nodes" in data and "events" not in data:
        events = {}
        for node_id, raw in data["nodes"].items():
            item = {"audio": raw.get("audio")}
            if raw.get("funscript"):
                item["tracks"] = [{"type": "linear", "funscript": raw["funscript"]}]
            else:
                item["funscript"] = None
            events[node_id] = {
                "device": {"linear": "C"},
                "channels": {"C": {"mode": "sequential", "items": [item],
                                   "end": {"type": "once"}}},
                "next": raw.get("next"),
            }
        converted = {"title": data.get("title", ""), "start": data.get("start"),
                     "events": events}
        # detail等、変換対象以外のトップレベルキーは保持する
        for key, v in data.items():
            if key not in ("title", "start", "nodes", "events"):
                converted[key] = v
        data = converted
    # items直書き → channels形式へ
    for ev in data.get("events", {}).values():
        if "channels" not in ev and "states" not in ev and "items" in ev:
            ch = {"mode": ev.pop("mode", "sequential"), "items": ev.pop("items")}
            if "end" in ev:
                ch["end"] = ev.pop("end")
            if "interval" in ev:
                ch["interval"] = ev.pop("interval")
            ev["channels"] = {"C": ch}
            ev.setdefault("device", {"linear": "C"})
    # 旧形式(イベント/ステート直下の "video")→ 動画チャンネルへ移行(=52)。
    # scenario.py と同じ関数を通すので、編集画面で開いて保存すると新形式で
    # 書き出される(ユーザー決定=自動移行して新形式に統一)。空きチャンネルが
    # 無いイベントは移行できないので、そのイベントだけ旧形式のまま残し、
    # 保存時の検証(Scenario.load)で明確なエラーにする。
    for ev_id, ev in (data.get("events") or {}).items():
        if not isinstance(ev, dict):
            continue
        where = tr("イベント '{0}'").format(ev_id)
        if "states" not in ev:
            # ステート形式ではイベント直下の video は元々エラーなので触らない
            try:
                data["events"][ev_id] = migrate_video_node(ev, where)
            except ValueError:
                continue
            ev = data["events"][ev_id]
        for sid, st in list((ev.get("states") or {}).items()):
            try:
                ev["states"][sid] = migrate_video_node(
                    st, tr("{0} ステート'{1}'").format(where, sid),
                    is_state=True)
            except ValueError:
                continue
    return data


def _title_from_path(path: str | None) -> str:
    """JSONファイルのパスからタイトル(=拡張子を除いたファイル名)を得る。
    パス未確定(新規・未保存)のときは空文字を返す。"""
    if not path:
        return ""
    return os.path.splitext(os.path.basename(path))[0]


def _load_dialog_dir(key: str) -> str:
    """ファイルダイアログの前回フォルダを設定ファイルから読む(=270)。

    キーは "editor"(シナリオ編集画面の全ダイアログで共有) /
    "review_save"(レビュー画面の新規保存・独立)。未記録なら空文字。
    """
    try:
        dd = load_config().get("dialog_dirs")
        v = dd.get(key) if isinstance(dd, dict) else None
        return v if isinstance(v, str) else ""
    except Exception:
        return ""


def _dialog_initialdir(base_dir: str, key: str = "editor") -> str:
    """ファイルダイアログの初期ディレクトリ(=270で設定ファイルへ永続化)。

    前回選択フォルダ(~/.rvp_config.json の dialog_dirs[key])が実在すれば
    それ、無ければ base_dir を返す。アプリを再起動しても引き継がれる。
    (=270以前はウィンドウ属性への一時記憶=アプリ終了で消えていた)
    """
    d = _load_dialog_dir(key)
    return d if d and os.path.isdir(d) else base_dir


def _script_track_type(path: str) -> str | None:
    """funscript/CSV のファイル名タグから紐づけるデバイス種別を返す。

    =44(D&D)と同じ規則: タグ(ufo→rotate_ufo / a10→rotate_a10cyclonesa /
    vib→vibration)で判定し、タグ無し .funscript は linear、.csv はROTATE系のみで
    タグ無しは rotate_ufo。対象外拡張子は None。
    """
    ext = os.path.splitext(path)[1].lower()
    stem = os.path.splitext(os.path.basename(path))[0].casefold()
    matched = [t for t in menu_device_types()
               if AUTO_FS_TAGS.get(t) and AUTO_FS_TAGS[t] in stem]
    if ext == ".funscript":
        return matched[0] if matched else "linear"
    if ext == ".csv":
        csv_ok = [t for t in matched if t in CSV_TRACK_TYPES]
        return csv_ok[0] if csv_ok else "rotate_ufo"
    return None


def _raw_has_video(channels) -> bool:
    """チャンネルraw(dict)に動画アイテムが1つでもあるか(=52)。"""
    for ch in (channels or {}).values():
        if not isinstance(ch, dict):
            continue
        for it in ch.get("items") or []:
            if isinstance(it, dict) and it.get("video") is not None:
                return True
    return False


def _remember_dialog_dir(path: str, key: str = "editor"):
    """選択したファイルのフォルダを次回用に設定ファイルへ記憶する(=270)。

    ~/.rvp_config.json の "dialog_dirs" に {key: フォルダ} で保存する
    (言語等の既存キーは保持)。書き込み失敗は黙って無視(記憶が効かない
    だけで操作は成立させる)。
    """
    if not path:
        return
    try:
        cfg = load_config()
        dd = cfg.get("dialog_dirs")
        if not isinstance(dd, dict):
            dd = {}
        dd[key] = os.path.dirname(os.path.abspath(path))
        cfg["dialog_dirs"] = dd
        save_config(cfg)
    except Exception:
        pass


def _ellipsize_middle(text: str, font, max_px: int) -> str:
    """テキストを「先頭…末尾」形式で max_px に収まるよう省略する(=101)。

    ファイル名向けに**末尾(連番・拡張子)を優先して残す**。=96と同じく
    平均文字幅からの推定+近傍実測で、font.measure の回数を数回に抑える
    (Windowsには measure 1回が10ms超の実機がある)。
    """
    if max_px <= 0:
        return text
    full = font.measure(text)
    if full <= max_px:
        return text
    avg = max(1.0, full / max(1, len(text)))
    dots = font.measure("…")
    keep = max(4, int((max_px - dots) / avg))   # 残せる文字数の推定
    def cand(k: int) -> str:
        tail = min(12, max(2, k // 2))          # 末尾は最大12文字を確保
        head = max(2, k - tail)
        return text[:head] + "…" + text[-tail:]
    # 推定から実測で微調整(通常2〜4回のmeasureで確定)
    while keep > 4 and font.measure(cand(keep)) > max_px:
        keep -= 1
    while keep < len(text) - 1 and font.measure(cand(keep + 1)) <= max_px:
        keep += 1
    return cand(keep)


class Tooltip:
    """マウスオーバーで全文を表示する簡易ツールチップ(=101)。

    text_getter が空文字を返す間は表示しない(=省略されていない時は
    出さない)。ウィジェットの使い回し(ItemRow.reload)にも追従する。
    """

    def __init__(self, widget, text_getter, delay_ms: int = 450):
        self.widget = widget
        self.text_getter = text_getter
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _e=None):
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay_ms, self._show)
        except Exception:
            pass

    def _on_leave(self, _e=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        try:
            text = self.text_getter() or ""
        except Exception:
            text = ""
        if not text or self._tip is not None:
            return
        try:
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 18
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            tk.Label(tip, text=text, justify="left",
                     background="#2b2b2b", foreground="#f2f2f2",
                     relief="solid", borderwidth=1,
                     font=(appfont.FAMILY, 10), padx=6, pady=3).pack()
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class VarRefField(ctk.CTkFrame):
    """数値欄+変数指定トグル。

    値は「数値(テキスト)」または変数参照 {"var": 名前}。
    「x」トグルON で入力欄が変数選択メニューに切り替わる。
    利用可能な変数が無いときはトグル自体を表示しない(密集化対策)。
    """

    def __init__(self, master, width=54, placeholder="", on_change=None):
        super().__init__(master, fg_color="transparent")
        self.on_change = on_change
        self.use_var = False
        self.names: list[str] = []
        self._state = "normal"
        self.text_var = tk.StringVar(value="")
        self.entry = ctk.CTkEntry(self, textvariable=self.text_var,
                                  width=width, height=26,
                                  placeholder_text=placeholder)
        self.sel_var = tk.StringVar(value="")
        # =64(起動短縮): 変数選択メニューとトグルボタンは**使うときに作る**。
        # 編集画面には VarRefField が数十個並ぶが、その多くは変数を使わない
        # (=メニュー不要)。CTkOptionMenu/CTkButton の生成は1個あたり
        # 10ms前後かかるため、遅延生成が開く速度に直接効く。
        self._width = width
        self._menu = None
        self._toggle_btn = None
        self._sync()

    # ---- 遅延生成(=64) ----

    @property
    def menu(self):
        """変数選択メニュー(初回アクセス時に生成)。"""
        if self._menu is None:
            self._menu = CTkOptionMenu(
                self, variable=self.sel_var,
                width=max(self._width + 40, 90), height=26,
                values=self.names or [""],
                fg_color=("gray75", "gray28"),
                button_color=("gray70", "gray33"))
            if self._state != "normal":
                self._menu.configure(state=self._state)
        return self._menu

    @property
    def toggle_btn(self):
        """変数指定トグル(初回アクセス時に生成)。"""
        if self._toggle_btn is None:
            self._toggle_btn = ctk.CTkButton(
                self, text="x", width=26, height=26,
                font=ctk.CTkFont(size=12, slant="italic", weight="bold"),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray30", "gray70"),
                hover_color=("gray85", "gray28"),
                command=self._toggle)
            if self._state != "normal":
                self._toggle_btn.configure(state=self._state)
        return self._toggle_btn

    # ---- API ----

    def set_names(self, names: list[str]):
        """選択できる変数名を設定する。空なら静的入力のみ(トグル非表示)。"""
        self.names = list(names)
        if self.names:
            if self._menu is not None:
                self._menu.configure(values=self.names)
            if self.sel_var.get() not in self.names:
                self.sel_var.set(self.names[0])
        else:
            self.use_var = False
        self._sync()

    def set(self, value):
        """値を反映する。dict={"var":..}なら変数モード、それ以外は静的テキスト。"""
        if isinstance(value, dict):
            self.use_var = True
            name = value.get("var", "")
            if name and name not in self.names:
                # 宣言に無い名前でも表示は維持する(保存時の検証で捕捉)
                self.names.append(name)
                if self._menu is not None:
                    self._menu.configure(values=self.names)
            self.sel_var.set(name)
        else:
            self.use_var = False
            if value is None:
                self.text_var.set("")
            elif isinstance(value, float):
                self.text_var.set(f"{value:g}")
            else:
                self.text_var.set(str(value))
        self._sync()

    def get_raw(self):
        """{"var": 名前}(変数モード) または 入力テキスト(str) を返す。"""
        if self.use_var:
            return {"var": self.sel_var.get()}
        return self.text_var.get()

    def get_text(self) -> str:
        return self.text_var.get()

    def set_state(self, state: str):
        self._state = state
        self.entry.configure(state=state)
        # 未生成のものは生成時に反映する(=64)
        if self._menu is not None:
            self._menu.configure(state=state)
        if self._toggle_btn is not None:
            self._toggle_btn.configure(state=state)

    # ---- 内部 ----

    def _toggle(self):
        self.use_var = not self.use_var
        self._sync()
        if self.on_change:
            self.on_change()

    def _sync(self):
        # =64: 未生成のメニュー/トグルは「隠れている」のと同じなので触らない
        # =282: 並びは常に「[入力欄 or 変数メニュー] [x]」(x は右)。従来は
        # 入力欄/メニューを pack_forget→pack し直すたびに pack 順の末尾へ
        # 回り、x を1回押した後は x が左へ移動していた(1回目のトグルで
        # 初めて生成される x は入力欄の後ろに並ぶが、以後の入れ替えで
        # 入力欄/メニューが x の後ろへ回る)。x を必ず一度外して最後に
        # 付け直すことで並びを固定する。
        if self._toggle_btn is not None:
            self._toggle_btn.pack_forget()
        if self.use_var and self.names:
            self.entry.pack_forget()
            self.menu.pack(side="left")
            self.toggle_btn.configure(fg_color=ACCENT, border_color=ACCENT,
                                      text_color="white")
        else:
            if self._menu is not None:
                self._menu.pack_forget()
            self.entry.pack(side="left")
            if self._toggle_btn is not None:
                self._toggle_btn.configure(fg_color="transparent",
                                           border_color=MUTED,
                                           text_color=("gray30", "gray70"))
        if self.names:
            self.toggle_btn.pack(side="left", padx=(3, 0))


class CondListEditor(ctk.CTkFrame):
    """判定式(AND条件)リストの編集部品。watch・変数分岐(cond)で共用。

    各行: [変数▼] [演算子▼] [値(VarRefField)] [✕]。最低1行。
    """

    def __init__(self, master, min_rows: int = 1):
        super().__init__(master, fg_color="transparent")
        self.min_rows = min_rows
        self.rows: list[dict] = []
        self.all_names: list[str] = []
        self.rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_frame.pack(fill="x")
        self.add_btn = ctk.CTkButton(
            self, text=tr("＋AND条件"), width=90, height=22,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self.add_row())
        self.add_btn.pack(anchor="w", pady=(2, 0))

    def set_names(self, names: list[str]):
        self.all_names = list(names)
        for r in self.rows:
            r["var_menu"].configure(values=self.all_names or [""])
            r["value"].set_names(self.all_names)

    def load(self, conds_raw: list):
        for r in self.rows:
            r["frame"].destroy()
        self.rows = []
        for c in conds_raw or []:
            if isinstance(c, dict):
                self.add_row(c)
        while len(self.rows) < self.min_rows:
            self.add_row()

    def add_row(self, raw: dict | None = None):
        raw = raw or {}
        row = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        var_var = tk.StringVar(
            value=raw.get("var") or (self.all_names[0] if self.all_names else ""))
        var_menu = CTkOptionMenu(
            row, variable=var_var, width=110, height=24,
            values=self.all_names or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        var_menu.pack(side="left")
        op_var = tk.StringVar(value=raw.get("op") if raw.get("op") in _COND_OPS
                              else "==")
        CTkOptionMenu(
            row, variable=op_var, width=58, height=24, values=list(_COND_OPS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
        ).pack(side="left", padx=4)
        value = VarRefField(row, width=70, placeholder=tr("値"))
        value.set_names(self.all_names)
        value.set(raw.get("value") if "value" in raw else "")
        value.pack(side="left")
        entry = {"frame": row, "var_var": var_var, "var_menu": var_menu,
                 "op_var": op_var, "value": value}
        ctk.CTkButton(row, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_row(e)
                      ).pack(side="left", padx=(4, 0))
        self.rows.append(entry)

    def _delete_row(self, entry):
        if len(self.rows) <= self.min_rows:
            return
        self.rows.remove(entry)
        entry["frame"].destroy()

    def collect(self, where: str, string_vars: set | None = None, mark=None):
        """(エラーメッセージ, 判定式リスト) を返す。

        string_vars: 文字列型の変数名集合(値をテキストのまま保持する判定に使う)。
        mark: 不正フィールドを着色するコールバック(通常 ScenarioEditor._want_mark)。
              変数未選択なら変数メニュー、値が不正なら値欄(VarRefField)を渡す。
        """
        string_vars = string_vars or set()
        out = []
        for i, r in enumerate(self.rows):
            name = r["var_var"].get()
            if not name:
                if mark:
                    mark(r["var_menu"], "error")
                return tr("{0}: 条件{1}の変数を選択してください").format(where, i + 1), None
            v = r["value"].get_raw()
            if isinstance(v, str):
                if name in string_vars:
                    pass  # 文字列変数はテキストをそのまま比較値にする
                else:
                    try:
                        v = _parse_num_text(v)
                    except ValueError:
                        if mark:
                            mark(r["value"], "error")
                        return tr("{0}: 条件{1}の値が不正です").format(where, i + 1), None
            out.append({"var": name, "op": r["op_var"].get(), "value": v})
        if not out:
            return tr("{0}: 条件を1つ以上指定してください").format(where), None
        return None, out


def _range_disp(v) -> str:
    """区間欄の表示文字列。None/非数値は空欄(=指定なし)。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return ""
    return f"{float(v):g}"


def _raw_range(raw) -> tuple[str, str]:
    """dict の "range" を (開始秒の表示, 終了秒の表示) にする(=59)。"""
    r = raw.get("range") if isinstance(raw, dict) else None
    if not isinstance(r, dict):
        return "", ""
    return _range_disp(r.get("start")), _range_disp(r.get("end"))


def _validate_range_fields(start_var, end_var, start_widget, end_widget,
                           disp_name: str, owner) -> str | None:
    """区間欄(開始秒/終了秒)を検証する(=59。アイテム/トラック共通)。

    空欄=指定なし。数値でない/開始が負/終了<=開始 はエラーにして、
    原因のフィールドを赤くする(=8の標準動作)。
    """
    vals = {}
    for key, var, widget in (("start", start_var, start_widget),
                             ("end", end_var, end_widget)):
        txt = var.get().strip()
        if not txt:
            vals[key] = None
            continue
        try:
            vals[key] = float(txt)
        except ValueError:
            if owner is not None:
                owner._want_mark(widget, "error")
            return tr("{0}: 区間は秒数(数値)で指定してください").format(disp_name)
    if vals["start"] is not None and vals["start"] < 0:
        if owner is not None:
            owner._want_mark(start_widget, "error")
        return tr("{0}: 区間の開始は0以上にしてください").format(disp_name)
    if vals["end"] is not None and vals["end"] <= (vals["start"] or 0.0):
        if owner is not None:
            owner._want_mark(end_widget, "error")
        return tr("{0}: 区間の終了は開始より後にしてください").format(disp_name)
    return None


def _range_dict(start_var, end_var) -> dict | None:
    """区間欄の値を JSON の "range" 辞書にする(=59)。空欄/0だけなら None。"""
    out = {}
    for key, var in (("start", start_var), ("end", end_var)):
        txt = var.get().strip()
        if not txt:
            continue
        try:
            v = float(txt)
        except ValueError:
            continue
        if key == "start" and v == 0:
            continue          # 0=先頭は既定なのでキーを書かない
        out[key] = v
    return out or None


class TrackRowsMixin:
    """トラック行(種別+funscript/CSV+✕)のリスト編集を提供する共有部品(=50)。

    =50で ItemRow と VideoRow の編集体験を揃えるために切り出した共有部品。
    =52 で動画がチャンネルのアイテムになり VideoRow は廃止されたが、
    トラック行の生成・選択・D&D紐づけ・検証・書き出しはこのまま使う。
    利用側は以下を用意する:

      self.base_dir / self.owner / self.compact / self.mode / self.mode_var
      self.is_script(省略可=False) / self._update_mode_ui()

    `_build_tracks_area(parent)` でウィジェット一式を作り、`_update_mode_ui`
    から `self.tracks_area` の pack/pack_forget を制御する。
    """

    def _build_tracks_area(self, parent):
        """手動モードのトラック行リスト(枠+＋追加ボタン+注記)を生成する。

        height=0: トラック0本(funscript:null等)の空フレームが CTkFrame 既定の
        200pxを確保して、行が縦に間延びするのを防ぐ(中身が入れば追従)。
        """
        if not hasattr(self, "track_rows"):
            self.track_rows = []
        self.tracks_area = ctk.CTkFrame(parent, fg_color="transparent")
        self.tracks_frame = ctk.CTkFrame(self.tracks_area,
                                         fg_color="transparent", height=0)
        self.tracks_frame.pack(fill="x")
        foot = ctk.CTkFrame(self.tracks_area, fg_color="transparent")
        foot.pack(fill="x")
        self.add_track_btn = ctk.CTkButton(
            foot, text=tr("＋トラック追加"), width=110, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_track_row())
        self.add_track_btn.pack(side="left", pady=(2, 0))
        self.no_track_label = ctk.CTkLabel(
            foot, text=tr("(トラックなし=デバイス動作なし)"),
            font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self.no_track_label.pack(side="left", padx=8)

    def _on_tracks_changed(self):
        """トラック構成(本数/種別)が変わったときの通知フック(既定=何もしない)。

        (=50では VideoRow がオーバーライドしていた。=52で廃止。)
        """

    def _track_type_capacity(self) -> int:
        """追加できるトラック行の上限(=79: twist非表示時は4)。"""
        used = {e["type_var"].get() for e in self.track_rows}
        return len(set(menu_device_types()) | used)

    def _update_tracks_ui(self):
        """手動モードのトラック領域(追加ボタンの可否・注記・高さ)を更新する。"""
        self.add_track_btn.configure(
            state="normal" if len(self.track_rows) < self._track_type_capacity()
            else "disabled")
        if self.track_rows:
            self.no_track_label.pack_forget()
        else:
            self.no_track_label.pack(side="left", padx=8)
        self._refit_tracks()

    def _refit_tracks(self):
        """トラック0本のとき tracks_frame を潰す(空フレームの間延び防止)。"""
        if self.track_rows:
            self.tracks_frame.pack_propagate(True)
        else:
            self.tracks_frame.pack_propagate(False)
            self.tracks_frame.configure(height=1)

    # ---- トラック行 ----

    def _add_track_row(self, ttype: str = "", fs: str = "",
                       start: str = "", end: str = ""):
        if len(self.track_rows) >= self._track_type_capacity():
            return
        if not ttype:
            used = {e["type_var"].get() for e in self.track_rows}
            free = [t for t in menu_device_types() if t not in used]
            ttype = free[0] if free else DEVICE_TYPES[0]
        # 追加時は中身に追従して伸びるよう propagate を戻す
        self.tracks_frame.pack_propagate(True)
        row = ctk.CTkFrame(self.tracks_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        # compact時は「種別+✕」を上段、funscript選択を下段(全幅)に分ける
        r_top = ctk.CTkFrame(row, fg_color="transparent")
        r_top.pack(fill="x")
        r_bot = ctk.CTkFrame(row, fg_color="transparent") if self.compact else r_top
        if self.compact:
            r_bot.pack(fill="x", pady=(2, 0))
        type_var = tk.StringVar(
            value=ttype if ttype in DEVICE_TYPES else "linear")
        type_menu = CTkOptionMenu(
            r_top, variable=type_var, width=150 if self.compact else 170, height=24,
            font=ctk.CTkFont(size=11),
            values=menu_device_types(ttype),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        type_menu.pack(side="left")
        entry = {"frame": row, "type_var": type_var, "type_menu": type_menu,
                 "fs_path": fs or ""}
        # 種別を変えたら未選択時のプレースホルダ(funscript / CSV)を更新する
        type_menu.configure(
            command=lambda _v, e=entry: (self._update_track_row(e),
                                         self._on_tracks_changed()))
        ctk.CTkButton(r_top, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_track_row(e)
                      ).pack(side="right", padx=(2, 0))
        fs_btn = ctk.CTkButton(
            r_bot, text="", width=180, height=24, anchor="w",
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
            command=lambda e=entry: self._pick_track_fs(e))
        fs_btn.pack(side="left", padx=(0, 4) if self.compact else 4,
                    fill="x", expand=True)
        entry["fs_btn"] = fs_btn
        # ---- トラック個別の区間(=59)。「詳細設定」ON のときだけ表示する ----
        # 空欄=アイテムの区間に連動。ここに入れるとそちらが優先される。
        r_rng = ctk.CTkFrame(row, fg_color="transparent")
        entry["range_row"] = r_rng
        ctk.CTkLabel(r_rng, text=tr("区間:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        entry["start_var"] = tk.StringVar(value=start or "")
        entry["start_entry"] = ctk.CTkEntry(
            r_rng, width=52, height=22, textvariable=entry["start_var"],
            font=ctk.CTkFont(size=11), placeholder_text=tr("連動"))
        entry["start_entry"].pack(side="left", padx=(4, 2))
        # =67: 区切りは「[値]秒 〜 [値]秒」で他の min〜max 行と揃える
        ctk.CTkLabel(r_rng, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(r_rng, text="〜", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(4, 2))
        entry["end_var"] = tk.StringVar(value=end or "")
        entry["end_entry"] = ctk.CTkEntry(
            r_rng, width=52, height=22, textvariable=entry["end_var"],
            font=ctk.CTkFont(size=11), placeholder_text=tr("連動"))
        entry["end_entry"].pack(side="left", padx=(4, 2))
        ctk.CTkLabel(r_rng, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")   # =67 単位を両方に
        self.track_rows.append(entry)
        self._update_track_row(entry)
        self._refresh_track_ranges()
        self._update_mode_ui()

    def _refresh_track_ranges(self):
        """トラックの区間欄を「詳細設定」の状態に合わせて出し入れする(=59)。"""
        show = bool(getattr(self, "_detail", False))
        for e in getattr(self, "track_rows", []):
            row = e.get("range_row")
            if row is None:
                continue
            if show:
                row.pack(fill="x", pady=(2, 0))
            else:
                row.pack_forget()

    def _update_track_row(self, entry):
        # ROTATE系(ufo / a10)は funscript の他に CSV も選べるため、
        # 未選択時のプレースホルダも「funscript または CSV」を示す。
        ttype = entry["type_var"].get()
        # =79: 行の種別に応じて候補を更新(twist行はOFFでも自分の種別を保持)
        entry["type_menu"].configure(values=menu_device_types(ttype))
        if ttype in ("rotate_ufo", "rotate_a10cyclonesa"):
            placeholder = tr("(funscriptまたはCSVを選択...)")
        else:
            placeholder = tr("(funscriptを選択...)")
        entry["fs_btn"].configure(
            text=os.path.basename(entry["fs_path"]) or placeholder)

    def _pick_track_fs(self, entry):
        # ROTATE系トラックは funscript の他に CSV も選べる。
        ttype = entry["type_var"].get()
        if ttype in ("rotate_ufo", "rotate_a10cyclonesa"):
            filetypes = [(tr("funscript / CSV"), "*.funscript *.csv"),
                         ("funscript", "*.funscript"), ("CSV", "*.csv"),
                         (tr("すべて"), "*.*")]
            title = tr("funscript または CSV を選択")
        else:
            filetypes = [("funscript", "*.funscript"), (tr("すべて"), "*.*")]
            title = tr("funscriptを選択")
        path = filedialog.askopenfilename(
            title=title,
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=filetypes, parent=self,
        )
        if not path:
            return
        _remember_dialog_dir(path)
        entry["fs_path"] = _safe_relpath(path, self.base_dir)
        self._update_track_row(entry)

    def dropped_track_hit(self, x: int, y: int):
        """=278: 落下点(スクリーン座標)が手動トラック行の上ならその種別を返す。

        行の矩形(トラック行フレーム)で判定する。表示中のもののみ。
        """
        for e in self.track_rows:
            w = e.get("frame")
            try:
                if w is None or not w.winfo_ismapped():
                    continue
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                if (wx <= x < wx + w.winfo_width()
                        and wy <= y < wy + w.winfo_height()):
                    return e["type_var"].get()
            except Exception:
                continue
        return None

    def bind_dropped_fs(self, path, track_type: str | None = None):
        """D&Dで落とされた funscript/CSV をこのアイテム/動画へ手動紐づけする(=44)。

        種別はファイル名タグの既存命名ルール(ufo→rotate_ufo /
        a10→rotate_a10cyclonesa / vib→vibration)で決める。
        .funscript のタグ無しは linear、.csv はROTATE系のみで
        タグ無しは rotate_ufo(ユーザー確認済みの既定。a10はタグで指定)。
        自動モードなら手動モードへ切り替え(自動解決結果はトラック行として
        引き継がれる)、同種別のトラックがあれば差し替え、無ければ追加する。
        対象外拡張子・空きトラックなし・失敗は黙って無視(仕様)。

        =278: track_type を渡すと(=特定のトラック行の上へ落とした)、
        ファイル名タグによらず**その種別のトラック**へ紐づける
        (twist 行へ .funscript を落とせば twist に、linear 行へ
        .twist.funscript を落とせば linear になる。クリック→ダイアログ
        選択と同じ結果)。.csv はROTATE系の行以外へは落とせない
        (その場合は従来のタグ規則へ戻す)。
        """
        if not self._device_on():
            return          # =252: デバイス連動OFFでは紐づけを受け付けない
        if not os.path.isfile(path):
            return
        ttype = _script_track_type(path)
        if ttype is None:
            return
        if track_type is not None and track_type in DEVICE_TYPES:
            ext = os.path.splitext(path)[1].lower()
            if ext != ".csv" or track_type in CSV_TRACK_TYPES:
                ttype = track_type
        if self.mode != "manual":
            self.mode_var.set(tr("手動"))
            self._on_mode_change()
        rel = _safe_relpath(path, self.base_dir)
        for e in self.track_rows:
            if e["type_var"].get() == ttype:
                e["fs_path"] = rel
                self._update_track_row(e)
                self._on_tracks_changed()
                return
        if len(self.track_rows) >= self._track_type_capacity():
            return
        self._add_track_row(ttype, rel)
        self._update_mode_ui()
        self._on_tracks_changed()

    def _delete_track_row(self, entry):
        if getattr(self, "is_script", False) and len(self.track_rows) <= 1:
            return   # スクリプトのみアイテムはトラック0本にできない
        self.track_rows.remove(entry)
        entry["frame"].destroy()
        self._update_mode_ui()
        self._on_tracks_changed()

    # ---- 読み書き ----

    def _sync_track_rows(self, init_tracks):
        """トラック行を init_tracks [(種別, パス, 開始, 終了)] に合わせる。

        行は使い回す。区間(=59)は文字列("" = アイテムの区間に連動)。
        """
        for i, tr_def in enumerate(init_tracks):
            ttype, fs = tr_def[0], tr_def[1]
            start = tr_def[2] if len(tr_def) > 2 else ""
            end = tr_def[3] if len(tr_def) > 3 else ""
            if i < len(self.track_rows):
                e = self.track_rows[i]
                e["type_var"].set(ttype if ttype in DEVICE_TYPES else "linear")
                e["fs_path"] = fs or ""
                e["start_var"].set(start)
                e["end_var"].set(end)
                self._update_track_row(e)
            else:
                self._add_track_row(ttype, fs, start, end)
        while len(self.track_rows) > len(init_tracks):
            self.track_rows.pop()["frame"].destroy()
        self._refresh_track_ranges()

    @staticmethod
    def _tracks_from_raw(raw: dict):
        """dict の tracks/funscript を (モード, [(種別, パス)]) へ解釈する。

        戻り値のモードは "manual"(明示指定あり)/"auto"(未指定)。
        scenario.parse_tracks と同じ優先順(tracks > funscript > 自動)。
        """
        tracks_raw = raw.get("tracks")
        if isinstance(tracks_raw, list):
            return "manual", [
                (normalize_track_type(t.get("type", "linear")),
                 t.get("funscript", "")) + _raw_range(t)
                for t in tracks_raw if isinstance(t, dict)]
        if "funscript" in raw:
            fs = raw["funscript"]
            return "manual", ([("linear", fs, "", "")] if fs else [])
        return "auto", []

    def _validate_track_rows(self, disp_name: str):
        """手動モードのトラック行を検証する(エラー文言 or None)。"""
        used = set()
        for i, e in enumerate(self.track_rows):
            ttype = e["type_var"].get()
            if ttype in used:
                if self.owner is not None:
                    self.owner._want_mark(e["type_menu"], "error")
                return tr("{0}: デバイス種別 '{1}' が重複しています").format(
                    disp_name, ttype)
            used.add(ttype)
            if not e["fs_path"]:
                if self.owner is not None:
                    self.owner._want_mark(e["fs_btn"], "error")
                return tr("{0}: トラック{1}のfunscriptを選択してください").format(
                    disp_name, i + 1)
            err = _validate_range_fields(
                e["start_var"], e["end_var"], e["start_entry"], e["end_entry"],
                tr("{0} トラック{1}").format(disp_name, i + 1), self.owner)
            if err:
                return err
        return None

    def _write_tracks(self, out: dict):
        """手動モードのトラックを out へ書き出す(0本は "funscript": null)。"""
        if not self.track_rows:
            out["funscript"] = None
            return out
        rows = []
        for e in self.track_rows:
            t = {"type": e["type_var"].get(), "funscript": e["fs_path"]}
            rng = _range_dict(e["start_var"], e["end_var"])
            if rng:
                t["range"] = rng      # =59: 空欄ならキーを書かない(=連動)
            rows.append(t)
        out["tracks"] = rows
        return out

    def track_types(self) -> set:
        """現在のトラック行が担当するデバイス種別の集合(手動モード時)。"""
        return {e["type_var"].get() for e in self.track_rows}


class ItemRow(TrackRowsMixin, ctk.CTkFrame):
    """チャンネル内の音声1件の編集行(複数トラック対応)。

    モード:
      自動 = JSONにキーを書かず、命名ルール(auto_bind_tracks)で紐づける。
             解決結果をその場に表示(linear=wav名を含む同名系 / rotate=+"ufo" /
             rotate_a10cyclonesa=+"a10" / vibration=+"vib")
      手動 = tracksを明示指定。トラック行(種別+funscript+✕)のリストで編集し、
             最大4本・種別重複不可。0本=デバイスなし("funscript": null)
    """

    def __init__(self, master, raw_item, base_dir, on_delete, owner=None,
                 compact: bool = False):
        super().__init__(master, fg_color=("gray82", "gray24"), corner_radius=8)
        self.base_dir = base_dir
        self.on_delete = on_delete
        self.owner = owner   # ScenarioEditor(変数操作の編集用)。Noneも可
        self.compact = compact   # 幅の狭いチャンネル枠向けに要素を縮める
        self.orig = {}
        self.audio_rel = ""
        self.mode = "auto"
        self.track_rows: list[dict] = []

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(6, 2))
        self.head = head

        self.audio_label = ctk.CTkLabel(
            head, text="", font=ctk.CTkFont(size=12), anchor="w",
            width=110 if compact else 200,
        )

        # 削除✕(compactでも常に右端に置く)。
        # =101: ✕/メニューを**ラベルより先にpack**する(packは後のスレーブから
        # 場所を失う=長いファイル名でラベルが幅を食い尽くすと✕やメニューが
        # 消えていた。=66の「履歴✕は先にpack」と同じ落とし穴)。
        del_btn = ctk.CTkButton(
            head, text="✕", width=26, height=26,
            fg_color="transparent", text_color="#e05a5a",
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_delete(self))
        del_btn.pack(side="right", padx=(2, 0))

        # 自動/手動 メニュー。compactは幅を詰めて、選択肢ラベルも短縮表示。
        auto_txt = tr("自動") if compact else tr("自動(ルールで紐づけ)")
        self.mode_var = tk.StringVar(value=auto_txt)
        self.mode_menu = CTkOptionMenu(
            head, variable=self.mode_var, width=96 if compact else 150, height=26,
            values=[auto_txt, tr("手動")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_mode_change(),
        )
        self.mode_menu.pack(side="right", padx=4)

        # =282: 並び替えの取っ手(≡)。「順番に再生」のチャンネルでだけ出す
        # (ランダム系では順序に意味が無い)。取っ手かファイル名を縦に
        # ドラッグすると行が入れ替わる(ボタンでなくD&D=ユーザー決定)。
        # 実体の入れ替えは ChannelSection(reorder_host)が行う。
        self.reorder_host = None
        self.grip = ctk.CTkLabel(
            head, text="≡", width=14, font=ctk.CTkFont(size=13),
            text_color=TEXT_MUTED, cursor="fleur")
        self._grip_shown = False
        for w in (self.grip, self.audio_label):
            w.bind("<ButtonPress-1>", self._on_drag_press)
            w.bind("<B1-Motion>", self._on_drag_motion)
            w.bind("<ButtonRelease-1>", self._on_drag_release)
        self._drag_armed = False
        self._drag_active = False
        self._drag_y0 = 0

        # ラベルは最後にpack=右側の操作類を必ず残し、余り幅だけを使う
        self.audio_label.pack(side="left", fill="x", expand=True)

        # =101: 長いファイル名は「先頭…末尾」に省略し、ホバーで全文を
        # ツールチップ表示する。fit計算は<Configure>からafter(60ms)へ集約
        # (=96と同じ作法。同期でやると発振・起動ブロックの温床になる)。
        self._full_name = ""
        self._name_fit_after = None
        self._name_font = tkfont.Font(family=appfont.FAMILY, size=-12)
        self._name_truncated = False
        head.bind("<Configure>", lambda _e: self._schedule_name_fit())
        self._name_tooltip = Tooltip(
            self.audio_label,
            lambda: self._full_name if self._name_truncated else "")

        # 変数操作(on_play/on_complete)。変数宣言があるときだけ表示。
        # 行の使い回し時に変数の有無が変わりうるため、生成/表示は
        # _ensure_ops_btn() へ集約(_apply_item から毎回呼ぶ)。
        self.ops_btn = None
        self._ensure_ops_btn()

        # =164: アイテムレビュー(音声の試聴+スクリプトのグラフ)。
        # 動画アイテムでは出さない(動画は対象外=ユーザー決定)。表示の
        # 出し入れは _refresh_review_btn()(_apply_item から毎回呼ぶ)。
        self.review_btn = ctk.CTkButton(
            head, text=tr("レビュー"), width=58 if compact else 68, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
            command=self._open_review)

        # ---- weight / pan(アイテム個別) ----
        # weight: ランダム再生chのみ有効な抽選の重み(既定1.0)。
        #         ChannelSection.set_random で表示/非表示を切り替える。
        # pan   : チャンネル既定パンの上書き(左右各0〜100%)。上書きON時のみ書き出す。
        #         表示はチャンネルの「パン詳細」トグルに従う(既定OFF=非表示)。
        # 並びは _refresh_opts_ui() で [重み][パン上書き][L/R] の順に再構築する。
        self._random = False
        self._detail = False               # チャンネルの「詳細設定」ON/OFF
        self.ch_default_pan = (100, 100)   # 上書きON時の初期値(chの実効パン)
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=16, pady=(0, 0))
        self.opts_row = opts
        self.weight_box = ctk.CTkFrame(opts, fg_color="transparent")
        ctk.CTkLabel(self.weight_box, text=tr("重み"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        # =74: 重みは定数 or 変数参照(VarRefField)。数値変数が無ければ
        # 素の数値欄(従来同等)。weight_var は互換エイリアス(テストが
        # .get()/.set() で定数値を読み書きする)。
        self.weight_field = VarRefField(self.weight_box, width=46,
                                        placeholder="1")
        self.weight_field.set("1")
        self.weight_field.pack(side="left", padx=(3, 0))
        self.weight_var = self.weight_field.text_var
        self.pan_var = tk.BooleanVar(value=False)
        self.pan_check = ctk.CTkCheckBox(
            opts, text=tr("パン上書き"), variable=self.pan_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_pan_toggle)
        self.pan_fields = ctk.CTkFrame(opts, fg_color="transparent")
        self.pan_l_var = tk.StringVar(value="")
        self.pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.pan_fields, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_l_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_l_var,
            font=ctk.CTkFont(size=11))
        self.pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.pan_fields, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_r_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_r_var,
            font=ctk.CTkFont(size=11))
        self.pan_r_entry.pack(side="left", padx=(2, 0))

        # ---- アイテムの区間指定 ----
        # =51で動画に導入 → =52でアイテムへ移設 → **=59で音声・スクリプトへ
        # 拡張し、「詳細設定」ON のときだけ表示する**(ユーザー要望=既定は
        # 非表示にしてUIを簡素化)。1本の長尺素材を「開始秒〜終了秒」で
        # 切り分ける。空欄=先頭/末尾。トラックは既定でこの区間に連動する。
        self.range_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.range_row, text=tr("区間:"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.vstart_var = tk.StringVar(value="")
        self.vstart_entry = ctk.CTkEntry(
            self.range_row, width=56, height=24, textvariable=self.vstart_var,
            font=ctk.CTkFont(size=11), placeholder_text=tr("先頭"))
        self.vstart_entry.pack(side="left", padx=(4, 2))
        ctk.CTkLabel(self.range_row, text=tr("秒"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")
        ctk.CTkLabel(self.range_row, text="〜",
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=(4, 2))
        self.vend_var = tk.StringVar(value="")
        self.vend_entry = ctk.CTkEntry(
            self.range_row, width=56, height=24, textvariable=self.vend_var,
            font=ctk.CTkFont(size=11), placeholder_text=tr("末尾"))
        self.vend_entry.pack(side="left", padx=(4, 2))
        # =67: 単位「秒」は**常に**出す(従来は説明文の頭に付いていたため、
        # compact な編集画面では終了欄に単位が無いように見えていた)
        ctk.CTkLabel(self.range_row, text=tr("秒"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.range_hint = ctk.CTkLabel(
            self.range_row, text=tr("(空欄=素材全体・区間の先頭が0秒)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        if not compact:
            self.range_hint.pack(side="left", padx=(2, 0))

        # ---- 自動モード: 解決結果の表示 ----
        self.auto_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            anchor="w", justify="left", wraplength=280 if compact else 520)

        # ---- 手動モード: トラック行リスト(=50で TrackRowsMixin へ共通化) ----
        self._build_tracks_area(self)

        # アイテムのデータ(音声/動画/モード/トラック)を反映(reloadと共通処理)
        self._apply_item(raw_item)
        self._refresh_grip()   # =282: 既定(順番に再生)では取っ手を出す

    def _apply_item(self, raw_item):
        """アイテムのデータをウィジェットへ反映する。

        __init__ と reload() で共通。ウィジェット自体は再利用し、可変部分
        (トラック行)だけ作り直す。CTkウィジェットの生成コストが高いため、
        イベント切替時は ChannelSection がこの reload で行を使い回す。
        """
        # 元のdict(weight等の未対応フィールド保持用)。文字列itemはdict化。
        self.orig = {"audio": raw_item} if isinstance(raw_item, str) \
            else dict(raw_item)
        audio = self.orig.get("audio", "")
        self.audio_rel = audio
        # 動画アイテム(=52 フェーズ3-B): "video" が文字列 or {file,start,end}。
        # 音声とは同じチャンネルに混在できない(チャンネル単位で1種類)。
        self.video_rel, vstart, vend = self._video_from_raw(self.orig)
        self.is_video = bool(self.video_rel)
        # =59: 区間はアイテム共通の "range" へ。旧形式(video辞書内の
        # start/end)は range が無いときだけ読む(保存し直すと range になる)。
        r_start, r_end = _raw_range(self.orig)
        if r_start or r_end:
            vstart, vend = r_start, r_end
        self.vstart_var.set(vstart)
        self.vend_var.set(vend)
        # スクリプトのみアイテム(audio/videoなし+tracks/funscript明示)か。
        # スクリプト専用チャンネルの行。音声は再生せず、トラックの
        # スクリプト長ぶんデバイスを動かす。
        self.is_script = (not audio and not self.is_video and
                          ("tracks" in self.orig or "funscript" in self.orig))
        # =101: 全文は保持し、表示は幅に合わせて省略(_fit_name)
        self._full_name = self._disp_name()
        self._name_truncated = False
        self.audio_label.configure(text=self._full_name)
        self._schedule_name_fit()
        # モードの解釈
        self.mode, init_tracks = self._tracks_from_raw(self.orig)
        if self.is_script:
            # スクリプトのみアイテムは自動紐づけ(音声名基準)が使えない=手動固定
            self.mode = "manual"
        auto_txt = tr("自動") if self.compact else tr("自動(ルールで紐づけ)")
        self.mode_var.set(tr("手動") if self.mode == "manual" else auto_txt)
        self.mode_menu.configure(
            state="disabled" if self.is_script else "normal")
        # weight / pan を反映(行の使い回し時に前アイテムの値が残らないよう毎回設定)
        self._apply_weight_pan()
        # 変数の有無に応じてボタンを遅延生成/表示切替(行の使い回し対応)
        self._ensure_ops_btn()
        # =252: デバイス連動OFFでは自動/手動メニューを隠す(行の使い回し対応)
        self._refresh_device_ui()
        # =164: レビューボタンの表示可否(動画アイテムでは出さない)
        self._refresh_review_btn()
        # トラック行も使い回す(差分だけ生成/破棄)。生成コストが高いため。
        self._sync_track_rows(init_tracks)
        self._update_mode_ui()

    def reload(self, raw_item):
        """既存の行ウィジェットを使い回して別アイテムを表示する(高速化)。"""
        self._apply_item(raw_item)

    @staticmethod
    def _video_from_raw(raw: dict) -> tuple[str, str, str]:
        """item dict の "video" を (相対パス, 開始秒, 終了秒) の文字列で返す。

        動画アイテムでなければ ("", "", "")。秒は欄に入れる文字列
        (未指定/不正は空欄=先頭/末尾)。
        """
        def _sec(v) -> str:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return ""
            return f"{float(v):g}"

        rv = raw.get("video")
        if isinstance(rv, str) and rv:
            return rv, "", ""
        if isinstance(rv, dict):
            f = rv.get("file")
            if isinstance(f, str) and f:
                return f, _sec(rv.get("start")), _sec(rv.get("end"))
        return "", "", ""

    def _disp_name(self) -> str:
        """エラー文言・ダイアログ見出し用のアイテム表示名。"""
        if self.audio_rel:
            return os.path.basename(self.audio_rel)
        if getattr(self, "is_video", False):
            return os.path.basename(self.video_rel)
        if getattr(self, "is_script", False):
            return tr("(スクリプト)")
        return tr("(音声未指定)")

    # ---- ファイル名の省略表示(=101) ----

    def _schedule_name_fit(self):
        """ラベル幅に合わせた省略計算を after(60ms) へ集約して予約する。"""
        if self._name_fit_after is not None:
            return
        try:
            self._name_fit_after = self.after(60, self._fit_name)
        except Exception:
            self._name_fit_after = None

    def _fit_name(self):
        self._name_fit_after = None
        try:
            avail = self.audio_label.winfo_width() - 6
        except Exception:
            return
        if avail <= 12 or not self._full_name:
            return
        fitted = _ellipsize_middle(self._full_name, self._name_font, avail)
        self._name_truncated = (fitted != self._full_name)
        if self.audio_label.cget("text") != fitted:
            self.audio_label.configure(text=fitted)

    # ---- weight / pan(アイテム個別) ----

    def _apply_weight_pan(self):
        """orig の weight / pan をウィジェットへ反映する(_apply_item から呼ぶ)。"""
        # =74: 変数参照トグルのため、利用できる数値変数名を先に流し込む
        if self.owner is not None and hasattr(self.owner, "_numeric_var_names"):
            self.weight_field.set_names(self.owner._numeric_var_names())
        w = self.orig.get("weight", 1.0)
        if isinstance(w, dict):
            self.weight_field.set(w)          # {"var": 名前}=変数モード
        else:
            try:
                self.weight_field.set(f"{float(w):g}")
            except (TypeError, ValueError):
                self.weight_field.set("1")
        pan = self.orig.get("pan")
        if isinstance(pan, dict) and "left" in pan and "right" in pan:
            self.pan_var.set(True)
            self.pan_l_var.set(str(int(round(float(pan["left"]) * 100))))
            self.pan_r_var.set(str(int(round(float(pan["right"]) * 100))))
        else:
            self.pan_var.set(False)
            self.pan_l_var.set("")
            self.pan_r_var.set("")
        self._refresh_opts_ui()

    def _refresh_opts_ui(self):
        """[重み][パン上書き][L/R] を状態に応じて並べ直す。

        重み = ランダム再生chのみ。パン上書き = チャンネルのパン詳細ON時のみ。
        L/R入力 = パン詳細ON かつ 上書きON時のみ。
        表示要素が無いときは opts 枠を潰す(空CTkFrameが既定200pxを確保して
        アイテム行が縦に間延びするのを防ぐ。items_frame/tracks_frameと同じ対処)。
        """
        # =59: 区間欄は音声/動画/スクリプトのすべてに出すが、
        # 「詳細設定」ON のときだけ表示する(既定は非表示=ユーザー要望)。
        if self._detail:
            self.range_row.pack(fill="x", padx=16, pady=(2, 0),
                                before=self.opts_row)
        else:
            self.range_row.pack_forget()
        self._refresh_track_ranges()
        for w in (self.weight_box, self.pan_check, self.pan_fields):
            w.pack_forget()
        any_shown = False
        if self._random:
            self.weight_box.pack(side="left")
            any_shown = True
        # パン上書きはスクリプトのみ/動画アイテムでは無意味なので出さない
        # (動画の音声は mpv 側の管理=合意事項4)
        if self._detail and not getattr(self, "is_script", False) \
                and not getattr(self, "is_video", False):
            self.pan_check.pack(side="left", padx=(10, 4))
            any_shown = True
            if self.pan_var.get():
                self.pan_fields.pack(side="left")
        if any_shown:
            self.opts_row.pack_propagate(True)
        else:
            self.opts_row.pack_propagate(False)
            self.opts_row.configure(height=1)

    def _on_pan_toggle(self):
        """パン上書きチェックの操作。ONで既定値を埋めて入力欄を表示。"""
        if self.pan_var.get():
            if not self.pan_l_var.get():
                self.pan_l_var.set(str(self.ch_default_pan[0]))
            if not self.pan_r_var.get():
                self.pan_r_var.set(str(self.ch_default_pan[1]))
        self._refresh_opts_ui()

    def set_random(self, flag: bool):
        """チャンネルの再生方式(ランダム=weight有効)を反映する。

        ランダム再生chのみ weight 欄を表示する(相関制御)。sequentialでは非表示。
        """
        self._random = bool(flag)
        self._refresh_opts_ui()
        self._refresh_grip()

    # ---- 並び替え D&D(=282) ----

    def _refresh_grip(self):
        """順番に再生のときだけ取っ手(≡)を出す。"""
        show = not self._random
        if show and not self._grip_shown:
            self.grip.pack(side="left", padx=(0, 4), before=self.audio_label)
            self.audio_label.configure(cursor="fleur")
        elif not show and self._grip_shown:
            self.grip.pack_forget()
            self.audio_label.configure(cursor="")
        self._grip_shown = show

    def _on_drag_press(self, event):
        self._drag_armed = bool(self._grip_shown and
                                self.reorder_host is not None)
        self._drag_active = False
        self._drag_y0 = event.y_root

    def _on_drag_motion(self, event):
        if not self._drag_armed:
            return
        if not self._drag_active:
            # 4px 動くまではクリック扱い(ツールチップ等を邪魔しない)
            if abs(event.y_root - self._drag_y0) < 4:
                return
            self._drag_active = True
            self.reorder_host.begin_row_drag(self)
        self.reorder_host.drag_row_motion(self, event.y_root)

    def _on_drag_release(self, _event):
        if self._drag_active:
            self.reorder_host.end_row_drag(self)
        self._drag_armed = False
        self._drag_active = False

    def set_dragging(self, on: bool):
        """ドラッグ中の見た目(アクセント色の枠)。"""
        if on:
            self.configure(border_width=1, border_color=ACCENT)
        else:
            self.configure(border_width=0)

    def set_detail(self, flag: bool):
        """チャンネルの「詳細設定」ON/OFFを反映する(=59で改名)。

        ONで「区間」欄(アイテム・トラック)と「パン上書き」が現れる。
        """
        self._detail = bool(flag)
        self._refresh_opts_ui()

    def set_channel_default_pan(self, left_pct: int, right_pct: int):
        """パン上書きON時の初期値(=チャンネルの実効パン)を受け取る。"""
        self.ch_default_pan = (int(left_pct), int(right_pct))

    # ---- 変数操作 ----

    def _ensure_ops_btn(self):
        """変数宣言の有無に応じて、変数操作ボタンを生成/表示切替する。

        行を破棄せず使い回すため、生成後に変数が増減しても正しく追従する。
        変数ありでボタン未生成なら生成し、右端(削除✕/モードの左)へ配置。
        変数なしなら pack_forget で隠す(オブジェクトは保持=再利用)。
        """
        has_vars = self.owner is not None and bool(self.owner._var_names())
        if has_vars and self.ops_btn is None:
            self.ops_btn = ctk.CTkButton(
                self.head, text="", width=72 if self.compact else 86, height=26,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
                command=self._edit_ops)
        if self.ops_btn is None:
            return
        if has_vars:
            if not self.ops_btn.winfo_ismapped():
                # =101: ラベルより先のpack順にする(before=)。ラベルの後だと
                # 幅が足りないときにこのボタンから消える
                self.ops_btn.pack(side="right", padx=2,
                                  before=self.audio_label)
            self._update_ops_btn()
        else:
            self.ops_btn.pack_forget()

    def _update_ops_btn(self):
        if self.ops_btn:
            n = len(self.orig.get("on_play") or []) \
                + len(self.orig.get("on_complete") or [])
            self.ops_btn.configure(text=tr("変数({0})").format(n))

    # ---- アイテムレビュー(=164) ----

    def _refresh_review_btn(self):
        """レビューボタンの表示を更新する(行の使い回しに追従)。

        =203: **動画アイテムにも出す**(=164の「動画は対象外」を撤回。
        mpv 単独再生+再生/一時停止の同期でレビューできる)。
        レビュー画面を開くのは編集画面なので、owner が無いときも出さない。
        """
        show = (self.owner is not None
                and callable(getattr(self.owner, "open_item_review", None)))
        # =252: デバイス連動OFFのスクリプト専用アイテムはレビュー対象が無い
        # (音声なし+スクリプト非表示=AQ7)ためボタン自体を出さない
        if getattr(self, "is_script", False) and not self._device_on():
            show = False
        if show:
            if not self.review_btn.winfo_ismapped():
                # =101と同じ作法: ラベルより先のpack順にして、幅が足りない
                # ときに削られるのがラベル側になるようにする
                self.review_btn.pack(side="right", padx=2,
                                     before=self.audio_label)
        else:
            self.review_btn.pack_forget()

    def _open_review(self):
        if self.owner is not None:
            self.owner.open_item_review(self)

    def review_spec(self) -> dict:
        """レビュー画面へ渡す「いま入力されている内容」を組み立てる(=164)。

        **保存前の値**が対象(ユーザー決定)。トラックは自動モードなら
        命名ルールの解決結果、手動モードならトラック行の内容。区間は
        =59の規則(トラック個別 > アイテム)で解決して ms で渡す。
        """
        warn = []
        lo, hi, _given, ok = _review_range_ms(self.vstart_var, self.vend_var)
        if not ok:
            warn.append(tr("区間の指定が不正なので、素材全体を対象にします"))
        tracks = []
        # =252: デバイス連動OFFではレビューを音声試聴のみにする(AQ7)。
        # スクリプトのグラフを渡さず、レビュー画面側は "device_off" で
        # 「スクリプト編集」ボタンも隠す。
        if not self._device_on():
            audio = os.path.join(self.base_dir, self.audio_rel) \
                if self.audio_rel else ""
            video = os.path.join(self.base_dir, self.video_rel) \
                if getattr(self, "is_video", False) and self.video_rel else ""
            return {"name": self._disp_name(), "audio": audio, "video": video,
                    "audio_lo": lo, "audio_hi": hi,
                    "tracks": [], "warn": warn, "device_off": True}
        if self.mode == "auto":
            for ttype, path in self._resolve_auto():
                tracks.append((ttype, path, lo, hi))
        else:
            bad_track = False
            for e in self.track_rows:
                if not e["fs_path"]:
                    continue
                t_lo, t_hi, given, t_ok = _review_range_ms(e["start_var"],
                                                           e["end_var"])
                bad_track = bad_track or not t_ok
                if not given:
                    t_lo, t_hi = lo, hi     # 既定=アイテムの区間に連動(=59)
                tracks.append((e["type_var"].get(),
                               os.path.join(self.base_dir, e["fs_path"]),
                               t_lo, t_hi))
            if bad_track:
                warn.append(tr("トラックの区間の指定が不正なので、"
                               "アイテムの区間に連動させます"))
        audio = os.path.join(self.base_dir, self.audio_rel) \
            if self.audio_rel else ""
        video = os.path.join(self.base_dir, self.video_rel) \
            if getattr(self, "is_video", False) and self.video_rel else ""
        return {"name": self._disp_name(), "audio": audio, "video": video,
                "audio_lo": lo, "audio_hi": hi,
                "tracks": tracks, "warn": warn}

    def set_ops(self, result: dict):
        """変数操作ダイアログの結果(on_play/on_complete)を反映する。"""
        for key in ("on_play", "on_complete"):
            if result.get(key):
                self.orig[key] = result[key]
            else:
                self.orig.pop(key, None)
        self._update_ops_btn()

    def _edit_ops(self):
        self.owner.open_ops_dialog(
            self._disp_name(),
            [(tr("再生開始時(on_play)"), "on_play",
              self.orig.get("on_play") or []),
             (tr("自然完了時のみ(on_complete)"), "on_complete",
              self.orig.get("on_complete") or [])],
            self.set_ops)

    # ---- 自動/手動モード ----

    def _resolve_auto(self) -> list[tuple[str, str]]:
        """命名ルールでの自動紐づけ結果 [(種別, 絶対パス)] を返す。

        動画アイテム(=52)は動画ファイル名を基準にする(音声と同じ規則)。
        """
        rel = self.video_rel if getattr(self, "is_video", False) \
            else self.audio_rel
        if not rel:
            return []
        return auto_bind_tracks(os.path.join(self.base_dir, rel))

    def _on_mode_change(self):
        new_mode = "manual" if self.mode_var.get() == tr("手動") else "auto"
        if new_mode == self.mode:
            return
        self.mode = new_mode
        if new_mode == "manual" and not self.track_rows:
            # 自動→手動: 現在の自動解決結果を明示トラックとして引き継ぐ
            for ttype, fs_abs in self._resolve_auto():
                self._add_track_row(
                    ttype, _safe_relpath(fs_abs, self.base_dir))
        self._update_mode_ui()

    def _device_on(self) -> bool:
        """デバイス連動(=252)が有効か。owner(編集画面)のフラグを見る。"""
        return bool(getattr(self.owner, "device_enabled", True))

    def _refresh_device_ui(self):
        """自動/手動メニューの表示をデバイス連動フラグへ追従させる(=252)。

        OFFではメニューを隠す(トラック領域は _update_mode_ui が隠す)。
        ONへ戻すときは pack順(✕→メニュー→レビュー→ラベル)を保つため、
        レビューを一旦外してから入れ直す(review は _refresh_review_btn が
        before=audio_label で再配置する)。
        """
        if self._device_on():
            if not self.mode_menu.winfo_manager():
                self.review_btn.pack_forget()
                self.mode_menu.pack(side="right", padx=4,
                                    before=self.audio_label)
        else:
            self.mode_menu.pack_forget()

    def _update_mode_ui(self):
        # =252: デバイス連動OFFではトラック関連の表示を出さない
        # (裏のモード・トラック行は保持=collect/保存は従来どおり通る)
        if not self._device_on():
            self.tracks_area.pack_forget()
            self.auto_label.pack_forget()
            return
        if self.mode == "auto":
            self.tracks_area.pack_forget()
            resolved = self._resolve_auto()
            if resolved:
                text = tr("自動: {0}").format("  ".join(
                    f"{t}={os.path.basename(p)}" for t, p in resolved))
            else:
                text = tr("自動: 紐づくfunscriptなし")
            self.auto_label.configure(text=text)
            self.auto_label.pack(fill="x", padx=16, pady=(0, 6))
        else:
            self.auto_label.pack_forget()
            self.tracks_area.pack(fill="x", padx=16, pady=(0, 6))
            self._update_tracks_ui()

    # ---- 検証・書き出し ----

    def validate(self) -> str | None:
        # weight(ランダム再生chのみ有効): 数値または変数参照であること。
        # =74: 0/負は許容(実行時に「抽選に出さない」候補になる)
        if self._random and not self.weight_field.use_var:
            txt = self.weight_field.get_text().strip()
            if txt:
                try:
                    float(txt)
                except ValueError:
                    if self.owner is not None:
                        self.owner._want_mark(self.weight_field, "error")
                    return tr("{0}: 重みが不正です").format(
                        self._disp_name())
        # pan上書き: 左右とも0〜100
        if self.pan_var.get():
            for var, w in ((self.pan_l_var, self.pan_l_entry),
                           (self.pan_r_var, self.pan_r_entry)):
                try:
                    v = int(float(var.get()))
                    if not (0 <= v <= 100):
                        raise ValueError
                except ValueError:
                    if self.owner is not None:
                        self.owner._want_mark(w, "error")
                    return tr("{0}: パンは0〜100で指定してください").format(
                        self._disp_name())
        # 区間指定(=59で音声/スクリプトへ拡張): 数値・開始>=0・終了>開始
        err = self._validate_range()
        if err:
            return err
        if self.mode == "auto":
            return None
        return self._validate_track_rows(self._disp_name())

    def _validate_range(self) -> str | None:
        """アイテムの区間欄(開始秒/終了秒)を検証する(=59で全種別が対象)。"""
        return _validate_range_fields(
            self.vstart_var, self.vend_var, self.vstart_entry, self.vend_entry,
            self._disp_name(), self.owner)

    def _mark_owner(self, widget):
        if self.owner is not None:
            self.owner._want_mark(widget, "error")

    def device_types(self) -> set:
        """このアイテムが担当するデバイス種別の集合(相関制御用)。

        手動=トラック行の種別 / 自動=命名ルールで解決できた種別。
        動画アイテムの種別一意性(動画トラック vs チャンネルのdevice担当)を
        エディタ側で先回り防止するために使う(=50の VideoRow から移設)。
        """
        if self.mode == "manual":
            return self.track_types()
        return {t for t, _p in self._resolve_auto()}

    def collect(self) -> dict:
        """編集内容をitem dictへ書き戻す(未対応フィールドは保持)。

        自動モード=キー省略(読み込み時に命名ルールで解決)。
        手動モード=tracksを明示(0本は "funscript": null)。
        """
        item = dict(self.orig)
        # =59: 区間はアイテム共通の "range" キーへ一本化(音声/動画/スクリプト)。
        # 旧形式(video辞書内の start/end)はここで落とすので、開いて保存し直せば
        # 新形式に揃う。
        item.pop("range", None)
        rng = _range_dict(self.vstart_var, self.vend_var)
        if rng:
            item["range"] = rng
        if self.is_video:
            # 動画アイテム(=52): audio は書かず video を書く(常に文字列)
            item.pop("audio", None)
            item["video"] = self.video_rel
        elif self.is_script:
            # スクリプトのみアイテム: audio キーを書かない
            item.pop("audio", None)
            item.pop("video", None)
        else:
            item["audio"] = self.audio_rel
            item.pop("video", None)
        item.pop("tracks", None)
        item.pop("funscript", None)
        # weight(ランダム再生chのみ。既定1.0は書かない)。
        # =74: 変数参照は {"var":..}、定数は 0/負も含め 1.0 以外を書く
        item.pop("weight", None)
        if self._random:
            raw_w = self.weight_field.get_raw()
            if isinstance(raw_w, dict):
                item["weight"] = raw_w
            else:
                txt = raw_w.strip()
                if txt:
                    try:
                        wv = float(txt)
                        if wv != 1.0:
                            item["weight"] = wv
                    except ValueError:
                        pass
        # pan上書き(ONのときだけ書く。0〜100%→0.0〜1.0)
        item.pop("pan", None)
        if self.pan_var.get() and not self.is_video:
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                item["pan"] = {"left": lp / 100.0, "right": rp / 100.0}
            except ValueError:
                pass
        if self.mode == "auto":
            return item
        return self._write_tracks(item)


class BgmItemRow(ctk.CTkFrame):
    """BGMチャンネルの1曲の編集行(=256)。

    通常のItemRowと違い、内容は**ファイル+アイテム個別パンのみ**
    (Q6=最小構成。重み・区間・変数操作・トラックは対象外)。並べ替えは
    ▲▼ボタン(BGMは再生順が意味を持つが、行ドラッグ機構を持ち込むほどの
    件数にならない想定)。レビューは音声試聴のみ(device_off 指定で
    ItemReviewDialog を流用=252のOFF時と同じ画面)。
    """

    def __init__(self, master, raw_item, base_dir, on_delete, on_move,
                 owner=None):
        super().__init__(master, fg_color=("gray82", "gray24"),
                         corner_radius=8)
        self.base_dir = base_dir
        self.owner = owner
        self.on_delete = on_delete
        self.on_move = on_move
        self.is_video = False    # open_item_review の動画(mpv必須)ガード用
        self.orig = {"audio": raw_item} if isinstance(raw_item, str) \
            else dict(raw_item)
        self.audio_rel = self.orig.get("audio", "")

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=4)
        self.head = head

        # ✕/▲▼/レビュー/パンを**ラベルより先に**pack(=101の作法: 長い
        # ファイル名がラベル幅を食い尽くしても操作類が消えないように)
        del_btn = ctk.CTkButton(
            head, text="✕", width=26, height=26,
            fg_color="transparent", text_color="#e05a5a",
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_delete(self))
        del_btn.pack(side="right", padx=(2, 0))
        self.down_btn = ctk.CTkButton(
            head, text="▼", width=26, height=26,
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_move(self, +1))
        self.down_btn.pack(side="right")
        self.up_btn = ctk.CTkButton(
            head, text="▲", width=26, height=26,
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray85", "gray28"),
            command=lambda: self.on_move(self, -1))
        self.up_btn.pack(side="right", padx=(6, 0))
        self.review_btn = ctk.CTkButton(
            head, text=tr("レビュー"), width=68, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
            command=self._open_review)
        self.review_btn.pack(side="right", padx=(6, 2))

        # パン(アイテム個別・0〜100%)。上書きON時のみL/R欄を出す
        self.pan_fields = ctk.CTkFrame(head, fg_color="transparent")
        self.pan_l_var = tk.StringVar(value="")
        self.pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.pan_fields, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_l_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_l_var,
            font=ctk.CTkFont(size=11))
        self.pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.pan_fields, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_r_entry = ctk.CTkEntry(
            self.pan_fields, width=40, height=24, textvariable=self.pan_r_var,
            font=ctk.CTkFont(size=11))
        self.pan_r_entry.pack(side="left", padx=(2, 0))
        self.pan_var = tk.BooleanVar(value=False)
        self.pan_check = ctk.CTkCheckBox(
            head, text=tr("パン上書き"), variable=self.pan_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_pan_toggle)
        self.pan_check.pack(side="right", padx=(10, 0))

        # ラベルは最後にpack=右側の操作類を必ず残し、余り幅だけを使う
        self.audio_label = ctk.CTkLabel(
            head, text="", font=ctk.CTkFont(size=12), anchor="w")
        self.audio_label.pack(side="left", fill="x", expand=True)

        # ファイル名の省略表示(=101のItemRowと同じ作法)
        self._full_name = self.audio_rel
        self._name_fit_after = None
        self._name_font = tkfont.Font(family=appfont.FAMILY, size=-12)
        self._name_truncated = False
        self.audio_label.configure(text=self._full_name)
        head.bind("<Configure>", lambda _e: self._schedule_name_fit())
        self._name_tooltip = Tooltip(
            self.audio_label,
            lambda: self._full_name if self._name_truncated else "")

        # パンの初期値
        pan = self.orig.get("pan")
        if isinstance(pan, dict):
            self.pan_var.set(True)
            try:
                self.pan_l_var.set(
                    str(int(round(float(pan.get("left", 1.0)) * 100))))
                self.pan_r_var.set(
                    str(int(round(float(pan.get("right", 1.0)) * 100))))
            except (TypeError, ValueError):
                pass
        self._on_pan_toggle()

    # ---- ファイル名の省略表示(ItemRow=101と同じ) ----

    def _schedule_name_fit(self):
        if self._name_fit_after is not None:
            return
        try:
            self._name_fit_after = self.after(60, self._fit_name)
        except Exception:
            self._name_fit_after = None

    def _fit_name(self):
        self._name_fit_after = None
        try:
            avail = self.audio_label.winfo_width() - 6
        except Exception:
            return
        if avail <= 12 or not self._full_name:
            return
        fitted = _ellipsize_middle(self._full_name, self._name_font, avail)
        self._name_truncated = (fitted != self._full_name)
        if self.audio_label.cget("text") != fitted:
            self.audio_label.configure(text=fitted)

    def _on_pan_toggle(self):
        if self.pan_var.get():
            if not self.pan_fields.winfo_manager():
                self.pan_fields.pack(side="right", padx=(4, 0))
            if not self.pan_l_var.get() and not self.pan_r_var.get():
                self.pan_l_var.set("100")
                self.pan_r_var.set("100")
        else:
            self.pan_fields.pack_forget()

    def _open_review(self):
        if self.owner is not None:
            self.owner.open_item_review(self)

    def review_spec(self) -> dict:
        """レビュー画面へ渡す内容(音声試聴のみ=device_off)。"""
        audio = os.path.join(self.base_dir, self.audio_rel) \
            if self.audio_rel else ""
        return {"name": os.path.basename(self.audio_rel or ""),
                "audio": audio, "video": "",
                "audio_lo": 0.0, "audio_hi": None,
                "tracks": [], "warn": [], "device_off": True}

    def collect(self):
        """JSONアイテムへ。パン上書きが無ければ簡潔な文字列書式で返す。"""
        if self.pan_var.get():
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                return {"audio": self.audio_rel,
                        "pan": {"left": lp / 100.0, "right": rp / 100.0}}
            except ValueError:
                pass
        return self.audio_rel


class ChannelSection(ctk.CTkFrame):
    """チャンネル1つ分の編集セクション。

    state_mode=True のとき、終了条件に「無限(移行/終了まで)」が選べる。
    無限は end キーを書かないことで表現する(scenario側の既定挙動)。
    """

    END_CHOICES = (tr("1周で終了"), tr("N周で終了"), tr("N秒で終了"), tr("N回再生で終了"))

    def __init__(self, master, ch_id: str, base_dir: str, owner=None,
                 compact: bool = False):
        super().__init__(master, corner_radius=10, fg_color=BOX_BG,
                         border_width=1, border_color=BOX_BORDER)
        self.ch_id = ch_id
        self.base_dir = base_dir
        self.owner = owner   # ScenarioEditor(変数コンテキスト用)。Noneも可
        self.orig = {}      # 元のチャンネルdict(未対応フィールド保持)
        self.rows: list[ItemRow] = []
        self._pool: list[ItemRow] = []   # 使い回し用に隠した余剰行(破棄しない)
        self.state_mode = False
        # 「無限(移行/終了まで)」を選べるか。state_mode か、または通常イベントで
        # 「指定チャンネル終了」の対象外チャンネル(=対象chが終わるまでループ可)。
        self.allow_infinite = False
        # =63: 終了条件を「無限」だけに絞るか(スクリプト専用chのみのイベント)
        self.infinite_only = False
        self.compact = compact   # True: 幅約320pxに収めるため見出しを縦積み

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 2))

        # compact時は「有効化」の下に「再生方式+終了条件」を1行で置く。
        # (=58: 以前は終了条件をさらに1段下げていたが、編集画面が1360px幅に
        #  なって横に余裕ができたので1行にまとめ、縦を約32px節約する。
        #  アイテム行を見ながら編集できるようにするためのユーザー要望。)
        # 通常時は従来どおり横1列。
        row_a = ctk.CTkFrame(head, fg_color="transparent")
        row_a.pack(fill="x")
        row_b = ctk.CTkFrame(head, fg_color="transparent") if compact else row_a
        row_c = row_b
        if compact:
            row_b.pack(fill="x", pady=(4, 0))

        self.enabled_var = tk.BooleanVar(value=False)
        self.enable_check = ctk.CTkCheckBox(
            row_a, text=tr('チャンネル {0}').format(ch_id), variable=self.enabled_var,
            font=ctk.CTkFont(size=13, weight="bold"),
            checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_toggle,
        )
        self.enable_check.pack(side="left")

        # 詳細(シーク追従/パン/区間/インターバル)トグル。row_a の右端。
        # =68: **枠なしのテキストリンク調**へ(ユーザー要望)。チャンネル見出しに
        # 枠付きボタンが2個×3ch並ぶと、頻度の高い「＋音声」等と同じ強さで
        # 主張して見た目が複雑になっていた。開閉は▾▴を消して**文字色**で示す
        # (開=アクセント色 / 閉=灰。テキストは常に「詳細設定」)。
        self.detail_open = False
        self.detail_btn = ctk.CTkButton(
            row_a, text=tr("詳細設定"), width=64, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=0,
            text_color=TEXT_MUTED, hover_color=("gray85", "gray25"),
            command=self._toggle_detail)
        self.detail_btn.pack(side="right")

        # 他のイベント/ステート/チャンネルから内容を丸ごとコピーしてくるボタン。
        # 押下で owner がモーダルを開く。見た目は「詳細設定」と同じテキスト調。
        self.copy_from_btn = ctk.CTkButton(
            row_a, text=tr("他からコピー"), width=76, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=0,
            text_color=TEXT_MUTED, hover_color=("gray85", "gray25"),
            command=self._on_copy_from)
        self.copy_from_btn.pack(side="right", padx=(0, 2))

        # =76: 「ランダム(重複なし)」追加に伴い幅を広げた(compact 116→165。
        # EN "Random (no repeat)"=130px が矢印込みで収まる幅。row_b には
        # 終了条件も並ぶが1360px幅の編集画面では収まる=スクリーンショットで
        # 確認済み)。
        self.mode_var = tk.StringVar(value=tr("順番に再生"))
        self.mode_menu = CTkOptionMenu(
            row_b, variable=self.mode_var, width=165 if compact else 170,
            height=26,
            values=[tr("順番に再生"), tr("ランダム再生"),
                    tr("ランダム(重複なし)")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_mode_change(),
        )
        self.mode_menu.pack(side="left", padx=(0, 6) if compact else 10)

        self.end_var = tk.StringVar(value=tr("1周で終了"))
        self.end_menu = CTkOptionMenu(
            row_c, variable=self.end_var, width=104 if compact else 110,
            height=26,
            values=list(self.END_CHOICES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_end_ui(),
        )
        self.end_menu.pack(side="left", padx=(0, 4) if compact else 4)
        self.end_field = VarRefField(row_c, width=54, placeholder=tr("値"),
                                     on_change=self._update_end_ui)
        self.end_field.pack(side="left", padx=2)
        self.end_unit_label = ctk.CTkLabel(row_c, text="", font=ctk.CTkFont(size=11),
                                           text_color=TEXT_MUTED)
        self.end_unit_label.pack(side="left")
        # =99: 「N秒で終了」の範囲抽選([min]秒 〜 [max]秒)。当初はコンボの
        # 右に置く案(コンボ幅を狭めて捻出)だったが、実測で EN 表示と
        # 変数トグル(x)/変数メニュー表示時に行へ収まらず(tkのpackは
        # 収まらない末尾ウィジェットを非表示にする)、**コンボの下の専用行**
        # に置く方式へ変更した。この行は「N秒で終了」選択時のみ表示され、
        # 他の終了条件では高さを消費しない。
        self._row_b = row_b
        self.end_range_row = ctk.CTkFrame(head, fg_color="transparent")
        self.end_rmin = VarRefField(self.end_range_row, width=54,
                                    placeholder=tr("min"))
        self.end_rmin.pack(side="left", padx=(2, 0))
        self.end_rmin_unit = ctk.CTkLabel(
            self.end_range_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.end_rmin_unit.pack(side="left")
        ctk.CTkLabel(self.end_range_row, text="〜",
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED).pack(
            side="left", padx=2)
        self.end_rmax = VarRefField(self.end_range_row, width=54,
                                    placeholder=tr("max"))
        self.end_rmax.pack(side="left", padx=(0, 0))
        self.end_rmax_unit = ctk.CTkLabel(
            self.end_range_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.end_rmax_unit.pack(side="left")
        self.end_range_hint = ctk.CTkLabel(
            self.end_range_row, text=tr("(範囲は抽選)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.end_range_hint.pack(side="left", padx=6)

        # ---- 詳細: チャンネル既定パン(上書き) / インターバル ----
        # detail_frame(=チャンネルのパン上書き)は head 内。
        # 「パン詳細」トグルON時のみ表示。既定OFF。
        self.detail_frame = ctk.CTkFrame(head, fg_color="transparent")
        # シークバー追従ラジオ(L/C/Rで1つを選ぶ。変数は owner.seek_var を共有)
        self.seek_radio = None
        seek_var = getattr(owner, "seek_var", None) if owner is not None else None
        if seek_var is not None:
            seek_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
            seek_row.pack(fill="x", pady=(4, 0))
            self.seek_radio = ctk.CTkRadioButton(
                seek_row, text=tr("シークバー追従"), variable=seek_var,
                value=ch_id, font=ctk.CTkFont(size=11),
                radiobutton_width=16, radiobutton_height=16,
                fg_color=ACCENT, hover_color=ACCENT_HOVER)
            self.seek_radio.pack(side="left")
        pan_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        pan_row.pack(fill="x", pady=(4, 0))
        self.pan_override_var = tk.BooleanVar(value=False)
        self.pan_check = ctk.CTkCheckBox(
            pan_row, text=tr("パン上書き(L/R %)"), variable=self.pan_override_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_ch_pan_toggle)
        self.pan_check.pack(side="left")
        self.pan_entry_box = ctk.CTkFrame(pan_row, fg_color="transparent")
        self.pan_l_var = tk.StringVar(value="")
        self.pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.pan_entry_box, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_l_entry = ctk.CTkEntry(
            self.pan_entry_box, width=42, height=24, textvariable=self.pan_l_var,
            font=ctk.CTkFont(size=11))
        self.pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.pan_entry_box, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.pan_r_entry = ctk.CTkEntry(
            self.pan_entry_box, width=42, height=24, textvariable=self.pan_r_var,
            font=ctk.CTkFont(size=11))
        self.pan_r_entry.pack(side="left", padx=(2, 0))

        # =64: インターバルも「詳細設定」の中へ移動(ユーザー要望)。
        # detail_frame の中に置くので、トグルONのときだけ現れる。
        iv_row = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        iv_row.pack(fill="x", pady=(4, 0))
        self.iv_row = iv_row
        self.iv_var = tk.BooleanVar(value=False)
        self.iv_check = ctk.CTkCheckBox(
            iv_row, text=tr("インターバル"), variable=self.iv_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_iv_toggle)
        self.iv_check.pack(side="left")
        # =67: 「下限[__]〜[__]上限」→「[__]秒 〜 [__]秒」(単位を各欄に添える)。
        # チェックのラベルから「(秒)」を外せるので、compact幅にも余裕が出る。
        self.iv_box = ctk.CTkFrame(iv_row, fg_color="transparent")
        self.iv_min_var = tk.StringVar(value="")
        self.iv_max_var = tk.StringVar(value="")
        self.iv_min_entry = ctk.CTkEntry(
            self.iv_box, width=48, height=24, textvariable=self.iv_min_var,
            font=ctk.CTkFont(size=11))
        self.iv_min_entry.pack(side="left", padx=(2, 2))
        ctk.CTkLabel(self.iv_box, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(self.iv_box, text="〜", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(4, 4))
        self.iv_max_entry = ctk.CTkEntry(
            self.iv_box, width=48, height=24, textvariable=self.iv_max_var,
            font=ctk.CTkFont(size=11))
        self.iv_max_entry.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(self.iv_box, text=tr("秒"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=10, pady=(0, 8))

        # height=0 にしないと、音声0件の空フレームが CTkFrame 既定の200pxを
        # 確保してしまい、空チャンネルだけ縦に間延びする(中身が入れば追従)。
        self.items_frame = ctk.CTkFrame(self.body, fg_color="transparent",
                                        height=0)
        self.items_frame.pack(fill="x")

        # =58: 表示名を「＋音声」「＋動画」「＋スクリプト」へ短縮し、
        # この順に並べる(ユーザー決定)。3チャンネル分が横に並ぶため、
        # 短いラベルの方が枠内に収まりやすい。
        btn_row = ctk.CTkFrame(self.body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(6, 0))
        self.add_audio_btn = ctk.CTkButton(
            btn_row, text=tr("＋音声"), width=84, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_items,
        )
        self.add_audio_btn.pack(side="left")
        # 動画アイテムの追加(=52 フェーズ3-B)。動画はチャンネルの中身に
        # なったので、音声と同じ「＋追加」で並べられる。音声/スクリプトとの
        # 混在は禁止のため _update_add_buttons が相互排他する。
        # 動画を置けるチャンネルは1つだけ(owner が他chを無効化する)。
        self.add_video_btn = ctk.CTkButton(
            btn_row, text=tr("＋動画"), width=84, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_videos,
        )
        self.add_video_btn.pack(side="left", padx=(6, 0))
        # スクリプトのみアイテムの追加(スクリプト専用チャンネル用)。
        # 音声アイテムとの混在は禁止のため、_update_add_buttons が相互排他する。
        self.add_script_btn = ctk.CTkButton(
            btn_row, text=tr("＋スクリプト"), width=104, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_scripts,
        )
        self.add_script_btn.pack(side="left", padx=(6, 0))

        # =160: **D&Dできることの控えめな告知**(ユーザー決定)。
        # 音声/動画/funscript/CSV は =44/=52/=65 からドラッグ&ドロップで
        # 追加できるが、画面上に手がかりが無く**知らないと気づけない**
        # ままだった。追加ボタンの並びの右に、薄い小さな文字で「(D&D可)」と
        # だけ置く。**行そのものは既にあるので縦の高さは増えず**、
        # レイアウトは動かない。
        # ・幅が足りないときに削られるのは最後に置いたこのラベル
        #   (packの取り分は後ろから削られる=159)。ボタンは無傷で残る。
        # ・tkinterdnd2 が無い環境ではD&D自体が効かないので**出さない**
        #   (嘘の案内にしない)。owner の `_dnd_ok` を見るが、__init__ での
        #   D&D初期化は _build_ui より**後**なので、最初のパネルの分は
        #   ScenarioEditor 側の `_refresh_dnd_hints()` が後から出す。
        self.dnd_hint = ctk.CTkLabel(
            btn_row, text=tr("(D&D可)"), font=ctk.CTkFont(size=11),
            text_color=MUTED, anchor="w",
        )
        self.set_dnd_hint(bool(getattr(self.owner, "_dnd_ok", False)))

        self._update_end_ui()
        self._update_enabled()
        self._update_add_buttons()

    def set_dnd_hint(self, on: bool):
        """「(D&D可)」の表示/非表示(=160)。D&Dが使える環境でだけ出す。"""
        try:
            if on:
                if not self.dnd_hint.winfo_ismapped():
                    self.dnd_hint.pack(side="left", padx=(10, 0))
            else:
                self.dnd_hint.pack_forget()
        except Exception:
            pass

    # ---- 詳細(パン/インターバル) ----

    def _toggle_detail(self):
        self._set_detail(not self.detail_open)

    def _on_copy_from(self):
        """「他からコピー」= 他のイベント/ステート/チャンネルの内容を取り込む。"""
        if self.owner is not None:
            self.owner._open_channel_copy(self.ch_id)

    def _set_detail(self, flag: bool):
        """詳細設定の開閉。シーク追従・パン上書き・区間・インターバル(=64)を
        チャンネルと各アイテム行でまとめて出し入れする。"""
        self.detail_open = bool(flag)
        if self.detail_open:
            # シークバー追従・パン上書き・区間・インターバル(=64)をまとめて出す
            self.detail_frame.pack(fill="x", pady=(2, 0))
            # =68: 開閉は文字色で示す(▾▴の記号は廃止=ユーザー要望)
            self.detail_btn.configure(text_color=ACCENT_TEXT)
        else:
            self.detail_frame.pack_forget()
            self.detail_btn.configure(text_color=TEXT_MUTED)
        for r in self.rows:
            r.set_detail(self.detail_open)

    def _default_pan_pct(self) -> tuple[int, int]:
        """このチャンネルのデフォルトパン(%)。"""
        l, r = DEFAULT_PAN.get(self.ch_id, (1.0, 1.0))
        return int(round(l * 100)), int(round(r * 100))

    def _effective_pan_pct(self) -> tuple[int, int]:
        """行のpan上書き初期値に使う、chの実効パン(%)。上書きON時はその値。"""
        if self.pan_override_var.get():
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                return lp, rp
            except ValueError:
                pass
        return self._default_pan_pct()

    def _on_ch_pan_toggle(self):
        if self.pan_override_var.get():
            dl, dr = self._default_pan_pct()
            if not self.pan_l_var.get():
                self.pan_l_var.set(str(dl))
            if not self.pan_r_var.get():
                self.pan_r_var.set(str(dr))
            self.pan_entry_box.pack(side="left", padx=(6, 0))
        else:
            self.pan_entry_box.pack_forget()
        self._apply_row_correlations()

    def _on_iv_toggle(self):
        if self.iv_var.get():
            if not self.iv_min_var.get():
                self.iv_min_var.set("0.0")
            if not self.iv_max_var.get():
                self.iv_max_var.set("0.0")
            self.iv_box.pack(side="left", padx=(6, 0))
        else:
            self.iv_box.pack_forget()

    def _sync_detail_visibility(self):
        """var の状態に合わせて入力欄の表示だけ更新する(値は変えない)。"""
        if self.pan_override_var.get():
            self.pan_entry_box.pack(side="left", padx=(6, 0))
        else:
            self.pan_entry_box.pack_forget()
        if self.iv_var.get():
            self.iv_box.pack(side="left", padx=(6, 0))
        else:
            self.iv_box.pack_forget()

    def _apply_row_correlations(self):
        """各音声行へ相関情報(weight表示可否・pan詳細の開閉・pan上書き初期値)を伝える。"""
        is_rand = self._is_random()
        dl, dr = self._effective_pan_pct()
        for r in self.rows:
            r.set_random(is_rand)
            r.set_channel_default_pan(dl, dr)
            r.set_detail(self.detail_open)

    @staticmethod
    def _parse_interval_secs(raw) -> tuple[float, float]:
        """interval指定(秒 or {min,max})を (min, max) 秒で返す。未指定は(0,0)。"""
        if raw is None:
            return 0.0, 0.0
        if isinstance(raw, (int, float)):
            return float(raw), float(raw)
        if isinstance(raw, dict):
            try:
                mn = float(raw.get("min", 0) or 0)
                mx = float(raw.get("max", raw.get("min", 0)) or 0)
                return mn, mx
            except (TypeError, ValueError):
                return 0.0, 0.0
        return 0.0, 0.0

    # ---- 読み込み/書き出し ----

    def _is_random(self) -> bool:
        # =76: 「ランダム(重複なし)」もランダム扱い(終了条件の相関・
        # 重み欄の表示可否はランダム再生と同じ。=98: 終了条件のみ
        # 「N周で終了」が追加で選べる=_is_bag)。
        return self.mode_var.get() in (tr("ランダム再生"),
                                       tr("ランダム(重複なし)"))

    def _is_bag(self) -> bool:
        # =98: 「ランダム(重複なし)」だけの相関(N周=袋N回で終了)。
        return self.mode_var.get() == tr("ランダム(重複なし)")

    def _default_finite_end(self) -> str:
        """無限が使えなくなった時の既定終了条件(モードに合う有効な値)。"""
        # ランダム再生は「1周で終了」「N周で終了」が使えないため「N秒で終了」を既定に
        return tr("N秒で終了") if self._is_random() else tr("1周で終了")

    def _end_menu_values(self) -> list:
        """相関制御: 再生方式と無限可否に応じた終了条件の選択肢。

        - ランダム再生: 「1周で終了」「N周で終了」は無効(リストを順に回る概念が
          無い)ので外し、「N秒で終了」「N回再生で終了」のみ。
        - ランダム(重複なし): 上に加えて「N周で終了」も可(=98。袋方式では
          1周=袋を1回使い切る、が明確に定義できるため)。
        - 順番に再生: 「N秒で終了」「N回再生で終了」は無効(順番再生はリストの
          周回=1周/N周で終了を表す)ので外し、「1周で終了」「N周で終了」のみ。
        無限は state_mode か allow_infinite のときだけ出す。
        """
        if self._is_random():
            vals = [tr("N秒で終了"), tr("N回再生で終了")]
            if self._is_bag():
                vals.append(tr("N周で終了"))
        else:
            vals = [tr("1周で終了"), tr("N周で終了")]
        if self.state_mode or self.allow_infinite:
            vals.append(INFINITE_CHOICE)
            if getattr(self, "infinite_only", False):
                # =63: スクリプト専用chだけのイベントで終了条件が
                # 「合計時間/変数条件」のときは無限のみ(有限だとスクリプトが
                # 終わった時点でイベントも終わってしまい紛らわしいため)
                return [INFINITE_CHOICE]
        return vals

    def _refresh_end_menu(self):
        """終了条件メニューの選択肢を現在の相関(方式/無限可否)に合わせて更新。

        現在の選択が選択肢から外れたら、モードに合う既定値へ寄せる。
        (変数指定でロック中は触らない)
        """
        vals = self._end_menu_values()
        self.end_menu.configure(values=vals)
        if getattr(self, "end_locked", False):
            return
        # 変数指定の終了条件(N分/N回等)はJSONから読み込んだ既存のバインドを
        # 壊さないよう、相関で選択肢外になっても保持する(新規選択のみ制限)。
        if getattr(self.end_field, "use_var", False):
            return
        if self.end_var.get() not in vals:
            self.end_var.set(INFINITE_CHOICE if vals == [INFINITE_CHOICE]
                             else self._default_finite_end())
            self._update_end_ui()

    def _on_mode_change(self):
        """再生方式が変わったとき、終了条件メニューと行の相関を更新する。"""
        self._refresh_end_menu()
        # =286: 方式を変えたら終了条件をその方式の既定へ(順番=1周で終了、
        # ランダム系=N秒で終了)。「無限」のままにしない(ユーザー依頼)。
        # 変数指定でロック中/変数参照中は従来どおり触らない。
        if not getattr(self, "end_locked", False) \
                and not getattr(self.end_field, "use_var", False) \
                and self.end_var.get() == INFINITE_CHOICE \
                and self._default_finite_end() in self._end_menu_values():
            self.end_var.set(self._default_finite_end())
            self._update_end_ui()
        self._apply_row_correlations()   # weight表示可否を各行へ反映

    def set_state_mode(self, flag: bool, allow_infinite: bool | None = None):
        """ステート内チャンネルか(終了条件に「無限」を追加)。

        allow_infinite を明示すると、通常イベントでも「無限」を選べる
        (指定チャンネル終了の対象外ch用)。None なら従来どおり state_mode に従う。
        """
        self.state_mode = flag
        if allow_infinite is not None:
            self.allow_infinite = allow_infinite
        self._refresh_end_menu()

    def set_allow_infinite(self, flag: bool):
        """通常イベントでの「無限」選択可否を切り替える(指定ch終了の対象外ch)。"""
        if flag == self.allow_infinite:
            return
        self.allow_infinite = flag
        self._refresh_end_menu()

    def set_infinite_only(self, flag: bool):
        """終了条件を「無限」だけに絞る(=63・スクリプト専用chのみのイベント)。"""
        flag = bool(flag)
        if flag == getattr(self, "infinite_only", False):
            return
        self.infinite_only = flag
        self._refresh_end_menu()

    def is_script_only(self) -> bool:
        """このチャンネルがスクリプト専用ch(音声も動画も無い)か(=63)。"""
        if not self.rows:
            return False
        return all(getattr(r, "is_script", False) for r in self.rows)

    def _numeric_names(self) -> list[str]:
        return self.owner._numeric_var_names() if self.owner else []

    def load(self, ch_raw: dict | None, state_mode: bool = False,
             allow_infinite: bool = False):
        self.set_state_mode(state_mode, allow_infinite)
        inf_ok = state_mode or allow_infinite
        # 音声行(ItemRow)は destroy せず _set_rows で使い回す(生成コスト削減)
        self.end_locked = False
        self.end_field.set_names(self._numeric_names())
        self.end_rmin.set_names(self._numeric_names())
        self.end_rmax.set_names(self._numeric_names())
        if ch_raw is None:
            self.orig = {}
            self.enabled_var.set(False)
            self.mode_var.set(tr("順番に再生"))
            # =286: 新しく有効にするchの既定は「1周で終了」(ステート内でも。
            # 従来は無限が許される文脈なら「無限」だった=ユーザー依頼で変更)
            self.end_var.set(tr("1周で終了"))
            self.end_field.set("")
            self.end_rmin.set("")
            self.end_rmax.set("")
        else:
            self.orig = dict(ch_raw)
            self.enabled_var.set(True)
            raw_mode = ch_raw.get("mode")
            self.mode_var.set(
                tr("ランダム再生") if raw_mode == "random"
                else tr("ランダム(重複なし)") if raw_mode == "random_bag"
                else tr("順番に再生"))
            end = ch_raw.get("end")
            etype = (end or {}).get("type")
            # 時間指定は秒のみ(minutesは廃止=読まない)
            has_ref = isinstance(end, dict) and _has_varref(
                end.get("count"), end.get("seconds"))
            self.end_rmin.set("")
            self.end_rmax.set("")
            if has_ref and etype == "duration" and isinstance(end.get("seconds"), dict):
                self.end_var.set(tr("N秒で終了"))
                self.end_rmin.set(end.get("seconds"))
                self.end_rmax.set(end.get("seconds"))   # min==max(単一値)
            elif has_ref and etype in ("repeat", "plays"):   # count が変数
                self.end_var.set(tr("N周で終了") if etype == "repeat"
                                 else tr("N回再生で終了"))
                self.end_field.set(end.get("count"))
            elif end is None and inf_ok:
                # 無限が許可される文脈(ステート内 or 指定ch終了の対象外ch)で
                # end 省略 = 移行/イベント終了まで無限
                self.end_var.set(INFINITE_CHOICE)
                self.end_field.set("")
            elif etype == "none" and inf_ok:
                self.end_var.set(INFINITE_CHOICE)
                self.end_field.set("")
            elif etype == "repeat":
                self.end_var.set(tr("N周で終了"))
                self.end_field.set(str(end.get("count", 1)))
            elif etype == "plays":
                self.end_var.set(tr("N回再生で終了"))
                self.end_field.set(str(end.get("count", 1)))
            elif etype == "duration":
                # 秒のみ。旧minutesは無視するので、seconds無し=空欄(再入力を促す)
                # =99: 範囲(min_seconds/max_seconds)は2欄へ、単一 seconds は
                # min==max として両欄に同じ値を入れる。各値は定数 or 変数参照
                self.end_var.set(tr("N秒で終了"))
                if "min_seconds" in end or "max_seconds" in end:
                    lo = end.get("min_seconds", 0)
                    hi = end.get("max_seconds", lo)
                else:
                    lo = hi = end.get("seconds")
                def _dsec(v):
                    if isinstance(v, dict):
                        return v
                    return f"{float(v):g}" if isinstance(v, (int, float)) else ""
                self.end_rmin.set(_dsec(lo))
                self.end_rmax.set(_dsec(hi))
            else:
                self.end_var.set(tr("1周で終了"))
                self.end_field.set("")
        # 詳細: パン上書き / インターバルを反映
        pan = (ch_raw or {}).get("pan")
        if isinstance(pan, dict) and "left" in pan and "right" in pan:
            self.pan_override_var.set(True)
            self.pan_l_var.set(str(int(round(float(pan["left"]) * 100))))
            self.pan_r_var.set(str(int(round(float(pan["right"]) * 100))))
        else:
            self.pan_override_var.set(False)
            self.pan_l_var.set("")
            self.pan_r_var.set("")
        iv_min, iv_max = self._parse_interval_secs((ch_raw or {}).get("interval"))
        if iv_max > 0 or iv_min > 0:
            self.iv_var.set(True)
            self.iv_min_var.set(f"{iv_min:g}")
            self.iv_max_var.set(f"{iv_max:g}")
        else:
            self.iv_var.set(False)
            self.iv_min_var.set("")
            self.iv_max_var.set("")
        # 「パン詳細」は既定OFF(データにpan上書きがあっても畳んだまま。
        #  値は保持され、パン詳細を開けば表示される)。現在の開閉状態は維持する。
        self._set_detail(self.detail_open)
        self._sync_detail_visibility()
        # 音声行を使い回して設定(件数差分だけ生成/破棄)
        self._set_rows([] if ch_raw is None else ch_raw.get("items", []))
        self._refit_items()   # 0件なら枠を縮める(空チャンネルの間延び防止)
        # 各行へ相関(weight表示可否・pan上書き初期値)を反映
        self._apply_row_correlations()
        # 音声/スクリプト追加ボタンの相互排他(混在禁止)を反映
        self._update_add_buttons()
        # 読み込んだ再生方式に合わせて終了条件メニューの選択肢を相関更新
        self._refresh_end_menu()
        self._update_end_ui()
        self._update_enabled()
        if self.end_locked:
            self.end_menu.configure(state="disabled")
            self.end_field.set_state("disabled")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")
        else:
            self.end_menu.configure(state="normal")

    def collect(self) -> dict | None:
        """有効なら編集内容をチャンネルdictへ、無効ならNone。"""
        if not self.enabled_var.get():
            return None
        ch = dict(self.orig)
        m = self.mode_var.get()
        ch["mode"] = ("random" if m == tr("ランダム再生")
                      else "random_bag" if m == tr("ランダム(重複なし)")
                      else "sequential")
        self._collect_pan_interval(ch)
        choice = self.end_var.get()
        if getattr(self, "end_locked", False):
            # UIで表せない変数指定の終了条件は元のendをそのまま保持する
            ch["items"] = [r.collect() for r in self.rows]
            return ch
        ch.pop("end", None)
        use_var = self.end_field.use_var
        raw = self.end_field.get_raw()
        if choice == tr("N周で終了"):
            ch["end"] = {"type": "repeat",
                         "count": raw if use_var else int(float(raw or 1))}
        elif choice == tr("N回再生で終了"):
            ch["end"] = {"type": "plays",
                         "count": raw if use_var else int(float(raw or 1))}
        elif choice == tr("N秒で終了"):
            # 時間指定は秒のみ(minutesは廃止)。=99: min==max は単一 seconds、
            # 異なれば範囲 min_seconds/max_seconds で保存(イベント終了と同じ)
            lo_var, hi_var = self.end_rmin.use_var, self.end_rmax.use_var
            lo = (self.end_rmin.get_raw() if lo_var
                  else float(self.end_rmin.get_text() or 0))
            if hi_var:
                hi = self.end_rmax.get_raw()
            elif self.end_rmax.get_text().strip():
                hi = float(self.end_rmax.get_text())
            else:
                hi = lo          # max空欄=minと同じ(minが変数なら同じ変数)
                hi_var = lo_var
            same = (lo_var == hi_var) and (
                (lo_var and lo.get("var") == hi.get("var"))
                or (not lo_var and lo == hi))
            if same:
                ch["end"] = {"type": "duration", "seconds": lo}
            else:
                if not lo_var and not hi_var and hi < lo:
                    lo, hi = hi, lo
                ch["end"] = {"type": "duration",
                             "min_seconds": lo, "max_seconds": hi}
        elif choice == INFINITE_CHOICE:
            # **通常イベントでは end を省略すると「1周で終了」(sequential の
            # 既定)になってしまう**ため、無限は必ず {"type":"none"} を明示する
            # (=55の不具合修正。ステート内は省略でも無限だが、読んだときに
            #  意図が分かるよう同じく明示する)。
            ch["end"] = {"type": "none"}
        else:
            ch["end"] = {"type": "once"}
        ch["items"] = [r.collect() for r in self.rows]
        return ch

    def _collect_pan_interval(self, ch: dict):
        """チャンネル既定パン / インターバルを ch dict へ書く(未使用ならキー削除)。"""
        ch.pop("pan", None)
        if self.pan_override_var.get():
            try:
                lp = max(0, min(100, int(float(self.pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.pan_r_var.get() or 0))))
                ch["pan"] = {"left": lp / 100.0, "right": rp / 100.0}
            except ValueError:
                pass
        ch.pop("interval", None)
        if self.iv_var.get():
            try:
                mn = round(float(self.iv_min_var.get() or 0), 1)
                mx = round(float(self.iv_max_var.get() or 0), 1)
                if mx > 0 or mn > 0:
                    ch["interval"] = mn if mn == mx else {"min": mn, "max": mx}
            except ValueError:
                pass

    def _mark(self, widget, kind="error"):
        """検証エラーの原因ウィジェットを owner(編集画面)へ記録する。"""
        if self.owner is not None:
            self.owner._want_mark(widget, kind)

    def validate(self) -> str | None:
        if not self.enabled_var.get():
            return None
        if not self.rows:
            self._mark(self.enable_check, "error")
            return tr('チャンネル{0}: 音声が1つもありません').format(self.ch_id)
        for r in self.rows:
            err = r.validate()
            if err:
                return tr('チャンネル{0}: {1}').format(self.ch_id, err)
        choice = self.end_var.get()
        if choice == tr("N秒で終了") \
                and not getattr(self, "end_locked", False):
            # =99: min秒〜max秒の範囲(max空欄=min)。検証規則はイベント終了の
            # 範囲(evend)と同じ: 各欄は正の数 or 変数、min≤0はminのみ0可
            lo_var, hi_var = self.end_rmin.use_var, self.end_rmax.use_var
            try:
                if lo_var:
                    if not self.end_rmin.get_raw().get("var"):
                        raise ValueError
                    lo = None
                else:
                    lo = float(self.end_rmin.get_text())
                if hi_var:
                    if not self.end_rmax.get_raw().get("var"):
                        raise ValueError
                    hi = None
                elif self.end_rmax.get_text().strip():
                    hi = float(self.end_rmax.get_text())
                else:
                    hi = lo          # max空欄=minと同じ
                    hi_var = lo_var
            except ValueError:
                self._mark(self.end_rmin, "error")
                self._mark(self.end_rmax, "error")
                return tr('チャンネル{0}: 終了条件の値(min/max)が不正です').format(self.ch_id)
            if not lo_var and not hi_var:
                l, h = (lo, hi) if hi >= lo else (hi, lo)
                if l < 0 or h <= 0:
                    self._mark(self.end_rmin, "error")
                    self._mark(self.end_rmax, "error")
                    return tr('チャンネル{0}: 終了条件の秒数は0以上(最大は正の数)にしてください').format(self.ch_id)
        elif choice in (tr("N周で終了"), tr("N回再生で終了")) \
                and not getattr(self, "end_locked", False):
            if self.end_field.use_var:
                if not self.end_field.get_raw().get("var"):
                    self._mark(self.end_field, "error")
                    return tr('チャンネル{0}: 終了条件の変数を選択してください').format(self.ch_id)
            else:
                try:
                    v = float(self.end_field.get_text())
                    if v <= 0:
                        raise ValueError
                except ValueError:
                    self._mark(self.end_field, "error")
                    return tr('チャンネル{0}: 終了条件の値が不正です').format(self.ch_id)
        if self._is_bag():
            # =98: ランダム(重複なし)は「N周で終了」(袋N回)も可
            if self.state_mode or self.allow_infinite:
                if choice not in (tr("N秒で終了"), tr("N回再生で終了"),
                                  tr("N周で終了"), INFINITE_CHOICE):
                    self._mark(self.end_menu, "error")
                    return (tr('チャンネル{0}: ランダム(重複なし)の終了条件は「N秒で終了」「N回再生で終了」「N周で終了」「{1}」のいずれかにしてください').format(self.ch_id, INFINITE_CHOICE))
            elif choice not in (tr("N秒で終了"), tr("N回再生で終了"),
                                tr("N周で終了")):
                self._mark(self.end_menu, "error")
                return (tr('チャンネル{0}: ランダム(重複なし)の終了条件は「N秒で終了」「N回再生で終了」「N周で終了」のいずれかにしてください').format(self.ch_id))
        elif self._is_random():
            if self.state_mode or self.allow_infinite:
                if choice not in (tr("N秒で終了"), tr("N回再生で終了"), INFINITE_CHOICE):
                    self._mark(self.end_menu, "error")
                    return (tr('チャンネル{0}: ランダム再生の終了条件は「N秒で終了」「N回再生で終了」「{1}」のいずれかにしてください').format(self.ch_id, INFINITE_CHOICE))
            elif choice not in (tr("N秒で終了"), tr("N回再生で終了")):
                self._mark(self.end_menu, "error")
                return (tr('チャンネル{0}: ランダム再生の終了条件は「N秒で終了」か「N回再生で終了」にしてください').format(self.ch_id))
        elif not getattr(self, "end_locked", False) and not self.end_field.use_var:
            # 順番に再生(変数指定は既存バインド保持のため対象外)
            if self.state_mode or self.allow_infinite:
                if choice not in (tr("1周で終了"), tr("N周で終了"), INFINITE_CHOICE):
                    self._mark(self.end_menu, "error")
                    return (tr('チャンネル{0}: 順番に再生の終了条件は「1周で終了」「N周で終了」「{1}」のいずれかにしてください').format(self.ch_id, INFINITE_CHOICE))
            elif choice not in (tr("1周で終了"), tr("N周で終了")):
                self._mark(self.end_menu, "error")
                return (tr('チャンネル{0}: 順番に再生の終了条件は「1周で終了」か「N周で終了」にしてください').format(self.ch_id))
        # 詳細: パン上書き(0〜100) / インターバル(下限≤上限、0以上)
        if self.pan_override_var.get():
            for var, w in ((self.pan_l_var, self.pan_l_entry),
                           (self.pan_r_var, self.pan_r_entry)):
                try:
                    v = int(float(var.get()))
                    if not (0 <= v <= 100):
                        raise ValueError
                except ValueError:
                    self._mark(w, "error")
                    return tr('チャンネル{0}: パンは0〜100で指定してください').format(self.ch_id)
        if self.iv_var.get():
            try:
                mn = float(self.iv_min_var.get() or 0)
                mx = float(self.iv_max_var.get() or 0)
                if mn < 0 or mx < 0 or mx < mn:
                    raise ValueError
            except ValueError:
                self._mark(self.iv_min_entry, "error")
                return tr('チャンネル{0}: インターバルは「下限≤上限」かつ0以上で指定してください').format(self.ch_id)
        return None

    # ---- 内部 ----

    def _refit_items(self):
        """音声が0件のとき items_frame を潰す。

        空になった直後のフレームは pack_propagate では縮まず(最後のサイズを
        保持する)、音声のないチャンネルだけ枠が縦に伸びたままになる。
        件数に応じて propagate を切り替え、0件なら高さ1に固定して縮める。
        """
        if self.rows:
            self.items_frame.pack_propagate(True)
        else:
            self.items_frame.pack_propagate(False)
            self.items_frame.configure(height=1)

    def _set_rows(self, items_raw):
        """音声行を件数に合わせて設定する。ItemRow を使い回し、余りは破棄せず
        プールへ隠す(CTkウィジェット生成が重いため、切替のたびに作り直さない)。"""
        items_raw = list(items_raw)
        for i, raw in enumerate(items_raw):
            if i < len(self.rows):
                self.rows[i].reload(raw)          # 表示中の行を使い回す
            elif self._pool:
                row = self._pool.pop()            # プールの隠し行を復帰
                row.reload(raw)
                self.items_frame.pack_propagate(True)
                row.pack(fill="x", pady=2)
                self.rows.append(row)
            else:
                self._append_row(raw)             # 足りなければ新規生成
        while len(self.rows) > len(items_raw):    # 余りはプールへ退避(隠す)
            row = self.rows.pop()
            row.pack_forget()
            self._pool.append(row)

    def _append_row(self, raw_item):
        # 追加時は中身に追従して伸びるよう propagate を戻す
        self.items_frame.pack_propagate(True)
        row = ItemRow(self.items_frame, raw_item, self.base_dir,
                      self._delete_row, owner=self.owner, compact=self.compact)
        row.reorder_host = self   # =282: 並び替えD&Dの受け口
        row.pack(fill="x", pady=2)
        self.rows.append(row)

    # ---- 並び替え D&D(=282) ----
    # ItemRow の取っ手/ファイル名を縦ドラッグ → ポインタが隣の行の中心を
    # 越えたら即座に入れ替える(挿入線を別に描かず、行そのものが動く)。
    # rows の順序が JSON の items の順序になる(collect はそのまま)。

    def begin_row_drag(self, row):
        row.set_dragging(True)

    def drag_row_motion(self, row, y_root: int):
        if row not in self.rows:
            return
        others = [r for r in self.rows if r is not row]
        cur = self.rows.index(row)
        new = 0
        for r in others:
            try:
                mid = r.winfo_rooty() + r.winfo_height() / 2
            except Exception:
                continue
            if y_root > mid:
                new += 1
        if new != cur:
            self.move_row(cur, new)

    def end_row_drag(self, row):
        row.set_dragging(False)

    def move_row(self, src: int, dst: int):
        """rows[src] を dst の位置へ移し、表示(pack順)も追従させる。"""
        if src == dst or not (0 <= src < len(self.rows)) \
                or not (0 <= dst < len(self.rows)):
            return
        row = self.rows.pop(src)
        self.rows.insert(dst, row)
        row.pack_forget()
        if dst + 1 < len(self.rows):
            row.pack(fill="x", pady=2, before=self.rows[dst + 1])
        else:
            row.pack(fill="x", pady=2)

    def _delete_row(self, row):
        was_video = getattr(row, "is_video", False)
        self.rows.remove(row)
        row.destroy()
        self._refit_items()
        self._update_add_buttons()
        if was_video and not self.has_video():
            # 動画chでなくなったので他chの相関(動画ロック・device担当)を戻す
            self._notify_video_changed()

    def _add_items(self):
        paths = filedialog.askopenfilenames(
            title=tr("音声ファイルを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("音声"), "*.wav *.mp3"), (tr("すべて"), "*.*")],
            parent=self,
        )
        if paths:
            _remember_dialog_dir(paths[0])
        for p in paths:
            rel = _safe_relpath(p, self.base_dir)   # 別ドライブでも落ちない
            self._append_row(rel)
        if paths:
            self._apply_row_correlations()   # 新規行へ相関(weight/pan)を反映
            self._update_add_buttons()
        if paths and not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
            # =40の相関制御(音声なし絞り込み)にも追従させる
            if self.owner is not None \
                    and hasattr(self.owner, "_on_channel_enabled"):
                self.owner._on_channel_enabled()

    def add_dropped_audio(self, paths):
        """D&Dで落とされた音声ファイル群をこのチャンネルへ登録する(=44)。

        対応拡張子(.wav/.mp3)以外・実在しないファイルは黙って無視(仕様=
        失敗時は画面上何も起こさない)。無効チャンネルは自動で有効化する
        (ユーザー確認済み)。パスの相対化・相関反映は「＋音声」と同じ。
        スクリプト専用ch・動画ch(混在禁止)への音声D&Dも黙って無視する。
        """
        if any(getattr(r, "is_script", False) or getattr(r, "is_video", False)
               for r in self.rows):
            return
        ok = [p for p in paths
              if os.path.isfile(p)
              and os.path.splitext(p)[1].lower() in (".wav", ".mp3")]
        if not ok:
            return
        for p in ok:
            self._append_row(_safe_relpath(p, self.base_dir))
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
            if self.owner is not None \
                    and hasattr(self.owner, "_on_channel_enabled"):
                self.owner._on_channel_enabled()

    def _add_scripts(self):
        """スクリプトのみアイテム(funscript/CSV)を追加する。

        スクリプト専用チャンネル(音声を持たず、デバイス担当分のスクリプトを
        独立したリズムで実行するチャンネル)を作るための追加ボタン。種別は
        ファイル名タグ(=44のD&Dと同じ規則: ufo/a10/vib、タグ無しfunscript=
        linear、タグ無しcsv=rotate_ufo)で初期判定し、行内で変更できる。
        """
        paths = filedialog.askopenfilenames(
            title=tr("スクリプトファイルを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("funscript / CSV"), "*.funscript *.csv"),
                       ("funscript", "*.funscript"), ("CSV", "*.csv"),
                       (tr("すべて"), "*.*")],
            parent=self,
        )
        if paths:
            _remember_dialog_dir(paths[0])
        added = False
        for p in paths:
            ttype = _script_track_type(p)
            if ttype is None:
                continue
            rel = _safe_relpath(p, self.base_dir)
            self._append_row({"tracks": [{"type": ttype, "funscript": rel}]})
            added = True
        if not added:
            return
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        # =40の相関制御(音声なし絞り込み)と =63(スクリプトのみイベントの
        # 終了条件絞り込み)へ追従させる
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()

    def add_dropped_script(self, paths) -> bool:
        """D&Dで落とされた funscript/CSV をスクリプトのみアイテムとして登録(=65)。

        =46の既知の小制約「チャンネル枠へのfunscript D&D未対応」の解消。
        アイテム枠の**外**(チャンネル枠)へ落としたときの動作で、「＋スクリプト」
        と同じ規則(ファイル名タグで種別判定・1ファイル=1アイテム)で追加する
        (ユーザー決定 2026-07-27)。アイテム枠の上へ落としたときは従来どおり
        そのアイテムのトラックになる(ItemRow.bind_dropped_fs)。

        音声/動画のアイテムがあるチャンネル(=52の混在禁止)・実在しない
        ファイル・対象外拡張子は黙って無視する(=44の仕様=失敗時は画面上
        何も起こさない)。戻り値: 1件でも登録したか。
        """
        if not bool(getattr(self.owner, "device_enabled", True)):
            return False    # =252: デバイス連動OFFでは追加しない
        if any(r.audio_rel or getattr(r, "is_video", False)
               for r in self.rows):
            return False
        added = False
        for p in paths:
            if not os.path.isfile(p):
                continue
            ttype = _script_track_type(p)
            if ttype is None:
                continue
            rel = _safe_relpath(p, self.base_dir)
            self._append_row({"tracks": [{"type": ttype, "funscript": rel}]})
            added = True
        if not added:
            return False
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        # =40 / =63 の相関制御へ追従させる(「＋スクリプト」と同じ)
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()
        return True

    def _add_videos(self):
        """動画アイテムを追加する(=52)。複数選べば抽選/プレイリストになる。"""
        paths = filedialog.askopenfilenames(
            title=tr("動画ファイルを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=list(VIDEO_FILETYPES),
            parent=self,
        )
        if not paths:
            return
        _remember_dialog_dir(paths[0])
        for p in paths:
            self._append_row({"video": _safe_relpath(p, self.base_dir)})
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        self._notify_video_changed()

    def add_dropped_video(self, paths) -> bool:
        """D&Dで落とされた動画ファイル群をこのチャンネルへ登録する(=52)。

        音声/スクリプトのアイテムがある(混在禁止)チャンネルへは登録しない。
        戻り値: 1件でも登録したか。
        """
        if any(r.audio_rel or getattr(r, "is_script", False)
               for r in self.rows):
            return False
        ok = [p for p in paths
              if os.path.isfile(p)
              and os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        if not ok:
            return False
        for p in ok:
            self._append_row({"video": _safe_relpath(p, self.base_dir)})
        self._apply_row_correlations()
        self._update_add_buttons()
        if not self.enabled_var.get():
            self.enabled_var.set(True)
            self._update_enabled()
        self._notify_video_changed()
        return True

    def _notify_video_changed(self):
        """動画アイテムの増減を owner へ通知する(相関制御の再計算)。"""
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()

    def has_video(self) -> bool:
        """動画アイテムを1つ以上持つか(=動画チャンネル)。"""
        return any(getattr(r, "is_video", False) for r in self.rows)

    def video_device_types(self) -> set:
        """このチャンネルの動画アイテムが担当するデバイス種別(種別一意性用)。"""
        out = set()
        for r in self.rows:
            if getattr(r, "is_video", False):
                out |= r.device_types()
        return out

    def is_script_only(self) -> bool:
        """スクリプト専用チャンネルか(アイテムが1つ以上あり全てスクリプト)。"""
        return bool(self.rows) and all(getattr(r, "is_script", False)
                                       for r in self.rows)

    def set_seek_radio_suppressed(self, flag: bool):
        """シーク追従ラジオの強制非表示(動画イベント=シークは動画に固定)。"""
        self._seek_suppressed = bool(flag)
        self._update_add_buttons()

    def _update_add_buttons(self):
        """音声/スクリプト追加ボタンの相互排他とシーク追従ラジオを更新する。

        チャンネルは「音声ch」「動画ch」「スクリプト専用ch」のどれか1つ
        (混在禁止)なので、いずれかのアイテムがある間は他の追加ボタンを
        無効化する。動画は同時に1本なので、他のチャンネルが動画chのときも
        「＋動画」を無効化する(_video_lock。owner が設定)。
        シークバー追従ラジオは動画ch(シークは動画に固定)と抑止時のみ隠す。
        **=63でスクリプト専用chも追従対象に選べる**ようになった(音声が無い
        場合はスクリプトの進行が経過時間になる)。
        """
        has_audio = any(r.audio_rel for r in self.rows)
        has_script = any(getattr(r, "is_script", False) for r in self.rows)
        has_video = self.has_video()
        other = has_script or has_video
        self.add_audio_btn.configure(
            state="disabled" if other else "normal")
        self.add_script_btn.configure(
            state="disabled" if (has_audio or has_video) else "normal")
        self.add_video_btn.configure(
            state="disabled" if (has_audio or has_script
                                 or getattr(self, "_video_lock", False))
            else "normal")
        if self.seek_radio is not None:
            if has_video or getattr(self, "_seek_suppressed", False):
                self.seek_radio.pack_forget()
            elif not self.seek_radio.winfo_ismapped():
                self.seek_radio.pack(side="left")

    def set_device_visible(self, on: bool):
        """デバイス連動(=252)のON/OFFをこのチャンネルへ反映する。

        OFF: 「＋スクリプト」ボタンを隠し、各アイテム行のトラックUI
        (自動/手動メニュー・トラック領域)を隠す。行そのものは残す
        (スクリプト専用アイテムも=AQ6)。裏のデータは保持したままなので、
        ONに戻せばそのまま復活する。
        """
        if on:
            if not self.add_script_btn.winfo_manager():
                if self.dnd_hint.winfo_manager():
                    self.add_script_btn.pack(side="left", padx=(6, 0),
                                             before=self.dnd_hint)
                else:
                    self.add_script_btn.pack(side="left", padx=(6, 0))
        else:
            self.add_script_btn.pack_forget()
        for row in self.rows:
            row._refresh_device_ui()
            row._refresh_review_btn()
            row._update_mode_ui()

    def set_video_lock(self, flag: bool):
        """他のチャンネルが動画chのとき「＋動画」を無効化する(=52)。

        「動画を置けるチャンネルは1つだけ」(ユーザー決定)を、保存時の
        検証エラーではなく編集時の相関制御で先回り防止する。
        """
        self._video_lock = bool(flag)
        self._update_add_buttons()

    def _update_end_ui(self):
        """終了条件に応じて値欄と単位ラベルを出し入れする。

        =67(ユーザー要望): **値が要らない終了条件(「1周で終了」「無限」)では
        入力欄と変数トグル「x」を欄ごと隠す**。以前は無効化した空欄と単位なしの
        ラベルが残っていて、何を入れる欄なのか分からなかった。
        値が要るときは単位(周/回/秒)を必ず添える。
        """
        choice = self.end_var.get()
        unit = {tr("N周で終了"): tr("周"),
                tr("N回再生で終了"): tr("回"),
                # 時間指定は秒のみ(分/秒の単位切替は廃止)
                tr("N秒で終了"): tr("秒")}.get(choice, "")
        is_range = (choice == tr("N秒で終了"))
        # 並び順([値][単位])が崩れないよう、いったん外してから戻す
        self.end_field.pack_forget()
        self.end_unit_label.pack_forget()
        self.end_unit_label.configure(text=unit)
        if is_range:
            # =99: 値はコンボ下の専用行([min]秒 〜 [max]秒)で編集する。
            # インラインの単一値欄は隠す(構築時コメント参照)。
            # min欄が空なら単一値欄の値を引き継ぐ(=99以前は1つの欄を
            # 全終了条件で共用していたため、N周/N回→N秒の切替で値が
            # 残っていた。その連続性を維持=切替直後の検証エラー防止)
            if (not self.end_rmin.use_var
                    and not self.end_rmin.get_text().strip()
                    and not self.end_field.use_var
                    and self.end_field.get_text().strip()):
                self.end_rmin.set(self.end_field.get_text())
            self.end_range_row.pack(fill="x", pady=(2, 0), after=self._row_b)
            state = "normal" if self.enabled_var.get() else "disabled"
            self.end_rmin.set_state(state)
            self.end_rmax.set_state(state)
            self.end_field.set_state("disabled")
        elif unit:
            # 逆方向(N秒→N周/N回)も同様に、単一値欄が空ならmin欄の値を継ぐ
            if (not self.end_field.use_var
                    and not self.end_field.get_text().strip()
                    and not self.end_rmin.use_var
                    and self.end_rmin.get_text().strip()):
                self.end_field.set(self.end_rmin.get_text())
            self.end_range_row.pack_forget()
            self.end_field.pack(side="left", padx=2)
            self.end_unit_label.pack(side="left")
            self.end_field.set_state("normal")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")
        else:
            self.end_range_row.pack_forget()
            self.end_field.set_state("disabled")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")

    def _update_enabled(self):
        state = "normal" if self.enabled_var.get() else "disabled"
        self.mode_menu.configure(state=state)
        self.end_menu.configure(state=state)
        self.detail_btn.configure(state=state)
        for w in (self.pan_check, self.pan_l_entry, self.pan_r_entry,
                  self.iv_check, self.iv_min_entry, self.iv_max_entry):
            w.configure(state=state)
        # 値欄の出し入れは終了条件だけで決まる(=67)。無効チャンネルでも
        # 「1周で終了」なら欄は出さない=見た目が揃う。
        self._update_end_ui()
        if not self.enabled_var.get():
            self.end_field.set_state("disabled")
            self.end_rmin.set_state("disabled")
            self.end_rmax.set_state("disabled")

    def _on_toggle(self):
        """ユーザーが有効化チェックを操作したとき(load中は呼ばれない)。"""
        self._update_enabled()
        if self.owner is not None \
                and hasattr(self.owner, "_on_channel_enabled"):
            self.owner._on_channel_enabled()


# =132: 編集画面から開く小ダイアログの「中央やや左上」ずらし量(実ピクセル)
POPUP_SHIFT = 50


def _place_popup(dlg, master, w, h):
    """=132: ダイアログを親ウィンドウ中央のやや左上へ配置する。

    従来は位置未指定でOS任せだったため、編集画面がサブディスプレイに
    あってもメインディスプレイ側へ出てしまっていた。親の載っているモニタ
    (=115の monitor_rects)内へクランプするのでモニタをまたがない。

    位置は素の wm_geometry(実ピクセル)で指定する(=115: CTkのgeometry()は
    スケーリングが掛かるため混ぜない)。w/h は self.geometry() に渡した
    CTk単位の設計サイズで、実ピクセル寸法はスケーリングを掛けて見積もる
    (未マップのうちに位置を決めるため winfo_width は使えない)。
    """
    try:
        scaling = float(dlg._get_window_scaling())
    except Exception:
        scaling = 1.0
    rw, rh = int(round(w * scaling)), int(round(h * scaling))
    try:
        prect = (int(master.winfo_rootx()), int(master.winfo_rooty()),
                 int(master.winfo_width()), int(master.winfo_height()))
        mons = winstate.monitor_rects(master)
        x, y = winstate.center_popup_pos(prect, rw, rh, mons, POPUP_SHIFT)
        dlg.wm_geometry(f"+{x}+{y}")
    except Exception:
        pass                      # 配置は補助機能=失敗しても開くことを優先


class OpsDialog(ctk.CTkToplevel):
    """変数操作リストの編集ダイアログ。

    sections: [(見出し, キー, 操作リストraw)] を並べて編集する
    (アイテムのようにon_play/on_completeをまとめて1ダイアログで扱える)。
    OKで self.result = {キー: 操作リスト}(空リスト=削除の意)。キャンセルはNone。
    """

    def __init__(self, master, title: str, sections, all_names, numeric_names,
                 string_vars: set):
        super().__init__(master)
        self.title(tr("変数操作 - {0}").format(title))
        self.geometry("640x460")
        _place_popup(self, master, 640, 460)
        self.result = None
        self.all_names = list(all_names)
        self.numeric_names = list(numeric_names)
        self.string_vars = set(string_vars)
        self.sections: list[dict] = []

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        ctk.CTkLabel(body, text=tr("上から順に実行されます。加算/乗算/乱数は数値変数のみ、負の値で減算。乱数は最小〜最大の整数を代入。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x")
        # =126: 条件式(eval)の説明
        ctk.CTkLabel(body, text=tr("条件式は成立で1、不成立で0を代入します(対象は数値変数のみ)。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x", pady=(0, 4))

        for sec_title, key, ops_raw in sections:
            sec = {"key": key, "rows": []}
            ctk.CTkLabel(body, text=sec_title,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w").pack(fill="x", pady=(6, 0))
            sec["rows_frame"] = ctk.CTkFrame(body, fg_color="transparent")
            sec["rows_frame"].pack(fill="x")
            ctk.CTkButton(
                body, text=tr("＋ 操作を追加"), width=110, height=24,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
                command=lambda s=sec: self._add_row(s)
            ).pack(anchor="w", pady=(2, 2))
            self.sections.append(sec)
            for op in ops_raw or []:
                if isinstance(op, dict):
                    self._add_row(sec, op)
            self._refit_section(sec)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("OK"), width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._ok).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=4)
        # 入力エラーはダイアログ内にインライン表示(メッセージボックスは使わない)
        self.err_label = ctk.CTkLabel(
            btns, text="", font=ctk.CTkFont(size=12), text_color=MSG_ERROR,
            justify="left", anchor="w", wraplength=380)
        self.err_label.pack(side="left", padx=4)

        _front_window(self)
        self.grab_set()

    def _add_row(self, sec, raw: dict | None = None):
        raw = raw or {}
        if "roll" in raw:
            kind = "roll"
        elif "add" in raw:
            kind = "add"
        elif "mul" in raw:
            kind = "mul"
        elif "eval" in raw:
            kind = "eval"
        else:
            kind = "set"
        target = raw.get(kind) or ""
        row = ctk.CTkFrame(sec["rows_frame"], fg_color="transparent")
        row.pack(fill="x", pady=1)
        kind_label = {"add": tr("加算"), "mul": tr("乗算"),
                      "roll": tr("乱数"),
                      "eval": tr("条件式")}.get(kind, tr("セット"))
        kind_var = tk.StringVar(value=kind_label)
        entry = {"frame": row, "kind_var": kind_var}
        CTkOptionMenu(
            row, variable=kind_var, width=84, height=24,
            values=[tr("セット"), tr("加算"), tr("乗算"), tr("乱数"),
                    tr("条件式")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v, e=entry: self._update_row(e),
        ).pack(side="left")
        tgt_var = tk.StringVar(
            value=target or (self.all_names[0] if self.all_names else ""))
        tgt_menu = CTkOptionMenu(
            row, variable=tgt_var, width=110, height=24,
            values=self.all_names or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        tgt_menu.pack(side="left", padx=4)
        ctk.CTkLabel(row, text="←", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        # 値エリア: set/add=単一の値 / roll=min〜max の2欄(種別で出し分け)
        val_area = ctk.CTkFrame(row, fg_color="transparent")
        val_area.pack(side="left", padx=4)
        value = VarRefField(val_area, width=80, placeholder=tr("値"))
        value.set_names(self.all_names)
        value.set(raw.get("value") if "value" in raw else "")
        roll_min = VarRefField(val_area, width=64, placeholder=tr("最小"))
        roll_min.set_names(self.numeric_names)
        roll_min.set(raw.get("min") if "min" in raw else "")
        roll_sep = ctk.CTkLabel(val_area, text="〜", font=ctk.CTkFont(size=12),
                                text_color=TEXT_MUTED)
        roll_max = VarRefField(val_area, width=64, placeholder=tr("最大"))
        roll_max.set_names(self.numeric_names)
        roll_max.set(raw.get("max") if "max" in raw else "")
        # =126 条件式(eval): [変数▼][演算子▼][値] の3点(値欄は共用)
        when = raw.get("when") if isinstance(raw.get("when"), dict) else {}
        cond_var = tk.StringVar(
            value=when.get("var")
            or (self.all_names[0] if self.all_names else ""))
        cond_menu = CTkOptionMenu(
            val_area, variable=cond_var, width=100, height=24,
            values=self.all_names or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        cond_op = tk.StringVar(value=when.get("op")
                               if when.get("op") in _COND_OPS else "==")
        cond_op_menu = CTkOptionMenu(
            val_area, variable=cond_op, width=58, height=24,
            values=list(_COND_OPS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        if kind == "eval" and "value" in when:
            value.set(when.get("value"))
        entry.update({"tgt_var": tgt_var, "tgt_menu": tgt_menu, "value": value,
                      "roll_min": roll_min, "roll_sep": roll_sep,
                      "roll_max": roll_max,
                      "cond_var": cond_var, "cond_menu": cond_menu,
                      "cond_op": cond_op, "cond_op_menu": cond_op_menu})
        ctk.CTkButton(row, text="↑", width=24, height=24,
                      fg_color="transparent", text_color=TEXT_MUTED,
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry, s=sec: self._move_up(s, e)
                      ).pack(side="left", padx=(6, 0))
        ctk.CTkButton(row, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry, s=sec: self._delete_row(s, e)
                      ).pack(side="left")
        sec["rows"].append(entry)
        self._update_row(entry)
        self._refit_section(sec)

    def _row_kind(self, entry) -> str:
        v = entry["kind_var"].get()
        if v == tr("加算"):
            return "add"
        if v == tr("乗算"):
            return "mul"
        if v == tr("乱数"):
            return "roll"
        if v == tr("条件式"):
            return "eval"
        return "set"

    def _update_row(self, entry):
        """加算/乗算/乱数/条件式は数値変数のみ対象。roll時は min〜max、
        eval時は [変数][演算子][値]、それ以外は単一値欄。"""
        kind = self._row_kind(entry)
        if kind in ("add", "mul", "roll", "eval"):
            names = self.numeric_names or [""]
            entry["tgt_menu"].configure(values=names)
            if entry["tgt_var"].get() not in names:
                entry["tgt_var"].set(names[0])
        else:
            entry["tgt_menu"].configure(values=self.all_names or [""])
        for w in (entry["value"], entry["roll_min"], entry["roll_sep"],
                  entry["roll_max"], entry["cond_menu"],
                  entry["cond_op_menu"]):
            w.pack_forget()
        if kind == "roll":
            entry["roll_min"].pack(side="left")
            entry["roll_sep"].pack(side="left", padx=2)
            entry["roll_max"].pack(side="left")
        elif kind == "eval":
            # =126: x ← (a op 値) の3点
            entry["cond_menu"].pack(side="left")
            entry["cond_op_menu"].pack(side="left", padx=2)
            entry["value"].pack(side="left")
        else:
            entry["value"].pack(side="left")

    def _move_up(self, sec, entry):
        i = sec["rows"].index(entry)
        if i == 0:
            return
        sec["rows"][i - 1], sec["rows"][i] = sec["rows"][i], sec["rows"][i - 1]
        for e in sec["rows"]:
            e["frame"].pack_forget()
        for e in sec["rows"]:
            e["frame"].pack(fill="x", pady=1)

    def _refit_section(self, sec):
        """操作0件のセクションの rows_frame を潰す(空フレームの余白を防ぐ)。

        VarsDialog の _refit_vars/_refit_watch と同一手法。見出しと
        「＋操作を追加」ボタンの間に空フレームの既定高さぶんの隙間が出るのを防ぐ。
        """
        if sec["rows"]:
            sec["rows_frame"].pack_propagate(True)
        else:
            sec["rows_frame"].pack_propagate(False)
            sec["rows_frame"].configure(height=1)

    def _delete_row(self, sec, entry):
        sec["rows"].remove(entry)
        entry["frame"].destroy()
        self._refit_section(sec)

    def collect(self):
        """(エラーメッセージ, {キー: 操作リスト}) を返す。"""
        result = {}
        for sec in self.sections:
            ops = []
            for i, e in enumerate(sec["rows"]):
                kind = self._row_kind(e)
                name = e["tgt_var"].get()
                if not name:
                    return tr("操作{0}: 対象の変数を選択してください").format(i + 1), None
                if kind == "roll":
                    def _numval(field, lbl):
                        raw = field.get_raw()
                        if isinstance(raw, str):
                            return _parse_num_text(raw)   # ValueError で捕捉
                        return raw
                    try:
                        mn = _numval(e["roll_min"], "min")
                        mx = _numval(e["roll_max"], "max")
                    except ValueError:
                        return tr("操作{0}: 乱数の最小/最大が不正です").format(i + 1), None
                    ops.append({"roll": name, "min": mn, "max": mx})
                    continue
                if kind == "eval":
                    # =126 条件式: when={"var","op","value"} を組み立てる
                    cv = e["cond_var"].get()
                    if not cv:
                        return tr("操作{0}: 条件式の変数を選択してください").format(i + 1), None
                    v = e["value"].get_raw()
                    if isinstance(v, str):
                        if cv in self.string_vars:
                            pass   # 文字列変数との比較はテキストのまま
                        else:
                            try:
                                v = _parse_num_text(v)
                            except ValueError:
                                return tr("操作{0}: 条件式の値が不正です").format(i + 1), None
                    ops.append({"eval": name,
                                "when": {"var": cv, "op": e["cond_op"].get(),
                                         "value": v}})
                    continue
                v = e["value"].get_raw()
                if isinstance(v, str):
                    if kind == "set" and name in self.string_vars:
                        pass   # 文字列変数へのセットはテキストのまま
                    else:
                        try:
                            v = _parse_num_text(v)
                        except ValueError:
                            return tr("操作{0}: 値が不正です").format(i + 1), None
                ops.append({kind: name, "value": v})
            result[sec["key"]] = ops
        return None, result

    def _ok(self):
        err, result = self.collect()
        if err:
            self.err_label.configure(text=err)
            MESSAGE_LOG.append(("error", (tr("編集エラー"), err)))
            return
        self.result = result
        self.destroy()


class DetailDialog(ctk.CTkToplevel):
    """紹介文(シナリオ選択時に表示される説明文)の編集ダイアログ(=250)。

    常設だった編集画面上部の説明テキストボックスを、ツールバーの
    「紹介文」ボタンから開くダイアログへ移した(ユーザー要望=記入後は
    ほぼ触らずデッドスペースになっていたため)。

    - モーダル(変数/監視と同じ。非モーダルだと未確定テキストと
      ファイル保存の競合が生まれる=AQ2)。
    - 「保存」=**メモリへの反映のみ**(AQ1)。JSONファイルへは従来どおり
      編集画面の「保存」で書き出す。「キャンセル」「✕」は破棄。
    - 位置・サイズは WindowMemory("detail") で記憶する(他画面と同じ)。
    """

    def __init__(self, master, text: str):
        super().__init__(master)
        self.title(tr("紹介文"))
        self.result: str | None = None   # None=キャンセル / str=保存
        self.geometry("560x360")
        # 位置・サイズの記憶(=115の機構)。保存値が無ければ従来の
        # ダイアログ配置(編集画面の中央やや左上=132)へ。
        self.winmem = WindowMemory(self, "detail")
        if not self.winmem.restore():
            _place_popup(self, master, 560, 360)
        self.winmem.watch()

        hint = ctk.CTkLabel(
            self, text=tr("シナリオ選択時に表示される紹介文です(改行可)"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED, anchor="w")
        hint.pack(fill="x", padx=14, pady=(12, 4))
        # シナリオタブの「シナリオ内容」と同じテキストエリア(編集可)
        self.detail_box = ctk.CTkTextbox(
            self, font=ctk.CTkFont(size=12), wrap="word")
        self.detail_box.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.detail_box.insert("1.0", text or "")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("保存"), width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._on_save).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._on_cancel).pack(side="right", padx=4)
        # ✕はキャンセル扱い(ウィンドウ記憶だけ保存して閉じる)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        _front_window(self)
        self.grab_set()
        self.detail_box.focus_set()

    def _close(self):
        try:
            self.winmem.save_now()
        except Exception:
            pass
        self.destroy()

    def _on_save(self):
        self.result = self.detail_box.get("1.0", "end-1c")
        self._close()

    def _on_cancel(self):
        self.result = None
        self._close()


class BackgroundDialog(ctk.CTkToplevel):
    """背景イラスト(=262)の設定ダイアログ(モーダル)。

    再生画面全体の背景に敷くイラスト(トップレベル "background")を
    ファイル選択・クリア・暗さ(%)で編集する。「保存」=メモリ(self.data)への
    反映のみで、JSONファイルへは編集画面の「保存」で書き出す(紹介文と
    同じ作法)。キャンセル/✕は破棄。

    self.result:
      None                        = キャンセル(変更なし)
      {"background": None}        = クリア(キーごと削除)
      {"background": {"file": p, "dim": n}} = 設定
    """

    FILETYPES_EXT = "*.png *.jpg *.jpeg *.webp *.bmp *.gif"

    def __init__(self, master, raw, base_dir: str):
        super().__init__(master)
        self.title(tr("背景イラスト"))
        self.result = None
        self.base_dir = base_dir
        self.geometry("560x240")
        _place_popup(self, master, 560, 240)
        self.transient(master)

        # 現在値を分解(文字列/辞書の2書式。dim省略=40)
        self._file = ""
        dim = 40
        if isinstance(raw, str):
            self._file = raw
        elif isinstance(raw, dict):
            f = raw.get("file")
            self._file = f if isinstance(f, str) else ""
            d = raw.get("dim", 40)
            if isinstance(d, (int, float)) and not isinstance(d, bool):
                dim = int(round(d))

        hint = ctk.CTkLabel(
            self, text=tr("再生画面全体の背景に表示するイラストです(png/jpg等)。"
                          "表示のON/OFFは視聴する人がメイン画面の設定で"
                          "切り替えられます。"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED,
            wraplength=520, justify="left", anchor="w")
        hint.pack(fill="x", padx=14, pady=(12, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=14)
        self.file_label = ctk.CTkLabel(
            row, text="", font=ctk.CTkFont(size=12), anchor="w",
            wraplength=300, justify="left")
        self.file_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text=tr("クリア(背景なし)"), width=120, height=28,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._clear).pack(side="right", padx=(6, 0))
        ctk.CTkButton(row, text=tr("画像を選ぶ…"), width=110, height=28,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._choose).pack(side="right")

        dim_row = ctk.CTkFrame(self, fg_color="transparent")
        dim_row.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(dim_row, text=tr("暗さ(%)"), width=70, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.dim_var = tk.StringVar(value=str(dim))
        self.dim_entry = ctk.CTkEntry(
            dim_row, textvariable=self.dim_var, width=64, height=28,
            justify="right", font=ctk.CTkFont(size=13))
        self.dim_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            dim_row,
            text=tr("0=画像を最も強く表示 〜 100=真っ黒(既定40。40より下げるほど画像の主張が強くなります)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            wraplength=360, justify="left", anchor="w",
        ).pack(side="left", fill="x", expand=True)

        self.err_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e05a5a", anchor="w")
        self.err_label.pack(fill="x", padx=14, pady=(4, 0))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(4, 12), side="bottom")
        ctk.CTkButton(btns, text=tr("保存"), width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._on_save).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._on_cancel).pack(side="right", padx=4)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._refresh_file_label()
        _front_window(self)
        self.grab_set()

    def _refresh_file_label(self):
        if self._file:
            self.file_label.configure(
                text=os.path.basename(self._file), text_color=TEXT_MUTED)
        else:
            self.file_label.configure(text=tr("(なし)"),
                                      text_color=TEXT_MUTED)

    def _choose(self):
        kwargs = {}
        start = self._file if os.path.isabs(self._file) \
            else os.path.join(self.base_dir, self._file)
        # =270: 優先順は「設定済みファイルのフォルダ > 前回選択フォルダ
        # (編集画面共有・永続) > シナリオフォルダ」。差し替え時は元画像の
        # 場所が開くほうが自然なため、設定済みフォルダを最優先にする。
        init = os.path.dirname(start) if self._file \
            else _dialog_initialdir(self.base_dir)
        if init and os.path.isdir(init):
            kwargs["initialdir"] = init
        path = filedialog.askopenfilename(
            parent=self, title=tr("背景画像を選択"),
            filetypes=[(tr("画像ファイル"), self.FILETYPES_EXT),
                       (tr("すべてのファイル"), "*.*")],
            **kwargs)
        if not path:
            return
        _remember_dialog_dir(path)
        self._file = os.path.normpath(path)
        self.err_label.configure(text="")
        self._refresh_file_label()

    def _clear(self):
        self._file = ""
        self.err_label.configure(text="")
        self._refresh_file_label()

    def _on_save(self):
        if not self._file:
            self.result = {"background": None}
            self.destroy()
            return
        raw = self.dim_var.get().strip()
        try:
            dim = int(raw)
            if not (0 <= dim <= 100):
                raise ValueError
        except ValueError:
            self.err_label.configure(
                text=tr("暗さ(%)は 0〜100 の整数で指定してください"))
            return
        self.result = {"background": {"file": self._file, "dim": dim}}
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


class VarsDialog(ctk.CTkToplevel):
    """変数宣言と監視(watch)の編集ダイアログ。

    OKで self.result = {"vars": {...} or None, "watch": [...] or None}。
    キャンセルはNone。
    """

    def __init__(self, master, vars_raw: dict, watch_raw: list, event_ids):
        super().__init__(master)
        self.title(tr("変数と監視の管理"))
        self.geometry("760x640")
        _place_popup(self, master, 760, 640)
        self.result = None
        self.event_ids = list(event_ids)
        self.var_rows: list[dict] = []
        self.watch_rows: list[dict] = []

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self.body = body

        ctk.CTkLabel(body, text=tr("変数宣言"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(
            body,
            text=tr("型は「数値/文字列」で選択。最小/最大は数値のみ(全操作の結果がこの範囲に収まる)。\n"
                    "名前の変更・削除は、その変数を使う操作・条件を自動では直しません(保存時の検証でエラーになります)。"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, justify="left",
            anchor="w").pack(fill="x", pady=(0, 4))
        hdr = ctk.CTkFrame(body, fg_color="transparent")
        hdr.pack(fill="x")
        for text, w in ((tr("名前"), 140), (tr("型"), 90), (tr("初期値"), 90),
                        (tr("最小"), 70), (tr("最大"), 70)):
            ctk.CTkLabel(hdr, text=text, width=w, anchor="w",
                         font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                         ).pack(side="left", padx=(0, 6))
        # height=0: 変数0件の空フレームが CTkFrame 既定の200pxを確保して
        # 「＋変数を追加」ボタンが下に押し下げられる(余白)のを防ぐ。
        self.vars_frame = ctk.CTkFrame(body, fg_color="transparent", height=0)
        self.vars_frame.pack(fill="x")
        ctk.CTkButton(
            body, text=tr("＋ 変数を追加"), width=110, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_var_row()
        ).pack(anchor="w", pady=(4, 10))

        ctk.CTkLabel(body, text=tr("監視(watch)"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(
            body,
            text=tr("変数条件が成立した瞬間に指定イベントへ遷移します(シナリオ全体で有効)。\n"
                    "条件は変数操作の直後に評価され、遷移先のイベント再生中は評価されません。"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, justify="left",
            anchor="w").pack(fill="x", pady=(0, 4))
        self.watch_frame = ctk.CTkFrame(body, fg_color="transparent", height=0)
        self.watch_frame.pack(fill="x")
        self.watch_add_btn = ctk.CTkButton(
            body, text=tr("＋ 監視を追加"), width=110, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_watch_row())
        self.watch_add_btn.pack(anchor="w", pady=(4, 4))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("OK"), width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._ok).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=4)
        # 入力エラーはダイアログ内にインライン表示(メッセージボックスは使わない)
        self.err_label = ctk.CTkLabel(
            btns, text="", font=ctk.CTkFont(size=12), text_color=MSG_ERROR,
            justify="left", anchor="w", wraplength=440)
        self.err_label.pack(side="left", padx=4)

        for name, decl in (vars_raw or {}).items():
            self._add_var_row(name, decl)
        for w in (watch_raw or []):
            if isinstance(w, dict):
                self._add_watch_row(w)
        # 0件なら空フレームを潰して「追加」ボタンを直下へ寄せる
        self._refit_vars()
        self._refit_watch()

        _front_window(self)
        self.grab_set()

    def _refit_vars(self):
        """変数0件のとき vars_frame を潰す(空フレームの200px余白を防ぐ)。"""
        if self.var_rows:
            self.vars_frame.pack_propagate(True)
        else:
            self.vars_frame.pack_propagate(False)
            self.vars_frame.configure(height=1)

    def _refit_watch(self):
        if self.watch_rows:
            self.watch_frame.pack_propagate(True)
        else:
            self.watch_frame.pack_propagate(False)
            self.watch_frame.configure(height=1)

    # ---- 変数宣言 ----

    def _add_var_row(self, name: str = "", decl=None):
        if isinstance(decl, dict):
            init, vmin, vmax = decl.get("init"), decl.get("min"), decl.get("max")
            extra = {k: v for k, v in decl.items()
                     if k not in ("init", "min", "max")}
        else:
            init, vmin, vmax, extra = decl, None, None, {}
        is_str = isinstance(init, str)
        row = ctk.CTkFrame(self.vars_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        name_var = tk.StringVar(value=name)
        ctk.CTkEntry(row, textvariable=name_var, width=140, height=26
                     ).pack(side="left", padx=(0, 6))
        type_var = tk.StringVar(value=tr("文字列") if is_str else tr("数値"))
        entry = {"frame": row, "name_var": name_var, "type_var": type_var,
                 "extra": extra}
        CTkOptionMenu(
            row, variable=type_var, width=90, height=26,
            values=[tr("数値"), tr("文字列")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v, e=entry: self._update_var_row(e),
        ).pack(side="left", padx=(0, 6))
        init_var = tk.StringVar(
            value="" if init is None else
            (f"{init:g}" if isinstance(init, float) else str(init)))
        ctk.CTkEntry(row, textvariable=init_var, width=90, height=26
                     ).pack(side="left", padx=(0, 6))
        min_var = tk.StringVar(value="" if vmin is None else
                               (f"{vmin:g}" if isinstance(vmin, float) else str(vmin)))
        min_entry = ctk.CTkEntry(row, textvariable=min_var, width=70, height=26,
                                 placeholder_text=tr("なし"))
        min_entry.pack(side="left", padx=(0, 6))
        max_var = tk.StringVar(value="" if vmax is None else
                               (f"{vmax:g}" if isinstance(vmax, float) else str(vmax)))
        max_entry = ctk.CTkEntry(row, textvariable=max_var, width=70, height=26,
                                 placeholder_text=tr("なし"))
        max_entry.pack(side="left", padx=(0, 6))
        entry.update({"init_var": init_var, "min_var": min_var,
                      "max_var": max_var, "min_entry": min_entry,
                      "max_entry": max_entry})
        ctk.CTkButton(row, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_var_row(e)
                      ).pack(side="left")
        self.var_rows.append(entry)
        self._update_var_row(entry)
        self._refit_vars()

    def _update_var_row(self, entry):
        state = "disabled" if entry["type_var"].get() == tr("文字列") else "normal"
        entry["min_entry"].configure(state=state)
        entry["max_entry"].configure(state=state)

    def _delete_var_row(self, entry):
        self.var_rows.remove(entry)
        entry["frame"].destroy()
        self._refit_vars()
        self._refresh_cond_names()

    def _current_names(self) -> list[str]:
        return [e["name_var"].get().strip() for e in self.var_rows
                if e["name_var"].get().strip()]

    def _refresh_cond_names(self):
        names = self._current_names()
        for w in self.watch_rows:
            w["conds"].set_names(names)

    # ---- 監視(watch) ----

    def _add_watch_row(self, raw: dict | None = None):
        raw = raw or {}
        box = ctk.CTkFrame(self.watch_frame, corner_radius=8,
                           fg_color=("gray85", "gray20"))
        box.pack(fill="x", pady=3)
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=6)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=tr("条件(AND):"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", anchor="n")
        conds = CondListEditor(top)
        conds.set_names(self._current_names())
        conds.load(raw.get("when") or [])
        conds.pack(side="left", fill="x", expand=True, padx=(6, 0))
        entry = {"frame": box, "conds": conds,
                 "extra": {k: v for k, v in raw.items()
                           if k not in ("when", "to", "mode", "once")}}
        ctk.CTkButton(top, text="✕", width=24, height=24,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_watch_row(e)
                      ).pack(side="right", anchor="n")
        bottom = ctk.CTkFrame(inner, fg_color="transparent")
        bottom.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(bottom, text=tr("成立で→"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        to_var = tk.StringVar(
            value=raw.get("to") if raw.get("to") in self.event_ids
            else (self.event_ids[0] if self.event_ids else ""))
        CTkOptionMenu(
            bottom, variable=to_var, width=140, height=26,
            values=self.event_ids or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
        ).pack(side="left", padx=6)
        mode_var = tk.StringVar(
            value=tr("即時打ち切りで遷移") if raw.get("mode") == "interrupt"
            else tr("再生中の音声を待って遷移"))
        CTkOptionMenu(
            bottom, variable=mode_var, width=190, height=26,
            values=[tr("再生中の音声を待って遷移"), tr("即時打ち切りで遷移")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
        ).pack(side="left", padx=6)
        once_var = tk.BooleanVar(value=raw.get("once", True) is not False)
        ctk.CTkCheckBox(
            bottom, text=tr("1回の再生につき1度だけ"), variable=once_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(side="left", padx=8)
        entry.update({"to_var": to_var, "mode_var": mode_var,
                      "once_var": once_var})
        self.watch_rows.append(entry)
        self._refit_watch()

    def _delete_watch_row(self, entry):
        self.watch_rows.remove(entry)
        entry["frame"].destroy()
        self._refit_watch()

    # ---- 収集 ----

    def collect(self):
        """(エラーメッセージ, {"vars": .., "watch": ..}) を返す。"""
        vars_out = {}
        for i, e in enumerate(self.var_rows):
            name = e["name_var"].get().strip()
            if not name:
                return tr("変数{0}: 名前が空です").format(i + 1), None
            if name in vars_out:
                return tr("変数名 '{0}' が重複しています").format(name), None
            is_str = e["type_var"].get() == tr("文字列")
            init_text = e["init_var"].get()
            if is_str:
                init = init_text
            else:
                try:
                    init = _parse_num_text(init_text or "0")
                except ValueError:
                    return tr("変数 '{0}': 初期値が数値ではありません").format(name), None
            vmin = vmax = None
            if not is_str:
                try:
                    if e["min_var"].get().strip():
                        vmin = _parse_num_text(e["min_var"].get())
                    if e["max_var"].get().strip():
                        vmax = _parse_num_text(e["max_var"].get())
                except ValueError:
                    return tr("変数 '{0}': 最小/最大が数値ではありません").format(name), None
                if vmin is not None and vmax is not None and vmin > vmax:
                    return tr("変数 '{0}': 最小は最大以下にしてください").format(name), None
                if ((vmin is not None and init < vmin)
                        or (vmax is not None and init > vmax)):
                    return tr("変数 '{0}': 初期値が最小/最大の範囲外です").format(name), None
            if vmin is None and vmax is None and not e["extra"]:
                vars_out[name] = init
            else:
                d = {"init": init}
                if vmin is not None:
                    d["min"] = vmin
                if vmax is not None:
                    d["max"] = vmax
                d.update(e["extra"])
                vars_out[name] = d

        string_vars = {n for n, d in vars_out.items()
                       if isinstance(d.get("init") if isinstance(d, dict) else d,
                                     str)}
        watch_out = []
        for i, e in enumerate(self.watch_rows):
            where = tr("監視{0}").format(i + 1)
            err, conds = e["conds"].collect(where, string_vars)
            if err:
                return err, None
            to = e["to_var"].get()
            if to not in self.event_ids:
                return tr("{0}: 遷移先のイベントを選択してください").format(where), None
            w = {"when": conds, "to": to}
            if e["mode_var"].get() == tr("即時打ち切りで遷移"):
                w["mode"] = "interrupt"
            if not e["once_var"].get():
                w["once"] = False
            w.update(e["extra"])
            watch_out.append(w)
        if watch_out and not vars_out:
            return tr("監視(watch)を使うには変数を1つ以上宣言してください"), None
        return None, {"vars": vars_out or None, "watch": watch_out or None}

    def _ok(self):
        err, result = self.collect()
        if err:
            self.err_label.configure(text=err)
            MESSAGE_LOG.append(("error", (tr("編集エラー"), err)))
            return
        self.result = result
        self.destroy()


class ChannelCopyDialog(ctk.CTkToplevel):
    """チャンネル内容のコピー元を選ぶモーダルダイアログ。

    イベント/ステート/チャンネルの3コンボを横並びで連動表示する。音声ch・
    スクリプト専用chのみ選べる(=65。動画chは除外)。OKで
    on_ok(ev_id, st_key, cid) を呼ぶ。
    st_key はステートID、通常イベントは None。キャンセルは何もしない。

    sources: [(ev_id, st_id|None, cid), ...] の選択可能なコピー元一覧。
    """

    NO_STATE_LABEL = None   # __init__ で tr() 済みを入れる

    def __init__(self, master, sources, on_ok):
        super().__init__(master)
        self.title(tr("他からコピー"))
        self.geometry("560x180")
        _place_popup(self, master, 560, 180)
        self._on_ok = on_ok
        self.NO_STATE_LABEL = tr("(ステートなし)")
        self.sources = list(sources)

        # イベント順を保ちつつ重複排除
        self.event_ids = []
        for ev_id, _st, _cid in self.sources:
            if ev_id not in self.event_ids:
                self.event_ids.append(ev_id)

        wrap = ctk.CTkFrame(self, corner_radius=10)
        wrap.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        ctk.CTkLabel(
            wrap, text=tr("コピー元のチャンネルを選んでください(音声ch・スクリプトchのみ)。"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED, anchor="w",
            justify="left", wraplength=520).pack(fill="x", padx=10, pady=(10, 6))

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 8))

        def col(title):
            c = ctk.CTkFrame(row, fg_color="transparent")
            c.pack(side="left", padx=(0, 10))
            ctk.CTkLabel(c, text=title, font=ctk.CTkFont(size=11),
                         text_color=TEXT_MUTED, anchor="w").pack(fill="x")
            return c

        ce = col(tr("イベント"))
        self.ev_var = tk.StringVar()
        self.ev_menu = CTkOptionMenu(
            ce, variable=self.ev_var, width=150, height=28,
            values=self.event_ids or [""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_event_change())
        self.ev_menu.pack()

        cs = col(tr("ステート"))
        self.st_var = tk.StringVar()
        self.st_menu = CTkOptionMenu(
            cs, variable=self.st_var, width=140, height=28, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_state_change())
        self.st_menu.pack()

        cc = col(tr("チャンネル"))
        self.cid_var = tk.StringVar()
        self.cid_menu = CTkOptionMenu(
            cc, variable=self.cid_var, width=90, height=28, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.cid_menu.pack()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("OK"), width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._ok).pack(side="right", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=4)

        if self.event_ids:
            self.ev_var.set(self.event_ids[0])
            self._on_event_change()

        _front_window(self)
        self.grab_set()

    # ---- コンボの連動 ----
    def _state_label(self, st_id):
        return self.NO_STATE_LABEL if st_id is None else st_id

    def _states_for(self, ev_id):
        """(label, st_id) を順序保持で返す。"""
        out = []
        for e, st, _cid in self.sources:
            if e != ev_id:
                continue
            lbl = self._state_label(st)
            if lbl not in [x[0] for x in out]:
                out.append((lbl, st))
        return out

    def _chans_for(self, ev_id, st_id):
        return [cid for e, st, cid in self.sources
                if e == ev_id and st == st_id]

    def _on_event_change(self):
        ev_id = self.ev_var.get()
        states = self._states_for(ev_id)
        labels = [lbl for lbl, _st in states] or [""]
        self.st_menu.configure(values=labels)
        self.st_var.set(labels[0])
        self._on_state_change()

    def _on_state_change(self):
        ev_id = self.ev_var.get()
        st_id = self._selected_state_id()
        cids = self._chans_for(ev_id, st_id) or [""]
        self.cid_menu.configure(values=cids)
        self.cid_var.set(cids[0])

    def _selected_state_id(self):
        ev_id = self.ev_var.get()
        lbl = self.st_var.get()
        for l, st in self._states_for(ev_id):
            if l == lbl:
                return st
        return None

    def _ok(self):
        ev_id = self.ev_var.get()
        st_id = self._selected_state_id()
        cid = self.cid_var.get()
        if not ev_id or not cid:
            self.destroy()
            return
        cb = self._on_ok
        self.destroy()
        cb(ev_id, st_id, cid)


def _help_blocks():
    """ヘルプ本文(=244全面改稿・=245推敲・ユーザー原稿)。1表示行=1ソース行。

    =118〜=161の詳細なTIPS(34項目)は「細かすぎて読まれない」ため廃止し、
    概要レベルの4章構成へ更改した(2026-08-27 ユーザー決定)。=245で
    ユーザー推敲を反映: ①導入文の差し替え ②機能一覧の各項目間に空行
    (「主な機能」行は削除) ③視聴方法の文言変更(字下げなし・空行あり)
    ④「シナリオ編集」→「シナリオについて」+1文目追加(空行あり)。
    """
    return (
        ("h1", tr("本アプリについて"), ""),
        # =246: 空行の位置はユーザー指定(1行目の後/段落の後/「以下の機能が
        # あります。」の前後)。前後の空行を正確に出すため、導入文と機能一覧は
        # 1つのtextブロックに統合した。
        ("text", "", tr(
            "ルールや手順を組み合わせてプレイリストを構築できる、\n"
            "プログラマブルな音声プレイヤーです。\n"
            "\n"
            "分岐・ランダム・ゲーム要素が含まれる音声作品に対して、\n"
            "あらかじめ視聴ルールを\"シナリオ\"として定義することで、\n"
            "手動で切り替える手間を減らし、自動で進行させることができます。\n"
            "\n"
            "以下の機能があります。\n"
            "\n"
            "・進行分岐\n"
            "\n"
            "・重み付き抽選\n"
            "\n"
            "・3チャンネル同時再生\n"
            "\n"
            "・選択肢表示\n"
            "\n"
            "・変数\n"
            "\n"
            "・すごろく\n"
            "\n"
            "・動画再生(mpv)\n"
            "\n"
            "・ハプティクスデバイス連携\n"
            "\n"
            "・スクリプト制作支援")),
        ("h1", tr("視聴方法"), ""),
        ("text", "", tr(
            "１．シナリオタブでシナリオを選択、または新規作成\n"
            "\n"
            "２．再生タブでシナリオ視聴")),
        ("h1", tr("シナリオについて"), ""),
        ("text", "", tr(
            "本アプリにおいて、ユーザーが視聴する対象がシナリオです。\n"
            "\n"
            "シナリオの構造は\n"
            "「シナリオ - イベント - チャンネル - アイテム」\n"
            "となっています。")),
        ("h2", tr("アイテム"), tr(
            "音声ファイルのことです。\n"
            "シナリオを構成する最小単位です。")),
        ("h2", tr("チャンネル"), tr(
            "複数のアイテムを保持し、\n"
            "再生順序などの定義を持ちます。")),
        ("h2", tr("イベント"), tr(
            "3つのチャンネル(L/C/R)を保持し、\n"
            "終了条件や遷移先イベントなどの定義を持ちます。")),
        # ※ステート形式イベントは、イベント配下の補足として item 階層に置く
        # (ユーザー決定)。
        ("item", tr("※ステート形式イベント"), tr(
            "イベント内に複数の状態(ステート)を保持できます。\n"
            "イベントと同様、各ステートは3つのチャンネル(L/C/R)を持ち、\n"
            "遷移条件・遷移先ステートなどの定義を持ちます。")),
        ("h2", tr("シナリオ"), tr(
            "イベントの集まりであり、視聴の対象です。\n"
            "実体はJSON形式のファイルです。")),
        ("h1", tr("ハプティクスデバイス連携"), ""),
        ("text", "", tr(
            "アイテムにスクリプト(funscript/csv)を紐づけることで、\n"
            "再生に同期してハプティクスデバイスを動かすことができます。\n"
            "シリアルポート接続とBluetooth接続(Intiface Central経由)が利用可能です。")),
    )


def _help_sections():
    """互換API: 見出しと本文を持つブロックだけを (見出し, 本文) で返す。"""
    return tuple((title, body) for kind, title, body in _help_blocks()
                 if kind != "text" and title)


def _license_blocks():
    """ライセンス・クレジット画面の中身を (種別, 見出し, 本文) で返す。

    種別は _help_blocks() と同じ "h1"/"h2"/"item"/"text" で、LicenseDialog が
    同じ体裁で描画する。粒度は「要約+クレジット+同梱ファイル案内」(ユーザー
    決定)。各ライセンスの全文は載せず **THIRD-PARTY-LICENSES.txt を参照
    させる方式で確定**(=258: 2026-08-30 ユーザー決定。かつて予定していた
    「画面内に全文表示」は行わない)。
    =136: COEIROINK の節は削除(サンプル音声は GitHub 配布物に同梱せず
    Ci-en 記事で独立配布するため、RVP 本体のライセンス画面では言及しない)。

    表示専用。ライブラリの種別・著作権表記は実際のパッケージから確認した値
    (customtkinter=MIT/(c)2023 Tom Schimansky、buttplug-py=BSD-3/(c)2022
    Siege-Wizard、pyserial=BSD-3/(c)2001-2020 Chris Liechti、pygame-ce=
    LGPL-2.1)。制作者表記は「Torp」で確定済み(=140。LICENSE・i18n・
    test_license_ui も同名で整合)。
    """
    return (
        ("h1", tr("ライセンス・クレジット"), ""),
        ("text", "", tr(
            "RVP(Random Voice Player)は MITライセンスのオープンソースソフトウェアです。\n"
            "本ソフトウェアは、以下のソフトウェア・素材を利用しています。")),
        # =292: 公開バージョン(rvp.__version__)を先頭行に出す。
        ("h2", tr("RVP 本体"), tr(
            "バージョン {0}\n"
            "MIT License\n"
            "© 2026 Torp").format(__version__)),
        ("h2", tr("使用しているソフトウェア"), ""),
        # =140: 各説明は体言止め(「〜に使用しています。」の術語を削除=ユーザー
        # 指定)。mpv・フォントも item(青文字)としてこの節に並べる。
        ("item", "pygame-ce", tr(
            "音声再生\n"
            "LGPL-2.1 / © pygame-ce developers")),
        ("item", "CustomTkinter", tr(
            "画面表示\n"
            "MIT License / © 2023 Tom Schimansky")),
        # =262: 背景イラスト機能でPillowを直接importするようになった
        # (従来もCustomTkinterの必須依存として同梱されていた)。
        ("item", "Pillow", tr(
            "背景イラスト表示(画像の読み込み・加工)\n"
            "MIT-CMU License / © 2010 Jeffrey A. Clark and contributors")),
        ("item", "buttplug-py", tr(
            "デバイス通信(Buttplug)\n"
            "BSD-3-Clause / © 2022 Siege-Wizard")),
        ("item", "tkinterdnd2 / tkdnd", tr(
            "編集画面ドラッグ&ドロップ\n"
            "MIT License / tkdnd は BSD スタイルライセンス")),
        ("item", "pyserial", tr(
            "TCodeデバイス通信\n"
            "BSD-3-Clause / © 2001-2020 Chris Liechti")),
        ("item", tr("Python / Tcl・Tk"), tr(
            "実行環境\n"
            "PSF License / Tcl・Tk License(BSD スタイル)")),
        ("item", "mpv", tr("動画再生")),
        ("item", tr("フォント"), tr(
            "BIZ UDゴシック(SIL Open Font License)")),
    )


class ImportDialog(ctk.CTkToplevel):
    """他のシナリオ(.json)からイベントを取り込むモーダルダイアログ。

    取り込み元のイベント一覧をチェックボックスで複数選択し、「取り込み」で
    owner._perform_import(src_data, src_dir, ids) を呼ぶ(ids は元ファイルの
    定義順)。「参照先も選択」はチェック済みイベントから next 遷移先
    (random/choice/cond/input/timeout/default/else/when_exhausted 含む)を
    再帰的に辿り、取り込み元に存在するものを追加選択する閉包補助。
    """

    def __init__(self, master, src_data, src_dir, src_name):
        super().__init__(master)
        self.owner = master
        self.src = src_data
        self.src_dir = src_dir
        self.title(tr("イベントの取り込み"))
        self.geometry("560x560")
        _place_popup(self, master, 560, 560)
        self.transient(master)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(head, text=tr("取り込み元: {0}").format(src_name),
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).pack(side="left")

        sel_row = ctk.CTkFrame(self, fg_color="transparent")
        sel_row.pack(fill="x", padx=14, pady=(0, 4))
        for text, cmd in ((tr("全選択"), self._select_all),
                          (tr("全解除"), self._clear_all),
                          (tr("参照先も選択"), self._select_closure)):
            ctk.CTkButton(sel_row, text=text, width=92, height=26,
                          fg_color="transparent", border_width=1,
                          border_color=MUTED,
                          text_color=("gray20", "gray85"),
                          hover_color=("gray85", "gray25"),
                          command=cmd).pack(side="left", padx=(0, 6))

        self.list_frame = ctk.CTkScrollableFrame(self, corner_radius=8)
        self.list_frame.pack(fill="both", expand=True, padx=14, pady=4)

        # (ev_id, BooleanVar) を元ファイルの定義順で保持
        self.rows: list = []
        for ev_id, ev in (src_data.get("events") or {}).items():
            row = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=False)
            ctk.CTkCheckBox(row, text=ev_id, variable=var,
                            font=ctk.CTkFont(size=12), width=180,
                            checkbox_width=18, checkbox_height=18
                            ).pack(side="left")
            detail = ""
            if isinstance(ev, dict) and isinstance(ev.get("detail"), str):
                detail = ev["detail"].splitlines()[0][:40]
            if detail:
                ctk.CTkLabel(row, text=detail,
                             font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                             ).pack(side="left", padx=(8, 0))
            self.rows.append((ev_id, var))

        self.warn_label = ctk.CTkLabel(
            self, text=tr("取り込むイベントを選択してください"),
            font=ctk.CTkFont(size=11), text_color="#e05a5a")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(btn_row, text=tr("キャンセル"), width=100, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self.destroy).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_row, text=tr("取り込み"), width=110, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._do_import).pack(side="right")

        self.after(100, self._try_grab)

    def _try_grab(self):
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _select_all(self):
        for _ev_id, var in self.rows:
            var.set(True)

    def _clear_all(self):
        for _ev_id, var in self.rows:
            var.set(False)

    def _select_closure(self):
        """チェック済みイベントの遷移先を再帰的に辿って追加選択する。"""
        from .scenario_map import next_targets
        events = self.src.get("events") or {}
        sel = {ev_id for ev_id, var in self.rows if var.get()}
        changed = True
        while changed:
            changed = False
            for ev_id in list(sel):
                ev = events.get(ev_id)
                if not isinstance(ev, dict):
                    continue
                for t in next_targets(ev):
                    if t in events and t not in sel:
                        sel.add(t)
                        changed = True
        for ev_id, var in self.rows:
            if ev_id in sel:
                var.set(True)

    def _do_import(self):
        ids = [ev_id for ev_id, var in self.rows if var.get()]
        if not ids:
            self.warn_label.pack(pady=(0, 2))
            return
        owner = self.owner
        src, src_dir = self.src, self.src_dir
        self.destroy()
        owner._perform_import(src, src_dir, ids)


class HelpDialog(ctk.CTkToplevel):
    """アプリ概要を表示する非モーダルのヘルプ画面(=244で概要レベルへ更改)。

    編集画面と交互に読めるよう grab_set は行わない(非モーダル)。
    全項目を1枚のスクロールで表示する。
    """

    def __init__(self, master):
        super().__init__(master)
        # =244: TIPS廃止に伴い、タイトルは「ヘルプ」だけにする
        self.title(tr("ヘルプ"))
        # =115: 前回のサイズ・位置を復元(無ければ従来の760x600・親任せの位置)
        self.winmem = WindowMemory(self, "help")
        if not self.winmem.restore():
            self.geometry("760x600")
        self.minsize(520, 360)
        self.winmem.watch()
        self.winmem.install_close_hook()

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        # =116: 3階層(章 h1 / 節 h2 / 項目 item)+ 見出しのない本文 text。
        # 段差は「文字の大きさ・太さ・色」と「左インデント」の2つで付ける。
        self.section_titles = []
        first_h1 = True
        for kind, title, text in _help_blocks():
            if kind == "h1":
                if not first_h1:
                    # 章の区切り線(空のCTkFrameは200pxを要求するので明示指定)
                    sep = ctk.CTkFrame(body, height=2, corner_radius=0,
                                       fg_color=("gray75", "gray35"))
                    sep.pack(fill="x", padx=8, pady=(22, 0))
                first_h1 = False
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=16, weight="bold"),
                    text_color=ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=660).pack(fill="x", padx=8, pady=(14, 4))
            elif kind == "h2":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                    text_color=TEXT_HEAD, anchor="w", justify="left",
                    wraplength=650).pack(fill="x", padx=16, pady=(14, 2))
            elif kind == "item":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=640).pack(fill="x", padx=24, pady=(10, 1))
            if not text:
                continue
            indent = {"h1": 16, "h2": 26, "item": 34}.get(kind, 16)
            ctk.CTkLabel(
                body, text=text, font=ctk.CTkFont(size=12),
                anchor="w", justify="left",
                wraplength=670 - indent).pack(fill="x", padx=indent,
                                              pady=(0, 2))


        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("閉じる"), width=90, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._close).pack(side="right")
        # ライセンス・クレジットは別窓(LicenseDialog)へ。ヘルプ本文の目的は
        # アプリの説明なので、性質の違う法的テキストは分ける
        # (ユーザー決定=案A)。side="right" は後にpackした方が左へ来る。
        self._license_dlg = None
        ctk.CTkButton(btns, text=tr("ライセンス"), width=110, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._open_license).pack(side="right",
                                                       padx=(0, 8))

        _front_window(self)
        # 非モーダル: grab_set しない(編集画面と交互に操作できる)

    def _open_license(self):
        """ライセンス・クレジット画面(非モーダル)を開く。既に開いていれば
        前面化するだけ(2つ目は開かない)。ヘルプを閉じても残せるよう、親は
        ヘルプの親(=メイン画面)にする。"""
        dlg = self._license_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.deiconify()
                    dlg.lift()
                    dlg.focus_set()
                    return
            except Exception:
                pass
            self._license_dlg = None
        self._license_dlg = LicenseDialog(self.master)

    def _close(self):
        """=115: 閉じる前にサイズ・位置を保存する(×ボタンも同じ経路)。"""
        try:
            self.winmem.save_now()
        except Exception:
            pass
        self.destroy()


def third_party_licenses_path():
    """THIRD-PARTY-LICENSES.txt の探索(=139)。見つからなければ None。

    exe(PyInstaller onedir)では exe と同じフォルダ、開発環境では
    リポジトリルート(rvp パッケージの1つ上)に置かれる。
    """
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(sys.executable),
                                  "THIRD-PARTY-LICENSES.txt"))
    cands.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "THIRD-PARTY-LICENSES.txt"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def third_party_licenses_text():
    """THIRD-PARTY-LICENSES.txt の全文(=139)。読めなければ None。"""
    path = third_party_licenses_path()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


class LicenseDialog(ctk.CTkToplevel):
    """ライセンス・クレジットを表示する非モーダルのダイアログ。

    HelpDialog の「ライセンス」ボタンから開く(案A)。体裁は HelpDialog と
    同じ3階層(h1/h2/item)+本文。中身は _license_blocks()。
    =139: 末尾に THIRD-PARTY-LICENSES.txt の全文を表示する(ユーザー決定
    「画面には txt と同じ全文が出る」)。全文は tr() に通さない(英語の
    法的文書=翻訳対象外)。表示は読み取り専用の CTkTextbox(内側スクロール)。
    ファイルが見つからないときは案内文を出す。
    """

    def __init__(self, master):
        super().__init__(master)
        self.title(tr("ライセンス・クレジット"))
        # =115: 前回のサイズ・位置を復元(無ければ従来の760x600)。
        self.winmem = WindowMemory(self, "license")
        if not self.winmem.restore():
            self.geometry("720x560")
        self.minsize(480, 340)
        self.winmem.watch()
        self.winmem.install_close_hook()

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        self.section_titles = []
        first_h1 = True
        for kind, title, text in _license_blocks():
            if kind == "h1":
                if not first_h1:
                    sep = ctk.CTkFrame(body, height=2, corner_radius=0,
                                       fg_color=("gray75", "gray35"))
                    sep.pack(fill="x", padx=8, pady=(22, 0))
                first_h1 = False
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=16, weight="bold"),
                    text_color=ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=660).pack(fill="x", padx=8, pady=(14, 4))
            elif kind == "h2":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                    text_color=TEXT_HEAD, anchor="w", justify="left",
                    wraplength=650).pack(fill="x", padx=16, pady=(14, 2))
            elif kind == "item":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=640).pack(fill="x", padx=24, pady=(10, 1))
            if not text:
                continue
            indent = {"h1": 16, "h2": 26, "item": 34}.get(kind, 16)
            ctk.CTkLabel(
                body, text=text, font=ctk.CTkFont(size=12),
                anchor="w", justify="left",
                wraplength=670 - indent).pack(fill="x", padx=indent,
                                              pady=(0, 2))

        # ---- =139: THIRD-PARTY-LICENSES.txt の全文 ----
        sep = ctk.CTkFrame(body, height=2, corner_radius=0,
                           fg_color=("gray75", "gray35"))
        sep.pack(fill="x", padx=8, pady=(22, 0))
        ctk.CTkLabel(
            body, text=tr("サードパーティライセンス全文"),
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ACCENT_TEXT, anchor="w", justify="left",
            wraplength=660).pack(fill="x", padx=8, pady=(14, 4))
        full = third_party_licenses_text()
        if full is None:
            ctk.CTkLabel(
                body, text=tr(
                    "THIRD-PARTY-LICENSES.txt が見つかりませんでした。\n"
                    "配布物に同梱されているファイルを参照してください。"),
                font=ctk.CTkFont(size=12), anchor="w", justify="left",
                wraplength=650).pack(fill="x", padx=16, pady=(0, 8))
            self.tp_textbox = None
        else:
            # 全文(約1900行)はラベルではなく読み取り専用Textboxで表示する
            # (ラベル1900個は生成が重い。Textboxなら1ウィジェット+内側
            # スクロールで済む。英語の法的文書=tr()非対象)。
            self.tp_textbox = ctk.CTkTextbox(
                body, corner_radius=8, height=380, wrap="word",
                font=ctk.CTkFont(size=11))
            self.tp_textbox.pack(fill="x", padx=16, pady=(2, 10))
            self.tp_textbox.insert("1.0", full)
            self.tp_textbox.configure(state="disabled")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("閉じる"), width=90, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._close).pack(side="right")

        _front_window(self)

    def _close(self):
        try:
            self.winmem.save_now()
        except Exception:
            pass
        self.destroy()


# ==================== アイテムレビュー(=164) ====================
#
# 編集中のアイテムを「実際に再生される姿」で確かめるための画面。
# 音声の試聴と funscript/csv のグラフ表示を1枚にまとめる。
#
# 仕様(2026-08-16 ユーザー決定):
#  - 音声のみ           → シークバー・再生/一時停止・10秒戻し/送り・音量
#  - スクリプトのみ     → グラフ(個別表示のみ)。**音声が無くても操作バーを出す**
#                        (無音のまま時間だけ進み、グラフが流れる)
#  - 音声+スクリプト    → 両方。グラフは音声の再生位置に追従する
#  - 動画               → =203で対応(mpv 単独再生。再生/一時停止・シークを
#                        RVP側と同期。クロックは mpv の time-pos に従属)
#  - **デバイスへは何も送らない**(画面での確認のみ)
#  - **区間指定(=59)を反映する**(区間の先頭が0秒。トラック個別 > アイテム)
#  - 保存前の**入力中の値**が対象(行のウィジェットから組み立てる)
#  - 同時に1つだけ。開いていれば中身を差し替えて前面化する
#  - 開いたら自動再生。本編が再生中なら自動で一時停止する
#
# 再生には pygame.mixer の **7番チャンネル**を使う。本編は L/C/R=0/1/2 しか
# 使わない(player._CH_SLOT)ので、本編と取り合いにならない。

REVIEW_SLOT = 7          # レビュー再生に使う pygame.mixer のチャンネル番号
# =214: 再生速度の選択肢(速い→遅い。既定 1.0)
SPEED_CHOICES = tuple(round(2.0 - 0.1 * i, 1) for i in range(19))

# =243: 音声波形の表示モード(configキー audio_wave_mode。参照・編集共通)
WAVE_BUCKET_MS = 10
WAVE_MODE_KEYS = ("off", "mono", "stereo", "stereo_rev")


def compute_wave_env(raw, freq: int, fmt: int, channels: int,
                     bucket_ms: int = WAVE_BUCKET_MS):
    """PCMデータから音声波形のエンベロープを作る(=243。numpy 不使用)。

    10ms(bucket_ms)ごとのバケットへ区切り、各バケットの**振幅のピーク**
    (max(max(seg), -min(seg)))を ch ごとに並べる。表示は中央線に対する
    上下対称の帯なので min/max を別々に持つ必要はない。
    デインタリーブは array のスライス(arr[c::channels])で C 速度。
    raw は bytes のほか memoryview も可(=249。コピーレスで渡せる)。
    戻り値 {"bucket_ms","chans"(L,R),"mono"(バケットごと max 合成),
    "peak"(L/R共通の最大値=正規化の分母)} / 対応外フォーマット・無音は None。
    """
    from array import array
    channels = max(1, int(channels))
    absfmt = abs(int(fmt))
    if absfmt == 16 and fmt < 0:
        unit, code, base = 2, "h", 0
    elif absfmt == 8:
        unit, code, base = 1, ("b" if fmt < 0 else "B"), (0 if fmt < 0
                                                          else 128)
    else:
        return None                     # 想定外のフォーマット
    arr = array(code)
    n = len(raw) - (len(raw) % (unit * channels))
    if n <= 0:
        return None
    arr.frombytes(raw[:n])
    spb = max(1, int(freq) * bucket_ms // 1000)
    chans = []
    for c in range(min(2, channels)):
        ch = arr[c::channels]
        out = array("i")
        for i in range(0, len(ch), spb):
            seg = ch[i:i + spb]
            if not seg:
                break
            out.append(max(max(seg) - base, base - min(seg), 0))
        chans.append(out)
    if not chans or not len(chans[0]):
        return None
    if len(chans) == 1:
        chans.append(chans[0])
    m = min(len(chans[0]), len(chans[1]))
    mono = array("i", (max(chans[0][i], chans[1][i]) for i in range(m)))
    peak = max(max(chans[0]), max(chans[1]))
    if peak <= 0:
        return None                     # 無音
    return {"bucket_ms": int(bucket_ms), "chans": (chans[0], chans[1]),
            "mono": mono, "peak": int(peak)}


# =203: 動画レビューで mpv へ渡す追加引数(テスト用フック。ヘッドレス環境の
# テストは ("--vo=null","--ao=null","--force-window=no","--no-config") にする)
REVIEW_MPV_EXTRA_ARGS: tuple = ()


def _mpv_path_setting() -> str | None:
    """mpv のパス(=203)。再生タブと同じ設定(config "mpv_path")を共用し、
    未設定なら find_mpv() の自動探索。見つからなければ None。"""
    try:
        cfg = load_config()
        mp = cfg.get("mpv_path")
        if isinstance(mp, str) and mp:
            return mp
    except Exception:
        pass
    try:
        from . import mpv_client
        return mpv_client.find_mpv()
    except Exception:
        return None


class _EditorMpv:
    """レビュー画面用の mpv(=203)。専用スレッドの asyncio ループで
    MpvClient を動かし、tk 側からはスレッドセーフに操作・状態参照する。

    再生タブの mpv(player 側)とは**プロセスも IPC パイプも別**
    (default_ipc_path はプロセスID固有なので "-edit" を足して衝突を防ぐ)。
    エディタ(ScenarioEditor)が生きている間はプロセスを維持して
    loadfile で差し替え、エディタを閉じたら quit する。
    """

    def __init__(self, mpv_path: str | None = None):
        from . import mpv_client
        self.client = mpv_client.MpvClient(
            mpv_path=mpv_path,
            ipc_path=mpv_client.default_ipc_path() + "-edit")
        self.loop = asyncio.new_event_loop()
        self._th = threading.Thread(target=self._run, daemon=True,
                                    name="rvp-editor-mpv")
        self._th.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _call(self, coro):
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        except Exception:
            return None

    @property
    def state(self) -> dict:
        return self.client.state

    @property
    def alive(self) -> bool:
        try:
            return bool(self.client.connected and self.client.alive)
        except Exception:
            return False

    def open(self, path: str, seek_s: float = 0.0):
        """起動(必要なら)→ loadfile → 一時停止 → 区間頭へシーク。"""
        client = self.client

        async def _o():
            if not client.connected:
                await client.start(extra_args=REVIEW_MPV_EXTRA_ARGS)
            await client.loadfile(path)
            await client.set_pause(True)
            # ロード完了(duration 到着)を待ってからシークする
            for _ in range(100):
                if client.state.get("duration"):
                    break
                await asyncio.sleep(0.05)
            if seek_s > 0:
                try:
                    await client.seek(seek_s)
                except Exception:
                    pass
        return self._call(_o())

    def set_pause(self, flag: bool):
        return self._call(self.client.set_pause(bool(flag)))

    def seek(self, seconds: float):
        return self._call(self.client.seek(float(seconds)))

    def set_speed(self, rate: float):
        """=214: 再生速度(mpv の speed プロパティ。ピッチ補正は mpv 既定=
        audio-pitch-correction=yes のまま)。"""
        client = self.client

        async def _s():
            # open() 直後は起動待ちのことがあるので、接続まで少し待つ
            for _ in range(100):
                if client.connected:
                    break
                await asyncio.sleep(0.05)
            try:
                await client.command("set_property", "speed", float(rate))
                client.state["speed"] = float(rate)
            except Exception:
                pass
        return self._call(_s())

    def quit(self):
        client, loop = self.client, self.loop

        async def _q():
            try:
                await client.quit()
            except Exception:
                pass
            finally:
                loop.stop()
        self._call(_q())


def _hold_editor_mpv(holder, mpv_path: str | None) -> "_EditorMpv":
    """holder(ScenarioEditor など)に紐づく _EditorMpv を用意する(=203)。

    既存インスタンスが死んでいれば作り直す。holder の破棄で quit する
    (アプリ終了時も破棄の連鎖で閉じる)。
    """
    m = getattr(holder, "_edit_mpv", None)
    if m is not None:
        try:
            if m.client.alive or not m.client.state.get("path"):
                return m
        except Exception:
            pass
    m = _EditorMpv(mpv_path)
    holder._edit_mpv = m
    if hasattr(holder, "bind") and             not getattr(holder, "_edit_mpv_hooked", False):
        holder._edit_mpv_hooked = True

        def _on_holder_destroy(event, h=holder):
            if event.widget is h and getattr(h, "_edit_mpv", None):
                h._edit_mpv.quit()
                h._edit_mpv = None
        try:
            holder.bind("<Destroy>", _on_holder_destroy, add="+")
        except Exception:
            pass
    return m
REVIEW_ROTATE_TYPES = ("rotate_ufo", "rotate_a10cyclonesa")

# =170: スクリプト編集モードで対象にできるトラック種別(P1)。
# =224: csv(ROTATE 3列/5列)を追加。
# =225: **rotate系・vibration の funscript も追加**(未対応領域を解消)。
SCRIPT_EDIT_TYPES = ("linear", "twist", "vibration",
                     "rotate_ufo", "rotate_a10cyclonesa")
# =224: csv で編集できる種別(ROTATE 系)。
SCRIPT_EDIT_CSV_TYPES = REVIEW_ROTATE_TYPES
# =225: **階段**(次の指示まで値を保つ)で描く種別。csv も常に階段。
#   ROTATE  : pos 50=停止 / 100=正回転最大 / 0=逆回転最大
#   VIBRATION: pos 0=停止 〜 100=最大
# どちらも funscript の pos がそのまま値なので、変換は要らない
# (player._graph_points_funscript + kind="step" と同じ扱い)。
SCRIPT_EDIT_STEP_TYPES = ("vibration", "rotate_ufo", "rotate_a10cyclonesa")
# 自動紐づけのタグ(=新規保存の既定ファイル名に添える。ヘルプ 1-2 の規則)。
# **linear/twist は従来どおり接尾辞なし**(仕様 12・既存の挙動を変えない)。
# =225 で足した種別は、タグが無いと自動紐づけで LINEAR 扱いになってしまう
# ため必ず添える。
SCRIPT_EDIT_TAGS = {"vibration": "_vib",
                    "rotate_ufo": "_ufo", "rotate_a10cyclonesa": "_a10"}
# 新規作成コンボの値 → (種別, kind, 列数)。kind="funscript" / "csv"
SCRIPT_EDIT_NEW_KINDS = (
    ("linear", ("linear", "funscript", 0)),
    ("twist", ("twist", "funscript", 0)),
    ("vibration", ("vibration", "funscript", 0)),
    ("rotate_ufo (funscript)", ("rotate_ufo", "funscript", 0)),
    ("rotate_a10cyclonesa (funscript)",
     ("rotate_a10cyclonesa", "funscript", 0)),
    ("rotate_ufo (csv)", ("rotate_ufo", "csv", 3)),
    ("rotate_a10cyclonesa (csv)", ("rotate_a10cyclonesa", "csv", 3)),
    # =229: 5列(左右独立)は **UFO TW だけ**。A10サイクロンSA にはロータが
    # 1つしか無く5列にする意味が無いため、新規作成の候補から外した
    # (既存の5列 csv を「(左)」「(右)」で編集する側は従来どおり)。
    # =233: 5列は UFO TW の左右独立ロータ専用なので、表示をそう書く
    ("rotate_ufo (UFOTW用csv)", ("rotate_ufo", "csv", 5)),
)


def _is_csv(path: str) -> bool:
    return str(path).casefold().endswith(".csv")
# =205: ユーザーパターン編集ポップアップの未保存確認の自動応答(テスト用)。
# True=破棄して続行 / False=キャンセル / None=実際の確認ダイアログを出す。
USER_PAT_UNSAVED_AUTO: bool | None = None


class UserPatternDialog(ctk.CTkToplevel):
    """ユーザーパターンの編集ポップアップ(=205・仕様 6.1)。

    - 20枠をボタンで切り替え、同じ描画エディタ(ScriptEditGraph)で
      **点だけ**を打って「保存」する(パターンの配置・グループ化は不可)。
    - **=231: 枠は種別ごと**(linear=L1〜L20 / twist=T1〜T20 /
      rotate_ufo=U1〜U20 / rotate_a10cyclonesa=A1〜A20 /
      vibration=V1〜V20)。この画面は**開いたときの対象の種別に固定**
      (ユーザー決定)。
    - 名前は固定。基準長=最終 at(保存時に先頭 at を 0 へ正規化)。
    - 保存先は RVP のコンフィグ(アプリ全体で共有・シナリオ非依存)。
    - **=231 の既定**: 縮尺=1秒 / 位置[pos]=5単位 / 時間[at]=0.05秒単位。
      rotate系・vibration では**階段**で描く。
    """

    GRAPH_H = 300
    LEVEL_1S = 3              # =231: 縮尺表示が「1秒」になる段(LEVELS)
    GRID_POS_DEF = 5          # =231: 位置[pos]の既定
    GRID_AT_DEF = 50          # =231: 時間[at]の既定(0.05秒)

    def __init__(self, master, owner=None, kind: str = "linear"):
        super().__init__(master)
        from . import script_edit
        self._se = script_edit
        self.owner = owner            # ItemReviewDialog(保存後の反映先)
        self.kind = kind if kind in script_edit.USER_PAT_PREFIX else "linear"
        self.title(tr("ユーザーパターン編集") + f"（{self.kind}）")
        self.resizable(True, False)
        self._patterns = script_edit.load_user_patterns(load_config(),
                                                        self.kind)
        self._keys = script_edit.user_pattern_keys(self.kind)
        # =231 要望4: 既定は**未登録のうち一番小さい枠**(全部埋まって
        # いれば最後の枠=X20)
        self._key = self._keys[
            script_edit.first_free_slot(self._patterns, self.kind) - 1]
        self.model = script_edit.ScriptEditModel()

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=(12, 12))

        # ---- 1段目: 枠の切り替え ----
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text=tr("編集する枠:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left", anchor="n", padx=(0, 6), pady=(2, 0))
        # =218: 20枠になったので **2段×10枠** の grid で並べる(ユーザー決定。
        # 1列20個だと popup の幅が 1400 必要になる)。
        slots = ctk.CTkFrame(row1, fg_color="transparent")
        slots.pack(side="left")
        self.slot_btns = {}
        for j, ukey in enumerate(self._keys):
            b = ctk.CTkButton(
                slots, text=ukey, width=46, height=26,
                font=ctk.CTkFont(size=12), fg_color="transparent",
                border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=lambda k=ukey: self._select_slot(k))
            b.grid(row=j // 10, column=j % 10, padx=(0, 4), pady=(0, 4),
                   sticky="w")
            self.slot_btns[ukey] = b
        self.state_label = ctk.CTkLabel(row1, text="",
                                        font=ctk.CTkFont(size=11),
                                        text_color=TEXT_MUTED)
        self.state_label.pack(side="left", anchor="n", padx=(10, 0),
                              pady=(4, 0))

        # ---- 2段目: グリッド(編集モードと同じ選択肢) ----
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(6, 0))
        none_lbl = tr("なし")
        ctk.CTkLabel(row2, text=tr("位置[pos]:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self._grid_pos_map = {script_edit.grid_label(g, none_lbl): g
                              for g in script_edit.GRID_POS_CHOICES}
        self.grid_pos_var = tk.StringVar(value=script_edit.grid_label(
            self.GRID_POS_DEF, none_lbl))          # =231: 既定は 5単位
        CTkOptionMenu(
            row2, variable=self.grid_pos_var, width=84, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_pos_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change()
        ).pack(side="left", padx=(4, 10))
        ctk.CTkLabel(row2, text=tr("時間[at]:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self._grid_at_map = {script_edit.grid_at_label(g, none_lbl): g
                             for g in script_edit.GRID_AT_CHOICES}
        self.grid_at_var = tk.StringVar(value=script_edit.grid_at_label(
            self.GRID_AT_DEF, none_lbl))           # =231: 既定は 0.05秒
        CTkOptionMenu(
            row2, variable=self.grid_at_var, width=100, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_at_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change()
        ).pack(side="left", padx=(4, 10))

        # ---- グラフ(点だけの編集) ----
        self.graph = script_edit.ScriptEditGraph(
            inner, self.model, on_change=self._on_change,
            on_select=lambda: None, on_menu=self._menu,
            height=self.GRAPH_H)
        self.graph.plain = True       # =206: 再生位置の線・追従バッジなし
        # =231 要望5: rotate系・vibration は**階段**で描く(次の指示まで
        # 値を保つ=編集画面と同じ見え方)。ヒートマップも出さない。
        self.graph.step = self.kind in SCRIPT_EDIT_STEP_TYPES
        self.graph.heat = not self.graph.step
        self.graph.pack(fill="both", expand=True, pady=(6, 0))
        ctk.CTkLabel(
            inner,
            text=tr("点だけで編集します（パターンは使えません）。"
                    "最後の点の時間が基準長になります"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w",
        ).pack(fill="x", pady=(4, 0))

        # ---- 下段: 保存/メッセージ ----
        row3 = ctk.CTkFrame(inner, fg_color="transparent")
        row3.pack(fill="x", pady=(8, 0))
        self.save_btn = ctk.CTkButton(
            row3, text=tr("保存"), width=96, height=30, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, command=self._save)
        self.save_btn.pack(side="left")
        self.msg_label = ctk.CTkLabel(row3, text="",
                                      font=ctk.CTkFont(size=11),
                                      text_color=TEXT_MUTED, anchor="w")
        self.msg_label.pack(side="left", padx=(10, 0), fill="x", expand=True)

        # =218: 枠が20個(2段)になったぶん少しだけ縦を足す(幅は据え置き)
        _place_popup(self, master, 840, 500)
        _front_window(self)
        self._load_slot(self._key)
        self._on_grid_change()

    # ---- 枠の切り替え ----

    def _select_slot(self, key: str):
        if key == self._key:
            return
        if self.model.dirty and not self._confirm_discard():
            return
        self._load_slot(key)

    def _confirm_discard(self) -> bool:
        """未保存の編集がある枠から離れる確認。テスト用の自動応答つき。"""
        if USER_PAT_UNSAVED_AUTO is not None:
            return bool(USER_PAT_UNSAVED_AUTO)
        win = ctk.CTkToplevel(self)
        win.title("RVP")
        result = {"ok": False}
        ctk.CTkLabel(win, justify="left",
                     text=tr("保存していない編集があります。\n"
                             "破棄して切り替えますか？"),
                     font=ctk.CTkFont(size=13)).pack(padx=24, pady=(18, 10))
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(pady=(0, 14))

        def done(ok):
            result["ok"] = ok
            win.destroy()
        ctk.CTkButton(btns, text=tr("破棄する"), width=90, height=28,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=lambda: done(True)).pack(side="left", padx=4)
        ctk.CTkButton(btns, text=tr("キャンセル"), width=90, height=28,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      command=lambda: done(False)).pack(side="left", padx=4)
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return result["ok"]

    def _load_slot(self, key: str):
        self._key = key
        shape = self._patterns.get(key)
        self.model.load(list(shape) if shape else [])
        span = shape[-1][0] if shape else 0
        # =206: initial_view は view_ms を now_ms から作るので、now_ms は
        # 常に 0 のままにする(=205 では now_ms を画面外へ飛ばしていたため、
        # 2回目以降の枠切り替えで view ごと画面外へ飛び、表示も打点も
        # できなくなっていた)。再生位置の線は plain フラグで消す。
        self.graph.now_ms = 0.0
        self.graph.initial_view(span)
        # =231 要望2: 既定の拡大率は**縮尺1秒**(initial_view は素材長から
        # 段を選ぶが、ユーザーパターンは短いので拡大側で固定する)
        self.graph.set_level(self.LEVEL_1S)
        self.graph.view_ms = 0.0
        self.graph.follow = False
        self.graph.redraw()
        for ukey, b in self.slot_btns.items():
            on = ukey == key
            b.configure(fg_color=ACCENT if on else "transparent",
                        border_width=0 if on else 1,
                        text_color=("white", "white") if on
                        else ("gray20", "gray85"),
                        hover_color=ACCENT_HOVER if on
                        else ("gray85", "gray25"))
        self.state_label.configure(
            text=tr("登録済み（基準長 {0}ms）").format(span) if shape
            else tr("未登録"))
        self.msg_label.configure(text="", text_color=TEXT_MUTED)

    # ---- 編集 ----

    def _on_grid_change(self):
        self.graph.grid_pos = self._grid_pos_map.get(
            self.grid_pos_var.get(), self.GRID_POS_DEF)
        self.graph.grid_at = self._grid_at_map.get(
            self.grid_at_var.get(), self.GRID_AT_DEF)
        self.graph.redraw()

    def _on_change(self):
        self.msg_label.configure(text="", text_color=TEXT_MUTED)

    def _menu(self, event, ctx):
        """右クリックメニュー: 点の削除だけ(グループ化・パターンは無し)。"""
        if ctx.get("at") is None or not self.model.selection:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=tr("削除"), command=self._menu_delete)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _menu_delete(self):
        if self.model.delete_selected():
            self.graph.redraw()
            self._on_change()

    # ---- 保存 ----

    def _save(self):
        shape = self._se.normalize_user_shape(self.model.points)
        if shape is None:
            self.msg_label.configure(
                text=tr("2点以上を打ってください（幅0は登録できません）"),
                text_color=MSG_ERROR)
            return
        cfg = load_config()
        self._se.save_user_pattern(cfg, self._key, shape)
        save_config(cfg)
        self._patterns[self._key] = shape
        # 正規化(先頭atの0ずらし)の結果を画面へも反映する
        self.model.load(list(shape))
        self.model.dirty = False
        self.graph.redraw()
        self.state_label.configure(
            text=tr("登録済み（基準長 {0}ms）").format(shape[-1][0]))
        self.msg_label.configure(text=tr("保存しました") + f": {self._key}",
                                 text_color=MSG_OK)
        if self.owner is not None:
            try:
                self.owner._on_user_patterns_saved()
            except Exception:
                pass


# =170: テスト用フック。未保存確認は "save"/"discard"/"cancel"、
# 上書き/削除の確認は True(実行)/False(キャンセル) を即座に返す。
# 実行時はどちらも None(実際の確認ダイアログを出す)。
SCRIPT_EDIT_UNSAVED_AUTO: str | None = None
SCRIPT_EDIT_CONFIRM_AUTO: bool | None = None


def _review_range_ms(start_var, end_var) -> tuple:
    """区間欄の値を (開始ms, 終了ms|None, 指定あり, 妥当) にする(=164)。

    保存前の入力途中の値を読むので、不正な値は**エラーにせず「指定なし」**
    として扱い、妥当=False を返す(呼び出し側が画面に注意書きを出す)。
    """
    vals = []
    ok = True
    for var in (start_var, end_var):
        txt = (var.get() or "").strip()
        if not txt:
            vals.append(None)
            continue
        try:
            vals.append(float(txt))
        except ValueError:
            vals.append(None)
            ok = False
    lo, hi = vals
    if lo is not None and lo < 0:
        lo, ok = None, False
    if hi is not None and hi <= (lo or 0.0):
        hi, ok = None, False
    given = bool(lo) or hi is not None
    return (lo or 0.0) * 1000.0, (None if hi is None else hi * 1000.0), given, ok


def _review_segments(ttype: str, src) -> list:
    """スクリプト1本を DeviceGraph のスナップショット断片へ変換する(=164)。

    種別→行(key)と線の描き方(kind)の対応は player._graph_add_track と同じ。
    2ch(5列csv)の rotate は左右2行に分かれる。レビューは1本のスクリプトを
    先頭から通して見るだけなので、断片の位置は t0=0 / x0=0 で固定する。
    """
    from .player import ScenarioPlayer as _SP

    def seg(key, kind, points):
        return {"key": key, "kind": kind, "points": points,
                "times": [p[0] for p in points],
                "x0": 0.0, "x1": None, "t0": 0.0, "live": False}

    if ttype == "linear":
        return [seg("linear", "linear", _SP._graph_points_funscript(src))]
    if ttype == "twist":
        return [seg("twist", "linear", _SP._graph_points_funscript(src))]
    if ttype == "vibration":
        return [seg("vibration", "step", _SP._graph_points_funscript(src))]
    if ttype in REVIEW_ROTATE_TYPES:
        base = "rotate_ufo" if ttype == "rotate_ufo" else "rotate_a10"
        if getattr(src, "channels", 1) >= 2:
            return [seg(base + ("_r" if r else ""), "step",
                        _SP._graph_points_rotate(src, r)) for r in (0, 1)]
        return [seg(base, "step", _SP._graph_points_rotate(src, 0))]
    return []


class ItemReviewDialog(ctk.CTkToplevel):
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
        # 非モーダル: grab_set しない(編集画面を見ながら確認できるように)

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

    # ---------------- 画面の組み立て ----------------

    def _build_ui(self):
        from .main import DeviceGraph, FixedBtn   # 遅延import(循環を避ける)

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
            progress_color=ACCENT, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER)
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
            font=ctk.CTkFont(size=20), fg_color=ACCENT,
            hover_color=ACCENT_HOVER, command=self.toggle_play)
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
            height=16, progress_color=ACCENT, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER, command=self._on_volume_change)
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

    # ---------------- 内容の読み込み ----------------

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
            from .player import ScenarioPlayer as _SP
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
            from .player import describe_audio_load_error
            extra = describe_audio_load_error(path, e)
            return None, (tr("音声を再生できません: {0}").format(e)
                          + (("\n" + extra) if extra else ""))
        return snd, ""

    # ---- =243: 音声波形(エンベロープの計算と表示モード) ----

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
                from .player import sound_byte_view
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

    # ---------------- 再生 ----------------

    def _pause_main(self):
        """本編が再生中なら止める。戻り値=初期音量(0.0〜1.0)or None。"""
        fn = getattr(self.host, "pause_for_review", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:
            return None

    # ---------------- 動画レビュー(=203) ----------------

    def _video_offset(self) -> float:
        """RVPの0秒に対応する動画側の位置(ms)。レビュー=区間の開始、
        編集モード=素材全体(0)。"""
        return 0.0 if self.edit_mode else self._video_lo

    def _refresh_vol_visibility(self):
        """動画モードでは音量スライダーを隠す(mpv の音声には触れない=
        合意事項4。音量は mpv 側で操作してもらう)。"""
        try:
            if self._video:
                self.vol_box.pack_forget()
            elif not self.vol_box.winfo_ismapped():
                # =214: 速度コンボの左へ戻す(pack 順を保つ)
                self.vol_box.pack(side="left", padx=(14, 0),
                                  before=self.speed_box)
        except Exception:
            pass

    def _video_open(self, seek_ms: float) -> str:
        """mpv を用意して動画を開く。失敗理由の文字列を返す(成功="")。"""
        mpv_path = _mpv_path_setting()
        if not mpv_path:
            return tr("mpv が見つからないため、動画は再生できません"
                      "（メイン画面の Settings でパスを指定してください）")
        holder = self.owner if self.owner is not None else self
        try:
            self._mpv = _hold_editor_mpv(holder, mpv_path)
            self._mpv.open(self._video, max(0.0, seek_ms) / 1000.0)
            if abs(self._speed - 1.0) > 1e-9:
                self._mpv.set_speed(self._speed)      # =214
            self._mpv_cmd_t = time.monotonic()
        except Exception as exc:
            self._mpv = None
            return tr("mpv を起動できませんでした: {0}").format(exc)
        return ""

    def _apply_video_duration(self):
        """mpv 実測の全長を全長へ反映する(区間・スクリプトと合成)。"""
        vl = self._video_len
        if vl <= 0:
            return
        if self.edit_mode:
            self._edit_refresh_duration()
        else:
            lo, hi = self._video_lo, self._video_hi
            end = min(float(hi), vl) if hi is not None else vl
            vd = max(0.0, end - lo)
            if lo >= vl:
                self.warn_label.configure(
                    text=tr("区間の開始が動画の長さを超えています"))
                self.warn_label.pack(fill="x", padx=14, pady=(4, 0),
                                     before=self.transport_card)
            self._duration_ms = int(max(vd, self._script_ms))
            for seg in self._segments:
                seg["x1"] = float(self._duration_ms)
            self.info_label.configure(text=self._info_text())
        self._update_time(self._now_ms(), force=True)
        self._sync_buttons()

    def _video_tick(self):
        """毎フレームの mpv 同期(=203)。再生タブと同じ考え方:
        ①実長の反映 ②クロックを time-pos へ従属(50ms超のみ補正)
        ③mpv 側の一時停止/再開の取り込み ④切断=一時停止扱い。"""
        m = self._mpv
        if m is None:
            return
        st = m.state
        d = st.get("duration")
        if d and abs(d * 1000.0 - self._video_len) > 1.0:
            self._video_len = d * 1000.0
            self._apply_video_duration()
        tp = st.get("time_pos")
        if tp is not None and self._clock is not None and self._playing:
            ms = tp * 1000.0 - self._video_offset()
            ms = max(0.0, min(float(ms), float(self._duration_ms)))
            if abs(ms - self._clock.now_ms()) > 50:
                self._clock.set_ms(ms)
        own_recent = (time.monotonic() - self._mpv_cmd_t) <= 0.6
        pv = st.get("pause")
        if pv is not None and st.get("connected") and not own_recent:
            if pv and self._playing:
                # mpv 側で一時停止された
                self._playing = False
                self._clock.pause()
                if self.edit_mode and self._edit_built:
                    self.edit_graph.set_playing(False)
                self._sync_buttons()
            elif (not pv) and (not self._playing) and                     self._now_ms() < self._duration_ms - 1:
                # mpv 側で再開された
                self._playing = True
                self._clock.resume()
                if self.edit_mode and self._edit_built:
                    self.edit_graph.follow = True
                    self.edit_graph.set_playing(True)
                self._sync_buttons()
        if not st.get("connected") and self._playing:
            # mpv が閉じられた/落ちた: 一時停止扱い(次の▶で開き直す)
            self._playing = False
            self._clock.pause()
            if self.edit_mode and self._edit_built:
                self.edit_graph.set_playing(False)
            self._sync_buttons()

    def _reset_clock(self):
        from .player import PlaybackClock
        self._clock = PlaybackClock()
        self._clock.start()
        self._clock.pause()          # 0秒で停止した状態から始める
        self._clock.set_rate(getattr(self, "_speed", 1.0))   # =214

    def _now_ms(self) -> float:
        if self._clock is None:
            return 0.0
        return max(0.0, min(float(self._duration_ms), self._clock.now_ms()))

    def _on_space_key(self, _event=None):
        """=231: スペース=再生/一時停止。入力欄では普通の空白を入れる。"""
        if self._key_target_is_entry():
            return None
        if self._duration_ms <= 0:
            return "break"
        self.toggle_play()
        return "break"

    def toggle_play(self):
        if self._playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self._duration_ms <= 0:
            return
        self._pause_main()
        # 末尾まで来ていたら頭から鳴らし直す(再生タブの「停止後に▶」と同じ)
        if self._now_ms() >= self._duration_ms - 1:
            self._clock.set_ms(0.0)
            if self._video and self._mpv is not None:
                self._mpv.seek(self._video_offset() / 1000.0)
        if self._video:
            # =203: mpv が閉じられていたら開き直してから再生する
            if self._mpv is None or not self._mpv.state.get("connected"):
                err = self._video_open(self._video_offset() + self._now_ms())
                if err:
                    self.warn_label.configure(text=err)
                    self.warn_label.pack(fill="x", padx=14, pady=(4, 0),
                                         before=self.transport_card)
                    return
            self._mpv.set_pause(False)
            self._mpv_cmd_t = time.monotonic()
        self._clock.resume()
        self._playing = True
        # =170: 編集モードでは単クリックが打点になり「クリックで追従復帰」が
        # 使えないため、再生ボタンで自動追従ONへ戻す(仕様 8b)。
        if self.edit_mode and self._edit_built:
            self.edit_graph.follow = True
            self.edit_graph.set_playing(True)   # =198: 再生位置を中央へ
        self._play_audio_from(self._now_ms())
        self._sync_buttons()
        self._schedule_tick()

    def pause(self):
        if self._clock is not None:
            self._clock.pause()
        self._playing = False
        if self._video and self._mpv is not None:
            self._mpv.set_pause(True)
            self._mpv_cmd_t = time.monotonic()
        if self.edit_mode and self._edit_built:
            self.edit_graph.set_playing(False)
        self._pause_audio()
        self._sync_buttons()

    def seek_back10(self):
        self.seek(self._now_ms() - 10_000)

    def seek_fwd10(self):
        self.seek(self._now_ms() + 10_000)

    def seek(self, ms: float):
        """再生位置を移動する(音声・グラフとも同じ時間軸で動く)。"""
        if self._duration_ms <= 0 or self._clock is None:
            return
        ms = max(0.0, min(float(ms), max(self._duration_ms - 200, 0)))
        self._clock.set_ms(ms)
        if self._video:
            if self._mpv is not None:
                self._mpv.seek((self._video_offset() + ms) / 1000.0)
                self._mpv_cmd_t = time.monotonic()
        elif self._playing:
            self._play_audio_from(ms)
        else:
            self._stop_audio()
        self._update_time(ms, force=True)

    # ---- 音声(pygame.mixer の REVIEW_SLOT 番チャンネル) ----

    def _channel(self):
        try:
            import pygame
            if not pygame.mixer.get_init():
                return None
            return pygame.mixer.Channel(REVIEW_SLOT)
        except Exception:
            return None

    def _play_audio_from(self, ms: float):
        """指定位置から鳴らす(区間切り出し済みSoundのさらに途中から)。
        =203: 動画モードでは何もしない(音は mpv 側)。"""
        if self._video or self._sound is None:
            return
        ch = self._channel()
        if ch is None:
            return
        try:
            from .player import ScenarioPlayer as _SP
            # =214: 速度 r のときは r 倍済み Sound の ms/r 位置から鳴らす
            src = self._rated_sound()
            ms_r = ms / self._speed if self._speed > 0 else ms
            snd = src if ms_r <= 0 else _SP._sliced_sound(src, ms_r)
            if snd is None:
                return
            ch.play(snd)
            ch.set_volume(self._volume, self._volume)
        except Exception:
            pass

    def _pause_audio(self):
        ch = self._channel()
        if ch is not None:
            try:
                ch.pause()
            except Exception:
                pass

    def _stop_audio(self):
        ch = self._channel()
        if ch is not None:
            try:
                ch.stop()
            except Exception:
                pass

    # ---- =214: 再生速度 ----

    @staticmethod
    def _speed_label(v: float) -> str:
        return "x{0:.1f}".format(v)

    def set_speed(self, rate: float):
        """再生速度を変える(=214)。クロックの rate・音声の鳴らし直し・
        mpv の speed。now_ms は素材上の時刻のまま(Fキー配置に影響なし)。"""
        rate = float(rate)
        if rate <= 0:
            rate = 1.0
        if abs(rate - self._speed) < 1e-9:
            return
        self._speed = rate
        if self._clock is not None:
            self._clock.set_rate(rate)
        if self._video:
            if self._mpv is not None:
                try:
                    self._mpv.set_speed(rate)
                except Exception:
                    pass
        elif self._playing:
            self._play_audio_from(self._now_ms())

    def _on_speed_change(self):
        self.set_speed(self._speed_map.get(self.speed_var.get(), 1.0))

    def _rated_sound(self):
        """今の速度用の Sound(=214。rate=1.0 は原本)。キャッシュ1つ。"""
        if self._sound is None:
            return None
        if abs(self._speed - 1.0) < 1e-9:
            return self._sound
        c = self._rate_cache
        if c and c[0] == id(self._sound) and abs(c[1] - self._speed) < 1e-9:
            return c[2]
        try:
            from .player import ScenarioPlayer as _SP
            snd = _SP._rated_sound(self._sound, self._speed)
        except Exception:
            snd = None
        if snd is None:
            return self._sound
        self._rate_cache = (id(self._sound), self._speed, snd)
        return snd

    def _on_volume_change(self, value):
        self._volume = max(0.0, min(1.0, float(value) / 100.0))
        ch = self._channel()
        if ch is not None:
            try:
                ch.set_volume(self._volume, self._volume)
            except Exception:
                pass

    # ---- シークバー ----

    def _on_seek_press(self, _event):
        if self._duration_ms > 0:
            self._dragging = True

    def _on_seek_release(self, _event):
        if not self._dragging:
            return
        self._dragging = False
        self.seek(self.seek_slider.get() / 1000.0 * self._duration_ms)

    # ---------------- 表示の更新 ----------------

    def _sync_buttons(self):
        active = self._duration_ms > 0
        state = "normal" if active else "disabled"
        self.btn_play.configure(text="❚❚" if self._playing else "▶",
                                state=state)
        for b in (self.btn_back10, self.btn_fwd10):
            b.configure(state=state)
        self.seek_slider.configure(state=state)

    @staticmethod
    def _fmt_ms(ms: float) -> str:
        s = max(0.0, ms) / 1000.0
        return "{0:02d}:{1:04.1f}".format(int(s // 60), s % 60)

    def _update_time(self, now_ms: float, force: bool = False):
        # =229: 60fps では 1/10 秒表示のラベルは同じ文字のことが多い。
        # 変わったときだけ configure する(CTkLabel の再設定は重い)。
        txt = "{0} / {1}".format(self._fmt_ms(now_ms),
                                 self._fmt_ms(self._duration_ms))
        if txt != self._time_text:
            self._time_text = txt
            self.time_label.configure(text=txt)
        if not self._dragging:
            pos = 0.0
            if self._duration_ms > 0:
                pos = min(1000.0, now_ms / self._duration_ms * 1000.0)
            # =229: CTkSlider.set() は毎回スライダーを描き直す。目盛は
            # 1000段なので、**1段ぶん動いたときだけ**動かす(60fps 対策)。
            if self._seek_step != int(pos):
                self._seek_step = int(pos)
                self.seek_slider.set(pos)
        if force or now_ms != self._last_draw:
            self._last_draw = now_ms
            if self.edit_mode and self._edit_built:
                self.edit_graph.set_now(now_ms)
            elif self._segments:
                self.graph_view.set_snapshot({"now_ms": now_ms,
                                              "segments": self._segments})

    @classmethod
    def _load_refresh_ms(cls) -> int:
        """=229: 設定「描画更新頻度」(graph_fps)から更新間隔[ms]を決める。

        再生タブ(`RVPApp._graph_interval_ms`)と同じ値・同じ既定(60fps)。
        """
        try:
            gfps = load_config().get("graph_fps")
        except Exception:
            gfps = None
        return 33 if gfps == 30 else 16

    def _schedule_tick(self):
        if self._tick_job is None:
            # =229: 待ちの決め方は再生タブと共通(main.paced_delay)。
            # フレーム開始基準で設定どおりの fps を狙いつつ、重い描画の
            # ときは待ちを描画時間以上にして操作の応答を守る。
            from .main import paced_delay
            delay = paced_delay(self.refresh_ms, self._draw_cost_ms)
            try:
                self._tick_job = self.after(delay, self._tick)
            except Exception:
                self._tick_job = None

    def _cancel_tick(self):
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _tick(self):
        self._tick_job = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        _t0 = time.perf_counter()
        if self._video:
            self._video_tick()            # =203: mpv 同期
        now = self._now_ms()
        if self._playing and self._duration_ms > 0 \
                and now >= self._duration_ms:
            # 末尾に到達: 止めて終端で待たせる(再生タブと同じ「残す」動作)
            self._playing = False
            self._clock.pause()
            self._clock.set_ms(self._duration_ms)
            self._stop_audio()
            if self._video and self._mpv is not None:
                self._mpv.set_pause(True)     # =203: 区間の終端で mpv も停止
                self._mpv_cmd_t = time.monotonic()
            if self.edit_mode and self._edit_built:
                self.edit_graph.set_playing(False)
            self._sync_buttons()
            now = float(self._duration_ms)
        self._update_time(now)
        self._draw_cost_ms = (time.perf_counter() - _t0) * 1000.0
        self._schedule_tick()

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

    # ---------------- 後始末 ----------------

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

    # ================ =170: スクリプト編集モード ================

    def _toggle_edit_mode(self):
        if self.edit_mode:
            self._return_to_review()
        else:
            self._enter_edit_mode()

    # ---- モードの出入り ----

    def _enter_edit_mode(self):
        self.pause()
        if not self._edit_built:
            self._build_edit_ui()
        # 編集対象候補 = spec.tracks のうち編集できる種別(仕様 4.1。
        # 自動モードの解決結果も review_spec 経由で含まれている)。
        # 同じ (種別, パス) の重複は除く。
        # =224: csv(ROTATE)は **チャンネルごとに1候補**(5列=左/右の2つ)。
        # 候補は (種別, パス, lo, hi, ch) の5つ組。ch=None は funscript。
        # =225: rotate系・vibration の funscript も候補に入る(階段で編集)。
        seen = set()
        self._edit_tracks = []
        for ttype, path, lo, hi in self.spec.get("tracks") or []:
            csv = ttype in SCRIPT_EDIT_CSV_TYPES and _is_csv(path)
            if ttype not in SCRIPT_EDIT_TYPES and not csv:
                continue
            if not csv and _is_csv(path):
                continue          # 保険: rotate 以外の csv は扱わない
            key = (ttype, os.path.normcase(os.path.abspath(path)))
            if key in seen:
                continue
            seen.add(key)
            if not csv:
                self._edit_tracks.append((ttype, path, lo, hi, None))
                continue
            # =232: **5列(左右独立)は1項目にまとめ、開いたら左右2本を
            # 同時に編集する**(=224 の「（左）」「（右）」に分ける方式は廃止)
            ch = 0
            try:
                from . import script_edit as _se
                cols, _pts, allp = _se.load_csv_points(path, 0)
                ch = (0, 1) if cols == 5 else 0
                del allp
            except Exception:
                ch = 0           # 壊れた csv も候補には出す(開いた時に警告)
            self._edit_tracks.append((ttype, path, lo, hi, ch))
        self.edit_mode = True
        self.edit_btn.configure(text=tr("レビューに戻る"))
        # レビューのグラフを隠し、編集モードの部品を「閉じる」行の前へ差し込む
        # (pack_forget は pack 順を失うので before= で入れる=仕様 7.3)
        self.graph_box.pack_forget()
        # =219: ボタン行(side="bottom")より後ろへ詰める=縮むのはこちら側
        self.edit_wrap.pack(fill="both", expand=True, padx=14, pady=(8, 0))
        # トラックコンボを組み立てる
        # =227: 末尾に必ず「(新規作成)」を足す(別の種別のトラックを
        # あとから足せるようにする=要望5)
        if self._edit_tracks:
            # =234(要望2): 一覧に「(新規作成)」は並べない。新規作成へ入る
            # 導線は **「＋新規」ボタンだけ**(押したときだけコンボの表示が
            # 「(新規作成)」になる)。候補が0本のときは従来どおり。
            values = [self._edit_track_label(i)
                      for i in range(len(self._edit_tracks))]
            self.edit_type_menu.pack_forget()
            self._edit_new = False
        else:
            values = [tr("(新規作成)")]
            self.edit_type_menu.pack(side="left", padx=(6, 0))
            self._edit_new = True
        self.edit_track_menu.configure(values=values)
        self.edit_track_var.set(values[0])
        # 素材全体の音声(区間を無視=仕様 4.2/10)
        self._edit_sound = None
        audio = self.spec.get("audio") or ""
        edit_warns = []
        if audio:
            snd, err = self._load_audio(audio, 0.0, None)
            if err:
                edit_warns.append(err)
            else:
                self._edit_sound = snd
        self._edit_warn_base = edit_warns
        self._start_wave_env()           # =243: 編集グラフへも波形を配る
        self._edit_load_track(0 if self._edit_tracks else -1)
        self._apply_edit_layout()

    def _leave_edit_ui(self):
        """編集モードのUIを畳む(確認は済んでいる前提)。"""
        self.pause()
        self._save_edit_height()          # =211
        self.edit_mode = False
        self._edit_sound = None
        self.edit_btn.configure(text=tr("スクリプト編集"))
        if self._edit_built:
            self._chain_stop()            # =222: 数珠つなぎの監視を止める
            self._show_sub(-1)            # =227: サブ表示も畳む
            self._set_edit_pair(False)    # =232: 右(ロータ2)も畳む
            self.edit_wrap.pack_forget()

    def _return_to_review(self):
        """「レビューに戻る」。未保存確認→レビュー内容を読み直す。"""
        if not self._resolve_unsaved():
            return
        self._leave_edit_ui()
        # 新規保存でトラック構成が変わっている可能性があるため、行が
        # 生きていれば spec を取り直す(仕様 11)。自動再生はしない。
        spec = self.spec
        row = self._row
        if row is not None:
            try:
                if row.winfo_exists() and \
                        getattr(row, "audio_rel", None) == self._row_audio_rel:
                    spec = row.review_spec()
            except Exception:
                pass
        self.load(spec, autoplay=False)

    # ---- =224: csv(ROTATE)編集モードの切り替え ----

    @staticmethod
    def _new_kind_of(label: str):
        """新規作成コンボの表示名 → (種別, kind, 列数)。"""
        for name, spec in SCRIPT_EDIT_NEW_KINDS:
            if name == label:
                return spec
        return ("linear", "funscript", 0)

    def _pat_mode(self) -> str:
        """=226: パターンの考え方(linear / rotate / vib)。

        linear/twist = 従来どおり(クリックした高さへ置ける・接続・追従あり)。
        rotate(csv も funscript も)= 定義どおりの高さで固定・中心 50。
        vib = 定義どおりの高さで固定・基準 0。
        """
        return self._pat_mode_of(self._edit_type)

    @staticmethod
    def _pat_mode_of(ttype: str) -> str:
        from . import script_edit
        if ttype in SCRIPT_EDIT_CSV_TYPES:
            return script_edit.PAT_MODE_ROTATE
        if ttype == "vibration":
            return script_edit.PAT_MODE_VIB
        return script_edit.PAT_MODE_LINEAR

    def _edit_pos_max(self) -> int:
        """=297: 編集モデルの pos 分解能。csv=200(速度 1 刻み) / それ以外 100。"""
        from . import script_edit
        return script_edit.CSV_POS_MAX \
            if getattr(self, "_edit_kind", "funscript") == "csv" else 100

    def _pat_center_scaled(self, mode: str):
        """=297: PAT_CENTERS(0〜100 定義)を編集モデルの分解能へ換算した中心。
        ROTATE: funscript=50 / csv=100。VIBRATION=0。linear/twist=None。"""
        from . import script_edit
        c = script_edit.PAT_CENTERS.get(mode)
        if c is None:
            return None
        return int(round(c * self._edit_pos_max() / 100.0))

    def _std_patterns(self) -> tuple:
        """いま使う標準パターンのカタログ(=226。モードで切り替わる)。"""
        from . import script_edit
        return script_edit.std_patterns(self._pat_mode())

    def _apply_edit_kind(self):
        """編集中の種類(funscript / csv)に合わせて画面の作法を切り替える。

        csv は **階段(次の点まで同じ値)**・**時刻は100ms単位**・
        **ヒートマップなし**(速度そのものを描いているため)。
        パターン内側の点もグリッドへ丸める(100ms格子に乗せるため)。
        """
        from . import script_edit
        csv = getattr(self, "_edit_kind", "funscript") == "csv"
        # =225: rotate系・vibration の funscript も**階段**で描く
        # (次の指示まで値を保つ=csv と同じ意味づけ)。
        step = csv or self._edit_type in SCRIPT_EDIT_STEP_TYPES
        for g in self.edit_graphs:            # =232: 左右2本とも同じ作法
            g.step = step
            # 速度そのもの・強さそのものを描いているので、ストローク速度の
            # 警告(ヒートマップ)は linear/twist のときだけ意味を持つ
            g.heat = not step
            g.round_interior = csv
        mode = self._pat_mode()
        # =297: csv は分解能 200(中心 100)。パターンの中心も分解能に合わせる
        center = self._pat_center_scaled(mode)
        for m in self.edit_models:
            m.pos_max = self._edit_pos_max()
            m.round_interior = csv
            m.step_mode = step
            m.pat_center = center
        g = self.edit_graph
        # =226: 離散的なスクリプトのパターン規則
        #  ・定義どおりの高さで固定(ROTATE=中心50(csv は 100) / VIBRATION=基準0)
        #  ・接続配置なし / 隣接パターンの追従なし
        for g2 in self.edit_graphs:
            g2.pat_center = center
            g2.pat_follow = mode == script_edit.PAT_MODE_LINEAR
            g2.pat_connect = mode == script_edit.PAT_MODE_LINEAR
        # 標準パターンのカタログとボタンの絵を差し替える(モードで変わる)
        if getattr(self, "_pat_mode_cur", None) != mode:
            self._pat_mode_cur = mode
            self._set_edit_tool("point")
            self._rebuild_pattern_icons()
        # =231: ユーザーパターンは**種別ごと**なので、種別が変わったら
        # 枠を読み直して名前・絵・バッジを付け替える(ufo↔a10 のように
        # パターンモードが同じでも種別が違えば別の20枠)。
        if getattr(self, "_user_kind_cur", None) != self._edit_type:
            self._user_kind_cur = self._edit_type
            if getattr(self, "edit_user_btns", None):
                self._user_patterns = script_edit.load_user_patterns(
                    load_config(), self._user_kind())
                if isinstance(self._edit_tool, tuple):
                    self._set_edit_tool("point")
                self._refresh_user_pattern_btns()
                self._refresh_fkey_badges()
        # =226: VIBRATION では上下反転は使わない(0↔100 が入れ替わって
        # 「停止が最大」になってしまうため。ユーザー決定)
        vib = mode == script_edit.PAT_MODE_VIB
        if vib and self._edit_invert:
            self._on_invert_btn()
        self.edit_invert_btn.configure(state="disabled" if vib else "normal")
        # =233(要望6): csv は at/pos ではなく「時間(100ms)」「回転速度」。
        # 数値も csv の生の値(時間=100ms単位 / 速度=-100〜100)で見せる。
        for g2 in self.edit_graphs:
            g2.pos_axis = "speed" if csv else "pos"
        if getattr(self, "grid_at_label", None) is not None:
            self.grid_at_label.configure(
                text=tr("時間(100ms):") if csv else tr("時間[at]:"))
            self.grid_pos_label.configure(
                text=tr("回転速度:") if csv else tr("位置[pos]:"))
            self.edit_at_label.configure(
                text=tr("時間(100ms):") if csv else "at(ms):")
            self.edit_pos_label.configure(
                text=tr("回転速度:") if csv else "pos:")
        # 時間[at]グリッド: csv は 100ms 未満を出さない(刻めないため)
        choices = [v for v in script_edit.GRID_AT_CHOICES
                   if not csv or v >= script_edit.CSV_AT_UNIT]
        none_lbl = tr("なし")
        self._grid_at_map = {script_edit.grid_at_label(v, none_lbl): v
                             for v in choices}
        self.edit_grid_at_menu.configure(values=list(self._grid_at_map))
        cur = self._grid_at_map.get(self.grid_at_var.get())
        if cur is None or (csv and cur < script_edit.CSV_AT_UNIT):
            self.grid_at_var.set(script_edit.grid_at_label(
                script_edit.CSV_AT_UNIT if csv
                else script_edit.GRID_AT_DEFAULT, none_lbl))
        # =297: csv は分解能 200(速度 1 刻み)なので、「回転速度:」グリッドの
        # 選択肢(20/10/5/2/なし)はそのまま速度単位になる(=296 の速度換算
        # 表示は不要になり撤回)。
        self._on_grid_change()
        # 読み方の案内(パレットの下)。csv / ROTATE / VIBRATION で文言を変える
        text = ""
        if csv:
            text = tr("csv(ROTATE): 中央0=停止 ／ 上=正回転 ／ 下=逆回転"
                      " ／ 次の点まで同じ値を保ちます ／ "
                      "時刻は100ms単位です")
        elif self._edit_type in SCRIPT_EDIT_CSV_TYPES:
            text = tr("ROTATE: 中央50=停止 ／ 上=正回転 ／ 下=逆回転 ／ "
                      "次の点まで同じ値を保ちます")
        elif self._edit_type == "vibration":
            text = tr("VIBRATION: 0=停止 〜 100=最大 ／ "
                      "次の点まで同じ値を保ちます")
        if text:
            self.edit_csv_hint.configure(text=text)
            if not self.edit_csv_hint.winfo_ismapped():
                self.edit_csv_hint.pack(fill="x", pady=(2, 0),
                                        before=self.edit_hint_label)
        else:
            self.edit_csv_hint.pack_forget()
        self._csv_pat_warned = False

    def _set_edit_pair(self, on: bool):
        """=232: 左右2本の同時編集を出し入れする(5列csv のときだけ ON)。"""
        on = bool(on)
        g0, g1 = self._edit_graphs[0], self._edit_graphs[1]
        if on == self._edit_pair:
            pass
        elif on:
            g1.pack(fill="both", expand=True, pady=(0, 0),   # =298: 上下を詰める
                    after=g0)
        else:
            if g1.winfo_ismapped():
                g1.pack_forget()
            self._edit_models[1].load([])
        self._edit_pair = on
        self._edit_active = 0
        g0.corner_text = tr("左（ロータ1）") if on else ""
        g1.corner_text = tr("右（ロータ2）") if on else ""
        g0.set_active(True, peer_mode=on)
        g1.set_active(False, peer_mode=on)
        # =243: 左右2本のときは 上(左)=L / 下(右)=R を全高で描く
        g0.wave_channel = 0 if on else None
        g1.wave_channel = 1 if on else None
        # =298(要望4-①〜③): 2本のときは縮尺表示・「追従停止中」を上だけ、
        # 時間ラベルを下だけにして、上下のグラフを詰める
        g0.show_scale = True
        g0.show_follow_hint = True
        g0.show_time = not on
        g1.show_scale = False
        g1.show_follow_hint = False
        g1.show_time = True
        self._apply_graph_heights()

    def _apply_graph_heights(self):
        """=227/=232: 出ているグラフの要求高さを揃える(expand で等分)。"""
        shown = list(self.edit_graphs)
        # pack した直後は winfo_ismapped() がまだ 0 なので、状態で見る
        if getattr(self, "edit_sub_graph", None) is not None and \
                getattr(self, "_edit_sub_idx", -1) >= 0:
            shown.append(self.edit_sub_graph)
        h = self.EDIT_GRAPH_H if len(shown) <= 1 else self.EDIT_SUB_H
        for g in shown:
            g.configure(height=h)

    def _edit_track_label(self, i: int) -> str:
        ttype, path, _lo, _hi, ch = self._edit_tracks[i]
        label = "{0}: {1}".format(ttype, os.path.basename(path))
        if isinstance(ch, tuple):
            label += tr("（左右）")        # =232: 5列csv は1項目(同時編集)
        return label

    @staticmethod
    def _csv_is_2ch(ch) -> bool:
        """=232: 5列csv(左右独立)の候補かどうか。"""
        return isinstance(ch, tuple)

    # ---- 編集モードのUI(遅延構築) ----

    def _build_edit_ui(self):
        from . import script_edit
        self._edit_built = True
        # =232: 左右2本ぶん用意する(2本目は5列csvのときだけ出す)
        self._edit_models = [script_edit.ScriptEditModel(),
                             script_edit.ScriptEditModel()]
        # =298: 左右2本のクリップボード共有と UNDO の一本化(要望2/3)
        self._edit_link = script_edit.EditLink(self._edit_models)
        self._edit_active = 0
        self._edit_pair = False
        wrap = ctk.CTkFrame(self, corner_radius=10, fg_color=BOX_BG,
                            border_width=1, border_color=BOX_BORDER)
        self.edit_wrap = wrap
        inner = ctk.CTkFrame(wrap, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=10, pady=(8, 6))

        # ---- 1段目: 対象トラック / [点] / 保存・新規保存 ----
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text=tr("対象:"), font=ctk.CTkFont(size=12)
                     ).pack(side="left")
        self.edit_track_var = tk.StringVar(value="")
        self.edit_track_menu = CTkOptionMenu(
            row1, variable=self.edit_track_var, width=250, height=26,
            font=ctk.CTkFont(size=11), values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=self._on_edit_track_change)
        self.edit_track_menu.pack(side="left", padx=(6, 0))
        # =227: 別の種別のトラックをあとから足す導線(要望5)
        self.edit_new_btn = ctk.CTkButton(
            row1, text=tr("＋新規"), width=64, height=26,
            font=ctk.CTkFont(size=11), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._on_edit_new_track)
        self.edit_new_btn.pack(side="left", padx=(6, 0))
        Tooltip(self.edit_new_btn,
                lambda: tr("このアイテムへ別の種別のトラックを新しく作る"))
        # 新規作成のときだけ出す種別コンボ
        # (=224: funscript の linear/twist に加えて csv(ROTATE 3列/5列))
        self.edit_type_var = tk.StringVar(value="linear")
        self.edit_type_menu = CTkOptionMenu(
            row1, variable=self.edit_type_var, width=190, height=26,
            font=ctk.CTkFont(size=11),
            values=[n for n, _spec in SCRIPT_EDIT_NEW_KINDS],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_edit_type_change())
        # [点] ボタンはツール枠(row1 の下)へ移設(P2 =173)
        self.edit_saveas_btn = ctk.CTkButton(
            row1, text=tr("新規保存"), width=90, height=26,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"), command=self._edit_save_as)
        self.edit_saveas_btn.pack(side="right")
        self.edit_save_btn = ctk.CTkButton(
            row1, text=tr("保存"), width=76, height=26,
            font=ctk.CTkFont(size=12), fg_color=ACCENT,
            hover_color=ACCENT_HOVER, command=self._edit_save)
        self.edit_save_btn.pack(side="right", padx=(0, 6))

        # ---- ツール枠(P2 =173/=180): [点](縦2段)+標準パターン20個(=217) ----
        # 並びは=180のユーザー指定: [点]はパターン群の左隣に縦2段ぶんの
        # 高さで置く。[上下反転](旧インバート)は2段目(位置[pos]の左)へ。
        tools = ctk.CTkFrame(inner, fg_color="transparent")
        tools.pack(fill="x", pady=(6, 0))
        self._edit_tool = "point"
        self._edit_invert = False
        self._pat_icons = []            # PhotoImage の保持(インスタンス側。
        #                                 モジュールキャッシュは 7.3 の罠)
        self.edit_point_btn = ctk.CTkButton(
            tools, text=tr("点"), width=44, height=60,
            font=ctk.CTkFont(size=12), fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self._on_point_btn)
        self.edit_point_btn.grid(row=0, column=0, rowspan=2,
                                 padx=(0, 8), pady=(0, 4), sticky="ns")
        self.edit_pattern_btns = []
        for i, (name, shape) in enumerate(script_edit.STD_PATTERNS):
            icon = self._pattern_icon(shape, step=False)
            self._pat_icons.append(icon)
            # CTkImage以外を渡すと HighDPI の警告が出る(=111 と同じ理由で
            # 黙らせる。Pillow=CTkImage は使わない方針)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                btn = ctk.CTkButton(
                    tools, text="", image=icon, width=self.PAT_BTN_W,
                    height=28,
                    fg_color="transparent", border_width=1,
                    border_color=MUTED, hover_color=("gray85", "gray25"),
                    command=lambda idx=i: self._set_edit_tool(idx))
            btn.grid(row=i // 10, column=1 + i % 10, padx=(0, 4),
                     pady=(0, 4), sticky="w")
            Tooltip(btn, lambda n=name: tr(n))
            self.edit_pattern_btns.append(btn)

        # ---- =205: ユーザーパターン U1〜U20(仕様 6.1)+編集ボタン ----
        # 未登録の枠はグレー(disabled)で押しても何も起きない。登録すると
        # 標準パターンと同じ絵柄ボタンになる。名前は U1〜U20 固定。
        # 配置(=218 ユーザー指定): U1〜U10=標準1〜10(1段目)の右、
        # U11〜U20=標準11〜20(2段目)の右。標準との間に少し隙間を空ける。
        # 「edit」ボタンは U20 の右隣(その上は空間になる)。
        # =231: **種別ごとに別々の20枠**。ボタンは枠番号(1〜20)で持ち、
        # 表示名(L1/T1/U1/A1/V1 …)は種別が変わるたびに付け替える。
        self._user_patterns = script_edit.load_user_patterns(
            load_config(), self._user_kind())
        self._user_icons = {}
        self.edit_user_btns = {}
        for j in range(script_edit.USER_PAT_SLOTS):
            slot = j + 1
            r, c = j // 10, 11 + j % 10
            btn = ctk.CTkButton(
                tools, text=self._user_key(slot), width=self.PAT_BTN_W,
                height=28,
                font=ctk.CTkFont(size=11), fg_color="transparent",
                border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=lambda n=slot: self._set_edit_tool(("user", n)))
            btn.grid(row=r, column=c, padx=(14, 4) if j % 10 == 0 else (0, 4),
                     pady=(0, 4), sticky="w")
            Tooltip(btn, lambda n=slot: (
                tr("ユーザーパターン {0}").format(self._user_key(n))
                if self._user_key(n) in self._user_patterns
                else tr("未登録（「ユーザーパターン編集」から登録）")))
            self.edit_user_btns[slot] = btn
        self.edit_userpat_btn = ctk.CTkButton(
            tools, text="edit", width=self.PAT_BTN_W, height=28,
            font=ctk.CTkFont(size=11), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._open_user_pattern_editor)
        self.edit_userpat_btn.grid(row=1, column=21, padx=(0, 4),
                                   pady=(0, 4), sticky="w")
        Tooltip(self.edit_userpat_btn, lambda: tr("ユーザーパターン編集"))
        self._refresh_user_pattern_btns()
        # ---- =212: Fキー割り当て(右クリックメニュー+右上のバッジ) ----
        # バッジは tk.Label をボタンへ重ね置き(place)。画像へ文字を描く
        # より軽く、テーマ切替でも configure で色を変えるだけで済む。
        self._fkey_map = script_edit.load_fkey_map(load_config())
        self._fkey_badges = {}          # ref → tk.Label
        self._fkey_menu = None
        # 右クリックは **ダイアログ(Toplevel)のバインドタグ** で受ける。
        # CTkButton は画像ラベルを後から作る(U枠の登録時)ため、ボタン
        # ごとに bind すると張り直しが要る。Toplevel で受けて event.widget
        # の親をたどれば、どのボタン上の右クリックかが常に分かる。
        self.bind("<Button-3>", self._on_palette_right_click, add="+")
        self._refresh_fkey_badges()

        # ---- 2段目: [上下反転] / グリッド2コンボ / (at,pos) 数値入力欄 ----
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(6, 0))
        # [上下反転](=180で「インバート」から改名し、位置[pos]の左へ移設。
        # 独立した ON/OFF。配置するパターンの上下を反転する)
        self.edit_invert_btn = ctk.CTkButton(
            row2, text=tr("上下反転"), width=72, height=26,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._on_invert_btn)
        # =193: 点ボタン(幅44+8)の下は空け、2段目左端のパターン
        # (=217以降は STD11)の直下に置く
        self.edit_invert_btn.pack(side="left", padx=(52, 10))
        Tooltip(self.edit_invert_btn,
                lambda: tr("配置するパターンの上下を反転する（画面を閉じるまで有効）"))
        # =194: 縮尺配置ボタン(x2〜x0.5・排他トグル・既定 x1.0)。
        # 標準パターンを縮尺した長さで配置する(接続配置の片側接続にも
        # 効く。両側接続は両端で長さが決まるため対象外)
        self._edit_scale = 1.0
        self.edit_scale_btns = {}
        for label, v in (("x2", 2.0), ("x1.5", 1.5), ("x1.0", 1.0),
                         ("x0.75", 0.75), ("x0.5", 0.5)):
            b = ctk.CTkButton(
                row2, text=label, width=44, height=26,
                font=ctk.CTkFont(size=12), fg_color="transparent",
                border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=lambda vv=v: self._on_scale_btn(vv))
            b.pack(side="left", padx=(0, 4))
            self.edit_scale_btns[v] = b
        self._refresh_scale_btns()
        Tooltip(self.edit_scale_btns[1.0],
                lambda: tr("パターンを配置するときの縮尺（画面を閉じるまで有効）"))
        # =233(要望5): **時間[at] を左・位置[pos] を右**へ入れ替えた
        # (右隣の at / pos の入力欄と並び順を揃えるため)。
        # ラベルは csv のときだけ「時間(100ms)」「回転速度」になる(=233 要望6)
        none_lbl = tr("なし")
        self.grid_at_label = ctk.CTkLabel(
            row2, text=tr("時間[at]:"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.grid_at_label.pack(side="left")
        self._grid_at_map = {
            script_edit.grid_at_label(g, none_lbl): g
            for g in script_edit.GRID_AT_CHOICES}
        self.grid_at_var = tk.StringVar(value=script_edit.grid_at_label(
            script_edit.GRID_AT_DEFAULT, none_lbl))
        self.grid_at_menu = CTkOptionMenu(
            row2, variable=self.grid_at_var, width=100, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_at_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change())
        self.grid_at_menu.pack(side="left", padx=(4, 10))
        # =224: csv では選択肢を 100ms 以上に差し替える(_apply_edit_kind)
        self.edit_grid_at_menu = self.grid_at_menu
        self.grid_pos_label = ctk.CTkLabel(
            row2, text=tr("位置[pos]:"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.grid_pos_label.pack(side="left")
        self._grid_pos_map = {
            script_edit.grid_label(g, none_lbl): g
            for g in script_edit.GRID_POS_CHOICES}
        self.grid_pos_var = tk.StringVar(value=script_edit.grid_label(
            script_edit.GRID_POS_DEFAULT, none_lbl))
        self.grid_pos_menu = CTkOptionMenu(
            row2, variable=self.grid_pos_var, width=86, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_pos_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change())
        self.grid_pos_menu.pack(side="left", padx=(4, 10))
        # 選択中の点の (at, pos)。Enter で確定・衝突すれば元へ戻す(仕様 4.6)
        self.edit_at_label = ctk.CTkLabel(
            row2, text="at(ms):", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.edit_at_label.pack(side="left", padx=(8, 0))
        self.edit_at_entry = ctk.CTkEntry(row2, width=76, height=24,
                                          font=ctk.CTkFont(size=11))
        self.edit_at_entry.pack(side="left", padx=(4, 6))
        self.edit_pos_label = ctk.CTkLabel(
            row2, text="pos:", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.edit_pos_label.pack(side="left")
        self.edit_pos_entry = ctk.CTkEntry(row2, width=64, height=24,
                                           font=ctk.CTkFont(size=11))
        self.edit_pos_entry.pack(side="left", padx=(4, 0))
        for w in (self.edit_at_entry, self.edit_pos_entry):
            w.bind("<Return>", lambda _e: self._edit_apply_entry())
        # ---- =213/=242: 時間補正(Fキー・数字キー配置の押し遅れを遡る) ----
        # =242: コンボ(0.1刻み)→**0.01秒刻みの入力欄+▲▼ボタン**。
        # 0.00〜-1.00。config("time_adjust_sec")へ保存し次回も引き継ぐ。
        ctk.CTkLabel(row2, text=tr("時間補正(秒):"),
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(10, 0))
        self._time_adj_last = self._load_time_adj()
        self.time_adj_var = tk.StringVar(
            value=self._time_adj_label(self._time_adj_last))
        self.time_adj_entry = ctk.CTkEntry(
            row2, width=52, height=24, font=ctk.CTkFont(size=11),
            justify="right", textvariable=self.time_adj_var)
        self.time_adj_entry.pack(side="left", padx=(4, 0))
        self.time_adj_entry.bind("<Return>",
                                 lambda _e: self._time_adj_commit())
        self.time_adj_entry.bind("<FocusOut>",
                                 lambda _e: self._time_adj_commit())
        Tooltip(self.time_adj_entry,
                lambda: tr("F1〜F9・数字キーで配置する位置を、押した瞬間"
                           "より前へずらす（押し遅れの補正。0.01秒刻み・"
                           "0.00〜-1.00秒）"))
        # ▲▼(縦2段)。三角形の文字は Roboto に無いのでUIフォントで描く
        # (7.4 の教訓)。押しっぱなしで連続増減(初回400ms→80ms間隔)。
        spin = ctk.CTkFrame(row2, fg_color="transparent")
        spin.pack(side="left", padx=(2, 0))
        self._time_adj_job = None
        self.time_adj_up = ctk.CTkButton(
            spin, text="▲", width=20, height=11, corner_radius=3,
            font=ctk.CTkFont(family=(appfont.FAMILY or None), size=8),
            fg_color=("gray75", "gray28"),
            hover_color=("gray70", "gray33"),
            text_color=("gray20", "gray85"))
        self.time_adj_up.pack()
        self.time_adj_down = ctk.CTkButton(
            spin, text="▼", width=20, height=11, corner_radius=3,
            font=ctk.CTkFont(family=(appfont.FAMILY or None), size=8),
            fg_color=("gray75", "gray28"),
            hover_color=("gray70", "gray33"),
            text_color=("gray20", "gray85"))
        self.time_adj_down.pack(pady=(2, 0))
        for btn, delta in ((self.time_adj_up, script_edit.TIME_ADJ_STEP),
                           (self.time_adj_down,
                            -script_edit.TIME_ADJ_STEP)):
            btn.bind("<ButtonPress-1>",
                     lambda _e, d=delta: self._time_adj_press(d))
            btn.bind("<ButtonRelease-1>",
                     lambda _e: self._time_adj_release())
        # ---- =243: 音声波形(参照モードのコンボと同じ設定を共有) ----
        ctk.CTkLabel(row2, text=tr("音声波形:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(10, 0))
        self.edit_wave_var = tk.StringVar(
            value=self._wave_labels[self._wave_mode_key])
        self.edit_wave_menu = CTkOptionMenu(
            row2, variable=self.edit_wave_var, width=110, height=24,
            font=ctk.CTkFont(size=11),
            values=[self._wave_labels[k] for k in WAVE_MODE_KEYS],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=self._on_wave_mode)
        self.edit_wave_menu.pack(side="left", padx=(4, 0))
        # Fキーはダイアログのスコープで受ける(bind_all は編集画面全体へ
        # 漏れるため不可)。Entry にフォーカスがあっても Toplevel の
        # バインドタグで届く
        # =222: 押しっぱなしの数珠つなぎ。KeyPress/KeyRelease で受け、
        # OS の自動リピートは無視して**自前で「終端に来たら次を置く」**。
        self._fkey_held = None          # 押しっぱなし中の F キー
        self._fkey_chain_end = None     # 次に置く起点(直前パターンの終端)
        self._fkey_job = None           # 終端監視の after id
        self._fkey_release_job = None   # KeyRelease の確定待ち(自動リピート)
        for fk in script_edit.FKEYS:
            self.bind("<KeyPress-" + fk + ">",
                      lambda _e, k=fk: self._on_fkey_press(k), add="+")
            self.bind("<KeyRelease-" + fk + ">",
                      lambda _e, k=fk: self._on_fkey_release(k), add="+")
        # =223: 数字キーで打点(0=pos0 / 1=pos10 … 9=pos90 / +=pos100)。
        # テンキー(NumLock ON)とメインの数字行の両方。at/pos の入力欄に
        # カーソルがあるときは数字入力を優先する(_key_target_is_entry)。
        for d in range(10):
            for seq in ("<KeyPress-" + str(d) + ">",
                        "<KeyPress-KP_" + str(d) + ">"):
                self.bind(seq, lambda _e, v=d * 10: self._on_pos_key(v),
                          add="+")
        for seq in ("<KeyPress-plus>", "<KeyPress-KP_Add>"):
            self.bind(seq, lambda _e: self._on_pos_key(100), add="+")

        # ---- グラフ ----
        # =232: 2本作る。1本目は常に出し、2本目(右=ロータ2)は5列csvの
        # ときだけ pack する。表示は peers で常に揃える。
        self._edit_graphs = []
        for gi in range(2):
            g = script_edit.ScriptEditGraph(
                inner, self._edit_models[gi], on_change=self._edit_on_change,
                on_select=self._edit_on_select, on_menu=self._edit_menu,
                height=self.EDIT_GRAPH_H)
            g.on_paste_reject = self._edit_paste_reject
            # =192: 右ダブルクリックで再生位置をセット(メニューが出ない場所)
            g.on_seek = self.seek
            # =179: パターンの配置に成功したら点モードへ戻る
            g.on_placed = lambda: self._set_edit_tool("point")
            g.on_activate = lambda i=gi: self._set_active_graph(i)
            g.active_color = ACCENT
            self._edit_graphs.append(g)
        self._edit_graphs[0].pack(fill="both", expand=True, pady=(6, 0))
        # =227: サブ表示(参考用の別トラック。要望6)。既定は非表示で、
        # 下のコンボから選ぶとグラフの下半分に出る。地の色を変えて区別する。
        self.edit_sub_model = script_edit.ScriptEditModel()
        self.edit_sub_graph = script_edit.ScriptEditGraph(
            inner, self.edit_sub_model, on_change=lambda: None,
            on_select=lambda: None, on_menu=lambda *_a: None,
            height=self.EDIT_GRAPH_H)
        self.edit_sub_graph.make_readonly(self._edit_graphs[0])
        # =298(要望4-④): サブ表示には縮尺・時間ラベル・追従停止中を出さない
        self.edit_sub_graph.show_scale = False
        self.edit_sub_graph.show_time = False
        self.edit_sub_graph.show_follow_hint = False
        # =232: 表示を揃える仲間(左・右・サブ)を相互に結ぶ
        g0, g1, gs = self._edit_graphs[0], self._edit_graphs[1], \
            self.edit_sub_graph
        g0.peers = [g1, gs]
        g1.peers = [g0, gs]
        gs.peers = []
        g0.mirror = gs                 # =227 互換(テスト・既存コード)
        self._edit_sub_idx = -1
        # =224: csv(ROTATE)のときだけ出す読み方の案内(グラフのすぐ下)
        self.edit_csv_hint = ctk.CTkLabel(
            inner,
            text=tr("csv(ROTATE): 中央0=停止 ／ 上=正回転 ／ 下=逆回転"
                    " ／ 次の点まで同じ値を保ちます ／ "
                    "時刻は100ms単位です"),
            font=ctk.CTkFont(size=11), text_color=ACCENT_TEXT, anchor="w")
        self.edit_hint_label = ctk.CTkLabel(
            inner,
            text=tr("左クリック=打点・選択 ／ 左ドラッグ=移動・矩形選択 ／ "
                    "右クリック=点・パターンのメニュー ／ "
                    "右ダブルクリック=再生位置セット ／ "
                    "右ドラッグ=前後を見る ／ ホイール=時間の拡大縮小"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w")
        self.edit_hint_label.pack(fill="x", pady=(4, 0))
        # =213: リアルタイム配置のヒント(2行目)
        self.edit_fkey_hint = ctk.CTkLabel(
            inner,
            text=tr("F1〜F9=割り当てたパターンを再生位置へ上書き配置（長押しで数珠つなぎ） "
                    "／ 割り当て=パターンボタンを右クリック "
                    "／ 数字キー=再生位置へ打点（0〜9=pos0〜90・+=pos100） "
                    "／ 矢印キー=選択中の点・パターンを1グリッド移動（長押しで連続）"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w")
        self.edit_fkey_hint.pack(fill="x", pady=(0, 0))
        # =227: サブ表示の選択(ショートカットキーの説明の下・枠線の中)
        sub_row = ctk.CTkFrame(inner, fg_color="transparent")
        sub_row.pack(fill="x", pady=(4, 0))
        self.edit_sub_row = sub_row
        ctk.CTkLabel(sub_row, text=tr("サブ:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.edit_sub_var = tk.StringVar(value=tr("(なし)"))
        self.edit_sub_menu = CTkOptionMenu(
            sub_row, variable=self.edit_sub_var, width=250, height=24,
            font=ctk.CTkFont(size=11), values=[tr("(なし)")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_edit_sub_change())
        self.edit_sub_menu.pack(side="left", padx=(4, 0))
        Tooltip(self.edit_sub_menu,
                lambda: tr("編集中のトラックの下に、別のトラックを"
                           "参考として表示します（見るだけ・編集不可）"))
        # 保存結果・エラーの表示欄(モーダルにしない)
        self.edit_msg = ctk.CTkLabel(
            inner, text="", font=ctk.CTkFont(size=11), anchor="w",
            justify="left", wraplength=self.EDIT_WIN_W - 80)
        self.edit_msg.pack(fill="x", pady=(2, 0))

    # ---- =211: 編集モードの高さの記憶 ----

    @classmethod
    def load_edit_height(cls, cfg=None) -> int:
        """記憶した編集モードの高さ(論理px)。無い/不正なら 0。"""
        try:
            cfg = load_config() if cfg is None else cfg
            sec = cfg.get(cls.CFG_REVIEW_WIN)
            v = int(sec.get("edit_h")) if isinstance(sec, dict) else 0
            return v if v > 0 else 0
        except Exception:
            return 0

    def _save_edit_height(self):
        """編集モード中の今の高さを config へ(winstate が有効なときだけ=
        =115/=181 と同じ作法。テストが共用設定を汚さないため)。"""
        if not self.edit_mode or not winstate.ENABLED:
            return False
        try:
            ch = self._to_logical(self.winfo_height())
        except Exception:
            return False
        if ch <= 1:
            return False
        try:
            cfg = load_config()
            sec = cfg.get(self.CFG_REVIEW_WIN)
            if not isinstance(sec, dict):
                sec = {}
            sec["edit_h"] = int(ch)
            cfg[self.CFG_REVIEW_WIN] = sec
            save_config(cfg)
            return True
        except Exception:
            return False

    def _apply_edit_layout(self):
        """編集モードのウィンドウサイズ。高さは reqheight 実測(仕様 2.1)。
        =211: 既定=下限 780(EDIT_WIN_H)。記憶した高さがあればそれを使う
        (下限未満には縮めない)。"""
        try:
            self.update_idletasks()
        except Exception:
            pass
        win_h = max(self.MIN_WIN_H, self._to_logical(self.winfo_reqheight()),
                    self.EDIT_WIN_H, self.load_edit_height())
        if self._user_geom:
            # =181: 記憶した大きさを尊重(編集UIに必要な幅・高さまでは広げる)
            cw = self._to_logical(self.winfo_width())
            ch = self._to_logical(self.winfo_height())
            if cw > 1 and ch > 1:
                self.geometry("{0}x{1}".format(max(cw, self.EDIT_WIN_W),
                                               max(ch, win_h)))
            else:
                self.geometry("{0}x{1}".format(self.EDIT_WIN_W, win_h))
        else:
            self.geometry("{0}x{1}".format(self.EDIT_WIN_W, win_h))

    # ---- トラックの読み込み ----

    def _on_edit_track_change(self, _value):
        # =227: 一覧の末尾の「(新規作成)」を選んでも新規作成へ入れる
        if self.edit_track_var.get() == tr("(新規作成)"):
            self._on_edit_new_track()
            return
        idx = 0
        try:
            idx = self.edit_track_menu.cget("values").index(
                self.edit_track_var.get())
        except ValueError:
            pass
        if idx >= len(self._edit_tracks):
            idx = -1
        if idx == self._edit_index and not self._edit_new:
            return
        # トラックの切り替え前に未保存確認(仕様 4.1)。UNDO の対象外。
        if not self._resolve_unsaved():
            self.edit_track_var.set(
                self._edit_track_label(self._edit_index)
                if self._edit_index >= 0 and not self._edit_new
                else tr("(新規作成)"))
            return
        self.edit_type_menu.pack_forget()
        self._edit_load_track(idx)

    # ---- =227: サブ表示(参考用の別トラック) ----

    def _refresh_sub_choices(self):
        """サブ表示のコンボを、いま編集していない候補で作り直す。"""
        vals = [tr("(なし)")]
        self._edit_sub_targets = []
        for i in range(len(self._edit_tracks)):
            if i == self._edit_index:
                continue
            vals.append(self._edit_track_label(i))
            self._edit_sub_targets.append(i)
        self.edit_sub_menu.configure(values=vals)
        if self.edit_sub_var.get() not in vals:
            self.edit_sub_var.set(tr("(なし)"))
            self._show_sub(-1)
        # 候補が1本だけ(=サブに出せる相手がいない)ときは選ばせない
        self.edit_sub_menu.configure(
            state="normal" if len(vals) > 1 else "disabled")

    def _on_edit_sub_change(self):
        label = self.edit_sub_var.get()
        vals = list(self.edit_sub_menu.cget("values"))
        try:
            k = vals.index(label)
        except ValueError:
            k = 0
        self._show_sub(self._edit_sub_targets[k - 1] if k >= 1 else -1)

    def _show_sub(self, idx: int):
        """サブ表示のトラックを切り替える(-1=非表示)。"""
        from . import script_edit
        self._edit_sub_idx = idx
        if idx < 0:
            if self.edit_sub_graph.winfo_ismapped():
                self.edit_sub_graph.pack_forget()
            self._apply_graph_heights()       # =232: 残りで等分し直す
            return
        ttype, path, lo, hi, ch = self._edit_tracks[idx]
        pts = []
        try:
            if ch is not None:
                # =232: 5列(左右)の候補は**左(ロータ1)**を参考表示する
                c0 = ch[0] if isinstance(ch, tuple) else ch
                _cols, pts, _all = script_edit.load_csv_points(path, c0)
            else:
                _raw, pts = script_edit.load_funscript_raw(path)
        except Exception:
            pts = []
        # =297: サブ表示も種別の分解能で読む(csv=200)
        sub_max = script_edit.CSV_POS_MAX if ch is not None else 100
        self.edit_sub_model.pos_max = sub_max
        sub_c = script_edit.PAT_CENTERS.get(self._pat_mode_of(ttype))
        self.edit_sub_model.pat_center = None if sub_c is None \
            else int(round(sub_c * sub_max / 100.0))
        self.edit_sub_model.load(pts)
        g = self.edit_sub_graph
        g.pat_center = self.edit_sub_model.pat_center
        # サブ側の作法(階段/ヒートマップ)は**その種別のもの**にする
        step = (ch is not None) or ttype in SCRIPT_EDIT_STEP_TYPES
        g.step = step
        g.heat = not step
        g.region = self.edit_graph.region
        if not g.winfo_ismapped():
            # **一番下**へ出す(=227 要望6)。どれも expand=True なので、
            # 要求の高さを揃えれば縦に等分される(=232: 3本のときも同じ)
            g.pack(fill="both", expand=True, pady=(4, 0),
                   before=self.edit_csv_hint
                   if self.edit_csv_hint.winfo_ismapped()
                   else self.edit_hint_label)
        self._apply_graph_heights()
        main = self._edit_graphs[0]
        g.level = main.level
        g.view_ms = main.view_ms
        g.now_ms = main.now_ms
        g.redraw()

    def _on_edit_new_track(self):
        """=227: **別のトラックを新しく作る**導線(要望5)。

        従来は「候補が0本のときだけ」新規作成に入れたので、1本作ると
        他の種別を足せなかった。「＋新規」ボタン(と一覧の末尾の
        「(新規作成)」)から、いつでも空のグラフへ切り替えられるようにする。
        """
        if self._edit_new and self._edit_index < 0:
            return
        if not self._resolve_unsaved():
            self.edit_track_var.set(
                self._edit_track_label(self._edit_index)
                if self._edit_index >= 0 else tr("(新規作成)"))
            return
        self.edit_track_var.set(tr("(新規作成)"))
        if not self.edit_type_menu.winfo_ismapped():
            self.edit_type_menu.pack(side="left", padx=(6, 0))
        self._edit_load_track(-1)

    def _on_edit_type_change(self):
        # 新規作成の種別変更(点列はそのまま。保存時の紐づけ先種別が変わる)
        # =224: csv/funscript の切り替えも起きるので画面の作法を作り直す
        self._edit_type, self._edit_kind, self._edit_cols = \
            self._new_kind_of(self.edit_type_var.get())
        self._edit_csv_ch = 0
        self._edit_csv_other = []
        # =234(要望1): **種別コンボで UFOTW用csv を選んだ時点で左右2本**
        # (=233 では `_edit_load_track(-1)` を通る導線だけだった)
        pair = self._edit_kind == "csv" and self._edit_cols == 5
        if pair != self._edit_pair:
            self._set_edit_pair(pair)
            if pair:
                self._edit_models[1].load([])
                self._edit_models[1].dirty = False
        self._apply_edit_kind()
        for _g in self.edit_graphs:
            _g.redraw()

    def _edit_load_track(self, idx: int):
        """編集対象を読み込む。idx=-1 は空のグラフから新規作成。

        **区間(=59)は一切適用しない**(仕様 4.2 ← 最重要)。区間内だけを
        保存すると区間外のデータが消えるため。区間は目印の縦線だけ描く。
        """
        from . import script_edit
        self.pause()
        self._edit_index = idx
        warns = list(getattr(self, "_edit_warn_base", []))
        region = (0.0, None, False)
        if idx < 0:
            self._edit_new = True
            self._edit_path = ""
            self._edit_extra = None
            # =224: 新規作成の種別は「種別 / kind / 列数」の3つ組
            self._edit_type, self._edit_kind, self._edit_cols = \
                self._new_kind_of(self.edit_type_var.get())
            self._edit_csv_ch = 0
            self._edit_csv_other = []
            # =233(要望2): 新規作成でも **5列(UFOTW用csv)は最初から2本**
            self._set_edit_pair(self._edit_kind == "csv"
                                and self._edit_cols == 5)
            for _m in self._edit_models:
                _m.pos_max = self._edit_pos_max()     # =297: load の前に
                _m.load([])
        else:
            self._edit_new = False
            ttype, path, lo, hi, ch = self._edit_tracks[idx]
            self._edit_type = ttype
            self._edit_path = path
            self._edit_extra = None
            self._edit_kind = "csv" if (ch is not None) else "funscript"
            for _m in self._edit_models:
                _m.pos_max = self._edit_pos_max()     # =297: load の前に
            pair = self._csv_is_2ch(ch)         # =232: 5列=左右同時編集
            self._edit_csv_ch = 0 if pair else (ch or 0)
            self._edit_csv_other = []
            self._edit_cols = 3
            pts = []
            if self._edit_kind == "csv":
                # =224: csv(ROTATE)。=297: pos 100=停止 / 200=正回転 /
                # 0=逆回転(分解能 200=速度 1 刻み)。
                # **=232: 5列は左右2本を同時に読み込む**(片方を保持して
                # 保存へ回す `_edit_csv_other` の受け渡しは不要になった)。
                other = []
                if os.path.isfile(path):
                    try:
                        cols, pts, allp = script_edit.load_csv_points(
                            path, self._edit_csv_ch)
                        self._edit_cols = cols
                        if cols == 5 and len(allp) >= 2:
                            other = allp[1]
                            if not pair:
                                self._edit_csv_other = \
                                    allp[1 - self._edit_csv_ch]
                    except Exception as e:
                        warns.append(tr("スクリプトを読み込めません({0}): {1}")
                                     .format(os.path.basename(path), e))
                        self._edit_path = ""
                pair = pair and self._edit_cols == 5
                self._set_edit_pair(pair)
                self._edit_models[0].load(pts)
                if pair:
                    self._edit_models[1].load(other)
            else:
                self._set_edit_pair(False)
                if os.path.isfile(path):
                    try:
                        self._edit_extra, pts = \
                            script_edit.load_funscript_raw(path)
                    except Exception as e:
                        warns.append(tr("スクリプトを読み込めません({0}): {1}")
                                     .format(os.path.basename(path), e))
                        # 壊れたファイルを黙って上書きしないよう保存先は無効化
                        self._edit_path = ""
                # ファイルが無い=空のグラフから描き始める(仕様 決定5)。
                # 保存は「新規保存」ではなくそのパスへの上書きで作成できる。
                self._edit_models[0].load(pts)
                # =204: パターン記憶("rvp" キー)の復元。照合に失敗した
                # パターンは情報だけ捨てて点は残す(仕様 6.2)
                if isinstance(self._edit_extra, dict) and \
                        script_edit.RVP_KEY in self._edit_extra:
                    _n, dropped = self._edit_models[0].import_patterns(
                        self._edit_extra.get(script_edit.RVP_KEY))
                    if dropped:
                        warns.append(tr(
                            "一部のパターン情報を復元できませんでした"
                            "（点はそのまま残しています）"))
            region = (lo, hi, bool(lo) or hi is not None)
        self._apply_edit_kind()
        for _g in self.edit_graphs:                  # =232: 区間は全部へ
            _g.region = region
        self.edit_save_btn.configure(
            state="normal" if self._edit_path else "disabled")
        # 区間指定のあるアイテムでは「区間を無視している」注意書き(仕様 4.2)
        if region[2]:
            warns.insert(0, tr("編集モード: 区間指定を無視して素材全体を"
                               "表示・再生しています"))
        if warns:
            self.warn_label.configure(text="\n".join(warns))
            self.warn_label.pack(fill="x", padx=14, pady=(4, 0),
                                 before=self.transport_card)
        else:
            self.warn_label.pack_forget()
        self.edit_msg.configure(text="")
        if getattr(self, "edit_sub_menu", None) is not None:
            self._refresh_sub_choices()          # =227
            if self._edit_sub_idx == idx:        # 自分自身はサブにしない
                self.edit_sub_var.set(tr("(なし)"))
                self._show_sub(-1)
        self._edit_refresh_duration()
        self._reset_clock()
        if self._video and self._mpv is not None:
            # =203: 編集モードは素材全体(区間を無視)なので頭出しする
            self._mpv.set_pause(True)
            self._mpv.seek(0.0)
            self._mpv_cmd_t = time.monotonic()
        self._set_active_graph(0)                 # =232: 上(左)から始める
        for _g in self.edit_graphs:
            _g.now_ms = 0.0
            _g.playing = self._playing        # =198: 再生中に入った場合
            _g.initial_view(self._duration_ms)
        self._update_time(0.0, force=True)
        self._edit_on_select()
        self._schedule_tick()

    def _edit_refresh_duration(self):
        """全長 = max(音声の長さ, スクリプトの最終点)(仕様 4.9)。

        末尾より後ろに打点したら全長も伸びる。編集モードの音声は
        **区間を無視した素材全体**(self._edit_sound)。
        """
        audio_ms = 0
        if self._video:
            audio_ms = int(self._video_len)   # =203: 動画の実長(素材全体)
        elif self._edit_sound is not None:
            try:
                audio_ms = int(self._edit_sound.get_length() * 1000)
            except Exception:
                audio_ms = 0
        self._sound = self._edit_sound
        self._duration_ms = max([audio_ms] +                # =232: 左右とも
                                [m.duration_ms()
                                 for m in self.edit_models])
        self._sync_buttons()

    # ---- グラフからのコールバック ----

    def _edit_on_change(self):
        # 貼り付け拒否の警告は、次の編集が成功した時点で下ろす(=171)
        if getattr(self, "_edit_paste_warned", False):
            self._edit_paste_warned = False
            self.edit_msg.configure(text="")
        self._edit_refresh_duration()
        self._update_time(self._now_ms(), force=True)

    def _edit_paste_reject(self, reason: str = "range"):
        """貼り付けが拒否されたときの表示(=171/=172/=185)。"""
        self._edit_paste_warned = True
        if reason == "no_selection":
            msg = tr("貼り付けの基準になる点を選択してください"
                     "（選択中で最も未来の点が基準になり、その点は"
                     "上書きされます）")
        elif reason == "edge":
            msg = tr("パターンの端のすぐ近くには点を置けないため、"
                     "貼り付けできませんでした")
        else:
            msg = tr("貼り付け先に収まらないため、貼り付けできませんでした"
                     "（0〜100または時間の範囲外になります）")
        self.edit_msg.configure(text=msg, text_color=MSG_WARN)

    # =233(要望6): csv では入力欄の単位を **csv の生の値**にする。
    #   時間 = 100ms 単位(10 = 1秒) / 回転速度 = -100〜100
    #   (正=正回転・負=逆回転。絶対値が csv の3列目/5列目と一致する)
    def _entry_is_csv(self) -> bool:
        return getattr(self, "_edit_kind", "funscript") == "csv"

    def _at_to_entry(self, at: int) -> str:
        from . import script_edit
        if self._entry_is_csv():
            return "{0:g}".format(at / script_edit.CSV_AT_UNIT)
        return str(at)

    def _at_from_entry(self, txt: str) -> int:
        from . import script_edit
        v = float(txt)
        if self._entry_is_csv():
            v *= script_edit.CSV_AT_UNIT
        return int(round(v))

    def _pos_to_entry(self, pos: int) -> str:
        from . import script_edit
        if self._entry_is_csv():
            # =297: 分解能 200(中心 100)なので速度 = pos - 100(1 刻み)
            return str(int(pos) - script_edit.CSV_STOP_POS)   # -100〜100
        return str(pos)

    def _pos_from_entry(self, txt: str) -> int:
        from . import script_edit
        v = float(txt)
        if self._entry_is_csv():
            v = script_edit.CSV_STOP_POS + max(-100.0, min(100.0, v))
        return int(round(v))

    def _edit_on_select(self):
        """選択の変化を (at,pos) 欄へ反映。単独選択のときだけ編集できる。"""
        sel = self.edit_model.selection
        for w in (self.edit_at_entry, self.edit_pos_entry):
            try:
                w.configure(state="normal")
                w.delete(0, "end")
            except Exception:
                pass
        if len(sel) == 1:
            at = next(iter(sel))
            pos = self.edit_model.pos_of(at)
            self.edit_at_entry.insert(0, self._at_to_entry(at))
            if pos is not None:
                self.edit_pos_entry.insert(0, self._pos_to_entry(pos))
        else:
            for w in (self.edit_at_entry, self.edit_pos_entry):
                w.configure(state="disabled")

    def _edit_apply_entry(self):
        """(at,pos) 数値入力の確定。衝突・不正値なら表示を元へ戻す。"""
        sel = self.edit_model.selection
        if len(sel) != 1:
            return
        at = next(iter(sel))
        try:
            new_at = self._at_from_entry(self.edit_at_entry.get())
            new_pos = self._pos_from_entry(self.edit_pos_entry.get())
        except ValueError:
            self._edit_on_select()
            return
        if self.edit_model.set_point(at, new_at, new_pos):
            self._edit_on_change()
        self._edit_on_select()
        self.edit_graph.redraw()

    def _on_point_btn(self):
        self._set_edit_tool("point")

    def _on_invert_btn(self):
        """インバート(仕様 5.2)。独立した ON/OFF。配置時に pos を反転。"""
        self._edit_invert = not self._edit_invert
        if self._edit_invert:
            self.edit_invert_btn.configure(fg_color=ACCENT, border_width=0,
                                           text_color=("white", "white"),
                                           hover_color=ACCENT_HOVER)
        else:
            self.edit_invert_btn.configure(fg_color="transparent",
                                           border_width=1,
                                           text_color=("gray20", "gray85"),
                                           hover_color=("gray85", "gray25"))
        self._set_edit_tool(self._edit_tool)   # 配置ツールへ反転を反映

    def _on_scale_btn(self, value: float):
        """縮尺配置(=194)。x2〜x0.5 の排他トグル。配置時の長さに掛かる。"""
        self._edit_scale = value
        self._refresh_scale_btns()
        self.edit_graph.place_scale = value
        self.edit_graph.redraw()               # ゴーストへ即反映

    def _refresh_scale_btns(self):
        for v, b in self.edit_scale_btns.items():
            on = abs(v - self._edit_scale) < 1e-9
            b.configure(fg_color=ACCENT if on else "transparent",
                        border_width=0 if on else 1,
                        text_color=("white", "white") if on
                        else ("gray20", "gray85"),
                        hover_color=ACCENT_HOVER if on
                        else ("gray85", "gray25"))

    # ---- =231: ユーザーパターンは種別ごとに別々の20枠 ----

    def _user_kind(self) -> str:
        """ユーザーパターンの種別(linear / twist / rotate_ufo /
        rotate_a10cyclonesa / vibration)。編集中のトラックの種別そのもの。"""
        from . import script_edit
        t = getattr(self, "_edit_type", "linear")
        return t if t in script_edit.USER_PAT_PREFIX else "linear"

    def _user_key(self, slot: int) -> str:
        """枠番号(1〜20) → いまの種別での枠の名前(L1 / T1 / U1 …)。"""
        from . import script_edit
        return script_edit.user_pat_key(self._user_kind(), slot)

    def _refresh_user_pattern_btns(self):
        """枠ボタンの見た目を登録状態に合わせる(=205 / =231 種別ごと)。

        登録済み=波形アイコン+押せる/未登録=グレーの文字ボタンで
        押しても何も起きない(disabled)。**文字は種別ごとの名前**
        (L1〜L20 / T1〜T20 / U1〜U20 / A1〜A20 / V1〜V20)。
        """
        for slot, btn in self.edit_user_btns.items():
            ukey = self._user_key(slot)
            shape = self._user_patterns.get(ukey)
            if shape:
                icon = self._pattern_icon(shape)
                self._user_icons[slot] = icon
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    btn.configure(image=icon, text="", state="normal")
            else:
                self._user_icons.pop(slot, None)
                btn.configure(image=None, text=ukey, state="disabled")

    # ---- =213: Fキー配置(リアルタイム編集) ----

    # =242: 0.01秒刻み・0.00〜-1.00。config("time_adjust_sec")へ保存する。
    CFG_TIME_ADJ = "time_adjust_sec"
    TIME_ADJ_REPEAT_FIRST = 400     # 長押しの初回ディレイ(ms)
    TIME_ADJ_REPEAT_MS = 80         # 以降の連続増減の間隔(ms)

    @staticmethod
    def _time_adj_clamp(v: float) -> float:
        from . import script_edit
        step = script_edit.TIME_ADJ_STEP
        v = round(round(v / step) * step, 2)
        return max(script_edit.TIME_ADJ_MIN, min(0.0, v))

    @staticmethod
    def _time_adj_label(v: float) -> str:
        return f"{v:.2f}"

    def _load_time_adj(self) -> float:
        try:
            return self._time_adj_clamp(float(
                load_config().get(self.CFG_TIME_ADJ, 0.0)))
        except Exception:
            return 0.0

    def _time_adj_value(self) -> float:
        """入力欄の現在値(秒)。読めないときは 0。"""
        try:
            return self._time_adj_clamp(float(self.time_adj_var.get()))
        except (ValueError, TypeError):
            return 0.0

    def _time_adj_ms(self) -> float:
        return self._time_adj_value() * 1000.0

    def _time_adj_commit(self):
        """Enter / フォーカスアウト: 0.01秒へ丸め・0.00〜-1.00へクランプ。
        数値として読めない入力は直前の有効値へ戻す。"""
        try:
            v = self._time_adj_clamp(float(self.time_adj_var.get()))
        except (ValueError, TypeError):
            v = getattr(self, "_time_adj_last", 0.0)
        self._time_adj_set(v)

    def _time_adj_set(self, v: float):
        v = self._time_adj_clamp(v)
        self._time_adj_last = v
        self.time_adj_var.set(self._time_adj_label(v))
        try:
            cfg = load_config()
            if cfg.get(self.CFG_TIME_ADJ) != v:
                cfg[self.CFG_TIME_ADJ] = v
                save_config(cfg)
        except Exception:
            pass

    def _time_adj_press(self, delta: float):
        """▲▼の押下: 1回進めて、押しっぱなしなら連続増減(=242)。"""
        self._time_adj_release()
        self._time_adj_set(self._time_adj_value() + delta)
        self._time_adj_job = self.after(
            self.TIME_ADJ_REPEAT_FIRST,
            lambda: self._time_adj_repeat(delta))

    def _time_adj_repeat(self, delta: float):
        self._time_adj_set(self._time_adj_value() + delta)
        self._time_adj_job = self.after(
            self.TIME_ADJ_REPEAT_MS, lambda: self._time_adj_repeat(delta))

    def _time_adj_release(self):
        if self._time_adj_job is not None:
            try:
                self.after_cancel(self._time_adj_job)
            except Exception:
                pass
            self._time_adj_job = None

    # ---- =222: Fキーの押しっぱなし=数珠つなぎ ----

    CHAIN_POLL_MS = 30        # 終端に来たかを見る間隔
    FKEY_RELEASE_MS = 60      # X11 の自動リピート(Release/Press連打)対策

    def _on_fkey_press(self, fk: str):
        """F キーの押下。単押しは =213 のまま(今の再生位置へ1つ置く)。
        押しっぱなしのときは、置いたパターンの**終端**に再生位置が届くたび、
        その終端を起点に次を置いて数珠つなぎにする(=222)。"""
        if self._fkey_release_job is not None:
            # 自動リピートの Release が来ていた=押しっぱなしの継続
            try:
                self.after_cancel(self._fkey_release_job)
            except Exception:
                pass
            self._fkey_release_job = None
        if self._fkey_held == fk:
            return "break"              # OS の自動リピートは無視
        if self._fkey_held is not None:
            # 押しっぱなし中に別のキー=**数珠つなぎは続けたまま形だけ交代**
            # (ユーザー決定)。ここでは置かない=次の終端から新しい形になる
            if self._fkey_map.get(fk) is None:
                return "break"
            self._fkey_held = fk
            return "break"
        r = self._on_fkey(fk)
        if r == "break" and self._fkey_map.get(fk) is not None:
            self._fkey_held = fk
            # 置けた区間の終端だけを次の起点にする(失敗時は None のまま)
            rng = getattr(self.edit_model, "last_place_range", None)
            self._fkey_chain_end = rng[1] if rng else None
            self._chain_stop_job()
            if self._fkey_chain_end is not None:
                self._fkey_job = self.after(self.CHAIN_POLL_MS,
                                            self._fkey_chain_tick)
        return r

    def _fkey_chain_tick(self):
        """再生位置が直前のパターンの終端に届いたら、そこから次を置く。"""
        self._fkey_job = None
        if self._fkey_held is None or self._fkey_chain_end is None:
            return
        try:
            if not self.winfo_exists() or not self.edit_mode:
                return
        except Exception:
            return
        if self._now_ms() >= self._fkey_chain_end:
            # 起点は**素材時刻ちょうど**(時間補正は最初の1つだけに掛ける)
            self._on_fkey(self._fkey_held, at0=self._fkey_chain_end)
            # 置けなかった回は last_place_range が None=そこで連続を止める
            rng = getattr(self.edit_model, "last_place_range", None)
            if not rng or rng[1] <= self._fkey_chain_end:
                self._chain_stop()      # 進まない=無限ループ防止
                return
            self._fkey_chain_end = rng[1]
        self._fkey_job = self.after(self.CHAIN_POLL_MS, self._fkey_chain_tick)

    def _on_fkey_release(self, fk: str):
        if self._fkey_held != fk:
            return "break"
        if self._fkey_release_job is not None:
            try:
                self.after_cancel(self._fkey_release_job)
            except Exception:
                pass
        self._fkey_release_job = self.after(self.FKEY_RELEASE_MS,
                                            self._chain_stop)
        return "break"

    def _chain_stop_job(self):
        if self._fkey_job is not None:
            try:
                self.after_cancel(self._fkey_job)
            except Exception:
                pass
            self._fkey_job = None

    def _chain_stop(self):
        self._fkey_release_job = None
        self._fkey_held = None
        self._fkey_chain_end = None
        self._chain_stop_job()

    # ---- =223: 数字キーで打点 ----

    def _key_target_is_entry(self) -> bool:
        """フォーカスが文字入力欄にあるか(数字キーは入力を優先する)。"""
        try:
            w = self.focus_get()
        except Exception:
            return False
        return isinstance(w, (tk.Entry, tk.Text))

    def _on_pos_key(self, pos: int):
        """0〜9 と + キー: 今の再生位置(+時間補正)へ点を打つ(=223)。

        pos は 0/10/…/90/100(csv は 2 倍=297)。時間[at]グリッドへ吸着する。重なるパターンは
        丸ごと消して打つ(ユーザー決定=要望1と同じ「後から置くものを優先」)。
        再生は止めない。1回の打点が UNDO 1ステップ。
        """
        from . import script_edit
        if not self.edit_mode or not self._edit_built or \
                self.edit_model is None:
            return None
        if self._key_target_is_entry():
            return None                 # at/pos 欄へ数字を入れている最中
        g = self.edit_graph
        at = script_edit.snap(max(0.0, self._now_ms() + self._time_adj_ms()),
                              g.grid_at)
        # =297: csv(分解能 200)では 0〜9/+ を 0/20/…/200(=速度 -100〜+100
        # の 20 刻み)にする(ユーザー決定)
        pos = int(round(int(pos) * self._edit_pos_max() / 100.0))
        r = self.edit_model.place_point_over(int(at), int(pos))
        if r == "ok":
            g.sel_pattern = None
            self._edit_on_change()
            self._edit_on_select()
            g.redraw()
        return "break"

    def _on_fkey(self, fk: str, at0: float | None = None):
        """F1〜F9: 割り当てたパターンを**今の再生位置**(+時間補正)へ
        左端を合わせて上書き配置する(=213)。再生は止めない。
        失敗は edit_msg へ短く出すだけ。
        **=227: 再生中でないときは「グラフ上のマウス位置」へ置く**
        (マウスがグラフの外にあるときは従来どおり再生位置)。
        at0 を渡すと**その素材時刻**を起点にする(=222 の数珠つなぎ)。"""
        from . import script_edit
        if not self.edit_mode or not self._edit_built or \
                self.edit_model is None:
            return None
        ref = self._fkey_map.get(fk)
        if ref is None:
            return None
        if ref[0] == "user":
            ukey = self._user_key(ref[1])      # =231: 枠番号→今の種別の枠
            shape = self._user_patterns.get(ukey)
            if not shape:
                return None
            name, base = ukey, None
        else:
            try:
                name, shape = self._std_patterns()[int(ref[1])]
            except (IndexError, ValueError, TypeError):
                return None
            # =226: 離散的なスクリプトは **定義どおりの高さで固定**
            # (pos50 基準へずらすのは linear/twist のときだけ)
            base = None if self.edit_graph.pat_center is not None else 50
        if self._edit_invert:
            shape = script_edit.invert_shape(shape)
        g = self.edit_graph
        pos0 = None
        if at0 is None:
            # =227: **再生中でないときは、グラフ上のマウス位置へ置く**
            # (ユーザー要望2。マウスがグラフの外なら従来どおり再生位置)。
            mp = None if self._playing else g.mouse_place_at()
            if mp is not None:
                at0, pos0 = mp
            else:
                at0 = self._now_ms() + self._time_adj_ms()
        if pos0 is not None and base is not None:
            # linear/twist はクリックと同じく「マウスの高さ」を基準にする
            base = max(0, min(100, int(round(pos0))))
        r = self.edit_model.place_pattern_over(
            shape, at0, base, g.grid_at, g.grid_pos, name,
            scale=self._edit_scale)
        if r == "ok":
            g.sel_pattern = None
            self._edit_on_change()
            g.redraw()
        else:
            self._edit_paste_warned = True
            if r == "edge":
                msg = tr("パターンの端のすぐ近くには点を置けないため、"
                         "貼り付けできませんでした")
            else:
                msg = tr("この位置にはパターンを配置できません")
            self.edit_msg.configure(text=msg, text_color=MSG_WARN)
        return "break"

    # ---- =212: Fキー割り当て ----

    def _palette_ref_of(self, widget):
        """ウィジェット(とその親)がパレットのどのボタンか → ref。
        ("std", i) | ("user", "U3") | None。未登録のU枠は None。"""
        w = widget
        for _ in range(6):
            if w is None:
                return None
            for i, b in enumerate(self.edit_pattern_btns):
                if w is b:
                    return ("std", i)
            for slot, b in self.edit_user_btns.items():
                if w is b:
                    return ("user", slot) \
                        if self._user_key(slot) in self._user_patterns \
                        else None
            try:
                w = w.master
            except Exception:
                return None
        return None

    def _on_palette_right_click(self, event):
        if not self._edit_built or not self.edit_mode:
            return None
        ref = self._palette_ref_of(getattr(event, "widget", None))
        if ref is None:
            return None
        self._open_fkey_menu(ref, event.x_root, event.y_root)
        return "break"

    def _open_fkey_menu(self, ref, x_root: int, y_root: int):
        """F1〜F9+「割り当て解除」のメニューを出す(=212)。"""
        from . import script_edit
        if self._fkey_menu is not None:
            try:
                self._fkey_menu.destroy()
            except Exception:
                pass
        menu = tk.Menu(self, tearoff=0)
        cur = script_edit.fkey_of(self._fkey_map, ref)
        for fk in script_edit.FKEYS:
            other = self._fkey_map.get(fk)
            label = fk
            if fk == cur:
                label = "● " + fk
            elif other is not None:
                label = fk + "  (" + self._ref_label(other) + ")"
            menu.add_command(label=label,
                             command=lambda k=fk, r=ref: self.assign_fkey(k, r))
        menu.add_separator()
        menu.add_command(label=tr("割り当て解除"),
                         state="normal" if cur else "disabled",
                         command=lambda r=ref: self.assign_fkey(None, r))
        self._fkey_menu = menu
        try:
            menu.tk_popup(int(x_root), int(y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return menu

    def _ref_label(self, ref) -> str:
        from . import script_edit
        if ref[0] == "user":
            return self._user_key(ref[1])      # =231: 種別ごとの名前
        try:
            return tr(self._std_patterns()[int(ref[1])][0])
        except Exception:
            return str(ref[1])

    def assign_fkey(self, fkey, ref):
        """ref へ fkey を割り当てる(fkey=None は解除)。排他=同じ ref を
        持つ他のキーは外れて移動する。config へ即保存。"""
        from . import script_edit
        cfg = load_config()
        if fkey is None:
            cur = script_edit.fkey_of(self._fkey_map, ref)
            if cur is None:
                return
            script_edit.save_fkey_map(cfg, cur, None)
        else:
            script_edit.save_fkey_map(cfg, fkey, tuple(ref))
        save_config(cfg)
        self._fkey_map = script_edit.load_fkey_map(cfg)
        self._refresh_fkey_badges()

    def _badge_colors(self):
        dark = ctk.get_appearance_mode() != "Light"
        return (ACCENT, "white") if dark else ("#5245c9", "white")

    def _refresh_fkey_badges(self):
        """全ボタンのバッジを割り当てに合わせて更新する(=212)。"""
        from . import script_edit
        want = {}
        for fk, ref in self._fkey_map.items():
            if ref[0] == "user" and \
                    self._user_key(ref[1]) not in self._user_patterns:
                continue          # 今の種別で未登録の枠にはバッジを出さない
            want[tuple(ref)] = fk
        for ref, lbl in list(self._fkey_badges.items()):
            if ref not in want:
                try:
                    lbl.destroy()
                except Exception:
                    pass
                del self._fkey_badges[ref]
        bg, fg = self._badge_colors()
        for ref, fk in want.items():
            btn = self.edit_pattern_btns[ref[1]] if ref[0] == "std" \
                else self.edit_user_btns.get(ref[1])
            if btn is None:
                continue
            lbl = self._fkey_badges.get(ref)
            if lbl is None or not lbl.winfo_exists():
                lbl = tk.Label(btn, text=fk, bd=0, padx=2, pady=0,
                               font=(appfont.FAMILY, 7, "bold"))
                # バッジの上のクリックもボタンとして働かせる
                lbl.bind("<Button-1>", lambda _e, r=ref:
                         self._set_edit_tool(r[1] if r[0] == "std" else r))
                self._fkey_badges[ref] = lbl
            lbl.configure(text=fk, bg=bg, fg=fg)
            lbl.place(relx=1.0, x=-1, y=1, anchor="ne")
            lbl.lift()

    def _on_user_patterns_saved(self):
        """ユーザーパターン編集ポップアップの保存後(=205)。"""
        from . import script_edit
        self._user_patterns = script_edit.load_user_patterns(
            load_config(), self._user_kind())          # =231: 種別ごと
        self._refresh_user_pattern_btns()
        self._refresh_fkey_badges()       # =212: 画像ラベルより前面へ戻す
        # 使用中のツールが更新された枠なら、新しい形を配置ツールへ反映する
        if isinstance(self._edit_tool, tuple) and \
                self._edit_tool[0] == "user":
            self._set_edit_tool(self._edit_tool)

    def _open_user_pattern_editor(self):
        """ユーザーパターン編集ポップアップを開く(=205)。同時に1つだけ。"""
        dlg = getattr(self, "_userpat_dlg", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    # =231: 種別が変わっていたら開き直す(枠が別物のため)
                    if getattr(dlg, "kind", None) != self._user_kind():
                        dlg.destroy()
                        self._userpat_dlg = None
                    else:
                        dlg.deiconify()
                        dlg.lift()
                        dlg.focus_set()
                        return dlg
            except Exception:
                pass
        self._userpat_dlg = UserPatternDialog(self, owner=self,
                                             kind=self._user_kind())
        return self._userpat_dlg

    def _set_edit_tool(self, key):
        """[点] とパターンの排他トグル(仕様 2.4)。
        key="point" | 0〜19(標準=217) | **("user", 枠番号1〜20)**
        (=205/=218。**=231 で "U1" 等の文字列から枠番号へ変更**)。
        ※=179のコピー配置ツールは=186で廃止(Ctrl+C/Vに一本化)。"""
        from . import script_edit
        is_user = isinstance(key, tuple) and key[:1] == ("user",)
        if is_user and self._user_key(key[1]) not in self._user_patterns:
            key = "point"                 # 未登録の枠(押せないはずの保険)
            is_user = False
        self._edit_tool = key
        is_point = key == "point"
        self.edit_point_btn.configure(
            fg_color=ACCENT if is_point else "transparent",
            border_width=0 if is_point else 1,
            text_color=("white", "white") if is_point
            else ("gray20", "gray85"),
            hover_color=ACCENT_HOVER if is_point else ("gray85", "gray25"))
        for i, btn in enumerate(self.edit_pattern_btns):
            on = (not is_point) and (not is_user) and i == key
            btn.configure(fg_color=ACCENT if on else "transparent",
                          border_width=0 if on else 1,
                          hover_color=ACCENT_HOVER if on
                          else ("gray85", "gray25"))
        for slot, btn in self.edit_user_btns.items():
            on = is_user and slot == key[1]
            btn.configure(fg_color=ACCENT if on else "transparent",
                          border_width=0 if on else 1,
                          hover_color=ACCENT_HOVER if on
                          else ("gray85", "gray25"))
        # =232: 配置ツールは**出ているグラフ全部**へ載せる(どちらの
        # グラフをクリックしても置ける。クリックした方がアクティブになる)
        if is_point:
            tool = (None, None)
        elif is_user:
            ukey = self._user_key(key[1])
            shape = self._user_patterns[ukey]
            if self._edit_invert:
                shape = script_edit.invert_shape(shape)
            tool = (shape, ukey)
        else:
            name, shape = self._std_patterns()[key]
            if self._edit_invert:
                shape = script_edit.invert_shape(shape)
            tool = (shape, name)
        for g in self.edit_graphs:
            g.set_place_tool(*tool)

    def _pattern_icon(self, shape, w: int = 40, h: int = 22,
                      step: bool | None = None):
        """点列から波形の絵を tk.PhotoImage へ描く(仕様 2.4。Pillow不使用)。

        線分を1pxずつサンプリングして打つ(=110/=111 と同じ手法)。色は
        ライト/ダーク両方で見える中間のグレー1色にする(テーマ切替で
        描き直さなくて済む)。
        **=226: 離散的なスクリプトでは斜め線ではなく直角(階段)で描く**
        (step=None なら今の編集対象から判断する)。
        """
        if step is None:
            # 組み立ての途中(グラフがまだ無い)では従来どおり斜め線
            step = bool(getattr(getattr(self, "edit_graph", None),
                                "step", False))
        img = tk.PhotoImage(width=w, height=h, master=self)
        length = max(1, shape[-1][0])
        col = "#7a7a7a"

        def xy(t, p):
            x = int(round((w - 3) * t / length)) + 1
            y = int(round((h - 3) * (100 - p) / 100.0)) + 1
            return x, y

        def line(x0, y0, x1, y1):
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for st in range(steps + 1):
                x = x0 + (x1 - x0) * st // steps
                y = y0 + (y1 - y0) * st // steps
                img.put(col, (x, y))
                if y + 1 < h:
                    img.put(col, (x, y + 1))

        for i in range(len(shape) - 1):
            x0, y0 = xy(*shape[i])
            x1, y1 = xy(*shape[i + 1])
            if step:                     # 直角(横→縦)
                line(x0, y0, x1, y0)
                line(x1, y0, x1, y1)
            else:
                line(x0, y0, x1, y1)
        return img

    def _rebuild_pattern_icons(self):
        """=226: パターンモードに合わせて標準/ユーザーのボタンの絵を作り直す
        (カタログの入れ替え+斜め線↔直角の描き分け)。"""
        if not getattr(self, "_edit_built", False):
            return
        cat = self._std_patterns()
        self._pat_icons = []
        for i, btn in enumerate(self.edit_pattern_btns):
            if i >= len(cat):
                continue
            name, shape = cat[i]
            icon = self._pattern_icon(shape)
            self._pat_icons.append(icon)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                btn.configure(image=icon)
            Tooltip(btn, lambda n=name: tr(n))
        self._refresh_user_pattern_btns()
        self._refresh_fkey_badges()

    def _on_grid_change(self):
        # グリッド設定の変更は UNDO の対象外(仕様 4.5)
        # =298(要望1): 左右2本(とサブ表示)の**全部**へ反映する(以前は
        # アクティブな方だけだった)
        gp = self._grid_pos_map.get(self.grid_pos_var.get(), 10)
        ga = self._grid_at_map.get(self.grid_at_var.get(), 100)
        graphs = list(self.edit_graphs)
        if getattr(self, "edit_sub_graph", None) is not None:
            graphs.append(self.edit_sub_graph)
        for g in graphs:
            g.grid_pos = gp
            g.grid_at = ga
            g.redraw()

    # ---- 右クリックメニュー(仕様 4.4 / P2 =173: パターン項目) ----

    def _edit_menu(self, event, ctx):
        """右クリックメニュー(=186で削減)。

        残す機能は グループ化 / 解除 / 削除 / 上下反転(配置済みの反転=
        唯一の手段)のみ。コピー・切り取り・貼り付けはショートカットキー
        (Ctrl+C/X/V)に一本化し、=179のコピー配置ツールは廃止。
        メニューを出す場面が無ければ何も出さない(空欄のダブル右クリック=
        再生位置セット=192 が効くように)。
        """
        model = self.edit_model
        pat = ctx.get("pattern")
        multi = bool(ctx.get("multi"))
        center = self.edit_graph.pat_center   # None=linear/twist / 中央 / 0
        # =297: rotate 系の中心は funscript=50 / csv=100。「0 でない」=rotate
        rotate = center is not None and center > 0
        menu = tk.Menu(self, tearoff=0)
        if pat is not None and not multi:
            # パターン上の右クリック(仕様 3c/6b。=241 で反転を再編)
            if center is None:
                menu.add_command(label=tr("上下反転(全幅)"),
                                 command=lambda: self._menu_flip_v(True))
                menu.add_command(
                    label=tr("上下反転(パターン内)"),
                    command=lambda: self._menu_flip_v(False))
            elif rotate:
                # rotate系: 回転方向の反転(速度は維持)=全幅のみ
                menu.add_command(label=tr("上下反転"),
                                 command=lambda: self._menu_flip_v(True))
            menu.add_command(label=tr("左右反転"),
                             command=self._menu_flip_h)
            menu.add_command(label=tr("グループ解除"),
                             command=lambda: self._menu_pat_ungroup(pat))
            menu.add_command(label=tr("削除"),
                             command=lambda: self._menu_pat_delete(pat))
        elif model.selection or model.pattern_selection:
            # =278: 選択中のパターンの構成点も数に入れる(点+パターン混在の
            # グループ化=選択中のパターンは解除して1つにまとめる)
            if len(model.group_candidate_ats()) >= 2:
                menu.add_command(label=tr("グループ化"),
                                 command=self._menu_group)
            if model.pattern_selection:
                menu.add_command(label=tr("グループ解除"),
                                 command=self._menu_ungroup_selected)
            if center is None:
                menu.add_command(label=tr("上下反転(全幅)"),
                                 command=lambda: self._menu_flip_v(True))
                menu.add_command(
                    label=tr("上下反転(選択内)"),
                    command=lambda: self._menu_flip_v(False))
            elif rotate:
                menu.add_command(label=tr("上下反転"),
                                 command=lambda: self._menu_flip_v(True))
            menu.add_command(label=tr("左右反転"),
                             command=self._menu_flip_h)
            menu.add_command(label=tr("削除"), command=self._menu_delete)
        else:
            return                          # 何も出さない(=186)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _menu_flip_v(self, full: bool):
        """=241: 上下反転。full=True は全幅(軸=中央。=297: csv は pos100)、
        False は選択全体の min〜max の中点が軸。空振りの理由を表示する。"""
        res = self.edit_model.flip_vertical(
            self._edit_pos_max() / 2.0 if full else None)
        if res == "ok":
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()
        elif res == "shared":
            self.edit_msg.configure(
                text=tr("未選択のパターンと共有している点が軸の上に"
                        "ないため、上下反転できませんでした"),
                text_color=MSG_WARN)
        elif res == "range":
            self.edit_msg.configure(
                text=tr("上下反転すると位置が0〜100に収まらないため、"
                        "反転できませんでした"),
                text_color=MSG_WARN)

    def _menu_flip_h(self):
        """=241: 左右反転(軸=選択の時間範囲の中心)。理由を表示する。"""
        res = self.edit_model.flip_horizontal()
        if res == "ok":
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()
        elif res == "inside":
            self.edit_msg.configure(
                text=tr("選択範囲の中に選択していない点やパターンが"
                        "あるため、左右反転できませんでした"),
                text_color=MSG_WARN)
        elif res == "ends":
            self.edit_msg.configure(
                text=tr("未選択のパターンと接しているため、両端の位置"
                        "(pos)が同じときだけ左右反転できます"),
                text_color=MSG_WARN)

    def _menu_pat_ungroup(self, idx: int):
        """解除=普通の点の集まりに戻す(仕様 6b)。"""
        if self.edit_model.ungroup_pattern(idx):
            self.edit_graph.sel_pattern = None
            self.edit_model.pattern_selection = set()
            self._edit_on_change()
            self.edit_graph.redraw()

    def _menu_ungroup_selected(self):
        """=278: 選択中のパターンをまとめて解除(構成点は選択に残る)。"""
        if self.edit_model.ungroup_selected():
            self.edit_graph.sel_pattern = None
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()

    def _menu_pat_delete(self, idx: int):
        if self.edit_model.delete_pattern(idx):
            self.edit_graph.sel_pattern = None
            self.edit_model.pattern_selection = set()
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()

    def _menu_group(self):
        """グループ化(仕様 5.4)。拒否の理由は edit_msg へ表示する。"""
        res = self.edit_model.group_selection()
        if res == "ok":
            self.edit_graph.sel_pattern = len(self.edit_model.patterns) - 1
            self.edit_model.pattern_selection = {
                self.edit_graph.sel_pattern}
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()
        elif res == "has_pattern":
            self.edit_msg.configure(
                text=tr("パターンが含まれています。先に解除してください"),
                text_color=MSG_WARN)
        elif res == "unselected_in_range":
            self.edit_msg.configure(
                text=tr("範囲内に選択していない点があるため、"
                        "グループ化できません"),
                text_color=MSG_WARN)

    def _menu_delete(self):
        """メニューの「削除」。確認なしで直接削除する(=171 実機FB2。
        Delete キーと同じ挙動。取り消しは Ctrl+Z)。=241: 選択に
        パターンが含まれていてもまとめて消す(delete_selected_any)。"""
        if self.edit_model.delete_selected_any():
            self.edit_graph.sel_pattern = None
            self._edit_on_change()
            self._edit_on_select()
            self.edit_graph.redraw()

    # ---- 確認ダイアログ(小さなモーダル) ----

    def _ask_confirm(self, message: str, yes_text: str) -> bool:
        """[yes_text]/[キャンセル] の確認。テストは SCRIPT_EDIT_CONFIRM_AUTO。"""
        if SCRIPT_EDIT_CONFIRM_AUTO is not None:
            return bool(SCRIPT_EDIT_CONFIRM_AUTO)
        res = self._ask_buttons(message, [("yes", yes_text, "danger"),
                                          ("cancel", tr("キャンセル"),
                                           "ghost")])
        return res == "yes"

    def _resolve_unsaved(self) -> bool:
        """未保存の編集の確認(仕様 14b/4.10)。

        戻り値 True=続行してよい(保存済み/破棄/そもそも未編集)、
        False=「やめる」(元の操作を中止する)。
        """
        # =232: 左右2本のどちらかが未保存なら確認を出す
        if not self.edit_mode or self.edit_model is None \
                or not any(m.dirty for m in self.edit_models):
            return True
        if SCRIPT_EDIT_UNSAVED_AUTO is not None:
            choice = SCRIPT_EDIT_UNSAVED_AUTO
        else:
            choice = self._ask_buttons(
                tr("スクリプトに未保存の編集があります。"),
                [("save", tr("保存する"), "primary"),
                 ("discard", tr("破棄する"), "danger"),
                 ("cancel", tr("やめる"), "ghost")])
        if choice == "cancel" or choice is None:
            return False
        if choice == "save":
            if self._edit_path:
                return self._edit_save()
            return self._edit_save_as()
        # discard
        for m in self.edit_models:
            m.dirty = False
        return True

    def _ask_buttons(self, message: str, buttons) -> str | None:
        """小さなモーダルでボタンを選ばせる。戻り値=押したボタンのキー。"""
        dlg = ctk.CTkToplevel(self)
        dlg.title(tr("確認"))
        dlg.resizable(False, False)
        dlg.transient(self)
        result = {"key": None}
        ctk.CTkLabel(dlg, text=message, font=ctk.CTkFont(size=12),
                     wraplength=380, justify="left"
                     ).pack(padx=18, pady=(16, 10))
        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(padx=18, pady=(0, 14))

        def choose(key):
            result["key"] = key
            dlg.destroy()

        for key, label, style in buttons:
            kw = {}
            if style == "primary":
                kw = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER)
            elif style == "danger":
                kw = dict(fg_color="#c9451a", hover_color="#a83a16")
            else:
                kw = dict(fg_color="transparent", border_width=1,
                          border_color=MUTED,
                          text_color=("gray20", "gray85"),
                          hover_color=("gray85", "gray25"))
            ctk.CTkButton(row, text=label, width=96, height=30,
                          command=lambda k=key: choose(k), **kw
                          ).pack(side="left", padx=4)
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        _place_popup(dlg, self, 420, 120)
        try:
            dlg.grab_set()
        except Exception:
            pass
        self.wait_window(dlg)
        return result["key"]

    # ---- 保存 / 新規保存(仕様 4.10) ----

    def _edit_save(self) -> bool:
        """上書き保存。同じファイルが他でも使われていれば警告を挟む。"""
        if not self._edit_path:
            return self._edit_save_as()
        # 検出範囲=編集画面で開いているシナリオ全体(未保存の編集内容込み・
        # 自動紐づけも走査=仕様 14a)。owner が無い経路では走査しない。
        if self.owner is not None:
            try:
                uses = self.owner.script_usage(self._edit_path)
            except Exception:
                uses = []
            others = max(0, len(uses) - 1)
            if others >= 1:
                msg = tr("このファイルは他の {0} 箇所でも使われています。"
                         "上書きしますか？").format(others)
                msg += "\n" + "\n".join(uses)
                if not self._ask_confirm(msg, tr("上書きする")):
                    return False
        return self._edit_write(self._edit_path)

    def _edit_save_as(self) -> bool:
        """新規保存。初期フォルダは前回保存フォルダ(独立キーで永続=270)、
        無ければシナリオのフォルダ。既定名=音声名+タグ.funscript(仕様 12)。"""
        from tkinter import filedialog
        # =270: レビュー画面の新規保存は独立キー "review_save" で前回の
        # 保存フォルダを記憶する(編集画面のダイアログ群とは別管理)。
        initial_dir = _load_dialog_dir("review_save")
        if not (initial_dir and os.path.isdir(initial_dir)):
            initial_dir = ""
            if self.owner is not None and getattr(self.owner, "path", None):
                initial_dir = os.path.dirname(
                    os.path.abspath(self.owner.path))
            elif self.spec.get("audio") or self.spec.get("video"):
                initial_dir = os.path.dirname(
                    self.spec.get("audio") or self.spec.get("video"))
        src = self.spec.get("audio") or self.spec.get("video") or ""
        base = os.path.splitext(os.path.basename(src))[0] if src else ""
        # =224: csv を編集しているときは csv として保存する。
        # 自動紐づけのタグ(ufo/a10)も既定のファイル名へ添えておく
        csv = self._edit_kind == "csv"
        # =225: 自動紐づけのタグ(_twist / _vib / _ufo / _a10)を既定名へ添える
        tag = SCRIPT_EDIT_TAGS.get(self._edit_type, "")
        if csv:
            ext, title = ".csv", tr("csvの新規保存")
            types = [("csv", "*.csv"), (tr("すべて"), "*.*")]
        else:
            ext, title = ".funscript", tr("funscriptの新規保存")
            types = [("funscript", "*.funscript"), (tr("すべて"), "*.*")]
        initial_file = (base + tag + ext) if base else ext
        path = filedialog.asksaveasfilename(
            parent=self, title=title,
            initialdir=initial_dir or None, initialfile=initial_file,
            defaultextension=ext, filetypes=types)
        if not path:
            return False
        _remember_dialog_dir(path, "review_save")
        if not self._edit_write(path):
            return False
        # 保存したファイルをアイテムのトラックへ紐づける(仕様 12)。
        self._bind_saved_file(path)
        self._edit_path = path
        self._edit_new = False
        self.edit_save_btn.configure(state="normal")
        return True

    def _edit_write(self, path: str) -> bool:
        """書き出し本体。tmp→os.replace・actions以外のキー保持(仕様 14c)。
        =204: パターン記憶("rvp" キー)を同梱する(パターン0個なら
        キーごと書かない=削除)。"""
        from . import script_edit
        try:
            if self._edit_kind == "csv":
                # =224: csv は階段+100ms単位。5列はもう一方のチャンネルを
                # そのまま残して1行に両方を書く。**パターン記憶は書けない**
                # (ユーザー決定=ファイル形式は変えない)。
                if self._edit_pair:
                    # =232: 5列の左右同時編集=2本のモデルをそのまま書く
                    script_edit.write_csv(
                        path, self._edit_models[0].points, 5,
                        self._edit_models[1].points, 0)
                else:
                    script_edit.write_csv(
                        path, self.edit_model.points, self._edit_cols or 3,
                        self._edit_csv_other if (self._edit_cols == 5)
                        else None, self._edit_csv_ch)
            else:
                script_edit.write_funscript(path, self.edit_model.points,
                                            self._edit_extra,
                                            self.edit_model.export_patterns())
        except OSError as e:
            self.edit_msg.configure(
                text=tr("ファイルへ書き込めませんでした(他のアプリで使用中・"
                        "読み取り専用・アクセス権なし等の可能性):\n{0}")
                .format(e), text_color=MSG_ERROR)
            return False
        for m in self.edit_models:        # =232: 左右とも保存済みにする
            m.dirty = False
        msg = tr("保存しました") + ": " + os.path.basename(path)
        color = MSG_OK
        # =224: csv にはパターン記憶の置き場が無い(初回だけ案内する)
        if self._edit_kind == "csv" \
                and any(m.patterns for m in self.edit_models) \
                and not self._csv_pat_warned:
            self._csv_pat_warned = True
            msg += "  " + tr("（csvではパターンの記憶は保存されません。"
                             "開き直すと点だけになります）")
            color = MSG_WARN
        self.edit_msg.configure(text=msg, text_color=color)
        # 保存後は編集モードのまま留まる(仕様 11)
        return True

    def _bind_saved_file(self, path: str):
        """新規保存したファイルをアイテムのトラックへ紐づける(仕様 12)。

        自動モードだった場合は**手動モードへ切り替えて**トラック行を作る。
        シナリオ .json 自体の保存は従来どおり編集画面の「保存」で行う
        (この時点では .json は書かない)。
        行が別のアイテムへ使い回されていた場合は紐づけを行わない
        (レビューを開いたまま編集画面でイベントを切り替えたケース)。
        """
        row = self._row
        try:
            if row is None or not row.winfo_exists() or \
                    getattr(row, "audio_rel", None) != self._row_audio_rel:
                self.edit_msg.configure(
                    text=tr("アイテムの行が見つからないため、トラックへの"
                            "紐づけは行われませんでした"),
                    text_color=MSG_WARN)
                return
        except Exception:
            return
        ttype = self._edit_type
        rel = _safe_relpath(path, row.base_dir)
        try:
            if row.mode == "auto":
                # 手動モードへ切り替え(自動の解決結果を行として引き継ぐ)
                row.mode_var.set(tr("手動"))
                row._on_mode_change()
            hit = None
            for e in row.track_rows:
                if e["type_var"].get() == ttype:
                    hit = e
                    break
            if hit is not None:
                hit["fs_path"] = rel
                row._update_track_row(hit)
            else:
                row._add_track_row(ttype, rel)
            row._update_mode_ui()
        except Exception:
            pass      # 紐づけに失敗しても保存自体は成功している
        # 編集対象候補にも加えて、コンボから選び直せるようにする
        # (=224: csv は (…, ch) 付き。5列で新規保存したときは左右2つ足す)
        ch = self._edit_csv_ch if self._edit_kind == "csv" else None
        entry = (ttype, path, 0.0, None, ch)
        labels = []
        found = False
        self._edit_tracks = [t for t in self._edit_tracks]
        for i, (t, p, lo, hi, c) in enumerate(self._edit_tracks):
            if t == ttype and c == ch and \
                    os.path.normcase(os.path.abspath(p)) == \
                    os.path.normcase(os.path.abspath(path)):
                self._edit_tracks[i] = entry
                found = True
                self._edit_index = i
        if not found:
            # =232: 5列csv は**1項目(左右)**として加える(=224 の
            # 「（左）」「（右）」に分ける方式は廃止)
            if self._edit_kind == "csv" and self._edit_cols == 5:
                entry = (entry[0], entry[1], entry[2], entry[3], (0, 1))
            self._edit_tracks.append(entry)
            self._edit_index = len(self._edit_tracks) - 1
        labels = [self._edit_track_label(i)
                  for i in range(len(self._edit_tracks))]
        sel = labels[self._edit_index]
        self.edit_track_menu.configure(values=labels)   # =234: 新規作成は
        #                                    「＋新規」ボタンからだけ
        self.edit_track_var.set(sel)
        self.edit_type_menu.pack_forget()


class StateChoiceEditor(ctk.CTkFrame):
    """=275: 「選択肢でステート移行」の編集ブロック。

    イベントの選択肢エディタ(ScenarioEditor.choice_*)と同じ項目構成だが、
    行き先が「ステート」または「イベント」になる(行ごとに種別を切り替える)。
    transition raw との往復: load(t) / collect(where) → (err, transition)。
    表示タイミングはステート基準(開始時/全チャンネル終了時/開始から指定秒)。
    """

    KIND_STATE = tr("ステート")
    KIND_EVENT = tr("イベント")
    TLIM_NONE = tr("無制限")
    TLIM_SEC = tr("時間指定")
    DFLT_FIRST = tr("先頭の選択肢へ")
    DFLT_RANDOM = tr("選択肢から等確率で抽選")
    DFLT_STATE = tr("指定ステートへ")
    DFLT_EVENT = tr("指定イベントへ")
    SHOW_START = tr("ステート開始時")
    SHOW_END = tr("ステート内の全チャンネル終了時")
    SHOW_SEC = tr("ステート開始から指定時間後")

    def __init__(self, master, owner):
        super().__init__(master, fg_color="transparent")
        self.owner = owner
        self.rows: list[dict] = []
        self.extra: dict = {}
        self.timeout_ops: list = []
        self._state_ids: list[str] = []

        self.rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_frame.pack(fill="x", pady=(2, 0))
        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.pack(fill="x", pady=(2, 0))
        self.add_btn = ctk.CTkButton(
            add_row, text=tr("＋ 選択肢を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self.add_row())
        self.add_btn.pack(side="left")
        ctk.CTkLabel(add_row, text=tr("(最小1・最大9)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        tlim_row = ctk.CTkFrame(self, fg_color="transparent")
        tlim_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(tlim_row, text=tr("タイムリミット:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.tlim_var = tk.StringVar(value=self.TLIM_NONE)
        CTkOptionMenu(
            tlim_row, variable=self.tlim_var, width=110, height=26,
            values=[self.TLIM_NONE, self.TLIM_SEC],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self.update_ui()).pack(side="left", padx=6)
        self.tsec_var = tk.StringVar(value="30")
        self.tsec_entry = ctk.CTkEntry(tlim_row, textvariable=self.tsec_var,
                                       width=42, height=26, justify="right")
        self.tsec_label = ctk.CTkLabel(tlim_row, text=tr("秒"),
                                       font=ctk.CTkFont(size=11),
                                       text_color=TEXT_MUTED)
        self.toops_btn = ctk.CTkButton(
            tlim_row, text="", width=150, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_timeout_ops)

        dflt_row = ctk.CTkFrame(self, fg_color="transparent")
        dflt_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(dflt_row, text=tr("デフォルト遷移先:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.dflt_var = tk.StringVar(value=self.DFLT_FIRST)
        CTkOptionMenu(
            dflt_row, variable=self.dflt_var, width=210, height=26,
            values=[self.DFLT_FIRST, self.DFLT_RANDOM,
                    self.DFLT_STATE, self.DFLT_EVENT],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self.update_ui()).pack(side="left", padx=6)
        self.dflt_to_var = tk.StringVar(value="")
        self.dflt_to_menu = CTkOptionMenu(
            dflt_row, variable=self.dflt_to_var, width=140, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        ctk.CTkLabel(dflt_row, text=tr("(タイムアウト時の行き先)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="right")

        stay_row = ctk.CTkFrame(self, fg_color="transparent")
        stay_row.pack(fill="x", pady=(4, 0))
        self.stay_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            stay_row, text=tr("▶▶で飛ばさない(選択されるまで待機)"),
            variable=self.stay_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
        ).pack(side="left")
        ctk.CTkLabel(stay_row,
                     text=tr("(選択肢の表示中は▶▶を無効にする。タイムアウトは進む)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        show_row = ctk.CTkFrame(self, fg_color="transparent")
        show_row.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(show_row, text=tr("表示タイミング:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.show_var = tk.StringVar(value=self.SHOW_START)
        CTkOptionMenu(
            show_row, variable=self.show_var, width=230, height=26,
            values=[self.SHOW_START, self.SHOW_END, self.SHOW_SEC],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self.update_ui()).pack(side="left", padx=6)
        self.ssec_var = tk.StringVar(value="30")
        self.ssec_entry = ctk.CTkEntry(show_row, textvariable=self.ssec_var,
                                       width=42, height=26, justify="right")
        self.ssec_label = ctk.CTkLabel(show_row, text=tr("秒"),
                                       font=ctk.CTkFont(size=11),
                                       text_color=TEXT_MUTED)
        ctk.CTkLabel(show_row,
                     text=tr("(選択/タイムアウトで即座に移行。全チャンネルが終わっても選ばれるまで待機)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

    # ---- 候補の名前 ----

    def set_state_ids(self, ids: list[str]):
        self._state_ids = list(ids) or [""]

    def _ids_for(self, kind: str) -> list[str]:
        if kind == self.KIND_EVENT:
            return self.owner._event_id_choices()
        return list(self._state_ids) or [""]

    # ---- 行 ----

    def add_row(self, label: str = "", to=None, raw: dict | None = None):
        if len(self.rows) >= 9:
            return
        to_event = isinstance(to, dict)
        to_id = to.get("event") if to_event else to
        row = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        num = ctk.CTkLabel(row, text="", width=22, font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        num.pack(side="left")
        label_var = tk.StringVar(value=label)
        label_entry = ctk.CTkEntry(row, textvariable=label_var, width=220,
                                   height=26,
                                   placeholder_text=tr("ボタンの表示テキスト"))
        label_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        kind_var = tk.StringVar(
            value=self.KIND_EVENT if to_event else self.KIND_STATE)
        entry = {"frame": row, "num": num, "label_var": label_var,
                 "label_entry": label_entry, "kind_var": kind_var,
                 "raw": dict(raw or {})}
        kind_menu = CTkOptionMenu(
            row, variable=kind_var, width=96, height=26,
            values=[self.KIND_STATE, self.KIND_EVENT],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v, e=entry: self._on_kind_change(e))
        kind_menu.pack(side="left", padx=(6, 0))
        ids = self._ids_for(kind_var.get())
        to_var = tk.StringVar(value=to_id if to_id in ids else ids[0])
        to_menu = CTkOptionMenu(row, variable=to_var, width=150, height=26,
                                values=ids,
                                fg_color=("gray75", "gray28"),
                                button_color=("gray70", "gray33"))
        to_menu.pack(side="left", padx=6)
        entry.update({"to_var": to_var, "to_menu": to_menu,
                      "kind_menu": kind_menu})
        if self.owner._has_vars():
            ops_btn = ctk.CTkButton(
                row, text="", width=86, height=26,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
                command=lambda e=entry: self._edit_row_ops(e))
            ops_btn.pack(side="left", padx=(6, 0))
            entry["ops_btn"] = ops_btn
            self._update_ops_btn(entry)
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_row(e)
                      ).pack(side="left", padx=(4, 0))
        self.rows.append(entry)
        self._renumber()

    def _on_kind_change(self, entry):
        ids = self._ids_for(entry["kind_var"].get())
        entry["to_menu"].configure(values=ids)
        if entry["to_var"].get() not in ids:
            entry["to_var"].set(ids[0])

    def _update_ops_btn(self, entry):
        if entry.get("ops_btn"):
            n = len(entry["raw"].get("ops") or [])
            entry["ops_btn"].configure(text=tr("変数({0})").format(n))

    def _edit_row_ops(self, entry):
        idx = self.rows.index(entry) + 1

        def on_ok(result):
            if result.get("ops"):
                entry["raw"]["ops"] = result["ops"]
            else:
                entry["raw"].pop("ops", None)
            self._update_ops_btn(entry)
        self.owner.open_ops_dialog(
            tr("選択肢{0}").format(idx),
            [(tr("この選択肢が選ばれた時(ops)"), "ops",
              entry["raw"].get("ops") or [])], on_ok)

    def _edit_timeout_ops(self):
        def on_ok(result):
            self.timeout_ops = result.get("on_timeout") or []
            self._update_toops_btn()
        self.owner.open_ops_dialog(
            tr("タイムアウト時"),
            [(tr("タイムアウト確定時のみ(on_timeout)"), "on_timeout",
              self.timeout_ops)], on_ok)

    def _update_toops_btn(self):
        self.toops_btn.configure(
            text=tr("時間切れ時の変数操作({0})").format(len(self.timeout_ops)))

    def _delete_row(self, entry):
        if len(self.rows) <= 1:
            self.owner._report("warn", tr("削除できません"),
                               tr("選択肢は最低1つ必要です"))
            return
        self.rows.remove(entry)
        entry["frame"].destroy()
        self._renumber()

    def _renumber(self):
        for i, e in enumerate(self.rows):
            e["num"].configure(text=f"{i + 1}.")
        self.add_btn.configure(
            state="normal" if len(self.rows) < 9 else "disabled")

    def clear_rows(self):
        for e in self.rows:
            e["frame"].destroy()
        self.rows = []

    # ---- 付随入力の表示切替 ----

    def update_ui(self):
        if self.tlim_var.get() == self.TLIM_SEC:
            self.tsec_entry.pack(side="left", padx=(6, 2))
            self.tsec_label.pack(side="left")
            if self.owner._has_vars():
                self._update_toops_btn()
                self.toops_btn.pack(side="left", padx=(10, 0))
            else:
                self.toops_btn.pack_forget()
        else:
            for w in (self.tsec_entry, self.tsec_label, self.toops_btn):
                w.pack_forget()
        d = self.dflt_var.get()
        if d in (self.DFLT_STATE, self.DFLT_EVENT):
            ids = self._ids_for(self.KIND_EVENT if d == self.DFLT_EVENT
                                else self.KIND_STATE)
            self.dflt_to_menu.configure(values=ids)
            if self.dflt_to_var.get() not in ids:
                self.dflt_to_var.set(ids[0])
            if not self.dflt_to_menu.winfo_manager():
                self.dflt_to_menu.pack(side="left", padx=(6, 0))
        else:
            self.dflt_to_menu.pack_forget()
        if self.show_var.get() == self.SHOW_SEC:
            self.ssec_entry.pack(side="left", padx=(6, 2))
            self.ssec_label.pack(side="left")
        else:
            self.ssec_entry.pack_forget()
            self.ssec_label.pack_forget()

    # ---- 往復 ----

    def load(self, t: dict | None):
        """transition raw(when.type==choice)を読み込む。None=初期状態。"""
        t = t if isinstance(t, dict) else {}
        self.clear_rows()
        self.extra = {k: v for k, v in t.items()
                      if k not in ("when", "choice", "timeout", "default",
                                   "show", "on_timeout", "skip")}
        self.timeout_ops = list(t.get("on_timeout") or [])
        self.stay_var.set(t.get("skip") == "stay")
        for ent in t.get("choice") or []:
            if isinstance(ent, dict):
                self.add_row(str(ent.get("label", "")), ent.get("to"), raw=ent)
        if not self.rows:
            self.add_row()
        traw = t.get("timeout")
        if isinstance(traw, dict):
            self.tlim_var.set(self.TLIM_SEC)
            self.tsec_var.set(f"{float(traw.get('seconds', 0)):g}")
        else:
            self.tlim_var.set(self.TLIM_NONE)
        draw = t.get("default")
        if draw == "random":
            self.dflt_var.set(self.DFLT_RANDOM)
        elif isinstance(draw, dict) and isinstance(draw.get("to"), dict) \
                and draw["to"].get("event"):
            self.dflt_var.set(self.DFLT_EVENT)
            self.dflt_to_var.set(draw["to"]["event"])
        elif isinstance(draw, dict) and isinstance(draw.get("to"), str):
            self.dflt_var.set(self.DFLT_STATE)
            self.dflt_to_var.set(draw["to"])
        else:
            self.dflt_var.set(self.DFLT_FIRST)
        sraw = t.get("show", "start" if not t else "end")
        if sraw == "start":
            self.show_var.set(self.SHOW_START)
        elif isinstance(sraw, dict):
            self.show_var.set(self.SHOW_SEC)
            self.ssec_var.set(f"{float(sraw.get('seconds', 0)):g}")
        else:
            self.show_var.set(self.SHOW_END)
        self.update_ui()

    def collect(self, where: str) -> tuple:
        """UIから transition raw を組み立てる。(err, value)。"""
        mark = self.owner._want_mark
        events = self.owner.data["events"]
        entries = []
        for i, e in enumerate(self.rows):
            label = e["label_var"].get().strip()
            to = e["to_var"].get()
            is_event = e["kind_var"].get() == self.KIND_EVENT
            if not label:
                mark(e.get("label_entry"), "error")
                return tr("{0}: 選択肢{1}の表示テキストが空です").format(where, i + 1), None
            ok = (to in events) if is_event else (to in self._state_ids)
            if not ok:
                mark(e.get("to_menu"), "error")
                return tr("{0}: 選択肢{1}の行き先が不正です").format(where, i + 1), None
            extra = {k: v for k, v in (e.get("raw") or {}).items()
                     if k not in ("label", "to")}
            entries.append({"label": label,
                            "to": {"event": to} if is_event else to,
                            **extra})
        if not 1 <= len(entries) <= 9:
            return tr("{0}: 選択肢は1〜9件にしてください").format(where), None
        value = {"when": {"type": "choice"}, "choice": entries}
        if self.tlim_var.get() == self.TLIM_SEC:
            try:
                total = float(self.tsec_var.get() or 0)
            except ValueError:
                total = 0
            if total <= 0:
                mark(self.tsec_entry, "error")
                return tr("{0}: タイムリミットの時間が不正です").format(where), None
            value["timeout"] = {"seconds": round(total, 3)}
        d = self.dflt_var.get()
        if d == self.DFLT_RANDOM:
            value["default"] = "random"
        elif d == self.DFLT_STATE:
            to = self.dflt_to_var.get()
            if to not in self._state_ids:
                mark(self.dflt_to_menu, "error")
                return tr("{0}: デフォルト遷移先のステートを選択してください").format(where), None
            value["default"] = {"to": to}
        elif d == self.DFLT_EVENT:
            to = self.dflt_to_var.get()
            if to not in events:
                mark(self.dflt_to_menu, "error")
                return tr("{0}: デフォルト遷移先のイベントを選択してください").format(where), None
            value["default"] = {"to": {"event": to}}
        show = self.show_var.get()
        if show == self.SHOW_START:
            value["show"] = "start"
        elif show == self.SHOW_SEC:
            try:
                total = float(self.ssec_var.get() or 0)
            except ValueError:
                mark(self.ssec_entry, "error")
                return tr("{0}: 表示タイミングの時間が不正です").format(where), None
            value["show"] = {"seconds": round(total, 3)}
        # "end"(全チャンネル終了時)は既定なので省略
        if self.stay_var.get():
            value["skip"] = "stay"
        if self.timeout_ops:
            value["on_timeout"] = self.timeout_ops
        for k, v in self.extra.items():
            value.setdefault(k, v)
        return None, value

    def targets(self) -> tuple:
        """現在のUI上の (ステート宛てID集合, イベント宛てID集合)。"""
        st, ev = set(), set()
        for e in self.rows:
            (ev if e["kind_var"].get() == self.KIND_EVENT else st).add(
                e["to_var"].get())
        return st, ev


class ScenarioEditor(ctk.CTkToplevel):
    """シナリオ編集ウィンドウ。"""

    EV_END_CHOICES = (tr("合計N秒で次へ"), tr("合計N回の再生で次へ"), tr("N回のステート移行で次へ"))
    EV_END_COND = tr("変数条件で次へ")   # 変数宣言がある時だけ選択肢に追加(相関制御)
    EV_END_STATES = tr("指定ステートが終了したら次へ")   # 指定ステートの終了で終了
    # =168: 無限(イベント自身は終わらない)。ステート形式・通常の両方に追加。
    EV_END_INFINITE = tr("無限")   # =169: 「無限(終了しない)」から改称
    # ステート移行条件のコンボ。=165で並び順をユーザー指定の順へ変更し、
    # 「チャンネル時間でステート移行」(channel_time)を撤廃した(経過時間と
    # 役割が重複するため)。「判定式」は変数宣言がある時だけ末尾へ追加する。
    TRANS_CHOICES = (tr("経過時間でステート移行"),
                     tr("指定チャンネル終了でステート移行"),
                     tr("全チャンネル終了でステート移行"),
                     tr("チャンネル回数でステート移行"),
                     tr("選択肢でステート移行"),   # =275
                     tr("ステート移行なし"))
    TRANS_TARGET_COLS = 3   # =287: 移行先チェックの折返し列数
    TRANS_COND = tr("判定式でステート移行")
    TRANS_CHOICE = tr("選択肢でステート移行")   # =275: 選ばれた選択肢で移行先が決まる   # 変数宣言がある時だけ選択肢に追加(相関制御)
    TRANS_NONE = tr("ステート移行なし")
    # =62: ステートに入ってからの経過時間。チャンネルを見ないので
    # 音声なし(有効ch0)ステートでも使える=無音待機ノード。
    TRANS_STATE_TIME = tr("経過時間でステート移行")
    TRANS_ALL_CH = tr("全チャンネル終了でステート移行")   # 全chが自然終了したら移行
    TRANS_CHANNEL_END = tr("指定チャンネル終了でステート移行")  # 指定chの終了で移行
    TRANS_CHANNEL_COUNT = tr("チャンネル回数でステート移行")
    # イベントの遷移方法(「次のイベント:」のコンボ)。=166で「固定」と「なし」を追加。
    # 「固定」= 1つのイベントへ必ず進む(JSONは "next": "eventB" の文字列形式)。
    # 「なし」= 遷移しない(JSONは "next": null)。
    # 「変数分岐」「数値入力」は変数宣言があるときだけ出す(相関制御)。
    NEXT_FIXED = tr("固定")
    NEXT_BRANCH = tr("分岐")
    NEXT_CHOICE = tr("選択肢")
    NEXT_COND = tr("変数分岐")
    NEXT_INPUT = tr("数値入力")
    NEXT_NONE = tr("なし")

    # イベント終了条件(■イベント枠のコンボ)
    EVEND_ALL = tr("全チャンネルが終了した時")
    # =52: 動画はチャンネルのアイテムになったので「動画が終わった時」は
    # 「指定チャンネルが終了した時」(動画chを指定)へ一本化した。
    EVEND_CHANNEL = tr("指定チャンネルが終了した時")
    EVEND_DURATION = tr("合計時間が経過した時")   # イベント開始からの累積秒。全ch無限を許可(相関制御)
    EVEND_COND = tr("変数条件が成立した時")
    EVEND_STATE = tr("ステートのイベント終了に委譲")
    # =169: 「無限(終了しない)」→「無限」(ユーザー決定)。出口が
    # 選択肢・数値入力・判定式(常に監視)でもあるため、「終了しない」
    # という言い方が実際の挙動と合わなくなるため。
    EVEND_INFINITE = tr("無限")   # =168
    # 選択肢/数値入力の表示タイミング。=168: 「無限」では1つ目が永久に来ない
    SHOW_TIMES = (tr("イベント終了条件の達成時"), tr("イベント開始時"),
                  tr("イベント開始から指定時間後"))
    # =168: 終了条件が「無限」のときの遷移方法。判定式は**再生中に常時監視**
    # する別モードなので名前を分ける(ユーザー命名)。
    NEXT_COND_WATCH = tr("判定式(常に監視)")

    # =256: BGMの3択(引き継ぐ/指定/オフ)。既定は「引き継ぐ」= JSONキー省略
    BGM_INHERIT = tr("前のBGMを引き継ぐ")
    BGM_SET = tr("BGMを指定")
    BGM_OFF = tr("BGMオフ")

    def __init__(self, master, path: str | None, on_saved=None,
                 review_host=None):
        super().__init__(master)
        self.title(tr("シナリオ編集 - RVP"))
        self.minsize(920, 780)
        # =164: アイテムレビュー画面。同時に1つまで(開いていれば差し替え)。
        # review_host は本編の再生を止めて初期音量をもらう相手(main.RVPApp)。
        # テストから直接生成する経路では None(その場合は何もしない)。
        self._review_dlg = None
        self.review_host = review_host
        # =115: 前回閉じたときのサイズ・位置・最大化を復元する。
        # 保存が無い/どのモニタにも載っていない場合だけ従来の既定配置
        # (左上0,0に WIN_W×WIN_H)。
        self.winmem = WindowMemory(self, "editor")
        self._winmem_restored = self.winmem.restore()
        if not self._winmem_restored:
            self._apply_window_size()
        self.winmem.watch()
        self.winmem.install_close_hook()
        self.on_saved = on_saved

        self.path = path
        if path:
            self.base_dir = os.path.dirname(os.path.abspath(path))
            try:
                self.data = _load_raw(path)
            except Exception:
                # 読み込み不可(アクセス拒否・破損等)。作りかけの空ウィンドウを
                # 残さないよう自身を破棄してから呼び出し元へ伝える
                # (main.on_edit_scenario がcatchしてエラーダイアログを出す)。
                self.destroy()
                raise
            # タイトルは常にJSONファイル名に追従させる(ファイル名が正)
            self.data["title"] = _title_from_path(path)
        else:
            self.base_dir = os.getcwd()
            # 未保存。タイトルは初回保存時にファイル名から確定する。
            # =252: 新規シナリオは「デバイス連動:OFF」が既定(ユーザー決定)。
            self.data = {"title": "", "device_enabled": False,
                         "start": "event1",
                         "events": {"event1": json.loads(json.dumps(DEFAULT_EVENT))}}

        # =252: デバイス連動フラグ(JSONトップレベル "device_enabled")。
        # キー省略=ON(旧シナリオは従来どおり)。OFFはデバイス関連UIを隠し、
        # 再生時の駆動も止める。JSON上の紐づけ・担当は**保持**される
        # (UIは非表示にするだけで、裏ではデータを読み込んだまま)。
        self.device_enabled = bool(self.data.get("device_enabled", True))
        # =256: BGM機能フラグ(JSONトップレベル "bgm_enabled")。キー省略=OFF
        # (BGMを持つ旧シナリオは存在しないため device_enabled とは逆の既定)。
        # OFFはBGMブロックを隠すだけで、各ノードの "bgm" 定義は保持される。
        self.bgm_enabled = bool(self.data.get("bgm_enabled", False))
        # =250: 紹介文(旧・説明欄)。常設テキストボックスを廃止し、ダイアログ
        # 経由でこの変数に保持する。ファイルへは _do_save で書き出す。
        self._detail_text = str(self.data.get("detail", "") or "")

        self.selected: str | None = None
        self.sel_state: str | None = None   # 編集中のステートID(ステート形式のみ)
        # =277: UNDO/REDO(スナップショット履歴)。_hist_reset() で起点を作る
        # までは _hist_last=None でチェックポイントは何もしない
        self._hist_undo: list = []
        self._hist_redo: list = []
        self._hist_last: dict | None = None
        self._hist_suspend = False
        # =270: ファイルダイアログの前回フォルダは設定ファイルへ永続化する
        # 方式になった(_dialog_initialdir / _remember_dialog_dir)。
        # ウィンドウ属性(_last_audio_dir 等)への一時記憶は廃止。

        self._build_ui()
        self._apply_device_enabled()   # =252: OFFで開いたシナリオへ即反映
        self._apply_bgm_enabled()      # =256: 同上(BGM)
        self._init_map_toggle()
        self._init_map_undock()        # =300
        self._redraw_canvas()
        # =64(体感の起動短縮): ここまでで骨組み(ツリー・図・パネルの枠)は
        # 揃っているので、**先にウィンドウを見せてから**中身(選択イベントの
        # パネル)を作る。CTkToplevel は生成時に withdraw して約200ms後に
        # deiconify するため、__init__ を最後まで回してから表示すると
        # 「押しても何も出ない時間」がそのぶん延びる。
        try:
            self.deiconify()
            self.update()
        except Exception:
            pass
        first = self.data.get("start") or (next(iter(self.data["events"]), None))
        if first in self.data["events"]:
            self._select(first)
        # =277: ここまでの読み込み・初期選択は履歴に含めない(起点にする)
        self._hist_reset()

        _front_window(self)
        # CTkToplevelは内部でwithdraw→約200ms後にdeiconifyするため、その際に
        # ジオメトリが解除されることがある。遅延して再適用する(=58で
        # 最大化をやめ固定サイズ+左上0,0にしたので、その再適用に変わった)。
        for delay in (220, 620):
            try:
                self.after(delay, self._apply_window_size)
            except Exception:
                pass

        # OSからのファイルD&D(=44)。編集ウィンドウ全体を単一のドロップ先として
        # 登録し、落下点の座標からアイテム枠/チャンネル枠を特定して振り分ける
        # (tkdndは落下点直下から親方向へ登録済みウィジェットを探すため、
        # 動的に増えるアイテム行も個別登録なしで受けられる)。
        # tkinterdnd2 未導入・ロード失敗時は静かに無効(他は従来どおり)。
        self._dnd_ok = False
        if TkinterDnD is not None:
            try:
                TkinterDnD._require(self)
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_dnd_drop)
                self._dnd_ok = True
            except Exception:
                self._dnd_ok = False
        # =160: この初期化は _build_ui より後なので、既に組み上がっている
        # チャンネル枠の「(D&D可)」を結果に合わせて出し入れする
        # (以降に作り直されるパネルは build 時に _dnd_ok を見る)。
        self._refresh_dnd_hints()

    def _refresh_dnd_hints(self):
        """全チャンネル枠の「(D&D可)」を `_dnd_ok` に合わせる(=160)。"""
        for sec in getattr(self, "channel_sections", {}).values():
            fn = getattr(sec, "set_dnd_hint", None)
            if callable(fn):
                fn(self._dnd_ok)

    def _dnd_hit(self, x, y):
        """落下点(スクリーン座標)に当たるチャンネル枠/アイテム枠/動画行を返す。

        winfo_containing は X サーバ/重なり順に依存して None を返すことが
        あるため、winfo_rootx/rooty+width/height の矩形判定で決定的に解決する
        (Windows実機のtkdnd %X %Y も同じスクリーン座標系)。表示中(ismapped)
        のウィジェットのみ対象。
        戻り値 (ChannelSection|None, ItemRow|None, 動画行の上か)。
        """
        def inside(w):
            try:
                if w is None or not w.winfo_ismapped():
                    return False
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                return (wx <= x < wx + w.winfo_width()
                        and wy <= y < wy + w.winfo_height())
            except Exception:
                return False

        for sec in self.channel_sections.values():
            if inside(sec):
                for r in sec.rows:
                    if inside(r):
                        return sec, r
                return sec, None
        return None, None

    def _on_dnd_drop(self, event):
        """OSからのファイルD&D(=44/動画対応=50/=52でch内へ)の振り分け。

        落下点の矩形判定でアイテム枠(ItemRow)・チャンネル枠
        (ChannelSection)を特定して振り分ける:
          - 音声(.wav/.mp3): チャンネル枠へ落とすとアイテム追加
            (アイテム枠の上に落ちてもチャンネルへの追加扱い)
          - 動画(.mp4等): チャンネル枠へ落とすと動画アイテムを追加する
            (=52。動画はチャンネルの中身になったため、音声と同じ扱い。
            他chが動画chのとき・音声/スクリプトのあるchでは無視される)
          - .funscript/.csv: アイテム枠の上ならそのアイテムへ手動紐づけ
            (動画アイテムの上なら動画のトラックになる)。=278: 手動の
            トラック行(linear/twist/…)の上に落とせば、ファイル名タグに
            よらずその行の種別へ紐づける。**アイテム枠の外
            (チャンネル枠)ならスクリプトのみアイテムを追加**(=65。
            「＋スクリプト」と同じ。音声/動画のあるchでは無視される)
        対象外拡張子・落下先なし・失敗はすべて黙って無視(仕様=失敗時は
        画面上何も起こさない)。
        """
        try:
            paths = list(self.tk.splitlist(event.data))
            x, y = int(event.x_root), int(event.y_root)
        except Exception:
            return
        sec, row = self._dnd_hit(x, y)
        audio = [p for p in paths
                 if os.path.splitext(p)[1].lower() in (".wav", ".mp3")]
        fs = [p for p in paths
              if os.path.splitext(p)[1].lower() in (".funscript", ".csv")]
        # =252: デバイス連動OFFではスクリプトのD&Dを無効化(AQ8。音声・動画は
        # 従来どおり)。失敗は黙って無視する既存仕様に合わせて何も出さない。
        if not self.device_enabled:
            fs = []
        video = [p for p in paths
                 if os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        try:
            if video and sec is not None and self.selected:
                # 動画chは1つだけ(他chが動画chなら _video_lock で弾く)
                if not getattr(sec, "_video_lock", False):
                    sec.add_dropped_video(video)
            if audio and sec is not None:
                sec.add_dropped_audio(audio)
            elif audio and self.bgm_enabled and self._bgm_hit(x, y):
                # =256: BGMブロックへ落とした音声はBGMアイテムとして追加
                # (BGM:OFFのときは受けない=デバイスOFFのfs D&Dと同じ流儀)
                self._bgm_dropped_audio(audio)
            if fs and row is not None:
                # =278: トラック行の上ならその種別へ(タグ規則より優先)
                ttype = row.dropped_track_hit(x, y)
                for p in fs:
                    row.bind_dropped_fs(p, track_type=ttype)
            elif fs and sec is not None:
                # アイテム枠の外=チャンネル枠へ落とした(=65)。
                # スクリプト専用チャンネルの素材として追加する。
                sec.add_dropped_script(fs)
        except Exception:
            pass

    # イベント遷移図の高さ(=58で170→110。1600x900のデスクトップ対応)
    CANVAS_H = 110

    # 初期サイズと位置(=58 ユーザー決定)。以前は最大化していたが、
    # 1600x900のデスクトップでは画面を占有しすぎるため固定サイズにした。
    # メインウィンドウ(690x820・左上0,0)の右側に並べられる幅にしてある。
    WIN_W, WIN_H = 1360, 820

    # ---- =300: 2段目バー+イベント遷移図(ドッキング/別ウィンドウ共用) ----
    #
    # Tk はウィジェットの親を付け替えられないので、ドッキング解除/ドッキングの
    # たびに**バーと図を作り直す**。self.undo_btn / map_toggle_btn / map_fit_btn /
    # map_mode_btn / canvas / canvas_wrap / map_sash などの参照は現在の側を
    # 指すよう付け替える(既存ロジックは self.canvas 経由なのでそのまま動く)。
    # 履歴(=277)は編集画面に1本なので、どちらのウィンドウで Ctrl+Z しても
    # 同じ順で戻る。
    def _build_map_area(self, parent, docked: bool):
        before = {}
        if docked and getattr(self, "panel_wrap", None) is not None:
            before = {"before": self.panel_wrap}   # 再ドッキング時の差し込み位置
        # 2段目: イベント操作+インポート+図の折りたたみ(=255でmap_bar統合)
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        if docked:
            bar.pack(fill="x", padx=14, pady=(0, 0), **before)
        else:
            bar.pack(fill="x", padx=8, pady=(6, 0))
        ctk.CTkButton(bar, text=tr("＋ イベント追加"), width=110, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._add_event).pack(side="left", padx=4)
        ctk.CTkButton(bar, text=tr("イベント削除"), width=110, height=30,
                      fg_color="transparent", border_width=1,
                      border_color="#e05a5a", text_color="#e05a5a",
                      hover_color=("gray85", "gray25"),
                      command=self._delete_event).pack(side="left", padx=4)
        ctk.CTkButton(bar, text=tr("イベントコピー"), width=110, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._copy_event).pack(side="left", padx=4)
        self.tool_sep = _toolbar_sep(bar)
        ctk.CTkButton(bar, text=tr("インポート"), width=100, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._open_import_dialog).pack(side="left", padx=4)
        # =277: 元に戻す/やり直す(履歴が無いときは無効表示)
        _toolbar_sep(bar)
        self.undo_btn = ctk.CTkButton(
            bar, text=tr("↶ 元に戻す"), width=100, height=30,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._undo)
        self.undo_btn.pack(side="left", padx=4)
        self.redo_btn = ctk.CTkButton(
            bar, text=tr("↷ やり直す"), width=100, height=30,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._redo)
        self.redo_btn.pack(side="left", padx=4)
        # Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z。Toplevel への bind は配下の全
        # ウィジェット(bindtags にトップレベルを含む)で効く。=300: 別
        # ウィンドウ側にも同じものを束ねる(履歴は編集画面と共通の1本)
        top = parent.winfo_toplevel()
        top.bind("<Control-z>", lambda e: self._undo() or "break")
        top.bind("<Control-y>", lambda e: self._redo() or "break")
        top.bind("<Control-Z>", lambda e: self._redo() or "break")
        self._hist_update_buttons()
        # 図の折りたたみトグル(=58)+自動フィット(=72)。=255でこの段へ統合
        # (旧・図直上の細い行 map_bar は廃止。表記も「▼ 折りたたみ」/
        # 「▶ 折りたたみ中」へ変更=Q3)。区切り線は挟まない(テキストリンク調
        # で見た目が違うため=Q5)。canvas_wrap の pack アンカーとして
        # self.map_bar 名は維持する。
        self.map_bar = bar
        self.map_toggle_btn = ctk.CTkButton(
            bar, text="", width=110, height=18,
            font=ctk.CTkFont(size=11), anchor="w",
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray85", "gray25"),
            command=self._toggle_map)
        if docked:
            self.map_toggle_btn.pack(side="left", padx=(10, 0))
        self.map_fit_btn = ctk.CTkButton(
            bar, text=tr("図の高さに合わせる"), width=120, height=18,
            font=ctk.CTkFont(size=11), anchor="w",
            fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
            hover_color=("gray85", "gray25"),
            command=self._toggle_map_fit)
        # =299: 配置モード(自動/手動)。「図の高さに合わせる」の右隣
        self.map_mode_btn = ctk.CTkButton(
            bar, text="", width=96, height=18,
            font=ctk.CTkFont(size=11), anchor="w",
            fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
            hover_color=("gray85", "gray25"),
            command=self._toggle_map_mode)
        if not docked:
            # =300: 別ウィンドウでは折りたたみ/自動フィット/取っ手は無し。
            # 「配置」だけ出し、右端に「ドッキング」
            self.map_mode_btn.pack(side="left", padx=(8, 0))
            ctk.CTkButton(
                bar, text=tr("ドッキング"), width=96, height=18,
                font=ctk.CTkFont(size=11), anchor="e",
                fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
                hover_color=("gray85", "gray25"),
                command=self._dock_map).pack(side="right", padx=(0, 4))
        else:
            self.map_undock_btn = ctk.CTkButton(
                bar, text=tr("ドッキング解除"), width=110, height=18,
                font=ctk.CTkFont(size=11), anchor="w",
                fg_color="transparent", border_width=0, text_color=TEXT_MUTED,
                hover_color=("gray85", "gray25"),
                command=self._undock_map)

        # (=255: タイトルラベルは1段目 head_bar 内へ、折りたたみトグルと
        #  「図の高さに合わせる」は2段目 bar 内へ統合済み)

        # キャンバス(イベントの数珠つなぎ)。イベントが増えて図が画面外に
        # 伸びても見られるよう、縦横スクロールバーを付ける(grid配置)。
        # 高さは=58で170→110へ(1600x900のデスクトップ対応)。
        # CTkFrame は既定で 200px の高さを要求するので、grid_propagate(False)
        # + 明示の height で「キャンバス高さ+スクロールバー」に固定する
        # (=58。これをしないと CANVAS_H を下げても枠が縮まない)。
        canvas_wrap = ctk.CTkFrame(parent, corner_radius=10,
                                   height=self.CANVAS_H + 36)
        if docked:
            canvas_wrap.pack(fill="x", padx=14, pady=4, **before)
            canvas_wrap.grid_propagate(False)
        else:
            # =300: 別ウィンドウでは図が全面(ウィンドウの大きさに追従)
            canvas_wrap.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        canvas_wrap.grid_rowconfigure(0, weight=1)
        canvas_wrap.grid_columnconfigure(0, weight=1)
        self.canvas_wrap = canvas_wrap
        self.canvas = tk.Canvas(canvas_wrap, height=self.CANVAS_H, bg=CANVAS_BG,
                                highlightthickness=0)
        # スクロールバーは下部の編集パネル(CTkScrollableFrame)と見た目を
        # 揃えるため、素の tk.Scrollbar ではなく customtkinter の
        # CTkScrollbar(角丸のモダン表示)を tk.Canvas に接続して使う。
        hbar = ctk.CTkScrollbar(canvas_wrap, orientation="horizontal",
                                command=self.canvas.xview)
        vbar = ctk.CTkScrollbar(canvas_wrap, orientation="vertical",
                                command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=(6, 0))
        vbar.grid(row=0, column=1, sticky="ns", pady=(6, 2), padx=(2, 4))
        hbar.grid(row=1, column=0, sticky="ew", padx=(6, 0), pady=(2, 6))
        self.canvas_hbar = hbar
        self.canvas_vbar = vbar
        # =285: 高さ変更の取っ手(図の枠の直下・上下ドラッグ)。=300: 別
        # ウィンドウでは無し(map_sash は None)
        self.map_sash = None
        if docked:
            self.map_sash = ctk.CTkFrame(parent, height=7, corner_radius=3,
                                         fg_color=("gray80", "gray28"),
                                         cursor="sb_v_double_arrow")
            self.map_sash.pack(fill="x", padx=200, pady=(0, 2),
                               after=canvas_wrap)
            for w in (self.map_sash,):
                w.bind("<ButtonPress-1>", self._on_map_sash_press)
                w.bind("<B1-Motion>", self._on_map_sash_drag)
                w.bind("<ButtonRelease-1>", self._on_map_sash_release)
        self._sash_y0 = None
        # マウスホイール: 通常=縦、Shift+ホイール=横
        self.canvas.bind(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind(
            "<Shift-MouseWheel>",
            lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind(   # Linux(X11)のホイールは Button-4/5
            "<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind(
            "<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))


    # ---- イベント遷移図の折りたたみ(=58) ----

    MAP_CFG_KEY = "editor_map_open"
    # =72: 「図の高さに合わせる」(自動フィット)の保存キー
    MAP_FIT_CFG_KEY = "editor_map_fit"

    def _init_map_toggle(self):
        """コンフィグから開閉状態を復元してトグル表示を整える。"""
        cfg = load_config()
        v = cfg.get(self.MAP_CFG_KEY)
        # 既定は「開く」。bool以外の値は無視する(壊れたコンフィグ対策)
        self._map_open = True if not isinstance(v, bool) else v
        # =72: 自動フィット。既定はOFF(従来どおり110px固定)
        f = cfg.get(self.MAP_FIT_CFG_KEY)
        self._map_fit = False if not isinstance(f, bool) else f
        self._apply_map_fit_style()
        self._apply_map_open()

    def _toggle_map(self):
        self._map_open = not self._map_open
        self._apply_map_open()
        cfg = load_config()
        cfg[self.MAP_CFG_KEY] = bool(self._map_open)
        save_config(cfg)
        if self._map_open:
            # 隠している間の変更が反映されていないので描き直す
            self._redraw_canvas()

    def _apply_map_open(self):
        """図の表示/非表示とトグルのラベルを現在の状態に合わせる。"""
        if self._map_undocked:
            return                # =300: 別ウィンドウ中は常に表示(折りたたみ無し)
        if self._map_open:
            # =255: 表記を「▼ 折りたたみ」/「▶ 折りたたみ中」へ変更(Q3)
            self.map_toggle_btn.configure(text=tr("▼ 折りたたみ"))
            if not self.canvas_wrap.winfo_manager():
                self.canvas_wrap.pack(fill="x", padx=14, pady=4,
                                      after=self.map_bar)
                self.map_sash.pack(fill="x", padx=200, pady=(0, 2),
                                   after=self.canvas_wrap)   # =285
            # =72: 自動フィットの切替は図が開いているときだけ意味を持つ
            if not self.map_fit_btn.winfo_manager():
                self.map_fit_btn.pack(side="left", padx=(8, 0))
            if not self.map_mode_btn.winfo_manager():     # =299
                self.map_mode_btn.pack(side="left", padx=(8, 0))
            if not self.map_undock_btn.winfo_manager():   # =300
                self.map_undock_btn.pack(side="left", padx=(8, 0))
        else:
            self.map_toggle_btn.configure(text=tr("▶ 折りたたみ中"))
            self.canvas_wrap.pack_forget()
            self.map_sash.pack_forget()   # =285
            self.map_fit_btn.pack_forget()
            self.map_mode_btn.pack_forget()
            self.map_undock_btn.pack_forget()

    # ---- =300: イベント遷移図のドッキング解除(別ウィンドウ) ----
    #
    # 2段目バー(イベント追加〜インポート・元に戻す/やり直す・配置)と図だけを
    # 別ウィンドウへ出す。タイトル行・イベントのパラメータ・ステート図・
    # チャンネルは編集画面に残る(ユーザー決定)。解除中の編集画面側は
    # 2段目バーごと消す。折りたたみ/自動フィット/取っ手は別ウィンドウには
    # 無い。topmost にはしない(独立して動かせ、最小化もできる)。解除状態と
    # ウィンドウの位置・大きさはコンフィグに記憶(次回も同じ状態で開く)。
    MAP_UNDOCK_CFG_KEY = "editor_map_undocked"
    MAP_WIN_KEY = "editor_map"           # winstate の保存キー
    MAP_WIN_W, MAP_WIN_H = 800, 500      # 別ウィンドウの既定サイズ

    def _destroy_map_area(self):
        for w in (getattr(self, "map_sash", None),
                  getattr(self, "canvas_wrap", None),
                  getattr(self, "map_bar", None)):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.map_sash = None

    def _undock_map(self, *, save: bool = True):
        if self._map_undocked:
            return
        self._map_undocked = True
        self._destroy_map_area()
        win = ctk.CTkToplevel(self)
        win.title(tr("イベント遷移図") + " - " + (self.title_var.get() or ""))
        self.map_win = win
        self.map_winmem = WindowMemory(win, self.MAP_WIN_KEY)
        if not self.map_winmem.restore():
            try:
                x = self.winfo_rootx() + 40
                y = self.winfo_rooty() + 40
            except Exception:
                x, y = 60, 60
            win.geometry(f"{self.MAP_WIN_W}x{self.MAP_WIN_H}+{x}+{y}")
        win.minsize(420, 240)
        self._build_map_area(win, docked=False)
        self.map_winmem.watch()
        self.map_winmem.install_close_hook(self._dock_map)   # ×=ドッキング
        self._refresh_map_mode_btn()
        self._redraw_canvas()
        if save:
            cfg = load_config()
            cfg[self.MAP_UNDOCK_CFG_KEY] = True
            save_config(cfg)

    def _dock_map(self, *, save: bool = True):
        if not self._map_undocked:
            return
        self._map_undocked = False
        win = getattr(self, "map_win", None)
        mem = getattr(self, "map_winmem", None)
        if mem is not None:
            try:
                mem.save_now()
            except Exception:
                pass
        self._destroy_map_area()
        self.map_win = None
        self.map_winmem = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        self._build_map_area(self, docked=True)
        self._apply_map_fit_style()
        self._apply_map_open()
        self._refresh_map_mode_btn()
        self._redraw_canvas()
        self._apply_map_height()
        if save:
            cfg = load_config()
            cfg[self.MAP_UNDOCK_CFG_KEY] = False
            save_config(cfg)

    def destroy(self):
        # =300: 別ウィンドウの位置・大きさを保存してから閉じる
        mem = getattr(self, "map_winmem", None)
        if mem is not None:
            try:
                mem.save_now()
            except Exception:
                pass
        super().destroy()

    def _init_map_undock(self):
        """コンフィグに「解除したまま」が残っていれば起動時に別ウィンドウで開く。"""
        cfg = load_config()
        if cfg.get(self.MAP_UNDOCK_CFG_KEY) is True:
            self._undock_map(save=False)

    # ---- イベント遷移図の自動フィット(=72) ----
    #
    # 「図の高さに合わせる」ON のとき、図の実高さ(描画後の scrollregion 下端)
    # まで表示領域を縦に広げる。図が小さいときは従来の CANVAS_H(110)を下限に
    # する。上限は設けない(ユーザー決定=下の編集パネルはスクロールで対応)が、
    # ウィンドウの下端を越えて広げてもスクロールバーごと画面外に出て操作
    # できなくなるだけなので、「ウィンドウ内に収まる高さ」を物理的な天井と
    # する(それ以上は従来どおり図の縦スクロールで見る)。

    def _toggle_map_fit(self):
        self._map_fit = not self._map_fit
        self._apply_map_fit_style()
        self._apply_map_height()
        cfg = load_config()
        cfg[self.MAP_FIT_CFG_KEY] = bool(self._map_fit)
        save_config(cfg)

    def _apply_map_fit_style(self):
        """ONのとき文字色をアクセント色にする(=68の詳細設定と同じ作法)。"""
        self.map_fit_btn.configure(
            text_color=ACCENT_TEXT if self._map_fit else TEXT_MUTED)

    def _map_content_height(self) -> int:
        """描画済みの図の実高さ(scrollregion の下端)。未描画なら下限値。"""
        try:
            sr = str(self.canvas.cget("scrollregion")).split()
            return int(float(sr[3]))
        except (IndexError, ValueError, tk.TclError):
            return self.CANVAS_H

    # =285: 図の高さの天井は**ウィンドウ高さの80%**(図の枠の下端がウィンドウ
    # の80%位置を越えない=下の20%はイベント/チャンネル欄に必ず残す。ユーザー
    # 決定)。自動フィット・手動リサイズの両方に効く。
    MAP_MAX_RATIO = 0.8
    MAP_MIN_H = 60
    MAP_H_CFG_KEY = "editor_map_height"   # =285: 手動リサイズ後の高さ

    def _map_height_limit(self) -> int:
        """キャンバス高さの天井=ウィンドウ高さの80%に収まる高さ。

        図の枠(canvas_wrap)の上端からウィンドウの80%位置までを使える高さ。
        36=横スクロールバーぶん、12=下側の余白ぶん。ウィンドウがまだ
        実寸を持たない起動直後は既定サイズ(WIN_H)から概算する。
        """
        try:
            win_h = self.winfo_height()
            top = self.canvas_wrap.winfo_y() \
                if getattr(self, "_map_open", True) else 0
            if win_h > 1 and top > 0:
                return max(self.MAP_MIN_H,
                           int(win_h * self.MAP_MAX_RATIO) - top - 36 - 12)
        except tk.TclError:
            pass
        return max(self.MAP_MIN_H, int(self.WIN_H * self.MAP_MAX_RATIO) - 150)

    def _apply_map_height(self):
        """現在の設定(手動高さ/自動フィット)に合わせて図の高さを反映する。"""
        if getattr(self, "_map_undocked", False):
            return                # =300: 別ウィンドウは fill=both で追従
        h = getattr(self, "_map_manual_h", None) or self.CANVAS_H
        if getattr(self, "_map_fit", False):
            h = max(self.CANVAS_H, self._map_content_height())
        h = max(self.MAP_MIN_H, min(h, self._map_height_limit()))
        if int(self.canvas.cget("height")) != h:
            self.canvas.configure(height=h)
            self.canvas_wrap.configure(height=h + 36)

    # ---- イベント遷移図の手動リサイズ(=285) ----
    # 図の枠の直下に細い取っ手(sash)を置き、上下ドラッグで高さを変える。
    # ドラッグすると「図の高さに合わせる」は自動でOFF(手動の高さが優先)。
    # 高さはコンフィグ(MAP_H_CFG_KEY)に保存され次回も同じ高さで開く。

    def _init_map_manual_height(self):
        cfg = load_config()
        v = cfg.get(self.MAP_H_CFG_KEY)
        self._map_manual_h = int(v) if isinstance(v, (int, float)) and v > 0 \
            else None

    def _on_map_sash_press(self, e):
        self._sash_y0 = e.y_root
        self._sash_h0 = int(self.canvas.cget("height"))

    def _on_map_sash_drag(self, e):
        if getattr(self, "_sash_y0", None) is None:
            return
        h = self._sash_h0 + (e.y_root - self._sash_y0)
        h = max(self.MAP_MIN_H, min(int(h), self._map_height_limit()))
        if self._map_fit:
            self._map_fit = False
            self._apply_map_fit_style()
            cfg = load_config()
            cfg[self.MAP_FIT_CFG_KEY] = False
            save_config(cfg)
        self._map_manual_h = h
        self._apply_map_height()

    def _on_map_sash_release(self, _e):
        if getattr(self, "_sash_y0", None) is None:
            return
        self._sash_y0 = None
        cfg = load_config()
        cfg[self.MAP_H_CFG_KEY] = int(self._map_manual_h or self.CANVAS_H)
        save_config(cfg)

    def _on_win_configure(self, e):
        """ウィンドウのリサイズに自動フィットの天井を追従させる。

        <Configure> は子ウィジェットからも上がってくるので自分(Toplevel)の
        ぶんだけ拾い、高さが変わったときだけ再計算する(再入・空振り防止)。
        """
        if e.widget is not self:
            return
        if e.height != getattr(self, "_last_win_h", None):
            self._last_win_h = e.height
            # =285: 手動高さも80%の天井で切り詰めるので常に再計算する
            if getattr(self, "_map_open", True):
                self._apply_map_height()

    def _apply_window_size(self):
        """左上(0,0)に WIN_W×WIN_H で開く(画面が小さければ画面内に収める)。

        =115: 前回の配置を復元したときは**その矩形**を再適用する
        (CTkToplevelのwithdraw→deiconifyでジオメトリが解除されることへの
        対策=下の遅延呼び出しの用途は同じ)。
        """
        if getattr(self, "_winmem_restored", False):
            if self.winmem.reapply():
                return
        w, h = self.WIN_W, self.WIN_H
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            # 画面より大きい場合だけ縮める(はみ出して操作できなくなるのを防ぐ)
            w = min(w, sw)
            h = min(h, max(600, sh - 40))
        except Exception:
            pass
        self.geometry(f"{w}x{h}+0+0")

    def _title_display(self) -> str:
        """タイトル欄に表示する文字列。保存済み=ファイル名、未保存=プレースホルダ。"""
        return _title_from_path(self.path) if self.path else tr("(未保存)")

    def _refresh_title_display(self):
        """タイトル欄の表示を現在のパスに合わせて更新する(読み取り専用欄)。"""
        if hasattr(self, "title_var"):
            self.title_var.set(self._title_display())

    # ================= UI構築 =================

    def _build_ui(self):
        # =255: 2段構成(実機レビューでユーザー指定・お絵かき添付):
        #   1段目(head_bar) = [シナリオタイトル(大)] …右寄せで
        #     [デバイス連動：ON/OFF][BGM：ON/OFF][変数/監視][紹介文]｜
        #     [名前を付けて保存][保存]
        #     (=256: BGMトグルを変数/監視の左へ追加し、デバイス連動の右の
        #      区切り線(旧head_sep1)は廃止=ユーザー指定)
        #   2段目(bar)      = [＋イベント追加][イベント削除][イベントコピー]｜
        #     [インポート] [▼ 折りたたみ] [図の高さに合わせる]
        # 「タイトル=画面の見出しが最上段」「シナリオ全体にかかる道具は
        # タイトルと同じ列」「イベント操作は対象(イベント遷移図)のすぐ上」。
        head_bar = ctk.CTkFrame(self, fg_color="transparent")
        head_bar.pack(fill="x", padx=14, pady=(12, 4))
        self.head_bar = head_bar

        # =251: タイトルはファイル名に追従するラベル表示(欄は撤去済み)。
        self.title_var = tk.StringVar(value=self._title_display())

        # 右寄せ群は side="right" で右端から順に置く(視覚順は左→右で
        # デバイス連動｜変数/監視 紹介文｜名前を付けて保存 保存)。
        ctk.CTkButton(head_bar, text=tr("保存"), width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._save).pack(side="right", padx=4)
        ctk.CTkButton(head_bar, text=tr("名前を付けて保存"), width=130,
                      height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._save_as).pack(side="right", padx=4)
        self.head_sep2 = _toolbar_sep(head_bar, side="right")
        self.detail_btn = ctk.CTkButton(
            head_bar, text=tr("紹介文"), width=90, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._open_detail_dialog)
        self.detail_btn.pack(side="right", padx=4)
        self.vars_btn = ctk.CTkButton(
            head_bar, text=tr("変数/監視"), width=100, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._open_vars_dialog)
        self.vars_btn.pack(side="right", padx=4)
        self._update_vars_btn()
        # =262: 背景イラスト(「BGM」トグルの右隣=ユーザー指定Q7)。押すと
        # 画像の選択・クリア・暗さ(%)のダイアログを開く。
        self.bg_btn = ctk.CTkButton(
            head_bar, text=tr("背景"), width=84, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._open_background_dialog)
        self.bg_btn.pack(side="right", padx=4)
        # =256: BGMトグル(「変数/監視」の左隣)。デバイス連動の右隣にあった
        # 区切り線(旧head_sep1)は廃止(ユーザー指定=トグル2つ+設定系を
        # 同じグループにする)。
        self.bgm_toggle_btn = ctk.CTkButton(
            head_bar, text=self._bgm_btn_text(), width=100, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._toggle_bgm_enabled)
        self.bgm_toggle_btn.pack(side="right", padx=4)
        self.device_toggle_btn = ctk.CTkButton(
            head_bar, text=self._device_btn_text(), width=140, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._toggle_device_enabled)
        self.device_toggle_btn.pack(side="right", padx=4)
        # タイトルは残り幅いっぱい(長いファイル名は右側ボタンの手前で切れる)
        self.title_label = ctk.CTkLabel(
            head_bar, textvariable=self.title_var,
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT_HEAD, anchor="w")
        self.title_label.pack(side="left", fill="x", expand=True)
        # =118: ヘルプボタンは**メイン画面のヘッダー(「設定」の左)へ移動**した
        # (ユーザー決定。ヘルプは編集画面から独立=視聴だけの人にも届く場所へ)。
        # 編集画面からは開けなくなったが、`_open_help()` は互換のため残す。

        self._init_map_manual_height()
        self._map_undocked = False
        self.map_win = None
        self.map_winmem = None
        self._build_map_area(self, docked=True)
        # 画面内メッセージ/エラー領域(モーダルダイアログ+警告音の代替)。
        # 先に生成しておき、パネルより下(side=bottom)へ差し込む。
        self._build_msg_area()

        # イベント編集パネル(スクロール)。
        # =58: CTkScrollableFrame は pack/winfo_* が内側フレームに委譲される
        # ため、pack(before=...) の基準にできない。素の CTkFrame で包んで、
        # メッセージ領域のパック順の基準(_pack_msg_area)にする。
        self.panel_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self.panel_wrap.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self.panel = ctk.CTkScrollableFrame(self.panel_wrap, corner_radius=10)
        self.panel.pack(fill="both", expand=True)

        # 描画中インジケータ。パネル再構築(音声行の作り直し)は重く数秒かかる
        # ことがあるため、その間パネルを隠して「描画中…」を出す(中途半端な
        # リアルタイム描画も抑止し、完成後に一括表示)。
        self._rendering = False
        self.rendering_label = ctk.CTkLabel(
            self.panel_wrap, text=tr("描画中…"),
            font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_MUTED)

        self._build_panel_widgets()

    # ============ 画面内メッセージ/エラー領域 ============
    #
    # 従来はエラー・警告・保存完了・削除確認を Windows のメッセージボックス
    # (モーダル+警告音)で出していた。これを画面下部の常設領域へ集約する。
    #   - 検証エラー/警告 : 赤/橙で内容を「一覧」表示(OK押下不要・入力ブロックなし)
    #   - 保存完了/情報   : 緑/灰で状態表示
    #   - 削除等の確認    : メッセージ+インラインの[実行]/[キャンセル]ボタン
    # ダイアログを一切開かないため、警告音も鳴らず、正しい入力をするまで
    # 画面から抜けられない、という不満を解消する。

    def _build_msg_area(self):
        self._pending_confirm = None
        self._msg_line_labels: list = []
        self._msg_buttons: list = []
        self._marks: list = []          # 着色中の不正フィールド
        self._pending_marks: list = []  # 検証中に記録する (widget, kind)

        self.msg_area = ctk.CTkFrame(self, corner_radius=10,
                                     fg_color=("gray92", "gray17"))
        # 生成時は非表示。_report / _confirm で pack する。
        head = ctk.CTkFrame(self.msg_area, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(6, 0))
        self.msg_icon = ctk.CTkLabel(
            head, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=MSG_INFO, anchor="w")
        self.msg_icon.pack(side="left")
        self.msg_close_btn = ctk.CTkButton(
            head, text="✕", width=24, height=22, font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=("gray82", "gray28"), command=self._clear_message)
        self.msg_close_btn.pack(side="right")

        self.msg_body = ctk.CTkFrame(self.msg_area, fg_color="transparent")
        self.msg_body.pack(fill="x", padx=12, pady=(0, 2))
        self.msg_btn_row = ctk.CTkFrame(self.msg_area, fg_color="transparent")
        # ボタン行は確認時のみ pack する
        self._install_msg_autodismiss()   # =286

    def _clear_message(self):
        """メッセージ領域を空にして隠す(着色フィールドも元に戻す)。"""
        self._pending_confirm = None
        self._msg_kind = None   # =286
        self._clear_field_marks()
        for w in self._msg_line_labels:
            w.destroy()
        self._msg_line_labels = []
        for w in self._msg_buttons:
            w.destroy()
        self._msg_buttons = []
        try:
            self.msg_btn_row.pack_forget()
            self.msg_area.pack_forget()
        except Exception:
            pass

    def _render_message(self, kind: str, title: str, lines, buttons=None):
        """メッセージ領域を描画する。buttons=[(text, cmd, style), ...]。"""
        color = {"error": MSG_ERROR, "warn": MSG_WARN,
                 "ok": MSG_OK, "info": MSG_INFO,
                 "confirm": ACCENT_TEXT}.get(kind, MSG_INFO)
        # 既存の行/ボタンを消す
        for w in self._msg_line_labels:
            w.destroy()
        self._msg_line_labels = []
        for w in self._msg_buttons:
            w.destroy()
        self._msg_buttons = []

        self.msg_icon.configure(text=title, text_color=color)
        self._msg_kind = kind                  # =286: 自動消去の判定に使う
        self._msg_shown_at = time.monotonic()  # =286: 表示直後の同じ操作を無視
        # =58: 折り返し幅はウィンドウ幅に追従させる(固定780だと1360幅の
        # ウィンドウで無駄に折り返し、行数が増えてボタンが押し出される)
        try:
            wrap_px = max(560, self.winfo_width() - 80)
        except Exception:
            wrap_px = 780
        for ln in lines:
            lbl = ctk.CTkLabel(self.msg_body, text=("• " + ln) if len(lines) > 1
                               else ln,
                               font=ctk.CTkFont(size=12), text_color=color,
                               justify="left", anchor="w", wraplength=wrap_px)
            lbl.pack(fill="x", anchor="w")
            self._msg_line_labels.append(lbl)

        if buttons:
            for text, cmd, style in buttons:
                # 長い表記(3択保存警告など)が切れないよう、文字量で幅を広げる
                bw = max(96, sum(13 if ord(c) > 0x2000 else 8
                                 for c in text) + 24)
                if style == "danger":
                    b = ctk.CTkButton(
                        self.msg_btn_row, text=text, width=bw, height=28,
                        fg_color="#c0392b", hover_color="#a93226", command=cmd)
                elif style == "primary":
                    b = ctk.CTkButton(
                        self.msg_btn_row, text=text, width=bw, height=28,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER, command=cmd)
                else:
                    b = ctk.CTkButton(
                        self.msg_btn_row, text=text, width=bw, height=28,
                        fg_color="transparent", border_width=1, border_color=MUTED,
                        text_color=("gray20", "gray85"),
                        hover_color=("gray85", "gray25"), command=cmd)
                b.pack(side="right", padx=4)
                self._msg_buttons.append(b)
            self.msg_btn_row.pack(fill="x", padx=12, pady=(2, 8))
        else:
            self.msg_btn_row.pack_forget()

        self._pack_msg_area()

    def _pack_msg_area(self):
        """メッセージ領域を最下部へ表示する(=58 不具合修正)。

        **パネルより先に pack しないとボタン行がはみ出す**。パネルは
        `expand=True` で余白を全部取るため、後から pack したメッセージ
        領域には行数分の高さが回ってこず、下端のボタンが画面外へ切れて
        いた(ユーザー報告=「素材をコピーして相対パスで保存」の確認)。
        pack の `before=` で順序を先に差し込むと、メッセージ領域が必要な
        高さを先に確保し、パネル側が縮む。
        """
        try:
            self.msg_area.pack(side="bottom", fill="x", padx=14,
                               pady=(0, 10), before=self.panel_wrap)
        except Exception:
            try:
                self.msg_area.pack(side="bottom", fill="x", padx=14,
                                   pady=(0, 10))
            except Exception:
                pass

    def _report(self, kind: str, title: str, body):
        """検証エラー・警告・情報を画面内領域へ表示する(モーダルなし)。

        kind: "error" / "warn" / "ok" / "info"。
        body: 文字列(改行で複数行の一覧になる)または文字列のリスト。
        """
        self._pending_confirm = None
        if isinstance(body, str):
            lines = [s for s in body.split("\n") if s.strip() != ""]
        else:
            lines = [str(s) for s in body if str(s).strip() != ""]
        if not lines:
            lines = [title]
        self._render_message(kind, title, lines)
        # 検証で記録された不正フィールドを着色(error/warnのみ)
        if kind in ("error", "warn"):
            self._apply_field_marks()
        else:
            self._pending_marks = []
        # テスト観測用(messagebox 互換の記録)
        compat = {"error": "error", "warn": "warning",
                  "ok": "info", "info": "info"}.get(kind, "info")
        MESSAGE_LOG.append((compat, (title, "\n".join(lines))))

    # ---- 不正フィールドの着色(要件: 原因ウィジェットを赤/黄で示す) ----

    def _want_mark(self, widget, kind="error"):
        """検証中に不正フィールドを記録する(着色は _apply_field_marks で)。"""
        if widget is not None:
            self._pending_marks.append((widget, kind))

    def _apply_field_marks(self):
        self._clear_field_marks()
        for widget, kind in self._pending_marks:
            self._mark_field(widget, kind)
        self._pending_marks = []

    def _mark_field(self, widget, kind):
        # VarRefField は実体の入力欄/メニューへ委譲
        if isinstance(widget, VarRefField):
            widget = widget.menu if widget.use_var else widget.entry
        try:
            if widget is None or not widget.winfo_exists():
                return
        except Exception:
            return
        if getattr(widget, "_rvp_marked", False):
            return
        cls = widget.__class__.__name__
        bg = FIELD_ERR_BG if kind == "error" else FIELD_WARN_BG
        edge = FIELD_ERR_EDGE if kind == "error" else FIELD_WARN_EDGE
        saved = {}
        try:
            if cls == "CTkEntry":
                saved["fg_color"] = widget.cget("fg_color")
                widget.configure(fg_color=bg)
                seq = "<KeyRelease>"
            elif cls == "CTkOptionMenu":
                saved["fg_color"] = widget.cget("fg_color")
                widget.configure(fg_color=bg)
                seq = "<Button-1>"
            elif cls == "CTkCheckBox":
                saved["border_color"] = widget.cget("border_color")
                saved["text_color"] = widget.cget("text_color")
                widget.configure(border_color=edge, text_color=edge)
                seq = "<Button-1>"
            elif cls == "CTkButton":
                saved["fg_color"] = widget.cget("fg_color")
                saved["border_color"] = widget.cget("border_color")
                widget.configure(fg_color=bg, border_color=edge)
                seq = "<Button-1>"
            else:
                return
        except Exception:
            return
        widget._rvp_marked = True
        widget._rvp_saved = saved
        # 修正操作で自動的にメッセージ+着色を消す(要件2)。1度だけ束縛する。
        if not getattr(widget, "_rvp_bound", False):
            try:
                widget.bind(seq, lambda _e=None: self._on_field_edited(),
                            add="+")
                widget._rvp_bound = True
            except Exception:
                pass
        self._marks.append(widget)

    def _clear_field_marks(self):
        for widget in self._marks:
            try:
                if widget.winfo_exists():
                    widget.configure(**getattr(widget, "_rvp_saved", {}))
                widget._rvp_marked = False
            except Exception:
                pass
        self._marks = []

    def _on_field_edited(self):
        """着色中フィールドが修正されたらメッセージ+着色を消す。"""
        if self._marks:
            self._clear_message()

    # ---- メッセージの自動消去(=286) ----
    # ユーザー要望: 「保存しました」等は次の操作で自然に消える(×を押すのが
    # 億劫)。エラー/警告は原因のコンボ/入力欄を編集した時点で消える(解消の
    # 有無を問わない)。着色フィールド(_mark_field)の束縛は従来どおり残し、
    # 着色されない検証エラー(チャンネル内容の不備など)にも効くよう、編集画面
    # 内の**入力ウィジェットへの操作**を bind_all で拾って消す。
    #   ok/info : 編集画面内のどこかをクリック/キー入力 → 消す
    #   error/warn : 入力欄へのキー入力、コンボ/チェック/スイッチ/スライダー
    #                のクリック → 消す(空欄のクリック・スクロールでは消さない)
    #   confirm(削除確認など) : 消さない(ボタンで答えてもらう)
    # 表示した操作自体(保存ボタンのクリック等)で即消えないよう、表示から
    # 0.3秒以内の操作は無視する。メッセージ領域内(×・[実行]等)も無視。

    _EDIT_WIDGET_CLASSES = ("CTkOptionMenu", "CTkComboBox", "CTkCheckBox",
                            "CTkSwitch", "CTkRadioButton", "CTkSlider",
                            "CTkSegmentedButton", "VarRefField")

    def _install_msg_autodismiss(self):
        self._msg_kind = None
        self._msg_shown_at = 0.0
        self.bind_all("<Button-1>", self._on_any_click_for_msg, add="+")
        self.bind_all("<KeyRelease>", self._on_any_key_for_msg, add="+")

    def _msg_event_in_editor(self, e) -> bool:
        """イベントがこの編集画面内(メッセージ領域の外)で起きたか。"""
        try:
            if not self.winfo_exists() or not self.msg_area.winfo_manager():
                return False
            w = e.widget
            if not hasattr(w, "winfo_toplevel"):
                return False
            if w.winfo_toplevel() is not self:
                return False
            if str(w).startswith(str(self.msg_area)):
                return False
        except Exception:
            return False
        return time.monotonic() - self._msg_shown_at > 0.3

    def _msg_widget_is_input(self, w) -> bool:
        """クリックされたウィジェット(内部の canvas/label 含む)が入力系か。"""
        cur = w
        for _ in range(3):
            if cur is None:
                return False
            if cur.__class__.__name__ in self._EDIT_WIDGET_CLASSES:
                return True
            cur = getattr(cur, "master", None)
        return False

    def _on_any_click_for_msg(self, e):
        kind = getattr(self, "_msg_kind", None)
        if kind is None or self._pending_confirm is not None \
                or self._msg_buttons:      # ボタン付き(確認/選択)は消さない
            return
        if not self._msg_event_in_editor(e):
            return
        if kind in ("ok", "info") or self._msg_widget_is_input(e.widget):
            self._clear_message()

    def _on_any_key_for_msg(self, e):
        kind = getattr(self, "_msg_kind", None)
        if kind is None or self._pending_confirm is not None \
                or self._msg_buttons:
            return
        if not self._msg_event_in_editor(e):
            return
        try:
            is_text = e.widget.winfo_class() in ("Entry", "Text", "TEntry")
        except Exception:
            is_text = False
        if kind in ("ok", "info") or is_text:
            self._clear_message()

    def _confirm(self, message: str, on_yes, yes_text=None,
                 title=None, warn=False):
        """はい/いいえの確認を画面内領域で行う(モーダルaskyesnoの代替)。

        [実行]でon_yesを呼ぶ。[キャンセル]で領域を閉じる。
        """
        self._pending_confirm = on_yes
        MESSAGE_LOG.append(("confirm", (title or tr("確認"), message)))
        if AUTO_CONFIRM:
            on_yes()
            return
        self._render_message(
            "confirm", title or tr("確認"), [message],
            buttons=[(yes_text or tr("実行"), on_yes, "danger" if warn else "primary"),
                     (tr("キャンセル"), self._clear_message, "ghost")])

    @staticmethod
    def _align_label_column(labels):
        """先頭ラベルの幅を揃え、右側の入力欄の左端を縦一直線にする(=66)。

        ユーザー要望「コンボボックスやテキストボックスの縦の位置を揃えたい」。
        固定値を決め打ちにすると英語UI(=36)で足りなくなるので、**実際の
        要求幅の最大値**を採用する。`anchor="w"` を必ず添えること
        (CTkLabel は既定で中央寄せなので、幅だけ広げると文字が右へずれる)。
        """
        try:
            widths = [w.winfo_reqwidth() for w in labels if w is not None]
            if not widths:
                return
            col = max(widths)
            for w in labels:
                if w is not None:
                    w.configure(width=col, anchor="w")
        except Exception:
            pass

    def _build_panel_widgets(self):
        p = self.panel

        # ===== 上段: [イベント] [ステート] を丸角の灰色枠で2列に並べる =====
        top = ctk.CTkFrame(p, fg_color="transparent")
        top.pack(fill="x", pady=(2, 4))
        top.grid_columnconfigure(0, weight=1, uniform="pane")
        top.grid_columnconfigure(1, weight=1, uniform="pane")

        self.event_box = ctk.CTkFrame(top, corner_radius=10, fg_color=BOX_BG,
                                      border_width=1, border_color=BOX_BORDER)
        self.event_box.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.state_box = ctk.CTkFrame(top, corner_radius=10, fg_color=BOX_BG,
                                      border_width=1, border_color=BOX_BORDER)
        self.state_box.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # --- イベントボックスの中身 ---
        ev = ctk.CTkFrame(self.event_box, fg_color="transparent")
        ev.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        ctk.CTkLabel(ev, text=tr("■ イベント"),
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_HEAD, anchor="w").pack(fill="x")

        # =66: 先頭ラベルは幅を揃えて入力欄の左端を縦一直線にする
        # (最後に _align_label_column でまとめて幅を決める)
        ev_col_labels = []

        head = ctk.CTkFrame(ev, fg_color="transparent")
        head.pack(fill="x", pady=(4, 6))
        lbl = ctk.CTkLabel(head, text=tr("イベント名:"),
                           font=ctk.CTkFont(size=15, weight="bold"),
                           text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        # 見出しをインライン編集(Enter/フォーカス外れで確定→参照も自動追従)
        self.event_id_var = tk.StringVar(value="")
        self.event_id_entry = ctk.CTkEntry(
            head, textvariable=self.event_id_var, width=200, height=30,
            font=ctk.CTkFont(size=15, weight="bold"))
        self.event_id_entry.pack(side="left", padx=(6, 0))
        self.event_id_entry.bind("<Return>", lambda _e: self._commit_event_rename())
        self.event_id_entry.bind("<FocusOut>", lambda _e: self._commit_event_rename())
        # 参照キーとしての互換用(表示更新は _load_panel で行う)
        self.event_title_label = self.event_id_entry

        # 「開始イベントにする」はイベント名入力欄のすぐ右に置く
        self.start_var = tk.BooleanVar(value=False)
        self.start_check = ctk.CTkCheckBox(
            head, text=tr("開始イベントにする"), variable=self.start_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.start_check.pack(side="left", padx=(10, 0))

        # イベント終了条件(イベント名の下・次のイベントの上)
        evend_row = ctk.CTkFrame(ev, fg_color="transparent")
        evend_row.pack(fill="x", pady=(4, 0))
        self.evend_row = evend_row
        lbl = ctk.CTkLabel(evend_row, text=tr("イベント終了条件:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.evend_var = tk.StringVar(value=self.EVEND_ALL)
        self.evend_menu = CTkOptionMenu(
            evend_row, variable=self.evend_var, width=210, height=26,
            # 選択肢は _load_panel で相関更新(通常=全/指定、ステート形式=委譲のみ)
            values=[self.EVEND_ALL, self.EVEND_CHANNEL],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_evend_ui(),
        )
        self.evend_menu.pack(side="left", padx=(6, 0))
        # 音声なしイベント(有効チャンネル0)のときだけ pack するヒント
        self.evend_noaudio_hint = ctk.CTkLabel(
            evend_row, text=tr("(音声なし=即座に次へ)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.evend_ch_label = ctk.CTkLabel(
            evend_row, text=tr("対象ch:"), font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED)
        self.evend_ch_var = tk.StringVar(value="C")
        self.evend_ch_menu = CTkOptionMenu(
            evend_row, variable=self.evend_ch_var, width=70, height=26,
            values=list(CHANNEL_IDS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._refresh_channel_infinite(),
        )
        # evend_ch_label / evend_ch_menu は「指定チャンネル」時のみ pack

        # 「合計時間が経過した時」の秒数欄。EVEND_DURATION 選択時のみ pack。
        # min秒〜max秒(0以上)。入場ごとに [min,max] の一様乱数を抽選し、その秒数を
        # 超えたら終了(ステート移行の channel_time と同じ範囲抽選)。max空欄=min。
        # 各欄は定数 or 数値変数参照(VarRefField)。
        self.evend_secs_min = VarRefField(
            evend_row, width=54, placeholder=tr("min"))
        # =67: 単位は min / max の**両方**に添える(ユーザー要望)
        self.evend_secs_min_label = ctk.CTkLabel(
            evend_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.evend_secs_tilde = ctk.CTkLabel(
            evend_row, text="〜", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.evend_secs_max = VarRefField(
            evend_row, width=54, placeholder=tr("max"))
        self.evend_secs_label = ctk.CTkLabel(
            evend_row, text=tr("秒"), font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        # =67: min/max の両方に単位を付けたぶん行が伸びるので、ヒントは
        # 短くする(長いままだとイベント枠からはみ出して切れる)
        self.evend_secs_hint = ctk.CTkLabel(
            evend_row, text=tr("(累積・範囲は抽選)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # 「変数条件で終了」時の判定式(AND)。EVEND_COND 選択時のみ pack
        self.evend_cond_frame = ctk.CTkFrame(ev, fg_color="transparent")
        ctk.CTkLabel(self.evend_cond_frame, text=tr("終了する判定式(すべて成立で終了):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.evend_cond = CondListEditor(self.evend_cond_frame)
        self.evend_cond.pack(fill="x", padx=(12, 0))

        # 次のイベント(チェック+重みで分岐)
        row1 = ctk.CTkFrame(ev, fg_color="transparent")
        row1.pack(fill="x", pady=(4, 0))
        lbl = ctk.CTkLabel(row1, text=tr("次のイベント:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.next_mode_var = tk.StringVar(value=self.NEXT_FIXED)
        self.next_mode_menu = CTkOptionMenu(
            row1, variable=self.next_mode_var, width=130, height=26,
            values=[self.NEXT_FIXED, self.NEXT_BRANCH, self.NEXT_CHOICE,
                    self.NEXT_NONE],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_next_mode(),
        )
        self.next_mode_menu.pack(side="left", padx=(6, 0))
        # choiceオブジェクトの編集対象外キーの保持
        self._choice_extra = {}
        # 幅の狭いイベント枠に収めるため、ヒントは次行で折り返し表示する
        self.next_hint_label = ctk.CTkLabel(
            ev, text=tr("(チェックなし=再生終了、複数チェック=重み付き抽選)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            justify="left", anchor="w", wraplength=440)
        self.next_hint_label.pack(fill="x", padx=(2, 0))

        # 変数操作(イベント開始時)。変数宣言があるときだけ表示する
        self.ev_ops_row = ctk.CTkFrame(ev, fg_color="transparent")
        lbl = ctk.CTkLabel(self.ev_ops_row, text=tr("変数操作:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self._ev_ops: list = []
        self._ev_end_ops: list = []
        self.ev_ops_btn = ctk.CTkButton(
            self.ev_ops_row, text="", width=140, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_event_ops)
        self.ev_ops_btn.pack(side="left", padx=6)
        ctk.CTkLabel(self.ev_ops_row,
                     text=tr("(◀◀での再実行でも発火します)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left")

        # 分岐/選択肢/変数分岐/数値入力の切替コンテナ(=133で「分岐」へ改名)
        # =220: height=0 が要る。遷移方法が「なし」のときは中身(各inner)を
        # すべて pack_forget するので**空のCTkFrameになり、CTk既定の高さ
        # 200px(実測268px)ぶんの空白がイベント枠に居座っていた**
        # (ユーザー報告=新規作成・next:null の1イベントシナリオで、
        # チャンネルを見るのにスクロールが要る)。空のときは
        # `_collapse_if_empty` が height=1 まで潰す。
        next_area = ctk.CTkFrame(ev, fg_color="transparent", height=0)
        next_area.pack(fill="x")
        self.next_area = next_area
        self.random_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        self.random_inner.pack(fill="x")
        self.choice_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        self.cond_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        self.input_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        # =166「固定」: 遷移先を1つだけ選ぶ。JSONは "next": "eventB"(文字列)。
        # 「分岐」で1件だけチェックした場合と保存形式は同じなので、読み込みは
        # 常に「固定」として開く(ユーザー決定 2026-08-16)。
        self.fixed_inner = ctk.CTkFrame(next_area, fg_color="transparent")
        fx_row = ctk.CTkFrame(self.fixed_inner, fg_color="transparent")
        fx_row.pack(fill="x", pady=(2, 4))
        lbl = ctk.CTkLabel(fx_row, text=tr("遷移先:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.next_fixed_var = tk.StringVar(value="")
        self.next_fixed_menu = CTkOptionMenu(
            fx_row, variable=self.next_fixed_var, width=200, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.next_fixed_menu.pack(side="left", padx=(6, 0))
        # 各inner は _update_next_mode で pack/pack_forget

        # =123 すごろく(advance): チェックONで「進む歩数」欄を表示。
        # next解決をN回繰り返し、途中のイベントは再生せず通過する。
        # 選択肢/数値入力の遷移では使えない(相関制御で行ごと非表示)。
        self.sugoroku_row = ctk.CTkFrame(ev, fg_color="transparent")
        self.sugoroku_row.pack(fill="x", pady=(2, 0))
        self.sugoroku_var = tk.BooleanVar(value=False)
        self.sugoroku_check = ctk.CTkCheckBox(
            self.sugoroku_row, text=tr("すごろく"), variable=self.sugoroku_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            command=self._update_sugoroku_ui)
        self.sugoroku_check.pack(side="left")
        self.sugoroku_steps_label = ctk.CTkLabel(
            self.sugoroku_row, text=tr("進む歩数:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.advance_field = VarRefField(self.sugoroku_row, width=54,
                                         placeholder="2")
        self.sugoroku_hint = ctk.CTkLabel(
            self.sugoroku_row,
            text=tr("(N歩先まで進み、通過するイベントは再生されません)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self._update_sugoroku_ui()

        # 遷移候補(イベントごとのチェック+重み)。_rebuild_next_ui で動的構築
        self.next_targets_frame = ctk.CTkFrame(self.random_inner,
                                               fg_color="transparent")
        self.next_targets_frame.pack(fill="x", pady=(2, 0))
        self.next_target_vars: dict[str, tk.BooleanVar] = {}
        # eid -> VarRefField(重み。定数 or 変数参照。0/負=出さない)
        self.next_weight_vars: dict = {}

        # 分岐オプション(未実行優先・全消化時の挙動)
        opt_row = ctk.CTkFrame(self.random_inner, fg_color="transparent")
        opt_row.pack(fill="x", pady=(2, 4))
        lbl = ctk.CTkLabel(opt_row, text=tr("分岐先の候補:"),
                           font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        lbl.pack(side="left")
        ev_col_labels.append(lbl)
        self.next_visited_var = tk.StringVar(value=tr("毎回すべて候補"))
        CTkOptionMenu(
            opt_row, variable=self.next_visited_var, width=190, height=26,
            values=[tr("毎回すべて候補"), tr("未実行イベントのみ候補")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_next_ui(),
        ).pack(side="left", padx=6)
        # 「全候補が実行済みのとき:」は**2行目**に置く(=66・ユーザー要望)。
        # 1行に並べると幅690の枠では行き先メニューがはみ出して見えなかった。
        self.next_exh_row = ctk.CTkFrame(self.random_inner,
                                         fg_color="transparent")
        self.next_exh_label = ctk.CTkLabel(
            self.next_exh_row, text=tr("全候補が実行済みのとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.next_exhausted_var = tk.StringVar(
            value=tr("以後、毎回すべて候補"))
        # =133: 幅280=最長の「リセットして再び、未実行イベントのみ候補」対応
        self.next_exh_menu = CTkOptionMenu(
            self.next_exh_row, variable=self.next_exhausted_var,
            width=280, height=26,
            values=[tr("以後、毎回すべて候補"),
                    tr("リセットして再び、未実行イベントのみ候補"),
                    tr("指定イベントへ")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_next_ui(),
        )
        self.next_exh_to_var = tk.StringVar(value="")
        self.next_exh_to_menu = CTkOptionMenu(
            self.next_exh_row, variable=self.next_exh_to_var,
            width=140, height=26, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))

        # 全候補の重みが0以下(=出せる候補なし)のときの行き先(else)。
        # 「(終了)」=遷移なし=シナリオ終了。重みを変数にした時のフォールバック用。
        opt_row2 = ctk.CTkFrame(self.random_inner, fg_color="transparent")
        opt_row2.pack(fill="x", pady=(0, 4))
        self.next_else_row = opt_row2   # next_exh_row の pack 基準(=66)
        self.next_else_label = ctk.CTkLabel(
            opt_row2, text=tr("重みが全て0のとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.next_else_label.pack(side="left")
        self.NEXT_ELSE_END = tr("(終了)")
        self.next_else_var = tk.StringVar(value=self.NEXT_ELSE_END)
        self.next_else_menu = CTkOptionMenu(
            opt_row2, variable=self.next_else_var, width=160, height=26,
            values=[self.NEXT_ELSE_END],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.next_else_menu.pack(side="left", padx=6)
        ev_col_labels += [self.next_exh_label, self.next_else_label]
        # 入力欄の左端を縦一直線に揃える(=66・ユーザー要望)
        self._align_label_column(ev_col_labels)

        # ===== 選択肢エディタ =====
        self.choice_rows_frame = ctk.CTkFrame(self.choice_inner,
                                              fg_color="transparent")
        self.choice_rows_frame.pack(fill="x", pady=(2, 0))
        self.choice_rows: list[dict] = []   # {frame,label_var,to_var}

        add_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        add_row.pack(fill="x", pady=(2, 0))
        self.choice_add_btn = ctk.CTkButton(
            add_row, text=tr("＋ 選択肢を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._add_choice_row)
        self.choice_add_btn.pack(side="left")
        ctk.CTkLabel(add_row, text=tr("(最小1・最大9)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        tlim_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        tlim_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(tlim_row, text=tr("タイムリミット:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.choice_tlim_var = tk.StringVar(value=tr("無制限"))
        CTkOptionMenu(
            tlim_row, variable=self.choice_tlim_var, width=110, height=26,
            values=[tr("無制限"), tr("時間指定")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_choice_ui(),
        ).pack(side="left", padx=6)
        self.choice_tmin_var = tk.StringVar(value="0")
        self.choice_tmin_entry = ctk.CTkEntry(
            tlim_row, textvariable=self.choice_tmin_var, width=42, height=26,
            justify="right")
        self.choice_tmin_label = ctk.CTkLabel(
            tlim_row, text=tr("分"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.choice_tsec_var = tk.StringVar(value="30")
        self.choice_tsec_entry = ctk.CTkEntry(
            tlim_row, textvariable=self.choice_tsec_var, width=42, height=26,
            justify="right")
        self.choice_tsec_label = ctk.CTkLabel(
            tlim_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # デフォルト遷移先(タイムアウト時・▶▶スキップ時の共通の行き先)
        dflt_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        dflt_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(dflt_row, text=tr("デフォルト遷移先:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.choice_dflt_var = tk.StringVar(value=tr("先頭の選択肢へ"))
        CTkOptionMenu(
            dflt_row, variable=self.choice_dflt_var, width=210, height=26,
            values=[tr("先頭の選択肢へ"), tr("選択肢から等確率で抽選"),
                    tr("指定イベントへ")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_choice_ui(),
        ).pack(side="left", padx=6)
        self.choice_dflt_to_var = tk.StringVar(value="")
        self.choice_dflt_to_menu = CTkOptionMenu(
            dflt_row, variable=self.choice_dflt_to_var, width=140, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        ctk.CTkLabel(dflt_row, text=tr("(タイムアウト時・▶▶スキップ時の行き先)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="right")

        # =274: 選択必須(▶▶で飛ばさない)。ONのとき "skip": "stay" で保存。
        # タイムリミット併用時のタイムアウト遷移は従来どおり進む(仕様)。
        stay_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        stay_row.pack(fill="x", pady=(4, 0))
        self.choice_stay_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            stay_row, text=tr("▶▶で飛ばさない(選択されるまで待機)"),
            variable=self.choice_stay_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
        ).pack(side="left")
        ctk.CTkLabel(stay_row,
                     text=tr("(音声中の▶▶は音声だけ打ち切る。タイムアウトは進む)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)

        show_row = ctk.CTkFrame(self.choice_inner, fg_color="transparent")
        show_row.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(show_row, text=tr("表示タイミング:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.choice_show_var = tk.StringVar(value=tr("イベント終了条件の達成時"))
        # =168: 終了条件が「無限」のときは候補を絞るのでメニューを保持する
        self.choice_show_menu = CTkOptionMenu(
            show_row, variable=self.choice_show_var, width=210, height=26,
            values=list(self.SHOW_TIMES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_choice_ui(),
        )
        self.choice_show_menu.pack(side="left", padx=6)
        self.choice_smin_var = tk.StringVar(value="0")
        self.choice_smin_entry = ctk.CTkEntry(
            show_row, textvariable=self.choice_smin_var, width=42, height=26,
            justify="right")
        self.choice_smin_label = ctk.CTkLabel(
            show_row, text=tr("分"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.choice_ssec_var = tk.StringVar(value="30")
        self.choice_ssec_entry = ctk.CTkEntry(
            show_row, textvariable=self.choice_ssec_var, width=42, height=26,
            justify="right")
        self.choice_ssec_label = ctk.CTkLabel(
            show_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # 選択肢の変数連携(各行のopsボタンは行内、on_timeoutはここ)
        self._choice_timeout_ops: list = []
        self.choice_toops_btn = ctk.CTkButton(
            tlim_row, text="", width=150, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_choice_timeout_ops)

        # ===== 変数分岐(cond)エディタ =====
        ctk.CTkLabel(self.cond_inner,
                     text=tr("上から順に評価し、最初に成立した行へ遷移します。行内の条件はAND。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, anchor="w"
                     ).pack(fill="x", pady=(2, 0))
        self.cond_rows_frame = ctk.CTkFrame(self.cond_inner,
                                            fg_color="transparent")
        self.cond_rows_frame.pack(fill="x", pady=(2, 0))
        self.cond_rows: list[dict] = []
        cond_add_row = ctk.CTkFrame(self.cond_inner, fg_color="transparent")
        cond_add_row.pack(fill="x", pady=(2, 0))
        ctk.CTkButton(
            cond_add_row, text=tr("＋ 条件行を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_cond_row()).pack(side="left")
        else_row = ctk.CTkFrame(self.cond_inner, fg_color="transparent")
        else_row.pack(fill="x", pady=(4, 4))
        # =168: 終了条件が「無限」のときは else が成立しえないので行ごと隠す
        self.cond_else_row = else_row
        ctk.CTkLabel(else_row, text=tr("どの行も成立しないとき(else):"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.cond_else_var = tk.StringVar(value=tr("再生終了"))
        self.cond_else_menu = CTkOptionMenu(
            else_row, variable=self.cond_else_var, width=150, height=26,
            values=[tr("再生終了")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.cond_else_menu.pack(side="left", padx=6)

        # ===== 数値入力(input)エディタ =====
        in_row1 = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_row1.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(in_row1, text=tr("入力先の変数:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.input_var_var = tk.StringVar(value="")
        self.input_var_menu = CTkOptionMenu(
            in_row1, variable=self.input_var_var, width=120, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.input_var_menu.pack(side="left", padx=6)
        ctk.CTkLabel(in_row1, text=tr("見出し:"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", padx=(10, 0))
        self.input_label_var = tk.StringVar(value="")
        ctk.CTkEntry(in_row1, textvariable=self.input_label_var, width=220,
                     height=26, placeholder_text=tr("省略時「数値を入力してください」")
                     ).pack(side="left", padx=6)
        ctk.CTkLabel(in_row1, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        self.input_to_var = tk.StringVar(value="")
        self.input_to_menu = CTkOptionMenu(
            in_row1, variable=self.input_to_var, width=140, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.input_to_menu.pack(side="left", padx=6)
        in_row2 = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_row2.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(in_row2, text=tr("入力範囲:"), font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        self.input_min_var = tk.StringVar(value="")
        self.input_min_entry = ctk.CTkEntry(
            in_row2, textvariable=self.input_min_var, width=60,
            height=26, justify="right", placeholder_text="min")
        self.input_min_entry.pack(side="left", padx=(6, 2))
        ctk.CTkLabel(in_row2, text="〜", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        self.input_max_var = tk.StringVar(value="")
        self.input_max_entry = ctk.CTkEntry(
            in_row2, textvariable=self.input_max_var, width=60,
            height=26, justify="right", placeholder_text="max")
        self.input_max_entry.pack(side="left", padx=2)
        ctk.CTkLabel(in_row2, text=tr("(空欄=変数宣言の最小/最大。決定した値は変数へセットされ即遷移)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)
        # =288: 入力必須(▶▶で飛ばさない)。ONのとき "skip": "stay" で保存。
        # 新規の数値入力では ON が既定(入力が前提のことが多い=ユーザー決定)。
        # JSONに skip が無い既存のものは OFF(=従来どおり飛ばせる)で開く。
        in_stay = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_stay.pack(fill="x", pady=(4, 0))
        self.input_stay_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            in_stay, text=tr("▶▶で飛ばさない(入力されるまで待機)"),
            variable=self.input_stay_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
        ).pack(side="left")
        ctk.CTkLabel(in_stay,
                     text=tr("(音声中の▶▶は音声だけ打ち切る)"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
                     ).pack(side="left", padx=8)
        in_row3 = ctk.CTkFrame(self.input_inner, fg_color="transparent")
        in_row3.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(in_row3, text=tr("表示タイミング:"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.input_show_var = tk.StringVar(value=tr("イベント終了条件の達成時"))
        # =168: 終了条件が「無限」のときは候補を絞るのでメニューを保持する
        self.input_show_menu = CTkOptionMenu(
            in_row3, variable=self.input_show_var, width=210, height=26,
            values=list(self.SHOW_TIMES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_input_ui(),
        )
        self.input_show_menu.pack(side="left", padx=6)
        self.input_smin_var = tk.StringVar(value="0")
        self.input_smin_entry = ctk.CTkEntry(
            in_row3, textvariable=self.input_smin_var, width=42, height=26,
            justify="right")
        self.input_smin_label = ctk.CTkLabel(
            in_row3, text=tr("分"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.input_ssec_var = tk.StringVar(value="30")
        self.input_ssec_entry = ctk.CTkEntry(
            in_row3, textvariable=self.input_ssec_var, width=42, height=26,
            justify="right")
        self.input_ssec_label = ctk.CTkLabel(
            in_row3, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self._input_extra = {}       # inputオブジェクトの編集対象外キー保持
        self._input_next_extra = {}  # next直下の編集対象外キー保持

        # ===== ステートボックスの中身 =====
        st = ctk.CTkFrame(self.state_box, fg_color="transparent")
        st.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        st_title_row = ctk.CTkFrame(st, fg_color="transparent")
        st_title_row.pack(fill="x")
        ctk.CTkLabel(st_title_row, text=tr("■ ステート"),
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_HEAD, anchor="w").pack(side="left")
        # 通常イベント⇔ステート形式の変換ボタン(ステートボックスの見出し右)
        self.states_toggle_btn = ctk.CTkButton(
            st_title_row, text=tr("ステート形式に変換"), width=150, height=28,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._toggle_states_mode)
        self.states_toggle_btn.pack(side="right")
        # 通常イベント時の案内(ステート未使用のとき states_inner の代わりに表示)
        self.states_hint = ctk.CTkLabel(
            st, text=tr("このイベントはステートを使っていません。\n"
                        "「ステート形式に変換」で複数ステートの\n"
                        "編集ができます。"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, justify="left",
            anchor="w")

        # ステート領域(ステート形式のイベント選択時のみ内容を表示)
        self.states_area = st
        self.states_inner = ctk.CTkFrame(self.states_area, fg_color="transparent")
        # states_inner / states_hint は _show_states_ui で pack/pack_forget する

        # イベント終了条件(ステート形式で必須)
        ev_end_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        ev_end_row.pack(fill="x", pady=(2, 4))
        st_col_labels = []
        lbl = ctk.CTkLabel(ev_end_row, text=tr("イベント終了条件:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        st_col_labels.append(lbl)
        self.ev_end_var = tk.StringVar(value=self.EV_END_CHOICES[0])
        self.ev_end_menu = CTkOptionMenu(
            ev_end_row, variable=self.ev_end_var, width=160, height=26,
            values=list(self.EV_END_CHOICES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_ev_end_menu_change(),
        )
        self.ev_end_menu.pack(side="left", padx=6)
        self.ev_end_field = VarRefField(ev_end_row, width=48,
                                        placeholder=tr("値"),
                                        on_change=lambda: self._update_ev_end_ui())
        self.ev_end_field.pack(side="left", padx=2)
        self.ev_end_unit_label = ctk.CTkLabel(
            ev_end_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.ev_end_unit_label.pack(side="left")
        # =99: 「合計N秒で次へ」の範囲抽選(min〜max)。duration 選択時のみ
        # [min]秒 〜 [max]秒 の2欄になる(通常イベントの evend と同じ書式)
        self.ev_end_tilde_label = ctk.CTkLabel(
            ev_end_row, text="〜", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.ev_end_field2 = VarRefField(ev_end_row, width=48,
                                         placeholder=tr("max"),
                                         on_change=lambda: self._update_ev_end_ui())
        self.ev_end_unit2_label = ctk.CTkLabel(
            ev_end_row, text=tr("秒"), font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.ev_end_hint_label = ctk.CTkLabel(
            ev_end_row, text=tr("(累積)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.ev_end_hint_label.pack(side="left", padx=6)
        self.ev_end_row = ev_end_row

        # 「変数条件で次へ」時の判定式(AND)。EV_END_COND 選択時のみ pack
        self.ev_end_cond_frame = ctk.CTkFrame(
            self.states_inner, fg_color="transparent")
        ctk.CTkLabel(self.ev_end_cond_frame,
                     text=tr("終了する判定式(すべて成立で終了):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.ev_end_cond = CondListEditor(self.ev_end_cond_frame)
        self.ev_end_cond.pack(fill="x", padx=(12, 0))

        # 「指定ステートが終了したら次へ」時のステート選択(チェックで複数指定)。
        # EV_END_STATES 選択時のみ pack。チェックしたステートのどれかが終了したら終了。
        self.ev_end_states_frame = ctk.CTkFrame(
            self.states_inner, fg_color="transparent")
        ctk.CTkLabel(self.ev_end_states_frame,
                     text=tr("終了とみなすステート(複数可):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(
                         side="left", padx=(12, 0))
        self.ev_end_states_inner = ctk.CTkFrame(
            self.ev_end_states_frame, fg_color="transparent")
        self.ev_end_states_inner.pack(side="left", padx=6)
        self.ev_end_state_vars: dict = {}
        self.ev_end_state_checks: list = []

        # ステートマシン図
        st_canvas_wrap = ctk.CTkFrame(self.states_inner, corner_radius=10,
                                      fg_color=("gray82", "gray24"))
        st_canvas_wrap.pack(fill="x", pady=(2, 2))   # =100②: 4→2
        st_head = ctk.CTkFrame(st_canvas_wrap, fg_color="transparent")
        st_head.pack(fill="x", padx=8, pady=(4, 0))   # =100②: 6→4
        ctk.CTkLabel(st_head, text=tr("ステート図(○で選択)"),
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_MUTED
                     ).pack(side="left")
        ctk.CTkButton(st_head, text=tr("＋追加"), width=64, height=26,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._add_state).pack(side="right", padx=2)
        ctk.CTkButton(st_head, text=tr("削除"), width=56, height=26,
                      fg_color="transparent", border_width=1,
                      border_color="#e05a5a", text_color="#e05a5a",
                      hover_color=("gray85", "gray25"),
                      command=self._delete_state).pack(side="right", padx=2)
        ctk.CTkButton(st_head, text=tr("コピー"), width=56, height=26,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._copy_state).pack(side="right", padx=2)

        # =100②: 高さ130→118(図の実内容=○+開始ラベル+下向きの弧は
        # 約115pxに収まる実測。詰めた分でパネル下端の行の欠けを防ぐ)
        self.state_canvas = tk.Canvas(st_canvas_wrap, height=118, bg=CANVAS_BG,
                                      highlightthickness=0)
        self.state_canvas.pack(fill="x", padx=8, pady=(4, 2))   # =100②: 6→4
        # =108: ステートが増えると図が右へ伸びて画面外の○が選べなくなる
        # (ユーザー報告)。イベント遷移図と同じ CTkScrollbar を横に付ける。
        # ステート図は1行に並ぶだけなので横だけでよい。**はみ出している
        # ときだけ表示**して、少ないときは従来どおりの高さを保つ。
        self.state_hbar = ctk.CTkScrollbar(
            st_canvas_wrap, orientation="horizontal", height=12,
            command=self.state_canvas.xview)
        self.state_canvas.configure(xscrollcommand=self._on_state_xscroll)
        # =276: ホイールはステート図を横に動かさず、編集パネル全体の縦スクロール
        # に任せる(ユーザー依頼。=108で付けた「ホイール=横」は、○が2〜3個で
        # はみ出していなくても図が右へずれて見えたため廃止)。パネル
        # (CTkScrollableFrame)は bind_all で子孫上のホイールを拾うので、
        # ここで何も bind しなければ縦スクロールになる。はみ出しているときの
        # 横移動は横スクロールバー(=108)と Shift+ホイールで行う。
        self.state_canvas.bind("<Shift-MouseWheel>", self._on_state_shift_wheel)
        self.state_canvas.bind(   # Linux(X11)のホイールは Button-4/5
            "<Shift-Button-4>",
            lambda e: self._on_state_shift_wheel(e, step=-1))
        self.state_canvas.bind(
            "<Shift-Button-5>",
            lambda e: self._on_state_shift_wheel(e, step=1))
        self.trans_summary_label = ctk.CTkLabel(
            st_canvas_wrap, text="", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED, justify="left", anchor="w")
        self.trans_summary_label.pack(fill="x", padx=10, pady=(0, 4))   # =100②

        # 選択ステートの見出し(インライン編集で名前を変更できる)
        st_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        st_row.pack(fill="x", pady=(2, 0))   # =100②: 4→2
        lbl = ctk.CTkLabel(st_row, text=tr("ステート名:"),
                           font=ctk.CTkFont(size=14, weight="bold"),
                           text_color=ACCENT_TEXT)
        lbl.pack(side="left")
        st_col_labels.append(lbl)
        self.state_id_var = tk.StringVar(value="")
        self.state_id_entry = ctk.CTkEntry(
            st_row, textvariable=self.state_id_var, width=150, height=28,
            font=ctk.CTkFont(size=14, weight="bold"))
        self.state_id_entry.pack(side="left", padx=(6, 0))
        self.state_id_entry.bind("<Return>", lambda _e: self._commit_state_rename())
        self.state_id_entry.bind("<FocusOut>", lambda _e: self._commit_state_rename())
        self.state_title_label = self.state_id_entry
        self.state_start_var = tk.BooleanVar(value=False)
        self.state_start_check = ctk.CTkCheckBox(
            st_row, text=tr("開始ステートにする"), variable=self.state_start_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.state_start_check.pack(side="left", padx=16)
        # ステート開始時の変数操作(変数宣言があるときだけ表示)
        self._st_ops: list = []
        self._st_end_ops: list = []
        self.state_ops_btn = ctk.CTkButton(
            st_row, text="", width=150, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._edit_state_ops)

        # 移行条件
        trans_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        trans_row.pack(fill="x", pady=(2, 0))   # =100②: 4→2
        lbl = ctk.CTkLabel(trans_row, text=tr("ステート移行:"),
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        lbl.pack(side="left")
        st_col_labels.append(lbl)
        self._align_label_column(st_col_labels)
        self.trans_type_var = tk.StringVar(value=tr("ステート移行なし"))
        self.trans_type_menu = CTkOptionMenu(
            trans_row, variable=self.trans_type_var, width=170, height=26,
            values=list(self.TRANS_CHOICES),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_trans_type_menu_change(),
        )
        self.trans_type_menu.pack(side="left", padx=6)
        self.trans_row = trans_row
        self.trans_ch_label = ctk.CTkLabel(
            trans_row, text=tr("対象ch:"), font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED)
        self.trans_ch_label.pack(side="left", padx=(8, 2))
        self.trans_ch_var = tk.StringVar(value="C")
        self.trans_ch_menu = CTkOptionMenu(
            trans_row, variable=self.trans_ch_var, width=60, height=26,
            values=list(CHANNEL_IDS),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.trans_ch_menu.pack(side="left")
        self.trans_min_field = VarRefField(trans_row, width=54,
                                           placeholder="min")
        self.trans_min_field.pack(side="left", padx=(10, 2))
        # =67: 単位は min / max の両方に添える(ユーザー要望)
        self.trans_unit_min_label = ctk.CTkLabel(
            trans_row, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.trans_unit_min_label.pack(side="left")
        self.trans_tilde_label = ctk.CTkLabel(
            trans_row, text="〜", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_tilde_label.pack(side="left", padx=(4, 2))
        self.trans_max_field = VarRefField(trans_row, width=54,
                                           placeholder="max")
        self.trans_max_field.pack(side="left", padx=2)
        self.trans_unit_label = ctk.CTkLabel(
            trans_row, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.trans_unit_label.pack(side="left")
        # 音声なしステート(有効チャンネル0)のときだけ pack するヒント
        self.trans_noaudio_hint = ctk.CTkLabel(
            trans_row, text=tr("(音声なし=即座に通過)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)

        # 「判定式でステート移行」時の判定式(AND)。TRANS_COND 選択時のみ pack
        self.trans_cond_frame = ctk.CTkFrame(
            self.states_inner, fg_color="transparent")
        ctk.CTkLabel(self.trans_cond_frame,
                     text=tr("移行する判定式(すべて成立で移行):"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w")
        self.trans_cond = CondListEditor(self.trans_cond_frame)
        self.trans_cond.pack(fill="x", padx=(12, 0))
        # =275: 「選択肢でステート移行」の編集ブロック。TRANS_CHOICE 選択時のみ
        # trans_row の直後へ pack し、移行先行(to_row)・分岐オプションは隠す
        self.state_choice = StateChoiceEditor(self.states_inner, self)

        # 移行先(チェックで複数=抽選。=73で各候補に重み欄=定数 or 変数参照)
        to_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        to_row.pack(fill="x", pady=(2, 2))   # =100②: 4→2
        self.trans_to_row = to_row           # =125: cond箱のpack位置基準
        self.trans_to_label = ctk.CTkLabel(
            to_row, text=tr("ステート移行先(複数チェックで抽選):"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_to_label.pack(side="left")
        # =125: 移行先の決め方(分岐 / 変数分岐)。「変数分岐」は
        # 変数宣言があるときだけ選択肢に出す(イベントの遷移方法と同じ相関制御)
        # =167: 「固定」(移行先が1つ)を追加。JSONは "to": "S2"(文字列)で、
        # 「分岐」を1つだけチェックした場合と同じ形。読み込みは
        # 常に「固定」として開く(イベントの遷移方法=166と同じ扱い)。
        # =169: 名称を「チェックで抽選」→「分岐」へ(ユーザー決定)。
        # イベントの遷移方法の「分岐」と同じ概念なので言葉を揃える。
        self.TRANS_TOMODE_FIXED = tr("固定")
        self.TRANS_TOMODE_PICK = tr("分岐")
        self.TRANS_TOMODE_COND = tr("変数分岐")
        self.trans_tomode_var = tk.StringVar(value=self.TRANS_TOMODE_FIXED)
        self.trans_tomode_menu = CTkOptionMenu(
            to_row, variable=self.trans_tomode_var, width=130, height=26,
            values=[self.TRANS_TOMODE_FIXED, self.TRANS_TOMODE_PICK],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_trans_tomode_ui())
        self.trans_tomode_menu.pack(side="left", padx=(6, 0))
        # =167: 「固定」の移行先メニュー(同じ行に置く)
        self.trans_fixed_var = tk.StringVar(value="")
        self.trans_fixed_menu = CTkOptionMenu(
            to_row, variable=self.trans_fixed_var, width=160, height=26,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.trans_targets_frame = ctk.CTkFrame(to_row, fg_color="transparent")
        self.trans_targets_frame.pack(side="left", padx=8)
        self.trans_target_vars: dict[str, tk.BooleanVar] = {}
        self.trans_target_checks: list[ctk.CTkCheckBox] = []
        self.trans_weight_fields: dict[str, VarRefField] = {}

        # =77: 分岐オプション(未実行ステートのみ候補・全消化時の挙動)。
        # =133: 文言を「分岐先の候補:」「〜のみ候補」へ(仕様どおり=除外)。
        # =26のイベント版(next_visited_var/next_exh_row)と同じ構成・同じ
        # 2行方式(「全候補が実行済みのとき:」は行ごと出し入れする)。
        tv_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        tv_row.pack(fill="x", pady=(0, 2))   # =100②: 4→2
        self.trans_visited_row = tv_row
        self.trans_visited_label = ctk.CTkLabel(
            tv_row, text=tr("分岐先の候補:"), font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED)
        self.trans_visited_label.pack(side="left")
        self.trans_visited_var = tk.StringVar(value=tr("毎回すべて候補"))
        self.trans_visited_menu = CTkOptionMenu(
            tv_row, variable=self.trans_visited_var, width=190, height=26,
            values=[tr("毎回すべて候補"), tr("未実行ステートのみ候補")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_trans_visited_ui(),
        )
        self.trans_visited_menu.pack(side="left", padx=6)
        self.trans_exh_row = ctk.CTkFrame(self.states_inner,
                                          fg_color="transparent")
        self.trans_exh_label = ctk.CTkLabel(
            self.trans_exh_row, text=tr("全候補が実行済みのとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_exhausted_var = tk.StringVar(
            value=tr("以後、毎回すべて候補"))
        self.trans_exh_menu = CTkOptionMenu(
            self.trans_exh_row, variable=self.trans_exhausted_var,
            width=280, height=26,
            values=[tr("以後、毎回すべて候補"),
                    tr("リセットして再び、未実行ステートのみ候補"),
                    tr("指定ステートへ")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_trans_visited_ui(),
        )
        self.trans_exh_to_var = tk.StringVar(value="")
        self.trans_exh_to_menu = CTkOptionMenu(
            self.trans_exh_row, variable=self.trans_exh_to_var,
            width=140, height=26, values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))

        # =73: 全候補の重みが0以下(=出せる候補なし)のときの移行先(else)。
        # 「(イベント終了)」=指定なし。重みを変数にした時のフォールバック用
        # (=26のイベント版ランダム分岐と同じ作法)。
        else_row = ctk.CTkFrame(self.states_inner, fg_color="transparent")
        else_row.pack(fill="x", pady=(0, 2))   # =100②: 4→2
        self.trans_else_row = else_row
        self.trans_else_label = ctk.CTkLabel(
            else_row, text=tr("重みが全て0のとき:"),
            font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.trans_else_label.pack(side="left")
        self.TRANS_ELSE_END = tr("(イベント終了)")
        self.trans_else_var = tk.StringVar(value=self.TRANS_ELSE_END)
        self.trans_else_menu = CTkOptionMenu(
            else_row, variable=self.trans_else_var, width=160, height=26,
            values=[self.TRANS_ELSE_END],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.trans_else_menu.pack(side="left", padx=6)

        # =125: 移行先の判定式(cond)エディタ。「変数分岐」選択時のみ
        # to_row の直後へ pack する(イベントの変数分岐と同じ見た目)。
        self.strans_cond_box = ctk.CTkFrame(self.states_inner,
                                            fg_color="transparent")
        ctk.CTkLabel(self.strans_cond_box,
                     text=tr("上から順に評価し、最初に成立した行へ移行します。行内の条件はAND。"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
                     anchor="w").pack(fill="x", pady=(2, 0))
        self.strans_rows_frame = ctk.CTkFrame(self.strans_cond_box,
                                              fg_color="transparent")
        self.strans_rows_frame.pack(fill="x", pady=(2, 0))
        self.strans_rows: list[dict] = []
        strans_add = ctk.CTkFrame(self.strans_cond_box,
                                  fg_color="transparent")
        strans_add.pack(fill="x", pady=(2, 0))
        ctk.CTkButton(
            strans_add, text=tr("＋ 条件行を追加"), width=120, height=26,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=lambda: self._add_strans_row()).pack(side="left")
        strans_else = ctk.CTkFrame(self.strans_cond_box,
                                   fg_color="transparent")
        strans_else.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(strans_else, text=tr("どの行も成立しないとき(else):"),
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED
                     ).pack(side="left")
        self.STRANS_ELSE_NONE = tr("(移行しない)")
        self.strans_else_var = tk.StringVar(value=self.STRANS_ELSE_NONE)
        self.strans_else_menu = CTkOptionMenu(
            strans_else, variable=self.strans_else_var, width=150, height=26,
            values=[self.STRANS_ELSE_NONE],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.strans_else_menu.pack(side="left", padx=6)

        # ===== ここまでステート領域 =====

        # device担当
        # =107: 「見落としやすい」というユーザー指摘への対応。透明フレームの
        # 1行だったものを、イベント/ステートボックスと同じ丸角+枠線の
        # 独立ブロックにして、上のステート領域・下のチャンネル3列から
        # 視覚的に切り離す。中身(ラベル+種別ごとのメニュー)は従来どおり。
        self.device_box = ctk.CTkFrame(p, corner_radius=8, fg_color=BOX_BG,
                                       border_width=1, border_color=BOX_BORDER)
        self.device_box.pack(fill="x", pady=(6, 4))
        row2 = ctk.CTkFrame(self.device_box, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(row2, text=tr("デバイス担当:"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_HEAD).pack(side="left")
        self.device_vars: dict[str, tk.StringVar] = {}
        self.device_menus: dict[str, ctk.CTkOptionMenu] = {}
        # =88: TWIST常時表示化により show_types は常に全種別
        # (=79のサブ機能スイッチによる出し入れは廃止)。
        show_types = menu_device_types()
        for ttype in DEVICE_TYPES:
            shown = ttype in show_types
            lbl = ctk.CTkLabel(row2, text=f"{ttype}→", font=ctk.CTkFont(size=12),
                               text_color=TEXT_MUTED)
            if shown:
                lbl.pack(side="left", padx=(10, 2))
            var = tk.StringVar(value=tr("なし"))
            menu = CTkOptionMenu(
                row2, variable=var, width=70, height=26,
                values=[tr("なし"), "L", "C", "R"],
                fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            )
            if shown:
                menu.pack(side="left")
            self.device_vars[ttype] = var
            self.device_menus[ttype] = menu

        # 動画(外部mpv再生)は =52 でチャンネルの中身になったため、
        # ここにあった専用の VideoRow は廃止した(ユーザー要望=動画対応
        # 以前と同じレイアウトへ戻す)。動画は各チャンネルの
        # 「＋動画」から動画アイテムとして並べる。

        # ===== 下段: チャンネル L/C/R を丸角の灰色枠で3列に並べる =====
        chan_grid = ctk.CTkFrame(p, fg_color="transparent")
        chan_grid.pack(fill="x", pady=(2, 4))
        # =252: デバイス連動OFF→ONで device_box を戻すときの pack 先アンカー
        self.chan_grid = chan_grid
        for i in range(3):
            chan_grid.grid_columnconfigure(i, weight=1, uniform="chan")
        # シークバー追従チャンネル(L/C/Rで1つ。各セクションのラジオで共有)
        self.seek_var = tk.StringVar(value="C")
        self.channel_sections: dict[str, ChannelSection] = {}
        for i, ch_id in enumerate(CHANNEL_IDS):
            sec = ChannelSection(chan_grid, ch_id, self.base_dir, owner=self,
                                 compact=True)
            # sticky=new: 横は3等分で伸ばし、縦は各チャンネルの中身の高さのまま
            # 上端揃え(音声数が違ってもボタンが下端に落ちない)
            sec.grid(row=0, column=i, sticky="new",
                     padx=(0, 6) if i < 2 else 0, pady=5)
            self.channel_sections[ch_id] = sec

        # ===== BGM(=256): ノード(イベント/ステート)の一番下のブロック =====
        # bgm_enabled ONのときだけ _apply_bgm_enabled が chan_grid の直後へ
        # packする。3択(引き継ぐ/指定/オフ)+「指定」時のみアイテム一覧・
        # ＋BGM・順序・既定パンを展開する。
        self.bgm_box = ctk.CTkFrame(p, corner_radius=8, fg_color=BOX_BG,
                                    border_width=1, border_color=BOX_BORDER)
        brow = ctk.CTkFrame(self.bgm_box, fg_color="transparent")
        brow.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(brow, text=tr("BGM:"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_HEAD).pack(side="left")
        self.bgm_mode_var = tk.StringVar(value=self.BGM_INHERIT)
        self.bgm_mode_menu = CTkOptionMenu(
            brow, variable=self.bgm_mode_var, width=190, height=26,
            values=[self.BGM_INHERIT, self.BGM_SET, self.BGM_OFF],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._update_bgm_ui(),
        )
        self.bgm_mode_menu.pack(side="left", padx=(6, 0))
        self.bgm_hint = ctk.CTkLabel(
            brow, text=tr("(BGMはイベント/ステートをまたいで再生され続けます)"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.bgm_hint.pack(side="left", padx=8)

        self.bgm_body = ctk.CTkFrame(self.bgm_box, fg_color="transparent")
        # 空のCTkFrameは既定高さ(200px)を保持して間延びするため、height=0で
        # 生成し、行数に応じて _bgm_refit_items が propagate を切り替える
        # (ChannelSection.items_frame の =既知対処と同じ)。
        self.bgm_items_frame = ctk.CTkFrame(self.bgm_body,
                                            fg_color="transparent", height=0)
        self.bgm_items_frame.pack(fill="x", padx=6)
        self._bgm_rows: list[BgmItemRow] = []
        bctl = ctk.CTkFrame(self.bgm_body, fg_color="transparent")
        bctl.pack(fill="x", padx=6, pady=(4, 4))
        self.bgm_add_btn = ctk.CTkButton(
            bctl, text=tr("＋BGM"), width=84, height=28,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._bgm_add_items)
        self.bgm_add_btn.pack(side="left")
        self.bgm_order_var = tk.StringVar(value=tr("順番に再生"))
        self.bgm_order_menu = CTkOptionMenu(
            bctl, variable=self.bgm_order_var, width=150, height=26,
            values=[tr("順番に再生"), tr("ランダム再生")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"))
        self.bgm_order_menu.pack(side="left", padx=8)
        self.bgm_pan_var = tk.BooleanVar(value=False)
        self.bgm_pan_check = ctk.CTkCheckBox(
            bctl, text=tr("パン上書き(L/R %)"), variable=self.bgm_pan_var,
            font=ctk.CTkFont(size=11), checkbox_width=16, checkbox_height=16,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_bgm_pan_toggle)
        self.bgm_pan_check.pack(side="left", padx=(10, 0))
        self.bgm_pan_box = ctk.CTkFrame(bctl, fg_color="transparent")
        self.bgm_pan_l_var = tk.StringVar(value="")
        self.bgm_pan_r_var = tk.StringVar(value="")
        ctk.CTkLabel(self.bgm_pan_box, text="L", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.bgm_pan_l_entry = ctk.CTkEntry(
            self.bgm_pan_box, width=42, height=24,
            textvariable=self.bgm_pan_l_var, font=ctk.CTkFont(size=11))
        self.bgm_pan_l_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(self.bgm_pan_box, text="R", font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.bgm_pan_r_entry = ctk.CTkEntry(
            self.bgm_pan_box, width=42, height=24,
            textvariable=self.bgm_pan_r_var, font=ctk.CTkFont(size=11))
        self.bgm_pan_r_entry.pack(side="left", padx=(2, 0))

    # ---------------- シークバー追従チャンネル ----------------

    @staticmethod
    def _ch_raw_has_audio(ch_raw) -> bool:
        """チャンネルraw(dict)に音声つきアイテムが1つ以上あるか。"""
        if not isinstance(ch_raw, dict):
            return False
        for it in (ch_raw.get("items") or []):
            if isinstance(it, str) and it:
                return True
            if isinstance(it, dict) and it.get("audio"):
                return True
        return False

    def _auto_seek_channel(self, channels: dict) -> str:
        """無指定時の自動決定: C→L→R の優先順で音声のあるチャンネル。

        =63: 音声がどこにも無い(スクリプト専用chだけの)ときは、同じ優先順で
        アイテムを持つチャンネルへフォールバックする(Scenario 側の
        seek_follow_channel と揃える)。
        """
        for cid in ("C", "L", "R"):
            if self._ch_raw_has_audio((channels or {}).get(cid)):
                return cid
        for cid in ("C", "L", "R"):
            ch = (channels or {}).get(cid)
            if isinstance(ch, dict) and (ch.get("items") or []):
                return cid
        return "C"

    def _seek_channel_out(self, channels: dict):
        """保存値を返す。自動決定と同じ選択なら None(=キー省略)。

        選択チャンネルに音声が無い(無効な選択)場合は自動決定へ正規化する。
        """
        sel = self.seek_var.get() or "C"
        sel_raw = (channels or {}).get(sel)
        # =63: スクリプト専用chも選べる(アイテムがあれば有効な選択とみなす)
        if not (self._ch_raw_has_audio(sel_raw)
                or (isinstance(sel_raw, dict) and (sel_raw.get("items") or []))):
            sel = self._auto_seek_channel(channels)
        auto = self._auto_seek_channel(channels)
        return sel if sel != auto else None

    # ================= 変数(vars)まわり =================

    def _var_decl_init(self, decl):
        return decl.get("init") if isinstance(decl, dict) else decl

    # ---------------- BGM編集(=256) ----------------

    def _update_bgm_ui(self):
        """3択に応じて「指定」の本体(アイテム一覧+操作行)を出し入れする。"""
        if self.bgm_mode_var.get() == self.BGM_SET:
            if not self.bgm_body.winfo_manager():
                self.bgm_body.pack(fill="x", padx=4, pady=(0, 4))
        else:
            self.bgm_body.pack_forget()

    def _on_bgm_pan_toggle(self):
        if self.bgm_pan_var.get():
            if not self.bgm_pan_box.winfo_manager():
                self.bgm_pan_box.pack(side="left", padx=(4, 0))
            if not self.bgm_pan_l_var.get() and not self.bgm_pan_r_var.get():
                self.bgm_pan_l_var.set("100")
                self.bgm_pan_r_var.set("100")
        else:
            self.bgm_pan_box.pack_forget()

    def _bgm_refit_items(self):
        """BGMが0曲のとき bgm_items_frame を潰す(=257)。

        空になった直後のフレームは pack_propagate では縮まず(最後のサイズ、
        初期状態では既定の200pxを保持する)、「BGMを指定」へ切り替えた直後に
        3択行と＋BGM行の間が大きく空く。ChannelSection._refit_items と同じ
        対処: 行があれば propagate、0件なら高さ1に固定して縮める。
        """
        if self._bgm_rows:
            self.bgm_items_frame.pack_propagate(True)
        else:
            self.bgm_items_frame.pack_propagate(False)
            self.bgm_items_frame.configure(height=1)

    def _bgm_append_row(self, raw_item):
        self.bgm_items_frame.pack_propagate(True)
        row = BgmItemRow(self.bgm_items_frame, raw_item, self.base_dir,
                         self._bgm_delete_row, self._bgm_move_row, owner=self)
        row.pack(fill="x", pady=2)
        self._bgm_rows.append(row)

    def _bgm_delete_row(self, row):
        self._bgm_rows.remove(row)
        row.destroy()
        self._bgm_refit_items()

    def _bgm_move_row(self, row, delta: int):
        """▲▼でBGMの再生順を入れ替える(順番モードで意味を持つ)。"""
        i = self._bgm_rows.index(row)
        j = i + delta
        if j < 0 or j >= len(self._bgm_rows):
            return
        self._bgm_rows[i], self._bgm_rows[j] = \
            self._bgm_rows[j], self._bgm_rows[i]
        for r in self._bgm_rows:
            r.pack_forget()
        for r in self._bgm_rows:
            r.pack(fill="x", pady=2)

    def _bgm_add_items(self):
        """＋BGM: 音声ファイル(wav/mp3)をダイアログで選んで追加する。"""
        paths = filedialog.askopenfilenames(
            title=tr("BGM音声を選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("音声"), "*.wav *.mp3"), (tr("すべて"), "*.*")],
            parent=self,
        )
        if paths:
            _remember_dialog_dir(paths[0])
        for p in paths:
            self._bgm_append_row(_safe_relpath(p, self.base_dir))

    def _bgm_hit(self, x, y) -> bool:
        """落下点(スクリーン座標)がBGMブロックの上か(=256のD&D用)。"""
        try:
            w = self.bgm_box
            if not w.winfo_ismapped():
                return False
            wx, wy = w.winfo_rootx(), w.winfo_rooty()
            return (wx <= x < wx + w.winfo_width()
                    and wy <= y < wy + w.winfo_height())
        except Exception:
            return False

    def _bgm_dropped_audio(self, paths):
        """D&DされたBGM音声を登録する(=256)。

        ＋BGMと同じくwav/mp3のみ。3択が「指定」以外なら自動で「指定」へ
        切り替える(チャンネルD&Dの自動有効化=44と同じ流儀)。対応外の
        拡張子・実在しないファイルは黙って無視する(既存仕様)。
        """
        ok = [p for p in paths
              if os.path.isfile(p)
              and os.path.splitext(p)[1].lower() in (".wav", ".mp3")]
        if not ok:
            return
        if self.bgm_mode_var.get() != self.BGM_SET:
            self.bgm_mode_var.set(self.BGM_SET)
            self._update_bgm_ui()
        for p in ok:
            self._bgm_append_row(_safe_relpath(p, self.base_dir))

    def _load_bgm(self, raw):
        """ノードの "bgm" 値をBGMブロックへ反映する(=256)。

        None/キー無し=「引き継ぐ」、{"off": true}=オフ、それ以外=指定。
        """
        for r in self._bgm_rows:
            r.destroy()
        self._bgm_rows = []
        self._bgm_refit_items()
        self.bgm_order_var.set(tr("順番に再生"))
        self.bgm_pan_var.set(False)
        self.bgm_pan_l_var.set("")
        self.bgm_pan_r_var.set("")
        self._on_bgm_pan_toggle()
        if not isinstance(raw, dict):
            self.bgm_mode_var.set(self.BGM_INHERIT)
        elif raw.get("off"):
            self.bgm_mode_var.set(self.BGM_OFF)
        else:
            self.bgm_mode_var.set(self.BGM_SET)
            for it in (raw.get("items") or []):
                self._bgm_append_row(it)
            if raw.get("order") == "random":
                self.bgm_order_var.set(tr("ランダム再生"))
            pan = raw.get("pan")
            if isinstance(pan, dict):
                self.bgm_pan_var.set(True)
                try:
                    self.bgm_pan_l_var.set(
                        str(int(round(float(pan.get("left", 1.0)) * 100))))
                    self.bgm_pan_r_var.set(
                        str(int(round(float(pan.get("right", 1.0)) * 100))))
                except (TypeError, ValueError):
                    pass
                self._on_bgm_pan_toggle()
        self._update_bgm_ui()

    def _collect_bgm(self, where: str):
        """BGMブロックの内容をJSON値へ。(エラー文|None, 値|None) を返す。

        値: None=引き継ぐ(キー省略) / {"off": true} / {"items": [...], ...}。
        「指定」なのにアイテム0件はエラー(Q12=ユーザー確定)。
        BGM:OFF(bgm_enabled=False)の間もブロックは非表示なだけで、読み込んだ
        値を保持したまま書き戻す=保存してもノードの "bgm" 定義は消えない
        (=252のデバイス連動と同じ「UIは非表示・裏はデータ保持」方式)。
        """
        mode = self.bgm_mode_var.get()
        if mode == self.BGM_OFF:
            return None, {"off": True}
        if mode != self.BGM_SET:
            return None, None
        if not self._bgm_rows:
            self._want_mark(self.bgm_add_btn, "error")
            return (tr('{0}: BGMを「指定」にしていますが、音声がありません'
                       ).format(where), None)
        out = {"items": [r.collect() for r in self._bgm_rows]}
        if self.bgm_order_var.get() == tr("ランダム再生"):
            out["order"] = "random"
        if self.bgm_pan_var.get():
            try:
                lp = max(0, min(100, int(float(self.bgm_pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.bgm_pan_r_var.get() or 0))))
                out["pan"] = {"left": lp / 100.0, "right": rp / 100.0}
            except ValueError:
                pass
        return None, out


    def _var_names(self) -> list[str]:
        return list((self.data.get("vars") or {}).keys())

    @staticmethod
    def _branch_weight_is_zero(w) -> bool:
        """重み欄が 0/空/数値でない(=未指定扱い)か。変数参照は「0ではない」。"""
        raw = w.get_raw()
        if isinstance(raw, dict):
            return False
        txt = raw.strip()
        if txt == "":
            return True
        try:
            return _parse_num_text(txt) == 0
        except ValueError:
            return True

    def _on_branch_check(self, var, w):
        """=284: チェックONで重みが0/空なら「1」を入れる。OFFは重みを触らない。"""
        if var.get() and self._branch_weight_is_zero(w):
            self._branch_syncing = True
            try:
                w.set("1")
            finally:
                self._branch_syncing = False

    def _on_branch_weight(self, var, w):
        """=284: 重みに0以外(変数参照を含む)が入ったら自動でチェックON。"""
        if getattr(self, "_branch_syncing", False):
            return
        if not var.get() and not self._branch_weight_is_zero(w):
            var.set(True)

    def _numeric_var_names(self) -> list[str]:
        return [n for n, d in (self.data.get("vars") or {}).items()
                if _is_num(self._var_decl_init(d))]

    def _string_var_names(self) -> set:
        return {n for n, d in (self.data.get("vars") or {}).items()
                if isinstance(self._var_decl_init(d), str)}

    def _has_vars(self) -> bool:
        return bool(self.data.get("vars"))

    def _update_vars_btn(self):
        n = len(self._var_names())
        self.vars_btn.configure(text=tr("変数/監視({0})").format(n) if n
                                else tr("変数/監視"))

    def _open_help(self):
        """ヘルプを開く(=118でボタンはメイン画面へ移動。互換用に残置)。

        既に開いていれば前面へ出すだけ(1つに限定)。
        """
        dlg = getattr(self, "_help_dlg", None)
        if dlg is not None and dlg.winfo_exists():
            _front_window(dlg)
            return
        self._help_dlg = HelpDialog(self)

    def _mpv_missing_popup(self):
        """動画レビューに mpv が無いときの案内(=203)。"""
        win = ctk.CTkToplevel(self)
        win.title("RVP")
        ctk.CTkLabel(
            win, justify="left",
            text=tr("動画のレビューには mpv が必要です。\n"
                    "メイン画面の Settings で mpv のパスを指定してください"),
            font=ctk.CTkFont(size=13)).pack(padx=24, pady=(20, 12))
        ctk.CTkButton(win, text="OK", width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=win.destroy).pack(pady=(0, 16))
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

    def open_item_review(self, row) -> "ItemReviewDialog":
        """アイテムレビュー画面を開く(=164)。

        **同時に開けるのは1つ**(ユーザー決定)。既に開いていれば、その窓の
        中身を新しいアイテムへ差し替えて前面化する(2つ目は作らない)。
        破棄の検出は winfo_exists() なので、閉じる側に後始末は要らない
        (=152の編集画面・=118のヘルプと同じ作法)。
        """
        # =203: 動画アイテムは mpv が必須。見つからなければ開かずに案内する
        if getattr(row, "is_video", False) and _mpv_path_setting() is None:
            self._mpv_missing_popup()
            return None
        spec = row.review_spec()
        dlg = self._review_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    # =170: 編集モードで未保存なら確認が挟まり、「やめる」で
                    # 差し替えを中止する(load が False を返す)。
                    if dlg.load(spec, row=row):
                        dlg.front()
                    return dlg
            except Exception:
                pass
            self._review_dlg = None
        self._review_dlg = ItemReviewDialog(self, spec,
                                            host=self.review_host,
                                            owner=self, row=row)
        return self._review_dlg

    def script_usage(self, fs_abs: str) -> list[str]:
        """シナリオ全体で指定スクリプトを使っている箇所の一覧(=170)。

        スクリプト編集の上書き保存の警告(仕様 14a)。検出範囲=**編集画面で
        開いているシナリオ全体**(未保存の編集内容込み)。パネルの内容を
        先にモデルへ反映してから raw dict を走査する(自動紐づけも解決する)。
        戻り値: ["イベント名 / L", "イベント名:ステート名 / C", ...]
        """
        if self.selected and self.selected in self.data.get("events", {}):
            try:
                self._apply_panel()     # 失敗しても data の現状で走査する
            except Exception:
                pass
        target = os.path.normcase(os.path.abspath(fs_abs))

        def item_paths(item):
            if isinstance(item, str):
                item = {"audio": item}
            if not isinstance(item, dict):
                return
            tracks = item.get("tracks")
            if isinstance(tracks, list):
                for t in tracks:
                    fs = (t or {}).get("funscript") if isinstance(t, dict) \
                        else None
                    if fs:
                        yield os.path.join(self.base_dir, fs)
                return
            if "funscript" in item:
                fs = item.get("funscript")
                if fs:                       # None = デバイスなし(=明示)
                    yield os.path.join(self.base_dir, fs)
                return
            src = item.get("audio") or item.get("video")
            if src:
                for _t, p in auto_bind_tracks(
                        os.path.join(self.base_dir, src)):
                    yield p

        uses: list[str] = []

        def scan_channels(label, raw):
            for ch_id, ch_raw in (raw.get("channels") or {}).items():
                if not isinstance(ch_raw, dict):
                    continue
                for item in ch_raw.get("items") or []:
                    for p in item_paths(item):
                        if os.path.normcase(os.path.abspath(p)) == target:
                            uses.append("{0} / {1}".format(label, ch_id))

        for ev_id, raw in (self.data.get("events") or {}).items():
            if not isinstance(raw, dict):
                continue
            if "states" in raw:
                for st_id, st_raw in (raw.get("states") or {}).items():
                    if isinstance(st_raw, dict):
                        scan_channels("{0}:{1}".format(ev_id, st_id), st_raw)
            else:
                scan_channels(ev_id, raw)
        return uses

    # ---------------- 紹介文ダイアログ(=250) ----------------

    def _open_detail_dialog(self):
        """紹介文の編集ダイアログを開く(モーダル)。

        「保存」はメモリ(_detail_text)への反映のみ(AQ1)。JSONファイルへは
        従来どおり編集画面の「保存」で書き出す。キャンセル/✕は破棄。
        """
        dlg = DetailDialog(self, self._detail_text)   # =277: 結果は履歴対象
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._detail_text = dlg.result

    # ---------------- 背景イラストダイアログ(=262) ----------------

    def _open_background_dialog(self):
        """背景イラスト(トップレベル "background")の設定ダイアログを開く。

        「保存」はメモリ(self.data)への反映のみ。JSONファイルへは編集画面の
        「保存」で書き出す(紹介文と同じ作法)。パスは絶対で保持し、保存時の
        _rebased_data_for_save が保存先基準の相対パスへ付け替える(保存先の
        外にある画像は外部素材警告・素材コピーの対象=_map_item_paths)。
        """
        dlg = BackgroundDialog(self, self.data.get("background"),
                               self.base_dir)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        bg = dlg.result.get("background")
        if bg is None:
            self.data.pop("background", None)
        else:
            # 相対で保持されていた既存パスと同様、self.data は base_dir
            # 基準。ダイアログは絶対パスを返すのでそのまま格納してよい
            # (_rebase_scenario_path が isabs を処理する)。
            self.data["background"] = bg

    # ---------------- デバイス連動トグル(=252) ----------------

    def _device_btn_text(self) -> str:
        return tr("デバイス連動：ON") if self.device_enabled \
            else tr("デバイス連動：OFF")

    def _toggle_device_enabled(self):
        """デバイス連動のON/OFFを切り替える(ツールバーのトグル)。

        UIは即時に切り替わり、JSONへは「保存」時に書き出される。
        OFFでもデバイス関連の入力値・トラック行は**裏で保持したまま**
        非表示にするだけなので、保存してもJSONの紐づけ定義・デバイス担当は
        消えない(ONに戻せば自動/手動紐づけがそのまま復活する)。
        """
        self.device_enabled = not self.device_enabled
        self._apply_device_enabled()
        self._hist_check()   # =277

    def _apply_device_enabled(self):
        """デバイス連動フラグを編集画面のUIへ反映する(=252)。

        OFFで隠すもの: デバイス担当ブロック(device_box)・各アイテムの
        自動/手動メニューとトラック領域・「＋スクリプト」ボタン。
        funscript/csv のD&Dも無効になる(_on_dnd_drop 側のガード)。
        スクリプト専用アイテムの**行そのものは残す**(AQ6=存在を隠さない)。
        """
        self.device_toggle_btn.configure(text=self._device_btn_text())
        if self.device_enabled:
            if not self.device_box.winfo_manager():
                self.device_box.pack(fill="x", pady=(6, 4),
                                     before=self.chan_grid)
        else:
            self.device_box.pack_forget()
        for sec in self.channel_sections.values():
            sec.set_device_visible(self.device_enabled)

    # ---------------- BGMトグル(=256) ----------------

    def _bgm_btn_text(self) -> str:
        return tr("BGM：ON") if self.bgm_enabled else tr("BGM：OFF")

    def _toggle_bgm_enabled(self):
        """BGM機能のON/OFFを切り替える(1段目のトグル。=252と同方式)。

        UIは即時に切り替わり、JSONへは「保存」時に書き出される。OFFでも
        各ノードのBGM設定は**裏で保持したまま**非表示にするだけなので、
        保存してもJSONの "bgm" 定義は消えない(ONに戻せば復活する)。
        """
        self.bgm_enabled = not self.bgm_enabled
        self._apply_bgm_enabled()
        self._hist_check()   # =277

    def _apply_bgm_enabled(self):
        """BGMフラグを編集画面のUIへ反映する(=256)。

        OFFで隠すもの: パネル最下部のBGMブロック(bgm_box)。BGM音声の
        D&Dも受けない(_on_dnd_drop 側のガード)。
        """
        self.bgm_toggle_btn.configure(text=self._bgm_btn_text())
        if self.bgm_enabled:
            if not self.bgm_box.winfo_manager():
                self.bgm_box.pack(fill="x", pady=(2, 4),
                                  after=self.chan_grid)
        else:
            self.bgm_box.pack_forget()

    def _open_vars_dialog(self):
        # 現在のパネルを反映してから開く(OK後にパネルを再構築するため)
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        dlg = VarsDialog(self, self.data.get("vars") or {},
                         self.data.get("watch") or [],
                         list(self.data["events"].keys()))
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._apply_vars_result(dlg.result)

    def _apply_vars_result(self, result: dict):
        """変数ダイアログの結果をモデルへ反映し、パネルを再構築する。"""
        if result.get("vars"):
            self.data["vars"] = result["vars"]
        else:
            self.data.pop("vars", None)
        if result.get("watch"):
            self.data["watch"] = result["watch"]
        else:
            self.data.pop("watch", None)
        self._update_vars_btn()
        if self.selected in self.data["events"]:
            self._load_panel(self.selected)
        self._redraw_canvas()

    def open_ops_dialog(self, title: str, sections, on_ok):
        """変数操作ダイアログを開き、OKなら on_ok(result) を呼ぶ。"""
        dlg = OpsDialog(self, title, sections, self._var_names(),
                        self._numeric_var_names(), self._string_var_names())
        self.wait_window(dlg)
        if dlg.result is not None:
            on_ok(dlg.result)

    @staticmethod
    def ops_btn_text(*ops_lists) -> str:
        n = sum(len(o or []) for o in ops_lists)
        return tr("変数操作({0})").format(n)

    # ================= イベントキャンバス =================

    def _chain_order(self) -> tuple[list[str], list[str]]:
        """startからnextを辿った順序と、到達できないイベントを返す。"""
        return _smap.chain_order(self.data)

    def _layout_tree(self) -> dict:
        """イベントを左→右の木として配置し {ev_id: (x, y)} を返す。

        実体は scenario_map.layout_tree(共通描画モジュール)。
        """
        return _smap.layout_tree(self.data)

    def refresh_theme(self):
        """=151: メイン画面でテーマが切り替わったときに呼ばれる。

        CTkウィジェットは apptheme.switch() が塗り直すが、素の tk.Canvas
        (イベント遷移図・ステート図)は自前描画なので描き直す必要がある。
        配布元は main.RVPApp._refresh_theme_widgets(親の子ウィンドウを
        走査して refresh_theme を持つものへ配る)。
        """
        for fn in (getattr(self, "_redraw_canvas", None),
                   getattr(self, "_redraw_state_canvas", None)):
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                # 図が描き直せなくても編集内容には影響しないので握りつぶす
                # (次の操作で描き直される)
                pass
        # =164: 開いているレビュー画面のグラフも自前描画なので配る
        dlg = getattr(self, "_review_dlg", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.refresh_theme()
            except Exception:
                pass

    # ================= UNDO/REDO(=277) =================
    #
    # スナップショット方式。モデル(self.data + 紹介文 + 連動/BGMフラグ)の
    # deepcopy を「節目」ごとに積む。節目=パネル反映(イベント/ステート切替・
    # 保存・構造操作の前)と、構造操作が最後に必ず通る共通経路
    # (_redraw_canvas / _load_panel / _load_state_panel)。個別のフックを
    # 入れ忘れても、直前スナップショットとの比較で拾える(チェックポイント方式)。
    # 文字単位の取り消しは対象外(Q1)。保存しても履歴は残す(Q4)。上限100段。

    HIST_MAX = 100

    def _hist_snapshot(self) -> dict:
        return {"data": copy.deepcopy(self.data),
                "detail": self._detail_text,
                "device": self.device_enabled,
                "bgm": self.bgm_enabled,
                "sel": self.selected,
                "state": self.sel_state}

    def _hist_key_live(self):
        """比較用キー(生の data から。title はファイル名追従なので除外)。"""
        d = {k: v for k, v in self.data.items() if k != "title"}
        return (d, self._detail_text, self.device_enabled, self.bgm_enabled)

    @staticmethod
    def _hist_key_snap(snap: dict):
        d = {k: v for k, v in snap["data"].items() if k != "title"}
        return (d, snap["detail"], snap["device"], snap["bgm"])

    def _hist_reset(self):
        """履歴を空にして現在を起点にする(起動時)。"""
        self._hist_undo = []
        self._hist_redo = []
        self._hist_last = self._hist_snapshot()
        self._hist_update_buttons()

    def _hist_check(self):
        """チェックポイント: 直前スナップショットと違えば1段積む。

        変更が無ければ選択位置だけを直前スナップショットへ写す(次に積む
        ときの「戻り先」になる)。
        """
        if self._hist_last is None or self._hist_suspend:
            return
        if self._hist_key_live() != self._hist_key_snap(self._hist_last):
            self._hist_undo.append(self._hist_last)
            if len(self._hist_undo) > self.HIST_MAX:
                del self._hist_undo[0]
            self._hist_redo = []
            self._hist_last = self._hist_snapshot()
        else:
            self._hist_last["sel"] = self.selected
            self._hist_last["state"] = self.sel_state
        self._hist_update_buttons()

    def _hist_normalize(self, apply_body):
        """読み込んだパネルを一度書き戻し、その「正規化」を起点へ畳み込む。

        JSONを読み込んだ直後の初回反映は、値を変えていなくても自動デバイス
        割当・秒の float 化・min/max 分解・next:null など正規化で raw と
        差が出る。それを1段の履歴にすると「何もしていないのに戻る」ことに
        なるので、読み込み直後に書き戻して直前スナップショットへ写す。
        本当の未記録変更が残っていれば先に積む(取りこぼし防止)。
        """
        if self._hist_last is None or self._hist_suspend:
            return
        if self._hist_key_live() != self._hist_key_snap(self._hist_last):
            self._hist_check()
        try:
            err = apply_body()
        except Exception:
            err = "exc"
        if err is not None:
            self._pending_marks = []   # 検証エラーは今は表示しない(次の反映で)
            return
        if self._hist_key_live() != self._hist_key_snap(self._hist_last):
            self._hist_last["data"] = copy.deepcopy(self.data)

    def _hist_update_buttons(self):
        for btn, hist in ((getattr(self, "undo_btn", None), self._hist_undo),
                          (getattr(self, "redo_btn", None), self._hist_redo)):
            if btn is not None:
                try:
                    btn.configure(state="normal" if hist else "disabled")
                except Exception:
                    pass

    def _hist_prepare(self) -> bool:
        """Q2-A: 入力途中の内容を先に反映してから履歴を操作する。

        反映で検証エラーなら履歴操作を行わず False。
        """
        if self._hist_suspend or self._hist_last is None:
            return False
        self._commit_pending_renames()
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return False
        self._hist_check()
        return True

    def _undo(self):
        if not self._hist_prepare() or not self._hist_undo:
            return
        self._hist_redo.append(self._hist_last)
        self._hist_restore(self._hist_undo.pop())
        self._report("ok", tr("元に戻しました"),
                     tr("あと{0}段").format(len(self._hist_undo)))

    def _redo(self):
        if not self._hist_prepare() or not self._hist_redo:
            return
        self._hist_undo.append(self._hist_last)
        self._hist_restore(self._hist_redo.pop())
        self._report("ok", tr("やり直しました"),
                     tr("あと{0}段").format(len(self._hist_redo)))

    def _hist_restore(self, snap: dict):
        """スナップショットをモデルへ戻し、一覧・図・パネルを組み立て直す。"""
        self._hist_suspend = True
        try:
            title = self.data.get("title", "")
            self.data = copy.deepcopy(snap["data"])
            self.data["title"] = title       # タイトルはファイル名追従のまま
            self._detail_text = snap["detail"]
            self.device_enabled = snap["device"]
            self.bgm_enabled = snap["bgm"]
            self._apply_device_enabled()
            self._apply_bgm_enabled()
            events = self.data["events"]
            sel = snap["sel"]
            if sel not in events:
                sel = self.data.get("start")
                if sel not in events:
                    sel = next(iter(events), None)
            self.selected = sel
            if sel is not None:
                self._load_panel(sel)
                st = snap["state"]
                ev = events[sel]
                if "states" in ev and st in ev["states"] \
                        and st != self.sel_state:
                    self._load_state_panel(st)
            self._redraw_canvas()
        finally:
            self._hist_suspend = False
        self._hist_last = snap
        self._hist_last["sel"] = self.selected
        self._hist_last["state"] = self.sel_state
        self._hist_update_buttons()

    # ---- =299: イベント図の配置モード(自動/手動) ----
    def _map_manual(self) -> bool:
        return _smap.is_manual(self.data)

    def _toggle_map_mode(self):
        """自動⇄手動。手動→自動は自動配置で描くが、各イベントの "pos" は
        **残す**(次に手動へ戻すと前の配置が復活。ユーザー決定)。"""
        if self._map_manual():
            self.data.pop("map_mode", None)
        else:
            self.data["map_mode"] = "manual"
        self._redraw_canvas()

    def _refresh_map_mode_btn(self):
        manual = self._map_manual()
        self.map_mode_btn.configure(
            text=tr("配置：手動") if manual else tr("配置：自動"),
            text_color=ACCENT_TEXT if manual else TEXT_MUTED)

    def _on_node_moved(self, ev_id: str, xy):
        """=299: ノードを D&D で離した(格子へ吸着・重なり回避済み)。"""
        ev = self.data["events"].get(ev_id)
        if not isinstance(ev, dict):
            return
        ev["pos"] = [int(xy[0]), int(xy[1])]
        self._redraw_canvas()

    def _redraw_canvas(self):
        # =299: 手動配置なら座標を確定(pos の無いイベントは自動配置から
        # 格子へ吸着し、重なるなら下へずらした位置を書き戻す)
        positions = on_move = None
        if _smap.is_manual(self.data):
            positions = _smap.manual_positions(self.data)
            for ev_id, (x, y) in positions.items():
                ev = self.data["events"].get(ev_id)
                if isinstance(ev, dict) and _smap.stored_pos(ev) != (x, y):
                    ev["pos"] = [int(x), int(y)]
            on_move = self._on_node_moved
        if hasattr(self, "map_mode_btn"):
            self._refresh_map_mode_btn()
        # =277: 構造操作(追加/削除/リネーム/コピー/変数・監視/背景 等)は
        # 最後に必ずここを通るので、履歴チェックポイントを置く
        self._hist_check()
        # =58: 折りたたみ中は描かない(開いたときに _toggle_map が描き直す)。
        # =300: 別ウィンドウ中は常に描く
        if not getattr(self, "_map_open", True) \
                and not getattr(self, "_map_undocked", False):
            return
        # 実体は scenario_map.draw_event_map(再生タブの表示専用ビューと共用)
        _smap.draw_event_map(
            self.canvas, self.data,
            selected=self.selected,
            on_click=self._select, on_rclick=self._on_event_node_rclick,
            positions=positions, on_move=on_move)
        # =72: 描画後の scrollregion が図の実高さなので、ここで反映する
        # (イベントの追加・削除・分岐の変更にも自動で追従する)
        self._apply_map_height()

    # ================= ステートマシン図 =================

    def _on_state_shift_wheel(self, event, step: int | None = None):
        """=276: Shift+ホイールでステート図を横に動かす(はみ出し時のみ)。"""
        try:
            first, last = self.state_canvas.xview()
        except Exception:
            return
        if float(first) <= 0.0 and float(last) >= 1.0:
            return          # はみ出していなければ動かさない(左寄せ固定)
        if step is None:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        self.state_canvas.xview_scroll(step, "units")

    def _on_state_xscroll(self, first, last):
        """ステート図の横スクロール位置をバーへ渡し、要否も判定する(=108)。"""
        try:
            self.state_hbar.set(first, last)
        except Exception:
            pass
        self._update_state_hbar(float(first), float(last))

    def _update_state_hbar(self, first: float = 0.0, last: float = 1.0):
        """はみ出しているときだけ横スクロールバーを出す(=108)。

        ステートが2〜3個しかない普段の編集で高さを食わないようにする。
        pack は「要約ラベルの前」へ差し込む(pack_forget で順序が失われるため
        before= の指定が必須。=107のmpv欄と同じ作法)。
        """
        bar = getattr(self, "state_hbar", None)
        if bar is None or getattr(self, "_state_hbar_busy", False):
            return
        need = (last - first) < 0.999
        mapped = bool(bar.winfo_ismapped())
        if need == mapped:
            return
        # pack/pack_forget は再びスクロール通知を呼びうるので1段で止める
        self._state_hbar_busy = True
        try:
            if need:
                bar.pack(fill="x", padx=8, pady=(0, 2),
                         before=self.trans_summary_label)
            else:
                bar.pack_forget()
        finally:
            self._state_hbar_busy = False

    def _scroll_state_into_view(self, positions: dict):
        """選択中のステートが画面外なら、見える位置まで横スクロールする(=108)。"""
        sid = self.sel_state
        if not sid or sid not in (positions or {}):
            return
        c = self.state_canvas
        try:
            sr = str(c.cget("scrollregion")).split()
            total = float(sr[2]) if len(sr) == 4 else 0.0
            vis = c.winfo_width()
            if total <= 0 or vis <= 1 or total <= vis:
                return
            x = positions[sid][0]
            left = c.canvasx(0)
            if left <= x - 40 and x + 40 <= left + vis:
                return                       # すでに見えている
            c.xview_moveto(max(0.0, (x - vis / 2)) / total)
        except Exception:
            pass

    def _redraw_state_canvas(self):
        ev = self.data["events"].get(self.selected or "")
        c = self.state_canvas
        c.configure(bg=_canvas_bg())   # テーマに応じて背景色を追従
        if not ev or "states" not in ev:
            c.delete("all")
            c.configure(scrollregion=(0, 0, 0, 0))
            self._update_state_hbar(0.0, 1.0)
            return
        # 実体は scenario_map.draw_state_map(再生タブの表示専用ビューと共用)
        pos = _smap.draw_state_map(
            c, ev, selected=self.sel_state,
            on_click=self._select_state, on_rclick=self._on_state_node_rclick)
        # =108: 図の幅が変わるのでスクロールバーの要否と位置を更新する
        self._scroll_state_into_view(pos)
        try:
            self._update_state_hbar(*[float(v) for v in c.xview()])
        except Exception:
            pass
        return pos

    # ================= ノード着色パレット(=124) =================

    def _on_event_node_rclick(self, ev_id: str, e):
        """イベント図のノード右クリック → カラーパレットを開く。"""
        if ev_id not in self.data["events"]:
            return
        self._open_node_palette(
            e.x_root, e.y_root,
            lambda col: self._set_node_color(ev_id, None, col))

    def _on_state_node_rclick(self, sid: str, e):
        """ステート図のノード右クリック → カラーパレットを開く。"""
        ev = self.data["events"].get(self.selected or "")
        if not isinstance(ev, dict) or sid not in (ev.get("states") or {}):
            return
        self._open_node_palette(
            e.x_root, e.y_root,
            lambda col, ev_id=self.selected: self._set_node_color(
                ev_id, sid, col))

    def _set_node_color(self, ev_id: str, state_id: str | None, color):
        """ノード色をモデル(raw dict)へ書き込む。color=None で既定色へ戻す。

        _apply_panel はイベントdictをin-placeで更新するため、ここで書いた
        "color" キーは保存(_do_save)までそのまま生き残る。
        """
        ev = self.data["events"].get(ev_id)
        if not isinstance(ev, dict):
            return
        target = ev if state_id is None \
            else (ev.get("states") or {}).get(state_id)
        if not isinstance(target, dict):
            return
        if color:
            target["color"] = color
        else:
            target.pop("color", None)
        self._redraw_canvas()
        if state_id is not None:
            self._redraw_state_canvas()

    def _close_node_palette(self):
        top = getattr(self, "_palette_win", None)
        self._palette_win = None
        if top is not None:
            try:
                top.grab_release()
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass

    def _open_node_palette(self, x_root: int, y_root: int, apply_cb):
        """右クリック位置へ色パレットのポップアップを開く。

        クリックで着色、「リセット」で既定色へ、パレット外クリック/Escで
        閉じる(grabで外クリックを捕まえる)。overrideredirect の素の
        Toplevel なので =108③のCTk withdraw バグの影響は受けない。
        """
        self._close_node_palette()
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        self._palette_win = top
        dark = ctk.get_appearance_mode() != "Light"
        bg = "#2b2b2b" if dark else "#f5f5f5"
        bd = "#777777" if dark else "#888888"
        fg = "#e8e8e8" if dark else "#111111"
        frame = tk.Frame(top, bg=bg, highlightthickness=1,
                         highlightbackground=bd)
        frame.pack(fill="both", expand=True)
        sw, gap, pad = 22, 4, 8
        cols = len(NODE_PALETTE[0])
        rows = len(NODE_PALETTE)
        w = pad * 2 + cols * sw + (cols - 1) * gap
        h = pad * 2 + rows * sw + (rows - 1) * gap
        cv = tk.Canvas(frame, width=w, height=h, bg=bg,
                       highlightthickness=0, bd=0)
        cv.pack()
        cells = []
        for ri, row in enumerate(NODE_PALETTE):
            for ci, col in enumerate(row):
                x0 = pad + ci * (sw + gap)
                y0 = pad + ri * (sw + gap)
                cv.create_rectangle(x0, y0, x0 + sw, y0 + sw,
                                    fill=col, outline=bd, width=1)
                cells.append((x0, y0, x0 + sw, y0 + sw, col))
        self._palette_cells = cells      # テストからのヒット判定用

        def on_swatch(e):
            for (x0, y0, x1, y1, col) in cells:
                if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                    apply_cb(col)
                    break
            self._close_node_palette()
            return "break"
        cv.bind("<Button-1>", on_swatch)
        tk.Button(frame, text=tr("リセット"), bg=bg, fg=fg,
                  activebackground=bd, relief="flat", bd=0,
                  font=(appfont.FAMILY, 10),
                  command=lambda: (apply_cb(None),
                                   self._close_node_palette())
                  ).pack(fill="x", padx=pad, pady=(0, 6))

        # 配置(画面外へはみ出さない)+外クリック/Escで閉じる。
        # =132: クランプ先は「右クリックした点のあるモニタ」。従来の
        # winfo_screenwidth はWindowsではプライマリモニタの幅なので、
        # サブディスプレイ上の右クリック位置がメイン側へ引き戻されていた。
        top.update_idletasks()
        tw = max(top.winfo_reqwidth(), w + 2)
        th = top.winfo_reqheight()
        mons = winstate.monitor_rects(top)
        fallback = (0, 0, int(top.winfo_screenwidth()),
                    int(top.winfo_screenheight()))
        x, y = winstate.clamp_point_popup(int(x_root), int(y_root),
                                          tw, th, mons, fallback)
        top.geometry(f"+{x}+{y}")
        top.bind("<Escape>", lambda _e: self._close_node_palette())

        def on_press(e):
            # grab中は外側のクリックもここへ届く。ポップアップの
            # 矩形外なら閉じる(内側はスウォッチ/ボタンに任せる)
            if not (0 <= e.x_root - top.winfo_rootx() <= top.winfo_width()
                    and 0 <= e.y_root - top.winfo_rooty()
                    <= top.winfo_height()):
                self._close_node_palette()
        top.bind("<Button-1>", on_press)
        try:
            top.grab_set()
        except Exception:
            pass

    def _update_trans_summary(self):
        """図の下に移行条件の要約テキストを表示する。"""
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev:
            self.trans_summary_label.configure(text="")
            return
        lines = []
        for sid, st in ev["states"].items():
            t = st.get("transition")
            if not t:
                lines.append(tr('{0}: ステート移行なし(イベント終了まで)').format(sid))
                continue
            when = t.get("when", {})
            ch = when.get("channel", "?")
            if when.get("type") == "choice":
                # =275: 選択肢(1)A 2)B) で → S2/イベント:X
                ents = [e for e in (t.get("choice") or [])
                        if isinstance(e, dict)]
                items = " ".join("{0}){1}".format(i + 1, e.get("label", ""))
                                 for i, e in enumerate(ents))
                dests = []
                for e in ents:
                    to = e.get("to")
                    d = (tr("イベント:{0}").format(to.get("event"))
                         if isinstance(to, dict) else str(to))
                    if d not in dests:
                        dests.append(d)
                lines.append(tr('{0}: {1} で → {2}').format(
                    sid, tr('選択肢({0})').format(items), "/".join(dests)))
                continue
            if when.get("type") == "cond":
                parts = []
                for c in when.get("conds") or []:
                    rhs = c.get("value")
                    if isinstance(rhs, dict):
                        rhs = tr("変数{0}").format(rhs.get("var"))
                    parts.append("{0} {1} {2}".format(
                        c.get("var"), c.get("op"), rhs))
                cond = tr('判定式({0})').format(tr("、").join(parts))
            elif when.get("type") == "all_channels":
                cond = tr('全チャンネル終了')
            elif when.get("type") == "channel_end":
                cond = tr('{0}ch終了').format(ch)
            elif when.get("type") == "channel_count":
                lo = _num_disp(when.get("count", when.get("min", "?")))
                hi = _num_disp(when.get("count", when.get("max",
                               when.get("min", "?"))))
                cond = tr('{0}chの{1}回').format(ch, lo) if lo == hi else tr('{0}chの{1}〜{2}回').format(ch, lo, hi)
            elif when.get("type") == "state_time":
                # =62: 経過時間(このステートに入ってからの秒数)
                lo = _num_disp(when.get("seconds", when.get("min_seconds", "?")))
                hi = _num_disp(when.get("seconds", when.get("max_seconds",
                               when.get("min_seconds", "?"))))
                cond = tr('経過{0}秒').format(lo) if lo == hi \
                    else tr('経過{0}〜{1}秒').format(lo, hi)
            else:
                lo = _num_disp(when.get("seconds", when.get("min_seconds", "?")))
                hi = _num_disp(when.get("seconds", when.get("max_seconds",
                               when.get("min_seconds", "?"))))
                cond = tr('{0}chの合計{1}秒').format(ch, lo) if lo == hi else tr('{0}chの合計{1}〜{2}秒').format(ch, lo, hi)
            to = t.get("to")
            if isinstance(to, str):
                dest = to
            else:
                # =73: 候補は文字列 or {"to","weight"}。重み・elseも要約に出す
                parts = []
                for ent in (to.get("random", []) if isinstance(to, dict) else []):
                    if isinstance(ent, str):
                        parts.append(ent)
                    elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                        w = ent.get("weight", 1.0)
                        if isinstance(w, dict):
                            w = tr("変数{0}").format(w.get("var"))
                        else:
                            w = _num_disp(w)
                        parts.append(tr("{0}(重み{1})").format(ent["to"], w))
                dest = "(" + "/".join(parts) + tr(" から抽選)")
                if isinstance(to, dict) and to.get("visited") == "exclude":
                    # =77: 未実行優先と全消化時の挙動も要約に出す
                    ex = to.get("when_exhausted", "all")
                    if ex == "reset":
                        ex_txt = tr("消化でリセット")
                    elif isinstance(ex, dict):
                        ex_txt = tr("消化で{0}へ").format(ex.get("to"))
                    else:
                        ex_txt = tr("消化で全候補")
                    dest += tr("(未実行優先・{0})").format(ex_txt)
                if isinstance(to, dict) and isinstance(to.get("else"), str):
                    dest += tr("(重み全0→{0})").format(to["else"])
            lines.append(tr('{0}: {1} で → {2}').format(sid, cond, dest))
        self.trans_summary_label.configure(text="\n".join(lines))

    # ================= 選択・パネル =================

    def _commit_pending_renames(self):
        """入力途中のイベント名/ステート名リネームをコミットする(=100①)。

        名前欄は <FocusOut>/<Return> でコミットしているが、遷移図・ステート図の
        ○やツールバーのボタンは**クリックしてもフォーカスが移らない**
        (tk.Canvas / CTkButton はクリックでフォーカスを取らない)ため、
        入力→即クリックの操作では FocusOut が発火せず、修正が黙って捨てられて
        いた。切り替え/保存/追加の入口で明示的にコミットする。
        名前が不正(空・重複)なときは従来どおりエラー表示+旧名へ戻る。
        """
        self._commit_state_rename()
        self._commit_event_rename()

    def _select(self, event_id: str):
        if event_id not in self.data["events"]:
            return
        # =100①: 入力途中のイベント名/ステート名を先にコミットする
        self._commit_pending_renames()
        if event_id not in self.data["events"]:
            # クリックした○が、直前のリネームで旧名になった場合(=自分自身)
            return
        # 現在の編集内容を反映してから切り替え
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        self.selected = event_id
        self._load_panel(event_id)
        self._redraw_canvas()

    # ---------------- next(遷移)UI ----------------

    @staticmethod
    def _next_primary(ev) -> str | None:
        """レイアウト用の代表遷移先(実体は scenario_map.next_primary)。"""
        return _smap.next_primary(ev)

    @staticmethod
    def _next_targets(ev) -> list[str]:
        """矢印描画用の全遷移先(実体は scenario_map.next_targets)。"""
        return _smap.next_targets(ev)

    def _rebuild_next_ui(self, event_id: str):
        """遷移候補チェック群と分岐オプションを現在のイベントで再構築する。"""
        ev = self.data["events"][event_id]
        nxt_raw = ev.get("next")
        # =123 すごろく(advance)の読込
        adv = ev.get("advance")
        self.sugoroku_var.set(adv is not None)
        self.advance_field.set_names(self._numeric_var_names())
        self.advance_field.set(adv if adv is not None else "")
        self._update_sugoroku_ui()
        self._choice_extra = {}
        self._choice_timeout_ops = []
        # 遷移方法の選択肢: 変数宣言があるときだけ変数分岐/数値入力を出す。
        # =166: 「固定」(遷移先が1つ)と「なし」(遷移しない)を追加。
        # 「固定」は選べる相手が居るときだけ出す(自分自身へは遷移させない)。
        others = [e for e in self.data["events"] if e != event_id]
        modes = []
        if others:
            modes.append(self.NEXT_FIXED)
        modes += [self.NEXT_BRANCH, self.NEXT_CHOICE]
        if self._has_vars():
            modes += [self.NEXT_COND, self.NEXT_COND_WATCH, self.NEXT_INPUT]
        modes.append(self.NEXT_NONE)
        self.next_mode_menu.configure(values=modes)
        self.next_fixed_menu.configure(values=others or [""])
        if isinstance(nxt_raw, dict) and "cond" in nxt_raw:
            # =168: 無限イベントでは「判定式(常に監視)」という別名で見せる
            self.next_mode_var.set(
                self.NEXT_COND_WATCH if self._infinite_event()
                else self.NEXT_COND)
            self._clear_choice_rows()
            self._load_cond_ui(nxt_raw)
            self._load_input_ui(None)
        elif isinstance(nxt_raw, dict) and "input" in nxt_raw:
            self.next_mode_var.set(self.NEXT_INPUT)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(nxt_raw)
        elif isinstance(nxt_raw, dict) and "choice" in nxt_raw:
            self.next_mode_var.set(self.NEXT_CHOICE)
            self._load_choice_ui(nxt_raw)
            self._clear_cond_rows()
            self._load_input_ui(None)
        elif isinstance(nxt_raw, str) and nxt_raw in others:
            # 文字列形式=遷移先が1つ → 「固定」として開く
            self.next_mode_var.set(self.NEXT_FIXED)
            self.next_fixed_var.set(nxt_raw)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(None)
        elif nxt_raw is None:
            self.next_mode_var.set(self.NEXT_NONE)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(None)
        else:
            self.next_mode_var.set(self.NEXT_BRANCH)
            self._clear_choice_rows()
            self._clear_cond_rows()
            self._load_input_ui(None)
        if self.next_fixed_var.get() not in others:
            self.next_fixed_var.set(others[0] if others else "")
        if self.next_mode_var.get() not in modes:
            # 遷移先が無くなった等で選べない状態になったら「なし」へ寄せる
            self.next_mode_var.set(self.NEXT_NONE)
        self._update_next_mode()
        for w in self.next_targets_frame.winfo_children():
            w.destroy()
        self.next_target_vars = {}
        self.next_weight_vars = {}

        # 現在の設定を解釈
        nxt = ev.get("next")
        checked: dict[str, object] = {}   # eid -> 生の重み(数値 or {"var":..})
        visited_exclude = False
        exh_mode, exh_to = "all", None
        else_to = None
        if isinstance(nxt, str):
            checked[nxt] = 1.0
        elif isinstance(nxt, dict):
            for ent in nxt.get("random") or []:
                if isinstance(ent, str):
                    checked[ent] = 1.0
                elif isinstance(ent, dict) and ent.get("to"):
                    checked[ent["to"]] = ent.get("weight", 1.0)
            visited_exclude = nxt.get("visited") == "exclude"
            ex = nxt.get("when_exhausted", "all")
            if ex == "reset":
                exh_mode = "reset"
            elif isinstance(ex, dict) and ex.get("to"):
                exh_mode, exh_to = "to", ex["to"]
            if isinstance(nxt.get("else"), str):
                else_to = nxt["else"]

        nums = self._numeric_var_names()
        per_row = 3
        for i, eid in enumerate(others):
            cell = ctk.CTkFrame(self.next_targets_frame, fg_color="transparent")
            cell.grid(row=i // per_row, column=i % per_row,
                      sticky="w", padx=(0, 14), pady=1)
            var = tk.BooleanVar(value=eid in checked)
            label = eid if len(eid) <= 14 else eid[:13] + "…"
            # 重みは定数 or 変数参照(VarRefField)。数値変数が無ければ素の数値欄。
            wfield = VarRefField(cell, width=48, placeholder="1")
            wfield.set_names(nums)
            # =284(A案): 未チェック行は重み「0」で表示(従来「1」)。チェックON
            # で重みが0/空なら「1」に、重みに0以外を入れたら自動でチェックON。
            # チェックOFFは重みを触らず、重み0のままチェックONも許す(変数で
            # 重みを動かす用途)。未チェック行の重みは従来どおり無視される。
            wfield.set(checked[eid] if eid in checked else "0")
            ctk.CTkCheckBox(
                cell, text=label, variable=var,
                font=ctk.CTkFont(size=12), checkbox_width=16, checkbox_height=16,
                width=20, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                command=lambda v=var, w=wfield: self._on_branch_check(v, w),
            ).pack(side="left")
            wfield.pack(side="left", padx=(4, 0))
            wfield.on_change = (lambda v=var, w=wfield:
                                self._on_branch_weight(v, w))
            wfield.text_var.trace_add(
                "write", lambda *_a, v=var, w=wfield: self._on_branch_weight(v, w))
            wfield.sel_var.trace_add(
                "write", lambda *_a, v=var, w=wfield: self._on_branch_weight(v, w))
            self.next_target_vars[eid] = var
            self.next_weight_vars[eid] = wfield

        self._branch_syncing = False
        # 候補が0件のときの空きスペースを潰す(=66・ユーザー報告)。
        # **CTkFrame は中身が無いと既定で200pxの高さを要求する**ため、
        # イベントが1つだけのシナリオでは「次のイベント」と「分岐」の間に
        # 200pxの空白が空いていた(=58の CANVAS_H と同じ落とし穴)。
        if others:
            self.next_targets_frame.pack_propagate(True)
        else:
            self.next_targets_frame.pack_propagate(False)
            self.next_targets_frame.configure(height=1)

        # else(重み全0時の行き先)メニューを構築
        else_vals = [self.NEXT_ELSE_END] + others
        self.next_else_menu.configure(values=else_vals)
        self.next_else_var.set(else_to if else_to in others else self.NEXT_ELSE_END)

        self.next_visited_var.set(tr("未実行イベントのみ候補") if visited_exclude
                                  else tr("毎回すべて候補"))
        self.next_exhausted_var.set(
            tr("リセットして再び、未実行イベントのみ候補") if exh_mode == "reset"
            else tr("指定イベントへ") if exh_mode == "to"
            else tr("以後、毎回すべて候補"))
        all_ids = list(self.data["events"].keys())
        self.next_exh_to_menu.configure(values=all_ids or [""])
        self.next_exh_to_var.set(exh_to if exh_to in self.data["events"]
                                 else (all_ids[0] if all_ids else ""))
        self._update_next_ui()

    def _update_next_mode(self):
        """遷移方法(固定/分岐/選択肢/変数分岐/数値入力/なし)の表示切替。"""
        mode = self.next_mode_var.get()
        inners = {self.NEXT_FIXED: self.fixed_inner,
                  self.NEXT_CHOICE: self.choice_inner,
                  self.NEXT_COND: self.cond_inner,
                  self.NEXT_COND_WATCH: self.cond_inner,   # =168
                  self.NEXT_INPUT: self.input_inner}
        # 「なし」は入力欄そのものが無い(どのinnerも出さない)
        active = inners.get(mode, None if mode == self.NEXT_NONE
                            else self.random_inner)
        for w in (self.random_inner, self.choice_inner, self.cond_inner,
                  self.input_inner, self.fixed_inner):
            if w is not active:
                w.pack_forget()
        if active is not None:
            active.pack(fill="x")
        # =220: どのinnerも出さない「なし」では空フレームが CTk 既定の
        # 200px を要求してしまうので、明示的に潰す(中身が戻ったら
        # pack_propagate が高さを取り戻す)。
        self.next_area.configure(height=1 if active is None else 0)
        if mode == self.NEXT_FIXED:
            self.next_hint_label.configure(
                text=tr("(再生が終わると、指定したイベントへ進みます)"))
        elif mode == self.NEXT_NONE:
            self.next_hint_label.configure(
                text=tr("(遷移しません。再生はここで終わります)"))
        elif mode == self.NEXT_CHOICE:
            self.next_hint_label.configure(
                text=tr("(ボタンで選ばれた遷移先へ進みます)"))
            if not self.choice_rows:
                self._add_choice_row()
            self._update_choice_ui()
        elif mode in (self.NEXT_COND, self.NEXT_COND_WATCH):
            self.next_hint_label.configure(
                text=tr("(再生中ずっと判定し、成立した行へ進みます)")
                if mode == self.NEXT_COND_WATCH
                else tr("(変数の条件で遷移先を決めます)"))
            if not self.cond_rows:
                self._add_cond_row()
        elif mode == self.NEXT_INPUT:
            self.next_hint_label.configure(
                text=tr("(数値を入力させて変数へセットし、遷移します)"))
            self._update_input_menus()
            self._update_input_ui()
        else:
            self.next_hint_label.configure(
                text=tr("(チェックなし=再生終了、複数チェック=重み付き抽選)"))
        # =123: すごろくは選択肢/数値入力/なしの遷移では使えない(行ごと非表示)
        if mode in (self.NEXT_CHOICE, self.NEXT_INPUT, self.NEXT_NONE,
                    self.NEXT_COND_WATCH):
            self.sugoroku_row.pack_forget()
        else:
            self.sugoroku_row.pack(fill="x", pady=(2, 0))

    def _update_sugoroku_ui(self):
        """=123: すごろくチェックに応じて歩数欄の表示を切り替える。"""
        if self.sugoroku_var.get():
            self.sugoroku_steps_label.pack(side="left", padx=(10, 2))
            self.advance_field.pack(side="left")
            self.advance_field.set_names(self._numeric_var_names())
            self.sugoroku_hint.pack(side="left", padx=(8, 0))
        else:
            self.sugoroku_steps_label.pack_forget()
            self.advance_field.pack_forget()
            self.sugoroku_hint.pack_forget()

    def _collect_advance(self):
        """=123: すごろく欄からadvance値を組み立てる。(エラー, 値|None)。"""
        if not self.sugoroku_var.get():
            return None, None
        if self.next_mode_var.get() in (self.NEXT_CHOICE, self.NEXT_INPUT,
                                        self.NEXT_NONE,
                                        self.NEXT_COND_WATCH):
            return None, None   # 行ごと非表示のモード=無効(保存しない)
        raw = self.advance_field.get_raw()
        if isinstance(raw, dict):
            if not raw.get("var"):
                return tr("すごろくの歩数の変数を選択してください"), None
            return None, raw
        txt = str(raw).strip()
        try:
            v = float(txt)
            if not v.is_integer() or v < 1:
                raise ValueError
        except ValueError:
            return tr("すごろくの歩数は1以上の整数か変数で指定してください"), None
        return None, int(v)

    # ---------------- 選択肢エディタ ----------------

    def _event_id_choices(self):
        return list(self.data["events"].keys()) or [""]

    def _add_choice_row(self, label: str = "", to: str | None = None,
                        raw: dict | None = None):
        if len(self.choice_rows) >= 9:
            return
        row = ctk.CTkFrame(self.choice_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        num = ctk.CTkLabel(row, text="", width=22, font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        num.pack(side="left")
        label_var = tk.StringVar(value=label)
        label_entry = ctk.CTkEntry(row, textvariable=label_var, width=260,
                                   height=26,
                                   placeholder_text=tr("ボタンの表示テキスト"))
        label_entry.pack(side="left", padx=(2, 6))
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left")
        ids = self._event_id_choices()
        to_var = tk.StringVar(value=to if to in self.data["events"] else ids[0])
        to_menu = CTkOptionMenu(row, variable=to_var, width=150, height=26,
                                    values=ids,
                                    fg_color=("gray75", "gray28"),
                                    button_color=("gray70", "gray33"))
        to_menu.pack(side="left", padx=6)
        entry = {"frame": row, "num": num, "label_var": label_var,
                 "label_entry": label_entry, "to_var": to_var,
                 "to_menu": to_menu, "raw": dict(raw or {})}
        if self._has_vars():
            ops_btn = ctk.CTkButton(
                row, text="", width=86, height=26,
                font=ctk.CTkFont(size=11),
                fg_color="transparent", border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"), hover_color=("gray85", "gray28"),
                command=lambda e=entry: self._edit_choice_row_ops(e))
            ops_btn.pack(side="left", padx=(6, 0))
            entry["ops_btn"] = ops_btn
            self._update_choice_ops_btn(entry)
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_choice_row(e)
                      ).pack(side="left", padx=(4, 0))
        self.choice_rows.append(entry)
        self._renumber_choice_rows()

    def _update_choice_ops_btn(self, entry):
        if entry.get("ops_btn"):
            n = len(entry["raw"].get("ops") or [])
            entry["ops_btn"].configure(text=tr("変数({0})").format(n))

    def set_choice_row_ops(self, entry, result: dict):
        """選択肢1件の変数操作(ops=選択時のみ発火)を反映する。"""
        if result.get("ops"):
            entry["raw"]["ops"] = result["ops"]
        else:
            entry["raw"].pop("ops", None)
        self._update_choice_ops_btn(entry)

    def _edit_choice_row_ops(self, entry):
        idx = self.choice_rows.index(entry) + 1
        self.open_ops_dialog(
            tr("選択肢{0}").format(idx),
            [(tr("この選択肢が選ばれた時(ops)"), "ops",
              entry["raw"].get("ops") or [])],
            lambda result: self.set_choice_row_ops(entry, result))

    def set_choice_timeout_ops(self, result: dict):
        """choiceのon_timeout(タイムアウト確定時のみ発火)を反映する。"""
        self._choice_timeout_ops = result.get("on_timeout") or []
        self._update_choice_toops_btn()

    def _update_choice_toops_btn(self):
        self.choice_toops_btn.configure(
            text=tr("時間切れ時の変数操作({0})").format(
                len(self._choice_timeout_ops)))

    def _edit_choice_timeout_ops(self):
        self.open_ops_dialog(
            tr("タイムアウト時"),
            [(tr("タイムアウト確定時のみ(on_timeout)"), "on_timeout",
              self._choice_timeout_ops)],
            self.set_choice_timeout_ops)

    def _edit_event_ops(self):
        def on_ok(result):
            self.set_event_ops(result)
        self.open_ops_dialog(
            tr("イベント {0}").format(self.selected),
            [(tr("イベント開始時(on_start)"), "on_start", self._ev_ops),
             (tr("イベント終了時(on_end)"), "on_end", self._ev_end_ops)], on_ok)

    def _refresh_ev_ops_btn(self):
        self.ev_ops_btn.configure(text=tr("開始{0}・終了{1}").format(
            len(self._ev_ops), len(self._ev_end_ops)))

    def set_event_ops(self, result: dict):
        self._ev_ops = result.get("on_start") or []
        self._ev_end_ops = result.get("on_end") or []
        self._refresh_ev_ops_btn()

    def _edit_state_ops(self):
        def on_ok(result):
            self.set_state_ops(result)
        self.open_ops_dialog(
            tr("ステート {0}").format(self.sel_state),
            [(tr("ステート開始時・再入ごと(on_start)"), "on_start",
              self._st_ops),
             (tr("ステート終了時(on_end)"), "on_end", self._st_end_ops)], on_ok)

    def _refresh_state_ops_btn(self):
        self.state_ops_btn.configure(text=tr("開始{0}・終了{1}").format(
            len(self._st_ops), len(self._st_end_ops)))

    def set_state_ops(self, result: dict):
        self._st_ops = result.get("on_start") or []
        self._st_end_ops = result.get("on_end") or []
        self._refresh_state_ops_btn()

    def _delete_choice_row(self, entry):
        if len(self.choice_rows) <= 1:
            self._report("warn", tr("削除できません"), tr("選択肢は最低1つ必要です"))
            return
        self.choice_rows.remove(entry)
        entry["frame"].destroy()
        self._renumber_choice_rows()

    def _renumber_choice_rows(self):
        for i, e in enumerate(self.choice_rows):
            e["num"].configure(text=f"{i + 1}.")
        state = "normal" if len(self.choice_rows) < 9 else "disabled"
        self.choice_add_btn.configure(state=state)

    def _clear_choice_rows(self):
        for e in self.choice_rows:
            e["frame"].destroy()
        self.choice_rows = []

    def _update_choice_ui(self):
        """タイムリミット/表示タイミングの付随入力の表示切替。"""
        if self.choice_tlim_var.get() == tr("時間指定"):
            # 時間指定は秒のみ(分欄は廃止)
            self.choice_tsec_entry.pack(side="left", padx=(6, 2))
            self.choice_tsec_label.pack(side="left")
            if self._has_vars():
                self._update_choice_toops_btn()
                self.choice_toops_btn.pack(side="left", padx=(10, 0))
            else:
                self.choice_toops_btn.pack_forget()
        else:
            for w in (self.choice_tmin_entry, self.choice_tmin_label,
                      self.choice_tsec_entry, self.choice_tsec_label,
                      self.choice_toops_btn):
                w.pack_forget()
        if self.choice_dflt_var.get() == tr("指定イベントへ"):
            self.choice_dflt_to_menu.pack(side="left", padx=(6, 0))
            self.choice_dflt_to_menu.configure(values=self._event_id_choices())
            if self.choice_dflt_to_var.get() not in self.data["events"]:
                self.choice_dflt_to_var.set(self._event_id_choices()[0])
        else:
            self.choice_dflt_to_menu.pack_forget()
        if self.choice_show_var.get() == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            self.choice_ssec_entry.pack(side="left", padx=(6, 2))
            self.choice_ssec_label.pack(side="left")
        else:
            for w in (self.choice_smin_entry, self.choice_smin_label,
                      self.choice_ssec_entry, self.choice_ssec_label):
                w.pack_forget()

    def _load_choice_ui(self, nxt: dict):
        """next(choice形式)をエディタへ読み込む。"""
        self._clear_choice_rows()
        self._choice_extra = {k: v for k, v in nxt.items()
                              if k not in ("choice", "timeout", "default",
                                           "show", "on_timeout", "skip")}
        self._choice_timeout_ops = list(nxt.get("on_timeout") or [])
        # =274: 選択必須(skip=stay)
        self.choice_stay_var.set(nxt.get("skip") == "stay")
        for ent in nxt.get("choice") or []:
            self._add_choice_row(str(ent.get("label", "")), ent.get("to"),
                                 raw=ent if isinstance(ent, dict) else None)
        if not self.choice_rows:
            self._add_choice_row()
        traw = nxt.get("timeout")
        if isinstance(traw, dict):
            self.choice_tlim_var.set(tr("時間指定"))
            # 時間指定は秒のみ(minutesは廃止=読まない)
            self.choice_tsec_var.set(f"{float(traw.get('seconds', 0)):g}")
        else:
            self.choice_tlim_var.set(tr("無制限"))
        # デフォルト遷移先(旧形式timeout.toは「指定イベントへ」として読み込む)
        draw = nxt.get("default")
        legacy_to = traw.get("to") if isinstance(traw, dict) else None
        if draw == "random":
            self.choice_dflt_var.set(tr("選択肢から等確率で抽選"))
        elif isinstance(draw, dict) and draw.get("to"):
            self.choice_dflt_var.set(tr("指定イベントへ"))
            self.choice_dflt_to_var.set(draw["to"])
        elif legacy_to:
            self.choice_dflt_var.set(tr("指定イベントへ"))
            self.choice_dflt_to_var.set(legacy_to)
        else:
            self.choice_dflt_var.set(tr("先頭の選択肢へ"))
        sraw = nxt.get("show", "end")
        if sraw == "start":
            self.choice_show_var.set(tr("イベント開始時"))
        elif isinstance(sraw, dict):
            self.choice_show_var.set(tr("イベント開始から指定時間後"))
            # 時間指定は秒のみ(minutesは廃止=読まない)
            self.choice_ssec_var.set(f"{float(sraw.get('seconds', 0)):g}")
        else:
            self.choice_show_var.set(tr("イベント終了条件の達成時"))

    def _collect_choice(self, event_id: str):
        """選択肢UIからnext(choice形式)を組み立てる。(err, value)。"""
        entries = []
        for i, e in enumerate(self.choice_rows):
            label = e["label_var"].get().strip()
            to = e["to_var"].get()
            if not label:
                self._want_mark(e.get("label_entry"), "error")
                return tr("イベント {0}: 選択肢{1}の表示テキストが空です").format(event_id, i + 1), None
            if to not in self.data["events"]:
                self._want_mark(e.get("to_menu"), "error")
                return tr("イベント {0}: 選択肢{1}の遷移先が不正です").format(event_id, i + 1), None
            extra = {k: v for k, v in (e.get("raw") or {}).items()
                     if k not in ("label", "to")}
            entries.append({"label": label, "to": to, **extra})
        if not 1 <= len(entries) <= 9:
            return tr("イベント {0}: 選択肢は1〜9件にしてください").format(event_id), None
        value = {"choice": entries}
        if self.choice_tlim_var.get() == tr("時間指定"):
            # 時間指定は秒のみ(分欄は廃止)
            try:
                total = float(self.choice_tsec_var.get() or 0)
            except ValueError:
                total = 0
            if total <= 0:
                return tr("イベント {0}: タイムリミットの時間が不正です").format(event_id), None
            value["timeout"] = {"seconds": round(total, 3)}
        # デフォルト遷移先(先頭=既定なので省略。旧timeout.toはdefaultへ移行して保存)
        dflt = self.choice_dflt_var.get()
        if dflt == tr("選択肢から等確率で抽選"):
            value["default"] = "random"
        elif dflt == tr("指定イベントへ"):
            to = self.choice_dflt_to_var.get()
            if to not in self.data["events"]:
                return tr("イベント {0}: デフォルト遷移先のイベントを選択してください").format(event_id), None
            value["default"] = {"to": to}
        show = self.choice_show_var.get()
        if show == tr("イベント開始時"):
            value["show"] = "start"
        elif show == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            try:
                total = float(self.choice_ssec_var.get() or 0)
            except ValueError:
                return tr("イベント {0}: 表示タイミングの時間が不正です").format(event_id), None
            value["show"] = {"seconds": round(total, 3)}
        # "end"は既定なので省略
        # =274: 選択必須(ON=stayを明示。OFF=既定なのでキーを書かない)
        if self.choice_stay_var.get():
            value["skip"] = "stay"
        if self._choice_timeout_ops:
            value["on_timeout"] = self._choice_timeout_ops
        # 編集対象外キーを保持する
        for k, v in self._choice_extra.items():
            value.setdefault(k, v)
        return None, value

    # ---------------- 変数分岐(cond)エディタ ----------------

    def _add_cond_row(self, raw: dict | None = None):
        raw = raw or {}
        row = ctk.CTkFrame(self.cond_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=2)
        num = ctk.CTkLabel(row, text="", width=22, font=ctk.CTkFont(size=12),
                           text_color=TEXT_MUTED)
        num.pack(side="left", anchor="n", pady=4)
        conds = CondListEditor(row)
        conds.set_names(self._var_names())
        conds.load(raw.get("when") or [])
        conds.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", anchor="n", pady=4)
        ids = self._event_id_choices()
        to_var = tk.StringVar(
            value=raw.get("to") if raw.get("to") in self.data["events"]
            else ids[0])
        CTkOptionMenu(row, variable=to_var, width=140, height=26,
                          values=ids,
                          fg_color=("gray75", "gray28"),
                          button_color=("gray70", "gray33")
                          ).pack(side="left", padx=6, anchor="n", pady=2)
        entry = {"frame": row, "num": num, "conds": conds, "to_var": to_var}
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_cond_row(e)
                      ).pack(side="left", padx=(4, 0), anchor="n", pady=2)
        self.cond_rows.append(entry)
        self._renumber_cond_rows()

    def _delete_cond_row(self, entry):
        if len(self.cond_rows) <= 1:
            self._report("warn", tr("削除できません"), tr("条件行は最低1つ必要です"))
            return
        self.cond_rows.remove(entry)
        entry["frame"].destroy()
        self._renumber_cond_rows()

    def _renumber_cond_rows(self):
        for i, e in enumerate(self.cond_rows):
            e["num"].configure(text=f"{i + 1}.")

    def _clear_cond_rows(self):
        for e in self.cond_rows:
            e["frame"].destroy()
        self.cond_rows = []
        self._cond_extra = {}

    def _load_cond_ui(self, nxt: dict):
        """next(cond形式)をエディタへ読み込む。"""
        self._clear_cond_rows()
        self._cond_extra = {k: v for k, v in nxt.items()
                            if k not in ("cond", "else")}
        for row in nxt.get("cond") or []:
            if isinstance(row, dict):
                self._add_cond_row(row)
        if not self.cond_rows:
            self._add_cond_row()
        ids = [tr("再生終了")] + list(self.data["events"].keys())
        self.cond_else_menu.configure(values=ids)
        else_to = nxt.get("else")
        self.cond_else_var.set(else_to if else_to in self.data["events"]
                               else tr("再生終了"))

    def _collect_cond(self, event_id: str):
        """変数分岐UIからnext(cond形式)を組み立てる。(err, value)。"""
        string_vars = self._string_var_names()
        rows = []
        for i, e in enumerate(self.cond_rows):
            where = tr("イベント {0} 条件行{1}").format(event_id, i + 1)
            err, conds = e["conds"].collect(where, string_vars,
                                            mark=self._want_mark)
            if err:
                return err, None
            to = e["to_var"].get()
            if to not in self.data["events"]:
                return tr("{0}: 遷移先が不正です").format(where), None
            rows.append({"when": conds, "to": to})
        if not rows:
            return tr("イベント {0}: 条件行を1つ以上指定してください").format(event_id), None
        else_choice = self.cond_else_var.get()
        value = {"cond": rows,
                 "else": else_choice if else_choice in self.data["events"]
                 else None}
        for k, v in getattr(self, "_cond_extra", {}).items():
            value.setdefault(k, v)
        return None, value

    # ---------------- 数値入力(input)エディタ ----------------

    def _update_input_menus(self):
        nums = self._numeric_var_names()
        self.input_var_menu.configure(values=nums or [""])
        if self.input_var_var.get() not in nums:
            self.input_var_var.set(nums[0] if nums else "")
        ids = self._event_id_choices()
        self.input_to_menu.configure(values=ids)
        if self.input_to_var.get() not in self.data["events"]:
            self.input_to_var.set(ids[0])

    def _update_input_ui(self):
        if self.input_show_var.get() == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            self.input_ssec_entry.pack(side="left", padx=(6, 2))
            self.input_ssec_label.pack(side="left")
        else:
            for w in (self.input_smin_entry, self.input_smin_label,
                      self.input_ssec_entry, self.input_ssec_label):
                w.pack_forget()

    def _load_input_ui(self, nxt: dict | None):
        """next(input形式)をエディタへ読み込む(None=初期化)。"""
        self._input_extra = {}
        self._input_next_extra = {}
        self._update_input_menus()
        if not isinstance(nxt, dict):
            self.input_label_var.set("")
            self.input_min_var.set("")
            self.input_max_var.set("")
            self.input_show_var.set(tr("イベント終了条件の達成時"))
            self.input_stay_var.set(True)   # =288: 新規は入力必須が既定
            self._update_input_ui()
            return
        iraw = nxt.get("input") or {}
        self._input_extra = {k: v for k, v in iraw.items()
                             if k not in ("var", "label", "to", "min", "max")}
        self._input_next_extra = {k: v for k, v in nxt.items()
                                  if k not in ("input", "show", "skip")}
        self.input_stay_var.set(nxt.get("skip") == "stay")   # =288
        if iraw.get("var"):
            nums = self._numeric_var_names()
            if iraw["var"] not in nums:
                nums = nums + [iraw["var"]]
                self.input_var_menu.configure(values=nums)
            self.input_var_var.set(iraw["var"])
        self.input_label_var.set(str(iraw.get("label", "") or ""))
        if iraw.get("to") in self.data["events"]:
            self.input_to_var.set(iraw["to"])
        self.input_min_var.set(_num_disp(iraw["min"]) if "min" in iraw else "")
        self.input_max_var.set(_num_disp(iraw["max"]) if "max" in iraw else "")
        sraw = nxt.get("show", "end")
        if sraw == "start":
            self.input_show_var.set(tr("イベント開始時"))
        elif isinstance(sraw, dict):
            self.input_show_var.set(tr("イベント開始から指定時間後"))
            # 時間指定は秒のみ(minutesは廃止=読まない)
            self.input_ssec_var.set(f"{float(sraw.get('seconds', 0)):g}")
        else:
            self.input_show_var.set(tr("イベント終了条件の達成時"))
        self._update_input_ui()

    def _collect_input(self, event_id: str):
        """数値入力UIからnext(input形式)を組み立てる。(err, value)。"""
        var = self.input_var_var.get()
        if var not in self._numeric_var_names():
            self._want_mark(self.input_var_menu, "error")
            return tr("イベント {0}: 入力先の数値変数を選択してください").format(event_id), None
        to = self.input_to_var.get()
        if to not in self.data["events"]:
            self._want_mark(self.input_to_menu, "error")
            return tr("イベント {0}: 数値入力の遷移先が不正です").format(event_id), None
        iraw = {"var": var}
        label = self.input_label_var.get().strip()
        if label:
            iraw["label"] = label
        iraw["to"] = to
        mn_text = self.input_min_var.get().strip()
        mx_text = self.input_max_var.get().strip()
        try:
            mn = _parse_num_text(mn_text) if mn_text else None
            mx = _parse_num_text(mx_text) if mx_text else None
        except ValueError:
            self._want_mark(self.input_min_entry, "error")
            self._want_mark(self.input_max_entry, "error")
            return tr("イベント {0}: 入力範囲(min/max)が不正です").format(event_id), None
        if mn is not None and mx is not None and mn > mx:
            self._want_mark(self.input_min_entry, "error")
            self._want_mark(self.input_max_entry, "error")
            return tr("イベント {0}: 入力範囲はmin≦maxにしてください").format(event_id), None
        if mn is not None:
            iraw["min"] = mn
        if mx is not None:
            iraw["max"] = mx
        iraw.update(self._input_extra)
        value = {"input": iraw}
        show = self.input_show_var.get()
        if show == tr("イベント開始時"):
            value["show"] = "start"
        elif show == tr("イベント開始から指定時間後"):
            # 時間指定は秒のみ(分欄は廃止)
            try:
                total = float(self.input_ssec_var.get() or 0)
            except ValueError:
                return tr("イベント {0}: 表示タイミングの時間が不正です").format(event_id), None
            value["show"] = {"seconds": round(total, 3)}
        # =288: 入力必須(ON=stayを明示。OFF=既定なのでキーを書かない)
        if self.input_stay_var.get():
            value["skip"] = "stay"
        for k, v in self._input_next_extra.items():
            value.setdefault(k, v)
        return None, value

    def _update_next_ui(self):
        """分岐オプションの表示/非表示を更新する。

        =66: 「全候補が実行済みのとき:」は行ごと2行目(`next_exh_row`)に
        分けた。1行に並べると「指定イベントへ」の行き先メニューが枠から
        はみ出して見えなかった(ユーザー報告)。
        """
        exclude = self.next_visited_var.get() == tr("未実行イベントのみ候補")
        if exclude:
            if not self.next_exh_row.winfo_ismapped():
                self.next_exh_row.pack(fill="x", pady=(0, 2),
                                       before=self.next_else_row)
            self.next_exh_label.pack(side="left")
            self.next_exh_menu.pack(side="left", padx=(6, 0))
            if self.next_exhausted_var.get() == tr("指定イベントへ"):
                self.next_exh_to_menu.pack(side="left", padx=(6, 0))
            else:
                self.next_exh_to_menu.pack_forget()
        else:
            self.next_exh_row.pack_forget()
            self.next_exh_label.pack_forget()
            self.next_exh_menu.pack_forget()
            self.next_exh_to_menu.pack_forget()

    def _collect_next(self, event_id: str):
        """遷移UIからnext値を組み立てる。(エラーメッセージ, 値) を返す。"""
        mode = self.next_mode_var.get()
        if mode == self.NEXT_CHOICE:
            return self._collect_choice(event_id)
        if mode in (self.NEXT_COND, self.NEXT_COND_WATCH):
            # =168: 「判定式(常に監視)」も保存形式は cond。無限イベント
            # との組み合わせでのみ出る名前違いのモード
            return self._collect_cond(event_id)
        if mode == self.NEXT_INPUT:
            return self._collect_input(event_id)
        if mode == self.NEXT_NONE:
            # =166: 遷移なし(シナリオはここで終わる)
            return None, None
        if mode == self.NEXT_FIXED:
            # =166: 遷移先が1つ。保存形式は従来の文字列next(後方互換)
            to = self.next_fixed_var.get()
            if to not in self.data["events"] or to == event_id:
                self._want_mark(self.next_fixed_menu, "error")
                return tr("イベント {0}: 遷移先を選択してください").format(
                    event_id), None
            return None, to
        checked = []   # (eid, 重み: 数値 or {"var":..})
        for eid, var in self.next_target_vars.items():
            if not var.get() or eid not in self.data["events"]:
                continue
            raw = self.next_weight_vars[eid].get_raw()
            if isinstance(raw, str):
                txt = raw.strip()
                if txt == "":
                    w = 1.0
                else:
                    try:
                        w = _parse_num_text(txt)   # 0/負も許容(=出さない面)
                    except ValueError:
                        return tr("イベント {0}: 重みが不正です").format(event_id), None
            else:
                w = raw   # {"var": 変数名}
            checked.append((eid, w))
        if not checked:
            return None, None
        exclude = self.next_visited_var.get() == tr("未実行イベントのみ候補")
        # else(重み全0時の行き先)。(終了)=指定なし。
        else_choice = self.next_else_var.get()
        else_to = (else_choice if else_choice != self.NEXT_ELSE_END
                   and else_choice in self.data["events"] else None)
        # 1件・定数重み1.0・非exclude・else無し → 従来の文字列形式(後方互換)
        if (len(checked) == 1 and not exclude and else_to is None
                and checked[0][1] == 1.0):
            return None, checked[0][0]
        value = {"random": [
            eid if w == 1.0 else {"to": eid, "weight": w}
            for eid, w in checked]}
        if exclude:
            value["visited"] = "exclude"
            exh = self.next_exhausted_var.get()
            if exh == tr("リセットして再び、未実行イベントのみ候補"):
                value["when_exhausted"] = "reset"
            elif exh == tr("指定イベントへ"):
                to = self.next_exh_to_var.get()
                if to not in self.data["events"]:
                    return tr("イベント {0}: 「指定イベントへ」の行き先を選択してください").format(event_id), None
                value["when_exhausted"] = {"to": to}
        if else_to is not None:
            value["else"] = else_to
        return None, value

    def _show_states_ui(self, show: bool):
        if show:
            self.states_hint.pack_forget()
            self.states_inner.pack(fill="x", pady=(6, 0))
        else:
            self.states_inner.pack_forget()
            self.states_hint.pack(fill="x", pady=(8, 0))

    # ---- 描画中インジケータ ----

    def _begin_render(self) -> bool:
        """パネル再構築の間、パネルを隠して「描画中…」を表示する。

        再入時(_load_panel が内部で _load_state_panel を呼ぶ等)は最外だけ
        効かせる。戻り値 True の呼び出しだけが _end_render で復帰させる。
        """
        if self._rendering:
            return False
        self._rendering = True
        try:
            self.panel.pack_forget()
            self.rendering_label.pack(fill="both", expand=True)
            self.update_idletasks()   # 「描画中…」を先に描く
        except Exception:
            pass
        return True

    def _end_render(self, active: bool):
        if not active:
            return
        self._rendering = False
        try:
            self.rendering_label.pack_forget()
            self.panel.pack(fill="both", expand=True)
        except Exception:
            pass

    # ---- イベント終了条件(全ch終了 / 指定ch終了 / ステート委譲) ----

    def _evend_ch_candidates(self) -> list[str]:
        """指定チャンネル終了の対象ch候補(有効なチャンネルのみ)。"""
        enabled = [c for c in CHANNEL_IDS
                   if self.channel_sections[c].enabled_var.get()]
        return enabled or list(CHANNEL_IDS)

    def _infinite_event(self) -> bool:
        """=168: このイベントの終了条件が「無限」か(通常/ステート形式の両対応)。"""
        ev = self.data["events"].get(self.selected or "")
        if isinstance(ev, dict) and "states" in ev:
            return self.ev_end_var.get() == self.EV_END_INFINITE
        return self.evend_var.get() == self.EVEND_INFINITE

    def _update_infinite_correlation(self):
        """=168: 終了条件が「無限」のときの遷移方法・表示タイミングの相関制御。

        無限のイベントは**自分では終わらない**ので、終了時に評価される
        「固定」「分岐」は永久に発火しない。出口になりうるのは
        **なし / 選択肢 / 数値入力 / 判定式(常に監視)** だけ。
        「判定式」は通常の変数分岐と実装が別(再生中に監視する)なので
        名前も分ける。選択肢・数値入力の表示タイミングも
        「イベント終了条件の達成時」が永久に来ないため2択に絞る。
        """
        if not hasattr(self, "next_mode_menu"):
            return
        infinite = self._infinite_event()
        others = [e for e in self.data["events"] if e != self.selected]
        show_times = list(self.SHOW_TIMES)
        # =275: ステート移行に選択肢があるイベントでは、イベント側の
        # 選択肢/数値入力は「イベント終了条件の達成時」のみ(同時表示を避ける)。
        # 無限イベントではその達成時が来ないので選択肢/数値入力ごと出さない
        state_choice = self._event_has_state_choice()
        if infinite:
            modes = [self.NEXT_NONE] + ([] if state_choice
                                        else [self.NEXT_CHOICE])
            if self._has_vars():
                modes += ([] if state_choice else [self.NEXT_INPUT]) \
                    + [self.NEXT_COND_WATCH]
            show_times = show_times[1:]      # 「終了条件の達成時」は来ない
        else:
            modes = ([self.NEXT_FIXED] if others else []) \
                + [self.NEXT_BRANCH, self.NEXT_CHOICE]
            if self._has_vars():
                modes += [self.NEXT_COND, self.NEXT_INPUT]
            modes.append(self.NEXT_NONE)
            if state_choice:
                show_times = show_times[:1]  # 「終了条件の達成時」のみ
        self.next_mode_menu.configure(values=modes)
        cur = self.next_mode_var.get()
        if cur not in modes:
            # 無限⇔有限の切り替えで選べなくなったモードは読み替える
            swap = {self.NEXT_COND: self.NEXT_COND_WATCH,
                    self.NEXT_COND_WATCH: self.NEXT_COND}
            self.next_mode_var.set(swap.get(cur) if swap.get(cur) in modes
                                   else self.NEXT_NONE)
            self._update_next_mode()
        for menu, var in ((getattr(self, "choice_show_menu", None),
                           getattr(self, "choice_show_var", None)),
                          (getattr(self, "input_show_menu", None),
                           getattr(self, "input_show_var", None))):
            if menu is None or var is None:
                continue
            menu.configure(values=show_times)
            if var.get() not in show_times:
                var.set(show_times[0])
        if hasattr(self, "cond_else_row"):
            # 無限では else(どの行も成立しないとき)が成立しえないので隠す
            if infinite:
                self.cond_else_row.pack_forget()
            elif not self.cond_else_row.winfo_ismapped():
                self.cond_else_row.pack(fill="x", pady=(4, 4))
        if hasattr(self, "_update_choice_ui"):
            self._update_choice_ui()

    def _event_has_state_choice(self) -> bool:
        """=275: 選択中イベントのいずれかのステートが「選択肢でステート移行」か。

        保存前のUI状態も見る(編集中のステートのコンボが選択肢なら True)。
        """
        ev = (self.data.get("events") or {}).get(self.selected or "")
        if not isinstance(ev, dict) or "states" not in ev:
            return False
        cur = self.sel_state
        for sid, st in ev["states"].items():
            if sid == cur and hasattr(self, "trans_type_var"):
                if self.trans_type_var.get() == self.TRANS_CHOICE:
                    return True
                continue
            t = st.get("transition") if isinstance(st, dict) else None
            if isinstance(t, dict) and isinstance(t.get("when"), dict) \
                    and t["when"].get("type") == "choice":
                return True
        return False

    def _update_state_choice_correlation(self):
        """=275: ステート移行の選択肢の有無をイベント側の遷移UIへ反映する。"""
        if hasattr(self, "next_mode_menu"):
            self._update_infinite_correlation()

    def _update_evend_ui(self):
        """イベント終了条件コンボの選択に応じて対象ch欄の表示を更新する。"""
        cond = self.evend_var.get()
        if cond == self.EVEND_CHANNEL:
            cands = self._evend_ch_candidates()
            self.evend_ch_menu.configure(values=cands)
            if self.evend_ch_var.get() not in cands:
                self.evend_ch_var.set(cands[0])
            self.evend_ch_label.pack(side="left", padx=(10, 2))
            self.evend_ch_menu.pack(side="left")
        else:
            self.evend_ch_label.pack_forget()
            self.evend_ch_menu.pack_forget()
        if cond == self.EVEND_DURATION:
            nums = self._numeric_var_names()
            self.evend_secs_min.set_names(nums)
            self.evend_secs_max.set_names(nums)
            self.evend_secs_min.pack(side="left", padx=(10, 2))
            self.evend_secs_min_label.pack(side="left")
            self.evend_secs_tilde.pack(side="left", padx=(4, 2))
            self.evend_secs_max.pack(side="left", padx=2)
            self.evend_secs_label.pack(side="left")
            self.evend_secs_hint.pack(side="left", padx=(6, 0))
        else:
            self.evend_secs_min.pack_forget()
            self.evend_secs_min_label.pack_forget()
            self.evend_secs_tilde.pack_forget()
            self.evend_secs_max.pack_forget()
            self.evend_secs_label.pack_forget()
            self.evend_secs_hint.pack_forget()
        if cond == self.EVEND_COND:
            self.evend_cond.set_names(self._var_names())
            if not self.evend_cond.rows:
                self.evend_cond.add_row()
            self.evend_cond_frame.pack(fill="x", pady=(2, 0), after=self.evend_row)
        else:
            self.evend_cond_frame.pack_forget()
        self._refresh_noaudio_hints()
        self._refresh_channel_infinite()
        self._update_infinite_correlation()   # =168

    def _script_only_event(self) -> bool:
        """有効チャンネルが1つ以上あり、すべてスクリプト専用chか(=63)。"""
        enabled = [sec for sec in self.channel_sections.values()
                   if sec.enabled_var.get()]
        if not enabled:
            return False
        return all(sec.is_script_only() for sec in enabled)

    def _refresh_channel_infinite(self):
        """指定チャンネル終了の「対象外ch」に無限を許可する(対象chは有限)。

        =63: スクリプト専用chだけのイベントで終了条件が「合計時間」/「変数条件」
        のときは、**無限のみ**に絞る(有限だとスクリプトが終わった時点で
        イベントも終わってしまい、指定した秒数/条件まで持たないため)。
        """
        cond = self.evend_var.get()
        target = self.evend_ch_var.get()
        has_video = bool(self._video_channel_id())
        # =168: 「無限」もイベント自身が終わらないので全ch無限を許可する
        wait_end = cond in (self.EVEND_COND, self.EVEND_DURATION,
                            self.EVEND_INFINITE)
        # =63のスクリプト専用chの絞り込みは「無限」には要らない
        # (イベント自身が終わらないので、有限のスクリプトでも問題ない)
        script_only = (wait_end and cond != self.EVEND_INFINITE
                       and self._script_only_event())
        for cid, sec in self.channel_sections.items():
            if wait_end:
                allow = True   # 条件成立/指定秒の経過まで続けるので全ch無限を許可
            elif has_video:
                allow = True   # 動画ch: 動画終了/終了条件まで続けるので許可(=48)
            else:
                allow = (cond == self.EVEND_CHANNEL and cid != target)
            sec.set_allow_infinite(allow)
            sec.set_infinite_only(script_only and sec.enabled_var.get())

    def _on_channel_enabled(self):
        """チャンネルの有効/無効が切り替わったとき(ChannelSectionから通知)。"""
        # 指定チャンネル終了の対象ch候補(有効chのみ)を追従させる
        if getattr(self, "evend_var", None) is not None \
                and self.evend_var.get() == self.EVEND_CHANNEL:
            self._update_evend_ui()
        # 音声なし(有効チャンネル0)の相関制御を追従させる
        self._update_noaudio_correlation()
        # 動画の相関制御(終了コンボの絞り込み・device担当の締め出し)は
        # noaudio の後に上書きする必要がある(=48の順序ルール)。これが
        # 無いと動画イベントでチャンネルを有効化したときに絞り込みが
        # 巻き戻る(=50で追加)。
        if getattr(self, "channel_sections", None):
            self._update_video_correlation()
        # =63: スクリプト専用chだけになった/でなくなった場合の無限のみ絞り込み
        if getattr(self, "evend_var", None) is not None:
            self._refresh_channel_infinite()

    def _update_noaudio_correlation(self):
        """有効チャンネル0(=音声なしイベント/ステート)の相関制御。

        通常イベント: イベント終了コンボ=EVEND_ALL / EVEND_DURATION(=62の
        無音待機ノード)のみ+ヒント表示。ステート形式: ステート移行コンボ=
        「なし」/経過時間(=62)/判定式(変数宣言時)のみ+ヒント表示。デバイス担当メニューは全て「なし」固定
        (無効化)。チャンネルを1つでも有効化すると選択肢・メニューを復元する。
        動画があるときは音声なしイベントではなく動画イベントなので対象外
        (=48。イベント終了コンボは _update_video_correlation が引き継ぐ)。
        """
        ev = (self.data.get("events") or {}).get(self.selected or "")
        if not isinstance(ev, dict):
            return
        # =52: 動画もチャンネルなので、動画chが有効なら noaudio は False
        noaudio = not any(s.enabled_var.get()
                          for s in self.channel_sections.values())
        multi = "states" in ev
        # デバイス担当: 音声なしは「なし」固定(無効化)
        for ttype in DEVICE_TYPES:
            menu = self.device_menus.get(ttype)
            if noaudio:
                self.device_vars[ttype].set(tr("なし"))
                if menu is not None:
                    menu.configure(state="disabled")
            elif menu is not None:
                menu.configure(state="normal")
        if multi:
            # ステート移行コンボの選択肢を絞る/復元する
            if noaudio:
                # =62: チャンネルを見ない移行条件(経過時間・判定式)は使える
                # =275: 選択肢(選ばれるまで無音で待機)も使える
                choices = [self.TRANS_STATE_TIME, self.TRANS_CHOICE,
                           self.TRANS_NONE]
                if self._has_vars():
                    choices.append(self.TRANS_COND)
                self.trans_type_menu.configure(values=choices)
                if self.trans_type_var.get() not in choices:
                    self.trans_type_var.set(tr("ステート移行なし"))
                    self._update_trans_ui()
                self.trans_noaudio_hint.configure(
                    text=self._trans_noaudio_hint_text())
                self.trans_noaudio_hint.pack(side="left", padx=(8, 0))
            else:
                choices = list(self.TRANS_CHOICES)
                if self._has_vars():
                    choices.append(self.TRANS_COND)
                self.trans_type_menu.configure(values=choices)
                self.trans_noaudio_hint.pack_forget()
            self.evend_noaudio_hint.pack_forget()
        else:
            # イベント終了コンボの選択肢を絞る/復元する
            if noaudio:
                # =62: 「合計時間が経過した時」= 無音待機ノード(指定秒だけ待つ)
                self.evend_menu.configure(
                    values=[self.EVEND_ALL, self.EVEND_DURATION])
                if self.evend_var.get() not in (self.EVEND_ALL,
                                                self.EVEND_DURATION):
                    self.evend_var.set(self.EVEND_ALL)
                    self._update_evend_ui()
                self.evend_noaudio_hint.configure(
                    text=tr("(音声なし=指定秒だけ待機)")
                    if self.evend_var.get() == self.EVEND_DURATION
                    else tr("(音声なし=即座に次へ)"))
                self.evend_noaudio_hint.pack(side="left", padx=(8, 0))
            else:
                evend_choices = [self.EVEND_ALL, self.EVEND_CHANNEL,
                                 self.EVEND_DURATION]
                if self._has_vars():
                    evend_choices.append(self.EVEND_COND)
                evend_choices.append(self.EVEND_INFINITE)   # =168
                self.evend_menu.configure(values=evend_choices)
                self.evend_noaudio_hint.pack_forget()
            self.trans_noaudio_hint.pack_forget()

    def _refresh_noaudio_hints(self):
        """音声なしヒントの文言だけを現在の選択に合わせる(=62)。

        表示/非表示は _update_noaudio_correlation が決める。ここは
        「即座に次へ」と「指定秒だけ待機」の出し分けだけを行う
        (コンボ操作のたびに呼ぶので、相関制御を呼ぶと再帰する)。
        """
        hint = getattr(self, "evend_noaudio_hint", None)
        if hint is not None and hint.winfo_manager():
            hint.configure(
                text=tr("(音声なし=指定秒だけ待機)")
                if self.evend_var.get() == self.EVEND_DURATION
                else tr("(音声なし=即座に次へ)"))
        hint = getattr(self, "trans_noaudio_hint", None)
        if hint is not None and hint.winfo_manager():
            hint.configure(text=self._trans_noaudio_hint_text())

    def _trans_noaudio_hint_text(self) -> str:
        """音声なしステートのヒント文言(=62/=275)。"""
        t = self.trans_type_var.get()
        if t == self.TRANS_STATE_TIME:
            return tr("(音声なし=指定秒だけ待機)")
        if t == self.TRANS_CHOICE:
            return tr("(音声なし=選択されるまで待機)")
        return tr("(音声なし=即座に通過)")

    def _video_channel_id(self) -> str:
        """動画アイテムを持つチャンネルのID(無ければ "")。=52。"""
        for cid, sec in self.channel_sections.items():
            if sec.has_video():
                return cid
        return ""

    def _update_video_correlation(self):
        """動画チャンネルの有無に応じた相関制御(=48 → =52でch基準へ)。

        - 「＋動画」: 動画chが既にあるとき、他のチャンネルでは無効化
          (動画は同時に1本=合意事項7の先回り防止)。
        - シークバー追従ラジオ: 動画chがあるとき全チャンネルで非表示
          (シークバーは動画に固定・seek_channel は保存しない)。
        - 通常イベントのイベント終了コンボ: 動画chがあるときは
          「全チャンネルが終了した時」を外す(動画chは無限にもできるため)。
          「動画が終わった時」は「指定チャンネルが終了した時」へ一本化した。
        - デバイス担当メニュー(=50): 動画のトラックが担当する種別は
          「なし」固定+無効化する。種別一意性(1種別1駆動源)の検証エラーを
          保存時ではなく編集時に先回りで防ぐ(ユーザー決定 2026-07-25)。
        _update_noaudio_correlation の後に呼ぶこと(選択肢を上書きするため)。
        """
        vcid = self._video_channel_id()
        video = bool(vcid)
        for cid, sec in self.channel_sections.items():
            sec.set_seek_radio_suppressed(video)
            sec.set_video_lock(video and cid != vcid)
        self._update_video_device_correlation()
        ev = (self.data.get("events") or {}).get(self.selected or "")
        if not isinstance(ev, dict) or "states" in ev:
            return   # ステート形式のイベント終了コンボは「委譲」固定のため対象外
        # 有効ch0(=40の音声なしイベント)の絞り込みは
        # _update_noaudio_correlation が管理するため触らない
        if not any(s.enabled_var.get()
                   for s in self.channel_sections.values()):
            return
        choices = [self.EVEND_CHANNEL, self.EVEND_DURATION]
        if not video:
            choices.insert(0, self.EVEND_ALL)
        if self._has_vars():
            choices.append(self.EVEND_COND)
        choices.append(self.EVEND_INFINITE)   # =168(動画chがあっても選べる)
        self.evend_menu.configure(values=choices)
        if self.evend_var.get() not in choices:
            self.evend_var.set(choices[0])
            self._update_evend_ui()

    def _update_video_device_correlation(self):
        """動画のトラックがある種別の device 担当メニューの相関(=50→=238→=239)。

        =50〜=238 は**「なし」(=238で動画chのID)へ固定+無効化**していた。
        これは「動画トラックと同じ種別を別chの担当にするとエラー」という
        =48の制約を編集時に先回りするためのものだった。

        **=239でその制約自体を外した**(ユーザー要望: 動画に linear の
        トラックがあっても、linear の担当を L や R へ付け替えたい)ので、
        メニューは**選べる状態にする**。ここでやることは2つだけ:

        - **既定値を動画chのIDにする**(まだ「なし」のときだけ)。=238の
          「画面上『なし』だと設定漏れに見える」という指摘への対応は残す。
          **ユーザーが選んだ値は上書きしない**(この関数はチャンネルの
          有効/無効やパネルの読み込みのたびに呼ばれるため)。
        - **選択肢から「なし」を外す**。動画のトラックがある種別は
          「担当なし=動画が鳴らす」なので、**「なし」と動画chのIDは同じ意味**
          になり、2通りの表現があると紛らわしいため。

        音声なし(有効ch0)で無効化されているメニューは、そちらの相関制御を
        尊重して有効化しない。
        """
        vtypes = set()
        for sec in self.channel_sections.values():
            vtypes |= sec.video_device_types()
        vcid = self._video_channel_id()
        noaudio = not any(s.enabled_var.get()
                          for s in self.channel_sections.values())
        for ttype in DEVICE_TYPES:
            menu = self.device_menus.get(ttype)
            if menu is None:
                continue
            if ttype in vtypes and vcid:
                menu.configure(values=list(CHANNEL_IDS))
                if self.device_vars[ttype].get() == tr("なし"):
                    self.device_vars[ttype].set(vcid)
                if not noaudio:
                    menu.configure(state="normal")
            else:
                menu.configure(values=[tr("なし")] + list(CHANNEL_IDS))
                if not noaudio:
                    menu.configure(state="normal")

    def _set_device_vars(self, device, channels: dict,
                         has_video: bool = False):
        dmap = {}
        if isinstance(device, str):
            dmap = {t: device for t in DEVICE_TYPES}
        elif isinstance(device, dict):
            dmap = device
        elif device is None and not has_video:
            default_ch = "C" if "C" in channels else \
                (next(iter(channels)) if channels else "C")
            dmap = {t: default_ch for t in DEVICE_TYPES}
        # 動画イベント(=48)の device 未指定の既定は「全てなし」
        # (全種別が動画側。C既定にすると動画トラックと種別が衝突するため)
        for ttype in DEVICE_TYPES:
            self.device_vars[ttype].set(dmap.get(ttype, tr("なし")))

    def _load_panel(self, event_id: str):
        _r = self._begin_render()
        try:
            self._load_panel_body(event_id)
        finally:
            self._end_render(_r)
        # =277: 読み込み直後の正規化を履歴の変更と見なさない(下記)
        self._hist_normalize(lambda: self._apply_panel_body())
        self._hist_check()   # =277: 選択位置の記録(変更があれば1段積む)

    def _load_panel_body(self, event_id: str):
        ev = self.data["events"][event_id]
        multi = "states" in ev
        self.ev_end_locked = False
        self.event_id_var.set(event_id)
        self.states_toggle_btn.configure(
            text=tr("ステート形式を解除") if multi else tr("ステート形式に変換"))

        # 遷移UI
        self._rebuild_next_ui(event_id)
        self.start_var.set(self.data.get("start") == event_id)

        # イベント開始時の変数操作(変数宣言があるときだけ表示)
        self._ev_ops = list(ev.get("on_start") or [])
        self._ev_end_ops = list(ev.get("on_end") or [])
        if self._has_vars():
            self._refresh_ev_ops_btn()
            self.ev_ops_row.pack(fill="x", pady=(4, 0), before=self.next_area)
        else:
            self.ev_ops_row.pack_forget()

        if multi:
            self._show_states_ui(True)
            # ステート形式ではイベント終了条件は「ステートに委譲」のみ・変更不可。
            # (「委譲」はステート形式でないと選択肢に出さない = 相関制御)
            self.evend_menu.configure(values=[self.EVEND_STATE])
            self.evend_var.set(self.EVEND_STATE)
            self.evend_menu.configure(state="disabled")
            self._update_evend_ui()
            # イベント終了条件
            end = ev.get("end") or {}
            etype = end.get("type")
            self.ev_end_locked = False
            self.ev_end_field.set_names(self._numeric_var_names())
            self.ev_end_field2.set_names(self._numeric_var_names())
            self.ev_end_field2.set("")
            # 「変数条件で次へ」は変数宣言がある時だけ選択肢に出す(相関制御)
            ev_end_choices = list(self.EV_END_CHOICES)
            if self._has_vars():
                ev_end_choices.append(self.EV_END_COND)
            ev_end_choices.append(self.EV_END_STATES)
            ev_end_choices.append(self.EV_END_INFINITE)   # =168
            self.ev_end_menu.configure(values=ev_end_choices)
            self._rebuild_ev_end_state_checks(ev)
            # 時間指定は秒のみ(minutesは廃止=読まない)
            has_ref = _has_varref(end.get("count"), end.get("seconds"))
            if etype == "none":
                self.ev_end_var.set(self.EV_END_INFINITE)   # =168
            elif etype == "cond":
                self.ev_end_var.set(self.EV_END_COND)
                self.ev_end_cond.set_names(self._var_names())
                self.ev_end_cond.load(end.get("when") or [])
            elif etype == "states":
                self.ev_end_var.set(self.EV_END_STATES)
                for s in (end.get("states") or []):
                    if s in self.ev_end_state_vars:
                        self.ev_end_state_vars[s].set(True)
            elif has_ref and etype in ("plays", "transitions"):
                self.ev_end_var.set(tr("合計N回の再生で次へ") if etype == "plays"
                                    else tr("N回のステート移行で次へ"))
                self.ev_end_field.set(end.get("count"))
            elif has_ref and etype == "duration" and isinstance(end.get("seconds"), dict):
                self.ev_end_var.set(tr("合計N秒で次へ"))
                self.ev_end_field.set(end.get("seconds"))
                self.ev_end_field2.set(end.get("seconds"))   # min==max(単一値)
            elif etype == "plays":
                self.ev_end_var.set(tr("合計N回の再生で次へ"))
                self.ev_end_field.set(str(end.get("count", 1)))
            elif etype == "transitions":
                self.ev_end_var.set(tr("N回のステート移行で次へ"))
                self.ev_end_field.set(str(end.get("count", 1)))
            else:   # duration(秒のみ)。旧minutesは無視するので空欄で再入力を促す
                # =99: 範囲(min_seconds/max_seconds)は2欄へ、単一 seconds は
                # min==max として両欄に同じ値を入れる(通常イベントと同じ)
                self.ev_end_var.set(tr("合計N秒で次へ"))
                if "min_seconds" in end or "max_seconds" in end:
                    lo = end.get("min_seconds", 0)
                    hi = end.get("max_seconds", lo)
                else:
                    lo = hi = end.get("seconds")
                def _dsec(v):
                    if isinstance(v, dict):
                        return v
                    return (f"{float(v):g}"
                            if isinstance(v, (int, float)) else "5")
                self.ev_end_field.set(_dsec(lo))
                self.ev_end_field2.set(_dsec(hi))
            self._update_ev_end_ui()
            lock_state = "disabled" if self.ev_end_locked else "normal"
            self.ev_end_menu.configure(state=lock_state)
            self.ev_end_field.set_state(lock_state)
            self.ev_end_field2.set_state(lock_state)
            # 開始ステート(無効なら先頭)を選択して読み込む
            sid = ev.get("start")
            if sid not in ev["states"]:
                sid = next(iter(ev["states"]))
            self.sel_state = None
            self._load_state_panel(sid)
        else:
            self._show_states_ui(False)
            self.sel_state = None
            self._set_device_vars(
                ev.get("device"), ev.get("channels", {}),
                has_video=_raw_has_video(ev.get("channels")))
            self._load_bgm(ev.get("bgm"))   # =256
            channels = ev.get("channels", {})
            # イベント終了条件(全ch終了 / 指定ch終了 / 合計時間)。「委譲」は
            # ステート形式専用なので通常イベントの選択肢には出さない(相関制御)。
            # 「変数条件で終了」は変数宣言があるときだけ選択肢に出す(相関制御)
            evend_choices = [self.EVEND_ALL, self.EVEND_CHANNEL,
                             self.EVEND_DURATION]
            if self._has_vars():
                evend_choices.append(self.EVEND_COND)
            evend_choices.append(self.EVEND_INFINITE)   # =168
            self.evend_menu.configure(values=evend_choices, state="normal")
            self.evend_secs_min.set_names(self._numeric_var_names())
            self.evend_secs_max.set_names(self._numeric_var_names())
            end = ev.get("end")
            if isinstance(end, dict) and end.get("type") == "channel":
                self.evend_var.set(self.EVEND_CHANNEL)
                self.evend_ch_var.set(end.get("channel") or "C")
            elif isinstance(end, dict) and end.get("type") == "none":
                self.evend_var.set(self.EVEND_INFINITE)   # =168
            elif isinstance(end, dict) and end.get("type") == "cond":
                self.evend_var.set(self.EVEND_COND)
                self.evend_cond.set_names(self._var_names())
                self.evend_cond.load(end.get("when") or [])
            elif isinstance(end, dict) and end.get("type") == "duration":
                self.evend_var.set(self.EVEND_DURATION)
                def _dsec(v):
                    if isinstance(v, dict):
                        return v
                    return f"{float(v):g}" if isinstance(v, (int, float)) else ""
                if "min_seconds" in end or "max_seconds" in end:
                    lo = end.get("min_seconds", 0)
                    hi = end.get("max_seconds", lo)
                else:   # 単一 seconds
                    lo = hi = end.get("seconds", 0)
                self.evend_secs_min.set(_dsec(lo))
                self.evend_secs_max.set(_dsec(hi))
            else:
                self.evend_var.set(self.EVEND_ALL)
            target = self.evend_ch_var.get()
            cond = self.evend_var.get()
            for ch_id, sec in self.channel_sections.items():
                if cond in (self.EVEND_COND, self.EVEND_DURATION,
                            self.EVEND_INFINITE):
                    allow = True
                else:
                    allow = (cond == self.EVEND_CHANNEL and ch_id != target)
                sec.load(channels.get(ch_id), state_mode=False,
                         allow_infinite=allow)
            self.seek_var.set(ev.get("seek_channel")
                              or self._auto_seek_channel(channels))
            self._update_evend_ui()
            self._update_noaudio_correlation()
            self._update_video_correlation()

    # ---------------- ステートの読み込み・書き戻し ----------------

    def _select_state(self, state_id: str):
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev or state_id not in ev["states"]:
            return
        # =100①: 入力途中のステート名を先にコミットする(ステート図の○は
        # クリックしてもフォーカスが移らず FocusOut が発火しないため)
        self._commit_state_rename()
        if state_id not in ev["states"]:
            return   # クリックした○が直前のリネームで旧名になった(=自分自身)
        if self.sel_state and self.sel_state != state_id \
                and self.sel_state in ev["states"]:
            err = self._apply_state_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        self._load_state_panel(state_id)

    def _rebuild_target_checks(self, ev):
        for cb in self.trans_target_checks:
            cb.master.destroy()   # =73: セル(チェック+重み欄)ごと破棄
        self.trans_target_checks = []
        self.trans_target_vars = {}
        self.trans_weight_fields = {}
        nums = self._numeric_var_names()
        # =287: 横一列だとステートが増えたとき画面外に出て選べない(ユーザー
        # 報告)。TRANS_TARGET_COLS 列で折り返す(grid)。
        for i, sid in enumerate(ev["states"]):
            cell = ctk.CTkFrame(self.trans_targets_frame,
                                fg_color="transparent")
            cell.grid(row=i // self.TRANS_TARGET_COLS,
                      column=i % self.TRANS_TARGET_COLS,
                      sticky="w", padx=(0, 8), pady=1)
            var = tk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(
                cell, text=sid, variable=var,
                font=ctk.CTkFont(size=12), checkbox_width=16, checkbox_height=16,
                width=20, fg_color=ACCENT, hover_color=ACCENT_HOVER)
            cb.pack(side="left")
            # =73: 重み欄(定数 or 変数参照)。数値変数が無ければ素の数値欄
            wfield = VarRefField(cell, width=48, placeholder="1")
            wfield.set_names(nums)
            wfield.set("1")
            wfield.pack(side="left", padx=(4, 0))
            self.trans_target_vars[sid] = var
            self.trans_target_checks.append(cb)
            self.trans_weight_fields[sid] = wfield
        # =73: else(重み全0時の移行先)メニューの候補を作り直す
        self.trans_else_menu.configure(
            values=[self.TRANS_ELSE_END] + list(ev["states"].keys()))
        self.trans_else_var.set(self.TRANS_ELSE_END)
        # =77: 分岐オプションも初期状態へ(「指定ステートへ」の候補を作り直す)
        sids = list(ev["states"].keys())
        self.trans_exh_to_menu.configure(values=sids or [""])
        self.trans_exh_to_var.set("")
        self.trans_visited_var.set(tr("毎回すべて候補"))
        self.trans_exhausted_var.set(tr("以後、毎回すべて候補"))
        # =125: 移行先の決め方も初期化(変数宣言がある時だけ「変数分岐」)
        # =167: 「固定」は他ステートが居るときだけ出す(自分自身へは移行しない)
        others = [s for s in sids if s != self.sel_state]
        vals = ([self.TRANS_TOMODE_FIXED] if others else []) \
            + [self.TRANS_TOMODE_PICK]
        if self._has_vars():
            vals.append(self.TRANS_TOMODE_COND)
        self.trans_tomode_menu.configure(values=vals)
        self.trans_tomode_var.set(vals[0])
        self.trans_fixed_menu.configure(values=others or [""])
        self.trans_fixed_var.set(others[0] if others else "")
        self._clear_strans_rows()
        self.strans_else_menu.configure(
            values=[self.STRANS_ELSE_NONE] + sids)
        self.strans_else_var.set(self.STRANS_ELSE_NONE)
        self._update_trans_visited_ui()

    def _update_trans_visited_ui(self):
        """=77: 「未実行ステートのみ候補」の相関表示(=26の_update_next_uiと同じ)。

        「全候補が実行済みのとき:」の行(trans_exh_row)は exclude 選択時のみ
        else行の上へ pack し、「指定ステートへ」のときだけ行き先メニューを出す。
        """
        exclude = self.trans_visited_var.get() == tr("未実行ステートのみ候補")
        if exclude:
            if not self.trans_exh_row.winfo_ismapped():
                self.trans_exh_row.pack(fill="x", pady=(0, 4),
                                        before=self.trans_else_row)
            self.trans_exh_label.pack(side="left")
            self.trans_exh_menu.pack(side="left", padx=(6, 0))
            if self.trans_exhausted_var.get() == tr("指定ステートへ"):
                self.trans_exh_to_menu.pack(side="left", padx=(6, 0))
            else:
                self.trans_exh_to_menu.pack_forget()
        else:
            self.trans_exh_row.pack_forget()
            self.trans_exh_label.pack_forget()
            self.trans_exh_menu.pack_forget()
            self.trans_exh_to_menu.pack_forget()

    def _trans_load_targets(self, t: dict):
        """transition raw の to/else を移行先UI(チェック・重み・else)へ反映する。

        =73: 候補は文字列(重み1)または {"to","weight"}。呼び出し前に
        _rebuild_target_checks 済み(チェックOFF・重み1・else終了が初期状態)。
        """
        to = t.get("to")
        # =125: 判定式(cond)形式なら「変数分岐」モードへ切り替えて行を読む
        if isinstance(to, dict) and isinstance(to.get("cond"), list):
            self.trans_tomode_var.set(self.TRANS_TOMODE_COND)
            self._clear_strans_rows()
            for row in to["cond"]:
                if isinstance(row, dict):
                    self._add_strans_row(row)
            if not self.strans_rows:
                self._add_strans_row()
            sids = self._state_id_choices()
            self.strans_else_menu.configure(
                values=[self.STRANS_ELSE_NONE] + sids)
            et = to.get("else")
            self.strans_else_var.set(
                et if et in sids else self.STRANS_ELSE_NONE)
            self._update_trans_tomode_ui()
            return
        checked: dict[str, object] = {}
        else_to = None
        if isinstance(to, str):
            # =167: 移行先が1つ=「固定」として開く(「分岐」で1つ
            # だけ選んだ場合と保存形式が同じなので区別できない)
            if to in self.trans_fixed_menu.cget("values"):
                self.trans_tomode_var.set(self.TRANS_TOMODE_FIXED)
                self.trans_fixed_var.set(to)
                self._update_trans_tomode_ui()
                return
            checked[to] = 1.0
        elif isinstance(to, dict):
            for ent in to.get("random") or []:
                if isinstance(ent, str):
                    checked[ent] = 1.0
                elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                    checked[ent["to"]] = ent.get("weight", 1.0)
            if isinstance(to.get("else"), str):
                else_to = to["else"]
        # ここまで来たら「分岐」(または不整合な文字列)
        if self.trans_tomode_var.get() == self.TRANS_TOMODE_FIXED:
            self.trans_tomode_var.set(self.TRANS_TOMODE_PICK)
        for sid, var in self.trans_target_vars.items():
            var.set(sid in checked)
            if sid in checked:
                self.trans_weight_fields[sid].set(checked[sid])
        if else_to in self.trans_target_vars:
            self.trans_else_var.set(else_to)
        # =77: visited / when_exhausted の反映(文法は=26のnextと同じ)
        if isinstance(to, dict):
            self.trans_visited_var.set(
                tr("未実行ステートのみ候補") if to.get("visited") == "exclude"
                else tr("毎回すべて候補"))
            ex = to.get("when_exhausted", "all")
            if ex == "reset":
                self.trans_exhausted_var.set(
                    tr("リセットして再び、未実行ステートのみ候補"))
            elif isinstance(ex, dict) and isinstance(ex.get("to"), str):
                self.trans_exhausted_var.set(tr("指定ステートへ"))
                if ex["to"] in self.trans_target_vars:
                    self.trans_exh_to_var.set(ex["to"])
            else:
                self.trans_exhausted_var.set(tr("以後、毎回すべて候補"))
            self._update_trans_visited_ui()

    def _collect_trans_to(self, where: str, ev) -> tuple:
        """移行先UIから transition の to 値を組み立てる(=73)。

        戻り値: (エラーメッセージ, to値)。重みは定数 or {"var":..}、
        空欄=1、0/負も許容(=出さない候補)。従来形式との後方互換:
        1候補・重み1・else無し=文字列 / 全候補重み1・else無し=
        {"random": [ID...]}(旧来の均等抽選と同じ書式)で保存する。
        """
        # =125: 「変数分岐」モードは判定式(cond)形式で保存する
        if self.trans_tomode_var.get() == self.TRANS_TOMODE_COND:
            return self._collect_strans_cond(where, ev)
        # =167: 「固定」モードは移行先1つ=文字列で保存する
        if self.trans_tomode_var.get() == self.TRANS_TOMODE_FIXED:
            to = self.trans_fixed_var.get()
            if to not in ev["states"] or to == self.sel_state:
                self._want_mark(self.trans_fixed_menu, "error")
                return tr("{0}: 移行先を選択してください").format(where), None
            return None, to
        checked = []   # (sid, 重み: 数値 or {"var":..})
        for sid, var in self.trans_target_vars.items():
            if not var.get() or sid not in ev["states"]:
                continue
            raw = self.trans_weight_fields[sid].get_raw()
            if isinstance(raw, str):
                txt = raw.strip()
                if txt == "":
                    w = 1.0
                else:
                    try:
                        w = _parse_num_text(txt)   # 0/負も許容(=出さない候補)
                    except ValueError:
                        self._want_mark(self.trans_weight_fields[sid], "error")
                        return tr('{0}: 移行先 {1} の重みが不正です').format(where, sid), None
            else:
                w = raw   # {"var": 変数名}
            checked.append((sid, w))
        if not checked:
            for cb in self.trans_target_checks:
                self._want_mark(cb, "error")
            return tr('{0}: 移行先を1つ以上チェックしてください').format(where), None
        else_choice = self.trans_else_var.get()
        else_to = (else_choice if else_choice != self.TRANS_ELSE_END
                   and else_choice in ev["states"] else None)
        # =77: 未実行優先。exclude時は後方互換の簡易書式を使わない
        exclude = (self.trans_visited_var.get()
                   == tr("未実行ステートのみ候補"))
        all_one = all(w == 1.0 for _s, w in checked)
        if all_one and else_to is None and not exclude:
            if len(checked) == 1:
                return None, checked[0][0]
            return None, {"random": [sid for sid, _w in checked]}
        value = {"random": [
            sid if w == 1.0 else {"to": sid, "weight": w}
            for sid, w in checked]}
        if exclude:
            value["visited"] = "exclude"
            exh = self.trans_exhausted_var.get()
            if exh == tr("リセットして再び、未実行ステートのみ候補"):
                value["when_exhausted"] = "reset"
            elif exh == tr("指定ステートへ"):
                to = self.trans_exh_to_var.get()
                if to not in ev["states"]:
                    return tr('{0}: 「指定ステートへ」の行き先を選択してください').format(where), None
                value["when_exhausted"] = {"to": to}
        if else_to is not None:
            value["else"] = else_to
        return None, value

    # ---------------- 移行先の変数分岐(=125) ----------------

    def _state_id_choices(self) -> list[str]:
        ev = self.data["events"].get(self.selected or "")
        if isinstance(ev, dict) and isinstance(ev.get("states"), dict):
            return list(ev["states"].keys())
        return []

    def _add_strans_row(self, raw: dict | None = None):
        """=125: 移行先判定式の条件行(イベントの _add_cond_row と同型)。"""
        raw = raw or {}
        row = ctk.CTkFrame(self.strans_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=2)
        num = ctk.CTkLabel(row, text="", width=22,
                           font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        num.pack(side="left", anchor="n", pady=4)
        conds = CondListEditor(row)
        conds.set_names(self._var_names())
        conds.load(raw.get("when") or [])
        conds.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(row, text="→", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(side="left", anchor="n",
                                                 pady=4)
        ids = self._state_id_choices() or [""]
        to_var = tk.StringVar(
            value=raw.get("to") if raw.get("to") in ids else ids[0])
        CTkOptionMenu(row, variable=to_var, width=120, height=26,
                      values=ids,
                      fg_color=("gray75", "gray28"),
                      button_color=("gray70", "gray33")
                      ).pack(side="left", padx=6, anchor="n", pady=2)
        entry = {"frame": row, "num": num, "conds": conds, "to_var": to_var}
        ctk.CTkButton(row, text="✕", width=26, height=26,
                      fg_color="transparent", text_color="#e05a5a",
                      hover_color=("gray85", "gray28"),
                      command=lambda e=entry: self._delete_strans_row(e)
                      ).pack(side="left", padx=(4, 0), anchor="n", pady=2)
        self.strans_rows.append(entry)
        self._renumber_strans_rows()

    def _delete_strans_row(self, entry):
        if len(self.strans_rows) <= 1:
            self._report("warn", tr("削除できません"),
                         tr("条件行は最低1つ必要です"))
            return
        self.strans_rows.remove(entry)
        entry["frame"].destroy()
        self._renumber_strans_rows()

    def _renumber_strans_rows(self):
        for i, e in enumerate(self.strans_rows):
            e["num"].configure(text=f"{i + 1}.")

    def _clear_strans_rows(self):
        for e in self.strans_rows:
            e["frame"].destroy()
        self.strans_rows = []

    def _collect_strans_cond(self, where: str, ev) -> tuple:
        """=125: 変数分岐UIから transition の to(cond形式)を組み立てる。

        書式はイベント next の cond と同じ。**else は省略可**で、
        「(移行しない)」選択時は else キー自体を書かない
        (=どの行も成立しなければ移行しない。2026-08-13ユーザー決定)。
        """
        string_vars = self._string_var_names()
        rows = []
        for i, e in enumerate(self.strans_rows):
            w = tr("{0} 条件行{1}").format(where, i + 1)
            err, conds = e["conds"].collect(w, string_vars,
                                            mark=self._want_mark)
            if err:
                return err, None
            to = e["to_var"].get()
            if to not in ev["states"]:
                return tr("{0}: 移行先が不正です").format(w), None
            rows.append({"when": conds, "to": to})
        if not rows:
            return tr("{0}: 条件行を1つ以上指定してください").format(where), None
        value = {"cond": rows}
        else_choice = self.strans_else_var.get()
        if else_choice != self.STRANS_ELSE_NONE                 and else_choice in ev["states"]:
            value["else"] = else_choice
        return None, value

    def _update_trans_tomode_ui(self):
        """=125: 移行先の決め方(抽選/変数分岐)の表示切替。

        「変数分岐」×移行あり のときだけ cond箱を出し、抽選系の
        チェック・分岐オプション・重みelse行を隠す。「移行なし」時は
        従来どおり抽選レイアウト(無効化)で表示する。
        """
        active = self.trans_type_var.get() != self.TRANS_NONE
        if self.trans_type_var.get() == self.TRANS_CHOICE:
            # =275: 選択肢モードでは移行先の決め方UI一式を出さない
            self.strans_cond_box.pack_forget()
            self.trans_targets_frame.pack_forget()
            self.trans_visited_row.pack_forget()
            self.trans_exh_row.pack_forget()
            self.trans_else_row.pack_forget()
            self.trans_fixed_menu.pack_forget()
            self._update_state_choice_correlation()
            return
        self._update_state_choice_correlation()
        cond_mode = (self.trans_tomode_var.get() == self.TRANS_TOMODE_COND
                     and active)
        # =167: 「固定」は「移行なし」でもレイアウトを保つ(無効化して見せる)。
        # コンボが「固定」なのにチェック欄が出ていると分かりにくいため。
        fixed_mode = self.trans_tomode_var.get() == self.TRANS_TOMODE_FIXED
        if fixed_mode:
            # =167: 移行先が1つ。チェック・分岐オプション・else行は出さない
            self.strans_cond_box.pack_forget()
            self.trans_to_label.configure(text=tr("ステート移行先:"))
            self.trans_targets_frame.pack_forget()
            self.trans_visited_row.pack_forget()
            self.trans_exh_row.pack_forget()
            self.trans_else_row.pack_forget()
            if not self.trans_fixed_menu.winfo_ismapped():
                self.trans_fixed_menu.pack(side="left", padx=(6, 0))
            return
        self.trans_fixed_menu.pack_forget()
        if cond_mode:
            self.trans_to_label.configure(text=tr("ステート移行先(判定式):"))
            self.trans_targets_frame.pack_forget()
            self.trans_visited_row.pack_forget()
            self.trans_exh_row.pack_forget()
            self.trans_else_row.pack_forget()
            if not self.strans_rows:
                self._add_strans_row()
            if not self.strans_cond_box.winfo_ismapped():
                self.strans_cond_box.pack(fill="x", pady=(0, 2),
                                          after=self.trans_to_row)
        else:
            self.strans_cond_box.pack_forget()
            self.trans_to_label.configure(
                text=tr("ステート移行先(複数チェックで抽選):"))
            if not self.trans_targets_frame.winfo_ismapped():
                self.trans_targets_frame.pack(side="left", padx=8)
            if not self.trans_visited_row.winfo_ismapped():
                self.trans_visited_row.pack(fill="x", pady=(0, 2),
                                            after=self.trans_to_row)
            if not self.trans_else_row.winfo_ismapped():
                self.trans_else_row.pack(fill="x", pady=(0, 2),
                                         after=self.trans_visited_row)
            self._update_trans_visited_ui()

    def _refresh_ev_end_state_checks(self, ev, rename=None):
        """=285: ステートの追加/削除/コピー/リネーム後にチェック一覧を作り直す。

        従来はイベントパネルの読み込み時にしか作らず、ステートを増やしたり
        名前を変えても「S1」しか選べなかった(ユーザー報告)。チェック状態は
        引き継ぎ、rename=(old,new) なら名前を付け替える。
        """
        checked = {sid for sid, v in self.ev_end_state_vars.items() if v.get()}
        if rename:
            old, new = rename
            checked = {new if x == old else x for x in checked}
        self._rebuild_ev_end_state_checks(ev)
        for sid in checked:
            if sid in self.ev_end_state_vars:
                self.ev_end_state_vars[sid].set(True)

    def _rebuild_ev_end_state_checks(self, ev):
        """イベント終了「指定ステートが終了」の対象ステートチェックを再構築する。"""
        for cb in self.ev_end_state_checks:
            cb.destroy()
        self.ev_end_state_checks = []
        self.ev_end_state_vars = {}
        for sid in ev.get("states", {}):
            var = tk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(
                self.ev_end_states_inner, text=sid, variable=var,
                font=ctk.CTkFont(size=12), checkbox_width=16, checkbox_height=16,
                width=60, fg_color=ACCENT, hover_color=ACCENT_HOVER)
            cb.pack(side="left", padx=4)
            self.ev_end_state_vars[sid] = var
            self.ev_end_state_checks.append(cb)

    def _load_state_panel(self, state_id: str):
        _r = self._begin_render()
        try:
            self._load_state_panel_body(state_id)
        finally:
            self._end_render(_r)
        self._hist_normalize(lambda: self._apply_state_panel_body())   # =277
        self._hist_check()   # =277

    def _load_state_panel_body(self, state_id: str):
        ev = self.data["events"][self.selected]
        st = ev["states"][state_id]
        self.sel_state = state_id
        self.state_id_var.set(state_id)
        self.state_start_var.set(ev.get("start") == state_id)

        channels = st.get("channels", {})
        self._set_device_vars(st.get("device"), channels,
                              has_video=_raw_has_video(channels))
        for ch_id, sec in self.channel_sections.items():
            sec.load(channels.get(ch_id), state_mode=True)
        self.seek_var.set(st.get("seek_channel")
                          or self._auto_seek_channel(channels))
        self._load_bgm(st.get("bgm"))   # =256

        # ステート開始時の変数操作(変数宣言があるときだけ表示)
        self._st_ops = list(st.get("on_start") or [])
        self._st_end_ops = list(st.get("on_end") or [])
        if self._has_vars():
            self._refresh_state_ops_btn()
            self.state_ops_btn.pack(side="left", padx=8)
        else:
            self.state_ops_btn.pack_forget()

        # 移行条件
        self._rebuild_target_checks(ev)
        self.state_choice.set_state_ids(list(ev["states"]))   # =275
        self.state_choice.load(None)   # =275: 他形式から切り替えた時の初期状態
        nums = self._numeric_var_names()
        self.trans_min_field.set_names(nums)
        self.trans_max_field.set_names(nums)
        # 「判定式でステート移行」は変数宣言がある時だけ選択肢に出す(相関制御)
        trans_choices = list(self.TRANS_CHOICES)
        if self._has_vars():
            trans_choices.append(self.TRANS_COND)
        self.trans_type_menu.configure(values=trans_choices)
        t = st.get("transition")
        if not t:
            self.trans_type_var.set(tr("ステート移行なし"))
            self.trans_min_field.set("")
            self.trans_max_field.set("")
        elif t.get("when", {}).get("type") == "cond":
            self.trans_type_var.set(self.TRANS_COND)
            self.trans_cond.set_names(self._var_names())
            self.trans_cond.load(t.get("when", {}).get("conds") or [])
            self._trans_load_targets(t)
        elif t.get("when", {}).get("type") == "all_channels":
            self.trans_type_var.set(self.TRANS_ALL_CH)
            self._trans_load_targets(t)
        elif t.get("when", {}).get("type") == "choice":
            # =275: 選択肢でステート移行
            self.trans_type_var.set(self.TRANS_CHOICE)
            self.state_choice.set_state_ids(list(ev["states"]))
            self.state_choice.load(t)
        elif t.get("when", {}).get("type") == "state_time":
            # =62: 経過時間(このステートに入ってからの秒数)。対象chは無し
            when = t.get("when", {})
            self.trans_type_var.set(self.TRANS_STATE_TIME)

            def _load_sec(v):
                if isinstance(v, dict):
                    return v
                return f"{float(v):g}"

            if "seconds" in when:
                lo = hi = when["seconds"]
            else:
                lo = when.get("min_seconds", 0)
                hi = when.get("max_seconds", lo)
            self.trans_min_field.set(_load_sec(lo))
            self.trans_max_field.set(_load_sec(hi))
            self._trans_load_targets(t)
        elif t.get("when", {}).get("type") == "channel_end":
            self.trans_type_var.set(self.TRANS_CHANNEL_END)
            self.trans_ch_var.set(t.get("when", {}).get("channel", "C"))
            self._trans_load_targets(t)
        else:
            when = t.get("when", {})

            def _load_bound(v, count_mode):
                if isinstance(v, dict):
                    return v
                return str(int(v)) if count_mode else f"{float(v):g}"

            if when.get("type") == "channel_count":
                self.trans_type_var.set(self.TRANS_CHANNEL_COUNT)
                lo = when.get("count", when.get("min", 1))
                hi = when.get("count", when.get("max", lo))
                self.trans_min_field.set(_load_bound(lo, True))
                self.trans_max_field.set(_load_bound(hi, True))
            else:
                # =165: 廃止した channel_time は「経過時間」へ読み替えて開く。
                # 秒数と移行先を捨てずに直せるようにするため(そのまま保存し直せば
                # state_time になる)。読み替えたことは画面に出す(黙って変えない)。
                self.trans_type_var.set(self.TRANS_STATE_TIME)
                if "seconds" in when:
                    lo = hi = when["seconds"]
                else:
                    lo = when.get("min_seconds", 0)
                    hi = when.get("max_seconds", lo)
                self.trans_min_field.set(_load_bound(lo, False))
                self.trans_max_field.set(_load_bound(hi, False))
                if when.get("type") == "channel_time":
                    self._report(
                        "warn", tr("移行条件を読み替えました"),
                        tr("ステート {0}: 廃止した「チャンネル時間でステート移行」を"
                           "「経過時間でステート移行」として開きました。"
                           "保存し直すと新しい形式になります。").format(state_id))
            self.trans_ch_var.set(when.get("channel", "C"))
            self._trans_load_targets(t)
        self.trans_type_menu.configure(state="normal")
        self._update_trans_ui()
        self._update_noaudio_correlation()
        self._update_video_correlation()
        self._redraw_state_canvas()
        self._update_trans_summary()

    def _apply_state_panel(self, lenient_channel: str | None = None) -> str | None:
        """=277: 成功したら履歴チェックポイント(本体は _apply_state_panel_body)。"""
        err = self._apply_state_panel_body(lenient_channel)
        if err is None:
            self._hist_check()
        return err

    def _apply_state_panel_body(self, lenient_channel: str | None = None) -> str | None:
        """パネルの内容を選択中ステートへ書き戻す。エラーメッセージ or None。"""
        self._pending_marks = []
        ev = self.data["events"].get(self.selected or "")
        sid = self.sel_state
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return None
        where = tr('イベント {0} ステート {1}').format(self.selected, sid)

        # 検証
        # 有効チャンネル0=音声なしステート(即時通過ノード)として保存できる
        enabled = [c for c, s in self.channel_sections.items()
                   if s.enabled_var.get()]
        for ch_id, sec in self.channel_sections.items():
            if ch_id == lenient_channel:
                continue   # =129: コピーで丸ごと置換される対象chは検証免除
            err = sec.validate()
            if err:
                return f"{where}: {err}"
        # 音声なし(有効ch0)は device 自体を保存しないため検証をスキップ
        for ttype, var in (self.device_vars.items() if enabled else ()):
            ch = var.get()
            if ch != tr("なし") and ch not in enabled:
                self._want_mark(self.device_menus.get(ttype), "error")
                return (tr('{0}: デバイス担当 {1}→{2} は無効なチャンネルを指しています').format(where, ttype, ch))

        transition = None
        ttype_choice = self.trans_type_var.get()
        if ttype_choice == self.TRANS_CHOICE:
            # =275: 選択肢でステート移行(行き先はステート or イベント)
            self.state_choice.set_state_ids(list(ev["states"]))
            err, transition = self.state_choice.collect(where)
            if err:
                return err
        elif ttype_choice == self.TRANS_COND:
            err, conds = self.trans_cond.collect(
                tr('{0} 移行条件').format(where), self._string_var_names(),
                mark=self._want_mark)
            if err:
                return err
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err
            transition = {"when": {"type": "cond", "conds": conds}, "to": to}
        elif ttype_choice == self.TRANS_ALL_CH:
            # 全チャンネル終了で移行: 有効チャンネルは全て有限(無限以外)が必要
            for cid in enabled:
                if self.channel_sections[cid].end_var.get() == INFINITE_CHOICE:
                    self._want_mark(self.channel_sections[cid].end_menu, "error")
                    return tr('{0}: 全チャンネル終了で移行するには、全チャンネルに有限の終了条件(無限以外)を設定してください').format(where)
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err
            transition = {"when": {"type": "all_channels"}, "to": to}
        elif ttype_choice == self.TRANS_CHANNEL_END:
            # 指定チャンネル終了で移行: 対象chは有効かつ有限(無限以外)が必要
            ch = self.trans_ch_var.get()
            if ch not in enabled:
                self._want_mark(self.trans_ch_menu, "error")
                return tr('{0}: 移行条件の対象チャンネル {1} が有効ではありません').format(where, ch)
            tsec = self.channel_sections[ch]
            if tsec.end_var.get() == INFINITE_CHOICE:
                self._want_mark(tsec.end_menu, "error")
                return tr('{0}: 指定チャンネル終了で移行するには、対象チャンネル {1} に有限の終了条件(無限以外)を設定してください').format(where, ch)
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err
            transition = {"when": {"type": "channel_end", "channel": ch}, "to": to}
        elif ttype_choice != self.TRANS_NONE:
            # =62: 経過時間はチャンネルを見ない(音声なしステートでも使える)
            state_time = (ttype_choice == self.TRANS_STATE_TIME)
            ch = self.trans_ch_var.get()
            if not state_time and ch not in enabled:
                self._want_mark(self.trans_ch_menu, "error")
                return tr('{0}: 移行条件の対象チャンネル {1} が有効ではありません').format(where, ch)
            count_mode = (ttype_choice == self.TRANS_CHANNEL_COUNT)

            lo_var = self.trans_min_field.use_var
            hi_var = self.trans_max_field.use_var
            try:
                if lo_var:
                    lo = self.trans_min_field.get_raw()
                else:
                    lo = float(self.trans_min_field.get_text())
                if hi_var:
                    hi = self.trans_max_field.get_raw()
                elif self.trans_max_field.get_text().strip():
                    hi = float(self.trans_max_field.get_text())
                else:
                    hi = lo   # max空欄=minと同じ(minが変数なら同じ変数)
                    hi_var = lo_var
            except ValueError:
                self._want_mark(self.trans_min_field, "error")
                self._want_mark(self.trans_max_field, "error")
                return tr('{0}: 移行条件の値(min/max)が不正です').format(where)
            if not lo_var and not hi_var:
                if hi < lo:
                    lo, hi = hi, lo
                if count_mode and hi < 1:
                    self._want_mark(self.trans_min_field, "error")
                    self._want_mark(self.trans_max_field, "error")
                    return tr('{0}: 移行条件の回数は1以上にしてください').format(where)
                if not count_mode and hi <= 0:
                    self._want_mark(self.trans_min_field, "error")
                    self._want_mark(self.trans_max_field, "error")
                    return tr('{0}: 移行条件の秒数は正の数にしてください').format(where)
            err, to = self._collect_trans_to(where, ev)
            if err:
                return err

            def _out(v, is_var):
                if is_var:
                    return v
                return int(v) if count_mode else v

            if count_mode:
                when = {"type": "channel_count", "channel": ch,
                        "min": _out(lo, lo_var), "max": _out(hi, hi_var)}
            else:
                # =165: 時間系の移行条件は経過時間(state_time)だけになった
                when = {"type": "state_time",
                        "min_seconds": _out(lo, lo_var),
                        "max_seconds": _out(hi, hi_var)}
            transition = {"when": when, "to": to}

        # 書き戻し(未対応フィールドは保持)
        st = dict(ev["states"][sid])
        channels = {}
        for ch_id in CHANNEL_IDS:
            collected = self.channel_sections[ch_id].collect()
            if collected is not None:
                channels[ch_id] = collected
        if channels:
            st["channels"] = channels
            st["device"] = {t: v.get() for t, v in self.device_vars.items()
                            if v.get() != tr("なし")}
        else:
            # 音声なし/動画のみステート: channels キー省略で保存
            st.pop("channels", None)
            st.pop("device", None)
        # 動画は =52 でチャンネルのアイテムになったため、直下の video は
        # 書かない(旧形式で読んだものは _load_raw が移行済み)
        st.pop("video", None)
        # 動画chのシークバーは動画に固定=seek_channel は保存しない
        sk = None if _raw_has_video(channels) \
            else self._seek_channel_out(channels)
        if sk:
            st["seek_channel"] = sk
        else:
            st.pop("seek_channel", None)
        # =256: BGM(引き継ぐ=キー省略 / オフ / 指定)
        err, bgm = self._collect_bgm(where)
        if err:
            return err
        if bgm is not None:
            st["bgm"] = bgm
        else:
            st.pop("bgm", None)
        if transition:
            st["transition"] = transition
        else:
            st.pop("transition", None)
        if self._st_ops:
            st["on_start"] = self._st_ops
        else:
            st.pop("on_start", None)
        if self._st_end_ops:
            st["on_end"] = self._st_end_ops
        else:
            st.pop("on_end", None)
        ev["states"][sid] = st
        if self.state_start_var.get():
            ev["start"] = sid
        self._redraw_state_canvas()
        self._update_trans_summary()
        return None

    def _update_ev_end_ui(self):
        # =168: 「無限」の相関(遷移方法・表示タイミング)はどの分岐でも通す
        self._update_infinite_correlation()
        choice = self.ev_end_var.get()
        num_widgets = (self.ev_end_field, self.ev_end_unit_label,
                       self.ev_end_tilde_label, self.ev_end_field2,
                       self.ev_end_unit2_label, self.ev_end_hint_label)
        if choice == self.EV_END_COND:
            # 変数条件: 数値欄/単位/累積ヒントを隠し、判定式エディタを出す
            for w in num_widgets:
                w.pack_forget()
            self.ev_end_states_frame.pack_forget()
            self.ev_end_cond.set_names(self._var_names())
            if not self.ev_end_cond.rows:
                self.ev_end_cond.add_row()
            self.ev_end_cond_frame.pack(fill="x", pady=(0, 4),
                                        after=self.ev_end_row)
            return
        if choice == self.EV_END_STATES:
            # 指定ステート終了: 数値欄/単位/ヒント/判定式を隠し、ステート選択を出す
            for w in num_widgets:
                w.pack_forget()
            self.ev_end_cond_frame.pack_forget()
            self.ev_end_states_frame.pack(fill="x", pady=(0, 4),
                                          after=self.ev_end_row)
            return
        if choice == self.EV_END_INFINITE:
            # =168 無限: 入力欄は何も要らない
            for w in num_widgets:
                w.pack_forget()
            self.ev_end_cond_frame.pack_forget()
            self.ev_end_states_frame.pack_forget()
            return
        # 数値系: 判定式エディタを隠し、数値欄/単位/ヒントを [欄][単位][ヒント]
        # の順で確実に戻す(cond から戻る際の順序崩れを防ぐ)
        self.ev_end_cond_frame.pack_forget()
        self.ev_end_states_frame.pack_forget()
        for w in num_widgets:
            w.pack_forget()
        is_duration = (choice == tr("合計N秒で次へ"))
        self.ev_end_field.pack(side="left", padx=2)
        self.ev_end_unit_label.pack(side="left")
        if is_duration:
            # =99: [min]秒 〜 [max]秒(max空欄=min)。入場ごとに範囲から抽選
            self.ev_end_tilde_label.pack(side="left", padx=2)
            self.ev_end_field2.pack(side="left", padx=2)
            self.ev_end_unit2_label.pack(side="left")
            self.ev_end_hint_label.configure(text=tr("(累積・範囲は抽選)"))
        else:
            self.ev_end_hint_label.configure(text=tr("(累積)"))
        self.ev_end_hint_label.pack(side="left", padx=6)
        # 時間指定は秒のみ(分/秒の単位切替は廃止)
        if choice == tr("変数指定"):
            self.ev_end_unit_label.configure(text="")
        else:
            self.ev_end_unit_label.configure(
                text=tr("秒") if is_duration else tr("回"))

    # =287: ステート形式のイベント終了条件/ステート移行の既定値(ユーザー決定)
    #   合計N回の再生で次へ=5回 / 合計N秒で次へ=60〜60秒 /
    #   N回のステート移行で次へ=5回 / チャンネル回数でステート移行=5〜5回
    # コンボで選んだとき、数値欄が空なら既定値を入れる(入力済み・変数参照は
    # 触らない)。JSONからの読み込みは保存値をそのまま出す。
    EV_END_DEFAULTS = {"plays": "5", "transitions": "5", "duration": ("60", "60")}
    TRANS_COUNT_DEFAULT = ("5", "5")

    @staticmethod
    def _field_is_blank(f) -> bool:
        raw = f.get_raw()
        return isinstance(raw, str) and raw.strip() == ""

    def _on_ev_end_menu_change(self):
        choice = self.ev_end_var.get()
        if choice == tr("合計N秒で次へ"):
            if self._field_is_blank(self.ev_end_field) \
                    and self._field_is_blank(self.ev_end_field2):
                lo, hi = self.EV_END_DEFAULTS["duration"]
                self.ev_end_field.set(lo)
                self.ev_end_field2.set(hi)
        elif choice in (tr("合計N回の再生で次へ"), tr("N回のステート移行で次へ")):
            if self._field_is_blank(self.ev_end_field):
                key = "plays" if choice == tr("合計N回の再生で次へ") else "transitions"
                self.ev_end_field.set(self.EV_END_DEFAULTS[key])
        self._update_ev_end_ui()

    def _on_trans_type_menu_change(self):
        if self.trans_type_var.get() == self.TRANS_CHANNEL_COUNT:
            if self._field_is_blank(self.trans_min_field) \
                    and self._field_is_blank(self.trans_max_field):
                lo, hi = self.TRANS_COUNT_DEFAULT
                self.trans_min_field.set(lo)
                self.trans_max_field.set(hi)
        self._update_trans_ui()

    def _update_trans_ui(self):
        choice = self.trans_type_var.get()
        active = choice != self.TRANS_NONE
        is_cond = (choice == self.TRANS_COND)
        is_all_ch = (choice == self.TRANS_ALL_CH)
        is_ch_end = (choice == self.TRANS_CHANNEL_END)
        is_state_time = (choice == self.TRANS_STATE_TIME)
        is_choice = (choice == self.TRANS_CHOICE)   # =275
        # 判定式移行では ch/min/max/単位 を隠し、判定式エディタを出す
        num_widgets = (self.trans_ch_label, self.trans_ch_menu,
                       self.trans_min_field, self.trans_unit_min_label,
                       self.trans_tilde_label,
                       self.trans_max_field, self.trans_unit_label)
        # =275: 選択肢ブロックと移行先行(to_row)の出し入れ
        if is_choice:
            self.trans_to_row.pack_forget()
            if not self.state_choice.winfo_manager():
                self.state_choice.pack(fill="x", pady=(0, 2),
                                       after=self.trans_row)
            self.state_choice.update_ui()
        else:
            self.state_choice.pack_forget()
            if not self.trans_to_row.winfo_manager():
                self.trans_to_row.pack(
                    fill="x", pady=(2, 2),
                    after=(self.trans_cond_frame
                           if self.trans_cond_frame.winfo_manager()
                           else self.trans_row))
        if is_choice:
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_unit_label.configure(text="")
            self.trans_unit_min_label.configure(text="")
        elif is_cond:
            for w in num_widgets:
                w.pack_forget()
            self.trans_cond.set_names(self._var_names())
            if not self.trans_cond.rows:
                self.trans_cond.add_row()
            self.trans_cond_frame.pack(fill="x", pady=(0, 2),
                                       after=self.trans_row)
        elif is_all_ch:
            # 全チャンネル終了: 追加入力なし(移行先チェックのみ)
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
        elif is_ch_end:
            # 指定チャンネル終了: 対象chのみ(min/max/単位なし)
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_ch_label.pack(side="left", padx=(8, 2))
            self.trans_ch_menu.pack(side="left")
            self.trans_ch_menu.configure(state="normal")
        elif is_state_time:
            # =62 経過時間: 対象ch欄は無し。min〜max(秒)だけ出す
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_min_field.pack(side="left", padx=(10, 2))
            self.trans_unit_min_label.pack(side="left")
            self.trans_tilde_label.pack(side="left", padx=(4, 2))
            self.trans_max_field.pack(side="left", padx=2)
            self.trans_unit_label.pack(side="left")
            self.trans_min_field.set_state("normal")
            self.trans_max_field.set_state("normal")
            self.trans_unit_label.configure(text=tr("秒"))
            self.trans_unit_min_label.configure(text=tr("秒"))
        elif not active:
            # =67: 「ステート移行なし」でも対象ch/min/max/単位を欄ごと隠す
            # (無効化した空欄を残さない=チャンネルの終了条件と同じ方針)
            self.trans_cond_frame.pack_forget()
            for w in num_widgets:
                w.pack_forget()
            self.trans_unit_label.configure(text="")
            self.trans_unit_min_label.configure(text="")
        else:
            self.trans_cond_frame.pack_forget()
            # ch/min/〜/max/単位 を元の並びで戻す
            state = "normal" if active else "disabled"
            for w in num_widgets:
                w.pack_forget()
            self.trans_ch_label.pack(side="left", padx=(8, 2))
            self.trans_ch_menu.pack(side="left")
            self.trans_min_field.pack(side="left", padx=(10, 2))
            self.trans_unit_min_label.pack(side="left")
            self.trans_tilde_label.pack(side="left", padx=(4, 2))
            self.trans_max_field.pack(side="left", padx=2)
            self.trans_unit_label.pack(side="left")
            self.trans_ch_menu.configure(state=state)
            self.trans_min_field.set_state(state)
            self.trans_max_field.set_state(state)
            unit = "" if not active else tr("回")
            self.trans_unit_label.configure(text=unit)
            self.trans_unit_min_label.configure(text=unit)
        # 移行先チェックは cond でも有効(移行なし時のみ無効)。
        # =73: 重み欄・elseメニューもチェックと同じ扱いにする
        for cb in self.trans_target_checks:
            cb.configure(state="normal" if active else "disabled")
        for f in self.trans_weight_fields.values():
            f.set_state("normal" if active else "disabled")
        self.trans_else_menu.configure(
            state="normal" if active else "disabled")
        # =167: 「固定」の移行先メニューも「移行なし」では無効化
        self.trans_fixed_menu.configure(
            state="normal" if active else "disabled")
        # =77: 分岐オプションも「移行なし」では無効化
        st_ = "normal" if active else "disabled"
        self.trans_visited_menu.configure(state=st_)
        self.trans_exh_menu.configure(state=st_)
        self.trans_exh_to_menu.configure(state=st_)
        # =125: 移行先の決め方メニューも「移行なし」では無効化し、
        # 表示レイアウト(抽選/変数分岐)を現在のモードへ合わせる
        self.trans_tomode_menu.configure(state=st_)
        self._update_trans_tomode_ui()
        self._refresh_noaudio_hints()

    # ---------------- ステートの追加・削除 ----------------

    # =167(a): ステートの自動接続で入れる移行条件の既定。
    # 「全チャンネル終了」ではなく**経過時間**を使う(ユーザー決定 2026-08-16)。
    # 理由: ステート内チャンネルの終了条件は既定が「無限」で、その場合
    # 「全チャンネル終了」は永久に成立しないため保存エラーになる
    # (=追加した直後から保存できないシナリオになってしまう)。経過時間は
    # チャンネルを見ないので、どんなステートでも必ず有効な条件になる。
    AUTO_TRANS_SECONDS = 60

    def _link_last_state(self, ev: dict, new_sid: str):
        """末尾のステートから新しいステートへ「固定」でつなぐ(=167)。

        イベント側の「＋イベント追加」と同じ考え方(ユーザー決定 2026-08-16):
        - 「末尾」= **ステート一覧の定義順の末尾**(ステート図の並び順)。
          ステートは網目状で一意の終端が決まらないため、順序で決める。
        - **移行条件が未設定(ステート移行なし)のときだけ**つなぐ
          (既にある移行の設定は壊さない)。
        - 自動で入れる移行条件は **「経過時間でステート移行」**(既定60秒)。
          秒数は仮の値なので、ユーザーが実際の長さに合わせて直す前提。
        - **新しく足したステート自身は「移行なし」のまま**(そこから先は
          まだ決められないため)。
        """
        order = [s for s in ev.get("states", {}) if s != new_sid]
        if not order:
            return
        last = order[-1]
        if ev["states"][last].get("transition"):
            return
        ev["states"][last]["transition"] = {
            "when": {"type": "state_time",
                     "min_seconds": self.AUTO_TRANS_SECONDS,
                     "max_seconds": self.AUTO_TRANS_SECONDS},
            "to": new_sid}

    def _add_state(self):
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev:
            return
        err = self._apply_state_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        n = 1
        while f"S{n}" in ev["states"]:
            n += 1
        sid = f"S{n}"
        ev["states"][sid] = json.loads(json.dumps(DEFAULT_STATE))
        self._link_last_state(ev, sid)
        self._refresh_ev_end_state_checks(ev)   # =285
        self._load_state_panel(sid)

    def _delete_state(self):
        ev = self.data["events"].get(self.selected or "")
        sid = self.sel_state
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return
        if len(ev["states"]) <= 1:
            self._report(
                "warn", tr("削除できません"),
                tr("最後のステートは削除できません。\n"
                "ステートをやめる場合は「ステート形式を解除」を使ってください。"))
            return
        self._confirm(tr("ステート '{0}' を削除しますか？").format(sid),
                      lambda s=sid: self._perform_delete_state(s),
                      yes_text=tr("削除する"), warn=True)

    def _perform_delete_state(self, sid):
        self._clear_message()
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return
        if len(ev["states"]) <= 1:
            return
        del ev["states"][sid]
        # 参照修復: 他ステートの移行先から取り除く
        for st in ev["states"].values():
            t = st.get("transition")
            if not t:
                continue
            if isinstance(t.get("when"), dict) \
                    and t["when"].get("type") == "choice":
                # =275: 該当ステート宛ての選択肢を除去。尽きたら移行ごと削除
                ents = [e for e in t.get("choice") or []
                        if not (isinstance(e, dict) and e.get("to") == sid)]
                if not ents:
                    st.pop("transition", None)
                else:
                    t["choice"] = ents
                    draw = t.get("default")
                    if isinstance(draw, dict) and draw.get("to") == sid:
                        t.pop("default", None)
                continue
            to = t.get("to")
            if isinstance(to, str):
                if to == sid:
                    st.pop("transition", None)
            elif isinstance(to, dict):
                # =125: 判定式(cond)形式は該当行を除去。行が尽きたら移行ごと削除
                if isinstance(to.get("cond"), list):
                    rows = [r for r in to["cond"]
                            if not (isinstance(r, dict)
                                    and r.get("to") == sid)]
                    if not rows:
                        st.pop("transition", None)
                    else:
                        to["cond"] = rows
                        if to.get("else") == sid:
                            to.pop("else", None)
                    continue
                lst = [x for x in to.get("random", []) if x != sid]
                if not lst:
                    st.pop("transition", None)
                elif len(lst) == 1:
                    t["to"] = lst[0]
                else:
                    to["random"] = lst
        # イベント終了「指定ステート」から削除ステートを取り除く
        end = ev.get("end")
        if isinstance(end, dict) and end.get("type") == "states":
            end["states"] = [x for x in end.get("states", []) if x != sid]
        if ev.get("start") not in ev["states"]:
            ev["start"] = next(iter(ev["states"]))
        self.sel_state = None
        self._refresh_ev_end_state_checks(ev)   # =285
        self._load_state_panel(ev["start"])

    def _copy_state(self):
        """選択中のステートを複製する(ステート形式イベントのみ)。

        - 除外: ステートの transition(移行先)のみ。それ以外(全チャンネル/音声/
          トラック・on_start/on_end・device)はすべて複製する。
        - 接続先: =167から「＋追加」と同じく**末尾のステートから「固定」で
          つなぐ**(末尾の移行条件が未設定のときだけ。移行条件は
          「全チャンネル終了でステート移行」)。
        - コピー後はコピー先を選択する。編集内容にエラーがあればコピーしない。
        """
        ev = self.data["events"].get(self.selected or "")
        sid = self.sel_state
        if not ev or "states" not in ev or sid not in ev.get("states", {}):
            return
        err = self._apply_state_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        base = self._copy_base(sid)
        new_sid = self._unique_copy_id(ev["states"], base)
        dup = json.loads(json.dumps(ev["states"][sid]))
        dup.pop("transition", None)   # 移行先はコピーしない(ループ防止)
        ev["states"][new_sid] = dup
        self._link_last_state(ev, new_sid)
        self._refresh_ev_end_state_checks(ev)   # =285
        self._load_state_panel(new_sid)

    # ---------------- チャンネルの「他からコピー」 ----------------

    @staticmethod
    def _channel_copyable(ch: dict) -> bool:
        """コピー元にできるチャンネルか(=音声ch または スクリプト専用ch)。

        =65で「音声設定済みのアイテムが1つ以上」から拡張した(=46の既知の
        小制約の解消)。スクリプト専用チャンネル(音声を持たず tracks /
        funscript だけを持つアイテム)も候補に含める。

        **動画chは従来どおり除外**(ユーザー決定 2026-07-27): =52の「動画chは
        1つだけ」「音声/動画/スクリプトの混在禁止」があるため、コピー先が
        2つ目の動画chになる組み合わせを弾く相関制御が別途必要になる。動画は
        コピーせず「＋動画」で登録してもらう。
        """
        if not isinstance(ch, dict):
            return False
        for it in (ch.get("items") or []):
            if not isinstance(it, dict):
                continue
            if it.get("video"):
                return False    # 動画ch(混在禁止なので1件見れば確定)
            if it.get("audio") or it.get("tracks") or it.get("funscript"):
                return True
        return False

    def _channel_copy_sources(self, exclude):
        """コピー元候補 [(ev_id, st_id|None, cid), ...] を集める。

        音声ch・スクリプト専用chのみ(動画chは除外=65)。exclude=(ev,st,cid) は
        対象自身なので除外。
        """
        out = []
        for ev_id, ev in self.data["events"].items():
            if "states" in ev:
                for st_id, st in ev.get("states", {}).items():
                    for cid, ch in (st.get("channels") or {}).items():
                        if self._channel_copyable(ch) and \
                                (ev_id, st_id, cid) != exclude:
                            out.append((ev_id, st_id, cid))
            else:
                for cid, ch in (ev.get("channels") or {}).items():
                    if self._channel_copyable(ch) and \
                            (ev_id, None, cid) != exclude:
                        out.append((ev_id, None, cid))
        return out

    def _open_channel_copy(self, target_cid: str):
        """チャンネル「他からコピー」ダイアログを開く。"""
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        is_state_form = "states" in self.data["events"][ev_id]
        # 現在パネルを反映(同一イベント/ステートのソースも最新化)。エラーは中止。
        # =129: コピー対象チャンネル自身の検証エラーは免除する(コピーで
        # 丸ごと置き換わるため。他の検証は維持=不正なコミットを防ぐ)。
        err = (self._apply_state_panel(lenient_channel=target_cid)
               if is_state_form
               else self._apply_panel(lenient_channel=target_cid))
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        cur_st = self.sel_state if is_state_form else None
        sources = self._channel_copy_sources((ev_id, cur_st, target_cid))
        if not sources:
            self._report("info", tr("他からコピー"),
                         tr("コピーできるチャンネルがありません。"))
            return
        ChannelCopyDialog(
            self, sources,
            lambda e, s, c: self._apply_channel_copy(target_cid, e, s, c))

    def _apply_channel_copy(self, target_cid, src_ev, src_st, src_cid):
        """コピー元チャンネルの内容を対象チャンネルへ丸ごと取り込む。"""
        try:
            src_ev_raw = self.data["events"][src_ev]
            if src_st is None:
                src_ch = src_ev_raw["channels"][src_cid]
            else:
                src_ch = src_ev_raw["states"][src_st]["channels"][src_cid]
        except (KeyError, TypeError):
            return
        dup = json.loads(json.dumps(src_ch))
        tgt_ev = self.data["events"][self.selected]
        if "states" in tgt_ev:
            st = tgt_ev["states"][self.sel_state]
            st.setdefault("channels", {})[target_cid] = dup
            self._load_state_panel(self.sel_state)
        else:
            tgt_ev.setdefault("channels", {})[target_cid] = dup
            self._load_panel(self.selected)
        lines = [tr("チャンネル {0} に {1} の内容をコピーしました。").format(
            target_cid, src_cid)]
        # スクリプト専用chはデバイス担当が無いと何も起きない(=46の制約)。
        # コピー直後は担当が別chのままのことが多いので先に知らせる(=65)。
        if self._raw_is_script_only(dup) and not any(
                v.get() == target_cid for v in self.device_vars.values()):
            lines.append(
                tr("スクリプト専用チャンネルはデバイス担当を設定してください。"))
        self._report("info", tr("他からコピー"), lines)

    @staticmethod
    def _raw_is_script_only(ch: dict) -> bool:
        """rawチャンネルがスクリプト専用ch(アイテムが全てスクリプト)か。"""
        items = [it for it in ((ch or {}).get("items") or [])
                 if isinstance(it, dict)]
        return bool(items) and all(
            not it.get("audio") and not it.get("video")
            and (it.get("tracks") or it.get("funscript")) for it in items)

    def _toggle_states_mode(self):
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        ev = self.data["events"][ev_id]

        if "states" not in ev:
            # 通常イベント → ステート形式(現在の内容をS1に移す)
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
            st = {"channels": ev.pop("channels", {})}
            device = ev.pop("device", None)
            if device:
                st["device"] = device
            # =256: BGMは開始ステートへ移す(Q7。他ステートは既定の
            # 「引き継ぐ」なので聴感は変わらない)
            bgm = ev.pop("bgm", None)
            if bgm is not None:
                st["bgm"] = bgm
            # 動画は =52 でチャンネルの中身になったので、channels ごと
            # 移動すれば追従する(=49の video キー移動は不要になった)
            ev["states"] = {"S1": st}
            ev["start"] = "S1"
            end = ev.get("end")
            if not (isinstance(end, dict) and end.get("type") == "duration"):
                # ステート形式のイベント終了はduration/plays/transitionsのみ。
                # 引き継げない終了条件は既定の60秒にする(=287。旧300秒)。
                ev["end"] = {"type": "duration", "seconds": 60}   # =287: 300→60
        else:
            # ステート形式 → 通常イベント(ステート1つ・移行なしのみ)
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
            states = ev["states"]
            if len(states) != 1:
                self._report(
                    "error", tr("解除できません"),
                    tr("ステートが1つのときだけ解除できます。\n"
                    "先に不要なステートを削除してください。"))
                return
            sid = next(iter(states))
            st = states[sid]
            if st.get("transition"):
                self._report(
                    "error", tr("解除できません"),
                    tr("移行条件を「移行なし」にしてから解除してください。"))
                return
            ev.pop("states")
            ev.pop("start", None)
            ev.pop("end", None)   # 通常イベントの終了はチャンネル側で管理
            channels = st.get("channels", {})
            # 無限(end省略)チャンネルは通常イベントでは使えないため補正する
            for ch in channels.values():
                if "end" not in ch or (ch.get("end") or {}).get("type") == "none":
                    ch.pop("end", None)
                    if ch.get("mode") == "random":
                        ch["end"] = {"type": "duration", "seconds": 300}
                    else:
                        ch["end"] = {"type": "once"}
            ev["channels"] = channels
            if st.get("device"):
                ev["device"] = st["device"]
            # =256: ステートのBGMをイベントへ戻す
            if st.get("bgm") is not None:
                ev["bgm"] = st["bgm"]
            else:
                ev.pop("bgm", None)

        self.sel_state = None
        self._load_panel(ev_id)
        self._redraw_canvas()

    # ---------------- パネル反映(イベント単位) ----------------

    def _apply_panel(self, lenient_channel: str | None = None) -> str | None:
        """パネルの内容をモデルへ書き戻す。エラーメッセージ or None。

        =277: 成功したら履歴チェックポイント(変更があれば1段積む)。
        """
        err = self._apply_panel_body(lenient_channel)
        if err is None:
            self._hist_check()
        return err

    def _apply_panel_body(self, lenient_channel: str | None = None) -> str | None:
        """パネルの内容をモデルへ書き戻す(本体)。エラーメッセージ or None。

        lenient_channel(=129): 「他からコピー」の対象チャンネル。コピーで
        丸ごと置き換わるため、そのチャンネル自身の検証エラーは免除する
        (空のまま有効化してコピーしようとした時の「音声が1つもありません」
        等で操作が塞がらないように)。他チャンネル・イベント終了条件・
        デバイス担当の検証は従来どおり(コミットされて残るため緩めない)。
        """
        self._pending_marks = []
        ev_id = self.selected
        ev = self.data["events"][ev_id]

        if "states" in ev:
            err = self._apply_state_panel()
            if err:
                return err
            # =256: ステート形式では bgm は各ステートの持ち物(イベント直下は
            # 読み込みエラーになる書式なので、残っていれば落とす)
            ev.pop("bgm", None)
            # イベント終了条件(UIで表せない変数指定は元の値をそのまま保持)
            if not getattr(self, "ev_end_locked", False):
                choice = self.ev_end_var.get()
                if choice == self.EV_END_COND:
                    err, conds = self.ev_end_cond.collect(
                        tr('イベント {0} 終了条件').format(ev_id),
                        self._string_var_names(), mark=self._want_mark)
                    if err:
                        return err
                    ev["end"] = {"type": "cond", "when": conds}
                elif choice == self.EV_END_INFINITE:
                    ev["end"] = {"type": "none"}   # =168
                elif choice == self.EV_END_STATES:
                    sel = [s for s, v in self.ev_end_state_vars.items()
                           if v.get() and s in ev["states"]]
                    if not sel:
                        for cb in self.ev_end_state_checks:
                            self._want_mark(cb, "error")
                        return tr('イベント {0}: 終了とみなすステートを1つ以上選択してください').format(ev_id)
                    ev["end"] = {"type": "states", "states": sel}
                elif choice == tr("合計N秒で次へ"):
                    # =99: min秒〜max秒(範囲は入場ごとに抽選・max空欄=min)。
                    # 保存規則は通常イベントの evend と同じ: min==max は単一
                    # seconds、異なれば min_seconds/max_seconds
                    lo_var = self.ev_end_field.use_var
                    hi_var = self.ev_end_field2.use_var
                    try:
                        if lo_var:
                            lo = self.ev_end_field.get_raw()
                            if not lo.get("var"):
                                raise ValueError
                        else:
                            lo = float(self.ev_end_field.get_text())
                        if hi_var:
                            hi = self.ev_end_field2.get_raw()
                            if not hi.get("var"):
                                raise ValueError
                        elif self.ev_end_field2.get_text().strip():
                            hi = float(self.ev_end_field2.get_text())
                        else:
                            hi = lo      # max空欄=minと同じ
                            hi_var = lo_var
                    except ValueError:
                        self._want_mark(self.ev_end_field, "error")
                        self._want_mark(self.ev_end_field2, "error")
                        return tr('イベント {0}: イベント終了条件の値(min/max)が不正です').format(ev_id)
                    if not lo_var and not hi_var:
                        if hi < lo:
                            lo, hi = hi, lo
                        if lo < 0 or hi <= 0:
                            self._want_mark(self.ev_end_field, "error")
                            self._want_mark(self.ev_end_field2, "error")
                            return tr('イベント {0}: イベント終了条件の秒数は0以上(最大は正の数)にしてください').format(ev_id)
                    same = (lo_var == hi_var) and (
                        (lo_var and lo.get("var") == hi.get("var"))
                        or (not lo_var and lo == hi))
                    if same:
                        ev["end"] = {"type": "duration", "seconds": lo}
                    else:
                        ev["end"] = {"type": "duration",
                                     "min_seconds": lo, "max_seconds": hi}
                elif self.ev_end_field.use_var:
                    raw = self.ev_end_field.get_raw()
                    if not raw.get("var"):
                        self._want_mark(self.ev_end_field, "error")
                        return tr('イベント {0}: イベント終了条件の変数を選択してください').format(ev_id)
                    if choice == tr("合計N回の再生で次へ"):
                        ev["end"] = {"type": "plays", "count": raw}
                    else:   # N回のステート移行で次へ
                        ev["end"] = {"type": "transitions", "count": raw}
                else:
                    try:
                        v = float(self.ev_end_field.get_text())
                        if v <= 0:
                            raise ValueError
                    except ValueError:
                        self._want_mark(self.ev_end_field, "error")
                        return tr('イベント {0}: イベント終了条件の値が不正です').format(ev_id)
                    if choice == tr("合計N回の再生で次へ"):
                        ev["end"] = {"type": "plays", "count": int(v)}
                    else:   # N回のステート移行で次へ
                        ev["end"] = {"type": "transitions", "count": int(v)}
            if self._ev_ops:
                ev["on_start"] = self._ev_ops
            else:
                ev.pop("on_start", None)
            if self._ev_end_ops:
                ev["on_end"] = self._ev_end_ops
            else:
                ev.pop("on_end", None)
            err, value = self._collect_next(ev_id)
            if err:
                return err
            ev["next"] = value
            err, adv = self._collect_advance()
            if err:
                return tr('イベント {0}: {1}').format(ev_id, err)
            if adv is not None:
                ev["advance"] = adv
            else:
                ev.pop("advance", None)
            if self.start_var.get():
                self.data["start"] = ev_id
            return None

        # ---- 通常イベント ----
        # 有効チャンネル0=音声なしイベント(即時通過ノード)として保存できる
        enabled = [c for c, s in self.channel_sections.items()
                   if s.enabled_var.get()]
        for ch_id, sec in self.channel_sections.items():
            if ch_id == lenient_channel:
                continue   # =129: コピーで丸ごと置換される対象chは検証免除
            err = sec.validate()
            if err:
                return tr('イベント {0}: {1}').format(ev_id, err)
        # 音声なし(有効ch0)は device 自体を保存しないため検証をスキップ
        for ttype, var in (self.device_vars.items() if enabled else ()):
            ch = var.get()
            if ch != tr("なし") and ch not in enabled:
                self._want_mark(self.device_menus.get(ttype), "error")
                return (tr('イベント {0}: デバイス担当 {1}→{2} は無効なチャンネルを指しています').format(ev_id, ttype, ch))

        # イベント終了条件(全ch終了 / 指定ch終了 / 合計時間 / 変数条件)
        end_channel = None
        end_cond_value = None
        end_duration_value = None
        if self.evend_var.get() == self.EVEND_CHANNEL:
            end_channel = self.evend_ch_var.get()
            if end_channel not in enabled:
                self._want_mark(self.evend_ch_menu, "error")
                return (tr('イベント {0}: イベント終了条件の対象チャンネル {1} が有効ではありません').format(ev_id, end_channel))
            tsec = self.channel_sections[end_channel]
            if tsec.end_var.get() == INFINITE_CHOICE:
                self._want_mark(tsec.end_menu, "error")
                return (tr('イベント {0}: イベント終了条件の対象チャンネル {1} には有限の終了条件(無限以外)を設定してください').format(ev_id, end_channel))
        elif self.evend_var.get() == self.EVEND_DURATION:
            # 合計時間(イベント開始からの累積秒)。min秒〜max秒(0以上)。max空欄=min。
            # 各欄は定数 or 数値変数参照。入場ごとに [min,max] を一様抽選して判定。
            lo_var = self.evend_secs_min.use_var
            hi_var = self.evend_secs_max.use_var
            try:
                if lo_var:
                    lo = self.evend_secs_min.get_raw()
                    if not lo.get("var"):
                        raise ValueError
                else:
                    lo = float(self.evend_secs_min.get_text())
                if hi_var:
                    hi = self.evend_secs_max.get_raw()
                    if not hi.get("var"):
                        raise ValueError
                elif self.evend_secs_max.get_text().strip():
                    hi = float(self.evend_secs_max.get_text())
                else:
                    hi = lo          # max空欄=minと同じ(minが変数なら同じ変数)
                    hi_var = lo_var
            except ValueError:
                self._want_mark(self.evend_secs_min, "error")
                self._want_mark(self.evend_secs_max, "error")
                return tr('イベント {0}: イベント終了条件の値(min/max)が不正です').format(ev_id)
            if not lo_var and not hi_var:
                if hi < lo:
                    lo, hi = hi, lo
                if lo < 0 or hi <= 0:
                    self._want_mark(self.evend_secs_min, "error")
                    self._want_mark(self.evend_secs_max, "error")
                    return tr('イベント {0}: イベント終了条件の秒数は0以上(最大は正の数)にしてください').format(ev_id)
            # min==max は単一 seconds、異なれば範囲 min_seconds/max_seconds で保存
            same = (lo_var == hi_var) and (
                (lo_var and lo.get("var") == hi.get("var"))
                or (not lo_var and lo == hi))
            if same:
                end_duration_value = {"seconds": lo}
            else:
                end_duration_value = {"min_seconds": lo, "max_seconds": hi}
        elif self.evend_var.get() == self.EVEND_COND:
            err, conds = self.evend_cond.collect(
                tr('イベント {0} 終了条件').format(ev_id),
                self._string_var_names(), mark=self._want_mark)
            if err:
                return err
            end_cond_value = conds

        # 書き戻し
        channels = {}
        for ch_id in CHANNEL_IDS:
            collected = self.channel_sections[ch_id].collect()
            if collected is not None:
                channels[ch_id] = collected
        if channels:
            ev["channels"] = channels
            ev["device"] = {t: v.get() for t, v in self.device_vars.items()
                            if v.get() != tr("なし")}
        else:
            # 音声なし/動画のみイベント: channels キー省略で保存
            # (device も担当先が無く無意味なので省略)
            ev.pop("channels", None)
            ev.pop("device", None)
        # 動画は =52 でチャンネルのアイテムになったため、直下の video は
        # 書かない(旧形式で読んだものは _load_raw が移行済み)
        ev.pop("video", None)
        # 動画chのシークバーは動画に固定=seek_channel は保存しない
        sk = None if _raw_has_video(channels) \
            else self._seek_channel_out(channels)
        if sk:
            ev["seek_channel"] = sk
        else:
            ev.pop("seek_channel", None)
        # =256: BGM(引き継ぐ=キー省略 / オフ / 指定)
        err, bgm = self._collect_bgm(tr('イベント {0}').format(ev_id))
        if err:
            return err
        if bgm is not None:
            ev["bgm"] = bgm
        else:
            ev.pop("bgm", None)
        if self.evend_var.get() == self.EVEND_INFINITE:
            ev["end"] = {"type": "none"}   # =168
        elif end_channel is not None:
            ev["end"] = {"type": "channel", "channel": end_channel}
        elif end_duration_value is not None:
            ev["end"] = {"type": "duration", **end_duration_value}
        elif end_cond_value is not None:
            ev["end"] = {"type": "cond", "when": end_cond_value}
        else:
            ev.pop("end", None)   # 全チャンネル終了 = end キー無し
        if self._ev_ops:
            ev["on_start"] = self._ev_ops
        else:
            ev.pop("on_start", None)
        if self._ev_end_ops:
            ev["on_end"] = self._ev_end_ops
        else:
            ev.pop("on_end", None)
        err, value = self._collect_next(ev_id)
        if err:
            return err
        ev["next"] = value
        err, adv = self._collect_advance()
        if err:
            return tr('イベント {0}: {1}').format(ev_id, err)
        if adv is not None:
            ev["advance"] = adv
        else:
            ev.pop("advance", None)
        if self.start_var.get():
            self.data["start"] = ev_id
        return None

    # ================= イベント名・ステート名のリネーム =================
    #
    # 名前(ID)は参照キーそのものなので、変更時に start / next(文字列・
    # 分岐・選択肢・変数分岐・数値入力・全消化先・タイムアウト・
    # 既定)・トップレベル watch の遷移先も自動で追従させる。

    @staticmethod
    def _validate_new_id(new: str, existing, label: str) -> str | None:
        if not new:
            return tr('{0}を空にはできません').format(label)
        if new in existing:
            return tr('{0}「{1}」は既に使われています').format(label, new)
        return None

    @staticmethod
    def _rename_in_next(nxt, old: str, new: str):
        """next 値に含まれる遷移先 old を new へ置換して返す。"""
        if nxt == old:
            return new
        if not isinstance(nxt, dict):
            return nxt
        lst = []
        for ent in nxt.get("random") or []:
            if isinstance(ent, str):
                lst.append(new if ent == old else ent)
            elif isinstance(ent, dict):
                if ent.get("to") == old:
                    ent["to"] = new
                lst.append(ent)
            else:
                lst.append(ent)
        if "random" in nxt:
            nxt["random"] = lst
        for ent in nxt.get("choice") or []:
            if isinstance(ent, dict) and ent.get("to") == old:
                ent["to"] = new
        for row in nxt.get("cond") or []:
            if isinstance(row, dict) and row.get("to") == old:
                row["to"] = new
        if isinstance(nxt.get("else"), str) and nxt["else"] == old:
            nxt["else"] = new
        for key in ("when_exhausted", "timeout", "default", "input"):
            sub = nxt.get(key)
            if isinstance(sub, dict) and sub.get("to") == old:
                sub["to"] = new
        return nxt

    def _commit_event_rename(self):
        old = self.selected
        if not old or old not in self.data["events"]:
            return
        new = self.event_id_var.get().strip()
        if new == old:
            self.event_id_var.set(old)   # 前後空白の正規化
            return
        err = self._validate_new_id(
            new, set(self.data["events"]) - {old}, tr("イベント名"))
        if err:
            self._report("error", tr("リネームできません"), err)
            self.event_id_var.set(old)
            return
        # 先に現在のパネル内容を old へ反映してからキーを付け替える
        applied = self._apply_panel()
        if applied:
            self._report("error", tr("編集エラー"), applied)
            self.event_id_var.set(old)
            return
        self._rename_event(old, new)
        self._clear_message()

    def _rename_event(self, old: str, new: str):
        self.data["events"] = {(new if k == old else k): v
                               for k, v in self.data["events"].items()}
        if self.data.get("start") == old:
            self.data["start"] = new
        for ev in self.data["events"].values():
            if "next" in ev:
                ev["next"] = self._rename_in_next(ev.get("next"), old, new)
            # =275: ステート移行の選択肢のイベント宛て行き先も追従
            for st in (ev.get("states") or {}).values():
                t = st.get("transition") if isinstance(st, dict) else None
                if not isinstance(t, dict):
                    continue
                for ent in t.get("choice") or []:
                    to = ent.get("to") if isinstance(ent, dict) else None
                    if isinstance(to, dict) and to.get("event") == old:
                        to["event"] = new
                draw = t.get("default")
                if isinstance(draw, dict) and isinstance(draw.get("to"), dict) \
                        and draw["to"].get("event") == old:
                    draw["to"]["event"] = new
        wlist = self.data.get("watch")
        if isinstance(wlist, list):
            for w in wlist:
                if isinstance(w, dict) and w.get("to") == old:
                    w["to"] = new
        self.selected = new
        self._load_panel(new)
        self._redraw_canvas()

    def _commit_state_rename(self):
        ev = self.data["events"].get(self.selected or "")
        if not ev or "states" not in ev:
            return
        old = self.sel_state
        if not old or old not in ev["states"]:
            return
        new = self.state_id_var.get().strip()
        if new == old:
            self.state_id_var.set(old)
            return
        err = self._validate_new_id(
            new, set(ev["states"]) - {old}, tr("ステート名"))
        if err:
            self._report("error", tr("リネームできません"), err)
            self.state_id_var.set(old)
            return
        applied = self._apply_state_panel()
        if applied:
            self._report("error", tr("編集エラー"), applied)
            self.state_id_var.set(old)
            return
        self._rename_state(ev, old, new)
        self._clear_message()

    def _rename_state(self, ev: dict, old: str, new: str):
        ev["states"] = {(new if k == old else k): v
                        for k, v in ev["states"].items()}
        if ev.get("start") == old:
            ev["start"] = new
        for st in ev["states"].values():
            t = st.get("transition")
            if not isinstance(t, dict):
                continue
            to = t.get("to")
            if isinstance(to, str):
                if to == old:
                    t["to"] = new
            elif isinstance(to, dict):
                if "random" in to:
                    to["random"] = [new if x == old else x
                                    for x in to.get("random", [])]
                # =125: 判定式(cond)形式の行き先・elseも追従
                for row in to.get("cond") or []:
                    if isinstance(row, dict) and row.get("to") == old:
                        row["to"] = new
                if to.get("else") == old:
                    to["else"] = new
            # =275: 選択肢(choice)のステート宛て行き先・既定も追従
            for ent in t.get("choice") or []:
                if isinstance(ent, dict) and ent.get("to") == old:
                    ent["to"] = new
            draw = t.get("default")
            if isinstance(draw, dict) and draw.get("to") == old:
                draw["to"] = new
        # イベント終了「指定ステート」の参照も更新
        end = ev.get("end")
        if isinstance(end, dict) and end.get("type") == "states":
            end["states"] = [new if x == old else x
                             for x in end.get("states", [])]
        self.sel_state = None
        self._refresh_ev_end_state_checks(ev, rename=(old, new))   # =285
        self._load_state_panel(new)

    # ================= イベント追加・削除 =================

    def _add_event(self):
        # 先に現在のパネルを反映してから追加する
        # (後で反映すると、末尾イベントへのnext設定が旧値で上書きされるため)
        self._commit_pending_renames()   # =100①
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        n = 1
        while f"event{n}" in self.data["events"]:
            n += 1
        new_id = f"event{n}"
        self.data["events"][new_id] = json.loads(json.dumps(DEFAULT_EVENT))
        # チェーン末尾が未接続(next無し)のときだけつなぐ(分岐設定は壊さない)
        chain, _ = self._chain_order()
        if chain:
            if self.data["events"][chain[-1]].get("next") is None:
                self.data["events"][chain[-1]]["next"] = new_id
        else:
            self.data["start"] = new_id
        self.selected = new_id
        self._load_panel(new_id)
        self._redraw_canvas()

    @staticmethod
    def _copy_base(name: str) -> str:
        """末尾の _<数字> を除いた語幹を返す。

        コピーは完了後にコピー先を選択するため、連続コピーは「直前のコピーを
        選んだ状態での再コピー」になる。語幹ベースで連番を振ることで
        E1→E1_1→E1_2→… と伸びる(E1_1_1 のようにならない)。
        """
        stem, sep, suf = name.rpartition("_")
        if sep and stem and suf.isdigit():
            return stem
        return name

    @staticmethod
    def _unique_copy_id(existing, base: str) -> str:
        """existing に無い「base_1, base_2, …」の最初の未使用IDを返す。"""
        n = 1
        while f"{base}_{n}" in existing:
            n += 1
        return f"{base}_{n}"

    def _copy_event(self):
        """選択中のイベントを複製する。

        - 除外: イベントの next(終了時の遷移先)のみ。ループを生まないよう
          コピー先の next は空にする。それ以外(全チャンネル/音声/トラック・
          on_start/on_end・終了条件・device・ステート形式なら全ステートと内部移行)
          はすべて複製する。
        - 接続先: 「＋イベント追加」と同じくチェーン末尾(末尾の next が空のときのみ)。
        - コピー後はコピー先を選択する。編集内容にエラーがあればコピーしない。
        """
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        err = self._apply_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        base = self._copy_base(ev_id)
        new_id = self._unique_copy_id(self.data["events"], base)
        dup = json.loads(json.dumps(self.data["events"][ev_id]))
        dup["next"] = None   # 終了時の遷移先はコピーしない(ループ防止)
        dup.pop("pos", None)  # =299: 手動配置の座標は複製しない(重なるため)
        self.data["events"][new_id] = dup
        # チェーン末尾が未接続(next無し)のときだけつなぐ(分岐設定は壊さない)
        chain, _ = self._chain_order()
        if chain:
            if self.data["events"][chain[-1]].get("next") is None:
                self.data["events"][chain[-1]]["next"] = new_id
        else:
            self.data["start"] = new_id
        self.selected = new_id
        self._load_panel(new_id)
        self._redraw_canvas()

    def _open_import_dialog(self):
        """「他から取り込み」: 取り込み元jsonを選び ImportDialog を開く。"""
        err = self._apply_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        path = filedialog.askopenfilename(
            title=tr("取り込み元のシナリオを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("シナリオ"), "*.json"), (tr("すべて"), "*.*")],
            parent=self,
        )
        if not path:
            return
        _remember_dialog_dir(path)
        try:
            src = _load_raw(path)
        except Exception as e:
            self._report("error", tr("読み込みエラー"), str(e))
            return
        if not isinstance(src.get("events"), dict) or not src["events"]:
            self._report("error", tr("読み込みエラー"),
                         tr("取り込み元にイベントがありません"))
            return
        ImportDialog(self, src, os.path.dirname(os.path.abspath(path)),
                     os.path.basename(path))

    def _perform_import(self, src_data, src_dir, ids):
        """選択イベントを self.data へ取り込む(ImportDialog から呼ばれる)。

        - ID衝突は語幹ベースの連番で自動改名(_unique_copy_id)。取り込み
          イベント同士の相互参照は新IDへ追従する。
        - 取り込み対象外への遷移先は「遷移なし」へ置換(_remap_next_refs)。
          取り込み先に偶然同名のイベントがあっても誤接続しない(仕様)。
        - 素材パスは取り込み元フォルダ基準の絶対パスへ書き換えるだけにし、
          保存時の既存フロー(外部素材警告→素材コピー+相対パス化=33)に委ねる。
        - 参照している変数の宣言を取り込み元 vars からマージ(同名は
          取り込み先を優先=上書きしない)。watch(トップレベル)は対象外。
        - チェーン末尾の next が未接続なら先頭の取り込みイベントへ接続
          (_copy_event と同じ規則)。取り込み後は先頭を選択する。
        """
        events = self.data.setdefault("events", {})
        # 新IDの割り当て(元ファイルの定義順=ids順)
        id_map = {}
        taken = set(events)
        for sid in ids:
            nid = sid if sid not in taken \
                else self._unique_copy_id(taken, self._copy_base(sid))
            id_map[sid] = nid
            taken.add(nid)
        imported = {}
        for sid in ids:
            imported[id_map[sid]] = json.loads(
                json.dumps(src_data["events"][sid]))
        # 参照書き換え(集合内→新ID / 集合外→遷移なし)
        for ev in imported.values():
            self._remap_next_refs(ev, id_map)
        # 素材パスの絶対化(相対→取り込み元フォルダ基準)
        def to_abs(_kind, p):
            if not p or os.path.isabs(p):
                return p
            return os.path.normpath(os.path.join(src_dir, p))
        _map_item_paths({"events": imported}, to_abs)
        # 参照変数の宣言マージ
        added_vars = self._merge_var_decls(src_data, imported)
        events.update(imported)
        # チェーン末尾が未接続なら先頭の取り込みイベントへ接続
        first = id_map[ids[0]]
        chain, _ = self._chain_order()
        if chain:
            tail = chain[-1]
            if tail not in imported and events[tail].get("next") is None:
                events[tail]["next"] = first
        self.selected = first
        self._update_vars_btn()
        self._load_panel(first)
        self._redraw_canvas()
        renamed = [f"{s}→{n}" for s, n in id_map.items() if s != n]
        msg = tr("{0} 件のイベントを取り込みました").format(len(ids))
        if renamed:
            msg += tr("(ID重複のため改名: {0})").format(", ".join(renamed))
        if added_vars:
            msg += tr("(変数を追加: {0})").format(", ".join(added_vars))
        self._report("ok", tr("取り込み完了"), msg)

    @staticmethod
    def _remap_next_refs(ev, id_map):
        """取り込みイベントの next 参照を書き換える(in-place)。

        規則: 遷移先が id_map(取り込み集合)内→新IDへ / 集合外→除去
        (=「遷移なしへ置換」)。分岐形式ごとの除去の作法は
        _perform_delete_event の参照修復と同じ。cond は行が全て対象外なら
        else へ直行(else も対象外なら遷移なし)。random は候補が全て
        対象外なら遷移なし(random は1件以上必須のため else へは畳まない)。
        """
        def m(to):
            return id_map.get(to) if isinstance(to, str) else None

        nxt = ev.get("next")
        if isinstance(nxt, str):
            ev["next"] = m(nxt)
            return
        if not isinstance(nxt, dict):
            return
        if "choice" in nxt:
            lst = []
            for ent in nxt.get("choice") or []:
                if not isinstance(ent, dict):
                    continue
                nt = m(ent.get("to"))
                if nt:
                    ent["to"] = nt
                    lst.append(ent)
            if not lst:
                ev["next"] = None
                return
            nxt["choice"] = lst
            traw = nxt.get("timeout")
            if isinstance(traw, dict) and traw.get("to"):
                nt = m(traw["to"])
                if nt:
                    traw["to"] = nt
                else:
                    traw.pop("to", None)   # 時間は残す(遷移先は先頭候補へ)
            draw = nxt.get("default")
            if isinstance(draw, dict) and draw.get("to"):
                nt = m(draw["to"])
                if nt:
                    draw["to"] = nt
                else:
                    nxt.pop("default", None)
        elif "cond" in nxt:
            rows = []
            for r in nxt.get("cond") or []:
                if not isinstance(r, dict):
                    continue
                nt = m(r.get("to"))
                if nt:
                    r["to"] = nt
                    rows.append(r)
            els = nxt.get("else")
            els_new = m(els) if isinstance(els, str) else None
            if rows:
                nxt["cond"] = rows
                nxt["else"] = els_new
            else:
                ev["next"] = els_new   # 行が全滅→else直行(それも無ければ終了)
        elif "input" in nxt:
            iraw = nxt.get("input")
            nt = m(iraw.get("to")) if isinstance(iraw, dict) else None
            if nt:
                iraw["to"] = nt
            else:
                ev["next"] = None      # input の to は必須
        elif "random" in nxt:
            lst = []
            for ent in nxt.get("random") or []:
                if isinstance(ent, str):
                    nt = m(ent)
                    if nt:
                        lst.append(nt)
                elif isinstance(ent, dict):
                    nt = m(ent.get("to"))
                    if nt:
                        ent["to"] = nt
                        lst.append(ent)
            if not lst:
                ev["next"] = None
                return
            nxt["random"] = lst
            ex = nxt.get("when_exhausted")
            if isinstance(ex, dict) and ex.get("to"):
                nt = m(ex["to"])
                if nt:
                    ex["to"] = nt
                else:
                    nxt.pop("when_exhausted", None)
            els = nxt.get("else")
            if isinstance(els, str):
                nt = m(els)
                if nt:
                    nxt["else"] = nt
                else:
                    nxt.pop("else", None)
            # 候補1つ(素の文字列)・オプションなしなら文字列形式へ戻す
            if (len(lst) == 1 and isinstance(lst[0], str)
                    and nxt.get("visited") != "exclude"
                    and "else" not in nxt):
                ev["next"] = lst[0]

    def _merge_var_decls(self, src_data, imported):
        """取り込みイベントが参照する変数の宣言をマージし、追加名を返す。

        参照の収集は取り込みイベントdictの再帰走査:
        - 任意の {"var": 名前}(値/重み/レンジ/判定式/input の変数参照)
        - VarOp の対象名({"set"/"add"/"roll": 名前})
        取り込み元 vars に宣言がある名前だけを、取り込み先に無ければ
        deep copy で追加する(同名は取り込み先を優先=上書きしない)。
        取り込み元にも宣言が無い参照はそのまま=保存時の検証(宣言必須)で
        赤マークになる。
        """
        names = set()

        def walk(o):
            if isinstance(o, dict):
                v = o.get("var")
                if isinstance(v, str):
                    names.add(v)
                for k in ("set", "add", "roll"):
                    t = o.get(k)
                    if isinstance(t, str):
                        names.add(t)
                for vv in o.values():
                    walk(vv)
            elif isinstance(o, (list, tuple)):
                for vv in o:
                    walk(vv)

        walk(imported)
        src_vars = src_data.get("vars")
        if not isinstance(src_vars, dict):
            return []
        added = []
        for n in sorted(names):
            if n in src_vars:
                dest = self.data.setdefault("vars", {})
                if n not in dest:
                    dest[n] = json.loads(json.dumps(src_vars[n]))
                    added.append(n)
        return added

    def _delete_event(self):
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        if len(self.data["events"]) <= 1:
            self._report("warn", tr("削除できません"),
                         tr("最後のイベントは削除できません"))
            return
        self._confirm(tr("イベント '{0}' を削除しますか？").format(ev_id),
                      lambda i=ev_id: self._perform_delete_event(i),
                      yes_text=tr("削除する"), warn=True)

    def _perform_delete_event(self, ev_id):
        self._clear_message()
        if not ev_id or ev_id not in self.data["events"]:
            return
        if len(self.data["events"]) <= 1:
            return
        removed_next = self.data["events"][ev_id].get("next")
        if not isinstance(removed_next, str):
            removed_next = None
        del self.data["events"][ev_id]
        # 参照の修復: 文字列nextは付け替え、分岐nextは候補から除去
        for ev in self.data["events"].values():
            # =275: ステート移行の選択肢のイベント宛て候補を除去(尽きたら
            # 移行ごと削除・既定がその先なら既定を外す)
            for st in (ev.get("states") or {}).values():
                t = st.get("transition") if isinstance(st, dict) else None
                if not isinstance(t, dict) or not isinstance(t.get("when"), dict) \
                        or t["when"].get("type") != "choice":
                    continue
                ents = [e for e in t.get("choice") or []
                        if not (isinstance(e, dict)
                                and isinstance(e.get("to"), dict)
                                and e["to"].get("event") == ev_id)]
                if not ents:
                    st.pop("transition", None)
                    continue
                t["choice"] = ents
                draw = t.get("default")
                if isinstance(draw, dict) and isinstance(draw.get("to"), dict) \
                        and draw["to"].get("event") == ev_id:
                    t.pop("default", None)
            nxt = ev.get("next")
            if nxt == ev_id:
                ev["next"] = removed_next if removed_next != ev_id else None
            elif isinstance(nxt, dict) and "choice" in nxt:
                lst = [ent for ent in nxt.get("choice") or []
                       if ent.get("to") != ev_id]
                if not lst:
                    ev["next"] = None
                    continue
                nxt["choice"] = lst
                traw = nxt.get("timeout")
                if isinstance(traw, dict) and traw.get("to") == ev_id:
                    traw.pop("to", None)   # 時間指定は残す(遷移先は先頭候補に戻る)
                draw = nxt.get("default")
                if isinstance(draw, dict) and draw.get("to") == ev_id:
                    nxt.pop("default", None)
            elif isinstance(nxt, dict) and "cond" in nxt:
                rows = [r for r in nxt.get("cond") or []
                        if not (isinstance(r, dict) and r.get("to") == ev_id)]
                if nxt.get("else") == ev_id:
                    nxt["else"] = None
                if not rows:
                    # 条件行が無くなったらelse先へ縮退(文字列 or null)
                    ev["next"] = nxt.get("else")
                else:
                    nxt["cond"] = rows
            elif isinstance(nxt, dict) and "input" in nxt:
                iraw = nxt.get("input")
                if isinstance(iraw, dict) and iraw.get("to") == ev_id:
                    # 遷移先が消えた数値入力はnextごと除去(toは必須のため)
                    ev["next"] = None
            elif isinstance(nxt, dict):
                lst = []
                for ent in nxt.get("random") or []:
                    to = ent if isinstance(ent, str) else ent.get("to")
                    if to != ev_id:
                        lst.append(ent)
                if not lst:
                    ev["next"] = None
                    continue
                nxt["random"] = lst
                ex = nxt.get("when_exhausted")
                if isinstance(ex, dict) and ex.get("to") == ev_id:
                    nxt.pop("when_exhausted", None)
                # 候補1つ・オプションなしなら文字列へ戻す
                if (len(lst) == 1 and isinstance(lst[0], str)
                        and nxt.get("visited") != "exclude"):
                    ev["next"] = lst[0]
        if self.data.get("start") == ev_id:
            self.data["start"] = removed_next if removed_next in self.data["events"] \
                else next(iter(self.data["events"]))
        # トップレベルwatchの修復: 遷移先が消えたトリガーを除去
        if isinstance(self.data.get("watch"), list):
            kept = [w for w in self.data["watch"]
                    if not (isinstance(w, dict) and w.get("to") == ev_id)]
            if kept:
                self.data["watch"] = kept
            else:
                self.data.pop("watch", None)
        # 監視(watch)の修復: 削除イベントを遷移先とする監視を取り除く
        wlist = self.data.get("watch")
        if isinstance(wlist, list):
            wlist = [w for w in wlist
                     if not (isinstance(w, dict) and w.get("to") == ev_id)]
            if wlist:
                self.data["watch"] = wlist
            else:
                self.data.pop("watch", None)
        self.selected = None
        self.sel_state = None
        self._select(self.data["start"])

    # ================= 保存 =================

    def _save(self):
        if not self.path:
            return self._save_as()
        if self._prepare_for_save() is not None:
            return
        self._save_with_path_check(self.path)

    def _save_as(self):
        # 先に検証する。エラーがあれば保存ダイアログを出さずにその場で表示し、
        # 確実に保存できるときだけダイアログを表示する。
        if self._prepare_for_save() is not None:
            return
        path = filedialog.asksaveasfilename(
            title=tr("シナリオファイルを保存"),
            initialdir=_dialog_initialdir(self.base_dir),
            defaultextension=".json",
            filetypes=[(tr("シナリオファイル"), "*.json")], parent=self)
        if not path:
            return
        _remember_dialog_dir(path)
        # 保存先が別フォルダでも、書き出し時に全パスを保存先基準へ付け替える
        # (保存先の内=相対 / 外=絶対)。外部素材(=絶対パス)が残る場合は
        # _save_with_path_check が警告を出し、ユーザーが扱いを選ぶ。
        self._save_with_path_check(path)

    # ---- 外部素材(絶対パス)の警告と相対化(コピー) ----

    def _save_with_path_check(self, path: str):
        """保存前に、絶対パスとして書き出される素材の有無を確認する。

        無ければそのまま保存(従来どおり)。有れば警告を表示し、
        「絶対パスのまま保存 / 相対パスに書き換えて保存(素材コピー) /
        保存しない」の3択をユーザーへ委ねる。
        """
        save_dir = os.path.dirname(os.path.abspath(path))
        externals = self._external_paths_for_save(save_dir)
        if not externals:
            self._do_save(path)
            return
        lines = [tr("以下のファイルは保存先フォルダの外にあるため、"
                    "絶対パスで保存されます。このシナリオを配布すると"
                    "他の環境では再生できません。")]
        lines += [p for _k, p in externals]
        self._render_message(
            "warn", tr("外部の素材ファイルがあります"), lines,
            buttons=[
                (tr("はい(相対パスに書き換えて保存する)"),
                 lambda: self._confirm_flatten(path), "primary"),
                (tr("はい(絶対パスのまま保存する)"),
                 lambda: (self._clear_message(), self._do_save(path)),
                 "ghost"),
                (tr("いいえ(保存しない)"), self._clear_message, "ghost"),
            ])

    @staticmethod
    def _externals_size_text(externals) -> str:
        """コピー対象の合計サイズ(と動画の内訳)の説明文を作る(=50)。

        動画はサイズが大きいため、コピー前に総量を提示する(ユーザー決定
        2026-07-25)。サイズが取得できないファイルは 0 として扱う。
        """
        total = video = 0
        for kind, p in externals:
            try:
                n = os.path.getsize(p)
            except OSError:
                n = 0
            total += n
            if kind == "video":
                video += n
        if video:
            return tr("コピーするファイルの合計サイズ: {0}"
                      "(うち動画 {1})。動画は容量が大きいため、"
                      "保存先の空き容量にご注意ください。").format(
                _format_bytes(total), _format_bytes(video))
        return tr("コピーするファイルの合計サイズ: {0}").format(
            _format_bytes(total))

    def _confirm_flatten(self, path: str):
        """相対化(素材コピー)の内容を説明し、最終確認する。"""
        save_dir = os.path.dirname(os.path.abspath(path))
        externals = self._external_paths_for_save(save_dir)
        if not externals:            # 警告表示中に編集で解消された場合
            self._clear_message()
            self._do_save(path)
            return
        lines = [
            tr("相対パスに書き換えて保存します。次の処理を行います:"),
            tr("外部の素材ファイル{0}個を、シナリオファイル(.json)と同じ"
               "フォルダへコピーします。").format(len(externals)),
            tr("音声(wav)・動画には、同じフォルダで自動紐づけされる"
               "funscript/CSVがあれば、それも一緒にコピーします。"),
            tr("シナリオ内の参照パスを相対パスに書き換えます"
               "(元のファイルは削除しません)。"),
            tr("コピー先に同名で内容の異なるファイルが既にある場合は、"
               "何もせず中止します(内容が同一ならコピー不要として"
               "スキップします)。"),
        ]
        # 動画は容量が大きい(=50)。コピー量を事前に見せて事故を防ぐ。
        lines.append(self._externals_size_text(externals))
        self._render_message(
            "confirm", tr("素材をコピーして相対パスで保存"), lines,
            buttons=[
                (tr("はい(コピーして保存する)"),
                 lambda: self._flatten_and_save(path), "primary"),
                (tr("いいえ(保存しない)"), self._clear_message, "ghost"),
            ])

    def _flatten_and_save(self, path: str):
        """外部素材を保存先フォルダへコピーし、参照を相対化して保存する。"""
        self._clear_message()
        # 警告表示中の編集に備え、パネルを反映し直してから最新の状態で処理
        err = self._apply_panel() if self.selected else None
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        save_dir = os.path.dirname(os.path.abspath(path))
        externals = self._external_paths_for_save(save_dir)
        if not externals:
            self._do_save(path)
            return

        # 1) コピー計画: 参照されている外部素材 + wavの自動紐づけ同伴ファイル
        plan: dict[str, str] = {}    # normcase(src) -> src絶対パス

        def add(src):
            src = os.path.normpath(src)
            plan.setdefault(os.path.normcase(src), src)

        for kind, src in externals:
            add(src)
            if kind in ("audio", "video"):
                # 外部wav/動画と同フォルダの自動紐づけfunscript/CSVも同伴コピー
                # (コピー後も同じ命名ルールで保存先フォルダから解決される)
                # 動画も命名ルールで自動紐づけする(=48)ため対象に含める(=50)
                for _t, fs_abs in auto_bind_tracks(src):
                    add(fs_abs)

        # 2) 衝突チェック(1つでも衝突があれば何もコピーせず中止)
        dest_names: dict[str, str] = {}   # normcase(basename) -> src
        conflicts: list[str] = []
        for src in plan.values():
            name = os.path.basename(src)
            key = os.path.normcase(name)
            other = dest_names.get(key)
            if other is not None:
                conflicts.append(tr("{0} と {1} が同名です").format(other, src))
                continue
            dest_names[key] = src
            dest = os.path.join(save_dir, name)
            if os.path.exists(dest):
                if _same_file_content(src, dest):
                    continue     # 同一実体/同一内容=コピー不要(衝突ではない)
                conflicts.append(tr("{0} が既にあります").format(dest))
        if conflicts:
            self._report(
                "error", tr("コピーできません(同名ファイル)"),
                tr("コピー先に同名のファイルがあるため中止しました。"
                   "ファイル名を変更するか、既存ファイルを整理してください:")
                + "\n" + "\n".join(conflicts))
            return

        # 3) コピー実行
        copied = []
        try:
            for src in plan.values():
                dest = os.path.join(save_dir, os.path.basename(src))
                if os.path.exists(dest):
                    if _same_file_content(src, dest):
                        continue    # 既に同一内容がある=コピー不要
                shutil.copy2(src, dest)
                copied.append(os.path.basename(src))
        except OSError as e:
            self._report(
                "error", tr("コピーできません"),
                tr("素材のコピーに失敗したため保存を中止しました"
                   "(コピー済みのファイルは残りますが、シナリオの参照は"
                   "書き換えていません):\n{0}").format(e))
            return

        # 4) self.data の参照をコピー先へ書き換え(base_dir基準の相対 or 絶対)
        def rewrite(_kind, p):
            abs_p = p if os.path.isabs(p) \
                else os.path.normpath(os.path.join(self.base_dir, p))
            if os.path.normcase(os.path.normpath(abs_p)) in plan:
                dest = os.path.join(save_dir, os.path.basename(abs_p))
                return _safe_relpath(dest, self.base_dir)
            return p

        _map_item_paths(self.data, rewrite)
        # 表示中パネルへ反映(選択イベントの音声/トラック表示を更新)
        if self.selected and self.selected in self.data["events"]:
            self._load_panel(self.selected)

        # 5) 保存(付け替え後はすべて保存先の内=相対パスで書き出される)
        if self._do_save(path) and copied:
            self._report(
                "ok", tr("保存しました(素材をコピー)"),
                tr("{0}個の素材を保存先フォルダへコピーし、"
                   "参照を相対パスに書き換えました:").format(len(copied))
                + "\n" + "\n".join(sorted(copied)))


    def _prepare_for_save(self) -> str | None:
        """保存ダイアログを出す前の事前検証。エラーメッセージ(None=OK)。

        エラーがある場合はその場で画面にエラーを表示して非Noneを返す。
        呼び出し側はこの戻り値が非Noneなら保存ダイアログを開かない
        (＝ダイアログは確実に保存できるときだけ表示される)。
        """
        # 1) パネル内容をモデルへ書き戻す(空チャンネル等の構造エラー検出)
        err = self._apply_panel() if self.selected else None
        if err:
            self._report("error", tr("編集エラー"), err)
            return err
        # 2) 参照ファイル(音声/funscript/CSV)の存在などを保存先に依らず
        #    base_dir 基準で事前検証する(Scenario.load 相当)。実際の保存は
        #    保存先基準へ付け替えたコピーで行うが、参照の存在は場所に依らない。
        tmp = os.path.join(self.base_dir, ".rvp_save_check.tmp")
        try:
            rebased = self._rebased_data_for_save(self.base_dir)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rebased, f, ensure_ascii=False)
            sc = Scenario.load(tmp)
            # =130: 読み込み警告(担当種別のトラックが無いチャンネル等)は
            # 保存を止めずに知らせる(エラーとは別枠)
            if sc.load_warnings:
                self._report("warn", tr("保存しますが、注意点があります"),
                             "\n".join(sc.load_warnings))
        except Exception as e:
            self._report(
                "error", tr("保存できません"),
                tr('シナリオの検証でエラーが見つかりました:\n{0}').format(e))
            return str(e)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None

    def _rebased_data_for_save(self, new_base: str) -> dict:
        """保存用に、全アイテムの音声/funscript/CSVパスを new_base 基準へ
        付け替えたディープコピーを返す(self.data は変更しない)。"""
        data = copy.deepcopy(self.data)
        _map_item_paths(
            data,
            lambda _k, p: _rebase_scenario_path(p, self.base_dir, new_base))
        return data

    def _external_paths_for_save(self, save_dir: str) -> list[tuple[str, str]]:
        """保存先 save_dir 基準へ付け替えた結果、絶対パスとして書き出される
        (=保存先フォルダの外にある)素材の一覧を返す。

        戻り値: [(kind, 絶対パス)]。kind は "audio" / "fs"。
        重複(同一ファイルへの複数参照)は除去し、パス順で返す。
        """
        rebased = self._rebased_data_for_save(save_dir)
        found: dict[str, tuple[str, str]] = {}

        def collect(kind, p):
            if os.path.isabs(p):
                key = os.path.normcase(os.path.normpath(p))
                if key not in found or kind == "audio":
                    # 同一ファイルがaudio/fs両方で参照されたらaudio扱いを優先
                    # (自動紐づけの同伴コピー判定のため)
                    found[key] = (kind, os.path.normpath(p))
            return p

        _map_item_paths(rebased, collect)
        return sorted(found.values(), key=lambda kv: kv[1])

    def _do_save(self, path: str) -> bool:
        """保存を実行する。成功でTrue、エラー表示して中止でFalse。"""
        self._commit_pending_renames()   # =100①(保存ボタンもフォーカスを取らない)
        err = self._apply_panel() if self.selected else None
        if err:
            self._report("error", tr("編集エラー"), err)
            return False
        # タイトルは保存先JSONファイル名から確定する(ファイル名が正)
        self.data["title"] = _title_from_path(path)
        # =250: 紹介文はダイアログで反映済みの _detail_text から書き出す
        detail = self._detail_text.rstrip("\n")
        if detail:
            self.data["detail"] = detail
        else:
            self.data.pop("detail", None)
        # =252: デバイス連動フラグ。ON=キーを書かない(省略=ON=旧シナリオと
        # 同じ書式のまま) / OFF=false を明示する。
        if self.device_enabled:
            self.data.pop("device_enabled", None)
        else:
            self.data["device_enabled"] = False
        # =256: BGMフラグ。省略の意味が逆(省略=OFF)なので、ON=true明示 /
        # OFF=キーを書かない。
        if self.bgm_enabled:
            self.data["bgm_enabled"] = True
        else:
            self.data.pop("bgm_enabled", None)
        # トップレベルのキー順を title, detail, background, bgm_enabled,
        # device_enabled, start, events, その他 に整える(=262でbackground追加)
        ordered = {}
        for key in ("title", "detail", "background", "bgm_enabled",
                    "device_enabled", "start", "events"):
            if key in self.data:
                ordered[key] = self.data[key]
        for key, v in self.data.items():
            if key not in ordered:
                ordered[key] = v
        self.data = ordered

        # 書き出しは保存先フォルダ基準へパスを付け替えたコピーで行う
        # (self.data 自体は base_dir 基準のまま保持=編集を続けても整合)。
        save_dir = os.path.dirname(os.path.abspath(path))
        out_data = self._rebased_data_for_save(save_dir)
        payload = json.dumps(out_data, ensure_ascii=False, indent=2)

        # 一時ファイルに書いてバリデーション
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            _sc = Scenario.load(tmp)  # 参照ファイルの存在チェック等
            # =130: 読み込み警告(担当種別のトラックが無いチャンネル等)は
            # 保存を止めずに知らせる(エラーとは別枠)
            if _sc.load_warnings:
                self._report("warn", tr("保存しますが、注意点があります"),
                             "\n".join(_sc.load_warnings))
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            self._report(
                "error", tr("保存できません"),
                tr('シナリオの検証でエラーが見つかりました:\n{0}').format(e))
            return False
        try:
            os.replace(tmp, path)
        except OSError as e:
            # 保存先が使用中(他プロセスが掴んでいる)・読み取り専用・
            # アクセス権なし等。tmpを片付けてユーザーへ通知する。
            try:
                os.remove(tmp)
            except OSError:
                pass
            self._report(
                "error", tr("保存できません"),
                tr("ファイルへ書き込めませんでした(他のアプリで使用中・"
                   "読み取り専用・アクセス権なし等の可能性):\n{0}").format(e))
            return False
        self.path = path
        # 名前を付けて保存でファイル名が変わった場合に備え、タイトル表示を更新
        self._refresh_title_display()
        # base_dir は変更しない: self.data の相対パスは base_dir 基準のまま保持し、
        # 保存のたびに保存先基準へ付け替えたコピーを書き出す(整合を崩さない)。
        self._report("ok", tr("保存しました"), os.path.basename(path))
        if self.on_saved:
            self.on_saved(path)
        return True
