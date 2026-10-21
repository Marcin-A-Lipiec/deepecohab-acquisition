from __future__ import annotations

import gc
import json
from deepecohab_recorder.experiment_files import available_prefix
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from deepecohab_recorder.models import CameraConfig, SessionSnapshot
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.video_tools import resolve_video_tools, subprocess_options


@dataclass(frozen=True)
class EncodingOption:
    label: str
    extension: str
    encoder_args: Tuple[str, ...]


ENCODING_OPTIONS: Dict[str, EncodingOption] = {
    "DeepEcoHAB_default (NVENC HQ)": EncodingOption(
        "DeepEcoHAB_default (NVENC HQ)",
        "mp4",
        (
            "-c:v",
            "hevc_nvenc",
            "-preset",
            "slow",
            "-tune",
            "hq",
            "-rc:v",
            "vbr",
            "-cq:v",
            "22",
            "-b:v",
            "0",
            "-spatial_aq",
            "1",
            "-aq-strength",
            "4",
            "-rc-lookahead",
            "10",
            "-g",
            "5",
            "-force_key_frames",
            "expr:gte(t,n_forced*6)",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ),
    ),
    "GPU NVENC conservative": EncodingOption(
        "GPU NVENC conservative",
        "mp4",
        (
            "-c:v",
            "hevc_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc:v",
            "vbr",
            "-cq:v",
            "24",
            "-b:v",
            "0",
            "-spatial_aq",
            "1",
            "-aq-strength",
            "3",
            "-rc-lookahead",
            "8",
            "-g",
            "60",
            "-pix_fmt",
            "yuv420p",
        ),
    ),
    "CPU x265 safe mode": EncodingOption(
        "CPU x265 safe mode",
        "mp4",
        (
            "-c:v",
            "libx265",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "60",
        ),
    ),
    "CPU H.264 emergency fallback": EncodingOption(
        "CPU H.264 emergency fallback",
        "mp4",
        (
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "60",
        ),
    ),
    "FFV1 lossless diagnostic": EncodingOption(
        "FFV1 lossless diagnostic",
        "mkv",
        ("-c:v", "ffv1", "-level", "3", "-g", "1"),
    ),
}


def _try_set_enum(pyspin: Any, nodemap: Any, node_name: str, entry_name: str) -> bool:
    try:
        node = pyspin.CEnumerationPtr(nodemap.GetNode(node_name))
        if not pyspin.IsReadable(node) or not pyspin.IsWritable(node):
            return False
        entry = node.GetEntryByName(entry_name)
        if not pyspin.IsReadable(entry):
            return False
        node.SetIntValue(entry.GetValue())
        return True
    except Exception:
        return False


def _try_get_enum(pyspin: Any, nodemap: Any, node_name: str) -> str:
    try:
        node = pyspin.CEnumerationPtr(nodemap.GetNode(node_name))
        if not pyspin.IsReadable(node):
            return ""
        entry = node.GetCurrentEntry()
        return entry.GetSymbolic() if pyspin.IsReadable(entry) else ""
    except Exception:
        return ""


def _try_set_bool(pyspin: Any, nodemap: Any, node_name: str, value: bool) -> bool:
    try:
        node = pyspin.CBooleanPtr(nodemap.GetNode(node_name))
        if not pyspin.IsWritable(node):
            return False
        node.SetValue(bool(value))
        return True
    except Exception:
        return False


def _try_set_float(pyspin: Any, nodemap: Any, node_name: str, value: float) -> bool:
    try:
        node = pyspin.CFloatPtr(nodemap.GetNode(node_name))
        if not pyspin.IsWritable(node):
            return False
        value = max(float(node.GetMin()), min(float(node.GetMax()), float(value)))
        node.SetValue(value)
        return True
    except Exception:
        return False


def _align_down(value: int, minimum: int, maximum: int, increment: int) -> int:
    value = max(minimum, min(maximum, int(value)))
    if increment <= 1:
        return value
    return minimum + ((value - minimum) // increment) * increment


def _set_centered_roi(
    pyspin: Any, nodemap: Any, width: Optional[int], height: Optional[int]
) -> None:
    if not width or not height:
        return
    try:
        width_node = pyspin.CIntegerPtr(nodemap.GetNode("Width"))
        height_node = pyspin.CIntegerPtr(nodemap.GetNode("Height"))
        ox_node = pyspin.CIntegerPtr(nodemap.GetNode("OffsetX"))
        oy_node = pyspin.CIntegerPtr(nodemap.GetNode("OffsetY"))
        for node in (width_node, height_node, ox_node, oy_node):
            if not pyspin.IsReadable(node) or not pyspin.IsWritable(node):
                return
        ox_node.SetValue(ox_node.GetMin())
        oy_node.SetValue(oy_node.GetMin())
        w = _align_down(
            width, width_node.GetMin(), width_node.GetMax(), width_node.GetInc()
        )
        h = _align_down(
            height, height_node.GetMin(), height_node.GetMax(), height_node.GetInc()
        )
        width_node.SetValue(w)
        height_node.SetValue(h)
        max_x = ox_node.GetMax()
        max_y = oy_node.GetMax()
        off_x = _align_down(
            max(0, (max_x + ox_node.GetMin()) // 2),
            ox_node.GetMin(),
            max_x,
            ox_node.GetInc(),
        )
        off_y = _align_down(
            max(0, (max_y + oy_node.GetMin()) // 2),
            oy_node.GetMin(),
            max_y,
            oy_node.GetInc(),
        )
        ox_node.SetValue(off_x)
        oy_node.SetValue(off_y)
    except Exception:
        return


class CameraController:
    """Long-running FLIR capture with bounded buffering and FFmpeg recovery.

    Camera capture, preview and timestamping are independent from the FFmpeg
    writer. A blocked/crashed encoder therefore cannot freeze the camera thread.
    The bounded queue limits RAM use; overflow is counted rather than growing
    without limit.
    """

    def __init__(self, config: CameraConfig, frame_queue_size: int = 32) -> None:
        self.config = config
        self.frame_queue_size = max(2, int(frame_queue_size))
        self._frames: "queue.Queue[Optional[Tuple[int, Any]]]" = queue.Queue(
            maxsize=self.frame_queue_size
        )
        self._stop = threading.Event()
        self.ready = threading.Event()
        self._capture_done = threading.Event()
        self._dimensions_ready = threading.Event()
        self._encoder_ready = threading.Event()
        self.clock = ExperimentClock()
        self._inventory = []
        self._part_submitted = {}
        self._ledger_handle = None
        self.frames_incomplete = 0
        self.started_iso = self.stopped_iso = ""
        self._capture_thread: Optional[threading.Thread] = None
        self._writer_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._running = False
        self._external_trigger = True
        self._session: Optional[SessionSnapshot] = None

        self.frames_received = 0
        self.frames_enqueued = 0
        self.frames_written = 0
        self.frames_dropped = 0
        self.ffmpeg_restarts = 0
        self.last_error = ""
        self.waiting_for_first_frame = True
        self.actual_width: Optional[int] = None
        self.actual_height: Optional[int] = None
        self.record_width: Optional[int] = None
        self.record_height: Optional[int] = None
        self.camera_config_report: Dict[str, Any] = {}

        self._preview_lock = threading.Lock()
        self._preview_frame = None
        self._preview_sequence = 0
        self._ffmpeg_lock = threading.RLock()
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._ffmpeg_log_handle = None
        self._ffmpeg_part = 0
        self._last_writer_progress = time.monotonic()
        self._writer_needs_restart = threading.Event()
        self._timestamps_handle = None
        self._timestamps_last_flush = time.monotonic()
        self.current_output_pattern = ""
        self.active_encoding = self.config.encoding
        self._using_cpu_fallback = False
        self._encoder_failure_streak = 0
        self._stable_writes_since_failure = 0
        self._video_tools = None

    def reconfigure(self, config: CameraConfig) -> None:
        if self.is_running():
            raise RuntimeError("Cannot change camera settings while recording.")
        self.config = config

    def start(self, session: SessionSnapshot, external_trigger: bool) -> None:
        if self.is_running():
            raise RuntimeError("Camera workers are still active.")
        # Freeze machine paths for this recording, including encoder restarts.
        self._video_tools = resolve_video_tools()
        self._video_tools['ffmpeg'].require()
        self._session = replace(
            session, file_prefix=available_prefix([session.save_path], session.file_prefix)
        )
        self._session.save_path.mkdir(parents=True, exist_ok=True)
        self.clock = ExperimentClock(session.timezone)
        self.ready.clear()
        self._capture_done.clear()
        self._dimensions_ready.clear()
        self._encoder_ready.clear()
        self._inventory, self._part_submitted = [], {}
        self.frames_incomplete = 0
        self.started_iso = self.stopped_iso = ""
        self._external_trigger = bool(external_trigger)
        self._frames = queue.Queue(maxsize=self.frame_queue_size)
        self._stop.clear()
        self._writer_needs_restart.clear()
        self.frames_received = 0
        self.frames_enqueued = 0
        self.frames_written = 0
        self.frames_dropped = 0
        self.ffmpeg_restarts = 0
        self.last_error = ""
        self.waiting_for_first_frame = True
        self.actual_width = None
        self.actual_height = None
        self.record_width = None
        self.record_height = None
        self.camera_config_report = {}
        with self._preview_lock:
            self._preview_frame = None
            self._preview_sequence += 1
        self.current_output_pattern = ""
        self.active_encoding = self.config.encoding
        self._using_cpu_fallback = False
        self._encoder_failure_streak = 0
        self._stable_writes_since_failure = 0
        self._ffmpeg_part = 0
        self._last_writer_progress = time.monotonic()
        self._timestamps_last_flush = time.monotonic()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="camera-capture", daemon=True
        )
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="camera-ffmpeg-writer", daemon=True
        )
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="camera-ffmpeg-watchdog", daemon=True
        )
        self._writer_thread.start()
        self._watchdog_thread.start()
        self._capture_thread.start()
        self._running = True

    def request_stop(self) -> None:
        # Only the producer stops here; the writer drains its accepted frames.
        self._stop.set()

    def stop(self, timeout: float = 12.0) -> None:
        self.request_stop()
        deadline = time.monotonic() + timeout
        for thread in (
            self._capture_thread,
            self._writer_thread,
            self._watchdog_thread,
        ):
            if thread and thread.is_alive():
                thread.join(max(0, deadline - time.monotonic()))
        if self._writer_thread and self._writer_thread.is_alive():
            self._kill_ffmpeg("forced stop after drain timeout")
            self._writer_thread.join(3)
        self._running = self.is_running()
        if self._running:
            self.last_error = "Stop timeout: camera workers remain active."

    def is_running(self) -> bool:
        return any(
            thread and thread.is_alive()
            for thread in (
                self._capture_thread,
                self._writer_thread,
                self._watchdog_thread,
            )
        )

    def get_preview_frame(self):
        with self._preview_lock:
            return None if self._preview_frame is None else self._preview_frame.copy()

    def get_preview_snapshot(self, after_sequence=None):
        """Copy a new display frame only; recording buffers keep their ownership."""
        with self._preview_lock:
            sequence = self._preview_sequence
            frame = self._preview_frame
            if frame is None or sequence == after_sequence:
                return sequence, None
            return sequence, frame.copy()

    def status_text(self) -> str:
        state = (
            "WAITING FOR TTL"
            if self.is_running()
            and self.waiting_for_first_frame
            and self._external_trigger
            else ("RUNNING" if self.is_running() else "STOPPED")
        )
        error = f" | error={self.last_error}" if self.last_error else ""
        return (
            f"{state} | received={self.frames_received:,} submitted={self.frames_written:,} "
            f"dropped={self.frames_dropped:,} queue={self._frames.qsize()} restarts={self.ffmpeg_restarts} "
            f"encoder={self.active_encoding}{' [CPU FALLBACK]' if self._using_cpu_fallback else ''}{error}"
        )

    def _capture_loop(self) -> None:
        try:
            import numpy as np
            import cv2
            import PySpin
        except Exception as exc:
            self.last_error = f"Camera dependencies unavailable: {exc}. Open System check for camera setup instructions."
            self._stop.set()
            self._capture_done.set()
            return

        system = None
        cam_list = None
        cam = None
        image = None
        try:
            if self._session is None:
                raise RuntimeError("Camera session is not initialized.")
            prefix = self._session.file_prefix
            timestamps_path = (
                self._session.save_path / f"{prefix}_camera_timestamps.csv"
            )
            self._timestamps_handle = timestamps_path.open(
                "x", encoding="utf-8", buffering=1024 * 1024
            )
            if timestamps_path.stat().st_size == 0:
                self._timestamps_handle.write(
                    "frame_index;camera_timestamp;pc_iso;enqueued;hardware_frame_id;incomplete\n"
                )

            system = PySpin.System.GetInstance()
            cam_list = system.GetCameras()
            if cam_list.GetSize() == 0:
                raise RuntimeError("No FLIR camera detected by PySpin. Check the connection in SpinView, then use System check to detect cameras.")
            cam = cam_list.GetByIndex(0)
            cam.Init()
            nodemap = cam.GetNodeMap()

            _try_set_enum(PySpin, nodemap, "TriggerMode", "Off")
            _try_set_enum(PySpin, nodemap, "PixelFormat", "Mono8")
            _try_set_enum(PySpin, nodemap, "AcquisitionMode", "Continuous")
            _set_centered_roi(PySpin, nodemap, self.config.width, self.config.height)
            if self._external_trigger:
                input_line = (
                    self.config.trigger_source
                    if str(self.config.trigger_source).startswith("Line")
                    else "Line0"
                )
                _try_set_enum(PySpin, nodemap, "LineSelector", input_line)
                _try_set_enum(PySpin, nodemap, "LineMode", "Input")
                _try_set_bool(PySpin, nodemap, "LineInverter", False)
                _try_set_enum(PySpin, nodemap, "TriggerSelector", "FrameStart")
                _try_set_enum(
                    PySpin, nodemap, "TriggerSource", self.config.trigger_source
                )
                _try_set_enum(
                    PySpin, nodemap, "TriggerActivation", self.config.trigger_activation
                )
                _try_set_enum(
                    PySpin, nodemap, "TriggerOverlap", self.config.trigger_overlap
                )
                _try_set_float(
                    PySpin, nodemap, "TriggerDelay", self.config.trigger_delay_us
                )
                _try_set_enum(PySpin, nodemap, "TriggerMode", "On")
            else:
                _try_set_enum(PySpin, nodemap, "TriggerMode", "Off")
            self.camera_config_report = {
                "TriggerMode": _try_get_enum(PySpin, nodemap, "TriggerMode"),
                "TriggerSource": _try_get_enum(PySpin, nodemap, "TriggerSource"),
                "TriggerActivation": _try_get_enum(
                    PySpin, nodemap, "TriggerActivation"
                ),
            }

            expected = {"TriggerMode": "On" if self._external_trigger else "Off"}
            if self._external_trigger:
                expected.update(
                    TriggerSource=self.config.trigger_source,
                    TriggerActivation=self.config.trigger_activation,
                )
            for key, value in expected.items():
                if self.camera_config_report[key] != value:
                    raise RuntimeError(
                        f"Camera did not apply {key}={value}: {self.camera_config_report[key]!r}"
                    )
            if not self._external_trigger:
                _try_set_bool(PySpin, nodemap, "AcquisitionFrameRateEnable", True)
                if not _try_set_float(
                    PySpin, nodemap, "AcquisitionFrameRate", self.config.fps
                ):
                    raise RuntimeError(
                        "Cannot configure the free-running camera acquisition FPS."
                    )
            for key in ("AcquisitionFrameRate", "TriggerDelay", "ExposureTime", "Gain"):
                try:
                    self.camera_config_report[key] = float(
                        PySpin.CFloatPtr(nodemap.GetNode(key)).GetValue()
                    )
                except Exception:
                    self.camera_config_report[key] = None
            for key in ("Width", "Height", "OffsetX", "OffsetY"):
                self.camera_config_report[key] = int(
                    PySpin.CIntegerPtr(nodemap.GetNode(key)).GetValue()
                )
            applied_fps = self.camera_config_report.get("AcquisitionFrameRate")
            if not self._external_trigger and (
                applied_fps is None or abs(applied_fps - self.config.fps) > 0.01
            ):
                raise RuntimeError(
                    f"Camera FPS readback {applied_fps!r} differs from requested {self.config.fps}."
                )
            self.actual_width, self.actual_height = (
                self.camera_config_report["Width"],
                self.camera_config_report["Height"],
            )
            self.record_width = (
                int(self.config.width)
                if self.config.software_resize
                else self.actual_width
            )
            self.record_height = (
                int(self.config.height)
                if self.config.software_resize
                else self.actual_height
            )
            from .evidence import save_device
            save_device(self, prefix + ":camera_applied", self.camera_config_report)
            self._probe_encoder()
            self._dimensions_ready.set()
            if not self._encoder_ready.wait(8) or self._stop.is_set():
                raise RuntimeError(self.last_error or "Encoder did not become ready.")
            cam.BeginAcquisition()
            self.started_iso = self.clock.iso()
            self.ready.set()
            last_preview = 0.0
            while not self._stop.is_set():
                image = None
                try:
                    image = cam.GetNextImage(1000)
                    hardware_frame_id = int(image.GetFrameID())
                    if image.IsIncomplete():
                        self.frames_incomplete += 1
                        self._timestamps_handle.write(
                            f";{int(image.GetTimeStamp())};{self.clock.iso()};0;{hardware_frame_id};1\n"
                        )
                        continue
                    camera_timestamp = int(image.GetTimeStamp())
                    # Assign PC time immediately after a complete frame arrives,
                    # before resize/preview/queue work introduces extra latency.
                    pc_iso = self.clock.iso()
                    if image.GetPixelFormatName() != "Mono8":
                        converted = image.Convert(
                            PySpin.PixelFormat_Mono8, PySpin.HQ_LINEAR
                        )
                        frame = converted.GetNDArray().copy()
                        converted.Release()
                    else:
                        frame = image.GetNDArray().copy()
                    if frame.ndim != 2:
                        continue
                    self.frames_received += 1
                    self.waiting_for_first_frame = False
                    if self.actual_width is None:
                        self.actual_height, self.actual_width = frame.shape
                        if self.config.software_resize:
                            self.record_width, self.record_height = int(
                                self.config.width
                            ), int(self.config.height)
                        else:
                            self.record_width, self.record_height = (
                                self.actual_width,
                                self.actual_height,
                            )
                    if (frame.shape[1], frame.shape[0]) != (
                        self.record_width,
                        self.record_height,
                    ):
                        frame_out = cv2.resize(
                            frame,
                            (self.record_width, self.record_height),
                            interpolation=cv2.INTER_AREA,
                        )
                    else:
                        frame_out = frame
                    frame_out = np.ascontiguousarray(frame_out)

                    now = time.monotonic()
                    if self.config.preview_enabled and now - last_preview >= 1.0 / max(
                        1, self.config.preview_fps
                    ):
                        last_preview = now
                        preview = frame_out
                        max_side = 640
                        if max(preview.shape) > max_side:
                            scale = max_side / max(preview.shape)
                            preview = cv2.resize(
                                preview,
                                (
                                    int(preview.shape[1] * scale),
                                    int(preview.shape[0] * scale),
                                ),
                                interpolation=cv2.INTER_AREA,
                            )
                        with self._preview_lock:
                            self._preview_frame = preview.copy()
                            self._preview_sequence += 1

                    enqueued = 1
                    try:
                        self._frames.put_nowait((self.frames_received, frame_out))
                        self.frames_enqueued += 1
                    except queue.Full:
                        self.frames_dropped += 1
                        enqueued = 0
                    self._timestamps_handle.write(
                        f"{self.frames_received};{camera_timestamp};{pc_iso};{enqueued};{hardware_frame_id};0\n"
                    )
                    if now - self._timestamps_last_flush >= 1.0:
                        self._timestamps_handle.flush()
                        self._timestamps_last_flush = now
                except PySpin.SpinnakerException as exc:
                    message = str(exc).lower()
                    if "timeout" in message or "-1011" in message:
                        continue
                    self.last_error = f"PySpin acquisition error: {exc}"
                    break
                finally:
                    if image is not None:
                        try:
                            image.Release()
                        except Exception:
                            pass
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self.stopped_iso = self.clock.iso()
            self._stop.set()
            self._capture_done.set()
            try:
                self._frames.put_nowait(None)
            except queue.Full:
                pass
            try:
                if cam:
                    cam.EndAcquisition()
            except Exception:
                pass
            try:
                if cam:
                    cam.DeInit()
            except Exception:
                pass
            try:
                if cam_list:
                    cam_list.Clear()
            except Exception:
                pass
            try:
                if system:
                    system.ReleaseInstance()
            except Exception:
                pass
            try:
                if self._timestamps_handle:
                    self._timestamps_handle.flush()
                    self._timestamps_handle.close()
            except Exception:
                pass
            self._timestamps_handle = None
            gc.collect()

    def _encoder_executable(self):
        if self._video_tools is None:
            self._video_tools = resolve_video_tools()
        return self._video_tools['ffmpeg'].require()

    def _container_probe_executable(self):
        if self._video_tools is None:
            self._video_tools = resolve_video_tools()
        return self._video_tools['ffprobe'].require()

    def _ffmpeg_command(self, part: int) -> Tuple[List[str], Path, Path]:
        if (
            self._session is None
            or self.record_width is None
            or self.record_height is None
        ):
            raise RuntimeError("Camera dimensions/session are not ready.")
        selected_encoding = (
            "CPU H.264 emergency fallback"
            if self._using_cpu_fallback
            else self.config.encoding
        )
        option = ENCODING_OPTIONS.get(
            selected_encoding, ENCODING_OPTIONS["DeepEcoHAB_default (NVENC HQ)"]
        )
        self.active_encoding = option.label
        prefix = self._session.file_prefix
        pattern = (
            self._session.save_path
            / f"{prefix}_camera_part{part:03d}_%04d.{option.extension}"
        )
        log_path = None  # Encoder stderr is temporary; failures are summarized in GUI state.
        cmd = [
            self._encoder_executable(),
            "-n",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-s",
            f"{self.record_width}x{self.record_height}",
            "-r",
            str(int(self.config.fps)),
            "-i",
            "-",
            *option.encoder_args,
            "-f",
            "segment",
            "-segment_time",
            str(int(self.config.segment_seconds)),
            str(pattern),
        ]
        return cmd, pattern, log_path

    def _start_ffmpeg(self) -> None:
        cmd, pattern, log_path = self._ffmpeg_command(self._ffmpeg_part)
        import tempfile
        log_handle = tempfile.TemporaryFile(mode="w+b")
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=log_handle, stderr=log_handle, bufsize=0,
                **subprocess_options()
            )
        except Exception:
            log_handle.close()
            raise
        time.sleep(0.2)
        if proc.poll() is not None:
            log_handle.seek(0, 2)
            log_handle.seek(max(0, log_handle.tell() - 8000))
            error = log_handle.read().decode(errors="replace")
            log_handle.close()
            raise RuntimeError(f"FFmpeg failed to start: {error}")
        with self._ffmpeg_lock:
            self._ffmpeg_proc = proc
            self._ffmpeg_log_handle = log_handle
            self.current_output_pattern = str(pattern)
            self._last_writer_progress = time.monotonic()

    def _probe_encoder(self) -> None:
        """Encode one synthetic frame before declaring an externally armed camera ready."""
        executable = self._encoder_executable()
        attempts = (
            max(1, self.config.fallback_after_failures)
            if self.config.auto_cpu_fallback
            else 1
        )
        for attempt in range(attempts + 1):
            option = ENCODING_OPTIONS[
                (
                    "CPU H.264 emergency fallback"
                    if self._using_cpu_fallback
                    else self.config.encoding
                )
            ]
            command = [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-s",
                f"{self.record_width}x{self.record_height}",
                "-r",
                str(self.config.fps),
                "-i",
                "-",
                *option.encoder_args,
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ]
            probe = subprocess.run(
                command,
                input=bytes(self.record_width * self.record_height),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=12,
                **subprocess_options(),
            )
            if probe.returncode == 0:
                self.last_error = ""
                return
            self._note_encoder_failure()
            self.last_error = (
                "Encoder preflight: " + probe.stderr.decode(errors="replace")[-1500:]
            )
            if self._stop.is_set():
                raise RuntimeError("Camera start cancelled.")
        raise RuntimeError(self.last_error)

    def _kill_ffmpeg(self, reason: str, graceful: bool = False) -> None:
        with self._ffmpeg_lock:
            proc, log_handle = self._ffmpeg_proc, self._ffmpeg_log_handle
            self._ffmpeg_proc = self._ffmpeg_log_handle = None
            pattern, part = self.current_output_pattern, self._ffmpeg_part
        if proc is None:
            return
        forced = not graceful
        encoder_error = ""
        # Forced path terminates first: never wait on a buffered writer's close lock.
        try:
            if graceful:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=8)
            else:
                proc.kill()
                proc.wait(timeout=3)
        except Exception:
            forced = True
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            if log_handle:
                try:
                    if forced or proc.poll() != 0:
                        log_handle.seek(0, 2)
                        log_handle.seek(max(0, log_handle.tell() - 8000))
                        encoder_error = log_handle.read()
                        if isinstance(encoder_error, bytes):
                            encoder_error = encoder_error.decode(errors="replace")
                finally:
                    log_handle.close()
        files = []
        total = 0
        counts_known = True
        for path in (
            sorted(Path(pattern).parent.glob(Path(pattern).name.replace("%04d", "*")))
            if pattern
            else []
        ):
            # GUI state and media share the experiment root.
            info = {"file": path.name, "bytes": path.stat().st_size}
            try:
                probe = subprocess.run(
                    [
                        self._container_probe_executable(),
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=nb_frames,width,height",
                        "-of",
                        "json",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **subprocess_options(),
                )
                streams = json.loads(probe.stdout).get("streams", [])
                info["container_readable"] = probe.returncode == 0 and bool(streams)
                count = streams[0].get("nb_frames") if streams else None
                info["container_frame_count"] = (
                    int(count) if str(count).isdigit() else None
                )
                if info["container_frame_count"] is None:
                    counts_known = False
                else:
                    info["part_output_start_index"] = total
                    total += info["container_frame_count"]
            except Exception as exc:
                info.update(container_readable=False, error=str(exc))
                counts_known = False
            files.append(info)
        submitted = self._part_submitted.get(part, 0)
        self._inventory.append(
            {
                "part": part,
                "reason": reason,
                "forced": forced,
                "return_code": proc.poll(),
                "submitted_frames": submitted,
                "container_count_matches": bool(
                    not forced
                    and proc.poll() == 0
                    and counts_known
                    and total == submitted
                ),
                "decode_verified": False,
                "encoder_error": encoder_error,
                "files": files,
            }
        )
        if self._session:
            from .evidence import save_device
            save_device(
                self, self._session.file_prefix + ":camera_inventory",
                {
                    "parts": self._inventory,
                    "updated_at": self.clock.iso(),
                    "frames_dropped": self.frames_dropped,
                    "frames_incomplete": self.frames_incomplete,
                },
            )

    def _note_encoder_failure(self) -> None:
        self._encoder_failure_streak += 1
        self._stable_writes_since_failure = 0
        original_is_nvenc = "NVENC" in str(self.config.encoding).upper()
        if (
            original_is_nvenc
            and bool(self.config.auto_cpu_fallback)
            and not self._using_cpu_fallback
            and self._encoder_failure_streak
            >= max(1, int(self.config.fallback_after_failures))
        ):
            self._using_cpu_fallback = True
            self.last_error = (
                "Repeated NVENC failures reached the configured threshold; "
                "the next encoder part will use CPU H.264 emergency fallback."
            )

    def _note_encoder_write_success(self) -> None:
        self._stable_writes_since_failure += 1
        # After one minute of stable output, forget an isolated failure streak.
        if self._stable_writes_since_failure >= max(30, int(self.config.fps) * 60):
            self._encoder_failure_streak = 0
            self._stable_writes_since_failure = 0

    def _writer_loop(self) -> None:
        pending = None
        try:
            while not self._dimensions_ready.wait(0.1):
                if self._capture_done.is_set() or self._stop.is_set():
                    return
            path = (
                self._session.save_path
                / f"{self._session.file_prefix}_camera_output_frames.csv"
            )
            with path.open("x", encoding="utf-8", buffering=1024 * 1024) as ledger:
                ledger.write(
                    "source_frame_index;encoder_part;part_input_index;result;pc_datetime\n"
                )
                self._start_ffmpeg()
                self._encoder_ready.set()
                last_flush = time.monotonic()
                while pending is not None or not (
                    self._capture_done.is_set() and self._frames.empty()
                ):
                    if pending is None:
                        try:
                            pending = self._frames.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        if pending is None:
                            continue
                    if self._writer_needs_restart.is_set():
                        self._kill_ffmpeg("encoder recovery")
                        self._ffmpeg_part += 1
                        self.ffmpeg_restarts += 1
                        self._writer_needs_restart.clear()
                        try:
                            self._start_ffmpeg()
                        except Exception as exc:
                            self.last_error = f"Encoder recovery failed: {exc}"
                            self._note_encoder_failure()
                            self._writer_needs_restart.set()
                            if self._stop.wait(0.5):
                                break
                            continue
                    index, frame = pending
                    try:
                        with self._ffmpeg_lock:
                            proc = self._ffmpeg_proc
                        if (
                            proc is None
                            or proc.poll() is not None
                            or proc.stdin is None
                        ):
                            raise BrokenPipeError("Encoder unavailable")
                        data = memoryview(frame).cast("B")
                        while data:
                            written = proc.stdin.write(data)
                            if not written:
                                raise BrokenPipeError("Encoder accepted no bytes")
                            data = data[written:]
                        position = self._part_submitted.get(self._ffmpeg_part, 0)
                        self._part_submitted[self._ffmpeg_part] = position + 1
                        ledger.write(
                            f"{index};{self._ffmpeg_part};{position};submitted;{self.clock.iso()}\n"
                        )
                        self.frames_written += 1
                        self._last_writer_progress = time.monotonic()
                        self._note_encoder_write_success()
                        pending = None
                    except Exception as exc:
                        ledger.write(
                            f"{index};{self._ffmpeg_part};;write_failed;{self.clock.iso()}\n"
                        )
                        ledger.flush()
                        self.last_error = f"Encoder write failed: {exc}"
                        self._note_encoder_failure()
                        self._writer_needs_restart.set()
                        if self._stop.is_set():
                            break
                    if time.monotonic() - last_flush >= 1:
                        ledger.flush()
                        last_flush = time.monotonic()
                if pending is not None:
                    ledger.write(
                        f"{pending[0]};{self._ffmpeg_part};;not_submitted_at_stop;{self.clock.iso()}\n"
                    )
                while not self._frames.empty():
                    item = self._frames.get_nowait()
                    if item is not None:
                        ledger.write(
                            f"{item[0]};{self._ffmpeg_part};;not_submitted_at_stop;{self.clock.iso()}\n"
                        )
                ledger.flush()
        except Exception as exc:
            self.last_error = f"Camera writer failed: {exc}"
            self._stop.set()
        finally:
            self._kill_ffmpeg("writer finished", graceful=True)
            self._stop.set()
            self._running = False

    def _watchdog_loop(self) -> None:
        while not self._capture_done.wait(1.0):
            with self._ffmpeg_lock:
                proc = self._ffmpeg_proc
            if proc is not None and proc.poll() is not None:
                self.last_error = (
                    f"FFmpeg exited with code {proc.returncode}; restart requested."
                )
                self._writer_needs_restart.set()
                continue
            # If frames are waiting but no frame has been written for 30 seconds,
            # kill the encoder. This unblocks a stuck pipe write on Windows.
            if (
                not self._frames.empty()
                and time.monotonic() - self._last_writer_progress > 30.0
            ):
                self.last_error = (
                    "FFmpeg writer stalled for >30 s; encoder restart requested."
                )
                self._writer_needs_restart.set()
                self._kill_ffmpeg("watchdog stall")
