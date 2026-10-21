"""Acknowledged trigger commands, effective timing and cancellable monitoring."""

from __future__ import annotations
import copy
import datetime as dt
import threading
import time
from deepecohab_recorder.clock import ExperimentClock, UTC, localize
from deepecohab_recorder.models import TriggerConfig


def parse_interval_text(text):
    try:
        hh, mm, ss, ms = map(int, text.strip().split(":"))
        if min(hh, mm, ss, ms) < 0 or mm > 59 or ss > 59 or ms > 999:
            return None
        return ((hh * 60 + mm) * 60 + ss) * 1000 + ms
    except (ValueError, AttributeError):
        return None


def trigger_timing(config):
    if config.timing_mode == "interval":
        interval = parse_interval_text(config.interval_text)
        if not interval:
            raise ValueError("Interval must be HH:MM:SS:MS and greater than zero.")
        period = interval * 1000
    else:
        if config.frequency_hz <= 0:
            raise ValueError("Frequency must be greater than zero.")
        period = round(1_000_000 / config.frequency_hz)
    if not 1 <= period <= 300_000_000:
        raise ValueError("Trigger period must be between 1 microsecond and 5 minutes.")
    pulse = round(config.pulse_ms * 1000)
    if not 0 < pulse < period:
        raise ValueError("Pulse width must be positive and shorter than the period.")
    duration = (
        config.duration_days * 86400
        + config.duration_hours * 3600
        + config.duration_minutes * 60
    )
    count = (
        int(config.count)
        if config.train_mode == "count"
        else int(duration * 1_000_000 // period)
    )
    if not 1 <= count <= 4_294_967_295:
        raise ValueError(
            "Pulse count must be in 1..4,294,967,295 (firmware uint32 range)."
        )
    return dict(
        period_us=period,
        pulse_us=pulse,
        count=count,
        frequency_hz=1_000_000 / period,
        interval_ms=period / 1000,
        train_length_s=count * period / 1_000_000,
    )


class HardwareTriggerController:
    def __init__(self):
        self.status, self.last_error, self.last_command = "Idle", "", ""
        self.sent_iso = self.started_iso = self.stopped_iso = ""
        self._thread = None
        self._stop, self.ready = threading.Event(), threading.Event()
        self.clock, self.supports_stop = ExperimentClock(), False
        self._serial = None
        self.config = TriggerConfig()

    def is_busy(self):
        return bool(self._thread and self._thread.is_alive())

    is_running = is_busy

    def status_text(self):
        return self.status

    def start(self, session, config=None):
        if self.is_busy():
            raise RuntimeError("The trigger connection is still active.")
        if config is not None:
            self.config = copy.deepcopy(config)
        timing = trigger_timing(self.config)
        if not self.config.com_port:
            raise ValueError("Select the Hardware Trigger COM port.")
        self.clock = ExperimentClock(session.timezone)
        self._session = session
        self._stop.clear()
        self.ready.clear()
        self.last_error = self.started_iso = self.stopped_iso = ""
        self.supports_stop = False
        self._thread = threading.Thread(
            target=self._worker,
            args=(copy.deepcopy(self.config), timing),
            daemon=True,
            name="hardware-trigger",
        )
        self._thread.start()

    def request_stop(self):
        self._stop.set()

    def stop(self, timeout=5):
        self.request_stop()
        if self._thread:
            self._thread.join(timeout)
        if self.is_busy():
            self.last_error = "Trigger stop timed out; connection still owned."

    def _worker(self, config, timing):
        try:
            import serial

            self.command_history = []
            def record(direction, line):
                from .evidence import save_device
                self.command_history = (self.command_history + [{"pc_datetime": self.clock.iso(),
                    "direction": direction, "text": line[:2048]}])[-100:]
                save_device(self, self._session.file_prefix + ":trigger", {"commands": self.command_history})

            with serial.Serial(
                config.com_port, config.baudrate, timeout=0.1, write_timeout=1
            ) as ser:
                self._serial = ser
                self.status = f"Connecting to {config.com_port}"
                deadline = time.monotonic() + 2.2
                while time.monotonic() < deadline and not self._stop.is_set():
                    line = ser.readline().decode("ascii", errors="replace").strip()
                    if line:
                        record("rx", line)
                        self.supports_stop |= "ACQ_TRIGGER_V2" in line
                if self._stop.is_set():
                    self.status = "Cancelled before arming"
                    return
                now = self.clock.now()
                delay = 0
                if config.start_mode == "scheduled":
                    target = localize(
                        dt.datetime.combine(
                            now.date(),
                            dt.time.fromisoformat(config.scheduled_hhmmss),
                        ),
                        self.clock.zone,
                    )
                    if target.astimezone(UTC) <= now.astimezone(UTC):
                        target = localize(
                            dt.datetime.combine(
                                now.date() + dt.timedelta(days=1), target.time()
                            ),
                            self.clock.zone,
                        )
                    delay = int(
                        (
                            target.astimezone(UTC) - now.astimezone(UTC)
                        ).total_seconds()
                    )
                    if delay >= 86400:
                        raise ValueError(
                            "Legacy ARMUS scheduling supports delays under 24 hours; choose a nearer time."
                        )
                # Firmware uses a relative day clock. UTC-independent virtual clock avoids DST arithmetic on the Arduino.
                target_text = (
                    f"{delay // 3600:02d}:{delay // 60 % 60:02d}:{delay % 60:02d}"
                )
                command = f"ARMUS {timing['period_us']} {timing['pulse_us']} {timing['count']} 00:00:00 {target_text}"
                ser.write((command + "\n").encode())
                ser.flush()
                record("tx", command)
                self.last_command, self.sent_iso = command, self.clock.iso()
                deadline = time.monotonic() + 4
                while not self._stop.is_set():
                    line = ser.readline().decode("ascii", errors="replace").strip()
                    if line:
                        record("rx", line)
                        self.status = line
                        if line.startswith(("ERR", "ERROR")):
                            raise RuntimeError(line)
                        if line.startswith(("ARMED", "START")):
                            self.ready.set()
                        if line.startswith("START"):
                            self.started_iso = self.clock.iso()
                        if line.startswith(("DONE", "STOPPED")):
                            return
                    if not self.ready.is_set() and time.monotonic() > deadline:
                        raise RuntimeError(
                            "No ARMUS acknowledgement from trigger firmware."
                        )
                if self.supports_stop:
                    ser.write(b"STOP\n")
                    ser.flush()
                    record("tx", "STOP")
                    end = time.monotonic() + 2
                    while time.monotonic() < end:
                        line = (
                            ser.readline().decode("ascii", errors="replace").strip()
                        )
                        if line:
                            record("rx", line)
                        if line.startswith(("STOPPED", "DONE")):
                            self.status = "Stopped (Arduino acknowledged)"
                            return
                    raise RuntimeError(
                        "No STOP acknowledgement; physical output state is unknown."
                    )
                self.last_error = "Legacy trigger firmware has no STOP command; pulse train may continue until its programmed end or hardware reset."
                self.status = self.last_error
        except Exception as exc:
            self.last_error = str(exc)
            self.status = f"Error: {exc}"
        finally:
            self._serial = None
            self.stopped_iso = self.clock.iso()
            from .evidence import save_device
            try:
                save_device(self, self._session.file_prefix + ":trigger", {
                    "commands": getattr(self, "command_history", []), "started": self.started_iso,
                    "stopped": self.stopped_iso, "error": self.last_error, "status": self.status})
            except (OSError, ValueError) as exc:
                self.last_error = self.last_error or f"Trigger checkpoint failed: {exc}"
