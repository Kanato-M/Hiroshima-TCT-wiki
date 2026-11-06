# -*- coding: utf-8 -*-
"""AC-LGAD laser-scan control GUI entry point.

Usage:
    python main.py                 # start with config.py + config_local.json
    python main.py --simulate      # run all devices in simulation (UI check)
    python main.py --config my.json

After powering up the instruments, this GUI lets you
  1. connect the oscilloscope, stage, laser and camera,
  2. position the irradiation point with the stage while watching the camera,
  3. turn the laser emission ON and inspect/save waveforms on the scope,
all without entering the dark room.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add this file's directory to the import path (so the GUI can be
# launched from anywhere)
sys.path.insert(0, str(Path(__file__).parent))

# Logical resolution required to fit every GUI panel. Below this the panels
# collapse and overlap when maximized (the lab's EX-LDHU431DB is 4K with
# Windows scaling at 300%, leaving only 1280x720 logical pixels, so the
# correction below is mandatory there).
_MIN_LOGICAL_W = 1600
_MIN_LOGICAL_H = 900


def _fit_scale_factor() -> float | None:
    """Return a Qt scale correction factor for high-DPI screens that lack
    logical resolution.

    When the Windows scaling setting (e.g. 300%) would push the logical
    resolution below _MIN_LOGICAL_W/H, lower the effective scale via
    QT_SCALE_FACTOR (<1.0) to the largest value that still fits the screen.
    Text is rendered at the physical resolution, so nothing gets blurry —
    everything is just drawn slightly smaller.
    Returns None when no correction is needed (screen large enough / 100%).
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # Make the process DPI-aware first so we can read physical pixels
        # (Qt applies an equivalent setting later, so this has no side effect)
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PMv2
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        phys_w = user32.GetSystemMetrics(0)
        phys_h = user32.GetSystemMetrics(1)
        win_scale = user32.GetDpiForSystem() / 96.0
    except Exception:
        return None
    if phys_w <= 0 or phys_h <= 0 or win_scale <= 1.0:
        return None
    # Largest scale this screen can afford (any larger and the GUI won't fit)
    allowed = min(phys_w / _MIN_LOGICAL_W, phys_h / _MIN_LOGICAL_H)
    if allowed >= win_scale:
        return None  # the Windows setting already fits
    # Never go below 100% (1:1)
    return max(allowed, 1.0) / win_scale


_factor = _fit_scale_factor()
if _factor is not None and "QT_SCALE_FACTOR" not in os.environ:
    os.environ["QT_SCALE_FACTOR"] = f"{_factor:.3f}"

from PySide6.QtWidgets import QApplication  # noqa: E402

from config import GuiConfig  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AC-LGAD laser-scan control GUI")
    parser.add_argument("--simulate", action="store_true",
                        help="run all devices in simulation mode")
    parser.add_argument("--config", default=None,
                        help="path to a JSON file with config overrides")
    args = parser.parse_args()

    cfg = GuiConfig.load(args.config)
    if args.simulate:
        cfg.force_simulate()

    app = QApplication(sys.argv)
    window = MainWindow(cfg)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
