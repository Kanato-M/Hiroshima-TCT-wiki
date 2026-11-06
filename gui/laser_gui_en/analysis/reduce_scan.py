# -*- coding: utf-8 -*-
"""Stage 1: reduce every scan point to per-point summary quantities and save them to an npz file.

Reads the sequence waveforms (.trc) of all channels at each scan point and reduces them with features.reduce_point to
  per-channel quantities (QUANT), event-level quantities (EV_QUANT), neighbor-pair quantities (PAIR_QUANT),
  eta histograms, the per-event jitter distribution of the leading strip, and mean waveforms.
The result is cached in results\\<scan name>\\reduced.npz. Per-segment values are not saved.
The analysis stages (analyze_*.py) read only this npz, so remaking the figures takes only a few seconds.

Steps (same as the unified reduction of the "matome" analysis note):
  1. Pre-pass: read about 24 points spread over the scan, and decide the global noise reference of each channel
     (median of the baseline RMS) and the pulse polarity. The thresholds of the quality cut and the hit decision come from this reference.
  2. Main pass: reduce all points in parallel.

Standalone: python reduce_scan.py <scan_dir> [--force]
"""
from __future__ import annotations

import os

# If BLAS starts more threads inside the parallel workers it gets slower, so fix it to 1 thread (must be before importing numpy)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import (ETA_NBIN, EV_QUANT, JIT_MAX_PS, JIT_NBIN, PAIR_QUANT, QUANT,  # noqa: E402
                      FeatureConfig, detect_polarity, reduce_point, segment_features)
from scan_data import ScanInfo, load_scan, resolve_scan_dir  # noqa: E402
from trc_reader import read_trc  # noqa: E402

REDUCED_VERSION = 2          # format of reduced.npz (1: old quick-look analysis, 2: matome-compatible)


def adjacent_pairs(channels) -> list[tuple[int, int]]:
    """Pairs of channels with neighboring numbers (indices into the channel array). Example C1..C4 → (0,1),(1,2),(2,3)."""
    num = [int(c[1:]) for c in channels]
    return [(i, j) for i in range(len(num)) for j in range(len(num)) if num[j] == num[i] + 1]


def _prepass(scan: ScanInfo, cfg: FeatureConfig, n_sample: int = 24):
    """Decide the global noise reference [mV] (per channel) and the polarity from a thinned set of points."""
    step = max(1, scan.n_points // n_sample)
    noise = {ch: [] for ch in scan.channels}
    best = (0.0, -1.0)
    for idx in scan.index[::step]:
        for ch in scan.channels:
            p = scan.file_for(ch, int(idx))
            if not p.exists():
                continue
            try:
                w = read_trc(p)
            except Exception:
                continue
            v = w.volts()
            nb = max(int(round(cfg.baseline_frac * v.shape[1])), 4)
            noise[ch].append(v[:, :nb].std(axis=1, ddof=1) * 1e3)
            pol, size = detect_polarity(w, cfg)
            if size > best[0]:
                best = (size, pol)
    ref = np.array([float(np.median(np.concatenate(noise[ch]))) if noise[ch] else np.nan
                    for ch in scan.channels])
    return ref, best[1]


def _reduce_point(args):
    """Process all channels of one scan point (ProcessPool worker)."""
    scan_dir, prefix, channels, idx, cfg_dict, ref, pol, pairs = args
    cfg = FeatureConfig(**cfg_dict)
    feats, waves = [], []
    for ch in channels:
        path = Path(scan_dir) / f"{ch}--{prefix}--{idx:05d}.trc"
        ft, wave = None, None
        try:
            w = read_trc(path)
            ft = segment_features(w, cfg, pol)
            wave = w.volts().mean(axis=0).astype(np.float32)
        except FileNotFoundError:
            pass
        except Exception as exc:  # skip broken files and continue
            print(f"[warn] {path.name}: {exc}", flush=True)
        feats.append(ft)
        waves.append(wave)
    return idx, reduce_point(feats, ref, cfg, pol, pairs), waves


def reduce_scan(scan: ScanInfo, out_path: Path, cfg: FeatureConfig, workers: int = 8) -> Path:
    n, chs = scan.n_points, list(scan.channels)
    row_of = {int(v): i for i, v in enumerate(scan.index)}
    pairs = adjacent_pairs(chs)

    # Take the waveform length from the first file that exists
    nsamp, t_ns, dt_ns = 0, np.zeros(0), np.nan
    for idx in scan.index:
        p = scan.file_for(chs[0], int(idx))
        if p.exists():
            w0 = read_trc(p)
            nsamp = w0.n_samples
            if w0.n_segments < cfg.n_min:
                print(f"[warn] Each file has only {w0.n_segments} segment(s) (waveform averaged on the scope?). "
                      "MPV, jitter, time resolution and position resolution cannot be computed; the median is used for the amplitude etc.", flush=True)
            dt_ns = w0.horiz_interval * 1e9
            t_ns = (w0.horiz_offset + w0.horiz_interval * np.arange(nsamp)) * 1e9
            break
    if nsamp == 0:
        raise FileNotFoundError(f"No readable .trc found: {scan.scan_dir}")

    t0 = time.time()
    ref, pol = _prepass(scan, cfg)
    print(f"  global noise reference [mV]: " + ", ".join(f"{c} {r:.2f}" for c, r in zip(chs, ref))
          + f"  polarity {'negative' if pol < 0 else 'positive'}", flush=True)

    quant = {ch: np.full((n, len(QUANT)), np.nan) for ch in chs}
    waves = {ch: np.full((n, nsamp), np.nan, np.float32) for ch in chs}
    ev = np.full((n, len(EV_QUANT)), np.nan)
    pr = np.full((n, len(pairs), len(PAIR_QUANT)), np.nan)
    eta_hist = np.zeros((n, len(pairs), 2, ETA_NBIN), np.uint16)
    jit_hist = np.zeros((n, 2, JIT_NBIN), np.uint16)

    jobs = [(str(scan.scan_dir), scan.prefix, tuple(chs), int(idx), cfg.as_dict(), ref, pol, pairs)
            for idx in scan.index]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, (idx, res, wv) in enumerate(pool.map(_reduce_point, jobs, chunksize=4)):
            i = row_of[idx]
            for c, ch in enumerate(chs):
                quant[ch][i] = res["ch"][c]
                if wv[c] is not None and len(wv[c]) == nsamp:
                    waves[ch][i] = wv[c]
            ev[i], pr[i] = res["ev"], res["pair"]
            eta_hist[i], jit_hist[i] = res["eta_hist"], res["jit_lead_hist"]
            if (k + 1) % 100 == 0 or k + 1 == n:
                el = time.time() - t0
                print(f"  reduce {k + 1}/{n}  {el:.0f}s (ETA {el / (k + 1) * (n - k - 1):.0f}s)",
                      flush=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path, version=REDUCED_VERSION,
        index=scan.index, x_um=scan.x_um, y_um=scan.y_um, z_um=scan.z_um,
        tune=scan.tune, ok=scan.ok, xn=scan.xn, yn=scan.yn, zn=scan.zn,
        t_ns=t_ns, dt_ns=dt_ns, noise_ref_mV=ref, polarity=pol,
        quant_names=np.array(QUANT), ev_names=np.array(EV_QUANT), pair_quant_names=np.array(PAIR_QUANT),
        channels=np.array(chs),
        pair_names=np.array([chs[a] + chs[b] for a, b in pairs]),
        pair_index=np.array(pairs, int).reshape(-1, 2),
        ev=ev, pair=pr, eta_hist=eta_hist, jit_lead_hist=jit_hist,
        jit_edges_ps=np.linspace(0.0, JIT_MAX_PS, JIT_NBIN + 1),
        cfg_json=np.str_(json.dumps(cfg.as_dict())),
        meta_json=np.str_(scan.meta_json()),
        **{f"q_{ch}": quant[ch] for ch in chs},
        **{f"w_{ch}": waves[ch] for ch in chs})
    print(f"reduced → {out_path} ({out_path.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s)")
    return out_path


def load_reduced(scan: ScanInfo, results_root=None, force: bool = False,
                 cfg: FeatureConfig | None = None, workers: int = 8):
    """Load the cache (reduced.npz). Rebuild it if it is missing, has an old format, has different settings, or force is set."""
    cfg = cfg or FeatureConfig()
    out = scan.results_dir(results_root) / "reduced.npz"
    redo = force or not out.exists()
    if not redo:
        with np.load(out, allow_pickle=False) as z:
            if "version" not in z.files or int(z["version"]) != REDUCED_VERSION:
                print("The reduced cache has an old format; rebuilding it")
                redo = True
            elif json.loads(str(z["cfg_json"])) != cfg.as_dict():
                print("The analysis settings differ from the cache; rebuilding it")
                redo = True
    if redo:
        reduce_scan(scan, out, cfg, workers=workers)
    else:
        print(f"using reduced cache: {out} (use --force to recompute)")
    return np.load(out, allow_pickle=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scan", help="Path or name of the scan folder")
    ap.add_argument("--log", default=None, help="Scan-log CSV (searched automatically if omitted)")
    ap.add_argument("--results", default=None, help="Results root (default <ws>\\results)")
    ap.add_argument("--force", action="store_true", help="Rebuild the cache")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    scan = load_scan(resolve_scan_dir(args.scan), args.log)
    print(f"scan={scan.name} mode={scan.mode} points={scan.n_points} ch={scan.channels}")
    load_reduced(scan, args.results, force=args.force, workers=args.workers)


if __name__ == "__main__":
    main()
