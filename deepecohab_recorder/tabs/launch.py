from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QDoubleSpinBox,
)


class LaunchTab(QWidget):
    paths = ("acquisition.launch",)
    hardware = True

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        root = QVBoxLayout(self)
        text = QLabel(
            "Start selected devices together, or control each device below. RFID boards define the recording start and end. Standalone lickometer BNC events are recorded through their TTL inputs."
        )
        text.setWordWrap(True)
        root.addWidget(text)
        group = QGroupBox("Devices")
        grid = QGridLayout(group)
        self.selected, self.status, self.starts, self.stops = {}, {}, {}, {}
        for row, (key, title) in enumerate(
            [
                ("board1", "RFID DAQ 1"),
                ("board2", "RFID DAQ 2"),
                ("camera", "Camera"),
                ("trigger", "Hardware trigger"),
            ]
        ):
            select = QCheckBox(title)
            select.setChecked(key == "board1")
            self.selected[key] = select
            start, stop = QPushButton("Start"), QPushButton("Stop")
            stop.setProperty("role", "stop")
            start.clicked.connect(
                lambda checked=False, key=key: self.ui.start_devices([key])
            )
            stop.clicked.connect(
                lambda checked=False, key=key: self.ui.stop_devices([key])
            )
            status = QLabel("Stopped")
            status.setWordWrap(True)
            grid.addWidget(select, row, 0)
            grid.addWidget(start, row, 1)
            grid.addWidget(stop, row, 2)
            grid.addWidget(status, row, 3)
            self.status[key], self.starts[key], self.stops[key] = status, start, stop
        grid.setColumnStretch(3, 1)
        root.addWidget(group)
        self.external = QCheckBox("Camera uses external TTL pulses")
        self.external.setChecked(True)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 600)
        self.delay.setValue(2)
        self.delay.setSuffix(" s")
        row = QHBoxLayout()
        row.addWidget(self.external)
        row.addWidget(QLabel("Delay after devices are ready:"))
        row.addWidget(self.delay)
        row.addStretch(1)
        root.addLayout(row)
        buttons = QHBoxLayout()
        start = QPushButton("Start selected devices")
        start.setObjectName("primary")
        stop = QPushButton("Stop all devices")
        stop.setProperty("role", "stop")
        start.clicked.connect(
            lambda: self.ui.start_devices(
                [key for key, box in self.selected.items() if box.isChecked()]
            )
        )
        stop.clicked.connect(lambda: self.ui.stop_devices())
        buttons.addWidget(start)
        buttons.addWidget(stop)
        buttons.addStretch(1)
        root.addLayout(buttons)
        self.timeline = QLabel("No RFID recording started.")
        self.timeline.setWordWrap(True)
        root.addWidget(self.timeline)
        root.addStretch(1)

    def read(self):
        return {
            "acquisition.launch": {
                **{key: box.isChecked() for key, box in self.selected.items()},
                "externally_triggered": self.external.isChecked(),
                "delay_seconds": self.delay.value(),
            }
        }

    def load(self, data):
        value = data["acquisition"]["launch"]
        for key, box in self.selected.items():
            box.setChecked(bool(value.get(key, key == "board1")))
        self.external.setChecked(value.get("externally_triggered", True))
        self.delay.setValue(value.get("delay_seconds", 2))

    def refresh(self):
        services = self.ui.services
        for key, label in self.status.items():
            controller = services.controllers.get(key)
            label.setText(
                controller.status_text() if controller else services.states[key]
            )
            self.starts[key].setEnabled(not services.busy(key))
            self.stops[key].setEnabled(services.busy(key))
        self.external.setEnabled(not services.busy("camera"))
        self.delay.setEnabled(not services.busy())
        timeline = services.run.timeline
        if timeline:
            self.timeline.setText(
                f"RFID start: {timeline.get('start_datetime')}\nRFID end: {timeline.get('end_datetime') or 'Recording…'}\nAnalysis origin: {timeline.get('analysis_start_datetime') or timeline.get('analysis_origin_error', '—')}"
            )
