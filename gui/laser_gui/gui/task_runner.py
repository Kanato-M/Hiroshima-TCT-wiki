# -*- coding: utf-8 -*-
"""長時間タスク (ワーカースレッド) の排他実行と進捗の集約。

スキャン・データ回収・Z フォーカス・自動アライメント・TUNE 最適化は
いずれもステージ / オシロ / レーザーを占有するため同時に走らせない。
以前は自動スキャンパネルが全ワーカーを抱えていたが、操作 UI を
ステージパネル (アライメント) とレーザーパネル (TUNE 最適化) へ
移したので、排他制御と進捗表示をこのクラスに集約した。

  - start(): 実行中でなければワーカーを開始する (実行中なら False)
  - 進捗・状態はツールバーのタスク表示 (TaskStrip) に常時出る
    (どのパネルを見ていても、スクロール位置に関係なく見える)
  - abort(): 中断ボタン / 全停止から呼ぶ
  - busy_changed: 各パネルが操作ボタンの有効/無効を切り替えるのに使う

ワーカーの約束: progress(int, int, str) シグナルと abort() を持ち、
終了時に (bool 成功, str メッセージ) のシグナルを 1 回 emit する QThread。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QWidget)

from .widgets import ElidedLabel


class TaskRunner(QObject):
    busy_changed = Signal(bool)
    started = Signal(str)                 # タスク名
    progress = Signal(int, int, str)      # (完了, 総数, メッセージ)
    finished = Signal(str, bool, str)     # (タスク名, 成功か, メッセージ)

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
        """ワーカーを開始する。別タスクの実行中は何もせず False を返す。"""
        if self._worker is not None:
            return False
        self._worker = worker
        self._name = name
        self._callback = on_finished
        worker.progress.connect(self.progress)
        finished_signal.connect(self._on_finished)
        # 安全網: 終了シグナルを出さずにスレッドが終わった場合も解放する
        worker.finished.connect(self._on_thread_finished)
        self.busy_changed.emit(True)
        self.started.emit(name)
        worker.start()
        return True

    def abort(self) -> None:
        if self._worker is not None:
            self._worker.abort()
            self.progress.emit(0, 0, f"{self._name}: 中断要求を送信しました...")

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
            self._on_finished(False, f"{self._name}が結果を返さずに終了しました")

    def shutdown(self) -> None:
        worker = self._worker
        if worker is not None:
            self._callback = None   # 終了処理中はダイアログを出さない
            worker.abort()
            worker.wait(5000)
            self._worker = None


class TaskStrip(QWidget):
    """ツールバーに置くタスク表示: [タスク名] [進捗バー] [状態] [中断]。"""

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.runner = runner
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        self.name_label = QLabel("タスク: なし")
        self.name_label.setStyleSheet("color: white; font-weight: bold;")
        row.addWidget(self.name_label)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(220)
        self.bar.setTextVisible(True)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        row.addWidget(self.bar)
        self.status = ElidedLabel("待機中")
        self.status.setStyleSheet("color: #eceff1;")
        row.addWidget(self.status, 1)
        self.abort_btn = QPushButton("中断")
        self.abort_btn.setToolTip("実行中のタスク (スキャン / 較正 / 転送など) "
                                  "を中断する")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(runner.abort)
        row.addWidget(self.abort_btn)

        runner.started.connect(self._on_started)
        runner.progress.connect(self._on_progress)
        runner.finished.connect(self._on_finished)

    def _on_started(self, name: str) -> None:
        self.name_label.setText(f"実行中: {name}")
        self.bar.setRange(0, 0)          # 最初の進捗が来るまでビジー表示
        self.status.setText(f"{name}を開始...")
        self.abort_btn.setEnabled(True)

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        self.status.setText(message)

    def _on_finished(self, name: str, ok: bool, message: str) -> None:
        self.name_label.setText("タスク: なし")
        self.bar.setRange(0, 1)
        self.bar.setValue(1 if ok else 0)
        first = message.splitlines()[0] if message else ""
        self.status.setText(f"{name} {'完了' if ok else '終了'}: {first}")
        self.status.setToolTip(message)
        self.abort_btn.setEnabled(False)
