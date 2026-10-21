"""Patch 3 boundary tests: fixed clock, real click gestures and device lifecycles."""

import copy
import csv
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from deepecohab_recorder.clock import (
    ExperimentClock,
    establish_schedule,
    fixed_phase_state,
    fixed_phase_origin,
    next_daily_occurrence,
)
from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.models import LickometerConfig, LightingConfig
from deepecohab_recorder.controllers.lickometer import LickometerController
from deepecohab_recorder.controllers.lighting import LightingController
from deepecohab_recorder.protocols.control import (
    parse_status,
    ControlInbox,
    payload_crc,
)
from deepecohab_recorder.services.application import ApplicationServices
from deepecohab_recorder.schema import default_config, migrate_config
from tests.helpers import FakeSerial, SerialFactory, snapshot, valid_config, wait_for


class DeviceSerial(FakeSerial):
    def __init__(self, kind="lighting", uid="testdevice", boot="boot1"):
        super().__init__()
        self.kind, self.uid, self.boot = kind, uid, boot
        self.lock = threading.RLock()
        self.armed = False
        self.revision = 0
        self.crc = "00000000"
        self.sid = "-"
        self.anchor = 0
        self.light = self.dark = 43200
        self.utc = 1800000000
        self.staged = {}
        self.commands = []

    def emit(self, line):
        with self.lock:
            self.buffer.extend((line + "\n").encode())

    def status(self):
        mode = (
            ("RUNNING" if self.armed else "IDLE")
            if self.kind == "lighting"
            else "READY"
        )
        self.emit(
            f"STATUS2,{self.kind},{self.uid},{self.boot},{mode},RTC_ANCHORED,{self.utc},{self.sid},{self.anchor},{self.light},{self.dark},{self.revision},{self.crc},0,NONE,1000000,{int(self.armed)}"
        )

    def read(self, n=1):
        with self.lock:
            return super().read(n)

    def write(self, data):
        super().write(data)
        for line in data.decode().splitlines():
            p = line.split(",")
            self.commands.append(p)
            if p[0] == "HELLO":
                self.emit(f"HELLO,2,{self.kind},{self.uid},{self.boot},TEST")
            elif p[0] == "STATUS":
                self.status()
            elif p[0] == "RECALIBRATE":
                self.emit("RECALIBRATION_PENDING,0,2000,MANUAL")
            else:
                verb, request = p[:2]
                if verb == "SET_TIME":
                    self.utc = int(ExperimentClock().parse(p[2]).timestamp())
                if verb == "SET_SETTINGS":
                    self.staged["crc"] = p[3]
                if verb == "SET_SCHEDULE":
                    self.staged.update(
                        revision=int(p[2]),
                        sid=p[3],
                        anchor=int(p[4]),
                        light=int(p[5]),
                        dark=int(p[6]),
                    )
                if verb == "COMMIT":
                    for k, v in self.staged.items():
                        setattr(self, k, v)
                if verb == "ARM":
                    self.armed = True
                if verb == "STOP_CONTROL":
                    self.armed = False
                self.emit(f"ACK,{request},{verb},{self.revision}")
        return len(data)


class FixedClockTests(unittest.TestCase):
    def test_dst_and_phase_origin(self):
        for actual, expected in [
            ("2026-03-29T00:00:00+01:00", "2026-03-29T13:00:00+02:00"),
            ("2026-10-25T00:00:00+02:00", "2026-10-25T11:00:00+01:00"),
        ]:
            timeline = establish_schedule(
                default_config()["recording"]["timeline"], actual
            )
            s = timeline["phase_schedule"]
            state = fixed_phase_state(s, actual)
            self.assertEqual(state["next_transition_datetime"], expected)
            self.assertEqual(
                s,
                establish_schedule(timeline, "2027-01-01T12:00:00+01:00")[
                    "phase_schedule"
                ],
            )
        timeline = establish_schedule(
            default_config()["recording"]["timeline"], "2026-09-01T13:20:00+02:00"
        )
        origin = fixed_phase_origin(
            "2026-09-01T13:20:00+02:00",
            "Europe/Warsaw",
            timeline["phase_schedule"],
            "dark_phase",
        )
        self.assertEqual(origin["missing_initial_seconds"], 4800)

    def test_migration_empty_layout_and_fixed_clock(self):
        cfg = valid_config()
        cfg["recording"]["layout"] = {}
        cfg["recording"]["timeline"].pop("phase_schedule")
        migrated = migrate_config(cfg)
        schedule = migrated["recording"]["timeline"]["phase_schedule"]
        self.assertEqual(schedule["mode"], "fixed_cycle")
        self.assertEqual(schedule["light_duration_seconds"], 43200)
        self.assertEqual(schedule["dark_duration_seconds"], 43200)
        self.assertEqual(migrated, migrate_config(migrated))
        self.assertFalse(migrated["recording"]["layout"]["cages"])
        self.assertEqual(len(default_config()["recording"]["layout"]["cages"]), 4)

    def test_protocol_rejects_malformed_and_wrong_ack(self):
        for text in [
            "STATUS2,lighting,x,b,IDLE,RTC,0,-,0,0,0,0,00000000,0,NONE,0",
            "STATUS2,lighting,x,b,IDLE,RTC,0,-,0,43200,43200,0,bogus,0,NONE,0",
        ]:
            with self.assertRaises(ValueError):
                parse_status(text)
        inbox = ControlInbox()

        def send(line):
            p = line.split(",")
            inbox.consume(f"ACK,{p[1]},WRONG,1")

        with self.assertRaises(ValueError):
            inbox.command(send, "COMMIT", 1)


class ControllerTests(unittest.TestCase):
    def test_lighting_commit_disconnect_and_stop_are_independent(self):
        device = DeviceSerial()
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, serial=SerialFactory([device])
        ):
            app = ApplicationServices()
            app.set_context("Test", temp, "Europe/Warsaw")
            app.repo.replace(valid_config())
            app.lighting.submit("connect", asdict(LightingConfig(True, "COM20")))
            app.lighting.future.result(6)
            self.assertIsNotNone(app.lighting.controller, app.lighting.error)
            with self.assertRaises(RuntimeError):
                app.ports.acquire("COM20", "board1")
            app.lighting.submit("apply", app.repo.data["recording"]["timeline"])
            app.lighting.future.result(6)
            self.assertTrue(device.armed, app.lighting.error)
            sid = app.repo.data["recording"]["timeline"]["phase_schedule"][
                "schedule_id"
            ]
            self.assertEqual(sid, device.sid)
            count = len(device.commands)
            app.lighting.submit("apply", app.repo.data["recording"]["timeline"])
            app.lighting.future.result(6)
            self.assertFalse(any(p[0] == "SET_TIME" for p in device.commands[count:]))
            app.stop_devices()
            app._future.result(3)
            self.assertTrue(device.armed)
            app.lighting.close()
            app.lighting.future.result(4)
            self.assertTrue(device.armed)
            self.assertFalse(any(p[0] == "STOP_CONTROL" for p in device.commands))
            self.assertFalse(app.ports.snapshot())
            app.shutdown()

    def test_passive_lickometer_reconnect_and_no_invented_release(self):
        first, second = DeviceSerial("lickometer"), DeviceSerial("lickometer")
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, serial=SerialFactory([first, second])
        ):
            config = LickometerConfig(True, "COM7")
            controller = LickometerController(config)
            controller.reconnect_delays = (0.01,)
            try:
                controller.start(snapshot(temp))
                first.emit("EVENT,0,100,1,1")
                wait_for(lambda: controller.event_rows == 1)
                first.fail_read = True
                second.emit("STATE,0,200,1,1")
                wait_for(lambda: controller.protocol.previous == 200)
                self.assertEqual(controller.event_rows, 1)
                self.assertIsNone(controller.channels[1]["contact_started_ns"])
                second.emit("EVENT,0,300,1,0")
                wait_for(lambda: controller.event_rows == 2)
            finally:
                controller.stop()
            log = controller.status_file.read_text()
            self.assertIn("COMMUNICATION_LOST", log)
            self.assertIn("COMMUNICATION_RESTORED", log)
            self.assertEqual(len(controller.data_file.read_text().splitlines()), 2)
            self.assertFalse(controller.lines_bad, log)
            self.assertEqual(first.commands + second.commands, [])

    def test_lost_lighting_arm_ack_is_resolved_from_readback(self):
        device = DeviceSerial()
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, serial=SerialFactory([device])
        ):
            link = LightingController(
                LightingConfig(True, "COM20"), temp, "Europe/Warsaw"
            )
            link.open()
            original = link.command

            def command(verb, *args):
                result = original(verb, *args)
                if verb == "ARM":
                    # A real missing-ACK timeout outlasts a Windows monotonic
                    # clock tick. Keep the simulation fast without making its
                    # readback indistinguishable from the pre-ARM status.
                    time.sleep(max(0.002, 2 * time.get_clock_info("monotonic").resolution))
                    raise TimeoutError("Simulated missing ARM acknowledgement")
                return result

            try:
                with patch.object(link, "command", side_effect=command):
                    status = link.apply(
                        establish_schedule(default_config()["recording"]["timeline"])
                    )
                self.assertTrue(status.armed)
                self.assertEqual(sum(p[0] == "ARM" for p in device.commands), 1)
            finally:
                link.close()

    def test_passive_lickometer_records_without_arm_or_acknowledgement(self):
        device = DeviceSerial("lickometer")
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, serial=SerialFactory([device])
        ):
            controller = LickometerController(LickometerConfig(True, "COM7"))
            try:
                controller.start(snapshot(temp))
                device.emit("EVENT,0,100,1,1")
                wait_for(lambda: controller.event_rows == 1)
                self.assertTrue(controller.channels[1]["active"])
                self.assertFalse(device.armed)
            finally:
                controller.stop()
            self.assertEqual(device.commands, [])
            self.assertEqual(len(controller.data_file.read_text().splitlines()), 1)

    def test_initial_silent_passive_device_reports_gap_without_reset(self):
        device = DeviceSerial("lickometer")
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, serial=SerialFactory([device])
        ):
            controller = LickometerController(LickometerConfig(True, "COM7"))
            controller.stream_timeout_seconds = 0.05
            try:
                controller.start(snapshot(temp))
                wait_for(lambda: controller._gap is not None)
                self.assertTrue(controller.is_running())
                self.assertTrue(device.is_open)
                self.assertIsNone(controller.channels[1]["active"])
            finally:
                controller.stop()
            self.assertEqual(device.commands, [])
            self.assertIn("NO_DATA_RECEIVED", controller.status_file.read_text())

    def test_lighting_rejects_active_foreign_schedule(self):
        device = DeviceSerial()
        device.sid = "another_experiment"
        device.anchor = 1800000000
        device.armed = True
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            sys.modules, serial=SerialFactory([device])
        ):
            link = LightingController(
                LightingConfig(True, "COM20"), temp, "Europe/Warsaw"
            )
            link.open()
            try:
                with self.assertRaises(RuntimeError):
                    link.apply(
                        establish_schedule(default_config()["recording"]["timeline"])
                    )
                self.assertFalse(any(p[0].startswith("SET_") for p in device.commands))
            finally:
                link.close()


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from deepecohab_recorder.tabs.layout import EcoHABLayoutTab
    from deepecohab_recorder.ui.context import UiContext

    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class LayoutGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.app = ApplicationServices()
        self.tab = EcoHABLayoutTab(UiContext(self.app))
        self.tab.resize(1200, 900)
        self.tab.show()
        self.qt.processEvents()
        self.tab.fit()
        self.qt.processEvents()

    def tearDown(self):
        self.tab.close()
        self.tab.deleteLater()
        self.qt.processEvents()
        self.app.shutdown()

    def click(self, point):
        viewport = self.tab.view.viewport()
        at = self.tab.view.mapFromScene(point)
        QTest.mouseMove(viewport, at)
        QTest.mouseClick(viewport, Qt.LeftButton, pos=at)
        self.qt.processEvents()

    def test_preset_grid_and_cross_tunnels(self):
        self.assertTrue(self.tab.view.show_grid)
        self.assertEqual(
            self.tab.graph["editor"]["cage_positions"],
            {"C1": [120, 600], "C2": [120, 120], "C3": [600, 120], "C4": [600, 600]},
        )
        for a, b in [("C1", "C3"), ("C2", "C4")]:
            self.tab.set_mode("connect")
            self.click(self.tab.endpoint(a))
            self.click(self.tab.endpoint(b))
            self.assertEqual(self.tab.mode, "select", self.tab.hint.text())
        self.assertEqual(len(self.tab.graph["tunnels"]), 6)
        self.assertEqual(len(self.tab.graph["cages"]), 4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.png"
            self.tab.render_png(path)
            self.assertGreater(path.stat().st_size, 5000)

    def test_branch_preview_does_not_intercept_destination(self):
        self.tab.start_branch(self.tab.graph["tunnels"][0], QPointF(120, 360))
        self.click(QPointF(-120, 360))
        self.assertEqual(self.tab.mode, "select", self.tab.hint.text())
        self.assertEqual(len(self.tab.graph["junctions"]), 1)
        self.assertTrue(self.tab.graph["tunnels"][-1]["dead_end"])
        self.assertEqual(len(self.tab.graph["tunnels"][-1]["antennas"]), 1)
        self.assertEqual(len(self.tab.graph["cages"]), 4)

    def test_branch_to_other_tunnel_and_cancel(self):
        self.tab.start_branch(self.tab.graph["tunnels"][0], QPointF(120, 360))
        self.click(QPointF(600, 360))
        self.assertEqual(self.tab.mode, "select", self.tab.hint.text())
        self.assertEqual(len(self.tab.graph["junctions"]), 2)
        before = copy.deepcopy(self.tab.graph)
        self.tab.start_tunnel(self.tab.graph["cages"][0])
        QTest.keyClick(self.tab.view, Qt.Key_Escape)
        self.assertEqual(before, self.tab.graph)


if __name__ == "__main__":
    unittest.main()
