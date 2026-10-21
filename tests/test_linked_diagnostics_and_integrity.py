"""Hash-free diagnostics, linked settings, real part counts and safe replacement."""
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest
import polars as pl
import jsonschema

from deepecohab_recorder.config_bundle import load_config_bundle, save_config_bundle
from deepecohab_recorder.preprocessing.diagnostics import Diagnostics
from deepecohab_recorder.preprocessing.models import InputFile, Settings
from deepecohab_recorder.preprocessing.pipeline import run
from deepecohab_recorder.preprocessing.result_state import result_integrity
from tests.helpers import preprocessing_context
from tests.test_output_publication import experiment, snapshot
from tests.test_diagnostics_and_options import experiment as linear_experiment
from tests import test_preprocessing as old


def read_report(result):
    return json.loads(Path(result['diagnostic_path']).read_text())


def assert_public(value):
    if isinstance(value, dict):
        for key, item in value.items():
            assert not re.search(r'sha[-_]?\d+|hash|digest|checksum|fingerprint|md5', key, re.I), key
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)


def test_no_copied_config_or_visible_integrity_and_settings_snapshot(tmp_path):
    main, raw, settings = experiment(tmp_path)
    cfg = load_config_bundle(main)
    cfg['acquisition']['device_history'] = {'camera': {'frames': 1234, 'status': 'stopped'}}
    save_config_bundle(main, cfg)
    result = run(settings)
    log = read_report(result)
    assert log['diagnostics_version'] == 12
    assert_public(log)
    assert_public(result)
    for key in ('device_evidence', 'recording_timezone', 'experiment_output_name'):
        assert key not in log
    for key in ('options', 'stitch_windows_ms', 'antenna_aliases', 'excluded_movement_inputs', 'boundary_policy'):
        assert key not in log['processing']
    for key in ('mode', 'start_datetime', 'end_datetime', 'automatic_start_datetime',
                'automatic_end_datetime', 'provenance', 'path', 'backup', 'diagnostic_log_path'):
        assert key not in log['experiment_timing']
    context = preprocessing_context(log, result['diagnostic_path'])
    assert context['options']['window_ms'] == 300
    cfg = load_config_bundle(main)
    for digest in result_integrity(cfg)['output_files'].values():
        assert digest not in Path(result['diagnostic_path']).read_text()
    cfg['acquisition']['preprocessing']['options']['window_ms'] = 600
    save_config_bundle(main, cfg)
    assert preprocessing_context(log, result['diagnostic_path']) == context
    schema = json.loads((Path(__file__).parents[1]/'docs/diagnostic_v12.schema.json').read_text())
    jsonschema.validate(log, schema)
    assert all(o['part'] == dict(number=1, total=1) for o in log['outputs'])
    assert raw.exists()


def test_duplicates_are_still_detected_without_publishing_their_hash(tmp_path):
    main, raw, settings = experiment(tmp_path)
    first = run(settings)
    baseline = {o['file']: pl.read_parquet(tmp_path/o['file']) for o in first['outputs'] if o['file'].endswith('.parquet')}
    duplicate = raw.with_name('duplicate.csv')
    shutil.copyfile(raw, duplicate)
    settings.inputs.append(InputFile(str(duplicate), board_id=1, profile='v1.5'))
    result = run(settings)
    log = read_report(result)
    assert log['counts']['duplicate_files_skipped'] == 1
    assert log['issues']['duplicate_file_skipped']['count'] == 1
    assert_public(log)
    for name, frame in baseline.items():
        assert frame.equals(pl.read_parquet(tmp_path/name))


def test_sparse_group_parts_and_dual_formats_count_generated_parts(linear_experiment):
    e = linear_experiment
    specs = [e.raw('one.txt', [(0, 'A', old.TAG), (50, 'A', old.TAG), (60, 'TTL1', '00000001')]),
             e.raw('two.txt', [(1000, 'TTL2', '00000002')]),
             e.raw('three.txt', [(2000, 'A', old.TAG), (2100, 'B', old.TAG), (2200, 'TTL1', '00000001')])]
    for i, spec in enumerate(specs, 1):
        spec.part = i
    e.process(specs, parquet=True, split_restarts=True, routes={'B-1': 'device_antenna1'}, ttl_routes={'TTL2-1': 'TTL_data2'})
    groups = {}
    for entry in e.summary['outputs']:
        groups.setdefault(entry['group'], {})[entry['stream']] = entry['part']
    assert groups == {
        'rfid': {'rfid:1': {'number': 1, 'total': 2}, 'rfid:3': {'number': 2, 'total': 2}},
        'device_antenna1': {'device_antenna1:3': {'number': 1, 'total': 1}},
        'TTL_data1': {'TTL_data1:1': {'number': 1, 'total': 2}, 'TTL_data1:3': {'number': 2, 'total': 2}},
        'TTL_data2': {'TTL_data2:2': {'number': 1, 'total': 1}},
    }
    assert len(e.result['outputs']) == sum(map(len, groups.values())) * 2
    assert_public(e.summary)
    old_files = {e.output/o['file'] for o in e.result['outputs']}
    e.process(specs, parquet=True, split_restarts=False, routes={'B-1': 'device_antenna1'}, ttl_routes={'TTL2-1': 'TTL_data2'})
    assert all(o['part'] == {'number': 1, 'total': 1} for o in e.summary['outputs'])
    assert not any(p.exists() for p in old_files)


def test_v11_result_upgrades_without_converter_and_preserves_data(tmp_path):
    main, raw, settings = experiment(tmp_path)
    result = run(settings)
    log = read_report(result)
    for entry in log['outputs']:
        entry['sha256'] = hashlib.sha256((tmp_path/entry['file']).read_bytes()).hexdigest()
        entry['part'] = int(entry['stream'].rpartition(':')[2])
    log.update(diagnostics_version=11, experiment_output_name='E')
    Path(result['diagnostic_path']).write_text(json.dumps(log))
    cfg = load_config_bundle(main)
    cfg['acquisition']['preprocessing'].pop('_output_integrity')
    cfg['acquisition']['preprocessing'].pop('completed_run')
    save_config_bundle(main, cfg)
    before = {o['file']: (tmp_path/o['file']).read_bytes() for o in result['outputs']}
    result = run(settings)
    assert_public(read_report(result))
    assert result_integrity(load_config_bundle(main))['output_files']
    assert before == {name: (tmp_path/name).read_bytes() for name in before}


def test_edited_outputs_and_missing_state_remain_protected(tmp_path):
    main, raw, settings = experiment(tmp_path)
    result = run(settings)
    output = tmp_path/result['outputs'][0]['file']
    original = output.read_bytes()
    output.write_bytes(original + b'edited')
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='changed since'):
        run(settings)
    assert snapshot(tmp_path) == before
    output.write_bytes(original)
    (tmp_path/'E.acquisition_gui_state.json').unlink()
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='matching application state'):
        run(settings)
    assert snapshot(tmp_path) == before
    # A fresh output folder remains available when old ownership evidence is lost.
    settings.output_dir = str(tmp_path/'fresh')
    result = run(settings)
    assert output.read_bytes() == original
    assert_public(read_report(result))


def test_config_free_reprocessing_and_integrity_are_supported(tmp_path):
    raw = tmp_path/'raw.txt'
    raw.write_text('A-1;0000A819;1;2026-09-01T12:00:00+02:00;1\n')
    output = tmp_path/'output'
    settings = Settings([InputFile(str(raw), board_id=1, profile='v1.5')], str(output),
                        whitelist=False, interpolate=False, parquet=False, csv=True)
    result = run(settings)
    log = read_report(result)
    assert log['processing']['options']['whitelist'] is False
    assert log['recording_timezone'] == settings.timezone
    assert_public(log)
    assert len(list(output.glob('.*.preprocessing_state'))) == 1
    run(settings)
    target = output/result['outputs'][0]['file']
    target.write_text(target.read_text() + 'edited')
    with pytest.raises(ValueError, match='changed since'):
        run(settings)


def test_internal_issue_fields_never_reach_json(tmp_path):
    d = Diagnostics(tmp_path, 'Europe/Warsaw')
    d.write('future_internal_warning', checksum='secret', evidence={'SHA256': 'secret', 'file': 'raw.txt'},
            extra=[{'input_hash': 'secret', 'explanation': 'Readable diagnostic'}])
    d.info['config_bundle_sha256'] = 'secret'
    d.finish(None)
    log = json.loads((tmp_path/d.filename).read_text())
    assert_public(log)
    assert 'secret' not in json.dumps(log)
    assert log['issues']['future_internal_warning']['examples'][0]['evidence']['file'] == 'raw.txt'


def test_linked_state_and_result_survive_moving_experiment_folder(tmp_path):
    root = tmp_path/'original'
    main, raw, settings = experiment(root)
    result = run(settings)
    before = {o['file']: (root/o['file']).read_bytes() for o in result['outputs']}
    moved = tmp_path/'moved'
    shutil.move(root, moved)
    settings.config_path = str(moved/main.name)
    settings.output_dir = str(moved)
    settings.inputs[0].path = str(moved/'raw_antenna_reads'/raw.name)
    result = run(settings)
    assert before == {name: (moved/name).read_bytes() for name in before}
    assert preprocessing_context(read_report(result), result['diagnostic_path'])['options']['window_ms'] == 300


def test_uncertain_stops_reference_distinct_saved_epochs(tmp_path):
    main, raw, settings = experiment(tmp_path)
    cfg = load_config_bundle(main)
    def epoch(start):
        return dict(config={'board_id': 1}, start_datetime=start, end_datetime=None,
                    stop_status='error', data_file='raw_antenna_reads/capture.csv', error='USB disconnect')
    current = epoch('2026-09-01T11:59:00+02:00')
    current['previous_epochs'] = [epoch('2026-09-01T11:00:00+02:00')]
    cfg['acquisition']['recording_history'] = {'runs': [{'run_id': 'run1', 'devices': {'board1': current}}]}
    save_config_bundle(main, cfg)
    result = run(settings)
    rows = read_report(result)['acquisition_evidence']
    assert len(rows) == 2
    assert [(r['run'], r['device'], r['epoch']) for r in rows] == [('run1', 'board1', 0), ('run1', 'board1', 1)]
    assert all(r['true_stop_unknown'] and 'recording_start' not in r and 'error' not in r for r in rows)
    saved = load_config_bundle(main)['acquisition']['recording_history']['runs'][0]['devices']['board1']
    assert saved['error'] == 'USB disconnect' and saved['previous_epochs'][0]['error'] == 'USB disconnect'
