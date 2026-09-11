"""再生エンジン: 音声(音量・シーク・スライス・トラック読み込み)と BGM(ScenarioPlayer の mixin)。"""
from __future__ import annotations

import asyncio
import os
import pygame
from ..funscript import Funscript
from ..scenario import MODE_RANDOM, track_range_ms
from ..i18n import tr

from .clock import describe_audio_load_error, resample_frames, sound_byte_view
from .common import BGM_FADEOUT_MS, _BGM_SLOT, _CH_SLOT, logger


class _ScenarioPlayerAudioMixin:
    """ScenarioPlayer の mixin(=301 分割)。音声(音量・シーク・スライス・トラック読み込み)と BGM"""

    def set_master_volume(self, volume: float) -> None:
        """マスター音量(0.0〜1.0)を設定し、再生中の音声へ即時反映する。

        各チャンネルのパン(左右バランス)に乗算して適用する。
        pygameのChannel.set_volumeは軽量なため、UIスレッドから直接呼んでよい。
        設定はUI側でコンフィグ保存され、次回起動時に復元される。
        """
        volume = max(0.0, min(1.0, float(volume)))
        self.master_volume = volume
        for slot, (left, right) in list(self._slot_pan.items()):
            try:
                pygame.mixer.Channel(slot).set_volume(left * volume,
                                                      right * volume)
            except Exception:
                pass

    async def seek_relative(self, delta_ms: int) -> None:
        """現在の再生位置から相対シークする(10秒送り/戻しボタン用)。

        先頭より前は0、末尾より後は末尾付近にクランプされる(seekと同じ)。
        音声も実際の途中位置から再生される。
        """
        if self.state["status"] not in ("playing", "paused"):
            return
        await self.seek(int(self.state["elapsed_ms"]) + int(delta_ms))

    @staticmethod
    def _sliced_sound(sound, ms: float, end_ms: float | None = None):
        """途中位置(ms)から(end_msまで)のSoundを作る。

        Soundのrawデータはmixer出力形式へ変換済みのため、
        サンプル位置でバイト列を切り出せば正確な途中再生になる。
        end_ms は=59の区間指定で使う(None=末尾まで)。
        =249: get_raw()(全体をbytesへ複製)をやめ、ゼロコピーの
        memoryview経由に変更。巨大な音声でもコピーは新Soundの
        切り出し範囲1回だけになる(以前はシークのたびに全体×2〜3)。
        """
        init = pygame.mixer.get_init()
        if not init:
            return None
        freq, fmt, channels = init
        frame_bytes = abs(fmt) // 8 * channels
        raw = sound_byte_view(sound)
        off = int(freq * ms / 1000.0) * frame_bytes
        if off >= len(raw):
            off = max(0, len(raw) - frame_bytes)
        if end_ms is None:
            return pygame.mixer.Sound(buffer=raw[off:])
        end = int(freq * end_ms / 1000.0) * frame_bytes
        end = max(off + frame_bytes, min(end, len(raw)))
        return pygame.mixer.Sound(buffer=raw[off:end])

    @staticmethod
    def _rated_sound(sound, rate: float):
        """=214: 速度 rate で鳴らすための Sound(リサンプリング済み)。
        rate=1.0 は元の Sound をそのまま返す。mixer 未初期化は None。"""
        init = pygame.mixer.get_init()
        if not init:
            return None
        if abs(float(rate) - 1.0) < 1e-9:
            return sound
        freq, fmt, channels = init
        frame_bytes = abs(fmt) // 8 * channels
        # =249: get_raw()の全複製をやめ、ゼロコピーのビューから読む
        raw = resample_frames(sound_byte_view(sound), frame_bytes, rate)
        if not raw:
            return None
        return pygame.mixer.Sound(buffer=raw)

    def _load_track_src(self, item, track, rotate: bool = False):
        """トラックのスクリプトを読み込み、区間指定(=59)を適用して返す。

        適用する区間は**トラック個別 > アイテム**(`track_range_ms`)。
        `sliced()` が区間の先頭を0msへずらすので、実行側(_run_funscript 等)は
        クロックをそのまま使えて無改修で済む。
        """
        if rotate:
            src = self._load_rotate_safe(track.funscript)
        else:
            src = Funscript.load(track.funscript)
        if src is None:
            return None
        lo, hi = track_range_ms(item, track)
        if lo > 0 or hi is not None:
            src = src.sliced(lo, hi)
        return src

    def _stop_all_audio(self) -> None:
        for slot in _CH_SLOT.values():
            try:
                pygame.mixer.Channel(slot).stop()
            except Exception:
                pass

    def _bgm_on(self) -> bool:
        """シナリオのBGM機能フラグ(bgm_enabled)。属性ごと無い場合はFalse。"""
        return bool(getattr(getattr(self, "scenario", None),
                            "bgm_enabled", False))

    def _bgm_load(self, path: str):
        """BGM音声を読み込む(同一シナリオ再生中はキャッシュ)。

        失敗は None を返し、警告ログへ原因(=249のメモリ系ガイダンス含む)を
        添える。
        """
        snd = self._bgm_sounds.get(path)
        if snd is not None:
            return snd
        try:
            snd = pygame.mixer.Sound(path)
        except Exception as e:
            logger.error(tr("BGMの読み込み失敗: %s (%s)"), path, e)
            extra = describe_audio_load_error(path, e)
            self._log("warn",
                      tr("BGMの読み込みに失敗しました: {0}").format(
                          os.path.basename(path))
                      + ((" " + extra) if extra else ""))
            return None
        self._bgm_sounds[path] = snd
        return snd

    def _bgm_set_pan(self, item, spec) -> None:
        """再生中BGMのパンを適用する(item個別 > BGM既定 > 等倍)。

        _slot_pan へ登録するので、以後の set_master_volume にも追従する。
        """
        pan = item.pan or spec.pan
        left, right = (pan.left, pan.right) if pan is not None else (1.0, 1.0)
        self._slot_pan[_BGM_SLOT] = (left, right)
        try:
            pygame.mixer.Channel(_BGM_SLOT).set_volume(
                left * self.master_volume, right * self.master_volume)
        except Exception:
            pass

    async def _apply_bgm(self, spec) -> None:
        """ノード(ステート)入場時のBGM適用(=256)。

        spec=None は「前のBGMを引き継ぐ」= 何もしない(鳴りっぱなし)。
        "off" は停止、"set" は**同じ構成でも必ず先頭から再生し直す**(Q3)。
        bgm_enabled=False のシナリオでは定義があっても一切鳴らさない
        (device_enabled と同じ「データは保持・出力だけ抑制」方式)。
        """
        if spec is None or not self._bgm_on():
            return
        was = self._bgm_active
        await self._bgm_stop(fade=True)
        if spec.mode == "off":
            if was:
                self._log("bgm", tr("BGM停止"))
            return
        self._bgm_start(spec)

    def _bgm_start(self, spec) -> None:
        """BGM再生を開始する(読み込めない曲はスキップ・全滅なら警告のみ)。"""
        items = [it for it in spec.items
                 if self._bgm_load(it.audio) is not None]
        if not items:
            self._log("warn", tr("BGMを再生できません(読み込めた音声がありません)"))
            return
        self._bgm_gen += 1
        self._bgm_active = True
        self._log("bgm", tr("BGM開始: {0}").format(spec.describe()))
        if len(items) == 1:
            snd = self._bgm_load(items[0].audio)
            self._bgm_set_pan(items[0], spec)
            try:
                ch = pygame.mixer.Channel(_BGM_SLOT)
                ch.play(snd, loops=-1)   # 1曲はミキサー任せのギャップレスループ
                if self._paused:
                    ch.pause()
            except Exception:
                pass
            return
        self._bgm_task = asyncio.ensure_future(self._bgm_feed(items, spec))

    def _bgm_iter(self, items, order):
        """BGMの再生順を無限に生成する。

        順番(sequential)=リストを順に周回。ランダム(random)=袋方式
        (1周分をシャッフルして出し切り、周回の境目で同じ曲が連続しない
        よう先頭を入れ替える=Q4)。
        """
        import random as _r
        if order != MODE_RANDOM or len(items) == 1:
            i = 0
            while True:
                yield items[i % len(items)]
                i += 1
        last = None
        while True:
            bag = list(items)
            _r.shuffle(bag)
            if len(bag) >= 2 and bag[0] is last:
                bag[0], bag[-1] = bag[-1], bag[0]
            for it in bag:
                yield it
                last = it

    def _bgm_queue_sound(self, item, cur_snd):
        """予約用のSoundを返す。直前と同一オブジェクトなら独立品を作る。

        切替検知は get_sound() のオブジェクト比較で行うため、同じファイルを
        連続で並べるとキャッシュの同一Soundでは切替が見えない。その場合だけ
        キャッシュ外の独立したSoundを読み直して予約する。
        """
        snd = self._bgm_load(item.audio)
        if snd is None:
            return None
        if snd is cur_snd:
            try:
                snd = pygame.mixer.Sound(item.audio)
            except Exception:
                pass
        return snd

    async def _bgm_feed(self, items, spec) -> None:
        """複数曲BGMの送り込みループ(=256)。

        pygameの Channel.queue() で次曲を予約し、再生中Soundの入れ替わり
        (get_sound() のオブジェクト変化)を検知したら「その曲のパン適用+
        さらに次を予約」する。切り替えはミキサー側で行われるためほぼ
        ギャップレス。**get_queued() は pygame-ce 2.5系に存在しない**ので
        使わない(コンテナ実測: AttributeError)。
        """
        ch = pygame.mixer.Channel(_BGM_SLOT)
        it = self._bgm_iter(items, spec.order)
        cur = next(it)
        nxt = next(it)
        cur_snd = self._bgm_load(cur.audio)
        nxt_snd = self._bgm_queue_sound(nxt, cur_snd)
        self._bgm_set_pan(cur, spec)
        try:
            ch.play(cur_snd)
            if nxt_snd is not None:
                ch.queue(nxt_snd)
            if self._paused:
                ch.pause()
        except Exception:
            return
        while True:
            await asyncio.sleep(0.1)
            try:
                busy = ch.get_busy()
                snd = ch.get_sound()
            except Exception:
                return
            if self._paused:
                continue
            if not busy:
                # 送り込みが間に合わず完全に止まった(重い処理等)。次曲から
                # 鳴らし直す
                cur, cur_snd = nxt, nxt_snd
                nxt = next(it)
                nxt_snd = self._bgm_queue_sound(nxt, cur_snd)
                self._bgm_set_pan(cur, spec)
                try:
                    ch.play(cur_snd)
                    if nxt_snd is not None:
                        ch.queue(nxt_snd)
                except Exception:
                    return
            elif snd is not cur_snd:
                # 予約曲へ切り替わった: パン適用+さらに次を予約
                cur, cur_snd = nxt, nxt_snd
                nxt = next(it)
                nxt_snd = self._bgm_queue_sound(nxt, cur_snd)
                self._bgm_set_pan(cur, spec)
                try:
                    if nxt_snd is not None:
                        ch.queue(nxt_snd)
                except Exception:
                    return

    async def _bgm_stop(self, fade: bool = True) -> None:
        """BGMを停止する(=256)。fade=True で300msフェードアウト。

        フェード後に残る予約曲(queue)を掃除するため、フェード完了の少し後に
        stop() を撃つ小タスクを流す。その間に新しいBGMが始まった場合は
        世代カウンタ(_bgm_gen)の不一致で何もしない。
        """
        task, self._bgm_task = self._bgm_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._bgm_active = False
        self._bgm_gen += 1
        gen = self._bgm_gen
        self._slot_pan.pop(_BGM_SLOT, None)
        try:
            ch = pygame.mixer.Channel(_BGM_SLOT)
            if fade and ch.get_busy() and not self._paused:
                ch.fadeout(BGM_FADEOUT_MS)

                async def _kill():
                    await asyncio.sleep(BGM_FADEOUT_MS / 1000 + 0.05)
                    if self._bgm_gen == gen:   # 新しいBGMが始まっていない
                        try:
                            pygame.mixer.Channel(_BGM_SLOT).stop()
                        except Exception:
                            pass

                asyncio.ensure_future(_kill())
            else:
                ch.stop()
        except Exception:
            pass
