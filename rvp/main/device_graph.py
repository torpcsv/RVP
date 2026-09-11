"""デバイス動作グラフ(DeviceGraph)。"""
from __future__ import annotations

import bisect
import customtkinter as ctk
import tkinter as tk
from .. import appfont
from ..i18n import tr
from .. import apptheme

from .common import OK_COLOR, WARN_TEXT
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class DeviceGraph(tk.Canvas):
    """③グラフ表示(=69): 実行中スクリプトの波形を共通の時間軸に描く。

    横軸=イベント入場からの経過時間 / 縦軸=pos(0-100)。デバイス種別ごとに
    1本のグラフを縦に並べ、時間の縮尺と縦の基準線・時間ラベルは全体で共有する。

    - linear は始点・終点の間も動き続けるので**斜め線**(三角波のような見た目)
    - rotate / vibration は次の指示まで同じ強さを保つので**直角の線**(矩形波)
    - ホイールで時間縮尺を9段階に拡大縮小 / ドラッグで自動追従を外して移動 /
      単クリックで自動追従へ復帰。
    """

    # (細線, 中線, 太線) の間隔(秒)。表示幅は常に「太線×2.5」= 細線×25。
    # 縮尺段階(=176で細分化)。各段は (細線, 中線, 太線) の秒数で、
    # 表示幅 = 太線×2.5。従来の9段の**それぞれの間に中間段**を足し、
    # 最大拡大側へさらに1段(0.5=従来の2倍拡大)を追加した。
    # どの段も (太線/10, 太線/2, 太線) の関係(従来と同じ規則)。
    # =226: 中ほどの刻み(=縮尺表示に出る副線の間隔)を
    # **1.5秒→2秒 / 2.5秒→3秒 / 3.5秒→4秒** へ(ユーザー要望9)。
    # LEVELS は (coarse/10, coarse/2, coarse) なので、coarse を
    # 3→4 / 5→6 / 7→8 に変えると副線が 1.5/2.5/3.5 → 2/3/4 になる。
    # 編集グラフ(script_edit)も再生タブもこの LEVELS を共有している。
    # =231: 最大拡大の縮尺表示を **0.25秒 → 0.2秒**(coarse 0.5 → 0.4)
    _COARSE = (0.4, 1.0, 1.5, 2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0,
               30.0, 40.0, 60.0, 100.0, 200.0, 300.0, 600.0, 900.0)
    LEVELS = tuple((c / 10.0, c / 2.0, c) for c in _COARSE)
    DEFAULT_LEVEL = 7          # [1秒/5秒/10秒] = 表示幅25秒(従来と同じ)

    # 表示順(上から)。実行中の種別だけを表示する。
    ORDER = ("linear", "twist", "rotate_ufo", "rotate_ufo_r",
             "rotate_a10", "rotate_a10_r", "vibration")

    GUTTER = 30      # 左端の位置目盛(100/50/0)欄
    HEAD_H = 15      # 各グラフの見出し行(種別名)
    AXIS_H = 18      # 下端の時間軸
    ROW_GAP = 6      # グラフ間の余白
    # グラフ1本ぶんの高さ(**固定**。ユーザー決定=本数で高さを変えない)。
    # 690x820 のウィンドウで4本がちょうど収まる値。5本になったときは
    # はみ出すので、上下方向のドラッグで送って見る(=横のドラッグと同じ操作)。
    ROW_H = 87
    CLICK_PX = 5     # この移動量未満の押下は「単クリック」

    # 色(ライト, ダーク)
    C_BG = ("gray94", "#333333")
    # =191: ライト系(ライト+カラーテーマ6色)で線が視認できないため、
    # ライト側だけ一段ずつ濃くした(細=旧中 / 中=旧太 / 太=さらに一段濃く。
    # ただし再生位置の線 C_PLAYHEAD #404040 より薄いこと)。ダーク側は不変。
    C_GRID_POS = ("#9e9e9e", "#6e6e6e")     # 位置の基準線 0/50/100
    C_GRID_POS_SUB = ("#c6c6c6", "#484848")  # 位置の基準線 25/75
    # 時間の基準線(細, 中, 太)
    C_GRID_T = (("#d2d2d2", "#3d3d3d"), ("#bcbcbc", "#4e4e4e"),
                ("#a6a6a6", "#666666"))
    C_HEAD = ("gray35", "gray70")
    C_AXIS_TEXT = ("gray30", "gray72")
    C_PLAYHEAD = ("#404040", "#d8d8d8")

    # =102: 1枚表示(集約)のデバイス色(ライト, ダーク)。全種別を同一平面に
    # 重ねるため、種別ごとに色相を分けて識別できるようにする。
    # =114: **ライト側だけ**さらに濃くした(ユーザー指摘「ライトのグラフの線が
    # 背景に近くて見づらい」)。背景 gray94(#f0f0f0)に対するコントラスト比を
    # 全色 4.5:1 前後へ揃える(旧: 橙3.0 / 黄3.1 / 緑3.6 / 青3.8 と、
    # 基準線(#b4b4b4=1.9)との差が小さく波形が浮き上がっていなかった)。
    # **ダーク側は従来値のまま**。色相の割り当ても不変。
    C_KEY = {
        "linear":       ("#5245c9", "#8f84ff"),   # 紫 6.0:1
        "twist":        ("#0e6fa8", "#4db8ff"),   # 青 4.8:1
        "rotate_ufo":   ("#9c6000", "#ffb340"),   # 橙 4.5:1
        "rotate_ufo_r": ("#6b6f00", "#d8dd45"),   # 黄 4.7:1
        "rotate_a10":   ("#b03a72", "#ff7ab8"),   # 桃 5.0:1
        "rotate_a10_r": ("#9c382a", "#ff8a70"),   # 赤茶 6.1:1
        "vibration":    ("#1a7c43", "#3ddc84"),   # 緑 4.6:1
    }
    # =114: 個別表示の波形色。従来は ACCENT(紫)/OK_COLOR(緑)の
    # **ダーク用の明るい色をライトでもそのまま**使っていたため、緑が
    # 1.6:1(ほぼ背景と同化)・紫が3.5:1しかなかった。ライトだけ濃くする
    # (色は C_KEY の linear/vibration と同値=1枚表示と印象を揃える)。
    C_WAVE_POS = ("#5245c9", _clr.ACCENT)     # linear / twist
    C_WAVE_SPEED = ("#1a7c43", OK_COLOR)  # rotate / vibration

    def __init__(self, master, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, height=1, **kwargs)
        self.level = self.DEFAULT_LEVEL
        self.overlay = False          # =102: True=1枚表示(全種別を重ねる)
        self.minus = False            # =103: True=再生位置より右(将来)を描かない
        self.follow = True            # 再生位置を中央に追従させる
        self.view_ms = 0.0            # 画面中央の時刻(ms)
        self.view_y = 0.0             # 縦の送り量(5本ではみ出したときだけ)
        self._over_y = 0.0            # 縦のはみ出し量(0=全部見えている)
        self.snapshot = {"now_ms": 0.0, "segments": []}
        self._drag = None
        # =237: 右ダブルクリック=再生位置をそこへ(編集グラフ=192の横展開)。
        # 呼び出し側が「グラフの時間軸のms」を受け取るコールバックを差す。
        # None のままなら何も起きない。
        self.on_seek = None
        # =243: 音声波形の帯(レビュー画面だけが設定する。再生タブは None)。
        # wave_env={"bucket_ms","chans","mono","peak"} / wave_mode=
        # "off"/"mono"/"stereo"/"stereo_rev" / wave_offset=グラフ 0ms の
        # 素材時刻(レビューは音声区間の開始)。ステレオは各行を上下半分に
        # 分けて上=L・下=R。**左右2行の組(rotate_ufo と rotate_ufo_r 等)が
        # 両方あるときは、上の行=L・下の行=R を全高で**描く(ユーザー指定。
        # 「(逆)」はどちらも割り当てを入れ替える)。
        self.wave_env = None
        self.wave_mode = "off"
        self.wave_offset = 0.0
        self.configure(bg=self._c(self.C_BG))
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-3>", self._on_double3)

    # ---- 小道具 ----

    @staticmethod
    def _dark() -> bool:
        return ctk.get_appearance_mode() != "Light"

    def _c(self, pair):
        return pair[1] if self._dark() else pair[0]

    @staticmethod
    def fmt_time(sec: float) -> str:
        """時間ラベル「Y分M秒」。"""
        s = int(round(abs(sec)))
        return ("-" if sec < 0 else "") + tr("{0}分{1}秒").format(s // 60, s % 60)

    def span_ms(self) -> float:
        """画面の横幅が表す時間(ms)= 太線間隔×2.5。"""
        return self.LEVELS[self.level][2] * 2.5 * 1000.0

    def visible_keys(self) -> list:
        """表示するグラフの種別(実行中/実行済みの種別のみ)。"""
        present = {s["key"] for s in self.snapshot.get("segments", ())}
        return [k for k in self.ORDER if k in present]

    def key_label(self, key: str, keys) -> str:
        if key == "linear":
            return "LINEAR"
        if key == "twist":
            return "TWIST"
        if key == "vibration":
            return "VIBRATION"
        base = "ROTATE(ufo)" if key.startswith("rotate_ufo") else "ROTATE(a10)"
        if key.endswith("_r"):
            return base + " " + tr("右")
        if key + "_r" in keys:
            return base + " " + tr("左")
        return base

    # ---- 外部API ----

    def set_snapshot(self, snap: dict):
        self.snapshot = snap
        if self.follow:
            self.view_ms = float(snap.get("now_ms", 0.0))
        self._redraw()

    def set_level(self, level: int):
        level = max(0, min(len(self.LEVELS) - 1, int(level)))
        if level != self.level:
            self.level = level
            self._redraw()

    def set_overlay(self, flag: bool):
        """=102: 1枚表示(集約)⇄個別表示を切り替える。"""
        flag = bool(flag)
        if flag != self.overlay:
            self.overlay = flag
            self.view_y = 0.0
            self._redraw()

    # =127: マイナス表示では再生位置(基準線)を右端付近へ寄せる。
    # 右側(将来)を描かないため、中央配置だと右半分が空白になっていた。
    # 1.0=右端ぴったりではなく、少し余裕を残す(ユーザー要望)。
    MINUS_ANCHOR = 0.92

    def set_minus(self, flag: bool):
        """=103: マイナス表示=再生位置より右(将来)の波形を描かない。

        実行済みの軌跡だけがリアルタイムに残り、現在値は縦線上で上下に
        動き続けて見える(=127で縦線は右端付近)。基準線・時間軸・凡例は
        通常どおり描く。
        """
        flag = bool(flag)
        if flag != self.minus:
            self.minus = flag
            self._redraw()

    # ---- 操作 ----

    def _on_wheel(self, event):
        up = getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4
        # 上=拡大(細かい縮尺へ) / 下=縮小
        self.set_level(self.level + (-1 if up else 1))
        return "break"

    def _on_press(self, event):
        self._drag = {"x": event.x, "y": event.y, "view": self.view_ms,
                      "vy": self.view_y, "moved": 0}

    def _on_drag(self, event):
        if self._drag is None:
            return
        dx = event.x - self._drag["x"]
        dy = event.y - self._drag["y"]
        self._drag["moved"] = max(self._drag["moved"], abs(dx), abs(dy))
        if self._drag["moved"] < self.CLICK_PX:
            return
        w = max(1, self.winfo_width() - self.GUTTER - 4)
        self.follow = False
        self.view_ms = self._drag["view"] - dx * (self.span_ms() / w)
        # 縦: グラフが5本ではみ出しているときだけ送れる(はみ出し0なら無効)
        self.view_y = max(0.0, min(self._over_y, self._drag["vy"] - dy))
        self._redraw()

    def _on_release(self, _event):
        if self._drag is not None and self._drag["moved"] < self.CLICK_PX:
            # 単クリック=自動追従へ復帰(縦の送りも先頭へ戻す)
            self.follow = True
            self.view_ms = float(self.snapshot.get("now_ms", 0.0))
            self.view_y = 0.0
            self._redraw()
        self._drag = None

    def ms_of(self, x: float) -> float:
        """画面のx座標が指すグラフの時刻(ms)。=237

        描画(`_redraw` の `x_of`)の逆写像。マイナス表示のときの基準位置
        (MINUS_ANCHOR)も含めて同じ式を使う。
        """
        plot_w = max(1.0, float(self.winfo_width() - self.GUTTER - 4))
        span = self.span_ms()
        anchor = self.MINUS_ANCHOR if self.minus else 0.5
        t_left = self.view_ms - span * anchor
        return t_left + (float(x) - self.GUTTER) * (span / plot_w)

    def _on_double3(self, event):
        """=237: 右ダブルクリック=再生位置をそこへ(編集グラフ=192と同じ操作)。

        グラフの時間軸の ms を `on_seek` へ渡すだけで、実際に何を動かすかは
        呼び出し側が決める(レビュー画面=そのまま絶対位置 / 再生タブ=
        現在位置との差分ぶんの相対シーク)。**自動追従の状態は変えない**
        (追従停止中に押しても停止のまま=ユーザー決定)。
        """
        self._drag = None            # パンのドラッグ状態は捨てる
        if callable(self.on_seek):
            self.on_seek(self.ms_of(event.x))
        return "break"

    # ---- 描画 ----

    def _redraw(self):
        self.delete("all")
        self.configure(bg=self._c(self.C_BG))
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 40:
            return

        keys = self.visible_keys()
        rows = keys or [""]          # 未再生でも基準線だけのグラフを1本出す
        visible_h = h - self.AXIS_H
        if self.overlay:
            # =102: 1枚表示=画面全体を1つのグラフが覆う(縦の送りなし)
            row_h = 0
            self._over_y = 0.0
            self.view_y = 0.0
            axis_top = visible_h
        else:
            # 1本あたりの高さは**固定**(本数で変えない=ユーザー決定)。5本に
            # なって入りきらないときだけ、縦のドラッグで送って見る。
            row_h = self.ROW_H
            content_h = row_h * len(rows)
            self._over_y = max(0.0, content_h - visible_h)
            self.view_y = max(0.0, min(self._over_y, self.view_y))
            axis_top = min(content_h - self.view_y, visible_h)
        x0 = self.GUTTER
        plot_w = w - self.GUTTER - 4
        span = self.span_ms()
        # =127: view_ms(追従時=now)を置く位置。通常=中央 / マイナス表示=
        # 右端付近(右の将来側を描かないぶん、過去の軌跡へ幅を使う)
        anchor = self.MINUS_ANCHOR if self.minus else 0.5
        t_left = self.view_ms - span * anchor
        ppm = plot_w / span              # px / ms

        def x_of(t):
            return x0 + (t - t_left) * ppm

        fine, mid, coarse = self.LEVELS[self.level]

        # ---- =243: 音声波形の帯(基準線・波形の背面=最初に描く) ----
        if self.wave_env and self.wave_mode != "off" and not self.overlay:
            self._draw_audio_rows(rows, row_h, t_left, ppm,
                                  axis_top, x0, w)

        # ---- 時間の基準線(細→中→太の順に上書き) ----
        for step_s, color in ((fine, self._c(self.C_GRID_T[0])),
                              (mid, self._c(self.C_GRID_T[1])),
                              (coarse, self._c(self.C_GRID_T[2]))):
            step = step_s * 1000.0
            k = int(t_left // step)
            t = k * step
            while t <= t_left + span + 1:
                x = x_of(t)
                if x0 - 1 <= x <= w:
                    self.create_line(x, 0, x, axis_top, fill=color)
                t += step

        # ---- 各グラフ ----
        if self.overlay:
            self._draw_overlay(keys, x_of, x0, w, plot_w, t_left, span,
                               axis_top)
            rows = []      # 個別行は描かない(以降の共通処理だけ実行)
        for i, key in enumerate(rows):
            top = i * row_h - self.view_y
            p_top = top + self.HEAD_H
            p_bot = top + row_h - self.ROW_GAP
            if p_bot - p_top < 8 or p_bot < 0 or p_top > axis_top:
                continue        # 画面の外(縦の送りで隠れている行)

            def y_of(pos, _t=p_top, _b=p_bot):
                return _b - (max(0.0, min(100.0, pos)) / 100.0) * (_b - _t)

            # 位置の基準線(25/75 は最も薄く、0/50/100 はその次)
            for pos in (25, 75):
                y = y_of(pos)
                self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS_SUB))
            for pos in (0, 50, 100):
                y = y_of(pos)
                self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS))

            # 見出し(種別名)と位置目盛
            if key:
                self.create_text(2, top + self.HEAD_H / 2, anchor="w",
                                 text=self.key_label(key, keys),
                                 fill=self._c(self.C_HEAD),
                                 font=(appfont.FAMILY, 9, "bold"))
            gx = self.GUTTER - 4
            for pos, anc in ((100, "ne"), (50, "e"), (0, "se")):
                self.create_text(gx, y_of(pos), anchor=anc, text=str(pos),
                                 fill=self._c(self.C_AXIS_TEXT), font=(appfont.FAMILY, 8))

            if key:
                color = self._c(self.C_WAVE_POS if key in ("linear", "twist")
                                else self.C_WAVE_SPEED)
                for seg in self.snapshot.get("segments", ()):
                    if seg["key"] != key:
                        continue
                    self._draw_wave(seg, x_of, y_of, x0, w, plot_w,
                                    t_left, span, color)

        # はみ出した行が時間軸へかぶらないように下端を塗り潰す
        if axis_top < h:
            self.create_rectangle(0, axis_top, w, h,
                                  fill=self._c(self.C_BG), outline="")

        # ---- 再生位置(現在のデバイス位置を示す縦線) ----
        now_x = x_of(float(self.snapshot.get("now_ms", 0.0)))
        if x0 <= now_x <= w:
            # =112: 太さ2→1(ユーザー要望「1pxのほうがいい感じ」)。色は不変。
            self.create_line(now_x, 0, now_x, axis_top,
                             fill=self._c(self.C_PLAYHEAD), width=1)

        # ---- 時間軸(太線の位置にラベル) ----
        step = coarse * 1000.0
        t = int(t_left // step) * step
        while t <= t_left + span + 1:
            if t >= 0:
                x = x_of(t)
                if x0 <= x <= w:
                    self.create_text(x, axis_top + 2, anchor="n",
                                     text=self.fmt_time(t / 1000.0),
                                     fill=self._c(self.C_AXIS_TEXT),
                                     font=(appfont.FAMILY, 8))
            t += step

        if not self.follow:
            # =102: 1枚表示では上端が凡例行のため、その下に出す
            fy = self.HEAD_H + 2 if self.overlay else 2
            self.create_text(w - 4, fy, anchor="ne",
                             text=tr("追従停止中（クリックで戻る）"),
                             fill=self._c(WARN_TEXT), font=(appfont.FAMILY, 8, "bold"))

        # グラフが5本で入りきらないときの上下の送り(ドラッグ)の目印
        if self._over_y > 0:
            arrow = ("▲" if self.view_y > 0 else "") + \
                    ("▼" if self.view_y < self._over_y else "")
            if arrow:
                self.create_text(w - 2, axis_top + 2, anchor="ne", text=arrow,
                                 fill=self._c(self.C_HEAD), font=(appfont.FAMILY, 9))

    def _draw_audio_rows(self, rows, row_h, t_left, ppm,
                         axis_top, x0, w):
        """=243: 各行の背景へ音声波形の帯を描く(基準線より先に呼ぶ)。

        ステレオは行を上下半分に分けて上=L・下=R。**左右2行の組**
        (例: rotate_ufo と rotate_ufo_r が両方出ている)は、上の行=L・
        下の行=R を**全高で**描く。「(逆)」はどちらも入れ替え。モノラルは
        合成chを全高で描く。はみ出しは軸下の塗り潰し(既存)が隠す。
        """
        from .. import script_edit as _se     # 遅延import(循環回避)
        env = self.wave_env
        color = self._c(_se.WAVE_BAND_COLOR)
        bucket, peak, chans = env["bucket_ms"], env["peak"], env["chans"]
        mode = self.wave_mode
        rev = mode == "stereo_rev"
        off = float(self.wave_offset)

        def ms_of_px(x):
            return t_left + (x - x0) / ppm + off

        # 左右2行の組(key と key+"_r" が両方出ている)を集める
        pair_l = {k for k in rows if k and not k.endswith("_r")
                  and (k + "_r") in rows}
        pair_r = {k + "_r" for k in pair_l}
        for i, key in enumerate(rows):
            top = i * row_h - self.view_y
            p_top = top + self.HEAD_H
            p_bot = top + row_h - self.ROW_GAP
            if p_bot - p_top < 8 or p_bot < 0 or p_top > axis_top:
                continue
            if mode == "mono" or len(chans) < 2:
                _se.draw_audio_band(self, env["mono"], bucket, peak,
                                    x0, w, ms_of_px, (p_top + p_bot) / 2.0,
                                    (p_bot - p_top) / 2.0, color)
                continue
            a, b = (1, 0) if rev else (0, 1)
            if key in pair_l or key in pair_r:
                # 上の行(…)=L / 下の行(…_r)=R を全高で
                ch = chans[b if key in pair_r else a]
                _se.draw_audio_band(self, ch, bucket, peak, x0, w,
                                    ms_of_px, (p_top + p_bot) / 2.0,
                                    (p_bot - p_top) / 2.0, color)
                continue
            mid = (p_top + p_bot) / 2.0
            half = (p_bot - p_top) / 4.0
            _se.draw_audio_band(self, chans[a], bucket, peak, x0, w,
                                ms_of_px, (p_top + mid) / 2.0, half, color)
            _se.draw_audio_band(self, chans[b], bucket, peak, x0, w,
                                ms_of_px, (mid + p_bot) / 2.0, half, color)

    def _draw_overlay(self, keys, x_of, x0, w, plot_w, t_left, span,
                      axis_top):
        """=102: 1枚表示。全種別の波形を1つの座標平面へ色分けして重ねる。

        位置の基準線・位置目盛は1組だけ描き、上端に色分けの凡例を置く。
        線の色は C_KEY(種別ごとに色相を分ける)。描画そのものは個別表示と
        同じ _draw_wave を共有する(=71のB案=窓で重なりを防ぐ、も共通)。
        """
        p_top = self.HEAD_H + 2
        p_bot = axis_top - 4
        if p_bot - p_top < 8:
            return

        def y_of(pos):
            return p_bot - (max(0.0, min(100.0, pos)) / 100.0) * (p_bot - p_top)

        # 位置の基準線(25/75 は最も薄く、0/50/100 はその次)
        for pos in (25, 75):
            y = y_of(pos)
            self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS_SUB))
        for pos in (0, 50, 100):
            y = y_of(pos)
            self.create_line(x0, y, w, y, fill=self._c(self.C_GRID_POS))
        gx = self.GUTTER - 4
        for pos, anc in ((100, "ne"), (50, "e"), (0, "se")):
            self.create_text(gx, y_of(pos), anchor=anc, text=str(pos),
                             fill=self._c(self.C_AXIS_TEXT), font=(appfont.FAMILY, 8))
        # 凡例(種別名を各色で左上に横並び)
        lx = x0
        for key in keys:
            item = self.create_text(
                lx, self.HEAD_H / 2, anchor="w",
                text=self.key_label(key, keys),
                fill=self._c(self.C_KEY.get(key, self.C_HEAD)),
                font=(appfont.FAMILY, 9, "bold"))
            box = self.bbox(item)
            lx = (box[2] if box else lx + 60) + 12
        # 波形(種別ごとの色で同一平面へ重ねる)
        for key in keys:
            color = self._c(self.C_KEY.get(key, self.C_HEAD))
            for seg in self.snapshot.get("segments", ()):
                if seg["key"] != key:
                    continue
                self._draw_wave(seg, x_of, y_of, x0, w, plot_w,
                                t_left, span, color)

    def _draw_wave(self, seg, x_of, y_of, x0, w, plot_w, t_left, span, color):
        """1つのスクリプト断片を描く(表示範囲だけを切り出して描画)。

        =71: 切り出しは**点の番号ではなく時間(x)で行い、窓の境界をまたぐ線は
        補間して繋ぐ**。以前は番号で挟み込んでいたため、シーク位置が2点の
        あいだに落ちると手前の点ごと捨てられ、次の点までの線が丸ごと消えて
        「波形が欠けた」ように見えていた(CSVのように点がまばらだと顕著)。
        窓は seg["x0"] 〜 seg["x1"](同じ行の次の断片の開始位置)で、
        断片どうしの重なりはこれで防ぐ。
        """
        pts = seg["points"]
        times = seg.get("times") or [p[0] for p in pts]
        t0 = seg["t0"]
        # 描いてよい時間の窓(イベント経過)を表示範囲と突き合わせる
        wl = t_left if seg.get("x0") is None else max(t_left, seg["x0"])
        wr = t_left + span if seg.get("x1") is None \
            else min(t_left + span, seg["x1"])
        if self.minus:
            # =103: マイナス表示=窓の右端を再生位置で打ち切る(将来を描かない)。
            # 窓の境界は=71の補間で繋がるため、波形はちょうど再生位置の
            # 現在値で終わり、リアルタイムに描き足されていくように見える
            wr = min(wr, float(self.snapshot.get("now_ms", 0.0)))
        if wr <= wl:
            return
        lo_t, hi_t = wl - t0, wr - t0          # スクリプト時間へ換算
        # 境界をまたぐ線を残すため前後1点を含めて切り出す
        lo = max(0, bisect.bisect_right(times, lo_t) - 1)
        hi = min(len(pts), bisect.bisect_left(times, hi_t) + 1)
        view = pts[lo:hi]
        if not view:
            return
        step = (seg["kind"] == "step")
        # =104: 前段の粗間引き。可視点が1pxあたり6点を超えるときはストライドで
        # 落としてから処理する(境界補間・エンベロープの入力として1pxに
        # 約6サンプルあれば見た目は変わらない)。これで _clip_view と
        # エンベロープのPythonループが縮尺によらず plot_w*6 前後で頭打ちになり、
        # 60fps(=104)でも最大縮尺で1フレーム数msに収まる。
        cap = max(64, int(plot_w) * 6)
        if len(view) > cap:
            stride = len(view) // cap
            if stride > 1:
                last = view[-1]
                view = view[::stride]
                if view[-1] is not last:
                    view.append(last)   # 右端の点は保持(末尾延長・補間用)
        view = self._clip_view(view, lo_t, hi_t, step)
        if len(view) < 2:
            return

        if len(view) > plot_w * 3:
            # 点が多すぎるときは1px単位の最小/最大の帯(エンベロープ)にする。
            # =104: x_of/y_of(クロージャ)を点ごとに呼ばず、算術をインライン化
            # +バケツは生のposで持ち、y変換はピクセルごとに2回だけにする
            ppm = plot_w / span
            base = x0 + (t0 - t_left) * ppm
            buckets = {}
            bget = buckets.get
            for t, p in view:
                px = int(base + t * ppm)
                b = bget(px)
                if b is None:
                    buckets[px] = [p, p]
                elif p < b[0]:
                    b[0] = p
                elif p > b[1]:
                    b[1] = p
            coords = []
            for px in sorted(buckets):
                pmin, pmax = buckets[px]
                coords += [px, y_of(pmax), px, y_of(pmin)]
            if len(coords) >= 4:
                self.create_line(*coords, fill=color, width=1)
            return

        coords = []
        prev_y = None
        for t, p in view:
            x = x_of(t0 + t)
            y = y_of(p)
            if step and prev_y is not None:
                coords += [x, prev_y]     # 次の指示までは同じ強さ(直角の線)
            coords += [x, y]
            prev_y = y
        if len(coords) >= 4:
            self.create_line(*coords, fill=color, width=2,
                             capstyle="round", joinstyle="round")

    @staticmethod
    def _clip_view(view, lo_t, hi_t, step):
        """点列を [lo_t, hi_t] の窓へ切り詰める(境界の値は補間で作る)。

        step(rotate/vibration)は「次の指示まで同じ値」なので手前の点の値を
        そのまま境界へ持ってくる。linear は2点を結ぶ直線上の値を求める。
        """
        out = []
        prev = None
        for t, p in view:
            if t < lo_t:
                prev = (t, p)
                continue
            if prev is not None:            # 窓の左端を作る
                pt, pp = prev
                if step or t == pt:
                    out.append((lo_t, pp))
                else:
                    r = (lo_t - pt) / (t - pt)
                    out.append((lo_t, pp + (p - pp) * r))
                prev = None
            if t > hi_t:                    # 窓の右端を作って終わり
                if out:
                    lt, lp = out[-1]
                    if step or t == lt:
                        out.append((hi_t, lp))
                    else:
                        r = (hi_t - lt) / (t - lt)
                        out.append((hi_t, lp + (p - lp) * r))
                return out
            out.append((t, p))
        if out and out[-1][0] < hi_t and step:
            # 最後の指示は次が来るまで続く(実行中の断片の右端まで伸ばす)
            out.append((hi_t, out[-1][1]))
        return out


apptheme.register(globals())   # テーマ追従する色定数・クラス属性を登録(apptheme)
