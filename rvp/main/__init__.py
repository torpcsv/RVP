"""RVP - Random Voice Player (最小構成版 / CustomTkinter UI)

音声ファイル(.wav) / funscript(.funscript) / シナリオファイル(.json) を素材に、
音声再生と Intiface Central 経由の linear デバイス制御を同期実行するプレーヤー。

起動: python -m rvp.main
依存: pip install customtkinter pygame-ce buttplug-py
"""
from __future__ import annotations

# =302: main.py(6,907行)をパッケージへ分割した。従来の `from rvp.main import X` /
# `rvp.main.X` はそのまま使える(下で再エクスポート)。`python -m rvp.main` は
# __main__.py が受ける。テストが差し替えるフック(TkinterDnD / probe_ws_port)は
# この __init__ の属性で、実装側は `_hooks._pkg().NAME` で呼び出し時に読む。
import asyncio
import bisect
import logging
import os
import sys
import threading
import time
import tkinter as tk
import warnings
from tkinter import filedialog, messagebox   # テストが showerror 等を差し替える

from .startup import (_STARTUP_T0, _STARTUP_MARKS, _startup_mark,  # noqa: F401
                      _startup_log_wanted, _start_stack_sampler,
                      _stop_stack_sampler, _write_startup_log)
_startup_mark("main.py 実行開始(標準ライブラリ import 済み)")
import customtkinter as ctk   # noqa: E402
_startup_mark("import customtkinter")
import pygame   # noqa: E402
_startup_mark("import pygame")

from .. import scenario_map, appfont, apptheme, winstate, __version__  # noqa: E402,F401
from ..i18n import LANG, load_config, save_config, set_language, tr  # noqa: E402,F401
_startup_mark("import scenario_map/i18n")
from ..intiface_client import IntifaceClient, probe_ws_port  # noqa: E402,F401  probe_ws_port はテストが差し替える
_startup_mark("import intiface_client(buttplug)")
from .. import tcode_client  # noqa: E402,F401
from ..tcode_client import TCodeClient  # noqa: E402,F401
_startup_mark("import tcode_client(pyserial)")
from ..player import ScenarioPlayer  # noqa: E402,F401
from ..rotate_source import load_rotate_source  # noqa: E402,F401
from ..scenario import Scenario, TRACK_ROTATE_A10, TRACK_ROTATE_UFO  # noqa: E402,F401
from ..winstate import WindowMemory  # noqa: E402,F401
_startup_mark("import player/scenario ほか")
from .common import PILImage, PILImageTk, HAS_PIL, logger  # noqa: E402,F401
_startup_mark("import PIL(Pillow)")

logging.basicConfig(level=logging.INFO)

from ._hooks import TkinterDnD, DND_FILES   # noqa: E402,F401  テストが TkinterDnD を差し替える
_startup_mark("import tkinterdnd2")

# ---- 再エクスポート(旧 main.py の公開名) ----
from .startup import (_STARTUP_T0, _STARTUP_MARKS, _startup_mark,
    _startup_log_wanted, _STACKS_FILE, _start_stack_sampler,
    _stop_stack_sampler, _write_startup_log)  # noqa: E402,F401
from .common import (HAS_PIL, PILImage, PILImageTk, logger,
    DEFAULT_INTIFACE_URL, OK_COLOR, WARN_COLOR, NEG_COLOR, ERROR_COLOR,
    OK_TEXT, WARN_TEXT, NEG_TEXT, ERROR_TEXT, MUTED, LABEL, COMBO_TEXT,
    COMBO_TEXT_DISABLED, TAB_HEIGHT, TAB_WIDTH, TAB_OVERLAP,
    scenario_display_name, _triangle_photo, TAB_IDLE_COLOR, TAB_HOVER_COLOR,
    paced_delay, AsyncRunner)  # noqa: E402,F401
from .widgets import (FixedBtn, TabView, ZoneBar, RangeSlider)  # noqa: E402,F401
from .device_graph import (DeviceGraph)  # noqa: E402,F401
from .background_art import (bg_fit_geometry, BackgroundArt)  # noqa: E402,F401
from .rvpapp import (RVPApp)  # noqa: E402,F401
from .guards import (_install_ctk_reentrancy_guard,
    _install_crash_diagnostics)  # noqa: E402,F401
from .winicon import (APP_USER_MODEL_ID, _set_win_app_id, _WIN_ICON_CACHE,
    _win_user32, _win_icon_handles, _apply_win_icons, _set_win_taskbar_icons,
    _set_app_icon)  # noqa: E402,F401
from .entry import (main)  # noqa: E402,F401

_startup_mark("rvp.main パッケージ import 完了")

# テーマ追従する色定数(apptheme.register の対象)は値をコピーせず、定義元
# common の「今の値」を返す(旧モジュールでは register 済みで差し替わっていた
# ので、`rvp.main.ACCENT` を読むテスト/コードとの互換)。
_THEME_KEYS = ('ACCENT', 'ACCENT_HOVER', 'ACCENT_TEXT', 'CANVAS_BG_LIGHT', 'CARD_BORDER', 'CARD_COLOR', 'PANEL_COLOR')


def __getattr__(name):
    if name in _THEME_KEYS:
        from . import common as _c
        return getattr(_c, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

