# -*- coding: utf-8 -*-
"""Exclusive execution of long tasks (worker threads) and progress hub.

Scans, Data Retrieval, Z focus, auto alignment and TUNE optimization all
occupy the stage / scope / laser, so they must never run concurrently.
The Auto Scan panel used to own every worker, but the controls have
moved to the stage panel (alignment) and the laser panel (TUNE
optimization), so the mutual exclusion and the progress display are
gathered in this class.

  - start(): starts the worker unless a task is running (then False)
  - progress and state are always shown in the toolbar task display
    (TaskStrip), visible from any panel regardless of scroll position
  - abort(): called from the Abort button / STOP ALL
  - busy_changed: used by the panels to enable/disable their buttons

Worker contract: a QThread with a progress(int, int, str) signal and
abort(), which emits a (bool success, str message) signal exactly once
when it ends.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QWidget)

from .widgets import ElidedLabel


class TaskRunner(QObject):
    busy_changed = Signal(bool)
    started = Signal(str)                 # task name
    progress = Signal(int, int, str)      # (done, total, message)
    finished = Signal(str, bool, str)     # (task name, success, message)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._name = ""
        self._callback = None

    def is_busy(self) -> bool:
        return self._worker is not None

    def current_name(self) -> str:
        return self._name

    def start(self, name: str, worker, finished_signal,
              on_finished=None) -> bool:
        """Start the worker. Does nothing and returns False while another
        task is running."""
        if self._worker is not None:
            return False
        self._worker = worker
        self._name = name
        self._callback = on_finished
        worker.progress.connect(self.progress)
        finished_signal.connect(self._on_finished)
        # Safety net: also release when the thread ends without emitting
        # its finished signal
        worker.finished.connect(self._on_thread_finished)
        self.busy_changed.emit(True)
        self.started.emit(name)
        worker.start()
        return True

    def abort(self) -> None:
        if self._worker is not None:
            self._worker.abort()
            self.progress.emit(0, 0, f"{self._name}: abort requested...")

    def _on_finished(self, ok: bool, message: str) -> None:
        worker, name, callback = self._worker, self._name, self._callback
        if worker is None:
            return
        self._worker, self._name, self._callback = None, "", None
        worker.wait(2000)
        self.busy_changed.emit(False)
        self.finished.emit(name, ok, message)
        if callback is not None:
            callback(ok, message, worker)

    def _on_thread_finished(self) -> None:
        if self._worker is not None and self.sender() is self._worker:
            self._on_finished(
                False, f"{self._name} ended without returning a result")

    def shutdown(self) -> None:
        worker = self._worker
        if worker is not None:
            self._callback = None   # no dialogs while shutting down
            worker.abort()
            worker.wait(5000)
            self._worker = None


class TaskStrip(QWidget):
    """Toolbar task display: [task name] [progress bar] [status] [Abort]."""

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.runner = runner
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        self.name_label = QLabel("Task: none")
        self.name_label.setStyleSheet("color: white; font-weight: bold;")
        row.addWidget(self.name_label)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(220)
        self.bar.setTextVisible(True)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        row.addWidget(self.bar)
        self.status = ElidedLabel("Idle")
        self.status.setStyleSheet("color: #eceff1;")
        row.addWidget(self.status, 1)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setToolTip("Abort the running task (scan / calibration "
                                  "/ transfer, etc.)")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(runner.abort)
        row.addWidget(self.abort_btn)

        runner.started.connect(self._on_started)
        runner.progress.connect(self._on_progress)
        runner.finished.connect(self._on_finished)

    def _on_started(self, name: str) -> None:
        self.name_label.setText(f"Running: {name}")
        self.bar.setRange(0, 0)          # busy until the first progress
        self.status.setText(f"Starting {name}...")
        self.abort_btn.setEnabled(True)

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        self.status.setText(message)

    def _on_finished(self, name: str, ok: bool, message: str) -> None:
        self.name_label.setText("Task: none")
        self.bar.setRange(0, 1)
        self.bar.setValue(1 if ok else 0)
        first = message.splitlines()[0] if message else ""
        self.status.setText(f"{name} {'done' if ok else 'ended'}: {first}")
        self.status.setToolTip(message)
        self.abort_btn.setEnabled(False)
