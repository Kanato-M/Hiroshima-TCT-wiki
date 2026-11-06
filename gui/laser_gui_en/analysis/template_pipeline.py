# -*- coding: utf-8 -*-
"""Runs the template-fit analysis of macro\\template_timing as a subprocess (from the 2dYX / pixel kinds of run_analysis).

  2dYX  : tt_pipeline.py <scan> --workers N --y-off Y   → results\\<scan>\\paper_plots\\<scan>\\01–09 (paper figures) and paper_*.png
  pixel : tt_pixel.py <scan> --workers N                → results\\<scan>\\pix01–06 and pixel\\

Nothing is done when the workspace has no macro\\template_timing (only the quick-look figures are made).
The scripts in macro\\template_timing are shared with the Japanese GUI (their messages are partly in Japanese).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_data import _workspace_root  # noqa: E402


def macro_dir(scan) -> Path | None:
    d = _workspace_root(scan.scan_dir) / "macro" / "template_timing"
    return d if d.exists() else None


def run_script(scan, script: str, args: list[str]) -> bool:
    """Run macro\\template_timing\\<script> as a subprocess (progress is streamed through)."""
    d = macro_dir(scan)
    if d is None or not (d / script).exists():
        print(f"[warn] macro\\template_timing\\{script} not found: the template-fit analysis is skipped (quick-look figures only)", flush=True)
        return False
    cmd = [sys.executable, "-u", str(d / script), scan.name] + [str(a) for a in args]
    print("  $ " + " ".join(cmd[1:]), flush=True)
    proc = subprocess.run(cmd, cwd=str(d))
    if proc.returncode != 0:
        print(f"[warn] {script} exited with code {proc.returncode}", flush=True)
        return False
    return True


def run_strip_pipeline(scan, workers: int, y_off: float = 0.0, force: bool = False) -> bool:
    args = ["--workers", workers, "--y-off", f"{y_off:g}"]
    if force:
        args.append("--force")
    return run_script(scan, "tt_pipeline.py", args)


def run_pixel(scan, workers: int, force: bool = False) -> bool:
    args = ["--workers", workers]
    if force:
        args.append("--force")
    return run_script(scan, "tt_pixel.py", args)
