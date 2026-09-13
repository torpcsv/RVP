"""スクリプト編集: マウス/キー/ナッジの入力処理と選択・履歴・変更通知(mixin)。"""
from __future__ import annotations


from .common import scale_shape, snap


class _ScriptEditGraphInputMixin:
    """ScriptEditGraph の mixin(=301 分割)。マウス/キー/ナッジの入力処理と選択・履歴・変更通知"""

    def nudge(self, dat_step: int, dpos_step: int) -> bool:
        """選択中の点・パターン(複数なら全部=剛体)を at/pos のグリッド
        1単位ぶん動かす(グリッド「なし」=1)。置けなければ False で
        何も変えない(描画もしない)。UNDO 1ステップ。"""
        if self.readonly:            # =227: サブ表示は見るだけ
            return False
        self._sync_pat_sel()
        m = self.model
        if not m.selection and not m.pattern_selection:
            return False
        dat = int(dat_step) * (self.grid_at if self.grid_at > 1 else 1)
        dpos = int(dpos_step) * (self.grid_pos if self.grid_pos > 1 else 1)
        # =320: 離散(rotate/vib)でもパターンの縦移動を許す(=226 の制限を
        # 撤廃。ユーザー決定=平行移動で速度・強さをまとめて変える)
        if dat == 0 and dpos == 0:
            return False
        plan = m.group_move_plan(dat, dpos, self.grid_at, self.grid_pos)
        if plan is None or plan == (0, 0):
            return False
        if not m.apply_group_move(*plan):
            return False
        self._notify_change()
        self._notify_select()
        self.redraw()
        return True

    def _on_nudge_press(self, step):
        if self._nudge_release_job is not None:
            # 自動リピート(X11 は Release/Press を繰り返す)=押しっぱなし継続
            try:
                self.after_cancel(self._nudge_release_job)
            except Exception:
                pass
            self._nudge_release_job = None
        if self._nudge_held == step:
            return "break"              # OS の自動リピートは無視(自前で刻む)
        self._nudge_stop()
        self._nudge_held = step
        self.nudge(*step)
        self._nudge_job = self.after(self.NUDGE_HOLD_MS, self._nudge_repeat)
        return "break"

    def _nudge_repeat(self):
        self._nudge_job = None
        if self._nudge_held is None:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.nudge(*self._nudge_held)
        self._nudge_job = self.after(self.NUDGE_REPEAT_MS, self._nudge_repeat)

    def _on_nudge_release(self, step):
        if self._nudge_held != step:
            return "break"
        if self._nudge_release_job is not None:
            try:
                self.after_cancel(self._nudge_release_job)
            except Exception:
                pass
        self._nudge_release_job = self.after(60, self._nudge_stop)
        return "break"

    def _nudge_stop(self):
        self._nudge_release_job = None
        self._nudge_held = None
        if self._nudge_job is not None:
            try:
                self.after_cancel(self._nudge_job)
            except Exception:
                pass
            self._nudge_job = None

    def set_place_tool(self, shape, name) -> None:
        """パターン配置ツールへ切り替える(shape=None で「点」へ戻る)。

        =297: shape は 0〜100 定義(標準/ユーザーパターン)。モデルの分解能
        (csv=200)へ縦に伸ばして持つ。"""
        self.place_shape = scale_shape(shape, self.pos_max) if shape \
            else None
        self.place_name = name if shape else None
        self.tool = "pattern" if shape else "point"
        self.sel_pattern = None
        self._ghost = None
        self.redraw()

    def _clear_ghost(self) -> None:
        if self._ghost is not None or self._point_ghost is not None:
            self._ghost = None
            self._point_ghost = None
            self.redraw()

    def _set_cross(self, cross, from_peer: bool = False) -> bool:
        """=233: 十字ガイドを更新し、**仲間のグラフへは縦線だけ**を配る。

        戻り値=自分の表示が変わったか(呼び出し側で redraw するため)。
        """
        changed = cross != self.cross
        self.cross = cross
        if not from_peer:
            tc = (cross[0], None) if cross else None
            subs = [g for g in (list(self.peers) + [self.mirror])
                    if g is not None and g is not self]
            for g in subs:
                try:
                    if not g.winfo_exists() or not g.winfo_ismapped():
                        continue
                except Exception:
                    continue
                if g.cross != tc:
                    g.cross = tc
                    g.redraw()
        return changed

    def _on_leave(self, _event=None):
        self._mouse_xy = None
        if self._set_cross(None):            # =233: 十字ガイドも消す
            self.redraw()
        self._clear_ghost()

    def mouse_place_at(self):
        """=227: グラフ上のマウス位置 (at, pos)。グラフの外なら None。"""
        if self._mouse_xy is None:
            return None
        x, y = self._mouse_xy
        x0, top, x1, bot = self._plot()
        if not (x0 <= x <= x1 and top <= y <= bot):
            return None
        return self.ms_of(x), self.pos_of_y(y)

    def _on_configure(self, _event=None):
        """=229: 実寸が変わったので _plot() の控えを捨てて描き直す。"""
        self._plot_cache = None
        self.redraw()

    def _on_motion(self, event):
        """カーソル位置の予定(ゴースト)を見せる。

        パターン配置ツール=予定形(仕様 4-2)/点モード=打点できる位置
        だけに小さな丸(=176。点の上・パターンの範囲では出さない)。
        """
        self._mouse_xy = (event.x, event.y)
        # =233: 十字ガイド。グリッドへ吸着した位置(=打点される位置)を通る
        x0, top, x1, bot = self._plot()
        if x0 <= event.x <= x1 and top <= event.y <= bot:
            cross = (snap(max(0.0, self.ms_of(event.x)), self.grid_at),
                     max(0, min(self.pos_max, snap(self.pos_of_y(event.y),
                                          self.grid_pos))))
        else:
            cross = None
        cross_changed = self._set_cross(cross)
        if self._drag is not None:
            self._clear_ghost()
            return
        if self.place_shape is not None:
            self._ghost = self._plan_at(self.ms_of(event.x),
                                        self.pos_of_y(event.y))
            self.redraw()
            return
        g = self._point_ghost_at(event.x, event.y)
        if g != self._point_ghost or cross_changed:
            self._point_ghost = g
            self.redraw()

    def _on_wheel(self, event):
        """ホイール=時間の拡大縮小。=197: **マウス位置を基準**にする
        (カーソル下の時刻が同じ画面位置に留まるよう view_ms を合わせる)。
        再生中の追従では次のフレームで再生位置基準に戻る。"""
        up = getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4
        ex = getattr(event, "x", None)
        ms = self.ms_of(ex) if ex is not None else None
        old = self.level
        self.set_level(self.level + (-1 if up else 1))
        if ms is not None and self.level != old and \
                not (self.follow and self.playing):
            x0, _t, x1, _b = self._plot()
            span = self.span_ms()
            frac = (ex - x0) / max(1.0, (x1 - x0))
            self.view_ms = ms - (frac - self._anchor()) * span
            self._clamp_view()       # =279
            self.redraw()
        return "break"

    def _ctrl(self, event) -> bool:
        return bool(getattr(event, "state", 0) & 0x0004)

    def _pattern_side_of(self, at: int, x: float):
        """=230: `at` が2つのパターンの**境目**なら、クリックした x の側の
        パターンを返す(左寄りなら手前・右寄りなら未来)。境目でなければ
        従来どおり `pattern_of_at()`。

        数珠つなぎで作った並びでは、境目の点は前後2つのパターンの端点を
        兼ねる。従来は常に**手前側**が選ばれていたため、未来側のパターンを
        掴みたいときに掴めなかった(=230 ユーザー報告「移動しづらい」)。
        """
        left = right = None
        for j, rec in enumerate(self.model.patterns):
            if not rec["ats"]:
                continue
            if rec["ats"][-1] == at:
                left = j
            if rec["ats"][0] == at:
                right = j
        if left is not None and right is not None:
            return right if x >= self.x_of(at) else left
        return self.model.pattern_of_at(at)

    def _on_press1(self, event):
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        self.focus_set()
        if self.on_activate is not None:      # =232: 触った方がアクティブ
            self.on_activate()
        if self.place_shape is not None:
            # パターン配置ツール(P2): クリックで配置。ドラッグはしない。
            self._drag = {"kind": "place", "x": event.x, "y": event.y,
                          "moved": 0}
            return
        hit = self.hit_point(event.x, event.y)
        hit_pat = self._pattern_side_of(hit, event.x) \
            if hit is not None else None
        if self._ctrl(event):
            # Ctrl+左クリック=選択に追加/解除。点は従来どおり、
            # パターンも追加選択できる(=185: 混在コピーの入口)
            if hit is not None and hit_pat is None:
                self.model.toggle_select(hit)
                self._notify_select()
                self.redraw()
            else:
                pat = hit_pat if hit_pat is not None else \
                    self._pattern_hit(event.x, event.y)
                if pat is not None:
                    self.model.toggle_pattern_select(pat)
                    self.sel_pattern = pat \
                        if pat in self.model.pattern_selection else None
                    self._notify_select()
                    self.redraw()
            self._drag = None
            return
        # 選択中パターンの拡縮ハンドル(仕様 5.4)
        handle = self._handle_hit(event.x, event.y)
        if handle is not None:
            self._drag = {"kind": "resize", "handle": handle,
                          "x": event.x, "y": event.y, "moved": 0,
                          "plan": None}
            return
        # =202: 剛体の掴み領域。複数選択(合計2つ以上)のときは選択範囲の
        # 帯(点線の長方形)の中の**ドラッグ**を剛体移動にする(=188の
        # 「選択済みの要素を掴む」判定を包含する)。**クリック**(動かさず
        # 離す)は従来の操作へフォールバック(_group_click_fallback)=
        # 打点・選択の操作は失われない
        if self._group_box_hit(event.x, event.y):
            self._drag = {"kind": "groupmove", "x": event.x,
                          "y": event.y, "moved": 0, "plan": None,
                          "click_fb": True,
                          # =320: 掴んだ位置に最も近い選択点の pos(縦の
                          # グリッド吸着の基準)
                          "anchor_pos": self._group_anchor_pos(event.x,
                                                               event.y)}
            return
        if hit_pat is None and hit is not None:
            # 普通の点(P1 と同じ)
            self.sel_pattern = None
            self.model.pattern_selection = set()
            if hit not in self.model.selection:
                self.model.select_only(hit)
                self._notify_select()
            self._drag = {"kind": "move", "x": event.x, "y": event.y,
                          "moved": 0, "dat": 0.0, "dpos": 0.0}
        else:
            pat = hit_pat if hit_pat is not None else                 self._pattern_hit(event.x, event.y)
            if pat is not None:
                # パターンの選択と移動(点の個別選択はできない=仕様 6a)
                self.sel_pattern = pat
                self.model.pattern_selection = {pat}
                self.model.clear_selection()
                self._notify_select()
                self._drag = {"kind": "patmove", "x": event.x,
                              "y": event.y, "moved": 0, "plan": None}
            else:
                self.sel_pattern = None
                self.model.pattern_selection = set()
                self._drag = {"kind": "rect", "x": event.x, "y": event.y,
                              "moved": 0, "cur": (event.x, event.y)}
        self.redraw()

    def _on_drag1(self, event):
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        d = self._drag
        if d is None:
            return
        d["moved"] = max(d["moved"], abs(event.x - d["x"]),
                         abs(event.y - d["y"]))
        if d["kind"] == "move":
            x0, _t, x1, _b = self._plot()
            span = self.span_ms()
            d["dat"] = (event.x - d["x"]) * (span / (x1 - x0))
            d["dpos"] = self.pos_of_y(event.y) - self.pos_of_y(d["y"])
        elif d["kind"] == "patmove":
            d["plan"] = self._patmove_plan(event)
        elif d["kind"] == "groupmove":
            # =188: 剛体移動。Δをグリッドへ丸めた (dat,dpos) が plan
            x0, _t, x1, _b = self._plot()
            span = self.span_ms()
            dat = (event.x - d["x"]) * (span / (x1 - x0))
            dpos = self.pos_of_y(event.y) - self.pos_of_y(d["y"])
            if self.pat_center is not None:
                # =320: 離散(rotate/vib)も**平行移動**(=234 の「中心を軸に
                # した拡縮」を置き換え。ユーザー決定)。縦の Δ は「掴んだ点
                # がグリッド線に乗る量」に限定し(90→40、他は同じ Δ で
                # 80→30)、全員が 0〜pos_max に収まる範囲で端で止める。
                dpos = self._discrete_group_dpos(d.get("anchor_pos"), dpos)
                d["plan"] = self.model.group_move_plan(
                    dat, dpos, self.grid_at, self.grid_pos, exact_dpos=True)
            else:
                d["plan"] = self.model.group_move_plan(
                    dat, dpos, self.grid_at, self.grid_pos)
        elif d["kind"] == "resize":
            d["plan"] = self._resize_plan(event, d["handle"])
        elif d["kind"] == "rect":
            d["cur"] = (event.x, event.y)
        self.redraw()

    def _on_release1(self, event):
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d["kind"] == "place":
            if d["moved"] < self.CLICK_PX:
                plan = self._plan_at(self.ms_of(event.x),
                                     self.pos_of_y(event.y))
                # =221: overwrite フラグ付き=重なるものを明け渡してから置く
                if plan is not None and plan.get("overwrite"):
                    done = self.model.place_plan_overwrite(
                        plan, self.place_name) == "ok"
                else:
                    done = plan is not None and \
                        self.model.place_pattern(plan, self.place_name)
                if done:
                    self._notify_change()
                    self._notify_select()
                    # =179: 配置に成功したら点モードへ戻る(ダイアログが
                    # ツールのトグル状態を戻し、set_place_tool(None) を呼ぶ)
                    if callable(self.on_placed):
                        self.on_placed()
            self._clear_ghost()
            self.redraw()
            return
        if d["kind"] == "patmove":
            if d["moved"] >= self.CLICK_PX and d.get("plan") and \
                    self.sel_pattern is not None:
                if self.model.replace_patterns(d["plan"]):
                    self._notify_change()
            self.redraw()
            return
        if d["kind"] == "groupmove":
            # =188: 剛体移動の確定(UNDO 1ステップ)
            if d["moved"] < self.CLICK_PX and d.get("click_fb"):
                # =202: 掴み領域の中のクリック=従来の操作(打点・選択)
                self._group_click_fallback(event)
                self.redraw()
                return
            if d["moved"] >= self.CLICK_PX and d.get("plan") is not None:
                gdat, gdpos = d["plan"]
                if (gdat or gdpos) and \
                        self.model.apply_group_move(gdat, gdpos):
                    self._notify_change()
                    self._notify_select()
            self.redraw()
            return
        if d["kind"] == "resize":
            if d.get("plan") and self.sel_pattern is not None:
                if self.model.replace_patterns(d["plan"]):
                    self._notify_change()
            self.redraw()
            return
        if d["kind"] == "move":
            if d["moved"] >= self.CLICK_PX:
                # 移動の確定(離した時点で1ステップ=仕様 4.8)
                if self.model.move_selected(d["dat"], d["dpos"],
                                            self.grid_at, self.grid_pos):
                    self._notify_change()
                self._notify_select()
            self.redraw()
            return
        # 空欄からのドラッグ
        if d["moved"] < self.CLICK_PX:
            if self._out_of_range(event.x, event.y):
                # =279: 枠の外のクリック=打点せず選択解除
                self._deselect_all()
                self.redraw()
                return
            # 単クリック=打点(点モード・グリッド吸着)
            if self.tool == "point":
                at = snap(max(0.0, self.ms_of(event.x)), self.grid_at)
                pos = max(0, min(self.pos_max, snap(self.pos_of_y(event.y),
                                           self.grid_pos)))
                if self.model.add_point(at, pos):
                    self._notify_change()
                self._notify_select()
        else:
            # 矩形選択(矩形に完全に入った点を選択)
            a0 = self.ms_of(d["x"])
            a1 = self.ms_of(event.x)
            p0 = self.pos_of_y(d["y"])
            p1 = self.pos_of_y(event.y)
            self.model.select_rect(a0, a1, p0, p1)
            self._notify_select()
        self.redraw()

    def _on_press3(self, event):
        if not self.readonly and self.on_activate is not None:
            self.on_activate()                # =232: 触った方がアクティブ
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        self.focus_set()
        self._drag = {"kind": "pan", "x": event.x, "y": event.y,
                      "view": self.view_ms, "moved": 0}

    def _on_drag3(self, event):
        d = self._drag
        if d is None or d.get("kind") != "pan":
            return
        d["moved"] = max(d["moved"], abs(event.x - d["x"]),
                         abs(event.y - d["y"]))
        if d["moved"] < self.CLICK_PX:
            return
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        self.follow = False
        self.view_ms = d["view"] - (event.x - d["x"]) * (span / (x1 - x0))
        self._clamp_view()           # =279: 0:00 より過去へはパンしない
        self.redraw()

    def _on_release3(self, event):
        d = self._drag
        self._drag = None
        if d is None or d.get("kind") != "pan":
            return
        if d["moved"] < self.CLICK_PX and callable(self.on_menu):
            hit = self.hit_point(event.x, event.y)
            hit_pat = self.model.pattern_of_at(hit) \
                if hit is not None else None
            if hit_pat is None:
                hit_pat = self._pattern_hit(event.x, event.y)
            if hit_pat is not None:
                # =241: 複数選択に含まれるパターンの上なら選択を保ち、
                # **選択全体のメニュー**を出す(multi=True)。
                m = self.model
                multi = hit_pat in m.pattern_selection and \
                    (len(m.pattern_selection) + len(m.selection)) >= 2
                if not multi:
                    # パターン上の右クリック=パターンを選択してメニュー(P2)
                    self.sel_pattern = hit_pat
                    m.pattern_selection = {hit_pat}
                    m.clear_selection()
                    self._notify_select()
                    self.redraw()
                self.on_menu(event, {"at": None, "pattern": hit_pat,
                                     "multi": multi,
                                     "ms": max(0.0, self.ms_of(event.x)),
                                     "pos": self.pos_of_y(event.y)})
                return
            if hit is None:
                # =199: 空欄ではメニューを出さない(選択があっても)。
                # ダブル右クリック=192が削除メニューに邪魔されないように
                return
            if hit not in self.model.selection:
                self.model.select_only(hit)
                self._notify_select()
                self.redraw()
            self.on_menu(event, {"at": hit, "pattern": None,
                                 "ms": max(0.0, self.ms_of(event.x)),
                                 "pos": self.pos_of_y(event.y)})

    def _on_double3(self, event):
        """右ダブルクリック=再生位置をそこへセットする(=192)。

        1回目の右クリックでメニューが開いた場合(パターン上・選択あり)は
        2回目のクリックがメニュー側へ行くためこのイベントは来ない=
        実質「メニューが出ない場所」でだけ効く(案A)。パンのドラッグ状態は
        ここで破棄する(release3 が何もしないように)。
        """
        if self.readonly:            # =227: サブ表示は見るだけ
            return
        self._drag = None
        if callable(self.on_seek):
            self.on_seek(max(0.0, self.ms_of(event.x)))
        return "break"

    def _selected_pos_values(self) -> list:
        """選択中の点+パターンの点の (at, pos) 一覧。"""
        m = self.model
        out = []
        for a in m.selection:
            p = m.pos_of(a)
            if p is not None:
                out.append((a, p))
        for gi in m.pattern_selection:
            if 0 <= gi < len(m.patterns):
                for a in m.patterns[gi]["ats"]:
                    p = m.pos_of(a)
                    if p is not None:
                        out.append((a, p))
        return out

    def _group_anchor_pos(self, x: float, y: float):
        """=320: 掴んだ画面位置に最も近い選択点の pos(無ければ None)。"""
        best, bd = None, None
        for a, p in self._selected_pos_values():
            dd = (self.x_of(a) - x) ** 2 + (self.y_of(p) - y) ** 2
            if bd is None or dd < bd:
                best, bd = p, dd
        return best

    def _discrete_group_dpos(self, anchor, dpos: float) -> int:
        """=320: 縦の Δ を「掴んだ点(anchor)がグリッド線に乗る量」にし、
        選択全体が 0〜pos_max に収まるよう端で止める。"""
        vals = [p for _a, p in self._selected_pos_values()]
        if not vals:
            return 0
        lo, hi = min(vals), max(vals)
        dpos = max(-lo, min(self.pos_max - hi, dpos))
        g = self.grid_pos if self.grid_pos > 1 else 1
        if anchor is None:
            anchor = vals[0]
        dq = snap(anchor + dpos, g) - anchor
        # 吸着で端をはみ出したら 1 グリッド内側へ
        while dq > 0 and hi + dq > self.pos_max:
            dq -= g
        while dq < 0 and lo + dq < 0:
            dq += g
        return int(dq)

    def _group_click_fallback(self, event):
        """掴み領域の中で動かさずに離した(=クリック)ときの従来操作(=202)。

        空欄=打点(点モード)/選択外の点=単独選択/選択外のパターン=
        単独選択。選択済みの点・パターンのクリックは選択を保つ(従来の
        =188 のクリックと同じ)。
        """
        hit = self.hit_point(event.x, event.y)
        hit_pat = self.model.pattern_of_at(hit) if hit is not None else None
        if hit_pat is None and hit is not None:
            if hit not in self.model.selection:
                self.sel_pattern = None
                self.model.pattern_selection = set()
                self.model.select_only(hit)
                self._notify_select()
            return
        pat = hit_pat if hit_pat is not None else \
            self._pattern_hit(event.x, event.y)
        if pat is not None:
            if pat not in self.model.pattern_selection:
                self.sel_pattern = pat
                self.model.pattern_selection = {pat}
                self.model.clear_selection()
                self._notify_select()
            else:
                self.sel_pattern = pat
            return
        # 空欄=打点(点モードのみ。配置ツール中は press で place になる
        # ため、ここへは来ない)
        if self._out_of_range(event.x, event.y):
            self._deselect_all()     # =279
            return
        if self.tool == "point":
            self.sel_pattern = None
            self.model.pattern_selection = set()
            at = snap(max(0.0, self.ms_of(event.x)), self.grid_at)
            pos = max(0, min(self.pos_max, snap(self.pos_of_y(event.y),
                                       self.grid_pos)))
            if self.model.add_point(at, pos):
                self._notify_change()
            self._notify_select()

    def _deselect_all(self):
        """=279: 点・パターンの選択をすべて解除する。"""
        self.sel_pattern = None
        self.model.pattern_selection = set()
        self.model.clear_selection()
        self._notify_select()

    def _sync_pat_sel(self):
        """sel_pattern を pattern_selection へ畳み込む(=185)。

        通常のクリック経路では常に同期しているが、グループ化直後など
        コード側から sel_pattern だけが設定される経路の保険。
        """
        if self.sel_pattern is not None and \
                0 <= self.sel_pattern < len(self.model.patterns):
            self.model.pattern_selection.add(self.sel_pattern)

    def _on_delete_key(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        # =185: 選択中の点+パターンをまとめて1ステップで削除
        self._sync_pat_sel()
        had_pat = bool(self.model.pattern_selection)
        if self.model.delete_selected_any():
            if had_pat:
                self.sel_pattern = None
            self._notify_change()
            self._notify_select()
            self.redraw()
        return "break"

    def _key_copy(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        # =185: 点+パターンの混在コピー(選択がそのまま入る。
        # 単独パターンの Ctrl+C=旧=180 も pattern_selection 経由で同じ)
        self._sync_pat_sel()
        self.model.copy_selected()
        return "break"

    def _key_cut(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        self._sync_pat_sel()
        had_pat = bool(self.model.pattern_selection)
        if self.model.cut_selected():
            if had_pat:
                self.sel_pattern = None
            self._notify_change()
            self._notify_select()
            self.redraw()
        return "break"

    def _paste_anchor(self):
        """Ctrl+V の基準(=185)。選択中の最も未来の端の (at, pos)。

        点なら点の at、パターンなら右端。両方選択中なら未来のほう。
        何も選んでいなければ最も未来のパターンの右端(=180 の踏襲)。
        """
        m = self.model
        best = None                      # (at, pos)
        if m.selection:
            a = max(m.selection)
            best = (a, m.pos_of(a))
        for i in m.pattern_selection:
            if 0 <= i < len(m.patterns) and m.patterns[i]["ats"]:
                hi = m.patterns[i]["ats"][-1]
                if best is None or hi > best[0]:
                    best = (hi, m.pos_of(hi))
        if best is None and self.sel_pattern is not None and \
                self.sel_pattern < len(m.patterns) and \
                m.patterns[self.sel_pattern]["ats"]:
            hi = m.patterns[self.sel_pattern]["ats"][-1]
            best = (hi, m.pos_of(hi))
        if best is None:
            mf = m.most_future_pattern()
            if mf is not None:
                hi = m.patterns[mf]["ats"][-1]
                best = (hi, m.pos_of(hi))
        return best

    def _key_paste(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        # =185: 混在クリップボードの上書き貼り付け。
        # パターンを含む=アンカーの端に先頭が載り、高さもアンカーへ
        # 平行移動(=180 の踏襲)/点のみ=選択中で最も at が大きい点に
        # 先頭が載り、高さは絶対値のまま(=172 の踏襲)。
        m = self.model
        if not m.has_clipboard():
            return "break"
        if m.clipboard_patterns:
            anchor = self._paste_anchor()
            if anchor is None:
                return "break"          # 置き場所が決まらない=無音(10b)
            first = m.clip_first_pos()
            # =227: 離散的なスクリプトでは **高さは固定**(直前のパターンへ
            # 合わせる「連続性」は付けない。ユーザー要望4)
            if self.pat_center is not None:
                voff = 0
            else:
                voff = (anchor[1] - first) if (first is not None
                                               and anchor[1] is not None) \
                    else 0
            result = m.paste_clip(float(anchor[0]), voff)
        else:
            result = m.paste_over_anchor()
        if result == "ok":
            # 続けて Ctrl+V で数珠つなぎできるよう、貼ったパターンの
            # うち最も未来のものを選択状態にする
            if m.pattern_selection:
                self.sel_pattern = max(
                    m.pattern_selection,
                    key=lambda i: m.patterns[i]["ats"][-1])
            self._notify_change()
            self._notify_select()
            self.redraw()
        elif result in ("no_selection", "range", "edge") and \
                callable(self.on_paste_reject):
            # 拒否の理由をダイアログ側へ知らせる(=171/=172/=185)
            self.on_paste_reject(result)
        return "break"

    def _key_undo(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        if self.model.undo():
            self._after_history()
        return "break"

    def _key_redo(self, _event=None):
        if self.readonly:            # =227: サブ表示は見るだけ
            return "break"
        if self.model.redo():
            self._after_history()
        return "break"

    def _after_history(self):
        """=298: UNDO/REDO 後の後始末。戻したのが**仲間のモデル**(左右
        共有の UNDO)なら、そのグラフの選択パターン表示も畳んで描き直す。"""
        target = self.model.last_undone
        for g in [self] + [p for p in self.peers if p is not None]:
            if g.model is target or g is self:
                g.sel_pattern = None
                g.redraw()
        self._notify_change()
        self._notify_select()

    def _notify_change(self):
        if callable(self.on_change):
            self.on_change()

    def _notify_select(self):
        if callable(self.on_select):
            self.on_select()
