"""再生エンジン: デバイス動作グラフ用のスナップショット(ScenarioPlayer の mixin)。"""
from __future__ import annotations

from ..scenario import (TRACK_LINEAR, TRACK_ROTATE, TRACK_ROTATE_A10,
    TRACK_TWIST, TRACK_VIBRATION)

from .common import GRAPH_SEG_LIMIT


class _ScenarioPlayerGraphMixin:
    """ScenarioPlayer の mixin(=301 分割)。デバイス動作グラフ用のスナップショット"""

    @staticmethod
    def _graph_points_funscript(fs) -> list:
        """funscript を (時刻ms, pos0-100) の列にする。"""
        return [(float(a.at), float(a.pos)) for a in fs.actions]

    def _graph_trim(self) -> None:
        while len(self.graph_segments) > GRAPH_SEG_LIMIT:
            for i, s in enumerate(self.graph_segments):
                if not s["live"]:
                    del self.graph_segments[i]
                    break
            else:
                del self.graph_segments[0]

    @staticmethod
    def _graph_points_rotate(tl, index: int) -> list:
        """RotateTimeline の1ロータ分を (時刻ms, pos0-100) の列にする。

        50=停止 / 100=正回転最大 / 0=逆回転最大(再生タブのバー表示と同じ写像)。
        """
        out = []
        for st in tl.steps:
            cw, frac = st.vals[index]
            f = max(0.0, min(1.0, float(frac)))
            out.append((float(st.at), 50.0 + f * 50.0 if cw else 50.0 - f * 50.0))
        return out

    def _graph_add(self, base: str, kind: str, points: list, clock,
                   offset_key: str, lane: str = "", rotor=None):
        if not points or clock is None:
            return None
        seg = {"base": base, "kind": kind, "points": points,
               "times": [p[0] for p in points], "clock": clock,
               "offset_key": offset_key, "lane": lane, "rotor": rotor,
               "t0": 0.0, "live": True,
               # =71: 再生を開始したイベント経過時刻。**履歴なので動かさない**。
               # 描画はこの x0 から「次の断片の x0」までの窓に限る(重なり防止)。
               # 窓の中でスクリプトの中身は t0 に従って左右へスライドする。
               "x0": self._graph_now_ms()}
        self.graph_segments.append(seg)
        self._graph_trim()
        return seg

    def _graph_add_track(self, ttype: str, src, clock) -> list:
        """実行を開始したトラックをグラフ用に登録する(戻り値=断片のリスト)。"""
        segs = []
        if ttype == TRACK_LINEAR:
            segs.append(self._graph_add(
                "linear", "linear", self._graph_points_funscript(src),
                clock, "linear"))
        elif ttype == TRACK_TWIST:
            segs.append(self._graph_add(
                "twist", "linear", self._graph_points_funscript(src),
                clock, "twist"))
        elif ttype == TRACK_VIBRATION:
            segs.append(self._graph_add(
                "vibration", "step", self._graph_points_funscript(src),
                clock, "vibration"))
        elif ttype in (TRACK_ROTATE, TRACK_ROTATE_A10):
            ufo = (ttype == TRACK_ROTATE)
            lane = "ufo" if ufo else "a10"
            base = "rotate_ufo" if ufo else "rotate_a10"
            okey = "rotate" if ufo else TRACK_ROTATE_A10
            if getattr(src, "channels", 1) >= 2:
                # タイプB(5列CSV)=ufotwの左右独立。ロータごとに1本ずつ。
                for rotor in (0, 1):
                    segs.append(self._graph_add(
                        base, "step", self._graph_points_rotate(src, rotor),
                        clock, okey, lane=lane, rotor=rotor))
            else:
                segs.append(self._graph_add(
                    base, "step", self._graph_points_rotate(src, 0),
                    clock, okey, lane=lane))
        return [s for s in segs if s]

    def _graph_now_ms(self) -> float:
        return self._event_clock.now_ms() if self._event_clock is not None else 0.0

    def _graph_t0(self, seg, now_ms: float) -> float:
        if not seg["live"] or seg["clock"] is None:
            return seg["t0"]
        return (now_ms - seg["clock"].now_ms()
                + self.offsets_ms.get(seg["offset_key"], 0))

    def _graph_finish(self, segs) -> None:
        """実行の終わった断片の位置を凍結する(過去の波形として残す)。"""
        now = self._graph_now_ms()
        for s in segs or ():
            if s["live"]:
                s["t0"] = self._graph_t0(s, now)
                s["live"] = False
                s["clock"] = None

    def _graph_freeze(self) -> None:
        """再生終了・停止でグラフの時間を止める(=69の不具合修正)。

        イベント経過クロックを止めないと、再生が終わったあともグラフだけが
        右から左へ流れ続けてしまう。表示は「直前の状態を残す」仕様なので、
        クロックを一時停止して現在位置の縦線ごと固定する。
        """
        self._graph_finish([s for s in self.graph_segments if s["live"]])
        if self._event_clock is not None:
            self._event_clock.pause()

    def _graph_key(self, seg) -> str:
        """表示上のグラフ種別キー。2ロータは左右反転を反映して振り分ける。"""
        if seg["rotor"] is None:
            return seg["base"]
        swap = bool(self.rotate_swap.get(seg["lane"], False))
        is_right = (seg["rotor"] == 1) != swap
        return seg["base"] + ("_r" if is_right else "")

    def graph_snapshot(self) -> dict:
        """グラフ表示用のスナップショット(UIスレッドから毎フレーム呼ぶ)。

        =71: 断片は**常にスクリプト全体**を持つ。重なりは「描画してよい x の窓」
        (x0 〜 同じ行の次の断片の x0)で防ぐ。窓は毎回その場で計算するので、
        巻き戻して次の断片の開始位置が右へ動けば、切り詰められていた過去の
        波形が自動的に戻ってくる。
        """
        now = self._graph_now_ms()
        segs = [{"key": self._graph_key(s), "kind": s["kind"],
                 "points": s["points"], "times": s["times"],
                 "x0": s["x0"], "x1": None,
                 "t0": self._graph_t0(s, now), "live": s["live"]}
                for s in list(self.graph_segments)]
        # 行(=表示種別)ごとに開始位置順へ並べ、次の断片の開始位置を右端にする
        rows: dict = {}
        for seg in segs:
            rows.setdefault(seg["key"], []).append(seg)
        for row in rows.values():
            row.sort(key=lambda s: s["x0"])
            for a, b in zip(row, row[1:]):
                a["x1"] = b["x0"]
        return {"now_ms": now, "segments": segs}
