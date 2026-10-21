"""Nullable table date editors: visible format hints, calendar and zoned clock."""

from datetime import date, datetime, timezone

from PySide6.QtCore import QDate, QEvent, QRect, QTime, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QTimeEdit,
)
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.ui.theme import color, ThemedCalendar


def local_candidates(day, hour, zone):
    """Return valid instants; two entries require an explicit DST choice."""
    wall = datetime.combine(day, hour)
    candidates = {}
    for fold in (0, 1):
        instant = wall.replace(tzinfo=zone, fold=fold)
        if (
            instant.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
            == wall
        ):
            candidates[instant.utcoffset()] = instant
    return sorted(candidates.values(), key=lambda value: value.astimezone(timezone.utc))


class TemporalPicker(QDialog):
    def __init__(self, parent, text, zone, with_time=False):
        super().__init__(parent)
        self.with_time, self.clock, self.value = with_time, ExperimentClock(zone), None
        self.setWindowTitle(
            "Choose event date and time" if with_time else "Choose date of birth"
        )
        self._original = None
        if text:
            try:
                self._original = (
                    self.clock.parse(text, allow_naive=True)
                    if with_time
                    else date.fromisoformat(text)
                )
            except ValueError:
                pass
        initial = self._original or (
            self.clock.now() if with_time else self.clock.now().date()
        )
        day = initial.date() if with_time else initial
        layout = QVBoxLayout(self)
        self.calendar = ThemedCalendar()
        self.calendar.setGridVisible(True)
        self.calendar.setSelectedDate(QDate(day.year, day.month, day.day))
        layout.addWidget(self.calendar)
        self.hour = QTimeEdit()
        self.hour.setDisplayFormat("HH:mm:ss")
        self.hour.setKeyboardTracking(False)
        self.hour.setTime(
            QTime(initial.hour, initial.minute, initial.second)
            if with_time
            else QTime(0, 0)
        )
        self._initial_hms = self.hour.time().toString("HH:mm:ss")
        self.offset = QComboBox()
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        if with_time:
            row = QHBoxLayout()
            row.addWidget(QLabel("Time (hours : minutes : seconds):"))
            row.addWidget(self.hour)
            layout.addLayout(row)
            layout.addWidget(QLabel(f"Experiment timezone: {self.clock.name}"))
            layout.addWidget(self.offset)
            layout.addWidget(self.notice)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        clear = self.buttons.addButton("Leave blank", QDialogButtonBox.ResetRole)
        clear.clicked.connect(self.clear_value)
        self.buttons.accepted.connect(self.accept_value)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.calendar.selectionChanged.connect(self.update_offsets)
        self.hour.timeChanged.connect(self.update_offsets)
        self.offset.currentIndexChanged.connect(self.update_ok)
        self.update_offsets()
        if with_time and self._original:
            for i in range(self.offset.count()):
                candidate = self.offset.itemData(i)
                if candidate and candidate.utcoffset() == self._original.utcoffset():
                    self.offset.setCurrentIndex(i)
                    break

    def selected_day(self):
        value = self.calendar.selectedDate()
        return date(value.year(), value.month(), value.day())

    def update_offsets(self):
        if not self.with_time:
            return
        value = self.hour.time()
        microsecond = (
            self._original.microsecond
            if self._original and value.toString("HH:mm:ss") == self._initial_hms
            else 0
        )
        from datetime import time

        options = local_candidates(
            self.selected_day(),
            time(value.hour(), value.minute(), value.second(), microsecond),
            self.clock.zone,
        )
        self.offset.blockSignals(True)
        self.offset.clear()
        for instant in options:
            self.offset.addItem(f"UTC{instant.isoformat()[-6:]}", instant)
        self.offset.setCurrentIndex(0 if len(options) == 1 else -1)
        self.offset.blockSignals(False)
        self.notice.setText(
            "This time occurs twice. Choose the UTC offset for the intended occurrence."
            if len(options) == 2
            else (
                "This local time does not exist during the clock change. Choose another time."
                if not options
                else "Saved as YYYY-MM-DDTHH:MM:SS±HH:MM."
            )
        )
        self.update_ok()

    def update_ok(self):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            not self.with_time or self.offset.currentIndex() >= 0
        )

    def clear_value(self):
        self.value = ""
        self.accept()

    def accept_value(self):
        if self.with_time:
            instant = self.offset.currentData()
            if instant is None:
                return
            self.value = instant.isoformat()
        else:
            self.value = self.selected_day().isoformat()
        self.accept()


class TemporalDelegate(QStyledItemDelegate):
    """A lightweight editor; inactive rows do not allocate calendar widgets."""

    def __init__(self, parent, zone_getter, with_time=False):
        super().__init__(parent)
        self.zone_getter, self.with_time = zone_getter, with_time
        self.placeholder = "YYYY-MM-DDTHH:MM:SS±HH:MM" if with_time else "YYYY-MM-DD"

    def canonical(self, text):
        text = text.strip()
        if not text:
            return ""
        if self.with_time:
            return (
                ExperimentClock(self.zone_getter())
                .parse(text, allow_naive=True)
                .isoformat()
            )
        return date.fromisoformat(text).isoformat()

    @staticmethod
    def button_rect(option):
        return QRect(
            option.rect.right() - 23, option.rect.top(), 23, option.rect.height()
        )

    def paint(self, painter, option, index):
        display = QStyleOptionViewItem(option)
        self.initStyleOption(display, index)
        display.rect = option.rect.adjusted(0, 0, -25, 0)
        if not index.data():
            display.text = self.placeholder
            display.palette.setColor(QPalette.Text, color("ink-3"))
        style = option.widget.style()
        from PySide6.QtWidgets import QStyle

        style.drawControl(QStyle.CE_ItemViewItem, display, painter, option.widget)
        # Calendar affordance, drawn without adding a widget to every table row.
        rect = self.button_rect(option)
        x, y = rect.center().x() - 7, rect.center().y() - 6
        painter.save()
        painter.setPen(option.palette.color(QPalette.HighlightedText)
                       if option.state & QStyle.State_Selected else color("accent-text"))
        painter.drawRect(x, y, 14, 13)
        painter.drawLine(x, y + 4, x + 14, y + 4)
        for dx in (4, 9):
            painter.drawLine(x + dx, y - 2, x + dx, y + 2)
            painter.drawLine(x + dx, y + 7, x + dx, y + 10)
        painter.restore()

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setPlaceholderText(self.placeholder)
        editor.setToolTip("Type the date, or use the calendar icon in the cell.")
        return editor

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")

    def setModelData(self, editor, model, index):
        try:
            model.setData(index, self.canonical(editor.text()), Qt.EditRole)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(
                editor, "Date/time not saved", f"{exc}\nExpected: {self.placeholder}"
            )

    def editorEvent(self, event, model, option, index):
        if (
            event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
            and self.button_rect(option).contains(event.position().toPoint())
        ):
            try:
                dialog = TemporalPicker(
                    option.widget,
                    index.data() or "",
                    self.zone_getter(),
                    self.with_time,
                )
                if dialog.exec() == QDialog.Accepted:
                    model.setData(index, dialog.value, Qt.EditRole)
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(
                    option.widget, "Choose an experiment timezone", str(exc)
                )
            return True
        return super().editorEvent(event, model, option, index)
