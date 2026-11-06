# -*- coding: utf-8 -*-
"""Entry point of the quick-look analysis (detects the scan kind and runs the matching analysis).

It uses the same definitions as the "matome" analysis note (laser_test/analysis/results/mip/combined/matome)
and runs the analysis items that are possible for each scan kind:

  kind      script                analysis items
  1dX       analyze_1dx.py        along the strip: amplitude/charge, rise time, jitter, time resolution, Elmore delay
  1dY       analyze_1dy.py        across the strips: amplitude/charge, rise time, jitter, position resolution (eta), time resolution, v_eff
  1dZ       analyze_1dz.py        best Z of a focus scan
  2dYX      analyze_2dyx.py       all items 1–7 (maps, in the gap / along the strip, position resolution, propagation)
                                  + the paper figures 01–09 of the template-fit analysis (macro\template_timing\tt_pipeline.py; skip with --no-template)
  tune      analyze_tune.py       amplitude dependence: TUNE for a MIP-like signal, 1/A law of the jitter, sigma_t vs amplitude
  1d_tune   analyze_1d_tune.py    position x intensity: 1/A law, sigma_t vs amplitude, intensity dependence of the position resolution
  pixel     analyze_pixel.py      pixel sensor (2x2 ch) 2D scan: amplitude maps, time / position resolution of 2/3/4-pixel combinations,
                                  propagation (auto-detected from sensor_type=pixel in the log; main analysis = macro\template_timing\tt_pixel.py)

usage:
  python run_analysis.py <scan_dir or scan name> [options]

Examples:
  python run_analysis.py ..\\..\\data\\2026\\20260826\\scan_20260826_155255_1dX
  python run_analysis.py scan_20260824_202544_2dYX --x-off -632
  python run_analysis.py scan_20260707_005415_1dYtune --force

Outputs:
  figures, summary.txt, numbers.json, summary.csv : <workspace>\\results\\<scan name>\\
  mean-waveform ROOT : <workspace>\\results\\<scan name>\\mean_waveforms.root
  ROOT file : <scan folder>.root (inside data\\..., with --root)

Typical time (8 workers): the reduction takes about 0.03 s per point (about 10 s for a 1D scan of 100 points, about 1 min for a 2D scan of 2000 points).
The reduced result is kept in reduced.npz, so from the second run only the figures are remade (a few seconds to 20 s).
"""
from __future__ import annotations

import os

# Stop BLAS from starting threads inside the parallel workers (must be before importing numpy)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from features import FeatureConfig  # noqa: E402
from reduce_scan import load_reduced  # noqa: E402
from scan_data import load_scan, resolve_scan_dir  # noqa: E402

KINDS = ("1dX", "1dY", "1dZ", "2dYX", "tune", "1d_tune", "pixel")
MODULES = {"1dX": "analyze_1dx", "1dY": "analyze_1dy", "1dZ": "analyze_1dz", "2dYX": "analyze_2dyx",
           "tune": "analyze_tune", "1d_tune": "analyze_1d_tune", "pixel": "analyze_pixel"}


def sensor_name(scan) -> str:
    """sensor=... in the header of the scan log (empty if absent)."""
    for ln in scan.header.get("comments", []):
        for tok in str(ln).split():
            if tok.startswith("sensor="):
                return tok.split("=", 1)[1]
    return ""


def sensor_type(scan) -> str:
    """sensor_type=... (strip / pixel) in the header of the scan log. If absent, guessed from the sensor name (pixel...)."""
    for ln in scan.header.get("comments", []):
        for tok in str(ln).split():
            if tok.startswith("sensor_type="):
                return tok.split("=", 1)[1].lower()
    return "pixel" if sensor_name(scan).lower().startswith("pixel") else "strip"


def scan_kind(scan) -> str:
    """Decide the analysis kind from the mode in the scan log and the tag in the folder name."""
    mode = scan.mode
    if mode in ("tune", "1d_tune"):
        return mode
    if mode == "2d" and sensor_type(scan) == "pixel":
        return "pixel"
    if mode == "2d":
        axes = {scan.axis_letter(0), scan.axis_letter(1)}
        if axes != {"X", "Y"}:
            raise SystemExit(f"The axes of this 2D scan are {sorted(axes)}. The quick-look analysis supports only 2D scans in X and Y (2dYX / 2dXY)")
        return "2dYX"
    return {"X": "1dX", "Y": "1dY", "Z": "1dZ"}[scan.axis_letter(0)]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[2:]))
    ap.add_argument("scan", help="Path or name of the scan folder")
    ap.add_argument("--log", default=None, help="Scan-log CSV (if omitted, searched automatically in logs)")
    ap.add_argument("--results", default=None, help="Root folder for the results (default <workspace>\\results)")
    ap.add_argument("--force", action="store_true", help="Rebuild the reduced cache")
    ap.add_argument("--workers", type=int, default=8, help="Number of parallel processes for the reduction (default 8)")
    ap.add_argument("--mode", default=None, choices=KINDS, help="Force the analysis kind (default: automatic from the log/tag)")
    ap.add_argument("--no-template", action="store_true",
                    help="Skip the template-fit analysis (paper figures, macro\\template_timing) for 2dYX / pixel (quick-look figures only)")
    ap.add_argument("--force-template", action="store_true", help="Redo every stage of the template-fit analysis")
    # Coordinates and geometry
    ap.add_argument("--x-off", type=float, default=0.0,
                    help="X offset in um from log coordinates to sensor coordinates (X=0 is the outer frame edge on the readout side. "
                         "For scans before 2026-09-07 use e.g. -632)")
    ap.add_argument("--y-off", type=float, default=0.0, help="Y offset in um (same meaning)")
    ap.add_argument("--pitch", type=float, default=500.0, help="Strip pitch in um (default 500)")
    ap.add_argument("--body-x", type=float, nargs=2, default=(-9900.0, -700.0), metavar=("LO", "HI"),
                    help="X range of the strip \"body\" in um (excludes the pad region and beyond the far end. Default -9900 -700)")
    # ROOT conversion
    ap.add_argument("--root", action="store_true", help="Also convert .trc → a single ROOT file")
    ap.add_argument("--root-out", type=Path, default=None, help="ROOT output path (default <scan_dir>.root)")
    ap.add_argument("--no-raw", action="store_true", help="Do not store raw waveforms in the ROOT file (features only)")
    ap.add_argument("--limit", type=int, default=None, help="Limit the ROOT conversion to the first N points (for a quick check)")
    ap.add_argument("--no-meanwave-root", action="store_true",
                    help="Do not write the per-point mean-waveform ROOT file (mean_waveforms.root)")
    # Feature settings (defaults are the same as in the analysis note)
    ap.add_argument("--hit-thr", type=float, default=8.0,
                    help="Lower amplitude limit of a hit in mV: max(this, 5 x global noise reference) (default 8)")
    ap.add_argument("--noise-max", type=float, default=4.0,
                    help="Upper noise limit of the quality cut in mV: max(this, 3 x global noise reference) (default 4)")
    ap.add_argument("--tot-thr", type=float, default=10.0, help="ToT threshold in mV (default 10)")
    ap.add_argument("--n-boot", type=int, default=30,
                    help="Number of bootstrap samples for the MPV error (default 30. The analysis note uses 100. The MPV value does not depend on this number)")
    args = ap.parse_args()

    cfg = FeatureConfig(hit_thr_mV=args.hit_thr, noise_abs_mV=args.noise_max, tot_thr_mV=args.tot_thr,
                        n_boot=args.n_boot)
    scan = load_scan(resolve_scan_dir(args.scan), args.log)
    kind = args.mode or scan_kind(scan)
    outdir = scan.results_dir(args.results)
    print(f"scan     : {scan.name}")
    print(f"kind     : {kind}  points={scan.n_points}  ch={','.join(scan.channels)}")
    print(f"log csv  : {scan.log_csv}")
    print(f"results  : {outdir}")
    if scan.log_csv is None:
        print("[warn] Scan-log CSV not found. The coordinates are unknown, so position-dependent analysis is not possible (give it with --log)")

    red = load_reduced(scan, args.results, force=args.force, cfg=cfg, workers=args.workers)

    import importlib
    opts = {"x_off": args.x_off, "y_off": args.y_off, "pitch": args.pitch, "body_x": tuple(args.body_x),
            "workers": args.workers, "no_template": args.no_template, "force_template": args.force_template or args.force}
    importlib.import_module(MODULES[kind]).run(scan, red, outdir, opts)

    # Write the mean waveform of each scan point to ROOT (from reduced.npz; fast)
    if not args.no_meanwave_root:
        try:
            import meanwave_root
            meanwave_root.write(scan, red, outdir)
        except ImportError as exc:
            print(f"[warn] skipped mean_waveforms.root ({exc})")

    if args.root:
        import trc_to_root
        trc_to_root.convert(scan, args.root_out, limit=args.limit, raw=not args.no_raw, cfg=cfg)

    print("Done")


if __name__ == "__main__":
    main()
