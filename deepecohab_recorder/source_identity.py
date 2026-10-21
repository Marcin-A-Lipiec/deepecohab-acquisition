"""Logical board identities. Serial ports describe connections, never antennas."""
import re


class IdentityConflict(ValueError):
    """A source assignment contradicts recorded identity; abort instead of dropping reads."""


def split_source(value):
    value = str(value).strip().upper()
    match = re.fullmatch(r'(.+?)_(\d+)_(COM\d+)', value)
    if match:
        return match[1], int(match[2]), match[3]
    match = re.fullmatch(r'(.+?)-(\d+)', value)
    if match:
        return match[1], int(match[2]), ''
    match = re.fullmatch(r'(.+?)(COM\d+)', value)
    if match:
        return match[1], None, match[2]
    return value, None, ''


def source_id(channel, board_id):
    if isinstance(board_id, bool) or int(board_id) < 1:
        raise ValueError('Choose a positive RFID DAQ board number.')
    channel = str(channel).strip().upper()
    return f'{channel}-{int(board_id)}'


def canonical_source(value, board_id=None, boards=None):
    channel, recorded_board, port = split_source(value)
    if recorded_board is not None:
        if board_id is not None and int(board_id) != recorded_board:
            raise IdentityConflict(f'{value}: recorded board conflicts with board {board_id}.')
        return source_id(channel, recorded_board)
    if board_id is None and port:
        candidates = {int(b['board_id']) for b in (boards or {}).values()
                      if str(b.get('com_port', '')).upper() == port and b.get('board_id')}
        if len(candidates) == 1:
            board_id = candidates.pop()
    # Bare and ambiguous legacy labels require the explicit layout repair prompt.
    return source_id(channel, board_id) if board_id is not None else str(value).strip().upper()


def assigned_source(value, board_id, port=''):
    channel, embedded_board, embedded_port = split_source(value)
    if embedded_board is not None and embedded_board != int(board_id):
        raise IdentityConflict(f'{value}: embedded board {embedded_board} conflicts with assigned board {board_id}.')
    if embedded_port and port and embedded_port != port.upper():
        raise IdentityConflict(f'{value}: embedded port conflicts with recorded port {port}.')
    return source_id(channel, board_id)


def canonical_alias(value, boards):
    """Retain user-chosen alias case while migrating only its board suffix."""
    resolved = canonical_source(value, boards=boards)
    _, board, port = split_source(resolved)
    if board is None or port:
        return str(value).strip()
    channel = re.sub(r'(?:_\d+_)?COM\d+$|-\d+$', '', str(value).strip(), flags=re.I)
    return f'{channel}-{board}'
