"""シナリオ編集: 色定数・共通小部品(CTkOptionMenu ラッパ・区切り線)・小さなヘルパー。"""
from __future__ import annotations

import customtkinter as ctk
import json
import os
from ..scenario import AUTO_FS_TAGS, CSV_TRACK_TYPES, migrate_video_node
from ..scenario_map import OK_COLOR
from .. import apptheme, scenario_map as _smap, winstate
from ..i18n import load_config, save_config, tr



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
