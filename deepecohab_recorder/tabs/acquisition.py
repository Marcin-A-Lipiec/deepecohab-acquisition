from dataclasses import asdict
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.utils import list_serial_ports
from deepecohab_recorder.widgets.board_panel import BoardPanel


class AcquisitionTab(QWidget):
    paths = ("acquisition.boards",)
    hardware = True

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        refresh = QPushButton("Refresh COM ports")
        refresh.clicked.connect(self.refresh_ports)
        controls.addWidget(refresh)
        note = QLabel("Experiment identity and save directory are on Setup. Raw RFID files are saved in raw_antenna_reads; recording history is saved in the experiment GUI-state file.")
        note.setWordWrap(True)
        controls.addWidget(note, 1)
        root.addLayout(controls)
        self.splitter = QSplitter(Qt.Horizontal)
        self.panels = {}
        for i in (1, 2):
            panel = BoardPanel(i, i == 1)
            self.panels[i] = panel
            controls = QHBoxLayout()
            start, stop = QPushButton("Start RFID"), QPushButton("Stop RFID")
            stop.setProperty("role", "stop")
            start.clicked.connect(
                lambda checked=False, i=i: self.ui.start_devices([f"board{i}"])
            )
            stop.clicked.connect(
                lambda checked=False, i=i: self.ui.stop_devices([f"board{i}"])
            )
            controls.addWidget(start)
            controls.addWidget(stop)
            panel.layout().insertLayout(0, controls)
            self.splitter.addWidget(panel)
        root.addWidget(self.splitter, 1)
    def refresh_ports(self):
        for panel in self.panels.values():
            panel.set_ports(list_serial_ports())

    def read(self):
        return {
            "acquisition.boards": {
                f"board_{i}": asdict(panel.config()) for i, panel in self.panels.items()
            },
        }

    def load(self, data):
        for i, panel in self.panels.items():
            panel.apply_config(data["acquisition"]["boards"][f"board_{i}"])

    def refresh(self):
        for i, panel in self.panels.items():
            controller = self.ui.services.controllers.get(f"board{i}")
            if controller:
                panel.update_from_controller(controller)
            panel.set_running_state(
                self.ui.services.busy(f"board{i}")
                or bool(
                    self.ui.services.ports.snapshot().get(
                        panel.com_combo.currentText().upper()
                    )
                    == "tag matching"
                )
            )

    def viewport_size(self, width, height):
        self.splitter.setOrientation(Qt.Vertical if width < 1020 else Qt.Horizontal)
