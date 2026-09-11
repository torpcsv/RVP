"""シナリオ編集: チャンネル・音声なし・動画・デバイス担当の相関制御と他からコピー(mixin)。"""
from __future__ import annotations

import json
from ..i18n import tr

from .common import CHANNEL_IDS, DEVICE_TYPES
from ._hooks import _pkg


class _ScenarioEditorCorrelationMixin:
    """ScenarioEditor の mixin(=301 分割)。チャンネル・音声なし・動画・デバイス担当の相関制御と他からコピー"""

    @staticmethod
    def _ch_raw_has_audio(ch_raw) -> bool:
        """チャンネルraw(dict)に音声つきアイテムが1つ以上あるか。"""
        if not isinstance(ch_raw, dict):
            return False
        for it in (ch_raw.get("items") or []):
            if isinstance(it, str) and it:
                return True
            if isinstance(it, dict) and it.get("audio"):
                return True
        return False

    def _auto_seek_channel(self, channels: dict) -> str:
        """無指定時の自動決定: C→L→R の優先順で音声のあるチャンネル。

        =63: 音声がどこにも無い(スクリプト専用chだけの)ときは、同じ優先順で
        アイテムを持つチャンネルへフォールバックする(Scenario 側の
        seek_follow_channel と揃える)。
        """
        for cid in ("C", "L", "R"):
            if self._ch_raw_has_audio((channels or {}).get(cid)):
                return cid
        for cid in ("C", "L", "R"):
            ch = (channels or {}).get(cid)
            if isinstance(ch, dict) and (ch.get("items") or []):
                return cid
        return "C"

    def _seek_channel_out(self, channels: dict):
        """保存値を返す。自動決定と同じ選択なら None(=キー省略)。

        選択チャンネルに音声が無い(無効な選択)場合は自動決定へ正規化する。
        """
        sel = self.seek_var.get() or "C"
        sel_raw = (channels or {}).get(sel)
        # =63: スクリプト専用chも選べる(アイテムがあれば有効な選択とみなす)
        if not (self._ch_raw_has_audio(sel_raw)
                or (isinstance(sel_raw, dict) and (sel_raw.get("items") or []))):
            sel = self._auto_seek_channel(channels)
        auto = self._auto_seek_channel(channels)
        return sel if sel != auto else None

    def _device_btn_text(self) -> str:
        return tr("デバイス連動：ON") if self.device_enabled \
            else tr("デバイス連動：OFF")

    def _toggle_device_enabled(self):
        """デバイス連動のON/OFFを切り替える(ツールバーのトグル)。

        UIは即時に切り替わり、JSONへは「保存」時に書き出される。
        OFFでもデバイス関連の入力値・トラック行は**裏で保持したまま**
        非表示にするだけなので、保存してもJSONの紐づけ定義・デバイス担当は
        消えない(ONに戻せば自動/手動紐づけがそのまま復活する)。
        """
        self.device_enabled = not self.device_enabled
        self._apply_device_enabled()
        self._hist_check()   # =277

    def _apply_device_enabled(self):
        """デバイス連動フラグを編集画面のUIへ反映する(=252)。

        OFFで隠すもの: デバイス担当ブロック(device_box)・各アイテムの
        自動/手動メニューとトラック領域・「＋スクリプト」ボタン。
        funscript/csv のD&Dも無効になる(_on_dnd_drop 側のガード)。
        スクリプト専用アイテムの**行そのものは残す**(AQ6=存在を隠さない)。
        """
        self.device_toggle_btn.configure(text=self._device_btn_text())
        if self.device_enabled:
            if not self.device_box.winfo_manager():
                self.device_box.pack(fill="x", pady=(6, 4),
                                     before=self.chan_grid)
        else:
            self.device_box.pack_forget()
        for sec in self.channel_sections.values():
            sec.set_device_visible(self.device_enabled)

    def _script_only_event(self) -> bool:
        """有効チャンネルが1つ以上あり、すべてスクリプト専用chか(=63)。"""
        enabled = [sec for sec in self.channel_sections.values()
                   if sec.enabled_var.get()]
        if not enabled:
            return False
        return all(sec.is_script_only() for sec in enabled)

    def _refresh_channel_infinite(self):
        """指定チャンネル終了の「対象外ch」に無限を許可する(対象chは有限)。

        =63: スクリプト専用chだけのイベントで終了条件が「合計時間」/「変数条件」
        のときは、**無限のみ**に絞る(有限だとスクリプトが終わった時点で
        イベントも終わってしまい、指定した秒数/条件まで持たないため)。
        """
        cond = self.evend_var.get()
        target = self.evend_ch_var.get()
        has_video = bool(self._video_channel_id())
        # =168: 「無限」もイベント自身が終わらないので全ch無限を許可する
        wait_end = cond in (self.EVEND_COND, self.EVEND_DURATION,
                            self.EVEND_INFINITE)
        # =63のスクリプト専用chの絞り込みは「無限」には要らない
        # (イベント自身が終わらないので、有限のスクリプトでも問題ない)
        script_only = (wait_end and cond != self.EVEND_INFINITE
                       and self._script_only_event())
        for cid, sec in self.channel_sections.items():
            if wait_end:
                allow = True   # 条件成立/指定秒の経過まで続けるので全ch無限を許可
            elif has_video:
                allow = True   # 動画ch: 動画終了/終了条件まで続けるので許可(=48)
            else:
                allow = (cond == self.EVEND_CHANNEL and cid != target)
            sec.set_allow_infinite(allow)
            sec.set_infinite_only(script_only and sec.enabled_var.get())

    def _on_channel_enabled(self):
        """チャンネルの有効/無効が切り替わったとき(ChannelSectionから通知)。"""
        # 指定チャンネル終了の対象ch候補(有効chのみ)を追従させる
        if getattr(self, "evend_var", None) is not None \
                and self.evend_var.get() == self.EVEND_CHANNEL:
            self._update_evend_ui()
        # 音声なし(有効チャンネル0)の相関制御を追従させる
        self._update_noaudio_correlation()
        # 動画の相関制御(終了コンボの絞り込み・device担当の締め出し)は
        # noaudio の後に上書きする必要がある(=48の順序ルール)。これが
        # 無いと動画イベントでチャンネルを有効化したときに絞り込みが
        # 巻き戻る(=50で追加)。
        if getattr(self, "channel_sections", None):
            self._update_video_correlation()
        # =63: スクリプト専用chだけになった/でなくなった場合の無限のみ絞り込み
        if getattr(self, "evend_var", None) is not None:
            self._refresh_channel_infinite()

    def _update_noaudio_correlation(self):
        """有効チャンネル0(=音声なしイベント/ステート)の相関制御。

        通常イベント: イベント終了コンボ=EVEND_ALL / EVEND_DURATION(=62の
        無音待機ノード)のみ+ヒント表示。ステート形式: ステート移行コンボ=
        「なし」/経過時間(=62)/判定式(変数宣言時)のみ+ヒント表示。デバイス担当メニューは全て「なし」固定
        (無効化)。チャンネルを1つでも有効化すると選択肢・メニューを復元する。
        動画があるときは音声なしイベントではなく動画イベントなので対象外
        (=48。イベント終了コンボは _update_video_correlation が引き継ぐ)。
        """
        ev = (self.data.get("events") or {}).get(self.selected or "")
        if not isinstance(ev, dict):
            return
        # =52: 動画もチャンネルなので、動画chが有効なら noaudio は False
        noaudio = not any(s.enabled_var.get()
                          for s in self.channel_sections.values())
        multi = "states" in ev
        # デバイス担当: 音声なしは「なし」固定(無効化)
        for ttype in DEVICE_TYPES:
            menu = self.device_menus.get(ttype)
            if noaudio:
                self.device_vars[ttype].set(tr("なし"))
                if menu is not None:
                    menu.configure(state="disabled")
            elif menu is not None:
                menu.configure(state="normal")
        if multi:
            # ステート移行コンボの選択肢を絞る/復元する
            if noaudio:
                # =62: チャンネルを見ない移行条件(経過時間・判定式)は使える
                # =275: 選択肢(選ばれるまで無音で待機)も使える
                choices = [self.TRANS_STATE_TIME, self.TRANS_CHOICE,
                           self.TRANS_NONE]
                if self._has_vars():
                    choices.append(self.TRANS_COND)
                self.trans_type_menu.configure(values=choices)
                if self.trans_type_var.get() not in choices:
                    self.trans_type_var.set(tr("ステート移行なし"))
                    self._update_trans_ui()
                self.trans_noaudio_hint.configure(
                    text=self._trans_noaudio_hint_text())
                self.trans_noaudio_hint.pack(side="left", padx=(8, 0))
            else:
                choices = list(self.TRANS_CHOICES)
                if self._has_vars():
                    choices.append(self.TRANS_COND)
                self.trans_type_menu.configure(values=choices)
                self.trans_noaudio_hint.pack_forget()
            self.evend_noaudio_hint.pack_forget()
        else:
            # イベント終了コンボの選択肢を絞る/復元する
            if noaudio:
                # =62: 「合計時間が経過した時」= 無音待機ノード(指定秒だけ待つ)
                self.evend_menu.configure(
                    values=[self.EVEND_ALL, self.EVEND_DURATION])
                if self.evend_var.get() not in (self.EVEND_ALL,
                                                self.EVEND_DURATION):
                    self.evend_var.set(self.EVEND_ALL)
                    self._update_evend_ui()
                self.evend_noaudio_hint.configure(
                    text=tr("(音声なし=指定秒だけ待機)")
                    if self.evend_var.get() == self.EVEND_DURATION
                    else tr("(音声なし=即座に次へ)"))
                self.evend_noaudio_hint.pack(side="left", padx=(8, 0))
            else:
                evend_choices = [self.EVEND_ALL, self.EVEND_CHANNEL,
                                 self.EVEND_DURATION]
                if self._has_vars():
                    evend_choices.append(self.EVEND_COND)
                evend_choices.append(self.EVEND_INFINITE)   # =168
                self.evend_menu.configure(values=evend_choices)
                self.evend_noaudio_hint.pack_forget()
            self.trans_noaudio_hint.pack_forget()

    def _refresh_noaudio_hints(self):
        """音声なしヒントの文言だけを現在の選択に合わせる(=62)。

        表示/非表示は _update_noaudio_correlation が決める。ここは
        「即座に次へ」と「指定秒だけ待機」の出し分けだけを行う
        (コンボ操作のたびに呼ぶので、相関制御を呼ぶと再帰する)。
        """
        hint = getattr(self, "evend_noaudio_hint", None)
        if hint is not None and hint.winfo_manager():
            hint.configure(
                text=tr("(音声なし=指定秒だけ待機)")
                if self.evend_var.get() == self.EVEND_DURATION
                else tr("(音声なし=即座に次へ)"))
        hint = getattr(self, "trans_noaudio_hint", None)
        if hint is not None and hint.winfo_manager():
            hint.configure(text=self._trans_noaudio_hint_text())

    def _trans_noaudio_hint_text(self) -> str:
        """音声なしステートのヒント文言(=62/=275)。"""
        t = self.trans_type_var.get()
        if t == self.TRANS_STATE_TIME:
            return tr("(音声なし=指定秒だけ待機)")
        if t == self.TRANS_CHOICE:
            return tr("(音声なし=選択されるまで待機)")
        return tr("(音声なし=即座に通過)")

    def _video_channel_id(self) -> str:
        """動画アイテムを持つチャンネルのID(無ければ "")。=52。"""
        for cid, sec in self.channel_sections.items():
            if sec.has_video():
                return cid
        return ""

    def _update_video_correlation(self):
        """動画チャンネルの有無に応じた相関制御(=48 → =52でch基準へ)。

        - 「＋動画」: 動画chが既にあるとき、他のチャンネルでは無効化
          (動画は同時に1本=合意事項7の先回り防止)。
        - シークバー追従ラジオ: 動画chがあるとき全チャンネルで非表示
          (シークバーは動画に固定・seek_channel は保存しない)。
        - 通常イベントのイベント終了コンボ: 動画chがあるときは
          「全チャンネルが終了した時」を外す(動画chは無限にもできるため)。
          「動画が終わった時」は「指定チャンネルが終了した時」へ一本化した。
        - デバイス担当メニュー(=50): 動画のトラックが担当する種別は
          「なし」固定+無効化する。種別一意性(1種別1駆動源)の検証エラーを
          保存時ではなく編集時に先回りで防ぐ(ユーザー決定 2026-07-25)。
        _update_noaudio_correlation の後に呼ぶこと(選択肢を上書きするため)。
        """
        vcid = self._video_channel_id()
        video = bool(vcid)
        for cid, sec in self.channel_sections.items():
            sec.set_seek_radio_suppressed(video)
            sec.set_video_lock(video and cid != vcid)
        self._update_video_device_correlation()
        ev = (self.data.get("events") or {}).get(self.selected or "")
        if not isinstance(ev, dict) or "states" in ev:
            return   # ステート形式のイベント終了コンボは「委譲」固定のため対象外
        # 有効ch0(=40の音声なしイベント)の絞り込みは
        # _update_noaudio_correlation が管理するため触らない
        if not any(s.enabled_var.get()
                   for s in self.channel_sections.values()):
            return
        choices = [self.EVEND_CHANNEL, self.EVEND_DURATION]
        if not video:
            choices.insert(0, self.EVEND_ALL)
        if self._has_vars():
            choices.append(self.EVEND_COND)
        choices.append(self.EVEND_INFINITE)   # =168(動画chがあっても選べる)
        self.evend_menu.configure(values=choices)
        if self.evend_var.get() not in choices:
            self.evend_var.set(choices[0])
            self._update_evend_ui()

    def _update_video_device_correlation(self):
        """動画のトラックがある種別の device 担当メニューの相関(=50→=238→=239)。

        =50〜=238 は**「なし」(=238で動画chのID)へ固定+無効化**していた。
        これは「動画トラックと同じ種別を別chの担当にするとエラー」という
        =48の制約を編集時に先回りするためのものだった。

        **=239でその制約自体を外した**(ユーザー要望: 動画に linear の
        トラックがあっても、linear の担当を L や R へ付け替えたい)ので、
        メニューは**選べる状態にする**。ここでやることは2つだけ:

        - **既定値を動画chのIDにする**(まだ「なし」のときだけ)。=238の
          「画面上『なし』だと設定漏れに見える」という指摘への対応は残す。
          **ユーザーが選んだ値は上書きしない**(この関数はチャンネルの
          有効/無効やパネルの読み込みのたびに呼ばれるため)。
        - **選択肢から「なし」を外す**。動画のトラックがある種別は
          「担当なし=動画が鳴らす」なので、**「なし」と動画chのIDは同じ意味**
          になり、2通りの表現があると紛らわしいため。

        音声なし(有効ch0)で無効化されているメニューは、そちらの相関制御を
        尊重して有効化しない。
        """
        vtypes = set()
        for sec in self.channel_sections.values():
            vtypes |= sec.video_device_types()
        vcid = self._video_channel_id()
        noaudio = not any(s.enabled_var.get()
                          for s in self.channel_sections.values())
        for ttype in DEVICE_TYPES:
            menu = self.device_menus.get(ttype)
            if menu is None:
                continue
            if ttype in vtypes and vcid:
                menu.configure(values=list(CHANNEL_IDS))
                if self.device_vars[ttype].get() == tr("なし"):
                    self.device_vars[ttype].set(vcid)
                if not noaudio:
                    menu.configure(state="normal")
            else:
                menu.configure(values=[tr("なし")] + list(CHANNEL_IDS))
                if not noaudio:
                    menu.configure(state="normal")

    def _set_device_vars(self, device, channels: dict,
                         has_video: bool = False):
        dmap = {}
        if isinstance(device, str):
            dmap = {t: device for t in DEVICE_TYPES}
        elif isinstance(device, dict):
            dmap = device
        elif device is None and not has_video:
            default_ch = "C" if "C" in channels else \
                (next(iter(channels)) if channels else "C")
            dmap = {t: default_ch for t in DEVICE_TYPES}
        # 動画イベント(=48)の device 未指定の既定は「全てなし」
        # (全種別が動画側。C既定にすると動画トラックと種別が衝突するため)
        for ttype in DEVICE_TYPES:
            self.device_vars[ttype].set(dmap.get(ttype, tr("なし")))

    @staticmethod
    def _channel_copyable(ch: dict) -> bool:
        """コピー元にできるチャンネルか(=音声ch または スクリプト専用ch)。

        =65で「音声設定済みのアイテムが1つ以上」から拡張した(=46の既知の
        小制約の解消)。スクリプト専用チャンネル(音声を持たず tracks /
        funscript だけを持つアイテム)も候補に含める。

        **動画chは従来どおり除外**(ユーザー決定 2026-07-27): =52の「動画chは
        1つだけ」「音声/動画/スクリプトの混在禁止」があるため、コピー先が
        2つ目の動画chになる組み合わせを弾く相関制御が別途必要になる。動画は
        コピーせず「＋動画」で登録してもらう。
        """
        if not isinstance(ch, dict):
            return False
        for it in (ch.get("items") or []):
            if not isinstance(it, dict):
                continue
            if it.get("video"):
                return False    # 動画ch(混在禁止なので1件見れば確定)
            if it.get("audio") or it.get("tracks") or it.get("funscript"):
                return True
        return False

    def _channel_copy_sources(self, exclude):
        """コピー元候補 [(ev_id, st_id|None, cid), ...] を集める。

        音声ch・スクリプト専用chのみ(動画chは除外=65)。exclude=(ev,st,cid) は
        対象自身なので除外。
        """
        out = []
        for ev_id, ev in self.data["events"].items():
            if "states" in ev:
                for st_id, st in ev.get("states", {}).items():
                    for cid, ch in (st.get("channels") or {}).items():
                        if self._channel_copyable(ch) and \
                                (ev_id, st_id, cid) != exclude:
                            out.append((ev_id, st_id, cid))
            else:
                for cid, ch in (ev.get("channels") or {}).items():
                    if self._channel_copyable(ch) and \
                            (ev_id, None, cid) != exclude:
                        out.append((ev_id, None, cid))
        return out

    def _open_channel_copy(self, target_cid: str):
        """チャンネル「他からコピー」ダイアログを開く。"""
        ev_id = self.selected
        if not ev_id or ev_id not in self.data["events"]:
            return
        is_state_form = "states" in self.data["events"][ev_id]
        # 現在パネルを反映(同一イベント/ステートのソースも最新化)。エラーは中止。
        # =129: コピー対象チャンネル自身の検証エラーは免除する(コピーで
        # 丸ごと置き換わるため。他の検証は維持=不正なコミットを防ぐ)。
        err = (self._apply_state_panel(lenient_channel=target_cid)
               if is_state_form
               else self._apply_panel(lenient_channel=target_cid))
        if err:
            self._report("error", tr("編集エラー"), err)
            return
        cur_st = self.sel_state if is_state_form else None
        sources = self._channel_copy_sources((ev_id, cur_st, target_cid))
        if not sources:
            self._report("info", tr("他からコピー"),
                         tr("コピーできるチャンネルがありません。"))
            return
        _pkg().ChannelCopyDialog(
            self, sources,
            lambda e, s, c: self._apply_channel_copy(target_cid, e, s, c))

    def _apply_channel_copy(self, target_cid, src_ev, src_st, src_cid):
        """コピー元チャンネルの内容を対象チャンネルへ丸ごと取り込む。"""
        try:
            src_ev_raw = self.data["events"][src_ev]
            if src_st is None:
                src_ch = src_ev_raw["channels"][src_cid]
            else:
                src_ch = src_ev_raw["states"][src_st]["channels"][src_cid]
        except (KeyError, TypeError):
            return
        dup = json.loads(json.dumps(src_ch))
        tgt_ev = self.data["events"][self.selected]
        if "states" in tgt_ev:
            st = tgt_ev["states"][self.sel_state]
            st.setdefault("channels", {})[target_cid] = dup
            self._load_state_panel(self.sel_state)
        else:
            tgt_ev.setdefault("channels", {})[target_cid] = dup
            self._load_panel(self.selected)
        lines = [tr("チャンネル {0} に {1} の内容をコピーしました。").format(
            target_cid, src_cid)]
        # スクリプト専用chはデバイス担当が無いと何も起きない(=46の制約)。
        # コピー直後は担当が別chのままのことが多いので先に知らせる(=65)。
        if self._raw_is_script_only(dup) and not any(
                v.get() == target_cid for v in self.device_vars.values()):
            lines.append(
                tr("スクリプト専用チャンネルはデバイス担当を設定してください。"))
        self._report("info", tr("他からコピー"), lines)

    @staticmethod
    def _raw_is_script_only(ch: dict) -> bool:
        """rawチャンネルがスクリプト専用ch(アイテムが全てスクリプト)か。"""
        items = [it for it in ((ch or {}).get("items") or [])
                 if isinstance(it, dict)]
        return bool(items) and all(
            not it.get("audio") and not it.get("video")
            and (it.get("tracks") or it.get("funscript")) for it in items)
