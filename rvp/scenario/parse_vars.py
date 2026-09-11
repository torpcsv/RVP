"""シナリオ読み込み: 変数宣言・変数参照・操作(ops)・判定式(cond)・監視(watch)のパース。"""
from __future__ import annotations

from .model import (CondRule, NumRef, VarCond, VarDecl, VarOp, WatchRule,
    _is_num)
from ..i18n import tr

from .load_context import _COND_OPS


def parse_vars(ctx, raw) -> dict[str, VarDecl]:
    decls: dict[str, VarDecl] = {}
    if raw is None:
        return decls
    if not isinstance(raw, dict):
        raise ValueError(tr("vars はオブジェクトで指定してください"))
    for name, v in raw.items():
        w = tr("vars '{0}'").format(name)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(tr("vars: 変数名が不正です"))
        vmin = vmax = None
        if isinstance(v, dict):
            if "init" not in v:
                raise ValueError(tr("{0}: init が必要です").format(w))
            init = v["init"]
            vmin, vmax = v.get("min"), v.get("max")
        else:
            init = v
        is_num = _is_num(init)
        if not is_num and not isinstance(init, str):
            raise ValueError(
                tr("{0}: 初期値は数値か文字列にしてください").format(w))
        if vmin is not None or vmax is not None:
            if not is_num:
                raise ValueError(
                    tr("{0}: min/max は数値変数のみ指定できます").format(w))
            for label, bound in (("min", vmin), ("max", vmax)):
                if bound is not None and not _is_num(bound):
                    raise ValueError(
                        tr("{0}: {1} は数値にしてください").format(w, label))
            if vmin is not None and vmax is not None and vmax < vmin:
                raise ValueError(
                    tr("{0}: max は min 以上にしてください").format(w))
            if (vmin is not None and init < vmin) or \
               (vmax is not None and init > vmax):
                raise ValueError(
                    tr("{0}: 初期値が min/max の範囲外です").format(w))
        decls[name] = VarDecl(name=name, init=init, is_number=is_num,
                              vmin=vmin, vmax=vmax)
    return decls


def _require_var(ctx, name, where) -> VarDecl:
    if not isinstance(name, str) or name not in ctx.var_decls:
        raise ValueError(
            tr("{0}: 変数 '{1}' が vars で宣言されていません").format(where, name))
    return ctx.var_decls[name]


def _parse_rhs(ctx, value, where):
    """右辺(定数 or {"var": Y})を (const, var_name, is_number) で返す。"""
    if isinstance(value, dict):
        ref = _require_var(ctx, value.get("var"), where)
        return None, ref.name, ref.is_number
    if _is_num(value):
        return value, None, True
    if isinstance(value, str):
        return value, None, False
    raise ValueError(
        tr("{0}: value は数値・文字列・{{\"var\": ...}} のいずれかにしてください").format(where))


def parse_numref(ctx, v, where):
    """数値欄の値を解析する。(静的値orNone, NumReforNone) を返す。

    数値ならそのまま、{"var": "X"} なら数値変数の参照として受理する。
    """
    if isinstance(v, dict):
        ref = _require_var(ctx, v.get("var"), where)
        if not ref.is_number:
            raise ValueError(
                tr("{0}: 変数 '{1}' は数値変数ではありません").format(where, ref.name))
        return None, NumRef(var=ref.name)
    if _is_num(v):
        return float(v), None
    raise ValueError(
        tr('{0}: 数値か {{"var": "..."}} で指定してください').format(where))


def parse_ops(ctx, raw, where) -> tuple:
    """変数操作リストを解析する。省略はOK(空タプル)。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(tr("{0}: 変数操作はリストで指定してください").format(where))
    ops = []
    for i, o in enumerate(raw):
        w = tr("{0} 操作[{1}]").format(where, i)
        present = [k for k in ("set", "add", "mul", "roll", "eval")
                   if k in o]
        if not isinstance(o, dict) or len(present) != 1:
            raise ValueError(
                tr("{0}: set / add / mul / roll / eval のいずれか1つを指定してください").format(w))
        kind = present[0]
        target = _require_var(ctx, o[kind], w)
        if kind == "eval":
            # =126 条件式: when の判定式(1件)を評価し 1/0 を代入
            if not target.is_number:
                raise ValueError(
                    tr("{0}: eval は数値変数にのみ使えます").format(w))
            if not isinstance(o.get("when"), dict):
                raise ValueError(
                    tr('{0}: eval には when({{"var","op","value"}}) が必要です').format(w))
            conds = parse_conds(ctx, [o["when"]], w)
            ops.append(VarOp(kind="eval", name=target.name,
                             cond=conds[0]))
            continue
        if kind == "roll":
            # 乱数代入: 数値変数へ [min, max] の一様乱数(整数)を代入
            if not target.is_number:
                raise ValueError(
                    tr("{0}: roll は数値変数にのみ使えます").format(w))
            if "min" not in o or "max" not in o:
                raise ValueError(
                    tr("{0}: roll には min と max が必要です").format(w))
            mn_c, mn_v, mn_num = _parse_rhs(ctx, o["min"], w)
            mx_c, mx_v, mx_num = _parse_rhs(ctx, o["max"], w)
            if not mn_num or not mx_num:
                raise ValueError(
                    tr("{0}: roll の min/max は数値にしてください").format(w))
            ops.append(VarOp(kind="roll", name=target.name,
                             value=mn_c, value_var=mn_v,
                             value2=mx_c, value2_var=mx_v))
            continue
        if "value" not in o:
            raise ValueError(tr("{0}: value が必要です").format(w))
        const, var_name, rhs_num = _parse_rhs(ctx, o["value"], w)
        if kind in ("add", "mul"):
            # =75: mul(乗算)は add と同じ制約(数値変数×数値のみ)
            if not target.is_number:
                raise ValueError(
                    tr("{0}: {1} は数値変数にのみ使えます").format(w, kind))
            if not rhs_num:
                raise ValueError(
                    tr("{0}: {1} の value は数値にしてください").format(w, kind))
        elif rhs_num != target.is_number:
            raise ValueError(
                tr("{0}: 変数 '{1}' と value の型が一致しません").format(w, target.name))
        ops.append(VarOp(kind=kind, name=target.name,
                         value=const, value_var=var_name))
    return tuple(ops)


def parse_conds(ctx, raw, where) -> tuple:
    """AND条件リストを解析する。"""
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            tr("{0}: when は1件以上の条件リストで指定してください").format(where))
    conds = []
    for i, c in enumerate(raw):
        w = tr("{0} 条件[{1}]").format(where, i)
        if not isinstance(c, dict):
            raise ValueError(tr("{0}: 条件はオブジェクトで指定してください").format(w))
        lhs = _require_var(ctx, c.get("var"), w)
        op = c.get("op")
        if op not in _COND_OPS:
            raise ValueError(
                tr("{0}: op は {1} のいずれかにしてください").format(w, " ".join(_COND_OPS)))
        if "value" not in c:
            raise ValueError(tr("{0}: value が必要です").format(w))
        const, var_name, rhs_num = _parse_rhs(ctx, c["value"], w)
        if rhs_num != lhs.is_number:
            raise ValueError(
                tr("{0}: 変数 '{1}' と value の型が一致しません").format(w, lhs.name))
        if not lhs.is_number and op not in ("==", "!="):
            raise ValueError(
                tr("{0}: 文字列変数の比較は == と != のみです").format(w))
        conds.append(VarCond(name=lhs.name, op=op,
                             value=const, value_var=var_name))
    return tuple(conds)


def parse_cond_next(ctx, raw, where) -> CondRule:
    """next の cond 形式を CondRule へ解析する。

    {"cond": [{"when": [...], "to": "..."}, ...],
     "else": "id" or null}   ※elseキーは必須(書き忘れ防止)
    """
    rows_raw = raw.get("cond")
    if not isinstance(rows_raw, list) or not rows_raw:
        raise ValueError(
            tr("{0}: cond は1件以上のリストで指定してください").format(where))
    rows = []
    for i, row in enumerate(rows_raw):
        w = tr("{0} cond[{1}]").format(where, i)
        if not isinstance(row, dict) or not isinstance(row.get("to"), str):
            raise ValueError(tr("{0}: when と to が必要です").format(w))
        rows.append((parse_conds(ctx, row.get("when"), w), row["to"]))
    if "else" not in raw:
        raise ValueError(
            tr('{0}: cond には "else"(どの条件も成立しない時の遷移先、'
               'null=再生終了) が必要です').format(where))
    else_to = raw["else"]
    if else_to is not None and not isinstance(else_to, str):
        raise ValueError(
            tr("{0}: else はイベントIDの文字列か null にしてください").format(where))
    return CondRule(rows=rows, else_to=else_to)


def parse_watch(ctx, raw) -> list[WatchRule]:
    """トップレベル watch を解析する。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(tr("watch はリストで指定してください"))
    out = []
    for i, w in enumerate(raw):
        where = tr("watch[{0}]").format(i)
        if not isinstance(w, dict) or not isinstance(w.get("to"), str):
            raise ValueError(
                tr("{0}: to はイベントIDの文字列で指定してください").format(where))
        conds = parse_conds(ctx, w.get("when"), where)
        mode = w.get("mode", "graceful")
        if mode not in ("graceful", "interrupt"):
            raise ValueError(
                tr('{0}: mode は "graceful" か "interrupt" にしてください').format(where))
        once = w.get("once", True)
        if not isinstance(once, bool):
            raise ValueError(
                tr("{0}: once は true/false にしてください").format(where))
        out.append(WatchRule(conds=conds, to=w["to"],
                             mode=mode, once=once))
    return out
