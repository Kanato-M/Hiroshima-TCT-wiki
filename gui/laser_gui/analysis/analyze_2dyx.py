# -*- coding: utf-8 -*-
"""2D スキャン (2dYX: ストリップを横切る Y × ストリップに沿う X) の簡易解析。

解析ノート matome の解析項目 1–7 を、同じ定義で 1 スキャンに対して行う
(元の実装: analysis/macro/matome/a1…a7_*.py)。数値のキーも matome の numbers.json に合わせてある。

出力 (results\\<スキャン名>\\):
  fig1_amplitude_charge.png  項目 1: A_lead / A_lead+A_sub / Q_tot のマップ、ギャップ内・長さ方向 (A_total は参考)
  fig2_rise_time.png         項目 2: 立ち上がり 10–90 % / 40–60 % のマップとプロファイル
  fig3_jitter.png            項目 3: ジッターのマップ、Y × イベント毎ジッターの 2D + MPV、振幅依存と 1/A 則
  fig4_position.png          項目 4: η 較正曲線、実効ピッチ、位置分解能 (偶奇分割 test) のマップと Y 依存
  fig5_time_resolution.png   項目 5: σ_lead / σ_2strip のマップ、Y × σ の 2D + MPV、長さ方向、振幅依存
  fig6_propagation.png       項目 6: 長さ方向の Elmore フィット、横方向の ΔT と v_eff、単一 ch の遅延
  fig7_amp_vs_propagation.png 項目 7: 横方向の遅延と振幅の関係 (τ ln(A0/A))
  fig_waveforms.png / summary.csv / summary.txt / numbers.json
  さらに (opts["no_template"] でなければ) macro\\template_timing\\tt_pipeline.py をサブプロセスで実行し、テンプレートフィット解析の
  論文図 (results\\paper_plots_20261013 と同じ 01–09) を results\\<スキャン名>\\paper_plots\\ と paper_NN_*.png に作る。

座標: X = xn + x_off (ストリップ方向)、Y = yn + y_off。「本体」= body_x の範囲 (既定 −9900..−700 µm、
この範囲に列が無いスキャンでは全列)。ストリップ中心は振幅プロファイルから求める。

単体実行: python analyze_2dyx.py <scan_dir> [--x-off -632]
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


def _u_edges(grid, pitch, u=None):
    """ギャップ内プロファイルのビン境界。行の間隔に合わせ、測定行がビンの中央に来るよう位相を合わせる
    (行がビン境界に乗ると、隣のビンに振り分けられて値がばたつくため)。"""
    step = float(np.median(np.diff(grid.yc))) if grid.yc.size > 1 else 25.0
    step = max(step, pitch / 40.0)
    phase = 0.0
    if u is not None and np.isfinite(u).any():
        phase = float(np.nanmedian(np.mod(u[np.isfinite(u)], step)))
    start = phase - step / 2.0
    while start > 0:
        start -= step
    return np.arange(start, pitch + step, step)


def _vlines_x(ax, r):
    lo, hi = r.body_x
    for x in (lo, hi):
        if ax.get_xlim()[0] < x < ax.get_xlim()[1]:
            ax.axvline(x, color=pc.C_GRAY, ls=":", lw=0.9)


def _mfill(k):
    return ("o", None) if k == 0 else ("s", "none")


# ----------------------------------------------------------------------
def fig_amplitude(r, g, f, ns, outdir):
    fig, axes = plt.subplots(2, 3, figsize=(17.5, 9.4))
    for ax, key, lab in ((axes[0, 0], "amp_lead_mpv_mV", "Leading amplitude MPV [mV]"),
                         (axes[0, 1], "amp_ls_mpv_mV", "A_lead + A_sub MPV [mV]"),
                         (axes[0, 2], "q_sum_mpv_pC", "Total charge (all ch) MPV [pC]")):
        v = r.ev(key)
        pc.map_panel(ax, g, v, lab, vmin=0, vmax=pc.finite_range(v)[1])
        pc.panel_label(ax, lab.split(" MPV")[0])
    body = r.body()
    gapc = body & (f["gap"] >= 0) & (r.q("hit_frac").max(axis=1) >= pc.HIT_FRAC_MIN) \
        & np.isfinite(r.ev("amp_lead_mpv_mV"))
    ue = _u_edges(g, r.pitch, f["u"])
    # (d) ギャップ 1 つに折り畳んだプロファイル
    ax = axes[1, 0]
    ax2 = ax.twinx()
    for key, col, lab, mk in (("amp_lead_mpv_mV", pc.C_BLUE, "A_lead", "o"),
                              ("amp_sub_mpv_mV", pc.C_ORANGE, "A_sub", "s"),
                              ("amp_ls_mpv_mV", "k", "A_lead + A_sub", "D"),
                              ("amp_tot_mpv_mV", pc.C_PINK, "A_total (reference)", "^")):
        cen, med, err, n = st.binned_profile(f["u"][gapc], r.ev(key)[gapc], ue)
        pc.eb(ax, cen, med, err, color=col, marker=mk, label=lab)
    cen, med, err, n = st.binned_profile(f["u"][gapc], r.ev("q_sum_mpv_pC")[gapc], ue)
    pc.eb(ax2, cen, med, err, color=pc.C_GREEN, marker="v", label="Q_tot (right axis)")
    side = gapc & (((f["u"] > 25) & (f["u"] < 75)) | ((f["u"] > f["width"] - 75) & (f["u"] < f["width"] - 25)))
    mid = gapc & (np.abs(f["u"] - f["width"] / 2) < 20.0)
    for name, key, unit in (("amp_lead", "amp_lead_mpv_mV", "mV"), ("amp_sub", "amp_sub_mpv_mV", "mV"),
                            ("amp_ls", "amp_ls_mpv_mV", "mV"), ("amp_tot", "amp_tot_mpv_mV", "mV"),
                            ("amp_4th", "amp_4th_mpv_mV", "mV"), ("q_tot", "q_sum_mpv_pC", "pC")):
        for tag, sel in (("side", side), ("gapcenter", mid)):
            m_, e_, _ = st.median_sem(r.ev(key)[sel])
            ns.set(f"a1_{name}_{tag}", m_, e_, unit, "median over body cells of the per-point MPV")
    for name in ("amp_ls", "amp_tot", "q_tot"):
        ns.set(f"a1_{name}_center_over_side_pct",
               100.0 * (ns.get(f"a1_{name}_gapcenter") / ns.get(f"a1_{name}_side") - 1.0), unit="%")
    ax.set_xlabel("Distance from the strip-A center, u [um]")
    ax.set_ylabel("Amplitude MPV [mV]")
    ax2.set_ylabel("Total charge MPV [pC]")
    ax.set_ylim(0, 1.75 * np.nanmax(ax.get_ylim()))
    ax2.set_ylim(0, 1.75 * np.nanmax(ax2.get_ylim()))
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, ncol=2, loc="upper center")
    pc.panel_label(ax, "(d) folded into one gap (body, all columns)")
    # (e) 長さ方向: ストリップ脇の振幅 (ch 別)
    ax = axes[1, 1]
    slopes = []
    amp, amp_e = r.q("amp_mpv_mV"), r.q("amp_mpv_err_mV")
    for c, ch in enumerate(r.chs):
        xc, mu, er, n = pc.band_profile(r, g, f, amp[:, c], amp_e[:, c], c)
        pc.eb(ax, xc, mu, er, color=pc.ch_color(ch), label=ch)
        b_ = np.isfinite(mu) & (xc >= r.body_x[0]) & (xc <= r.body_x[1])
        if b_.sum() < 4:
            b_ = np.isfinite(mu)
        if b_.sum() >= 4:
            a, ea, b, eb_, chi2 = st.wlinfit(xc[b_] / 1e3, mu[b_], er[b_])
            a0 = a + b * float(np.mean(xc[b_])) / 1e3
            ns.set(f"a1_amp_slope_{ch}_pct_per_mm", -100.0 * b / a0, 100.0 * eb_ / a0, "%/mm",
                   "relative change toward -X (far end); band beside the strip, body")
            slopes.append(-100.0 * b / a0)
    if slopes:
        ns.set("a1_amp_slope_absmax_pct_per_mm", np.max(np.abs(slopes)), unit="%/mm")
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("Amplitude MPV beside the strip [mV]")
    ax.set_ylim(0, None)
    _vlines_x(ax, r)
    pc.legend_room(ax, ncol=4)
    pc.panel_label(ax, "(e) along the strip (band 25-75 um beside each strip)")
    # (f) 長さ方向: 全電荷
    ax = axes[1, 2]
    ok = (f["gap"] >= 0) & (r.q("hit_frac").max(axis=1) >= pc.HIT_FRAC_MIN)
    sidex = ok & (np.abs(f["dist"]) >= pc.BAND_UM[0]) & (np.abs(f["dist"]) <= pc.BAND_UM[1])
    midx = ok & (np.abs(f["u"] - f["width"] / 2) < 30.0)
    for tag, sel, col, mk in (("beside the strips", sidex, pc.C_BLUE, "o"), ("gap centers", midx, pc.C_ORANGE, "s")):
        xc, mu, er, n = pc.column_profile(g, r.ev("q_sum_mpv_pC"), r.ev("q_sum_err_pC"), sel)
        pc.eb(ax, xc, mu, er, color=col, marker=mk, label=tag)
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("Total charge MPV [pC]")
    ax.set_ylim(0, None)
    _vlines_x(ax, r)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, "(f) total charge along the strip")
    pc.savefig(fig, outdir, "fig1_amplitude_charge.png")


def _dist_profile(r, f, name, edges, ratio_to=None):
    xs, vs = [], []
    v = r.q(name)
    if ratio_to is not None:
        v = v / r.q(ratio_to)
    for c in range(r.nch):
        m = pc.ch_cells(r, c)
        xs.append(np.abs(f["dy"][m, c]))
        vs.append(v[m, c])
    return st.binned_profile(np.concatenate(xs), np.concatenate(vs), edges)


def fig_rise(r, g, f, ns, outdir):
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.4))
    for ax, key, lab in ((axes[0, 0], "rise1090_mpv_ps", "Rise time 10-90% MPV, leading strip [ps]"),
                         (axes[0, 1], "rise4060_mpv_ps", "Rise time 40-60% MPV, leading strip [ps]")):
        pc.map_panel(ax, g, r.lead(key), lab)
        pc.panel_label(ax, lab.split(" MPV")[0])
    ue = _u_edges(g, r.pitch, f["u"])
    ax = axes[1, 0]
    for key, tag, col, mk in (("rise1090_mpv_ps", "1090", pc.C_BLUE, "o"), ("rise4060_mpv_ps", "4060", pc.C_ORANGE, "s")):
        cen, med, err, n = _dist_profile(r, f, key, ue)
        pc.eb(ax, cen, med, err, color=col, marker=mk, label=f"{tag[:2]}-{tag[2:]}%")
        v, e, n = pc.band_median(r, f, key)
        ns.set(f"a2_rise{tag}_band_ps", v, e, "ps", "band 25-75 um beside the strips, body, median over cells")
        sel = (np.abs(cen - r.pitch / 2) < 20.0) & np.isfinite(med)
        if sel.any():
            vv, ee, _ = st.wmean(med[sel], err[sel])
            ns.set(f"a2_rise{tag}_gapcenter_ps", vv, ee, "ps")
    ax.set_xlabel("Distance from the own strip center [um]")
    ax.set_ylabel("Rise time MPV [ps]")
    ax.set_ylim(0, None)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, "(c) vs distance from the strip (body, all ch)")
    ax = axes[1, 1]
    sl = []
    v, ve = r.q("rise1090_mpv_ps"), r.q("rise1090_err_ps")
    for c, ch in enumerate(r.chs):
        xc, mu, er, n = pc.band_profile(r, g, f, v[:, c], ve[:, c], c)
        pc.eb(ax, xc, mu, er, color=pc.ch_color(ch), label=ch)
        b_ = np.isfinite(mu) & (xc >= r.body_x[0]) & (xc <= r.body_x[1])
        if b_.sum() >= 4:
            a, ea, b, eb_, chi2 = st.wlinfit(xc[b_] / 1e3, mu[b_], er[b_])
            sl.append(b)
    if len(sl) >= 2:
        ns.set("a2_rise1090_slope_mean_ps_per_mm", np.mean(sl), np.std(sl, ddof=1) / np.sqrt(len(sl)), "ps/mm",
               "d(rise)/dX beside the strip (positive = longer toward the read-out end)")
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("Rise time 10-90% beside the strip [ps]")
    _vlines_x(ax, r)
    pc.legend_room(ax, ncol=4)
    pc.panel_label(ax, "(d) along the strip")
    pc.savefig(fig, outdir, "fig2_rise_time.png")


def fig_jitter(r, g, f, ns, outdir, cen0):
    fig, axes = plt.subplots(2, 2, figsize=(14.0, 9.4))
    pc.map_panel(axes[0, 0], g, r.lead("jit_4060_mpv_ps"), "Jitter MPV (40-60% slope), leading strip [ps]",
                 vmin=10, vmax=45)
    pc.panel_label(axes[0, 0], "(a) jitter of the leading strip")
    ax = axes[0, 1]
    yc, mu, er = pc.events_vs_pos(ax, r.Y, r.jit_lead_hist[:, 0], r.jit_edges,
                                  "Event jitter (40-60% slope) [ps]", "Y across strips [um]",
                                  mask=r.body() & r.ok, centers=cen0)
    ax.set_ylim(0, 100)
    pc.legend_on_map(ax, loc="upper right")
    pc.panel_label(ax, "(b) per-event jitter of the leading strip vs Y (body columns)")
    lo, hi = pc.row_range(yc, mu, cen0)
    ns.set("a3_jit4060_rowmpv_lo_ps", lo, unit="ps", note="5th percentile of the per-row MPV between the strips")
    ns.set("a3_jit4060_rowmpv_hi_ps", hi, unit="ps", note="95th percentile")
    for tag in ("4060", "1090", "cfd50"):
        v, e, n = pc.band_median(r, f, f"jit_{tag}_mpv_ps")
        ns.set(f"a3_jit{tag}_band_ps", v, e, "ps", "band 25-75 um beside the strips, body, median over cells")
    # (c) ギャップ内: leading / sub-leading
    ue = _u_edges(g, r.pitch, f["u"])
    ax = axes[1, 0]
    gm = r.body() & (f["gap"] >= 0)
    for tag, col in (("4060", pc.C_BLUE), ("1090", pc.C_YELLOW)):
        for rank, rname, mk in ((0, "leading", "o"), (1, "sub-leading", "v")):
            if r.nch < 2 and rank:
                continue
            cen, med, err, n = st.binned_profile(f["u"][gm], r.rank(f"jit_{tag}_mpv_ps", rank)[gm], ue)
            pc.eb(ax, cen, med, err, color=col, marker=mk, label=f"{rname} {tag[:2]}-{tag[2:]}%")
            if rank == 0:
                sel = (np.abs(cen - r.pitch / 2) < 20.0) & np.isfinite(med)
                if sel.any():
                    vv, ee, _ = st.wmean(med[sel], err[sel])
                    ns.set(f"a3_jit{tag}_lead_gapcenter_ps", vv, ee, "ps")
    ax.set_xlabel("Distance from the strip-A center, u [um]")
    ax.set_ylabel("Jitter MPV [ps]")
    ax.set_ylim(0, 75)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, "(c) within a gap")
    # (d) 振幅依存と 1/A 則
    ax = axes[1, 1]
    a_edges = np.concatenate([np.arange(10, 40, 3.0), np.arange(40, 96, 5.0)])
    amp = r.q("amp_mpv_mV")
    for tag, lab, col, mk in (("4060", "40-60% slope", pc.C_BLUE, "o"), ("1090", "10-90% slope", pc.C_YELLOW, "s"),
                              ("cfd50", "CFD50 local slope", "k", "D")):
        v = r.q(f"jit_{tag}_mpv_ps")
        xs = np.concatenate([amp[pc.ch_cells(r, c), c] for c in range(r.nch)])
        vs = np.concatenate([v[pc.ch_cells(r, c), c] for c in range(r.nch)])
        cen, med, err, n = st.binned_profile(xs, vs, a_edges, n_min=5)
        pc.eb(ax, cen, med, err, color=col, marker=mk, label=lab)
        ok = np.isfinite(med) & np.isfinite(err) & (err > 0) & (cen >= 25.0)
        if ok.sum() >= 4:
            p0, e0, p1, e1, chi2 = st.fit_inverse_law(cen[ok], med[ok], err[ok])
            ns.set(f"a3_jit{tag}_law_p0_psmV", p0, e0, "ps mV", "fit sigma_jit = p0/A + p1, A >= 25 mV")
            ns.set(f"a3_jit{tag}_law_p1_ps", p1, e1, "ps")
    aa = np.linspace(10, 95, 200)
    ax.plot(aa, pc.JITTER_LAW[0] / aa + pc.JITTER_LAW[1], "r-", lw=1.4,
            label=f"reference 1/A law: {pc.JITTER_LAW[0]:.0f}/A + {pc.JITTER_LAW[1]:.2f} ps")
    ax.set_xlabel("Amplitude MPV [mV]")
    ax.set_ylabel("Jitter MPV [ps]")
    ax.set_ylim(0, 110)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, "(d) jitter vs amplitude (body cells, all ch)")
    pc.savefig(fig, outdir, "fig3_jitter.png")


def fig_position(r, g, f, ns, outdir, cen0):
    if not r.pairs:
        print("  [skip] 位置分解能: 隣り合う ch の組がありません")
        return
    out, lines = pos.analyze(r, f, line_id=g.ix)
    fig, axes = plt.subplots(2, 2, figsize=(14.0, 9.4))
    ix_mid = int(np.argmin(np.abs(g.xc - np.median(g.xc[(g.xc >= r.body_x[0]) & (g.xc <= r.body_x[1])]
                                                   if ((g.xc >= r.body_x[0]) & (g.xc <= r.body_x[1])).any()
                                                   else g.xc))))
    ax = axes[0, 0]
    for gi, pn in enumerate(r.pair_names):
        rows = np.where((g.ix == ix_mid) & (f["gap"] == gi) & np.isfinite(out["eta_mean"]))[0]
        rows = rows[np.argsort(f["u"][rows])]
        pc.eb(ax, f["u"][rows], out["eta_mean"][rows], out["eta_err"][rows], color=pc.PAIR_COLORS[gi % 4],
              label=f"{pn[:2]}-{pn[2:]}")
    uu = np.array([0.0, r.pitch])
    ax.plot(uu, uu / r.pitch, color=pc.C_GRAY, ls="--", lw=1.0, label=f"eta = u / {r.pitch:.0f} um (nominal)")
    ax.set_xlabel("Distance from the strip-A center, u [um]")
    ax.set_ylabel("Mean eta = A_B / (A_A + A_B)")
    ax.set_xlim(0, r.pitch)
    ax.set_ylim(0, 1)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, f"(a) eta calibration curves at X = {g.xc[ix_mid]:.0f} um")
    # (b) 実効ピッチの長さ方向依存
    ax = axes[0, 1]
    bodyc = (g.xc >= r.body_x[0]) & (g.xc <= r.body_x[1])
    if bodyc.sum() < 3:
        bodyc = np.ones(g.xc.size, bool)
    allp = []
    for gi, pn in enumerate(r.pair_names):
        p = np.array([lines.get((k, gi), {}).get("pitch_eff", np.nan) for k in range(g.xc.size)])
        pe = np.array([lines.get((k, gi), {}).get("pitch_eff_err", np.nan) for k in range(g.xc.size)])
        pc.eb(ax, g.xc, p, pe, color=pc.PAIR_COLORS[gi % 4], label=f"{pn[:2]}-{pn[2:]}")
        m_, e_, _ = st.median_sem(p[bodyc])
        ns.set(f"a4_pitch_eff_{pn}_um", m_, e_, "um", "median over body columns")
        allp.append(p[bodyc])
    allp = np.concatenate(allp)
    allp = allp[np.isfinite(allp)]
    if allp.size:
        ns.set("a4_pitch_eff_body_um", np.median(allp), allp.std(ddof=1) if allp.size > 1 else None, "um",
               "median over body columns and gaps; error = column-to-column spread (std)")
    ax.axhline(r.pitch, color=pc.C_GRAY, ls="--", lw=1.0, label=f"nominal pitch {r.pitch:.0f} um")
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("Effective pitch 1 / (d eta / du) [um]")
    _vlines_x(ax, r)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, "(b) effective pitch along the strip")
    # (c) マップ
    pc.map_panel(axes[1, 0], g, out["sig_test"], "Position resolution (test) [um]", vmin=5, vmax=30)
    pc.panel_label(axes[1, 0], "(c) position resolution (calibrated on even, evaluated on odd events)")
    # (d) Y × セルごとの位置分解能 + 行ごとの MPV
    ax = axes[1, 1]
    lab = "Position resolution (test) [um]"
    n_min = 12 if bodyc.sum() >= 12 else max(3, int(bodyc.sum()) - 1)
    yc, mu, er = pc.cells_vs_pos(ax, r.Y, out["sig_test"], np.arange(0.0, 40.5, 0.5), lab,
                                 "Y across strips [um]", mask=r.body(), centers=cen0, n_min=n_min,
                                 label="MPV over cells per row (test)")
    pc.cells_vs_pos(ax, r.Y, out["sig_exp"], np.arange(0.0, 40.5, 0.5), lab, "Y across strips [um]",
                    mask=r.body(), n_min=n_min, color="k", marker="x", label="MPV, noise expectation",
                    draw=False)
    ax.set_xlim(np.nanmin(cen0) - 100, np.nanmax(cen0) + 100)
    pc.legend_on_map(ax, loc="upper right")
    pc.panel_label(ax, "(d) resolution per cell vs Y (body columns)")
    lo, hi = pc.row_range(yc, mu, cen0)
    ns.set("a4_posres_rowmpv_lo_um", lo, unit="um", note="5th percentile of the per-row MPV")
    ns.set("a4_posres_rowmpv_hi_um", hi, unit="um", note="95th percentile")
    sel = r.body() & (f["u"] >= 50.0) & (f["u"] <= f["width"] - 50.0)
    for key, tag in (("sig_test", "test"), ("sig_insitu", "insitu"), ("sig_exp", "expected")):
        m_, e_, _ = st.median_sem(out[key][sel])
        ns.set(f"a4_posres_{tag}_um", m_, e_, "um", "median over body cells, 50 um inside the gap")
    m_, e_, _ = st.median_sem(np.abs(out["bias_test"][sel]))
    ns.set("a4_posbias_abs_median_um", m_, e_, "um")
    ns.set("a4_binary_resolution_um", r.pitch / np.sqrt(12.0), unit="um", note="pitch / sqrt(12)")
    pc.savefig(fig, outdir, "fig4_position.png")


def _time_derived(r, f):
    lc = r.lead_channel()
    lead_ok = r.take("hit_frac", lc) >= pc.HIT_FRAC_MIN
    s_lead = np.where(lead_ok, r.ev("sigma_lead_ps"), np.nan)
    va, vb, vd, vc = (pc.gap_pair(r, f, f"var_{k}_ps2") for k in ("a", "b", "d", "cw2"))
    with np.errstate(invalid="ignore", divide="ignore"):
        s_dt = np.sqrt(vd / 2.0)
        s_2s = np.sqrt(vc)
        sref2 = 0.5 * (va + vb - vd)
        sref = pc.SIGMA_REF_PS
        s_2s_sub = np.sqrt(np.where(vc > sref ** 2, vc - sref ** 2, np.nan))
    return dict(s_lead=s_lead, s_dt=s_dt, s_2s=s_2s, sref2=sref2, s_2s_sub=s_2s_sub,
                jit_lead=r.take("jit_cfd50_mpv_ps", lc))


def fig_time(r, g, f, ns, outdir, cen0):
    dv = _time_derived(r, f)
    sref = pc.SIGMA_REF_PS
    fig, axes = plt.subplots(2, 3, figsize=(18.5, 9.4))
    pc.map_panel(axes[0, 0], g, dv["s_lead"], "sigma_t of the leading strip [ps]", vmin=20, vmax=60)
    pc.panel_label(axes[0, 0], "(a) sigma_t, leading strip (TRIG OUT ref.)")
    pc.map_panel(axes[0, 1], g, dv["s_2s"], "sigma_t, 2-strip A^2-weighted [ps]", vmin=20, vmax=45)
    pc.panel_label(axes[0, 1], "(b) sigma_t, weighted mean of 2 strips")
    ncol_body = int(((g.xc >= r.body_x[0]) & (g.xc <= r.body_x[1])).sum()) or g.xc.size
    n_min = 12 if ncol_body >= 12 else max(3, ncol_body - 1)
    ax = axes[0, 2]
    yc, mu, er = pc.cells_vs_pos(ax, r.Y, dv["s_lead"], np.arange(10.0, 80.5, 1.0),
                                 "sigma_t of the leading strip [ps]", "Y across strips [um]",
                                 mask=r.body(), centers=cen0, n_min=n_min)
    pc.cells_vs_pos(ax, r.Y, dv["s_2s"], np.arange(10.0, 80.5, 1.0), "", "Y across strips [um]",
                    mask=r.body(), n_min=n_min, color="w", marker="D", label="MPV, 2-strip weighted",
                    draw=False)
    pc.legend_on_map(ax, loc="upper right", facecolor="0.75")
    pc.panel_label(ax, "(c) sigma_t per cell vs Y (body columns)")
    lo, hi = pc.row_range(yc, mu, cen0)
    ns.set("a5_sigma_lead_rowmpv_lo_ps", lo, unit="ps", note="5th percentile of the per-row MPV")
    ns.set("a5_sigma_lead_rowmpv_hi_ps", hi, unit="ps", note="95th percentile")
    # (d) ギャップ内
    ue = _u_edges(g, r.pitch, f["u"])
    ax = axes[1, 0]
    gm = r.body() & (f["gap"] >= 0) & (f["u"] >= 30.0) & (f["u"] <= f["width"] - 30.0)
    for key, lab, col, mk in (("s_lead", "leading (TRIG ref.)", pc.C_BLUE, "o"),
                              ("s_dt", "Delta t / sqrt(2)", pc.C_ORANGE, "v"),
                              ("s_2s", "2-strip weighted (TRIG ref.)", pc.C_GREEN, "D"),
                              ("jit_lead", "predicted jitter (leading)", "k", "x")):
        cen, med, err, n = st.binned_profile(f["u"][gm], dv[key][gm], ue)
        pc.eb(ax, cen, med, err, color=col, marker=mk, label=lab)
    ctr = gm & (np.abs(f["u"] - f["width"] / 2) < 30.0)
    for key, name in (("s_lead", "sigma_lead"), ("s_dt", "sigma_dt_sqrt2"), ("s_2s", "sigma_2strip"),
                      ("s_2s_sub", "sigma_2strip_floorsub")):
        for tag, sel in (("gapcenter", ctr), ("gapall", gm)):
            m_, e_, _ = st.median_sem(dv[key][sel])
            ns.set(f"a5_{name}_{tag}_ps", m_, e_, "ps", "median over body cells")
    ax.set_xlabel("Distance from the strip-A center, u [um]")
    ax.set_ylabel("Time resolution [ps]")
    ax.set_ylim(0, 75)
    pc.legend_room(ax, ncol=2)
    pc.panel_label(ax, "(d) within a gap")
    # (e) 長さ方向 (ストリップ脇の単ストリップ σ)
    ax = axes[1, 1]
    sc, sce = r.q("sigma_core_ps"), r.q("sigma_core_err_ps")
    for c, ch in enumerate(r.chs):
        xc, mu, er, n = pc.band_profile(r, g, f, sc[:, c], sce[:, c], c)
        pc.eb(ax, xc, mu, er, color=pc.ch_color(ch), label=ch)
    v, e, n = pc.band_median(r, f, "sigma_core_ps")
    ns.set("a5_sigma_band_ps", v, e, "ps", "single-strip sigma_core beside the strips, body, TRIG OUT reference")
    if np.isfinite(v) and v > sref:
        ns.set("a5_sigma_band_floorsub_ps", np.sqrt(v ** 2 - sref ** 2), v * e / np.sqrt(v ** 2 - sref ** 2),
               "ps", f"sqrt(sigma^2 - {sref}^2)")
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("sigma_t beside the strip (TRIG OUT ref.) [ps]")
    ax.set_ylim(0, 60)
    _vlines_x(ax, r)
    pc.legend_room(ax, ncol=4)
    pc.panel_label(ax, "(e) along the strip")
    # (f) 振幅依存
    ax = axes[1, 2]
    a_edges = np.concatenate([np.arange(10, 40, 3.0), np.arange(40, 96, 5.0)])
    amp = r.q("amp_mpv_mV")
    xs = np.concatenate([amp[pc.ch_cells(r, c), c] for c in range(r.nch)])
    vs = np.concatenate([sc[pc.ch_cells(r, c), c] for c in range(r.nch)])
    cen, med, err, n = st.binned_profile(xs, vs, a_edges, n_min=5)
    pc.eb(ax, cen, med, err, color=pc.C_BLUE, label="single-strip sigma_t (body cells, all ch)")
    aa = np.linspace(10, 95, 200)
    jl = pc.JITTER_LAW[0] / aa + pc.JITTER_LAW[1]
    ax.plot(aa, jl, "r--", lw=1.2, label=f"reference jitter law {pc.JITTER_LAW[0]:.0f}/A + {pc.JITTER_LAW[1]:.2f} ps")
    ax.plot(aa, np.hypot(jl, sref), "r-", lw=1.4, label=f"jitter law (+) sigma_ref = {sref} ps")
    sel = r.body() & (f["gap"] >= 0) & (np.abs(f["u"] - f["width"] / 2) < 100.0) & np.isfinite(dv["sref2"])
    m2_, e2_, n = st.median_sem(dv["sref2"][sel])
    if np.isfinite(m2_) and m2_ > 0:
        ns.set("a5_sigma_ref_self_ps", np.sqrt(m2_), e2_ / (2.0 * np.sqrt(m2_)), "ps",
               "sqrt(median of (VarA + VarB - VarDelta)/2), cells within 100 um of the gap center")
    ax.set_xlabel("Amplitude MPV [mV]")
    ax.set_ylabel("Single-strip sigma_t (TRIG OUT ref.) [ps]")
    ax.set_ylim(0, 130)
    pc.legend_room(ax)
    pc.panel_label(ax, "(f) time resolution vs amplitude")
    pc.savefig(fig, outdir, "fig5_time_resolution.png")


def fig_propagation(r, g, f, ns, outdir):
    """項目 6 と 7。返り値なし。"""
    toa, toa_e, ntime = r.q("toa_mu_ps"), r.q("toa_mu_err_ps"), r.q("n_time")
    fig, axes = plt.subplots(2, 3, figsize=(18.5, 9.4))
    # ---- 長さ方向: 帯の ToA と Elmore フィット (d = −X) ----
    ax, axr = axes[0, 0], axes[0, 1]
    lon, rc, rce, tots = {}, [], [], []
    dfine = np.linspace(0.0, 10.0, 300)
    for c, ch in enumerate(r.chs):
        t = np.where(ntime[:, c] >= 500, toa[:, c], np.nan)
        xc, mu, er, n = pc.band_profile(r, g, f, t, toa_e[:, c], c)
        lon[c] = mu
        b_ = np.isfinite(mu) & (xc >= r.body_x[0]) & (xc <= r.body_x[1])
        if b_.sum() < 5:
            b_ = np.isfinite(mu)
        fit = st.fit_elmore(-xc[b_] / 1e3, mu[b_], er[b_]) if b_.sum() >= 5 else None
        t0 = fit["t0"] if fit else (np.nanmin(mu) if np.isfinite(mu).any() else 0.0)
        pc.eb(ax, xc, mu - t0, er, color=pc.ch_color(ch), label=ch)
        if fit is None:
            continue
        ax.plot(-dfine * 1e3, st.elmore_toa(dfine, fit["rho_c"], fit["L"], 0.0), color=pc.ch_color(ch), lw=0.9)
        pc.eb(axr, xc, mu - st.elmore_toa(-xc / 1e3, fit["rho_c"], fit["L"], fit["t0"]), er,
              color=pc.ch_color(ch), label=ch)
        for q, unit in (("rho_c", "ps/mm^2"), ("L", "mm")):
            ns.set(f"a6_elmore_{q}_{ch}", fit[q], fit[f"{q}_err"], unit, "Elmore fit of the band ToA, body, d = -X")
        ns.set(f"a6_elmore_chi2ndf_{ch}", fit["chi2_ndf"])
        rc.append(fit["rho_c"])
        rce.append(fit["rho_c_err"])
        d_lo, d_hi = -r.body_x[1] / 1e3, -r.body_x[0] / 1e3
        tots.append(st.elmore_toa(d_hi, fit["rho_c"], fit["L"], 0.0) - st.elmore_toa(d_lo, fit["rho_c"], fit["L"], 0.0))
    if rc:
        m_, e_, _ = st.wmean(rc, rce)
        ns.set("a6_elmore_rho_c_mean", m_, e_, "ps/mm^2", "weighted mean over channels")
        ns.set("a6_long_delay_mean_ps", np.mean(tots), np.std(tots, ddof=1) / np.sqrt(len(tots)) if len(tots) > 1 else None,
               "ps", f"Elmore-fit delay between X = {r.body_x[1]:.0f} and {r.body_x[0]:.0f} um")
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("ToA - t0 beside the strip [ps]")
    ax.set_xlim(g.xe[0], g.xe[-1])
    _vlines_x(ax, r)
    pc.legend_room(ax, ncol=4)
    pc.panel_label(ax, "(a) arrival time along the strip and Elmore fits")
    axr.axhline(0, color=pc.C_GRAY, ls=":", lw=0.9)
    axr.set_xlabel("X along strip [um]")
    axr.set_ylabel("ToA - Elmore fit [ps]")
    axr.set_ylim(-40, 60)
    _vlines_x(axr, r)
    pc.legend_room(axr, ncol=4)
    pc.panel_label(axr, "(b) residuals")
    # ---- 横方向: ペア時間差 ΔT(u) の傾き = −2 / v_eff ----
    dt, dte, npair = r.pair("dt_mpv_ps"), r.pair("dt_err_ps"), r.pair("n_pair")
    ncol = g.xc.size
    v = np.full((ncol, max(len(r.pairs), 1)), np.nan)
    ve = np.full_like(v, np.nan)
    for gi in range(len(r.pairs)):
        for k in range(ncol):
            m = ((g.ix == k) & (f["gap"] == gi) & (f["u"] >= pc.U_FIT_MARGIN)
                 & (f["u"] <= f["width"] - pc.U_FIT_MARGIN) & (npair[:, gi] >= N_PAIR_MIN)
                 & np.isfinite(dt[:, gi]))
            if m.sum() < 5:
                continue
            a, ea, b, eb_, chi2 = st.wlinfit(f["u"][m], dt[m, gi], np.maximum(dte[m, gi], 0.3))
            if b < 0:
                v[k, gi], ve[k, gi] = -2.0 / b, 2.0 * eb_ / b ** 2
    bodyc = (g.xc >= r.body_x[0]) & (g.xc <= r.body_x[1])
    if bodyc.sum() < 3:
        bodyc = np.ones(ncol, bool)
    ax = axes[0, 2]
    k_mid = int(np.argmin(np.abs(g.xc - np.median(g.xc[bodyc]))))
    for gi, pn in enumerate(r.pair_names):
        m = (g.ix == k_mid) & (f["gap"] == gi) & (npair[:, gi] >= N_PAIR_MIN) & np.isfinite(dt[:, gi])
        pc.eb(ax, f["u"][m], dt[m, gi], dte[m, gi], color=pc.PAIR_COLORS[gi % 4], label=f"{pn[:2]}-{pn[2:]}")
    ax.axhline(0, color=pc.C_GRAY, ls=":", lw=0.9)
    ax.set_xlabel("Distance from the strip-A center, u [um]")
    ax.set_ylabel("Delta T = t_B - t_A [ps]")
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, f"(c) pair time difference at X = {g.xc[k_mid]:.0f} um")
    ax = axes[1, 0]
    allv, alle = [], []
    for gi, pn in enumerate(r.pair_names):
        pc.eb(ax, g.xc, v[:, gi], ve[:, gi], color=pc.PAIR_COLORS[gi % 4], label=f"{pn[:2]}-{pn[2:]}")
        m_, e_, _ = st.wmean(v[bodyc, gi], ve[bodyc, gi])
        ns.set(f"a6_veff_{pn}_um_per_ps", m_, e_, "um/ps", "weighted mean over body columns")
        allv.append(v[bodyc, gi])
        alle.append(ve[bodyc, gi])
    if allv:
        m_, e_, _ = st.wmean(np.concatenate(allv), np.concatenate(alle))
        ns.set("a6_veff_body_um_per_ps", m_, e_, "um/ps",
               "from the slope of Delta T(u) = -2/v_eff, weighted mean over body columns and gaps")
    ax.set_xlabel("X along strip [um]")
    ax.set_ylabel("Transverse velocity v_eff [um/ps]")
    ax.set_ylim(0, 2.0)
    _vlines_x(ax, r)
    pc.legend_room(ax, ncol=3)
    pc.panel_label(ax, "(d) v_eff along the strip")
    # ---- 単一 ch の横伝搬遅延 = ToA − 同じ列の自ストリップ脇の ToA ----
    amp = r.q("amp_mpv_mV")
    dist, dly, am = [], [], []
    for c in range(r.nch):
        ref = lon[c][g.ix]
        m = pc.ch_cells(r, c) & (ntime[:, c] >= 200) & np.isfinite(ref)
        dist.append(np.abs(f["dy"][m, c]))
        dly.append(toa[m, c] - ref[m])
        am.append(amp[m, c])
    dist, dly, am = np.concatenate(dist), np.concatenate(dly), np.concatenate(am)
    ue = _u_edges(g, r.pitch, f["u"])
    ax = axes[1, 1]
    cen, med, err, n = st.binned_profile(dist, dly, ue)
    pc.eb(ax, cen, med, err, color=pc.C_BLUE, label="single-channel delay (body cells, all ch)")
    ok = np.isfinite(med) & (cen >= 50.0) & (cen <= r.pitch - 50.0)
    if ok.sum() >= 4:
        a, ea, b, eb_, chi2 = st.wlinfit(cen[ok], med[ok], np.maximum(err[ok], 0.3))
        ax.plot(cen[ok], a + b * cen[ok], "r-", lw=1.3, label=f"linear fit: {b:.2f} ps/um  (v = {1 / b:.2f} um/ps)")
        ns.set("a6_single_delay_slope_ps_per_um", b, eb_, "ps/um", "single-channel delay vs distance, 50 um inside")
        ns.set("a6_single_velocity_um_per_ps", 1.0 / b, eb_ / b ** 2, "um/ps")
    ax.set_xlabel("Distance from the own strip center [um]")
    ax.set_ylabel("Transverse delay of a single channel [ps]")
    pc.legend_room(ax)
    pc.panel_label(ax, "(e) single-channel delay vs distance from the strip")
    # ---- 項目 7: 遅延と振幅 (横方向は同じ RC 拡散: 遅延 = τ ln(A0/A)) ----
    ax = axes[1, 2]
    a_edges = np.concatenate([np.arange(10, 40, 3.0), np.arange(40, 91, 5.0)])
    cen, med, err, n = st.binned_profile(am, dly, a_edges, n_min=5)
    pc.eb(ax, cen, med, err, color=pc.C_BLUE, label="transverse delay (body cells, all ch)")
    ok = np.isfinite(med) & np.isfinite(err) & (err > 0)
    if ok.sum() >= 4:
        # 遅延 = τ ln A0 − τ ln A → ln A について直線
        c0, e0, c1, e1, chi2 = st.wlinfit(np.log(cen[ok]), med[ok], np.maximum(err[ok], 0.3))
        tau = -c1
        if tau > 0:
            a0 = float(np.exp(c0 / tau))
            aa = np.linspace(cen[ok].min(), cen[ok].max(), 200)
            ax.plot(aa, tau * np.log(a0 / aa), "r-", lw=1.3,
                    label=f"tau ln(A0/A): tau = {tau:.0f} +- {e1:.0f} ps, A0 = {a0:.0f} mV")
            ns.set("a7_delay_log_tau_ps", tau, e1, "ps", "transverse delay = tau ln(A0/A)")
            ns.set("a7_delay_log_A0_mV", a0, unit="mV")
    ax.set_xlabel("Amplitude MPV of the channel [mV]")
    ax.set_ylabel("Transverse delay of the channel [ps]")
    pc.legend_room(ax)
    pc.panel_label(ax, "(f) item 7: transverse delay vs amplitude")
    pc.savefig(fig, outdir, "fig6_7_propagation.png")


# ----------------------------------------------------------------------
def run(scan, red, outdir: Path, opts=None):
    opts = opts or {}
    r = pc.Red(red, opts.get("x_off", 0.0), opts.get("y_off", 0.0), opts.get("pitch", 500.0),
               opts.get("body_x", (-9900.0, -700.0)))
    g = pc.Grid(r.X, r.Y)
    cen, cinfo = pc.strip_centers(r, g)
    f = pc.fold(r, cen)
    cen0 = np.median(cen, axis=0)
    ns = pc.Numbers()
    print(f"  grid: X {g.xc.size} columns x Y {g.yc.size} rows; strip centers [um]: "
          + ", ".join(f"{ch} {v:+.0f}" for ch, v in zip(r.chs, cen0)))
    fig_amplitude(r, g, f, ns, outdir)
    fig_rise(r, g, f, ns, outdir)
    fig_jitter(r, g, f, ns, outdir, cen0)
    fig_position(r, g, f, ns, outdir, cen0)
    fig_time(r, g, f, ns, outdir, cen0)
    fig_propagation(r, g, f, ns, outdir)
    extra = [f"grid: X {g.xc.size} columns ({g.xc.min():.0f}..{g.xc.max():.0f} um) x Y {g.yc.size} rows "
             f"({g.yc.min():.0f}..{g.yc.max():.0f} um)",
             f"body: X in [{r.body_x[0]:.0f}, {r.body_x[1]:.0f}] um" + ("" if (~r.body()).any() else " (all columns used)")]
    extra += [f"strip {ch}: {txt}" for ch, txt in cinfo.items()]
    pc.finish(scan, r, ns, outdir, extra)
    if not opts.get("no_template", False):
        import template_pipeline
        template_pipeline.run_strip_pipeline(scan, int(opts.get("workers", 8)), float(opts.get("y_off", 0.0)),
                                             bool(opts.get("force_template", False)))
    return ns
