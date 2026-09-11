"""=301: パッケージ属性で差し替えられる名前(テストフック)への参照口。

`rvp.editor` パッケージの属性(AUTO_CONFIRM / MESSAGE_LOG /
SCRIPT_EDIT_*_AUTO / USER_PAT_UNSAVED_AUTO / REVIEW_MPV_EXTRA_ARGS /
filedialog / TkinterDnD / _mpv_path_setting)は、テストが
`from rvp import editor as ed_mod; ed_mod.AUTO_CONFIRM = True` のように
**実行時に差し替える**。分割後もその作法を保つため、実装側はモジュールの
グローバルを直接読まず `_pkg().NAME` で呼び出し時にパッケージから読む。
"""
from __future__ import annotations
import sys


def _pkg():
    """`rvp.editor` パッケージモジュール(部分初期化中でも参照できる)。"""
    return sys.modules[__package__]


# OSからのドラッグ&ドロップ(=44)。tkinterdnd2(MIT・内包tkdndはBSD系)が
# 未導入の環境では D&D 機能だけ静かに無効になり、他は従来どおり動く。
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = "DND_Files"
