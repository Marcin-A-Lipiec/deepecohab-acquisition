"""Independent lighting lifecycle; acquisition stop never changes light output."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import copy
from deepecohab_recorder.clock import establish_schedule
from deepecohab_recorder.controllers.lighting import LightingController
from deepecohab_recorder.models import LightingConfig


class LightingService:
    def __init__(self, app):
        self.app = app
        self.controller = None
        self.future = None
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="lighting-command"
        )
        self.error = ""

    @property
    def connected(self):
        return self.controller is not None

    @property
    def busy(self):
        return bool(self.future and not self.future.done())

    def submit(self, action, *args):
        if self.busy:
            raise RuntimeError("Wait for the lighting operation to finish.")
        self.future = self.executor.submit(self._execute, action, copy.deepcopy(args))

    def _execute(self, action, args):
        self.error = ""
        try:
            getattr(self, "_" + action)(*args)
        except Exception as exc:
            self.error = str(exc)
            self.app.notify("error", f"Lighting: {exc}")

    def _connect(self, values):
        if self.controller:
            raise RuntimeError("Lighting monitoring is already connected.")
        config = LightingConfig(**values)
        self.app.ports.acquire(config.com_port, "lighting")
        link = LightingController(
            config, self.app.context.save_directory, self.app.context.timezone, self.app.save_device_state
        )
        try:
            status = link.open()
            self.controller = link
            self.app.repo.update_paths(
                {"acquisition.environment.lighting.device_uid": status.uid}
            )
            self.app._update_ports()
            self.app.notify(
                "info", f"Lighting connected: {status.mode}; output was not changed."
            )
        except Exception:
            link.close()
            self.app.ports.release(config.com_port, "lighting")
            raise

    def _apply(self, timeline):
        if not self.controller:
            raise RuntimeError("Connect the lighting controller first.")
        # A committed anchor is reused on every reconnection and RFID restart.
        timeline = establish_schedule(timeline)
        self.app.repo.update_paths(
            {
                "recording.timeline": timeline,
                "acquisition._provenance.device_settings.lighting_pending": {
                    "schedule_id": timeline["phase_schedule"]["schedule_id"]
                },
            }
        )
        self.app.repo.save()  # Persist the intended anchor before any hardware mutation.
        status = self.controller.apply(timeline)
        self.app.repo.update_paths(
            {
                "recording.timeline": timeline,
                "acquisition.environment.lighting.device_uid": status.uid,
                "acquisition._provenance.device_settings.lighting_pending": None,
                "acquisition._provenance.device_settings.lighting": dict(
                    asdict(status),
                    applied_at=self.controller.clock.iso(),
                    protocol_version=2,
                ),
            }
        )
        self.app.repo.save()
        self.app.notify("info", "Lighting schedule verified and saved to the experiment configuration.")

    def _stop(self):
        if self.controller:
            self.controller.stop_lighting()
            self.app.notify("info", "Lighting explicitly stopped; output is off.")

    def _disconnect(self):
        link = self.controller
        if link:
            link.close()
            self.app.ports.release(link.config.com_port, "lighting")
            self.controller = None
            self.app.notify(
                "info",
                "Lighting monitoring disconnected; the Arduino continues its schedule.",
            )

    def close(self):
        # Queue behind any acknowledged mutation so port ownership ends only after I/O.
        self.future = self.executor.submit(self._execute, "disconnect", ())

    def shutdown(self):
        self.executor.shutdown(wait=False)
