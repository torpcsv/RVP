"""PlaybackClock(一時停止対応クロック)と音声ヘルパー(長さ取得・バイト列ビュー・読み込みエラー説明・リサンプル)。"""
from __future__ import annotations

import pygame
import time
import wave
from ..i18n import tr

from .common import logger


def _audio_duration_ms(path: str) -> int:
    """音声ファイルの長さ(ミリ秒)を取得する。wavは高速なwaveモジュールで読む。"""
    try:
        with wave.open(path, "rb") as w:
            return int(w.getnframes() / w.getframerate() * 1000)
    except Exception:
        pass
    try:
        return int(pygame.mixer.Sound(path).get_length() * 1000)
    except Exception:
        logger.warning(tr("音声の長さを取得できません: %s"), path)
        return 0


class PlaybackClock:
    """一時停止対応の単調クロック(ミリ秒)。

    =214: 再生速度(rate)に対応。now_ms() の進みが rate 倍になる
    (既定 1.0=従来どおり。player 本体は 1.0 のまま使う)。
    """

    def __init__(self):
        self._start: float | None = None
        self._pause_start: float | None = None
        self._paused_total: float = 0.0
        self._rate: float = 1.0

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        """速度を変える(=214)。今の位置を基準に取り直すので位置は飛ばない。
        一時停止状態も維持する。"""
        rate = float(rate)
        if rate <= 0:
            rate = 1.0
        if self._start is not None:
            cur = self.now_ms()
            self._rate = rate
            self.set_ms(cur)
        else:
            self._rate = rate

    def start(self) -> None:
        self._start = time.monotonic()
        self._pause_start = None
        self._paused_total = 0.0

    def pause(self) -> None:
        if self._start is not None and self._pause_start is None:
            self._pause_start = time.monotonic()

    def resume(self) -> None:
        if self._pause_start is not None:
            self._paused_total += time.monotonic() - self._pause_start
            self._pause_start = None

    @property
    def paused(self) -> bool:
        return self._pause_start is not None

    def now_ms(self) -> float:
        if self._start is None:
            return 0.0
        base = self._pause_start if self.paused else time.monotonic()
        return (base - self._start - self._paused_total) * 1000.0 * self._rate

    def set_ms(self, ms: float) -> None:
        """再生位置を指定時刻(ミリ秒)へジャンプさせる。一時停止状態は維持する。"""
        now = time.monotonic()
        self._paused_total = 0.0
        self._start = now - ms / 1000.0 / self._rate
        if self.paused:
            self._pause_start = now


def sound_byte_view(sound) -> memoryview:
    """Soundの生PCMを**コピーせず**バイト単位のmemoryviewで返す(=249)。

    従来の get_raw() はデータ全体をbytesへ複製するため、巨大な音声
    (例: 60分超のwav=700MB級)ではシーク・区間切り出しのたびに
    一時コピーが積み上がっていた。pygame-ce の Sound はバッファ
    プロトコル対応なので、memoryview → cast("B") でゼロコピーの
    ビューが取れる(スライスもビューのままコピーなし)。
    ビューは元Soundのバッファを指すので**書き込み禁止**・元Soundより
    長く持たない。バッファ非対応の実装(テスト用スタブ等)だけ
    get_raw() へフォールバックする。
    """
    try:
        return memoryview(sound).cast("B")
    except (TypeError, ValueError):
        return memoryview(sound.get_raw())


def describe_audio_load_error(path: str, exc: Exception) -> str:
    """音声読み込み失敗の詳しい説明(=249)。該当しなければ ""。

    SDL2は、ミキサー形式(既定 44100Hz/16bit/2ch)と違うwavを読み込む
    ときに形式変換を行うが、その変換バッファ長がint(2^31)管理のため、
    **データ部が約512MiB(48kHz→44.1kHzの実測値。変換内容によっては
    さらに小さい)を超えると空きメモリと無関係に "Out of memory" で
    失敗する**。エラーがメモリ系で、wavヘッダが読めた場合だけ、
    形式と対処(44.1kHzへの変換 or メモリ確保)を具体的に案内する。
    """
    if "memory" not in str(exc).lower():
        return ""
    try:
        with wave.open(path, "rb") as w:
            rate = int(w.getframerate())
            bits = int(w.getsampwidth()) * 8
            ch = int(w.getnchannels())
            data_mib = (w.getnframes() * w.getsampwidth() * ch
                        // (1024 * 1024))
    except Exception:
        return ""
    init = pygame.mixer.get_init() or (44100, -16, 2)
    mrate, mfmt, mch = int(init[0]), abs(int(init[1])), int(init[2])
    src = f"{rate}Hz/{bits}bit/{ch}ch"
    dst = f"{mrate}Hz/{mfmt}bit/{mch}ch"
    if (rate, bits, ch) != (mrate, mfmt, mch):
        return tr(
            "このwavは{0}のため、読み込み時に形式変換が必要です。"
            "変換はデータ部が約512MiBを超えると空きメモリに関係なく"
            "失敗します(SDLの上限)。{1}のwavへ変換すると読み込めます"
            "(このファイルのデータ部: 約{2}MiB)。").format(
                src, dst, data_mib)
    return tr(
        "メモリが不足しています(このwavの展開には約{0}MiB必要です)。"
        "ほかのアプリを閉じるか、音声ファイルを分割してください。").format(
            data_mib)


def resample_frames(raw: bytes, frame_bytes: int, rate: float) -> bytes:
    """=214: 再生速度 rate のための最近傍リサンプリング(音の高さも変わる
    テープ早回し式)。rate>1 で短く(間引き)・rate<1 で長く(複製)。

    numpy/audioop に頼らない(Python 3.13 で audioop が消えるため)。
    rate を分数 p/q(q≦20)に近似し、「入力 p フレーム → 出力 q フレーム」
    のブロック規則を **array の拡張スライス代入 q 回** で一気に作る
    (1フレームずつの Python ループを避ける=数分の音声でも数十ms)。
    フレーム幅が array の型に合わないときだけ素朴なループへ落ちる。
    """
    from fractions import Fraction
    from array import array
    rate = float(rate)
    if rate <= 0 or abs(rate - 1.0) < 1e-9 or frame_bytes <= 0:
        return raw
    fr = Fraction(rate).limit_denominator(20)
    p, q = fr.numerator, fr.denominator
    n_in = len(raw) // frame_bytes
    n_blocks = n_in // p
    n_out = n_blocks * q
    if n_out <= 0:
        return b""
    tc = None
    for cand in ("B", "H", "I", "L", "Q"):
        try:
            if array(cand).itemsize == frame_bytes:
                tc = cand
                break
        except ValueError:
            continue
    if tc is None:
        out = bytearray()
        for j in range(n_out):
            i = (j * p) // q
            out += raw[i * frame_bytes:(i + 1) * frame_bytes]
        return bytes(out)
    src = array(tc)
    src.frombytes(raw[:n_blocks * p * frame_bytes])
    out = array(tc, bytes(n_out * frame_bytes))
    for k in range(q):
        off = (k * p) // q
        out[k::q] = src[off::p][:n_blocks]
    return out.tobytes()
