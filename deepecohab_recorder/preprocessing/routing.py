"""One routing rule for both movement eligibility and exported files."""
from functools import lru_cache

from .models import Cancelled


def route_output_rows(db, settings, cancelled, batch_size=4096):
    """Stream bounded batches to the disk-backed routing index in source order."""
    db.execute("CREATE TEMP TABLE routed(id INTEGER PRIMARY KEY, destination TEXT, part INTEGER)")

    @lru_cache(maxsize=512)
    def group_for(antenna, kind):
        return destination(antenna, kind, settings.routes, settings.ttl_routes)

    cursor = db.execute("SELECT id,antenna,kind,part FROM passes")
    while True:
        if cancelled():
            raise Cancelled()
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        db.executemany("INSERT INTO routed VALUES(?,?,?)", (
            (row["id"], group_for(row["antenna"], row["kind"]),
             row["part"] if settings.split_restarts else 1) for row in rows))
    db.execute("CREATE INDEX routed_group ON routed(destination,part)")


def destination(antenna, kind, routes, ttl_routes=None):
    if kind == "ttl":
        return (ttl_routes or {}).get(antenna, "TTL_data1")
    group = routes.get(antenna, "main")
    return "rfid" if group in ("main", "rfid") else group


def excluded_antennas(routes):
    return {name for name in routes if destination(name, "rfid", routes) != "rfid"}


def stitch_window_ms(antenna, settings):
    group = destination(antenna, "rfid", settings.routes)
    return settings.window_ms if group == "rfid" else settings.group_windows_ms.get(group, 300)


def prepare_movement_stream(db, graph, settings, diagnostics):
    """Classify after stitching; retain excluded sensors' physical connections."""
    actual = {r[0] for r in db.execute("SELECT DISTINCT antenna FROM passes WHERE kind!='ttl'")}
    excluded = excluded_antennas(settings.routes) | getattr(settings, "_device_sources", set())
    main = actual - excluded
    unmapped = main - graph.antennas
    diagnostics.info["movement_antennas"] = {
        "layout": sorted(graph.movement_antennas),
        "observed_main": sorted(main),
        "excluded_by_output_group": sorted(excluded),
        "observed_excluded": sorted(actual & excluded),
        "unmapped_main": sorted(unmapped),
        "policy": "Separate output groups are excluded from movement inference and transit learning. Physical tunnel connections are retained.",
    }
    if unmapped and settings.effective_interpolation_mode != "none":
        raise ValueError(
            "These main-file antennas are absent from the physical layout: "
            + ", ".join(sorted(unmapped))
            + ". In Manage RFID and TTL output grouping, assign non-layout antennas to a separate file; "
            "otherwise correct the layout or choose No interpolation in Advanced options."
        )
    db.execute("CREATE TEMP TABLE movement_antennas(antenna TEXT PRIMARY KEY)")
    db.executemany("INSERT INTO movement_antennas VALUES(?)", [(a,) for a in sorted(graph.movement_antennas)])
    diagnostics.count("RFID_periods_excluded_from_interpolation", db.execute(
        "SELECT COUNT(*) FROM passes WHERE kind!='ttl' AND antenna NOT IN (SELECT antenna FROM movement_antennas)"
    ).fetchone()[0])
