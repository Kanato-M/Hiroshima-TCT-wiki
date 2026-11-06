# -*- coding: utf-8 -*-
"""Registry of sensor / DUT profiles (added 2026-10).

So that the code (scan_geometry.py) does not have to be edited every time
the board or sensor is swapped, the following information is bundled into
one profile per sensor and stored in <log_dir>/sensors.json:

  - geometry: strip pitch, electrode width, offset from the frame edge to
    the start of the exposed strip, exposed strip length, end gap
  - readout: scope CH → pad name and electrode-center Y (the electrode
    center of the reference CH is Y=0)
  - default scan range / Auto Alignment measurement points (can also be
    derived automatically from the geometry)
  - tilt coefficients (result of Full Calibration or manual entry;
    independent per sensor)
  - machine coordinates of the logical origin (saved automatically on
    every Find Origin)
  - saved positions (name → coordinates; the frame is "logical" =
    relative to the logical origin, or "machine" = relative to the
    mechanical origin)

Coordinate convention (same as scan_geometry.py):
  logical origin = sensor frame (GND) edge X=0, electrode center of the
  reference CH Y=0, focus Z=0. Physical X+ is stage x−, so the exposed
  strip spans x = −x_frame_offset … −(x_frame_offset + strip_length).

Pixel sensors (added 2026-10-16, sensor_type = "pixel"):
  - geometry: pixel pitch pitch_um and the side length pad_um of the (square,
    signal-free) bonding pad
  - readout: scope CH → pad name and pixel (column col, row row); the reference
    CH is (0, 0)
  - logical origin = the +X/+Y corner of the reference pixel's pad (same as the
    scan of 2026-10-15: the pad spans x, y ∈ [−pad, 0]; pixel center = pad
    center = (−pad/2 − col·pitch, −pad/2 − row·pitch)). Z=0 is the focus. The
    4-pixel corner is at (−pad/2 − pitch/2, −pad/2 − pitch/2)
  - Find Origin: Y dip of the pad (signal-free square) → Z on the silicon beside
    the pad → X dip → R:W at the pad corner. Full Calibration repeats the
    measurement on the pads of the other readout pixels and fits a plane
    (dZ/dX, dZ/dY) plus the row direction dY/dX
  - default scan: from the pad corner (0, 0) to the far pad edge of the readout
    pixels (600 × 600 µm in 10 µm steps for 2×2, pitch 500, pad 100)

The defaults of the standard sensor (500 µm pitch, 10 mm strips) are
generated from scan_geometry.py (measured 2026-08-24). The old-format
files logs/sensor_calibration.json and logs/positions.json are migrated
into the standard sensor's profile on first start.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
from pathlib import Path

import scan_geometry

FILENAME = "sensors.json"
VERSION = 1
STD_ID = "strip500_std"
CHANNELS = ("C1", "C2", "C3", "C4")

SENSOR_TYPES = (("strip", "Strip"), ("pixel", "Pixel"))
PIXEL_PLACES = (("Beside pad (signal)", "beside"), ("Pad center (no signal)", "center"),
                ("4-pixel corner", "corner"))
STRIP_PLACES = (("Readout-side end", "near"), ("Middle", "mid"), ("Far-side end", "far"))

_LEGACY_CALIB = "sensor_calibration.json"
_LEGACY_BOOKMARKS = "positions.json"
_LEGACY_BOOKMARK_MARKER = "_units_calibrated_2um_per_pulse"
# "manual" written in Japanese: calibration files saved by the Japanese
# edition of the GUI mark manually entered coefficients with this word
_JA_MANUAL = "\u624b\u52d5"


# ----------------------------------------------------------------------
# Automatic derivation from the geometry (also used by "Derive from
# geometry" in the sensor edit dialog)
# ----------------------------------------------------------------------
def sensor_type(profile: dict) -> str:
    """"strip" (default) / "pixel"."""
    return str(profile.get("sensor_type", "strip") or "strip")


def is_pixel(profile: dict) -> bool:
    return sensor_type(profile) == "pixel"


def pixel_center(profile: dict, ch: str) -> tuple[float, float]:
    """Pixel sensor: logical coordinates (x, y) [µm] of the pixel center (= pad
    center) of CH. The logical origin is the +X/+Y corner of the reference pad."""
    pad = float(profile.get("pad_um", 100.0))
    pitch = float(profile["pitch_um"])
    ro = profile["readout"].get(ch, {})
    col, row = int(ro.get("col", 0)), int(ro.get("row", 0))
    return (-pad / 2.0 - col * pitch, -pad / 2.0 - row * pitch)


def channel_xy(profile: dict, ch: str) -> tuple[float, float]:
    """Representative electrode coordinates of CH (pixel: pixel center;
    strip: (0, electrode center Y))."""
    if is_pixel(profile):
        return pixel_center(profile, ch)
    ro = profile["readout"]
    y_ref = float(ro.get(profile.get("ref_channel", "C1"), {}).get("y_um", 0.0))
    return (0.0, float(ro[ch]["y_um"]) - y_ref)


def used_channels(profile: dict) -> list[str]:
    """Readout CHs in use, sorted by distance from the reference CH
    (reference CH first)."""
    ro = profile.get("readout", {})
    ref = profile.get("ref_channel", "C1")
    chans = [ch for ch in CHANNELS if ro.get(ch, {}).get("use", True)
             and ch in ro]
    if is_pixel(profile):
        x0, y0 = pixel_center(profile, ref) if ref in ro else (0.0, 0.0)
        return sorted(chans, key=lambda ch: (
            ch != ref, abs(pixel_center(profile, ch)[0] - x0)
            + abs(pixel_center(profile, ch)[1] - y0), ch))
    y_ref = float(ro.get(ref, {}).get("y_um", 0.0))
    return sorted(chans, key=lambda ch: (ch != ref,
                                         abs(ro[ch]["y_um"] - y_ref)))


def derive_scan(profile: dict) -> dict:
    """Derive the default scan range from the geometry.

    Fast axis Y: crosses the readout strips with 1.5 pitches of margin on
                 each side (the neighboring non-readout strip + half a
                 pitch)
    Slow axis X: from the readout-side end of the exposed strip to the
                 middle of the end gap on the far side
    """
    ro = profile["readout"]
    if is_pixel(profile):
        # from the pad corner (0, 0) to the far pad edge of the readout pixels (10 µm steps)
        pad = float(profile.get("pad_um", 100.0))
        cs = [pixel_center(profile, ch) for ch in used_channels(profile)] or [(-pad / 2, -pad / 2)]
        return {
            "fast_start": 0.0,
            "fast_stop": float(min(c[1] for c in cs) - pad / 2.0),
            "fast_step": 10.0,
            "slow_start": 0.0,
            "slow_stop": float(min(c[0] for c in cs) - pad / 2.0),
            "slow_step": 10.0,
        }
    ys = [ro[ch]["y_um"] for ch in used_channels(profile)] or [0.0]
    pitch = float(profile["pitch_um"])
    x_off = float(profile["x_frame_offset_um"])
    x_end = x_off + float(profile["strip_length_um"]) \
        + float(profile.get("x_end_gap_um", 0.0)) / 2.0
    return {
        "fast_start": max(ys) + 1.5 * pitch,
        "fast_stop": min(ys) - 1.5 * pitch,
        "fast_step": 10.0,
        "slow_start": -x_off,
        "slow_stop": -float(round(x_end)),
        "slow_step": 500.0,
    }


def derive_align(profile: dict) -> dict:
    """Derive the Auto Alignment (Full Calibration) points from the
    geometry.

    X points: near the middle of the exposed strip and 50 µm before the
              far end (maximizes the lever arm for the rotation dY/dX and
              the tilt dZ/dX)
    Y points: among the readout electrodes other than the reference CH,
              the nearest and the farthest one (the amplitude is read on
              the CH that reads that electrode; used for dZ/dY and the
              pitch check)
    """
    ro = profile["readout"]
    ref = profile.get("ref_channel", "C1")
    if is_pixel(profile):
        # calibration points = pad centers of the readout pixels other than the
        # reference (amplitude read on that CH). Z is measured on the silicon
        # pad edge + 80 µm inside the pixel (clear signal).
        pad = float(profile.get("pad_um", 100.0))
        pads = [[ch, *pixel_center(profile, ch)]
                for ch in used_channels(profile) if ch != ref]
        return {
            "x_points": [], "y_points": [], "pads": pads,
            "y_probe_off_um": pad / 2.0 + 80.0,
            "x_probe_off_um": 0.0,
            "y_half_um": pad + 100.0,
        }
    y_ref = float(ro.get(ref, {}).get("y_um", 0.0))
    x_off = float(profile["x_frame_offset_um"])
    length = float(profile["strip_length_um"])
    pitch = float(profile["pitch_um"])
    x_mid = -round((x_off + 0.5 * length) / 100.0) * 100.0
    x_far = -(x_off + length - 50.0)
    others = [ch for ch in used_channels(profile) if ch != ref]
    y_points = []
    if others:
        picks = [others[0]] if len(others) == 1 else [others[0], others[-1]]
        y_points = [[ro[ch]["y_um"] - y_ref, ch] for ch in picks]
    return {
        "x_points": [x_mid, x_far],
        "y_points": y_points,
        # Z focus is done on the silicon y_probe_off beside the electrode
        # center (electrode half-width + margin for the defocused spot).
        # For narrow pitches keep it within 1/3 of the pitch so it does
        # not reach the next electrode
        "y_probe_off_um": min(80.0, round(pitch / 3.0)),
        # The X edge is detected close to the electrode, within the Y
        # width of the bond pad
        "x_probe_off_um": min(50.0, round(pitch / 4.0)),
        "y_half_um": min(150.0, round(pitch * 0.3)),
    }


def _std_profile() -> dict:
    """Profile of the standard sensor (scan_geometry.py, measured
    2026-08-24)."""
    g = scan_geometry
    p = {
        "id": STD_ID,
        "name": "Standard strip (500 µm pitch, 10 mm)",
        "note": f"Default sensor from scan_geometry.py "
                f"(measured {g.MEASURED_DATE})",
        "sensor_type": "strip",
        "pitch_um": float(g.STRIP_PITCH_UM),
        "pad_um": 100.0,
        "electrode_width_um": 50.0,
        "x_frame_offset_um": float(g.X_FRAME_OFFSET_UM),
        "strip_length_um": float(g.SEG_STRIP_UM),
        "x_end_gap_um": float(g.SEG_GAP_UM),
        "readout": {
            "C1": {"pad": "J8", "y_um": 0.0, "use": True},
            "C2": {"pad": "J7", "y_um": -500.0, "use": True},
            "C3": {"pad": "J6", "y_um": -1000.0, "use": True},
            "C4": {"pad": "J5", "y_um": -1500.0, "use": True},
        },
        "ref_channel": "C1",
        "calibration": None,
        "machine_origin": {
            "pos_um": list(g.MACHINE_ORIGIN_UM),
            "registered_at": g.MEASURED_DATE,
            "method": "scan_geometry default (guide only)",
        },
        "positions": {},
    }
    p["scan"] = derive_scan(p)
    p["align"] = derive_align(p)
    return p


def _template(sid: str, name: str, pitch: float, note: str) -> dict:
    """Template for another DUT (standard-sensor dimensions with only the
    pitch replaced)."""
    p = _std_profile()
    p.update({"id": sid, "name": name, "note": note, "pitch_um": pitch,
              "machine_origin": None})
    for i, ch in enumerate(CHANNELS):
        p["readout"][ch]["y_um"] = -pitch * i
    p["scan"] = derive_scan(p)
    p["align"] = derive_align(p)
    return p


# Readout layout measured in the pixel scan of 2026-10-15 (scan_20261015_193055_2dYX):
# the pads (signal-free 100 µm squares) are at C1 (−50, −50), C2 (−550, −50), C3 (−50, −550), C4 (−550, −550)
# → C1 (col 0, row 0), C2 (1, 0) = neighbor towards −X, C3 (0, 1) = neighbor towards −Y, C4 (1, 1) = diagonal.
# The 4-pixel corner is at (−300, −300)
PIXEL_2X2_LAYOUT = {"C1": (0, 0), "C2": (1, 0), "C3": (0, 1), "C4": (1, 1)}


def pixel_profile(sid: str = "pixel_2x2_500", name: str = "Pixel 2x2 (500 µm pitch, 100 µm pad)",
                  pitch: float = 500.0, pad: float = 100.0, pads: dict | None = None,
                  note: str = "") -> dict:
    """Template profile of a pixel sensor (2x2 readout)."""
    pads = pads or {"C1": "J13", "C2": "J14", "C3": "J15", "C4": "J16"}
    p = {
        "id": sid, "name": name,
        "note": note or ("Layout of the pixel#3 scan of 2026-10-15 (origin = pad corner, C2 = −X neighbor, "
                         "C3 = −Y neighbor). Check the columns/rows and pad names when the board changes"),
        "sensor_type": "pixel",
        "pitch_um": float(pitch), "pad_um": float(pad),
        # strip dimensions are unused, kept at the standard values for compatibility
        "electrode_width_um": 50.0, "x_frame_offset_um": 0.0,
        "strip_length_um": float(pitch), "x_end_gap_um": 0.0,
        "readout": {}, "ref_channel": "C1",
        "calibration": None, "machine_origin": None, "positions": {},
    }
    for ch in CHANNELS:
        col, row = PIXEL_2X2_LAYOUT[ch]
        p["readout"][ch] = {"pad": pads.get(ch, ""), "use": True, "col": col, "row": row,
                            "y_um": -pad / 2.0 - row * pitch}
    p["scan"] = derive_scan(p)
    p["align"] = derive_align(p)
    return p


def builtin_profiles() -> dict[str, dict]:
    unverified = ("★DIMENSIONS NOT VERIFIED★ Only the pitch comes from the "
                  "analysis config; everything else is copied from the "
                  "standard sensor. Edit to the measured values before use")
    profs = [
        _std_profile(),
        _template("4col_1000", "4col (1000 µm pitch)", 1000.0, unverified),
        _template("varpitch_500", "Variable pitch (500 µm part)", 500.0,
                  unverified),
        pixel_profile(),
    ]
    return {p["id"]: p for p in profs}


def validate(profile: dict) -> None:
    """Check the consistency of a profile (ValueError if invalid)."""
    if not str(profile.get("name", "")).strip():
        raise ValueError("The sensor name is empty")
    if sensor_type(profile) not in [t for t, _ in SENSOR_TYPES]:
        raise ValueError(f"Unknown sensor type: {profile.get('sensor_type')!r}")
    if float(profile.get("pitch_um", 0.0)) <= 0:
        raise ValueError("pitch_um must be positive")
    ref = profile.get("ref_channel")
    ro = profile.get("readout", {})
    if ref not in ro or not ro[ref].get("use", True):
        raise ValueError("The reference CH must be one of the CHs in use")
    if is_pixel(profile):
        pad = float(profile.get("pad_um", 0.0))
        if pad <= 0:
            raise ValueError("The pad size pad_um must be positive")
        if pad >= float(profile["pitch_um"]):
            raise ValueError("The pad size is not smaller than the pixel pitch")
        seen = {}
        for ch, entry in ro.items():
            if not entry.get("use", True):
                continue
            key = (int(entry.get("col", 0)), int(entry.get("row", 0)))
            if key in seen:
                raise ValueError(f"{ch} and {seen[key]} are the same pixel (col {key[0]}, row {key[1]})")
            seen[key] = ch
        if (int(ro[ref].get("col", 0)), int(ro[ref].get("row", 0))) != (0, 0):
            raise ValueError("The pixel of the reference CH must be (col 0, row 0) (origin = corner of its pad)")
    else:
        for key in ("electrode_width_um", "strip_length_um"):
            if float(profile.get(key, 0.0)) <= 0:
                raise ValueError(f"{key} must be positive")
        if float(profile.get("x_frame_offset_um", -1.0)) < 0:
            raise ValueError("x_frame_offset_um must be 0 or larger")
        if float(profile["electrode_width_um"]) >= float(profile["pitch_um"]):
            raise ValueError("The electrode width is not smaller than the pitch")
    sc = profile.get("scan", {})
    for key in ("fast_step", "slow_step"):
        if float(sc.get(key, 0.0)) <= 0:
            raise ValueError("Scan steps must be positive")


# ----------------------------------------------------------------------
class SensorRegistry:
    """Reads/writes the sensor profiles and tracks the "current sensor".

    Only used from the GUI thread (worker threads receive the values they
    need as params when they start, and their results are written back
    here in the GUI thread). Changes are announced to every panel that
    called subscribe().
    """

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / FILENAME
        self._listeners: list = []
        self.sensors: dict[str, dict] = {}
        self.current_id = STD_ID
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.sensors = dict(data.get("sensors", {}))
                self.current_id = data.get("current", STD_ID)
            except Exception:
                # Move a corrupt file aside and rebuild from the defaults
                # (never lose it by overwriting)
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.path.replace(
                    self.path.with_name(f"sensors_broken_{stamp}.json"))
                self.sensors = {}
        fresh = not self.sensors
        if fresh:
            self.sensors = builtin_profiles()
            self._migrate_legacy()
        # Fill in missing keys with defaults (hand-edited / older files)
        migrated = False
        for sid, prof in self.sensors.items():
            prof.setdefault("id", sid)
            base = _std_profile()
            for key, value in base.items():
                if key in ("calibration", "machine_origin", "positions",
                           "scan", "align", "id", "name", "note",
                           "sensor_type", "pad_um"):     # the type is migrated below
                    continue
                prof.setdefault(key, copy.deepcopy(value))
            prof.setdefault("name", sid)
            prof.setdefault("note", "")
            if "sensor_type" not in prof:
                # pixel sensors registered with an older version (id / name pixel...)
                # are migrated to the pixel type (columns/rows = layout measured 2026-10-15)
                if str(sid).lower().startswith("pixel") \
                        or "pixel" in str(prof.get("name", "")).lower():
                    prof["sensor_type"] = "pixel"
                    prof.setdefault("pad_um", 100.0)
                    for ch, (col, row) in PIXEL_2X2_LAYOUT.items():
                        entry = prof.setdefault("readout", {}).setdefault(
                            ch, {"pad": "", "use": True})
                        entry.setdefault("col", col)
                        entry.setdefault("row", row)
                        entry["y_um"] = -float(prof["pad_um"]) / 2.0 \
                            - row * float(prof["pitch_um"])
                    prof["scan"] = derive_scan(prof)
                    prof["align"] = derive_align(prof)
                    prof["note"] = (str(prof.get("note", "")) + " [2026-10-16 migrated to the pixel type: "
                                    "C2 = −X neighbor, C3 = −Y neighbor, origin = corner of the C1 pad]").strip()
                else:
                    prof["sensor_type"] = "strip"
                migrated = True
            prof.setdefault("pad_um", 100.0)
            for ch in CHANNELS:
                if ch in prof.get("readout", {}):
                    prof["readout"][ch].setdefault("col", 0)
                    prof["readout"][ch].setdefault("row", 0)
            prof.setdefault("calibration", None)
            prof.setdefault("machine_origin", None)
            prof.setdefault("positions", {})
            prof.setdefault("scan", derive_scan(prof))
            prof.setdefault("align", derive_align(prof))
        # add the pixel template when the file (older version) has no pixel profile
        if not any(is_pixel(pp) for pp in self.sensors.values()):
            tpl = pixel_profile()
            self.sensors[tpl["id"]] = tpl
            migrated = True
        if self.current_id not in self.sensors:
            self.current_id = next(iter(self.sensors))
        if fresh or migrated:
            self.save()

    def _migrate_legacy(self) -> None:
        """Import the old format (one file shared by all sensors) into
        the standard sensor."""
        std = self.sensors[STD_ID]
        cal_path = self.state_dir / _LEGACY_CALIB
        if cal_path.exists():
            try:
                cal = json.loads(cal_path.read_text(encoding="utf-8"))
                if all(k in cal for k in ("dzdx", "dzdy", "dydx")):
                    std["calibration"] = cal
            except Exception:
                pass
        bm_path = self.state_dir / _LEGACY_BOOKMARKS
        if bm_path.exists():
            try:
                marks = json.loads(bm_path.read_text(encoding="utf-8"))
                marks.pop(_LEGACY_BOOKMARK_MARKER, None)
                presets = scan_geometry.stage_presets()
                preset_coords = [tuple(v) for v in presets.values()]
                for name, pos in marks.items():
                    # Presets that came from scan_geometry are replaced by
                    # "Go to strip" (computed on demand from geometry +
                    # calibration), so they are not migrated. Only the
                    # positions the user saved are carried over, as
                    # machine coordinates. Presets are recognized by name
                    # or by coordinates (the name differs when the file
                    # was written by the Japanese edition)
                    pos = [float(v) for v in pos]
                    if name in presets or any(
                            all(abs(a - b) < 0.5 for a, b in zip(pos, c))
                            for c in preset_coords):
                        continue
                    std["positions"][name] = {"pos_um": pos,
                                              "frame": "machine"}
            except Exception:
                pass

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": VERSION, "current": self.current_id,
                "sensors": self.sensors}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        # atomic replace: a power cut mid-write must not corrupt the file
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # Notification
    # ------------------------------------------------------------------
    def subscribe(self, callback) -> None:
        self._listeners.append(callback)

    def notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------------------
    # Selecting / editing sensors
    # ------------------------------------------------------------------
    def items(self) -> list[tuple[str, str]]:
        return [(sid, p.get("name", sid)) for sid, p in self.sensors.items()]

    def current(self) -> dict:
        return self.sensors[self.current_id]

    def select(self, sid: str) -> None:
        if sid in self.sensors and sid != self.current_id:
            self.current_id = sid
            self.save()
            self.notify()

    def new_id(self, name: str) -> str:
        base = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower() \
            or "sensor"
        sid, n = base, 2
        while sid in self.sensors:
            sid, n = f"{base}_{n}", n + 1
        return sid

    def upsert(self, profile: dict, select: bool = False) -> None:
        validate(profile)
        self.sensors[profile["id"]] = profile
        if select:
            self.current_id = profile["id"]
        self.save()
        self.notify()

    def remove(self, sid: str) -> None:
        if len(self.sensors) <= 1:
            raise ValueError("The last remaining sensor cannot be deleted")
        self.sensors.pop(sid, None)
        if self.current_id == sid:
            self.current_id = next(iter(self.sensors))
        self.save()
        self.notify()

    # ------------------------------------------------------------------
    # Tilt coefficients
    # ------------------------------------------------------------------
    def coefficients(self, sid: str | None = None
                     ) -> tuple[float, float, float, str]:
        """(dzdx, dzdy, dydx) [µm/µm] used by scans, plus a source label.

        Priority: the profile's calibration (Full Calibration / manual)
          → standard sensor only: the camera-measured fit in
            scan_geometry.py
          → otherwise uncalibrated (0, 0, 0)
        """
        prof = self.sensors[sid or self.current_id]
        cal = prof.get("calibration")
        if cal and all(k in cal for k in ("dzdx", "dzdy", "dydx")):
            date = str(cal.get("measured_at", ""))[:10]
            method = str(cal.get("method", ""))
            label = "manual" if ("manual" in method
                                 or _JA_MANUAL in method) \
                else "Full Calibration"
            return (float(cal["dzdx"]), float(cal["dzdy"]),
                    float(cal["dydx"]), f"{label} {date}")
        if prof["id"] == STD_ID:
            g = scan_geometry
            return (g.TILT_DZDX, g.TILT_DZDY, g.ROT_DYDX,
                    f"scan_geometry default (camera, {g.MEASURED_DATE})")
        return 0.0, 0.0, 0.0, "uncalibrated (no correction)"

    def summary(self, sid: str | None = None) -> str:
        dzdx, dzdy, dydx, source = self.coefficients(sid)
        return (f"dZ/dX={dzdx * 1000:+.2f}, dZ/dY={dzdy * 1000:+.2f}, "
                f"dY/dX={dydx * 1000:+.2f} µm/mm [{source}]")

    def rotation_ref_x(self, sid: str | None = None) -> float:
        """X at which the electrode center Y=0 was measured (the Find
        Origin position). Reference point of the rotation."""
        prof = self.sensors[sid or self.current_id]
        cal = prof.get("calibration") or {}
        if is_pixel(prof):
            default = -float(prof.get("pad_um", 100.0)) / 2.0     # center X of the reference pad
        else:
            default = -(float(prof["x_frame_offset_um"]) + 60.0)
        return float(cal.get("x_yc_measured_um", default))

    def set_calibration(self, data: dict, sid: str | None = None) -> Path:
        """Store a calibration result (a history file is kept as well)."""
        sid = sid or self.current_id
        data = dict(data)
        data.setdefault("measured_at", datetime.datetime.now().isoformat())
        data["sensor_id"] = sid
        self.sensors[sid]["calibration"] = data
        self.save()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        hist = self.state_dir / f"sensor_calibration_{sid}_{stamp}.json"
        hist.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        self.notify()
        return hist

    def set_manual_coefficients(self, dzdx: float, dzdy: float,
                                dydx: float) -> Path:
        prev = self.coefficients()
        return self.set_calibration({
            "method": "manual entry",
            "dzdx": dzdx, "dzdy": dzdy, "dydx": dydx,
            "previous": {"dzdx": prev[0], "dzdy": prev[1],
                         "dydx": prev[2], "source": prev[3]}})

    # ------------------------------------------------------------------
    # Machine coordinates of the logical origin / saved positions
    # ------------------------------------------------------------------
    def set_machine_origin(self, pos_um, method: str,
                           sid: str | None = None) -> None:
        self.sensors[sid or self.current_id]["machine_origin"] = {
            "pos_um": [float(v) for v in pos_um],
            "registered_at": datetime.datetime.now().isoformat(
                timespec="seconds"),
            "method": method,
        }
        self.save()
        self.notify()

    def positions(self, sid: str | None = None) -> dict:
        return self.sensors[sid or self.current_id]["positions"]

    def set_position(self, name: str, pos_um, frame: str) -> None:
        if frame not in ("logical", "machine"):
            raise ValueError(f"Unknown coordinate frame: {frame}")
        self.positions()[name] = {"pos_um": [float(v) for v in pos_um],
                                  "frame": frame}
        self.save()
        self.notify()

    def delete_position(self, name: str) -> None:
        self.positions().pop(name, None)
        self.save()
        self.notify()

    # ------------------------------------------------------------------
    # Geometry-based positions (for "Go to strip")
    # ------------------------------------------------------------------
    def places(self) -> list[tuple[str, str]]:
        """Choices of "Go to electrode" (label, key). Depends on the sensor type."""
        return list(PIXEL_PLACES if is_pixel(self.current()) else STRIP_PLACES)

    def position_for(self, ch: str, where: str) -> list[float]:
        """Logical coordinates [µm] of the electrode of CH (strip: strip_position,
        pixel: pixel_position)."""
        if is_pixel(self.current()):
            return self.pixel_position(ch, where)
        return self.strip_position(ch, where)

    def pixel_position(self, ch: str, where: str) -> list[float]:
        """Pixel sensor: logical coordinates [µm] related to the pixel of CH.

        where: "center" (pad center = pixel center, no signal) / "beside" (on the
        silicon probe_off away from the pad center, clear signal) / "corner" (the
        4-pixel corner closest to the center of the readout pixels). Y is corrected
        for the rotation and Z for the tilt with the current coefficients.
        """
        prof = self.current()
        dzdx, dzdy, dydx, _ = self.coefficients()
        pad = float(prof.get("pad_um", 100.0))
        pitch = float(prof["pitch_um"])
        probe = float(prof.get("align", {}).get("y_probe_off_um", pad / 2.0 + 80.0))
        cx, cy = pixel_center(prof, ch)
        if where == "beside":
            x, y = cx, cy - probe
        elif where == "corner":
            cs = [pixel_center(prof, c) for c in used_channels(prof)]
            mx = sum(c[0] for c in cs) / len(cs)
            my = sum(c[1] for c in cs) / len(cs)
            sx = 1.0 if mx > cx else -1.0
            sy = 1.0 if my > cy else -1.0
            x, y = cx + sx * pitch / 2.0, cy + sy * pitch / 2.0
        else:
            x, y = cx, cy
        y = y + dydx * (x - self.rotation_ref_x())
        return [x, y, dzdx * x + dzdy * y]

    def strip_position(self, ch: str, where: str) -> list[float]:
        """Logical coordinates [µm] on the electrode center line of CH.

        where: "near" (100 µm inside the readout-side end) / "mid"
        (middle) / "far" (100 µm inside the far end). Y is corrected for
        the rotation and Z for the tilt with the current coefficients
        (assuming focus at the logical origin, Z=0).
        """
        prof = self.current()
        dzdx, dzdy, dydx, _ = self.coefficients()
        x_off = float(prof["x_frame_offset_um"])
        length = float(prof["strip_length_um"])
        inset = min(100.0, 0.1 * length)
        depth = {"near": inset, "mid": 0.5 * length,
                 "far": length - inset}[where]
        x = -(x_off + depth)
        ro = prof["readout"]
        y_ref = float(ro[prof["ref_channel"]]["y_um"])
        y = float(ro[ch]["y_um"]) - y_ref \
            + dydx * (x - self.rotation_ref_x())
        return [x, y, dzdx * x + dzdy * y]

    def adjacent_pairs(self) -> list[tuple[str, str]]:
        """Pairs of readout CHs whose electrodes are adjacent (for the 2-channel
        sum of the TUNE optimization). Strips: neighbors in Y (|ΔY| < 1.5 pitch);
        pixels: column or row differs by 1."""
        prof = self.current()
        ro = prof["readout"]
        pitch = float(prof["pitch_um"])
        if is_pixel(prof):
            chans = used_channels(prof)
            pairs = []
            for i, a in enumerate(chans):
                for b in chans[i + 1:]:
                    da = abs(int(ro[a].get("col", 0)) - int(ro[b].get("col", 0)))
                    db = abs(int(ro[a].get("row", 0)) - int(ro[b].get("row", 0)))
                    if da + db == 1:
                        pairs.append((a, b))
            return pairs
        chans = sorted(used_channels(prof),
                       key=lambda c: -float(ro[c]["y_um"]))
        pairs = []
        for a, b in zip(chans, chans[1:]):
            if abs(float(ro[a]["y_um"]) - float(ro[b]["y_um"])) \
                    < 1.5 * pitch:
                pairs.append((a, b))
        return pairs

    def midpoint_position(self, ch_a: str, ch_b: str,
                          current_um) -> list[float]:
        """Logical coordinates [µm] of the midpoint between two electrodes.

        Strips: on the midline of the two electrodes at the current X; pixels:
        the midpoint of the two pixel centers. Y is corrected for the rotation,
        Z is shifted relative to the current Z by the tilt (assumes the current
        position is in focus).
        """
        prof = self.current()
        dzdx, dzdy, dydx, _ = self.coefficients()
        x, y_cur, z_cur = (float(v) for v in current_um)
        ro = prof["readout"]
        if is_pixel(prof):
            (xa, ya), (xb, yb) = pixel_center(prof, ch_a), pixel_center(prof, ch_b)
            x_mid, y_mid = 0.5 * (xa + xb), 0.5 * (ya + yb)
            y_mid += dydx * (x_mid - self.rotation_ref_x())
            return [x_mid, y_mid, z_cur + dzdx * (x_mid - x) + dzdy * (y_mid - y_cur)]
        y_ref = float(ro[prof["ref_channel"]]["y_um"])
        y_mid = 0.5 * (float(ro[ch_a]["y_um"]) + float(ro[ch_b]["y_um"])) \
            - y_ref + dydx * (x - self.rotation_ref_x())
        return [x, y_mid, z_cur + dzdy * (y_mid - y_cur)]
