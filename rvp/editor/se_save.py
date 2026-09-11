"""シナリオ編集: 保存(検証・素材の相対化/コピー・上書き警告の走査)(mixin)。"""
from __future__ import annotations

import copy
import json
import os
from ..scenario import Scenario, auto_bind_tracks
from ..i18n import tr

from .common import _dialog_initialdir, _remember_dialog_dir, _title_from_path
from .paths import _format_bytes, _map_item_paths, _rebase_scenario_path
from ._hooks import _pkg


class _ScenarioEditorSaveMixin:
    """ScenarioEditor の mixin(=301 分割)。保存(検証・素材の相対化/コピー・上書き警告の走査)"""

    def script_usage(self, fs_abs: str) -> list[str]:
        """シナリオ全体で指定スクリプトを使っている箇所の一覧(=170)。

        スクリプト編集の上書き保存の警告(仕様 14a)。検出範囲=**編集画面で
        開いているシナリオ全体**(未保存の編集内容込み)。パネルの内容を
        先にモデルへ反映してから raw dict を走査する(自動紐づけも解決する)。
        戻り値: ["イベント名 / L", "イベント名:ステート名 / C", ...]
        """
        if self.selected and self.selected in self.data.get("events", {}):
            try:
                self._apply_panel()     # 失敗しても data の現状で走査する
            except Exception:
                pass
        target = os.path.normcase(os.path.abspath(fs_abs))

        def item_paths(item):
            if isinstance(item, str):
                item = {"audio": item}
            if not isinstance(item, dict):
                return
            tracks = item.get("tracks")
            if isinstance(tracks, list):
                for t in tracks:
                    fs = (t or {}).get("funscript") if isinstance(t, dict) \
                        else None
                    if fs:
                        yield os.path.join(self.base_dir, fs)
                return
            if "funscript" in item:
                fs = item.get("funscript")
                if fs:                       # None = デバイスなし(=明示)
                    yield os.path.join(self.base_dir, fs)
                return
            src = item.get("audio") or item.get("video")
            if src:
                for _t, p in auto_bind_tracks(
                        os.path.join(self.base_dir, src)):
                    yield p

        uses: list[str] = []

        def scan_channels(label, raw):
            for ch_id, ch_raw in (raw.get("channels") or {}).items():
                if not isinstance(ch_raw, dict):
                    continue
                for item in ch_raw.get("items") or []:
                    for p in item_paths(item):
                        if os.path.normcase(os.path.abspath(p)) == target:
                            uses.append("{0} / {1}".format(label, ch_id))

        for ev_id, raw in (self.data.get("events") or {}).items():
            if not isinstance(raw, dict):
                continue
            if "states" in raw:
                for st_id, st_raw in (raw.get("states") or {}).items():
                    if isinstance(st_raw, dict):
                        scan_channels("{0}:{1}".format(ev_id, st_id), st_raw)
            else:
                scan_channels(ev_id, raw)
        return uses

    def _save(self):
        if not self.path:
            return self._save_as()
        if self._prepare_for_save() is not None:
            return
        self._save_with_path_check(self.path)

    def _save_as(self):
        # 先に検証する。エラーがあれば保存ダイアログを出さずにその場で表示し、
        # 確実に保存できるときだけダイアログを表示する。
        if self._prepare_for_save() is not None:
            return
        path = _pkg().filedialog.asksaveasfilename(
            title=tr("シナリオファイルを保存"),
            initialdir=_dialog_initialdir(self.base_dir),
            defaultextension=".json",
            filetypes=[(tr("シナリオファイル"), "*.json")], parent=self)
        if not path:
            return
        _remember_dialog_dir(path)
        # 保存先が別フォルダでも、書き出し時に全パスを保存先基準へ付け替える
        # (保存先の内=相対 / 外=絶対)。外部素材(=絶対パス)が残る場合は
        # _save_with_path_check が警告を出し、ユーザーが扱いを選ぶ。
        self._save_with_path_check(path)

    def _save_with_path_check(self, path: str):
        """保存前に、絶対パスとして書き出される素材の有無を確認する。

        無ければそのまま保存(従来どおり)。有れば警告を表示し、
        「絶対パスのまま保存 / 相対パスに書き換えて保存(素材コピー) /
        保存しない」の3択をユーザーへ委ねる。
        """
        save_dir = os.path.dirname(os.path.abspath(path))
        externals = self._external_paths_for_save(save_dir)
        if not externals:
            self._do_save(path)
            return
        lines = [tr("以下のファイルは保存先フォルダの外にあるため、"
                    "絶対パスで保存されます。このシナリオを配布すると"
                    "他の環境では再生できません。")]
        lines += [p for _k, p in externals]
        # =310: 件数を見出しへ(本文が長くスクロール枠になっても総数が分かる)
        self._render_message(
            "warn", tr("外部の素材ファイルがあります")
            + tr("({0}件)").format(len(externals)), lines,
            buttons=[
                (tr("はい(相対パスに書き換えて保存する)"),
                 lambda: self._confirm_flatten(path), "primary"),
                (tr("はい(絶対パスのまま保存する)"),
                 lambda: (self._clear_message(), self._do_save(path)),
                 "ghost"),
                (tr("いいえ(保存しない)"), self._clear_message, "ghost"),
            ])

    @staticmethod
    def _externals_size_text(externals) -> str:
        """コピー対象の合計サイズ(と動画の内訳)の説明文を作る(=50)。

        動画はサイズが大きいため、コピー前に総量を提示する(ユーザー決定
        2026-07-25)。サイズが取得できないファイルは 0 として扱う。
        """
        total = video = 0
        for kind, p in externals:
            try:
                n = os.path.getsize(p)
            except OSError:
                n = 0
            total += n
            if kind == "video":
                video += n
        if video:
            return tr("コピーするファイルの合計サイズ: {0}"
                      "(うち動画 {1})。動画は容量が大きいため、"
                      "保存先の空き容量にご注意ください。").format(
                _format_bytes(total), _format_bytes(video))
        return tr("コピーするファイルの合計サイズ: {0}").format(
            _format_bytes(total))

    def _prepare_for_save(self) -> str | None:
        """保存ダイアログを出す前の事前検証。エラーメッセージ(None=OK)。

        エラーがある場合はその場で画面にエラーを表示して非Noneを返す。
        呼び出し側はこの戻り値が非Noneなら保存ダイアログを開かない
        (＝ダイアログは確実に保存できるときだけ表示される)。
        """
        # 1) パネル内容をモデルへ書き戻す(空チャンネル等の構造エラー検出)
        err = self._apply_panel() if self.selected else None
        if err:
            self._report("error", tr("編集エラー"), err)
            return err
        # 2) 参照ファイル(音声/funscript/CSV)の存在などを保存先に依らず
        #    base_dir 基準で事前検証する(Scenario.load 相当)。実際の保存は
        #    保存先基準へ付け替えたコピーで行うが、参照の存在は場所に依らない。
        tmp = os.path.join(self.base_dir, ".rvp_save_check.tmp")
        try:
            rebased = self._rebased_data_for_save(self.base_dir)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rebased, f, ensure_ascii=False)
            sc = Scenario.load(tmp)
            # =130: 読み込み警告(担当種別のトラックが無いチャンネル等)は
            # 保存を止めずに知らせる(エラーとは別枠)
            if sc.load_warnings:
                self._report("warn", tr("保存しますが、注意点があります")
                             + tr("({0}件)").format(len(sc.load_warnings)),
                             "\n".join(sc.load_warnings))
        except Exception as e:
            self._report(
                "error", tr("保存できません"),
                tr('シナリオの検証でエラーが見つかりました:\n{0}').format(e))
            return str(e)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None

    def _rebased_data_for_save(self, new_base: str) -> dict:
        """保存用に、全アイテムの音声/funscript/CSVパスを new_base 基準へ
        付け替えたディープコピーを返す(self.data は変更しない)。"""
        data = copy.deepcopy(self.data)
        _map_item_paths(
            data,
            lambda _k, p: _rebase_scenario_path(p, self.base_dir, new_base))
        return data

    def _external_paths_for_save(self, save_dir: str) -> list[tuple[str, str]]:
        """保存先 save_dir 基準へ付け替えた結果、絶対パスとして書き出される
        (=保存先フォルダの外にある)素材の一覧を返す。

        戻り値: [(kind, 絶対パス)]。kind は "audio" / "fs"。
        重複(同一ファイルへの複数参照)は除去し、パス順で返す。
        """
        rebased = self._rebased_data_for_save(save_dir)
        found: dict[str, tuple[str, str]] = {}

        def collect(kind, p):
            if os.path.isabs(p):
                key = os.path.normcase(os.path.normpath(p))
                if key not in found or kind == "audio":
                    # 同一ファイルがaudio/fs両方で参照されたらaudio扱いを優先
                    # (自動紐づけの同伴コピー判定のため)
                    found[key] = (kind, os.path.normpath(p))
            return p

        _map_item_paths(rebased, collect)
        return sorted(found.values(), key=lambda kv: kv[1])

    def _do_save(self, path: str) -> bool:
        """保存を実行する。成功でTrue、エラー表示して中止でFalse。"""
        self._commit_pending_renames()   # =100①(保存ボタンもフォーカスを取らない)
        err = self._apply_panel() if self.selected else None
        if err:
            self._report("error", tr("編集エラー"), err)
            return False
        # タイトルは保存先JSONファイル名から確定する(ファイル名が正)
        self.data["title"] = _title_from_path(path)
        # =250: 紹介文はダイアログで反映済みの _detail_text から書き出す
        detail = self._detail_text.rstrip("\n")
        if detail:
            self.data["detail"] = detail
        else:
            self.data.pop("detail", None)
        # =252: デバイス連動フラグ。ON=キーを書かない(省略=ON=旧シナリオと
        # 同じ書式のまま) / OFF=false を明示する。
        if self.device_enabled:
            self.data.pop("device_enabled", None)
        else:
            self.data["device_enabled"] = False
        # =256: BGMフラグ。省略の意味が逆(省略=OFF)なので、ON=true明示 /
        # OFF=キーを書かない。
        if self.bgm_enabled:
            self.data["bgm_enabled"] = True
        else:
            self.data.pop("bgm_enabled", None)
        # トップレベルのキー順を title, detail, background, bgm_enabled,
        # device_enabled, start, events, その他 に整える(=262でbackground追加)
        ordered = {}
        for key in ("title", "detail", "background", "bgm_enabled",
                    "device_enabled", "start", "events"):
            if key in self.data:
                ordered[key] = self.data[key]
        for key, v in self.data.items():
            if key not in ordered:
                ordered[key] = v
        self.data = ordered

        # 書き出しは保存先フォルダ基準へパスを付け替えたコピーで行う
        # (self.data 自体は base_dir 基準のまま保持=編集を続けても整合)。
        save_dir = os.path.dirname(os.path.abspath(path))
        out_data = self._rebased_data_for_save(save_dir)
        payload = json.dumps(out_data, ensure_ascii=False, indent=2)

        # 一時ファイルに書いてバリデーション
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            _sc = Scenario.load(tmp)  # 参照ファイルの存在チェック等
            # =130: 読み込み警告(担当種別のトラックが無いチャンネル等)は
            # 保存を止めずに知らせる(エラーとは別枠)
            if _sc.load_warnings:
                self._report("warn", tr("保存しますが、注意点があります")
                             + tr("({0}件)").format(len(_sc.load_warnings)),
                             "\n".join(_sc.load_warnings))
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            self._report(
                "error", tr("保存できません"),
                tr('シナリオの検証でエラーが見つかりました:\n{0}').format(e))
            return False
        try:
            os.replace(tmp, path)
        except OSError as e:
            # 保存先が使用中(他プロセスが掴んでいる)・読み取り専用・
            # アクセス権なし等。tmpを片付けてユーザーへ通知する。
            try:
                os.remove(tmp)
            except OSError:
                pass
            self._report(
                "error", tr("保存できません"),
                tr("ファイルへ書き込めませんでした(他のアプリで使用中・"
                   "読み取り専用・アクセス権なし等の可能性):\n{0}").format(e))
            return False
        self.path = path
        # 名前を付けて保存でファイル名が変わった場合に備え、タイトル表示を更新
        self._refresh_title_display()
        # base_dir は変更しない: self.data の相対パスは base_dir 基準のまま保持し、
        # 保存のたびに保存先基準へ付け替えたコピーを書き出す(整合を崩さない)。
        self._report("ok", tr("保存しました"), os.path.basename(path))
        if self.on_saved:
            self.on_saved(path)
        return True
