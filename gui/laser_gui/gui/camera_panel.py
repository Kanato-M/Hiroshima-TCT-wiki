# -*- coding: utf-8 -*-
"""フォーカスカメラパネル。

- USBカメラのライブ表示 (照射位置・フォーカスの確認用)
- 十字線オーバーレイ (レーザースポットの位置合わせ基準)
- ゲイン調整
- スナップショット保存 (測定条件の記録用)

本機のカメラはオートフォーカス非対応 (手動フォーカスレンズ) のため、
AF・フォーカス位置・露出・鮮鋭度表示の操作は 2026-10 に削除した。
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
        super().__init__("フォーカスカメラ", parent)
        self.camera = camera
        self._capture_dir = Path(capture_dir)
        self._grabber: FrameGrabber | None = None
        self._last_frame: np.ndarray | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.view = QLabel("カメラ停止中")
        # 最小高は低めにする (高DPIの狭い論理解像度でも他パネルを押し潰さない。
        # 余った高さはストレッチで自動的に映像へ割り当てられる)
        self.view.setMinimumHeight(120)
        # Ignored: 表示中の画像サイズがレイアウトの要求サイズに反映されて
        # パネルが育っていくのを防ぐ (オシロのミラー画面と同じ対策)
        self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet(
            "background: black; color: gray; border: 1px solid #555;")
        layout.addWidget(self.view, 1)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("デバイス:"))
        self.index_spin = QSpinBox()
        self.index_spin.setRange(0, 9)
        self.index_spin.setValue(self.camera.index)
        ctrl.addWidget(self.index_spin)
        self.start_btn = QPushButton("開始")
        self.start_btn.setMinimumWidth(56)
        self.start_btn.clicked.connect(self._toggle)
        ctrl.addWidget(self.start_btn)
        self.crosshair_chk = QCheckBox("十字線")
        self.crosshair_chk.setChecked(True)
        ctrl.addWidget(self.crosshair_chk)
        ctrl.addWidget(QLabel("ゲイン:"))
        self.gain_spin = QSpinBox()
        self.gain_spin.setRange(0, 255)
        self.gain_spin.setToolTip("カメラゲイン (反映後の実値は通信ログに出る)")
        self.gain_spin.valueChanged.connect(self._gain_changed)
        ctrl.addWidget(self.gain_spin)
        snap_btn = QPushButton("スナップショット")
        snap_btn.clicked.connect(self._snapshot)
        ctrl.addWidget(snap_btn)
        ctrl.addStretch(1)
        layout.addLayout(ctrl)

        # スナップショットの保存結果 (ダイアログで作業を止めない)
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
            QMessageBox.warning(self, "カメラエラー", str(exc))
            return
        self._grabber = FrameGrabber(self.camera, interval_s=0.05)
        self._grabber.frame_ready.connect(self._show_frame)
        self._grabber.error.connect(self._on_error)
        self._grabber.start()
        self.start_btn.setText("停止")

    def _stop(self):
        if self._grabber is not None:
            self._grabber.stop()
            self._grabber = None
        try:
            self.camera.close()
        except Exception:
            pass
        self.start_btn.setText("開始")
        self.view.setText("カメラ停止中")

    def _on_error(self, msg: str):
        self._stop()
        QMessageBox.warning(self, "カメラエラー", msg)

    # ------------------------------------------------------------------
    def _gain_changed(self, value: int):
        # プロパティ設定は数 ms 程度なので GUI スレッドから直接呼ぶ
        if not self.camera.connected:
            return
        try:
            self.camera.set_gain(value)
        except Exception as exc:
            QMessageBox.warning(self, "カメラ設定エラー", str(exc))

    # ------------------------------------------------------------------
    def _show_frame(self, frame: np.ndarray):
        self._last_frame = frame
        h, w = frame.shape[:2]
        # OpenCV は BGR 並びなので QImage.Format_BGR888 で直接ラップする
        img = QImage(frame.data, w, h, frame.strides[0], QImage.Format_BGR888)
        pix = QPixmap.fromImage(img.copy())

        if self.crosshair_chk.isChecked():
            # 画面中心の十字線: レーザースポットをここへ合わせる運用
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
            self.snap_label.setText("スナップショット: 映像がありません "
                                    "(カメラを開始してください)")
            return
        import cv2
        self._capture_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.datetime.now().strftime("snap_%Y%m%d_%H%M%S.png")
        path = self._capture_dir / name
        if cv2.imwrite(str(path), self._last_frame):
            self.snap_label.setText(f"保存しました: {path}")
        else:
            QMessageBox.warning(self, "保存失敗", f"保存できません:\n{path}")

    # ------------------------------------------------------------------
    def shutdown(self):
        self._stop()
