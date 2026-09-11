"""ヘルプ・ライセンス画面(HelpDialog / LicenseDialog と本文ブロック)。"""
from __future__ import annotations

import customtkinter as ctk
import os
import sys
from ..winstate import WindowMemory
from .. import __version__
from ..i18n import tr

from .common import MUTED, TEXT_HEAD, _front_window
from . import common as _clr   # =301: テーマ追従する色定数は定義元を参照


def _help_blocks():
    """ヘルプ本文(=244全面改稿・=245推敲・ユーザー原稿)。1表示行=1ソース行。

    =118〜=161の詳細なTIPS(34項目)は「細かすぎて読まれない」ため廃止し、
    概要レベルの4章構成へ更改した(2026-08-27 ユーザー決定)。=245で
    ユーザー推敲を反映: ①導入文の差し替え ②機能一覧の各項目間に空行
    (「主な機能」行は削除) ③視聴方法の文言変更(字下げなし・空行あり)
    ④「シナリオ編集」→「シナリオについて」+1文目追加(空行あり)。
    """
    return (
        ("h1", tr("本アプリについて"), ""),
        # =246: 空行の位置はユーザー指定(1行目の後/段落の後/「以下の機能が
        # あります。」の前後)。前後の空行を正確に出すため、導入文と機能一覧は
        # 1つのtextブロックに統合した。
        ("text", "", tr(
            "ルールや手順を組み合わせてプレイリストを構築できる、\n"
            "プログラマブルな音声プレイヤーです。\n"
            "\n"
            "分岐・ランダム・ゲーム要素が含まれる音声作品に対して、\n"
            "あらかじめ視聴ルールを\"シナリオ\"として定義することで、\n"
            "手動で切り替える手間を減らし、自動で進行させることができます。\n"
            "\n"
            "以下の機能があります。\n"
            "\n"
            "・進行分岐\n"
            "\n"
            "・重み付き抽選\n"
            "\n"
            "・3チャンネル同時再生\n"
            "\n"
            "・選択肢表示\n"
            "\n"
            "・変数\n"
            "\n"
            "・すごろく\n"
            "\n"
            "・動画再生(mpv)\n"
            "\n"
            "・ハプティクスデバイス連携\n"
            "\n"
            "・スクリプト制作支援")),
        ("h1", tr("視聴方法"), ""),
        ("text", "", tr(
            "１．シナリオタブでシナリオを選択、または新規作成\n"
            "\n"
            "２．再生タブでシナリオ視聴")),
        ("h1", tr("シナリオについて"), ""),
        ("text", "", tr(
            "本アプリにおいて、ユーザーが視聴する対象がシナリオです。\n"
            "\n"
            "シナリオの構造は\n"
            "「シナリオ - イベント - チャンネル - アイテム」\n"
            "となっています。")),
        ("h2", tr("アイテム"), tr(
            "音声ファイルのことです。\n"
            "シナリオを構成する最小単位です。")),
        ("h2", tr("チャンネル"), tr(
            "複数のアイテムを保持し、\n"
            "再生順序などの定義を持ちます。")),
        ("h2", tr("イベント"), tr(
            "3つのチャンネル(L/C/R)を保持し、\n"
            "終了条件や遷移先イベントなどの定義を持ちます。")),
        # ※ステート形式イベントは、イベント配下の補足として item 階層に置く
        # (ユーザー決定)。
        ("item", tr("※ステート形式イベント"), tr(
            "イベント内に複数の状態(ステート)を保持できます。\n"
            "イベントと同様、各ステートは3つのチャンネル(L/C/R)を持ち、\n"
            "遷移条件・遷移先ステートなどの定義を持ちます。")),
        ("h2", tr("シナリオ"), tr(
            "イベントの集まりであり、視聴の対象です。\n"
            "実体はJSON形式のファイルです。")),
        ("h1", tr("ハプティクスデバイス連携"), ""),
        ("text", "", tr(
            "アイテムにスクリプト(funscript/csv)を紐づけることで、\n"
            "再生に同期してハプティクスデバイスを動かすことができます。\n"
            "シリアルポート接続とBluetooth接続(Intiface Central経由)が利用可能です。")),
    )


def _help_sections():
    """互換API: 見出しと本文を持つブロックだけを (見出し, 本文) で返す。"""
    return tuple((title, body) for kind, title, body in _help_blocks()
                 if kind != "text" and title)


def _license_blocks():
    """ライセンス・クレジット画面の中身を (種別, 見出し, 本文) で返す。

    種別は _help_blocks() と同じ "h1"/"h2"/"item"/"text" で、LicenseDialog が
    同じ体裁で描画する。粒度は「要約+クレジット+同梱ファイル案内」(ユーザー
    決定)。各ライセンスの全文は載せず **THIRD-PARTY-LICENSES.txt を参照
    させる方式で確定**(=258: 2026-08-30 ユーザー決定。かつて予定していた
    「画面内に全文表示」は行わない)。
    =136: COEIROINK の節は削除(サンプル音声は GitHub 配布物に同梱せず
    Ci-en 記事で独立配布するため、RVP 本体のライセンス画面では言及しない)。

    表示専用。ライブラリの種別・著作権表記は実際のパッケージから確認した値
    (customtkinter=MIT/(c)2023 Tom Schimansky、buttplug-py=BSD-3/(c)2022
    Siege-Wizard、pyserial=BSD-3/(c)2001-2020 Chris Liechti、pygame-ce=
    LGPL-2.1)。制作者表記は「Torp」で確定済み(=140。LICENSE・i18n・
    test_license_ui も同名で整合)。
    """
    return (
        ("h1", tr("ライセンス・クレジット"), ""),
        ("text", "", tr(
            "RVP(Random Voice Player)は MITライセンスのオープンソースソフトウェアです。\n"
            "本ソフトウェアは、以下のソフトウェア・素材を利用しています。")),
        # =292: 公開バージョン(rvp.__version__)を先頭行に出す。
        ("h2", tr("RVP 本体"), tr(
            "バージョン {0}\n"
            "MIT License\n"
            "© 2026 Torp").format(__version__)),
        ("h2", tr("使用しているソフトウェア"), ""),
        # =140: 各説明は体言止め(「〜に使用しています。」の術語を削除=ユーザー
        # 指定)。mpv・フォントも item(青文字)としてこの節に並べる。
        ("item", "pygame-ce", tr(
            "音声再生\n"
            "LGPL-2.1 / © pygame-ce developers")),
        ("item", "CustomTkinter", tr(
            "画面表示\n"
            "MIT License / © 2023 Tom Schimansky")),
        # =262: 背景イラスト機能でPillowを直接importするようになった
        # (従来もCustomTkinterの必須依存として同梱されていた)。
        ("item", "Pillow", tr(
            "背景イラスト表示(画像の読み込み・加工)\n"
            "MIT-CMU License / © 2010 Jeffrey A. Clark and contributors")),
        ("item", "buttplug-py", tr(
            "デバイス通信(Buttplug)\n"
            "BSD-3-Clause / © 2022 Siege-Wizard")),
        ("item", "tkinterdnd2 / tkdnd", tr(
            "編集画面ドラッグ&ドロップ\n"
            "MIT License / tkdnd は BSD スタイルライセンス")),
        ("item", "pyserial", tr(
            "TCodeデバイス通信\n"
            "BSD-3-Clause / © 2001-2020 Chris Liechti")),
        ("item", tr("Python / Tcl・Tk"), tr(
            "実行環境\n"
            "PSF License / Tcl・Tk License(BSD スタイル)")),
        ("item", "mpv", tr("動画再生")),
        ("item", tr("フォント"), tr(
            "BIZ UDゴシック(SIL Open Font License)")),
    )


class HelpDialog(ctk.CTkToplevel):
    """アプリ概要を表示する非モーダルのヘルプ画面(=244で概要レベルへ更改)。

    編集画面と交互に読めるよう grab_set は行わない(非モーダル)。
    全項目を1枚のスクロールで表示する。
    """

    def __init__(self, master):
        super().__init__(master)
        # =244: TIPS廃止に伴い、タイトルは「ヘルプ」だけにする
        self.title(tr("ヘルプ"))
        # =115: 前回のサイズ・位置を復元(無ければ従来の760x600・親任せの位置)
        self.winmem = WindowMemory(self, "help")
        if not self.winmem.restore():
            self.geometry("760x600")
        self.minsize(520, 360)
        self.winmem.watch()
        self.winmem.install_close_hook()

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        # =116: 3階層(章 h1 / 節 h2 / 項目 item)+ 見出しのない本文 text。
        # 段差は「文字の大きさ・太さ・色」と「左インデント」の2つで付ける。
        self.section_titles = []
        first_h1 = True
        for kind, title, text in _help_blocks():
            if kind == "h1":
                if not first_h1:
                    # 章の区切り線(空のCTkFrameは200pxを要求するので明示指定)
                    sep = ctk.CTkFrame(body, height=2, corner_radius=0,
                                       fg_color=("gray75", "gray35"))
                    sep.pack(fill="x", padx=8, pady=(22, 0))
                first_h1 = False
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=16, weight="bold"),
                    text_color=_clr.ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=660).pack(fill="x", padx=8, pady=(14, 4))
            elif kind == "h2":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                    text_color=TEXT_HEAD, anchor="w", justify="left",
                    wraplength=650).pack(fill="x", padx=16, pady=(14, 2))
            elif kind == "item":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=_clr.ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=640).pack(fill="x", padx=24, pady=(10, 1))
            if not text:
                continue
            indent = {"h1": 16, "h2": 26, "item": 34}.get(kind, 16)
            ctk.CTkLabel(
                body, text=text, font=ctk.CTkFont(size=12),
                anchor="w", justify="left",
                wraplength=670 - indent).pack(fill="x", padx=indent,
                                              pady=(0, 2))


        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("閉じる"), width=90, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._close).pack(side="right")
        # ライセンス・クレジットは別窓(LicenseDialog)へ。ヘルプ本文の目的は
        # アプリの説明なので、性質の違う法的テキストは分ける
        # (ユーザー決定=案A)。side="right" は後にpackした方が左へ来る。
        self._license_dlg = None
        ctk.CTkButton(btns, text=tr("ライセンス"), width=110, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._open_license).pack(side="right",
                                                       padx=(0, 8))

        _front_window(self)
        # 非モーダル: grab_set しない(編集画面と交互に操作できる)

    def _open_license(self):
        """ライセンス・クレジット画面(非モーダル)を開く。既に開いていれば
        前面化するだけ(2つ目は開かない)。ヘルプを閉じても残せるよう、親は
        ヘルプの親(=メイン画面)にする。"""
        dlg = self._license_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.deiconify()
                    dlg.lift()
                    dlg.focus_set()
                    return
            except Exception:
                pass
            self._license_dlg = None
        self._license_dlg = LicenseDialog(self.master)

    def _close(self):
        """=115: 閉じる前にサイズ・位置を保存する(×ボタンも同じ経路)。"""
        try:
            self.winmem.save_now()
        except Exception:
            pass
        self.destroy()


def third_party_licenses_path():
    """THIRD-PARTY-LICENSES.txt の探索(=139)。見つからなければ None。

    exe(PyInstaller onedir)では exe と同じフォルダ、開発環境では
    リポジトリルート(rvp パッケージの1つ上)に置かれる。
    """
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(sys.executable),
                                  "THIRD-PARTY-LICENSES.txt"))
    cands.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),   # =301: rvp/editor/ から2段上
        "THIRD-PARTY-LICENSES.txt"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def third_party_licenses_text():
    """THIRD-PARTY-LICENSES.txt の全文(=139)。読めなければ None。"""
    path = third_party_licenses_path()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


class LicenseDialog(ctk.CTkToplevel):
    """ライセンス・クレジットを表示する非モーダルのダイアログ。

    HelpDialog の「ライセンス」ボタンから開く(案A)。体裁は HelpDialog と
    同じ3階層(h1/h2/item)+本文。中身は _license_blocks()。
    =139: 末尾に THIRD-PARTY-LICENSES.txt の全文を表示する(ユーザー決定
    「画面には txt と同じ全文が出る」)。全文は tr() に通さない(英語の
    法的文書=翻訳対象外)。表示は読み取り専用の CTkTextbox(内側スクロール)。
    ファイルが見つからないときは案内文を出す。
    """

    def __init__(self, master):
        super().__init__(master)
        self.title(tr("ライセンス・クレジット"))
        # =115: 前回のサイズ・位置を復元(無ければ従来の760x600)。
        self.winmem = WindowMemory(self, "license")
        if not self.winmem.restore():
            self.geometry("720x560")
        self.minsize(480, 340)
        self.winmem.watch()
        self.winmem.install_close_hook()

        body = ctk.CTkScrollableFrame(self, corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        self.section_titles = []
        first_h1 = True
        for kind, title, text in _license_blocks():
            if kind == "h1":
                if not first_h1:
                    sep = ctk.CTkFrame(body, height=2, corner_radius=0,
                                       fg_color=("gray75", "gray35"))
                    sep.pack(fill="x", padx=8, pady=(22, 0))
                first_h1 = False
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=16, weight="bold"),
                    text_color=_clr.ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=660).pack(fill="x", padx=8, pady=(14, 4))
            elif kind == "h2":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                    text_color=TEXT_HEAD, anchor="w", justify="left",
                    wraplength=650).pack(fill="x", padx=16, pady=(14, 2))
            elif kind == "item":
                self.section_titles.append(title)
                ctk.CTkLabel(
                    body, text=title, font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=_clr.ACCENT_TEXT, anchor="w", justify="left",
                    wraplength=640).pack(fill="x", padx=24, pady=(10, 1))
            if not text:
                continue
            indent = {"h1": 16, "h2": 26, "item": 34}.get(kind, 16)
            ctk.CTkLabel(
                body, text=text, font=ctk.CTkFont(size=12),
                anchor="w", justify="left",
                wraplength=670 - indent).pack(fill="x", padx=indent,
                                              pady=(0, 2))

        # ---- =139: THIRD-PARTY-LICENSES.txt の全文 ----
        sep = ctk.CTkFrame(body, height=2, corner_radius=0,
                           fg_color=("gray75", "gray35"))
        sep.pack(fill="x", padx=8, pady=(22, 0))
        ctk.CTkLabel(
            body, text=tr("サードパーティライセンス全文"),
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=_clr.ACCENT_TEXT, anchor="w", justify="left",
            wraplength=660).pack(fill="x", padx=8, pady=(14, 4))
        full = third_party_licenses_text()
        if full is None:
            ctk.CTkLabel(
                body, text=tr(
                    "THIRD-PARTY-LICENSES.txt が見つかりませんでした。\n"
                    "配布物に同梱されているファイルを参照してください。"),
                font=ctk.CTkFont(size=12), anchor="w", justify="left",
                wraplength=650).pack(fill="x", padx=16, pady=(0, 8))
            self.tp_textbox = None
        else:
            # 全文(約1900行)はラベルではなく読み取り専用Textboxで表示する
            # (ラベル1900個は生成が重い。Textboxなら1ウィジェット+内側
            # スクロールで済む。英語の法的文書=tr()非対象)。
            self.tp_textbox = ctk.CTkTextbox(
                body, corner_radius=8, height=380, wrap="word",
                font=ctk.CTkFont(size=11))
            self.tp_textbox.pack(fill="x", padx=16, pady=(2, 10))
            self.tp_textbox.insert("1.0", full)
            self.tp_textbox.configure(state="disabled")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btns, text=tr("閉じる"), width=90, height=30,
                      fg_color="transparent", border_width=1,
                      border_color=MUTED, text_color=("gray20", "gray85"),
                      hover_color=("gray85", "gray25"),
                      command=self._close).pack(side="right")

        _front_window(self)

    def _close(self):
        try:
            self.winmem.save_now()
        except Exception:
            pass
        self.destroy()
