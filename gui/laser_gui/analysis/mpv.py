# -*- coding: utf-8 -*-
"""分布の最頻値 (MPV, most probable value) のノンパラメトリック推定。

本解析の分布 (ジッター ∝ 1/A、振幅、Δt など) は Landau や 1/A 変換による
非対称な裾を持つため、代表値には平均や中央値ではなく **MPV** を使う
(プロジェクト規約、2026-08-25)。Landau フィットは形状が合わない量 (ジッター等)
にも使えるよう、ここではカーネル密度推定 (KDE) のピークで MPV を定義する:

  1. 頑健スケール s = min(std, IQR/1.349)、バンド幅 h = 0.9·s·N^(−1/5)
     (Silverman の経験則。裾で膨らむ std ではなく IQR で決めるため
     Landau 裾があっても過平滑化しない)
  2. h/4 刻みの格子に度数を取り、ガウスカーネル (±4h) で畳み込んで密度を作る
     (N ~ 10^5 でも O(N + M) で速い)
  3. 密度最大のビンとその両隣で放物線補間してピーク位置 = MPV
  4. 誤差: 度数を多項分布で再標本化するブートストラップ (既定 100 回) の標準偏差

N < n_min では NaN。分布が退化 (s = 0) なら中央値を返す。

本ファイルは laser_test/analysis/src/aclgad_tct/mpv.py の同期コピー (numpy のみ)。
GUI の簡易解析を単体で配布できるよう複製している。アルゴリズムを変えるときは元ファイルと
beam 側 (beam_test/TOF/src/tof_analysis/mpv.py)、英語版 GUI (laser_gui_v2_en) も同時に更新すること。
"""

from __future__ import annotations

import numpy as np

N_MIN_DEFAULT = 50
N_BOOT_DEFAULT = 100


def _kde_counts(counts: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    return np.convolve(counts, kernel, mode="same")


def _peak(density: np.ndarray, grid: np.ndarray) -> float:
    """密度最大ビンの両隣で放物線補間したピーク位置。"""
    i = int(np.argmax(density))
    if 0 < i < density.size - 1:
        y0, y1, y2 = density[i - 1], density[i], density[i + 1]
        denom = y0 - 2.0 * y1 + y2
        if denom < 0:
            shift = 0.5 * (y0 - y2) / denom
            shift = float(np.clip(shift, -1.0, 1.0))
            return float(grid[i] + shift * (grid[1] - grid[0]))
    return float(grid[i])


def mpv_estimate(values, n_min: int = N_MIN_DEFAULT,
                 n_boot: int = N_BOOT_DEFAULT, seed: int = 0
                 ) -> tuple[float, float, int]:
    """(MPV, ブートストラップ誤差, N) を返す。無効値は除く。"""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    n = int(v.size)
    if n < n_min:
        return np.nan, np.nan, n
    q25, q50, q75 = np.percentile(v, [25, 50, 75])
    s = min(float(v.std(ddof=1)), float((q75 - q25) / 1.349))
    if not np.isfinite(s) or s <= 0:
        return float(q50), 0.0, n
    h = 0.9 * s * n ** (-0.2)
    lo, hi = np.percentile(v, [0.5, 99.5])
    lo, hi = lo - 3 * h, hi + 3 * h
    step = h / 4.0
    m = int(np.ceil((hi - lo) / step)) + 1
    if m > 4096:                       # 極端に広い裾でも格子を有限に保つ
        step = (hi - lo) / 4095.0
        m = 4096
    edges = lo + step * np.arange(m + 1)
    grid = 0.5 * (edges[:-1] + edges[1:])
    counts = np.histogram(v, bins=edges)[0].astype(np.float64)
    kx = np.arange(-int(np.ceil(4 * h / step)), int(np.ceil(4 * h / step)) + 1) * step
    kernel = np.exp(-0.5 * (kx / h) ** 2)
    kernel /= kernel.sum()
    mpv = _peak(_kde_counts(counts, kernel), grid)
    if n_boot <= 0:
        return mpv, np.nan, n
    rng = np.random.default_rng(seed)
    p = counts / counts.sum()
    boots = np.empty(n_boot)
    for b in range(n_boot):
        cb = rng.multinomial(n, p).astype(np.float64)
        boots[b] = _peak(_kde_counts(cb, kernel), grid)
    return mpv, float(boots.std(ddof=1)), n
