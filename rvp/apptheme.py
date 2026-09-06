"""=134: パステルカラーテーマ(ブルー/グリーン/イエロー/オレンジ/ピンク/パープル)。

ダーク/ライトに続く第3の外観として、**ライト基調のパステル(うすい)背景+
同色相ストロングトーンのアクセントボタン**のテーマを6色用意する。

## 仕組み

起動時は `main()` が root 生成より前に `set_active(テーマ名)` を呼ぶ。
**実行中の切り替えは `switch()`**(=151で再起動不要になった。仕組みは
このファイル下部の「=151: 実行中のテーマ切り替え」を参照)。

`set_active()` は、

1. CTk の ThemeManager(既定テーマ辞書)の**ライト側**だけを差し替える
   (ウィンドウ/フレーム背景=パステル、既定ボタン=ストロングトーン)。
   ダーク側は不変=実行中にダークへ切り替えても従来のダークになる。
2. `register()` 済みモジュールの色定数(ACCENT 等)を差し替える。
   モジュール側は色定数の定義後に `apptheme.register(globals())` を
   呼んでおく(import 順に依存しない: set_active が先でも register が
   先でも成立する)。クラス属性(DeviceGraph.C_BG 等)は register が
   globals 内のクラスを走査して差し替える。

テストは main() を通らないので汚染しない。テーマを使うテストは
`set_active()` → 検証 → `clear()` で元に戻す(ThemeManager・モジュール
定数とも保存した元値へ復元する)。

## 色設計

PCCS トーン近似。背景=ペール(明度0.90前後・低〜中彩度)、アクセント=
ストロング(彩度0.62・中明度)。ボタン文字色はアクセントの明るさから
自動判定(=124 の text_for_fill と同じ 0.299R+0.587G+0.114B >= 150 →黒)。
イエロー/グリーン系はストロングトーンが明るいので黒文字になる。
文字色の白/黒は**WCAGコントラスト比が大きい方**を採用する
(0.299式=124だと緑・橙が白文字になり実効2〜3:1で沈むため)。
"""

import colorsys
import re

import customtkinter as ctk

# テーマ名(コンフィグ "appearance" に保存する値)→ 色相(度)
THEMES = {
    "pastel_blue": 210,
    "pastel_green": 145,
    "pastel_yellow": 48,
    "pastel_orange": 26,
    "pastel_pink": 340,
    "pastel_purple": 272,
}

# UI表示ラベル(i18nキー)。順序は設定メニューの並び。
# =136: 表示は「パステル」を外した色名のみ(ユーザー決定)。キー値は pastel_* のまま。
THEME_LABELS = {
    "pastel_blue": "ブルー",
    "pastel_green": "グリーン",
    "pastel_yellow": "イエロー",
    "pastel_orange": "オレンジ",
    "pastel_pink": "ピンク",
    "pastel_purple": "パープル",
}

ACTIVE = None        # 適用中のテーマ名(None=従来のダーク/ライト)
PALETTE = None       # 適用中のパレット dict

_modules = []        # register() されたモジュールの globals() たち
_saved_globals = {}  # {id(g): {key: 元値}} 復元用
_saved_classes = {}  # {クラス: {attr: 元値}} 復元用
_saved_theme = {}    # ThemeManager の元値(ライト側) {(widget,key): 値}


def _hx(h_deg, l, s):
    r, g, b = colorsys.hls_to_rgb((h_deg % 360) / 360.0, l, s)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _rel_lum(hex_color):
    """WCAG相対輝度。"""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r = lin(int(hex_color[1:3], 16))
    g = lin(int(hex_color[3:5], 16))
    b = lin(int(hex_color[5:7], 16))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAGコントラスト比(1〜21)。"""
    la, lb = _rel_lum(a), _rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def build_palette(name):
    """テーマ名からパレットを生成する(純粋関数)。"""
    h = THEMES[name]
    accent = _hx(h, 0.48, 0.62)          # ストロングトーン(彩度高・中明度)
    # ボタン文字はWCAGコントラスト比が大きい方(白/黒)を採る。ストロング
    # トーンの明るさは色相で大きく変わる(黄緑系は明るい=黒文字が読める)。
    btn_text = ("#111111" if contrast(accent, "#111111")
                >= contrast(accent, "#ffffff") else "#ffffff")
    return {
        "H": h,                          # 色相(グレー転写=tint_gray 用)
        "BG": _hx(h, 0.905, 0.55),       # ウィンドウ背景(ペール)
        "PANEL": _hx(h, 0.875, 0.48),    # パネル=選択中タブ(旧gray88)
        "CARD": _hx(h, 0.93, 0.55),      # カード面(旧gray94)
        "CARD_BORDER": _hx(h, 0.70, 0.28),
        "CANVAS": _hx(h, 0.945, 0.50),   # 図/グラフ背景(ごく薄い同系色)
        "FIELD": _hx(h, 0.955, 0.50),    # テキストボックス面(旧#F9F9FA)
        "ACCENT": accent,
        "ACCENT_HOVER": _hx(h, 0.41, 0.62),
        "ACCENT_TEXT_L": _hx(h, 0.30, 0.58),   # ライト背景上の色付き文字
        "BTN_TEXT": btn_text,
    }


# モジュール定数の差し替え表: 定数名 → (種別, パレットキー)
#   scalar   = 値そのものを置換
#   light    = (ライト, ダーク) タプルのライト側だけ置換
_GLOBAL_KEYS = {
    "ACCENT": ("scalar", "ACCENT"),
    "ACCENT_HOVER": ("scalar", "ACCENT_HOVER"),
    "ACCENT_TEXT": ("light", "ACCENT_TEXT_L"),
    "PANEL_COLOR": ("light", "PANEL"),
    "CARD_COLOR": ("light", "CARD"),
    "CARD_BORDER": ("light", "CARD_BORDER"),
    "CANVAS_BG_LIGHT": ("scalar", "CANVAS"),
}

# クラス属性の差し替え表(register が globals 内のクラスを走査する)
_CLASS_KEYS = {
    "C_BG": ("light", "CANVAS"),         # DeviceGraph のグラフ背景
    "BG_LIGHT": ("scalar", "CANVAS"),    # RangeSlider 等のライト背景
}


def _new_value(kind, cur, pal_key):
    v = PALETTE[pal_key]
    if kind == "scalar":
        return v
    # light: (ライト, ダーク) のライト側だけ差し替え
    if isinstance(cur, (tuple, list)) and len(cur) == 2:
        return (v, cur[1])
    return v


def _retint_globals(g):
    saved = _saved_globals.setdefault(id(g), {})
    for key, (kind, pal_key) in _GLOBAL_KEYS.items():
        if key in g:
            saved.setdefault(key, g[key])
            g[key] = _new_value(kind, g[key], pal_key)
    for obj in list(g.values()):
        if isinstance(obj, type):
            for attr, (kind, pal_key) in _CLASS_KEYS.items():
                if attr in vars(obj):
                    _saved_classes.setdefault(obj, {}) \
                        .setdefault(attr, getattr(obj, attr))
                    setattr(obj, attr, _new_value(kind, getattr(obj, attr),
                                                  pal_key))


def register(g):
    """モジュールが色定数の定義後に呼ぶ。ACTIVE なら即座に差し替える。"""
    if g not in _modules:
        _modules.append(g)
    if ACTIVE:
        _retint_globals(g)
        # =151: set_active より後に import されたモジュール(editor 等)の
        # 元値も変換表へ取り込む。取りこぼすと、そのモジュールの定数で
        # 塗られたウィジェットが実行中の切り替えで追従しない。
        _build_maps()


# ThemeManager の差し替え対象(ライト側=index 0)。存在しないキーは無視。
def _theme_targets():
    p = PALETTE
    return [
        ("CTk", "fg_color", p["BG"]),
        ("CTkToplevel", "fg_color", p["BG"]),
        # =151: CTkFrame の既定(gray86 / gray81)はここでは差し替えず、
        # _BASE_GRAYS でラッパ側の変換に任せる。ThemeManagerを書き換えると
        # ウィジェットが**テーマ色を抱えてしまい**、テーマ解除のとき元の
        # gray86 へ正確に戻せなくなるため(CARD役の元色が gray86 と
        # CARD_COLORのgray94 の2つになり、区別できない)。見え方は同じ。
        ("CTkButton", "fg_color", p["ACCENT"]),
        ("CTkButton", "hover_color", p["ACCENT_HOVER"]),
        ("CTkButton", "text_color", p["BTN_TEXT"]),
        ("CTkOptionMenu", "fg_color", p["ACCENT"]),
        ("CTkOptionMenu", "button_color", p["ACCENT_HOVER"]),
        ("CTkOptionMenu", "button_hover_color", p["ACCENT_HOVER"]),
        ("CTkCheckBox", "fg_color", p["ACCENT"]),
        ("CTkCheckBox", "hover_color", p["ACCENT_HOVER"]),
        ("CTkSwitch", "progress_color", p["ACCENT"]),
        ("CTkSlider", "progress_color", p["ACCENT"]),
        ("CTkSlider", "button_color", p["ACCENT"]),
        ("CTkSlider", "button_hover_color", p["ACCENT_HOVER"]),
        ("CTkRadioButton", "fg_color", p["ACCENT"]),
        ("CTkProgressBar", "progress_color", p["ACCENT"]),
        # =151: CTkScrollableFrame の label_fg_color(gray78)は**外した**。
        # RVPはラベル付きスクロールフレームを使っておらず効いていない一方、
        # gray78 は TAB_IDLE_COLOR(非選択タブ)と同じ値なので、変換表へ
        # PANEL役として載ると非選択タブの色を横取りしてしまう。
        # =138: 「シナリオ内容」等のテキストボックス面(既定は#F9F9FAのため
        # grayNN転写に掛からない)をごく薄い同系色へ。
        ("CTkTextbox", "fg_color", p["FIELD"]),
    ]


def _patch_theme():
    theme = ctk.ThemeManager.theme
    for widget, key, value in _theme_targets():
        try:
            cur = theme[widget][key]
        except Exception:
            continue
        if isinstance(cur, (tuple, list)) and len(cur) == 2:
            _saved_theme.setdefault((widget, key), list(cur))
            theme[widget][key] = [value, cur[1]]
        elif isinstance(cur, str):
            _saved_theme.setdefault((widget, key), cur)
            theme[widget][key] = value


def set_active(name):
    """テーマを適用する(起動時=root/ウィジェット生成より前に呼ぶ)。

    未知の名前は無視して False(古いコンフィグでも壊れない=appfontと同じ)。
    **実行中の切り替えは switch() を使う**(=151)。
    """
    global ACTIVE, PALETTE
    if name not in THEMES:
        return False
    ACTIVE = name
    PALETTE = build_palette(name)
    _patch_theme()
    _patch_apply_mode()
    for g in _modules:
        _retint_globals(g)
    _build_maps()             # =151 実行中切り替え用の変換表
    return True


# ---- =138: grayNN のテーマ色相への転写 -------------------------------------
#
# 編集画面のイベント/ステート/デバイス/チャンネル面(BOX_BG=gray90)、メイン
# 画面の「最近のシナリオ」(gray92)、タブ・Settings/ヘルプボタンとその
# ホバー色(gray72〜85)など、アプリ中の中間トーンは "grayNN" 名で大量に
# 直書きされている。個別定数化の代わりに、**CTkの色解決の単一路である
# CTkAppearanceModeBaseClass._apply_appearance_mode をラップ**し、ライト
# モードのウィジェットで解決結果が grayNN のときだけテーマ色相の低彩度色へ
# 写す。ダークモード(=タプルのダーク側 gray21 等)は素通し。
#
# - 彩度はグレーの明度に応じて減衰: s = clamp((L-0.35)*0.9, 0, 0.50)。
#   L=0.90 で s≈0.50(BG/CARDと同等の淡さ)、L=0.62(枠線MUTED)で s≈0.24、
#   **L<=0.35(gray10等の文字色)は s=0 で完全に素通し**(可読性を守る)。
# - hex直書き(#RRGGBB)・"transparent"・色名以外は対象外。
# - パッチは set_active() で適用し clear() で復元(テスト汚染なし=既存則)。

_GRAY_RE = re.compile(r"^gr[ae]y(\d{1,3})$")
_tint_cache = {}
_orig_apply_mode = None


def tint_gray(color):
    """ライト側で解決済みの色名 grayNN をテーマ色相の低彩度色へ写す(純粋関数)。

    テーマ非適用時・grayNN以外・暗いグレー(L<=0.35)はそのまま返す。
    """
    if not ACTIVE or not isinstance(color, str):
        return color
    key = (ACTIVE, color)
    if key in _tint_cache:
        return _tint_cache[key]
    out = color
    m = _GRAY_RE.match(color)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 100:
            l = n / 100.0
            s = max(0.0, min(0.50, (l - 0.35) * 0.9))
            if s > 0.0:
                out = _hx(PALETTE["H"], l, s)
    _tint_cache[key] = out
    return out


def _patch_apply_mode():
    """_apply_appearance_mode をラップする(1回だけ)。"""
    global _orig_apply_mode
    if _orig_apply_mode is not None:
        return
    from customtkinter.windows.widgets.appearance_mode import (
        CTkAppearanceModeBaseClass as _Base)
    _orig_apply_mode = _Base._apply_appearance_mode

    def _wrapped(self, color):
        res = _orig_apply_mode(self, color)
        # =151: ACTIVE が None(=素のライト)でも通す。実行中に
        # テーマ→素へ戻したとき、ウィジェットが抱えたままのテーマ色を
        # 元の色へ**訳し戻す**必要があるため。
        if isinstance(res, str):
            try:
                if self._get_appearance_mode() == "light":
                    return live_color(res, self)
            except Exception:
                pass
        return res

    _Base._apply_appearance_mode = _wrapped


def _unpatch_apply_mode():
    global _orig_apply_mode
    if _orig_apply_mode is None:
        return
    from customtkinter.windows.widgets.appearance_mode import (
        CTkAppearanceModeBaseClass as _Base)
    _Base._apply_appearance_mode = _orig_apply_mode
    _orig_apply_mode = None
    _tint_cache.clear()


# ---- =151: 実行中のテーマ切り替え(再起動なし) -------------------------------
#
# ## なぜ従来は再起動が必要だったか
#
# CTkウィジェットは色を**生成時に自分の中へコピー**する。だから
# ThemeManager やモジュール定数を後から書き換えても、既にある
# ウィジェットには効かない(=134の「起動時に1回」方式の理由)。
#
# ## 突破口
#
# 1. 抱えている色が実際のTk色へ変換される道は
#    `_apply_appearance_mode` **1本だけ**(=138でラップ済み)。
# 2. `AppearanceModeTracker.update_callbacks()` を呼ぶと、全CTk
#    ウィジェットがその変換をやり直す。
#
# そこでラッパに「**この色は何の役割(パレットキー)か**」の変換表を持たせ、
# 2を叩けば、生成済みのウィジェットも新しいテーマ色へ塗り替わる。
#
# ## 変換表
#
# - `_ROLE_OF`: 色の値 → 役割。キーは **hexのみ**入れる。
#   grayNN を入れてはいけない: 例えば gray78 は CTkScrollableFrame の
#   既定(=PANEL役)と TAB_IDLE_COLOR(非選択タブ)の**両方**で使われており、
#   役割を横取りするとタブの色が壊れる(試作で実際に踏んだ)。
#   grayNN は従来どおり tint_gray に任せれば色相は付く。
# - `_BASE_OF`: 役割 → **テーマ非適用時の色**。テーマ→素(ライト/ダーク)へ
#   戻すときに使う。1つの役割に複数の元色がある場合(例: CARD役は
#   CTkFrame既定 gray86 と CARD_COLOR の gray94)は**モジュール定数を優先**
#   する(アプリが明示的に指定している側のほうが数が多いため)。
#
# 表は一度作ったら消さない(素へ戻したあとも訳し戻しに使うため)。

_ROLE_OF = {}        # 色の値 → パレットキー
_BASE_OF = {}        # パレットキー → テーマ非適用時の色
_PALETTE_VALUES = set()   # 全テーマのパレット値(訳し戻すべき色の集合)

# 役割の逆引きに使わないパレットキー(色ではない/色相に依存しない)
_MAP_SKIP = ("H",)

# 例外的に役割を割り当てる grayNN(=151)。
# 原則 grayNN は tint_gray に任せる(役割の横取りを避けるため)が、この2つは
# **CTkFrame の既定値そのもの**で、アプリのソースには literal として1箇所も
# 出てこない(確認済み)。ThemeManager を書き換える代わりにここで写すことで、
# ウィジェットは元の gray86/gray81 を抱えたままになり、テーマ解除のときに
# 正確に元へ戻せる。
_BASE_GRAYS = {
    "gray86": "CARD",    # CTkFrame.fg_color の既定
    "gray81": "BG",      # CTkFrame.top_fg_color の既定
    # 以下はモジュール定数の元値。テーマ適用中に生成されたウィジェットは
    # 差し替え後のパレット値を抱えるが、**適用前**に生成されたものは
    # 元の grayNN を抱えたままなので、ここで同じ役割へ写して色を揃える
    # (揃えないと、同じ「カード面」なのに数値が数ステップずれる)。
    # gray94/gray88 はアプリ内で他の用途に使われていないことを確認済み
    # (gray94 の他の出現は tk.Canvas 側=ラッパを通らない)。
    "gray94": "CARD",    # CARD_COLOR のライト側
    "gray88": "PANEL",   # PANEL_COLOR のライト側
    # gray92(ウィンドウ背景)と gray70(カード枠線)は**入れない**:
    # gray92 は「最近のシナリオ」面、gray70 は各所の button_color として
    # 別用途にも使われており、役割を横取りしてしまうため。
}

# 元の色が**別のウィジェット種別とも共有**されているため、値だけでは役割を
# 決められないもの(=151)。{色: (パレットキー, 効かせるクラス名)}。
#   #F9F9FA は CTkTextbox の既定であると同時に CTkEntry/CTkComboBox の
#   既定でもある。=134はThemeManagerのCTkTextboxだけを差し替えていたので、
#   **入力欄は白のまま**が正しい見え方。値だけで写すと入力欄まで色が付いて
#   しまう(実際にスクリーンショット比較で検出した)。
#   #DCE4EE は CTkButton.text_color の既定であると同時に、CTkCheckBox の
#   チェックマーク色・CTkOptionMenu の文字色でもある。=134が差し替えるのは
#   ボタンの文字色だけなので、クラスで限定する。
_CLASS_BOUND = {
    "#F9F9FA": ("FIELD", "CTkTextbox"),
    "#DCE4EE": ("BTN_TEXT", "CTkButton"),
}


def _is_kind(widget, cls_name: str) -> bool:
    """widget が cls_name(またはその派生)か。CTk のクラス名で判定する。"""
    try:
        return any(c.__name__ == cls_name for c in type(widget).__mro__)
    except Exception:
        return False


def _theme_targets_roles():
    """_theme_targets() を (ウィジェット, キー, パレットキー) で返す。"""
    inv = {v: k for k, v in PALETTE.items() if isinstance(v, str)}
    return [(w, k, inv.get(v)) for w, k, v in _theme_targets()]


def _learn(value, pal_key, prefer_base=False):
    """1つの「元の色 → 役割」を変換表へ覚えさせる。"""
    base = value[0] if isinstance(value, (tuple, list)) else value
    if not isinstance(base, str):
        return
    # クラス依存のものは値だけで写さない(live_color がクラスを見て判断する)
    if base.startswith("#") and base not in _CLASS_BOUND:
        _ROLE_OF.setdefault(base, pal_key)
    if prefer_base or pal_key not in _BASE_OF:
        _BASE_OF[pal_key] = base


def _build_maps():
    """変換表を作る(set_active から呼ばれる。積み増し方式で消さない)。"""
    # 全テーマのパレット値 → 役割。テーマA→テーマBの乗り換えはこれで通る。
    # 色相が違うので、テーマ間で値が衝突することはない。
    for name in THEMES:
        for pal_key, value in build_palette(name).items():
            if pal_key in _MAP_SKIP:
                continue
            if isinstance(value, str) and value.startswith("#"):
                _ROLE_OF.setdefault(value, pal_key)
                _PALETTE_VALUES.add(value)
    # CTkFrame の既定 grayNN(ThemeManagerは書き換えない=151)。
    # これらは「元の色」側なので _BASE_OF には入れない(役割の元色は
    # ThemeManager/モジュール定数の退避値から決める)。
    for gray, pal_key in _BASE_GRAYS.items():
        _ROLE_OF.setdefault(gray, pal_key)
    # ThemeManager の元値(CTk既定)
    roles = {(w, k): pk for w, k, pk in _theme_targets_roles()}
    for (widget, key), value in _saved_theme.items():
        pal_key = roles.get((widget, key))
        if pal_key:
            _learn(value, pal_key)
    # モジュール定数の元値(こちらを _BASE_OF の本命にする)
    for saved in _saved_globals.values():
        for key, value in saved.items():
            pal_key = _GLOBAL_KEYS.get(key, (None, None))[1]
            if pal_key:
                _learn(value, pal_key, prefer_base=True)


def live_color(res, widget=None):
    """解決済みの単色を、いま有効なテーマの色へ写す(=151の要)。

    - テーマ適用中: 役割が分かる色はそのテーマの色へ。grayNN は tint_gray。
    - テーマ非適用: ウィジェットが抱えたままの**テーマ色だけ**を元の色へ
      戻す。元の色(gray86 等)はそのまま返す=二重変換しない。

    widget を渡すと、値だけでは役割が決まらない色(_CLASS_BOUND)を
    クラスで判断できる。
    """
    bound = _CLASS_BOUND.get(res)
    if bound is not None:
        pal_key, cls_name = bound
        if ACTIVE and pal_key in PALETTE and _is_kind(widget, cls_name):
            return PALETTE[pal_key]
        return res
    pal_key = _ROLE_OF.get(res)
    if pal_key is not None:
        if ACTIVE and pal_key in PALETTE:
            return PALETTE[pal_key]
        if res in _PALETTE_VALUES:
            return _BASE_OF.get(pal_key, res)   # 訳し戻し
        return res
    return tint_gray(res)          # 非適用時は素通し(tint_gray の仕様)


def _restore_values():
    """ThemeManager・モジュール定数・クラス属性を元の値へ戻す(ラッパは残す)。"""
    theme = ctk.ThemeManager.theme
    for (widget, key), value in _saved_theme.items():
        try:
            theme[widget][key] = value
        except Exception:
            pass
    _saved_theme.clear()
    for g in _modules:
        saved = _saved_globals.pop(id(g), {})
        for key, value in saved.items():
            g[key] = value
    for cls, attrs in _saved_classes.items():
        for attr, value in attrs.items():
            setattr(cls, attr, value)
    _saved_classes.clear()


def switch(name):
    """実行中にテーマを切り替える(=151。再起動不要)。

    name="" / None でカラーテーマを解除する(素のライト/ダークへ戻す)。
    戻り値は適用したテーマ名("" =解除)。

    **自前描画(tk.Canvas)のウィジェットはこの関数では追従しない**。
    モジュール定数・クラス属性は差し替わるので、呼び出し側が再描画すること
    (main.RVPApp._refresh_theme_widgets が担当)。
    """
    global ACTIVE, PALETTE
    from customtkinter.windows.widgets.appearance_mode import (
        AppearanceModeTracker as _Tracker)
    name = name or ""
    _restore_values()
    ACTIVE = None
    PALETTE = None
    if name in THEMES:
        set_active(name)           # ThemeManager・定数を貼り直し、表も更新
    else:
        name = ""
        _patch_apply_mode()        # 解除後も「訳し戻し」のためラッパは残す
    _tint_cache.clear()
    _Tracker.update_callbacks()    # 全CTkウィジェットに色を解決し直させる
    return name


def clear():
    """テスト用: ThemeManager・モジュール定数・クラス属性を元へ戻す。

    変換表とラッパも含めて**完全に**元へ戻す(テスト汚染を残さない)。
    実行中の切り替えには switch() を使うこと。
    """
    global ACTIVE, PALETTE
    _unpatch_apply_mode()
    _restore_values()
    _ROLE_OF.clear()
    _BASE_OF.clear()
    _PALETTE_VALUES.clear()
    ACTIVE = None
    PALETTE = None
