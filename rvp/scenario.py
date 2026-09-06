"""シナリオファイル(.json)の読み込み。

# イベント形式 (v2)

{
  "title": "シナリオ名",
  "start": "開始イベントID",
  "events": {
    "イベントID": {
      "mode": "sequential" | "random" | "random_bag",
      "items": [
        "音声ファイル.wav",              // 文字列: 同名funscriptをlinearとして自動対応
        {                                // オブジェクト: 明示指定
          "audio": "voice.wav",
          "funscript": "stroke.funscript",  // linear単一(後方互換)
          "weight": 20
        },
        {
          "audio": "voice.wav",
          "tracks": [                    // 複数デバイスを明示指定
            {"type": "linear",    "funscript": "a.funscript"},
            {"type": "vibration", "funscript": "b.funscript"},
            {"type": "rotate",    "funscript": "c.funscript"}
          ]
        }
      ],
      "end": {"type": "once"} | {"type":"repeat","count":3} | {"type":"duration","seconds":360},
      "next": "次のイベントID"
    }
  }
}

## funscript(トラック)の指定方法

1つの音声に、デバイス種別ごとの funscript を紐づける。指定方法は3通り:

- **tracks 配列**: 種別(type)とfunscriptの組を複数指定できる。
  同じ音声でもイベントごとに別のfunscriptを割り当てられる(itemは各イベントで独立)。
  type は linear / rotate / vibration。
- **funscript 単一**: "funscript": "x.funscript" は linear 1本の指定(後方互換)。
  "funscript": null で自動対応を無効化。
- **未指定**: 同名の .funscript があれば linear として自動対応。

注: 現行の再生エンジンが実際に駆動するのは linear のみ。rotate / vibration の
トラックは読み込み・検証されるが、対応デバイスの実装は今後追加する
(シナリオファイルは将来分を先に記述しておける)。

# ノード形式 (v1, 後方互換)

"nodes" キーを持つ旧形式もそのまま読み込める。内部的には
「音声1件のsequentialイベント(linear1トラック)」へ変換される。
"""

import json
import os
import wave
from .i18n import tr
from dataclasses import dataclass, field
from typing import NamedTuple

from .funscript import Funscript
from .rotate_source import load_rotate_source

MODE_SEQUENTIAL = "sequential"
MODE_RANDOM = "random"
# =76: ランダム(重複なし)=ランダムバッグ方式。全アイテムを一通り再生して
# から次の周回の抽選を行う。終了条件などの扱いは random と同じ。
MODE_RANDOM_BAG = "random_bag"

END_ONCE = "once"
END_REPEAT = "repeat"
END_DURATION = "duration"
END_CHANNEL = "channel"      # イベント用: 指定チャンネルの終了でイベント終了
END_NONE = "none"            # チャンネル用: 終了条件なし(イベント/ステート終了まで無限に再生)
END_PLAYS = "plays"          # イベント用: 合計実行回数(全チャンネル)で終了
                             # チャンネル用: そのチャンネルでN回再生したら終了
                             # (count=1 + random で「どれか1曲流して終了」)
END_TRANSITIONS = "transitions"  # イベント用: ステート移行回数で終了
END_COND = "cond"            # イベント用: 変数の判定式(AND)が成立したら終了
END_STATES = "states"        # イベント用(ステート形式): 指定ステートが終了したら終了

# ステート移行条件のタイプ
WHEN_CHANNEL_COUNT = "channel_count"  # 指定chの実行回数
WHEN_COND = "cond"                    # 変数の判定式(AND)が成立したら移行
WHEN_ALL_CHANNELS = "all_channels"    # ステート内の全チャンネルが自然終了したら移行
WHEN_CHANNEL_END = "channel_end"      # 指定chが終了したら移行(他chはワインドダウン)
# =62: ステートに入ってからの経過時間(秒)。チャンネル不要=音声なし
# (チャンネルなし)ステートの「無音待機」でも使える唯一の時間系移行条件。
WHEN_STATE_TIME = "state_time"
# =275: 選択肢でステート移行。選ばれた選択肢(またはタイムアウトの既定)で
# 次のステート、あるいは遷移先イベントが決まる。transition の "to" は持たず、
# イベントの next choice と同じキー(choice/timeout/on_timeout/default/skip/
# show)を transition 直下に置く(show の意味はステート基準)。
WHEN_CHOICE = "choice"
# =165で廃止: "channel_time"(指定chの累積再生時間)。state_time と役割が
# 重複するため撤廃した(ユーザー決定 2026-08-16)。読み込み時にエラーにする。
WHEN_CHANNEL_TIME_REMOVED = "channel_time"


TRACK_LINEAR = "linear"
# TWIST(=79): 2軸linearデバイスの2軸目(有限回転)。funscript仕様はlinearと
# 完全に同一(pos 0-100)で、駆動先が各デバイスのlinearアクチュエータ index1。
TRACK_TWIST = "twist"
# ROTATE(ufo): ufosa / ufotw 系のrotate。旧称は "rotate"(下記エイリアスで互換)。
TRACK_ROTATE_UFO = "rotate_ufo"
# 後方互換の別名: 旧コード/旧テストが参照する TRACK_ROTATE を残す(値は新種別)。
TRACK_ROTATE = TRACK_ROTATE_UFO
# A10サイクロンSA専用のrotate。funscript仕様はrotateと完全に同一だが、
# 接続時のデバイス名判定でA10のみを駆動する(通常rotateとは完全分離)
TRACK_ROTATE_A10 = "rotate_a10cyclonesa"
TRACK_VIBRATION = "vibration"
VALID_TRACK_TYPES = (TRACK_LINEAR, TRACK_TWIST, TRACK_ROTATE_UFO,
                     TRACK_ROTATE_A10, TRACK_VIBRATION)

# 旧トラック種別名 → 新種別名の読み替え(既存シナリオ/設定の互換維持)。
# 旧 "rotate" は "rotate_ufo" として扱う。保存時は新名で書き出す。
OLD_TRACK_ALIASES = {"rotate": TRACK_ROTATE_UFO}


def wav_duration_ms(path: str) -> int:
    """wavの長さ(ミリ秒)。wav以外・読めない場合は0(=検証をスキップ)。

    区間指定(=59)が素材の長さを超えていないかを**読み込み時に**検証する
    ために使う(ユーザー決定: 動画と違い音声・スクリプトは長さが分かるので
    保存・読み込み時にエラーにする)。pygame に依存しないよう wave のみ。
    """
    try:
        with wave.open(path, "rb") as w:
            return int(w.getnframes() / w.getframerate() * 1000)
    except Exception:
        return 0


def load_script_source(path: str):
    """funscript / CSV を種別に応じて読み込む(検証・再生で共通)。"""
    if path.lower().endswith(".csv"):
        return load_rotate_source(path)
    return Funscript.load(path)


def track_range_ms(item, track) -> tuple:
    """トラックに実際に適用される区間(開始ms, 終了ms or None)を返す(=59)。

    **トラック個別の指定 > アイテムの区間**(ユーザー決定=C案)。
    """
    if track.has_range:
        start_s, end_s = track.start_s, track.end_s
    else:
        start_s, end_s = item.start_s, item.end_s
    return start_s * 1000.0, (None if end_s is None else end_s * 1000.0)


def normalize_track_type(ttype: str) -> str:
    """旧トラック種別名を新名へ正規化する(未知はそのまま返す)。"""
    return OLD_TRACK_ALIASES.get(ttype, ttype)


# funscript自動紐づけの種別タグ(ファイル名に含まれる文字列、大文字小文字無視)。
# LINEARはタグなし(標準デバイス)。=79: twist はタグ "twist"。
AUTO_FS_TAGS = {TRACK_TWIST: "twist",
                TRACK_ROTATE_UFO: "ufo", TRACK_ROTATE_A10: "a10",
                TRACK_VIBRATION: "vib"}


# CSVを紐づけられる種別(ROTATE系レーンのみ。linear/twist/vibrationはfunscriptのみ)。
CSV_TRACK_TYPES = (TRACK_ROTATE_UFO, TRACK_ROTATE_A10)


def auto_bind_tracks(audio_abs: str) -> list[tuple[str, str]]:
    """wavと同じフォルダから命名ルールで funscript / CSV を自動紐づけする。

    ルール(いずれも大文字小文字無視):
      - 「wavファイル名(拡張子なし)を含む」.funscript / .csv が候補
      - 候補のうちタグを含むものは対応種別へ:
        「twist」→twist / 「ufo」→rotate_ufo / 「a10」→rotate_a10cyclonesa /
        「vib」→vibration(複数タグを含むファイルは該当する全種別の候補になる)
      - タグを含まない .funscript 候補は linear
      - **CSVはROTATE系(rotate_ufo/rotate_a10cyclonesa)にのみ紐づく**
        (linear/twist/vibrationのCSVは無視。タグ無しCSVも無視)
      - 同一種別に候補が複数ある場合は決定的に1つ選ぶ:
        **CSVを優先**(ROTATEはCSVが主流)。同一拡張子内では
        linearは完全一致(同名)を最優先、それ以外/同率は短い順→辞書順で先頭

    戻り値: [(種別, 絶対パス)] を VALID_TRACK_TYPES の順で返す。
    """
    folder = os.path.dirname(audio_abs) or "."
    base_cf = os.path.splitext(os.path.basename(audio_abs))[0].casefold()
    if not base_cf:
        return []
    try:
        files = os.listdir(folder)
    except OSError:
        return []
    cands: dict[str, list[str]] = {t: [] for t in VALID_TRACK_TYPES}
    for fname in files:
        cf = fname.casefold()
        if cf.endswith(".funscript"):
            stem_cf, is_csv = cf[:-len(".funscript")], False
        elif cf.endswith(".csv"):
            stem_cf, is_csv = cf[:-len(".csv")], True
        else:
            continue
        if base_cf not in stem_cf:
            continue
        matched = [t for t, tag in AUTO_FS_TAGS.items() if tag in stem_cf]
        if is_csv:
            # CSVはROTATE系レーンのタグを持つものだけ対象
            for t in matched:
                if t in CSV_TRACK_TYPES:
                    cands[t].append(fname)
        elif matched:
            for t in matched:
                cands[t].append(fname)
        else:
            cands[TRACK_LINEAR].append(fname)
    out: list[tuple[str, str]] = []
    for t in VALID_TRACK_TYPES:
        lst = cands[t]
        if not lst:
            continue
        # CSV優先: CSV候補があればCSVプールから、無ければfunscriptプールから選ぶ
        csvs = [f for f in lst if f.casefold().endswith(".csv")]
        pool = csvs if csvs else lst
        pick = None
        if t == TRACK_LINEAR:
            exact = [f for f in pool
                     if f.casefold() == base_cf + ".funscript"]
            if exact:
                pick = exact[0]
        if pick is None:
            pick = sorted(pool, key=lambda f: (len(f), f.casefold()))[0]
        out.append((t, os.path.join(folder, pick)))
    return out

# チャンネル識別子
CH_LEFT = "L"
CH_CENTER = "C"
CH_RIGHT = "R"
VALID_CHANNELS = (CH_LEFT, CH_CENTER, CH_RIGHT)

# チャンネル別デフォルトのパン(left, right) 各0.0〜1.0
DEFAULT_PAN = {
    CH_LEFT:   (1.0, 0.2),
    CH_CENTER: (1.0, 1.0),
    CH_RIGHT:  (0.2, 1.0),
}


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


# ---- 旧形式(イベント/ステート直下の "video")→ 動画チャンネルへの移行 ----
# フェーズ3-B(=52)で動画は「チャンネル内のアイテム」になった。旧形式の
# シナリオを読めるようにするため、パース前に raw dict を書き換える
# (エディタも同じ関数を通すので、開いて保存すれば新形式で書き出される)。
# ユーザー決定: 自動移行して新形式に統一する / ループ☑は終了条件へ一本化。

def raw_video_channel(raw: dict) -> str:
    """raw の channels から動画チャンネルのIDを返す(無ければ "")。"""
    for cid, ch in (raw.get("channels") or {}).items():
        if not isinstance(ch, dict):
            continue
        for it in ch.get("items") or []:
            if isinstance(it, dict) and it.get("video") is not None:
                return cid
    return ""


def free_video_channel(raw: dict) -> str:
    """動画チャンネルに使える空きチャンネルID(C→L→R優先)。無ければ ""。"""
    used = set((raw.get("channels") or {}).keys())
    for cid in (CH_CENTER, CH_LEFT, CH_RIGHT):
        if cid not in used:
            return cid
    return ""


def migrate_video_node(raw: dict, where: str = "", is_state: bool = False) -> dict:
    """イベント/ステート直下の "video" を動画チャンネルへ移行した dict を返す。

    - 動画は空きチャンネル(C→L→R優先)へ「動画アイテム1つのチャンネル」として入る。
    - ループ☑は終了条件へ一本化する(ユーザー決定):
        loop なし → チャンネル終了条件 "once"(1周で終了。動画が終わったら終わる)
        loop あり → "none"(無限。イベント/ステート側の終了条件で抜ける)
    - 通常イベントで end 未指定 かつ 非ループ だった場合は、旧挙動
      「動画が終わったらイベント終了」を保つため end を
      {"type":"channel","channel":動画ch} にする。
    - 空きチャンネルが無い(L/C/R すべて使用中)場合はエラー。ユーザー決定に
      より動画は L/C/R のどれか1つを占有するため、1ch空ける必要がある。
    """
    rv = raw.get("video")
    if rv is None:
        return raw
    cid = free_video_channel(raw)
    if not cid:
        raise ValueError(
            tr('{0}: 動画を置くチャンネルの空きがありません(動画は L/C/R のどれか1つを使います。どれか1チャンネルを空けてください)').format(where))

    loop = False
    item: dict = {}
    if isinstance(rv, str):
        item["video"] = rv
    elif isinstance(rv, dict):
        loop = bool(rv.get("loop", False))
        vid: dict = {"file": rv.get("file")}
        for key in ("start", "end"):
            if rv.get(key) is not None:
                vid[key] = rv[key]
        item["video"] = vid
        for key in ("tracks", "funscript"):
            if key in rv:
                item[key] = rv[key]
        # 旧形式で保持していた未知キーは動画アイテム側へ引き継ぐ
        for key, val in rv.items():
            if key not in ("file", "loop", "start", "end", "tracks", "funscript"):
                item[key] = val
    else:
        raise ValueError(tr('{0}: video の指定が不正です').format(where))

    out = dict(raw)
    out.pop("video", None)
    channels = dict(out.get("channels") or {})
    channels[cid] = {"mode": MODE_SEQUENTIAL, "items": [item],
                     "end": {"type": END_NONE if loop else END_ONCE}}
    out["channels"] = channels
    if not is_state and out.get("end") is None and not loop:
        # 旧挙動「動画が終わったらイベント終了」を明示的な終了条件で再現する
        out["end"] = {"type": END_CHANNEL, "channel": cid}
    return out


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


@dataclass
class Scenario:
    title: str
    start: str
    events: dict[str, ScenarioEvent] = field(default_factory=dict)
    path: str = ""
    detail: str = ""   # シナリオの説明文(作成者の自由記入、改行可)
    var_decls: dict[str, VarDecl] = field(default_factory=dict)  # トップレベルvars宣言
    watches: tuple = ()   # 監視(watch)トリガー(トップレベル、宣言順に評価)
    # =130: 読み込み時の警告(エラーにはしないが高確率で意図と違う構成)。
    # 現状は「デバイス担当チャンネルに、その種別のトラックが1つも無い」のみ。
    load_warnings: list = field(default_factory=list)
    # =252: デバイス連動フラグ(トップレベル "device_enabled")。
    # 省略=True(旧シナリオは従来どおり)。False のシナリオは、トラックの
    # 紐づけ定義がJSONに残っていても**再生時にデバイスを駆動しない**。
    # UI側(再生タブの④デバイス調整・⑤グラフ、編集画面のデバイス関連)も
    # このフラグで非表示になる。
    device_enabled: bool = True
    # =256: BGM機能フラグ(トップレベル "bgm_enabled")。**省略=False**
    # (BGMを持つ旧シナリオは存在しないため、device_enabled とは省略時の
    # 意味が逆)。False のシナリオは "bgm" 定義がJSONに残っていても
    # 再生時にBGMを鳴らさない(UIも非表示。データは保持=device と同じ方式)。
    bgm_enabled: bool = False
    # =262: 背景イラスト(トップレベル "background")。省略=None=なし。
    background: "BackgroundSpec | None" = None

    # ---------------- 読み込み ----------------

    @classmethod
    def load(cls, path: str) -> "Scenario":
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        base_dir = os.path.dirname(os.path.abspath(path))
        load_warnings: list[str] = []   # =130 読み込み警告(エラーにしない)

        # =252: デバイス連動フラグ(省略=True=従来どおり)。bool以外はエラー
        device_enabled = data.get("device_enabled", True)
        if not isinstance(device_enabled, bool):
            raise ValueError(
                tr("device_enabled は true/false で指定してください"))

        # =256: BGM機能フラグ(省略=False)。bool以外はエラー
        bgm_enabled = data.get("bgm_enabled", False)
        if not isinstance(bgm_enabled, bool):
            raise ValueError(
                tr("bgm_enabled は true/false で指定してください"))

        def resolve(p: str | None) -> str | None:
            if not p:
                return None
            return p if os.path.isabs(p) else os.path.normpath(os.path.join(base_dir, p))

        # ---------------- 背景イラスト(=262) ----------------
        # 文字列("path")と辞書({"file","dim"})の2書式。dim省略=40。
        background = None
        raw_bg = data.get("background")
        if raw_bg is not None:
            if isinstance(raw_bg, str):
                raw_bg = {"file": raw_bg}
            if not isinstance(raw_bg, dict):
                raise ValueError(
                    tr("background は文字列かオブジェクトで指定してください"))
            bg_file = raw_bg.get("file")
            if not isinstance(bg_file, str) or not bg_file.strip():
                raise ValueError(tr("background: file を指定してください"))
            bg_dim = raw_bg.get("dim", 40)
            if isinstance(bg_dim, bool) or not isinstance(bg_dim, (int, float)) \
                    or not (0 <= bg_dim <= 100):
                raise ValueError(
                    tr("background: dim は 0〜100 の数値で指定してください"))
            background = BackgroundSpec(file=resolve(bg_file),
                                        dim=int(round(bg_dim)))

        # ---------------- 変数(vars) ----------------

        def parse_vars(raw) -> dict[str, VarDecl]:
            decls: dict[str, VarDecl] = {}
            if raw is None:
                return decls
            if not isinstance(raw, dict):
                raise ValueError(tr("vars はオブジェクトで指定してください"))
            for name, v in raw.items():
                w = tr("vars '{0}'").format(name)
                if not isinstance(name, str) or not name.strip():
                    raise ValueError(tr("vars: 変数名が不正です"))
                vmin = vmax = None
                if isinstance(v, dict):
                    if "init" not in v:
                        raise ValueError(tr("{0}: init が必要です").format(w))
                    init = v["init"]
                    vmin, vmax = v.get("min"), v.get("max")
                else:
                    init = v
                is_num = _is_num(init)
                if not is_num and not isinstance(init, str):
                    raise ValueError(
                        tr("{0}: 初期値は数値か文字列にしてください").format(w))
                if vmin is not None or vmax is not None:
                    if not is_num:
                        raise ValueError(
                            tr("{0}: min/max は数値変数のみ指定できます").format(w))
                    for label, bound in (("min", vmin), ("max", vmax)):
                        if bound is not None and not _is_num(bound):
                            raise ValueError(
                                tr("{0}: {1} は数値にしてください").format(w, label))
                    if vmin is not None and vmax is not None and vmax < vmin:
                        raise ValueError(
                            tr("{0}: max は min 以上にしてください").format(w))
                    if (vmin is not None and init < vmin) or \
                       (vmax is not None and init > vmax):
                        raise ValueError(
                            tr("{0}: 初期値が min/max の範囲外です").format(w))
                decls[name] = VarDecl(name=name, init=init, is_number=is_num,
                                      vmin=vmin, vmax=vmax)
            return decls

        var_decls = parse_vars(data.get("vars"))

        def _require_var(name, where) -> VarDecl:
            if not isinstance(name, str) or name not in var_decls:
                raise ValueError(
                    tr("{0}: 変数 '{1}' が vars で宣言されていません").format(where, name))
            return var_decls[name]

        def _parse_rhs(value, where):
            """右辺(定数 or {"var": Y})を (const, var_name, is_number) で返す。"""
            if isinstance(value, dict):
                ref = _require_var(value.get("var"), where)
                return None, ref.name, ref.is_number
            if _is_num(value):
                return value, None, True
            if isinstance(value, str):
                return value, None, False
            raise ValueError(
                tr("{0}: value は数値・文字列・{{\"var\": ...}} のいずれかにしてください").format(where))

        def parse_numref(v, where):
            """数値欄の値を解析する。(静的値orNone, NumReforNone) を返す。

            数値ならそのまま、{"var": "X"} なら数値変数の参照として受理する。
            """
            if isinstance(v, dict):
                ref = _require_var(v.get("var"), where)
                if not ref.is_number:
                    raise ValueError(
                        tr("{0}: 変数 '{1}' は数値変数ではありません").format(where, ref.name))
                return None, NumRef(var=ref.name)
            if _is_num(v):
                return float(v), None
            raise ValueError(
                tr('{0}: 数値か {{"var": "..."}} で指定してください').format(where))

        def parse_ops(raw, where) -> tuple:
            """変数操作リストを解析する。省略はOK(空タプル)。"""
            if raw is None:
                return ()
            if not isinstance(raw, list):
                raise ValueError(tr("{0}: 変数操作はリストで指定してください").format(where))
            ops = []
            for i, o in enumerate(raw):
                w = tr("{0} 操作[{1}]").format(where, i)
                present = [k for k in ("set", "add", "mul", "roll", "eval")
                           if k in o]
                if not isinstance(o, dict) or len(present) != 1:
                    raise ValueError(
                        tr("{0}: set / add / mul / roll / eval のいずれか1つを指定してください").format(w))
                kind = present[0]
                target = _require_var(o[kind], w)
                if kind == "eval":
                    # =126 条件式: when の判定式(1件)を評価し 1/0 を代入
                    if not target.is_number:
                        raise ValueError(
                            tr("{0}: eval は数値変数にのみ使えます").format(w))
                    if not isinstance(o.get("when"), dict):
                        raise ValueError(
                            tr('{0}: eval には when({{"var","op","value"}}) が必要です').format(w))
                    conds = parse_conds([o["when"]], w)
                    ops.append(VarOp(kind="eval", name=target.name,
                                     cond=conds[0]))
                    continue
                if kind == "roll":
                    # 乱数代入: 数値変数へ [min, max] の一様乱数(整数)を代入
                    if not target.is_number:
                        raise ValueError(
                            tr("{0}: roll は数値変数にのみ使えます").format(w))
                    if "min" not in o or "max" not in o:
                        raise ValueError(
                            tr("{0}: roll には min と max が必要です").format(w))
                    mn_c, mn_v, mn_num = _parse_rhs(o["min"], w)
                    mx_c, mx_v, mx_num = _parse_rhs(o["max"], w)
                    if not mn_num or not mx_num:
                        raise ValueError(
                            tr("{0}: roll の min/max は数値にしてください").format(w))
                    ops.append(VarOp(kind="roll", name=target.name,
                                     value=mn_c, value_var=mn_v,
                                     value2=mx_c, value2_var=mx_v))
                    continue
                if "value" not in o:
                    raise ValueError(tr("{0}: value が必要です").format(w))
                const, var_name, rhs_num = _parse_rhs(o["value"], w)
                if kind in ("add", "mul"):
                    # =75: mul(乗算)は add と同じ制約(数値変数×数値のみ)
                    if not target.is_number:
                        raise ValueError(
                            tr("{0}: {1} は数値変数にのみ使えます").format(w, kind))
                    if not rhs_num:
                        raise ValueError(
                            tr("{0}: {1} の value は数値にしてください").format(w, kind))
                elif rhs_num != target.is_number:
                    raise ValueError(
                        tr("{0}: 変数 '{1}' と value の型が一致しません").format(w, target.name))
                ops.append(VarOp(kind=kind, name=target.name,
                                 value=const, value_var=var_name))
            return tuple(ops)

        _COND_OPS = ("==", "!=", ">=", "<=", ">", "<")

        def parse_conds(raw, where) -> tuple:
            """AND条件リストを解析する。"""
            if not isinstance(raw, list) or not raw:
                raise ValueError(
                    tr("{0}: when は1件以上の条件リストで指定してください").format(where))
            conds = []
            for i, c in enumerate(raw):
                w = tr("{0} 条件[{1}]").format(where, i)
                if not isinstance(c, dict):
                    raise ValueError(tr("{0}: 条件はオブジェクトで指定してください").format(w))
                lhs = _require_var(c.get("var"), w)
                op = c.get("op")
                if op not in _COND_OPS:
                    raise ValueError(
                        tr("{0}: op は {1} のいずれかにしてください").format(w, " ".join(_COND_OPS)))
                if "value" not in c:
                    raise ValueError(tr("{0}: value が必要です").format(w))
                const, var_name, rhs_num = _parse_rhs(c["value"], w)
                if rhs_num != lhs.is_number:
                    raise ValueError(
                        tr("{0}: 変数 '{1}' と value の型が一致しません").format(w, lhs.name))
                if not lhs.is_number and op not in ("==", "!="):
                    raise ValueError(
                        tr("{0}: 文字列変数の比較は == と != のみです").format(w))
                conds.append(VarCond(name=lhs.name, op=op,
                                     value=const, value_var=var_name))
            return tuple(conds)

        def parse_cond_next(raw, where) -> CondRule:
            """next の cond 形式を CondRule へ解析する。

            {"cond": [{"when": [...], "to": "..."}, ...],
             "else": "id" or null}   ※elseキーは必須(書き忘れ防止)
            """
            rows_raw = raw.get("cond")
            if not isinstance(rows_raw, list) or not rows_raw:
                raise ValueError(
                    tr("{0}: cond は1件以上のリストで指定してください").format(where))
            rows = []
            for i, row in enumerate(rows_raw):
                w = tr("{0} cond[{1}]").format(where, i)
                if not isinstance(row, dict) or not isinstance(row.get("to"), str):
                    raise ValueError(tr("{0}: when と to が必要です").format(w))
                rows.append((parse_conds(row.get("when"), w), row["to"]))
            if "else" not in raw:
                raise ValueError(
                    tr('{0}: cond には "else"(どの条件も成立しない時の遷移先、'
                       'null=再生終了) が必要です').format(where))
            else_to = raw["else"]
            if else_to is not None and not isinstance(else_to, str):
                raise ValueError(
                    tr("{0}: else はイベントIDの文字列か null にしてください").format(where))
            return CondRule(rows=rows, else_to=else_to)

        def parse_watch(raw) -> list[WatchRule]:
            """トップレベル watch を解析する。"""
            if raw is None:
                return []
            if not isinstance(raw, list):
                raise ValueError(tr("watch はリストで指定してください"))
            out = []
            for i, w in enumerate(raw):
                where = tr("watch[{0}]").format(i)
                if not isinstance(w, dict) or not isinstance(w.get("to"), str):
                    raise ValueError(
                        tr("{0}: to はイベントIDの文字列で指定してください").format(where))
                conds = parse_conds(w.get("when"), where)
                mode = w.get("mode", "graceful")
                if mode not in ("graceful", "interrupt"):
                    raise ValueError(
                        tr('{0}: mode は "graceful" か "interrupt" にしてください').format(where))
                once = w.get("once", True)
                if not isinstance(once, bool):
                    raise ValueError(
                        tr("{0}: once は true/false にしてください").format(where))
                out.append(WatchRule(conds=conds, to=w["to"],
                                     mode=mode, once=once))
            return out

        def parse_tracks(raw: dict, audio_abs: str, where: str) -> list[DeviceTrack]:
            """item辞書から DeviceTrack のリストを構築する。

            指定方法(優先順):
              1. "tracks": [{"type":"linear","funscript":"..."}, ...]  複数デバイス対応
              2. "funscript": "..." / null                          linear単一(後方互換)
              3. 未指定    命名ルールで自動紐づけ(auto_bind_tracks参照。
                           linear=wav名を含む同名系 / rotate=+"ufo" /
                           rotate_a10cyclonesa=+"a10" / vibration=+"vib")
            """
            tracks: list[DeviceTrack] = []

            if "tracks" in raw:
                for i, t in enumerate(raw["tracks"]):
                    if not isinstance(t, dict):
                        raise ValueError(tr('{0}: tracks[{1}] はオブジェクトで指定してください').format(where, i))
                    ttype = normalize_track_type(t.get("type", TRACK_LINEAR))
                    if ttype not in VALID_TRACK_TYPES:
                        raise ValueError(
                            tr("{0}: tracks[{1}] の type '{2}' は不正です(有効: {3})").format(where, i, ttype, ', '.join(VALID_TRACK_TYPES))
                        )
                    fs = resolve(t.get("funscript"))
                    if not fs:
                        raise ValueError(tr('{0}: tracks[{1}] に funscript がありません').format(where, i))
                    # =59: トラック個別の区間(省略時はアイテムの区間に連動)
                    t_start, t_end = parse_range(
                        t.get("range"), tr("{0} tracks[{1}]").format(where, i))
                    tracks.append(DeviceTrack(type=ttype, funscript=fs,
                                              start_s=t_start, end_s=t_end))
                return tracks

            if "funscript" in raw:
                fs = resolve(raw["funscript"])  # null なら無効化(トラックなし)
                if fs:
                    tracks.append(DeviceTrack(type=TRACK_LINEAR, funscript=fs))
                return tracks

            # 未指定 → 命名ルールで自動紐づけ(linearは同名、他種別はタグ)
            for ttype, fs in auto_bind_tracks(audio_abs):
                tracks.append(DeviceTrack(type=ttype, funscript=fs))
            return tracks

        def parse_video_item(raw: dict, where: str) -> EventItem:
            """動画アイテム(フェーズ3-B=52)を解析する。

            指定形式:
              {"video": "scene1.mp4"}                       … 自動紐づけ
              {"video": {"file": "...", "start": 60, "end": 90},
               "tracks": [...] / "funscript": ...}          … 明示指定

            トラックの指定規則は音声アイテムと同じ(tracks / funscript /
            未指定=命名ルールで自動紐づけ)。動画クロックに完全同期する。
            start/end は区間指定(=51)。区間の先頭が0秒として扱われるため、
            funscript は区間ごとに0秒起点で作れる。
            """
            if raw.get("audio"):
                raise ValueError(
                    tr('{0}: 同じアイテムに audio と video は指定できません').format(where))
            rv = raw.get("video")
            start_s, end_s = 0.0, None
            if isinstance(rv, str):
                if not rv:
                    raise ValueError(
                        tr('{0}: video のファイルを指定してください').format(where))
                vfile = resolve(rv)
            elif isinstance(rv, dict):
                f = rv.get("file")
                if not f or not isinstance(f, str):
                    raise ValueError(
                        tr('{0}: video の file を指定してください').format(where))
                vfile = resolve(f)
                # 旧形式(=51〜=58): video 辞書の中に start/end を書いていた。
                # =59でアイテム共通の "range" へ移したので、range が無いときの
                # 後方互換として読む(編集画面で保存し直すと range になる)。
                start_s, end_s = parse_range(
                    {k: rv.get(k) for k in ("start", "end")}, where, "video")
            else:
                raise ValueError(tr('{0}: video の指定が不正です').format(where))
            if raw.get("range") is not None:
                start_s, end_s = parse_range(raw.get("range"), where)
            tracks = parse_tracks(raw, vfile, where)
            # =74: 重みは定数 or {"var":..}(0/負も受理=実行時に出さない)
            w_const, w_ref = parse_numref(
                raw.get("weight", 1.0),
                tr("{0} アイテムの weight").format(where))
            return EventItem(audio="", video=vfile, tracks=tracks,
                             weight=(1.0 if w_ref is not None else w_const),
                             weight_var=(w_ref.var if w_ref is not None else None),
                             on_play=parse_ops(raw.get("on_play"),
                                               tr("{0} on_play").format(where)),
                             on_complete=parse_ops(
                                 raw.get("on_complete"),
                                 tr("{0} on_complete").format(where)),
                             start_s=start_s, end_s=end_s)

        def parse_item(raw, where: str) -> EventItem:
            if isinstance(raw, dict) and raw.get("video") is not None:
                return parse_video_item(raw, where)
            if isinstance(raw, str):
                audio = resolve(raw)
                tracks = [DeviceTrack(t, fs)
                          for t, fs in auto_bind_tracks(audio)]
                return EventItem(audio=audio, tracks=tracks)
            if isinstance(raw, dict):
                # =59: 音声・スクリプトのみアイテムにも区間指定を導入
                start_s, end_s = parse_range(raw.get("range"), where)
                audio = resolve(raw.get("audio"))
                if not audio:
                    # スクリプトのみアイテム: audio 省略は tracks/funscript の
                    # 明示指定がある場合のみ許可(作りかけの検出は維持)。
                    # 自動紐づけは音声ファイル名が基準のため使えない。
                    if "tracks" not in raw and "funscript" not in raw:
                        raise ValueError(tr('{0}: audio がありません').format(where))
                    audio = ""
                    tracks = parse_tracks(raw, "", where)
                    if not tracks:
                        raise ValueError(
                            tr('{0}: スクリプトのみのアイテムには funscript/CSV のトラックが1つ以上必要です').format(where))
                else:
                    tracks = parse_tracks(raw, audio, where)
                # =74: 重みは定数 or {"var":..}(0/負も受理=実行時に出さない)
                w_const, w_ref = parse_numref(
                    raw.get("weight", 1.0),
                    tr("{0} アイテムの weight").format(where))
                pan = parse_pan(raw.get("pan"), where)
                on_play = parse_ops(raw.get("on_play"),
                                    tr("{0} on_play").format(where))
                on_complete = parse_ops(raw.get("on_complete"),
                                        tr("{0} on_complete").format(where))
                return EventItem(audio=audio, tracks=tracks,
                                 weight=(1.0 if w_ref is not None else w_const),
                                 weight_var=(w_ref.var if w_ref is not None
                                             else None),
                                 pan=pan, on_play=on_play,
                                 on_complete=on_complete,
                                 start_s=start_s, end_s=end_s)
            raise ValueError(tr('{0}: items の要素が不正です').format(where))

        def parse_pan(raw, where: str) -> Pan | None:
            """pan指定を解析する。{"left":1.0,"right":0.2} 形式。未指定はNone。"""
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise ValueError(tr('{0}: pan はオブジェクトで指定してください').format(where))
            try:
                left = float(raw["left"])
                right = float(raw["right"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(tr('{0}: pan には left と right を数値で指定してください').format(where))
            left = max(0.0, min(1.0, left))
            right = max(0.0, min(1.0, right))
            return Pan(left=left, right=right)

        def parse_bgm(raw, where: str) -> BgmSpec | None:
            """ノードの "bgm" キーを解析する(=256)。

            省略/None = 「前のBGMを引き継ぐ」(None)。
            {"off": true} = BGM停止。
            {"items": [...], "order": ..., "pan": ...} = BGM指定。
            items は "path" か {"audio": "path", "pan": {...}}。
            指定なのにアイテム0件はエラー(Q12=ユーザー確定)。
            """
            if raw is None:
                return None
            w = tr("{0} bgm").format(where)
            if not isinstance(raw, dict):
                raise ValueError(
                    tr("{0}: オブジェクトで指定してください").format(w))
            if "off" in raw:
                if raw["off"] is not True:
                    raise ValueError(
                        tr('{0}: off は true のみ指定できます'
                           '(引き継ぐ場合はキーごと省略します)').format(w))
                return BgmSpec(mode="off")
            items_raw = raw.get("items")
            if not isinstance(items_raw, list) or not items_raw:
                raise ValueError(
                    tr('{0}: items(1件以上)か "off": true を'
                       '指定してください').format(w))
            items = []
            for i, it in enumerate(items_raw):
                wi = tr("{0} items[{1}]").format(w, i)
                pan = None
                if isinstance(it, str):
                    audio = resolve(it)
                elif isinstance(it, dict):
                    audio = resolve(it.get("audio"))
                    pan = parse_pan(it.get("pan"), wi)
                else:
                    raise ValueError(
                        tr("{0}: 要素が不正です").format(wi))
                if not audio:
                    raise ValueError(
                        tr("{0}: audio がありません").format(wi))
                items.append(BgmItem(audio=audio, pan=pan))
            order = raw.get("order", MODE_SEQUENTIAL)
            if order not in (MODE_SEQUENTIAL, MODE_RANDOM):
                raise ValueError(
                    tr("{0}: order は sequential / random の"
                       "いずれかにしてください").format(w))
            pan = parse_pan(raw.get("pan"), w)
            return BgmSpec(mode="set", items=tuple(items), order=order,
                           pan=pan)

        def parse_end(raw_end, mode, where, allow_infinite=False):
            """終了条件を解析して (type, count, ms) を返す。

            allow_infinite=True の場合、random で end 未指定を END_NONE
            (イベント終了まで無限再生)として許可する。
            """
            if raw_end is None:
                if mode in (MODE_RANDOM, MODE_RANDOM_BAG):
                    if allow_infinite:
                        return END_NONE, 1, 0, None, None
                    raise ValueError(tr('{0}: random には end (duration) の指定が必要です').format(where))
                return END_ONCE, 1, 0, None, None
            etype = raw_end.get("type")
            c_val, c_ref = parse_numref(raw_end.get("count", 1), where)
            # 時間指定は秒のみ(minutesは廃止=読まない。旧minutesは無効)
            s_val, s_ref = parse_numref(raw_end.get("seconds", 0), where)
            count = int(c_val) if c_ref is None else 1
            ms = 0
            dur_refs = None
            if s_ref is not None:
                dur_refs = (None, s_ref)
            else:
                ms = int((s_val or 0) * 1_000)
            if etype not in (END_ONCE, END_REPEAT, END_DURATION, END_NONE,
                             END_PLAYS):
                raise ValueError(tr("{0}: end type '{1}' は不正です").format(where, etype))
            if etype == END_NONE and not allow_infinite:
                raise ValueError(
                    tr("{0}: end 'none' はイベントの end が channel 指定の場合のみ使えます").format(where))
            if etype == END_REPEAT and c_ref is None and count < 1:
                raise ValueError(tr('{0}: repeat の count は1以上にしてください').format(where))
            if etype == END_PLAYS and c_ref is None and count < 1:
                raise ValueError(tr('{0}: plays の count は1以上にしてください').format(where))
            if etype == END_DURATION and dur_refs is None and ms <= 0:
                raise ValueError(tr('{0}: duration には seconds を指定してください').format(where))
            if mode == MODE_RANDOM \
                    and etype not in (END_DURATION, END_NONE, END_PLAYS):
                raise ValueError(
                    tr('{0}: random の end は duration / plays (または none) のみ対応です').format(where))
            # =98: random_bag は repeat(N周=袋をN回使い切ったら終了)も可
            if mode == MODE_RANDOM_BAG \
                    and etype not in (END_DURATION, END_NONE, END_PLAYS,
                                      END_REPEAT):
                raise ValueError(
                    tr('{0}: random_bag の end は duration / plays / repeat (または none) のみ対応です').format(where))
            return (etype, count, ms,
                    c_ref if etype in (END_REPEAT, END_PLAYS) else None,
                    dur_refs if etype == END_DURATION else None)

        def parse_event_duration_range(raw_end, where):
            """イベント終了 duration の範囲抽選(min_seconds〜max_seconds)を解析する。

            min/max はそれぞれ 0以上の定数 または {"var":名}(数値変数)。
            入場ごとに [min, max] の一様乱数(秒)を抽選し、その秒数を超えたら終了。
            戻り値は ScenarioEvent の range 用 kwargs。
            """
            lo_v, lo_ref = parse_numref(raw_end.get("min_seconds", 0), where)
            hi_raw = raw_end.get("max_seconds", raw_end.get("min_seconds", 0))
            hi_v, hi_ref = parse_numref(hi_raw, where)
            lo_ms = (lo_v or 0) * 1000 if lo_ref is None else 0.0
            hi_ms = (hi_v or 0) * 1000 if hi_ref is None else 0.0
            # 定数のときは 0以上・max>0 を検証(変数は入場時に解決・クランプ)
            if lo_ref is None and lo_ms < 0:
                raise ValueError(
                    tr('{0}: duration の min 秒は0以上にしてください').format(where))
            if lo_ref is None and hi_ref is None:
                if hi_ms < lo_ms:
                    lo_ms, hi_ms = hi_ms, lo_ms
                if hi_ms <= 0:
                    raise ValueError(
                        tr('{0}: duration の秒数(min/max)は正の数にしてください').format(where))
            elif hi_ref is None and hi_ms < 0:
                raise ValueError(
                    tr('{0}: duration の max 秒は0以上にしてください').format(where))
            return dict(end_duration_range=True,
                        end_duration_min_ms=lo_ms, end_duration_max_ms=hi_ms,
                        end_duration_min_ref=lo_ref, end_duration_max_ref=hi_ref)

        def parse_interval(raw, where):
            """interval指定を (min_ms, max_ms) で返す。

            指定形式:
              "interval": {"min": 0.5, "max": 1.0}   秒単位
              "interval": 0.5                        単一値(min=max=0.5)
            未指定は (0, 0) = 待機なし。
            """
            if raw is None:
                return 0, 0
            if isinstance(raw, (int, float)):
                ms = int(round(float(raw) * 1000 / 100)) * 100
                ms = max(0, ms)
                return ms, ms
            if isinstance(raw, dict):
                try:
                    lo = float(raw.get("min", 0))
                    hi = float(raw.get("max", lo))
                except (TypeError, ValueError):
                    raise ValueError(tr('{0}: interval の min/max は数値で指定してください').format(where))
                lo_ms = max(0, int(round(lo * 1000 / 100)) * 100)  # 0.1秒に丸め
                hi_ms = max(0, int(round(hi * 1000 / 100)) * 100)
                if hi_ms < lo_ms:
                    lo_ms, hi_ms = hi_ms, lo_ms
                return lo_ms, hi_ms
            raise ValueError(tr('{0}: interval の指定が不正です').format(where))

        def parse_channel(ch_id, raw, where, allow_infinite=False,
                          default_infinite=False) -> Channel:
            mode = raw.get("mode", MODE_SEQUENTIAL)
            if mode not in (MODE_SEQUENTIAL, MODE_RANDOM, MODE_RANDOM_BAG):
                raise ValueError(tr("{0}: mode '{1}' は不正です").format(where, mode))
            items = [parse_item(r, where) for r in raw.get("items", [])]
            if not items:
                raise ValueError(tr('{0}: items が空です').format(where))
            # チャンネルは「音声ch」「動画ch」「スクリプト専用ch」のどれか1つ
            # (混在禁止=ユーザー決定。動画chは =52 フェーズ3-Bで追加)
            kinds = {("video" if it.video else "audio" if it.audio else "script")
                     for it in items}
            if len(kinds) > 1:
                names = {"audio": tr('音声'), "video": tr('動画'),
                         "script": tr('スクリプトのみ')}
                raise ValueError(
                    tr('{0}: {1} のアイテムは同じチャンネルに混在できません(チャンネル単位でどれか1種類にしてください)').format(
                        where, tr('と').join(names[k] for k in sorted(kinds))))
            raw_end = raw.get("end")
            ch_range = None
            if raw_end is None and default_infinite:
                # ステート内チャンネルの既定: 移行/イベント終了まで無限に再生
                etype, count, ms, c_ref, d_refs = END_NONE, 1, 0, None, None
            elif (isinstance(raw_end, dict)
                    and raw_end.get("type") == END_DURATION
                    and ("min_seconds" in raw_end or "max_seconds" in raw_end)):
                # =99: duration の範囲抽選(min_seconds/max_seconds)。書式・
                # 検証はイベント終了 duration の範囲と同じ。全モードで使える
                # (durationが使えるモードなら範囲も使える)
                ch_range = parse_event_duration_range(
                    raw_end, tr("{0} end").format(where))
                etype, count, ms, c_ref, d_refs = END_DURATION, 1, 0, None, None
            else:
                etype, count, ms, c_ref, d_refs = parse_end(
                    raw_end, mode, where, allow_infinite)
            pan = parse_pan(raw.get("pan"), where) or Pan(*DEFAULT_PAN[ch_id])
            imin, imax = parse_interval(raw.get("interval"), where)
            return Channel(
                channel_id=ch_id, mode=mode, items=items,
                end_type=etype, end_count=count, end_duration_ms=ms,
                end_count_ref=c_ref, end_duration_refs=d_refs, pan=pan,
                interval_min_ms=imin, interval_max_ms=imax,
                **(ch_range or {}),
            )

        def parse_device(raw, channels, where) -> dict[str, str]:
            """device指定を {デバイス種別: チャンネルID} のマップへ解析する。

            指定形式:
              "device": "C"                          全種別をCが担当(後方互換)
              "device": {"linear":"L","rotate":"R"}  種別ごとに担当を指定
            未指定はCが担当(Cが無ければ最初のチャンネル)。
            """
            if raw is None:
                default_ch = CH_CENTER if CH_CENTER in channels else next(iter(channels))
                return {t: default_ch for t in VALID_TRACK_TYPES}
            if isinstance(raw, str):
                ch = raw if raw in channels else next(iter(channels))
                return {t: ch for t in VALID_TRACK_TYPES}
            if isinstance(raw, dict):
                dmap = {}
                for ttype, ch in raw.items():
                    ttype = normalize_track_type(ttype)
                    if ttype not in VALID_TRACK_TYPES:
                        raise ValueError(
                            tr("{0}: device の種別 '{1}' は不正です(有効: {2})").format(where, ttype, ', '.join(VALID_TRACK_TYPES)))
                    if ch not in channels:
                        raise ValueError(
                            tr("{0}: device の '{1}' に指定されたチャンネル '{2}' が存在しません").format(where, ttype, ch))
                    dmap[ttype] = ch
                return dmap
            raise ValueError(tr('{0}: device の指定が不正です').format(where))

        def check_script_channels(channels: dict, device_map: dict, where,
                                  has_video: bool = False):
            """スクリプト専用チャンネルの制約を検証する。

            **=63でコンテンツ必須の制約は撤廃**した(ユーザー要望: 音声・動画の
            素材が無い状況もありうるため)。スクリプト専用チャンネルだけの
            イベント/ステートを作れる。残る制約は「スクリプト専用chはデバイス
            担当が無いと何も起きない」ことだけで、どのデバイス種別の担当にも
            なっていない場合はエラーにする。

            =130(警告): チャンネルが担当するデバイス種別のトラックを持つ
            アイテムが1つも無い場合、そのチャンネルはデバイスを一切動かさない
            (再生時は担当外トラックが無音でスキップされる=46)。トラック種別の
            指定漏れの典型(自動紐づけの既定が rotate_ufo のため、a10担当chに
            rotate_ufo型トラックが入る事故が実例)なので警告する。担当外
            トラックの同居自体は正当(コピー由来の予備等)なのでエラーにしない。
            """
            if not channels:
                return
            script_chs = [cid for cid, ch in channels.items()
                          if not ch.is_content]
            assigned = set(device_map.values())
            for cid in script_chs:
                if cid not in assigned:
                    raise ValueError(
                        tr("{0}: スクリプト専用チャンネル '{1}' がどのデバイス種別の担当にもなっていません(device で担当を指定してください)").format(where, cid))
            # =130 警告: 担当種別のトラックが皆無のチャンネル
            for ttype, cid in device_map.items():
                ch = channels.get(cid)
                if ch is None:
                    continue
                if any(t.type == ttype for item in ch.items
                       for t in item.tracks):
                    continue
                load_warnings.append(
                    tr("{0}: チャンネル{1}は {2} の担当ですが、その種別のトラックを持つアイテムが1つもありません(このチャンネルはデバイスを動かしません)").format(
                        where, cid, ttype))

        def has_content_channels(channels: dict) -> bool:
            """音声または動画のチャンネルが1つでもあるか(=63)。

            スクリプト専用chだけのイベントは「N回再生(plays)」を満たせない
            (plays は音声/動画の本数だけを数えるため)。その検証に使う。
            """
            return any(ch.is_content for ch in (channels or {}).values())

        def parse_range(raw_range, where, label="range"):
            """区間指定(start / end)を秒で解析する(=51の動画→=59で汎用化)。

            戻り値: (start秒, end秒 or None)。省略・null は「指定なし」。
            bool は数値として受理しない(True が 1 と解釈される事故の防止)。
            0.1秒未満の細かい指定も可(小数で書ける)。
            """
            if raw_range is None:
                return 0.0, None
            if not isinstance(raw_range, dict):
                raise ValueError(
                    tr('{0}: {1} はオブジェクトで指定してください').format(where, label))

            def num(key, default):
                v = raw_range.get(key)
                if v is None:
                    return default
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise ValueError(
                        tr('{0}: {1} の {2} は秒数(数値)で指定してください').format(
                            where, label, key))
                return float(v)

            start_s = num("start", 0.0)
            end_s = num("end", None)
            if start_s < 0:
                raise ValueError(
                    tr('{0}: {1} の start は0以上にしてください').format(where, label))
            if end_s is not None and end_s <= start_s:
                raise ValueError(
                    tr('{0}: {1} の end は start より後にしてください').format(
                        where, label))
            return start_s, end_s

        def check_video_channels(channels: dict, where) -> str:
            """動画チャンネルを1つに制限し、そのIDを返す(無ければ "")。

            合意事項7「動画は同時に最大1本」をチャンネル形式でも維持する
            (ユーザー回答: 2ch以上に動画を置いたら読み込みエラー)。
            """
            vids = [cid for cid, ch in channels.items() if ch.has_video]
            if len(vids) > 1:
                raise ValueError(
                    tr('{0}: 動画を置けるチャンネルは1つだけです(動画は同時に1本。{1} に指定されています)').format(
                        where, ', '.join(sorted(vids))))
            return vids[0] if vids else ""

        def parse_transition(raw, where) -> StateTransition | None:
            """transition指定を解析する。

            {"when": {"type": "state_time",
                      "min_seconds": 60, "max_seconds": 90},   # or "seconds": 30
             "to": "S2"}                                        # or {"random": ["S1","S2"]}
            {"when": {"type": "channel_count", "channel": "R",
                      "min": 10, "max": 15},                    # or "count": 10
             "to": ...}
            """
            if raw is None:
                return None
            when = raw.get("when")
            to = raw.get("to")
            wtype = when.get("type") if isinstance(when, dict) else None
            if wtype == WHEN_CHOICE:
                # =275: 選択肢でステート移行。to は持たず、choice/timeout/
                # default/skip/show を transition 直下に置く(next choice と
                # 同じ文法。行き先はステートID or {"event": ID})。
                if to is not None:
                    raise ValueError(
                        tr('{0}: 選択肢(choice)のステート移行では to は指定できません(行き先は各選択肢に書きます)').format(where))
                rule = parse_choice(raw, tr("{0} transition").format(where),
                                    state_mode=True)
                return StateTransition(
                    when_type=WHEN_CHOICE, channel="",
                    min_value=0, max_value=0, candidates=[],
                    choice=rule)
            if not isinstance(when, dict) or to is None:
                raise ValueError(tr('{0}: transition には when と to が必要です').format(where))

            def _parse_candidates():
                """to を (candidates, else_to, visitedオプション, cond_rows) へ解析する(=73/=125)。

                candidates の要素は (state_id, weight定数|None, weight変数名|None)。
                文字列候補は重み1の定数。weight は 0/負も許容(実行時に
                0以下=出さない。=26のイベント版と同じ規則)。
                =77: 第3戻り値は StateTransition へ渡す visited系kwargs
                (visited_exclude / exhausted_mode / exhausted_to)。文法は
                =26の next と同じ "visited" / "when_exhausted" キー(toのdict内)。
                =125: 第4戻り値は判定式(cond)形式の行 [(conds, to)]。
                {"cond": [{"when": [...], "to": "S1"}, ...], "else": "Sx"}。
                書式はイベント next の cond と同じ。**else は省略可**で、
                省略時(または null)は「どの行も成立しなければ移行しない」
                (イベント版の else 必須とは意図的に異なる=ユーザー決定)。
                random と cond は同時指定不可。
                """
                if isinstance(to, str):
                    return [(to, 1.0, None)], None, {}, []
                if isinstance(to, dict) and isinstance(to.get("cond"), list):
                    if to.get("random") is not None:
                        raise ValueError(
                            tr("{0}: to の random と cond は同時に指定できません").format(where))
                    rows_raw = to["cond"]
                    if not rows_raw:
                        raise ValueError(
                            tr("{0}: cond は1件以上のリストで指定してください").format(where))
                    rows = []
                    for i, row in enumerate(rows_raw):
                        w = tr("{0} cond[{1}]").format(where, i)
                        if not isinstance(row, dict) \
                                or not isinstance(row.get("to"), str):
                            raise ValueError(
                                tr("{0}: when と to が必要です").format(w))
                        rows.append((parse_conds(row.get("when"), w),
                                     row["to"]))
                    else_to = to.get("else")
                    if else_to is not None and not isinstance(else_to, str):
                        raise ValueError(
                            tr('{0}: transition の else はステートIDの文字列で指定してください').format(where))
                    return [], else_to, {}, rows
                if isinstance(to, dict) and isinstance(to.get("random"), list) \
                        and to["random"]:
                    cands = []
                    for i, ent in enumerate(to["random"]):
                        if isinstance(ent, str):
                            cands.append((ent, 1.0, None))
                        elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                            w_const, w_ref = parse_numref(
                                ent.get("weight", 1.0),
                                tr("{0}: transition random[{1}] の weight").format(where, i))
                            cands.append((ent["to"], w_const,
                                          w_ref.var if w_ref is not None else None))
                        else:
                            raise ValueError(
                                tr("{0}: transition random[{1}] の要素が不正です").format(where, i))
                    else_to = to.get("else")
                    if else_to is not None and not isinstance(else_to, str):
                        raise ValueError(
                            tr('{0}: transition の else はステートIDの文字列で指定してください').format(where))
                    visited = to.get("visited", "include")
                    if visited not in ("include", "exclude"):
                        raise ValueError(
                            tr("{0}: transition の visited は include か exclude にしてください").format(where))
                    ex = to.get("when_exhausted", "all")
                    mode, ex_to = "all", None
                    if ex in ("all", "reset"):
                        mode = ex
                    elif isinstance(ex, dict) and isinstance(ex.get("to"), str):
                        mode, ex_to = "to", ex["to"]
                    else:
                        raise ValueError(
                            tr('{0}: transition の when_exhausted は "all" / "reset" / {{"to": "..."}} にしてください').format(where))
                    vk = {"visited_exclude": visited == "exclude",
                          "exhausted_mode": mode, "exhausted_to": ex_to}
                    return cands, else_to, vk, []
                raise ValueError(tr('{0}: to は文字列か {{"random": [...]}} か {{"cond": [...]}} で指定してください').format(where))

            # 判定式(cond)形式: チャンネル/閾値は不要、変数の判定式で移行
            if wtype == WHEN_COND:
                conds = parse_conds(when.get("conds"),
                                    tr("{0} transition").format(where))
                cands, else_to, vk, crows = _parse_candidates()
                return StateTransition(
                    when_type=WHEN_COND, channel="",
                    min_value=0, max_value=0, candidates=cands,
                    else_to=else_to, conds=conds, cond_rows=crows, **vk)

            # 全チャンネル終了形式: チャンネル/閾値不要
            if wtype == WHEN_ALL_CHANNELS:
                cands, else_to, vk, crows = _parse_candidates()
                return StateTransition(
                    when_type=WHEN_ALL_CHANNELS, channel="",
                    min_value=0, max_value=0, candidates=cands,
                    else_to=else_to, cond_rows=crows, **vk)

            # 指定チャンネル終了形式: channel のみ必要(閾値不要)
            if wtype == WHEN_CHANNEL_END:
                channel = when.get("channel")
                if channel not in VALID_CHANNELS:
                    raise ValueError(tr("{0}: when の channel '{1}' は不正です").format(where, channel))
                cands, else_to, vk, crows = _parse_candidates()
                return StateTransition(
                    when_type=WHEN_CHANNEL_END, channel=channel,
                    min_value=0, max_value=0, candidates=cands,
                    else_to=else_to, cond_rows=crows, **vk)

            channel = when.get("channel")
            # =165: channel_time は廃止(state_time と役割が重複するため)。
            # 気づかず動き続けるより明確に落とすほうが安全なので専用の
            # メッセージでエラーにする(ユーザー決定 2026-08-16=B案)。
            if wtype == WHEN_CHANNEL_TIME_REMOVED:
                raise ValueError(
                    tr("{0}: when type 'channel_time' は廃止しました。"
                       "'state_time'(経過時間)を使ってください").format(where))
            if wtype not in (WHEN_CHANNEL_COUNT, WHEN_STATE_TIME):
                raise ValueError(tr("{0}: when type '{1}' は不正です(有効: {2}, {3}, {4}, {5}, {6}, {7})").format(where, wtype, WHEN_CHANNEL_COUNT, WHEN_STATE_TIME, WHEN_COND, WHEN_ALL_CHANNELS, WHEN_CHANNEL_END, WHEN_CHOICE))
            if wtype == WHEN_STATE_TIME:
                # =62: ステート滞在時間はチャンネルを見ない(音声なしでも使える)
                channel = ""
            elif channel not in VALID_CHANNELS:
                raise ValueError(tr("{0}: when の channel '{1}' は不正です").format(where, channel))

            lo_ref = hi_ref = None
            if wtype == WHEN_STATE_TIME:
                if "seconds" in when:
                    v, r = parse_numref(when["seconds"], where)
                    if r is not None:
                        lo_ref = hi_ref = r
                        lo = hi = 0.0
                    else:
                        lo = hi = v * 1000
                else:
                    lo_v, lo_ref = parse_numref(when.get("min_seconds", 0), where)
                    hi_v, hi_ref = parse_numref(
                        when.get("max_seconds",
                                 when.get("min_seconds", 0)), where)
                    lo = (lo_v or 0) * 1000 if lo_ref is None else 0.0
                    hi = (hi_v or 0) * 1000 if hi_ref is None else 0.0
                if lo_ref is None and hi_ref is None:
                    if hi < lo:
                        lo, hi = hi, lo
                    if hi <= 0:
                        raise ValueError(tr('{0}: {1} には秒数を指定してください').format(where, wtype))
            else:
                if "count" in when:
                    v, r = parse_numref(when["count"], where)
                    if r is not None:
                        lo_ref = hi_ref = r
                        lo = hi = 1
                    else:
                        lo = hi = int(v)
                else:
                    lo_v, lo_ref = parse_numref(when.get("min", 0), where)
                    hi_v, hi_ref = parse_numref(
                        when.get("max", when.get("min", 0)), where)
                    lo = int(lo_v) if lo_ref is None else 1
                    hi = int(hi_v) if hi_ref is None else 1
                if lo_ref is None and hi_ref is None:
                    if hi < lo:
                        lo, hi = hi, lo
                    if hi < 1:
                        raise ValueError(tr('{0}: channel_count には1以上の回数を指定してください').format(where))

            cands, else_to, vk, crows = _parse_candidates()

            return StateTransition(
                when_type=wtype, channel=channel,
                min_value=lo, max_value=hi, candidates=cands,
                else_to=else_to, cond_rows=crows,
                min_ref=lo_ref, max_ref=hi_ref, **vk,
            )

        def parse_seek_channel(raw_seek, channels: dict, where: str):
            """seek_channel(シークバー追従チャンネル指定)の読取と検証。

            None=無指定(自動)。指定時は定義済みチャンネルのみ許可する。
            """
            if raw_seek is None:
                return None
            if not isinstance(raw_seek, str) or raw_seek not in channels:
                raise ValueError(
                    tr("{0}: seek_channel は定義済みのチャンネル(L/C/R)を"
                       "指定してください").format(where))
            # =63: スクリプト専用チャンネルも追従対象に選べる(ユーザー決定)。
            # 音声が無い場合は経過時間=スクリプトの進行として表示・シークする。
            return raw_seek

        def parse_state(state_id, raw, where) -> EventState:
            # 旧形式(直下 video)は動画チャンネルへ移行してから解析する(=52)
            raw = migrate_video_node(raw, where, is_state=True)
            # =124 ノードの着色(表示専用)
            check_node_color(raw, where)
            channels: dict[str, Channel] = {}
            for ch_id, ch_raw in (raw.get("channels") or {}).items():
                if ch_id not in VALID_CHANNELS:
                    raise ValueError(tr("{0}: チャンネル '{1}' は不正です").format(where, ch_id))
                channels[ch_id] = parse_channel(
                    ch_id, ch_raw, tr('{0} チャンネル{1}').format(where, ch_id),
                    allow_infinite=True, default_infinite=True)
            vcid = check_video_channels(channels, where)
            # channels 省略/空dict = 音声なしステート(即時通過ノード)。
            # ただし「チャンネルがあるのに items 空」等は parse_channel が
            # 従来どおりエラーにする(作りかけの検出は維持)。
            if not channels:
                if raw.get("device"):
                    raise ValueError(
                        tr('{0}: 音声なし(チャンネルなし)に device は指定できません').format(where))
                transition = parse_transition(raw.get("transition"), where)
                # =62: 経過時間(state_time)= 無音待機ノード。チャンネルを見ない
                # 移行条件だけが使える(channel_* は対象chが無いので不可)。
                # =275: 選択肢(choice)も可(選択されるまで無音で待機する)
                if transition is not None and transition.when_type not in (
                        WHEN_COND, WHEN_STATE_TIME, WHEN_CHOICE):
                    raise ValueError(
                        tr('{0}: 音声なしステートの移行条件は「なし」「経過時間」「判定式(cond)」「選択肢」のみ使用できます').format(where))
                on_start = parse_ops(raw.get("on_start"),
                                     tr("{0} on_start").format(where))
                on_end = parse_ops(raw.get("on_end"),
                                   tr("{0} on_end").format(where))
                seek_ch = parse_seek_channel(raw.get("seek_channel"),
                                             channels, where)
                return EventState(
                    state_id=state_id, channels={}, device_map={},
                    transition=transition,
                    on_start=on_start, on_end=on_end,
                    seek_channel=seek_ch,
                    bgm=parse_bgm(raw.get("bgm"), where),
                )
            # 動画ステートの device 既定は「担当なし」(チャンネルはデバイスを
            # 駆動しない=全種別が動画側)。明示指定した種別のみチャンネル駆動。
            if vcid and raw.get("device") is None:
                device_map = {}
            else:
                device_map = parse_device(raw.get("device"), channels, where)
            check_script_channels(channels, device_map, where)
            if vcid and raw.get("seek_channel") is not None:
                raise ValueError(
                    tr('{0}: 動画があるときシークバーは動画に固定されるため seek_channel は指定できません').format(where))
            transition = parse_transition(raw.get("transition"), where)
            on_start = parse_ops(raw.get("on_start"),
                                 tr("{0} on_start").format(where))
            on_end = parse_ops(raw.get("on_end"),
                               tr("{0} on_end").format(where))
            seek_ch = parse_seek_channel(raw.get("seek_channel"),
                                         channels, where)
            return EventState(
                state_id=state_id, channels=channels,
                device_map=device_map, transition=transition,
                on_start=on_start, on_end=on_end,
                seek_channel=seek_ch, video_channel=vcid,
                bgm=parse_bgm(raw.get("bgm"), where),
            )

        def parse_event_end(raw_end, where):
            """イベントレベル(マルチステート)の終了条件を解析する。

            戻り値: (etype, count, ms, count_ref, dur_refs, end_conds, end_states)。
            end_conds は type=="cond"、end_states は type=="states" のときのみ非空。
            """
            if raw_end is None:
                raise ValueError(
                    tr('{0}: ステートを持つイベントには end (duration/plays/transitions/cond/states/none) が必要です').format(where))
            etype = raw_end.get("type")
            if etype == END_COND:
                conds = parse_conds(raw_end.get("when"),
                                    tr("{0} end").format(where))
                return (END_COND, 1, 0, None, None, conds, ())
            if etype == END_STATES:
                states_raw = raw_end.get("states")
                if not isinstance(states_raw, list) or not states_raw:
                    raise ValueError(
                        tr('{0}: end states には終了ステートIDを1つ以上指定してください').format(where))
                end_states = tuple(str(s) for s in states_raw)
                return (END_STATES, 1, 0, None, None, (), end_states)
            if etype == END_NONE:
                # =168: 無限。イベント自身は終わらない(出口は「なし」=終わらない/
                # 選択肢/数値入力/判定式の常時監視のいずれか)。
                return (END_NONE, 1, 0, None, None, (), ())
            c_val, c_ref = parse_numref(raw_end.get("count", 1), where)
            # 時間指定は秒のみ(minutesは廃止=読まない。旧minutesは無効)
            s_val, s_ref = parse_numref(raw_end.get("seconds", 0), where)
            count = int(c_val) if c_ref is None else 1
            ms = 0
            dur_refs = None
            if s_ref is not None:
                dur_refs = (None, s_ref)
            else:
                ms = int((s_val or 0) * 1_000)
            if etype not in (END_DURATION, END_PLAYS, END_TRANSITIONS):
                raise ValueError(
                    tr('{0}: ステートを持つイベントの end は duration/plays/transitions/cond/states/none のみです').format(where))
            if etype == END_DURATION and dur_refs is None and ms <= 0:
                raise ValueError(tr('{0}: duration には seconds を指定してください').format(where))
            if etype in (END_PLAYS, END_TRANSITIONS) and c_ref is None and count < 1:
                raise ValueError(tr('{0}: {1} の count は1以上にしてください').format(where, etype))
            return (etype, count, ms,
                    c_ref if etype in (END_PLAYS, END_TRANSITIONS) else None,
                    dur_refs if etype == END_DURATION else None,
                    (), ())

        def parse_next(raw, where):
            """next指定を (next_str, NextRule, ChoiceRule, CondRule, InputRule) へ解析する。

            対応形式:
              null / 省略        → 遷移なし(シナリオ終了)
              "eventB"           → 単純遷移(後方互換)
              {"random": [...]}  → 重み付きランダム分岐
                 候補は "B" または {"to": "B", "weight": 2}
                 "visited": "include"(既定) / "exclude"(実行済みを除外)
                 "when_exhausted": "all"(既定) / "reset" / {"to": "id"}
              {"choice": ...}    → 選択肢
              {"cond": ..., "else": ...} → 変数分岐
              {"input": ...}     → 数値入力
            """
            if raw is None:
                return None, None, None, None, None
            if isinstance(raw, str):
                return raw, None, None, None, None
            if not isinstance(raw, dict):
                raise ValueError(tr("{0}: next の指定が不正です").format(where))
            forms = [k for k in ("random", "choice", "cond", "input") if k in raw]
            if len(forms) > 1:
                raise ValueError(
                    tr("{0}: next の random / choice / cond / input は同時に指定できません").format(where))
            if "cond" in raw:
                return None, None, None, parse_cond_next(raw, where), None
            if "input" in raw:
                return None, None, None, None, parse_input(raw, where)
            if "choice" in raw:
                return None, None, parse_choice(raw, where), None, None
            if not isinstance(raw.get("random"), list) or not raw["random"]:
                raise ValueError(
                    tr("{0}: next のオブジェクト形式には random (1件以上のリスト) が必要です").format(where))
            candidates = []
            for i, ent in enumerate(raw["random"]):
                if isinstance(ent, str):
                    candidates.append((ent, 1.0, None))
                elif isinstance(ent, dict) and isinstance(ent.get("to"), str):
                    # weight は定数 or 変数参照。0/負も許容(実行時に0以下=出さない)。
                    w_const, w_ref = parse_numref(
                        ent.get("weight", 1.0),
                        tr("{0}: next random[{1}] の weight").format(where, i))
                    candidates.append((ent["to"], w_const,
                                       w_ref.var if w_ref is not None else None))
                else:
                    raise ValueError(
                        tr("{0}: next random[{1}] の要素が不正です").format(where, i))
            else_to = raw.get("else")
            if else_to is not None and not isinstance(else_to, str):
                raise ValueError(
                    tr('{0}: next の else はイベントIDの文字列で指定してください').format(where))
            visited = raw.get("visited", "include")
            if visited not in ("include", "exclude"):
                raise ValueError(
                    tr("{0}: next の visited は include か exclude にしてください").format(where))
            ex = raw.get("when_exhausted", "all")
            mode, ex_to = "all", None
            if ex in ("all", "reset"):
                mode = ex
            elif isinstance(ex, dict) and isinstance(ex.get("to"), str):
                mode, ex_to = "to", ex["to"]
            else:
                raise ValueError(
                    tr('{0}: next の when_exhausted は "all" / "reset" / {{"to": "..."}} にしてください').format(where))
            rule = NextRule(candidates=candidates,
                            visited_exclude=(visited == "exclude"),
                            exhausted_mode=mode, exhausted_to=ex_to,
                            else_to=else_to)
            return None, rule, None, None, None

        def parse_show(raw, where):
            """choice/input共通の show(表示タイミング)指定を解析する。"""
            if raw in ("start", "end"):
                return raw, 0
            if isinstance(raw, dict):
                # 時間指定は秒のみ(minutesは廃止=読まない)
                show_ms = int(float(raw.get("seconds", 0)) * 1_000)
                if show_ms < 0:
                    raise ValueError(
                        tr("{0}: show の時間指定が不正です").format(where))
                return "ms", show_ms
            raise ValueError(
                tr('{0}: show は "start" / "end" / {{"seconds"}} にしてください').format(where))

        def parse_input(raw, where):
            """next の input 形式を InputRule へ解析する。

            {"input": {"var": "min_len", "label": "再生時間(分)",
                       "to": "main_play", "min": 1, "max": 30},
             "show": "start" | "end"(既定) | {"seconds"}}

            min/max は受理する入力範囲(省略時は変数宣言のmin/max)。
            宣言のmin/maxの外へ広げる指定は読み込みエラー。
            """
            iraw = raw.get("input")
            if not isinstance(iraw, dict):
                raise ValueError(
                    tr("{0}: input はオブジェクトで指定してください").format(where))
            decl = _require_var(iraw.get("var"),
                                tr("{0} input").format(where))
            if not decl.is_number:
                raise ValueError(
                    tr("{0}: 変数 '{1}' は数値変数ではありません").format(
                        tr("{0} input").format(where), decl.name))
            to = iraw.get("to")
            if not isinstance(to, str) or not to:
                raise ValueError(
                    tr("{0}: input の to はイベントIDの文字列で指定してください").format(where))
            label = str(iraw.get("label", "") or "").strip()

            def _bound(key):
                if key not in iraw:
                    return None
                v = iraw[key]
                if not _is_num(v):
                    raise ValueError(
                        tr("{0}: input の min/max は数値で指定してください").format(where))
                return float(v)

            mn, mx = _bound("min"), _bound("max")
            if mn is not None and mx is not None and mn > mx:
                raise ValueError(
                    tr("{0}: input の min は max 以下にしてください").format(where))
            # 宣言のmin/maxより広い入力範囲は矛盾(セット時にクランプされてしまう)
            if ((mn is not None and decl.vmin is not None and mn < decl.vmin)
                    or (mx is not None and decl.vmax is not None and mx > decl.vmax)):
                raise ValueError(
                    tr("{0}: input の min/max が変数 '{1}' の宣言範囲の外です").format(where, decl.name))
            vmin = mn if mn is not None else decl.vmin
            vmax = mx if mx is not None else decl.vmax
            show_mode, show_ms = parse_show(raw.get("show", "end"), where)
            skip_raw = raw.get("skip", "default")
            if skip_raw not in ("default", "stay"):
                raise ValueError(
                    tr("{0}: skip は \"default\" か \"stay\" で指定してください").format(where))
            return InputRule(var=decl.name, to=to, label=label,
                             vmin=vmin, vmax=vmax,
                             show_mode=show_mode, show_ms=show_ms,
                             skip_stay=(skip_raw == "stay"))

        def parse_choice_to(v, where, state_mode: bool):
            """=275: choice の行き先を (to, to_event) へ解析する。

            通常(イベントの next): 文字列=イベントID のみ。
            ステート移行(state_mode): 文字列=ステートID、
            {"event": "イベントID"}=イベントを終了して飛ぶ。
            """
            if isinstance(v, str) and v:
                return v, False
            if state_mode and isinstance(v, dict) \
                    and isinstance(v.get("event"), str) and v["event"]:
                return v["event"], True
            if state_mode:
                raise ValueError(
                    tr('{0}: 行き先はステートIDの文字列か {{"event": "イベントID"}} で指定してください').format(where))
            raise ValueError(
                tr("{0}: 行き先はイベントIDの文字列で指定してください").format(where))

        def parse_choice(raw, where, state_mode: bool = False):
            """next の choice 形式を ChoiceRule へ解析する。

            {"choice": [{"label": "...", "to": "..."}, ...],   1〜9件
             "timeout": {"seconds": 30},  省略=無制限
             "default": "random" | {"to": "..."},  タイムアウト/▶▶スキップ時の行き先。
                        省略=先頭候補。旧形式のtimeout.toも受理(defaultと併記はエラー)
             "skip": "default"(既定) | "stay",  =274: "stay"=選択必須(▶▶で飛ばさない)
             "show": "start" | "end"(既定) | {"seconds": 90}}

            =275: state_mode=True(ステート移行の選択肢)では、各 to と
            default.to に「ステートID」または {"event": "イベントID"} を
            受理する。show の意味はステート基準(start=ステート開始時 /
            end=ステート内の全チャンネル終了時 / seconds=ステート開始から)。
            """
            entries_raw = raw.get("choice")
            if not isinstance(entries_raw, list) or not (1 <= len(entries_raw) <= 9):
                raise ValueError(
                    tr("{0}: choice は1〜9件のリストで指定してください").format(where))
            entries = []
            for i, ent in enumerate(entries_raw):
                if not isinstance(ent, dict) or ent.get("to") is None:
                    raise ValueError(
                        tr("{0}: choice[{1}] には label と to が必要です").format(where, i))
                to, to_event = parse_choice_to(
                    ent["to"], tr("{0} choice[{1}]").format(where, i), state_mode)
                label = str(ent.get("label", "") or "").strip()
                if not label:
                    raise ValueError(
                        tr("{0}: choice[{1}] の label が空です").format(where, i))
                ops = parse_ops(ent.get("ops"),
                                tr("{0} choice[{1}]").format(where, i))
                entries.append(ChoiceEntry(label=label, to=to, ops=ops,
                                           to_event=to_event))

            on_timeout = parse_ops(raw.get("on_timeout"),
                                   tr("{0} on_timeout").format(where))

            timeout_ms, legacy_to = None, None
            traw = raw.get("timeout")
            if traw is not None:
                if not isinstance(traw, dict):
                    raise ValueError(
                        tr("{0}: timeout は seconds のオブジェクトで指定してください").format(where))
                # 時間指定は秒のみ(minutesは廃止=読まない)
                ms = int(float(traw.get("seconds", 0)) * 1_000)
                if ms <= 0:
                    raise ValueError(
                        tr("{0}: timeout には seconds を指定してください").format(where))
                timeout_ms = ms
                if traw.get("to") is not None:   # 旧形式(timeout.to)の互換受理
                    if not isinstance(traw["to"], str):
                        raise ValueError(
                            tr("{0}: timeout の to はイベントIDの文字列にしてください").format(where))
                    legacy_to = traw["to"]

            # デフォルト遷移先(タイムアウト時・▶▶スキップ時共通)
            default_mode, default_to = "first", None
            default_to_event = False
            draw = raw.get("default")
            if draw is not None:
                if legacy_to is not None:
                    raise ValueError(
                        tr('{0}: "default" と timeout の to は同時に指定できません').format(where))
                if draw == "random":
                    default_mode = "random"
                elif isinstance(draw, dict) and draw.get("to") is not None:
                    default_to, default_to_event = parse_choice_to(
                        draw["to"], tr("{0} default").format(where), state_mode)
                    default_mode = "to"
                else:
                    raise ValueError(
                        tr('{0}: default は "random" か {{"to": "..."}} で指定してください').format(where))
            elif legacy_to is not None:
                default_mode, default_to = "to", legacy_to

            show_mode, show_ms = parse_show(raw.get("show", "end"), where)

            # =274: 選択必須(▶▶で飛ばさない)。"default"/"stay" 以外はエラー
            skip_raw = raw.get("skip", "default")
            if skip_raw not in ("default", "stay"):
                raise ValueError(
                    tr('{0}: skip は "default" か "stay" で指定してください').format(where))

            return ChoiceRule(entries=entries, timeout_ms=timeout_ms,
                              on_timeout=on_timeout,
                              default_mode=default_mode, default_to=default_to,
                              skip_stay=(skip_raw == "stay"),
                              show_mode=show_mode, show_ms=show_ms,
                              default_to_event=default_to_event)

        events: dict[str, ScenarioEvent] = {}

        if "events" in data:
            for event_id, raw in data["events"].items():
                where = tr("イベント '{0}'").format(event_id)

                next_str, next_rule, next_choice, next_cond, next_input = \
                    parse_next(raw.get("next"), where)
                # =123 すごろく(advance)
                adv_raw = raw.get("advance")
                advance = advance_ref = None
                if adv_raw is not None:
                    if isinstance(adv_raw, dict):
                        _c, advance_ref = parse_numref(
                            adv_raw, tr("{0} advance").format(where))
                    elif (_is_num(adv_raw) and float(adv_raw).is_integer()
                          and adv_raw >= 1):
                        advance = int(adv_raw)
                    else:
                        raise ValueError(
                            tr('{0}: advance(すごろく)は1以上の整数か {{"var": 変数名}} で指定してください').format(where))
                    if (next_str is None and next_rule is None
                            and next_cond is None and next_choice is None
                            and next_input is None):
                        raise ValueError(
                            tr("{0}: advance(すごろく)が指定されていますが、次のイベントがありません").format(where))
                    if next_choice is not None or next_input is not None:
                        raise ValueError(
                            tr("{0}: advance(すごろく)は選択肢/数値入力の遷移には使えません").format(where))
                # =124 ノードの着色(表示専用)
                check_node_color(raw, where)
                on_start_ops = parse_ops(raw.get("on_start"),
                                         tr("{0} on_start").format(where))
                on_end_ops = parse_ops(raw.get("on_end"),
                                       tr("{0} on_end").format(where))

                if "states" in raw:
                    # --- マルチステート形式 ---
                    if raw.get("video") is not None:
                        raise ValueError(
                            tr('{0}: ステート形式では video は各ステートに指定してください').format(where))
                    if raw.get("bgm") is not None:
                        # =256: BGMもチャンネルと同じく各ステートの持ち物
                        raise ValueError(
                            tr('{0}: ステート形式では bgm は各ステートに指定してください').format(where))
                    states: dict[str, EventState] = {}
                    for state_id, st_raw in raw["states"].items():
                        states[state_id] = parse_state(
                            state_id, st_raw, tr("{0} ステート'{1}'").format(where, state_id))
                    if not states:
                        raise ValueError(tr('{0}: states が空です').format(where))

                    start_state = raw.get("start")
                    if not start_state or start_state not in states:
                        raise ValueError(tr('{0}: start ステートが見つかりません').format(where))

                    # =99: イベント終了 duration の範囲抽選(min_seconds/
                    # max_seconds)をステート形式でも許可(通常イベントと同じ)
                    st_end_raw = raw.get("end")
                    st_dur_range = None
                    if (isinstance(st_end_raw, dict)
                            and st_end_raw.get("type") == END_DURATION
                            and ("min_seconds" in st_end_raw
                                 or "max_seconds" in st_end_raw)):
                        st_dur_range = parse_event_duration_range(
                            st_end_raw, tr("{0} end").format(where))
                        (etype, count, ms, ev_c_ref, ev_d_refs,
                         ev_end_conds, ev_end_states) = (
                            END_DURATION, 1, 0, None, None, (), ())
                    else:
                        (etype, count, ms, ev_c_ref, ev_d_refs,
                         ev_end_conds, ev_end_states) = parse_event_end(
                            st_end_raw, where)

                    if etype == END_PLAYS and not any(
                            has_content_channels(st.channels)
                            for st in states.values()):
                        # =63: plays は音声/動画の本数だけを数えるので、
                        # どのステートにもコンテンツが無いと永久に成立しない
                        raise ValueError(
                            tr('{0}: スクリプト専用チャンネルだけのイベントに「N回の再生で次へ」は使えません(再生回数は音声・動画のみ数えます)').format(where))

                    # end states の存在チェック
                    for s in ev_end_states:
                        if s not in states:
                            raise ValueError(
                                tr("{0}: end states の '{1}' が存在しません").format(where, s))

                    # 移行先の参照整合性
                    for st in states.values():
                        if st.transition:
                            for t in st.transition.targets:
                                if t not in states:
                                    raise ValueError(
                                        tr("{0} ステート'{1}': 移行先 '{2}' が存在しません").format(where, st.state_id, t))
                            et = st.transition.else_to
                            if et is not None and et not in states:
                                raise ValueError(
                                    tr("{0} ステート'{1}': 移行先(else) '{2}' が存在しません").format(where, st.state_id, et))
                            # =77: 全候補実行済み時の固定移行先も存在チェック
                            xt = st.transition.exhausted_to
                            if xt is not None and xt not in states:
                                raise ValueError(
                                    tr("{0} ステート'{1}': 移行先(実行済み時) '{2}' が存在しません").format(where, st.state_id, xt))
                            # cond / all_channels / state_time(=62) 形式は
                            # チャンネルを見ないので検証を省く
                            if (st.transition.when_type
                                    not in (WHEN_COND, WHEN_ALL_CHANNELS,
                                            WHEN_STATE_TIME, WHEN_CHOICE)
                                    and st.transition.channel not in st.channels):
                                raise ValueError(
                                    tr("{0} ステート'{1}': transition の channel '{2}' がこのステートにありません").format(where, st.state_id, st.transition.channel))

                    events[event_id] = ScenarioEvent(
                        event_id=event_id,
                        states=states,
                        start_state=start_state,
                        end_type=etype, end_count=count, end_duration_ms=ms,
                        end_count_ref=ev_c_ref, end_duration_refs=ev_d_refs,
                        end_conds=ev_end_conds, end_states=ev_end_states,
                        **(st_dur_range or {}),
                        next=next_str, next_rule=next_rule, next_choice=next_choice,
                        next_cond=next_cond, next_input=next_input,
                        advance=advance, advance_ref=advance_ref,
                        on_start=on_start_ops,
                        on_end=on_end_ops,
                    )

                elif "channels" in raw or "video" in raw or "items" not in raw:
                    # --- 単一ステート(チャンネル形式) → "main"ステートに正規化 ---
                    if raw.get("video") is not None and "items" in raw:
                        raise ValueError(
                            tr('{0}: video と items 直書き形式は併用できません(channels を使ってください)').format(where))
                    # channels 省略(itemsも無し)/空dict かつ動画なし =
                    # 音声なしイベント(分岐・変数操作だけの即時通過ノード)。
                    if not raw.get("channels") and raw.get("video") is None:
                        # =62(フェーズ2): 終了条件に「経過時間(duration)」を
                        # 指定すると**無音待機ノード**になる(指定秒だけ何も
                        # 再生せずに待ってから次へ)。それ以外の終了条件は、
                        # 成立させる主体(チャンネル)が居ないため引き続きエラー。
                        # ※ cond は待機中に変数を書き換える実行主体が存在せず
                        #   永久待機になるので許可しない(=62でユーザー決定)。
                        raw_end = raw.get("end")
                        etype, count, ms = END_ONCE, 1, 0
                        ev_d_refs = None
                        silent_range = None
                        if raw_end is not None:
                            if (not isinstance(raw_end, dict)
                                    or raw_end.get("type") != END_DURATION):
                                raise ValueError(
                                    tr('{0}: 音声なし(チャンネルなし)のイベントの終了条件は経過時間(duration)のみ指定できます').format(where))
                            if ("min_seconds" in raw_end
                                    or "max_seconds" in raw_end):
                                silent_range = parse_event_duration_range(
                                    raw_end, tr("{0} end").format(where))
                                etype = END_DURATION
                            else:
                                (etype, count, ms, _c_ref,
                                 ev_d_refs) = parse_end(
                                    raw_end, MODE_SEQUENTIAL, where)
                        if raw.get("device"):
                            raise ValueError(
                                tr('{0}: 音声なし(チャンネルなし)に device は指定できません').format(where))
                        state = EventState(
                            state_id="main", channels={}, device_map={},
                            seek_channel=parse_seek_channel(
                                raw.get("seek_channel"), {}, where),
                            # =290: 音声なしイベントも bgm(指定/引き継ぐ/オフ)を
                            # 持てる。従来は読み落としていて、選択肢だけの
                            # イベントで「BGMオフ」が効かなかった(ユーザー報告)
                            bgm=parse_bgm(raw.get("bgm"), where))
                        events[event_id] = ScenarioEvent(
                            event_id=event_id,
                            states={"main": state}, start_state="main",
                            end_type=etype, end_count=count, end_duration_ms=ms,
                            end_duration_refs=ev_d_refs,
                            next=next_str, next_rule=next_rule,
                            next_choice=next_choice,
                            next_cond=next_cond, next_input=next_input,
                            advance=advance, advance_ref=advance_ref,
                            on_start=on_start_ops,
                            on_end=on_end_ops,
                            **(silent_range or {}),
                        )
                        continue
                    # 旧形式(直下 video)は動画チャンネルへ移行してから解析
                    # する(=52)。end 未指定の非ループ動画には
                    # end=channel(動画ch) が補われる(旧挙動の再現)。
                    raw = migrate_video_node(raw, where)
                    has_video_ch = bool(raw_video_channel(raw))
                    event_end_raw = raw.get("end")
                    end_channel = None
                    end_conds = ()
                    is_duration = (isinstance(event_end_raw, dict)
                                   and event_end_raw.get("type") == END_DURATION)
                    # =168: 無限。イベント自身の終了条件を持たない
                    is_infinite = (isinstance(event_end_raw, dict)
                                   and event_end_raw.get("type") == END_NONE)
                    if (isinstance(event_end_raw, dict)
                            and event_end_raw.get("type") == END_CHANNEL):
                        end_channel = event_end_raw.get("channel")
                        if not end_channel:
                            raise ValueError(tr('{0}: end channel には channel を指定してください').format(where))
                    elif (isinstance(event_end_raw, dict)
                            and event_end_raw.get("type") == END_COND):
                        end_conds = parse_conds(event_end_raw.get("when"),
                                                tr("{0} end").format(where))
                    # 判定式・合計時間(duration)終了は全チャンネル無限再生を許可
                    # (条件成立/指定秒の経過まで再生を続ける)。
                    # 動画イベントも許可(動画終了/終了条件がイベントを終える)
                    allow_infinite = (end_channel is not None or bool(end_conds)
                                      or is_duration or has_video_ch
                                      or is_infinite)
                    # duration の範囲抽選(min_seconds/max_seconds)を先に解析
                    dur_range = None
                    if is_duration and ("min_seconds" in event_end_raw
                                        or "max_seconds" in event_end_raw):
                        dur_range = parse_event_duration_range(
                            event_end_raw, tr("{0} end").format(where))

                    channels: dict[str, Channel] = {}
                    for ch_id, ch_raw in (raw.get("channels") or {}).items():
                        if ch_id not in VALID_CHANNELS:
                            raise ValueError(
                                tr("{0}: チャンネル '{1}' は不正です(有効: {2})").format(where, ch_id, ', '.join(VALID_CHANNELS))
                            )
                        channels[ch_id] = parse_channel(
                            ch_id, ch_raw, tr('{0} チャンネル{1}').format(where, ch_id),
                            allow_infinite=allow_infinite)
                    vcid = check_video_channels(channels, where)
                    if (event_end_raw is None and vcid
                            and channels[vcid].end_type == END_NONE):
                        # 旧「ループ動画には end が必要」の後継(=52)。
                        # 動画chが無限なのにイベント側にも終了条件が無いと、
                        # そのイベントは永久に終わらない。
                        raise ValueError(
                            tr('{0}: 動画チャンネルの終了条件が「無限」のときは、イベントの終了条件(合計時間・変数条件・指定チャンネル終了など)が必要です').format(where))
                    # 動画イベントの device 既定は「担当なし」(チャンネルは
                    # デバイスを駆動しない)。明示指定した種別のみチャンネル駆動。
                    if vcid and raw.get("device") is None:
                        device_map = {}
                    else:
                        device_map = parse_device(raw.get("device"), channels,
                                                  where)
                    check_script_channels(channels, device_map, where)
                    if vcid and raw.get("seek_channel") is not None:
                        raise ValueError(
                            tr('{0}: 動画があるときシークバーは動画に固定されるため seek_channel は指定できません').format(where))

                    if end_channel is not None:
                        if end_channel not in channels:
                            raise ValueError(
                                tr("{0}: end のチャンネル '{1}' が存在しません").format(where, end_channel))
                        etype, count, ms, ev_c_ref, ev_d_refs = END_CHANNEL, 1, 0, None, None
                    elif end_conds:
                        etype, count, ms, ev_c_ref, ev_d_refs = END_COND, 1, 0, None, None
                    elif dur_range is not None:
                        # duration 範囲抽選(値は dur_range 経由で ScenarioEvent へ)
                        etype, count, ms, ev_c_ref, ev_d_refs = END_DURATION, 1, 0, None, None
                    elif is_infinite:
                        # =168: 無限(イベント自身は終わらない)
                        etype, count, ms, ev_c_ref, ev_d_refs = END_NONE, 1, 0, None, None
                    elif event_end_raw is not None:
                        etype, count, ms, ev_c_ref, ev_d_refs = parse_end(
                            event_end_raw, MODE_SEQUENTIAL, where)
                        if etype == END_PLAYS \
                                and not has_content_channels(channels):
                            # =63: plays は音声/動画の本数だけを数えるので、
                            # スクリプト専用chだけでは永久に成立しない
                            raise ValueError(
                                tr('{0}: スクリプト専用チャンネルだけのイベントに「N回の再生で次へ」は使えません(再生回数は音声・動画のみ数えます)').format(where))
                    else:
                        # イベント終了未指定 = 全チャンネルが終了したらイベント終了。
                        # (旧: linear担当chの終了条件を継承していたが、時間指定chが
                        #  他chを打ち切る直感に反する挙動になるため廃止。END_ONCEは
                        #  event_end_met で早期終了せず、全chの自然終了を待つ。)
                        etype, count, ms = END_ONCE, 1, 0
                        ev_c_ref, ev_d_refs = None, None

                    state = EventState(
                        state_id="main", channels=channels, device_map=device_map,
                        seek_channel=parse_seek_channel(
                            raw.get("seek_channel"), channels, where),
                        video_channel=vcid,
                        bgm=parse_bgm(raw.get("bgm"), where))
                    events[event_id] = ScenarioEvent(
                        event_id=event_id,
                        states={"main": state}, start_state="main",
                        end_type=etype, end_count=count, end_duration_ms=ms,
                        end_count_ref=ev_c_ref, end_duration_refs=ev_d_refs,
                        end_channel=end_channel, end_conds=end_conds,
                        next=next_str, next_rule=next_rule, next_choice=next_choice,
                        next_cond=next_cond, next_input=next_input,
                        advance=advance, advance_ref=advance_ref,
                        on_start=on_start_ops,
                        on_end=on_end_ops,
                        **(dur_range or {}),
                    )
                else:
                    # --- items直書き形式 → Cチャンネル1つの"main"ステートに正規化 ---
                    ch = parse_channel(CH_CENTER, raw, where)
                    check_script_channels(
                        {CH_CENTER: ch},
                        {t: CH_CENTER for t in VALID_TRACK_TYPES}, where)
                    state = EventState(
                        state_id="main", channels={CH_CENTER: ch},
                        device_map={t: CH_CENTER for t in VALID_TRACK_TYPES},
                        bgm=parse_bgm(raw.get("bgm"), where))
                    events[event_id] = ScenarioEvent(
                        event_id=event_id,
                        states={"main": state}, start_state="main",
                        end_type=ch.end_type, end_count=ch.end_count,
                        end_duration_ms=ch.end_duration_ms,
                        end_count_ref=ch.end_count_ref,
                        end_duration_refs=ch.end_duration_refs,
                        end_duration_range=ch.end_duration_range,
                        end_duration_min_ms=ch.end_duration_min_ms,
                        end_duration_max_ms=ch.end_duration_max_ms,
                        end_duration_min_ref=ch.end_duration_min_ref,
                        end_duration_max_ref=ch.end_duration_max_ref,
                        next=next_str, next_rule=next_rule, next_choice=next_choice,
                        next_cond=next_cond, next_input=next_input,
                        advance=advance, advance_ref=advance_ref,
                        on_start=on_start_ops,
                        on_end=on_end_ops,
                    )

        elif "nodes" in data:
            # v1 ノード形式 → 音声1件のsequentialイベント(Cチャンネル)に変換
            for node_id, raw in data["nodes"].items():
                next_str, next_rule, next_choice, next_cond, next_input = \
                    parse_next(raw.get("next"), tr("イベント '{0}'").format(node_id))
                on_start_ops = ()
                audio = resolve(raw.get("audio"))
                if not audio:
                    raise ValueError(tr("ノード '{0}' に audio がありません").format(node_id))
                funscript = resolve(raw.get("funscript"))
                tracks = [DeviceTrack(TRACK_LINEAR, funscript)] if funscript else []
                ch = Channel(
                    channel_id=CH_CENTER, mode=MODE_SEQUENTIAL,
                    items=[EventItem(audio=audio, tracks=tracks)],
                    end_type=END_ONCE, pan=Pan(*DEFAULT_PAN[CH_CENTER]),
                )
                state = EventState(
                    state_id="main", channels={CH_CENTER: ch},
                    device_map={t: CH_CENTER for t in VALID_TRACK_TYPES})
                events[node_id] = ScenarioEvent(
                    event_id=node_id,
                    states={"main": state}, start_state="main",
                    end_type=END_ONCE,
                    next=next_str, next_rule=next_rule, next_choice=next_choice,
                    next_cond=next_cond, next_input=next_input,
                    on_start=on_start_ops,
                )
        else:
            raise ValueError(tr("シナリオファイルに events (または nodes) がありません"))

        start = data.get("start")
        if not start or start not in events:
            raise ValueError(tr("start イベントが見つかりません"))

        watches = parse_watch(data.get("watch"))

        # 参照整合性チェック
        errors = []
        # =262: 背景イラストの存在チェック(音声等と同じくエラー扱い)
        if background is not None and not os.path.exists(background.file):
            errors.append(
                tr("背景画像ファイルが見つかりません: {0}").format(
                    background.file))
        for i, w in enumerate(watches):
            if w.to not in events:
                errors.append(
                    tr("watch[{0}] の to '{1}' が存在しません").format(i, w.to))
        for ev in events.values():
            # =168: 終了条件が「無限」のイベントは自分では終わらないので、
            # 「固定」(next文字列)や「分岐」(random)は永久に発火しない。
            # 出口になりうるのは なし(遷移なし)/選択肢/数値入力/
            # 判定式(再生中に常時監視)だけ。
            if ev.end_type == END_NONE:
                if ev.next:
                    errors.append(
                        tr("イベント '{0}': 終了条件が「無限」のときは固定の遷移先を指定できません(選択肢・数値入力・判定式・遷移なしのいずれかにしてください)").format(ev.event_id))
                if ev.next_rule:
                    errors.append(
                        tr("イベント '{0}': 終了条件が「無限」のときはランダム分岐を指定できません(選択肢・数値入力・判定式・遷移なしのいずれかにしてください)").format(ev.event_id))
            if ev.next and ev.next not in events:
                errors.append(tr("イベント '{0}' の next '{1}' が存在しません").format(ev.event_id, ev.next))
            if ev.next_rule or ev.next_choice or ev.next_cond or ev.next_input:
                targets = []
                if ev.next_rule:
                    targets += [c[0] for c in ev.next_rule.candidates]
                    if ev.next_rule.exhausted_to:
                        targets.append(ev.next_rule.exhausted_to)
                    if ev.next_rule.else_to:
                        targets.append(ev.next_rule.else_to)
                if ev.next_choice:
                    targets += [e.to for e in ev.next_choice.entries]
                    if ev.next_choice.default_to:
                        targets.append(ev.next_choice.default_to)
                if ev.next_cond:
                    targets += [to for _conds, to in ev.next_cond.rows]
                    if ev.next_cond.else_to:
                        targets.append(ev.next_cond.else_to)
                if ev.next_input:
                    targets.append(ev.next_input.to)
                for to in targets:
                    if to not in events:
                        errors.append(tr("イベント '{0}' の next '{1}' が存在しません").format(ev.event_id, to))
            # =275: ステート移行の選択肢のイベント宛て行き先と、イベント側の
            # 選択肢/数値入力との併用制限(表示枠は1つ=同時表示を避ける)
            if ev.has_state_choice:
                for st in ev.states.values():
                    for to in st.transition.event_targets if st.transition else ():
                        if to not in events:
                            errors.append(
                                tr("イベント '{0}' ステート'{1}': 選択肢の遷移先イベント '{2}' が存在しません").format(ev.event_id, st.state_id, to))
                rule = ev.next_choice or ev.next_input
                if rule is not None:
                    if ev.end_type == END_NONE:
                        errors.append(
                            tr("イベント '{0}': ステート移行に選択肢があるときは、終了条件が「無限」のイベントにイベント側の選択肢/数値入力を併用できません").format(ev.event_id))
                    elif rule.show_mode != "end":
                        errors.append(
                            tr("イベント '{0}': ステート移行に選択肢があるときは、イベント側の選択肢/数値入力の表示タイミングは「イベント終了条件の達成時」のみ使えます").format(ev.event_id))
            for st in ev.states.values():
              # =256: BGMアイテムの存在チェック(通常アイテムと同じ扱い)
              if st.bgm is not None:
                  for it in st.bgm.items:
                      if not os.path.exists(it.audio):
                          errors.append(
                              tr('BGMの音声ファイルが見つかりません: {0}').format(it.audio))
              for ch in st.channels.values():
                for item in ch.items:
                    if item.audio and not os.path.exists(item.audio):
                        errors.append(tr('音声ファイルが見つかりません: {0}').format(item.audio))
                    if item.video and not os.path.exists(item.video):
                        errors.append(
                            tr('動画ファイルが見つかりません: {0}').format(item.video))
                    missing = False
                    for track in item.tracks:
                        if not os.path.exists(track.funscript):
                            missing = True
                            errors.append(
                                tr('funscriptファイルが見つかりません({0}): {1}').format(track.type, track.funscript)
                            )
                    # =59: 区間指定が素材の長さを超えていないかを検証する
                    # (ユーザー決定: 音声・スクリプトは長さが分かるので
                    #  読み込み時にエラー。動画だけは mpv が無いと分からない
                    #  ので従来どおり再生時の警告=54に任せる)。
                    if (item.audio and item.has_range
                            and os.path.exists(item.audio)):
                        adur = wav_duration_ms(item.audio)
                        if adur > 0 and item.start_s * 1000 >= adur:
                            errors.append(
                                tr('区間の開始({0:g}秒)が音声の長さ({1:g}秒)を超えています: {2}').format(
                                    item.start_s, adur / 1000,
                                    os.path.basename(item.audio)))
                    for track in item.tracks:
                        if not track.has_range or missing:
                            continue
                        try:
                            src = load_script_source(track.funscript)
                        except Exception:
                            continue    # 読み込み失敗は下の一括検証で報告
                        if (src.duration_ms > 0
                                and track.start_s * 1000 >= src.duration_ms):
                            errors.append(
                                tr('区間の開始({0:g}秒)がスクリプトの長さ({1:g}秒)を超えています: {2}').format(
                                    track.start_s, src.duration_ms / 1000,
                                    os.path.basename(track.funscript)))
                    if not item.audio and not item.video and not missing:
                        # スクリプトのみアイテム: 長さ0(=高速空回りの原因)を
                        # 読み込み時に検出する。再生時間は全トラックの最大長。
                        # 動画アイテム(=52)は再生長を動画が決めるので対象外
                        # (トラック0本=デバイスを動かさない動画も許容する)。
                        # =59: 区間指定があるときは**切り出した後**で判定する。
                        # =121: 区間終了の明示で duration_ms は区間の長さに
                        # なる(末尾の停止区間を含む)ため、「動作なし」は
                        # 長さでなく**アクション/ステップの有無**で判定する
                        # (動作0のままアイテムが走ると初期指令が無く、直前の
                        # 動作が続いてしまう事故のもと)。
                        max_dur = 0
                        any_motion = False
                        broken = False
                        for track in item.tracks:
                            try:
                                src = load_script_source(track.funscript)
                                lo, hi = track_range_ms(item, track)
                                if lo > 0 or hi is not None:
                                    src = src.sliced(lo, hi)
                                max_dur = max(max_dur, src.duration_ms)
                                if (getattr(src, "actions", None)
                                        or getattr(src, "steps", None)):
                                    any_motion = True
                            except Exception:
                                broken = True
                                errors.append(
                                    tr('スクリプトを読み込めません({0}): {1}').format(track.type, track.funscript))
                        if not broken and (max_dur <= 0 or not any_motion):
                            if item.has_range or any(t.has_range
                                                     for t in item.tracks):
                                errors.append(
                                    tr('指定した区間にスクリプトの動作がありません: {0}').format(
                                        os.path.basename(item.tracks[0].funscript)))
                            else:
                                errors.append(
                                    tr('スクリプトの長さが0です(アクションがありません): {0}').format(item.tracks[0].funscript))
                    # =74: weight の 0/負は許容(実行時に「抽選に出さない」候補。
                    # 全アイテム0以下はチャンネル自然終了)。正数チェックは廃止
        if errors:
            raise ValueError(tr("シナリオファイルにエラーがあります:\n") + "\n".join(errors))

        return cls(
            title=data.get("title", os.path.basename(path)),
            detail=str(data.get("detail", "") or ""),
            start=start,
            events=events,
            path=path,
            var_decls=var_decls,
            watches=tuple(watches),
            load_warnings=load_warnings,
            device_enabled=device_enabled,
            bgm_enabled=bgm_enabled,
            background=background,
        )
