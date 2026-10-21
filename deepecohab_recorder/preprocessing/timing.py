"""One time model before RFID/TTL routing; counter epochs remain explicit."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from statistics import median
from bisect import bisect_right
import hashlib
from pathlib import Path

from .recording_gaps import recording_boundaries, observed_file_gaps, missed_data_periods, rotation_boundaries, recording_coverage, acquisition_journals, attach_journal_evidence
from .models import Cancelled, iso, parse_pc
from .readers import records


def file_hash(path, cancelled=lambda: False):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            if cancelled():
                raise Cancelled()
            digest.update(block)
    return digest.hexdigest()


class RecordStream:
    """Small look-ahead distinguishes an isolated late record from a reset."""
    def __init__(self, source):
        self.source, self.pending = iter(source), deque()

    def __iter__(self):
        return self

    def __next__(self):
        return self.pending.popleft() if self.pending else next(self.source)

    def following_ticks(self):
        while len(self.pending) < 16:
            try:
                self.pending.append(next(self.source))
            except StopIteration:
                break
        return [record.ticks for record, error, _ in self.pending if not error and record and record.ticks is not None][:4]


@dataclass
class Epoch:
    id: int
    port: str
    board: int
    group: str
    first_pc: int
    first_ticks: int | None
    profile: str
    tick_ms: float | None
    counter_bits: int
    last_ticks: int | None = None
    last_pc: int = 0
    wraps: int = 0
    unwrap: int = 0
    points: list = field(default_factory=list)
    start_known: int | None = None
    end_known: int | None = None
    scale_source: str = ""
    files: list = field(default_factory=list)
    reason: str = "file_group"

    def point(self, rel_ticks, pc):
        if rel_ticks is not None and (
            not self.points or pc - self.points[-1][1] >= 1_000_000
        ):
            self.points.append((rel_ticks, pc))
            if len(self.points) > 512:
                self.points = self.points[::2]

    def scale(self):
        if self.first_ticks is None:
            self.scale_source = "unavailable_in_legacy_raw_input_PC_clock_used"
            return None
        if self.tick_ms is not None:
            self.scale_source = "explicit_ms_per_tick"
            return self.tick_ms
        if self.profile in ("v1.0", "v1.5"):
            self.scale_source = "hardware_profile_10ms_per_tick"
            return 10.0
        end = (self.last_ticks + self.unwrap - self.first_ticks, self.last_pc)
        points = self.points + (
            [end] if not self.points or self.points[-1] != end else []
        )
        # Long-baseline ratios reduce USB scheduling noise. Do not use adjacent raw reads.
        candidates = []
        for index, a in enumerate(points):
            for b in points[index + 1 :]:
                ticks, pc = b[0] - a[0], (b[1] - a[1]) / 1000
                if ticks > 0 and pc >= 5000:
                    candidates.append(pc / ticks)
        if not candidates and len(points) >= 2:
            a, b = points[0], points[-1]
            if b[0] > a[0] and b[1] > a[1]:
                candidates = [(b[1] - a[1]) / 1000 / (b[0] - a[0])]
        candidates = [x for x in candidates if 0.000001 <= x <= 10000]
        if not candidates:
            raise ValueError(
                f"Cannot infer counter units for {self.port}, group {self.group}. Supply ms/tick in the input table's advanced settings."
            )
        self.scale_source = "estimated_from_PC_time_not_verified_firmware_units"
        return median(candidates)


def ingest(db, settings, diagnostics, progress, cancelled, config=None):
    epochs, current, hashes, fingerprints = [], {}, {}, {}
    evidence, warnings = acquisition_journals(Path(settings.config_path).resolve().parent, settings.timezone, config) if settings.config_path else ([], [])
    attach_journal_evidence(settings.inputs, evidence)
    diagnostics.info['acquisition_journal_warnings'] = warnings
    files = []
    batch = []
    reported_pc_offsets = set()
    insert = "INSERT INTO raw(epoch,file,line,antenna,tag,pc,rel_ticks,counter,kind,legacy_duration,part) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
    for file_index, spec in enumerate(settings.inputs):
        if cancelled():
            raise Cancelled()
        before = Path(spec.path).stat()
        digest = file_hash(spec.path, cancelled)
        hash_key = (spec.board_id, digest)
        if hash_key in hashes:
            diagnostics.write(
                "duplicate_file_skipped",
                path=spec.path,
                duplicate_of=hashes[hash_key],
            )
            diagnostics.count("duplicate_files_skipped")
            continue
        hashes[hash_key] = spec.path
        if spec.naive_utc_offset:
            source_path = spec.source_path or spec.path
            choice = (source_path, spec.board_id, spec.naive_utc_offset)
            if choice not in reported_pc_offsets:
                reported_pc_offsets.add(choice)
                diagnostics.write("raw_pc_clock_override", source=Path(source_path).name,
                    board=spec.board_id, utc_offset=spec.naive_utc_offset,
                    message="User-declared fixed offset for offset-free raw times in this source. Recorded explicit offsets remain authoritative; the experiment timezone is unchanged.")
        files.append(
            {
                "id": file_index,
                "path": spec.path,
                "bytes": before.st_size,
                "port": spec.port,
                "board_id": spec.board_id,
                "clock_group": spec.group,
                "recording_part": spec.part,
                "rotation_group": spec.rotation_group,
                "source_path": spec.source_path, "source_kind": spec.source_kind,
                "port_origin": spec.port_origin,
                "profile": spec.profile,
                "timing_basis": spec.timing_basis,
                "stop_status": spec.stop_status,
            }
        )
        key = (spec.board_id, spec.group)
        fingerprint = (spec.profile, spec.tick_ms, spec.counter_bits)
        if key in fingerprints and fingerprints[key] != fingerprint:
            raise ValueError(
                f"Continuous files on board {spec.board_id}, group {spec.group}, have different clock profiles."
            )
        fingerprints[key] = fingerprint
        epoch = current.get(key)
        valid_in_file = 0
        stream = RecordStream(records(spec, settings.timezone))
        file_first_pc, file_last_pc = None, None
        for record, error, text in stream:
            diagnostics.count("input_read_rows")
            if diagnostics.counts["input_read_rows"] % 10000 == 0 and cancelled():
                raise Cancelled()
            if error:
                diagnostics.count("malformed_rows")
                diagnostics.write(
                    "malformed_record", file=spec.path, error=error, text=text[:2000],
                )
                continue
            valid_in_file += 1
            file_first_pc = record.pc_us if file_first_pc is None else min(file_first_pc, record.pc_us)
            file_last_pc = record.pc_us if file_last_pc is None else max(file_last_pc, record.pc_us)
            boundary_reason = None
            if epoch and valid_in_file == 1:
                if spec.start and epoch.start_known is not None and parse_pc(spec.start, settings.timezone) != epoch.start_known:
                    boundary_reason = 'acquisition_journal_start'
                elif epoch.port and spec.port and epoch.port != spec.port:
                    boundary_reason = 'connection_changed_PC_reanchor'
            restart = bool(boundary_reason)
            late = False
            if (
                epoch
                and not restart
                and record.ticks is not None
                and epoch.last_ticks is not None
                and record.ticks < epoch.last_ticks
            ):
                modulus = 2**spec.counter_bits if spec.counter_bits else 0
                scale = spec.tick_ms or (
                    10 if spec.profile in ("v1.0", "v1.5") else None
                )
                delta = record.ticks + modulus - epoch.last_ticks
                pc_delta = (record.pc_us - epoch.last_pc) / 1000
                verified_wrap = (
                    modulus
                    and scale
                    and epoch.last_ticks > modulus * 0.9
                    and record.ticks < modulus * 0.1
                    and pc_delta >= 0
                    and abs(delta * scale - pc_delta) <= max(2000, pc_delta * 0.1)
                )
                if verified_wrap:
                    epoch.unwrap += modulus
                    epoch.wraps += 1
                    diagnostics.count("counter_rollovers")
                else:
                    if scale is None and epoch.last_ticks > epoch.first_ticks and epoch.last_pc > epoch.first_pc:
                        scale = (epoch.last_pc - epoch.first_pc) / 1000 / (epoch.last_ticks - epoch.first_ticks)
                    backwards_ms = (epoch.last_ticks - record.ticks) * scale if scale else None
                    # At the beginning of a new recording file a backwards
                    # counter may be a real reinitialization, even if small.
                    new_recording = bool(epoch.files) and valid_in_file == 1 and spec.part != settings.inputs[epoch.files[-1]].part
                    nearby = backwards_ms is not None and backwards_ms <= settings.counter_reorder_ms
                    following = stream.following_ticks() if not new_recording else []
                    restored = any(ticks >= epoch.last_ticks for ticks in following)
                    # A new, sustained increasing sequence below the old counter
                    # is a discontinuity even when its first drop was small.
                    sequence = [record.ticks, *following]
                    sustained = len(following) == 4 and not restored and following[-1] > record.ticks and all(a <= b for a, b in zip(sequence, sequence[1:]))
                    late = not new_recording and (restored or (nearby and not sustained))
                    if late:
                        diagnostics.count("out_of_order_counter_reads")
                        diagnostics.write("counter_read_reordered", file=spec.path, line=record.line, counter=record.counter, backwards_ms=backwards_ms)
                    else:
                        restart = True
                        diagnostics.count("counter_resets_or_unverified_rollovers")
            if epoch is None or restart:
                epoch = Epoch(
                    len(epochs) + 1,
                    spec.port,
                    spec.board_id,
                    spec.group,
                    record.pc_us,
                    record.ticks,
                    spec.profile,
                    spec.tick_ms,
                    spec.counter_bits,
                    last_pc=record.pc_us,
                    reason=boundary_reason or (
                        "counter_reset_or_unverified_rollover"
                        if restart
                        else "file_group"
                    ),
                )
                if (not restart or boundary_reason) and spec.start:
                    epoch.start_known = parse_pc(spec.start, settings.timezone)
                epochs.append(epoch)
                current[key] = epoch
            if epoch.profile != spec.profile:
                raise ValueError("A clock group mixes incompatible hardware profiles.")
            if record.pc_us < epoch.last_pc:
                diagnostics.count("PC_timestamp_reversals")
            if file_index not in epoch.files:
                epoch.files.append(file_index)
            if spec.end and (spec.timing_basis != "acquisition_journal" or spec.stop_status == "clean"):
                epoch.end_known = parse_pc(spec.end, settings.timezone)
            relative = (
                None
                if record.ticks is None
                else record.ticks + epoch.unwrap - epoch.first_ticks
            )
            if relative is not None and not -(2**63) < relative < 2**63:
                raise ValueError("Counter elapsed value exceeds the supported range.")
            if not late:
                epoch.point(relative, record.pc_us)
                epoch.last_ticks = record.ticks
            epoch.last_pc = record.pc_us
            batch.append(
                (
                    epoch.id,
                    file_index,
                    record.line,
                    record.antenna,
                    record.tag,
                    record.pc_us,
                    relative,
                    record.counter,
                    record.kind,
                    record.duration_ms,
                    spec.part,
                )
            )
            if len(batch) >= 10000:
                db.executemany(insert, batch)
                batch.clear()
                if cancelled():
                    raise Cancelled()
                progress(
                    5 + int(30 * file_index / len(settings.inputs)),
                    f"Reading {Path(spec.path).name}: {valid_in_file:,} records",
                )
        if not valid_in_file:
            raise ValueError(
                f"No valid records in {Path(spec.path).name}; check the profile, board assignment and timezone."
            )
        files[-1].update(first_pc_us=file_first_pc, last_pc_us=file_last_pc,
                         recording_start=spec.start, recording_end=spec.end,
                         valid_reads=valid_in_file)
        after = Path(spec.path).stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(
                f"Input changed during processing: {spec.path}. Process a completed recording or a stable copy."
            )
        progress(
            5 + int(30 * (file_index + 1) / len(settings.inputs)),
            f"Read {Path(spec.path).name}",
        )
    db.executemany(insert, batch)
    db.commit()
    if not epochs:
        raise ValueError("No usable clock periods were found.")
    reference = min(
        (e for e in epochs if e.board == settings.master_board),
        key=lambda e: e.first_pc,
        default=None,
    )
    if reference is None:
        raise ValueError("The reference board has no valid records.")
    origin = reference.first_pc
    diagnostics.info.update(
        origin_us=origin,
        origin_datetime=iso(origin, settings.timezone),
        reference_board=settings.master_board,
        reference_epoch=reference.id,
        files=files,
        clock_alignment="PC-anchored between independent boards; no shared-TTL synchronization was assumed",
        missing_board_clock_policy="Vanilla legacy raw data uses the PC clock for all timing; internal_board_timestamp is null. Logical board numbers identify sources only, never hardware synchronization.",
    )
    report_epochs = []
    for epoch in epochs:
        scale = epoch.scale()
        if scale is not None:
            # Arithmetic uses elapsed ticks, never absolute 64-bit counters in a float.
            db.execute(
                "UPDATE raw SET board_time=?+CAST(ROUND(rel_ticks*?) AS INTEGER) WHERE epoch=?",
                (epoch.first_pc, scale * 1000, epoch.id),
            )
        row = db.execute(
            "SELECT MIN(pc) AS first_pc,MAX(pc) AS last_pc,COUNT(*) AS reads,MIN(board_time-pc) AS minimum_clock_difference_us,MAX(board_time-pc) AS maximum_clock_difference_us FROM raw WHERE epoch=?",
            (epoch.id,),
        ).fetchone()
        report_epochs.append(
            {
                "id": epoch.id,
                "port": epoch.port,
                "board_id": epoch.board,
                "group": epoch.group,
                "reason": epoch.reason,
                "first_counter": (
                    str(epoch.first_ticks) if epoch.first_ticks is not None else None
                ),
                "anchor_datetime": iso(epoch.first_pc, settings.timezone),
                "offset_from_reference_ms": (epoch.first_pc - origin) / 1000,
                "tick_ms": scale,
                "scale_source": epoch.scale_source,
                "wraps": epoch.wraps,
                "first_record": iso(row["first_pc"], settings.timezone),
                "last_record": iso(row["last_pc"], settings.timezone),
                "recording_start": iso(epoch.start_known, settings.timezone),
                "recording_end": iso(epoch.end_known, settings.timezone),
                "files": epoch.files,
                "reads": row["reads"],
                "minimum_PC_board_difference_ms": (
                    None if scale is None else row["minimum_clock_difference_us"] / 1000
                ),
                "maximum_PC_board_difference_ms": (
                    None if scale is None else row["maximum_clock_difference_us"] / 1000
                ),
            }
        )
    # Clock epochs describe timing, not output-file boundaries. Only input
    # recording parts determine output parts; a counter reversal cannot create
    # another output file. Preserve every timing discontinuity in diagnostics.
    part_starts = {}
    for source in files:
        part = source["recording_part"]
        part_starts[part] = min(part_starts.get(part, source["first_pc_us"]), source["first_pc_us"])
    boundaries = sorted(part_starts[part] for part in sorted(part_starts)[1:])
    minimum_gap = settings.minimum_recording_gap_seconds if settings.ignore_short_recording_gaps else 0
    reported_boundaries = recording_boundaries(files, settings.timezone, minimum_gap)
    diagnostics.info["recording_boundaries"] = reported_boundaries
    for boundary in reported_boundaries:
        diagnostics.write("recording_boundary", **boundary)
    diagnostics.info["recording_boundary_policy"] = {
        "ignore_short_gaps": settings.ignore_short_recording_gaps,
        "minimum_gap_seconds": minimum_gap,
        "configured_minimum_gap_seconds": settings.minimum_recording_gap_seconds,
        "threshold_comparison": "positive gap >= minimum_gap_seconds",
        "suppressed_short_gaps": sum(1 for _ in observed_file_gaps(files)) - len(reported_boundaries),
        "scope": "Positive gaps in the union of observed file ranges, separately for each board; all qualifying entries are in recording_boundaries, examples.recording_boundary is a preview.",
        "preserved_evidence": "Source bounds, declared file rotations, clock resets and confirmed journal interruptions are retained regardless of this reporting threshold.",
        "changes_processing": False,
    }
    gaps = missed_data_periods(files, settings.timezone, minimum_gap) if settings.flag_missed_data_periods else []
    diagnostics.info["missed_data_periods"] = gaps
    rotations = rotation_boundaries(files, settings.timezone)
    diagnostics.info["routine_file_boundaries"] = rotations
    diagnostics.count("routine_file_boundaries", len(rotations))
    diagnostics.info["missed_data_policy"] = {
        "enabled": settings.flag_missed_data_periods,
        "minimum_gap_seconds": minimum_gap,
        "scope": "per_logical_board",
        "bounds": "Last valid record in preceding coverage to first valid record in next file; not confirmed failure times.",
        "overlapping_files": "Merged as coverage; no positive gap is created inside overlapping ranges.",
        "routine_rotation": "Files in the same declared continuous folder/source and recording part are listed separately in routine_file_boundaries. Continuity is user-declared, not proof that no outage occurred.",
    }
    diagnostics.count("missed_data_periods", len(gaps))
    diagnostics.info["recording_coverage"] = recording_coverage(files, settings.timezone, evidence, minimum_gap)
    diagnostics.info["clock_epochs"] = report_epochs
    diagnostics.info["segment_boundaries"] = [iso(b, settings.timezone) for b in boundaries]
    diagnostics.info["recording_parts"] = [{"part": part, "first_record": iso(start, settings.timezone)} for part, start in sorted(part_starts.items())]
    diagnostics.info["segmentation_policy"] = "Input recording parts; clock epochs do not create output files"
    diagnostics.count("clock_epochs", len(epochs))
    diagnostics.count("recording_parts", len(part_starts))
    db.commit()
    return epochs, boundaries


def part_for(value, boundaries):
    return bisect_right(boundaries, value) + 1
