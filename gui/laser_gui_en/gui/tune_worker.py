# -*- coding: utf-8 -*-
"""Execution thread of the TUNE optimization (waveform-amplitude based).

The optimal value of the PiLAS TUNE (lasing efficiency) depends on the
repetition frequency. Changing the frequency while keeping a manual TUNE
value that was tuned at 40 MHz can drop below the lasing threshold so
that no light comes out and "no signal is visible at all" (TRIG OUT keeps
firing electrically, so the scope still triggers but the waveform stays
flat — the symptom confirmed on the hardware in 2026-07).

This worker keeps the current frequency, lowers TUNE from the high (weak
light) side while reading the scope amplitude (PAVA), and finds and sets
the TUNE closest to the target amplitude. After a frequency change, run
this with position and focus untouched and the operating point for that
frequency is found within tens of seconds.

Measurement method (decided by the number of params["channels"]):
  - Single CH: match the amplitude of that CH to the target (classic)
  - Sum of two adjacent CHs (added 2026-10-07): match the sum
    A_lead + A_sub of the amplitudes of the CHs that read two neighboring
    strips. In an AC-LGAD the sum A_lead + A_sub changes little when the
    beam moves between the two strips (charge sharing only redistributes
    it), so measuring the sum midway between the strips (250 µm from the
    electrode for a 500 µm pitch) matches a MIP-equivalent signal in a way
    that is insensitive to position errors. The DESY beam test (2026)
    gave A_lead + A_sub = 64 ± 1 mV midway between strips (for
    reference: 77 mV on a single CH next to a strip). If
    params["move_to"] is given, the stage moves to those logical
    coordinates (strip midpoint) before measuring and returns to
    params["return_to"] afterwards.

Procedure:
  1. Sequence OFF + trigger NORM + mirror paused (for amplitude reads)
     (move to the measurement position if requested)
  2. Switch TUNE to manual; coarse sweep: from start (default 100%) toward
     stop (default 0%) in steps (default 2%), measuring the amplitude at
     each point, stopping once the target is crossed
  3. Fine sweep: re-measure the interval that crossed the target (previous
     point … crossing point) in fine_step (default 0.5%) steps with many
     reads (median) and stop at the TUNE that falls within the tolerance
     (±tol). If none does, adopt the measured TUNE closest to the target
     (with the coarse sweep alone the threshold crossing is decided by a
     few mV of read noise; the 2026-10-05 log jumped 65 → 77 mV between
     adjacent TUNE values)
  4. Set the adopted TUNE, verify (many reads) and report
  5. If the coarse sweep never reaches the target, set the TUNE of maximum
     amplitude and report with a warning (if everything stayed ~0, restore
     the original TUNE and report an error). If the very first point
     (maximum TUNE = weakest) already exceeds the target, set that TUNE
     and warn that attenuation is needed
  6. The curve (phase, tune, amplitude per CH, sum, spread) is saved as CSV

Prerequisites: laser ON, beam directly above a strip or midway between
strips, focused, scope trigger running (same as the Z autofocus).
"""

from __future__ import annotations

import csv
import datetime
import math
import statistics
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .scan_worker import frange


class TuneOptimizeWorker(QThread):
    progress = Signal(int, int, str)    # (points done, total, message)
    finished_tune = Signal(bool, str)   # (success?, message)

    def __init__(self, scope, laser, params: dict, csv_path: str | Path,
                 stage=None, parent=None):
        """params:
          channels    : list of measured CHs (1 = single CH, 2 = adjacent sum)
          ch          : (legacy) single CH used when channels is omitted
          target_mv   : target amplitude (sum) [mV]
          tol_mv      : tolerance [mV] (default 1.0)
          tune_start / tune_stop / tune_step : coarse sweep [%]
          fine_step   : step of the fine sweep [%] (default 0.5, 0 = none)
          settle_s    : settling time after a TUNE change [s]
          reads       : number of reads in the coarse sweep (default 3)
          fine_reads  : number of reads in fine sweep / verification (default 8)
          pava_param  : PAVA parameter (default PKPK)
          move_to     : logical coordinates [x, y, z] to move to before
                        measuring (None = no move)
          return_to   : logical coordinates to return to afterwards (None = stay)
          move_timeout_s : timeout of the stage moves [s]
        """
        super().__init__(parent)
        self.scope = scope
        self.laser = laser
        self.stage = stage
        self.params = params
        self.csv_path = Path(csv_path)
        self._abort = False
        self._rows: list[dict] = []     # CSV rows (phase, tune, amps, sum, std)
        self._channels: list[str] = list(params.get("channels")
                                         or [params["ch"]])

    def abort(self):
        self._abort = True

    # ------------------------------------------------------------------
    def run(self):
        self.scope.mirror_paused = True
        try:
            self._run()
        except Exception as exc:
            self.finished_tune.emit(False, f"TUNE optimization error: {exc}")
        finally:
            self.scope.mirror_paused = False

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------
    def _read_once(self, ch: str, pava: str) -> float | None:
        v, state = self.scope.measure_pava(ch, pava)
        if math.isnan(v) or state.upper() in ("NP", "IV"):
            return None
        return v

    def _measure(self, reads: int, pava: str) -> tuple[dict, float, float]:
        """Read every CH `reads` times and return (CH → median [V],
        sum [V], standard deviation of the sum). Failed reads are
        dropped; if all fail the value is 0."""
        per_ch: dict[str, list[float]] = {ch: [] for ch in self._channels}
        sums: list[float] = []
        for _ in range(reads):
            vals = []
            for ch in self._channels:
                v = self._read_once(ch, pava)
                if v is not None:
                    per_ch[ch].append(v)
                vals.append(0.0 if v is None else v)
            sums.append(sum(vals))
            time.sleep(0.05)
        amps = {ch: (statistics.median(v) if v else 0.0)
                for ch, v in per_ch.items()}
        total = sum(amps.values())
        std = statistics.pstdev(sums) if len(sums) > 1 else 0.0
        return amps, total, std

    def _point(self, phase: str, tv: float, reads: int, settle: float,
               pava: str) -> float:
        """Set TUNE to tv, measure, record and return the sum [V]."""
        self.laser.set_tune_percent(tv)
        time.sleep(settle)
        amps, total, std = self._measure(reads, pava)
        self._rows.append({"phase": phase, "tune": tv, "amps": amps,
                           "sum": total, "std": std})
        return total

    def _fmt(self, row: dict) -> str:
        amps = row["amps"]
        if len(self._channels) == 1:
            return f"{self._channels[0]} {row['sum'] * 1e3:.1f} mV"
        parts = " + ".join(f"{ch} {amps[ch] * 1e3:.1f}"
                           for ch in self._channels)
        return f"{parts} = {row['sum'] * 1e3:.1f} mV"

    # ------------------------------------------------------------------
    # Stage moves (measurement midway between strips)
    # ------------------------------------------------------------------
    def _goto(self, pos, timeout: float) -> None:
        x, y, z = pos
        self.stage.move_to_um_relative(x, y, z)
        self.stage.wait_ready(timeout=timeout,
                              abort_check=lambda: self._abort)

    def _return(self) -> str:
        """Return to the original position at the end. Returns a note if
        the return move failed."""
        pos = self.params.get("return_to")
        if pos is None or self.stage is None:
            return ""
        try:
            self._abort = False      # complete the return move even after an abort
            self._goto(pos, self.params.get("move_timeout_s", 30.0))
            return ""
        except Exception as exc:
            return (f"\nNote: returning to the original position "
                    f"({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f}) failed: {exc}")

    # ------------------------------------------------------------------
    def _run(self):
        p = self.params
        pava = p.get("pava_param", "PKPK")
        settle = p.get("settle_s", 0.3)
        reads = p.get("reads", 3)
        fine_reads = p.get("fine_reads", 8)
        fine_step = p.get("fine_step", 0.5)
        tol_v = p.get("tol_mv", 1.0) / 1e3
        target_v = p["target_mv"] / 1e3
        chs = "+".join(self._channels)
        label = (f"{chs} sum" if len(self._channels) > 1 else chs)

        # 1. put the acquisition into a state where the amplitude can be
        #    read in real time
        self.scope.set_sequence(False)
        self.scope.set_trigger_mode("NORM")

        # note the current state (frequency, TUNE)
        st = self.laser.get_status()
        freq_hz = st["int_freq_hz"]
        # Original TUNE [%] to restore on abort/failure (mState is in
        # units of 0.01%. The old version converted it wrongly and noted
        # a value 10 times too large, so restoring pinned it at 100%)
        tune0 = st["tune_percent"]

        # move to the measurement position (midway between strips)
        move_to = p.get("move_to")
        if move_to is not None:
            if self.stage is None:
                raise RuntimeError("Moving to the measurement position "
                                   "requires a stage connection")
            self.progress.emit(0, 0,
                               f"Moving to the measurement position "
                               f"({move_to[0]:.0f}, {move_to[1]:.0f}, "
                               f"{move_to[2]:.0f}) µm...")
            self._goto(move_to, p.get("move_timeout_s", 30.0))
            if self._abort:
                self.finished_tune.emit(
                    False, "TUNE optimization aborted (TUNE unchanged)"
                    + self._return())
                return
            time.sleep(0.2)

        self.laser.set_tune_mode(False)  # manual

        def finish(ok: bool, message: str):
            self._write_csv(pava, freq_hz)
            self.finished_tune.emit(ok, message + self._return())

        def aborted():
            self.laser.set_tune_percent(tune0)
            finish(False, "TUNE optimization aborted "
                          f"(TUNE restored to {tune0:.1f}%)")

        # 2. coarse sweep: lower TUNE until the target is crossed
        tunes = frange(p.get("tune_start", 100.0), p.get("tune_stop", 0.0),
                       p.get("tune_step", 2.0))
        total = len(tunes)
        coarse: list[tuple[float, float]] = []
        cross = None      # index at which the target was reached
        for i, tv in enumerate(tunes):
            if self._abort:
                aborted()
                return
            self.progress.emit(i, total,
                               f"Coarse sweep {i + 1}/{total}: TUNE {tv:.1f}%")
            amp = self._point("coarse", tv, reads, settle, pava)
            coarse.append((tv, amp))
            self.progress.emit(i + 1, total,
                               f"Coarse sweep TUNE {tv:.1f}%: "
                               f"{self._fmt(self._rows[-1])}")
            if amp >= target_v - tol_v:
                cross = i
                break

        tgt = f"{p['target_mv']:.0f} mV"
        tol_txt = f"±{p['tol_mv']:.1f}" if "tol_mv" in p else ""

        if cross is None:
            # target not reached: adopt the TUNE of maximum amplitude
            # (restore the original if everything was dead)
            best_tv, best_amp = max(coarse, key=lambda t: t[1])
            if best_amp * 1e3 >= 5.0:  # clearly above the noise (a few mV)
                self.laser.set_tune_percent(best_tv)
                finish(True,
                       f"Target {tgt} ({label}) not reached; set the "
                       f"maximum-amplitude TUNE = {best_tv:.1f}% "
                       f"({best_amp * 1e3:.1f} mV) instead\n"
                       f"At {freq_hz / 1e6:.4g} MHz no higher amplitude is "
                       "available. Check whether this meets the energy "
                       f"requirement\nCurve log: {self.csv_path}")
            else:
                self.laser.set_tune_percent(tune0)
                finish(False,
                       f"Signal stayed near 0 for every TUNE (max "
                       f"{best_amp * 1e3:.1f} mV @ {best_tv:.1f}%). TUNE "
                       f"restored to {tune0:.1f}%\n"
                       "Check: laser ON / beam above a strip / focus / "
                       f"{chs} cabling and trigger operation\n"
                       f"Curve log: {self.csv_path}")
            return

        # 3. fine sweep: re-measure the crossed interval (previous point …
        #    crossing point) finely
        candidates: list[tuple[float, float]] = [coarse[cross]]
        lo_tv, lo_amp = coarse[cross]            # at/above target (lower TUNE side)
        hi_tv = coarse[cross - 1][0] if cross > 0 else None
        if hi_tv is not None:
            candidates.append(coarse[cross - 1])
        in_tol = abs(lo_amp - target_v) <= tol_v
        if hi_tv is not None and fine_step > 0 and not in_tol \
                and hi_tv - lo_tv > fine_step * 1.5:
            fine = frange(hi_tv - fine_step, lo_tv + fine_step, fine_step)
            n = len(fine)
            for j, tv in enumerate(fine):
                if self._abort:
                    aborted()
                    return
                self.progress.emit(j, n,
                                   f"Fine sweep {j + 1}/{n}: TUNE {tv:.1f}%")
                amp = self._point("fine", tv, fine_reads, settle, pava)
                candidates.append((tv, amp))
                self.progress.emit(
                    j + 1, n, f"Fine sweep TUNE {tv:.1f}%: "
                    f"{self._fmt(self._rows[-1])}")
                if abs(amp - target_v) <= tol_v:
                    break
        elif hi_tv is not None and not in_tol:
            # narrow interval (the coarse step is already fine): re-measure
            # the previous point with many reads as well
            if self._abort:
                aborted()
                return
            amp = self._point("fine", hi_tv, fine_reads, settle, pava)
            candidates.append((hi_tv, amp))

        # 4. adopt the TUNE closest to the target and verify
        best_tv, _ = min(candidates, key=lambda t: abs(t[1] - target_v))
        if self._abort:
            aborted()
            return
        self.progress.emit(0, 0, f"Verifying at TUNE {best_tv:.1f}%...")
        self._point("verify", best_tv, fine_reads, settle, pava)
        row = self._rows[-1]
        amp = row["sum"]
        dev_mv = (amp - target_v) * 1e3
        ok_tol = abs(amp - target_v) <= tol_v
        self.progress.emit(1, 1, "Done")

        first_tv, first_amp = coarse[0]
        if cross == 0 and first_amp > target_v + tol_v:
            # above target even at maximum TUNE (weakest): the laser cannot
            # go lower
            finish(True,
                   f"Even at maximum TUNE ({first_tv:.1f}%) the target {tgt} "
                   f"is exceeded: {self._fmt(row)}\n"
                   f"TUNE = {best_tv:.1f}% was set, but at this frequency "
                   "the light cannot be made weaker. Attenuate with an ND "
                   "filter or reconsider the target\n"
                   f"Frequency {freq_hz / 1e6:.4g} MHz / verification "
                   f"±{row['std'] * 1e3:.1f} mV (std, {fine_reads} reads)\n"
                   f"Curve log: {self.csv_path}")
            return

        head = (f"Target {tgt} {tol_txt} ({label}) reached" if ok_tol
                else f"Adopted the point closest to the target {tgt} "
                     f"{tol_txt} ({label}) (deviation {dev_mv:+.1f} mV)")
        finish(True,
               f"{head}: TUNE = {best_tv:.1f}%\n"
               f"Verification: {self._fmt(row)} (std ±{row['std'] * 1e3:.1f} "
               f"mV, {fine_reads} reads)\n"
               f"Frequency {freq_hz / 1e6:.4g} MHz / TUNE set to manual "
               f"{best_tv:.1f}%\nCurve log: {self.csv_path}")

    # ------------------------------------------------------------------
    def _write_csv(self, pava, freq_hz):
        p = self.params
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        move_to = p.get("move_to")
        pos = "" if move_to is None else "/".join(f"{v:.1f}" for v in move_to)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([f"# TUNE optimization {'+'.join(self._channels)} "
                        f"{pava} {datetime.datetime.now().isoformat()}"])
            w.writerow([f"# freq_hz={freq_hz} target_mv={p['target_mv']} "
                        f"tol_mv={p.get('tol_mv', '')} "
                        f"mode={'sum2' if len(self._channels) > 1 else 'single'} "
                        f"pos_um={pos}"])
            w.writerow(["phase", "tune_pct"]
                       + [f"{ch}_v" for ch in self._channels]
                       + ["sum_v", "sum_std_v"])
            for r in self._rows:
                w.writerow([r["phase"], f"{r['tune']:.1f}"]
                           + [f"{r['amps'][ch]:.6g}" for ch in self._channels]
                           + [f"{r['sum']:.6g}", f"{r['std']:.3g}"])
