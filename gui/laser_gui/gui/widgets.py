# -*- coding: utf-8 -*-
"""パネル共通の小物ウィジェット。

- ElidedLabel: 長い文字列を「…」で省略表示するラベル。
  通常の QLabel は文字列の幅がそのまま最小幅になるため、状態メッセージ
  (例: 原点出し完了の長い 1 行) を入れるとパネルが横に押し広げられ、
  カラムに横スクロールが出てしまう (2026-10 の不具合)。動的な文字列は
  必ずこのラベルに入れる。全文はツールチップで読める。
- NoWheelFilter: スピンボックス / コンボボックス上でのホイール操作による
  値の変更を無効化する (スクロール中にカーソルが乗っただけで設定値が
  変わる事故の防止)。ホイールイベントは親へ流すので、カラムの
  スクロールはそのまま効く。
- ComboPopupFitter: コンボボックスのドロップダウンを項目の文字幅に
  合わせて広げる。Windows 11 スタイルではドロップダウン幅がコンボ本体の
  幅に追従するため、カラムが狭くてコンボが縮められると項目が「1...V」の
  ように省略されて選べなくなる (2026-10-07 の不具合)。開く直前に
  リストビューの最小幅を項目幅 + 余白に設定する (後から項目を入れ替える
  コンボにも効く)。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QLabel,
                               QSizePolicy, QSlider)


class ElidedLabel(QLabel):
    """幅に収まらない文字列を末尾省略で 1 行表示するラベル。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._refresh()

    def full_text(self) -> str:
        return self._full

    def _refresh(self) -> None:
        width = max(self.contentsRect().width() - 2, 10)
        super().setText(self.fontMetrics().elidedText(
            self._full, Qt.ElideRight, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def minimumSizeHint(self) -> QSize:
        return QSize(24, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(120, super().sizeHint().height())


class NoWheelFilter(QObject):
    """数値・選択系ウィジェットのホイール操作を無効化するイベントフィルタ。

    QApplication に 1 つインストールして使う。イベントを ignore して
    True を返すと Qt は同じホイールイベントを親ウィジェットへ伝搬させる
    ので、スクロールエリアのスクロールは従来どおり動く。
    コンボボックスのドロップダウンリスト (別ウィンドウのビュー) は
    対象外なので、開いたリスト内のホイールスクロールは使える。
    """

    _TYPES = (QAbstractSpinBox, QComboBox, QSlider)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(obj, self._TYPES):
            event.ignore()
            return True
        return False


class ComboPopupFitter(QObject):
    """QComboBox のドロップダウンを項目の文字幅に合わせて広げるフィルタ。

    main_window で全コンボに installEventFilter する。開く操作 (クリック /
    キー) の直前にリストビューの最小幅を更新するので、項目を後から
    入れ替えるコンボ (読み出し CH など) にも効く。
    """

    PADDING = 48   # 項目の左右マージン + ハイライト枠 + スクロールバー分

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QComboBox) and event.type() in (
                QEvent.MouseButtonPress, QEvent.KeyPress):
            view = obj.view()
            if view is not None and obj.count() > 0:
                view.setMinimumWidth(
                    view.sizeHintForColumn(0) + self.PADDING)
        return False
