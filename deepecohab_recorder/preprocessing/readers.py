"""Explicit parsers; malformed records are accounted for instead of silently dropped."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from datetime import datetime
import json
from pathlib import Path
import re

from deepecohab_recorder.protocols.rfid import NewRFIDProtocol, V15RFIDProtocol
from deepecohab_recorder.source_identity import assigned_source, split_source, source_id, IdentityConflict
from .models import COMPRESSED_MESSAGE, InputFile, RawRecord, parse_pc
from .raw_clock import RawClockError


def detect(path: str) -> str:
    with Path(path).open(encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            header = re.sub(r"\s", "", text).lower()
            if all(k in header for k in ("pctime", "channel", "boardtime", "rfidtag")):
                return "old_decimal"
            parts = text.split(";")
            if len(parts) in (4, 5) and re.fullmatch(r"[0-9A-Fa-f]+", parts[2].strip()):
                return "current" if len(parts) == 5 else "raw"
            fields = text.split()
            if len(fields) == 3 and re.fullmatch(r"\d{8}_\d{9,12}", fields[0]):
                return "legacy_raw"
            if len(fields) >= 6 and re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", fields[1]):
                return "compressed"
            return "unknown"
    return "unknown"


def channel_id(channel: str, board_id: int, port: str = '') -> str:
    return assigned_source(channel, board_id, port)


def raw_channel(channel: str) -> str:
    return split_source(channel)[0]


def parse_fields(
    parts: list[str], fmt: str, spec: InputFile, zone: str, line: int
) -> RawRecord:
    if fmt == "old_decimal":
        if len(parts) != 4:
            raise ValueError("Expected PC Time, Channel, Board Time, RFID Tag.")
        pc, channel, counter, tag = (v.strip() for v in parts)
        try:
            number = Decimal(counter)
            if (
                not number.is_finite()
                or number != number.to_integral_value()
                or number < 0
            ):
                raise ValueError("Expected an unsigned decimal board counter.")
            ticks = int(number)
        except InvalidOperation as exc:
            raise ValueError("Invalid decimal board counter.") from exc
        if channel not in list("12345678"):
            raise ValueError("Old-board CSV channels must be 1–8.")
        if not re.fullmatch(r"[0-9A-Fa-f]{6,16}", tag):
            raise ValueError("Invalid RFID tag.")
        return RawRecord(
            line,
            channel_id(channel, spec.board_id, spec.port),
            tag.upper(),
            parse_pc(pc, zone, spec.naive_utc_offset),
            ticks,
            counter,
        )
    if fmt == "legacy_raw":
        if len(parts) != 3:
            raise ValueError("Expected legacy PC timestamp, RFID tag and antenna number.")
        pc, tag, channel = (v.strip() for v in parts)
        if not re.fullmatch(r"\d{8}_\d{9,12}", pc):
            raise ValueError("Legacy PC time must use YYYYMMDD_HHMMSSmmm (up to microseconds).")
        if not re.fullmatch(r"[0-9A-Fa-f]{10}", tag):
            raise ValueError("Legacy RFID tags must have ten hexadecimal characters.")
        if not re.fullmatch(r"0?[1-8]", channel):
            raise ValueError("Legacy raw antenna numbers must be 1–8 or 01–08.")
        pc = datetime.strptime(pc, "%Y%m%d_%H%M%S%f").isoformat()
        return RawRecord(line, channel_id(str(int(channel)), spec.board_id, spec.port), tag.upper(),
                         parse_pc(pc, zone, spec.naive_utc_offset), None, "")
    if len(parts) not in (4, 5):
        raise ValueError("Expected four or five semicolon-separated columns.")
    channel, tag, counter, pc = (v.strip() for v in parts[:4])
    if len(parts) == 5 and int(parts[4]) != spec.board_id:
        raise IdentityConflict("Embedded board number conflicts with the input assignment.")
    source = channel_id(channel, spec.board_id, spec.port)
    channel = raw_channel(source)
    if not re.fullmatch(r"[0-9A-Fa-f]{1,16}", counter):
        raise ValueError("Invalid hexadecimal counter.")
    ticks = int(counter, 16)
    if spec.profile == "old_hex":
        if channel not in list("12345678") or not re.fullmatch(r"[0-9A-Fa-f]{10}", tag):
            raise ValueError(
                "Old-board acquisition output requires channel 1–8 and a ten-character tag."
            )
        kind = "rfid"
    elif re.fullmatch(r"TTL[1-4]", channel):
        kind = "ttl"
    else:
        protocol = V15RFIDProtocol() if spec.profile == "v1.5" else NewRFIDProtocol()
        record = protocol.parse(f"{channel};{tag};{counter}".encode("ascii"))
        source = source_id(record.logical_channel, spec.board_id)
        kind = "ttl" if record.is_ttl else "rfid"
    return RawRecord(
        line,
        source,
        None if kind == "ttl" else tag.upper(),
        parse_pc(pc, zone, spec.naive_utc_offset),
        ticks,
        counter,
        kind,
    )


def records(spec: InputFile, zone: str):
    """Yield (record, error, source text); headers and blank lines are not reads."""
    fmt = detect(spec.path)
    if fmt == "compressed":
        raise ValueError(f"{Path(spec.path).name}: {COMPRESSED_MESSAGE}")
    if fmt == "unknown":
        raise ValueError(f"Unsupported file structure: {spec.path}")
    if (fmt in ("old_decimal", "legacy_raw")) != (
        spec.profile in ("old_decimal", "legacy_raw")
    ) or (fmt in ("old_decimal", "legacy_raw") and fmt != spec.profile):
        raise ValueError(f"Selected profile does not match {Path(spec.path).name}.")
    with Path(spec.path).open(
        encoding="utf-8-sig", errors="replace", newline=""
    ) as handle:
        order = None
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            text = line.strip()
            try:
                if fmt == "legacy_raw":
                    parts = text.split()
                else:
                    parts = next(
                        csv.reader(
                            [text], delimiter="," if fmt == "old_decimal" else ";"
                        )
                    )
                if fmt == "old_decimal" and order is None:
                    header = [re.sub(r"\s", "", p).lower() for p in parts]
                    order = [
                        header.index(k)
                        for k in ("pctime", "channel", "boardtime", "rfidtag")
                    ]
                    continue
                if order is not None:
                    parts = [parts[i] for i in order]
                yield parse_fields(parts, fmt, spec, zone, line_number), None, text
            except IdentityConflict:
                raise
            except RawClockError as exc:
                raise RawClockError(f"{Path(spec.path).name}, line {line_number}: {exc} No timestamp is being guessed or silently discarded.") from exc
            except (ValueError, IndexError, OverflowError, UnicodeError) as exc:
                yield None, f"Line {line_number}: {exc}", text


def scan(paths: list[str], config: dict, zone: str, *, preview_limit=500, notices=None, profile_override=None, naive_utc_offset="", allow_unresolved_clock=False) -> list[dict]:
    boards = list(config.get("acquisition", {}).get("boards", {}).values())
    counts = {}
    found = []
    history = {}
    roots = {Path(path).parent for path in paths}
    for directory in list(roots):
        raw_root = next((parent for parent in (directory, *directory.parents) if parent.name == 'raw_antenna_reads'), None)
        if raw_root:
            roots.add(raw_root.parent)
    from deepecohab_recorder.recording_history import recording_runs
    seen_runs = set()
    for root in roots:
        for run in recording_runs(root, config):
            key = run.get('run_id') or json.dumps(run, sort_keys=True)
            if key in seen_runs:
                continue
            seen_runs.add(key)
            for device in run.get('devices', {}).values():
                for epoch in device.get('previous_epochs', []) + [device]:
                    filename = Path(epoch.get('data_file', '').replace('\\', '/')).name
                    history.setdefault(filename, []).append(epoch)
    for path in paths:
        fmt = detect(path)
        if fmt in ("unknown", "compressed"):
            if notices is not None:
                notices.append({"path": path, "format": fmt, "message": COMPRESSED_MESSAGE if fmt == "compressed" else "Unrecognized raw recording format."})
            continue
        with Path(path).open(encoding="utf-8-sig", errors="replace") as handle:
            first = next((line.strip() for line in handle if line.strip()), "")
        embedded = re.search(r"COM\d+", first.upper()) or re.search(r"COM\d+", Path(path).stem.upper())
        candidates = history.get(Path(path).name, [])
        meta = candidates[0] if len(candidates) == 1 else {}
        board = meta.get("config", {})
        _, suffix_board, _ = split_source(first.split(";", 1)[0])
        board_number = suffix_board or board.get("board_id")
        if fmt == "current":
            try:
                board_number = int(first.split(";")[4])
            except (ValueError, IndexError):
                pass
        if not board:
            matches = [
                b
                for b in boards
                if (embedded and b.get("com_port", "").upper() == embedded[0])
                or (board_number and b.get("board_id") == board_number)
            ]
            active = [b for b in boards if b.get("active", True) and b.get("com_port")]
            board = (
                matches[0]
                if len(matches) == 1
                else active[0] if len(active) == 1 else {}
            )
        port = embedded[0] if embedded else board.get("com_port", "")
        board_number = board_number or board.get("board_id", 1)
        hardware = board.get("hardware", "")
        profile = (
            fmt
            if fmt in ("old_decimal", "legacy_raw")
            else (
                "v1.5"
                if "1.5" in hardware
                else "old_hex" if hardware and "DeepEcoHAB" not in hardware else "v1.0"
            )
        )
        if fmt in ("raw", "current"):
            if profile_override:
                profile = profile_override
            elif profile != "old_hex":
                with Path(path).open(encoding="utf-8-sig", errors="replace") as probe:
                    channels = {raw_channel(line.split(";", 1)[0]) for _, line in zip(range(32), probe) if ";" in line}
                numeric = bool(channels & set("0123456789"))
                high_letters = bool(channels & set("GHIJKLMNOP"))
                if numeric and high_letters:
                    if notices is not None:
                        notices.append({"path": path, "format": fmt, "message": "Mixed v1.0/v1.5 channel alphabets; verify this source's hardware format."})
                    continue
                if high_letters:
                    profile = "v1.5"
                elif numeric:
                    profile = "v1.0"
        if fmt in ("legacy_raw", "old_decimal") and not embedded and not meta:
            # A connected/current board is not evidence of a historical port.
            port = ""
        spec = InputFile(
            str(Path(path).resolve()),
            port,
            board_number,
            profile,
            start=meta.get("start_datetime"),
            end=meta.get("end_datetime"),
            port_origin="recorded" if embedded else "metadata" if port else "unrecorded",
            stop_status=meta.get("stop_status", "unknown"),
            timing_basis="acquisition_journal" if meta else "unknown",
            naive_utc_offset=naive_utc_offset,
        )
        first_pc, last_pc, antennas = None, None, set()
        # Fast preview; full accounting and final ranges are determined by processing.
        preview_spec = spec
        unresolved_clock = False
        try:
            for index, (record, _, _) in enumerate(records(preview_spec, zone)):
                if record:
                    first_pc = record.pc_us if first_pc is None else first_pc
                    last_pc = record.pc_us
                    antennas.add(record.antenna)
                if index >= preview_limit - 1:
                    break
        except RawClockError as exc:
            if not allow_unresolved_clock:
                raise
            # Retain the source assignment so the exceptional clock option can
            # be reached. Inventory/processing still reject unresolved times.
            unresolved_clock = True
            if notices is not None:
                notices.append(dict(path=path, format="clock", message=str(exc)))
        if first_pc is None and not unresolved_clock:
            if notices is not None:
                notices.append({"path": path, "format": fmt, "message": "No valid rows in the preview; check hardware format and timestamp timezone."})
            continue  # A matching-looking header alone is not a usable raw input.
        found.append(
            {
                "input": spec,
                "format": fmt,
                "embedded_port": embedded[0] if embedded else "",
                "embedded_board": board_number if fmt == "current" or suffix_board else None,
                "first_pc_us": first_pc,
                "preview_last_pc_us": last_pc,
                "antennas": sorted(antennas),
            }
        )
    found.sort(
        key=lambda item: (
            item["first_pc_us"] is None,
            item["first_pc_us"] or 0,
            item["input"].path,
        )
    )
    for item in found:
        key = item["input"].board_id
        counts[key] = counts.get(key, 0) + 1
        item["input"].group = "1"
        item["input"].part = counts[key]
    return found
