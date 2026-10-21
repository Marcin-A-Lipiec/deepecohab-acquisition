"""Disk-backed stages with bounded raw-data memory and atomic job publication."""

from __future__ import annotations

from contextlib import ExitStack
from importlib.util import find_spec
from pathlib import Path
import sqlite3

from deepecohab_recorder.schema import default_config
from deepecohab_recorder.config_bundle import load_config_bundle, relative_link
from deepecohab_recorder.metadata_updates import config_digest
from .experiment_bounds import proposed_bounds
from .layout_check import check_layout
from . import VERSION
from .diagnostics import Diagnostics
from .export import export
from .interpolation import PhysicalGraph, interpolate, learn_transits
from .models import Cancelled
from .stitching import stitch
from .timing import ingest
from .routing import destination, excluded_antennas, prepare_movement_stream, stitch_window_ms
from .output_plan import experiment_output_name
from .publication import Publication
from .publish_result import publish_result
from deepecohab_recorder.experiment_files import filename


SQL = """
CREATE TABLE raw(id INTEGER PRIMARY KEY,epoch INTEGER,file INTEGER,line INTEGER,antenna TEXT,tag TEXT,pc INTEGER,rel_ticks INTEGER,counter TEXT,kind TEXT,legacy_duration INTEGER,part INTEGER,board_time INTEGER,accepted INTEGER DEFAULT 0);
CREATE INDEX raw_epoch ON raw(epoch);
CREATE TABLE passes(id INTEGER PRIMARY KEY,epoch INTEGER,antenna TEXT,tag TEXT,pc_start INTEGER,pc_end INTEGER,board_start INTEGER,board_end INTEGER,duration INTEGER,reads INTEGER,file_start INTEGER,line_start INTEGER,file_end INTEGER,line_end INTEGER,kind TEXT,part INTEGER,interpolated INTEGER,detail TEXT);
CREATE TABLE transits(tag TEXT,a TEXT,b TEXT,gap INTEGER);
CREATE INDEX transit_lookup ON transits(tag,a,b,gap);
"""


def run(settings, progress=lambda value, text: None, cancelled=lambda: False):
    import copy
    settings = copy.deepcopy(settings)
    settings.validate()
    if settings.parquet and find_spec("pyarrow") is None:
        raise ValueError(
            'Install the new dependency first: python -m pip install "pyarrow>=15,<22"'
        )
    config = default_config()
    expected_config = None
    if settings.config_path:
        expected_config = config_digest(settings.config_path)
        config = load_config_bundle(settings.config_path)
        if config_digest(settings.config_path) != expected_config:
            raise ValueError('Configuration changed while loading. Reload before processing.')
    from deepecohab_recorder.device_antennas import resolve_routes, device_errors
    errors = device_errors(config['recording'])
    if errors:
        raise ValueError('Correct device antennas before preprocessing:\n' + '\n'.join(errors))
    route_options = {'routes': copy.deepcopy(settings.routes), 'ttl_routes': copy.deepcopy(settings.ttl_routes),
                     'device_groups': dict(settings.device_groups or config.get('acquisition', {}).get('preprocessing', {}).get('options', {}).get('device_groups', {}))}
    settings._route_overrides = copy.deepcopy(route_options)
    settings.routes, settings.ttl_routes, settings._device_names, _ = resolve_routes(config, route_options)
    settings.device_groups = route_options['device_groups']
    settings._device_sources = {d['antenna'] for d in config['recording'].get('devices', [])}
    metadata_review = None
    if settings.allow_incomplete_metadata:
        from .metadata_review import apply_metadata_override
        metadata_review = apply_metadata_override(config, settings)
    layout_check = check_layout(config, settings.inputs)
    if settings.config_path and any(layout_check.values()) and not settings.allow_incomplete_metadata:
        raise ValueError('Layout antennas need matching board suffixes. Use Data preprocessing in the GUI to confirm unambiguous updates, or assign each board in EcoHAB layout and save the configuration. Multiple boards sharing channels require manual assignment.')
    tags = {
        str(animal.get("tag", "")).strip().upper()
        for animal in config["recording"]["cohort"]["animals"]
        if animal.get("tag")
    }
    if settings.whitelist and not tags:
        raise ValueError(
            "Tag filtering is enabled but no animal tags were found. Choose the experiment JSON file or explicitly disable filtering in Advanced options."
        )
    graph = PhysicalGraph(
        config["recording"]["layout"], {item.board_id for item in settings.inputs},
        excluded_antennas(settings.routes) | settings._device_sources,
        devices=config["recording"].get("devices", []),
    )
    if settings.effective_interpolation_mode != "none" and graph.problems:
        raise ValueError(
            "Interpolation requires a valid physical layout: "
            + "; ".join(graph.problems[:5])
        )
    output_name = experiment_output_name(settings.config_path, config)
    published = Path(settings.output_dir).resolve()
    main_root = Path(settings.config_path).resolve().parent if settings.config_path else published
    transaction = Publication(main_root, published, output_name)
    transaction.__enter__()
    staging = transaction.staging
    db = None
    diagnostics = None
    try:
        diagnostics = Diagnostics(staging, settings.timezone, filename(output_name, 'diagnostic', 'json'))
        diagnostics.config_root = Path(settings.config_path).resolve().parent if settings.config_path else Path(settings.output_dir).resolve()
        diagnostics.settings = settings
        diagnostics.cancelled = cancelled
        diagnostics.main_antennas = sorted(graph.antennas)
        diagnostics.info['diagnostic_relative_to_data'] = relative_link(main_root / diagnostics.filename, published / 'data')
        diagnostics.info.update(
            software=VERSION,
            experiment_output_name=output_name,
            ttl_time_spent_policy="0 ms: instantaneous rising-edge event; pulse width is not available",
            config_file=Path(settings.config_path).name if settings.config_path else None,
            configured_animal_tags=sorted(tags),
            processing_options={key: value for key, value in settings.to_dict().items() if key not in ("inputs", "output_dir", "config_path", "interpolate", "aliases", "routes")},
        )
        legacy_tunnels = [t['name'] for t in config['recording']['layout'].get('tunnels', []) if len(t.get('antennas', [])) > 2]
        if legacy_tunnels:
            diagnostics.write('legacy_tunnel_measurements', tunnels=legacy_tunnels, message='More than two main antennas: length is null; individual historical measurements remain in GUI state. Review the topology.')
        diagnostics.info["processing_options"]["interpolation_mode"] = settings.effective_interpolation_mode
        if metadata_review:
            diagnostics.write("incomplete_metadata_accepted", issues=metadata_review["issues"],
                              processing_changes=metadata_review["changes"],
                              message="The user accepted incomplete experiment metadata. Missing tags or topology were not inferred.")
        with ExitStack() as cleanup:
            database_path = staging / "processing_cache.sqlite"
            cleanup.callback(lambda: database_path.unlink(missing_ok=True))
            db = sqlite3.connect(str(database_path))
            cleanup.callback(db.close)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-32768")
            db.execute(
                "PRAGMA journal_mode=OFF"
            )  # Rebuildable staging DB; source files are read-only.
            db.set_progress_handler(lambda: 1 if cancelled() else 0, 20000)
            db.executescript(SQL)
            progress(1, "Reading configuration and reconstructing clock periods")
            epochs, boundaries = ingest(db, settings, diagnostics, progress, cancelled, config)
            db.execute("CREATE TEMP TABLE allowed(tag TEXT PRIMARY KEY)")
            db.executemany("INSERT INTO allowed VALUES(?)", [(tag,) for tag in tags])
            db.execute(
                "UPDATE raw SET accepted=1 WHERE kind='ttl' OR tag IN (SELECT tag FROM allowed)"
                if settings.whitelist
                else "UPDATE raw SET accepted=1"
            )
            diagnostics.count(
                "valid_read_rows", db.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
            )
            diagnostics.count(
                "rejected_tag_reads",
                db.execute(
                    "SELECT COUNT(*) FROM raw WHERE kind!='ttl' AND accepted=0"
                ).fetchone()[0],
            )
            diagnostics.count(
                "accepted_RFID_reads",
                db.execute(
                    "SELECT COUNT(*) FROM raw WHERE kind!='ttl' AND accepted=1"
                ).fetchone()[0],
            )
            if not db.execute("SELECT 1 FROM raw WHERE accepted=1 LIMIT 1").fetchone():
                raise ValueError(
                    "No records remain after tag filtering. Check the experiment config and its tag list."
                )
            progress(38, "Summarizing accepted and rejected reads")
            db.execute(
                "CREATE TABLE raw_counts AS SELECT antenna,kind,tag,accepted,COUNT(*) AS reads FROM raw GROUP BY antenna,kind,tag,accepted"
            )
            db.execute("CREATE INDEX raw_counts_lookup ON raw_counts(antenna,tag)")
            diagnostics.info["stitch_windows_ms"] = {"main": settings.window_ms}
            for (antenna,) in db.execute("SELECT DISTINCT antenna FROM raw_counts WHERE kind!='ttl'"):
                group = destination(antenna, "rfid", settings.routes)
                diagnostics.info["stitch_windows_ms"]["main" if group == "rfid" else group] = stitch_window_ms(antenna, settings)
            db.execute("CREATE INDEX raw_stream ON raw(epoch,antenna,COALESCE(board_time,pc),id)")
            stitch(db, settings, boundaries, diagnostics, cancelled, progress)
            db.execute(
                "CREATE INDEX observed_order ON passes(tag,COALESCE(board_start,pc_start),id)"
            )
            db.execute(
                "CREATE INDEX period_stats ON passes(tag,antenna,interpolated,duration)"
            )
            prepare_movement_stream(db, graph, settings, diagnostics)
            progress(60, "Calculating all mouse and tunnel-direction transit medians")
            learn_transits(db, graph, settings, boundaries, epochs, diagnostics, cancelled)
            if settings.effective_interpolation_mode != "none":
                interpolate(
                    db,
                    graph,
                    settings,
                    boundaries,
                    epochs,
                    diagnostics,
                    cancelled,
                    progress,
                )
            outputs = export(
                db,
                staging,
                settings,
                graph,
                boundaries,
                diagnostics,
                cancelled,
                progress,
            )
            updated_config, timing_report = proposed_bounds(config, settings, db)
            if cancelled():
                raise Cancelled()
            for antenna in sorted(
                graph.antennas
                | {
                    row[0]
                    for row in db.execute(
                        "SELECT DISTINCT antenna FROM raw_counts WHERE kind!='ttl'"
                    )
                }
            ):
                for tag in sorted(tags):
                    count = db.execute(
                        "SELECT SUM(reads) FROM raw_counts WHERE antenna=? AND tag=?",
                        (antenna, tag),
                    ).fetchone()[0]
                    if not count:
                        diagnostics.write(
                            "expected_tag_antenna_without_reads",
                            antenna=antenna,
                            tag=tag,
                        )
            timing_report = publish_result(transaction, settings, config, updated_config, timing_report,
                                           diagnostics, outputs, expected_config, db, cancelled)
            db.close()
            db = None
        progress(100, f"Completed: {published}")
        return {
            "directory": str(published),
            "diagnostic_path": str(main_root / diagnostics.filename),
            "outputs": outputs,
            "counts": dict(diagnostics.counts),
            "config_update": dict(timing_report, status=timing_report.get("metadata_status", timing_report["status"])),
        }
    except Exception as exc:
        is_cancelled = isinstance(exc, Cancelled) or cancelled()
        if db is not None:
            try:
                db.set_progress_handler(None, 0)
                db.close()
            except sqlite3.Error:
                pass
        if diagnostics is not None and not diagnostics._inserts.closed:
            diagnostics._inserts.close()
        if is_cancelled:
            raise Cancelled('Cancelled. Previous completed results were preserved.') from exc
        raise ValueError(str(exc)) from exc
    finally:
        transaction.__exit__(None, None, None)
