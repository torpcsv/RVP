"""スクリプト編集: コピー/切り取り/貼り付け(上書き貼り付け・アンカー)と選択の一括削除(mixin)。"""
from __future__ import annotations


from .common import scale_shape, snap
from .patterns import plan_normal


class _ScriptEditModelClipboardMixin:
    """ScriptEditModel の mixin(=301 分割)。コピー/切り取り/貼り付け(上書き貼り付け・アンカー)と選択の一括削除"""

    def copy_selected(self) -> bool:
        """選択(点+パターン)を先頭 at を 0 とした相対配置で格納する。

        =185: 点だけでなく pattern_selection のパターンも一緒にコピー
        できる。パターンは**今の形**(移動・拡縮・反転後の現在の点列)を
        そのまま持つ(=179のコピーと同じ考え方)。
        """
        pats = sorted(i for i in self.pattern_selection
                      if 0 <= i < len(self.patterns)
                      and len(self.patterns[i]["ats"]) >= 2)
        if not self.selection and not pats:
            return False
        bases = [a for a in self.selection]
        bases += [self.patterns[i]["ats"][0] for i in pats]
        base = min(bases)
        sel = sorted((a, p) for a, p in self.points if a in self.selection)
        self.clipboard = [(a - base, p) for a, p in sel]
        plist = []
        for i in pats:
            ats = self.patterns[i]["ats"]
            lo = ats[0]
            plist.append({
                "rel": lo - base,
                "shape": tuple((a - lo, self.pos_of(a)) for a in ats),
                "name": self.patterns[i]["name"]})
        self.clipboard_patterns = plist
        if self.link is not None:          # =298: 左右で共有
            self.link.share_clipboard(self)
        return True

    def cut_selected(self) -> bool:
        """切り取り=コピー+削除。貼り付けできなくても切り取りは成立する。"""
        if not self.copy_selected():
            return False
        return self.delete_selected_any()

    def has_clipboard(self) -> bool:
        return bool(self.clipboard) or bool(self.clipboard_patterns)

    def _clip_len(self) -> int:
        """クリップボードの全長(先頭 rel=0〜最後の要素の末尾)。"""
        end = 0
        for rel, _p in (self.clipboard or ()):
            end = max(end, rel)
        for pc in self.clipboard_patterns:
            end = max(end, int(round(pc["rel"] + pc["shape"][-1][0])))
        return end

    def clip_first_pos(self):
        """クリップボードの先頭(rel=0)の要素の pos(高さ合わせの基準)。"""
        for rel, p in (self.clipboard or ()):
            if rel == 0:
                return p
        for pc in self.clipboard_patterns:
            if pc["rel"] == 0:
                return pc["shape"][0][1]
        return None

    def paste_clip(self, base_at: float, voff: int = 0) -> str:
        """上書き貼り付け(=185)。base_at にクリップボードの先頭が載る。

        貼り付け内容の先頭〜末尾の**閉区間を明け渡す**方式(Q3=a):
        区間内にある既存の通常の点は削除し、区間と(真に)重なる既存
        パターンは**丸ごと**削除してから、点とパターンを配置する。
        端でぴったり接するパターンは残り、接する端点の pos が一致すれば
        端点を共有する(=接続)。pos が食い違うときはそのパターンも削除
        する(貼り付けるものを優先)。voff は縦の平行移動(パターンを
        含む貼り付けでアンカーの高さへ合わせる=180の踏襲)。

        戻り値: "ok" / "no_clipboard" / "range"(0〜100やat<0をはみ出す)
        / "edge"(残るパターンの端点の禁止帯=183に掛かる)。
        UNDO は1ステップ。成功時は貼った点が selection、貼ったパターンが
        pattern_selection になる(続けて Ctrl+V で数珠つなぎにできる)。
        """
        if not self.has_clipboard():
            return "no_clipboard"
        base = int(round(base_at))
        if base < 0:
            return "range"
        # 貼り付ける中身(絶対座標)を作る
        new_pts = []
        for rel, p in (self.clipboard or ()):
            np_ = p + voff
            if np_ < 0 or np_ > self.pos_max:
                return "range"
            new_pts.append((base + rel, np_))
        new_pats = []
        for pc in self.clipboard_patterns:
            lo = base + int(round(pc["rel"]))
            pts = []
            for t, p in pc["shape"]:
                np_ = int(round(p + voff))
                if np_ < 0 or np_ > self.pos_max:
                    return "range"
                pts.append((lo + int(round(t)), np_))
            new_pats.append({"points": pts, "name": pc["name"]})
        end = base + self._clip_len()
        first_pos = self.clip_first_pos()
        first_pos = first_pos + voff if first_pos is not None else None
        return self._overwrite_place(base, end, new_pts, new_pats, first_pos)

    def place_pattern_over(self, shape, at0: float, base_pos: float | None,
                           grid_at: int, grid_pos: int, name,
                           scale: float = 1.0) -> str:
        """=213: リアルタイム配置(Fキー)。at0 を**左端**にして shape を
        Ctrl+V と同じ上書き規則(_overwrite_place)で置く。クリップボードは
        汚さない。選択状態も変えない(再生中の操作を邪魔しない)。

        base_pos: 標準パターン=50(端の高さを 50 へ。0〜100 に収まるよう
        クランプ=通常配置 =177 と同じ規則)/ None=形のまま(ユーザー
        パターン)。scale=時間軸の縮尺(=194)。
        戻り値は paste_clip と同じ("ok"/"range"/"edge")+"grid"(配置不能)。
        """
        # =222: 失敗した回の値が残らないよう、毎回まず消す(数珠つなぎは
        # 「置けた区間」だけを次の起点にする)
        self.last_place_range = None
        if not shape or len(shape) < 2:
            return "grid"
        shape = scale_shape(shape, self.pos_max)   # =297: 0〜100 定義→分解能
        if abs(scale - 1.0) > 1e-9:
            shape = tuple((t * scale, p) for t, p in shape)
        p0 = shape[0][1]
        plast = shape[-1][1]
        if base_pos is None:
            off = 0
        else:
            pmin = min(p for _t, p in shape)
            pmax = max(p for _t, p in shape)
            target = max(0, min(self.pos_max, base_pos))
            off = max(-pmin, min(self.pos_max - pmax, target - p0))
        A = snap(max(0.0, at0), grid_at)
        plan = plan_normal(shape, A, grid_at, grid_pos,
                           b0=p0 + off, b1=plast + off,
                           round_interior=self.round_interior,
                           pos_max=self.pos_max)
        if plan is None:
            return "grid"
        pts = plan["points"]
        sel, psel = set(self.selection), set(self.pattern_selection)
        r = self._overwrite_place(pts[0][0], pts[-1][0], [],
                                  [{"points": pts, "name": name}],
                                  pts[0][1])
        if r == "ok":
            # 配置前の選択を保つ(消えた点・パターンは除く)
            pat = self.pattern_ats()
            self.selection = {a for a in sel
                              if a not in pat and self.pos_of(a) is not None}
            self.pattern_selection = set()
            # =222: 置いた区間を控える(Fキー長押しの数珠つなぎが、この
            # **終端**を次の起点にする)
            self.last_place_range = (pts[0][0], pts[-1][0])
        return r

    def _overwrite_place(self, base: int, end: int, new_pts, new_pats,
                         first_pos) -> str:
        """上書き配置の共通部(=185 の paste_clip から =213 で抽出)。
        [base, end] の閉区間を明け渡して点とパターンを置く。UNDO 1ステップ。
        成功時は置いた点が selection・パターンが pattern_selection。"""
        # 明け渡し: 区間と真に重なるパターンは丸ごと削除。端で接する
        # パターンは、接する端点の pos が貼り付け側と一致すれば残す
        last_pos = None
        for a, p in new_pts:
            if a == end:
                last_pos = p
        for rec in new_pats:
            if rec["points"][-1][0] == end:
                last_pos = rec["points"][-1][1]
            if rec["points"][0][0] == base:
                first_pos = rec["points"][0][1]
        drop = set()
        # =226(離散): **隣り合わせを許容し、境目の pos は「未来側の
        # パターンの始端」を採用する**(ユーザー決定)。階段では前の
        # パターンの最後の点は見た目に影響しないため、前のパターンは
        # 消さずに残し、境目の点だけ譲る。
        yield_at = None      # 前のパターンから明け渡してもらう境目の at
        keep_pos = {}        # 未来側のパターンの始端=書き換えない点
        for i, rec in enumerate(self.patterns):
            if not rec["ats"]:
                continue
            plo, phi = rec["ats"][0], rec["ats"][-1]
            if max(plo, base) < min(phi, end):
                drop.add(i)                     # 真の重なり=丸ごと削除
            elif phi == base and first_pos is not None \
                    and self.pos_of(phi) != first_pos:
                if self.step_mode:
                    yield_at = base             # 境目は新しい側の値になる
                else:
                    drop.add(i)                 # 接する端の pos 不一致
            elif plo == end and last_pos is not None \
                    and self.pos_of(plo) != last_pos:
                if self.step_mode:
                    keep_pos[end] = self.pos_of(plo)   # 未来側が勝つ
                else:
                    drop.add(i)
        survivors = [rec for i, rec in enumerate(self.patterns)
                     if i not in drop]
        surv_ats = set()
        for rec in survivors:
            surv_ats.update(rec["ats"])
        if yield_at is not None:
            # 境目の点の**値**は新しいパターンのものにする(階段では前の
            # パターンの最後の点は見た目に影響しないため)。いったん外して
            # 置き直させる。
            # **=235: 所有権は前のパターンにも残す**(=230 と同じ「両方が
            # 持ち主」)。手放させると `shared_endpoints()` が拾って
            # ◎(共有端点)で描かれ、**数珠つなぎの境目に打点が残る**
            # ように見えていた(ユーザー報告)。
            surv_ats.discard(yield_at)
        # 残るパターンの端点の禁止帯(=183)に掛かる通常の点は拒否
        # (端点そのものへの合流=共有は許す)
        tol = self.edge_tol
        for a, _p in new_pts:
            for rec in survivors:
                if not rec["ats"]:
                    continue
                for ep in (rec["ats"][0], rec["ats"][-1]):
                    if a != ep and abs(a - ep) <= tol:
                        return "edge"
        self._push()
        # 削除: 落とすパターンの単独所有点(残る側に共有されていないもの)
        pts = dict(self.points)
        for i in drop:
            for a in self.patterns[i]["owned"]:
                if a not in surv_ats:
                    pts.pop(a, None)
        self.patterns = survivors
        # 削除: 閉区間内の通常の点(残るパターンの構成点は残す)
        for a in list(pts):
            if base <= a <= end and a not in surv_ats:
                del pts[a]
        # 配置: 点 → パターン(端点の共有=すでにある点は owned に含めない)
        for a, p in new_pts:
            pts[a] = p
        # =230: **パターン同士の境目は「共有点」にしない**(ユーザー要望)。
        # 数珠つなぎ(Fキー長押し)や接続配置で、新しいパターンの端点が
        # **別のパターンの端点**にちょうど重なるときは、新しい側も
        # その点を**自分のもの**として持つ。結果:
        #   ・◎(二重丸=共有端点)にならず、普通のパターンの点として描かれる
        #   ・両方のパターンを消せば、その点も一緒に消える(取り残しが出ない)
        # 波形(点の位置・値)は一切変わらない。**通常の点(ユーザーが打った
        # 点)へ繋いだときは従来どおり共有=◎**(=184 の意味は残す)。
        sel_pats = set()
        for rec in new_pats:
            ats = []
            for a, p in rec["points"]:
                ats.append(a)
                pts[a] = p
            owned = list(ats)   # =279: 通常の点へ繋いだ端点も自分の点にする
            lo = ats[0]
            shape = tuple((a - lo, p) for a, p in rec["points"])
            self.patterns.append({
                "name": rec["name"], "shape": shape, "A": float(lo),
                "s": 1.0, "k": 1.0, "b0": float(rec["points"][0][1]),
                "b1": float(rec["points"][-1][1]),
                "ats": tuple(ats), "owned": tuple(owned)})
            sel_pats.add(len(self.patterns) - 1)
        # =226(離散): 未来側のパターンの始端に重なった境目は、その値を戻す
        for a, p in keep_pos.items():
            if a in pts:
                pts[a] = p
        self._sorted_set(pts)
        pat = self.pattern_ats()
        self.selection = {a for a, _ in new_pts if a not in pat}
        self.pattern_selection = sel_pats
        return "ok"

    def paste_over_anchor(self) -> str:
        """Ctrl+V の貼り付け(点のみのクリップボード=172の踏襲)。

        基準=**選択中で最も at が大きい点(アンカー)**。クリップボードの
        先頭(rel=0)がアンカーの at にちょうど載る(グリッド吸着はしない)。
        =185で上書き方式になり、アンカーだけでなく**貼り付け区間の既存の
        点はすべて上書き**される。pos は絶対値のまま(縦は動かさない)。

        戻り値: "ok" / "no_clipboard" / "no_selection" / "range" / "edge"
        """
        if not self.has_clipboard():
            return "no_clipboard"
        if not self.selection:
            return "no_selection"
        return self.paste_clip(float(max(self.selection)), 0)

    def delete_selected_any(self) -> bool:
        """選択中の点+パターンをまとめて削除する(=185・UNDOは1ステップ)。

        パターンは単独所有の点ごと消える(delete_pattern と同じ規則)。
        """
        pats = sorted((i for i in self.pattern_selection
                       if 0 <= i < len(self.patterns)), reverse=True)
        if not pats and not self.selection:
            return False
        self._push()
        keep_ats = set()
        for i, rec in enumerate(self.patterns):
            if i not in pats:
                keep_ats.update(rec["ats"])
        drop_ats = set(self.selection)
        for i in pats:
            drop_ats.update(a for a in self.patterns[i]["owned"]
                            if a not in keep_ats)
        self.points = [(a, p) for a, p in self.points
                       if a not in drop_ats]
        for i in pats:
            del self.patterns[i]
        self.selection = set()
        self.pattern_selection = set()
        return True
