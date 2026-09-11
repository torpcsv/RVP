"""メイン画面: ヘッダ(タイトル・ヘルプ・設定・外観/テーマ/言語/フォント)とウィンドウ寸法(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
import warnings
from ..i18n import LANG, load_config, set_language, tr
from .. import appfont, apptheme
from tkinter import messagebox

from .common import (COMBO_TEXT, COMBO_TEXT_DISABLED, LABEL, MUTED,
    _triangle_photo, logger)
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


class _RVPAppHeaderMixin:
    """RVPApp の mixin(=301 分割)。ヘッダ(タイトル・ヘルプ・設定・外観/テーマ/言語/フォント)とウィンドウ寸法"""

    def _place_window(self, w: int, h: int):
        """ウィンドウを画面左上から WIN_MARGIN px の位置に配置する。"""
        self.root.update_idletasks()
        m = self.WIN_MARGIN
        self.root.geometry(f"{w}x{h}+{m}+{m}")

    def _measure_header_max_width(self) -> int:
        """接続ピルが**いちばん広い文言**のときのヘッダー幅(px)を実測する。

        文言によって幅が変わるので、狭いほうで判定すると「接続した瞬間に
        タブへ食い込む」ことになる。いったん最長の文言を入れて測り、元へ戻す
        (起動時に1回だけ。表示は一瞬たりとも変わらない=描画前に戻すため)。
        """
        pill = self.conn_pill
        keep = pill.cget("text")
        best = 0
        try:
            for t in self._pill_texts:
                pill.configure(text=t)
                self.root.update_idletasks()
                best = max(best, self.header.winfo_reqwidth())
        except Exception:
            logger.exception("header width measure failed")
        finally:
            try:
                pill.configure(text=keep)
                self.root.update_idletasks()
            except Exception:
                logger.exception("header text restore failed")
        return best or self.header.winfo_reqwidth()

    def _header_needed_width(self) -> int:
        """タブとヘッダーを同じ帯に置くのに必要なウィンドウ幅(px)。

        タブの右端(左余白20＋最後のタブ)＋隙間＋ヘッダー幅＋右余白。
        タブ幅もヘッダー幅も**言語で変わる**ので実測から求める
        (日本語/英語でボタン幅とピルの文字数が違う)。
        """
        tabs_end = 20 + max(0, self.tabs._next_x - 6)
        hw = getattr(self, "_header_max_w", 0) or self.header.winfo_reqwidth()
        return tabs_end + self.HEADER_GAP + hw + self.HEADER_RIGHT

    def _apply_tabs_top(self) -> bool:
        """タブ行の上端を決める(compact=ヘッダーと同じ帯 / classic=その下)。

        狭いウィンドウでヘッダーがタブに重なると、タブの右端が押しボタンで
        隠れて押せなくなる。**幅が足りないときだけ**=156までと同じ
        「ヘッダー行の下にタブ」へ自動で戻す(見た目は従来どおりで安全)。
        戻り値は compact かどうか。
        """
        try:
            w = self.root.winfo_width()
            # =158: マップ前は Windows だと 1 が返る。ここで「狭い」と決めると
            # 起動直後だけ classic に落ちてしまうので**判断を保留**する
            # (マップ後の <Configure> でもう一度呼ばれる)。
            if w <= 1:
                return True if self._tabs_compact is None else self._tabs_compact
            compact = w >= self._header_needed_width()
        except Exception:
            logger.exception("tabs top calc failed")
            compact = False
        if compact == self._tabs_compact:
            return compact
        self._tabs_compact = compact
        top = self.TABS_TOP if compact else self.TABS_TOP_CLASSIC
        self.tabs.pack_configure(pady=(top, 4))
        return compact

    def _ensure_header_width(self):
        """タブとヘッダーが同居できる幅を最小幅として保証する(=157)。

        必要幅は**言語とフォントで変わる**ので実測から決める(日本語 約714px /
        英語 約733px)。=156までの既定幅690pxではわずかに足りないので、
        **minsize を上げるだけ**にする。Tkは最小幅より小さいウィンドウを
        その場で広げてくれる(=幅だけが変わり、高さと位置には触らない)。

        **=158の不具合の教訓**: ここで `geometry()` を呼んではいけない。
        呼ぶと**サイズだけを上書き**するので、=115で復元した「前回の大きさ」を
        毎回つぶしてしまう(位置は残るので「位置だけ記憶される」ように見える)。
        しかも起動直後のウィンドウはまだ地図に載っておらず、Windowsでは
        `winfo_width()/winfo_height()` が **1** を返す=「狭すぎる」と誤判定して
        必ず走り、高さ1px→最小高700pxへ丸められて固定サイズ化していた。
        (Xvfbでは実サイズを返すため、コンテナのテストでは見えなかった。)
        幅が足りるかどうかの判定は、**マップ後の `<Configure>`** に任せる
        (`_apply_tabs_top`)。
        """
        try:
            self.root.update_idletasks()
            self._header_max_w = self._measure_header_max_width()
            need = self._header_needed_width()          # 実ピクセル
            # **単位に注意**: winfo_* は実ピクセル、CTkの minsize() は
            # **論理ピクセル**(内部で window scaling を掛けてから Tk へ渡す)。
            # 高DPI(125%等)で実ピクセルのまま渡すと二重に拡大されてしまう
            # ので、渡す前に割り戻す(等倍環境では素通り)。
            # 高さの最小値は従来どおり700(この改修で必要量は増えない)。
            self.root.minsize(max(580, self._to_logical(need)),
                              self._min_win_h())
            self._apply_tabs_top()
        except Exception:
            logger.exception("ensure header width failed")

    def _min_win_h(self) -> int:
        """最小の高さ。画面が低ければその範囲へ丸める(論理ピクセル)。"""
        try:
            avail = self._to_logical(self.root.winfo_screenheight() - 60)
            return max(self.MIN_WIN_H_FLOOR, min(self.MIN_WIN_H, avail))
        except Exception:
            logger.exception("min window height calc failed")
            return self.MIN_WIN_H

    def _to_logical(self, px: int) -> int:
        """実ピクセル → CTkの論理ピクセル(window scaling を割り戻す)。

        倍率そのもの(`__window_scaling`)は名前修飾されていて触りにくいので、
        CTkが持つ変換メソッド `_reverse_window_scaling` / `_apply_window_scaling`
        を使う。**切り捨てで1px足りなくなることがある**ので、掛け直して
        元に届かなければ1つ足す(足りないと最小幅の意味が無くなる)。
        等倍環境・メソッドが無い環境ではそのまま返す。
        """
        rev = getattr(self.root, "_reverse_window_scaling", None)
        app = getattr(self.root, "_apply_window_scaling", None)
        if not callable(rev):
            return int(px)
        try:
            v = int(rev(px))
            if callable(app) and int(app(v)) < px:
                v += 1
            return v
        except Exception:
            logger.exception("window scaling reverse failed")
            return int(px)

    def _on_root_configure(self, event=None):
        """ウィンドウのリサイズ。**メインウィンドウ自身の変化だけ**を見る。"""
        if event is not None and event.widget is not self.root:
            return
        self._apply_tabs_top()

    def _build_header(self):
        # =157: **pack をやめて place で右上に固定する**(2026-08-16 ユーザー決定)。
        # ヘッダーを pack で流し込むと、その行の高さ(28px)+上下余白(18/10px)を
        # タブ行が丸ごと下へずれる形で負担する=**タブの左側は空いているのに
        # 上に56pxの帯ができる**。ヘッダーの中身は右端に寄っているので、
        # place で浮かせてタブ行を同じ帯の左側へ入れれば、見た目を変えずに
        # 28px を表示領域へ返せる(ユーザー提示の完成イメージどおり)。
        # 重なるのは「タブ行の右側の空き」だけで、パネル(タブの中身)には
        # かからない(タブ上端28 + TAB_HEIGHT-TAB_OVERLAP=34 → パネル上端62px、
        # ヘッダーは18〜46px)。
        # **stacking**: place した兄弟でも、後から生成された TabView のほうが
        # 上に来る(Tkは生成順)。`_build_ui` の最後で `header.lift()` を呼ぶ
        # (透明フレームでも実体は不透明なので、上げないとボタンが隠れる)。
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.place(relx=1.0, x=-self.HEADER_RIGHT, y=self.HEADER_TOP,
                     anchor="ne")
        self.header = header

        # =156: **アプリ名の表示(「RVP」+「Random Voice Player」)を廃止**
        # (2026-08-16 ユーザー決定)。最初のバージョンから置いていたが、
        # 自分の名前を画面内で自己説明するアプリは珍しく、無いほうが
        # スタイリッシュ、という判断。**Windowsのタイトルバー
        # (root.title = "RVP - Random Voice Player")とアイコンは従来どおり**
        # 残すので、タスクバー・Alt+Tabでの識別性は落ちない。
        # ヘッダー行の高さはボタン(ヘルプ/Settings/接続ピル)に従って自然に
        # 縮み、タブ以下の表示領域がその分広がる(ユーザー選択)。

        # =157: 接続ピルの文言は「未接続 / 接続中... / 接続済み / 再接続中...」
        # と変わり、そのたびにヘッダー全体の幅が伸び縮みする(右寄せなので
        # 左へ伸びる)。タブと同じ帯に置くようになったので、**いちばん広い
        # 文言のときでもタブに掛からない幅**を確保する必要がある。文言の
        # 一覧をここに持っておき、`_ensure_header_width` が最大幅を実測する。
        self._pill_texts = [tr("● 未接続"), tr("● 接続中..."),
                            tr("● 接続済み"), tr("● 再接続中...")]
        self.conn_pill = ctk.CTkLabel(
            header, text=tr("● 未接続"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED,
            fg_color=("gray85", "gray20"),
            corner_radius=99, padx=12, pady=4,
        )
        self.conn_pill.pack(side="right")

        # =105: 設定はボタン1つに集約(旧: ☀/☾ボタン+言語コンボを廃止し
        # ポップアップへ移動。グラフ更新頻度=104もここに同居)。
        # 各StringVarはダイアログを開かなくても存在する(テスト・設定復元用)。
        self.lang_var = tk.StringVar(value="日本語" if LANG == "ja" else "English")
        self.appearance_var = tk.StringVar(value="")
        self.graph_fps_var = tk.StringVar(value="60fps")
        # =262: 背景イラストの表示ON/OFF(視聴側設定。既定=表示する)。
        self.show_bg_var = tk.BooleanVar(value=True)
        # =263: 背景イラストの透け具合(弱/中/強。既定=中)。
        self.bg_alpha_var = tk.StringVar(value=tr("中"))
        # =119: UIフォント。値は表示文言(「システム標準」or フォント名)。
        # 実際の適用は main() 起動時の appfont.apply()=**再起動で反映**
        # (CTkFontはウィジェット生成時にfamilyを確定するため。言語と同じ扱い)。
        self.font_var = tk.StringVar(value=appfont.DEFAULT_FAMILY)   # =293
        self._graph_interval_ms = 16          # =104: 既定60fps
        self._settings_win = None
        # =113: 表示は歯車記号「⚙」ではなく文言(記号はRobotoに字形が無く
        # 絵文字系フォントへ落ちる=112④)。
        # =135: 表記は**日本語表示でも英語 "Settings" に固定**(ユーザー決定)。
        # 日本語を読めない人が「設定」を読めず言語切替に辿り着けないため、
        # 言語切替への導線(このボタン→Language行)だけは常に英語にする。
        # 幅は言語によらず一定(CTkButtonは幅を自動調整しない=113)。
        self.settings_btn = ctk.CTkButton(
            header, text="Settings",
            width=74, height=26,
            font=ctk.CTkFont(size=13),
            fg_color=("gray80", "gray25"), hover_color=("gray72", "gray30"),
            # CTkButtonの既定文字色は明色(gray98)なので、ライトの
            # fg_color=gray80 の上だと文字が沈む(=112③と同じ話。記号1文字なら
            # 形で読めたが、文言にすると読みづらさが目立つ)。ライトだけ濃く。
            text_color=("gray10", "gray90"),
            command=self._open_settings,
        )
        self.settings_btn.pack(side="right", padx=(0, 10))

        # =118: ヘルプは編集画面のツールバーから**メイン画面のヘッダーへ移動**
        # (ユーザー決定=「設定」の左隣。視聴だけの人にも届く場所にする)。
        # 見た目は「設定」と揃える。side="right" は後にpackしたものが左へ来る。
        self._help_dlg = None
        self._editor_win = None      # =152: 編集画面は1つまで
        # =155: 履歴へ記録済みのシナリオパス(再生タブを出入りしても
        # 記録し直さないための印)。シナリオを読み込むと None へ戻る。
        self._recorded_path = None
        self.help_btn = ctk.CTkButton(
            header, text=tr("ヘルプ"),
            width=(66 if LANG == "ja" else 60), height=26,
            font=ctk.CTkFont(size=13),
            fg_color=("gray80", "gray25"), hover_color=("gray72", "gray30"),
            text_color=("gray10", "gray90"),
            command=self._open_help,
        )
        self.help_btn.pack(side="right", padx=(0, 8))
        self._update_theme_btn()

    def _open_help(self):
        """=118: ヘルプ画面(非モーダル)を開く。2つ目は開かず前面化する。

        HelpDialog は editor.py にあるので**押した時に遅延importする**
        (起動時に編集画面のモジュールを読み込まないため=起動時間対策)。
        """
        dlg = self._help_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.deiconify()
                    dlg.lift()
                    dlg.focus_set()
                    return
            except Exception:
                pass
            self._help_dlg = None
        from ..editor import HelpDialog
        self._help_dlg = HelpDialog(self.root)

    def _open_settings(self):
        """=105: 設定ポップアップ(外観/言語/描画更新頻度)を開く。

        非モーダルの小窓。既に開いていれば前面へ出すだけ。

        =108: 外観(ダーク/ライト)を切り替えると、**customtkinter が
        Windowsでタイトルバーの色を塗り替えるために小窓を withdraw し、
        5ms後に deiconify で戻す**(CTkToplevel._windows_set_titlebar_color)。
        この往復の最中に withdraw がもう一度走ると CTk は
        「ユーザーが閉じたのだ」と解釈して**畳んだまま復帰させない**。
        すると winfo_exists() は真のままなので、設定ボタンを押しても lift() する
        だけで何も出てこなくなる(ユーザー報告=「二度と反応しなくなる」)。
        対策として、生きている小窓でも**実際に見える状態かどうか**を確認し、
        見えないなら deiconify、それでも駄目なら作り直す。
        """
        win = self._settings_win
        if win is not None:
            try:
                alive = bool(win.winfo_exists())
            except Exception:
                alive = False
            if alive:
                try:
                    if win.state() != "normal":
                        win.deiconify()
                    win.lift()
                    win.focus_set()
                    win.update_idletasks()
                    if win.winfo_viewable():
                        return
                except Exception:
                    pass
                # 見える状態にできない=CTkのテーマ切替に巻き込まれた残骸。
                # 破棄して作り直す(ボタンが無反応にならないことを優先)。
                try:
                    win.destroy()
                except Exception:
                    pass
            self._settings_win = None
        win = ctk.CTkToplevel(self.root)
        self._settings_win = win
        win.title("Settings")   # =135: 言語切替導線は常に英語表記
        win.resizable(False, False)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", self._close_settings)
        # 設定ボタンの下あたりへ表示
        try:
            x = max(0, self.settings_btn.winfo_rootx() - 240)
            y = self.settings_btn.winfo_rooty() + 34
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(padx=16, pady=12)

        def row(label):
            r = ctk.CTkFrame(body, fg_color="transparent")
            r.pack(fill="x", pady=4)
            ctk.CTkLabel(r, text=label, width=130, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(side="left")
            return r

        # =152: 幅は他のコンボと揃える(150→130)。=136で表示名から「パステル」を
        # 外して最長ラベルが4文字になったのに、幅が広いままだった名残の解消。
        ctk.CTkOptionMenu(
            row(tr("外観")), variable=self.appearance_var, width=130, height=26,
            values=[tr("ダーク"), tr("ライト"),
                    *[tr(apptheme.THEME_LABELS[n]) for n in apptheme.THEMES]],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,     # =114: ライトは黒
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_appearance_select).pack(side="left")
        ctk.CTkOptionMenu(
            # =135: 行ラベルも常に英語 "Language"(選択肢は従来から
            # 各言語の自称表記=「日本語」/"English" で据え置き)
            row("Language"), variable=self.lang_var, width=130, height=26,
            values=["日本語", "English"],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_language_change).pack(side="left")
        # =119: UIフォント。選択肢は「システム標準」+ BIZ UDGothic
        # (Windows 10 1809以降に標準搭載・SIL OFL)。反映は再起動後(言語と同じ)。
        ctk.CTkOptionMenu(
            row(tr("フォント")), variable=self.font_var, width=130, height=26,
            values=[tr("システム標準"), *appfont.FONT_CHOICES],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_font_change).pack(side="left")
        # =104/=106: 描画更新頻度(グラフ+駆動値バー)。60fps=なめらか(推奨)/
        # 30fps=負荷半減。
        # 重い縮尺ではコスト適応ペーシングが自動で間隔を広げる
        ctk.CTkOptionMenu(
            row(tr("描画更新頻度")), variable=self.graph_fps_var,
            width=130, height=26, values=["60fps", "30fps"],
            fg_color=("gray80", "gray25"), button_color=("gray72", "gray30"),
            text_color=COMBO_TEXT,
            text_color_disabled=COMBO_TEXT_DISABLED,
            command=self._on_graph_fps_change).pack(side="left")
        # =262: 背景イラストの表示ON/OFF。シナリオが背景を指定していても、
        # 視聴する人がここでOFFにできる(最終決定は視聴側)。
        # =263: UI全体を半透明化する方式になったため「透け具合」も選べる。
        bg_row = row(tr("背景イラスト"))
        ctk.CTkSwitch(
            bg_row, text=tr("表示する"),
            variable=self.show_bg_var, width=110,
            font=ctk.CTkFont(size=12),
            progress_color=_clr.ACCENT,
            command=self._on_show_bg_change).pack(side="left")
        ctk.CTkLabel(bg_row, text=tr("透け具合"),
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(10, 4))
        ctk.CTkOptionMenu(
            bg_row, variable=self.bg_alpha_var, width=70, height=26,
            values=[tr("弱"), tr("中"), tr("強")],
            font=ctk.CTkFont(size=12),
            command=self._on_bg_alpha_change).pack(side="left")
        # =139: ライト/カラーテーマで gray62 は沈む(ユーザー指摘)→
        # ライト側は黒、ダーク側は従来の gray62(=LABEL と同じ組)。
        self._settings_note = ctk.CTkLabel(
            body, text="", font=ctk.CTkFont(size=11), text_color=LABEL,
            justify="left", anchor="w")
        self._settings_note.pack(fill="x", pady=(6, 0))

    def _close_settings(self):
        """設定ポップアップを閉じる(×ボタン)。参照を必ず落とす(=108)。"""
        win = self._settings_win
        self._settings_win = None
        try:
            if win is not None:
                win.destroy()
        except Exception:
            pass

    def _reassert_settings_win(self):
        """=108: 外観切替でCTkが畳んだままにした小窓を、見える状態へ戻す。

        CTk側の deiconify は after(5) で走るので、それより後に確認する。
        """
        win = self._settings_win
        if win is None:
            return
        try:
            if not win.winfo_exists():
                self._settings_win = None
                return
            if win.state() != "normal" or not win.winfo_viewable():
                win.deiconify()
                win.lift()
        except Exception:
            pass

    def _on_appearance_select(self, choice: str):
        """=105/=134/=151: 設定の外観メニュー(ダーク/ライト/カラー6色)。

        **=151で全て即時反映**になった(再起動は不要)。6色は「ライトの他
        バリエーション」なので、8つは排他選択: 色を選べばライト基調のその色、
        ダーク/ライトを選べばカラーテーマは解除される。
        """
        picked = None
        for name in apptheme.THEMES:
            if choice == tr(apptheme.THEME_LABELS[name]):
                picked = name
                break
        if picked is not None:
            if picked == self.color_theme:
                return
            self._apply_theme_now("light", picked)
            return
        # ダーク/ライトが選ばれた=カラーテーマは解除
        mode = "dark" if choice == tr("ダーク") else "light"
        if mode == self.appearance_mode and not self.color_theme:
            return
        self._apply_theme_now(mode, "")

    def _show_settings_note(self, note: str):
        """設定ポップアップ内の案内欄へ表示する(無ければダイアログ)。"""
        lbl = getattr(self, "_settings_note", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=note)
        else:
            messagebox.showinfo("Settings", note, parent=self.root)

    def _on_show_bg_change(self):
        """=262: 背景イラストの表示ON/OFFを反映し設定へ保存する。"""
        self.bg_art.set_user_enabled(self.show_bg_var.get())
        self.save_app_config()

    def _bg_alpha_level(self) -> str:
        """現在の透け具合コンボの表示名 → 保存値(weak/mid/strong)。"""
        v = self.bg_alpha_var.get()
        for ja, level in self._BG_ALPHA_LABELS:
            if v == tr(ja):
                return level
        return "mid"

    def _on_bg_alpha_change(self, _choice=None):
        """=263: 背景イラストの透け具合を反映し設定へ保存する。"""
        self.bg_art.set_alpha_level(self._bg_alpha_level())
        self.save_app_config()

    def _on_graph_fps_change(self, choice: str):
        """=104/=106: 描画更新頻度(60fps/30fps)を反映し設定へ保存する。

        =229: **開いているアイテムレビュー画面にも即反映**する
        (レビュー画面も同じ設定に従うようになったため)。
        """
        self._graph_interval_ms = 33 if "30" in str(choice) else 16
        self.save_app_config()
        try:
            win = getattr(self, "_editor_win", None)
            dlg = getattr(win, "_review_dlg", None) if win else None
            if dlg is not None and dlg.winfo_exists():
                dlg.refresh_ms = self._graph_interval_ms
        except Exception:
            pass

    def _toggle_theme(self):
        """外観を ダーク⇄ライト で即時切替する。

        =151: カラーテーマ選択中に呼ばれたら**解除して**素のダーク/ライトへ。
        (6色は「ライトの他バリエーション」=2026-08-15ユーザー決定)
        """
        mode = "light" if self.appearance_mode == "dark" else "dark"
        self._apply_theme_now(mode, "")

    def _refresh_arrow_icons(self):
        """ページ切替◀▶の三角アイコンを現在のテーマ色で描き直す(=111)。

        tk.PhotoImage は CTkImage と違って明暗の切替を自前で行う必要がある。
        画像への参照は self._arrow_photos に残しておく(参照が切れると
        tk 側で破棄されてボタンが空になる)。
        """
        # ctk.get_appearance_mode() は起動直後だとまだ "System" 判定のことが
        # あるので、RVPが持っている現在値を正とする。
        dark = getattr(self, "appearance_mode", "dark") == "dark"
        for name, btn in (("left", getattr(self, "play_prev_btn", None)),
                          ("right", getattr(self, "play_next_btn", None))):
            if btn is None:
                continue
            try:
                img = _triangle_photo(name, dark=dark)
                self._arrow_photos[name] = img      # 参照を保持
                # CTkImage以外を渡すと「HighDPIで拡縮できない」旨の警告が
                # 毎回出る。13pxの小さな三角なので実害はなく、テーマ切替の
                # たびにコンソールが荒れるほうが困るので黙らせる。
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    btn.configure(image=img)
            except Exception:
                # 万一描けなくても操作性は落とさない(文字へフォールバック)
                try:
                    btn.configure(text="<" if name == "left" else ">")
                except Exception:
                    pass

    def _refresh_slider_theme(self):
        """テーマ切替時に補正スライダー(tk.Canvas)の背景を再描画で追従させる。

        CTkウィジェットは set_appearance_mode で自動追従するが、素の tk.Canvas
        (RangeSlider)は自前で背景色を切り替えるため明示的に再描画する。
        """
        for g in getattr(self, "track_groups", {}).values():
            for key in ("slider", "bar"):     # =151 駆動値バー(ZoneBar)も
                w = g.get(key)
                if w is not None:
                    try:
                        w._redraw()
                    except Exception:
                        pass
        gv = getattr(self, "graph_view", None)
        if gv is not None:
            gv._redraw()          # =69 グラフも自前で色を切り替える

    def _refresh_theme_widgets(self):
        """=151: テーマ切替のあと、CTk任せにできない部分を追従させる。

        `apptheme.switch()` は全**CTk**ウィジェットを塗り直すが、素の
        tk.Canvas に自前描画しているもの(補正スライダー・駆動値バー・
        グラフ・イベント遷移図)と tk.PhotoImage(◀▶の三角)は対象外なので、
        ここで明示的に描き直す。開いている別ウィンドウ(編集画面など)にも
        `refresh_theme()` があれば波及させる。
        """
        try:
            self._refresh_slider_theme()
            self._refresh_arrow_icons()
        except Exception:
            logger.exception("refresh theme widgets failed")
        # イベント遷移図/ステート図。表示中でなければ次に開いたときに
        # _on_play_page_shown() が強制再描画するので、ここは表示中だけでよい。
        try:
            self._map_sig = None
            self._update_event_map()
        except Exception:
            logger.exception("refresh event map failed")
        # 開いている Toplevel(編集画面・ヘルプ等)へ波及させる(=149の
        # 全Toplevelアイコン適用と同じ考え方)。
        for w in list(self.root.winfo_children()):
            fn = getattr(w, "refresh_theme", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    logger.exception("refresh theme of %r failed", w)

    def _apply_theme_now(self, mode: str, theme: str):
        """=151: 外観(ダーク/ライト/カラーテーマ)を**その場で**適用する。

        カラーテーマは「ライトの他バリエーション」として扱う(2026-08-15
        ユーザー決定)。したがって theme を選べばライト基調になり、
        ダーク/ライトを選べばカラーテーマは解除される。
        """
        theme = theme or ""
        mode = "light" if theme else mode
        self.color_theme = theme
        self.appearance_mode = mode
        # 順番: 先にモードを合わせてから配色を切り替える(どちらも全
        # ウィジェットの塗り直しを走らせるので、逆だと一瞬素の色が見える)。
        if ctk.get_appearance_mode().lower() != mode:
            ctk.set_appearance_mode(mode)
        apptheme.switch(theme)
        self._refresh_theme_widgets()
        self._update_theme_btn()
        self.save_app_config()
        # =108: CTkがタイトルバー色の塗り替えで小窓を withdraw→deiconify
        # することがある(Windowsのみ)。設定窓の見え方を後追いで確認する。
        try:
            self.root.after(60, self._reassert_settings_win)
        except Exception:
            pass

    def _update_theme_btn(self):
        # =105: 旧☀/☾ボタンは廃止。設定ダイアログの外観メニューへ現在値を反映
        var = getattr(self, "appearance_var", None)
        if var is not None:
            if self.color_theme:      # =134: パステル選択中はテーマ名を表示
                var.set(tr(apptheme.THEME_LABELS.get(self.color_theme, "")))
            else:
                var.set(tr("ダーク") if self.appearance_mode == "dark"
                        else tr("ライト"))

    def _on_language_change(self, choice: str):
        new_lang = "ja" if choice == "日本語" else "en"
        if new_lang == LANG:
            return
        set_language(new_lang)
        note = ("言語設定を保存しました。アプリの再起動後に反映されます。\n"
                "Language preference saved. "
                "It takes effect after restarting the app.")
        # =105: 設定ダイアログが開いていればインラインで案内(モーダル回避)
        lbl = getattr(self, "_settings_note", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=note)
        else:
            messagebox.showinfo("Language / 言語", note, parent=self.root)

    def _on_font_change(self, choice: str):
        """=119: 設定のフォントメニュー。保存のみ行い、反映は再起動後。

        CTkFont はウィジェット生成時に family を確定するため実行中は
        切り替えられない(言語設定と同じ扱い)。選んだフォントがこの端末に
        無い場合はその旨を案内する(保存はする=別の端末では有効になり得る)。
        """
        fam = "" if choice == tr("システム標準") else choice
        cfg = load_config()
        # =293: キーが無い(初回起動=既定 BIZ UDPGothic を表示中)ときは
        # 選択値を必ず保存して明示化する(システム標準を選んでも次回
        # 既定へ戻らないように)。
        if "font_family" in cfg and cfg.get("font_family") == fam:
            return
        # OptionMenu経由なら var 設定済みだが、直接呼ばれても成立するように
        self.font_var.set(choice)
        self.save_app_config()
        note = tr("フォント設定を保存しました。アプリの再起動後に反映されます。")
        if fam and not appfont.resolve(self.root, fam):
            note += "\n" + tr("(このフォントはこの端末に見つかりません。"
                              "見つかるまでは標準フォントで表示します)")
        lbl = getattr(self, "_settings_note", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=note)
        else:
            messagebox.showinfo("Settings", note, parent=self.root)
