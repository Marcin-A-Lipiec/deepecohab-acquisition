"""Monitoring link to independently running lighting hardware. Disconnect never stops lights."""

from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from deepecohab_recorder.clock import ExperimentClock
from .evidence import DiscardRawLog
from deepecohab_recorder.protocols.control import ControlInbox, parse_health_frame
from deepecohab_recorder.protocols.lighting import verify_lighting


class LightingController:
    def __init__(self, config, directory, zone, state_sink=None):
        self.config = config
        self.state_sink = state_sink
        self.events = []
        self.latest_status = {}
        self.clock = ExperimentClock(zone)
        self.directory = Path(directory)
        self.inbox = ControlInbox()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.log_lock = threading.RLock()
        self.serial = None
        self.thread = None
        self.state, self.last_error = "Disconnected", ""
        self.uid = config.device_uid
        self._gap = None
        self._last_good = None
        self._last_summary = 0
        self._last_state = None

    def log(self, event, **data):
        with self.log_lock:
            entry = {"datetime": self.clock.iso(), "event": event, **data}
            if event == "status":
                self.latest_status = entry
            else:
                self.events = (self.events + [entry])[-100:]
            if self.state_sink:
                self.state_sink("lighting", {"latest_status": self.latest_status, "events": self.events})

    def open(self):
        self.raw = DiscardRawLog()
        try:
            self._open_serial()
            self.thread = threading.Thread(
                target=self._read, daemon=True, name="lighting-monitor"
            )
            self.thread.start()
            self.send("HELLO")
            self.send("STATUS")
            status = verify_lighting(
                self.inbox.wait_status(timeout=5, cancel=self.stop_event), self.uid
            )
            self.uid = status.uid
            self.state = "Connected"
            self.log("connected", device_uid=self.uid, boot=status.boot)
            return status
        except Exception:
            self.close()
            raise

    def _open_serial(self):
        import serial

        port = serial.Serial(
            port=None, baudrate=self.config.baudrate, timeout=0.2, write_timeout=1
        )
        port.dtr = False
        port.rts = False
        port.port = self.config.com_port
        port.open()
        with self.lock:
            self.serial = port

    def send(self, line):
        with self.lock:
            if not self.serial or not self.serial.is_open:
                raise OSError("Lighting communication is unavailable.")
            self.serial.write((line + "\n").encode("ascii"))

    def command(self, verb, *args):
        return self.inbox.command(self.send, verb, *args, cancel=self.stop_event)

    def query(self):
        after = time.monotonic()
        self.send("STATUS")
        return verify_lighting(
            self.inbox.wait_status(after, 5, self.stop_event), self.uid
        )

    def _read(self):
        buffer = b""
        poll = 0
        last = time.monotonic()
        backoff = iter([1, 2, 5, 10])
        try:
            while not self.stop_event.is_set():
                try:
                    if self.serial is None:
                        self._open_serial()
                        buffer = b""
                        self.send("HELLO")
                        poll = 0
                        last = time.monotonic()
                    if time.monotonic() - poll >= 2:
                        self.send("STATUS")
                        poll = time.monotonic()
                    chunk = self.serial.read(4096)
                    if chunk:
                        self.raw.write(chunk)
                        buffer += chunk
                        if len(buffer) > 65536:
                            raise ValueError("Overlong lighting serial frame.")
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            try:
                                line = raw.rstrip(b"\r").decode("ascii")
                                if line.startswith(("HISTORY,", "HEALTH_SUMMARY,")):
                                    self.log(
                                        "device_health_history",
                                        **parse_health_frame(line),
                                    )
                                    continue
                                accepted = self.inbox.consume(line)
                                if accepted and line.startswith(
                                    ("STATUS2,", "HEARTBEAT,")
                                ):
                                    status = verify_lighting(
                                        self.inbox.status, self.uid
                                    )
                                    if not self.uid:
                                        self.uid = status.uid
                                    last = time.monotonic()
                                    self._last_good = self.clock.iso()
                                    if self._gap:
                                        self.log(
                                            "communication_restored",
                                            gap=self._gap,
                                            status=asdict(status),
                                        )
                                        self._gap = None
                                    self.state = "Connected"
                                    self.last_error = ""
                                    backoff = iter([1, 2, 5, 10])
                                    health_key = (
                                        status.boot,
                                        status.mode,
                                        status.clock_source,
                                        status.fault,
                                        status.revision,
                                        status.channel_mask,
                                    )
                                    if (
                                        health_key != self._last_state
                                        or last - self._last_summary >= 60
                                    ):
                                        self.log(
                                            "status",
                                            status=asdict(status),
                                            pc_clock_difference_seconds=(
                                                (
                                                    self.clock.now().timestamp()
                                                    - status.utc_seconds
                                                )
                                                if status.utc_seconds
                                                else None
                                            ),
                                        )
                                        self._last_summary = last
                                        self._last_state = health_key
                                elif line:
                                    self.log("device_message", line=line)
                            except (UnicodeError, ValueError) as exc:
                                if "identity" in str(exc) or "not a lighting" in str(
                                    exc
                                ):
                                    raise
                                self.log(
                                    "malformed_frame", error=str(exc), raw=raw.hex()
                                )
                    self.raw.flush_due()
                    if time.monotonic() - last > 10:
                        raise TimeoutError("Lighting status timed out.")
                except Exception as exc:
                    if self.stop_event.is_set():
                        break
                    self.last_error = str(exc)
                    self.state = "Reconnecting"
                    if not self._gap:
                        self._gap = {
                            "last_good_datetime": self._last_good,
                            "detected_datetime": self.clock.iso(),
                        }
                        self.log("communication_lost", gap=self._gap, error=str(exc))
                    with self.lock:
                        if self.serial:
                            self.serial.close()
                        self.serial = None
                    if "identity" in str(exc) or "not a lighting" in str(exc):
                        self.state = "Wrong device"
                        self.stop_event.set()
                        break
                    if self.stop_event.wait(next(backoff, 30)):
                        break
        finally:
            self.state = "Disconnected" if not self.last_error else self.state

    def apply(self, timeline):
        from deepecohab_recorder.clock import ExperimentClock

        schedule = timeline["phase_schedule"]
        status = self.query()
        anchor = int(
            ExperimentClock(timeline["recording_timezone"])
            .parse(schedule["anchor_datetime"])
            .timestamp()
        )
        if status.mode == "RUNNING":
            self.verify_schedule(status, schedule, anchor)
            return status
        revision = status.revision + 1
        try:
            if (
                status.clock_source == "UNKNOWN"
                or status.schedule_id != schedule["schedule_id"]
            ):
                self.command(
                    "SET_TIME", datetime.now(timezone.utc).isoformat(timespec="seconds")
                )
            self.command(
                "SET_SCHEDULE",
                revision,
                schedule["schedule_id"],
                anchor,
                schedule["light_duration_seconds"],
                schedule["dark_duration_seconds"],
            )
            self.command("COMMIT", revision)
            self.command("ARM", schedule["schedule_id"], revision)
        except TimeoutError:
            observed = self.query()
            self.verify_schedule(observed, schedule, anchor)
            if observed.revision != revision:
                raise
            self.log("ack_timeout_readback_verified", status=asdict(observed))
            if not observed.armed:
                self.command("ARM", schedule["schedule_id"], revision)
        status = self.query()
        self.verify_schedule(status, schedule, anchor)
        if status.mode != "RUNNING":
            raise RuntimeError("Lighting did not enter RUNNING state.")
        self.log("schedule_applied", status=asdict(status))
        return status

    @staticmethod
    def verify_schedule(status, schedule, anchor):
        if (
            status.schedule_id,
            status.anchor_seconds,
            status.light_seconds,
            status.dark_seconds,
        ) != (
            schedule["schedule_id"],
            anchor,
            schedule["light_duration_seconds"],
            schedule["dark_duration_seconds"],
        ):
            raise RuntimeError(
                "The Arduino has a different active schedule. Disconnecting will preserve it; explicitly stop lighting before applying a new experiment."
            )

    def stop_lighting(self):
        self.command("STOP_CONTROL")
        status = self.query()
        if status.mode != "IDLE":
            raise RuntimeError("Lighting stop was not confirmed.")
        self.log("lighting_stopped", status=asdict(status))

    def close(self):
        self.stop_event.set()
        with self.lock:
            if self.serial:
                try:
                    self.serial.close()
                except OSError:
                    pass
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(3)
        self.log("monitoring_closed", open_gap=self._gap)
        for handle in (getattr(self, "raw", None), getattr(self, "log_file", None)):
            if handle:
                handle.close()
        self.log_file = None
        self.state = "Disconnected"
