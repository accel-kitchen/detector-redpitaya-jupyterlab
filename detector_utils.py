"""検出器ノートブック共通の汎用ユーティリティ。

import detector_utils as du
# または
from detector_utils import find_latest, auto_bin_width, shared_xbins, parabolic_peak_offset

依存は numpy と標準ライブラリ (pathlib) のみ。Red Pitaya 本体でも、手元の PC でも
そのまま読み込めます (rp や plotly に依存しません)。

ここに集めているのは「データ解析の本筋ではない、配線的なヘルパー」です:
- find_latest         … data フォルダから一番新しいファイルを探す
- auto_bin_width      … ヒストグラムの自動ビン幅
- shared_xbins        … 複数配列でそろえたヒストグラムの区切り
- parabolic_peak_offset … 相互相関ピークのサブサンプル補間 (放物線フィット)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def find_latest(ext: str, folder: str = "./data") -> Path:
    '''folder 内で拡張子 ext を持つファイルのうち、更新時刻が最新のものを返す。'''
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(
            f"{folder} が見つかりません。取得用ノートブックでデータを取った後に実行してください。"
        )
    candidates = sorted(folder.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"{folder} の中に *{ext} ファイルが見つかりません。")
    return candidates[0]


def auto_bin_width(arrays, target_bins: int = 30) -> float:
    '''まとめた範囲を target_bins 本に分けたときの、棒 1 本の幅を返す。'''
    lo = min(float(np.min(a)) for a in arrays)
    hi = max(float(np.max(a)) for a in arrays)
    return (hi - lo) / target_bins if hi > lo else 1.0


def shared_xbins(arrays, width: float) -> dict:
    '''複数の配列でそろえた、ヒストグラムの棒の区切り (start, end, size) を返す。
    棒の境目がそろうように、開始・終了を幅の倍数に合わせる。'''
    lo = min(float(np.min(a)) for a in arrays)
    hi = max(float(np.max(a)) for a in arrays)
    start = np.floor(lo / width) * width
    end   = np.ceil (hi / width) * width
    return dict(start=float(start), end=float(end), size=float(width))


def parabolic_peak_offset(y_m1: float, y_0: float, y_p1: float) -> float:
    '''ピーク前後の 3 点に放物線を当てはめて、ピークのずれ (オフセット) を返す。
    分母が 0 のとき (ピークが平ら) は 0 を返す。'''
    denom = 2.0 * (y_m1 - 2.0 * y_0 + y_p1)
    if denom == 0:
        return 0.0
    return (y_m1 - y_p1) / denom


__all__ = ["find_latest", "auto_bin_width", "shared_xbins", "parabolic_peak_offset"]
