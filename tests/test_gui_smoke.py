from deepecohab_recorder.config_bundle import load_config_bundle
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from deepecohab_recorder.main_window import MainWindow

    HAVE_QT = True
except ImportError:
    HAVE_QT = False
from tests.helpers import valid_config, SerialFactory, wait_for


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow()
        self.window.widgets["setup"].directory_edit.setText(self.temp.name)
        cfg = valid_config()
        cfg["acquisition"]["camera"]["trigger_delay_us"] = 0
        cfg["acquisition"]["hardware_trigger"].update(
            duration_days=0, duration_hours=2, duration_minutes=0
        )
        self.window.services.repo.replace(cfg)
        self.window._apply(cfg)
        self.window._sync_context()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.services.stop_devices()
        wait_for(lambda: not self.window.services.busy(), 10)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_tabs_zero_values_and_resizing(self):
        self.assertEqual(self.window.tabs.count(), 10)
        self.assertEqual(self.window.tabs.tabText(5), "Events")
        self.assertEqual(self.window.widgets["camera"].config().trigger_delay_us, 0)
        self.assertEqual(self.window.widgets["trigger"].config().duration_days, 0)
        for size in ((800, 600), (1280, 800), (1700, 1000), (720, 1000)):
            self.window.resize(*size)
            for i in range(self.window.tabs.count()):
                self.window.tabs.setCurrentIndex(i)
                for _ in range(4):
                    self.app.processEvents()
                self.window.refresh()
                if i == 3:
                    view = self.window.widgets["layout"].view
                    self.assertTrue(
                        view.viewport()
                        .rect()
                        .contains(view.content_bounds().toAlignedRect()),
                        (size, view.content_bounds(), view.viewport().rect()),
                    )
            self.assertLessEqual(self.window.minimumWidth(), 800)
        self.window.resize(1600, 950)
        self.window.tabs.setCurrentIndex(6)
        self.app.processEvents()
        self.assertGreater(self.window.widgets["camera"].preview_label.width(), 500)

    def test_config_round_trip_and_untouched_sections(self):
        with patch.object(QMessageBox, "warning") as warning:
            self.window.save_all()
            self.assertFalse(warning.called, warning.call_args)
            path = Path(self.temp.name) / "Test.config.json"
            cfg = load_config_bundle(path)
            self.assertEqual(cfg["version"], 8)
            self.assertEqual(
                cfg["recording"]["cohort"]["animals"][0]["subject_name"], "ID1"
            )
            self.assertNotIn("age", cfg["recording"]["cohort"]["animals"][0])
            self.assertEqual(
                len(cfg["recording"]["layout"]["antenna_combinations"]), 24
            )
            self.window.widgets["events"].notes.setPlainText("A note")
            self.window.save_section(self.window.widgets["events"])
            after = json.loads(path.read_text())
            self.assertEqual(after["recording"]["notes"], "A note")
            self.assertEqual(after["recording"]["cohort"], cfg["recording"]["cohort"])

    def test_branch_export_and_stable_tunnel_number(self):
        tab = self.window.widgets["layout"]
        tab.add_branch(tab.graph["tunnels"][0])
        self.assertEqual(len(tab.graph["junctions"]), 1)
        self.assertEqual(tab.graph["tunnels"][-1]["name"], "tunnel_5")
        self.assertTrue(tab.graph["tunnels"][-1]["dead_end"])
        self.assertEqual(len(tab.graph["tunnels"][-1]["antennas"]), 1)
        tab.graph["tunnels"][-1]["antennas"] = ["P-1"]
        graph = tab.read()["recording.layout"]
        self.assertEqual(len(graph["antenna_combinations"]), 24)
        tab.graph["tunnels"].pop()
        tab.graph["junctions"].clear()
        tab.add_tunnel("C1", "C3")
        self.assertEqual(tab.graph["tunnels"][-1]["name"], "tunnel_6")

    def test_missing_config_records_without_a_metadata_confirmation(self):
        factory = SerialFactory([b"A;D5969C1A;01\n"])
        with patch.dict(sys.modules, serial=factory), patch.object(
            self.window, "confirm_override", return_value=False
        ) as prompt:
            self.window.start_devices(["board1"])
            self.window.services._future.result(timeout=10)
            self.assertFalse(prompt.called)
            self.assertTrue(self.window.services.busy("board1"))
            self.assertTrue(factory.instances)
            self.assertTrue(self.window.services.run.started_without_config)
            self.assertFalse((Path(self.temp.name) / "Test.config.json").exists())
            self.window.stop_devices()
            self.window.services._future.result(timeout=10)

    def test_valid_config_has_no_start_confirmation(self):
        self.window.services.repo.save()
        with patch.dict(
            sys.modules, serial=SerialFactory([b"A;D5969C1A;01\n"])
        ), patch.object(self.window, "confirm_override") as prompt, patch.object(
            QMessageBox, "information"
        ) as information:
            self.window.start_devices(["board1"])
            self.window.services._future.result(timeout=10)
            self.assertFalse(prompt.called)
            self.assertFalse(information.called)
            self.assertTrue(self.window.services.busy("board1"))
            self.window.stop_devices()
            self.window.services._future.result(timeout=10)
