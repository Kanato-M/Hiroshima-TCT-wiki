# -*- coding: utf-8 -*-
"""Fake sensor response for --simulate (stage position, laser state →
amplitude).

If the scope amplitude readout (PAVA) stayed constant in simulation
mode, auto-alignment, Z focus and TUNE optimization could not be
exercised (no dip, no edge, no valley). So the behavior observed on
the real hardware is reproduced, in simplified form, from the stage's
"true machine coordinates" and the laser state:

  - no signal right above a metal electrode (edge cut by the beam size)
  - no signal outside the exposed strip region (bond-pad side, and
    beyond the far end)
  - the amplitude falls exponentially with distance from the readout
    CH's electrode (charge sharing)
  - the closer to focus, the smaller the amplitude (shallow valley from
    LGAD gain suppression); defocusing widens the beam and blurs the
    electrode edges
  - the sensor is mounted tilted (focal-plane tilt + in-plane rotation)
  - lowering TUNE raises the amplitude, and above the emission
    threshold (≳97%) no light comes out

The numbers are placeholders matched to the real hardware's order of
magnitude; accuracy as a physics model is not intended (the purpose is
to let the GUI sequences run to completion).
"""

from __future__ import annotations

import math
import random


class SimWorld:
    # Placement of the fake sensor (machine coordinates [µm] and
    # tilts [µm/µm])
    # frame edge / reference electrode / focus Z
    ORIGIN_UM = (108932.0, 100050.0, 1414.0)
    DZDX, DZDY, DYDX = 0.0003, 0.0045, 0.0075
    SHARE_LAMBDA_UM = 300.0     # attenuation length of charge sharing
    BEAM_R0_UM = 3.0            # beam radius at focus
    NOISE_V = 0.0015

    def __init__(self, stage, laser, registry):
        self.stage = stage
        self.laser = laser
        self.registry = registry

    def _machine_pos(self) -> list[float]:
        return [self.stage.pulse_to_um(i, p)
                for i, p in enumerate(self.stage._sim_abs)]

    def _laser_amplitude(self) -> float:
        """Base amplitude [V] set by the laser state
        (TUNE 90%→0.1, 50%→0.21)."""
        st = self.laser._sim
        if not st["emission_pulsed"]:
            return 0.0
        tune = st["tune_permil"] / 100.0
        if st["tune_mode"]:          # auto = controller calibration (mid)
            tune = 70.0
        if tune > 97.0:              # below the emission threshold
            return 0.0
        return 0.1 + (90.0 - tune) * 0.00275

    def amplitude(self, ch: str) -> float:
        """PKPK amplitude [V] of CH at the current stage position."""
        prof = self.registry.current()
        x, y, z = (p - o for p, o in zip(self._machine_pos(),
                                         self.ORIGIN_UM))
        x_off = float(prof["x_frame_offset_um"])
        length = float(prof["strip_length_um"])
        width = float(prof["electrode_width_um"])
        ro = prof["readout"]
        y_ref = float(ro[prof["ref_channel"]]["y_um"])
        pitch = float(prof["pitch_um"])

        # Offset from the focal plane → beam radius and gain suppression
        dz = z - (self.DZDX * x + self.DZDY * y)
        r = math.hypot(self.BEAM_R0_UM, 0.25 * dz)
        gain = 1.0 - 0.35 * math.exp(-(dz / 10.0) ** 2)

        # Signal only inside the exposed strip region
        # (x = −x_off … −(x_off+length))
        def step(d):   # step blurred by the beam size (1 for d>0)
            return 0.5 * (1.0 + math.erf(d / (r * math.sqrt(2.0))))
        exposed = step(-x_off - x) * step(x + x_off + length)

        # Shadowing by the electrode metal (rotated straight lines).
        # Also place the two neighbors outside the readout
        y_line = y - self.DYDX * (x + x_off)
        centers = {c: float(v["y_um"]) - y_ref for c, v in ro.items()}
        metals = list(centers.values())
        metals += [max(metals) + pitch, min(metals) - pitch]
        open_frac = 1.0
        for yc in metals:
            d = y_line - yc
            open_frac -= step(d + width / 2.0) - step(d - width / 2.0)
        open_frac = max(open_frac, 0.0)

        share = math.exp(-abs(y_line - centers.get(ch, 0.0))
                         / self.SHARE_LAMBDA_UM)
        amp = self._laser_amplitude() * gain * exposed * open_frac * share
        return max(amp + random.gauss(0.0, self.NOISE_V), 0.0)
