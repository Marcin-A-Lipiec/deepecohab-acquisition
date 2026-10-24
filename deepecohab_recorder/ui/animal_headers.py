"""Animal-table actions follow Qt header sections, without adding data columns."""

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QCheckBox, QHeaderView, QPushButton
from deepecohab_recorder.ui.theme import color


class ColumnSharingHeader(QHeaderView):
    def __init__(self, fields, parent):
        super().__init__(Qt.Horizontal, parent)
        self.boxes = {}
        for column, key, title in fields:
            box = QCheckBox(self.viewport())
            box.setAccessibleName(f"Use first mouse's {title} for all mice")
            box.setToolTip(f"Use the first mouse's {title} for all mice. Uncheck to edit mice individually.")
            self.boxes[key] = (column, box)
        self.sectionResized.connect(self.position_controls)
        self.sectionMoved.connect(self.position_controls)
        self.geometriesChanged.connect(self.position_controls)

    def strip_height(self):
        return max((box.sizeHint().height() for _, box in self.boxes.values()), default=20) + 6

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(size.width(), size.height() + self.strip_height())

    def paintSection(self, painter, rect, logical):
        height = self.strip_height()
        super().paintSection(painter, rect.adjusted(0, height, 0, 0), logical)
        painter.save()
        painter.fillRect(QRect(rect.x(), rect.y(), rect.width(), height), color("sunken"))
        painter.setPen(color("line"))
        painter.drawLine(rect.right(), rect.top(), rect.right(), rect.top() + height)
        painter.restore()

    def position_controls(self, *args):
        for column, box in self.boxes.values():
            size = box.sizeHint()
            left, width = self.sectionViewportPosition(column), self.sectionSize(column)
            box.setGeometry(left + (width - size.width()) // 2, 3, size.width(), size.height())
            box.setVisible(not self.isSectionHidden(column) and left + width > 0 and left < self.viewport().width())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_controls()

    def event(self, event):
        result = super().event(event)
        if hasattr(self, "boxes") and event.type() in (QEvent.FontChange, QEvent.StyleChange, QEvent.Show):
            self.updateGeometry()
            self.position_controls()
        return result


class RowMatchHeader(QHeaderView):
    match_requested = Signal(int)

    def __init__(self, parent):
        super().__init__(Qt.Vertical, parent)
        self.buttons = {}
        self.sectionResized.connect(self.position_controls)
        self.geometriesChanged.connect(self.position_controls)

    def sync_rows(self, count):
        for row in list(self.buttons):
            if row >= count:
                self.buttons.pop(row).deleteLater()
        for row in range(count):
            if row not in self.buttons:
                button = QPushButton("Match tag", self.viewport())
                button.setObjectName("rowMatchTag")
                button.setAccessibleName(f"Match RFID tag for mouse {row + 1}")
                button.setToolTip(f"Present and assign a tag to mouse {row + 1}.")
                button.clicked.connect(lambda checked=False, row=row: self.match_requested.emit(row))
                self.buttons[row] = button
        if self.buttons:
            height = next(iter(self.buttons.values())).sizeHint().height() + 4
            self.setMinimumSectionSize(height)
            self.setDefaultSectionSize(height)
        self.updateGeometry()
        self.position_controls()

    def sizeHint(self):
        width = max((button.sizeHint().width() for button in self.buttons.values()), default=80) + 8
        return QSize(width, super().sizeHint().height())

    def paintSection(self, painter: QPainter, rect, logical):
        painter.fillRect(rect, color("sunken"))

    def position_controls(self, *args):
        for row, button in self.buttons.items():
            top, height = self.sectionViewportPosition(row), self.sectionSize(row)
            button.setGeometry(4, top + 2, max(0, self.viewport().width() - 8), max(0, height - 4))
            button.setVisible(top + height > 0 and top < self.viewport().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_controls()

    def event(self, event):
        result = super().event(event)
        if hasattr(self, "buttons") and event.type() in (QEvent.FontChange, QEvent.StyleChange, QEvent.Show):
            self.sync_rows(self.count())
        return result
