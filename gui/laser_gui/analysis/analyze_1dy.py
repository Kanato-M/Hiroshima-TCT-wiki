# -*- coding: utf-8 -*-
"""1dY スキャン (ストリップを横切る Y 走査、X 固定) の簡易解析。

解析ノート matome の解析項目のうち、横方向 (ギャップ内) の依存を 1 本の走査で調べる。
長さ方向の依存 (Elmore 遅延など) は 1dX か 2dYX が必要。

出力 (results\\<スキャン名>\\):
  fig1_amplitude_charge.png  項目 1: ch 別振幅・A_lead・A_lead+A_sub・A_total (参考)・電荷 対 Y
  fig2_rise_time.png         項目 2: 立ち上がり 10–90 % / 40–60 % 対 Y
  fig3_jitter.png            項目 3: Y × イベント毎ジッターの 2D + MPV (2 定義)、振幅依存と 1/A 則
  fig4_position.png          項目 4: η 較正曲線と実効ピッチ、位置分解能 (偶奇分割 test / in-situ / ノイズ期待値)
  fig5_time_resolution.png   項目 5: 単ストリップ σ、σ_lead・σ_Δt/√2・σ_2strip 対 Y、振幅依存、床の自己推定
  fig6_7_propagation.png     項目 6, 7: ToA 対 Y、ΔT(u) と v_eff、単一 ch の遅延と振幅の関係
  fig_waveforms.png / summary.csv / summary.txt / numbers.json

単体実行: python analyze_1dy.py <scan_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_common as pc  # noqa: E402
import position as pos  # noqa: E402
import stats as st  # noqa: E402
from plot_common import plt  # noqa: E402

N_PAIR_MIN = 200
YL = "Y across strips [um]"


def _centers(ax, cen0):
    for y in cen0:
        ax.axvline(y, color=pc.C_GRAY, ls=":", lw=0.9)


def gap_sets(r, f):
    """ストリップ脇 (中心から 25–75 µm) とギャップ中央 (±20 µm) の点。"""
    g = (f["gap"] >= 0) & (r.q("hit_frac").max(axis=1) >= pc.HIT_FRAC_MIN) & np.isfinite(r.ev("amp_lead_mpv_mV"))
    side = g & (((f["u"] > 25) & (f["u"] < 75)) | ((f["u"] > f["width"] - 75) & (f["u"] < f["width"] - 25)))
    mid = g & (np.abs(f["u"] - f["width"] / 2) < 20.0)
    return g, side, mid


def fig_amplitude(r, f, ns, outdir, cen0):
    o = np.argsort(r.Y)
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.2))
    ax = axes[0]
    amp, ae = r.q("amp_mpv_mV"), r.q("amp_mpv_err_mV")
    for c, ch in enumerate(r.chs):
        pc.eb(ax, r.Y[o], amp[o, c], ae[o, c], color=pc.ch_color(ch), label=ch)
    for key, ek, col, mk, lab in (("amp_lead_mpv_mV", "amp_lead_err_mV", "k", "D", "A_lead"),
                                  ("amp_ls_mpv_mV", "amp_ls_err_mV", pc.C_GRAY, "s", "A_lead + A_sub"),
                                  ("amp_tot_mpv_mV", "amp_tot_err_mV", pc.C_PINK, "^", "A_total (reference)")):
        pc.eb(ax, r.Y[o], r.ev(key)[o], r.ev(ek)[o], color=col, marker=mk, mfc="none", label=lab)
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("Amplitude MPV [mV]")
    ax.set_ylim(0, None)
    pc.legend_room(ax, ncol=4)
    pc.panel_label(ax, "(a) amplitude (A_total = sum of all channels, includes a no-signal bias)")
    ax = axes[1]
    q, qe = r.q("q_mpv_pC"), r.q("q_mpv_err_pC")
    for c, ch in enumerate(r.chs):
        pc.eb(ax, r.Y[o], q[o, c], qe[o, c], color=pc.ch_color(ch), label=ch)
    pc.eb(ax, r.Y[o], r.ev("q_sum_mpv_pC")[o], r.ev("q_sum_err_pC")[o], color="k", marker="D", mfc="none",
          label="Q_tot (all ch)")
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("Charge MPV [pC]")
    ax.set_ylim(0, None)
    pc.legend_room(ax, ncol=5)
    pc.panel_label(ax, "(b) gate-integrated charge")
    g, side, mid = gap_sets(r, f)
    for name, key, unit in (("amp_lead", "amp_lead_mpv_mV", "mV"), ("amp_sub", "amp_sub_mpv_mV", "mV"),
                            ("amp_ls", "amp_ls_mpv_mV", "mV"), ("amp_tot", "amp_tot_mpv_mV", "mV"),
                            ("amp_4th", "amp_4th_mpv_mV", "mV"), ("q_tot", "q_sum_mpv_pC", "pC")):
        for tag, sel in (("side", side), ("gapcenter", mid)):
            m_, e_, _ = st.median_sem(r.ev(key)[sel])
            ns.set(f"a1_{name}_{tag}", m_, e_, unit, "median over scan points of the per-point MPV")
    for name in ("amp_ls", "amp_tot", "q_tot"):
        ns.set(f"a1_{name}_center_over_side_pct",
               100.0 * (ns.get(f"a1_{name}_gapcenter") / ns.get(f"a1_{name}_side") - 1.0), unit="%")
    pc.savefig(fig, outdir, "fig1_amplitude_charge.png")


def _valid(r, c):
    return (r.q("hit_frac")[:, c] >= pc.HIT_FRAC_MIN) & (r.q("amp_mpv_mV")[:, c] >= 10.0)


def fig_rise(r, f, ns, outdir, cen0):
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.0))
    for ax, tag, lab in ((axes[0], "1090", "10-90%"), (axes[1], "4060", "40-60%")):
        v, ve = r.q(f"rise{tag}_mpv_ps"), r.q(f"rise{tag}_err_ps")
        for c, ch in enumerate(r.chs):
            m = _valid(r, c)
            o = np.argsort(r.Y[m])
            pc.eb(ax, r.Y[m][o], v[m, c][o], ve[m, c][o], color=pc.ch_color(ch), label=ch)
        _centers(ax, cen0)
        ax.set_xlabel(YL)
        ax.set_ylabel(f"Rise time {lab} MPV [ps]")
        pc.legend_room(ax, ncol=4)
        pc.panel_label(ax, f"rise time {lab} (points with hit fraction >= 0.95)")
        vals = np.concatenate([v[pc.band_mask(r, f, c), c] for c in range(r.nch)])
        m_, e_, _ = st.median_sem(vals)
        ns.set(f"a2_rise{tag}_band_ps", m_, e_, "ps", "points 25-75 um beside the strips")
        mid = (f["gap"] >= 0) & (np.abs(f["u"] - f["width"] / 2) < 20.0)
        m_, e_, _ = st.median_sem(r.lead(f"rise{tag}_mpv_ps")[mid])
        ns.set(f"a2_rise{tag}_gapcenter_ps", m_, e_, "ps", "leading strip at the gap centers")
    pc.savefig(fig, outdir, "fig2_rise_time.png")


def jitter_vs_amp(ax, r, ns, a_min_fit=20.0):
    """ジッター対振幅 (全点・全 ch) と 1/A 則 σ_jit = p0/A + p1。1dY / tune 系で共通。

    フィットは matome の参照値 (781/A + 5.4 ps、1dYtune) を求めたときと同じ手順:
    ch ごとに A ≥ 20 mV の点を 1/A について重みなしで直線フィットし、ch で平均する
    (元の実装: analysis/src/aclgad_tct/phase7.py)。誤差は ch 間のばらつき/√n_ch。
    """
    amp = r.q("amp_mpv_mV")
    for tag, lab, col, mk in (("4060", "40-60% slope", pc.C_BLUE, "o"), ("1090", "10-90% slope", pc.C_YELLOW, "s"),
                              ("cfd50", "CFD50 local slope", "k", "D")):
        v, ve = r.q(f"jit_{tag}_mpv_ps"), r.q(f"jit_{tag}_err_ps")
        good = np.stack([_valid(r, c) for c in range(r.nch)], axis=1) & np.isfinite(v)
        pc.eb(ax, amp[good], v[good], ve[good], color=col, marker=mk, ms=2.5, label=lab)
        fits = []
        for c in range(r.nch):
            m = good[:, c] & (amp[:, c] >= a_min_fit)
            if m.sum() >= 4 and np.ptp(1.0 / amp[m, c]) > 0:
                fits.append(np.polyfit(1.0 / amp[m, c], v[m, c], 1))
        if not fits:
            continue
        fits = np.array(fits)
        p0, p1 = fits.mean(axis=0)
        e0, e1 = (fits.std(axis=0, ddof=1) / np.sqrt(len(fits))) if len(fits) > 1 else (np.nan, np.nan)
        ns.set(f"a3_jit{tag}_law_p0_psmV", p0, e0, "ps mV",
               f"sigma_jit = p0/A + p1: unweighted fit per channel for A >= {a_min_fit:.0f} mV, mean of {len(fits)} ch")
        ns.set(f"a3_jit{tag}_law_p1_ps", p1, e1, "ps")
        if tag == "cfd50":
            aa = np.linspace(max(np.nanmin(amp[good]), 8.0), np.nanmax(amp[good]), 200)
            ax.plot(aa, p0 / aa + p1, "k-", lw=1.0, label=f"fit (CFD50 slope): {p0:.0f}/A + {p1:.1f} ps")
    aa = np.linspace(10, 95, 200)
    ax.plot(aa, pc.JITTER_LAW[0] / aa + pc.JITTER_LAW[1], "r-", lw=1.4,
            label=f"reference 1/A law: {pc.JITTER_LAW[0]:.0f}/A + {pc.JITTER_LAW[1]:.2f} ps")
    ax.set_xlabel("Amplitude MPV [mV]")
    ax.set_ylabel("Jitter MPV [ps]")
    ax.set_ylim(0, 110)
    pc.legend_room(ax, ncol=2)


def fig_jitter(r, f, ns, outdir, cen0):
    fig, axes = plt.subplots(1, 3, figsize=(19.0, 5.2))
    for j, (ax, tag, lab) in enumerate(((axes[0], "4060", "40-60% slope"), (axes[1], "1090", "10-90% slope"))):
        yc, mu, er = pc.events_vs_pos(ax, r.Y, r.jit_lead_hist[:, j], r.jit_edges, f"Event jitter ({lab}) [ps]",
                                      YL, mask=r.ok, centers=cen0)
        ax.set_ylim(0, 100)
        pc.legend_on_map(ax, loc="upper right")
        pc.panel_label(ax, f"per-event jitter of the leading strip, {lab}")
        lo, hi = pc.row_range(yc, mu, cen0)
        ns.set(f"a3_jit{tag}_rowmpv_lo_ps", lo, unit="ps", note="5th percentile of the per-point MPV between the strips")
        ns.set(f"a3_jit{tag}_rowmpv_hi_ps", hi, unit="ps", note="95th percentile")
    for tag in ("4060", "1090", "cfd50"):
        v = r.q(f"jit_{tag}_mpv_ps")
        m_, e_, _ = st.median_sem(np.concatenate([v[pc.band_mask(r, f, c), c] for c in range(r.nch)]))
        ns.set(f"a3_jit{tag}_band_ps", m_, e_, "ps", "points 25-75 um beside the strips")
    jitter_vs_amp(axes[2], r, ns)
    pc.panel_label(axes[2], "jitter vs amplitude (all points, all ch)")
    pc.savefig(fig, outdir, "fig3_jitter.png")


def fig_position(r, f, ns, outdir, cen0):
    if not r.pairs:
        print("  [skip] 位置分解能: 隣り合う ch の組がありません")
        return None
    out, lines = pos.analyze(r, f)
    fig, axes = plt.subplots(2, 2, figsize=(14.0, 9.4))
    ax = axes[0, 0]
    allp = []
    for gi, pn in enumerate(r.pair_names):
        rows = np.where((f["gap"] == gi) & np.isfinite(out["eta_mean"]))[0]
        rows = rows[np.argsort(f["u"][rows])]
        ln = lines.get((0, gi), {})
        pe = ln.get("pitch_eff", np.nan)
        lab = f"{pn[:2]}-{pn[2:]}" + (f": P_eff = {pe:.0f} +- {ln['pitch_eff_err']:.0f} um" if np.isfinite(pe) else "")
        pc.eb(ax, f["u"][rows], out["eta_mean"][rows], out["eta_err"][rows], color=pc.PAIR_COLORS[gi % 4], label=lab)
        ns.set(f"a4_pitch_eff_{pn}_um", pe, ln.get("pitch_eff_err"), "um", "1/(d eta/du), linear fit for 0.2 < eta < 0.8")
        allp.append(pe)
    allp = np.array(allp)[np.isfinite(allp)]
    if allp.size:
        ns.set("a4_pitch_eff_body_um", np.median(allp), allp.std(ddof=1) if allp.size > 1 else None, "um",
               "median over gaps; error = gap-to-gap spread (std)")
    uu = np.array([0.0, r.pitch])
    ax.plot(uu, uu / r.pitch, color=pc.C_GRAY, ls="--", lw=1.0, label=f"eta = u / {r.pitch:.0f} um (nominal)")
    ax.set_xlabel("Distance from the strip-A center, u [um]")
    ax.set_ylabel("Mean eta = A_B / (A_A + A_B)")
    ax.set_xlim(0, r.pitch)
    ax.set_ylim(0, 1)
    pc.legend_room(ax)
    pc.panel_label(ax, "(a) eta calibration curves and effective pitch")
    ax = axes[0, 1]
    o = np.argsort(r.Y)
    pc.eb(ax, r.Y[o], out["sig_test"][o], out["sig_test_err"][o], color=pc.C_BLUE, label="test (calibrated on even, evaluated on odd)")
    pc.eb(ax, r.Y[o], out["sig_insitu"][o], out["sig_insitu_err"][o], color=pc.C_GRAY, marker="D", mfc="none", label="in-situ (all events)")
    pc.eb(ax, r.Y[o], out["sig_exp"][o], None, color="k", marker="x", label="noise expectation")
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("Position resolution [um]")
    ax.set_ylim(0, 40)
    pc.legend_room(ax)
    pc.panel_label(ax, "(b) position resolution (laser spot and stage included)")
    sel = (f["u"] >= 50.0) & (f["u"] <= f["width"] - 50.0)
    for key, tag in (("sig_test", "test"), ("sig_insitu", "insitu"), ("sig_exp", "expected")):
        m_, e_, _ = st.median_sem(out[key][sel])
        ns.set(f"a4_posres_{tag}_um", m_, e_, "um", "median over points 50 um inside the gaps")
    m_, e_, _ = st.median_sem(np.abs(out["bias_test"][sel]))
    ns.set("a4_posbias_abs_median_um", m_, e_, "um")
    ns.set("a4_binary_resolution_um", r.pitch / np.sqrt(12.0), unit="um", note="pitch / sqrt(12)")
    # (c) 残差分布の例 (中央のギャップ: 中央と脇)
    ax = axes[1, 0]
    gi = len(r.pairs) // 2
    rng = np.random.default_rng(1)
    if lines.get((0, gi), {}).get("lut") is not None:
        lu, le = lines[(0, gi)]["lut"]
        rows = np.where((f["gap"] == gi) & np.isfinite(out["sig_insitu"]))[0]
        for frac, col in ((0.5, pc.C_BLUE), (0.3, pc.C_ORANGE)):
            if rows.size == 0:
                continue
            i = rows[np.argmin(np.abs(f["u"][rows] - frac * r.pitch))]
            res = pos.residuals(r.eta_hist[i, gi].sum(axis=0), lu, le, f["u"][i], rng)
            cnt, edges = np.histogram(res, bins=np.arange(-80, 81, 4.0))
            cx = 0.5 * (edges[1:] + edges[:-1])
            nz = cnt > 0
            pc.eb(ax, cx[nz], cnt[nz], np.sqrt(cnt[nz]), color=col,
                  label=f"u = {f['u'][i]:.0f} um: sigma = {out['sig_insitu'][i]:.1f} +- {out['sig_insitu_err'][i]:.1f} um")
            xx = np.linspace(-80, 80, 400)
            ax.plot(xx, cnt.max() * np.exp(-0.5 * ((xx - np.median(res)) / out["sig_insitu"][i]) ** 2), color=col, lw=1.2)
    ax.set_xlabel("Reconstructed - true position [um]")
    ax.set_ylabel("Events / 4 um")
    pc.legend_room(ax)
    pc.panel_label(ax, f"(c) residuals, gap {r.pair_names[gi][:2]}-{r.pair_names[gi][2:]} (all events, in-situ)")
    ax = axes[1, 1]
    pc.eb(ax, r.Y[o], out["bias_test"][o], out["sig_test"][o] / np.sqrt(500.0), color=pc.C_BLUE, label="bias (test)")
    ax.axhline(0, color=pc.C_GRAY, ls=":", lw=0.9)
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("Position bias (test) [um]")
    ax.set_ylim(-15, 15)
    pc.legend_room(ax)
    pc.panel_label(ax, "(d) bias (center of the residual distribution)")
    pc.savefig(fig, outdir, "fig4_position.png")
    return out


def time_derived(r, f):
    lc = r.lead_channel()
    lead_ok = r.take("hit_frac", lc) >= pc.HIT_FRAC_MIN
    s_lead = np.where(lead_ok, r.ev("sigma_lead_ps"), np.nan)
    e_lead = np.where(lead_ok, r.ev("sigma_lead_err_ps"), np.nan)
    va, vb, vd, vc = (pc.gap_pair(r, f, f"var_{k}_ps2") for k in ("a", "b", "d", "cw2"))
    evd, evc = pc.gap_pair(r, f, "evar_d"), pc.gap_pair(r, f, "evar_cw2")
    with np.errstate(invalid="ignore", divide="ignore"):
        s_dt, s_2s = np.sqrt(vd / 2.0), np.sqrt(vc)
        return dict(s_lead=s_lead, e_lead=e_lead, s_dt=s_dt, e_dt=evd / (4.0 * s_dt), s_2s=s_2s,
                    e_2s=evc / (2.0 * s_2s), sref2=0.5 * (va + vb - vd),
                    jit_lead=r.take("jit_cfd50_mpv_ps", lc))


def sigma_vs_amp(ax, r, ns):
    """単ストリップ σ_t 対振幅 (全点・全 ch)、参照の 1/A 則 ⊕ 床、σ² = (p0/A)² + c² のフィット。"""
    amp, sc, sce = r.q("amp_mpv_mV"), r.q("sigma_core_ps"), r.q("sigma_core_err_ps")
    m = np.stack([_valid(r, c) for c in range(r.nch)], axis=1) & np.isfinite(sc) & (sce > 0)
    x, y, e = amp[m], sc[m], sce[m]
    pc.eb(ax, x, y, e, color=pc.C_BLUE, ms=2.5, label="single-strip sigma_t (all points, all ch)")
    aa = np.linspace(10, 95, 200)
    jl = pc.JITTER_LAW[0] / aa + pc.JITTER_LAW[1]
    ax.plot(aa, np.hypot(jl, pc.SIGMA_REF_PS), "r-", lw=1.4,
            label=f"reference: ({pc.JITTER_LAW[0]:.0f}/A + {pc.JITTER_LAW[1]:.2f}) (+) {pc.SIGMA_REF_PS} ps")
    ok = (x >= 15.0) & (y < 200.0)
    if ok.sum() >= 4:
        # σ² = c² + p0²/A²: 1/A² について直線
        c2, ec2, p2, ep2, chi2 = st.wlinfit(1.0 / x[ok] ** 2, y[ok] ** 2, 2.0 * y[ok] * e[ok])
        if c2 > 0 and p2 > 0:
            xx = np.linspace(max(x[ok].min(), 8.0), x[ok].max(), 200)
            ax.plot(xx, np.sqrt(c2 + p2 / xx ** 2), "k-", lw=1.0,
                    label=f"fit: ({np.sqrt(p2):.0f}/A) (+) {np.sqrt(c2):.1f} ps")
            ns.set("a5_sigma_law_p0_psmV", np.sqrt(p2), ep2 / (2 * np.sqrt(p2)), "ps mV", "fit sigma^2 = (p0/A)^2 + c^2, A >= 15 mV")
            ns.set("a5_sigma_law_floor_ps", np.sqrt(c2), ec2 / (2 * np.sqrt(c2)), "ps", "constant term c (includes sigma_ref)")
    ax.set_xlabel("Amplitude MPV [mV]")
    ax.set_ylabel("Single-strip sigma_t (TRIG OUT ref.) [ps]")
    ax.set_ylim(0, 130)
    pc.legend_room(ax)


def fig_time(r, f, ns, outdir, cen0):
    dv = time_derived(r, f)
    sref = pc.SIGMA_REF_PS
    fig, axes = plt.subplots(2, 2, figsize=(14.0, 9.4))
    ax = axes[0, 0]
    sc, sce = r.q("sigma_core_ps"), r.q("sigma_core_err_ps")
    for c, ch in enumerate(r.chs):
        m = _valid(r, c)
        o = np.argsort(r.Y[m])
        pc.eb(ax, r.Y[m][o], sc[m, c][o], sce[m, c][o], color=pc.ch_color(ch), label=ch)
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("Single-strip sigma_t (TRIG OUT ref.) [ps]")
    ax.set_ylim(0, 150)
    pc.legend_room(ax, ncol=4)
    pc.panel_label(ax, "(a) sigma_t of each channel")
    m_, e_, _ = st.median_sem(np.concatenate([sc[pc.band_mask(r, f, c), c] for c in range(r.nch)]))
    ns.set("a5_sigma_band_ps", m_, e_, "ps", "single-strip sigma_core, points 25-75 um beside the strips")
    if np.isfinite(m_) and m_ > sref:
        ns.set("a5_sigma_band_floorsub_ps", np.sqrt(m_ ** 2 - sref ** 2), m_ * e_ / np.sqrt(m_ ** 2 - sref ** 2), "ps",
               f"sqrt(sigma^2 - {sref}^2)")
    ax = axes[0, 1]
    o = np.argsort(r.Y)
    for key, ek, lab, col, mk in (("s_lead", "e_lead", "leading strip (TRIG ref.)", pc.C_BLUE, "o"),
                                  ("s_dt", "e_dt", "Delta t / sqrt(2)", pc.C_ORANGE, "v"),
                                  ("s_2s", "e_2s", "2-strip A^2-weighted (TRIG ref.)", pc.C_GREEN, "D"),
                                  ("jit_lead", None, "predicted jitter (leading)", "k", "x")):
        pc.eb(ax, r.Y[o], dv[key][o], None if ek is None else dv[ek][o], color=col, marker=mk, label=lab)
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("Time resolution [ps]")
    ax.set_ylim(0, 90)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, "(b) leading strip, pair difference and 2-strip weighted time")
    gm = (f["gap"] >= 0) & (f["u"] >= 30.0) & (f["u"] <= f["width"] - 30.0)
    ctr = gm & (np.abs(f["u"] - f["width"] / 2) < 30.0)
    for key, name in (("s_lead", "sigma_lead"), ("s_dt", "sigma_dt_sqrt2"), ("s_2s", "sigma_2strip")):
        for tag, sel in (("gapcenter", ctr), ("gapall", gm)):
            m_, e_, _ = st.median_sem(dv[key][sel])
            ns.set(f"a5_{name}_{tag}_ps", m_, e_, "ps", "median over scan points")
    yc = r.Y
    lo, hi = pc.row_range(yc[o], dv["s_lead"][o], cen0)
    ns.set("a5_sigma_lead_rowmpv_lo_ps", lo, unit="ps", note="5th percentile of sigma_lead between the strips")
    ns.set("a5_sigma_lead_rowmpv_hi_ps", hi, unit="ps", note="95th percentile")
    sigma_vs_amp(axes[1, 0], r, ns)
    pc.panel_label(axes[1, 0], "(c) time resolution vs amplitude")
    ax = axes[1, 1]
    with np.errstate(invalid="ignore"):
        s_ref = np.sqrt(np.where(dv["sref2"] > 0, dv["sref2"], np.nan))
    sel = (f["gap"] >= 0) & (np.abs(f["u"] - f["width"] / 2) < 100.0) & np.isfinite(dv["sref2"])
    pc.eb(ax, r.Y[o], s_ref[o], None, color=pc.C_BLUE, label="per point: sqrt((VarA + VarB - VarDelta)/2)")
    m2_, e2_, n = st.median_sem(dv["sref2"][sel])
    if np.isfinite(m2_) and m2_ > 0:
        ns.set("a5_sigma_ref_self_ps", np.sqrt(m2_), e2_ / (2.0 * np.sqrt(m2_)), "ps",
               "sqrt(median of (VarA + VarB - VarDelta)/2), points within 100 um of the gap centers")
        ax.axhline(np.sqrt(m2_), color=pc.C_BLUE, lw=1.0, label=f"sqrt(median variance) = {np.sqrt(m2_):.1f} ps")
    ax.axhline(sref, color="r", ls="--", lw=1.2, label=f"reference sigma_ref = {sref} ps")
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("Self-estimated common floor [ps]")
    ax.set_ylim(0, 40)
    pc.legend_room(ax)
    pc.panel_label(ax, "(d) self-estimate of the common time floor")
    pc.savefig(fig, outdir, "fig5_time_resolution.png")


def fig_propagation(r, f, ns, outdir, cen0):
    toa, toa_e, ntime, amp = r.q("toa_mu_ps"), r.q("toa_mu_err_ps"), r.q("n_time"), r.q("amp_mpv_mV")
    fig, axes = plt.subplots(2, 2, figsize=(14.0, 9.4))
    # 各 ch の基準 = 自ストリップ脇の ToA (逆分散重み平均)
    ref = np.full(r.nch, np.nan)
    for c in range(r.nch):
        m = pc.band_mask(r, f, c) & (ntime[:, c] >= 500)
        ref[c] = st.wmean(toa[m, c], toa_e[m, c])[0]
    ax = axes[0, 0]
    dist, dly, dle, am = [], [], [], []
    for c, ch in enumerate(r.chs):
        m = _valid(r, c) & (ntime[:, c] >= 200) & np.isfinite(ref[c])
        o = np.argsort(r.Y[m])
        pc.eb(ax, r.Y[m][o], (toa[m, c] - ref[c])[o], toa_e[m, c][o], color=pc.ch_color(ch), label=ch)
        dist.append(np.abs(f["dy"][m, c]))
        dly.append(toa[m, c] - ref[c])
        dle.append(toa_e[m, c])
        am.append(amp[m, c])
    dist, dly, dle, am = (np.concatenate(a) if a else np.array([]) for a in (dist, dly, dle, am))
    _centers(ax, cen0)
    ax.set_xlabel(YL)
    ax.set_ylabel("ToA - ToA beside the own strip [ps]")
    pc.legend_room(ax, ncol=4)
    pc.panel_label(ax, "(a) arrival time of each channel (V shape = transverse propagation)")
    # ΔT(u) の傾き = −2 / v_eff
    ax = axes[0, 1]
    dt, dte, npair = r.pair("dt_mpv_ps"), r.pair("dt_err_ps"), r.pair("n_pair")
    vs, ves = [], []
    for gi, pn in enumerate(r.pair_names):
        m = (f["gap"] == gi) & (npair[:, gi] >= N_PAIR_MIN) & np.isfinite(dt[:, gi])
        lab = f"{pn[:2]}-{pn[2:]}"
        mf = m & (f["u"] >= pc.U_FIT_MARGIN) & (f["u"] <= f["width"] - pc.U_FIT_MARGIN)
        if mf.sum() >= 5:
            a, ea, b, eb_, chi2 = st.wlinfit(f["u"][mf], dt[mf, gi], np.maximum(dte[mf, gi], 0.3))
            if b < 0:
                v, ve = -2.0 / b, 2.0 * eb_ / b ** 2
                vs.append(v)
                ves.append(ve)
                ns.set(f"a6_veff_{pn}_um_per_ps", v, ve, "um/ps", "from the slope of Delta T(u) = -2/v_eff")
                lab += f": v_eff = {v:.3f} +- {ve:.3f} um/ps"
                uu = np.array([pc.U_FIT_MARGIN, np.nanmedian(f["width"][mf]) - pc.U_FIT_MARGIN])
                ax.plot(uu, a + b * uu, color=pc.PAIR_COLORS[gi % 4], lw=1.0)
        pc.eb(ax, f["u"][m], dt[m, gi], dte[m, gi], color=pc.PAIR_COLORS[gi % 4], label=lab)
    if vs:
        m_, e_, _ = st.wmean(vs, ves)
        ns.set("a6_veff_body_um_per_ps", m_, e_, "um/ps", "weighted mean over gaps")
    ax.axhline(0, color=pc.C_GRAY, ls=":", lw=0.9)
    ax.set_xlabel("Distance from the strip-A center, u [um]")
    ax.set_ylabel("Delta T = t_B - t_A [ps]")
    pc.legend_room(ax)
    pc.panel_label(ax, "(b) pair time difference and v_eff")
    ax = axes[1, 0]
    ok = np.isfinite(dly) & (dle > 0)
    pc.eb(ax, dist[ok], dly[ok], dle[ok], color=pc.C_BLUE, ms=2.5, label="single-channel delay (all ch)")
    mf = ok & (dist >= 50.0) & (dist <= r.pitch - 50.0)
    if mf.sum() >= 4:
        a, ea, b, eb_, chi2 = st.wlinfit(dist[mf], dly[mf], np.maximum(dle[mf], 0.3))
        xx = np.array([50.0, r.pitch - 50.0])
        ax.plot(xx, a + b * xx, "r-", lw=1.3, label=f"linear fit: {b:.2f} ps/um  (v = {1 / b:.2f} um/ps)")
        ns.set("a6_single_delay_slope_ps_per_um", b, eb_, "ps/um", "single-channel delay vs distance, 50 um inside")
        ns.set("a6_single_velocity_um_per_ps", 1.0 / b, eb_ / b ** 2, "um/ps")
    ax.set_xlabel("Distance from the own strip center [um]")
    ax.set_ylabel("Transverse delay of a single channel [ps]")
    pc.legend_room(ax)
    pc.panel_label(ax, "(c) single-channel delay vs distance from the strip")
    ax = axes[1, 1]
    pc.eb(ax, am[ok], dly[ok], dle[ok], color=pc.C_BLUE, ms=2.5, label="transverse delay (all ch)")
    if ok.sum() >= 4:
        c0, e0, c1, e1, chi2 = st.wlinfit(np.log(am[ok]), dly[ok], np.maximum(dle[ok], 0.3))
        tau = -c1
        if tau > 0:
            a0 = float(np.exp(c0 / tau))
            aa = np.linspace(am[ok].min(), am[ok].max(), 200)
            ax.plot(aa, tau * np.log(a0 / aa), "r-", lw=1.3,
                    label=f"tau ln(A0/A): tau = {tau:.0f} +- {e1:.0f} ps, A0 = {a0:.0f} mV")
            ns.set("a7_delay_log_tau_ps", tau, e1, "ps", "transverse delay = tau ln(A0/A)")
            ns.set("a7_delay_log_A0_mV", a0, unit="mV")
    ax.set_xlabel("Amplitude MPV of the channel [mV]")
    ax.set_ylabel("Transverse delay of the channel [ps]")
    pc.legend_room(ax)
    pc.panel_label(ax, "(d) item 7: transverse delay vs amplitude")
    pc.savefig(fig, outdir, "fig6_7_propagation.png")


def run(scan, red, outdir: Path, opts=None):
    opts = opts or {}
    r = pc.Red(red, opts.get("x_off", 0.0), opts.get("y_off", 0.0), opts.get("pitch", 500.0))
    cen, cinfo = pc.strip_centers(r)
    f = pc.fold(r, cen)
    cen0 = cen[0]
    ns = pc.Numbers()
    print("  strip centers [um]: " + ", ".join(f"{ch} {v:+.0f}" for ch, v in zip(r.chs, cen0)))
    fig_amplitude(r, f, ns, outdir, cen0)
    fig_rise(r, f, ns, outdir, cen0)
    fig_jitter(r, f, ns, outdir, cen0)
    fig_position(r, f, ns, outdir, cen0)
    fig_time(r, f, ns, outdir, cen0)
    fig_propagation(r, f, ns, outdir, cen0)
    extra = [f"scan axis: Y {np.nanmin(r.Y):.0f}..{np.nanmax(r.Y):.0f} um at X = {np.nanmedian(r.X):.0f} um",
             "strip centers (half-maximum midpoint; None = taken from the pitch): " + json_str(cinfo.get("found", {}))]
    pc.finish(scan, r, ns, outdir, extra)
    return ns


def json_str(d):
    return ", ".join(f"{k} {v}" for k, v in d.items())
