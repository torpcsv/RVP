"""シナリオファイルの rvp_version(=383)。

トップレベル `"rvp_version": "1.11.0"` = **そのファイルを保存した RVP の版**
(動かすのに最低限必要な版ではない)。編集画面の保存のたびに今の
`rvp.__version__` で上書きし、JSON の先頭に置く。

読む側の扱い(ユーザー決定 Q1〜Q9):
  - キーなし / 自分と同じか古い → 何もしない("none" / "ok")
  - 自分より新しい → 警告だけ出して読み込みは続ける("newer")
  - 値が不正(文字列でない・数字の並びでない) → 警告にして無視("invalid")
比較は数字の並び(1.10.0 > 1.9.1)。足りない桁は 0 とみなす(1.11 == 1.11.0)。
"""
from __future__ import annotations

import json
import re

RVP_VERSION_KEY = "rvp_version"

_VER_RE = re.compile(r"^\d+(\.\d+){0,3}$")

STATUS_NONE = "none"
STATUS_OK = "ok"
STATUS_NEWER = "newer"
STATUS_INVALID = "invalid"


def current_version() -> str:
    from .. import __version__
    return __version__


def parse_version(value) -> tuple | None:
    """"1.11.0" → (1, 11, 0)。不正なら None。"""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not _VER_RE.match(s):
        return None
    nums = [int(p) for p in s.split(".")]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _norm(t: tuple) -> tuple:
    t = list(t)
    while len(t) > 1 and t[-1] == 0:
        t.pop()
    return tuple(t)


def check_version(value, missing: bool = False,
                  current: str | None = None) -> str:
    """rvp_version の値を今の RVP と比べた結果(STATUS_*)を返す。

    missing=True はキー自体が無いとき(値 None と区別する)。
    """
    if missing:
        return STATUS_NONE
    file_v = parse_version(value)
    if file_v is None:
        return STATUS_INVALID
    cur_v = parse_version(current or current_version())
    if cur_v is None:          # 開発中の特殊な版表記など。比べられない
        return STATUS_OK
    return STATUS_NEWER if _norm(file_v) > _norm(cur_v) else STATUS_OK


def check_data(data) -> tuple[str, object]:
    """生の JSON(dict)から (status, 値) を返す。dict でなければ none。"""
    if not isinstance(data, dict) or RVP_VERSION_KEY not in data:
        return STATUS_NONE, None
    value = data.get(RVP_VERSION_KEY)
    return check_version(value), value


def check_file(path: str) -> tuple[str, object]:
    """ファイルから (status, 値) を返す。読めない/JSON でなければ none。

    Scenario.load が失敗したときに、エラー文へ版の案内を添えるために使う。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return STATUS_NONE, None
    return check_data(data)


def newer_message(file_version) -> str:
    """「このシナリオは新しい版で保存されています」の本文(再生タブ・編集画面共通)。"""
    from ..i18n import tr
    return tr("このシナリオは RVP {0} で保存されています。"
              "この RVP({1})では一部の機能が無視される可能性があります。"
              ).format(file_version, current_version())


def invalid_message(value) -> str:
    from ..i18n import tr
    return tr("rvp_version の値が正しくありません({0})。無視して読み込みます。"
              ).format(json.dumps(value, ensure_ascii=False))


def with_version_first(data: dict, version: str | None = None) -> dict:
    """data の先頭に rvp_version を置いた新しい dict を返す(元の値は置き換え)。"""
    out = {RVP_VERSION_KEY: version or current_version()}
    for k, v in data.items():
        if k != RVP_VERSION_KEY:
            out[k] = v
    return out
