"""編集グラフ本体(ScriptEditGraph: 生成・表示状態・座標変換・ミラー同期)。"""
from __future__ import annotations

import tkinter as tk

from .common import EMPTY_VIEW_MS, GRID_AT_DEFAULT, GRID_POS_DEFAULT
from .graph_draw import _ScriptEditGraphDrawMixin
from .graph_input import _ScriptEditGraphInputMixin
from .graph_plan import _ScriptEditGraphPlanMixin
from .model import ScriptEditModel


class ScriptEditGraph(_ScriptEditGraphPlanMixin, _ScriptEditGraphInputMixin, _ScriptEditGraphDrawMixin, tk.Canvas):
    """funscript 編集用のグラフ(1トラックだけを大きく表示)。

    操作の割り当て(仕様 4.3):
      左クリック(空欄)=打点 / 左クリック(点)=単独選択 /
      Ctrl+左クリック=選択に追加・解除 / 左ドラッグ(点)=移動 /
      左ドラッグ(空欄)=矩形選択 / 右ドラッグ=左右パン /
      右クリック(静止)=メニュー / ホイール=時間の拡大縮小 /
      Delete=削除 / Ctrl+C/X/V=コピー/切取/貼付 / Ctrl+Z/Y=UNDO/REDO

    縮尺段階(LEVELS)・クリック判定(CLICK_PX)・色は main.DeviceGraph から
    借りる(遅延import。tk.PhotoImage 等のモジュールレベルキャッシュは
    持たない=2つ目の Tk() を作るテストで TclError になるため)。
    """

    GUTTER = 30

    AXIS_H = 30   # =195: ヒートマップの帯(pos=0の下)+時間ラベルのぶん

                  # (=247: 帯を点と重ならない位置へ下げたぶん+4px)
    TOP_PAD = 10

    SCALE_H = 20  # =210: 縮尺表示の帯(左上・pos「100」ラベルの上)

    HIT_PX = 6            # 点のヒット判定の半径(px)

    # view_ms(追従時=再生位置)を置く水平位置(=176)。0.5=中央 →
    # 0.12=左寄り(初期表示で0秒が左端近くに来る。再生タブのマイナス表示の
    # アンカーと同じ考え方で、左に少し余裕を残す)
    VIEW_ANCHOR = 0.12

    # =184: パターンの線と構成点の色(青の波形と見分けやすい緑。
    # ライト/ダーク)。ゴーストの緑破線とは線種(実線/破線)で区別する
    PATTERN_GREEN = ("#149a43", "#3ddc84")

    # =195: ヒートマップ(速い順)。しきい値は speed=|Δpos|÷Δt(ms)×60。
    # =247: 紫は「警告色として目立たない」ため廃止し、色を1段ずつスライド。
    # また青より弱く感じる水色を最弱側へ移した(いずれもユーザー指定)。
    # (下限(含む), 色)。24以上=赤(最高段階) / 20-24=オレンジ /
    # 16-20=黄 / 12-16=緑 / 9-12=青 / 6-9=水色 / 0超-6=水色。
    # 速度0は無色(=None)。TFGのしきい値(▲危険=20/△注意=16)は不変。
    HEAT_LEVELS = (
        (24.0, ("#df1b1b", "#ff5b5b")),     # 赤
        (20.0, ("#e87c00", "#ffa040")),     # オレンジ
        (16.0, ("#cfae00", "#e6d34a")),     # 黄
        (12.0, ("#1c9e4d", "#3ddc84")),     # 緑
        (9.0, ("#2b62d9", "#6ba3ff")),      # 青
        (6.0, ("#0999b8", "#3fd2ee")),      # 水色
        (0.0, ("#0999b8", "#3fd2ee")),      # 水色(0超〜6未満)
    )

    def __init__(self, master, model: ScriptEditModel,
                 on_change=None, on_select=None, on_menu=None, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, **kwargs)
        from ..main import DeviceGraph as _DG    # 遅延import(循環回避)
        self._DG = _DG
        self.LEVELS = _DG.LEVELS
        self.CLICK_PX = _DG.CLICK_PX
        self.model = model
        self.on_change = on_change      # 編集が確定した(dirty・全長の更新)
        self.on_select = on_select      # 選択が変わった((at,pos)欄の更新)
        self.on_menu = on_menu          # 右クリック(静止)= メニューを出す
        self.on_paste_reject = None     # Ctrl+V が重なりで拒否された(=171)
        self.on_seek = None             # 右ダブルクリック=再生位置(=192)
        self.place_scale = 1.0          # =194: 配置時の縮尺(x2〜x0.5)

        self.level = _DG.DEFAULT_LEVEL
        self.follow = True              # 再生位置へ追従(パンで解除)
        self.playing = False            # =198: 再生中は追従アンカー=中央
        self._played = False            # =201: 一度再生したら停止中も中央
        self.plain = False              # =206: 再生位置の線・追従バッジを
        #                                 描かない(ユーザーパターン編集用)
        self.view_ms = 0.0              # 画面中央の時刻(ms)
        self.now_ms = 0.0               # 再生位置
        self.grid_at = GRID_AT_DEFAULT  # property(=183: edge_tol を同期)
        self.grid_pos = GRID_POS_DEFAULT
        self.tool = "point"             # "point" / "pattern"(P2 =173)
        # P2(=173): パターン配置ツールの状態
        self.place_shape = None         # 配置する正規化点列(インバート適用後)
        self.place_name = None          # パターン名(i18nキー)
        self.sel_pattern = None         # 選択中の配置済みパターンの index
        self._ghost = None              # 配置ゴーストの plan(仕様 4-2)
        self._point_ghost = None        # 点モードのゴースト (at, pos)(=176)
        # =233: マウス位置の十字ガイド (at, pos|None)。pos=None は
        # 「縦線だけ」= 仲間のグラフ(サブ・左右のもう片方)へ映したもの。
        self.cross = None
        # =318: 時間ラベルを hh:mm:ss.fff にするか(素材が 1 時間以上のとき
        # だけ。普段は mm:ss.fff)。initial_view(duration) で決まる。
        self.time_hours = False
        # =243: 音声波形(背景の帯)。wave_env は編集画面が渡す
        # {"bucket_ms", "chans"(ch別ピーク列), "mono"(合成), "peak"}。
        # wave_mode: "off" / "mono" / "stereo" / "stereo_rev"。
        # wave_channel: None=1本のグラフ(ステレオは上下半分に分割) /
        #   0 or 1=UFOTW の左右2本(そのchを全高で描く。rev で入れ替え)。
        # wave_offset: グラフの 0ms が指す素材時刻(編集モードは常に 0)。
        self.wave_env = None
        self.wave_mode = "off"
        self.wave_channel = None
        self.wave_offset = 0.0
        # =233: 縦軸の表記。"speed" は csv の回転速度(下から -100 / 0 / 100)
        self.pos_axis = "pos"
        self.on_placed = None           # パターン配置成功の通知(=179)
        # 区間の目印(仕様 4.2)。編集モードは区間を適用しないが、指定が
        # あるアイテムでは開始・終了の位置へ縦の破線を描く。
        self.region = (0.0, None, False)     # (lo_ms, hi_ms|None, 指定あり)

        # =224: csv(ROTATE)の編集モード。
        #  step  : 階段で描く(次の点まで同じ値・最初の点の前は停止=50)
        #  heat  : ヒートマップ(linear のストローク速度の警告)を出すか
        #  round_interior: パターン内側の点もグリッドへ丸める
        #          (csv は時刻が 100ms 単位なので、内側も格子へ乗せる)
        self.step = False
        self.heat = True
        self.round_interior = False
        # =226: 離散的なスクリプト(csv・rotate系・vibration)のパターン規則。
        #  pat_center : None=従来(クリックした高さへ縦にずらせる) /
        #               50=ROTATE(停止が中心) / 0=VIBRATION(停止が下端)。
        #               None でないときは **定義どおりの高さで固定**して置き、
        #               縦の拡縮は中心を軸に対称、縦移動は禁止。
        #  pat_follow : 隣接パターンの追従(=175/=182)。離散では False。
        #  pat_connect: 接続配置(端点の共有)。離散では False。
        self.pat_center = None
        self.pat_follow = True
        self.pat_connect = True
        # =227: サブ表示(参考用の別トラック)。
        #  readonly : 入力を一切受けない(見るだけ)
        #  sub_bg   : 背景を分ける(ライト/カラーテーマ=灰色 / ダーク=明るめ)
        #  mirror   : 自分の表示範囲・再生位置を映す相手(サブ側のグラフ)
        self.readonly = False
        self.sub_bg = False
        self.mirror = None

        self._drag = None
        self.configure(bg=self.bg_color())
        self.bind("<Configure>", self._on_configure)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        # =229: _plot() の1フレームキャッシュ(<Configure> と redraw で捨てる)
        self._plot_cache = None
        # =232: 5列csv の左右同時編集。表示(縮尺・位置・再生位置)を揃える
        #  仲間のグラフ / 「いま触っている方」の枠の強調 / 右上の見出し。
        self.peers = []
        self._syncing = False
        self.active = True          # 単独のときは常にアクティブ
        self.peer_mode = False      # 2本以上を同時に編集しているか
        self.active_color = "#1f6aa5"
        self.on_activate = None     # クリックで「アクティブになった」通知
        self.corner_text = ""       # 右上の見出し(「左（ロータ1）」など)
        # =298(要望4): 周辺表示の出し分け。5列csv の左右2本を縦に詰めるため、
        # 縮尺表示・「追従停止中」は上のグラフだけ、時間ラベルは下のグラフ
        # だけに出す。サブ表示(=227)は全部出さない。False にしたぶんの
        # 余白(SCALE_H / AXIS_H)は _plot が詰める。
        self.show_scale = True
        self.show_time = True
        self.show_follow_hint = True
        # =227: グラフ上のマウス位置(停止中の F キー配置に使う)
        self._mouse_xy = None
        self.bind("<ButtonPress-1>", self._on_press1)
        self.bind("<B1-Motion>", self._on_drag1)
        self.bind("<ButtonRelease-1>", self._on_release1)
        self.bind("<ButtonPress-3>", self._on_press3)
        self.bind("<B3-Motion>", self._on_drag3)
        self.bind("<ButtonRelease-3>", self._on_release3)
        self.bind("<Double-Button-3>", self._on_double3)
        self.bind("<Delete>", self._on_delete_key)
        for seq, fn in (("<Control-c>", self._key_copy),
                        ("<Control-x>", self._key_cut),
                        ("<Control-v>", self._key_paste),
                        ("<Control-z>", self._key_undo),
                        ("<Control-y>", self._key_redo)):
            self.bind(seq, fn)
            self.bind(seq.replace(seq[-2], seq[-2].upper()), fn)
        # =216: 矢印キー(テンキーの矢印も)で選択中の点・パターンを移動。
        # 単押し=1グリッド / 0.5秒以上の長押し=0.1秒ごとに連続移動
        self._nudge_held = None         # 押しっぱなし中の (dat_step, dpos_step)
        self._nudge_job = None          # 連続移動の after id
        self._nudge_release_job = None  # KeyRelease の確定待ち(自動リピート対策)
        for names, step in ((("Up", "KP_Up"), (0, 1)),
                            (("Down", "KP_Down"), (0, -1)),
                            (("Left", "KP_Left"), (-1, 0)),
                            (("Right", "KP_Right"), (1, 0))):
            for nm in names:
                self.bind("<KeyPress-" + nm + ">",
                          lambda _e, st=step: self._on_nudge_press(st))
                self.bind("<KeyRelease-" + nm + ">",
                          lambda _e, st=step: self._on_nudge_release(st))

    NUDGE_HOLD_MS = 500       # 長押しと見なすまで

    NUDGE_REPEAT_MS = 100     # 連続移動の間隔

    @property
    def pos_max(self) -> int:
        return int(getattr(self.model, "pos_max", 100) or 100)

    @property
    def pos_center(self) -> float:
        return self.pos_max / 2.0

    @property
    def grid_at(self) -> int:
        return self._grid_at

    @grid_at.setter
    def grid_at(self, value: int) -> None:
        self._grid_at = value
        # 端点の禁止帯=atグリッド半分・最低1ms(=183)。テストが
        # graph.grid_at へ直接代入しても同期されるよう property にする
        if getattr(self, "model", None) is not None:
            self.model.edge_tol = max(1.0, value * 0.5)

    def set_active(self, flag: bool, peer_mode: bool | None = None):
        """=232: 「いま触っているグラフ」の枠を強調する。

        peer_mode=False(単独)では枠を出さない。
        """
        self.active = bool(flag)
        if peer_mode is not None:
            self.peer_mode = bool(peer_mode)
        try:
            if not self.peer_mode:
                self.configure(highlightthickness=0)
            else:
                col = self.active_color if self.active else self.bg_color()
                self.configure(highlightthickness=2,
                               highlightbackground=col, highlightcolor=col)
        except Exception:
            pass

    def _guard(self, _event=None):
        """=227: サブ表示(readonly)は入力を受けない。"""
        return "break"

    def make_readonly(self, main=None):
        """=227: このグラフを**サブ表示(見るだけ)**にする。

        入力のバインドを外し、地の色を変える。`main` を渡すと、
        ホイールだけは本体へ回して**サブの上でも拡大縮小できる**ようにする
        (表示範囲は本体から映されるので、サブ側では動かさない)。
        """
        self.readonly = True
        self.sub_bg = True
        self.plain = False
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>",
                    "<ButtonPress-3>", "<B3-Motion>", "<ButtonRelease-3>",
                    "<Double-Button-3>", "<Delete>", "<Motion>", "<Leave>",
                    "<MouseWheel>", "<Button-4>", "<Button-5>",
                    "<Control-c>", "<Control-x>", "<Control-v>",
                    "<Control-z>", "<Control-y>",
                    "<Control-C>", "<Control-X>", "<Control-V>",
                    "<Control-Z>", "<Control-Y>"):
            try:
                self.unbind(seq)
            except Exception:
                pass
        for names in (("Up", "KP_Up"), ("Down", "KP_Down"),
                      ("Left", "KP_Left"), ("Right", "KP_Right")):
            for nm in names:
                for pre in ("<KeyPress-", "<KeyRelease-"):
                    try:
                        self.unbind(pre + nm + ">")
                    except Exception:
                        pass
        if main is not None:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.bind(seq, main._on_wheel)
        self.configure(bg=self.bg_color())

    def _sync_mirror(self):
        """=227/=232: 仲間のグラフへ**表示範囲と再生位置だけ**を映す。

        サブ表示(=227)は見るだけなので、時間軸(縮尺・位置)と再生位置・
        区間の目印を本体と揃えて描き直す(モデルと種別ごとの作法は相手の
        まま)。**=232: 5列csv の左右2本も同じ仕組みで揃える**
        (`peers`)。相手の redraw から戻ってくる無限再帰は
        `_syncing` で止める。
        """
        if self._syncing:
            return
        subs = [g for g in (list(self.peers) + [self.mirror])
                if g is not None and g is not self]
        if not subs:
            return
        self._syncing = True
        try:
            for sub in subs:
                try:
                    if not sub.winfo_exists() or not sub.winfo_ismapped():
                        continue
                except Exception:
                    continue
                changed = (sub.level != self.level
                           or sub.view_ms != self.view_ms
                           or sub.now_ms != self.now_ms
                           or sub.playing != self.playing
                           or sub.follow != self.follow
                           or sub.region != self.region)
                sub.level = self.level
                sub.view_ms = self.view_ms
                sub.now_ms = self.now_ms
                sub.playing = self.playing
                sub._played = self._played
                sub.follow = self.follow
                sub.region = self.region
                if changed:
                    sub._syncing = True
                    try:
                        sub.redraw()
                    finally:
                        sub._syncing = False
        finally:
            self._syncing = False

    @staticmethod
    def _dark() -> bool:
        import customtkinter as ctk
        return ctk.get_appearance_mode() != "Light"

    def _c(self, pair):
        return pair[1] if self._dark() else pair[0]

    def bg_color(self):
        """キャンバスの地の色。=227: サブ表示は地の色を変えて区別する
        (ライト/カラーテーマ=灰色 / ダーク=通常より明るめ)。"""
        if self.sub_bg:
            return self._c(("#d9d9d9", "#4a4a4a"))
        return self._c(self._DG.C_BG)

    def span_ms(self) -> float:
        return self.LEVELS[self.level][2] * 2.5 * 1000.0

    def set_level(self, level: int):
        level = max(0, min(len(self.LEVELS) - 1, int(level)))
        if level != self.level:
            self.level = level
            self.redraw()

    def set_now(self, ms: float):
        self.now_ms = float(ms)
        if self.follow:
            self.view_ms = self.now_ms
        self.redraw()

    def initial_view(self, duration_ms: int):
        """全長に応じた初期表示。空(全長0)は表示幅30秒相当の縮尺にする。
        =201: 初期表示は左寄りアンカーへ戻す(_played リセット)。"""
        self._played = bool(self.playing)
        self.time_hours = int(duration_ms) >= 3600_000       # =318
        if duration_ms <= 0:
            for i, lv in enumerate(self.LEVELS):
                if lv[2] * 2.5 * 1000.0 >= EMPTY_VIEW_MS:
                    self.level = i
                    break
        else:
            self.level = self._DG.DEFAULT_LEVEL
        self.follow = True
        self.view_ms = self.now_ms
        self.redraw()

    def x_of(self, ms: float) -> float:
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        left = self.view_ms - span * self._anchor()
        return x0 + (ms - left) * ((x1 - x0) / span)

    def ms_of(self, x: float) -> float:
        x0, _t, x1, _b = self._plot()
        span = self.span_ms()
        left = self.view_ms - span * self._anchor()
        return left + (x - x0) * (span / (x1 - x0))

    def y_of(self, pos: float) -> float:
        _x0, top, _x1, bot = self._plot()
        pm = float(self.pos_max)
        return bot - (max(0.0, min(pm, pos)) / pm) * (bot - top)

    def pos_of_y(self, y: float) -> float:
        _x0, top, _x1, bot = self._plot()
        if bot <= top:
            return 0.0
        pm = float(self.pos_max)
        return max(0.0, min(pm, (bot - y) / (bot - top) * pm))

    def _out_of_range(self, x: float, y: float) -> bool:
        """=279: 打点できない場所(at<0 / pos<0 / pos>100)か。

        グリッド枠の外(上下の余白・0:00 より左)へのクリックは打点せず、
        **選択解除**として扱う(実機FB1)。
        """
        _x0, top, _x1, bot = self._plot()
        if bot <= top:
            return True
        # 枠線上のクリックは打点扱い(1px の許容)
        return (y < top - 1 or y > bot + 1 or x < self.x_of(0) - 1)

    def _min_view_ms(self) -> float:
        """=279: 手動スクロール(パン/ホイール)で許す view_ms の下限。

        左端が **0:00 より初期表示ぶん(VIEW_ANCHOR)** より左へ行かないよう
        にする(マイナス時間を表示する必要はない=実機FB2)。再生追従は
        対象外(再生位置を中央に置く動きは従来どおり)。
        """
        span = self.span_ms()
        return span * self._anchor() - span * self.VIEW_ANCHOR

    def _clamp_view(self) -> None:
        self.view_ms = max(self._min_view_ms(), self.view_ms)

    def hit_point(self, x: float, y: float):
        """(x,y) に最も近い点(HIT_PX 以内)の at を返す。無ければ None。"""
        best, best_d = None, None
        for at, pos in self.model.points:
            dx = self.x_of(at) - x
            dy = self.y_of(pos) - y
            d = (dx * dx + dy * dy) ** 0.5
            if d <= self.HIT_PX and (best_d is None or d < best_d):
                best, best_d = at, d
        return best
