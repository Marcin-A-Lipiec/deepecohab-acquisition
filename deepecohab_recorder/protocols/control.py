"""Bounded version-2 control frames, independent of biological event parsing."""

from __future__ import annotations
from dataclasses import dataclass
import threading
import time
import uuid
import zlib
import re


def payload_crc(payload):
    return f'{zlib.crc32(payload.encode("ascii")) & 0xffffffff:08x}'


@dataclass(frozen=True)
class DeviceStatus:
    kind: str
    uid: str
    boot: str
    mode: str
    clock_source: str
    utc_seconds: int
    schedule_id: str
    anchor_seconds: int
    light_seconds: int
    dark_seconds: int
    revision: int
    config_crc: str
    channel_mask: int
    fault: str
    uptime_us: int
    armed: bool
    received_monotonic: float


def parse_status(line):
    p = line.split(",")
    if len(p) != 17 or p[0] not in ("STATUS2", "HEARTBEAT"):
        raise ValueError("Malformed control status.")
    if (
        p[1] not in ("lighting", "lickometer")
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", p[2])
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", p[3])
        or not re.fullmatch(r"[0-9a-f]{8}", p[12])
        or any(not re.fullmatch(r"[0-9]+", p[i]) for i in (6, 8, 9, 10, 11, 13, 15))
    ):
        raise ValueError("Invalid controller identity or numeric field.")
    if p[16] not in ("0", "1"):
        raise ValueError("Invalid controller arming state.")
    values = [int(p[i]) for i in (6, 8, 9, 10, 11, 13, 15)]
    if (
        any(v < 0 for v in values)
        or values[-2] > 15
        or len(p[2]) > 64
        or len(p[3]) > 64
        or values[2] + values[3] != 86400
        or not values[2]
        or not values[3]
    ):
        raise ValueError("Invalid controller state.")
    return DeviceStatus(
        p[1],
        p[2],
        p[3],
        p[4],
        p[5],
        values[0],
        p[7],
        values[1],
        values[2],
        values[3],
        values[4],
        p[12],
        values[5],
        p[14],
        values[6],
        p[16] == "1",
        time.monotonic(),
    )


class ControlInbox:
    def __init__(self):
        self.condition = threading.Condition()
        self.status = None
        self.hello = None
        self.responses = {}
        self.pending = set()

    def consume(self, line):
        p = line.split(",")
        with self.condition:
            if p[0] in ("STATUS2", "HEARTBEAT"):
                self.status = parse_status(line)
            elif p[0] == "HELLO" and len(p) == 6 and p[1] == "2":
                self.hello = tuple(p)
            elif p[0] in ("ACK", "ERR") and len(p) == 4:
                if p[1] in self.pending:
                    self.responses[p[1]] = p
            else:
                return False
            self.condition.notify_all()
        return True

    def wait_status(self, after=0, timeout=3, cancel=None):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.status or self.status.received_monotonic < after:
                if cancel and cancel.is_set():
                    raise RuntimeError("Connection stopped.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "No valid version-2 device status reply was received. "
                        "Check the COM port, USB connection and Arduino startup. "
                        "A timeout alone does not identify the installed firmware version."
                    )
                self.condition.wait(min(remaining, 0.1))
            return self.status

    def command(self, send, verb, *args, timeout=5, cancel=None):
        req = uuid.uuid4().hex[:10]
        fields = [verb, req, *map(str, args)]
        if any(any(x in field for x in "\r\n") for field in fields):
            raise ValueError("Invalid control field.")
        line = ",".join(fields)
        if len(line) > 511:
            raise ValueError("Control command exceeds 511 bytes.")
        with self.condition:
            self.pending.add(req)
        try:
            send(line)
            deadline = time.monotonic() + timeout
            with self.condition:
                while req not in self.responses:
                    if cancel and cancel.is_set():
                        raise RuntimeError("Connection stopped.")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"{verb}: no acknowledgement; query status before retrying."
                        )
                    self.condition.wait(min(remaining, 0.1))
                response = self.responses.pop(req)
            if response[2] != verb:
                raise ValueError("Acknowledgement command mismatch.")
            if response[0] == "ERR":
                raise RuntimeError(f"{verb}: {response[3]}")
            return int(response[3])
        finally:
            with self.condition:
                self.pending.discard(req)
                self.responses.pop(req, None)


def parse_health_frame(line):
    from datetime import datetime, timezone

    p = line.split(",")
    if p[0] == "HEALTH_SUMMARY" and len(p) == 4:
        return {
            "boot": p[1],
            "history_overwritten": int(p[2]),
            "usb_records_not_delivered": int(p[3]),
        }
    if p[0] == "HISTORY" and len(p) >= 7:
        seconds = int(p[3])
        return {
            "boot": p[1],
            "sequence": int(p[2]),
            "device_datetime": (
                datetime.fromtimestamp(seconds, timezone.utc).isoformat()
                if seconds
                else None
            ),
            "raw_device_record": ",".join(p[4:]),
        }
    raise ValueError("Malformed device history frame.")
