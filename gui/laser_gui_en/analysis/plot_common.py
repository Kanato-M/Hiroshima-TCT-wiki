# -*- coding: utf-8 -*-
"""Shared helpers for figures, geometry and summaries (used by analyze_*.py of all modes).

  - ROOT style (frame on 4 sides, inward ticks, legend without frame); text in figures is English with units
  - Red: accessor of reduced.npz (per-channel / event-level / pair quantities, sensor coordinates)
  - Geometry: estimate of the strip centers (midpoint of the half-maximum crossings), folding into a gap, gridding
  - Profiles: column average of the band next to a strip, 2D distribution vs Y position + MPV of each row
  - Numbers: main numbers (numbers.json and summary.txt)
Original implementation: analysis/macro/matome/{matome_common,mip2d_common,mip2d_plots}.py.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats as st  # noqa: E402
from features import FeatureConfig, segment_features  # noqa: E402
from scan_data import ScanInfo  # noqa: E402
from trc_reader import read_trc  # noqa: E402

CH_COLORS = {"C1": "#1f77b4", "C2": "#ff7f0e", "C3": "#2ca02c", "C4": "#d62728"}
PAIR_COLORS = ("#1f77b4", "#2ca02c", "#d62728", "#9467bd")
C_BLUE, C_ORANGE, C_GREEN, C_PINK, C_YELLOW, C_GRAY = (
    "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#999999")

SIGMA_REF_PS = 12.5             # common floor of the laser TRIG OUT reference (value adopted in matome)
JITTER_LAW = (781.4, 5.42)      # σ_jit = p0/A + p1 [ps, A in mV] (matome reference value, 1dYtune MPV version)
BAND_UM = (25.0, 75.0)          # "next to the strip": 25–75 µm from the center
HIT_FRAC_MIN = 0.95             # lower limit of the hit rate, to remove points right on the metal (IR does not pass, no signal)
U_FIT_MARGIN = 105.0            # width removed at both ends in the line fit of ΔT(u) (80 µm inside the metal edge)


def ch_color(ch: str) -> str:
    return CH_COLORS.get(ch, "#888888")


def apply_root_style():
    """Reproduce the ROOT plot design with matplotlib.

    Synchronized copy of beam_test/TOF/src/tof_analysis/root_style.py (the single implementation). The GUI analysis is
    distributed on its own, so it keeps a copy (the Japanese GUI laser_gui_v2/analysis holds the same file). When you change rcParams, follow the original file.
    """
    plt.rcParams.update({
        "xtick.direction": "in", "ytick.direction": "in",
        "xtick.top": True, "ytick.right": True,
        "xtick.minor.visible": True, "ytick.minor.visible": True,
        "xtick.major.size": 6, "ytick.major.size": 6,
        "xtick.minor.size": 3, "ytick.minor.size": 3,
        "axes.linewidth": 1.2, "axes.grid": False,
        "legend.frameon": False, "font.family": "sans-serif",
        "font.size": 12, "axes.labelsize": 13, "axes.titlesize": 11.5,
        "xtick.labelsize": 11, "ytick.labelsize": 11, "legend.fontsize": 9.5,
        "savefig.dpi": 140,
    })


apply_root_style()


def legend_on_map(ax, **kw):
    """Legend placed on top of a color map (with a white framed background so that it can be read)."""
    kw.setdefault("frameon", True)
    kw.setdefault("framealpha", 0.85)
    kw.setdefault("fancybox", False)
    kw.setdefault("edgecolor", "0.4")
    return ax.legend(**kw)


def legend_room(ax, loc="upper center", ncol=1, frac=None, **kw):
    """Extend the vertical axis by the legend height before placing the legend, so that it does not overlap the data."""
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return None
    rows = int(np.ceil(len(handles) / max(ncol, 1))) + (1 if kw.get("title") else 0)
    if frac is None:
        frac = min(0.075 * rows + 0.05, 0.6)
    lo, hi = ax.get_ylim()
    bottom = "lower" in loc
    if ax.get_yscale() == "log":
        llo, lhi = np.log10(lo), np.log10(hi)
        span = (lhi - llo) / (1.0 - frac)
        ax.set_ylim(10 ** (lhi - span) if bottom else lo, hi if bottom else 10 ** (llo + span))
    else:
        span = (hi - lo) / (1.0 - frac)
        ax.set_ylim(hi - span if bottom else lo, hi if bottom else lo + span)
    return ax.legend(handles, labels, loc="lower center" if bottom else "upper center", ncol=ncol, **kw)


def panel_label(ax, text):
    ax.set_title(text, loc="left", fontsize=10.5)


def eb(ax, x, y, ey=None, **kw):
    """Data points + error bars (markers only, not connected by lines)."""
    kw.setdefault("marker", "o")
    kw.setdefault("ms", 3.5)
    kw.setdefault("lw", 1.0)
    kw.setdefault("ls", "none")
    return ax.errorbar(np.asarray(x, float), np.asarray(y, float), yerr=ey, **kw)


def savefig(fig, outdir: Path, name: str) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / name
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path}")
    return path


def finite_range(v, lo=2.0, hi=98.0, pad=0.0):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0, 1.0
    a, b = (float(x) for x in np.percentile(v, [lo, hi]))
    return a - pad * (b - a), b + pad * (b - a)


def channels_of(red) -> list[str]:
    return [str(c) for c in red["channels"]]


def snap_grid(vals: np.ndarray, min_step: float = 0.5):
    """Snap the nominal coordinates to an evenly spaced grid (for the grid indices of meanwave_root). Returns (centers, idx)."""
    v = np.asarray(vals, float)
    u = np.unique(np.round(v[np.isfinite(v)], 3))
    if len(u) < 2:
        return u, np.zeros(len(v), int)
    d = np.diff(u)
    d = d[d > min_step]
    step = float(np.median(d)) if len(d) else float(np.ptp(u))
    v0 = u[0]
    idx = np.round((v - v0) / step).astype(int)
    return v0 + step * np.arange(idx.max() + 1), idx


# ----------------------------------------------------------------------
# Accessor of reduced.npz
# ----------------------------------------------------------------------
class Red:
    """Makes reduced.npz accessible by quantity name and adds the sensor coordinates X/Y.

    X = xn + x_off, Y = yn + y_off (xn/yn are the nominal coordinates, without the rotation and focus tracking applied during the scan).
    The log coordinates of the GUI from 2026-09-07 on are already sensor coordinates (X = 0 is the outer frame edge on the readout side),
    so the offsets can be 0. For earlier scans, adjust with --x-off / --y-off.
    """

    def __init__(self, red, x_off: float = 0.0, y_off: float = 0.0, pitch: float = 500.0,
                 body_x=(-9900.0, -700.0)):
        self.npz = red
        self.chs = [str(c) for c in red["channels"]]
        self.nch = len(self.chs)
        self.pair_names = [str(p) for p in red["pair_names"]]
        self.pairs = [tuple(int(v) for v in p) for p in np.asarray(red["pair_index"]).reshape(-1, 2)]
        self._q = {str(n): i for i, n in enumerate(red["quant_names"])}
        self._e = {str(n): i for i, n in enumerate(red["ev_names"])}
        self._p = {str(n): i for i, n in enumerate(red["pair_quant_names"])}
        self._qm = np.stack([red[f"q_{ch}"] for ch in self.chs], axis=1)     # (npt, nch, nq)
        self._ev = np.asarray(red["ev"])
        self._pr = np.asarray(red["pair"])
        self.ok = np.asarray(red["ok"], bool)
        self.X = np.asarray(red["xn"], float) + x_off
        self.Y = np.asarray(red["yn"], float) + y_off
        self.Z = np.asarray(red["zn"], float)
        self.tune = np.asarray(red["tune"], float)
        self.index = np.asarray(red["index"], int)
        self.npt = self.index.size
        self.pitch = float(pitch)
        self.body_x = body_x
        self.noise_ref = np.asarray(red["noise_ref_mV"], float)
        self.eta_hist = np.asarray(red["eta_hist"])
        self.jit_lead_hist = np.asarray(red["jit_lead_hist"])
        self.polarity = float(red["polarity"])
        self.jit_edges = np.asarray(red["jit_edges_ps"], float)

    def q(self, name: str) -> np.ndarray:
        """Per-channel quantity (npt, nch). NaN at points where the acquisition failed (not ok)."""
        v = self._qm[:, :, self._q[name]].astype(float).copy()
        v[~self.ok] = np.nan
        return v

    def ev(self, name: str) -> np.ndarray:
        v = self._ev[:, self._e[name]].astype(float).copy()
        v[~self.ok] = np.nan
        return v

    def pair(self, name: str) -> np.ndarray:
        v = self._pr[:, :, self._p[name]].astype(float).copy()
        v[~self.ok] = np.nan
        return v

    # --- leading channel (the channel with the largest amplitude MPV at each point) ---
    def lead_channel(self) -> np.ndarray:
        a = self.q("amp_mpv_mV")
        lc = np.where(np.isfinite(a), a, -np.inf).argmax(axis=1)
        lc[~np.isfinite(a).any(axis=1)] = -1
        return lc

    def take(self, name: str, c: np.ndarray) -> np.ndarray:
        """From a (npt, nch) quantity, take the value of the given channel at each point."""
        v = self.q(name)[np.arange(self.npt), np.clip(c, 0, self.nch - 1)]
        v[c < 0] = np.nan
        return v

    def lead(self, name: str, valid_only: bool = True) -> np.ndarray:
        """Quantity of the leading channel. With valid_only, points with hit rate < 0.95 (e.g. right on the metal) are NaN."""
        lc = self.lead_channel()
        v = self.take(name, lc)
        if valid_only:
            v[~(self.take("hit_frac", lc) >= HIT_FRAC_MIN)] = np.nan
        return v

    def rank(self, name: str, rank: int) -> np.ndarray:
        """Quantity of the channel with amplitude-MPV rank `rank` (0 = leading, 1 = sub-leading)."""
        a = np.where(np.isfinite(self.q("amp_mpv_mV")), self.q("amp_mpv_mV"), -np.inf)
        c = np.argsort(-a, axis=1)[:, rank]
        v = self.take(name, c)
        v[~(self.take("hit_frac", c) >= HIT_FRAC_MIN)] = np.nan
        return v

    def body(self) -> np.ndarray:
        """The "body" (X range that excludes the pad region and beyond the far end). All points if fewer than 3 columns are in the range."""
        m = (self.X >= self.body_x[0]) & (self.X <= self.body_x[1])
        if np.unique(np.round(self.X[m])).size < min(3, np.unique(np.round(self.X)).size):
            return np.ones(self.npt, bool)
        return m


# ----------------------------------------------------------------------
# Gridding (2D scan)
# ----------------------------------------------------------------------
def cluster_axis(vals, tol: float | None = None):
    """Group coordinate values into columns (rows). They need not be evenly spaced. Returns (centers ascending, idx)."""
    v = np.asarray(vals, float)
    u = np.unique(np.round(v[np.isfinite(v)], 2))
    if u.size == 0:
        return u, np.zeros(v.size, int)
    if tol is None:
        d = np.diff(u)
        tol = 0.25 * float(np.median(d[d > 0.5])) if (d > 0.5).any() else 0.5
    groups = [[u[0]]]
    for x in u[1:]:
        if x - groups[-1][-1] <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    centers = np.array([float(np.mean(g)) for g in groups])
    idx = np.abs(v[:, None] - centers[None, :]).argmin(axis=1)
    return centers, idx


def edges_from_centers(c):
    c = np.asarray(c, float)
    if c.size == 1:
        return np.array([c[0] - 0.5, c[0] + 0.5])
    mid = 0.5 * (c[1:] + c[:-1])
    return np.concatenate([[c[0] - (mid[0] - c[0])], mid, [c[-1] + (c[-1] - mid[-1])]])


class Grid:
    """Grid of a 2D scan (X columns × Y rows)."""

    def __init__(self, X, Y):
        self.xc, self.ix = cluster_axis(X)
        self.yc, self.iy = cluster_axis(Y)
        self.xe, self.ye = edges_from_centers(self.xc), edges_from_centers(self.yc)

    def vals(self, v) -> np.ndarray:
        """Point quantity (npt,) → (NY, NX) (C of pcolormesh, both axes ascending). For duplicate points the last one wins."""
        g = np.full((self.yc.size, self.xc.size), np.nan)
        g[self.iy, self.ix] = np.asarray(v, float)
        return g


def map_panel(ax, grid: Grid, v, label, cmap="viridis", vmin=None, vmax=None):
    """One 2D map in sensor coordinates."""
    if vmin is None or vmax is None:
        lo, hi = finite_range(v)
        vmin = lo if vmin is None else vmin
        vmax = hi if vmax is None else vmax
    pm = ax.pcolormesh(grid.xe, grid.ye, np.ma.masked_invalid(grid.vals(v)), cmap=cmap,
                       vmin=vmin, vmax=vmax, shading="flat", rasterized=True)
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("Y across strips [um]")
    ax.figure.colorbar(pm, ax=ax, pad=0.02).set_label(label)
    return pm


# ----------------------------------------------------------------------
# Geometry: strip centers and gaps
# ----------------------------------------------------------------------
def find_strip_centers(y, amp, n_edge: int = 3):
    """Find the strip center [µm] of each channel from the amplitude profile amp (ny, nch) of one column.

    The IR laser does not pass through the metal, so the profile has an M shape with a dip at the center.
    After a 3-point moving average, the center is the midpoint of the left and right crossings (linear interpolation) with the half-maximum level
    (same as find_strip_centers in analysis/src/aclgad_tct/position.py). For a channel where only one side
    crosses because of the scan-range edge, the full width at half maximum of the channels with both sides is used instead. NaN if not possible.
    """
    o = np.argsort(y)
    y = np.asarray(y, float)[o]
    nch = amp.shape[1]
    cen = np.full(nch, np.nan)
    one_sided, widths = {}, []
    for c in range(nch):
        a = np.nan_to_num(np.asarray(amp[o, c], float))
        if a.size < 5 or a.max() <= 0:
            continue
        a_s = np.convolve(a, np.ones(3) / 3, mode="same")
        half = float(a_s.max()) / 2
        idx = np.where(a_s > half)[0]
        lo, hi = int(idx[0]), int(idx[-1])
        y_l = y[lo - 1] + (half - a_s[lo - 1]) * (y[lo] - y[lo - 1]) / (a_s[lo] - a_s[lo - 1]) \
            if lo > 0 else y[lo]
        y_r = y[hi] + (half - a_s[hi]) * (y[hi + 1] - y[hi]) / (a_s[hi + 1] - a_s[hi]) \
            if hi < y.size - 1 else y[hi]
        cen[c] = 0.5 * (y_l + y_r)
        cut_lo, cut_hi = lo <= n_edge, hi >= y.size - 1 - n_edge
        if not cut_lo and not cut_hi:
            widths.append(y_r - y_l)
        elif not cut_lo:
            one_sided[c] = (float(y_l), True)
        elif not cut_hi:
            one_sided[c] = (float(y_r), False)
        else:
            cen[c] = np.nan            # cut at the scan edge on both sides (the signal of this channel has no peak inside the range)
    if widths:
        w_half = 0.5 * float(np.median(widths))
        for c, (y_x, is_low) in one_sided.items():
            cen[c] = y_x + w_half if is_low else y_x - w_half
    else:
        for c in one_sided:
            cen[c] = np.nan
    # Fallback: for a channel whose outer crossings of the M shape cannot be taken because the scan range is short,
    # use the midpoint of the electrode dip (the inner half-maximum crossings of the M shape)
    # (added 2026-10-19: in a short 1D scan over Y −300…−1200 both C2 and C3 had only one side and everything was NaN)
    for c in range(nch):
        if np.isfinite(cen[c]):
            continue
        a = np.nan_to_num(np.asarray(amp[o, c], float))
        if a.size < 5 or a.max() <= 0:
            continue
        a_s = np.convolve(a, np.ones(3) / 3, mode="same")
        half = float(a_s.max()) / 2
        below = a_s < half
        k = int(np.argmax(a_s))
        # From the maximum outward, the first points that drop below half maximum = candidate dip edges
        # (only a dip with a peak on both sides is accepted)
        runs = []
        j = 0
        while j < a_s.size:
            if below[j]:
                j0 = j
                while j < a_s.size and below[j]:
                    j += 1
                runs.append((j0, j - 1))
            else:
                j += 1
        for j0, j1 in runs:
            if j0 <= n_edge or j1 >= a_s.size - 1 - n_edge:
                continue                      # a dip touching the scan edge is the spread outside the range (gap side), so skip it
            left_peak, right_peak = a_s[:j0].max(), a_s[j1 + 1:].max()
            if left_peak < half or right_peak < half:
                continue
            y_l = y[j0 - 1] + (half - a_s[j0 - 1]) * (y[j0] - y[j0 - 1]) / (a_s[j0] - a_s[j0 - 1])
            y_r = y[j1] + (half - a_s[j1]) * (y[j1 + 1] - y[j1]) / (a_s[j1 + 1] - a_s[j1])
            cen[c] = 0.5 * (y_l + y_r)
            break
    return cen


def fill_centers(cen, chs, pitch: float):
    """Fill the channels with no center from the found channels and the pitch (Y decreases as the channel number increases)."""
    cen = np.array(cen, float)
    num = np.array([int(c[1:]) for c in chs], float)
    ok = np.isfinite(cen)
    if not ok.any():
        return cen
    sign = -1.0
    if ok.sum() >= 2:
        s = np.polyfit(num[ok], cen[ok], 1)[0]
        sign = 1.0 if s > 0 else -1.0
        pitch = abs(s) if 0.5 * pitch < abs(s) < 1.5 * pitch else pitch
    ref = int(np.where(ok)[0][0])
    for c in np.where(~ok)[0]:
        cen[c] = cen[ref] + sign * pitch * (num[c] - num[ref])
    return cen


def strip_centers(r: Red, grid: Grid | None = None):
    """Strip centers at the X of each point (npt, nch), and a description (for the summary).

    1D (Y scan): found from the single profile. 2D: found for each column and smoothed with a linear function of X
    (median of the columns if there are fewer than 5 columns). The amplitude used is the mean (amp_mean), which drops to 0 right on the metal.
    """
    amp = r.q("amp_mean_mV")
    info = {}
    if grid is None or grid.xc.size == 1:
        c0 = find_strip_centers(r.Y[r.ok], amp[r.ok])
        info["found"] = {ch: (None if not np.isfinite(v) else round(float(v), 1))
                         for ch, v in zip(r.chs, c0)}
        cen = fill_centers(c0, r.chs, r.pitch)
        return np.tile(cen, (r.npt, 1)), info
    per_col = np.full((grid.xc.size, r.nch), np.nan)
    for k in range(grid.xc.size):
        m = (grid.ix == k) & r.ok
        if m.sum() >= 5:
            per_col[k] = find_strip_centers(r.Y[m], amp[m])
    # Remove outlier columns (e.g. on the pad): for each channel, drop values more than 150 µm from the median
    med = np.nanmedian(per_col, axis=0)
    per_col[np.abs(per_col - med[None, :]) > 150.0] = np.nan
    cen = np.full((r.npt, r.nch), np.nan)
    off = np.full(r.nch, np.nan)
    for c in range(r.nch):
        m = np.isfinite(per_col[:, c])
        if m.sum() >= 5:
            p = np.polyfit(grid.xc[m] / 1e3, per_col[m, c], 1)
            cen[:, c] = np.polyval(p, r.X / 1e3)
            off[c] = np.polyval(p, np.median(grid.xc) / 1e3)
            info[r.chs[c]] = f"center {p[1]:+.1f} um at X=0, slope {p[0]:+.2f} um/mm"
        elif m.any():
            off[c] = float(np.nanmedian(per_col[:, c]))
            cen[:, c] = off[c]
            info[r.chs[c]] = f"center {off[c]:+.1f} um (median of {int(m.sum())} columns)"
    filled = fill_centers(off, r.chs, r.pitch)
    for c in np.where(~np.isfinite(off))[0]:
        cen[:, c] = filled[c]
        info[r.chs[c]] = f"center {filled[c]:+.1f} um (from the pitch; strip edge not in the scan)"
    return cen, info


def fold(r: Red, cen):
    """Fold each point onto the nearest strip and gap.

    dy (npt, nch): signed distance from each channel center. k: nearest channel. dist: that distance.
    gap: the pair the point belongs to (index in r.pairs, −1 outside the range). u: distance from the center of the A-side strip of the pair.
    width: center-to-center distance of that gap.
    """
    dy = r.Y[:, None] - cen
    k = np.abs(dy).argmin(axis=1)
    dist = dy[np.arange(r.npt), k]
    gap = np.full(r.npt, -1)
    u = np.full(r.npt, np.nan)
    width = np.full(r.npt, np.nan)
    for g, (ca, cb) in enumerate(r.pairs):
        lo, hi = np.minimum(cen[:, ca], cen[:, cb]), np.maximum(cen[:, ca], cen[:, cb])
        m = (r.Y >= lo) & (r.Y <= hi)
        gap[m] = g
        u[m] = np.abs(r.Y - cen[:, ca])[m]
        width[m] = (hi - lo)[m]
    return {"cen": cen, "dy": dy, "k": k, "dist": dist, "gap": gap, "u": u, "width": width}


def gap_pair(r: Red, f, name: str) -> np.ndarray:
    """Pair quantity of the gap each point belongs to (npt,). NaN outside the gaps."""
    v = np.full(r.npt, np.nan)
    ok = f["gap"] >= 0
    v[ok] = r.pair(name)[np.where(ok)[0], f["gap"][ok]]
    return v


def band_mask(r: Red, f, c: int, band=BAND_UM):
    """Points next to the strip of channel c (band µm from the center, hit rate ≥ 0.95)."""
    a = np.abs(f["dy"][:, c])
    return (a >= band[0]) & (a <= band[1]) & (r.q("hit_frac")[:, c] >= HIT_FRAC_MIN)


def ch_cells(r: Red, c: int, amp_min: float = 10.0):
    """Body points where the signal of channel c is valid (hit rate ≥ 0.95, amplitude MPV ≥ amp_min)."""
    return r.body() & (r.q("hit_frac")[:, c] >= HIT_FRAC_MIN) & (r.q("amp_mpv_mV")[:, c] >= amp_min)


def band_median(r: Red, f, name: str):
    """Median ± standard error over the points next to the strips of all channels (body)."""
    v = r.q(name)
    vals = [v[band_mask(r, f, c) & r.body(), c] for c in range(r.nch)]
    return st.median_sem(np.concatenate(vals))


def column_profile(grid: Grid, vals, errs, mask):
    """Inverse-variance weighted mean of the points inside mask for each column (X). Returns (X, mean, err, n)."""
    mu = np.full(grid.xc.size, np.nan)
    er = np.full(grid.xc.size, np.nan)
    n = np.zeros(grid.xc.size, int)
    for i in range(grid.xc.size):
        m = mask & (grid.ix == i) & np.isfinite(vals) & np.isfinite(errs) & (errs > 0)
        n[i] = int(m.sum())
        if n[i]:
            mu[i], er[i], _ = st.wmean(vals[m], errs[m])
    return grid.xc, mu, er, n


def band_profile(r: Red, grid: Grid, f, vals_c, errs_c, c: int, band=BAND_UM):
    """Profile along the strip of the band next to the strip of channel c (column average with the row-by-row systematic offset removed).

    The rows inside the band (different distances from the center) are shifted systematically (20–35 ps in ToA). This shift is
    the same in all columns, so the row offset (median over the body columns) is subtracted before averaging each column.
    The error is max(statistical error, spread of the row residuals/√n). Returns (X, mean, err, n_rows).
    """
    msk = band_mask(r, f, c, band) & np.isfinite(vals_c) & np.isfinite(errs_c)
    nrow, ncol = grid.yc.size, grid.xc.size
    T = np.full((nrow, ncol), np.nan)
    E = np.full((nrow, ncol), np.nan)
    T[grid.iy[msk], grid.ix[msk]] = vals_c[msk]
    E[grid.iy[msk], grid.ix[msk]] = errs_c[msk]
    cnt = np.isfinite(T).sum(axis=1)
    nan = np.full(ncol, np.nan)
    if cnt.max() == 0:
        return grid.xc, nan, nan.copy(), np.zeros(ncol, int)
    rows = np.where(cnt >= 0.5 * cnt.max())[0]
    T, E = T[rows], E[rows]
    body = (grid.xc >= r.body_x[0]) & (grid.xc <= r.body_x[1])
    if body.sum() < 3:
        body = np.ones(ncol, bool)
    with np.errstate(invalid="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            col_mean = np.nanmean(T, axis=0)
            a_r = np.nanmedian((T - col_mean[None, :])[:, body], axis=1)
            a_r = np.where(np.isfinite(a_r), a_r, 0.0)
            a_r -= a_r.mean()
            Tc = T - a_r[:, None]
            n = np.isfinite(Tc).sum(axis=0)
            mu = np.nanmean(Tc, axis=0)
            stat = np.sqrt(np.nansum(E ** 2, axis=0)) / np.maximum(n, 1)
            scat = np.where(n >= 2, np.nanstd(Tc, axis=0, ddof=1) / np.sqrt(np.maximum(n, 1)), 0.0)
    er = np.maximum(stat, np.nan_to_num(scat))
    mu[n == 0] = np.nan
    er[n == 0] = np.nan
    return grid.xc, mu, er, n


# ----------------------------------------------------------------------
# 2D distribution vs position + MPV of each bin
# ----------------------------------------------------------------------
def _draw_2d(ax, pe, ve, H, zlabel, xlabel, centers=None):
    pm = ax.pcolormesh(pe, ve, np.ma.masked_less_equal(H.T, 0), cmap="viridis", shading="flat",
                       rasterized=True, vmin=0)
    ax.figure.colorbar(pm, ax=ax, pad=0.02).set_label(zlabel)
    for y in ([] if centers is None else centers):
        ax.axvline(y, color=C_GRAY, ls=":", lw=0.9)
    ax.set_xlabel(xlabel)
    ax.set_xlim(pe[0], pe[-1])
    ax.set_ylim(ve[0], ve[-1])


def events_vs_pos(ax, pos, hist_pt, v_edges, ylabel, xlabel, mask=None, centers=None,
                  n_min: int = 200):
    """2D distribution with position on the horizontal axis and per-event values on the vertical axis (points at the same position are summed), and the MPV point at each position.

    hist_pt: (npt, NV) per-point event histograms. The MPV is the KDE peak of a sample made by spreading the histogram
    uniformly inside each bin. Returns (position, MPV, error).
    """
    pc, ip = cluster_axis(pos)
    pe = edges_from_centers(pc)
    ok = np.ones(len(pos), bool) if mask is None else mask
    vc = 0.5 * (v_edges[1:] + v_edges[:-1])
    H = np.zeros((pc.size, vc.size))
    np.add.at(H, ip[ok], np.asarray(hist_pt)[ok].astype(float))
    _draw_2d(ax, pe, v_edges, H, "Events / bin", xlabel, centers)
    ax.set_ylabel(ylabel)
    rng = np.random.default_rng(20261005)
    mu = np.full(pc.size, np.nan)
    er = np.full(pc.size, np.nan)
    for i in range(pc.size):
        cnt = H[i].astype(int)
        if cnt.sum() < n_min:
            continue
        x = np.repeat(vc, cnt)
        x = x + (rng.random(x.size) - 0.5) * (v_edges[1] - v_edges[0])
        mu[i], er[i], _ = st.mpv(x, n_min=n_min, n_boot=30)
    eb(ax, pc, mu, er, color="r", ms=3, lw=0.8, label="MPV per position")
    return pc, mu, er


def cells_vs_pos(ax, pos, vals, v_edges, ylabel, xlabel, mask=None, centers=None, n_min: int = 12,
                 color="r", marker="o", label="MPV over cells per row", draw=True):
    """2D distribution with position (row) on the horizontal axis and per-cell values (resolution etc.) on the vertical axis, and the MPV point of each row.

    A resolution has only one value per cell (one scan point), so the vertical axis is the cell value and the shade is the number of cells.
    One row contains the cells of all body columns. Returns (position, MPV, error).
    """
    pc, ip = cluster_axis(pos)
    pe = edges_from_centers(pc)
    v = np.asarray(vals, float)
    ok = np.isfinite(v) & (np.ones(v.size, bool) if mask is None else mask)
    if draw:
        H = np.histogram2d(pc[ip[ok]], v[ok], bins=(pe, v_edges))[0]
        _draw_2d(ax, pe, v_edges, H, "Cells / bin", xlabel, centers)
        ax.set_ylabel(ylabel)
    mu = np.full(pc.size, np.nan)
    er = np.full(pc.size, np.nan)
    for i in range(pc.size):
        mu[i], er[i], _ = st.mpv(v[ok & (ip == i)], n_min=n_min, n_boot=50)
    eb(ax, pc, mu, er, color=color, marker=marker, ms=3, lw=0.8, label=label)
    return pc, mu, er


def row_range(pc, mu, centers, lo=5.0, hi=95.0, edge_um=35.0):
    """lo/hi percentiles of the row MPVs between the readout strips (excluding the metal and the penumbra edge_um)."""
    centers = np.asarray(centers, float)
    ok = np.isfinite(mu) & (pc < centers.max()) & (pc > centers.min())
    ok &= np.abs(pc[:, None] - centers[None, :]).min(axis=1) > edge_um
    if ok.sum() < 3:
        return np.nan, np.nan
    return tuple(float(x) for x in np.percentile(mu[ok], [lo, hi]))


# ----------------------------------------------------------------------
# Main numbers (numbers.json / summary.txt)
# ----------------------------------------------------------------------
class Numbers:
    """key → value ± err [unit]. The key names match the numbers.json of the "matome" analysis note
    (without the scan abbreviations FS/BP)."""

    def __init__(self):
        self.data = {}

    def set(self, key, value, err=None, unit="", note=""):
        def _f(v):
            if v is None:
                return None
            v = float(v)
            return v if np.isfinite(v) else None
        self.data[key] = {"value": _f(value), "err": _f(err), "unit": unit, "note": note}

    def get(self, key, default=np.nan):
        v = self.data.get(key, {}).get("value")
        return default if v is None else v

    def lines(self):
        out = []
        for k, d in self.data.items():
            if d["value"] is None:
                continue
            s = f"{d['value']:.4g}" + (f" +- {d['err']:.2g}" if d["err"] is not None else "")
            out.append(f"{k:44s} {s} {d['unit']}" + (f"   ({d['note']})" if d["note"] else ""))
        return out

    def save(self, outdir: Path, header_lines):
        outdir.mkdir(parents=True, exist_ok=True)
        with open(outdir / "numbers.json", "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
        text = list(header_lines) + ["", "# key numbers (preliminary; same definitions as the matome note)"] \
            + self.lines()
        (outdir / "summary.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
        print(f"  wrote {outdir / 'summary.txt'}")
        print(f"  wrote {outdir / 'numbers.json'}")


def header_lines(scan: ScanInfo, r: Red, extra=()):
    return [f"scan: {scan.name}", f"mode: {scan.mode} ({scan.tag})",
            f"points: {r.npt} (ok {int(r.ok.sum())})", f"channels: {','.join(r.chs)}",
            "noise reference [mV]: " + ", ".join(f"{c} {v:.2f}" for c, v in zip(r.chs, r.noise_ref)),
            "conventions: ToA = CFD 50 % (linear interp., per-segment TRIGTIME); MPV = KDE peak; "
            "sigma = +-1.5 sigma core Gaussian; sigma_t is w.r.t. the laser TRIG OUT "
            f"(includes the common floor sigma_ref ~ {SIGMA_REF_PS} ps)"] + list(extra)


# ----------------------------------------------------------------------
# Shared by all modes: waveform figure of the max-amplitude point and summary CSV
# ----------------------------------------------------------------------
def best_point(r: Red) -> int:
    a = r.lead("amp_mpv_mV", valid_only=False)
    return int(np.nanargmax(a)) if np.isfinite(a).any() else 0


def point_label(r: Red, i: int) -> str:
    s = f"idx {int(r.index[i])}  X={r.X[i]:.0f} Y={r.Y[i]:.0f} Z={r.Z[i]:.0f} um"
    return s + (f"  tune={r.tune[i]:.0f}%" if np.isfinite(r.tune[i]) else "")


def waveform_fig(scan: ScanInfo, r: Red, ipoint: int, outdir: Path, cfg: FeatureConfig | None = None,
                 n_overlay: int = 60):
    """At the max-amplitude point: (a) mean waveforms (b) raw waveforms of the leading channel (c) amplitude distribution (√N error bars)."""
    cfg = cfg or FeatureConfig()
    idx = int(r.index[ipoint])
    pol = r.polarity
    waves, feats = {}, {}
    for ch in r.chs:
        p = scan.file_for(ch, idx)
        if p.exists():
            waves[ch] = read_trc(p)
            feats[ch] = segment_features(waves[ch], cfg, pol)
    if not waves:
        print(f"[warn] waveform figure: point idx={idx} has no .trc file")
        return
    lead = max(feats, key=lambda c: np.nanmedian(feats[c]["amp"]))
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.6))
    ax = axes[0]
    for ch, w in waves.items():
        v = pol * w.volts()
        mw = v.mean(axis=0)
        nb = max(int(round(cfg.baseline_frac * mw.size)), 4)
        sem = v.std(axis=0, ddof=1) / np.sqrt(v.shape[0])
        ax.errorbar(w.time_axis() * 1e9, (mw - mw[:nb].mean()) * 1e3, yerr=sem * 1e3, marker="o",
                    ms=2.5, lw=0.8, color=ch_color(ch), label=ch)
    ax.set_xlabel("Time from trigger [ns]")
    ax.set_ylabel("Mean waveform - baseline [mV]")
    ax.legend()
    panel_label(ax, f"(a) mean waveforms, {point_label(r, ipoint)}")
    ax = axes[1]
    w = waves[lead]
    t = w.time_axis() * 1e9
    v = pol * w.volts()
    bl = pol * feats[lead]["baseline"] * 1e-3
    step = max(1, w.n_segments // n_overlay)
    for s in range(0, w.n_segments, step):
        ax.plot(t, (v[s] - bl[s]) * 1e3, lw=0.4, alpha=0.25, color="#555555")
    ax.plot(t, (v.mean(axis=0) - bl.mean()) * 1e3, lw=1.6, color=ch_color(lead), label=f"{lead} mean")
    ax.set_xlabel("Time from trigger [ns]")
    ax.set_ylabel("Waveform - baseline [mV]")
    ax.legend()
    panel_label(ax, f"(b) {lead}: every {step}th of {w.n_segments} segments")
    ax = axes[2]
    for ch in feats:
        amp = feats[ch]["amp"]
        cnt, edges = np.histogram(amp, bins=80)
        cx = 0.5 * (edges[1:] + edges[:-1])
        m, e, _ = st.mpv(amp, n_boot=30)
        nz = cnt > 0
        ax.errorbar(cx[nz], cnt[nz], yerr=np.sqrt(cnt[nz]), marker="o", ms=2.5, lw=0.8, ls="none",
                    color=ch_color(ch), label=f"{ch}: MPV = {m:.1f} +- {e:.1f} mV")
    ax.set_xlabel("Amplitude [mV]")
    ax.set_ylabel("Events / bin")
    legend_room(ax)
    panel_label(ax, "(c) amplitude distributions")
    savefig(fig, outdir, "fig_waveforms.png")


def write_summary_csv(r: Red, outdir: Path, name: str = "summary.csv") -> Path:
    """Write all per-point summary quantities (per-channel, event-level, pair) to one CSV."""
    red = r.npz
    outdir.mkdir(parents=True, exist_ok=True)
    qn = [str(n) for n in red["quant_names"]]
    en = [str(n) for n in red["ev_names"]]
    pn = [str(n) for n in red["pair_quant_names"]]
    # An npz is decompressed on every access, so read it only once outside the loop
    idx, xr, yr, zr, okr = (np.asarray(red[k]) for k in ("index", "x_um", "y_um", "z_um", "ok"))
    ev, pr = np.asarray(red["ev"]), np.asarray(red["pair"])
    qm = {ch: np.asarray(red[f"q_{ch}"]) for ch in r.chs}
    path = outdir / name
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        head = ["index", "x_um", "y_um", "z_um", "X_um", "Y_um", "tune_pct", "ok"] + en
        for ch in r.chs:
            head += [f"{q}_{ch}" for q in qn]
        for p in r.pair_names:
            head += [f"pair_{q}_{p}" for q in pn]
        w.writerow(head)
        for i in range(r.npt):
            row = [int(idx[i]), f"{xr[i]:.2f}", f"{yr[i]:.2f}", f"{zr[i]:.2f}", f"{r.X[i]:.2f}",
                   f"{r.Y[i]:.2f}", "" if not np.isfinite(r.tune[i]) else f"{r.tune[i]:.1f}", int(okr[i])]
            row += [f"{v:.6g}" for v in ev[i]]
            for ch in r.chs:
                row += [f"{v:.6g}" for v in qm[ch][i]]
            for j in range(len(r.pair_names)):
                row += [f"{v:.6g}" for v in pr[i, j]]
            w.writerow(row)
    print(f"  wrote {path}")
    return path


def finish(scan: ScanInfo, r: Red, ns: Numbers, outdir: Path, extra_header=()):
    """Common final step of all modes: waveform figure, summary.csv, summary.txt, numbers.json."""
    waveform_fig(scan, r, best_point(r), outdir)
    write_summary_csv(r, outdir)
    ns.save(outdir, header_lines(scan, r, extra_header))
