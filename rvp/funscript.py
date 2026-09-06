"""funscript ファイルの読み込み。

funscript は JSON 形式で、最低限 "actions" 配列を持つ。
各 action は {"at": ミリ秒, "pos": 0-100} を持つ。
"""

import json
from dataclasses import dataclass


@dataclass
class FunscriptAction:
    at: int   # ミリ秒
    pos: int  # 0-100


class Funscript:
    def __init__(self, actions: list[FunscriptAction], path: str = ""):
        self.actions = sorted(actions, key=lambda a: a.at)
        self.path = path
        # =121: 区間指定(=59)の終了が明示されているとき、その長さ(ms)。
        # duration_ms が最終アクションでなく**区間の長さ**を返すために使う
        # (末尾の停止・保持区間を最後まで実行する。ユーザー決定 2026-08-12)。
        self.end_override_ms: int | None = None

    @property
    def duration_ms(self) -> int:
        last = self.actions[-1].at if self.actions else 0
        if self.end_override_ms is not None:
            return max(last, self.end_override_ms)
        return last

    def sliced(self, start_ms: float, end_ms: float | None = None) -> "Funscript":
        """区間[start_ms, end_ms]を切り出し、**先頭を0msにずらした**新しい
        Funscript を返す(=59の区間指定)。

        区間の先頭を0秒として扱うのは動画(=51)と同じ方針。区間内に
        アクションが1つも無ければ空の Funscript になる(そのトラックは
        何も動かさない)。
        =121: end_ms を明示した切り出しは duration_ms が **end_ms - start_ms**
        (=区間の長さ)になる。最終アクション以降の停止・保持区間も
        アイテムの再生時間に含める(スクリプト専用チャンネル)。
        """
        lo = max(0.0, float(start_ms))
        hi = None if end_ms is None else float(end_ms)
        out = [FunscriptAction(at=int(round(a.at - lo)), pos=a.pos)
               for a in self.actions
               if a.at >= lo and (hi is None or a.at <= hi)]
        sl = Funscript(out, path=self.path)
        if hi is not None:
            sl.end_override_ms = max(0, int(round(hi - lo)))
        return sl

    @classmethod
    def load(cls, path: str) -> "Funscript":
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        raw = data.get("actions", [])
        actions = []
        for a in raw:
            try:
                at = int(a["at"])
                pos = int(a["pos"])
            except (KeyError, TypeError, ValueError):
                continue
            pos = max(0, min(100, pos))
            if at >= 0:
                actions.append(FunscriptAction(at=at, pos=pos))
        return cls(actions, path=path)
