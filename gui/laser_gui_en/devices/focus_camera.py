# -*- coding: utf-8 -*-
"""Focus camera (USB camera) capture for checking the irradiation position.

Thin wrapper around OpenCV VideoCapture. Used to see the laser spot and
sensor position from outside the dark room. Frames are BGR numpy arrays.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

import numpy as np

LogFunc = Optional[Callable[[str, str], None]]


class FocusCamera:
    def __init__(self, index: int = 0, simulate: bool = False,
                 log: LogFunc = None):
        self.index = index
        self.simulate = simulate
        self._log = log or (lambda d, t: None)
        self._cap = None
        self._lock = threading.Lock()
        self.connected = False
        self._sim_t0 = time.monotonic()

    def open(self) -> None:
        if self.simulate:
            self.connected = True
            self._log("INFO", f"[SIM] camera {self.index} opened")
            return
        import cv2
        # The default MSMF backend on Windows often ignores property
        # controls (exposure, focus, ...), so prefer DirectShow
        if sys.platform.startswith("win"):
            self._cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                self._cap.release()
                self._cap = cv2.VideoCapture(self.index)
        else:
            self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            self._cap = None
            # The focus camera on this setup is an OMRON SENTECH
            # STC-MBS312U3V used through the Sentech DirectShow filter
            # (not UVC). If it cannot be opened, the Sentech driver layer
            # almost certainly does not see the camera
            raise RuntimeError(
                f"Cannot open camera {self.index}.\n"
                "The Sentech camera (STC-MBS312U3V) may not be responding:\n"
                "1) Unplug the camera's USB cable at the PC, wait 10 s and\n"
                "   plug it back in (if Windows plays no connection sound,\n"
                "   try another USB3 port)\n"
                "2) If that does not help, reboot the PC")
        self.connected = True
        w = self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        h = self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self._log("INFO", f"camera {self.index} opened ({int(w)}x{int(h)})")

    def close(self) -> None:
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            self.connected = False
        self._log("INFO", "camera closed")

    def read(self) -> Optional[np.ndarray]:
        """Grab one frame (BGR). Returns None on failure."""
        with self._lock:
            if self.simulate:
                return self._simulate_frame()
            if self._cap is None:
                return None
            ok, frame = self._cap.read()
            return frame if ok else None

    # ------------------------------------------------------------------
    # Property control (gain only)
    # ------------------------------------------------------------------
    # The camera on this setup (STC-MBS312U3V + a manual-focus C-mount
    # lens) supports neither autofocus nor focus-position control, so
    # the GUI only operates the gain (cleaned up 2026-10). The actual
    # value after setting is logged, so if it does not change you can
    # conclude the camera does not support it.

    def _set_prop(self, prop_name: str, value: float) -> Optional[float]:
        """Set an OpenCV property and return the actual value afterwards
        (None on failure)."""
        if self.simulate:
            self._log("INFO", f"[SIM] {prop_name} := {value}")
            return float(value)
        import cv2
        prop = getattr(cv2, prop_name)
        with self._lock:
            if self._cap is None:
                raise RuntimeError("Camera is not open")
            ok = self._cap.set(prop, float(value))
            actual = self._cap.get(prop)
        self._log("INFO",
                  f"{prop_name} := {value} → actual {actual}"
                  f"{'' if ok else ' (set failed: camera may not support it)'}")
        return actual if ok else None

    def set_gain(self, value: float) -> Optional[float]:
        return self._set_prop("CAP_PROP_GAIN", value)

    # ------------------------------------------------------------------
    def _simulate_frame(self) -> np.ndarray:
        """Fake camera image: sensor-board-like background + a moving
        laser spot."""
        import cv2
        h, w = 480, 640
        img = np.full((h, w, 3), (30, 60, 30), dtype=np.uint8)
        # Vertical bars imitating strip electrodes (500 µm pitch look)
        for x in range(40, w, 80):
            cv2.rectangle(img, (x, 40), (x + 10, h - 40), (140, 150, 150), -1)
        # Laser spot (slow circular motion)
        t = time.monotonic() - self._sim_t0
        cx = int(w / 2 + 60 * np.cos(t / 3))
        cy = int(h / 2 + 40 * np.sin(t / 3))
        cv2.circle(img, (cx, cy), 6, (80, 255, 120), -1)
        cv2.circle(img, (cx, cy), 12, (60, 200, 90), 2)
        cv2.putText(img, "FOCUS CAMERA (SIMULATION)", (16, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
        return img
