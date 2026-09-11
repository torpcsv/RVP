"""編集モデル本体(ScriptEditModel: 点列・選択・Undo/Redo・基本編集)と EditLink。"""
from __future__ import annotations


from .common import (GRID_AT_DEFAULT, RVP_VERSION, UNDO_LIMIT, moved_points,
    snap)
from .model_clipboard import _ScriptEditModelClipboardMixin
from .model_patterns import _ScriptEditModelPatternsMixin


class EditLink:
    """=298: 5列csv(UFOTW)の左右2本の編集モデルを束ねる(ユーザー要望2/3)。

    - **クリップボードの共有**: どちらでコピー/切り取りしても、全モデルの
      clipboard へ同じ内容が入る(片方でコピー→もう片方へ貼り付け)。
    - **UNDO/REDO の一本化**: 変更の発生順を `order` に記録し、Ctrl+Z は
      どのグラフにフォーカスがあっても**最後に変更したモデル**を1つ戻す
      (左→右→左→右と打点したら、右→左→右→左の順に戻る)。REDO も同じ。
      各モデルの `_undo`/`_redo` はそのまま使い、順番だけをここで持つ。
    """

    def __init__(self, models=()):
        self.models: list = list(models)
        self.order: list = []          # 変更した順のモデル(UNDO 用)
        self.redo_order: list = []     # 戻した順のモデル(REDO 用)
        for m in self.models:
            m.link = self

    def reset(self) -> None:
        self.order = []
        self.redo_order = []

    def record(self, model) -> None:
        """model._push() から呼ばれる: 変更順を積み、REDO を捨てる。"""
        self.order.append(model)
        if len(self.order) > UNDO_LIMIT * max(1, len(self.models)):
            del self.order[0]
        self.redo_order = []

    def share_clipboard(self, src) -> None:
        for m in self.models:
            if m is not src:
                m.clipboard = list(src.clipboard) \
                    if src.clipboard is not None else None
                m.clipboard_patterns = [dict(pc) for pc in
                                        src.clipboard_patterns]

    def undo(self):
        """最後に変更したモデルを1つ戻す。戻したモデル(無ければ None)。"""
        while self.order:
            m = self.order.pop()
            if m._undo_local():
                self.redo_order.append(m)
                return m
        return None

    def redo(self):
        while self.redo_order:
            m = self.redo_order.pop()
            if m._redo_local():
                self.order.append(m)
                return m
        return None


class ScriptEditModel(_ScriptEditModelPatternsMixin, _ScriptEditModelClipboardMixin):

    """編集中の点列と選択・クリップボード・UNDO を持つ(Tk非依存)。

    - points: [(at, pos)] を at 昇順・at一意で保持
    - selection: 選択中の点の at の集合(at が一意なので識別子に使える)
    - UNDO は**点列のスナップショットを積むだけ**の素朴な方式(仕様 4.8。
      funscript は数千点でも軽い)。パターン(P2)は同じスナップショットへ
      一覧を足す形で拡張する。
    """

    def __init__(self):
        self.points: list[tuple[int, int]] = []
        self.selection: set[int] = set()
        self.clipboard: list[tuple[int, int]] | None = None
        # P2(=173): 配置済みパターンの一覧。各要素は dict:
        #   name(表示名|None=無名) / shape(正規化点列・インバート適用後) /
        #   A / s / k / b0 / b1(変換パラメータ=canonical 端線基準) /
        #   ats(構成点の at のタプル) / owned(このパターンが挿入した at。
        #   共有された点は含まない=移動・削除で消してよい点)
        self.patterns: list[dict] = []
        # =185: パターンのクリップボード(点と合わせた混在コピー)。
        # 各要素 {"rel": 先頭からの相対at, "shape": ((t,pos),...), "name"}
        self.clipboard_patterns: list[dict] = []
        # =185: 複数選択中のパターン index の集合(点の selection と併存)
        self.pattern_selection: set[int] = set()
        # =183: 端点の禁止帯の幅(端点±edge_tol へは打点・移動・貼り付け
        # 不可)。グラフが grid_at 変更時に max(1, atグリッド×0.5) へ更新
        self.edge_tol: float = max(1.0, GRID_AT_DEFAULT * 0.5)
        # =222: 直前に place_pattern_over で置いた区間 (at開始, at終端)。
        # Fキー長押しの数珠つなぎが次の起点に使う
        self.last_place_range = None
        # =224: csv 編集では**パターン内側の点もグリッドへ丸める**
        # (csv の時刻は 100ms 単位なので、内側も格子に乗せる必要がある)
        self.round_interior = False
        # =233: 離散的なスクリプトの中心(ROTATE=50 / VIBRATION=0)。
        # 上下反転の軸に使う(None=linear/twist は従来どおり基準線が軸)。
        self.pat_center = None
        # =226: 階段(離散)として扱うか。上書き配置の境目の規則が変わる
        # (前のパターンを消さず、境目の点だけ未来側のパターンへ譲る)
        self.step_mode = False
        # =297: pos の上限(分解能)。funscript=100 / csv=200(速度1刻み:
        # 下端=逆回転100 / 中央100=停止 / 上端=正回転100)。
        self.pos_max = 100
        # =298: 左右2本を束ねる EditLink(None=単独)。
        self.link = None
        self.last_undone = None        # 直前の undo/redo で戻したモデル
        self._undo: list = []
        self._redo: list = []
        self.dirty = False

    def load(self, actions) -> None:
        """点列を読み込み、選択・UNDO・dirty をリセットする。

        actions: [(at,pos)] または funscript の action dict の列。
        同一 at は後勝ちで1点に集約する(funscript 側の保証が無いため)。
        """
        pts = {}
        for a in actions or ():
            if isinstance(a, dict):
                try:
                    at, pos = int(a["at"]), int(a["pos"])
                except (KeyError, TypeError, ValueError):
                    continue
            else:
                at, pos = int(a[0]), int(a[1])
            if at >= 0:
                pts[at] = max(0, min(self.pos_max, pos))
        self.points = sorted(pts.items())
        self.selection = set()
        self.clipboard = None
        self.clipboard_patterns = []
        self.pattern_selection = set()
        self.patterns = []
        self._undo = []
        self._redo = []
        self.dirty = False
        if self.link is not None:          # =298: 読み直しで順番も捨てる
            self.link.reset()

    def duration_ms(self) -> int:
        return self.points[-1][0] if self.points else 0

    def export_patterns(self):
        """funscript へ埋め込む "rvp" 辞書を作る。パターンが無ければ None
        (キー自体を書かない)。

        仕様 6.2 の at/s/k/base/shape に加えて、**実現された点列 `pts` と
        所有点 `owned` も書く**(=204での拡張)。読み込み時の照合を
        「保存時のグリッド設定に依存せず」厳密にでき、共有端の所有関係も
        正確に往復できるため。
        """
        recs = []
        pos_map = dict(self.points)
        for rec in self.patterns:
            if not rec["ats"]:
                continue
            pts = [[int(a), int(pos_map.get(a, 0))] for a in rec["ats"]]
            recs.append({
                "name": rec.get("name"),
                "at": float(rec["A"]), "s": float(rec["s"]),
                "k": float(rec["k"]),
                "base": [float(rec["b0"]), float(rec["b1"])],
                "shape": [[float(t), float(p)] for t, p in rec["shape"]],
                "pts": pts,
                "owned": [int(a) for a in rec["owned"]],
            })
        if not recs:
            return None
        return {"version": RVP_VERSION, "patterns": recs}

    def import_patterns(self, raw) -> tuple[int, int]:
        """"rvp" 辞書からパターンを復元する(=204)。戻り値 (復元数, 破棄数)。

        self.points は load() 済みが前提。照合に失敗したパターンは
        **その情報だけを捨て、点は actions のまま残す**(仕様 6.2 =
        他アプリで編集された結果を壊さない)。
        """
        self.patterns = []
        if not isinstance(raw, dict):
            return 0, 0
        items = raw.get("patterns")
        if not isinstance(items, list):
            return 0, 0
        if raw.get("version") != RVP_VERSION:
            return 0, len(items)
        pos_map = dict(self.points)
        restored = dropped = 0
        claimed: set = set()
        ranges: list = []
        for it in items:
            rec = self._import_one_pattern(it, pos_map, claimed, ranges)
            if rec is None:
                dropped += 1
                continue
            self.patterns.append(rec)
            claimed.update(rec["ats"])
            ranges.append((rec["ats"][0], rec["ats"][-1]))
            restored += 1
        return restored, dropped

    def _import_one_pattern(self, it, pos_map, claimed, ranges):
        """1パターンぶんの照合と復元。不正・不一致は None(=破棄)。"""
        try:
            shape = tuple((float(t), float(p)) for t, p in it["shape"])
            pts = [(int(a), int(p)) for a, p in it["pts"]]
            A = float(it["at"])
            sc = float(it["s"])
            k = float(it["k"])
            b0, b1 = (float(x) for x in it["base"])
            name = it.get("name")
        except (KeyError, TypeError, ValueError):
            return None
        if name is not None and not isinstance(name, str):
            return None
        if len(shape) < 2 or len(pts) != len(shape):
            return None
        ats = [a for a, _ in pts]
        if len(ats) < 2 or ats != sorted(set(ats)):
            return None
        # 照合1: パターンの全点が actions に(at,pos そのまま)ある
        for a, p in pts:
            if pos_map.get(a) != p:
                return None
        # 照合2: 範囲の内側に無関係の点が挟まっていない
        aset = set(ats)
        lo, hi = ats[0], ats[-1]
        for a, _p in self.points:
            if lo < a < hi and a not in aset:
                return None
        # 照合3: 復元済みのパターンと内側で重ならない(端の共有は可)
        for plo, phi in ranges:
            if lo < phi and plo < hi:
                return None
        owned = None
        if isinstance(it.get("owned"), list):
            try:
                owned = tuple(int(a) for a in it["owned"] if int(a) in aset)
            except (TypeError, ValueError):
                owned = None
        if owned is None:
            # 旧データ等で owned が無ければ「先に復元したパターンと
            # 共有していない点」を所有とみなす
            owned = tuple(a for a in ats if a not in claimed)
        return {"name": name, "shape": shape, "A": A, "s": sc, "k": k,
                "b0": b0, "b1": b1, "ats": tuple(ats), "owned": owned}

    def pos_of(self, at: int):
        for a, p in self.points:
            if a == at:
                return p
        return None

    def _sorted_set(self, pts: dict) -> None:
        self.points = sorted(pts.items())

    def _snapshot(self):
        return (list(self.points), [dict(p) for p in self.patterns])

    def _restore(self, snap) -> None:
        self.points, pats = snap
        self.patterns = [dict(p) for p in pats]

    def _push(self) -> None:
        self._undo.append(self._snapshot())
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo = []
        self.dirty = True
        if self.link is not None:
            self.link.record(self)

    def undo(self) -> bool:
        """=298: link があれば**最後に変更したモデル**(自分とは限らない)を
        戻す。戻したモデルは `last_undone` に入る(グラフの再描画用)。"""
        if self.link is not None:
            m = self.link.undo()
            self.last_undone = m
            return m is not None
        self.last_undone = self if self._undo_local() else None
        return self.last_undone is not None

    def redo(self) -> bool:
        if self.link is not None:
            m = self.link.redo()
            self.last_undone = m
            return m is not None
        self.last_undone = self if self._redo_local() else None
        return self.last_undone is not None

    def _undo_local(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        ats = {a for a, _ in self.points}
        self.selection &= ats
        self.pattern_selection = set()      # index が変わり得るため(=185)
        self.dirty = True
        return True

    def _redo_local(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        ats = {a for a, _ in self.points}
        self.selection &= ats
        self.pattern_selection = set()
        self.dirty = True
        return True

    def select_only(self, at: int) -> None:
        ok = self.pos_of(at) is not None and at not in self.pattern_ats()
        self.selection = {at} if ok else set()

    def toggle_select(self, at: int) -> None:
        if self.pos_of(at) is None or at in self.pattern_ats():
            return
        if at in self.selection:
            self.selection.discard(at)
        else:
            self.selection.add(at)

    def clear_selection(self) -> None:
        self.selection = set()

    def select_rect(self, at0: float, at1: float,
                    pos0: float, pos1: float) -> None:
        """矩形選択(仕様 4.3/=185/=281)。

        - 点: 矩形に**完全に入った**点(パターンの構成点は除く)。
        - パターン(グループ): =281 で「構成点のすべてが矩形の中」から
          **「グループの矩形(at 範囲×pos の最小〜最大)が矩形に一部でも
          触れている/含まれている」**へ緩和(実機FB。当てやすくする)。
          点の個別選択はできないままだが、パターン単位では複数選択して
          コピー・削除・グループ化・グループ解除ができる。
        """
        lo_a, hi_a = min(at0, at1), max(at0, at1)
        lo_p, hi_p = min(pos0, pos1), max(pos0, pos1)
        pat = self.pattern_ats()
        self.selection = {a for a, p in self.points
                          if lo_a <= a <= hi_a and lo_p <= p <= hi_p
                          and a not in pat}
        self.pattern_selection = set()
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            vals = [self.pos_of(a) for a in rec["ats"]]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            plo, phi = rec["ats"][0], rec["ats"][-1]
            pmin, pmax = min(vals), max(vals)
            if plo <= hi_a and phi >= lo_a and pmin <= hi_p and pmax >= lo_p:
                self.pattern_selection.add(i)

    def toggle_pattern_select(self, index: int) -> None:
        """Ctrl+クリックによるパターンの追加選択・解除(=185)。"""
        if not (0 <= index < len(self.patterns)):
            return
        if index in self.pattern_selection:
            self.pattern_selection.discard(index)
        else:
            self.pattern_selection.add(index)

    def add_point(self, at: int, pos: int) -> bool:
        """打点。同じ at に既存の(普通の)点があるときは**上書き**する
        (=176 実機FB。クリックした pos へ打点し直せる)。
        パターンの構成点・パターンの時間範囲の内側は従来どおり拒否。"""
        at = int(at)
        pos = max(0, min(self.pos_max, int(pos)))
        if at < 0 or self.at_blocked(at) or at in self.pattern_ats():
            return False
        if self.pos_of(at) == pos:
            return False        # 同一なら何もしない(UNDOを積まない)
        self._push()
        pts = dict(self.points)
        pts[at] = pos
        self._sorted_set(pts)
        self.selection = {at}
        return True

    def delete_selected(self) -> bool:
        if not self.selection:
            return False
        self._push()
        self.points = [(a, p) for a, p in self.points
                       if a not in self.selection]
        self.selection = set()
        return True

    def move_selected(self, dat: float, dpos: float,
                      grid_at: int, grid_pos: int) -> bool:
        """選択中の点をまとめて移動する(結果をグリッドへ吸着)。

        移動先で衝突するとき(他の点と同じ at / 移動同士で重複)は
        **何も起きない**(仕様 10b)。移動量0(丸めた結果が同一)なら
        UNDO ステップを積まない。
        """
        if not self.selection:
            return False
        mapping, ok = moved_points(self.points, self.selection,
                                   dat, dpos, grid_at, grid_pos,
                                   pos_max=self.pos_max)
        if not ok:
            return False
        if any(self.at_blocked(na) for na, _ in mapping.values()):
            return False
        if all(mapping[a] == (a, self.pos_of(a)) for a in mapping):
            return False
        self._push()
        pts = dict(self.points)
        for old in mapping:
            del pts[old]
        for old, (na, np_) in mapping.items():
            pts[na] = np_
        self._sorted_set(pts)
        self.selection = {na for na, _ in mapping.values()}
        return True

    def set_point(self, at: int, new_at: int, new_pos: int) -> bool:
        """(at,pos) 数値入力欄からの確定(仕様 4.6)。

        グリッドへは**丸めない**(明示的に入力された値をそのまま使う)。
        移動先に別の点があれば False(呼び出し側が表示を元へ戻す)。
        """
        if self.pos_of(at) is None:
            return False
        new_at = int(new_at)
        new_pos = max(0, min(self.pos_max, int(new_pos)))
        if new_at < 0:
            return False
        if new_at != at and self.pos_of(new_at) is not None:
            return False
        if new_at != at and self.at_blocked(new_at):
            return False
        if (new_at, new_pos) == (at, self.pos_of(at)):
            return False
        self._push()
        pts = dict(self.points)
        del pts[at]
        pts[new_at] = new_pos
        self._sorted_set(pts)
        self.selection = {new_at}
        return True
