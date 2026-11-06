# -*- coding: utf-8 -*-
"""LeCroy WavePro oscilloscope control panel.

- connect / screen mirroring (periodic screen dumps via SCDP)
- trigger settings (EXT trigger = sync to the laser TRIG OUT is standard)
- channel settings (DC 50 Ω coupling is standard for AC-LGAD readout)
- timebase / sequence mode / clear sweeps
- waveform save setup (directory, prefix, autosave) and SAV/RCL of
  front-panel setups
- free-form remote-command console
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox,
                               QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QSizePolicy, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from .worker import AsyncCaller, ScreenMirror

TDIV_PRESETS = ["200PS", "500PS", "1NS", "2NS", "5NS", "10NS", "20NS",
                "50NS", "100NS", "200NS", "500NS", "1US", "2US", "5US",
                "10US", "20US", "50US", "100US", "1MS"]
VDIV_PRESETS = ["1MV", "2MV", "5MV", "10MV", "20MV", "50MV",
                "100MV", "200MV", "500MV", "1V"]
COUPLINGS = ["D50", "D1M", "A1M", "GND"]
TRIG_COUPLINGS = ["DC", "AC", "HFREJ", "LFREJ"]


class ScopePanel(QGroupBox):
    def __init__(self, scope, cfg, registry, parent=None):
        super().__init__("Oscilloscope (LeCroy WavePro)", parent)
        self.scope = scope
        self.cfg = cfg
        self.registry = registry
        self.caller = AsyncCaller("scope", self)
        self._mirror: ScreenMirror | None = None
        # manual-save default: <base>\<year>\<YYYYMMDD>\manual (by date)
        self._save_dir_default = cfg.dated_save_dir() + "\\manual"
        self._build_ui()
        registry.subscribe(self.refresh_sensor)
        self.refresh_sensor()

    def refresh_sensor(self):
        """Sync the pad names in the channel table with the readout
        mapping of the current sensor."""
        readout = self.registry.current()["readout"]
        for ch, label in self.ch_labels.items():
            entry = readout.get(ch, {})
            pad = entry.get("pad", "")
            text = f"{ch} ({pad})" if pad else ch
            if not entry.get("use", True):
                text += " unused"
            label.setText(text)

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- connection row ---
        conn_row = QHBoxLayout()
        self.addr_edit = QLineEdit(self.scope.resource)
        self.addr_edit.setToolTip(
            "Resource name.\n"
            "VICP::<SCOPE_IP>::INSTR — LeCroy default (Remote=TCPIP(VICP))\n"
            "TCPIP0::<SCOPE_IP>::inst0::INSTR — for LXI(VXI-11)\n"
            "On connect, the other transport is tried automatically")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.conn_label = QLabel("Disconnected")
        self.conn_label.setStyleSheet("color: gray;")
        conn_row.addWidget(QLabel("VISA:"))
        conn_row.addWidget(self.addr_edit, 1)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.conn_label)
        layout.addLayout(conn_row)

        # --- screen mirror ---
        self.screen_label = QLabel("Screen mirror stopped")
        # Keep the minimum height low (for narrow logical resolutions;
        # spare space is filled by the stretch)
        self.screen_label.setMinimumHeight(140)
        # Size policy Ignored: prevents the size of a received mirror
        # image (pixmap) from feeding back into the layout's requested
        # size. Without this, the loop "receive image → label's size hint
        # grows → the whole panel grows → an even bigger image is
        # rendered" makes the panel grow without bound and pushes the
        # settings tabs below apart as well.
        self.screen_label.setSizePolicy(QSizePolicy.Ignored,
                                        QSizePolicy.Ignored)
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setStyleSheet(
            "background: black; color: gray; border: 1px solid #555;")
        layout.addWidget(self.screen_label, 1)

        mirror_row = QHBoxLayout()
        self.mirror_btn = QPushButton("Start Mirror")
        self.mirror_btn.setEnabled(False)
        self.mirror_btn.clicked.connect(self._toggle_mirror)
        mirror_row.addWidget(self.mirror_btn)
        mirror_row.addWidget(QLabel("Interval:"))
        self.mirror_interval = QComboBox()
        for label, sec in (("0.5 s", 0.5), ("1 s", 1.0), ("2 s", 2.0)):
            self.mirror_interval.addItem(label, sec)
        self.mirror_interval.setCurrentIndex(1)
        self.mirror_interval.currentIndexChanged.connect(
            self._mirror_interval_changed)
        mirror_row.addWidget(self.mirror_interval)
        mirror_row.addStretch(1)
        layout.addLayout(mirror_row)

        # --- settings tabs ---
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_trigger_tab(), "Trigger/Sweep")
        self.tabs.addTab(self._build_channel_tab(), "Channels")
        self.tabs.addTab(self._build_save_tab(), "Save")
        self.tabs.addTab(self._build_console_tab(), "Command")
        self.tabs.setEnabled(False)
        # Fixed height for the lower control area: keeps it from growing
        # with the mirror image (the mirror only stretches into the spare
        # space of the label above)
        self.tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.tabs)

    def _build_trigger_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)

        grid.addWidget(QLabel("Trigger mode:"), 0, 0)
        mode_row = QHBoxLayout()
        for mode in ("AUTO", "NORM", "SINGLE", "STOP"):
            btn = QPushButton(mode)
            btn.clicked.connect(
                lambda _=False, m=mode: self._call(
                    lambda: self.scope.set_trigger_mode(m)))
            mode_row.addWidget(btn)
        grid.addLayout(mode_row, 0, 1, 1, 3)

        grid.addWidget(QLabel("Source:"), 1, 0)
        self.trig_src = QComboBox()
        self.trig_src.addItems(list(self.scope.TRIGGER_SOURCES))
        self.trig_src.setCurrentText(self.cfg.scope_trig_source)  # std: EX
        grid.addWidget(self.trig_src, 1, 1)
        grid.addWidget(QLabel("Level [mV]:"), 1, 2)
        self.trig_level = QSpinBox()
        self.trig_level.setRange(-5000, 5000)
        self.trig_level.setSingleStep(10)
        self.trig_level.setValue(self.cfg.scope_trig_level_mv)  # std 410 mV
        grid.addWidget(self.trig_level, 1, 3)
        grid.addWidget(QLabel("Slope:"), 2, 0)
        self.trig_slope = QComboBox()
        self.trig_slope.addItems(["POS", "NEG"])
        self.trig_slope.setCurrentText(self.cfg.scope_trig_slope)
        grid.addWidget(self.trig_slope, 2, 1)
        grid.addWidget(QLabel("Coupling:"), 2, 2)
        self.trig_coupling = QComboBox()
        self.trig_coupling.addItems(TRIG_COUPLINGS)
        self.trig_coupling.setCurrentText(self.cfg.scope_trig_coupling)
        grid.addWidget(self.trig_coupling, 2, 3)
        apply_btn = QPushButton("Apply Edge Trigger")
        apply_btn.clicked.connect(self._apply_trigger)
        grid.addWidget(apply_btn, 3, 2, 1, 2)

        grid.addWidget(QLabel("Timebase:"), 3, 0)
        self.tdiv_combo = QComboBox()
        self.tdiv_combo.addItems(TDIV_PRESETS)
        self.tdiv_combo.setCurrentText(self.cfg.scope_tdiv)  # std 1 ns/div
        grid.addWidget(self.tdiv_combo, 3, 1)

        grid.addWidget(QLabel("Horiz. offset:"), 4, 0)
        self.trdl_spin = QDoubleSpinBox()
        self.trdl_spin.setRange(-1e6, 1e6)
        self.trdl_spin.setDecimals(2)
        self.trdl_spin.setSuffix(" ns")
        # standard -53 ns: at 1 ns/div the display covers +48 to +58 ns
        # (the signal comes about 52 ns after the laser trigger; see
        # config.py)
        self.trdl_spin.setValue(
            float(self.cfg.scope_trig_delay.upper().replace("NS", "")))
        grid.addWidget(self.trdl_spin, 4, 1)
        hax_btn = QPushButton("Apply Horizontal")
        hax_btn.setToolTip("Send timebase and horizontal offset (TRDL)")
        hax_btn.clicked.connect(self._apply_horizontal)
        grid.addWidget(hax_btn, 4, 2, 1, 2)

        self.seq_check = QCheckBox("Sequence")
        self.seq_check.setToolTip(
            "Fast back-to-back capture, 1 trigger = 1 segment")
        grid.addWidget(self.seq_check, 5, 0)
        self.seq_segments = QSpinBox()
        self.seq_segments.setRange(2, 100000)
        self.seq_segments.setValue(200)
        self.seq_segments.setToolTip("Number of segments")
        grid.addWidget(self.seq_segments, 5, 1)
        seq_btn = QPushButton("Apply")
        seq_btn.clicked.connect(self._apply_sequence)
        grid.addWidget(seq_btn, 5, 2)
        clsw_btn = QPushButton("Clear Sweeps (CLSW)")
        clsw_btn.setToolTip("Reset the averaging accumulation")
        clsw_btn.clicked.connect(
            lambda: self._call(self.scope.clear_sweeps))
        grid.addWidget(clsw_btn, 5, 3)

        defaults_btn = QPushButton("★ Apply Standard Settings ★")
        defaults_btn.setToolTip(
            "Send the standard conditions: "
            f"Trigger {self.cfg.scope_trig_source}/"
            f"{self.cfg.scope_trig_coupling}/{self.cfg.scope_trig_slope}/"
            f"{self.cfg.scope_trig_level_mv} mV, "
            f"{self.cfg.scope_tdiv.replace('NS', ' ns')}/div, "
            f"horizontal {self.cfg.scope_trig_delay.replace('NS', ' ns')}, "
            f"C1–C4 {self.cfg.scope_vdiv.replace('MV', ' mV')}/div, "
            f"offset {self.cfg.scope_ch_offset_mv:+d} mV, DC50Ω, "
            "Averaging OFF")
        defaults_btn.setStyleSheet("font-weight: bold;")
        defaults_btn.clicked.connect(self._apply_standard_defaults)
        grid.addWidget(defaults_btn, 6, 0, 1, 4)
        grid.setRowStretch(7, 1)
        return w

    def _build_channel_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.addWidget(QLabel("CH"), 0, 0)
        grid.addWidget(QLabel("On"), 0, 1)
        grid.addWidget(QLabel("VDIV"), 0, 2)
        # Units go into the header: a suffix on the spin box, together
        # with the side-by-side arrows of the Windows 11 style, leaves too
        # little width and the digits get clipped
        grid.addWidget(QLabel("Offset [mV]"), 0, 3)
        grid.addWidget(QLabel("Coupling"), 0, 4)
        avg_head = QLabel("Avg")
        avg_head.setToolTip(
            "Pre-processing averaging (app.Acquisition.Cx.AverageSweeps).\n"
            "ON averages over the sweep count; OFF means no averaging (=1)")
        grid.addWidget(avg_head, 0, 5)
        grid.addWidget(QLabel("Sweeps"), 0, 6)
        grid.addWidget(QLabel(""), 0, 7)

        self.ch_widgets = {}
        self.ch_labels = {}
        for row, ch in enumerate(self.scope.CHANNELS, start=1):
            on_chk = QCheckBox()
            on_chk.setChecked(True)
            vdiv = QComboBox()
            vdiv.addItems(VDIV_PRESETS)
            vdiv.setCurrentText(self.cfg.scope_vdiv)  # standard 50 mV/div
            offset = QSpinBox()
            offset.setRange(-10000, 10000)
            offset.setSingleStep(10)
            # standard +100 mV: shifted for negative pulses, display
            # covers -300 to +100 mV
            offset.setValue(self.cfg.scope_ch_offset_mv)
            cpl = QComboBox()
            # default D50: avoids reflections on fast signals (spec chap. 2)
            cpl.addItems(COUPLINGS)
            cpl.setCurrentText(self.cfg.scope_coupling)
            avg_chk = QCheckBox()
            avg_chk.setToolTip(f"Pre-processing averaging ON/OFF for {ch}")
            avg_sweeps = QSpinBox()
            avg_sweeps.setRange(2, 1000000)
            avg_sweeps.setValue(100)
            avg_sweeps.setEnabled(False)  # disabled while averaging is OFF
            # No up/down buttons: the Windows 11 style draws side-by-side
            # arrows that take width, and the digits get clipped in a
            # narrow column (the width goes to the offset field instead)
            avg_sweeps.setButtonSymbols(QAbstractSpinBox.NoButtons)
            avg_sweeps.setToolTip("Number of sweeps to average")
            avg_chk.toggled.connect(avg_sweeps.setEnabled)
            apply_btn = QPushButton("Apply")
            apply_btn.setMinimumWidth(56)
            apply_btn.clicked.connect(
                lambda _=False, c=ch: self._apply_channel(c))
            # the readout-pad mapping (C1=J8 etc.) is appended to the
            # label by refresh_sensor
            self.ch_labels[ch] = QLabel(ch)
            grid.addWidget(self.ch_labels[ch], row, 0)
            grid.addWidget(on_chk, row, 1)
            grid.addWidget(vdiv, row, 2)
            grid.addWidget(offset, row, 3)
            grid.addWidget(cpl, row, 4)
            grid.addWidget(avg_chk, row, 5)
            grid.addWidget(avg_sweeps, row, 6)
            grid.addWidget(apply_btn, row, 7)
            self.ch_widgets[ch] = (on_chk, vdiv, offset, cpl,
                                   avg_chk, avg_sweeps)
        grid.setRowStretch(len(self.scope.CHANNELS) + 1, 1)
        return w

    def _build_save_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)

        save_dir_label = QLabel("Save dir:")
        save_dir_label.setToolTip("Save folder on the oscilloscope itself")
        grid.addWidget(save_dir_label, 0, 0)
        self.save_dir_edit = QLineEdit(self._save_dir_default)
        grid.addWidget(self.save_dir_edit, 0, 1, 1, 3)
        prefix_label = QLabel("Prefix:")
        prefix_label.setToolTip("Leading part (prefix) of the saved file names")
        grid.addWidget(prefix_label, 1, 0)
        self.prefix_edit = QLineEdit("aclgad")
        grid.addWidget(self.prefix_edit, 1, 1)
        grid.addWidget(QLabel("Format:"), 1, 2)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["Binary", "ASCII", "MATLAB"])
        grid.addWidget(self.fmt_combo, 1, 3)
        grid.addWidget(QLabel("Source:"), 2, 0)
        self.src_combo = QComboBox()
        self.src_combo.addItems(["AllDisplayed", "C1", "C2", "C3", "C4"])
        grid.addWidget(self.src_combo, 2, 1)
        setup_btn = QPushButton("Send Save Setup")
        setup_btn.clicked.connect(self._apply_save_setup)
        grid.addWidget(setup_btn, 2, 2, 1, 2)

        save_now_btn = QPushButton("Save Now")
        save_now_btn.clicked.connect(
            lambda: self._call(self.scope.save_waveform_now))
        grid.addWidget(save_now_btn, 3, 0)
        grid.addWidget(QLabel("Autosave:"), 3, 1)
        self.autosave_combo = QComboBox()
        self.autosave_combo.addItems(["Off", "Fill", "Wrap"])
        grid.addWidget(self.autosave_combo, 3, 2)
        autosave_btn = QPushButton("Apply")
        autosave_btn.clicked.connect(
            lambda: self._call(lambda: self.scope.set_autosave(
                self.autosave_combo.currentText())))
        grid.addWidget(autosave_btn, 3, 3)

        grid.addWidget(QLabel("Panel setup memory:"), 4, 0)
        self.setup_slot = QSpinBox()
        self.setup_slot.setRange(1, 6)
        grid.addWidget(self.setup_slot, 4, 1)
        sav_btn = QPushButton("Save (*SAV)")
        sav_btn.clicked.connect(
            lambda: self._call(lambda: self.scope.save_panel_setup(
                self.setup_slot.value())))
        rcl_btn = QPushButton("Recall (*RCL)")
        rcl_btn.clicked.connect(
            lambda: self._call(lambda: self.scope.recall_panel_setup(
                self.setup_slot.value())))
        grid.addWidget(sav_btn, 4, 2)
        grid.addWidget(rcl_btn, 4, 3)
        grid.setRowStretch(5, 1)
        return w

    def _build_console_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        row = QHBoxLayout()
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText(
            "Remote command (e.g. TRMD?, "
            "VBS? 'return=app.Acquisition.TriggerMode')")
        self.cmd_edit.returnPressed.connect(self._send_query)
        write_btn = QPushButton("Send")
        write_btn.setToolTip("Do not wait for a response (for set commands)")
        write_btn.clicked.connect(self._send_write)
        query_btn = QPushButton("Query")
        query_btn.setToolTip(
            "Wait for a response (for ? commands). Shown in the log")
        query_btn.clicked.connect(self._send_query)
        row.addWidget(self.cmd_edit, 1)
        row.addWidget(write_btn)
        row.addWidget(query_btn)
        layout.addLayout(row)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.scope.connected:
            self._stop_mirror()
            self.caller.call(self.scope.disconnect,
                             on_result=lambda _: self._on_disconnected())
            return
        self.scope.resource = self.addr_edit.text().strip()
        self.connect_btn.setEnabled(False)
        self.caller.call(self.scope.connect,
                         on_result=lambda _: self._on_connected(),
                         on_error=self._on_conn_error)

    def _on_connected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect")
        self.conn_label.setText("Connected")
        self.conn_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        self.tabs.setEnabled(True)
        self.mirror_btn.setEnabled(True)

    def _on_disconnected(self):
        self.connect_btn.setText("Connect")
        self.conn_label.setText("Disconnected")
        self.conn_label.setStyleSheet("color: gray;")
        self.tabs.setEnabled(False)
        self.mirror_btn.setEnabled(False)

    def _on_conn_error(self, exc: Exception):
        self.connect_btn.setEnabled(True)
        QMessageBox.warning(self, "Scope Connection Error", str(exc))

    # ------------------------------------------------------------------
    # Mirroring
    # ------------------------------------------------------------------
    def _toggle_mirror(self):
        if self._mirror is not None:
            self._stop_mirror()
        else:
            self._mirror = ScreenMirror(
                self.scope, self.mirror_interval.currentData())
            self._mirror.image_ready.connect(self._show_screen)
            self._mirror.error.connect(self._mirror_error)
            self._mirror.start()
            self.mirror_btn.setText("Stop Mirror")

    def _stop_mirror(self):
        if self._mirror is not None:
            self._mirror.stop()
            self._mirror = None
        self.mirror_btn.setText("Start Mirror")

    def _mirror_interval_changed(self):
        if self._mirror is not None:
            self._mirror.interval_s = self.mirror_interval.currentData()

    def _show_screen(self, png: bytes):
        pix = QPixmap()
        if pix.loadFromData(png):
            self.screen_label.setPixmap(pix.scaled(
                self.screen_label.size(), Qt.KeepAspectRatio,
                Qt.SmoothTransformation))

    def _mirror_error(self, msg: str):
        self._stop_mirror()
        # Word-wrap so a long error text does not widen the panel
        self.screen_label.setWordWrap(True)
        self.screen_label.setText(f"Mirror error: {msg}")

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------
    def _call(self, fn):
        """Async call with a connection check (common path)."""
        if not self.scope.connected:
            return
        self.caller.call(fn, on_error=self._show_error)

    def _apply_trigger(self):
        src = self.trig_src.currentText()
        level = f"{self.trig_level.value()}MV"
        slope = self.trig_slope.currentText()
        coupling = self.trig_coupling.currentText()
        self._call(lambda: self.scope.set_edge_trigger(
            src, level, slope, coupling))

    def _apply_horizontal(self):
        tdiv = self.tdiv_combo.currentText()
        delay = f"{self.trdl_spin.value():g}NS"

        def apply():
            self.scope.set_tdiv(tdiv)
            self.scope.set_trigger_delay(delay)
        self._call(apply)

    def _apply_standard_defaults(self):
        """Send the standard measurement conditions (the config's scope_*
        set) to the instrument in one go.

        The UI values are also reset to the standards, so the screen and
        the instrument settings never diverge.
        """
        cfg = self.cfg
        self.trig_src.setCurrentText(cfg.scope_trig_source)
        self.trig_level.setValue(cfg.scope_trig_level_mv)
        self.trig_slope.setCurrentText(cfg.scope_trig_slope)
        self.trig_coupling.setCurrentText(cfg.scope_trig_coupling)
        self.tdiv_combo.setCurrentText(cfg.scope_tdiv)
        self.trdl_spin.setValue(
            float(cfg.scope_trig_delay.upper().replace("NS", "")))
        # Averaging is always OFF in the standard conditions: without
        # real-time waveforms the completion check of SINGLE/sequence
        # acquisitions does not work (confirmed on hardware 2026-07)
        for ch in self.scope.CHANNELS:
            on_chk, vdiv, offset, cpl, avg_chk, _avg_sweeps = \
                self.ch_widgets[ch]
            on_chk.setChecked(True)
            vdiv.setCurrentText(cfg.scope_vdiv)
            offset.setValue(cfg.scope_ch_offset_mv)
            cpl.setCurrentText(cfg.scope_coupling)
            avg_chk.setChecked(False)

        def apply():
            self.scope.set_edge_trigger(
                cfg.scope_trig_source, f"{cfg.scope_trig_level_mv}MV",
                cfg.scope_trig_slope, cfg.scope_trig_coupling)
            self.scope.set_tdiv(cfg.scope_tdiv)
            self.scope.set_trigger_delay(cfg.scope_trig_delay)
            for ch in self.scope.CHANNELS:
                self.scope.set_channel(
                    ch, on=True, vdiv=cfg.scope_vdiv,
                    offset=f"{cfg.scope_ch_offset_mv}MV",
                    coupling=cfg.scope_coupling)
            self.scope.set_averaging_off()
        self._call(apply)

    def _apply_sequence(self):
        on = self.seq_check.isChecked()
        n = self.seq_segments.value()
        self._call(lambda: self.scope.set_sequence(on, n))

    def _apply_channel(self, ch: str):
        on_chk, vdiv, offset, cpl, avg_chk, avg_sweeps = self.ch_widgets[ch]
        on = on_chk.isChecked()
        vdiv_v = vdiv.currentText()
        offset_v = f"{offset.value()}MV"
        cpl_v = cpl.currentText()
        # averaging OFF corresponds to AverageSweeps=1 (no averaging)
        sweeps = avg_sweeps.value() if avg_chk.isChecked() else 1

        def apply():
            self.scope.set_channel(ch, on=on, vdiv=vdiv_v,
                                   offset=offset_v, coupling=cpl_v)
            self.scope.set_channel_average(ch, sweeps)
        self._call(apply)

    def _apply_save_setup(self):
        directory = self.save_dir_edit.text().strip()
        prefix = self.prefix_edit.text().strip() or "aclgad"
        fmt = self.fmt_combo.currentText()
        source = self.src_combo.currentText()
        self._call(lambda: self.scope.configure_waveform_save(
            directory, prefix, source=source, fmt=fmt))

    def _send_write(self):
        cmd = self.cmd_edit.text().strip()
        if cmd:
            self._call(lambda: self.scope.write(cmd))

    def _send_query(self):
        cmd = self.cmd_edit.text().strip()
        if cmd:
            # the response reaches the log panel via the device's log
            self._call(lambda: self.scope.query(cmd))

    def _show_error(self, exc: Exception):
        QMessageBox.warning(self, "Scope Error", str(exc))

    # ------------------------------------------------------------------
    def emergency_stop(self):
        if self.scope.connected:
            self.caller.call(self.scope.stop_acquisition)

    def shutdown(self):
        self._stop_mirror()
        if self.scope.connected:
            try:
                self.scope.disconnect()
            except Exception:
                pass
        self.caller.shutdown()
