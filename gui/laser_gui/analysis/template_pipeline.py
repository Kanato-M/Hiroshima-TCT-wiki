# -*- coding: utf-8 -*-
"""macro\\template_timing のテンプレートフィット解析をサブプロセスで呼ぶ (run_analysis の 2dYX / pixel から)。

  2dYX  : tt_pipeline.py <scan> --workers N --y-off Y   → results\\<scan>\\paper_plots\\<scan>\\01–09 (論文図) と paper_*.png
  pixel : tt_pixel.py <scan> --workers N                → results\\<scan>\\pix01–06 と pixel\\

ワークスペースに macro\\template_timing が無い場合は何もしない (簡易解析の図だけになる)。
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
    """macro\\template_timing\\<script> をサブプロセスで実行する (進捗はそのまま流す)。"""
    d = macro_dir(scan)
    if d is None or not (d / script).exists():
        print(f"[warn] macro\\template_timing\\{script} が無いのでテンプレートフィット解析は省略 (簡易解析の図のみ)", flush=True)
        return False
    cmd = [sys.executable, "-u", str(d / script), scan.name] + [str(a) for a in args]
    print("  $ " + " ".join(cmd[1:]), flush=True)
    proc = subprocess.run(cmd, cwd=str(d))
    if proc.returncode != 0:
        print(f"[warn] {script} が code {proc.returncode} で終了", flush=True)
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
