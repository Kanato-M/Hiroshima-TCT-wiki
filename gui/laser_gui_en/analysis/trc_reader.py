# -*- coding: utf-8 -*-
"""Minimal reader for LeCroy .trc (LECROY_2_3 WAVEDESC template).

Supports sequence-mode waveforms (subarray_count segments). Returns the
raw int8/int16 sample matrix plus the vertical/horizontal scales, so the
voltage conversion can be left to the caller (saves memory in bulk
processing).

★ Note for ToA analysis (established by measurement in 2026-07) ★
  The header horiz_offset is "one random number within one sample period
  per acquisition"; using it as a t0 common to all segments adds a random
  per-scan-point offset (±50 ps) and fake jitter (σ≈29 ps). Always use
  trig_offsets (the per-segment offsets of the TRIGTIME array) as the
  time origin of each segment.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np


@dataclass
class TrcWave:
    y_raw: np.ndarray          # (n_segments, n_samples) int8/int16
    vertical_gain: float
    vertical_offset: float     # value = gain*raw - offset  [V]
    horiz_interval: float      # sample period [s]
    horiz_offset: float        # first-sample time (w.r.t. trigger) [s]
    trig_times: np.ndarray     # (n_segments,) trigger time from 1st seg [s]
    trig_offsets: np.ndarray   # (n_segments,) per-seg first-sample time [s]

    @property
    def n_segments(self) -> int:
        return self.y_raw.shape[0]

    @property
    def n_samples(self) -> int:
        return self.y_raw.shape[1]

    def volts(self) -> np.ndarray:
        return self.vertical_gain * self.y_raw.astype(np.float32) \
            - self.vertical_offset

    def time_axis(self) -> np.ndarray:
        return self.horiz_offset + self.horiz_interval * np.arange(
            self.n_samples, dtype=np.float64)


def read_trc(path) -> TrcWave:
    with open(path, "rb") as f:
        head = f.read(64)
        pos = head.find(b"WAVEDESC")
        if pos < 0:
            raise ValueError(f"WAVEDESC not found: {path}")
        f.seek(pos)
        desc = f.read(346)
        comm_order = struct.unpack_from("<h", desc, 34)[0]
        e = "<" if comm_order == 1 else ">"  # 1 = little-endian
        comm_type = struct.unpack_from(e + "h", desc, 32)[0]
        wave_descriptor = struct.unpack_from(e + "i", desc, 36)[0]
        user_text = struct.unpack_from(e + "i", desc, 40)[0]
        trigtime_array = struct.unpack_from(e + "i", desc, 48)[0]
        wave_array_1 = struct.unpack_from(e + "i", desc, 60)[0]
        wave_array_count = struct.unpack_from(e + "i", desc, 116)[0]
        subarray_count = struct.unpack_from(e + "i", desc, 144)[0]
        vertical_gain = struct.unpack_from(e + "f", desc, 156)[0]
        vertical_offset = struct.unpack_from(e + "f", desc, 160)[0]
        horiz_interval = struct.unpack_from(e + "f", desc, 176)[0]
        horiz_offset = struct.unpack_from(e + "d", desc, 180)[0]

        f.seek(pos + wave_descriptor + user_text)
        if trigtime_array:
            tt = np.frombuffer(f.read(trigtime_array),
                               dtype=e + "f8").reshape(-1, 2)
            trig_times, trig_offsets = tt[:, 0].copy(), tt[:, 1].copy()
        else:
            trig_times = np.zeros(max(subarray_count, 1))
            trig_offsets = np.full(max(subarray_count, 1), horiz_offset)

        dtype = np.dtype(e + ("i2" if comm_type == 1 else "i1"))
        y = np.frombuffer(f.read(wave_array_1), dtype=dtype)
        n_seg = max(subarray_count, 1)
        n_samp = wave_array_count // n_seg
        y = y[:n_seg * n_samp].reshape(n_seg, n_samp)

    return TrcWave(y, vertical_gain, vertical_offset,
                   horiz_interval, horiz_offset, trig_times, trig_offsets)


if __name__ == "__main__":
    import sys
    w = read_trc(sys.argv[1])
    v = w.volts()
    print(f"segments={w.n_segments} samples={w.n_samples}")
    print(f"gain={w.vertical_gain:.4g} offset={w.vertical_offset:.4g} "
          f"dt={w.horiz_interval:.4g} t0={w.horiz_offset:.4g}")
    print(f"volts range: {v.min()*1e3:.1f} .. {v.max()*1e3:.1f} mV")
