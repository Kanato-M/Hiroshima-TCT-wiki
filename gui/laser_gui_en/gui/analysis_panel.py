# -*- coding: utf-8 -*-
"""Analysis tab: run the quick-look analysis of acquired scan data from
the GUI.

analysis\\run_analysis.py is launched as a subprocess (QProcess):
  - the GUI thread is not blocked (analysis works during measurements/scans)
  - matplotlib / multiprocessing stay isolated from the GUI process
  - it can be aborted (killed) at any time

Features:
  - pick a scan folder from the list under data\\ (newest first), or browse
  - the scan kind (1dX / 1dY / 1dZ / 2dYX / tune / 1d_tune) is detected
    automatically and the analysis script for that kind is run (by
    run_analysis); manual override possible. Same definitions as the
    "matome" analysis note
  - options: ROOT conversion (--root / --no-raw), cache rebuild (--force),
    thresholds [mV], ...
  - run log + progress bar for the reduce/conversion steps
  - when finished: preview the generated figures (PNG) in place and open
    the results folder

As specified by the analysis package, outputs go outside the repository
(results\\<scan name>\\ and the .root next to the scan folder).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtGui import QFont, QPixmap, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QVBoxLayout,
                               QWidget)

# laser_gui_v2_en\gui\ → laser_gui_v2_en\analysis\
ANALYSIS_DIR = Path(__file__).resolve().parents[1] / "analysis"


class AnalysisPanel(QWidget):
    """Panel that runs the quick-look scan analysis (Analysis tab body)."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._proc: QProcess | None = None
        self._results_dir: Path | None = None
        self._preview_pixmap: QPixmap | None = None

        root = QHBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split)

        # ============ Left: settings, run, log ============
        left = QWidget()
        lv = QVBoxLayout(left)

        # --- Scan selection ---
        box = QGroupBox("Scan data")
        g = QGridLayout(box)
        self.scan_combo = QComboBox()
        self.scan_combo.setMinimumWidth(280)
        g.addWidget(self.scan_combo, 0, 0, 1, 2)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_scans)
        g.addWidget(btn_refresh, 0, 2)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_scan)
        g.addWidget(btn_browse, 0, 3)
        self.scan_info = QLabel("-")
        self.scan_info.setWordWrap(True)
        g.addWidget(self.scan_info, 1, 0, 1, 4)
        lv.addWidget(box)

        # --- Options ---
        box = QGroupBox("Analysis options")
        g = QGridLayout(box)
        g.addWidget(QLabel("Mode:"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Auto", "1dX", "1dY", "1dZ", "2dYX", "tune", "1d_tune", "pixel"])
        self.mode_combo.setToolTip(
            "There is one analysis script per scan kind:\n"
            "1dX along the strip (Elmore delay etc.) / 1dY across the strips (position resolution, v_eff etc.) / 1dZ focus\n"
            "2dYX all items / tune amplitude dependence (1/A law) / 1d_tune position x intensity\n"
            "pixel: pixel 2x2 readout (auto-detected from sensor_type=pixel in the log; time / position "
            "resolution of 2/3/4-pixel combinations)")
        g.addWidget(self.mode_combo, 0, 1)
        self.chk_force = QCheckBox("Rebuild cache (--force)")
        g.addWidget(self.chk_force, 0, 2, 1, 2)

        self.chk_template = QCheckBox("Also run the template-fit analysis (paper figures 01–09 / pixel pix01–06)")
        self.chk_template.setChecked(True)
        self.chk_template.setToolTip(
            "2dYX: macro\\template_timing\\tt_pipeline.py fits the pulse template (CFD50) directly on the .trc files →\n"
            "time resolution (optimal method per position), position resolution of the uncalibrated amplitude centroid,\n"
            "amplitude distributions, rise time, jitter and propagation: the paper figures 01–09 in results\\<scan>\\paper_plots\\\n"
            "(same content as results\\paper_plots_20261013; about 30 min for 2000 segments x 16000 points, minutes afterwards).\n"
            "pixel: tt_pixel.py (2/3/4-pixel combinations). OFF: only the quick-look (linear CFD) figures")
        g.addWidget(self.chk_template, 1, 0, 1, 4)

        self.chk_root = QCheckBox("Convert to ROOT")
        self.chk_root.toggled.connect(
            lambda on: self.chk_noraw.setEnabled(on))
        g.addWidget(self.chk_root, 2, 0, 1, 2)
        self.chk_noraw = QCheckBox("No raw waveforms (features only)")
        self.chk_noraw.setEnabled(False)
        g.addWidget(self.chk_noraw, 2, 2, 1, 2)

        def _thr(v):
            s = QDoubleSpinBox()
            s.setRange(0.1, 500.0)
            s.setDecimals(1)
            s.setValue(v)
            s.setSuffix(" mV")
            return s
        # Defaults are the same as in the analysis note (matome): hit = max(8 mV, 5σ), quality = noise < max(4 mV, 3σ)
        g.addWidget(QLabel("Hit thr:"), 3, 0)
        self.spin_hit = _thr(8.0)
        self.spin_hit.setToolTip("Lower amplitude limit of a hit. The actual threshold is max(this value, 5 x global noise reference)")
        g.addWidget(self.spin_hit, 3, 1)
        g.addWidget(QLabel("Noise max:"), 3, 2)
        self.spin_noise = _thr(4.0)
        self.spin_noise.setToolTip("Quality cut: baseline RMS < max(this value, 3 x global noise reference)")
        g.addWidget(self.spin_noise, 3, 3)
        g.addWidget(QLabel("ToT thr:"), 4, 0)
        self.spin_tot = _thr(10.0)
        g.addWidget(self.spin_tot, 4, 1)
        g.addWidget(QLabel("Workers:"), 4, 2)
        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 32)
        self.spin_workers.setValue(8)
        g.addWidget(self.spin_workers, 4, 3)

        def _um(v, lo=-50000.0, hi=50000.0):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(0)
            s.setSingleStep(10.0)
            s.setValue(v)
            s.setSuffix(" um")
            return s
        # Offsets to sensor coordinates (X = 0 is the outer frame edge on the readout side). 0 for scans taken with the current GUI
        g.addWidget(QLabel("X offset:"), 5, 0)
        self.spin_xoff = _um(0.0)
        self.spin_xoff.setToolTip("Log coordinates → sensor coordinates. For scans before 2026-09-07 use e.g. -632")
        g.addWidget(self.spin_xoff, 5, 1)
        g.addWidget(QLabel("Y offset:"), 5, 2)
        self.spin_yoff = _um(0.0)
        g.addWidget(self.spin_yoff, 5, 3)
        g.addWidget(QLabel("Pitch:"), 6, 0)
        self.spin_pitch = _um(500.0, 50.0, 5000.0)
        g.addWidget(self.spin_pitch, 6, 1)
        g.addWidget(QLabel("Body X range:"), 6, 2)
        hb = QHBoxLayout()
        self.spin_body_lo = _um(-9900.0)
        self.spin_body_hi = _um(-700.0)
        for w_ in (self.spin_body_lo, self.spin_body_hi):
            w_.setToolTip("X range used for the fits along the strip and the representative values (excludes the pad region and beyond the far end)")
            hb.addWidget(w_)
        g.addLayout(hb, 6, 3)
        lv.addWidget(box)

        # --- Run / progress ---
        row = QHBoxLayout()
        self.btn_run = QPushButton("Run analysis")
        self.btn_run.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white;"
            " font-weight: bold; padding: 5px 14px; }")
        self.btn_run.clicked.connect(self.start_analysis)
        row.addWidget(self.btn_run)
        self.btn_abort = QPushButton("Abort")
        self.btn_abort.setEnabled(False)
        self.btn_abort.clicked.connect(self.abort_analysis)
        row.addWidget(self.btn_abort)
        self.btn_open = QPushButton("Open results")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_results)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        lv.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        lv.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(8)
        self.log_view.setFont(f)
        lv.addWidget(self.log_view, 1)

        split.addWidget(left)

        # ============ Right: figure preview ============
        right = QWidget()
        rv = QVBoxLayout(right)
        row = QHBoxLayout()
        row.addWidget(QLabel("Figure:"))
        self.fig_combo = QComboBox()
        self.fig_combo.currentIndexChanged.connect(self._show_figure)
        row.addWidget(self.fig_combo, 1)
        rv.addLayout(row)
        self.fig_label = QLabel("Figures appear here after an analysis run")
        self.fig_label.setAlignment(Qt.AlignCenter)
        self.fig_scroll = QScrollArea()
        self.fig_scroll.setWidget(self.fig_label)
        self.fig_scroll.setWidgetResizable(True)
        rv.addWidget(self.fig_scroll, 1)
        split.addWidget(right)
        split.setSizes([460, 640])

        self.refresh_scans()

    # ------------------------------------------------------------------
    # Scan folder list
    # ------------------------------------------------------------------
    def _data_root(self) -> Path:
        """Transfer-destination data root (config.transfer_dest_dir,
        default ..\\data)."""
        p = Path(self.cfg.transfer_dest_dir)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[1] / p
        return p.resolve()

    def refresh_scans(self):
        """List the scan_* folders under data\\, newest first."""
        current = self.scan_combo.currentData()
        self.scan_combo.clear()
        root = self._data_root()
        scans = sorted(root.glob("*/*/scan_*"),
                       key=lambda p: p.name, reverse=True)
        scans = [s for s in scans if s.is_dir()]
        for s in scans:
            try:
                label = str(s.relative_to(root))
            except ValueError:
                label = s.name
            self.scan_combo.addItem(label, str(s))
        if current:
            i = self.scan_combo.findData(current)
            if i >= 0:
                self.scan_combo.setCurrentIndex(i)
        self.scan_info.setText(
            f"Data root: {root}  ({len(scans)} scans)")

    def _browse_scan(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select scan folder", str(self._data_root()))
        if d:
            self.scan_combo.insertItem(0, d, d)
            self.scan_combo.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return (self._proc is not None
                and self._proc.state() != QProcess.NotRunning)

    def start_analysis(self):
        if self.is_running():
            return
        scan_path = self.scan_combo.currentData()
        if not scan_path:
            QMessageBox.warning(self, "Cannot start analysis",
                                "Select a scan folder first")
            return
        if not (ANALYSIS_DIR / "run_analysis.py").exists():
            QMessageBox.warning(self, "Cannot start analysis",
                                f"Analysis script not found:\n"
                                f"{ANALYSIS_DIR}\\run_analysis.py")
            return

        args = ["-u", "run_analysis.py", scan_path,
                "--workers", str(self.spin_workers.value()),
                "--hit-thr", f"{self.spin_hit.value():g}",
                "--noise-max", f"{self.spin_noise.value():g}",
                "--tot-thr", f"{self.spin_tot.value():g}",
                "--x-off", f"{self.spin_xoff.value():g}",
                "--y-off", f"{self.spin_yoff.value():g}",
                "--pitch", f"{self.spin_pitch.value():g}",
                "--body-x", f"{self.spin_body_lo.value():g}", f"{self.spin_body_hi.value():g}"]
        if self.mode_combo.currentIndex() > 0:
            args += ["--mode", self.mode_combo.currentText()]
        if self.chk_force.isChecked():
            args.append("--force")
        if not self.chk_template.isChecked():
            args.append("--no-template")
        if self.chk_root.isChecked():
            args.append("--root")
            if self.chk_noraw.isChecked():
                args.append("--no-raw")

        self._results_dir = None
        self.log_view.clear()
        self._append_log(f"$ python {' '.join(args[1:])}\n")
        self.progress.setRange(0, 0)      # busy until a progress line arrives

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(str(ANALYSIS_DIR))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start(sys.executable, args)

        self.btn_run.setEnabled(False)
        self.btn_abort.setEnabled(True)

    def abort_analysis(self):
        if self.is_running():
            self._append_log("\n★ Abort requested (killing process) ★\n")
            self._proc.kill()

    def shutdown(self):
        """Cleanup on GUI exit (stops a running process)."""
        if self.is_running():
            self._proc.kill()
            self._proc.waitForFinished(2000)

    # ------------------------------------------------------------------
    # Process output / termination handling
    # ------------------------------------------------------------------
    _RE_PROGRESS = re.compile(
        r"(?:reduce\s+(\d+)/(\d+)|(\d+)/(\d+)\s+points)")

    def _append_log(self, text: str):
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.insertPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_output(self):
        if self._proc is None:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", errors="replace")
        self._append_log(text)
        for line in text.splitlines():
            m = self._RE_PROGRESS.search(line)
            if m:
                done, total = (m.group(1), m.group(2)) if m.group(1) \
                    else (m.group(3), m.group(4))
                self.progress.setRange(0, int(total))
                self.progress.setValue(int(done))
            elif line.startswith("results"):
                # Pick up the results dir from run_analysis's
                # "results  : <path>" line
                path = line.split(":", 1)[1].strip()
                if path:
                    self._results_dir = Path(path)

    def _on_error(self, err):
        if self._proc is not None \
                and self._proc.state() == QProcess.NotRunning:
            self._append_log(f"\n[process error] {err}\n")

    def _on_finished(self, code: int, status):
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if code == 0 else 0)
        self.btn_run.setEnabled(True)
        self.btn_abort.setEnabled(False)
        if code == 0:
            self._append_log("\n=== Analysis finished ===\n")
        else:
            self._append_log(f"\n=== Exited (code {code}) ===\n")
        if self._results_dir and self._results_dir.is_dir():
            self.btn_open.setEnabled(True)
            self._load_figures()

    # ------------------------------------------------------------------
    # Results (figure preview)
    # ------------------------------------------------------------------
    def _load_figures(self):
        self.fig_combo.blockSignals(True)
        self.fig_combo.clear()
        pngs = sorted(self._results_dir.glob("*.png"))
        for p in pngs:
            self.fig_combo.addItem(p.name, str(p))
        self.fig_combo.blockSignals(False)
        if pngs:
            self.fig_combo.setCurrentIndex(0)
            self._show_figure(0)

    def _show_figure(self, _index: int):
        path = self.fig_combo.currentData()
        if not path:
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.fig_label.setText(f"Failed to load: {path}")
            return
        self._preview_pixmap = pm
        self._rescale_figure()

    def _rescale_figure(self):
        if self._preview_pixmap is None:
            return
        w = max(self.fig_scroll.viewport().width() - 4, 100)
        self.fig_label.setPixmap(self._preview_pixmap.scaledToWidth(
            min(w, self._preview_pixmap.width()),
            Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_figure()

    def _open_results(self):
        if self._results_dir and self._results_dir.is_dir():
            os.startfile(str(self._results_dir))  # noqa: S606 (Windows)
