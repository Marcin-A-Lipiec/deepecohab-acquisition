"""Atomic per-user application preferences, independent of experiment schemas."""
import threading

from .runtime_paths import app_data_directory
from .utils import atomic_write_json, read_json

_lock = threading.RLock()


class PreferencesError(ValueError):
    pass


def preferences_path():
    return app_data_directory() / 'preferences.json'


def read_preferences():
    path = preferences_path()
    if not path.exists():
        return {'format': 'deepecohab_application_preferences', 'version': 1, 'video_tools': {}}
    try:
        data = read_json(path)
        if not isinstance(data, dict):
            raise ValueError('Application preferences must be a JSON object.')
        if data.get('format') != 'deepecohab_application_preferences' or data.get('version') != 1:
            raise ValueError('Unsupported application preferences format/version.')
        return data
    except (ValueError, OSError) as exc:
        raise PreferencesError(f'Cannot read {path}: {exc}. Repair this application preferences file; experiment metadata is separate.') from exc


def update_section(section, values):
    with _lock:
        data = read_preferences()
        current = data.setdefault(section, {})
        if not isinstance(current, dict):
            raise PreferencesError(f'Invalid {section} settings in {preferences_path()}; the file has been retained for repair.')
        current.update(values)
        atomic_write_json(preferences_path(), data)
