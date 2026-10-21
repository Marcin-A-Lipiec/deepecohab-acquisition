from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from deepecohab_recorder.schema import migrate_config
from deepecohab_recorder.services.application import ApplicationServices
from tests.helpers import valid_config, SerialFactory


class AdditionalRegressions(unittest.TestCase):
    def test_legacy_nested_zero_duration_and_camera_alias(self):
        old = {
            "version": 4,
            "experiment": {"name": "Old"},
            "camera": {"target_width": 640, "trigger_delay_us": 0},
            "hardware_trigger": {"duration_dd_hh_mm": {"dd": 0, "hh": 2, "mm": 0}},
        }
        result = migrate_config(old)
        self.assertEqual(result["acquisition"]["camera"]["width"], 640)
        self.assertEqual(result["acquisition"]["camera"]["trigger_delay_us"], 0)
        self.assertEqual(result["acquisition"]["hardware_trigger"]["duration_days"], 0)
        self.assertEqual(result["acquisition"]["hardware_trigger"]["duration_hours"], 2)

    def test_switch_experiment_detaches_old_runtime(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, serial=SerialFactory([b"A;D5969C1A;01\n"])
        ):
            services = ApplicationServices()
            first = str(Path(directory) / "first")
            services.set_context("First", first, "Europe/Warsaw")
            cfg = valid_config()
            services.repo.replace(cfg)
            services.start_devices(["board1"], cfg["acquisition"], override=True)
            services._future.result(10)
            self.assertTrue(services.run.start)
            services.stop_devices()
            services._future.result(10)
            services.set_context(
                "Second", str(Path(directory) / "second"), "Europe/Warsaw"
            )
            services.repo.replace(valid_config())
            services.run.save_metadata({"recording.recording_location": "Second room"})
            self.assertIsNone(services.run.run_id)
            self.assertIsNone(
                services.repo.data["recording"]["timeline"]["start_datetime"]
            )
            services.shutdown()

    def test_camera_ready_precedes_trigger_and_settings_are_frozen(self):
        operations = []

        class FakeCamera:
            def __init__(self, config=None):
                self.config, self.active, self.ready, self.last_error = (
                    config,
                    False,
                    threading.Event(),
                    "",
                )
                self.started_iso = self.stopped_iso = ""

            def is_running(self):
                return self.active

            def start(self, session, external_trigger):
                self.active = True
                operations.append("camera_initializing")

                def complete():
                    operations.append("camera_ready")
                    self.ready.set()

                threading.Timer(0.1, complete).start()

            def request_stop(self):
                self.active = False

            def stop(self):
                self.active = False

        class FakeTrigger(FakeCamera):
            def start(self, session, config):
                self.config = config
                self.active = True
                operations.append(("trigger", config.frequency_hz))
                self.ready.set()

        with tempfile.TemporaryDirectory() as directory, patch(
            "deepecohab_recorder.services.application.CameraController", FakeCamera
        ), patch(
            "deepecohab_recorder.services.application.HardwareTriggerController",
            FakeTrigger,
        ):
            services = ApplicationServices()
            services.set_context("Test", directory, "Europe/Warsaw")
            settings = valid_config()["acquisition"]
            settings["hardware_trigger"]["com_port"] = "COM9"
            settings["launch"]["delay_seconds"] = 0
            services.start_devices(["trigger", "camera"], settings)
            settings["hardware_trigger"]["frequency_hz"] = 99
            services._future.result(5)
            self.assertEqual(
                operations, ["camera_initializing", "camera_ready", ("trigger", 30.0)]
            )
            self.assertIsNone(
                services.repo.data["recording"]["timeline"]["start_datetime"]
            )
            services.stop_devices()
            services._future.result(5)
            services.shutdown()

    def test_late_event_save_keeps_original_run_end_on_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, serial=SerialFactory([b"A;D5969C1A;01\n", b"B;D5969C1A;02\n"])
        ):
            services = ApplicationServices()
            services.set_context("Test", directory, "Europe/Warsaw")
            cfg = valid_config()
            services.repo.replace(cfg)
            services.start_devices(["board1"], cfg["acquisition"], override=True)
            services._future.result(10)
            services.stop_devices()
            services._future.result(10)
            first_end, first_run = services.run.end, services.run.run_id
            events = {
                "event_types": [{"name": "stim", "description": ""}],
                "bouts": [
                    {"name": "stim", "start": "2026-01-01T00:00:00+01:00", "end": None}
                ],
            }
            services.run.save_metadata({"recording.events": events})
            services.start_devices(["board1"], cfg["acquisition"], services.repo.data)
            services._future.result(10)
            services.stop_devices()
            services._future.result(10)
            bout = services.repo.data["recording"]["events"]["bouts"][0]
            self.assertEqual(bout["end"], first_end)
            self.assertEqual(bout["finalized_run_id"], first_run)
            services.shutdown()

    def test_serial_diagnostics_flush_during_idle_recording(self):
        from deepecohab_recorder.controllers.rfid import RFIDBoardController
        from deepecohab_recorder.models import BoardConfig, DAQ_V15
        from tests.helpers import snapshot, wait_for

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, serial=SerialFactory([b"A;D5969C1A;01\n"])
        ):
            board = RFIDBoardController(BoardConfig(1, True, DAQ_V15, "COM5"))
            board.start(snapshot(directory))
            try:
                wait_for(lambda: board.data_file.stat().st_size > 0, 3)
                self.assertTrue(board.is_running())
            finally:
                board.stop()
