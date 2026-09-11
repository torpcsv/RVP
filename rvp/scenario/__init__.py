"""シナリオファイル(.json)の読み込み。

# イベント形式 (v2)

{
  "title": "シナリオ名",
  "start": "開始イベントID",
  "events": {
    "イベントID": {
      "mode": "sequential" | "random" | "random_bag",
      "items": [
        "音声ファイル.wav",              // 文字列: 同名funscriptをlinearとして自動対応
        {                                // オブジェクト: 明示指定
          "audio": "voice.wav",
          "funscript": "stroke.funscript",  // linear単一(後方互換)
          "weight": 20
        },
        {
          "audio": "voice.wav",
          "tracks": [                    // 複数デバイスを明示指定
            {"type": "linear",    "funscript": "a.funscript"},
            {"type": "vibration", "funscript": "b.funscript"},
            {"type": "rotate",    "funscript": "c.funscript"}
          ]
        }
      ],
      "end": {"type": "once"} | {"type":"repeat","count":3} | {"type":"duration","seconds":360},
      "next": "次のイベントID"
    }
  }
}

## funscript(トラック)の指定方法

1つの音声に、デバイス種別ごとの funscript を紐づける。指定方法は3通り:

- **tracks 配列**: 種別(type)とfunscriptの組を複数指定できる。
  同じ音声でもイベントごとに別のfunscriptを割り当てられる(itemは各イベントで独立)。
  type は linear / rotate / vibration。
- **funscript 単一**: "funscript": "x.funscript" は linear 1本の指定(後方互換)。
  "funscript": null で自動対応を無効化。
- **未指定**: 同名の .funscript があれば linear として自動対応。

注: 現行の再生エンジンが実際に駆動するのは linear のみ。rotate / vibration の
トラックは読み込み・検証されるが、対応デバイスの実装は今後追加する
(シナリオファイルは将来分を先に記述しておける)。

# ノード形式 (v1, 後方互換)

"nodes" キーを持つ旧形式もそのまま読み込める。内部的には
「音声1件のsequentialイベント(linear1トラック)」へ変換される。
"""
from __future__ import annotations

# =305: scenario.py(3,189行)をパッケージへ分割した。従来の
# `from rvp.scenario import Scenario, TRACK_LINEAR, ...` はそのまま使える。
import json  # noqa: F401
import os  # noqa: F401
import wave  # noqa: F401
from ..i18n import tr  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from typing import NamedTuple  # noqa: F401
from ..funscript import Funscript  # noqa: F401
from ..rotate_source import load_rotate_source  # noqa: F401

# ---- 再エクスポート(旧 scenario.py の公開名) ----
from .constants import (MODE_SEQUENTIAL, MODE_RANDOM, MODE_RANDOM_BAG,
    END_ONCE, END_REPEAT, END_DURATION, END_CHANNEL, END_NONE, END_PLAYS,
    END_TRANSITIONS, END_COND, END_STATES, WHEN_CHANNEL_COUNT, WHEN_COND,
    WHEN_ALL_CHANNELS, WHEN_CHANNEL_END, WHEN_STATE_TIME, WHEN_CHOICE,
    WHEN_CHANNEL_TIME_REMOVED, TRACK_LINEAR, TRACK_TWIST, TRACK_ROTATE_UFO,
    TRACK_ROTATE, TRACK_ROTATE_A10, TRACK_VIBRATION, VALID_TRACK_TYPES,
    OLD_TRACK_ALIASES, CH_LEFT, CH_CENTER, CH_RIGHT, VALID_CHANNELS,
    DEFAULT_PAN)  # noqa: F401
from .tracks import (wav_duration_ms, load_script_source, track_range_ms,
    normalize_track_type, AUTO_FS_TAGS, CSV_TRACK_TYPES, auto_bind_tracks)  # noqa: F401
from .model import (Pan, DeviceTrack, EventItem, NumRef, Channel, BgmItem,
    BgmSpec, BackgroundSpec, StateTransition, EventState, NextRule, _is_num,
    check_node_pos, check_node_color, VarDecl, VarOp, VarCond,
    _describe_cond, CondRule, ChoiceEntry, WatchRule, ChoiceRule, InputRule,
    ScenarioEvent)  # noqa: F401
from .video import (raw_video_channel, free_video_channel,
    migrate_video_node)  # noqa: F401
from .scenario import (Scenario)  # noqa: F401
