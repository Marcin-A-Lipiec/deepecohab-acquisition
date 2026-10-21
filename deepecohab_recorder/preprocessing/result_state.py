"""Internal output ownership, kept outside the scientific diagnostic report."""
from pathlib import Path
import os
import re
from deepecohab_recorder.utils import read_json


def private_path(root, name):
    # Only config-free preprocessing needs a separate application state file.
    return Path(root) / f'.{name}.preprocessing_state'


def result_integrity(config):
    return config.get('acquisition', {}).get('preprocessing', {}).get('_output_integrity', {})


def prior_outputs(diagnostic, output, name, config):
    result = {}
    if not diagnostic.exists():
        return result
    log = read_json(diagnostic)
    identity = log.get('experiment_output_name') == name or log.get('config_file') == name + '.config.json'
    if not identity or log.get('status') != 'completed':
        raise ValueError('The existing diagnostic file does not identify a completed result for this experiment.')
    entries = []
    for entry in log.get('outputs', []):
        value = entry.get('file', '')
        if not re.fullmatch(re.escape(name) + r'\.(?:data|auxiliary_data\d+|device_antenna\d+|TTL_data\d+)(?:_part\d+)?\.(?:csv|parquet)', value):
            raise ValueError('The previous output manifest contains an unrecognized filename.')
        relative = entry.get('relative_path', value)
        path = (diagnostic.parent / relative).resolve()
        if path.name != value:
            raise ValueError('The previous output path does not match its filename.')
        if path.parent == output:
            entries.append((path, relative))
    if not entries:
        return result
    if log.get('diagnostics_version', 0) >= 12:
        saved = result_integrity(config)
        fallback = private_path(diagnostic.parent, name)
        if not saved and fallback.exists():
            envelope = read_json(fallback)
            if envelope.get('format') != 'preprocessing_state' or envelope.get('experiment') != name:
                raise ValueError('The internal preprocessing state belongs to another experiment.')
            saved = envelope.get('completed_run', {})
        if saved.get('created_at') != log.get('created_at') or saved.get('diagnostic_file') != diagnostic.name:
            if any(path.exists() for path, _ in entries):
                raise ValueError('The previous result has no matching application state. Keep its linked GUI state with the data, or choose a new output folder before preprocessing.')
            saved = {}
        fingerprints = saved.get('output_files', {})
    else:
        # Upgrade completed older results without a converter or a new file.
        fingerprints = {e.get('relative_path', e.get('file', '')): e.get('sha256') for e in log.get('outputs', [])}
    for path, relative in entries:
        result[path] = fingerprints.get(relative)
    return result


def hide_private_state(path):
    """Dotfiles are hidden on Unix; use the hidden attribute on Windows too."""
    if os.name == 'nt':
        import ctypes
        get = ctypes.windll.kernel32.GetFileAttributesW
        set_flags = ctypes.windll.kernel32.SetFileAttributesW
        get.argtypes, get.restype = [ctypes.c_wchar_p], ctypes.c_uint32
        set_flags.argtypes, set_flags.restype = [ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_int
        flags = get(str(path))
        if flags != 0xFFFFFFFF:
            set_flags(str(path), flags | 2)
