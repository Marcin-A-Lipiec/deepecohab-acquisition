"""Explicit hardware profiles: channel alphabets cannot be auto-detected safely."""

from __future__ import annotations
from dataclasses import dataclass
import re
from deepecohab_recorder.models import NEW_DAQ, OLD_DAQ, DAQ_V15

NEW_RFID_CHANNELS = list("0123456789ABCDEF")
V15_RFID_CHANNELS = list("ABCDEFGHIJKLMNOP")
OLD_RFID_CHANNELS = list("12345678")
TTL_CHANNELS = ["TTL1", "TTL2", "TTL3", "TTL4"]
TTL_PAYLOAD_TO_CHANNEL = {f"0{i}000000": f"TTL{i}" for i in range(1, 5)}


@dataclass(frozen=True)
class RFIDRecord:
    raw_channel: str
    payload: str
    board_time: str
    logical_channel: str
    is_ttl: bool
    raw_text: str


class RFIDProtocol:
    def parse(self, raw: bytes) -> RFIDRecord:
        raise NotImplementedError


class NewRFIDProtocol(RFIDProtocol):
    name = NEW_DAQ
    rfid_channels = NEW_RFID_CHANNELS
    ttl_channels = TTL_CHANNELS
    start_command = b"READC"
    legacy_payloads = True

    def parse(self, raw: bytes) -> RFIDRecord:
        text = raw.decode("ascii", errors="strict").strip()
        parts = text.split(";") if ";" in text else text.split()
        if len(parts) not in (3, 4):
            raise ValueError("RFID message needs channel, payload and device time.")
        channel, payload, board_time = [part.strip().upper() for part in parts[:3]]
        if not re.fullmatch(r"[0-9A-F]{1,16}", board_time):
            raise ValueError("Invalid hexadecimal device counter.")
        ttl = None
        if channel == "10":
            if re.fullmatch(r"0*[1-4]", payload):
                ttl = f"TTL{int(payload)}"
            elif self.legacy_payloads:
                ttl = TTL_PAYLOAD_TO_CHANNEL.get(payload)
            if ttl is None:
                raise ValueError(f"Unknown TTL payload {payload!r}.")
        elif self.legacy_payloads and channel in ("11", "12", "13"):
            ttl = f"TTL{int(channel) - 9}"
        elif channel not in self.rfid_channels:
            raise ValueError(f"Unknown {self.name} RFID channel {channel!r}.")
        if ttl is None and not re.fullmatch(r"[0-9A-F]{8,10}", payload):
            raise ValueError("Invalid RFID tag: expected 8–10 hexadecimal characters.")
        return RFIDRecord(
            channel, payload, board_time, ttl or channel, ttl is not None, text
        )


class V15RFIDProtocol(NewRFIDProtocol):
    name = DAQ_V15
    rfid_channels = V15_RFID_CHANNELS
    legacy_payloads = False


class OldRFIDProtocol(RFIDProtocol):
    name = OLD_DAQ
    rfid_channels = OLD_RFID_CHANNELS
    ttl_channels = ()
    start_command = b""

    def parse(self, raw: bytes) -> RFIDRecord:
        text = raw.decode("ascii", errors="strict").strip()
        parts = text.split("-")
        if len(parts) != 3:
            raise ValueError("Legacy RFID message needs three fields.")
        channel_raw, board_time, tag = [p.strip().upper() for p in parts]
        if not re.fullmatch(r"[0-7]", channel_raw):
            raise ValueError("Legacy channel must be 0–7.")
        if not re.fullmatch(r"[0-9A-F]{10}", board_time) or not re.fullmatch(
            r"[0-9A-F]{10}", tag
        ):
            raise ValueError(
                "Legacy tag and counter must have ten hexadecimal characters."
            )
        return RFIDRecord(
            channel_raw, tag, board_time, str(int(channel_raw) + 1), False, text
        )


def protocol_for_hardware(hardware: str) -> RFIDProtocol:
    profiles = {
        NEW_DAQ: NewRFIDProtocol,
        DAQ_V15: V15RFIDProtocol,
        OLD_DAQ: OldRFIDProtocol,
    }
    if hardware not in profiles:
        raise ValueError(f"Unknown hardware profile: {hardware!r}")
    return profiles[hardware]()
