"""シナリオ書式の定数(再生モード・終了条件・移行条件・トラック種別・チャンネル・既定パン)。"""
from __future__ import annotations




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
