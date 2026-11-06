# -*- coding: utf-8 -*-
"""Focus camera panel.

- live view of the USB camera (to check irradiation position and focus)
- crosshair overlay (alignment reference for the laser spot)
- gain adjustment
- snapshot saving (to document measurement conditions)

The camera of this setup has no autofocus (manual-focus lens), so the
AF / focus position / exposure / sharpness controls were removed in
2026-10.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QSizePolicy,
                               QSpinBox, QVBoxLayout)

from .widgets import ElidedLabel
from .worker import FrameGrabber


class CameraPanel(QGroupBox):
    def __init__(self, camera, capture_dir: str, parent=None):
        super().__init__("Focus Camera", parent)
        self.camera = camera
        self._capture_dir = Path(capture_dir)
        self._grabber: FrameGrabber | None = None
        self._last_frame: np.ndarray | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.view = QLabel("Camera stopped")
        # Keep the minimum height low (so other panels are not squeezed
        # at narrow high-DPI logical resolutions; spare height is
        # allocated to the video automatically by the stretch)
        self.view.setMinimumHeight(120)
        # Ignored: prevents the size of the displayed image from feeding
        # back into the layout's size request and making the panel grow
        # (same countermeasure as for the scope mirror screen)
        self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet(
            "background: black; color: gray; border: 1px solid #555;")
        layout.addWidget(self.view, 1)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Device:"))
        self.index_spin = QSpinBox()
        self.index_spin.setRange(0, 9)
        self.index_spin.setValue(self.camera.index)
        ctrl.addWidget(self.index_spin)
        self.start_btn = QPushButton("Start")
        self.start_btn.setMinimumWidth(56)
        self.start_btn.clicked.connect(self._toggle)
        ctrl.addWidget(self.start_btn)
        self.crosshair_chk = QCheckBox("Crosshair")
        self.crosshair_chk.setChecked(True)
        ctrl.addWidget(self.crosshair_chk)
        ctrl.addWidget(QLabel("Gain:"))
        self.gain_spin = QSpinBox()
        self.gain_spin.setRange(0, 255)
        self.gain_spin.setToolTip(
            "Camera gain (the actual applied value appears in the comm log)")
        self.gain_spin.valueChanged.connect(self._gain_changed)
        ctrl.addWidget(self.gain_spin)
        snap_btn = QPushButton("Snapshot")
        snap_btn.clicked.connect(self._snapshot)
        ctrl.addWidget(snap_btn)
        ctrl.addStretch(1)
        layout.addLayout(ctrl)

        # Result of saving a snapshot (no dialog, so work is not
        # interrupted)
        self.snap_label = ElidedLabel("")
        self.snap_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.snap_label)

    # ------------------------------------------------------------------
    def _toggle(self):
        if self._grabber is not None:
            self._stop()
            return
        self.camera.index = self.index_spin.value()
        try:
            self.camera.open()
        except Exception as exc:
            QMessageBox.warning(self, "Camera Error", str(exc))
            return
        self._grabber = FrameGrabber(self.camera, interval_s=0.05)
        self._grabber.frame_ready.connect(self._show_frame)
        self._grabber.error.connect(self._on_error)
        self._grabber.start()
        self.start_btn.setText("Stop")

    def _stop(self):
        if self._grabber is not None:
            self._grabber.stop()
            self._grabber = None
        try:
            self.camera.close()
        except Exception:
            pass
        self.start_btn.setText("Start")
        self.view.setText("Camera stopped")

    def _on_error(self, msg: str):
        self._stop()
        QMessageBox.warning(self, "Camera Error", msg)

    # ------------------------------------------------------------------
    def _gain_changed(self, value: int):
        # Property sets take only a few ms, so call directly from the
        # GUI thread
        if not self.camera.connected:
            return
        try:
            self.camera.set_gain(value)
        except Exception as exc:
            QMessageBox.warning(self, "Camera Setting Error", str(exc))

    # ------------------------------------------------------------------
    def _show_frame(self, frame: np.ndarray):
        self._last_frame = frame
        h, w = frame.shape[:2]
        # OpenCV frames are BGR, so wrap directly with QImage.Format_BGR888
        img = QImage(frame.data, w, h, frame.strides[0], QImage.Format_BGR888)
        pix = QPixmap.fromImage(img.copy())

        if self.crosshair_chk.isChecked():
            # center crosshair: the laser spot is aligned to it
            painter = QPainter(pix)
            pen = QPen(Qt.red)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(w // 2, 0, w // 2, h)
            painter.drawLine(0, h // 2, w, h // 2)
            painter.drawEllipse(w // 2 - 20, h // 2 - 20, 40, 40)
            painter.end()

        self.view.setPixmap(pix.scaled(
            self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _snapshot(self):
        if self._last_frame is None:
            self.snap_label.setText("Snapshot: no image "
                                    "(start the camera first)")
            return
        import cv2
        self._capture_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.datetime.now().strftime("snap_%Y%m%d_%H%M%S.png")
        path = self._capture_dir / name
        if cv2.imwrite(str(path), self._last_frame):
            self.snap_label.setText(f"Saved: {path}")
        else:
            QMessageBox.warning(self, "Save Failed",
                                f"Cannot save:\n{path}")

    # ------------------------------------------------------------------
    def shutdown(self):
        self._stop()
