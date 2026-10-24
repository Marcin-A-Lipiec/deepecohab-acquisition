"""One read-only discovery process, one active request and one coalesced retry."""
import json
from PySide6.QtCore import QObject, QProcess, Signal

from deepecohab_recorder.runtime_paths import app_data_directory
from deepecohab_recorder.worker_process import start_module


class SourceDiscovery(QObject):
    completed = Signal(object)
    failed = Signal(str)
    activity_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.pending = None
        self.running = False
        self._buffer = b''
        self._retired = []

    @property
    def busy(self):
        return self.running or self.pending is not None

    def request(self, request):
        if self.pending and self.pending.get('force'):
            request['force'] = True
        self.pending = request
        if self.running:
            return
        if self.process is None:
            process = self.process = QProcess(self)
            process.started.connect(self._send)
            process.readyReadStandardOutput.connect(lambda: self._read(process))
            process.readyReadStandardError.connect(lambda: process.readAllStandardError())
            process.errorOccurred.connect(lambda error: self._error(process, error))
            process.finished.connect(lambda *args: self._finished(process))
            directory = app_data_directory()
            directory.mkdir(parents=True, exist_ok=True)
            start_module(process, 'deepecohab_recorder.preprocessing.discovery', [], directory)
        elif self.process.state() == QProcess.Running:
            self._send()
        self.activity_changed.emit()

    def _send(self):
        if self.process is None or not self.pending:
            return
        request, self.pending = self.pending, None
        self.running = True
        self.process.write((json.dumps(request) + '\n').encode('utf-8'))
        self.activity_changed.emit()

    def _read(self, process):
        if process is not self.process:
            process.readAllStandardOutput()
            return
        self._buffer += bytes(process.readAllStandardOutput())
        while b'\n' in self._buffer:
            line, self._buffer = self._buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
                self.running = False
                if message['kind'] == 'discovery':
                    self.completed.emit(message['result'])
                else:
                    self.failed.emit(message.get('text', 'Discovery failed.'))
            except (ValueError, KeyError) as exc:
                self.running = False
                self.failed.emit(str(exc))
            if self.pending:
                self._send()
            self.activity_changed.emit()

    def _error(self, process, error):
        if process is self.process and error == QProcess.FailedToStart:
            self.failed.emit('Cannot start source discovery: ' + process.errorString())
            self.reset()

    def _finished(self, process):
        if process in self._retired:
            self._retired.remove(process)
        if process is self.process:
            busy = self.busy
            self.process = None
            self.running = False
            self.pending = None
            self._buffer = b''
            if busy:
                self.failed.emit('Source discovery stopped. Use Rescan experiment to retry.')
            self.activity_changed.emit()
        process.deleteLater()

    def reset(self):
        process, self.process = self.process, None
        self.pending = None
        self.running = False
        self._buffer = b''
        if process is not None:
            self._retired.append(process)
            process.kill()  # Read-only worker; no scientific output to drain.
        self.activity_changed.emit()

    def close(self):
        self.reset()
        for process in list(self._retired):
            process.waitForFinished(1000)
