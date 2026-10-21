"""Value controls whose wheel input scrolls the surrounding form.

Import value editors from this module when adding controls. Native Qt model,
construction and non-wheel event handling stay untouched.
"""

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6 import QtWidgets


def scroll_parent(control, event):
    delta = event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta()
    horizontal = bool(event.modifiers() & Qt.ShiftModifier) or abs(delta.x()) > abs(delta.y())
    parent = control.parentWidget()
    while parent is not None:
        if isinstance(parent, QtWidgets.QAbstractScrollArea):
            bar = parent.horizontalScrollBar() if horizontal else parent.verticalScrollBar()
            if bar.maximum() == bar.minimum():
                parent = parent.parentWidget()
                continue
            viewport = parent.viewport()
            forwarded = QWheelEvent(
                QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                event.source(), event.pointingDevice(),
            )
            QtWidgets.QApplication.sendEvent(viewport, forwarded)
            event.accept()
            return
        parent = parent.parentWidget()
    event.ignore()


class _EditorWheelFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel:
            scroll_parent(self.parent(), event)
            return True
        return False


def _watch_line_edit(control):
    guard = getattr(control, "_wheel_editor_filter", None)
    if guard is None:
        guard = control._wheel_editor_filter = _EditorWheelFilter(control)
    control.lineEdit().installEventFilter(guard)


class QComboBox(QtWidgets.QComboBox):
    def setEditable(self, editable):
        super().setEditable(editable)
        if editable:
            _watch_line_edit(self)

    def setLineEdit(self, editor):
        super().setLineEdit(editor)
        _watch_line_edit(self)

    def wheelEvent(self, event):
        scroll_parent(self, event)


class QSpinBox(QtWidgets.QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _watch_line_edit(self)

    def wheelEvent(self, event):
        scroll_parent(self, event)


class QDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _watch_line_edit(self)

    def wheelEvent(self, event):
        scroll_parent(self, event)


class QDateTimeEdit(QtWidgets.QDateTimeEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _watch_line_edit(self)

    def wheelEvent(self, event):
        scroll_parent(self, event)


class QDateEdit(QtWidgets.QDateEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _watch_line_edit(self)

    def wheelEvent(self, event):
        scroll_parent(self, event)


class QTimeEdit(QtWidgets.QTimeEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _watch_line_edit(self)

    def wheelEvent(self, event):
        scroll_parent(self, event)


class QSlider(QtWidgets.QSlider):
    def wheelEvent(self, event):
        scroll_parent(self, event)


class QDial(QtWidgets.QDial):
    def wheelEvent(self, event):
        scroll_parent(self, event)
