from __future__ import annotations
import queue
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from deepecohab_recorder.services.matching import MatchSession


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(widget)
    return area


def wrapping_form(parent=None):
    form = QFormLayout(parent)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return form


def config_buttons(tab, label):
    row = QHBoxLayout()
    load = QPushButton(f"Load {label} from experiment file")
    save = QPushButton(f"Save {label} to experiment file")
    save.setObjectName("primary")
    load.clicked.connect(lambda: tab.ui.load_section(tab))
    save.clicked.connect(lambda: tab.ui.save_section(tab))
    row.addWidget(load)
    row.addWidget(save)
    row.addStretch(1)
    return row


def help_button(parent, title, text):
    button = QPushButton("?")
    button.setFixedSize(30, 30)
    button.setAccessibleName(title)
    button.clicked.connect(lambda: QMessageBox.information(parent, title, text))
    return button


class MatchDialog(QDialog):
    def __init__(self, ui, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match RFID tag / antenna")
        self.setMinimumSize(460, 280)
        self.resize(620, 430)
        self.worker = MatchSession(ui.services, ui.hardware_settings()["boards"])
        self.selected = None
        self.closing = False
        self._rows = {}
        layout = QVBoxLayout(self)
        self.message = QLabel(
            "Present a tag to an antenna, select its row and click Use selected."
        )
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["RFID tag", "Antenna", "Reads"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox()
        self.use = buttons.addButton("Use selected", QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.Cancel)
        self.use.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self.worker.start()

    def poll(self):
        while True:
            try:
                tag, source, board = self.worker.results.get_nowait()
            except queue.Empty:
                break
            key = (tag, source, board)
            if key not in self._rows:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._rows[key] = [row, 0]
                self.table.setItem(row, 0, QTableWidgetItem(tag))
                self.table.setItem(row, 1, QTableWidgetItem(source))
            row, count = self._rows[key]
            self._rows[key][1] += 1
            self.table.setItem(row, 2, QTableWidgetItem(str(count + 1)))
        if self.worker.error:
            self.message.setText(self.worker.error)
        if self.closing and not self.worker.is_running():
            self.timer.stop()
            super().done(QDialog.Accepted if self.selected else QDialog.Rejected)

    def accept(self):
        row = self.table.currentRow()
        if row < 0:
            self.message.setText("Select a detected tag first.")
            return
        self.selected = next(
            key for key, value in self._rows.items() if value[0] == row
        )
        self.finish()

    def reject(self):
        self.selected = None
        self.finish()

    def finish(self):
        self.worker.cancel.set()
        self.closing = True
        self.use.setEnabled(False)
        self.message.setText("Closing matching connections…")
        self.poll()

    def closeEvent(self, event):
        if self.worker.is_running():
            event.ignore()
            self.reject()
        else:
            event.accept()
