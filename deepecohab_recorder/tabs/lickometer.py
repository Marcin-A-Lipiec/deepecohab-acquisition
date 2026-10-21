from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QSpinBox,
)

from deepecohab_recorder.models import LickometerConfig
from deepecohab_recorder.ui.common import config_buttons
from dataclasses import asdict
from deepecohab_recorder.utils import list_serial_ports
from deepecohab_recorder.ui.theme import colors, indicator_style, manager


def led_style(active: bool) -> str:
    return indicator_style(active, 24) + f' border:2px solid {colors()["line-strong"]};'


class LickometerTab(QWidget):
    """USB recording controls and live observations of an autonomous device."""

    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.ui = app
        self.app = app.services
        root = QVBoxLayout(self)
        root.addLayout(config_buttons(self, "lickometer settings"))

        setup_group = QGroupBox("Four-channel capacitive lickometer")
        setup_form = QFormLayout(setup_group)
        self.enabled_check = QCheckBox("Use lickometer USB recording")
        self.com_combo = QComboBox()
        self.com_combo.setEditable(True)
        self.refresh_button = QPushButton("Refresh COM ports")
        port_widget = QWidget()
        port_row = QHBoxLayout(port_widget)
        port_row.setContentsMargins(0, 0, 0, 0)
        port_row.addWidget(self.com_combo)
        port_row.addWidget(self.refresh_button)
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(115200, 115200)
        self.baud_spin.setValue(115200)
        setup_form.addRow("Enabled:", self.enabled_check)
        setup_form.addRow("COM port:", port_widget)
        setup_form.addRow("Baud:", self.baud_spin)
        root.addWidget(setup_group)

        hardware_group = QGroupBox("Fixed hardware mapping")
        hardware_layout = QGridLayout(hardware_group)
        headers = (
            "Logical channel",
            "MPR121 electrode",
            "Arduino output",
            "BNC output",
        )
        for column, value in enumerate(headers):
            label = QLabel(value)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("font-weight:600;")
            hardware_layout.addWidget(label, 0, column)

        mappings = [
            ("Channel 1", "E1", "D8", "BNC 1"),
            ("Channel 2", "E3", "D9", "BNC 2"),
            ("Channel 3", "E5", "D10", "BNC 3"),
            ("Channel 4", "E7", "D11", "BNC 4"),
        ]
        for row, mapping in enumerate(mappings, start=1):
            for column, value in enumerate(mapping):
                label = QLabel(value)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                hardware_layout.addWidget(label, row, column)
        description = QLabel(
            "The mapping above is fixed because it matches the last hardware-verified working firmware. "
            "The Arduino senses and drives its BNC outputs whenever powered. Starting the lickometer in "
            "LAUNCH opens USB recording; it does not enable or disable the physical BNC function."
        )
        description.setWordWrap(True)
        description.setProperty("tone", "muted")
        hardware_layout.addWidget(description, 5, 0, 1, 4)

        channels_group = QGroupBox("Live lickometer channel activity")
        channel_grid = QGridLayout(channels_group)
        self.channel_widgets = {}
        for channel in range(1, 5):
            box = QGroupBox(f"Channel {channel}")
            layout = QVBoxLayout(box)
            led = QLabel()
            led.setStyleSheet(led_style(False))
            state = QLabel("Unknown")
            state.setAlignment(Qt.AlignmentFlag.AlignCenter)
            count = QLabel("Contacts: 0")
            count.setAlignment(Qt.AlignmentFlag.AlignCenter)
            last = QLabel("Last event: —")
            last.setWordWrap(True)
            last.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(led, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(state)
            layout.addWidget(count)
            layout.addWidget(last)
            channel_grid.addWidget(box, (channel - 1) // 2, (channel - 1) % 2)
            self.channel_widgets[channel] = {
                "led": led,
                "state": state,
                "count": count,
                "last": last,
            }
        root.addWidget(channels_group)

        control_group = QGroupBox("Passive USB recording")
        control_layout = QHBoxLayout(control_group)
        self.start_button = QPushButton("Start USB recording")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("Stop USB recording")
        self.clear_counters_button = QPushButton("Clear GUI counters")
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)
        control_layout.addWidget(self.clear_counters_button)
        root.insertWidget(2, control_group)
        self.start_button.clicked.connect(self._start_usb)
        self.stop_button.clicked.connect(lambda: self.ui.stop_devices(["lickometer"]))
        autonomy = QLabel(
            "Firmware v7 runs autonomously: routine maintenance every 24 hours of powered operation, "
            "after 5 quiet seconds; recovery after a 90-second uninterrupted activation or sensor fault. "
            "USB provides live observations and CSV recording. Safeguard settings are defined in the Arduino sketch."
        )
        autonomy.setWordWrap(True)
        root.addWidget(autonomy)

        output_group = QGroupBox("Output files")
        output_layout = QVBoxLayout(output_group)
        output_text = QLabel(
            "Touch and release events are saved to <session>_lickometer1_data.csv in the same five-column "
            "shape as RFID data. Calibration and warnings are saved to lickometer1_status.csv. Every raw "
            "Parsed Arduino events and status rows are preserved in CSV. Bounded malformed examples and monitoring checkpoints are retained in GUI state. Status rows mark reset/calibration gaps and the preceding event index. STATE snapshots do not count as contacts. Periodic states monitor USB reception; observation gaps are logged."
        )
        output_text.setWordWrap(True)
        output_text.setProperty("tone", "muted")
        output_layout.addWidget(output_text)
        self.file_label = QLabel("Event CSV: —")
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        output_layout.addWidget(self.file_label)
        root.addWidget(output_group)

        status_group = QGroupBox("USB recording status")
        status_layout = QVBoxLayout(status_group)
        self.status_label = QLabel("Stopped")
        self.status_label.setWordWrap(True)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setProperty("tone", "error")
        self.warning_label.setStyleSheet("font-weight:600;")
        self.admin_label = QLabel("Last device message: —")
        self.admin_label.setWordWrap(True)
        self.quality_label = QLabel(
            "Initial channel states are unknown until reported by the device."
        )
        self.quality_label.setWordWrap(True)
        self.raw_label = QLabel("Last raw line: —")
        self.raw_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.warning_label)
        status_layout.addWidget(self.admin_label)
        status_layout.addWidget(self.raw_label)
        status_layout.addWidget(self.quality_label)
        root.addWidget(status_group)
        root.addWidget(hardware_group)
        root.addStretch(1)

        self.refresh_button.clicked.connect(self.refresh_ports)
        self.clear_counters_button.clicked.connect(self._clear_gui_counters)
        manager().changed.connect(self.apply_theme)

    def apply_theme(self, *args):
        for widgets in self.channel_widgets.values():
            led = widgets["led"]
            led.setStyleSheet(led_style(bool(led.property("activity"))))

    def refresh_ports(self):
        current = self.com_combo.currentText()
        self.com_combo.clear()
        self.com_combo.addItems(list_serial_ports())
        if current and self.com_combo.findText(current) < 0:
            self.com_combo.addItem(current)
        self.com_combo.setCurrentText(current)

    def set_running_state(self, running: bool) -> None:
        self.enabled_check.setEnabled(not running)
        self.com_combo.setEnabled(not running)
        self.refresh_button.setEnabled(not running)
        self.baud_spin.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def config(self) -> LickometerConfig:
        return LickometerConfig(
            enabled=self.enabled_check.isChecked(),
            com_port=self.com_combo.currentText().strip(),
            baudrate=self.baud_spin.value(),
            device_id=1,
            electrode_1=1,
            electrode_2=3,
            electrode_3=5,
            electrode_4=7,
        )

    def apply_config(self, data: dict):
        if not isinstance(data, dict):
            return
        self.enabled_check.setChecked(bool(data.get("enabled", False)))
        port = str(data.get("com_port") or "")
        if port and self.com_combo.findText(port) < 0:
            self.com_combo.addItem(port)
        self.com_combo.setCurrentText(port)
        self.baud_spin.setValue(115200)

    def _start_usb(self):
        self.enabled_check.setChecked(True)
        self.ui.start_devices(["lickometer"])

    def _clear_gui_counters(self):
        if self.app.lickometer_controller:
            self.app.lickometer_controller.clear_gui_counters()

    def update_from_controller(self, controller):
        if not controller:
            return

        self.status_label.setText(controller.status_text())
        self.file_label.setText("Event CSV: " + str(controller.data_file or "—"))
        warnings = []
        if controller.protocol_warning:
            warnings.append(controller.protocol_warning)
        if controller.last_error:
            warnings.append(controller.last_error)
        self.warning_label.setText("\n".join(dict.fromkeys(warnings)))
        self.admin_label.setText(
            "Last device message: " + (controller.last_admin_message or "—")
        )
        self.raw_label.setText("Last raw line: " + (controller.last_raw_line or "—"))

        self.quality_label.setText(controller.quality_note)
        snapshot = controller.channel_snapshot()
        for channel, widgets in self.channel_widgets.items():
            data = snapshot[channel]
            active = bool(data["active"])
            if widgets["led"].property("activity") != active:
                widgets["led"].setProperty("activity", active)
                widgets["led"].setStyleSheet(led_style(active))
            widgets["state"].setText(
                "Unknown"
                if data["active"] is None
                else "ACTIVE" if active else "Inactive"
            )
            widgets["count"].setText(f"Contacts: {data['events']:,}")
            widgets["last"].setText("Last event: " + (data["last_event_iso"] or "—"))

    paths = ("acquisition.lickometer",)
    hardware = True

    def read(self):
        return {"acquisition.lickometer": asdict(self.config())}

    def load(self, data):
        self.apply_config(data["acquisition"]["lickometer"])

    def refresh(self):
        self.set_running_state(self.ui.services.busy("lickometer"))
        self.update_from_controller(self.ui.services.lickometer_controller)
