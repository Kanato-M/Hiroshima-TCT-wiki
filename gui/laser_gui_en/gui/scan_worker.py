# -*- coding: utf-8 -*-
"""Phase 3: execution thread of the auto-scan sequence.

To complete scans without glitches, the start-up always follows this
order (established by the 2026-07 hardware debugging):
  1. Change the oscilloscope settings — turn averaging OFF if it is ON
     (the completion check of sequence/SINGLE acquisitions only works
     with real-time waveforms)
     → sequence setup → waveform-save setup (the built-in counter
     suffix is not used, see below)
  2. Laser setup (auto-ON when requested)
  3. Start the scan

Per measurement point:
  1. Move the stage to the target coordinates → wait for completion
     (★uses the relative move (M:W), not the absolute move (A:W).
      The logical origin is fixed beforehand with an R:W reset, e.g. at
      the C1 edge)
  2. Wait the settle time → pause the screen mirror → reset the scope's
     sweep accumulation
  3. Arm a SINGLE acquisition and wait for sequence completion (STOP)
  4. Save the waveform to the scope's local disk → resume the mirror
  5. Save verification: check that the file count in the scope-side
     folder has increased
  6. Move to the next point and repeat

File-name numbering (fixed 2026-09-07):
  With the scope's built-in counter suffix (EnableCounterSuffix) the
  counter sometimes did not advance on the real hardware and files were
  overwritten under the same name (all points of 2 scans were
  overwritten into CX--scan--00000.trc and lost). The numbering is
  therefore managed on the PC side: every point is saved with an
  explicit TraceTitle="scan--00042" (file names keep the previous
  C2--scan--00042.trc form). In addition the file count of the save
  folder is checked per point, and if it has not increased for 2
  consecutive points the scan is aborted with an error (prevents hours
  of silent data loss).

Screen mirroring (changed 2026-09-07):
  Previously the mirror was stopped for the whole scan, but SCDP is only
  dangerous during the sequence acquisition, so the pause is now limited
  to the acquisition window "accumulation reset to save done". The
  mirror resumes during moves and settling, so the last acquired
  waveform is shown on screen for every point during a scan as well.
  (While the mirror's SCDP read-out holds the lock, the next command is
  delayed by at most 1 frame, but the VICP-side resync (_drain) prevents
  any response shift)

Scan modes:
  - 1d      : 1-axis position scan (e.g. across strips C0→C5 / along the
              9.3 mm strip)
  - 2d      : 2-axis raster scan (with snake option; matches the TCT Scan
              plan)
  - tune    : energy scan sweeping TUNE at a fixed position (tune 50–90%)
  - 1d_tune : combined 1-axis position × TUNE scan — TUNE is swept at
              each position (position is the outer loop, TUNE the inner
              one; the stage only moves when the position changes).
              A wider position step (~50 µm) is recommended

1d/2d scans can apply the sensor tilt correction (focus tracking Z /
rotation tracking Y) — see the build_points docstring and
scan_geometry.py.

Each point's coordinates, TUNE, duration and result are recorded in a
PC-side CSV, used to match the scope's numbered save files.
"""

from __future__ import annotations

import csv
import datetime
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

AXIS_NAMES = ("X", "Y", "Z")


def frange(start: float, stop: float, step: float) -> list[float]:
    """Values from start to stop in step increments (both ends included,
    with rounding-error protection)."""
    if step <= 0:
        raise ValueError("Step must be positive")
    n = int(round(abs(stop - start) / step))
    sign = 1.0 if stop >= start else -1.0
    values = [start + sign * step * i for i in range(n + 1)]
    # Fix a last point pushed beyond stop by rounding
    if (sign > 0 and values[-1] > stop + 1e-9) or \
       (sign < 0 and values[-1] < stop - 1e-9):
        values[-1] = stop
    return values


def _z_mode(params: dict) -> str:
    """Return the Z tracking method (compatible with the old parameter
    tilt_correct).

      "off"          : do not move Z
      "coef_origin"  : anchor the plane of the calibrated coefficients
                       at the logical origin (0,0,0) = focus
      "coef_current" : anchor the plane of the calibrated coefficients
                       at the current position when the scan starts
      "points"       : linear (bilinear) interpolation of the measured Z
                       at the start / end points of the scan range (the
                       four corners for 2D)
    """
    mode = params.get("z_mode")
    if mode is None:
        mode = "coef_current" if params.get("tilt_correct") else "off"
    if mode not in ("off", "coef_origin", "coef_current", "points"):
        raise ValueError(f"Unknown Z tracking method: {mode}")
    return mode


def _frac(value: float, start: float, stop: float) -> float:
    """Position with start→stop mapped to 0→1 (0 when the width is 0)."""
    return 0.0 if stop == start else (value - start) / (stop - start)


def build_points(params: dict, base_pos: list[float]) -> list[dict]:
    """Generate the list of measurement points from the scan parameters.

    Each point is {"pos": [x, y, z] (µm), "tune": float or None}.
    base_pos supplies the coordinates of the axes that are not scanned
    (the current position when the scan starts).

    Sensor tilt correction:
      - Rotation tracking (rot_correct): the strip-center Y drifts along
        X (dY/dX), so Y is shifted relative to the X scan start and the
        scan always runs at the same position relative to the strip.
      - Z tracking (z_mode, see _z_mode):
        * Coefficient methods: the focal plane is approximated by the
          plane z = dzdx·x + dzdy·y.
          "coef_origin" assumes focus at the logical origin (the state
          right after Find Origin) and anchors the plane at the origin,
          so **Z is the same wherever the stage is when the scan
          starts**. "coef_current" is the previous method and assumes
          focus at the current position at start.
        * Measured method "points": the focus Z measured at the start /
          end points (1D) or the four corners (2D) (z_points, absolute
          values in logical coordinates) is interpolated linearly /
          bilinearly. No coefficient calibration is needed, and the
          error inside the scan range is set by the accuracy of the
          end-point measurements. The order of z_points is
            1D: [start, end]
            2D: [fast start×slow start, fast end×slow start,
                 fast start×slow end, fast end×slow end]
      When the Z axis itself is scanned, Z is not overwritten.
    """
    mode = params["mode"]
    points: list[dict] = []

    if mode in ("1d", "2d", "1d_tune"):
        fast_axis = params["fast_axis"]
        fast_vals = frange(params["fast_start"], params["fast_stop"],
                           params["fast_step"])
        if mode == "2d":
            slow_axis = params["slow_axis"]
            if slow_axis == fast_axis:
                raise ValueError("Fast and slow axes must differ")
            slow_vals = frange(params["slow_start"], params["slow_stop"],
                               params["slow_step"])
        else:
            slow_axis, slow_vals = None, [None]
        # 1d_tune: inner TUNE loop at each position
        tune_vals = None
        if mode == "1d_tune":
            tune_vals = frange(params["tune_start"], params["tune_stop"],
                               params["tune_step"])

        scan_axes = {fast_axis, slow_axis} if mode == "2d" else {fast_axis}
        # No correction when X is not scanned (reference = current
        # position, so there is no difference)
        rot = bool(params.get("rot_correct")) and 0 in scan_axes
        # Do not overwrite Z when Z itself is scanned (focus search etc.)
        z_mode = "off" if 2 in scan_axes else _z_mode(params)
        dydx = params.get("rot_dydx", 0.0)
        dzdx = params.get("tilt_dzdx", 0.0)
        dzdy = params.get("tilt_dzdy", 0.0)
        z_pts = list(params.get("z_points") or [])
        if z_mode == "points":
            need = 4 if mode == "2d" else 2
            if len(z_pts) < need:
                raise ValueError(
                    f"{need} measured Z values are required "
                    f"(currently {len(z_pts)})")
        if fast_axis == 0:
            x_ref = params["fast_start"]
        elif mode == "2d" and slow_axis == 0:
            x_ref = params["slow_start"]
        else:
            x_ref = base_pos[0]

        for j, sv in enumerate(slow_vals):
            # Snake (bidirectional) scan: odd rows run in reverse order
            # to shorten the travel time
            row = list(fast_vals)
            if params.get("snake", True) and j % 2 == 1:
                row.reverse()
            for fv in row:
                pos = list(base_pos)
                pos[fast_axis] = fv
                if sv is not None:
                    pos[slow_axis] = sv
                if rot:
                    pos[1] += dydx * (pos[0] - x_ref)
                if z_mode == "coef_origin":
                    pos[2] = dzdx * pos[0] + dzdy * pos[1]
                elif z_mode == "coef_current":
                    pos[2] = base_pos[2] + dzdx * (pos[0] - base_pos[0]) \
                                         + dzdy * (pos[1] - base_pos[1])
                elif z_mode == "points":
                    s = _frac(fv, params["fast_start"], params["fast_stop"])
                    z = z_pts[0] + s * (z_pts[1] - z_pts[0])
                    if sv is not None:
                        t = _frac(sv, params["slow_start"],
                                  params["slow_stop"])
                        z_far = z_pts[2] + s * (z_pts[3] - z_pts[2])
                        z += t * (z_far - z)
                    pos[2] = z
                if tune_vals is None:
                    points.append({"pos": pos, "tune": None})
                else:
                    for tv in tune_vals:
                        points.append({"pos": list(pos), "tune": tv})

    elif mode == "tune":
        for tv in frange(params["tune_start"], params["tune_stop"],
                         params["tune_step"]):
            points.append({"pos": list(base_pos), "tune": tv})
    else:
        raise ValueError(f"Unknown scan mode: {mode}")

    return points


def scan_corners(params: dict, base_pos: list[float]) -> list[list[float]]:
    """Return the XY of the measurement positions of the measured-Z
    method (start / end points or four corners).

    Same order as z_points in build_points. These are the actual move
    targets including the rotation-tracking shift; Z stays at base_pos
    (the focus is adjusted and measured on the spot).
    """
    p = dict(params, z_mode="off", snake=False)
    if p["mode"] == "1d_tune":
        p["mode"] = "1d"
    if p["mode"] not in ("1d", "2d"):
        return []
    pts = build_points(p, base_pos)
    n_fast = len(frange(p["fast_start"], p["fast_stop"], p["fast_step"]))
    idx = [0, n_fast - 1]
    if p["mode"] == "2d":
        idx += [len(pts) - n_fast, len(pts) - 1]
    return [list(pts[i]["pos"]) for i in idx]


def count_points(params: dict) -> int:
    """For the UI estimate: compute only the number of points."""
    return len(build_points(params, [0.0, 0.0, 0.0]))


class ScanWorker(QThread):
    """Thread that runs the scan sequence.

    All device wrappers are serialized with locks, so they can be used
    safely in parallel with the GUI-side polling (position display,
    screen mirror, etc.).
    """

    progress = Signal(int, int, str)      # (points done, total, status msg)
    finished_scan = Signal(bool, str)     # (completed normally?, message)

    def __init__(self, scope, stage, laser, params: dict,
                 csv_path: str | Path, parent=None):
        super().__init__(parent)
        self.scope = scope
        self.stage = stage
        self.laser = laser
        self.params = params
        self.csv_path = Path(csv_path)
        self._abort = False
        self._file_count = -1   # save check: file count in the scope folder
        self._save_miss = 0     # save check: consecutive non-increases

    def abort(self):
        """Request a scan abort (stops at the next checkpoint)."""
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        p = self.params
        laser_turned_on = False
        try:
            # --- Preparation: build the point list relative to the
            #     current position (logical coordinates) ---
            if self.stage.connected:
                base_pos, _ = self.stage.get_position_um()
            else:
                base_pos = [0.0, 0.0, 0.0]  # tune scans need no stage
            points = build_points(p, base_pos)
            total = len(points)

            # === Step 1: change the scope settings ===
            # Averaging must be OFF: without real-time waveforms the
            # completion check (waiting for STOP) of SINGLE/sequence
            # acquisitions does not work
            if p.get("avg_off", True):
                self.scope.set_averaging_off()
            if p.get("use_sequence"):
                self.scope.set_sequence(True, p["segments"])
            # The numbering is managed on the PC side (counter=False).
            # The built-in counter sometimes does not advance, and all
            # points were lost to same-name overwrites (2026-09-07)
            self.scope.configure_waveform_save(
                p["save_dir"], p["prefix"],
                source=p.get("save_source", "AllDisplayed"),
                fmt=p.get("save_format", "Binary"), counter=False)
            # Baseline of the save check (file count at scan start;
            # normally 0)
            self._file_count = self.scope.count_saved_files(p["save_dir"])
            self._save_miss = 0

            # === Step 2: laser setup (auto-ON when requested) ===
            if p.get("laser_on") and self.laser.connected:
                self.laser.set_emission(True)
                laser_turned_on = True

            # === Step 3: start the scan ===
            # The screen mirror is not stopped: it is paused inside
            # _measure_point only during the acquisition window where
            # SCDP is dangerous, and keeps running during moves/settling
            self._write_csv_header(p, total, base_pos)

            t_scan_start = time.monotonic()
            done = 0
            for i, pt in enumerate(points):
                if self._abort:
                    break
                # Estimate the remaining time (ETA) from the average
                # time per point so far
                if done > 0:
                    eta = (time.monotonic() - t_scan_start) / done * (total - done)
                    eta_str = f" / ~{eta / 60:.1f} min left"
                else:
                    eta_str = ""
                x, y, z = pt["pos"]
                desc = f"X={x:.1f} Y={y:.1f} Z={z:.1f} µm"
                if pt["tune"] is not None:
                    desc += f" TUNE={pt['tune']:.1f}%"
                self.progress.emit(done, total,
                                   f"Point {i + 1}/{total}: {desc}{eta_str}")

                status = self._measure_point(pt, i)
                done += 1
                self._write_csv_row(i, pt, status)
                if status in ("ok", "timeout"):
                    self._verify_saved(i)

            if self._abort:
                self._safe_stop()
                self.finished_scan.emit(
                    False, f"Scan aborted ({done}/{total} points done)")
            else:
                elapsed = (time.monotonic() - t_scan_start) / 60
                self.progress.emit(done, total, "Done")
                self.finished_scan.emit(
                    True,
                    f"Scan complete: {done} points / {elapsed:.1f} min\n"
                    f"Log: {self.csv_path}")
        except Exception as exc:
            self._safe_stop()
            self.finished_scan.emit(False, f"Scan error: {exc}")
        finally:
            # Safety net: resume the mirror even after an exception or
            # abort inside the acquisition window
            self.scope.mirror_paused = False
            # For safety, a laser that was auto-switched ON (or when
            # requested) is always switched back OFF
            if p.get("laser_off", True) and laser_turned_on \
                    and self.laser.connected:
                try:
                    self.laser.set_emission(False)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def _measure_point(self, pt: dict, index: int) -> str:
        """One measurement point: (set TUNE) → move → settle → acquire →
        save.

        The number in the save file name is given explicitly from index
        (title="scan--00042" → C2--scan--00042.trc; it always matches
        the CSV index and does not depend on the scope's built-in
        counter).
        """
        p = self.params
        title = f"{p['prefix']}--{index:05d}"

        # For TUNE scans, set the value in manual mode
        if pt["tune"] is not None:
            self.laser.set_tune_mode(False)
            self.laser.set_tune_percent(pt["tune"])

        # Stage move → wait for completion (tune scans stay at a fixed
        # position, so no move). The move is a relative move (M:W): the
        # absolute move (A:W) had glitches, so the stage moves by
        # difference pulses to coordinates relative to the logical origin
        # (frame edge = 0,0,0). In 1d_tune, consecutive points at the
        # same position (inner TUNE loop) are just 0-pulse moves and the
        # stage does not actually move
        if p["mode"] in ("1d", "2d", "1d_tune"):
            x, y, z = pt["pos"]
            self.stage.move_to_um_relative(x, y, z)
            self.stage.wait_ready(timeout=p.get("move_timeout_s", 120),
                                  abort_check=lambda: self._abort)
            if self._abort:
                return "aborted"

        # Settle wait (until vibrations die out). The screen mirror is
        # running meanwhile, so the waveform acquired at the previous
        # point can be checked in real time
        self._sleep_abortable(p["settle_s"])
        if self._abort:
            return "aborted"

        # Acquisition window: the screen mirror is stopped only here
        # (SCDP during a sequence acquisition causes timeouts, response
        # shifts and interference with the acquisition)
        self.scope.mirror_paused = True
        try:
            # Acquire: accumulation reset → arm SINGLE → wait for
            # completion (STOP)
            self.scope.clear_sweeps()
            self.scope.arm_single()
            deadline = time.monotonic() + p["point_timeout_s"]
            while time.monotonic() < deadline:
                if self._abort:
                    return "aborted"
                if self.scope.acquisition_stopped():
                    break
                time.sleep(0.2)
            else:
                # Timeout: stop the acquisition and go on to the next
                # point (missing triggers etc.)
                self.scope.set_trigger_mode("STOP")
                self.scope.save_waveform_now(title)
                return "timeout"

            # Save the waveform (to the scope's local disk, with the
            # PC-managed number)
            self.scope.save_waveform_now(title)
            return "ok"
        finally:
            self.scope.mirror_paused = False

    def _verify_saved(self, index: int) -> None:
        """After saving, check that the file count in the scope-side
        folder has increased.

        If it has not increased for 2 consecutive points the scan is
        aborted (prevents running for hours without any data being kept,
        e.g. due to same-name overwrites). Where the file count cannot be
        checked (VBS trouble etc.) the verification is skipped.
        """
        n = self.scope.count_saved_files(self.params["save_dir"])
        if n < 0 or self._file_count < 0:
            return  # cannot check: continue unverified (old behavior)
        if n > self._file_count:
            self._file_count = n
            self._save_miss = 0
            return
        self._save_miss += 1
        if self._save_miss >= 2:
            raise RuntimeError(
                f"The number of saved files on the scope is not increasing "
                f"({n} files at point {index}). The scan was aborted "
                f"because of a possible same-name overwrite or broken "
                f"save setup. "
                f"Check the scope's Save Waveform settings and the save "
                f"folder ({self.params['save_dir']})")

    def _sleep_abortable(self, seconds: float):
        # Waits and timeouts are measured with the monotonic clock: the
        # wall clock (time.time) can jump discontinuously on OS time
        # synchronization, causing false timeouts or overlong waits
        deadline = time.monotonic() + seconds
        while not self._abort:
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            time.sleep(min(0.1, remain))

    def _safe_stop(self):
        """Stop the stage and scope safely on abort/error."""
        try:
            self.stage.stop()
        except Exception:
            pass
        try:
            self.scope.set_trigger_mode("STOP")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CSV log (for matching the scope's numbered files)
    # ------------------------------------------------------------------
    def _write_csv_header(self, p: dict, total: int,
                          base_pos: list[float] | None = None):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# AC-LGAD auto scan log "
                        f"{datetime.datetime.now().isoformat()}"])
            w.writerow([f"# mode={p['mode']} total_points={total} "
                        f"segments={p.get('segments')} "
                        f"settle_s={p['settle_s']} prefix={p['prefix']} "
                        f"save_dir={p['save_dir']}"])
            # Tilt coefficients and reference position (coordinates are
            # logged in logical coordinates after the correction)
            base = "" if base_pos is None else \
                ",".join(f"{v:.1f}" for v in base_pos)
            z_pts = ";".join(f"{v:.1f}" for v in p.get("z_points") or [])
            w.writerow([f"# sensor={p.get('sensor_id', '')} "
                        f"sensor_type={p.get('sensor_type', 'strip')} "
                        f"z_follow={_z_mode(p)} z_points_um=[{z_pts}]"])
            w.writerow([f"# tilt_correct={_z_mode(p) != 'off'} "
                        f"rot_correct={bool(p.get('rot_correct'))} "
                        f"dzdx={p.get('tilt_dzdx', 0):.6g} "
                        f"dzdy={p.get('tilt_dzdy', 0):.6g} "
                        f"dydx={p.get('rot_dydx', 0):.6g} "
                        f"base_pos_um=({base})"])
            w.writerow(["index", "x_um", "y_um", "z_um", "tune_pct",
                        "status", "timestamp"])

    def _write_csv_row(self, index: int, pt: dict, status: str):
        x, y, z = pt["pos"]
        tune = "" if pt["tune"] is None else f"{pt['tune']:.1f}"
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                [index, f"{x:.2f}", f"{y:.2f}", f"{z:.2f}", tune,
                 status, datetime.datetime.now().isoformat()])
