"""Modeless, cancellable system checks; native probes run outside the GUI."""
import json
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from deepecohab_recorder.runtime_paths import asset
from deepecohab_recorder.video_tools import read_preferences, save_video_paths
from deepecohab_recorder.worker_process import start_module


class SystemCheckDialog(QDialog):
    def __init__(self, parent, options, activity):
        super().__init__(parent)
        self.setWindowTitle('System check')
        self.resize(920, 660)
        self._options, self._activity = options, activity
        self._temporary = None
        self._close_requested = False
        self._report = None
        self._buffer = b''
        self._error = ''
        root = QVBoxLayout(self)
        intro = QLabel('Check software and output storage before recording. Camera setup is optional for RFID and preprocessing.')
        intro.setWordWrap(True)
        root.addWidget(intro)
        tabs = QTabWidget()
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.paths = {}
        self.browse_buttons = []
        for name in ('ffmpeg', 'ffprobe'):
            row = QWidget()
            horizontal = QHBoxLayout(row)
            horizontal.setContentsMargins(0, 0, 0, 0)
            entry = QLineEdit()
            entry.setPlaceholderText('Automatic discovery' + (' beside FFmpeg, then PATH' if name == 'ffprobe' else ' from PATH'))
            button = QPushButton('Browse…')
            button.clicked.connect(lambda checked=False, tool=name: self.browse(tool))
            horizontal.addWidget(entry, 1)
            horizontal.addWidget(button)
            self.paths[name] = entry
            self.browse_buttons.append(button)
            form.addRow(name + ':', row)
        layout.addLayout(form)
        self.save_paths = QPushButton('Save paths for this computer')
        self.save_paths.clicked.connect(self.save_preferences)
        layout.addWidget(self.save_paths, alignment=Qt.AlignLeft)
        options_row = QHBoxLayout()
        self.encode_test = QCheckBox('Test video encoders (three synthetic frames)')
        self.encode_test.setChecked(True)
        self.detect_cameras = QCheckBox('Detect cameras')
        self.detect_cameras.setToolTip('Enumerate cameras without initializing them or acquiring images.')
        options_row.addWidget(self.encode_test)
        options_row.addWidget(self.detect_cameras)
        options_row.addStretch(1)
        layout.addLayout(options_row)
        self.activity_notice = QLabel()
        self.activity_notice.setWordWrap(True)
        layout.addWidget(self.activity_notice)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Component', 'Status', 'Details'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        tabs.addTab(page, 'Checks and paths')
        guide = QTextBrowser()
        guide.setOpenExternalLinks(True)
        guide.setMarkdown(asset('help/camera_setup.md').read_text(encoding='utf-8'))
        tabs.addTab(guide, 'Setup help')
        root.addWidget(tabs, 1)
        self.status = QLabel('Ready. Checks do not start physical acquisition.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.run_button = QPushButton('Run system check')
        self.cancel_button = QPushButton('Cancel check')
        self.export_button = QPushButton('Save report…')
        close = QPushButton('Close')
        self.run_button.clicked.connect(self.run_checks)
        self.cancel_button.clicked.connect(self.request_background_stop)
        self.export_button.clicked.connect(self.export_report)
        close.clicked.connect(self.close)
        for button in (self.run_button, self.cancel_button, self.export_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        root.addLayout(buttons)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        try:
            preferences = read_preferences().get('video_tools', {})
            for name, entry in self.paths.items():
                entry.setText(preferences.get(name, ''))
        except ValueError as exc:
            self.status.setText(str(exc))
        self.update_activity()

    def background_busy(self):
        return self._temporary is not None

    def update_activity(self):
        activity = self._activity()
        busy = self.background_busy()
        if busy and activity['busy'] and not self._started_active:
            self.request_background_stop()
        self.run_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.export_button.setEnabled(not busy and self._report is not None)
        self.save_paths.setEnabled(not busy and not activity['camera'])
        for widget in (*self.paths.values(), *self.browse_buttons):
            widget.setEnabled(not busy and not activity['camera'])
        self.encode_test.setEnabled(not busy and not activity['busy'])
        self.detect_cameras.setEnabled(not busy and not activity['busy'])
        self.activity_notice.setText('Camera and encoder probes are deferred while acquisition, tag matching or preprocessing is active.'
            if activity['busy'] else 'Encoder checks use a small temporary video; full recording speed and camera triggers need separate hardware validation.')

    def browse(self, name):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose ' + name, self.paths[name].text(),
            'Programs (*.exe)' if os.name == 'nt' else 'All files (*)')
        if path:
            self.paths[name].setText(path)

    def save_preferences(self):
        try:
            save_video_paths(**{name: entry.text() for name, entry in self.paths.items()})
            self.status.setText('Video-tool paths saved for this computer. Run the check to verify them.')
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def run_checks(self):
        if self.background_busy():
            return
        activity = self._activity()
        options = self._options()
        options.update({name: entry.text() for name, entry in self.paths.items()},
            encode_test=self.encode_test.isChecked(), camera_devices=self.detect_cameras.isChecked(),
            skip_camera=activity['busy'], skip_video_tests=activity['busy'])
        self._started_active = activity['busy']
        self._close_requested = False
        self._report, self._buffer, self._error = None, b'', ''
        self.table.setRowCount(0)
        self._temporary = tempfile.TemporaryDirectory(prefix='deepecohab_system_check_')
        self._root = Path(self._temporary.name)
        try:
            request = self._root / 'request.json'
            request.write_text(json.dumps(options), encoding='utf-8')
            self.status.setText('Starting system check…')
            start_module(self.process, 'deepecohab_recorder.services.system_check_worker',
                ['--request', str(request), '--report', str(self._root / 'report.json'),
                 '--cancel-file', str(self._root / 'cancel')], self._root)
            self.update_activity()
        except Exception as exc:
            self._error = str(exc)
            self.process_finished(1)

    def read_progress(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self._buffer:
            line, self._buffer = self._buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
                if 'error' in message:
                    self._error = message['error']
                self.status.setText(message.get('progress') or self._error)
            except (ValueError, UnicodeDecodeError):
                pass

    def process_error(self, error):
        if error == QProcess.FailedToStart and self.background_busy():
            self._error = self.process.errorString()
            self.process_finished(1)

    def process_finished(self, exit_code, exit_status=None):
        if not self.background_busy():
            return
        self.read_progress()
        try:
            if exit_code == 0:
                self._report = json.loads((self._root / 'report.json').read_text(encoding='utf-8'))
                checks = self._report['checks']
                self.table.setRowCount(len(checks))
                for row, check in enumerate(checks):
                    for column, key in enumerate(('label', 'status', 'detail')):
                        item = QTableWidgetItem(check[key].replace('_', ' ') if key == 'status' else check[key])
                        item.setToolTip(check['detail'])
                        self.table.setItem(row, column, item)
                self.table.resizeRowsToContents()
                warnings = sum(c['status'] in ('warning', 'error') for c in checks)
                self.status.setText(('Core software and output storage are ready.' if self._report['core_ready']
                    else 'Core software or output storage needs attention.') + f' {warnings} item(s) need attention; review Details and Setup help.')
            elif exit_code == 2:
                self.status.setText('System check cancelled. Temporary probe files were released.')
            else:
                stderr = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')
                self.status.setText(self._error or stderr[-2000:] or f'System check stopped unexpectedly (exit code {exit_code}).')
        except (ValueError, OSError, KeyError) as exc:
            self.status.setText(f'Could not read the system check report: {exc}')
        finally:
            self._temporary.cleanup()
            self._temporary = None
            self.update_activity()
            if self._close_requested:
                self.close()

    def request_background_stop(self):
        if self.background_busy():
            (self._root / 'cancel').touch()
            self.status.setText('Cancelling system check and releasing probe processes…')

    def export_report(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save system check report', 'acquisition_system_check.json', 'JSON files (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps(self._report, indent=2) + '\n', encoding='utf-8')
            except OSError as exc:
                self.status.setText(str(exc))

    def closeEvent(self, event):
        if self.background_busy():
            self._close_requested = True
            self.request_background_stop()
            event.ignore()
        else:
            event.accept()
