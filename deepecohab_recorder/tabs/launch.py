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
from deepecohab_recorder.live_boards import RFID_BOARD_IDS


class LaunchTab(QWidget):
    paths = ("acquisition.launch",)
    hardware = True

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self._board_count = 1
        self._device_rows = {}
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
                *((f"board{i}", f"RFID DAQ {i}") for i in RFID_BOARD_IDS),
                ("camera", "Camera"),
                ("trigger", "Hardware trigger"),
            ]
        ):
            select = QCheckBox(title)
            select.setChecked(key == "board1")
            self.selected[key] = select
            start, stop = QPushButton("Start"), QPushButton("Stop")
            start.setProperty("role", "start")
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
            self._device_rows[key] = (select, start, stop, status)
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
        self.set_board_count(1)

    def set_board_count(self, count):
        previous, self._board_count = self._board_count, count
        for i in RFID_BOARD_IDS:
            key = f"board{i}"
            for widget in self._device_rows[key]:
                widget.setVisible(i <= count)
            if i > count:
                self.selected[key].setChecked(False)
            elif i > previous:
                self.selected[key].setChecked(True)

    def read(self):
        return {
            "acquisition.launch": {
                **{key: box.isChecked() and (not key.startswith("board") or int(key[5:]) <= self._board_count)
                   for key, box in self.selected.items()},
                "externally_triggered": self.external.isChecked(),
                "delay_seconds": self.delay.value(),
            }
        }

    def load(self, data):
        from deepecohab_recorder.live_boards import visible_board_count
        self.set_board_count(visible_board_count(data["acquisition"]))
        value = data["acquisition"]["launch"]
        for key, box in self.selected.items():
            box.setChecked(bool(value.get(key, key == "board1")))
        self.external.setChecked(value.get("externally_triggered", True))
        self.delay.setValue(value.get("delay_seconds", 2))
        # Saved selections for hidden boards cannot start hidden hardware.
        self.set_board_count(self._board_count)

    def refresh(self):
        services = self.ui.services
        for key, label in self.status.items():
            controller = services.controllers.get(key)
            label.setText(
                controller.status_text() if controller else services.states[key]
            )
            available = not key.startswith("board") or int(key[5:]) <= self._board_count
            self.starts[key].setEnabled(available and not services.busy(key))
            self.stops[key].setEnabled(services.busy(key))
        self.external.setEnabled(not services.busy("camera"))
        self.delay.setEnabled(not services.busy())
        timeline = services.run.timeline
        if timeline:
            self.timeline.setText(
                f"RFID start: {timeline.get('start_datetime')}\nRFID end: {timeline.get('end_datetime') or 'Recording…'}\nAnalysis origin: {timeline.get('analysis_start_datetime') or timeline.get('analysis_origin_error', '—')}"
            )
