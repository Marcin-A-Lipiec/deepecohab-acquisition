"""Intervals between observed file ranges, independently for each physical board."""

from collections import defaultdict
from .models import iso, parse_pc


def same_rotation(left, right):
    return (bool(left.get("rotation_group"))
            and left.get("rotation_group") == right.get("rotation_group")
            and left["board_id"] == right["board_id"]
            and left["recording_part"] == right["recording_part"]
            and left.get("clock_group") == right.get("clock_group"))


def rotation_boundaries(files, zone):
    groups = defaultdict(list)
    for source in files:
        if source.get("rotation_group"):
            groups[(source["board_id"], source["rotation_group"], source["recording_part"], source.get("clock_group"))].append(source)
    result = []
    for sources in groups.values():
        sources.sort(key=lambda f: (f["first_pc_us"], f["id"]))
        for previous, following in zip(sources, sources[1:]):
            result.append({
                "board_id": following["board_id"], "com_port": following["port"],
                "source_group": following["rotation_group"], "recording_part": following["recording_part"],
                "previous_file": previous["path"], "next_file": following["path"],
                "previous_last_record": iso(previous["last_pc_us"], zone),
                "next_first_record": iso(following["first_pc_us"], zone),
                "record_gap_seconds": (following["first_pc_us"] - previous["last_pc_us"]) / 1_000_000,
                "basis": "declared_continuous_source_file_rotation; negative gap means overlapping file ranges",
            })
    return result


def observed_file_gaps(files):
    """Positive gaps in the union of observed coverage, independently per board."""
    by_board = defaultdict(list)
    for source in files:
        by_board[source["board_id"]].append(source)
    for _, sources in sorted(by_board.items()):
        sources.sort(key=lambda f: (f["first_pc_us"], f["last_pc_us"], f["id"]))
        anchor = None
        for source in sources:
            if anchor is not None and source["first_pc_us"] > anchor["last_pc_us"]:
                yield anchor, source
            # A nested file must not invent a gap inside another file's range.
            if anchor is None or source["last_pc_us"] > anchor["last_pc_us"]:
                anchor = source


def recording_boundaries(files, zone, minimum_seconds=0):
    result = []
    threshold_us = round(minimum_seconds * 1_000_000)
    for previous, following in observed_file_gaps(files):
        gap = following["first_pc_us"] - previous["last_pc_us"]
        if gap < threshold_us:
            continue
        result.append({
            "board_id": following["board_id"],
            "previous_file": previous["path"], "next_file": following["path"],
            "previous_part": previous["recording_part"], "next_part": following["recording_part"],
            "previous_last_record": iso(previous["last_pc_us"], zone),
            "next_first_record": iso(following["first_pc_us"], zone),
            "record_gap_seconds": gap / 1_000_000,
            "previous_reported_stop": previous.get("recording_end"),
            "previous_stop_status": previous.get("stop_status", "unknown"),
            "next_reported_start": following.get("recording_start"),
            "declared_continuous_rotation": same_rotation(previous, following),
            "silence_is_not_proof_of_failure": True,
        })
    return result


def missed_data_periods(files, zone, minimum_seconds=0):
    periods = []
    threshold_us = round(minimum_seconds * 1_000_000)
    for previous, following in observed_file_gaps(files):
        gap = following["first_pc_us"] - previous["last_pc_us"]
        if gap < threshold_us or same_rotation(previous, following):
            continue
        periods.append({
            "board_id": following["board_id"], "com_port": following["port"],
            "start": iso(previous["last_pc_us"], zone), "end": iso(following["first_pc_us"], zone),
            "duration_seconds": gap / 1_000_000,
            "previous_file": previous["path"], "next_file": following["path"],
            "previous_part": previous["recording_part"], "next_part": following["recording_part"],
            "basis": "last_record_to_first_record_between_files",
            "classification": "observed_record_gap", "confirmed_interruption": False,
            "note": "No records observed in this interval; inactivity or missing recording are both possible.",
        })
    return periods


def acquisition_journals(root, zone, config=None):
    """Return every RFID epoch, including runs with no selected detections."""
    import json
    from pathlib import Path
    evidence, warnings = [], []
    from deepecohab_recorder.recording_history import recording_runs
    for run in recording_runs(root, config):
        path = run.get('run_id', 'recording_history')
        try:
            for device_name, device in run.get('devices', {}).items():
                for epoch_index, epoch in enumerate([*device.get('previous_epochs', []), device]):
                    board = epoch.get('config', {}).get('board_id')
                    if not board:
                        continue
                    start, end = epoch.get('start_datetime'), epoch.get('end_datetime')
                    if start: parse_pc(start, zone)
                    if end: parse_pc(end, zone)
                    if start and end and parse_pc(end, zone) < parse_pc(start, zone):
                        raise ValueError('Journal stop precedes its start.')
                    evidence.append({'board_id': int(board), 'device': device_name, 'epoch': epoch_index, 'recording_start': start,
                        'recording_end': end, 'stop_status': epoch.get('stop_status', 'unknown'),
                        'data_file': epoch.get('data_file', ''), 'journal': str(path),
                        'basis': 'acquisition_journal', 'recording_counters': epoch.get('counters', {}),
                        'error': epoch.get('error', ''), 'malformed_examples': epoch.get('malformed_examples', []),
                        'reported_stop_is_failure_time': False})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            warnings.append({'journal': str(path), 'error': str(exc)})
    return evidence, warnings


def attach_journal_evidence(inputs, evidence):
    from pathlib import Path
    by_name = defaultdict(list)
    for entry in evidence:
        by_name[(entry['board_id'], Path(entry['data_file'].replace('\\', '/')).name)].append(entry)
    for spec in inputs:
        candidates = by_name[(spec.board_id, Path(spec.path).name)]
        if len(candidates) == 1 and not spec.start and not spec.end:
            entry = candidates[0]
            spec.start, spec.end = entry['recording_start'], entry['recording_end']
            spec.stop_status, spec.timing_basis = entry['stop_status'], 'acquisition_journal'


def recording_coverage(files, zone, journal_evidence=(), minimum_gap_seconds=0):
    """Separate evidence of records, declared continuity, and confirmed DAQ gaps.

    Coverage here means evidence in the selected raw sources, before tag filters.
    It cannot establish whether every physical detection was captured.
    """
    groups = defaultdict(list)
    for source in files:
        groups[source['board_id']].append(source)
    for entry in journal_evidence:
        groups.setdefault(entry['board_id'], [])
    result = []
    for board_id, sources in sorted(groups.items()):
        sources = sorted(sources, key=lambda s: (s['first_pc_us'], s['id']))
        intervals, confirmed_gaps, declared_rotations = [], [], []
        for s in sources:
            journal = s.get('timing_basis') == 'acquisition_journal'
            start = s.get('recording_start')
            stop = s.get('recording_end')
            clean = journal and s.get('stop_status') == 'clean' and bool(stop)
            intervals.append({
                'file': s['path'], 'recording_part': s['recording_part'],
                'first_record': iso(s['first_pc_us'], zone),
                'last_record': iso(s['last_pc_us'], zone),
                'record_basis': 'valid_raw_PC_timestamps_RFID_and_TTL_before_tag_filter',
                'recording_start': start, 'reported_stop': stop,
                'start_basis': 'acquisition_journal_start' if journal and start else 'explicit_input_bound' if start else 'unknown',
                'end_basis': 'confirmed_clean_DAQ_stop' if clean else 'abrupt_or_incomplete_stop' if journal else 'explicit_input_bound' if stop else 'unknown',
                'confirmed_stop': stop if clean else None,
                'stop_status': s.get('stop_status', 'unknown'),
                'last_record_is_failure_time': False,
            })
        # Only clean, explicitly journaled stop/start pairs establish an inactive
        # DAQ interval. Another overlapping journal or recorded row vetoes it.
        journaled = [dict(j, path=j['data_file']) for j in journal_evidence if j['board_id'] == board_id and j.get('recording_start')]
        if not journaled:
            journaled = [s for s in sources if s.get('timing_basis') == 'acquisition_journal' and s.get('recording_start')]
        journaled.sort(key=lambda s: parse_pc(s['recording_start'], zone))
        for previous, following in zip(journaled, journaled[1:]):
            if previous.get('stop_status') != 'clean' or not previous.get('recording_end'):
                continue
            low, high = parse_pc(previous['recording_end'], zone), parse_pc(following['recording_start'], zone)
            if high <= low:
                continue
            conflicts = any(s['first_pc_us'] < high and s['last_pc_us'] > low for s in sources)
            conflicts |= any(j is not previous and j is not following and
                parse_pc(j['recording_start'], zone) < high and
                (not j.get('recording_end') or parse_pc(j['recording_end'], zone) > low) for j in journaled)
            if conflicts:
                continue
            confirmed_gaps.append({'start': iso(low, zone), 'end': iso(high, zone),
                'duration_seconds': (high-low)/1_000_000, 'classification': 'confirmed_DAQ_inactive',
                'basis': 'clean_stop_and_subsequent_start_in_acquisition_journals',
                'previous_file': previous['path'], 'next_file': following['path']})
        result.append({'board_id': board_id,
            'first_observed_record': iso(min((s['first_pc_us'] for s in sources), default=None), zone),
            'last_observed_record': iso(max((s['last_pc_us'] for s in sources), default=None), zone),
            'source_intervals': intervals, 'acquisition_journals': [j for j in journal_evidence if j['board_id']==board_id],
            'selected_files_with_records': len(sources), 'confirmed_interruptions': confirmed_gaps,
            'observed_record_gaps': missed_data_periods(sources, zone, minimum_gap_seconds),
            'declared_rotation_boundaries': rotation_boundaries(sources, zone),
            'scope': 'observed_coverage_from_selected_sources; acquisition_events_from_all_available_experiment_journals',
            'interpretation': 'Observed spans and declared folder continuity do not prove uninterrupted acquisition. Unknown leading/trailing bounds remain unknown; record silence is not a failure time.'})
    return result
