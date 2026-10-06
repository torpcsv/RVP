"""シナリオ編集: 選択肢ボタンの色(=363)。

選択肢の表示テキスト欄を右クリック → イベント図と同じカラーパレット
(`_open_node_palette`)で色を選ぶ。選んだ色はテキスト欄の背景に反映し、
文字色は明るさから黒系/白系を自動で選ぶ。「リセット」で既定色(色なし)。
値は行の raw dict の "color" に持つ(保存時は raw の追加キーとしてそのまま
選択肢へ書き出される=label/to 以外は素通しの既存仕様)。
"""
from __future__ import annotations

from .. import apptheme


def apply_entry_color(entry_widget, color) -> None:
    """テキスト欄の見た目を色に合わせる(None/""=作成時の既定色へ戻す)。"""
    try:
        if not hasattr(entry_widget, "_rvp_default_colors"):
            entry_widget._rvp_default_colors = (
                entry_widget.cget("fg_color"), entry_widget.cget("text_color"))
        if color:
            entry_widget.configure(fg_color=apptheme.user_color(color),
                                   text_color=apptheme.ink_for(color))
        else:
            fg, tx = entry_widget._rvp_default_colors
            entry_widget.configure(fg_color=fg, text_color=tx)
    except Exception:
        pass


def bind_choice_color(owner, row: dict) -> None:
    """選択肢の行(row["label_entry"]・row["raw"])に右クリックの色設定を付ける。

    owner は `_open_node_palette(x_root, y_root, cb)` を持つ ScenarioEditor。
    """
    w = row.get("label_entry")
    if w is None:
        return
    raw = row.setdefault("raw", {})

    def set_color(col):
        if col:
            raw["color"] = col
        else:
            raw.pop("color", None)
        apply_entry_color(w, col)

    def on_rclick(e):
        owner._open_node_palette(e.x_root, e.y_root, set_color)
        return "break"

    row["set_color"] = set_color          # テスト・他機能から使う
    w.bind("<Button-3>", on_rclick)
    apply_entry_color(w, raw.get("color"))
