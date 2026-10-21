from __future__ import annotations

from dataclasses import asdict
from deepecohab_recorder.clock import ExperimentClock

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QTimeEdit,
)

from deepecohab_recorder.controllers.trigger import trigger_timing
from deepecohab_recorder.models import TriggerConfig
from deepecohab_recorder.utils import list_serial_ports
from deepecohab_recorder.widgets.waveform import WaveformPreview


class HardwareTriggerTab(QWidget):
    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.ui = app
        self.app = app.services
        root = QHBoxLayout(self)
        left = QVBoxLayout()

        connection = QGroupBox("Hardware trigger connection")
        cform = QFormLayout(connection)
        self.enabled_check = QCheckBox("Use hardware trigger")
        self.enabled_check.setChecked(True)
        self.com_combo = QComboBox()
        self.com_combo.setEditable(True)
        self.refresh_button = QPushButton("Refresh COM ports")
        port_row = QWidget()
        prow = QHBoxLayout(port_row)
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self.com_combo)
        prow.addWidget(self.refresh_button)
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(300, 3_000_000)
        self.baud_spin.setValue(115200)
        cform.addRow("Enabled:", self.enabled_check)
        cform.addRow("COM port:", port_row)
        cform.addRow("Baud:", self.baud_spin)
        left.addWidget(connection)

        timing = QGroupBox("TTL timing")
        tform = QFormLayout(timing)
        self.timing_mode_combo = QComboBox()
        self.timing_mode_combo.addItems(["Frequency", "Interval HH:MM:SS:MS"])
        self.frequency_spin = QDoubleSpinBox()
        self.frequency_spin.setRange(0.003333, 10000)
        self.frequency_spin.setDecimals(6)
        self.frequency_spin.setValue(30.0)
        self.frequency_spin.setSuffix(" Hz")
        self.interval_edit = QLineEdit("00:00:00:033")
        self.pulse_spin = QDoubleSpinBox()
        self.pulse_spin.setRange(0.001, 300000)
        self.pulse_spin.setDecimals(3)
        self.pulse_spin.setValue(4.0)
        self.pulse_spin.setSuffix(" ms")
        tform.addRow("Timing input:", self.timing_mode_combo)
        tform.addRow("Frequency:", self.frequency_spin)
        tform.addRow("Interval:", self.interval_edit)
        tform.addRow("Pulse width:", self.pulse_spin)
        left.addWidget(timing)

        train = QGroupBox("Pulse train")
        trform = QFormLayout(train)
        self.train_mode_combo = QComboBox()
        self.train_mode_combo.addItems(["Duration", "Pulse count"])
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 2_000_000_000)
        self.count_spin.setValue(12_960_000)
        duration = QWidget()
        drow = QHBoxLayout(duration)
        drow.setContentsMargins(0, 0, 0, 0)
        self.days_spin = QSpinBox()
        self.days_spin.setRange(0, 3650)
        self.days_spin.setValue(5)
        self.days_spin.setSuffix(" d")
        self.hours_spin = QSpinBox()
        self.hours_spin.setRange(0, 23)
        self.hours_spin.setSuffix(" h")
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, 59)
        self.minutes_spin.setSuffix(" min")
        drow.addWidget(self.days_spin)
        drow.addWidget(self.hours_spin)
        drow.addWidget(self.minutes_spin)
        trform.addRow("Train definition:", self.train_mode_combo)
        trform.addRow("Count:", self.count_spin)
        trform.addRow("Duration:", duration)
        left.addWidget(train)

        schedule = QGroupBox("Start mode")
        sform = QFormLayout(schedule)
        self.start_mode_combo = QComboBox()
        self.start_mode_combo.addItems(["Immediate", "Scheduled within next 24 h"])
        self.current_time_label = QLabel("Current PC time: --:--:--")
        self.scheduled_time = QTimeEdit()
        self.scheduled_time.setDisplayFormat("HH:mm:ss")
        self.scheduled_time.setTime(QTime.currentTime().addSecs(60))
        sform.addRow("Mode:", self.start_mode_combo)
        sform.addRow(self.current_time_label)
        sform.addRow("Scheduled start:", self.scheduled_time)
        left.addWidget(schedule)

        self.derived_label = QLabel()
        self.derived_label.setWordWrap(True)
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        left.addWidget(self.derived_label)
        left.addWidget(self.status_label)
        left.addStretch(1)
        root.addLayout(left, 1)
        self.preview = WaveformPreview()
        root.addWidget(self.preview, 1, Qt.AlignTop)

        self.refresh_button.clicked.connect(self.refresh_ports)
        for widget in [
            self.timing_mode_combo,
            self.frequency_spin,
            self.interval_edit,
            self.pulse_spin,
            self.train_mode_combo,
            self.count_spin,
            self.days_spin,
            self.hours_spin,
            self.minutes_spin,
            self.start_mode_combo,
            self.scheduled_time,
        ]:
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._update_derived)
            elif hasattr(widget, "currentTextChanged"):
                widget.currentTextChanged.connect(self._update_derived)
            elif hasattr(widget, "textChanged"):
                widget.textChanged.connect(self._update_derived)
            elif hasattr(widget, "timeChanged"):
                widget.timeChanged.connect(self._update_derived)
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start()
        self._update_derived()

    def refresh_ports(self) -> None:
        current = self.com_combo.currentText()
        ports = list_serial_ports()
        self.com_combo.clear()
        self.com_combo.addItems(ports)
        if current and self.com_combo.findText(current) < 0:
            self.com_combo.addItem(current)
        self.com_combo.setCurrentText(current)

    def _tick_clock(self) -> None:
        try:
            self.current_time_label.setText(
                "Experiment time: "
                + ExperimentClock(self.ui.experiment_timezone())
                .now()
                .strftime("%H:%M:%S %Z")
            )
        except (ValueError, KeyError):
            self.current_time_label.setText("Choose a valid experiment timezone.")
        controller = self.app.trigger_controller
        self.status_label.setText(controller.status if controller else "Idle")

    def config(self) -> TriggerConfig:
        return TriggerConfig(
            enabled=self.enabled_check.isChecked(),
            com_port=self.com_combo.currentText().strip(),
            baudrate=self.baud_spin.value(),
            timing_mode=(
                "frequency"
                if self.timing_mode_combo.currentIndex() == 0
                else "interval"
            ),
            frequency_hz=self.frequency_spin.value(),
            interval_text=self.interval_edit.text().strip(),
            pulse_ms=self.pulse_spin.value(),
            train_mode=(
                "duration" if self.train_mode_combo.currentIndex() == 0 else "count"
            ),
            count=self.count_spin.value(),
            duration_days=self.days_spin.value(),
            duration_hours=self.hours_spin.value(),
            duration_minutes=self.minutes_spin.value(),
            start_mode=(
                "immediate"
                if self.start_mode_combo.currentIndex() == 0
                else "scheduled"
            ),
            scheduled_hhmmss=self.scheduled_time.time().toString("HH:mm:ss"),
        )

    def _update_derived(self, *args) -> None:
        try:
            cfg = self.config()
            timing = trigger_timing(cfg)
            self.derived_label.setText(
                f"Effective frequency: {timing['frequency_hz']:.6f} Hz | period: {timing['interval_ms']:.3f} ms | "
                f"pulse: {cfg.pulse_ms:.3f} ms | interval between pulses: {max(0.0, timing['interval_ms']-cfg.pulse_ms):.3f} ms | "
                f"pulses: {timing['count']:,} | train length: {timing['train_length_s']/3600:.3f} h"
            )
            self.preview.set_parameters(
                float(timing["interval_ms"]), cfg.pulse_ms, True
            )
        except Exception as exc:
            self.derived_label.setText(f"Invalid settings: {exc}")
            self.preview.set_parameters(1, 1, False, str(exc))

    def apply_config(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.enabled_check.setChecked(bool(data.get("enabled", True)))
        port = str(data.get("com_port") or "")
        if port and self.com_combo.findText(port) < 0:
            self.com_combo.addItem(port)
        self.com_combo.setCurrentText(port)
        self.baud_spin.setValue(int(data.get("baudrate") or 115200))
        self.timing_mode_combo.setCurrentIndex(
            1 if data.get("timing_mode") == "interval" else 0
        )
        self.frequency_spin.setValue(float(data.get("frequency_hz") or 30))
        self.interval_edit.setText(str(data.get("interval_text") or "00:00:00:033"))
        self.pulse_spin.setValue(float(data.get("pulse_ms") or 4))
        self.train_mode_combo.setCurrentIndex(
            1 if data.get("train_mode") == "count" or data.get("mode") == "count" else 0
        )
        self.count_spin.setValue(int(data.get("count") or 12_960_000))
        dur = data.get("duration_dd_hh_mm") or data.get("duration") or {}
        self.days_spin.setValue(int(data.get("duration_days", dur.get("dd", 5))))
        self.hours_spin.setValue(int(data.get("duration_hours", dur.get("hh", 0))))
        self.minutes_spin.setValue(int(data.get("duration_minutes", dur.get("mm", 0))))
        self.start_mode_combo.setCurrentIndex(
            1 if data.get("start_mode") == "scheduled" else 0
        )
        q = QTime.fromString(
            str(data.get("scheduled_hhmmss") or "12:00:00"), "HH:mm:ss"
        )
        if q.isValid():
            self.scheduled_time.setTime(q)
        self._update_derived()

    paths = ("acquisition.hardware_trigger",)
    hardware = True

    def read(self):
        return {"acquisition.hardware_trigger": asdict(self.config())}

    def load(self, data):
        self.apply_config(data["acquisition"]["hardware_trigger"])

    def refresh(self):
        for widget in self.findChildren(QWidget):
            if isinstance(
                widget,
                (QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTimeEdit, QCheckBox),
            ):
                widget.setEnabled(not self.ui.services.busy("trigger"))
