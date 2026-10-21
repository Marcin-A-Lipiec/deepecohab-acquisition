"""Fixed experiment schedule and optional autonomous lighting controller."""

import copy
from dataclasses import asdict
from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QTimeEdit,
)
from deepecohab_recorder.clock import (
    ExperimentClock,
    fixed_phase_state,
    schedule_defaults,
)
from deepecohab_recorder.models import LightingConfig
from deepecohab_recorder.ui.common import config_buttons, wrapping_form
from deepecohab_recorder.utils import list_serial_ports


class EnvironmentalSettingsTab(QWidget):
    paths = (
        "recording.timeline.phases",
        "recording.timeline.phase_schedule",
        "acquisition.environment",
    )
    hardware = True

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self._schedule = schedule_defaults()
        self._loaded_phases = {"light_phase": "00:00", "dark_phase": "12:00"}
        self._lighting = asdict(LightingConfig())
        layout = QVBoxLayout(self)
        layout.addLayout(config_buttons(self, "environmental settings"))
        group = QGroupBox("Fixed light / dark cycle")
        form = wrapping_form(group)
        self.light, self.dark = QTimeEdit(QTime(0, 0)), QTimeEdit(QTime(12, 0))
        for editor in (self.light, self.dark):
            editor.setDisplayFormat("HH:mm")
        form.addRow("Light phase start hour:", self.light)
        form.addRow("Dark phase start hour:", self.dark)
        info = QLabel(
            "Hours define the initial cycle (12 hours light / 12 hours dark by default). Once anchored, the cycle follows elapsed time. Daylight-saving changes do not shorten, lengthen or realign phases."
        )
        info.setWordWrap(True)
        form.addRow(info)
        self.phase_status = QLabel()
        self.phase_status.setWordWrap(True)
        form.addRow(self.phase_status)
        layout.addWidget(group)
        self.time_group = group
        note = QLabel("Experiment timezone and analysis start phase are set on Setup.")
        note.setWordWrap(True)
        layout.addWidget(note)
        hardware = QGroupBox("Arduino lighting")
        form = wrapping_form(hardware)
        self.enabled = QCheckBox("Use Arduino-controlled light / dark cycle")
        self.port = QComboBox()
        self.port.setEditable(True)
        refresh = QPushButton("Refresh COM ports")
        refresh.clicked.connect(self.refresh_ports)
        portrow = QHBoxLayout()
        portrow.addWidget(self.port, 1)
        portrow.addWidget(refresh)
        form.addRow(self.enabled)
        form.addRow("Lighting Arduino COM port:", portrow)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Connect / monitor")
        self.apply_button = QPushButton("Apply schedule and start lighting")
        self.disconnect_button = QPushButton("Disconnect monitoring")
        self.stop_button = QPushButton("Stop lighting (output off)")
        for button in (
            self.connect_button,
            self.apply_button,
            self.disconnect_button,
            self.stop_button,
        ):
            buttons.addWidget(button)
        form.addRow(buttons)
        self.hardware_status = QLabel(
            "Metadata only. Lights are controlled externally."
        )
        self.hardware_status.setWordWrap(True)
        form.addRow(self.hardware_status)
        layout.addWidget(hardware)
        self.connect_button.clicked.connect(lambda: self.command("connect"))
        self.apply_button.clicked.connect(lambda: self.command("apply"))
        self.disconnect_button.clicked.connect(lambda: self.command("disconnect"))
        self.stop_button.clicked.connect(lambda: self.command("stop"))
        placeholder = QLabel(
            "Temperature and humidity recording will be added in a future version."
        )
        placeholder.setWordWrap(True)
        layout.addWidget(placeholder)
        layout.addStretch(1)

    def refresh_ports(self):
        current = self.port.currentText()
        self.port.clear()
        self.port.addItems(list_serial_ports())
        self.port.setCurrentText(current)

    def read(self):
        zone = self.ui.experiment_timezone()
        ExperimentClock(zone)
        phases = {
            "light_phase": self.light.time().toString("HH:mm"),
            "dark_phase": self.dark.time().toString("HH:mm"),
        }
        if phases["light_phase"] == phases["dark_phase"]:
            raise ValueError("Light and dark phase start hours must differ.")
        saved = self.ui.services.repo.data["recording"]["timeline"].get(
            "phase_schedule", {}
        )
        schedule = copy.deepcopy(
            saved if saved.get("anchor_datetime") else self._schedule
        )
        if phases != self._loaded_phases:
            if schedule.get("anchor_datetime"):
                raise ValueError(
                    "This experiment already has a fixed schedule. Use a new experiment configuration to change phase hours."
                )
            schedule = schedule_defaults(phases)
        lighting = dict(
            self._lighting,
            enabled=self.enabled.isChecked(),
            com_port=self.port.currentText().strip(),
        )
        if self.ui.services.lighting.controller:
            lighting["device_uid"] = self.ui.services.lighting.controller.uid
        return {
            "recording.timeline.phases": phases,
            "recording.timeline.phase_schedule": schedule,
            "acquisition.environment.lighting": lighting,
        }

    def load(self, data):
        timeline = data["recording"]["timeline"]
        self._loaded_phases = copy.deepcopy(timeline["phases"])
        self.light.setTime(QTime.fromString(timeline["phases"]["light_phase"], "HH:mm"))
        self.dark.setTime(QTime.fromString(timeline["phases"]["dark_phase"], "HH:mm"))
        self._schedule = copy.deepcopy(
            timeline.get("phase_schedule", schedule_defaults(timeline["phases"]))
        )
        self._lighting = asdict(LightingConfig())
        self._lighting.update(
            data["acquisition"].get("environment", {}).get("lighting", {})
        )
        self.enabled.setChecked(self._lighting["enabled"])
        self.port.setCurrentText(self._lighting["com_port"])

    def command(self, action):
        try:
            if not self.ui.prepare_matching():
                return
            updates = self.read()
            self.ui.services.repo.update_paths(updates)
            if action == "stop":
                if (
                    QMessageBox.question(
                        self,
                        "Stop lighting",
                        "Stop the independent lighting schedule and switch its output off?",
                    )
                    != QMessageBox.Yes
                ):
                    return
            args = ()
            if action == "connect":
                args = (updates["acquisition.environment.lighting"],)
            if action == "apply":
                args = (self.ui.services.repo.data["recording"]["timeline"],)
            self.ui.services.lighting.submit(action, *args)
        except Exception as exc:
            QMessageBox.warning(self, "Lighting controller", str(exc))

    def refresh(self):
        service = self.ui.services.lighting
        link = service.controller
        timeline = self.ui.services.repo.data["recording"]["timeline"]
        schedule = timeline.get("phase_schedule", {})
        anchored = bool(schedule.get("anchor_datetime"))
        locked = self.ui.services.busy() or service.connected or service.busy
        self.time_group.setEnabled(not locked and not anchored)
        self.port.setEnabled(not service.connected and not service.busy)
        self.enabled.setEnabled(not service.connected and not service.busy)
        self.connect_button.setEnabled(
            self.enabled.isChecked() and not service.connected and not service.busy
        )
        for b in (self.apply_button, self.disconnect_button, self.stop_button):
            b.setEnabled(service.connected and not service.busy)
        if anchored:
            try:
                state = fixed_phase_state(
                    schedule,
                    self.ui.services.experiment_now(),
                    timeline["recording_timezone"],
                )
                self.phase_status.setText(
                    f"Current phase: {state['phase'].replace('_phase', '')}. Next switch: {state['next_transition_datetime']}"
                )
            except ValueError as exc:
                self.phase_status.setText(str(exc))
        else:
            self.phase_status.setText(
                "The cycle will be anchored when lighting is applied or the first RFID board starts."
            )
        if link:
            status = link.inbox.status
            detail = (
                f"{link.state} | {status.mode} | clock: {status.clock_source} | fault: {status.fault}"
                if status
                else link.state
            )
            self.hardware_status.setText(
                detail
                + (
                    "\n" + (service.error or link.last_error)
                    if service.error or link.last_error
                    else ""
                )
            )
        else:
            self.hardware_status.setText(
                service.error
                or "Monitoring disconnected. Any schedule already running on the Arduino continues."
            )
