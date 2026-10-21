"""One row per bout; repeated event names share a single description."""

import copy
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QSpinBox,
)
from deepecohab_recorder.ui.date_time_fields import TemporalDelegate
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.schema import normalize_events
from deepecohab_recorder.ui.common import config_buttons
from deepecohab_recorder.ui.event_position import CompartmentButton, DeviceButton
from deepecohab_recorder.event_metadata import flat_events, compartment_names, positions


class EventsTab(QWidget):
    paths = ("recording.events", "recording.notes", "acquisition.ui.events_row_count")

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self._extras = []
        self._loading = False
        self._resizing_columns = False
        self._type_extras = {}
        layout = QVBoxLayout(self)
        layout.addLayout(config_buttons(self, "events"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Number of event rows:"))
        self.count = QSpinBox()
        self.count.setRange(0, 10000)
        self.count.setValue(4)
        row.addWidget(self.count)
        row.addStretch(1)
        layout.addLayout(row)
        info = QLabel(
            "Use date and time (YYYY-MM-DDTHH:MM:SS±HH:MM). Click a calendar icon for a calendar and clock; double-click text to type. Local times use the experiment timezone. "
            "Leave end blank to use the RFID recording end; the omission remains flagged. A name and description alone define an unscheduled event type."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.table = QTableWidget(4, 6)
        self.table.setHorizontalHeaderLabels(
            ["Event name", "Description", "Event start time", "Event end time", "Compartment(s)", "Devices (optional)"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col in range(4):
            self.table.setColumnWidth(col, 190 if col < 2 else 280)
        self.table.setColumnWidth(4, 225)
        self.table.setColumnWidth(5, 250)
        self.table.horizontalHeader().sectionResized.connect(self.equal_time_columns)
        for col in (2, 3):
            self.table.setItemDelegateForColumn(
                col,
                TemporalDelegate(
                    self.table, self.ui.experiment_timezone, with_time=True
                ),
            )
        self.table.itemChanged.connect(self.shared_description)
        layout.addWidget(self.table, 3)
        layout.addWidget(QLabel("Notes"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Experiment notes…")
        self.notes.setMinimumHeight(130)
        layout.addWidget(self.notes, 1)
        self.count.valueChanged.connect(self.resize_rows)
        self.resize_rows(4)

    def available_compartments(self):
        if hasattr(self.ui, "layout_compartments"):
            return self.ui.layout_compartments()
        return compartment_names(self.ui.services.repo.data["recording"]["layout"])

    def available_devices(self):
        if hasattr(self.ui, 'layout_state'):
            return self.ui.layout_state().get('recording.devices', [])
        return self.ui.services.repo.data['recording'].get('devices', [])

    def resize_rows(self, count):
        self.table.setRowCount(count)
        while len(self._extras) < count:
            self._extras.append({})
        self._extras = self._extras[:count]
        for row in range(count):
            for col in range(4):
                if self.table.item(row, col) is None:
                    self.table.setItem(row, col, QTableWidgetItem(""))
            if self.table.cellWidget(row, 4) is None:
                self.table.setCellWidget(row, 4, CompartmentButton(self.available_compartments, self.table))
            if self.table.cellWidget(row, 5) is None:
                button = DeviceButton(self.available_devices, self.table)
                button.changed.connect(lambda row=row: self.shared_devices(row))
                self.table.setCellWidget(row, 5, button)

    def shared_devices(self, row):
        if self._loading:
            return
        name = self.table.item(row, 0).text().strip()
        if name:
            value = self.table.cellWidget(row, 5).value()
            for other in range(self.table.rowCount()):
                if self.table.item(other, 0).text().strip() == name:
                    self.table.cellWidget(other, 5).set_devices(value)

    def equal_time_columns(self, column, old, size):
        if column in (2, 3) and not self._resizing_columns:
            self._resizing_columns = True
            self.table.setColumnWidth(5 - column, size)
            self._resizing_columns = False

    def shared_description(self, item):
        if self._loading or item.column() not in (0, 1):
            return
        row = item.row()
        name_item, description_item = self.table.item(row, 0), self.table.item(row, 1)
        if name_item is None or description_item is None:
            return
        name = name_item.text().strip()
        if not name:
            return
        peers = [
            r
            for r in range(self.table.rowCount())
            if r != row
            and self.table.item(r, 0)
            and self.table.item(r, 0).text().strip() == name
        ]
        self._loading = True
        try:
            if item.column() == 0 and peers:
                description_item.setText(self.table.item(peers[0], 1).text())
                self.table.cellWidget(row, 5).set_devices(self.table.cellWidget(peers[0], 5).value())
            description = description_item.text()
            for r in peers:
                self.table.item(r, 1).setText(description)
        finally:
            self._loading = False

    def read(self):
        types, bouts, event_devices = {}, [], {}
        for row in range(self.table.rowCount()):
            name, description, start, end = [
                self.table.item(row, col).text().strip() for col in range(4)
            ]
            devices = self.table.cellWidget(row, 5).value()
            if not any((name, description, start, end, devices)):
                continue
            if not name or (end and not start):
                raise ValueError(
                    f"Event row {row + 1}: enter an event name, and a start time when an end is supplied."
                )
            if name in types and types[name] != description:
                raise ValueError(
                    f"Event {name!r} has conflicting descriptions. Repeated names must share a description."
                )
            types[name] = description
            if name in event_devices and event_devices[name] != devices:
                raise ValueError(f'Event {name!r} has conflicting device selections.')
            event_devices[name] = devices
            unknown_devices = set(devices or []) - {d['name'] for d in self.available_devices()}
            if unknown_devices:
                raise ValueError(f"Event {name!r}: devices are missing from the EcoHAB layout: {', '.join(sorted(unknown_devices))}.")
            if not start:
                continue  # An event type may be prepared before scheduling its first bout.
            bout = copy.deepcopy(self._extras[row])
            # A displayed inferred end stays inferred unless the user edits that value.
            old_end = bout.get("end") or ""
            specified = bool(end) and (
                end != old_end or bout.get("end_user_specified", True)
            )
            if end != old_end:
                bout.pop("finalized_run_id", None)
            bout.update(
                name=name, start=start, end=end or None, end_user_specified=specified,
                position=self.table.cellWidget(row, 4).position,
            )
            unknown = set(positions(bout["position"])) - set(self.available_compartments())
            if unknown:
                raise ValueError(f"Event row {row + 1}: compartments are missing from the EcoHAB layout: {', '.join(sorted(unknown))}.")
            bouts.append(bout)
        timeline = self.ui.services.repo.data["recording"]["timeline"]
        events = normalize_events(
            {
                "event_types": [
                    dict(self._type_extras.get(name, {}), name=name, description=desc, devices=event_devices[name])
                    for name, desc in types.items()
                ],
                "bouts": bouts,
            },
            ExperimentClock(self.ui.experiment_timezone()),
            timeline.get("end_datetime"),
        )
        return {"recording.events": events, "recording.notes": self.notes.toPlainText(), "acquisition.ui.events_row_count": self.count.value()}

    def load(self, data):
        self._loading = True
        events = flat_events(data["recording"]["events"])
        bouts = copy.deepcopy(events["bouts"])
        self._type_extras = {item["name"]: dict(item) for item in events["event_types"]}
        used = {bout["name"] for bout in bouts}
        bouts.extend(
            {"name": item["name"]}
            for item in events["event_types"]
            if item["name"] not in used
        )
        descriptions = {
            item["name"]: item.get("description", "") for item in events["event_types"]
        }
        count = max(len(bouts), int(data.get("acquisition", {}).get("ui", {}).get("events_row_count", len(bouts))))
        self.count.setValue(count)
        self.resize_rows(count)
        self._extras = copy.deepcopy(bouts) + [{} for _ in range(count - len(bouts))]
        for row in range(count):
            bout = bouts[row] if row < len(bouts) else {}
            self.table.cellWidget(row, 4).set_position(bout.get("position"))
            self.table.cellWidget(row, 5).set_devices(self._type_extras.get(bout.get('name'), {}).get('devices'))
            values = (
                bout.get("name", ""),
                descriptions.get(bout.get("name"), ""),
                bout.get("start") or "",
                bout.get("end") or "",
            )
            for col, value in enumerate(values):
                item = self.table.item(row, col)
                item.setText(value)
                if col == 3:
                    item.setToolTip(
                        "Inferred from RFID recording end; user did not specify an end."
                        if bout
                        and not bout.get("end_user_specified", bool(bout.get("end")))
                        else ""
                    )
        self.notes.setPlainText(data["recording"].get("notes", ""))
        self._loading = False
