import copy
import json
from pathlib import Path
import tempfile
import unittest
from deepecohab_recorder.clock import ExperimentClock, phase_origin
from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.models import DAQ_V15, NEW_DAQ, OLD_DAQ, TriggerConfig
from deepecohab_recorder.protocols.rfid import protocol_for_hardware
from deepecohab_recorder.protocols.lickometer import LickometerProtocol
from deepecohab_recorder.controllers.trigger import trigger_timing
from deepecohab_recorder.schema import (
    migrate_config,
    normalize_animals,
    normalize_events,
    validate_config,
)
from deepecohab_recorder.topology import normalize_layout, validate_layout
from tests.helpers import valid_config


class ConfigTests(unittest.TestCase):
    def test_v2_example_migration_and_direct_maps(self):
        path = Path(__file__).parent / "fixtures/legacy_v2.json"
        source = json.loads(path.read_text())
        original = copy.deepcopy(source)
        cfg = migrate_config(source)
        self.assertEqual(source, original)
        self.assertEqual(cfg["version"], 8)
        self.assertIn("acquisition", cfg)
        self.assertNotIn("acqusition", cfg)
        self.assertEqual(len(cfg["recording"]["cohort"]["animals"]), 12)
        self.assertEqual(len(cfg["recording"]["layout"]["antenna_combinations"]), 24)
        self.assertIn("events", cfg["recording"])
        self.assertNotIn(
            "antenna_combinations_interpolated", cfg["recording"]["layout"]
        )
        self.assertEqual(migrate_config(cfg), cfg)

    def test_legacy_v4_notice_and_canonical_save(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = ConfigRepository(lambda: "Test", lambda: directory)
            source = Path(__file__).parent / "experiment_config.json"
            before = source.read_bytes()
            cfg = repo.import_file(source)
            self.assertIn("legacy", repo.notice)
            self.assertEqual(cfg["version"], 8)
            repo.save()
            self.assertTrue(repo.preflight()[1])  # An incomplete archive remains a valid saved draft.
            repo.update_paths({"recording.cohort": valid_config()["recording"]["cohort"]})
            paths = repo.save()
            self.assertEqual(paths[1].name, "Test.config.json")
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(json.loads(paths[1].read_text())["version"], 8)
            self.assertEqual(repo.notice, "")
            changed = repo.data
            changed["recording"]["name"] = "external mutation"
            self.assertNotEqual(repo.data["recording"]["name"], "external mutation")

    def test_animal_minimum_and_duplicate_preservation(self):
        cfg = valid_config()
        self.assertEqual(validate_config(cfg), [])
        cohort = cfg["recording"]["cohort"]
        cohort["animals"].append(copy.deepcopy(cohort["animals"][0]))
        cohort["n_mice"] = 2
        self.assertEqual(len(normalize_animals(cohort)["animals"]), 2)
        self.assertTrue(any("duplicate" in error for error in validate_config(cfg)))
        cohort["animals"][1]["tag"] = "3F5DEC1A04"
        cohort["animals"][1]["date_of_birth"] = ""
        self.assertTrue(any("birth" in error for error in validate_config(cfg)))

    def test_preflight_missing_malformed_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = ConfigRepository(save_dir_getter=lambda: directory)
            self.assertTrue(repo.preflight()[1])
            path = Path(directory) / "config.json"
            path.write_text("{bad json")
            self.assertTrue(repo.preflight()[1])
            self.assertEqual(path.read_text(), "{bad json")
            repo.replace(valid_config())
            repo.save()
            self.assertEqual(repo.preflight()[1], [])
            repo.update_paths({"recording.recording_location": "room 2"})
            repo.save()
            self.assertFalse((Path(directory) / "session_configs").exists())

    def test_unknown_fields_preserved_and_future_rejected(self):
        cfg = valid_config()
        cfg["custom"] = {"extension": [1, 2]}
        cfg["recording"]["cohort"]["animals"][0]["custom_key"] = 5
        out = migrate_config(cfg)
        self.assertEqual(out["custom"], cfg["custom"])
        self.assertEqual(out["recording"]["cohort"]["animals"][0]["custom_key"], 5)
        cfg["version"] = 9
        with self.assertRaises(ValueError):
            migrate_config(cfg)

    def test_dead_end_topology_without_inferred_pairs(self):
        cfg = valid_config()
        graph = cfg["recording"]["layout"]
        before = graph["antenna_combinations"].copy()
        graph["junctions"] = [
            {"id": "junction_1", "parent_tunnel": "tunnel_1", "position": 0.5}
        ]
        graph["tunnels"].append(
            {
                "name": "tunnel_5",
                "tunnel_no": 5,
                "start_cell_id": "junction_1",
                "end_cell_id": None,
                "dead_end": True,
                "antennas": ["P-1"],
            }
        )
        graph = normalize_layout(graph)
        self.assertEqual(graph["antenna_combinations"], before)
        self.assertEqual(validate_layout(graph, cfg["acquisition"]["boards"]), [])
        graph["junctions"][0]["parent_tunnel"] = "tunnel_5"
        self.assertTrue(any("Cyclic" in error for error in validate_layout(graph)))


class ClockEventTests(unittest.TestCase):
    def test_phase_examples_and_midnight(self):
        phases = {"light_phase": "00:00", "dark_phase": "12:00"}
        same = phase_origin(
            "2026-09-01T13:20:00+02:00", "Europe/Warsaw", phases, "dark_phase"
        )
        self.assertEqual(same["missing_initial_seconds"], 4800)
        self.assertEqual(same["analysis_start_datetime"], "2026-09-01T12:00:00+02:00")
        following = phase_origin(
            "2026-09-01T09:00:00+02:00", "Europe/Warsaw", phases, "dark_phase"
        )
        self.assertEqual(following["recorded_before_origin_seconds"], 10800)
        other = phase_origin(
            "2026-09-01T02:00:00+02:00",
            "Europe/Warsaw",
            {"light_phase": "08:00", "dark_phase": "20:00"},
            "dark_phase",
        )
        self.assertEqual(other["analysis_start_datetime"], "2026-08-31T20:00:00+02:00")

    def test_dst_elapsed_time_and_ambiguous_input(self):
        origin = phase_origin(
            "2026-03-29T04:00:00+02:00",
            "Europe/Warsaw",
            {"light_phase": "08:00", "dark_phase": "20:00"},
            "dark_phase",
        )
        self.assertEqual(origin["missing_initial_seconds"], 8 * 3600)
        clock = ExperimentClock()
        with self.assertRaises(ValueError):
            clock.parse("2026-10-25T02:30:00", allow_naive=True)
        with self.assertRaises(ValueError):
            clock.parse("2026-03-29T02:30:00", allow_naive=True)
        self.assertIsNotNone(clock.parse("2026-10-25T02:30:00+01:00").utcoffset())

    def test_event_default_end_and_flag(self):
        events = {
            "event_types": [{"name": "stim", "description": "social"}],
            "bouts": [
                {"name": "stim", "start": "2026-09-01T13:00:00", "end": None},
                {
                    "name": "stim",
                    "start": "2026-09-01T14:00:00+02:00",
                    "end": "2026-09-01T15:00:00+02:00",
                },
            ],
        }
        parsed = normalize_events(events, ExperimentClock())
        self.assertIsNone(parsed["bouts"][0]["end"])
        parsed = normalize_events(
            parsed, ExperimentClock(), "2026-09-01T16:00:00+02:00"
        )
        self.assertEqual(parsed["bouts"][0]["end"], "2026-09-01T16:00:00+02:00")
        self.assertFalse(parsed["bouts"][0]["end_user_specified"])
        self.assertTrue(parsed["bouts"][1]["end_user_specified"])
        self.assertEqual(parsed["bouts"][1]["end"], "2026-09-01T15:00:00+02:00")
        parsed["bouts"][1]["end"] = "2026-09-01T10:00:00+02:00"
        with self.assertRaises(ValueError):
            normalize_events(parsed, ExperimentClock())


class ProtocolTests(unittest.TestCase):
    def test_profiles_and_ttl_payloads(self):
        v15 = protocol_for_hardware(DAQ_V15)
        for channel in "ABCDEFGHIJKLMNOP":
            self.assertEqual(
                v15.parse(
                    f"{channel};D5969C1A;07DF6948;2026-07-19T01:21:25.840800".encode()
                ).logical_channel,
                channel,
            )
        for channel in range(1, 5):
            self.assertEqual(
                v15.parse(f"10;0000000{channel};07DF6948".encode()).logical_channel,
                f"TTL{channel}",
            )
        for payload in ("00000000", "00000005", "02000000", "garbage"):
            with self.assertRaises(ValueError):
                v15.parse(f"10;{payload};07DF6948".encode())
        with self.assertRaises(ValueError):
            v15.parse(b"6;D5969C1A;07DF6948")
        old = protocol_for_hardware(OLD_DAQ)
        self.assertEqual(old.parse(b"0-0000001234-0123456789").logical_channel, "1")
        new = protocol_for_hardware(NEW_DAQ)
        self.assertEqual(new.parse(b"10;02000000;0197266A").logical_channel, "TTL2")

    def test_entire_lickometer_replay_after_reset(self):
        protocol = LickometerProtocol()
        events = 0
        path = (
            Path(__file__).parent
            / "experiment_20260716_005009_lickometer1_raw_serial.log"
        )
        for line in path.read_text().splitlines():
            _, text = line.split(";", 1)
            if text.startswith("PARSE_ERROR="):
                continue  # Legacy logger annotations repeat the original input; they are not device messages.
            record = protocol.parse(text)
            events += record.kind == "EVENT"
        self.assertEqual(events, 872)
        self.assertGreater(protocol.epoch, 0)

    def test_invalid_event_does_not_commit_clock(self):
        protocol = LickometerProtocol()
        protocol.parse("EVENT,0,100,1,1")
        with self.assertRaises(ValueError):
            protocol.parse("EVENT,0,999999999,7,1")
        self.assertEqual(protocol.parse("EVENT,0,101,1,0").time_us, 101)
        protocol.parse("BOOT,0,1,TEST")
        self.assertEqual(protocol.parse("EVENT,0,2,1,1").epoch, 1)

    def test_trigger_quantized_timing_and_uint32(self):
        cfg = TriggerConfig(frequency_hz=29.97, duration_days=0, duration_hours=1)
        timing = trigger_timing(cfg)
        self.assertEqual(timing["frequency_hz"], 1_000_000 / timing["period_us"])
        self.assertLessEqual(timing["train_length_s"], 3600)
        cfg.train_mode = "count"
        cfg.count = 2**32
        with self.assertRaises(ValueError):
            trigger_timing(cfg)
