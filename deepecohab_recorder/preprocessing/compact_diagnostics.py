"""Version 12: diagnostic observations, with links to saved setup and settings."""
import copy
import json
import re
from pathlib import Path
from .models import iso, parse_pc
from .recording_gaps import observed_file_gaps

DEFINITIONS = {
    'inserted': 'Last data column: Boolean True for interpolated rows; False for rows derived from raw observations, including stitched RFID periods, minimum-duration padding and TTL events.',
    'sections': {
        'processing': 'Measured clock reference and data format. settings_source points to the executed settings in linked GUI state; standalone preprocessing includes its settings here.',
        'recordings': 'One entry per selected file or board folder; bounds are observations, not proof of uninterrupted recording.',
        'clocks': 'Independent board clock epochs, estimates and resets. PC anchoring is approximate, not hardware synchronization.',
        'recording_boundaries': 'One entry per reported logical recording gap or confirmed acquisition interruption.',
        'transition_diagnostic': 'Counts of antenna visits/candidates for every configured mouse and main antenna, including zero rows.',
        'transition_medians': 'Every mouse and directed adjacent tunnel pair; only observed visits train these values.',
        'interpolations': 'Every inserted row, its observed anchors and a reference to inference_methods. Row numbers start at zero; CSV header excluded. outputs identifies each stream and file.',
        'issues': 'Warnings, failure reasons and bounded examples. Only nonempty categories appear.',
        'read_diagnostic': 'Raw accepted/rejected read totals per input. These are not stitched visit counts.',
        'device_diagnostic': 'Observed device RFID periods, separate from movement transition statistics.',
        'duration_summary': 'Observed RFID period durations per antenna, in milliseconds.',
        'outputs': 'Published filenames, input membership, row counts and generated part number/total for each group. CSV and Parquet copies of one part count once.',
        'experiment_timing': 'First/last valid raw RFID PC datetimes before tag filtering, excluding TTLs, and metadata recovery outcome. Selected experiment bounds and provenance are in the main config.',
        'acquisition_evidence': 'Uncertain stop events in linked GUI recording_history, identified by run, device and epoch. Epoch indexes previous_epochs followed by the current device epoch, starting at zero. The last observed detection is in recordings; it is not the true stop time.',
    },
    'raw_read': 'One parsed RFID detection or TTL event from a source recording.',
    'stitched_period': 'Consecutive reads of one animal at one antenna joined within its selected gap window; not a raw read count.',
    'correct': 'Observed accepted RFID periods after stitching; excludes interpolations. This is not independent proof of the physical route.',
    'interpolated': 'Estimated missing antenna visits actually inserted.',
    'rejected': 'Identified missing-antenna candidates not inserted, for any reason. Once per animal, observed transition and candidate antenna. Not rejected tag reads.',
    'ambiguous_route': 'Several permitted routes remain possible. Each distinct candidate antenna receives one rejection; this does not prove all alternatives were visited. Antenna-candidate totals can exceed transition totals.',
    'missed_antenna_detection': 'Possible missing visit inferred from layout and observations, not measured hardware loss.',
    'rejected_tag_reads': 'Raw RFID reads excluded because their tag is absent from the allowed cohort; cannot be attributed to a real configured mouse.',
    'observed_record_gap': 'No records between bounding logical recording ranges. Silence alone does not prove failure or establish its exact time.',
    'confirmed_DAQ_inactive': 'A clean recorded stop and subsequent start establish an inactive interval.',
    'abrupt_end': 'True stop/failure time is unknown. Last detections are observed evidence; recorded stop/error events remain in linked GUI history.',
    'clock_epoch': 'One continuous interpretation of a board counter or legacy PC clock. Resets constrain interpolation.',
    'median_ms': 'Next observed first read minus previous actual last read, using board time when available. Null means no observed transit; samples=0. Below-threshold medians remain reported.',
    'inference_methods': 'Shared interpolation explanations. mouse_median and cohort_median reference zero-based rows in transition_medians and cohort_medians. Each inserted row refers to one method. previous/following reference observed output rows (an integer in the same stream, otherwise a stream/row object). Actual previous end times preserve anchors before duration rounding or minimum padding.',
    'cohort_fallback': 'A reference to a separately recorded cohort estimate; it never replaces the observed mouse median.',
    'TTL': 'Unstitched instantaneous rising edge, zero duration and null animal_id. Device association does not assign an animal.',
    'null': 'Unavailable or inapplicable, not zero.',
    'zero': 'No counted events in the selected data. Interpolation disabled means no attempts, not failures.',
    'folder_recording': 'User-selected board folder treated as one logical recording. Routine internal file inventory/boundaries are omitted; real errors and resets remain.',
    'counts': 'Raw rows, stitched periods, observed transitions and rejected antenna candidates have different units; they must not be added together.',
    'reason_precedence': 'Candidate rejection first checks recording/clock boundary, then route ambiguity/mode, clock overlap, missing timing support, and timing/coverage placement. One primary reason per candidate.',
    'part': 'number is the chronological ordinal among parts actually generated for this output group; total is their count. stream retains the original recording-part identity, including gaps when a group has no data in some recording parts.',
}


def public_fields(value):
    """Prevent internal integrity fields leaking through optional issue examples."""
    if isinstance(value, dict):
        return {k: public_fields(v) for k,v in value.items()
                if not re.search(r'sha[-_]?\d+|hash|digest|checksum|fingerprint|md5', str(k), re.I)}
    if isinstance(value, (list, tuple)):
        return [public_fields(v) for v in value]
    return value


def portable(path, root):
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path).replace('\\', '/')


def recording_summary(info, root, zone, options):
    groups, file_ids, paths = {}, {}, {}
    for source in info.get('files', []):
        folder = source.get('source_kind') == 'folder'
        path = source.get('source_path') or source['path']
        # Compatibility with explicitly supplied pre-v16 folder assignments.
        if not source.get('source_path') and source.get('rotation_group') and Path(source['rotation_group']).is_dir():
            folder, path = True, source['rotation_group']
        key = (source['board_id'], path, source['recording_part'], 'folder' if folder else 'file')
        groups.setdefault(key, []).append(source)
    rows, logical = [], []
    for (board, path, part, kind), sources in groups.items():
        number = len(rows) + 1
        for s in sources:
            file_ids[s['id']] = number
            paths[s['path'].replace('\\', '/')] = number
        first = min(s['first_pc_us'] for s in sources)
        last = max(s['last_pc_us'] for s in sources)
        row = dict(id=number, path=portable(path, root), kind=kind, board=board, part=part,
                   file_count=len(sources), bytes=sum(s['bytes'] for s in sources),
                   first_record=iso(first, zone), last_record=iso(last, zone),
                   raw_reads=sum(s.get('valid_reads', 0) for s in sources),
                   profile=sources[0]['profile'])
        explicit = {(s.get('recording_start'), s.get('recording_end'), s.get('stop_status'), s.get('timing_basis')) for s in sources if s.get('recording_start') or s.get('recording_end')}
        # Journal evidence is emitted once below; retain user-declared bounds here.
        bounds = [dict(start=a, end=b, stop_status=c, basis=d) for a,b,c,d in sorted(explicit, key=str) if d != 'acquisition_journal']
        if bounds: row['declared_bounds'] = bounds
        rows.append(row)
        logical.append(dict(id=number, board_id=board, first_pc_us=first, last_pc_us=last))
    gaps = []
    minimum = options.get('minimum_recording_gap_seconds', 180) if options.get('ignore_short_recording_gaps', True) else 0
    if options.get('flag_missed_data_periods', True):
        for left, right in observed_file_gaps(logical):
            duration = (right['first_pc_us'] - left['last_pc_us']) / 1e6
            if duration >= minimum:
                gaps.append(dict(board=right['board_id'], previous_recording=left['id'], next_recording=right['id'],
                                 kind='observed_record_gap', start=iso(left['last_pc_us'], zone),
                                 end=iso(right['first_pc_us'], zone), seconds=duration))
    journals, seen = [], set()
    for coverage in info.get('recording_coverage', []):
        board = coverage['board_id']
        for j in coverage.get('acquisition_journals', []):
            entry = {k: v for k,v in j.items() if k not in ('data_file','basis','reported_stop_is_failure_time') and v not in (None, '', [], {})}
            rid = paths.get(j.get('data_file', '').replace('\\', '/'))
            if rid is None:
                candidates = {n for p,n in paths.items() if p.endswith('/' + j.get('data_file', '').replace('\\', '/'))}
                if len(candidates) == 1: rid = candidates.pop()
            if rid is not None: entry['recording'] = rid
            if j.get('stop_status') != 'clean': entry['true_stop_unknown'] = True
            key = json.dumps(entry, sort_keys=True)
            if key not in seen:
                seen.add(key)
                journals.append(entry)
        for gap in coverage.get('confirmed_interruptions', []):
            match = next((g for g in gaps if g['board'] == board and
                          parse_pc(g['start'], zone) <= parse_pc(gap['start'], zone) <= parse_pc(gap['end'], zone) <= parse_pc(g['end'], zone)), None)
            evidence = dict(start=gap['start'], end=gap['end'], seconds=gap['duration_seconds'])
            if match:
                match['confirmed_DAQ_inactive'] = evidence
            else:
                row = dict(board=board, kind='confirmed_DAQ_inactive', **evidence)
                if row not in gaps: gaps.append(row)
    clocks = []
    for source in info.get('clock_epochs', []):
        row = {k:v for k,v in source.items() if k not in ('files', 'port', 'first_record', 'recording_start', 'recording_end', 'offset_from_reference_ms') and (v is not None or k == 'tick_ms')}
        row['recordings'] = sorted({file_ids[i] for i in source.get('files', []) if i in file_ids})
        clocks.append(row)
    return rows, gaps, clocks, journals


def compact_medians(info, include_tunnel=True):
    rows, cohort, lookup, cohort_lookup = [], [], {}, {}
    for source in info.get('transition_medians', []):
        key = (source['from_antenna'], source['to_antenna'])
        row = dict(animal=source['tag'], from_antenna=key[0], to_antenna=key[1],
                   median_ms=source['median_ms'], samples=source['samples'], eligible=source['eligible'])
        if include_tunnel:
            row['tunnel'] = source['tunnel']
        if source.get('reason') == 'excluded_from_movement_inference':
            row['excluded_from_inference'] = True
        lookup[(source['tag'], *key)] = len(rows)
        estimate = source.get('interpolation_estimate', {})
        if estimate.get('scope') == 'cohort':
            if key not in cohort_lookup:
                cohort_lookup[key] = len(cohort)
                cohort.append(dict(from_antenna=key[0], to_antenna=key[1], **{k:estimate[k] for k in ('median_ms','samples','eligible')}))
            row['cohort_fallback'] = cohort_lookup[key]
        rows.append(row)
    return rows, cohort, lookup, cohort_lookup


def compact_header(d):
    info = d.info
    linked = getattr(d, 'linked_config', None)
    options = dict(info.get('processing_options', {}))
    for key in ('ttl_routes','device_groups'):
        options.pop(key, None)  # Actual membership is in outputs.
    root = getattr(d, 'config_root', d.directory)
    recordings, boundaries, clocks, journals = recording_summary(info, root, d.zone, options)
    counts = {k:v for k,v in d.counts.items() if k not in ('routine_file_boundaries','missed_data_periods')}
    counts['rejected_interpolation_candidates'] = sum(d.rejection_reasons.values())
    ignored = ('transit_median', 'recording_boundary', 'expected_tag_antenna_without_reads',
               'interpolation_skipped', 'ambiguous_transition')
    issues = {k:dict(count=v, examples=d.examples.get(k, [])) for k,v in d.detail_counts.items()
              if not k.startswith(ignored)}
    if d.rejection_reasons: issues['rejected_interpolation_reasons'] = dict(d.rejection_reasons)
    if info.get('acquisition_journal_warnings'): issues['acquisition_evidence_warnings'] = info['acquisition_journal_warnings']
    movement = info.get('movement_antennas', {})
    if movement.get('unmapped_main'): issues['unmapped_main_antennas'] = movement['unmapped_main']
    if info.get('transition_medians_policy', {}).get('layout_problems'):
        issues['layout'] = info['transition_medians_policy']['layout_problems']
    timing = info.get('experiment_timing', {})
    # Selected/automatic bounds, sources and mode are owned by the config pair.
    timing_keys = ('first_RFID_detection', 'last_RFID_detection', 'status',
                   'omitted_raw_files', 'note')
    selected_timing = {k:timing[k] for k in timing_keys if k in timing}
    processing = dict(origin_datetime=info.get('origin_datetime'),
                      reference_board=info.get('reference_board'), reference_epoch=info.get('reference_epoch'),
                      formats=info.get('format_schemas', {}),
                      output_sort_order=info.get('output_sort_order'))
    if linked:
        processing['settings_source'] = dict(file=info['gui_state_file'], section='state.acquisition.preprocessing.completed_run')
        # No second copy of acquisition starts/stops, counters or device history.
        journals = [dict(run=j['journal'], board=j['board_id'],
                         device=j['device'], epoch=j['epoch'],
                         **({'recording': j['recording']} if 'recording' in j else {}),
                         true_stop_unknown=True) for j in journals if j.get('true_stop_unknown')]
    else:
        processing.update(options=options, stitch_windows_ms=info.get('stitch_windows_ms', {}),
                          excluded_movement_inputs=movement.get('excluded_by_output_group', []))
        if info.get('antenna_aliases'): processing['antenna_aliases'] = info['antenna_aliases']
    result = dict(definitions=copy.deepcopy(DEFINITIONS), diagnostics_version=12)
    header_keys = ('software','created_at','status','config_file')
    if not linked:
        header_keys += ('recording_timezone','experiment_output_name')
    result.update({k:info[k] for k in header_keys if k in info})
    result.update(processing=processing, experiment_timing=selected_timing, counts=counts,
                  recordings=recordings, clocks=clocks, recording_boundaries=boundaries)
    if journals: result['acquisition_evidence'] = journals
    # Device history already uses compact bounded events/settings; include once,
    # without duplicating the RFID recording_history or journal-derived facts.
    if not linked and info.get('device_history'): result['device_evidence'] = info['device_history']
    result['issues'] = issues
    result['outputs'] = [{k:v for k,v in o.items() if k != 'relative_path' or v != o.get('file')} for o in info.get('outputs', [])]
    return public_fields(result)
