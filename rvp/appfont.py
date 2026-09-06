"""アプリ全体のUIフォント切り替え(=119)。

方針:
- 既存コードのフォント指定は2系統ある。
  (a) ``ctk.CTkFont(size=..)``: family未指定 → **ウィジェット生成時**に
      CTkテーマの既定family(ThemeManager.theme["CTkFont"]["family"])を確定する。
  (b) ``font=("", 9)`` などのタプル(素の tk.Canvas / Tooltip):
      空familyはTkのプラットフォーム既定になり、テーマも名前付きフォントも見ない。
- そこで切替は3点を書き換える:
  ①CTkテーマの既定family ②Tkの名前付きフォント(メニュー等)
  ③本モジュールの ``FAMILY``(タプル指定側はこれを参照するよう変更済み)。
- (a)の性質上、**ウィジェット生成前=main()起動時に1回だけ**呼ぶ。
  実行中の切替はできない=設定変更は「再起動後に反映」(言語設定と同じ扱い)。
- 端末にフォントが無ければ何もしない(=システム標準のまま)。テストは
  main() を通らないので汚染しない(winstate / CTk再入ガードと同じ流儀)。

BIZ UDGothic はモリサワ製・SIL OFL 1.1(同梱時はライセンス全文添付・単体販売
不可のみ)。**Windows 10 バージョン1809(2018-10)以降に標準搭載**なので、
配布物にフォントファイルを同梱する必要はない。
"""
from __future__ import annotations

import tkinter.font as tkfont

# 選択肢として提示するフォントの正式名(コンフィグ保存値もこれ)。
# BIZ UDGothic=等幅(欧文も等幅) / BIZ UDPGothic=プロポーショナル。
# どちらも Windows 10 1809 以降に標準搭載。
FONT_BIZ = "BIZ UDGothic"
FONT_BIZP = "BIZ UDPGothic"
FONT_CHOICES = (FONT_BIZ, FONT_BIZP)
# =293: 初期設定(コンフィグに font_family キーが無いとき)のフォント。
# 「システム標準」は空文字で保存されるので、キー欠落(初回起動)とは区別する。
DEFAULT_FAMILY = FONT_BIZP


def configured_family(cfg) -> str:
    """コンフィグから UI フォントの保存値を返す(=293)。

    キーが無い(初回起動)→ DEFAULT_FAMILY、"" → システム標準、
    文字列 → その名前。
    """
    fam = cfg.get("font_family") if isinstance(cfg, dict) else None
    if isinstance(fam, str):
        return fam
    return DEFAULT_FAMILY

# 端末により登録名が日本語名のことがある(Tk は locale 依存で列挙する)。
# 正式名 → 実際に試す候補名の並び。
_ALIASES = {
    FONT_BIZ: (FONT_BIZ, "BIZ UDゴシック"),
    FONT_BIZP: (FONT_BIZP, "BIZ UDPゴシック"),
}

# 素の tk.Canvas / タプル指定用の現在family。""=プラットフォーム既定。
# import appfont; font=(appfont.FAMILY, 9) の形で**属性参照**で使うこと
# (from-import で値を写すと切替が伝わらない)。
FAMILY = ""

# Tkの名前付きフォント(メニュー・ツールチップ・メッセージボックス等が使う)。
_NAMED_FONTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                "TkHeadingFont", "TkCaptionFont", "TkTooltipFont",
                "TkIconFont")


def resolve(root, family: str) -> str:
    """端末に実在する登録名を返す。見つからなければ ""。

    families() は端末の全フォントを列挙する(数百件)ので小文字化して照合。
    """
    if not family:
        return ""
    try:
        installed = {f.lower() for f in tkfont.families(root)}
    except Exception:
        return ""
    for cand in _ALIASES.get(family, (family,)):
        if cand.lower() in installed:
            return cand
    return ""


def apply(root, family: str) -> bool:
    """UIフォントを family へ切り替える(ウィジェット生成前に呼ぶこと)。

    端末に無いフォントなら何もせず False(=システム標準のまま起動)。
    """
    global FAMILY
    real = resolve(root, family)
    if not real:
        return False
    try:
        # ①CTkテーマの既定family(以降に生成される CTkFont が拾う)
        from customtkinter import ThemeManager
        ThemeManager.theme["CTkFont"]["family"] = real
    except Exception:
        return False
    # ②Tkの名前付きフォント(メニュー等)。sizeは触らない。
    for name in _NAMED_FONTS:
        try:
            tkfont.nametofont(name, root=root).configure(family=real)
        except Exception:
            pass
    # ③タプル指定(素のCanvas等)用
    FAMILY = real
    return True
