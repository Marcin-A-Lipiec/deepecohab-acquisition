"""Mouse-wheel input scrolls forms even when a value editor has focus."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QScrollArea, QTableWidget, QVBoxLayout, QWidget,
)

from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox, QDateTimeEdit, QDoubleSpinBox, QSpinBox, QTimeEdit,
)


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cleanup_widgets(qt_app):
    widgets = []
    yield widgets
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    qt_app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("kind", ["integer", "decimal", "combo", "editable_combo", "datetime", "time"])
def test_wheel_scrolls_the_form_without_changing_a_focused_value(kind, qt_app, cleanup_widgets):
    app = qt_app
    area = QScrollArea()
    cleanup_widgets.append(area)
    area.setWidgetResizable(True)
    area.resize(400, 240)
    body = QWidget()
    body.setMinimumHeight(1200)
    form = QVBoxLayout(body)
    if kind in ("combo", "editable_combo"):
        editor = QComboBox()
        editor.addItems(["First", "Second", "Third"])
        editor.setEditable(kind == "editable_combo")
        read = editor.currentIndex
    else:
        editor = {"integer": QSpinBox, "decimal": QDoubleSpinBox,
                  "datetime": QDateTimeEdit, "time": QTimeEdit}[kind]()
        read = editor.dateTime if kind in ("datetime", "time") else editor.value
    form.addWidget(editor)
    form.addStretch()
    area.setWidget(body)
    area.show()
    editor.setFocus()
    app.processEvents()
    initial = read()
    targets = [editor]
    if editor.lineEdit() is not None:
        targets.append(editor.lineEdit())
    for target in targets:
        area.verticalScrollBar().setValue(0)
        event = QWheelEvent(QPointF(target.rect().center()), QPointF(target.mapToGlobal(target.rect().center())),
                            QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        QApplication.sendEvent(target, event)
        app.processEvents()
        assert read() == initial
        assert area.verticalScrollBar().value() > 0
    # Deliberate keyboard editing must still work.
    if kind == "integer":
        QTest.keyClick(editor, Qt.Key_Up)
        assert editor.value() == initial + 1
    area.close()


def test_standalone_combo_wheel_cannot_change_the_selected_value(qt_app, cleanup_widgets):
    app = qt_app
    combo = QComboBox()
    cleanup_widgets.append(combo)
    combo.addItems(["First", "Second"])
    combo.show()
    combo.setFocus()
    app.processEvents()
    event = QWheelEvent(QPointF(5, 5), QPointF(combo.mapToGlobal(QPoint(5, 5))),
                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(combo, event)
    assert combo.currentIndex() == 0
    combo.close()


def test_wheel_over_a_control_in_a_fitted_table_scrolls_the_outer_page(qt_app, cleanup_widgets):
    outer = QScrollArea()
    cleanup_widgets.append(outer)
    outer.setWidgetResizable(True)
    outer.resize(400, 240)
    body = QWidget()
    body.setMinimumHeight(1200)
    layout = QVBoxLayout(body)
    table = QTableWidget(1, 1)
    table.setFixedHeight(100)
    combo = QComboBox()
    combo.addItems(["First", "Second"])
    table.setCellWidget(0, 0, combo)
    layout.addWidget(table)
    layout.addStretch()
    outer.setWidget(body)
    outer.show()
    qt_app.processEvents()
    event = QWheelEvent(QPointF(5, 5), QPointF(combo.mapToGlobal(QPoint(5, 5))),
                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(combo, event)
    qt_app.processEvents()
    assert combo.currentIndex() == 0
    assert outer.verticalScrollBar().value() > 0
