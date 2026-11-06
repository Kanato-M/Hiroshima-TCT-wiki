# -*- coding: utf-8 -*-
"""Signal-based automatic sensor alignment (Find Origin + tilt
coefficient calibration).

Instead of the camera, the laser-induced signal itself is used as the
position sensor: the signal vanishes on the metal electrodes and the
edges are cut off within the beam radius (a few µm), so the dips/edges
of the amplitude profile serve as a ruler with µm precision.
The amplitude is read the same way as in the autofocus: PAVA (NORM
mode, no waveform saving).

Prerequisites (state at start):
  - Laser ON (or auto-ON requested), waveforms visible with trigger EXT
  - Beam on the C1 strip, 50–200 µm inside the readout-side end (X=0)
    (a coarse approach by camera/preset is enough; an error of about
    ±100 µm is acceptable)

★Focus premise (established by measurement on 2026-09-07)★
  The better the focus, the SMALLER the amplitude: LGAD gain suppression
  caused by the increased local carrier density (plasma effect). The
  amplitude was minimal when the camera focus was best. The old
  "maximum amplitude = focus" scheme was wrong.
  The Z sweep is done **on the silicon beside the strip, where the
  signal is clearly visible** (the electrode is ~50 µm wide; directly
  above it the spot stays within the metal even when defocused, so the
  valley has no contrast — confirmed on the hardware on 2026-09-07).
  The valley is a V without a plateau and can be asymmetric (hardware
  data of 2026-10-05), so the fine mode takes the minimum of a
  Lorentzian + linear-baseline fit curve as the focus (two measurements
  at the same spot agreed within 0.1 µm; the old 50% threshold method
  scattered by 0.4–0.7 µm).

Mode "origin" (Find Origin; coarse only about 2–3 min, with fine
finishing + verification about 7–12 min):
  1. Y scan (coarse) → rough estimate of the C1 electrode center from
     the electrode dip
  2. Z sweep on the silicon probe_off (default 80 µm) beside the
     electrode → move to the Z of minimum amplitude (= focus)
  3. Y scan (fine, after focusing) → fix the electrode center Y
     ★fine finishing★ one-pulse (2 µm) steps, many reads (median), erf
     fit of both edges → electrode center with sub-µm precision. Several
     sweeps give the repeatability, a reverse sweep estimates the
     backlash
  4. X scan (toward + = pad region) → the edge where the signal
     vanishes = readout-side end of the exposed strip region (②/③
     boundary) ★fine finishing★ erf fit in the same way
  5. Z re-check on the silicon (fine: Lorentzian fit) →
     **approach edge + 632 µm (design offset ⓪+①+② = equivalent of the
     frame GND edge) from the negative side on all axes and Reset
     Logical Origin (R:W)** → this is (0,0,0). Moving X=−10 mm from the
     origin lands at the center of gap ④ (boundary of the strip regions)
     (see the X definition in scan_geometry)
  6. ★verification★ after R:W, re-measure the electrode center Y, the
     signal edge X and the focus Z precisely; if the residual of any axis
     is one pulse (2 µm) or more, correct it and redo R:W (at most
     verify_max_iter times). The final residual is kept in the result

Mode "full" (Full Calibration; coarse about 8–12 min, fine about
15–25 min) = origin followed by:
  7. At X = x_points (−5300, −9900 µm for the standard sensor): Y dip
     center → focus Z on the silicon → straight-line fit of dY/dX
     (rotation) and dZ/dX (tilt)
  8. At Y = y_points (−500, −1500 µm = C2, C4 for the standard sensor):
     likewise electrode center + focus Z → straight-line fit of dZ/dY,
     and a check of the strip pitch (in fine mode each measurement point
     is also determined by the one-pulse-step fit)
  9. Put the coefficients into self.calibration and return to the origin
     (0,0,0). Saving is done by the GUI thread (stage panel) into the
     current sensor profile

Findings on precision (analysis of the 2026-10-05 logs):
  - The electrode edge drops within one pulse (erf σ ≈ 0.5–2 µm) and
    the amplitude is 10–20 % higher just before the edge ("edge
    enhancement") → the edge fit gives the plateau a linear slope term
    to absorb it
  - The threshold method and the erf fit agree within 0.3 µm, i.e. the
    signal-based measurement itself is self-consistent below 1 µm; a
    difference of several µm from a manual (camera) origin most likely
    comes from differences in the reference definition (offset between
    the crosshair and the spot, the design value of pad edge → frame
    edge, approach direction = backlash)
  - A 3 µm step is rounded to the 2 µm/pulse grid; recording the
    commanded value adds a sawtooth error of up to 1 µm → the actual
    pulse position is recorded

The sensor geometry (frame edge → exposed-region offset, measurement
points, probe positions, pitch) is received through params (from the
profile in sensors.py). No edits of this file are needed when the board
or the sensor is changed.

Pixel sensors (params["sensor_type"] == "pixel", added 2026-10-16), Find Origin:
  the bonding pad (signal-free square of side pad_um) is the ruler.
  1. Y sweep across the pad → dip center = first estimate of the pad center Y
  2. Z (minimum amplitude) on the silicon beside the pad (y_probe_off below the
     pad center, inside the pixel)
  3. fine Y dip → pad center Y
  4. X sweep along the pad center row → dip center = pad center X (erf fits in
     fine mode)
  5. Z re-check → **R:W at the +X/+Y corner of the pad (center + pad/2, + pad/2)**.
     Pixel center = pad center = (−pad/2, −pad/2); neighboring pixels at ±pitch
  6. verification: Y dip in the column X = −pad/2, X dip in that row, Z re-measured
  Full Calibration (params["pads"] = [[CH, x, y], ...] = pad centers of the other
  readout pixels): each pad is measured on its CH (Y → X → Z); a plane fit gives
  dZ/dX and dZ/dY, the pads in the same row as the reference give dY/dX, and the
  pad spacing checks the pitch (the lever arm is one pitch, so the coefficients
  are coarser than for strips).

On abort/error: before R:W return to the start position, after R:W
return to the origin (0,0,0).
"""

from __future__ import annotations

import csv
import datetime
import math
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from .scan_worker import frange


# ----------------------------------------------------------------------
# Profile analysis (pure functions: unit-testable)
# ----------------------------------------------------------------------
def _smooth3(a: np.ndarray) -> np.ndarray:
    """3-point moving average with the end points replicated as padding.

    ★np.convolve(mode='same') is not used: it attenuates the end points
    to 2/3, and for a shallow valley the end points then become falsely
    "below threshold" and cause a wrong range-edge verdict★
    """
    a = np.asarray(a, float)
    if len(a) < 3:
        return a.copy()
    pad = np.concatenate([a[:1], a, a[-1:]])
    return (pad[:-2] + pad[1:-1] + pad[2:]) / 3.0



def dip_center(pos: np.ndarray, amp: np.ndarray,
               min_width: float = 4.0, max_width: float = 200.0,
               max_ratio: float = 0.5, min_depth: float = 0.0):
    """Return the center of the dip (valley) of an amplitude profile
    (50% threshold method, for the coarse search).

    Both edges are linearly interpolated at the 50% threshold between
    the plateau and the valley, and their midpoint is taken as the dip
    center. Used both for the electrode dip in Y (almost zero on the
    metal) and for the gain-suppression valley of the Z focus (shallow).
    max_ratio: largest valley/plateau ratio accepted as a dip. Electrode
      detection uses 0.5, the coarse detection before focusing 0.75
      (defocus makes the dip shallower), and the Z focus valley 0.9
      (gain suppression gives a shallow valley of a few tens of %).
    min_depth: minimum valley depth [V] (so that noise is not mistaken
      for a dip).
    Returns: (center, width, lo_edge, hi_edge). ValueError if not found.
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    if len(pos) < 7:
        raise ValueError("Too few points")
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    # smooth the noise with a 3-point moving average (end points use
    # replicated padding)
    sm = _smooth3(amp)
    hi = float(np.percentile(sm, 85))
    lo = float(sm.min())
    if hi < 0.005:
        raise ValueError(
            f"No signal (plateau={hi * 1e3:.1f} mV). "
            "Check the beam position, the focus and that the laser is ON")
    if lo > max_ratio * hi or hi <= 0:
        raise ValueError(
            f"No clear dip (min={lo * 1e3:.1f} mV, "
            f"plateau={hi * 1e3:.1f} mV). There may be no electrode in "
            "the range, or the focus is bad")
    if hi - lo < min_depth:
        raise ValueError(
            f"Valley too shallow (depth {(hi - lo) * 1e3:.1f} mV < "
            f"{min_depth * 1e3:.1f} mV). Possibly noise")
    thr = 0.5 * (hi + lo)
    imin = int(np.argmin(sm))
    below = sm < thr
    if not below[imin]:
        raise ValueError("Threshold calculation for dip detection failed")
    # left edge of the dip (falling crossing)
    i = imin
    while i > 0 and below[i - 1]:
        i -= 1
    if i == 0:
        raise ValueError("Dip touches the left edge of the range. "
                         "Widen the range or shift the center")
    f = (sm[i - 1] - thr) / (sm[i - 1] - sm[i])
    e1 = pos[i - 1] + f * (pos[i] - pos[i - 1])
    # right edge (rising crossing)
    j = imin
    while j < len(sm) - 1 and below[j + 1]:
        j += 1
    if j == len(sm) - 1:
        raise ValueError("Dip touches the right edge of the range. "
                         "Widen the range or shift the center")
    f = (thr - sm[j]) / (sm[j + 1] - sm[j])
    e2 = pos[j] + f * (pos[j + 1] - pos[j])
    width = e2 - e1
    if not (min_width <= width <= max_width):
        raise ValueError(f"Dip width {width:.1f} µm is implausible for "
                         f"an electrode ({min_width}–{max_width} µm "
                         "expected)")
    return 0.5 * (e1 + e2), width, e1, e2


def edge_position(pos: np.ndarray, amp: np.ndarray,
                  falling: bool = True):
    """Return the position of a signal step edge (50% crossing; coarse).

    falling=True: the edge where the signal goes high→low with increasing
    pos (for the X origin: scan region → pad region).
    Returns: (edge_pos, hi, lo).
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    if len(pos) < 7:
        raise ValueError("Too few points")
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    sm = _smooth3(amp)
    hi = float(np.percentile(sm, 90))
    lo = float(np.percentile(sm, 10))
    if hi < 0.005:
        raise ValueError(
            f"No signal (hi={hi * 1e3:.1f} mV). "
            "Check the beam position, the focus and that the laser is ON")
    if hi <= 0 or (hi - lo) < 0.5 * hi:
        raise ValueError(
            f"No clear edge (hi={hi * 1e3:.1f}, "
            f"lo={lo * 1e3:.1f} mV). There may be no boundary in the "
            "range")
    thr = 0.5 * (hi + lo)
    above = sm >= thr
    crossings = []
    for i in range(1, len(sm)):
        if falling and above[i - 1] and not above[i]:
            f = (sm[i - 1] - thr) / (sm[i - 1] - sm[i])
            crossings.append((abs(sm[i - 1] - sm[i]),
                              pos[i - 1] + f * (pos[i] - pos[i - 1])))
        elif not falling and not above[i - 1] and above[i]:
            f = (thr - sm[i - 1]) / (sm[i] - sm[i - 1])
            crossings.append((abs(sm[i] - sm[i - 1]),
                              pos[i - 1] + f * (pos[i] - pos[i - 1])))
    if not crossings:
        raise ValueError("No edge crossing found. "
                         "Widen the sweep range")
    # take the steepest crossing (rejects noise crossings)
    return max(crossings)[1], hi, lo


def refine_extremum(zs: np.ndarray, amps: np.ndarray,
                    minimum: bool = False) -> float:
    """Position of the extremum of an amplitude curve (parabolic
    interpolation of the extremum point ±1).

    minimum=True for the minimum (focus = minimum amplitude above the
    electrode, bottom of the V).
    """
    zs = np.asarray(zs, float)
    amps = np.asarray(amps, float)
    i = int(np.argmin(amps) if minimum else np.argmax(amps))
    if 0 < i < len(zs) - 1:
        y0, y1, y2 = amps[i - 1], amps[i], amps[i + 1]
        denom = y0 - 2 * y1 + y2
        if (denom > 0) if minimum else (denom < 0):
            return float(zs[i] + 0.5 * (y0 - y2) / denom
                         * (zs[i + 1] - zs[i]))
    return float(zs[i])


# ----------------------------------------------------------------------
# Precision fits (numpy only; scipy is not installed on the control PC)
# ----------------------------------------------------------------------
_EDGE_MARKER = "edge of the range"   # identifies range-end errors (re-centering)


def _erf(x):
    """Vectorized erf (Abramowitz–Stegun 7.1.26, |error| < 1.5e-7)."""
    x = np.asarray(x, float)
    s = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-a * a)
    return s * y


def _phi(x):
    """Cumulative distribution Φ(x) of the standard normal."""
    return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)))


def _batched_lstsq(cols: np.ndarray, y: np.ndarray):
    """cols: a bundle (K, n, p) of design matrices, y: (n,) →
    coefficients (K, p) and SSE (K,).

    Solves the K normal equations at once (fast inside the grid search).
    """
    At = np.transpose(cols, (0, 2, 1))
    ata = At @ cols
    aty = At @ y
    p = cols.shape[2]
    ridge = 1e-9 * np.trace(ata, axis1=1, axis2=2)[:, None, None] / p
    coef = np.linalg.solve(ata + ridge * np.eye(p), aty[..., None])[..., 0]
    resid = y[None, :] - np.einsum("knp,kp->kn", cols, coef)
    return coef, np.sum(resid * resid, axis=1)


def _grid_min(grid: np.ndarray, sse: np.ndarray, n_pts: int, n_free: int):
    """Sub-grid minimum of an SSE grid: (position, 1σ statistical error,
    minimum SSE).

    When σ→0 (the transition fell between samples) the SSE is flat on
    the grid; the midpoint of the flat interval is returned (= the
    midpoint of the two samples is the best estimate; argmin would be
    biased to one end of the interval). Otherwise a parabola is
    interpolated and the error is estimated from the SSE curvature,
    var ≈ 2·s² / SSE''.
    """
    i = int(np.argmin(sse))
    smin = float(sse[i])
    tol = smin * 1e-4 + 1e-15
    lo = i
    while lo > 0 and sse[lo - 1] <= smin + tol:
        lo -= 1
    hi = i
    while hi < len(sse) - 1 and sse[hi + 1] <= smin + tol:
        hi += 1
    h = float(grid[1] - grid[0]) if len(grid) > 1 else 0.1
    flat = (hi - lo) * h
    if flat > 3.0 * h:
        return 0.5 * float(grid[lo] + grid[hi]), flat / math.sqrt(12.0), smin
    x = float(grid[i])
    err = float("nan")
    if 0 < i < len(grid) - 1:
        y0, y1, y2 = sse[i - 1], sse[i], sse[i + 1]
        d2 = y0 - 2 * y1 + y2
        if d2 > 0:
            x = float(grid[i] + 0.5 * (y0 - y2) / d2 * h)
            s2 = smin / max(n_pts - n_free, 1)
            err = math.sqrt(2.0 * s2 / (d2 / (h * h)))
    return x, err, smin


def fit_edge(pos, amp, falling: bool = True, sigma_max: float = 10.0) -> dict:
    """Least-squares fit of one step edge with an erf model.

    Model: s(x) = lo + (a + b·(x − x0)) · Φ(∓(x − x0)/σ)
      falling=True: high→low in the +x direction. a, b are the plateau
      level at the edge position and its slope (absorbs the charge-sharing
      gradient and the amplitude enhancement just before the edge).
    x0 (0.1 µm steps) and σ (geometric series) are grid-searched (lo, a,
    b are linear least squares at each grid point) and x0 is refined to
    sub-grid.
    Returns dict: center, sigma, hi (plateau level at x0), lo, slope,
      rms [V], err (1σ statistical error of x0 [µm]), n
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    if len(pos) < 6:
        raise ValueError("Too few points")
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    hi_p = float(np.percentile(amp, 90))
    lo_p = float(np.percentile(amp, 10))
    if hi_p < 0.005:
        raise ValueError(f"No signal (hi={hi_p * 1e3:.1f} mV)")
    if hi_p - lo_p < 0.5 * hi_p:
        raise ValueError(f"No clear edge (hi={hi_p * 1e3:.1f}, "
                         f"lo={lo_p * 1e3:.1f} mV)")
    step = float(np.median(np.diff(pos)))
    sgn = -1.0 if falling else 1.0
    x0_grid = np.arange(pos[0] + 0.5 * step, pos[-1] - 0.5 * step + 1e-9,
                        0.1)
    sig_grid = np.geomspace(0.5, sigma_max, 14)
    best = None
    for sig in sig_grid:
        dx = pos[None, :] - x0_grid[:, None]
        ph = _phi(sgn * dx / sig)
        cols = np.stack([np.ones_like(ph), ph, dx * ph], axis=2)
        coef, sse = _batched_lstsq(cols, amp)
        i = int(np.argmin(sse))
        if best is None or sse[i] < best[0]:
            best = (float(sse[i]), float(sig), coef[i].copy(), sse)
    sse_min, sig, coef, sse_arr = best
    x0, err, _ = _grid_min(x0_grid, sse_arr, len(pos), 5)
    lo, a, b = (float(v) for v in coef)
    if x0 < pos[0] + step or x0 > pos[-1] - step:
        raise ValueError(f"Edge touches the {_EDGE_MARKER}. "
                         "Widen the range or shift the center")
    if sig >= sig_grid[-1] * 0.999:
        raise ValueError(f"Edge too blurred (σ≈{sig:.1f} µm). "
                         "Possibly bad focus")
    if a <= 0:
        raise ValueError("Invalid edge fit (plateau level ≤ 0)")
    return {"center": float(x0), "sigma": float(sig), "hi": a, "lo": lo,
            "slope": b, "rms": math.sqrt(sse_min / len(pos)), "err": err,
            "n": int(len(pos))}


def fit_dip(pos, amp, coarse=None, min_width: float = 4.0,
            max_width: float = 200.0, sigma_max: float = 10.0) -> dict:
    """Fit the left and right edges of a dip (valley) separately with
    erf and return the center.

    coarse: rough estimate (left edge, right edge) (if absent, the 50%
    threshold method of dip_center is used). Points left of the coarse
    center are fitted as falling, points right of it as rising (no bias
    even if the plateau levels differ on the two sides).
    Returns dict: center, width, err, left, right (dicts of fit_edge)
    """
    pos = np.asarray(pos, float)
    amp = np.asarray(amp, float)
    order = np.argsort(pos)
    pos, amp = pos[order], amp[order]
    if coarse is None:
        _, _, e1, e2 = dip_center(pos, amp, min_width, max_width,
                                  max_ratio=0.75)
    else:
        e1, e2 = coarse
    mid = 0.5 * (e1 + e2)
    lm = pos <= mid
    rm = pos >= mid
    left = fit_edge(pos[lm], amp[lm], falling=True, sigma_max=sigma_max)
    right = fit_edge(pos[rm], amp[rm], falling=False, sigma_max=sigma_max)
    width = right["center"] - left["center"]
    if not (min_width <= width <= max_width):
        raise ValueError(f"Dip width {width:.1f} µm is implausible for "
                         f"an electrode ({min_width}–{max_width} µm "
                         "expected)")
    errs = [left["err"], right["err"]]
    err = 0.5 * math.hypot(*errs) if all(np.isfinite(errs)) else float("nan")
    return {"center": 0.5 * (left["center"] + right["center"]),
            "width": width, "err": err, "left": left, "right": right}


def fit_focus(zs, amps, min_depth: float = 0.004,
              gamma_range=(2.0, 40.0)) -> dict:
    """Fit the valley (gain suppression) of a Z sweep with a Lorentzian
    plus a linear baseline.

    A(z) = a + b·(z − z̄) − c / (1 + ((z − z0)/γ)²)
    (corresponds to the peak intensity ∝ 1/(1+(z/zR)²) of a Gaussian beam
    near the focus; the linear term b absorbs the left/right asymmetry of
    the valley observed on the hardware). z0 (0.1 µm steps) and γ
    (geometric series) are grid-searched; a, b, c are linear least
    squares at each grid point.
    **The focus (center) is the minimum of the fit curve itself** (on the
    2026-10-05 hardware data two measurements at the same spot agreed
    within 0.1 µm; the Lorentzian center z0 and the 50% threshold method
    scattered by 0.4–0.7 µm).
    Returns dict: center, z0, gamma, depth [V], depth_ratio, base, slope,
      rms, err (statistical error of z0), n
    """
    zs = np.asarray(zs, float)
    amps = np.asarray(amps, float)
    if len(zs) < 7:
        raise ValueError("Too few points")
    order = np.argsort(zs)
    zs, amps = zs[order], amps[order]
    step = float(np.median(np.diff(zs)))
    zbar = float(zs.mean())
    z0_grid = np.arange(zs[0] + 0.5 * step, zs[-1] - 0.5 * step + 1e-9, 0.1)
    g_grid = np.geomspace(gamma_range[0], gamma_range[1], 14)
    best = None
    for g in g_grid:
        lor = 1.0 / (1.0 + ((zs[None, :] - z0_grid[:, None]) / g) ** 2)
        cols = np.stack([np.ones_like(lor),
                         np.broadcast_to(zs - zbar, lor.shape), -lor], axis=2)
        coef, sse = _batched_lstsq(cols, amps)
        sse = np.where(coef[:, 2] > 0, sse, np.inf)   # valleys (c > 0) only
        i = int(np.argmin(sse))
        if np.isfinite(sse[i]) and (best is None or sse[i] < best[0]):
            best = (float(sse[i]), float(g), coef[i].copy(), sse)
    if best is None:
        raise ValueError("No valley found (amplitude is monotonic)")
    sse_min, g, coef, sse_arr = best
    z0, err, _ = _grid_min(z0_grid, sse_arr, len(zs), 5)
    a, b, c = (float(v) for v in coef)
    if c < min_depth:
        raise ValueError(f"Valley too shallow (depth {c * 1e3:.1f} mV < "
                         f"{min_depth * 1e3:.1f} mV). Possibly noise")
    # minimum of the fit curve (0.05 µm grid)
    zz = np.arange(zs[0], zs[-1] + 1e-9, 0.05)
    curve = a + b * (zz - zbar) - c / (1.0 + ((zz - z0) / g) ** 2)
    center = float(zz[int(np.argmin(curve))])
    if center < zs[0] + step or center > zs[-1] - step:
        raise ValueError(f"Valley touches the {_EDGE_MARKER}. "
                         "Widen the range or shift the center")
    return {"center": center, "z0": float(z0), "gamma": g, "depth": c,
            "depth_ratio": c / a if a else float("nan"),
            "base": a, "slope": b, "rms": math.sqrt(sse_min / len(zs)),
            "err": err, "n": int(len(zs))}


def _r(v, nd=2):
    """For result dicts: round to float (NaN stays as is)."""
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return v


# ----------------------------------------------------------------------
class AlignmentWorker(QThread):
    """Auto Alignment execution thread.

    params:
      mode: "origin" | "full"
      ch: scope CH whose amplitude is read ("C1".."C4")
      af_range_um / af_step_um: autofocus sweep (default ±20 / 2)
      y_half_um / y_step_um:    Y dip sweep (default ±150 / 3)
      x_back_um / x_fwd_um / x_step_um: X edge sweep (default -30/+250 / 4)
      x_probe_off_um: Y offset of the X edge sweep (from the electrode
        center, default 50. With 80 the beam leaves the Y width of the
        bond pad, the signal does not vanish and no edge appears)
      y_probe_off_um: Y offset of the Z focus (default 80)
      x_frame_offset_um: design offset signal edge (②/③ boundary) →
        frame edge
      pitch_um:  nominal strip pitch (to identify the measured pitch)
      x_points:  extra X measurement points of Full Calibration
                 (default [-5300, -9900])
      y_points:  extra Y measurement points of Full Calibration
                 [[y, readout CH], ...] (default [[-500, "C2"],
                 [-1500, "C4"]])
      coef0:     current coefficients (dzdx, dzdy, dydx, source). Used to
                 predict the measurement points and for the small
                 correction that converts the focus Z measured at the
                 probe position (beside the electrode) to the electrode
                 center (0.3 µm for the standard sensor)
      settle_s / reads / backlash_um / move_timeout_s
      laser_on:  switch the laser ON automatically at start
      reset_origin: perform R:W after Find Origin (default True)
      pre_move_xy: logical coordinates (x, y) to move to before starting.
                 When redoing from an already-aligned state this moves
                 automatically to the start position on the reference strip
      --- fine finishing (added 2026-10) ---
      fine:          True → after the coarse search, one-pulse-step fine
                     sweeps + model fits
      fine_reads:    reads per point in the fine sweeps (default 4, median)
      fine_settle_s: settling time of the fine sweeps (default 0.25)
      fine_passes:   repetitions of the fine sweeps in Find Origin
                     (default 2; the difference is reported as
                     repeatability)
      fine_margin_um: margin outside the electrode edges in the fine Y
                     sweep (default 16)
      fine_x_half_um: half-width of the fine X sweep (default 20)
      backlash_check: add one reverse sweep to estimate the backlash
                     (default True)
      verify:        re-measure the origin after R:W and correct residuals
                     of one pulse or more (default True)
      verify_max_iter: maximum number of corrections (default 2)
      csv_path:      where to record the sweep profiles (optional)

    Results read by the GUI thread after completion:
      origin_done: whether R:W was completed
      calibration: result dict of Full Calibration (None for origin only /
                   on failure)
      origin_report: dict of the fine finishing / verification results of
                   Find Origin (None in coarse mode)
    """

    progress = Signal(int, int, str)       # (points done, total, message)
    finished_align = Signal(bool, str)     # (success, message)

    def __init__(self, scope, stage, laser, params: dict, parent=None):
        super().__init__(parent)
        self.scope = scope
        self.stage = stage
        self.laser = laser
        self.p = dict(params)
        self._abort = False
        self._origin_done = False
        self.origin_done = False        # public flag read by the GUI
        self.calibration: dict | None = None
        self.origin_report: dict | None = None
        self._laser_turned_on = False
        self._warnings: list[str] = []
        self._csv_rows: list[tuple] = []
        # current amplitude readout channel. At the Y measurement points of
        # Full Calibration it switches to the channel that reads that strip
        # (see below)
        self._ch = self.p.get("ch", "C1")
        self._last_focus_exact = float("nan")
        self._last_focus_info: dict = {}
        dzdx0, dzdy0, _, _ = self.p.get("coef0", (0.0, 0.0, 0.0, ""))
        self._dzdx0 = float(dzdx0)
        self._dzdy0 = float(dzdy0)

    def abort(self):
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        self.scope.mirror_paused = True
        try:
            self._run()
        except _Aborted:
            self._restore()
            self.finished_align.emit(False, "Alignment aborted")
        except Exception as exc:
            self._restore()
            self.finished_align.emit(False, f"Alignment error: {exc}")
        finally:
            self.scope.mirror_paused = False
            self._write_csv()
            # a laser that was switched ON automatically is switched OFF
            # again for safety (same policy as scans)
            if self._laser_turned_on and self.p.get("laser_off", False):
                try:
                    self.laser.set_emission(False)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------
    def _check_abort(self):
        if self._abort:
            raise _Aborted()

    def _goto(self, x, y, z):
        self._check_abort()
        self.stage.move_to_um_relative(x, y, z)
        self.stage.wait_ready(timeout=self.p.get("move_timeout_s", 60),
                              abort_check=lambda: self._abort)
        self._check_abort()

    def _quant(self, axis: int, um: float) -> float:
        """Round µm to the pulse grid of the stage (2 µm/pulse)."""
        return float(self.stage.pulse_to_um(axis,
                                            self.stage.um_to_pulse(axis, um)))

    def _pulse_um(self, axis: int) -> float:
        """Travel of one pulse [µm] (step of the fine sweeps)."""
        return abs(float(self.stage.pulse_to_um(axis, 1))) or 2.0

    def _amp(self, reads: int | None = None) -> float:
        """PAVA amplitude read several times [V]. Median for 3 reads or
        more (robust against outliers).

        The channel is self._ch (at the Y measurement points of Full
        Calibration it switches to the channel of the target strip — with
        a fixed channel there is almost no signal on the neighboring strip
        and the detection fails. Hardware feedback of 2026-09-07).
        """
        n = int(reads or self.p.get("reads", 2))
        vals = []
        for _ in range(n):
            v, state = self.scope.measure_pava(self._ch,
                                               self.p.get("pava", "PKPK"))
            if not math.isnan(v) and state.upper() not in ("NP", "IV"):
                vals.append(v)
            time.sleep(0.05)
        if not vals:
            raise ValueError(
                f"Cannot read the amplitude of {self._ch} (no waveform?). "
                "Check that the laser is ON and the trigger is running")
        if len(vals) >= 3:
            return float(np.median(vals))
        return sum(vals) / len(vals)

    def _sweep(self, axis: int, base: list[float], offsets: list[float],
               label: str, reads: int | None = None,
               settle: float | None = None, descending: bool = False,
               tag: str = "") -> tuple[np.ndarray, np.ndarray]:
        """Sweep one axis along offsets and return (positions, amplitudes).

        Backlash protection: first go backlash_um beyond the minimum side,
        then sweep in one direction (ascending). descending=True is the
        reverse (from beyond the maximum side, descending; for the
        backlash estimate).
        The recorded position is not the commanded value but **the value
        rounded to the actual pulse grid** (a 3 µm step is rounded to the
        2 µm grid; keeping the commanded value adds a sawtooth error of up
        to 1 µm — confirmed in the 2026-10-05 logs).
        """
        backlash = self.p.get("backlash_um", 10.0)
        if settle is None:
            settle = self.p.get("settle_s", 0.15)
        offsets = sorted(offsets)
        if descending:
            offsets = offsets[::-1]
        lead = list(base)
        lead[axis] = base[axis] + offsets[0] + (backlash if descending
                                                else -backlash)
        self._goto(*lead)
        positions, amps = [], []
        n = len(offsets)
        for i, off in enumerate(offsets):
            tgt = list(base)
            tgt[axis] = self._quant(axis, base[axis] + off)
            self._goto(*tgt)
            time.sleep(settle)
            a = self._amp(reads)
            positions.append(tgt[axis])
            amps.append(a)
            if tag:
                self._csv_rows.append((tag, "XYZ"[axis], i, self._ch,
                                       tgt[0], tgt[1], tgt[2], a))
            self.progress.emit(i + 1, n,
                               f"{label} {i + 1}/{n}: "
                               f"{'XYZ'[axis]}={tgt[axis]:+.1f} µm "
                               f"{a * 1e3:.1f} mV")
        return np.array(positions), np.array(amps)

    def _approach(self, target: list[float]):
        """Approach the target from the negative side (same direction as
        the sweeps) on all axes (backlash protection)."""
        b = self.p.get("backlash_um", 10.0)
        self._goto(target[0] - b, target[1] - b, target[2] - b)
        self._goto(*target)

    def _fine_cfg(self) -> dict:
        p = self.p
        return {"reads": int(p.get("fine_reads", 4)),
                "settle": float(p.get("fine_settle_s", 0.25)),
                "passes": max(1, int(p.get("fine_passes", 2))),
                "margin": float(p.get("fine_margin_um", 16.0)),
                "x_half": float(p.get("fine_x_half_um", 20.0))}

    # ------------------------------------------------------------------
    # Fine measurements (one-pulse steps, many reads, model fits)
    # ------------------------------------------------------------------
    def _fine_dip(self, base: list[float], e1: float, e2: float,
                  label: str, passes: int, backlash_check: bool = False,
                  tag: str = "", axis: int = 1,
                  max_width: float = 200.0) -> dict:
        """Fine measurement of the electrode dip. e1/e2: coarse edges
        (absolute Y).

        Sweeps [e1−margin, e2+margin] in one-pulse steps ascending passes
        times, fit_dip for each → returns the mean center and the
        difference between passes (repeatability). With backlash_check one
        descending sweep is added and the center difference to the
        ascending sweeps is returned as the backlash estimate (the origin
        is set with an ascending approach, so the ascending value is
        used).
        """
        cfg = self._fine_cfg()
        step = self._pulse_um(axis)
        offsets = [v - base[axis] for v in frange(e1 - cfg["margin"],
                                                  e2 + cfg["margin"], step)]
        centers, widths, errs, sigmas = [], [], [], []
        for k in range(passes):
            ys, amps = self._sweep(
                axis, base, offsets, f"{label} fine {k + 1}/{passes}",
                reads=cfg["reads"], settle=cfg["settle"],
                tag=f"{tag}:asc{k + 1}")
            f = fit_dip(ys, amps, coarse=(e1, e2), max_width=max_width)
            centers.append(f["center"])
            widths.append(f["width"])
            errs.append(f["err"])
            sigmas.append(0.5 * (f["left"]["sigma"] + f["right"]["sigma"]))
        res = {"center": float(np.mean(centers)),
               "centers": [_r(c) for c in centers],
               "spread": float(max(centers) - min(centers)),
               "width": float(np.mean(widths)),
               "err": float(np.nanmean(errs)) if errs else float("nan"),
               "sigma": float(np.mean(sigmas)),
               "n_pass": passes, "n_points": len(offsets),
               "backlash": float("nan")}
        if backlash_check:
            ys, amps = self._sweep(
                axis, base, offsets, f"{label} reverse (backlash estimate)",
                reads=cfg["reads"], settle=cfg["settle"], descending=True,
                tag=f"{tag}:desc")
            try:
                fd = fit_dip(ys, amps, coarse=(e1, e2), max_width=max_width)
                res["center_desc"] = fd["center"]
                res["backlash"] = fd["center"] - res["center"]
            except ValueError as exc:
                self._warnings.append(f"{label}: reverse sweep could not "
                                      f"be fitted ({exc})")
        return res

    def _fine_edge(self, base: list[float], x_edge: float, label: str,
                   passes: int, backlash_check: bool = False,
                   tag: str = "") -> dict:
        """Fine measurement of the signal edge (X). x_edge: coarse edge
        (absolute X)."""
        cfg = self._fine_cfg()
        step = self._pulse_um(0)
        offsets = [v - base[0] for v in frange(x_edge - cfg["x_half"],
                                               x_edge + cfg["x_half"], step)]
        centers, errs, sigmas, his = [], [], [], []
        for k in range(passes):
            xs, amps = self._sweep(
                0, base, offsets, f"{label} fine {k + 1}/{passes}",
                reads=cfg["reads"], settle=cfg["settle"],
                tag=f"{tag}:asc{k + 1}")
            f = fit_edge(xs, amps, falling=True)
            centers.append(f["center"])
            errs.append(f["err"])
            sigmas.append(f["sigma"])
            his.append(f["hi"])
        res = {"center": float(np.mean(centers)),
               "centers": [_r(c) for c in centers],
               "spread": float(max(centers) - min(centers)),
               "err": float(np.nanmean(errs)) if errs else float("nan"),
               "sigma": float(np.mean(sigmas)),
               "plateau_mv": float(np.mean(his)) * 1e3,
               "n_pass": passes, "n_points": len(offsets),
               "backlash": float("nan")}
        if backlash_check:
            xs, amps = self._sweep(
                0, base, offsets, f"{label} reverse (backlash estimate)",
                reads=cfg["reads"], settle=cfg["settle"], descending=True,
                tag=f"{tag}:desc")
            try:
                fd = fit_edge(xs, amps, falling=True)
                res["center_desc"] = fd["center"]
                res["backlash"] = fd["center"] - res["center"]
            except ValueError as exc:
                self._warnings.append(f"{label}: reverse sweep could not "
                                      f"be fitted ({exc})")
        return res

    def _focus_z(self, base: list[float], half: float, step: float,
                 label: str, fine: bool = False, passes: int = 1,
                 tag: str = "") -> tuple[float, float]:
        """Sweep Z on the silicon (beside the strip) and move to the Z of
        minimum amplitude.

        ★Premise (established by measurement on 2026-09-07)★: the better
        the focus, the SMALLER the amplitude — LGAD gain suppression by
        the increased local carrier density (plasma effect). The old
        implementation's "maximum amplitude = focus" was wrong.
        ★Location (hardware feedback of 2026-09-07)★: the electrode is
        ~50 µm wide; directly above it the spot stays inside the metal
        even when defocused (the amplitude stays 0 and no valley is
        visible). Sweep **on the silicon beside the strip where the signal
        is clearly visible** (base is probe_off away from the electrode
        center).
        Coarse mode: midpoint of the two 50%-threshold flank edges
        (dip_center).
        Fine mode (fine): sweep passes times in one-pulse steps with many
        reads, fit the mean profile with a Lorentzian + linear baseline →
        minimum of the curve (fit_focus). If the fit fails, fall back to
        the threshold method (warning).
        Returns: (target Z, amplitude there). The target is rounded to the
        stage resolution (2 µm). The valley center before rounding is kept
        in self._last_focus_exact (the coefficient fit of Full Calibration
        uses the unrounded value: fitting only rounded values puts a ±1 µm
        quantization error on differences of a few µm over 3 points and
        shifts dZ/dY by about 1 µm/mm).
        """
        cfg = self._fine_cfg()
        reads = settle = None
        if fine:
            step = self._pulse_um(2)
            reads, settle = cfg["reads"], cfg["settle"]
        offsets = frange(-half, half, step)
        center = base[2]
        best = None
        info: dict = {"method": "fit" if fine else "threshold"}
        # if the valley touches the end of the sweep because of a
        # pre-focus offset, re-center on the minimum and retry (at most
        # twice → tolerates an initial offset of ±2×range)
        for attempt in range(3):
            b = [base[0], base[1], center]
            prof = []
            for k in range(max(1, passes)):
                sub = (f" (re-centering {attempt})" if attempt else "") + \
                      (f" {k + 1}/{passes}" if passes > 1 else "")
                zs, amps = self._sweep(2, b, offsets, label + sub,
                                       reads=reads, settle=settle,
                                       tag=f"{tag}:pass{k + 1}" if tag else "")
                prof.append((zs, amps))
            zs = prof[0][0]
            amps = np.mean([a for _, a in prof], axis=0)
            try:
                if fine:
                    try:
                        f = fit_focus(zs, amps, min_depth=0.004)
                        best = f["center"]
                        per_pass = []
                        for z_k, a_k in prof:
                            try:
                                per_pass.append(fit_focus(z_k, a_k)["center"])
                            except ValueError:
                                pass
                        info.update({
                            "center": best, "z0": f["z0"],
                            "gamma": f["gamma"],
                            "depth_pct": 100.0 * f["depth_ratio"],
                            "err": f["err"], "rms_mv": f["rms"] * 1e3,
                            "per_pass": [_r(v) for v in per_pass],
                            "spread": (max(per_pass) - min(per_pass))
                            if len(per_pass) > 1 else 0.0})
                    except ValueError as exc:
                        if _EDGE_MARKER in str(exc):
                            raise
                        best, _, _, _ = dip_center(
                            zs, amps, min_width=2.0, max_width=4.0 * half,
                            max_ratio=0.9, min_depth=0.004)
                        info.update({"method": "threshold", "center": best})
                        self._warnings.append(
                            f"{label}: valley fit failed ({exc}) → 50% "
                            "threshold method")
                else:
                    best, _, _, _ = dip_center(
                        zs, amps, min_width=2.0, max_width=4.0 * half,
                        max_ratio=0.9, min_depth=0.004)
                    info["center"] = best
                break
            except ValueError as exc:
                if _EDGE_MARKER in str(exc) and attempt < 2:
                    center = float(zs[int(np.argmin(_smooth3(amps)))])
                    continue
                raise ValueError(
                    f"Cannot locate the Z focus (valley of minimum "
                    f"amplitude): {exc}\n"
                    "→ widen the Z sweep range (±30–50 µm), put the beam on "
                    "silicon where the signal is clear, or do a coarse "
                    "focus with the camera and re-run") from None
        # round to the stage Z resolution (2 µm/pulse) (keep the unrounded
        # value for the fits)
        self._last_focus_exact = float(best)
        self._last_focus_info = info
        best = self._quant(2, best)
        tgt = list(base)
        tgt[2] = best - self.p.get("backlash_um", 10.0)
        self._goto(*tgt)          # approach from below for repeatability
        tgt[2] = best
        self._goto(*tgt)
        time.sleep(self.p.get("settle_s", 0.15))
        return best, self._amp()

    def _z_origin_from_probe(self, z_probe: float, dx: float,
                             dy: float) -> float:
        """Focus Z measured at the probe position (dx, dy from the origin)
        → focus Z at the origin.

        Small correction that extrapolates the plane with the prior tilt
        coefficients (coef0) (for the standard sensor dZ/dY 3.8 µm/mm ×
        80 µm ≈ 0.3 µm). No correction for an uncalibrated sensor.
        """
        return z_probe - (self._dzdx0 * dx + self._dzdy0 * dy)

    def _restore(self):
        """Recovery on abort/error: start position before R:W, origin
        after R:W."""
        try:
            self.stage.stop()
        except Exception:
            pass
        try:
            if self._origin_done:
                self.stage.move_to_um_relative(0.0, 0.0, 0.0)
            elif hasattr(self, "_start_pos"):
                self.stage.move_to_um_relative(*self._start_pos)
        except Exception:
            pass

    def _write_csv(self):
        path = self.p.get("csv_path")
        if not path or not self._csv_rows:
            return
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([f"# Auto Alignment sweep log "
                            f"{datetime.datetime.now().isoformat()} "
                            f"mode={self.p.get('mode')}"])
                w.writerow(["tag", "axis", "idx", "ch", "x_um", "y_um",
                            "z_um", "amplitude_v"])
                for row in self._csv_rows:
                    w.writerow([row[0], row[1], row[2], row[3],
                                f"{row[4]:.1f}", f"{row[5]:.1f}",
                                f"{row[6]:.1f}", f"{row[7]:.6g}"])
        except Exception as exc:
            self._warnings.append(f"Could not write the sweep log CSV "
                                  f"({exc})")

    # ------------------------------------------------------------------
    # Verification (re-measurement of the origin after R:W)
    # ------------------------------------------------------------------
    def _verify_origin(self, frame_off: float, x_ref: float, ywidth: float,
                       probe_off: float, x_probe_off: float, half_z: float,
                       tag: str) -> tuple[float, float, float, dict]:
        """Re-measure the electrode center Y, the signal edge X and the
        focus Z in the new logical origin.

        Returns: (dx, dy, dz, details). dx/dy/dz are "the current logical
        coordinates of the true origin" = residuals. dz is converted to
        the origin position with the tilt coefficients.
        """
        # the electrode surface Z follows the coefficient plane (0 at origin)
        def zp(x, y):
            return self._quant(2, self._dzdx0 * x + self._dzdy0 * y)
        e1, e2 = -0.5 * ywidth, 0.5 * ywidth
        fy = self._fine_dip([x_ref, 0.0, zp(x_ref, 0.0)], e1, e2,
                            "Verify Y", 1, tag=f"{tag}:y")
        dy = fy["center"]
        base = [-frame_off, dy - x_probe_off, zp(-frame_off, dy)]
        fx = self._fine_edge(base, -frame_off, "Verify X", 1, tag=f"{tag}:x")
        dx = fx["center"] + frame_off
        zb = [dx - frame_off - 30.0, dy - probe_off, 0.0]
        zb[2] = zp(zb[0], zb[1])
        self._focus_z(zb, half_z, self._pulse_um(2), "Verify Z", fine=True,
                      passes=1, tag=f"{tag}:z")
        z_probe = self._last_focus_exact
        dz = self._z_origin_from_probe(z_probe, zb[0] - dx, zb[1] - dy)
        detail = {"dx": _r(dx), "dy": _r(dy), "dz": _r(dz),
                  "y_err": _r(fy["err"]), "x_err": _r(fx["err"]),
                  "z_info": {k: (_r(v) if isinstance(v, float) else v)
                             for k, v in self._last_focus_info.items()},
                  "z_probe_exact": z_probe, "ywidth": _r(fy["width"])}
        return dx, dy, dz, detail

    # ------------------------------------------------------------------
    # Main body
    # ------------------------------------------------------------------
    def _run(self):
        if str(self.p.get("sensor_type", "strip")) == "pixel":
            return self._run_pixel()
        p = self.p
        fine = bool(p.get("fine", False))
        cfg = self._fine_cfg()
        results: dict = {"method": "signal-based alignment"
                         + (" (fine)" if fine else ""),
                         "ch": p["ch"],
                         "measured_at": datetime.datetime.now().isoformat()}

        # --- preparation: laser / scope into amplitude-reading mode ---
        if p.get("laser_on") and self.laser.connected:
            self.laser.set_emission(True)
            self._laser_turned_on = True
            time.sleep(1.0)
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        pos, _ = self.stage.get_position_um()
        self._start_pos = list(pos)
        if p.get("pre_move_xy") is not None:
            # so that it can also start from the already-found logical
            # origin (frame edge = no signal), move to the start position
            # on the reference strip
            self.progress.emit(0, 0, "Moving to the start position (on the "
                                     "reference strip)")
            x0, y0 = p["pre_move_xy"]
            self._goto(x0, y0, pos[2])
            pos, _ = self.stage.get_position_um()
        cur = list(pos)
        y_half = p.get("y_half_um", 150.0)
        y_step = p.get("y_step_um", 3.0)
        af_half = p.get("af_range_um", 20.0)
        af_step = p.get("af_step_um", 2.0)
        # The Z focus is done on the silicon probe_off (80 µm) beside the
        # electrode center (electrode width ~50 µm = half-width 25 + margin
        # for the spot growth when defocused; where the signal is clear).
        # The X edge detection uses x_probe_off (50 µm): with 80 µm the beam
        # left the Y width of the bond pad, the signal did not vanish and
        # the result was "no clear edge" (hardware feedback 2026-09-07).
        # Scan closer to the electrode, where the pad metal surely covers
        probe_off = p.get("y_probe_off_um", 80.0)
        x_probe_off = p.get("x_probe_off_um", 50.0)
        n_steps = 6 if fine else 5

        # === 1. coarse Y dip scan (locate the electrode at the current Z) ===
        # defocus makes the dip shallower, so relax the criterion
        # (max_ratio 0.75)
        self.progress.emit(0, 0, f"Step 1/{n_steps}: coarse Y electrode "
                                 "dip search")
        ys, amps = self._sweep(
            1, cur, frange(-y_half, y_half, y_step), "Y coarse scan",
            tag="origin:y_coarse")
        yc0, _, _, _ = dip_center(ys, amps, max_ratio=0.75)

        # === 2. Z focus on the silicon beside the strip (min. amplitude) ===
        self.progress.emit(0, 0, f"Step 2/{n_steps}: Z focus (on silicon, "
                                 "minimum amplitude)")
        base = [cur[0], yc0 - probe_off, cur[2]]
        best_z, amp_foc = self._focus_z(base, af_half, af_step,
                                        "Z focus", tag="origin:z1")
        cur[2] = best_z
        results["amp_at_focus_mv"] = round(amp_foc * 1e3, 1)

        # === 3. fine Y dip scan (edges are sharp after focusing) ===
        self.progress.emit(0, 0, f"Step 3/{n_steps}: fine Y electrode dip "
                                 "search")
        base = [cur[0], yc0, best_z]
        half3 = min(y_half, 60.0)
        ys, amps = self._sweep(
            1, base, frange(-half3, half3, y_step), "Y scan",
            tag="origin:y_mid")
        yc, ywidth, e1, e2 = dip_center(ys, amps)
        fine_y = fine_x = None
        if fine:
            # ★fine finishing: one-pulse steps, many reads, erf fit★
            base = [cur[0], yc, best_z]
            try:
                fine_y = self._fine_dip(
                    base, e1, e2, "Y electrode center", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:y_fine")
                yc, ywidth = fine_y["center"], fine_y["width"]
            except ValueError as exc:
                self._warnings.append(f"Y fine finishing failed ({exc}) → "
                                      "coarse value")
        results["electrode_width_um"] = round(ywidth, 1)
        cur[1] = yc

        # === 4. X edge scan → detect the readout-side end (②/③ boundary) ===
        self.progress.emit(0, 0, f"Step 4/{n_steps}: X end (readout side) "
                                 "search")
        probe = [cur[0], yc - x_probe_off, cur[2]]
        xs, amps = self._sweep(
            0, probe, frange(-p.get("x_back_um", 30.0),
                             p.get("x_fwd_um", 250.0),
                             p.get("x_step_um", 4.0)), "X scan",
            tag="origin:x_coarse")
        x_edge, hi, lo = edge_position(xs, amps, falling=True)
        results["plateau_mv"] = round(hi * 1e3, 1)
        if fine:
            base = [x_edge, yc - x_probe_off, cur[2]]
            try:
                fine_x = self._fine_edge(
                    base, x_edge, "X signal edge", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:x_fine")
                x_edge = fine_x["center"]
            except ValueError as exc:
                self._warnings.append(f"X fine finishing failed ({exc}) → "
                                      "coarse value")

        # === 5. Z re-check (on silicon) → move to the frame edge and reset
        #        the origin ===
        self.progress.emit(0, 0, f"Step 5/{n_steps}: Z re-check and origin "
                                 "reset")
        # re-check the minimum amplitude 30 µm inside the end, on the
        # silicon beside the electrode (keep the sweep range: both flanks
        # of the valley are needed)
        frame_off = float(p.get("x_frame_offset_um", 632.0))
        frame_x = x_edge + frame_off
        chk = [x_edge - 30.0, yc - probe_off, cur[2]]
        self._goto(*chk)
        self._focus_z(chk, af_half, af_step, "Z re-check", fine=fine,
                      passes=cfg["passes"] if fine else 1, tag="origin:z2")
        fine_z = dict(self._last_focus_info) if fine else None
        # origin Z: valley center at the probe position converted to the
        # electrode center (frame_x, yc) with the tilt coefficients (small)
        # → rounded to the pulse grid
        z_exact = self._z_origin_from_probe(
            self._last_focus_exact, chk[0] - frame_x, chk[1] - yc)
        z_t = self._quant(2, z_exact)
        # origin = signal edge (②/③ boundary) + design offset 632 µm =
        # frame edge
        cur = [frame_x, yc, z_t]
        self._approach(cur)      # enter from the negative side on all axes
        if p.get("reset_origin", True):
            self.stage.zero_all()          # R:W → frame edge is (0,0,0)
            self._origin_done = self.origin_done = True
        # for Full Calibration: the X where yc was measured (logical
        # coordinates of the new origin) and the rounding remainder of the
        # focus Z of the origin (valley center at the probe, new origin)
        x_ref = pos[0] - frame_x
        z0_frac = self._last_focus_exact - z_t
        residual = [frame_x - cur[0], 0.0, z_exact - z_t]  # rounding remainder
        results["origin_quantization_um"] = [_r(v) for v in residual]

        # === 6. verification: re-measure the origin after R:W and correct
        #        residuals of one pulse or more ===
        verify: dict | None = None
        if fine and p.get("verify", True) and self._origin_done:
            max_iter = int(p.get("verify_max_iter", 2))
            corrections: list[list[float]] = []
            details: list[dict] = []
            residual = None
            for it in range(max_iter + 1):
                self.progress.emit(
                    0, 0, f"Step 6/{n_steps}: origin verification {it + 1}"
                          f" (re-measure Y→X→Z)")
                dx, dy, dz, det = self._verify_origin(
                    frame_off, x_ref, ywidth, probe_off, x_probe_off,
                    af_half, tag=f"verify{it + 1}")
                details.append(det)
                corr = [self._quant(i, v) for i, v in enumerate((dx, dy, dz))]
                if any(c != 0.0 for c in corr) and it < max_iter:
                    self.progress.emit(
                        0, 0, f"Verification {it + 1}: residual X={dx:+.1f} "
                              f"Y={dy:+.1f} Z={dz:+.1f} µm → correct and "
                              "R:W")
                    self._approach(corr)
                    self.stage.zero_all()
                    corrections.append(corr)
                    x_ref -= corr[0]
                    continue
                residual = [dx, dy, dz]
                z0_frac = det["z_probe_exact"]
                if any(c != 0.0 for c in corr):
                    self._warnings.append(
                        f"Residual still one pulse or more at verification "
                        f"{it + 1} (X={dx:+.1f} Y={dy:+.1f} Z={dz:+.1f} µm). "
                        "Repeatability may be poor — check the sweep log")
                break
            verify = {"n_measure": len(details),
                      "corrections_um": corrections,
                      "residual_um": [_r(v) for v in residual],
                      "details": details}
            results["origin_residual_um"] = verify["residual_um"]
            # back to the origin (verification ends on the electrode)
            self._approach([0.0, 0.0, 0.0])
        self._x_ref = x_ref
        results["x_yc_measured_um"] = round(x_ref, 1)

        # --- report of Find Origin ---
        if fine:
            results["fine"] = {
                "y": {k: (_r(v) if isinstance(v, float) else v)
                      for k, v in (fine_y or {}).items()},
                "x": {k: (_r(v) if isinstance(v, float) else v)
                      for k, v in (fine_x or {}).items()},
                "z": {k: (_r(v) if isinstance(v, float) else v)
                      for k, v in (fine_z or {}).items()},
                "reads": cfg["reads"], "passes": cfg["passes"]}
            results["backlash_um"] = {
                "y": _r((fine_y or {}).get("backlash", float("nan"))),
                "x": _r((fine_x or {}).get("backlash", float("nan")))}
            results["verify"] = verify
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.origin_report = {k: results[k] for k in
                              ("fine", "backlash_um", "verify",
                               "origin_residual_um", "warnings")
                              if k in results} or None

        origin_msg = (
            f"Find Origin done (electrode width {ywidth:.0f} µm)\n"
            f"Electrode center Y, "
            f"signal edge+{frame_off:.0f} µm = "
            f"frame edge, focus Z (amplitude at focus {amp_foc * 1e3:.0f} mV"
            f" — minimal due to gain suppression) "
            f"{'→ logical origin reset done (R:W)' if self._origin_done else ''}\n"
            f"The exposed strip region starts at X=-{frame_off:.0f} µm")
        if fine:
            origin_msg += "\n" + self._fine_summary(fine_y, fine_x, fine_z,
                                                   verify, cfg)
        if self._warnings:
            origin_msg += "\n★Note: " + " / ".join(self._warnings)

        if p.get("mode", "origin") != "full":
            self.finished_align.emit(True, origin_msg)
            return

        # ==============================================================
        # Full Calibration: fit the coefficients at extra measurement
        # points (coordinates relative to the new logical origin)
        # ==============================================================
        dzdx0, dzdy0, dydx0, src0 = p.get(
            "coef0", (0.0, 0.0, 0.0, "uncalibrated"))
        # the X where Find Origin measured the electrode center (new
        # origin). The electrode line passes through (x_ref, 0), so the
        # prediction extrapolates the tilt from here
        x_rows = [(x_ref, 0.0, z0_frac)]   # (x, y_electrode_center, z_focus)
        point_fits: list[dict] = []
        for k, x in enumerate(p.get("x_points", [-5300.0, -9900.0])):
            x = float(x)
            self.progress.emit(
                0, 0, f"Full Calibration: X point {k + 1} (X={x:.0f} µm)")
            y_pred = dydx0 * (x - x_ref)
            z_pred = self._quant(2, dzdx0 * (x - x_ref))
            # dip detection at the tilt-predicted Z → Z minimization on the
            # electrode center
            base = [x, y_pred, z_pred]
            yc, e1, e2, info = self._point_dip(
                base, y_half, y_step, f"Y scan @X={x:.0f}", fine,
                tag=f"cal:x{k + 1}")
            base[1] = yc - probe_off        # focus on the silicon
            self._focus_z(base, af_half, af_step,
                          f"Z focus @X={x:.0f}", fine=fine,
                          tag=f"cal:x{k + 1}:z")
            x_rows.append((x, yc, self._last_focus_exact))
            info.update({"x": x, "z": _r(self._last_focus_exact),
                         "z_info": dict(self._last_focus_info)})
            point_fits.append(info)

        y_rows = [(0.0, z0_frac)]      # (y_electrode_center, z_focus)
        # measure inside the exposed strip region (X < -632), near the same
        # X as Find Origin
        x_probe = p.get("x_probe_um", x_ref)
        # ★at each Y measurement point switch to the scope CH that reads
        # that strip★ With a fixed CH (e.g. C1) there is almost no signal
        # at Y=-500/-1500 (C2/C4 strips) and the coefficient scan fails
        # (confirmed on the hardware 2026-09-07). Which Y is read with
        # which CH comes from the readout table of the sensor profile
        # (params["y_points"] = [[y, CH], ...])
        y_points = p.get("y_points", [[-500.0, "C2"], [-1500.0, "C4"]])
        y_half2 = min(120.0, y_half)
        used_channels = [self.p.get("ch", "C1")]
        for k, (y, strip_ch) in enumerate(y_points):
            self._ch = strip_ch
            used_channels.append(strip_ch)
            self.progress.emit(
                0, 0, f"Full Calibration: Y point {k + 1} "
                      f"(Y={y:.0f} µm, CH={strip_ch})")
            z_pred = self._quant(2, dzdy0 * y)
            # dip detection at the tilt-predicted Z → Z minimization on
            # the silicon
            base = [x_probe, y, z_pred]
            yc, e1, e2, info = self._point_dip(
                base, y_half2, y_step, f"Y scan @Y={y:.0f} ({strip_ch})",
                fine, tag=f"cal:y{k + 1}")
            base[1] = yc - probe_off        # focus on the silicon
            self._focus_z(base, af_half, af_step,
                          f"Z focus @Y={y:.0f} ({strip_ch})", fine=fine,
                          tag=f"cal:y{k + 1}:z")
            y_rows.append((yc, self._last_focus_exact))
            info.update({"y_nominal": y, "ch": strip_ch,
                         "z": _r(self._last_focus_exact),
                         "z_info": dict(self._last_focus_info)})
            point_fits.append(info)
        self._ch = self.p.get("ch", "C1")   # back to the reference CH
        results["y_strip_channels"] = used_channels[1:]

        # --- fits (including the origin) ---
        # a direction without measurement points (e.g. only 1 readout ch)
        # keeps the previous coefficient
        xa = np.array([r[0] for r in x_rows])
        ya = np.array([r[0] for r in y_rows])
        dydx, dzdx, dzdy = dydx0, dzdx0, dzdy0
        if len(y_rows) >= 2:
            dzdy = float(np.polyfit(ya, [r[1] for r in y_rows], 1)[0])
        if len(x_rows) >= 2:
            dydx = float(np.polyfit(xa, [r[1] for r in x_rows], 1)[0])
            # the Z of the X points is measured along the electrode line
            # (Y drifts with dY/dX), so its slope is dZ/dX + dZ/dY·dY/dX.
            # The plane model of the scans z = dZ/dX·x + dZ/dY·y uses the
            # partial derivative at constant Y, so remove the coupling term
            # (a small correction of 0.03 µm/mm for the standard sensor)
            dzdx = float(np.polyfit(xa, [r[2] for r in x_rows], 1)[0]) \
                - dzdy * dydx
        # measured pitch: electrode center Y vs strip index (identified by
        # dividing by the nominal pitch)
        nominal = float(p.get("pitch_um", 500.0))
        idx = [0.0] + [round(-y / nominal) for y, _ in y_points]
        pitch = float(-np.polyfit(idx, [r[0] for r in y_rows], 1)[0]) \
            if len(y_rows) >= 2 else float("nan")

        # fit residuals (Z is only determined in 2 µm steps, so if the
        # residual exceeds a few µm the valley detection is doubtful →
        # shown in the result as a basis for judgment)
        def _resid(xs, ys, slope):
            xs, ys = np.asarray(xs, float), np.asarray(ys, float)
            r = ys - slope * xs
            return float(np.max(np.abs(r - r.mean())))
        res_y = _resid(xa, [r[1] for r in x_rows], dydx)
        res_zx = _resid(xa, [r[2] for r in x_rows], dzdx)
        res_zy = _resid(ya, [r[1] for r in y_rows], dzdy)

        results.update({
            "dzdx": dzdx, "dzdy": dzdy, "dydx": dydx,
            "x_rows_um": [[round(v, 2) for v in r] for r in x_rows],
            "y_rows_um": [[round(v, 2) for v in r] for r in y_rows],
            "strip_pitch_measured_um": round(pitch, 1),
            "fit_residual_um": {"y_vs_x": round(res_y, 2),
                                "z_vs_x": round(res_zx, 2),
                                "z_vs_y": round(res_zy, 2)},
            "previous": {"dzdx": dzdx0, "dzdy": dzdy0, "dydx": dydx0,
                         "source": src0},
        })
        if fine:
            results["point_fits"] = point_fits
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.calibration = results

        # back to the origin (Z approached from below as in the sweeps for
        # repeatability)
        self._approach([0.0, 0.0, 0.0])
        msg = (f"{origin_msg}\n"
               f"Full Calibration done — coefficients updated:\n"
               f"  dY/dX = {dydx * 1000:+.2f} µm/mm "
               f"(previous {dydx0 * 1000:+.2f})\n"
               f"  dZ/dX = {dzdx * 1000:+.2f} µm/mm "
               f"(previous {dzdx0 * 1000:+.2f})\n"
               f"  dZ/dY = {dzdy * 1000:+.2f} µm/mm "
               f"(previous {dzdy0 * 1000:+.2f})\n"
               f"  Adjacent electrode spacing (measured): {pitch:.0f} µm "
               f"(nominal {nominal:.0f})\n"
               f"  Linear-fit residuals (max): Y(X) ±{res_y:.1f} µm, "
               f"Z(X) ±{res_zx:.1f} µm, Z(Y) ±{res_zy:.1f} µm\n"
               f"Subsequent scans use these coefficients automatically")
        self.finished_align.emit(True, msg)

    # ------------------------------------------------------------------
    # Pixel sensors (the pad = signal-free square is the ruler)
    # ------------------------------------------------------------------
    def _verify_origin_pixel(self, pad: float, ywidth: float, xwidth: float,
                             probe_off: float, half_z: float,
                             tag: str) -> tuple[float, float, float, dict]:
        """Re-measure the reference pad center (−pad/2, −pad/2) and the focus Z
        in the new logical origin. Returns (dx, dy, dz, detail) = current
        logical coordinates of the true origin (residual)."""
        def zp(x, y):
            return self._quant(2, self._dzdx0 * x + self._dzdy0 * y)
        cx = cy = -pad / 2.0
        mw = 2.0 * pad
        fy = self._fine_dip([cx, cy, zp(cx, cy)], cy - 0.5 * ywidth,
                            cy + 0.5 * ywidth, "verify Y", 1, tag=f"{tag}:y",
                            axis=1, max_width=mw)
        yc = fy["center"]
        dy = yc - cy
        fx = self._fine_dip([cx, yc, zp(cx, yc)], cx - 0.5 * xwidth,
                            cx + 0.5 * xwidth, "verify X", 1, tag=f"{tag}:x",
                            axis=0, max_width=mw)
        xc = fx["center"]
        dx = xc - cx
        zb = [xc, yc - probe_off, 0.0]
        zb[2] = zp(zb[0], zb[1])
        self._focus_z(zb, half_z, self._pulse_um(2), "verify Z", fine=True,
                      passes=1, tag=f"{tag}:z")
        z_probe = self._last_focus_exact
        dz = self._z_origin_from_probe(z_probe, zb[0] - dx, zb[1] - dy)
        detail = {"dx": _r(dx), "dy": _r(dy), "dz": _r(dz),
                  "y_err": _r(fy["err"]), "x_err": _r(fx["err"]),
                  "z_info": {k: (_r(v) if isinstance(v, float) else v)
                             for k, v in self._last_focus_info.items()},
                  "z_probe_exact": z_probe, "ywidth": _r(fy["width"]),
                  "xwidth": _r(fx["width"])}
        return dx, dy, dz, detail

    def _run_pixel(self):
        """Find Origin (+ Full Calibration) for pixel sensors; see "Pixel sensors"
        in the module docstring."""
        p = self.p
        fine = bool(p.get("fine", False))
        cfg = self._fine_cfg()
        pad = float(p.get("pad_um", 100.0))
        pitch = float(p.get("pitch_um", 500.0))
        mw = 2.0 * pad                      # upper limit of the dip width (defocus widens it)
        results: dict = {"method": "signal-based alignment (pixel)"
                         + (" (fine)" if fine else ""),
                         "ch": p["ch"], "pad_um": pad,
                         "measured_at": datetime.datetime.now().isoformat()}

        if p.get("laser_on") and self.laser.connected:
            self.laser.set_emission(True)
            self._laser_turned_on = True
            time.sleep(1.0)
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        pos, _ = self.stage.get_position_um()
        self._start_pos = list(pos)
        if p.get("pre_move_xy") is not None:
            self.progress.emit(0, 0, "Moving to the start position (center of the reference pad)")
            x0, y0 = p["pre_move_xy"]
            self._goto(x0, y0, pos[2])
            pos, _ = self.stage.get_position_um()
        cur = list(pos)
        y_half = float(p.get("y_half_um", pad + 100.0))
        y_step = float(p.get("y_step_um", 3.0))
        x_step = float(p.get("x_step_um", 4.0))
        af_half = p.get("af_range_um", 20.0)
        af_step = p.get("af_step_um", 2.0)
        probe_off = float(p.get("y_probe_off_um", pad / 2.0 + 80.0))
        n_steps = 6 if fine else 5

        # === 1. coarse Y dip (across the pad) ===
        self.progress.emit(0, 0, f"Step 1/{n_steps}: coarse Y pad-dip detection")
        ys, amps = self._sweep(1, cur, frange(-y_half, y_half, y_step),
                               "coarse Y sweep (pad)", tag="origin:y_coarse")
        yc0, _, _, _ = dip_center(ys, amps, max_ratio=0.75, max_width=mw + 50.0)

        # === 2. Z focus beside the pad (silicon inside the pixel) ===
        self.progress.emit(0, 0, f"Step 2/{n_steps}: Z focus (beside the pad, minimum amplitude)")
        base = [cur[0], yc0 - probe_off, cur[2]]
        best_z, amp_foc = self._focus_z(base, af_half, af_step, "Z focus",
                                        tag="origin:z1")
        cur[2] = best_z
        results["amp_at_focus_mv"] = round(amp_foc * 1e3, 1)

        # === 3. fine Y dip ===
        self.progress.emit(0, 0, f"Step 3/{n_steps}: fine detection of the pad center Y")
        half3 = min(y_half, pad / 2.0 + 60.0)
        ys, amps = self._sweep(1, [cur[0], yc0, best_z],
                               frange(-half3, half3, y_step), "Y sweep (pad)",
                               tag="origin:y_mid")
        yc, ywidth, e1, e2 = dip_center(ys, amps, max_width=mw)
        fine_y = fine_x = None
        if fine:
            try:
                fine_y = self._fine_dip(
                    [cur[0], yc, best_z], e1, e2, "Y pad center", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:y_fine", axis=1, max_width=mw)
                yc, ywidth = fine_y["center"], fine_y["width"]
            except ValueError as exc:
                self._warnings.append(f"Y fine finishing failed ({exc}) → coarse value")
        results["pad_width_y_um"] = round(ywidth, 1)
        cur[1] = yc

        # === 4. X dip (across the pad along the pad center row) ===
        self.progress.emit(0, 0, f"Step 4/{n_steps}: detection of the pad center X")
        half4 = pad / 2.0 + 100.0
        xs, amps = self._sweep(0, [cur[0], yc, best_z],
                               frange(-half4, half4, x_step), "X sweep (pad)",
                               tag="origin:x_coarse")
        xc, xwidth, ex1, ex2 = dip_center(xs, amps, max_width=mw)
        if fine:
            try:
                fine_x = self._fine_dip(
                    [xc, yc, best_z], ex1, ex2, "X pad center", cfg["passes"],
                    backlash_check=bool(p.get("backlash_check", True)),
                    tag="origin:x_fine", axis=0, max_width=mw)
                xc, xwidth = fine_x["center"], fine_x["width"]
            except ValueError as exc:
                self._warnings.append(f"X fine finishing failed ({exc}) → coarse value")
        results["pad_width_x_um"] = round(xwidth, 1)

        # === 5. Z re-check → reset the origin at the +X/+Y corner of the pad ===
        self.progress.emit(0, 0, f"Step 5/{n_steps}: Z re-check and origin reset")
        corner = [xc + pad / 2.0, yc + pad / 2.0]
        chk = [xc, yc - probe_off, best_z]
        self._goto(*chk)
        self._focus_z(chk, af_half, af_step, "Z re-check", fine=fine,
                      passes=cfg["passes"] if fine else 1, tag="origin:z2")
        fine_z = dict(self._last_focus_info) if fine else None
        z_exact = self._z_origin_from_probe(
            self._last_focus_exact, chk[0] - corner[0], chk[1] - corner[1])
        z_t = self._quant(2, z_exact)
        cur = [corner[0], corner[1], z_t]
        self._approach(cur)
        if p.get("reset_origin", True):
            self.stage.zero_all()          # R:W → the pad corner is (0,0,0)
            self._origin_done = self.origin_done = True
        x_ref = -pad / 2.0                 # pad center X in the new origin
        z0_frac = self._last_focus_exact - z_t
        results["origin_quantization_um"] = [_r(v) for v in (0.0, 0.0, z_exact - z_t)]
        results["pad_center_measured_um"] = [_r(xc - corner[0]), _r(yc - corner[1])]

        # === 6. verification ===
        verify: dict | None = None
        if fine and p.get("verify", True) and self._origin_done:
            max_iter = int(p.get("verify_max_iter", 2))
            corrections: list[list[float]] = []
            details: list[dict] = []
            residual = None
            for it in range(max_iter + 1):
                self.progress.emit(
                    0, 0, f"Step 6/{n_steps}: origin verification {it + 1} (re-measure Y→X→Z)")
                dx, dy, dz, det = self._verify_origin_pixel(
                    pad, ywidth, xwidth, probe_off, af_half, tag=f"verify{it + 1}")
                details.append(det)
                corr = [self._quant(i, v) for i, v in enumerate((dx, dy, dz))]
                if any(c != 0.0 for c in corr) and it < max_iter:
                    self.progress.emit(
                        0, 0, f"verification {it + 1}: residual X={dx:+.1f} Y={dy:+.1f} "
                              f"Z={dz:+.1f} µm → correcting and redoing R:W")
                    self._approach(corr)
                    self.stage.zero_all()
                    corrections.append(corr)
                    continue
                residual = [dx, dy, dz]
                z0_frac = det["z_probe_exact"]
                if any(c != 0.0 for c in corr):
                    self._warnings.append(
                        f"residual still one pulse or more after verification {it + 1} "
                        f"(X={dx:+.1f} Y={dy:+.1f} Z={dz:+.1f} µm). "
                        "Possibly poor repeatability — check the sweep log")
                break
            verify = {"n_measure": len(details), "corrections_um": corrections,
                      "residual_um": [_r(v) for v in residual], "details": details}
            results["origin_residual_um"] = verify["residual_um"]
            self._approach([0.0, 0.0, 0.0])
        self._x_ref = x_ref
        results["x_yc_measured_um"] = round(x_ref, 1)
        if fine:
            results["fine"] = {
                "y": {k: (_r(v) if isinstance(v, float) else v) for k, v in (fine_y or {}).items()},
                "x": {k: (_r(v) if isinstance(v, float) else v) for k, v in (fine_x or {}).items()},
                "z": {k: (_r(v) if isinstance(v, float) else v) for k, v in (fine_z or {}).items()},
                "reads": cfg["reads"], "passes": cfg["passes"]}
            results["backlash_um"] = {
                "y": _r((fine_y or {}).get("backlash", float("nan"))),
                "x": _r((fine_x or {}).get("backlash", float("nan")))}
            results["verify"] = verify
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.origin_report = {k: results[k] for k in
                              ("fine", "backlash_um", "verify", "origin_residual_um", "warnings")
                              if k in results} or None

        origin_msg = (
            f"Find Origin done (pixel: pad width X {xwidth:.0f} / Y {ywidth:.0f} µm)\n"
            f"Origin = +X/+Y corner of the pad (pad center + ({pad / 2:.0f}, {pad / 2:.0f}) µm), "
            f"focus Z (amplitude at focus {amp_foc * 1e3:.0f} mV — minimum by gain suppression) "
            f"{'→ logical origin reset (R:W)' if self._origin_done else ''}\n"
            f"Pixel center (pad center) at ({-pad / 2:.0f}, {-pad / 2:.0f}) µm, "
            f"neighboring pixels at ±{pitch:.0f} µm, 4-pixel corner at "
            f"({-pad / 2 - pitch / 2:.0f}, {-pad / 2 - pitch / 2:.0f}) µm")
        if fine:
            origin_msg += "\n" + self._fine_summary(fine_y, fine_x, fine_z, verify, cfg)
        if self._warnings:
            origin_msg += "\n★Note: " + " / ".join(self._warnings)

        if p.get("mode", "origin") != "full":
            self.finished_align.emit(True, origin_msg)
            return

        # ==============================================================
        # Full Calibration: pad centers of the other readout pixels (read on their CH)
        # ==============================================================
        dzdx0, dzdy0, dydx0, src0 = p.get("coef0", (0.0, 0.0, 0.0, "uncalibrated"))
        pads = [list(v) for v in p.get("pads", [])]
        rows = [(x_ref, -pad / 2.0, z0_frac)]     # (pad center x, y, focus z) — reference pad
        nominal = [(x_ref, -pad / 2.0)]
        point_fits: list[dict] = []
        for k, (ch, xn, yn) in enumerate(pads):
            xn, yn = float(xn), float(yn)
            self._ch = str(ch)
            self.progress.emit(0, 0, f"Full Calibration: pad {k + 1}/{len(pads)} ({ch}, X={xn:.0f}, Y={yn:.0f} µm)")
            y_pred = yn + dydx0 * (xn - x_ref)
            z_pred = self._quant(2, dzdx0 * xn + dzdy0 * y_pred)
            yc_k, _e1, _e2, info = self._point_dip(
                [xn, y_pred, z_pred], pad / 2.0 + 60.0, y_step, f"Y sweep @{ch}",
                fine, tag=f"cal:{ch}", axis=1, max_width=mw)
            xc_k, _e1, _e2, info_x = self._point_dip(
                [xn, yc_k, z_pred], pad / 2.0 + 60.0, x_step, f"X sweep @{ch}",
                fine, tag=f"cal:{ch}:x", axis=0, max_width=mw)
            base = [xc_k, yc_k - probe_off, z_pred]
            self._focus_z(base, af_half, af_step, f"Z focus @{ch}", fine=fine,
                          tag=f"cal:{ch}:z")
            z_c = self._z_origin_from_probe(self._last_focus_exact, 0.0, -probe_off)
            rows.append((xc_k, yc_k, z_c))
            nominal.append((xn, yn))
            info.update({"ch": str(ch), "x_nominal": xn, "y_nominal": yn,
                         "x": _r(xc_k), "y": _r(yc_k), "z": _r(z_c),
                         "x_info": info_x, "z_info": dict(self._last_focus_info)})
            point_fits.append(info)
        self._ch = self.p.get("ch", "C1")

        # --- fits ---
        pts = np.array(rows, float)
        dzdx, dzdy, dydx = dzdx0, dzdy0, dydx0
        res_z = float("nan")
        if len(rows) >= 3:
            A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(rows))])
            if np.linalg.matrix_rank(A) >= 3:
                coef, *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
                dzdx, dzdy = float(coef[0]), float(coef[1])
                res_z = float(np.max(np.abs(pts[:, 2] - A @ coef)))
        elif len(rows) == 2:
            dx_, dy_, dz_ = pts[1] - pts[0]
            if abs(dx_) > abs(dy_) and abs(dx_) > 1.0:
                dzdx = float(dz_ / dx_)
            elif abs(dy_) > 1.0:
                dzdy = float(dz_ / dy_)
        # in-plane rotation: pad center Y vs X of the pads in the reference row (same nominal y)
        same_row = [i for i, (xn, yn) in enumerate(nominal) if abs(yn - (-pad / 2.0)) < pitch / 2.0]
        res_y = float("nan")
        if len(same_row) >= 2:
            xs_ = pts[same_row, 0]
            ys_ = pts[same_row, 1]
            dydx = float(np.polyfit(xs_, ys_, 1)[0])
            r_ = ys_ - dydx * xs_
            res_y = float(np.max(np.abs(r_ - r_.mean())))
        # measured pitch: distance from the reference pad center / nominal number of pitches
        pitches = []
        for i in range(1, len(rows)):
            d = math.hypot(pts[i, 0] - pts[0, 0], pts[i, 1] - pts[0, 1])
            d0 = math.hypot(nominal[i][0] - nominal[0][0], nominal[i][1] - nominal[0][1])
            n_ = round(d0 / pitch) if pitch > 0 else 0
            if n_ >= 1 and abs(d0 / pitch - n_) < 0.1:
                pitches.append(d / n_)
            elif d0 > 0:
                pitches.append(d / d0 * pitch)
        pitch_meas = float(np.mean(pitches)) if pitches else float("nan")
        results.update({
            "dzdx": dzdx, "dzdy": dzdy, "dydx": dydx,
            "pad_rows_um": [[round(v, 2) for v in r] for r in rows],
            "pad_nominal_um": [[round(v, 2) for v in r] for r in nominal],
            "pad_channels": [str(c[0]) for c in pads],
            "pixel_pitch_measured_um": round(pitch_meas, 1),
            "fit_residual_um": {"z_plane": _r(res_z), "y_vs_x": _r(res_y)},
            "previous": {"dzdx": dzdx0, "dzdy": dzdy0, "dydx": dydx0, "source": src0},
        })
        if fine:
            results["point_fits"] = point_fits
        if self._warnings:
            results["warnings"] = list(self._warnings)
        self.calibration = results

        self._approach([0.0, 0.0, 0.0])
        msg = (f"{origin_msg}\n"
               f"Full Calibration done (pixel: {len(rows)} pads, lever arm ≈ {pitch:.0f} µm) — coefficients updated:\n"
               f"  dY/dX = {dydx * 1000:+.2f} µm/mm (previous {dydx0 * 1000:+.2f})\n"
               f"  dZ/dX = {dzdx * 1000:+.2f} µm/mm (previous {dzdx0 * 1000:+.2f})\n"
               f"  dZ/dY = {dzdy * 1000:+.2f} µm/mm (previous {dzdy0 * 1000:+.2f})\n"
               f"  pixel pitch (measured): {pitch_meas:.0f} µm (nominal {pitch:.0f})\n"
               f"  fit residuals (max): Z plane ±{res_z:.1f} µm, Y(X) ±{res_y:.1f} µm\n"
               "★The lever arm is only one pitch, so the coefficients are a coarse guide "
               "(less than one pulse over a scan range below 1 mm)\n"
               "Later scans use these coefficients automatically")
        self.finished_align.emit(True, msg)

    def _point_dip(self, base: list[float], half: float, step: float,
                   label: str, fine: bool, tag: str, axis: int = 1,
                   max_width: float = 200.0):
        """Measurement point of Full Calibration: coarse dip detection
        (+ fine finishing). axis = 1 (Y) / 0 (X).

        Returns: (electrode / pad center, coarse left edge, coarse right edge,
        info dict)
        """
        ys, amps = self._sweep(axis, base, frange(-half, half, step), label,
                               tag=f"{tag}:coarse")
        yc, width, e1, e2 = dip_center(ys, amps, max_ratio=0.75,
                                       max_width=max_width)
        info = {"y_coarse": _r(yc), "width": _r(width)}
        if fine:
            try:
                b2 = list(base)
                b2[axis] = yc
                fy = self._fine_dip(b2, e1, e2, label, 1, tag=f"{tag}:fine",
                                    axis=axis, max_width=max_width)
                yc = fy["center"]
                info.update({"y": _r(yc), "y_err": _r(fy["err"]),
                             "width": _r(fy["width"]),
                             "sigma": _r(fy["sigma"])})
            except ValueError as exc:
                self._warnings.append(f"{label}: fine finishing failed "
                                      f"({exc}) → coarse value")
        return yc, e1, e2, info

    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_bl(v) -> str:
        return "n/a" if v is None or not np.isfinite(v) else f"{v:+.1f} µm"

    def _fine_summary(self, fy, fx, fz, verify, cfg) -> str:
        pixel = str(self.p.get("sensor_type", "strip")) == "pixel"
        lines = [f"Fine finishing (one-pulse steps, median of {cfg['reads']} "
                 "reads, erf / Lorentzian fits):"]
        if fy:
            lines.append(
                f"  Y {'pad center' if pixel else 'electrode center'}: fit error ±{fy['err']:.2f} µm, "
                f"difference between {fy['n_pass']} passes "
                f"{fy['spread']:.2f} µm, edge width σ={fy['sigma']:.1f} µm, "
                f"difference to the reverse sweep (backlash estimate) "
                f"{self._fmt_bl(fy.get('backlash'))}")
        if fx:
            lines.append(
                f"  X {'pad center' if pixel else 'signal edge'}: ±{fx['err']:.2f} µm, difference between "
                f"{fx['n_pass']} passes {fx['spread']:.2f} µm, "
                f"σ={fx['sigma']:.1f} µm, backlash estimate "
                f"{self._fmt_bl(fx.get('backlash'))}")
        if fz and fz.get("method") == "fit":
            lines.append(
                f"  Z focus: valley depth {fz.get('depth_pct', 0):.0f} %, "
                f"γ={fz.get('gamma', 0):.0f} µm, difference between "
                f"{len(fz.get('per_pass', []))} passes "
                f"{fz.get('spread', 0):.2f} µm (unrounded "
                f"{fz.get('center', 0):+.1f})")
        if verify:
            rx, ry, rz = verify["residual_um"]
            corr = verify["corrections_um"]
            ctxt = ("no correction" if not corr else
                    "corrected " + "; ".join(
                        f"X={c[0]:+.0f} Y={c[1]:+.0f} Z={c[2]:+.0f}"
                        for c in corr) + " µm")
            ok = all(abs(v) < 1.0 for v in (rx, ry, rz))
            lines.append(
                f"Verification (re-measured after R:W, {verify['n_measure']} "
                f"time(s), {ctxt}): residual X={rx:+.1f} Y={ry:+.1f} "
                f"Z={rz:+.1f} µm "
                + ("→ below one pulse (2 µm) OK" if ok
                   else "→ ★one pulse or more★"))
        return "\n".join(lines)


class _Aborted(Exception):
    pass
