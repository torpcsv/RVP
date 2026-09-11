"""シナリオ編集: BGM ブロック(=256)(mixin)。"""
from __future__ import annotations

import os
from ..i18n import tr

from .common import _dialog_initialdir, _remember_dialog_dir
from .items import BgmItemRow
from .paths import _safe_relpath
from ._hooks import _pkg


class _ScenarioEditorBgmMixin:
    """ScenarioEditor の mixin(=301 分割)。BGM ブロック(=256)"""

    def _update_bgm_ui(self):
        """3択に応じて「指定」の本体(アイテム一覧+操作行)を出し入れする。"""
        if self.bgm_mode_var.get() == self.BGM_SET:
            if not self.bgm_body.winfo_manager():
                self.bgm_body.pack(fill="x", padx=4, pady=(0, 4))
        else:
            self.bgm_body.pack_forget()

    def _on_bgm_pan_toggle(self):
        if self.bgm_pan_var.get():
            if not self.bgm_pan_box.winfo_manager():
                self.bgm_pan_box.pack(side="left", padx=(4, 0))
            if not self.bgm_pan_l_var.get() and not self.bgm_pan_r_var.get():
                self.bgm_pan_l_var.set("100")
                self.bgm_pan_r_var.set("100")
        else:
            self.bgm_pan_box.pack_forget()

    def _bgm_refit_items(self):
        """BGMが0曲のとき bgm_items_frame を潰す(=257)。

        空になった直後のフレームは pack_propagate では縮まず(最後のサイズ、
        初期状態では既定の200pxを保持する)、「BGMを指定」へ切り替えた直後に
        3択行と＋BGM行の間が大きく空く。ChannelSection._refit_items と同じ
        対処: 行があれば propagate、0件なら高さ1に固定して縮める。
        """
        if self._bgm_rows:
            self.bgm_items_frame.pack_propagate(True)
        else:
            self.bgm_items_frame.pack_propagate(False)
            self.bgm_items_frame.configure(height=1)

    def _bgm_append_row(self, raw_item):
        self.bgm_items_frame.pack_propagate(True)
        row = BgmItemRow(self.bgm_items_frame, raw_item, self.base_dir,
                         self._bgm_delete_row, self._bgm_move_row, owner=self)
        row.pack(fill="x", pady=2)
        self._bgm_rows.append(row)

    def _bgm_delete_row(self, row):
        self._bgm_rows.remove(row)
        row.destroy()
        self._bgm_refit_items()

    def _bgm_move_row(self, row, delta: int):
        """▲▼でBGMの再生順を入れ替える(順番モードで意味を持つ)。"""
        i = self._bgm_rows.index(row)
        j = i + delta
        if j < 0 or j >= len(self._bgm_rows):
            return
        self._bgm_rows[i], self._bgm_rows[j] = \
            self._bgm_rows[j], self._bgm_rows[i]
        for r in self._bgm_rows:
            r.pack_forget()
        for r in self._bgm_rows:
            r.pack(fill="x", pady=2)

    def _bgm_add_items(self):
        """＋BGM: 音声ファイル(wav/mp3)をダイアログで選んで追加する。"""
        paths = _pkg().filedialog.askopenfilenames(
            title=tr("BGM音声を選択"),
            initialdir=_dialog_initialdir(self.base_dir),
            filetypes=[(tr("音声"), "*.wav *.mp3"), (tr("すべて"), "*.*")],
            parent=self,
        )
        if paths:
            _remember_dialog_dir(paths[0])
        for p in paths:
            self._bgm_append_row(_safe_relpath(p, self.base_dir))

    def _bgm_hit(self, x, y) -> bool:
        """落下点(スクリーン座標)がBGMブロックの上か(=256のD&D用)。"""
        try:
            w = self.bgm_box
            if not w.winfo_ismapped():
                return False
            wx, wy = w.winfo_rootx(), w.winfo_rooty()
            return (wx <= x < wx + w.winfo_width()
                    and wy <= y < wy + w.winfo_height())
        except Exception:
            return False

    def _bgm_dropped_audio(self, paths):
        """D&DされたBGM音声を登録する(=256)。

        ＋BGMと同じくwav/mp3のみ。3択が「指定」以外なら自動で「指定」へ
        切り替える(チャンネルD&Dの自動有効化=44と同じ流儀)。対応外の
        拡張子・実在しないファイルは黙って無視する(既存仕様)。
        """
        ok = [p for p in paths
              if os.path.isfile(p)
              and os.path.splitext(p)[1].lower() in (".wav", ".mp3")]
        if not ok:
            return
        if self.bgm_mode_var.get() != self.BGM_SET:
            self.bgm_mode_var.set(self.BGM_SET)
            self._update_bgm_ui()
        for p in ok:
            self._bgm_append_row(_safe_relpath(p, self.base_dir))

    def _load_bgm(self, raw):
        """ノードの "bgm" 値をBGMブロックへ反映する(=256)。

        None/キー無し=「引き継ぐ」、{"off": true}=オフ、それ以外=指定。
        """
        for r in self._bgm_rows:
            r.destroy()
        self._bgm_rows = []
        self._bgm_refit_items()
        self.bgm_order_var.set(tr("順番に再生"))
        self.bgm_pan_var.set(False)
        self.bgm_pan_l_var.set("")
        self.bgm_pan_r_var.set("")
        self._on_bgm_pan_toggle()
        if not isinstance(raw, dict):
            self.bgm_mode_var.set(self.BGM_INHERIT)
        elif raw.get("off"):
            self.bgm_mode_var.set(self.BGM_OFF)
        else:
            self.bgm_mode_var.set(self.BGM_SET)
            for it in (raw.get("items") or []):
                self._bgm_append_row(it)
            if raw.get("order") == "random":
                self.bgm_order_var.set(tr("ランダム再生"))
            pan = raw.get("pan")
            if isinstance(pan, dict):
                self.bgm_pan_var.set(True)
                try:
                    self.bgm_pan_l_var.set(
                        str(int(round(float(pan.get("left", 1.0)) * 100))))
                    self.bgm_pan_r_var.set(
                        str(int(round(float(pan.get("right", 1.0)) * 100))))
                except (TypeError, ValueError):
                    pass
                self._on_bgm_pan_toggle()
        self._update_bgm_ui()

    def _collect_bgm(self, where: str):
        """BGMブロックの内容をJSON値へ。(エラー文|None, 値|None) を返す。

        値: None=引き継ぐ(キー省略) / {"off": true} / {"items": [...], ...}。
        「指定」なのにアイテム0件はエラー(Q12=ユーザー確定)。
        BGM:OFF(bgm_enabled=False)の間もブロックは非表示なだけで、読み込んだ
        値を保持したまま書き戻す=保存してもノードの "bgm" 定義は消えない
        (=252のデバイス連動と同じ「UIは非表示・裏はデータ保持」方式)。
        """
        mode = self.bgm_mode_var.get()
        if mode == self.BGM_OFF:
            return None, {"off": True}
        if mode != self.BGM_SET:
            return None, None
        if not self._bgm_rows:
            self._want_mark(self.bgm_add_btn, "error")
            return (tr('{0}: BGMを「指定」にしていますが、音声がありません'
                       ).format(where), None)
        out = {"items": [r.collect() for r in self._bgm_rows]}
        if self.bgm_order_var.get() == tr("ランダム再生"):
            out["order"] = "random"
        if self.bgm_pan_var.get():
            try:
                lp = max(0, min(100, int(float(self.bgm_pan_l_var.get() or 0))))
                rp = max(0, min(100, int(float(self.bgm_pan_r_var.get() or 0))))
                out["pan"] = {"left": lp / 100.0, "right": rp / 100.0}
            except ValueError:
                pass
        return None, out

    def _bgm_btn_text(self) -> str:
        return tr("BGM：ON") if self.bgm_enabled else tr("BGM：OFF")

    def _toggle_bgm_enabled(self):
        """BGM機能のON/OFFを切り替える(1段目のトグル。=252と同方式)。

        UIは即時に切り替わり、JSONへは「保存」時に書き出される。OFFでも
        各ノードのBGM設定は**裏で保持したまま**非表示にするだけなので、
        保存してもJSONの "bgm" 定義は消えない(ONに戻せば復活する)。
        """
        self.bgm_enabled = not self.bgm_enabled
        self._apply_bgm_enabled()
        self._hist_check()   # =277

    def _apply_bgm_enabled(self):
        """BGMフラグを編集画面のUIへ反映する(=256)。

        OFFで隠すもの: パネル最下部のBGMブロック(bgm_box)。BGM音声の
        D&Dも受けない(_on_dnd_drop 側のガード)。
        """
        self.bgm_toggle_btn.configure(text=self._bgm_btn_text())
        if self.bgm_enabled:
            if not self.bgm_box.winfo_manager():
                self.bgm_box.pack(fill="x", pady=(2, 4),
                                  after=self.chan_grid)
        else:
            self.bgm_box.pack_forget()
