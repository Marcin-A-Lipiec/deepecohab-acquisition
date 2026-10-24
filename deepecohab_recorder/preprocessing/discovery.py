"""Read-only discovery in a dedicated process, with bounded per-file previews."""
import hashlib
import json
from pathlib import Path
import sys

from deepecohab_recorder.experiment_files import discover_config
from .sources import candidate_files, experiment_sources, inspect_source, source_key


def discover(request, cache):
    root = Path(request['root'])
    raw = root / 'raw_antenna_reads'
    config_path = request.get('config_path', '')
    error = ''
    if not request.get('manual_config'):
        try:
            config_path = str(discover_config(root, request['name']) or '')
        except (OSError, ValueError) as exc:
            config_path, error = '', str(exc)
    config = request['config']
    # Use the GUI's frozen snapshot. A bundle load can recover a pending save;
    # a discovery worker must never enter that writer/recovery path.
    zone = request.get('timezone', config['recording']['timeline']['recording_timezone'])
    automatic = [str(p.resolve()) for p in experiment_sources(raw)]
    saved_sources = request.get('saved_sources', {})
    selected = automatic + request.get('manual_paths', []) + [str((root / key).resolve()) for key in saved_sources]
    excluded = set(request.get('removed', []))
    paths = list(dict.fromkeys(p for p in selected if p not in excluded))
    if request.get('force'):
        cache.clear()
    entries, watched = [], {str(root), str(raw)}
    assignments = request.get('assignments', {})
    for path in paths:
        notices = []
        try:
            files = candidate_files(path)
            for f in files:
                watched.add(str(f.parent))
            saved = dict(saved_sources.get(source_key(path, root), {}))
            saved.update(assignments.get(path, {}))
            if path not in request.get('profile_overrides', []):
                saved.pop('profile', None)
            stats = [(str(f), f.stat().st_size, f.stat().st_mtime_ns) for f in files]
            fingerprint = hashlib.sha256(json.dumps([stats, saved, zone, request.get('active', []),
                config.get('acquisition', {}).get('recording_history', {}),
                config.get('acquisition', {}).get('boards', {})], sort_keys=True).encode()).hexdigest()
            if not request.get('force') and request.get('fingerprints', {}).get(path) == fingerprint:
                entries.append(dict(path=path, source=None, notices=[], fingerprint=fingerprint,
                                    invalid=False, unchanged=True))
                continue
            source, notices = inspect_source(path, config, zone, saved=saved,
                active=request.get('active', []), allow_unresolved_clock=True, paths=files, cache=cache,
                bounded_preview=not request.get('force', False))
            if not Path(path).is_relative_to(root.resolve()):
                notices.append(dict(path=path, format='external', message=
                    'Source is outside this experiment directory: ' + path + '. Verify that it belongs to this experiment before preprocessing.'))
            if source is None and not notices:
                notices.append(dict(path=path, format='unavailable', message='No completed supported raw files found. Check the source path and rescan.'))
            # A partial result under an explicit incompatible profile is not safe
            # to treat as a complete selected source.
            invalid = any(n['format'] in ('conflicting_source', 'active', 'unavailable') or
                          (path in request.get('profile_overrides', []) and 'No valid rows' in n['message']) for n in notices)
            entries.append(dict(path=path, source=source.to_dict() if source else None,
                                notices=notices, fingerprint=fingerprint, invalid=invalid))
        except (OSError, ValueError, TypeError) as exc:
            entries.append(dict(path=path, source=None, fingerprint='', invalid=True,
                notices=[dict(path=path, format='error', message=str(exc))]))
    return dict(generation=request['generation'], revision=request['revision'],
        entries=entries, automatic=automatic, config_path=config_path, config_error=error,
        timezone=zone, watched=sorted(p for p in watched if Path(p).is_dir())[:256],
        force=request.get('force', False))


def main():
    cache = {}
    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = discover(request, cache)
            print(json.dumps(dict(kind='discovery', result=result)), flush=True)
        except Exception as exc:
            print(json.dumps(dict(kind='error', text=str(exc))), flush=True)


if __name__ == '__main__':
    main()
