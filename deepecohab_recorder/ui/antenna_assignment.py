"""Assign a physical channel and logical board, including archived recordings."""
import re
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QCheckBox,
    QLineEdit,
    QWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QSpinBox,
)
from deepecohab_recorder.protocols.rfid import protocol_for_hardware
from deepecohab_recorder.source_identity import split_source, source_id
from deepecohab_recorder.ui.common import MatchDialog, wrapping_form


class AntennaAssignmentDialog(QDialog):
    def __init__(self, ui, current='', parent=None, *, device=None, layout=None, require_slot=False):
        super().__init__(parent)
        self.ui = ui
        self.require_slot = require_slot
        self.original_device = device or {}
        self.physical_layout = layout or {}
        self.setWindowTitle('Assign layout antenna')
        self.resize(610, 650)
        layout = QVBoxLayout(self)
        form = wrapping_form()
        layout.addLayout(form)
        self.mode = QComboBox()
        self.mode.addItem('Configured acquisition board', 'hardware')
        self.mode.addItem('Manual / archived board', 'manual')
        self.board = QComboBox()
        self.boards = [b for b in ui.hardware_settings()['boards'].values() if b.get('active')]
        for board in self.boards:
            self.board.addItem(f"RFID DAQ {board['board_id']} / {board['hardware']}", board)
        self.channel = QComboBox()
        self.board_number = QSpinBox()
        self.board_number.setRange(1, 9999)
        form.addRow('Assignment method:', self.mode)
        form.addRow('DAQ board:', self.board)
        form.addRow('Physical antenna channel:', self.channel)
        form.addRow('Board number:', self.board_number)
        self.preview = QLabel()
        form.addRow('Antenna name:', self.preview)
        note = QLabel('Antenna 1 on RFID DAQ 1 is named 1-1. The hyphen is added automatically. Use the same board number in Data preprocessing. Archived recordings need no connected hardware or COM label.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.role = QComboBox()
        self.role.addItem('Main layout antenna', 'main')
        self.role.addItem('Device antenna', 'device')
        form.addRow('Antenna role:', self.role)
        self.main_slot = QComboBox()
        for tunnel in self.physical_layout.get('tunnels', []):
            for i, a in enumerate(tunnel['antennas']):
                if a == '?': self.main_slot.addItem(f"{tunnel['name']} / position {i+1}", (tunnel['name'], i))
            if len(tunnel['antennas']) < 2:
                i = len(tunnel['antennas'])
                self.main_slot.addItem(f"{tunnel['name']} / position {i+1}", (tunnel['name'], i))
        self.main_slot_label = QLabel('Main tunnel assignment:')
        form.addRow(self.main_slot_label, self.main_slot)
        self.device_panel = QWidget()
        device_form = wrapping_form(self.device_panel)
        self.device_type = QLineEdit(self.original_device.get('device_type', self.original_device.get('name', '')))
        self.device_type.setPlaceholderText('e.g. lickometer')
        self.device_description = QLineEdit(self.original_device.get('description', self.original_device.get('_legacy_description')) or '')
        self.device_description.setPlaceholderText('Optional description')
        self.device_name = QLabel()
        self.device_name.setWordWrap(True)
        self.device_position = QComboBox()
        self.device_position.addItems([r['name'] for key in ('cages', 'tunnels') for r in self.physical_layout.get(key, [])])
        self.device_position.setCurrentIndex(-1)
        self.device_position.setPlaceholderText('Choose a cage or tunnel')
        self.device_position.setCurrentText(self.original_device.get('position', ''))
        self.link_ttl = QCheckBox('Associate a TTL input')
        self.ttl_channel = QComboBox()
        self.ttl_channel.addItems(['TTL1', 'TTL2', 'TTL3', 'TTL4'])
        self.ttl_board = QSpinBox()
        self.ttl_board.setRange(1, 9999)
        self.ttl_board.setValue(split_source(current)[1] or 1)
        if self.original_device.get('TTL_port'):
            tc, tb, _ = split_source(self.original_device['TTL_port'])
            self.ttl_channel.setCurrentText(tc)
            self.ttl_board.setValue(tb or 1)
            self.link_ttl.setChecked(True)
        device_form.addRow('Device type:', self.device_type)
        device_form.addRow('Description (optional):', self.device_description)
        device_form.addRow('Position:', self.device_position)
        device_form.addRow('Generated device name:', self.device_name)
        device_form.addRow(self.link_ttl)
        device_form.addRow('TTL input channel:', self.ttl_channel)
        device_form.addRow('TTL board number:', self.ttl_board)
        info = QLabel('The device name is generated as type-position. Antennas with the same generated name share a numbered device_antenna output, including linked TTL events. Override destinations in Data preprocessing → Manage RFID and TTL output grouping.')
        info.setWordWrap(True)
        device_form.addRow(info)
        self.group_preview = QLabel()
        self.group_preview.setWordWrap(True)
        device_form.addRow('Default output:', self.group_preview)
        self.device_type.textChanged.connect(self.update_group_preview)
        self.device_position.currentTextChanged.connect(self.update_group_preview)
        self.link_ttl.toggled.connect(self.update_group_preview)
        self.ttl_channel.currentTextChanged.connect(self.update_group_preview)
        self.ttl_board.valueChanged.connect(self.update_group_preview)
        layout.addWidget(self.device_panel)
        self.role.setCurrentIndex(1 if device is not None else 0)
        self.role.currentIndexChanged.connect(self.update_role)
        self.link_ttl.toggled.connect(self.update_role)
        self.update_role()
        self.match = QPushButton('Match antenna by presenting a tag')
        self.match.clicked.connect(self.match_antenna)
        layout.addWidget(self.match)
        self.mode.currentIndexChanged.connect(self.update_controls)
        self.board.currentIndexChanged.connect(self.update_controls)
        self.channel.currentTextChanged.connect(self.update_preview)
        self.board_number.valueChanged.connect(self.update_preview)
        self.update_controls()
        channel, number, port = split_source(current)
        if number is None and port:
            candidates = {s.board_id for s in ui.preprocessing_sources() if s.port.upper() == port}
            candidates |= {b['board_id'] for b in self.boards if b.get('com_port', '').upper() == port}
            if len(candidates) == 1:
                number = candidates.pop()
        index = next((i for i, b in enumerate(self.boards) if number == b['board_id'] and channel in protocol_for_hardware(b['hardware']).rfid_channels), None)
        if index is None:
            self.mode.setCurrentIndex(1)
        else:
            self.board.setCurrentIndex(index)
        if number:
            self.board_number.setValue(number)
        if channel not in ('', '?'):
            self.channel.setCurrentText(str(int(channel)) if re.fullmatch(r'0?[1-8]', channel) else channel)
        if not self.boards:
            self.mode.model().item(0).setEnabled(False)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_preview()
        self.update_group_preview()

    def update_group_preview(self, *_):
        import copy
        from deepecohab_recorder.device_antennas import resolve_routes
        from deepecohab_recorder.preprocessing.output_plan import data_stem
        config = copy.deepcopy(self.ui.services.repo.data)
        if hasattr(self.ui, "layout_state"):
            config["recording"]["devices"] = self.ui.layout_state().get("recording.devices", [])
        from deepecohab_recorder.device_antennas import device_name
        name = device_name(self.device_type.text(), self.device_position.currentText())
        self.device_name.setText(name or 'Choose a device type and position')
        if not name:
            self.group_preview.setText("Enter a device type and position to see its output group.")
            return
        devices = config["recording"].setdefault("devices", [])
        devices[:] = [d for d in devices if d["antenna"] != self.original_device.get("antenna")]
        try:
            source = self.value()
        except ValueError:
            return
        devices.append(dict(name=name, device_type=self.device_type.text().strip(),
                            position=self.device_position.currentText(), antenna=source, TTL_port=None))
        options = config["acquisition"].setdefault("preprocessing", {}).setdefault("options", {})
        from deepecohab_recorder.device_antennas import layout_defaults
        defaults, _, _, _ = layout_defaults(config, options, strict=False)
        group = defaults[source]
        members = sorted({d["antenna"] for d in devices if d["name"].strip().casefold() == name.casefold()})
        self.group_preview.setText(data_stem(config["recording"]["name"], group) + ".parquet / .csv\nRFID inputs: " + ", ".join(members) + ("; linked TTL included" if self.link_ttl.isChecked() else ""))

    def update_role(self, *_):
        device = self.role.currentData() == 'device'
        self.device_panel.setVisible(device)
        self.main_slot.setVisible(not device and self.require_slot)
        self.main_slot_label.setVisible(not device and self.require_slot)
        self.ttl_channel.setEnabled(self.link_ttl.isChecked())
        self.ttl_board.setEnabled(self.link_ttl.isChecked())

    def device_value(self):
        if self.role.currentData() != 'device':
            if self.require_slot and self.main_slot.currentData() is None:
                raise ValueError('Create an unassigned main tunnel antenna position before converting this device to Main.')
            return None
        kind, position = self.device_type.text().strip(), self.device_position.currentText()
        if not kind or not position:
            raise ValueError('Devices require a type and a cage/tunnel position.')
        from deepecohab_recorder.device_antennas import normalize_devices
        return normalize_devices([dict(self.original_device, device_type=kind,
                    description=self.device_description.text().strip() or None,
                    antenna=self.value(), position=position,
                    TTL_port=source_id(self.ttl_channel.currentText(), self.ttl_board.value()) if self.link_ttl.isChecked() else None)])[0]

    def update_controls(self):
        manual = self.mode.currentData() == 'manual'
        previous = self.channel.currentText()
        self.board.setEnabled(not manual and bool(self.boards))
        self.board_number.setEnabled(manual)
        self.channel.setEditable(manual)
        self.channel.clear()
        if manual:
            self.channel.addItems(list('1234567890ABCDEFGHIJKLMNOP'))
            if previous:
                self.channel.setCurrentText(previous)
        elif self.board.currentData():
            board = self.board.currentData()
            self.channel.addItems(list(protocol_for_hardware(board['hardware']).rfid_channels))
            self.board_number.setValue(board['board_id'])
        self.match.setEnabled(not manual and bool(self.boards) and bool((self.board.currentData() or {}).get('com_port')))
        self.update_preview()

    def update_preview(self, *_):
        self.preview.setText(source_id(self.channel.currentText() or '?', self.board_number.value()))

    def value(self):
        channel = self.channel.currentText().strip().upper()
        if re.fullmatch(r'0[1-8]', channel):
            channel = str(int(channel))
        if not re.fullmatch(r'[0-9A-P]', channel):
            raise ValueError('Enter one physical RFID antenna channel (0–9 or A–P). TTL channels are not layout antennas.')
        return source_id(channel, self.board_number.value())

    def validate_and_accept(self):
        try:
            self.value()
            self.device_value()
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, 'Antenna assignment', str(exc))

    def match_antenna(self):
        if not self.ui.prepare_matching():
            return
        matcher = MatchDialog(self.ui, self)
        if matcher.exec() and matcher.selected:
            _, source, board_id = matcher.selected
            for i, board in enumerate(self.boards):
                if board['board_id'] == board_id:
                    self.board.setCurrentIndex(i)
                    self.channel.setCurrentText(split_source(source)[0])
                    break
