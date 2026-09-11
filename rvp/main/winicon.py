"""Windows のタスクバー/ウィンドウアイコン(=144)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import sys
import tkinter as tk



# =144 タスクバーアイコン対策。Windowsのタスクバーはウィンドウを
# AppUserModelID 単位でグループ化してアイコンを決めるため、python.exe から
# 起動するとタスクバーには Python のアイコンが出る(タイトルバーは iconbitmap
# が効く)。独自IDを宣言するとウィンドウ自身のアイコン(=143)が使われる。
# exe化(PyInstaller)後も無害。
APP_USER_MODEL_ID = "RVP.RVP"


def _set_win_app_id():
    """main() 起動時のみ・ウィンドウ生成より前に呼ぶ。戻り値=適用できたか。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID)
        return True
    except Exception:
        return False


# =149 一度LoadImageしたHICONの使い回しキャッシュ(プロセス寿命・破棄しない)。
# {"done": True, "big": HICON|None, "small": HICON|None}
_WIN_ICON_CACHE = {}


def _win_user32():
    """user32を取得し、64bitでのHICON/HWND切り詰め防止の型宣言を試みる
    (テストのスタブ相手では失敗してよい)。"""
    import ctypes
    user32 = ctypes.windll.user32
    try:
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_void_p]
    except Exception:
        pass
    return user32


def _win_icon_handles():
    """(big, small) の HICON。初回だけ LoadImage し以後はキャッシュを返す。
    サイズはシステムメトリクス(DPIスケーリング反映済み)に合わせる。"""
    if _WIN_ICON_CACHE.get("done"):
        return (_WIN_ICON_CACHE.get("big"), _WIN_ICON_CACHE.get("small"))
    _WIN_ICON_CACHE["done"] = True
    ico = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "rvp.ico")   # =302: rvp/main/ から1段上
    if not os.path.isfile(ico):
        return (None, None)
    try:
        user32 = _win_user32()
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        # (キー, metric, フォールバック): SM_CXICON=11 / SM_CXSMICON=49
        for key, metric, fb in (("big", 11, 32), ("small", 49, 16)):
            cx = user32.GetSystemMetrics(metric) or fb
            h = user32.LoadImageW(None, ico, IMAGE_ICON, cx, cx,
                                  LR_LOADFROMFILE)
            _WIN_ICON_CACHE[key] = h or None
    except Exception:
        pass
    return (_WIN_ICON_CACHE.get("big"), _WIN_ICON_CACHE.get("small"))


def _apply_win_icons(widget):
    """=147/=149 対象ウィンドウ(root/Toplevel)へ WM_SETICON で高精細アイコンを
    直接セットする。戻り値=適用できたか。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        big, small = _win_icon_handles()
        if not (big or small):
            return False
        user32 = _win_user32()
        widget.update_idletasks()
        hwnd = user32.GetAncestor(widget.winfo_id(), 2)  # 2=GA_ROOT
        if not hwnd:
            return False
        WM_SETICON = 0x0080
        applied = False
        for which, h in ((1, big), (0, small)):  # 1=ICON_BIG / 0=ICON_SMALL
            if h:
                user32.SendMessageW(hwnd, WM_SETICON, which, h)
                applied = True
        return applied
    except Exception:
        return False


def _set_win_taskbar_icons(root):
    """=147 タスクバー/タイトルバーのぼやけ対策。実行中のタスクバーボタンや
    タイトルバーは「ウィンドウのアイコン」を使うが、Tk の iconbitmap はそれを
    固定の小サイズで生成するため拡大時に滲む。root へ高精細アイコンを直接
    セットし、=149: 以後開く**すべての Toplevel(編集画面・ヘルプ・設定等)にも
    <Map> フックで自動適用**する(適用済みは _rvp_win_icons 印で二重適用防止)。
    main() 起動時のみ・_set_app_icon() の後に呼ぶ。戻り値=rootへ適用できたか。
    HICON はプロセス寿命まで使うので破棄しない。"""
    if not sys.platform.startswith("win"):
        return False
    applied = _apply_win_icons(root)

    def _on_map(event):
        w = getattr(event, "widget", None)
        if isinstance(w, tk.Toplevel) and not getattr(w, "_rvp_win_icons",
                                                      False):
            w._rvp_win_icons = True
            try:
                _apply_win_icons(w)
            except Exception:
                pass

    try:
        root.bind_all("<Map>", _on_map, add="+")
    except Exception:
        pass
    return applied


def _set_app_icon(root):
    """=143 アプリアイコン(rvp/assets/)。main() 起動時のみ呼ぶ
    (winstate / appfont / apptheme と同じ「テストを汚染しない」流儀)。

    - Windows: iconbitmap(default=rvp.ico) で全 Toplevel(編集画面・ヘルプ等)へ
      既定として波及させる。CustomTkinter は CTk/CTkToplevel の生成200ms後に
      自前のCTkロゴを iconbitmap で押し付ける(_windows_set_titlebar_icon)ため、
      CTkToplevel 側のそれを無効化して default= の継承を守る
      (root側は iconbitmap 呼び出しで _iconbitmap_method_called が立ち抑止される)。
    - その他OS: iconphoto(True, rvp.png)。True=以後の Toplevel の既定にもなる。
      PhotoImage はGCされると消えるので root へ参照を残す。
    失敗してもアイコンが出ないだけなので起動は続行する。戻り値=適用できたか。
    """
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets")   # =302: rvp/main/ から1段上
    try:
        if sys.platform.startswith("win"):
            ico = os.path.join(base, "rvp.ico")
            if os.path.isfile(ico):
                root.iconbitmap(default=ico)
                try:
                    ctk.CTkToplevel._windows_set_titlebar_icon = (
                        lambda self: None)
                except Exception:
                    pass
                return True
        png = os.path.join(base, "rvp.png")
        if os.path.isfile(png):
            img = tk.PhotoImage(file=png)
            root.iconphoto(True, img)
            root._rvp_icon_img = img  # 参照保持(GC防止)
            return True
    except Exception:
        pass
    return False
