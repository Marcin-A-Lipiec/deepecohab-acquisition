from __future__ import annotations
import csv
import io
import threading
import time
from deepecohab_recorder.controllers.serial_base import SerialCapture
from deepecohab_recorder.controllers.lickometer_stream import LickometerStream
from deepecohab_recorder.protocols.lickometer import LickometerProtocol, TYPES
from deepecohab_recorder.utils import sanitize_com_port

STATUS_COLUMNS = [
    "pc_datetime",
    "pc_monotonic_ns",
    "message_type",
    "arduino_time_us",
    "channel",
    "state_or_status",
    "reason",
    "lickometer_id",
    "raw_line",
    "clock_epoch",
    "event_index",
    "active_channels_before",
    "quality_note",
]
RECOGNIZED_TYPES = TYPES


class LickometerController(LickometerStream, SerialCapture):
    serial_timeout = 0.05

    def __init__(self, config, max_queue=100_000):
        super().__init__(config, max_queue)
        self.kind = f"lickometer{config.device_id}"
        self.status_headers = ";".join(STATUS_COLUMNS) + "\n"
        self._channel_lock = threading.RLock()
        self._reset()

    def _reset(self):
        self._stream_reset()
        self.protocol = LickometerProtocol(allow_unannounced_reset=True)
        self.channels = {
            i: {
                "active": None,
                "events": 0,
                "last_event_iso": "",
                "contact_started_ns": None,
            }
            for i in range(1, 5)
        }
        self.firmware_name = self.last_admin_message = self.protocol_warning = (
            self.last_raw_line
        ) = ""
        self.arduino_mode = "UNKNOWN"
        self.event_index = 0
        self.quality_note = (
            "Initial channel states unknown until STATE or EVENT is received."
        )
        self.last_event_monotonic = self.last_message_monotonic = 0

    def start(self, session):
        if self.is_running():
            raise RuntimeError("Device workers are still active.")
        self._reset()
        super().start(session)
        self._stream_start()

    def _consume(self, raw, pc_time, mono_ns):
        try:
            return self._parse_message(
                raw.decode("ascii", errors="strict").strip(), pc_time, mono_ns
            )
        except (ValueError, UnicodeError):
            self.protocol_warning = (
                "Unrecognized or malformed lickometer output; inspect the GUI-state evidence."
            )
            if any(
                token in raw.upper() for token in (b"FAULT", b"CRASH", b"BARE METAL")
            ):
                self.last_error = "Arduino crash/debug output detected. Physical sensing may have stopped."
            raise

    def _parse_message(self, text, pc_time, mono_ns):
        old_epoch = self.protocol.epoch
        record = self.protocol.parse(text)
        self.last_raw_line, self.last_message_monotonic = text, time.monotonic()
        self._stream_observed(pc_time)
        if self.protocol_warning.startswith("Unrecognized or malformed"):
            self.protocol_warning = ""
        if record.epoch != old_epoch:
            self.health(
                "CLOCK_BOUNDARY",
                previous_epoch=old_epoch,
                new_epoch=record.epoch,
                raw_line=text,
                note="Board restart or clock discontinuity; do not bridge contacts.",
            )
            self._unknown_states("Clock boundary: active contacts are censored.")
        if record.kind == "EVENT":
            self.arduino_mode = "READY"
            self.event_index += 1
            with self._channel_lock:
                ch = self.channels[record.channel]
                ch["active"] = bool(record.state)
                ch["events"] += record.state
                ch["last_event_iso"] = pc_time
                ch["contact_started_ns"] = mono_ns if record.state else None
            self.last_event_monotonic = time.monotonic()
            source = f"{record.channel}_L{self.config.device_id}_{sanitize_com_port(self.config.com_port)}"
            return (
                "event",
                f"{source};{record.state:010d};{record.time_us};{pc_time};{self.config.device_id}\n",
            )
        self.last_admin_message, self.arduino_mode = text, record.kind
        with self._channel_lock:
            active_before = ",".join(
                str(i) for i, ch in self.channels.items() if ch["active"] is True
            )
        note = ""
        if record.kind in ("BOOT", "STATUS"):
            self.firmware_name = record.fields[3]
        if record.kind == "STATUS":
            self.arduino_mode = record.fields[4] if len(record.fields) >= 5 else "READY"
            if self.arduino_mode != "READY":
                self._unknown_states(
                    f"Arduino reports {self.arduino_mode}; channel states are unknown."
                )
        if record.kind == "STATE":
            with self._channel_lock:
                ch = self.channels[record.channel]
                changed = ch["active"] is None or ch["active"] != bool(record.state)
                ch["active"] = bool(record.state)
                if changed:
                    ch["contact_started_ns"] = None
            self.arduino_mode = "READY"
            note = "State snapshot; not a contact onset. An active snapshot has unknown onset."
            if not changed:
                return "status", ""  # Raw log retains all periodic snapshots.
        if record.kind == "WARNING" and any(
            reason in record.fields
            for reason in (
                "SENSOR_LINK_UNCERTAIN",
                "SENSING_GAP_START",
                "USB_RECORDS_DROPPED",
            )
        ):
            note = "Missing-data interval: sensor/USB observations were lost. Active contacts are censored."
            self._unknown_states(note)
        if record.kind == "WARNING" and "SENSOR_LINK_RESTORED" in record.fields:
            note = "Sensor link restored; state snapshots or subsequent transitions establish channel states."
        if record.kind in ("BOOT", "CALIBRATION_START", "SENSOR_ERROR"):
            with self._channel_lock:
                for ch in self.channels.values():
                    ch.update(active=None, contact_started_ns=None)
            note = "Missing-data boundary: active contacts are censored; do not bridge durations across this boundary."
        if record.kind == "READY":
            note = "Calibration/startup gap ends; awaiting state snapshots."
        if note:
            self.quality_note = note
        buffer = io.StringIO(newline="")
        csv.writer(buffer, delimiter=";", lineterminator="\n").writerow(
            [
                pc_time,
                mono_ns,
                record.kind,
                record.time_us,
                record.channel or "",
                record.state if record.kind == "STATE" else record.kind,
                ",".join(record.fields[3:]),
                self.config.device_id,
                text,
                record.epoch,
                self.event_index,
                active_before,
                note,
            ]
        )
        return "status", buffer.getvalue()

    def channel_snapshot(self):
        with self._channel_lock:
            return {key: dict(value) for key, value in self.channels.items()}

    def clear_gui_counters(self):
        with self._channel_lock:
            for ch in self.channels.values():
                ch["events"] = 0

    def status_text(self):
        return (
            super().status_text()
            + f" | {self.connection_state} | Arduino={self.arduino_mode} | epoch={self.protocol.epoch}"
        )
