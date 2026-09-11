"""起動不能対策(=83 CTk 再入ガード)と例外ログ/クラッシュダンプ(=155)。"""
from __future__ import annotations

import customtkinter as ctk
import sys
from .. import __version__



def _install_ctk_reentrancy_guard():
    """=83/=84: customtkinter 6.0.0 の同期再帰バグを遮断する(main()起動時のみ)。

    =84(真因・実機クラッシュダンプで確定): **CTkScrollbar._draw が
    update_idletasks() を呼ぶ**(ctk_scrollbar.py:168)ため、
    canvasのスクロール連携 → set → _draw → update_idletasks →
    (Tclが保留中のスクロール更新を同期処理) → また set → … の
    **真の無限再帰**になる。履歴リスト(CTkScrollableFrame)の行数が
    一定以上でスクロール領域の再計算が連鎖する条件を踏むと発症し、
    起動不能に至っていた(6件○/7件✕の境界はこの発症条件)。
    再帰は必ず set を通るので、set の再入を1段で打ち切れば完全に切れる。
    再入時は値の更新だけ行い描画しない(1フレームの表示遅れのみ・
    次のスクロールイベントで追いつく)。

    =83(保険として維持): _update_dimensions_event の同一ウィジェット
    再入ガード。

    =95(起動14秒問題の真因対応): =84のsetガードは**クラッシュ(無限再帰)**
    は止めたが、_draw が呼ぶ update_idletasks() 自体は残っていた。この呼び出しは
    「スクロールバーを1回描くたびに、溜まっている**全ウィジェットの保留描画を
    同期処理する**」ため、起動直後やシナリオ読込直後(保留描画が数百件)には
    別インスタンスのスクロールバー同士で入れ子になり、**メインスレッドが
    7〜8秒完全停止**していた(=94の実機スタックサンプルで確定。
    mainloop→set→_draw→update_idletasks→set→_draw→update_idletasks→…が
    そのまま写っている)。対策=**_draw の実行中だけ canvas.update_idletasks を
    無効化**する。効果は「スクロールバーの描画が次の自然なidle処理まで
    最大1フレーム遅れる」だけで、上流バグの副作用(全保留描画の強制処理)を
    根本から断つ。
    """
    try:
        from customtkinter.windows.widgets.ctk_scrollbar import CTkScrollbar
        orig_set = CTkScrollbar.set

        def guarded_set(self, start_value, end_value):
            if getattr(self, "_rvp_in_set", False):
                # 再入(=_draw内のupdate_idletasksから同期的に呼び戻された):
                # 値だけ最新化して描画は外側に任せ、再帰を断つ
                self._start_value = float(start_value)
                self._end_value = float(end_value)
                return
            self._rvp_in_set = True
            try:
                return orig_set(self, start_value, end_value)
            finally:
                self._rvp_in_set = False

        CTkScrollbar.set = guarded_set

        # =95: _draw 実行中は canvas.update_idletasks を無効化する
        orig_draw = CTkScrollbar._draw

        def _noop_update_idletasks():
            pass

        def draw_without_idletasks(self, *args, **kwargs):
            canvas = getattr(self, "_canvas", None)
            if canvas is None:
                return orig_draw(self, *args, **kwargs)
            # インスタンス属性でクラスメソッドを一時的に遮蔽する
            canvas.update_idletasks = _noop_update_idletasks
            try:
                return orig_draw(self, *args, **kwargs)
            finally:
                try:
                    del canvas.update_idletasks   # クラスの実装へ戻す
                except AttributeError:
                    pass

        CTkScrollbar._draw = draw_without_idletasks
    except Exception:
        pass
    try:
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class \
            import CTkBaseClass
        orig = CTkBaseClass._update_dimensions_event

        class _Dims:
            """保留分の流し直し用。実サイズ(winfo)をそのまま渡す。"""

            __slots__ = ("width", "height")

            def __init__(self, w, h):
                self.width, self.height = w, h

        def guarded(self, event):
            if getattr(self, "_rvp_in_dims", False):
                # =108: 再入を**捨てる**と、そのウィジェットは古いサイズを
                # 覚えたまま(_current_width/_current_height)になり、canvas は
                # 旧サイズで描かれ続ける=コンボの角丸や▼が途中で切れた
                # ような崩れになる(Windows実機で報告)。あとで流し直す。
                self._rvp_dims_pending = True
                return
            self._rvp_in_dims = True
            try:
                r = orig(self, event)
                # 保留があれば「そのときの実サイズ」で1回流し直す。古い
                # イベントを再生すると順序次第で逆戻りするので、必ず winfo
                # の現物を使う(連鎖しても3回で打ち切り=無限再帰の遮断と
                # いう本来の目的は維持する)。
                for _ in range(3):
                    if not getattr(self, "_rvp_dims_pending", False):
                        break
                    self._rvp_dims_pending = False
                    try:
                        dims = _Dims(self.winfo_width(), self.winfo_height())
                    except Exception:
                        break
                    r = orig(self, dims)
                return r
            finally:
                self._rvp_in_dims = False
                self._rvp_dims_pending = False

        CTkBaseClass._update_dimensions_event = guarded
    except Exception:
        pass


def _install_crash_diagnostics(root):
    """=83: Tkコールバック例外の初回に全スタックを rvp_crashdump.txt へ記録。

    RecursionError の嵐では標準の例外表示が浅いトレースしか出せない
    (深い部分は**呼び出し側**のスタックにあり、表示自体も失敗する)。
    report_callback_exception は最深部で呼ばれるので、その時点の
    スレッド全体のスタックを faulthandler(C実装=再帰制限の影響を
    受けない)でダンプすれば、繰り返しているフレーム=真犯人が分かる。
    2回目以降の例外表示は5回で打ち切り、コンソールの嵐も止める。
    """
    import faulthandler
    state = {"n": 0}

    def report(exc, val, tb):
        state["n"] += 1
        if state["n"] == 1:
            try:
                with open("rvp_crashdump.txt", "w", encoding="utf-8") as fp:
                    fp.write("RVP crash diagnostics (=83)\n")
                    fp.write(f"rvp_version={__version__}\n")   # =292
                    fp.write(f"python={sys.version}\n")
                    fp.write(f"frozen={bool(getattr(sys, 'frozen', False))}\n")
                    try:
                        fp.write(f"customtkinter={ctk.__version__}\n")
                        from customtkinter import ScalingTracker
                        fp.write("widget_scaling="
                                 f"{ScalingTracker.get_widget_scaling(root)}\n")
                        fp.write("window_scaling="
                                 f"{ScalingTracker.get_window_scaling(root)}\n")
                    except Exception as e:
                        fp.write(f"ctk info error: {e}\n")
                    try:
                        fp.write(f"tk scaling={root.tk.call('tk', 'scaling')}\n")
                    except Exception:
                        pass
                    fp.write(f"exception={getattr(exc, '__name__', exc)}: "
                             f"{val}\n")
                    fp.write("\n== この瞬間のスレッド全スタック"
                             "(faulthandler・最深部から) ==\n")
                    fp.flush()
                    faulthandler.dump_traceback(file=fp, all_threads=False)
                    fp.write("\n== 例外のトレースバック(浅い側) ==\n")
                    t = tb
                    while t is not None:
                        f = t.tb_frame
                        fp.write(f"{f.f_code.co_filename}:{t.tb_lineno}:"
                                 f"{f.f_code.co_name}\n")
                        t = t.tb_next
                print("rvp_crashdump.txt へ診断情報を記録しました")
            except Exception:
                pass
        if state["n"] <= 5:
            try:
                import traceback
                traceback.print_exception(exc, val, tb)
            except Exception:
                print("Exception in Tkinter callback (表示失敗)")
        elif state["n"] == 6:
            print("(以降のコールバック例外の表示は省略します。"
                  "rvp_crashdump.txt を参照)")

    root.report_callback_exception = report
