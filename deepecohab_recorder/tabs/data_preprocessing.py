"""A small UI over an isolated, cancellable preprocessing process."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QFileSystemWatcher, QProcess, Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
)

from deepecohab_recorder.schema import default_config
from deepecohab_recorder.worker_process import start_preprocessing
from deepecohab_recorder.config_bundle import load_config_bundle
from deepecohab_recorder.source_identity import source_id
from deepecohab_recorder.metadata_updates import config_digest, write_config_update
from deepecohab_recorder.preprocessing.layout_check import check_layout, apply_layout_changes
from deepecohab_recorder.preprocessing.models import PROFILES, PROFILE_LABELS, Settings, iso
from deepecohab_recorder.preprocessing.sources import SourceGroup, serialize_source, source_key
from deepecohab_recorder.services.discovery import SourceDiscovery
from deepecohab_recorder.preprocessing.inventory import input_signature
from deepecohab_recorder.preprocessing.metadata_review import review_metadata
from deepecohab_recorder.preprocessing.output_plan import experiment_output_name
from deepecohab_recorder.ui.preprocessing_outputs import OutputGroupsDialog
from deepecohab_recorder.ui.preprocessing_advanced import AdvancedOptions


def button(text, callback, hint):
    widget = QPushButton(text)
    widget.setToolTip(hint)
    widget.clicked.connect(callback)
    return widget


def item(value):
    return QTableWidgetItem(str(value))


class DataPreprocessingTab(QWidget):
    paths = ("acquisition.preprocessing",)

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.config_snapshot = default_config()
        self.options = {
            "window_ms": 300,
            "minimum_ms": 10,
            "whitelist": True,
            "interpolate": True,
            "interpolation_mode": "standard",
            "split_restarts": False,
            "minimum_transits": 3,
            "cohort_fallback": False,
            "master_board": 1,
            "timezone": "Europe/Warsaw",
            "aliases": {},
            "routes": {},
            "ttl_routes": {},
            "device_groups": {},
            "counter_reorder_ms": 2000,
            "group_windows_ms": {},
            "flag_missed_data_periods": True,
            "ignore_short_recording_gaps": True,
            "minimum_recording_gap_seconds": 180.0,
        }
        self._default_options = copy.deepcopy(self.options)
        self._auto_context = None
        self._config_discovery_error = ""
        self._config_signature = None
        self._scan_signature = None
        self._last_scan_check = 0
        self._manual_config = self._manual_output = False
        self.preview_antennas = set()
        self._preview_by_path = {}
        self._used_antennas = set()
        self._used_ttl = set()
        self._inventory_signature = None
        self._inventory_pending = False
        self._specs = []  # One representative per source row, not per hourly file.
        self._sources = []
        self._restore_sources = False
        self._saved_sources = {}
        self._notice_keys = set()
        self._output_directory = ""
        self._buffer = b""
        self._temporary = None
        self._job_result = None
        self._processing = False
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.process_output)
        self.process.readyReadStandardError.connect(self.process_errors)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Combine RFID reads into antenna-presence periods, using the experiment layout and tag list. Original recordings remain unchanged."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.config_path = QLineEdit()
        self.config_path.editingFinished.connect(self.config_path_edited)
        self.config_path.textEdited.connect(lambda text: setattr(self, "_manual_config", bool(text.strip())))
        self.config_path.setPlaceholderText(
            "Automatically finds the experiment’s JSON file"
        )
        row = QHBoxLayout()
        row.addWidget(self.config_path, 1)
        self.config_button = button(
            "Browse…",
            self.choose_config,
            "Choose metadata for the recording being preprocessed; acquisition settings are not changed.",
        )
        row.addWidget(self.config_button)
        form.addRow("Experiment config:", row)
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("<experiment directory>")
        self.output_path.editingFinished.connect(lambda: setattr(self, "_manual_output", True))
        row = QHBoxLayout()
        row.addWidget(self.output_path, 1)
        self.output_button = button(
            "Browse…",
            self.choose_output,
            "Choose the output directory. Successful runs replace the previous generated outputs safely.",
        )
        row.addWidget(self.output_button)
        form.addRow("Output directory:", row)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.add_files_button = button(
            "Add files…",
            self.add_files,
            "Select completed RFID files from one experiment. Supported: CSV, TXT and TSV.",
        )
        self.scan_button = button(
            "Rescan experiment",
            self.scan_directory,
            "Rescan raw_antenna_reads in the experiment directory; preserve existing input assignments.",
        )
        self.remove_button = button(
            "Remove selected",
            self.remove_selected,
            "Remove selected rows from this processing job. Source files are not deleted.",
        )
        for control in (self.add_files_button, self.scan_button, self.remove_button):
            actions.addWidget(control)
        self.add_folder_button = button("Add board folder…", self.add_folder, "All supported raw files in this folder form one continuous recording from one board.")
        actions.addWidget(self.add_folder_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 6)
        self.table.setMinimumHeight(175)
        self.table.setHorizontalHeaderLabels(
            ["Input file / board folder", "Board", "Port metadata", "Hardware / format", "Clock group", "Recording part"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 60), (2, 90), (3, 220), (4, 90), (5, 100)):
            label = self.table.horizontalHeaderItem(col).text()
            label_width = self.table.horizontalHeader().fontMetrics().horizontalAdvance(label) + 24
            self.table.setColumnWidth(col, max(width, label_width))
        layout.addWidget(self.table, 1)
        note = QLabel(
            "One board folder is reported as one recording: individual files and routine internal boundaries are omitted from diagnostics. Confirmed interruptions and clock resets remain reported. Assign each source its board number; antenna names become 1-1, 8-2, etc. The hyphen is automatic. Use the same board numbers in EcoHAB layout. Live acquisition supports up to four boards; archived board numbers are unrestricted. Simultaneous boards share a recording part. Put known restarts in separate sources/parts."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        source_controls = QHBoxLayout()
        self.legacy_note = QLabel()
        self.legacy_note.setWordWrap(True)
        source_controls.addWidget(self.legacy_note, 1)
        self.save_sources_button = button("Save preprocessing settings", lambda: self.ui.save_section(self), "Save source/folder assignments and preprocessing options to the experiment configuration.")
        source_controls.addWidget(self.save_sources_button)
        layout.addLayout(source_controls)
        controls = QHBoxLayout()
        self.parquet = QCheckBox("Parquet")
        self.parquet.setChecked(True)
        self.csv = QCheckBox("CSV")
        controls.addWidget(QLabel("Output:"))
        controls.addWidget(self.parquet)
        controls.addWidget(self.csv)
        self.parquet.setToolTip('datetime, antenna (Categorical), time_under (microsecond duration), animal_id (Enum), internal_board_timestamp.')
        self.csv.setToolTip('Compatibility CSV: original column order and time_spent in milliseconds. Parquet uses time_under in microseconds.')
        self.routes_button = button("Manage RFID and TTL output grouping", self.output_groups, "Scan selected recordings for RFID antennas and TTL inputs, then group or split each input type.")
        controls.addWidget(self.routes_button)
        self.advanced_button = button(
            "Advanced options…",
            self.advanced,
            "Change stitching, interpolation, clock conversion, antenna aliases or merging of recording parts.",
        )
        controls.addWidget(self.advanced_button)
        controls.addStretch(1)
        self.start_button = button(
            "Start preprocessing",
            self.start,
            "Process the selected files in a separate background process.",
        )
        self.cancel_button = button(
            "Cancel",
            self.request_background_stop,
            "Cancel this preprocessing job; original recordings are unaffected.",
        )
        self.cancel_button.setEnabled(False)
        layout.addLayout(controls)
        run_controls = QHBoxLayout()
        run_controls.addStretch(1)
        run_controls.addWidget(self.start_button)
        run_controls.addWidget(self.cancel_button)
        layout.addLayout(run_controls)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMaximumHeight(140)
        self.log.setPlaceholderText(
            "Progress, clock estimates and output results appear here."
        )
        layout.addWidget(self.log)
        self.open_button = button(
            "Open result folder",
            self.open_result,
            "Open the completed preprocessing result folder.",
        )
        self.open_button.setEnabled(False)
        layout.addWidget(self.open_button, 0, Qt.AlignRight)

        self._generation = self._revision = 0
        self._job_generation = None
        self._inventory_selection = None
        self._scan_ready = False
        self._applying_scan = self._updating_table = self._discovery_closed = False
        self._manual_paths, self._removed_sources, self._invalid_sources = set(), set(), set()
        self._profile_overrides, self._automatic_paths = set(), set()
        self._source_fingerprints = {}
        self.discovery = SourceDiscovery(self)
        self.discovery.completed.connect(self._discovery_completed)
        self.discovery.failed.connect(self._discovery_failed)
        self.discovery.activity_changed.connect(self._scan_controls)
        self.watcher = QFileSystemWatcher(self)
        self._scan_debounce = QTimer(self)
        self._scan_debounce.setSingleShot(True)
        self._scan_debounce.timeout.connect(lambda: self.scan_directory(only_changed=True))
        self.watcher.directoryChanged.connect(lambda *_: self._scan_debounce.start(500))
        self.table.itemChanged.connect(self._source_edited)
        self.scan_status = QLabel('Raw recording discovery is idle.')
        self.scan_status.setWordWrap(True)
        layout.insertWidget(layout.indexOf(self.table), self.scan_status)

    def read(self):
        root = self.ui.services.context.save_directory
        sources = {key: copy.deepcopy(entry) for key, entry in self._saved_sources.items()
                   if str((Path(root) / key).resolve()) not in self._removed_sources}
        sources.update({source_key(source.path, root): serialize_source(source, spec, root)
                        for source, spec in zip(self._sources, self.source_specs())})
        return {"acquisition.preprocessing": {
            "sources": list(sources.values()),
            "options": {key: copy.deepcopy(value) for key, value in self.options.items() if key != "timezone"},
            "formats": {"parquet": self.parquet.isChecked(), "csv": self.csv.isChecked()},
        }}

    def load(self, config):
        if self.background_busy():
            raise RuntimeError('Finish preprocessing before loading its settings.')
        self.config_snapshot = copy.deepcopy(config)
        self._reset_experiment(config)
        self._auto_context = (self.ui.services.context.experiment_name, self.ui.services.context.save_directory)
        self.configure_experiment(force=True)

    def _reset_experiment(self, config, restore=True):
        self.discovery.reset()
        self._generation += 1
        self._revision = 0
        self._scan_ready = False
        self._applying_scan = False
        self._manual_config = self._manual_output = False
        self._manual_paths.clear()
        self._removed_sources.clear()
        self._invalid_sources.clear()
        self._source_fingerprints.clear()
        self._automatic_paths.clear()
        self._profile_overrides.clear()
        self._scan_debounce.stop()
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self._updating_table = True
        self.table.setRowCount(0)
        self._updating_table = False
        self._specs.clear()
        self._sources.clear()
        self._notice_keys.clear()
        self.preview_antennas.clear()
        self._preview_by_path.clear()
        self._used_antennas.clear()
        self._used_ttl.clear()
        self._inventory_signature = self._inventory_selection = None
        self._inventory_pending = False
        self._requested_inventory_signature = None
        self._scan_signature = self._config_signature = None
        self._config_discovery_error = ''
        self._last_scan_check = time.monotonic()
        self._output_directory = ''
        self._job_result = None
        self._job_generation = None
        self._buffer = b''
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
        self.open_button.setEnabled(False)
        self.progress.setValue(0)
        self.log.clear()
        self.config_path.clear()
        self.output_path.setText(str(self.ui.services.context.save_directory))
        self.options = copy.deepcopy(self._default_options)
        self.options['timezone'] = config['recording']['timeline']['recording_timezone']
        metadata = config.get('acquisition', {}).get('preprocessing', {}) if restore else {}
        self._saved_sources = {entry['path']: copy.deepcopy(entry) for entry in metadata.get('sources', [])
                               if isinstance(entry, dict) and 'path' in entry}
        root = Path(self.ui.services.context.save_directory)
        self._profile_overrides = {str((root / key).resolve()) for key, entry in self._saved_sources.items() if entry.get('profile')}
        self.options.update({key: copy.deepcopy(value) for key, value in metadata.get('options', {}).items()
                             if key in self.options and key != 'timezone'})
        from deepecohab_recorder.preprocessing.output_plan import normalize_groups
        normalize_groups(self.options)
        formats = metadata.get('formats', {})
        self.parquet.setChecked(bool(formats.get('parquet', True)))
        self.csv.setChecked(bool(formats.get('csv', False)))
        self._restore_sources = False
        self._scan_controls()

    def configure_experiment(self, force=False):
        if self.background_busy() or self._discovery_closed:
            return
        context = self.ui.services.context
        current = (context.experiment_name, context.save_directory)
        changed = current != self._auto_context
        if self._auto_context is not None and current[1] != self._auto_context[1]:
            self._reset_experiment(self.config_snapshot, restore=self._restore_sources)
        self._auto_context = current
        if not (force or changed):
            return
        if not self._manual_output:
            self.output_path.setText(str(context.save_directory))
        self.scan_directory(only_changed=True)

    def viewport_size(self, width, height):
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch if width >= 1000 else QHeaderView.Interactive)
        if width < 1000:
            self.table.setColumnWidth(0, 220)

    def refresh(self):
        if self.background_busy() or self._discovery_closed:
            return
        self.configure_experiment()
        if time.monotonic() - self._last_scan_check >= 30:
            self._last_scan_check = time.monotonic()
            self.scan_directory(only_changed=True)

    def selection_signature(self):
        return (self.options['timezone'], tuple((s.path, self._source_fingerprints.get(s.path),
            a.board_id, a.port, a.profile, a.naive_utc_offset) for s, a in zip(self._sources, self.source_specs())))

    def antenna_names(self):
        if self._inventory_selection == self.selection_signature():
            return set(self._used_antennas)
        return {source_id(channel, spec.board_id) for source, spec in zip(self._sources, self.source_specs()) for channel in source.channels}

    def output_name(self):
        return experiment_output_name(self.config_path.text().strip(), self.read_config())

    def grouping_config(self):
        config = self.read_config()
        # Preview deliberate unsaved layout edits only for this GUI experiment.
        repo = self.ui.services.repo
        current = repo.discover()
        selected = self.config_path.text().strip()
        if (not selected or (current and Path(selected).resolve() == Path(current).resolve())) and hasattr(self.ui, 'layout_state'):
            from deepecohab_recorder.config import set_path
            for key, value in self.ui.layout_state().items():
                set_path(config, key, value)
        return config

    def output_groups(self):
        try:
            if self.background_busy():
                return
            if self.discovery_busy() or not self._scan_ready:
                raise ValueError('Wait for raw recording discovery to finish before managing groups.')
            specs = self.input_specs()
            if not specs:
                self._show_output_groups()
                return
            signature = input_signature(specs, self.options["timezone"])
            if self._inventory_signature == signature and self._inventory_selection == self.selection_signature():
                self._show_output_groups()
                return
            active = {str(Path(c.data_file).resolve()) for c in self.ui.services.controllers.values()
                      if getattr(c, "data_file", None) and c.is_running()}
            if any(str(Path(spec.path).resolve()) in active for spec in specs):
                raise ValueError("A selected file is still being recorded. Use completed recordings for antenna grouping.")
            self._temporary = tempfile.TemporaryDirectory(prefix="deepecohab_antenna_scan_")
            root = Path(self._temporary.name)
            path = root / "settings.json"
            path.write_text(json.dumps(Settings(inputs=specs, output_dir="", timezone=self.options["timezone"]).to_dict()), encoding="utf-8")
            self._cancel_file = root / "cancel"
            self._inventory_pending = True
            self._requested_inventory_signature = signature
            self._requested_inventory_selection = self.selection_signature()
            self._job_generation = self._generation
            self._job_result = None
            self._buffer = b""
            self.progress.setValue(0)
            self.set_running(True)
            self.log.appendPlainText("Scanning complete recordings for used antennas. The grouping window will open when the scan finishes.")
            start_preprocessing(self.process,
                ["--settings", str(path), "--cancel-file", str(self._cancel_file), "--inventory"], root)
        except Exception as exc:
            if not self.background_busy():
                self.set_running(False)
                self._inventory_pending = False
                if self._temporary:
                    self._temporary.cleanup()
                    self._temporary = None
            self.error(exc)

    def _show_output_groups(self):
        try:
            dialog = OutputGroupsDialog(self)
            if dialog.exec() == QDialog.Accepted:
                self.options["device_groups"] = dict(dialog.registry)
                self.options["routes"] = dialog.values()
                self.options["ttl_routes"] = dialog.ttl_values()
                self.options["group_windows_ms"] = dialog.stitch_windows()
        except Exception as exc:
            self.error(exc)

    def read_config(self):
        path = self.config_path.text().strip()
        if path:
            return load_config_bundle(path)
        if self._config_discovery_error:
            raise ValueError(self._config_discovery_error)
        return copy.deepcopy(self.config_snapshot)

    def choose_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Experiment configuration", self.config_path.text(), "JSON (*.json)"
        )
        if path:
            try:
                config = load_config_bundle(path)
                self.options["timezone"] = config["recording"]["timeline"][
                    "recording_timezone"
                ]
                self.config_path.setText(path)
                self._manual_config = True
                if not self._manual_output:
                    self.output_path.setText(str(Path(path).parent))
                self.log.appendPlainText(
                    f'Using {path}; timezone {self.options["timezone"]}. Acquisition metadata is unchanged.'
                )
            except Exception as exc:
                self.error(exc)

    def config_path_edited(self):
        if not self.config_path.text().strip() or self.background_busy():
            return
        try:
            self.options["timezone"] = self.read_config()["recording"]["timeline"][
                "recording_timezone"
            ]
        except Exception as exc:
            self.error(exc)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(
            self, "Preprocessing output", self.output_path.text()
        )
        if path:
            self._manual_output = True
            self.output_path.setText(path)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add RFID recordings",
            str(self.ui.services.repo.paths()[1].parent),
            "Recordings (*.csv *.txt *.tsv)",
        )
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select one board's recording folder", str(self.ui.services.context.save_directory))
        if path:
            self.add_paths([path])

    def scan_directory(self, checked=False, only_changed=False):
        if self.background_busy() or self._discovery_closed:
            return
        if not only_changed:
            self.discovery.reset()
            self._scan_debounce.stop()
            self._revision += 1
            self._applying_scan = False
            self.table.setEnabled(True)
            self._removed_sources.clear()
        try:
            assignments = {source.path: {key: getattr(spec, key) for key in
                ('board_id', 'port', 'profile', 'group', 'part', 'tick_ms', 'counter_bits', 'naive_utc_offset')}
                for source, spec in zip(self._sources, self.source_specs())}
            context = self.ui.services.context
            active = [str(Path(c.data_file).resolve()) for c in self.ui.services.controllers.values()
                      if getattr(c, 'data_file', None) and c.is_running()]
            self.discovery.request(dict(root=str(context.save_directory), name=context.experiment_name,
                timezone=self.options['timezone'], config=copy.deepcopy(self.config_snapshot),
                config_path=self.config_path.text().strip(), manual_config=self._manual_config,
                saved_sources=copy.deepcopy(self._saved_sources), assignments=assignments,
                manual_paths=sorted(self._manual_paths), profile_overrides=sorted(self._profile_overrides),
                removed=sorted(self._removed_sources), active=active, generation=self._generation,
                fingerprints=dict(self._source_fingerprints), revision=self._revision, force=not only_changed))
            self.scan_status.setText('Discovering raw recordings in the background…')
            self._scan_controls()
        except (ValueError, AttributeError) as exc:
            self.error(exc)

    def discovery_busy(self):
        return self.discovery.busy or self._applying_scan or self._scan_debounce.isActive()

    def _scan_controls(self):
        if not hasattr(self, 'discovery'):
            return
        self.start_button.setEnabled(not self._processing and not self.background_busy() and not self.discovery_busy()
                                     and self._scan_ready and bool(self._sources) and not self._invalid_sources)

    def _source_edited(self, *_):
        if not self._updating_table:
            self._revision += 1
            self._inventory_selection = None
            self._scan_debounce.start(350)
            self._scan_controls()

    def _profile_edited(self, path):
        if not self._updating_table:
            self._profile_overrides.add(path)
            self._source_edited()

    def _discovery_failed(self, message):
        self._scan_ready = False
        self.scan_status.setText('Discovery failed. Use Rescan experiment to retry.')
        self.log.appendPlainText('Discovery: ' + message)
        self._scan_controls()

    def _discovery_completed(self, result):
        if self._discovery_closed or result['generation'] != self._generation:
            return
        if result['revision'] != self._revision or self.background_busy():
            if not self.background_busy():
                self.scan_directory(only_changed=True)
            return
        if not self._manual_config:
            self.config_path.setText(result['config_path'])
        self._config_discovery_error = result['config_error']
        if result['config_error']:
            self.log.appendPlainText('Configuration discovery: ' + result['config_error'])
        self.options['timezone'] = result['timezone']
        wanted = {entry['path'] for entry in result['entries']}
        self._updating_table = True
        for row in reversed(range(len(self._sources))):
            source = self._sources[row]
            if source.path in self._automatic_paths and source.path not in wanted:
                self.table.removeRow(row)
                self._sources.pop(row)
                self._specs.pop(row)
                self._source_fingerprints.pop(source.path, None)
                self._invalid_sources.discard(source.path)
        self._updating_table = False
        self._automatic_paths = set(result['automatic'])
        self._applying_scan = True
        self.table.setEnabled(False)
        entries = iter(result['entries'])
        notices, changed = [], [False]
        generation, revision = self._generation, self._revision

        def apply_batch():
            if generation != self._generation or self._discovery_closed:
                return
            if revision != self._revision:
                self._applying_scan = False
                self.table.setEnabled(not self.background_busy())
                self.scan_directory(only_changed=True)
                return
            until = time.monotonic() + .008
            self._updating_table = True
            try:
                while time.monotonic() < until:
                    entry = next(entries)
                    notices.extend(entry['notices'])
                    path = entry['path']
                    row = next((i for i, s in enumerate(self._sources) if s.path == path), None)
                    if entry.get('unchanged') and row is not None:
                        self._invalid_sources.discard(path)
                        continue
                    if entry['invalid'] or entry['source'] is None:
                        if row is not None:
                            self._invalid_sources.add(path)
                            self.table.item(row, 0).setToolTip(path + '\nUnavailable or conflicting source. Correct it and rescan, or remove this row.')
                        continue
                    self._invalid_sources.discard(path)
                    if self._source_fingerprints.get(path) == entry['fingerprint']:
                        continue
                    source = SourceGroup.from_dict(entry['source'])
                    spec = source.prototype
                    members = {s.path for s in source.files}
                    overlap = next((s.label for i, s in enumerate(self._sources) if i != row and members & {f.path for f in s.files}), None)
                    if overlap:
                        notices.append(dict(path=path, format='duplicate', message=f'Overlaps selected source {overlap}; remove that source before adding this folder.'))
                        continue
                    if row is None:
                        if source_key(path, self.ui.services.context.save_directory) not in self._saved_sources:
                            assigned = self.source_specs()
                            known = next((a for a in assigned if spec.port and a.port == spec.port), None)
                            if not source.embedded_boards:
                                if known:
                                    spec.board_id = known.board_id
                                elif source.is_folder:
                                    used = {a.board_id for a in assigned}
                                    spec.board_id = next(n for n in range(1, 10000) if n not in used)
                            spec.part = 1 if source.is_folder else 1 + max((a.part or 1 for a in assigned if a.board_id == spec.board_id), default=0)
                            for member in source.files:
                                member.board_id = spec.board_id
                        row = len(self._sources)
                        self._sources.append(source)
                        self._specs.append(spec)
                        self.table.insertRow(row)
                    else:
                        self._sources[row], self._specs[row] = source, spec
                    self._set_source_row(source, spec, row)
                    self._source_fingerprints[path] = entry['fingerprint']
                    changed[0] = True
            except StopIteration:
                self._updating_table = False
                self._applying_scan = False
                self._scan_ready = True
                self.table.setEnabled(not self.background_busy())
                if changed[0]:
                    self._inventory_selection = None
                self.preview_antennas = self.antenna_names()
                self._report_notices(notices)
                count = sum(len(source.files) for source in self._sources if source.path not in self._invalid_sources)
                self.scan_status.setText(f'Scan complete: {len(self._sources)} sources, {count} selected raw files; {len(notices)} notices.' +
                    (' Resolve or remove unavailable sources before processing.' if self._invalid_sources else ''))
                if result['force']:
                    self.log.appendPlainText(self.scan_status.text())
                legacy = any(s.prototype.profile == 'legacy_raw' for s in self._sources)
                self.legacy_note.setText('Legacy hourly files detected. Board folders merge automatically.' if legacy else 'Save assignments here or with Save experiment configuration on Setup.')
                if self.watcher.directories():
                    self.watcher.removePaths(self.watcher.directories())
                if result['watched']:
                    self.watcher.addPaths(result['watched'])
                self._scan_controls()
                return
            except Exception as exc:
                self._applying_scan = False
                self.table.setEnabled(not self.background_busy())
                self._discovery_failed(str(exc))
                return
            finally:
                self._updating_table = False
            QTimer.singleShot(0, apply_batch)

        apply_batch()

    def close_discovery(self):
        self._discovery_closed = True
        self._scan_debounce.stop()
        self.discovery.close()

    def _report_notices(self, notices):
        fresh = []
        for notice in notices:
            key = (notice["path"], notice["message"])
            if key not in self._notice_keys:
                self._notice_keys.add(key)
                fresh.append(notice)
        if not fresh:
            return
        for notice in fresh[:10]:
            self.log.appendPlainText(f'{Path(notice["path"]).name}: {notice["message"]}')
        if len(fresh) > 10:
            self.log.appendPlainText(f'{len(fresh) - 10} further source notices omitted from this log summary; review the selected sources and the messages above.')
        rejected = [n for n in fresh if n["format"] == "compressed"]
        if rejected:
            QMessageBox.information(self, "Previously preprocessed legacy files excluded",
                f'{len(rejected)} file(s) contain already preprocessed legacy data and were not added. '
                'Select the original raw files containing timestamp, RFID tag and antenna number. '
                'Other supported raw files remain selected.')

    def add_paths(self, paths, rescan=False):
        if self.background_busy():
            return
        raw_root = Path(self.ui.services.context.save_directory) / 'raw_antenna_reads'
        for value in paths:
            path = Path(value).resolve()
            try:
                relative = path.relative_to(raw_root.resolve())
                if len(relative.parts) > 1:
                    path = raw_root / relative.parts[0]
            except ValueError:
                pass
            value = str(path)
            self._manual_paths.add(value)
            self._removed_sources.discard(value)
        self._revision += 1
        self.scan_directory(only_changed=not rescan)

    def _set_source_row(self, source, spec, row):
        external = not Path(source.path).is_relative_to(Path(self.ui.services.context.save_directory).resolve())
        path_item = item(source.label + (' [external]' if external else ''))
        path_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        path_item.setToolTip(source.path + "\nFirst preview PC time: " + str(iso(source.first_pc_us, self.options["timezone"]) or "unresolved; see the clock warning in the log") +
            ("\nOffset-free raw PC times: fixed UTC" + spec.naive_utc_offset if spec.naive_utc_offset else "\nOffset-free raw PC times: experiment timezone rules") +
            ("\nAll files in this folder share these assignments and form one continuous source." if source.is_folder else ""))
        self.table.setItem(row, 0, path_item)
        self.table.setItem(row, 1, item(spec.board_id))
        port_item = item(spec.port)
        port_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        port_item.setToolTip("Connection metadata only. Antenna names use the Board number; no COM label is required.")
        self.table.setItem(row, 2, port_item)
        profile = QComboBox()
        for value in PROFILES:
            profile.addItem(PROFILE_LABELS[value], value)
        profile.setCurrentIndex(max(0, profile.findData(spec.profile)))
        profile.setToolTip("Detected from file contents. v1.0 and v1.5 have the same structure; A–F-only data may need a manual profile choice.")
        self.table.setCellWidget(row, 3, profile)
        profile.currentIndexChanged.connect(lambda *_: self._profile_edited(source.path))
        self.table.setItem(row, 4, item(spec.group))
        self.table.setItem(row, 5, item(spec.part))
        self._preview_by_path[spec.path] = (spec.profile, spec.port, spec.board_id, {source_id(a, spec.board_id) for a in source.channels})

    def source_specs(self):
        values = []
        for row, original in enumerate(self._specs):
            spec = copy.copy(original)
            spec.board_id = int(self.table.item(row, 1).text())
            spec.port = self.table.item(row, 2).text().strip().upper()
            spec.profile = self.table.cellWidget(row, 3).currentData()
            spec.group = self.table.item(row, 4).text().strip()
            spec.part = int(self.table.item(row, 5).text().strip())
            values.append(spec)
        return values

    def input_specs(self):
        if self._invalid_sources:
            raise ValueError('A selected source is unavailable or conflicting. Correct it and rescan, or remove its row.')
        return [spec for source, assignment in zip(self._sources, self.source_specs()) for spec in source.expand(assignment)]

    def remove_selected(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            path = self._sources[row].path
            self._removed_sources.add(path)
            self._invalid_sources.discard(path)
            self._manual_paths.discard(path)
            self.table.removeRow(row)
            self._specs.pop(row)
            self._sources.pop(row)
        self._source_edited()
        self._scan_controls()

    def advanced(self):
        try:
            dialog = AdvancedOptions(self)
            if dialog.exec() == QDialog.Accepted:
                options, clocks = dialog.values()
                offsets = dialog.recording_offsets()
                self.options = options
                for spec, (scale, bits), offset in zip(self._specs, clocks, offsets):
                    spec.tick_ms, spec.counter_bits = scale, bits
                    spec.naive_utc_offset = offset
                self._inventory_signature = None
                self.add_paths([source.path for source in self._sources], rescan=True)
        except Exception as exc:
            self.error(exc)

    def error(self, exc):
        self.log.appendPlainText("ERROR: " + str(exc))
        QMessageBox.warning(self, "Data preprocessing", str(exc))

    def set_running(self, running):
        self._processing = running
        for widget in (
            self.table,
            self.config_path,
            self.output_path,
            self.config_button,
            self.output_button,
            self.add_files_button,
            self.add_folder_button,
            self.save_sources_button,
            self.scan_button,
            self.remove_button,
            self.advanced_button,
            self.routes_button,
            self.parquet,
            self.csv,
            self.start_button,
        ):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self._scan_controls()

    def start(self):
        try:
            if self.background_busy():
                return
            if self.discovery_busy() or not self._scan_ready:
                raise ValueError('Wait for raw recording discovery to finish before preprocessing.')
            self._inventory_pending = False
            self._job_generation = self._generation
            config = self.read_config()
            preview = self.grouping_config()
            if any(preview['recording'].get(k) != config['recording'].get(k) for k in ('layout', 'devices')):
                raise ValueError('Save the EcoHAB layout before preprocessing so the saved config and output groups use the same antenna roles.')
            specs = self.input_specs()
            settings = Settings(
                inputs=specs,
                output_dir=self.output_path.text().strip(),
                config_path=self.config_path.text().strip(),
                parquet=self.parquet.isChecked(),
                csv=self.csv.isChecked(),
                **self.options,
            )
            if not settings.output_dir:
                raise ValueError("Choose an output directory.")
            settings.validate()
            active_files = {
                str(Path(controller.data_file).resolve())
                for controller in self.ui.services.controllers.values()
                if getattr(controller, "data_file", None) and controller.is_running()
            }
            if any(str(Path(spec.path).resolve()) in active_files for spec in specs):
                raise ValueError(
                    "A selected file is still being recorded. Use completed files or an immutable copy."
                )
            review = review_metadata(config, settings)
            if review["issues"]:
                consequences = []
                if "whitelist" in review["changes"]:
                    consequences.append("The animal tag list is incomplete: tag filtering will be disabled for this job and all observed tags retained.")
                if "interpolate" in review["changes"]:
                    consequences.append("The layout is incomplete or unverified: interpolation will be disabled for this job. Observed detections will still be stitched.")
                message = "Some experiment metadata is missing or invalid:\n\n" + "\n".join(review["issues"][:6])
                if len(review["issues"]) > 6:
                    message += f"\n… and {len(review['issues']) - 6} further items. Use Check configuration to review the full list."
                message += "\n\n" + "\n".join(consequences) + "\n\nProceed at your own risk with incomplete metadata? This choice will be recorded in the diagnostics."
                if QMessageBox.question(self, "Preprocess with incomplete metadata?", message,
                        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                    return
                settings.allow_incomplete_metadata = True
            if not (settings.allow_incomplete_metadata and review["incomplete_layout"]) and not self.confirm_layout_labels(config, specs):
                return
            self._temporary = tempfile.TemporaryDirectory(
                prefix="deepecohab_preprocessing_job_"
            )
            root = Path(self._temporary.name)
            # A new experiment needs its real, portable config pair in the experiment root.
            if not settings.config_path:
                from deepecohab_recorder.config_bundle import save_config_bundle
                from deepecohab_recorder.experiment_files import filename
                snapshot = Path(self.ui.services.context.save_directory) / filename(self.config_snapshot['recording']['name'], 'config', 'json')
                if snapshot.exists():
                    raise ValueError("A config already exists for this experiment. Load it before preprocessing.")
                save_config_bundle(snapshot, self.config_snapshot)
                settings.config_path = str(snapshot)
                self.config_path.setText(str(snapshot))
            path = root / "settings.json"
            path.write_text(json.dumps(settings.to_dict()), encoding="utf-8")
            self._cancel_file = root / "cancel"
            self._job_result = None
            self._buffer = b""
            self._output_directory = ""
            self.open_button.setEnabled(False)
            self.progress.setValue(0)
            self.log.clear()
            self.set_running(True)
            start_preprocessing(self.process,
                ["--settings", str(path), "--cancel-file", str(self._cancel_file)], root)
        except Exception as exc:
            self.set_running(False)
            if self._temporary and not self.background_busy():
                self._temporary.cleanup()
                self._temporary = None
            self.error(exc)

    def confirm_layout_labels(self, config, specs):
        report = check_layout(config, specs)
        if report['ambiguous'] or report['mismatched'] or report['collisions']:
            details = []
            for label, ports in report['ambiguous'].items():
                details.append(f'{label}: ' + (', '.join(ports) if ports else 'no matching board/channel'))
            if report['mismatched']:
                details.append('Board numbers / channels absent from the assigned sources: ' + ', '.join(report['mismatched']))
            if report['collisions']:
                details.append('Duplicate physical assignments: ' + ', '.join(report['collisions']))
            QMessageBox.warning(self, 'Layout board assignments need correction',
                'The software cannot distinguish which board an antenna belongs to when boards share channel numbers. '
                'Go to EcoHAB layout, assign each physical antenna its correct board number, and save the experiment configuration. '
                'Also check the source assignments in Data preprocessing.\n\n' + '\n'.join(details[:15]))
            return False
        if not report['changes']:
            return True
        path = self.config_path.text().strip()
        if not path:
            QMessageBox.warning(self, 'Save the experiment configuration',
                'Save the experiment configuration on Setup before automatically updating its layout antenna labels.')
            return False
        expected = config_digest(path)
        mappings = '\n'.join(f'{a} → {b}' for a, b in report['changes'].items())
        answer = QMessageBox.question(self, 'Update antenna board suffixes in the experiment layout?',
            'These layout antenna numbers have one unambiguous board assignment:\n\n' + mappings +
            '\n\nUpdate the selected experiment JSON, cage antenna lists and antenna/tunnel combinations? '
            'With multiple boards sharing channels, automatic assignment is not possible; '
            'those layouts must be corrected manually.\n\nFile: ' + path,
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return False
        updated = apply_layout_changes(config, report['changes'])
        updated['acquisition']['preprocessing'] = self.read()['acquisition.preprocessing']
        updated, backup = write_config_update(path, updated, expected)
        self.config_snapshot = copy.deepcopy(updated)
        self.ui.preprocessing_config_updated(path, updated, True)
        self.log.appendPlainText('Updated layout board suffixes in ' + path)
        return True

    def background_busy(self):
        return self.process.state() != QProcess.NotRunning

    def request_background_stop(self):
        self.discovery.reset()
        self._scan_debounce.stop()
        self._inventory_pending = False
        if self.background_busy():
            self._cancel_file.touch()
            self.cancel_button.setEnabled(False)
            self.log.appendPlainText(
                "Cancelling the background task and closing any output files…"
            )

    def process_output(self):
        if self._job_generation != self._generation:
            self.process.readAllStandardOutput()
            return
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
                kind = message.get("kind")
                if kind == "progress":
                    self.progress.setValue(message["value"])
                    self.log.appendPlainText(message["text"])
                elif kind == "inventory_completed":
                    self._job_result = message
                    self._used_antennas = set(message["antennas"])
                    self._used_ttl = set(message.get("ttl_inputs", []))
                    self._inventory_signature = self._requested_inventory_signature
                    self._inventory_selection = self._requested_inventory_selection
                    self.log.appendPlainText(
                        f'Antenna scan complete: {len(self._used_antennas)} used RFID antennas; '
                        f'{message["ttl_reads"]:,} TTL events kept separate; {message["malformed_rows"]:,} malformed rows excluded.'
                    )
                elif kind == "completed":
                    self._job_result = message
                    self._output_directory = message["directory"]
                    self.open_button.setEnabled(True)
                    update = message.get('config_update', {})
                    if update.get('status') == 'updated':
                        config = load_config_bundle(update['path'])
                        self.config_snapshot = copy.deepcopy(config)
                        self.config_path.setText(update['path'])
                        self.ui.preprocessing_config_updated(update['path'], config, False)
                        self.log.appendPlainText('Experiment metadata and output links saved. Datetime update: ' + update.get('bounds_status', 'unknown') + '.')
                    elif update:
                        self.log.appendPlainText('Experiment times: ' + update.get('status', 'unknown') + '. ' + update.get('note', update.get('error', '')))
                    self.log.appendPlainText(
                        f'Completed: {len(message["outputs"])} data files. See the experiment diagnostic JSON for counts, clock estimates and inserted-row references.'
                    )
                    counts = message.get("counts", {})
                    self.log.appendPlainText(
                        f'Reads: {counts.get("valid_read_rows", 0):,}; '
                        f'RFID periods: {counts.get("observed_RFID_periods", 0):,}; '
                        f'TTL events: {counts.get("TTL_events", 0):,}; '
                        f'rejected tag reads: {counts.get("rejected_tag_reads", 0):,}; '
                        f'interpolated periods: {counts.get("interpolated_periods", 0):,} '
                        f'(Standard: {counts.get("interpolated_standard_periods", 0):,}; '
                        f'additional Extended: {counts.get("interpolated_extended_periods", 0):,}).'
                    )
                    self.log.appendPlainText(
                        f'Route checks: {counts.get("interpolation_standard_candidate_transitions", 0):,} single-missing candidates; '
                        f'{counts.get("interpolation_skipped_requires_extended", 0):,} transitions need Extended mode; '
                        f'{counts.get("ambiguous_transitions", 0):,} unresolved alternatives. '
                        f'Skipped antenna estimates: {counts.get("interpolation_missing_timing_support", 0):,} without transit medians; '
                        f'{counts.get("interpolation_timing_or_coverage_conflicts", 0):,} outside timing/coverage bounds. '
                        f'Blocked transitions: {counts.get("interpolation_blocked_by_recording_boundary", 0):,} at recording/clock boundaries; '
                        f'{counts.get("interpolation_timing_conflicts", 0):,} with incompatible/overlapping timing.'
                    )
                    if counts.get("malformed_rows"):
                        self.log.appendPlainText(
                            f'CHECK DIAGNOSTICS: {counts["malformed_rows"]:,} malformed records were excluded. Verify the input format and board assignments.'
                        )
                    self.log.appendPlainText(
                        f'Missed-data intervals between files: {counts.get("missed_data_periods", 0)}. '
                        + ("See the experiment diagnostic JSON for each board and the exact datetime bounds."
                           if self.options["flag_missed_data_periods"] else "Gap reporting is disabled in Advanced options.")
                    )
                else:
                    self._job_result = message
                    self.log.appendPlainText(str(message.get("text", "")))
            except (ValueError, KeyError):
                self.log.appendPlainText(line.decode("utf-8", errors="replace"))

    def process_errors(self):
        text = (
            bytes(self.process.readAllStandardError())
            .decode("utf-8", errors="replace")
            .strip()
        )
        if text:
            self.log.appendPlainText(text)

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.log.appendPlainText(
                "Could not start the Python preprocessing worker: "
                + self.process.errorString()
            )
            self.process_finished(-1)

    def process_finished(self, exit_code, *args):
        if self._job_generation != self._generation:
            return
        self.process_output()
        self.process_errors()
        self.set_running(False)
        if exit_code and not self._job_result:
            self.log.appendPlainText(
                f"Preprocessing stopped unexpectedly (exit code {exit_code}). Previous completed results are preserved; retry preprocessing to recover an interrupted publication."
            )
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
        show_groups = self._inventory_pending and not exit_code and self._job_result and self._job_result.get("kind") == "inventory_completed"
        self._inventory_pending = False
        if show_groups:
            generation = self._generation
            show = self._show_output_groups
            QTimer.singleShot(0, lambda: show() if generation == self._generation else None)

    def open_result(self):
        if self._output_directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_directory))
