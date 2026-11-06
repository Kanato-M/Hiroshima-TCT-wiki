# -*- coding: utf-8 -*-
"""照射位置確認用フォーカスカメラ (USBカメラ) の取得。

OpenCV VideoCapture の薄いラッパ。暗室外からレーザースポットと
センサー位置を確認するために使う。フレームは BGR の numpy 配列。
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

import numpy as np

LogFunc = Optional[Callable[[str, str], None]]


class FocusCamera:
    def __init__(self, index: int = 0, simulate: bool = False,
                 log: LogFunc = None):
        self.index = index
        self.simulate = simulate
        self._log = log or (lambda d, t: None)
        self._cap = None
        self._lock = threading.Lock()
        self.connected = False
        self._sim_t0 = time.monotonic()

    def open(self) -> None:
        if self.simulate:
            self.connected = True
            self._log("INFO", f"[SIM] カメラ {self.index} オープン")
            return
        import cv2
        # Windows 既定の MSMF バックエンドは露出・フォーカス等の
        # プロパティ制御が効かないことが多いため DirectShow を優先する
        if sys.platform.startswith("win"):
            self._cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                self._cap.release()
                self._cap = cv2.VideoCapture(self.index)
        else:
            self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            self._cap = None
            # 本機のフォーカスカメラは OMRON SENTECH STC-MBS312U3V を
            # Sentech DirectShow フィルタ経由で使っている (UVC ではない)。
            # 開けない場合はほぼ Sentech ドライバ層からカメラが見えていない
            raise RuntimeError(
                f"カメラ {self.index} を開けません。\n"
                "Sentech カメラ (STC-MBS312U3V) が応答していない可能性:\n"
                "1) カメラの USB ケーブルを PC 側で抜き、10秒待って挿し直す\n"
                "   (Windows の接続音がしない場合は別の USB3 ポートへ)\n"
                "2) それでも駄目なら PC を再起動する")
        self.connected = True
        w = self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        h = self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self._log("INFO", f"カメラ {self.index} オープン ({int(w)}x{int(h)})")

    def close(self) -> None:
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            self.connected = False
        self._log("INFO", "カメラ クローズ")

    def read(self) -> Optional[np.ndarray]:
        """1フレーム取得 (BGR)。失敗時は None。"""
        with self._lock:
            if self.simulate:
                return self._simulate_frame()
            if self._cap is None:
                return None
            ok, frame = self._cap.read()
            return frame if ok else None

    # ------------------------------------------------------------------
    # プロパティ制御 (ゲインのみ)
    # ------------------------------------------------------------------
    # 本機のカメラ (STC-MBS312U3V + 手動フォーカスの C マウントレンズ) は
    # オートフォーカス・フォーカス位置の制御に対応しないため、GUI から
    # 操作するのはゲインだけにしている (2026-10 整理)。設定後の実値を
    # ログに出すので、値が変わらなければカメラ側が非対応と判断できる。

    def _set_prop(self, prop_name: str, value: float) -> Optional[float]:
        """OpenCV プロパティを設定し、反映後の実値を返す (失敗時 None)。"""
        if self.simulate:
            self._log("INFO", f"[SIM] {prop_name} := {value}")
            return float(value)
        import cv2
        prop = getattr(cv2, prop_name)
        with self._lock:
            if self._cap is None:
                raise RuntimeError("カメラが開かれていません")
            ok = self._cap.set(prop, float(value))
            actual = self._cap.get(prop)
        self._log("INFO",
                  f"{prop_name} := {value} → 実値 {actual}"
                  f"{'' if ok else ' (set失敗: カメラ非対応の可能性)'}")
        return actual if ok else None

    def set_gain(self, value: float) -> Optional[float]:
        return self._set_prop("CAP_PROP_GAIN", value)

    # ------------------------------------------------------------------
    def _simulate_frame(self) -> np.ndarray:
        """疑似カメラ映像: センサー基板風の背景 + 動くレーザースポット。"""
        import cv2
        h, w = 480, 640
        img = np.full((h, w, 3), (30, 60, 30), dtype=np.uint8)
        # ストリップ電極風の縦線 (500µmピッチのイメージ)
        for x in range(40, w, 80):
            cv2.rectangle(img, (x, 40), (x + 10, h - 40), (140, 150, 150), -1)
        # レーザースポット (緩やかに円運動)
        t = time.monotonic() - self._sim_t0
        cx = int(w / 2 + 60 * np.cos(t / 3))
        cy = int(h / 2 + 40 * np.sin(t / 3))
        cv2.circle(img, (cx, cy), 6, (80, 255, 120), -1)
        cv2.circle(img, (cx, cy), 12, (60, 200, 90), 2)
        cv2.putText(img, "FOCUS CAMERA (SIMULATION)", (16, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
        return img
