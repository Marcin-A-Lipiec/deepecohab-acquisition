"""Resolve bare layout labels only when every candidate board agrees."""
import copy
import re
from collections import defaultdict
from deepecohab_recorder.topology import normalize_layout, records
from deepecohab_recorder.source_identity import split_source, source_id

CHANNELS = {'legacy_raw': set('12345678'), 'old_decimal': set('12345678'),
            'old_hex': set('12345678'), 'v1.0': set('0123456789ABCDEF'),
            'v1.5': set('ABCDEFGHIJKLMNOP')}


def antenna_labels(layout):
    return {str(a).strip().upper() for key in ('cages', 'tunnels')
            for entry in records(layout.get(key), key)
            for a in (entry.get('antenna_sources') or entry.get('antennas') or [])
            if str(a).strip() not in ('', '?')}


def check_layout(config, inputs):
    by_channel = defaultdict(set)
    profiles, ports = {}, defaultdict(set)
    saved = config.get('acquisition', {}).get('preprocessing', {}).get('sources', [])
    for source in [*saved, *(s.__dict__ for s in inputs)]:
        board, profile = source.get('board_id'), source.get('profile')
        if not board or profile not in CHANNELS:
            continue
        profiles.setdefault(board, set()).update(CHANNELS[profile])
        if source.get('port'):
            ports[source['port'].upper()].add(board)
    for board, channels in profiles.items():
        for channel in channels:
            by_channel[channel].add(board)
    known_boards = {b.get('board_id'): b for b in config.get('acquisition', {}).get('boards', {}).values()
                    if b.get('active') and b.get('com_port')}
    changes, ambiguous, mismatched = {}, {}, []
    labels = antenna_labels(config['recording']['layout']) | {d['antenna'] for d in config['recording'].get('devices', [])}
    for label in sorted(labels):
        channel, board, port = split_source(label)
        if re.fullmatch(r'0?[1-8]', channel):
            channel = str(int(channel))
        if board is not None:
            allowed = profiles.get(board)
            if allowed is None and board in known_boards:
                from deepecohab_recorder.protocols.rfid import protocol_for_hardware
                allowed = set(protocol_for_hardware(known_boards[board]['hardware']).rfid_channels)
            if allowed is None and board in config.get('acquisition', {}).get('active_inputs', {}).get('rfid_boards', []):
                allowed = set('0123456789ABCDEFGHIJKLMNOP')
            if allowed is not None and channel in allowed:
                if label != source_id(channel, board):
                    changes[label] = source_id(channel, board)
            else:
                mismatched.append(label)
            continue
        candidates = sorted(by_channel[channel] & ports[port] if port else by_channel[channel])
        if len(candidates) == 1:
            changes[label] = source_id(channel, candidates[0])
        else:
            ambiguous[label] = [str(b) for b in candidates]
    # Prevent a rename from merging two distinct physical layout antennas.
    proposed = copy.deepcopy(config['recording']['layout'])
    assigned = []
    for entry in records(proposed.get('tunnels'), 'tunnel'):
        assigned.extend(changes.get(str(a).strip().upper(), str(a).strip().upper())
                        for a in (entry.get('antenna_sources') or entry.get('antennas') or []) if a and a != '?')
    assigned.extend(changes.get(d['antenna'], d['antenna']) for d in config['recording'].get('devices', []))
    collisions = sorted({a for a in assigned if assigned.count(a) > 1})
    return dict(changes=changes, ambiguous=ambiguous, mismatched=mismatched, collisions=collisions)


def apply_layout_changes(config, changes):
    result = copy.deepcopy(config)
    layout = result['recording']['layout']
    for key in ('cages', 'tunnels'):
        entries = records(layout.get(key), key)
        for entry in entries:
            for field in ('antennas', 'antenna_sources'):
                if field in entry:
                    entry[field] = [changes.get(str(a).strip().upper(), a) for a in entry[field]]
        layout[key] = entries
    # Derive cage lists and direct antenna/tunnel combinations from the physical
    # topology. No stale legacy combination keys survive a rename.
    result['recording']['layout'] = normalize_layout(layout)
    for device in result['recording'].get('devices', []):
        device['antenna'] = changes.get(device['antenna'], device['antenna'])
    return result
