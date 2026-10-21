"""File/folder source discovery and portable assignments; no Qt or hardware I/O."""
from dataclasses import dataclass, field, replace
from pathlib import Path

from .models import InputFile
from .readers import scan, raw_channel

EXTENSIONS = {'.csv', '.txt', '.tsv'}


def source_key(path, experiment_root):
    path = Path(path).resolve()
    try:
        return path.relative_to(Path(experiment_root).resolve()).as_posix()
    except ValueError:
        return str(path)


def candidate_files(path):
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() in EXTENSIONS else []
    if path.is_dir():
        return sorted(p for p in path.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in EXTENSIONS)
    return []


def experiment_sources(root):
    root = Path(root)
    if not root.is_dir():
        return []
    # Each immediate subfolder is one source, including its nested files.
    return sorted(p for p in root.iterdir() if not p.is_symlink() and
                  (p.is_dir() or (p.is_file() and p.suffix.lower() in EXTENSIONS)))


@dataclass
class SourceGroup:
    path: str
    is_folder: bool
    files: list[InputFile]
    first_pc_us: int | None
    channels: set[str] = field(default_factory=set)
    embedded_ports: set[str] = field(default_factory=set)
    embedded_boards: set[int] = field(default_factory=set)

    @property
    def prototype(self):
        return self.files[0]

    @property
    def label(self):
        return (f'{Path(self.path).name}/ ({len(self.files)} files)' if self.is_folder else Path(self.path).name)

    def expand(self, assignment):
        if len(self.embedded_ports) == 1 and self.embedded_ports != {assignment.port}:
            raise ValueError(f'{self.label}: retain the recorded COM port as connection metadata. Antenna names use the board number.')
        if self.embedded_boards and self.embedded_boards != {assignment.board_id}:
            raise ValueError(f'{self.label}: the recorded Board number must remain {next(iter(self.embedded_boards))}.')
        return [replace(spec, port=spec.port or assignment.port, board_id=assignment.board_id,
                        profile=assignment.profile, group=assignment.group,
                        part=assignment.part, tick_ms=assignment.tick_ms,
                        counter_bits=assignment.counter_bits,
                        naive_utc_offset=assignment.naive_utc_offset,
                        rotation_group=self.path if self.is_folder else '',
                        source_path=self.path, source_kind='folder' if self.is_folder else 'file',
                        port_origin='recorded' if self.embedded_ports else 'unrecorded')
                for spec in self.files]


def inspect_source(path, config, zone, *, saved=None, active=(), allow_unresolved_clock=False):
    path = Path(path).resolve()
    paths = candidate_files(path)
    if any(str(p.resolve()) in active for p in paths):
        return None, [{'path': str(path), 'format': 'active', 'message': 'Source still being recorded; scan it after recording stops.'}]
    notices = []
    # Folder identity is explicit. Do not borrow a connected board's COM port.
    local_config = dict(config)
    if path.is_dir():
        local_config['acquisition'] = dict(config.get('acquisition', {}), boards={})
    found = scan([str(p) for p in paths], local_config, zone, preview_limit=24,
                 notices=notices, profile_override=(saved or {}).get('profile'),
                 naive_utc_offset=(saved or {}).get('naive_utc_offset', ''),
                 allow_unresolved_clock=allow_unresolved_clock)
    if not found:
        return None, notices
    profiles = {entry['input'].profile for entry in found}
    channels = {raw_channel(a) for e in found for a in e['antennas'] if not a.startswith('TTL')}
    if profiles == {'v1.0', 'v1.5'} and channels <= set('ABCDEFGHIJKLMNOP'):
        # An A–F-only hourly file is ambiguous by itself; another file from
        # the same board containing G–P identifies the whole source as v1.5.
        found = scan([str(p) for p in paths], local_config, zone, preview_limit=24,
                     profile_override='v1.5', naive_utc_offset=(saved or {}).get('naive_utc_offset', ''),
                     allow_unresolved_clock=allow_unresolved_clock)
        profiles = {'v1.5'}
    ports = {entry['embedded_port'] for entry in found if entry['embedded_port']}
    boards = {entry['embedded_board'] for entry in found if entry['embedded_board'] is not None}
    if len(profiles) != 1 or (len(ports) > 1 and len(boards) != 1) or len(boards) > 1:
        notices.append({'path': str(path), 'format': 'conflicting_source',
            'message': 'This folder mixes hardware formats or recorded board identities. Put one board/format in each source folder.'})
        return None, notices
    specs = [entry['input'] for entry in found]  # scan sorts by first PC datetime.
    group = SourceGroup(str(path), path.is_dir(), specs, found[0]['first_pc_us'],
                        {raw_channel(a) for e in found for a in e['antennas'] if not a.startswith('TTL')}, ports, boards)
    if group.is_folder:
        for spec in specs:
            spec.part = 1
            spec.rotation_group = str(path)
    if saved:
        for spec in specs:
            for key in ('board_id', 'port', 'profile', 'group', 'part', 'tick_ms', 'counter_bits', 'naive_utc_offset'):
                if key in saved:
                    setattr(spec, key, saved[key])
    return group, notices


def serialize_source(source, assignment, experiment_root):
    result = dict(path=source_key(source.path, experiment_root), kind='folder' if source.is_folder else 'file',
                board_id=assignment.board_id, port=assignment.port,
                port_origin='recorded' if source.embedded_ports else 'virtual',
                profile=assignment.profile, group=assignment.group, part=assignment.part,
                tick_ms=assignment.tick_ms, counter_bits=assignment.counter_bits)
    if assignment.naive_utc_offset:
        result['naive_utc_offset'] = assignment.naive_utc_offset
    return result
