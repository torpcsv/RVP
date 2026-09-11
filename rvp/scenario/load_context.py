"""シナリオ読み込みの共有コンテキスト(=306)。

`Scenario.load` はかつて 1,856 行の単一メソッドで、32 個の入れ子関数が
base_dir / var_decls / load_warnings をクロージャで共有していた。=306 で
入れ子関数をモジュール関数(parse_vars / parse_items / parse_channels /
parse_flow)へ出し、共有していた値はこの LoadContext を**先頭引数 `ctx`**
として渡す。
"""
from __future__ import annotations
from dataclasses import dataclass, field

# 判定式(cond)で使える比較演算子
_COND_OPS = ("==", "!=", ">=", "<=", ">", "<")


@dataclass
class LoadContext:
    """1 回の Scenario.load の間だけ生きる共有状態。"""
    base_dir: str                       # シナリオ json のあるフォルダ(素材の相対パス解決)
    load_warnings: list = field(default_factory=list)   # =130 読み込み警告(エラーにしない)
    var_decls: dict = field(default_factory=dict)       # トップレベル vars 宣言(parse_vars の結果)
