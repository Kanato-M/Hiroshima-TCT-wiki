# -*- coding: utf-8 -*-
"""Measured sensor tilt/rotation map and correction coefficients
(measured 2026-08-24).

After the setup jig was improved (2026-08), the stage positions were
re-measured. This time each strip-center position is recorded in
**logical coordinates [µm]**; absolute machine coordinates are
reconstructed by adding MACHINE_ORIGIN_UM, the machine coordinates of the
logical origin (Z is the height at which the camera focus was sharp). The
readout strips are C1–C4; C0/C5 are the adjacent (non-readout) strips
just outside them.

Definition of the X coordinate (updated 2026-09-07, see the
"Strip detail" slide):
  - **X=0 is the sensor frame (GND) edge** (definition changed on
    2026-09-07; before that X=0 was the boundary between the wire-bond
    pad region ② and the scan region ③, i.e. the readout-side end of the
    exposed strip).
  - Breakdown from the frame edge (measured on the slide):
      ⓪ 0.052 mm (frame edge –) ① 0.380 mm ② 0.200 mm (bond pad)
      ③ 9.318 mm (exposed strip = scan region) ④ 0.098 mm (end gap)
    ⓪+①+② = 0.632 mm is where the exposed strip starts, and
    ⓪+①+②+③+④/2 = 9.999 mm is the extent of one strip → **moving
    10 mm from the origin lands inside gap ④ (between two strip
    regions)**.
  - **Physical X+ corresponds to stage x−** (the frame edge is at large
    machine x). In logical coordinates the exposed strip spans
    x = −632 … −9950 and the middle of gap ④ is at x = −9999.

Sensor tilt derived from the measurements (least-squares fit to
STRIP_POINTS; the coefficients are difference-based, so logical and
machine coordinates give identical values):
  1. Focal-plane tilt: dZ/dX = −0.02 µm/mm (essentially flat, vs +1.04
     with the old jig), dZ/dY = +3.79 µm/mm (residual max ±1.6 µm ≤ the
     2 µm/pulse Z resolution). X is almost flat, but Z changes by ~11 µm
     across the 3 mm scan width in Y, so Z tracking is still needed to
     keep a spot with a beam radius of a few µm.
  2. In-plane (XY) rotation: the strip-center Y drifts by +7.7 µm/mm
     along X (~72 µm over the full strip length, rotation angle ~0.44°).

Correcting the tilt on the scan side is easier than re-seating the sensor
to make it parallel (policy since 2026-07-06). Absolute coordinates can
change after every M-ORG, but the tilt coefficients (gradients) do not
depend on where the origin is taken, so the same coefficients apply no
matter where the logical origin is reset. Scans themselves use relative
moves (M:W) referenced to the logical origin.

Notes:
- Coordinate-system check: the stage position just before R:W in that
  day's log gui_20260824.log, (54150, 50025, 707) pulses, converts at
  2 µm/pulse to (108300, 100050, 1414) µm = MACHINE_ORIGIN_UM (before the
  +632 µm shift), confirming this table is in µm of the current GUI
  display system (2 µm/pulse calibration).
- In this GUI these values are the built-in defaults of the *standard*
  sensor profile (sensors.py); other sensors are defined in
  <log_dir>/sensors.json.
"""

from __future__ import annotations

MEASURED_DATE = "2026-08-24"

# ------------------------------------------------------------------
# Design dimensions along the strip (X) [µm] (from the "Strip detail" slide)
# ------------------------------------------------------------------
SEG_FRAME_UM = 52.0     # ⓪ frame (GND) edge –
SEG_RING_UM = 380.0     # ①
SEG_PAD_UM = 200.0      # ② wire-bond pad region
SEG_STRIP_UM = 9318.0   # ③ exposed strip region (scan region)
SEG_GAP_UM = 98.0       # ④ gap between strip end and next region
# Offset from the frame edge (X=0) to the start of the exposed strip
# (②/③ boundary)
X_FRAME_OFFSET_UM = SEG_FRAME_UM + SEG_RING_UM + SEG_PAD_UM   # 632
# X extent of one strip (frame edge → middle of gap ④): 9999 µm ≈ 10 mm
X_STRIP_UNIT_UM = X_FRAME_OFFSET_UM + SEG_STRIP_UM + SEG_GAP_UM / 2

# Machine coordinates [µm] of the logical origin (frame edge, C1 electrode
# center in Y). The 2026-08-24 R:W was taken with the old definition
# (②/③ boundary), hence the +632 µm conversion.
MACHINE_ORIGIN_UM: tuple[float, float, float] = (
    108300.0 + X_FRAME_OFFSET_UM, 100050.0, 1414.0)

# Measured logical coordinates [µm] of the strip centers. The measurement
# (2026-08-24) was recorded with the old definition (X=0 = ②/③ boundary),
# so X_FRAME_OFFSET_UM is subtracted from X to convert to the current
# definition (X=0 = frame edge).
_STRIP_POINTS_SCAN_EDGE: dict[str, tuple[tuple[float, float, float], ...]] = {
    "C0": ((-4.0, 500.0, 2.0), (-4504.0, 464.0, 0.0)),
    "C1": ((0.0, 0.0, 0.0), (-4500.0, -36.0, 0.0),
           (-9318.0, -70.0, 0.0), (-9370.0, -72.0, 0.0)),
    "C2": ((6.0, -500.0, -2.0), (-4494.0, -536.0, -2.0)),
    "C3": ((10.0, -1000.0, -4.0), (-4490.0, -1036.0, -4.0)),
    "C4": ((14.0, -1500.0, -6.0), (-4486.0, -1536.0, -6.0)),
    "C5": ((18.0, -2000.0, -8.0), (-4482.0, -2036.0, -8.0),
           (-9300.0, -2070.0, -8.0)),
}
STRIP_POINTS_LOGICAL: dict[str, tuple[tuple[float, float, float], ...]] = {
    name: tuple((p[0] - X_FRAME_OFFSET_UM, p[1], p[2]) for p in pts)
    for name, pts in _STRIP_POINTS_SCAN_EDGE.items()
}

# Absolute machine coordinates [µm] of the strip centers (= machine
# origin + logical offset). They can change after every M-ORG, so they
# are only a guide.
STRIP_POINTS: dict[str, tuple[tuple[float, float, float], ...]] = {
    name: tuple(tuple(o + d for o, d in zip(MACHINE_ORIGIN_UM, p))
                for p in pts)
    for name, pts in STRIP_POINTS_LOGICAL.items()
}

# Strip pitch [µm] (measured Y difference C0 → C5, / 5 = exactly 500)
STRIP_PITCH_UM = (STRIP_POINTS["C0"][0][1] - STRIP_POINTS["C5"][0][1]) / 5.0

# Default auto-scan range (logical origin = frame edge, C1 electrode
# center in Y):
#   fast axis Y: half a pitch (250 µm) of margin outside the C0 center
#                (+500) and the C5 center (−2000), so the scan crosses
#                the electrodes of the outer strips as well
#   slow axis X: covers the exposed strip ③ (−632 … −9950) + ④/2
#                (−9999 ≈ −10 mm is the middle of gap ④ = the boundary
#                of the strip region)
SCAN_FAST_Y_START = 750.0
SCAN_FAST_Y_STOP = -2250.0
SCAN_FAST_Y_STEP = 10.0
SCAN_SLOW_X_START = -X_FRAME_OFFSET_UM              # -632
SCAN_SLOW_X_STOP = -round(X_STRIP_UNIT_UM)          # -9999
SCAN_SLOW_X_STEP = 500.0


def _fit_tilt_plane(points) -> tuple[float, float, float]:
    """Least-squares fit of z = dzdx·x + dzdy·y + z0.

    For numerical stability the points are centered on their centroid and
    the 2×2 normal equations are solved (centering removes the intercept
    term).
    """
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    sxx = sum((p[0] - cx) ** 2 for p in points)
    syy = sum((p[1] - cy) ** 2 for p in points)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in points)
    sxz = sum((p[0] - cx) * (p[2] - cz) for p in points)
    syz = sum((p[1] - cy) * (p[2] - cz) for p in points)
    det = sxx * syy - sxy * sxy
    dzdx = (sxz * syy - syz * sxy) / det
    dzdy = (syz * sxx - sxz * sxy) / det
    return dzdx, dzdy, cz - dzdx * cx - dzdy * cy


def _fit_rotation(strips) -> float:
    """Least-squares common slope dy/dx of the Y(X) lines of all strips.

    Each strip has its own free intercept (Y position); only the slope is
    shared. The number of points per strip may vary (2 or more).
    """
    num = den = 0.0
    for pts in strips:
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        num += sum((p[0] - mx) * (p[1] - my) for p in pts)
        den += sum((p[0] - mx) ** 2 for p in pts)
    return num / den


_ALL_POINTS = [p for pts in STRIP_POINTS.values() for p in pts]

# Focal-plane tilt [µm/µm]: dZ/dX=-2.14e-5, dZ/dY=+3.785e-3 (measured fit)
TILT_DZDX, TILT_DZDY, _PLANE_Z0 = _fit_tilt_plane(_ALL_POINTS)
# In-plane rotation [µm/µm]: dY/dX=+7.67e-3 (~72 µm over the 9.37 mm strip)
ROT_DYDX = _fit_rotation(STRIP_POINTS.values())


def tilt_summary() -> str:
    """Coefficient summary string for the UI."""
    return (f"dZ/dX={TILT_DZDX * 1000:+.2f}, dZ/dY={TILT_DZDY * 1000:+.2f}, "
            f"dY/dX={ROT_DYDX * 1000:+.1f} µm/mm (fit, {MEASURED_DATE})")


def strip_y_offsets_um() -> dict[str, float]:
    """Y offset [µm] of each strip center (edge side), with C1 edge = 0."""
    y1 = STRIP_POINTS["C1"][0][1]
    return {name: pts[0][1] - y1 for name, pts in STRIP_POINTS.items()}


def stage_presets() -> dict[str, list[float]]:
    """Legacy bookmark presets (measured absolute machine coordinates).

    Older GUI versions registered these as initial bookmarks in
    logs/positions.json. This GUI replaces them with "Go to strip"
    (computed from the sensor geometry and tilt coefficients), so the
    names are only used by sensors.py to recognize and skip the old
    presets when migrating a positions.json.
    Only C1 has four points along the strip.
    """
    presets = {
        f"Logical origin: frame edge X=0 (C1 electrode Y) "
        f"({MEASURED_DATE} conv.)": list(MACHINE_ORIGIN_UM),
        f"C1 edge, readout side X=-632 ({MEASURED_DATE})":
            list(STRIP_POINTS["C1"][0]),
    }
    for name in ("C0", "C2", "C3", "C4", "C5"):
        presets[f"{name} edge, readout side X=-632 ({MEASURED_DATE})"] = \
            list(STRIP_POINTS[name][0])
    presets[f"C1 middle X=-5.1mm ({MEASURED_DATE})"] = \
        list(STRIP_POINTS["C1"][1])
    presets[f"C1 far side X=-9.95mm strip end ({MEASURED_DATE})"] = \
        list(STRIP_POINTS["C1"][2])
    presets[f"C1 scan end X=-10.0mm ({MEASURED_DATE})"] = \
        list(STRIP_POINTS["C1"][3])
    return presets
