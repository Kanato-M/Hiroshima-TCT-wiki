# -*- coding: utf-8 -*-
"""Helpers separating the GUI thread from device I/O.

- AsyncCaller: one worker thread per device, executing blocking I/O
  sequentially and calling back into the GUI thread with the result.
  (Prevents the GUI from freezing on serial/socket timeouts.)
- FrameGrabber: thread that continuously grabs camera frames.
- ScreenMirror: thread that periodically fetches the scope screen PNG.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QThread, Signal


class AsyncCaller(QObject):
    """Run a blocking function on a worker thread and receive the result
    on the GUI thread.

    Usage:
        caller.call(lambda: device.send("Q:"),
                    on_result=lambda r: ...,
                    on_error=lambda e: ...)

    The _done signal is emitted from the worker thread, so Qt's automatic
    connection makes it a queued connection and the callback always runs
    on the GUI thread.
    """

    _done = Signal(object)  # carries the closure to run on the GUI thread

    def __init__(self, name: str = "device", parent=None):
        super().__init__(parent)
        # One thread per device serializes the calls and guarantees the
        # order of the commands sent to the instrument
        self._pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix=name)
        self._done.connect(lambda fn: fn())

    def call(self, fn, on_result=None, on_error=None):
        def work():
            try:
                result = fn()
            except Exception as exc:
                if on_error is not None:
                    self._done.emit(lambda exc=exc: on_error(exc))
            else:
                if on_result is not None:
                    self._done.emit(lambda result=result: on_result(result))
        self._pool.submit(work)

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


class FrameGrabber(QThread):
    """Thread that continuously grabs and emits camera frames."""

    frame_ready = Signal(object)   # np.ndarray (BGR)
    error = Signal(str)

    def __init__(self, camera, interval_s: float = 0.05, parent=None):
        super().__init__(parent)
        self._camera = camera
        self._interval = interval_s
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            try:
                frame = self._camera.read()
            except Exception as exc:
                self.error.emit(str(exc))
                break
            if frame is not None:
                self.frame_ready.emit(frame)
            self.msleep(int(self._interval * 1000))

    def stop(self):
        self._running = False
        self.wait(2000)


class ScreenMirror(QThread):
    """Thread that periodically fetches the oscilloscope screen (PNG)."""

    image_ready = Signal(bytes)    # PNG byte string
    error = Signal(str)

    def __init__(self, scope, interval_s: float = 1.0, parent=None):
        super().__init__(parent)
        self._scope = scope
        self.interval_s = interval_s
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            try:
                png = self._scope.get_screen_png()
            except Exception as exc:
                self.error.emit(str(exc))
                break
            if png:
                self.image_ready.emit(png)
            # sleep in slices so interval changes take effect mid-wait
            waited = 0.0
            while self._running and waited < self.interval_s:
                self.msleep(50)
                waited += 0.05

    def stop(self):
        self._running = False
        self.wait(3000)
