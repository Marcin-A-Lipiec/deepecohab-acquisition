"""Measured centre-to-centre distances between successive tunnel antennas."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QDoubleSpinBox,
)


class TunnelDistances(QTableWidget):
    def __init__(self, antennas, distances=(), parent=None):
        super().__init__(0, 2, parent)
        self.setObjectName("tunnel_antenna_distances")
        self._antennas = list(antennas)
        self._saved_values = list(distances)
        self.setHorizontalHeaderLabels(["Successive antennas", "Distance (cm)"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.verticalHeader().hide()
        self.setMinimumWidth(380)
        self.set_antenna_count(len(antennas))

    def set_antenna_count(self, count):
        self._saved_values[:self.rowCount()] = self.values()
        self.setRowCount(max(0, count - 1))
        labels = (self._antennas + ["?"] * count)[:count]
        for row, (left, right) in enumerate(zip(labels, labels[1:])):
            left = left if left != "?" else f"Position {row + 1} (unassigned)"
            right = right if right != "?" else f"Position {row + 2} (unassigned)"
            label = QTableWidgetItem(f"{left} ↔ {right}")
            label.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            label.setToolTip(label.text())
            self.setItem(row, 0, label)
            value = self._saved_values[row] if row < len(self._saved_values) else None
            control = QDoubleSpinBox()
            control.setDecimals(3)
            control.setRange(0, max(1_000_000, value or 0))
            control.setSpecialValueText("Not set")
            control.setSuffix(" cm")
            control.setValue(value or 0)
            control.setToolTip("Measured along the tunnel, between antenna centres. Set 0 to leave the distance unknown.")
            self.setCellWidget(row, 1, control)
        self.setFixedHeight(min(280, max(75, 36 + self.rowCount() * 36)))

    def values(self):
        return [self.cellWidget(row, 1).value() or None for row in range(self.rowCount())]
