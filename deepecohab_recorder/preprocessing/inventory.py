"""Discover observed RFID antennas without retaining raw rows in memory."""

from pathlib import Path

from deepecohab_recorder.clock import ExperimentClock
from .models import Cancelled
from .readers import records


def input_signature(inputs, zone):
    """Invalidate the small UI cache when a file or parsing assignment changes."""
    result = []
    for spec in inputs:
        spec.validate()
        path = Path(spec.path).resolve()
        stat = path.stat()
        result.append((str(path), stat.st_size, stat.st_mtime_ns,
                       spec.board_id, spec.port, spec.profile, spec.naive_utc_offset))
    return zone, tuple(result)


def inventory(inputs, zone, progress=lambda value, text: None, cancelled=lambda: False):
    ExperimentClock(zone)
    if not inputs:
        raise ValueError("Add completed recording files before managing antenna groups.")
    signature = input_signature(inputs, zone)
    antennas, ttl_inputs = set(), set()
    counts = {"rfid_reads": 0, "ttl_reads": 0, "malformed_rows": 0}
    for index, spec in enumerate(inputs):
        if cancelled():
            raise Cancelled("Antenna scan cancelled.")
        progress(round(100 * index / len(inputs)), f"Finding used antennas: {Path(spec.path).name}")
        for number, (record, error, _) in enumerate(records(spec, zone), 1):
            if record is None:
                counts["malformed_rows"] += 1
            elif record.kind == "ttl":
                counts["ttl_reads"] += 1
                ttl_inputs.add(record.antenna)
            else:
                antennas.add(record.antenna)
                counts["rfid_reads"] += 1
            if number % 10000 == 0:
                if cancelled():
                    raise Cancelled("Antenna scan cancelled.")
                if number % 100000 == 0:
                    progress(round(100 * index / len(inputs)), f"{Path(spec.path).name}: {number:,} records scanned")
    if cancelled():
        raise Cancelled("Antenna scan cancelled.")
    if signature != input_signature(inputs, zone):
        raise ValueError("An input file changed during the antenna scan. Use completed recordings and scan again.")
    progress(100, f"Found {len(antennas)} used RFID antennas in {len(inputs)} complete files.")
    return {"antennas": sorted(antennas), "ttl_inputs": sorted(ttl_inputs), "files_scanned": len(inputs), **counts}
