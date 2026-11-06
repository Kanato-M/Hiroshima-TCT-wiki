# -*- coding: utf-8 -*-
"""Dialog for editing sensor / DUT profiles.

When a board or sensor is swapped, its geometry (pitch, electrode width,
strip length) and readout mapping (scope CH ↔ pad / electrode position)
can be registered and switched from the GUI (sensors.py). Opening an
existing sensor and using "Save As New Sensor" creates a new sensor from
a copy.

Sensor types (added 2026-10-16):
  Strip: as before (pitch, electrode width, frame offset, exposed length,
         CH ↔ electrode center Y)
  Pixel: pixel pitch and bonding-pad size, CH ↔ pixel (column, row). The
         reference CH is (0, 0) and the logical origin is the +X/+Y corner
         of its pad (same coordinates as the scan of 2026-10-15). Find
         Origin, Full Calibration, the default scan range and "Go to
         pixel" follow from this geometry (docstring of sensors.py)

The default scan range and the Full Calibration measurement points can
be derived from the geometry values with "Derive from geometry"
(sensors.derive_scan / derive_align). They may also be edited by hand.
"""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout)

import sensors


def _spin(value: float, lo: float = -1e6, hi: float = 1e6,
          dec: int = 1) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(float(value))
    return s


def _ispin(value: int, lo: int = -20, hi: int = 20) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(value))
    return s


class SensorDialog(QDialog):
    def __init__(self, registry: sensors.SensorRegistry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.profile = copy.deepcopy(registry.current())
        self.setWindowTitle("Edit Sensor / DUT")
        self.setMinimumWidth(600)
        self._build_ui()
        self._load(self.profile)

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        top = QGridLayout()
        top.addWidget(QLabel("Name:"), 0, 0)
        self.name_edit = QLineEdit()
        top.addWidget(self.name_edit, 0, 1)
        top.addWidget(QLabel("Note:"), 1, 0)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("board name, die ID, remarks, ...")
        top.addWidget(self.note_edit, 1, 1)
        top.addWidget(QLabel("Type:"), 2, 0)
        self.type_combo = QComboBox()
        for key, label in sensors.SENSOR_TYPES:
            self.type_combo.addItem(label, key)
        self.type_combo.setToolTip(
            "Strip: electrode center Y per CH. Origin = frame edge X=0, reference electrode center Y=0\n"
            "Pixel: pixel (column, row) per CH. Origin = +X/+Y corner of the reference pad\n"
            "Find Origin, Full Calibration, default scan range and the analysis kind (2dYX / pixel) switch accordingly")
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        top.addWidget(self.type_combo, 2, 1)
        layout.addLayout(top)

        # --- geometry (strip) ---
        self.geo_box = QGroupBox("Geometry [µm] (logical origin = frame edge, X=0)")
        g = QGridLayout(self.geo_box)
        self.pitch = _spin(500, 1, 1e5)
        self.width = _spin(50, 1, 1e5)
        self.x_off = _spin(632, 0, 1e5)
        self.length = _spin(9318, 1, 1e6)
        self.gap = _spin(98, 0, 1e5)
        for i, (label, w, tip) in enumerate((
                ("Strip pitch", self.pitch, "Spacing of neighboring electrode centers"),
                ("Electrode (metal) width", self.width,
                 "The signal vanishes directly above the electrode. Reference for the\n"
                 "dip detection and the probe positions"),
                ("Frame edge → exposed start", self.x_off,
                 "Distance from the frame (GND) edge to the readout-side end of the\n"
                 "exposed strip (standard sensor: ⓪+①+② = 632 µm). Find Origin puts\n"
                 "X=0 this far outside the signal edge"),
                ("Exposed strip length", self.length,
                 "Length of the exposed strip hit by the laser (scan region)"),
                ("End gap", self.gap,
                 "Gap between the far end of the strip and the neighboring region"))):
            lab = QLabel(label + ":")
            lab.setToolTip(tip)
            w.setToolTip(tip)
            g.addWidget(lab, i // 2, (i % 2) * 2)
            g.addWidget(w, i // 2, (i % 2) * 2 + 1)
        layout.addWidget(self.geo_box)

        # --- geometry (pixel) ---
        self.pix_box = QGroupBox("Pixel geometry [µm] (logical origin = +X/+Y corner of the reference CH's pad)")
        g = QGridLayout(self.pix_box)
        self.pix_pitch = _spin(500, 1, 1e5)
        self.pad = _spin(100, 1, 1e5)
        for i, (label, w, tip) in enumerate((
                ("Pixel pitch", self.pix_pitch, "Spacing of neighboring pixel centers (= pad centers)"),
                ("Pad size", self.pad,
                 "Side of the (square, metal = signal-free) bonding pad. The ruler of\n"
                 "Find Origin (Y and X dip widths). 100 µm for pixel#3 of 2026-10-15"))):
            lab = QLabel(label + ":")
            lab.setToolTip(tip)
            w.setToolTip(tip)
            g.addWidget(lab, 0, i * 2)
            g.addWidget(w, 0, i * 2 + 1)
        g.addWidget(QLabel("Pixel center = (−pad/2 − col×pitch, −pad/2 − row×pitch). Columns increase towards −X, "
                           "rows towards −Y (reference CH = col 0, row 0)"), 1, 0, 1, 4)
        layout.addWidget(self.pix_box)

        # --- readout ---
        ro = QGroupBox("Readout (scope CH ↔ electrode)")
        g = QGridLayout(ro)
        self.ro_header = {}
        for col, text in enumerate(("CH", "Use", "Pad",
                                    "Electrode center Y [µm]", "Col", "Row")):
            lab = QLabel(text)
            g.addWidget(lab, 0, col)
            self.ro_header[text] = lab
        self.ro_rows = {}
        for row, ch in enumerate(sensors.CHANNELS, start=1):
            use = QCheckBox()
            pad = QLineEdit()
            y = _spin(0.0)
            y.setToolTip("Y position with the electrode center of the reference CH at 0\n"
                         "(standard layout: Y decreases with increasing CH number)")
            col_spin = _ispin(0)
            row_spin = _ispin(0)
            col_spin.setToolTip("Pixel column (+1 towards −X)")
            row_spin.setToolTip("Pixel row (+1 towards −Y)")
            g.addWidget(QLabel(ch), row, 0)
            g.addWidget(use, row, 1)
            g.addWidget(pad, row, 2)
            g.addWidget(y, row, 3)
            g.addWidget(col_spin, row, 4)
            g.addWidget(row_spin, row, 5)
            self.ro_rows[ch] = (use, pad, y, col_spin, row_spin)
        g.addWidget(QLabel("Reference CH:"), 5, 0)
        self.ref_combo = QComboBox()
        self.ref_combo.addItems(sensors.CHANNELS)
        self.ref_combo.setToolTip(
            "CH whose electrode center (Y=0) / pad corner (origin) is found by Find Origin")
        g.addWidget(self.ref_combo, 5, 1, 1, 2)
        self.fill_btn = QPushButton("Fill Y from pitch")
        self.fill_btn.clicked.connect(self._fill_readout_y)
        g.addWidget(self.fill_btn, 5, 3)
        self.fill_pix_btn = QPushButton("Standard 2x2 layout")
        self.fill_pix_btn.setToolTip("C1 (0,0), C2 (1,0) = −X neighbor, C3 (0,1) = −Y neighbor, C4 (1,1) = diagonal\n"
                                     "(layout measured in the pixel#3 scan of 2026-10-15)")
        self.fill_pix_btn.clicked.connect(self._fill_pixel_layout)
        g.addWidget(self.fill_pix_btn, 5, 4, 1, 2)
        layout.addWidget(ro)

        # --- default scan range / calibration points ---
        sc = QGroupBox("Default scan range / calibration points [µm]")
        g = QGridLayout(sc)
        for col, text in ((1, "Start"), (2, "Stop"), (3, "Step")):
            g.addWidget(QLabel(text), 0, col)
        self.scan_spins = {}
        for row, (label, prefix) in enumerate(
                (("Fast axis Y (across)", "fast"), ("Slow axis X (along)", "slow")),
                start=1):
            g.addWidget(QLabel(label + ":"), row, 0)
            for col, key in enumerate(("start", "stop", "step"), start=1):
                lo = 0.1 if key == "step" else -1e6
                spin = _spin(0.0 if key != "step" else 10.0, lo)
                g.addWidget(spin, row, col)
                self.scan_spins[f"{prefix}_{key}"] = spin
        self.x_points_label = QLabel("Calib. X points:")
        g.addWidget(self.x_points_label, 3, 0)
        self.x_points_edit = QLineEdit()
        self.x_points_edit.setToolTip(
            "X positions (comma separated) where Full Calibration measures the\n"
            "electrode center Y and the focus Z. Used for the dY/dX and dZ/dX fits")
        g.addWidget(self.x_points_edit, 3, 1, 1, 3)
        self.y_points_label = QLabel("Calib. Y points:")
        g.addWidget(self.y_points_label, 4, 0)
        self.y_points_edit = QLineEdit()
        self.y_points_edit.setToolTip(
            "Electrode Y positions measured by Full Calibration and the CH that reads\n"
            "that electrode, as \"Y:CH\" comma separated (e.g. -500:C2, -1500:C4).\n"
            "Used for the dZ/dY fit and the pitch check")
        g.addWidget(self.y_points_edit, 4, 1, 1, 3)
        self.pads_label = QLabel("Calib. pads (auto):")
        g.addWidget(self.pads_label, 3, 0)
        self.pads_text = QLabel("")
        self.pads_text.setWordWrap(True)
        self.pads_text.setToolTip("Pad centers of the readout pixels other than the reference (read on their CH)\n"
                                  "measured by Full Calibration. Derived from the columns/rows of the readout table")
        g.addWidget(self.pads_text, 3, 1, 2, 3)
        self.y_probe = _spin(80, 0, 1e4)
        self.x_probe = _spin(50, 0, 1e4)
        self.y_half = _spin(150, 10, 1e4)
        self.probe_labels = []
        for col, (label, w, tip) in enumerate((
                ("Y offset for Z", self.y_probe,
                 "Y offset from the electrode / pad center where the Z focus is measured\n"
                 "(on the silicon beside the electrode / pad; well beyond the half width)"),
                ("Y offset for X edge", self.x_probe,
                 "Y offset from the electrode center where the X edge is detected\n"
                 "(close to the electrode, within the Y width of the bond pad; unused for pixels)"),
                ("Y sweep half-width", self.y_half,
                 "Y sweep range (±) of the electrode / pad dip detection. Larger than the\n"
                 "coarse-approach error, but small enough not to include the next electrode / pad"))):
            lab = QLabel(label + ":")
            lab.setToolTip(tip)
            w.setToolTip(tip)
            g.addWidget(lab, 5 + col, 0)
            g.addWidget(w, 5 + col, 1)
            self.probe_labels.append(lab)
        auto_btn = QPushButton("Derive from geometry")
        auto_btn.setToolTip("Recompute the scan range and the calibration points\n"
                            "from the geometry and readout values above")
        auto_btn.clicked.connect(self._auto_derive)
        g.addWidget(auto_btn, 5, 2, 1, 2)
        layout.addWidget(sc)

        # --- calibration / origin state ---
        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.state_label)

        btns = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(lambda: self._save(as_new=False))
        new_btn = QPushButton("Save As New Sensor")
        new_btn.setToolTip("Register the entered values as a new sensor.\n"
                           "Tilt coefficients, saved origin and saved positions are not copied")
        new_btn.clicked.connect(lambda: self._save(as_new=True))
        del_btn = QPushButton("Delete This Sensor")
        del_btn.clicked.connect(self._delete)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(save_btn)
        btns.addWidget(new_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    # ------------------------------------------------------------------
    def _is_pixel(self) -> bool:
        return self.type_combo.currentData() == "pixel"

    def _type_changed(self):
        pixel = self._is_pixel()
        self.geo_box.setVisible(not pixel)
        self.pix_box.setVisible(pixel)
        self.ro_header["Electrode center Y [µm]"].setVisible(not pixel)
        self.ro_header["Col"].setVisible(pixel)
        self.ro_header["Row"].setVisible(pixel)
        for use, pad, y, col_spin, row_spin in self.ro_rows.values():
            y.setVisible(not pixel)
            col_spin.setVisible(pixel)
            row_spin.setVisible(pixel)
        self.fill_btn.setVisible(not pixel)
        self.fill_pix_btn.setVisible(pixel)
        for w in (self.x_points_label, self.x_points_edit,
                  self.y_points_label, self.y_points_edit):
            w.setVisible(not pixel)
        for w in (self.pads_label, self.pads_text):
            w.setVisible(pixel)
        self.x_probe.setVisible(not pixel)
        self.probe_labels[1].setVisible(not pixel)
        if pixel:
            self._update_pads_text()

    def _update_pads_text(self):
        try:
            p = self._geometry_profile()
            al = sensors.derive_align(p)
            self.pads_text.setText(
                ", ".join(f"{ch} ({x:.0f}, {y:.0f})" for ch, x, y in al.get("pads", []))
                or "none (no readout CH other than the reference)")
        except Exception as exc:          # inconsistent input while editing: display only
            self.pads_text.setText(f"({exc})")

    # ------------------------------------------------------------------
    def _load(self, p: dict):
        self.name_edit.setText(p.get("name", ""))
        self.note_edit.setText(p.get("note", ""))
        idx = self.type_combo.findData(sensors.sensor_type(p))
        self.type_combo.setCurrentIndex(max(idx, 0))
        self.pitch.setValue(p["pitch_um"])
        self.pix_pitch.setValue(p["pitch_um"])
        self.pad.setValue(p.get("pad_um", 100.0))
        self.width.setValue(p["electrode_width_um"])
        self.x_off.setValue(p["x_frame_offset_um"])
        self.length.setValue(p["strip_length_um"])
        self.gap.setValue(p.get("x_end_gap_um", 0.0))
        for ch, (use, pad, y, col_spin, row_spin) in self.ro_rows.items():
            entry = p["readout"].get(ch, {})
            use.setChecked(bool(entry.get("use", ch in p["readout"])))
            pad.setText(entry.get("pad", ""))
            y.setValue(entry.get("y_um", 0.0))
            col_spin.setValue(int(entry.get("col", 0)))
            row_spin.setValue(int(entry.get("row", 0)))
        self.ref_combo.setCurrentText(p.get("ref_channel", "C1"))
        self._load_scan_align(p)
        self._type_changed()
        dzdx, dzdy, dydx, src = self.registry.coefficients(p["id"]) \
            if p["id"] in self.registry.sensors else (0, 0, 0, "uncalibrated")
        org = p.get("machine_origin")
        org_text = "none" if not org else \
            "X={:.0f} Y={:.0f} Z={:.0f} µm ({})".format(
                *org["pos_um"], str(org.get("registered_at", ""))[:16])
        self.state_label.setText(
            f"Tilt coefficients: dZ/dX={dzdx * 1000:+.2f}, dZ/dY={dzdy * 1000:+.2f}, "
            f"dY/dX={dydx * 1000:+.2f} µm/mm [{src}]\n"
            f"Saved origin (machine): {org_text} / "
            f"saved positions: {len(p.get('positions', {}))}")

    def _load_scan_align(self, p: dict):
        for key, spin in self.scan_spins.items():
            spin.setValue(p["scan"][key])
        al = p["align"]
        self.x_points_edit.setText(
            ", ".join(f"{x:g}" for x in al.get("x_points", [])))
        self.y_points_edit.setText(
            ", ".join(f"{y:g}:{ch}" for y, ch in al.get("y_points", [])))
        self.pads_text.setText(
            ", ".join(f"{ch} ({x:.0f}, {y:.0f})" for ch, x, y in al.get("pads", [])))
        self.y_probe.setValue(al.get("y_probe_off_um", 80.0))
        self.x_probe.setValue(al.get("x_probe_off_um", 50.0))
        self.y_half.setValue(al.get("y_half_um", 150.0))

    def _geometry_profile(self) -> dict:
        """Profile with the geometry / readout from the input fields (scan/align unchanged)."""
        p = copy.deepcopy(self.profile)
        p["name"] = self.name_edit.text().strip()
        p["note"] = self.note_edit.text().strip()
        pixel = self._is_pixel()
        p["sensor_type"] = "pixel" if pixel else "strip"
        p["pitch_um"] = self.pix_pitch.value() if pixel else self.pitch.value()
        p["pad_um"] = self.pad.value()
        p["electrode_width_um"] = self.width.value()
        p["x_frame_offset_um"] = self.x_off.value()
        p["strip_length_um"] = self.length.value()
        p["x_end_gap_um"] = self.gap.value()
        ref = self.ref_combo.currentText()
        p["ref_channel"] = ref
        if pixel:
            # re-reference the reference CH to (0, 0) (convention: origin = corner of the reference pad)
            c0 = self.ro_rows[ref][3].value()
            r0 = self.ro_rows[ref][4].value()
            p["readout"] = {}
            for ch, (use, pad, y, col_spin, row_spin) in self.ro_rows.items():
                col, row = col_spin.value() - c0, row_spin.value() - r0
                p["readout"][ch] = {
                    "pad": pad.text().strip(), "use": use.isChecked(),
                    "col": col, "row": row,
                    "y_um": -p["pad_um"] / 2.0 - row * p["pitch_um"]}
        else:
            # Y is re-referenced to the electrode center of the reference CH (convention)
            y_ref = self.ro_rows[ref][2].value()
            p["readout"] = {
                ch: {"pad": pad.text().strip(), "y_um": y.value() - y_ref,
                     "use": use.isChecked(), "col": 0, "row": 0}
                for ch, (use, pad, y, _c, _r) in self.ro_rows.items()}
        return p

    def _fill_readout_y(self):
        ref_idx = sensors.CHANNELS.index(self.ref_combo.currentText())
        for i, ch in enumerate(sensors.CHANNELS):
            self.ro_rows[ch][2].setValue(-(i - ref_idx) * self.pitch.value())

    def _fill_pixel_layout(self):
        for ch, (col, row) in sensors.PIXEL_2X2_LAYOUT.items():
            self.ro_rows[ch][3].setValue(col)
            self.ro_rows[ch][4].setValue(row)
        self.ref_combo.setCurrentText("C1")
        self._update_pads_text()

    def _auto_derive(self):
        p = self._geometry_profile()
        p["scan"] = sensors.derive_scan(p)
        p["align"] = sensors.derive_align(p)
        self._load_scan_align(p)

    def _collect(self) -> dict:
        p = self._geometry_profile()
        p["scan"] = {key: spin.value()
                     for key, spin in self.scan_spins.items()}
        if self._is_pixel():
            al = sensors.derive_align(p)
            al["y_probe_off_um"] = self.y_probe.value()
            al["x_probe_off_um"] = 0.0
            al["y_half_um"] = self.y_half.value()
            p["align"] = al
            if self.y_probe.value() <= self.pad.value() / 2.0:
                raise ValueError("The Y offset for Z is not larger than half the pad size "
                                 "(the focus would be measured on the pad)")
            if self.y_half.value() <= self.pad.value() / 2.0:
                raise ValueError("The Y sweep half-width is not larger than half the pad size "
                                 "(the dip would not fit into the sweep)")
            sensors.validate(p)
            return p
        try:
            x_points = [float(t) for t in
                        self.x_points_edit.text().split(",") if t.strip()]
        except ValueError:
            raise ValueError("Calib. X points must be numbers separated by "
                             "commas") from None
        y_points = []
        for token in self.y_points_edit.text().split(","):
            if not token.strip():
                continue
            y_text, sep, ch = token.partition(":")
            ch = ch.strip().upper()
            try:
                y = float(y_text)
            except ValueError:
                y = None
            if y is None or not sep or ch not in sensors.CHANNELS:
                raise ValueError(
                    f"Invalid format of a Calib. Y point: {token.strip()!r} "
                    "(e.g. -500:C2)")
            y_points.append([y, ch])
        p["align"] = {
            "x_points": x_points, "y_points": y_points,
            "y_probe_off_um": self.y_probe.value(),
            "x_probe_off_um": self.x_probe.value(),
            "y_half_um": self.y_half.value(),
        }
        if self.y_probe.value() <= self.width.value() / 2.0:
            raise ValueError("The Y offset for Z is not larger than half the electrode width "
                             "(the focus would be measured on the electrode)")
        sensors.validate(p)
        return p

    # ------------------------------------------------------------------
    def _save(self, as_new: bool):
        try:
            p = self._collect()
        except ValueError as exc:
            QMessageBox.warning(self, "Input error", str(exc))
            return
        if as_new:
            if p["name"] in (n for _sid, n in self.registry.items()):
                QMessageBox.warning(
                    self, "Input error",
                    "A sensor with the same name already exists. Change the name")
                return
            p["id"] = self.registry.new_id(p["name"])
            p["calibration"] = None
            p["machine_origin"] = None
            p["positions"] = {}
        self.registry.upsert(p, select=True)
        self.accept()

    def _delete(self):
        ret = QMessageBox.question(
            self, "Confirm deletion",
            f"Delete the sensor \"{self.profile['name']}\"?\n"
            "Its tilt coefficients, saved origin and saved positions are deleted too.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            self.registry.remove(self.profile["id"])
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot delete", str(exc))
            return
        self.accept()
