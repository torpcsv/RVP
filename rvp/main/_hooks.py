"""=302: パッケージ属性で差し替えられる名前(テストフック)への参照口(=301 と同じ作法)。

テストは `from rvp import main as main_mod; main_mod.TkinterDnD = None` /
`main_mod.probe_ws_port = fake` のように **`rvp.main` パッケージの属性を実行時に
差し替える**。実装側はモジュールのグローバルを直接読まず `_pkg().NAME` で
呼び出し時にパッケージから読む(messagebox/filedialog/tk/os のように
モジュール属性を差し替えるものは同一オブジェクトなので再エクスポートで足りる)。
"""
from __future__ import annotations
import sys


def _pkg():
    """`rvp.main` パッケージモジュール(部分初期化中でも参照できる)。"""
    return sys.modules[__package__]


# OSからのドラッグ&ドロップ(=270: シナリオタブへのシナリオjson D&D)。
# 編集画面(editor =44)と同じ流儀: tkinterdnd2 が無い環境では静かに無効。
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:       # pragma: no cover - 未導入環境では機能ごと無効
    TkinterDnD = None
    DND_FILES = "DND_Files"
