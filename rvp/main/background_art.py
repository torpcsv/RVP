"""バックグラウンドイラスト(=262: bg_fit_geometry / BackgroundArt)。"""
from __future__ import annotations

import logging
import sys
import tkinter as tk
from ..i18n import tr

from .common import HAS_PIL, PILImage, PILImageTk


def bg_fit_geometry(region_w: int, region_h: int,
                    img_w: int, img_h: int) -> tuple[int, int, int]:
    """=262: 背景イラストの配置計算(縦フィット・中央寄せ・左右見切れ)。

    ユーザー仕様(Q4):
    - 画像は領域の**縦方向にフィット**(アスペクト比維持)。
    - 横は中央寄せ。領域より広ければ左右が見切れ、横へ広げると隠れていた
      部分が現れる。領域の方が広ければ左右に黒帯(黒はキャンバスのbg)。
    戻り値: (x0, 拡縮後の幅, 拡縮後の高さ)。x0 は領域内での画像左端
    (負=左が見切れている)。
    """
    region_w = max(1, int(region_w))
    region_h = max(1, int(region_h))
    img_w = max(1, int(img_w))
    img_h = max(1, int(img_h))
    sw = max(1, round(img_w * region_h / img_h))
    return (region_w - sw) // 2, sw, region_h


class BackgroundArt:
    """=262〜=265: 背景イラスト(クリック透過オーバーレイ方式)。

    縁なしの画像ウィンドウ(イラスト+左右黒帯)を**rootのクライアント領域
    (タイトルバーの下)だけに**重ね、薄いalphaで表示する。Windowsでは
    WS_EX_TRANSPARENT(+LAYERED/NOACTIVATE)で**クリック透過**にするので、
    下のUIはそのまま操作できる。合成結果は「UIを半透明化して背後に画像」
    (=263/=264方式)と数学的に同一(image*a + UI*(1-a))だが、rootのalphaを
    一切触らないため**タイトルバーは完全に不透過**になり(=265ユーザー要望)、
    最小化復帰などrootの見た目への副作用もない。

    クリック透過を設定できない環境(Windows以外)では、従来どおり
    「rootのalphaを下げて画像を真下に敷く」**アンダーレイ方式へ自動
    フォールバック**する(mode="underlay")。

    - 適用条件(AND): シナリオが background を持つ / 設定「表示する」ON /
      **現在タブ=再生**(Q1)。外れたら非表示(従来表示と完全一致)。
    - 画像: dim%(黒ブレンド=Q5)焼き込み後、**クライアント領域の高さへ
      縦フィット**・中央寄せ・左右見切れ・画像より広い分は黒(Q4)。
    - 透け具合: 弱/中/強。UI不透明度 0.88/0.76/0.62 に相当
      (オーバーレイ側のalphaは 1-値 = 0.12/0.24/0.38)。
    - ちらつき対策(=264): event.widget is root フィルタ(bindtagの罠)・
      描画/ジオメトリのキャッシュ・約120msフェード・非表示はウィンドウの
      alpha0(withdrawしない。最小化時のみwithdraw)。
    - 起動直後の位置ずれ対策(=265): rootの位置がWMで確定する前に表示が
      走ると画像が画面左上に張り付く(実機FB)。表示後 60/200/500ms に
      再同期をリトライし、root <Configure> 後にも遅延再同期を仕込む
      (キャッシュ比較なので確定済みなら何もしない)。
    """

    ALPHA_LEVELS = {"weak": 0.88, "mid": 0.76, "strong": 0.62}
    # =269: dim<40では画像の主張を線形に強化し、dim=0でこの値(画像側alpha)
    # に達する。dim>=40は従来どおり(1-ALPHA_LEVELS)のまま=既定の見た目不変。
    IMG_ALPHA_DIM0 = {"weak": 0.28, "mid": 0.48, "strong": 0.62}
    REBUILD_DELAY_MS = 150
    FADE_STEPS = 4
    FADE_INTERVAL_MS = 30
    RESYNC_DELAYS_MS = (60, 200, 500)

    def __init__(self, app, root):
        self.app = app
        self.root = root
        self.available = HAS_PIL
        self.user_enabled = True          # アプリ設定「背景イラスト 表示する」
        self.alpha_level = "mid"          # アプリ設定「透け具合」weak/mid/strong
        self.spec = None                  # scenario.BackgroundSpec | None
        self.shown = False
        self.mode = None                  # "overlay"(クリック透過) / "underlay"
        self._under = None                # 画像ウィンドウ(tk.Toplevel)
        self._canvas = None
        self._img_item = None             # キャンバス上の画像アイテムid
        self._drawn = None                # (id(photo), x0) 描画済みキャッシュ
        self._last_geo = None             # (w,h,x,y) 同期済みキャッシュ
        self._src = None                  # dim焼き込み済みPIL画像(原寸)
        self._src_key = None              # (path, dim)
        self._photo = None                # ImageTk.PhotoImage(拡縮後)
        self._photo_h = 0
        self._x0 = 0
        self._rebuild_job = None
        self._fade_jobs = {}              # win -> after id
        self._hwnd = None                 # 画像窓のWin32ハンドル(=266)
        try:
            root.bind("<Configure>", self._on_root_configure, add="+")
            root.bind("<Map>", self._on_root_map, add="+")
            root.bind("<Unmap>", self._on_root_unmap, add="+")
            root.bind("<FocusIn>", self._on_root_focus, add="+")
            root.bind("<FocusOut>", self._on_root_focus_out, add="+")
        except Exception:
            pass

    # ---------------- 外部API ----------------

    def set_scenario(self, spec) -> None:
        """シナリオ読み込み時に呼ぶ(spec=BackgroundSpec|None)。"""
        self.spec = spec
        self._apply()

    def set_user_enabled(self, flag: bool) -> None:
        """アプリ設定(表示ON/OFF)の反映。"""
        self.user_enabled = bool(flag)
        self._apply()

    def set_alpha_level(self, level: str) -> None:
        """アプリ設定(透け具合 weak/mid/strong)の反映。"""
        if level in self.ALPHA_LEVELS:
            self.alpha_level = level
            if self.shown:
                self._restack()           # =267: Settings操作直後の順序ずれ対策
                if self.mode == "overlay":
                    self._fade_to(self._under, self._img_alpha())
                else:
                    self._fade_to(self.root, self._ui_opacity())

    def on_tab_changed(self) -> None:
        """タブ切替時に呼ぶ(再生タブ以外では表示しない=Q1)。"""
        self._apply()

    def _img_alpha(self) -> float:
        """画像側のalpha。基準=1-UI不透明度(弱0.12/中0.24/強0.38)。

        =269: シナリオのdimが40未満のときは、dim=0で IMG_ALPHA_DIM0
        (弱0.28/中0.48/強0.62)に達するよう線形に強化する(「暗さ0のとき
        画像の主張を強くしたい」ユーザー要望)。dim>=40は従来どおり。
        """
        base = round(1.0 - self.ALPHA_LEVELS[self.alpha_level], 2)
        dim = self.spec.dim if self.spec is not None else 40
        if dim >= 40:
            return base
        t = (40 - max(0, dim)) / 40.0
        top = self.IMG_ALPHA_DIM0[self.alpha_level]
        return round(base + (top - base) * t, 3)

    def _ui_opacity(self) -> float:
        """アンダーレイ方式でのroot側alpha(=1-画像側alpha)。"""
        return round(1.0 - self._img_alpha(), 3)

    # ---------------- 表示/非表示 ----------------

    def _play_tab_visible(self) -> bool:
        tabs = getattr(self.app, "tabs", None)
        return tabs is not None and \
            getattr(tabs, "_current", None) == tr("再生")

    def _want_shown(self) -> bool:
        return bool(self.available and self.user_enabled
                    and self.spec is not None and self._play_tab_visible())

    def _apply(self) -> None:
        want = self._want_shown()
        if want and not self._load_source():
            want = False
        if want:
            self._show()
        else:
            self._hide()

    def _load_source(self) -> bool:
        """dim焼き込み済みの原寸画像を用意する。失敗はFalse(機能無効)。"""
        key = (self.spec.file, self.spec.dim)
        if key == self._src_key and self._src is not None:
            return True
        try:
            img = PILImage.open(self.spec.file)
            img = img.convert("RGB")
            dim = max(0, min(100, int(self.spec.dim)))
            if dim > 0:
                black = PILImage.new("RGB", img.size, (0, 0, 0))
                img = PILImage.blend(img, black, dim / 100.0)
            self._src = img
            self._src_key = key
            self._photo_h = 0            # 再拡縮を強制
            return True
        except Exception as e:           # 壊れた画像等は静かに無効化
            logging.getLogger(__name__).warning(
                "background image load failed: %s", e)
            self._src = None
            self._src_key = None
            return False

    def _ensure_win(self) -> None:
        if self._under is not None:
            return
        under = tk.Toplevel(self.root)
        under.overrideredirect(True)      # 縁なし・タスクバー非表示
        under.withdraw()
        # =268: =267のTk transient(owned化)は撤去。Tkのtransient管理が
        # ラッパーHWNDの再生成を誘発し、最大化/復元でEXSTYLEと保持HWNDが
        # 失われた(クリック素通し消失・追従停止の実機FB)。owned関係は
        # Win32の GWLP_HWNDPARENT で直接設定する(_apply_click_through)。
        self._set_win_alpha(under, 0.0)   # 出現時のちらつき防止(=264)
        canvas = tk.Canvas(under, bd=0, highlightthickness=0, bg="black")
        canvas.pack(fill="both", expand=True)
        self._under = under
        self._canvas = canvas

    def _setup_click_through(self, win) -> bool:
        """Windows: クリック透過+オーナー設定(オーバーレイ方式の初回判定)。

        成功=True(オーバーレイ方式が使える)。Windows以外や失敗時はFalse
        (アンダーレイ方式へフォールバック)。実体は _apply_click_through。
        """
        if sys.platform != "win32":
            return False
        return self._apply_click_through()

    def _resolve_hwnd(self, win):
        """トップレベルの実HWNDを解決する(wm_frame優先=Tkラッパー窓)。"""
        try:
            h = int(win.wm_frame(), 16)
            if h:
                return h
        except (tk.TclError, ValueError):
            pass
        try:
            import ctypes
            h = win.winfo_id()
            parent = ctypes.windll.user32.GetParent(h)
            return parent or h
        except Exception:
            return None

    def _apply_click_through(self) -> bool:
        """=268: 画像窓へ EXSTYLE(クリック透過)+Win32オーナーを(再)適用。

        - WS_EX_TRANSPARENT|LAYERED|NOACTIVATE: マウスを下のUIへ素通し。
        - GWLP_HWNDPARENT=rootのHWND: owned windowのZ帯域(rootの直上に
          保たれ、Settings等の別窓操作で下へ落ちない=旧=267のtransientの
          代替。Tkのtransient管理を使わないのでラッパー再生成を誘発しない)。
        - SWP_FRAMECHANGED: スタイル変更をシステムへ確定(=266)。
        適用先HWNDは毎回解決し直して self._hwnd へ保持する(Tkが何らかの
        理由でラッパーを作り直しても _ensure_click_through が検知して
        ここへ戻ってくる=自己修復)。
        """
        if sys.platform != "win32" or self._under is None:
            return False
        try:
            import ctypes
            GWL_EXSTYLE = -20
            GWLP_HWNDPARENT = -8
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020
            self._under.update_idletasks()
            hwnd = self._resolve_hwnd(self._under)
            if not hwnd:
                return False
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            root_hwnd = self._resolve_hwnd(self.root)
            if root_hwnd:
                set_ptr = getattr(user32, "SetWindowLongPtrW",
                                  user32.SetWindowLongW)
                set_ptr(hwnd, GWLP_HWNDPARENT, root_hwnd)
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            self._hwnd = hwnd
            return True
        except Exception:
            return False

    def _ensure_click_through(self) -> bool:
        """=268: ラッパーHWNDの再生成を検知したらスタイル等を再適用する。

        最大化/復元などでTkが画像窓の実HWNDを作り直すと、外部から書いた
        EXSTYLE・オーナー・保持HWNDが全て失われる(実機FB: 最大化中は
        クリック不能・復元後は追従停止)。同期のたびに現HWNDを確認し、
        変わっていたら適用し直す。戻り値=再適用したか。
        """
        if self.mode != "overlay" or sys.platform != "win32":
            return False
        cur = self._resolve_hwnd(self._under) if self._under else None
        if cur and cur != self._hwnd:
            self._apply_click_through()
            return True
        return False

    def _win32_move(self, x, y, w, h) -> bool:
        """=266: 画像窓の移動/リサイズをWin32 SetWindowPosで直接行う。

        EXSTYLE書き換え後の窓はTkの geometry() の**位置指定が効かなくなる**
        ことがある(実機: サイズは変わるのに座標が固定される)。overlay方式では
        こちらを正とし、失敗時のみTk geometryへフォールバックする。
        """
        if self._hwnd is None or sys.platform != "win32":
            return False
        try:
            import ctypes
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            return bool(ctypes.windll.user32.SetWindowPos(
                self._hwnd, 0, int(x), int(y), int(w), int(h),
                SWP_NOZORDER | SWP_NOACTIVATE))
        except Exception:
            return False

    def _set_win_alpha(self, win, value: float) -> None:
        try:
            win.attributes("-alpha", value)
        except tk.TclError:
            pass

    def _show(self) -> None:
        """表示する(冪等。起動直後などは=265のリトライで収束させる)。"""
        self._ensure_win()
        first = not self.shown
        self.shown = True
        try:
            root_ready = self.root.winfo_ismapped() and \
                self.root.winfo_width() >= 2
        except tk.TclError:
            root_ready = False
        if root_ready:
            self._sync(force=True)
            self._rebuild()
            try:
                self._under.deiconify()
            except tk.TclError:
                pass
            if self.mode is None:
                # 初回だけ方式を決める(HWNDが要るので表示後)
                self.mode = ("overlay"
                             if self._setup_click_through(self._under)
                             else "underlay")
            self._restack()
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass
            if self.mode == "overlay":
                # rootは触らない(タイトルバー完全不透過=265)。画像側を上に
                # 薄く重ねる(合成結果は=263/=264と同一)。
                self._fade_to(self._under, self._img_alpha())
            else:
                self._set_win_alpha(self._under, 1.0)
                self._fade_to(self.root, self._ui_opacity())
        if first or not root_ready:
            # =265: 起動直後はrootの位置がWMで未確定のことがある(画像が
            # 画面左上に張り付く実機FB)。少し遅らせて再同期・再表示する。
            for delay in self.RESYNC_DELAYS_MS:
                try:
                    self.root.after(delay, self._resync_later)
                except Exception:
                    pass

    def _resync_later(self) -> None:
        """=265: 遅延再同期。位置が確定済みなら(キャッシュ比較で)何もしない。"""
        if not self.shown:
            return
        try:
            if not self.root.winfo_ismapped():
                return
        except tk.TclError:
            return
        if self.mode is None or self._photo is None \
                or self._photo_h != self.root.winfo_height():
            self._show()                  # 初回が不発だった(未確定で戻った)
        else:
            self._sync()

    def _hide(self) -> None:
        if not self.shown:
            return
        self.shown = False
        if self.mode == "overlay":
            # rootは元から触っていない。画像側を消すだけ。
            self._fade_to(self._under, 0.0)
            return

        # アンダーレイ方式: alphaを戻し切ってから画像を消す(=264 順序)
        def after_fade():
            if self.shown:                # フェード中に再表示された
                return
            if self._under is not None:
                self._set_win_alpha(self._under, 0.0)

        self._fade_to(self.root, 1.0, done=after_fade)

    def _fade_to(self, win, target: float, done=None) -> None:
        """winのalphaを約120msかけて段階的に変える(=264 ちらつき緩和)。"""
        job = self._fade_jobs.pop(win, None)
        if job is not None:
            try:
                win.after_cancel(job)
            except Exception:
                pass
        try:
            cur = float(win.attributes("-alpha"))
        except (tk.TclError, ValueError):
            cur = 1.0
        steps = self.FADE_STEPS
        if abs(cur - target) < 0.01:
            self._set_win_alpha(win, target)
            if done is not None:
                done()
            return

        def step(i):
            self._fade_jobs.pop(win, None)
            v = cur + (target - cur) * (i / steps)
            self._set_win_alpha(win, v)
            if i >= steps:
                if done is not None:
                    done()
                return
            try:
                self._fade_jobs[win] = win.after(
                    self.FADE_INTERVAL_MS, lambda: step(i + 1))
            except Exception:
                pass

        step(1)

    # ---------------- 同期・描画 ----------------

    def _sync(self, force: bool = False) -> None:
        """画像窓をrootのクライアント領域へ重ねる(変化時のみ)。

        位置基準はクライアント領域(winfo_rootx/rooty)なので、**タイトル
        バーは画像の表示領域に含まれない**(=265)。
        """
        if not self.shown or self._under is None:
            return
        try:
            if not self.root.winfo_ismapped():
                return
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w < 2 or h < 2:
                return
            if self._ensure_click_through():
                force = True              # =268: HWNDが変わった→位置も再適用
            geo = (w, h, x, y)
            if not force and geo == self._last_geo:
                return
            self._last_geo = geo
            if not self._win32_move(x, y, w, h):
                self._under.geometry(f"{w}x{h}+{x}+{y}")
            self._restack()
        except tk.TclError:
            pass

    def _restack(self) -> None:
        """画像窓の重ね順を整える。

        overlay: rootの**直上**(rootのダイアログや編集画面はさらに上に
        来るので覆わない)。underlay: rootの**直下**。
        """
        if self._under is None or self.mode is None:
            return
        try:
            if self.mode == "overlay":
                self._under.lift(self.root)
                # =267: rootの子トップレベル(Settings・ヘルプ・編集画面等)を
                # オーバーレイの上へ再整列する。Settingsはtransient(owned)の
                # ためrootの直上に保たれ、素朴なlift(root)だとオーバーレイが
                # その上へ割り込んで薄衣がかかっていた(実機FB)。
                for w in self.root.winfo_children():
                    if w is self._under or not isinstance(w, tk.Toplevel):
                        continue
                    try:
                        if w.winfo_viewable():
                            w.lift(self._under)
                    except tk.TclError:
                        pass
            else:
                self._under.lower(self.root)
        except tk.TclError:
            pass

    def _on_root_configure(self, e=None) -> None:
        # =264: bindtagの仕様でこのハンドラは**全子孫のConfigure**でも呼ばれる。
        # root自身のイベントだけを扱う(でないとページ切替のたびに再描画が
        # 連打されて画像が点滅する=実機ちらつきの主因)。
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        if not self.shown:
            return
        self._sync()
        # =265: Configure直後はwinfo値が古いことがある→遅延再同期
        # (キャッシュ比較なので変化がなければ何もしない)
        try:
            self.root.after(80, self._resync_later)
        except Exception:
            pass
        h = self.root.winfo_height()
        if h != self._photo_h:
            self._schedule_rebuild()       # 縦が変わった=再拡縮(デバウンス)
        else:
            self._recompute_x0()
            self._redraw()

    def _on_root_map(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        if self.shown and self._under is not None:
            try:
                self._under.deiconify()
            except tk.TclError:
                pass
            if self.mode == "overlay":
                self._set_win_alpha(self._under, self._img_alpha())
            elif self.mode == "underlay":
                self._set_win_alpha(self._under, 1.0)
            self._sync(force=True)

    def _on_root_unmap(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        # 最小化に追従(shownフラグは維持=復帰で再表示)
        if self.shown and self._under is not None:
            try:
                self._under.withdraw()
            except tk.TclError:
                pass

    def _on_root_focus(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        # 他アプリの窓を挟んだ後の復帰: 重ね順だけ直す
        if self.shown:
            self._restack()

    def _on_root_focus_out(self, e=None) -> None:
        if e is not None and getattr(e, "widget", None) is not self.root:
            return
        # =267: Settings等の別窓へフォーカスが移った直後、OSのowned再整列で
        # 順序がずれることがある→少し遅らせて直す(保険)。
        if self.shown:
            try:
                self.root.after(80, self._restack)
            except Exception:
                pass

    def _schedule_rebuild(self, immediate: bool = False) -> None:
        if self._rebuild_job is not None:
            try:
                self.root.after_cancel(self._rebuild_job)
            except Exception:
                pass
            self._rebuild_job = None
        delay = 1 if immediate else self.REBUILD_DELAY_MS
        try:
            self._rebuild_job = self.root.after(delay, self._rebuild)
        except Exception:
            pass

    def _rebuild(self) -> None:
        self._rebuild_job = None
        if not self.shown or self._src is None:
            return
        h = self.root.winfo_height()
        w = self.root.winfo_width()
        if h < 2 or w < 2:
            return
        _x0, sw, sh = bg_fit_geometry(w, h, self._src.width, self._src.height)
        if sh != self._photo_h or self._photo is None:
            try:
                scaled = self._src.resize((sw, sh),
                                          PILImage.Resampling.LANCZOS)
                self._photo = PILImageTk.PhotoImage(scaled)
                self._photo_h = sh
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "background resize failed: %s", e)
                return
        self._recompute_x0()
        self._redraw()

    def _recompute_x0(self) -> None:
        if self._photo is None:
            return
        w = self.root.winfo_width()
        self._x0 = (max(1, w) - self._photo.width()) // 2

    def _redraw(self) -> None:
        """描画は(photo, x0)が変わった時だけ(=264)。移動はcoordsで済ます。"""
        if self._canvas is None or self._photo is None:
            return
        key = (id(self._photo), self._x0)
        if key == self._drawn:
            return
        try:
            if (self._img_item is None or self._drawn is None
                    or self._drawn[0] != id(self._photo)):
                self._canvas.delete("all")
                self._img_item = self._canvas.create_image(
                    self._x0, 0, anchor="nw", image=self._photo)
            else:
                self._canvas.coords(self._img_item, self._x0, 0)
            self._drawn = key
        except tk.TclError:
            pass
