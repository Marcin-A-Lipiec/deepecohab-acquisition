from tests.helpers import settle_discovery
"""Agreed local repair policy: single missing neighbours, no maximum time gap."""
import json
from tests.helpers import preprocessing_context
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests import test_preprocessing as helper
from deepecohab_recorder.preprocessing.interpolation import PhysicalGraph
from deepecohab_recorder.preprocessing.models import Settings


def four_cages():
    return {
        "cages": [{"cell_id": f"C{i}", "name": f"cage_{i}"} for i in range(1, 5)],
        "tunnels": [
            {"name": f"tunnel_{i}", "start_cell_id": f"C{i}", "end_cell_id": f"C{i % 4 + 1}", "antennas": [a + "-1", b + "-1"], "dead_end": False}
            for i, (a, b) in enumerate((("1", "E"), ("6", "D"), ("B", "5"), ("2", "8")), 1)
        ],
        "junctions": [],
    }


class LocalRuleTests(unittest.TestCase):
    setUp = helper.PreprocessingTests.setUp
    tearDown = helper.PreprocessingTests.tearDown
    raw = helper.PreprocessingTests.raw
    process = helper.PreprocessingTests.process
    training_events = helper.PreprocessingTests.training_events

    def test_exact_user_examples_ignore_longer_detour(self):
        graph = PhysicalGraph(four_cages(), {1}, {"3-1"})
        for mode in ("standard", "extended"):
            for start, end, expected in (
                ("6-1", "D-1", ((), "direct")),
                ("D-1", "B-1", ((), "direct")),
                ("D-1", "5-1", (("B-1",), "single_missing")),
                ("6-1", "1-1", (("E-1",), "single_missing")),
                ("6-1", "6-1", ((), "direct")),
            ):
                self.assertEqual(graph.missing_route(start, end, mode), expected)
        # The old global guarantee query is no longer the repair policy.
        self.assertEqual(graph.required("D-1", "5-1"), ((), "ambiguous"))

    def test_two_equally_possible_single_missing_antennas_are_skipped(self):
        layout = {
            "cages": [{"cell_id": "C1"}, {"cell_id": "C2"}],
            "tunnels": [
                {"name": "tunnel_1", "start_cell_id": "C1", "end_cell_id": "C2", "antennas": ["A-1", "B-1"]},
                {"name": "tunnel_2", "start_cell_id": "C2", "end_cell_id": "C1", "antennas": ["C-1", "D-1"]},
            ],
        }
        self.cfg["recording"]["layout"] = layout
        graph = PhysicalGraph(layout, {1})
        for mode in ("standard", "extended"):
            self.assertEqual(graph.missing_route("A-1", "C-1", mode), ((), "ambiguous"))
            self.process([self.raw("ambiguous.txt", [(0, "A", helper.TAG), (1000, "C", helper.TAG)])], interpolate=True, interpolation_mode=mode)
            self.assertEqual(self.summary["counts"]["ambiguous_transitions"], 1)
            self.assertEqual(self.inserted, [])

    def test_five_hour_gap_in_chain_and_cycle_uses_previous_last_read(self):
        for circular in (False, True):
            self.cfg["recording"]["layout"] = helper.linear_layout()
            if circular:
                self.cfg["recording"]["layout"]["tunnels"].append({"name": "loop", "start_cell_id": "C3", "end_cell_id": "C1", "antennas": ["E-1", "F-1"]})
            events = self.training_events()[:24]
            # X ends at 12:01; Z begins at 17:01. Transit samples give X->Y = 100 ms.
            events += [(59950, "A", helper.TAG), (60000, "A", helper.TAG), (18060000, "C", helper.TAG), (18060050, "C", helper.TAG)]
            self.process([self.raw(f"five_hours_{circular}.txt", events)], interpolate=True)
            inserts = self.inserted
            self.assertEqual(len(inserts), 1)
            self.assertEqual(inserts[0]["antenna"], "B-1")
            self.assertEqual(inserts[0]["datetime"], (helper.BASE + timedelta(milliseconds=60100)).isoformat(timespec="microseconds"))
            self.assertEqual(inserts[0]["method"]["transit_ms"], 100)
            self.assertEqual(inserts[0]["method"]["median_samples"], 3)
            self.assertEqual(inserts[0]["method"]["inference_type"], "standard")

    def test_standard_never_recursively_fills_two_missing_antennas(self):
        events = self.training_events()[:-2] + [(3550, "D", helper.TAG), (3600, "D", helper.TAG)]
        spec = self.raw("two_missing.txt", events)
        self.process([spec], interpolate=True)
        self.assertEqual(self.inserted, [])
        self.assertGreater(self.summary["counts"]["interpolation_skipped_requires_extended"], 0)
        self.process([spec], interpolate=True, interpolation_mode="extended")
        inserts = self.inserted
        self.assertEqual({r["antenna"] for r in inserts}, {"B-1", "C-1"})
        self.assertEqual(self.summary["counts"]["interpolated_standard_periods"], 0)
        self.assertEqual(self.summary["counts"]["interpolated_extended_periods"], 2)

    def test_old_option_names_normalize_and_disabled_stays_disabled(self):
        spec = self.raw("options.txt", self.training_events())
        for old, new in (("guaranteed", "standard"), ("plausible", "extended")):
            settings = Settings.from_dict({"inputs": [spec.__dict__], "output_dir": str(self.root / "out"), "interpolation_mode": old})
            self.assertEqual(settings.interpolation_mode, new)
            self.assertEqual(settings.effective_interpolation_mode, new)
            settings.interpolate = False
            self.assertEqual(settings.effective_interpolation_mode, "none")
        settings = Settings.from_dict({"inputs": [spec.__dict__], "output_dir": str(self.root / "out")})
        self.assertEqual(settings.effective_interpolation_mode, "standard")
        self.assertEqual(Settings.from_dict(settings.to_dict()).to_dict(), settings.to_dict())

    def test_neighboring_or_same_antennas_never_trigger_an_insertion(self):
        self.cfg["recording"]["layout"] = four_cages()
        events = [(0, "6", helper.TAG), (1000, "6", helper.TAG), (2000, "D", helper.TAG), (3000, "B", helper.TAG)]
        self.process([self.raw("neighbors.txt", events, port="COM5", profile="v1.0")], interpolate=True)
        self.assertEqual(self.inserted, [])
        self.assertEqual(self.summary["counts"]["interpolation_direct_transitions"], 3)

    def test_unknown_and_disconnected_antennas_are_not_repaired(self):
        layout = helper.linear_layout()
        layout["cages"] += [{"cell_id": "C4"}, {"cell_id": "C5"}]
        layout["tunnels"].append({"name": "isolated", "start_cell_id": "C4", "end_cell_id": "C5", "antennas": ["E-1", "F-1"]})
        graph = PhysicalGraph(layout, {1})
        self.assertEqual(graph.missing_route("A-1", "E-1"), ((), "unmapped_or_disconnected"))
        self.assertEqual(graph.missing_route("missing", "A-1"), ((), "unmapped_or_disconnected"))

    def test_other_mouse_detections_do_not_supply_or_interrupt_transits(self):
        events = self.training_events()
        events += [(3100, "B", helper.OTHER), (3150, "B", helper.OTHER)]
        self.process([self.raw("two_mice.txt", sorted(events))], interpolate=True)
        inserts = self.inserted
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0]["animal_id"], helper.TAG)
        self.assertEqual(inserts[0]["datetime"], (helper.BASE + timedelta(milliseconds=3150)).isoformat(timespec="microseconds"))
        self.assertEqual(inserts[0]["method"]["median_samples"], 3)


class DefaultWorkerGuiTests(unittest.TestCase):
    from tests.test_metadata_and_gui import Patch6GuiTests as _gui
    setUpClass = classmethod(_gui.setUpClass.__func__)
    setUp = _gui.setUp
    tearDown = _gui.tearDown

    def test_default_standard_mode_reaches_background_worker(self):
        self.cfg["recording"]["layout"] = helper.linear_layout()
        self.cfg["recording"]["cohort"]["animals"][0]["tag"] = helper.TAG
        self.cfg["acquisition"]["boards"]["board_1"].update(com_port="COM3", hardware="DeepEcoHAB RFID DAQ v1.5")
        self.window.services.repo.replace(self.cfg)
        self.window.services.repo.save()
        self.window._apply(self.window.services.repo.data)
        (self.root / "raw_antenna_reads").mkdir()
        helper.PreprocessingTests.raw(self, "raw_antenna_reads/Test_COM3.csv", helper.PreprocessingTests.training_events(self))
        tab = self.window.widgets["preprocessing"]
        tab.configure_experiment(force=True)
        settle_discovery(tab)
        self.assertEqual(tab.options["interpolation_mode"], "standard")
        self.assertTrue(tab.options["interpolate"])
        tab.csv.setChecked(True)
        tab.start()
        self.assertTrue(tab.process.waitForFinished(15000))
        self.app.processEvents()
        self.assertEqual(tab.process.exitCode(), 0, tab.log.toPlainText())
        self.assertEqual(tab._job_result["counts"]["interpolated_standard_periods"], 1)
        self.assertEqual(tab._job_result["counts"]["interpolated_extended_periods"], 0)
        report = json.loads(Path(tab._job_result["diagnostic_path"]).read_text())
        self.assertEqual(preprocessing_context(report, tab._job_result['diagnostic_path'])['options']["interpolation_mode"], "standard")
        self.assertIn("Standard: 1", tab.log.toPlainText())
        self.assertFalse(self.warning.called, self.warning.call_args)
