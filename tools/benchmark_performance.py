"""Hardware-free, repeatable measurements of display bursts, previews and routing.

Use --package-parent to measure a wheel extracted into an audit directory with
the same interpreter/dependencies as the source. Results include correctness
fingerprints; they are measurements, not timing assertions in the test suite.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time
from types import SimpleNamespace


def measure(callback, repeat=5):
    values = [callback() for _ in range(repeat)]
    return dict(median_ms=statistics.median(values), samples_ms=values)


class RoutingComplete(Exception):
    pass


class RoutingDatabase(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith("CREATE TEMP TABLE output_rows"):
            raise RoutingComplete()
        return super().execute(sql, parameters)


def routing_benchmark(directory, rows):
    from deepecohab_recorder.preprocessing.export import export
    settings = SimpleNamespace(parquet=False, aliases={},
        routes={"A-1": "main", "B-1": "lickometer_data1", "C-1": "rfid"},
        ttl_routes={"ttl_1-1": "TTL_data2"}, split_restarts=True)
    graph = SimpleNamespace(antennas={"A-1", "B-1", "C-1"})
    diagnostics = SimpleNamespace(write=lambda *args, **kwargs: None)
    channels = ("A-1", "B-1", "C-1", "D-2", "ttl_1-1", "ttl_2-2")
    samples, fingerprints = [], []
    for trial in range(5):
        with sqlite3.connect(directory / "routing.sqlite", factory=RoutingDatabase) as db:
            db.row_factory = sqlite3.Row
            db.execute("DROP TABLE IF EXISTS passes")
            db.execute("DROP TABLE IF EXISTS raw_counts")
            db.execute("CREATE TABLE passes(id INTEGER PRIMARY KEY,antenna TEXT,kind TEXT,part INTEGER)")
            db.execute("CREATE TABLE raw_counts(antenna TEXT)")
            db.executemany("INSERT INTO raw_counts VALUES(?)", [(channel,) for channel in channels])
            db.executemany("INSERT INTO passes VALUES(?,?,?,?)", (
                (index, channels[index % len(channels)], "ttl" if index % 6 >= 4 else "rfid", index % 3 + 1)
                for index in range(rows)))
            db.commit()
            started = time.perf_counter()
            try:
                export(db, directory, settings, graph, [], diagnostics, lambda: False, lambda *args: None)
            except RoutingComplete:
                pass
            samples.append((time.perf_counter() - started) * 1000)
            checksum = hashlib.sha256()
            for row in db.execute("SELECT id,destination,part FROM routed ORDER BY id"):
                checksum.update(json.dumps(tuple(row), separators=(",", ":")).encode("utf-8"))
            fingerprints.append(checksum.hexdigest())
    assert len(set(fingerprints)) == 1
    return dict(rows=rows, median_ms=statistics.median(samples), samples_ms=samples,
        routed_rows_sha256=fingerprints[0], disk_backed=True)


def gui_benchmark(directory):
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from deepecohab_recorder.app import configure_logging
    from deepecohab_recorder.main_window import MainWindow
    from deepecohab_recorder.controllers.camera import CameraController
    from deepecohab_recorder.models import CameraConfig

    app = QApplication([])
    configure_logging()
    window = MainWindow()
    window.services.shutdown()
    window.timer.stop()
    window.resize(1280, 800)
    window.show()
    for _ in range(8):
        app.processEvents()
    bursts = []
    for trial in range(5):
        window.log.clear()
        for index in range(10000):
            window.services.messages.put(("info", f"Synthetic status {index}"))
        started = time.perf_counter()
        ticks, first_tick, durations = 0, None, []
        while not window.services.messages.empty():
            tick = time.perf_counter()
            window.refresh()
            duration = (time.perf_counter() - tick) * 1000
            if first_tick is None:
                first_tick = duration
            durations.append(duration)
            ticks += 1
            app.processEvents()
        bursts.append(dict(first_tick_ms=first_tick, total_ms=(time.perf_counter()-started)*1000,
            ticks=ticks, maximum_tick_ms=max(durations), last_message_present="Synthetic status 9999" in window.log.toPlainText()))
    controller = CameraController(CameraConfig())
    controller._preview_frame = np.arange(640 * 640, dtype=np.uint8).reshape(640,640)
    if hasattr(controller, "_preview_sequence"):
        controller._preview_sequence = 1
    camera = window.widgets["camera"]
    window.pages.set_current_key("camera")
    for _ in range(8):
        app.processEvents()
    camera.update_preview(controller)
    def preview():
        started = time.perf_counter()
        for _ in range(200):
            camera.update_preview(controller)
        return (time.perf_counter()-started)*1000
    repeated = measure(preview)
    repeated["updates"] = 200
    repeated["preview_pixels_sha256"] = hashlib.sha256(bytes(camera.preview_label.pixmap().toImage().constBits())).hexdigest()
    window.close()
    return dict(message_bursts=dict(messages=10000, trials=bursts,
        median_first_tick_ms=statistics.median(item["first_tick_ms"] for item in bursts),
        median_total_ms=statistics.median(item["total_ms"] for item in bursts)), unchanged_preview=repeated)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-parent", type=Path)
    parser.add_argument("--routing-rows", type=int, default=100000)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str((args.package_parent or Path(__file__).resolve().parent.parent).resolve()))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["DEEPECOHAB_ACQUISITION_HOME"] = str(directory / "application-data")
    from deepecohab_recorder import __version__
    report = dict(version=__version__, python=sys.version, physical_hardware_tested=False,
        gui=gui_benchmark(directory), routing=routing_benchmark(directory, args.routing_rows))
    (directory / "performance.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
