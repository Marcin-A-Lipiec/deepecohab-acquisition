"""Processing controls, per-output stitching windows and board-clock guidance."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
)
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.preprocessing.models import normalize_interpolation_mode
from deepecohab_recorder.preprocessing.raw_clock import fixed_utc_offset
from .preprocessing_outputs import action as button


def item(value):
    return QTableWidgetItem(str(value))


class AdvancedOptions(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Data preprocessing — Advanced options")
        self.resize(920, 660)
        self.setMinimumSize(600, 420)
        layout = QVBoxLayout(self)
        tabs = self.tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        settings = QWidget()
        form = QFormLayout(settings)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.window = QSpinBox()
        self.window.setRange(1, 60000)
        self.window.setValue(parent.options["window_ms"])
        self.window.setSuffix(" ms")
        form.addRow("Main group stitch window:", self.window)
        self.minimum = QSpinBox()
        self.minimum.setRange(1, 10000)
        self.minimum.setValue(parent.options["minimum_ms"])
        self.minimum.setSuffix(" ms")
        form.addRow("Singleton / zero-duration minimum:", self.minimum)
        self.filter = QCheckBox("Keep only animal tags listed in the experiment configuration")
        self.filter.setChecked(parent.options["whitelist"])
        form.addRow(self.filter)
        self.interpolation = QComboBox()
        for label, mode in (
            ("Standard interpolation (default)", "standard"),
            ("No interpolation", "none"),
            ("Extended interpolation (includes longer missing routes)", "extended"),
        ):
            self.interpolation.addItem(label, mode)
        mode = normalize_interpolation_mode(parent.options.get("interpolation_mode", "standard")) if parent.options["interpolate"] else "none"
        self.interpolation.setCurrentIndex(self.interpolation.findData(mode))
        form.addRow("Missing antenna passes:", self.interpolation)
        self.interpolation_warning = QLabel()
        self.interpolation_warning.setWordWrap(True)
        form.addRow(self.interpolation_warning)
        self.interpolation.currentIndexChanged.connect(self.update_interpolation_note)
        self.update_interpolation_note()
        self.transits = QSpinBox()
        self.transits.setRange(1, 10000)
        self.transits.setValue(parent.options["minimum_transits"])
        form.addRow("Minimum observed traversals for a tunnel median:", self.transits)
        self.cohort = QCheckBox(
            "Allow cohort median if the mouse has insufficient traversals"
        )
        self.cohort.setChecked(parent.options["cohort_fallback"])
        form.addRow(self.cohort)
        self.split = QCheckBox("Merge recording parts into one output file per group")
        self.split.setChecked(not parent.options["split_restarts"])
        form.addRow(self.split)
        self.flag_gaps = QCheckBox("Flag periods between recordings as missed data")
        self.flag_gaps.setChecked(parent.options.get("flag_missed_data_periods", True))
        self.flag_gaps.setToolTip("List each board's intervals between the last valid record in one file and the first in the next. This does not add rows or change processing.")
        form.addRow(self.flag_gaps)
        self.ignore_short_gaps = QCheckBox("Ignore short gaps between recording files")
        self.ignore_short_gaps.setChecked(parent.options.get("ignore_short_recording_gaps", True))
        self.gap_minutes = QDoubleSpinBox()
        self.gap_minutes.setDecimals(3)
        self.gap_minutes.setRange(0, 5256000)
        self.gap_minutes.setSuffix(" min")
        self.gap_minutes.setValue(parent.options.get("minimum_recording_gap_seconds", 180.0) / 60)
        self.gap_minutes.setEnabled(self.ignore_short_gaps.isChecked())
        self.ignore_short_gaps.toggled.connect(self.gap_minutes.setEnabled)
        self.ignore_short_gaps.setToolTip("Uncheck to report every positive gap, however short. Clock resets and confirmed DAQ interruptions are always retained.")
        form.addRow(self.ignore_short_gaps)
        form.addRow("Report gaps at least this long:", self.gap_minutes)
        gap_note = QLabel("By default, gaps shorter than 3 minutes are omitted from recording_boundaries and observed missed-period lists. The exact threshold is included. This affects reporting only. Gap reporting uses the last and first valid PC datetimes in separate recording sources from the same logical board, including TTL records. Hourly files inside one board folder are declared continuous: their routine boundaries and individual file lists are omitted. Confirmed stops and clock resets remain reported. Put known restarts in separate sources/parts. Overlapping file ranges do not create a gap. It remains available when output parts are merged. These are observed record bounds, not proof of the exact power-failure or restart times.")
        gap_note.setWordWrap(True)
        form.addRow(gap_note)
        self.master = QSpinBox()
        self.master.setRange(1, 999)
        self.master.setValue(parent.options["master_board"])
        self.zone = QLineEdit(parent.options["timezone"])
        self.zone.setPlaceholderText("Europe/Warsaw")
        form.addRow("Experiment timezone (IANA name):", self.zone)
        note = QLabel(
            "Clock alignment between independent boards uses PC timestamps and is marked as approximate. The last data column, inserted, is True for interpolated rows and False for observed rows. Detailed interpolation and duration assumptions are recorded in diagnostics."
        )
        note.setWordWrap(True)
        form.addRow(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(settings)
        tabs.addTab(scroll, "Processing")

        stitching_page = QWidget()
        stitching_layout = QVBoxLayout(stitching_page)
        guide = QLabel(
            "Main uses the window on the Processing tab. Each separate destination below starts at 300 ms and can use its own value. "
            "For example, assign 4-1 and 7-1 to device_antenna1, then set that group's window to 1000 ms.\n\n"
            "Assign numbered destinations with Manage RFID and TTL output grouping in the main tab. "
            "Only consecutive reads of the same animal on the same antenna can be stitched; different antennas remain independent. "
            "A gap equal to the window starts a new period. TTL events are never stitched."
        )
        guide.setWordWrap(True)
        stitching_layout.addWidget(guide)
        self.group_windows = {}
        from deepecohab_recorder.device_antennas import resolve_routes
        import copy
        self.normalized_options = copy.deepcopy(parent.options)
        effective_routes, _, _, _ = resolve_routes(parent.grouping_config(), self.normalized_options, strict=False)
        groups = sorted(set(effective_routes.values()) - {"main", "rfid", "ttl"})
        self.stitching_table = QTableWidget(len(groups), 3)
        self.stitching_table.setHorizontalHeaderLabels(["Destination group", "Assigned antennas", "Stitch window"])
        self.stitching_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.stitching_table.setColumnWidth(0, 200)
        self.stitching_table.setColumnWidth(2, 150)
        for row, group in enumerate(groups):
            antennas = sorted(a for a, destination in effective_routes.items() if destination == group)
            for column, text in ((0, group), (1, ", ".join(antennas))):
                cell = item(text)
                cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                cell.setToolTip(text)
                self.stitching_table.setItem(row, column, cell)
            control = QSpinBox()
            control.setRange(1, 60000)
            control.setSuffix(" ms")
            control.setValue(self.normalized_options.get("group_windows_ms", {}).get(group, 300))
            self.group_windows[group] = control
            self.stitching_table.setCellWidget(row, 2, control)
        stitching_layout.addWidget(self.stitching_table, 1)
        if not groups:
            stitching_layout.addWidget(QLabel("No separate destinations yet. Assign antennas to a named group first."))
        tabs.addTab(stitching_page, "Stitching windows")

        antenna_page = QWidget()
        antenna_layout = QVBoxLayout(antenna_page)
        hint = QLabel(
            "Use complete source names, such as A-1. Aliases change antenna labels inside files and must retain a board suffix, for example lick_port-1. Use Manage RFID and TTL output grouping in the main preprocessing tab to choose separate files."
        )
        hint.setWordWrap(True)
        antenna_layout.addWidget(hint)
        names = (
            set(parent.options["aliases"])
            | parent.antenna_names()
        )
        self.antennas = QTableWidget(len(names), 2)
        self.antennas.setHorizontalHeaderLabels(
            ["Source antenna", "Output alias (optional)"]
        )
        self.antennas.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row, name in enumerate(sorted(names)):
            for col, value in enumerate(
                (
                    name,
                    parent.options["aliases"].get(name, ""),
                )
            ):
                self.antennas.setItem(row, col, item(value))
        antenna_layout.addWidget(self.antennas, 1)
        antenna_layout.addWidget(
            button(
                "Add antenna setting",
                self.add_antenna,
                "Add a source antenna not shown in the initial file preview.",
            )
        )
        tabs.addTab(antenna_page, "Antenna aliases")

        clock_page = QWidget()
        clock_layout = QVBoxLayout(clock_page)
        note = QLabel(
            "<b>What this does</b><br>Each RFID board has its own counter. Preprocessing converts it to a timezone-aware internal_board_timestamp "
            "and uses PC datetimes to place different boards on a common timeline. The reference board's first read defines the zero point. "
            "This is approximate alignment between independent clocks, not hardware synchronization.<br><br>"
            "<b>Usual setup — one or two boards</b><br>"
            "1. In the main input table, give each physical board its own Board number. Select the correct hardware format.<br>"
            "2. Keep the same Board number and Clock group 1 for that board's files after a recording restart. "
            "Board 1 and Board 2 can both use Clock group 1: groups are local to each board.<br>"
            "3. Use reference Board 1 (or another selected board). Normally leave the overrides below blank and counter bits at 0.<br>"
            "4. Recording part controls separate output files; it does not identify the board's clock. Files recorded simultaneously on two boards should share a part number.<br><br>"
            "<b>Vanilla legacy boards / hourly folders</b><br>"
            "These recordings have no internal counter. Assign each board folder its own Board number; "
            "use the same labels in EcoHAB layout. PC datetimes align these sources; internal_board_timestamp is null. "
            "Counter overrides are disabled. All hourly files in one folder share its assignments and recording part. "
            "Simultaneous board folders should share the same part number.<br><br>"
            "<b>Only change overrides when the firmware counter format is known</b><br>"
            "Milliseconds per tick means the time represented by one counter increment, not the clock's MHz. "
            "Blank uses the profile: v1.0/v1.5 = 10 ms; old counters are estimated from PC datetimes. "
            "Counter bits describes unsigned rollover (for example 32); 0 means unknown. "
            "Small backwards counter steps are tolerated as reordered serial reads; larger discontinuities are checked as possible resets. "
            "The 2000 ms tolerance below is unrelated to the RFID stitching window or gaps between recording files. "
            "Inspect clocks in the experiment diagnostic JSON for the applied scale, alignment and reset findings."
        )
        note.setWordWrap(True)
        help_scroll = QScrollArea()
        help_scroll.setWidgetResizable(True)
        clock_help = QWidget()
        clock_help_layout = QVBoxLayout(clock_help)
        clock_help_layout.addWidget(note)
        help_scroll.setWidget(clock_help)
        clock_layout.addWidget(help_scroll, 2)
        clock_form = QFormLayout()
        self.master.setToolTip("Use a Board number present in the main input table. Its earliest read anchors the experiment's internal clock.")
        clock_form.addRow("Reference board (Board number):", self.master)
        self.reorder = QSpinBox()
        self.reorder.setRange(0, 60000)
        self.reorder.setSuffix(" ms")
        self.reorder.setValue(parent.options.get("counter_reorder_ms", 2000))
        clock_form.addRow("Out-of-order counter tolerance:", self.reorder)
        clock_layout.addLayout(clock_form)
        specs = parent.source_specs()
        self.raw_clock_enabled = QCheckBox("Exceptional fixed PC-clock offsets (verified historical recordings only)")
        self.raw_clock_enabled.setToolTip("Off by default. Enable only to resolve a known fixed recording-PC clock, for example after an ambiguous-time error.")
        clock_help_layout.addWidget(self.raw_clock_enabled)
        self.raw_clock_details = QWidget()
        exceptional_layout = QVBoxLayout(self.raw_clock_details)
        exceptional_layout.setContentsMargins(0, 0, 0, 0)
        explanation = QLabel(
            "<b>Exceptional option: establish the recording PC's actual clock setting first.</b><br>"
            "Use this only if the PC kept the same known UTC offset throughout the selected file or board folder, "
            "for example because automatic daylight-saving adjustment was disabled. The error alone cannot establish this.<br><br>"
            "Enter a verified signed offset such as +02:00 or +01:00 for each affected source. Blank keeps normal "
            "experiment-timezone rules and strict ambiguity checks; enabling this option alone chooses no offset.<br><br>"
            "The offset interprets <b>every offset-free raw timestamp in that source</b>, including dates outside the "
            "repeated hour. A wrong choice can shift the entire source by an hour and misalign animals, boards, "
            "events and light phases. Do not use one fixed offset for a source whose PC actually changed clocks; "
            "separate it into verified offset periods first.<br><br>"
            "Recorded timestamps that already carry offsets stay authoritative. Original raw files and the experiment "
            "timezone are preserved. Output datetimes remain timezone-aware in the experiment timezone, so their "
            "displayed local hour can differ from the unadjusted PC clock. Fixed light/dark scheduling and file schemas "
            "are unchanged. The choice is retained in private GUI settings and the existing diagnostic issues section."
        )
        explanation.setWordWrap(True)
        exceptional_layout.addWidget(explanation)
        self.raw_clock_offsets = QTableWidget(len(specs), 2)
        self.raw_clock_offsets.setHorizontalHeaderLabels(["Recording source", "Verified fixed UTC offset (blank = normal)"])
        self.raw_clock_offsets.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.raw_clock_offsets.setMinimumHeight(150)
        for row, spec in enumerate(specs):
            self.raw_clock_offsets.setItem(row, 0, item(f"Board {spec.board_id} — {parent._sources[row].label}"))
            self.raw_clock_offsets.item(row, 0).setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.raw_clock_offsets.item(row, 0).setToolTip(parent._sources[row].path)
            self.raw_clock_offsets.setItem(row, 1, item(spec.naive_utc_offset))
        exceptional_layout.addWidget(self.raw_clock_offsets)
        clock_help_layout.addWidget(self.raw_clock_details)
        self.raw_clock_enabled.toggled.connect(self.raw_clock_details.setVisible)
        self.raw_clock_enabled.setChecked(any(spec.naive_utc_offset for spec in specs))
        self.raw_clock_details.setVisible(self.raw_clock_enabled.isChecked())
        self.clocks = QTableWidget(len(specs), 3)
        self.clocks.setHorizontalHeaderLabels(
            ["Source / board assignment", "Override ms per tick (blank = auto)", "Counter bits (0 = unknown)"]
        )
        self.clocks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row, spec in enumerate(specs):
            self.clocks.setItem(row, 0, item(f"Board {spec.board_id} / {spec.port} — {parent._sources[row].label}"))
            self.clocks.item(row, 0).setFlags(Qt.ItemIsEnabled)
            self.clocks.item(row, 0).setToolTip(f"{parent._sources[row].path}\nBoard {spec.board_id} / {spec.port} / {spec.profile}\nClock group {spec.group}; recording part {spec.part}")
            self.clocks.setItem(
                row, 1, item("" if spec.tick_ms is None else spec.tick_ms)
            )
            self.clocks.setItem(row, 2, item(spec.counter_bits))
            if spec.profile == "legacy_raw":
                for column, value in ((1, ""), (2, "0")):
                    cell = self.clocks.item(row, column)
                    cell.setText(value)
                    cell.setFlags(Qt.ItemIsEnabled)
                    cell.setToolTip("No board counter was recorded. PC datetimes are used; internal_board_timestamp remains null.")
        clock_layout.addWidget(self.clocks, 1)
        tabs.addTab(clock_page, "Board clocks")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_interpolation_note(self):
        mode = self.interpolation.currentData()
        self.interpolation_warning.setText({
            "none": "Keep observed detections only. All RFID antennas are still stitched; TTLs remain individual events.",
            "standard": "Repair one missing antenna when it is the unique neighbour connecting consecutive non-adjacent detections of the same mouse. Longer detours do not block this repair. There is no maximum time gap; transit-median and recording-coverage checks still apply. Separate output groups are excluded.",
            "extended": "WARNING: Extended inference is likely to introduce some incorrect antenna detections. It includes Standard repairs and additionally selects a unique route requiring the fewest missed movement antennas. Equal alternatives remain unresolved. No maximum time gap; timing checks still apply. Standard and additional Extended inserts are counted separately in the experiment diagnostic JSON.",
        }[mode])
        self.interpolation_warning.setStyleSheet("font-weight: bold;" if mode == "extended" else "")

    def add_antenna(self):
        row = self.antennas.rowCount()
        self.antennas.insertRow(row)
        for col in range(2):
            self.antennas.setItem(row, col, item(""))

    def values(self):
        self.recording_offsets()  # Validate before applying any options.
        zone = self.zone.text().strip()
        ExperimentClock(zone)
        aliases, seen = {}, set()
        for row in range(self.antennas.rowCount()):
            source, alias = [
                self.antennas.item(row, col).text().strip() for col in range(2)
            ]
            source = source.upper()
            if alias and not source:
                raise ValueError("An alias requires a source antenna.")
            if source and source in seen:
                raise ValueError(f"Duplicate antenna setting: {source}")
            seen.add(source)
            if alias:
                aliases[source] = alias
        clocks = []
        for row in range(self.clocks.rowCount()):
            scale = self.clocks.item(row, 1).text().strip()
            clocks.append(
                (float(scale) if scale else None, int(self.clocks.item(row, 2).text()))
            )
        return {
            "window_ms": self.window.value(),
            "minimum_ms": self.minimum.value(),
            "whitelist": self.filter.isChecked(),
            "interpolate": self.interpolation.currentData() != "none",
            "interpolation_mode": self.interpolation.currentData(),
            "minimum_transits": self.transits.value(),
            "cohort_fallback": self.cohort.isChecked(),
            "split_restarts": not self.split.isChecked(),
            "master_board": self.master.value(),
            "timezone": zone,
            "aliases": aliases,
            "routes": dict(self.normalized_options["routes"]),
            "ttl_routes": dict(self.normalized_options.get("ttl_routes", {})),
            "device_groups": dict(self.normalized_options.get("device_groups", {})),
            "counter_reorder_ms": self.reorder.value(),
            "flag_missed_data_periods": self.flag_gaps.isChecked(),
            "ignore_short_recording_gaps": self.ignore_short_gaps.isChecked(),
            "minimum_recording_gap_seconds": self.gap_minutes.value() * 60,
            "group_windows_ms": {
                **self.normalized_options.get("group_windows_ms", {}),
                **{group: control.value() for group, control in self.group_windows.items()},
            },
        }, clocks

    def recording_offsets(self):
        values = []
        for row in range(self.raw_clock_offsets.rowCount()):
            offset = self.raw_clock_offsets.item(row, 1).text().strip() if self.raw_clock_enabled.isChecked() else ""
            fixed_utc_offset(offset)
            values.append(offset)
        return values
