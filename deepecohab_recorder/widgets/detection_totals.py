"""Read-only live counters: allocate data once, paint only visible cells."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView, QSizePolicy, QTableView
from deepecohab_recorder.ui.theme import color, manager


class DetectionTotalsModel(QAbstractTableModel):
    def __init__(self, tags=False, parent=None):
        super().__init__(parent)
        self.tags = tags
        self.counts = {}
        self.names = []
        self.known_tags = frozenset()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.names)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 2

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return ("RFID tag" if self.tags else "TTL channel", "Reads")[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.names):
            return None
        name = self.names[index.row()]
        unknown = self.tags and name.strip().upper() not in self.known_tags
        if role == Qt.DisplayRole:
            return name if index.column() == 0 else str(self.counts[name])
        if role == Qt.ForegroundRole and unknown:
            return color("ink-3")
        if role == Qt.ToolTipRole:
            return name + ("\nTag is not listed in Animal details." if unknown else "")
        if role == Qt.TextAlignmentRole and index.column() == 1:
            return Qt.AlignRight | Qt.AlignVCenter
        return None

    def set_counts(self, counts):
        if counts == self.counts:
            return
        names = (sorted(counts, key=lambda name: (-counts[name], name))
                 if self.tags else sorted(counts))
        if set(counts) != set(self.counts):
            self.beginResetModel()
            self.counts, self.names = dict(counts), names
            self.endResetModel()
        elif names != self.names:
            old_indices = self.persistentIndexList()
            old_names = [self.names[index.row()] for index in old_indices]
            self.layoutAboutToBeChanged.emit()
            self.counts, self.names = dict(counts), names
            rows = {name: row for row, name in enumerate(names)}
            self.changePersistentIndexList(old_indices, [self.index(rows[name], index.column())
                                            for name, index in zip(old_names, old_indices)])
            self.layoutChanged.emit()
            self.repaint_data()
        else:
            self.counts = dict(counts)
            self.repaint_data()

    def set_known_tags(self, tags):
        known = frozenset(str(tag).strip().upper() for tag in tags if str(tag).strip())
        if known != self.known_tags:
            self.known_tags = known
            self.repaint_data()

    def repaint_data(self, *args):
        if self.names:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.names) - 1, 1),
                                  [Qt.DisplayRole, Qt.ForegroundRole, Qt.ToolTipRole])


class DetectionTotalsTable(QTableView):
    def __init__(self, *, tags=False, parent=None):
        super().__init__(parent)
        self.setObjectName("detectionTotalsTable")
        self.totals = DetectionTotalsModel(tags, self)
        self.setModel(self.totals)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(22)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.horizontalHeader().setMinimumSectionSize(35)
        # Identical viewport widths even when only one table has many rows.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setMinimumSize(130, 114)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.setAccessibleName("Total tag detections" if tags else "Total TTL detections")
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self)
        self.copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self.copy_selection)
        manager().changed.connect(self.totals.repaint_data)

    def copy_selection(self):
        rows = sorted({index.row() for index in self.selectionModel().selectedIndexes()})
        if rows:
            QApplication.clipboard().setText("\n".join(
                "\t".join(str(self.totals.data(self.totals.index(row, col))) for col in range(2))
                for row in rows))
