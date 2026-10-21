from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from deepecohab_recorder.experiment_files import experiment_stem
from typing import Any, Dict, Optional, Set
import re
import threading
import uuid

NEW_DAQ = "DeepEcoHAB RFID DAQ v1.0"
OLD_DAQ = "EcoHAB Old RFID DAQ + new firmware"
DAQ_V15 = "DeepEcoHAB RFID DAQ v1.5"
SUPPORTED_DAQ_TYPES = (NEW_DAQ, DAQ_V15, OLD_DAQ)


@dataclass
class BoardConfig:
    board_id: int
    active: bool
    hardware: str = NEW_DAQ
    com_port: str = ""
    baudrate: int = 115200
    raw_log_enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CameraConfig:
    width: int = 1280
    height: int = 1280
    fps: int = 30
    segment_seconds: int = 3600
    preview_enabled: bool = True
    preview_fps: int = 10
    software_resize: bool = False
    encoding: str = "DeepEcoHAB_default (NVENC HQ)"
    trigger_source: str = "Line0"
    trigger_activation: str = "RisingEdge"
    trigger_delay_us: float = 24.0
    trigger_overlap: str = "ReadOut"
    auto_cpu_fallback: bool = True
    fallback_after_failures: int = 3


@dataclass
class TriggerConfig:
    enabled: bool = True
    com_port: str = ""
    baudrate: int = 115200
    timing_mode: str = "frequency"  # frequency | interval
    frequency_hz: float = 30.0
    interval_text: str = "00:00:00:033"
    pulse_ms: float = 4.0
    train_mode: str = "duration"  # duration | count
    count: int = 12_960_000
    duration_days: int = 5
    duration_hours: int = 0
    duration_minutes: int = 0
    start_mode: str = "immediate"  # immediate | scheduled
    scheduled_hhmmss: str = "12:00:00"


@dataclass
class LickometerConfig:
    enabled: bool = False
    com_port: str = ""
    baudrate: int = 115200
    device_id: int = 1
    electrode_1: int = 1
    electrode_2: int = 3
    electrode_3: int = 5
    electrode_4: int = 7

    # Accepted only for old config files; the passive recorder never applies them.
    device_uid: str | None = None
    automatic_recalibration: dict | None = None
    connection_monitoring: dict | None = None
    control_mode: str = "passive_usb"

    def electrode_map(self) -> tuple[int, int, int, int]:
        return (
            int(self.electrode_1),
            int(self.electrode_2),
            int(self.electrode_3),
            int(self.electrode_4),
        )


@dataclass
class LightingConfig:
    enabled: bool = False
    com_port: str = ""
    baudrate: int = 115200
    device_uid: str | None = None


@dataclass(frozen=True)
class SessionSnapshot:
    experiment_name: str
    save_directory: str
    session_id: str
    started_iso: str
    file_prefix: str
    timezone: str = "Europe/Warsaw"

    @property
    def save_path(self) -> Path:
        return Path(self.save_directory)


class ExperimentContext:
    """Thread-safe application-level experiment/session state.

    The experiment name and save directory can be edited while no device is
    active. The first started device freezes a session snapshot. Additional
    devices join the same session. The snapshot is released after all devices
    have stopped.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.experiment_name = "experiment"
        from .runtime_paths import default_experiment_directory
        self.save_directory = str(default_experiment_directory())
        self.timezone = "Europe/Warsaw"
        self._snapshot: Optional[SessionSnapshot] = None
        self._running_devices: Set[str] = set()

    def set_editable_values(self, experiment_name: str, save_directory: str) -> None:
        with self._lock:
            if self._running_devices:
                raise RuntimeError(
                    "Experiment name and save directory are locked while hardware is running."
                )
            self.experiment_name = experiment_stem(experiment_name)
            self.save_directory = str(Path(save_directory).expanduser())

    def ensure_session(self) -> SessionSnapshot:
        with self._lock:
            if self._snapshot is None:
                from deepecohab_recorder.clock import ExperimentClock

                now = ExperimentClock(self.timezone).now()
                experiment_name = self.experiment_name.strip() or "experiment"
                safe_name = experiment_stem(experiment_name)
                self._snapshot = SessionSnapshot(
                    experiment_name=experiment_name,
                    save_directory=str(Path(self.save_directory).expanduser()),
                    session_id=uuid.uuid4().hex[:12],
                    started_iso=now.isoformat(),
                    file_prefix=f"{safe_name}_{now.strftime('%Y%m%d_%H%M%S')}",
                    timezone=self.timezone,
                )
                self._snapshot.save_path.mkdir(parents=True, exist_ok=True)
            return self._snapshot

    def mark_running(self, device_key: str) -> SessionSnapshot:
        with self._lock:
            snap = self.ensure_session()
            self._running_devices.add(device_key)
            return snap

    def mark_stopped(self, device_key: str) -> None:
        with self._lock:
            self._running_devices.discard(device_key)
            if not self._running_devices:
                self._snapshot = None

    def active_snapshot(self) -> Optional[SessionSnapshot]:
        with self._lock:
            return self._snapshot

    def discard_unused_session(self) -> None:
        with self._lock:
            if not self._running_devices:
                self._snapshot = None

    def running_devices(self) -> Set[str]:
        with self._lock:
            return set(self._running_devices)

    def is_locked(self) -> bool:
        with self._lock:
            return bool(self._running_devices)
