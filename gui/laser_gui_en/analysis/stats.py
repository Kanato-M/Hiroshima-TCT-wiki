# -*- coding: utf-8 -*-
"""Statistics helpers (numpy only). Collects the same rules as the "matome" analysis note.

  fit_gaussian_core  σ of a ±1.5σ core Gaussian fitted with 4 iterations (definition of the time and position resolution)
  wmean / wlinfit    inverse-variance weighted mean and line fit (errors scaled by √(χ²/ndf) if χ²/ndf > 1)
  median_sem         summary of the cell-to-cell spread (median ± 1.2533·1.4826·MAD/√N)
  binned_profile     median ± standard error in each x bin
  fit_elmore         Elmore delay of an open-ended RC line, t0 + ρc (L d − d²/2) (quadratic in d, so linear least squares)
Original implementation: analysis/macro/root_2dYX/rootwave_common.py, analysis/macro/matome/matome_common.py.
To avoid a scipy dependency, the Gaussian fit has its own Levenberg–Marquardt code.
"""
from __future__ import annotations

import numpy as np

from mpv import mpv_estimate

CORE_NSIGMA = 1.5
CORE_BIN = 1.0            # histogram bin width (ps for time, µm for position)
CORE_HALFRANGE = 200.0    # the histogram covers median ± this range


def _gauss(x, p):
    return p[2] * np.exp(-0.5 * ((x - p[0]) / p[1]) ** 2)


def _fit_gauss_lm(x, y, p0, max_iter: int = 100):
    """Unweighted least-squares Gaussian fit (mu, sigma, A). Returns (p, cov) or None.

    cov has the same definition as scipy.curve_fit(absolute_sigma=False) (residual variance × (JᵀJ)⁻¹).
    """
    p = np.array(p0, float)
    r = y - _gauss(x, p)
    chi = float(r @ r)
    lam = 1e-3
    J = None
    for _ in range(max_iter):
        z = (x - p[0]) / p[1]
        e = np.exp(-0.5 * z * z)
        J = np.stack([p[2] * e * z / p[1], p[2] * e * z * z / p[1], e], axis=1)
        A = J.T @ J
        g = J.T @ r
        try:
            step = np.linalg.solve(A + lam * np.diag(np.diag(A)), g)
        except np.linalg.LinAlgError:
            return None
        pn = p + step
        if not np.isfinite(pn).all() or pn[1] == 0:
            lam *= 10.0
            continue
        rn = y - _gauss(x, pn)
        cn = float(rn @ rn)
        if cn < chi:
            done = (chi - cn) <= 1e-12 * max(chi, 1e-300)
            p, r, chi = pn, rn, cn
            lam = max(lam / 10.0, 1e-12)
            if done:
                break
        else:
            lam *= 10.0
            if lam > 1e12:
                break
    z = (x - p[0]) / p[1]
    e = np.exp(-0.5 * z * z)
    J = np.stack([p[2] * e * z / p[1], p[2] * e * z * z / p[1], e], axis=1)
    ndf = x.size - 3
    try:
        cov = np.linalg.inv(J.T @ J) * (chi / ndf if ndf > 0 else np.nan)
    except np.linalg.LinAlgError:
        cov = np.full((3, 3), np.nan)
    return p, cov


def fit_gaussian_core(values, bin_width: float = CORE_BIN, n_sigma: float = CORE_NSIGMA,
                      halfrange: float = CORE_HALFRANGE):
    """±n_sigma core-Gaussian fit. Returns (sigma, mu, sigma_err) (same steps as rootwave_common)."""
    values = np.asarray(values, float)
    values = values[np.isfinite(values)]
    if values.size < 10:
        return np.nan, np.nan, np.nan
    med = float(np.median(values))
    n_bins = max(10, int(round(2.0 * halfrange / bin_width)))
    counts, edges = np.histogram(values, bins=n_bins, range=(med - halfrange, med + halfrange))
    centers = 0.5 * (edges[:-1] + edges[1:])
    mu, sigma, amp = med, float(np.std(values)), float(counts.max())
    sigma_err = np.nan
    for _ in range(4):
        mask = (centers > mu - n_sigma * sigma) & (centers < mu + n_sigma * sigma)
        if mask.sum() < 5:
            break
        fit = _fit_gauss_lm(centers[mask], counts[mask].astype(float), (mu, sigma, amp))
        if fit is None:
            break
        p, cov = fit
        mu, sigma, amp = float(p[0]), abs(float(p[1])), float(p[2])
        if np.isfinite(cov[1, 1]) and cov[1, 1] >= 0:
            sigma_err = float(np.sqrt(cov[1, 1]))
    if not np.isfinite(sigma) or abs(sigma) > 5.0 * halfrange:
        # The fit diverges for a distribution with no peak (e.g. a channel with no signal). The value is not usable, so return NaN
        return np.nan, np.nan, np.nan
    return abs(sigma), mu, sigma_err


def mpv(vals, n_min: int = 50, n_boot: int = 100):
    """(MPV, bootstrap error, N)."""
    v = np.asarray(vals, float)
    return mpv_estimate(v[np.isfinite(v)], n_min=n_min, n_boot=n_boot)


def wmean(vals, errs):
    """Inverse-variance weighted mean. Errors scaled by √(χ²/ndf) if χ²/ndf > 1. Returns (mean, err, n)."""
    v, e = np.asarray(vals, float), np.asarray(errs, float)
    ok = np.isfinite(v) & np.isfinite(e) & (e > 0)
    if ok.sum() == 0:
        return np.nan, np.nan, 0
    v, e = v[ok], e[ok]
    w = 1.0 / e ** 2
    m = float((w * v).sum() / w.sum())
    err = float(1.0 / np.sqrt(w.sum()))
    if v.size > 1:
        chi2 = float((w * (v - m) ** 2).sum() / (v.size - 1))
        if chi2 > 1:
            err *= np.sqrt(chi2)
    return m, err, int(v.size)


def median_sem(vals):
    """Median ± 1.2533·1.4826·MAD/√N (to summarize the spread between points/cells). Returns (median, err, n)."""
    v = np.asarray(vals, float)
    v = v[np.isfinite(v)]
    if v.size < 2:
        return (float(v[0]) if v.size else np.nan), np.nan, int(v.size)
    med = float(np.median(v))
    return med, float(1.2533 * 1.4826 * np.median(np.abs(v - med)) / np.sqrt(v.size)), int(v.size)


def wlinfit(x, y, ye):
    """Weighted line fit y = a + b x. Returns (a, ea, b, eb, chi2_ndf)."""
    x, y, ye = np.asarray(x, float), np.asarray(y, float), np.asarray(ye, float)
    w = 1.0 / ye ** 2
    A = np.vstack([np.ones_like(x), x]).T
    cov = np.linalg.inv(A.T @ (A * w[:, None]))
    p = cov @ (A.T @ (w * y))
    resid = y - A @ p
    ndf = x.size - 2
    chi2_ndf = float((resid ** 2 * w).sum() / ndf) if ndf > 0 else np.nan
    err = np.sqrt(np.diag(cov))
    if np.isfinite(chi2_ndf) and chi2_ndf > 1:
        err = err * np.sqrt(chi2_ndf)
    return float(p[0]), float(err[0]), float(p[1]), float(err[1]), chi2_ndf


def binned_profile(x, vals, edges, n_min: int = 3):
    """Median ± standard error in each x bin. Returns (centers, med, err, n)."""
    x, vals = np.asarray(x, float), np.asarray(vals, float)
    cen = 0.5 * (edges[1:] + edges[:-1])
    med = np.full(cen.size, np.nan)
    err = np.full(cen.size, np.nan)
    n = np.zeros(cen.size, int)
    ok = np.isfinite(x) & np.isfinite(vals)
    idx = np.digitize(x, edges) - 1
    for i in range(cen.size):
        v = vals[ok & (idx == i)]
        n[i] = v.size
        if v.size >= n_min:
            med[i] = float(np.median(v))
            err[i] = float(1.2533 * 1.4826 * np.median(np.abs(v - med[i])) / np.sqrt(v.size))
    return cen, med, err, n


def elmore_toa(d_mm, rho_c, L, t0):
    """Elmore delay [ps]. d: distance from the readout end [mm], rho_c [ps/mm²], L [mm]."""
    return t0 + rho_c * (L * d_mm - 0.5 * d_mm ** 2)


def fit_elmore(d_mm, mu_ps, err_ps):
    """Weighted fit of ToA(d) = t0 + ρc (L d − d²/2).

    It is a quadratic in d, c0 + c1 d + c2 d² (ρc = −2 c2, L = c1/ρc, t0 = c0), so it is solved by linear least squares
    and the errors are propagated from the covariance. Errors scaled by √(χ²/ndf) if χ²/ndf > 1. Returns None if there are too few points.
    """
    d, y, e = (np.asarray(a, float) for a in (d_mm, mu_ps, err_ps))
    ok = np.isfinite(d) & np.isfinite(y) & np.isfinite(e) & (e > 0)
    d, y, e = d[ok], y[ok], e[ok]
    if d.size < 5:
        return None
    w = 1.0 / e ** 2
    A = np.vstack([np.ones_like(d), d, d ** 2]).T
    try:
        cov = np.linalg.inv(A.T @ (A * w[:, None]))
    except np.linalg.LinAlgError:
        return None
    c = cov @ (A.T @ (w * y))
    resid = y - A @ c
    ndf = d.size - 3
    chi2_ndf = float((resid ** 2 * w).sum() / ndf) if ndf > 0 else np.nan
    if np.isfinite(chi2_ndf) and chi2_ndf > 1:
        cov = cov * chi2_ndf
    rho = -2.0 * c[2]
    if not rho > 0:
        return None
    L = c[1] / rho
    # Error propagation for L = c1 / (−2 c2)
    gL = np.array([0.0, 1.0 / rho, c[1] / (2.0 * c[2] ** 2)])
    return {"rho_c": float(rho), "rho_c_err": float(2.0 * np.sqrt(cov[2, 2])),
            "L": float(L), "L_err": float(np.sqrt(gL @ cov @ gL)),
            "t0": float(c[0]), "t0_err": float(np.sqrt(cov[0, 0])),
            "chi2_ndf": chi2_ndf, "resid_rms_ps": float(np.sqrt(np.mean(resid ** 2)))}


def fit_inverse_law(a_mV, y, ye):
    """Weighted fit of σ = p0 / A + p1 (linear in 1/A). Returns (p0, e0, p1, e1, chi2_ndf)."""
    a_mV = np.asarray(a_mV, float)
    p1, e1, p0, e0, chi2 = wlinfit(1.0 / a_mV, y, ye)
    return p0, e0, p1, e1, chi2
