# -*- coding: utf-8 -*-
"""Quick-look analysis of a tune scan (fixed position, sweep of the laser intensity TUNE).

Finds the amplitude-dependent relations used in the "matome" analysis note (1/A law of the jitter, σ_t vs amplitude, intensity for a MIP-like signal).
The position does not move, so the position-dependent items (position resolution, propagation) are not covered.

Outputs (results\\<scan name>\\):
  fig1_amplitude_charge.png  amplitude (per channel, A_lead, A_lead+A_sub) and charge vs TUNE, TUNE for a MIP-like signal (pulse height 60–75 mV)
  fig2_rise_time.png         rise time 10–90 % / 40–60 % vs amplitude (check whether the pulse shape changes with intensity)
  fig3_jitter.png            jitter (3 definitions) vs amplitude and the 1/A-law fit σ_jit = p0/A + p1
  fig5_time_resolution.png   single-strip σ_t vs amplitude, the reference 1/A law ⊕ floor, and the fit σ² = (p0/A)² + c²
  fig8_waveforms_vs_tune.png mean waveform for each TUNE (main channel)
  fig_waveforms.png / summary.csv / summary.txt / numbers.json

Standalone: python analyze_tune.py <scan_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_common as pc  # noqa: E402
import stats as st  # noqa: E402
from analyze_1dy import jitter_vs_amp, sigma_vs_amp  # noqa: E402
from plot_common import plt  # noqa: E402

MIP_BAND_MV = (60.0, 75.0)      # pulse height next to the strip regarded as "MIP-like" (range of the 3 matome scans)
TL = "Laser TUNE [%]"


def tune_at_amplitude(tune, amp, target):
    """Find the TUNE where the amplitude equals target by linear interpolation (first crossing if not monotonic)."""
    o = np.argsort(tune)
    t, a = tune[o], amp[o]
    ok = np.isfinite(a)
    t, a = t[ok], a[ok]
    for i in range(len(t) - 1):
        if (a[i] - target) * (a[i + 1] - target) <= 0 and a[i] != a[i + 1]:
            return float(t[i] + (target - a[i]) * (t[i + 1] - t[i]) / (a[i + 1] - a[i]))
    return np.nan


def waveforms_vs_tune(r, rows, outdir, name="fig8_waveforms_vs_tune.png"):
    """Overlay the mean waveforms of the main channel at the given points (TUNE sweep), colored by TUNE."""
    lc = r.lead_channel()
    tunes = r.tune[rows]
    if not np.isfinite(tunes).any():
        return
    cmap = plt.get_cmap("viridis")
    tmin, tmax = np.nanmin(tunes), np.nanmax(tunes)
    t_ns = np.asarray(r.npz["t_ns"])
    waves = {ch: np.asarray(r.npz[f"w_{ch}"]) for ch in r.chs}
    nb = max(int(round(0.2 * t_ns.size)), 4)
    fig, ax = plt.subplots(figsize=(8.5, 5.0))
    for i in rows:
        ch = r.chs[lc[i]] if lc[i] >= 0 else r.chs[0]
        wf = waves[ch][i]
        if not np.isfinite(wf).any():
            continue
        c = cmap(0.5 if tmax == tmin else (r.tune[i] - tmin) / (tmax - tmin))
        ax.plot(t_ns, r.polarity * (wf - np.nanmean(wf[:nb])) * 1e3, lw=1.0, color=c)
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(tmin, tmax))
    fig.colorbar(sm, ax=ax, pad=0.02).set_label(TL)
    ax.set_xlabel("Time [ns]")
    ax.set_ylabel("Mean waveform - baseline [mV]")
    pc.panel_label(ax, "mean waveform of the leading strip for each TUNE")
    pc.savefig(fig, outdir, name)


def rise_vs_amp(axes, r, ns):
    amp = r.q("amp_mpv_mV")
    for ax, tag, lab in ((axes[0], "1090", "10-90%"), (axes[1], "4060", "40-60%")):
        v, ve = r.q(f"rise{tag}_mpv_ps"), r.q(f"rise{tag}_err_ps")
        for c, ch in enumerate(r.chs):
            m = (r.q("hit_frac")[:, c] >= pc.HIT_FRAC_MIN) & np.isfinite(v[:, c])
            pc.eb(ax, amp[m, c], v[m, c], ve[m, c], color=pc.ch_color(ch), label=ch)
        ax.set_xlabel("Amplitude MPV [mV]")
        ax.set_ylabel(f"Rise time {lab} MPV [ps]")
        pc.legend_room(ax, ncol=4)
        pc.panel_label(ax, f"rise time {lab} vs amplitude")


def run(scan, red, outdir: Path, opts=None):
    opts = opts or {}
    r = pc.Red(red, opts.get("x_off", 0.0), opts.get("y_off", 0.0), opts.get("pitch", 500.0))
    ns = pc.Numbers()
    o = np.argsort(r.tune)
    t = r.tune[o]
    amp, ae = r.q("amp_mpv_mV"), r.q("amp_mpv_err_mV")
    cm = int(np.nanargmax(np.nanmax(amp, axis=0)))
    chm = r.chs[cm]

    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.2))
    ax = axes[0]
    for c, ch in enumerate(r.chs):
        pc.eb(ax, t, amp[o, c], ae[o, c], color=pc.ch_color(ch), label=ch)
    pc.eb(ax, t, r.ev("amp_ls_mpv_mV")[o], r.ev("amp_ls_err_mV")[o], color=pc.C_GRAY, marker="s", mfc="none",
          label="A_lead + A_sub")
    ax.axhspan(*MIP_BAND_MV, color=pc.C_GREEN, alpha=0.15, lw=0, label=f"MIP-like amplitude {MIP_BAND_MV[0]:.0f}-{MIP_BAND_MV[1]:.0f} mV")
    for target in (MIP_BAND_MV[0], 70.0, MIP_BAND_MV[1]):
        tv = tune_at_amplitude(r.tune, amp[:, cm], target)
        ns.set(f"tune_for_{target:.0f}mV_pct", tv, unit="%", note=f"TUNE where the amplitude MPV of {chm} is {target:.0f} mV (linear interpolation)")
    ns.set("tune_amp_max_mV", np.nanmax(amp[:, cm]), unit="mV", note=f"largest amplitude MPV of {chm} in the scan")
    ax.set_xlabel(TL)
    ax.set_ylabel("Amplitude MPV [mV]")
    ax.set_ylim(0, None)
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, "(a) amplitude vs TUNE")
    ax = axes[1]
    q, qe = r.q("q_mpv_pC"), r.q("q_mpv_err_pC")
    for c, ch in enumerate(r.chs):
        pc.eb(ax, t, q[o, c], qe[o, c], color=pc.ch_color(ch), label=ch)
    pc.eb(ax, t, r.ev("q_sum_mpv_pC")[o], r.ev("q_sum_err_pC")[o], color="k", marker="D", mfc="none", label="Q_tot (all ch)")
    ax.set_xlabel(TL)
    ax.set_ylabel("Charge MPV [pC]")
    ax.set_ylim(0, None)
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, "(b) charge vs TUNE")
    pc.savefig(fig, outdir, "fig1_amplitude_charge.png")

    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.0))
    rise_vs_amp(axes, r, ns)
    v = r.q("rise1090_mpv_ps")[:, cm]
    ok = np.isfinite(v) & (r.q("hit_frac")[:, cm] >= pc.HIT_FRAC_MIN)
    if ok.sum() >= 2:
        i_lo, i_hi = np.where(ok)[0][np.argmin(amp[ok, cm])], np.where(ok)[0][np.argmax(amp[ok, cm])]
        ns.set("tune_rise1090_change_ps", v[i_lo] - v[i_hi], unit="ps",
               note=f"rise 10-90% of {chm} at the lowest amplitude ({amp[i_lo, cm]:.0f} mV) minus at the highest ({amp[i_hi, cm]:.0f} mV)")
    pc.savefig(fig, outdir, "fig2_rise_time.png")

    fig, ax = plt.subplots(figsize=(8.5, 5.4))
    jitter_vs_amp(ax, r, ns)
    ax.set_xlim(0, None)
    pc.panel_label(ax, "jitter vs amplitude and the 1/A law")
    pc.savefig(fig, outdir, "fig3_jitter.png")

    fig, ax = plt.subplots(figsize=(8.5, 5.4))
    sigma_vs_amp(ax, r, ns)
    pc.panel_label(ax, "single-strip time resolution vs amplitude")
    pc.savefig(fig, outdir, "fig5_time_resolution.png")

    waveforms_vs_tune(r, np.arange(r.npt)[o], outdir)
    extra = [f"TUNE: {np.nanmin(r.tune):.1f}..{np.nanmax(r.tune):.1f} % at X = {np.nanmedian(r.X):.0f}, Y = {np.nanmedian(r.Y):.0f} um",
             f"main channel: {chm}"]
    pc.finish(scan, r, ns, outdir, extra)
    return ns
