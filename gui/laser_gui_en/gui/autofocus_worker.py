# -*- coding: utf-8 -*-
"""Execution thread of the Z autofocus (waveform-amplitude based).

With the laser ON, Z is swept and the stage automatically moves to the
Z of the extremum of the scope's amplitude measurement (PAVA?). The
offset problem relative to the camera focus does not arise.

★Corrected premise (established by measurement on 2026-09-07)★
  The better the focus, the SMALLER the amplitude — LGAD gain
  suppression caused by the increased local carrier density (plasma
  effect). The amplitude was minimal when the camera focus was best.
  The default scheme is "put the beam **on the silicon beside the
  strip, where the signal is clearly visible** and search for the Z of
  minimum amplitude" (metric="min", center of the shallow valley =
  focus). Directly above the electrode (~50 µm wide) does not work: the
  spot stays within the metal even when defocused and no valley is
  visible. The old "maximum amplitude" scheme (metric="max") is kept
  for compatibility, but it most likely does not hit the focus.

Intended to be run at the start position before an auto scan (the tilt
correction (focus tracking) inherits the Z at start as its reference
plane). Normally the "Find Origin" of the automatic alignment includes
this procedure.

Procedure:
  1. Sequence OFF + trigger NORM (to read the amplitude in real time).
     Screen mirroring is also paused (avoids SCDP response delays)
  2. Backlash protection: move below the sweep start (-backlash) once,
     then sweep upward in a single direction
  3. At each Z, settle, read PAVA several times and average →
     record the (Z, amplitude) curve
  4. Move to the Z of the amplitude extremum (min: minimum / max:
     maximum), again approaching from below. The curve is saved as CSV
  5. On abort/error, return to the original Z

Note: if the extremum lies at an end of the sweep range, the range may
be too small; the message recommends a re-run.
"""

from __future__ import annotations

import csv
import datetime
import math
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .scan_worker import frange


class AutofocusWorker(QThread):
    progress = Signal(int, int, str)      # (points done, total, message)
    finished_focus = Signal(bool, str)    # (success?, message)

    def __init__(self, scope, stage, params: dict, csv_path: str | Path,
                 parent=None):
        """params: ch, range_um, step_um, settle_s, reads, backlash_um,
        pava_param, move_timeout_s,
        metric: "min" (minimum amplitude on the electrode = focus,
        default) / "max" (old scheme)"""
        super().__init__(parent)
        self.scope = scope
        self.stage = stage
        self.params = params
        self.csv_path = Path(csv_path)
        self._abort = False

    def abort(self):
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        self.scope.mirror_paused = True
        try:
            self._run()
        except Exception as exc:
            self.finished_focus.emit(False, f"Autofocus error: {exc}")
        finally:
            self.scope.mirror_paused = False

    def _run(self):
        p = self.params
        ch = p["ch"]
        pava = p.get("pava_param", "PKPK")
        settle = p.get("settle_s", 0.3)
        reads = p.get("reads", 3)
        backlash = p.get("backlash_um", 10.0)
        timeout = p.get("move_timeout_s", 30.0)

        # 1. put the acquisition into a state where the amplitude can be
        #    read in real time
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        pos, _ = self.stage.get_position_um()
        x0, y0, z0 = pos
        zs = [z0 + dz for dz in frange(-p["range_um"], p["range_um"],
                                       p["step_um"])]
        total = len(zs)

        def goto_z(z):
            self.stage.move_to_um_relative(x0, y0, z)
            self.stage.wait_ready(timeout=timeout,
                                  abort_check=lambda: self._abort)

        def restore():
            try:
                goto_z(z0)
            except Exception:
                pass

        # 2. backlash protection: enter from below, one direction only
        goto_z(zs[0] - backlash)
        if self._abort:
            restore()
            self.finished_focus.emit(False, "Autofocus aborted")
            return

        # 3. sweep and record the amplitude curve
        curve: list[tuple[float, float]] = []
        for i, z in enumerate(zs):
            if self._abort:
                restore()
                self.finished_focus.emit(False,
                                         "Autofocus aborted")
                return
            self.progress.emit(i, total,
                               f"Autofocus {i + 1}/{total}: "
                               f"Z={z:.1f} µm")
            goto_z(z)
            time.sleep(settle)
            vals = []
            for _ in range(reads):
                v, state = self.scope.measure_pava(ch, pava)
                if not math.isnan(v) and state.upper() not in ("NP", "IV"):
                    vals.append(v)
                time.sleep(0.05)
            if not vals:
                restore()
                self.finished_focus.emit(
                    False,
                    f"Cannot read the amplitude ({pava}) of {ch} "
                    "(no waveform?). Check that the laser is ON, the "
                    "trigger is running (waveforms updating in NORM) and "
                    "the beam is directly above a strip")
                return
            curve.append((z, sum(vals) / len(vals)))

        # 4. move to the Z of the extremum (same direction = from below,
        #    for repeatability)
        # min: the bottom of the V shape is flat over the depth of focus,
        # so the center of the bottom is taken as the midpoint of the two
        # flank edges at the 50% threshold (the plain minimum point can
        # jump to an end because of noise)
        metric = p.get("metric", "min")
        edge = False
        if metric == "min":
            from .alignment_worker import dip_center, fit_focus
            zs_c = [z for z, _ in curve]
            vs_c = [v for _, v in curve]
            try:
                # minimum of the Lorentzian + linear-baseline fit curve
                # (same estimator as the fine mode of Auto Alignment; on
                # the 2026-10-05 hardware data two measurements at the
                # same spot agreed within 0.1 µm)
                try:
                    best_z = fit_focus(zs_c, vs_c, min_depth=0.004)["center"]
                except ValueError as exc:
                    if "edge of the range" in str(exc):
                        raise
                    best_z, _, _, _ = dip_center(
                        zs_c, vs_c, min_width=2.0,
                        max_width=4.0 * p["range_um"],
                        max_ratio=0.9, min_depth=0.004)
                best_z = round(best_z / 2.0) * 2.0
                best_v = min(curve, key=lambda t: abs(t[0] - best_z))[1]
            except ValueError:
                # valley cannot be located (range too small / signal
                # unclear) → minimum point + warning
                best_z, best_v = min(curve, key=lambda t: t[1])
                edge = True
        else:
            best_z, best_v = max(curve, key=lambda t: t[1])
            edge = best_z in (curve[0][0], curve[-1][0])
        goto_z(zs[0] - backlash)
        goto_z(best_z)
        self._write_csv(ch, pava, z0, best_z, curve)

        label = ("min (valley center = focus)" if metric == "min"
                 else "max (old scheme)")
        msg = (f"Best focus: Z = {best_z:.1f} µm "
               f"(moved {best_z - z0:+.1f} µm)\n"
               f"{ch} {pava} {label} {best_v * 1e3:.1f} mV\n"
               f"Curve log: {self.csv_path}")
        if edge:
            msg += ("\n★ The valley/extremum could not be located within "
                    "the range. Re-running with a wider range or with the "
                    "beam repositioned directly above the electrode is "
                    "recommended ★")
        self.progress.emit(total, total, "Done")
        self.finished_focus.emit(True, msg)

    # ------------------------------------------------------------------
    def _write_csv(self, ch, pava, z0, best_z, curve):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# Z autofocus {ch} {pava} "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# z_start={z0:.1f} z_best={best_z:.1f}"])
            w.writerow(["z_um", "amplitude_v"])
            for z, v in curve:
                w.writerow([f"{z:.2f}", f"{v:.6g}"])
