"""=115: ウィンドウのサイズ・位置の記憶(マルチディスプレイ対応)。

対象は**メインウィンドウ / シナリオ編集ウィンドウ / ヘルプ**の3つ
(ユーザー決定。変数一覧などの一時的な小ダイアログは従来どおり毎回同じ
サイズで親の近くに出す)。保存先は他の設定と同じ ~/.rvp_config.json の
`"windows"` セクション:

    "windows": {
      "main":   {"geom": "690x820+2560+120", "zoomed": false},
      "editor": {"geom": "1360x820+0+0",     "zoomed": true},
      "help":   {"geom": "760x600+300+200",  "zoomed": false}
    }

## 座標の単位について(重要)

customtkinter は `CTk.geometry()` / `CTkToplevel.geometry()` を**上書きして
ウィンドウスケーリングを掛ける**(`_apply_geometry_scaling`)。一方
`winfo_x/y/width/height` とモニタ構成(OS問い合わせ)は**実ピクセル**。
両者を混ぜると高DPI環境でずれるので、このモジュールは**素の
`wm_geometry()`(=tkinter側。CTkは上書きしていない)だけを使い、
一貫して実ピクセルで読み書きする**。

- 初回起動(保存なし)は**従来どおりの既定配置**(CTkの`geometry()`経由)。
  そこで実際に置かれた実ピクセルを保存するので、次回以降は同じ見た目に
  復元される。DPIが違う環境でも「前回見えていたそのもの」を再現する。

## マルチディスプレイ

- Windows は `EnumDisplayMonitors`(ctypes・**追加ライブラリなし**)で
  全モニタの**作業領域**(rcWork=タスクバーを除いた領域)を取得する。
  副ディスプレイの座標は負にも 2560 のような大きな値にもなるため、
  「画面幅を超えていたら画面外」といった単純な判定はしない。
- Windows以外(開発環境のLinux等)は tk の screenwidth/height を1枚の
  モニタとみなす。
- 保存位置がどのモニタにも十分に載っていなければ(ディスプレイを外した等)
  **既定位置へ戻す**(ユーザー決定)。ウィンドウが現在のモニタより大きい
  ときはそのモニタに収まるよう縮める。
"""

import re
import sys

from .i18n import load_config, save_config

CFG_KEY = "windows"

# =115: **既定は無効**で、`main()` から enable() されたときだけ働く
# (=83のCTk再入ガードと同じ作法)。UIテストは1つの設定ファイルを共用して
# いるため、テストが動かしたウィンドウの大きさを覚えてしまうと**別のテスト
# の初期サイズが変わって落ちる**(test_play_layout の 690x820 など)。
# 記憶そのものの検証は test_winstate.py が明示的に enable() して行う。
ENABLED = False


def enable(on: bool = True):
    global ENABLED
    ENABLED = bool(on)

_GEOM_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")

# 「載っている」と認めるための最小の重なり(px)。タイトルバーをつかめれば
# 復帰できるので、幅160×高さ80もあれば十分。
MIN_VISIBLE_W = 160
MIN_VISIBLE_H = 80
# タイトルバーが画面上端より上に出ていないか。最大化解除直後などは
# -8 程度の負値になる環境があるので少しだけ許容する。
TOP_SLACK = 40


def parse_geometry(text):
    """"WxH+X+Y" を (x, y, w, h) にする。壊れていれば None。"""
    if not isinstance(text, str):
        return None           # 壊れたコンフィグ(数値・None等)は使わない
    m = _GEOM_RE.match(text.strip())
    if not m:
        return None
    w, h, x, y = int(m.group(1)), int(m.group(2)), \
        int(m.group(3)), int(m.group(4))
    if w < 100 or h < 100:
        return None           # 異常値(アイコン化直後など)は使わない
    return (x, y, w, h)


def format_geometry(rect):
    x, y, w, h = rect
    return f"{w}x{h}{x:+d}{y:+d}"


# ---------------- モニタ構成 ----------------

def _windows_monitors():
    """Windowsの全モニタの作業領域を [(x, y, w, h), ...] で返す。"""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []
    try:
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, wintypes.HANDLE, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        rects = []

        def _cb(hmon, _hdc, _lprc, _data):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                rects.append((int(r.left), int(r.top),
                              int(r.right - r.left), int(r.bottom - r.top)))
            return 1

        user32.EnumDisplayMonitors(0, None, proc_type(_cb), 0)
        return [r for r in rects if r[2] > 0 and r[3] > 0]
    except Exception:
        return []


def monitor_rects(win=None):
    """利用可能なモニタの矩形(実ピクセル)。取得できなければ空リスト。"""
    if sys.platform.startswith("win"):
        rects = _windows_monitors()
        if rects:
            return rects
    if win is not None:
        try:
            return [(0, 0, int(win.winfo_screenwidth()),
                     int(win.winfo_screenheight()))]
        except Exception:
            pass
    return []


# ---------------- 判定 ----------------

def _overlap(rect, mon):
    x, y, w, h = rect
    mx, my, mw, mh = mon
    ix = min(x + w, mx + mw) - max(x, mx)
    iy = min(y + h, my + mh) - max(y, my)
    return max(0, ix), max(0, iy)


def is_usable(rect, monitors):
    """どこかのモニタに「操作できる程度」載っているか。

    モニタ構成が取れなかった場合(monitors が空)は判定できないので、
    保存値を信用して True にする(取得失敗を理由に位置を捨てない)。
    """
    if not monitors:
        return True
    x, y, w, h = rect
    for mon in monitors:
        ix, iy = _overlap(rect, mon)
        if ix >= min(MIN_VISIBLE_W, w) and iy >= min(MIN_VISIBLE_H, h) \
                and y >= mon[1] - TOP_SLACK:
            return True
    return False


def fit_into_monitor(rect, monitors):
    """一番よく載っているモニタからはみ出す分だけ縮めて寄せる。

    位置の記憶が目的なので**動かすのは必要な分だけ**。載っていないときは
    None(=既定位置へ戻す)。
    """
    if not monitors:
        return rect
    if not is_usable(rect, monitors):
        return None
    best, best_area = None, -1
    for mon in monitors:
        ix, iy = _overlap(rect, mon)
        if ix * iy > best_area:
            best, best_area = mon, ix * iy
    mx, my, mw, mh = best
    x, y, w, h = rect
    w = min(w, mw)
    h = min(h, mh)
    x = max(mx, min(x, mx + mw - w))
    y = max(my, min(y, my + mh - h))
    return (x, y, w, h)


# ---------------- ポップアップの配置(=132) ----------------
# 編集画面から開く小ダイアログ・カラーパレットの置き場所を決める純粋関数群。
# マルチディスプレイでは「親(またはクリック点)のあるモニタ」を基準にする。
# tk の winfo_screenwidth() は Windows ではプライマリモニタの幅を返すため、
# それでクランプするとサブディスプレイ上の座標がメイン側へ引き戻される
# (=132の不具合報告)。座標はすべて実ピクセル。

def monitor_at(x, y, monitors):
    """点(x, y)を含むモニタの矩形。どのモニタにも無ければ None。"""
    for mon in monitors:
        mx, my, mw, mh = mon
        if mx <= x < mx + mw and my <= y < my + mh:
            return mon
    return None


def _best_monitor_for(rect, monitors):
    """矩形と最もよく重なるモニタ。全く重ならなければ None。"""
    best, best_area = None, 0
    for mon in monitors:
        ix, iy = _overlap(rect, mon)
        if ix * iy > best_area:
            best, best_area = mon, ix * iy
    return best


def clamp_point_popup(x, y, w, h, monitors, fallback, margin=4):
    """点(x, y)を左上とする w×h のポップアップを、その点のあるモニタ内へ
    収める。点がどのモニタにも無ければ fallback 矩形(従来のスクリーン全体)
    でクランプする。"""
    mon = monitor_at(x, y, monitors) or fallback
    mx, my, mw, mh = mon
    nx = min(max(mx, x), mx + mw - w - margin)
    ny = min(max(my, y), my + mh - h - margin)
    return int(nx), int(ny)


def center_popup_pos(parent_rect, w, h, monitors, shift=50):
    """親ウィンドウ中央に w×h を重ね、shift px だけ左上へずらした位置。

    親の中心があるモニタ(無ければ親と最もよく重なるモニタ)の内側へ
    クランプするので、サブディスプレイの親から開いてもメイン側へ出ない。
    モニタ構成が取れなければクランプせずそのまま返す。"""
    px, py, pw, ph = parent_rect
    x = px + pw // 2 - w // 2 - shift
    y = py + ph // 2 - h // 2 - shift
    cx, cy = px + pw // 2, py + ph // 2
    mon = monitor_at(cx, cy, monitors) or _best_monitor_for(parent_rect,
                                                            monitors)
    if mon is not None:
        mx, my, mw, mh = mon
        x = max(mx, min(x, mx + mw - w))
        y = max(my, min(y, my + mh - h))
    return int(x), int(y)


# ---------------- 保存/復元 ----------------

def load_entry(key):
    cfg = load_config()
    win_cfg = cfg.get(CFG_KEY)
    if not isinstance(win_cfg, dict):
        return None
    entry = win_cfg.get(key)
    return entry if isinstance(entry, dict) else None


def store_entry(key, geom, zoomed):
    cfg = load_config()
    win_cfg = cfg.get(CFG_KEY)
    if not isinstance(win_cfg, dict):
        win_cfg = {}
    win_cfg[key] = {"geom": geom, "zoomed": bool(zoomed)}
    cfg[CFG_KEY] = win_cfg
    save_config(cfg)


class WindowMemory:
    """1つのウィンドウのサイズ・位置・最大化状態を覚える係。

    使い方:
        self._winmem = WindowMemory(win, "editor")
        if not self._winmem.restore():
            ...従来どおりの既定配置...
        self._winmem.watch()      # 以降の移動/リサイズを追跡して自動保存

    保存の契機は **①移動・リサイズが止まってから SAVE_DELAY_MS 後**
    (デバウンス)と **②閉じるとき(`save_now`)** の2つ。①があるので
    アプリが異常終了しても直前の配置は残る。
    """

    SAVE_DELAY_MS = 900

    def __init__(self, win, key):
        self.win = win
        self.key = key
        self.applied = None        # restore() で実際に適用した矩形
        self._normal_geom = None   # 最後に見た「最大化していない」配置
        self._zoomed = False
        self._after_id = None
        self._watching = False

    # ---- 現在の状態 ----

    def raw_geometry(self):
        """実ピクセルの "WxH+X+Y"(CTkのスケーリングを通さない)。"""
        try:
            return self.win.wm_geometry()
        except Exception:
            return ""

    def is_zoomed(self) -> bool:
        try:
            if str(self.win.state()) == "zoomed":     # Windows
                return True
        except Exception:
            pass
        try:
            return bool(self.win.attributes("-zoomed"))   # X11
        except Exception:
            return False

    # ---- 復元 ----

    def restore(self) -> bool:
        """保存値があれば適用して True。無い/画面外なら False(=既定配置へ)。"""
        if not ENABLED:
            return False
        entry = load_entry(self.key)
        if not entry:
            return False
        rect = parse_geometry(entry.get("geom"))
        if rect is None:
            return False
        rect = fit_into_monitor(rect, monitor_rects(self.win))
        if rect is None:
            return False          # ディスプレイを外した等 → 既定位置へ
        self.applied = rect
        self._normal_geom = format_geometry(rect)
        self.reapply()
        if entry.get("zoomed"):
            self._zoomed = True
            # CTkToplevelは生成直後に withdraw→deiconify するので、
            # 最大化はそれが落ち着いてから掛ける。
            try:
                self.win.after(120, self.apply_zoom)
            except Exception:
                self.apply_zoom()
        return True

    def reapply(self):
        """restore() で決めた矩形をもう一度適用する(遅延再適用用)。"""
        if self.applied is None:
            return False
        try:
            self.win.wm_geometry(format_geometry(self.applied))
            return True
        except Exception:
            return False

    def apply_zoom(self):
        try:
            self.win.state("zoomed")          # Windows
            return True
        except Exception:
            pass
        try:
            self.win.attributes("-zoomed", True)   # X11
            return True
        except Exception:
            return False

    # ---- 追跡と保存 ----

    def watch(self):
        """<Configure> を監視して、止まったら保存する。"""
        if self._watching or not ENABLED:
            return
        self._watching = True
        try:
            self.win.bind("<Configure>", self._on_configure, add="+")
        except Exception:
            self._watching = False

    def _on_configure(self, event=None):
        if event is not None and getattr(event, "widget", None) is not self.win:
            return          # 子ウィジェットのConfigureは無視
        self.snapshot()
        try:
            if self._after_id is not None:
                self.win.after_cancel(self._after_id)
            self._after_id = self.win.after(self.SAVE_DELAY_MS, self.save_now)
        except Exception:
            self._after_id = None

    def snapshot(self):
        """今の配置を内部に控える(最大化中は通常時の配置を保持する)。"""
        self._zoomed = self.is_zoomed()
        if self._zoomed:
            return            # 最大化中の矩形は覚えない(解除時に戻せなくなる)
        geom = self.raw_geometry()
        if parse_geometry(geom):
            self._normal_geom = geom

    def save_now(self):
        """設定ファイルへ書き出す。閉じるときにも呼ぶ。"""
        self._after_id = None
        if not ENABLED:
            return False
        try:
            self.snapshot()
        except Exception:
            pass
        if not self._normal_geom:
            return False
        try:
            store_entry(self.key, self._normal_geom, self._zoomed)
            return True
        except Exception:
            return False

    def install_close_hook(self, on_close=None):
        """×ボタンで閉じる前に保存する。on_close 省略時は destroy。"""
        def _close():
            self.save_now()
            try:
                (on_close or self.win.destroy)()
            except Exception:
                pass
        try:
            self.win.protocol("WM_DELETE_WINDOW", _close)
        except Exception:
            pass
        return _close
