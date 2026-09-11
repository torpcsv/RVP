"""メイン画面: 状態ポーリング(_poll_state)とモーションバー/シークの更新(RVPApp の mixin)。"""
from __future__ import annotations

import os
import time
from ..scenario import TRACK_ROTATE_A10
from ..i18n import tr

from .common import LABEL, MUTED, NEG_TEXT, OK_TEXT


class _RVPAppPollMixin:
    """RVPApp の mixin(=301 分割)。状態ポーリング(_poll_state)とモーションバー/シークの更新"""

    def _apply(self, widget, **kwargs):
        """値が前回から変わった項目だけ configure する(チラつき防止)。

        CTkウィジェットは configure のたびに再描画されるため、
        毎ポーリングで無条件に呼ぶとボタン等が高速で明滅する。
        """
        if not hasattr(self, "_ui_cache"):
            self._ui_cache = {}
        wid = id(widget)
        cache = self._ui_cache.setdefault(wid, {})
        changed = {k: v for k, v in kwargs.items() if cache.get(k) != v}
        if changed:
            widget.configure(**changed)
            cache.update(changed)

    def _linear_disp(self, v):
        """LINEAR表示値: 反転ONなら区間内で折り返した実位置を表す(=86)。"""
        if not self.invert_var.get():
            return v
        lo, hi = getattr(self, "_linear_zone", (0, 100))
        return lo + hi - v

    def _update_seek_slider(self, elapsed: float, duration: float):
        """シークバーの位置を反映する(ドラッグ中はユーザー操作を優先)。

        =286: 値は 1000分率の小数(number_of_steps=1000 で丸まる)。
        _poll_state(100ms)と _animate_linear(設定fps)の両方から呼ばれる。
        """
        if self._seek_dragging:
            return
        value = (elapsed / duration * 1000.0) if duration > 0 else 0.0
        value = max(0.0, min(1000.0, value))
        value = round(value, 1)
        if getattr(self, "_last_seek_value", None) != value:
            self.seek_slider.set(value)
            self._last_seek_value = value

    def _animate_linear(self):
        """LINEAR/TWISTバーを補間更新し、他の駆動値バーも同じ頻度で更新する。

        playerが記録した進行中の移動(from→to, 所要dur秒)を単純線形補間で描く。
        一時停止中もデバイスは送信済みコマンドを完遂するため、補間は継続して
        to位置で自然に停止する。反転ON時はミラー表示(実位置)になる。
        =106: 更新間隔は設定「描画更新頻度」(graph_fps。既定60fps)に従い、
        ROTATE/VIBRATIONバー(_update_motion_bars)もここから同頻度で更新する
        (従来は_poll_stateの100ms)。バー/ラベルは値が変わらない限り
        再描画しない(ZoneBar.set_value/_applyの差分ガード)ため、
        アイドル時の60fpsは実質無負荷。
        """
        st = self.player.state
        mv = st.get("linear_move")
        if mv and st["status"] in ("playing", "paused"):
            frac = (time.monotonic() - mv["t0"]) / max(mv["dur"], 0.001)
            frac = max(0.0, min(1.0, frac))
            cur = mv["from"] + (mv["to"] - mv["from"]) * frac
            self.pos_bar.set_value(round(self._linear_disp(cur), 1))
        else:
            self.pos_bar.set_value(self._linear_disp(st.get("linear_pos", 0)))
        # =79 TWISTバーも同じ補間で更新
        tmv = st.get("twist_move")
        if tmv and st["status"] in ("playing", "paused"):
            frac = (time.monotonic() - tmv["t0"]) / max(tmv["dur"], 0.001)
            frac = max(0.0, min(1.0, frac))
            cur = tmv["from"] + (tmv["to"] - tmv["from"]) * frac
            self.twist_bar.set_value(round(self._twist_disp(cur), 1))
        else:
            self.twist_bar.set_value(self._twist_disp(st.get("twist_pos", 0)))
        # =106: ROTATE(ufo/a10)・VIBRATIONのバーも同じ頻度で更新
        self._update_motion_bars()
        # =286: シークバーも同じ頻度で滑らかに動かす(ユーザー報告: 100ms刻み
        # だと丸がカクつく)。player の時計から直接読む(live_elapsed_ms)
        if st["status"] == "playing" and st["duration_ms"] > 0:
            self._update_seek_slider(self.player.live_elapsed_ms(),
                                     st["duration_ms"])
        self.root.after(self._graph_interval_ms, self._animate_linear)

    def _update_motion_bars(self):
        """ROTATE(ufo/a10)・VIBRATIONの駆動値バーと数値ラベルを更新する。

        =106で_poll_state(100ms)から切り出し、_animate_linear(設定fps)から
        呼ぶ。_poll_stateからも従来どおり呼ばれる(表示構造の変化に対する
        保険+既存テストの作法「state書き換え+_poll_state()」の維持)。
        """
        st = self.player.state

        def show_rotate(pos, speed, invert, bar, label, connected):
            """rotate系の表示。反転ON時は方向(緑⇔ピンク・符号)を入れ替える。"""
            gray = self.TRACK_DISABLED_COLOR
            if pos == 50 or speed <= 0:
                bar.set_value(None)
                self._apply(label, text=tr("停止"),
                            text_color=LABEL if connected else gray)
                return
            positive = (pos > 50)
            if invert:
                positive = not positive   # 反転=回転方向が入れ替わる(大きさ維持)
            bar.set_value(speed)
            pct = int(round(speed * 100))
            if positive:
                self._apply(label, text=f"{pct}",
                            text_color=OK_TEXT if connected else gray)
            else:
                self._apply(label, text=f"-{pct}",
                            text_color=NEG_TEXT if connected else gray)

        # rotate(ufo)表示: ufotwが居れば上下分割(左/右)、居なければ単一バー
        r_on = self._track_conn.get("rotate", False)
        if self._lane_is_split("ufo"):
            self._show_rotate_split("ufo", "rotate_ufo_ch",
                                    self.rotate_bar, self.rotate_value_label,
                                    r_on)
        else:
            rpos = st.get("rotate_pos", 50)
            speed, _cw = self.intiface.map_rotate_speed(rpos)
            show_rotate(rpos, speed, self.rotate_invert_var.get(),
                        self.rotate_bar, self.rotate_value_label, r_on)

        # rotate(a10cyclonesa)表示: 同様(ufotwを付け替えた場合も分割対応)
        a_on = self._track_conn.get(TRACK_ROTATE_A10, False)
        if self._lane_is_split("a10"):
            self._show_rotate_split("a10", "rotate_a10_ch",
                                    self.a10_bar, self.a10_value_label, a_on)
        else:
            apos = st.get("rotate_a10_pos", 50)
            aspeed, _acw = self.intiface.map_rotate_a10_speed(apos)
            show_rotate(apos, aspeed, self.a10_invert_var.get(),
                        self.a10_bar, self.a10_value_label, a_on)

        # vibration表示: バー + 実出力強度(強度レンジ適用後)の数値
        vib_on = self._track_conn.get("vibration", False)
        vpos = st.get("vibration_pos", 0)
        vspeed = self.intiface.map_vibration_speed(vpos)
        if vpos <= 0 or vspeed <= 0:
            self.vibration_bar.set_value(None)
            self._apply(self.vibration_value_label, text=tr("停止"),
                        text_color=LABEL if vib_on
                        else self.TRACK_DISABLED_COLOR)
        else:
            self.vibration_bar.set_value(vspeed)
            self._apply(self.vibration_value_label,
                        text=f"{int(round(vspeed * 100))}",
                        text_color=OK_TEXT if vib_on
                        else self.TRACK_DISABLED_COLOR)

    def _poll_state(self):
        # 接続ロストの検知と自動再接続(音声再生には影響しない)
        self._check_auto_connect()
        # =272: 接続状況(Intiface/COM/デバイス増減)が変わったら
        # 「接続中デバイス」欄を描き直す(変化がなければ何もしない)
        self._refresh_device_box()

        st = self.player.state
        status = st["status"]

        text, color = self.STATUS_TEXT.get(status, (status, MUTED))
        self._apply(self.status_label, text=text, text_color=color)

        # 再生中のチャンネル表示(=68でチャンネルごとの行に変更)。
        # 動画ch(=52)は「▶動画」、シークバーが追従しているchは「★」を付ける。
        ch_audio = st.get("channel_audio", {})
        vch = st.get("video_channel") or ""
        dev_ch = st.get("seek_channel") or st.get("device_channel", "")
        for ch_id in ("L", "C", "R"):
            raw = ch_audio.get(ch_id) or ""
            if not ch_audio and ch_id == dev_ch and st.get("audio_file"):
                # 旧経路の保険(ch別が1つも無いときだけ代表を出す)。=286: ch別が
                # あるときは終わったchを「—」に戻したいので代表で埋めない
                raw = st["audio_file"]
            marks = ("★" if raw and ch_id == dev_ch else "") + \
                    (tr("▶動画") if raw and ch_id == vch else "")
            self._apply(self.ch_mark_labels[ch_id], text=marks)
            self._apply(self.ch_name_labels[ch_id],
                        text=os.path.basename(raw) if raw else "—",
                        text_color=LABEL if raw else MUTED)
        def fmt_mmss(ms):
            s = ms / 1000
            return f"{int(s // 60):02d}:{int(s % 60):02d}"

        ev_id = st['event_id'] or '-'
        ev_text = tr('イベント: {0}').format(ev_id)
        if st['event_id']:
            ev_text += tr('  ／  経過 {0}').format(fmt_mmss(st['event_elapsed_ms']))
        self._apply(self.event_label, text=ev_text)

        if st.get("state_id"):
            state_text = (tr('ステート: {0}  ／  経過 {1}').format(st['state_id'], fmt_mmss(st.get('state_elapsed_ms', 0))))
        else:
            state_text = tr("ステート: -")
        self._apply(self.state_label, text=state_text)

        # (=289: ①の変数行は廃止。変数は③のページで表示する)

        def fmt(ms):
            s = ms / 1000
            return f"{int(s // 60):02d}:{s % 60:04.1f}"

        elapsed = st["elapsed_ms"]
        duration = st["duration_ms"]
        self._apply(self.time_label, text=f"{fmt(elapsed)} / {fmt(duration)}")

        # シークバー(ドラッグ中はユーザー操作を優先して上書きしない)
        seekable = status in ("playing", "paused") and duration > 0
        self._apply(self.seek_slider, state="normal" if seekable else "disabled")
        # 10秒送り/戻しボタンはシーク可能なときだけ有効
        seek_state = "normal" if seekable else "disabled"
        self._apply(self.btn_seek_back, state=seek_state)
        self._apply(self.btn_seek_fwd, state=seek_state)
        self._update_seek_slider(elapsed, duration)

        # linear: バーの塗りは_animate_linear(設定fps=106)が補間更新する。
        # ラベルは補正後の指示値「from→to」を緑で表示(停止中は非表示)
        # トラックの接続状況(並び替え・灰色表示)を反映
        self._update_track_conn()
        # 回転デバイスの増減を割り当てUIへ反映(集合が変わった時だけ再構築)
        self._refresh_rotate_assign_ui()
        lin_on = self._track_conn.get("linear", False)

        mv = st.get("linear_move")
        if mv and status in ("playing", "paused"):
            # 反転ON時はミラー表示(デバイス実位置)
            d_from = int(round(self._linear_disp(mv['from'])))
            d_to = int(round(self._linear_disp(mv['to'])))
            self._apply(self.pos_value_label,
                        text=f"{d_from}→{d_to}",
                        text_color=OK_TEXT if lin_on
                        else self.TRACK_DISABLED_COLOR)
        else:
            self._apply(self.pos_value_label, text="")
        # =79 TWIST: バーは_animate_linear(設定fps)が補間更新。ラベルはlinearと同型
        tmv = st.get("twist_move")
        if tmv and status in ("playing", "paused"):
            t_on = self._track_conn.get("twist", False)
            d_from = int(round(self._twist_disp(tmv['from'])))
            d_to = int(round(self._twist_disp(tmv['to'])))
            self._apply(self.twist_value_label,
                        text=f"{d_from}→{d_to}",
                        text_color=OK_TEXT if t_on
                        else self.TRACK_DISABLED_COLOR)
        else:
            self._apply(self.twist_value_label, text="")
        self._apply(self.msg_label, text=st["message"])

        # 選択肢カード
        ch = st.get("choice")
        if ch and status in ("playing", "paused"):
            sig = (ch.get("event_id"), tuple(ch.get("labels") or ()))
            if sig != self._choice_sig:
                self._choice_sig = sig
                self._choice_display = "active"
                self._show_choice_card(list(sig[1]))
                self._schedule_autoselect(sig, len(sig[1]))
            rem = st.get("choice_remaining_ms")
            if rem is not None:
                s = max(0, rem) / 1000
                self._apply(self.choice_timer_label,
                            text=tr("残り {0}:{1:02d}").format(int(s // 60), int(s % 60)))
            else:
                self._apply(self.choice_timer_label, text="")
        else:
            # 選択肢が非アクティブ: 選択肢ありシナリオなら■■■を常時表示、無ければ隠す
            if self._choice_sig is not None:
                self._choice_sig = None
                self._cancel_autoselect()
            want = "placeholder" if self._scenario_has_choices else "hidden"
            if self._choice_display != want:
                self._choice_display = want
                if want == "placeholder":
                    self._show_choice_placeholder()
                else:
                    self._hide_choice_card()

        # 数値入力カード
        inp = st.get("input")
        if inp and status in ("playing", "paused"):
            sig = (inp.get("event_id"), inp.get("var"))
            if sig != self._input_sig:
                self._input_sig = sig
                self._show_input_card(inp)
        elif self._input_sig is not None:
            self._input_sig = None
            self._hide_input_card()

        # 動画イベント中に選択肢/入力カードが出たらRVPを最前面へ出す
        # (フルスクリーン動画の上でも操作できるように。解決したら解除する。
        #  フェーズ2=ユーザー決定。動画のないイベントでは何もしない)
        front = (bool(st.get("video_file"))
                 and status in ("playing", "paused")
                 and (self._choice_sig is not None
                      or self._input_sig is not None))
        if front != self._video_front:
            self._video_front = front
            try:
                self.root.attributes("-topmost", front)
                if front:
                    self.root.lift()
            except Exception:
                pass

        # ufotwが割り当たったレーンは左右反転チェックの表示を更新する
        self._update_swap_visibility()

        # =106: 駆動値バー本体の更新は _update_motion_bars へ切り出し、
        # _animate_linear(設定fps)からも呼ばれる。ここでも呼ぶのは
        # 表示構造の変化(接続・分割)への保険+既存テストの作法の維持。
        self._update_motion_bars()

        active = status in ("playing", "paused")
        if status == "playing":
            self._apply(self.btn_play, text="❚❚", state="normal")
        elif status == "paused":
            self._apply(self.btn_play, text="▶", state="normal")
        else:
            self._apply(self.btn_play, text="▶",
                        state="normal" if self.scenario else "disabled")
        self._apply(self.btn_skip, state="normal" if active else "disabled")
        self._apply(self.btn_back, state="normal" if active else "disabled")

        # ②イベント状態ビュー/③変数・イベントログ(表示中のみ、差分更新)
        self._update_event_map()
        self._update_run_log()

        self.root.after(100, self._poll_state)
