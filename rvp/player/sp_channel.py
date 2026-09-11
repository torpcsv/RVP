"""再生エンジン: チャンネル/アイテムの再生(音声アイテム・スクリプト専用アイテム・interval)(ScenarioPlayer の mixin)。"""
from __future__ import annotations

import asyncio
import os
import pygame
from ..scenario import (DEFAULT_PAN, END_DURATION, END_NONE, END_PLAYS,
    END_REPEAT, MODE_RANDOM_BAG, MODE_SEQUENTIAL, Pan, TRACK_LINEAR,
    TRACK_ROTATE, TRACK_ROTATE_A10, TRACK_TWIST, TRACK_VIBRATION)
from ..i18n import tr

from .clock import PlaybackClock, _audio_duration_ms, describe_audio_load_error
from .common import _CH_SLOT, logger


class _ScenarioPlayerChannelMixin:
    """ScenarioPlayer の mixin(=301 分割)。チャンネル/アイテムの再生(音声アイテム・スクリプト専用アイテム・interval)"""

    async def _play_channel(self, channel, device_types: set, clock,
                            is_primary: bool, stats: dict,
                            end_resolved: tuple | None = None) -> None:
        """1チャンネルを終了条件まで再生する(本体は _play_channel_body)。

        =286: 終了条件で先に終わったチャンネルの「再生中の音声」表示は
        消す(finally で channel_audio から外す)。従来はステートが変わっても
        前のステートで鳴らしたチャンネルのファイル名が残っていた(ユーザー
        報告: S1→S2→S3 で C→L→R と鳴らすと S3 中も C/L が表示される)。
        """
        try:
            await self._play_channel_body(channel, device_types, clock,
                                          is_primary, stats, end_resolved)
        finally:
            self.state["channel_audio"].pop(channel.channel_id, None)

    async def _play_channel_body(self, channel, device_types: set, clock,
                                 is_primary: bool, stats: dict,
                                 end_resolved: tuple | None = None) -> None:
        """1チャンネルを終了条件まで再生する。

        device_types: このチャンネルが担当するデバイス種別の集合。
        stats: ステート内統計 {"time_ms": float, "count": int}。
               アイテム再生ごとに時間(音声+インターバル)と回数を加算する。
        END_NONE のチャンネルはワインドダウン/停止/ジャンプまで無限に再生する。
        終了条件の数値(count/duration)はステート入場時の変数値で解決される。
        end_resolved: 入場時に解決済みの (count, duration_ms)。=99の範囲抽選は
        resolve_end のたびに引き直されるため、ログ(=56)に出した値と実際に使う
        値が一致するよう、呼び出し側で解決した結果を受け取る(None=ここで解決)。
        """
        end_count, end_duration_ms = (end_resolved if end_resolved is not None
                                      else channel.resolve_end(self.vars))
        ch_elapsed_ms = 0.0

        def interrupted():
            return self._stop_requested or self._jump or self._winding_down

        # 動画チャンネル(=52): アイテムが1つだけで終了条件が「N秒」「無限」の
        # ときは mpv 側でループさせて継ぎ目を無くす(従来の「ループ動画」に
        # 相当。RVPが毎回シークし直すより mpv の ab-loop/loop-file が正確)。
        seamless_video = (channel.has_video and len(channel.items) == 1
                          and channel.end_type in (END_NONE, END_DURATION))

        async def play_one(item) -> None:
            nonlocal ch_elapsed_ms
            if item.video:
                limit = None
                if channel.end_type == END_DURATION:
                    limit = max(0.0, end_duration_ms - ch_elapsed_ms)
                dur = await self._play_video_item(
                    channel, item, device_types, clock, is_primary,
                    seamless=seamless_video, limit_ms=limit)
            else:
                dur = await self._play_channel_item(
                    channel, item, device_types, clock, is_primary)
            stats["count"] += 1
            # イベント終了条件「N回再生」(plays)は音声の再生本数のみ数える
            # (ユーザー決定)。スクリプトのみアイテムはチャンネル自身の終了
            # 条件(N回/N周)や channel_count 移行では従来どおりカウントされる
            # (stats側は無条件加算)。=52: 動画もコンテンツなので数える。
            if item.audio or item.video:
                self._event_plays += 1
            interval = await self._wait_interval(channel, device_types)
            total = dur + interval
            ch_elapsed_ms += total
            stats["time_ms"] += total

        plays_done = 0

        if channel.mode == MODE_SEQUENTIAL:
            if channel.end_type == END_PLAYS:
                # 順番にN回再生して終了(Nがアイテム数を超える場合は周回)
                i = 0
                while plays_done < end_count:
                    if interrupted():
                        return
                    await play_one(channel.items[i % len(channel.items)])
                    plays_done += 1
                    i += 1
                return
            passes = end_count if channel.end_type == END_REPEAT else 1
            loop_forever = channel.end_type == END_NONE
            while True:
                for _ in range(passes if not loop_forever else 1):
                    for item in channel.items:
                        if interrupted():
                            return
                        await play_one(item)
                        if (channel.end_type == END_DURATION
                                and ch_elapsed_ms >= end_duration_ms):
                            return
                if not loop_forever:
                    return
        else:  # MODE_RANDOM / MODE_RANDOM_BAG
            # =76 ランダム(重複なし): 「袋」=この周回でまだ再生していない
            # アイテムのリスト。袋から重み付きで引き、引けるものが無くなったら
            # (=一巡した or 残りが全て重み0以下)全アイテムで補充する。
            # 重みは従来どおり抽選のたびに解決する(=74)。
            bag = list(channel.items)
            bags_done = 0        # =98: 使い切った袋の数(repeat用)
            bag_limit_hit = False

            def draw():
                nonlocal bag, bags_done, bag_limit_hit
                if channel.mode != MODE_RANDOM_BAG:
                    return self._weighted_choice(channel.items)
                item = self._weighted_choice(bag)
                if item is None:
                    # 袋から引けない=1周消化(何か引いた後に限る。開始時から
                    # 全アイテム重み0以下のときは周と数えず=74の自然終了へ)。
                    # 早めの補充(残りが全て重み0以下)も1周と数える(=98
                    # ユーザー決定)。
                    if len(bag) < len(channel.items):
                        bags_done += 1
                        if (channel.end_type == END_REPEAT
                                and bags_done >= end_count):
                            # =98: 袋をN回使い切ったら終了(補充しない)
                            bag_limit_hit = True
                            return None
                    # 補充=全アイテムから抽選し直す。直前アイテムの回避は
                    # しない(=97でユーザー決定により撤去。アイテム数2〜3
                    # では境界回避が抽選を機械的な交互にしてしまうため)。
                    item = self._weighted_choice(channel.items)
                    if item is None:
                        return None   # 全アイテム重み0以下(=74と同じ扱い)
                    self._log("chan", tr(
                        "チャンネル{0}: 重複なしの候補を使い切ったため補充").format(
                            channel.channel_id))
                    bag = [it for it in channel.items if it is not item]
                else:
                    bag = [it for it in bag if it is not item]
                return item

            while not interrupted():
                if (channel.end_type == END_DURATION
                        and ch_elapsed_ms >= end_duration_ms):
                    return
                item = draw()
                if item is None:
                    if bag_limit_hit:
                        # =98: N周(袋N回)の消化による通常終了。ログは他の
                        # 終了条件と同様に出さない(理由はイベント側の→行)
                        return
                    # =74: 全アイテムの重みが0以下=出せるものが無い →
                    # チャンネル自然終了(ユーザー決定)。無限chでもここで終わる
                    self._log("end", tr(
                        "チャンネル{0}: 全アイテムの重みが0のため終了").format(
                            channel.channel_id))
                    return
                await play_one(item)
                plays_done += 1
                if (channel.end_type == END_PLAYS
                        and plays_done >= end_count):
                    return

    async def _wait_interval(self, channel, device_types: set) -> float:
        """チャンネルのインターバルを抽選し、その時間だけ待機する。

        待機時間(ms)を返す。一時停止中はカウントを止め、
        停止/ジャンプ/ワインドダウンで即中断する。
        """
        interval_ms = channel.pick_interval_ms()
        if interval_ms <= 0:
            return 0.0
        # 担当チャンネルのインターバル中は担当デバイスを停止(funscript間の空白)
        if TRACK_ROTATE in device_types:
            try:
                await self.intiface.send_rotate(50)
                self.state["rotate_pos"] = 50
            except Exception:
                pass
        if TRACK_ROTATE_A10 in device_types:
            try:
                await self.intiface.send_rotate_a10(50)
                self.state["rotate_a10_pos"] = 50
            except Exception:
                pass
        if TRACK_VIBRATION in device_types:
            try:
                await self.intiface.send_vibration(0)
                self.state["vibration_pos"] = 0
            except Exception:
                pass
        waited = 0.0
        step = 0.05
        while waited < interval_ms / 1000.0:
            if self._stop_requested or self._jump or self._winding_down:
                break
            if self._paused:
                await asyncio.sleep(step)
                continue
            await asyncio.sleep(step)
            waited += step
        return interval_ms

    async def _play_channel_item(self, channel, item, device_types: set,
                                 clock, is_primary: bool) -> float:
        """チャンネル内の1アイテムを再生し、実際の再生時間(ms)を返す。

        担当種別(device_types)に含まれるトラックのfunscriptのみ実行する。
        """
        if not item.audio:
            # スクリプトのみアイテム(スクリプト専用チャンネル)
            return await self._play_script_item(channel, item, device_types,
                                                clock, is_primary)
        self._log_item(channel.channel_id, item)
        slot = _CH_SLOT[channel.channel_id]
        try:
            sound = pygame.mixer.Sound(item.audio)
        except Exception as e:
            logger.error(tr("音声の読み込み失敗: %s (%s)"), item.audio, e)
            # =249: メモリ系の失敗は原因と対処(44.1kHzへの変換など)を添える
            extra = describe_audio_load_error(item.audio, e)
            self._log("warn", tr("音声の読み込みに失敗しました: {0}").format(
                os.path.basename(item.audio))
                + ((" " + extra) if extra else ""))
            return 0.0

        duration_ms = _audio_duration_ms(item.audio)
        # =59: 区間指定があれば、その範囲だけを鳴らす Sound に差し替える。
        # 経過表示・シークバー・funscript同期はすべて**区間の先頭が0秒**
        # (動画=51と同じ方針)。シーク元(_slot_sound)も切り出し後を持たせて、
        # シーク操作が区間の外へ出ないようにする。
        if item.has_range:
            hi = None if item.end_s is None else item.end_s * 1000.0
            sliced = self._sliced_sound(sound, item.start_s * 1000.0, hi)
            if sliced is not None:
                sound = sliced
                duration_ms = int(sound.get_length() * 1000)

        # パン: item個別 > チャンネル既定
        pan = item.pan or channel.pan or Pan(*DEFAULT_PAN[channel.channel_id])

        py_channel = pygame.mixer.Channel(slot)
        py_channel.play(sound)
        # 再生開始時の変数操作(一時停止中に開始したアイテムでも発火する)
        self._apply_ops(item.on_play, tr("アイテム再生時"))
        self._slot_pan[slot] = (pan.left, pan.right)
        self._slot_sound[slot] = sound   # シーク時の切り出し元(原音)
        try:
            py_channel.set_volume(pan.left * self.master_volume,
                                  pan.right * self.master_volume)
        except Exception:
            pass
        # 一時停止中に開始したアイテム(一時停止中のジャンプ先など)は、
        # 音声もクロックも一時停止状態で始める。再生ボタン(resume)で動き出す。
        if self._paused:
            try:
                py_channel.pause()
            except Exception:
                pass

        self.state["channel_audio"][channel.channel_id] = item.audio

        fs_tasks = []
        graph_segs = []            # =69 グラフ表示用
        had_rotate = False
        had_rotate_a10 = False
        had_vibration = False
        if clock is not None:
            clock.start()
            if self._paused:
                clock.pause()
        if is_primary:
            self.state["audio_file"] = item.audio
            self.state["duration_ms"] = duration_ms

        if device_types and clock is not None and self._device_output_on():
            # トラックは _load_track_src が区間(=59)を適用して返す
            if TRACK_LINEAR in device_types:
                linear = item.tracks_of(TRACK_LINEAR)
                if linear:
                    fs = self._load_track_src(item, linear[0])
                    if fs is not None and fs.actions:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_funscript(fs, clock)))
                        graph_segs += self._graph_add_track(
                            TRACK_LINEAR, fs, clock)
            if TRACK_TWIST in device_types:
                twist = item.tracks_of(TRACK_TWIST)
                if twist:
                    tfs = self._load_track_src(item, twist[0])
                    if tfs is not None and tfs.actions:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_funscript(tfs, clock, twist=True)))
                        graph_segs += self._graph_add_track(
                            TRACK_TWIST, tfs, clock)
            if TRACK_ROTATE in device_types:
                rotate = item.tracks_of(TRACK_ROTATE)
                if rotate:
                    had_rotate = True
                    tl = self._load_track_src(item, rotate[0], rotate=True)
                    if tl is not None and tl.steps:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_rotate_timeline(tl, clock, "ufo")))
                        graph_segs += self._graph_add_track(
                            TRACK_ROTATE, tl, clock)
            if TRACK_ROTATE_A10 in device_types:
                rotate_a10 = item.tracks_of(TRACK_ROTATE_A10)
                if rotate_a10:
                    had_rotate_a10 = True
                    tl = self._load_track_src(item, rotate_a10[0], rotate=True)
                    if tl is not None and tl.steps:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_rotate_timeline(tl, clock, "a10")))
                        graph_segs += self._graph_add_track(
                            TRACK_ROTATE_A10, tl, clock)
            if TRACK_VIBRATION in device_types:
                vib = item.tracks_of(TRACK_VIBRATION)
                if vib:
                    had_vibration = True
                    vfs = self._load_track_src(item, vib[0])
                    if vfs is not None and vfs.actions:
                        fs_tasks.append(asyncio.ensure_future(
                            self._run_vibration(vfs, clock)))
                        graph_segs += self._graph_add_track(
                            TRACK_VIBRATION, vfs, clock)

        # 音声終了を待つ
        try:
            while not self._stop_requested and self._jump is None:
                if clock is not None and is_primary:
                    self.state["elapsed_ms"] = int(clock.now_ms())
                if not py_channel.get_busy() and not self._paused:
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
            # rotate/vibrationを実行したアイテムの終了時は動作を止めておく
            # (次のアイテムが同種トラックを持たない場合の動きっぱなし防止)
            if had_rotate and not self._stop_requested:
                try:
                    await self.intiface.send_rotate(50)
                    self.state["rotate_pos"] = 50
                except Exception:
                    pass
            if had_rotate_a10 and not self._stop_requested:
                try:
                    await self.intiface.send_rotate_a10(50)
                    self.state["rotate_a10_pos"] = 50
                except Exception:
                    pass
            if had_vibration and not self._stop_requested:
                try:
                    await self.intiface.send_vibration(0)
                    self.state["vibration_pos"] = 0
                except Exception:
                    pass

        # 自然完了時のみ発火する変数操作(▶▶スキップ・選択肢確定・停止・
        # 監視(interrupt)による打ち切りでは発火しない。
        # ワインドダウン(graceful監視含む)で最後まで再生された場合は発火)
        if not self._stop_requested and self._jump is None:
            self._apply_ops(item.on_complete, tr("アイテム完了時"))

        return duration_ms

    async def _play_script_item(self, channel, item, device_types: set,
                                clock, is_primary: bool = False) -> float:
        """スクリプトのみアイテム(audioなし)を実行し、再生時間(ms)を返す。

        再生時間 = 全トラックの duration_ms の最大。=121: 区間終了(end)を
        明示したトラックは duration_ms が**区間の長さ**になる(最終アクション
        時刻ではない)ため、末尾の停止・保持区間も最後まで実行される。
        区間指定なし・終了省略のときは従来どおり最終アクション時刻まで。
        音声は再生せず、チャンネルクロックで経過を計る。担当種別
        (device_types)に含まれるトラックのみ実行する(音声アイテムと同じ規則)。

        音声アイテムとの意図的な違い(ユーザー決定):
        - ワインドダウン(イベント終了・ステート移行の成立)では最後まで
          流さず「即時中断」する(長尺スクリプトがイベント終了を遅らせない)。
        - on_complete は最後まで流れた(クロックが長さに達した)場合のみ発火。
        """
        self._log_item(channel.channel_id, item)
        # トラックの読み込みと長さの解決(種別ごとに先勝ち=音声アイテムの
        # tracks_of(...)[0] と同じ規則)
        loaded: dict = {}
        duration_ms = 0.0
        for t in item.tracks:
            if t.type in loaded:
                continue
            try:
                src = self._load_track_src(
                    item, t, rotate=t.type in (TRACK_ROTATE, TRACK_ROTATE_A10))
                if src is None:
                    continue
            except Exception as e:
                logger.error(tr("スクリプトの読み込み失敗: %s (%s)"),
                             t.funscript, e)
                continue
            loaded[t.type] = src
            duration_ms = max(duration_ms, float(src.duration_ms))
        if duration_ms <= 0:
            # 長さ0(読込失敗含む)の高速空回り防止。読み込み時検証があるため
            # 通常は起きない(再生中のファイル削除など)。1秒として扱う。
            logger.warning(tr("スクリプトの長さが0のため1秒として扱います: %s"),
                           item.tracks[0].funscript if item.tracks else "")
            duration_ms = 1000.0

        if clock is None:
            # 防御: 読み込み時検証によりスクリプト専用chは必ずデバイス担当を
            # 持つ(=クロックが渡る)が、万一に備えローカルクロックで計る。
            clock = PlaybackClock()
        clock.start()
        if self._paused:
            clock.pause()

        # 再生タブの表示(音声欄)にはスクリプトファイル名を出す
        self.state["channel_audio"][channel.channel_id] = \
            item.tracks[0].funscript if item.tracks else ""
        if is_primary:
            # =63: スクリプト専用chがシークバー追従チャンネルのときは、
            # スクリプトの長さを全長として表示・シークできるようにする
            self.state["audio_file"] = \
                item.tracks[0].funscript if item.tracks else ""
            self.state["duration_ms"] = duration_ms

        # 再生開始時の変数操作(音声アイテムと同じタイミング)
        self._apply_ops(item.on_play, tr("アイテム再生時"))

        fs_tasks = []
        graph_segs = []            # =69 グラフ表示用
        had_rotate = False
        had_rotate_a10 = False
        had_vibration = False
        for ttype, src in loaded.items():
            if not self._device_output_on():
                break        # =252: デバイス連動OFF=ランナーを起動しない
            if ttype not in device_types:
                continue
            if ttype == TRACK_LINEAR and src.actions:
                fs_tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock)))
                graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_TWIST and src.actions:
                fs_tasks.append(asyncio.ensure_future(
                    self._run_funscript(src, clock, twist=True)))
                graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_ROTATE:
                had_rotate = True
                if src.steps:
                    fs_tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "ufo")))
                    graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_ROTATE_A10:
                had_rotate_a10 = True
                if src.steps:
                    fs_tasks.append(asyncio.ensure_future(
                        self._run_rotate_timeline(src, clock, "a10")))
                    graph_segs += self._graph_add_track(ttype, src, clock)
            elif ttype == TRACK_VIBRATION and src.actions:
                had_vibration = True
                fs_tasks.append(asyncio.ensure_future(
                    self._run_vibration(src, clock)))
                graph_segs += self._graph_add_track(ttype, src, clock)

        completed = False
        try:
            while not self._stop_requested and self._jump is None:
                if self._winding_down:
                    break   # スクリプトは即時中断(ユーザー決定)
                if clock.now_ms() >= duration_ms:
                    completed = True
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
            # 実行した種別の動作を止めておく(音声アイテムと同じ後始末)
            if had_rotate and not self._stop_requested:
                try:
                    await self.intiface.send_rotate(50)
                    self.state["rotate_pos"] = 50
                except Exception:
                    pass
            if had_rotate_a10 and not self._stop_requested:
                try:
                    await self.intiface.send_rotate_a10(50)
                    self.state["rotate_a10_pos"] = 50
                except Exception:
                    pass
            if had_vibration and not self._stop_requested:
                try:
                    await self.intiface.send_vibration(0)
                    self.state["vibration_pos"] = 0
                except Exception:
                    pass

        # 最後まで流れた場合のみ on_complete を発火(ワインドダウンでの
        # 即時中断・停止・ジャンプでは発火しない=「流れ切った」セマンティクス)
        if completed and not self._stop_requested and self._jump is None:
            self._apply_ops(item.on_complete, tr("アイテム完了時"))

        return duration_ms
