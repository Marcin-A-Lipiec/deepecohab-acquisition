"""A short catalogue of the data files actually present after publication."""
from pathlib import Path
import re

README = 'DATATYPE READ ME.txt'


def description(name):
    if name.endswith('.config.json'):
        return 'Main experiment metadata: animals, timeline, events and physical layout.'
    if name.endswith('.acquisition_gui_state.json'):
        return 'Acquisition settings, editor state, grouping assignments and recording history. Loaded automatically through the main config.'
    if name.endswith('.diagnostic.json'):
        return 'Preprocessing checks, transit medians, recording gaps, interpolation details and output-file information.'
    match = re.search(r'\.(data|auxiliary_data\d+|device_antenna\d+|TTL_data\d+)(?:_part\d+)?\.(parquet|csv)$', name)
    if match:
        kind = match[1]
        return ('Movement RFID detections, including stitched observations and any interpolated detections.' if kind == 'data'
                else 'Individual TTL rising-edge events.' if kind.startswith('TTL') else 'Device RFID detections and any TTL inputs assigned to this group. TTL rows have zero duration and no animal identity.')
    if name.endswith('.acquisition_errors.txt'):
        return 'Acquisition failures and relevant device error details.'
    if '_camera_timestamps.' in name:
        return 'Camera frame timestamps and capture status.'
    if '_camera_output_frames.' in name:
        return 'Camera frame-to-video-part recording ledger.'
    if '_camera_part' in name:
        return 'Recorded camera video segment.'
    return None


def readme_text(directory, changes, outputs=()):
    directory = Path(directory).resolve()
    names = {p.name for p in directory.iterdir() if p.is_file() and not p.name.startswith('.')}
    for target, source in changes.items():
        if Path(target).parent == directory:
            if source is None:
                names.discard(Path(target).name)
            else:
                names.add(Path(target).name)
    # Preserve channel descriptions for other experiments sharing this folder.
    previous = {}
    if (directory / README).exists():
        for line in (directory / README).read_text(encoding='utf-8').splitlines():
            if ' — ' in line:
                name, text = line.split(' — ', 1)
                previous[name] = text
    current = {}
    for output in outputs:
        name = output['file']
        text = description(name)
        if text and output.get('device_name'):
            text += ' Device: ' + output['device_name'] + '.'
        if text and output.get('sources'):
            text += ' Inputs: ' + ', '.join(output['sources']) + '.'
        if text and name.endswith('.csv'):
            text += ' CSV time_spent is in milliseconds.'
        if text:
            text += ' inserted is True for interpolated rows and False for rows derived from raw records.'
        current[name] = text
    lines = ['DATATYPE READ ME', 'Files currently present in this folder:', '']
    for name in sorted(names):
        text = current.get(name) or previous.get(name) or description(name)
        if text:
            lines.append(name + ' — ' + text)
    if (directory / 'raw_antenna_reads').is_dir():
        lines.append('raw_antenna_reads/ — Original RFID and TTL recordings used as preprocessing input.')
    return '\n'.join(lines) + '\n'
