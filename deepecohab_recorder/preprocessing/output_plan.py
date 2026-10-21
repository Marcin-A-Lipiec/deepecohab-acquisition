"""One naming contract for scientific files and GUI filename previews."""
import re
from deepecohab_recorder.experiment_files import config_name, filename


def experiment_output_name(config_path, config):
    return config_name(config_path, config)


def group_numbers(routes, prefix, reserved=()):
    """Preserve assigned numbers; migrate older friendly names deterministically."""
    groups = set(routes.values()) - {'main', 'rfid'}
    if any(not isinstance(g, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', g) for g in groups):
        raise ValueError('Output groups require a valid name.')
    mapped = {g: g for g in groups if re.fullmatch(prefix + r'[1-9]\d*', g)}
    used = set(mapped.values()) | set(reserved)
    for group in sorted(groups - mapped.keys(), key=lambda g: (g.casefold(), g)):
        number = 1
        while f'{prefix}{number}' in used:
            number += 1
        mapped[group] = f'{prefix}{number}'
        used.add(mapped[group])
    return mapped


def normalize_groups(options):
    routes = options.get('routes', {})
    # Existing numbered groups retain their number. The same old group in a
    # TTL override and RFID override must migrate to one shared destination.
    legacy = {g: 'device_antenna' + g.removeprefix('auxiliary_data')
              for g in set(routes.values()) | set(options.get('ttl_routes', {}).values())
              if re.fullmatch(r'auxiliary_data[1-9]\d*', g)}
    converted = {a: legacy.get(g, g) for a, g in routes.items()}
    reserved = set(options.get('device_groups', {}).values()) | set(options.get('ttl_routes', {}).values())
    mapping = group_numbers(converted, 'device_antenna', reserved)
    options['routes'] = {a: mapping.get(g, 'main') for a, g in converted.items()}
    options['ttl_routes'] = {a: legacy.get(g, g) for a, g in options.get('ttl_routes', {}).items()}
    windows = options.get('group_windows_ms', {})
    options['group_windows_ms'] = {mapping.get(legacy.get(g, g), legacy.get(g, g)): value for g, value in windows.items()}
    return options


def data_stem(name, group='main', part=1, parts=1):
    kind = 'data' if group in ('main', 'rfid') else 'TTL_data1' if group == 'ttl' else group
    if parts > 1:
        kind += f'_part{part:02d}'
    return filename(name, kind, 'parquet').removesuffix('.parquet')
