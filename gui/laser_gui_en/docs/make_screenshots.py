# -*- coding: utf-8 -*-
"""Write screenshots for the manual, one per panel / tab / group box.

Usage (in the laser_gui_v4_en directory):
    python docs\\make_screenshots.py                # writes to docs\\screenshots\\
    python docs\\make_screenshots.py --out <dir>    # custom output directory

Starts the GUI with all devices connected in simulation mode (position
display, laser status and the simulated camera image filled in) and saves
  - the whole window / toolbar / status bar / [Control]-[Analysis] tabs
  - every panel (scope, communication log, camera, stage, laser, auto
    scan, analysis), each of its tabs (switched before shooting) and
    each of its group boxes
  - the laser panel with emission ON, the sensor dialog (strip / pixel)
as individual PNG files, with a list in INDEX.md in the same folder.
The images are rendered with the same scale correction as main.py (2x or
more pixels on high-DPI screens), so they can be pasted into the manual
as they are. No instrument and no logs/ are touched (the simulation uses
logs_sim/).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_DIR))

import main  # noqa: E402,F401  (applies the same QT_SCALE_FACTOR correction as main.py)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (QApplication, QGroupBox, QMessageBox,  # noqa: E402
                               QTabWidget, QToolBar, QWidget)

from config import GuiConfig  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.sensor_dialog import SensorDialog  # noqa: E402


def pump(app: QApplication, seconds: float) -> None:
    """Wait while running the event loop (async connect / polling)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def slug(text: str, fallback: str) -> str:
    """ASCII-only slug for file names (fallback if nothing is left)."""
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or fallback


class Shooter:
    def __init__(self, app: QApplication, out: Path):
        self.app = app
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.rows: list[tuple[str, str, str]] = []
        self.n = 0

    def shot(self, widget: QWidget, name: str, title: str) -> None:
        self.app.processEvents()
        pm = widget.grab()
        self.n += 1
        fname = f"{self.n:02d}_{name}.png"
        pm.save(str(self.out / fname))
        self.rows.append((fname, title, f"{pm.width()}×{pm.height()}"))
        print(f"  {fname:40s} {title}")

    def panel_with_parts(self, panel: QWidget, name: str, title: str) -> None:
        """Whole panel → each tab (switched) → each group box."""
        self.shot(panel, name, title)
        for tabs in panel.findChildren(QTabWidget):
            keep = tabs.currentIndex()
            for i in range(tabs.count()):
                tabs.setCurrentIndex(i)
                self.app.processEvents()
                tab_title = tabs.tabText(i)
                tab_id = f"tab{i + 1}_{slug(tab_title, 'tab')}"
                self.shot(panel, f"{name}_{tab_id}",
                          f"{title} — [{tab_title}] tab")
                page = tabs.widget(i)
                for k, box in enumerate(page.findChildren(QGroupBox), 1):
                    if box.title():
                        self.shot(box, f"{name}_{tab_id}_{slug(box.title(), f'box{k}')}",
                                  f"{title} — [{tab_title}] — \"{box.title()}\"")
            tabs.setCurrentIndex(keep)
        # group boxes outside the tabs
        in_tabs = set()
        for tabs in panel.findChildren(QTabWidget):
            for i in range(tabs.count()):
                in_tabs.update(id(b) for b in tabs.widget(i).findChildren(QGroupBox))
        for k, box in enumerate(panel.findChildren(QGroupBox), 1):
            if box.title() and id(box) not in in_tabs:
                self.shot(box, f"{name}_{slug(box.title(), f'box{k}')}",
                          f"{title} — \"{box.title()}\"")

    def write_index(self, header: str) -> None:
        lines = [header, "", "| File | Subject | Pixels |", "|---|---|---|"]
        for fname, title, size in self.rows:
            lines.append(f"| `{fname}` | {title} | {size} |")
        (self.out / "INDEX.md").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")


def run(out: Path) -> int:
    cfg = GuiConfig.load()
    cfg.force_simulate()
    app = QApplication(sys.argv)
    win = MainWindow(cfg)
    win.show()
    pump(app, 0.8)

    # --- connect all devices in simulation (show the connected state) ---
    win.stage_panel._home_reminded = True   # suppress the (modal) homing reminder
    win.scope_panel._toggle_connect()
    win.stage_panel._toggle_connect()
    win.laser_panel._toggle_connect()
    win.camera_panel._toggle()
    pump(app, 3.0)      # wait for connect, first poll, camera image
    win.scope_panel._toggle_mirror()         # start the waveform mirror
    pump(app, 2.5)

    sh = Shooter(app, out)
    print(f"Output: {out}")

    # --- whole window, toolbar, status bar, main tabs ---
    sh.shot(win, "main_window", "Whole window ([Control] tab)")
    toolbar = win.findChild(QToolBar)
    sh.shot(toolbar, "toolbar", "Toolbar (sensor selection / task progress & abort / emergency stop)")
    sh.shot(win.statusBar(), "statusbar", "Status bar (sensor, bias, readout mapping)")
    main_tabs = win.centralWidget()
    sh.shot(main_tabs.tabBar(), "main_tabs", "[Control] / [Analysis] tabs")

    # --- panels ---
    sh.panel_with_parts(win.scope_panel, "scope", "Oscilloscope panel")
    sh.panel_with_parts(win.log_panel, "log", "Communication log panel")
    sh.panel_with_parts(win.camera_panel, "camera", "Focus camera panel")
    sh.panel_with_parts(win.stage_panel, "stage", "Stage panel")
    sh.panel_with_parts(win.laser_panel, "laser", "Laser panel")
    # emission ON display (simulation)
    win.laser_panel._set_emission(True)
    pump(app, 1.0)
    sh.shot(win.laser_panel, "laser_emission_on", "Laser panel — emission ON")
    win.laser_panel._set_emission(False)
    pump(app, 1.0)
    sh.panel_with_parts(win.scan_panel, "scan", "Auto scan panel")

    # --- analysis tab ---
    main_tabs.setCurrentIndex(1)
    pump(app, 0.3)
    sh.shot(win, "main_window_analysis", "Whole window ([Analysis] tab)")
    sh.panel_with_parts(win.analysis_panel, "analysis", "Analysis panel")
    main_tabs.setCurrentIndex(0)

    # --- sensor dialog (strip / pixel) ---
    dlg = SensorDialog(win.registry, win)
    dlg.setAttribute(Qt.WA_DontShowOnScreen, False)
    dlg.show()
    pump(app, 0.5)
    sh.shot(dlg, "sensor_dialog_strip", "Sensor dialog (strip type)")
    idx = dlg.type_combo.findData("pixel")
    if idx >= 0:
        dlg.type_combo.setCurrentIndex(idx)
        pump(app, 0.3)
        sh.shot(dlg, "sensor_dialog_pixel", "Sensor dialog (pixel type)")
    dlg.reject()

    sh.write_index(
        f"# Screenshots for the manual ({PKG_DIR.name})\n\n"
        "Generated automatically by `docs/make_screenshots.py` in "
        "simulation mode, with all devices connected.")
    print(f"{sh.n} images written → {out / 'INDEX.md'}")

    # --- exit (answer the close-confirmation dialog with Yes) ---
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    win.close()
    pump(app, 0.5)
    return 0


def main_cli() -> int:
    parser = argparse.ArgumentParser(
        description="Generate screenshots for the manual (per panel / tab / group)")
    parser.add_argument("--out", default=str(PKG_DIR / "docs" / "screenshots"),
                        help="output directory (default: docs/screenshots)")
    args = parser.parse_args()
    return run(Path(args.out))


if __name__ == "__main__":
    sys.exit(main_cli())
