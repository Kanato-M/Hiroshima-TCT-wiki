# -*- coding: utf-8 -*-
"""LeCroy .trc (LECROY_2_3 WAVEDESC テンプレート) の最小リーダ。

シーケンスモード波形 (subarray_count セグメント) 対応。生の int8/int16
サンプル行列と縦軸・横軸スケールを返し、電圧変換は呼び出し側で行える
(大量処理時のメモリ節約)。

★ToA 解析の注意 (2026-07 に実測で確定)★
  ヘッダの horiz_offset は「取得ごとに 1 サンプル周期内の乱数 1 個」で、
  全セグメント共通 t0 に使うとスキャン点ごとの乱数オフセット (±50 ps) と
  偽ジッタ (σ≈29 ps) が乗る。セグメントごとの時刻原点には必ず
  trig_offsets (TRIGTIME 配列の per-segment オフセット) を使うこと。
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
    horiz_interval: float      # サンプル周期 [s]
    horiz_offset: float        # 先頭サンプル時刻 (トリガ基準) [s]
    trig_times: np.ndarray     # (n_segments,) 先頭セグメントからのトリガ時刻 [s]
    trig_offsets: np.ndarray   # (n_segments,) セグメント別の先頭サンプル時刻 [s]

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
