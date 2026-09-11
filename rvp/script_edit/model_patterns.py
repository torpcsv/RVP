"""スクリプト編集: パターン(グループ)の配置・移動・伸縮・反転・グループ化/解除(mixin)。"""
from __future__ import annotations


from .patterns import invert_shape, plan_connect_both, shape_len


class _ScriptEditModelPatternsMixin:
    """ScriptEditModel の mixin(=301 分割)。パターン(グループ)の配置・移動・伸縮・反転・グループ化/解除"""

    def pattern_ats(self) -> set:
        """全パターンの構成点の at 集合(=個別選択できない点)。"""
        out = set()
        for p in self.patterns:
            out.update(p["ats"])
        return out

    def at_in_pattern(self, at: float) -> bool:
        """at がいずれかのパターンの時間範囲の**内側**にあるか(=174)。

        パターンの範囲は一体として扱うので、範囲内へ普通の点を打つ・
        動かす・貼ることはできない(端の at は既存点の衝突判定に任せる)。
        """
        for rec in self.patterns:
            if rec["ats"] and rec["ats"][0] < at < rec["ats"][-1]:
                return True
        return False

    def at_blocked(self, at: float) -> bool:
        """普通の点を置けない at か(=183)。

        パターンの範囲の内側(=174)に加え、**始端・終端の±edge_tol
        (atグリッド半分・最低1ms)の禁止帯**も拒否する。端点がグリッド外
        にあるとき、真上をクリックするとグリッド吸着で数ms隣へ「見た目は
        端点に重なる点」がいつのまにか置かれてしまう問題への対処。
        打点・点の移動・数値入力・貼り付けの全経路がこれを使う。
        """
        tol = self.edge_tol
        for rec in self.patterns:
            if rec["ats"] and \
                    rec["ats"][0] - tol <= at <= rec["ats"][-1] + tol:
                return True
        return False

    def shared_endpoints(self) -> set:
        """共有されている端点の at の集合(=184: ◎で描く箇所)。

        端点のうち **owned に無いもの**=配置時にすでに存在していた点
        (通常の点への接続、または別パターンの端点との接続)。
        グループ化で作ったパターンは自分の点を owned に持つので、
        端点は共有扱いにならない(後から別パターンが繋がれば、その
        パターン側の端点が owned 外となり共有になる)。
        """
        out = set()
        for rec in self.patterns:
            if rec["ats"]:
                for a in (rec["ats"][0], rec["ats"][-1]):
                    if a not in rec["owned"]:
                        out.add(a)
        return out

    def pattern_of_at(self, at: int) -> int | None:
        for i, p in enumerate(self.patterns):
            if at in p["ats"]:
                return i
        return None

    def pattern_endpoints(self, exclude: int | None = None) -> list:
        """接続相手になれる端点 [(pattern_idx, at, pos, side)](仕様 4-4)。"""
        out = []
        for i, p in enumerate(self.patterns):
            if i == exclude or not p["ats"]:
                continue
            lo, hi = p["ats"][0], p["ats"][-1]
            pl, ph = self.pos_of(lo), self.pos_of(hi)
            if pl is not None:
                out.append((i, lo, pl, "L"))
            if ph is not None:
                out.append((i, hi, ph, "R"))
        return out

    def check_pattern_points(self, pts, exclude: int | None = None):
        """パターン配置の衝突判定(仕様 5.3/決定10・11)。

        - 時間範囲の**内側**に既存点が1つでもあれば拒否
        - 両端: 同じ at の既存点は **pos が一致すれば「点を共有」**、違えば拒否
        exclude を渡すと、そのパターンが**単独所有する点**を無視する
        (移動・拡縮のとき自分自身と衝突しないため)。
        戻り値: (ok, shared_ats)
        """
        if not pts:
            return False, set()
        ignore = set()
        if exclude is not None:
            others = set()
            for i, p in enumerate(self.patterns):
                if i != exclude:
                    others.update(p["ats"])
            ignore = {a for a in self.patterns[exclude]["owned"]
                      if a not in others}
        existing = {a: p for a, p in self.points if a not in ignore}
        lo, hi = pts[0][0], pts[-1][0]
        for a in existing:
            if lo < a < hi:
                return False, set()
        shared = set()
        for a, p in (pts[0], pts[-1]):
            if a in existing:
                if existing[a] != p:
                    return False, set()
                shared.add(a)
        return True, shared

    def _apply_pattern(self, plan: dict, name, index: int | None,
                       shared: set) -> None:
        """plan の点列を points へ反映し、パターン記録を作る/置き換える。"""
        pts = dict(self.points)
        if index is not None:
            others = set()
            for i, p in enumerate(self.patterns):
                if i != index:
                    others.update(p["ats"])
            for a in self.patterns[index]["owned"]:
                if a not in others:
                    pts.pop(a, None)
        new_ats = []
        for a, p in plan["points"]:
            new_ats.append(a)
            pts[a] = p
        self._sorted_set(pts)
        # =279: 端点が既存の**通常の点**に重なったら、その点はパターンが
        # 取り込む(owned に含める=以後パターンと一緒に動き、パターンを
        # 消せば消える)。従来は「共有=◎」として通常の点を残していたが、
        # 取り残された点がパターンのD&D・拡縮の邪魔になるため(実機FB)。
        # パターン同士の境目は =230 のとおり両方が自分の点として持つ。
        rec = {"name": name, "shape": tuple(plan["shape"]),
               "A": plan["A"], "s": plan["s"], "k": plan["k"],
               "b0": plan["b0"], "b1": plan["b1"],
               "ats": tuple(new_ats), "owned": tuple(new_ats)}
        if index is None:
            self.patterns.append(rec)
        else:
            rec["name"] = self.patterns[index]["name"]
            self.patterns[index] = rec

    def overwrite_preview(self, base: int, end: int, first_pos=None,
                          last_pos=None):
        """=221: [base, end] を明け渡したときに**消えるもの**を先に知る。

        `_overwrite_place` の「明け渡し」と同じ規則で判定するだけで、
        モデルは一切変えない(ゴーストの赤い予告に使う)。
        戻り値: (消えるパターンの index 集合, 消える点の at 集合)。
        """
        drop = set()
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            plo, phi = rec["ats"][0], rec["ats"][-1]
            if max(plo, base) < min(phi, end):
                drop.add(i)
            elif phi == base and first_pos is not None \
                    and self.pos_of(phi) != first_pos:
                drop.add(i)
            elif plo == end and last_pos is not None \
                    and self.pos_of(plo) != last_pos:
                drop.add(i)
        surv_ats = set()
        for i, rec in enumerate(self.patterns):
            if i not in drop:
                surv_ats.update(rec["ats"])
        ats = set()
        for i in drop:
            ats.update(a for a in self.patterns[i]["owned"]
                       if a not in surv_ats)
        for a, _p in self.points:
            if base <= a <= end and a not in surv_ats:
                ats.add(a)
        return drop, ats

    def place_plan_overwrite(self, plan: dict, name) -> str:
        """=221: 検証済みの plan を **上書き方式**で置く(Ctrl+V と同じ規則)。

        区間と重なる点・パターンを明け渡してから配置する。戻り値は
        paste_clip と同じ "ok" / "range" / "edge"。
        """
        if plan is None:
            return "range"
        pts = plan["points"]
        return self._overwrite_place(pts[0][0], pts[-1][0], [],
                                     [{"points": pts, "name": name}],
                                     pts[0][1])

    def place_point_over(self, at: int, pos: int) -> str:
        """=223: 数字キーの打点(上書き方式)。

        at を含む・端の禁止帯に掛かるパターンは**丸ごと削除**してから点を
        置く(ユーザー決定=「後から置くものを優先」)。同じ at の普通の点は
        pos を書き換える。戻り値 "ok" / "range" / "same"。
        """
        at = int(at)
        pos = max(0, min(self.pos_max, int(pos)))
        if at < 0:
            return "range"
        tol = self.edge_tol
        drop = set()
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            lo, hi = rec["ats"][0], rec["ats"][-1]
            if lo <= at <= hi or abs(at - lo) <= tol or abs(at - hi) <= tol:
                drop.add(i)
        if not drop and self.pos_of(at) == pos:
            return "same"               # 変化なし(UNDOを積まない)
        self._push()
        survivors = [rec for i, rec in enumerate(self.patterns)
                     if i not in drop]
        surv_ats = set()
        for rec in survivors:
            surv_ats.update(rec["ats"])
        pts = dict(self.points)
        for i in drop:
            for a in self.patterns[i]["owned"]:
                if a not in surv_ats:
                    pts.pop(a, None)
        self.patterns = survivors
        pts[at] = pos
        self._sorted_set(pts)
        self.selection = {at}
        self.pattern_selection = set()
        return "ok"

    def place_pattern(self, plan: dict, name) -> bool:
        """パターンの新規配置。拒否は False(何も起きない=仕様 10b)。"""
        if plan is None:
            return False
        ok, shared = self.check_pattern_points(plan["points"])
        if not ok:
            return False
        self._push()
        self._apply_pattern(plan, name, None, shared)
        self.selection = set()
        return True

    def pattern_neighbors(self, index: int):
        """端点を共有して隣接するパターン (left_idx|None, right_idx|None)。"""
        rec = self.patterns[index]
        if not rec["ats"]:
            return None, None
        lo, hi = rec["ats"][0], rec["ats"][-1]
        left = right = None
        for j, other in enumerate(self.patterns):
            if j == index or not other["ats"]:
                continue
            if other["ats"][-1] == lo:
                left = j
            if other["ats"][0] == hi:
                right = j
        return left, right

    @staticmethod
    def _scaled_pos(pos: float, scale) -> int:
        """=234: 中心 c を軸に f 倍した pos(0〜100 は呼び出し側で検証)。"""
        c, f = scale
        return int(round(c + (float(pos) - c) * f))

    def group_move_plan(self, dat: float, dpos: float,
                        grid_at: int, grid_pos: int, scale=None):
        """複数選択(点+パターン)の剛体移動プラン(=188)。

        **選択集合をそのまま平行移動**する。Δをグリッドへ丸め(相対配置は
        保つ=剛体)、全員が置けるときだけ (dat_q, dpos_q) を返す。1つでも
        置けない(選択外との衝突・0〜100/時間0のはみ出し・選択外パターンの
        禁止帯=183)なら None=全体が動かない。接続スナップと隣接追従
        (=182)は働かせない。選択外パターンと共有していた端点は、はがれて
        元の点が残る(=182の単独移動と同じ規則)。

        **=234: `scale=(中心, 倍率)` を渡すと、縦は平行移動ではなく
        「中心の線を軸にした拡縮」**になる(rotate系: 回転速度0=pos50 が軸)。
        点だけの選択でも、点とパターンが混ざった選択でも同じように効く。
        """
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and self.patterns[i]["ats"])
        pts_sel = set(self.selection)
        if not pats and not pts_sel:
            return None
        dat_q = round(dat / grid_at) * grid_at if grid_at > 1 \
            else int(round(dat))
        if scale is not None:
            dpos_q = 0                      # =234: 縦は拡縮で表す
        else:
            dpos_q = round(dpos / grid_pos) * grid_pos if grid_pos > 1 \
                else int(round(dpos))
        dat_q, dpos_q = int(dat_q), int(dpos_q)
        if dat_q == 0 and dpos_q == 0 and \
                (scale is None or abs(scale[1] - 1.0) < 1e-9):
            return (0, 0)
        static_pats = [rec for i, rec in enumerate(self.patterns)
                       if i not in pats and rec["ats"]]
        static_pat_ats = set()
        for rec in static_pats:
            static_pat_ats.update(rec["ats"])
        # 移動で消える元の点: 選択点 + 移動パターンの owned のうち
        # 選択外パターンに共有されていないもの(共有端・元から在った
        # 普通の点は残る=はがれる)
        removed = set(pts_sel)
        for i in pats:
            removed.update(a for a in self.patterns[i]["owned"]
                           if a not in static_pat_ats)
        static_ats = {a for a, _ in self.points if a not in removed}
        tol = self.edge_tol
        # 点の移動先の検証
        for a in pts_sel:
            p = self.pos_of(a)
            na = a + dat_q
            np_ = self._scaled_pos(p, scale) if scale is not None \
                else p + dpos_q
            if na < 0 or np_ < 0 or np_ > self.pos_max or na in static_ats:
                return None
            for rec in static_pats:
                if rec["ats"][0] - tol <= na <= rec["ats"][-1] + tol:
                    return None             # 選択外パターンの範囲・禁止帯
        # パターンの移動先の検証
        for i in pats:
            ats = self.patterns[i]["ats"]
            nlo, nhi = ats[0] + dat_q, ats[-1] + dat_q
            if nlo < 0:
                return None
            for a in ats:
                np_ = self._scaled_pos(self.pos_of(a), scale) \
                    if scale is not None else self.pos_of(a) + dpos_q
                na = a + dat_q
                if np_ < 0 or np_ > self.pos_max or na in static_ats:
                    return None
            for rec in static_pats:
                if max(nlo, rec["ats"][0]) < min(nhi, rec["ats"][-1]):
                    return None             # 範囲の重なり
            for a, _p in self.points:
                if a not in removed and nlo < a < nhi:
                    return None             # 範囲内に選択外の点
        return (dat_q, dpos_q)

    def apply_group_move(self, dat: int, dpos: int, scale=None) -> bool:
        """group_move_plan で検証済みの移動を確定する(UNDO 1ステップ)。

        =234: `scale=(中心, 倍率)` のときは縦を拡縮する。
        """
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and self.patterns[i]["ats"])
        pts_sel = set(self.selection)
        if not pats and not pts_sel:
            return False
        if dat == 0 and dpos == 0 and \
                (scale is None or abs(scale[1] - 1.0) < 1e-9):
            return True                     # クリック相当(何もしない)
        self._push()
        static_pat_ats = set()
        for i, rec in enumerate(self.patterns):
            if i not in pats:
                static_pat_ats.update(rec["ats"])
        # 移動パターンの構成点の pos を先に控える(削除前)
        member_pos = {}
        for i in pats:
            for a in self.patterns[i]["ats"]:
                member_pos[a] = self.pos_of(a)
        sel_pos = {a: self.pos_of(a) for a in pts_sel}
        pts = dict(self.points)
        for a in pts_sel:
            pts.pop(a, None)
        for i in pats:
            for a in self.patterns[i]["owned"]:
                if a not in static_pat_ats:
                    pts.pop(a, None)
        def _np(pos):
            return self._scaled_pos(pos, scale) if scale is not None \
                else pos + dpos

        new_sel = set()
        for a in pts_sel:
            pts[a + dat] = _np(sel_pos[a])
            new_sel.add(a + dat)
        for i in pats:
            rec = self.patterns[i]
            ats = []
            for a in rec["ats"]:
                na = a + dat
                ats.append(na)
                pts[na] = _np(member_pos[a])
            owned = list(ats)          # =279: 構成点はすべて自分の点
            if scale is not None:
                # =234: canonical も中心の線で f 倍する(k はそのまま。
                # shape の pos が f 倍されるので、ふくらみ d も f 倍になる)
                c, f = scale
                shape = tuple((t, c + (p - c) * f) for t, p in rec["shape"])
                b0 = c + (rec["b0"] - c) * f
                b1 = c + (rec["b1"] - c) * f
            else:
                shape = tuple((t, p + dpos) for t, p in rec["shape"])
                b0, b1 = rec["b0"] + dpos, rec["b1"] + dpos
            self.patterns[i] = {
                **rec, "shape": shape, "A": rec["A"] + dat,
                "b0": b0, "b1": b1,
                "ats": tuple(ats), "owned": tuple(owned)}
        self._sorted_set(pts)
        self.selection = new_sel
        return True

    def neighbor_follow_plans(self, index: int, plan: dict,
                              grid_at: int, grid_pos: int,
                              directional: bool = False):
        """index の新しい姿(plan)に合わせた隣接パターンの追従plan(=175)。

        隣接パターンは**隣り合う側の端を新しい (at,pos) へ**、隣り合わない
        側の端は固定のまま、両側接続として解き直す(=矩形→平行四辺形)。
        固定端は動かないので**隣の隣へは影響しない**。
        戻り値: {j: plan}(端が動いていない隣は含めない)/
        None(追従できない=縮尺・振幅・丸めの制約を満たせない)。

        directional=True(=182・中央ドラッグ=移動のみ): **離れる側の隣は
        追従しない**(接続が外れて「はがれる」)。右へ動かす=左隣を除外・
        右隣は追従(変形)/左へ動かす=鏡像。上下のみ(時間移動ゼロ)は
        従来どおり両隣が追従する。拡縮は従来どおり(directional=False)。
        """
        left, right = self.pattern_neighbors(index)
        rec = self.patterns[index]
        old_lo, old_hi = rec["ats"][0], rec["ats"][-1]
        old_qlo, old_qhi = self.pos_of(old_lo), self.pos_of(old_hi)
        nlo, qlo = plan["points"][0]
        nhi, qhi = plan["points"][-1]
        # =182: 移動方向(中心の時間シフト)。接続スナップで s が変わる
        # plan でも中心の差なら向きを取り違えない
        shift = ((nlo + nhi) - (old_lo + old_hi)) / 2.0
        if directional:
            if shift > 0:
                left = None      # 右へ移動=左隣ははがれる(追従しない)
            elif shift < 0:
                right = None     # 左へ移動=右隣ははがれる
        out = {}
        if left is not None and (nlo, qlo) != (old_lo, old_qlo):
            o = self.patterns[left]
            a_far = o["ats"][0]
            q_far = self.pos_of(a_far)
            if not a_far < nlo:
                return None
            p = plan_connect_both(o["shape"], a_far, q_far, nlo, qlo,
                                  grid_at, grid_pos, k_cap=o["k"],
                                  pos_max=self.pos_max)
            if p is None:
                return None
            out[left] = p
        if right is not None and (nhi, qhi) != (old_hi, old_qhi):
            o = self.patterns[right]
            a_far = o["ats"][-1]
            q_far = self.pos_of(a_far)
            if not nhi < a_far:
                return None
            p = plan_connect_both(o["shape"], nhi, qhi, a_far, q_far,
                                  grid_at, grid_pos, k_cap=o["k"],
                                  pos_max=self.pos_max)
            if p is None:
                return None
            out[right] = p
        return out

    def check_plans(self, plans: dict) -> bool:
        """複数パターンの同時置き換え(=175)の衝突判定。

        involved なパターンの単独所有点を除いた既存点に対して、各 plan の
        範囲の内側・端の共有(pos一致)を検証し、plan どうしは端の一致
        (接続点)以外で範囲が重ならないことを確認する。
        """
        involved = set(plans)
        others_ats = set()
        for i, p in enumerate(self.patterns):
            if i not in involved:
                others_ats.update(p["ats"])
        ignore = set()
        for i in involved:
            ignore.update(a for a in self.patterns[i]["owned"]
                          if a not in others_ats)
        existing = {a: p for a, p in self.points if a not in ignore}
        combined = {}
        ranges = []
        for _i, plan in plans.items():
            pts = plan["points"]
            lo, hi = pts[0][0], pts[-1][0]
            for a in existing:
                if lo < a < hi:
                    return False
            for a, p in (pts[0], pts[-1]):
                if a in existing and existing[a] != p:
                    return False
            for a, p in pts:
                if a in combined and combined[a] != p:
                    return False
                combined[a] = p
            ranges.append((lo, hi))
        for x in range(len(ranges)):
            for y in range(x + 1, len(ranges)):
                if max(ranges[x][0], ranges[y][0]) < \
                        min(ranges[x][1], ranges[y][1]):
                    return False
        return True

    def replace_patterns(self, plans: dict) -> bool:
        """複数パターンの同時置き換え(=175・UNDOは1ステップ)。"""
        if not plans:
            return False
        for i, plan in plans.items():
            if plan is None or not (0 <= i < len(self.patterns)):
                return False
        if not self.check_plans(plans):
            return False
        self._push()
        involved = set(plans)
        others_ats = set()
        for i, p in enumerate(self.patterns):
            if i not in involved:
                others_ats.update(p["ats"])
        pts = dict(self.points)
        for i in involved:
            for a in self.patterns[i]["owned"]:
                if a not in others_ats:
                    pts.pop(a, None)
        for i in sorted(involved):
            plan = plans[i]
            ats = []
            for a, p in plan["points"]:
                ats.append(a)
                pts[a] = p
            owned = list(ats)          # =279: 端点が重なった通常の点も取り込む
            self.patterns[i] = {
                "name": self.patterns[i]["name"],
                "shape": tuple(plan["shape"]),
                "A": plan["A"], "s": plan["s"], "k": plan["k"],
                "b0": plan["b0"], "b1": plan["b1"],
                "ats": tuple(ats), "owned": tuple(owned)}
        self._sorted_set(pts)
        return True

    def replace_pattern(self, index: int, plan: dict) -> bool:
        """移動・拡縮の確定(単独)。拒否は False(元のまま)。"""
        if plan is None:
            return False
        return self.replace_patterns({index: plan})

    def move_pattern(self, index: int, dat: int, dpos: int) -> dict | None:
        """平行移動の plan を作る(点は現在値を平行移動=再計算しない)。

        s は不変(仕様 5.4)。at<0 / pos が 0〜100 を出る / 移動先の衝突は
        None。確定は replace_pattern で行う。
        """
        if not (0 <= index < len(self.patterns)):
            return None
        rec = self.patterns[index]
        pts = []
        for a in rec["ats"]:
            p = self.pos_of(a)
            if p is None:
                return None
            na, np_ = a + dat, p + dpos
            if na < 0 or np_ < 0 or np_ > self.pos_max:
                return None
            pts.append((na, np_))
        return {"points": pts, "A": rec["A"] + dat, "s": rec["s"],
                "k": rec["k"], "b0": rec["b0"] + dpos,
                "b1": rec["b1"] + dpos, "shape": rec["shape"]}

    def _flip_targets(self):
        """選択中の (パターンindex昇順, 点のat集合) を返す(=241)。"""
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and self.patterns[i]["ats"])
        pts = {a for a in self.selection if self.pos_of(a) is not None}
        return pats, pts

    def _static_pat_ats(self, pats) -> set:
        """選択外パターンの構成点の at 集合。"""
        out = set()
        for i, rec in enumerate(self.patterns):
            if i not in pats:
                out.update(rec["ats"])
        return out

    def flip_vertical(self, axis=None) -> str:
        """選択中の点+パターンの上下反転(=241。UNDOは1ステップ)。

        `pos' = 2c - pos`(at は変わらない)。軸 c は:
          axis=数値  … その pos の線(「上下反転(全幅)」= 50)。
          axis=None … **選択全体**の min(pos)〜max(pos) の中点
                      (「上下反転(パターン内)/(選択内)」)。
        速度(点どうしの pos 差の絶対値)は変わらない。at が動かないため
        条件は1つだけ: **選択外のパターンと共有している点は、軸の上に
        あるときだけ反転できる**(動かないので共有が保たれる)。
        パターンの canonical は invert_shape + 基準線の折り返しで表す
        (実現値がちょうど 2c - pos になる。=233 の rotate と同じ式)。

        戻り値 "ok" / "empty"(選択なし) / "shared"(軸上にない共有点) /
        "range"(0〜100 を出る。全幅・選択内とも原理上は出ない=保険)。
        """
        pats, pts = self._flip_targets()
        if not pats and not pts:
            return "empty"
        involved = set(pts)
        for i in pats:
            involved.update(self.patterns[i]["ats"])
        pos_map = dict(self.points)
        if axis is None:
            vals = [pos_map[a] for a in involved]
            axis = (min(vals) + max(vals)) / 2.0
        c2 = 2.0 * float(axis)

        def _m(p):
            return int(round(c2 - p))

        static_ats = self._static_pat_ats(pats)
        changed = False
        for a in involved:
            np_ = _m(pos_map[a])
            if np_ < 0 or np_ > self.pos_max:
                return "range"
            if np_ != pos_map[a]:
                changed = True
                if a in static_ats:
                    return "shared"
        if not changed:
            return "ok"                    # 全点が軸の上=何も起きない
        self._push()
        for a in involved:
            pos_map[a] = _m(pos_map[a])
        for i in pats:
            rec = self.patterns[i]
            # invert_shape は d を符号反転し、基準線の折り返しと合わせて
            # 実現値が 2c - pos になる(shape の値域 0〜100 も保たれる)
            self.patterns[i] = {**rec, "shape": invert_shape(rec["shape"]),
                                "b0": c2 - rec["b0"], "b1": c2 - rec["b1"]}
        self._sorted_set(pos_map)
        return "ok"

    def flip_horizontal(self) -> str:
        """選択中の点+パターンの左右反転(=241。UNDOは1ステップ)。

        軸=選択全体の at の min〜max の中心。`at' = (min+max) - at`、
        pos は変わらない(整数・csv の 100ms 単位も自動的に保たれる)。
        条件:
          (a) 選択範囲の内側(端を除く)に選択外の点・共有点が無いこと。
          (b) 端(min/max)を選択外のパターンと共有しているときは
              pos(min) == pos(max) であること(範囲の左右の中身が
              そっくり入れ替わるため)。
        戻り値 "ok" / "empty" / "inside"(範囲内に選択外) / "ends"(端の
        pos が不一致)。1点だけの選択は何も起きない("ok")。
        """
        pats, pts = self._flip_targets()
        if not pats and not pts:
            return "empty"
        involved = set(pts)
        for i in pats:
            involved.update(self.patterns[i]["ats"])
        lo, hi = min(involved), max(involved)
        if lo == hi:
            return "ok"                    # 1点だけ=何も起きない
        pos_map = dict(self.points)
        static_ats = self._static_pat_ats(pats)
        for a in pos_map:
            if a not in involved and lo < a < hi:
                return "inside"            # 範囲内に選択外の点
        for a in involved:
            if a in static_ats and lo < a < hi:
                return "inside"            # 範囲内で選択外パターンと共有
        if (lo in static_ats or hi in static_ats) and \
                pos_map[lo] != pos_map[hi]:
            return "ends"
        M = lo + hi
        self._push()
        moved = {a: pos_map[a] for a in involved}
        for a in involved:
            if a not in static_ats:        # 共有端は残す(値は下で上書き)
                pos_map.pop(a, None)
        for a, p in moved.items():
            pos_map[M - a] = p
        for i in pats:
            rec = self.patterns[i]
            shape = rec["shape"]
            length = shape_len(shape)
            nshape = tuple((length - t, p) for t, p in reversed(shape))
            self.patterns[i] = {
                **rec, "shape": nshape,
                "A": M - (rec["A"] + rec["s"] * length),
                "b0": rec["b1"], "b1": rec["b0"],
                "ats": tuple(sorted(M - a for a in rec["ats"])),
                "owned": tuple(sorted(M - a for a in rec["owned"]))}
        # =230 の作法: 端の共有点の所有者が反転で入れ替わった結果、
        # 誰も所有しない共有端が生じたら、選択外パターンへ引き継ぐ
        for a in (lo, hi):
            if a not in static_ats:
                continue
            owned = any(a in rec["owned"] for rec in self.patterns
                        if rec["ats"] and a in (rec["ats"][0],
                                                rec["ats"][-1]))
            if owned:
                continue
            for i, rec in enumerate(self.patterns):
                if rec["ats"] and a in (rec["ats"][0], rec["ats"][-1]):
                    self.patterns[i] = dict(rec)
                    self.patterns[i]["owned"] = tuple(rec["owned"]) + (a,)
                    break
        self._sorted_set(pos_map)
        self.selection = {M - a for a in pts}
        return "ok"

    def delete_pattern(self, index: int) -> bool:
        """パターンとその**単独所有の点**を削除する。

        =230: 残るパターンがまだ使っている点は消せないが、その点の
        **所有権は引き継ぐ**(そのパターンの `owned` へ入れる)。
        こうしないと、数珠つなぎで繋がったパターンを順に消したときに
        **境目の点だけが単独で取り残される**(=230 ユーザー報告の画像2)。
        =230 以降に置いたぶんは両方が持ち主なので元から取り残されないが、
        **それ以前に作ったスクリプトもこれで救える**。
        """
        if not (0 <= index < len(self.patterns)):
            return False
        self._push()
        others = set()
        for i, p in enumerate(self.patterns):
            if i != index:
                others.update(p["ats"])
        gone = {a for a in self.patterns[index]["owned"] if a not in others}
        # 残る側へ所有権を引き継ぐ(消せなかった自分の持ち点)
        keep = [a for a in self.patterns[index]["owned"] if a in others]
        if keep:
            for i, rec in enumerate(self.patterns):
                if i == index or not rec["ats"]:
                    continue
                add = [a for a in keep
                       if a in (rec["ats"][0], rec["ats"][-1])
                       and a not in rec["owned"]]
                if add:
                    self.patterns[i] = dict(rec)
                    self.patterns[i]["owned"] = tuple(rec["owned"]) + \
                        tuple(add)
        self.points = [(a, p) for a, p in self.points if a not in gone]
        del self.patterns[index]
        return True

    def ungroup_pattern(self, index: int) -> bool:
        """解除=パターンを普通の点の集まりに戻す(点は残す。仕様 6b)。"""
        if not (0 <= index < len(self.patterns)):
            return False
        self._push()
        del self.patterns[index]
        return True

    def copy_pattern(self, index: int) -> bool:
        """パターン1つのコピー(=180→=185で混在クリップボードへ統合)。

        pattern_selection をそのパターンだけにして copy_selected を呼ぶ
        (Ctrl+C の単独パターンコピーと同じ結果になる)。
        """
        if not (0 <= index < len(self.patterns)):
            return False
        if len(self.patterns[index]["ats"]) < 2:
            return False
        self.pattern_selection = {index}
        self.selection = set()
        return self.copy_selected()

    def most_future_pattern(self) -> int | None:
        """右端(最大at)が最も未来のパターンの index。"""
        best = None
        for i, p in enumerate(self.patterns):
            if p["ats"] and (best is None or
                             p["ats"][-1] >
                             self.patterns[best]["ats"][-1]):
                best = i
        return best

    def _selected_pattern_indices(self) -> list:
        return sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns))

    def group_candidate_ats(self) -> set:
        """=278: グループ化の対象になる点の at 集合
        (選択中の点+選択中のパターンの構成点)。"""
        out = set(self.selection)
        for i in self._selected_pattern_indices():
            out.update(self.patterns[i]["ats"])
        return out

    def ungroup_selected(self) -> bool:
        """=278: 選択中のパターンをすべて解除する(点は残り、選択中の点に
        加わる=そのままグループ化し直せる)。1段のUNDO。"""
        idxs = self._selected_pattern_indices()
        if not idxs:
            return False
        self._push()
        freed = set()
        for i in reversed(idxs):
            freed.update(self.patterns[i]["ats"])
            del self.patterns[i]
        self.pattern_selection = set()
        self.selection = set(self.selection) | freed
        return True

    def group_selection(self) -> str:
        """グループ化(仕様 5.4)。戻り値 "ok"/"too_few"/"has_pattern"/
        "unselected_in_range"。範囲=選択の最小 at〜最大 at。

        =278: 選択にパターンが含まれていれば、それらを**いったん解除して
        構成点を選択に含め**、全体を1つのパターンにする(1段のUNDO)。
        選択していないパターンと端点を共有していてもよい(その端点は
        新パターンの owned に入れない=共有端点◎のまま)。範囲の内側に
        選択していないパターンの点があれば従来どおり拒否。
        """
        pat_sel = self._selected_pattern_indices()
        sel_ats = self.group_candidate_ats()
        if len(sel_ats) < 2:
            return "too_few"
        lo, hi = min(sel_ats), max(sel_ats)
        other = set()
        for i, p in enumerate(self.patterns):
            if i not in pat_sel:
                other.update(p["ats"])
        for a, _p in self.points:
            if lo <= a <= hi and a in other and a not in sel_ats:
                return "has_pattern"
            if lo < a < hi and a in other:
                return "has_pattern"     # 内側で他パターンと重なる
            if lo <= a <= hi and a not in sel_ats:
                return "unselected_in_range"
        sel = sorted((a, self.pos_of(a)) for a in sel_ats)
        shape = tuple((a - lo, p) for a, p in sel)
        if shape_len(shape) <= 0:
            return "too_few"
        self._push()
        for i in reversed(pat_sel):
            del self.patterns[i]
        self.patterns.append({
            "name": None, "shape": shape, "A": float(lo), "s": 1.0,
            "k": 1.0, "b0": float(shape[0][1]), "b1": float(shape[-1][1]),
            "ats": tuple(a for a, _ in sel),
            "owned": tuple(a for a, _ in sel)})   # =279: 全点を自分の点に
        self.selection = set()
        self.pattern_selection = set()
        return "ok"
