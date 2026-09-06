"""ROTATE(ufo / a10cyclonesa)トラックの動作元(funscript または CSV)の読み込み。

funscript も CSV も、統一表現 **RotateTimeline** に正規化して扱う。

- channels: 1 または 2(タイプB=ufotwの左右独立のみ2)
- steps: 各行 = (at_ms, ((clockwise, frac0.0-1.0) を channels 個))
  clockwise=正回転か / frac=速度率(0.0=停止 〜 1.0=最大)
- source_kind: "funscript" または "csv"
  funscript は既存の pos(0-100)送信APIで駆動でき、テスト互換のため区別する。

CSVフォーマット(時刻は100ミリ秒単位):
- タイプA(3列): 時刻, 方向(0=逆/1=正), 速度(0-100)               → 1ch
- タイプB(5列, ufotw): 時刻, 左方向, 左速度, 右方向, 右速度        → 2ch
"""

from dataclasses import dataclass

from .funscript import Funscript
from .i18n import tr


@dataclass
class RotateStep:
    at: int                 # ミリ秒
    vals: tuple             # ((clockwise: bool, frac: float), ...) 長さ=channels


class RotateTimeline:
    def __init__(self, channels: int, steps: list, source_kind: str,
                 path: str = ""):
        self.channels = channels
        self.steps = sorted(steps, key=lambda s: s.at)
        self.source_kind = source_kind    # "funscript" / "csv"
        self.path = path
        self.ats = [s.at for s in self.steps]
        # =121: 区間終了が明示された切り出しの長さ(Funscript と同じ)。
        self.end_override_ms: int | None = None

    @property
    def duration_ms(self) -> int:
        last = self.steps[-1].at if self.steps else 0
        if self.end_override_ms is not None:
            return max(last, self.end_override_ms)
        return last

    def sliced(self, start_ms: float, end_ms: float | None = None) -> "RotateTimeline":
        """区間[start_ms, end_ms]を切り出し、先頭を0msにずらして返す(=59)。

        Funscript.sliced と同じ規則(区間の先頭が0秒)。
        =121: end_ms 明示時は duration_ms=区間の長さ(末尾の停止区間を含む)。
        """
        lo = max(0.0, float(start_ms))
        hi = None if end_ms is None else float(end_ms)
        steps = [RotateStep(int(round(st.at - lo)), st.vals)
                 for st in self.steps
                 if st.at >= lo and (hi is None or st.at <= hi)]
        sl = RotateTimeline(self.channels, steps, self.source_kind,
                            path=self.path)
        if hi is not None:
            sl.end_override_ms = max(0, int(round(hi - lo)))
        return sl

    @classmethod
    def from_funscript(cls, funscript: Funscript) -> "RotateTimeline":
        """既読の Funscript から1chタイムラインを作る。

        pos=50停止 / 100正回転max / 0負回転max → (clockwise, frac)。
        """
        steps = [
            RotateStep(a.at, ((a.pos >= 50, abs(a.pos - 50) / 50.0),))
            for a in funscript.actions
        ]
        return cls(1, steps, "funscript", getattr(funscript, "path", ""))


def _to_int(s: str, where: str) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        raise ValueError(tr("CSV {0}: 数値が不正です '{1}'").format(where, s))


def _dir(s: str, where: str) -> bool:
    v = _to_int(s, where)
    if v not in (0, 1):
        raise ValueError(tr("CSV {0}: 方向は 0(逆) か 1(正) です '{1}'").format(where, s))
    return v == 1


def _speed_frac(s: str, where: str) -> float:
    v = float(_to_int(s, where))
    v = max(0.0, min(100.0, v))
    return v / 100.0


def load_csv(path: str) -> RotateTimeline:
    """CSV(タイプA=3列 / タイプB=5列)を RotateTimeline へ読み込む。"""
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            while parts and parts[-1] == "":   # 末尾の空カラムを落とす
                parts.pop()
            if not parts:
                continue
            rows.append((lineno, parts))
    if not rows:
        return RotateTimeline(1, [], "csv", path)

    ncol = len(rows[0][1])
    if ncol == 3:
        channels = 1
    elif ncol == 5:
        channels = 2
    else:
        raise ValueError(
            tr("CSV {0}: 列数は 3(タイプA) か 5(タイプB) です(先頭行={1}列)").format(
                path, ncol))

    steps = []
    for lineno, p in rows:
        where = tr("{0}行目").format(lineno)
        if len(p) != ncol:
            raise ValueError(
                tr("CSV {0}: 列数が揃っていません({1}列目で{2}列)").format(
                    path, lineno, len(p)))
        at = _to_int(p[0], where)
        if at < 0:
            raise ValueError(tr("CSV {0}: 時刻が負です").format(where))
        at_ms = at * 100   # 100ミリ秒単位 → ミリ秒
        if channels == 1:
            vals = ((_dir(p[1], where), _speed_frac(p[2], where)),)
        else:
            vals = ((_dir(p[1], where), _speed_frac(p[2], where)),
                    (_dir(p[3], where), _speed_frac(p[4], where)))
        steps.append(RotateStep(at_ms, vals))
    return RotateTimeline(channels, steps, "csv", path)


def load_funscript(path: str) -> RotateTimeline:
    return RotateTimeline.from_funscript(Funscript.load(path))


def load_rotate_source(path: str) -> RotateTimeline:
    """拡張子で funscript / CSV を判別して RotateTimeline を返す。"""
    if path.casefold().endswith(".csv"):
        return load_csv(path)
    return load_funscript(path)
