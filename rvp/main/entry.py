"""エントリポイント main()。"""
from __future__ import annotations

import customtkinter as ctk
import time
from .. import appfont, apptheme, winstate
from ..i18n import load_config

from .guards import _install_crash_diagnostics, _install_ctk_reentrancy_guard
from .rvpapp import RVPApp
from .startup import (_STARTUP_T0, _start_stack_sampler, _startup_log_wanted,
    _startup_mark, _write_startup_log)
from .winicon import _set_app_icon, _set_win_app_id, _set_win_taskbar_icons


def main():
    _startup_mark("main() 開始")
    _install_ctk_reentrancy_guard()       # =83 起動不能対策(CTk同期入れ子の遮断)
    _set_win_app_id()                     # =144 タスクバーアイコン(窓生成より前)
    winstate.enable()                     # =115 ウィンドウ位置の記憶(実行時のみ)
    ctk.set_appearance_mode("dark")       # "dark" / "light" / "system"
    ctk.set_default_color_theme("blue")
    # =134: パステルカラーテーマ。CTkの色はウィジェット生成時に確定する
    # ので、rootを含む全ウィジェットより前に1回だけ適用する(ライト基調)。
    # テストは main() を通らないので汚染しない(appfont/winstateと同じ流儀)。
    _theme = load_config().get("appearance")
    if isinstance(_theme, str) and _theme in apptheme.THEMES:
        ctk.set_appearance_mode("light")
        apptheme.set_active(_theme)
    _startup_mark("カラーテーマ適用")

    root = ctk.CTk()
    _startup_mark("Tkルートウィンドウ生成")
    # =119: UIフォントの適用。CTkFontはウィジェット生成時にfamilyを確定する
    # ので、RVPApp(=全ウィジェット)より前に1回だけ行う。端末に無ければ
    # 何もしない=システム標準のまま。テストはmain()を通らないので汚染しない
    # (winstate / CTk再入ガードと同じ流儀)。
    _fam = appfont.configured_family(load_config())   # =293 初期値 BIZ UDPGothic
    if _fam:
        appfont.apply(root, _fam)
    _startup_mark("フォント適用")
    _set_app_icon(root)                   # =143 アプリアイコン(main()時のみ)
    _set_win_taskbar_icons(root)          # =147 タスクバー用の高精細アイコン
    _startup_mark("アイコン適用")
    _install_crash_diagnostics(root)      # =83 万一の際は rvp_crashdump.txt へ記録
    app = RVPApp(root)

    # =92: ウィンドウが実際に画面へ出た瞬間を記録(<Map>は最初の表示で発火)
    def _on_first_map(_e=None):
        root.unbind("<Map>", map_bind)
        _startup_mark("★ウィンドウ表示(<Map>)")
    map_bind = root.bind("<Map>", _on_first_map, add="+")
    if _startup_log_wanted():
        # =93: ハートビート。250ms毎のタイマーが「いつ実際に発火したか」を
        # 記録する。イベントループ(メインスレッド)がブロックされている間は
        # タイマーが発火できないので、**ログ上のハートビートの間隔が開いて
        # いる場所=ループが止まっていた区間**として犯人の時間帯を特定できる
        # (v92のログで<Map>→自動オープンの間に約7.7秒の空白があり、その
        # 内側を見るための増設)。
        def _heartbeat():
            _startup_mark("♥heartbeat")
            if time.perf_counter() - _STARTUP_T0 < 12.0:
                root.after(250, _heartbeat)
        root.after(250, _heartbeat)
        _start_stack_sampler()   # =94: ブロック中のスタックを2.5秒毎に採取
        # 自動オープンや履歴の遅延構築、ハートビート全部を含めてから書き出す
        # (ブロックで遅れて発火してもよい=遅れた分のスタックも採れる)
        root.after(13000, _write_startup_log)

    def on_close():
        try:
            app.save_app_config()
        except Exception:
            pass
        try:
            app.winmem.save_now()      # =115: 終了時のサイズ・位置を記憶
        except Exception:
            pass
        try:
            app.runner.submit(app.player.stop())
            # mpvはRVP終了時のみ閉じる(合意事項)
            app.runner.submit(app.player.shutdown_mpv())
            app.runner.submit(app.intiface.disconnect())
            app.tcode.disconnect()   # =80 シリアルポートを閉じる
        except Exception:
            pass
        root.after(300, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
