# -*- coding: utf-8 -*-
"""Convert the LeCroy .trc (sequence) files of a scan folder to one ROOT file.

  TTree "wave"   : 1 entry = 1 segment
                   (index, seg, coords, tune, trig_time/offset, c2[nsamp], ...)
  TTree "points" : 1 entry = 1 scan point (coords, calibration, per-CH
                   summary features)
  meta/*         : string metadata (calibration formula, CH mapping, ...)

Waveforms are stored as raw int16/int8 ADC values; the voltage is
restored with
  V = gain[ch] * raw - offset[ch]
For ToA analysis use the per-segment trig_offset branch, not the header
horiz_offset (avoids fake jitter; see trc_reader.py).

The default output is next to the scan folder (<scan_dir>.root),
i.e. outside laser_gui_v2_en (under data\\...).

usage: python trc_to_root.py <scan_dir> [--out PATH] [--limit N]
                             [--no-raw] [--no-features]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import uproot

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import QUANT, FeatureConfig, segment_features, summarize_channel  # noqa: E402
from scan_data import ScanInfo, load_scan, resolve_scan_dir  # noqa: E402
from trc_reader import read_trc  # noqa: E402

FLUSH_POINTS = 32   # extend the wave tree every this many points


def _wave_branch_types(channels, n_ch, n_samp, sample_dtype):
    t = {
        "index": np.int32, "seg": np.int32,
        "x_um": np.float32, "y_um": np.float32, "z_um": np.float32,
        "xn_um": np.float32, "yn_um": np.float32, "zn_um": np.float32,
        "tune_pct": np.float32, "ok": np.int8,
        "trig_time": np.dtype((np.float64, (n_ch,))),
        "trig_offset": np.dtype((np.float64, (n_ch,))),
    }
    for ch in channels:
        t[ch.lower()] = np.dtype((sample_dtype, (n_samp,)))
    return t


def _points_branch_types(n_ch, with_features):
    t = {
        "index": np.int32,
        "x_um": np.float32, "y_um": np.float32, "z_um": np.float32,
        "xn_um": np.float32, "yn_um": np.float32, "zn_um": np.float32,
        "tune_pct": np.float32, "ok": np.int8, "n_seg": np.int32,
        "horiz_interval": np.float64,
        "gain": np.dtype((np.float32, (n_ch,))),
        "offset": np.dtype((np.float32, (n_ch,))),
        "horiz_offset": np.dtype((np.float64, (n_ch,))),
    }
    if with_features:
        for q in QUANT:
            if q == "n_seg":
                continue
            t[q] = np.dtype((np.float32, (n_ch,)))
    return t


def convert(scan: ScanInfo, out_path: Path | None = None,
            limit: int | None = None, raw: bool = True,
            with_features: bool = True,
            cfg: FeatureConfig | None = None) -> Path:
    cfg = cfg or FeatureConfig()
    out_path = Path(out_path) if out_path else scan.default_root_path()
    rows = list(range(scan.n_points))
    if limit:
        rows = rows[:limit]
    n_ch = len(scan.channels)

    # The waveform format is taken from the first readable file
    n_samp = None
    sample_dtype = np.int16
    for i in rows:
        p = scan.file_for(scan.channels[0], int(scan.index[i]))
        if p.exists():
            w0 = read_trc(p)
            n_samp = w0.n_samples
            sample_dtype = w0.y_raw.dtype.type
            break
    if n_samp is None:
        raise FileNotFoundError(f"No readable .trc found: {scan.scan_dir}")

    ref, pol = None, -1.0
    if with_features:
        # Compute the features with the same rules as the analysis
        # (quality cut and hit decision based on the global noise reference)
        from reduce_scan import _prepass
        ref, pol = _prepass(scan, cfg)
    print(f"convert → {out_path}  points={len(rows)} ch={scan.channels} "
          f"nsamp={n_samp} raw={raw} features={with_features}")
    fout = uproot.recreate(out_path)
    wave_types = _wave_branch_types(scan.channels, n_ch, n_samp,
                                    sample_dtype)
    wave_tree = None
    if raw:
        wave_tree = fout.mktree(
            "wave", wave_types,
            title="raw waveforms (1 entry = 1 segment)")
    points_tree = fout.mktree(
        "points", _points_branch_types(n_ch, with_features),
        title="per-scan-point metadata and features")

    wave_buf = {k: [] for k in wave_types}
    pts_buf = {k: [] for k in _points_branch_types(n_ch, with_features)}
    n_entries = 0
    n_skipped = 0
    horiz_interval = 0.0
    t0 = time.time()

    def flush_waves():
        nonlocal n_entries
        if wave_tree is None or not wave_buf["index"]:
            return
        data = {k: np.concatenate(v) for k, v in wave_buf.items()}
        wave_tree.extend(data)
        n_entries += len(data["index"])
        for v in wave_buf.values():
            v.clear()

    def flush_points():
        if not pts_buf["index"]:
            return
        points_tree.extend({k: np.stack(v) if np.ndim(v[0]) else
                            np.asarray(v) for k, v in pts_buf.items()})
        for v in pts_buf.values():
            v.clear()

    for n_done, i in enumerate(rows):
        idx = int(scan.index[i])
        waves = {}
        for ch in scan.channels:
            p = scan.file_for(ch, idx)
            if p.exists():
                try:
                    waves[ch] = read_trc(p)
                except Exception as exc:
                    print(f"[warn] idx={idx} {ch}: {exc}", file=sys.stderr)
        if len(waves) != n_ch:
            n_skipped += 1
            continue
        wl = [waves[ch] for ch in scan.channels]
        n_seg = min(w.n_segments for w in wl)
        if any(w.n_samples != n_samp for w in wl):
            print(f"[warn] idx={idx}: n_samples mismatch → skipped",
                  file=sys.stderr)
            n_skipped += 1
            continue
        horiz_interval = wl[0].horiz_interval

        coords = {"x_um": scan.x_um[i], "y_um": scan.y_um[i],
                  "z_um": scan.z_um[i], "xn_um": scan.xn[i],
                  "yn_um": scan.yn[i], "zn_um": scan.zn[i],
                  "tune_pct": scan.tune[i]}

        if raw:
            wave_buf["index"].append(np.full(n_seg, idx, np.int32))
            wave_buf["seg"].append(np.arange(n_seg, dtype=np.int32))
            for k, val in coords.items():
                wave_buf[k].append(np.full(n_seg, val, np.float32))
            wave_buf["ok"].append(
                np.full(n_seg, int(scan.ok[i]), np.int8))
            wave_buf["trig_time"].append(np.stack(
                [w.trig_times[:n_seg] for w in wl], axis=1))
            wave_buf["trig_offset"].append(np.stack(
                [w.trig_offsets[:n_seg] for w in wl], axis=1))
            for ch, w in zip(scan.channels, wl):
                wave_buf[ch.lower()].append(w.y_raw[:n_seg])

        pts_buf["index"].append(idx)
        for k, val in coords.items():
            pts_buf[k].append(np.float32(val))
        pts_buf["ok"].append(np.int8(scan.ok[i]))
        pts_buf["n_seg"].append(np.int32(n_seg))
        pts_buf["horiz_interval"].append(np.float64(horiz_interval))
        pts_buf["gain"].append(np.array(
            [w.vertical_gain for w in wl], np.float32))
        pts_buf["offset"].append(np.array(
            [w.vertical_offset for w in wl], np.float32))
        pts_buf["horiz_offset"].append(np.array(
            [w.horiz_offset for w in wl], np.float64))
        if with_features:
            summaries = []
            for c, w in enumerate(wl):
                d = summarize_channel(segment_features(w, cfg, pol), ref[c], cfg, pol)[0]
                summaries.append([d[q] for q in QUANT])
            smat = np.array(summaries, float)    # (n_ch, n_quant)
            for qi, q in enumerate(QUANT):
                if q == "n_seg":
                    continue
                pts_buf[q].append(smat[:, qi].astype(np.float32))

        if (n_done + 1) % FLUSH_POINTS == 0:
            flush_waves()
            flush_points()
        if (n_done + 1) % 100 == 0 or n_done + 1 == len(rows):
            rate = (n_done + 1) / (time.time() - t0)
            eta = (len(rows) - n_done - 1) / max(rate, 1e-9)
            print(f"  {n_done + 1}/{len(rows)} points "
                  f"({rate:.1f} pt/s, ETA {eta / 60:.1f} min)", flush=True)

    flush_waves()
    flush_points()

    fout["meta/scan_name"] = scan.name
    fout["meta/channels"] = ",".join(scan.channels)
    fout["meta/channel_note"] = ("wave branches c1..c4 correspond to the "
                                 "scope CHs. Elements of array branches "
                                 "(gain etc.) follow the meta/channels order")
    fout["meta/n_samples"] = str(n_samp)
    fout["meta/horiz_interval_s"] = str(horiz_interval)
    fout["meta/volts_formula"] = "V = gain[ch]*raw - offset[ch]"
    fout["meta/toa_note"] = ("For ToA use the per-segment trig_offset as "
                             "the time origin (horiz_offset causes fake "
                             "jitter)")
    fout["meta/feature_config"] = repr(cfg.as_dict())
    fout["meta/scan_log_header"] = "\n".join(
        scan.header.get("comments", []))
    fout.close()

    size = out_path.stat().st_size
    print(f"done: {n_entries} wave entries, "
          f"{len(rows) - n_skipped} points (skip {n_skipped}), "
          f"{time.time() - t0:.0f}s, {size / 1e6:.1f} MB → {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description=".trc → single ROOT file conversion")
    ap.add_argument("scan", help="scan folder path or name")
    ap.add_argument("--log", default=None, help="scan-log CSV")
    ap.add_argument("--out", type=Path, default=None,
                    help="output .root (default: <scan_dir>.root)")
    ap.add_argument("--limit", type=int, default=None,
                    help="convert only the first N points (for testing)")
    ap.add_argument("--no-raw", action="store_true",
                    help="omit raw waveforms (wave tree); features only")
    ap.add_argument("--no-features", action="store_true",
                    help="skip the points-tree feature calculation (fast)")
    args = ap.parse_args()

    scan = load_scan(resolve_scan_dir(args.scan), args.log)
    convert(scan, args.out, limit=args.limit, raw=not args.no_raw,
            with_features=not args.no_features)


if __name__ == "__main__":
    main()
