"""Editable cohort metadata, independent of device controls."""

from __future__ import annotations
import copy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QSpinBox,
)
from deepecohab_recorder.ui.date_time_fields import TemporalDelegate
from deepecohab_recorder.schema import normalize_animals
from deepecohab_recorder.ui.common import config_buttons, MatchDialog


class AnimalDetailsTab(QWidget):
    paths = ("recording.cohort",)
    COLUMNS = [
        ("row_index", "No."),
        ("tag", "RFID tag"),
        ("subject_name", "Subject name"),
        ("mouse_line", "Mouse line"),
        ("genotype", "Genotype"),
        ("genetic_background", "Genetic background"),
        ("treatment", "Treatment"),
        ("sex", "Sex"),
        ("date_of_birth", "Date of birth"),
        ("notes", "Notes"),
    ]

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self._loading, self._extras = False, []
        layout = QVBoxLayout(self)
        layout.addLayout(config_buttons(self, "animal details"))
        row = QHBoxLayout()
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(12)
        match = QPushButton("Match tag for selected mouse")
        match.clicked.connect(self.match)
        row.addWidget(QLabel("Number of mice:"))
        row.addWidget(self.count)
        row.addWidget(match)
        row.addStretch(1)
        layout.addLayout(row)
        note = QLabel(
            "Required: RFID tag, mouse line, genotype, genetic background, sex and date of birth. Subject name, Treatment and Notes may be blank. Double-click Genotype or Genetic background to choose a preset or type your own; Sex offers Male or Female."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        group = QGroupBox(
            "Check if the value is same for all the mice within the group"
        )
        grid = QGridLayout(group)
        self.same_grid = grid
        self.same = {}
        for i, (key, title) in enumerate(self.COLUMNS[3:]):
            box = QCheckBox(title)
            self.same[key] = box
            grid.addWidget(box, i // 4, i % 4)
            box.toggled.connect(
                lambda on, key=key: self.copy_column(key) if on else None
            )
        layout.addWidget(group)
        self.table = QTableWidget(12, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([title for _, title in self.COLUMNS])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col in range(len(self.COLUMNS)):
            self.table.setColumnWidth(col, 130 if col else 48)
        self.table.setColumnWidth(8, 155)
        self.table.setColumnWidth(9, 230)
        self.table.setItemDelegateForColumn(
            8, TemporalDelegate(self.table, self.ui.experiment_timezone)
        )
        from deepecohab_recorder.ui.animal_presets import PresetDelegate, GENOTYPES, BACKGROUNDS
        for col, choices, editable in ((4, GENOTYPES, True), (5, BACKGROUNDS, True), (7, ["Male", "Female"], False)):
            self.table.setItemDelegateForColumn(col, PresetDelegate(choices, editable, self.table))
        layout.addWidget(self.table, 1)
        self.count.valueChanged.connect(self.resize_rows)
        self.table.itemChanged.connect(self.changed)
        self.resize_rows(12)

    def resize_rows(self, count):
        self._loading = True
        self.table.setRowCount(count)
        while len(self._extras) < count:
            self._extras.append({})
        self._extras = self._extras[:count]
        for row in range(count):
            for col, (key, _) in enumerate(self.COLUMNS):
                if self.table.item(row, col) is None:
                    self.table.setItem(
                        row, col, QTableWidgetItem(str(row + 1) if col == 0 else "")
                    )
                if col == 0:
                    self.table.item(row, col).setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    )
        self._loading = False
        for key, box in self.same.items():
            if box.isChecked():
                self.copy_column(key)

    def changed(self, item):
        if not self._loading:
            key = self.COLUMNS[item.column()][0]
            if key in self.same and self.same[key].isChecked():
                self.copy_column(key, item.row())

    def copy_column(self, key, source=0):
        if not hasattr(self, "table") or self._loading:
            return
        col = next(i for i, pair in enumerate(self.COLUMNS) if pair[0] == key)
        item = self.table.item(source, col)
        if item is None:
            return
        self._loading = True
        for row in range(self.table.rowCount()):
            if self.table.item(row, col):
                self.table.item(row, col).setText(item.text())
        self._loading = False

    def match(self):
        row = self.table.currentRow()
        if row < 0:
            row = 0
        if self.ui.prepare_matching():
            dialog = MatchDialog(self.ui, self)
            if dialog.exec() and dialog.selected:
                self.table.item(row, 1).setText(dialog.selected[0])

    def read(self):
        rows = []
        for row in range(self.table.rowCount()):
            data = copy.deepcopy(self._extras[row])
            for col, (key, _) in enumerate(self.COLUMNS):
                data[key] = self.table.item(row, col).text().strip()
            data["row_index"] = row + 1
            data["tag"] = data["tag"].upper()
            rows.append(data)
        return {
            "recording.cohort": normalize_animals(
                {"n_mice": len(rows), "animals": rows}
            )
        }

    def load(self, data):
        cohort = data["recording"]["cohort"]
        rows = cohort["animals"]
        for box in self.same.values():
            box.setChecked(False)
        count = max(1, len(rows) or int(cohort.get("n_mice") or 12))
        self.count.setValue(count)
        self.resize_rows(count)
        self._loading = True
        self._extras = copy.deepcopy(rows) if rows else [{} for _ in range(count)]
        for row in range(count):
            source = rows[row] if row < len(rows) else {}
            for col, (key, _) in enumerate(self.COLUMNS):
                self.table.item(row, col).setText(
                    str(source.get(key, row + 1 if col == 0 else "") or "")
                )
        self._loading = False

    def viewport_size(self, width, height):
        columns = 2 if width < 750 else 4
        for index, box in enumerate(self.same.values()):
            self.same_grid.removeWidget(box)
            self.same_grid.addWidget(box, index // columns, index % columns)
