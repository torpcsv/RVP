"""再生エンジン: 再生ログ・変数操作(ops)・監視(watch)(ScenarioPlayer の mixin)。"""
from __future__ import annotations

import os
import random
from ..i18n import tr



class _ScenarioPlayerLogMixin:
    """ScenarioPlayer の mixin(=301 分割)。再生ログ・変数操作(ops)・監視(watch)"""

    def _log(self, kind: str, text: str) -> None:
        """⑤変数・イベントログへ1行追記する(=54でアイテム/警告に拡充)。

        kind: "event" / "state" / "chan"(解釈後のチャンネル設定) /
        "item"(再生したアイテム) / "warn"(警告) / "end"(次へ進んだ理由) /
        "var"(=70 変数操作) / "choice"・"input"(=70 選択肢/数値入力の表示)。
        表示は main._update_run_log が担当する。
        """
        self.state["run_log"] = self.state["run_log"] + ((kind, text),)

    def _log_item(self, channel_id: str, item) -> None:
        """再生を開始したアイテムをログへ残す。

        「順番に再生/ランダム再生が効いているか」「どの動画のどの区間が
        再生されたか」を後から確認できるようにするため(ユーザー要望)。
        """
        path = item.audio or item.video
        label = os.path.basename(path) if path else tr("(スクリプト)")
        # =59: 区間は音声・動画・スクリプトのすべてに付くようになった
        if item.has_range:
            start = f"{item.start_s:g}"
            end = "" if item.end_s is None else f"{item.end_s:g}"
            label = f"{label} {start}〜{end}s"
        self._log("item", f"{channel_id}: {label}")

    def _log_manual_jump(self, jump) -> None:
        """手動の▶▶スキップ/◀◀巻き戻しを遷移理由としてログへ残す(=57)。

        自動の終了条件と区別できるよう「手動」と明記する。
        jump が ("goto", to) のときは選択肢/監視(watch)側で理由を
        記録済みなので何もしない。
        """
        if jump == "next":
            self._log("end", tr("イベント終了: 手動でスキップ(▶▶)"))
        elif jump == "back":
            self._log("end", tr("イベント終了: 手動で巻き戻し(◀◀)"))

    @staticmethod
    def _fmt_val(v) -> str:
        """ログ表示用の値の整形(小数は余分な0を出さない)。"""
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    @classmethod
    def _fmt_signed(cls, v) -> str:
        """加算の値を符号つきで表す(+5 / -20)。"""
        try:
            return ("+" if float(v) >= 0 else "") + cls._fmt_val(v)
        except (TypeError, ValueError):
            return cls._fmt_val(v)

    def _log_var(self, name: str, src: str, before, after, raw,
                 detail: str) -> None:
        """変数操作1件をログへ残す(=70)。

        形は「名前: 変更前 → 変更後 (種別 値) [発火場所]」。値が変わらなかった
        場合も残す(操作されたこと自体が分かるように=ユーザー決定)。宣言の
        min/max で丸められたときはその旨も添える。
        """
        txt = "{0}: {1} → {2} ({3})".format(
            name, self._fmt_val(before), self._fmt_val(after), detail)
        if raw != after:
            try:
                over = float(raw) > float(after)
            except (TypeError, ValueError):
                over = False
            txt += " (" + (tr("上限で丸め") if over else tr("下限で丸め")) + ")"
        if src:
            txt += " [" + src + "]"
        self._log("var", txt)

    def _apply_ops(self, ops, src: str = "") -> None:
        """変数操作を順に実行する(宣言のmin/maxでクランプ)。

        src(=70) は「どこで発火したか」のログ用ラベル(イベント開始時など)。
        """
        if not ops:
            return
        for op in ops:
            decl = self._var_decls.get(op.name)
            if decl is None:
                continue   # 読み込み時に検証済みのため通常到達しない
            before = self.vars.get(op.name)
            refs = []
            if op.kind == "roll":
                # [min, max] の一様乱数(整数, 両端含む)を代入。min>max は入替。
                lo = self.vars[op.value_var] if op.value_var is not None \
                    else op.value
                hi = self.vars[op.value2_var] if op.value2_var is not None \
                    else op.value2
                if op.value_var is not None:
                    refs.append(op.value_var)
                if op.value2_var is not None:
                    refs.append(op.value2_var)
                lo, hi = int(round(float(lo))), int(round(float(hi)))
                if lo > hi:
                    lo, hi = hi, lo
                raw = random.randint(lo, hi)
                detail = tr("乱数 {0}〜{1}").format(lo, hi)
            elif op.kind == "eval":
                # =126 条件式: 成立=1 / 不成立=0 を代入(クランプは共通処理)
                c = op.cond
                rhs = c.value_var if c.value_var is not None else c.value
                raw = 1 if c.eval(self.vars) else 0
                detail = tr("条件式 {0} → {1}").format(
                    f"{c.name} {c.op} {self._fmt_val(rhs)}", raw)
                if c.value_var is not None:
                    refs.append(c.value_var)
            else:
                value = self.vars[op.value_var] if op.value_var is not None \
                    else op.value
                if op.value_var is not None:
                    refs.append(op.value_var)
                if op.kind == "add":
                    detail = tr("加算 {0}").format(self._fmt_signed(value))
                    raw = self.vars[op.name] + value
                elif op.kind == "mul":
                    # =75: 乗算。フラグに ×1/×0 を掛けて「全yes判定」等に使う
                    detail = tr("乗算 {0}").format(self._fmt_val(value))
                    raw = self.vars[op.name] * value
                else:
                    detail = tr("代入 {0}").format(self._fmt_val(value))
                    raw = value
            if refs:
                detail += " ←" + ",".join(refs)
            self.vars[op.name] = decl.clamp(raw)
            self._log_var(op.name, src, before, self.vars[op.name], raw, detail)
        self.state["vars"] = dict(self.vars)
        self._check_watches()

    def _check_watches(self) -> None:
        """監視(watch)を宣言順に評価し、最初に成立した1件を発火する。

        - 遷移先(to)のイベント再生中はその監視を評価しない(自明なループ防止)
        - once=True は1回の再生につき1度だけ発火
        - graceful: ワインドダウン(再生中の音声を終えてから)でイベントを終了し遷移
        - interrupt: 即時打ち切りで遷移(▶▶スキップと同様)
        いずれも遷移は履歴に積まれ、◀◀で元のイベントへ戻れる。
        """
        if not self._watches or not self._playing or self._stop_requested:
            return
        current = self.state.get("event_id")
        for i, w in enumerate(self._watches):
            if w.once and i in self._watch_fired:
                continue
            if w.to == current:
                continue
            if not all(c.eval(self.vars) for c in w.conds):
                continue
            if w.mode == "interrupt":
                if self._jump is not None:
                    continue   # 既存のジャンプを上書きしない
                self._watch_fired.add(i)
                self._jump = ("goto", w.to)
                self._stop_all_audio()
            else:
                if self._watch_goto is not None:
                    continue   # 先に成立した保留を優先
                self._watch_fired.add(i)
                self._watch_goto = w.to
                self._winding_down = True
            return
