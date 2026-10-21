"""End-to-end scientific-data regression tests; no physical board is required."""

import csv
from datetime import datetime, timedelta
import hashlib
import json
from tests.helpers import result_fingerprint
from pathlib import Path
import tempfile
import unittest

from deepecohab_recorder.preprocessing import COLUMNS, PARQUET_COLUMNS
from deepecohab_recorder.preprocessing.interpolation import PhysicalGraph
from deepecohab_recorder.preprocessing.models import (
    InputFile,
    Settings,
    Cancelled,
    parse_pc,
)
from deepecohab_recorder.preprocessing.pipeline import run
from deepecohab_recorder.preprocessing.readers import scan
from deepecohab_recorder.schema import default_config

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = pq = None

FIXTURES = Path(__file__).parent / "fixtures" / "preprocessing"
TAG = "0000A819"
OTHER = "0000B819"
BASE = datetime.fromisoformat("2025-06-01T12:00:00+02:00")


def linear_layout():
    return {
        "cages": [{"cell_id": name, "name": name} for name in ("C1", "C2", "C3")],
        "tunnels": [
            {
                "name": "tunnel_1",
                "start_cell_id": "C1",
                "end_cell_id": "C2",
                "antennas": ["A-1", "B-1"],
                "dead_end": False,
            },
            {
                "name": "tunnel_2",
                "start_cell_id": "C2",
                "end_cell_id": "C3",
                "antennas": ["C-1", "D-1"],
                "dead_end": False,
            },
        ],
        "junctions": [],
    }


class PreprocessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = default_config()
        self.cfg["recording"]["cohort"] = {
            "n_mice": 2,
            "animals": [
                {"tag": tag, "subject_name": str(i), "date_of_birth": "2024-01-01", "mouse_line": "Test", "genotype": "WT", "genetic_background": "C57BL/6J", "sex": "Male"}
                for i, tag in enumerate((TAG, OTHER))
            ],
        }
        self.cfg["recording"]["layout"] = linear_layout()
        self.cfg["acquisition"]["boards"]["board_1"].update(
            com_port="COM3", hardware="DeepEcoHAB RFID DAQ v1.5"
        )

    def tearDown(self):
        self.temp.cleanup()

    def raw(
        self, name, events, port="COM3", board=1, group="1", profile="v1.5", tick_ms=10
    ):
        """Events are (milliseconds, antenna, tag[, counter]) with independently known times."""
        lines = []
        for event in events:
            ms, antenna, tag = event[:3]
            counter = event[3] if len(event) > 3 else round(ms / tick_ms)
            lines.append(
                f"{antenna};{tag};{counter:X};{(BASE + timedelta(milliseconds=ms)).isoformat()}"
            )
        path = self.root / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return InputFile(str(path), port, board, profile, group, tick_ms=tick_ms)

    def process(self, inputs, **options):
        config = self.root / "experiment.config.json"
        # Model a GUI metadata edit: keep the application's last-result state.
        if config.exists():
            from deepecohab_recorder.config_bundle import load_config_bundle
            previous = load_config_bundle(config)['acquisition'].get('preprocessing', {})
            for key in ('completed_run', '_output_integrity'):
                if key in previous:
                    self.cfg['acquisition'].setdefault('preprocessing', {})[key] = previous[key]
        config.write_text(json.dumps(self.cfg), encoding="utf-8")
        args = dict(
            inputs=inputs,
            config_path=str(config),
            output_dir=str(self.root / "out"),
            parquet=False,
            csv=True,
            interpolate=False,
        )
        args.update(options)
        result = run(Settings(**args))
        self.result = result
        self.output = Path(result["directory"])
        self.summary = json.loads(
            Path(result["diagnostic_path"]).read_text()
        )
        from tests.helpers import inserted_rows, preprocessing_context
        self.context = preprocessing_context(self.summary, result['diagnostic_path'])
        self.inserted = inserted_rows(self.summary, self.output)
        self.details = [example for issue in self.summary['issues'].values() if isinstance(issue, dict)
                        for example in issue.get('examples', [])] + self.inserted
        data = []
        for file in result["outputs"]:
            if file["file"].endswith(".csv"):
                with (self.output / file["file"]).open(
                    newline="", encoding="utf-8"
                ) as handle:
                    reader = csv.DictReader(handle)
                    self.assertEqual(reader.fieldnames, list(COLUMNS))
                    data.extend(reader)
        return data

    def details_of(self, kind):
        return [row for row in self.details if row["type"] == kind]

    def test_supplied_v1_sample(self):
        data = self.process(
            [InputFile(str(FIXTURES / "user_v1_sample.txt"), "COM3")], whitelist=False
        )
        self.assertEqual(self.summary["counts"]["valid_read_rows"], 100)
        self.assertEqual(
            [int(row["time_spent"]) for row in data],
            [760, 480, 460, 940, 360, 520, 520, 520, 10],
        )
        self.assertEqual(self.summary["counts"]["RFID_minimum_durations_assigned"], 1)
        self.assertTrue(all(row["datetime"].endswith("+02:00") for row in data))

    def test_supplied_old_decimal_sample(self):
        self.cfg['recording']['layout'] = {}
        data = self.process(
            [
                InputFile(
                    str(FIXTURES / "user_old_decimal.csv"),
                    "COM3",
                    profile="old_decimal",
                )
            ],
            whitelist=False,
        )
        self.assertEqual(self.summary["counts"]["valid_read_rows"], 44)
        self.assertEqual(len(data), 2)
        self.assertAlmostEqual(
            self.summary["clocks"][0]["tick_ms"], 0.504, delta=0.002
        )
        self.assertAlmostEqual(int(data[0]["time_spent"]), 975, delta=3)
        self.assertAlmostEqual(int(data[1]["time_spent"]), 1151, delta=3)
        self.assertTrue(all(row["datetime"].endswith("+01:00") for row in data))

    def test_supplied_compressed_data_rejected(self):
        self.cfg['recording']['layout'] = {}
        for profile in ('compressed', 'legacy_raw', 'v1.0'):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, 'previously preprocessed legacy'):
                self.process([InputFile(str(FIXTURES / 'user_compressed_sample.txt'),
                                        'COM3', profile=profile)], whitelist=False)

    def test_per_antenna_stitch_and_real_tag_interruption(self):
        spec = self.raw(
            "local.txt",
            [
                (0, "A", TAG),
                (50, "B", OTHER),
                (100, "A", "FFFFFFFF"),
                (200, "A", TAG),
                (210, "A", OTHER),
                (220, "A", TAG),
            ],
        )
        data = self.process([spec])
        periods = [row for row in data if row["antenna"] == "A-1"]
        self.assertEqual([int(row["time_spent"]) for row in periods], [200, 10, 10])
        self.assertEqual([row["animal_id"] for row in periods], [TAG, OTHER, TAG])
        self.assertEqual(self.summary["counts"]["rejected_tag_reads"], 1)

    def test_strict_300ms_boundary(self):
        spec = self.raw(
            "boundary.txt", [(0, "A", TAG), (299, "A", TAG), (599, "A", TAG)], tick_ms=1
        )
        data = self.process([spec])
        self.assertEqual([int(row["time_spent"]) for row in data], [299, 10])

    def test_old_hex_profile_keeps_ten_digit_tag_and_custom_clock_units(self):
        self.cfg['recording']['layout'] = {}
        spec = self.raw(
            "old_hex.txt",
            [(0, "1", "A967A81904", 10000), (100, "1", "A967A81904", 10200)],
            profile="old_hex",
            tick_ms=0.5,
        )
        data = self.process([spec], whitelist=False)
        self.assertEqual(data[0]["animal_id"], "A967A81904")
        self.assertEqual(data[0]["antenna"], "1-1")
        self.assertEqual(int(data[0]["time_spent"]), 100)

    def test_windows_output_group_case_collision_is_rejected(self):
        spec = self.raw("case.txt", [(0, "A", TAG), (10, "B", OTHER)])
        with self.assertRaisesRegex(ValueError, "letter case"):
            self.process([spec], routes={"A-1": "Lickometer", "B-1": "lickometer"})

    def test_ttl_sets_origin_and_bypasses_tag_filter(self):
        spec = self.raw(
            "ttl.txt",
            [
                (0, "10", "00000001"),
                (100, "A", TAG),
                (150, "A", TAG),
                (200, "10", "00000002"),
            ],
        )
        data = self.process([spec])
        ttl = [row for row in data if row["antenna"].startswith("TTL")]
        self.assertEqual(len(ttl), 2)
        self.assertTrue(all(row["animal_id"] == "" and row["time_spent"] == "0" for row in ttl))
        self.assertEqual(
            parse_pc(self.summary['processing']['origin_datetime'], "Europe/Warsaw"),
            parse_pc(BASE.isoformat(), "Europe/Warsaw"),
        )
        self.assertEqual({f["group"] for f in self.result["outputs"]}, {"rfid", "TTL_data1"})
        rfid = next(row for row in data if row["antenna"] == "A-1")
        self.assertEqual(
            (
                datetime.fromisoformat(rfid["internal_board_timestamp"]) - BASE
            ).total_seconds(),
            0.1,
        )

    def test_two_boards_keep_distinct_antennas_and_late_start_offset(self):
        first = self.raw("first.txt", [(0, "A", TAG), (200, "A", TAG)])
        second = self.raw(
            "second.txt", [(100, "A", OTHER, 0), (200, "A", OTHER, 10)], "COM5", 2
        )
        data = self.process([first, second])
        self.assertEqual({row["antenna"] for row in data}, {"A-1", "A-2"})
        second_row = next(row for row in data if row["antenna"] == "A-2")
        self.assertEqual(
            (
                datetime.fromisoformat(second_row["internal_board_timestamp"]) - BASE
            ).total_seconds(),
            0.1,
        )
        self.assertEqual({f["part"]["number"] for f in self.result["outputs"]}, {1})

    def test_identical_bytes_on_separate_boards_are_not_duplicates(self):
        first = self.raw("first.txt", [(0, "A", TAG)])
        second = self.raw("second.txt", [(0, "A", TAG)], "COM5", 2)
        data = self.process([first, second])
        self.assertEqual(len(data), 2)
        duplicate = self.raw("copy.txt", [(0, "A", TAG)], group="2")
        data = self.process([first, duplicate])
        self.assertEqual(len(data), 1)
        self.assertEqual(self.summary["counts"]["duplicate_files_skipped"], 1)

    def test_restart_files_separate_but_explicit_continuation_stitches(self):
        first = self.raw("first.txt", [(0, "A", TAG), (100, "A", TAG)])
        second = self.raw("second.txt", [(200, "A", TAG), (250, "A", TAG)], group="2")
        data = self.process([first, second], split_restarts=True)
        self.assertEqual([int(row["time_spent"]) for row in data], [100, 50])
        self.assertEqual({f["part"]["number"] for f in self.result["outputs"]}, {1, 2})
        second.group = "1"
        first.part = second.part = 1
        data = self.process([first, second], split_restarts=True)
        self.assertEqual([int(row["time_spent"]) for row in data], [250])
        self.assertEqual(len(self.summary["clocks"]), 1)

    def test_other_board_restart_does_not_clip_healthy_board_presence(self):
        first = self.raw(
            "first.txt",
            [(0, "A", TAG), (100, "A", TAG), (200, "A", TAG), (300, "A", TAG)],
        )
        second = self.raw("second.txt", [(0, "B", OTHER)], "COM5", 2)
        restart = self.raw("restart.txt", [(150, "B", OTHER, 0)], "COM5", 2, group="2")
        data = self.process([first, second, restart])
        self.assertEqual(
            int(next(row for row in data if row["antenna"] == "A-1")["time_spent"]),
            300,
        )
        self.assertTrue(
            any(
                row["spans_output_boundary"] for row in self.details_of("observed_period_spanning_boundary")
            )
        )

    def test_counter_reset_vs_verified_unsigned_rollover(self):
        spec = self.raw("reset.txt", [(0, "A", TAG, 1000), (100, "A", TAG, 0)])
        data = self.process([spec])
        self.assertEqual(len(data), 2)
        self.assertEqual(len(self.summary["clocks"]), 2)
        spec = self.raw("wrap.txt", [(0, "A", TAG, 2**32 - 5), (100, "A", TAG, 5)])
        spec.counter_bits = 32
        data = self.process([spec])
        self.assertEqual([int(row["time_spent"]) for row in data], [100])
        self.assertEqual(self.summary["counts"]["counter_rollovers"], 1)

    def test_mixed_iso_precision_and_bad_rows_accounted_for(self):
        spec = self.raw(
            "precision.txt", [(0, "A", TAG), (100, "A", TAG), (200, "A", TAG)]
        )
        with Path(spec.path).open("a") as handle:
            # Unresolvable DST timestamps must stop (recording-clock tests),
            # rather than count as discardable malformed input.
            handle.write(f"A;{TAG};30;invalid-PC-timestamp\n")
            handle.write("invalid record\n")
        data = self.process([spec])
        self.assertEqual(len(data), 1)
        self.assertEqual(self.summary["counts"]["malformed_rows"], 2)
        self.assertEqual(self.summary["counts"]["valid_read_rows"], 3)

    def test_small_sustained_counter_reset_is_not_hidden_by_reorder_tolerance(self):
        events = [(0, "A", TAG, 100), (100, "A", TAG, 0)]
        events.extend((100 + tick * 10, "A", TAG, tick) for tick in range(1, 6))
        data = self.process([self.raw("small_reset.txt", events)])
        self.assertEqual(len(self.summary["clocks"]), 2)
        self.assertEqual([int(row["time_spent"]) for row in data], [10, 50])
        self.assertEqual(len(self.result["outputs"]), 1)

    def training_events(self, singleton=False):
        events = []
        for base in (0, 1000, 2000):
            events += [
                (base + ms, antenna, TAG)
                for ms, antenna in [
                    (0, "A"),
                    (50, "A"),
                    (150, "B"),
                    (200, "B"),
                    (400, "C"),
                    (450, "C"),
                    (550, "D"),
                    (600, "D"),
                ]
            ]
        events += [(3000, "A", TAG)]
        if not singleton:
            events += [(3050, "A", TAG)]
        events += [(3400, "C", TAG), (3450, "C", TAG)]
        return events

    def test_required_interpolation_uses_last_read_plus_mouse_tunnel_median(self):
        spec = self.raw("routes.txt", self.training_events())
        data = self.process([spec], interpolate=True)
        inferred = [
            row for row in self.details_of("output_record") if row["is_interpolated"]
        ]
        self.assertEqual(len(inferred), 1)
        self.assertEqual(inferred[0]["antenna"], "B-1")
        inserted = data[inferred[0]["row_number"]]
        self.assertEqual(
            (
                datetime.fromisoformat(inserted["internal_board_timestamp"]) - BASE
            ).total_seconds(),
            3.15,
        )
        self.assertEqual(int(inserted["time_spent"]), 50)
        self.assertEqual(inferred[0]["method"]["median_samples"], 3)
        self.assertEqual(inferred[0]["method"]["median_scope"], "mouse")

    def test_singleton_minimum_does_not_shift_interpolation_anchor(self):
        spec = self.raw("routes.txt", self.training_events(singleton=True))
        data = self.process([spec], interpolate=True)
        inferred = [
            row for row in self.details_of("output_record") if row["is_interpolated"]
        ]
        self.assertEqual(len(inferred), 1)
        inserted = data[inferred[0]["row_number"]]
        self.assertEqual(
            (datetime.fromisoformat(inserted["datetime"]) - BASE).total_seconds(), 3.1
        )

    def test_estimated_pass_outside_observed_time_bounds_is_skipped(self):
        events = self.training_events()[:-2] + [(3100, "C", TAG), (3150, "C", TAG)]
        self.process([self.raw("too_short.txt", events)], interpolate=True)
        self.assertFalse(
            any(row["is_interpolated"] for row in self.details_of("output_record"))
        )
        self.assertGreater(
            self.summary["counts"]["interpolation_timing_or_coverage_conflicts"], 0
        )

    def test_dead_end_antennas_default_to_main_output(self):
        self.cfg["recording"]["layout"]["junctions"] = [
            {"id": "J1", "parent_tunnel": "tunnel_1", "position": 0.5}
        ]
        self.cfg["recording"]["layout"]["tunnels"].append(
            {
                "name": "tunnel_3",
                "start_cell_id": "J1",
                "end_cell_id": "",
                "antennas": ["E-1"],
                "dead_end": True,
            }
        )
        self.process([self.raw("branch.txt", [(0, "A", TAG), (100, "E", TAG)])])
        self.assertEqual(
            {file["group"] for file in self.result["outputs"]}, {"rfid"}
        )

    def test_missing_timing_support_and_restart_block_interpolation(self):
        spec = self.raw("unsupported.txt", [(0, "A", TAG), (400, "C", TAG)])
        data = self.process([spec], interpolate=True)
        self.assertEqual(len(data), 2)
        self.assertEqual(
            self.summary["counts"]["interpolation_missing_timing_support"], 1
        )
        training = self.raw("training.txt", self.training_events()[:-2])
        next_run = self.raw("next.txt", [(3400, "C", TAG)], group="2")
        self.process([training, next_run], interpolate=True)
        self.assertFalse(
            any(row["is_interpolated"] for row in self.details_of("output_record"))
        )
        self.assertGreater(
            self.summary["counts"]["interpolation_blocked_by_recording_boundary"], 0
        )

    def test_cycle_does_not_assume_shortest_route_and_junction_is_respected(self):
        layout = linear_layout()
        graph = PhysicalGraph(layout, {1})
        self.assertEqual(graph.required("A-1", "C-1"), (("B-1",), "required"))
        layout["tunnels"].append(
            {
                "name": "tunnel_3",
                "start_cell_id": "C3",
                "end_cell_id": "C1",
                "antennas": ["E-1", "F-1"],
                "dead_end": False,
            }
        )
        graph = PhysicalGraph(layout, {1})
        self.assertEqual(graph.required("A-1", "C-1"), ((), "ambiguous"))
        layout = linear_layout()
        layout["junctions"] = [
            {"id": "J1", "parent_tunnel": "tunnel_1", "position": 0.5}
        ]
        layout["tunnels"].append(
            {
                "name": "tunnel_3",
                "start_cell_id": "J1",
                "end_cell_id": "",
                "antennas": ["E-1"],
                "dead_end": True,
            }
        )
        graph = PhysicalGraph(layout, {1})
        self.assertEqual(graph.required("A-1", "E-1"), ((), "direct"))
        self.assertEqual(graph.required("E-1", "C-1"), (("B-1",), "required"))

    def test_routes_and_aliases_do_not_change_stitching(self):
        spec = self.raw(
            "route.txt",
            [(0, "A", TAG), (10, "B", OTHER), (100, "A", TAG), (110, "B", OTHER)],
        )
        data = self.process(
            [spec], aliases={"B-1": "lick_port-1"}, routes={"B-1": "lickometer"}
        )
        self.assertEqual(
            {f["group"] for f in self.result["outputs"]}, {"rfid", "device_antenna1"}
        )
        self.assertEqual({row["antenna"] for row in data}, {"A-1", "lick_port-1"})
        self.assertEqual({int(row["time_spent"]) for row in data}, {100})
        with self.assertRaisesRegex(ValueError, "would merge"):
            self.process([spec], aliases={"B-1": "A-1"})

    @unittest.skipUnless(pa, "PyArrow is not installed")
    def test_parquet_csv_types_values_and_provenance_hash_agree(self):
        spec = self.raw(
            "types.txt", [(0, "A", TAG), (100, "A", TAG), (120, "10", "00001")]
        )
        self.process([spec], parquet=True)
        for output in self.result["outputs"]:
            path = self.output / output["file"]
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                result_fingerprint(self.result['diagnostic_path'], path)
            )
            if path.suffix != ".parquet":
                continue
            table = pq.read_table(path)
            self.assertEqual(table.column_names, list(PARQUET_COLUMNS))
            self.assertTrue(
                pa.types.is_dictionary(table.schema.field("animal_id").type)
            )
            self.assertTrue(pa.types.is_dictionary(table.schema.field("antenna").type))
            self.assertEqual(table.schema.field('time_under').type, pa.duration('us'))
            self.assertEqual(table.schema.field("datetime").type.tz, "Europe/Warsaw")
            with path.with_suffix(".csv").open(newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            for arrow, csv_row in zip(table.to_pylist(), csv_rows):
                self.assertEqual(arrow['time_under'], timedelta(milliseconds=int(csv_row['time_spent'])))
                self.assertEqual(arrow["animal_id"] or "", csv_row["animal_id"])
                self.assertEqual(
                    arrow["datetime"], datetime.fromisoformat(csv_row["datetime"])
                )
                self.assertEqual(
                    arrow["internal_board_timestamp"],
                    datetime.fromisoformat(csv_row["internal_board_timestamp"]),
                )
            references = [
                r
                for r in self.details_of("output_record")
                if path.name in r["output_files"]
            ]
            self.assertEqual(
                references, []  # Ordinary observed rows are no longer duplicated in diagnostics.
            )

    def test_cancelled_run_never_publishes_completed_output_and_releases_staging(self):
        spec = self.raw("cancel.txt", [(0, "A", TAG)])
        output = self.root / "cancelled"
        settings = Settings(
            [spec],
            str(output),
            whitelist=False,
            interpolate=False,
            parquet=False,
            csv=True,
        )
        with self.assertRaises(Cancelled):
            run(settings, cancelled=lambda: True)
        self.assertEqual(list(output.iterdir()), [])
        children = list(self.root.glob("cancelled_processing.partial"))
        self.assertEqual(children, [])

    def test_current_gui_format_and_embedded_identity_validation(self):
        spec = self.raw("current.txt", [(0, "A-1", TAG), (100, "A-1", TAG)])
        path = Path(spec.path)
        path.write_text(
            "\n".join(line + ";1" for line in path.read_text().splitlines()) + "\n"
        )
        self.assertEqual(len(self.process([spec])), 1)
        found = scan([str(path)], self.cfg, "Europe/Warsaw")
        self.assertEqual(found[0]["input"].port, "COM3")
        self.assertEqual(found[0]["input"].profile, "v1.5")
        spec.board_id = 2
        with self.assertRaisesRegex(ValueError, "Embedded board|embedded board"):
            self.process([spec], master_board=2)


    def test_many_late_reads_do_not_create_output_parts(self):
        specs = []
        for part in range(2):
            events = []
            for index in range(220):
                tick = part * 10000 + index * 10
                # USB arrival order differs from quartz-clock order.
                events.extend([(tick * 10, "A", TAG, tick),
                               (tick * 10 + 10, "B", OTHER, tick + 1),
                               (tick * 10 + 20, "10", "00001", tick - 1 if tick else 0)])
            specs.append(self.raw(f"part{part}.txt", events))
        data = self.process(specs, parquet=True, split_restarts=True)
        self.assertEqual(len(self.summary["clocks"]), 1)
        self.assertEqual(len({r["part"] for r in self.summary["recordings"]}), 2)
        self.assertGreater(self.summary["counts"]["out_of_order_counter_reads"], 400)
        self.assertEqual(len(self.result["outputs"]), 8)  # 2 parts x RFID/TTL x CSV/Parquet.
        self.assertEqual(len([row for row in data if row["antenna"].startswith("TTL")]), 440)
        self.assertEqual(len([row for row in data if not row["antenna"].startswith("TTL")]), 4)
        self.assertTrue(all("part01" in f["file"] or "part02" in f["file"] for f in self.result["outputs"]))
        data = self.process(specs, split_restarts=False, routes={"B-1": "extra"})
        self.assertEqual({f["file"] for f in self.result["outputs"]},
                         {"experiment.data.csv", "experiment.device_antenna1.csv", "experiment.TTL_data1.csv"})
        self.assertTrue(all(row["time_spent"] == "0" for row in data if row["antenna"].startswith("TTL")))

    def test_true_counter_resets_still_do_not_create_output_files(self):
        # To isolate actual resets from temporarily delayed rows use increasing
        # low counters after each reset, so the look-ahead cannot restore high water.
        events = [(0, "A", TAG, 10000)]
        for index in range(215):
            for offset in range(20):
                events.append((10000 + index * 60000 + offset * 100, "A", TAG, offset))
        self.process([self.raw("restarts_confirmed.txt", events)], counter_reorder_ms=0)
        self.assertGreater(len(self.summary["clocks"]), 200)
        self.assertEqual(len(self.result["outputs"]), 1)
        self.assertEqual(self.result["outputs"][0]["file"], "experiment.data.csv")

    def test_named_config_output_and_readable_repeated_run_directory(self):
        name = "WT_M_TSC2_12_10_2026"
        config = self.root / f"{name}.config.json"
        config.write_text(json.dumps(self.cfg))
        spec = self.raw("one.txt", [(0, "A", TAG), (100, "A", TAG)])
        self.process([spec], config_path=str(config))
        self.assertEqual(self.output, self.root / "out")
        self.assertEqual(self.result["outputs"][0]["file"], name + ".data.csv")
        first_hash = hashlib.sha256((self.output / (name + ".data.csv")).read_bytes()).hexdigest()
        self.process([spec], config_path=str(config))
        self.assertEqual(self.output, self.root / "out")
        self.assertEqual(hashlib.sha256((self.root / "out" / (name + ".data.csv")).read_bytes()).hexdigest(), first_hash)

    def test_scan_same_port_shares_clock_group_and_numbers_recording_parts(self):
        first = self.raw("first_COM3.txt", [(0, "A", TAG)])
        second = self.raw("second_COM3.txt", [(600000, "A", TAG)])
        entries = scan([second.path, first.path], self.cfg, "Europe/Warsaw")
        self.assertEqual([e["input"].group for e in entries], ["1", "1"])
        self.assertEqual([e["input"].part for e in entries], [1, 2])
        self.assertEqual([e["input"].port for e in entries], ["COM3", "COM3"])

    def test_shared_and_individual_routes_preserve_stitched_durations(self):
        spec = self.raw("three.txt", [(0, "A", TAG), (10, "B", TAG), (20, "C", OTHER),
                                      (100, "A", TAG), (110, "B", TAG), (120, "C", OTHER)])
        data = self.process([spec], routes={"B-1": "extras", "C-1": "extras"})
        self.assertEqual({f["file"] for f in self.result["outputs"]}, {"experiment.data.csv", "experiment.device_antenna1.csv"})
        self.assertEqual([int(row["time_spent"]) for row in data], [100, 100, 100])
        self.process([spec], routes={"B-1": "antenna_BCOM3", "C-1": "antenna_CCOM3"})
        self.assertEqual({f["file"] for f in self.result["outputs"]},
                         {"experiment.data.csv", "experiment.device_antenna1.csv", "experiment.device_antenna2.csv"})


if __name__ == "__main__":
    unittest.main()
