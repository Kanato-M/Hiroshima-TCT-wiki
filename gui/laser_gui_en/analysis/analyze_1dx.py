# -*- coding: utf-8 -*-
"""Quick-look analysis of a 1dX scan (X scan along the strip, fixed Y).

Of the analysis items of the "matome" analysis note, this studies the dependence along the strip with a single scan line.
The position resolution and the transverse propagation (v_eff) need a scan in Y, so get them from 1dY or 2dYX.

Outputs (results\\<scan name>\\):
  fig1_amplitude_charge.png  item 1: amplitude (per channel, A_lead, A_lead+A_sub, A_total for reference) and charge vs X, slope along the strip
  fig2_rise_time.png         item 2: rise time 10–90 % / 40–60 % vs X, slope
  fig3_jitter.png            item 3: 2D of X × per-event jitter + MPV, jitter of the 3 definitions vs X
  fig5_time_resolution.png   item 5: single-strip σ and σ_lead vs X, amplitude dependence
  fig6_propagation.png       item 6: ToA vs X with the Elmore fit, residuals, apparent velocity
  fig_waveforms.png / summary.csv / summary.txt / numbers.json

"Body" = points inside the body_x range (default −9900..−700 µm) with hit rate ≥ 0.95. Points on the pad (no signal) drop out automatically.

Standalone: python analyze_1dx.py <scan_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_common as pc  # noqa: E402
import stats as st  # noqa: E402
from analyze_1dy import sigma_vs_amp  # noqa: E402
from plot_common import plt  # noqa: E402

XL = "X along strip [um]"


def _body_lines(ax, r):
    for x in r.body_x:
        if ax.get_xlim()[0] < x < ax.get_xlim()[1]:
            ax.axvline(x, color=pc.C_GRAY, ls=":", lw=0.9)


def _slope(ax, x, y, e, sel, ns, key, unit, note, color="r", rel=False):
    """Line fit in the body. With rel=True, the relative change divided by the value at the middle [%/mm] (−X = toward the far end is positive)."""
    m = sel & np.isfinite(y) & np.isfinite(e) & (e > 0)
    if m.sum() < 4:
        return
    a, ea, b, eb_, chi2 = st.wlinfit(x[m] / 1e3, y[m], e[m])
    xx = np.array([x[m].min(), x[m].max()])
    ax.plot(xx, a + b * xx / 1e3, color=color, lw=1.2)
    if rel:
        a0 = a + b * float(np.mean(x[m])) / 1e3
        ns.set(key, -100.0 * b / a0, 100.0 * eb_ / a0, unit, note)
    else:
        ns.set(key, b, eb_, unit, note)


def run(scan, red, outdir: Path, opts=None):
    opts = opts or {}
    r = pc.Red(red, opts.get("x_off", 0.0), opts.get("y_off", 0.0), opts.get("pitch", 500.0),
               opts.get("body_x", (-9900.0, -700.0)))
    ns = pc.Numbers()
    o = np.argsort(r.X)
    amp, ae, hf = r.q("amp_mpv_mV"), r.q("amp_mpv_err_mV"), r.q("hit_frac")
    # Main channel = the channel with the largest amplitude over the whole scan (the strip the laser runs next to)
    cm = int(np.nanargmax(np.nanmedian(amp, axis=0)))
    chm = r.chs[cm]
    valid = hf[:, cm] >= pc.HIT_FRAC_MIN
    body = r.body() & valid
    print(f"  main channel {chm}; body points {int(body.sum())} / {r.npt}")

    # ---------------- fig1: amplitude and charge ----------------
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.2))
    ax = axes[0]
    for c, ch in enumerate(r.chs):
        pc.eb(ax, r.X[o], amp[o, c], ae[o, c], color=pc.ch_color(ch), ms=2.5, label=ch)
    for key, ek, col, mk, lab in (("amp_ls_mpv_mV", "amp_ls_err_mV", pc.C_GRAY, "s", "A_lead + A_sub"),
                                  ("amp_tot_mpv_mV", "amp_tot_err_mV", pc.C_PINK, "^", "A_total (reference)")):
        pc.eb(ax, r.X[o], r.ev(key)[o], r.ev(ek)[o], color=col, marker=mk, ms=2.5, mfc="none", label=lab)
    _slope(ax, r.X, amp[:, cm], ae[:, cm], body, ns, f"a1_amp_slope_{chm}_pct_per_mm", "%/mm",
           "relative change toward -X (far end), linear fit in the body", rel=True)
    m_, e_, _ = st.median_sem(amp[body, cm])
    ns.set(f"a1_amp_{chm}_body_mV", m_, e_, "mV", "median over body points")
    for name, key in (("amp_lead", "amp_lead_mpv_mV"), ("amp_ls", "amp_ls_mpv_mV"), ("amp_tot", "amp_tot_mpv_mV")):
        m_, e_, _ = st.median_sem(r.ev(key)[body])
        ns.set(f"a1_{name}_body_mV", m_, e_, "mV", "median over body points")
    ax.set_xlabel(XL)
    ax.set_ylabel("Amplitude MPV [mV]")
    ax.set_ylim(0, None)
    _body_lines(ax, r)
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, f"(a) amplitude (red line: linear fit of {chm} in the body)")
    ax = axes[1]
    q, qe = r.q("q_mpv_pC"), r.q("q_mpv_err_pC")
    for c, ch in enumerate(r.chs):
        pc.eb(ax, r.X[o], q[o, c], qe[o, c], color=pc.ch_color(ch), ms=2.5, label=ch)
    pc.eb(ax, r.X[o], r.ev("q_sum_mpv_pC")[o], r.ev("q_sum_err_pC")[o], color="k", marker="D", ms=2.5, mfc="none",
          label="Q_tot (all ch)")
    _slope(ax, r.X, r.ev("q_sum_mpv_pC"), r.ev("q_sum_err_pC"), body, ns, "a1_q_tot_slope_pct_per_mm", "%/mm",
           "relative change toward -X (far end)", rel=True)
    m_, e_, _ = st.median_sem(r.ev("q_sum_mpv_pC")[body])
    ns.set("a1_q_tot_body_pC", m_, e_, "pC", "median over body points")
    ax.set_xlabel(XL)
    ax.set_ylabel("Charge MPV [pC]")
    ax.set_ylim(0, None)
    _body_lines(ax, r)
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, "(b) gate-integrated charge")
    pc.savefig(fig, outdir, "fig1_amplitude_charge.png")

    # ---------------- fig2: rise time ----------------
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.0))
    for ax, tag, lab in ((axes[0], "1090", "10-90%"), (axes[1], "4060", "40-60%")):
        v, ve = r.q(f"rise{tag}_mpv_ps")[:, cm], r.q(f"rise{tag}_err_ps")[:, cm]
        pc.eb(ax, r.X[valid], v[valid], ve[valid], color=pc.ch_color(chm), ms=2.5, label=chm)
        _slope(ax, r.X, v, ve, body, ns, f"a2_rise{tag}_slope_ps_per_mm", "ps/mm",
               "d(rise)/dX (positive = longer toward the read-out end)")
        m_, e_, _ = st.median_sem(v[body])
        ns.set(f"a2_rise{tag}_band_ps", m_, e_, "ps", "median over body points")
        ax.set_xlabel(XL)
        ax.set_ylabel(f"Rise time {lab} MPV [ps]")
        lo, hi = pc.finite_range(v[body], 1, 99, pad=0.6)
        ax.set_ylim(lo, hi)
        _body_lines(ax, r)
        pc.legend_room(ax)
        pc.panel_label(ax, f"rise time {lab} (red line: linear fit in the body)")
    pc.savefig(fig, outdir, "fig2_rise_time.png")

    # ---------------- fig3: jitter ----------------
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.2))
    ax = axes[0]
    pc.events_vs_pos(ax, r.X, r.jit_lead_hist[:, 0], r.jit_edges, "Event jitter (40-60% slope) [ps]", XL, mask=r.ok)
    ax.set_ylim(0, 100)
    pc.legend_on_map(ax, loc="upper right")
    pc.panel_label(ax, "(a) per-event jitter of the leading strip vs X")
    ax = axes[1]
    for tag, lab, col, mk in (("4060", "40-60% slope", pc.C_BLUE, "o"), ("1090", "10-90% slope", pc.C_YELLOW, "s"),
                              ("cfd50", "CFD50 local slope", "k", "D")):
        v, ve = r.q(f"jit_{tag}_mpv_ps")[:, cm], r.q(f"jit_{tag}_err_ps")[:, cm]
        pc.eb(ax, r.X[valid], v[valid], ve[valid], color=col, marker=mk, ms=2.5, label=lab)
        m_, e_, _ = st.median_sem(v[body])
        ns.set(f"a3_jit{tag}_band_ps", m_, e_, "ps", "median over body points")
    ax.set_xlabel(XL)
    ax.set_ylabel(f"Jitter MPV of {chm} [ps]")
    ax.set_ylim(0, 60)
    _body_lines(ax, r)
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, "(b) jitter for the three slope definitions")
    pc.savefig(fig, outdir, "fig3_jitter.png")

    # ---------------- fig5: time resolution ----------------
    sref = pc.SIGMA_REF_PS
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.2))
    ax = axes[0]
    sc, sce = r.q("sigma_core_ps"), r.q("sigma_core_err_ps")
    for c, ch in enumerate(r.chs):
        m = (hf[:, c] >= pc.HIT_FRAC_MIN) & (amp[:, c] >= 10.0)
        pc.eb(ax, r.X[m], sc[m, c], sce[m, c], color=pc.ch_color(ch), ms=2.5, label=ch)
    sl = np.where(valid, r.ev("sigma_lead_ps"), np.nan)
    pc.eb(ax, r.X, sl, r.ev("sigma_lead_err_ps"), color="k", marker="D", ms=2.5, mfc="none", label="leading strip (event by event)")
    _slope(ax, r.X, sc[:, cm], sce[:, cm], body, ns, "a5_sigma_band_slope_ps_per_mm", "ps/mm", "d(sigma)/dX in the body")
    m_, e_, _ = st.median_sem(sc[body, cm])
    ns.set("a5_sigma_band_ps", m_, e_, "ps", f"single-strip sigma_core of {chm}, median over body points, TRIG OUT reference")
    if np.isfinite(m_) and m_ > sref:
        ns.set("a5_sigma_band_floorsub_ps", np.sqrt(m_ ** 2 - sref ** 2), m_ * e_ / np.sqrt(m_ ** 2 - sref ** 2), "ps",
               f"sqrt(sigma^2 - {sref}^2)")
    ax.set_xlabel(XL)
    ax.set_ylabel("sigma_t (TRIG OUT ref.) [ps]")
    ax.set_ylim(0, 90)
    _body_lines(ax, r)
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, "(a) time resolution along the strip")
    sigma_vs_amp(axes[1], r, ns)
    pc.panel_label(axes[1], "(b) time resolution vs amplitude")
    pc.savefig(fig, outdir, "fig5_time_resolution.png")

    # ---------------- fig6: propagation (Elmore, d = −X) ----------------
    toa, toa_e, ntime = r.q("toa_mu_ps")[:, cm], r.q("toa_mu_err_ps")[:, cm], r.q("n_time")[:, cm]
    good = valid & (ntime >= 500) & np.isfinite(toa)
    fb = good & r.body()
    fit = st.fit_elmore(-r.X[fb] / 1e3, toa[fb], np.maximum(toa_e[fb], 0.5)) if fb.sum() >= 5 else None
    t0 = fit["t0"] if fit else (np.nanmin(toa[good]) if good.any() else 0.0)
    fig, axes = plt.subplots(1, 3, figsize=(19.0, 5.2))
    ax = axes[0]
    pc.eb(ax, r.X[good], toa[good] - t0, toa_e[good], color=pc.ch_color(chm), ms=2.5, label=f"{chm} (CFD 50%)")
    if fit:
        d = np.linspace(-r.body_x[1] / 1e3 if (~r.body()).any() else -np.nanmax(r.X[fb]) / 1e3,
                        -np.nanmin(r.X[fb]) / 1e3, 300)
        ax.plot(-d * 1e3, st.elmore_toa(d, fit["rho_c"], fit["L"], 0.0), "r-", lw=1.3,
                label=f"Elmore: rho*c = {fit['rho_c']:.2f} +- {fit['rho_c_err']:.2f} ps/mm^2, L = {fit['L']:.2f} mm")
        for qk, unit in (("rho_c", "ps/mm^2"), ("L", "mm")):
            ns.set(f"a6_elmore_{qk}_{chm}", fit[qk], fit[f"{qk}_err"], unit, "Elmore fit t0 + rho_c (L d - d^2/2), d = -X, body")
        ns.set(f"a6_elmore_chi2ndf_{chm}", fit["chi2_ndf"])
        d_lo, d_hi = -np.nanmax(r.X[fb]) / 1e3, -np.nanmin(r.X[fb]) / 1e3
        ns.set("a6_long_delay_ps", st.elmore_toa(d_hi, fit["rho_c"], fit["L"], 0.0) - st.elmore_toa(d_lo, fit["rho_c"], fit["L"], 0.0),
               unit="ps", note=f"Elmore-fit delay between X = {-d_lo * 1e3:.0f} and {-d_hi * 1e3:.0f} um")
    ax.set_xlabel(XL)
    ax.set_ylabel("ToA - t0 [ps]")
    _body_lines(ax, r)
    pc.legend_room(ax)
    pc.panel_label(ax, "(a) arrival time along the strip")
    ax = axes[1]
    if fit:
        pc.eb(ax, r.X[good], toa[good] - st.elmore_toa(-r.X[good] / 1e3, fit["rho_c"], fit["L"], fit["t0"]), toa_e[good],
              color=pc.ch_color(chm), ms=2.5, label=f"{chm}: residual RMS in the body = {fit['resid_rms_ps']:.1f} ps")
    ax.axhline(0, color=pc.C_GRAY, ls=":", lw=0.9)
    ax.set_xlabel(XL)
    ax.set_ylabel("ToA - Elmore fit [ps]")
    ax.set_ylim(-40, 60)
    _body_lines(ax, r)
    pc.legend_room(ax)
    pc.panel_label(ax, "(b) residuals")
    ax = axes[2]
    if fit:
        d = np.linspace(0.3, min(fit["L"] - 0.3, -np.nanmin(r.X[fb]) / 1e3), 200)
        ax.plot(-d * 1e3, 1e3 / (fit["rho_c"] * (fit["L"] - d)), "r-", lw=1.4, label="1 / (rho*c (L - d)) from the fit")
    # Velocity from the local slope (line fit in a 2 mm window)
    xs, vs, es = [], [], []
    if good.any():
        for x0 in np.arange(np.ceil(np.nanmin(r.X[good]) / 1e3) * 1e3 + 1000.0, np.nanmax(r.X[good]) - 999.0, 1000.0):
            m = good & (np.abs(r.X - x0) <= 1000.0)
            if m.sum() >= 3:
                a, ea, b, eb_, chi2 = st.wlinfit(r.X[m], toa[m], np.maximum(toa_e[m], 0.2))
                if b < 0 and eb_ < 0.5 * abs(b):      # in a window where the slope is 0 within errors (near the far end) the velocity is not defined
                    xs.append(x0)
                    vs.append(-1.0 / b)
                    es.append(eb_ / b ** 2)
    pc.eb(ax, xs, vs, es, color=pc.C_BLUE, label="local slope (2 mm windows)")
    ax.set_yscale("log")
    ax.set_ylim(20, 2000)
    ax.set_xlabel(XL)
    ax.set_ylabel("Apparent longitudinal velocity [um/ps]")
    pc.legend_room(ax)
    pc.panel_label(ax, "(c) apparent velocity -1/(dToA/dX)")
    pc.savefig(fig, outdir, "fig6_propagation.png")

    extra = [f"scan axis: X {np.nanmin(r.X):.0f}..{np.nanmax(r.X):.0f} um at Y = {np.nanmedian(r.Y):.0f} um",
             f"main channel: {chm}; body = X in [{r.body_x[0]:.0f}, {r.body_x[1]:.0f}] um with hit fraction >= {pc.HIT_FRAC_MIN}",
             "note: position resolution and transverse velocity need a Y scan (1dY or 2dYX)"]
    pc.finish(scan, r, ns, outdir, extra)
    return ns
