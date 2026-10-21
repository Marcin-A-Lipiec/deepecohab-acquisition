"""Data columns plus a Boolean interpolation flag; detailed provenance stays in diagnostics."""

from __future__ import annotations

import csv
from datetime import timedelta
import json

from . import COLUMNS, PARQUET_COLUMNS
from .models import Cancelled, EPOCH, iso
from .timing import file_hash, part_for
from .output_plan import data_stem
from .routing import route_output_rows


def export(
    db, directory, settings, graph, boundaries, diagnostics, cancelled, progress
):
    if settings.parquet:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ValueError(
                'Parquet output requires pyarrow. In your Anaconda environment run: python -m pip install "pyarrow>=15,<22"'
            ) from exc
        categories = sorted(set(diagnostics.info.get('configured_animal_tags', [])) |
                            {row[0] for row in db.execute('SELECT DISTINCT tag FROM passes WHERE tag IS NOT NULL')})
        category_index = {tag: i for i, tag in enumerate(categories)}
        category_values = pa.array(categories, type=pa.string())
        enum_metadata = {b'_PL_ENUM_VALUES2': ''.join(f'{len(tag.encode("utf-8"))};{tag}' for tag in categories).encode('utf-8')}
        schema = pa.schema(
            [
                pa.field("datetime", pa.timestamp("us", tz=settings.timezone)),
                pa.field("antenna", pa.dictionary(pa.uint32(), pa.string())),
                pa.field("time_under", pa.duration('us')),
                pa.field("animal_id", pa.dictionary(pa.uint32(), pa.string(), ordered=True), metadata=enum_metadata),
                pa.field(
                    "internal_board_timestamp", pa.timestamp("us", tz=settings.timezone)
                ),
                pa.field("inserted", pa.bool_(), nullable=False),
            ],
            metadata={
                b"time_under_unit": b"microseconds",
                b"diagnostics": diagnostics.info.get("diagnostic_relative_to_data", diagnostics.filename).encode("utf-8"),
                b"clock_alignment": b"PC-anchored estimates between independent board clocks",
                b"preprocessing_schema_version": b"5",
                b"ttl_duration": b"0 denotes an instantaneous rising edge; pulse width is not measured",
            },
        )
    actual = {row[0] for row in db.execute("SELECT DISTINCT antenna FROM raw_counts")}
    targets = {}
    for antenna in actual | graph.antennas:
        alias = settings.aliases.get(antenna, antenna).strip()
        if alias in targets and targets[alias] != antenna:
            raise ValueError(
                f"Alias {alias!r} would merge {targets[alias]} and {antenna}. Choose distinct names."
            )
        targets[alias] = antenna
    unknown = (set(settings.aliases) | set(settings.routes) | set(settings.ttl_routes)) - actual - graph.antennas
    if unknown:
        diagnostics.write("unused_antenna_settings", sources=sorted(unknown))
    route_output_rows(db, settings, cancelled)
    db.execute("CREATE TEMP TABLE output_rows AS SELECT passes.id, routed.destination, routed.part, ROW_NUMBER() OVER (PARTITION BY routed.destination,routed.part ORDER BY passes.pc_start,passes.id)-1 AS row_number FROM passes JOIN routed USING(id)")
    db.execute("CREATE UNIQUE INDEX output_rows_id ON output_rows(id)")
    outputs = []
    groups = db.execute(
        "SELECT DISTINCT destination,part FROM routed ORDER BY part,destination"
    ).fetchall()
    part_count = db.execute("SELECT COUNT(DISTINCT part) FROM raw").fetchone()[0] if settings.split_restarts else 1
    generated_parts = {}
    for group, part in groups:
        generated_parts.setdefault(group, []).append(part)
    diagnostics.output_fingerprints = {}
    for group_index, (group, part) in enumerate(groups):
        if cancelled():
            raise Cancelled()
        stem = data_stem(diagnostics.info["experiment_output_name"], group, part, part_count)
        names = [
            stem + "." + suffix
            for suffix, enabled in [
                ("parquet", settings.parquet),
                ("csv", settings.csv),
            ]
            if enabled
        ]
        pq_writer, csv_handle = None, None
        batch = []
        total = 0
        try:
            if settings.parquet:
                pq_writer = pq.ParquetWriter(
                    directory / (stem + ".parquet.partial"),
                    schema,
                    compression="zstd",
                    use_dictionary=["antenna", "animal_id"],
                    store_schema=True,
                )
            if settings.csv:
                csv_handle = (directory / (stem + ".csv.partial")).open(
                    "w", encoding="utf-8", newline=""
                )
                csv_writer = csv.writer(csv_handle)
                csv_writer.writerow(COLUMNS)

            def flush():
                if pq_writer is not None and batch:
                    arrays = []
                    for index, field in enumerate(schema):
                        values = [row[index] for row in batch]
                        if field.name == 'antenna':
                            # Logical dictionary type makes Polars read Categorical;
                            # Parquet dictionary compression alone would stay String.
                            array = pa.array(values, type=pa.string()).dictionary_encode().cast(field.type)
                        elif field.name == 'animal_id':
                            array = pa.DictionaryArray.from_arrays(
                                pa.array([category_index[v] if v is not None else None for v in values], type=pa.uint32()),
                                category_values, ordered=True)
                        else:
                            array = pa.array(values, type=field.type)
                        arrays.append(array)
                    pq_writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                batch.clear()

            query = "SELECT passes.* FROM passes JOIN routed USING(id) WHERE routed.destination=? AND routed.part=? ORDER BY pc_start,passes.id"
            for raw in db.execute(query, (group, part)):
                row = dict(raw)
                values = [
                    settings.aliases.get(row["antenna"], row["antenna"]),
                    row["tag"],
                    iso(row["pc_start"], settings.timezone),
                    row["duration"],
                    iso(row["board_start"], settings.timezone),
                    bool(row["interpolated"]),
                ]
                if csv_handle:
                    csv_writer.writerow(values)
                if pq_writer:
                    batch.append(
                        [
                            EPOCH + timedelta(microseconds=row["pc_start"]),
                            values[0],
                            values[3] * 1000,
                            values[1],
                            (
                                None
                                if row["board_start"] is None
                                else EPOCH + timedelta(microseconds=row["board_start"])
                            ),
                            values[5],
                        ]
                    )
                if row["interpolated"] or part_for(row["pc_start"], boundaries) != part_for(row["pc_end"], boundaries):
                    provenance = {
                        key: row[key]
                        for key in (
                            "id",
                            "epoch",
                            "antenna",
                            "part",
                            "reads",
                            "file_start",
                            "line_start",
                            "file_end",
                            "line_end",
                        )
                    }
                    provenance.update(
                        animal_id=row["tag"],
                        output_antenna=values[0],
                        datetime=values[2],
                        internal_board_timestamp=values[4],
                        output_files=names,
                        row_number=total,
                        stream=f"{group}:{part}",
                        is_interpolated=bool(row["interpolated"]),
                        actual_last_PC_detection=iso(row["pc_end"], settings.timezone),
                        actual_last_board_detection=iso(
                            row["board_end"], settings.timezone
                        ),
                        spans_output_boundary=part_for(row["pc_start"], boundaries)
                        != part_for(row["pc_end"], boundaries),
                        duration_ms=row["duration"],
                    )
                    try:
                        provenance["method"] = json.loads(row["detail"])
                    except (ValueError, TypeError):
                        provenance["method"] = row["detail"]
                    if row["interpolated"]:
                        # An estimated end is not an actual observed detection.
                        provenance["estimated_end_datetime"] = provenance.pop(
                            "actual_last_PC_detection"
                        )
                        provenance["estimated_end_board_timestamp"] = provenance.pop(
                            "actual_last_board_detection"
                        )
                    diagnostics.write("output_record", **provenance)
                total += 1
                if total % 5000 == 0:
                    flush()
                    if cancelled():
                        raise Cancelled()
            flush()
        finally:
            if pq_writer:
                pq_writer.close()
            if csv_handle:
                csv_handle.close()
        for name in names:
            partial = directory / (name + ".partial")
            partial.rename(directory / name)
            outputs.append(
                {
                    "file": name,
                    "group": group,
                    "stream": f"{group}:{part}",
                    **({"device_name": settings._device_names[group]} if group in getattr(settings, "_device_names", {}) else {}),
                    "sources": [r[0] for r in db.execute("SELECT DISTINCT antenna FROM passes JOIN routed USING(id) WHERE routed.destination=? AND routed.part=? ORDER BY antenna", (group, part))],
                    "part": {"number": generated_parts[group].index(part) + 1,
                             "total": len(generated_parts[group])},
                    "rows": total,
                }
            )
            # Needed for safe replacement only; never part of a public result.
            diagnostics.output_fingerprints[name] = file_hash(directory / name, cancelled)
        progress(
            75 + int(20 * (group_index + 1) / max(1, len(groups))),
            f"Wrote {stem}: {total:,} rows",
        )
    diagnostics.info["outputs"] = outputs
    diagnostics.info["data_columns"] = list(COLUMNS)
    diagnostics.info['format_schemas'] = {
        'parquet': {'version': 5, 'columns': list(PARQUET_COLUMNS),
                    'time_under': 'duration[us]', 'antenna': 'dictionary categorical / Polars Categorical',
                    'animal_id': 'dictionary categorical / Polars Enum', 'inserted': 'Boolean'},
        'csv': {'columns': list(COLUMNS), 'time_spent_unit': 'milliseconds', 'inserted': 'Boolean (True/False)'},
    }
    diagnostics.info['format_schemas'] = {k:v for k,v in diagnostics.info['format_schemas'].items() if getattr(settings, k)}
    diagnostics.info["output_sort_order"] = "datetime ascending (UTC instant); stable internal record ID for equal timestamps"
    diagnostics.info["time_spent_unit"] = "milliseconds"
    diagnostics.info["antenna_aliases"] = settings.aliases
    diagnostics.info["antenna_routes"] = settings.routes
    diagnostics.info["ttl_routes"] = settings.ttl_routes
    diagnostics.count("output_data_files", len(outputs))
    return outputs
