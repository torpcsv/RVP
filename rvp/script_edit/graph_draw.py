"""スクリプト編集: 描画(redraw・波形帯・ヒートマップ・目盛り)(mixin)。"""
from __future__ import annotations

from .. import appfont
from ..i18n import tr

from .common import WAVE_BAND_COLOR, draw_audio_band, moved_points


class _ScriptEditGraphDrawMixin:
    """ScriptEditGraph の mixin(=301 分割)。描画(redraw・波形帯・ヒートマップ・目盛り)"""

    def _heat_color(self, a0, p0, a1, p1):
        """区間 (a0,p0)→(a1,p1) のヒートマップ色(=195)。無色は None。"""
        dt = a1 - a0
        dp = abs(p1 - p0)
        if dt <= 0 or dp == 0:
            return None                     # 速度0(水平)は無色
        speed = (dp / dt) * 60.0
        for lo, col in self.HEAT_LEVELS:
            if speed >= lo and speed > 0:
                return col
        return None

    def fmt_time_ms(self, ms: float, hours=None) -> str:
        """時間軸ラベル(=210: 日英共通・=179 のミリ秒精度はそのまま)。
        =318: 普段は **mm:ss.fff**、素材が 1 時間以上(time_hours)のときだけ
        **hh:mm:ss.fff**。hours を渡すと明示できる。"""
        if hours is None:
            hours = getattr(self, "time_hours", False)
        neg = ms < 0
        ms = int(round(abs(ms)))
        h, rem = divmod(ms, 3600_000)
        m, rem = divmod(rem, 60_000)
        s, f = divmod(rem, 1000)
        if hours or h:
            return ("-" if neg else "") + f"{h:02d}:{m:02d}:{s:02d}.{f:03d}"
        return ("-" if neg else "") + f"{m:02d}:{s:02d}.{f:03d}"

    def fmt_guide_time(self, ms: float) -> str:
        """=319: ガイド表示の時間。csv(分解能 200=100ms 単位)は小数 1 桁。"""
        txt = self.fmt_time_ms(ms)
        return txt[:-2] if self.pos_max == 200 else txt

    def guide_text(self, at: float, pos: float) -> str:
        """=319: マウス位置のガイド文言(グリッド吸着後の値)。"""
        t = self.fmt_guide_time(at)
        c = self.pat_center
        if c is not None and c > 0:
            # 回転(csv=中心 100 / rotate funscript=中心 50): 符号付きの速度
            spd = int(round((pos - c) * 100.0 / c))
            return tr("時間={0} 回転速度={1}").format(t, f"{spd:+d}" if spd else "0")
        if c == 0:
            return tr("時間={0} 強さ={1}").format(t, int(pos))
        return tr("時間={0} 位置={1}").format(t, int(pos))

    @staticmethod
    def fmt_scale_s(sec: float) -> str:
        """縮尺表示の文言(=210)。副線(mid)の間隔を「0.25秒」「7分30秒」
        のように短く(1分未満=秒・小数はそのまま / 1分以上=分+秒)。"""
        sec = float(sec)
        if sec < 60.0:
            return tr("{0}秒").format(f"{sec:g}")
        m = int(sec // 60)
        s = sec - m * 60
        if s <= 0:
            return tr("{0}分").format(m)
        return tr("{0}分{1}秒").format(m, f"{s:g}")

    def _guide_font(self):
        """=319: ガイド文言の幅計測用フォント(1度だけ作る)。"""
        fnt = getattr(self, "_gd_font", None)
        if fnt is None:
            import tkinter.font as tkfont
            fnt = tkfont.Font(root=self, family=appfont.FAMILY, size=9)
            self._gd_font = fnt
        return fnt

    def label_px(self) -> int:
        """時間ラベル1つぶんの幅(px)。=210: hh:mm:ss.FFF の実測。
        フォントは1度だけ作って使い回す。"""
        fnt = getattr(self, "_lbl_font", None)
        if fnt is None:
            import tkinter.font as tkfont
            fnt = tkfont.Font(root=self, family=appfont.FAMILY, size=8)
            self._lbl_font = fnt
        return int(fnt.measure("00:00:00.000" if getattr(self, "time_hours",
                                                          False)
                               else "00:00.000"))

    def _plot(self):
        """描画領域 (x0, top, x1, bot)。=210: 上端に縮尺表示の帯
        (SCALE_H)を確保し、グラフ本体はその下から始まる。

        **=229: 1フレームぶんキャッシュする**。`winfo_width()` /
        `winfo_height()` は Tk への往復で、`x_of` / `y_of` 経由で
        1フレームに数千回呼ばれるため、**描画時間の3〜4割**をここで
        使っていた(60fps 化の最大のボトルネック)。実寸が変わるのは
        `<Configure>` のときだけなので、そこと `redraw()` の先頭で
        捨てれば十分。
        """
        p = self._plot_cache
        if p is None:
            w = self.winfo_width()
            h = self.winfo_height()
            # =298: 縮尺表示を出さないグラフは上端を詰める(「100」ラベル・
            # 見出し・時間グリッドの張り出し(top-12)ぶんの 14px は残す)
            top = self.TOP_PAD + self.SCALE_H if self.show_scale else 14
            # 時間ラベルを出さないグラフは下端も詰める(ヒートマップの帯が
            # 出るときはその高さぶんだけ残す)
            bottom = self.AXIS_H if self.show_time else \
                (16 if self.heat else 6)
            p = (self.GUTTER, top, max(self.GUTTER + 1, w - 4),
                 max(top + 1, h - bottom))
            self._plot_cache = p
        return p

    def set_playing(self, playing: bool):
        """再生状態の通知(=198/=201)。再生を開始したら追従アンカーは
        中央になり、一時停止しても中央のまま(画面は動かない)。"""
        playing = bool(playing)
        if playing:
            self._played = True
        if playing == self.playing:
            return
        self.playing = playing
        self.redraw()

    def _draw_audio_wave(self, x0, top, x1, bot, left, span):
        """=243: 音声波形(振幅の帯)を背景へ描く。

        モノラル=合成chを全高で1本。ステレオ=上半分に L・下半分に R
        (「(逆)」で入れ替え)。wave_channel が 0/1 のとき(UFOTW の
        左右2本)は、そのchを全高で描く(「(逆)」で入れ替え)。
        """
        env = self.wave_env
        mode = self.wave_mode
        if not env or mode == "off" or x1 - x0 < 8 or bot - top < 8:
            return
        color = self._c(WAVE_BAND_COLOR)
        ppm = (x1 - x0) / span
        off = float(self.wave_offset)

        def ms_of_px(x):
            return left + (x - x0) / ppm + off

        bucket = env["bucket_ms"]
        peak = env["peak"]
        chans = env["chans"]
        if mode == "mono" or len(chans) < 2:
            draw_audio_band(self, env["mono"], bucket, peak, x0, x1,
                            ms_of_px, (top + bot) / 2.0,
                            (bot - top) / 2.0, color)
            return
        rev = mode == "stereo_rev"
        a, b = (1, 0) if rev else (0, 1)
        if self.wave_channel is not None:
            # 0=上(左)→L / 1=下(右)→R。「(逆)」は割り当てを入れ替える
            ch = chans[(int(self.wave_channel) + (1 if rev else 0)) % 2]
            draw_audio_band(self, ch, bucket, peak, x0, x1, ms_of_px,
                            (top + bot) / 2.0, (bot - top) / 2.0, color)
            return
        mid = (top + bot) / 2.0
        half = (bot - top) / 4.0
        draw_audio_band(self, chans[a], bucket, peak, x0, x1, ms_of_px,
                        (top + mid) / 2.0, half, color)
        draw_audio_band(self, chans[b], bucket, peak, x0, x1, ms_of_px,
                        (mid + bot) / 2.0, half, color)

    def redraw(self):
        _DG = self._DG
        self._plot_cache = None       # =229: 実寸はフレームごとに1回だけ読む
        self.delete("all")
        self.configure(bg=self.bg_color())
        self._sync_mirror()
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 40:
            return
        x0, top, x1, bot = self._plot()
        span = self.span_ms()
        left = self.view_ms - span * self._anchor()
        fine, mid, coarse = self.LEVELS[self.level]

        # =243: 音声波形の帯(基準線・点・線の背面=最初に描く)
        self._draw_audio_wave(x0, top, x1, bot, left, span)

        # 時間の基準線。=176: グリッド[at]が「なし(1)」以外のときは、
        # **細線をatグリッドの線に置き換える**(縦横の薄い線の交点=
        # 打点できる位置、が成り立つように)。1(なし)のときは従来どおり
        # 縮尺段階の細線を描く。グリッド線が詰まりすぎる縮尺(4px未満)では
        # 細線へフォールバックする。
        grid_px = self.grid_at * ((x1 - x0) / span)
        use_grid_lines = self.grid_at > 1 and grid_px >= 4
        if use_grid_lines:
            t = int(max(0.0, left) // self.grid_at) * self.grid_at
            col = self._c(_DG.C_GRID_T[0])
            while t <= left + span + 1:
                x = self.x_of(t)
                if x0 - 1 <= x <= w and t >= 0:
                    self.create_line(x, top, x, bot, fill=col)
                t += self.grid_at
        steps = [(mid, self._c(_DG.C_GRID_T[1])),
                 (coarse, self._c(_DG.C_GRID_T[2]))]
        if not use_grid_lines:
            steps.insert(0, (fine, self._c(_DG.C_GRID_T[0])))
        for step_s, color in steps:
            step = step_s * 1000.0
            t = int(left // step) * step
            while t <= left + span + 1:
                x = self.x_of(t)
                if x0 - 1 <= x <= w:
                    self.create_line(x, top - 12, x, bot, fill=color)
                t += step

        # 位置の基準線。posグリッド(10単位など)を薄く、0/中央/上端 を濃く。
        # =297: csv は 0〜200(中央 100)。
        pm = self.pos_max
        pc = int(self.pos_center)
        if self.grid_pos > 1:
            p = 0
            while p <= pm:
                if p not in (0, pc, pm):
                    y = self.y_of(p)
                    self.create_line(x0, y, w, y,
                                     fill=self._c(_DG.C_GRID_POS_SUB))
                p += self.grid_pos
        for p in (0, pc, pm):
            y = self.y_of(p)
            self.create_line(x0, y, w, y, fill=self._c(_DG.C_GRID_POS))
        gx = self.GUTTER - 4
        # =233: csv(回転速度)では **下から -100 / 0 / 100** と書く
        # (=297: 中央 pos100=停止・上=正回転・下=逆回転。速度 1 刻み)
        _axis = ({pm: "100", pc: "0", 0: "-100"}
                 if self.pos_axis == "speed" else None)
        for p, anc in ((pm, "ne"), (pc, "e"), (0, "se")):
            self.create_text(gx, self.y_of(p), anchor=anc,
                             text=(_axis[p] if _axis else str(p)),
                             fill=self._c(_DG.C_AXIS_TEXT),
                             font=(appfont.FAMILY, 8))

        # 区間の目印(仕様 4.2: 縦の破線2本。編集はできない)
        lo, hi, given = self.region
        if given:
            col = self._c(_DG.C_HEAD)
            for t in (lo, hi):
                if t is None:
                    continue
                x = self.x_of(float(t))
                if x0 <= x <= w:
                    self.create_line(x, top, x, bot, fill=col, dash=(4, 3))

        # 波形(移動ドラッグ中は選択点へ暫定の移動を適用したゴーストを描く)
        pts = list(self.model.points)
        sel = set(self.model.selection)
        drag = self._drag if (self._drag and
                              self._drag.get("kind") == "move" and
                              self._drag["moved"] >= self.CLICK_PX) else None
        ghost_map = {}
        if drag:
            ghost_map, _ok = moved_points(pts, sel, drag["dat"], drag["dpos"],
                                          self.grid_at, self.grid_pos,
                                          pos_max=self.pos_max)
        disp = []
        for at, pos in pts:
            if at in ghost_map:
                disp.append((ghost_map[at][0], ghost_map[at][1], at in sel))
            else:
                disp.append((at, pos, at in sel))
        disp.sort(key=lambda t: t[0])

        wave = self._c(_DG.C_WAVE_POS)
        acc = self._c(("#c9451a", "#ff8a3d"))     # 選択(オレンジ系の赤)
        patc = self._c(self.PATTERN_GREEN)        # パターンの線と点(=184)
        # =184: パターンの時間範囲の内側の線分は緑で描く(青の波形と
        # 一目で見分けられるように)。範囲=各パターンの (先頭at, 末尾at)
        # =189/=196: 選択中のものは線も赤にする。判定は「両端とも選択集合
        # (選択中の点 ∪ 選択中パターンの構成点)に入る線分」。これで
        # パターン内の線分に加え、**選択中の点と選択中パターンの端点を結ぶ
        # 線分**・選択中パターンどうしをつなぐ線分も赤になる(=196)
        sel = self.model.selection
        sel_pat_idx = set(self.model.pattern_selection)
        if self.sel_pattern is not None:
            sel_pat_idx.add(self.sel_pattern)
        pat_ranges = []
        sel_pat_ats = set()
        for i, rec in enumerate(self.model.patterns):
            if not rec["ats"]:
                continue
            pat_ranges.append((rec["ats"][0], rec["ats"][-1]))
            if i in sel_pat_idx:
                sel_pat_ats.update(rec["ats"])
        selset = sel | sel_pat_ats
        # 線(linear/twist は隣り合う点を斜め線で結ぶ)
        # =224: csv(step)は**階段**で結ぶ(次の点まで同じ値を保つ)
        # **=229: 同じ色が続くぶんは1本のポリラインにまとめる**
        # (1フレームのキャンバスアイテム数が描画時間をほぼ決めるため。
        #  色の変わり目・表示範囲外で切る。見た目は従来と同じ)
        _run = []
        _run_col = None

        def _flush_run():
            nonlocal _run, _run_col
            if _run_col is not None and len(_run) >= 4:
                self.create_line(*_run, fill=_run_col, width=2, tags="wave")
            _run, _run_col = [], None

        for i in range(len(disp) - 1):
            a0, p0, _s0 = disp[i]
            a1, p1, _s1 = disp[i + 1]
            if a1 < left - span or a0 > left + span * 1.5:
                _flush_run()
                continue
            if a0 in selset and a1 in selset:
                col = acc
            elif any(lo <= a0 and a1 <= hi for lo, hi in pat_ranges):
                col = patc
            else:
                col = wave
            xa, ya = self.x_of(a0), self.y_of(p0)
            xb, yb = self.x_of(a1), self.y_of(p1)
            if col != _run_col:
                _flush_run()
                _run_col = col
                _run = [xa, ya]
            if self.step:
                _run += [xb, ya, xb, yb]
            else:
                _run += [xb, yb]
        _flush_run()
        # =224: 階段の「最初の点の前=停止」と「最後の点のあと=保持」も描く
        if self.step and self.model.points:
            fa, fp = self.model.points[0]
            la, lp = self.model.points[-1]
            # =297: 停止の高さは中心(csv=100 / rotate funscript=50 /
            # vibration=0)。pat_center が無ければ従来の定数。
            ys = self.y_of(self.pat_center if self.pat_center is not None
                           else self.pos_center)
            if fa > left:
                self.create_line(max(x0, self.x_of(left)), ys,
                                 min(w, self.x_of(fa)), ys,
                                 fill=wave, width=2, tags="wave")
                self.create_line(self.x_of(fa), ys, self.x_of(fa),
                                 self.y_of(fp), fill=wave, width=2,
                                 tags="wave")
            if self.x_of(la) < w:
                self.create_line(max(x0, self.x_of(la)), self.y_of(lp),
                                 w, self.y_of(lp),
                                 fill=wave, width=2, tags="wave")
        # 点(選択中は大きく・色を変える)。パターンの構成点は緑の丸○
        # (=184。=176のひし形◇は撤回)。端点が共有されている箇所=
        # 二重丸◎(外側緑・内側白)。選択中のパターンの構成点は赤(=189)
        pat_ats = self.model.pattern_ats()
        shared_eps = self.model.shared_endpoints()
        for at, pos, selected in disp:
            x, y = self.x_of(at), self.y_of(pos)
            if x < x0 - 8 or x > w + 8:
                continue
            if at in pat_ats:
                pcol = acc if at in sel_pat_ats else patc
                if at in shared_eps:
                    self.create_oval(x - 5, y - 5, x + 5, y + 5,
                                     fill=pcol, outline="", tags="wave")
                    self.create_oval(x - 2, y - 2, x + 2, y + 2,
                                     fill="#ffffff", outline="",
                                     tags="wave")
                else:
                    self.create_oval(x - 3, y - 3, x + 3, y + 3,
                                     fill=pcol, outline="", tags="wave")
                continue
            r = 5 if selected else 3
            self.create_oval(x - r, y - r, x + r, y + r,
                             fill=acc if selected else wave,
                             outline="", tags="wave")

        # 配置済みパターンの外形(仕様 5.3。linear/twist=平行四辺形 /
        # =228 離散的なスクリプト=矩形)+ハンドル。
        # =176: 実線・1px太く。=180/=181: さらに薄い灰色
        pat_col = self._c(("#d1d1d1", "#4e4e4e"))   # =181: =180のさらに半分
        for idx in range(len(self.model.patterns)):
            band = self._pattern_band(idx)
            if band is None:
                continue
            plo, phi, ptop, pbot = band
            if phi < left - span or plo > left + span * 1.5:
                continue
            xl, xr = self.x_of(plo), self.x_of(phi)
            corners = (xl, self.y_of(ptop[0]), xr, self.y_of(ptop[1]),
                       xr, self.y_of(pbot[1]), xl, self.y_of(pbot[0]))
            selected = (idx == self.sel_pattern
                        or idx in self.model.pattern_selection)
            self.create_polygon(*corners, fill="", outline=pat_col,
                                width=3 if selected else 2)
            if selected and self._drag is None:
                for _code, (hx, hy) in self._handles(idx).items():
                    self.create_rectangle(hx - 3, hy - 3, hx + 3, hy + 3,
                                          fill=pat_col, outline="")
        # =181: 波形(青線)と点をパターンの枠より前面へ
        self.tag_raise("wave")

        # =202: 剛体の掴み領域(点線の長方形)。複数選択(2つ以上)のとき、
        # atの範囲×pos全域を赤の点線で囲む=この中はどこを掴んでも剛体移動
        grng = self._group_box_range()
        if grng is not None and self._drag is None:
            gx0 = max(x0 - 1, self.x_of(grng[0]))
            gx1 = min(w + 1, self.x_of(grng[1]))
            if gx1 > x0 and gx0 < w:
                self.create_rectangle(gx0, top, gx1, bot,
                                      outline=acc, dash=(4, 3), width=1)

        # 移動・拡縮ドラッグ中のゴースト(仕様 4-2)。=175以降は
        # {index: plan} の複数plan(隣接パターンの追従を含む)を全部描く
        gplans = []
        if self._drag and self._drag.get("kind") in ("patmove", "resize"):
            dp = self._drag.get("plan")
            if dp:
                gplans = list(dp.values())
        elif self._drag and self._drag.get("kind") == "groupmove":
            gm = self._drag.get("plan")
            gsc = self._drag.get("scale")
            if gm is not None and \
                    (gm[0] or gm[1] or
                     (gsc is not None and abs(gsc[1] - 1.0) > 1e-9)) and \
                    self._drag["moved"] >= self.CLICK_PX:
                gdat, gdpos = gm

                def _gp(a):                      # =234: 拡縮つき
                    p = self.model.pos_of(a)
                    return (self.model._scaled_pos(p, gsc)
                            if gsc is not None else p + gdpos)

                for gi in sorted(self.model.pattern_selection):
                    if 0 <= gi < len(self.model.patterns) and \
                            self.model.patterns[gi]["ats"]:
                        gplans.append({"points": [
                            (a + gdat, _gp(a))
                            for a in self.model.patterns[gi]["ats"]]})
                for ga in sorted(self.model.selection):
                    gplans.append({"points": [(ga + gdat, _gp(ga))]})
        elif self._ghost is not None:
            gplans = [self._ghost]
            # =221: 上書きになる配置は、**消えるもの**を赤で予告する
            # (クリックする前に何が失われるか分かるように。ユーザー決定)
            if self._ghost.get("overwrite"):
                gp = self._ghost["points"]
                dpats, dats = self.model.overwrite_preview(
                    gp[0][0], gp[-1][0], gp[0][1], gp[-1][1])
                # 選択の色(オレンジ系の赤)とは別の、はっきりした赤にする
                rcol = self._c(("#df1b1b", "#ff5b5b"))
                for pi in sorted(dpats):
                    rec = self.model.patterns[pi]
                    ats = rec["ats"]
                    for i in range(len(ats) - 1):
                        x_a, x_b = self.x_of(ats[i]), self.x_of(ats[i + 1])
                        y_a = self.y_of(self.model.pos_of(ats[i]))
                        y_b = self.y_of(self.model.pos_of(ats[i + 1]))
                        if self.step:
                            # =227: 階段のグラフでは赤い予告も直角で描く
                            # (斜め線になっていた不具合)
                            self.create_line(x_a, y_a, x_b, y_a,
                                             fill=rcol, width=3)
                            self.create_line(x_b, y_a, x_b, y_b,
                                             fill=rcol, width=3)
                        else:
                            self.create_line(x_a, y_a, x_b, y_b,
                                             fill=rcol, width=3)
                for a in sorted(dats):
                    p = self.model.pos_of(a)
                    if p is None:
                        continue
                    x, y = self.x_of(a), self.y_of(p)
                    self.create_oval(x - 4, y - 4, x + 4, y + 4,
                                     fill=rcol, outline=rcol)
        if gplans:
            gcol = self._c(("#1f8a4d", "#4dd68a"))
            for dplan in gplans:
                gp = dplan["points"]
                for i in range(len(gp) - 1):
                    if self.step:       # =224: ゴーストも階段で描く
                        self.create_line(self.x_of(gp[i][0]),
                                         self.y_of(gp[i][1]),
                                         self.x_of(gp[i + 1][0]),
                                         self.y_of(gp[i][1]),
                                         fill=gcol, width=2, dash=(4, 3))
                        self.create_line(self.x_of(gp[i + 1][0]),
                                         self.y_of(gp[i][1]),
                                         self.x_of(gp[i + 1][0]),
                                         self.y_of(gp[i + 1][1]),
                                         fill=gcol, width=2, dash=(4, 3))
                        continue
                    self.create_line(self.x_of(gp[i][0]),
                                     self.y_of(gp[i][1]),
                                     self.x_of(gp[i + 1][0]),
                                     self.y_of(gp[i + 1][1]),
                                     fill=gcol, width=2, dash=(4, 3))
                for a, p in gp:
                    x, y = self.x_of(a), self.y_of(p)
                    self.create_oval(x - 3, y - 3, x + 3, y + 3,
                                     fill="", outline=gcol)
        # =233: マウス位置の十字ガイド(1px)。グラフの端まで伸ばし、
        # 仲間のグラフ(左右のもう片方・サブ)には**縦線だけ**を映す。
        if self.cross is not None and self._drag is None:
            ca, cp = self.cross
            ccol = self._c(("#1f8a4d", "#4dd68a"))
            cx = self.x_of(ca)
            if x0 <= cx <= w:
                self.create_line(cx, top, cx, bot, fill=ccol, width=1)
            if cp is not None:
                cy = self.y_of(cp)
                self.create_line(x0, cy, w, cy, fill=ccol, width=1)
                # =319: カーソルの右上に「時間=00:10.000 位置=50」
                # (黒背景・白文字。右端・上端では内側へ折り返す)
                mx, my = self._mouse_xy if self._mouse_xy else (cx, cy)
                txt = self.guide_text(ca, cp)
                fnt = (appfont.FAMILY, 9)
                tw = self._guide_font().measure(txt) + 8
                th = 16
                gx = mx + 12
                gy = my - 10
                if gx + tw > w:
                    gx = mx - 12 - tw
                if gy - th < 0:
                    gy = my + 10 + th
                self.create_rectangle(gx, gy - th, gx + tw, gy,
                                      fill="#000000", outline="")
                self.create_text(gx + 4, gy - th / 2, anchor="w",
                                 text=txt, fill="#ffffff", font=fnt)

        # 点モードのゴースト(=176: 打点できる位置だけに出る)
        if self._point_ghost is not None and self._drag is None:
            ga, gp_ = self._point_ghost
            x, y = self.x_of(ga), self.y_of(gp_)
            gcol = self._c(("#1f8a4d", "#4dd68a"))
            self.create_oval(x - 4, y - 4, x + 4, y + 4,
                             outline=gcol, width=2, fill="")

        # 矩形選択のラバーバンド
        if self._drag and self._drag.get("kind") == "rect" \
                and self._drag["moved"] >= self.CLICK_PX:
            cx, cy = self._drag["cur"]
            self.create_rectangle(self._drag["x"], self._drag["y"], cx, cy,
                                  outline=self._c(_DG.C_PLAYHEAD),
                                  dash=(3, 2))

        # =232: 右上の見出し(「左（ロータ1）」など。同時編集の目印)
        if self.corner_text:
            self.create_text(w - 6, top - 12, anchor="ne",
                             text=self.corner_text,
                             fill=self._c(_DG.C_AXIS_TEXT),
                             font=(appfont.FAMILY, 9, "bold"))

        # 再生位置(=206: plain モードでは描かない)
        if not self.plain:
            nx = self.x_of(self.now_ms)
            if x0 <= nx <= w:
                self.create_line(nx, top - 12, nx, bot,
                                 fill=self._c(_DG.C_PLAYHEAD), width=1)

        # ヒートマップ(=195): 隣り合う点の区間の速度を色の帯で警告する。
        # speed = |Δpos| ÷ Δt(ms) × 60(TFGと同じ式)。速度0と点の無い
        # 区間は無色。位置は pos=0 の線のすぐ下・時間ラベルの上。
        # =224: csv(ROTATE)は「速度そのもの」を描いているので、
        # ストローク速度の警告は意味を持たない=出さない
        pts_all = self.model.points if self.heat else []
        # =247: 帯の中心を bot+4 → bot+8 へ下げ、pos=0 の打点と重ならない
        # ようにする(最大太さ4px=帯上端 bot+6。選択中の点は半径5px=下端
        # bot+5 なので、1px の隙間で接触しない)。時間ラベルも +4px 下げる。
        hy = bot + 8
        # =209: 高速の警告を明確にするため速度で帯を太くする(=247で色は
        # スライドしたが、しきい値基準は不変: speed≥20=2px太く(赤・
        # オレンジ)/12-20=1px太く(黄・緑)/それ未満=2px)
        heat_w = {}
        for hidx, (_thr, hc) in enumerate(self.HEAT_LEVELS):
            heat_w.setdefault(hc, 4 if hidx <= 1 else (3 if hidx <= 3 else 2))
        # =229: 帯も**同じ色が続くぶんは1本**にまとめる(アイテム数を減らす)
        _hrun = []
        _hcol = None

        def _flush_heat():
            nonlocal _hrun, _hcol
            if _hcol is not None and len(_hrun) >= 4:
                self.create_line(*_hrun, fill=self._c(_hcol),
                                 width=heat_w.get(_hcol, 2))
            _hrun, _hcol = [], None

        for i in range(len(pts_all) - 1):
            a0, p0 = pts_all[i]
            a1, p1 = pts_all[i + 1]
            if a1 < left - span or a0 > left + span * 1.5:
                _flush_heat()
                continue
            hcol = self._heat_color(a0, p0, a1, p1)
            if hcol is None:
                _flush_heat()
                continue
            xa = max(x0, self.x_of(a0))
            xb = min(w, self.x_of(a1))
            if hcol != _hcol:
                _flush_heat()
                _hcol = hcol
                _hrun = [xa, hy]
            _hrun += [xb, hy]
        _flush_heat()

        # 時間軸(=190: 主線に加えて副線の位置にもラベルを出す。
        # 主線=coarse は mid の倍数なので mid 刻みで全部に出る)
        # =210: hh:mm:ss.FFF(12文字)はラベル幅が広いので、ラベル幅+余白が
        # 刻み幅(px)を超えるときは刻みを2倍ずつ粗くして**間引く**
        # (縮尺表示があるので間隔は読み取れる)
        step = mid * 1000.0
        px_per_ms = (x1 - x0) / span
        need = self.label_px() + 8
        while step * px_per_ms < need and step < span:
            step *= 2.0
        self._label_step_ms = step
        t = int(left // step) * step
        while t <= left + span + 1 and self.show_time:
            if t >= 0:
                x = self.x_of(t)
                if x0 <= x <= w:
                    # =247: ヒートマップの帯を下げたぶんラベルも +4px
                    self.create_text(x, bot + 12, anchor="n",
                                     text=self.fmt_time_ms(t),
                                     fill=self._c(_DG.C_AXIS_TEXT),
                                     font=(appfont.FAMILY, 8))
            t += step

        # 縮尺表示(=210): 左上(pos「100」の上)に副線(mid)の間隔を
        # 「0.25秒┗━┛」のように描く。ブラケットの横幅=実際の mid 間隔ぶん
        # (Google Map の縮尺と同じ考え方=長さそのものが縮尺の実感)
        # 基線は「100」ラベル(top の上側に約12px)のさらに上
        if self.show_scale:
            sy = top - 14
            scol = self._c(_DG.C_AXIS_TEXT)
            tid = self.create_text(4, sy, anchor="sw",
                                   text=self.fmt_scale_s(mid), fill=scol,
                                   font=(appfont.FAMILY, 8),
                                   tags=("scale_text",))
            bx0 = self.bbox(tid)[2] + 4
            bw = mid * 1000.0 * px_per_ms
            bx1 = min(float(w - 4), bx0 + bw)
            self.create_line(bx0, sy - 4, bx0, sy, bx1, sy, bx1, sy - 4,
                             fill=scol, width=1, tags=("scale_bar",))

        if not self.follow and not self.plain and self.show_follow_hint:
            self.create_text(w - 4, 2, anchor="ne",
                             text=tr("追従停止中（再生で戻る）"),
                             fill=self._c(("#8f6300", "#e0a23a")),
                             font=(appfont.FAMILY, 8, "bold"))
