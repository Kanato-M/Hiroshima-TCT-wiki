# -*- coding: utf-8 -*-
"""Quick-look analysis of a 1dZ scan (focus scan): find the best Z.

The better the spot is focused, the higher the amplitude and the lower the jitter, so the plateau of the
amplitude MPV of the leading strip is fitted with a parabola to get the best Z (a tuning analysis, separate from the analysis items of the analysis note).

Outputs (results\\<scan name>\\):
  fig1_focus.png   (a) amplitude MPV vs Z with the parabola fit (b) jitter and σ_t vs Z
  best_z.txt / fig_waveforms.png / summary.csv / summary.txt / numbers.json

Standalone: python analyze_1dz.py <scan_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_common as pc  # noqa: E402
from plot_common import plt  # noqa: E402


def run(scan, red, outdir: Path, opts=None):
    opts = opts or {}
    r = pc.Red(red, opts.get("x_off", 0.0), opts.get("y_off", 0.0), opts.get("pitch", 500.0))
    ns = pc.Numbers()
    o = np.argsort(r.Z)
    z = r.Z[o]
    la = r.lead("amp_mpv_mV", valid_only=False)[o]
    le = r.lead("amp_mpv_err_mV", valid_only=False)[o]
    ib = int(np.nanargmax(la))
    # With a flat plateau a narrow window does not give a downward-opening parabola, so widen the window until it does (0.99 → 0.90)
    z_fit, thr_used, coef = np.nan, np.nan, None
    for thr in (0.99, 0.97, 0.95, 0.92, 0.90):
        sel = (la >= thr * la[ib]) & np.isfinite(la)
        if sel.sum() < 5:
            continue
        c = np.polyfit(z[sel], la[sel], 2)
        if c[0] < 0:
            z_fit, thr_used, coef = -c[1] / (2 * c[0]), thr, c
            break
    fig, axes = plt.subplots(1, 2, figsize=(14.0, 5.0))
    ax = axes[0]
    pc.eb(ax, z, la, le, color="k", label="leading amplitude MPV")
    ax.axvline(z[ib], color="k", ls=":", lw=1, label=f"maximum at Z = {z[ib]:.1f} um")
    if coef is not None:
        zz = np.linspace(z[la >= thr_used * la[ib]].min(), z[la >= thr_used * la[ib]].max(), 100)
        ax.plot(zz, np.polyval(coef, zz), "r-", lw=1.3,
                label=f"parabola (points >= {thr_used:.0%} of max): Z = {z_fit:.1f} um")
        ax.axvline(z_fit, color="r", ls="--", lw=1.2)
    ax.set_xlabel("Z (focus) [um]")
    ax.set_ylabel("Leading amplitude MPV [mV]")
    pc.legend_room(ax)
    pc.panel_label(ax, "(a) focus scan")
    ax = axes[1]
    pc.eb(ax, z, r.lead("jit_4060_mpv_ps", False)[o], r.lead("jit_4060_err_ps", False)[o], color=pc.C_BLUE,
          label="jitter MPV (40-60% slope)")
    pc.eb(ax, z, r.lead("sigma_core_ps", False)[o], r.lead("sigma_core_err_ps", False)[o], color=pc.C_ORANGE, marker="s",
          label="sigma_t (TRIG OUT ref.)")
    if np.isfinite(z_fit):
        ax.axvline(z_fit, color="r", ls="--", lw=1.2)
    ax.set_xlabel("Z (focus) [um]")
    ax.set_ylabel("Leading strip [ps]")
    ax.set_ylim(0, None)
    pc.legend_room(ax)
    pc.panel_label(ax, "(b) jitter and time resolution")
    pc.savefig(fig, outdir, "fig1_focus.png")
    ns.set("focus_best_z_raw_um", z[ib], unit="um", note="Z of the maximum leading amplitude")
    ns.set("focus_best_z_fit_um", z_fit, unit="um", note="vertex of the parabola fitted to the plateau")
    ns.set("focus_amp_max_mV", la[ib], le[ib], "mV")
    (outdir / "best_z.txt").write_text(
        f"scan: {scan.name}\nbest_z_raw_um: {z[ib]:.1f}\nbest_z_fit_um: {z_fit:.1f}\n", encoding="utf-8")
    pc.finish(scan, r, ns, outdir, [f"scan axis: Z {np.nanmin(r.Z):.0f}..{np.nanmax(r.Z):.0f} um"])
    return ns
