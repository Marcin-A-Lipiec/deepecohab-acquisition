from __future__ import annotations

import time
from typing import Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
)

from deepecohab_recorder.models import (
    BoardConfig,
    NEW_DAQ,
    OLD_DAQ,
    SUPPORTED_DAQ_TYPES,
)
from deepecohab_recorder.protocols.rfid import TTL_CHANNELS, protocol_for_hardware
from deepecohab_recorder.ui.theme import indicator_style, manager


def _indicator_style(active: bool) -> str:
    return indicator_style(active)


class BoardPanel(QGroupBox):
    configuration_changed = Signal()

    def __init__(self, board_id: int, default_active: bool, parent=None) -> None:
        super().__init__(f"RFID Data Acquisition Board {board_id}", parent)
        self.board_id = board_id
        self._ttl_labels: Dict[str, QLabel] = {}
        self._rfid_labels: Dict[str, QLabel] = {}
        self._last_tag_snapshot: Dict[str, int] = {}

        root = QVBoxLayout(self)
        setup = QFormLayout()
        self.active_check = QCheckBox("Active")
        self.active_check.setChecked(default_active)
        self.hardware_combo = QComboBox()
        self.hardware_combo.addItems(SUPPORTED_DAQ_TYPES)
        self.com_combo = QComboBox()
        self.com_combo.setEditable(True)
        setup.addRow("Enabled:", self.active_check)
        setup.addRow("Hardware version:", self.hardware_combo)
        setup.addRow("DAQ COM:", self.com_combo)
        root.addLayout(setup)

        self.status_label = QLabel("Inactive" if not default_active else "Ready")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.activity_group = QGroupBox("TTL and RFID antenna activity")
        self.activity_layout = QVBoxLayout(self.activity_group)
        root.addWidget(self.activity_group)

        self.totals_group = QGroupBox("Tag totals")
        totals_layout = QVBoxLayout(self.totals_group)
        self.tag_table = QTableWidget(0, 2)
        self.tag_table.setHorizontalHeaderLabels(["RFID tag", "Reads"])
        self.tag_table.horizontalHeader().setStretchLastSection(True)
        self.tag_table.setMinimumHeight(140)
        self._last_totals_update = 0
        totals_layout.addWidget(self.tag_table)
        root.addWidget(self.totals_group, 1)

        self.active_check.toggled.connect(self._apply_enabled)
        self.active_check.toggled.connect(self.configuration_changed)
        self.hardware_combo.currentTextChanged.connect(self._hardware_changed)
        self.hardware_combo.currentTextChanged.connect(self.configuration_changed)
        self.com_combo.currentTextChanged.connect(self.configuration_changed)
        self._apply_enabled(default_active)
        self._hardware_changed(self.hardware_combo.currentText())
        manager().changed.connect(self.apply_theme)

    def apply_theme(self, *args):
        for led in (*self._ttl_labels.values(), *self._rfid_labels.values()):
            led.setStyleSheet(_indicator_style(bool(led.property("activity"))))

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if widget:
                widget.deleteLater()
            if child:
                self._clear_layout(child)

    def _hardware_changed(self, hardware: str) -> None:
        self._clear_layout(self.activity_layout)
        self._ttl_labels.clear()
        self._rfid_labels.clear()

        ttl_box = QGroupBox("TTL inputs")
        ttl_grid = QGridLayout(ttl_box)
        if hardware == OLD_DAQ:
            unavailable = QLabel("Unavailable for the currently chosen hardware")
            unavailable.setProperty("tone", "muted")
            ttl_grid.addWidget(unavailable, 0, 0, 1, 4)
        else:
            for col, ch in enumerate(TTL_CHANNELS):
                led = QLabel()
                led.setStyleSheet(_indicator_style(False))
                text = QLabel(ch.replace("TTL", "TTL "))
                pair = QHBoxLayout()
                pair.addWidget(led)
                pair.addWidget(text)
                pair.addStretch(1)
                ttl_grid.addLayout(pair, 0, col)
                self._ttl_labels[ch] = led
        self.activity_layout.addWidget(ttl_box)

        antenna_box = QGroupBox("RFID antennas")
        grid = QGridLayout(antenna_box)
        channels = protocol_for_hardware(hardware).rfid_channels
        columns = 4
        for idx, ch in enumerate(channels):
            led = QLabel()
            led.setStyleSheet(_indicator_style(False))
            label = QLabel(ch)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            pair_widget = QWidget()
            pair = QHBoxLayout(pair_widget)
            pair.setContentsMargins(2, 2, 2, 2)
            pair.setSpacing(6)
            pair.addWidget(led)
            pair.addWidget(label)
            pair.addStretch(1)
            grid.addWidget(pair_widget, idx // columns, idx % columns)
            self._rfid_labels[ch] = led
        self.activity_layout.addWidget(antenna_box)
        self.activity_layout.addStretch(1)

    def _apply_enabled(self, enabled: bool) -> None:
        for widget in (
            self.hardware_combo,
            self.com_combo,
            self.tag_table,
            self.activity_group,
            self.totals_group,
        ):
            widget.setEnabled(enabled)
        if not enabled:
            self.status_label.setText("Inactive")

    def set_running_state(self, running: bool) -> None:
        self.active_check.setEnabled(not running)
        editable = self.active_check.isChecked() and not running
        self.hardware_combo.setEnabled(editable)
        self.com_combo.setEnabled(editable)

    def set_ports(self, ports: list[str]) -> None:
        current = self.com_combo.currentText().strip()
        self.com_combo.blockSignals(True)
        self.com_combo.clear()
        self.com_combo.addItems(ports)
        if current:
            idx = self.com_combo.findText(current)
            if idx < 0:
                self.com_combo.addItem(current)
                idx = self.com_combo.findText(current)
            self.com_combo.setCurrentIndex(idx)
        self.com_combo.setCurrentText(current)
        self.com_combo.blockSignals(False)

    def config(self) -> BoardConfig:
        return BoardConfig(
            board_id=self.board_id,
            active=self.active_check.isChecked(),
            hardware=self.hardware_combo.currentText(),
            com_port=self.com_combo.currentText().strip(),
            baudrate=115200,
            raw_log_enabled=True,
        )

    def apply_config(self, config: dict) -> None:
        self.active_check.setChecked(bool(config.get("active", self.board_id == 1)))
        hardware = str(config.get("hardware") or NEW_DAQ)
        idx = self.hardware_combo.findText(hardware)
        if idx >= 0:
            self.hardware_combo.setCurrentIndex(idx)
        port = str(config.get("com_port") or "")
        if port:
            idx = self.com_combo.findText(port)
            if idx < 0:
                self.com_combo.addItem(port)
                idx = self.com_combo.findText(port)
            self.com_combo.setCurrentIndex(idx)

        self.com_combo.setCurrentText(port)

    def update_from_controller(self, controller) -> None:
        if controller is None:
            return
        self.status_label.setText(controller.status_text())
        snapshot = controller.get_status_snapshot()
        now = time.monotonic()
        for ch, led in self._ttl_labels.items():
            active = now - float(snapshot.get(ch, {}).get("t", 0.0)) <= 0.35
            if led.property("activity") != active:
                led.setProperty("activity", active)
                led.setStyleSheet(_indicator_style(active))
        for ch, led in self._rfid_labels.items():
            active = now - float(snapshot.get(ch, {}).get("t", 0.0)) <= 1.5
            if led.property("activity") != active:
                led.setProperty("activity", active)
                led.setStyleSheet(_indicator_style(active))
        if now - self._last_totals_update < 1:
            return
        self._last_totals_update = now
        tags = controller.get_tag_counts_snapshot()
        if tags != self._last_tag_snapshot:
            self._last_tag_snapshot = tags
            ordered = sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))
            self.tag_table.setRowCount(len(ordered))
            for row, (tag, count) in enumerate(ordered):
                self.tag_table.setItem(row, 0, QTableWidgetItem(tag))
                self.tag_table.setItem(row, 1, QTableWidgetItem(str(count)))
