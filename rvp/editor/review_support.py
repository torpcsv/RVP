"""アイテムレビュー画面の補助(定数・波形エンベロープ・編集用 mpv・区間解決)。"""
from __future__ import annotations

import asyncio
import threading
from ..i18n import load_config

from ._hooks import _pkg


REVIEW_SLOT = 7          # レビュー再生に使う pygame.mixer のチャンネル番号


# =214: 再生速度の選択肢(速い→遅い。既定 1.0)
SPEED_CHOICES = tuple(round(2.0 - 0.1 * i, 1) for i in range(19))


# =243: 音声波形の表示モード(configキー audio_wave_mode。参照・編集共通)
WAVE_BUCKET_MS = 10


WAVE_MODE_KEYS = ("off", "mono", "stereo", "stereo_rev")


def compute_wave_env(raw, freq: int, fmt: int, channels: int,
                     bucket_ms: int = WAVE_BUCKET_MS):
    """PCMデータから音声波形のエンベロープを作る(=243。numpy 不使用)。

    10ms(bucket_ms)ごとのバケットへ区切り、各バケットの**振幅のピーク**
    (max(max(seg), -min(seg)))を ch ごとに並べる。表示は中央線に対する
    上下対称の帯なので min/max を別々に持つ必要はない。
    デインタリーブは array のスライス(arr[c::channels])で C 速度。
    raw は bytes のほか memoryview も可(=249。コピーレスで渡せる)。
    戻り値 {"bucket_ms","chans"(L,R),"mono"(バケットごと max 合成),
    "peak"(L/R共通の最大値=正規化の分母)} / 対応外フォーマット・無音は None。
    """
    from array import array
    channels = max(1, int(channels))
    absfmt = abs(int(fmt))
    if absfmt == 16 and fmt < 0:
        unit, code, base = 2, "h", 0
    elif absfmt == 8:
        unit, code, base = 1, ("b" if fmt < 0 else "B"), (0 if fmt < 0
                                                          else 128)
    else:
        return None                     # 想定外のフォーマット
    arr = array(code)
    n = len(raw) - (len(raw) % (unit * channels))
    if n <= 0:
        return None
    arr.frombytes(raw[:n])
    spb = max(1, int(freq) * bucket_ms // 1000)
    chans = []
    for c in range(min(2, channels)):
        ch = arr[c::channels]
        out = array("i")
        for i in range(0, len(ch), spb):
            seg = ch[i:i + spb]
            if not seg:
                break
            out.append(max(max(seg) - base, base - min(seg), 0))
        chans.append(out)
    if not chans or not len(chans[0]):
        return None
    if len(chans) == 1:
        chans.append(chans[0])
    m = min(len(chans[0]), len(chans[1]))
    mono = array("i", (max(chans[0][i], chans[1][i]) for i in range(m)))
    peak = max(max(chans[0]), max(chans[1]))
    if peak <= 0:
        return None                     # 無音
    return {"bucket_ms": int(bucket_ms), "chans": (chans[0], chans[1]),
            "mono": mono, "peak": int(peak)}


def _mpv_path_setting() -> str | None:
    """mpv のパス(=203)。再生タブと同じ設定(config "mpv_path")を共用し、
    未設定なら find_mpv() の自動探索。見つからなければ None。"""
    try:
        cfg = load_config()
        mp = cfg.get("mpv_path")
        if isinstance(mp, str) and mp:
            return mp
    except Exception:
        pass
    try:
        from .. import mpv_client
        return mpv_client.find_mpv()
    except Exception:
        return None


class _EditorMpv:
    """レビュー画面用の mpv(=203)。専用スレッドの asyncio ループで
    MpvClient を動かし、tk 側からはスレッドセーフに操作・状態参照する。

    再生タブの mpv(player 側)とは**プロセスも IPC パイプも別**
    (default_ipc_path はプロセスID固有なので "-edit" を足して衝突を防ぐ)。
    エディタ(ScenarioEditor)が生きている間はプロセスを維持して
    loadfile で差し替え、エディタを閉じたら quit する。
    """

    def __init__(self, mpv_path: str | None = None):
        from .. import mpv_client
        self.client = mpv_client.MpvClient(
            mpv_path=mpv_path,
            ipc_path=mpv_client.default_ipc_path() + "-edit")
        self.loop = asyncio.new_event_loop()
        self._th = threading.Thread(target=self._run, daemon=True,
                                    name="rvp-editor-mpv")
        self._th.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _call(self, coro):
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        except Exception:
            return None

    @property
    def state(self) -> dict:
        return self.client.state

    @property
    def alive(self) -> bool:
        try:
            return bool(self.client.connected and self.client.alive)
        except Exception:
            return False

    def open(self, path: str, seek_s: float = 0.0):
        """起動(必要なら)→ loadfile → 一時停止 → 区間頭へシーク。"""
        client = self.client

        async def _o():
            if not client.connected:
                await client.start(extra_args=_pkg().REVIEW_MPV_EXTRA_ARGS)
            await client.loadfile(path)
            await client.set_pause(True)
            # ロード完了(duration 到着)を待ってからシークする
            for _ in range(100):
                if client.state.get("duration"):
                    break
                await asyncio.sleep(0.05)
            if seek_s > 0:
                try:
                    await client.seek(seek_s)
                except Exception:
                    pass
        return self._call(_o())

    def set_pause(self, flag: bool):
        return self._call(self.client.set_pause(bool(flag)))

    def seek(self, seconds: float):
        return self._call(self.client.seek(float(seconds)))

    def set_speed(self, rate: float):
        """=214: 再生速度(mpv の speed プロパティ。ピッチ補正は mpv 既定=
        audio-pitch-correction=yes のまま)。"""
        client = self.client

        async def _s():
            # open() 直後は起動待ちのことがあるので、接続まで少し待つ
            for _ in range(100):
                if client.connected:
                    break
                await asyncio.sleep(0.05)
            try:
                await client.command("set_property", "speed", float(rate))
                client.state["speed"] = float(rate)
            except Exception:
                pass
        return self._call(_s())

    def quit(self):
        client, loop = self.client, self.loop

        async def _q():
            try:
                await client.quit()
            except Exception:
                pass
            finally:
                loop.stop()
        self._call(_q())


def _hold_editor_mpv(holder, mpv_path: str | None) -> "_EditorMpv":
    """holder(ScenarioEditor など)に紐づく _EditorMpv を用意する(=203)。

    既存インスタンスが死んでいれば作り直す。holder の破棄で quit する
    (アプリ終了時も破棄の連鎖で閉じる)。
    """
    m = getattr(holder, "_edit_mpv", None)
    if m is not None:
        try:
            if m.client.alive or not m.client.state.get("path"):
                return m
        except Exception:
            pass
    m = _EditorMpv(mpv_path)
    holder._edit_mpv = m
    if hasattr(holder, "bind") and             not getattr(holder, "_edit_mpv_hooked", False):
        holder._edit_mpv_hooked = True

        def _on_holder_destroy(event, h=holder):
            if event.widget is h and getattr(h, "_edit_mpv", None):
                h._edit_mpv.quit()
                h._edit_mpv = None
        try:
            holder.bind("<Destroy>", _on_holder_destroy, add="+")
        except Exception:
            pass
    return m


REVIEW_ROTATE_TYPES = ("rotate_ufo", "rotate_a10cyclonesa")


# =170: スクリプト編集モードで対象にできるトラック種別(P1)。
# =224: csv(ROTATE 3列/5列)を追加。
# =225: **rotate系・vibration の funscript も追加**(未対応領域を解消)。
SCRIPT_EDIT_TYPES = ("linear", "twist", "vibration",
                     "rotate_ufo", "rotate_a10cyclonesa")


# =224: csv で編集できる種別(ROTATE 系)。
SCRIPT_EDIT_CSV_TYPES = REVIEW_ROTATE_TYPES


# =225: **階段**(次の指示まで値を保つ)で描く種別。csv も常に階段。
#   ROTATE  : pos 50=停止 / 100=正回転最大 / 0=逆回転最大
#   VIBRATION: pos 0=停止 〜 100=最大
# どちらも funscript の pos がそのまま値なので、変換は要らない
# (player._graph_points_funscript + kind="step" と同じ扱い)。
SCRIPT_EDIT_STEP_TYPES = ("vibration", "rotate_ufo", "rotate_a10cyclonesa")


# 自動紐づけのタグ(=新規保存の既定ファイル名に添える。ヘルプ 1-2 の規則)。
# **linear/twist は従来どおり接尾辞なし**(仕様 12・既存の挙動を変えない)。
# =225 で足した種別は、タグが無いと自動紐づけで LINEAR 扱いになってしまう
# ため必ず添える。
SCRIPT_EDIT_TAGS = {"vibration": "_vib",
                    "rotate_ufo": "_ufo", "rotate_a10cyclonesa": "_a10"}


# 新規作成コンボの値 → (種別, kind, 列数)。kind="funscript" / "csv"
SCRIPT_EDIT_NEW_KINDS = (
    ("linear", ("linear", "funscript", 0)),
    ("twist", ("twist", "funscript", 0)),
    ("vibration", ("vibration", "funscript", 0)),
    ("rotate_ufo (funscript)", ("rotate_ufo", "funscript", 0)),
    ("rotate_a10cyclonesa (funscript)",
     ("rotate_a10cyclonesa", "funscript", 0)),
    ("rotate_ufo (csv)", ("rotate_ufo", "csv", 3)),
    ("rotate_a10cyclonesa (csv)", ("rotate_a10cyclonesa", "csv", 3)),
    # =229: 5列(左右独立)は **UFO TW だけ**。A10サイクロンSA にはロータが
    # 1つしか無く5列にする意味が無いため、新規作成の候補から外した
    # (既存の5列 csv を「(左)」「(右)」で編集する側は従来どおり)。
    # =233: 5列は UFO TW の左右独立ロータ専用なので、表示をそう書く
    ("rotate_ufo (UFOTW用csv)", ("rotate_ufo", "csv", 5)),
)


def _is_csv(path: str) -> bool:
    return str(path).casefold().endswith(".csv")


def _review_range_ms(start_var, end_var) -> tuple:
    """区間欄の値を (開始ms, 終了ms|None, 指定あり, 妥当) にする(=164)。

    保存前の入力途中の値を読むので、不正な値は**エラーにせず「指定なし」**
    として扱い、妥当=False を返す(呼び出し側が画面に注意書きを出す)。
    """
    vals = []
    ok = True
    for var in (start_var, end_var):
        txt = (var.get() or "").strip()
        if not txt:
            vals.append(None)
            continue
        try:
            vals.append(float(txt))
        except ValueError:
            vals.append(None)
            ok = False
    lo, hi = vals
    if lo is not None and lo < 0:
        lo, ok = None, False
    if hi is not None and hi <= (lo or 0.0):
        hi, ok = None, False
    given = bool(lo) or hi is not None
    return (lo or 0.0) * 1000.0, (None if hi is None else hi * 1000.0), given, ok


def _review_segments(ttype: str, src) -> list:
    """スクリプト1本を DeviceGraph のスナップショット断片へ変換する(=164)。

    種別→行(key)と線の描き方(kind)の対応は player._graph_add_track と同じ。
    2ch(5列csv)の rotate は左右2行に分かれる。レビューは1本のスクリプトを
    先頭から通して見るだけなので、断片の位置は t0=0 / x0=0 で固定する。
    """
    from ..player import ScenarioPlayer as _SP

    def seg(key, kind, points):
        return {"key": key, "kind": kind, "points": points,
                "times": [p[0] for p in points],
                "x0": 0.0, "x1": None, "t0": 0.0, "live": False}

    if ttype == "linear":
        return [seg("linear", "linear", _SP._graph_points_funscript(src))]
    if ttype == "twist":
        return [seg("twist", "linear", _SP._graph_points_funscript(src))]
    if ttype == "vibration":
        return [seg("vibration", "step", _SP._graph_points_funscript(src))]
    if ttype in REVIEW_ROTATE_TYPES:
        base = "rotate_ufo" if ttype == "rotate_ufo" else "rotate_a10"
        if getattr(src, "channels", 1) >= 2:
            return [seg(base + ("_r" if r else ""), "step",
                        _SP._graph_points_rotate(src, r)) for r in (0, 1)]
        return [seg(base, "step", _SP._graph_points_rotate(src, 0))]
    return []
