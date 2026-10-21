"""Public metadata placement and device-only branch regression coverage."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import polars as pl
import pytest

from deepecohab_recorder.config_bundle import load_config_bundle, project, save_config_bundle, validate_main
from deepecohab_recorder.experiment_timing import select_timing
from deepecohab_recorder.preprocessing.interpolation import PhysicalGraph
from deepecohab_recorder.schema import migrate_config, validate_config
from deepecohab_recorder.topology import normalize_layout, validate_layout
from tests.helpers import valid_config
from tests import test_preprocessing as old
from tests.test_diagnostics_and_options import gui


def timed_config():
    cfg = valid_config()
    t = cfg['recording']['timeline']
    t.update(automatic_start_datetime='2026-09-01T12:00:00+02:00',
             automatic_end_datetime='2026-09-02T12:00:00+02:00',
             datetime_provenance=dict(start_source='first_raw_RFID_detection',
                                      end_source='last_raw_RFID_detection', end_requires_recovery=False))
    cfg['recording']['timeline'] = select_timing(t)
    return cfg


def add_device_branch(cfg, position='tunnel_5'):
    layout = cfg['recording']['layout']
    layout['junctions'] = [dict(id='J1', parent_tunnel='tunnel_1', position=0.5)]
    layout['tunnels'].append(dict(name='tunnel_5', tunnel_no=5, start_cell_id='J1',
                                  end_cell_id=None, dead_end=True, antennas=[]))
    cfg['recording']['devices'] = [dict(name='Lickometer', description='',
        antenna='I-1', position=position, TTL_port=None)]
    return migrate_config(cfg)


def test_metadata_placement_order_and_round_trip(tmp_path):
    cfg = timed_config()
    cfg['recording']['notes'] = 'Experiment notes'
    path = tmp_path/'Test.config.json'
    save_config_bundle(path, cfg)
    text = path.read_text()
    main = json.loads(text)
    assert list(main) == ['version', 'acquisition', 'recording']
    assert list(main['recording'])[-1] == 'notes'
    assert list(main['recording']).index('devices') == list(main['recording']).index('events') + 1
    assert text.rstrip().endswith('"notes": "Experiment notes"\n  }\n}')
    assert 'datetime_provenance' not in main['recording']['timeline']
    expected = dict(start_source='first_raw_RFID_detection', end_source='last_raw_RFID_detection')
    assert main['acquisition']['datetime_provenance'] == expected
    jsonschema.validate(main, json.loads((Path(__file__).parents[1]/'docs/config_v8.schema.json').read_text()))
    loaded = load_config_bundle(path)
    assert project(loaded, path)[0] == main
    assert migrate_config(main)['recording']['timeline']['datetime_provenance'] == expected
    state_path = tmp_path/'Test.acquisition_gui_state.json'
    state_timeline = json.loads(state_path.read_text())['state']['recording']['timeline']
    assert 'datetime_provenance' not in state_timeline
    assert state_timeline['automatic_datetime_provenance']['end_requires_recovery'] is False
    state_path.unlink()
    assert project(load_config_bundle(path), path)[0]['acquisition']['datetime_provenance'] == expected


@pytest.mark.parametrize('version', [5, 6])
def test_older_config_provenance_imports_without_changing_source(tmp_path, version):
    path = tmp_path/'Test.config.json'
    main = save_config_bundle(path, timed_config())
    main['version'] = version
    main['recording']['timeline']['datetime_provenance'] = main['acquisition'].pop('datetime_provenance')
    if version == 5:
        main['recording']['timeline'].pop('end_with')
        main['recording'].pop('devices')
    else:
        main['recording']['device_antennas'] = main['recording'].pop('devices')
    path.write_text(json.dumps(main))
    before = path.read_bytes()
    validate_main(main)
    cfg = load_config_bundle(path)
    assert path.read_bytes() == before
    assert cfg['recording']['timeline']['datetime_provenance']['start_source'] == 'first_raw_RFID_detection'
    result = save_config_bundle(path, cfg)
    assert result['version'] == 8
    assert result['acquisition']['datetime_provenance'] == main['recording']['timeline']['datetime_provenance']
    assert 'datetime_provenance' not in result['recording']['timeline']


def test_manual_automatic_evidence_and_external_edits(tmp_path):
    cfg = timed_config()
    cfg['recording']['timeline'].update(datetime_mode='manual',
        manual_start_datetime='2026-09-01T13:00:00+02:00', manual_end_datetime='2026-09-02T11:00:00+02:00')
    cfg['recording']['timeline'] = select_timing(cfg['recording']['timeline'])
    path = tmp_path/'Test.config.json'
    main = save_config_bundle(path, cfg)
    assert set(main['acquisition']['datetime_provenance'].values()) == {'manual_override'}
    cfg = load_config_bundle(path)
    assert cfg['recording']['timeline']['datetime_provenance']['start_source'] == 'first_raw_RFID_detection'
    cfg['recording']['timeline']['datetime_mode'] = 'automatic'
    cfg['recording']['timeline'] = select_timing(cfg['recording']['timeline'])
    main = save_config_bundle(path, cfg)
    assert main['acquisition']['datetime_provenance']['start_source'] == 'first_raw_RFID_detection'
    assert main['recording']['timeline']['start_datetime'] == '2026-09-01T12:00:00+02:00'
    # Scientific source edits win over a stale GUI-state copy.
    main['acquisition']['datetime_provenance']['start_source'] = 'curated_record'
    path.write_text(json.dumps(main))
    assert project(load_config_bundle(path), path)[0]['acquisition']['datetime_provenance']['start_source'] == 'curated_record'
    main['recording']['timeline']['start_datetime'] = '2026-09-01T11:00:00+02:00'
    path.write_text(json.dumps(main))
    assert project(load_config_bundle(path), path)[0]['acquisition']['datetime_provenance']['start_source'] == 'external_config_edit'


@pytest.mark.parametrize('position', ['tunnel_5', 'cage_1'])
def test_device_spur_preserves_main_routes_and_validation(position):
    cfg = valid_config()
    baseline = PhysicalGraph(cfg['recording']['layout'], {1})
    cfg = add_device_branch(cfg, position)
    r = cfg['recording']
    assert validate_layout(r['layout'], devices=r['devices']) == []
    assert not any('tunnel_5' in message for message in validate_config(cfg))
    graph = PhysicalGraph(r['layout'], {1}, devices=r['devices'])
    assert graph.problems == []
    assert graph.antennas == baseline.antennas
    assert graph.movement_edges == baseline.movement_edges
    assert graph.tunnel_pairs == baseline.tunnel_pairs
    assert all('I-1' not in row for row in graph.tunnel_pairs)
    main = project(cfg, 'Test.config.json')[0]['recording']
    assert 'tunnel_5' not in {t['name'] for t in main['layout']['tunnels']}
    assert main['layout']['junctions'] == r['layout']['junctions']
    assert main['devices'][0]['antenna'] == 'I-1'


def test_device_only_through_tunnel_keeps_its_connections():
    cfg = add_device_branch(valid_config())
    r = cfg['recording']
    branch = r['layout']['tunnels'][-1]
    branch.update(dead_end=False, end_cell_id='C3')
    assert validate_layout(r['layout'], devices=r['devices']) == []
    graph = PhysicalGraph(r['layout'], {1}, devices=r['devices'])
    assert graph.problems == []
    assert 'N:C3' in graph.edges['N:J1']
    assert 'N:J1' in graph.edges['N:C3']
    assert 'I-1' not in graph.antennas
    assert PhysicalGraph(r['layout'], {1}).problems
    assert any('tunnel_5' in error for error in validate_layout(r['layout']))


def test_incomplete_main_and_invalid_device_connections_still_rejected():
    cfg = add_device_branch(valid_config())
    r = cfg['recording']
    for antennas in ([], ['A-1'], ['?', 'B-1']):
        layout = copy.deepcopy(r['layout'])
        layout['tunnels'][0]['antennas'] = antennas
        assert PhysicalGraph(layout, {1}, devices=r['devices']).problems
        assert validate_layout(layout, devices=r['devices'])
    r['layout']['tunnels'][-1]['start_cell_id'] = 'missing_junction'
    assert any('Invalid endpoints' in e for e in PhysicalGraph(r['layout'], {1}, devices=r['devices']).problems)
    assert any('invalid start endpoint' in e for e in validate_layout(r['layout'], devices=r['devices']))


@pytest.mark.parametrize('mode', ['standard', 'extended'])
def test_preprocessing_device_branch_leaves_main_data_and_inference_unchanged(mode):
    e = old.PreprocessingTests()
    e.setUp()
    try:
        events = e.training_events()
        raw = e.raw('main.txt', events)
        e.process([raw], interpolate=True, interpolation_mode=mode, parquet=True)
        main_file = next(x['file'] for x in e.result['outputs'] if x['group'] == 'rfid' and x['file'].endswith('.parquet'))
        baseline = pl.read_parquet(e.output/main_file)
        diagnostics = copy.deepcopy(e.summary)
        e.cfg = add_device_branch(e.cfg)
        raw = e.raw('with_device.txt', sorted(events + [(3100, 'I', old.TAG), (3250, 'I', old.TAG)]))
        e.process([raw], interpolate=True, interpolation_mode=mode, parquet=True)
        result = pl.read_parquet(e.output/main_file)
        assert result.equals(baseline) and result.schema == baseline.schema
        assert result.schema['antenna'] == pl.Categorical()
        assert list(result.schema) == ['datetime', 'antenna', 'time_under', 'animal_id', 'internal_board_timestamp', 'inserted']
        for section in ('transition_medians', 'transition_diagnostic', 'interpolations', 'inference_methods'):
            assert e.summary[section] == diagnostics[section]
        assert e.summary['counts']['interpolated_periods'] > 0
        device_file = next(x['file'] for x in e.result['outputs'] if x['group'] == 'device_antenna1' and x['file'].endswith('.parquet'))
        assert set(pl.read_parquet(e.output/device_file)['antenna'].cast(pl.String)) == {'I-1'}
        assert all(row['antenna'] != 'I-1' for row in e.summary['transition_diagnostic'])
        cfg_path = e.root/'experiment.config.json'
        main = json.loads(cfg_path.read_text())
        assert main['acquisition']['datetime_provenance']['end_source'] == 'last_raw_RFID_detection'
        assert list(main)[-1] == 'recording' and list(main['recording'])[-1] == 'notes'
    finally:
        e.tearDown()


def test_gui_device_conversion_and_tunnel_editor_keep_zero_main_antennas(gui):
    from PySide6.QtWidgets import QDialog, QSpinBox
    from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
    cfg = add_device_branch(valid_config())
    cfg['recording']['devices'] = []
    cfg['recording']['layout']['tunnels'][-1]['antennas'] = ['I-1']
    cfg['recording']['layout'] = normalize_layout(cfg['recording']['layout'])
    w = gui.window
    w.services.repo.replace(cfg)
    w._apply(cfg)
    tab = w.widgets['layout']
    branch = tab.graph['tunnels'][-1]
    def as_device(dialog):
        dialog.role.setCurrentIndex(1)
        dialog.device_type.setText('Lickometer')
        dialog.device_position.setCurrentText('tunnel_5')
        return QDialog.Accepted
    with patch.object(AntennaAssignmentDialog, 'exec', as_device):
        tab.assign_antenna(branch, 0)
    assert branch['antennas'] == []
    assert 'excluded from interpolation' in tab.hint.text()
    def accept_properties(dialog):
        spin = dialog.findChild(QSpinBox)
        assert spin.value() == spin.minimum() == 0
        return QDialog.Accepted
    with patch.object(QDialog, 'exec', accept_properties):
        tab.edit_tunnel(branch)
    data = tab.read()
    assert data['recording.layout']['tunnels'][-1]['antennas'] == []
    assert data['recording.devices'][0]['antenna'] == 'I-1'
    assert validate_layout(data['recording.layout'], devices=data['recording.devices']) == []
