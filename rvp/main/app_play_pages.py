"""メイン画面: 再生タブのページ切替・ランプ・ホイール・グラフ/遷移図/ログ表示(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import time
from .. import appfont, scenario_map
from ..i18n import tr

from .common import logger, paced_delay


class _RVPAppPlayPagesMixin:
    """RVPApp の mixin(=301 分割)。再生タブのページ切替・ランプ・ホイール・グラフ/遷移図/ログ表示"""

    def _play_card_of(self, page_id: str):
        """ページIDに対応するカードウィジェットを返す(=150)。"""
        return {
            "play": self.play_card,      # ①選択中のシナリオ・再生中チャンネル
            "map": self.map_card,        # ②イベント遷移図/ステート図
            "log": self.log_card,        # ③変数・イベントログ
            "device": self.lower_card,   # ④デバイス調整
            "graph": self.graph_card,    # ⑤グラフ表示
        }[page_id]

    def _play_cards(self):
        """表示中のページのカード列(巡回順。=150で可変長になった)。"""
        return tuple(self._play_card_of(p) for p in self._play_pages)

    @property
    def play_page_id(self) -> str:
        """現在表示中のページID(=150)。ページ判定は番号でなくこれで行う。"""
        try:
            return self._play_pages[self._play_page]
        except (IndexError, AttributeError):
            return ""

    def _update_page_lamp(self):
        """バーランプを現在ページに合わせて点灯し直す(=89)。"""
        for i, seg in enumerate(self.page_lamps):
            seg.configure(bg=self.PAGE_LAMP_ON if i == self._play_page
                          else self.PAGE_LAMP_OFF)

    def _relayout_page_lamps(self):
        """表示中のページ数に合わせてバーの分割数を合わせる(=150)。

        セグメントは常に PLAY_PAGE_COUNT 個あり、余った末尾を pack_forget
        する。expand=True なので、残ったセグメントが自動で幅を分け合う
        (3画面なら3分割)。戻すときは昇順に pack するので並び順は保たれる。
        """
        n = len(self._play_pages)
        for i, seg in enumerate(self.page_lamps):
            if i < n:
                if not seg.winfo_manager():
                    seg.pack(side="left", fill="both", expand=True,
                             padx=(0 if i == 0 else 6, 0))
            elif seg.winfo_manager():
                seg.pack_forget()
        self._update_page_lamp()

    def _apply_play_pages(self):
        """デバイス連動の有無で再生タブのページ構成を切り替える(=150)。

        シナリオ未読込(起動直後・読み込み失敗)のときは判定できないので
        5画面のまま(2026-08-15ユーザー決定)。
        """
        hide = self.scenario is not None and not self._scenario_has_device
        self._set_play_pages([p for p in self.PLAY_PAGE_ORDER
                              if not (hide and p in self.DEVICE_PAGES)])

    def _set_play_pages(self, pages):
        """表示するページ列を差し替える(=150)。

        表示中のページが消える場合は①再生へ戻す(残ったページの中で番号を
        クランプすると、意図しない画面へ飛んで分かりにくいため)。
        """
        pages = [p for p in self.PLAY_PAGE_ORDER if p in pages] or ["play"]
        if pages == self._play_pages:
            return
        cur = self.play_page_id
        self._play_card_of(cur).place_forget()
        self._play_pages = pages
        self._play_page = pages.index(cur) if cur in pages else 0
        self._play_scroll = 0
        self._place_sig = None
        self._relayout_page_lamps()
        self._refresh_play_card()
        self._on_play_page_shown()

    def _play_scroll_max(self) -> int:
        """現在のページがはみ出している量(0=収まっている)。"""
        card = self._play_cards()[self._play_page]
        return max(0, card.winfo_reqheight() - self.play_content.winfo_height())

    def _refresh_play_card(self):
        """現在のページを切替領域へ配置し直す(スクロール量をクランプ)。

        place で高さを変えると <Configure> が返ってきて再入するため、
        同じ配置なら何もしない+再入ガードで無限ループを防ぐ。
        """
        if getattr(self, "_placing", False):
            return
        card = self._play_cards()[self._play_page]
        avail = self.play_content.winfo_height()
        over = max(0, card.winfo_reqheight() - avail)
        self._play_scroll = max(0, min(self._play_scroll, over))
        sig = (self._play_page, over, self._play_scroll)
        if sig == getattr(self, "_place_sig", None):
            return
        self._placing = True
        try:
            # CTkウィジェットの place は width/height を受け付けないので、
            # 収まるときは relheight=1(領域を埋める)、はみ出すときは高さ指定
            # なし(=カードの自然高さ)にして y をずらす。
            if over > 0:
                card.place(x=0, y=-self._play_scroll, relwidth=1.0)
            else:
                card.place(x=0, y=0, relwidth=1.0, relheight=1.0)
            self._place_sig = sig
        finally:
            self._placing = False

    def _on_play_wheel(self, event):
        """再生タブの切替領域上でのホイール操作(はみ出し時のみ効く)。"""
        try:
            if event.widget.winfo_toplevel() is not self.root:
                return          # 編集画面など別ウィンドウのホイールは無視
        except Exception:
            return
        if self.tabs._current != tr("再生"):
            return
        if self.play_page_id == "graph":
            return          # =69 グラフ表示ページのホイールは時間縮尺に使う
        w = self.play_content
        if not w.winfo_ismapped():
            return
        # ポインタが切替領域の中にあるときだけ
        x, y = event.x_root, event.y_root
        x0, y0 = w.winfo_rootx(), w.winfo_rooty()
        if not (x0 <= x <= x0 + w.winfo_width()
                and y0 <= y <= y0 + w.winfo_height()):
            return
        # ③変数・イベントログのテキスト欄は自前でスクロールするので譲る
        t = event.widget
        while t is not None and t is not w:
            if t in (self.log_vars_box, self.log_events_box):
                return
            t = getattr(t, "master", None)
        if self._play_scroll_max() <= 0:
            return
        num = getattr(event, "num", 0)
        delta = getattr(event, "delta", 0)
        step = -1 if (delta > 0 or num == 4) else 1
        self._play_scroll += step * self.WHEEL_STEP
        self._refresh_play_card()

    def _goto_play_page(self, page: int):
        """指定ページ(表示中のページ列での位置)を表示する。"""
        if page != self._play_page:
            self._switch_play_page(page - self._play_page)

    def _goto_play_page_id(self, page_id: str):
        """指定ページIDを表示する(=150。非表示のIDなら何もしない)。"""
        if page_id in self._play_pages:
            self._goto_play_page(self._play_pages.index(page_id))

    def _pack_play_overlay(self, card, focus: bool = True):
        """選択肢/数値入力カードを操作バーの下(切替領域の外)へ表示する(=60)。

        ページ切替と無関係に常に見えるので、=57の「①再生ページへ強制的に
        切り替える」処理は不要になった(focus は呼び出し側の互換のため残す)。
        カードが出た分、下の切替領域が自動で縮む(pack の割り当て順)。
        """
        card.pack(fill="x", pady=(8, 0), before=self.play_area)

    def _switch_play_page(self, delta: int):
        """◀▶ボタンで再生タブのページを巡回切替する。

        ①再生 ⇄ ②イベント遷移 ⇄ ③変数・イベントログ ⇄ ④デバイス調整
        ⇄ ⑤グラフ表示(=150で並び替え)。▶で先頭へ巻き戻り、◀で逆順。
        デバイス連動なしのシナリオでは④⑤が外れ、①②③の3画面で巡回する。
        """
        cards = self._play_cards()
        old = self._play_page
        new = (old + delta) % len(cards)
        if new == old:
            return
        cards[old].place_forget()
        self._play_page = new
        self._play_scroll = 0
        self._place_sig = None
        self._update_page_lamp()     # =89 バーランプを追従
        self._refresh_play_card()
        self._on_play_page_shown()

    def _on_play_page_shown(self):
        """ページが切り替わった直後の初回描画(=150でID判定へ)。

        表示していない間は描画コストを払わない作りなので、出た瞬間に
        1回だけ強制更新する。
        """
        pid = self.play_page_id
        if pid == "graph":
            self._update_graph()                  # =69 グラフを即描画
        elif pid == "map":
            self._map_sig = None                  # 強制再描画
            self._update_event_map(center=True)
        elif pid == "log":
            self._log_sig = None                  # 強制更新
            self._update_run_log()

    def _toggle_graph_overlay(self):
        """=102/=103: グラフの表示モードを1つ進める(設定へ保存)。"""
        cur = (self.graph_view.overlay, self.graph_view.minus)
        try:
            i = self.GRAPH_MODES.index(cur)
        except ValueError:
            i = -1
        self._set_graph_mode(*self.GRAPH_MODES[(i + 1) % len(self.GRAPH_MODES)])
        self.save_app_config()

    def _set_graph_mode(self, overlay: bool, minus: bool):
        """=102/=103: 表示モードを反映し、ボタンの文言を「切替先」に合わせる。

        「-」サフィックス=マイナス表示(再生位置より右の将来を描かない)。
        """
        self.graph_view.set_overlay(bool(overlay))
        self.graph_view.set_minus(bool(minus))
        i = self.GRAPH_MODES.index(
            (self.graph_view.overlay, self.graph_view.minus))
        nov, nmi = self.GRAPH_MODES[(i + 1) % len(self.GRAPH_MODES)]
        label = (tr("1枚表示") if nov else tr("個別表示")) + ("-" if nmi else "")
        self.graph_overlay_btn.configure(text=label)

    def _update_graph(self):
        """⑤グラフ表示を更新する(=69。約30fpsで呼ばれる)。

        表示中のページでないときは何もしない(描画コストを払わない)。
        """
        if self.play_page_id != "graph" or not self.graph_card.winfo_ismapped():
            return
        self.graph_view.set_snapshot(self.player.graph_snapshot())

    def _animate_graph(self):
        t0 = time.perf_counter()
        try:
            self._update_graph()
        except Exception:
            logger.exception("graph update failed")
        # =104: 既定60fps(16ms)。設定で30fps(33ms)へ切替可。
        # =229: 待ちの決め方は `paced_delay()` へ集約(フレーム開始基準+
        # 描画時間を下回らない=CPUの半分より多くを描画に使わない)
        cost_ms = (time.perf_counter() - t0) * 1000.0
        # =229: フレーム開始基準のペーシングへ(paced_delay の docstring)。
        # 設定 60fps が実際に 60fps で回るようになる。
        self.root.after(paced_delay(self._graph_interval_ms, cost_ms),
                        self._animate_graph)

    def _update_event_map(self, center: bool = False):
        """②イベント遷移の図を差分更新する(_poll_stateから毎回呼ばれる)。

        現在イベント=緑コーナー枠(=124)、◀◀で戻れる履歴経路=緑線、
        実行済み=減光、すごろく通過=0.5秒の明度アップ。イベントが切り替わった
        時だけ現在ノードへ自動センタリングし、それ以外は手動スクロールを尊重。
        """
        if self.play_page_id != "map":
            return
        st = self.player.state
        trail_ids = tuple(st.get("event_trail") or ())
        cur = trail_ids[-1] if trail_ids else ""
        cur_state = st.get("state_id") or ""
        state_trail = tuple(st.get("state_trail") or ())
        # =124: すごろく通過の明度アップ(0.5秒)と実行済みノードの減光。
        # グローの点灯/消灯は sig の変化として扱う(消灯は次のpollで気づく)
        now = time.monotonic()
        glow = tuple(sorted({e for (e, ts) in (st.get("advance_glow") or ())
                             if now - ts < 0.5}))
        visited = tuple(sorted(st.get("visited_events") or ()))
        data = self._map_data
        sig = (id(data), trail_ids, cur, cur_state, state_trail,
               glow, visited, ctk.get_appearance_mode())
        if sig == self._map_sig and not center:
            return
        prev_cur = self._map_sig[2] if self._map_sig else None
        self._map_sig = sig

        c = self.map_canvas
        if not data or not isinstance(data.get("events"), dict):
            c.configure(bg=scenario_map.canvas_bg())
            c.delete("all")
            c.create_text(
                16, 28, anchor="w",
                text=tr("(シナリオを読み込むとイベント図を表示します)"),
                fill="gray55", font=(appfont.FAMILY, 11))
            c.configure(scrollregion=(0, 0, 300, 60))
            self._set_map_split(False)
            return

        pairs = set(zip(trail_ids, trail_ids[1:]))
        # =299: 手動配置のシナリオは保存された座標で描く(再生タブは書き戻さない)
        manual_pos = scenario_map.manual_positions(data) \
            if scenario_map.is_manual(data) else None
        positions = scenario_map.draw_event_map(
            c, data, current=cur or None, trail=pairs,
            glow=glow, visited=visited, on_click=None,
            positions=manual_pos)

        # ステート形式イベント実行中(および停止後の余韻)は下半分にステート図
        ev_raw = data["events"].get(cur) if cur else None
        is_states = bool(ev_raw and isinstance(ev_raw.get("states"), dict))
        self._set_map_split(is_states)
        if is_states:
            scenario_map.draw_state_map(
                self.map_state_canvas, ev_raw,
                current=cur_state or None, trail=set(state_trail),
                on_click=None)

        # イベントが切り替わった時だけセンタリング(手動スクロールを邪魔しない)
        if (center or cur != prev_cur) and cur:
            self._center_map_on(cur, positions)

    def _update_run_log(self):
        """③変数・イベントログを差分更新する(_poll_stateから毎回呼ばれる)。

        上段=変数の現在値(全書き換え)、下段=通過ログ(追記式。縮んだら
        =再生開始/シナリオ切替のリセットなので全消去してやり直す)。
        """
        if self.play_page_id != "log":
            return
        st = self.player.state
        vars_ = st.get("vars") or {}
        run_log = tuple(st.get("run_log") or ())
        sig = (tuple(vars_.items()), len(run_log))
        if sig == self._log_sig:
            return
        self._log_sig = sig

        # 上段: 変数の現在値
        def _fmt_var(v):
            if isinstance(v, float):
                return f"{v:g}"
            return str(v)
        vbox = self.log_vars_box
        vbox.configure(state="normal")
        vbox.delete("1.0", "end")
        if vars_:
            vbox.insert("1.0", "\n".join(
                f"{k}: {_fmt_var(v)}" for k, v in vars_.items()))
        else:
            vbox.insert("1.0", tr("(変数なし)"))
        vbox.configure(state="disabled")

        # 下段: イベントログ(追記式)
        lbox = self.log_events_box
        if len(run_log) < self._log_len:
            lbox.configure(state="normal")
            lbox.delete("1.0", "end")
            lbox.configure(state="disabled")
            self._log_len = 0
        new_entries = run_log[self._log_len:]
        if new_entries:
            # 末尾を見ている時だけ自動スクロール(過去行の閲覧を邪魔しない)
            at_bottom = lbox.yview()[1] > 0.98
            lbox.configure(state="normal")
            for kind, name in new_entries:
                if kind == "event":
                    line = tr("イベント: {0}").format(name)
                elif kind == "state":
                    line = "  " + tr("ステート: {0}").format(name)
                elif kind == "chan":
                    # =56: 解釈後のチャンネル設定(再生方式/終了条件/件数)。
                    # JSONを読まなくても「アプリがどう解釈したか」が分かる。
                    line = "  ・" + name
                elif kind == "item":
                    # =54: 再生を開始したアイテム(chID: ファイル名[ 区間])。
                    # 順番/ランダム再生が効いているかを後から確認できる。
                    line = "    ▶ " + name
                elif kind == "var":
                    # =70: 変数操作(名前: 変更前 → 変更後 (種別 値) [発火場所])
                    line = "    ✎ " + name
                elif kind in ("choice", "input"):
                    # =70: 選択肢/数値入力を表示したこと(候補・制限時間・既定)
                    line = "    ☰ " + name
                elif kind == "warn":
                    line = "    ⚠ " + name
                elif kind == "bgm":
                    # =256: BGMの開始/停止(ノード入場で変化したときだけ記録)
                    line = "    ♪ " + name
                elif kind == "end":
                    # =56: なぜ次へ進んだか(終了条件/移行条件/選択肢の理由)
                    line = "  → " + name
                else:
                    line = "  " + str(name)
                lbox.insert("end", line + "\n")
            lbox.configure(state="disabled")
            self._log_len = len(run_log)
            if at_bottom:
                lbox.see("end")

    def _install_map_drag_pan(self, canvas):
        canvas.configure(cursor="fleur")
        canvas.bind("<ButtonPress-1>", lambda e, c=canvas: self._map_pan_press(c, e))
        canvas.bind("<B1-Motion>", lambda e, c=canvas: self._map_pan_drag(c, e))
        canvas.bind("<ButtonRelease-1>", lambda e, c=canvas: self._map_pan_release(c, e))

    def _map_pan_press(self, canvas, e):
        canvas.scan_mark(e.x, e.y)
        canvas._rvp_pan = (e.x, e.y)

    def _map_pan_drag(self, canvas, e):
        if getattr(canvas, "_rvp_pan", None) is None:
            return
        canvas.scan_dragto(e.x, e.y, gain=1)

    def _map_pan_release(self, canvas, _e):
        canvas._rvp_pan = None

    def _set_map_split(self, split: bool):
        """②の上下分割(ステート図)の表示/非表示を切り替える。"""
        if split == self._map_split:
            return
        self._map_split = split
        if split:
            self._map_grid.grid_rowconfigure(1, weight=0)   # =287: 固定高さ
            self.map_state_wrap.grid(row=1, column=0, sticky="nsew",
                                     pady=(8, 0))
        else:
            self.map_state_wrap.grid_forget()
            self._map_grid.grid_rowconfigure(1, weight=0)

    def _center_map_on(self, ev_id: str, positions: dict):
        """イベント図のスクロール位置を、指定ノードが中央に来るよう動かす。"""
        if ev_id not in positions:
            return
        c = self.map_canvas
        sr = (c.cget("scrollregion") or "").split()
        if len(sr) != 4:
            return
        total_w, total_h = float(sr[2]), float(sr[3])
        vw = max(c.winfo_width(), 1)
        vh = max(c.winfo_height(), 1)
        x, y = positions[ev_id]
        if total_w > vw:
            c.xview_moveto(max(0.0, min(1.0, (x - vw / 2) / total_w)))
        else:
            c.xview_moveto(0.0)
        if total_h > vh:
            c.yview_moveto(max(0.0, min(1.0, (y - vh / 2) / total_h)))
        else:
            c.yview_moveto(0.0)
