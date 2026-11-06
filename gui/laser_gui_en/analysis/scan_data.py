# -*- coding: utf-8 -*-
"""Pairing of a scan folder with the GUI scan-log CSV + common metadata.

Scan folder name: scan_<YYYYMMDD_HHMMSS>_<tag>
  tag: 1dX/1dY/1dZ, 2dYX (fast axis, then slow axis), tune, 1dYtune, ...
Log CSV: laser_gui_v2_en\\logs\\scan_<YYYYMMDD_HHMMSS>.csv
  (the # comment lines record mode / segments / tilt-correction
   coefficients / base_pos)

Default output locations:
  ROOT file        → next to the scan folder (<scan_dir>.root)
  analysis results → <parent of data>\\results\\<scan name>\\
Both are placed outside laser_gui_v2_en (so that the data are shared
with the Japanese GUI).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

AXIS_NAMES = ("X", "Y", "Z")
ALL_CHANNELS = ("C1", "C2", "C3", "C4")


def _workspace_root(scan_dir: Path) -> Path:
    """Find the workspace (parent of the 'data' dir) from a scan path."""
    for parent in scan_dir.parents:
        if parent.name.lower() == "data":
            return parent.parent
    # Fallback: two levels above this file (parent of the GUI folder)
    return Path(__file__).resolve().parents[2]


def _log_search_dirs(scan_dir: Path) -> list[Path]:
    ws = _workspace_root(scan_dir)
    here = Path(__file__).resolve().parents[1]  # laser_gui_v2
    return [scan_dir.parent / "logs",          # data\\<year>\\<date>\\logs (when the logs were collected together with the data)
            scan_dir.parents[1] / "logs",
            here / "logs",
            ws / "laser_gui_v4" / "logs",
            ws / "laser_gui_v4_en" / "logs",
            ws / "laser_gui_v2" / "logs",
            ws / "laser_gui_en_v2" / "logs",
            ws / "laser_gui" / "logs",
            ws / "logs"]


@dataclass
class ScanInfo:
    scan_dir: Path
    name: str                      # scan_20260826_152932_2dYX
    stamp: str                     # 20260826_152932
    tag: str                       # 2dYX / 1dX / tune / 1dYtune
    mode: str                      # 1d / 2d / tune / 1d_tune
    prefix: str                    # waveform file prefix (usually "scan")
    channels: tuple[str, ...]      # existing CHs only (e.g. ("C2","C3"))
    log_csv: Path | None
    header: dict = field(default_factory=dict)   # parsed from log # lines
    # Scan-point arrays (in log-CSV row order)
    index: np.ndarray = None       # int
    x_um: np.ndarray = None
    y_um: np.ndarray = None
    z_um: np.ndarray = None
    tune: np.ndarray = None        # NaN = not a TUNE scan
    ok: np.ndarray = None          # status == "ok"
    # Nominal coordinates with the corrections removed (for gridding
    # and axis display)
    xn: np.ndarray = None
    yn: np.ndarray = None
    zn: np.ndarray = None

    # ------------------------------------------------------------------
    @property
    def n_points(self) -> int:
        return len(self.index)

    def file_for(self, ch: str, idx: int) -> Path:
        p = self.scan_dir / f"{ch}--{self.prefix}--{idx:05d}.trc"
        if p.exists():
            return p
        # Fallback to the old scope auto-numbering format or an extra
        # sequence-number suffix (e.g. C2--scan--00042--00000.trc)
        hits = sorted(self.scan_dir.glob(
            f"{ch}--{self.prefix}--{idx:05d}*.trc"))
        return hits[0] if hits else p

    def axis_letter(self, which: int = 0) -> str:
        """1d/1d_tune: scan axis. 2d: which=0 fast axis, 1 slow axis."""
        m = re.match(r"(1d|2d)([XYZ])([XYZ])?", self.tag)
        if m:
            letter = m.group(2) if which == 0 else (m.group(3) or m.group(2))
            return letter
        # If the tag cannot be parsed: the axis with the largest spread
        spans = [np.ptp(v) for v in (self.xn, self.yn, self.zn)]
        order = np.argsort(spans)[::-1]
        return AXIS_NAMES[order[min(which, 2)]]

    def axis_values(self, letter: str) -> np.ndarray:
        return {"X": self.xn, "Y": self.yn, "Z": self.zn}[letter]

    def results_dir(self, results_root: Path | None = None) -> Path:
        root = Path(results_root) if results_root else \
            _workspace_root(self.scan_dir) / "results"
        return root / self.name

    def default_root_path(self) -> Path:
        return self.scan_dir.with_suffix(".root")

    def meta_json(self) -> str:
        return json.dumps({
            "scan_dir": str(self.scan_dir), "name": self.name,
            "stamp": self.stamp, "tag": self.tag, "mode": self.mode,
            "prefix": self.prefix, "channels": list(self.channels),
            "log_csv": str(self.log_csv), "header": self.header,
        }, ensure_ascii=False)


# ----------------------------------------------------------------------
def _parse_header_comments(lines: list[str]) -> dict:
    """Extract numeric/string parameters from the log-CSV # lines."""
    text = "\n".join(lines)
    out: dict = {"comments": lines}
    for key in ("mode", "prefix", "save_dir"):
        m = re.search(rf"{key}=(\S+)", text)
        if m:
            out[key] = m.group(1)
    for key in ("total_points", "segments"):
        m = re.search(rf"{key}=(\d+)", text)
        if m:
            out[key] = int(m.group(1))
    for key in ("dzdx", "dzdy", "dydx", "settle_s"):
        m = re.search(rf"{key}=([-+0-9.eE]+)", text)
        if m:
            out[key] = float(m.group(1))
    for key in ("tilt_correct", "rot_correct"):
        m = re.search(rf"{key}=(True|False)", text)
        if m:
            out[key] = m.group(1) == "True"
    m = re.search(r"base_pos_um=\(([^)]*)\)", text)
    if m and m.group(1).strip():
        out["base_pos_um"] = [float(v) for v in m.group(1).split(",")]
    return out


def _find_log_csv(scan_dir: Path, stamp: str) -> Path | None:
    for d in _log_search_dirs(scan_dir):
        p = d / f"scan_{stamp}.csv"
        if p.exists():
            return p
    return None


def _mode_from_tag(tag: str) -> str:
    if tag == "tune":
        return "tune"
    if tag.startswith("1d") and tag.endswith("tune"):
        return "1d_tune"
    if tag.startswith("2d"):
        return "2d"
    if tag.startswith("1d"):
        return "1d"
    return "1d"


def load_scan(scan_dir, log_csv=None) -> ScanInfo:
    """Build a ScanInfo from a scan folder (+ optional log CSV)."""
    scan_dir = Path(scan_dir).resolve()
    if not scan_dir.is_dir():
        raise FileNotFoundError(f"Scan folder not found: {scan_dir}")
    name = scan_dir.name
    m = re.match(r"scan_(\d{8}_\d{6})_(.+)$", name)
    stamp = m.group(1) if m else name
    tag = m.group(2) if m else ""

    # Auto-detect the CHs and the prefix from the waveform files
    trc = sorted(scan_dir.glob("C?--*--*.trc"))
    if not trc:
        raise FileNotFoundError(f"No .trc files found: {scan_dir}")
    prefix = trc[0].name.split("--")[1]
    channels = tuple(ch for ch in ALL_CHANNELS
                     if any(f.name.startswith(ch + "--") for f in trc))

    log_path = Path(log_csv).resolve() if log_csv else \
        _find_log_csv(scan_dir, stamp)

    comments: list[str] = []
    rows: list[dict] = []
    if log_path is not None and log_path.exists():
        with open(log_path, newline="", encoding="utf-8") as f:
            for rec in csv.reader(f):
                if not rec:
                    continue
                if rec[0].lstrip().startswith("#"):
                    comments.append(",".join(rec))
                    continue
                if rec[0] == "index":
                    continue
                rows.append({
                    "index": int(rec[0]), "x": float(rec[1]),
                    "y": float(rec[2]), "z": float(rec[3]),
                    "tune": float(rec[4]) if rec[4].strip() else np.nan,
                    "ok": rec[5].strip() == "ok",
                })
    else:
        # No log found: carry on with minimal info from the file numbers
        idxs = sorted({int(f.stem.split("--")[-1]) for f in trc})
        rows = [{"index": i, "x": np.nan, "y": np.nan, "z": np.nan,
                 "tune": np.nan, "ok": True} for i in idxs]

    header = _parse_header_comments(comments)
    mode = header.get("mode", _mode_from_tag(tag))
    prefix = header.get("prefix", prefix)

    info = ScanInfo(scan_dir=scan_dir, name=name, stamp=stamp, tag=tag,
                    mode=mode, prefix=prefix, channels=channels,
                    log_csv=log_path, header=header)
    info.index = np.array([r["index"] for r in rows], int)
    info.x_um = np.array([r["x"] for r in rows])
    info.y_um = np.array([r["y"] for r in rows])
    info.z_um = np.array([r["z"] for r in rows])
    info.tune = np.array([r["tune"] for r in rows])
    info.ok = np.array([r["ok"] for r in rows], bool)

    # Nominal coordinates: undo the scan-time rotation-follow (Y) and
    # focus-follow (Z) corrections
    x0 = info.x_um[0] if len(info.x_um) and np.isfinite(info.x_um[0]) else 0.0
    base = header.get("base_pos_um", [x0, 0.0, 0.0])
    dydx = header.get("dydx", 0.0) if header.get("rot_correct") else 0.0
    dzdx = header.get("dzdx", 0.0) if header.get("tilt_correct") else 0.0
    dzdy = header.get("dzdy", 0.0) if header.get("tilt_correct") else 0.0
    info.xn = info.x_um.copy()
    info.yn = info.y_um - dydx * (info.x_um - x0)
    info.zn = info.z_um - dzdx * (info.x_um - base[0]) \
        - dzdy * (info.y_um - base[1])
    return info


def resolve_scan_dir(arg: str) -> Path:
    """Resolve arg to a scan folder (a path, or a name searched in data\\)."""
    p = Path(arg)
    if p.is_dir():
        return p.resolve()
    ws = Path(__file__).resolve().parents[2]
    hits = sorted((ws / "data").glob(f"*/*/{arg}"))
    if len(hits) == 1:
        return hits[0].resolve()
    if len(hits) > 1:
        raise FileNotFoundError(
            f"Multiple scans share this name: {[str(h) for h in hits]}")
    raise FileNotFoundError(f"Scan folder not found: {arg}")
