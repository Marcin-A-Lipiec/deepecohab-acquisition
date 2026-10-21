"""Antenna-local state, preserving interruptions by another real tag."""

from functools import lru_cache
from .models import Cancelled
from .routing import stitch_window_ms
from .timing import part_for

PASS_COLUMNS = (
    "epoch",
    "antenna",
    "tag",
    "pc_start",
    "pc_end",
    "board_start",
    "board_end",
    "duration",
    "reads",
    "file_start",
    "line_start",
    "file_end",
    "line_end",
    "kind",
    "part",
    "interpolated",
    "detail",
)
INSERT_PASS = f"INSERT INTO passes({','.join(PASS_COLUMNS)}) VALUES({','.join('?' for _ in PASS_COLUMNS)})"


def insert_pass(db, values):
    return db.execute(INSERT_PASS, [values.get(k) for k in PASS_COLUMNS]).lastrowid


def stitch(db, settings, boundaries, diagnostics, cancelled, progress):
    current = None
    count = 0
    seen = 0

    @lru_cache(maxsize=None)
    def window_us(antenna):
        return stitch_window_ms(antenna, settings) * 1000

    def finish():
        nonlocal current, count
        if current is None:
            return
        has_counter = current["board_start"] is not None
        measured = ((current["board_end"] - current["board_start"]) if has_counter
                    else (current["pc_end"] - current["pc_start"])) / 1000
        current["duration"] = (
            max(1, round(measured)) if measured > 0 else settings.minimum_ms
        )
        current["detail"] = (
            "minimum_duration_assumption" if measured <= 0 else
            "observed_counter_span" if has_counter else "observed_PC_span_no_board_clock"
        )
        if measured <= 0:
            diagnostics.count("RFID_minimum_durations_assigned")
        insert_pass(db, current)
        count += 1
        current = None

    total = db.execute("SELECT COUNT(*) FROM raw WHERE accepted=1").fetchone()[0]
    # Sort each physical antenna by the reconstructed counter, not USB arrival
    # order. Other antennas and TTLs do not interrupt its stitching window.
    for row in db.execute(
        "SELECT * FROM raw WHERE accepted=1 ORDER BY epoch,antenna,COALESCE(board_time,pc),id"
    ):
        seen += 1
        if seen % 10000 == 0:
            if cancelled():
                raise Cancelled()
            progress(
                40 + int(20 * seen / max(1, total)),
                f"Stitching: {seen:,} / {total:,} reads",
            )
        start = {
            "epoch": row["epoch"],
            "antenna": row["antenna"],
            "tag": row["tag"],
            "pc_start": row["pc"],
            "pc_end": row["pc"],
            "board_start": row["board_time"],
            "board_end": row["board_time"],
            "duration": None,
            "reads": 1,
            "file_start": row["file"],
            "line_start": row["line"],
            "file_end": row["file"],
            "line_end": row["line"],
            "kind": row["kind"],
            "part": row["part"],
            "interpolated": 0,
            "detail": "",
        }
        if row["kind"] in ("ttl", "legacy"):
            finish()
            if row["kind"] == "ttl":
                start["duration"] = 0
                start["detail"] = "TTL_rising_edge_point_event;0_is_not_a_measured_pulse_width"
            else:
                start["reads"] = None
                duration = row["legacy_duration"]
                start["duration"] = duration if duration > 0 else settings.minimum_ms
                start["pc_end"] += (
                    duration * 1000
                )  # Actual historical span, before the zero-duration floor.
                start["detail"] = "legacy_compressed_counter_unavailable" + (
                    ";minimum_duration_assumption" if duration == 0 else ""
                )
                if duration == 0:
                    diagnostics.count("RFID_minimum_durations_assigned")
            insert_pass(db, start)
            count += 1
            continue
        if current is not None:
            event_time = row["board_time"] if row["board_time"] is not None else row["pc"]
            last_time = current["board_end"] if current["board_end"] is not None else current["pc_end"]
            gap = event_time - last_time
            same = (row["epoch"], row["antenna"], row["tag"]) == (
                current["epoch"],
                current["antenna"],
                current["tag"],
            )
            same_part = current["part"] == row["part"] or not settings.split_restarts
            if same and same_part and 0 <= gap < window_us(row["antenna"]):
                current.update(
                    pc_start=min(current["pc_start"], row["pc"]),
                    pc_end=max(current["pc_end"], row["pc"]),
                    board_end=row["board_time"],
                    reads=current["reads"] + 1,
                    file_end=row["file"],
                    line_end=row["line"],
                )
                continue
            finish()
        current = start
    finish()
    db.commit()
    diagnostics.count(
        "observed_RFID_periods",
        db.execute("SELECT COUNT(*) FROM passes WHERE kind!='ttl'").fetchone()[0],
    )
    diagnostics.count(
        "TTL_events",
        db.execute("SELECT COUNT(*) FROM passes WHERE kind='ttl'").fetchone()[0],
    )
    progress(60, f"Created {count:,} observed output records")
