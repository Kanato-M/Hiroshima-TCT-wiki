# -*- coding: utf-8 -*-
"""Auto Scan panel (Phase 3).

Enter the scan parameters → show the estimated points / events → run.
The run itself is scan_worker.ScanWorker (separate thread). Progress
display and abort are centralized in the toolbar task display
(gui/task_runner.py).

Coordinates are relative to the logical origin [µm] (frame edge X=0,
electrode center of the reference CH Y=0, focus Z=0). The origin is set
with "Find Origin" in the [Alignment] tab of the stage panel. The stage
moves with relative moves (M:W).

The default scan range and the tilt coefficients come from the current
sensor profile (sensors.py). Switching the sensor swaps the default
range.

Sensor tilt correction (1d/2d):
  Rotation tracking (Y): shift Y to follow the strips' in-plane rotation
  dY/dX
  Z tracking: choose one of these 3 methods (see
  scan_worker.build_points)
    - Coefficients (focus at logical origin): the plane built from the
      Full Calibration / manually entered coefficients is anchored at
      the logical origin. The standard right after Find Origin.
      Independent of the start position
    - Coefficients (focus at current position): the same plane is
      anchored at the current position at start (when starting from a
      place where the focus was adjusted manually)
    - Measured Z (start / end points or four corners): the focus Z
      measured at the edges of the scan range is interpolated linearly /
      bilinearly. A simple and reliable method that needs no coefficient
      calibration

Attached feature: Data Retrieval (scope → PC, SSH/SFTP;
gui/transfer_worker.py). Z focus and auto alignment moved to the stage
panel, TUNE optimization to the laser panel (2026-10).
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from devices.scope_scp import ScpFetcher, host_from_resource

from .scan_worker import ScanWorker, build_points, count_points, scan_corners
from .transfer_worker import TransferWorker
from .widgets import ElidedLabel
from .worker import AsyncCaller

AXIS_ITEMS = [("X (axis 1)", 0), ("Y (axis 2)", 1), ("Z (axis 3)", 2)]

Z_MODES = [
    ("Coefficients (focus at origin)", "coef_origin"),
    ("Coefficients (focus at current pos.)", "coef_current"),
    ("Measured Z (end points / corners)", "points"),
    ("Off", "off"),
]
Z_MODE_TIPS = (
    "Z (focus) tracking method:\n"
    "- Coefficients (focus at origin): anchor the plane of the tilt\n"
    "  coefficients at the logical origin (0,0,0). The standard right\n"
    "  after Find Origin. Same result wherever the stage is at scan start\n"
    "- Coefficients (focus at current pos.): anchor the plane at the\n"
    "  current position at start. For starting from a position where the\n"
    "  focus was adjusted manually\n"
    "- Measured Z: measure and enter the focus Z at the start / end\n"
    "  points of the scan range (four corners for 2D) and interpolate\n"
    "  linearly (bilinearly) in between. No coefficient calibration needed\n"
    "- Off: do not move Z")


class ScanPanel(QGroupBox):
    def __init__(self, scope, stage, laser, registry, runner, cfg,
                 get_stage_pos=None, parent=None):
        super().__init__("Auto Scan (Phase 3)", parent)
        self.scope = scope
        self.stage = stage
        self.laser = laser
        self.registry = registry
        self.runner = runner
        self.cfg = cfg
        self._log_dir = Path(cfg.log_dir)
        # Borrow the current position (logical coordinates) that the
        # stage panel polls periodically
        self._get_stage_pos = get_stage_pos or (lambda: None)
        self.caller = AsyncCaller("scanaux", self)
        self._applied_defaults = None   # (sensor, defaults) already applied
        # Auto-filled date folder (remembered so that it can follow a
        # date change)
        self._auto_save_dir = cfg.dated_save_dir()
        self._build_ui(self._auto_save_dir)
        registry.subscribe(self.refresh_sensor)
        runner.busy_changed.connect(self._on_busy_changed)
        self.refresh_sensor()
        self._mode_changed()

    # ------------------------------------------------------------------
    def _build_ui(self, scope_save_dir: str):
        # Tabs: [Scan] [Data Retrieval] (reduces the vertical space needed)
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        scan_tab = QWidget()
        tabs.addTab(scan_tab, "Scan")
        layout = QVBoxLayout(scan_tab)

        # --- Mode selection ---
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("1D position scan", "1d")
        self.mode_combo.addItem("2D raster scan", "2d")
        self.mode_combo.addItem("1D + TUNE (energy scan at each point)",
                                "1d_tune")
        self.mode_combo.addItem("TUNE scan (fixed position)", "tune")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        self.defaults_btn = QPushButton("Sensor defaults")
        self.defaults_btn.setToolTip(
            "Reset the scan range to the current sensor's defaults")
        self.defaults_btn.clicked.connect(
            lambda: self._apply_scan_defaults(force=True))
        mode_row.addWidget(self.defaults_btn)
        layout.addLayout(mode_row)

        # Note on the logical-origin scheme (precondition of position scans)
        origin_note = ElidedLabel(
            "Coordinates are relative to the logical origin (frame edge "
            "X=0, reference electrode center Y=0, focus Z=0). "
            "Set the origin with [Alignment] → \"Find Origin\" in the "
            "stage panel.")
        origin_note.setStyleSheet("color: #546e7a;")
        layout.addWidget(origin_note)

        # Columns: 0=row label, 1=axis selector, 2=start, 3=stop, 4=step.
        # Start/Stop/Step are column heads instead of per-row labels, so
        # that 6-digit coordinates are not clipped even at a narrow
        # logical resolution (high-DPI monitors).
        grid = QGridLayout()
        for col, text in ((2, "Start"), (3, "Stop"), (4, "Step")):
            head = QLabel(text)
            head.setAlignment(Qt.AlignCenter)
            head.setStyleSheet("color: #546e7a;")
            grid.addWidget(head, 0, col)
            grid.setColumnStretch(col, 1)

        def dspin(lo, hi, val, step=1.0, suffix="", dec=1):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSingleStep(step)
            s.setSuffix(suffix)
            s.setDecimals(dec)
            s.valueChanged.connect(self._update_estimate)
            return s

        # --- Fast axis (moved at every point) ---
        grid.addWidget(QLabel("Fast [µm]:"), 1, 0)
        self.fast_axis = QComboBox()
        for label, idx in AXIS_ITEMS:
            self.fast_axis.addItem(label, idx)
        self.fast_axis.setCurrentIndex(1)  # Y = across the strips
        self.fast_axis.currentIndexChanged.connect(self._update_estimate)
        grid.addWidget(self.fast_axis, 1, 1)
        self.fast_start = dspin(-1e6, 1e6, 0.0)
        self.fast_stop = dspin(-1e6, 1e6, 0.0)
        self.fast_step = dspin(0.1, 1e5, 10.0)
        grid.addWidget(self.fast_start, 1, 2)
        grid.addWidget(self.fast_stop, 1, 3)
        grid.addWidget(self.fast_step, 1, 4)

        # --- Slow axis (row direction of 2D scans) ---
        self.slow_label = QLabel("Slow [µm]:")
        grid.addWidget(self.slow_label, 2, 0)
        self.slow_axis = QComboBox()
        for label, idx in AXIS_ITEMS:
            self.slow_axis.addItem(label, idx)
        # Default: fast Y (across) × slow X (along)
        self.slow_axis.setCurrentIndex(0)
        self.slow_axis.currentIndexChanged.connect(self._update_estimate)
        grid.addWidget(self.slow_axis, 2, 1)
        self.slow_start = dspin(-1e6, 1e6, 0.0)
        self.slow_stop = dspin(-1e6, 1e6, 0.0)
        self.slow_step = dspin(0.1, 1e5, 500.0)
        grid.addWidget(self.slow_start, 2, 2)
        grid.addWidget(self.slow_stop, 2, 3)
        grid.addWidget(self.slow_step, 2, 4)
        self.snake_check = QCheckBox("Snake (bidirectional)")
        self.snake_check.setChecked(True)
        grid.addWidget(self.snake_check, 3, 1, 1, 2)

        # --- TUNE scan (fixed-position energy scan: 50–90%) ---
        self.tune_label = QLabel("TUNE [%]:")
        grid.addWidget(self.tune_label, 4, 0)
        self.tune_start = dspin(0, 100, 50.0, 5.0)
        self.tune_stop = dspin(0, 100, 90.0, 5.0)
        self.tune_step = dspin(0.1, 100, 5.0, 1.0)
        grid.addWidget(self.tune_start, 4, 2)
        grid.addWidget(self.tune_stop, 4, 3)
        grid.addWidget(self.tune_step, 4, 4)
        layout.addLayout(grid)

        layout.addWidget(self._build_tilt_box())

        # --- Acquisition settings ---
        grid = QGridLayout()
        grid.addWidget(QLabel("Settle:"), 0, 0)
        self.settle_spin = dspin(0.0, 60.0, 0.5, 0.1, " s", 1)
        grid.addWidget(self.settle_spin, 0, 1)
        self.seq_check = QCheckBox("Sequence")
        self.seq_check.setChecked(True)
        self.seq_check.stateChanged.connect(self._update_estimate)
        grid.addWidget(self.seq_check, 0, 2)
        self.segments_spin = QSpinBox()
        self.segments_spin.setRange(2, 1000000)
        self.segments_spin.setValue(2000)   # 2000 events/step in the plan
        self.segments_spin.setToolTip(
            "Segments per point (= events per point)")
        self.segments_spin.valueChanged.connect(self._update_estimate)
        grid.addWidget(self.segments_spin, 0, 3)
        self.timeout_spin = dspin(1.0, 3600.0, 60.0, 10.0, " s", 0)
        self.timeout_spin.setToolTip(
            "Timeout: upper limit of the acquisition wait per point")
        timeout_label = QLabel("Timeout:")
        timeout_label.setToolTip(
            "Upper limit of the acquisition wait per point")
        grid.addWidget(timeout_label, 1, 0)
        grid.addWidget(self.timeout_spin, 1, 1)
        self.avg_off_check = QCheckBox("Averaging OFF")
        self.avg_off_check.setChecked(True)
        self.avg_off_check.setToolTip(
            "Turn C1–C4 averaging OFF (AverageSweeps=1) when the scan "
            "starts.\nWithout real-time waveforms the completion check of "
            "SINGLE/sequence acquisitions does not work,\nso normally "
            "leave this checked")
        grid.addWidget(self.avg_off_check, 1, 2, 1, 2)

        # --- Save settings (scope side) ---
        save_label = QLabel("Save dir:")
        save_label.setToolTip(
            "Base save directory on the scope.\n"
            "For every scan a scan_<stamp>_<mode> sub-folder is created "
            "below it automatically")
        grid.addWidget(save_label, 2, 0)
        self.save_dir_edit = QLineEdit(scope_save_dir)
        grid.addWidget(self.save_dir_edit, 2, 1, 1, 2)
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("prefix:"))
        self.prefix_edit = QLineEdit("scan")
        prefix_row.addWidget(self.prefix_edit)
        grid.addLayout(prefix_row, 2, 3)

        # --- Automatic laser control ---
        self.laser_on_check = QCheckBox("Laser ON at start")
        self.laser_off_check = QCheckBox("Laser OFF at end")
        self.laser_off_check.setChecked(True)
        grid.addWidget(self.laser_on_check, 3, 0, 1, 2)
        grid.addWidget(self.laser_off_check, 3, 2, 1, 2)
        layout.addLayout(grid)

        # --- Estimate / run (progress goes to the toolbar task display) ---
        self.estimate_label = ElidedLabel("---")
        self.estimate_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.estimate_label)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start Scan")
        self.start_btn.setStyleSheet(
            "background: #1565c0; color: white; font-weight: bold; padding: 6px;")
        self.start_btn.clicked.connect(self._start_scan)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setToolTip("Abort the running task "
                                  "(same as \"Abort\" in the toolbar)")
        self.abort_btn.setStyleSheet(
            "QPushButton { background: #b71c1c; color: white;"
            " font-weight: bold; padding: 6px; }"
            "QPushButton:disabled { background: #cfd8dc; color: #90a4ae; }")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.runner.abort)
        btn_row.addWidget(self.start_btn, 3)
        btn_row.addWidget(self.abort_btn, 1)
        layout.addLayout(btn_row)
        layout.addStretch(1)
        tabs.addTab(self._build_transfer_tab(), "Data Retrieval")

    def _build_tilt_box(self) -> QGroupBox:
        """Settings UI of the sensor tilt correction (rotation tracking
        Y / Z tracking)."""
        box = QGroupBox("Tilt correction")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        z_label = QLabel("Z tracking:")
        z_label.setToolTip(Z_MODE_TIPS)
        row.addWidget(z_label)
        self.z_mode_combo = QComboBox()
        for label, key in Z_MODES:
            self.z_mode_combo.addItem(label, key)
        self.z_mode_combo.setToolTip(Z_MODE_TIPS)
        self.z_mode_combo.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.z_mode_combo, 1)
        self.rot_check = QCheckBox("Rot. tracking (Y)")
        self.rot_check.setChecked(True)
        self.rot_check.setToolTip(
            "Shift Y relative to the X scan start to follow the strips'\n"
            "in-plane (XY) rotation (dY/dX). With this ON the strip appears\n"
            "at the same scan Y coordinate in every X row (no correction\n"
            "when X is not scanned)")
        self.rot_check.stateChanged.connect(self._update_estimate)
        row.addWidget(self.rot_check)
        v.addLayout(row)

        # Coefficients currently in effect (updated on every sensor
        # switch, Full Calibration and manual entry)
        self.coef_label = QLabel("")
        self.coef_label.setWordWrap(True)
        self.coef_label.setStyleSheet("color: #546e7a;")
        v.addWidget(self.coef_label)

        # Inputs of the measured-Z method: start / end points (1D) /
        # four corners (2D)
        self.z_points_widget = QWidget()
        pg = QGridLayout(self.z_points_widget)
        pg.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Go to each position → focus (Z jog / "
                      "Z Focus) → capture with \"Get Z\"")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #546e7a;")
        pg.addWidget(hint, 0, 0, 1, 4)
        self.z_point_rows = []
        for i in range(4):
            label = ElidedLabel("")
            spin = QDoubleSpinBox()
            spin.setRange(-1e5, 1e5)
            spin.setDecimals(1)
            spin.setSingleStep(2.0)
            spin.setSuffix(" µm")
            spin.setToolTip("Focus Z at this position (logical coordinates)")
            spin.valueChanged.connect(self._update_estimate)
            go = QPushButton("Go")
            go.setToolTip("Move XY only to this position (Z stays as is)")
            go.clicked.connect(lambda _=False, k=i: self._goto_z_point(k))
            grab = QPushButton("Get Z")
            grab.setToolTip("Capture the stage's current Z")
            grab.clicked.connect(lambda _=False, k=i: self._grab_z_point(k))
            pg.addWidget(label, i + 1, 0)
            pg.addWidget(spin, i + 1, 1)
            pg.addWidget(go, i + 1, 2)
            pg.addWidget(grab, i + 1, 3)
            self.z_point_rows.append((label, spin, go, grab))
        pg.setColumnStretch(0, 1)
        v.addWidget(self.z_points_widget)
        return box

    def _build_transfer_tab(self) -> QWidget:
        """Control UI of the Data Retrieval (scope → PC; SSH/SFTP, like
        scp).

        Retrieves the whole folder <scope_save_dir>\\<year>\\<YYYYMMDD>
        of the given date into <destination>\\<year>\\<YYYYMMDD> on the
        PC (devices/scope_scp.py). Already-retrieved files/folders are
        skipped automatically. The password is entered for every
        transfer (not stored in the settings file). The progress display
        is shared with scans.
        """
        box = QWidget()
        grid = QGridLayout(box)
        user = getattr(self.cfg, "scope_smb_user", "<SCOPE_USER>")
        host = host_from_resource(self.scope.resource)
        src = QLabel(f"Source: {user}@{host}:"
                     f"{self.cfg.scope_save_dir}\\<year>\\<date> "
                     "(password is asked at transfer)")
        src.setStyleSheet("color: #546e7a;")
        src.setWordWrap(True)
        grid.addWidget(src, 0, 0, 1, 3)
        grid.addWidget(QLabel("Date (YYYYMMDD):"), 1, 0)
        self.transfer_date_edit = QLineEdit(
            datetime.date.today().strftime("%Y%m%d"))
        self.transfer_date_edit.setToolTip(
            "Date folder to retrieve. Default is today; past dates work too")
        grid.addWidget(self.transfer_date_edit, 1, 1, 1, 2)
        grid.addWidget(QLabel("PC destination:"), 2, 0)
        self.transfer_dest_edit = QLineEdit(
            str(Path(self.cfg.transfer_dest_dir).resolve()))
        self.transfer_dest_edit.setToolTip(
            "<year>\\<date>\\ is created below this "
            "(e.g. ...\\data\\2026\\20260907)")
        grid.addWidget(self.transfer_dest_edit, 2, 1, 1, 2)
        self.transfer_delete_check = QCheckBox(
            "Delete on scope after transfer")
        self.transfer_delete_check.setChecked(False)
        self.transfer_delete_check.setToolTip(
            "After retrieval, delete from the scope only the files whose "
            "size matches the PC copy\n(frees disk space). Performed only "
            "when the size check passes for all files:\nif even one file "
            "fails / conflicts or the transfer is aborted, nothing is "
            "deleted.\nDefault OFF: copy only (duplicate folders are "
            "skipped automatically next time)")
        grid.addWidget(self.transfer_delete_check, 3, 0, 1, 2)
        self.transfer_btn = QPushButton("Start Transfer")
        self.transfer_btn.clicked.connect(self._start_transfer)
        grid.addWidget(self.transfer_btn, 3, 2)
        grid.setRowStretch(4, 1)
        return box

    # ------------------------------------------------------------------
    # Sensor switching / state updates
    # ------------------------------------------------------------------
    def refresh_sensor(self):
        """Reflect sensor / coefficient changes in the display
        (notification from the registry)."""
        self.coef_label.setText(f"Coefficients: {self.registry.summary()}")
        self._apply_scan_defaults(force=False)
        self._update_estimate()

    def _apply_scan_defaults(self, force: bool):
        """Fill the inputs with the current sensor's default scan range.

        Overwriting on every notification would wipe manual entries, so
        this is applied only when the sensor changed (or the default
        range itself was edited).
        """
        prof = self.registry.current()
        key = (prof["id"], tuple(sorted(prof["scan"].items())))
        if not force and key == self._applied_defaults:
            return
        self._applied_defaults = key
        sc = prof["scan"]
        for spin, name in ((self.fast_start, "fast_start"),
                           (self.fast_stop, "fast_stop"),
                           (self.fast_step, "fast_step"),
                           (self.slow_start, "slow_start"),
                           (self.slow_stop, "slow_stop"),
                           (self.slow_step, "slow_step")):
            spin.setValue(float(sc[name]))
        self.fast_axis.setCurrentIndex(1)
        self.slow_axis.setCurrentIndex(0)
        if self.mode_combo.currentData() == "1d_tune":
            self.fast_step.setValue(50.0)

    def _on_busy_changed(self, busy: bool):
        self.start_btn.setEnabled(not busy)
        self.transfer_btn.setEnabled(not busy)
        self.abort_btn.setEnabled(busy)
        for _label, _spin, go, _grab in self.z_point_rows:
            go.setEnabled(not busy)

    def _mode_changed(self):
        """Enable only the input rows relevant to the selected mode."""
        mode = self.mode_combo.currentData()
        pos_scan = mode in ("1d", "2d", "1d_tune")
        for w in (self.fast_axis, self.fast_start, self.fast_stop,
                  self.fast_step, self.z_mode_combo, self.rot_check):
            w.setEnabled(pos_scan)
        for w in (self.slow_axis, self.slow_start, self.slow_stop,
                  self.slow_step, self.snake_check):
            w.setEnabled(mode == "2d")
        for w in (self.tune_start, self.tune_stop, self.tune_step):
            w.setEnabled(mode in ("tune", "1d_tune"))
        # 1d_tune runs an energy scan at each position, so a wider
        # position step (50 µm) is the standard. Switch automatically if
        # the value is still the default (manual entries are respected)
        default_step = float(self.registry.current()["scan"]["fast_step"])
        if mode == "1d_tune" and self.fast_step.value() == default_step:
            self.fast_step.setValue(50.0)
        elif mode in ("1d", "2d") and self.fast_step.value() == 50.0:
            self.fast_step.setValue(default_step)
        # Measured-Z inputs: 2 rows for 1D, 4 rows for 2D
        points_mode = pos_scan and self.z_mode_combo.currentData() == "points"
        self.z_points_widget.setVisible(points_mode)
        n_rows = 4 if mode == "2d" else 2
        for i, row in enumerate(self.z_point_rows):
            for w in row:
                w.setVisible(i < n_rows)
        self._update_estimate()

    def _collect_params(self) -> dict:
        mode = self.mode_combo.currentData()
        dzdx, dzdy, dydx, _src = self.registry.coefficients()
        n_pts = 4 if mode == "2d" else 2
        return {
            "mode": mode,
            "sensor_id": self.registry.current_id,
            "sensor_type": str(self.registry.current().get("sensor_type", "strip")),
            "fast_axis": self.fast_axis.currentData(),
            "fast_start": self.fast_start.value(),
            "fast_stop": self.fast_stop.value(),
            "fast_step": self.fast_step.value(),
            "slow_axis": self.slow_axis.currentData(),
            "slow_start": self.slow_start.value(),
            "slow_stop": self.slow_stop.value(),
            "slow_step": self.slow_step.value(),
            "snake": self.snake_check.isChecked(),
            # Sensor tilt correction (coefficients are the current
            # sensor's Full Calibration / manually entered values)
            "z_mode": self.z_mode_combo.currentData(),
            "z_points": [row[1].value()
                         for row in self.z_point_rows[:n_pts]],
            "rot_correct": self.rot_check.isChecked(),
            "tilt_dzdx": dzdx, "tilt_dzdy": dzdy, "rot_dydx": dydx,
            "tune_start": self.tune_start.value(),
            "tune_stop": self.tune_stop.value(),
            "tune_step": self.tune_step.value(),
            "settle_s": self.settle_spin.value(),
            "point_timeout_s": self.timeout_spin.value(),
            "use_sequence": self.seq_check.isChecked(),
            "segments": self.segments_spin.value(),
            "avg_off": self.avg_off_check.isChecked(),
            "save_dir": self.save_dir_edit.text().strip(),
            "prefix": self.prefix_edit.text().strip() or "scan",
            "laser_on": self.laser_on_check.isChecked(),
            "laser_off": self.laser_off_check.isChecked(),
        }

    def _update_estimate(self):
        params = self._collect_params()
        self._update_z_point_labels(params)
        try:
            n = count_points(params)
        except Exception as exc:
            self.estimate_label.setText(f"Parameter error: {exc}")
            return
        if self.seq_check.isChecked():
            events = n * self.segments_spin.value()
            self.estimate_label.setText(
                f"Points: {n:,} × {self.segments_spin.value():,} seg "
                f"= ~{events:,} events")
        else:
            self.estimate_label.setText(f"Points: {n:,}")

    # ------------------------------------------------------------------
    # Measured-Z method (start / end points or four corners)
    # ------------------------------------------------------------------
    def _corners(self, params: dict) -> list[list[float]]:
        """Measurement positions of the measured Z (logical coordinates).
        Relative to (0,0,0) if the current position is unknown."""
        base = self._get_stage_pos() or [0.0, 0.0, 0.0]
        try:
            return scan_corners(params, base)
        except Exception:
            return []

    def _update_z_point_labels(self, params: dict):
        if not hasattr(self, "z_point_rows"):
            return
        names = ["Start", "End"] if params["mode"] != "2d" else \
            ["Start", "Row end", "Last-row start", "Last-row end"]
        corners = self._corners(params)
        scanned = {params["fast_axis"]}
        if params["mode"] == "2d":
            scanned.add(params["slow_axis"])
        for i, (label, _spin, _go, _grab) in enumerate(self.z_point_rows):
            if i < len(corners) and i < len(names):
                coords = " ".join(f"{'XYZ'[ax]}={corners[i][ax]:.0f}"
                                  for ax in sorted(scanned) if ax < 2)
                label.setText(f"{names[i]} ({coords})")
            else:
                label.setText("")

    def _goto_z_point(self, index: int):
        if self.runner.is_busy() or not self.stage.connected:
            return
        cur = self._get_stage_pos()
        if cur is None:
            QMessageBox.warning(self, "Cannot Move",
                                "The stage position is not available")
            return
        corners = self._corners(self._collect_params())
        if index >= len(corners):
            return
        x, y = corners[index][0], corners[index][1]

        def move():
            # Z stays as is (focus at the target and then capture it).
            # The polled display value may be stale, so read it again
            pos, _busy = self.stage.get_position_um()
            self.stage.move_to_um_relative(x, y, pos[2])
        self.caller.call(
            move,
            on_error=lambda exc: QMessageBox.warning(
                self, "Stage Error", str(exc)))

    def _grab_z_point(self, index: int):
        if not self.stage.connected:
            QMessageBox.warning(self, "Cannot Get Z",
                                "Stage is not connected")
            return
        # Capture a freshly read Z, not the polled display value
        self.caller.call(
            self.stage.get_position_um,
            on_result=lambda res: self.z_point_rows[index][1].setValue(
                res[0][2]),
            on_error=lambda exc: QMessageBox.warning(
                self, "Stage Error", str(exc)))

    # ------------------------------------------------------------------
    # Data Retrieval (scope → PC)
    # ------------------------------------------------------------------
    def _start_transfer(self):
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "Cannot Start Transfer",
                f"\"{self.runner.current_name()}\" is running")
            return
        dest = self.transfer_dest_edit.text().strip()
        if not dest:
            QMessageBox.warning(self, "Cannot Start Transfer",
                                "PC destination is empty")
            return
        date_str = self.transfer_date_edit.text().strip()
        if not (len(date_str) == 8 and date_str.isdigit()):
            QMessageBox.warning(self, "Cannot Start Transfer",
                                "Enter the date in YYYYMMDD format "
                                f"(entered: {date_str!r})")
            return
        host = host_from_resource(self.scope.resource)
        user = getattr(self.cfg, "scope_smb_user", "<SCOPE_USER>")
        delete_after = self.transfer_delete_check.isChecked()
        year = date_str[:4]

        # The password is not stored and is entered every time (the
        # previous value is prefilled within the session)
        password, ok = QInputDialog.getText(
            self, "Scope SSH Password",
            f"Password for {user}@{host}:",
            QLineEdit.Password, getattr(self, "_ssh_password", ""))
        if not ok:
            return
        if not password:
            QMessageBox.warning(self, "Cannot Start Transfer",
                                "Password is empty")
            return
        self._ssh_password = password  # in memory only (never saved)

        lines = [
            f"Source: {user}@{host}:"
            f"{self.cfg.scope_save_dir}\\{year}\\{date_str}",
            f"Destination: {Path(dest).resolve()}\\{year}\\{date_str}",
            "Already-retrieved files/folders are skipped automatically.",
        ]
        if delete_after:
            lines.append("★ The scope-side originals are deleted only if "
                         "the size check passes for all files ★")
        else:
            lines.append("Scope-side files are kept (copy only).")
        lines.append("Make sure the scope's autosave is not running.")
        ret = QMessageBox.question(
            self, "Confirm Data Retrieval", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        fetcher = ScpFetcher(
            host, user, password, self.cfg.scope_save_dir, dest)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = self._log_dir / f"transfer_{stamp}.csv"
        worker = TransferWorker(fetcher, date_str, delete_after, csv_path)
        self.runner.start("Data Retrieval", worker, worker.finished_transfer,
                          self._finished_dialog("Data Retrieval"))

    def _finished_dialog(self, title: str):
        """Build the callback that shows the result dialog when a task
        ends."""
        def done(ok: bool, message: str, _worker):
            if ok:
                QMessageBox.information(self, f"{title} Complete", message)
            else:
                QMessageBox.warning(self, f"{title} Finished", message)
        return done

    # ------------------------------------------------------------------
    def _start_scan(self):
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "Cannot start scan",
                f"\"{self.runner.current_name()}\" is running")
            return
        # If the save dir is still the date folder auto-filled at
        # start-up, make it follow today's folder across a date change
        # (prevents data from landing in the previous day's folder)
        today_dir = self.cfg.dated_save_dir()
        if self.save_dir_edit.text().strip() == self._auto_save_dir \
                and today_dir != self._auto_save_dir:
            self.save_dir_edit.setText(today_dir)
        self._auto_save_dir = today_dir
        params = self._collect_params()

        # --- Precondition checks ---
        if not self.scope.connected:
            QMessageBox.warning(self, "Cannot start scan",
                                "Oscilloscope is not connected")
            return
        mode = params["mode"]
        if mode in ("1d", "2d", "1d_tune") and not self.stage.connected:
            QMessageBox.warning(self, "Cannot start scan",
                                "Stage is not connected")
            return
        if mode in ("tune", "1d_tune") and not self.laser.connected:
            QMessageBox.warning(self, "Cannot start scan",
                                "TUNE scans require the laser connection")
            return
        if (params["laser_on"] or params["laser_off"]) \
                and not self.laser.connected \
                and mode not in ("tune", "1d_tune"):
            QMessageBox.warning(self, "Cannot start scan",
                                "Automatic laser ON/OFF requires the laser "
                                "connection")
            return
        try:
            n = count_points(params)
        except Exception as exc:
            QMessageBox.warning(self, "Parameter Error", str(exc))
            return

        # --- Save dir: auto-create a per-scan sub-folder below the base ---
        # e.g. ...\data\2026\20260705\scan_20260705_143022_1dY
        # (the folder name tells when and in which mode the data was
        #  taken. The PC-side CSV has the same time stamp, so the two
        #  can be matched)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if mode == "2d":
            tag = (f"2d{'XYZ'[params['fast_axis']]}"
                   f"{'XYZ'[params['slow_axis']]}")
        elif mode == "1d":
            tag = f"1d{'XYZ'[params['fast_axis']]}"
        elif mode == "1d_tune":
            tag = f"1d{'XYZ'[params['fast_axis']]}tune"
        else:
            tag = "tune"
        base_dir = params["save_dir"].rstrip("\\/")
        params["save_dir"] = f"{base_dir}\\scan_{stamp}_{tag}"

        # --- Confirmation dialog (states the travel range and the
        #     number of points to prevent operating mistakes) ---
        lines = [f"Sensor: {self.registry.current()['name']}",
                 f"Points: {n:,}"]
        if mode in ("1d", "2d", "1d_tune"):
            fa = "XYZ"[params["fast_axis"]]
            lines.append(
                f"Fast axis {fa}: {params['fast_start']:.1f} → "
                f"{params['fast_stop']:.1f} µm (step {params['fast_step']:.1f})")
        if mode == "2d":
            sa = "XYZ"[params["slow_axis"]]
            lines.append(
                f"Slow axis {sa}: {params['slow_start']:.1f} → "
                f"{params['slow_stop']:.1f} µm (step {params['slow_step']:.1f})")
        if mode in ("1d", "2d", "1d_tune"):
            # Show the actual range of the corrections from the point
            # list after correction. Relative to the current position at
            # start (same calculation as the scan itself)
            base = self._get_stage_pos() or [0.0, 0.0, 0.0]
            pts = build_points(params, base)
            z_mode = params["z_mode"] if 2 not in (
                params["fast_axis"],
                params["slow_axis"] if mode == "2d" else -1) else "off"
            zs = [pt["pos"][2] for pt in pts]
            z_desc = {key: label for label, key in Z_MODES}[z_mode]
            if z_mode == "off":
                lines.append("Z tracking: off (Z stays at the current "
                             "position)")
            else:
                lines.append(
                    f"Z tracking [{z_desc}]: Z = {min(zs):+.1f} to "
                    f"{max(zs):+.1f} µm (logical coordinates, 2 µm steps)")
                if abs(zs[0] - base[2]) > 30.0:
                    lines.append(
                        f"★ The Z of the first point ({zs[0]:+.1f}) is more "
                        f"than 30 µm away from the current Z "
                        f"({base[2]:+.1f}). "
                        "Check that the origin / measured Z are correct ★")
            if z_mode == "coef_origin":
                lines.append("* Assumes the focus is correct at the logical "
                             "origin (0,0,0) (the state right after "
                             "Find Origin)")
            elif z_mode == "coef_current":
                lines.append("* Make sure the focus is correct at the "
                             "current position (the scan start point)")
            if params["rot_correct"]:
                plain = build_points({**params, "rot_correct": False}, base)
                shifts = [a["pos"][1] - b["pos"][1]
                          for a, b in zip(pts, plain)]
                lines.append(
                    f"Rotation tracking: Y shift {min(shifts):+.1f} to "
                    f"{max(shifts):+.1f} µm")
            lines.append(f"Tilt coefficients: {self.registry.summary()}")
        if mode in ("tune", "1d_tune"):
            prefix = "TUNE at each position" if mode == "1d_tune" else "TUNE"
            lines.append(
                f"{prefix}: {params['tune_start']:.1f} → "
                f"{params['tune_stop']:.1f}% (step {params['tune_step']:.1f})")
            if mode == "1d_tune":
                lines.append("* TUNE will switch to manual mode. If the "
                             "frequency was changed, verify the operating "
                             "point with the TUNE optimization first")
        if params["use_sequence"]:
            lines.append(f"Sequence: {params['segments']:,} seg/point")
        if params["avg_off"]:
            lines.append("C1–C4 averaging will be turned OFF at start")
        lines.append(f"Save dir (on scope): {params['save_dir']}")
        if params["laser_on"]:
            lines.append("★ The laser will be switched ON automatically at "
                         "start ★")
        if mode in ("1d", "2d", "1d_tune"):
            lines.append("Coordinates are relative to the logical origin "
                         "(frame edge = 0,0,0), using relative moves. "
                         "Confirm Find Origin (stage panel) has been done.")
            lines.append("Sanity check: the beam radius is a few µm, so "
                         "directly above an electrode (metal) the signal "
                         "should drop to ~0. Verify this in the first row "
                         "of data (= good focus).")
        lines.append("Confirm the stage cannot collide with the objective "
                     "lens etc. within the travel range.")
        ret = QMessageBox.question(
            self, "Confirm Scan Start", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        # --- Run (the CSV shares the time stamp of the scope folder) ---
        csv_path = self._log_dir / f"scan_{stamp}.csv"
        worker = ScanWorker(self.scope, self.stage, self.laser,
                            params, csv_path)
        self.runner.start("Scan", worker, worker.finished_scan,
                          self._finished_dialog("Scan"))

    # ------------------------------------------------------------------
    def shutdown(self):
        self.caller.shutdown()
