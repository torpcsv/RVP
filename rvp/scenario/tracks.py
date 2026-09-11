"""トラック関連のヘルパー(wav 長さ・スクリプト読み込み・区間・種別正規化・自動紐づけ)。"""
from __future__ import annotations

import os
import wave
from ..funscript import Funscript
from ..rotate_source import load_rotate_source

from .constants import (OLD_TRACK_ALIASES, TRACK_LINEAR, TRACK_ROTATE_A10,
    TRACK_ROTATE_UFO, TRACK_TWIST, TRACK_VIBRATION, VALID_TRACK_TYPES)


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
