"""Canonical scientific metadata with a portable, subordinate GUI state file.

The main file owns every scientific field. Sidecar state cannot override it.
A small write-ahead journal makes interrupted two-file saves recoverable.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import threading
from datetime import datetime, timedelta, timezone

from .experiment_files import config_name, filename
from .event_metadata import storage_config
from .schema import ANIMAL_FIELDS, CONFIG_VERSION, migrate_config
from .utils import atomic_write_json, read_json

MAIN_LINK = 'acquisition_gui_state_path'
LOG_LINK = 'diagnostic_log_path'
_LOCK = threading.RLock()
TIMELINE_KEYS = ('start_datetime', 'end_datetime', 'recording_timezone', 'start_from', 'end_with', 'phases')
RECORDING_KEYS = ('name', 'project_name', 'recording_location', 'notes')
CAGE_KEYS = ('name', 'cell_id', 'cage_type', 'antennas')
TUNNEL_KEYS = ('name', 'tunnel_no', 'start_cell_id', 'end_cell_id', 'dead_end', 'antenna_count', 'antennas', 'length')
JUNCTION_KEYS = ('id', 'parent_tunnel', 'position')


def relative_link(target, main_path):
    return Path(os.path.relpath(Path(target).resolve(), Path(main_path).resolve().parent)).as_posix()


def resolve_link(main_path, value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or PurePosixPath(value).is_absolute():
        raise ValueError('Config links must be portable relative paths using forward slashes.')
    return Path(main_path).resolve().parent.joinpath(*PurePosixPath(value).parts)


def links(main):
    return main.get('acquisition', {}).get('_provenance', {})


def pick(value, keys):
    return {k: copy.deepcopy(value[k]) for k in keys if k in value}


def _signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _row_key(value):
    if isinstance(value, dict) and 'start' not in value:
        for key in ('antenna', 'tag', 'cell_id', 'id', 'name'):
            if value.get(key):
                return [key, value[key]]
    return None


def _partition(full, public):
    """Preserve extras, including per-row flags, without duplicating public fields."""
    if isinstance(full, dict) and isinstance(public, dict):
        extras = {}
        for key, value in full.items():
            if key not in public:
                extras[key] = copy.deepcopy(value)
            else:
                remaining = _partition(value, public[key])
                if remaining:
                    extras[key] = remaining
        return extras
    if isinstance(full, list) and isinstance(public, list):
        rows = []
        for source, visible in zip(full, public):
            remaining = _partition(source, visible)
            if remaining:
                rows.append({'match': _signature(visible), 'key': _row_key(visible), 'extra': remaining})
        return {'__row_state__': rows} if rows else {}
    return {}


def _restore(public, extras):
    if isinstance(public, dict):
        result = copy.deepcopy(extras) if isinstance(extras, dict) else {}
        for key, value in public.items():
            result[key] = _restore(value, result.get(key))
        return result
    if isinstance(public, list) and isinstance(extras, dict):
        # Edited public rows do not inherit stale GUI completion flags.
        rows = list(extras.get('__row_state__', []))
        result = []
        for row in public:
            entry = next((r for r in rows if r.get('match') == _signature(row) or
                          (_row_key(row) is not None and r.get('key') == _row_key(row))), None)
            result.append(_restore(row, entry['extra']) if entry else copy.deepcopy(row))
            if entry:
                rows.remove(entry)
        return result
    return copy.deepcopy(public)


def project(config, main_path):
    full = storage_config(migrate_config(config))
    r = full['recording']
    from .device_antennas import device_errors
    errors = device_errors(r)
    if errors:
        raise ValueError('Correct device connections before saving:\n' + '\n'.join(errors))
    if r['timeline'].get('end_with') not in ('dark_phase', 'light_phase'):
        raise ValueError('Select a light or dark phase as the experiment end preference.')
    timeline = pick(r['timeline'], TIMELINE_KEYS)
    t = r['timeline']
    # Express phase hours in the selected start's fixed UTC offset. This retains
    # the same physical cycle when a manual start is moved across a DST switch,
    # without requiring downstream consumers to read GUI scheduling state.
    schedule = t.get('phase_schedule', {})
    if t.get('start_datetime') and schedule.get('anchor_datetime'):
        offset = datetime.fromisoformat(t['start_datetime']).utcoffset()
        anchor = datetime.fromisoformat(schedule['anchor_datetime']).astimezone(timezone(offset))
        timeline['phases'] = {'light_phase': anchor.strftime('%H:%M'),
            'dark_phase': (anchor + timedelta(seconds=schedule['light_duration_seconds'])).strftime('%H:%M')}
    datetime_provenance = {
        key + '_source': ('manual_override' if t.get('datetime_mode') == 'manual' else
                         t.get('datetime_provenance', {}).get(key + '_source', 'imported_config')
                         if t.get(key + '_datetime') else 'not_set')
        for key in ('start', 'end')}
    layout = r['layout']
    physical = {key: [pick(item, fields) for item in layout.get(key, [])]
                for key, fields in (('cages', CAGE_KEYS), ('tunnels', TUNNEL_KEYS), ('junctions', JUNCTION_KEYS))}
    device_sources = {d['antenna'] for d in r.get('devices', [])}
    visible_tunnels = {t['name'] for t in layout.get('tunnels', [])
                       if any(a and a != '?' and a not in device_sources for a in t.get('antennas', []))}
    physical['tunnels'] = [t for t in physical['tunnels'] if t['name'] in visible_tunnels]
    physical.update(pick(layout, ('antenna_combinations', 'tunnels_map')))
    events = [dict(pick(e, ('name', 'description', 'devices')), bouts=[pick(b, ('start', 'end', 'position')) for b in e.get('bouts', [])])
              for e in r['events']]
    known_devices = {d['name'] for d in r.get('devices', [])}
    for event in events:
        unknown = set(event.get('devices') or []) - known_devices
        if unknown:
            raise ValueError(f"Event {event['name']!r}: unknown devices: {', '.join(sorted(unknown))}.")
    from .device_antennas import DEVICE_FIELDS
    recording = dict(pick(r, RECORDING_KEYS), timeline=timeline, events=events,
                     devices=[pick(d, DEVICE_FIELDS) for d in r.get('devices', [])],
                     layout=physical, cohort={'n_mice': r['cohort']['n_mice'],
                     'animals': [pick(a, ANIMAL_FIELDS) for a in r['cohort']['animals']]})
    recording['notes'] = recording.pop('notes')
    a = full['acquisition']
    active = copy.deepcopy(a.get('active_inputs', {}))
    active['rfid_boards'] = sorted(set(active.get('rfid_boards', [])) | {int(b['board_id']) for b in a['boards'].values() if b.get('active')} |
                                  {int(s['board_id']) for s in a.get('preprocessing', {}).get('sources', []) if s.get('board_id')})
    from .device_antennas import resolve_routes
    routes, ttl_routes, _, _ = resolve_routes(full, a.setdefault('preprocessing', {}).setdefault('options', {}), strict=False)
    active['device_rfid'] = sorted({d['antenna'] for d in r.get('devices', [])} |
                                  {source for source, route in routes.items() if route not in ('main', 'rfid') and not source.startswith('TTL')})
    active['ttl'] = sorted(set(active.get('ttl', [])) | set(ttl_routes))
    active = {key: active[key] for key in ('rfid_boards', 'device_rfid', 'ttl') if active.get(key)}
    # Derived public inputs must not reappear as stale optional extras in state.
    for key in ('rfid_boards', 'auxiliary_rfid', 'device_rfid', 'ttl'):
        a.setdefault('active_inputs', {}).pop(key, None)
    a['active_inputs'].update(active)
    provenance = a.get('_provenance', {})
    # Keep notes in recording, but make it the final field in the entire JSON.
    main = {'version': CONFIG_VERSION, 'acquisition': {
        'active_inputs': active, 'datetime_provenance': datetime_provenance,
        '_provenance': {MAIN_LINK: filename(config_name(main_path, full), 'acquisition_gui_state', 'json'),
                        LOG_LINK: provenance.get(LOG_LINK)}}, 'recording': recording}
    # Match retained rows before partitioning their editor extras. Unlisted
    # physical tunnels live only in GUI state, including their original order.
    unlisted = [dict(index=i, tunnel=copy.deepcopy(t)) for i, t in enumerate(layout['tunnels'])
                if t['name'] not in visible_tunnels]
    layout['tunnels'] = [t for t in layout['tunnels'] if t['name'] in visible_tunnels]
    state = _partition(full, main)
    if unlisted:
        state.setdefault('recording', {}).setdefault('layout', {})['_unlisted_tunnels'] = unlisted
    # Selected provenance is public; underlying automatic evidence stays in state.
    state.setdefault('recording', {}).setdefault('timeline', {})['schedule_phases'] = copy.deepcopy(timeline['phases'])
    state.setdefault('recording', {}).setdefault('timeline', {})['operational_phases'] = copy.deepcopy(t['phases'])
    state.setdefault('recording', {}).setdefault('timeline', {})['automatic_datetime_provenance'] = copy.deepcopy(t.get('datetime_provenance', {}))
    state['recording']['timeline'].pop('datetime_provenance', None)
    # Prevent accidental opening of the state file as an experiment config.
    secondary = {'format': 'acquisition_gui_state', 'version': 1,
                 'main_config': Path(main_path).name, 'state': state}
    validate_main(main)
    return main, secondary


def validate_main(main):
    if set(main) != {'version', 'recording', 'acquisition'} or main['version'] not in (5, 6, 7, CONFIG_VERSION):
        raise ValueError('Unsupported canonical experiment config structure/version.')
    r = main['recording']
    extra = {'devices'} if main['version'] >= 8 else {'device_antennas'} if main['version'] >= 6 else set()
    if set(r) != {*RECORDING_KEYS, 'timeline', 'events', 'layout', 'cohort', *extra}:
        raise ValueError('Unexpected or missing canonical recording fields.')
    timeline_keys = set(TIMELINE_KEYS) - ({'end_with'} if main['version'] < 6 else set())
    if main['version'] < 7:
        timeline_keys.add('datetime_provenance')
    if set(r['timeline']) != timeline_keys:
        raise ValueError('Unexpected or missing canonical timeline fields.')
    acquisition_keys = {'active_inputs', '_provenance'}
    if main['version'] >= 7:
        acquisition_keys.add('datetime_provenance')
    if set(main['acquisition']) != acquisition_keys:
        raise ValueError('GUI acquisition settings belong in the linked secondary file.')
    if set(links(main)) != {MAIN_LINK, LOG_LINK}:
        raise ValueError('The canonical config requires its GUI-state and diagnostic-log links.')
    if main['version'] >= 8:
        from .device_antennas import DEVICE_FIELDS, device_name
        from .event_metadata import normalize_event_devices
        for device in r['devices']:
            # Patch 19 omitted description; accept those files without rewriting
            # them on load. The current writer always includes a nullable value.
            if set(device) not in (set(DEVICE_FIELDS), set(DEVICE_FIELDS) - {'description'}) or not device.get('device_type') or device['name'] != device_name(device['device_type'], device['position']):
                raise ValueError('Canonical devices require type-position names and the version 8 device fields.')
            if device.get('description') is not None and not isinstance(device['description'], str):
                raise ValueError('Device description must be text or null.')
        for event in r['events']:
            normalize_event_devices(event.get('devices'))
            for bout in event.get('bouts', []):
                value = bout.get('position')
                # Accept Patch 19 empty lists on input; normalization writes null.
                if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value)):
                    raise ValueError('Canonical bout positions must be lists of compartment names or null.')


def _journal(path):
    return Path(path).parent / ('.' + Path(path).name + '.pending')


def recover(path):
    from .preprocessing.publication import recover_for_config
    recover_for_config(path)
    journal = _journal(path)
    if not journal.exists():
        journal = Path(path).parent / 'acquisition' / ('.' + Path(path).name + '.pending')
        if not journal.exists():
            return
    transaction = read_json(journal)
    # Roll back to the last complete pair, never guess which half was intended.
    for name in ('secondary', 'main', 'previous_main', 'previous_state'):
        if name not in transaction:
            continue
        entry = transaction[name]
        target = resolve_link(path, entry['path'])
        if entry['old'] is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write_json(target, entry['old'])
    journal.unlink()


def bundle_digest(path):
    path = Path(path)
    with _LOCK:
        recover(path)
        raw = path.read_bytes()
        main = json.loads(raw.decode('utf-8-sig'))
        link = links(main).get(MAIN_LINK)
        if not link:
            return hashlib.sha256(raw).hexdigest()
        secondary = resolve_link(path, link)
        return hashlib.sha256(raw + b'\0' + link.encode() + b'\0' +
                              (secondary.read_bytes() if secondary.exists() else b'MISSING')).hexdigest()


def backup_bundle(path):
    """Legacy API: durable saves now use transient rollback, without snapshots."""
    return None


def load_config_bundle(path, notices=None):
    path = Path(path)
    with _LOCK:
        recover(path)
        main = read_json(path)
        if main.get('format') == 'acquisition_gui_state':
            raise ValueError('Load the experiment-named main JSON file, not acquisition_gui_state.json.')
        link = links(main).get(MAIN_LINK)
        if not link:
            if main.get('version') in (5, 6, 7, CONFIG_VERSION) and 'boards' not in main.get('acquisition', {}):
                raise ValueError('Canonical experiment config is missing its acquisition GUI state link.')
            from .recording_history import import_history
            return import_history(migrate_config(main), path.parent)
        validate_main(main)
        if '..' in PurePosixPath(link).parts:
            raise ValueError('The secondary config must be inside the experiment directory.')
        secondary = resolve_link(path, link)
        if secondary == path.resolve():
            raise ValueError('The secondary config cannot point to the main config itself.')
        missing = not secondary.exists()
        if missing:
            state = {}
            if notices is not None:
                notices.append('Linked acquisition GUI state is missing. Scientific metadata loaded; configure hardware and preprocessing settings before recording.')
        else:
            document = read_json(secondary)
            if document.get('format') != 'acquisition_gui_state' or document.get('version') != 1:
                raise ValueError('Unsupported secondary acquisition config format.')
            if document.get('main_config') != path.name:
                raise ValueError('The linked GUI state belongs to a different main config. Keep each experiment in its own folder; rename experiments through Setup.')
            state = document['state']
        combined = _restore(main, state)
        layout = combined['recording']['layout']
        public_names = {t['name'] for t in layout.get('tunnels', [])}
        for entry in sorted(layout.pop('_unlisted_tunnels', []), key=lambda e: e['index']):
            if entry['tunnel']['name'] not in public_names:
                layout.setdefault('tunnels', []).insert(entry['index'], entry['tunnel'])
        timeline = combined['recording']['timeline']
        selected = main['recording']['timeline']
        selected_provenance = main['acquisition'].get('datetime_provenance', selected.get('datetime_provenance', {}))
        combined['acquisition'].pop('datetime_provenance', None)
        evidence = timeline.pop('automatic_datetime_provenance', None)
        if evidence is not None:
            timeline['datetime_provenance'] = evidence
        else:
            timeline['datetime_provenance'] = copy.deepcopy(selected_provenance)
        # Main values always win, including edits made outside the GUI.
        mode = timeline.get('datetime_mode', 'automatic')
        prefix = 'manual_' if mode == 'manual' else 'automatic_'
        for key in ('start_datetime', 'end_datetime'):
            source_key = key.removesuffix('_datetime') + '_source'
            if timeline.get(prefix + key) != selected[key]:
                timeline[prefix + key] = selected[key]
                timeline['datetime_provenance'][source_key] = ('external_config_edit' if not missing else selected_provenance.get(source_key, 'imported_config'))
            elif mode != 'manual' and source_key in selected_provenance:
                timeline['datetime_provenance'][source_key] = selected_provenance[source_key]
        schedule = timeline.get('phase_schedule', {})
        saved_phases = state.get('recording', {}).get('timeline', {}).get('schedule_phases')
        if saved_phases is not None and saved_phases != selected['phases']:
            timeline.pop('phase_schedule', None)
        elif timeline.get('operational_phases'):
            timeline['phases'] = timeline['operational_phases']
        timeline.pop('operational_phases', None)
        timeline.pop('schedule_phases', None)
        result = migrate_config(combined)
        if missing:
            for board in result['acquisition']['boards'].values():
                board['active'] = False
            from .live_boards import DEVICE_KEYS
            result['acquisition']['launch'].update({key: False for key in DEVICE_KEYS})
        from .recording_history import import_history
        return import_history(result, path.parent)


def save_config_bundle(path, config, expected_digest=None, rename_from=None):
    path = Path(path).resolve()
    with _LOCK:
        recover(path)
        if expected_digest is not None and bundle_digest(path) != expected_digest:
            raise ValueError('Experiment configuration or linked GUI state changed. Reload before saving; no metadata was overwritten.')
        main, secondary = project(config, path)
        target = resolve_link(path, links(main)[MAIN_LINK]).resolve()
        if '..' in PurePosixPath(links(main)[MAIN_LINK]).parts:
            raise ValueError('The secondary config must be inside the experiment directory.')
        if target == path:
            raise ValueError('Main and secondary config paths must differ.')
        if target.exists():
            existing = read_json(target)
            if existing.get('format') != 'acquisition_gui_state' or (existing.get('main_config') != path.name and (rename_from is None or
                    Path(rename_from).resolve().parent != path.parent or
                    existing.get('main_config') != Path(rename_from).name)):
                raise ValueError('This directory already has GUI state for another experiment. Choose that experiment or a separate experiment directory.')
        secondary['state'].setdefault('recording', {}).setdefault('timeline', {})['schedule_phases'] = copy.deepcopy(main['recording']['timeline']['phases'])
        journal = _journal(path)
        transaction = {name: {'path': relative_link(file, path), 'old': read_json(file) if file.exists() else None}
                       for name, file in (('main', path), ('secondary', target))}
        if rename_from and Path(rename_from).resolve() != path and Path(rename_from).exists():
            transaction['previous_main'] = {'path': relative_link(rename_from, path), 'old': read_json(Path(rename_from))}
        previous = Path(rename_from) if rename_from else path
        if previous.exists():
            old_link = links(read_json(previous)).get(MAIN_LINK)
            if old_link:
                old_state = resolve_link(previous, old_link)
                if old_state != target and old_state.exists() and read_json(old_state).get('main_config') == previous.name:
                    transaction['previous_state'] = {'path': relative_link(old_state, path), 'old': read_json(old_state)}
        atomic_write_json(journal, transaction)
        try:
            atomic_write_json(target, secondary)
            atomic_write_json(path, main)
            if 'previous_main' in transaction:
                Path(rename_from).unlink()
            if 'previous_state' in transaction:
                resolve_link(path, transaction['previous_state']['path']).unlink()
        except Exception:
            recover(path)
            raise
        journal.unlink()
        return main
