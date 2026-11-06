# -*- coding: utf-8 -*-
"""通信ログパネル。

全デバイスの送受信コマンドをタイムスタンプ付きで表示し、
同時に logs/gui_YYYYMMDD.log へ追記する (測定記録・トラブル解析用)。
デバイスI/Oはワーカースレッドで走るため、LogBus のシグナル経由で
GUIスレッドへ安全に渡す。
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QPlainTextEdit,
                               QPushButton, QVBoxLayout)


class LogBus(QObject):
    """どのスレッドからでも呼べるログ集約点。"""

    message = Signal(str)

    def make_logger(self, source: str):
        """デバイスに渡す log(direction, text) コールバックを生成する。"""
        def _log(direction: str, text: str):
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.message.emit(f"[{ts}] {source:<6} {direction:<4} {text}")
        return _log


class LogPanel(QGroupBox):
    MAX_BLOCKS = 5000  # 表示行数の上限 (超えた分は古い行から破棄)

    def __init__(self, log_dir: str, parent=None):
        super().__init__("通信ログ", parent)
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._open_logfile()

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(self.MAX_BLOCKS)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self._text.setFont(font)

        clear_btn = QPushButton("表示クリア")
        clear_btn.clicked.connect(self._text.clear)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(clear_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._text)
        layout.addLayout(btn_row)

    def _open_logfile(self):
        name = datetime.datetime.now().strftime("gui_%Y%m%d.log")
        self._file = open(self._log_dir / name, "a", encoding="utf-8")

    def append(self, line: str):
        self._text.appendPlainText(line)
        if self._file is not None:
            self._file.write(line + "\n")
            self._file.flush()

    def close_file(self):
        if self._file is not None:
            self._file.close()
            self._file = None
