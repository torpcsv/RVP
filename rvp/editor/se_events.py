"""シナリオ編集: イベント操作(追加/コピー/削除/リネーム/選択)・パネルの読み書き・変数/監視・インポート(mixin)。"""
from __future__ import annotations

import json
import os
from ..i18n import tr

from .common import (CHANNEL_IDS, DEFAULT_EVENT, INFINITE_CHOICE,
    _dialog_initialdir, _has_varref, _is_num, _load_raw, _raw_has_video,
    _remember_dialog_dir)
from .dialogs import ImportDialog, OpsDialog, VarsDialog
from .paths import _map_item_paths
from ._hooks import _pkg


class _ScenarioEditorEventsMixin:
    """ScenarioEditor の mixin(=301 分割)。イベント操作(追加/コピー/削除/リネーム/選択)・パネルの読み書き・変数/監視・インポート"""

    def _var_decl_init(self, decl):
        return decl.get("init") if isinstance(decl, dict) else decl

    def _var_names(self) -> list[str]:
        return list((self.data.get("vars") or {}).keys())

    def _numeric_var_names(self) -> list[str]:
        return [n for n, d in (self.data.get("vars") or {}).items()
                if _is_num(self._var_decl_init(d))]

    def _string_var_names(self) -> set:
        return {n for n, d in (self.data.get("vars") or {}).items()
                if isinstance(self._var_decl_init(d), str)}

    def _has_vars(self) -> bool:
        return bool(self.data.get("vars"))

    def _update_vars_btn(self):
        n = len(self._var_names())
        self.vars_btn.configure(text=tr("変数/監視({0})").format(n) if n
                                else tr("変数/監視"))

    def _open_vars_dialog(self):
        # 現在のパネルを反映してから開く(OK後にパネルを再構築するため)
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        dlg = VarsDialog(self, self.data.get("vars") or {},
                         self.data.get("watch") or [],
                         list(self.data["events"].keys()))
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._apply_vars_result(dlg.result)

    def _apply_vars_result(self, result: dict):
        """変数ダイアログの結果をモデルへ反映し、パネルを再構築する。"""
        if result.get("vars"):
            self.data["vars"] = result["vars"]
        else:
            self.data.pop("vars", None)
        if result.get("watch"):
            self.data["watch"] = result["watch"]
        else:
            self.data.pop("watch", None)
        self._update_vars_btn()
        if self.selected in self.data["events"]:
            self._load_panel(self.selected)
        self._redraw_canvas()

    def open_ops_dialog(self, title: str, sections, on_ok):
        """変数操作ダイアログを開き、OKなら on_ok(result) を呼ぶ。"""
        dlg = OpsDialog(self, title, sections, self._var_names(),
                        self._numeric_var_names(), self._string_var_names())
        self.wait_window(dlg)
        if dlg.result is not None:
            on_ok(dlg.result)

    @staticmethod
    def ops_btn_text(*ops_lists) -> str:
        n = sum(len(o or []) for o in ops_lists)
        return tr("変数操作({0})").format(n)

    def _commit_pending_renames(self):
        """入力途中のイベント名/ステート名リネームをコミットする(=100①)。

        名前欄は <FocusOut>/<Return> でコミットしているが、遷移図・ステート図の
        ○やツールバーのボタンは**クリックしてもフォーカスが移らない**
        (tk.Canvas / CTkButton はクリックでフォーカスを取らない)ため、
        入力→即クリックの操作では FocusOut が発火せず、修正が黙って捨てられて
        いた。切り替え/保存/追加の入口で明示的にコミットする。
        名前が不正(空・重複)なときは従来どおりエラー表示+旧名へ戻る。
        """
        self._commit_state_rename()
        self._commit_event_rename()

    def _select(self, event_id: str):
        if event_id not in self.data["events"]:
            return
        # =100①: 入力途中のイベント名/ステート名を先にコミットする
        self._commit_pending_renames()
        if event_id not in self.data["events"]:
            # クリックした○が、直前のリネームで旧名になった場合(=自分自身)
            return
        # 現在の編集内容を反映してから切り替え
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        self.selected = event_id
        self._load_panel(event_id)
        self._redraw_canvas()

    def _edit_event_ops(self):
        def on_ok(result):
            self.set_event_ops(result)
        self.open_ops_dialog(
            tr("イベント {0}").format(self.selected),
            [(tr("イベント開始時(on_start)"), "on_start", self._ev_ops),
             (tr("イベント終了時(on_end)"), "on_end", self._ev_end_ops)], on_ok)

    def _refresh_ev_ops_btn(self):
        self.ev_ops_btn.configure(text=tr("開始{0}・終了{1}").format(
            len(self._ev_ops), len(self._ev_end_ops)))

    def set_event_ops(self, result: dict):
        self._ev_ops = result.get("on_start") or []
        self._ev_end_ops = result.get("on_end") or []
        self._refresh_ev_ops_btn()

    def _begin_render(self) -> bool:
        """パネル再構築の間、パネルを隠して「描画中…」を表示する。

        再入時(_load_panel が内部で _load_state_panel を呼ぶ等)は最外だけ
        効かせる。戻り値 True の呼び出しだけが _end_render で復帰させる。
        """
        if self._rendering:
            return False
        self._rendering = True
        try:
            self.panel.pack_forget()
            self.rendering_label.pack(fill="both", expand=True)
            self.update_idletasks()   # 「描画中…」を先に描く
        except Exception:
            pass
        return True

    def _end_render(self, active: bool):
        if not active:
            return
        self._rendering = False
        try:
            self.rendering_label.pack_forget()
            self.panel.pack(fill="both", expand=True)
        except Exception:
            pass

    def _load_panel(self, event_id: str):
        _r = self._begin_render()
        try:
            self._load_panel_body(event_id)
        finally:
            self._end_render(_r)
        # =277: 読み込み直後の正規化を履歴の変更と見なさない(下記)
        self._hist_normalize(lambda: self._apply_panel_body())
        self._hist_check()   # =277: 選択位置の記録(変更があれば1段積む)

    def _load_panel_body(self, event_id: str):
        ev = self.data["events"][event_id]
        multi = "states" in ev
        self.ev_end_locked = False
        self.event_id_var.set(event_id)
        self.states_toggle_btn.configure(
            text=tr("ステート形式を解除") if multi else tr("ステート形式に変換"))

        # 遷移UI
        self._rebuild_next_ui(event_id)
        self.start_var.set(self.data.get("start") == event_id)

        # イベント開始時の変数操作(変数宣言があるときだけ表示)
        self._ev_ops = list(ev.get("on_start") or [])
        self._ev_end_ops = list(ev.get("on_end") or [])
        if self._has_vars():
            self._refresh_ev_ops_btn()
            self.ev_ops_row.pack(fill="x", pady=(4, 0), before=self.next_area)
        else:
            self.ev_ops_row.pack_forget()

        if multi:
            self._show_states_ui(True)
            # ステート形式ではイベント終了条件は「ステートに委譲」のみ・変更不可。
            # (「委譲」はステート形式でないと選択肢に出さない = 相関制御)
            self.evend_menu.configure(values=[self.EVEND_STATE])
            self.evend_var.set(self.EVEND_STATE)
            self.evend_menu.configure(state="disabled")
            self._update_evend_ui()
            # イベント終了条件
            end = ev.get("end") or {}
            etype = end.get("type")
            self.ev_end_locked = False
            self.ev_end_field.set_names(self._numeric_var_names())
            self.ev_end_field2.set_names(self._numeric_var_names())
            self.ev_end_field2.set("")
            # 「変数条件で次へ」は変数宣言がある時だけ選択肢に出す(相関制御)
            ev_end_choices = list(self.EV_END_CHOICES)
            if self._has_vars():
                ev_end_choices.append(self.EV_END_COND)
            ev_end_choices.append(self.EV_END_STATES)
            ev_end_choices.append(self.EV_END_INFINITE)   # =168
            self.ev_end_menu.configure(values=ev_end_choices)
            self._rebuild_ev_end_state_checks(ev)
            # 時間指定は秒のみ(minutesは廃止=読まない)
            has_ref = _has_varref(end.get("count"), end.get("seconds"))
            if etype == "none":
                self.ev_end_var.set(self.EV_END_INFINITE)   # =168
            elif etype == "cond":
                self.ev_end_var.set(self.EV_END_COND)
                self.ev_end_cond.set_names(self._var_names())
                self.ev_end_cond.load(end.get("when") or [])
            elif etype == "states":
                self.ev_end_var.set(self.EV_END_STATES)
                for s in (end.get("states") or []):
                    if s in self.ev_end_state_vars:
                        self.ev_end_state_vars[s].set(True)
            elif has_ref and etype in ("plays", "transitions"):
                self.ev_end_var.set(tr("合計N回の再生で次へ") if etype == "plays"
                                    else tr("N回のステート移行で次へ"))
                self.ev_end_field.set(end.get("count"))
            elif has_ref and etype == "duration" and isinstance(end.get("seconds"), dict):
                self.ev_end_var.set(tr("合計N秒で次へ"))
                self.ev_end_field.set(end.get("seconds"))
                self.ev_end_field2.set(end.get("seconds"))   # min==max(単一値)
            elif etype == "plays":
                self.ev_end_var.set(tr("合計N回の再生で次へ"))
                self.ev_end_field.set(str(end.get("count", 1)))
            elif etype == "transitions":
                self.ev_end_var.set(tr("N回のステート移行で次へ"))
                self.ev_end_field.set(str(end.get("count", 1)))
            else:   # duration(秒のみ)。旧minutesは無視するので空欄で再入力を促す
                # =99: 範囲(min_seconds/max_seconds)は2欄へ、単一 seconds は
                # min==max として両欄に同じ値を入れる(通常イベントと同じ)
                self.ev_end_var.set(tr("合計N秒で次へ"))
                if "min_seconds" in end or "max_seconds" in end:
                    lo = end.get("min_seconds", 0)
                    hi = end.get("max_seconds", lo)
                else:
                    lo = hi = end.get("seconds")
                def _dsec(v):
                    if isinstance(v, dict):
                        return v
                    return (f"{float(v):g}"
                            if isinstance(v, (int, float)) else "5")
                self.ev_end_field.set(_dsec(lo))
                self.ev_end_field2.set(_dsec(hi))
            self._update_ev_end_ui()
            lock_state = "disabled" if self.ev_end_locked else "normal"
            self.ev_end_menu.configure(state=lock_state)
            self.ev_end_field.set_state(lock_state)
            self.ev_end_field2.set_state(lock_state)
            # 開始ステート(無効なら先頭)を選択して読み込む
            sid = ev.get("start")
            if sid not in ev["states"]:
                sid = next(iter(ev["states"]))
            self.sel_state = None
            self._load_state_panel(sid)
        else:
            self._show_states_ui(False)
            self.sel_state = None
            self._set_device_vars(
                ev.get("device"), ev.get("channels", {}),
                has_video=_raw_has_video(ev.get("channels")))
            self._load_bgm(ev.get("bgm"))   # =256
            channels = ev.get("channels", {})
            # イベント終了条件(全ch終了 / 指定ch終了 / 合計時間)。「委譲」は
            # ステート形式専用なので通常イベントの選択肢には出さない(相関制御)。
            # 「変数条件で終了」は変数宣言があるときだけ選択肢に出す(相関制御)
            evend_choices = [self.EVEND_ALL, self.EVEND_CHANNEL,
                             self.EVEND_DURATION]
            if self._has_vars():
                evend_choices.append(self.EVEND_COND)
            evend_choices.append(self.EVEND_INFINITE)   # =168
            self.evend_menu.configure(values=evend_choices, state="normal")
            self.evend_secs_min.set_names(self._numeric_var_names())
            self.evend_secs_max.set_names(self._numeric_var_names())
            end = ev.get("end")
            if isinstance(end, dict) and end.get("type") == "channel":
                self.evend_var.set(self.EVEND_CHANNEL)
                self.evend_ch_var.set(end.get("channel") or "C")
            elif isinstance(end, dict) and end.get("type") == "none":
                self.evend_var.set(self.EVEND_INFINITE)   # =168
            elif isinstance(end, dict) and end.get("type") == "cond":
                self.evend_var.set(self.EVEND_COND)
                self.evend_cond.set_names(self._var_names())
                self.evend_cond.load(end.get("when") or [])
            elif isinstance(end, dict) and end.get("type") == "duration":
                self.evend_var.set(self.EVEND_DURATION)
                def _dsec(v):
                    if isinstance(v, dict):
                        return v
                    return f"{float(v):g}" if isinstance(v, (int, float)) else ""
                if "min_seconds" in end or "max_seconds" in end:
                    lo = end.get("min_seconds", 0)
                    hi = end.get("max_seconds", lo)
                else:   # 単一 seconds
                    lo = hi = end.get("seconds", 0)
                self.evend_secs_min.set(_dsec(lo))
                self.evend_secs_max.set(_dsec(hi))
            else:
                self.evend_var.set(self.EVEND_ALL)
            target = self.evend_ch_var.get()
            cond = self.evend_var.get()
            for ch_id, sec in self.channel_sections.items():
                if cond in (self.EVEND_COND, self.EVEND_DURATION,
                            self.EVEND_INFINITE):
                    allow = True
                else:
                    allow = (cond == self.EVEND_CHANNEL and ch_id != target)
                sec.load(channels.get(ch_id), state_mode=False,
                         allow_infinite=allow)
            self.seek_var.set(ev.get("seek_channel")
                              or self._auto_seek_channel(channels))
            self._update_evend_ui()
            self._update_noaudio_correlation()
            self._update_video_correlation()

    def _apply_panel(self, lenient_channel: str | None = None) -> str | None:
        """パネルの内容をモデルへ書き戻す。エラーメッセージ or None。

        =277: 成功したら履歴チェックポイント(変更があれば1段積む)。
        """
        err = self._apply_panel_body(lenient_channel)
        if err is None:
            self._hist_check()
        return err

    def _apply_panel_body(self, lenient_channel: str | None = None) -> str | None:
        """パネルの内容をモデルへ書き戻す(本体)。エラーメッセージ or None。

        lenient_channel(=129): 「他からコピー」の対象チャンネル。コピーで
        丸ごと置き換わるため、そのチャンネル自身の検証エラーは免除する
        (空のまま有効化してコピーしようとした時の「音声が1つもありません」
        等で操作が塞がらないように)。他チャンネル・イベント終了条件・
        デバイス担当の検証は従来どおり(コミットされて残るため緩めない)。
        """
        self._pending_marks = []
        ev_id = self.selected
        ev = self.data["events"][ev_id]

        if "states" in ev:
            err = self._apply_state_panel()
            if err:
                return err
            # =256: ステート形式では bgm は各ステートの持ち物(イベント直下は
            # 読み込みエラーになる書式なので、残っていれば落とす)
            ev.pop("bgm", None)
            # イベント終了条件(UIで表せない変数指定は元の値をそのまま保持)
            if not getattr(self, "ev_end_locked", False):
                choice = self.ev_end_var.get()
                if choice == self.EV_END_COND:
                    err, conds = self.ev_end_cond.collect(
                        tr('イベント {0} 終了条件').format(ev_id),
                        self._string_var_names(), mark=self._want_mark)
                    if err:
                        return err
                    ev["end"] = {"type": "cond", "when": conds}
                elif choice == self.EV_END_INFINITE:
                    ev["end"] = {"type": "none"}   # =168
                elif choice == self.EV_END_STATES:
                    sel = [s for s, v in self.ev_end_state_vars.items()
                           if v.get() and s in ev["states"]]
                    if not sel:
                        for cb in self.ev_end_state_checks:
                            self._want_mark(cb, "error")
                        return tr('イベント {0}: 終了とみなすステートを1つ以上選択してください').format(ev_id)
                    ev["end"] = {"type": "states", "states": sel}
                elif choice == tr("合計N秒で次へ"):
                    # =99: min秒〜max秒(範囲は入場ごとに抽選・max空欄=min)。
                    # 保存規則は通常イベントの evend と同じ: min==max は単一
                    # seconds、異なれば min_seconds/max_seconds
                    lo_var = self.ev_end_field.use_var
                    hi_var = self.ev_end_field2.use_var
                    try:
                        if lo_var:
                            lo = self.ev_end_field.get_raw()
                            if not lo.get("var"):
                                raise ValueError
                        else:
                            lo = float(self.ev_end_field.get_text())
                        if hi_var:
                            hi = self.ev_end_field2.get_raw()
                            if not hi.get("var"):
                                raise ValueError
                        elif self.ev_end_field2.get_text().strip():
                            hi = float(self.ev_end_field2.get_text())
                        else:
                            hi = lo      # max空欄=minと同じ
                            hi_var = lo_var
                    except ValueError:
                        self._want_mark(self.ev_end_field, "error")
                        self._want_mark(self.ev_end_field2, "error")
                        return tr('イベント {0}: イベント終了条件の値(min/max)が不正です').format(ev_id)
                    if not lo_var and not hi_var:
                        if hi < lo:
                            lo, hi = hi, lo
                        if lo < 0 or hi <= 0:
                            self._want_mark(self.ev_end_field, "error")
                            self._want_mark(self.ev_end_field2, "error")
                            return tr('イベント {0}: イベント終了条件の秒数は0以上(最大は正の数)にしてください').format(ev_id)
                    same = (lo_var == hi_var) and (
                        (lo_var and lo.get("var") == hi.get("var"))
                        or (not lo_var and lo == hi))
                    if same:
                        ev["end"] = {"type": "duration", "seconds": lo}
                    else:
                        ev["end"] = {"type": "duration",
                                     "min_seconds": lo, "max_seconds": hi}
                elif self.ev_end_field.use_var:
                    raw = self.ev_end_field.get_raw()
                    if not raw.get("var"):
                        self._want_mark(self.ev_end_field, "error")
                        return tr('イベント {0}: イベント終了条件の変数を選択してください').format(ev_id)
                    if choice == tr("合計N回の再生で次へ"):
                        ev["end"] = {"type": "plays", "count": raw}
                    else:   # N回のステート移行で次へ
                        ev["end"] = {"type": "transitions", "count": raw}
                else:
                    try:
                        v = float(self.ev_end_field.get_text())
                        if v <= 0:
                            raise ValueError
                    except ValueError:
                        self._want_mark(self.ev_end_field, "error")
                        return tr('イベント {0}: イベント終了条件の値が不正です').format(ev_id)
                    if choice == tr("合計N回の再生で次へ"):
                        ev["end"] = {"type": "plays", "count": int(v)}
                    else:   # N回のステート移行で次へ
                        ev["end"] = {"type": "transitions", "count": int(v)}
            if self._ev_ops:
                ev["on_start"] = self._ev_ops
            else:
                ev.pop("on_start", None)
            if self._ev_end_ops:
                ev["on_end"] = self._ev_end_ops
            else:
                ev.pop("on_end", None)
            err, value = self._collect_next(ev_id)
            if err:
                return err
            ev["next"] = value
            err, adv = self._collect_advance()
            if err:
                return tr('イベント {0}: {1}').format(ev_id, err)
            if adv is not None:
                ev["advance"] = adv
            else:
                ev.pop("advance", None)
            if self.start_var.get():
                self.data["start"] = ev_id
            return None

        # ---- 通常イベント ----
        # 有効チャンネル0=音声なしイベント(即時通過ノード)として保存できる
        enabled = [c for c, s in self.channel_sections.items()
                   if s.enabled_var.get()]
        for ch_id, sec in self.channel_sections.items():
            if ch_id == lenient_channel:
                continue   # =129: コピーで丸ごと置換される対象chは検証免除
            err = sec.validate()
            if err:
                return tr('イベント {0}: {1}').format(ev_id, err)
        # 音声なし(有効ch0)は device 自体を保存しないため検証をスキップ
        for ttype, var in (self.device_vars.items() if enabled else ()):
            ch = var.get()
            if ch != tr("なし") and ch not in enabled:
                self._want_mark(self.device_menus.get(ttype), "error")
                return (tr('イベント {0}: デバイス担当 {1}→{2} は無効なチャンネルを指しています').format(ev_id, ttype, ch))

        # イベント終了条件(全ch終了 / 指定ch終了 / 合計時間 / 変数条件)
        end_channel = None
        end_cond_value = None
        end_duration_value = None
        if self.evend_var.get() == self.EVEND_CHANNEL:
            end_channel = self.evend_ch_var.get()
            if end_channel not in enabled:
                self._want_mark(self.evend_ch_menu, "error")
                return (tr('イベント {0}: イベント終了条件の対象チャンネル {1} が有効ではありません').format(ev_id, end_channel))
            tsec = self.channel_sections[end_channel]
            if tsec.end_var.get() == INFINITE_CHOICE:
                self._want_mark(tsec.end_menu, "error")
                return (tr('イベント {0}: イベント終了条件の対象チャンネル {1} には有限の終了条件(無限以外)を設定してください').format(ev_id, end_channel))
        elif self.evend_var.get() == self.EVEND_DURATION:
            # 合計時間(イベント開始からの累積秒)。min秒〜max秒(0以上)。max空欄=min。
            # 各欄は定数 or 数値変数参照。入場ごとに [min,max] を一様抽選して判定。
            lo_var = self.evend_secs_min.use_var
            hi_var = self.evend_secs_max.use_var
            try:
                if lo_var:
                    lo = self.evend_secs_min.get_raw()
                    if not lo.get("var"):
                        raise ValueError
                else:
                    lo = float(self.evend_secs_min.get_text())
                if hi_var:
                    hi = self.evend_secs_max.get_raw()
                    if not hi.get("var"):
                        raise ValueError
                elif self.evend_secs_max.get_text().strip():
                    hi = float(self.evend_secs_max.get_text())
                else:
                    hi = lo          # max空欄=minと同じ(minが変数なら同じ変数)
                    hi_var = lo_var
            except ValueError:
                self._want_mark(self.evend_secs_min, "error")
                self._want_mark(self.evend_secs_max, "error")
                return tr('イベント {0}: イベント終了条件の値(min/max)が不正です').format(ev_id)
            if not lo_var and not hi_var:
                if hi < lo:
                    lo, hi = hi, lo
                if lo < 0 or hi <= 0:
                    self._want_mark(self.evend_secs_min, "error")
                    self._want_mark(self.evend_secs_max, "error")
                    return tr('イベント {0}: イベント終了条件の秒数は0以上(最大は正の数)にしてください').format(ev_id)
            # min==max は単一 seconds、異なれば範囲 min_seconds/max_seconds で保存
            same = (lo_var == hi_var) and (
                (lo_var and lo.get("var") == hi.get("var"))
                or (not lo_var and lo == hi))
            if same:
                end_duration_value = {"seconds": lo}
            else:
                end_duration_value = {"min_seconds": lo, "max_seconds": hi}
        elif self.evend_var.get() == self.EVEND_COND:
            err, conds = self.evend_cond.collect(
                tr('イベント {0} 終了条件').format(ev_id),
                self._string_var_names(), mark=self._want_mark)
            if err:
                return err
            end_cond_value = conds

        # 書き戻し
        channels = {}
        for ch_id in CHANNEL_IDS:
            collected = self.channel_sections[ch_id].collect()
            if collected is not None:
                channels[ch_id] = collected
        if channels:
            ev["channels"] = channels
            ev["device"] = {t: v.get() for t, v in self.device_vars.items()
                            if v.get() != tr("なし")}
        else:
            # 音声なし/動画のみイベント: channels キー省略で保存
            # (device も担当先が無く無意味なので省略)
            ev.pop("channels", None)
            ev.pop("device", None)
        # 動画は =52 でチャンネルのアイテムになったため、直下の video は
        # 書かない(旧形式で読んだものは _load_raw が移行済み)
        ev.pop("video", None)
        # 動画chのシークバーは動画に固定=seek_channel は保存しない
        sk = None if _raw_has_video(channels) \
            else self._seek_channel_out(channels)
        if sk:
            ev["seek_channel"] = sk
        else:
            ev.pop("seek_channel", None)
        # =256: BGM(引き継ぐ=キー省略 / オフ / 指定)
        err, bgm = self._collect_bgm(tr('イベント {0}').format(ev_id))
        if err:
            return err
        if bgm is not None:
            ev["bgm"] = bgm
        else:
            ev.pop("bgm", None)
        if self.evend_var.get() == self.EVEND_INFINITE:
            ev["end"] = {"type": "none"}   # =168
        elif end_channel is not None:
            ev["end"] = {"type": "channel", "channel": end_channel}
        elif end_duration_value is not None:
            ev["end"] = {"type": "duration", **end_duration_value}
        elif end_cond_value is not None:
            ev["end"] = {"type": "cond", "when": end_cond_value}
        else:
            ev.pop("end", None)   # 全チャンネル終了 = end キー無し
        if self._ev_ops:
            ev["on_start"] = self._ev_ops
        else:
            ev.pop("on_start", None)
        if self._ev_end_ops:
            ev["on_end"] = self._ev_end_ops
        else:
            ev.pop("on_end", None)
        err, value = self._collect_next(ev_id)
        if err:
            return err
        ev["next"] = value
        err, adv = self._collect_advance()
        if err:
            return tr('イベント {0}: {1}').format(ev_id, err)
        if adv is not None:
            ev["advance"] = adv
        else:
            ev.pop("advance", None)
        if self.start_var.get():
            self.data["start"] = ev_id
        return None

    @staticmethod
    def _validate_new_id(new: str, existing, label: str) -> str | None:
        if not new:
            return tr('{0}を空にはできません').format(label)
        if new in existing:
            return tr('{0}「{1}」は既に使われています').format(label, new)
        return None

    @staticmethod
    def _rename_in_next(nxt, old: str, new: str):
        """next 値に含まれる遷移先 old を new へ置換して返す。"""
        if nxt == old:
            return new
        if not isinstance(nxt, dict):
            return nxt
        lst = []
        for ent in nxt.get("random") or []:
            if isinstance(ent, str):
                lst.append(new if ent == old else ent)
            elif isinstance(ent, dict):
                if ent.get("to") == old:
                    ent["to"] = new
                lst.append(ent)
            else:
                lst.append(ent)
        if "random" in nxt:
            nxt["random"] = lst
        for ent in nxt.get("choice") or []:
            if isinstance(ent, dict) and ent.get("to") == old:
                ent["to"] = new
        for row in nxt.get("cond") or []:
            if isinstance(row, dict) and row.get("to") == old:
                row["to"] = new
        if isinstance(nxt.get("else"), str) and nxt["else"] == old:
            nxt["else"] = new
        for key in ("when_exhausted", "timeout", "default", "input"):
            sub = nxt.get(key)
            if isinstance(sub, dict) and sub.get("to") == old:
                sub["to"] = new
        return nxt

    def _commit_event_rename(self):
        old = self.selected
        if not old or old not in self.data["events"]:
            return
        new = self.event_id_var.get().strip()
        if new == old:
            self.event_id_var.set(old)   # 前後空白の正規化
            return
        err = self._validate_new_id(
            new, set(self.data["events"]) - {old}, tr("イベント名"))
        if err:
            self._report("error", tr("リネームできません"), err)
            self.event_id_var.set(old)
            return
        # 先に現在のパネル内容を old へ反映してからキーを付け替える
        applied = self._apply_panel()
        if applied:
            self._report("error", tr("編集エラー"), applied)
            self.event_id_var.set(old)
            return
        self._rename_event(old, new)
        self._clear_message()

    def _rename_event(self, old: str, new: str):
        self.data["events"] = {(new if k == old else k): v
                               for k, v in self.data["events"].items()}
        if self.data.get("start") == old:
            self.data["start"] = new
        for ev in self.data["events"].values():
            if "next" in ev:
                ev["next"] = self._rename_in_next(ev.get("next"), old, new)
            # =275: ステート移行の選択肢のイベント宛て行き先も追従
            for st in (ev.get("states") or {}).values():
                t = st.get("transition") if isinstance(st, dict) else None
                if not isinstance(t, dict):
                    continue
                for ent in t.get("choice") or []:
                    to = ent.get("to") if isinstance(ent, dict) else None
                    if isinstance(to, dict) and to.get("event") == old:
                        to["event"] = new
                draw = t.get("default")
                if isinstance(draw, dict) and isinstance(draw.get("to"), dict) \
                        and draw["to"].get("event") == old:
                    draw["to"]["event"] = new
        wlist = self.data.get("watch")
        if isinstance(wlist, list):
            for w in wlist:
                if isinstance(w, dict) and w.get("to") == old:
                    w["to"] = new
        self.selected = new
        self._load_panel(new)
        self._redraw_canvas()

    def _add_event(self):
        # 先に現在のパネルを反映してから追加する
        # (後で反映すると、末尾イベントへのnext設定が旧値で上書きされるため)
        self._commit_pending_renames()   # =100①
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return
        n = 1
        while f"event{n}" in self.data["events"]:
            n += 1
        new_id = f"event{n}"
        self.data["events"][new_id] = json.loads(json.dumps(DEFAULT_EVENT))
        # チェーン末尾が未接続(next無し)のときだけつなぐ(分岐設定は壊さない)
        chain, _ = self._chain_order()
        if chain:
            if self.data["events"][chain[-1]].get("next") is None:
                self.data["events"][chain[-1]]["next"] = new_id
        else:
            self.data["start"] = new_id
        self.selected = new_id
        self._load_panel(new_id)
        self._redraw_canvas()

    @staticmethod
    def _copy_base(name: str) -> str:
        """末尾の _<数字> を除いた語幹を返す。

        コピーは完了後にコピー先を選択するため、連続コピーは「直前のコピーを
        選んだ状態での再コピー」になる。語幹ベースで連番を振ることで
        E1→E1_1→E1_2→… と伸びる(E1_1_1 のようにならない)。
        """
        stem, sep, suf = name.rpartition("_")
        if sep and stem and suf.isdigit():
            return stem
        return name

    @staticmethod
    def _unique_copy_id(existing, base: str) -> str:
        """existing に無い「base_1, base_2, …」の最初の未使用IDを返す。"""
        n = 1
        while f"{base}_{n}" in existing:
            n += 1
        return f"{base}_{n}"

    def _copy_event(self):
        """選択中のイベントを複製する。

        - 除外: イベントの next(終了時の遷移先)のみ。ループを生まないよう
          コピー先の next は空にする。それ以外(全チャンネル/音声/トラック・
          on_start/on_end・終了条件・device・ステート形式なら全ステートと内部移行)
          はすべて複製する。
        - 接続先: 「＋イベント追加」と同じくチェーン末尾(末尾の next が空のときのみ)。
        - コピー後はコピー先を選択する。編集内容にエラーがあればコピーしない。
        """
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        err = self._apply_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        base = self._copy_base(ev_id)
        new_id = self._unique_copy_id(self.data["events"], base)
        dup = json.loads(json.dumps(self.data["events"][ev_id]))
        dup["next"] = None   # 終了時の遷移先はコピーしない(ループ防止)
        dup.pop("pos", None)  # =299: 手動配置の座標は複製しない(重なるため)
        self.data["events"][new_id] = dup
        # チェーン末尾が未接続(next無し)のときだけつなぐ(分岐設定は壊さない)
        chain, _ = self._chain_order()
        if chain:
            if self.data["events"][chain[-1]].get("next") is None:
                self.data["events"][chain[-1]]["next"] = new_id
        else:
            self.data["start"] = new_id
        self.selected = new_id
        self._load_panel(new_id)
        self._redraw_canvas()

    def _open_import_dialog(self):
        """「他から取り込み」: 取り込み元jsonを選び ImportDialog を開く。"""
        err = self._apply_panel()
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        path = _pkg().filedialog.askopenfilename(
            title=tr("取り込み元のシナリオを選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("シナリオ"), "*.json"), (tr("すべて"), "*.*")],
            parent=self,
        )
        if not path:
            return
        _remember_dialog_dir(path)
        try:
            src = _load_raw(path)
        except Exception as e:
            self._report("error", tr("読み込みエラー"), str(e))
            return
        if not isinstance(src.get("events"), dict) or not src["events"]:
            self._report("error", tr("読み込みエラー"),
                         tr("取り込み元にイベントがありません"))
            return
        ImportDialog(self, src, os.path.dirname(os.path.abspath(path)),
                     os.path.basename(path))

    def _perform_import(self, src_data, src_dir, ids):
        """選択イベントを self.data へ取り込む(ImportDialog から呼ばれる)。

        - ID衝突は語幹ベースの連番で自動改名(_unique_copy_id)。取り込み
          イベント同士の相互参照は新IDへ追従する。
        - 取り込み対象外への遷移先は「遷移なし」へ置換(_remap_next_refs)。
          取り込み先に偶然同名のイベントがあっても誤接続しない(仕様)。
        - 素材パスは取り込み元フォルダ基準の絶対パスへ書き換えるだけにし、
          保存時の既存フロー(外部素材警告→素材コピー+相対パス化=33)に委ねる。
        - 参照している変数の宣言を取り込み元 vars からマージ(同名は
          取り込み先を優先=上書きしない)。watch(トップレベル)は対象外。
        - チェーン末尾の next が未接続なら先頭の取り込みイベントへ接続
          (_copy_event と同じ規則)。取り込み後は先頭を選択する。
        """
        events = self.data.setdefault("events", {})
        # 新IDの割り当て(元ファイルの定義順=ids順)
        id_map = {}
        taken = set(events)
        for sid in ids:
            nid = sid if sid not in taken \
                else self._unique_copy_id(taken, self._copy_base(sid))
            id_map[sid] = nid
            taken.add(nid)
        imported = {}
        for sid in ids:
            imported[id_map[sid]] = json.loads(
                json.dumps(src_data["events"][sid]))
        # 参照書き換え(集合内→新ID / 集合外→遷移なし)
        for ev in imported.values():
            self._remap_next_refs(ev, id_map)
        # 素材パスの絶対化(相対→取り込み元フォルダ基準)
        def to_abs(_kind, p):
            if not p or os.path.isabs(p):
                return p
            return os.path.normpath(os.path.join(src_dir, p))
        _map_item_paths({"events": imported}, to_abs)
        # 参照変数の宣言マージ
        added_vars = self._merge_var_decls(src_data, imported)
        events.update(imported)
        # チェーン末尾が未接続なら先頭の取り込みイベントへ接続
        first = id_map[ids[0]]
        chain, _ = self._chain_order()
        if chain:
            tail = chain[-1]
            if tail not in imported and events[tail].get("next") is None:
                events[tail]["next"] = first
        self.selected = first
        self._update_vars_btn()
        self._load_panel(first)
        self._redraw_canvas()
        renamed = [f"{s}→{n}" for s, n in id_map.items() if s != n]
        msg = tr("{0} 件のイベントを取り込みました").format(len(ids))
        if renamed:
            msg += tr("(ID重複のため改名: {0})").format(", ".join(renamed))
        if added_vars:
            msg += tr("(変数を追加: {0})").format(", ".join(added_vars))
        self._report("ok", tr("取り込み完了"), msg)

    @staticmethod
    def _remap_next_refs(ev, id_map):
        """取り込みイベントの next 参照を書き換える(in-place)。

        規則: 遷移先が id_map(取り込み集合)内→新IDへ / 集合外→除去
        (=「遷移なしへ置換」)。分岐形式ごとの除去の作法は
        _perform_delete_event の参照修復と同じ。cond は行が全て対象外なら
        else へ直行(else も対象外なら遷移なし)。random は候補が全て
        対象外なら遷移なし(random は1件以上必須のため else へは畳まない)。
        """
        def m(to):
            return id_map.get(to) if isinstance(to, str) else None

        nxt = ev.get("next")
        if isinstance(nxt, str):
            ev["next"] = m(nxt)
            return
        if not isinstance(nxt, dict):
            return
        if "choice" in nxt:
            lst = []
            for ent in nxt.get("choice") or []:
                if not isinstance(ent, dict):
                    continue
                nt = m(ent.get("to"))
                if nt:
                    ent["to"] = nt
                    lst.append(ent)
            if not lst:
                ev["next"] = None
                return
            nxt["choice"] = lst
            traw = nxt.get("timeout")
            if isinstance(traw, dict) and traw.get("to"):
                nt = m(traw["to"])
                if nt:
                    traw["to"] = nt
                else:
                    traw.pop("to", None)   # 時間は残す(遷移先は先頭候補へ)
            draw = nxt.get("default")
            if isinstance(draw, dict) and draw.get("to"):
                nt = m(draw["to"])
                if nt:
                    draw["to"] = nt
                else:
                    nxt.pop("default", None)
        elif "cond" in nxt:
            rows = []
            for r in nxt.get("cond") or []:
                if not isinstance(r, dict):
                    continue
                nt = m(r.get("to"))
                if nt:
                    r["to"] = nt
                    rows.append(r)
            els = nxt.get("else")
            els_new = m(els) if isinstance(els, str) else None
            if rows:
                nxt["cond"] = rows
                nxt["else"] = els_new
            else:
                ev["next"] = els_new   # 行が全滅→else直行(それも無ければ終了)
        elif "input" in nxt:
            iraw = nxt.get("input")
            nt = m(iraw.get("to")) if isinstance(iraw, dict) else None
            if nt:
                iraw["to"] = nt
            else:
                ev["next"] = None      # input の to は必須
        elif "random" in nxt:
            lst = []
            for ent in nxt.get("random") or []:
                if isinstance(ent, str):
                    nt = m(ent)
                    if nt:
                        lst.append(nt)
                elif isinstance(ent, dict):
                    nt = m(ent.get("to"))
                    if nt:
                        ent["to"] = nt
                        lst.append(ent)
            if not lst:
                ev["next"] = None
                return
            nxt["random"] = lst
            ex = nxt.get("when_exhausted")
            if isinstance(ex, dict) and ex.get("to"):
                nt = m(ex["to"])
                if nt:
                    ex["to"] = nt
                else:
                    nxt.pop("when_exhausted", None)
            els = nxt.get("else")
            if isinstance(els, str):
                nt = m(els)
                if nt:
                    nxt["else"] = nt
                else:
                    nxt.pop("else", None)
            # 候補1つ(素の文字列)・オプションなしなら文字列形式へ戻す
            if (len(lst) == 1 and isinstance(lst[0], str)
                    and nxt.get("visited") != "exclude"
                    and "else" not in nxt):
                ev["next"] = lst[0]

    def _merge_var_decls(self, src_data, imported):
        """取り込みイベントが参照する変数の宣言をマージし、追加名を返す。

        参照の収集は取り込みイベントdictの再帰走査:
        - 任意の {"var": 名前}(値/重み/レンジ/判定式/input の変数参照)
        - VarOp の対象名({"set"/"add"/"roll": 名前})
        取り込み元 vars に宣言がある名前だけを、取り込み先に無ければ
        deep copy で追加する(同名は取り込み先を優先=上書きしない)。
        取り込み元にも宣言が無い参照はそのまま=保存時の検証(宣言必須)で
        赤マークになる。
        """
        names = set()

        def walk(o):
            if isinstance(o, dict):
                v = o.get("var")
                if isinstance(v, str):
                    names.add(v)
                for k in ("set", "add", "roll"):
                    t = o.get(k)
                    if isinstance(t, str):
                        names.add(t)
                for vv in o.values():
                    walk(vv)
            elif isinstance(o, (list, tuple)):
                for vv in o:
                    walk(vv)

        walk(imported)
        src_vars = src_data.get("vars")
        if not isinstance(src_vars, dict):
            return []
        added = []
        for n in sorted(names):
            if n in src_vars:
                dest = self.data.setdefault("vars", {})
                if n not in dest:
                    dest[n] = json.loads(json.dumps(src_vars[n]))
                    added.append(n)
        return added

    def _delete_event(self):
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        if len(self.data["events"]) <= 1:
            self._report("warn", tr("削除できません"),
                         tr("最後のイベントは削除できません"))
            return
        self._confirm(tr("イベント '{0}' を削除しますか？").format(ev_id),
                      lambda i=ev_id: self._perform_delete_event(i),
                      yes_text=tr("削除する"), warn=True)

    def _perform_delete_event(self, ev_id):
        self._clear_message()
        if not ev_id or ev_id not in self.data["events"]:
            return
        if len(self.data["events"]) <= 1:
            return
        removed_next = self.data["events"][ev_id].get("next")
        if not isinstance(removed_next, str):
            removed_next = None
        del self.data["events"][ev_id]
        # 参照の修復: 文字列nextは付け替え、分岐nextは候補から除去
        for ev in self.data["events"].values():
            # =275: ステート移行の選択肢のイベント宛て候補を除去(尽きたら
            # 移行ごと削除・既定がその先なら既定を外す)
            for st in (ev.get("states") or {}).values():
                t = st.get("transition") if isinstance(st, dict) else None
                if not isinstance(t, dict) or not isinstance(t.get("when"), dict) \
                        or t["when"].get("type") != "choice":
                    continue
                ents = [e for e in t.get("choice") or []
                        if not (isinstance(e, dict)
                                and isinstance(e.get("to"), dict)
                                and e["to"].get("event") == ev_id)]
                if not ents:
                    st.pop("transition", None)
                    continue
                t["choice"] = ents
                draw = t.get("default")
                if isinstance(draw, dict) and isinstance(draw.get("to"), dict) \
                        and draw["to"].get("event") == ev_id:
                    t.pop("default", None)
            nxt = ev.get("next")
            if nxt == ev_id:
                ev["next"] = removed_next if removed_next != ev_id else None
            elif isinstance(nxt, dict) and "choice" in nxt:
                lst = [ent for ent in nxt.get("choice") or []
                       if ent.get("to") != ev_id]
                if not lst:
                    ev["next"] = None
                    continue
                nxt["choice"] = lst
                traw = nxt.get("timeout")
                if isinstance(traw, dict) and traw.get("to") == ev_id:
                    traw.pop("to", None)   # 時間指定は残す(遷移先は先頭候補に戻る)
                draw = nxt.get("default")
                if isinstance(draw, dict) and draw.get("to") == ev_id:
                    nxt.pop("default", None)
            elif isinstance(nxt, dict) and "cond" in nxt:
                rows = [r for r in nxt.get("cond") or []
                        if not (isinstance(r, dict) and r.get("to") == ev_id)]
                if nxt.get("else") == ev_id:
                    nxt["else"] = None
                if not rows:
                    # 条件行が無くなったらelse先へ縮退(文字列 or null)
                    ev["next"] = nxt.get("else")
                else:
                    nxt["cond"] = rows
            elif isinstance(nxt, dict) and "input" in nxt:
                iraw = nxt.get("input")
                if isinstance(iraw, dict) and iraw.get("to") == ev_id:
                    # 遷移先が消えた数値入力はnextごと除去(toは必須のため)
                    ev["next"] = None
            elif isinstance(nxt, dict):
                lst = []
                for ent in nxt.get("random") or []:
                    to = ent if isinstance(ent, str) else ent.get("to")
                    if to != ev_id:
                        lst.append(ent)
                if not lst:
                    ev["next"] = None
                    continue
                nxt["random"] = lst
                ex = nxt.get("when_exhausted")
                if isinstance(ex, dict) and ex.get("to") == ev_id:
                    nxt.pop("when_exhausted", None)
                # 候補1つ・オプションなしなら文字列へ戻す
                if (len(lst) == 1 and isinstance(lst[0], str)
                        and nxt.get("visited") != "exclude"):
                    ev["next"] = lst[0]
        if self.data.get("start") == ev_id:
            self.data["start"] = removed_next if removed_next in self.data["events"] \
                else next(iter(self.data["events"]))
        # トップレベルwatchの修復: 遷移先が消えたトリガーを除去
        if isinstance(self.data.get("watch"), list):
            kept = [w for w in self.data["watch"]
                    if not (isinstance(w, dict) and w.get("to") == ev_id)]
            if kept:
                self.data["watch"] = kept
            else:
                self.data.pop("watch", None)
        # 監視(watch)の修復: 削除イベントを遷移先とする監視を取り除く
        wlist = self.data.get("watch")
        if isinstance(wlist, list):
            wlist = [w for w in wlist
                     if not (isinstance(w, dict) and w.get("to") == ev_id)]
            if wlist:
                self.data["watch"] = wlist
            else:
                self.data.pop("watch", None)
        self.selected = None
        self.sel_state = None
        self._select(self.data["start"])
