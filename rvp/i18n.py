"""多言語対応(日本語/英語)。

方式: gettextスタイルで「日本語の原文」を翻訳キーにする。
  tr("再生")            → ja: "再生" / en: "Play"
  tr("チャンネル{ch}")  → プレースホルダは呼び出し側で .format(ch=...) する

言語は起動時に確定し、切り替えは再起動で反映される(ライブ切替なし)。
そのため、UI内の「表示文字列の比較」(choice == tr("N分で終了") 等)は
セッション内で常に一貫し、既存ロジックがそのまま動く。

言語の決定順序:
  1. 環境変数 RVP_LANG ("ja" / "en")  … テスト・一時切替用
  2. 設定ファイル ~/.rvp_config.json の "language"
  3. 既定 "ja"

英訳が未登録のキーは日本語のまま表示される(フォールバック)。
"""

import json
import os

# 設定ファイルの場所。環境変数 RVP_CONFIG_PATH で上書きできる
# (テストが実環境の設定を読み書きしないための分離用)。
CONFIG_PATH = os.environ.get("RVP_CONFIG_PATH") \
    or os.path.join(os.path.expanduser("~"), ".rvp_config.json")

SUPPORTED = ("ja", "en")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _detect_language() -> str:
    env = os.environ.get("RVP_LANG", "").strip().lower()
    if env in SUPPORTED:
        return env
    lang = load_config().get("language")
    if lang in SUPPORTED:
        return lang
    return "ja"


LANG = _detect_language()


def set_language(lang: str) -> None:
    """言語設定を保存する(反映は再起動後)。"""
    if lang not in SUPPORTED:
        return
    config = load_config()
    config["language"] = lang
    save_config(config)


def tr(text: str) -> str:
    """日本語原文を現在の言語の文字列へ変換する。"""
    if LANG == "ja":
        return text
    return EN.get(text, text)


# ============================================================
# 英訳辞書(キー=日本語原文)。プレースホルダ{name}は位置を変えてよい。
# ============================================================
EN: dict[str, str] = {
    # ---- タブ・ヘッダ・接続 ----
    "接続": "Connect",
    "シナリオ": "Scenario",
    "再生": "Play",
    "● 未接続": "● Not connected",
    "● 接続中...": "● Connecting...",
    "● 接続済み": "● Connected",
    "● 再接続中...": "● Reconnecting...",
    "Intiface Centralへ自動で接続する": "Auto-connect to Intiface Central",
    "(未接続)": "(not connected)",
    "(デバイスが見つかりません)": "(no devices found)",
    "Intiface Central サーバーURL": "Intiface Central server URL",
    "接続中デバイス": "Connected devices",
    "ROTATEデバイスの割り当て": "ROTATE device assignment",
    # =120: 2ロータ機のロータ単位割り当て
    "各回転デバイスを ROTATE(ufo) / ROTATE(a10cyclonesa) のどちらで動かすか選べます。"
    "2ロータ機はロータごとに割り当てを分けられます(左右を入れ替えたい時は割り当てを入れ替えます)。":
        "Choose whether each rotate device runs as ROTATE(ufo) or "
        "ROTATE(a10cyclonesa). Devices with two rotors can assign each "
        "rotor separately (swap the assignments to swap left/right).",
    "ロータ": "Rotor ",
    "(2ロータ)": " (2 rotors)",
    "左右反転": "Swap L/R",
    "左{0} 右{1}": "L{0} R{1}",
    "Intiface Central へ接続できませんでした:\n{0}\n\nIntiface Central のサーバーが起動しているか確認してください。":
        "Could not connect to Intiface Central:\n{0}\n\nMake sure the Intiface Central server is running.",
    "linear対応デバイスが見つかりません。\n音声のみで再生されます。":
        "No linear-capable device found.\nPlayback will be audio only.",
    "⚠ 接続できませんでした: {0}\nIntiface Central を起動し、サーバーを開始してから再度お試しください。":
        "⚠ Could not connect: {0}\nLaunch Intiface Central, start the server, then try again.",
    "⚠ linear対応デバイスが見つかりません。音声のみで再生されます。":
        "⚠ No linear-capable device found. Playback will be audio only.",

    # ---- シナリオタブ ----
    "シナリオファイルを開く": "Open scenario file",
    "編集画面": "Edit",
    "(シナリオ未選択)": "(no scenario selected)",
    "最近のシナリオ": "Recent scenarios",
    "(まだ履歴はありません)": "(No history yet)",
    "  (見つかりません)": "  (not found)",
    "ファイルが見つかりません(移動・削除された可能性があります):\n{0}":
        "File not found (it may have been moved or deleted):\n{0}",
    "シナリオ内容": "Scenario description",
    "シナリオファイルを開くと、ここに説明が表示されます。":
        "Open a scenario file to see its description here.",
    "(このシナリオには説明がありません)": "(This scenario has no description.)",
    "シナリオファイルを選択": "Select a scenario file",
    "シナリオファイル": "Scenario files",
    "すべてのファイル": "All files",
    # ("{0}  ({1}イベント)" は =152 でイベント数の表示を廃止したため削除。
    #  視聴前にボリュームが推測できてしまうため=ユーザー決定)
    "「編集」ボタンでこのシナリオを編集画面で開けます(素材の抜けは保存時に確認できます)。":
        "You can still open this scenario in the editor with the \"Edit\" button (missing files are reported when saving).",
    "シナリオファイルの読み込みに失敗しました:\n{0}":
        "Failed to load the scenario file:\n{0}",

    # ---- 再生タブ ----
    "選択中のシナリオファイル": "Selected scenario",
    "(未選択)": "(none)",
    "待機中": "Idle",
    "再生中": "Playing",
    "一時停止": "Paused",
    "シナリオ終了": "Finished",
    "エラー": "Error",
    "イベント: -": "Event: -",
    "イベント: {0}": "Event: {0}",
    "  ／  経過 {0}": "  /  elapsed {0}",
    "ステート: -": "State: -",
    "ステート: {0}  ／  経過 {1}": "State: {0}  /  elapsed {1}",
    "再生中のチャンネル": "Channels now playing",
    "★=シークバーが追従しているチャンネル":
        "★ = the channel the seek bar follows",
    # ---- ③グラフ表示(=69) ----
    "左": "L",
    "右": "R",
    "追従停止中（クリックで戻る）": "Not following (click to resume)",
    "ホイール=時間の拡大縮小 ／ ドラッグ=前後を見る ／ クリック=再生位置へ戻る":
        "Wheel = zoom time / Drag = pan / Click = back to playhead",
    # =237: レビュー画面(参照)のグラフだけ、右ダブルクリックの案内を足す
    "ホイール=時間の拡大縮小 ／ ドラッグ=前後を見る ／ クリック=再生位置へ戻る ／ 右ダブルクリック=再生位置をここへ":
        "Wheel = zoom time / Drag = pan / Click = back to playhead / "
        "Double right click = play from here",
    # ---- スクリプト編集モード(=170) ----
    "スクリプト編集": "Edit script",
    "レビューに戻る": "Back to review",
    "(新規作成)": "(New script)",
    "対象:": "Target:",
    "点": "Point",
    "新規保存": "Save as...",
    "位置[pos]:": "Position [pos]:",
    "時間[at]:": "Time [at]:",
    "{0}単位": "{0} steps",
    "左クリック=打点・選択 ／ 左ドラッグ=移動・矩形選択 ／ 右クリック=点・パターンのメニュー ／ 右ダブルクリック=再生位置セット ／ 右ドラッグ=前後を見る ／ ホイール=時間の拡大縮小":
        "Left click = add / select point / Left drag = move / box select / "
        "Right click = menu on points / patterns / "
        "Double right click = set playback position / "
        "Right drag = pan / Wheel = zoom time",
    "ユーザーパターン編集": "Edit user patterns",
    # =212〜=214: リアルタイム編集
    "割り当て解除": "Unassign",
    "時間補正(秒):": "Time offset (s):",
    "F1〜F9・数字キーで配置する位置を、押した瞬間より前へずらす（押し遅れの補正。0.01秒刻み・0.00〜-1.00秒）":
        "Shift the F1–F9 / number-key placement earlier than the key press "
        "(compensates for late presses; 0.01 s steps, 0.00 to -1.00 s)",
    "この位置にはパターンを配置できません":
        "The pattern cannot be placed at this position",
    "速度:": "Speed:",
    "F1〜F9=割り当てたパターンを再生位置へ上書き配置（長押しで数珠つなぎ） ／ 割り当て=パターンボタンを右クリック ／ 数字キー=再生位置へ打点（0〜9=pos0〜90・+=pos100） ／ 矢印キー=選択中の点・パターンを1グリッド移動（長押しで連続） ／ Q・E=10秒戻る・進む":
        "F1–F9 = place the assigned pattern at the playback position "
        "(overwrite; hold to chain) / Assign = right-click a pattern button / "
        "Number keys = add a point at the playback position "
        "(0–9 = pos 0–90, + = pos 100) / "
        "Arrow keys = move the selection by one grid step (hold to repeat) / "
        "Q / E = 10 s back / forward",
    # =311/=312/=313: 左右2本(UFOTW)のときの案内
    "F1〜F9=割り当てたパターンを割り当てた側へ上書き配置（長押しで数珠つなぎ・左右別々） ／ 割り当て=パターンボタンを右クリック ／ 数字キー=再生位置へ打点：数字行=左（ロータ1）・テンキー=右（ロータ2）（1〜9=速度-70〜+70・5=停止） ／ 矢印キー=選択中の点・パターンを1グリッド移動 ／ Q・E=10秒戻る・進む":
        "F1–F9 = place the assigned pattern on its assigned side "
        "(overwrite; hold to chain, left and right independently) / "
        "Assign = right-click a pattern button / "
        "Number keys = add a point at the playback position: "
        "number row = L (rotor 1), numpad = R (rotor 2) "
        "(1–9 = speed -70 to +70, 5 = stop) / "
        "Arrow keys = move the selection by one grid step / "
        "Q / E = 10 s back / forward",
    # =317: 回転(a10 の rotate funscript など・1本表示)の案内
    "F1〜F9=割り当てたパターンを再生位置へ上書き配置（長押しで数珠つなぎ） ／ 割り当て=パターンボタンを右クリック ／ 数字キー=再生位置へ打点（1〜9=速度-70,-60,-50,-40,0,+40,+50,+60,+70） ／ 矢印キー=選択中の点・パターンを1グリッド移動（長押しで連続） ／ Q・E=10秒戻る・進む":
        "F1–F9 = place the assigned pattern at the playback position "
        "(overwrite; hold to chain) / Assign = right-click a pattern button / "
        "Number keys = add a point at the playback position "
        "(1–9 = speed -70, -60, -50, -40, 0, +40, +50, +60, +70) / "
        "Arrow keys = move the selection by one grid step (hold to repeat) / "
        "Q / E = 10 s back / forward",
    "両方（同時）": "Both (at once)",
    "両": "B",
    # =319: マウス位置のガイド
    "時間={0} 位置={1}": "Time={0} Pos={1}",
    "時間={0} 回転速度={1}": "Time={0} Speed={1}",
    "時間={0} 強さ={1}": "Time={0} Strength={1}",
    "10秒戻る（Qキー）": "Back 10 s (Q key)",
    "10秒進む（Eキー）": "Forward 10 s (E key)",
    "再生速度（音声は音の高さも変わります。時間補正は素材の時間のまま）":
        "Playback speed (audio pitch changes too; the time offset stays "
        "in material time)",
    "{0}秒単位": "{0} s steps",
    "ユーザーパターン {0}": "User pattern {0}",
    "未登録（「ユーザーパターン編集」から登録）":
        "Not registered (register via \"Edit user patterns\")",
    "編集する枠:": "Slot to edit:",
    "点だけで編集します（パターンは使えません）。最後の点の時間が基準長になります":
        "Edit with points only (patterns are not available). "
        "The time of the last point becomes the base length",
    "登録済み（基準長 {0}ms）": "Registered (base length {0} ms)",
    "未登録": "Not registered",
    "保存していない編集があります。\n破棄して切り替えますか？":
        "There are unsaved edits.\nDiscard them and switch?",
    "2点以上を打ってください（幅0は登録できません）":
        "Add at least two points (zero-width patterns cannot be registered)",
    "一部のパターン情報を復元できませんでした（点はそのまま残しています）":
        "Some pattern info could not be restored "
        "(the points themselves are kept)",
    "mpv が見つからないため、動画は再生できません（メイン画面の Settings でパスを指定してください）":
        "Cannot play the video because mpv was not found "
        "(set the mpv path in Settings on the main window)",
    "mpv を起動できませんでした: {0}": "Could not start mpv: {0}",
    "区間の開始が動画の長さを超えています":
        "The range start exceeds the video length",
    "動画のレビューには mpv が必要です。\nメイン画面の Settings で mpv のパスを指定してください":
        "Reviewing a video item requires mpv.\n"
        "Set the mpv path in Settings on the main window",
    "パターンを配置するときの縮尺（画面を閉じるまで有効）":
        "Scale applied when placing a pattern (kept until this window closes)",
    "上下反転すると位置が0〜100に収まらないため、反転できませんでした":
        "Could not flip: the flipped positions would fall outside 0-100",
    "未選択のパターンと共有している点が軸の上にないため、上下反転できませんでした":
        "Could not flip: a point shared with an unselected pattern is "
        "not on the axis",
    "選択範囲の中に選択していない点やパターンがあるため、左右反転できませんでした":
        "Could not flip: the selected range contains points or patterns "
        "that are not selected",
    "未選択のパターンと接しているため、両端の位置(pos)が同じときだけ左右反転できます":
        "Touching an unselected pattern: flipping left-right is only "
        "possible when both ends have the same pos",
    "パターンの端のすぐ近くには点を置けないため、貼り付けできませんでした":
        "Could not paste: points cannot be placed right next to a "
        "pattern edge",
    "貼り付け先に収まらないため、貼り付けできませんでした（0〜100または時間の範囲外になります）":
        "Could not paste: the content does not fit here (it would fall "
        "outside 0-100 or before time 0)",
    "貼り付けの基準になる点を選択してください（選択中で最も未来の点が基準になり、その点は上書きされます）":
        "Select a point to paste at (the latest selected point becomes the "
        "base and is overwritten)",
    # ---- P2(=173): パターン ----
    # =217: 標準パターンの固有名(「山」「ギザギザ(粗)」など19件)は廃止し、
    # STD1〜STD20 の識別子になった(日英共通なので辞書の項目も不要)。
    "インバート": "Invert",
    "配置するパターンの上下を反転する（画面を閉じるまで有効）":
        "Flip patterns vertically when placing (until this window closes)",
    "上下反転": "Flip vertically",
    "上下反転(全幅)": "Flip vertically (full)",
    "上下反転(パターン内)": "Flip vertically (in pattern)",
    "上下反転(選択内)": "Flip vertically (in selection)",
    "パターン解除": "Ungroup pattern",   # =281 で「グループ解除」へ改名(旧キー残置)
    "グループ解除": "Ungroup",
    "グループ化": "Group",
    # =243: 音声波形
    "音声波形:": "Waveform:",
    "モノラル": "Mono",
    "ステレオ": "Stereo",
    "ステレオ(逆)": "Stereo (swapped)",
    "音声の振幅(音量の形)をグラフの背景へ薄く表示します（ステレオ=上がL・下がR。動画のみのアイテムでは表示されません）":
        "Show the audio amplitude (volume shape) faintly behind the "
        "graphs (stereo: top=L, bottom=R; not shown for video-only items)",
    "パターンが含まれています。先に解除してください":
        "The selection contains a pattern. Ungroup it first",
    "範囲内に選択していない点があるため、グループ化できません":
        "Cannot group: the range contains points that are not selected",
    "追従停止中（再生で戻る）": "Not following (play to resume)",
    "編集モード: 区間指定を無視して素材全体を表示・再生しています":
        "Edit mode: the range setting is ignored; the whole material is "
        "shown and played",
    "スクリプトに未保存の編集があります。":
        "The script has unsaved edits.",
    "保存する": "Save",
    "破棄する": "Discard",
    "やめる": "Cancel",
    "このファイルは他の {0} 箇所でも使われています。上書きしますか？":
        "This file is also used in {0} other place(s). Overwrite it?",
    "上書きする": "Overwrite",
    "funscriptの新規保存": "Save funscript as",
    # =224: csv(ROTATE)の編集
    "csvの新規保存": "Save csv as",
    "（左）": " (L)",
    "（右）": " (R)",
    # =232: 5列csv は左右2本を同時に編集する(候補は1項目・見出しは各グラフ)
    "（左右）": " (L/R)",
    # =233: csv では at/pos ではなく「時間(100ms)」「回転速度(-100〜100)」
    "時間(100ms):": "Time (100ms):",
    "回転速度:": "Speed:",
    "左（ロータ1）": "L (rotor 1)",
    "右（ロータ2）": "R (rotor 2)",
    # =233: 縦軸を回転速度(-100〜100)表記にしたので「中央0=停止」へ
    # =297: 分解能 200 で速度 1 刻みになったので「2刻み」の注記を外した
    "csv(ROTATE): 中央0=停止 ／ 上=正回転 ／ 下=逆回転 ／ "
    "次の点まで同じ値を保ちます ／ 時刻は100ms単位です":
        "csv (ROTATE): 0 = stop / up = forward / down = reverse "
        "/ each value is held until the next point / "
        "times are in 100 ms units",
    "（csvではパターンの記憶は保存されません。開き直すと点だけになります）":
        "(csv cannot store pattern memory; reopening leaves only the points)",
    # =227: サブ表示・新規トラックの導線
    "サブ:": "Sub:",
    "(なし)": "(none)",
    "＋新規": "+ New",
    "このアイテムへ別の種別のトラックを新しく作る":
        "Create another track of a different type for this item",
    "編集中のトラックの下に、別のトラックを参考として表示します（見るだけ・編集不可）":
        "Show another track below the one you are editing, for reference "
        "(view only)",
    # =225: rotate系・vibration の funscript(階段)
    "ROTATE: 中央50=停止 ／ 上=正回転 ／ 下=逆回転 ／ 次の点まで同じ値を保ちます":
        "ROTATE: 50 = stop / up = forward / down = reverse / "
        "each value is held until the next point",
    "VIBRATION: 0=停止 〜 100=最大 ／ 次の点まで同じ値を保ちます":
        "VIBRATION: 0 = off to 100 = max / "
        "each value is held until the next point",
    "アイテムの行が見つからないため、トラックへの紐づけは行われませんでした":
        "The item row could not be found, so the file was not bound to a "
        "track",
    "◀◀ 現在のイベントを巻き戻す": "◀◀ Restart current event",
    "▶▶ 現在のイベントをスキップする": "▶▶ Skip current event",
    "急動作防止": "Sudden-motion guard",   # 旧「速度制限」(2026-07-22改名)
    "自動(ルールで紐づけ)": "Auto (naming rules)",
    "手動": "Manual",
    "自動: {0}": "Auto: {0}",
    "自動: 紐づくfunscriptなし": "Auto: no matching funscript",
    "＋トラック追加": "+ Add track",
    "(トラックなし=デバイス動作なし)": "(no tracks = no device motion)",
    "(funscriptを選択...)": "(select funscript...)",
    "(funscriptまたはCSVを選択...)": "(select funscript or CSV...)",
    "{0}: デバイス種別 '{1}' が重複しています":
        "{0}: device type '{1}' is duplicated.",
    "{0}: トラック{1}のfunscriptを選択してください":
        "{0}: select the funscript of track {1}.",
    "チャンネル{0}: {1}": "Channel {0}: {1}",
    "弱": "Low",
    "中": "Mid",
    "強": "High",
    "停止": "Stopped",
    "区間補正0～100": "Range 0–100",
    "区間補正{0}～{1}": "Range {0}–{1}",
    "出力補正0～100%": "Output 0–100%",
    "出力補正{0}～{1}%": "Output {0}–{1}%",
    "動作タイミング": "Timing",
    "反転": "Invert",
    "※ 動作タイミング(秒): ＋で遅らせる / −で早める":
        "* Timing (sec): + delays / − advances the device",
    "再生中にエラー": "Playback error",

    # ---- 編集UI: ツールバー・共通 ----
    "シナリオ編集 - RVP": "Scenario Editor - RVP",
    "タイトル:": "Title:",
    "(未保存)": "(unsaved)",
    "＋ イベント追加": "+ Add event",
    "イベント削除": "Delete event",
    "イベントコピー": "Copy event",
    "コピー": "Copy",
    "他からコピー": "Copy from…",
    "(ステートなし)": "(no state)",
    "イベント": "Event",
    "ステート": "State",
    "チャンネル": "Channel",
    "コピー元のチャンネルを選んでください(音声ch・スクリプトchのみ)。":
        "Choose a source channel to copy from "
        "(audio or script-only channels).",
    "コピーできるチャンネルがありません。":
        "There are no channels available to copy from.",
    "チャンネル {0} に {1} の内容をコピーしました。":
        "Copied the content of {1} into channel {0}.",
    "スクリプト専用チャンネルはデバイス担当を設定してください。":
        "Assign a device type to this script-only channel.",
    "保存": "Save",
    # =250: 紹介文ダイアログ / =252: デバイス連動トグル
    "紹介文": "Description",
    "シナリオ選択時に表示される紹介文です(改行可)":
        "Shown when the scenario is selected (line breaks allowed)",
    "デバイス連動：ON": "Device link: ON",
    "デバイス連動：OFF": "Device link: OFF",
    "device_enabled は true/false で指定してください":
        "device_enabled must be true or false",
    # =256: BGM機能(ヘッダのトグル・ノードのBGMブロック・再生ログ・解析エラー)
    "BGM：ON": "BGM: ON",
    "BGM：OFF": "BGM: OFF",
    "BGM:": "BGM:",
    "前のBGMを引き継ぐ": "Inherit previous BGM",
    "BGMを指定": "Set BGM",
    "BGMオフ": "BGM off",
    "＋BGM": "+ BGM",
    "BGM音声を選択": "Select BGM audio",
    "(BGMはイベント/ステートをまたいで再生され続けます)":
        "(BGM keeps playing across events and states)",
    "{0}: BGMを「指定」にしていますが、音声がありません":
        "{0}: BGM mode is \"Set BGM\" but it has no audio",
    "bgm_enabled は true/false で指定してください":
        "bgm_enabled must be true or false",
    "{0} bgm": "{0} bgm",
    "{0}: オブジェクトで指定してください": "{0}: must be an object",
    '{0}: off は true のみ指定できます(引き継ぐ場合はキーごと省略します)':
        '{0}: off accepts only true (omit the key to inherit)',
    '{0}: items(1件以上)か "off": true を指定してください':
        '{0}: specify items (1 or more) or "off": true',
    "{0} items[{1}]": "{0} items[{1}]",
    "{0}: 要素が不正です": "{0}: invalid entry",
    "{0}: order は sequential / random のいずれかにしてください":
        "{0}: order must be sequential or random",
    "{0}: ステート形式では bgm は各ステートに指定してください":
        "{0}: in state form, specify bgm on each state",
    "BGMの音声ファイルが見つかりません: {0}": "BGM audio file not found: {0}",
    "BGMの読み込み失敗: %s (%s)": "Failed to load BGM: %s (%s)",
    "BGMの読み込みに失敗しました: {0}": "Failed to load BGM: {0}",
    "BGMを再生できません(読み込めた音声がありません)":
        "Cannot play BGM (no loadable audio)",
    "BGM開始: {0}": "BGM start: {0}",
    "BGM停止": "BGM stopped",
    "{0} (ループ)": "{0} (loop)",
    "{0} ほか ({1}・{2}曲)": "{0} and more ({1}, {2} tracks)",
    "順番": "in order",
    "名前を付けて保存": "Save as...",
    "保存しました": "Saved",
    "保存できません": "Cannot save",
    "無題シナリオ": "Untitled scenario",
    "新規シナリオ": "New scenario",
    "確認": "Confirm",
    "編集エラー": "Edit error",
    "削除できません": "Cannot delete",
    "解除できません": "Cannot convert",
    "シナリオファイルを保存": "Save scenario file",
    "保存先フォルダが元と異なります。\n音声・funscriptの相対パスが合わなくなる可能性があります。\n保存を続けますか？":
        "The destination folder differs from the original.\nRelative paths to audio/funscript files may break.\nSave anyway?",
    "シナリオの検証でエラーが見つかりました:\n\n{0}":
        "Scenario validation found errors:\n\n{0}",

    # ---- 編集UI: イベント・パネル ----
    "(イベント未選択)": "(no event selected)",
    "ステート形式に変換": "Convert to states",
    "ステート形式を解除": "Convert to simple event",
    "次のイベント:": "Next event:",
    "(再生終了)": "(end of scenario)",
    "開始イベントにする": "Set as start event",
    "イベント終了条件:": "End condition:",
    "(ステートを跨いで累積)": "(accumulated across states)",
    "合計N秒で次へ": "Next after N sec total",
    "合計N回の再生で次へ": "Next after N plays total",
    "N回のステート移行で次へ": "Next after N transitions",
    "デバイス担当:": "Device channels:",
    "なし": "None",
    "最後のイベントは削除できません": "The last event cannot be deleted.",
    "イベント '{0}' を削除しますか？": "Delete event '{0}'?",
    "イベント {0}: {1}": "Event {0}: {1}",
    "イベント {0}: 有効なチャンネルが1つもありません":
        "Event {0}: no channel is enabled.",
    "イベント {0}: デバイス担当 {1}→{2} は無効なチャンネルを指しています":
        "Event {0}: device assignment {1}→{2} points to a disabled channel.",
    "イベント {0}: イベント終了条件の値が不正です":
        "Event {0}: invalid value for the event end condition.",
    "{0}ステート": "{0} states",

    # ---- 編集UI: ステート ----
    "ステートマシン図(○をクリックで選択)": "State machine (click a circle to select)",
    "＋ ステート追加": "+ Add state",
    "ステート削除": "Delete state",
    "開始": "start",
    "ステート: {0}": "State: {0}",
    "開始ステートにする": "Set as start state",
    "ステート移行:": "State transition:",
    "ステート移行なし": "No state transition",
    "チャンネル回数でステート移行": "By play count",
    "対象ch:": "Channel:",
    "ステート移行先(複数チェックで抽選):": "Target state(s) (check several for random pick):",
    # =73: ステート移行先の重み(定数 or 変数参照)と else
    "(イベント終了)": "(end event)",
    "{0}: 移行先 {1} の重みが不正です":
        "{0}: invalid weight for target {1}",
    "{0}: transition random[{1}] の weight":
        "{0}: weight of transition random[{1}]",
    "{0}: transition random[{1}] の要素が不正です":
        "{0}: invalid element in transition random[{1}]",
    "{0}: transition の else はステートIDの文字列で指定してください":
        "{0}: transition else must be a state ID string",
    "{0} ステート'{1}': 移行先(else) '{2}' が存在しません":
        "{0} state '{1}': transition else target '{2}' does not exist",
    # =77: 未実行ステートの優先(=26のステート版)
    "{0}: transition の visited は include か exclude にしてください":
        "{0}: transition visited must be include or exclude.",
    '{0}: transition の when_exhausted は "all" / "reset" / {{"to": "..."}} にしてください':
        '{0}: transition when_exhausted must be "all" / "reset" / {{"to": "..."}}.',
    "{0} ステート'{1}': 移行先(実行済み時) '{2}' が存在しません":
        "{0} state '{1}': when_exhausted target '{2}' does not exist",
    "未実行ステートのみ候補": "Unvisited states only",
    "指定ステートへ": "Go to a specific state",
    "{0}: 「指定ステートへ」の行き先を選択してください":
        "{0}: select the target for \"Go to a specific state\".",
    "(未実行優先・{0})": " (prefer unvisited, {0})",
    "消化で全候補": "on exhaust: all candidates",
    "消化でリセット": "on exhaust: reset history",
    "消化で{0}へ": "on exhaust: to {0}",
    "イベント終了: 移行先の重みが全て0(else未指定)":
        "Event end: all transition weights are 0 (no else)",
    "{0}(重みが全て0 → else)": "{0} (all weights 0 -> else)",
    "{0}(重み{1})": "{0} (weight {1})",
    "(重み全0→{0})": " (all weights 0 -> {0})",
    "イベント {0} ステート {1}": "Event {0} state {1}",
    "{0}: 有効なチャンネルが1つもありません": "{0}: no channel is enabled.",
    "{0}: デバイス担当 {1}→{2} は無効なチャンネルを指しています":
        "{0}: device assignment {1}→{2} points to a disabled channel.",
    "{0}: 移行条件の対象チャンネル {1} が有効ではありません":
        "{0}: transition channel {1} is not enabled.",
    "{0}: 移行条件の値(min/max)が不正です": "{0}: invalid transition value (min/max).",
    "{0}: 移行条件の回数は1以上にしてください":
        "{0}: transition count must be at least 1.",
    "{0}: 移行条件の秒数は正の数にしてください":
        "{0}: transition seconds must be positive.",
    "{0}: 移行先を1つ以上チェックしてください": "{0}: check at least one target state.",
    "ステート '{0}' を削除しますか？": "Delete state '{0}'?",
    "最後のステートは削除できません。\nステートをやめる場合は「ステート形式を解除」を使ってください。":
        "The last state cannot be deleted.\nUse \"Convert to simple event\" to stop using states.",
    "ステートが1つのときだけ解除できます。\n先に不要なステートを削除してください。":
        "Conversion is only possible with a single state.\nDelete the other states first.",
    "移行条件を「移行なし」にしてから解除してください。":
        "Set the transition to \"No transition\" before converting.",
    "{0}: ステート移行なし(イベント終了まで)": "{0}: no state transition (until event end)",
    "{0}chの{1}回": "{1} plays on {0}",
    "{0}chの{1}〜{2}回": "{1}–{2} plays on {0}",
    "{0}chの合計{1}秒": "{1}s total on {0}",
    "{0}chの合計{1}〜{2}秒": "{1}–{2}s total on {0}",
    "{0}: {1} で → {2}": "{0}: {1} → {2}",
    " から抽選)": " random pick)",

    # ---- 編集UI: チャンネル・アイテム ----
    "チャンネル {0}": "Channel {0}",
    "順番に再生": "Sequential",
    "ランダム再生": "Random",
    # =76: ランダムバッグ方式(一巡してから次の抽選)
    "ランダム(重複なし)": "Random (no repeat)",
    "1周で終了": "End after 1 pass",
    "N周で終了": "End after N passes",
    "N秒で終了": "End after N seconds",
    "N回再生で終了": "End after N plays",
    "無限(移行/終了まで)": "Infinite (until transition/end)",
    "値": "value",
    "分": "min",
    "周": "passes",
    "回": "times",
    "秒": "s",
    "＋音声": "+ Audio",
    "音声ファイルを選択": "Select audio files",
    "音声": "Audio",
    "すべて": "All",
    "(音声未指定)": "(no audio)",
    "funscriptなし": "no funscript",
    "同名funscript(自動)": "same-name funscript (auto)",
    "(選択...)": "(select...)",
    "自動": "auto",
    "funscriptを選択": "Select a funscript",
    "funscript または CSV を選択": "Select a funscript or CSV",
    "funscript / CSV": "funscript / CSV",
    "チャンネル{0}: 音声が1つもありません": "Channel {0}: no audio items.",
    "チャンネル{0}: 終了条件の値が不正です": "Channel {0}: invalid end-condition value.",
    "チャンネル{0}: ランダム再生の終了条件は「N秒で終了」「N回再生で終了」「{1}」のいずれかにしてください":
        "Channel {0}: with Random, the end condition must be \"End after N seconds\", \"End after N plays\" or \"{1}\".",
    "チャンネル{0}: ランダム再生の終了条件は「N秒で終了」か「N回再生で終了」にしてください":
        "Channel {0}: with Random, the end condition must be \"End after N seconds\" or \"End after N plays\".",
    # =104/=105: 設定ダイアログ(=113でボタン表記は「設定」)
    "設定": "Settings",
    "外観": "Appearance",
    "言語": "Language",
    "描画更新頻度": "Refresh rate",
    # =119: UIフォント設定
    "フォント": "Font",
    "システム標準": "System default",
    # ---- =134 カラーテーマ(=136: 表示から「パステル」を外した) ----
    "ブルー": "Blue",
    "グリーン": "Green",
    "イエロー": "Yellow",
    "オレンジ": "Orange",
    "ピンク": "Pink",
    "パープル": "Purple",
    "外観設定を保存しました。アプリの再起動後に反映されます。":
        "Appearance preference saved. "
        "It takes effect after restarting the app.",
    "フォント設定を保存しました。アプリの再起動後に反映されます。":
        "Font preference saved. It takes effect after restarting the app.",
    "(このフォントはこの端末に見つかりません。見つかるまでは標準フォントで表示します)":
        "(This font is not installed on this device. The default font is "
        "used until it becomes available.)",
    "ダーク": "Dark",
    "ライト": "Light",
    # =102: グラフの1枚表示(集約)⇄個別表示
    "1枚表示": "Overlay",
    "個別表示": "Per-device",
    # =99: チャンネル「N秒で終了」の範囲抽選(min〜max)
    "(範囲は抽選)": "(range drawn at random)",
    "チャンネル{0}: 終了条件の値(min/max)が不正です":
        "Channel {0}: invalid end-condition value (min/max).",
    "チャンネル{0}: 終了条件の秒数は0以上(最大は正の数)にしてください":
        "Channel {0}: end-condition seconds must be 0 or more (max must be positive).",
    "{0:g}〜{1:g}秒で終了(今回{2:g}秒)":
        "End after {0:g}-{1:g} sec (this time {2:g} sec)",
    # =98: ランダム(重複なし)は「N周で終了」(袋N回)も可
    "チャンネル{0}: ランダム(重複なし)の終了条件は「N秒で終了」「N回再生で終了」「N周で終了」「{1}」のいずれかにしてください":
        "Channel {0}: with Random (no repeat), the end condition must be \"End after N seconds\", \"End after N plays\", \"End after N passes\" or \"{1}\".",
    "チャンネル{0}: ランダム(重複なし)の終了条件は「N秒で終了」「N回再生で終了」「N周で終了」のいずれかにしてください":
        "Channel {0}: with Random (no repeat), the end condition must be \"End after N seconds\", \"End after N plays\" or \"End after N passes\".",
    "チャンネル{0}: 順番に再生の終了条件は「1周で終了」「N周で終了」「{1}」のいずれかにしてください":
        "Channel {0}: with In order, the end condition must be \"End after one pass\", \"End after N passes\" or \"{1}\".",
    "チャンネル{0}: 順番に再生の終了条件は「1周で終了」か「N周で終了」にしてください":
        "Channel {0}: with In order, the end condition must be \"End after one pass\" or \"End after N passes\".",
    # ---- weight / pan / interval 編集UI ----
    "重み": "Weight",
    "パン上書き": "Override pan",
    "パン上書き(L/R %)": "Override pan (L/R %)",
    "インターバル": "Interval",
    "詳細 ▾": "Details ▾",
    "詳細 ▴": "Details ▴",
    # =59: 「音声設定」→「詳細設定」(区間・パン・シークバー追従をまとめる)
    "詳細設定": "Details",
    "下限": "Min",
    "上限": "Max",
    # =74: アイテムの重み(定数 or 変数参照。0/負も受理)
    "{0}: 重みが不正です": "{0}: invalid weight",
    "{0} アイテムの weight": "{0} item weight",
    "チャンネル{0}: 全アイテムの重みが0のため終了":
        "Channel {0}: ended because all item weights are 0",
    # =76: ランダム(重複なし)の補充ログ
    "チャンネル{0}: 重複なしの候補を使い切ったため補充":
        "Channel {0}: no-repeat pool used up; refilled",
    "{0}: パンは0〜100で指定してください": "{0}: pan must be 0-100.",
    "チャンネル{0}: パンは0〜100で指定してください": "Channel {0}: pan must be 0-100.",
    "チャンネル{0}: インターバルは「下限≤上限」かつ0以上で指定してください":
        "Channel {0}: interval must satisfy min ≤ max and be ≥ 0.",

    # ---- シナリオ検証(scenario.py) ----
    "start イベントが見つかりません": "Start event not found.",
    "シナリオファイルに events (または nodes) がありません":
        "The scenario file has no events (or nodes).",
    "シナリオファイルにエラーがあります:\n": "The scenario file has errors:\n",
    "イベント '{0}'": "Event '{0}'",
    "{0} チャンネル{1}": "{0} channel {1}",
    "{0} ステート'{1}'": "{0} state '{1}'",
    "イベント '{0}' の next '{1}' が存在しません":
        "Event '{0}': next event '{1}' does not exist.",
    "ノード '{0}' に audio がありません": "Node '{0}' has no audio.",
    "音声ファイルが見つかりません: {0}": "Audio file not found: {0}",
    "funscriptファイルが見つかりません({0}): {1}": "Funscript file not found ({0}): {1}",
    "{0}: audio がありません": "{0}: audio is missing.",
    "{0}: items の要素が不正です": "{0}: invalid element in items.",
    "{0}: items が空です": "{0}: items is empty.",
    "{0}: channels が空です": "{0}: channels is empty.",
    "{0}: states が空です": "{0}: states is empty.",
    "{0}: start ステートが見つかりません": "{0}: start state not found.",
    "{0}: mode '{1}' は不正です": "{0}: invalid mode '{1}'.",
    "{0}: チャンネル '{1}' は不正です": "{0}: invalid channel '{1}'.",
    "{0}: チャンネル '{1}' は不正です(有効: {2})":
        "{0}: invalid channel '{1}' (valid: {2}).",
    "{0}: end type '{1}' は不正です": "{0}: invalid end type '{1}'.",
    "{0}: end 'none' はイベントの end が channel 指定の場合のみ使えます":
        "{0}: end 'none' is only allowed when the event end is a channel condition.",
    "{0}: repeat の count は1以上にしてください":
        "{0}: repeat count must be at least 1.",
    "{0}: plays の count は1以上にしてください":
        "{0}: plays count must be at least 1.",
    "{0}: duration には minutes か seconds を指定してください":
        "{0}: duration requires minutes or seconds.",
    "{0}: random には end (duration) の指定が必要です":
        "{0}: random requires an end condition (duration).",
    "{0}: random の end は duration / plays (または none) のみ対応です":
        "{0}: with random, the end must be duration / plays (or none).",
    "{0}: random_bag の end は duration / plays / repeat (または none) のみ対応です":
        "{0}: with random_bag, the end must be duration / plays / repeat (or none).",
    "{0}: interval の指定が不正です": "{0}: invalid interval.",
    "{0}: interval の min/max は数値で指定してください":
        "{0}: interval min/max must be numbers.",
    "{0}: pan はオブジェクトで指定してください": "{0}: pan must be an object.",
    "{0}: pan には left と right を数値で指定してください":
        "{0}: pan requires numeric left and right.",
    "{0}: device の指定が不正です": "{0}: invalid device specification.",
    "{0}: device の種別 '{1}' は不正です(有効: {2})":
        "{0}: invalid device type '{1}' (valid: {2}).",
    "{0}: device の '{1}' に指定されたチャンネル '{2}' が存在しません":
        "{0}: device '{1}' refers to channel '{2}' which does not exist.",
    "{0}: tracks[{1}] はオブジェクトで指定してください":
        "{0}: tracks[{1}] must be an object.",
    "{0}: tracks[{1}] の type '{2}' は不正です(有効: {3})":
        "{0}: invalid type '{2}' in tracks[{1}] (valid: {3}).",
    "{0}: tracks[{1}] に funscript がありません":
        "{0}: tracks[{1}] has no funscript.",
    "{0}: transition には when と to が必要です":
        "{0}: transition requires when and to.",
    "{0}: when の channel '{1}' は不正です": "{0}: invalid when channel '{1}'.",
    "{0}: {1} には秒数を指定してください":
        "{0}: {1} requires seconds.",
    "{0}: channel_count には1以上の回数を指定してください":
        "{0}: channel_count requires a count of at least 1.",
    "{0}: to は文字列か {{\"random\": [...]}} で指定してください":
        "{0}: to must be a string or {{\"random\": [...]}}.",
    "{0} ステート'{1}': transition の channel '{2}' がこのステートにありません":
        "{0} state '{1}': transition channel '{2}' does not exist in this state.",
    "{0} ステート'{1}': 移行先 '{2}' が存在しません":
        "{0} state '{1}': transition target '{2}' does not exist.",
    "{0}: ステートを持つイベントには end (duration/plays/transitions) が必要です":
        "{0}: an event with states requires an end (duration/plays/transitions).",
    "{0}: ステートを持つイベントの end は duration/plays/transitions のみです":
        "{0}: an event with states only accepts duration/plays/transitions as end.",
    "{0}: {1} の count は1以上にしてください":
        "{0}: {1} count must be at least 1.",
    "{0}: end channel には channel を指定してください":
        "{0}: end channel requires a channel.",
    "{0}: end のチャンネル '{1}' が存在しません":
        "{0}: end channel '{1}' does not exist.",

    # ---- describe系(再利用のため残存) ----
    "({0}から抽選)": "(random from {0})",
    "{0}で→{1}": "{0} → {1}",
    "{0}chの終了で次へ": "Next when {0} finishes",
    "{0}周で終了": "End after {0} passes",
    "合計{0}回の実行で次へ": "Next after {0} plays total",
    "{0}回のステート移行で次へ": "Next after {0} transitions",
    "合計{0}を超えたら次へ": "Next after {0} total",
    "{0}chの合計{1:.0f}秒": "{1:.0f}s total on {0}",
    "{0}chの合計{1:.0f}〜{2:.0f}秒": "{1:.0f}–{2:.0f}s total on {0}",
    "{0}分{1}秒": "{0}m {1}s",
    "{0}分": "{0} min",
    "{0}秒": "{0} s",      # =210 縮尺表示

    # ---- ログ・内部メッセージ ----
    "音声の長さを取得できません: %s": "Could not determine audio duration: %s",
    "シーク失敗: %s": "Seek failed: %s",
    "音声の読み込み失敗: %s (%s)": "Failed to load audio: %s (%s)",
    "接続ロストのためfunscript実行を中断します":
        "Connection lost; stopping funscript execution.",
    "接続ロストのためrotate実行を中断します":
        "Connection lost; stopping rotate execution.",
    "接続ロストのためvibration実行を中断します":
        "Connection lost; stopping vibration execution.",
    "スキャン中にエラー: %s": "Error while scanning: %s",
    # =122: buttplug自衛パッチ
    "buttplug応答の処理でエラー(接続は継続します): %s":
        "Error while handling a buttplug response (connection stays alive): %s",
    "buttplug自衛パッチの適用に失敗: %s":
        "Failed to install the buttplug defensive patch: %s",
    "linearコマンド送信失敗: %s": "Failed to send linear command: %s",
    "twistコマンド送信失敗: %s": "Failed to send twist command: %s",
    "TCodeデバイス(シリアル直結)": "TCode device (direct serial)",
    "ボーレート": "Baud rate",
    "pyserialがインストールされていません":
        "pyserial is not installed",
    "pyserialがインストールされていません(pip install pyserial で有効化)":
        "pyserial is not installed (enable with: pip install pyserial)",
    "TCodeデバイスが接続されていません": "TCode device is not connected",
    "TCodeコマンド送信失敗: %s": "Failed to send TCode command: %s",
    "切断しました": "Disconnected",
    "ボーレートが数値ではありません": "Baud rate is not a number",
    "ポートを選択してください": "Select a port",
    "接続できません: {0}": "Cannot connect: {0}",
    "切断": "Disconnect",
    "接続中...": "Connecting...",
    "接続済: {0} (linear/twistはこのポートへ送られます)":
        "Connected: {0} (linear/twist are sent to this port)",
    "(COMポートは接続済: linear/twistはCOMポートへ送られます)":
        "(COM port connected: linear/twist are sent to the COM port)",
    "rotateコマンド送信失敗: %s": "Failed to send rotate command: %s",
    "rotate(a10)コマンド送信失敗: %s":
        "Failed to send rotate(a10) command: %s",
    "vibrationコマンド送信失敗: %s": "Failed to send vibration command: %s",
    "停止コマンド送信失敗: %s": "Failed to send stop command: %s",

    # ---- next分岐(2026-07-17) ----
    "{0}: next の指定が不正です": "{0}: invalid next specification.",
    "{0}: next の choice / cond 形式は将来のバージョンで対応予定です":
        "{0}: the choice / cond forms of next are reserved for a future version.",
    "{0}: next のオブジェクト形式には random (1件以上のリスト) が必要です":
        "{0}: the object form of next requires random (a non-empty list).",
    "{0}: next random[{1}] の要素が不正です":
        "{0}: invalid element in next random[{1}].",
    "{0}: next の visited は include か exclude にしてください":
        "{0}: next visited must be include or exclude.",
    '{0}: next の when_exhausted は "all" / "reset" / {{"to": "..."}} にしてください':
        '{0}: next when_exhausted must be "all" / "reset" / {{"to": "..."}}.',
    "(チェックなし=再生終了、複数チェック=重み付き抽選)":
        "(none = end of scenario, multiple = weighted random)",
    "分岐先の候補:": "Branch candidates:",
    "毎回すべて候補": "All candidates every time",
    "未実行イベントのみ候補": "Unvisited events only",
    "全候補が実行済みのとき:": "When all visited:",
    "以後、毎回すべて候補": "All candidates from then on",
    "リセットして再び、未実行イベントのみ候補":
        "Reset; unvisited events only again",
    "リセットして再び、未実行ステートのみ候補":
        "Reset; unvisited states only again",
    "指定イベントへ": "Go to a specific event",
    "イベント {0}: 重みが不正です": "Event {0}: invalid weight.",
    "イベント {0}: 「指定イベントへ」の行き先を選択してください":
        "Event {0}: select the target for \"Go to a specific event\".",
    "{0}: next random[{1}] の weight": "{0}: weight in next random[{1}]",
    "{0}: next の else はイベントIDの文字列で指定してください":
        "{0}: next 'else' must be an event ID string.",
    "重みが全て0のとき:": "When all weights are 0:",
    "(終了)": "(end)",

    # ---- 選択肢(2026-07-17) ----
    "選択してください": "Make a choice",
    "自動選択（ランダム）": "Auto-select (random)",
    "残り {0}:{1:02d}": "Time left {0}:{1:02d}",
    "分岐": "Branch",
    "選択肢": "Choices",
    "(ボタンで選ばれた遷移先へ進みます)": "(goes to the target chosen by button)",
    "＋ 選択肢を追加": "+ Add choice",
    "(最小1・最大9)": "(min 1, max 9)",
    "ボタンの表示テキスト": "button label",
    "選択肢は最低1つ必要です": "At least one choice is required.",
    "タイムリミット:": "Time limit:",
    "無制限": "Unlimited",
    "時間指定": "Timed",
    "デフォルト遷移先:": "Default destination:",
    "先頭の選択肢へ": "First choice",
    "選択肢から等確率で抽選": "Random pick among choices",
    "(タイムアウト時・▶▶スキップ時の行き先)":
        "(used on timeout and on ▶▶ skip)",
    "▶▶で飛ばさない(入力されるまで待機)":
        "Don't skip with ▶▶ (wait until a value is entered)",
    "(音声中の▶▶は音声だけ打ち切る)": "(▶▶ during audio only cuts the audio)",
    "▶▶は無効: 入力されるまで待機します(入力必須)":
        "▶▶ ignored: waiting for input (input required)",
    "▶▶: 音声を打ち切って数値入力の待機へ(入力必須)":
        "▶▶: audio cut, waiting for numeric input (input required)",
    "▶▶で飛ばさない(選択されるまで待機)":
        "Do not skip with ▶▶ (wait until a choice is made)",
    "(音声中の▶▶は音声だけ打ち切る。タイムアウトは進む)":
        "(▶▶ during audio only cuts the audio; timeout still advances)",
    '{0}: skip は "default" か "stay" で指定してください':
        '{0}: skip must be "default" or "stay"',
    "▶▶は無効: 選択されるまで待機します(選択必須)":
        "▶▶ ignored: waiting until a choice is made (choice required)",
    "▶▶: 音声を打ち切って選択肢の待機へ(選択必須)":
        "▶▶: audio cut, moving to the choice wait (choice required)",
    "表示タイミング:": "Show timing:",
    "イベント終了条件の達成時": "When the event end condition is met",
    "イベント開始時": "At event start",
    "イベント開始から指定時間後": "After a delay from event start",
    "イベント {0}: 選択肢{1}の表示テキストが空です":
        "Event {0}: label of choice {1} is empty.",
    "イベント {0}: 選択肢{1}の遷移先が不正です":
        "Event {0}: invalid target for choice {1}.",
    "イベント {0}: 選択肢は1〜9件にしてください":
        "Event {0}: the number of choices must be 1-9.",
    "イベント {0}: タイムリミットの時間が不正です":
        "Event {0}: invalid time limit.",
    "イベント {0}: デフォルト遷移先のイベントを選択してください":
        "Event {0}: select the default destination event.",
    "イベント {0}: 表示タイミングの時間が不正です":
        "Event {0}: invalid show-timing delay.",
    "{0}: choice は1〜9件のリストで指定してください":
        "{0}: choice must be a list of 1-9 entries.",
    "{0}: choice[{1}] には label と to が必要です":
        "{0}: choice[{1}] requires label and to.",
    "{0}: choice[{1}] の label が空です": "{0}: label of choice[{1}] is empty.",
    "{0}: timeout は minutes/seconds のオブジェクトで指定してください":
        "{0}: timeout must be an object with minutes/seconds.",
    "{0}: timeout の to はイベントIDの文字列にしてください":
        "{0}: timeout to must be an event id string.",
    '{0}: "default" と timeout の to は同時に指定できません':
        '{0}: "default" and timeout to cannot be used together.',
    '{0}: default は "random" か {{"to": "..."}} で指定してください':
        '{0}: default must be "random" or {{"to": "..."}}.',
    "{0}: timeout には minutes か seconds を指定してください":
        "{0}: timeout requires minutes or seconds.",
    "{0}: show の時間指定が不正です": "{0}: invalid show delay.",
    # ---- 変数(vars) ----
    "vars はオブジェクトで指定してください": "vars must be an object.",
    "vars '{0}'": "vars '{0}'",
    "vars: 変数名が不正です": "vars: invalid variable name.",
    "{0}: init が必要です": "{0}: init is required.",
    "{0}: 初期値は数値か文字列にしてください":
        "{0}: initial value must be a number or a string.",
    "{0}: min/max は数値変数のみ指定できます":
        "{0}: min/max are only allowed for numeric variables.",
    "{0}: {1} は数値にしてください": "{0}: {1} must be a number.",
    "{0}: max は min 以上にしてください": "{0}: max must be >= min.",
    "{0}: 初期値が min/max の範囲外です":
        "{0}: initial value is out of the min/max range.",
    "{0}: 変数 '{1}' が vars で宣言されていません":
        "{0}: variable '{1}' is not declared in vars.",
    '{0}: value は数値・文字列・{{"var": ...}} のいずれかにしてください':
        '{0}: value must be a number, a string, or {{"var": ...}}.',
    "{0}: 変数操作はリストで指定してください":
        "{0}: variable operations must be a list.",
    "{0} 操作[{1}]": "{0} op[{1}]",
    "{0}: roll は数値変数にのみ使えます":
        "{0}: roll can only be used on numeric variables.",
    "{0}: roll には min と max が必要です":
        "{0}: roll requires min and max.",
    "{0}: roll の min/max は数値にしてください":
        "{0}: roll min/max must be numeric.",
    "{0}: value が必要です": "{0}: value is required.",
    # =75: add/mul 共通のエラー(kind名を埋め込む)
    "{0}: {1} は数値変数にのみ使えます":
        "{0}: {1} can only be used on numeric variables.",
    "{0}: {1} の value は数値にしてください":
        "{0}: value of {1} must be numeric.",
    "{0}: 変数 '{1}' と value の型が一致しません":
        "{0}: type of value does not match variable '{1}'.",
    "{0}: when は1件以上の条件リストで指定してください":
        "{0}: when must be a list of one or more conditions.",
    "{0} 条件[{1}]": "{0} condition[{1}]",
    "{0}: 条件はオブジェクトで指定してください":
        "{0}: each condition must be an object.",
    "{0}: op は {1} のいずれかにしてください":
        "{0}: op must be one of {1}.",
    "{0}: 文字列変数の比較は == と != のみです":
        "{0}: string variables only support == and !=.",
    "{0}: cond は1件以上のリストで指定してください":
        "{0}: cond must be a list of one or more rows.",
    "{0} cond[{1}]": "{0} cond[{1}]",
    "{0}: when と to が必要です": "{0}: when and to are required.",
    '{0}: cond には "else"(どの条件も成立しない時の遷移先、null=再生終了) が必要です':
        '{0}: cond requires "else" (destination when no row matches; null = end of playback).',
    "{0}: else はイベントIDの文字列か null にしてください":
        "{0}: else must be an event id string or null.",
    "{0}: next の random / choice / cond / input は同時に指定できません":
        "{0}: random / choice / cond / input cannot be combined in next.",
    "{0} on_start": "{0} on_start",
    "{0} on_end": "{0} on_end",
    "{0} on_play": "{0} on_play",
    "{0} on_complete": "{0} on_complete",
    "{0} on_timeout": "{0} on_timeout",
    "{0} choice[{1}]": "{0} choice[{1}]",
    "変数: {0}": "Vars: {0}",
    "変数指定": "By variable",
    "変数:{0}": "var:{0}",
    "{0}: 変数 '{1}' は数値変数ではありません":
        "{0}: variable '{1}' is not a numeric variable.",
    # =123 すごろく(advance)
    '{0}: advance(すごろく)は1以上の整数か {{"var": 変数名}} で指定してください':
        '{0}: advance (sugoroku) must be an integer of 1 or more, or '
        '{{"var": name}}',
    "{0}: advance(すごろく)が指定されていますが、次のイベントがありません":
        "{0}: advance (sugoroku) is set but there is no next event",
    "{0}: advance(すごろく)は選択肢/数値入力の遷移には使えません":
        "{0}: advance (sugoroku) cannot be used with choice / numeric input "
        "transitions",
    "すごろく: {0} に止まる(選択肢/数値入力)":
        "Sugoroku: landing on {0} (choice / numeric input)",
    "すごろく: ゴール {0} に到達": "Sugoroku: reached the goal {0}",
    "すごろく: {0} で行き止まり(あがり扱いで終了)":
        "Sugoroku: dead end at {0} (treated as goal; ending)",
    "すごろく: {0} を通過": "Sugoroku: passed {0}",
    "すごろく": "Sugoroku",
    "進む歩数:": "Steps to advance:",
    "(N歩先まで進み、通過するイベントは再生されません)":
        "(Advances N steps; passed events are not played)",
    "すごろくの歩数の変数を選択してください":
        "Select a variable for the sugoroku step count",
    "すごろくの歩数は1以上の整数か変数で指定してください":
        "Sugoroku step count must be an integer of 1 or more, or a variable",
    # =124 ノード着色・図の視覚強化
    '{0}: color は "#RRGGBB" 形式の文字列で指定してください':
        '{0}: color must be a string in "#RRGGBB" format',
    "リセット": "Reset",
    # =125 ステート移行先の変数分岐(cond)
    "{0}: to の random と cond は同時に指定できません":
        "{0}: to cannot specify random and cond at the same time",
    "イベント終了: 移行先が決まらない(else未指定)":
        "Event end: no transition target resolved (no else)",
    "{0}(どの条件も不成立 → else)":
        "{0} (no condition matched → else)",
    "({0}へ判定式で分岐)": "(conditional branch to {0})",
    "上から順に評価し、最初に成立した行へ移行します。行内の条件はAND。":
        "Evaluated top to bottom; transitions to the first matching row. "
        "Conditions in a row are AND.",
    "(移行しない)": "(No transition)",
    "ステート移行先(判定式):": "Target state(s) (conditional):",
    "{0} 条件行{1}": "{0} condition row {1}",
    "{0}: 移行先が不正です": "{0}: invalid target state",
    "{0}: 条件行を1つ以上指定してください":
        "{0}: specify at least one condition row",
    # =126 変数操作「条件式」(eval)
    "{0}: set / add / mul / roll / eval のいずれか1つを指定してください":
        "{0}: specify exactly one of set / add / mul / roll / eval",
    "{0}: eval は数値変数にのみ使えます":
        "{0}: eval can only be used with numeric variables",
    '{0}: eval には when({{"var","op","value"}}) が必要です':
        '{0}: eval requires when ({{"var","op","value"}})',
    "条件式 {0} → {1}": "Condition {0} → {1}",
    "条件式": "Condition",
    "条件式は成立で1、不成立で0を代入します(対象は数値変数のみ)。":
        "A condition assigns 1 when true, 0 when false "
        "(numeric variables only).",
    "操作{0}: 条件式の変数を選択してください":
        "Op {0}: select a variable for the condition",
    "操作{0}: 条件式の値が不正です":
        "Op {0}: invalid condition value",
    # =130 担当種別トラック皆無の警告
    "{0}: チャンネル{1}は {2} の担当ですが、その種別のトラックを持つアイテムが1つもありません(このチャンネルはデバイスを動かしません)":
        "{0}: channel {1} is assigned to {2}, but no item has a track of "
        "that type (this channel will not drive any device)",
    "保存しますが、注意点があります":
        "Saved, but with warnings",
    '{0}: 数値か {{"var": "..."}} で指定してください':
        '{0}: specify a number or {{"var": "..."}}.',
    "watch はリストで指定してください": "watch must be a list.",
    "watch[{0}]": "watch[{0}]",
    "{0}: to はイベントIDの文字列で指定してください":
        "{0}: to must be an event id string.",
    '{0}: mode は "graceful" か "interrupt" にしてください':
        '{0}: mode must be "graceful" or "interrupt".',
    "{0}: once は true/false にしてください": "{0}: once must be true/false.",
    "watch[{0}] の to '{1}' が存在しません":
        "to '{1}' of watch[{0}] does not exist.",
    "変数分岐": "Variable branch",
    "(変数の条件で遷移します。編集画面では変更できません)":
        "(transitions by variable conditions; not editable in this editor)",
    '{0}: show は "start" / "end" / {{"minutes","seconds"}} にしてください':
        '{0}: show must be "start" / "end" / {{"minutes","seconds"}}.',
    "{0}: next の cond 形式は将来のバージョンで対応予定です":
        "{0}: the cond form of next is reserved for a future version.",
    # ---- 数値入力(next.input) ----
    "{0}: input はオブジェクトで指定してください":
        "{0}: input must be an object.",
    "{0} input": "{0} input",
    "{0}: input の to はイベントIDの文字列で指定してください":
        "{0}: input's to must be an event id string.",
    "{0}: input の min/max は数値で指定してください":
        "{0}: input's min/max must be numbers.",
    "{0}: input の min は max 以下にしてください":
        "{0}: input's min must not exceed max.",
    "{0}: input の min/max が変数 '{1}' の宣言範囲の外です":
        "{0}: input's min/max lie outside the declared range of variable '{1}'.",
    "数値を入力してください": "Enter a number",
    "決定": "OK",
    "(入力範囲: {0}〜{1})": "(range: {0}–{1})",
    "(入力範囲: {0}以上)": "(range: {0} or more)",
    "(入力範囲: {0}以下)": "(range: {0} or less)",
    "{0}〜{1}の数値を入力してください":
        "Enter a number between {0} and {1}.",
    "{0}以上の数値を入力してください": "Enter a number of {0} or more.",
    "{0}以下の数値を入力してください": "Enter a number of {0} or less.",
    "数値入力": "Number input",
    "(数値入力で変数をセットして遷移します。編集画面では変更できません)":
        "(sets a variable from a number input, then transitions; "
        "not editable in this editor)",
    # ---- 編集UIの変数対応(第4弾) ----
    "変数/監視": "Variables/Watch",
    "変数/監視({0})": "Variables/Watch ({0})",
    "変数と監視の管理": "Variables & Watches",
    "変数宣言": "Variable declarations",
    "型は「数値/文字列」で選択。最小/最大は数値のみ(全操作の結果がこの範囲に収まる)。\n"
    "名前の変更・削除は、その変数を使う操作・条件を自動では直しません(保存時の検証でエラーになります)。":
        "Choose number/string as the type. Min/max apply to numbers only "
        "(every operation result is clamped to this range).\n"
        "Renaming or deleting a variable does not fix operations/conditions "
        "that use it (validation on save will report errors).",
    "名前": "Name",
    "型": "Type",
    "初期値": "Initial",
    "最小": "Min",
    "最大": "Max",
    "数値": "Number",
    "文字列": "String",
    "＋ 変数を追加": "+ Add variable",
    "監視(watch)": "Watches",
    "変数条件が成立した瞬間に指定イベントへ遷移します(シナリオ全体で有効)。\n"
    "条件は変数操作の直後に評価され、遷移先のイベント再生中は評価されません。":
        "Jumps to the target event the moment the condition holds "
        "(applies to the whole scenario).\n"
        "Conditions are evaluated right after each variable operation, "
        "and are not evaluated while the target event is playing.",
    "＋ 監視を追加": "+ Add watch",
    "条件(AND):": "Conditions (AND):",
    "成立で→": "On match →",
    "再生中の音声を待って遷移": "Finish current audio, then jump",
    "即時打ち切りで遷移": "Interrupt and jump now",
    "1回の再生につき1度だけ": "Only once per playback",
    "OK": "OK",
    "キャンセル": "Cancel",
    "変数{0}: 名前が空です": "Variable {0}: name is empty.",
    "変数名 '{0}' が重複しています": "Variable name '{0}' is duplicated.",
    "変数 '{0}': 初期値が数値ではありません":
        "Variable '{0}': initial value is not a number.",
    "変数 '{0}': 最小/最大が数値ではありません":
        "Variable '{0}': min/max is not a number.",
    "変数 '{0}': 最小は最大以下にしてください":
        "Variable '{0}': min must not exceed max.",
    "変数 '{0}': 初期値が最小/最大の範囲外です":
        "Variable '{0}': initial value is outside min/max.",
    "監視{0}": "Watch {0}",
    "{0}: 遷移先のイベントを選択してください":
        "{0}: select the destination event.",
    "監視(watch)を使うには変数を1つ以上宣言してください":
        "Declare at least one variable to use watches.",
    "＋AND条件": "+ AND cond.",
    "{0}: 条件{1}の変数を選択してください":
        "{0}: select the variable of condition {1}.",
    "{0}: 条件{1}の値が不正です": "{0}: condition {1} has an invalid value.",
    "{0}: 条件を1つ以上指定してください":
        "{0}: specify at least one condition.",
    "変数操作 - {0}": "Variable operations - {0}",
    "上から順に実行されます。加算/乗算/乱数は数値変数のみ、負の値で減算。乱数は最小〜最大の整数を代入。":
        "Executed top to bottom. Add/Multiply/Random apply to number variables "
        "only; use a negative value to subtract. Random assigns an integer "
        "in [min, max].",
    "＋ 操作を追加": "+ Add operation",
    "セット": "Set",
    "加算": "Add",
    "乗算": "Multiply",
    "乱数": "Random",
    "操作{0}: 対象の変数を選択してください":
        "Operation {0}: select the target variable.",
    "操作{0}: 値が不正です": "Operation {0}: invalid value.",
    "操作{0}: 乱数の最小/最大が不正です":
        "Operation {0}: invalid Random min/max.",
    "変数操作({0})": "Var. ops ({0})",
    "変数操作:": "Variable ops:",
    "変数({0})": "Vars ({0})",
    "イベント {0}": "Event {0}",
    "ステート {0}": "State {0}",
    "選択肢{0}": "Choice {0}",
    "タイムアウト時": "On timeout",
    "イベント開始時(on_start)": "On event start (on_start)",
    "イベント終了時(on_end)": "On event end (on_end)",
    "イベント開始時({0})": "On event start ({0})",
    "開始{0}・終了{1}": "Start {0} / End {1}",
    "ステート開始時・再入ごと(on_start)":
        "On state start, incl. re-entry (on_start)",
    "ステート終了時(on_end)": "On state end (on_end)",
    "ステート開始時({0})": "On state start ({0})",
    "再生開始時(on_play)": "On item play (on_play)",
    "自然完了時のみ(on_complete)":
        "On natural completion only (on_complete)",
    "この選択肢が選ばれた時(ops)": "When this choice is picked (ops)",
    "タイムアウト確定時のみ(on_timeout)":
        "On timeout only (on_timeout)",
    "時間切れ時の変数操作({0})": "Timeout var. ops ({0})",
    "(◀◀での再実行でも発火します)":
        "(also fires when re-entered via ◀◀)",
    "(変数の条件で遷移先を決めます)":
        "(destination is decided by variable conditions)",
    "(数値を入力させて変数へセットし、遷移します)":
        "(asks for a number, sets it to a variable, then transitions)",
    "上から順に評価し、最初に成立した行へ遷移します。行内の条件はAND。":
        "Rows are evaluated top to bottom; the first matching row wins. "
        "Conditions within a row are AND.",
    "＋ 条件行を追加": "+ Add row",
    "どの行も成立しないとき(else):": "When no row matches (else):",
    "再生終了": "End playback",
    "条件行は最低1つ必要です": "At least one condition row is required.",
    "イベント {0} 条件行{1}": "Event {0} row {1}",
    "{0}: 遷移先が不正です": "{0}: invalid destination.",
    "イベント {0}: 条件行を1つ以上指定してください":
        "Event {0}: specify at least one condition row.",
    "入力先の変数:": "Target variable:",
    "見出し:": "Label:",
    "省略時「数値を入力してください」": 'default: "Enter a number"',
    "入力範囲:": "Input range:",
    "(空欄=変数宣言の最小/最大。決定した値は変数へセットされ即遷移)":
        "(blank = variable's min/max. The entered value is set to the "
        "variable and transitions immediately)",
    "イベント {0}: 入力先の数値変数を選択してください":
        "Event {0}: select a numeric target variable.",
    "イベント {0}: 数値入力の遷移先が不正です":
        "Event {0}: invalid destination for the number input.",
    "イベント {0}: 入力範囲(min/max)が不正です":
        "Event {0}: invalid input range (min/max).",
    "イベント {0}: 入力範囲はmin≦maxにしてください":
        "Event {0}: input range must satisfy min ≤ max.",
    "チャンネル{0}: 終了条件の変数を選択してください":
        "Channel {0}: select the variable of the end condition.",
    "イベント {0}: イベント終了条件の変数を選択してください":
        "Event {0}: select the variable of the event end condition.",
    # ---- ヘルプダイアログ ----
    # ---- =244: ヘルプ本文(全面改稿)+=245/=246: ユーザー推敲 ----
    "本アプリについて": "About this app",
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
    "・スクリプト制作支援":
        "A programmable audio player:\n"
        "build playlists by combining rules and procedures.\n"
        "\n"
        "For audio works that involve branching, randomness or game"
        " elements,\n"
        "define the listening rules in advance as a \"scenario\",\n"
        "and the app cuts down on manual switching and advances on its"
        " own.\n"
        "\n"
        "It offers the following features.\n"
        "\n"
        "- Branching progression\n"
        "\n"
        "- Weighted random draws\n"
        "\n"
        "- Playing 3 channels at once\n"
        "\n"
        "- Choice prompts\n"
        "\n"
        "- Variables\n"
        "\n"
        "- Sugoroku (board-game movement)\n"
        "\n"
        "- Video playback (mpv)\n"
        "\n"
        "- Haptic device sync\n"
        "\n"
        "- Script authoring support",
    "視聴方法": "How to play",
    "１．シナリオタブでシナリオを選択、または新規作成\n"
    "\n"
    "２．再生タブでシナリオ視聴":
        "1. On the Scenario tab, select a scenario or create a new one\n"
        "\n"
        "2. On the Play tab, play the scenario",
    "シナリオについて": "About scenarios",
    "本アプリにおいて、ユーザーが視聴する対象がシナリオです。\n"
    "\n"
    "シナリオの構造は\n"
    "「シナリオ - イベント - チャンネル - アイテム」\n"
    "となっています。":
        "In this app, a scenario is what you listen to.\n"
        "\n"
        "A scenario is structured as\n"
        "\"scenario - event - channel - item\".",
    "※ステート形式イベント": "* State-form events",
    "イベント内に複数の状態(ステート)を保持できます。\n"
    "イベントと同様、各ステートは3つのチャンネル(L/C/R)を持ち、\n"
    "遷移条件・遷移先ステートなどの定義を持ちます。":
        "An event can hold several states.\n"
        "Like an event, each state has three channels (L/C/R)\n"
        "and defines its own transition conditions and next state.",
    "イベントの集まりであり、視聴の対象です。\n"
    "実体はJSON形式のファイルです。":
        "A collection of events, and what you play.\n"
        "Physically it is a JSON file.",
    "ハプティクスデバイス連携": "Haptic device sync",
    "アイテムにスクリプト(funscript/csv)を紐づけることで、\n"
    "再生に同期してハプティクスデバイスを動かすことができます。\n"
    "シリアルポート接続とBluetooth接続(Intiface Central経由)が利用可能です。":
        "Bind a script (funscript/csv) to an item,\n"
        "and a haptic device moves in sync with playback.\n"
        "Serial port and Bluetooth (via Intiface Central) connections are"
        " available.",
    # =118〜=161の詳細ヘルプ(TIPS 34項目)の訳キーは=244で全削除した。
    # 以下は=244の新本文でも使われている残存キー(見出しはUI共通キーを再利用)。
    "音声ファイルのことです。\nシナリオを構成する最小単位です。":
        "An audio file.\nThis is the smallest unit of a scenario.",
    "ヘルプ":
        "Help",
    "複数のアイテムを保持し、\n再生順序などの定義を持ちます。":
        "Holds several items,\nalong with settings such as the playback order.",
    "3つのチャンネル(L/C/R)を保持し、\n終了条件や遷移先イベントなどの定義を持ちます。":
        "Holds three channels (L/C/R),\nalong with settings such as the end condition and the next event.",
    "アイテム":
        "Item",
    "閉じる": "Close",
    # ---- =249: 巨大wavの読み込み失敗の詳細説明 ----
    "このwavは{0}のため、読み込み時に形式変換が必要です。"
    "変換はデータ部が約512MiBを超えると空きメモリに関係なく"
    "失敗します(SDLの上限)。{1}のwavへ変換すると読み込めます"
    "(このファイルのデータ部: 約{2}MiB)。":
        "This wav is {0}, so it must be converted while loading."
        " The conversion fails once the data exceeds about 512 MiB,"
        " regardless of free memory (an SDL limit). Convert the file to"
        " a {1} wav and it should load (this file's data: about"
        " {2} MiB).",
    "メモリが不足しています(このwavの展開には約{0}MiB必要です)。"
    "ほかのアプリを閉じるか、音声ファイルを分割してください。":
        "Out of memory (decoding this wav needs about {0} MiB)."
        " Close other apps or split the audio file.",
    # ---- ライセンス・クレジット画面(HelpDialog の「ライセンス」ボタン) ----
    "ライセンス": "License",
    "ライセンス・クレジット": "License / Credits",
    "RVP(Random Voice Player)は MITライセンスのオープンソースソフトウェアです。\n"
    "本ソフトウェアは、以下のソフトウェア・素材を利用しています。":
        "RVP (Random Voice Player) is open-source software under the MIT License.\n"
        "It makes use of the following software and materials.",
    # ---- =139: サードパーティライセンス全文 ----
    "サードパーティライセンス全文": "Full third-party license texts",
    "THIRD-PARTY-LICENSES.txt が見つかりませんでした。\n"
    "配布物に同梱されているファイルを参照してください。":
        "THIRD-PARTY-LICENSES.txt was not found.\n"
        "Please refer to the file bundled with the distribution.",
    "RVP 本体": "RVP itself",
    # =292: 先頭行に公開バージョン({0}=rvp.__version__)
    "バージョン {0}\nMIT License\n© 2026 Torp":
        "Version {0}\nMIT License\n© 2026 Torp",
    "使用しているソフトウェア": "Software used",
    "音声再生\nLGPL-2.1 / © pygame-ce developers":
        "Audio playback\nLGPL-2.1 / © pygame-ce developers",
    "画面表示\nMIT License / © 2023 Tom Schimansky":
        "User interface\nMIT License / © 2023 Tom Schimansky",
    "デバイス通信(Buttplug)\nBSD-3-Clause / © 2022 Siege-Wizard":
        "Device communication (Buttplug)\nBSD-3-Clause / © 2022 Siege-Wizard",
    "編集画面ドラッグ&ドロップ\n"
    "MIT License / tkdnd は BSD スタイルライセンス":
        "Editor drag & drop\n"
        "MIT License / tkdnd is under a BSD-style license",
    "TCodeデバイス通信\n"
    "BSD-3-Clause / © 2001-2020 Chris Liechti":
        "TCode device communication\n"
        "BSD-3-Clause / © 2001-2020 Chris Liechti",
    "Python / Tcl・Tk": "Python / Tcl-Tk",
    "実行環境\n"
    "PSF License / Tcl・Tk License(BSD スタイル)":
        "Runtime environment\n"
        "PSF License / Tcl-Tk License (BSD-style)",
    "動画再生": "Video playback",
    "BIZ UDゴシック(SIL Open Font License)":
        "BIZ UDGothic (SIL Open Font License)",
    # ---- 保存時の外部素材(絶対パス)警告 ----
    "外部の素材ファイルがあります": "External asset files found",
    "({0}件)": " ({0})",   # =310: 見出しの件数(日本語は括弧直付け)
    "以下のファイルは保存先フォルダの外にあるため、"
    "絶対パスで保存されます。このシナリオを配布すると"
    "他の環境では再生できません。":
        "The files below are outside the save folder and will be saved as "
        "absolute paths. If you distribute this scenario, it will not play "
        "on other machines.",
    "はい(相対パスに書き換えて保存する)": "Yes (rewrite to relative paths and save)",
    "はい(絶対パスのまま保存する)": "Yes (save with absolute paths)",
    "いいえ(保存しない)": "No (do not save)",
    "素材をコピーして相対パスで保存": "Copy assets and save with relative paths",
    "相対パスに書き換えて保存します。次の処理を行います:":
        "The scenario will be saved with relative paths. "
        "The following will be done:",
    "外部の素材ファイル{0}個を、シナリオファイル(.json)と同じ"
    "フォルダへコピーします。":
        "{0} external asset file(s) will be copied into the same folder "
        "as the scenario file (.json).",
    "音声(wav)・動画には、同じフォルダで自動紐づけされる"
    "funscript/CSVがあれば、それも一緒にコピーします。":
        "For audio (wav) and video files, any funscript/CSV files auto-bound "
        "in the same folder will be copied along with them.",
    # =50: 素材コピーの容量表示(動画は容量が大きい)
    "コピーするファイルの合計サイズ: {0}":
        "Total size to copy: {0}",
    "コピーするファイルの合計サイズ: {0}"
    "(うち動画 {1})。動画は容量が大きいため、"
    "保存先の空き容量にご注意ください。":
        "Total size to copy: {0} (video: {1}). Videos are large - please "
        "check the free space on the destination drive.",
    "シナリオ内の参照パスを相対パスに書き換えます"
    "(元のファイルは削除しません)。":
        "Reference paths in the scenario will be rewritten to relative "
        "paths (the original files are not deleted).",
    "コピー先に同名で内容の異なるファイルが既にある場合は、"
    "何もせず中止します(内容が同一ならコピー不要として"
    "スキップします)。":
        "If a file with the same name but different content already exists "
        "at the destination, the operation is aborted without changes "
        "(identical files are skipped as already copied).",
    "はい(コピーして保存する)": "Yes (copy and save)",
    "コピーできません(同名ファイル)": "Cannot copy (name conflicts)",
    "コピー先に同名のファイルがあるため中止しました。"
    "ファイル名を変更するか、既存ファイルを整理してください:":
        "Aborted because files with the same name exist at the "
        "destination. Rename the files or clean up the existing ones:",
    "{0} と {1} が同名です": "{0} and {1} have the same name",
    "{0} が既にあります": "{0} already exists",
    "シークバー追従": "Seek bar follows",
    "{0}: seek_channel は定義済みのチャンネル(L/C/R)を"
    "指定してください":
        "{0}: seek_channel must be one of the defined channels (L/C/R).",
    "コピーできません": "Cannot copy",
    "素材のコピーに失敗したため保存を中止しました"
    "(コピー済みのファイルは残りますが、シナリオの参照は"
    "書き換えていません):\n{0}":
        "Saving was aborted because copying assets failed (files copied so "
        "far are left in place, but no references in the scenario were "
        "rewritten):\n{0}",
    "ファイルへ書き込めませんでした(他のアプリで使用中・"
    "読み取り専用・アクセス権なし等の可能性):\n{0}":
        "Could not write to the file (it may be in use by another "
        "application, read-only, or access was denied):\n{0}",
    "保存しました(素材をコピー)": "Saved (assets copied)",
    "{0}個の素材を保存先フォルダへコピーし、"
    "参照を相対パスに書き換えました:":
        "Copied {0} asset file(s) into the save folder and rewrote the "
        "references to relative paths:",

    # ---- シナリオ新規作成ボタン(=45) ----
    "新規作成": "New scenario",

    # ---- 他のシナリオからのイベント取り込み(=41) ----
    "インポート": "Import",
    "取り込み元のシナリオを選択": "Select the scenario to import from",
    "イベントの取り込み": "Import events",
    "取り込み元: {0}": "Source: {0}",
    "全選択": "Select all",
    "全解除": "Clear all",
    "参照先も選択": "Select referenced",
    "取り込み": "Import",
    "取り込むイベントを選択してください": "Select events to import",
    "読み込みエラー": "Load error",
    "取り込み元にイベントがありません": "The source scenario has no events",
    "取り込み完了": "Import finished",
    "{0} 件のイベントを取り込みました": "Imported {0} event(s)",
    "(ID重複のため改名: {0})": " (renamed due to ID conflicts: {0})",
    "(変数を追加: {0})": " (variables added: {0})",

    # ---- 音声なしイベント/ステート(分岐専用ノード) ----
    "(音声なし=即座に次へ)": "(No audio = advances immediately)",
    "(音声なし=即座に通過)": "(No audio = passes through immediately)",
    "音声なしイベントの無限ループの可能性があります":
        "Possible infinite loop of no-audio events",
    "{0}: 音声なし(チャンネルなし)に device は指定できません":
        "{0}: device cannot be specified for a no-audio (no channels) "
        "event/state",
    "{0}: 音声なし(チャンネルなし)のイベントの終了条件は経過時間(duration)のみ指定できます":
        "{0}: a no-audio (no channels) event may only use the elapsed-time "
        "(duration) end condition",
    "{0}: 音声なしステートの移行条件は「なし」「経過時間」「判定式(cond)」「選択肢」のみ使用できます":
        "{0}: a no-audio state may only use no transition, elapsed time, "
        "a condition (cond) transition, or a choice transition",
    "(音声なし=指定秒だけ待機)": "(No audio = waits for the given seconds)",
    "経過時間でステート移行": "By elapsed time",
    "経過{0:g}秒": "{0:g}s elapsed",
    "経過{0:g}〜{1:g}秒": "{0:g}-{1:g}s elapsed",
    "経過{0}秒": "{0}s elapsed",
    "経過{0}〜{1}秒": "{0}-{1}s elapsed",

    # ---- 編集UI: 未翻訳残の一掃(2026-07-22) ----
    # イベント枠
    "■ イベント": "■ Event",
    "イベント名:": "Event name:",
    "イベント名": "Event name",
    # イベント終了条件コンボ(EVEND_*)
    "全チャンネルが終了した時": "When all channels finish",
    "指定チャンネルが終了した時": "When a selected channel finishes",
    "合計時間が経過した時": "After a total time elapses",
    "変数条件が成立した時": "When a variable condition is met",
    "ステートのイベント終了に委譲": "Delegated to the states' event end",
    "(累積・範囲は抽選)": "(accumulated, random)",
    "終了する判定式(すべて成立で終了):":
        "End condition expressions (ends when all hold):",
    # ステート枠
    "■ ステート": "■ States",
    "このイベントはステートを使っていません。\n"
    "「ステート形式に変換」で複数ステートの\n"
    "編集ができます。":
        "This event does not use states.\n"
        "Use \"Convert to states\" to edit\n"
        "multiple states.",
    "ステート図(○で選択)": "State diagram (click a circle to select)",
    "＋追加": "+ Add",
    "削除": "Delete",
    "ステート名:": "State name:",
    "ステート名": "State name",
    "(累積)": "(accumulated)",
    "終了とみなすステート(複数可):": "Ending state(s) (multiple allowed):",
    "移行する判定式(すべて成立で移行):":
        "Transition expressions (moves when all hold):",
    # ステート形式のイベント終了コンボ(EV_END_*)
    "変数条件で次へ": "Next when a variable condition is met",
    "指定ステートが終了したら次へ": "Next when a selected state ends",
    # ステート移行コンボ(TRANS_*)
    "判定式でステート移行": "By variable condition",
    "全チャンネル終了でステート移行": "When all channels finish",
    "指定チャンネル終了でステート移行": "When a selected channel finishes",
    # 移行要約・describe系
    "変数{0}": "variable {0}",
    "判定式({0})": "condition ({0})",
    "全チャンネル終了": "all channels finished",
    "{0}ch終了": "ch {0} finished",
    "、": ", ",
    "全チャンネル終了で→{0}": "all channels finished → {0}",
    "{0}ch終了で→{1}": "ch {0} finished → {1}",
    "判定式({0})の成立で次へ": "Next when condition ({0}) holds",
    "ステート{0}の終了で次へ": "Next when state {0} ends",
    "合計{0}秒を超えたら次へ": "Next after {0}s total",
    "合計{0:g}秒を超えたら次へ": "Next after {0:g}s total",
    "合計{0}〜{1}秒(抽選)を超えたら次へ":
        "Next after {0}–{1}s total (randomized)",
    "合計(変数{0})秒を超えたら次へ": "Next after (variable {0}) sec total",
    # 汎用UI
    "描画中…": "Rendering...",
    "実行": "Proceed",
    "削除する": "Delete",
    "リネームできません": "Cannot rename",
    "{0}を空にはできません": "{0} cannot be empty.",
    "{0}「{1}」は既に使われています": "{0} \"{1}\" is already in use.",
    "シナリオの検証でエラーが見つかりました:\n{0}":
        "Scenario validation found errors:\n{0}",
    # 編集エラー(検証メッセージ)
    "{0} 移行条件": "{0} transition condition",
    "イベント {0} 終了条件": "Event {0} end condition",
    "イベント {0}: イベント終了条件の対象チャンネル {1} が有効ではありません":
        "Event {0}: end-condition channel {1} is not enabled.",
    "イベント {0}: イベント終了条件の対象チャンネル {1} には有限の終了条件(無限以外)を設定してください":
        "Event {0}: end-condition channel {1} needs a finite end "
        "condition (not infinite).",
    "イベント {0}: イベント終了条件の値(min/max)が不正です":
        "Event {0}: invalid event end value (min/max).",
    "イベント {0}: イベント終了条件の秒数は0以上(最大は正の数)にしてください":
        "Event {0}: event end seconds must be 0 or more "
        "(max must be positive).",
    "イベント {0}: 終了とみなすステートを1つ以上選択してください":
        "Event {0}: select at least one ending state.",
    "{0}: 全チャンネル終了で移行するには、全チャンネルに有限の終了条件(無限以外)を設定してください":
        "{0}: to transition when all channels finish, every channel needs "
        "a finite end condition (not infinite).",
    "{0}: 指定チャンネル終了で移行するには、対象チャンネル {1} に有限の終了条件(無限以外)を設定してください":
        "{0}: to transition when channel {1} finishes, it needs a finite "
        "end condition (not infinite).",
    # シナリオ検証エラー(scenario.py)
    '{0}: show は "start" / "end" / {{"seconds"}} にしてください':
        '{0}: show must be "start" / "end" / {{"seconds"}}.',
    "{0}: duration には seconds を指定してください":
        "{0}: duration requires seconds.",
    "{0}: duration の min 秒は0以上にしてください":
        "{0}: duration min seconds must be 0 or more.",
    "{0}: duration の max 秒は0以上にしてください":
        "{0}: duration max seconds must be 0 or more.",
    "{0}: duration の秒数(min/max)は正の数にしてください":
        "{0}: duration seconds (min/max) must be positive.",
    # =166: イベントの遷移方法「固定」「なし」
    "固定": "Fixed",
    "遷移先:": "Go to:",
    "(再生が終わると、指定したイベントへ進みます)":
        "(when playback ends, goes to the event you chose)",
    "(遷移しません。再生はここで終わります)":
        "(no transition; playback ends here)",
    "イベント {0}: 遷移先を選択してください": "Event {0}: choose the target event.",
    # =168: 終了条件「無限」
    "判定式(常に監視)": "Condition (always watching)",
    "(再生中ずっと判定し、成立した行へ進みます)":
        "(evaluated throughout playback; goes to the first matching row)",
    "無限(選択肢・判定式・手動操作で次へ)":
        "Infinite (advance via choice, condition or manual control)",
    "判定式が成立: {0}へ": "A condition matched: going to {0}",
    "イベント '{0}': 終了条件が「無限」のときは固定の遷移先を指定できません(選択肢・数値入力・判定式・遷移なしのいずれかにしてください)":
        "Event '{0}': a fixed target cannot be used when the end condition is "
        "\"infinite\" (use a choice, a number input, a condition, or no transition).",
    "イベント '{0}': 終了条件が「無限」のときはランダム分岐を指定できません(選択肢・数値入力・判定式・遷移なしのいずれかにしてください)":
        "Event '{0}': a random branch cannot be used when the end condition is "
        "\"infinite\" (use a choice, a number input, a condition, or no transition).",
    # =167: ステート移行先「固定」
    "ステート移行先:": "Transition to:",
    "{0}: 移行先を選択してください": "{0}: choose the target state.",
    # =165: 廃止した channel_time 移行条件
    "{0}: when type 'channel_time' は廃止しました。'state_time'(経過時間)を使ってください":
        "{0}: the when type 'channel_time' has been removed. "
        "Use 'state_time' (elapsed time) instead.",
    "移行条件を読み替えました": "The transition condition was converted",
    "ステート {0}: 廃止した「チャンネル時間でステート移行」を「経過時間でステート移行」として開きました。保存し直すと新しい形式になります。":
        "State {0}: the removed \"by channel time\" transition was opened as "
        "\"by elapsed time\". Saving again writes it in the new form.",
    "{0}: when type '{1}' は不正です(有効: {2}, {3}, {4}, {5}, {6}, {7})":
        "{0}: invalid when type '{1}' (valid: {2}, {3}, {4}, {5}, {6}, {7}).",
    "{0}: ステートを持つイベントには end (duration/plays/transitions/cond/states/none) が必要です":
        "{0}: an event with states requires end "
        "(duration/plays/transitions/cond/states/none).",
    "{0}: ステートを持つイベントの end は duration/plays/transitions/cond/states/none のみです":
        "{0}: end of an event with states must be "
        "duration/plays/transitions/cond/states/none.",
    "{0}: end states には終了ステートIDを1つ以上指定してください":
        "{0}: end states requires at least one state id.",
    "{0}: end states の '{1}' が存在しません":
        "{0}: end states '{1}' does not exist.",
    "{0}: timeout は seconds のオブジェクトで指定してください":
        "{0}: timeout must be an object with seconds.",
    "{0}: timeout には seconds を指定してください":
        "{0}: timeout requires seconds.",
    # player.py
    "rotate動作元の読み込みに失敗しました: %s":
        "Failed to load the rotate source: %s",

    # ---- 再生タブ: イベント状態ビュー(2026-07-22) ----
    "(シナリオを読み込むとイベント図を表示します)":
        "(Load a scenario to show the event map)",
    # ---- 再生タブ: 変数・イベントログビュー(2026-07-22) ----
    "変数(key: value)": "Variables (key: value)",
    "イベントログ(通過したイベント・ステート)":
        "Event log (events/states passed)",
    "(変数なし)": "(no variables)",

    # ---- スクリプト専用チャンネル(2026-07-24) ----
    # scenario.py(検証エラー)
    "{0}: スクリプトのみのアイテムには funscript/CSV のトラックが1つ以上必要です":
        "{0}: a script-only item needs at least one funscript/CSV track.",
    "{0}: 音声つきアイテムとスクリプトのみアイテムは同じチャンネルに混在できません":
        "{0}: audio items and script-only items cannot be mixed "
        "in the same channel.",
    "{0}: 音声を再生するチャンネルが1つもありません(スクリプト専用チャンネルだけでは作れません)":
        "{0}: no channel plays audio (script-only channels alone "
        "are not allowed).",
    "{0}: スクリプト専用チャンネル '{1}' がどのデバイス種別の担当にもなっていません(device で担当を指定してください)":
        "{0}: script-only channel '{1}' is not assigned to any device type "
        "(assign it via device).",
    "{0}: seek_channel にスクリプト専用チャンネル '{1}' は指定できません":
        "{0}: seek_channel cannot be the script-only channel '{1}'.",
    "スクリプトを読み込めません({0}): {1}":
        "Cannot read the script ({0}): {1}",
    "スクリプトの長さが0です(アクションがありません): {0}":
        "The script length is zero (no actions): {0}",
    # player.py
    "スクリプトの読み込み失敗: %s (%s)":
        "Failed to load the script: %s (%s)",
    "スクリプトの長さが0のため1秒として扱います: %s":
        "Script length is zero; treating it as 1 second: %s",
    # =52 フェーズ3-B(動画チャンネル)で追加
    "＋動画": "+ Video",
    # =56: 再生ログ(設定値の可視化・終了理由)
    "{0}  [終了条件: {1}]": "{0}  [end: {1}]",
    "{0}  [移行条件: {1}]": "{0}  [transition: {1}]",
    "{0}担当": "drives {0}",
    "合計{0:g}〜{1:g}秒(今回{2:g}秒)":
        "total {0:g}-{1:g}s (this time {2:g}s)",
    "合計{0:g}秒": "total {0:g}s",
    "合計{0}回再生": "{0} plays in total",
    "ステート移行{0}回": "{0} state transitions",
    "チャンネル{0}の終了": "channel {0} ends",
    "変数条件の成立": "variable condition met",
    "ステート{0}の終了": "state {0} ends",
    "全チャンネルの終了": "all channels end",
    "{0}回再生で終了": "end after {0} plays",
    "{0:g}秒で終了": "end after {0:g}s",
    "無限": "Infinite",   # =169: 終了条件コンボのラベルにも使う
    "スクリプト": "script",
    "{0}アイテム": "{0} items",
    "間隔{0:g}〜{1:g}秒": "interval {0:g}-{1:g}s",
    "合計時間 {0:g}秒に到達": "reached the total time of {0:g}s",
    "合計{0}回の再生に到達": "reached {0} plays in total",
    "変数条件が成立": "variable condition met",
    "チャンネル{0}が終了": "channel {0} ended",
    "イベント終了: 監視(watch)が成立": "Event end: a watch condition fired",
    "ステート移行: {0} → {1}": "State transition: {0} -> {1}",
    "移行条件が成立": "transition condition met",
    "イベント終了: {0}": "Event end: {0}",
    "終了条件が成立": "end condition met",
    "イベント終了: 全チャンネルが終了": "Event end: all channels finished",
    "イベント終了: ステート{0}が終了": "Event end: state {0} finished",
    "イベント終了: ステート移行{0}回に到達":
        "Event end: reached {0} state transitions",
    # =70: 変数操作・選択肢/数値入力のログ
    "代入 {0}": "set {0}",
    "加算 {0}": "add {0}",
    "乗算 {0}": "multiply by {0}",
    "乱数 {0}〜{1}": "random {0}-{1}",
    "上限で丸め": "clamped to max",
    "下限で丸め": "clamped to min",
    # (「イベント開始時」「タイムアウト時」「数値入力」は編集UIで既に登録済み)
    "イベント終了時": "At event end",
    "ステート開始時": "At state start",
    "ステート終了時": "At state end",
    "アイテム再生時": "At item start",
    "アイテム完了時": "At item complete",
    "選択時": "On choice",
    "linear位置の追従に失敗しました":
        "Failed to move the linear device to the new range",
    "twist位置の追従に失敗しました":
        "Failed to retarget twist position",
    "選択肢を表示: {0}": "Choices shown: {0}",
    "制限{0:g}秒": "limit {0:g}s",
    "既定={0}": "default={0}",
    "ランダム": "random",
    "数値入力を表示: {0}": "Number input shown: {0}",
    "範囲 {0}〜{1}": "range {0}-{1}",
    "数値入力を確定: {0} → {1}": "Number input confirmed: {0} -> {1}",
    "選択肢を選択: {0}": "Choice selected: {0}",
    "選択肢がタイムアウト: {0}": "Choice timed out: {0}",
    "音声の読み込みに失敗しました: {0}": "Failed to load the audio: {0}",
    # =58: 編集画面のイベント遷移図の折りたたみ
    "▼ 折りたたみ": "▼ Collapse",
    "▶ 折りたたみ中": "▶ Collapsed",
    # =72: イベント遷移図の自動フィット
    "図の高さに合わせる": "Fit to map height",
    # =299: イベント図の配置モード
    "配置：自動": "Layout: auto",
    "配置：手動": "Layout: manual",
    # =300: イベント遷移図のドッキング解除
    "ドッキング解除": "Undock",
    "ドッキング": "Dock",
    "イベント遷移図": "Event map",
    'map_mode は "auto" か "manual" で指定してください':
        'map_mode must be "auto" or "manual"',
    '{0}: pos は [x, y] の数値2つで指定してください':
        '{0}: pos must be two numbers [x, y]',
    # =57: 手動操作(▶▶/◀◀)の遷移理由
    "イベント終了: 手動でスキップ(▶▶)": "Event end: skipped manually (>>)",
    "イベント終了: 手動で巻き戻し(◀◀)": "Event end: rewound manually (<<)",
    "選択肢を手動でスキップ(▶▶): {0}":
        "Choice skipped manually (>>): {0}",
    "数値入力を手動でスキップ(▶▶) → {0}":
        "Number input skipped manually (>>) -> {0}",
    # =54: 再生ログの警告
    "区間の開始({0:g}秒)が動画の長さ({1:.1f}秒)を超えています: {2}":
        "Range start ({0:g}s) is past the end of the video ({1:.1f}s): {2}",
    "スクリプトのみ": "script-only",
    "と": " and ",
    "{0} {1}本": "{0} x{1}",
    "{0}: 同じアイテムに audio と video は指定できません":
        "{0}: an item cannot have both audio and video",
    "{0}: 動画を置けるチャンネルは1つだけです(動画は同時に1本。{1} に指定されています)":
        "{0}: only one channel may hold video (one video at a time; found in {1})",
    "{0}: 動画を置くチャンネルの空きがありません(動画は L/C/R のどれか1つを使います。どれか1チャンネルを空けてください)":
        "{0}: no free channel for the video (video uses one of L/C/R; please free up a channel)",
    "{0}: {1} のアイテムは同じチャンネルに混在できません(チャンネル単位でどれか1種類にしてください)":
        "{0}: {1} items cannot be mixed in one channel (use a single kind per channel)",
    # scenario.py(動画対応=48: 検証エラー)
    "{0}: video のファイルを指定してください":
        "{0}: specify the video file.",
    "{0}: video の file を指定してください":
        "{0}: video requires a file.",
    "{0}: video の loop は true/false にしてください":
        "{0}: video loop must be true/false.",
    "{0}: video の指定が不正です": "{0}: invalid video specification.",
    "{0}: 動画があるときシークバーは動画に固定されるため seek_channel は指定できません":
        "{0}: seek_channel cannot be used with video "
        "(the seek bar is fixed to the video).",
    "{0}: チャンネルの無い動画ステートに all_channels 移行は使えません":
        "{0}: an all_channels transition cannot be used in a video state "
        "with no channels.",
    "{0}: ステート形式では video は各ステートに指定してください":
        "{0}: in the states form, specify video on each state.",
    "{0}: video と items 直書き形式は併用できません(channels を使ってください)":
        "{0}: video cannot be combined with the inline items form "
        "(use channels).",
    "{0}: ループ動画には end(終了条件)の指定が必要です(ループは動画終了で終わらないため)":
        "{0}: a looping video requires an end condition "
        "(a loop never ends by itself).",
    "動画ファイルが見つかりません: {0}": "Video file not found: {0}",
    # editor.py(動画対応=48: 動画行・相関制御)
    "動画:": "Video:",
    "(なし=クリックで選択)": "(none - click to select)",
    "ループ再生": "Loop playback",
    "動画ファイルを選択": "Select a video file",
    "動画ファイル": "Video files",
    "自動: 紐づくfunscriptなし(動画のみ)":
        "Auto: no funscript bound (video only)",
    # =51 フェーズ3: 動画の区間指定(start/end)
    "区間:": "Range:",
    "先頭": "start",
    "末尾": "end",
    "(空欄=素材全体・区間の先頭が0秒)":
        "(empty = whole file; the range start counts as 0s)",
    "{0}: 区間は秒数(数値)で指定してください":
        "{0}: the range must be given in seconds (numbers).",
    "{0}: 区間の開始は0以上にしてください":
        "{0}: the range start must be 0 or greater.",
    "{0}: 区間の終了は開始より後にしてください":
        "{0}: the range end must be later than the start.",
    # =59: 区間指定を音声・スクリプトへ拡張(汎用メッセージ)
    "連動": "linked",
    "{0} トラック{1}": "{0} track {1}",
    "{0} tracks[{1}]": "{0} tracks[{1}]",
    "{0}: {1} はオブジェクトで指定してください":
        "{0}: {1} must be an object.",
    "{0}: {1} の {2} は秒数(数値)で指定してください":
        "{0}: {1} {2} must be given in seconds (a number).",
    "{0}: {1} の start は0以上にしてください":
        "{0}: {1} start must be 0 or greater.",
    "{0}: {1} の end は start より後にしてください":
        "{0}: {1} end must be later than start.",
    "区間の開始({0:g}秒)が音声の長さ({1:g}秒)を超えています: {2}":
        "The range start ({0:g}s) is past the end of the audio ({1:g}s): {2}",
    "区間の開始({0:g}秒)がスクリプトの長さ({1:g}秒)を超えています: {2}":
        "The range start ({0:g}s) is past the end of the script ({1:g}s): {2}",
    "指定した区間にスクリプトの動作がありません: {0}":
        "The chosen range contains no script actions: {0}",
    # =50: 動画トラックのGUI編集(検証エラーのアイテム名フォールバック)
    "動画": "Video",
    "動画が終わった時": "When the video ends",
    # main.py(動画対応=48: mpvパス設定・動画表示)
    "動画プレーヤー(mpv)の設定": "Video player (mpv) settings",
    "(空欄=自動で探す)": "(empty = auto-detect)",
    "参照...": "Browse...",
    "テスト": "Test",
    "mpv の実行ファイルを選択": "Select the mpv executable",
    "実行ファイル": "Executable",
    "mpv が見つかりました: {0}": "Found mpv: {0}",
    "mpv が見つかりません。パスを指定してください":
        "mpv not found. Please set the path.",
    "★動画: {0}": "★Video: {0}",
    # mpv_client.py(動画対応フェーズ0)
    "mpv が見つかりません。設定で mpv のパスを指定してください":
        "mpv not found. Set the mpv path in the settings.",
    "mpv を起動しました: %s": "Launched mpv: %s",
    "mpv が起動直後に終了しました": "mpv exited right after launch.",
    "mpv のIPC接続がタイムアウトしました: {0}":
        "Timed out connecting to the mpv IPC endpoint: {0}",
    "mpv IPC受信エラー: %s": "mpv IPC read error: %s",
    "mpv との接続が切れました": "Lost the connection to mpv.",
    "mpv に接続していません": "Not connected to mpv.",
    # player.py / scenario_map.py(動画対応フェーズ2)
    "mpv が繰り返し終了するため停止しました":
        "Stopped because mpv keeps exiting.",
    "mpv との接続が切れました。再起動して復帰します":
        "Lost the connection to mpv. Restarting it to recover.",
    "選択肢が表示されています(RVPウィンドウで選択)":
        "A choice is waiting (select in the RVP window)",
    "数値入力が表示されています(RVPウィンドウで入力)":
        "A number input is waiting (enter it in the RVP window)",
    "▶動画": "▶Video",
    # editor.py
    "(スクリプト)": "(script)",
    "＋スクリプト": "+ Script",
    "スクリプトファイルを選択": "Select script files",
    # =160: 追加ボタンの右に出す控えめな告知(D&Dで追加できること)。
    # 幅の狭いチャンネル枠に収めるため、英語も短い表記にする。
    "(D&D可)": "(or drop)",
    # ---- =63: スクリプトのみイベント ----
    "{0}: スクリプト専用チャンネルだけのイベントに「N回の再生で次へ」は使えません(再生回数は音声・動画のみ数えます)":
        "{0}: the \"advance after N plays\" end condition cannot be used for "
        "an event made only of script-only channels (plays counts audio and "
        "video only)",
    # ---- =164: アイテムレビュー画面 ----
    # ボタンはアイテム行の狭い見出しに入るので英語も短く保つ。
    "レビュー": "Review",
    "アイテムのレビュー - RVP": "Item review - RVP",
    "区間 {0}": "Range {0}",
    "{0}〜{1}": "{0}–{1}",
    "(区間指定なし)": "(no range set)",
    "音声を再生できません: {0}": "Cannot play this audio: {0}",
    "スクリプトが見つかりません: {0}": "Script file not found: {0}",
    # 「スクリプトを読み込めません({0}): {1}」は player 用に登録済みのものを流用する
    "区間の指定が不正なので、素材全体を対象にします":
        "The range is invalid, so the whole file is used.",
    "トラックの区間の指定が不正なので、アイテムの区間に連動させます":
        "A track range is invalid, so it follows the item range instead.",
    # ---- =262: 背景イラスト ----
    "背景": "Backdrop",
    "透け具合": "Opacity",
    "背景イラスト": "Background image",
    "再生画面全体の背景に表示するイラストです(png/jpg等)。"
    "表示のON/OFFは視聴する人がメイン画面の設定で切り替えられます。":
        "An illustration shown behind the whole playback screen (png/jpg "
        "etc.). Viewers can turn it on/off in the main-window settings.",
    "画像を選ぶ…": "Choose image…",
    "クリア(背景なし)": "Clear (no image)",
    "画像ファイル": "Image files",
    "暗さ(%)": "Dim (%)",
    "0=画像を最も強く表示 〜 100=真っ黒(既定40。40より下げるほど画像の主張が強くなります)":
        "0 = strongest image, 100 = black (default 40; below 40 the image "
        "shows through more strongly)",
    "暗さ(%)は 0〜100 の整数で指定してください":
        "Dim (%) must be an integer between 0 and 100",
    "背景画像を選択": "Select background image",
    "表示する": "Show",
    "background は文字列かオブジェクトで指定してください":
        "background must be a string or an object",
    "background: file を指定してください":
        "background: file is required",
    "background: dim は 0〜100 の数値で指定してください":
        "background: dim must be a number between 0 and 100",
    "背景画像ファイルが見つかりません: {0}":
        "Background image file not found: {0}",
    "背景イラスト表示(画像の読み込み・加工)\n"
    "MIT-CMU License / © 2010 Jeffrey A. Clark and contributors":
        "Background image display (image loading / processing)\n"
        "MIT-CMU License / © 2010 Jeffrey A. Clark and contributors",

    # =275: 選択肢でステート移行
    "選択肢でステート移行": "By choice",
    "ステート内の全チャンネル終了時": "When all channels in the state end",
    "ステート開始から指定時間後": "After a delay from state start",
    "(タイムアウト時の行き先)": "(destination on timeout)",
    "(選択肢の表示中は▶▶を無効にする。タイムアウトは進む)":
        "(disables ▶▶ while the choices are shown; timeout still advances)",
    "(選択/タイムアウトで即座に移行。全チャンネルが終わっても選ばれるまで待機)":
        "(transitions immediately on choice/timeout; waits even after all "
        "channels end)",
    "(音声なし=選択されるまで待機)": "(No audio = waits for a choice)",
    "{0}: 選択肢{1}の表示テキストが空です":
        "{0}: choice {1} has an empty button text",
    "{0}: 選択肢{1}の行き先が不正です":
        "{0}: choice {1} has an invalid destination",
    "{0}: 選択肢は1〜9件にしてください":
        "{0}: provide 1 to 9 choices",
    "{0}: タイムリミットの時間が不正です":
        "{0}: invalid time limit",
    "{0}: デフォルト遷移先のステートを選択してください":
        "{0}: select a state for the default destination",
    "{0}: デフォルト遷移先のイベントを選択してください":
        "{0}: select an event for the default destination",
    "{0}: 表示タイミングの時間が不正です":
        "{0}: invalid show-timing delay",
    "イベント:{0}": "event:{0}",
    "選択肢({0})": "choice ({0})",
    "選択肢({0})で→{1}": "choice ({0}) -> {1}",
    "イベント終了: 選択肢の遷移先イベント {0} へ":
        "Event end: choice leads to event {0}",
    "選択肢を閉じる(イベント終了条件が成立)":
        "Choices closed (event end condition met)",
    "{0} transition": "{0} transition",
    "{0} default": "{0} default",
    "{0}: 行き先はイベントIDの文字列で指定してください":
        "{0}: the destination must be an event ID string",
    "{0}: 行き先はステートIDの文字列か {{\"event\": \"イベントID\"}} で指定してください":
        "{0}: the destination must be a state ID string or "
        "{{\"event\": \"eventID\"}}",
    "{0}: 選択肢(choice)のステート移行では to は指定できません(行き先は各選択肢に書きます)":
        "{0}: a choice transition cannot have \"to\" (destinations go on "
        "each choice)",
    "イベント '{0}' ステート'{1}': 選択肢の遷移先イベント '{2}' が存在しません":
        "Event '{0}' state '{1}': choice destination event '{2}' does not exist",
    "イベント '{0}': ステート移行に選択肢があるときは、イベント側の選択肢/数値入力の表示タイミングは「イベント終了条件の達成時」のみ使えます":
        "Event '{0}': when a state transition uses choices, the event's "
        "choices/numeric input may only be shown when the event end "
        "condition is met",
    "イベント '{0}': ステート移行に選択肢があるときは、終了条件が「無限」のイベントにイベント側の選択肢/数値入力を併用できません":
        "Event '{0}': when a state transition uses choices, an infinite event "
        "cannot also have event-level choices/numeric input",

    # =277: UNDO/REDO
    "↶ 元に戻す": "↶ Undo",
    "↷ やり直す": "↷ Redo",
    "元に戻しました": "Undone",
    "やり直しました": "Redone",
    "あと{0}段": "{0} more step(s)",
}
