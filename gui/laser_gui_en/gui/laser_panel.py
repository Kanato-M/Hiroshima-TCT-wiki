# -*- coding: utf-8 -*-
"""PiLAS DX laser control panel.

- connection / status display (polls mState? periodically)
- trigger source, frequency and TUNE settings
- one-click standard settings (internal / 1 MHz / TUNE manual 50 % =
  MIP equivalent; laser_freq_hz, laser_tune_auto, laser_tune_percent in
  config.py)
- emission ON/OFF (ON asks for confirmation to prevent mistakes)
- TUNE optimization (automatically finds the TUNE that reaches the
  target amplitude. gui/tune_worker.py)
  Methods: single CH / sum of two adjacent CHs A_lead+A_sub matched to a
  MIP-equivalent amplitude midway between the strips (2026-10-07)
- console for sending arbitrary commands

The relation between TUNE and signal amplitude changes with conditions
such as the visible-light illumination for the camera being ON/OFF, so
no fixed "TUNE → amplitude" conversion is shown. To match an amplitude,
use the TUNE optimization, which follows the actual measurement.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QVBoxLayout)

import sensors
from .tune_worker import TuneOptimizeWorker
from .widgets import ElidedLabel
from .worker import AsyncCaller

# Presets of commonly used repetition frequencies [Hz]. The standard is
# 1 MHz (changed from the TCT-recommended 1.25 MHz on 2026-11-01;
# laser_freq_hz in config.py)
FREQ_PRESETS = [
    ("1 kHz", 1_000), ("10 kHz", 10_000), ("100 kHz", 100_000),
    ("1 MHz (standard)", 1_000_000), ("1.25 MHz (TCT rec.)", 1_250_000),
    ("10 MHz", 10_000_000), ("40 MHz", 40_000_000),
]


class LaserPanel(QGroupBox):
    POLL_MS = 2000  # mState? polling period

    def __init__(self, laser, scope, registry, runner, log_dir: str,
                 stage=None, parent=None, cfg=None):
        super().__init__("Laser (PiLAS DX)", parent)
        self.laser = laser
        self.scope = scope
        self.stage = stage      # used by the TUNE optimization's move to the strip midpoint
        # Standard conditions (config.py); same defaults without a cfg
        self.std_freq_hz = int(getattr(cfg, "laser_freq_hz", 1_000_000))
        self.std_tune_auto = bool(getattr(cfg, "laser_tune_auto", False))
        self.std_tune_percent = float(
            getattr(cfg, "laser_tune_percent", 50.0))
        self.registry = registry
        self.runner = runner
        self._log_dir = Path(log_dir)
        self.caller = AsyncCaller("laser", self)
        # whether the device settings have been copied into the input
        # fields after connecting
        self._synced = False
        self._build_ui()
        registry.subscribe(self.refresh_sensor)
        runner.busy_changed.connect(
            lambda busy: self.tune_opt_btn.setEnabled(not busy))
        self.refresh_sensor()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_MS)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_pending = False

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- connection row ---
        conn_row = QHBoxLayout()
        self.port_edit = QLineEdit(self.laser.port)
        self.port_edit.setToolTip("COM port (e.g. COM4, /dev/ttyUSB0)")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.conn_label = QLabel("Disconnected")
        self.conn_label.setStyleSheet("color: gray;")
        conn_row.addWidget(QLabel("Port:"))
        conn_row.addWidget(self.port_edit, 1)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.conn_label)
        layout.addLayout(conn_row)

        # --- status display ---
        status = QGridLayout()
        self.emission_label = QLabel("Emission: ---")
        self.emission_label.setAlignment(Qt.AlignCenter)
        self.emission_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 2px;"
            "background: #444; color: white; border-radius: 4px;")
        status.addWidget(self.emission_label, 0, 0, 1, 5)
        # The status strings vary in length (e.g. the interlock-open
        # warning), so use eliding labels that do not push the panel
        # wider
        self.interlock_label = ElidedLabel("Interlock: ---")
        self.trig_label = ElidedLabel("Trig: ---")
        self.freq_label = ElidedLabel("Freq: ---")
        self.tune_label = ElidedLabel("Tune: ---")
        self.temp_label = ElidedLabel("Head: ---")
        self.temp_label.setToolTip("Laser head temperature")
        status.addWidget(self.interlock_label, 1, 0)
        status.addWidget(self.trig_label, 1, 1)
        status.addWidget(self.freq_label, 1, 2)
        status.addWidget(self.tune_label, 1, 3)
        status.addWidget(self.temp_label, 1, 4)
        # Eliding labels request no width of their own, so distribute the
        # width through the column stretch factors
        for col, stretch in enumerate((3, 3, 3, 3, 4)):
            status.setColumnStretch(col, stretch)
        layout.addLayout(status)

        # --- settings ---
        cfg = QGridLayout()
        cfg.addWidget(QLabel("Trigger source:"), 0, 0)
        self.trig_combo = QComboBox()
        # (display name, value for ts=). The TCT standard is internal
        # (the laser is the reference clock)
        self.trig_combo.addItem("internal (ts=0)", 0)
        self.trig_combo.addItem("ext. TTL (ts=2)", 2)
        self.trig_combo.addItem("ext. Adj (ts=1)", 1)
        cfg.addWidget(self.trig_combo, 0, 1)
        apply_trig = QPushButton("Apply")
        apply_trig.clicked.connect(self._apply_trigger)
        cfg.addWidget(apply_trig, 0, 2)

        cfg.addWidget(QLabel("Rep. rate:"), 1, 0)
        self.freq_combo = QComboBox()
        # an arbitrary Hz value can also be typed in directly
        self.freq_combo.setEditable(True)
        for label, hz in FREQ_PRESETS:
            self.freq_combo.addItem(label, hz)
        # The initial selection is the standard value (1 MHz). After
        # connecting it is synced to the device's current value
        self._select_freq(self.std_freq_hz)
        cfg.addWidget(self.freq_combo, 1, 1)
        apply_freq = QPushButton("Apply")
        apply_freq.clicked.connect(self._apply_frequency)
        cfg.addWidget(apply_freq, 1, 2)

        cfg.addWidget(QLabel("TUNE:"), 2, 0)
        tune_row = QHBoxLayout()
        self.tune_mode_combo = QComboBox()
        self.tune_mode_combo.addItem("auto", True)
        self.tune_mode_combo.addItem("manual", False)
        # The standard is manual 50 % (MIP equivalent)
        self.tune_mode_combo.setCurrentIndex(
            self.tune_mode_combo.findData(self.std_tune_auto))
        self.tune_spin = QDoubleSpinBox()
        self.tune_spin.setRange(0.0, 100.0)
        self.tune_spin.setDecimals(1)
        self.tune_spin.setValue(self.std_tune_percent)
        self.tune_spin.setSuffix(" %")
        self.tune_spin.setToolTip(
            "TUNE value (0% = large pulse amplitude, 100% = small). The\n"
            "optimal value depends on the repetition rate. The standard is\n"
            f"manual {self.std_tune_percent:g}% ({self.std_freq_hz / 1e6:g} MHz)"
            " = MIP-equivalent output.\n"
            "To match another amplitude, use the TUNE optimization below")
        tune_row.addWidget(self.tune_mode_combo)
        tune_row.addWidget(self.tune_spin)
        cfg.addLayout(tune_row, 2, 1)
        apply_tune = QPushButton("Apply")
        apply_tune.clicked.connect(self._apply_tune)
        cfg.addWidget(apply_tune, 2, 2)

        # --- one-click standard settings (counterpart of the scope button) ---
        std_mode = "auto" if self.std_tune_auto else "manual"
        self.std_btn = QPushButton(
            f"★ Apply Standard Settings ({self.std_freq_hz / 1e6:g} MHz / "
            f"TUNE {std_mode} {self.std_tune_percent:g}% = MIP equiv.) ★")
        self.std_btn.setToolTip(
            "Send the laser's standard measurement conditions at once: "
            "trigger internal (ts=0), "
            f"repetition rate {self.std_freq_hz / 1e6:g} MHz, "
            f"TUNE {std_mode} {self.std_tune_percent:g}% "
            "(= MIP-equivalent output; adjacent 2ch sum matched to 64 mV\n"
            "midway between the strips). The input fields are reset to the "
            "standard values too. Emission ON/OFF is not changed")
        self.std_btn.setStyleSheet("font-weight: bold;")
        self.std_btn.clicked.connect(self._apply_standard_defaults)
        cfg.addWidget(self.std_btn, 3, 0, 1, 3)
        layout.addLayout(cfg)

        # --- TUNE optimization (waveform-amplitude based) ---
        # Lowers TUNE from 100% (weak-light side) while measuring the
        # scope amplitude and sets the TUNE that reaches the target
        # amplitude. The optimal TUNE depends on the frequency, so run
        # this first when the signal disappears after a frequency change
        # Methods (added 2026-10-07):
        #   single: match the amplitude of one CH to the target (classic)
        #   sum2  : match the sum A_lead + A_sub of the CHs that read two
        #           adjacent strips to the target. The sum is insensitive
        #           to the beam position (between the two strips), so it
        #           is used to match a MIP-equivalent signal midway between
        #           the strips (DESY beam test 2026: 64 ± 1 mV)
        opt_box = QGroupBox("TUNE optimization (match target amplitude)")
        opt_box.setToolTip(
            "Run with the laser ON, focused, and the trigger running\n"
            "(scope connection required). Single CH: on the silicon beside\n"
            "a strip; adjacent-2ch sum: midway between strips (auto move)")
        opt_v = QVBoxLayout(opt_box)
        # Packed into one row, the minimum width of the right column
        # exceeds 620 px, squeezes the left column (scope) and crushes the
        # combo/spin boxes to 64 px ("..."). Split into three rows:
        # [method, CH] / [target, ±, step, Optimize] / [move check box]
        row0 = QHBoxLayout()
        opt_v.addLayout(row0)
        opt = QHBoxLayout()
        row0.addWidget(QLabel("Method:"))
        self.tune_mode_sel = QComboBox()
        self.tune_mode_sel.addItem("Single CH", "single")
        self.tune_mode_sel.addItem("Adjacent 2ch sum (MIP equiv.)", "sum2")
        self.tune_mode_sel.setToolTip(
            "Single CH: match the amplitude (PKPK) of that CH to the target\n"
            "Adjacent 2ch sum: match the sum A_lead + A_sub of the CHs that\n"
            "  read two neighboring strips to the target. The sum is\n"
            "  insensitive to the beam position, so measure it midway\n"
            "  between the strips (250 µm for a 500 µm pitch).\n"
            "  MIP of the DESY beam test: 64 ± 1 mV midway (77 mV beside)")
        self.tune_mode_sel.currentIndexChanged.connect(
            self._on_tune_mode_changed)
        row0.addWidget(self.tune_mode_sel)
        self.tune_ch_label = QLabel("CH:")
        row0.addWidget(self.tune_ch_label)
        self.tune_ch = QComboBox()
        row0.addWidget(self.tune_ch)
        self.tune_pair = QComboBox()
        self.tune_pair.setToolTip("Pair of CHs that read neighboring electrodes")
        row0.addWidget(self.tune_pair)
        row0.addStretch(1)
        opt.addWidget(QLabel("Target"))
        self.tune_target_spin = QDoubleSpinBox()
        self.tune_target_spin.setRange(5.0, 2000.0)
        self.tune_target_spin.setValue(200.0)
        self.tune_target_spin.setSuffix(" mV")
        self.tune_target_spin.setDecimals(0)
        self.tune_target_spin.setToolTip(
            "Finds and sets the TUNE closest to this amplitude (PKPK; the "
            "sum for the 2ch-sum method)")
        opt.addWidget(self.tune_target_spin)
        opt.addWidget(QLabel("±"))
        self.tune_tol_spin = QDoubleSpinBox()
        self.tune_tol_spin.setRange(0.1, 50.0)
        self.tune_tol_spin.setValue(1.0)
        self.tune_tol_spin.setSuffix(" mV")
        self.tune_tol_spin.setDecimals(1)
        self.tune_tol_spin.setToolTip(
            "Tolerance. The interval of the coarse sweep that crosses the "
            "target is re-swept finely (0.5% steps, many reads) and the "
            "sweep stops at the TUNE that falls within this range")
        opt.addWidget(self.tune_tol_spin)
        opt.addWidget(QLabel("step"))
        self.tune_opt_step = QDoubleSpinBox()
        self.tune_opt_step.setRange(0.5, 20.0)
        self.tune_opt_step.setValue(2.0)
        self.tune_opt_step.setSuffix(" %")
        self.tune_opt_step.setDecimals(1)
        self.tune_opt_step.setToolTip("TUNE step of the coarse sweep")
        opt.addWidget(self.tune_opt_step)
        opt.addStretch(1)
        self.tune_opt_btn = QPushButton("Optimize")
        self.tune_opt_btn.clicked.connect(self._start_tune_optimize)
        opt.addWidget(self.tune_opt_btn)
        opt_v.addLayout(opt)
        self.tune_move_chk = QCheckBox(
            "Move to the Y midway between the 2 strips (current X; return afterwards)")
        self.tune_move_chk.setChecked(True)
        self.tune_move_chk.setToolTip(
            "Move to the Y midway between the electrode centers of the two\n"
            "selected CHs before measuring (Y/Z corrected with the in-plane\n"
            "rotation and tilt coefficients; stage connection required).\n"
            "Returns to the original position after completion or abort.\n"
            "OFF measures at the current position")
        opt_v.addWidget(self.tune_move_chk)
        layout.addWidget(opt_box)
        # each method has its own default target (the previous value is
        # restored when switching back)
        self._tune_targets = {"single": 200.0, "sum2": 64.0}
        self._tune_mode_cur = None
        self._on_tune_mode_changed()

        # --- emission ON/OFF ---
        onoff_row = QHBoxLayout()
        self.on_btn = QPushButton("Emission ON (ld=1)")
        self.on_btn.setStyleSheet(
            "background: #b71c1c; color: white; font-weight: bold; padding: 8px;")
        self.on_btn.clicked.connect(self._emission_on)
        self.off_btn = QPushButton("Emission OFF (ld=0)")
        self.off_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        self.off_btn.clicked.connect(lambda: self._set_emission(False))
        onoff_row.addWidget(self.on_btn)
        onoff_row.addWidget(self.off_btn)
        layout.addLayout(onoff_row)

        # --- arbitrary command console ---
        console_row = QHBoxLayout()
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("Command (e.g. version?, mState?)")
        self.cmd_edit.returnPressed.connect(self._send_manual)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send_manual)
        console_row.addWidget(self.cmd_edit, 1)
        console_row.addWidget(send_btn)
        layout.addLayout(console_row)
        layout.addStretch(1)

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool):
        for w in (self.on_btn, self.off_btn, self.cmd_edit,
                  self.trig_combo, self.freq_combo,
                  self.tune_mode_combo, self.tune_spin, self.std_btn):
            w.setEnabled(enabled)

    def _select_freq(self, hz: int):
        """Show the preset (or the plain number) in the frequency field."""
        idx = self.freq_combo.findData(int(hz))
        if idx >= 0:
            self.freq_combo.setCurrentIndex(idx)
        else:
            self.freq_combo.setEditText(str(int(hz)))

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.laser.connected:
            self._poll_timer.stop()
            self.caller.call(self.laser.disconnect,
                             on_result=lambda _: self._on_disconnected())
        else:
            self.laser.port = self.port_edit.text().strip()
            self.connect_btn.setEnabled(False)
            self.caller.call(self.laser.connect,
                             on_result=lambda _: self._on_connected(),
                             on_error=self._on_conn_error)

    def _on_connected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect")
        self.conn_label.setText("Connected")
        self.conn_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        self._set_controls_enabled(True)
        self._synced = False
        self._poll_timer.start()
        self._poll_status()

    def _on_disconnected(self):
        self.connect_btn.setText("Connect")
        self.conn_label.setText("Disconnected")
        self.conn_label.setStyleSheet("color: gray;")
        self._set_controls_enabled(False)
        self.emission_label.setText("Emission: ---")
        self.emission_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 2px;"
            "background: #444; color: white; border-radius: 4px;")

    def _on_conn_error(self, exc: Exception):
        self.connect_btn.setEnabled(True)
        QMessageBox.warning(self, "Laser Connection Error", str(exc))

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------
    def _poll_status(self):
        if self._poll_pending or not self.laser.connected:
            return
        self._poll_pending = True
        self.caller.call(self.laser.get_status,
                         on_result=self._update_status,
                         on_error=self._poll_failed)

    def _poll_failed(self, exc: Exception):
        self._poll_pending = False

    def _sync_inputs(self, st: dict):
        """Copy the device's current settings into the input fields,
        once, right after connecting.

        If the input fields stay at their defaults, one can "Apply" a
        value that differs from the device without noticing (especially
        the frequency and TUNE).
        """
        idx = self.trig_combo.findData(st["trigger_source"])
        if idx >= 0:
            self.trig_combo.setCurrentIndex(idx)
        self._select_freq(int(st["int_freq_hz"]))
        self.tune_mode_combo.setCurrentIndex(
            self.tune_mode_combo.findData(bool(st["tune_mode"])))
        self.tune_spin.setValue(st["tune_percent"])

    def _update_status(self, st: dict):
        self._poll_pending = False
        if not self._synced:
            self._synced = True
            self._sync_inputs(st)
        emitting = bool(st["emission_pulsed"])
        if emitting:
            self.emission_label.setText("Emission: ON")
            self.emission_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; padding: 2px;"
                "background: #b71c1c; color: white; border-radius: 4px;")
        else:
            self.emission_label.setText("Emission: OFF")
            self.emission_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; padding: 2px;"
                "background: #2e7d32; color: white; border-radius: 4px;")
        ilk = "OK" if st["interlock"] else "★OPEN (no emission)★"
        self.interlock_label.setText(f"Interlock: {ilk}")
        self.trig_label.setText(f"Trig: {st['trigger_source_name']}")
        if st["trigger_source"] == 0:
            self.freq_label.setText(f"Freq: {st['int_freq_hz'] / 1e6:.4g} MHz")
        else:
            self.freq_label.setText(f"Freq(ext): {st['ext_freq_hz'] / 1e6:.4g} MHz")
        mode = "auto" if st["tune_mode"] else "man"
        # TUNE in mState is in units of 0.01% (the old version divided
        # by 10 and displayed values like 650%. Fixed in 2026-10)
        self.tune_label.setText(f"Tune: {mode} {st['tune_percent']:.1f}%")
        temp_ok = "OK" if st["temp_ok"] else "BAD!"
        self.temp_label.setText(f"Head: {st['head_temp_c']:.2f} ℃ ({temp_ok})")

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------
    def _emission_on(self):
        # It is a class 1 laser, but one confirmation step is inserted
        # to prompt a check of the setup state
        ret = QMessageBox.question(
            self, "Confirm Emission Start",
            "About to start laser emission.\n"
            "Have you checked the interlock and the optical setup?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._set_emission(True)

    def _set_emission(self, on: bool):
        self.caller.call(lambda: self.laser.set_emission(on),
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_trigger(self):
        source = self.trig_combo.currentData()
        self.caller.call(lambda: self.laser.set_trigger_source(source),
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_frequency(self):
        # If the text matches a preset use its Hz value, otherwise
        # interpret the entered text as a number in Hz
        text = self.freq_combo.currentText()
        idx = self.freq_combo.findText(text)
        if idx >= 0:
            hz = self.freq_combo.itemData(idx)
        else:
            try:
                hz = int(float(text.replace(",", "").split()[0]))
            except (ValueError, IndexError):
                QMessageBox.warning(self, "Input Error",
                                    "Enter the frequency as a number in Hz")
                return
        self.caller.call(lambda: self.laser.set_frequency(hz),
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_tune(self):
        auto = self.tune_mode_combo.currentData()
        percent = self.tune_spin.value()

        def apply():
            self.laser.set_tune_mode(auto)
            if not auto:
                self.laser.set_tune_percent(percent)
        self.caller.call(apply,
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    def _apply_standard_defaults(self):
        """Send the laser's standard conditions (laser_* in config) at once.

        Trigger internal / repetition rate 1 MHz / TUNE manual 50 %
        (= MIP equivalent). The input fields are reset to the standard
        values too, so the screen and the device do not drift apart.
        Emission ON/OFF is not changed.
        """
        hz = self.std_freq_hz
        auto = self.std_tune_auto
        percent = self.std_tune_percent
        self.trig_combo.setCurrentIndex(self.trig_combo.findData(0))
        self._select_freq(hz)
        self.tune_mode_combo.setCurrentIndex(
            self.tune_mode_combo.findData(auto))
        self.tune_spin.setValue(percent)

        def apply():
            self.laser.set_trigger_source(0)
            self.laser.set_frequency(hz)
            self.laser.set_tune_mode(auto)
            if not auto:
                self.laser.set_tune_percent(percent)
        self.caller.call(apply,
                         on_result=lambda _: self._poll_status(),
                         on_error=self._show_error)

    # ------------------------------------------------------------------
    # TUNE optimization (waveform-amplitude based)
    # ------------------------------------------------------------------
    def refresh_sensor(self):
        """Match the readout CH / adjacent CH pair choices to the current
        sensor."""
        prof = self.registry.current()
        keep = self.tune_ch.currentData()
        self.tune_ch.clear()
        for ch in sensors.used_channels(prof):
            self.tune_ch.addItem(ch, ch)
        self.tune_ch.setCurrentIndex(max(self.tune_ch.findData(keep), 0))
        keep = self.tune_pair.currentData()
        self.tune_pair.clear()
        ro = prof["readout"]
        for a, b in self.registry.adjacent_pairs():
            self.tune_pair.addItem(
                f"{a}+{b} ({ro[a]['pad']}/{ro[b]['pad']})", (a, b))
        self.tune_pair.setCurrentIndex(
            max(self.tune_pair.findData(keep), 0))

    def _on_tune_mode_changed(self):
        """Switch the CH / CH-pair and move-check visibility and the
        default target according to the method."""
        mode = self.tune_mode_sel.currentData()
        prev = self._tune_mode_cur
        if prev != mode:
            # remember the target of the previous method and show the
            # previous value of this one (the default the first time)
            if prev is not None:
                self._tune_targets[prev] = self.tune_target_spin.value()
            self.tune_target_spin.setValue(self._tune_targets[mode])
            self._tune_mode_cur = mode
        single = mode == "single"
        self.tune_ch_label.setText("CH:" if single else "CH pair:")
        self.tune_ch.setVisible(single)
        self.tune_pair.setVisible(not single)
        self.tune_move_chk.setVisible(not single)

    def _start_tune_optimize(self):
        if self.runner.is_busy():
            QMessageBox.warning(
                self, "Cannot Run",
                f"\"{self.runner.current_name()}\" is running")
            return
        if not (self.scope.connected and self.laser.connected):
            QMessageBox.warning(self, "Cannot Run",
                                "The scope and the laser must be connected")
            return
        mode = self.tune_mode_sel.currentData()
        target = self.tune_target_spin.value()
        tol = self.tune_tol_spin.value()
        move_to = return_to = None
        if mode == "single":
            channels = [self.tune_ch.currentData()]
            what = f"the amplitude of {channels[0]}"
            where = ("beam where the signal is visible (on the silicon "
                     "beside a strip)")
        else:
            pair = self.tune_pair.currentData()
            if pair is None:
                QMessageBox.warning(
                    self, "Cannot Run",
                    "This sensor has no pair of readout CHs on neighboring "
                    "electrodes (check the electrode Y of the readout CHs "
                    "in the sensor editor)")
                return
            channels = list(pair)
            what = (f"the amplitude sum of {channels[0]}+{channels[1]} "
                    "(A_lead + A_sub)")
            if self.tune_move_chk.isChecked():
                if self.stage is None or not self.stage.connected:
                    QMessageBox.warning(
                        self, "Cannot Run",
                        "Moving to the strip midpoint requires a stage "
                        "connection (uncheck the box to measure at the "
                        "current position without moving)")
                    return
                try:
                    cur, busy = self.stage.get_position_um()
                except Exception as exc:
                    QMessageBox.warning(self, "Cannot Run",
                                        f"Cannot read the stage position: {exc}")
                    return
                if busy:
                    QMessageBox.warning(self, "Cannot Run",
                                        "The stage is moving")
                    return
                return_to = [float(v) for v in cur]
                move_to = self.registry.midpoint_position(
                    channels[0], channels[1], cur)
                what_mid = ("midway between the 2 pixels"
                            if sensors.is_pixel(self.registry.current())
                            else "midway between the strips")
                where = (f"move to {what_mid} X={move_to[0]:.0f}, "
                         f"Y={move_to[1]:.0f} µm (Z={move_to[2]:.0f}), "
                         "measure, and return to the current position "
                         "afterwards. The current position must be in focus")
            else:
                where = ("beam midway between the 2 pixels (measured at "
                         "the current position)"
                         if sensors.is_pixel(self.registry.current())
                         else "beam midway between the 2 strips (measured "
                         "at the current position)")
        ret = QMessageBox.question(
            self, "TUNE optimization",
            "Keeping the current frequency, TUNE is lowered from the 100% "
            f"side while measuring {what}, and set to the TUNE closest to "
            f"{target:.0f} ± {tol:.1f} mV (coarse sweep → fine sweep → "
            "verification).\n"
            f"Check: (1) laser ON (2) {where}, focused "
            "(3) trigger running\n"
            "TUNE switches to manual mode and the scope to sequence OFF, "
            "NORM.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        params = {"channels": channels, "tune_start": 100.0,
                  "tune_stop": 0.0, "tune_step": self.tune_opt_step.value(),
                  "fine_step": 0.5, "reads": 3, "fine_reads": 8,
                  "target_mv": target, "tol_mv": tol,
                  "move_to": move_to, "return_to": return_to}
        worker = TuneOptimizeWorker(
            self.scope, self.laser, params,
            self._log_dir / f"tuneopt_{stamp}.csv", stage=self.stage)
        self.runner.start("TUNE optimization", worker, worker.finished_tune,
                          self._on_tuneopt_finished)

    def _on_tuneopt_finished(self, ok: bool, message: str, _worker):
        # The optimization left TUNE at a new manual value, so bring the
        # input fields in line as well
        self._synced = False
        self._poll_status()
        if ok:
            QMessageBox.information(self, "TUNE optimization done", message)
        else:
            QMessageBox.warning(self, "TUNE optimization ended", message)

    def _send_manual(self):
        cmd = self.cmd_edit.text().strip()
        if not cmd:
            return
        self.cmd_edit.clear()
        # The response goes to the log panel, so no result callback is
        # needed
        self.caller.call(lambda: self.laser.send(cmd),
                         on_error=self._show_error)

    def _show_error(self, exc: Exception):
        QMessageBox.warning(self, "Laser Error", str(exc))

    # ------------------------------------------------------------------
    def emergency_stop(self):
        """Called from the main window's emergency stop."""
        if self.laser.connected:
            self.caller.call(self.laser.emergency_off)

    def shutdown(self):
        self._poll_timer.stop()
        if self.laser.connected:
            try:
                self.laser.disconnect()
            except Exception:
                pass
        self.caller.shutdown()
