"""Device orchestration and frozen settings. GUI code never runs in workers."""

from __future__ import annotations
import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path
import queue
import shutil
import threading
import time
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.live_boards import DEVICE_KEYS, RFID_BOARD_IDS, board_config_key
from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.controllers.camera import CameraController
from deepecohab_recorder.controllers.rfid import RFIDBoardController
from deepecohab_recorder.controllers.trigger import (
    HardwareTriggerController,
    trigger_timing,
)
from deepecohab_recorder.models import (
    BoardConfig,
    CameraConfig,
    ExperimentContext,
    TriggerConfig,
)
from deepecohab_recorder.services.ports import PortRegistry
from deepecohab_recorder.services.session import RunSession
from deepecohab_recorder.services.lighting import LightingService


def model(cls, values):
    return cls(
        **{
            key: value
            for key, value in values.items()
            if key in {f.name for f in fields(cls)}
        }
    )


class ApplicationServices:
    KEYS = DEVICE_KEYS

    def __init__(self):
        self.context = ExperimentContext()
        self.repo = ConfigRepository(
            lambda: self.context.experiment_name, lambda: self.context.save_directory
        )
        self.run = RunSession(self.repo)
        self.ports = PortRegistry()
        self.controllers = {}
        self.states = {key: "Stopped" for key in self.KEYS}
        self.messages = queue.Queue()
        self.lighting = LightingService(self)
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="device-command"
        )
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._shutdown = threading.Event()
        self._owned = {}
        self._future = None
        self._monitor = threading.Thread(
            target=self._monitor_loop, daemon=True, name="device-health"
        )
        self._monitor.start()

    @property
    def trigger_controller(self):
        return self.controllers.get("trigger")

    def notify(self, level, text):
        text = str(text)
        # Faults survive closing the window even if a display burst is pending.
        if level in ("error", "warning"):
            logging.getLogger(__name__).log(logging.ERROR if level == "error" else logging.WARNING, text)
        self.messages.put((level, text))

    def busy(self, key=None):
        keys = [key] if key else self.KEYS
        return any(self.states[k] in ("Starting", "Running", "Stopping") for k in keys)

    def set_context(self, name, directory, zone):
        if self.busy() or self.ports.snapshot() or self.lighting.busy:
            if (name.strip(), str(Path(directory).expanduser()), zone) != (
                self.context.experiment_name,
                self.context.save_directory,
                self.context.timezone,
            ):
                raise RuntimeError(
                    "Experiment name, directory and timezone are locked while devices or tag matching are active."
                )
            return
        ExperimentClock(zone)
        if (name.strip(), str(Path(directory).expanduser()), zone) != (
            self.context.experiment_name,
            self.context.save_directory,
            self.context.timezone,
        ):
            self.run.detach()
        self.context.set_editable_values(name, directory)
        self.context.timezone = zone

    def reset_experiment_state(self):
        """Retire completed hardware state before importing a different bundle."""
        with self._lock:
            if (self.busy() or self.ports.snapshot() or self.lighting.busy or
                    (self._future is not None and not self._future.done())):
                raise RuntimeError('Wait for devices and pending operations to finish before loading an experiment.')
            self.run.detach()
            self.controllers.clear()
            self._owned.clear()
            self.context.discard_unused_session()
            self.repo._digests.clear()
            self.repo._last_save_path = None

    def start_devices(self, keys, settings, validated=None, override=False):
        """Capture immutable inputs and check device settings before starting."""
        keys = list(dict.fromkeys(keys))
        self._check_device_keys(keys)
        if not keys:
            return
        frozen = copy.deepcopy(settings)
        with self._lock:
            if any(self.busy(key) for key in keys):
                raise RuntimeError(
                    "One of the selected devices is already running or changing state."
                )
            if self._future and not self._future.done():
                raise RuntimeError("Wait for the current device operation to finish.")
            ports = []
            for key in keys:
                value = (
                    frozen["boards"][board_config_key(key)]
                    if key.startswith("board")
                    else frozen.get("hardware_trigger" if key == "trigger" else key, {})
                )
                if key.startswith("board") and not value.get("active"):
                    raise ValueError(f"{key}: enable the board on the Acquisition tab.")
                if key != "camera":
                    port = str(value.get("com_port", "")).upper()
                    if not port:
                        raise ValueError(f"{key}: select a COM port.")
                    if port in ports or port in self.ports.snapshot():
                        raise ValueError(
                            f"{port} is selected more than once or already in use."
                        )
                    ports.append(port)
                if key == "trigger":
                    trigger_timing(model(TriggerConfig, value))
            if (
                any(key.startswith("board") for key in keys)
                and not self.run.active
                and validated is not None
            ):
                self.repo.replace(validated)
            self.repo.update_paths(
                {
                    "acquisition.boards": frozen["boards"],
                    **{
                        f"acquisition.{key}": frozen[key]
                        for key in (
                            "camera",
                            "hardware_trigger",
                            "launch",
                            "ui",
                        )
                        if key in frozen
                    },
                }
            )
            self._cancel = threading.Event()
            for key in keys:
                self.states[key] = "Starting"
            self._future = self.executor.submit(
                self._start_batch, keys, frozen, override, self._cancel
            )

    def _start_batch(self, keys, settings, override, cancel):
        # Arm the camera/encoder before enabling an external pulse source.
        order = sorted(keys, key=lambda key: key == "trigger")
        try:
            for key in order:
                if cancel.is_set():
                    break
                self._start_one(key, settings, override, cancel)
            if cancel.is_set():
                self.notify("info", "Device startup cancelled.")
        except Exception as exc:
            self.notify("error", f"Start failed: {exc}")
            self.run.log("start_error", error=str(exc))
            # Already recording RFID data remains available; the user can stop it explicitly.
        finally:
            with self._lock:
                for key in keys:
                    if self.states[key] == "Starting":
                        self.states[key] = "Stopped"
            self.context.discard_unused_session()

    @classmethod
    def _check_device_keys(cls, keys):
        unknown = set(keys) - set(cls.KEYS)
        if unknown:
            raise ValueError(
                "Unsupported device(s): "
                + ", ".join(sorted(unknown))
                + ". Standalone lickometers are recorded through RFID TTL inputs."
            )

    def _start_one(self, key, settings, override, cancel):
        self._check_device_keys([key])
        port, controller = "", None
        try:
            snapshot = self.context.mark_running(key)
            if key.startswith("board"):
                conf = copy.deepcopy(settings["boards"][board_config_key(key)])
                controller = RFIDBoardController(model(BoardConfig, conf))
            elif key == "camera":
                conf = settings["camera"]
                controller = CameraController(model(CameraConfig, conf))
            else:
                conf = settings["hardware_trigger"]
                controller = HardwareTriggerController()
            if key != "camera":
                port = conf["com_port"]
                self.ports.acquire(port, key)
                self._owned[key] = port
            controller.state_sink = self.save_device_state
            self.controllers[key] = controller
            if key == "camera":
                controller.start(
                    snapshot, bool(settings["launch"].get("externally_triggered", True))
                )
                self._await_ready(controller, cancel, 55)
            elif key == "trigger":
                if settings["launch"].get("externally_triggered") and self.busy(
                    "camera"
                ):
                    camera = self.controllers.get("camera")
                    if (
                        not camera
                        or not camera.ready.is_set()
                        or not camera.is_running()
                    ):
                        raise RuntimeError("The external-trigger camera is not ready.")
                delay = float(settings["launch"].get("delay_seconds", 2))
                if cancel.wait(delay):
                    raise RuntimeError("Startup cancelled before trigger arming.")
                controller.start(snapshot, model(TriggerConfig, conf))
                self._await_ready(controller, cancel, 9)
            else:
                controller.start(snapshot)
            with self._lock:
                self.states[key] = "Running"
            self._update_ports()
            if key.startswith("board"):
                self.run.board_started(key, controller, conf, override)
            self.run.log("device_started", device=key, settings=conf)
            self.notify("info", f"{key} started.")
        except Exception:
            if controller:
                controller.stop()
            if key in self.run.active and controller and not controller.is_running():
                try:
                    self.run.board_stopped(key, controller)
                except Exception as exc:
                    self.notify("error", f"Could not finalize failed start: {exc}")
            if controller and controller.is_running():
                self.states[key] = "Stopping"
            else:
                self.ports.release(port, key)
                self._owned.pop(key, None)
                self.context.mark_stopped(key)
                self.states[key] = "Stopped"
            raise

    @staticmethod
    def _await_ready(controller, cancel, seconds):
        deadline = time.monotonic() + seconds
        while not controller.ready.wait(0.05):
            if cancel.is_set():
                raise RuntimeError("Startup cancelled.")
            if not controller.is_running() or time.monotonic() >= deadline:
                raise RuntimeError(
                    controller.last_error or "Device did not become ready in time."
                )

    def stop_devices(self, keys=None):
        keys = list(keys or self.KEYS)
        self._check_device_keys(keys)
        self._cancel.set()
        with self._lock:
            for key in keys:
                if self.busy(key):
                    self.states[key] = "Stopping"
                controller = self.controllers.get(key)
                if controller and controller.is_running():
                    controller.request_stop()
            self._future = self.executor.submit(self._stop_batch, keys)

    def _stop_batch(self, keys):
        for key in sorted(keys, key=lambda key: key != "trigger"):
            controller = self.controllers.get(key)
            if controller:
                controller.stop()
                if controller.is_running():
                    self.notify(
                        "error",
                        f"{key}: workers are still stopping; this device remains locked.",
                    )
                    continue
            self._complete(key)

    def save_device_state(self, key, value):
        with self.repo.lock:
            history = self.repo.data["acquisition"].get("device_history", {})
            history[key] = copy.deepcopy(value)
            self.repo.update_paths({"acquisition.device_history": history})
            self.repo.persist_runtime()

    def _complete(self, key):
        with self._lock:
            controller = self.controllers.get(key)
            if self.states[key] == "Stopped":
                return
            if controller and key.startswith("board"):
                self.run.board_stopped(key, controller)
            if controller and controller.last_error:
                self.notify("error", f"{key}: {controller.last_error}")
            self.states[key] = "Stopped"
            self.ports.release(self._owned.pop(key, ""), key)
            self.context.mark_stopped(key)
            self.run.log(
                "device_stopped",
                device=key,
                error=controller.last_error if controller else "",
            )

    def _update_ports(self):
        roles = {
            **{f"board{i}": f"RFID_DAQ_{i}" for i in RFID_BOARD_IDS},
            "trigger": "Hardware_trigger",
        }
        previous = (
            self.repo.data["acquisition"].get("com_ports", {})
            if self.run.active
            else {}
        )
        if not isinstance(previous, dict):
            previous = {}
        previous.update({port.upper(): roles[key] for key, port in self._owned.items()})
        if self.lighting.controller:
            previous[self.lighting.controller.config.com_port.upper()] = (
                "Environmental_controller"
            )
        self.repo.update_paths({"acquisition.com_ports": previous})
        if self.run.active and not self.run.override:
            self.repo.save()

    def _monitor_loop(self):
        health_at = 0
        while not self._shutdown.wait(0.3):
            try:
                for key, controller in list(self.controllers.items()):
                    if (
                        self.states[key] in ("Running", "Stopping")
                        and not controller.is_running()
                    ):
                        self._complete(key)
                if self.busy() and time.monotonic() >= health_at:
                    health_at = time.monotonic() + 30
                    free = shutil.disk_usage(self.context.save_directory).free
                    counters = {
                        key: {
                            "state": self.states[key],
                            "error": controller.last_error,
                            **{
                                field: getattr(controller, field, None)
                                for field in (
                                    "lines_ok",
                                    "lines_written",
                                    "lines_bad",
                                    "lines_dropped",
                                    "queue_peak",
                                )
                            },
                        }
                        for key, controller in self.controllers.items()
                    }
                    self.run.log("health", free_bytes=free, devices=counters)
                    if free < 100 * 1024**2:
                        self.notify(
                            "error",
                            "Less than 100 MiB free; stopping devices to protect recording files.",
                        )
                        self.stop_devices()
                    elif free < 5 * 1024**3:
                        self.notify(
                            "warning",
                            f"Low disk space: {free / 1024**3:.2f} GiB remaining.",
                        )
            except Exception as exc:
                self.notify("error", f"Recording health check: {exc}")

    def experiment_now(self):
        """Correlate device time with monotonic receipt time; never chase PC clock changes."""
        from datetime import datetime, timezone

        sid = (
            self.repo.data["recording"]["timeline"]
            .get("phase_schedule", {})
            .get("schedule_id")
        )
        for status in (
            self.lighting.controller.inbox.status if self.lighting.controller else None,
        ):
            if status and status.schedule_id == sid and status.utc_seconds:
                seconds = (
                    status.utc_seconds + time.monotonic() - status.received_monotonic
                )
                return datetime.fromtimestamp(seconds, timezone.utc)
        if self.run.active and hasattr(self.run, "start_monotonic"):
            seconds = (
                ExperimentClock(self.context.timezone).parse(self.run.start).timestamp()
                + time.monotonic()
                - self.run.start_monotonic
            )
            return datetime.fromtimestamp(seconds, timezone.utc)
        return ExperimentClock(self.context.timezone).now()

    def shutdown(self):
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        if self.lighting.connected or self.lighting.busy:
            self.lighting.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.lighting.shutdown()
