"""起動時間の計測(=92)。マーク収集と rvp_startup_log.txt の書き出し。"""
from __future__ import annotations

import os
import sys
import time



# ---- 起動時間の計測(=92) ----
# 「python -m rvp.main」から画面表示まで実機で14秒かかるという報告の調査用。
# マークの収集は常時行う(perf_counter1回=実質ゼロコスト)。ログファイル
# (カレントディレクトリの rvp_startup_log.txt)への書き出しは
# 「--startup-log」引数か環境変数 RVP_STARTUP_LOG があるときだけ。
# 注意: python.exe 自体の起動(インタプリタ初期化)はこの計測の外
# (main.py の実行開始が起点)。ログは開発者向けなので i18n 対象外。
_STARTUP_T0 = time.perf_counter()


_STARTUP_MARKS: list = []


def _startup_mark(label: str):
    # =93: 繰り返し呼ばれる場所(履歴チャンク等)にもマークを置いたので、
    # 長時間セッションでリストが育たないよう上限を設ける(起動調査には十分)
    if len(_STARTUP_MARKS) < 400:
        _STARTUP_MARKS.append((label, time.perf_counter() - _STARTUP_T0))


def _startup_log_wanted() -> bool:
    return ("--startup-log" in sys.argv
            or bool(os.environ.get("RVP_STARTUP_LOG")))


# =94: スタックサンプラ用のファイルハンドル(--startup-log 時のみ開く)
_STACKS_FILE = None


def _start_stack_sampler():
    """=94: 2.5秒毎に全スレッドのスタックを rvp_startup_stacks.txt へ書く。

    =93のログで「メインスレッドが約8秒×2回、イベントループごと完全に固まる」
    ことが確定した(250msのハートビートすら発火しない)。固まっている最中に
    メインスレッドが何を実行しているかを、faulthandler の watchdog スレッド
    (C実装=メインスレッドがブロック中でも動く)で直接採取する。=83の
    クラッシュダンプで真因特定に成功したのと同じ手法。
    """
    global _STACKS_FILE
    try:
        import faulthandler
        _STACKS_FILE = open("rvp_startup_stacks.txt", "w", encoding="utf-8")
        _STACKS_FILE.write(
            "RVP 起動スタックサンプル (=96)\n"
            "2.5秒毎の全スレッドスタック。『Thread 0x...(most recent call "
            "first)』のうちMainThreadの最上段が、その瞬間に実行していた場所。\n\n")
        _STACKS_FILE.flush()
        faulthandler.dump_traceback_later(2.5, repeat=True, file=_STACKS_FILE)
    except Exception as e:
        print("stack sampler failed:", e)


def _stop_stack_sampler():
    global _STACKS_FILE
    try:
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        if _STACKS_FILE is not None:
            _STACKS_FILE.close()
            print("stack samples -> rvp_startup_stacks.txt")
    except Exception:
        pass
    _STACKS_FILE = None


def _write_startup_log():
    """収集済みマークを rvp_startup_log.txt へ書き出す(累積秒と区間秒)。

    =93: 250ms毎のハートビートは、**予定どおり発火したもの(区間0.35秒未満)
    は「♥×N回 正常」へ圧縮**し、遅れて発火したもの(=その間イベントループが
    ブロックされていた)だけを1行で残す。
    """
    try:
        lines = ["RVP 起動時間ログ (=96)",
                 f"python {sys.version.split()[0]} / {sys.platform}",
                 "累積秒  (+区間秒)  区間"]
        prev = 0.0
        ok_beats = 0

        def flush_beats(upto):
            nonlocal ok_beats
            if ok_beats:
                lines.append(f"{upto:8.3f}  (+     )  ♥×{ok_beats}回 "
                             "(250ms間隔で正常に発火=ループは動いていた)")
                ok_beats = 0

        for label, t in _STARTUP_MARKS:
            delta = t - prev
            if label == "♥heartbeat" and delta < 0.35:
                ok_beats += 1
                prev = t
                continue
            flush_beats(prev)
            if label == "♥heartbeat":
                lines.append(f"{t:8.3f}  (+{delta:6.3f})  ♥heartbeat遅延 "
                             "(この区間イベントループがブロックされていた)")
            else:
                lines.append(f"{t:8.3f}  (+{delta:6.3f})  {label}")
            prev = t
        flush_beats(prev)
        with open("rvp_startup_log.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("startup log -> rvp_startup_log.txt")
    except Exception as e:      # ログ機構自体で起動を壊さない
        print("startup log write failed:", e)
    _stop_stack_sampler()       # =94: サンプラもここで停止・ファイルを閉じる
