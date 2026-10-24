"""Installed resources, writable application paths and worker interpreter.

No path creation or Qt imports occur when this module is imported.
"""
from importlib import resources
import os
from pathlib import Path
import sys

from platformdirs import user_data_path, user_log_path

APP_DIRECTORY = "DeepEcoHabAcquisition"


def app_data_directory() -> Path:
    override = os.environ.get("DEEPECOHAB_ACQUISITION_HOME")
    return Path(override).expanduser().resolve() if override else user_data_path(APP_DIRECTORY, appauthor=False)


def log_directory() -> Path:
    if os.environ.get("DEEPECOHAB_ACQUISITION_HOME"):
        return app_data_directory() / "logs"
    return user_log_path(APP_DIRECTORY, appauthor=False)


def default_experiment_directory() -> Path:
    if os.environ.get('DEEPECOHAB_ACQUISITION_PREVIEW') == '1':
        return app_data_directory().parent / 'experiments'
    return Path.home() / "DeepEcoHAB_data"


def asset(name: str):
    return resources.files("deepecohab_recorder").joinpath("assets", name)


def worker_python() -> str:
    """Use the same environment's console interpreter for JSON-line IPC.

    Windows GUI entry points may set sys.executable to pythonw.exe, whose stdout
    is unavailable. Never resolve a different Python from PATH.
    """
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    if not executable.is_file():
        raise RuntimeError(f"Worker Python is missing: {executable}. Reinstall the application.")
    return str(executable)
