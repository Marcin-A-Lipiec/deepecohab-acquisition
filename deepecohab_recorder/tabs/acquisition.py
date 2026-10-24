"""One to four DAQ panels, with settings independent of their presentation."""
import copy
from dataclasses import asdict
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGridLayout,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.utils import list_serial_ports
from deepecohab_recorder.live_boards import RFID_BOARD_IDS, RFID_DEVICE_KEYS, visible_board_count
from deepecohab_recorder.models import BoardConfig
from deepecohab_recorder.ui.wheel_scroll import QSpinBox
from deepecohab_recorder.widgets.board_panel import BoardPanel


class AcquisitionTab(QWidget):
    paths = ("acquisition.boards", "acquisition.ui.rfid_board_count")
    hardware = True

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self._loaded_boards = {}
        self._count = 1
        root = QVBoxLayout(self)
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("Number of RFID DAQ boards:"))
        self.board_count = QSpinBox()
        self.board_count.setRange(1, len(RFID_BOARD_IDS))
        self.board_count.setValue(1)
        self.board_count.setAccessibleName("Number of RFID DAQ boards")
        self.board_count.setToolTip("Show 1–4 DAQ boards. Adding a panel does not start recording. Stop RFID acquisition and finish matching before changing this number.")
        count_row.addWidget(self.board_count)
        count_row.addStretch(1)
        root.addLayout(count_row)
        controls = QHBoxLayout()
        refresh = QPushButton("Refresh COM ports")
        refresh.clicked.connect(self.refresh_ports)
        controls.addWidget(refresh)
        note = QLabel("Experiment identity and save directory are on Setup. Raw RFID files are saved in raw_antenna_reads; recording history is saved in the experiment GUI-state file.")
        note.setWordWrap(True)
        controls.addWidget(note, 1)
        root.addLayout(controls)
        self.grid = QGridLayout()
        self.panels, self.starts, self.stops = {}, {}, {}
        for i in RFID_BOARD_IDS:
            panel = BoardPanel(i, i == 1)
            self.panels[i] = panel
            controls = QHBoxLayout()
            start, stop = QPushButton("Start RFID DAQ"), QPushButton("Stop RFID DAQ")
            start.setProperty("role", "start")
            stop.setProperty("role", "stop")
            start.clicked.connect(
                lambda checked=False, i=i: self.ui.start_devices([f"board{i}"])
            )
            stop.clicked.connect(
                lambda checked=False, i=i: self.ui.stop_devices([f"board{i}"])
            )
            controls.addWidget(start)
            controls.addWidget(stop)
            self.starts[i], self.stops[i] = start, stop
            panel.layout().insertLayout(0, controls)
        root.addLayout(self.grid, 1)
        self.board_count.valueChanged.connect(self._change_count)
        self._arrange()

    def _locked(self):
        return (any(self.ui.services.busy(key) for key in RFID_DEVICE_KEYS) or
                "tag matching" in self.ui.services.ports.snapshot().values())

    def _change_count(self, count):
        if self._locked():
            with QSignalBlocker(self.board_count):
                self.board_count.setValue(self._count)
            return
        previous, self._count = self._count, count
        for i, panel in self.panels.items():
            if previous < i <= count:
                panel.active_check.setChecked(True)
            elif i > count:
                panel.active_check.setChecked(False)
        self._arrange()
        self.ui.rfid_boards_changed(count)
        self.refresh()

    def _arrange(self):
        while self.grid.count():
            self.grid.takeAt(0)
        for row in range(2):
            self.grid.setRowStretch(row, int(row == 0 or self._count >= 3))
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, int(self._count > 1))
        for i, panel in self.panels.items():
            visible = i <= self._count
            panel.setVisible(visible)
            if visible:
                row, col = divmod(i - 1, 2) if self._count > 1 else (0, 0)
                self.grid.addWidget(panel, row, col)
        self.viewport_size(self.width(), self.height())

    def refresh_ports(self):
        ports = list_serial_ports()
        for panel in self.panels.values():
            panel.set_ports(ports)

    def read(self):
        boards = copy.deepcopy(self._loaded_boards)
        for i, panel in self.panels.items():
            key = f"board_{i}"
            boards[key] = dict(boards.get(key, {}), **asdict(panel.config()))
            if i > self._count:
                boards[key]["active"] = False
        return {"acquisition.boards": boards,
                "acquisition.ui.rfid_board_count": self._count}

    def load(self, data):
        acquisition = data["acquisition"]
        self._loaded_boards = copy.deepcopy(acquisition["boards"])
        self._count = visible_board_count(acquisition)
        with QSignalBlocker(self.board_count):
            self.board_count.setValue(self._count)
        for i, panel in self.panels.items():
            panel.apply_config(self._loaded_boards.get(f"board_{i}", asdict(BoardConfig(i, i == 1))))
            if i > self._count:
                panel.active_check.setChecked(False)
        self._arrange()
        self.ui.rfid_boards_changed(self._count)

    def set_known_tags(self, tags):
        for panel in self.panels.values():
            panel.set_known_tags(tags)

    def refresh(self):
        owned = self.ui.services.ports.snapshot()
        self.board_count.setEnabled(not self._locked())
        for i, panel in self.panels.items():
            busy = self.ui.services.busy(f"board{i}")
            matching = owned.get(panel.com_combo.currentText().upper()) == "tag matching"
            controller = self.ui.services.controllers.get(f"board{i}")
            if controller and i <= self._count:
                panel.update_from_controller(controller)
            panel.set_running_state(busy or matching)
            self.starts[i].setEnabled(i <= self._count and panel.active_check.isChecked() and not busy and not matching)
            self.stops[i].setEnabled(busy)

    def viewport_size(self, width, height):
        # The page scrolls when its readable minimum cannot fit this viewport.
        columns, rows = (1 if self._count == 1 else 2), (2 if self._count >= 3 else 1)
        self.setMinimumWidth(columns * 390 + 28)
        panel_height = max(panel.minimumSizeHint().height() for i, panel in self.panels.items() if i <= self._count)
        self.setMinimumHeight(rows * panel_height + 100)
