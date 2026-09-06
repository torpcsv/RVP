"""外部 MPV プレーヤーとの通信クライアント(JSON IPC)。

動画対応(PLAN_VIDEO.md)のフェーズ0(PoC)成果物。mpv を
`--input-ipc-server` 付きで起動し、Windows=名前付きパイプ /
Linux=unixソケット経由の JSON IPC でロード・再生位置・一時停止・
シーク・終了を制御/監視する。

設計方針(ユーザー合意済み・PLAN_VIDEO.md):
- mpv は**オプショナル**。動画を使わないシナリオでは本モジュールは使われない。
- mpv の音声設定には一切触れない(--no-audio しない。音量管理はユーザー)。
- mpv を閉じるのは RVP 終了時のみ(--keep-open=yes --idle=yes で
  動画終了・再生停止でもウィンドウを残す)。
- 起動は遅延(動画イベントに入った時)。初回起動後はプロセスを維持して
  loadfile で差し替える。

同期の要: `state["time_pos"]` の更新(mpvからのproperty-changeイベント、
おおむねフレーム間隔)を `on_time_pos` コールバックで受け、再生側が
PlaybackClock を従属させる(閾値超過時のみ set_ms=ドリフト補正)。
funscript実行はクロックからのbisect逆算方式のため、これだけで
一時停止・シークに自動再同期する。

注意(Windows): 名前付きパイプへの接続は Proactor イベントループの
`create_pipe_connection` を使う。Linux コンテナでは検証不可のため、
**Windows実機での動作確認が必要**(フェーズ1着手時の確認ポイント)。
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time

from .i18n import tr

logger = logging.getLogger("rvp.mpv")

# Windows の代表的なインストール先(find_mpv の探索順)。PATH が最優先。
_WIN_CANDIDATES = (
    r"C:\Program Files\mpv\mpv.exe",
    r"C:\Program Files (x86)\mpv\mpv.exe",
    r"C:\mpv\mpv.exe",
)


def find_mpv() -> str | None:
    """mpv 実行ファイルを探す(PATH → 代表的なインストール先)。

    見つからなければ None(=設定UIでユーザーにパスを入力してもらう)。
    """
    path = shutil.which("mpv")
    if path:
        return path
    if sys.platform == "win32":
        for cand in _WIN_CANDIDATES:
            if os.path.isfile(cand):
                return cand
        # スクープ/ユーザーフォルダ配置などの軽い探索
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            cand = os.path.join(local, "Programs", "mpv", "mpv.exe")
            if os.path.isfile(cand):
                return cand
    return None


def default_ipc_path() -> str:
    """IPCエンドポイントの既定パス(プロセス固有=多重起動で衝突しない)。"""
    if sys.platform == "win32":
        return r"\\.\pipe\rvp-mpv-" + str(os.getpid())
    return os.path.join("/tmp", f"rvp-mpv-{os.getpid()}.sock")


class MpvError(Exception):
    """mpv コマンドがエラーを返した/通信できない。"""


class MpvClient:
    """mpv プロセスの起動と JSON IPC 通信。

    使い方(フェーズ1で player 側から呼ぶ想定):
        client = MpvClient(mpv_path)
        await client.start()          # 起動+接続(初回の動画イベントで)
        await client.loadfile(path)   # イベントごとに差し替え
        ...
        await client.quit()           # RVP終了時のみ
    """

    # 観測するプロパティ(observe_property の id と名前)
    _OBSERVED = {
        1: "time-pos",
        2: "pause",
        3: "duration",
        4: "eof-reached",
        5: "path",
    }

    def __init__(self, mpv_path: str | None = None,
                 ipc_path: str | None = None):
        self.mpv_path = mpv_path or find_mpv()
        self.ipc_path = ipc_path or default_ipc_path()
        self._proc: subprocess.Popen | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        # 最新の観測値。UI/再生側はこの辞書をポーリングするか、
        # コールバック(on_time_pos / on_pause / on_eof)で受ける。
        self.state = {
            "connected": False,
            "time_pos": None,     # 秒(float)。未再生は None
            "pause": False,
            "duration": None,     # 秒(float)。ロード完了で入る
            "eof": False,         # keep-open で末尾到達すると True
            "path": "",           # mpv が現在開いているファイル
        }
        # 同期用フック(すべて任意。asyncioループ内で呼ばれる)
        self.on_time_pos = None   # callable(float 秒)
        self.on_pause = None      # callable(bool)
        self.on_eof = None        # callable()
        self.on_disconnect = None  # callable() 切断/プロセス終了時

    # ================= 起動・接続 =================

    @property
    def alive(self) -> bool:
        """mpv プロセスが生きているか。"""
        return self._proc is not None and self._proc.poll() is None

    @property
    def connected(self) -> bool:
        return bool(self.state["connected"])

    def build_args(self, extra_args=()) -> list[str]:
        """mpv の起動引数を組み立てる。

        --keep-open=yes … 動画終了でウィンドウを閉じない(合意事項)
        --idle=yes      … ファイル無しでも常駐(loadfile差し替え運用)
        --force-window=yes … 起動直後からウィンドウを出す
        音声関連のオプションは付けない(合意事項: mpvの音はユーザー管理)。
        extra_args は既定より後ろに付くので上書きできる(テストでは
        --vo=null --force-window=no 等を渡す)。
        """
        return [
            self.mpv_path,
            "--input-ipc-server=" + self.ipc_path,
            "--keep-open=yes",
            "--idle=yes",
            "--force-window=yes",
            *extra_args,
        ]

    async def start(self, extra_args=(), connect_timeout: float = 10.0):
        """mpv を起動して IPC 接続する。既に接続済みなら何もしない。"""
        if self.connected and self.alive:
            return
        if not self.mpv_path or not os.path.isfile(self.mpv_path) \
                and shutil.which(self.mpv_path or "") is None:
            raise MpvError(
                tr("mpv が見つかりません。設定で mpv のパスを指定してください"))
        if not self.alive:
            # 前回のソケットファイルが残っていたら消す(Linuxのみ)
            if sys.platform != "win32":
                try:
                    os.unlink(self.ipc_path)
                except OSError:
                    pass
            self._proc = subprocess.Popen(
                self.build_args(extra_args),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info(tr("mpv を起動しました: %s"), self.mpv_path)
        await self._connect(connect_timeout)

    async def _connect(self, timeout: float):
        """IPCエンドポイントが現れるまでリトライしながら接続する。"""
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if not self.alive:
                raise MpvError(tr("mpv が起動直後に終了しました"))
            try:
                if sys.platform == "win32":
                    reader, writer = await self._open_windows_pipe()
                else:
                    reader, writer = await asyncio.open_unix_connection(
                        self.ipc_path)
                break
            except (OSError, FileNotFoundError) as e:
                last_err = e
                await asyncio.sleep(0.1)
        else:
            raise MpvError(
                tr("mpv のIPC接続がタイムアウトしました: {0}").format(last_err))
        self._reader, self._writer = reader, writer
        self.state["connected"] = True
        self._reader_task = asyncio.ensure_future(self._read_loop())
        # プロパティ監視を張る(以後 property-change イベントが届く)
        for obs_id, name in self._OBSERVED.items():
            await self.command("observe_property", obs_id, name)

    async def _open_windows_pipe(self):
        """Windowsの名前付きパイプへ asyncio で接続する(Proactor前提)。

        ※Linuxでは検証できないため Windows 実機確認が必要(PLAN_VIDEO.md)。
        """
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        transport, _ = await loop.create_pipe_connection(
            lambda: protocol, self.ipc_path)
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        return reader, writer

    # ================= 受信ループ =================

    async def _read_loop(self):
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break   # mpv 側が閉じた
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                self._dispatch(msg)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            logger.warning(tr("mpv IPC受信エラー: %s"), e)
        finally:
            self.state["connected"] = False
            # 未応答のコマンドを全部エラーで解放する
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MpvError(tr("mpv との接続が切れました")))
            self._pending.clear()
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    pass

    def _dispatch(self, msg: dict):
        # コマンド応答
        if "request_id" in msg:
            fut = self._pending.pop(msg["request_id"], None)
            if fut is not None and not fut.done():
                if msg.get("error") == "success":
                    fut.set_result(msg.get("data"))
                else:
                    fut.set_exception(MpvError(str(msg.get("error"))))
            return
        # プロパティ変更イベント
        if msg.get("event") == "property-change":
            name = msg.get("name")
            data = msg.get("data")
            if name == "time-pos":
                if data is not None:
                    self.state["time_pos"] = float(data)
                    if self.on_time_pos:
                        try:
                            self.on_time_pos(float(data))
                        except Exception:
                            pass
            elif name == "pause":
                self.state["pause"] = bool(data)
                if self.on_pause:
                    try:
                        self.on_pause(bool(data))
                    except Exception:
                        pass
            elif name == "duration":
                self.state["duration"] = (float(data)
                                          if data is not None else None)
            elif name == "eof-reached":
                was = self.state["eof"]
                self.state["eof"] = bool(data)
                if data and not was and self.on_eof:
                    try:
                        self.on_eof()
                    except Exception:
                        pass
            elif name == "path":
                self.state["path"] = data or ""

    # ================= コマンド =================

    async def command(self, *args, timeout: float = 5.0):
        """mpv コマンドを送り、応答(data)を返す。失敗は MpvError。"""
        if not self.connected or self._writer is None:
            raise MpvError(tr("mpv に接続していません"))
        self._req_id += 1
        req_id = self._req_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        payload = json.dumps({"command": list(args), "request_id": req_id})
        self._writer.write(payload.encode("utf-8") + b"\n")
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(req_id, None)

    async def loadfile(self, path: str):
        """動画を読み込んで再生を開始する(現在の動画は置き換え)。

        keep-open で末尾停止していた場合もそのまま次を再生できる。
        eof/duration/time_pos はロードで新しい値に更新される。
        """
        self.state["eof"] = False
        self.state["duration"] = None
        self.state["time_pos"] = None
        await self.command("loadfile", path, "replace")
        # keep-open の末尾停止状態から load すると pause が残ることがあるため
        # 明示的に再生状態にする
        await self.set_pause(False)

    async def set_pause(self, flag: bool):
        await self.command("set_property", "pause", bool(flag))

    async def seek(self, seconds: float):
        """絶対位置(秒)へシークする。"""
        await self.command("seek", float(seconds), "absolute")

    async def set_loop(self, flag: bool):
        """ループ再生(loop-file)を切り替える(ループ動画=合意事項8)。"""
        await self.command("set_property", "loop-file",
                           "inf" if flag else "no")

    async def get_property(self, name: str):
        return await self.command("get_property", name)

    # ================= 終了 =================

    async def quit(self):
        """mpv を終了する(RVP終了時のみ呼ぶ=合意事項)。"""
        try:
            if self.connected:
                await self.command("quit", timeout=2.0)
        except Exception:
            pass
        await self.close()
        # プロセスの終了を少し待ち、残っていれば kill
        if self._proc is not None:
            for _ in range(20):
                if self._proc.poll() is not None:
                    break
                await asyncio.sleep(0.05)
            if self._proc.poll() is None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        if sys.platform != "win32":
            try:
                os.unlink(self.ipc_path)
            except OSError:
                pass

    async def close(self):
        """IPC接続だけを閉じる(プロセスは残す)。"""
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        self._reader = None
        self.state["connected"] = False
