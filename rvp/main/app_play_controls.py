"""メイン画面: 再生操作(再生/シーク/区間/反転/オフセット/トラック接続表示)(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from ..scenario import TRACK_ROTATE_A10
from ..i18n import tr

from .common import LABEL
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _RVPAppPlayControlsMixin:
    """RVPApp の mixin(=301 分割)。再生操作(再生/シーク/区間/反転/オフセット/トラック接続表示)"""

    def on_play_pause(self):
        """再生⇔一時停止のトグル。停止/終了後は最初から再生し直す。"""
        status = self.player.state["status"]
        if status == "playing":
            self.runner.submit(self.player.pause())
        elif status == "paused":
            self.runner.submit(self.player.resume())
        else:
            if self.scenario and not self.player.is_playing:
                self.runner.submit(self.player.play(self.scenario))

    def on_seek_back10(self):
        """再生中の音声を10秒巻き戻す(先頭より前は0にクランプ)。"""
        self.runner.submit(self.player.seek_relative(-10_000))

    def on_seek_fwd10(self):
        """再生中の音声を10秒早送りする(末尾付近にクランプ)。"""
        self.runner.submit(self.player.seek_relative(10_000))

    def _on_volume_change(self, value):
        """音量スライダー(0=消音〜100=最大)。再生中にも即時反映される。"""
        self.player.set_master_volume(float(value) / 100.0)

    def _speed_limit_labels(self) -> dict:
        return {tr("なし"): "none", tr("弱"): "low",
                tr("中"): "mid", tr("強"): "high"}

    def _on_speed_limit_change(self):
        """linear速度制限の変更を即時反映する(再生中も有効)。"""
        key = self._speed_limit_labels().get(self.speed_limit_var.get(), "mid")
        self.player.linear_speed_limit_ms = self.SPEED_LIMIT_MS[key]

    def on_skip_event(self):
        self.runner.submit(self.player.skip_event())

    def on_back_event(self):
        self.runner.submit(self.player.back_event())

    def _on_graph_seek(self, ms: float):
        """=237: ③グラフの右ダブルクリック=そこへ再生位置を移す。

        **相対シークで実現する**(ユーザー決定)。グラフの横軸は「イベント
        入場からの経過時間」、シークバーは「シークバー追従チャンネルの経過
        時間」で**原点が違う**ため、絶対値では合わせられない。今の再生位置
        (グラフの縦線=`snapshot["now_ms"]`)との差分だけ ↺10/↻10 と同じ
        `seek_relative` を投げると、**クリックした波形の点がちょうど縦線の
        位置まで来る**(断片の描画位置 t0 が同じ量だけ逆へ動くため)。

        停止中は何も起きない(`seek_relative` が playing/paused 以外を弾く)。
        """
        if self.player.state["status"] not in ("playing", "paused"):
            return
        now = float(self.graph_view.snapshot.get("now_ms", 0.0))
        delta = int(round(float(ms) - now))
        if delta == 0:
            return
        self.runner.submit(self.player.seek_relative(delta))

    def _on_seek_press(self, _event):
        if self.player.state["status"] in ("playing", "paused"):
            self._seek_dragging = True

    def _on_seek_release(self, _event):
        if not self._seek_dragging:
            return
        self._seek_dragging = False
        duration = self.player.state.get("duration_ms", 0)
        if duration > 0 and self.player.state["status"] in ("playing", "paused"):
            ms = int(self.seek_slider.get() / 1000 * duration)
            self.runner.submit(self.player.seek(ms))

    def _on_range_change(self, rmin, rmax):
        """駆動区間スライダーの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_linear_range(rmin, rmax)
        self._apply(self.range_label, text=tr('区間補正{0}～{1}').format(rmin, rmax))
        self._linear_zone = (int(rmin), int(rmax))
        self._update_linear_bar_range()
        self._request_linear_retarget()

    def _request_linear_retarget(self):
        """区間変更をデバイスへ反映する(間引きつき)。"""
        if not self._linear_retarget_allowed():
            return
        if getattr(self, "_retarget_job", None) is not None:
            return          # 予約済み。最後の値はタイマー発火時に読む
        self._retarget_job = self.root.after(self.RETARGET_MS,
                                             self._fire_linear_retarget)

    def _linear_retarget_allowed(self) -> bool:
        """停止中(=再生していない)かつ接続済みのときだけ追従させる。

        =80: TCode直結中も追従の対象(送信先の判断はplayer側)。
        """
        if not (self.intiface.connected or self.tcode.connected):
            return False
        return self.player.state.get("status") not in ("playing", "paused")

    def _fire_linear_retarget(self):
        self._retarget_job = None
        if not self._linear_retarget_allowed():
            return
        try:
            self.runner.submit(self.player.retarget_linear())
        except Exception:
            pass

    def _update_linear_bar_range(self):
        """LINEARバーの動作可能域を更新する。

        =86: 反転は区間の入れ替え(区間内の折り返し)なので、動作可能域は
        反転ON/OFFで変わらない(旧仕様では100-xミラーで域も移動していた)。
        """
        lo, hi = getattr(self, "_linear_zone", (0, 100))
        self.pos_bar.set_range(lo, hi)

    def _on_invert_change(self):
        self.intiface.invert = self.invert_var.get()
        # バーの動作可能域・現在位置表示もミラーする(実位置表示)
        self._update_linear_bar_range()

    def _on_twist_range_change(self, rmin, rmax):
        """TWIST駆動区間スライダーの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_twist_range(rmin, rmax)
        self._apply(self.twist_range_label,
                    text=tr('区間補正{0}～{1}').format(rmin, rmax))
        self._twist_zone = (int(rmin), int(rmax))
        self._update_twist_bar_range()
        self._request_twist_retarget()

    def _request_twist_retarget(self):
        """TWIST区間変更をデバイスへ反映する(=71と同じ間引き)。"""
        if not self._linear_retarget_allowed():
            return
        if getattr(self, "_twist_retarget_job", None) is not None:
            return
        self._twist_retarget_job = self.root.after(
            self.RETARGET_MS, self._fire_twist_retarget)

    def _fire_twist_retarget(self):
        self._twist_retarget_job = None
        if not self._linear_retarget_allowed():
            return
        try:
            self.runner.submit(self.player.retarget_twist())
        except Exception:
            pass

    def _update_twist_bar_range(self):
        """TWISTバーの動作可能域を更新する(=86: 反転で域は変わらない)。"""
        lo, hi = getattr(self, "_twist_zone", (0, 100))
        self.twist_bar.set_range(lo, hi)

    def _twist_disp(self, v):
        """TWIST表示値: 反転ONなら区間内で折り返した実位置を表す(=86)。"""
        if not self.twist_invert_var.get():
            return v
        lo, hi = getattr(self, "_twist_zone", (0, 100))
        return lo + hi - v

    def _on_twist_invert_change(self):
        self.intiface.twist_invert = self.twist_invert_var.get()
        self._update_twist_bar_range()

    def _on_rotate_invert_change(self):
        self.intiface.rotate_invert = self.rotate_invert_var.get()

    def _build_offset_cell(self, parent, dtype: str) -> ctk.CTkFrame:
        """スライダー行の右側に置く「動作タイミング」入力セルを作る。"""
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(cell, text=tr("動作タイミング"),
                     font=ctk.CTkFont(size=11), text_color=LABEL,
                     ).pack(side="left", padx=(0, 4))
        var = tk.StringVar(value="0.0")
        self.offset_vars[dtype] = var
        entry = ctk.CTkEntry(cell, textvariable=var, width=52, height=26,
                             justify="right")
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e, d=dtype: self._commit_offset(d))
        entry.bind("<FocusOut>", lambda _e, d=dtype: self._commit_offset(d))
        btns = ctk.CTkFrame(cell, fg_color="transparent")
        btns.pack(side="left", padx=(2, 0))
        ctk.CTkButton(btns, text="▲", width=22, height=13,
                      font=ctk.CTkFont(size=8),
                      fg_color=("gray75", "gray30"),
                      hover_color=("gray70", "gray35"),
                      command=lambda d=dtype: self._step_offset(d, +0.1),
                      ).pack()
        ctk.CTkButton(btns, text="▼", width=22, height=13,
                      font=ctk.CTkFont(size=8),
                      fg_color=("gray75", "gray30"),
                      hover_color=("gray70", "gray35"),
                      command=lambda d=dtype: self._step_offset(d, -0.1),
                      ).pack(pady=(1, 0))
        return cell

    def _commit_offset(self, dtype: str):
        """入力値を検証・クランプしてプレーヤーへ反映する。"""
        raw = self.offset_vars[dtype].get().replace("＋", "+").replace("−", "-")
        try:
            v = float(raw)
        except ValueError:
            v = self.player.offsets_ms[dtype] / 1000.0
        v = max(-2.0, min(2.0, round(v * 10) / 10))
        self.offset_vars[dtype].set(f"{v:+.1f}" if v else "0.0")
        self.player.set_offset(dtype, v)

    def _step_offset(self, dtype: str, delta: float):
        try:
            v = float(self.offset_vars[dtype].get().replace("＋", "+")
                      .replace("−", "-"))
        except ValueError:
            v = self.player.offsets_ms[dtype] / 1000.0
        self.offset_vars[dtype].set(f"{v + delta:.1f}")
        self._commit_offset(dtype)

    def _grid_track_groups(self, order):
        """トラックのグループを指定順に配置し直す(各グループ3行)。

        =79: order に含まれないグループ(TWIST非表示時)は grid から外す。
        """
        for key, g in self.track_groups.items():
            if key in order:
                continue
            for w in ("head", "slider", "invert", "offset", "bar", "value"):
                if g[w] is not None:
                    g[w].grid_forget()
        # =79: TWIST表示時は5グループになり従来の行間でははみ出すため、
        # 5グループ以上のときだけ縦の余白を詰める(4グループ以下は従来どおり)。
        # =88: TWIST常時表示化で5グループが恒常になったため row_pad をさらに
        # 1px詰めた(2→1)。=79時点の実測でも5グループは7pxはみ出しており、
        # これで690x820に5グループが収まる(test_play_layoutで検証)。
        compact = len(order) >= 5
        head_pad = 5 if compact else 12
        row_pad = 2 if compact else 4
        for idx, key in enumerate(order):
            g = self.track_groups[key]
            base = idx * 3
            g["head"].grid(row=base, column=0, columnspan=3, sticky="ew",
                           pady=(head_pad if idx else 0, 0))
            g["slider"].grid(row=base + 1, column=0, sticky="ew",
                             pady=(row_pad, 0))
            if g["invert"] is not None:
                g["invert"].grid(row=base + 1, column=1, sticky="w",
                                 padx=(10, 0))
            g["offset"].grid(row=base + 1, column=2, sticky="w", padx=(10, 0))
            g["bar"].grid(row=base + 2, column=0, sticky="ew",
                          pady=(row_pad, 0))
            g["value"].grid(row=base + 2, column=1, sticky="w", padx=(10, 0))
        self._track_order = list(order)

    def _set_widget_tree_state(self, widget, state: str):
        for ch in widget.winfo_children():
            if isinstance(ch, (ctk.CTkEntry, ctk.CTkButton, ctk.CTkOptionMenu)):
                try:
                    ch.configure(state=state)
                except Exception:
                    pass
            self._set_widget_tree_state(ch, state)

    def _update_track_conn(self):
        """トラックの接続状況を反映する。

        並び順: 接続デバイスあり→なし、同順位は
        linear→rotate→rotate(a10cyclonesa)→vibration。
        未接続トラックは全体を灰色表示にし、操作も無効化する
        (デバイスへ機能することはないことを表す)。
        """
        iface = self.intiface
        tcode_on = self.tcode.connected   # =80: TCode直結はL0/R0=linear/twist
        conn = {"linear": iface.has_linear or tcode_on,
                "twist": bool(getattr(iface, "has_twist", False)) or tcode_on,
                "rotate": iface.has_rotate,
                TRACK_ROTATE_A10: iface.has_rotate_a10,
                "vibration": iface.has_vibrate}
        # =88: TWIST欄は常時表示(=79のサブ機能スイッチは廃止)
        shown = tuple(self.TRACK_ORDER)
        if conn == self._track_conn and shown == self._track_shown:
            return
        self._track_conn = conn
        self._track_shown = shown
        order = sorted(shown,
                       key=lambda k: (not conn[k],
                                      self.TRACK_ORDER.index(k)))
        self._grid_track_groups(order)
        for key, g in self.track_groups.items():
            on = conn[key]
            # 見出しの文字色は接続状況を表す(未接続=灰)。ただし操作は接続の
            # 有無に関わらず常に可能にする。デバイス未所持のユーザーでも、
            # 補正スライダー・反転・左右反転・動作タイミング・速度制限を触って
            # 動作イメージをプレビューできるようにするため。
            g["title"].configure(
                text_color=LABEL if on else self.TRACK_DISABLED_COLOR)
            g["head_value"].configure(
                text_color=_clr.ACCENT_TEXT if on else self.TRACK_DISABLED_COLOR)
            # 補正スライダーは接続状況を色で表す(未接続=灰色)。ただし操作は
            # 常に可能(未接続でも灰色のままドラッグ調整できる=プレビュー用)。
            g["slider"].set_enabled(on)
            # バーは常に動作を描く。色だけ接続状況で切替(未接続=灰系)。
            g["bar"].set_enabled(on)
            if g["invert"] is not None:
                g["invert"].configure(state="normal")
            self._set_widget_tree_state(g["offset"], "normal")
            self._set_widget_tree_state(g["head"], "normal")

    def _on_rotate_range_change(self, rmin, rmax):
        """回転強度レンジの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_rotate_range(rmin, rmax)
        self._apply(self.rotate_scale_label, text=tr('出力補正{0}～{1}%').format(rmin, rmax))
        self.rotate_bar.set_range(rmin, rmax)

    def _on_a10_range_change(self, rmin, rmax):
        """rotate(A10)強度レンジの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_rotate_a10_range(rmin, rmax)
        self._apply(self.a10_scale_label,
                    text=tr('出力補正{0}～{1}%').format(rmin, rmax))
        self.a10_bar.set_range(rmin, rmax)

    def _on_a10_invert_change(self):
        self.intiface.rotate_a10_invert = self.a10_invert_var.get()

    def _on_vibration_range_change(self, rmin, rmax):
        """振動強度レンジの変更を即時反映する(再生中も有効)。"""
        self.intiface.set_vibration_range(rmin, rmax)
        self._apply(self.vibration_scale_label, text=tr('出力補正{0}～{1}%').format(rmin, rmax))
        self.vibration_bar.set_range(rmin, rmax)
