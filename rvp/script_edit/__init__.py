"""スクリプト編集機能(=170〜)P1: funscript の打点編集。

アイテムレビュー画面(editor.ItemReviewDialog)の「スクリプト編集」モードから
使われる。editor.py が既に11,000行を超えているため、編集ロジックと編集用
グラフはこのモジュールへ切り出す(仕様書 7.1)。

構成:
  - 純ロジック(Tk非依存): snap / moved_points / ScriptEditModel /
    dump_funscript / load_funscript_raw / write_funscript
  - ScriptEditGraph(tk.Canvas): 編集用グラフ。main.DeviceGraph の考え方
    (LEVELS の縮尺段階・座標変換・時間グリッド)を流用した**新規クラス**
    (DeviceGraph はスナップショットを描くだけの読み取り専用のため継承しない)。

P1 の対象は linear / twist の funscript のみ。点の打点・移動・選択・削除・
矩形選択・コピー/切取/貼付・UNDO/REDO・グリッド吸着・ズーム/パン。
パターン(P2)・ユーザーパターン(P3)は未実装だが、UNDO スナップショットや
描画は後から足せる形にしてある。

**このモジュールはデバイスへ一切送信しない**(=164 の方針を編集モードにも
適用する。test_script_edit_ui の回帰テストがソースを検査する)。
"""
from __future__ import annotations

# =303: script_edit.py(4,758行)をパッケージへ分割した。従来の
# `from rvp import script_edit as se; se.X` はそのまま使える(下で再エクスポート)。
import json  # noqa: F401
import os  # noqa: F401
import tkinter as tk  # noqa: F401
from ..i18n import tr  # noqa: F401
from .. import appfont  # noqa: F401

# ---- 再エクスポート(旧 script_edit.py の公開名) ----
from .common import (RVP_KEY, RVP_VERSION, USER_PAT_SLOTS, USER_PAT_PREFIX,
    USER_PAT_KINDS, USER_PAT_CFG_KEY, user_pat_prefix, user_pattern_keys,
    user_pat_key, ALL_USER_PATTERN_KEYS, USER_PATTERN_KEYS, FKEYS,
    TIME_ADJ_MIN, TIME_ADJ_STEP, GRID_POS_CHOICES, GRID_AT_CHOICES,
    GRID_POS_DEFAULT, GRID_AT_DEFAULT, UNDO_LIMIT, EMPTY_VIEW_MS,
    WAVE_BAND_COLOR, WAVE_BAND_STEP_PX, draw_audio_band, grid_label,
    scale_shape, grid_at_label, snap, moved_points)  # noqa: F401
from .patterns import (K_MAX, K_MIN, K_STRETCH_MAX, S_MAX, S_MIN,
    STD_PATTERNS, STD_PATTERNS_ROTATE, STD_PATTERNS_VIB, PAT_MODE_LINEAR,
    PAT_MODE_ROTATE, PAT_MODE_VIB, PAT_CENTERS, std_patterns, invert_shape,
    shape_len, _endline, shape_ds, solve_k, transform_pattern, _plan,
    plan_normal, plan_connect_side, plan_connect_both)  # noqa: F401
from .io import (normalize_user_shape, _valid_user_shape, load_user_patterns,
    save_user_pattern, first_free_slot, _valid_fkey_ref, load_fkey_map,
    save_fkey_map, fkey_of, load_funscript_raw, dump_funscript,
    write_funscript, CSV_AT_UNIT, CSV_POS_MAX, CSV_STOP_POS, csv_pos_of,
    csv_val_of, csv_hold_pos, load_csv_points, dump_csv, write_csv)  # noqa: F401
from .model import (EditLink, ScriptEditModel)  # noqa: F401
from .graph import (ScriptEditGraph)  # noqa: F401
