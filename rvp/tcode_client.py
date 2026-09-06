"""TCode(シリアル)直接出力クライアント(=80)。

FunSR1 2.0 / OSR2 / SR6 などの TCode v0.3 デバイスへ、Intiface を経由せず
シリアル(COM)ポートでコマンドテキストを直接送る。

背景: buttplug のtcode実装(リリース版v9系で確認)は LinearCmd の
feature index を常に「L{N}」へ写像するため、**R0(twist)へ届く経路が
存在しない**(buttplugio/buttplug issue #397・2021年から未解決)。さらに
Intiface の tcode 既定設定は L0 の1軸のみ。twist 軸の駆動にはこの
直接経路が必要になる(MultiFunPlayer と同じ方式)。

設計:
- **pyserial はオプショナル依存**(tkinterdnd2 と同方式)。無ければ
  HAS_SERIAL=False となり機能は無効・RVP本体は普通に動く。
- **送信のみ**(デバイスからの読み出しはしない)。書き込みは短時間で
  完了する(115200baud・十数バイト)ため asyncio ループ内で直接行うが、
  デバイス側の停止でブロックしないよう write_timeout を設ける。
- 対応軸は **L0(ストローク) / R0(twist)** のみ(=80スコープ)。
- **COMポートは排他**: Intiface Central のシリアルポートデバイスマネージャが
  同じポートを掴んでいると開けない(その場合は Intiface 側の
  Serial Port マネージャをOFFにする)。
"""
from __future__ import annotations

import logging

from .i18n import tr

try:
    import serial                      # pyserial (BSD-3)
    from serial.tools import list_ports
    HAS_SERIAL = True
except Exception:                      # pragma: no cover - 未インストール環境
    serial = None
    list_ports = None
    HAS_SERIAL = False

logger = logging.getLogger("rvp.tcode")

DEFAULT_BAUD = 115200

AXIS_L0 = "L0"   # 上下ストローク
AXIS_R0 = "R0"   # twist(有限回転)


def available_ports() -> list[tuple[str, str]]:
    """接続可能なシリアルポートの一覧 [(device, description), ...]。"""
    if not HAS_SERIAL:
        return []
    try:
        return [(p.device, p.description or "") for p in list_ports.comports()]
    except Exception:
        return []


class TCodeClient:
    """TCode v0.3 デバイスへのシリアル直接送信。"""

    def __init__(self):
        self.port: str = ""
        self.baud: int = DEFAULT_BAUD
        self.connected: bool = False
        self._ser = None

    # ---- 接続 ----

    def connect(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        """ポートを開く(同期・失敗は例外)。開けたら connected=True。"""
        if not HAS_SERIAL:
            raise RuntimeError(tr("pyserialがインストールされていません"))
        self.disconnect()
        self._ser = serial.Serial(
            port=port, baudrate=int(baud),
            bytesize=8, parity="N", stopbits=1,
            timeout=0.2, write_timeout=0.3,
        )
        self.port = port
        self.baud = int(baud)
        self.connected = True
        logger.info("TCode接続: %s @ %d", port, self.baud)

    def disconnect(self) -> None:
        """ポートを閉じる(未接続なら何もしない)。"""
        self.connected = False
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    # ---- 送信 ----

    @staticmethod
    def format_cmd(axis: str, pos100: float, duration_ms: int) -> bytes:
        """TCodeコマンド文字列を作る。

        pos100: デバイス実位置 0-100(RVPのレンジ/反転適用後の値)。
        TCodeの値域 0-999(3桁)へ変換し、I=所要時間(ms)を付ける。
        例: L0500I100\\n = L0軸を0.500へ100msかけて移動。
        """
        val = round(max(0.0, min(100.0, float(pos100))) / 100.0 * 999)
        dur = max(20, int(duration_ms))
        return f"{axis}{val:03d}I{dur}\n".encode("ascii")

    async def send_axis(self, axis: str, pos100: float,
                        duration_ms: int) -> None:
        """軸へ移動コマンドを送る(L0/R0)。失敗時は connected=False にして再送出。"""
        ser = self._ser
        if ser is None or not self.connected:
            raise RuntimeError(tr("TCodeデバイスが接続されていません"))
        data = self.format_cmd(axis, pos100, duration_ms)
        try:
            ser.write(data)
        except Exception as e:
            logger.error(tr("TCodeコマンド送信失敗: %s"), e)
            self.connected = False
            raise
