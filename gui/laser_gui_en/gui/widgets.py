# -*- coding: utf-8 -*-
"""Small widgets shared by the panels.

- ElidedLabel: label that shortens a long string with an ellipsis ("…").
  A plain QLabel takes the full text width as its minimum width, so a
  status message (e.g. the long one-line Find Origin result) pushes the
  panel wider and makes the column scroll horizontally (bug of 2026-10).
  Always put dynamic strings into this label. The full text can be read
  in the tooltip.
- NoWheelFilter: disables value changes by the mouse wheel over spin
  boxes / combo boxes (prevents settings from changing just because the
  cursor passed over them while scrolling). The wheel event is passed on
  to the parent, so scrolling of the column still works.
- ComboPopupFitter: widens the drop-down list of a combo box to the text
  width of its items. In the Windows 11 style the drop-down width follows
  the width of the combo box itself, so when a narrow column squeezes the
  combo the items are elided like "1...V" and cannot be chosen (bug of
  2026-10-07). Right before the list opens, the minimum width of the list
  view is set to the item width + padding (this also works for combo
  boxes whose items are replaced later).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QLabel,
                               QSizePolicy, QSlider)


class ElidedLabel(QLabel):
    """Single-line label that elides text too wide to fit at the end."""

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
    """Event filter that disables the wheel on numeric/selection widgets.

    Install a single instance on the QApplication. When the event is
    ignored and True is returned, Qt propagates the same wheel event to
    the parent widget, so scroll areas keep scrolling as before.
    The drop-down list of a combo box (a view in a separate window) is
    not affected, so wheel scrolling inside an opened list still works.
    """

    _TYPES = (QAbstractSpinBox, QComboBox, QSlider)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(obj, self._TYPES):
            event.ignore()
            return True
        return False


class ComboPopupFitter(QObject):
    """Event filter that widens a QComboBox drop-down to the item width.

    main_window installs it on every combo box. The minimum width of the
    list view is updated right before the opening action (click / key),
    so it also works for combo boxes whose items are replaced later
    (readout CH etc.).
    """

    PADDING = 48   # item margins + highlight frame + scroll bar

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QComboBox) and event.type() in (
                QEvent.MouseButtonPress, QEvent.KeyPress):
            view = obj.view()
            if view is not None and obj.count() > 0:
                view.setMinimumWidth(
                    view.sizeHintForColumn(0) + self.PADDING)
        return False
