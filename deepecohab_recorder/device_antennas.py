"""Physical device roles and layout-derived output defaults, independent of Qt."""
import copy
import re
from .source_identity import canonical_source

DEVICE_FIELDS = ('name', 'description', 'device_type', 'antenna', 'position', 'TTL_port')


def device_name(device_type, position):
    return f'{device_type.strip()}-{position.strip()}' if device_type.strip() and position.strip() else ''


def normalize_devices(rows, boards=None):
    if not isinstance(rows, list):
        raise ValueError('Devices must be a list.')
    result = []
    for source in rows:
        row = copy.deepcopy(source)
        if not isinstance(row, dict):
            raise ValueError('Each device must be an object.')
        old_name = str(row.get('name') or '').strip()
        # The old name identified the device kind; description is independent
        # optional text and never contributes to the generated identity.
        row['device_type'] = str(row.get('device_type', old_name) or '').strip()
        row['position'] = str(row.get('position') or '').strip()
        row['name'] = device_name(row['device_type'], row['position'])
        description = row.get('description', row.get('_legacy_description'))
        if description is not None and not isinstance(description, str):
            raise ValueError('Device description must be text or null.')
        row['description'] = (description.strip() or None) if description is not None else None
        row.pop('_legacy_description', None)
        if old_name and old_name != row['name']:
            row['_previous_names'] = list(dict.fromkeys([*row.get('_previous_names', []), old_name]))
        row['antenna'] = canonical_source(row.get('antenna', ''), boards=boards)
        ttl = row.get('TTL_port')
        row['TTL_port'] = canonical_source(ttl, boards=boards) if ttl else None
        result.append(row)
    return result


def remap_device_names(values, devices):
    """Keep event references attached when generated device names change."""
    from .event_metadata import normalize_event_devices
    values = normalize_event_devices(values)
    current = {d['name'] for d in devices}
    aliases = {}
    for d in devices:
        for old in d.get('_previous_names', []):
            aliases.setdefault(old, []).append(d['name'])
    return list(dict.fromkeys(new for value in (values or [])
                             for new in ([value] if value in current else aliases.get(value, [value])))) or None


def migrate_device_references(config):
    from .event_metadata import flat_events
    recording = config['recording']
    devices = recording['devices']
    events = flat_events(recording['events'])
    for event in events.get('event_types', []):
        event['devices'] = remap_device_names(event.get('devices'), devices)
    recording['events'] = events
    registry = config['acquisition'].get('preprocessing', {}).get('options', {}).get('device_groups', {})
    current = {d['name'].casefold() for d in devices}
    for d in devices:
        key = d['name'].casefold()
        for old in d.get('_previous_names', []):
            old = old.casefold()
            if old not in current and old in registry:
                group = registry.pop(old)
                registry.setdefault(key, group)


def device_errors(recording, boards=None):
    errors = []
    layout = recording['layout']
    positions = {c['name'] for c in layout.get('cages', [])} | {t['name'] for t in layout.get('tunnels', [])}
    assigned = {a for t in layout.get('tunnels', []) for a in t.get('antennas', []) if a != '?'}
    catalogue = {str(b.get('board_id')): b for b in (boards or {}).values()}
    for row in recording.get('devices', []):
        a = row.get('antenna', '')
        if not row.get('device_type', '').strip():
            errors.append(f'Device antenna {a}: enter a device type.')
        if row.get('position') not in positions:
            errors.append(f'Device antenna {a}: select an existing cage or tunnel.')
        if not re.fullmatch(r'[0-9A-P]-[1-9]\d*', a):
            errors.append(f'Device antenna {a!r}: assign a physical channel and board.')
        if a in assigned:
            errors.append(f'Antenna {a} is assigned more than once or has two roles.')
        assigned.add(a)
        channels = [(a, 'rfid_channels')]
        ttl = row.get('TTL_port')
        if ttl:
            if not re.fullmatch(r'TTL[1-4]-[1-9]\d*', ttl):
                errors.append(f'{a}: select a board-qualified TTL1–TTL4 input.')
            channels.append((ttl, 'ttl_channels'))
        if boards is not None:
            from .protocols.rfid import protocol_for_hardware
            for source, kind in channels:
                channel, _, board_id = source.rpartition('-')
                board = catalogue.get(board_id)
                if not board:
                    errors.append(f'{source}: no configured or archived board with this number.')
                elif channel not in getattr(protocol_for_hardware(board.get('hardware', '')), kind):
                    errors.append(f'{source}: channel is not supported by this board.')
    return errors


def layout_defaults(config, options, *, strict=True):
    """Allocate stable device groups; explicit per-input routes remain separate."""
    from .preprocessing.output_plan import normalize_groups
    normalize_groups(options)
    registry = options.setdefault('device_groups', {})
    used = set(registry.values()) | set(options.get('routes', {}).values()) | set(options.get('ttl_routes', {}).values())
    for key, value in registry.items():
        if not isinstance(key, str) or not re.fullmatch(r'device_antenna[1-9]\d*', str(value)):
            raise ValueError('Invalid saved device group assignment.')
    devices = config.get('recording', {}).get('devices', [])
    names = {}
    for row in devices:
        name = device_name(row.get('device_type', ''), row.get('position', ''))
        if not name:
            continue
        key = name.casefold()
        if key not in registry:
            n = 1
            while f'device_antenna{n}' in used:
                n += 1
            registry[key] = f'device_antenna{n}'
            used.add(registry[key])
        names.setdefault(registry[key], name)
    routes, ttl_candidates = {}, {}
    for row in devices:
        key = device_name(row.get('device_type', ''), row.get('position', '')).casefold()
        if not key:
            continue
        group = registry[key]
        routes[row['antenna']] = group
        if row.get('TTL_port'):
            ttl_candidates.setdefault(row['TTL_port'], set()).add(group)
    ttl, conflicts = {}, []
    for source, candidates in ttl_candidates.items():
        if len(candidates) == 1:
            ttl[source] = next(iter(candidates))
        elif source not in options.get('ttl_routes', {}):
            conflicts.append(source)
    if conflicts and strict:
        raise ValueError('TTL inputs linked to different device groups need an explicit destination in Manage RFID and TTL output grouping: ' + ', '.join(sorted(conflicts)))
    return routes, ttl, names, conflicts


def resolve_routes(config, options, *, strict=True):
    rfid, ttl, names, conflicts = layout_defaults(config, options, strict=strict)
    return (dict(rfid, **options.get('routes', {})),
            dict(ttl, **options.get('ttl_routes', {})), names, conflicts)
