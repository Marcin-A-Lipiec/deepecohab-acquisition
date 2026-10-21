from deepecohab_recorder.config_bundle import load_config_bundle
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from deepecohab_recorder.controllers.rfid import RFIDBoardController
from deepecohab_recorder.controllers.lickometer import LickometerController
from deepecohab_recorder.models import BoardConfig, LickometerConfig, DAQ_V15
from deepecohab_recorder.services.application import ApplicationServices
from deepecohab_recorder.services.matching import MatchSession
from tests.helpers import SerialFactory, FakeSerial, wait_for, snapshot, valid_config


class SerialIOTests(unittest.TestCase):
    def test_csv_raw_bytes_and_queue_drain(self):
        raw = (
            b"P;D5969C1A;07DF6948\r\n10;00000002;07DF6950\n" * 250
            + b"bad\xffline\npartial tail"
        )
        factory = SerialFactory([raw])
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, serial=factory
        ):
            board = RFIDBoardController(BoardConfig(1, True, DAQ_V15, "COM5"))
            board.start(snapshot(directory))
            wait_for(lambda: board.lines_ok == 500 and board.lines_bad == 1)
            board.stop()
            self.assertFalse(board.is_running())
            self.assertFalse(factory.instances[0].is_open)
            self.assertEqual(board.lines_written, 500)
            lines = board.data_file.read_text().splitlines()
            self.assertEqual(len(lines), 500)
            self.assertTrue(lines[0].startswith("P-1;"))
            self.assertTrue(lines[1].startswith("TTL2-1;"))
            self.assertIn("+02:00", lines[0])
            self.assertEqual(board.data_file.parent.name, "raw_antenna_reads")
            self.assertIsNone(board.raw_file)
            self.assertFalse((Path(directory) / "diagnostic_logs").exists())
            self.assertEqual(len(board.malformed_examples), 2)
            self.assertEqual(base64.b64decode(board.malformed_examples[0]['data_b64']), b'bad\xffline')

    def test_disconnect_closes_port_and_allows_restart(self):
        broken = FakeSerial(fail_read=True)
        factory = SerialFactory([broken, b"A;D5969C1A;01\n"])
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, serial=factory
        ):
            board = RFIDBoardController(BoardConfig(1, True, DAQ_V15, "COM5"))
            board.start(snapshot(directory))
            wait_for(lambda: not board.is_running())
            self.assertFalse(broken.is_open)
            self.assertIn("disconnect", board.last_error)
            first = board.data_file
            board.start(snapshot(directory))
            wait_for(lambda: board.lines_ok == 1)
            board.stop()
            self.assertNotEqual(first, board.data_file)
            self.assertTrue(first.exists())

    def test_diagnostic_open_failure_precedes_serial_initialization(self):
        factory = SerialFactory([b""])
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, serial=factory
        ), patch(
            "deepecohab_recorder.controllers.serial_base.Path.open",
            side_effect=OSError("disk unavailable"),
        ):
            board = RFIDBoardController(BoardConfig(1, True, DAQ_V15, "COM5"))
            with self.assertRaises(RuntimeError):
                board.start(snapshot(directory))
            self.assertEqual(factory.instances, [])
            self.assertFalse(board.is_running())

    def test_lickometer_writer_and_autonomous_reset_boundary(self):
        factory = SerialFactory(
            [b"BOOT,0,1,TEST\nEVENT,0,3,2,1\nBOOT,0,1,TEST\nEVENT,0,4,2,0\n"]
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, serial=factory
        ):
            controller = LickometerController(LickometerConfig(True, "COM7"))
            controller.start(snapshot(directory))
            wait_for(lambda: controller.lines_ok == 4)
            controller.stop()
            self.assertGreaterEqual(controller.lines_written, 4)
            self.assertEqual(len(controller.data_file.read_text().splitlines()), 2)
            self.assertEqual(controller.protocol.epoch, 1)
            self.assertIn("clock_epoch", controller.status_file.read_text())
            self.assertEqual(factory.instances[0].writes, [])


class AcknowledgedTriggerSerial(FakeSerial):
    def __init__(self):
        super().__init__(b"ACQ_TRIGGER_V2\n")

    def write(self, data):
        result = super().write(data)
        if data.startswith(b"ARMUS"):
            self.buffer.extend(b"ARMED\nSTART\n")
        elif data == b"STOP\n":
            self.buffer.extend(b"STOPPED\n")
        return result


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.services = ApplicationServices()
        self.services.set_context("Test", self.temp.name, "Europe/Warsaw")
        self.cfg = valid_config()
        self.cfg["acquisition"]["boards"]["board_2"]["active"] = True
        self.cfg["acquisition"]["hardware_trigger"].update(com_port="COM7")
        self.services.repo.replace(self.cfg)
        self.services.repo.save()

    def tearDown(self):
        self.services.stop_devices()
        wait_for(lambda: not self.services.busy(), 10)
        self.services.shutdown()
        self.temp.cleanup()

    def await_operation(self):
        self.services._future.result(timeout=10)

    def test_rfids_control_time_peripheral_does_not(self):
        factory = SerialFactory(
            [
                AcknowledgedTriggerSerial(),
                b"A;D5969C1A;01\n",
                b"B;D5969C1A;02\n",
                b"C;D5969C1A;03\n",
            ]
        )
        with patch.dict(sys.modules, serial=factory):
            settings = self.cfg["acquisition"]
            settings["launch"]["delay_seconds"] = 0
            settings["launch"]["externally_triggered"] = False
            self.services.start_devices(["trigger"], settings)
            self.await_operation()
            self.assertIsNone(
                self.services.repo.data["recording"]["timeline"]["start_datetime"]
            )
            self.services.start_devices(["board1"], settings, self.cfg)
            self.await_operation()
            first = self.services.run.start
            first_run = self.services.run.run_id
            self.assertEqual(first, self.services.controllers["board1"].started_iso)
            self.services.start_devices(["board2"], settings, self.cfg)
            self.await_operation()
            self.assertEqual(self.services.run.start, first)
            self.services.stop_devices(["board1"])
            self.await_operation()
            self.assertIsNone(self.services.run.end)
            self.services.stop_devices(["board2"])
            self.await_operation()
            end = self.services.run.end
            self.assertTrue(end)
            self.assertTrue(self.services.busy("trigger"))
            self.services.start_devices(["board1"], settings, self.services.repo.data)
            self.await_operation()
            self.assertNotEqual(self.services.run.run_id, first_run)
            self.assertNotEqual(self.services.run.start, first)
            self.services.stop_devices()
            self.await_operation()
            self.assertFalse(self.services.ports.snapshot())
            cfg = load_config_bundle(Path(self.temp.name) / "Test.config.json")
            self.assertEqual(cfg["acquisition"]["com_ports"]["COM5"], "RFID_DAQ_1")

    def test_override_journal_then_late_config_and_event_end(self):
        path = Path(self.temp.name) / "Test.config.json"
        path.unlink()
        events = {
            "event_types": [{"name": "stim", "description": ""}],
            "bouts": [
                {"name": "stim", "start": "2026-01-01T12:00:00+01:00", "end": None}
            ],
        }
        self.services.repo.update_paths({"recording.events": events})
        with patch.dict(sys.modules, serial=SerialFactory([b"A;D5969C1A;01\n"])):
            self.services.start_devices(
                ["board1"], self.cfg["acquisition"], override=True
            )
            self.await_operation()
            self.assertFalse(path.exists())
            start = self.services.run.start
            self.assertTrue(self.services.run.path.is_file())
            self.assertTrue(json.loads(self.services.run.path.read_text())["state"]["acquisition"]["recording_history"]["runs"])
            self.services.run.save_metadata(
                {"recording.recording_location": "Test room"}
            )
            self.assertEqual(
                json.loads(path.read_text())["recording"]["timeline"]["start_datetime"],
                start,
            )
            self.services.stop_devices()
            self.await_operation()
            cfg = load_config_bundle(path)
            bout = cfg["recording"]["events"]["bouts"][0]
            self.assertEqual(bout["end"], cfg["recording"]["timeline"]["end_datetime"])
            self.assertFalse(bout["end_user_specified"])
            self.assertTrue(cfg["acquisition"]["_provenance"]["config_override"])

    def test_cancelled_matching_releases_port(self):
        with patch.dict(sys.modules, serial=SerialFactory([b"", b""])):
            match = MatchSession(self.services, self.cfg["acquisition"]["boards"])
            match.start()
            wait_for(lambda: bool(self.services.ports.snapshot()))
            match.cancel.set()
            match.thread.join(3)
            self.assertFalse(match.is_running())
            self.assertFalse(self.services.ports.snapshot())

    def test_duplicate_port_rejected_before_open(self):
        self.cfg["acquisition"]["boards"]["board_2"]["com_port"] = "COM5"
        factory = SerialFactory([b""])
        with patch.dict(sys.modules, serial=factory):
            with self.assertRaises(ValueError):
                self.services.start_devices(
                    ["board1", "board2"], self.cfg["acquisition"], self.cfg
                )
            self.assertEqual(factory.instances, [])
