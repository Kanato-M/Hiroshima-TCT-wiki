# -*- coding: utf-8 -*-
"""Non-parametric estimate of the most probable value (MPV) of a distribution.

The distributions in this analysis (jitter ∝ 1/A, amplitude, Δt, ...) have asymmetric tails
from the Landau shape or the 1/A transform, so the representative value is the **MPV**, not the mean or the median
(project rule, 2026-08-25). So that it also works for quantities where a Landau fit does not match the shape (jitter etc.),
the MPV is defined here as the peak of a kernel density estimate (KDE):

  1. Robust scale s = min(std, IQR/1.349), bandwidth h = 0.9·s·N^(−1/5)
     (Silverman's rule of thumb. It uses the IQR instead of the std, which grows with the tail,
     so it does not over-smooth even with a Landau tail)
  2. Count the values on a grid with step h/4 and convolve with a Gaussian kernel (±4h) to get the density
     (fast, O(N + M), even for N ~ 10^5)
  3. Parabolic interpolation of the highest-density bin and its two neighbors gives the peak position = MPV
  4. Error: standard deviation of a bootstrap (default 100 samples) that resamples the counts with a multinomial distribution

NaN for N < n_min. If the distribution is degenerate (s = 0), the median is returned.

This file is a synchronized copy of laser_test/analysis/src/aclgad_tct/mpv.py (numpy only).
It is duplicated so that the GUI quick-look analysis can be distributed on its own. When you change the algorithm, also update the original file,
the beam side (beam_test/TOF/src/tof_analysis/mpv.py) and the Japanese GUI (laser_gui_v2/analysis holds the same file) at the same time.
"""

from __future__ import annotations

import numpy as np

N_MIN_DEFAULT = 50
N_BOOT_DEFAULT = 100


def _kde_counts(counts: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    return np.convolve(counts, kernel, mode="same")


def _peak(density: np.ndarray, grid: np.ndarray) -> float:
    """Peak position from a parabolic interpolation around the highest-density bin."""
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
    """Returns (MPV, bootstrap error, N). Invalid values are removed."""
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
    if m > 4096:                       # keep the grid finite even for an extremely wide tail
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
