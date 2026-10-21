from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import math

from deepecohab_recorder.clock import ExperimentClock
from .raw_clock import RawClockError, fixed_utc_offset

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PROFILES = ("v1.0", "v1.5", "legacy_raw", "old_decimal", "old_hex")
PROFILE_LABELS = {
    "v1.0": "RFID DAQ v1.0",
    "v1.5": "RFID DAQ v1.5",
    "legacy_raw": "Legacy raw — PC clock only",
    "old_decimal": "Legacy + new firmware (CSV)",
    "old_hex": "Legacy firmware (hex counter)",
}
COMPRESSED_MESSAGE = (
    "This file contains previously preprocessed legacy data. Its original raw detections cannot be recovered from these durations. "
    "Select the original raw files containing timestamp, RFID tag and antenna number."
)


def normalize_interpolation_mode(mode):
    """Map the superseded Patch 7 option names to the agreed Patch 8 policy."""
    mode = {"guaranteed": "standard", "plausible": "extended"}.get(mode, mode)
    if mode not in ("none", "standard", "extended"):
        raise ValueError("Interpolation mode must be none, standard, or extended.")
    return mode


def parse_pc(text: str, zone: str, naive_utc_offset: str = "") -> int:
    text = re.sub(r"(\d{2}:\d{2}:\d{2}):(\d+)$", r"\1.\2", text.strip())
    clock = ExperimentClock(zone)
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    offset = fixed_utc_offset(naive_utc_offset)
    if value.tzinfo is None and offset is not None:
        value = value.replace(tzinfo=offset)
    elif value.tzinfo is None:
        try:
            value = clock.parse(text, allow_naive=True)
        except ValueError as exc:
            if "Ambiguous or nonexistent local time" in str(exc):
                raise RawClockError(
                    str(exc) + " Only for a verified fixed recording clock: open Advanced options → Board clocks "
                    "and enable Exceptional fixed PC-clock offsets to declare a known offset "
                    "only if the recording PC kept that offset throughout this source."
                ) from exc
            raise
    value = value.astimezone(UTC)
    delta = value - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def iso(value: int | None, zone: str) -> str | None:
    if value is None:
        return None
    return (
        (EPOCH + timedelta(microseconds=int(value)))
        .astimezone(ExperimentClock(zone).zone)
        .isoformat(timespec="microseconds")
    )


@dataclass
class InputFile:
    path: str
    port: str = ""
    board_id: int = 1
    profile: str = "v1.0"
    group: str = "1"
    tick_ms: float | None = None
    counter_bits: int = 0
    start: str | None = None
    end: str | None = None
    part: int | None = None
    rotation_group: str = ""
    source_path: str = ""
    source_kind: str = "file"
    port_origin: str = "assigned"
    stop_status: str = "unknown"
    timing_basis: str = "unknown"
    naive_utc_offset: str = ""

    def validate(self):
        fixed_utc_offset(self.naive_utc_offset)
        if self.profile == "compressed":
            raise ValueError(COMPRESSED_MESSAGE)
        if not Path(self.path).is_file():
            raise ValueError(f"Input file is unavailable: {self.path}")
        self.port = self.port.strip().upper()
        if self.profile not in PROFILES or (type(self.board_id) is not int or self.board_id < 1) or not self.group.strip():
            raise ValueError(
                "Each input needs a hardware profile, positive board number and clock group."
            )
        if self.tick_ms is not None and not 0 < self.tick_ms <= 10000:
            raise ValueError("Counter scale must be positive milliseconds per unit.")
        if self.counter_bits not in (0, 16, 24, 32, 40, 48, 64):
            raise ValueError(
                "Counter width must be 0 (unknown) or a supported unsigned width."
            )
        if self.part is not None and (not isinstance(self.part, int) or not 1 <= self.part <= 10000):
            raise ValueError("Recording part must be a positive number (1–10000).")
        if self.profile == "legacy_raw" and (self.tick_ms is not None or self.counter_bits):
            raise ValueError("Vanilla legacy raw data has no board counter. Leave milliseconds per tick blank and counter bits at 0.")
        if not isinstance(self.rotation_group, str):
            raise ValueError("A continuous source group must be a string.")


@dataclass
class Settings:
    inputs: list[InputFile]
    output_dir: str
    config_path: str = ""
    timezone: str = "Europe/Warsaw"
    master_board: int = 1
    window_ms: int = 300
    minimum_ms: int = 10
    parquet: bool = True
    csv: bool = False
    whitelist: bool = True
    interpolate: bool = True
    split_restarts: bool = False
    minimum_transits: int = 3
    cohort_fallback: bool = False
    aliases: dict[str, str] = field(default_factory=dict)
    routes: dict[str, str] = field(default_factory=dict)
    ttl_routes: dict[str, str] = field(default_factory=dict)
    device_groups: dict[str, str] = field(default_factory=dict)
    counter_reorder_ms: int = 2000
    interpolation_mode: str = "standard"
    group_windows_ms: dict[str, int] = field(default_factory=dict)
    flag_missed_data_periods: bool = True
    ignore_short_recording_gaps: bool = True
    minimum_recording_gap_seconds: float = 180.0
    allow_incomplete_metadata: bool = False

    @property
    def effective_interpolation_mode(self):
        # Preserve the old worker/API switch when loading existing settings.
        return normalize_interpolation_mode(self.interpolation_mode) if self.interpolate else "none"

    def validate(self):
        self.interpolation_mode = normalize_interpolation_mode(self.interpolation_mode)
        if not isinstance(self.flag_missed_data_periods, bool):
            raise ValueError("Flag missed data periods must be true or false.")
        if not isinstance(self.ignore_short_recording_gaps, bool):
            raise ValueError("Ignore short recording gaps must be true or false.")
        gap = self.minimum_recording_gap_seconds
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap) or gap < 0:
            raise ValueError("Minimum recording gap must be a finite, nonnegative number of seconds.")
        for group, window in self.group_windows_ms.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", group) or group.casefold() in ("main", "rfid", "ttl"):
                raise ValueError("Group stitching overrides require a named non-main RFID output group.")
            if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 60000:
                raise ValueError("Group stitching windows must be whole milliseconds between 1 and 60000.")
        if not self.inputs:
            raise ValueError("Select at least one input file.")
        if not (self.parquet or self.csv):
            raise ValueError("Select Parquet, CSV, or both.")
        if self.window_ms < 1 or self.minimum_ms < 1 or self.minimum_transits < 1:
            raise ValueError("Durations and minimum transit count must be positive.")
        ExperimentClock(self.timezone)
        if not 0 <= self.counter_reorder_ms <= 60000:
            raise ValueError("Counter reordering tolerance must be between 0 and 60000 ms.")
        seen = set()
        boards = set()
        parts_per_port = {}
        rotation_parts = {}
        for item in self.inputs:
            item.validate()
            parts_per_port[item.board_id] = parts_per_port.get(item.board_id, 0) + 1
            if item.part is None:
                item.part = parts_per_port[item.board_id]
            if item.profile == "legacy_raw" and not self.split_restarts and not item.rotation_group:
                # Explicitly merging loose legacy hourly files also removes
                # their artificial part boundaries from movement inference.
                item.rotation_group = f"legacy_hourly:{item.board_id}:{item.group}"
                item.part = 1
            if item.rotation_group:
                key = (item.board_id, item.group, item.rotation_group)
                if key in rotation_parts:
                    item.part = rotation_parts[key]
                else:
                    rotation_parts[key] = item.part
            path = str(Path(item.path).resolve())
            if path in seen:
                raise ValueError(f"The same input file is selected twice: {path}")
            seen.add(path)
            boards.add(item.board_id)
        # Selecting just an old part or removing inputs must not leave empty
        # output parts. One logical part can contain files from several boards.
        numbers = {part: index for index, part in enumerate(sorted({i.part for i in self.inputs}), 1)}
        for item in self.inputs:
            item.part = numbers[item.part]
        if self.master_board not in boards:
            raise ValueError(
                "The reference board must have at least one selected input file."
            )
        # Saved Patch 11 routing/aliases use COM suffixes. Resolve them only
        # against explicit input assignments, before any stream is classified.
        from deepecohab_recorder.source_identity import canonical_source, split_source, canonical_alias
        catalogue = {str(i): dict(board_id=s.board_id, com_port=s.port) for i, s in enumerate(self.inputs)}
        for field_name in ('aliases', 'routes', 'ttl_routes'):
            converted = {}
            for source, value in getattr(self, field_name).items():
                name = canonical_source(source, boards=catalogue)
                target = canonical_alias(value, catalogue) if field_name == 'aliases' else value
                if split_source(name)[2] or (field_name == 'aliases' and split_source(target)[2]):
                    raise ValueError('Cannot resolve a legacy COM suffix to one board. Reassign it using antenna-board names in Advanced options.')
                if name in converted and converted[name] != target:
                    raise ValueError(f'Conflicting {field_name} for {name}.')
                converted[name] = target
            setattr(self, field_name, converted)
        for source, alias in self.aliases.items():
            if not source or not alias.strip() or any(c in alias for c in "\r\n\t"):
                raise ValueError("Antenna aliases must be nonempty, single-line names.")
            if not re.fullmatch(r'[A-Za-z0-9_-]+-[1-9]\d*', alias.strip()):
                raise ValueError('Every output antenna must include a board suffix. Use an alias such as lick_port-1, or clear the alias to keep its physical antenna name.')
        for route in self.routes.values():
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", route)
                or route.lower() == "ttl"
            ):
                raise ValueError(
                    "Output groups use letters, numbers, underscores or hyphens; 'ttl' is reserved."
                )
        group_names = {"rfid": "rfid", "auxiliary": "auxiliary"}
        for route in self.routes.values():
            if route == "main":
                continue
            key = route.casefold()
            if key in group_names and group_names[key] != route:
                raise ValueError(
                    "Output group names must not differ only in letter case on Windows: "
                    f"{group_names[key]} and {route}."
                )
            group_names[key] = route

        from .output_plan import normalize_groups
        normalized = normalize_groups({'routes': self.routes, 'ttl_routes': self.ttl_routes, 'group_windows_ms': self.group_windows_ms, 'device_groups': self.device_groups})
        self.routes, self.ttl_routes, self.group_windows_ms = normalized['routes'], normalized['ttl_routes'], normalized['group_windows_ms']
        for source, group in self.ttl_routes.items():
            if not re.fullmatch(r'TTL[1-4]-[1-9]\d*', source) or not re.fullmatch(r'(?:TTL_data|device_antenna)[1-9]\d*', group):
                raise ValueError('TTL inputs require a numbered TTL_data or device_antenna group.')
        if any(source.startswith('TTL') for source in self.routes):
            raise ValueError('Assign TTL inputs through TTL grouping; they cannot enter main movement data.')

    def to_dict(self):
        data = asdict(self)
        if not data["allow_incomplete_metadata"]:
            del data["allow_incomplete_metadata"]
        for spec in data["inputs"]:
            if not spec["naive_utc_offset"]:
                del spec["naive_utc_offset"]
        return data

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["interpolation_mode"] = normalize_interpolation_mode(data.get("interpolation_mode", "standard"))
        data["inputs"] = [InputFile(**item) for item in data["inputs"]]
        return cls(**data)


@dataclass
class RawRecord:
    line: int
    antenna: str
    tag: str | None
    pc_us: int
    ticks: int | None
    counter: str
    kind: str = "rfid"
    duration_ms: int | None = None


class Cancelled(Exception):
    pass
