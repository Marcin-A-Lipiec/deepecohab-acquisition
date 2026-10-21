"""Real recorder lifecycle tests using a USB transport that rejects every write."""

import base64
import csv
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from deepecohab_recorder.controllers.lickometer import LickometerController
from deepecohab_recorder.controllers.evidence import read_device
from deepecohab_recorder.models import LickometerConfig
from deepecohab_recorder.protocols.lickometer import LickometerProtocol
from deepecohab_recorder.schema import migrate_config, validate_config
from tests.helpers import FakeSerial, SerialFactory, snapshot, valid_config, wait_for


class ReceiveOnlySerial(FakeSerial):
    def __init__(self, payload=b""):
        super().__init__(payload)
        self.dtr = False
        self.dtr_at_open = None

    def open(self):
        self.dtr_at_open = self.dtr
        super().open()

    def read(self, count=1):
        if not self.dtr:
            return b""
        return super().read(count)

    def write(self, data):
        self.writes.append(bytes(data))
        raise AssertionError("Passive recorder attempted a USB command")

    def emit(self, line):
        self.buffer.extend((line + "\n").encode())


class PassiveLickometerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = LickometerController(LickometerConfig(True, "COM7"))
        self.controller.reconnect_delays = (0.01,)

    def tearDown(self):
        self.controller.stop()
        self.temp.cleanup()

    def start(self, *ports):
        factory = SerialFactory(ports)
        patched = patch.dict(sys.modules, serial=factory)
        patched.start()
        self.addCleanup(patched.stop)
        self.controller.start(snapshot(self.temp.name))
        return factory

    def test_csv_without_handshake_and_snapshots_are_not_licks(self):
        usb = ReceiveOnlySerial(b"STATE,0,100,1,1\n")
        c = self.controller
        self.start(usb)
        wait_for(lambda: c.channels[1]["active"] is True)
        self.assertTrue(usb.dtr_at_open)
        self.assertEqual(c.channels[1]["events"], 0)
        usb.emit("EVENT,0,200,1,0")
        usb.emit("EVENT,0,300,1,1")
        usb.emit("STATE,0,400,1,1")
        wait_for(lambda: c.protocol.previous == 400)
        self.assertIsNotNone(c.channels[1]["contact_started_ns"])
        usb.emit("EVENT,0,500,1,0")
        wait_for(lambda: c.event_rows == 3)
        c.stop()
        rows = list(csv.reader(c.data_file.read_text().splitlines(), delimiter=";"))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(row) == 5 for row in rows))
        self.assertEqual(rows[1][:3], ["1_L1_COM7", "0000000001", "300"])
        self.assertTrue(
            all(datetime.fromisoformat(row[3]).utcoffset() is not None for row in rows)
        )
        self.assertEqual(c.channels[1]["events"], 1)
        self.assertEqual(usb.writes, [])
        self.assertFalse(read_device(c, c._checkpoint)["monitoring"])
        self.assertIn("MONITORING_CLOSED", c.status_file.read_text())

    def test_silent_port_stays_open_and_later_data_recovers(self):
        usb = ReceiveOnlySerial()
        c = self.controller
        c.stream_timeout_seconds = 0.05
        factory = self.start(usb)
        wait_for(lambda: c._gap is not None)
        self.assertTrue(c.is_running())
        self.assertIsNone(c.channels[1]["active"])
        self.assertEqual(c.arduino_mode, "UNKNOWN")
        usb.emit("STATUS,0,1000,LICKOMETER_R4_V7_0_PASSIVE,READY")
        usb.emit("STATE,0,1001,1,1")
        wait_for(lambda: c.channels[1]["active"] is True)
        self.assertEqual(c.event_rows, 0)
        c.stop()
        self.assertEqual(len(factory.instances), 1)
        self.assertEqual(usb.writes, [])
        log = c.status_file.read_text()
        self.assertIn("NO_DATA_RECEIVED", log)
        self.assertIn("COMMUNICATION_RESTORED", log)
        self.assertNotIn("firmware version", c.last_error)

    def test_reconnect_drops_fragments_without_inventing_release(self):
        first = ReceiveOnlySerial(b"EVENT,0,100,1,1\nEVENT,0,2")
        second = ReceiveOnlySerial(b"STATE,0,900,1,1\n")
        c = self.controller
        self.start(first, second)
        wait_for(lambda: c.event_rows == 1)
        first.fail_read = True
        wait_for(lambda: c.protocol.previous == 900)
        self.assertTrue(second.dtr_at_open)
        self.assertEqual(c.event_rows, 1)
        self.assertIsNone(c.channels[1]["contact_started_ns"])
        second.emit("EVENT,0,1000,1,0")
        wait_for(lambda: c.event_rows == 2)
        c.stop()
        self.assertEqual(c.protocol.epoch, 1)
        self.assertEqual(len(c.data_file.read_text().splitlines()), 2)
        fragments = [sample for sample in c.malformed_examples
                     if sample["error"] == "USB gap interrupted serial fragment"]
        self.assertEqual(len(fragments), 1)
        self.assertEqual(base64.b64decode(fragments[0]["data_b64"]), b"EVENT,0,2")
        self.assertIn("COMMUNICATION_LOST", c.status_file.read_text())
        self.assertIn("COMMUNICATION_RESTORED", c.status_file.read_text())
        self.assertEqual(first.writes + second.writes, [])

    def test_reset_without_boot_and_invalid_frame_are_transactional(self):
        p = LickometerProtocol(allow_unannounced_reset=True)
        p.parse("EVENT,0,1000,1,1")
        with self.assertRaises(ValueError):
            p.parse("EVENT,0,1,5,1")
        self.assertEqual((p.epoch, p.previous), (0, 1000))
        self.assertEqual(p.parse("STATE,0,10,1,1").epoch, 1)
        c = self.controller
        stamp = "2026-09-16T12:00:00+02:00"
        c._parse_message("EVENT,0,1000,1,1", stamp, 1)
        c._parse_message("STATE,0,1,1,1", stamp, 2)
        self.assertEqual(c.channels[1]["events"], 1)
        self.assertIsNone(c.channels[1]["contact_started_ns"])
        c._parse_message("CALIBRATION_START,0,2,STUCK_ACTIVE_FAULT", stamp, 3)
        self.assertIsNone(c.channels[1]["active"])

    def test_checkpoint_failure_does_not_discard_contacts(self):
        usb = ReceiveOnlySerial()
        c = self.controller
        self.start(usb)
        c._checkpoint_at = 0
        with patch(
            "deepecohab_recorder.controllers.lickometer_stream.save_device",
            side_effect=OSError("checkpoint denied"),
        ):
            usb.emit("EVENT,0,100,2,1")
            wait_for(lambda: c.event_rows == 1)
        self.assertEqual(c.lines_bad, 0)
        c.stop()
        self.assertIn("CHECKPOINT_WRITE_FAILED", c.status_file.read_text())
        self.assertEqual(len(c.data_file.read_text().splitlines()), 1)

    def test_repeated_recordings_get_separate_files(self):
        ports = [ReceiveOnlySerial(b"EVENT,0,100,1,1\n") for _ in range(2)]
        c = self.controller
        self.start(*ports)
        wait_for(lambda: c.event_rows == 1)
        c.stop()
        first_path = c.data_file
        c.start(snapshot(self.temp.name))
        wait_for(lambda: c.event_rows == 1)
        c.stop()
        self.assertNotEqual(first_path, c.data_file)
        self.assertEqual(len(first_path.read_text().splitlines()), 1)
        self.assertEqual(len(c.data_file.read_text().splitlines()), 1)
        self.assertEqual(ports[0].writes + ports[1].writes, [])

    def test_legacy_pc_settings_do_not_block_passive_recording(self):
        cfg = valid_config()
        cfg["acquisition"]["lickometer"] = dict(
            device_uid="old",
            automatic_recalibration={"daily_hour": "bad"},
            connection_monitoring={"heartbeat_seconds": -1},
            control_mode="old",
        )
        migrated = migrate_config(cfg)
        self.assertNotIn("lickometer", migrated["acquisition"])
        self.assertTrue(migrated["acquisition"]["_legacy_lickometer"])
        self.assertEqual(validate_config(migrated), [])


if __name__ == "__main__":
    unittest.main()
