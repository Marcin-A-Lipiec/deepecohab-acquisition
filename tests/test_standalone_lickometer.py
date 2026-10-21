from deepecohab_recorder.config_bundle import load_config_bundle
"""Patch 5: no USB lickometer path; legacy metadata is inert; DAQ records TTLs."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.controllers.rfid import RFIDBoardController
from deepecohab_recorder.models import BoardConfig, NEW_DAQ, DAQ_V15
from deepecohab_recorder.schema import default_config, migrate_config, validate_config
from deepecohab_recorder.services.application import ApplicationServices
from tests.helpers import valid_config, SerialFactory, snapshot, wait_for


class StandaloneConfigTests(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        cfg = default_config()
        self.assertNotIn("lickometer", cfg["acquisition"])
        self.assertNotIn("lickometer", cfg["acquisition"]["launch"])
        self.assertEqual(migrate_config(migrate_config(cfg)), migrate_config(cfg))

    def test_modern_legacy_config_archived_without_mutating_input(self):
        cfg = valid_config()
        cfg["acquisition"]["lickometer"] = {
            "enabled": True,
            "com_port": "COM7",
            "control_mode": "passive_usb",
            "automatic_recalibration": {"daily_hour": "invalid"},
        }
        cfg["acquisition"]["launch"]["lickometer"] = True
        cfg["acquisition"]["com_ports"] = {"COM5": "RFID_DAQ_1", "COM7": "Lickometer"}
        original = copy.deepcopy(cfg)
        migrated = migrate_config(cfg)
        self.assertEqual(cfg, original)
        self.assertEqual(validate_config(migrated), [])
        self.assertNotIn("lickometer", migrated["acquisition"])
        self.assertNotIn("lickometer", migrated["acquisition"]["launch"])
        self.assertEqual(migrated["acquisition"]["com_ports"], {"COM5": "RFID_DAQ_1"})
        self.assertEqual(len(migrated["acquisition"]["_legacy_lickometer"]), 3)
        self.assertEqual(migrate_config(migrated), migrated)

    def test_old_recorder_format(self):
        old = {
            "version": 4,
            "experiment": {"name": "old"},
            "lickometer": {"enabled": True, "com_port": "COM4"},
            "launch": {"lickometer": True},
        }
        result = migrate_config(old)
        self.assertNotIn("lickometer", result)
        archive = result["acquisition"]["_legacy_lickometer"]
        self.assertTrue(any(row["source"] == "lickometer" for row in archive))
        self.assertNotIn("lickometer", result["acquisition"]["launch"])

    def test_save_retires_injected_legacy_settings_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = ConfigRepository(lambda: "Test", lambda: directory)
            cfg = valid_config()
            cfg["acquisition"]["lickometer"] = {"enabled": True, "com_port": "COM7"}
            repo.replace(cfg)
            repo.save()
            first = load_config_bundle(Path(directory) / "Test.config.json")
            self.assertEqual(first["acquisition"]["_provenance"]["software_patch"], 20)
            self.assertNotIn("lickometer", first["acquisition"])
            repo.update_paths({"recording.notes": "updated"})
            repo.save()
            second = load_config_bundle(Path(directory) / "Test.config.json")
            self.assertEqual(
                first["acquisition"]["_legacy_lickometer"],
                second["acquisition"]["_legacy_lickometer"],
            )
            self.assertEqual(second["recording"]["notes"], "updated")

    def test_usb_device_request_rejected_before_open(self):
        services = ApplicationServices()
        factory = SerialFactory([])
        try:
            with patch.dict(sys.modules, serial=factory):
                with self.assertRaisesRegex(ValueError, "Standalone lickometers"):
                    services.start_devices(
                        ["lickometer"], valid_config()["acquisition"]
                    )
            self.assertEqual(factory.instances, [])
            self.assertFalse(services.busy())
            self.assertIsNone(services._future)
        finally:
            services.shutdown()

    def test_all_ttl_channels_recorded_separately_for_both_daq_profiles(self):
        payload = b"".join(
            f"10;0000000{i};{t:08X}\n".encode()
            for t in (1000, 1001)
            for i in range(1, 5)
        )
        for hardware in (NEW_DAQ, DAQ_V15):
            with self.subTest(hardware=hardware), tempfile.TemporaryDirectory() as d:
                factory = SerialFactory([payload])
                with patch.dict(sys.modules, serial=factory):
                    board = RFIDBoardController(BoardConfig(1, True, hardware, "COM5"))
                    board.start(snapshot(d))
                    try:
                        wait_for(lambda: board.lines_ok == 8)
                    finally:
                        board.stop()
                lines = board.data_file.read_text().splitlines()
                self.assertEqual(len(lines), 8)
                self.assertEqual(
                    {line.split(";")[0] for line in lines},
                    {f"TTL{i}-1" for i in range(1, 5)},
                )
                self.assertTrue(all("+02:00" in line for line in lines))
                self.assertEqual(board.get_tag_counts_snapshot(), {})
                self.assertFalse(list(Path(d).rglob("*lickometer*.csv")))


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from deepecohab_recorder.main_window import MainWindow

    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 unavailable")
class StandaloneGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_no_tab_no_launch_control_and_old_config_load_save(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            QMessageBox, "warning"
        ) as warning:
            window = MainWindow()
            try:
                cfg = valid_config()
                cfg["acquisition"]["lickometer"] = {"enabled": True, "com_port": "COM7"}
                cfg["acquisition"]["launch"]["lickometer"] = True
                source = Path(d) / "old.json"
                source.write_text(json.dumps(cfg))
                loaded = window.services.repo.import_file(source)
                window.widgets["setup"].directory_edit.setText(d)
                window._apply(loaded)
                window.show()
                self.qt.processEvents()
                self.assertEqual(window.tabs.count(), 10)
                self.assertNotIn("lickometer", window.widgets)
                self.assertNotIn("lickometer", window.widgets["launch"].selected)
                self.assertNotIn("lickometer", window.hardware_settings())
                window.save_all()
                self.assertFalse(warning.called, warning.call_args)
                saved = json.loads((Path(d) / "Test.config.json").read_text())
                self.assertNotIn("lickometer", saved["acquisition"])
                self.assertFalse(window.services.ports.snapshot())
            finally:
                window.close()
                window.deleteLater()
                self.qt.processEvents()


@unittest.skipUnless(shutil.which("g++"), "g++ unavailable")
class StandaloneFirmwareTests(unittest.TestCase):
    def test_actual_sketch_with_register_and_adc_simulator(self):
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "firmware"
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I" + str(root / "standalone_stubs"),
                    str(root / "test_standalone_lickometer_firmware.cpp"),
                    "-o",
                    str(exe),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            for case in (
                "sensing",
                "debounce",
                "noise",
                "stuck",
                "faults",
                "startup",
                "autorange",
                "readback",
                "range",
                "bus",
                "wraps",
                "daily",
            ):
                with self.subTest(case=case):
                    result = subprocess.run(
                        [str(exe), case], capture_output=True, text=True, timeout=30
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("passed", result.stdout)
