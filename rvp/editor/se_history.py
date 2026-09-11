"""シナリオ編集: 元に戻す/やり直す(スナップショット履歴 =277)(mixin)。"""
from __future__ import annotations

import copy
from ..i18n import tr



class _ScenarioEditorHistoryMixin:
    """ScenarioEditor の mixin(=301 分割)。元に戻す/やり直す(スナップショット履歴 =277)"""

    def _hist_snapshot(self) -> dict:
        return {"data": copy.deepcopy(self.data),
                "detail": self._detail_text,
                "device": self.device_enabled,
                "bgm": self.bgm_enabled,
                "sel": self.selected,
                "state": self.sel_state}

    def _hist_key_live(self):
        """比較用キー(生の data から。title はファイル名追従なので除外)。"""
        d = {k: v for k, v in self.data.items() if k != "title"}
        return (d, self._detail_text, self.device_enabled, self.bgm_enabled)

    @staticmethod
    def _hist_key_snap(snap: dict):
        d = {k: v for k, v in snap["data"].items() if k != "title"}
        return (d, snap["detail"], snap["device"], snap["bgm"])

    def _hist_reset(self):
        """履歴を空にして現在を起点にする(起動時)。"""
        self._hist_undo = []
        self._hist_redo = []
        self._hist_last = self._hist_snapshot()
        self._hist_update_buttons()

    def _hist_check(self):
        """チェックポイント: 直前スナップショットと違えば1段積む。

        変更が無ければ選択位置だけを直前スナップショットへ写す(次に積む
        ときの「戻り先」になる)。
        """
        if self._hist_last is None or self._hist_suspend:
            return
        if self._hist_key_live() != self._hist_key_snap(self._hist_last):
            self._hist_undo.append(self._hist_last)
            if len(self._hist_undo) > self.HIST_MAX:
                del self._hist_undo[0]
            self._hist_redo = []
            self._hist_last = self._hist_snapshot()
        else:
            self._hist_last["sel"] = self.selected
            self._hist_last["state"] = self.sel_state
        self._hist_update_buttons()

    def _hist_normalize(self, apply_body):
        """読み込んだパネルを一度書き戻し、その「正規化」を起点へ畳み込む。

        JSONを読み込んだ直後の初回反映は、値を変えていなくても自動デバイス
        割当・秒の float 化・min/max 分解・next:null など正規化で raw と
        差が出る。それを1段の履歴にすると「何もしていないのに戻る」ことに
        なるので、読み込み直後に書き戻して直前スナップショットへ写す。
        本当の未記録変更が残っていれば先に積む(取りこぼし防止)。
        """
        if self._hist_last is None or self._hist_suspend:
            return
        if self._hist_key_live() != self._hist_key_snap(self._hist_last):
            self._hist_check()
        try:
            err = apply_body()
        except Exception:
            err = "exc"
        if err is not None:
            self._pending_marks = []   # 検証エラーは今は表示しない(次の反映で)
            return
        if self._hist_key_live() != self._hist_key_snap(self._hist_last):
            self._hist_last["data"] = copy.deepcopy(self.data)

    def _hist_update_buttons(self):
        for btn, hist in ((getattr(self, "undo_btn", None), self._hist_undo),
                          (getattr(self, "redo_btn", None), self._hist_redo)):
            if btn is not None:
                try:
                    btn.configure(state="normal" if hist else "disabled")
                except Exception:
                    pass

    def _hist_prepare(self) -> bool:
        """Q2-A: 入力途中の内容を先に反映してから履歴を操作する。

        反映で検証エラーなら履歴操作を行わず False。
        """
        if self._hist_suspend or self._hist_last is None:
            return False
        self._commit_pending_renames()
        if self.selected and self.selected in self.data["events"]:
            err = self._apply_panel()
            if err:
                self._report("error", tr("編集エラー"), err)
                return False
        self._hist_check()
        return True

    def _undo(self):
        if not self._hist_prepare() or not self._hist_undo:
            return
        self._hist_redo.append(self._hist_last)
        self._hist_restore(self._hist_undo.pop())
        self._report("ok", tr("元に戻しました"),
                     tr("あと{0}段").format(len(self._hist_undo)))

    def _redo(self):
        if not self._hist_prepare() or not self._hist_redo:
            return
        self._hist_undo.append(self._hist_last)
        self._hist_restore(self._hist_redo.pop())
        self._report("ok", tr("やり直しました"),
                     tr("あと{0}段").format(len(self._hist_redo)))

    def _hist_restore(self, snap: dict):
        """スナップショットをモデルへ戻し、一覧・図・パネルを組み立て直す。"""
        self._hist_suspend = True
        try:
            title = self.data.get("title", "")
            self.data = copy.deepcopy(snap["data"])
            self.data["title"] = title       # タイトルはファイル名追従のまま
            self._detail_text = snap["detail"]
            self.device_enabled = snap["device"]
            self.bgm_enabled = snap["bgm"]
            self._apply_device_enabled()
            self._apply_bgm_enabled()
            events = self.data["events"]
            sel = snap["sel"]
            if sel not in events:
                sel = self.data.get("start")
                if sel not in events:
                    sel = next(iter(events), None)
            self.selected = sel
            if sel is not None:
                self._load_panel(sel)
                st = snap["state"]
                ev = events[sel]
                if "states" in ev and st in ev["states"] \
                        and st != self.sel_state:
                    self._load_state_panel(st)
            self._redraw_canvas()
        finally:
            self._hist_suspend = False
        self._hist_last = snap
        self._hist_last["sel"] = self.selected
        self._hist_last["state"] = self.sel_state
        self._hist_update_buttons()
