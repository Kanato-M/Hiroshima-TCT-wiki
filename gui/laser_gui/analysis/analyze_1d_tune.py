# -*- coding: utf-8 -*-
"""1d_tune スキャン (位置の 1D 走査 × レーザー強度 TUNE) の簡易解析。

位置依存と振幅依存を同時に見る。解析ノート matome の参照値である 1/A 則 (σ_jit = p0/A + p1) は
この種類のスキャン (1dYtune) から求めたもの。

出力 (results\\<スキャン名>\\):
  fig1_maps.png              位置 × TUNE のマップ: A_lead と leading の σ_t
  fig2_profiles_by_tune.png  TUNE ごとの位置プロファイル (A_lead、leading の σ_t)
  fig3_jitter.png            ジッター (3 定義) 対 振幅 (全点・全 ch) と 1/A 則のフィット
  fig5_time_resolution.png   単ストリップ σ_t 対 振幅、参照曲線とフィット
  fig2b_rise_time.png        立ち上がり 10–90 % / 40–60 % 対 振幅
  fig4_position_vs_tune.png  (Y 走査で隣り合う ch があるとき) TUNE ごとの η 較正曲線・実効ピッチ・位置分解能
  fig_waveforms.png / summary.csv / summary.txt / numbers.json

単体実行: python analyze_1d_tune.py <scan_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_common as pc  # noqa: E402
import position as pos  # noqa: E402
import stats as st  # noqa: E402
from analyze_1dy import jitter_vs_amp, sigma_vs_amp  # noqa: E402
from analyze_tune import rise_vs_amp  # noqa: E402
from plot_common import plt  # noqa: E402


def run(scan, red, outdir: Path, opts=None):
    opts = opts or {}
    r = pc.Red(red, opts.get("x_off", 0.0), opts.get("y_off", 0.0), opts.get("pitch", 500.0))
    ns = pc.Numbers()
    axis = scan.axis_letter(0)
    p = {"X": r.X, "Y": r.Y, "Z": r.Z}[axis]
    plabel = {"X": "X along strip [um]", "Y": "Y across strips [um]", "Z": "Z (focus) [um]"}[axis]
    pcen, ip = pc.cluster_axis(p)
    tcen, it = pc.cluster_axis(r.tune, tol=0.2)
    cmap = plt.get_cmap("viridis")
    tcol = [cmap(k / max(tcen.size - 1, 1)) for k in range(tcen.size)]
    lc = r.lead_channel()
    lead_ok = r.take("hit_frac", lc) >= pc.HIT_FRAC_MIN
    a_lead = r.ev("amp_lead_mpv_mV")
    s_lead = np.where(lead_ok, r.ev("sigma_lead_ps"), np.nan)

    # ---------------- fig1: 位置 × TUNE のマップ ----------------
    pe, te = pc.edges_from_centers(pcen), pc.edges_from_centers(tcen)
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.0))
    for ax, v, lab, rng in ((axes[0], a_lead, "Leading amplitude MPV [mV]", (0, None)),
                            (axes[1], s_lead, "sigma_t of the leading strip [ps]", (15, 80))):
        gmap = np.full((tcen.size, pcen.size), np.nan)
        gmap[it, ip] = v
        pm = ax.pcolormesh(pe, te, np.ma.masked_invalid(gmap), cmap="viridis", shading="flat", rasterized=True,
                           vmin=rng[0], vmax=rng[1])
        fig.colorbar(pm, ax=ax, pad=0.02).set_label(lab)
        ax.set_xlabel(plabel)
        ax.set_ylabel("Laser TUNE [%]")
        pc.panel_label(ax, lab.split(" [")[0])
    pc.savefig(fig, outdir, "fig1_maps.png")

    # ---------------- fig2: TUNE ごとの位置プロファイル ----------------
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.2))
    for k, tv in enumerate(tcen):
        m = it == k
        o = np.argsort(p[m])
        pc.eb(axes[0], p[m][o], a_lead[m][o], r.ev("amp_lead_err_mV")[m][o], color=tcol[k], ms=2.5, label=f"{tv:.0f}%")
        pc.eb(axes[1], p[m][o], s_lead[m][o], r.ev("sigma_lead_err_ps")[m][o], color=tcol[k], ms=2.5, label=f"{tv:.0f}%")
        ns.set(f"amp_lead_max_tune{tv:.0f}_mV", np.nanmax(a_lead[m]) if np.isfinite(a_lead[m]).any() else np.nan,
               unit="mV", note="largest leading amplitude MPV along the scan")
    for ax, yl, top in ((axes[0], "Leading amplitude MPV [mV]", None), (axes[1], "sigma_t of the leading strip [ps]", 100)):
        ax.set_xlabel(plabel)
        ax.set_ylabel(yl)
        ax.set_ylim(0, top)
        pc.legend_room(ax, ncol=min(tcen.size, 5), title="TUNE")
    pc.panel_label(axes[0], "(a) amplitude profile for each TUNE")
    pc.panel_label(axes[1], "(b) time resolution of the leading strip for each TUNE")
    pc.savefig(fig, outdir, "fig2_profiles_by_tune.png")

    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.0))
    rise_vs_amp(axes, r, ns)
    pc.savefig(fig, outdir, "fig2b_rise_time.png")

    # ---------------- fig3 / fig5: 振幅依存 (全点・全 ch) ----------------
    fig, ax = plt.subplots(figsize=(8.5, 5.4))
    jitter_vs_amp(ax, r, ns)
    pc.panel_label(ax, "jitter vs amplitude and the 1/A law (all positions and TUNE values)")
    pc.savefig(fig, outdir, "fig3_jitter.png")
    fig, ax = plt.subplots(figsize=(8.5, 5.4))
    sigma_vs_amp(ax, r, ns)
    pc.panel_label(ax, "single-strip time resolution vs amplitude")
    pc.savefig(fig, outdir, "fig5_time_resolution.png")

    # ---------------- fig4: 位置再構成の強度依存 (Y 走査のみ) ----------------
    if axis == "Y" and r.pairs:
        # ストリップ中心は最も強い TUNE のプロファイルから求める (全 TUNE で共通)
        k_hi = int(np.nanargmax([np.nanmax(a_lead[it == k]) if np.isfinite(a_lead[it == k]).any() else -1
                                 for k in range(tcen.size)]))
        m = (it == k_hi) & r.ok
        cen1 = pc.fill_centers(pc.find_strip_centers(r.Y[m], r.q("amp_mean_mV")[m]), r.chs, r.pitch)
        f = pc.fold(r, np.tile(cen1, (r.npt, 1)))
        out, lines = pos.analyze(r, f, line_id=it)
        gi = int(np.argmax([sum(np.isfinite(lines.get((k, g), {}).get("pitch_eff", np.nan)) for k in range(tcen.size))
                            for g in range(len(r.pairs))]))
        pn = r.pair_names[gi]
        fig, axes = plt.subplots(1, 3, figsize=(19.0, 5.2))
        ax = axes[0]
        for k, tv in enumerate(tcen):
            rows = np.where((it == k) & (f["gap"] == gi) & np.isfinite(out["eta_mean"]))[0]
            rows = rows[np.argsort(f["u"][rows])]
            pc.eb(ax, f["u"][rows], out["eta_mean"][rows], out["eta_err"][rows], color=tcol[k], ms=2.5, label=f"{tv:.0f}%")
        ax.set_xlabel("Distance from the strip-A center, u [um]")
        ax.set_ylabel("Mean eta = A_B / (A_A + A_B)")
        ax.set_ylim(0, 1)
        pc.legend_room(ax, ncol=min(tcen.size, 5), title="TUNE")
        pc.panel_label(ax, f"(a) eta calibration curves, gap {pn[:2]}-{pn[2:]}")
        asum, pit, pite, res, rese = [], [], [], [], []
        for k in range(tcen.size):
            sel = (it == k) & (f["gap"] == gi) & (f["u"] >= 50.0) & (f["u"] <= f["width"] - 50.0)
            m_, e_, _ = st.median_sem(out["sig_test"][sel])
            ln = lines.get((k, gi), {})
            asum.append(np.nanmedian(r.ev("amp_ls_mpv_mV")[sel]) if sel.any() else np.nan)
            pit.append(ln.get("pitch_eff", np.nan))
            pite.append(ln.get("pitch_eff_err", np.nan))
            res.append(m_)
            rese.append(e_)
            ns.set(f"a4_pitch_eff_tune{tcen[k]:.0f}_um", pit[-1], pite[-1], "um", f"gap {pn}")
            ns.set(f"a4_posres_test_tune{tcen[k]:.0f}_um", m_, e_, "um", f"gap {pn}, median 50 um inside the gap")
        asum = np.array(asum)
        pc.eb(axes[1], asum, pit, pite, color=pc.C_BLUE, label=f"gap {pn[:2]}-{pn[2:]}")
        axes[1].axhline(r.pitch, color=pc.C_GRAY, ls="--", lw=1.0, label=f"nominal pitch {r.pitch:.0f} um")
        axes[1].set_xlabel("A_lead + A_sub MPV in the gap [mV]")
        axes[1].set_ylabel("Effective pitch 1 / (d eta / du) [um]")
        pc.legend_room(axes[1])
        pc.panel_label(axes[1], "(b) effective pitch vs signal size")
        pc.eb(axes[2], asum, res, rese, color=pc.C_BLUE, label="position resolution (test)")
        ok = np.isfinite(asum) & np.isfinite(res) & (np.array(rese) > 0)
        if ok.sum() >= 3:
            # ノイズが支配なら位置分解能 ∝ 1/振幅
            a, ea, b, eb_, chi2 = st.wlinfit(1.0 / asum[ok], np.array(res)[ok], np.array(rese)[ok])
            xx = np.linspace(asum[ok].min(), asum[ok].max(), 100)
            axes[2].plot(xx, a + b / xx, "r-", lw=1.2, label=f"fit: {a:.1f} + {b:.0f}/A um")
            ns.set("a4_posres_vs_amp_const_um", a, ea, "um", "fit sigma_y = const + k / (A_lead + A_sub)")
            ns.set("a4_posres_vs_amp_k_um_mV", b, eb_, "um mV")
        axes[2].set_xlabel("A_lead + A_sub MPV in the gap [mV]")
        axes[2].set_ylabel("Position resolution (test) [um]")
        axes[2].set_ylim(0, None)
        pc.legend_room(axes[2])
        pc.panel_label(axes[2], "(c) position resolution vs signal size")
        pc.savefig(fig, outdir, "fig4_position_vs_tune.png")

    extra = [f"scan axis: {axis} {np.nanmin(p):.0f}..{np.nanmax(p):.0f} um ({pcen.size} positions) x TUNE "
             + ", ".join(f"{v:.0f}" for v in tcen) + " %"]
    pc.finish(scan, r, ns, outdir, extra)
    return ns
