"""再生エンジン: 選択肢・数値入力(表示・監視・待機・解決)(ScenarioPlayer の mixin)。"""
from __future__ import annotations

import asyncio
from ..scenario import VarOp
from ..i18n import tr

from .clock import PlaybackClock


class _ScenarioPlayerInteractMixin:
    """ScenarioPlayer の mixin(=301 分割)。選択肢・数値入力(表示・監視・待機・解決)"""

    async def choose(self, index: int) -> None:
        """選択肢のボタン押下(UIスレッドからサブミットされる)。"""
        if (self.state["choice"] is None or self._watch_goto is not None
                or isinstance(self._jump, tuple)):
            return
        if 0 <= index < len(self._choice_entries):
            ent = self._choice_entries[index]
            self._choice_result_ops = ent.ops
            self._choice_result_index = index      # =70 ログ用(番号とラベル)
            self._choice_result_event = bool(getattr(ent, "to_event", False))
            self._choice_result = ent.to

    async def submit_input(self, value: float, event_id: str | None = None) -> None:
        """数値入力の決定ボタン押下(UIスレッドからサブミットされる)。

        受理範囲外の値は無視する(UI側が事前に検証してエラー表示する)。
        event_id 指定時は表示中の入力と一致するときだけ受理する
        (遷移直後に届いた遅延サブミットの誤適用を防ぐ)。
        """
        st = self.state["input"]
        if (st is None or self._watch_goto is not None
                or isinstance(self._jump, tuple)):
            return
        if event_id is not None and st.get("event_id") != event_id:
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if value != value or value in (float("inf"), float("-inf")):
            return   # NaN/無限大は受理しない
        mn, mx = st.get("min"), st.get("max")
        if (mn is not None and value < mn) or (mx is not None and value > mx):
            return
        self._input_result = value

    def _show_choice(self, event, rule=None, owner: str = "event",
                     state_id: str = "") -> None:
        """選択肢を表示する(表示済みなら何もしない)。

        =275: rule/owner/state_id を渡すとステート移行の選択肢として表示する
        (UI側の識別子 event_id は「イベントID/ステートID」にして、同じ
        イベント内でイベント側の選択肢と区別できるようにする)。
        """
        if self.state["choice"] is not None:
            return
        if rule is None:
            rule = event.next_choice
        self._choice_owner = owner
        self._choice_entries = list(rule.entries)
        self._choice_result = None
        self._choice_result_event = False
        clock = PlaybackClock()
        clock.start()
        if self._paused:
            clock.pause()
        self._choice_clock = clock
        self.state["choice_remaining_ms"] = rule.timeout_ms
        ui_id = event.event_id if owner == "event" \
            else "{0}/{1}".format(event.event_id, state_id)
        self.state["choice"] = {"event_id": ui_id,
                                "labels": [e.label for e in rule.entries]}
        self._log_choice_show(rule)
        self._video_osd(tr("選択肢が表示されています(RVPウィンドウで選択)"))

    @staticmethod
    def _entry_desc(index: int, entries) -> str:
        """選択肢1件を「番号)ラベル」で表す。"""
        if entries is None or not (0 <= index < len(entries)):
            return ""
        return "{0}){1}".format(index + 1, entries[index].label)

    def _to_desc(self, to, entries, index=None, to_event=None) -> str:
        """遷移先を「番号)ラベル → イベントID」で表す(該当なしはIDのみ)。

        =275: ステート移行の選択肢でイベント宛て(to_event)なら
        「→ イベント:X」と表す(ステートIDと区別する)。
        """
        if index is None:
            index = next((i for i, e in enumerate(entries or ())
                          if e.to == to
                          and (to_event is None or e.to_event == to_event)),
                         None)
        desc = self._entry_desc(index, entries) if index is not None else ""
        if to_event is None and index is not None and entries:
            to_event = entries[index].to_event
        target = tr("イベント:{0}").format(to) if to_event else str(to)
        return (desc + " → " + target) if desc else target

    def _default_desc(self, rule) -> str:
        """タイムアウト/▶▶での既定遷移先の説明(抽選前なので方式で示す)。"""
        if rule.default_mode == "random":
            return tr("ランダム")
        if rule.default_mode == "to" and rule.default_to:
            if rule.default_to_event:
                return tr("イベント:{0}").format(rule.default_to)
            return rule.default_to
        return self._entry_desc(0, rule.entries) or "-"

    def _log_choice_show(self, rule) -> None:
        """選択肢を表示したことをログへ残す(候補一覧・制限時間・既定)。"""
        items = " ".join(self._entry_desc(i, rule.entries)
                         for i in range(len(rule.entries)))
        extras = []
        if rule.timeout_ms is not None:
            extras.append(tr("制限{0:g}秒").format(rule.timeout_ms / 1000.0))
        extras.append(tr("既定={0}").format(self._default_desc(rule)))
        self._log("choice", tr("選択肢を表示: {0}").format(items)
                  + " (" + " / ".join(extras) + ")")

    def _clear_choice(self) -> None:
        if self.state["choice"] is not None:
            self._video_osd("")
        self.state["choice"] = None
        self.state["choice_remaining_ms"] = None
        self._choice_clock = None
        self._choice_entries = []
        self._choice_result = None
        self._choice_result_ops = ()
        self._choice_result_index = None
        self._choice_result_event = False
        self._choice_owner = "event"

    def _choice_resolution(self, rule, owner: str = "event") -> str | None:
        """選択済み/タイムアウトなら遷移先を返す(変数操作もここで発火)。未解決はNone。

        =275: owner が表示中の選択肢の持ち主と違えば何もしない(イベント側の
        監視がステート移行の選択肢の結果を消費しないため)。行き先が
        イベントか(ステート移行の選択肢)は _choice_result_event に残す。
        """
        if self.state["choice"] is not None and self._choice_owner != owner:
            return None
        if self._choice_result:
            self._apply_ops(self._choice_result_ops, tr("選択時"))
            self._choice_result_ops = ()
            if self._watch_goto is not None or isinstance(self._jump, tuple):
                return None   # 監視(watch)が発火 → 選択は破棄
            self._log("end", tr("選択肢を選択: {0}").format(
                self._to_desc(self._choice_result, self._choice_entries,
                              self._choice_result_index)))
            return self._choice_result
        if (self.state["choice"] is not None and rule.timeout_ms is not None
                and self._choice_clock is not None):
            remaining = rule.timeout_ms - self._choice_clock.now_ms()
            self.state["choice_remaining_ms"] = max(0, int(remaining))
            if remaining <= 0:
                self._apply_ops(rule.on_timeout, tr("タイムアウト時"))
                if self._watch_goto is not None or isinstance(self._jump, tuple):
                    return None   # 監視(watch)が発火 → デフォルト遷移は破棄
                to, to_event = rule.pick_default_info()
                self._choice_result_event = to_event
                self._log("end", tr("選択肢がタイムアウト: {0}").format(
                    self._to_desc(to, rule.entries, to_event=to_event)))
                return to
        return None

    async def _monitor_choice(self, event, event_clock) -> None:
        """再生中の選択肢の表示タイミング監視と、選択/タイムアウトの検出。

        解決したら _jump=("goto", to) でイベントを中断して遷移する。
        (再生中の音声は打ち切り。▶▶スキップと同じ即時遷移の挙動)
        """
        rule = event.next_choice
        while not self._stop_requested and self._jump is None:
            if self.state["choice"] is None:
                if rule.show_mode == "start":
                    self._show_choice(event)
                elif rule.show_mode == "ms" and event_clock.now_ms() >= rule.show_ms:
                    self._show_choice(event)
                elif (rule.show_mode == "end" and self._winding_down
                        and not self._transition_winding):
                    # =275: ステート移行の巻き取り中は「イベント終了」ではない
                    self._show_choice(event)
            else:
                to = self._choice_resolution(rule, owner="event")
                if to is not None:
                    if self._jump is None:   # 監視(watch)の発火を上書きしない
                        self._jump = ("goto", to)
                    return
            await asyncio.sleep(0.05)

    async def _monitor_cond(self, event) -> None:
        """=168 判定式(常に監視): 再生中に next の判定式を評価し続ける。

        終了条件が「無限」のイベント専用。通常の変数分岐は**イベントが
        終わった瞬間**にしか評価されないため、無限のイベントでは永久に
        発火しない。そこで、選択肢の監視(_monitor_choice)と同じように
        再生中に評価し、**どれかの行が成立したらイベントを打ち切って**
        その行の遷移先へ進む(ユーザー決定 2026-08-16)。

        else は「どの行も成立しないとき」なので、監視では使わない
        (成立するまで待ち続ける=イベントは終わらない)。
        """
        rule = event.next_cond
        while not self._stop_requested and self._jump is None:
            for conds, to in rule.rows:
                if conds and all(c.eval(self.vars) for c in conds):
                    if self._jump is None:   # 監視(watch)の発火を上書きしない
                        self._log("end", tr("判定式が成立: {0}へ").format(to))
                        self._jump = ("goto", to)
                    return
            await asyncio.sleep(0.05)

    async def _wait_choice(self, event) -> str | None:
        """イベント自然終了後の待機フェーズ。選択/タイムアウトまで待つ。"""
        rule = event.next_choice
        self._show_choice(event)   # 未表示ならここで表示
        while not self._stop_requested:
            if self._watch_goto is not None or isinstance(self._jump, tuple):
                return None   # 監視(watch)の発火が選択肢より優先
            if self._jump == "back":
                return None
            if self._jump == "next":
                self._jump = None
                if rule.skip_stay:
                    # =274: 選択必須=▶▶では飛ばさない。押下は消費して
                    # 待機を続ける(何度押しても進行しない)
                    self._log("choice", tr("▶▶は無効: 選択されるまで待機します(選択必須)"))
                    continue
                # 待機中の▶▶はデフォルト遷移先(first/random/指定イベント)へ
                to = rule.pick_default()
                self._log("end", tr("選択肢を手動でスキップ(▶▶): {0}").format(
                    self._to_desc(to, rule.entries)))
                return to
            to = self._choice_resolution(rule)
            if to is not None:
                return to
            await asyncio.sleep(0.05)
        return None

    def _show_input(self, event) -> None:
        if self.state["input"] is not None:
            return
        rule = event.next_input
        self._input_result = None
        self.state["input"] = {"event_id": event.event_id,
                               "var": rule.var,
                               "label": rule.label,
                               "min": rule.vmin,
                               "max": rule.vmax}
        self._log_input_show(rule)
        self._video_osd(tr("数値入力が表示されています(RVPウィンドウで入力)"))

    def _log_input_show(self, rule) -> None:
        """数値入力を表示したことをログへ残す(=70。対象変数・受理範囲・遷移先)。"""
        txt = tr("数値入力を表示: {0}").format(rule.var)
        if rule.vmin is not None or rule.vmax is not None:
            txt += " (" + tr("範囲 {0}〜{1}").format(
                self._fmt_val(rule.vmin) if rule.vmin is not None else "",
                self._fmt_val(rule.vmax) if rule.vmax is not None else "") + ")"
        self._log("input", txt + " → " + str(rule.to))

    def _clear_input(self) -> None:
        if self.state["input"] is not None:
            self._video_osd("")
        self.state["input"] = None
        self._input_result = None

    def _input_resolution(self, rule) -> str | None:
        """入力確定なら変数へセットして遷移先を返す。未確定はNone。

        整数値はintとして格納する(JSONの整数と型を揃える)。セットは
        通常の変数操作として扱われ、宣言のmin/maxクランプと監視(watch)の
        評価が働く。watchが発火した場合は入力遷移より優先される。
        """
        if self._input_result is None:
            return None
        v = self._input_result
        self._input_result = None
        v = int(v) if float(v).is_integer() else float(v)
        self._apply_ops((VarOp(kind="set", name=rule.var, value=v),),
                        tr("数値入力"))
        if self._watch_goto is not None or isinstance(self._jump, tuple):
            return None   # 監視(watch)が発火 → 入力遷移は破棄
        self._log("end", tr("数値入力を確定: {0} → {1}").format(
            self._fmt_val(v), rule.to))
        return rule.to

    async def _monitor_input(self, event, event_clock) -> None:
        """再生中の数値入力の表示タイミング監視と、決定の検出。

        決定したら _jump=("goto", to) でイベントを中断して遷移する。
        (再生中の音声は打ち切り。選択肢ボタンと同じ即時遷移の挙動)
        """
        rule = event.next_input
        while not self._stop_requested and self._jump is None:
            if self.state["input"] is None:
                if rule.show_mode == "start":
                    self._show_input(event)
                elif rule.show_mode == "ms" and event_clock.now_ms() >= rule.show_ms:
                    self._show_input(event)
                elif (rule.show_mode == "end" and self._winding_down
                        and not self._transition_winding):
                    self._show_input(event)
            else:
                to = self._input_resolution(rule)
                if to is not None:
                    if self._jump is None:   # 監視(watch)の発火を上書きしない
                        self._jump = ("goto", to)
                    return
            await asyncio.sleep(0.05)

    async def _wait_input(self, event) -> str | None:
        """イベント自然終了後の待機フェーズ。数値入力の決定まで待つ。"""
        rule = event.next_input
        self._show_input(event)   # 未表示ならここで表示
        while not self._stop_requested:
            if self._watch_goto is not None or isinstance(self._jump, tuple):
                return None   # 監視(watch)の発火が入力より優先
            if self._jump == "back":
                return None
            if self._jump == "next":
                self._jump = None
                if rule.skip_stay:
                    # =288: 入力必須=▶▶では飛ばさない。押下は消費して待機を続ける
                    self._log("choice", tr("▶▶は無効: 入力されるまで待機します(入力必須)"))
                    continue
                # 待機中の▶▶は変数を変更せずに遷移先へ
                self._log("end",
                          tr("数値入力を手動でスキップ(▶▶) → {0}").format(rule.to))
                return rule.to
            to = self._input_resolution(rule)
            if to is not None:
                return to
            await asyncio.sleep(0.05)
        return None
