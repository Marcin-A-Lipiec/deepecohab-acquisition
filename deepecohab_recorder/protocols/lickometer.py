"""Transactional parser for the working-base Arduino protocol and reset epochs."""

from __future__ import annotations
from dataclasses import dataclass

TYPES = {
    "BOOT",
    "READY",
    "EVENT",
    "STATE",
    "STATUS",
    "RECALIBRATION_PENDING",
    "CALIBRATION_START",
    "CALIBRATION_END",
    "WARNING",
    "SENSOR_ERROR",
}


@dataclass(frozen=True)
class LickRecord:
    kind: str
    time_us: int
    epoch: int
    fields: tuple[str, ...]
    channel: int = 0
    state: int = 0


def integer(text):
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


class LickometerProtocol:
    def __init__(self, allow_unannounced_reset=False):
        self.epoch, self.previous = 0, None
        self.boot_seen = False
        self.allow_unannounced_reset = allow_unannounced_reset

    def new_connection(self):
        """A USB gap is a boundary even if a boot message was not observed."""
        if self.previous is not None:
            self.epoch += 1
        self.previous = None
        self.boot_seen = False

    def parse(self, text):
        parts = tuple(part.strip() for part in text.split(","))
        if len(parts) < 3 or parts[0] not in TYPES:
            raise ValueError("Unrecognized lickometer message.")
        kind = parts[0]
        wrap, micros = integer(parts[1]), integer(parts[2])
        if wrap < 0 or not 0 <= micros <= 0xFFFFFFFF:
            raise ValueError("Invalid Arduino clock value.")
        value = (wrap << 32) | micros
        channel = state = 0
        if kind in ("BOOT", "STATUS") and (len(parts) < 4 or not parts[3]):
            raise ValueError(f"{kind} needs a firmware identifier.")
        if (
            kind == "STATUS"
            and len(parts) >= 5
            and parts[4] not in ("READY", "CALIBRATING", "SENSOR_FAULT", "UNKNOWN")
        ):
            raise ValueError("Invalid passive sensor mode.")
        if kind in ("EVENT", "STATE"):
            if len(parts) != 5:
                raise ValueError(f"{kind} needs five fields.")
            channel, state = integer(parts[3]), integer(parts[4])
            if channel not in (1, 2, 3, 4) or state not in (0, 1):
                raise ValueError("Invalid lickometer channel or state.")
        clock_reset = (
            kind != "BOOT" and self.previous is not None and value < self.previous
        )
        if clock_reset and not self.allow_unannounced_reset:
            raise ValueError("Arduino clock moved backwards without BOOT.")
        # Commit clock state only after all fields have passed validation.
        if kind == "BOOT" or clock_reset:
            if self.previous is not None:
                self.epoch += 1
            self.boot_seen = True
        self.previous = value
        return LickRecord(kind, value, self.epoch, parts, channel, state)
