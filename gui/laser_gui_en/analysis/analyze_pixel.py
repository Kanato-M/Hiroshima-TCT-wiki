# -*- coding: utf-8 -*-
"""2D scan analysis of a pixel sensor (2x2 channel readout) (kind = "pixel" of run_analysis.py).

1. Quick figures from the simple reduction (reduced.npz):
     fig1_pixel_amplitude.png   amplitude maps (A_lead, A_total, A_sub, A_lead+A_sub, A_lead/A_total, hit rate = pad positions)
     fig2_pixel_timing.png      maps of the rise time 10–90 % / 40–60 %, jitter and sigma_t of the leading pixel (linear CFD50)
2. Main analysis with the pulse-template fit (macro\\template_timing\\tt_pixel.py): time resolution (leading / 2 / 3 / 4 pixels,
   A² weights, best per position), position resolution (amplitude centroid of 2 / 3 / 4 pixels, best per axis), amplitude
   distributions, propagation → pix01–pix06_*.png and results\\<scan>\\pixel\\ (cells.csv, numbers.json, README.md, simple\\).
   It reads the .trc files directly, so no ROOT conversion is needed. Only step 1 runs when the workspace has no macro\\template_timing.

The pixel geometry (pads = signal-free 100 µm squares, pitch 500 µm) is found automatically from the amplitude and the hit rate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_common as pc  # noqa: E402
from plot_common import plt  # noqa: E402

PITCH_UM, PAD_UM = 500.0, 100.0


def _pads(r: pc.Red, g: pc.Grid):
    """Pad centers from clusters of signal-free cells (hit rate < 0.5); clusters cut by the scan edge are completed to 100 µm squares."""
    hit = np.nan_to_num(r.q("hit_frac").max(axis=1))
    M = g.vals((r.ok & (hit < 0.5)).astype(float)) > 0.5
    lab = np.zeros(M.shape, int)
    nl = 0
    for iy, ix in zip(*np.where(M)):
        if lab[iy, ix]:
            continue
        nl += 1
        stack = [(iy, ix)]
        lab[iy, ix] = nl
        while stack:
            a, b = stack.pop()
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                aa, bb = a + da, b + db
                if 0 <= aa < M.shape[0] and 0 <= bb < M.shape[1] and M[aa, bb] and not lab[aa, bb]:
                    lab[aa, bb] = nl
                    stack.append((aa, bb))
    pads = []
    for l in range(1, nl + 1):
        iy, ix = np.where(lab == l)
        if iy.size < 10:
            continue
        x0, x1, y0, y1 = g.xc[ix.min()], g.xc[ix.max()], g.yc[iy.min()], g.yc[iy.max()]
        if abs(x1 - x0) < PAD_UM - 15:
            x0, x1 = (x1 - PAD_UM, x1) if ix.min() == 0 else ((x0, x0 + PAD_UM) if ix.max() == g.xc.size - 1 else (x0, x1))
        if abs(y1 - y0) < PAD_UM - 15:
            y0, y1 = (y1 - PAD_UM, y1) if iy.min() == 0 else ((y0, y0 + PAD_UM) if iy.max() == g.yc.size - 1 else (y0, y1))
        pads.append((0.5 * (x0 + x1), 0.5 * (y0 + y1)))
    return pads


def _map(ax, g, v, label, pads, vmin=None, vmax=None, cmap="viridis"):
    pc.map_panel(ax, g, v, label, cmap=cmap, vmin=vmin, vmax=vmax)
    for (cx, cy) in pads:
        ax.add_patch(plt.Rectangle((cx - PAD_UM / 2, cy - PAD_UM / 2), PAD_UM, PAD_UM, fill=False, ec="w", lw=1.0, ls="--"))
    ax.set_xlabel("X [um]")
    ax.set_ylabel("Y [um]")
    ax.set_aspect("equal")


def quick_figures(r: pc.Red, g: pc.Grid, outdir: Path, ns: pc.Numbers):
    pads = _pads(r, g)
    print("  pads (no-signal squares): " + (", ".join(f"({x:.0f}, {y:.0f})" for x, y in pads) or "none found"), flush=True)
    hit = r.q("hit_frac").max(axis=1)
    fig, axes = plt.subplots(2, 3, figsize=(17.5, 10.5))
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = r.ev("amp_lead_mpv_mV") / r.ev("amp_tot_mpv_mV")
    items = ((r.ev("amp_lead_mpv_mV"), "A_lead MPV [mV]", "(a) leading amplitude"),
             (r.ev("amp_tot_mpv_mV"), "A_total (sum of 4 pixels) MPV [mV]", "(b) total amplitude"),
             (r.ev("amp_sub_mpv_mV"), "A_sub MPV [mV]", "(c) sub-leading amplitude"),
             (r.ev("amp_ls_mpv_mV"), "A_lead + A_sub MPV [mV]", "(d) leading + sub-leading"))
    for ax, (v, lab, t) in zip(axes.ravel()[:4], items):
        _map(ax, g, v, lab, pads, 0, np.nanpercentile(v[r.ok], 99.5))
        pc.panel_label(ax, t)
    _map(axes[1, 1], g, ratio, "A_lead / A_total", pads, 0.2, 1.0)
    pc.panel_label(axes[1, 1], "(e) leading share (0.25 = 4 equal pixels)")
    _map(axes[1, 2], g, hit, "hit fraction (max over pixels)", pads, 0, 1)
    pc.panel_label(axes[1, 2], "(f) hit fraction (dark squares = bonding pads)")
    pc.savefig(fig, outdir, "fig1_pixel_amplitude.png")
    fig, axes = plt.subplots(2, 3, figsize=(17.5, 10.5))
    lc = r.lead_channel()
    for ax, (key, lab, t, lo, hi) in zip(axes.ravel(), (("rise1090_mpv_ps", "rise time 10-90% [ps]", "(a) rise time 10-90%, leading pixel", None, None),
                                                      ("rise4060_mpv_ps", "rise time 40-60% [ps]", "(b) rise time 40-60%", None, None),
                                                      ("jit_4060_mpv_ps", "jitter (40-60% slope) [ps]", "(c) jitter, leading pixel", 10, 60),
                                                      ("jit_cfd50_mpv_ps", "jitter (CFD50 slope) [ps]", "(d) jitter (CFD50 slope)", 10, 60),
                                                      ("sigma_core_ps", "sigma_t, leading pixel (linear CFD50) [ps]", "(e) sigma_t of the leading pixel", 20, 80),
                                                      ("toa_mu_ps", "ToA of the leading pixel [ps]", "(f) arrival time (propagation)", None, None))):
        v = r.take(key, lc)
        v[~(r.take("hit_frac", lc) >= pc.HIT_FRAC_MIN)] = np.nan
        if key == "toa_mu_ps":
            v = v - np.nanpercentile(v[r.ok], 1)
        _map(ax, g, v, lab, pads, lo, hi)
        pc.panel_label(ax, t)
    pc.savefig(fig, outdir, "fig2_pixel_timing.png")
    near = r.ok & np.isfinite(ratio)
    for key, tag in (("amp_lead_mpv_mV", "amp_lead"), ("amp_tot_mpv_mV", "amp_total")):
        m_, e_, _ = pc.st.median_sem(r.ev(key)[near])
        ns.set(f"px_{tag}_median_mV", m_, e_, "mV", "median over cells")
    for i, (x, y) in enumerate(pads):
        ns.set(f"px_pad{i}_x_um", x, unit="um")
        ns.set(f"px_pad{i}_y_um", y, unit="um")
    return pads


def run_template_analysis(scan, workers: int, force: bool = False):
    """Run macro\\template_timing\\tt_pixel.py as a subprocess (progress is streamed through)."""
    import template_pipeline
    return template_pipeline.run_pixel(scan, workers, force)


def run(scan, red, outdir: Path, opts=None):
    opts = opts or {}
    r = pc.Red(red, opts.get("x_off", 0.0), opts.get("y_off", 0.0), PITCH_UM)
    r.body = lambda: np.ones(r.npt, bool)
    g = pc.Grid(r.X, r.Y)
    ns = pc.Numbers()
    print(f"  pixel: grid {g.xc.size} x {g.yc.size}, step {np.median(np.diff(g.xc)):.0f} um; channels {','.join(r.chs)}", flush=True)
    quick_figures(r, g, outdir, ns)
    pc.write_summary_csv(r, outdir)
    header = [f"scan: {scan.name}", "mode: pixel (2x2 read-out, pad 100 um, pitch 500 um)", f"points: {r.npt}",
              f"channels: {','.join(r.chs)}",
              "quick figures from the simple reduction; the template-fit analysis (tt_pixel.py) writes pix01-06_*.png and pixel\\"]
    ns.save(outdir, header)
    if not opts.get("no_template", False):
        run_template_analysis(scan, int(opts.get("workers", 8)), bool(opts.get("force_template", False)))
    return ns
