"""再生エンジン(チャンネル対応)。

- PlaybackClock: 一時停止を考慮した再生位置クロック。
- ScenarioPlayer: シナリオを進行させる。イベントは最大3チャンネル(L/C/R)を
  並行再生し、各チャンネルは独立した mode(順番/ランダム)・終了条件を持つ。
  funscript(デバイス連動)は担当チャンネル(device_channel)のアイテムのみが実行する。

音声再生は pygame.mixer.Sound + Channel を使い、チャンネルごとにパン
(左右の音量バランス)を適用する。funscript は担当チャンネルの音声再生に
同期したクロックを基準にスケジューリングする。
"""
from __future__ import annotations

# =304: player.py(3,863行)をパッケージへ分割した。従来の
# `from rvp.player import ScenarioPlayer, PlaybackClock` 等はそのまま使える。
import asyncio  # noqa: F401
import bisect  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import random  # noqa: F401
import time  # noqa: F401
import wave  # noqa: F401
import pygame  # noqa: F401
from ..i18n import tr  # noqa: F401
from ..funscript import Funscript  # noqa: F401
from ..rotate_source import RotateTimeline, load_rotate_source  # noqa: F401
from ..intiface_client import IntifaceClient  # noqa: F401
from ..mpv_client import MpvClient, MpvError, find_mpv  # noqa: F401
from ..scenario import (
    track_range_ms,
    CH_CENTER,
    END_CHANNEL,
    END_COND,
    END_PLAYS,
    END_TRANSITIONS,
    END_STATES,
    WHEN_CHANNEL_COUNT,
    WHEN_COND,
    WHEN_ALL_CHANNELS,
    WHEN_CHANNEL_END,
    WHEN_STATE_TIME,
    WHEN_CHOICE,
    END_DURATION,
    END_NONE,
    END_REPEAT,
    MODE_RANDOM,
    MODE_RANDOM_BAG,
    MODE_SEQUENTIAL,
    TRACK_LINEAR,
    TRACK_TWIST,
    TRACK_ROTATE,
    TRACK_ROTATE_A10,
    TRACK_VIBRATION,
    DEFAULT_PAN,
    Pan,
    Scenario,
    VarOp,
)  # noqa: F401

# ---- 再エクスポート(旧 player.py の公開名) ----
from .common import (logger, _CH_SLOT, _BGM_SLOT, BGM_FADEOUT_MS,
    SILENT_LOOP_LIMIT, GRAPH_SEG_LIMIT, SILENT_WAIT_RESET_MS)  # noqa: F401
from .clock import (_audio_duration_ms, PlaybackClock, sound_byte_view,
    describe_audio_load_error, resample_frames)  # noqa: F401
from .scenario_player import (ScenarioPlayer)  # noqa: F401
