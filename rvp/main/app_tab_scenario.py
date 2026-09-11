"""メイン画面: シナリオタブ(読み込み・D&D・履歴・編集画面との連携)(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import time
from ..scenario import Scenario, TRACK_ROTATE_A10, TRACK_ROTATE_UFO
from tkinter import filedialog, messagebox
from ..i18n import load_config, save_config, tr
from ..rotate_source import load_rotate_source

from .common import LABEL, MUTED, logger, scenario_display_name
from .startup import _startup_mark
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _RVPAppTabScenarioMixin:
    """RVPApp の mixin(=301 分割)。シナリオタブ(読み込み・D&D・履歴・編集画面との連携)"""

    def _build_tab_scenario(self, tab):
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=8, pady=8)

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x")
        self.btn_open = ctk.CTkButton(
            row, text=tr("シナリオファイルを開く"), width=180, height=36,
            fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
            command=self.on_open_scenario,
        )
        self.btn_open.pack(side="left")

        # =45: 起動時の前回シナリオ自動読込(=43)で「未選択→編集画面」という
        # 新規作成の入口が塞がれたため、明示的な新規作成ボタンを追加
        self.btn_new = ctk.CTkButton(
            row, text=tr("新規作成"), width=100, height=36,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self.on_new_scenario,
        )
        self.btn_new.pack(side="left", padx=(8, 0))

        self.btn_edit = ctk.CTkButton(
            row, text=tr("編集画面"), width=100, height=36,
            fg_color="transparent", border_width=1,
            border_color=MUTED, text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self.on_edit_scenario,
        )
        self.btn_edit.pack(side="left", padx=(8, 0))

        self.scenario_title_label = ctk.CTkLabel(
            wrap, text=tr("(シナリオ未選択)"),
            font=ctk.CTkFont(size=15, weight="bold"),
            wraplength=540, justify="left",
        )
        self.scenario_title_label.pack(anchor="w", pady=(16, 2))
        self.scenario_path_label = ctk.CTkLabel(
            wrap, text="", text_color=LABEL,
            font=ctk.CTkFont(size=11), wraplength=540, justify="left",
        )
        self.scenario_path_label.pack(anchor="w")

        # ---- 最近のシナリオ(履歴) ----
        ctk.CTkLabel(
            wrap, text=tr("最近のシナリオ"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(16, 4))
        # =139: ウィンドウを縦へ伸ばしたとき広がるのは**この履歴一覧**
        # (ユーザー決定。=57の「シナリオ内容が余りを吸収する」から変更)。
        self.history_frame = ctk.CTkScrollableFrame(
            wrap, height=132, corner_radius=10, fg_color=("gray92", "gray17"))
        self.history_frame.pack(fill="both", expand=True)
        self.history_empty_label = ctk.CTkLabel(
            self.history_frame, text=tr("(まだ履歴はありません)"),
            text_color=LABEL, font=ctk.CTkFont(size=11))

        ctk.CTkLabel(
            wrap, text=tr("シナリオ内容"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=LABEL,
        ).pack(anchor="w", pady=(14, 4))

        # =139: シナリオ内容は高さ固定(縦の余りは「最近のシナリオ」が吸収)。
        # 高さはフォントの実測linespaceから動的に求める(WindowsのBIZ UD等、
        # 環境でメトリクスが変わるため)。内側テキストの上下余白=
        # corner_radius×2+1。
        # =140: Windows実機では=139の「4行分」が2行強にしか見えなかった
        # (ユーザー報告)→**倍の8行分**へ(「この倍の高さがちょうどよい」)。
        self.scenario_box = ctk.CTkTextbox(
            wrap, corner_radius=10, height=170, font=ctk.CTkFont(size=12),
        )
        try:
            import tkinter.font as _tkfont
            _ls = _tkfont.Font(
                font=self.scenario_box._textbox.cget("font")
            ).metrics("linespace")
            self.scenario_box.configure(height=_ls * 8 + 21)
        except Exception:
            pass   # 実測に失敗しても既定170pxで動く
        self.scenario_box.pack(fill="x")
        self.scenario_box.insert("1.0", tr("シナリオファイルを開くと、ここに説明が表示されます。"))
        self.scenario_box.configure(state="disabled")

    def _scan_scenario_has_device(self, sc) -> bool:
        """シナリオがデバイストラック(funscript/csv)を1つでも持つか(=150)。

        1つも無ければ「デバイス連動なしのシナリオ」とみなし、再生タブから
        ④デバイス調整・⑤グラフ表示を外す(この2画面は見ても何も起きない)。
        判定は**シナリオの内容だけ**で行い、デバイスの接続状態は見ない
        (2026-08-15ユーザー決定。挙動を予測しやすくするため)。
        走査に失敗したときは**5画面のまま**にする(隠しすぎない側に倒す)。

        =252: シナリオの「デバイス連動」フラグ(device_enabled)が OFF の
        ときは、トラックの有無にかかわらず False(=④⑤を隠す)。
        トラックの紐づけ定義がJSONに残っていても駆動しないため。
        """
        if not getattr(sc, "device_enabled", True):
            return False
        try:
            for ev in sc.events.values():
                for st in ev.states.values():
                    for ch in st.channels.values():
                        for item in ch.items:
                            if item.tracks:
                                return True
        except Exception:
            logger.exception("scan device tracks failed")
            return True          # 判定できなければ隠さない
        return False

    def on_open_scenario(self):
        kwargs = {}
        if self._last_dir and os.path.isdir(self._last_dir):
            kwargs["initialdir"] = self._last_dir
        path = filedialog.askopenfilename(
            title=tr("シナリオファイルを選択"),
            filetypes=[(tr("シナリオファイル"), "*.json"), (tr("すべてのファイル"), "*.*")],
            **kwargs,
        )
        if not path:
            return
        self._load_scenario(path)

    def _on_scenario_dnd_drop(self, event):
        """OSからのシナリオファイルD&D(=270)。

        シナリオタブのフレームへの落下で呼ばれ、「シナリオファイルを開く」と
        同じ読み込みを行う。受け付け条件:
          - 現在タブ=シナリオ のときだけ(登録先がシナリオタブのフレーム
            なので通常ここに来るのはシナリオタブ表示中だが、保険で確認)
          - .json のみ。複数落とされたら先頭の .json だけ読む
        対象外拡張子・読み込み失敗は編集画面のD&D(=44)と同じ流儀で
        黙って無視する(読み込みエラー自体は _load_scenario がダイアログを
        出すのでそれに任せる)。
        """
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            return
        try:
            if self.tabs._current != tr("シナリオ"):
                return
        except Exception:
            return
        js = [p for p in paths
              if os.path.splitext(p)[1].lower() == ".json"]
        if not js:
            return
        try:
            self._load_scenario(js[0])
        except Exception:
            logger.exception("scenario D&D load failed: %s", js[0])

    def _scan_scenario_split_lanes(self, sc) -> dict:
        """未接続プレビュー用: シナリオ内に2ch(タイプBのCSV)のrotate/rotate_a10
        トラックがあるレーンを検出する。

        接続デバイスが無くても、その内容が ufotw(左右2ロータ独立)向けなら
        左右分割バー+左右反転チェックをプレビュー表示できるようにする。
        funscript は必ず1chなので、拡張子が .csv のトラックだけ調べる。
        """
        lanes = {"ufo": False, "a10": False}
        lane_of_type = {TRACK_ROTATE_UFO: "ufo", TRACK_ROTATE_A10: "a10"}
        try:
            for ev in sc.events.values():
                for st in ev.states.values():
                    for ch in st.channels.values():
                        for item in ch.items:
                            for trk in item.tracks:
                                lane = lane_of_type.get(trk.type)
                                if lane is None or lanes[lane]:
                                    continue
                                path = trk.funscript or ""
                                if not path.casefold().endswith(".csv"):
                                    continue   # funscriptは常に1ch
                                try:
                                    if load_rotate_source(path).channels >= 2:
                                        lanes[lane] = True
                                except Exception:
                                    pass
                    if lanes["ufo"] and lanes["a10"]:
                        return lanes
        except Exception:
            pass
        return lanes

    def _load_scenario(self, path: str, goto_play: bool = False):
        """シナリオを読み込む。goto_play=True なら成功時に再生タブへ自動遷移。

        =154: **既定を False に変えた**(ユーザー決定)。=43で「シナリオを選ぶと
        再生タブへ自動で移る」導線を入れたが、実使用のフィードバックで
        ①勝手な画面遷移が普通でない動きで煩わしい
        ②「最近のシナリオ」を行き来してシナリオ内容(detail)を見比べたいときに
        不便、となったため、**手で選んだときはタブを動かさない**。
        自動遷移が残るのは**起動時の自動オープンだけ**(そもそも画面が動く
        わけではなく、起動直後の表示タブが再生になるだけ=最短で視聴に入れる)。
        """
        try:
            self.scenario = Scenario.load(path)
            self._failed_load_path = None
        except Exception as e:
            self.scenario = None
            # =284: 読み込みに失敗しても「編集」からは**このファイルを**開ける
            # ようにパスを覚えておく(素材ファイルを消した後もシナリオ編集で
            # そのアイテムを外せる=ユーザー要望)。編集画面は生のJSONを読む
            # ので素材の有無に関係なく開け、保存時の検証で欠落を報告する。
            self._failed_load_path = path
            self._scenario_split_lanes = {"ufo": False, "a10": False}
            self._map_data = None
            self._map_sig = None
            self._scenario_has_device = True     # =150 未読込は5画面へ戻す
            self._apply_play_pages()
            self.bg_art.set_scenario(None)       # =262 背景イラストを外す
            messagebox.showerror(
                "RVP",
                tr('シナリオファイルの読み込みに失敗しました:\n{0}').format(e)
                + "\n\n" + tr("「編集」ボタンでこのシナリオを編集画面で開けます(素材の抜けは保存時に確認できます)。"))
            return

        sc = self.scenario
        # =130→=131: 読み込み警告(load_warnings)は**ここでは表示しない**。
        # シナリオ選択のたびに警告音つきダイアログが出るのは驚かせるため、
        # 表示は編集画面の保存時(_report=画面内メッセージ・音なし)に一本化
        # した(2026-08-13ユーザー決定)。
        # 選択肢のあるシナリオか(=選択肢イベントが1つでもあるか)を判定し、
        # 選択肢カードの常時表示/非表示を切り替える(次の_poll_stateで反映)
        self._scenario_has_choices = any(
            ev.next_choice or ev.has_state_choice   # =275 ステート移行の選択肢
            for ev in sc.events.values())
        self._sync_autoselect_visible()      # =61: 操作バーのチェック
        # =150: デバイストラックが皆無のシナリオなら、再生タブを3画面
        # (①再生/②イベント遷移/③変数・イベントログ)構成へ切り替える。
        self._scenario_has_device = self._scan_scenario_has_device(sc)
        self._apply_play_pages()
        # =262: 背景イラスト(シナリオ指定×視聴側設定のANDで表示)
        self.bg_art.set_scenario(sc.background)
        # 未接続プレビュー用に、2ch(タイプBのCSV)rotate内容を持つレーンを検出
        self._scenario_split_lanes = self._scan_scenario_split_lanes(sc)
        # =107: 動画つきシナリオなら接続タブのmpv欄を自動で開く(停止状態の
        # このタイミング=再生前に、パス設定が必要なことに気づけるように)。
        # (ここでは書かず、この関数の末尾の save_app_config にまとめる)
        if self._scenario_has_video(sc):
            self._set_mpv_open(True, save=False)
        self._choice_sig = None
        self._choice_display = None
        self._cancel_autoselect()
        # ②イベント状態ビュー用に生JSONを読み込む(編集画面の図と同じデータ源。
        # editor._load_raw を使い、v1 nodes形式/items直書きも図示できる形へ変換)。
        # シナリオ切替で軌跡・図はリセットされる(_map_sig=None で強制再描画)。
        try:
            from ..editor import _load_raw
            raw = _load_raw(sc.path)
            self._map_data = raw if isinstance(raw, dict) \
                and isinstance(raw.get("events"), dict) else None
        except Exception:
            self._map_data = None
        self._map_sig = None
        # 別シナリオを読み込んだら、再生中/一時停止中でも停止して
        # イベント・ステート・音声を完全にリセットし、再生待機状態にする
        # (シナリオ履歴からの選択・編集画面の上書き保存の双方をカバー)。
        self.runner.submit(self.player.stop_and_reset(sc.title))
        # =152: イベント数の表示は廃止(ユーザー決定)。視聴前に分岐や長さの
        # ボリュームが推測できてしまい、ネタバレになるため。構成を知りたい
        # ときは編集画面で見る。
        # =153: 表示名は**拡張子を除いたファイル名**に統一(ユーザー決定)。
        name = scenario_display_name(sc.path)
        self.scenario_title_label.configure(text=name)
        self.scenario_path_label.configure(text=sc.path)
        self.play_scenario_label.configure(text=name)
        self._render_scenario_content(sc)
        self.btn_play.configure(state="normal")
        # 前回フォルダを記憶し、即時保存(異常終了でも失われないように)
        self._last_dir = os.path.dirname(os.path.abspath(sc.path))
        self.save_app_config()
        # 履歴へ記録(開いた時=読み込み成功時)
        # =155: 履歴への記録は**ここでは行わない**。シナリオを選んだだけで
        # 「最近のシナリオ」の並びが変わると、見比べている最中に行が動いて
        # しまうため(ユーザー決定)。記録は再生タブへ切り替えた時
        # (_on_tab_changed)に行う。読み込み直後は「まだ記録していない」印。
        self._recorded_path = None
        # =43→=154: 再生タブへの自動遷移は**起動時の自動オープンだけ**に限定。
        # 手で選んだとき(ファイルを開く/履歴クリック)はタブを動かさない。
        if goto_play:
            self.tabs.set(tr("再生"))

    def _auto_open_last_scenario(self, _retry: int = 0):
        """起動時に前回シナリオ(履歴先頭)を自動で開く(=43 案1・常時有効)。

        成功すると _load_scenario 経由で再生タブへ自動遷移し、「再生」
        1クリックで視聴を開始できる(=154で自動遷移が残るのはここだけ。
        手で選んだときはタブを動かさない)。ファイルが移動/削除されていたり
        壊れていたら**静かに見送り**(起動時にエラーダイアログを出さない・
        履歴からも消さない)、従来どおりシナリオタブから始める。

        =81: ウィンドウがまだ実体化していなければ少し待って再試行する
        (最大2秒=テスト等のwithdraw環境では待ち切って通常どおり開く)。
        UI更新中の想定外の例外でも起動自体は続行する。
        """
        # =93: after(150)のコールバックが「いつ実際に発火したか」を記録する
        # (v92ログでは発火自体が7.7秒遅れていた=それまでループがブロック)
        if _retry == 0:
            _startup_mark("自動オープン: afterコールバック発火")
        try:
            viewable = bool(self.root.winfo_viewable())
            # withdraw/iconify 中(テスト環境や最小化起動)は待っても
            # 実体化しないので、待たずにそのまま開く
            hidden = self.root.wm_state() in ("withdrawn", "iconic")
        except Exception:
            viewable, hidden = True, False
        if not viewable and not hidden and _retry < 20:
            if _retry == 0:
                _startup_mark("自動オープン: ウィンドウ実体化待ちへ")
            self.root.after(100,
                            lambda: self._auto_open_last_scenario(_retry + 1))
            return
        _startup_mark(f"自動オープン: 実体化確認OK(retry={_retry})")
        hist = self._load_history()
        _startup_mark("自動オープン: 履歴読込")
        if not hist:
            return
        path = hist[0]["path"]
        if not os.path.isfile(path):
            return
        _startup_mark("自動オープン: isfile確認(前回シナリオのドライブ応答)")
        try:
            Scenario.load(path)   # 事前検証(失敗はダイアログを出さず見送る)
        except Exception:
            return
        _startup_mark("自動オープン: Scenario.load(事前検証)")
        try:
            # =154: 自動遷移が残るのはここだけ(起動直後の表示タブを再生に
            # する=「再生」1クリックで視聴を始められる状態にする)。
            self._load_scenario(path, goto_play=True)
        except Exception:
            # =81: 自動オープンのUI更新で何が起きても起動は続行する
            import traceback
            traceback.print_exc()
        _startup_mark("自動オープン: _load_scenario 完了")

    def _on_space_key(self, _event=None):
        """スペースキーで再生/一時停止をトグル(=43 案2)。

        文字入力を妨げないよう Entry/Text 系にフォーカスがある時は無効。
        再生ボタンが無効(シナリオ未読込など)の時も何もしない。
        """
        try:
            w = self.root.focus_get()
        except Exception:
            w = None
        if w is not None and w.winfo_class() in ("Entry", "Text", "TEntry"):
            return
        if str(self.btn_play.cget("state")) == "disabled":
            return
        self.on_play_pause()

    def _load_history(self) -> list:
        """設定から履歴リストを読み込む(壊れたエントリは除外)。"""
        cfg = load_config()
        hist = cfg.get("history")
        out = []
        if isinstance(hist, list):
            for e in hist:
                if (isinstance(e, dict) and isinstance(e.get("path"), str)
                        and e["path"]):
                    out.append({
                        "path": e["path"],
                        "title": e.get("title") if isinstance(e.get("title"), str)
                        else "",
                        "ts": e.get("ts") if isinstance(e.get("ts"), (int, float))
                        else 0,
                    })
        return out

    def _save_history(self, hist: list):
        cfg = load_config()
        cfg["history"] = hist[:self.HISTORY_MAX]
        save_config(cfg)

    def _on_tab_changed(self, name: str):
        """タブが切り替わったときの処理(=155)。

        **再生タブへ移った時に、読み込み済みシナリオを履歴の先頭へ記録する。**
        =154までは読み込み時点で記録していたが、シナリオを選んだだけで
        「最近のシナリオ」の並びが変わってしまい、説明文を見比べている最中に
        行が動いて不便だった(ユーザー決定)。「視聴するために再生タブを開いた」
        タイミングを記録の合図にする。

        同じシナリオのまま再生タブを出入りしても記録し直さない
        (`_recorded_path`)。並びも更新日時も変わらないので、re-render と
        コンフィグ書き込みを無駄に走らせないため。
        """
        # =263: 背景イラストは「再生タブ表示中」だけ出す(入る=表示/出る=解除)
        self.bg_art.on_tab_changed()
        if name != tr("再生"):
            return
        sc = self.scenario
        if sc is None or not sc.path:
            return
        if self._recorded_path == os.path.abspath(sc.path):
            return
        self._record_history(sc)

    def _record_history(self, sc: Scenario):
        """開いたシナリオを履歴の先頭へ記録する(同一パスは繰り上げ・重複なし)。"""
        path = os.path.abspath(sc.path)
        self._recorded_path = path      # =155 二重記録の抑止
        hist = self._load_history()
        hist = [e for e in hist if os.path.abspath(e["path"]) != path]
        hist.insert(0, {"path": path, "title": sc.title or os.path.basename(path),
                        "ts": int(time.time())})
        hist = hist[:self.HISTORY_MAX]
        self._save_history(hist)
        self._refresh_history_list()

    def _remove_history(self, path: str):
        hist = [e for e in self._load_history()
                if os.path.abspath(e["path"]) != os.path.abspath(path)]
        self._save_history(hist)
        self._refresh_history_list()

    def _open_from_history(self, path: str):
        """履歴の行クリック。存在すれば読み込み、無ければ一覧から除去。"""
        if not os.path.isfile(path):
            messagebox.showwarning(
                "RVP",
                tr('ファイルが見つかりません(移動・削除された可能性があります):\n{0}').format(path))
            self._remove_history(path)
            return
        self._load_scenario(path)

    def _refresh_history_list(self):
        """履歴フレームの行を作り直す。新しい順・最大 HISTORY_MAX 件。

        =90: 100件ぶんを一気に作ると起動・シナリオ切替のたびに数百msの
        UI停止が起きるため、**チャンク分割の遅延構築**にした。先頭
        HISTORY_CHUNK 件だけ同期で作り(スクロール枠の見えている範囲は
        即座に埋まる)、残りは after(15ms) で1チャンクずつ継ぎ足す。
        """
        # 進行中の遅延構築があればキャンセル(作り直しの重複防止)
        if getattr(self, "_hist_job", None) is not None:
            try:
                self.root.after_cancel(self._hist_job)
            except Exception:
                pass
            self._hist_job = None
        for w in self.history_frame.winfo_children():
            if w is not self.history_empty_label:
                w.destroy()
        hist = self._load_history()
        if not hist:
            self.history_empty_label.pack(anchor="w", padx=6, pady=4)
            return
        self.history_empty_label.pack_forget()
        self._hist_pending = hist[:self.HISTORY_MAX]
        self._build_history_chunk()

    def _build_history_chunk(self):
        """保留中の履歴行を1チャンクぶん生成する(=90)。"""
        self._hist_job = None
        chunk = self._hist_pending[:self.HISTORY_CHUNK]
        self._hist_pending = self._hist_pending[self.HISTORY_CHUNK:]
        for e in chunk:
            self._make_history_row(e)
        # =93: Windowsでのチャンクあたりの所要(フォント実測を含む)を見る
        _startup_mark(f"履歴チャンク構築 {len(chunk)}行(残り{len(self._hist_pending)})")
        if self._hist_pending:
            self._hist_job = self.root.after(15, self._build_history_chunk)

    def _make_history_row(self, e: dict):
        """履歴1件ぶんの行ウィジェットを作る(=90でくくり出し)。"""
        path = e["path"]
        exists = os.path.isfile(path)
        row = ctk.CTkFrame(self.history_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        when = ""
        if e.get("ts"):
            try:
                when = time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(e["ts"]))
            except (ValueError, OSError):
                when = ""
        # =153: 履歴の行も表示名(拡張子を除いたファイル名)だけにする。
        # 記録時の title は使わない=ファイルを改名しても表示が追従する。
        label = scenario_display_name(path)
        if when:
            label += f"   {when}"
        if not exists:
            label += tr("  (見つかりません)")
        # =66: 「✕」を**先に**パックして幅を確保する。行のボタンを先に
        # pack(expand=True) すると、ファイル名が長いときに要求幅が勝って
        # ✕が枠外へ押し出され、押せなくなっていた(ユーザー報告)。
        ctk.CTkButton(
            row, text="✕", width=26, height=28,
            fg_color="transparent", text_color="#e05a5a",
            hover_color=("gray85", "gray28"),
            command=lambda p=path: self._remove_history(p)).pack(side="right")
        # =82: width を明示して**要求幅を固定**する。テキストの長さが
        # 要求幅に反映されると「テキスト変更→レイアウト変化→<Configure>
        # →また省略計算」の発振経路ができ、Windows実機で
        # RecursionError の嵐=起動不能に至った(2026-08-06)。実際の
        # 表示幅は pack(fill=x, expand=True) が与えるので見た目は不変。
        btn = ctk.CTkButton(
            row, text=label, anchor="w", height=28, width=120,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            text_color=(MUTED if not exists else ("gray20", "gray85")),
            hover_color=("gray85", "gray28"),
            command=lambda p=path: self._open_from_history(p))
        btn.pack(side="left", fill="x", expand=True)
        # 収まらないときは中間を「…」で省略し、末尾の日時を残す(=66)
        btn._rvp_full_text = label
        btn.bind("<Configure>", lambda _e, b=btn: self._fit_history_label(b))
        self._fit_history_label(btn)

    @staticmethod
    def _ellipsize(text: str, font, avail: int) -> str:
        """avail ピクセルに収まるよう文字列の**中間**を「…」で省略する(=66)。

        末尾(更新日時)を残したいので中間を削る。
        =96: Windowsでは font.measure 1回が遅い実機がある(=94/=95の
        スタックサンプルで起動時の主要コストと確定)ため、二分探索
        (毎回O(log n)回のmeasure)をやめ、**全体幅から平均文字幅を出して
        「残せる文字数」を推定し、その近傍だけ実測**する方式にした
        (典型3〜5回のmeasureで確定)。
        """
        try:
            if avail <= 0:
                return text
            full_w = font.measure(text)
            if full_w <= avail:
                return text
            ell = "…"
            ell_w = font.measure(ell)
            if ell_w > avail:
                return ""

            def cand(k):
                head = k // 2
                tail = k - head
                return (text[:head] + ell
                        + (text[len(text) - tail:] if tail else ""))

            # 平均文字幅から「残せる文字数」を推定
            avg = max(1.0, full_w / max(1, len(text)))
            k = max(0, min(len(text) - 1, int((avail - ell_w) / avg)))
            # 収まるまで縮める(推定が甘かった場合。1割+1文字ずつ)
            while k > 0 and font.measure(cand(k)) > avail:
                k = min(k - 1, int(k * 0.9))
            best = cand(max(k, 0))
            # 収まったら1文字ずつ伸ばして限界を探す(推定が固かった場合)
            while k < len(text) - 1:
                nxt = cand(k + 1)
                if font.measure(nxt) <= avail:
                    k += 1
                    best = nxt
                else:
                    break
            return best
        except Exception:
            return text

    def _fit_history_label(self, btn):
        """履歴行のラベルを現在のボタン幅に合わせて省略表示する(=66)。

        =82: `<Configure>` ハンドラの中で同期的に configure(text=) すると、
        テキスト変更が新たな <Configure> を**同期的に**誘発してスタックが
        育ち、Windows実機で RecursionError の嵐=起動不能になった。
        実処理は遅延実行へ逃がし(1ボタンにつき保留は1件)、
        イベントの入れ子を構造的に不可能にする。

        =96: after_idle → **after(60ms)** へ変更。after_idle だと起動時の
        最初の update()(CTkのmainloop冒頭)の中で全行ぶん同期実行されて
        初回描画をブロックする(=95後の実機スタックサンプルで確定)。
        60msのタイマーなら①初回描画が先に出る ②起動時の過渡的な
        <Configure>(幅が数回変わる)が1回の計算に集約される、の両得。
        """
        if getattr(btn, "_rvp_fit_pending", False):
            return
        btn._rvp_fit_pending = True
        try:
            self.root.after(60, lambda: self._do_fit_history_label(btn))
        except Exception:
            btn._rvp_fit_pending = False

    def _do_fit_history_label(self, btn):
        """省略表示の実体(after_idle経由でのみ呼ばれる)。

        起動直後はまだレイアウト前(幅1)なので何もしない。実際の幅は
        `<Configure>` で通知されるので、そのたびに計算し直す。
        同じ幅で二度計算しないようにして再入・ちらつきを防ぐ。
        """
        try:
            btn._rvp_fit_pending = False
            if not btn.winfo_exists():
                return
            w = btn.winfo_width()
            if w <= 20:
                return
            if getattr(btn, "_rvp_fit_w", None) == w:
                return
            btn._rvp_fit_w = w
            full = getattr(btn, "_rvp_full_text", "")
            btn.configure(text=self._ellipsize(full, btn.cget("font"), w - 16))
        except Exception:
            pass

    def _render_scenario_content(self, sc: Scenario):
        """シナリオの説明(detail)を表示する。

        イベント/ステート構成の一覧表示はユーザー要望で廃止(2026-07-16)。
        視聴前に見るのは説明文のみとし、構成の確認・変更は編集画面で行う。
        """
        text = sc.detail if sc.detail else tr("(このシナリオには説明がありません)")
        self.scenario_box.configure(state="normal")
        self.scenario_box.delete("1.0", "end")
        self.scenario_box.insert("1.0", text)
        self.scenario_box.configure(state="disabled")

    def _front_editor(self):
        """=152: 既に開いている編集画面があれば前面化して返す(無ければ None)。

        編集画面は**1つまで**(ユーザー決定)。2つ開くと、同じシナリオを別々に
        編集して後から保存したほうが勝つ=編集内容が黙って消える事故になる。
        破棄は winfo_exists() で検出するので、編集画面側に後始末は要らない
        (=118のヘルプ画面と同じ作法)。
        """
        win = getattr(self, "_editor_win", None)
        if win is None:
            return None
        try:
            if win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_set()
                return win
        except Exception:
            pass
        self._editor_win = None
        return None

    def pause_for_review(self) -> float:
        """編集画面のアイテムレビューが始まる前に本編を一時停止する(=164)。

        レビュー画面は pygame.mixer の別チャンネルで鳴らすので技術的には
        重ねられるが、音が混ざって確認にならないため止める(ユーザー決定)。
        戻り値=レビュー画面の初期音量(本編のマスター音量。0.0〜1.0)。
        """
        try:
            if self.player.state.get("status") == "playing":
                self.runner.submit(self.player.pause())
        except Exception:
            logger.exception("pause for review failed")
        try:
            return float(self.player.master_volume)
        except Exception:
            return 1.0

    def _open_editor(self, path):
        """編集画面を開く(既に開いていれば前面化するだけ)。=152"""
        win = self._front_editor()
        if win is not None:
            return win
        from ..editor import ScenarioEditor
        try:
            self._editor_win = ScenarioEditor(
                self.root, path,
                on_saved=self._on_editor_saved,
                review_host=self)
            return self._editor_win
        except Exception as e:
            self._editor_win = None
            messagebox.showerror(
                "RVP",
                tr('シナリオファイルの読み込みに失敗しました:\n{0}').format(e))
            return None

    def _on_editor_saved(self, path: str):
        """編集画面で保存されたときの後始末(=236)。

        従来どおり読み込み直した上で、**「最近のシナリオ」の先頭へ記録する**
        (=236 ユーザー要望)。=155 で記録の合図を「再生タブへ移った時」に
        一本化したが、**保存は「そのシナリオを触った」ことが確実**なので、
        並びが動いて困る場面ではない(新規作成した .json が一覧に出ない・
        編集したシナリオが先頭に来ない、というのが要望の理由)。

        記録の日時は**保存した時刻**になる(ユーザー決定)。`_record_history`
        が `_recorded_path` も更新するので、このあと再生タブへ移っても
        二重には記録されない。
        """
        self._load_scenario(path, goto_play=False)
        sc = self.scenario
        # 読み込みに失敗した場合(scenario=None)や、別のシナリオが載って
        # いる場合は記録しない。
        if sc is not None and sc.path \
                and os.path.abspath(sc.path) == os.path.abspath(path):
            self._record_history(sc)

    def on_edit_scenario(self):
        """選択中のシナリオを編集画面で開く(=152: 2つ目は開かない)。"""
        path = self.scenario.path if self.scenario else \
            getattr(self, "_failed_load_path", None)   # =284
        return self._open_editor(path)

    def on_new_scenario(self):
        """新しいシナリオを編集画面で作る(=45)。

        現在選択中のシナリオには影響しない(空の編集画面を開くだけ)。
        新規シナリオが名前を付けて保存されると on_saved 経由で読み込まれ、
        履歴にも記録される(タブは動かさない=編集作業の途中のため)。
        戻り値は編集画面インスタンス(テスト用)。
        =152: 編集画面が既に開いていれば、それを前面化するだけ(2つ開かない)。
        """
        return self._open_editor(None)
