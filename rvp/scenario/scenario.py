"""シナリオ本体(Scenario dataclass。読み込みは load.py の mixin)。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .load import _ScenarioLoadMixin
from .model import BackgroundSpec, ScenarioEvent, VarDecl  # noqa: F401


@dataclass
class Scenario(_ScenarioLoadMixin):

    title: str
    start: str
    events: dict[str, ScenarioEvent] = field(default_factory=dict)
    path: str = ""
    detail: str = ""   # シナリオの説明文(作成者の自由記入、改行可)
    var_decls: dict[str, VarDecl] = field(default_factory=dict)  # トップレベルvars宣言
    watches: tuple = ()   # 監視(watch)トリガー(トップレベル、宣言順に評価)
    # =130: 読み込み時の警告(エラーにはしないが高確率で意図と違う構成)。
    # 現状は「デバイス担当チャンネルに、その種別のトラックが1つも無い」のみ。
    load_warnings: list = field(default_factory=list)
    # =252: デバイス連動フラグ(トップレベル "device_enabled")。
    # 省略=True(旧シナリオは従来どおり)。False のシナリオは、トラックの
    # 紐づけ定義がJSONに残っていても**再生時にデバイスを駆動しない**。
    # UI側(再生タブの④デバイス調整・⑤グラフ、編集画面のデバイス関連)も
    # このフラグで非表示になる。
    device_enabled: bool = True
    # =256: BGM機能フラグ(トップレベル "bgm_enabled")。**省略=False**
    # (BGMを持つ旧シナリオは存在しないため、device_enabled とは省略時の
    # 意味が逆)。False のシナリオは "bgm" 定義がJSONに残っていても
    # 再生時にBGMを鳴らさない(UIも非表示。データは保持=device と同じ方式)。
    bgm_enabled: bool = False
    # =262: 背景イラスト(トップレベル "background")。省略=None=なし。
    background: "BackgroundSpec | None" = None
