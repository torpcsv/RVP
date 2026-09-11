"""ユーザーパターン/Fキー割り当ての設定 I/O と funscript/csv の読み書き。"""
from __future__ import annotations

import json
import os

from .common import (ALL_USER_PATTERN_KEYS, FKEYS, RVP_KEY, USER_PAT_CFG_KEY,
    USER_PAT_SLOTS, user_pattern_keys)
from .patterns import STD_PATTERNS


def normalize_user_shape(points):
    """ユーザーパターンの点列 [(at,pos)] を shape へ正規化する(=205)。

    先頭の at を 0 へずらす(基準長=最終 at・仕様 6.1)。2点未満・
    幅0は None(登録できない)。
    """
    pts = sorted({int(a): max(0, min(100, int(p)))
                  for a, p in points or ()}.items())
    if len(pts) < 2:
        return None
    t0 = pts[0][0]
    shape = tuple((a - t0, p) for a, p in pts)
    if shape[-1][0] <= 0:
        return None
    return shape


def _valid_user_shape(v):
    try:
        shape = tuple((int(t), int(p)) for t, p in v)
    except (TypeError, ValueError):
        return None
    if len(shape) < 2 or shape[0][0] != 0 or shape[-1][0] <= 0:
        return None
    last = -1
    for t, p in shape:
        if t <= last or not (0 <= p <= 100):
            return None
        last = t
    return shape


def load_user_patterns(cfg: dict, kind: str | None = None) -> dict:
    """config から {"L1": shape, ...} を読む(=205 / =231 種別ごと)。

    kind を渡すとその種別の20枠だけ、None なら全種別ぶんを返す。
    不正な枠は無視。**旧 "user_patterns" は読まない**(=231)。
    """
    out = {}
    raw = (cfg.get("script_edit") or {}).get(USER_PAT_CFG_KEY) or {}
    if not isinstance(raw, dict):
        return out
    keys = user_pattern_keys(kind) if kind else ALL_USER_PATTERN_KEYS
    for key in keys:
        shape = _valid_user_shape(raw.get(key) or ())
        if shape is not None:
            out[key] = shape
    return out


def save_user_pattern(cfg: dict, key: str, shape) -> dict:
    """config 辞書へユーザーパターンを書き込む(=205。保存は呼び出し側)。"""
    se = cfg.setdefault("script_edit", {})
    if not isinstance(se, dict):
        se = cfg["script_edit"] = {}
    up = se.setdefault(USER_PAT_CFG_KEY, {})
    if not isinstance(up, dict):
        up = se[USER_PAT_CFG_KEY] = {}
    up[key] = [[int(t), int(p)] for t, p in shape]
    return cfg


def first_free_slot(patterns: dict, kind: str) -> int:
    """=231: 未登録のうち一番小さい枠番号(全部埋まっていれば 20)。"""
    for i, key in enumerate(user_pattern_keys(kind), start=1):
        if key not in patterns:
            return i
    return USER_PAT_SLOTS


def _valid_fkey_ref(v, n_std: int):
    if not isinstance(v, dict):
        return None
    kind = v.get("kind")
    if kind == "std":
        try:
            idx = int(v.get("index"))
        except (TypeError, ValueError):
            return None
        return ("std", idx) if 0 <= idx < n_std else None
    if kind == "user":
        slot = v.get("slot")
        if slot is None:
            # 旧形式 {"kind":"user","key":"U3"} は枠番号へ読み替える(=231)
            key = str(v.get("key") or "")
            slot = key[1:] if key[:1].isalpha() else key
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return None
        return ("user", slot) if 1 <= slot <= USER_PAT_SLOTS else None
    return None


def load_fkey_map(cfg: dict) -> dict:
    """config から {"F1": ref, ...} を読む(=212)。不正なキー・参照は無視。
    同じ参照が複数キーにあれば若いキーだけ残す(排他の保険)。"""
    out = {}
    raw = (cfg.get("script_edit") or {}).get("fkeys") or {}
    if not isinstance(raw, dict):
        return out
    seen = set()
    for fk in FKEYS:
        ref = _valid_fkey_ref(raw.get(fk), len(STD_PATTERNS))
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        out[fk] = ref
    return out


def save_fkey_map(cfg: dict, fkey: str, ref) -> dict:
    """config 辞書へ割り当てを書く(=212。保存は呼び出し側)。
    ref=None は解除。排他: 同じ ref を持つ他のキーは外す(=移動)。"""
    se = cfg.setdefault("script_edit", {})
    if not isinstance(se, dict):
        se = cfg["script_edit"] = {}
    cur = load_fkey_map(cfg)
    if fkey not in FKEYS:
        return cfg
    if ref is None:
        cur.pop(fkey, None)
    else:
        for k in [k for k, r in cur.items() if r == ref]:
            del cur[k]
        cur[fkey] = tuple(ref)
    se["fkeys"] = {k: ({"kind": "std", "index": int(r[1])} if r[0] == "std"
                       else {"kind": "user", "slot": int(r[1])})
                   for k, r in cur.items()}
    return cfg


def fkey_of(fmap: dict, ref):
    """ref に割り当てられている F キー名(無ければ None)。"""
    for k, r in fmap.items():
        if tuple(r) == tuple(ref):
            return k
    return None


def load_funscript_raw(path: str) -> tuple[dict, list[tuple[int, int]]]:
    """funscript を**dictごと**読み込む(actions 以外のキーを保存で保持する
    ため)。読み込みは従来どおり utf-8-sig。戻り値 (rawdict, [(at,pos)])。
    壊れた JSON・dictでない JSON は ValueError。
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("funscript is not a JSON object")
    pts = {}
    for a in data.get("actions") or []:
        try:
            at, pos = int(a["at"]), int(a["pos"])
        except (KeyError, TypeError, ValueError):
            continue
        if at >= 0:
            pts[at] = max(0, min(100, pos))
    return data, sorted(pts.items())


def dump_funscript(points, extra: dict | None, rvp: dict | None = None) -> str:
    """書き出し用の JSON 文字列を作る。

    - `actions` 以外のキー(metadata 等)は**元の順序のまま保持**する
    - at = 整数ms・pos = 整数0〜100・at 昇順
    - ensure_ascii=False(UTF-8。BOM は書き込み側で付けない)
    - =204: `rvp` はパターン記憶(仕様 6.2)。**現在の編集状態が真実**なので
      元の "rvp" キーは常に置き換える(None なら削除=パターンなし)。
      位置は元のキー順を保ち、無ければ末尾に足す。
    """
    actions = [{"at": int(a), "pos": max(0, min(100, int(p)))}
               for a, p in sorted(points)]
    out = {}
    placed = False
    placed_rvp = False
    for k, v in (extra or {}).items():
        if k == "actions":
            out[k] = actions
            placed = True
        elif k == RVP_KEY:
            if rvp is not None:
                out[k] = rvp
                placed_rvp = True
        else:
            out[k] = v
    if not placed:
        out["actions"] = actions
    if rvp is not None and not placed_rvp:
        out[RVP_KEY] = rvp
    return json.dumps(out, ensure_ascii=False)


def write_funscript(path: str, points, extra: dict | None,
                    rvp: dict | None = None) -> None:
    """tmp へ書いてから os.replace(ScenarioEditor._do_save と同じ作法)。

    UTF-8(BOMなし)。OSError は呼び出し側が握って画面へ出す。
    """
    payload = dump_funscript(points, extra, rvp)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


CSV_AT_UNIT = 100          # csv の時刻の単位(ms)


CSV_POS_MAX = 200          # =297: csv 編集モデルの pos 上限(分解能)


CSV_STOP_POS = 100         # 停止の pos(=中央)


def csv_pos_of(cw: bool, frac: float) -> int:
    """(方向, 速度率0.0〜1.0) → pos 0〜200(=297)。"""
    f = max(0.0, min(1.0, float(frac)))
    v = CSV_STOP_POS + f * 100.0 if cw else CSV_STOP_POS - f * 100.0
    return int(round(v))


def csv_val_of(pos: int) -> tuple[int, int]:
    """pos 0〜200 → (方向 0/1, 速度 0〜100)。停止(pos=100)の方向は 1。"""
    d = int(max(0, min(CSV_POS_MAX, int(pos)))) - CSV_STOP_POS
    return (1 if d >= 0 else 0, int(abs(d)))


def csv_hold_pos(points, at: int) -> int:
    """階段の保持値: at の時点で有効な pos(最初の行の前は停止)。"""
    cur = CSV_STOP_POS
    for a, p in points:
        if a > at:
            break
        cur = p
    return cur


def load_csv_points(path: str, ch: int = 0):
    """csv を読み、(列数(3/5), 指定チャンネルの点列, 全チャンネルの点列) を返す。

    列数・数値が不正なファイルは rotate_source と同じ ValueError。
    """
    from ..rotate_source import load_csv
    tl = load_csv(path)
    cols = 5 if tl.channels >= 2 else 3
    all_pts = []
    for i in range(tl.channels):
        all_pts.append([(int(st.at), csv_pos_of(*st.vals[i]))
                        for st in tl.steps])
    if not all_pts:                       # 空ファイル
        all_pts = [[]]
        cols = 3
    idx = ch if 0 <= ch < len(all_pts) else 0
    return cols, list(all_pts[idx]), all_pts


def dump_csv(points, cols: int = 3, other=None, ch: int = 0) -> str:
    """csv の本文を作る。

    - `points` = 編集したチャンネルの (at, pos)。at は 100ms へ丸める
      (同じ時刻になったら後ろ勝ち)。
    - 5列(2ch)のときは `other`(もう一方のチャンネルの (at,pos))の行も
      残し、**各行に両チャンネルの「その時点の保持値」を書く**
      (csv は1行で両方を指定する形式のため)。
    - `ch` = 編集したチャンネルの位置(0=左/1=右)。
    """
    mine = {}
    for a, p in points:
        mine[int(round(a / CSV_AT_UNIT)) * CSV_AT_UNIT] = \
            max(0, min(CSV_POS_MAX, int(p)))
    mine = sorted(mine.items())
    if cols == 3 or other is None:
        rows = [(a, csv_val_of(p)) for a, p in mine]
        return "".join("{0},{1},{2}\n".format(a // CSV_AT_UNIT, d, s)
                       for a, (d, s) in rows)
    oth = {}
    for a, p in other:
        oth[int(round(a / CSV_AT_UNIT)) * CSV_AT_UNIT] = \
            max(0, min(CSV_POS_MAX, int(p)))
    oth = sorted(oth.items())
    ats = sorted({a for a, _p in mine} | {a for a, _p in oth})
    out = []
    for a in ats:
        pm = csv_hold_pos(mine, a)
        po = csv_hold_pos(oth, a)
        vals = [csv_val_of(pm), csv_val_of(po)]
        if ch == 1:
            vals.reverse()
        out.append("{0},{1},{2},{3},{4}\n".format(
            a // CSV_AT_UNIT, vals[0][0], vals[0][1],
            vals[1][0], vals[1][1]))
    return "".join(out)


def write_csv(path: str, points, cols: int = 3, other=None,
              ch: int = 0) -> None:
    """tmp へ書いてから os.replace(write_funscript と同じ作法)。"""
    payload = dump_csv(points, cols, other, ch)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
