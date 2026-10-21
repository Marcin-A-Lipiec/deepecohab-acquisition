import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import numpy as np
from deepecohab_recorder.controllers.camera import CameraController
from deepecohab_recorder.models import CameraConfig
from tests.helpers import snapshot, wait_for
from tools.verify_video import verify


class SyntheticCamera(CameraController):
    """Exercise the production encoder/writer with a synthetic frame producer."""

    frame_count = 30

    def _capture_loop(self):
        try:
            self.actual_width = self.record_width = 64
            self.actual_height = self.record_height = 64
            self._probe_encoder()
            self._dimensions_ready.set()
            if not self._encoder_ready.wait(5):
                raise RuntimeError("Encoder failed readiness")
            self.started_iso = self.clock.iso()
            self.ready.set()
            for i in range(1, self.frame_count + 1):
                self._frames.put(
                    (i, np.full((64, 64), i % 255, dtype=np.uint8)), timeout=5
                )
                self.frames_received += 1
                self.frames_enqueued += 1
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self.stopped_iso = self.clock.iso()
            self._capture_done.set()
            self._stop.set()


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "FFmpeg/ffprobe are required for real encoder tests",
)
class CameraTests(unittest.TestCase):
    def test_restart_retains_both_runs_and_decodes_every_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = SyntheticCamera(
                CameraConfig(
                    width=64, height=64, fps=30, encoding="CPU H.264 emergency fallback"
                ),
                frame_queue_size=32,
            )
            paths, counts = [], []
            for count in (30, 10):
                controller.frame_count = count
                controller.start(snapshot(directory), False)
                wait_for(lambda: controller._capture_done.is_set(), 10)
                controller.stop(12)
                self.assertFalse(controller.is_running(), controller.last_error)
                self.assertEqual(
                    controller.frames_written, count, controller.last_error
                )
                root = Path(directory)
                inventory_path = root / "Test.acquisition_gui_state.json"
                inventory = json.loads(inventory_path.read_text())["state"]["acquisition"]["device_history"][controller._session.file_prefix + ":camera_inventory"]
                self.assertTrue(
                    inventory["parts"][0]["container_count_matches"], inventory
                )
                ledger = (
                    (
                        root
                        / f"{controller._session.file_prefix}_camera_output_frames.csv"
                    )
                    .read_text()
                    .splitlines()
                )
                self.assertEqual(len(ledger) - 1, count)
                part_files = [
                    inventory_path.parent / item["file"] for item in inventory["parts"][0]["files"]
                ]
                self.assertEqual(len(part_files), 1)
                report = verify(inventory_path, controller._session.file_prefix + ":camera_inventory")
                self.assertTrue(report["ok"], report)
                counts.append(report["results"][0]["decoded_frames"])
                paths.extend(part_files)
            self.assertEqual(counts, [30, 10])
            self.assertEqual(len(set(paths)), 2)
            self.assertTrue(all(path.exists() for path in paths))

    def test_kill_unblocks_actual_stalled_pipe(self):
        import sys

        controller = CameraController(CameraConfig())
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE,
            bufsize=0,
        )
        controller._ffmpeg_proc = process
        errors = []

        def writer():
            try:
                process.stdin.write(b"x" * 16_000_000)
            except (BrokenPipeError, ValueError, OSError) as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        time.sleep(0.1)
        start = time.monotonic()
        controller._kill_ffmpeg("test forced stop")
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 4)
        self.assertIsNotNone(process.poll())

    def test_camera_start_failure_finishes_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = CameraController(CameraConfig())
            controller.start(snapshot(directory), False)
            wait_for(lambda: not controller.is_running(), 10)
            self.assertTrue(controller.last_error)
