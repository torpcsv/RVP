"""再生エンジン: 動画アイテム(mpv 連携・A-B ループ・一時停止同期・復旧・OSD)(ScenarioPlayer の mixin)。"""
from __future__ import annotations

import asyncio
import os
import time
from ..mpv_client import MpvClient, MpvError, find_mpv
from ..scenario import (TRACK_LINEAR, TRACK_ROTATE, TRACK_ROTATE_A10,
    TRACK_TWIST, TRACK_VIBRATION)
from ..i18n import tr

from .clock import PlaybackClock
from .common import logger


class _ScenarioPlayerVideoMixin:
    """ScenarioPlayer の mixin(=301 分割)。動画アイテム(mpv 連携・A-B ループ・一時停止同期・復旧・OSD)"""

    async def _play_video_item(self, channel, item, device_types: set,
                               clock, is_primary: bool,
                               seamless: bool = False,
                               limit_ms: float | None = None) -> float:
        """動画アイテム1つを外部mpvで再生し、再生時間(ms)を返す(=52)。

        音声アイテム(_play_channel_item)の動画版。チャンネルの再生方式
        (順番/ランダム)・終了条件・インターバルは _play_channel が共通で
        面倒を見るので、ここは「1本を頭出しして終わりまで待つ」だけを担う。

        seamless: mpv側でループさせる(継ぎ目なし)。この場合 eof/区間終端では
                  戻らず、limit_ms(チャンネルの残り時間)まで再生し続ける。
        limit_ms: END_DURATION チャンネルの残り時間(ms)。None=制限なし。

        clock は動画クロック(_on_mpv_time_pos が mpv の time-pos に従属
        させる)。**区間の先頭が0秒**なので funscript は区間ごとに0秒起点。
        """
        # 復旧(_recover_mpv)と区間判定が参照する「いま再生中の動画」
        self._video_item = item
        self._video_seamless = seamless
        self._video_start_s = item.video_start_s
        self._video_end_s = item.video_end_s
        self.state["video_file"] = item.video
        self.state["channel_audio"][channel.channel_id] = item.video
        if clock is not None:
            clock.start()
            if self._paused:
                clock.pause()
        # **経過時間は動画クロックとは別に測る**(重要): 動画クロックは
        # mpv の time-pos に従属する「再生位置」なので、シークや ab-loop の
        # 折り返しで巻き戻る。チャンネルの終了条件「N秒」は実経過で数える
        # 必要があるため、専用のクロックを立てる(一時停止に追従させるため
        # _active_clocks に登録する)。
        elapsed = PlaybackClock()
        elapsed.start()
        if self._paused:
            elapsed.pause()
        self._active_clocks.append(elapsed)
        if is_primary:
            self.state["audio_file"] = item.video
            self.state["duration_ms"] = 0   # mpvのduration到着で更新
            self.state["elapsed_ms"] = 0

        await self._load_video(item, seamless=seamless)
        if self._paused:
            # 一時停止中に入場(一時停止中の◀◀/▶▶)は動画も停止で待機
            await self.mpv.set_pause(True)
        self._log_item(channel.channel_id, item)
        # **区間の開始が動画の長さを超えていないか**(=54)。
        # 動画の実長は mpv がないと分からないため読み込み時に検証できない。
        # 超えているとシーク先が終端の外になり、mpv が即座に「再生し終わった」
        # 状態になるので、そのアイテムが一瞬で飛ばされる(原因が分かりにくい
        # ため、ここでログに警告を残す)。
        vdur = self.mpv.state.get("duration")
        if vdur and item.video_start_s >= vdur:
            msg = tr("区間の開始({0:g}秒)が動画の長さ({1:.1f}秒)を超えています: {2}").format(
                item.video_start_s, vdur, os.path.basename(item.video))
            logger.warning(msg)
            self._log("warn", msg)
        # 再生開始時の変数操作(音声アイテムと同じタイミング)
        self._apply_ops(item.on_play, tr("アイテム再生時"))

        fs_tasks, had, graph_segs = self._start_video_tracks(
            item, clock, device_types)
        completed = False
        try:
            while not self._stop_requested and self._jump is None:
                if clock is not None and is_primary:
                    self.state["elapsed_ms"] = int(clock.now_ms())
                if self._winding_down:
                    # 動画は音声のようにグレースフルに流し切らない
                    # (=48からの挙動。終了条件が成立したら即座に離脱する)
                    break
                if limit_ms is not None and elapsed.now_ms() >= limit_ms:
                    completed = True
                    break
                if not seamless:
                    if self.mpv.state.get("eof"):
                        completed = True
                        break
                    if self._video_segment_over():
                        # 区間終端では eof が立たないため mpv を明示的に
                        # 止める(止めないと次のアイテムへ移るまでの間に
                        # 区間を越えて再生が続く)。RVP全体の一時停止へは
                        # 伝播させない(_mpv_quiet ガード)。
                        completed = True
                        await self._pause_mpv_quiet()
                        break
                await asyncio.sleep(0.03)
        finally:
            self._graph_finish(graph_segs)
            for t in fs_tasks:
                t.cancel()
            for t in fs_tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            await self._stop_video_devices(had)
            try:
                self._active_clocks.remove(elapsed)
            except ValueError:
                pass

        if completed and not self._stop_requested and self._jump is None:
            self._apply_ops(item.on_complete, tr("アイテム完了時"))
        return elapsed.now_ms()

    async def _ensure_mpv(self):
        """mpvを(必要なら)起動してIPC接続する。

        動画イベント/ステートに入った時に呼ばれる(遅延起動=合意事項)。
        見つからない/接続できない場合は RuntimeError(play() の包括catchで
        status="error"+メッセージ表示になる)。
        """
        if self.mpv is not None and self.mpv.connected and self.mpv.alive:
            return
        path = self.mpv_path or find_mpv()
        if not path:
            raise RuntimeError(
                tr("mpv が見つかりません。設定で mpv のパスを指定してください"))
        if self.mpv is None or not self.mpv.alive:
            self.mpv = MpvClient(path)
            self.mpv.on_time_pos = self._on_mpv_time_pos
            self.mpv.on_pause = self._on_mpv_pause
            self._mpv_loaded_file = ""   # 新プロセス=読み込み済み動画は無効
        try:
            await self.mpv.start(extra_args=self.mpv_extra_args)
        except MpvError as e:
            raise RuntimeError(str(e))

    async def _load_video(self, item, seamless: bool = False):
        """動画アイテムを mpv に用意する(=52。区間指定=51に対応)。

        seamless=True のときは mpv 側でループさせる(1アイテムだけの動画
        チャンネルで、終了条件が時間/無限のとき=従来の「ループ動画」)。

        **同じ動画ファイルが既に読み込まれていれば loadfile を省き、区間の
        先頭へシークするだけで繋ぐ**(ユーザー決定。=48で許容していた
        「ステート切替時につなぎが一瞬黒くなる」問題を解消する)。
        ループの実現方法は区間の有無で使い分ける:
          - 区間なし + ループ → mpv の `loop-file=inf`(従来どおり)
          - 区間あり + ループ → mpv の `ab-loop-a/b`(区間だけを繰り返す。
            loop-file だとファイル先頭に戻ってしまうため)
        """
        seg_loop = seamless and item.video_has_range
        # 古い ab-loop が残っていると seek 直後に飛ばされるので先にクリアする
        await self._set_ab_loop(None, None)
        await self.mpv.set_loop(seamless and not seg_loop)
        same = (self._mpv_loaded_file == item.video
                and self.mpv.connected and self.mpv.alive)
        if same:
            # つなぎの黒フレーム回避: 読み直さず頭出しだけ行う
            # (=52 では次のアイテムが同じファイルの別区間のときにも効く)
            await self.mpv.seek(item.video_start_s)
            await self.mpv.set_pause(False)
            # eof のクリアはシーク/再開の**後**に行う(前の再生で立った
            # eof-reached が遅れて届いても、次のアイテムを誤って終端と
            # 判定しないようにするため)
            self.mpv.state["eof"] = False
        else:
            await self.mpv.loadfile(item.video)
            self._mpv_loaded_file = item.video
            # **ロード完了(duration の観測)を必ず待つ**。=52で重要になった:
            # アイテムを次々に切り替えるとき、前の動画の eof-reached=true が
            # loadfile の後に遅れて届くと、次のアイテムが「開始直後に終端」と
            # 誤判定されて一瞬で飛ばされる(=複数動画が順番に再生されない)。
            # ロード完了まで待ってから eof を落とし直すことで、遅れて届いた
            # 前の動画のイベントを確実に無効化する。
            # (=49以来、start>0 のときは seek の失敗回避のためにも必要)
            await self._wait_video_loaded()
            self.mpv.state["eof"] = False
            if item.video_start_s > 0:
                await self.mpv.seek(item.video_start_s)
        if seg_loop:
            await self._set_ab_loop(item.video_start_s, item.video_end_s)

    async def _wait_video_loaded(self, timeout: float = 5.0):
        """mpv が duration を観測する(=ロード完了)まで待つ。"""
        deadline = time.monotonic() + timeout
        while (self.mpv.state.get("duration") is None
               and self.mpv.alive and time.monotonic() < deadline):
            await asyncio.sleep(0.05)

    async def _set_ab_loop(self, a, b):
        """mpv の A-Bループ(区間ループ)を設定/解除する。

        a/b が None なら解除("no")。ab-loop は mpv の古くからある機能で、
        区間の終端に達すると mpv 側が正確に a へ戻す(RVPの0.05s監視ループで
        seekし直すよりループの継ぎ目が正確)。失敗は無視する
        (未対応ビルドでも区間再生自体は成立させたいため)。
        """
        for prop, val in (("ab-loop-a", a), ("ab-loop-b", b)):
            try:
                await self.mpv.command("set_property", prop,
                                       "no" if val is None else float(val))
            except Exception:
                pass

    def _video_segment_over(self) -> bool:
        """区間の終端(end)に達したか。end 未指定なら常に False。"""
        if self._video_end_s is None:
            return False
        t = self.mpv.state.get("time_pos") if self.mpv is not None else None
        return t is not None and t >= self._video_end_s

    async def _pause_mpv_quiet(self):
        """mpvだけを一時停止する(RVP全体の一時停止へ伝播させない)。

        区間の終端で使う。通常の動画終了(eof)は keep-open の自動 pause を
        `_sync_pause_from_mpv` が eof フラグで弾いてくれるが、区間終端では
        eof が立たないため、明示的にガードする必要がある。
        """
        self._mpv_quiet = True
        try:
            await self.mpv.set_pause(True)
        except Exception:
            pass
        # _sync_pause_from_mpv は 0.08s 待ってから判定するので、それより
        # 長く待ってからガードを解除する
        await asyncio.sleep(0.15)
        self._mpv_quiet = False

    def _on_mpv_time_pos(self, t: float):
        """mpvの再生位置で動画クロックを従属させる(ドリフト補正)。

        50ms超のズレのときだけ set_ms する(フェーズ0で実測検証済み)。
        mpv側のシーク(ユーザー操作)もこの経路で自動追従し、funscriptは
        _fs_resync でbisect再同期する。
        """
        clock = self._video_clock
        if clock is None or clock.paused \
                or self._mpv_recovering or self._mpv_quiet:
            return
        # 区間指定(=51): 区間の先頭を0秒として扱う(ユーザー決定)。
        # これにより funscript は区間ごとに0秒起点で作れる。
        target = max(0.0, (t - self._video_start_s) * 1000.0)
        if abs(clock.now_ms() - target) > 50:
            clock.set_ms(target)
            self._fs_resync = True

    def _on_mpv_pause(self, flag: bool):
        """mpv側の一時停止/再開をRVP全体の一時停止に双方向同期する。

        RVP発の pause/resume は先に status が変わっているため無限ループに
        ならない。即時反映せず少し待って再確認する(_sync_*_from_mpv)のは、
        ①keep-open の末尾到達で mpv が自動的に pause になる(=「一時停止」で
        なく「動画終了」。eof-reached イベントが pause より後に届くことがある)
        ②loadfile 直後の内部的な pause 切替(過渡状態)を、ユーザー操作と
        誤認しないため。
        """
        if self._video_clock is None \
                or self._mpv_recovering or self._mpv_quiet:
            return
        if flag:
            asyncio.ensure_future(self._sync_pause_from_mpv())
        else:
            asyncio.ensure_future(self._sync_resume_from_mpv())

    async def _sync_pause_from_mpv(self):
        await asyncio.sleep(0.08)   # eof-reached の到着を待ってから判定
        if (self.mpv is None or self.mpv.state.get("eof")
                or not self.mpv.state.get("pause")
                or self._mpv_recovering or self._mpv_quiet):
            return
        if self._video_clock is None:
            return
        if self.state["status"] == "playing" and not self._paused:
            await self.pause()

    async def _sync_resume_from_mpv(self):
        await asyncio.sleep(0.08)   # loadfile等の過渡的な切替を除外
        if (self.mpv is None or self.mpv.state.get("pause")
                or self._mpv_recovering or self._mpv_quiet):
            return
        if self._video_clock is None:
            return
        if self.state["status"] == "paused" and self._paused:
            await self.resume()

    def _start_video_tracks(self, item, clock, device_types=None):
        """動画アイテムのトラックを動画クロックで実行開始する。

        種別ごと先勝ち(音声アイテムと同じ規則)。

        =239: **担当(device)が別のチャンネルにある種別のトラックは鳴らさない**。
        判定は「その種別がどこかのチャンネルの担当になっていて、かつ
        この動画チャンネルの担当ではない」。担当が指定されていない種別は
        **従来どおり動画が鳴らす**(=旧シナリオは device を書いていないので
        全トラックがそのまま動く)。これにより、動画に linear のトラックが
        あっても `device: {"linear": "L"}` として**音声チャンネル側へ
        付け替える**ことができる(ユーザー要望=239)。
        戻り値: (タスクのリスト, 後始末フラグdict, グラフ用断片のリスト)
        """
        tasks = []
        segs = []
        had = {"rotate": False, "a10": False, "vib": False}
        if not self._device_output_on():
            return tasks, had, segs   # =252: デバイス連動OFF
        seen = set()
        mine = set(device_types or ())
        for t in item.tracks:
            if t.type in seen:
                continue
            seen.add(t.type)
            if t.type not in mine and t.type in self._assigned_types:
                # =239: 担当が別のチャンネルにある = そちらが駆動源
                continue
            try:
                # =59: 動画トラックも動画の区間に連動させる(ユーザー決定)。
                # =51〜=58 は「区間の先頭を0秒とした別funscript」前提だったが、
                # 音声と規則を1つに揃えた(区間ごとに別スクリプトを当てたい
                # ときはトラック側の区間欄で上書きする)。
                src = self._load_track_src(
                    item, t, rotate=t.type in (TRACK_ROTATE, TRACK_ROTATE_A10))
                if src is None:
                    continue
            except Exception as e:
                logger.error(tr("スクリプトの読み込み失敗: %s (%s)"),
                             t.funscript, e)
                continue
            if t.type == TRACK_LINEAR and src.actions:
                tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock)))
                segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_TWIST and src.actions:
                tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock, twist=True)))
                segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_ROTATE:
                had["rotate"] = True
                if src.steps:
                    tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "ufo")))
                    segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_ROTATE_A10:
                had["a10"] = True
                if src.steps:
                    tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "a10")))
                    segs += self._graph_add_track(t.type, src, clock)
            elif t.type == TRACK_VIBRATION and src.actions:
                had["vib"] = True
                tasks.append(asyncio.ensure_future(
                    self._run_vibration(src, clock)))
                segs += self._graph_add_track(t.type, src, clock)
        return tasks, had, segs

    async def _stop_video_devices(self, had: dict):
        """動画トラックが動かしていたデバイスを停止する(アイテムと同じ後始末)。"""
        if had.get("rotate") and not self._stop_requested:
            try:
                await self.intiface.send_rotate(50)
                self.state["rotate_pos"] = 50
            except Exception:
                pass
        if had.get("a10") and not self._stop_requested:
            try:
                await self.intiface.send_rotate_a10(50)
                self.state["rotate_a10_pos"] = 50
            except Exception:
                pass
        if had.get("vib") and not self._stop_requested:
            try:
                await self.intiface.send_vibration(0)
                self.state["vibration_pos"] = 0
            except Exception:
                pass

    async def _recover_mpv(self) -> None:
        """動画再生中に mpv が落ちた/閉じられたとき、再起動して復帰する。

        フェーズ2(ユーザー決定=自動復旧): mpvを再起動→同じ動画をロード→
        直前の再生位置へシーク→一時停止状態を復元して継続する。
        直前位置は動画クロックから取る(mpv従属クロックのため、切断時点の
        値がそのまま残っている)。
        無限再起動を避けるため、60秒以内に3回目の復旧が必要になったら
        RuntimeError でエラー停止する(繰り返しクラッシュ=環境異常とみなす)。
        """
        now = time.monotonic()
        self._mpv_fail_times = [t for t in self._mpv_fail_times
                                if now - t < 60.0]
        self._mpv_fail_times.append(now)
        if len(self._mpv_fail_times) >= 3:
            raise RuntimeError(
                tr("mpv が繰り返し終了するため停止しました"))
        logger.warning(
            tr("mpv との接続が切れました。再起動して復帰します"))
        pos_s = 0.0
        if self._video_clock is not None:
            pos_s = max(0.0, self._video_clock.now_ms() / 1000.0)
        paused = self._paused
        # 復旧中の loadfile/pause 切替イベントをユーザー操作と誤同期しない
        # (特に一時停止中の復旧: loadfileは再生開始状態になるため、
        #  過渡的な pause=False で RVP を再開してしまわないようにする)
        self._mpv_recovering = True
        try:
            if self.mpv is not None:
                try:
                    await self.mpv.close()
                except Exception:
                    pass
            self._mpv_loaded_file = ""   # プロセスが変わるので読み直す
            await self._ensure_mpv()
            item = self._video_item
            seg_loop = self._video_seamless and item.video_has_range
            await self.mpv.set_loop(self._video_seamless and not seg_loop)
            await self.mpv.loadfile(item.video)
            self._mpv_loaded_file = item.video
            # ロード完了(durationの観測)を待ってから位置を復元する
            # (loadfile直後のseekはファイル未ロードで失敗することがある)
            await self._wait_video_loaded()
            # pos_s は区間先頭を0とした相対位置(=51)なので、mpv へは
            # 区間の開始秒を足した絶対位置でシークする
            abs_s = item.video_start_s + pos_s
            if abs_s > 0.5:
                try:
                    await self.mpv.seek(abs_s)
                except Exception:
                    pass
            if seg_loop:
                await self._set_ab_loop(item.video_start_s, item.video_end_s)
            if paused:
                await self.mpv.set_pause(True)
        finally:
            self._mpv_recovering = False

    def _video_osd(self, text: str) -> None:
        """mpvの画面にオーバーレイ文字を出す/消す(選択肢・入力の案内)。

        フェーズ2(ユーザー決定): フルスクリーン動画中でも選択肢/数値入力
        カードの存在に気づけるように、mpv の show-text OSD で案内する。
        空文字=クリア。案内目的のみなので失敗は無視する。
        動画のあるイベントの最中(または直後の待機フェーズ=state["video_file"]
        が残っている間)だけ表示する。
        """
        if self.mpv is None or not self.mpv.connected:
            return
        # 表示は動画のあるイベントの間だけ。クリアは接続中なら常に送る
        # (イベント開始時のリセットは video_file クリア後に呼ばれるため)
        if text and self._video_clock is None \
                and not self.state.get("video_file"):
            return

        async def _send():
            try:
                if text:
                    await self.mpv.command("show-text", text, 3600000)
                else:
                    await self.mpv.command("show-text", "", 1)
            except Exception:
                pass
        asyncio.ensure_future(_send())

    async def shutdown_mpv(self) -> None:
        """mpvを終了する。RVPの終了時のみ呼ぶ(合意事項)。"""
        if self.mpv is not None:
            try:
                await self.mpv.quit()
            except Exception:
                pass
            self.mpv = None
            self._mpv_loaded_file = ""   # 次回は必ず loadfile から(=51)
