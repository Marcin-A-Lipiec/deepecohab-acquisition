"""RFID-defined run boundaries and durable evidence of overrides/errors."""

from __future__ import annotations
import copy
import threading
import time
import uuid
import os
from deepecohab_recorder.clock import ExperimentClock, establish_schedule, fixed_phase_origin
from deepecohab_recorder.schema import normalize_events
from deepecohab_recorder.experiment_timing import rfid_started, rfid_stopped, select_timing


class RunSession:
    def __init__(self, repository):
        self.repo = repository
        self.lock = threading.RLock()
        self.active = set()
        self.run_id = None
        self.start = self.end = None
        self.override = False
        self.started_without_config = False
        self.path = None
        self.timeline = {}
        self.devices = {}

    def detach(self):
        """Release a completed run before changing experiment/template identity."""
        with self.lock:
            if self.active:
                raise RuntimeError("Cannot detach a run while RFID boards are active.")
            self.run_id = self.repo.run_id = None
            self.start = self.end = self.path = None
            self.timeline, self.devices = {}, {}
            self.override = self.started_without_config = False
            for key in ('clock', 'start_monotonic'):
                self.__dict__.pop(key, None)

    def log(self, event, **details):
        with self.lock, self.repo.lock:
            clock = getattr(self, 'clock', ExperimentClock(self.repo.data['recording']['timeline']['recording_timezone']))
            history = self.repo.data['acquisition'].get('recording_history', {})
            entry = dict(datetime=clock.iso(), event=event, **details)
            if event == 'health':
                history['last_health'] = entry
            else:
                history['events'] = (history.get('events', []) + [entry])[-100:]
            self.repo.update_paths({'acquisition.recording_history': history})
            self.repo.persist_runtime()

    def board_started(self, key, controller, settings, override=False):
        with self.lock, self.repo.lock:
            if not self.active:
                self.clock = controller.clock
                stamp = self.clock.parse(controller.started_iso).strftime("%Y%m%d_%H%M%S")
                self.run_id = f"run_{stamp}_{uuid.uuid4().hex[:8]}"
                self.repo.run_id = self.run_id
                self.start, self.end, self.override = (
                    controller.started_iso,
                    None,
                    override,
                )
                self.start_monotonic = time.monotonic()
                self.started_without_config = override
                self.devices = {}
                self.path = self.repo.paths()[0]
                previous = establish_schedule(
                    self.repo.data["recording"]["timeline"], self.start
                )
                self.timeline = dict(
                    previous, start_datetime=self.start, end_datetime=None
                )
                for field in (
                    "analysis_start_datetime",
                    "missing_initial_seconds",
                    "recorded_before_origin_seconds",
                    "analysis_origin_error",
                ):
                    self.timeline.pop(field, None)
                try:
                    self.timeline.update(
                        fixed_phase_origin(
                            self.start,
                            self.clock.name,
                            previous["phase_schedule"],
                            previous["start_from"],
                        )
                    )
                    self.timeline.pop("analysis_origin_error", None)
                except ValueError as exc:
                    # Keep true acquisition start even if an ambiguous phase schedule needs correction.
                    self.timeline["analysis_origin_error"] = str(exc)
                self.log(
                    "run_started", start_datetime=self.start, config_override=override
                )
            self.override = self.override or override
            self.started_without_config = self.started_without_config or override
            self.active.add(key)
            if key in self.devices:
                history = self.devices[key].get("previous_epochs", []) + [
                    {
                        k: v
                        for k, v in self.devices[key].items()
                        if k != "previous_epochs"
                    }
                ]
            else:
                history = []
            self.devices[key] = {
                "previous_epochs": history,
                "start_datetime": controller.started_iso,
                "end_datetime": None,
                "config": copy.deepcopy(settings),
                "data_file": os.path.relpath(controller.data_file, self.repo.paths()[1].parent).replace("\\", "/"),
            }
            experiment_timeline = self.repo.data['recording']['timeline']
            experiment_timeline['phase_schedule'] = self.timeline['phase_schedule']
            self.repo.update_paths({'recording.timeline': rfid_started(experiment_timeline, controller.started_iso)})
            self.log("board_started", device=key, **self.devices[key])
            self.apply_runtime()
            self._runtime_file()
            # An override deliberately does not create or overwrite an incomplete config.
            if not self.override:
                self.repo.save()
            self._runtime_file()

    def apply_runtime(self):
        if self.run_id:
            self.repo.update_paths(
                {
                    "acquisition._provenance": {
                        "run_id": self.run_id,
                        "config_override": self.started_without_config,
                        "rfid_devices": self.devices,
                    },
                }
            )

    def _runtime_file(self):
        if self.run_id:
            history = self.repo.data['acquisition'].get('recording_history', {})
            runs = [run for run in history.get('runs', []) if run.get('run_id') != self.run_id]
            runs.append(dict(run_id=self.run_id, timeline=self.timeline,
                             config_override=self.started_without_config, devices=self.devices))
            history['runs'] = runs
            self.repo.update_paths({'acquisition.recording_history': history})
            self.repo.persist_runtime()

    def board_stopped(self, key, controller):
        with self.lock, self.repo.lock:
            if key not in self.active:
                return
            self.active.remove(key)
            stopped = controller.stopped_iso or controller.clock.iso()
            self.devices[key]["end_datetime"] = stopped
            self.devices[key]['stop_status'] = 'failed' if controller.last_error else 'clean'
            self.devices[key]['counters'] = {field: getattr(controller, field, None) for field in ('lines_ok', 'lines_written', 'lines_dropped', 'lines_bad', 'bytes_read', 'queue_peak')}
            self.devices[key]['error'] = controller.last_error
            self.devices[key]['malformed_examples'] = getattr(controller, 'malformed_examples', [])
            self.log(
                "board_stopped",
                device=key,
                end_datetime=stopped,
                error=controller.last_error,
                received=controller.lines_ok,
                written=controller.lines_written,
                dropped=controller.lines_dropped,
                malformed=controller.lines_bad,
            )
            if not self.active:
                self.end = max(
                    (
                        item["end_datetime"]
                        for item in self.devices.values()
                        if item["end_datetime"]
                    ),
                    key=lambda value: self.clock.parse(value).timestamp(),
                )
                self.timeline["end_datetime"] = self.end
                self.log("run_stopped", end_datetime=self.end)
            clean = not controller.last_error and all(
                d.get('stop_status') != 'failed' for d in self.devices.values())
            self.repo.update_paths({'recording.timeline': rfid_stopped(
                self.repo.data['recording']['timeline'], stopped,
                complete=not self.active, clean=clean)})
            self.apply_runtime()
            if not self.active:
                try:
                    events = normalize_events(
                        self.repo.data["recording"]["events"], self.clock, self.end
                    )
                    for bout in events["bouts"]:
                        if not bout["end_user_specified"] and not bout.get(
                            "finalized_run_id"
                        ):
                            bout["finalized_run_id"] = self.run_id
                    self.repo.update_paths({"recording.events": events})
                except ValueError as exc:
                    # Scheduled future events cannot have an inferred end before their start.
                    self.log("event_finalization_error", error=str(exc))
                    self.repo.update_paths(
                        {
                            "acquisition._provenance": {
                                "event_finalization_error": str(exc)
                            }
                        }
                    )
            self._runtime_file()
            if not self.override:
                self.repo.save()
            self._runtime_file()

    def save_metadata(self, updates):
        with self.lock, self.repo.lock:
            self.repo.update_paths(updates)
            self.repo.update_paths({'recording.timeline': select_timing(
                self.repo.data['recording']['timeline'], require_manual=True)})
            self.apply_runtime()
            if self.run_id and self.end and "recording.events" in updates:
                events = normalize_events(
                    self.repo.data["recording"]["events"], self.clock, self.end
                )
                for bout in events["bouts"]:
                    if not bout["end_user_specified"] and not bout.get(
                        "finalized_run_id"
                    ):
                        bout["finalized_run_id"] = self.run_id
                self.repo.update_paths({"recording.events": events})
            paths = self.repo.save()
            # A later save attaches the original RFID start, including after an override.
            self.override = False
            self.log("config_saved", revision=self.repo.revision)
            return paths
