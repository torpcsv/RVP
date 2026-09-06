"""シナリオのイベント図/ステート図の共通描画モジュール。

編集画面(editor.py)と再生タブの「イベント状態」ビュー(main.py)が同じ
見た目の図を共有するための、レイアウト計算と tk.Canvas 描画の置き場。

- layout_tree(data): イベントを左→右の木として配置(編集画面と同一規則)
- draw_event_map(canvas, data, ...): イベント図を描画
- draw_state_map(canvas, ev, ...): ステートマシン図を描画

editor.py はここへ委譲する薄いラッパを持つ(既存の見た目・挙動を維持)。
再生タブは on_click=None(表示専用)+ current(黄緑の現在ノード)+
trail(辿った遷移=緑線)を付けて呼ぶ。
"""

import customtkinter as ctk

from .i18n import tr
from . import appfont

# 図の配色(編集画面と共通)。editor.py はここから import する。
OK_COLOR = "#3ddc84"
CANVAS_BG = "#1f1f1f"        # イベント図/ステート図の背景(ダーク時)
CANVAS_BG_LIGHT = "gray92"   # 同上(ライト時=明るい背景に溶け込ませる)
NODE_FILL = "#3a3a3a"
NODE_STATES = "#54413f"      # ステートを含むイベントの色
EDGE_COLOR = "#5a5a5a"
STATE_EDGE_COLOR = "#8a8a8a"
# 再生タブ「イベント状態」ビュー用
# =124: 現在実行中/選択中の「塗り」は廃止し、緑のコーナー枠(┏┓┗┛)に
# 統一した。CURRENT_FILL はノードの塗りには使わないが、ページランプ
# (main.PAGE_LAMP_ON)が同色を参照しているため定数としては残す。
CURRENT_FILL = "#9ccc3c"     # (旧)現在実行中の黄緑。ランプ色の由来として残置
CURRENT_TEXT = "#1f1f1f"     # (旧)黄緑ノード上の文字
TRAIL_COLOR = "#3ddc84"      # 辿った遷移の線(緑)
TRAIL_WIDTH = 3
VIDEO_LABEL_COLOR = "#6aa9dc"   # 動画つきノードの「▶動画」ラベル(水色)

# =112: ライトモードの図の配色(ユーザー要望「薄くて見づらい」)。
# ダーク時は「暗い背景に明るいノード」だったので、そのままライトへ
# 持ち込むと灰色の丸に白文字=背景に溶けて読めなかった。ライト時は
# **枠=黒 / 背景=薄色 / 文字=黒** に反転させる。ステート形式のイベントは
# 茶褐色系なので「うすい茶色」にする。選択中(青地に白文字)と
# 現在実行中(黄緑地に黒文字)は**両モードで同じ**。
NODE_FILL_LIGHT = "#f2f2f2"        # 通常イベント/ステートの丸(ライト)
NODE_STATES_LIGHT = "#efdfd9"      # ステートを含むイベント(うすい茶)
NODE_OUTLINE = "#777777"           # 丸の枠(ダーク)
NODE_OUTLINE_LIGHT = "#000000"     # 丸の枠(ライト)=黒
NODE_TEXT = "white"                # 丸の中の文字(ダーク)
NODE_TEXT_LIGHT = "#000000"        # 丸の中の文字(ライト)=黒
SELECTED_TEXT = "white"            # (旧)選択中の丸の文字。=124で塗り廃止・残置
STATES_LABEL = "#b09a97"           # 「Nステート」の添え字(ダーク)
STATES_LABEL_LIGHT = "#6b4f4a"     # 同(ライト)
VIDEO_LABEL_COLOR_LIGHT = "#1f6ba8"
EDGE_COLOR_LIGHT = "#8a8a8a"
STATE_EDGE_COLOR_LIGHT = "#6e6e6e"

# =124: 選択中/実行中のコーナー枠(┏┓┗┛)。線なので =114 の方針どおり
# ライトでは濃い緑にしてコントラストを確保する。
SEL_MARK = "#3ddc84"
SEL_MARK_LIGHT = "#0f8f4a"

# =134: パステルカラーテーマ時は図の背景(ライト側)をごく薄い同系色にする
from . import apptheme
apptheme.register(globals())
# =124: 選択肢/数値入力ノードの添え字(琥珀系。面でなく文字なので
# ライト側は濃色)
CHOICE_LABEL = "#d0a848"
CHOICE_LABEL_LIGHT = "#7a5d12"
# =124: すごろく通過の明度アップ(白との混合率)/実行済み減光(背景との混合率)
GLOW_MIX = 0.45
DIM_MIX = 0.55


def is_light() -> bool:
    return ctk.get_appearance_mode() == "Light"


def node_fill(has_states: bool = False) -> str:
    """通常状態の丸の塗り(テーマに追従)。"""
    if is_light():
        return NODE_STATES_LIGHT if has_states else NODE_FILL_LIGHT
    return NODE_STATES if has_states else NODE_FILL


def node_outline(is_start: bool = False) -> str:
    """丸の枠線色。開始ノードだけは両モードとも緑で目立たせる。"""
    if is_start:
        return OK_COLOR
    return NODE_OUTLINE_LIGHT if is_light() else NODE_OUTLINE


def node_text() -> str:
    """丸の中の文字色(通常状態)。"""
    return NODE_TEXT_LIGHT if is_light() else NODE_TEXT


def sel_mark_color() -> str:
    """=124: 選択中/実行中コーナー枠の色。"""
    return SEL_MARK_LIGHT if is_light() else SEL_MARK


def choice_label_color() -> str:
    """=124: 「選択肢」「数値入力」添え字の色。"""
    return CHOICE_LABEL_LIGHT if is_light() else CHOICE_LABEL


def parse_color(v) -> str | None:
    """=124: ノード色 "#RRGGBB" を検証して小文字正規化。不正は None。"""
    if not isinstance(v, str) or len(v) != 7 or not v.startswith("#"):
        return None
    try:
        int(v[1:], 16)
    except ValueError:
        return None
    return v.lower()


def _rgb(canvas, color) -> tuple[int, int, int]:
    """Tk色名("white"/"gray92"/"#rrggbb")を0-255のRGBへ。"""
    r16, g16, b16 = canvas.winfo_rgb(color)
    return (r16 // 256, g16 // 256, b16 // 256)


def mix_color(canvas, c1, c2, t: float) -> str:
    """=124: c1 を c2 側へ割合 t だけ寄せた色を返す(0=無変化, 1=c2)。"""
    r1, g1, b1 = _rgb(canvas, c1)
    r2, g2, b2 = _rgb(canvas, c2)
    return "#%02x%02x%02x" % (
        round(r1 + (r2 - r1) * t),
        round(g1 + (g2 - g1) * t),
        round(b1 + (b2 - b1) * t))


def text_for_fill(canvas, fill) -> str:
    """=124: 塗り色の明るさから文字色(黒系/白系)を自動判定する。"""
    r, g, b = _rgb(canvas, fill)
    bright = 0.299 * r + 0.587 * g + 0.114 * b
    return "#111111" if bright >= 150 else "#ffffff"


def _corner_marks(c, nx, ny, r, tag):
    """=124: 選択中/実行中ノードの四隅コーナー枠(┏┓┗┛)を描く。"""
    pad, arm = 5, 10
    x0, y0 = nx - r - pad, ny - r - pad
    x1, y1 = nx + r + pad, ny + r + pad
    col = sel_mark_color()
    for (cx, cy, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                             (x0, y1, 1, -1), (x1, y1, -1, -1)):
        c.create_line(cx, cy + dy * arm, cx, cy, cx + dx * arm, cy,
                      fill=col, width=2, tags=tag)


def _node_colors(canvas, raw, has_states, dimmed, glowing):
    """=124: ノードの (塗り, 文字色) を決める。

    優先順: カスタム色(raw["color"]) → 既定色。その上に
    減光(実行済み・背景側へ寄せる) / 明度アップ(すごろく通過・白へ寄せる)
    を掛ける。文字色はカスタム/明度アップ時は塗りの明るさから自動判定。
    """
    custom = parse_color(raw.get("color")) if isinstance(raw, dict) else None
    if custom:
        fill, txt = custom, text_for_fill(canvas, custom)
    else:
        fill, txt = node_fill(has_states), node_text()
    if dimmed:
        bg = canvas_bg()
        fill = mix_color(canvas, fill, bg, DIM_MIX)
        txt = mix_color(canvas, txt, bg, 0.5)
    elif glowing:
        fill = mix_color(canvas, fill, "#ffffff", GLOW_MIX)
        txt = text_for_fill(canvas, fill)
    return fill, txt


def states_label_color() -> str:
    return STATES_LABEL_LIGHT if is_light() else STATES_LABEL


def video_label_color() -> str:
    return VIDEO_LABEL_COLOR_LIGHT if is_light() else VIDEO_LABEL_COLOR


def edge_color(state: bool = False) -> str:
    if is_light():
        return STATE_EDGE_COLOR_LIGHT if state else EDGE_COLOR_LIGHT
    return STATE_EDGE_COLOR if state else EDGE_COLOR


def video_items(channels) -> list:
    """チャンネルraw(dict)の動画アイテムを列挙する(=52)。

    動画は =52 でチャンネルのアイテムになったため、図の判定も
    「channels の items に video を持つものがあるか」で行う。
    """
    out = []
    for ch in (channels or {}).values():
        if not isinstance(ch, dict):
            continue
        for it in ch.get("items") or []:
            if isinstance(it, dict) and it.get("video") is not None:
                out.append(it["video"])
    return out


def video_label(raw_videos) -> str:
    """動画ノードのラベル。動画が1本で区間指定(=51)があれば秒数を添える。

    1本の長尺動画を区間でステートへ切り分ける使い方では、図の上で
    どのステートがどの区間かを見分けられると分かりやすい。複数本
    (=52のプレイリスト/ランダム抽選)のときは本数を出す。
    """
    base = tr("▶動画")
    if isinstance(raw_videos, list):
        if len(raw_videos) > 1:
            return tr("{0} {1}本").format(base, len(raw_videos))
        raw_video = raw_videos[0] if raw_videos else None
    else:
        raw_video = raw_videos
    if not isinstance(raw_video, dict):
        return base

    def sec(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return f"{float(v):g}"

    start, end = sec(raw_video.get("start")), sec(raw_video.get("end"))
    if start is None and end is None:
        return base
    if end is None:
        return f"{base} {start}s〜"
    if start is None or start == "0":
        return f"{base} 〜{end}s"
    return f"{base} {start}〜{end}s"


def canvas_bg():
    """イベント図/ステート図キャンバスの背景色を現在のテーマから返す。"""
    return CANVAS_BG_LIGHT if ctk.get_appearance_mode() == "Light" \
        else CANVAS_BG


def next_primary(ev) -> str | None:
    """レイアウト用の代表遷移先(文字列next、または分岐の先頭候補)。"""
    nxt = ev.get("next")
    if isinstance(nxt, str):
        return nxt
    if isinstance(nxt, dict):
        lst = nxt.get("random") or []
        if lst:
            ent = lst[0]
            return ent if isinstance(ent, str) else ent.get("to")
        ch = nxt.get("choice") or []
        if ch:
            return ch[0].get("to")
        rows = nxt.get("cond") or []
        if rows and isinstance(rows[0], dict) and rows[0].get("to"):
            return rows[0]["to"]
        if isinstance(nxt.get("else"), str):
            return nxt["else"]
        iraw = nxt.get("input")
        if isinstance(iraw, dict) and iraw.get("to"):
            return iraw["to"]
    return None


def next_targets(ev) -> list[str]:
    """矢印描画用の全遷移先(重複除去、when_exhaustedのtoも含む)。"""
    nxt = ev.get("next")
    out = []
    if isinstance(nxt, str):
        out.append(nxt)
    elif isinstance(nxt, dict):
        for ent in nxt.get("random") or []:
            to = ent if isinstance(ent, str) else ent.get("to")
            if to:
                out.append(to)
        for ent in nxt.get("choice") or []:
            if isinstance(ent, dict) and ent.get("to"):
                out.append(ent["to"])
        ex = nxt.get("when_exhausted")
        if isinstance(ex, dict) and ex.get("to"):
            out.append(ex["to"])
        traw = nxt.get("timeout")
        if isinstance(traw, dict) and traw.get("to"):
            out.append(traw["to"])
        draw = nxt.get("default")
        if isinstance(draw, dict) and draw.get("to"):
            out.append(draw["to"])
        for row in nxt.get("cond") or []:
            if isinstance(row, dict) and row.get("to"):
                out.append(row["to"])
        if isinstance(nxt.get("else"), str):
            out.append(nxt["else"])
        iraw = nxt.get("input")
        if isinstance(iraw, dict) and iraw.get("to"):
            out.append(iraw["to"])
    # =275: ステート移行の選択肢の「イベント宛て」行き先もイベント図の矢印に含める
    out += state_choice_event_targets(ev)
    return list(dict.fromkeys(out))


def state_choice_event_targets(ev) -> list[str]:
    """=275: ステート移行の選択肢(when.type==choice)のイベント宛て行き先を
    列挙する(各選択肢の {"event": ID} と default.to の {"event": ID})。"""
    out: list[str] = []
    states = ev.get("states") if isinstance(ev, dict) else None
    if not isinstance(states, dict):
        return out
    for st in states.values():
        t = st.get("transition") if isinstance(st, dict) else None
        if not isinstance(t, dict) or not isinstance(t.get("when"), dict) \
                or t["when"].get("type") != "choice":
            continue
        for ent in t.get("choice") or []:
            to = ent.get("to") if isinstance(ent, dict) else None
            if isinstance(to, dict) and isinstance(to.get("event"), str) \
                    and to["event"] not in out:
                out.append(to["event"])
        draw = t.get("default")
        if isinstance(draw, dict) and isinstance(draw.get("to"), dict) \
                and isinstance(draw["to"].get("event"), str) \
                and draw["to"]["event"] not in out:
            out.append(draw["to"]["event"])
    return out


def has_state_choice(ev) -> bool:
    """=275: いずれかのステートが「選択肢でステート移行」を持つか(raw判定)。"""
    states = ev.get("states") if isinstance(ev, dict) else None
    if not isinstance(states, dict):
        return False
    for st in states.values():
        t = st.get("transition") if isinstance(st, dict) else None
        if isinstance(t, dict) and isinstance(t.get("when"), dict) \
                and t["when"].get("type") == "choice":
            return True
    return False


def chain_order(data) -> tuple[list[str], list[str]]:
    """startからnextを辿った順序と、到達できないイベントを返す。"""
    events = data["events"]
    chain, seen = [], set()
    cur = data.get("start")
    while cur in events and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = next_primary(events[cur])
    rest = [e for e in events if e not in seen]
    return chain, rest


def layout_tree(data) -> dict:
    """イベントを左→右の木として配置し {ev_id: (x, y)} を返す。

    - start から next_targets を深さ優先で辿り、各イベントは一度だけ配置する
      (訪問済みガード。ループ E1→…→E4→E1 は E1 を再展開せず戻り矢印だけになる)。
    - 子は列(x)を +1。先頭の子は親と同じ行、以降の分岐は直前の兄弟の部分木が
      占める行数だけ下げて置く(部分木の高さを数えて兄弟衝突を回避)。
    - 既配置ノードへの辺(戻り/合流)は配置せず、描画側で弧/交差辺になる。
    - start から到達できないイベントは、木の下に列0から別ブロックで並べる。
    """
    COL_W, ROW_H, X0, Y0 = 120, 75, 60, 60
    events = data["events"]
    positions: dict[str, tuple[int, int]] = {}
    placed: set[str] = set()

    def place(ev_id: str, col: int, row: int) -> int:
        """(col,row) に配置し、部分木が占める行数を返す。"""
        positions[ev_id] = (X0 + col * COL_W, int(Y0 + row * ROW_H))
        placed.add(ev_id)
        used = 0
        for ch in next_targets(events[ev_id]):
            if ch not in events or ch in placed:
                continue          # 未定義先/既配置(戻り・合流)は辺だけ
            used += place(ch, col + 1, row + used)
        return max(1, used)

    # 配置の起点順: start を先頭に、残りは定義順(到達済みは自動スキップ)
    start = data.get("start")
    roots = ([start] if start in events else []) \
        + [e for e in events if e != start]
    # =284: 辺を持たない孤立イベント(遷移先なし/自己ループ/未定義先のみで、
    # どこからも到達されない)は最後に**1行へ横並び**(SHELF_COLS 列で折返し)。
    # 従来はそれぞれ1行+1行の余白を取り、単独イベントを作るたびに縦に
    # 大きく隙間が空いていた(ユーザーFB)。辺のある独立成分は従来どおり
    # 別ブロックだが、余白は半行に詰める。
    SHELF_COLS = 8
    referenced = {t for eid, ev in events.items()
                  for t in next_targets(ev) if t != eid}

    def isolated(ev_id: str) -> bool:
        if ev_id == start or ev_id in referenced:
            return False
        return all(t == ev_id or t not in events
                   for t in next_targets(events[ev_id]))

    row_cursor, first = 0, True
    shelf = []
    for root in roots:
        if root in placed:
            continue
        if isolated(root):
            shelf.append(root)
            continue
        if not first:
            row_cursor += 0.5
        row_cursor += place(root, 0, row_cursor)
        first = False
    if shelf and not first:
        row_cursor += 0.5
    for i, ev_id in enumerate(shelf):
        col, row = i % SHELF_COLS, i // SHELF_COLS
        positions[ev_id] = (X0 + col * COL_W,
                            int(Y0 + (row_cursor + row) * ROW_H))
        placed.add(ev_id)
    return positions


def _edge_line(c, x1, y1, x2, y2, r, dash, fill, width, self_loop):
    """イベント図のエッジ1本を描く(形状規則は編集画面と同一)。"""
    if self_loop:
        # 自己ループ
        c.create_line(x1 - 10, y1 - r + 2, x1 - 16, y1 - r - 22,
                      x1 + 16, y1 - r - 22, x1 + 10, y1 - r + 2,
                      smooth=True, fill=fill, width=width,
                      arrow="last", arrowshape=(8, 10, 4), dash=dash)
    elif y1 != y2 and x1 == x2:
        # 同じ列で行だけ違う(独立成分どうし等)は上下から直線
        dy = r if y2 > y1 else -r
        c.create_line(x1, y1 + dy, x2, y2 - dy, fill=fill,
                      width=width, arrow="last",
                      arrowshape=(10, 12, 5), dash=dash)
    elif y1 != y2 and x2 > x1:
        # =282: 行が違う右方向(行き)は「出発ノードの右」→「先ノードの左」の
        # 直線(同じ行の右隣と同じ出発点に統一。従来は上下の端から出ていた)
        c.create_line(x1 + r, y1, x2 - r, y2, fill=fill,
                      width=width, arrow="last",
                      arrowshape=(10, 12, 5), dash=dash)
    elif y1 != y2:
        # =282: 行が違う左方向(戻り)は「出発ノードの左」→「先ノードの右」を
        # 結ぶ弧。同じ行の戻り(下弧)と同じ出発点/到着点で、弦に垂直な
        # 下向きへ膨らませて行きの直線と重ならないようにする
        ax, ay = x1 - r + 4, y1 + 8
        bx, by = x2 + r - 4, y2 + 8
        dx, dy = bx - ax, by - ay
        ln = (dx * dx + dy * dy) ** 0.5 or 1.0
        nx, ny = -dy / ln, dx / ln
        if ny < 0:
            nx, ny = -nx, -ny
        k = r + 10
        c.create_line(ax, ay, (ax + bx) / 2 + nx * k, (ay + by) / 2 + ny * k,
                      bx, by, smooth=True, fill=fill, width=width,
                      arrow="last", arrowshape=(9, 11, 4), dash=dash)
    elif abs(x2 - x1) <= 120 and x2 > x1:
        # 右隣は直線
        c.create_line(x1 + r, y1, x2 - r, y2, fill=fill,
                      width=width, arrow="last",
                      arrowshape=(10, 12, 5), dash=dash)
    elif x2 > x1:
        # 右方向(離れている)は上弧
        c.create_line(x1 + r - 4, y1 - 10, (x1 + x2) / 2, y1 - r - 16,
                      x2 - r + 4, y2 - 10,
                      smooth=True, fill=fill, width=width,
                      arrow="last", arrowshape=(9, 11, 4), dash=dash)
    else:
        # 左方向(ループ・戻り)は下弧
        c.create_line(x1 - r + 4, y1 + 10, (x1 + x2) / 2, y1 + r + 16,
                      x2 + r - 4, y2 + 10,
                      smooth=True, fill=fill, width=width,
                      arrow="last", arrowshape=(9, 11, 4), dash=dash)


def draw_event_map(canvas, data, *, selected=None, current=None,
                   trail=None, glow=None, visited=None,
                   on_click=None, on_rclick=None) -> dict:
    """イベント図を canvas へ描画する。positions({ev_id:(x,y)})を返す。

    - selected: 編集画面の選択ノード(=124: 緑コーナー枠┏┓┗┛で表示)
    - current: 現在実行中イベント(=124: selected と同じ緑コーナー枠)
    - trail: 辿った遷移の (from, to) ペア集合=緑線で強調。
      通常の矢印に無いペア(watch遷移など)も同じ形状規則で緑線を追加描画する
    - glow: すごろく通過中のノードid集合(=124: 0.5秒だけ明度アップ)
    - visited: 実行済みノードid集合(=124: 減光。current は除外して渡す)
    - on_click: ノードクリック時のコールバック(ev_id)。None なら束縛しない
    - on_rclick: ノード右クリック時のコールバック(ev_id, tkイベント)。
      編集画面のカラーパレット用。None なら束縛しない
    """
    c = canvas
    c.configure(bg=canvas_bg())   # テーマに応じて背景色を追従
    c.delete("all")
    r = 26
    trail = set(trail or ())
    positions = layout_tree(data)
    xs = [px for (px, _py) in positions.values()] or [60]
    max_x = max(xs) + r           # 最右ノードの右端(スクロール域算出用)

    # エッジ(全遷移先へ。=283: 戻り(左向き)=点線、それ以外=実線)
    drawn_pairs: dict[tuple, tuple] = {}   # (src,tgt) -> dash
    for ev_id, (x1, y1) in positions.items():
        ev = data["events"][ev_id]
        targets = next_targets(ev)
        for tgt in targets:
            if tgt not in positions:
                continue
            x2, y2 = positions[tgt]
            # =283: 線種は**向き**で決める(ユーザー決定)。遷移先が遷移元より
            # **左**にある(戻り)=点線、それ以外(行き・同じ列・自己ループ)=実線。
            # 従来の「破線=複数候補の抽選/点線=全消化時の行き先」は廃止
            # (選択肢・数値入力で止まるノードは添え字「選択肢」等で分かる)。
            dash = (2, 3) if x2 < x1 else ()
            drawn_pairs[(ev_id, tgt)] = dash
            if (ev_id, tgt) in trail:
                continue           # 辿った遷移は後で緑線で上描き
            _edge_line(c, x1, y1, x2, y2, r, dash, edge_color(), 2,
                       self_loop=(tgt == ev_id))

    # 辿った遷移(緑線)。矢印として存在しないペア(watch遷移など)は実線で追加
    for (src, tgt) in trail:
        if src not in positions or tgt not in positions:
            continue
        x1, y1 = positions[src]
        x2, y2 = positions[tgt]
        dash = drawn_pairs.get((src, tgt), ())
        _edge_line(c, x1, y1, x2, y2, r, dash, TRAIL_COLOR, TRAIL_WIDTH,
                   self_loop=(tgt == src))

    # ノード
    glow = set(glow or ())
    visited = set(visited or ())
    for idx, (ev_id, (nx, ny)) in enumerate(positions.items()):
        ev = data["events"][ev_id]
        has_states = "states" in ev
        # =124: 塗りはカスタム色(color)+減光/明度アップで決める。
        # 選択中/実行中は塗りを変えず緑コーナー枠で示す。
        fill, txt = _node_colors(
            c, ev, has_states,
            dimmed=(ev_id in visited and ev_id != current),
            glowing=(ev_id in glow))
        is_start = data.get("start") == ev_id
        outline = node_outline(is_start)
        if ev_id in visited and ev_id != current:
            outline = mix_color(c, outline, canvas_bg(), 0.5)
        # タグはID非依存(空白等を含む名前でも安全に)
        tag = f"evnode{idx}"
        c.create_oval(nx - r, ny - r, nx + r, ny + r,
                      fill=fill, outline=outline,
                      width=2 if is_start else 1, tags=tag)
        label = ev_id if len(ev_id) <= 8 else ev_id[:7] + "…"
        c.create_text(nx, ny, text=label, fill=txt, font=(appfont.FAMILY, 9), tags=tag)
        if ev_id == current or (selected is not None and ev_id == selected):
            _corner_marks(c, nx, ny, r, tag)
        # 添え字はノード下へ順に積む: Nステート → 選択肢/数値入力 → 動画
        ly = ny + r + 9
        if has_states:
            c.create_text(nx, ly,
                          text=tr('{0}ステート').format(len(ev['states'])),
                          fill=states_label_color(), font=(appfont.FAMILY, 8), tags=tag)
            ly += 11
        # =124: 選択肢/数値入力で止まるノードの添え字(すごろくで
        # 「ここに止まるマス」が事前に分かる)
        nxt_raw = ev.get("next")
        if isinstance(nxt_raw, dict) or has_state_choice(ev):
            stop_label = None
            if (isinstance(nxt_raw, dict) and nxt_raw.get("choice") is not None) \
                    or has_state_choice(ev):   # =275 ステート移行の選択肢
                stop_label = tr("選択肢")
            elif isinstance(nxt_raw, dict) and nxt_raw.get("input") is not None:
                stop_label = tr("数値入力")
            if stop_label:
                c.create_text(nx, ly, text=stop_label,
                              fill=choice_label_color(),
                              font=(appfont.FAMILY, 8), tags=tag)
                ly += 11
        # 動画つきイベントの表示(フェーズ2)。ステート形式は
        # どれかのステートに動画があればイベントノードにも印を出す
        ev_videos = video_items(ev.get("channels"))
        has_video = bool(ev_videos) or (has_states and any(
            video_items(s.get("channels")) for s in ev["states"].values()
            if isinstance(s, dict)))
        if has_video:
            # ステート形式は区間がステートごとに違うので秒数は出さない
            c.create_text(nx, ly,
                          text=tr("▶動画") if has_states
                          else video_label(ev_videos),
                          fill=video_label_color(), font=(appfont.FAMILY, 8), tags=tag)
        if on_click is not None:
            c.tag_bind(tag, "<Button-1>",
                       lambda _e, i=ev_id: on_click(i))
        if on_rclick is not None:
            c.tag_bind(tag, "<Button-3>",
                       lambda e, i=ev_id: on_rclick(i, e))

    # スクロール領域は木の最右ノード(max_x)と、実際のノード下端まで。
    ys = [ny for (_nx, ny) in positions.values()] or [60]
    bottom = max(ys) + r + 24
    c.configure(scrollregion=(0, 0, max_x + 40, bottom))
    return positions


def _state_edge_line(c, x1, x2, y, r, dashed, fill, width):
    """ステート図のエッジ1本を描く(形状規則は編集画面と同一)。"""
    if x2 == x1:
        # 自己ループ(上に小さな弧)
        c.create_line(
            x1 - 10, y - r + 2, x1 - 16, y - r - 22,
            x1 + 16, y - r - 22, x1 + 10, y - r + 2,
            smooth=True, fill=fill, width=width,
            arrow="last", arrowshape=(8, 10, 4), dash=dashed)
    elif x2 > x1:
        # 右向き: 上を通る
        c.create_line(
            x1 + r - 4, y - 10, (x1 + x2) / 2, y - r - 14,
            x2 - r + 4, y - 10,
            smooth=True, fill=fill, width=width,
            arrow="last", arrowshape=(9, 11, 4), dash=dashed)
    else:
        # 左向き: 下を通る
        c.create_line(
            x1 - r + 4, y + 10, (x1 + x2) / 2, y + r + 14,
            x2 + r - 4, y + 10,
            smooth=True, fill=fill, width=width,
            arrow="last", arrowshape=(9, 11, 4), dash=dashed)


def transition_targets(t) -> list[str]:
    """transition raw(dict)から移行先ステートIDを列挙する(=73)。

    to は文字列 / {"random": [...]}(要素は文字列 or {"to","weight"}) /
    {"cond": [{"when","to"}...]}(=125) / else(全重み0・全条件不成立時の
    行き先)に対応する。図の矢印・検証・エディタの読み込みが同じ解釈を
    共有するための単一の置き場。
    """
    if not isinstance(t, dict):
        return []
    to = t.get("to")
    out: list[str] = []
    if isinstance(t.get("when"), dict) and t["when"].get("type") == "choice":
        # =275: 選択肢のステート宛て行き先(文字列 to)と既定の指定ステート。
        # イベント宛て({"event"})はステート図には出さない
        for ent in t.get("choice") or []:
            eto = ent.get("to") if isinstance(ent, dict) else None
            if isinstance(eto, str) and eto not in out:
                out.append(eto)
        draw = t.get("default")
        if isinstance(draw, dict) and isinstance(draw.get("to"), str) \
                and draw["to"] not in out:
            out.append(draw["to"])
        return out
    if isinstance(to, str):
        out.append(to)
    elif isinstance(to, dict):
        for ent in to.get("random") or []:
            if isinstance(ent, str):
                out.append(ent)
            elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                out.append(ent["to"])
        # =125: 判定式(cond)形式の行き先
        for row in to.get("cond") or []:
            if isinstance(row, dict) and isinstance(row.get("to"), str) \
                    and row["to"] not in out:
                out.append(row["to"])
        if isinstance(to.get("else"), str) and to["else"] not in out:
            out.append(to["else"])
        # =77: 全候補が実行済みのときの固定移行先も矢印に含める
        ex = to.get("when_exhausted")
        if isinstance(ex, dict) and isinstance(ex.get("to"), str) \
                and ex["to"] not in out:
            out.append(ex["to"])
    return out


def draw_state_map(canvas, ev, *, selected=None, current=None,
                   trail=None, on_click=None, on_rclick=None) -> dict:
    """ステートマシン図を canvas へ描画する。positions({sid:(x,y)})を返す。

    引数の意味は draw_event_map と同じ(current=現在実行中ステート、
    trail=辿ったステート移行の (from, to) ペア集合。=124: 選択中/実行中は
    緑コーナー枠、カスタム色は state raw の "color")。
    """
    c = canvas
    c.configure(bg=canvas_bg())   # テーマに応じて背景色を追従
    c.delete("all")
    if not ev or "states" not in ev:
        return {}
    trail = set(trail or ())
    states = list(ev["states"].keys())
    r = 24
    y = 70
    positions = {}
    x = 55
    for sid in states:
        positions[sid] = (x, y)
        x += 110

    # 遷移エッジ
    drawn_pairs: dict[tuple, tuple] = {}   # (src,tgt) -> dash
    for sid in states:
        t = ev["states"][sid].get("transition")
        if not t:
            continue
        to = t.get("to")
        targets = transition_targets(t)
        for tgt in targets:
            if tgt not in positions:
                continue
            x1, _ = positions[sid]
            x2, _ = positions[tgt]
            dashed = () if len(targets) == 1 else (4, 3)
            drawn_pairs[(sid, tgt)] = dashed
            if (sid, tgt) in trail:
                continue           # 辿った移行は後で緑線で上描き
            _state_edge_line(c, x1, x2, y, r, dashed, edge_color(True), 2)

    # 辿った移行(緑線)
    for (src, tgt) in trail:
        if src not in positions or tgt not in positions:
            continue
        x1, _ = positions[src]
        x2, _ = positions[tgt]
        dashed = drawn_pairs.get((src, tgt), ())
        _state_edge_line(c, x1, x2, y, r, dashed, TRAIL_COLOR, TRAIL_WIDTH)

    # ノード
    start_state = ev.get("start")
    for idx, (sid, (nx, ny)) in enumerate(positions.items()):
        st_raw = ev["states"].get(sid)
        # =124: 塗りはカスタム色(color)。選択中/実行中は緑コーナー枠
        fill, txt = _node_colors(c, st_raw, False,
                                 dimmed=False, glowing=False)
        outline = node_outline(sid == start_state)
        width = 2 if sid == start_state else 1
        tag = f"stnode{idx}"
        c.create_oval(nx - r, ny - r, nx + r, ny + r,
                      fill=fill, outline=outline, width=width, tags=tag)
        label = sid if len(sid) <= 7 else sid[:6] + "…"
        c.create_text(nx, ny, text=label, fill=txt, font=(appfont.FAMILY, 9), tags=tag)
        if sid == current or (selected is not None and sid == selected):
            _corner_marks(c, nx, ny, r, tag)
        if sid == start_state:
            c.create_text(nx, ny + r + 9, text=tr("開始"), fill=OK_COLOR,
                          font=(appfont.FAMILY, 8), tags=tag)
        # 動画つきステートの表示(フェーズ2)。開始ラベルと重ならないよう
        # 開始ステートでは1段下げる
        st_videos = video_items(st_raw.get("channels")) \
            if isinstance(st_raw, dict) else []
        if st_videos:
            c.create_text(nx, ny + r + (20 if sid == start_state else 9),
                          text=video_label(st_videos),
                          fill=video_label_color(), font=(appfont.FAMILY, 8), tags=tag)
        if on_click is not None:
            c.tag_bind(tag, "<Button-1>",
                       lambda _e, i=sid: on_click(i))
        if on_rclick is not None:
            c.tag_bind(tag, "<Button-3>",
                       lambda e, i=sid: on_rclick(i, e))

    # =124: スクロール領域は実際の最終ノード右端まで(従来は最終ノードの
    # 先に約130pxの余白があり、図が収まっていてもホイールで動いてしまった)。
    # 収まっているときは左端固定へ戻す。
    last_x = (55 + 110 * (len(states) - 1)) if states else 0
    right = max(last_x + r + 31, 300)
    c.configure(scrollregion=(0, 0, right, 130))
    try:
        if c.winfo_width() > 1 and right <= c.winfo_width():
            c.xview_moveto(0.0)
    except Exception:
        pass
    return positions
