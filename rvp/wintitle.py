"""Windows のタイトルバーをダーク/ライトに合わせる(=369)。

customtkinter は CTkToplevel の作成時にタイトルバーの色(DWM の
DWMWA_USE_IMMERSIVE_DARK_MODE)を設定する。ところが作成直後に
`wm transient` を掛けると、Windows 版 Tk は外枠ウィンドウ(wrapper HWND)を
作り直すため、その設定が失われてタイトルバーが白に戻る(ダークのときだけ
目立つ。ユーザー報告=再生オプションの上部が白い)。

ここでは withdraw を伴わずに(CTk の withdraw→deiconify の往復と干渉
しないように)、今の外枠へ直接 DWM 属性を設定し、枠の再描画を促す。
Windows 以外・失敗時は何もしない(見た目の補助なので開くことを優先)。
"""
from __future__ import annotations

import sys

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
_SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED


def _windll():
    import ctypes
    return ctypes, ctypes.windll


def fix_titlebar(win, mode: str | None = None, *, _platform=None, _dll=None) -> bool:
    """win(Tk/CTk のトップレベル)のタイトルバーを mode("Dark"/"Light")に。

    mode 省略時は customtkinter の現在の外観。設定できたら True。
    _platform/_dll はテスト用の差し替え口。
    """
    plat = _platform if _platform is not None else sys.platform
    if not str(plat).startswith("win"):
        return False
    try:
        if mode is None:
            import customtkinter as ctk
            mode = ctk.get_appearance_mode()
        value = 1 if str(mode).lower() == "dark" else 0
        ctypes, windll = _dll if _dll is not None else _windll()
        hwnd = windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            return False
        v = ctypes.c_int(value)
        if windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(v), ctypes.sizeof(v)) != 0:
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1,
                ctypes.byref(v), ctypes.sizeof(v))
        # 枠を描き直させる(Windows 10 は再描画しないと色が変わらない)
        windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP)
        return True
    except Exception:
        return False


def schedule_fix_titlebar(win, delays=(20, 300, 700)) -> None:
    """CTk の作成時の withdraw→deiconify(約5〜200ms)が収まった後にも効くよう、
    数回に分けて fix_titlebar を掛ける(=369)。"""
    def run():
        try:
            if win.winfo_exists():
                fix_titlebar(win)
        except Exception:
            pass
    for d in delays:
        try:
            win.after(d, run)
        except Exception:
            pass
