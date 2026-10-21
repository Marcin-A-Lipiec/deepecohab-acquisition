"""Recover experiment bounds from complete raw input and acquisition journals."""
import copy
from pathlib import Path
from deepecohab_recorder.experiment_timing import normalize_timing, select_timing, extremum
from deepecohab_recorder.clock import ExperimentClock
from .models import iso
from .readers import detect
from .sources import candidate_files


def journal_bounds(root, timeline, config=None):
    starts, completed, incomplete = [], [], []
    from deepecohab_recorder.recording_history import recording_runs
    for run in recording_runs(root, config):
        try:
            devices = [epoch for device in run.get('devices', {}).values()
                       for epoch in [*device.get('previous_epochs', []), device]]
            run_starts = [d['start_datetime'] for d in devices if d.get('start_datetime')]
            for value in run_starts:
                ExperimentClock(timeline['recording_timezone']).parse(value)
            starts.extend(run_starts)
            if not run_starts:
                continue
            if all(d.get('end_datetime') and d.get('stop_status', 'clean') == 'clean' for d in devices):
                completed.extend(d['end_datetime'] for d in devices)
            else:
                incomplete.append(extremum(timeline, *run_starts, latest=True))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return (extremum(timeline, *starts), extremum(timeline, *completed, latest=True),
            extremum(timeline, *incomplete, latest=True))


def proposed_bounds(config, settings, db):
    first, last = db.execute("SELECT MIN(pc),MAX(pc) FROM raw WHERE kind!='ttl'").fetchone()
    report = {'basis': 'valid_raw_RFID_PC_datetimes_before_tag_filter; TTLs excluded',
              'first_RFID_detection': iso(first, settings.timezone),
              'last_RFID_detection': iso(last, settings.timezone)}
    if first is None:
        return None, dict(report, status='no_RFID_detections')
    if not settings.config_path:
        return None, dict(report, status='no_config_file')
    root = Path(settings.config_path).resolve().parent
    expected = set(candidate_files(root / 'raw_antenna_reads'))
    for source in config.get('acquisition', {}).get('preprocessing', {}).get('sources', []):
        expected.update(candidate_files(root / source['path']))
    selected = {Path(spec.path).resolve() for spec in settings.inputs}
    missing = [str(p) for p in expected if p.resolve() not in selected and detect(str(p)) not in ('unknown', 'compressed')]
    if missing:
        return None, dict(report, status='partial_selection_not_updated', omitted_raw_files=len(missing),
                          note='Select all experiment raw files to recover complete experiment bounds.')
    result = copy.deepcopy(config)
    timeline = normalize_timing(result['recording']['timeline'])
    clock = ExperimentClock(timeline['recording_timezone'])
    first, last = clock.parse(report['first_RFID_detection']).isoformat(), clock.parse(report['last_RFID_detection']).isoformat()
    journal_start, journal_end, open_run = journal_bounds(root, timeline, config)
    old_start, old_end = timeline['automatic_start_datetime'], timeline['automatic_end_datetime']
    provenance = timeline['datetime_provenance']
    start = extremum(timeline, old_start, journal_start, first)
    timeline['automatic_start_datetime'] = start
    if start != old_start:
        provenance['start_source'] = 'first_RFID_board_start_from_journals' if start == journal_start else 'first_raw_RFID_detection'
    candidate_end = extremum(timeline, old_end, journal_end, latest=True)
    needs_recovery = provenance.get('end_requires_recovery', False)
    if open_run and (not journal_end or clock.parse(open_run).timestamp() > clock.parse(journal_end).timestamp()):
        needs_recovery = True
    if not candidate_end or clock.parse(candidate_end).timestamp() < clock.parse(last).timestamp():
        needs_recovery = True
    if needs_recovery:
        timeline['automatic_end_datetime'] = last
        provenance['end_source'] = 'last_raw_RFID_detection'
        provenance['end_requires_recovery'] = False
    else:
        timeline['automatic_end_datetime'] = candidate_end
        if candidate_end == journal_end:
            provenance['end_source'] = 'last_RFID_board_stop_from_journals'
    result['recording']['timeline'] = select_timing(timeline, require_manual=True)
    selected_timing = result['recording']['timeline']
    return result, dict(report, status='proposed', mode=selected_timing['datetime_mode'],
        start_datetime=selected_timing['start_datetime'], end_datetime=selected_timing['end_datetime'],
        automatic_start_datetime=selected_timing['automatic_start_datetime'],
        automatic_end_datetime=selected_timing['automatic_end_datetime'],
        provenance=provenance, scope='all_discovered_experiment_raw_files_and_selected_sources',
        manual_overrides_preserved=selected_timing['datetime_mode'] == 'manual')
