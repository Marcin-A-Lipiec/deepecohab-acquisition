"""Bound display work per event-loop turn while retaining FIFO notifications."""
from dataclasses import dataclass
import queue
import time

from PySide6.QtCore import QObject, QTimer

from deepecohab_recorder.clock import ExperimentClock


@dataclass(frozen=True)
class NotificationBatch:
    lines: str
    last_message: str
    last_error: str | None


class NotificationPump(QObject):
    def __init__(self, messages, timezone, display, parent=None, *, limit=200, budget_ms=8):
        super().__init__(parent)
        self.messages, self.timezone, self.display = messages, timezone, display
        self.limit, self.budget_ms = limit, budget_ms
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.drain)

    def drain(self):
        self.timer.stop()
        lines, last_error, stamp = [], None, None
        deadline = time.monotonic() + self.budget_ms / 1000
        for _ in range(self.limit):
            try:
                level, message = self.messages.get_nowait()
            except queue.Empty:
                break
            if stamp is None:
                stamp = ExperimentClock(self.timezone()).now().strftime("%H:%M:%S %Z")
            lines.append(f"{stamp} {level.upper()}: {message}")
            if level == "error":
                last_error = message
            if time.monotonic() >= deadline:
                break
        if lines:
            self.display(NotificationBatch("\n".join(lines), message, last_error))
        if not self.messages.empty():
            self.timer.start(0)
