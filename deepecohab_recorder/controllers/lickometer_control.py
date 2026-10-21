"""Opt-in v2 control and USB recovery for the autonomous lickometer."""

import csv
import io
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from deepecohab_recorder.control_settings import settings_wire, validate_options
from deepecohab_recorder.protocols.control import (
    ControlInbox,
    payload_crc,
    parse_health_frame,
)
from deepecohab_recorder.utils import sanitize_com_port
from .evidence import read_device, save_device


class LickometerControl:
    def _control_reset(self):
        self.inbox = ControlInbox()
        self.control_status = None
        self.connection_state = "Connecting"
        self._heartbeat = time.monotonic()
        self._poll_at = self._summary_at = 0
        self._gap = None
        self._last_good = None
        self._boot = None
        self._wrong_device = False
        self._identity_verified = False
        self._health_key = None
        self._history_seen = []
        self._expected = None
        self._desired_configuration = False
        self.needs_reconfigure = False
        self.control_retry_at = 0
        self._checkpoint = None
        self._r, self._c = validate_options(
            self.config.automatic_recalibration, self.config.connection_monitoring
        )

    def _open_port(self):
        import serial

        port = serial.Serial(
            port=None,
            baudrate=self.config.baudrate,
            timeout=self.serial_timeout,
            write_timeout=1,
        )
        # UNO R4 native USB CDC requires DTR for both read() and write().
        # Keeping it low silences a correctly flashed board. At the normal
        # 115200 baud this is not the R4's 1200-baud bootloader reset sequence.
        port.dtr = True
        port.rts = False
        port.port = self.config.com_port
        port.open()
        return port

    def _control_start(self):
        self._checkpoint = f"lickometer_{sanitize_com_port(self.config.com_port)}_monitoring"
        try:
            previous = read_device(self, self._checkpoint)
            if previous:
                if previous.get("monitoring"):
                    self.health(
                        "PREVIOUS_MONITOR_UNCLEAN",
                        previous_checkpoint=previous,
                        note="USB observation gap is uncertain after the previous GUI/process exit; this does not prove TTL or sensor loss.",
                    )
        except (OSError, ValueError) as exc:
            self.health("CHECKPOINT_UNREADABLE", error=str(exc))
        self._write_checkpoint(True)

    def _write_checkpoint(self, monitoring):
        if self._checkpoint:
            save_device(
                self, self._checkpoint,
                {
                    "monitoring": monitoring,
                    "datetime": self.clock.iso(),
                    "last_good_datetime": self._last_good,
                    "device_uid": self.config.device_uid,
                    "boot": self._boot,
                    "open_communication_gap": self._gap,
                    "data_file": self.data_file.name,
                },
            )

    def health(self, event, **details):
        buffer = io.StringIO(newline="")
        csv.writer(buffer, delimiter=";", lineterminator="\n").writerow(
            [
                self.clock.iso(),
                time.monotonic_ns(),
                event,
                self.control_status.uptime_us if self.control_status else "",
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

    def _control_frame(self, text):
        if text.startswith(("HISTORY,", "HEALTH_SUMMARY,")):
            details = parse_health_frame(text)
            key = (
                details["boot"],
                details.get("sequence"),
                details.get("history_overwritten"),
                details.get("usb_records_not_delivered"),
            )
            if key not in self._history_seen:
                self.health("DEVICE_HEALTH_HISTORY", **details)
                self._history_seen = (self._history_seen + [key])[-128:]
            return True
        if not text.startswith(("HELLO,", "ACK,", "ERR,", "STATUS2,", "HEARTBEAT,")):
            return False
        self.inbox.consume(text)
        if text.startswith(("STATUS2,", "HEARTBEAT,")):
            status = self.inbox.status
            if status.kind != "lickometer" or (
                self.config.device_uid and status.uid != self.config.device_uid
            ):
                self._wrong_device = True
                self.health("WRONG_DEVICE", actual=asdict(status))
                raise ValueError(
                    "Lickometer identity mismatch; automatic reconnection stopped."
                )
            if self._boot and status.boot != self._boot:
                self.protocol.epoch += 1
                self.protocol.previous = None
                self.health(
                    "BOARD_RESET",
                    previous_boot=self._boot,
                    current_boot=status.boot,
                    note="New clock epoch; do not bridge contacts across this boundary.",
                )
            self._boot = status.boot
            self.config.device_uid = status.uid
            self.control_status = status
            self._identity_verified = True
            self._heartbeat = time.monotonic()
            self._last_good = self.clock.iso()
            self.connection_state = "Connected"
            self.last_error = ""
            if self._gap:
                self.health(
                    "COMMUNICATION_RESTORED",
                    gap=self._gap,
                    restored_datetime=self._last_good,
                    note="USB log gap closed. Sensing/TTL continuity must be judged separately from sensor reports and recorded RFID TTLs.",
                )
                self._gap = None
            with self._channel_lock:
                for ch, values in self.channels.items():
                    known = status.mode == "READY" and status.fault == "NONE"
                    values["active"] = (
                        bool(status.channel_mask & (1 << (ch - 1))) if known else None
                    )
            key = (
                status.boot,
                status.mode,
                status.clock_source,
                status.fault,
                status.revision,
                status.schedule_id,
            )
            if key != self._health_key or self._heartbeat - self._summary_at >= 60:
                self.health("DEVICE_STATUS", status=asdict(status))
                self._summary_at = self._heartbeat
                self._health_key = key
                self._write_checkpoint(True)
            self.arduino_mode = status.mode
            if self._desired_configuration and self._expected is None:
                self.needs_reconfigure = True
            observed = (
                status.config_crc,
                status.schedule_id,
                status.anchor_seconds,
                status.light_seconds,
                status.dark_seconds,
                status.armed,
            )
            if self._expected and (
                observed != self._expected or status.clock_source == "UNKNOWN"
            ):
                if not self.needs_reconfigure:
                    self.health(
                        "SETTINGS_MISMATCH", expected=self._expected, observed=observed
                    )
                self.needs_reconfigure = True
                self.protocol_warning = (
                    "Device settings need re-verification after reconnect/reset."
                )

        elif text.startswith(("ACK,", "ERR,")):
            self.health("CONTROL_REPLY", raw_line=text)
        return True

    def _poll_link(self):
        if self._wrong_device:
            raise RuntimeError("Lickometer identity mismatch.")
        now = time.monotonic()
        if now - self._poll_at >= self._c["heartbeat_interval_seconds"]:
            self.send_command("STATUS")
            self._poll_at = now
        if now - self._heartbeat > self._c["heartbeat_timeout_seconds"]:
            if (
                self.inbox.hello
                or self.control_status
                or self._gap
                or not self.firmware_name
            ):
                raise TimeoutError(
                    "No valid lickometer heartbeat within the configured timeout."
                )
            # Legacy firmware remains readable; its absence of a heartbeat is explicit.
            self.protocol_warning = "Legacy firmware: autonomous Patch 3 safeguards and heartbeat monitoring are unavailable. Upload lickometer v6.2."

    def _recover_link(self, error):
        if (
            self._stop.is_set()
            or self._wrong_device
            or not self._c["reconnect_enabled"]
        ):
            return False
        if not self._gap:
            self._gap = {
                "last_good_datetime": self._last_good,
                "detected_datetime": self.clock.iso(),
                "error": str(error),
            }
            self.health(
                "COMMUNICATION_LOST",
                gap=self._gap,
                note="USB observation interrupted; physical sensing and TTL may continue.",
            )
            self._write_checkpoint(True)
        self.connection_state = "Reconnecting"
        self._identity_verified = False
        with self._channel_lock:
            for values in self.channels.values():
                values.update(active=None, contact_started_ns=None)
        delays = self._c["reconnect_delays_seconds"]
        attempt = 0
        while not self._stop.is_set():
            with self._serial_lock:
                if self._serial:
                    try:
                        self._serial.close()
                    except OSError:
                        pass
                self._serial = None
            if self._stop.wait(delays[min(attempt, len(delays) - 1)]):
                return False
            attempt += 1
            try:
                with self._serial_lock:
                    self._serial = self._open_port()
                self._heartbeat = time.monotonic()
                self._poll_at = 0
                self.send_command("HELLO")
                self.send_command("STATUS")
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.health("RECONNECT_ATTEMPT_FAILED", attempt=attempt, error=str(exc))
        return False

    def _reader_closed(self):
        self.health(
            "MONITORING_CLOSED",
            open_gap=self._gap,
            note="Stopping USB recording does not stop physical sensing or TTL.",
        )
        self._write_checkpoint(False)

    def configure(self, timeline):
        self._desired_configuration = True
        after = time.monotonic()
        self.send_command("HELLO")
        self.send_command("STATUS")
        status = self.inbox.wait_status(after, 5, self._stop)
        if status.kind != "lickometer" or status.uid != self.config.device_uid:
            raise ValueError("Unverified lickometer identity.")
        schedule = timeline.get("phase_schedule", {})
        payload = settings_wire(self.config.automatic_recalibration, timeline["phases"])
        crc = payload_crc(payload)
        anchor = (
            int(self.clock.parse(schedule["anchor_datetime"]).timestamp())
            if schedule.get("anchor_datetime")
            else 0
        )
        sid = schedule.get("schedule_id") or "-"
        expected = (
            sid,
            anchor,
            schedule.get("light_duration_seconds", 43200),
            schedule.get("dark_duration_seconds", 43200),
        )
        actual = (
            status.schedule_id,
            status.anchor_seconds,
            status.light_seconds,
            status.dark_seconds,
        )
        if (
            status.config_crc == crc
            and actual == expected
            and status.clock_source != "UNKNOWN"
            and (not anchor or status.armed)
        ):
            self._remember_settings(status)
            return status
        # Clock is initialized only once per power/RTC epoch; normal updates never realign it.
        command = lambda verb, *args: self.inbox.command(
            self.send_command, verb, *args, cancel=self._stop
        )
        revision = status.revision + 1
        try:
            if status.clock_source == "UNKNOWN":
                command(
                    "SET_TIME", datetime.now(timezone.utc).isoformat(timespec="seconds")
                )
            command("SET_SETTINGS", revision, crc, payload)
            command("SET_SCHEDULE", revision, *expected)
            command("COMMIT", revision)
            if anchor:
                command("ARM", sid, revision)
        except TimeoutError:
            after = time.monotonic()
            self.send_command("STATUS")
            observed = self.inbox.wait_status(after, 5, self._stop)
            accepted = (
                observed.schedule_id,
                observed.anchor_seconds,
                observed.light_seconds,
                observed.dark_seconds,
            )
            if (
                observed.revision != revision
                or observed.config_crc != crc
                or accepted != expected
            ):
                raise
            self.health("ACK_TIMEOUT_READBACK_VERIFIED", status=asdict(observed))
            if anchor and not observed.armed:
                command("ARM", sid, revision)
        after = time.monotonic()
        self.send_command("STATUS")
        result = self.inbox.wait_status(after, 5, self._stop)
        if (
            result.revision != revision
            or result.config_crc != crc
            or (
                result.schedule_id,
                result.anchor_seconds,
                result.light_seconds,
                result.dark_seconds,
            )
            != expected
        ):
            raise RuntimeError(
                "Lickometer settings readback did not match; inspect diagnostics."
            )
        if anchor and not result.armed:
            raise RuntimeError("The daily schedule was not armed.")
        self.health(
            "SETTINGS_VERIFIED",
            status=asdict(result),
            settings=self.config.automatic_recalibration,
        )
        self._remember_settings(result)
        return result

    def _remember_settings(self, status):
        self._expected = (
            status.config_crc,
            status.schedule_id,
            status.anchor_seconds,
            status.light_seconds,
            status.dark_seconds,
            status.armed,
        )
        self.needs_reconfigure = False
        if self.protocol_warning.startswith("Device settings need"):
            self.protocol_warning = ""
