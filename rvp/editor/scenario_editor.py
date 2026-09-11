"""シナリオ編集ウィンドウ本体(ScenarioEditor)。"""
from __future__ import annotations

import customtkinter as ctk
import json
import os
import tkinter as tk
from ._hooks import DND_FILES
from ..winstate import WindowMemory
from ..i18n import tr

from .common import (DEFAULT_EVENT, MUTED, TEXT_HEAD, TEXT_MUTED, VIDEO_EXTS,
    _front_window, _load_raw, _title_from_path, _toolbar_sep)
from .dialogs import BackgroundDialog
from .help import HelpDialog
from .review import ItemReviewDialog
from .se_bgm import _ScenarioEditorBgmMixin
from .se_correlation import _ScenarioEditorCorrelationMixin
from .se_events import _ScenarioEditorEventsMixin
from .se_history import _ScenarioEditorHistoryMixin
from .se_map import _ScenarioEditorMapMixin
from .se_messages import _ScenarioEditorMessagesMixin
from .se_next import _ScenarioEditorNextMixin
from .se_panel import _ScenarioEditorPanelMixin
from .se_save import _ScenarioEditorSaveMixin
from .se_states import _ScenarioEditorStatesMixin
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg


class ScenarioEditor(_ScenarioEditorMapMixin, _ScenarioEditorMessagesMixin, _ScenarioEditorHistoryMixin, _ScenarioEditorPanelMixin, _ScenarioEditorNextMixin, _ScenarioEditorCorrelationMixin, _ScenarioEditorStatesMixin, _ScenarioEditorEventsMixin, _ScenarioEditorBgmMixin, _ScenarioEditorSaveMixin, ctk.CTkToplevel):
    """シナリオ編集ウィンドウ。"""

    EV_END_CHOICES = (tr("合計N秒で次へ"), tr("合計N回の再生で次へ"), tr("N回のステート移行で次へ"))

    EV_END_COND = tr("変数条件で次へ")   # 変数宣言がある時だけ選択肢に追加(相関制御)

    EV_END_STATES = tr("指定ステートが終了したら次へ")   # 指定ステートの終了で終了

    # =168: 無限(イベント自身は終わらない)。ステート形式・通常の両方に追加。
    EV_END_INFINITE = tr("無限")   # =169: 「無限(終了しない)」から改称

    # ステート移行条件のコンボ。=165で並び順をユーザー指定の順へ変更し、
    # 「チャンネル時間でステート移行」(channel_time)を撤廃した(経過時間と
    # 役割が重複するため)。「判定式」は変数宣言がある時だけ末尾へ追加する。
    TRANS_CHOICES = (tr("経過時間でステート移行"),
                     tr("指定チャンネル終了でステート移行"),
                     tr("全チャンネル終了でステート移行"),
                     tr("チャンネル回数でステート移行"),
                     tr("選択肢でステート移行"),   # =275
                     tr("ステート移行なし"))

    TRANS_TARGET_COLS = 3   # =287: 移行先チェックの折返し列数

    TRANS_COND = tr("判定式でステート移行")

    TRANS_CHOICE = tr("選択肢でステート移行")   # =275: 選ばれた選択肢で移行先が決まる   # 変数宣言がある時だけ選択肢に追加(相関制御)

    TRANS_NONE = tr("ステート移行なし")

    # =62: ステートに入ってからの経過時間。チャンネルを見ないので
    # 音声なし(有効ch0)ステートでも使える=無音待機ノード。
    TRANS_STATE_TIME = tr("経過時間でステート移行")

    TRANS_ALL_CH = tr("全チャンネル終了でステート移行")   # 全chが自然終了したら移行

    TRANS_CHANNEL_END = tr("指定チャンネル終了でステート移行")  # 指定chの終了で移行

    TRANS_CHANNEL_COUNT = tr("チャンネル回数でステート移行")

    # イベントの遷移方法(「次のイベント:」のコンボ)。=166で「固定」と「なし」を追加。
    # 「固定」= 1つのイベントへ必ず進む(JSONは "next": "eventB" の文字列形式)。
    # 「なし」= 遷移しない(JSONは "next": null)。
    # 「変数分岐」「数値入力」は変数宣言があるときだけ出す(相関制御)。
    NEXT_FIXED = tr("固定")

    NEXT_BRANCH = tr("分岐")

    NEXT_CHOICE = tr("選択肢")

    NEXT_COND = tr("変数分岐")

    NEXT_INPUT = tr("数値入力")

    NEXT_NONE = tr("なし")

    # イベント終了条件(■イベント枠のコンボ)
    EVEND_ALL = tr("全チャンネルが終了した時")

    # =52: 動画はチャンネルのアイテムになったので「動画が終わった時」は
    # 「指定チャンネルが終了した時」(動画chを指定)へ一本化した。
    EVEND_CHANNEL = tr("指定チャンネルが終了した時")

    EVEND_DURATION = tr("合計時間が経過した時")   # イベント開始からの累積秒。全ch無限を許可(相関制御)

    EVEND_COND = tr("変数条件が成立した時")

    EVEND_STATE = tr("ステートのイベント終了に委譲")

    # =169: 「無限(終了しない)」→「無限」(ユーザー決定)。出口が
    # 選択肢・数値入力・判定式(常に監視)でもあるため、「終了しない」
    # という言い方が実際の挙動と合わなくなるため。
    EVEND_INFINITE = tr("無限")   # =168

    # 選択肢/数値入力の表示タイミング。=168: 「無限」では1つ目が永久に来ない
    SHOW_TIMES = (tr("イベント終了条件の達成時"), tr("イベント開始時"),
                  tr("イベント開始から指定時間後"))

    # =168: 終了条件が「無限」のときの遷移方法。判定式は**再生中に常時監視**
    # する別モードなので名前を分ける(ユーザー命名)。
    NEXT_COND_WATCH = tr("判定式(常に監視)")

    # =256: BGMの3択(引き継ぐ/指定/オフ)。既定は「引き継ぐ」= JSONキー省略
    BGM_INHERIT = tr("前のBGMを引き継ぐ")

    BGM_SET = tr("BGMを指定")

    BGM_OFF = tr("BGMオフ")

    def __init__(self, master, path: str | None, on_saved=None,
                 review_host=None):
        super().__init__(master)
        self.title(tr("シナリオ編集 - RVP"))
        self.minsize(920, 780)
        # =164: アイテムレビュー画面。同時に1つまで(開いていれば差し替え)。
        # review_host は本編の再生を止めて初期音量をもらう相手(main.RVPApp)。
        # テストから直接生成する経路では None(その場合は何もしない)。
        self._review_dlg = None
        self.review_host = review_host
        # =115: 前回閉じたときのサイズ・位置・最大化を復元する。
        # 保存が無い/どのモニタにも載っていない場合だけ従来の既定配置
        # (左上0,0に WIN_W×WIN_H)。
        self.winmem = WindowMemory(self, "editor")
        self._winmem_restored = self.winmem.restore()
        if not self._winmem_restored:
            self._apply_window_size()
        self.winmem.watch()
        self.winmem.install_close_hook()
        self.on_saved = on_saved

        self.path = path
        if path:
            self.base_dir = os.path.dirname(os.path.abspath(path))
            try:
                self.data = _load_raw(path)
            except Exception:
                # 読み込み不可(アクセス拒否・破損等)。作りかけの空ウィンドウを
                # 残さないよう自身を破棄してから呼び出し元へ伝える
                # (main.on_edit_scenario がcatchしてエラーダイアログを出す)。
                self.destroy()
                raise
            # タイトルは常にJSONファイル名に追従させる(ファイル名が正)
            self.data["title"] = _title_from_path(path)
        else:
            self.base_dir = os.getcwd()
            # 未保存。タイトルは初回保存時にファイル名から確定する。
            # =252: 新規シナリオは「デバイス連動:OFF」が既定(ユーザー決定)。
            self.data = {"title": "", "device_enabled": False,
                         "start": "event1",
                         "events": {"event1": json.loads(json.dumps(DEFAULT_EVENT))}}

        # =252: デバイス連動フラグ(JSONトップレベル "device_enabled")。
        # キー省略=ON(旧シナリオは従来どおり)。OFFはデバイス関連UIを隠し、
        # 再生時の駆動も止める。JSON上の紐づけ・担当は**保持**される
        # (UIは非表示にするだけで、裏ではデータを読み込んだまま)。
        self.device_enabled = bool(self.data.get("device_enabled", True))
        # =256: BGM機能フラグ(JSONトップレベル "bgm_enabled")。キー省略=OFF
        # (BGMを持つ旧シナリオは存在しないため device_enabled とは逆の既定)。
        # OFFはBGMブロックを隠すだけで、各ノードの "bgm" 定義は保持される。
        self.bgm_enabled = bool(self.data.get("bgm_enabled", False))
        # =250: 紹介文(旧・説明欄)。常設テキストボックスを廃止し、ダイアログ
        # 経由でこの変数に保持する。ファイルへは _do_save で書き出す。
        self._detail_text = str(self.data.get("detail", "") or "")

        self.selected: str | None = None
        self.sel_state: str | None = None   # 編集中のステートID(ステート形式のみ)
        # =277: UNDO/REDO(スナップショット履歴)。_hist_reset() で起点を作る
        # までは _hist_last=None でチェックポイントは何もしない
        self._hist_undo: list = []
        self._hist_redo: list = []
        self._hist_last: dict | None = None
        self._hist_suspend = False
        # =270: ファイルダイアログの前回フォルダは設定ファイルへ永続化する
        # 方式になった(_dialog_initialdir / _remember_dialog_dir)。
        # ウィンドウ属性(_last_audio_dir 等)への一時記憶は廃止。

        self._build_ui()
        self._apply_device_enabled()   # =252: OFFで開いたシナリオへ即反映
        self._apply_bgm_enabled()      # =256: 同上(BGM)
        self._init_map_toggle()
        self._init_map_undock()        # =300
        self._redraw_canvas()
        # =64(体感の起動短縮): ここまでで骨組み(ツリー・図・パネルの枠)は
        # 揃っているので、**先にウィンドウを見せてから**中身(選択イベントの
        # パネル)を作る。CTkToplevel は生成時に withdraw して約200ms後に
        # deiconify するため、__init__ を最後まで回してから表示すると
        # 「押しても何も出ない時間」がそのぶん延びる。
        try:
            self.deiconify()
            self.update()
        except Exception:
            pass
        first = self.data.get("start") or (next(iter(self.data["events"]), None))
        if first in self.data["events"]:
            self._select(first)
        # =277: ここまでの読み込み・初期選択は履歴に含めない(起点にする)
        self._hist_reset()

        _front_window(self)
        # CTkToplevelは内部でwithdraw→約200ms後にdeiconifyするため、その際に
        # ジオメトリが解除されることがある。遅延して再適用する(=58で
        # 最大化をやめ固定サイズ+左上0,0にしたので、その再適用に変わった)。
        for delay in (220, 620):
            try:
                self.after(delay, self._apply_window_size)
            except Exception:
                pass

        # OSからのファイルD&D(=44)。編集ウィンドウ全体を単一のドロップ先として
        # 登録し、落下点の座標からアイテム枠/チャンネル枠を特定して振り分ける
        # (tkdndは落下点直下から親方向へ登録済みウィジェットを探すため、
        # 動的に増えるアイテム行も個別登録なしで受けられる)。
        # tkinterdnd2 未導入・ロード失敗時は静かに無効(他は従来どおり)。
        self._dnd_ok = False
        if _pkg().TkinterDnD is not None:
            try:
                _pkg().TkinterDnD._require(self)
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_dnd_drop)
                self._dnd_ok = True
            except Exception:
                self._dnd_ok = False
        # =160: この初期化は _build_ui より後なので、既に組み上がっている
        # チャンネル枠の「(D&D可)」を結果に合わせて出し入れする
        # (以降に作り直されるパネルは build 時に _dnd_ok を見る)。
        self._refresh_dnd_hints()

    def _refresh_dnd_hints(self):
        """全チャンネル枠の「(D&D可)」を `_dnd_ok` に合わせる(=160)。"""
        for sec in getattr(self, "channel_sections", {}).values():
            fn = getattr(sec, "set_dnd_hint", None)
            if callable(fn):
                fn(self._dnd_ok)

    def _dnd_hit(self, x, y):
        """落下点(スクリーン座標)に当たるチャンネル枠/アイテム枠/動画行を返す。

        winfo_containing は X サーバ/重なり順に依存して None を返すことが
        あるため、winfo_rootx/rooty+width/height の矩形判定で決定的に解決する
        (Windows実機のtkdnd %X %Y も同じスクリーン座標系)。表示中(ismapped)
        のウィジェットのみ対象。
        戻り値 (ChannelSection|None, ItemRow|None, 動画行の上か)。
        """
        def inside(w):
            try:
                if w is None or not w.winfo_ismapped():
                    return False
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                return (wx <= x < wx + w.winfo_width()
                        and wy <= y < wy + w.winfo_height())
            except Exception:
                return False

        for sec in self.channel_sections.values():
            if inside(sec):
                for r in sec.rows:
                    if inside(r):
                        return sec, r
                return sec, None
        return None, None

    def _on_dnd_drop(self, event):
        """OSからのファイルD&D(=44/動画対応=50/=52でch内へ)の振り分け。

        落下点の矩形判定でアイテム枠(ItemRow)・チャンネル枠
        (ChannelSection)を特定して振り分ける:
          - 音声(.wav/.mp3): チャンネル枠へ落とすとアイテム追加
            (アイテム枠の上に落ちてもチャンネルへの追加扱い)
          - 動画(.mp4等): チャンネル枠へ落とすと動画アイテムを追加する
            (=52。動画はチャンネルの中身になったため、音声と同じ扱い。
            他chが動画chのとき・音声/スクリプトのあるchでは無視される)
          - .funscript/.csv: アイテム枠の上ならそのアイテムへ手動紐づけ
            (動画アイテムの上なら動画のトラックになる)。=278: 手動の
            トラック行(linear/twist/…)の上に落とせば、ファイル名タグに
            よらずその行の種別へ紐づける。**アイテム枠の外
            (チャンネル枠)ならスクリプトのみアイテムを追加**(=65。
            「＋スクリプト」と同じ。音声/動画のあるchでは無視される)
        対象外拡張子・落下先なし・失敗はすべて黙って無視(仕様=失敗時は
        画面上何も起こさない)。
        """
        try:
            paths = list(self.tk.splitlist(event.data))
            x, y = int(event.x_root), int(event.y_root)
        except Exception:
            return
        sec, row = self._dnd_hit(x, y)
        audio = [p for p in paths
                 if os.path.splitext(p)[1].lower() in (".wav", ".mp3")]
        fs = [p for p in paths
              if os.path.splitext(p)[1].lower() in (".funscript", ".csv")]
        # =252: デバイス連動OFFではスクリプトのD&Dを無効化(AQ8。音声・動画は
        # 従来どおり)。失敗は黙って無視する既存仕様に合わせて何も出さない。
        if not self.device_enabled:
            fs = []
        video = [p for p in paths
                 if os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        try:
            if video and sec is not None and self.selected:
                # 動画chは1つだけ(他chが動画chなら _video_lock で弾く)
                if not getattr(sec, "_video_lock", False):
                    sec.add_dropped_video(video)
            if audio and sec is not None:
                sec.add_dropped_audio(audio)
            elif audio and self.bgm_enabled and self._bgm_hit(x, y):
                # =256: BGMブロックへ落とした音声はBGMアイテムとして追加
                # (BGM:OFFのときは受けない=デバイスOFFのfs D&Dと同じ流儀)
                self._bgm_dropped_audio(audio)
            if fs and row is not None:
                # =278: トラック行の上ならその種別へ(タグ規則より優先)
                ttype = row.dropped_track_hit(x, y)
                for p in fs:
                    row.bind_dropped_fs(p, track_type=ttype)
            elif fs and sec is not None:
                # アイテム枠の外=チャンネル枠へ落とした(=65)。
                # スクリプト専用チャンネルの素材として追加する。
                sec.add_dropped_script(fs)
        except Exception:
            pass

    # イベント遷移図の高さ(=58で170→110。1600x900のデスクトップ対応)
    CANVAS_H = 110

    # 初期サイズと位置(=58 ユーザー決定)。以前は最大化していたが、
    # 1600x900のデスクトップでは画面を占有しすぎるため固定サイズにした。
    # メインウィンドウ(690x820・左上0,0)の右側に並べられる幅にしてある。
    WIN_W, WIN_H = 1360, 820

    MAP_CFG_KEY = "editor_map_open"

    # =72: 「図の高さに合わせる」(自動フィット)の保存キー
    MAP_FIT_CFG_KEY = "editor_map_fit"

    # ---- =300: イベント遷移図のドッキング解除(別ウィンドウ) ----
    #
    # 2段目バー(イベント追加〜インポート・元に戻す/やり直す・配置)と図だけを
    # 別ウィンドウへ出す。タイトル行・イベントのパラメータ・ステート図・
    # チャンネルは編集画面に残る(ユーザー決定)。解除中の編集画面側は
    # 2段目バーごと消す。折りたたみ/自動フィット/取っ手は別ウィンドウには
    # 無い。topmost にはしない(独立して動かせ、最小化もできる)。解除状態と
    # ウィンドウの位置・大きさはコンフィグに記憶(次回も同じ状態で開く)。
    MAP_UNDOCK_CFG_KEY = "editor_map_undocked"

    MAP_WIN_KEY = "editor_map"           # winstate の保存キー

    MAP_WIN_W, MAP_WIN_H = 800, 500      # 別ウィンドウの既定サイズ

    def destroy(self):
        # =300: 別ウィンドウの位置・大きさを保存してから閉じる
        mem = getattr(self, "map_winmem", None)
        if mem is not None:
            try:
                mem.save_now()
            except Exception:
                pass
        super().destroy()

    # =285: 図の高さの天井は**ウィンドウ高さの80%**(図の枠の下端がウィンドウ
    # の80%位置を越えない=下の20%はイベント/チャンネル欄に必ず残す。ユーザー
    # 決定)。自動フィット・手動リサイズの両方に効く。
    MAP_MAX_RATIO = 0.8

    MAP_MIN_H = 60

    MAP_H_CFG_KEY = "editor_map_height"   # =285: 手動リサイズ後の高さ

    def _apply_window_size(self):
        """左上(0,0)に WIN_W×WIN_H で開く(画面が小さければ画面内に収める)。

        =115: 前回の配置を復元したときは**その矩形**を再適用する
        (CTkToplevelのwithdraw→deiconifyでジオメトリが解除されることへの
        対策=下の遅延呼び出しの用途は同じ)。
        """
        if getattr(self, "_winmem_restored", False):
            if self.winmem.reapply():
                return
        w, h = self.WIN_W, self.WIN_H
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            # 画面より大きい場合だけ縮める(はみ出して操作できなくなるのを防ぐ)
            w = min(w, sw)
            h = min(h, max(600, sh - 40))
        except Exception:
            pass
        self.geometry(f"{w}x{h}+0+0")

    def _title_display(self) -> str:
        """タイトル欄に表示する文字列。保存済み=ファイル名、未保存=プレースホルダ。"""
        return _title_from_path(self.path) if self.path else tr("(未保存)")

    def _refresh_title_display(self):
        """タイトル欄の表示を現在のパスに合わせて更新する(読み取り専用欄)。"""
        if hasattr(self, "title_var"):
            self.title_var.set(self._title_display())

    def _build_ui(self):
        # =255: 2段構成(実機レビューでユーザー指定・お絵かき添付):
        #   1段目(head_bar) = [シナリオタイトル(大)] …右寄せで
        #     [デバイス連動：ON/OFF][BGM：ON/OFF][変数/監視][紹介文]｜
        #     [名前を付けて保存][保存]
        #     (=256: BGMトグルを変数/監視の左へ追加し、デバイス連動の右の
        #      区切り線(旧head_sep1)は廃止=ユーザー指定)
        #   2段目(bar)      = [＋イベント追加][イベント削除][イベントコピー]｜
        #     [インポート] [▼ 折りたたみ] [図の高さに合わせる]
        # 「タイトル=画面の見出しが最上段」「シナリオ全体にかかる道具は
        # タイトルと同じ列」「イベント操作は対象(イベント遷移図)のすぐ上」。
        head_bar = ctk.CTkFrame(self, fg_color="transparent")
        head_bar.pack(fill="x", padx=14, pady=(12, 4))
        self.head_bar = head_bar

        # =251: タイトルはファイル名に追従するラベル表示(欄は撤去済み)。
        self.title_var = tk.StringVar(value=self._title_display())

        # 右寄せ群は side="right" で右端から順に置く(視覚順は左→右で
        # デバイス連動｜変数/監視 紹介文｜名前を付けて保存 保存)。
        ctk.CTkButton(head_bar, text=tr("保存"), width=90, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=self._save).pack(side="right", padx=4)
        ctk.CTkButton(head_bar, text=tr("名前を付けて保存"), width=130,
                      height=30,
                      fg_color="transparent", border_width=1, border_color=MUTED,
                      text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._save_as).pack(side="right", padx=4)
        self.head_sep2 = _toolbar_sep(head_bar, side="right")
        self.detail_btn = ctk.CTkButton(
            head_bar, text=tr("紹介文"), width=90, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._open_detail_dialog)
        self.detail_btn.pack(side="right", padx=4)
        self.vars_btn = ctk.CTkButton(
            head_bar, text=tr("変数/監視"), width=100, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._open_vars_dialog)
        self.vars_btn.pack(side="right", padx=4)
        self._update_vars_btn()
        # =262: 背景イラスト(「BGM」トグルの右隣=ユーザー指定Q7)。押すと
        # 画像の選択・クリア・暗さ(%)のダイアログを開く。
        self.bg_btn = ctk.CTkButton(
            head_bar, text=tr("背景"), width=84, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._open_background_dialog)
        self.bg_btn.pack(side="right", padx=4)
        # =256: BGMトグル(「変数/監視」の左隣)。デバイス連動の右隣にあった
        # 区切り線(旧head_sep1)は廃止(ユーザー指定=トグル2つ+設定系を
        # 同じグループにする)。
        self.bgm_toggle_btn = ctk.CTkButton(
            head_bar, text=self._bgm_btn_text(), width=100, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._toggle_bgm_enabled)
        self.bgm_toggle_btn.pack(side="right", padx=4)
        self.device_toggle_btn = ctk.CTkButton(
            head_bar, text=self._device_btn_text(), width=140, height=30,
            fg_color="transparent", border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"), hover_color=("gray85", "gray25"),
            command=self._toggle_device_enabled)
        self.device_toggle_btn.pack(side="right", padx=4)
        # タイトルは残り幅いっぱい(長いファイル名は右側ボタンの手前で切れる)
        self.title_label = ctk.CTkLabel(
            head_bar, textvariable=self.title_var,
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT_HEAD, anchor="w")
        self.title_label.pack(side="left", fill="x", expand=True)
        # =118: ヘルプボタンは**メイン画面のヘッダー(「設定」の左)へ移動**した
        # (ユーザー決定。ヘルプは編集画面から独立=視聴だけの人にも届く場所へ)。
        # 編集画面からは開けなくなったが、`_open_help()` は互換のため残す。

        self._init_map_manual_height()
        self._map_undocked = False
        self.map_win = None
        self.map_winmem = None
        self._build_map_area(self, docked=True)
        # 画面内メッセージ/エラー領域(モーダルダイアログ+警告音の代替)。
        # 先に生成しておき、パネルより下(side=bottom)へ差し込む。
        self._build_msg_area()

        # イベント編集パネル(スクロール)。
        # =58: CTkScrollableFrame は pack/winfo_* が内側フレームに委譲される
        # ため、pack(before=...) の基準にできない。素の CTkFrame で包んで、
        # メッセージ領域のパック順の基準(_pack_msg_area)にする。
        self.panel_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self.panel_wrap.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self.panel = ctk.CTkScrollableFrame(self.panel_wrap, corner_radius=10)
        self.panel.pack(fill="both", expand=True)

        # 描画中インジケータ。パネル再構築(音声行の作り直し)は重く数秒かかる
        # ことがあるため、その間パネルを隠して「描画中…」を出す(中途半端な
        # リアルタイム描画も抑止し、完成後に一括表示)。
        self._rendering = False
        self.rendering_label = ctk.CTkLabel(
            self.panel_wrap, text=tr("描画中…"),
            font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_MUTED)

        self._build_panel_widgets()

    _EDIT_WIDGET_CLASSES = ("CTkOptionMenu", "CTkComboBox", "CTkCheckBox",
                            "CTkSwitch", "CTkRadioButton", "CTkSlider",
                            "CTkSegmentedButton", "VarRefField")

    def _open_help(self):
        """ヘルプを開く(=118でボタンはメイン画面へ移動。互換用に残置)。

        既に開いていれば前面へ出すだけ(1つに限定)。
        """
        dlg = getattr(self, "_help_dlg", None)
        if dlg is not None and dlg.winfo_exists():
            _front_window(dlg)
            return
        self._help_dlg = HelpDialog(self)

    def _mpv_missing_popup(self):
        """動画レビューに mpv が無いときの案内(=203)。"""
        win = ctk.CTkToplevel(self)
        win.title("RVP")
        ctk.CTkLabel(
            win, justify="left",
            text=tr("動画のレビューには mpv が必要です。\n"
                    "メイン画面の Settings で mpv のパスを指定してください"),
            font=ctk.CTkFont(size=13)).pack(padx=24, pady=(20, 12))
        ctk.CTkButton(win, text="OK", width=90, height=30,
                      fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER,
                      command=win.destroy).pack(pady=(0, 16))
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

    def open_item_review(self, row) -> "ItemReviewDialog":
        """アイテムレビュー画面を開く(=164)。

        **同時に開けるのは1つ**(ユーザー決定)。既に開いていれば、その窓の
        中身を新しいアイテムへ差し替えて前面化する(2つ目は作らない)。
        破棄の検出は winfo_exists() なので、閉じる側に後始末は要らない
        (=152の編集画面・=118のヘルプと同じ作法)。
        """
        # =203: 動画アイテムは mpv が必須。見つからなければ開かずに案内する
        if getattr(row, "is_video", False) and _pkg()._mpv_path_setting() is None:
            self._mpv_missing_popup()
            return None
        spec = row.review_spec()
        dlg = self._review_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    # =170: 編集モードで未保存なら確認が挟まり、「やめる」で
                    # 差し替えを中止する(load が False を返す)。
                    if dlg.load(spec, row=row):
                        dlg.front()
                    return dlg
            except Exception:
                pass
            self._review_dlg = None
        self._review_dlg = ItemReviewDialog(self, spec,
                                            host=self.review_host,
                                            owner=self, row=row)
        return self._review_dlg

    def _open_detail_dialog(self):
        """紹介文の編集ダイアログを開く(モーダル)。

        「保存」はメモリ(_detail_text)への反映のみ(AQ1)。JSONファイルへは
        従来どおり編集画面の「保存」で書き出す。キャンセル/✕は破棄。
        """
        dlg = _pkg().DetailDialog(self, self._detail_text)   # =277: 結果は履歴対象
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._detail_text = dlg.result

    def _open_background_dialog(self):
        """背景イラスト(トップレベル "background")の設定ダイアログを開く。

        「保存」はメモリ(self.data)への反映のみ。JSONファイルへは編集画面の
        「保存」で書き出す(紹介文と同じ作法)。パスは絶対で保持し、保存時の
        _rebased_data_for_save が保存先基準の相対パスへ付け替える(保存先の
        外にある画像は外部素材警告・素材コピーの対象=_map_item_paths)。
        """
        dlg = BackgroundDialog(self, self.data.get("background"),
                               self.base_dir)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        bg = dlg.result.get("background")
        if bg is None:
            self.data.pop("background", None)
        else:
            # 相対で保持されていた既存パスと同様、self.data は base_dir
            # 基準。ダイアログは絶対パスを返すのでそのまま格納してよい
            # (_rebase_scenario_path が isabs を処理する)。
            self.data["background"] = bg

    def refresh_theme(self):
        """=151: メイン画面でテーマが切り替わったときに呼ばれる。

        CTkウィジェットは apptheme.switch() が塗り直すが、素の tk.Canvas
        (イベント遷移図・ステート図)は自前描画なので描き直す必要がある。
        配布元は main.RVPApp._refresh_theme_widgets(親の子ウィンドウを
        走査して refresh_theme を持つものへ配る)。
        """
        for fn in (getattr(self, "_redraw_canvas", None),
                   getattr(self, "_redraw_state_canvas", None)):
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                # 図が描き直せなくても編集内容には影響しないので握りつぶす
                # (次の操作で描き直される)
                pass
        # =164: 開いているレビュー画面のグラフも自前描画なので配る
        dlg = getattr(self, "_review_dlg", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.refresh_theme()
            except Exception:
                pass

    HIST_MAX = 100

    # =287: ステート形式のイベント終了条件/ステート移行の既定値(ユーザー決定)
    #   合計N回の再生で次へ=5回 / 合計N秒で次へ=60〜60秒 /
    #   N回のステート移行で次へ=5回 / チャンネル回数でステート移行=5〜5回
    # コンボで選んだとき、数値欄が空なら既定値を入れる(入力済み・変数参照は
    # 触らない)。JSONからの読み込みは保存値をそのまま出す。
    EV_END_DEFAULTS = {"plays": "5", "transitions": "5", "duration": ("60", "60")}

    TRANS_COUNT_DEFAULT = ("5", "5")

    # =167(a): ステートの自動接続で入れる移行条件の既定。
    # 「全チャンネル終了」ではなく**経過時間**を使う(ユーザー決定 2026-08-16)。
    # 理由: ステート内チャンネルの終了条件は既定が「無限」で、その場合
    # 「全チャンネル終了」は永久に成立しないため保存エラーになる
    # (=追加した直後から保存できないシナリオになってしまう)。経過時間は
    # チャンネルを見ないので、どんなステートでも必ず有効な条件になる。
    AUTO_TRANS_SECONDS = 60
