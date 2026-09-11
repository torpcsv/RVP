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
from __future__ import annotations

# =301: editor.py(16,668行)をパッケージへ分割した。従来の
# `from rvp.editor import X` / `rvp.editor.X` はそのまま使える(下で再エクスポート)。
# テストが実行時に差し替えるフック(下記)はこの __init__ に置く。実装側は
# `_hooks._pkg().NAME` で呼び出し時にここを読む(詳細は _hooks.py)。テストが
# 差し替えるクラス/関数(DetailDialog / ChannelCopyDialog / _mpv_path_setting)も同様。
import asyncio
import copy
import filecmp
import json
import os
import shutil
import sys
import threading
import time
import warnings
import tkinter as tk
import tkinter.font as tkfont
import customtkinter as ctk
from tkinter import filedialog          # テストが差し替える(askopenfilename 等)

from ..i18n import tr, load_config, save_config  # noqa: F401
from .. import __version__, appfont, apptheme, winstate  # noqa: F401
from ..winstate import WindowMemory  # noqa: F401
from ..scenario import (Scenario, auto_bind_tracks, DEFAULT_PAN,  # noqa: F401
                        normalize_track_type, AUTO_FS_TAGS, CSV_TRACK_TYPES,
                        migrate_video_node)
from ..funscript import Funscript  # noqa: F401
from ..rotate_source import load_rotate_source  # noqa: F401
from ..scenario_map import (OK_COLOR, CANVAS_BG, CANVAS_BG_LIGHT, NODE_FILL,  # noqa: F401
                            NODE_STATES, EDGE_COLOR, STATE_EDGE_COLOR)
from .. import scenario_map as _smap  # noqa: F401
from ._hooks import TkinterDnD, DND_FILES   # noqa: F401  テストが TkinterDnD を差し替える

# 編集画面が出したメッセージの記録(テスト用)。(kind, (title, message)) を
# 表示のたびに append する。kind は 'error' / 'warn' / 'ok' / 'info' / 'confirm'。
MESSAGE_LOG: list = []
# 確認ダイアログを自動で「はい」にする(テスト用)。
AUTO_CONFIRM = False
# =203: 動画レビューで mpv へ渡す追加引数(テスト用フック。ヘッドレス環境の
# テストは ("--vo=null","--ao=null","--force-window=no","--no-config") にする)
REVIEW_MPV_EXTRA_ARGS: tuple = ()
# =205: ユーザーパターン編集の未保存確認を自動で答える(テスト用。None=聞く)
USER_PAT_UNSAVED_AUTO: bool | None = None
# =170: スクリプト編集の未保存確認 / 上書き確認を自動で答える(テスト用)
SCRIPT_EDIT_UNSAVED_AUTO: str | None = None
SCRIPT_EDIT_CONFIRM_AUTO: bool | None = None

# ---- 再エクスポート(旧 editor.py の公開名) ----
from .paths import (_same_file_content, _safe_relpath, _rebase_scenario_path,
    _format_bytes, _map_item_paths)  # noqa: F401
from .common import (MUTED, TEXT_MUTED, TEXT_HEAD, NODE_SELECTED, COMBO_TEXT,
    COMBO_TEXT_DISABLED, NODE_PALETTE, _canvas_bg, CTkOptionMenu, BOX_BG,
    BOX_BORDER, _toolbar_sep, FIELD_ERR_BG, FIELD_WARN_BG, FIELD_ERR_EDGE,
    FIELD_WARN_EDGE, MSG_ERROR, MSG_WARN, MSG_OK, MSG_INFO, DEVICE_TYPES,
    menu_device_types, CHANNEL_IDS, VIDEO_EXTS, VIDEO_FILETYPES,
    INFINITE_CHOICE, _front_window, _has_varref, _num_disp, _is_num,
    _parse_num_text, _COND_OPS, DEFAULT_EVENT, DEFAULT_STATE, _load_raw,
    _title_from_path, _load_dialog_dir, _dialog_initialdir,
    _script_track_type, _raw_has_video, _remember_dialog_dir,
    _ellipsize_middle, POPUP_SHIFT, _place_popup)  # noqa: F401
from .fields import (Tooltip, VarRefField, CondListEditor, _range_disp,
    _raw_range, _validate_range_fields, _range_dict)  # noqa: F401
from .items import (TrackRowsMixin, ItemRow, BgmItemRow)  # noqa: F401
from .channel import (ChannelSection)  # noqa: F401
from .dialogs import (OpsDialog, DetailDialog, BackgroundDialog, VarsDialog,
    ChannelCopyDialog, ImportDialog)  # noqa: F401
from .help import (_help_blocks, _help_sections, _license_blocks, HelpDialog,
    third_party_licenses_path, third_party_licenses_text, LicenseDialog)  # noqa: F401
from .review_support import (REVIEW_SLOT, SPEED_CHOICES, WAVE_BUCKET_MS,
    WAVE_MODE_KEYS, compute_wave_env, _mpv_path_setting, _EditorMpv,
    _hold_editor_mpv, REVIEW_ROTATE_TYPES, SCRIPT_EDIT_TYPES,
    SCRIPT_EDIT_CSV_TYPES, SCRIPT_EDIT_STEP_TYPES, SCRIPT_EDIT_TAGS,
    SCRIPT_EDIT_NEW_KINDS, _is_csv, _review_range_ms, _review_segments)  # noqa: F401
from .user_patterns import (UserPatternDialog)  # noqa: F401
from .review import (ItemReviewDialog)  # noqa: F401
from .state_choice import (StateChoiceEditor)  # noqa: F401
from .scenario_editor import (ScenarioEditor)  # noqa: F401

# テーマ追従する色定数(apptheme.register の対象)は値をコピーせず、定義元
# common の「今の値」を返す(旧モジュールでは register 済みで差し替わっていた
# ので、`rvp.editor.ACCENT` を読むテスト/コードとの互換)。
_THEME_KEYS = ('ACCENT', 'ACCENT_HOVER', 'ACCENT_TEXT', 'CANVAS_BG_LIGHT', 'CARD_BORDER', 'CARD_COLOR', 'PANEL_COLOR')


def __getattr__(name):
    if name in _THEME_KEYS:
        from . import common as _c
        return getattr(_c, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

