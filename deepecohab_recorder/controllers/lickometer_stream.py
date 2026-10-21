"""Receive-only USB observation, gap logging and port recovery. No device commands."""

import csv
import io
import json
import threading
import time

from deepecohab_recorder.utils import sanitize_com_port
from .evidence import read_device, save_device


class LickometerStream:
    stream_timeout_seconds = 5.0
    reconnect_delays = (1, 2, 5, 10, 30)

    def _stream_reset(self):
        self.connection_state = "Port open — waiting for data"
        self._last_stream = time.monotonic()
        self._last_good = None
        self._gap = None
        self._checkpoint = None
        self._checkpoint_at = 0
        self._checkpoint_lock = threading.RLock()
        self._checkpoint_error = ""

    def _open_port(self):
        import serial

        port = serial.Serial(
            port=None, baudrate=115200, timeout=self.serial_timeout, write_timeout=1
        )
        # Normal USB CDC line state, not an application command or a board reset.
        port.dtr = True
        port.rts = False
        port.port = self.config.com_port
        port.open()
        return port

    def _stream_start(self):
        self._checkpoint = f"lickometer_{sanitize_com_port(self.config.com_port)}_monitoring"
        try:
            previous = read_device(self, self._checkpoint)
            if previous:
                if previous.get("monitoring"):
                    self.health(
                        "PREVIOUS_MONITOR_UNCLEAN",
                        previous_checkpoint=previous,
                        note="The previous observation session did not close cleanly; USB coverage is uncertain.",
                    )
        except (ValueError, OSError) as exc:
            self.health("CHECKPOINT_UNREADABLE", error=str(exc))
        self.health(
            "MONITORING_STARTED", com_port=self.config.com_port, mode="passive_usb"
        )
        self._write_checkpoint(True)

    def _write_checkpoint(self, monitoring):
        with self._checkpoint_lock:
            if not self._checkpoint:
                return
            try:
                save_device(
                    self, self._checkpoint,
                    {
                        "monitoring": monitoring and not self._stop.is_set(),
                        "datetime": self.clock.iso(),
                        "last_good_datetime": self._last_good,
                        "open_communication_gap": self._gap,
                        "clock_epoch": self.protocol.epoch,
                        "data_file": self.data_file.name,
                        "control_mode": "passive_usb",
                    },
                )
                if self.last_error == self._checkpoint_error:
                    self.last_error = ""
                self._checkpoint_error = ""
            except OSError as exc:
                # A monitoring sidecar failure must not discard a received lick.
                message = f"Monitoring checkpoint failed: {exc}"
                if message != self._checkpoint_error:
                    self.health("CHECKPOINT_WRITE_FAILED", error=str(exc))
                self._checkpoint_error = message
                self.last_error = self.last_error or message
            self._checkpoint_at = time.monotonic()

    def health(self, event, **details):
        buffer = io.StringIO(newline="")
        csv.writer(buffer, delimiter=";", lineterminator="\n").writerow(
            [
                self.clock.iso(),
                time.monotonic_ns(),
                event,
                self.protocol.previous if self.protocol.previous is not None else "",
                "",
                self.connection_state,
                "",
                self.config.device_id,
                "",
                self.protocol.epoch,
                self.event_index,
                ",".join(
                    str(k) for k, v in self.channel_snapshot().items() if v["active"]
                ),
                json.dumps(details, separators=(",", ":")),
            ]
        )
        self._put("status", buffer.getvalue())

    def _unknown_states(self, note):
        self.quality_note = note
        with self._channel_lock:
            for channel in self.channels.values():
                channel.update(active=None, contact_started_ns=None)

    def _begin_gap(self, reason):
        if self._gap is None:
            self._gap = {
                "last_good_datetime": self._last_good,
                "detected_datetime": self.clock.iso(),
                "reason": str(reason),
            }
            self.health(
                "COMMUNICATION_LOST" if self._last_good else "NO_DATA_RECEIVED",
                gap=self._gap,
                note="USB observation gap; this does not establish loss of physical sensing/BNC output.",
            )
            self.protocol.new_connection()
            self.arduino_mode = "UNKNOWN"
            self._unknown_states(
                "USB observation gap: contacts are censored; do not bridge durations across this interval."
            )
            self._write_checkpoint(True)

    def _stream_observed(self, pc_time):
        self._last_stream = time.monotonic()
        self._last_good = pc_time
        self.connection_state = "Recording — receiving data"
        if self._gap is not None:
            gap, self._gap = self._gap, None
            self.health(
                "COMMUNICATION_RESTORED",
                gap=gap,
                restored_datetime=pc_time,
                note="No missed events are reconstructed. New state snapshots are not contact onsets.",
            )
            if self.last_error.startswith("USB connection"):
                self.last_error = ""
            self._write_checkpoint(True)
        elif self._checkpoint and self._last_stream - self._checkpoint_at >= 30:
            self._write_checkpoint(True)

    def _poll_link(self):
        # No requests and no reconnect/reset on silence: snapshots arrive unsolicited.
        if time.monotonic() - self._last_stream >= self.stream_timeout_seconds:
            self.connection_state = "Port open — no current USB data"
            self._begin_gap(
                f"No valid observations within {self.stream_timeout_seconds:g} seconds"
            )

    def _recover_link(self, error):
        if self._stop.is_set():
            return False
        self._begin_gap(error)
        self.connection_state = "Reconnecting USB"
        attempt = 0
        while not self._stop.is_set():
            with self._serial_lock:
                if self._serial:
                    try:
                        self._serial.close()
                    except OSError:
                        pass
                self._serial = None
            if self._stop.wait(
                self.reconnect_delays[min(attempt, len(self.reconnect_delays) - 1)]
            ):
                return False
            attempt += 1
            try:
                with self._serial_lock:
                    if self._stop.is_set():
                        return False
                    self._serial = self._open_port()
                self.connection_state = "Port open — waiting for data"
                self._last_stream = time.monotonic()
                return True
            except Exception as exc:
                self.last_error = f"USB connection failed: {exc}"
                self.health("RECONNECT_ATTEMPT_FAILED", attempt=attempt, error=str(exc))
        return False

    def _reader_closed(self):
        self.health(
            "MONITORING_CLOSED",
            open_gap=self._gap,
            note="PC recording ended; physical sensing remains autonomous.",
        )
        self.connection_state = "Stopped"
        self._unknown_states("USB recording stopped; live channel states are unknown.")
        self._write_checkpoint(False)
