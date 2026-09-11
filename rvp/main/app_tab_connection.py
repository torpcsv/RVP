"""メイン画面: 接続タブ(Intiface 自動接続・TCode・rotate 割り当て・mpv)(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import time
import tkinter as tk
from tkinter import filedialog
from .. import tcode_client
from ..i18n import tr

from .common import (COMBO_TEXT, COMBO_TEXT_DISABLED, DEFAULT_INTIFACE_URL,
    LABEL, MUTED, OK_TEXT, WARN_TEXT)
from .startup import _startup_mark
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg


class _RVPAppTabConnectionMixin:
    """RVPApp の mixin(=301 分割)。接続タブ(Intiface 自動接続・TCode・rotate 割り当て・mpv)"""

    def _build_tab_connection(self, tab):
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="x", padx=8, pady=8)

        ctk.CTkLabel(
            wrap, text=tr("Intiface Central サーバーURL"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w")

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        self.url_var = tk.StringVar(value=DEFAULT_INTIFACE_URL)
        self.url_entry = ctk.CTkEntry(
            row, textvariable=self.url_var,
            placeholder_text="ws://127.0.0.1:12345", height=36,
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_connect = ctk.CTkButton(
            row, text=tr("接続"), width=100, height=36,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self.on_connect,
        )
        self.btn_connect.pack(side="left")

        # =78: Intiface Central への自動接続(既定ON・configへ保存)。
        # 未接続の間はポートプローブ(5秒毎)でサーバーの起動を静かに待ち、
        # 開いていれば接続を試みる。切断後の自動再接続もこの1つのチェックが担う
        # (旧「接続が切れたら自動で再接続する」を統合)。
        self.auto_connect_var = tk.BooleanVar(value=True)
        self.auto_connect_check = ctk.CTkCheckBox(
            wrap, text=tr("Intiface Centralへ自動で接続する"),
            variable=self.auto_connect_var,
            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self._on_auto_connect_toggle,
        )
        self.auto_connect_check.pack(anchor="w", pady=(10, 0))

        # =88: TWISTのサブ機能スイッチ(=79)は廃止。TWIST欄・エディタの
        # twist 種別は常時表示になった(相関制御が分かりにくいという
        # ユーザー判断。旧コンフィグの twist_enabled は読み捨てる)。

        # =80: TCodeデバイス(シリアル直結)。FunSR1/OSR2/SR6 などのtwist軸(R0)は
        # buttplug経由では駆動できない(LinearCmd→L{N}写像のみ)ため、
        # 対象デバイスはCOMポートへTCodeコマンドを直接送る。接続中は
        # linear/twist の送信先がIntifaceからこちらへ替わる。
        ctk.CTkLabel(
            wrap, text=tr("TCodeデバイス(シリアル直結)"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(20, 4))
        tc_row = ctk.CTkFrame(wrap, fg_color="transparent")
        tc_row.pack(fill="x")
        self.tcode_port_var = tk.StringVar(value="")
        self.tcode_port_menu = ctk.CTkOptionMenu(
            tc_row, variable=self.tcode_port_var, width=170, height=32,
            values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED)   # =114: ライトは黒(COM6等が薄かった)
        self.tcode_port_menu.pack(side="left")
        self.tcode_refresh_btn = ctk.CTkButton(
            tc_row, text="⟳", width=34, height=32,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._refresh_tcode_ports)
        self.tcode_refresh_btn.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(tc_row, text=tr("ボーレート"),
                     font=ctk.CTkFont(size=12), text_color=LABEL,
                     ).pack(side="left", padx=(12, 4))
        self.tcode_baud_var = tk.StringVar(value=str(tcode_client.DEFAULT_BAUD))
        self.tcode_baud_entry = ctk.CTkEntry(
            tc_row, textvariable=self.tcode_baud_var, width=80, height=32)
        self.tcode_baud_entry.pack(side="left")
        self.tcode_connect_btn = ctk.CTkButton(
            tc_row, text=tr("接続"), width=70, height=32,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self.on_tcode_connect)
        self.tcode_connect_btn.pack(side="left", padx=(10, 0))
        if tcode_client.HAS_SERIAL:
            # =91: 説明文は削除(ユーザー依頼7=接続タブの簡素化。詳細は
            # README_DEV/ヘルプ参照)。ラベル自体は接続ステータス表示に使う。
            tc_note = ""
        else:
            tc_note = tr("pyserialがインストールされていません(pip install pyserial で有効化)")
            self.tcode_connect_btn.configure(state="disabled")
        self.tcode_status_label = ctk.CTkLabel(
            wrap, text=tc_note,
            font=ctk.CTkFont(size=11), text_color=MUTED,
            anchor="w", justify="left", wraplength=560)
        self.tcode_status_label.pack(anchor="w", pady=(4, 0))
        _startup_mark("build: 接続タブ(TCodeポート列挙の前)")
        self._refresh_tcode_ports()
        # =92: Windowsではシリアルポート列挙(特にBluetooth仮想COM)が
        # 数秒かかる事例が知られているため、個別に計測する
        _startup_mark("build: TCodeポート列挙(pyserial comports)")

        # 動画プレーヤー(mpv)のパス設定(動画対応=48)。mpvはオプショナル:
        # 動画つきシナリオの再生時にだけ使われる(音声のみのユーザーは設定不要)
        # =107: 動画を使わない人には常時ノイズになるため、見出しを
        # 「▸ 動画プレーヤー(mpv)の設定」のテキストリンク調トグルにして
        # 折りたたむ(既定=閉じる・開閉状態はコンフィグ保存)。動画つき
        # シナリオを読み込んだときは自動で開く(_load_scenario)。
        self.mpv_toggle_btn = ctk.CTkButton(
            wrap, text="", width=240, height=24, anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent", border_width=0, text_color=LABEL,
            hover_color=("gray85", "gray25"),
            command=self._toggle_mpv_section)
        self.mpv_toggle_btn.pack(anchor="w", pady=(20, 2))
        # 折りたたみの中身。pack/pack_forget で出し入れするので、
        # 再表示のときに「接続中デバイス」より前へ戻せるよう before= を使う。
        self.mpv_body = ctk.CTkFrame(wrap, fg_color="transparent")
        mpv_row = ctk.CTkFrame(self.mpv_body, fg_color="transparent")
        mpv_row.pack(fill="x")
        self.mpv_path_var = tk.StringVar(value="")
        self.mpv_path_entry = ctk.CTkEntry(
            mpv_row, textvariable=self.mpv_path_var, height=32,
            placeholder_text=tr("(空欄=自動で探す)"))
        self.mpv_path_entry.pack(side="left", fill="x", expand=True)
        self.mpv_path_entry.bind(
            "<FocusOut>", lambda _e: self._on_mpv_path_change())
        self.mpv_browse_btn = ctk.CTkButton(
            mpv_row, text=tr("参照..."), width=70, height=32,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self.on_browse_mpv)
        self.mpv_browse_btn.pack(side="left", padx=(6, 0))
        self.mpv_test_btn = ctk.CTkButton(
            mpv_row, text=tr("テスト"), width=60, height=32,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self.on_test_mpv)
        self.mpv_test_btn.pack(side="left", padx=(6, 0))
        # =91: 既定の説明文は削除(ユーザー依頼7)。ラベルは「テスト」の
        # 結果表示に使うため空文字で残す。
        self.mpv_status_label = ctk.CTkLabel(
            self.mpv_body, text="",
            font=ctk.CTkFont(size=11), text_color=MUTED,
            anchor="w", justify="left", wraplength=560)
        self.mpv_status_label.pack(anchor="w", pady=(2, 0))

        self.conn_devices_label = ctk.CTkLabel(
            wrap, text=tr("接続中デバイス"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        )
        self.conn_devices_label.pack(anchor="w", pady=(20, 4))
        # 既定は閉じた状態。コンフィグ復元は _apply_saved_config が行う。
        self._mpv_open = False
        self._apply_mpv_open()

        self.device_box = ctk.CTkTextbox(
            wrap, height=140, corner_radius=10,
            font=ctk.CTkFont(size=13),
        )
        self.device_box.pack(fill="x")
        self.device_box.insert("1.0", tr("(未接続)"))
        self.device_box.configure(state="disabled")

        # ROTATEデバイスの割り当て(ufo/a10レーンの付け替え)。
        # 回転デバイスが接続された時だけ行を出す(未接続時は見出しごと非表示)。
        self.rotate_assign_label = ctk.CTkLabel(
            wrap, text=tr("ROTATEデバイスの割り当て"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        )
        self.rotate_assign_hint = ctk.CTkLabel(
            wrap,
            text=tr("各回転デバイスを ROTATE(ufo) / ROTATE(a10cyclonesa) のどちらで動かすか選べます。"
                    "2ロータ機はロータごとに割り当てを分けられます(左右を入れ替えたい時は割り当てを入れ替えます)。"),
            font=ctk.CTkFont(size=11), text_color=LABEL,
            wraplength=520, justify="left",
        )
        self.rotate_assign_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        # 現在表示中のデバイス割り当て行(名前 → ウィジェット群)。
        self._rotate_assign_rows: dict = {}
        self._rotate_assign_sig = None   # 直近の表示シグネチャ(再構築の抑制)

        # =91: 「※ Intiface Central を起動し…」の案内文は削除(ユーザー
        # 依頼7)。ただし _conn_note はデバイス一覧等の pack(before=) の
        # アンカーとして使われているため、見えない1pxフレームとして残す
        # (空のCTkFrameは200px要求するので pack_propagate(False)+height=1)。
        self._conn_note = ctk.CTkFrame(wrap, fg_color="transparent", height=1)
        self._conn_note.pack_propagate(False)
        self._conn_note.pack(fill="x")
        self._refresh_rotate_assign_ui()

    def on_connect(self):
        self.intiface.url = self.url_var.get().strip()
        self._set_pill(tr("● 接続中..."), WARN_TEXT)
        self.btn_connect.configure(state="disabled")
        self._manual_connecting = True   # =78: 手動接続中は自動接続を止める

        fut = self.runner.submit(self.intiface.connect())
        fut.add_done_callback(lambda f: self.root.after(0, self._after_connect, f))

    def _after_connect(self, fut):
        self.btn_connect.configure(state="normal")
        self._manual_connecting = False
        try:
            fut.result()
            self._want_connected = True   # 以後、切断されたら自動再接続の対象
            self._reconnect_after = 0.0
            self._set_pill(tr("● 接続済み"), OK_TEXT)
            # =272: デバイス欄の描画は _refresh_device_box へ一元化
            self._refresh_device_box(force=True)
            self._refresh_rotate_assign_ui()
        except Exception as e:
            # メッセージボックスは出さず「接続中デバイス」欄に警告を表示する
            # (OK押下の手間を省く)。
            self._set_pill(tr("● 未接続"), MUTED)
            self._set_device_box(
                tr('⚠ 接続できませんでした: {0}\nIntiface Central を起動し、サーバーを開始してから再度お試しください。').format(e))
            # =272: エラー文は接続状況が変わるまで残す(pollに消させない)
            self._device_box_snap = self._device_box_snapshot()

    def _device_box_snapshot(self):
        """欄の描画内容を決める状態の組。変化したときだけ描き直す。"""
        try:
            connected = bool(self.intiface.connected)
        except Exception:
            connected = False
        names = tuple(self.intiface.device_names()) if connected else ()
        try:
            has_linear = bool(self.intiface.has_linear) if connected else False
        except Exception:
            has_linear = False
        return (connected, names, has_linear, bool(self.tcode.connected))

    def _refresh_device_box(self, force: bool = False):
        """「接続中デバイス」欄を現在の接続状況で描き直す(=272)。

        Intiface(Bluetooth)・COM(TCode)いずれの接続状況が変わっても
        _poll_state 経由でここに来て、古い表示(切断済みデバイス名や
        解消済みの注意書き)が残らないようにする。
        linear非対応の注意は **COMポート接続中は表示しない**(COM経由で
        linear/twistデバイスが動くため。「linear接続に失敗した」ように
        読めてしまうという実機フィードバックへの対応)。
        """
        snap = self._device_box_snapshot()
        if not force and snap == self._device_box_snap:
            return
        self._device_box_snap = snap
        connected, names, has_linear, tcode_on = snap
        if not connected:
            text = tr("(未接続)")
            if tcode_on:
                # Intiface未接続でもCOMは生きていることが分かるようにする
                text += "\n" + tr("(COMポートは接続済: linear/twistはCOMポートへ送られます)")
        else:
            text = "\n".join(f"・{n}" for n in names) if names \
                else tr("(デバイスが見つかりません)")
            if not has_linear and not tcode_on:
                text += "\n\n" + tr("⚠ linear対応デバイスが見つかりません。音声のみで再生されます。")
        self._set_device_box(text)

    def _on_auto_connect_toggle(self):
        """自動接続チェックの操作。OFF→ONで即試行できるよう猶予をリセットし、保存。"""
        if self.auto_connect_var.get():
            self._reconnect_after = 0.0
        self.save_app_config()

    def _refresh_tcode_ports(self):
        """シリアルポート一覧を取り直してメニューへ反映する。"""
        ports = tcode_client.available_ports()
        values = [p[0] for p in ports] or [""]
        self.tcode_port_menu.configure(values=values)
        cur = self.tcode_port_var.get()
        if cur not in values:
            self.tcode_port_var.set(values[0])

    def on_tcode_connect(self):
        """TCodeの接続/切断トグル。接続は同期(ポートを開くだけで速い)。"""
        if self.tcode.connected:
            self.tcode.disconnect()
            self.tcode_connect_btn.configure(text=tr("接続"))
            self._set_tcode_status(tr("切断しました"), MUTED)
            self._track_conn = {}   # linear/twist の接続表示を再評価
            self._refresh_device_box()   # =272: 注意書きの出し分けを即反映
            return
        port = self.tcode_port_var.get().strip()
        try:
            baud = int(self.tcode_baud_var.get().strip() or
                       tcode_client.DEFAULT_BAUD)
        except ValueError:
            self._set_tcode_status(tr("ボーレートが数値ではありません"), WARN_TEXT)
            return
        if not port:
            self._set_tcode_status(tr("ポートを選択してください"), WARN_TEXT)
            return
        # =273: 「接続中...」→(成功)→「接続済」の2段階で表示する。
        # ポートを開くのは通常一瞬だが、Windowsではドライバ次第で
        # 数秒かかることがあるため、開いている間の状態を正しく見せる。
        self._set_tcode_status(tr("接続中..."), WARN_TEXT)
        try:
            self.root.update_idletasks()   # 同期接続の前にラベルを描画する
        except Exception:
            pass
        try:
            self.tcode.connect(port, baud)
        except Exception as e:
            self._set_tcode_status(
                tr("接続できません: {0}").format(e), WARN_TEXT)
            return
        self.tcode_connect_btn.configure(text=tr("切断"))
        # =273: 完了後の文言は「接続済」(旧「接続中」は認識の最中と誤読
        # されるという実機フィードバックへの対応)。
        self._set_tcode_status(
            tr("接続済: {0} (linear/twistはこのポートへ送られます)").format(port),
            OK_TEXT)
        self._track_conn = {}
        self._refresh_device_box()   # =272: linear注意書きを即座に取り下げる
        self.save_app_config()

    def _set_tcode_status(self, text: str, color) -> None:
        self._apply(self.tcode_status_label, text=text, text_color=color)

    def _check_auto_connect(self):
        """Intiface Central への自動接続(_poll_state=メインスレッドから毎回呼ぶ)。

        =78: 未接続の間、5秒毎にポートプローブ(probe_ws_port=1ソケットの
        開閉のみ)でサーバーの起動を静かに待ち、ポートが開いていたら本接続を
        試みる。プローブ失敗中はピル表示を変えない(Intifaceを使わない視聴を
        邪魔しない)。接続後に切断された場合も同じ経路で自動再接続になる。
        コルーチンは AsyncRunner(別スレッド)へ投げ、完了は future の
        ポーリングでメインスレッド側で回収する(Tkのクロススレッド呼び出しを
        避け、挙動を予測可能にする)。
        音声再生はそのまま継続し、接続できれば次アイテムからデバイス出力が乗る。
        """
        # 実行中の本接続の完了を回収(メインスレッドで結果を反映)
        if self._reconnect_fut is not None and self._reconnect_fut.done():
            fut, self._reconnect_fut = self._reconnect_fut, None
            self._finish_auto_connect(fut)
        # 実行中のプローブの完了を回収
        if self._probe_fut is not None and self._probe_fut.done():
            fut, self._probe_fut = self._probe_fut, None
            self._finish_probe(fut)
        if not self.auto_connect_var.get():
            return
        if self.intiface.connected or self._manual_connecting:
            return
        if self._reconnect_fut is not None or self._probe_fut is not None:
            return   # 接続 or プローブの実行中
        if time.monotonic() < self._reconnect_after:
            return
        # URL欄の現在値でプローブ(手動接続と同じ対象を見る)
        url = self.url_var.get().strip()
        self._probe_fut = self.runner.submit(_pkg().probe_ws_port(url))

    def _finish_probe(self, fut):
        """プローブ future の回収。ポートが開いていれば本接続を開始する。"""
        try:
            open_ = bool(fut.result())
        except Exception:
            open_ = False
        if not open_:
            # サーバー不在: 次のプローブまで待つ(表示は変えない=静かに待機)
            self._reconnect_after = time.monotonic() + self.AUTO_CONNECT_INTERVAL
            return
        if self.intiface.connected or self._manual_connecting:
            return   # プローブ中に手動接続が成立した等
        # ポートが開いている → 本接続(一度接続済みなら「再接続中」表示)
        self._set_pill(tr("● 再接続中...") if self._want_connected
                       else tr("● 接続中..."), WARN_TEXT)
        self.intiface.url = self.url_var.get().strip()
        self._reconnect_fut = self.runner.submit(self.intiface.connect())

    def _finish_auto_connect(self, fut):
        """自動接続 future の結果をUIへ反映する(メインスレッド)。"""
        try:
            fut.result()
            self._want_connected = True
            self._set_pill(tr("● 接続済み"), OK_TEXT)
            # =272: 手動接続と同じ一元描画へ(従来この経路はlinear非対応の
            # 注意書きを出しておらず、手動接続と表示が食い違っていた)
            self._refresh_device_box(force=True)
            self._refresh_rotate_assign_ui()
            self._reconnect_after = 0.0
        except Exception:
            # 失敗(サーバーは居るが接続不成立=他アプリ占有など):
            # 次の試行まで待つ。一度も接続していなければピルを未接続へ戻す
            # (「接続中...」で点滅し続けない)。切断復帰待ちは再接続中のまま。
            if self._want_connected:
                self._set_pill(tr("● 再接続中..."), WARN_TEXT)
            else:
                self._set_pill(tr("● 未接続"), MUTED)
            self._reconnect_after = time.monotonic() + self.AUTO_CONNECT_INTERVAL

    def _set_pill(self, text: str, color: str):
        self.conn_pill.configure(text=text, text_color=color)

    def _set_device_box(self, text: str):
        self.device_box.configure(state="normal")
        self.device_box.delete("1.0", "end")
        self.device_box.insert("1.0", text)
        self.device_box.configure(state="disabled")

    def _refresh_rotate_assign_ui(self):
        """接続中の回転デバイスに合わせて割り当て行を作り直す。

        デバイスの集合が変わった時だけ再構築し(シグネチャ比較)、レーンの
        値は毎回現在値へ更新する(再接続で復元された割り当ての反映)。
        回転デバイスが無い時は見出しごと隠す。
        """
        devs = self.intiface.rotate_devices() if self.intiface else []
        sig = tuple((d["name"], d.get("rotors", 1)) for d in devs)
        if not devs:
            # 回転デバイス無し: 見出し・ヒント・行を隠す
            if self._rotate_assign_sig is not None:
                self.rotate_assign_label.pack_forget()
                self.rotate_assign_hint.pack_forget()
                self.rotate_assign_frame.pack_forget()
                for row in self._rotate_assign_rows.values():
                    row["frame"].destroy()
                self._rotate_assign_rows.clear()
                self._rotate_assign_sig = None
            return

        if sig != self._rotate_assign_sig:
            # デバイス集合が変わった → 行を作り直す
            for row in self._rotate_assign_rows.values():
                row["frame"].destroy()
            self._rotate_assign_rows.clear()
            # 見出し・ヒント・枠を接続注記の前に配置
            self.rotate_assign_label.pack(anchor="w", pady=(18, 2),
                                          before=self._conn_note)
            self.rotate_assign_hint.pack(anchor="w", pady=(0, 4),
                                         before=self._conn_note)
            self.rotate_assign_frame.pack(fill="x", before=self._conn_note)
            for d in devs:
                self._build_rotate_assign_row(d)
            self._rotate_assign_sig = sig
        else:
            # 集合は同じ: 現在レーンをメニューへ反映(=120: ロータ別)
            for d in devs:
                row = self._rotate_assign_rows.get(d["name"])
                if row is not None:
                    lanes = d.get("lanes") or [d["lane"]]
                    for i, var in enumerate(row["vars"]):
                        lane = lanes[i] if i < len(lanes) else lanes[-1]
                        var.set(self._lane_label(lane))

    def _lane_label(self, lane: str) -> str:
        """レーン識別子 → 表示ラベル。"""
        return ("ROTATE(a10cyclonesa)"
                if lane == self.intiface.LANE_A10 else "ROTATE(ufo)")

    def _build_rotate_assign_row(self, dev: dict):
        """1デバイス分の割り当て行を作る(=120: 2ロータ機はロータ別2コンボ)。

        1ロータ機: 「名前 ─── [ufo/a10]」(従来)。
        2ロータ機: 「名前(2ロータ) ─ ロータ1→[ufo/a10] ロータ2→[ufo/a10]」。
        両ロータを別レーンへ割り当てる=分割(左右を変えたい時は割り当てを
        入れ替える。分割中は「左右反転」チェックが意味を失うので非表示になる)。
        """
        name = dev["name"]
        row = ctk.CTkFrame(self.rotate_assign_frame, fg_color="transparent")
        row.pack(fill="x", pady=2)
        rotors = dev.get("rotors", 1)
        suffix = tr("(2ロータ)") if rotors >= 2 else ""
        ctk.CTkLabel(
            row, text=f"{name}{suffix}", text_color=LABEL,
            font=ctk.CTkFont(size=12), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        lanes = dev.get("lanes") or [dev["lane"]] * rotors
        vars_, menus = [], []
        if rotors >= 2:
            for i in range(rotors):
                if i > 0:
                    pass  # ラベルが区切りを兼ねる
                ctk.CTkLabel(
                    row, text=f'{tr("ロータ")}{i + 1}→', text_color=LABEL,
                    font=ctk.CTkFont(size=11),
                ).pack(side="left", padx=(10, 2))
                var = tk.StringVar(value=self._lane_label(lanes[i]))
                menu = ctk.CTkOptionMenu(
                    row, variable=var, width=190, height=26,
                    font=ctk.CTkFont(size=11),
                    values=["ROTATE(ufo)", "ROTATE(a10cyclonesa)"],
                    fg_color=("gray75", "gray28"),
                    button_color=("gray70", "gray33"),
                    text_color=COMBO_TEXT,
                    text_color_disabled=COMBO_TEXT_DISABLED,
                    command=lambda _v, n=name, ix=i, rt=rotors:
                        self._on_rotate_rotor_change(n, ix, rt),
                )
                menu.pack(side="left")
                vars_.append(var)
                menus.append(menu)
        else:
            var = tk.StringVar(value=self._lane_label(lanes[0]))
            menu = ctk.CTkOptionMenu(
                row, variable=var, width=190, height=26,
                font=ctk.CTkFont(size=11),
                values=["ROTATE(ufo)", "ROTATE(a10cyclonesa)"],
                fg_color=("gray75", "gray28"),
                button_color=("gray70", "gray33"),
                text_color=COMBO_TEXT,
                text_color_disabled=COMBO_TEXT_DISABLED,
                command=lambda _v, n=name: self._on_rotate_assign_change(n),
            )
            menu.pack(side="right")
            vars_.append(var)
            menus.append(menu)
        self._rotate_assign_rows[name] = {"frame": row, "vars": vars_,
                                          "menus": menus,
                                          "var": vars_[0], "menu": menus[0]}

    def _lane_from_label(self, label: str) -> str:
        """表示ラベル → レーン識別子。"""
        return (self.intiface.LANE_A10
                if label == "ROTATE(a10cyclonesa)" else self.intiface.LANE_UFO)

    def _on_rotate_assign_change(self, name: str):
        """割り当てメニュー操作(1ロータ機): 反映・保存・トラック表示更新。"""
        row = self._rotate_assign_rows.get(name)
        if row is None:
            return
        self.intiface.set_rotate_assign(
            name, self._lane_from_label(row["vars"][0].get()))
        self._after_rotate_assign_change()

    def _on_rotate_rotor_change(self, name: str, index: int, rotors: int):
        """割り当てメニュー操作(=120: 2ロータ機のロータ別)。"""
        row = self._rotate_assign_rows.get(name)
        if row is None or index >= len(row["vars"]):
            return
        self.intiface.set_rotate_rotor_assign(
            name, index, self._lane_from_label(row["vars"][index].get()),
            rotors)
        self._after_rotate_assign_change()

    def _after_rotate_assign_change(self):
        """割り当て変更の共通後処理: 保存・接続表示・分割/反転チェック更新。"""
        self.save_app_config()
        # 接続中デバイス欄のタグ表示とトラックの接続状況を更新する
        # (=272: 一元描画へ。レーンタグ入りのデバイス名で描き直す)
        self._refresh_device_box(force=True)
        self._track_conn = {}     # 次の _update_track_conn で強制再評価
        self._update_track_conn()
        self._update_swap_visibility()   # ufotwの割当先変更を反映

    def _lane_has_ufotw(self, lane: str) -> bool:
        """指定レーンに2ロータ揃った機(ufotw)が割り当たっているか。

        =120: ロータ単位割り当てに対応。分割割り当て(片ロータずつ別レーン)の
        デバイスはどちらのレーンでも「2ロータ揃っていない」=False になり、
        単一バー表示+左右反転チェック非表示になる。
        """
        try:
            return any(d.get("lanes", []).count(lane) >= 2
                       for d in self.intiface.rotate_devices())
        except Exception:
            return False

    def _lane_is_split(self, lane: str) -> bool:
        """このレーンを左右分割(ufotw)で表示すべきか。

        接続デバイスがこのレーンに居る場合は「このレーンに2ロータ揃った機が
        居るか」で判定(=120: 分割割り当てなら単一バー)。
        レーンに接続デバイスが無い(未接続プレビュー)場合は、シナリオ内の
        2ch(タイプBのCSV)内容の有無で判定して、デバイスが無くても左右分割
        バーと左右反転チェックを出す。
        """
        try:
            lane_connected = any(lane in d.get("lanes", [])
                                 for d in self.intiface.rotate_devices())
        except Exception:
            lane_connected = False
        if lane_connected:
            return self._lane_has_ufotw(lane)
        return self._scenario_split_lanes.get(lane, False)

    def _update_swap_visibility(self):
        """ufotwが割り当たったレーンの「左右反転」チェックを出し入れする。"""
        for lane, (chk, _var) in getattr(self, "_swap_checks", {}).items():
            want = self._lane_is_split(lane)
            if want != self._swap_vis.get(lane):
                if want:
                    chk.pack(side="right", padx=(0, 6))
                else:
                    chk.pack_forget()
                self._swap_vis[lane] = want

    def _on_rotate_swap_change(self, lane: str):
        """左右反転チェックの操作: player へ反映して保存する。"""
        var = self.rotate_swap_var if lane == "ufo" else self.a10_swap_var
        self.player.rotate_swap[lane] = bool(var.get())
        self.save_app_config()

    def _rotor_disp(self, lane: str, val) -> tuple:
        """(clockwise, frac) を レーンのレンジ/反転を適用した (speed, positive) に。"""
        cw, frac = val
        inv = self.intiface._lane_invert(lane)
        positive = (not cw) if inv else cw
        if frac <= 0:
            return 0.0, positive
        rmin, rmax = self.intiface._lane_range(lane)
        speed = max(0.0, min(1.0, rmin + frac * (rmax - rmin)))
        return speed, positive

    def _show_rotate_split(self, lane, ch_key, bar, label, connected):
        """ufotwの左右2ロータを上下分割バー+「左X% 右Y%」で表示する。"""
        gray = self.TRACK_DISABLED_COLOR
        ch = self.player.state.get(ch_key) or [(True, 0.0)]
        swap = self.player.rotate_swap.get(lane, False)
        if len(ch) >= 2:
            src_l, src_r = ch[0], ch[1]
        else:
            src_l = src_r = ch[0]
        if swap:                       # デバイスのロータ視点で左右入替
            src_l, src_r = src_r, src_l
        ls, lp = self._rotor_disp(lane, src_l)
        rs, rp = self._rotor_disp(lane, src_r)
        bar.set_split_values(ls if ls > 0 else None, rs if rs > 0 else None)

        def part(sp, positive):
            if sp <= 0:
                return "0"
            pct = int(round(sp * 100))
            return f"{pct}" if positive else f"-{pct}"

        # 表示は「左/右」を横幅を取らない「30/30」形式にする(逆回転は負符号)。
        txt = f"{part(ls, lp)}/{part(rs, rp)}"
        color = LABEL if (ls <= 0 and rs <= 0) else OK_TEXT
        self._apply(label, text=txt,
                    text_color=color if connected else gray)

    def _apply_mpv_open(self):
        """現在の開閉状態を画面へ反映する(見出しの▸/▾と本体の出し入れ)。"""
        mark = "▾ " if self._mpv_open else "▸ "
        self.mpv_toggle_btn.configure(
            text=mark + tr("動画プレーヤー(mpv)の設定"))
        if self._mpv_open:
            # 「接続中デバイス」見出しより前へ戻す(pack の順序は
            # pack_forget で失われるため before= で位置を指定する)。
            self.mpv_body.pack(fill="x", before=self.conn_devices_label)
        else:
            self.mpv_body.pack_forget()

    def _set_mpv_open(self, open_: bool, save: bool = True):
        """mpv欄の開閉を設定する。save=False はこの場で設定ファイルを
        書かないだけの指定(起動時の復元・シナリオ読み込み時の自動
        オープン用。後者は _load_scenario 末尾の save_app_config で
        結果的に保存され、次回起動時も開いた状態で始まる)。"""
        open_ = bool(open_)
        if open_ == self._mpv_open:
            return
        self._mpv_open = open_
        self._apply_mpv_open()
        if save:
            self.save_app_config()

    def _toggle_mpv_section(self):
        self._set_mpv_open(not self._mpv_open)

    def _scenario_has_video(self, sc) -> bool:
        """シナリオが動画チャンネルを1つでも持つか(=107の自動オープン用)。"""
        try:
            for ev in sc.events.values():
                for st in ev.states.values():
                    if st.has_video:
                        return True
        except Exception:
            pass
        return False

    def _on_mpv_path_change(self):
        """mpvパス欄の確定(フォーカスアウト/参照)。プレーヤーへ反映して保存。"""
        path = self.mpv_path_var.get().strip()
        self.player.mpv_path = path or None
        try:
            self.save_app_config()
        except Exception:
            pass

    def on_browse_mpv(self):
        path = filedialog.askopenfilename(
            title=tr("mpv の実行ファイルを選択"),
            filetypes=[(tr("実行ファイル"), "*.exe"), (tr("すべて"), "*.*")])
        if path:
            self.mpv_path_var.set(path)
            self._on_mpv_path_change()

    def on_test_mpv(self):
        """mpvが見つかるかの簡易チェック(起動はしない)。"""
        from ..mpv_client import find_mpv
        import shutil as _sh
        path = self.mpv_path_var.get().strip() or find_mpv()
        if path and (os.path.isfile(path) or _sh.which(path)):
            self.mpv_status_label.configure(
                text=tr("mpv が見つかりました: {0}").format(path),
                text_color=OK_TEXT)
        else:
            self.mpv_status_label.configure(
                text=tr("mpv が見つかりません。パスを指定してください"),
                text_color=WARN_TEXT)
