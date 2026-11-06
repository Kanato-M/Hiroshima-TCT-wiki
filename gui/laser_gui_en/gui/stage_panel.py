# -*- coding: utf-8 -*-
"""SHRC-203 3-axis stage control panel.

Always visible: connection, current position (logical + machine
coordinates).
Tabs:
  [Move]       jog (laid out to match the camera image orientation),
               move to given coordinates, Mechanical Home / Reset Logical
               Origin, stop
  [Positions]  per-sensor dictionary of saved positions, move to the
               saved origin (machine coordinates), move to strip
               positions computed from the geometry
  [Alignment]  Z focus / Find Origin / Full Calibration (signal-based),
               display and manual entry of the tilt correction
               coefficients

Reorganization of 2026-10:
  - Jog buttons are laid out so that "the stage moves on the camera
    image in the direction of the arrow you press"
    (stage Y+ points down in the image; change via
    config.stage_screen_dir)
  - Saved positions moved from one file shared by all sensors to a
    per-sensor (DUT) dictionary (sensors.py). Every Find Origin run
    automatically saves the machine coordinates of the logical origin,
    so next time "Go to Origin" can be used for the coarse approach
  - The Find Origin / calibration controls moved here from the Auto
    Scan panel
  - Manual moves are disabled while a task (scan, calibration, ...) is
    running (prevents accidental jogging during a scan; the stop
    buttons stay enabled)
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

import sensors
from .alignment_worker import AlignmentWorker
from .autofocus_worker import AutofocusWorker
from .widgets import ElidedLabel
from .worker import AsyncCaller

# Travel per jog click, presets [µm]
# (Even steps so they map to whole pulses at 2 µm/pulse. 1 µm was removed
#  because it rounds to 0 pulses and does not move.)
JOG_STEPS_UM = [2, 4, 10, 20, 50, 100, 500, 1000, 5000]

STRIP_PLACES = [("Readout-side end", "near"), ("Middle", "mid"),
                ("Far end", "far")]


class StagePanel(QGroupBox):
    POLL_MS = 500  # position polling period

    def __init__(self, stage, scope, laser, registry, runner, cfg,
                 parent=None):
        super().__init__("3-Axis Stage (SHRC-203)", parent)
        self.stage = stage
        self.scope = scope
        self.laser = laser
        self.registry = registry
        self.runner = runner
        self.cfg = cfg
        self.caller = AsyncCaller("stage", self)
        self._log_dir = Path(cfg.log_dir)
        self._last_pos_um = [0.0, 0.0, 0.0]
        self._have_pos = False
        self._home_reminded = False
        self._manual_widgets: list[QWidget] = []   # disabled during a task
        self._build_ui()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_MS)
        self._poll_timer.timeout.connect(self._poll_position)
        self._poll_pending = False

        registry.subscribe(self.refresh_sensor)
        runner.busy_changed.connect(self._on_busy_changed)
        self.refresh_sensor()

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- connection row ---
        conn_row = QHBoxLayout()
        default_addr = (self.stage.serial_port if self.stage.use_serial
                        else f"{self.stage.host}:{self.stage.port}")
        self.addr_edit = QLineEdit(default_addr)
        self.addr_edit.setToolTip("LAN: host:port / serial: COM port name")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.conn_label = QLabel("Disconnected")
        self.conn_label.setStyleSheet("color: gray;")
        conn_row.addWidget(QLabel("Address:"))
        conn_row.addWidget(self.addr_edit, 1)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.conn_label)
        layout.addLayout(conn_row)

        # --- current position display (logical coordinates) ---
        pos_row = QHBoxLayout()
        self.pos_labels = []
        for name in ("X", "Y", "Z"):
            lbl = QLabel(f"{name}: --- µm")
            lbl.setStyleSheet(
                "font-family: monospace; font-size: 13px; font-weight: bold;"
                "background: #263238; color: #aed581; padding: 2px;"
                "border-radius: 4px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.pos_labels.append(lbl)
            pos_row.addWidget(lbl)
        self.busy_label = QLabel("---")
        self.busy_label.setAlignment(Qt.AlignCenter)
        pos_row.addWidget(self.busy_label)
        layout.addLayout(pos_row)

        # --- machine coordinates (H:W based; tracked across R:W too) ---
        self.machine_label = ElidedLabel("Machine coordinates: ---")
        self.machine_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.machine_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_move_tab(), "Move")
        self.tabs.addTab(self._build_position_tab(), "Positions")
        self.tabs.addTab(self._build_align_tab(), "Alignment")
        layout.addWidget(self.tabs)
        layout.addStretch(1)

        self._update_pulse_label()

    # ------------------------------------------------------------------
    # [Move] tab
    # ------------------------------------------------------------------
    def _build_move_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step:"))
        self.step_combo = QComboBox()
        for um in JOG_STEPS_UM:
            label = f"{um} µm" if um < 1000 else f"{um / 1000:g} mm"
            self.step_combo.addItem(label, float(um))
        self.step_combo.setCurrentIndex(2)  # 10 µm
        step_row.addWidget(self.step_combo)
        # shows the actual pulse count sent (conversion check)
        self.pulse_label = QLabel("")
        self.pulse_label.setStyleSheet("color: gray;")
        step_row.addWidget(self.pulse_label)
        step_row.addStretch(1)
        self.step_combo.currentIndexChanged.connect(self._update_pulse_label)
        layout.addLayout(step_row)

        def jog_btn(arrow, axis, sign):
            # Label = "arrow for the on-image direction + the axis and
            # sign actually sent"
            name = f"{'XYZ'[axis]}{'+' if sign > 0 else '−'}"
            b = QPushButton(f"{arrow} {name}")
            b.setFixedSize(64, 30)
            b.clicked.connect(lambda: self._jog(axis, sign))
            b.setAutoRepeat(True)          # hold for continuous jogging
            b.setAutoRepeatDelay(500)
            b.setAutoRepeatInterval(400)
            self._manual_widgets.append(b)
            return b

        # XY is a cross layout matching the camera image orientation: the
        # stage moves on the image in the direction of the pressed arrow.
        # The sign of the stage coordinates is fixed by how the hardware
        # is mounted; on this setup "Y+ points down in the image" (X is +
        # to the right). A layout following the coordinate signs (up = Y+)
        # made it easy to mix up up/down, so it is image-based instead.
        sx, sy = self.cfg.stage_screen_dir   # signs for image right / up
        jog = QGridLayout()
        jog.addWidget(jog_btn("▲", 1, sy), 0, 1)
        jog.addWidget(jog_btn("◀", 0, -sx), 1, 0)
        jog.addWidget(jog_btn("▶", 0, sx), 1, 2)
        jog.addWidget(jog_btn("▼", 1, -sy), 2, 1)
        center = QLabel("camera\nimage view")
        center.setAlignment(Qt.AlignCenter)
        center.setStyleSheet("color: #546e7a; font-size: 10px;")
        jog.addWidget(center, 1, 1)
        jog.setColumnMinimumWidth(3, 16)
        jog.addWidget(jog_btn("▲", 2, +1), 0, 4)
        jog.addWidget(jog_btn("▼", 2, -1), 2, 4)
        jog.addWidget(QLabel("Z (focus)"), 1, 4,
                      alignment=Qt.AlignCenter)
        jog.setColumnStretch(5, 1)
        layout.addLayout(jog)

        # --- move to given coordinates (logical coordinates) ---
        abs_row = QHBoxLayout()
        # No µm suffix on the spin boxes: 6-digit coordinates would get
        # clipped, so the unit lives in the label
        abs_row.addWidget(QLabel("Go to [µm]:"))
        self.abs_spins = []
        for name in ("X", "Y", "Z"):
            spin = QDoubleSpinBox()
            spin.setRange(-1e6, 1e6)
            spin.setDecimals(1)
            spin.setSingleStep(10)
            # No up/down buttons: prioritize display width for 6-digit
            # coordinates (fine adjustment is done by jogging)
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            spin.setToolTip(f"{name} target position [µm] "
                            "(logical coordinates)")
            self.abs_spins.append(spin)
            abs_row.addWidget(spin)
        # Short captions keep this row inside the column (the three
        # 6-digit fields need the width); details are in the tooltips
        go_btn = QPushButton("Go")
        go_btn.setToolTip("Move to the entered logical coordinates")
        go_btn.clicked.connect(self._move_absolute)
        self._manual_widgets.append(go_btn)
        abs_row.addWidget(go_btn)
        fill_btn = QPushButton("Now")
        fill_btn.setToolTip("Copy the current position into the fields")
        fill_btn.clicked.connect(self._fill_current)
        abs_row.addWidget(fill_btn)
        # Let the two short buttons shrink below the style's default
        # 80 px minimum so the row never dictates the column width
        go_btn.setMinimumWidth(56)
        fill_btn.setMinimumWidth(56)
        layout.addLayout(abs_row)

        # --- origin / stop ---
        # English labels are much wider than the Japanese ones: four
        # buttons in one row made this tab (and the whole column) wider
        # than the column and forced a horizontal scroll bar, so they are
        # laid out 2 × 2 (origin operations on top, stops below)
        ctrl_row = QGridLayout()
        home_btn = QPushButton("Mechanical Home (H:W)")
        home_btn.setToolTip(
            "Return all axes to the mechanical origin. Afterwards the\n"
            "displayed coordinates = machine coordinates, and the machine\n"
            "coordinates stay tracked across later Reset Logical Origin\n"
            "(R:W) operations")
        home_btn.clicked.connect(self._home)
        zero_btn = QPushButton("Reset Logical Origin (R:W)")
        zero_btn.setToolTip("Make the current position the logical origin "
                            "(0,0,0) (asks for confirmation)")
        zero_btn.clicked.connect(self._zero)
        self._manual_widgets += [home_btn, zero_btn]
        stop_btn = QPushButton("Stop (L:W)")
        stop_btn.clicked.connect(
            lambda: self.caller.call(self.stage.stop))
        estop_btn = QPushButton("EMERGENCY STOP")
        estop_btn.setStyleSheet(
            "background: #b71c1c; color: white; font-weight: bold;")
        estop_btn.clicked.connect(
            lambda: self.caller.call(self.stage.emergency_stop))
        ctrl_row.addWidget(home_btn, 0, 0)
        ctrl_row.addWidget(zero_btn, 0, 1)
        ctrl_row.addWidget(stop_btn, 1, 0)
        ctrl_row.addWidget(estop_btn, 1, 1)
        layout.addLayout(ctrl_row)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # [Positions] tab
    # ------------------------------------------------------------------
    def _build_position_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self.pos_sensor_label = ElidedLabel("")
        self.pos_sensor_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.pos_sensor_label)

        # --- saved origin (machine coordinates of the logical origin) ---
        self.origin_label = ElidedLabel("")
        self.origin_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.origin_label)
        origin_row = QHBoxLayout()
        goto_origin = QPushButton("Go to Origin")
        goto_origin.setToolTip(
            "Move to this sensor's saved origin (machine coordinates).\n"
            "Used for the coarse approach after Mechanical Home (H:W)")
        goto_origin.clicked.connect(lambda: self._goto_origin(False))
        goto_start = QPushButton("Go to Start")
        goto_start.setToolTip(
            "Move from the saved origin to the reference strip, 100 µm\n"
            "inside the readout-side end of the exposed strip region\n"
            "(= the Find Origin start position)")
        goto_start.clicked.connect(lambda: self._goto_origin(True))
        reg_origin = QPushButton("Save Origin")
        reg_origin.setToolTip(
            "Save the machine coordinates of the current logical origin\n"
            "(0,0,0) as this sensor's origin (running Find Origin saves\n"
            "it automatically)")
        reg_origin.clicked.connect(self._register_origin_manual)
        self._manual_widgets += [goto_origin, goto_start]
        origin_row.addWidget(goto_origin)
        origin_row.addWidget(goto_start)
        origin_row.addWidget(reg_origin)
        layout.addLayout(origin_row)

        # --- strip positions (computed each time from the geometry +
        #     tilt coefficients) ---
        strip_row = QHBoxLayout()
        self.strip_label = QLabel("Go to strip:")
        strip_row.addWidget(self.strip_label)
        self.strip_ch_combo = QComboBox()
        strip_row.addWidget(self.strip_ch_combo)
        self.strip_place_combo = QComboBox()
        for label, key in STRIP_PLACES:
            self.strip_place_combo.addItem(label, key)
        strip_row.addWidget(self.strip_place_combo)
        strip_btn = QPushButton("Go")
        strip_btn.setToolTip(
            "Move to the electrode of the selected CH (strip: on the electrode\n"
            "center line / pixel: beside the pad, pad center or the 4-pixel\n"
            "corner). The coordinates are computed from the sensor geometry\n"
            "and the current tilt coefficients (assumes the logical origin\n"
            "has been found)")
        strip_btn.clicked.connect(self._goto_strip)
        self._manual_widgets.append(strip_btn)
        strip_row.addWidget(strip_btn)
        strip_row.addStretch(1)
        layout.addLayout(strip_row)

        # --- dictionary of saved positions ---
        bm_row = QHBoxLayout()
        self.bm_list = QListWidget()
        self.bm_list.setMinimumHeight(60)
        self.bm_list.setMaximumHeight(130)
        self.bm_list.itemDoubleClicked.connect(
            lambda _item: self._goto_bookmark())
        bm_btns = QVBoxLayout()
        self.frame_combo = QComboBox()
        self.frame_combo.addItem("Save as logical", "logical")
        self.frame_combo.addItem("Save as machine", "machine")
        self.frame_combo.setToolTip(
            "Logical coordinates: relative to the sensor's logical origin.\n"
            "  Points at the same place on the sensor even after Find\n"
            "  Origin is redone (normally use this)\n"
            "Machine coordinates: relative to the mechanical origin. For\n"
            "  the coarse approach before the origin is found")
        add_btn = QPushButton("Add Current Position")
        add_btn.clicked.connect(self._add_bookmark)
        goto_btn = QPushButton("Go to Selected")
        goto_btn.clicked.connect(self._goto_bookmark)
        self._manual_widgets.append(goto_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_bookmark)
        bm_btns.addWidget(self.frame_combo)
        bm_btns.addWidget(add_btn)
        bm_btns.addWidget(goto_btn)
        bm_btns.addWidget(del_btn)
        bm_btns.addStretch(1)
        bm_row.addWidget(self.bm_list, 1)
        bm_row.addLayout(bm_btns)
        layout.addLayout(bm_row)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # [Alignment] tab
    # ------------------------------------------------------------------
    def _build_align_tab(self) -> QWidget:
        """Controls for signal-based Z focus, Find Origin, Full Calibration.

        All of them run with the laser ON and use the scope amplitude
        (PAVA) as the position sensor (gui/autofocus_worker.py,
        gui/alignment_worker.py).
        ★ The amplitude gets smaller as the focus improves (LGAD gain
        suppression, measured 2026-09-07), so Z focus means "minimum
        amplitude on silicon = center of the valley" ★
        """
        w = QWidget()
        layout = QVBoxLayout(w)

        grid = QGridLayout()
        grid.addWidget(QLabel("CH:"), 0, 0)
        self.af_ch = QComboBox()
        self.af_ch.setToolTip("Scope CH whose amplitude is read (Find "
                              "Origin runs on the reference CH strip)")
        grid.addWidget(self.af_ch, 0, 1)
        grid.addWidget(QLabel("Z range ±"), 0, 2)
        self.af_range = QDoubleSpinBox()
        self.af_range.setRange(4.0, 500.0)
        self.af_range.setValue(20.0)
        self.af_range.setSuffix(" µm")
        self.af_range.setDecimals(0)
        grid.addWidget(self.af_range, 0, 3)
        grid.addWidget(QLabel("step"), 0, 4)
        self.af_step = QDoubleSpinBox()
        self.af_step.setRange(2.0, 50.0)   # Z resolution 2 µm/pulse
        self.af_step.setValue(2.0)
        self.af_step.setSuffix(" µm")
        self.af_step.setDecimals(0)
        grid.addWidget(self.af_step, 0, 5)
        self.af_metric = QComboBox()
        self.af_metric.addItem("Min amplitude (focus)", "min")
        self.af_metric.addItem("Max amplitude (old)", "max")
        self.af_metric.setToolTip(
            "The amplitude gets smaller as the focus improves (LGAD gain\n"
            "suppression; established by the 2026-09-07 measurement). The\n"
            "default \"Min amplitude\" is run with the beam on the silicon\n"
            "beside a strip, where the signal is clearly visible\n"
            "(center of the shallow valley = focus). Directly on the\n"
            "electrode the amplitude stays 0 and no valley is visible, so\n"
            "that does not work. \"Max amplitude\" is the old method and\n"
            "does not land on the focus")
        grid.addWidget(self.af_metric, 1, 0, 1, 3)
        self.af_btn = QPushButton("Z Focus")
        self.af_btn.setToolTip(
            "Sweep Z at the current position and move to the Z where the\n"
            "amplitude is at its extremum. Run with the laser ON and the\n"
            "beam on the silicon beside a strip")
        self.af_btn.clicked.connect(self._start_autofocus)
        grid.addWidget(self.af_btn, 1, 3, 1, 3)
        layout.addLayout(grid)

        box = QGroupBox("Auto Alignment (signal-based)")
        box.setToolTip(
            "Preparation: put the beam on the reference CH strip,\n"
            "50–200 µm inside the readout-side end of the exposed strip\n"
            "region\n"
            "(\"Go to Start\" can do the coarse approach).\n"
            "Run with the laser ON, trigger EXT and the signal visible")
        bgrid = QGridLayout(box)
        self.align_laser_check = QCheckBox("Laser ON at start")
        bgrid.addWidget(self.align_laser_check, 0, 0)
        self.align_premove_check = QCheckBox("Pre-move to start")
        self.align_premove_check.setToolTip(
            "ON: before starting, move (relative to the current logical\n"
            "origin) onto the reference strip, 100 µm inside the\n"
            "readout-side end of the exposed strip region, then start.\n"
            "Use this to continue with Full Calibration or to redo Find\n"
            "Origin from an already-aligned state (origin = frame edge,\n"
            "no signal there).\n"
            "OFF: start from the current position (right after a coarse\n"
            "approach with the camera etc.)")
        bgrid.addWidget(self.align_premove_check, 0, 1)
        self.align_fine_check = QCheckBox(
            "Fine finishing + verification (recommended, 2–3× longer)")
        self.align_fine_check.setChecked(True)
        self.align_fine_check.setToolTip(
            "ON (recommended): after the coarse search, fine sweeps in\n"
            "one-pulse (2 µm) steps with many reads (median) determine the\n"
            "electrode center Y and the signal edge X by erf fits and the\n"
            "focus Z by a Lorentzian fit to sub-µm precision. A reverse\n"
            "sweep estimates the backlash, and after R:W the origin is\n"
            "re-measured; a residual of one pulse or more on any axis is\n"
            "corrected and R:W is redone (at most twice). The sweeps are\n"
            "logged to logs/alignment_<stamp>.csv\n"
            "Takes: Find Origin about 7–12 min, Full Calibration about\n"
            "15–25 min\n"
            "OFF: coarse search only, as before (Find Origin 2–3 min, Full\n"
            "Calibration 8–12 min)")
        bgrid.addWidget(self.align_fine_check, 1, 0, 1, 2)
        self.align_origin_btn = QPushButton("Find Origin (Z→Y→X→R:W)")
        self.align_origin_btn.setToolTip(
            "① Y sweep to locate the electrode ② Z of minimum amplitude\n"
            "(= focus) on the silicon beside the electrode ③ fine Y sweep\n"
            "for the electrode center ④ X sweep to detect the signal edge\n"
            "⑤ Z re-check → Reset Logical Origin (R:W) at the equivalent\n"
            "of the frame edge ⑥ (fine finishing + verification ON)\n"
            "re-measure the origin after R:W and correct residuals of one\n"
            "pulse or more.\n"
            "On completion the machine coordinates of the logical origin\n"
            "are saved to this sensor automatically.\n"
            "About 2–3 min (about 7–12 min with fine finishing + "
            "verification)")
        self.align_origin_btn.clicked.connect(
            lambda: self._start_alignment("origin"))
        bgrid.addWidget(self.align_origin_btn, 2, 0)
        self.align_full_btn = QPushButton("Full Calibration (origin+tilt)")
        self.align_full_btn.setToolTip(
            "After Find Origin, measure the electrode center and focus at\n"
            "the X/Y points below, fit dY/dX, dZ/dX and dZ/dY (tilt\n"
            "coefficients) and save them to this sensor.\n"
            "About 8–12 min (about 15–25 min with fine finishing +\n"
            "verification). Run after remounting the sensor or changing\n"
            "the jig")
        self.align_full_btn.clicked.connect(
            lambda: self._start_alignment("full"))
        bgrid.addWidget(self.align_full_btn, 2, 1)
        self.align_points_label = QLabel("")
        self.align_points_label.setStyleSheet("color: #546e7a;")
        self.align_points_label.setWordWrap(True)
        bgrid.addWidget(self.align_points_label, 3, 0, 1, 2)
        layout.addWidget(box)

        coef_row = QHBoxLayout()
        self.calib_label = QLabel("")
        self.calib_label.setStyleSheet("color: #546e7a;")
        self.calib_label.setWordWrap(True)
        coef_row.addWidget(self.calib_label, 1)
        self.coef_btn = QPushButton("Edit coefficients...")
        self.coef_btn.setToolTip(
            "Enter the tilt correction coefficients (dZ/dX, dZ/dY, dY/dX)\n"
            "by hand and save them")
        self.coef_btn.clicked.connect(self._edit_coefficients)
        coef_row.addWidget(self.coef_btn)
        layout.addLayout(coef_row)
        layout.addStretch(1)
        self._task_buttons = [self.af_btn, self.align_origin_btn,
                              self.align_full_btn, self.coef_btn]
        return w

    # ------------------------------------------------------------------
    # Sensor switching / state display
    # ------------------------------------------------------------------
    def refresh_sensor(self):
        """Update the display to match the current sensor profile."""
        prof = self.registry.current()
        chans = sensors.used_channels(prof)
        pixel = sensors.is_pixel(prof)
        self.pos_sensor_label.setText(
            f"Sensor: {prof['name']}" + (" [pixel]" if pixel else ""))
        # the "Go to electrode" choices depend on the sensor type
        self.strip_label.setText("Go to pixel:" if pixel else "Go to strip:")
        keep_place = self.strip_place_combo.currentData()
        self.strip_place_combo.blockSignals(True)
        self.strip_place_combo.clear()
        for label, key in self.registry.places():
            self.strip_place_combo.addItem(label, key)
        idx = self.strip_place_combo.findData(keep_place)
        self.strip_place_combo.setCurrentIndex(max(idx, 0))
        self.strip_place_combo.blockSignals(False)

        for combo in (self.af_ch, self.strip_ch_combo):
            keep = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            for ch in chans:
                pad = prof["readout"][ch].get("pad", "")
                combo.addItem(f"{ch} ({pad})" if pad else ch, ch)
            idx = combo.findData(keep.split()[0] if keep else "")
            combo.setCurrentIndex(max(idx, 0))
            combo.blockSignals(False)

        org = prof.get("machine_origin")
        if org:
            x, y, z = org["pos_um"]
            self.origin_label.setText(
                "Saved origin (machine): "
                f"X={x:.0f} Y={y:.0f} Z={z:.0f} µm — "
                f"{str(org.get('registered_at', ''))[:16]} "
                f"{org.get('method', '')}")
        else:
            self.origin_label.setText(
                "Saved origin: none (saved automatically by Find Origin)")

        self.bm_list.clear()
        for name, entry in self.registry.positions().items():
            x, y, z = entry["pos_um"]
            tag = "logical" if entry.get("frame") == "logical" else "machine"
            item = QListWidgetItem(
                f"{name}  [{tag}] X={x:.1f} Y={y:.1f} Z={z:.1f}")
            item.setData(Qt.UserRole, name)
            self.bm_list.addItem(item)

        al = prof["align"]
        if pixel:
            pads = ", ".join(f"{ch} ({x:.0f}, {y:.0f})" for ch, x, y in al.get("pads", []))
            self.align_points_label.setText(
                f"Full Calibration points (pad centers) — {pads or 'none'} µm "
                "(change in the sensor editor)")
        else:
            xs = ", ".join(f"{x:.0f}" for x in al.get("x_points", []))
            ys = ", ".join(f"{y:.0f} ({ch})" for y, ch in al.get("y_points", []))
            self.align_points_label.setText(
                f"Full Calibration points — X: {xs or 'none'} µm / "
                f"Y: {ys or 'none'} µm (change in the sensor editor)")
        self.calib_label.setText(
            f"Tilt coefficients: {self.registry.summary()}")

    def _on_busy_changed(self, busy: bool):
        for w in self._manual_widgets + self._task_buttons:
            w.setEnabled(not busy)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.stage.connected:
            self._poll_timer.stop()
            self.caller.call(self.stage.disconnect,
                             on_result=lambda _: self._on_disconnected())
            return
        addr = self.addr_edit.text().strip()
        if ":" in addr and not addr.upper().startswith("COM") \
                and not addr.startswith("/dev/"):
            host, _, port = addr.partition(":")
            self.stage.use_serial = False
            self.stage.host = host
            try:
                self.stage.port = int(port) if port else 9004
            except ValueError:
                QMessageBox.warning(self, "Input Error",
                                    f"Invalid port number: {port!r}")
                return
        else:
            self.stage.use_serial = True
            self.stage.serial_port = addr
        self.connect_btn.setEnabled(False)
        self.caller.call(self.stage.connect,
                         on_result=lambda _: self._on_connected(),
                         on_error=self._on_conn_error)

    def _on_connected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect")
        self.conn_label.setText("Connected")
        self.conn_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        # Apply the speed profile (the controller default is slow, making
        # the long move from home to the sensor position too sluggish.
        # Trapezoidal drive decelerates automatically near the target, so
        # stop accuracy is unchanged.)
        if self.cfg.stage_speed_pps is not None:
            s, f, r = self.cfg.stage_speed_pps
            self.caller.call(lambda: self.stage.set_speed(s, f, r),
                             on_error=self._show_error)
        self._poll_timer.start()
        # Homing is mandatory right after power-up (required by the
        # spec). Prompt for it only once per session
        if not self._home_reminded:
            self._home_reminded = True
            QMessageBox.information(
                self, "Homing Reminder",
                "The SHRC-203 requires mechanical homing right after "
                "power-up.\n"
                "If not done yet, run \"Mechanical Home (H:W)\".\n"
                "(Running it from the GUI also starts machine-coordinate\n"
                " tracking, which enables moving to the saved origin and\n"
                " saving the origin automatically.)")

    def _on_disconnected(self):
        self.connect_btn.setText("Connect")
        self.conn_label.setText("Disconnected")
        self.conn_label.setStyleSheet("color: gray;")
        self._have_pos = False

    def _on_conn_error(self, exc: Exception):
        self.connect_btn.setEnabled(True)
        QMessageBox.warning(self, "Stage Connection Error", str(exc))

    # ------------------------------------------------------------------
    # Position polling
    # ------------------------------------------------------------------
    def _poll_position(self):
        if self._poll_pending or not self.stage.connected:
            return
        self._poll_pending = True
        self.caller.call(self.stage.get_position_um,
                         on_result=self._update_position,
                         on_error=lambda e: setattr(self, "_poll_pending", False))

    def _update_position(self, result):
        self._poll_pending = False
        pos_um, busy = result
        self._last_pos_um = pos_um
        self._have_pos = True
        for lbl, name, um in zip(self.pos_labels, "XYZ", pos_um):
            lbl.setText(f"{name}: {um:,.1f} µm")
        mpos = self.stage.machine_from_logical(pos_um)
        if mpos is None:
            self.machine_label.setText(
                "Machine coordinates: unknown (run Mechanical Home (H:W) "
                "from the GUI to start tracking)")
        else:
            self.machine_label.setText(
                "Machine coordinates: X={:,.0f} Y={:,.0f} Z={:,.0f} µm "
                "(from mechanical origin)".format(*mpos))
        if busy:
            self.busy_label.setText("Moving")
            self.busy_label.setStyleSheet("color: #ef6c00; font-weight: bold;")
        else:
            self.busy_label.setText("Ready")
            self.busy_label.setStyleSheet("color: #2e7d32;")

    def last_position(self) -> list[float] | None:
        """Logical coordinates from the latest poll (None if not yet read)."""
        return list(self._last_pos_um) if self._have_pos else None

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------
    def _update_pulse_label(self):
        um = self.step_combo.currentData()
        pulses = [self.stage.um_to_pulse(i, um) for i in range(3)]
        self.pulse_label.setText(
            f"= {pulses[0]}/{pulses[1]}/{pulses[2]} pulse (X/Y/Z)")

    def _manual_ok(self) -> bool:
        """Whether manual moves are allowed (connected, no task running)."""
        return self.stage.connected and not self.runner.is_busy()

    def _jog(self, axis: int, sign: int):
        if not self._manual_ok():
            return
        um = self.step_combo.currentData() * sign
        self.caller.call(lambda: self.stage.move_relative_um(axis, um),
                         on_error=self._show_error)

    def _goto_logical(self, pos):
        """Move to logical coordinates pos (relative M:W moves, as in scans)."""
        if not self._manual_ok():
            return
        x, y, z = pos
        self.caller.call(lambda: self.stage.move_to_um_relative(x, y, z),
                         on_error=self._show_error)

    def _move_absolute(self):
        self._goto_logical([s.value() for s in self.abs_spins])

    def _fill_current(self):
        for spin, um in zip(self.abs_spins, self._last_pos_um):
            spin.setValue(um)

    def _home(self):
        # Homing moves all axes a long way, so confirm first (avoid
        # collision with the objective lens)
        ret = QMessageBox.question(
            self, "Mechanical Home",
            "All axes will travel to the mechanical origin.\n"
            "Have you verified there is no interference with the sample "
            "or objective lens?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes and self._manual_ok():
            self.caller.call(self.stage.home_all, on_error=self._show_error)

    def _zero(self):
        if not self._manual_ok():
            return
        # R:W cannot be undone (the current logical origin is lost), so
        # confirm first
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Reset Logical Origin (R:W)")
        box.setText("The current position will become the logical origin "
                    "(0,0,0).\n"
                    "The previous logical origin will be lost. Proceed?")
        chk = QCheckBox("Save this position as the origin of "
                        f"\"{self.registry.current()['name']}\"")
        box.setCheckBox(chk)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        register = chk.isChecked()

        def done(_):
            if register:
                self._register_origin("manual R:W")
        self.caller.call(self.stage.zero_all, on_result=done,
                         on_error=self._show_error)

    def _show_error(self, exc: Exception):
        QMessageBox.warning(self, "Stage Error", str(exc))

    # ------------------------------------------------------------------
    # Saved origin / strip positions
    # ------------------------------------------------------------------
    def _register_origin(self, method: str) -> bool:
        """Save the machine coordinates of the current logical origin to
        the sensor (False if they are unknown)."""
        off = self.stage.machine_offset_um
        if off is None:
            return False
        self.registry.set_machine_origin(off, method)
        return True

    def _register_origin_manual(self):
        if not self._register_origin("manual save"):
            QMessageBox.warning(
                self, "Cannot Save",
                "Machine coordinates are not being tracked. Run Mechanical "
                "Home (H:W) from the GUI, then find the origin again.")

    def _goto_origin(self, to_start: bool):
        prof = self.registry.current()
        org = prof.get("machine_origin")
        if not org:
            QMessageBox.warning(self, "Cannot Move",
                                "No origin is saved for this sensor")
            return
        target = list(org["pos_um"])
        desc = "saved origin"
        if to_start and sensors.is_pixel(prof):
            pad = float(prof.get("pad_um", 100.0))
            target[0] -= pad / 2.0
            target[1] -= pad / 2.0
            desc = "Find Origin start position (center of the reference pixel's pad)"
        elif to_start:
            target[0] -= float(prof["x_frame_offset_um"]) + 100.0
            desc = ("Find Origin start position (on the reference strip, "
                    "100 µm inside the end)")
        logical = self.stage.logical_from_machine(target)
        if logical is None:
            QMessageBox.warning(
                self, "Cannot Move",
                "Machine coordinates are not being tracked. Run Mechanical "
                "Home (H:W) from the GUI.")
            return
        ret = QMessageBox.question(
            self, "Go to Saved Position",
            f"Moving to the {desc} of {prof['name']}.\n"
            "Machine coordinates X={:.0f} Y={:.0f} Z={:.0f} µm\n".format(
                *target)
            + "(saved {} {})\n".format(str(org.get("registered_at", ""))[:16],
                                       org.get("method", ""))
            + "If the sensor has been remounted, the position may have "
              "shifted.\n"
              "Have you verified there is no interference with the "
              "objective lens?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._goto_logical(logical)

    def _goto_strip(self):
        ch = self.strip_ch_combo.currentData()
        if ch is None:
            return
        self._goto_logical(self.registry.position_for(
            ch, self.strip_place_combo.currentData()))

    # ------------------------------------------------------------------
    # Dictionary of saved positions (per sensor)
    # ------------------------------------------------------------------
    def _selected_bookmark(self) -> str | None:
        item = self.bm_list.currentItem()
        return None if item is None else item.data(Qt.UserRole)

    def _add_bookmark(self):
        if not self.stage.connected:
            QMessageBox.warning(self, "Cannot Save",
                                "The stage is disconnected")
            return
        frame = self.frame_combo.currentData()
        if frame == "machine" and self.stage.machine_offset_um is None:
            QMessageBox.warning(
                self, "Cannot Save",
                "Machine coordinates are not being tracked. Run Mechanical "
                "Home (H:W) from the GUI, or save in logical coordinates.")
            return
        name, ok = QInputDialog.getText(self, "Save Position", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        if name in self.registry.positions():
            ret = QMessageBox.question(
                self, "Confirm Overwrite",
                f"\"{name}\" is already saved. Overwrite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return

        # The displayed coordinates can be up to 0.5 s old (polled
        # values), so re-read from the stage when saving. Do not save
        # while the stage is moving
        def register(result):
            pos, busy = result
            if busy:
                QMessageBox.warning(self, "Cannot Save",
                                    "The stage is moving. Save after it "
                                    "has stopped")
                return
            if frame == "machine":
                pos = self.stage.machine_from_logical(pos)
                if pos is None:
                    return
            self.registry.set_position(name, pos, frame)
        self.caller.call(self.stage.get_position_um, on_result=register,
                         on_error=self._show_error)

    def _goto_bookmark(self):
        name = self._selected_bookmark()
        entry = self.registry.positions().get(name) if name else None
        if entry is None:
            return
        target = list(entry["pos_um"])
        if entry.get("frame") != "logical":
            target = self.stage.logical_from_machine(target)
            if target is None:
                QMessageBox.warning(
                    self, "Cannot Move",
                    "This position was saved in machine coordinates. "
                    "Machine coordinates are not being tracked, so the "
                    "move is not possible (run H:W from the GUI).")
                return
        self._goto_logical(target)

    def _delete_bookmark(self):
        name = self._selected_bookmark()
        if name is None:
            return
        ret = QMessageBox.question(
            self, "Confirm Delete", f"Delete saved position \"{name}\"?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self.registry.delete_position(name)

    # ------------------------------------------------------------------
    # Z autofocus (waveform-amplitude based)
    # ------------------------------------------------------------------
    def _task_precheck(self, need_laser: bool = False) -> bool:
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "Cannot run",
                f"\"{self.runner.current_name()}\" is running")
            return False
        if not (self.scope.connected and self.stage.connected):
            QMessageBox.warning(self, "Cannot run",
                                "The scope and the stage must be connected")
            return False
        if need_laser and not self.laser.connected:
            QMessageBox.warning(self, "Cannot run",
                                "Auto laser ON requires the laser to be "
                                "connected")
            return False
        return True

    def _start_autofocus(self):
        if not self._task_precheck():
            return
        ch = self.af_ch.currentData()
        metric = self.af_metric.currentData()
        if metric == "min":
            desc = (f"Z will be swept ±{self.af_range.value():.0f} µm and "
                    f"moved to the Z where the {ch} amplitude is minimum "
                    "(= focus, center of the valley).\n"
                    "Check: (1) laser ON (2) beam on the silicon beside a "
                    "strip — where the signal is clearly visible\n"
                    f"(not directly on the electrode) (3) signal visible "
                    f"on {ch} (trigger EXT)")
        else:
            desc = (f"Z will be swept ±{self.af_range.value():.0f} µm and "
                    f"moved to the Z where the {ch} amplitude is maximum "
                    "(old method).\n"
                    "★ The amplitude is minimum at the focus, so this "
                    "method will most likely miss the focus ★")
        ret = QMessageBox.question(
            self, "Z Autofocus",
            desc + "\nSequence mode is switched OFF and the trigger to "
            "NORM (they are set again when a scan starts, so this is "
            "harmless).",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {"ch": ch, "range_um": self.af_range.value(),
                  "step_um": self.af_step.value(), "metric": metric}
        worker = AutofocusWorker(self.scope, self.stage, params,
                                 self._log_dir / f"autofocus_{stamp}.csv")
        self.runner.start("Z Focus", worker, worker.finished_focus,
                          self._on_task_finished("Autofocus"))

    def _on_task_finished(self, title: str):
        def done(ok: bool, message: str, _worker):
            if ok:
                QMessageBox.information(self, f"{title} Complete", message)
            else:
                QMessageBox.warning(self, f"{title} Ended", message)
        return done

    # ------------------------------------------------------------------
    # Auto Alignment (signal-based)
    # ------------------------------------------------------------------
    def _start_alignment(self, mode: str):
        laser_on = self.align_laser_check.isChecked()
        if not self._task_precheck(need_laser=laser_on):
            return
        prof = self.registry.current()
        al = prof["align"]
        ch = self.af_ch.currentData()
        ref = prof["ref_channel"]
        x_off = float(prof["x_frame_offset_um"])
        if sensors.is_pixel(prof):
            return self._start_alignment_pixel(mode, prof, al, ch, ref, laser_on)
        lines = [
            f"Starting signal-based Auto Alignment "
            f"(sensor: {prof['name']} / CH: {ch}).",
            "",
            "Checklist:",
            f"  1. The beam is on the {ref} strip, 50–200 µm inside the",
            "     readout-side end of the exposed strip region (coarse "
            "approach done with the camera)",
            "  2. Laser ON (or auto ON selected), trigger EXT, and the",
            f"     signal is visible on {ch}",
            "",
            "Steps: ① coarse Y sweep to find the electrode ② Z of minimum",
            "amplitude (= focus) on the silicon beside the electrode "
            "③ fine Y sweep ④ X signal-edge detection ⑤ Z re-check →",
            f"Reset Logical Origin (R:W) at edge+{x_off:.0f} µm "
            "(equivalent to the frame edge).",
            "On completion the machine coordinates of the logical origin "
            "are saved to this sensor.",
        ]
        if self.align_premove_check.isChecked():
            lines[3:5] = [
                f"  1. The current logical origin is correct: before "
                f"starting, the stage moves to logical "
                f"X={-(x_off + 100.0):.0f}, Y=0",
                f"     (on the {ref} strip, 100 µm inside the end)"]
        if ch != ref:
            lines.insert(1, f"★ The readout CH ({ch}) differs from the "
                            f"reference CH ({ref}). Check that this is "
                            "intended ★")
        fine = self.align_fine_check.isChecked()
        if fine:
            lines += [
                "Fine finishing + verification ON: after the coarse search, "
                "fine sweeps in",
                "one-pulse steps with many reads (erf / Lorentzian fits, "
                "reverse sweep for",
                "the backlash estimate); after R:W the origin is re-measured "
                "and residuals",
                "of one pulse or more are corrected.",
            ]
        if mode == "full":
            xs = ", ".join(f"{x:.0f}" for x in al["x_points"])
            ys = ", ".join(f"{y:.0f} ({c})" for y, c in al["y_points"])
            lines += [
                f"⑥ Measure the electrode center + focus at X = {xs} µm "
                f"and Y = {ys} µm",
                "   and update the tilt coefficients (dY/dX, dZ/dX, dZ/dY) "
                + ("(about 15–25 min)" if fine else "(about 8–12 min)"),
                "★ The stage travels the full strip length.",
                "  Make sure nothing is in the way ★",
            ]
        else:
            lines.append("(takes about 7–12 min)" if fine
                         else "(takes about 2–3 min)")
        lines.append("Sequence mode is switched OFF and the trigger to "
                     "NORM.")
        ret = QMessageBox.question(
            self, "Confirm Auto Alignment", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {
            "mode": mode,
            "ch": ch,
            "af_range_um": self.af_range.value(),
            "af_step_um": self.af_step.value(),
            "laser_on": laser_on,
            "x_frame_offset_um": x_off,
            "pitch_um": float(prof["pitch_um"]),
            "x_points": list(al["x_points"]),
            "y_points": [list(v) for v in al["y_points"]],
            "y_probe_off_um": float(al["y_probe_off_um"]),
            "x_probe_off_um": float(al["x_probe_off_um"]),
            "y_half_um": float(al["y_half_um"]),
            "coef0": self.registry.coefficients(),
            # fine finishing + verification (see the module docstring of
            # gui/alignment_worker.py)
            "fine": fine, "verify": fine, "backlash_check": fine,
            "csv_path": str(self._log_dir / f"alignment_{stamp}.csv"),
        }
        if self.align_premove_check.isChecked():
            params["pre_move_xy"] = (-(x_off + 100.0), 0.0)
        worker = AlignmentWorker(self.scope, self.stage, self.laser, params)
        name = "Full Calibration" if mode == "full" else "Find Origin"
        self.runner.start(name, worker, worker.finished_align,
                          self._on_alignment_finished)

    def _start_alignment_pixel(self, mode: str, prof: dict, al: dict,
                               ch: str, ref: str, laser_on: bool):
        """Find Origin / Full Calibration for pixel sensors ("Pixel sensors" in
        gui/alignment_worker.py)."""
        pad = float(prof.get("pad_um", 100.0))
        pitch = float(prof["pitch_um"])
        fine = self.align_fine_check.isChecked()
        lines = [
            f"Starting signal-based Auto Alignment (pixel sensor) "
            f"(sensor: {prof['name']} / CH: {ch}).",
            "",
            "Checklist:",
            f"  1. The beam is on or near (±50 µm) the bonding pad of {ref}",
            f"     (signal-free {pad:.0f} µm square; coarse approach done with the camera)",
            "  2. Laser ON (or auto ON selected), trigger EXT, and the",
            f"     signal is visible on {ch} outside the pad",
            "",
            "Steps: ① Y sweep to find the pad dip ② Z of minimum amplitude (= focus)",
            "on the silicon beside the pad ③ fine Y dip ④ X sweep for the pad center X",
            "⑤ Z re-check → Reset Logical Origin (R:W) at the pad corner "
            f"= pad center + ({pad / 2:.0f}, {pad / 2:.0f}) µm.",
            f"Pixel center at (−{pad / 2:.0f}, −{pad / 2:.0f}), neighboring pixels at ±{pitch:.0f} µm.",
            "On completion the machine coordinates of the logical origin are saved to this sensor.",
        ]
        if self.align_premove_check.isChecked():
            lines[3:5] = [
                f"  1. The current logical origin is correct: before starting, the stage "
                f"moves to logical X={-pad / 2:.0f}, Y={-pad / 2:.0f} (center of the reference pad)"]
        if ch != ref:
            lines.insert(1, f"★ The readout CH ({ch}) differs from the "
                            f"reference CH ({ref}). Check that this is intended ★")
        if fine:
            lines += [
                "Fine finishing + verification ON: after the coarse search, fine sweeps in",
                "one-pulse steps with many reads (erf / Lorentzian fits, reverse sweep for",
                "the backlash estimate); after R:W the origin is re-measured and residuals",
                "of one pulse or more are corrected.",
            ]
        pads = [list(v) for v in al.get("pads", [])]
        if mode == "full":
            pl = ", ".join(f"{c} ({x:.0f}, {y:.0f})" for c, x, y in pads)
            lines += [
                f"⑥ Repeat the measurement (read on each CH) at the pad centers of the other",
                f"   readout pixels {pl or 'none'} µm and update the tilt coefficients by a plane fit",
                "   (lever arm = one pitch: the coefficients are a coarse guide)",
            ]
            if not pads:
                QMessageBox.warning(self, "Cannot run Full Calibration",
                                    "There is no readout pixel other than the reference "
                                    "(check the readout CHs in the sensor editor)")
                return
        else:
            lines.append("(takes about 7–12 min)" if fine else "(takes about 2–3 min)")
        lines.append("Sequence mode is switched OFF and the trigger to NORM.")
        ret = QMessageBox.question(
            self, "Confirm Auto Alignment (pixel)", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {
            "mode": mode,
            "sensor_type": "pixel",
            "ch": ch,
            "af_range_um": self.af_range.value(),
            "af_step_um": self.af_step.value(),
            "laser_on": laser_on,
            "pad_um": pad,
            "pitch_um": pitch,
            "pads": pads,
            "x_points": [], "y_points": [],
            "y_probe_off_um": float(al.get("y_probe_off_um", pad / 2.0 + 80.0)),
            "x_probe_off_um": 0.0,
            "y_half_um": float(al.get("y_half_um", pad + 100.0)),
            "coef0": self.registry.coefficients(),
            "fine": fine, "verify": fine, "backlash_check": fine,
            "csv_path": str(self._log_dir / f"alignment_{stamp}.csv"),
        }
        if self.align_premove_check.isChecked():
            params["pre_move_xy"] = (-pad / 2.0, -pad / 2.0)
        worker = AlignmentWorker(self.scope, self.stage, self.laser, params)
        name = "Full Calibration (pixel)" if mode == "full" else "Find Origin (pixel)"
        self.runner.start(name, worker, worker.finished_align,
                          self._on_alignment_finished)

    def _on_alignment_finished(self, ok: bool, message: str, worker):
        # Results are saved in the GUI thread (to the current sensor
        # profile)
        notes = []
        if worker.origin_done:
            if self._register_origin("Auto Alignment"):
                x, y, z = self.stage.machine_offset_um
                notes.append("Saved the machine coordinates of the logical "
                             f"origin: X={x:.0f} Y={y:.0f} Z={z:.0f} µm")
            else:
                notes.append("Machine coordinates are unknown, so the "
                             "origin was not saved (run H:W from the GUI "
                             "and it will be saved next time)")
        if worker.calibration is not None:
            path = self.registry.set_calibration(worker.calibration)
            notes.append("Saved the coefficients to "
                         f"\"{self.registry.current()['name']}\" "
                         f"(history: {path.name})")
        self.refresh_sensor()
        text = "\n".join([message] + notes)
        if ok:
            QMessageBox.information(self, "Auto Alignment Complete", text)
        else:
            QMessageBox.warning(self, "Auto Alignment Ended", text)

    def _edit_coefficients(self):
        """Manual-entry dialog for the tilt coefficients (unit µm/mm)."""
        dzdx, dzdy, dydx, source = self.registry.coefficients()
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Tilt Coefficients")
        form = QFormLayout(dlg)
        form.addRow(QLabel(f"Sensor: {self.registry.current()['name']}\n"
                           f"Current source: {source}"))
        spins = []
        for label, value, tip in (
                ("dZ/dX [µm/mm]", dzdx, "Tilt of the focal plane along X"),
                ("dZ/dY [µm/mm]", dzdy, "Tilt of the focal plane along Y"),
                ("dY/dX [µm/mm]", dydx, "In-plane rotation of the strips "
                                         "(drift of the electrode center Y "
                                         "along X)")):
            spin = QDoubleSpinBox()
            spin.setRange(-100.0, 100.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setValue(value * 1000.0)
            spin.setToolTip(tip)
            form.addRow(label, spin)
            spins.append(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() == QDialog.Accepted:
            self.registry.set_manual_coefficients(
                *(s.value() / 1000.0 for s in spins))

    # ------------------------------------------------------------------
    def emergency_stop(self):
        if self.stage.connected:
            self.caller.call(self.stage.emergency_stop)

    def shutdown(self):
        self._poll_timer.stop()
        if self.stage.connected:
            try:
                self.stage.disconnect()
            except Exception:
                pass
        self.caller.shutdown()
