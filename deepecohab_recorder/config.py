"""Thread-safe canonical config repository with compact runtime state and legacy notices."""

from __future__ import annotations
from deepecohab_recorder import SOFTWARE_LABEL, __version__
import copy
from pathlib import Path
import threading
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.config_bundle import load_config_bundle, save_config_bundle, bundle_digest
from deepecohab_recorder.experiment_files import experiment_stem, discover_config, filename
from deepecohab_recorder.event_metadata import storage_config, compartment_names, positions
from deepecohab_recorder.schema import (
    CONFIG_VERSION,
    default_config,
    merge,
    migrate_config,
    normalize_events,
    identity_boards,
    retire_lickometer_settings,
    validate_config,
)
from deepecohab_recorder.topology import normalize_layout
from deepecohab_recorder.experiment_timing import select_timing
from deepecohab_recorder.utils import read_json


def get_path(data, path):
    for key in path.split("."):
        data = data[key]
    return data


def set_path(data, path, value):
    parts = path.split(".")
    for key in parts[:-1]:
        data = data.setdefault(key, {})
    data[parts[-1]] = copy.deepcopy(value)


class ConfigRepository:
    def __init__(
        self,
        experiment_name_getter=lambda: "experiment",
        save_dir_getter=lambda: str(Path.cwd()),
    ):
        self._name, self._directory = experiment_name_getter, save_dir_getter
        self._config = default_config(self._name())
        self.lock = threading.RLock()
        self.notice, self.last_import_path = "", None
        self.revision, self.run_id = 0, None
        self._digests = {}
        self._last_save_path = None

    @property
    def data(self):
        with self.lock:
            return copy.deepcopy(self._config)

    def paths(self):
        root = Path(self._directory()).expanduser()
        stem = experiment_stem(self._name())
        return root / filename(stem, "acquisition_gui_state", "json"), root / filename(stem, "config", "json")

    def discover(self):
        return discover_config(self._directory(), self._name())

    def import_file(self, path):
        notices = []
        migrated = load_config_bundle(path, notices)
        source = read_json(path)
        self._digests[str(Path(path).resolve())] = bundle_digest(path)
        self._last_save_path = None
        legacy = (
            source.get("version") != CONFIG_VERSION
            or "recording" not in source
            or "acqusition" in source
        )
        with self.lock:
            self._config, self.last_import_path = migrated, Path(path)
            family = "recorder " if "recording" not in source else ""
            self.notice = (
                f"Loaded legacy {family}format v{source.get('version', 'unversioned')}. Current format is v{CONFIG_VERSION}. Save to {self.paths()[1].name} to update."
                if legacy
                else ""
            )
            self.notice = "\n".join(filter(None, [self.notice, *notices]))
            return copy.deepcopy(migrated)

    def replace(self, data):
        with self.lock:
            self._config = copy.deepcopy(data)

    def update_paths(self, updates):
        with self.lock:
            result = copy.deepcopy(self._config)
            for path, value in updates.items():
                try:
                    existing = get_path(result, path)
                except KeyError:
                    existing = None
                set_path(
                    result,
                    path,
                    (
                        merge(existing, value)
                        if isinstance(existing, dict) and isinstance(value, dict)
                        and path != "acquisition.preprocessing"
                        else value
                    ),
                )
            recording = result["recording"]
            clock = ExperimentClock(recording["timeline"]["recording_timezone"])
            if "recording.events" in updates:
                recording["events"] = normalize_events(
                    recording["events"],
                    clock,
                    recording["timeline"].get("end_datetime"),
                )
            if "recording.layout" in updates:
                recording["layout"] = normalize_layout(
                    recording["layout"], identity_boards(result)
                )
            self._config = result
            return copy.deepcopy(result)

    def save(self):
        with self.lock:
            state_path, json_path = self.paths()
            root = json_path.parent
            root.mkdir(parents=True, exist_ok=True)
            self._config = migrate_config(self._config)
            clock = ExperimentClock(
                self._config["recording"]["timeline"]["recording_timezone"]
            )
            self._config['recording']['timeline'] = select_timing(
                self._config['recording']['timeline'], require_manual=True)
            self._config["recording"]["events"] = normalize_events(
                self._config["recording"]["events"], clock,
                self._config["recording"]["timeline"].get("end_datetime"),
            )
            known = set(compartment_names(self._config["recording"]["layout"]))
            for bout in self._config["recording"]["events"]["bouts"]:
                unknown = set(positions(bout.get("position"))) - known
                if unknown:
                    raise ValueError(f"Event {bout['name']!r} references compartments missing from the saved layout: {', '.join(sorted(unknown))}.")
            previous_path = self._last_save_path or self.last_import_path
            rename_from = (previous_path if previous_path and previous_path.exists() and
                           previous_path.resolve().parent == json_path.resolve().parent and
                           previous_path.resolve() != json_path.resolve() else None)
            if rename_from:
                previous_digest = self._digests.get(str(rename_from.resolve()))
                if previous_digest is not None and bundle_digest(rename_from) != previous_digest:
                    raise ValueError('The previous experiment configuration changed. Reload before renaming.')
            expected = self._digests.get(str(json_path.resolve())) if json_path.exists() else None
            if json_path.exists():
                if expected is not None and bundle_digest(json_path) != expected:
                    raise ValueError('Main config or GUI state changed outside this window. Reload before saving.')
            retire_lickometer_settings(self._config)
            self._config["version"] = CONFIG_VERSION
            self.revision += 1
            self._config["acquisition"].setdefault("_provenance", {}).update(
                software=SOFTWARE_LABEL,
                software_version=__version__,
                software_patch=20,
                updated_at=clock.iso(),
                config_revision=self.revision,
            )
            self._config["recording"]["name"] = self._name()
            stored = storage_config(self._config)
            main = save_config_bundle(json_path, self._config, expected, rename_from=rename_from)
            self._last_save_path = json_path
            self._digests[str(json_path.resolve())] = bundle_digest(json_path)
            self.notice = ""
            return state_path, json_path

    def snapshot(self, label):
        # Compatibility for older callers: no per-save/session files are created.
        return None

    def persist_runtime(self):
        from .recording_history import persist_runtime
        with self.lock:
            main = self.paths()[1]
            current = bundle_digest(main) if main.exists() else None
            expected = self._digests.get(str(main.resolve()))
            persist_runtime(main, self._config)
            if main.exists() and (expected is None or current == expected):
                self._digests[str(main.resolve())] = bundle_digest(main)

    def preflight(self, boards=None):
        try:
            path = self.discover()
            if path is None:
                return None, [f"No experiment configuration in {self.paths()[1].parent}. Expected {self.paths()[1].name}."], ""
            source = read_json(path)
            notices = []
            cfg = load_config_bundle(path, notices)
            legacy = (
                source.get("version") != CONFIG_VERSION
                or "recording" not in source
                or "acqusition" in source
            )
            notice = (
                f"Loaded legacy config format v{source.get('version', 'unversioned')}; save as config v{CONFIG_VERSION}."
                if legacy
                else ""
            )
            return cfg, validate_config(cfg, boards), "\n".join(filter(None, [notice, *notices]))
        except Exception as exc:
            return None, [f"Cannot use experiment configuration: {exc}"], ""
