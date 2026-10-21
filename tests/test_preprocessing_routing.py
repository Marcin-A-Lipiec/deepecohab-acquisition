"""Bounded routing retains source identity, part selection and cancellation."""
import sqlite3
from types import SimpleNamespace

import pytest

from deepecohab_recorder.preprocessing.models import Cancelled
from deepecohab_recorder.preprocessing.routing import route_output_rows


@pytest.mark.parametrize("split", [False, True])
def test_many_channels_and_groups_keep_every_id_and_part(tmp_path, split):
    with sqlite3.connect(tmp_path / "routing.sqlite") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE passes(id INTEGER PRIMARY KEY,antenna TEXT,kind TEXT,part INTEGER)")
        channels = [(index, f"channel-{index % 700}", "ttl" if index % 3 == 0 else "rfid", index % 4 + 1)
                    for index in range(10000)]
        db.executemany("INSERT INTO passes VALUES(?,?,?,?)", channels)
        settings = SimpleNamespace(routes={"channel-1": "lickometer_data1", "channel-2": "rfid"},
            ttl_routes={"channel-0": "TTL_data2"}, split_restarts=split)
        route_output_rows(db, settings, lambda: False)
        expected = [(index, ("TTL_data2" if antenna == "channel-0" else "TTL_data1")
                     if kind == "ttl" else ("lickometer_data1" if antenna == "channel-1" else "rfid"),
                     part if split else 1) for index, antenna, kind, part in channels]
        assert [tuple(row) for row in db.execute("SELECT * FROM routed ORDER BY id")] == expected


def test_routing_can_cancel_between_batches_and_roll_back(tmp_path):
    db = sqlite3.connect(tmp_path / "cancel.sqlite")
    try:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE passes(id INTEGER PRIMARY KEY,antenna TEXT,kind TEXT,part INTEGER)")
        db.executemany("INSERT INTO passes VALUES(?, 'A-1', 'rfid', 1)", ((i,) for i in range(10000)))
        db.commit()
        settings = SimpleNamespace(routes={}, ttl_routes={}, split_restarts=False)
        with pytest.raises(Cancelled):
            route_output_rows(db, settings,
                lambda: db.execute("SELECT COUNT(*) FROM routed").fetchone()[0] > 0)
        assert 0 < db.execute("SELECT COUNT(*) FROM routed").fetchone()[0] < 10000
        db.rollback()
        assert db.execute("SELECT COUNT(*) FROM routed").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM passes").fetchone()[0] == 10000
    finally:
        db.close()
