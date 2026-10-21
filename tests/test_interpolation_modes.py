"""Routing, compact diagnostics and GUI regressions, updated for Patch 8 modes."""
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests import test_preprocessing as support
from tests import test_metadata_and_gui as gui_support
from deepecohab_recorder.preprocessing.interpolation import PhysicalGraph
from deepecohab_recorder.preprocessing.models import Settings
from deepecohab_recorder.tabs.data_preprocessing import AdvancedOptions
from deepecohab_recorder.ui.preprocessing_outputs import OutputGroupsDialog


def cycle_layout():
    layout = support.linear_layout()
    layout["tunnels"].append({"name": "tunnel_3", "start_cell_id": "C3", "end_cell_id": "C1", "antennas": ["E-1", "F-1"], "dead_end": False})
    return layout


class InferenceTests(unittest.TestCase):
    setUp = support.PreprocessingTests.setUp
    tearDown = support.PreprocessingTests.tearDown
    raw = support.PreprocessingTests.raw
    process = support.PreprocessingTests.process
    details_of = support.PreprocessingTests.details_of
    training_events = support.PreprocessingTests.training_events

    def test_default_mode_and_legacy_switch(self):
        settings = Settings([], "unused")
        self.assertEqual(settings.effective_interpolation_mode, "standard")
        settings.interpolate = False
        self.assertEqual(settings.effective_interpolation_mode, "none")
        settings.interpolation_mode = "extended"
        settings.interpolate = True
        self.assertEqual(Settings.from_dict(settings.to_dict()).effective_interpolation_mode, "extended")
        settings.interpolation_mode = "guess"
        with self.assertRaisesRegex(ValueError, "Interpolation mode"):
            settings.validate()

    def test_known_missing_pass_has_standard_provenance(self):
        # The synthetic route deliberately omits B from an otherwise complete A-B-C-D traversal.
        rows = self.process([self.raw("linear.txt", self.training_events())], interpolate=True)
        inserts = self.inserted
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0]["method"]["inference_type"], "standard")
        self.assertEqual(self.summary["counts"]["interpolated_standard_periods"], 1)
        self.assertEqual(self.summary["counts"]["interpolated_extended_periods"], 0)
        self.assertEqual(rows[inserts[0]["row_number"]]["antenna"], "B-1")

    def test_cycle_modes_and_unique_route_heuristic(self):
        self.cfg["recording"]["layout"] = cycle_layout()
        spec = self.raw("cycle.txt", self.training_events())
        none = self.process([spec], interpolate=True, interpolation_mode="none")
        standard = self.process([spec], interpolate=True)
        self.assertEqual(self.summary["counts"]["interpolated_periods"], 1)
        extended = self.process([spec], interpolate=True, interpolation_mode="extended")
        self.assertEqual(standard, extended)
        inserts = self.inserted
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0]["antenna"], "B-1")
        self.assertEqual(inserts[0]["method"]["inference_type"], "standard")
        self.assertEqual(inserts[0]["method"]["route_method"], "unique_common_neighbour")
        self.assertEqual([r for i, r in enumerate(extended) if i != inserts[0]["row_number"]], none)
        self.assertEqual(self.summary["counts"]["interpolated_standard_periods"], 1)
        self.assertEqual(self.summary["counts"]["interpolated_extended_periods"], 0)

    def test_equal_routes_never_choose_arbitrarily(self):
        graph = PhysicalGraph(cycle_layout(), {1})
        self.assertEqual(graph.minimum_route("A-1", "D-1")[1], "tied")
        self.cfg["recording"]["layout"] = cycle_layout()
        self.process([self.raw("tie.txt", [(0, "A", support.TAG), (1000, "D", support.TAG)])], interpolate=True, interpolation_mode="extended")
        self.assertEqual(self.summary["counts"]["ambiguous_transitions"], 1)
        self.assertEqual(self.inserted, [])

    def test_two_missing_antennas_are_both_classified_as_extended(self):
        # Two alternative tunnels connect C1/C2, but C is unavoidable before D.
        layout = self.cfg["recording"]["layout"]
        layout["tunnels"].append({"name": "alternative", "start_cell_id": "C2", "end_cell_id": "C1", "antennas": ["E-1", "F-1"], "dead_end": False})
        events = self.training_events()[:-2] + [(3550, "D", support.TAG), (3600, "D", support.TAG)]
        self.process([self.raw("mixed_inference.txt", events)], interpolate=True, interpolation_mode="extended")
        inserted = self.inserted
        self.assertEqual({r["antenna"]: r["method"]["inference_type"] for r in inserted}, {"B-1": "extended", "C-1": "extended"})
        self.assertEqual(self.summary["counts"]["interpolated_standard_periods"], 0)
        self.assertEqual(self.summary["counts"]["interpolated_extended_periods"], 2)

    def test_excluded_sensor_does_not_remove_an_alternative_physical_route(self):
        graph = PhysicalGraph(cycle_layout(), {1}, {"E-1", "F-1"})
        self.assertEqual(graph.required("A-1", "C-1"), ((), "ambiguous"))
        self.assertEqual(graph.minimum_route("A-1", "C-1")[1], "tied")
        linear = PhysicalGraph(support.linear_layout(), {1}, {"B-1"})
        self.assertEqual(linear.required("A-1", "C-1"), ((), "direct"))
        self.assertEqual(linear.required("B-1", "B-1")[1], "unmapped_or_disconnected")

    def test_auxiliary_detections_do_not_break_main_transit_learning(self):
        events = self.training_events()
        events.extend((base + 100, "3", support.TAG) for base in (0, 1000, 2000, 3000))
        events.sort()
        self.process([self.raw("aux.txt", events, profile="v1.0")], interpolate=True, routes={"3-1": "lickometers"})
        inserts = self.inserted
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0]["antenna"], "B-1")
        self.assertEqual(inserts[0]["method"]["median_samples"], 3)
        self.assertEqual(self.summary["counts"]["RFID_periods_excluded_from_interpolation"], 4)
        self.assertFalse(any("3-1" in (r.get("from_antenna"), r.get("to_antenna")) for r in self.details_of("transit_median")))

    def test_routed_layout_sensor_is_never_inserted(self):
        rows = self.process([self.raw("excluded.txt", self.training_events())], interpolate=True, routes={"B-1": "separate"})
        self.assertEqual(self.inserted, [])
        self.assertEqual(len([r for r in rows if r["antenna"] == "B-1"]), 3)
        self.assertEqual(self.context["excluded_movement_inputs"], ["B-1"])

    def test_unknown_main_sensor_requires_explicit_role(self):
        spec = self.raw("unknown.txt", [(0, "A", support.TAG), (100, "3", support.TAG)], profile="v1.0")
        with self.assertRaisesRegex(ValueError, "3-1.*Manage RFID and TTL output grouping"):
            self.process([spec], interpolate=True)
        self.assertFalse((self.root / "out_processing.partial").exists())
        self.assertEqual(len(self.process([spec], interpolation_mode="none")), 2)
        self.assertEqual(len(self.process([spec], interpolate=True, routes={"3-1": "lickometers"})), 2)

    def test_ttl_and_auxiliary_outputs_identical_across_modes(self):
        self.cfg["recording"]["layout"] = cycle_layout()
        events = self.training_events() + [(100, "3", support.TAG), (150, "3", support.TAG), (110, "10", "00001"), (120, "10", "00001")]
        spec = self.raw("mixed.txt", sorted(events), profile="v1.0")
        snapshots = []
        for mode in ("none", "standard", "extended"):
            self.process([spec], interpolate=True, interpolation_mode=mode, routes={"3-1": "lickometers"}, parquet=True)
            outputs = {o["group"]: (self.output / o["file"]).read_bytes() for o in self.result["outputs"] if o["file"].endswith(".csv")}
            snapshots.append({g: outputs[g] for g in ("TTL_data1", "device_antenna1")})
            self.assertEqual(self.summary["counts"]["TTL_events"], 2)
            self.assertTrue(all(r["antenna"] != "3-1" and not r["antenna"].startswith("TTL") for r in self.inserted))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])
        ttl_rows = list(csv.DictReader(snapshots[0]["TTL_data1"].decode().splitlines()))
        self.assertEqual([r["time_spent"] for r in ttl_rows], ["0", "0"])
        auxiliary = list(csv.DictReader(snapshots[0]["device_antenna1"].decode().splitlines()))
        self.assertEqual([r["time_spent"] for r in auxiliary], ["50"])

    def test_extended_still_obeys_timing_and_recording_boundaries(self):
        self.cfg["recording"]["layout"] = cycle_layout()
        short = self.training_events()[:-2] + [(3100, "C", support.TAG), (3150, "C", support.TAG)]
        self.process([self.raw("short.txt", short)], interpolate=True, interpolation_mode="extended")
        self.assertEqual(self.inserted, [])
        self.assertGreater(self.summary["counts"]["interpolation_timing_or_coverage_conflicts"], 0)
        train = self.raw("train.txt", self.training_events()[:-2])
        restart = self.raw("restart.txt", [(3400, "C", support.TAG)], group="2")
        self.process([train, restart], interpolate=True, interpolation_mode="extended", split_restarts=False)
        self.assertEqual(self.inserted, [])
        self.assertGreater(self.summary["counts"]["interpolation_blocked_by_recording_boundary"], 0)

    def test_compact_single_report_and_inserted_row_references(self):
        self.process([self.raw("report.txt", self.training_events())], interpolate=True, parquet=True)
        self.assertEqual(sorted(p.name for p in self.output.iterdir() if p.suffix not in (".csv", ".parquet")), ["DATATYPE READ ME.txt"])
        self.assertNotIn("input_config", self.summary)
        self.assertNotIn("effective_config", self.summary)
        self.assertNotIn("settings", self.summary)
        self.assertEqual(len(self.inserted), 1)
        for inserted in self.inserted:
            for name in inserted["output_files"]:
                path = self.output / name
                if path.suffix == ".csv":
                    with path.open() as f:
                        row = list(csv.DictReader(f))[inserted["row_number"]]
                    self.assertEqual(row["datetime"], inserted["datetime"])
                else:
                    table = support.pq.read_table(path)
                    self.assertEqual(table.schema.metadata[b"diagnostics"], b"../experiment.diagnostic.json")
                    row = table.to_pylist()[inserted["row_number"]]
                    self.assertEqual(row["datetime"], datetime.fromisoformat(inserted["datetime"]))
                self.assertEqual(row["animal_id"], inserted["animal_id"])
                self.assertEqual(row["antenna"], inserted["output_antenna"])

    def test_examples_bounded_but_counts_not_truncated(self):
        spec = self.raw("noise.txt", [(0, "A", support.TAG)])
        with Path(spec.path).open("a") as f:
            f.write("invalid row\n" * 1100)
        self.process([spec])
        self.assertEqual(self.summary["counts"]["malformed_rows"], 1100)
        self.assertEqual(self.summary["issues"]["malformed_record"]["count"], 1100)
        self.assertEqual(len(self.summary["issues"]["malformed_record"]["examples"]), 5)


class InferenceGuiTests(unittest.TestCase):
    setUpClass = classmethod(gui_support.Patch6GuiTests.setUpClass.__func__)
    setUp = gui_support.Patch6GuiTests.setUp
    tearDown = gui_support.Patch6GuiTests.tearDown
    prepare_inputs = gui_support.Patch6GuiTests.prepare_inputs

    def test_three_modes_warning_and_explicit_output_roles(self):
        tab = self.prepare_inputs()
        advanced = AdvancedOptions(tab)
        self.assertEqual(advanced.interpolation.count(), 3)
        self.assertEqual(advanced.interpolation.currentData(), "standard")
        for mode in ("extended", "none", "standard"):
            advanced.interpolation.setCurrentIndex(advanced.interpolation.findData(mode))
            values, clocks = advanced.values()
            self.assertEqual(values["interpolation_mode"], mode)
            self.assertEqual(values["interpolate"], mode != "none")
            if mode == "extended":
                self.assertIn("incorrect antenna detections", advanced.interpolation_warning.text())
        groups = OutputGroupsDialog(tab)
        row = groups.sources.index("A-1")
        self.assertEqual(groups.table.cellWidget(row, 2).currentText(), "Layout default")
        groups.table.selectRow(row)
        groups.rfid.group_together()
        self.assertEqual(groups.table.cellWidget(row, 2).currentText(), "device_antenna1")
        self.assertEqual(groups.values(), {"A-1": "device_antenna1"})
        groups.close()
        advanced.close()
