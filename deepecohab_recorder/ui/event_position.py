"""Select event compartments from the current physical EcoHAB layout."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout
from deepecohab_recorder.event_metadata import normalize_position, positions


class CompartmentButton(QPushButton):
    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.names = names
        self.position = None
        self.set_position(None)
        self.clicked.connect(self.choose)

    def set_position(self, value):
        self.position = normalize_position(value)
        selected = positions(self.position)
        self.setText(", ".join(selected) if selected else "Whole habitat")
        self.setToolTip("Choose cages or tunnels for this event bout.\n" + (", ".join(selected) if selected else "Applies to all compartments; saved as position: null."))

    def choose(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Where did this event happen?")
        dialog.resize(440, 420)
        layout = QVBoxLayout(dialog)
        whole = QCheckBox("Whole habitat (all compartments)")
        whole.setChecked(self.position is None)
        layout.addWidget(whole)
        layout.addWidget(QLabel("Or select one or more cages / tunnels:"))
        listing = QListWidget()
        current, selected = self.names(), positions(self.position)
        for name in dict.fromkeys([*current, *selected]):
            row = QListWidgetItem(name if name in current else name + " — missing from layout")
            row.setData(Qt.UserRole, name)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            listing.addItem(row)
        listing.setEnabled(not whole.isChecked())
        whole.toggled.connect(lambda checked: listing.setEnabled(not checked))
        layout.addWidget(listing, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        def valid_selection():
            buttons.button(QDialogButtonBox.Ok).setEnabled(
                whole.isChecked() or any(listing.item(i).checkState() == Qt.Checked for i in range(listing.count()))
            )
        whole.toggled.connect(valid_selection)
        listing.itemChanged.connect(valid_selection)
        valid_selection()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            selected = [listing.item(i).data(Qt.UserRole) for i in range(listing.count()) if listing.item(i).checkState() == Qt.Checked]
            self.set_position(None if whole.isChecked() else selected)


class DeviceButton(QPushButton):
    changed = Signal()

    def __init__(self, devices, parent=None):
        super().__init__(parent)
        self.devices = devices
        self.selection = None
        self.set_devices(None)
        self.clicked.connect(self.choose)

    def value(self):
        from deepecohab_recorder.device_antennas import remap_device_names
        return remap_device_names(self.selection, self.devices())

    def set_devices(self, value):
        from deepecohab_recorder.event_metadata import normalize_event_devices
        self.selection = normalize_event_devices(value)
        self.setText(', '.join(self.selection or []) or 'None')
        self.setToolTip('Optional devices for this event; repeated event names share this selection. Empty is saved as null.')

    def choose(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Devices associated with this event')
        dialog.resize(480, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Select devices, or leave all unchecked for none:'))
        listing = QListWidget()
        names = list(dict.fromkeys(d['name'] for d in self.devices()))
        selected = self.value() or []
        for name in dict.fromkeys([*names, *selected]):
            row = QListWidgetItem(name if name in names else name + ' — missing from layout')
            row.setData(Qt.UserRole, name)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            listing.addItem(row)
        layout.addWidget(listing, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.set_devices([listing.item(i).data(Qt.UserRole) for i in range(listing.count())
                              if listing.item(i).checkState() == Qt.Checked])
            self.changed.emit()
