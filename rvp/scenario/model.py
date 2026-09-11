"""シナリオの dataclass 群(Pan / DeviceTrack / EventItem / Channel / BGM / 背景 / ステート移行 / 変数・条件・選択肢・入力 / ScenarioEvent)。"""
from __future__ import annotations

import os
from typing import NamedTuple
from dataclasses import dataclass, field
from ..i18n import tr

from .constants import (END_CHANNEL, END_COND, END_DURATION, END_NONE,
    END_ONCE, END_PLAYS, END_REPEAT, END_STATES, END_TRANSITIONS,
    MODE_RANDOM, MODE_RANDOM_BAG, MODE_SEQUENTIAL, TRACK_LINEAR,
    WHEN_ALL_CHANNELS, WHEN_CHANNEL_COUNT, WHEN_CHANNEL_END, WHEN_CHOICE,
    WHEN_COND, WHEN_STATE_TIME)


@dataclass
class Pan:
    left: float
    right: float


@dataclass
class DeviceTrack:
    """1つの音声に紐づく、1デバイス種別ぶんのfunscript。"""
    type: str                     # linear / rotate / vibration
    funscript: str                # 絶対パスに解決済み
    # 区間指定(=59)。**省略時はアイテムの区間に連動**し、ここに指定があれば
    # そちらを優先する(ユーザー決定=C案)。区間の先頭が0秒として扱われる。
    start_s: float = 0.0          # 区間の開始秒(0=スクリプトの先頭)
    end_s: float | None = None    # 区間の終了秒(None=末尾まで)

    @property
    def has_range(self) -> bool:
        """このトラック自身に区間指定があるか(=アイテムの区間より優先)。"""
        return self.start_s > 0 or self.end_s is not None


@dataclass
class EventItem:
    audio: str                    # 絶対パスに解決済み。""=スクリプトのみ/動画アイテム
    tracks: list[DeviceTrack] = field(default_factory=list)
    # =74: 重みは定数 or 変数参照。weight_var 指定時は抽選のたびに変数値で
    # 解決する(0以下=抽選に出ない)。定数の0/負も受理(同じく出ない)。
    weight: float = 1.0
    weight_var: str | None = None
    pan: Pan | None = None        # item個別のパン(指定時はチャンネル既定より優先)
    on_play: tuple = ()           # 再生開始時の変数操作
    on_complete: tuple = ()       # 自然完了時の変数操作(スキップ/中断では発火しない)
    # --- 動画アイテム(フェーズ3-B=52)。外部mpvで再生する ---
    # audio と同居しない(チャンネル単位で音声/動画/スクリプトのどれか)。
    video: str = ""               # 絶対パスに解決済み。""=動画アイテムでない
    # 区間指定。=51で動画に導入し、**=59で音声・スクリプトへも拡張**した。
    # 経過表示・シークバー・トラック同期は**区間の先頭を0秒**として扱う。
    # トラック(funscript/CSV)は既定でこの区間に連動する(=59・ユーザー決定)。
    start_s: float = 0.0          # 区間の開始秒(0=素材の先頭)
    end_s: float | None = None    # 区間の終了秒(None=素材の末尾まで)

    @property
    def has_range(self) -> bool:
        """区間指定があるか(先頭以外から始まる、または終端が指定されている)。"""
        return self.start_s > 0 or self.end_s is not None

    # --- 後方互換アクセサ(=51〜=58 の呼び出し側が video_* を使っていた) ---
    @property
    def video_start_s(self) -> float:
        return self.start_s

    @property
    def video_end_s(self) -> float | None:
        return self.end_s

    @property
    def video_has_range(self) -> bool:
        return bool(self.video) and self.has_range

    @property
    def content(self) -> str:
        """このアイテムが再生するコンテンツのパス(音声 or 動画)。"""
        return self.audio or self.video

    # --- 後方互換アクセサ ---
    @property
    def funscript(self) -> str | None:
        """最初の linear トラックのfunscriptパス(旧APIとの互換用)。"""
        for t in self.tracks:
            if t.type == TRACK_LINEAR:
                return t.funscript
        return None

    def tracks_of(self, track_type: str) -> list[DeviceTrack]:
        return [t for t in self.tracks if t.type == track_type]


@dataclass
class NumRef:
    """数値の変数参照。終了条件・transitionの数値欄で使う。

    解決タイミングは「イベント/ステートに入った時」(その時点の変数値で固定)。
    """
    var: str

    def resolve(self, vars_: dict) -> float:
        return float(vars_[self.var])


@dataclass
class Channel:
    """イベント内の1チャンネル(L/C/R)。独立した再生列を持つ。"""
    channel_id: str               # L / C / R
    mode: str                     # sequential / random / random_bag(=76)
    items: list[EventItem] = field(default_factory=list)
    end_type: str = END_ONCE
    end_count: int = 1
    end_duration_ms: int = 0
    # 変数参照(指定時は上の静的値の代わりに、ステートに入った時点の変数値で解決)
    end_count_ref: NumRef | None = None
    end_duration_refs: tuple | None = None   # (None, secondsのNumRef)。minutesは廃止
    # =99: duration 範囲抽選(min秒〜max秒)。end_duration_range=True のとき有効。
    # 値はミリ秒(定数)、refはNumRef(秒)。ステート入場のたびに resolve_end が
    # [min, max] の一様乱数を抽選する(イベント終了 duration の範囲と同じ書式)。
    end_duration_range: bool = False
    end_duration_min_ms: float = 0
    end_duration_max_ms: float = 0
    end_duration_min_ref: NumRef | None = None
    end_duration_max_ref: NumRef | None = None
    pan: Pan = None               # チャンネル既定のパン
    # インターバル: 音声再生終了後、次の再生までの待機時間の抽選範囲(ミリ秒)
    # 0.1秒(100ms)刻みで min〜max を均等抽選する。既定は 0(待機なし)。
    interval_min_ms: int = 0
    interval_max_ms: int = 0

    @property
    def has_audio(self) -> bool:
        """音声つきアイテムを1つ以上持つか(False=スクリプト専用/動画チャンネル)。

        parse_channel が混在(音声つき/動画/スクリプトのみの同居)を禁止する
        ため、False なら全アイテムが動画かスクリプトのみ。
        """
        return any(it.audio for it in self.items)

    @property
    def has_video(self) -> bool:
        """動画アイテムのチャンネルか(フェーズ3-B=52)。

        混在は parse_channel が禁止するため、True なら全アイテムが動画。
        動画チャンネルは1つのステートに最大1つ(=合意事項7「動画は同時に1本」)。
        """
        return any(it.video for it in self.items)

    @property
    def is_content(self) -> bool:
        """コンテンツ(音声 or 動画)を再生するチャンネルか。

        False = スクリプト専用チャンネル(=46)。
        """
        return self.has_audio or self.has_video

    @property
    def video_tracks(self) -> tuple:
        """動画チャンネルの全アイテムのトラック(種別一意性の検証用)。"""
        out = []
        for it in self.items:
            if it.video:
                out.extend(it.tracks)
        return tuple(out)

    def resolve_end(self, vars_: dict) -> tuple[int, int]:
        """終了条件の数値を解決する(ステート入場時に呼ぶ)。(count, duration_ms)。

        変数解決値のクランプ: countは1未満なら1、durationは0未満なら0
        (0は「即時成立」= 再生中の音声を終えて終了)。
        =99: duration が範囲抽選(min〜max)のときは、入場ごとに [min, max] の
        一様乱数(ミリ秒)を抽選する(ステート再入で再抽選=イベント側と同じ)。
        """
        import random as _r
        count = self.end_count
        if self.end_count_ref is not None:
            count = max(1, int(self.end_count_ref.resolve(vars_)))
        if self.end_duration_range:
            lo = (self.end_duration_min_ref.resolve(vars_) * 1000
                  if self.end_duration_min_ref is not None
                  else self.end_duration_min_ms)
            hi = (self.end_duration_max_ref.resolve(vars_) * 1000
                  if self.end_duration_max_ref is not None
                  else self.end_duration_max_ms)
            lo, hi = max(0.0, lo), max(0.0, hi)
            if hi < lo:
                lo, hi = hi, lo
            ms = int(lo) if lo >= hi else int(_r.uniform(lo, hi))
            return count, ms
        ms = self.end_duration_ms
        if self.end_duration_refs is not None:
            m_ref, s_ref = self.end_duration_refs
            m = m_ref.resolve(vars_) if isinstance(m_ref, NumRef) else float(m_ref or 0)
            s = s_ref.resolve(vars_) if isinstance(s_ref, NumRef) else float(s_ref or 0)
            ms = max(0, int(m * 60_000 + s * 1_000))
        return count, ms

    def describe(self, count: int = None, ms: int = None) -> str:
        """チャンネルの設定を1行で説明する(=56の再生ログ用)。

        「順番に再生 / 無限 / 2アイテム」のような形。count/ms は
        resolve_end で解決した値(変数指定のときは実際に使う値になる)。
        """
        if count is None or ms is None:
            count, ms = self.end_count, self.end_duration_ms
        if self.mode == MODE_RANDOM:
            mode = tr('ランダム再生')
        elif self.mode == MODE_RANDOM_BAG:
            mode = tr('ランダム(重複なし)')
        else:
            mode = tr('順番に再生')
        if self.end_type == END_NONE:
            end = tr('無限')
        elif self.end_type == END_REPEAT:
            end = tr('{0}周で終了').format(count)
        elif self.end_type == END_PLAYS:
            end = tr('{0}回再生で終了').format(count)
        elif self.end_type == END_DURATION and self.end_duration_range \
                and self.end_duration_min_ref is None \
                and self.end_duration_max_ref is None \
                and self.end_duration_min_ms != self.end_duration_max_ms:
            # =99: 範囲抽選は設定値と今回の抽選結果の両方を出す(イベントと同じ)
            end = tr('{0:g}〜{1:g}秒で終了(今回{2:g}秒)').format(
                self.end_duration_min_ms / 1000,
                self.end_duration_max_ms / 1000, round(ms / 1000, 1))
        elif self.end_type == END_DURATION:
            end = tr('{0:g}秒で終了').format(ms / 1000)
        else:
            end = tr('1周で終了')
        kind = tr('動画') if self.has_video else (
            tr('音声') if self.has_audio else tr('スクリプト'))
        parts = [kind, mode, end, tr('{0}アイテム').format(len(self.items))]
        if self.interval_max_ms > 0:
            parts.append(tr('間隔{0:g}〜{1:g}秒').format(
                self.interval_min_ms / 1000, self.interval_max_ms / 1000))
        return ' / '.join(parts)

    def pick_interval_ms(self) -> int:
        """インターバルを0.1秒刻みで均等抽選する。"""
        if self.interval_max_ms <= 0:
            return 0
        lo = self.interval_min_ms // 100
        hi = self.interval_max_ms // 100
        if hi < lo:
            lo, hi = hi, lo
        import random as _r
        return _r.randint(lo, hi) * 100


@dataclass
class BgmItem:
    """BGMチャンネルの1曲(=256)。ファイルとアイテム個別パンのみ持つ。

    通常アイテムと違い、重み・区間・変数操作・トラック(デバイス)は
    持たない(Q6=最小構成。変数操作はループのたびに発火して収拾が
    つかなくなるため意図的に対象外)。
    """
    audio: str                    # 絶対パスに解決済み
    pan: Pan | None = None        # item個別のパン(指定時はBGM既定より優先)


@dataclass
class BgmSpec:
    """ノード(イベント/ステート)のBGM指定(=256)。

    JSONのノード直下 "bgm" キーから作る。**キー省略=None=「前のBGMを
    引き継ぐ」**(このオブジェクトは作られない)。channels とは独立した
    仕組みで、デバイス紐づけ・終了条件・シークバー追従・再生回数(plays)の
    対象外。再生は常に無限ループ(Q4)。
    """
    mode: str                     # "set"(指定) / "off"(停止)
    items: tuple = ()             # BgmItem列(mode=="set"のとき1件以上)
    order: str = MODE_SEQUENTIAL  # sequential(リスト順に周回) / random(袋方式)
    pan: Pan | None = None        # BGM既定パン(アイテム個別が優先)

    def describe(self) -> str:
        """再生ログ用の1行説明。"""
        if self.mode == "off":
            return tr("BGM停止")
        names = [os.path.basename(it.audio) for it in self.items]
        if len(names) == 1:
            return tr("{0} (ループ)").format(names[0])
        mode = tr("ランダム") if self.order == MODE_RANDOM else tr("順番")
        return tr("{0} ほか ({1}・{2}曲)").format(names[0], mode, len(names))


@dataclass
class BackgroundSpec:
    """シナリオの背景イラスト指定(=262。トップレベル "background")。

    再生タブの背景全体にイラストを敷く機能。JSONは
      "background": "images/bg.png"                  (文字列=dim既定40)
      "background": {"file": "images/bg.png", "dim": 40}
    の2書式。dim は画像へ被せる黒の濃度%(0=そのまま〜100=真っ黒。
    文字の可読性のための減光)。表示のON/OFFは視聴側のアプリ設定
    (show_background)が最終決定する(シナリオ側は素材の指定のみ)。
    """
    file: str                     # 絶対パスに解決済み
    dim: int = 40                 # 黒オーバーレイの濃度%(0〜100)


@dataclass
class StateTransition:
    """ステートの移行条件と移行先。"""
    when_type: str            # channel_count / state_time / cond /
                              # all_channels / channel_end
    channel: str              # 監視対象チャンネル(cond/state_time のときは未使用)
    min_value: float          # state_time: ミリ秒 / channel_count: 回数
    max_value: float
    # =73: 移行先は重み付き候補 [(state_id, weight定数|None, weight変数名|None)]。
    # 従来の均等抽選=全候補 (sid, 1.0, None)。解決後の重みが0以下の候補は
    # 「出さない」(=26のイベント版ランダム分岐と同じ規則)。
    candidates: list = field(default_factory=list)
    else_to: str | None = None   # 全候補の重みが0以下のときの移行先(None=イベント終了)
    # =77: 未実行ステートの優先(=26のイベント版ランダム分岐のステート移植)。
    # visited_exclude=True で「このイベント実行中に実行済みのステート」を候補
    # から除外する(履歴はイベントに入るたびリセット=ユーザー決定)。
    # 全候補が実行済みのときは exhausted_mode に従う(all/reset/to)。
    visited_exclude: bool = False
    exhausted_mode: str = "all"
    exhausted_to: str | None = None
    # 変数参照(指定時はステート入場時の変数値で解決。state_timeは秒、countは回数)
    min_ref: NumRef | None = None
    max_ref: NumRef | None = None
    # when_type==WHEN_COND のときの判定式(AND条件、再生中に常時評価)
    conds: tuple = ()
    # =125: 移行先の判定式(cond)形式。[(AND条件タプル, 移行先ステートID)] を
    # 上から順に評価し、最初に成立した行へ移行する(イベントの next cond と
    # 同じ規則)。非空のとき candidates は空(random形式と排他)。
    # どの行も成立しないときは else_to へ、else_to が None なら**移行しない**
    # (=transitionなしと同じ挙動。イベント版の「else必須・null=再生終了」とは
    # 意図的に異なる=2026-08-13ユーザー決定)。
    cond_rows: list = field(default_factory=list)
    # =275: when_type==WHEN_CHOICE のときの選択肢ルール(候補・タイムリミット・
    # 既定・選択必須・表示タイミング)。候補の to はステートID、または
    # to_event=True で遷移先イベントID。
    choice: "ChoiceRule | None" = None

    @property
    def targets(self) -> list[str]:
        """移行先候補のステートID列(重み・elseを除いた一覧)。

        =275: 選択肢形式ではステート宛ての行き先(既定の指定ステート含む)。
        イベント宛ては event_targets で別に列挙する。
        """
        if self.when_type == WHEN_CHOICE:
            return self.choice.state_targets if self.choice else []
        if self.cond_rows:
            return [to for _c, to in self.cond_rows]
        return [c[0] for c in self.candidates]

    @property
    def event_targets(self) -> list[str]:
        """=275: 選択肢形式で「イベントを終了して飛ぶ」行き先のイベントID列。"""
        if self.when_type == WHEN_CHOICE and self.choice:
            return self.choice.event_targets
        return []

    def cond_resolves(self, vars_: dict | None = None) -> bool:
        """=125: cond形式で「移行先が決まるか」。

        どの行も成立せず else も無ければ False =移行自体を見送る
        (player の transition_met がこの判定で発火を抑止する)。
        cond形式でなければ常に True。
        """
        if not self.cond_rows:
            return True
        if self.else_to is not None:
            return True
        vars_ = vars_ or {}
        return any(all(c.eval(vars_) for c in conds)
                   for conds, _to in self.cond_rows)

    def pick_threshold(self, vars_: dict | None = None) -> float:
        """閾値を抽選する(ステートに入るたびに呼ぶ)。変数参照はここで解決する。"""
        import random as _r
        lo, hi = self.min_value, self.max_value
        time_type = self.when_type == WHEN_STATE_TIME
        if self.min_ref is not None or self.max_ref is not None:
            scale = 1000.0 if time_type else 1.0
            if self.min_ref is not None:
                lo = self.min_ref.resolve(vars_ or {}) * scale
            if self.max_ref is not None:
                hi = self.max_ref.resolve(vars_ or {}) * scale
            if self.when_type == WHEN_CHANNEL_COUNT:
                lo, hi = max(1, int(lo)), max(1, int(hi))
            else:
                lo, hi = max(0.0, lo), max(0.0, hi)
        if hi < lo:
            lo, hi = hi, lo
        if lo >= hi:
            return lo
        if time_type:
            return _r.uniform(lo, hi)
        return _r.randint(int(lo), int(hi))

    def pick_target_info(self, vars_: dict | None = None,
                         visited: set | None = None):
        """移行先を抽選する(=73: 移行が成立した瞬間に呼ぶ)。

        重みは呼び出し時点の変数値で解決し(ユーザー決定=入場時ではない)、
        0以下の候補は抽選対象外。出せる候補が無ければ else_to
        (None=イベント終了)。戻り値: (移行先ID|None, elseに落ちたか)。

        =77: visited(このイベント実行中に実行済みのステートIDの集合)を渡すと、
        visited_exclude 時に実行済み候補を除外する。全候補が実行済みなら
        exhausted_mode に従う(player._pick_next の=26と同じ規則):
          all   → 除外をやめて全候補から抽選
          reset → 渡された集合から候補ぶんを取り除いて再一巡(集合を書き換える)
          to    → exhausted_to へ固定移行(重み・elseは見ない)
        """
        import random as _r
        vars_ = vars_ or {}
        # =125: 判定式(cond)形式は重み抽選より先に確定分岐する。
        # 上から順に評価し最初に成立した行へ。全滅は else_to
        # (None=移行しない。transition_met が事前に発火を抑止するので、
        # 通常ここで None が返るのは発火→抽選の間に変数が変わった稀な場合)
        if self.cond_rows:
            for conds, to in self.cond_rows:
                if all(c.eval(vars_) for c in conds):
                    return to, False
            return self.else_to, True
        cands = self.candidates
        if self.visited_exclude and visited is not None:
            remaining = [c for c in cands if c[0] not in visited]
            if not remaining:
                if self.exhausted_mode == "to":
                    return self.exhausted_to, False
                if self.exhausted_mode == "reset":
                    for c in cands:
                        visited.discard(c[0])
                remaining = cands
            cands = remaining
        targets, weights = [], []
        for to, w_const, w_var in cands:
            w = vars_.get(w_var) if w_var is not None else w_const
            try:
                w = float(w)
            except (TypeError, ValueError):
                w = 0.0
            if w > 0:
                targets.append(to)
                weights.append(w)
        if not targets:
            return self.else_to, True
        return _r.choices(targets, weights=weights, k=1)[0], False

    def pick_target(self, vars_: dict | None = None) -> str | None:
        return self.pick_target_info(vars_)[0]

    def _to_label(self) -> str:
        """describe用の移行先表記。=125: cond形式は「判定式で分岐」。"""
        if self.cond_rows:
            return tr('({0}へ判定式で分岐)').format('/'.join(self.targets))
        if len(self.targets) == 1:
            return self.targets[0]
        return tr('({0}から抽選)').format('/'.join(self.targets))

    def describe(self) -> str:
        if self.when_type == WHEN_CHOICE:
            # =275: 「選択肢(1)A 2)B)で→S2/S3」。イベント宛ては「→イベント:X」
            ents = self.choice.entries if self.choice else ()
            items = " ".join("{0}){1}".format(i + 1, e.label)
                             for i, e in enumerate(ents))
            dests = [e.to if not e.to_event else tr("イベント:{0}").format(e.to)
                     for e in ents]
            dests = list(dict.fromkeys(dests))
            return tr('選択肢({0})で→{1}').format(items, "/".join(dests))
        if self.when_type == WHEN_COND:
            cond = tr('判定式({0})').format(
                tr('、').join(_describe_cond(c) for c in self.conds))
            return tr('{0}で→{1}').format(cond, self._to_label())
        if self.when_type == WHEN_ALL_CHANNELS:
            return tr('全チャンネル終了で→{0}').format(self._to_label())
        if self.when_type == WHEN_CHANNEL_END:
            return tr('{0}ch終了で→{1}').format(self.channel, self._to_label())
        if self.when_type == WHEN_STATE_TIME:
            lo, hi = self.min_value / 1000, self.max_value / 1000
            cond = tr('経過{0:g}秒').format(lo) if lo == hi \
                else tr('経過{0:g}〜{1:g}秒').format(lo, hi)
        else:
            lo, hi = int(self.min_value), int(self.max_value)
            cond = tr('{0}chの{1}回').format(self.channel, lo) if lo == hi else tr('{0}chの{1}〜{2}回').format(self.channel, lo, hi)
        return tr('{0}で→{1}').format(cond, self._to_label())


@dataclass
class EventState:
    """イベント内の1ステート。チャンネル群と移行ルールを持つ。"""
    state_id: str
    channels: dict[str, Channel] = field(default_factory=dict)
    device_map: dict[str, str] = field(default_factory=dict)
    transition: StateTransition | None = None  # Noneなら移行しない(イベント終了待ち)
    on_start: tuple = ()          # ステート開始(再入含む)時の変数操作
    on_end: tuple = ()            # ステート自然終了(移行/イベント終了で抜ける)時の変数操作
    seek_channel: str | None = None  # シークバー追従チャンネルの指定(None=自動)
    # =256: BGM指定。None=「前のBGMを引き継ぐ」(ノード入場時に何もしない)。
    # 通常イベントではJSONのイベント直下 "bgm" を "main" ステートへ流し込む
    # (playerはステート入場で一元的に適用する)。
    bgm: "BgmSpec | None" = None
    # 動画(外部mpvで再生。PLAN_VIDEO.md)。フェーズ3-B(=52)で
    # 「イベント/ステート直下の video」から「動画アイテムを持つチャンネル」へ
    # 移行した。旧形式のJSONは migrate_video_node が読み込み時に変換する。
    video_channel: str = ""       # 動画チャンネルのID(""=動画なし)

    @property
    def has_video(self) -> bool:
        return bool(self.video_channel)

    @property
    def video_items(self) -> tuple:
        """動画チャンネルのアイテム列(動画なしなら空)。"""
        ch = self.channels.get(self.video_channel) if self.video_channel else None
        return tuple(ch.items) if ch is not None else ()

    @property
    def device_channel(self) -> str:
        if TRACK_LINEAR in self.device_map:
            return self.device_map[TRACK_LINEAR]
        if self.device_map:
            return next(iter(self.device_map.values()))
        if not self.channels:
            return ""   # 音声なし(チャンネルなし)ステート
        return next(iter(self.channels))

    def seek_follow_channel(self) -> str:
        """シークバー(経過表示・シーク操作)が追従するチャンネルを解決する。

        **動画チャンネルがあれば常にそれ**(シークバーは動画に固定=合意事項。
        =52で動画がチャンネルになったため、seek_channel と同じ仕組みに乗る)。
        以降は指定(seek_channel)があればそれ(=63でスクリプト専用チャンネルも
        指定できるようになった)。無指定なら C→L→R の優先順で音声のある
        チャンネルを自動選択し、どこにも音声が無ければ device_channel
        (従来の担当チャンネル=スクリプト専用chもここで拾われる)へ
        フォールバックする。
        """
        def has_audio(cid: str) -> bool:
            ch = self.channels.get(cid)
            return bool(ch and ch.has_audio)

        if self.video_channel:
            return self.video_channel
        if self.seek_channel and self.seek_channel in self.channels:
            return self.seek_channel
        for cid in ("C", "L", "R"):
            if has_audio(cid):
                return cid
        return self.device_channel


@dataclass
class NextRule:
    """分岐つきのnext指定。

    candidates      : [(イベントID, weight定数|None, weight変数名|None)] の重み付き候補。
                      weightは定数 or 変数参照。実行時に解決した値が0以下の候補は
                      「出さない」(抽選対象から除外)。
    visited_exclude : True=このシナリオ再生中に実行済みのイベントを候補から除外
    exhausted_mode  : 候補が全て実行済みのときの挙動
                      "all"   = 除外をやめて全候補から抽選(既定)
                      "reset" = 候補分の訪問履歴をリセットして再一巡
                      "to"    = exhausted_to のイベントへ
    else_to         : 解決後の重みが全て0以下(=出せる候補が無い)ときの遷移先。
                      None=遷移なし(シナリオ終了)。
    """
    candidates: list = field(default_factory=list)
    visited_exclude: bool = False
    exhausted_mode: str = "all"
    exhausted_to: str | None = None
    else_to: str | None = None


def _is_num(v) -> bool:
    """JSON数値かどうか(boolはPythonではintのサブクラスなので除外する)。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_node_pos(raw: dict, where: str) -> None:
    """=299: イベント図の手動配置座標 "pos": [x, y] を検証する(省略可)。
    表示専用。数値2つの配列以外はエラー(作りかけ検出=40の方針)。"""
    v = raw.get("pos")
    if v is None:
        return
    ok = (isinstance(v, list) and len(v) == 2
          and all(_is_num(n) for n in v))
    if not ok:
        raise ValueError(
            tr('{0}: pos は [x, y] の数値2つで指定してください').format(where))


def check_node_color(raw: dict, where: str) -> None:
    """=124: イベント/ステートのノード色 "color" を検証する(省略可)。

    形式は "#RRGGBB"(16進6桁)のみ。表示専用の飾りだが、書き間違いは
    読み込み時に知らせる(作りかけ検出の方針=40と同じ)。
    """
    v = raw.get("color")
    if v is None:
        return
    ok = (isinstance(v, str) and len(v) == 7 and v.startswith("#"))
    if ok:
        try:
            int(v[1:], 16)
        except ValueError:
            ok = False
    if not ok:
        raise ValueError(
            tr('{0}: color は "#RRGGBB" 形式の文字列で指定してください').format(where))


@dataclass
class VarDecl:
    """シナリオ変数の宣言(トップレベル vars)。

    型は初期値で確定する(数値/文字列)。min/maxは数値変数のみで、
    set/addを問わず**すべての操作結果**がこの範囲にクランプされる。
    """
    name: str
    init: object                  # int/float or str
    is_number: bool = True
    vmin: float | None = None
    vmax: float | None = None

    def clamp(self, v):
        if self.is_number:
            if self.vmin is not None and v < self.vmin:
                v = self.vmin
            if self.vmax is not None and v > self.vmax:
                v = self.vmax
        return v


@dataclass
class VarOp:
    """変数操作1件。kind="set"(代入) / "add"(加算、負数で減算) /
    "mul"(乗算=75) / "roll"(乱数代入) / "eval"(条件式=126)。

    value_var が指定されていれば他変数の現在値を、無ければ value(定数)を使う。
    add/mul は数値変数のみ。mul は「複数の選択肢の全yes判定」等に使える
    (フラグに ×1/×0 を掛けていき、1が残れば全yes)。
    kind="roll" は数値変数へ [value, value2] の一様乱数(整数, 両端含む)を代入する。
    value/value_var が下限、value2/value2_var が上限(それぞれ定数 or 変数)。
    kind="eval"(=126) は cond(VarCond)を評価し、成立=1 / 不成立=0 を
    数値変数へ代入する(x=(a==3) のような条件式代入。ANDが必要なら
    =75の mul 連鎖で表現する)。宣言のmin/maxクランプは結果0/1にも掛かる。
    """
    kind: str
    name: str
    value: object = None
    value_var: str | None = None
    value2: object = None
    value2_var: str | None = None
    cond: object = None            # =126 eval用(VarCond)


@dataclass
class VarCond:
    """変数判定式1件: 変数name op 右辺(定数 or 変数value_var)。"""
    name: str
    op: str                       # == / != / >= / <= / > / <
    value: object = None
    value_var: str | None = None

    def eval(self, vars_: dict) -> bool:
        lhs = vars_[self.name]
        rhs = vars_[self.value_var] if self.value_var is not None else self.value
        if self.op == "==":
            return lhs == rhs
        if self.op == "!=":
            return lhs != rhs
        if self.op == ">=":
            return lhs >= rhs
        if self.op == "<=":
            return lhs <= rhs
        if self.op == ">":
            return lhs > rhs
        return lhs < rhs


def _describe_cond(c: "VarCond") -> str:
    """判定式1件を短い日本語テキストにする(UI/デバッグ表示用)。"""
    rhs = tr("変数{0}").format(c.value_var) if c.value_var is not None else c.value
    return "{0} {1} {2}".format(c.name, c.op, rhs)


@dataclass
class CondRule:
    """変数分岐つきのnext指定。

    rows    : [(AND条件リスト, 遷移先イベントID)]。上から順に評価し最初に成立した行へ
    else_to : どの行も成立しなかったときの遷移先(None=再生終了)。elseキーは必須
    """
    rows: list = field(default_factory=list)
    else_to: str | None = None

    def pick(self, vars_: dict) -> str | None:
        for conds, to in self.rows:
            if all(c.eval(vars_) for c in conds):
                return to
        return self.else_to


class ChoiceEntry(NamedTuple):
    """選択肢1件。ops=選択された時に実行する変数操作。

    =275: to_event はステート移行の選択肢(WHEN_CHOICE)専用。True なら to は
    遷移先**イベント**ID(イベントを終了して飛ぶ)、False ならステートID。
    イベントの next choice では常に False(to はイベントID)。
    """
    label: str
    to: str
    ops: tuple = ()
    to_event: bool = False


@dataclass
class WatchRule:
    """監視(watch): 変数条件の常時監視トリガー(トップレベル)。

    変数操作の適用直後に条件を評価し、成立したら to のイベントへ遷移する。
    conds : AND条件(全て成立で発火)
    to    : 遷移先イベントID。**toのイベント再生中はこの監視を評価しない**(ループ防止)
    mode  : "graceful"(既定)=再生中の音声を最後まで終えてから遷移
            / "interrupt"=即時打ち切りで遷移
    once  : True(既定)=1回の再生につき1度だけ発火 / False=条件が成立するたび発火
    """
    conds: tuple = ()
    to: str = ""
    mode: str = "graceful"
    once: bool = True


@dataclass
class ChoiceRule:
    """選択肢つきのnext指定。

    entries      : ChoiceEntry(label, to, ops) 1〜9件
    timeout_ms   : タイムリミット(ms)。None=無制限
    on_timeout   : タイムアウト確定時に実行する変数操作(タイムリミット未設定なら発火しない)
    default_mode : デフォルト遷移先(タイムアウト時・▶▶スキップ時)の決め方
                   "first"=先頭候補(既定) / "random"=候補から等確率抽選
                   / "to"=default_toの指定イベント(選択肢に無いイベントも可)
    default_to   : default_mode="to" のときの遷移先イベントID
    skip_stay    : True=選択必須(=274)。▶▶では飛ばさず選択されるまで待機する
                   (音声再生中の▶▶は音声を打ち切って待機へ進むだけ。
                   タイムリミット併用時のタイムアウト遷移は従来どおり進む)
    show_mode    : 選択肢を表示するタイミング
                   "start"=イベント開始時 / "end"=イベント終了条件の達成時(既定)
                   / "ms"=イベント開始からshow_ms後
    show_ms      : show_mode="ms" のときの経過時間(ms)
    """
    entries: list[ChoiceEntry] = field(default_factory=list)
    timeout_ms: int | None = None
    on_timeout: tuple = ()
    default_mode: str = "first"
    default_to: str | None = None
    skip_stay: bool = False
    show_mode: str = "end"
    show_ms: int = 0
    # =275: ステート移行の選択肢で default_mode="to" の行き先がイベントか
    default_to_event: bool = False

    def pick_default(self) -> str | None:
        """デフォルト遷移先を決める(タイムアウト/▶▶スキップ共通)。"""
        return self.pick_default_info()[0]

    def pick_default_info(self) -> tuple:
        """=275: デフォルト遷移先を (to, to_event) で返す。

        ステート移行の選択肢では to_event=True のときイベントへ飛ぶ。
        イベントの next choice では常に False。
        """
        if self.default_mode == "random" and self.entries:
            import random as _r
            e = _r.choice(self.entries)
            return e.to, e.to_event
        if self.default_mode == "to" and self.default_to:
            return self.default_to, self.default_to_event
        if self.entries:
            return self.entries[0].to, self.entries[0].to_event
        return None, False

    @property
    def state_targets(self) -> list:
        """=275: ステート宛ての行き先(候補+既定)。重複除去・順序維持。"""
        out = [e.to for e in self.entries if not e.to_event]
        if self.default_mode == "to" and self.default_to \
                and not self.default_to_event:
            out.append(self.default_to)
        return list(dict.fromkeys(out))

    @property
    def event_targets(self) -> list:
        """=275: イベント宛ての行き先(候補+既定)。重複除去・順序維持。"""
        out = [e.to for e in self.entries if e.to_event]
        if self.default_mode == "to" and self.default_to \
                and self.default_to_event:
            out.append(self.default_to)
        return list(dict.fromkeys(out))


@dataclass
class InputRule:
    """数値入力つきのnext指定。

    再生UIに数値入力欄と決定ボタンを表示し、入力値を変数へセットして
    to のイベントへ遷移する。▶▶スキップは変数を変更せずに to へ進む。

    var       : セット先の数値変数名(宣言必須)
    to        : 遷移先イベントID
    label     : 入力欄の見出しテキスト(省略時はUI側の既定文言)
    vmin/vmax : 受理する入力範囲(input側のmin/max、省略時は変数宣言のmin/max)。
                範囲外の入力はUIがエラー表示して再入力を求める。None=制限なし
    show_mode : 入力欄を表示するタイミング(choiceと同じ)
                "start"=イベント開始時 / "end"=終了条件の達成時(既定)
                / "ms"=イベント開始からshow_ms後
    show_ms   : show_mode="ms" のときの経過時間(ms)
    skip_stay : True=入力必須(=288)。▶▶では飛ばさず入力されるまで待機する
                (音声中の▶▶は音声だけ打ち切って入力待ちへ)。JSON "skip": "stay"
    """
    var: str
    to: str
    label: str = ""
    vmin: float | None = None
    vmax: float | None = None
    show_mode: str = "end"
    show_ms: int = 0
    skip_stay: bool = False

    def accepts(self, value: float) -> bool:
        """入力値が受理範囲内かを判定する。"""
        if self.vmin is not None and value < self.vmin:
            return False
        if self.vmax is not None and value > self.vmax:
            return False
        return True


@dataclass
class ScenarioEvent:
    event_id: str
    states: dict[str, EventState] = field(default_factory=dict)
    start_state: str = "main"
    next: str | None = None              # 単純遷移(文字列next)。分岐/選択肢/変数分岐時はNone
    next_rule: NextRule | None = None    # 分岐つき遷移(オブジェクトnext)
    next_choice: ChoiceRule | None = None  # 選択肢つき遷移
    next_cond: CondRule | None = None    # 変数分岐つき遷移
    next_input: InputRule | None = None  # 数値入力つき遷移
    # =123 すごろく(advance): このイベントを抜けるとき next の解決を
    # 合計N回繰り返し、N番目の着地イベントだけを再生する(途中のイベントは
    # 「通過」= 再生せず on_start/on_end/監視も発火しない。遷移先はその
    # イベント自身の next(候補・重み・cond)に依拠)。定数(1以上の整数) or
    # 変数参照(実行時に解決・1〜999にクランプ)。適用は next規則の遷移のみ
    # (選択肢/数値入力/監視のgoto・◀◀には適用されない)。
    advance: int | None = None
    advance_ref: NumRef | None = None
    on_start: tuple = ()                 # イベント開始(◀◀での再実行含む)時の変数操作
    on_end: tuple = ()                   # イベント自然終了時の変数操作(停止/▶▶/◀◀では発火しない)

    # イベント全体の終了条件(ステートを何度移行しても累積で判定される)
    #   duration    : イベント滞在の合計時間(一時停止除く)
    #   plays       : 全チャンネル合計の実行回数
    #   transitions : ステート移行回数
    #   channel     : (単一ステートのみ)指定チャンネルの終了
    #   once        : (単一ステートのみ)全チャンネルの終了
    end_type: str = END_ONCE
    end_count: int = 1
    end_duration_ms: int = 0
    end_channel: str | None = None
    # 変数参照(指定時はイベントに入った時点(on_start適用後)の変数値で解決)
    end_count_ref: NumRef | None = None
    end_duration_refs: tuple | None = None
    # duration 範囲抽選(min秒〜max秒)。end_duration_range=True のとき有効。
    # 値はミリ秒(定数)、refはNumRef(秒。入場時に解決して *1000)。
    end_duration_range: bool = False
    end_duration_min_ms: float = 0
    end_duration_max_ms: float = 0
    end_duration_min_ref: NumRef | None = None
    end_duration_max_ref: NumRef | None = None
    # end_type==END_COND のときの判定式(AND条件、再生中に常時評価)
    end_conds: tuple = ()
    # end_type==END_STATES のときの終了ステートID群(このいずれかが終了したら終了)
    end_states: tuple = ()

    def resolve_end(self, vars_: dict) -> tuple[int, int]:
        """終了条件の数値を解決する(イベント入場時に呼ぶ)。(count, duration_ms)。

        duration が範囲抽選(min〜max)のときは、入場ごとに [min, max] の一様乱数
        (ミリ秒)を抽選する。ステート移行の state_time と同じ挙動。
        """
        import random as _r
        count = self.end_count
        if self.end_count_ref is not None:
            count = max(1, int(self.end_count_ref.resolve(vars_)))
        if self.end_duration_range:
            lo = (self.end_duration_min_ref.resolve(vars_) * 1000
                  if self.end_duration_min_ref is not None
                  else self.end_duration_min_ms)
            hi = (self.end_duration_max_ref.resolve(vars_) * 1000
                  if self.end_duration_max_ref is not None
                  else self.end_duration_max_ms)
            lo, hi = max(0.0, lo), max(0.0, hi)
            if hi < lo:
                lo, hi = hi, lo
            ms = int(lo) if lo >= hi else int(_r.uniform(lo, hi))
            return count, ms
        ms = self.end_duration_ms
        if self.end_duration_refs is not None:
            m_ref, s_ref = self.end_duration_refs
            m = m_ref.resolve(vars_) if isinstance(m_ref, NumRef) else float(m_ref or 0)
            s = s_ref.resolve(vars_) if isinstance(s_ref, NumRef) else float(s_ref or 0)
            ms = max(0, int(m * 60_000 + s * 1_000))
        return count, ms

    @property
    def is_multi_state(self) -> bool:
        return len(self.states) > 1 or self.start_state != "main"

    @property
    def has_state_choice(self) -> bool:
        """=275: いずれかのステートが「選択肢でステート移行」を持つか。"""
        return any(st.transition is not None
                   and st.transition.when_type == WHEN_CHOICE
                   for st in self.states.values())

    def describe_end_resolved(self, count: int, ms: int) -> str:
        """入場時に解決した終了条件を1行で説明する(=56の再生ログ用)。

        範囲抽選(min〜max秒)のときは「合計80〜100秒(今回87秒)」のように、
        設定値と今回の抽選結果の両方を出す。
        """
        t = self.end_type
        if t == END_DURATION:
            if (self.end_duration_range
                    and self.end_duration_min_ref is None
                    and self.end_duration_max_ref is None
                    and self.end_duration_min_ms != self.end_duration_max_ms):
                return tr('合計{0:g}〜{1:g}秒(今回{2:g}秒)').format(
                    self.end_duration_min_ms / 1000,
                    self.end_duration_max_ms / 1000, round(ms / 1000, 1))
            return tr('合計{0:g}秒').format(round(ms / 1000, 1))
        if t == END_PLAYS:
            return tr('合計{0}回再生').format(count)
        if t == END_TRANSITIONS:
            return tr('ステート移行{0}回').format(count)
        if t == END_CHANNEL:
            return tr('チャンネル{0}の終了').format(self.end_channel)
        if t == END_COND:
            return tr('変数条件の成立')
        if t == END_STATES:
            return tr('ステート{0}の終了').format('/'.join(self.end_states))
        if t == END_NONE:
            # =168: 無限(イベント自身の終了条件なし)
            return tr('無限(選択肢・判定式・手動操作で次へ)')
        return tr('全チャンネルの終了')

    def describe_end(self) -> str:
        if self.end_type == END_CHANNEL:
            return tr('{0}chの終了で次へ').format(self.end_channel)
        if self.end_type == END_ONCE:
            return tr("1周で終了")
        if self.end_type == END_REPEAT:
            return tr('{0}周で終了').format(self.end_count)
        if self.end_type == END_PLAYS:
            return tr('合計{0}回の実行で次へ').format(self.end_count)
        if self.end_type == END_TRANSITIONS:
            return tr('{0}回のステート移行で次へ').format(self.end_count)
        if self.end_type == END_COND:
            return tr('判定式({0})の成立で次へ').format(
                tr('、').join(_describe_cond(c) for c in self.end_conds))
        if self.end_type == END_STATES:
            return tr('ステート{0}の終了で次へ').format('/'.join(self.end_states))
        if self.end_type == END_DURATION:
            # 時間指定は秒に統一(=24)。変数参照durationは秒未確定なので変数名で表示
            if self.end_duration_range:
                def _b(ms, ref):
                    return tr('変数{0}').format(ref.var) if ref is not None else f"{ms/1000:g}"
                lo = _b(self.end_duration_min_ms, self.end_duration_min_ref)
                hi = _b(self.end_duration_max_ms, self.end_duration_max_ref)
                if lo == hi:
                    return tr('合計{0}秒を超えたら次へ').format(lo)
                return tr('合計{0}〜{1}秒(抽選)を超えたら次へ').format(lo, hi)
            s_ref = self.end_duration_refs[1] if self.end_duration_refs else None
            if isinstance(s_ref, NumRef):
                return tr('合計(変数{0})秒を超えたら次へ').format(s_ref.var)
            total_sec = self.end_duration_ms / 1000
            return tr('合計{0:g}秒を超えたら次へ').format(total_sec)
        return self.end_type
