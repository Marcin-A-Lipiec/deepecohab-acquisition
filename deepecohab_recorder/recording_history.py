"""Compact acquisition evidence in the named GUI-state file; legacy journals read-only."""
import copy
import json
from pathlib import Path

from .experiment_files import config_name, filename
from .utils import atomic_write_json, read_json


def recording_runs(root, config=None):
    runs = list((config or {}).get('acquisition', {}).get('recording_history', {}).get('runs', []))
    for path in sorted((Path(root) / 'session_configs').glob('*/run.json')):
        try:
            run = read_json(path)
            run.setdefault('run_id', path.parent.name)
            runs.append(run)
        except (OSError, ValueError, TypeError):
            continue
    result, seen = [], set()
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get('devices'), dict):
            continue
        key = run.get('run_id') or json.dumps(run, sort_keys=True)
        if key not in seen:
            result.append(copy.deepcopy(run))
            seen.add(key)
    return result


def import_history(config, root):
    runs = recording_runs(root, config)
    if runs:
        config.setdefault('acquisition', {}).setdefault('recording_history', {})['runs'] = runs
    return config


def persist_runtime(main_path, config):
    """Update only runtime-owned state; never overwrite the scientific main file."""
    from .config_bundle import _LOCK, project
    main_path = Path(main_path)
    path = main_path.parent / filename(config_name(main_path, config), 'acquisition_gui_state', 'json')
    with _LOCK:
        if path.exists():
            envelope = read_json(path)
            if envelope.get('format') != 'acquisition_gui_state' or envelope.get('main_config') != main_path.name:
                raise ValueError('The GUI-state filename is owned by a different file or experiment.')
        else:
            _, envelope = project(config, main_path)
        state = envelope.setdefault('state', {}).setdefault('acquisition', {})
        for key in ('recording_history', 'device_history'):
            if key in config.get('acquisition', {}):
                if key == 'device_history':
                    state.setdefault(key, {}).update(copy.deepcopy(config['acquisition'][key]))
                else:
                    state[key] = copy.deepcopy(config['acquisition'][key])
        atomic_write_json(path, envelope)
    return path


def device_state(directory, experiment, key, details):
    """Standalone controller fallback; application controllers use the repository callback."""
    from .schema import default_config
    config = default_config(experiment)
    config['acquisition']['device_history'] = {key: copy.deepcopy(details)}
    return persist_runtime(Path(directory) / filename(experiment, 'config', 'json'), config)
