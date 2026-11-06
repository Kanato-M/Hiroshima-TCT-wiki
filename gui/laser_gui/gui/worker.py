# -*- coding: utf-8 -*-
"""GUIスレッドとデバイスI/Oを分離するためのヘルパ群。

- AsyncCaller: デバイスごとに1本のワーカースレッドを持ち、ブロッキングI/Oを
  逐次実行して結果を GUI スレッドへコールバックする。
  (シリアル/ソケットのタイムアウト待ちで GUI が固まるのを防ぐ)
- FrameGrabber: カメラフレームを連続取得するスレッド。
- ScreenMirror: オシロ画面 PNG を周期取得するスレッド。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QThread, Signal


class AsyncCaller(QObject):
    """ブロッキング関数をワーカースレッドで実行し、結果をGUIスレッドで受け取る。

    使い方:
        caller.call(lambda: device.send("Q:"),
                    on_result=lambda r: ...,
                    on_error=lambda e: ...)

    _done シグナルはワーカースレッドから emit されるため Qt の自動接続で
    キュー接続となり、コールバックは必ず GUI スレッドで実行される。
    """

    _done = Signal(object)  # GUIスレッドで実行するクロージャを運ぶ

    def __init__(self, name: str = "device", parent=None):
        super().__init__(parent)
        # 1デバイス=1スレッドに直列化し、機器へのコマンド順序を保証する
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
    """カメラフレームを連続取得して emit するスレッド。"""

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
    """オシロスコープ画面 (PNG) を周期取得するスレッド。"""

    image_ready = Signal(bytes)    # PNG バイト列
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
            # 取得中に interval が変更されても追従できるよう分割スリープ
            waited = 0.0
            while self._running and waited < self.interval_s:
                self.msleep(50)
                waited += 0.05

    def stop(self):
        self._running = False
        self.wait(3000)
