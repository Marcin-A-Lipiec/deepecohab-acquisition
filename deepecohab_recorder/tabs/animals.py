"""Editable cohort metadata, independent of device controls."""

from __future__ import annotations
import copy
from PySide6.QtCore import QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
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
from deepecohab_recorder.ui.animal_headers import ColumnSharingHeader, RowMatchHeader


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
        self._generation, self._matching = 0, False
        self._known_tags = frozenset()
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
        sharing = QLabel("Check a box above a column to use the first mouse's value for all mice in that column.")
        sharing.setWordWrap(True)
        layout.addWidget(sharing)
        self.table = QTableWidget(12, len(self.COLUMNS))
        self.sharing_header = ColumnSharingHeader([(i, key, title) for i, (key, title) in enumerate(self.COLUMNS) if i >= 3], self.table)
        self.table.setHorizontalHeader(self.sharing_header)
        self.same = {key: box for key, (_, box) in self.sharing_header.boxes.items()}
        for key, box in self.same.items():
            box.toggled.connect(lambda on, key=key: self._sharing_changed(key, on))
        self.row_actions = RowMatchHeader(self.table)
        self.table.setVerticalHeader(self.row_actions)
        self.row_actions.match_requested.connect(self.match_row)
        self.table.horizontalScrollBar().valueChanged.connect(self.sharing_header.position_controls)
        self.table.verticalScrollBar().valueChanged.connect(self.row_actions.position_controls)
        self.table.setCornerButtonEnabled(False)
        self.table.setHorizontalHeaderLabels([title for _, title in self.COLUMNS])
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
        was_loading = self._loading
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
        self.row_actions.sync_rows(count)
        self._loading = was_loading
        for key, box in self.same.items():
            self._sharing_changed(key, box.isChecked())
        if not self._loading:
            self._notify_tags()

    def changed(self, item):
        if not self._loading:
            key = self.COLUMNS[item.column()][0]
            if key in self.same and self.same[key].isChecked():
                self.copy_column(key)
            if key == "tag":
                self._notify_tags()

    def _notify_tags(self):
        tags = frozenset(self.table.item(row, 1).text().strip().upper()
                         for row in range(self.table.rowCount())
                         if self.table.item(row, 1) and self.table.item(row, 1).text().strip())
        if tags != self._known_tags:
            self._known_tags = tags
            self.ui.animal_tags_changed(tags)

    def _sharing_changed(self, key, on):
        if not hasattr(self, "table") or self._loading:
            return
        col = next(i for i, pair in enumerate(self.COLUMNS) if pair[0] == key)
        self._loading = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, col)
            if item is not None:
                flags = item.flags()
                item.setFlags(flags & ~Qt.ItemIsEditable if on and row else flags | Qt.ItemIsEditable)
                item.setToolTip("Follows the first mouse. Uncheck the box above this column to edit independently." if on and row else "")
        self._loading = False
        if on:
            self.copy_column(key)

    def copy_column(self, key):
        if not hasattr(self, "table") or self._loading:
            return
        col = next(i for i, pair in enumerate(self.COLUMNS) if pair[0] == key)
        item = self.table.item(0, col)
        if item is None:
            return
        self._loading = True
        for row in range(self.table.rowCount()):
            if self.table.item(row, col):
                self.table.item(row, col).setText(item.text())
        self._loading = False

    def match(self):
        row = self.table.currentRow()
        self.match_row(max(0, row))

    def match_row(self, row):
        if self._matching or not 0 <= row < self.table.rowCount():
            return
        target = QPersistentModelIndex(self.table.model().index(row, 1))
        generation = self._generation
        self._matching = True
        try:
            if not self.ui.prepare_matching():
                return
            dialog = MatchDialog(self.ui, self)
            dialog.setWindowTitle(f"Match RFID tag for mouse {row + 1}")
            if dialog.exec() and dialog.selected and generation == self._generation and target.isValid():
                self.table.item(target.row(), target.column()).setText(dialog.selected[0])
        finally:
            self._matching = False

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
        self._generation += 1
        self._loading = True
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
        for key, box in self.same.items():
            self._sharing_changed(key, box.isChecked())
        self._notify_tags()
