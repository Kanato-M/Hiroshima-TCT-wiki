# -*- coding: utf-8 -*-
"""Feature extraction from segment waveforms and reduction of one scan point (numpy only, vectorized).

The calculation follows the same rules as the "matome" analysis note (laser_test/analysis/results/mip/combined/matome).
The original implementation is analysis/src/aclgad_tct/waveform.py (extract_features),
analysis/macro/matome/mip2d_common.py (extras_point) and mip2d_atot.py. To keep the numbers identical,
always compare both when you change a definition.

Rules:
  - Baseline and noise: mean and standard deviation (ddof=1) of the first baseline_frac (20 %) of the samples
  - Amplitude: maximum sample after baseline subtraction (after flipping to a positive pulse)
  - Charge: gate integral from peak −3 ns to peak +4 ns, divided by 50 Ω [pC]
  - ToA: CFD 50 % crossing with linear interpolation. Search backward from the peak for "the last sample below the threshold".
    The time origin is the TRIGTIME of each segment (trig_offsets).
    ★Using horiz_offset from the header adds a fake jitter of σ≈29 ps (see trc_reader)★
  - Rise time: 10–90 % and 40–60 % (same linear interpolation)
  - Jitter: σ_noise / (dV/dt). dV/dt is 0.2A/t(40–60), 0.8A/t(10–90), or the local slope of the CFD50 crossing interval
  - Quality cut: noise RMS < max(4 mV, 3 × global noise reference), no saturation, no multiple pulses
  - hit: amplitude > max(8 mV, 5 × global noise reference) and passes the quality cut
  - The representative value is the MPV (mpv.py, error from bootstrap); the width is a ±1.5σ core Gaussian (stats.py)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from mpv import mpv_estimate
from stats import fit_gaussian_core
from trc_reader import TrcWave

# Summary quantities per point × channel (columns of q_<CH> in reduced.npz, branch names of the ROOT points tree)
QUANT = ("n_seg", "polarity", "baseline_mV", "noise_med_mV", "q_frac", "bad_frac",
         "hit_frac", "hit_frac_err", "n_hit", "n_time",
         "amp_mpv_mV", "amp_mpv_err_mV", "amp_mean_mV", "amp_std_mV",
         "q_mpv_pC", "q_mpv_err_pC",
         "toa_mu_ps", "toa_mu_err_ps", "sigma_core_ps", "sigma_core_err_ps",
         "rise1090_mpv_ps", "rise1090_err_ps", "rise4060_mpv_ps", "rise4060_err_ps",
         "jit_4060_mpv_ps", "jit_4060_err_ps", "jit_1090_mpv_ps", "jit_1090_err_ps",
         "jit_cfd50_mpv_ps", "jit_cfd50_err_ps",
         "tot_mean_ns", "peak_t_mean_ns", "lead_frac")
# Quantities per point (event level)
EV_QUANT = ("amp_lead_mpv_mV", "amp_lead_err_mV", "amp_sub_mpv_mV", "amp_sub_err_mV",
            "amp_ls_mpv_mV", "amp_ls_err_mV",        # A_lead + A_sub (main definition of the "total amplitude")
            "amp_tot_mpv_mV", "amp_tot_err_mV",      # amplitude sum of all channels (for reference; includes the positive bias of channels with no signal)
            "amp_3rd_mpv_mV", "amp_4th_mpv_mV",
            "q_sum_mpv_pC", "q_sum_err_pC",          # charge sum of all channels
            "n_strip_mean", "sigma_lead_ps", "sigma_lead_err_ps", "q_all_frac")
# Neighbor pair (A = channel with the smaller number, B = larger; η = A_B/(A_A+A_B), Δt = t_B − t_A)
PAIR_QUANT = ("dt_mpv_ps", "dt_err_ps", "n_pair",
              "var_a_ps2", "var_b_ps2", "var_d_ps2", "var_cw2_ps2",
              "evar_a", "evar_b", "evar_d", "evar_cw2", "n_pair_time")
ETA_NBIN = 500                    # number of bins for η ∈ [0, 1] (kept separately for even/odd segments)
JIT_NBIN, JIT_MAX_PS = 150, 150.0  # per-event jitter distribution of the leading strip (1 ps steps)


@dataclass
class FeatureConfig:
    baseline_frac: float = 0.20     # baseline window (fraction at the start)
    gate_pre_ns: float = 3.0        # charge gate: before the peak
    gate_post_ns: float = 4.0       # charge gate: after the peak
    r_term: float = 50.0            # termination resistance [Ω]
    cfd_frac: float = 0.5           # CFD fraction for the ToA
    hit_thr_mV: float = 8.0         # hit: amplitude > max(this, hit_noise_factor × global noise reference)
    hit_noise_factor: float = 5.0
    noise_abs_mV: float = 4.0       # quality: noise < max(this, noise_factor × global noise reference)
    noise_factor: float = 3.0
    amp_sum_min_mV: float = 40.0    # lower limit of the pair amplitude sum used for η
    tot_thr_mV: float = 10.0        # fixed ToT threshold
    sat_margin_lsb: int = 4         # saturation: within this many LSB of the ADC edge
    sat_min_samples: int = 2
    multipulse_frac: float = 0.30   # multiple pulses: a peak outside the gate larger than this fraction of amp
    multipulse_snr: float = 5.0
    n_min: int = 50                 # minimum number of events required for the MPV / core Gaussian
    n_boot: int = 30                # number of bootstrap samples for the MPV error (matome uses 100; the value does not depend on it)

    def as_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------
# Per-segment features
# ----------------------------------------------------------------------
def _cross_leading(w, ipk, level, t0, dt, return_slope=False):
    """Time at which w crosses level upward on the rising edge (linear interpolation).

    Search before the peak for "the last sample below level" (same rule as aclgad_tct.waveform).
    NaN if there is no crossing. With return_slope=True also return the slope of the crossing interval.
    """
    n, ns = w.shape
    ar = np.arange(ns)
    below = (w < level[:, None]) & (ar[None, :] < ipk[:, None])
    has = below.any(axis=1)
    j = ns - 1 - np.argmax(below[:, ::-1], axis=1)
    j = np.clip(j, 0, ns - 2)
    rows = np.arange(n)
    w0, w1 = w[rows, j], w[rows, j + 1]
    slope = (w1 - w0) / dt
    valid = has & (slope > 0)
    frac = np.where(valid, (level - w0) / np.where(slope > 0, w1 - w0, 1.0), 0.0)
    t = np.where(valid, t0 + (j + frac) * dt, np.nan)
    if return_slope:
        return t, np.where(valid, slope, np.nan)
    return t


def detect_polarity(w: TrcWave, cfg: FeatureConfig) -> tuple[float, float]:
    """Direction (±1) of the larger swing of the mean waveform, and the size of that swing [V]."""
    v = w.volts()
    nb = max(int(round(cfg.baseline_frac * v.shape[1])), 4)
    mw = v.mean(axis=0)
    mbl = mw[:nb].mean()
    dn, up = float(mbl - mw.min()), float(mw.max() - mbl)
    return (-1.0, dn) if dn >= up else (1.0, up)


def segment_features(w: TrcWave, cfg: FeatureConfig, polarity: float = -1.0) -> dict:
    """Features of all segments of one file (one scan point × one channel).

    Returns (each (n_seg,)): amp [mV], noise [mV], q [pC], toa [ps], dvdt_cfd [mV/ns],
      rise1090 / rise4060 [ps], tot / peak_t [ns], baseline [mV], bad (saturation or multiple pulses)
    """
    v = w.volts().astype(np.float64)
    n, ns = v.shape
    dt = float(w.horiz_interval)                       # [s]
    t0 = np.asarray(w.trig_offsets[:n], np.float64)    # time origin of each segment [s]
    nb = max(int(round(cfg.baseline_frac * ns)), 4)
    baseline = v[:, :nb].mean(axis=1)
    noise = v[:, :nb].std(axis=1, ddof=1)
    e = polarity * (v - baseline[:, None])             # positive pulse [V]
    amp = e.max(axis=1)
    ipk = e.argmax(axis=1)

    gate_pre = int(round(cfg.gate_pre_ns * 1e-9 / dt))
    gate_post = int(round(cfg.gate_post_ns * 1e-9 / dt))
    ar = np.arange(ns)
    gate = (ar[None, :] >= (ipk - gate_pre)[:, None]) & (ar[None, :] <= (ipk + gate_post)[:, None])
    q = (e * gate).sum(axis=1) * dt / cfg.r_term * 1e12

    toa, slope = _cross_leading(e, ipk, cfg.cfd_frac * amp, t0, dt, return_slope=True)
    r1090 = _cross_leading(e, ipk, 0.9 * amp, t0, dt) - _cross_leading(e, ipk, 0.1 * amp, t0, dt)
    r4060 = _cross_leading(e, ipk, 0.6 * amp, t0, dt) - _cross_leading(e, ipk, 0.4 * amp, t0, dt)

    # Saturation (edge of the ADC range) and multiple pulses (a peak outside the gate)
    gain, off = float(w.vertical_gain), float(w.vertical_offset)
    info = np.iinfo(w.y_raw.dtype)
    lo_lim = gain * info.min - off + cfg.sat_margin_lsb * gain
    hi_lim = gain * info.max - off - cfg.sat_margin_lsb * gain
    sat = ((v <= lo_lim) | (v >= hi_lim)).sum(axis=1) >= cfg.sat_min_samples
    w_out = np.where(~gate, e, 0.0)
    multi = w_out.max(axis=1) > np.maximum(cfg.multipulse_frac * amp, cfg.multipulse_snr * noise)

    tot = (e > cfg.tot_thr_mV * 1e-3).sum(axis=1) * dt * 1e9
    return {"amp": amp * 1e3, "noise": noise * 1e3, "q": q, "toa": toa * 1e12,
            "dvdt_cfd": slope * 1e-6, "rise1090": r1090 * 1e12, "rise4060": r4060 * 1e12,
            "tot": np.where(tot > 0, tot, np.nan), "peak_t": (t0 + ipk * dt) * 1e9,
            "baseline": baseline * 1e3, "bad": sat | multi}


def quality_mask(noise_mV, bad, ref_mV: float, cfg: FeatureConfig):
    return (noise_mV < max(cfg.noise_abs_mV, cfg.noise_factor * ref_mV)) & ~bad


def hit_mask(amp_mV, ref_mV: float, cfg: FeatureConfig):
    return amp_mV > max(cfg.hit_thr_mV, cfg.hit_noise_factor * ref_mV)


# ----------------------------------------------------------------------
# Reduction of one scan point
# ----------------------------------------------------------------------
def _mpv(vals, cfg: FeatureConfig):
    v = np.asarray(vals, float)
    v = v[np.isfinite(v)]
    m, e, _ = mpv_estimate(v, n_min=cfg.n_min, n_boot=cfg.n_boot)
    return m, e


def _core_var(vals_ps, cfg: FeatureConfig):
    """Core-Gaussian σ² and its error (2σ·eσ). NaN if there are too few events."""
    v = vals_ps[np.isfinite(vals_ps)]
    if v.size < cfg.n_min:
        return np.nan, np.nan
    sig, _, sig_err = fit_gaussian_core(v)
    return float(sig ** 2), (float(2.0 * sig * sig_err) if np.isfinite(sig_err) else np.nan)


def summarize_channel(ft: dict, ref_mV: float, cfg: FeatureConfig, polarity: float) -> dict:
    """Segment features of one channel → dict of QUANT (lead_frac is filled by reduce_point)."""
    out = {k: np.nan for k in QUANT}
    amp, noise = ft["amp"], ft["noise"]
    q = quality_mask(noise, ft["bad"], ref_mV, cfg)
    h = hit_mask(amp, ref_mV, cfg) & q
    out.update(n_seg=amp.size, polarity=polarity, baseline_mV=float(ft["baseline"].mean()),
               noise_med_mV=float(np.median(noise)), q_frac=float(q.mean()),
               bad_frac=float(ft["bad"].mean()), n_hit=int(h.sum()), n_time=0)
    if q.sum() >= 2:
        out["amp_mpv_mV"], out["amp_mpv_err_mV"] = _mpv(amp[q], cfg)
        out["amp_mean_mV"] = float(amp[q].mean())
        out["amp_std_mV"] = float(amp[q].std(ddof=1))
        p = h.sum() / q.sum()
        out["hit_frac"], out["hit_frac_err"] = float(p), float(np.sqrt(p * (1 - p) / q.sum()))
    out["q_mpv_pC"], out["q_mpv_err_pC"] = _mpv(ft["q"][h], cfg)
    toa = ft["toa"][h]
    toa = toa[np.isfinite(toa)]
    out["n_time"] = int(toa.size)
    if toa.size >= cfg.n_min:
        sig, mu, sig_err = fit_gaussian_core(toa)
        out["toa_mu_ps"], out["toa_mu_err_ps"] = mu, sig / np.sqrt(toa.size)
        out["sigma_core_ps"], out["sigma_core_err_ps"] = sig, sig_err
    if h.sum() >= cfg.n_min:
        r1090, r4060 = ft["rise1090"][h], ft["rise4060"][h]
        out["rise1090_mpv_ps"], out["rise1090_err_ps"] = _mpv(r1090, cfg)
        out["rise4060_mpv_ps"], out["rise4060_err_ps"] = _mpv(r4060, cfg)
        a, nz = amp[h], noise[h]
        with np.errstate(divide="ignore", invalid="ignore"):
            for key, dv in (("cfd50", ft["dvdt_cfd"][h]), ("4060", 0.2 * a / (r4060 * 1e-3)),
                            ("1090", 0.8 * a / (r1090 * 1e-3))):
                dv = np.where(np.isfinite(dv) & (dv > 0), dv, np.nan)        # [mV/ns]
                out[f"jit_{key}_mpv_ps"], out[f"jit_{key}_err_ps"] = _mpv(nz / dv * 1e3, cfg)
        out["tot_mean_ns"] = float(np.nanmean(ft["tot"][h])) if np.isfinite(ft["tot"][h]).any() else np.nan
        out["peak_t_mean_ns"] = float(np.mean(ft["peak_t"][h]))
    return out, q, h


def summarize_few(ft: dict, polarity: float) -> dict:
    """Fallback summary for a file with too few segments for the MPV (e.g. a single averaged waveform).

    No distribution can be made, so amplitude, charge, rise time and ToA are medians; errors, jitter and σ stay NaN.
    """
    out = {k: np.nan for k in QUANT}
    n = ft["amp"].size
    out.update(n_seg=n, polarity=polarity, baseline_mV=float(ft["baseline"].mean()),
               noise_med_mV=float(np.median(ft["noise"])), q_frac=1.0, bad_frac=float(ft["bad"].mean()),
               hit_frac=1.0, n_hit=n, n_time=n,
               amp_mpv_mV=float(np.median(ft["amp"])), amp_mean_mV=float(ft["amp"].mean()),
               q_mpv_pC=float(np.median(ft["q"])), toa_mu_ps=float(np.nanmedian(ft["toa"])),
               rise1090_mpv_ps=float(np.nanmedian(ft["rise1090"])),
               rise4060_mpv_ps=float(np.nanmedian(ft["rise4060"])),
               tot_mean_ns=float(np.nanmedian(ft["tot"])) if np.isfinite(ft["tot"]).any() else np.nan,
               peak_t_mean_ns=float(np.mean(ft["peak_t"])))
    return out


def reduce_point(feats: list, ref_mV, cfg: FeatureConfig, polarity: float, pairs) -> dict:
    """Reduction of one scan point (all channels). feats: outputs of segment_features in channel order (None for a missing channel).

    Returns: "ch" (nch, len(QUANT)), "ev" (len(EV_QUANT),), "pair" (npair, len(PAIR_QUANT)),
            "eta_hist" (npair, 2, ETA_NBIN), "jit_lead_hist" (2, JIT_NBIN)
    """
    nch = len(feats)
    chq = np.full((nch, len(QUANT)), np.nan)
    ev = {k: np.nan for k in EV_QUANT}
    pr = np.full((len(pairs), len(PAIR_QUANT)), np.nan)
    eta_hist = np.zeros((len(pairs), 2, ETA_NBIN), np.uint16)
    jit_hist = np.zeros((2, JIT_NBIN), np.uint16)
    res = {"ch": chq, "pair": pr, "eta_hist": eta_hist, "jit_lead_hist": jit_hist}
    few = [f is not None and f["amp"].size < cfg.n_min for f in feats]
    if any(f is None for f in feats) or any(few):
        # For a point with a missing channel, or with few segments (e.g. a single waveform averaged on the scope),
        # give only the per-channel quantities (event-level quantities need all channels and enough events)
        for c, f in enumerate(feats):
            if f is not None:
                d = summarize_few(f, polarity) if few[c] else summarize_channel(f, ref_mV[c], cfg, polarity)[0]
                chq[c] = [d[k] for k in QUANT]
        res["ev"] = np.array([ev[k] for k in EV_QUANT])
        return res
    nseg = min(f["amp"].size for f in feats)
    feats = [{k: v[:nseg] for k, v in f.items()} for f in feats]
    amp = np.stack([f["amp"] for f in feats])
    toa = np.stack([f["toa"] for f in feats])
    chg = np.stack([f["q"] for f in feats])
    qual = np.empty((nch, nseg), bool)
    hit = np.empty((nch, nseg), bool)
    jit = np.full((2, nch, nseg), np.nan)
    rows = []
    for c, f in enumerate(feats):
        d, qual[c], hit[c] = summarize_channel(f, ref_mV[c], cfg, polarity)
        rows.append(d)
        with np.errstate(divide="ignore", invalid="ignore"):
            for j, (frac, key) in enumerate(((0.2, "rise4060"), (0.8, "rise1090"))):
                dv = frac * f["amp"] / (f[key] * 1e-3)
                jit[j, c] = np.where(np.isfinite(dv) & (dv > 0), f["noise"] / dv * 1e3, np.nan)

    # ---- Event level (events where all 4 channels pass the quality cut) ----
    qall = qual.all(axis=0)
    ev["q_all_frac"] = float(qall.mean())
    seg = np.arange(nseg)
    order = np.argsort(-amp, axis=0)
    lead_c = order[0]
    # Subtract the per-point median time of each channel (this calibrates the propagation delay at that position and the channel offset)
    dres = np.full((nch, nseg), np.nan)
    for c in range(nch):
        ok = hit[c] & np.isfinite(toa[c])
        if ok.sum() >= cfg.n_min:
            dres[c, ok] = toa[c][ok] - np.median(toa[c][ok])
    if qall.sum() >= cfg.n_min:
        srt = -np.sort(-amp, axis=0)
        ev["amp_lead_mpv_mV"], ev["amp_lead_err_mV"] = _mpv(srt[0][qall], cfg)
        if nch >= 2:
            ev["amp_sub_mpv_mV"], ev["amp_sub_err_mV"] = _mpv(srt[1][qall], cfg)
            ev["amp_ls_mpv_mV"], ev["amp_ls_err_mV"] = _mpv((srt[0] + srt[1])[qall], cfg)
        ev["amp_tot_mpv_mV"], ev["amp_tot_err_mV"] = _mpv(amp.sum(axis=0)[qall], cfg)
        if nch >= 3:
            ev["amp_3rd_mpv_mV"] = _mpv(srt[2][qall], cfg)[0]
        if nch >= 4:
            ev["amp_4th_mpv_mV"] = _mpv(srt[3][qall], cfg)[0]
        nh = hit.sum(axis=0)
        sel = qall & (nh >= 1)
        if sel.sum() >= cfg.n_min:
            # For the charge the expected value of a channel with no signal is 0, so just add it
            ev["q_sum_mpv_pC"], ev["q_sum_err_pC"] = _mpv(chg.sum(axis=0)[sel], cfg)
            ev["n_strip_mean"] = float(nh[sel].mean())
        lf = np.bincount(lead_c[qall], minlength=nch) / qall.sum()
        for c in range(nch):
            rows[c]["lead_frac"] = float(lf[c])
        vl, evl = _core_var(dres[lead_c, seg][qall], cfg)
        if np.isfinite(vl):
            ev["sigma_lead_ps"] = float(np.sqrt(vl))
            ev["sigma_lead_err_ps"] = float(evl / (2.0 * np.sqrt(vl))) if np.isfinite(evl) else np.nan
        sel = qall & hit[lead_c, seg]
        for j in range(2):
            x = jit[j, lead_c, seg][sel]
            jit_hist[j] = np.histogram(x[np.isfinite(x)], bins=JIT_NBIN, range=(0.0, JIT_MAX_PS))[0]
    for c in range(nch):
        chq[c] = [rows[c][k] for k in QUANT]
    res["ev"] = np.array([ev[k] for k in EV_QUANT])

    # ---- Neighbor pairs ----
    pk = {k: i for i, k in enumerate(PAIR_QUANT)}
    for j, (ca, cb) in enumerate(pairs):
        qa = qual[ca] & qual[cb]
        asum = amp[ca] + amp[cb]
        se = qa & (asum > cfg.amp_sum_min_mV)
        eta = amp[cb] / np.where(asum > 0, asum, np.nan)
        for par in (0, 1):          # kept separately for cross-validation: calibrate on even segments, evaluate on odd ones
            m = se & (seg % 2 == par) & np.isfinite(eta)
            eta_hist[j, par] = np.histogram(eta[m], bins=ETA_NBIN, range=(0.0, 1.0))[0]
        both = hit[ca] & hit[cb]
        pr[j, pk["n_pair"]] = int(both.sum())
        if both.sum() >= cfg.n_min:
            pr[j, pk["dt_mpv_ps"]], pr[j, pk["dt_err_ps"]] = _mpv((toa[cb] - toa[ca])[both], cfg)
        bt = both & np.isfinite(dres[ca]) & np.isfinite(dres[cb])
        pr[j, pk["n_pair_time"]] = int(bt.sum())
        if bt.sum() < cfg.n_min:
            continue
        da, db, aa, ab = dres[ca][bt], dres[cb][bt], amp[ca][bt], amp[cb][bt]
        for name, vals in (("a", da), ("b", db), ("d", db - da),
                           ("cw2", (aa ** 2 * da + ab ** 2 * db) / (aa ** 2 + ab ** 2))):   # A² weight
            pr[j, pk[f"var_{name}_ps2"]], pr[j, pk[f"evar_{name}"]] = _core_var(vals, cfg)
    return res
