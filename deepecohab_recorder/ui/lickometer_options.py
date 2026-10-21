"""Advanced safeguards are edited as one validated configuration."""

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QSpinBox,
    QTimeEdit,
)
from deepecohab_recorder.control_settings import validate_options
from deepecohab_recorder.ui.common import scroll, wrapping_form

LABELS = {
    "daily_enabled": "Daily recalibration",
    "daily_time_hhmm": "Daily hour on fixed experiment clock",
    "quiet_seconds": "All-channel quiet period (seconds)",
    "stuck_active_enabled": "Reset after uninterrupted activation",
    "stuck_active_seconds": "Uninterrupted activation fault (seconds)",
    "long_active_warning_seconds": "Long-active warning (seconds)",
    "high_rate_enabled": "High-rate warning / quiet-period recalibration",
    "high_rate_edges_per_second": "Rising edges per channel per second",
    "high_rate_window_seconds": "Consecutive high-rate seconds",
    "continuous_activity_enabled": "Continuous-activity warning / quiet-period recalibration",
    "continuous_activity_seconds": "Continuous activity duration (seconds)",
    "natural_pause_ms": "Natural pause (milliseconds)",
    "sensor_fault_recovery_enabled": "Automatic sensor communication recovery",
    "retry_delays_seconds": "Sensor retry delays (seconds, comma-separated)",
    "heartbeat_interval_seconds": "Status polling interval (seconds)",
    "heartbeat_timeout_seconds": "Heartbeat timeout (seconds)",
    "reconnect_enabled": "Reconnect USB automatically",
    "reconnect_delays_seconds": "USB retry delays (seconds, comma-separated)",
}


class LickometerOptionsDialog(QDialog):
    def __init__(self, recalibration, connection, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced lickometer options")
        self.resize(760, 720)
        self.values = validate_options(recalibration, connection)
        self.editors = []
        root = QVBoxLayout(self)
        body = QWidget()
        layout = QVBoxLayout(body)
        note = QLabel(
            "Routine recalibration always waits for quiet. The uninterrupted-activation fault and a confirmed sensor communication failure can interrupt sensing. All interruptions are recorded as missing-data boundaries. Daily time follows the fixed experiment clock; it can differ from local wall time after a daylight-saving change."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        for title, values in zip(
            ("Autonomous Arduino safeguards", "GUI communication monitoring"),
            self.values,
        ):
            group = QGroupBox(title)
            form = wrapping_form(group)
            editors = {}
            for key, value in values.items():
                if key not in LABELS:
                    continue
                if isinstance(value, bool):
                    editor = QCheckBox()
                    editor.setChecked(value)
                elif key == "daily_time_hhmm":
                    editor = QTimeEdit(QTime.fromString(value, "HH:mm"))
                    editor.setDisplayFormat("HH:mm")
                elif isinstance(value, int):
                    editor = QSpinBox()
                    editor.setRange(1, 86400)
                    editor.setValue(value)
                else:
                    editor = QLineEdit(", ".join(map(str, value)))
                form.addRow(LABELS[key] + ":", editor)
                editors[key] = editor
            self.editors.append(editors)
            layout.addWidget(group)
        root.addWidget(scroll(body))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def accept(self):
        try:
            result = []
            for original, editors in zip(self.values, self.editors):
                values = dict(original)
                for key, editor in editors.items():
                    if isinstance(editor, QCheckBox):
                        value = editor.isChecked()
                    elif isinstance(editor, QTimeEdit):
                        value = editor.time().toString("HH:mm")
                    elif isinstance(editor, QSpinBox):
                        value = editor.value()
                    else:
                        value = [int(x.strip()) for x in editor.text().split(",")]
                    values[key] = value
                result.append(values)
            self.values = validate_options(*result)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid safeguard settings", str(exc))
            return
        super().accept()
