"""Experiment-wide bounds, independent of individual acquisition runs."""
import copy
from .clock import ExperimentClock, fixed_phase_origin, phase_origin


def normalize_timing(timeline):
    result = copy.deepcopy(timeline)
    clock = ExperimentClock(result['recording_timezone'])
    mode = result.setdefault('datetime_mode', 'automatic')
    if mode not in ('automatic', 'manual'):
        raise ValueError('Experiment datetime mode must be automatic or manual.')
    for key in ('start_datetime', 'end_datetime'):
        result.setdefault('automatic_' + key, result.get(key))
        result.setdefault('manual_' + key, None)
    for key in ('start_datetime', 'end_datetime', 'automatic_start_datetime',
                'automatic_end_datetime', 'manual_start_datetime', 'manual_end_datetime'):
        value = result.get(key)
        result[key] = clock.parse(value, allow_naive=True).isoformat() if value else None
    result.setdefault('datetime_provenance', {})
    return result


def select_timing(timeline, *, require_manual=False):
    result = normalize_timing(timeline)
    prefix = 'manual_' if result['datetime_mode'] == 'manual' else 'automatic_'
    for key in ('start_datetime', 'end_datetime'):
        result[key] = result[prefix + key]
    if require_manual and prefix == 'manual_' and not all(result[k] for k in ('start_datetime', 'end_datetime')):
        raise ValueError('Manual experiment times require both a start and an end datetime.')
    clock = ExperimentClock(result['recording_timezone'])
    if result['start_datetime'] and result['end_datetime'] and clock.parse(result['end_datetime']).timestamp() < clock.parse(result['start_datetime']).timestamp():
        raise ValueError('Experiment end datetime must not be before its start datetime.')
    for key in ('analysis_start_datetime', 'missing_initial_seconds', 'recorded_before_origin_seconds', 'analysis_origin_error'):
        result.pop(key, None)
    if result['start_datetime']:
        try:
            schedule = result.get('phase_schedule', {})
            if schedule.get('mode') == 'fixed_cycle' and schedule.get('anchor_datetime'):
                origin = fixed_phase_origin(result['start_datetime'], clock.name, schedule, result['start_from'])
            else:
                origin = phase_origin(result['start_datetime'], clock.name, result['phases'], result['start_from'])
            result.update(origin)
        except ValueError as exc:
            result['analysis_origin_error'] = str(exc)
    return result


def extremum(timeline, *values, latest=False):
    clock = ExperimentClock(timeline['recording_timezone'])
    values = [v for v in values if v]
    if not values:
        return None
    return (max if latest else min)(values, key=lambda v: clock.parse(v).timestamp())


def rfid_started(timeline, started):
    result = normalize_timing(timeline)
    old = result['automatic_start_datetime']
    result['automatic_start_datetime'] = extremum(result, old, started)
    if not old or result['automatic_start_datetime'] != old:
        result['datetime_provenance']['start_source'] = 'first_RFID_board_start'
    result['datetime_provenance']['end_requires_recovery'] = True
    result['datetime_provenance']['end_source'] = 'recording_in_progress'
    return select_timing(result)


def rfid_stopped(timeline, stopped, *, complete, clean):
    result = normalize_timing(timeline)
    if clean:
        result['automatic_end_datetime'] = extremum(result, result['automatic_end_datetime'], stopped, latest=True)
    result['datetime_provenance']['end_requires_recovery'] = not (complete and clean)
    result['datetime_provenance']['end_source'] = 'last_RFID_board_stop' if complete and clean else 'incomplete_recording_needs_raw_recovery'
    return select_timing(result)
