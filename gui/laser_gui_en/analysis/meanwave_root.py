# -*- coding: utf-8 -*-
"""Write the mean waveform of each scan point (= each 2D-map bin) to ROOT.

Takes the per-point, per-CH mean waveforms stored in reduced.npz (plain
average over all segments), subtracts the baseline
(mean of the first n_baseline samples), and saves them in mV as

  TTree "meanwave" : 1 entry = 1 scan point
      index, ix/iy/iz/itune (grid index; -1 if the axis is absent),
      x_um..zn_um, tune_pct, ok,
      t_ns[nsamp], c1..c4[nsamp] (mean waveform - baseline, mV),
      baseline_mv[nch], n_seg[nch] and each QUANT feature [nch]
  TH1D  mw/<CH>/mw_<CH>_<coord>  : same mean waveform (x: time ns, y: mV)
                                   e.g. mw/C3/mw_C3_X-080_Y+040
                                   (in X,Y,Z,T order)
  meta/*                         : strings (units, axes, CH order, ...)

into results/<scan name>/mean_waveforms.root.
No .trc is read, so it takes only a few seconds. Common to all modes
(1d/2d/tune).

Standalone: python meanwave_root.py <scan_dir> [--out PATH] [--no-hist]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import QUANT  # noqa: E402
from plot_common import channels_of, snap_grid  # noqa: E402
from scan_data import ScanInfo, load_scan, resolve_scan_dir  # noqa: E402

MAX_HISTS = 20000     # above this, TH1s are skipped (tree only)


def _grid_index(vals, min_step=0.5):
    """Coordinate array → (grid index (-1 if invalid), grid centers)."""
    v = np.asarray(vals, float)
    idx = np.full(len(v), -1, np.int32)
    fin = np.isfinite(v)
    if not fin.any():
        return idx, np.zeros(0)
    if np.ptp(v[fin]) <= min_step:        # effectively single-valued axis
        idx[fin] = 0
        return idx, np.array([float(np.mean(v[fin]))])
    centers, k = snap_grid(v[fin], min_step)
    idx[fin] = k
    return idx, centers


def _z0(val: float) -> float:
    """Round |val| < 0.5 to +0.0 (avoids a "-0" label)."""
    return 0.0 if abs(float(val)) < 0.5 else float(val)


def _coord_tag(letter: str, val: float, width: int) -> str:
    """Coordinate tag for histogram names, e.g. Y+040, X-080, T050."""
    val = _z0(val)
    if letter == "T":
        return f"T{val:0{width}.0f}"
    return f"{letter}{val:+0{width + 1}.0f}"


def _th1(name: str, title: str, t_ns: np.ndarray, y: np.ndarray):
    """Build a TH1D (uniform bins) with uproot's low-level API."""
    from uproot.writing.identify import to_TAxis, to_TH1x
    n = len(t_ns)
    dt = float(t_ns[1] - t_ns[0]) if n > 1 else 1.0
    xmin = float(t_ns[0]) - dt / 2
    xmax = float(t_ns[-1]) + dt / 2
    yd = np.asarray(y, np.float64)
    data = np.concatenate([[0.0], yd, [0.0]])          # with under/overflow
    x = np.asarray(t_ns, np.float64)
    return to_TH1x(
        fName=name, fTitle=title, data=data,
        fEntries=float(n), fTsumw=float(yd.sum()),
        fTsumw2=float((yd ** 2).sum()), fTsumwx=float((yd * x).sum()),
        fTsumwx2=float((yd * x * x).sum()),
        fSumw2=np.zeros(0, np.float64),
        fXaxis=to_TAxis(fName="xaxis", fTitle="time (ns)", fNbins=n,
                        fXmin=xmin, fXmax=xmax))


def write(scan: ScanInfo, red, outdir: Path,
          name: str = "mean_waveforms.root", hists: bool = True) -> Path:
    """Write the reduced.npz mean waveforms to ROOT (TTree + TH1D)."""
    import uproot

    chs = channels_of(red)
    n_ch = len(chs)
    t_ns = np.asarray(red["t_ns"], np.float64)
    nsamp = len(t_ns)
    n = len(red["index"])
    try:
        frac = float(json.loads(str(red["cfg_json"])).get("baseline_frac", 0.2))
    except Exception:
        frac = 0.2
    nb = max(int(round(frac * nsamp)), 4)        # same baseline window as the feature extraction

    # Mean waveform - baseline [mV], and the baseline [mV]
    waves_mv, base_mv = {}, np.full((n, n_ch), np.nan, np.float32)
    for ci, ch in enumerate(chs):
        w = np.asarray(red[f"w_{ch}"], np.float64)          # (n, nsamp) V
        bl = np.nanmean(w[:, :nb], axis=1) if nsamp else np.full(n, np.nan)
        waves_mv[ch] = ((w - bl[:, None]) * 1e3).astype(np.float32)
        base_mv[:, ci] = (bl * 1e3).astype(np.float32)

    # Grid indices (computed for every axis; an axis with a single
    # value is 0 at all points)
    gx, cx = _grid_index(red["xn"])
    gy, cy = _grid_index(red["yn"])
    gz, cz = _grid_index(red["zn"])
    gt, ct = _grid_index(red["tune"], min_step=0.2)
    axes = [("X", gx, cx), ("Y", gy, cy), ("Z", gz, cz), ("T", gt, ct)]
    varying = [(L, g, c) for (L, g, c) in axes if len(c) > 1]

    qi = {str(q): i for i, q in enumerate(red["quant_names"])}
    qmat = {ch: np.asarray(red[f"q_{ch}"], np.float64) for ch in chs}

    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / name
    t0 = time.time()
    fout = uproot.recreate(path)

    # --- TTree ---
    types = {
        "index": np.int32, "ix": np.int32, "iy": np.int32, "iz": np.int32,
        "itune": np.int32,
        "x_um": np.float32, "y_um": np.float32, "z_um": np.float32,
        "xn_um": np.float32, "yn_um": np.float32, "zn_um": np.float32,
        "tune_pct": np.float32, "ok": np.int8,
        "t_ns": np.dtype((np.float32, (nsamp,))),
    }
    for ch in chs:
        types[ch.lower()] = np.dtype((np.float32, (nsamp,)))
    types["baseline_mv"] = np.dtype((np.float32, (n_ch,)))
    for q in QUANT:
        types[q] = np.dtype((np.float32, (n_ch,)))
    # Event-level quantities (leading / sub-leading, total charge, sigma_t of the leading strip, ...): one value per point
    ev_names = [str(q) for q in red["ev_names"]]
    for q in ev_names:
        types[f"ev_{q}"] = np.float32

    data = {
        "index": np.asarray(red["index"], np.int32),
        "ix": gx, "iy": gy, "iz": gz, "itune": gt,
        "x_um": np.asarray(red["x_um"], np.float32),
        "y_um": np.asarray(red["y_um"], np.float32),
        "z_um": np.asarray(red["z_um"], np.float32),
        "xn_um": np.asarray(red["xn"], np.float32),
        "yn_um": np.asarray(red["yn"], np.float32),
        "zn_um": np.asarray(red["zn"], np.float32),
        "tune_pct": np.asarray(red["tune"], np.float32),
        "ok": np.asarray(red["ok"], np.int8),
        "t_ns": np.tile(t_ns.astype(np.float32), (n, 1)),
    }
    for ch in chs:
        data[ch.lower()] = waves_mv[ch]
    data["baseline_mv"] = base_mv
    for q in QUANT:
        data[q] = np.stack([qmat[ch][:, qi[q]] for ch in chs],
                           axis=1).astype(np.float32)
    ev = np.asarray(red["ev"], np.float32)
    for k, q in enumerate(ev_names):
        data[f"ev_{q}"] = ev[:, k]
    tree = fout.mktree("meanwave", types,
                       title="mean waveform per scan point (bin), "
                             "baseline subtracted [mV]")
    tree.extend(data)

    # --- TH1D (per bin, per CH) ---
    n_hist = 0
    if hists and n * n_ch <= MAX_HISTS and nsamp > 1:
        widths = {L: max(3, len(f"{np.max(np.abs(c)):.0f}"))
                  for (L, g, c) in varying}
        names = []
        for i in range(n):
            parts = [_coord_tag(L, c[g[i]], widths[L])
                     for (L, g, c) in varying if g[i] >= 0]
            names.append("_".join(parts) if parts else f"idx{int(red['index'][i]):05d}")
        if len(set(names)) != n:      # same-coordinate duplicates: tag by index
            names = [f"{s}_idx{int(red['index'][i]):05d}"
                     for i, s in enumerate(names)]
        for ci, ch in enumerate(chs):
            for i in range(n):
                coord = "  ".join(
                    f"{L}={_z0(c[g[i]]):.0f}" + ("%" if L == "T" else "")
                    for (L, g, c) in varying if g[i] >= 0)
                y = waves_mv[ch][i]
                if not np.isfinite(y).all():
                    continue
                hname = f"mw_{ch}_{names[i]}"
                title = (f"{ch} mean waveform  {coord} um  "
                         f"(idx {int(red['index'][i])});"
                         "time (ns);mean waveform - baseline (mV)")
                fout[f"mw/{ch}/{hname}"] = _th1(hname, title, t_ns, y)
                n_hist += 1
    elif hists:
        print(f"[note] TH1s omitted: there would be {n * n_ch} (tree only)")

    # --- meta ---
    fout["meta/scan_name"] = scan.name
    fout["meta/mode"] = str(scan.mode)
    fout["meta/channels"] = ",".join(chs)
    fout["meta/units"] = ("c1..c4 = all-segment mean waveform - baseline "
                          "[mV]; "
                          f"baseline = mean of first {nb} samples "
                          "(baseline_mv); "
                          "t_ns = time [ns] (w.r.t. header horiz_offset)")
    fout["meta/grid"] = "; ".join(
        f"{L}: {len(c)} bins " + ", ".join(f"{_z0(v):.0f}" for v in c)
        for (L, g, c) in varying) or "single point"
    fout["meta/grid_note"] = ("ix/iy/iz/itune are the grid indices of the "
                              "nominal coords xn/yn/zn/tune "
                              "(-1 if the axis is absent)")
    fout["meta/hist_note"] = ("mw/<CH>/mw_<CH>_<coord tag> is the mean-"
                              "waveform TH1D of each bin. The coord tag "
                              "is the nominal coordinate [um] "
                              "(T is TUNE %)")
    fout["meta/feature_quantities"] = ",".join(QUANT)
    fout["meta/event_quantities"] = ",".join(f"ev_{q}" for q in ev_names)
    fout.close()

    print(f"  wrote {path} ({n} points x {n_ch} ch, {n_hist} TH1, "
          f"{path.stat().st_size / 1e3:.0f} kB, {time.time() - t0:.1f}s)")
    return path


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scan", help="scan folder path or name")
    ap.add_argument("--log", default=None)
    ap.add_argument("--results", default=None)
    ap.add_argument("--out", type=Path, default=None,
                    help="output .root (default "
                         "results/<scan name>/mean_waveforms.root)")
    ap.add_argument("--no-hist", action="store_true",
                    help="skip the TH1Ds and write the tree only")
    args = ap.parse_args()
    from reduce_scan import load_reduced
    scan = load_scan(resolve_scan_dir(args.scan), args.log)
    red = load_reduced(scan, args.results)
    outdir = scan.results_dir(args.results)
    if args.out:
        write(scan, red, args.out.parent, name=args.out.name,
              hists=not args.no_hist)
    else:
        write(scan, red, outdir, hists=not args.no_hist)


if __name__ == "__main__":
    main()
