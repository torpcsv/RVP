"""アイテムレビュー: 編集モード(構築・対象トラック・種別・サブ表示・数値欄・保存・未保存確認)(mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import time
import tkinter as tk
import warnings
from .. import appfont, winstate
from ..i18n import load_config, save_config, tr

from .common import (BOX_BG, BOX_BORDER, CTkOptionMenu, MSG_ERROR, MSG_OK,
    MSG_WARN, MUTED, TEXT_MUTED, _load_dialog_dir, _place_popup,
    _remember_dialog_dir)
from .fields import Tooltip
from .paths import _safe_relpath
from .review_support import (SCRIPT_EDIT_CSV_TYPES, SCRIPT_EDIT_NEW_KINDS,
    SCRIPT_EDIT_STEP_TYPES, SCRIPT_EDIT_TAGS, SCRIPT_EDIT_TYPES,
    WAVE_MODE_KEYS, _is_csv)
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照
from ._hooks import _pkg



class _FkChain:
    """=312: F キー長押しの数珠つなぎ(=222)の状態。打点先(左/右)ごとに1つ。"""
    __slots__ = ("held", "chain_end", "job", "release_job")

    def __init__(self):
        self.held = None          # 押しっぱなし中の F キー
        self.chain_end = None     # 次に置く起点(直前パターンの終端)
        self.job = None           # 終端監視の after id
        self.release_job = None   # KeyRelease の確定待ち(自動リピート)

class _ItemReviewEditMixin:
    """ItemReviewDialog の mixin(=301 分割)。編集モード(構築・対象トラック・種別・サブ表示・数値欄・保存・未保存確認)"""

    def _toggle_edit_mode(self):
        if self.edit_mode:
            self._return_to_review()
        else:
            self._enter_edit_mode()

    def _enter_edit_mode(self):
        self.pause()
        if not self._edit_built:
            self._build_edit_ui()
        # 編集対象候補 = spec.tracks のうち編集できる種別(仕様 4.1。
        # 自動モードの解決結果も review_spec 経由で含まれている)。
        # 同じ (種別, パス) の重複は除く。
        # =224: csv(ROTATE)は **チャンネルごとに1候補**(5列=左/右の2つ)。
        # 候補は (種別, パス, lo, hi, ch) の5つ組。ch=None は funscript。
        # =225: rotate系・vibration の funscript も候補に入る(階段で編集)。
        seen = set()
        self._edit_tracks = []
        for ttype, path, lo, hi in self.spec.get("tracks") or []:
            csv = ttype in SCRIPT_EDIT_CSV_TYPES and _is_csv(path)
            if ttype not in SCRIPT_EDIT_TYPES and not csv:
                continue
            if not csv and _is_csv(path):
                continue          # 保険: rotate 以外の csv は扱わない
            key = (ttype, os.path.normcase(os.path.abspath(path)))
            if key in seen:
                continue
            seen.add(key)
            if not csv:
                self._edit_tracks.append((ttype, path, lo, hi, None))
                continue
            # =232: **5列(左右独立)は1項目にまとめ、開いたら左右2本を
            # 同時に編集する**(=224 の「（左）」「（右）」に分ける方式は廃止)
            ch = 0
            try:
                from .. import script_edit as _se
                cols, _pts, allp = _se.load_csv_points(path, 0)
                ch = (0, 1) if cols == 5 else 0
                del allp
            except Exception:
                ch = 0           # 壊れた csv も候補には出す(開いた時に警告)
            self._edit_tracks.append((ttype, path, lo, hi, ch))
        self.edit_mode = True
        self.edit_btn.configure(text=tr("レビューに戻る"))
        # レビューのグラフを隠し、編集モードの部品を「閉じる」行の前へ差し込む
        # (pack_forget は pack 順を失うので before= で入れる=仕様 7.3)
        self.graph_box.pack_forget()
        # =219: ボタン行(side="bottom")より後ろへ詰める=縮むのはこちら側
        self.edit_wrap.pack(fill="both", expand=True, padx=14, pady=(8, 0))
        # トラックコンボを組み立てる
        # =227: 末尾に必ず「(新規作成)」を足す(別の種別のトラックを
        # あとから足せるようにする=要望5)
        if self._edit_tracks:
            # =234(要望2): 一覧に「(新規作成)」は並べない。新規作成へ入る
            # 導線は **「＋新規」ボタンだけ**(押したときだけコンボの表示が
            # 「(新規作成)」になる)。候補が0本のときは従来どおり。
            values = [self._edit_track_label(i)
                      for i in range(len(self._edit_tracks))]
            self.edit_type_menu.pack_forget()
            self._edit_new = False
        else:
            values = [tr("(新規作成)")]
            self.edit_type_menu.pack(side="left", padx=(6, 0))
            self._edit_new = True
        self.edit_track_menu.configure(values=values)
        self.edit_track_var.set(values[0])
        # 素材全体の音声(区間を無視=仕様 4.2/10)
        self._edit_sound = None
        audio = self.spec.get("audio") or ""
        edit_warns = []
        if audio:
            snd, err = self._load_audio(audio, 0.0, None)
            if err:
                edit_warns.append(err)
            else:
                self._edit_sound = snd
        self._edit_warn_base = edit_warns
        self._start_wave_env()           # =243: 編集グラフへも波形を配る
        self._edit_load_track(0 if self._edit_tracks else -1)
        self._apply_edit_layout()

    def _leave_edit_ui(self):
        """編集モードのUIを畳む(確認は済んでいる前提)。"""
        self.pause()
        self._save_edit_height()          # =211
        self.edit_mode = False
        self._edit_sound = None
        self.edit_btn.configure(text=tr("スクリプト編集"))
        if self._edit_built:
            self._chain_stop()            # =222: 数珠つなぎの監視を止める
            self._show_sub(-1)            # =227: サブ表示も畳む
            self._set_edit_pair(False)    # =232: 右(ロータ2)も畳む
            self.edit_wrap.pack_forget()

    def _return_to_review(self):
        """「レビューに戻る」。未保存確認→レビュー内容を読み直す。"""
        if not self._resolve_unsaved():
            return
        self._leave_edit_ui()
        # 新規保存でトラック構成が変わっている可能性があるため、行が
        # 生きていれば spec を取り直す(仕様 11)。自動再生はしない。
        spec = self.spec
        row = self._row
        if row is not None:
            try:
                if row.winfo_exists() and \
                        getattr(row, "audio_rel", None) == self._row_audio_rel:
                    spec = row.review_spec()
            except Exception:
                pass
        self.load(spec, autoplay=False)

    @staticmethod
    def _new_kind_of(label: str):
        """新規作成コンボの表示名 → (種別, kind, 列数)。"""
        for name, spec in SCRIPT_EDIT_NEW_KINDS:
            if name == label:
                return spec
        return ("linear", "funscript", 0)

    def _pat_mode(self) -> str:
        """=226: パターンの考え方(linear / rotate / vib)。

        linear/twist = 従来どおり(クリックした高さへ置ける・接続・追従あり)。
        rotate(csv も funscript も)= 定義どおりの高さで固定・中心 50。
        vib = 定義どおりの高さで固定・基準 0。
        """
        return self._pat_mode_of(self._edit_type)

    @staticmethod
    def _pat_mode_of(ttype: str) -> str:
        from .. import script_edit
        if ttype in SCRIPT_EDIT_CSV_TYPES:
            return script_edit.PAT_MODE_ROTATE
        if ttype == "vibration":
            return script_edit.PAT_MODE_VIB
        return script_edit.PAT_MODE_LINEAR

    def _edit_pos_max(self) -> int:
        """=297: 編集モデルの pos 分解能。csv=200(速度 1 刻み) / それ以外 100。"""
        from .. import script_edit
        return script_edit.CSV_POS_MAX \
            if getattr(self, "_edit_kind", "funscript") == "csv" else 100

    def _pat_center_scaled(self, mode: str):
        """=297: PAT_CENTERS(0〜100 定義)を編集モデルの分解能へ換算した中心。
        ROTATE: funscript=50 / csv=100。VIBRATION=0。linear/twist=None。"""
        from .. import script_edit
        c = script_edit.PAT_CENTERS.get(mode)
        if c is None:
            return None
        return int(round(c * self._edit_pos_max() / 100.0))

    def _std_patterns(self) -> tuple:
        """いま使う標準パターンのカタログ(=226。モードで切り替わる)。"""
        from .. import script_edit
        return script_edit.std_patterns(self._pat_mode())

    def _apply_edit_kind(self):
        """編集中の種類(funscript / csv)に合わせて画面の作法を切り替える。

        csv は **階段(次の点まで同じ値)**・**時刻は100ms単位**・
        **ヒートマップなし**(速度そのものを描いているため)。
        パターン内側の点もグリッドへ丸める(100ms格子に乗せるため)。
        """
        from .. import script_edit
        csv = getattr(self, "_edit_kind", "funscript") == "csv"
        # =225: rotate系・vibration の funscript も**階段**で描く
        # (次の指示まで値を保つ=csv と同じ意味づけ)。
        step = csv or self._edit_type in SCRIPT_EDIT_STEP_TYPES
        for g in self.edit_graphs:            # =232: 左右2本とも同じ作法
            g.step = step
            # 速度そのもの・強さそのものを描いているので、ストローク速度の
            # 警告(ヒートマップ)は linear/twist のときだけ意味を持つ
            g.heat = not step
            g.round_interior = csv
        mode = self._pat_mode()
        # =297: csv は分解能 200(中心 100)。パターンの中心も分解能に合わせる
        center = self._pat_center_scaled(mode)
        for m in self.edit_models:
            m.pos_max = self._edit_pos_max()
            m.round_interior = csv
            m.step_mode = step
            m.pat_center = center
        g = self.edit_graph
        # =226: 離散的なスクリプトのパターン規則
        #  ・定義どおりの高さで固定(ROTATE=中心50(csv は 100) / VIBRATION=基準0)
        #  ・接続配置なし / 隣接パターンの追従なし
        for g2 in self.edit_graphs:
            g2.pat_center = center
            g2.pat_follow = mode == script_edit.PAT_MODE_LINEAR
            g2.pat_connect = mode == script_edit.PAT_MODE_LINEAR
        # 標準パターンのカタログとボタンの絵を差し替える(モードで変わる)
        if getattr(self, "_pat_mode_cur", None) != mode:
            self._pat_mode_cur = mode
            self._set_edit_tool("point")
            self._rebuild_pattern_icons()
        # =231: ユーザーパターンは**種別ごと**なので、種別が変わったら
        # 枠を読み直して名前・絵・バッジを付け替える(ufo↔a10 のように
        # パターンモードが同じでも種別が違えば別の20枠)。
        if getattr(self, "_user_kind_cur", None) != self._edit_type:
            self._user_kind_cur = self._edit_type
            if getattr(self, "edit_user_btns", None):
                self._user_patterns = script_edit.load_user_patterns(
                    load_config(), self._user_kind())
                if isinstance(self._edit_tool, tuple):
                    self._set_edit_tool("point")
                self._refresh_user_pattern_btns()
                self._refresh_fkey_badges()
        # =226: VIBRATION では上下反転は使わない(0↔100 が入れ替わって
        # 「停止が最大」になってしまうため。ユーザー決定)
        vib = mode == script_edit.PAT_MODE_VIB
        if vib and self._edit_invert:
            self._on_invert_btn()
        self.edit_invert_btn.configure(state="disabled" if vib else "normal")
        # =233(要望6): csv は at/pos ではなく「時間(100ms)」「回転速度」。
        # 数値も csv の生の値(時間=100ms単位 / 速度=-100〜100)で見せる。
        for g2 in self.edit_graphs:
            g2.pos_axis = "speed" if csv else "pos"
        if getattr(self, "grid_at_label", None) is not None:
            self.grid_at_label.configure(
                text=tr("時間(100ms):") if csv else tr("時間[at]:"))
            self.grid_pos_label.configure(
                text=tr("回転速度:") if csv else tr("位置[pos]:"))
            self.edit_at_label.configure(
                text=tr("時間(100ms):") if csv else "at(ms):")
            self.edit_pos_label.configure(
                text=tr("回転速度:") if csv else "pos:")
        # 時間[at]グリッド: csv は 100ms 未満を出さない(刻めないため)
        choices = [v for v in script_edit.GRID_AT_CHOICES
                   if not csv or v >= script_edit.CSV_AT_UNIT]
        none_lbl = tr("なし")
        self._grid_at_map = {script_edit.grid_at_label(v, none_lbl): v
                             for v in choices}
        self.edit_grid_at_menu.configure(values=list(self._grid_at_map))
        cur = self._grid_at_map.get(self.grid_at_var.get())
        if cur is None or (csv and cur < script_edit.CSV_AT_UNIT):
            self.grid_at_var.set(script_edit.grid_at_label(
                script_edit.CSV_AT_UNIT if csv
                else script_edit.GRID_AT_DEFAULT, none_lbl))
        # =297: csv は分解能 200(速度 1 刻み)なので、「回転速度:」グリッドの
        # 選択肢(20/10/5/2/なし)はそのまま速度単位になる(=296 の速度換算
        # 表示は不要になり撤回)。
        self._on_grid_change()
        # 読み方の案内(パレットの下)。csv / ROTATE / VIBRATION で文言を変える
        text = ""
        if csv:
            text = tr("csv(ROTATE): 中央0=停止 ／ 上=正回転 ／ 下=逆回転"
                      " ／ 次の点まで同じ値を保ちます ／ "
                      "時刻は100ms単位です")
        elif self._edit_type in SCRIPT_EDIT_CSV_TYPES:
            text = tr("ROTATE: 中央50=停止 ／ 上=正回転 ／ 下=逆回転 ／ "
                      "次の点まで同じ値を保ちます")
        elif self._edit_type == "vibration":
            text = tr("VIBRATION: 0=停止 〜 100=最大 ／ "
                      "次の点まで同じ値を保ちます")
        if text:
            self.edit_csv_hint.configure(text=text)
            if not self.edit_csv_hint.winfo_ismapped():
                self.edit_csv_hint.pack(fill="x", pady=(2, 0),
                                        before=self.edit_hint_label)
        else:
            self.edit_csv_hint.pack_forget()
        self._csv_pat_warned = False

    def _set_edit_pair(self, on: bool):
        """=232: 左右2本の同時編集を出し入れする(5列csv のときだけ ON)。"""
        on = bool(on)
        g0, g1 = self._edit_graphs[0], self._edit_graphs[1]
        if on == self._edit_pair:
            pass
        elif on:
            g1.pack(fill="both", expand=True, pady=(0, 0),   # =298: 上下を詰める
                    after=g0)
        else:
            if g1.winfo_ismapped():
                g1.pack_forget()
            self._edit_models[1].load([])
        self._edit_pair = on
        self._edit_active = 0
        g0.corner_text = tr("左（ロータ1）") if on else ""
        g1.corner_text = tr("右（ロータ2）") if on else ""
        # =311/=312: 案内文とバッジ(F1左 F5右)を表示に合わせる
        if getattr(self, "edit_fkey_hint", None) is not None:
            from .. import script_edit
            rot = getattr(self, "_edit_type", None) is not None and \
                self._pat_mode() == script_edit.PAT_MODE_ROTATE
            self.edit_fkey_hint.configure(
                text=self._fkey_hint_text(on, rot))
            self._refresh_fkey_badges()
        g0.set_active(True, peer_mode=on)
        g1.set_active(False, peer_mode=on)
        # =243: 左右2本のときは 上(左)=L / 下(右)=R を全高で描く
        g0.wave_channel = 0 if on else None
        g1.wave_channel = 1 if on else None
        # =298(要望4-①〜③): 2本のときは縮尺表示・「追従停止中」を上だけ、
        # 時間ラベルを下だけにして、上下のグラフを詰める
        g0.show_scale = True
        g0.show_follow_hint = True
        g0.show_time = not on
        g1.show_scale = False
        g1.show_follow_hint = False
        g1.show_time = True
        self._apply_graph_heights()

    def _apply_graph_heights(self):
        """=227/=232: 出ているグラフの要求高さを揃える(expand で等分)。"""
        shown = list(self.edit_graphs)
        # pack した直後は winfo_ismapped() がまだ 0 なので、状態で見る
        if getattr(self, "edit_sub_graph", None) is not None and \
                getattr(self, "_edit_sub_idx", -1) >= 0:
            shown.append(self.edit_sub_graph)
        h = self.EDIT_GRAPH_H if len(shown) <= 1 else self.EDIT_SUB_H
        for g in shown:
            g.configure(height=h)

    def _edit_track_label(self, i: int) -> str:
        ttype, path, _lo, _hi, ch = self._edit_tracks[i]
        label = "{0}: {1}".format(ttype, os.path.basename(path))
        if isinstance(ch, tuple):
            label += tr("（左右）")        # =232: 5列csv は1項目(同時編集)
        return label

    @staticmethod
    def _csv_is_2ch(ch) -> bool:
        """=232: 5列csv(左右独立)の候補かどうか。"""
        return isinstance(ch, tuple)

    def _build_edit_ui(self):
        from .. import script_edit
        self._edit_built = True
        # =232: 左右2本ぶん用意する(2本目は5列csvのときだけ出す)
        self._edit_models = [script_edit.ScriptEditModel(),
                             script_edit.ScriptEditModel()]
        # =298: 左右2本のクリップボード共有と UNDO の一本化(要望2/3)
        self._edit_link = script_edit.EditLink(self._edit_models)
        self._edit_active = 0
        self._edit_pair = False
        wrap = ctk.CTkFrame(self, corner_radius=10, fg_color=BOX_BG,
                            border_width=1, border_color=BOX_BORDER)
        self.edit_wrap = wrap
        inner = ctk.CTkFrame(wrap, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=10, pady=(8, 6))

        # ---- 1段目: 対象トラック / [点] / 保存・新規保存 ----
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text=tr("対象:"), font=ctk.CTkFont(size=12)
                     ).pack(side="left")
        self.edit_track_var = tk.StringVar(value="")
        self.edit_track_menu = CTkOptionMenu(
            row1, variable=self.edit_track_var, width=250, height=26,
            font=ctk.CTkFont(size=11), values=[""],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=self._on_edit_track_change)
        self.edit_track_menu.pack(side="left", padx=(6, 0))
        # =227: 別の種別のトラックをあとから足す導線(要望5)
        self.edit_new_btn = ctk.CTkButton(
            row1, text=tr("＋新規"), width=64, height=26,
            font=ctk.CTkFont(size=11), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._on_edit_new_track)
        self.edit_new_btn.pack(side="left", padx=(6, 0))
        Tooltip(self.edit_new_btn,
                lambda: tr("このアイテムへ別の種別のトラックを新しく作る"))
        # 新規作成のときだけ出す種別コンボ
        # (=224: funscript の linear/twist に加えて csv(ROTATE 3列/5列))
        self.edit_type_var = tk.StringVar(value="linear")
        self.edit_type_menu = CTkOptionMenu(
            row1, variable=self.edit_type_var, width=190, height=26,
            font=ctk.CTkFont(size=11),
            values=[n for n, _spec in SCRIPT_EDIT_NEW_KINDS],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_edit_type_change())
        # [点] ボタンはツール枠(row1 の下)へ移設(P2 =173)
        self.edit_saveas_btn = ctk.CTkButton(
            row1, text=tr("新規保存"), width=90, height=26,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"), command=self._edit_save_as)
        self.edit_saveas_btn.pack(side="right")
        self.edit_save_btn = ctk.CTkButton(
            row1, text=tr("保存"), width=76, height=26,
            font=ctk.CTkFont(size=12), fg_color=_clr.ACCENT,
            hover_color=_clr.ACCENT_HOVER, command=self._edit_save)
        self.edit_save_btn.pack(side="right", padx=(0, 6))

        # ---- ツール枠(P2 =173/=180): [点](縦2段)+標準パターン20個(=217) ----
        # 並びは=180のユーザー指定: [点]はパターン群の左隣に縦2段ぶんの
        # 高さで置く。[上下反転](旧インバート)は2段目(位置[pos]の左)へ。
        tools = ctk.CTkFrame(inner, fg_color="transparent")
        tools.pack(fill="x", pady=(6, 0))
        self._edit_tool = "point"
        self._edit_invert = False
        self._pat_icons = []            # PhotoImage の保持(インスタンス側。
        #                                 モジュールキャッシュは 7.3 の罠)
        self.edit_point_btn = ctk.CTkButton(
            tools, text=tr("点"), width=44, height=60,
            font=ctk.CTkFont(size=12), fg_color=_clr.ACCENT,
            hover_color=_clr.ACCENT_HOVER,
            command=self._on_point_btn)
        self.edit_point_btn.grid(row=0, column=0, rowspan=2,
                                 padx=(0, 8), pady=(0, 4), sticky="ns")
        self.edit_pattern_btns = []
        for i, (name, shape) in enumerate(script_edit.STD_PATTERNS):
            icon = self._pattern_icon(shape, step=False)
            self._pat_icons.append(icon)
            # CTkImage以外を渡すと HighDPI の警告が出る(=111 と同じ理由で
            # 黙らせる。Pillow=CTkImage は使わない方針)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                btn = ctk.CTkButton(
                    tools, text="", image=icon, width=self.PAT_BTN_W,
                    height=28,
                    fg_color="transparent", border_width=1,
                    border_color=MUTED, hover_color=("gray85", "gray25"),
                    command=lambda idx=i: self._set_edit_tool(idx))
            btn.grid(row=i // 10, column=1 + i % 10, padx=(0, 4),
                     pady=(0, 4), sticky="w")
            Tooltip(btn, lambda n=name: tr(n))
            self.edit_pattern_btns.append(btn)

        # ---- =205: ユーザーパターン U1〜U20(仕様 6.1)+編集ボタン ----
        # 未登録の枠はグレー(disabled)で押しても何も起きない。登録すると
        # 標準パターンと同じ絵柄ボタンになる。名前は U1〜U20 固定。
        # 配置(=218 ユーザー指定): U1〜U10=標準1〜10(1段目)の右、
        # U11〜U20=標準11〜20(2段目)の右。標準との間に少し隙間を空ける。
        # 「edit」ボタンは U20 の右隣(その上は空間になる)。
        # =231: **種別ごとに別々の20枠**。ボタンは枠番号(1〜20)で持ち、
        # 表示名(L1/T1/U1/A1/V1 …)は種別が変わるたびに付け替える。
        self._user_patterns = script_edit.load_user_patterns(
            load_config(), self._user_kind())
        self._user_icons = {}
        self.edit_user_btns = {}
        for j in range(script_edit.USER_PAT_SLOTS):
            slot = j + 1
            r, c = j // 10, 11 + j % 10
            btn = ctk.CTkButton(
                tools, text=self._user_key(slot), width=self.PAT_BTN_W,
                height=28,
                font=ctk.CTkFont(size=11), fg_color="transparent",
                border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=lambda n=slot: self._set_edit_tool(("user", n)))
            btn.grid(row=r, column=c, padx=(14, 4) if j % 10 == 0 else (0, 4),
                     pady=(0, 4), sticky="w")
            Tooltip(btn, lambda n=slot: (
                tr("ユーザーパターン {0}").format(self._user_key(n))
                if self._user_key(n) in self._user_patterns
                else tr("未登録（「ユーザーパターン編集」から登録）")))
            self.edit_user_btns[slot] = btn
        self.edit_userpat_btn = ctk.CTkButton(
            tools, text="edit", width=self.PAT_BTN_W, height=28,
            font=ctk.CTkFont(size=11), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._open_user_pattern_editor)
        self.edit_userpat_btn.grid(row=1, column=21, padx=(0, 4),
                                   pady=(0, 4), sticky="w")
        Tooltip(self.edit_userpat_btn, lambda: tr("ユーザーパターン編集"))
        self._refresh_user_pattern_btns()
        # ---- =212: Fキー割り当て(右クリックメニュー+右上のバッジ) ----
        # バッジは tk.Label をボタンへ重ね置き(place)。画像へ文字を描く
        # より軽く、テーマ切替でも configure で色を変えるだけで済む。
        self._fkey_map = script_edit.load_fkey_map(load_config())
        self._fkey_sides = script_edit.load_fkey_sides(load_config())  # =312
        self._fkey_badges = {}          # ref → tk.Label(左上=左/1本表示)
        self._fkey_badges_r = {}        # =314: ref → tk.Label(右上=右・緑)
        self._fkey_menu = None
        # 右クリックは **ダイアログ(Toplevel)のバインドタグ** で受ける。
        # CTkButton は画像ラベルを後から作る(U枠の登録時)ため、ボタン
        # ごとに bind すると張り直しが要る。Toplevel で受けて event.widget
        # の親をたどれば、どのボタン上の右クリックかが常に分かる。
        self.bind("<Button-3>", self._on_palette_right_click, add="+")
        self._refresh_fkey_badges()

        # ---- 2段目: [上下反転] / グリッド2コンボ / (at,pos) 数値入力欄 ----
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(6, 0))
        # [上下反転](=180で「インバート」から改名し、位置[pos]の左へ移設。
        # 独立した ON/OFF。配置するパターンの上下を反転する)
        self.edit_invert_btn = ctk.CTkButton(
            row2, text=tr("上下反転"), width=72, height=26,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            border_width=1, border_color=MUTED,
            text_color=("gray20", "gray85"),
            hover_color=("gray85", "gray25"),
            command=self._on_invert_btn)
        # =193: 点ボタン(幅44+8)の下は空け、2段目左端のパターン
        # (=217以降は STD11)の直下に置く
        self.edit_invert_btn.pack(side="left", padx=(52, 10))
        Tooltip(self.edit_invert_btn,
                lambda: tr("配置するパターンの上下を反転する（画面を閉じるまで有効）"))
        # =194: 縮尺配置ボタン(x2〜x0.5・排他トグル・既定 x1.0)。
        # 標準パターンを縮尺した長さで配置する(接続配置の片側接続にも
        # 効く。両側接続は両端で長さが決まるため対象外)
        self._edit_scale = 1.0
        self.edit_scale_btns = {}
        for label, v in (("x2", 2.0), ("x1.5", 1.5), ("x1.0", 1.0),
                         ("x0.75", 0.75), ("x0.5", 0.5)):
            b = ctk.CTkButton(
                row2, text=label, width=44, height=26,
                font=ctk.CTkFont(size=12), fg_color="transparent",
                border_width=1, border_color=MUTED,
                text_color=("gray20", "gray85"),
                hover_color=("gray85", "gray25"),
                command=lambda vv=v: self._on_scale_btn(vv))
            b.pack(side="left", padx=(0, 4))
            self.edit_scale_btns[v] = b
        self._refresh_scale_btns()
        Tooltip(self.edit_scale_btns[1.0],
                lambda: tr("パターンを配置するときの縮尺（画面を閉じるまで有効）"))
        # =233(要望5): **時間[at] を左・位置[pos] を右**へ入れ替えた
        # (右隣の at / pos の入力欄と並び順を揃えるため)。
        # ラベルは csv のときだけ「時間(100ms)」「回転速度」になる(=233 要望6)
        none_lbl = tr("なし")
        self.grid_at_label = ctk.CTkLabel(
            row2, text=tr("時間[at]:"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.grid_at_label.pack(side="left")
        self._grid_at_map = {
            script_edit.grid_at_label(g, none_lbl): g
            for g in script_edit.GRID_AT_CHOICES}
        self.grid_at_var = tk.StringVar(value=script_edit.grid_at_label(
            script_edit.GRID_AT_DEFAULT, none_lbl))
        self.grid_at_menu = CTkOptionMenu(
            row2, variable=self.grid_at_var, width=100, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_at_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change())
        self.grid_at_menu.pack(side="left", padx=(4, 10))
        # =224: csv では選択肢を 100ms 以上に差し替える(_apply_edit_kind)
        self.edit_grid_at_menu = self.grid_at_menu
        self.grid_pos_label = ctk.CTkLabel(
            row2, text=tr("位置[pos]:"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self.grid_pos_label.pack(side="left")
        self._grid_pos_map = {
            script_edit.grid_label(g, none_lbl): g
            for g in script_edit.GRID_POS_CHOICES}
        self.grid_pos_var = tk.StringVar(value=script_edit.grid_label(
            script_edit.GRID_POS_DEFAULT, none_lbl))
        self.grid_pos_menu = CTkOptionMenu(
            row2, variable=self.grid_pos_var, width=86, height=24,
            font=ctk.CTkFont(size=11),
            values=list(self._grid_pos_map.keys()),
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_grid_change())
        self.grid_pos_menu.pack(side="left", padx=(4, 10))
        # 選択中の点の (at, pos)。Enter で確定・衝突すれば元へ戻す(仕様 4.6)
        self.edit_at_label = ctk.CTkLabel(
            row2, text="at(ms):", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.edit_at_label.pack(side="left", padx=(8, 0))
        self.edit_at_entry = ctk.CTkEntry(row2, width=76, height=24,
                                          font=ctk.CTkFont(size=11))
        self.edit_at_entry.pack(side="left", padx=(4, 6))
        self.edit_pos_label = ctk.CTkLabel(
            row2, text="pos:", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED)
        self.edit_pos_label.pack(side="left")
        self.edit_pos_entry = ctk.CTkEntry(row2, width=64, height=24,
                                           font=ctk.CTkFont(size=11))
        self.edit_pos_entry.pack(side="left", padx=(4, 0))
        for w in (self.edit_at_entry, self.edit_pos_entry):
            w.bind("<Return>", lambda _e: self._edit_apply_entry())
        # ---- =213/=242: 時間補正(Fキー・数字キー配置の押し遅れを遡る) ----
        # =242: コンボ(0.1刻み)→**0.01秒刻みの入力欄+▲▼ボタン**。
        # 0.00〜-1.00。config("time_adjust_sec")へ保存し次回も引き継ぐ。
        ctk.CTkLabel(row2, text=tr("時間補正(秒):"),
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(10, 0))
        self._time_adj_last = self._load_time_adj()
        self.time_adj_var = tk.StringVar(
            value=self._time_adj_label(self._time_adj_last))
        self.time_adj_entry = ctk.CTkEntry(
            row2, width=52, height=24, font=ctk.CTkFont(size=11),
            justify="right", textvariable=self.time_adj_var)
        self.time_adj_entry.pack(side="left", padx=(4, 0))
        self.time_adj_entry.bind("<Return>",
                                 lambda _e: self._time_adj_commit())
        self.time_adj_entry.bind("<FocusOut>",
                                 lambda _e: self._time_adj_commit())
        Tooltip(self.time_adj_entry,
                lambda: tr("F1〜F9・数字キーで配置する位置を、押した瞬間"
                           "より前へずらす（押し遅れの補正。0.01秒刻み・"
                           "0.00〜-1.00秒）"))
        # ▲▼(縦2段)。三角形の文字は Roboto に無いのでUIフォントで描く
        # (7.4 の教訓)。押しっぱなしで連続増減(初回400ms→80ms間隔)。
        spin = ctk.CTkFrame(row2, fg_color="transparent")
        spin.pack(side="left", padx=(2, 0))
        self._time_adj_job = None
        self.time_adj_up = ctk.CTkButton(
            spin, text="▲", width=20, height=11, corner_radius=3,
            font=ctk.CTkFont(family=(appfont.FAMILY or None), size=8),
            fg_color=("gray75", "gray28"),
            hover_color=("gray70", "gray33"),
            text_color=("gray20", "gray85"))
        self.time_adj_up.pack()
        self.time_adj_down = ctk.CTkButton(
            spin, text="▼", width=20, height=11, corner_radius=3,
            font=ctk.CTkFont(family=(appfont.FAMILY or None), size=8),
            fg_color=("gray75", "gray28"),
            hover_color=("gray70", "gray33"),
            text_color=("gray20", "gray85"))
        self.time_adj_down.pack(pady=(2, 0))
        for btn, delta in ((self.time_adj_up, script_edit.TIME_ADJ_STEP),
                           (self.time_adj_down,
                            -script_edit.TIME_ADJ_STEP)):
            btn.bind("<ButtonPress-1>",
                     lambda _e, d=delta: self._time_adj_press(d))
            btn.bind("<ButtonRelease-1>",
                     lambda _e: self._time_adj_release())
        # ---- =243: 音声波形(参照モードのコンボと同じ設定を共有) ----
        ctk.CTkLabel(row2, text=tr("音声波形:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left", padx=(10, 0))
        self.edit_wave_var = tk.StringVar(
            value=self._wave_labels[self._wave_mode_key])
        self.edit_wave_menu = CTkOptionMenu(
            row2, variable=self.edit_wave_var, width=110, height=24,
            font=ctk.CTkFont(size=11),
            values=[self._wave_labels[k] for k in WAVE_MODE_KEYS],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=self._on_wave_mode)
        self.edit_wave_menu.pack(side="left", padx=(4, 0))
        # Fキーはダイアログのスコープで受ける(bind_all は編集画面全体へ
        # 漏れるため不可)。Entry にフォーカスがあっても Toplevel の
        # バインドタグで届く
        # =222: 押しっぱなしの数珠つなぎ。KeyPress/KeyRelease で受け、
        # OS の自動リピートは無視して**自前で「終端に来たら次を置く」**。
        # =312: 数珠つなぎの状態は**打点先(左=0/右=1)ごと**に持つ。
        # F1(左)を押しっぱなしにしたまま F5(右)を押す使い方のため。
        # 1本表示のときは常に [0] を使う(_fkey_held 等は互換用の別名)。
        self._fk_chain = [_FkChain(), _FkChain(), _FkChain()]   # [2]=両方
        for fk in script_edit.FKEYS:
            self.bind("<KeyPress-" + fk + ">",
                      lambda _e, k=fk: self._on_fkey_press(k), add="+")
            self.bind("<KeyRelease-" + fk + ">",
                      lambda _e, k=fk: self._on_fkey_release(k), add="+")
        # =223: 数字キーで打点(0=pos0 / 1=pos10 … 9=pos90 / +=pos100)。
        # テンキー(NumLock ON)とメインの数字行の両方。at/pos の入力欄に
        # カーソルがあるときは数字入力を優先する(_key_target_is_entry)。
        # =311: 左右2本(UFOTW)のときは**メインの数字行=左(ロータ1)・
        # テンキー=右(ロータ2)**に固定する(1本表示のときは両方とも今のグラフ)。
        for d in range(10):
            self.bind("<KeyPress-" + str(d) + ">",
                      lambda _e, v=d * 10: self._on_pos_key(v, side=0),
                      add="+")
            self.bind("<KeyPress-KP_" + str(d) + ">",
                      lambda _e, v=d * 10: self._on_pos_key(v, side=1),
                      add="+")
        self.bind("<KeyPress-plus>",
                  lambda _e: self._on_pos_key(100, side=0), add="+")
        self.bind("<KeyPress-KP_Add>",
                  lambda _e: self._on_pos_key(100, side=1), add="+")

        # ---- グラフ ----
        # =232: 2本作る。1本目は常に出し、2本目(右=ロータ2)は5列csvの
        # ときだけ pack する。表示は peers で常に揃える。
        self._edit_graphs = []
        for gi in range(2):
            g = script_edit.ScriptEditGraph(
                inner, self._edit_models[gi], on_change=self._edit_on_change,
                on_select=self._edit_on_select, on_menu=self._edit_menu,
                height=self.EDIT_GRAPH_H)
            g.on_paste_reject = self._edit_paste_reject
            # =192: 右ダブルクリックで再生位置をセット(メニューが出ない場所)
            g.on_seek = self.seek
            # =179: パターンの配置に成功したら点モードへ戻る
            g.on_placed = lambda: self._set_edit_tool("point")
            g.on_activate = lambda i=gi: self._set_active_graph(i)
            g.active_color = _clr.ACCENT
            self._edit_graphs.append(g)
        self._edit_graphs[0].pack(fill="both", expand=True, pady=(6, 0))
        # =227: サブ表示(参考用の別トラック。要望6)。既定は非表示で、
        # 下のコンボから選ぶとグラフの下半分に出る。地の色を変えて区別する。
        self.edit_sub_model = script_edit.ScriptEditModel()
        self.edit_sub_graph = script_edit.ScriptEditGraph(
            inner, self.edit_sub_model, on_change=lambda: None,
            on_select=lambda: None, on_menu=lambda *_a: None,
            height=self.EDIT_GRAPH_H)
        self.edit_sub_graph.make_readonly(self._edit_graphs[0])
        # =298(要望4-④): サブ表示には縮尺・時間ラベル・追従停止中を出さない
        self.edit_sub_graph.show_scale = False
        self.edit_sub_graph.show_time = False
        self.edit_sub_graph.show_follow_hint = False
        # =232: 表示を揃える仲間(左・右・サブ)を相互に結ぶ
        g0, g1, gs = self._edit_graphs[0], self._edit_graphs[1], \
            self.edit_sub_graph
        g0.peers = [g1, gs]
        g1.peers = [g0, gs]
        gs.peers = []
        g0.mirror = gs                 # =227 互換(テスト・既存コード)
        self._edit_sub_idx = -1
        # =224: csv(ROTATE)のときだけ出す読み方の案内(グラフのすぐ下)
        self.edit_csv_hint = ctk.CTkLabel(
            inner,
            text=tr("csv(ROTATE): 中央0=停止 ／ 上=正回転 ／ 下=逆回転"
                    " ／ 次の点まで同じ値を保ちます ／ "
                    "時刻は100ms単位です"),
            font=ctk.CTkFont(size=11), text_color=_clr.ACCENT_TEXT, anchor="w")
        self.edit_hint_label = ctk.CTkLabel(
            inner,
            text=tr("左クリック=打点・選択 ／ 左ドラッグ=移動・矩形選択 ／ "
                    "右クリック=点・パターンのメニュー ／ "
                    "右ダブルクリック=再生位置セット ／ "
                    "右ドラッグ=前後を見る ／ ホイール=時間の拡大縮小"),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w")
        self.edit_hint_label.pack(fill="x", pady=(4, 0))
        # =213: リアルタイム配置のヒント(2行目)
        self.edit_fkey_hint = ctk.CTkLabel(
            inner, text=self._fkey_hint_text(False),
            font=ctk.CTkFont(size=11), text_color=MUTED, anchor="w")
        self.edit_fkey_hint.pack(fill="x", pady=(0, 0))
        # =227: サブ表示の選択(ショートカットキーの説明の下・枠線の中)
        sub_row = ctk.CTkFrame(inner, fg_color="transparent")
        sub_row.pack(fill="x", pady=(4, 0))
        self.edit_sub_row = sub_row
        ctk.CTkLabel(sub_row, text=tr("サブ:"), font=ctk.CTkFont(size=11),
                     text_color=TEXT_MUTED).pack(side="left")
        self.edit_sub_var = tk.StringVar(value=tr("(なし)"))
        self.edit_sub_menu = CTkOptionMenu(
            sub_row, variable=self.edit_sub_var, width=250, height=24,
            font=ctk.CTkFont(size=11), values=[tr("(なし)")],
            fg_color=("gray75", "gray28"), button_color=("gray70", "gray33"),
            command=lambda _v: self._on_edit_sub_change())
        self.edit_sub_menu.pack(side="left", padx=(4, 0))
        Tooltip(self.edit_sub_menu,
                lambda: tr("編集中のトラックの下に、別のトラックを"
                           "参考として表示します（見るだけ・編集不可）"))
        # 保存結果・エラーの表示欄(モーダルにしない)
        self.edit_msg = ctk.CTkLabel(
            inner, text="", font=ctk.CTkFont(size=11), anchor="w",
            justify="left", wraplength=self.EDIT_WIN_W - 80)
        self.edit_msg.pack(fill="x", pady=(2, 0))

    @staticmethod
    def _fkey_hint_text(pair: bool, rotate: bool = False) -> str:
        """=213 のショートカット案内。=311/=312: 左右2本のときは打点先の
        説明に差し替える。=313: Q/E の 10 秒移動を追記。=317: 回転
        (csv・rotate funscript)は数字キーの対応表が違う。"""
        if pair:
            return tr("F1〜F9=割り当てたパターンを割り当てた側へ上書き配置（長押しで数珠つなぎ・左右別々） "
                      "／ 割り当て=パターンボタンを右クリック "
                      "／ 数字キー=再生位置へ打点：数字行=左（ロータ1）・テンキー=右（ロータ2）（1〜9=速度-70〜+70・5=停止） "
                      "／ 矢印キー=選択中の点・パターンを1グリッド移動 "
                      "／ Q・E=10秒戻る・進む")
        if rotate:
            return tr("F1〜F9=割り当てたパターンを再生位置へ上書き配置（長押しで数珠つなぎ） "
                      "／ 割り当て=パターンボタンを右クリック "
                      "／ 数字キー=再生位置へ打点（1〜9=速度-70,-60,-50,-40,0,+40,+50,+60,+70） "
                      "／ 矢印キー=選択中の点・パターンを1グリッド移動（長押しで連続） "
                      "／ Q・E=10秒戻る・進む")
        return tr("F1〜F9=割り当てたパターンを再生位置へ上書き配置（長押しで数珠つなぎ） "
                  "／ 割り当て=パターンボタンを右クリック "
                  "／ 数字キー=再生位置へ打点（0〜9=pos0〜90・+=pos100） "
                  "／ 矢印キー=選択中の点・パターンを1グリッド移動（長押しで連続） "
                  "／ Q・E=10秒戻る・進む")

    # =312: _fkey_held 等は =222 当時の名前の互換(1本表示=側0)。
    @property
    def _fkey_held(self):
        return self._fk_chain[0].held

    @property
    def _fkey_chain_end(self):
        return self._fk_chain[0].chain_end

    @property
    def _fkey_job(self):
        return self._fk_chain[0].job

    @property
    def _fkey_release_job(self):
        return self._fk_chain[0].release_job

    @classmethod
    def load_edit_height(cls, cfg=None) -> int:
        """記憶した編集モードの高さ(論理px)。無い/不正なら 0。"""
        try:
            cfg = load_config() if cfg is None else cfg
            sec = cfg.get(cls.CFG_REVIEW_WIN)
            v = int(sec.get("edit_h")) if isinstance(sec, dict) else 0
            return v if v > 0 else 0
        except Exception:
            return 0

    def _save_edit_height(self):
        """編集モード中の今の高さを config へ(winstate が有効なときだけ=
        =115/=181 と同じ作法。テストが共用設定を汚さないため)。"""
        if not self.edit_mode or not winstate.ENABLED:
            return False
        try:
            ch = self._to_logical(self.winfo_height())
        except Exception:
            return False
        if ch <= 1:
            return False
        try:
            cfg = load_config()
            sec = cfg.get(self.CFG_REVIEW_WIN)
            if not isinstance(sec, dict):
                sec = {}
            sec["edit_h"] = int(ch)
            cfg[self.CFG_REVIEW_WIN] = sec
            save_config(cfg)
            return True
        except Exception:
            return False

    def _apply_edit_layout(self):
        """編集モードのウィンドウサイズ。高さは reqheight 実測(仕様 2.1)。
        =211: 既定=下限 780(EDIT_WIN_H)。記憶した高さがあればそれを使う
        (下限未満には縮めない)。"""
        try:
            self.update_idletasks()
        except Exception:
            pass
        win_h = max(self.MIN_WIN_H, self._to_logical(self.winfo_reqheight()),
                    self.EDIT_WIN_H, self.load_edit_height())
        if self._user_geom:
            # =181: 記憶した大きさを尊重(編集UIに必要な幅・高さまでは広げる)
            cw = self._to_logical(self.winfo_width())
            ch = self._to_logical(self.winfo_height())
            if cw > 1 and ch > 1:
                self.geometry("{0}x{1}".format(max(cw, self.EDIT_WIN_W),
                                               max(ch, win_h)))
            else:
                self.geometry("{0}x{1}".format(self.EDIT_WIN_W, win_h))
        else:
            self.geometry("{0}x{1}".format(self.EDIT_WIN_W, win_h))

    def _on_edit_track_change(self, _value):
        # =227: 一覧の末尾の「(新規作成)」を選んでも新規作成へ入れる
        if self.edit_track_var.get() == tr("(新規作成)"):
            self._on_edit_new_track()
            return
        idx = 0
        try:
            idx = self.edit_track_menu.cget("values").index(
                self.edit_track_var.get())
        except ValueError:
            pass
        if idx >= len(self._edit_tracks):
            idx = -1
        if idx == self._edit_index and not self._edit_new:
            return
        # トラックの切り替え前に未保存確認(仕様 4.1)。UNDO の対象外。
        if not self._resolve_unsaved():
            self.edit_track_var.set(
                self._edit_track_label(self._edit_index)
                if self._edit_index >= 0 and not self._edit_new
                else tr("(新規作成)"))
            return
        self.edit_type_menu.pack_forget()
        self._edit_load_track(idx)

    def _refresh_sub_choices(self):
        """サブ表示のコンボを、いま編集していない候補で作り直す。"""
        vals = [tr("(なし)")]
        self._edit_sub_targets = []
        for i in range(len(self._edit_tracks)):
            if i == self._edit_index:
                continue
            vals.append(self._edit_track_label(i))
            self._edit_sub_targets.append(i)
        self.edit_sub_menu.configure(values=vals)
        if self.edit_sub_var.get() not in vals:
            self.edit_sub_var.set(tr("(なし)"))
            self._show_sub(-1)
        # 候補が1本だけ(=サブに出せる相手がいない)ときは選ばせない
        self.edit_sub_menu.configure(
            state="normal" if len(vals) > 1 else "disabled")

    def _on_edit_sub_change(self):
        label = self.edit_sub_var.get()
        vals = list(self.edit_sub_menu.cget("values"))
        try:
            k = vals.index(label)
        except ValueError:
            k = 0
        self._show_sub(self._edit_sub_targets[k - 1] if k >= 1 else -1)

    def _show_sub(self, idx: int):
        """サブ表示のトラックを切り替える(-1=非表示)。"""
        from .. import script_edit
        self._edit_sub_idx = idx
        if idx < 0:
            if self.edit_sub_graph.winfo_ismapped():
                self.edit_sub_graph.pack_forget()
            self._apply_graph_heights()       # =232: 残りで等分し直す
            return
        ttype, path, lo, hi, ch = self._edit_tracks[idx]
        pts = []
        try:
            if ch is not None:
                # =232: 5列(左右)の候補は**左(ロータ1)**を参考表示する
                c0 = ch[0] if isinstance(ch, tuple) else ch
                _cols, pts, _all = script_edit.load_csv_points(path, c0)
            else:
                _raw, pts = script_edit.load_funscript_raw(path)
        except Exception:
            pts = []
        # =297: サブ表示も種別の分解能で読む(csv=200)
        sub_max = script_edit.CSV_POS_MAX if ch is not None else 100
        self.edit_sub_model.pos_max = sub_max
        sub_c = script_edit.PAT_CENTERS.get(self._pat_mode_of(ttype))
        self.edit_sub_model.pat_center = None if sub_c is None \
            else int(round(sub_c * sub_max / 100.0))
        self.edit_sub_model.load(pts)
        g = self.edit_sub_graph
        g.pat_center = self.edit_sub_model.pat_center
        # サブ側の作法(階段/ヒートマップ)は**その種別のもの**にする
        step = (ch is not None) or ttype in SCRIPT_EDIT_STEP_TYPES
        g.step = step
        g.heat = not step
        g.region = self.edit_graph.region
        if not g.winfo_ismapped():
            # **一番下**へ出す(=227 要望6)。どれも expand=True なので、
            # 要求の高さを揃えれば縦に等分される(=232: 3本のときも同じ)
            g.pack(fill="both", expand=True, pady=(4, 0),
                   before=self.edit_csv_hint
                   if self.edit_csv_hint.winfo_ismapped()
                   else self.edit_hint_label)
        self._apply_graph_heights()
        main = self._edit_graphs[0]
        g.level = main.level
        g.view_ms = main.view_ms
        g.now_ms = main.now_ms
        g.redraw()

    def _on_edit_new_track(self):
        """=227: **別のトラックを新しく作る**導線(要望5)。

        従来は「候補が0本のときだけ」新規作成に入れたので、1本作ると
        他の種別を足せなかった。「＋新規」ボタン(と一覧の末尾の
        「(新規作成)」)から、いつでも空のグラフへ切り替えられるようにする。
        """
        if self._edit_new and self._edit_index < 0:
            return
        if not self._resolve_unsaved():
            self.edit_track_var.set(
                self._edit_track_label(self._edit_index)
                if self._edit_index >= 0 else tr("(新規作成)"))
            return
        self.edit_track_var.set(tr("(新規作成)"))
        if not self.edit_type_menu.winfo_ismapped():
            self.edit_type_menu.pack(side="left", padx=(6, 0))
        self._edit_load_track(-1)

    def _on_edit_type_change(self):
        # 新規作成の種別変更(点列はそのまま。保存時の紐づけ先種別が変わる)
        # =224: csv/funscript の切り替えも起きるので画面の作法を作り直す
        self._edit_type, self._edit_kind, self._edit_cols = \
            self._new_kind_of(self.edit_type_var.get())
        self._edit_csv_ch = 0
        self._edit_csv_other = []
        # =234(要望1): **種別コンボで UFOTW用csv を選んだ時点で左右2本**
        # (=233 では `_edit_load_track(-1)` を通る導線だけだった)
        pair = self._edit_kind == "csv" and self._edit_cols == 5
        if pair != self._edit_pair:
            self._set_edit_pair(pair)
            if pair:
                self._edit_models[1].load([])
                self._edit_models[1].dirty = False
        self._apply_edit_kind()
        for _g in self.edit_graphs:
            _g.redraw()

    def _edit_load_track(self, idx: int):
        """編集対象を読み込む。idx=-1 は空のグラフから新規作成。

        **区間(=59)は一切適用しない**(仕様 4.2 ← 最重要)。区間内だけを
        保存すると区間外のデータが消えるため。区間は目印の縦線だけ描く。
        """
        from .. import script_edit
        self.pause()
        self._edit_index = idx
        warns = list(getattr(self, "_edit_warn_base", []))
        region = (0.0, None, False)
        if idx < 0:
            self._edit_new = True
            self._edit_path = ""
            self._edit_extra = None
            # =224: 新規作成の種別は「種別 / kind / 列数」の3つ組
            self._edit_type, self._edit_kind, self._edit_cols = \
                self._new_kind_of(self.edit_type_var.get())
            self._edit_csv_ch = 0
            self._edit_csv_other = []
            # =233(要望2): 新規作成でも **5列(UFOTW用csv)は最初から2本**
            self._set_edit_pair(self._edit_kind == "csv"
                                and self._edit_cols == 5)
            for _m in self._edit_models:
                _m.pos_max = self._edit_pos_max()     # =297: load の前に
                _m.load([])
        else:
            self._edit_new = False
            ttype, path, lo, hi, ch = self._edit_tracks[idx]
            self._edit_type = ttype
            self._edit_path = path
            self._edit_extra = None
            self._edit_kind = "csv" if (ch is not None) else "funscript"
            for _m in self._edit_models:
                _m.pos_max = self._edit_pos_max()     # =297: load の前に
            pair = self._csv_is_2ch(ch)         # =232: 5列=左右同時編集
            self._edit_csv_ch = 0 if pair else (ch or 0)
            self._edit_csv_other = []
            self._edit_cols = 3
            pts = []
            if self._edit_kind == "csv":
                # =224: csv(ROTATE)。=297: pos 100=停止 / 200=正回転 /
                # 0=逆回転(分解能 200=速度 1 刻み)。
                # **=232: 5列は左右2本を同時に読み込む**(片方を保持して
                # 保存へ回す `_edit_csv_other` の受け渡しは不要になった)。
                other = []
                if os.path.isfile(path):
                    try:
                        cols, pts, allp = script_edit.load_csv_points(
                            path, self._edit_csv_ch)
                        self._edit_cols = cols
                        if cols == 5 and len(allp) >= 2:
                            other = allp[1]
                            if not pair:
                                self._edit_csv_other = \
                                    allp[1 - self._edit_csv_ch]
                    except Exception as e:
                        warns.append(tr("スクリプトを読み込めません({0}): {1}")
                                     .format(os.path.basename(path), e))
                        self._edit_path = ""
                pair = pair and self._edit_cols == 5
                self._set_edit_pair(pair)
                self._edit_models[0].load(pts)
                if pair:
                    self._edit_models[1].load(other)
            else:
                self._set_edit_pair(False)
                if os.path.isfile(path):
                    try:
                        self._edit_extra, pts = \
                            script_edit.load_funscript_raw(path)
                    except Exception as e:
                        warns.append(tr("スクリプトを読み込めません({0}): {1}")
                                     .format(os.path.basename(path), e))
                        # 壊れたファイルを黙って上書きしないよう保存先は無効化
                        self._edit_path = ""
                # ファイルが無い=空のグラフから描き始める(仕様 決定5)。
                # 保存は「新規保存」ではなくそのパスへの上書きで作成できる。
                self._edit_models[0].load(pts)
                # =204: パターン記憶("rvp" キー)の復元。照合に失敗した
                # パターンは情報だけ捨てて点は残す(仕様 6.2)
                if isinstance(self._edit_extra, dict) and \
                        script_edit.RVP_KEY in self._edit_extra:
                    _n, dropped = self._edit_models[0].import_patterns(
                        self._edit_extra.get(script_edit.RVP_KEY))
                    if dropped:
                        warns.append(tr(
                            "一部のパターン情報を復元できませんでした"
                            "（点はそのまま残しています）"))
            region = (lo, hi, bool(lo) or hi is not None)
        self._apply_edit_kind()
        for _g in self.edit_graphs:                  # =232: 区間は全部へ
            _g.region = region
        self.edit_save_btn.configure(
            state="normal" if self._edit_path else "disabled")
        # 区間指定のあるアイテムでは「区間を無視している」注意書き(仕様 4.2)
        if region[2]:
            warns.insert(0, tr("編集モード: 区間指定を無視して素材全体を"
                               "表示・再生しています"))
        if warns:
            self.warn_label.configure(text="\n".join(warns))
            self.warn_label.pack(fill="x", padx=14, pady=(4, 0),
                                 before=self.transport_card)
        else:
            self.warn_label.pack_forget()
        self.edit_msg.configure(text="")
        if getattr(self, "edit_sub_menu", None) is not None:
            self._refresh_sub_choices()          # =227
            if self._edit_sub_idx == idx:        # 自分自身はサブにしない
                self.edit_sub_var.set(tr("(なし)"))
                self._show_sub(-1)
        self._edit_refresh_duration()
        self._reset_clock()
        if self._video and self._mpv is not None:
            # =203: 編集モードは素材全体(区間を無視)なので頭出しする
            self._mpv.set_pause(True)
            self._mpv.seek(0.0)
            self._mpv_cmd_t = time.monotonic()
        self._set_active_graph(0)                 # =232: 上(左)から始める
        for _g in self.edit_graphs:
            _g.now_ms = 0.0
            _g.playing = self._playing        # =198: 再生中に入った場合
            _g.initial_view(self._duration_ms)
        self._update_time(0.0, force=True)
        self._edit_on_select()
        self._schedule_tick()

    def _edit_refresh_duration(self):
        """全長 = max(音声の長さ, スクリプトの最終点)(仕様 4.9)。

        末尾より後ろに打点したら全長も伸びる。編集モードの音声は
        **区間を無視した素材全体**(self._edit_sound)。
        """
        audio_ms = 0
        if self._video:
            audio_ms = int(self._video_len)   # =203: 動画の実長(素材全体)
        elif self._edit_sound is not None:
            try:
                audio_ms = int(self._edit_sound.get_length() * 1000)
            except Exception:
                audio_ms = 0
        self._sound = self._edit_sound
        self._duration_ms = max([audio_ms] +                # =232: 左右とも
                                [m.duration_ms()
                                 for m in self.edit_models])
        self._sync_buttons()

    def _edit_on_change(self):
        # 貼り付け拒否の警告は、次の編集が成功した時点で下ろす(=171)
        if getattr(self, "_edit_paste_warned", False):
            self._edit_paste_warned = False
            self.edit_msg.configure(text="")
        self._edit_refresh_duration()
        self._update_time(self._now_ms(), force=True)

    def _edit_paste_reject(self, reason: str = "range"):
        """貼り付けが拒否されたときの表示(=171/=172/=185)。"""
        self._edit_paste_warned = True
        if reason == "no_selection":
            msg = tr("貼り付けの基準になる点を選択してください"
                     "（選択中で最も未来の点が基準になり、その点は"
                     "上書きされます）")
        elif reason == "edge":
            msg = tr("パターンの端のすぐ近くには点を置けないため、"
                     "貼り付けできませんでした")
        else:
            msg = tr("貼り付け先に収まらないため、貼り付けできませんでした"
                     "（0〜100または時間の範囲外になります）")
        self.edit_msg.configure(text=msg, text_color=MSG_WARN)

    # =233(要望6): csv では入力欄の単位を **csv の生の値**にする。
    #   時間 = 100ms 単位(10 = 1秒) / 回転速度 = -100〜100
    #   (正=正回転・負=逆回転。絶対値が csv の3列目/5列目と一致する)
    def _entry_is_csv(self) -> bool:
        return getattr(self, "_edit_kind", "funscript") == "csv"

    def _at_to_entry(self, at: int) -> str:
        from .. import script_edit
        if self._entry_is_csv():
            return "{0:g}".format(at / script_edit.CSV_AT_UNIT)
        return str(at)

    def _at_from_entry(self, txt: str) -> int:
        from .. import script_edit
        v = float(txt)
        if self._entry_is_csv():
            v *= script_edit.CSV_AT_UNIT
        return int(round(v))

    def _pos_to_entry(self, pos: int) -> str:
        from .. import script_edit
        if self._entry_is_csv():
            # =297: 分解能 200(中心 100)なので速度 = pos - 100(1 刻み)
            return str(int(pos) - script_edit.CSV_STOP_POS)   # -100〜100
        return str(pos)

    def _pos_from_entry(self, txt: str) -> int:
        from .. import script_edit
        v = float(txt)
        if self._entry_is_csv():
            v = script_edit.CSV_STOP_POS + max(-100.0, min(100.0, v))
        return int(round(v))

    def _edit_on_select(self):
        """選択の変化を (at,pos) 欄へ反映。単独選択のときだけ編集できる。"""
        sel = self.edit_model.selection
        for w in (self.edit_at_entry, self.edit_pos_entry):
            try:
                w.configure(state="normal")
                w.delete(0, "end")
            except Exception:
                pass
        if len(sel) == 1:
            at = next(iter(sel))
            pos = self.edit_model.pos_of(at)
            self.edit_at_entry.insert(0, self._at_to_entry(at))
            if pos is not None:
                self.edit_pos_entry.insert(0, self._pos_to_entry(pos))
        else:
            for w in (self.edit_at_entry, self.edit_pos_entry):
                w.configure(state="disabled")

    def _edit_apply_entry(self):
        """(at,pos) 数値入力の確定。衝突・不正値なら表示を元へ戻す。"""
        sel = self.edit_model.selection
        if len(sel) != 1:
            return
        at = next(iter(sel))
        try:
            new_at = self._at_from_entry(self.edit_at_entry.get())
            new_pos = self._pos_from_entry(self.edit_pos_entry.get())
        except ValueError:
            self._edit_on_select()
            return
        if self.edit_model.set_point(at, new_at, new_pos):
            self._edit_on_change()
        self._edit_on_select()
        self.edit_graph.redraw()

    def _on_grid_change(self):
        # グリッド設定の変更は UNDO の対象外(仕様 4.5)
        # =298(要望1): 左右2本(とサブ表示)の**全部**へ反映する(以前は
        # アクティブな方だけだった)
        gp = self._grid_pos_map.get(self.grid_pos_var.get(), 10)
        ga = self._grid_at_map.get(self.grid_at_var.get(), 100)
        graphs = list(self.edit_graphs)
        if getattr(self, "edit_sub_graph", None) is not None:
            graphs.append(self.edit_sub_graph)
        for g in graphs:
            g.grid_pos = gp
            g.grid_at = ga
            g.redraw()

    def _ask_confirm(self, message: str, yes_text: str) -> bool:
        """[yes_text]/[キャンセル] の確認。テストは SCRIPT_EDIT_CONFIRM_AUTO。"""
        if _pkg().SCRIPT_EDIT_CONFIRM_AUTO is not None:
            return bool(_pkg().SCRIPT_EDIT_CONFIRM_AUTO)
        res = self._ask_buttons(message, [("yes", yes_text, "danger"),
                                          ("cancel", tr("キャンセル"),
                                           "ghost")])
        return res == "yes"

    def _resolve_unsaved(self) -> bool:
        """未保存の編集の確認(仕様 14b/4.10)。

        戻り値 True=続行してよい(保存済み/破棄/そもそも未編集)、
        False=「やめる」(元の操作を中止する)。
        """
        # =232: 左右2本のどちらかが未保存なら確認を出す
        if not self.edit_mode or self.edit_model is None \
                or not any(m.dirty for m in self.edit_models):
            return True
        if _pkg().SCRIPT_EDIT_UNSAVED_AUTO is not None:
            choice = _pkg().SCRIPT_EDIT_UNSAVED_AUTO
        else:
            choice = self._ask_buttons(
                tr("スクリプトに未保存の編集があります。"),
                [("save", tr("保存する"), "primary"),
                 ("discard", tr("破棄する"), "danger"),
                 ("cancel", tr("やめる"), "ghost")])
        if choice == "cancel" or choice is None:
            return False
        if choice == "save":
            if self._edit_path:
                return self._edit_save()
            return self._edit_save_as()
        # discard
        for m in self.edit_models:
            m.dirty = False
        return True

    def _ask_buttons(self, message: str, buttons) -> str | None:
        """小さなモーダルでボタンを選ばせる。戻り値=押したボタンのキー。"""
        dlg = ctk.CTkToplevel(self)
        dlg.title(tr("確認"))
        dlg.resizable(False, False)
        dlg.transient(self)
        result = {"key": None}
        ctk.CTkLabel(dlg, text=message, font=ctk.CTkFont(size=12),
                     wraplength=380, justify="left"
                     ).pack(padx=18, pady=(16, 10))
        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(padx=18, pady=(0, 14))

        def choose(key):
            result["key"] = key
            dlg.destroy()

        for key, label, style in buttons:
            kw = {}
            if style == "primary":
                kw = dict(fg_color=_clr.ACCENT, hover_color=_clr.ACCENT_HOVER)
            elif style == "danger":
                kw = dict(fg_color="#c9451a", hover_color="#a83a16")
            else:
                kw = dict(fg_color="transparent", border_width=1,
                          border_color=MUTED,
                          text_color=("gray20", "gray85"),
                          hover_color=("gray85", "gray25"))
            ctk.CTkButton(row, text=label, width=96, height=30,
                          command=lambda k=key: choose(k), **kw
                          ).pack(side="left", padx=4)
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        _place_popup(dlg, self, 420, 120)
        try:
            dlg.grab_set()
        except Exception:
            pass
        self.wait_window(dlg)
        return result["key"]

    def _edit_save(self) -> bool:
        """上書き保存。同じファイルが他でも使われていれば警告を挟む。"""
        if not self._edit_path:
            return self._edit_save_as()
        # 検出範囲=編集画面で開いているシナリオ全体(未保存の編集内容込み・
        # 自動紐づけも走査=仕様 14a)。owner が無い経路では走査しない。
        if self.owner is not None:
            try:
                uses = self.owner.script_usage(self._edit_path)
            except Exception:
                uses = []
            others = max(0, len(uses) - 1)
            if others >= 1:
                msg = tr("このファイルは他の {0} 箇所でも使われています。"
                         "上書きしますか？").format(others)
                msg += "\n" + "\n".join(uses)
                if not self._ask_confirm(msg, tr("上書きする")):
                    return False
        return self._edit_write(self._edit_path)

    def _edit_save_as(self) -> bool:
        """新規保存。初期フォルダは前回保存フォルダ(独立キーで永続=270)、
        無ければシナリオのフォルダ。既定名=音声名+タグ.funscript(仕様 12)。"""
        from tkinter import filedialog
        # =270: レビュー画面の新規保存は独立キー "review_save" で前回の
        # 保存フォルダを記憶する(編集画面のダイアログ群とは別管理)。
        initial_dir = _load_dialog_dir("review_save")
        if not (initial_dir and os.path.isdir(initial_dir)):
            initial_dir = ""
            if self.owner is not None and getattr(self.owner, "path", None):
                initial_dir = os.path.dirname(
                    os.path.abspath(self.owner.path))
            elif self.spec.get("audio") or self.spec.get("video"):
                initial_dir = os.path.dirname(
                    self.spec.get("audio") or self.spec.get("video"))
        src = self.spec.get("audio") or self.spec.get("video") or ""
        base = os.path.splitext(os.path.basename(src))[0] if src else ""
        # =224: csv を編集しているときは csv として保存する。
        # 自動紐づけのタグ(ufo/a10)も既定のファイル名へ添えておく
        csv = self._edit_kind == "csv"
        # =225: 自動紐づけのタグ(_twist / _vib / _ufo / _a10)を既定名へ添える
        tag = SCRIPT_EDIT_TAGS.get(self._edit_type, "")
        if csv:
            ext, title = ".csv", tr("csvの新規保存")
            types = [("csv", "*.csv"), (tr("すべて"), "*.*")]
        else:
            ext, title = ".funscript", tr("funscriptの新規保存")
            types = [("funscript", "*.funscript"), (tr("すべて"), "*.*")]
        initial_file = (base + tag + ext) if base else ext
        path = _pkg().filedialog.asksaveasfilename(
            parent=self, title=title,
            initialdir=initial_dir or None, initialfile=initial_file,
            defaultextension=ext, filetypes=types)
        if not path:
            return False
        _remember_dialog_dir(path, "review_save")
        if not self._edit_write(path):
            return False
        # 保存したファイルをアイテムのトラックへ紐づける(仕様 12)。
        self._bind_saved_file(path)
        self._edit_path = path
        self._edit_new = False
        self.edit_save_btn.configure(state="normal")
        return True

    def _edit_write(self, path: str) -> bool:
        """書き出し本体。tmp→os.replace・actions以外のキー保持(仕様 14c)。
        =204: パターン記憶("rvp" キー)を同梱する(パターン0個なら
        キーごと書かない=削除)。"""
        from .. import script_edit
        try:
            if self._edit_kind == "csv":
                # =224: csv は階段+100ms単位。5列はもう一方のチャンネルを
                # そのまま残して1行に両方を書く。**パターン記憶は書けない**
                # (ユーザー決定=ファイル形式は変えない)。
                if self._edit_pair:
                    # =232: 5列の左右同時編集=2本のモデルをそのまま書く
                    script_edit.write_csv(
                        path, self._edit_models[0].points, 5,
                        self._edit_models[1].points, 0)
                else:
                    script_edit.write_csv(
                        path, self.edit_model.points, self._edit_cols or 3,
                        self._edit_csv_other if (self._edit_cols == 5)
                        else None, self._edit_csv_ch)
            else:
                script_edit.write_funscript(path, self.edit_model.points,
                                            self._edit_extra,
                                            self.edit_model.export_patterns())
        except OSError as e:
            self.edit_msg.configure(
                text=tr("ファイルへ書き込めませんでした(他のアプリで使用中・"
                        "読み取り専用・アクセス権なし等の可能性):\n{0}")
                .format(e), text_color=MSG_ERROR)
            return False
        for m in self.edit_models:        # =232: 左右とも保存済みにする
            m.dirty = False
        msg = tr("保存しました") + ": " + os.path.basename(path)
        color = MSG_OK
        # =224: csv にはパターン記憶の置き場が無い(初回だけ案内する)
        if self._edit_kind == "csv" \
                and any(m.patterns for m in self.edit_models) \
                and not self._csv_pat_warned:
            self._csv_pat_warned = True
            msg += "  " + tr("（csvではパターンの記憶は保存されません。"
                             "開き直すと点だけになります）")
            color = MSG_WARN
        self.edit_msg.configure(text=msg, text_color=color)
        # 保存後は編集モードのまま留まる(仕様 11)
        return True

    def _bind_saved_file(self, path: str):
        """新規保存したファイルをアイテムのトラックへ紐づける(仕様 12)。

        自動モードだった場合は**手動モードへ切り替えて**トラック行を作る。
        シナリオ .json 自体の保存は従来どおり編集画面の「保存」で行う
        (この時点では .json は書かない)。
        行が別のアイテムへ使い回されていた場合は紐づけを行わない
        (レビューを開いたまま編集画面でイベントを切り替えたケース)。
        """
        row = self._row
        try:
            if row is None or not row.winfo_exists() or \
                    getattr(row, "audio_rel", None) != self._row_audio_rel:
                self.edit_msg.configure(
                    text=tr("アイテムの行が見つからないため、トラックへの"
                            "紐づけは行われませんでした"),
                    text_color=MSG_WARN)
                return
        except Exception:
            return
        ttype = self._edit_type
        rel = _safe_relpath(path, row.base_dir)
        try:
            if row.mode == "auto":
                # 手動モードへ切り替え(自動の解決結果を行として引き継ぐ)
                row.mode_var.set(tr("手動"))
                row._on_mode_change()
            hit = None
            for e in row.track_rows:
                if e["type_var"].get() == ttype:
                    hit = e
                    break
            if hit is not None:
                hit["fs_path"] = rel
                row._update_track_row(hit)
            else:
                row._add_track_row(ttype, rel)
            row._update_mode_ui()
        except Exception:
            pass      # 紐づけに失敗しても保存自体は成功している
        # 編集対象候補にも加えて、コンボから選び直せるようにする
        # (=224: csv は (…, ch) 付き。5列で新規保存したときは左右2つ足す)
        ch = self._edit_csv_ch if self._edit_kind == "csv" else None
        entry = (ttype, path, 0.0, None, ch)
        labels = []
        found = False
        self._edit_tracks = [t for t in self._edit_tracks]
        for i, (t, p, lo, hi, c) in enumerate(self._edit_tracks):
            if t == ttype and c == ch and \
                    os.path.normcase(os.path.abspath(p)) == \
                    os.path.normcase(os.path.abspath(path)):
                self._edit_tracks[i] = entry
                found = True
                self._edit_index = i
        if not found:
            # =232: 5列csv は**1項目(左右)**として加える(=224 の
            # 「（左）」「（右）」に分ける方式は廃止)
            if self._edit_kind == "csv" and self._edit_cols == 5:
                entry = (entry[0], entry[1], entry[2], entry[3], (0, 1))
            self._edit_tracks.append(entry)
            self._edit_index = len(self._edit_tracks) - 1
        labels = [self._edit_track_label(i)
                  for i in range(len(self._edit_tracks))]
        sel = labels[self._edit_index]
        self.edit_track_menu.configure(values=labels)   # =234: 新規作成は
        #                                    「＋新規」ボタンからだけ
        self.edit_track_var.set(sel)
        self.edit_type_menu.pack_forget()
