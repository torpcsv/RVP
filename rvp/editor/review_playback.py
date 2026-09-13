"""アイテムレビュー: 再生・一時停止・シーク・速度・動画(mpv)同期・時間表示(mixin)。"""
from __future__ import annotations

import time
from ..i18n import load_config, tr

from .review_support import REVIEW_SLOT, _hold_editor_mpv
from ._hooks import _pkg


class _ItemReviewPlaybackMixin:
    """ItemReviewDialog の mixin(=301 分割)。再生・一時停止・シーク・速度・動画(mpv)同期・時間表示"""

    def _video_offset(self) -> float:
        """RVPの0秒に対応する動画側の位置(ms)。レビュー=区間の開始、
        編集モード=素材全体(0)。"""
        return 0.0 if self.edit_mode else self._video_lo

    def _refresh_vol_visibility(self):
        """動画モードでは音量スライダーを隠す(mpv の音声には触れない=
        合意事項4。音量は mpv 側で操作してもらう)。"""
        try:
            if self._video:
                self.vol_box.pack_forget()
            elif not self.vol_box.winfo_ismapped():
                # =214: 速度コンボの左へ戻す(pack 順を保つ)
                self.vol_box.pack(side="left", padx=(14, 0),
                                  before=self.speed_box)
        except Exception:
            pass

    def _video_open(self, seek_ms: float) -> str:
        """mpv を用意して動画を開く。失敗理由の文字列を返す(成功="")。"""
        mpv_path = _pkg()._mpv_path_setting()
        if not mpv_path:
            return tr("mpv が見つからないため、動画は再生できません"
                      "（メイン画面の Settings でパスを指定してください）")
        holder = self.owner if self.owner is not None else self
        try:
            self._mpv = _hold_editor_mpv(holder, mpv_path)
            self._mpv.open(self._video, max(0.0, seek_ms) / 1000.0)
            if abs(self._speed - 1.0) > 1e-9:
                self._mpv.set_speed(self._speed)      # =214
            self._mpv_cmd_t = time.monotonic()
        except Exception as exc:
            self._mpv = None
            return tr("mpv を起動できませんでした: {0}").format(exc)
        return ""

    def _apply_video_duration(self):
        """mpv 実測の全長を全長へ反映する(区間・スクリプトと合成)。"""
        vl = self._video_len
        if vl <= 0:
            return
        if self.edit_mode:
            self._edit_refresh_duration()
        else:
            lo, hi = self._video_lo, self._video_hi
            end = min(float(hi), vl) if hi is not None else vl
            vd = max(0.0, end - lo)
            if lo >= vl:
                self.warn_label.configure(
                    text=tr("区間の開始が動画の長さを超えています"))
                self.warn_label.pack(fill="x", padx=14, pady=(4, 0),
                                     before=self.transport_card)
            self._duration_ms = int(max(vd, self._script_ms))
            for seg in self._segments:
                seg["x1"] = float(self._duration_ms)
            self.info_label.configure(text=self._info_text())
        self._update_time(self._now_ms(), force=True)
        self._sync_buttons()

    def _video_tick(self):
        """毎フレームの mpv 同期(=203)。再生タブと同じ考え方:
        ①実長の反映 ②クロックを time-pos へ従属(50ms超のみ補正)
        ③mpv 側の一時停止/再開の取り込み ④切断=一時停止扱い。"""
        m = self._mpv
        if m is None:
            return
        st = m.state
        d = st.get("duration")
        if d and abs(d * 1000.0 - self._video_len) > 1.0:
            self._video_len = d * 1000.0
            self._apply_video_duration()
        tp = st.get("time_pos")
        if tp is not None and self._clock is not None and self._playing:
            ms = tp * 1000.0 - self._video_offset()
            ms = max(0.0, min(float(ms), float(self._duration_ms)))
            if abs(ms - self._clock.now_ms()) > 50:
                self._clock.set_ms(ms)
        own_recent = (time.monotonic() - self._mpv_cmd_t) <= 0.6
        pv = st.get("pause")
        if pv is not None and st.get("connected") and not own_recent:
            if pv and self._playing:
                # mpv 側で一時停止された
                self._playing = False
                self._clock.pause()
                if self.edit_mode and self._edit_built:
                    self.edit_graph.set_playing(False)
                self._sync_buttons()
            elif (not pv) and (not self._playing) and                     self._now_ms() < self._duration_ms - 1:
                # mpv 側で再開された
                self._playing = True
                self._clock.resume()
                if self.edit_mode and self._edit_built:
                    self.edit_graph.follow = True
                    self.edit_graph.set_playing(True)
                self._sync_buttons()
        if not st.get("connected") and self._playing:
            # mpv が閉じられた/落ちた: 一時停止扱い(次の▶で開き直す)
            self._playing = False
            self._clock.pause()
            if self.edit_mode and self._edit_built:
                self.edit_graph.set_playing(False)
            self._sync_buttons()

    def _reset_clock(self):
        from ..player import PlaybackClock
        self._clock = PlaybackClock()
        self._clock.start()
        self._clock.pause()          # 0秒で停止した状態から始める
        self._clock.set_rate(getattr(self, "_speed", 1.0))   # =214

    def _now_ms(self) -> float:
        if self._clock is None:
            return 0.0
        return max(0.0, min(float(self._duration_ms), self._clock.now_ms()))

    def _on_space_key(self, _event=None):
        """=231: スペース=再生/一時停止。入力欄では普通の空白を入れる。"""
        if self._key_target_is_entry():
            return None
        if self._duration_ms <= 0:
            return "break"
        self.toggle_play()
        return "break"

    def _on_seek_key(self, event, delta_ms: int):
        """=313: Q/E キー=10秒戻る/進む。入力欄では普通の文字を入れる。"""
        if self._key_target_is_entry():
            return None
        state = int(getattr(event, "state", 0) or 0)
        # Ctrl(0x4)/Alt(Windows=0x20000)併用は何もしない。**0x8 は見ない**
        # (Windows では NumLock ON のビット=テンキー打点中は常に立っている。
        # v311 の実機で Q/E が効かなかった原因)
        if state & 0x4 or state & 0x20000:
            return None
        if self._duration_ms <= 0:
            return "break"
        self.seek(self._now_ms() + delta_ms)
        return "break"

    def toggle_play(self):
        if self._playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self._duration_ms <= 0:
            return
        self._pause_main()
        # 末尾まで来ていたら頭から鳴らし直す(再生タブの「停止後に▶」と同じ)
        if self._now_ms() >= self._duration_ms - 1:
            self._clock.set_ms(0.0)
            if self._video and self._mpv is not None:
                self._mpv.seek(self._video_offset() / 1000.0)
        if self._video:
            # =203: mpv が閉じられていたら開き直してから再生する
            if self._mpv is None or not self._mpv.state.get("connected"):
                err = self._video_open(self._video_offset() + self._now_ms())
                if err:
                    self.warn_label.configure(text=err)
                    self.warn_label.pack(fill="x", padx=14, pady=(4, 0),
                                         before=self.transport_card)
                    return
            self._mpv.set_pause(False)
            self._mpv_cmd_t = time.monotonic()
        self._clock.resume()
        self._playing = True
        # =170: 編集モードでは単クリックが打点になり「クリックで追従復帰」が
        # 使えないため、再生ボタンで自動追従ONへ戻す(仕様 8b)。
        if self.edit_mode and self._edit_built:
            self.edit_graph.follow = True
            self.edit_graph.set_playing(True)   # =198: 再生位置を中央へ
        self._play_audio_from(self._now_ms())
        self._sync_buttons()
        self._schedule_tick()

    def pause(self):
        if self._clock is not None:
            self._clock.pause()
        self._playing = False
        if self._video and self._mpv is not None:
            self._mpv.set_pause(True)
            self._mpv_cmd_t = time.monotonic()
        if self.edit_mode and self._edit_built:
            self.edit_graph.set_playing(False)
        self._pause_audio()
        self._sync_buttons()

    def seek_back10(self):
        self.seek(self._now_ms() - 10_000)

    def seek_fwd10(self):
        self.seek(self._now_ms() + 10_000)

    def seek(self, ms: float):
        """再生位置を移動する(音声・グラフとも同じ時間軸で動く)。"""
        if self._duration_ms <= 0 or self._clock is None:
            return
        ms = max(0.0, min(float(ms), max(self._duration_ms - 200, 0)))
        self._clock.set_ms(ms)
        if self._video:
            if self._mpv is not None:
                self._mpv.seek((self._video_offset() + ms) / 1000.0)
                self._mpv_cmd_t = time.monotonic()
        elif self._playing:
            self._play_audio_from(ms)
        else:
            self._stop_audio()
        self._update_time(ms, force=True)

    def _channel(self):
        try:
            import pygame
            if not pygame.mixer.get_init():
                return None
            return pygame.mixer.Channel(REVIEW_SLOT)
        except Exception:
            return None

    def _play_audio_from(self, ms: float):
        """指定位置から鳴らす(区間切り出し済みSoundのさらに途中から)。
        =203: 動画モードでは何もしない(音は mpv 側)。"""
        if self._video or self._sound is None:
            return
        ch = self._channel()
        if ch is None:
            return
        try:
            from ..player import ScenarioPlayer as _SP
            # =214: 速度 r のときは r 倍済み Sound の ms/r 位置から鳴らす
            src = self._rated_sound()
            ms_r = ms / self._speed if self._speed > 0 else ms
            snd = src if ms_r <= 0 else _SP._sliced_sound(src, ms_r)
            if snd is None:
                return
            ch.play(snd)
            ch.set_volume(self._volume, self._volume)
        except Exception:
            pass

    def _pause_audio(self):
        ch = self._channel()
        if ch is not None:
            try:
                ch.pause()
            except Exception:
                pass

    def _stop_audio(self):
        ch = self._channel()
        if ch is not None:
            try:
                ch.stop()
            except Exception:
                pass

    @staticmethod
    def _speed_label(v: float) -> str:
        return "x{0:.1f}".format(v)

    def set_speed(self, rate: float):
        """再生速度を変える(=214)。クロックの rate・音声の鳴らし直し・
        mpv の speed。now_ms は素材上の時刻のまま(Fキー配置に影響なし)。"""
        rate = float(rate)
        if rate <= 0:
            rate = 1.0
        if abs(rate - self._speed) < 1e-9:
            return
        self._speed = rate
        if self._clock is not None:
            self._clock.set_rate(rate)
        if self._video:
            if self._mpv is not None:
                try:
                    self._mpv.set_speed(rate)
                except Exception:
                    pass
        elif self._playing:
            self._play_audio_from(self._now_ms())

    def _on_speed_change(self):
        self.set_speed(self._speed_map.get(self.speed_var.get(), 1.0))

    def _rated_sound(self):
        """今の速度用の Sound(=214。rate=1.0 は原本)。キャッシュ1つ。"""
        if self._sound is None:
            return None
        if abs(self._speed - 1.0) < 1e-9:
            return self._sound
        c = self._rate_cache
        if c and c[0] == id(self._sound) and abs(c[1] - self._speed) < 1e-9:
            return c[2]
        try:
            from ..player import ScenarioPlayer as _SP
            snd = _SP._rated_sound(self._sound, self._speed)
        except Exception:
            snd = None
        if snd is None:
            return self._sound
        self._rate_cache = (id(self._sound), self._speed, snd)
        return snd

    def _on_volume_change(self, value):
        self._volume = max(0.0, min(1.0, float(value) / 100.0))
        ch = self._channel()
        if ch is not None:
            try:
                ch.set_volume(self._volume, self._volume)
            except Exception:
                pass

    def _on_seek_press(self, _event):
        if self._duration_ms > 0:
            self._dragging = True

    def _on_seek_release(self, _event):
        if not self._dragging:
            return
        self._dragging = False
        self.seek(self.seek_slider.get() / 1000.0 * self._duration_ms)

    def _sync_buttons(self):
        active = self._duration_ms > 0
        state = "normal" if active else "disabled"
        self.btn_play.configure(text="❚❚" if self._playing else "▶",
                                state=state)
        for b in (self.btn_back10, self.btn_fwd10):
            b.configure(state=state)
        self.seek_slider.configure(state=state)

    @staticmethod
    def _fmt_ms(ms: float) -> str:
        s = max(0.0, ms) / 1000.0
        return "{0:02d}:{1:04.1f}".format(int(s // 60), s % 60)

    def _update_time(self, now_ms: float, force: bool = False):
        # =229: 60fps では 1/10 秒表示のラベルは同じ文字のことが多い。
        # 変わったときだけ configure する(CTkLabel の再設定は重い)。
        txt = "{0} / {1}".format(self._fmt_ms(now_ms),
                                 self._fmt_ms(self._duration_ms))
        if txt != self._time_text:
            self._time_text = txt
            self.time_label.configure(text=txt)
        if not self._dragging:
            pos = 0.0
            if self._duration_ms > 0:
                pos = min(1000.0, now_ms / self._duration_ms * 1000.0)
            # =229: CTkSlider.set() は毎回スライダーを描き直す。目盛は
            # 1000段なので、**1段ぶん動いたときだけ**動かす(60fps 対策)。
            if self._seek_step != int(pos):
                self._seek_step = int(pos)
                self.seek_slider.set(pos)
        if force or now_ms != self._last_draw:
            self._last_draw = now_ms
            if self.edit_mode and self._edit_built:
                self.edit_graph.set_now(now_ms)
            elif self._segments:
                self.graph_view.set_snapshot({"now_ms": now_ms,
                                              "segments": self._segments})

    @classmethod
    def _load_refresh_ms(cls) -> int:
        """=229: 設定「描画更新頻度」(graph_fps)から更新間隔[ms]を決める。

        再生タブ(`RVPApp._graph_interval_ms`)と同じ値・同じ既定(60fps)。
        """
        try:
            gfps = load_config().get("graph_fps")
        except Exception:
            gfps = None
        return 33 if gfps == 30 else 16

    def _schedule_tick(self):
        if self._tick_job is None:
            # =229: 待ちの決め方は再生タブと共通(main.paced_delay)。
            # フレーム開始基準で設定どおりの fps を狙いつつ、重い描画の
            # ときは待ちを描画時間以上にして操作の応答を守る。
            from ..main import paced_delay
            delay = paced_delay(self.refresh_ms, self._draw_cost_ms)
            try:
                self._tick_job = self.after(delay, self._tick)
            except Exception:
                self._tick_job = None

    def _cancel_tick(self):
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _tick(self):
        self._tick_job = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        _t0 = time.perf_counter()
        if self._video:
            self._video_tick()            # =203: mpv 同期
        now = self._now_ms()
        if self._playing and self._duration_ms > 0 \
                and now >= self._duration_ms:
            # 末尾に到達: 止めて終端で待たせる(再生タブと同じ「残す」動作)
            self._playing = False
            self._clock.pause()
            self._clock.set_ms(self._duration_ms)
            self._stop_audio()
            if self._video and self._mpv is not None:
                self._mpv.set_pause(True)     # =203: 区間の終端で mpv も停止
                self._mpv_cmd_t = time.monotonic()
            if self.edit_mode and self._edit_built:
                self.edit_graph.set_playing(False)
            self._sync_buttons()
            now = float(self._duration_ms)
        self._update_time(now)
        self._draw_cost_ms = (time.perf_counter() - _t0) * 1000.0
        self._schedule_tick()
