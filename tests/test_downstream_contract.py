"""Downstream schema, observed/interpolated identity, and GUI migration."""
import copy
import csv
import json
from pathlib import Path

import jsonschema
import polars as pl
import pyarrow.parquet as pq
import pytest

from deepecohab_recorder.config_bundle import load_config_bundle, project, save_config_bundle, TUNNEL_KEYS
from deepecohab_recorder.device_antennas import normalize_devices, resolve_routes
from deepecohab_recorder.preprocessing.interpolation import PhysicalGraph
from deepecohab_recorder.schema import migrate_config
from tests.helpers import valid_config
from tests.test_diagnostics_and_options import experiment, gui
from tests.test_device_topology_and_provenance import add_device_branch
from tests.test_preprocessing import TAG


@pytest.mark.parametrize('mode', ['none', 'standard', 'extended'])
@pytest.mark.parametrize('split', [False, True])
def test_inserted_is_boolean_last_and_matches_diagnostic_in_every_output(experiment, mode, split):
    e = experiment
    e.cfg = add_device_branch(e.cfg)
    e.cfg['recording']['devices'][0]['TTL_port'] = 'TTL1-1'
    events = sorted(e.training_events() + [(3100, 'I', TAG), (3200, 'I', TAG),
                                        (3300, 'TTL1', '00000001'), (3310, 'TTL2', '00000002')])
    a = e.raw('first.txt', events, group='1')
    b = e.raw('second.txt', [(7000, 'A', TAG), (8000, 'B', TAG), (8100, 'TTL2', '00000003')], group='2')
    e.process([a, b], parquet=True, csv=True, interpolate=True, interpolation_mode=mode, split_restarts=split)
    true_rows = {(r['stream'], r['row']) for r in e.summary['interpolations']}
    if mode == 'none':
        assert not true_rows
    else:
        assert true_rows
    for output in e.result['outputs']:
        path = e.output / output['file']
        if path.suffix == '.parquet':
            frame = pl.read_parquet(path)
            assert frame.columns[-2:] == ['internal_board_timestamp', 'inserted']
            assert frame.schema['inserted'] == pl.Boolean
            assert frame['inserted'].null_count() == 0
            assert pq.read_schema(path).metadata[b'preprocessing_schema_version'] == b'5'
            values = frame['inserted'].to_list()
            if output['group'] != 'rfid':
                assert not any(values)
        else:
            with path.open(newline='') as handle:
                reader = csv.DictReader(handle)
                assert reader.fieldnames[-2:] == ['internal_board_timestamp', 'inserted']
                rows = list(reader)
            assert all(row['inserted'] in ('True', 'False') for row in rows)
            values = [row['inserted'] == 'True' for row in rows]
            assert pl.read_csv(path).schema['inserted'] == pl.Boolean
        assert values == [(output['stream'], i) in true_rows for i in range(output['rows'])]


def test_config_filters_tunnels_preserves_physical_graph_and_row_extras(tmp_path):
    cfg = add_device_branch(valid_config())
    tunnels = cfg['recording']['layout']['tunnels']
    tunnels.insert(1, tunnels.pop())  # An omitted row between visible rows.
    for i, tunnel in enumerate(tunnels):
        tunnel['_editor_test'] = i
    cfg['recording']['events'] = [dict(name='Added', description='', devices=['Lickometer'], bouts=[
        dict(start='2026-09-01T12:00:00+02:00', position='tunnel_5'),
        dict(start='2026-09-02T12:00:00+02:00', position=None)])]
    cfg = migrate_config(cfg)
    baseline = PhysicalGraph(cfg['recording']['layout'], {1}, devices=cfg['recording']['devices'])
    path = tmp_path/'Test.config.json'
    for _ in range(2):
        main = save_config_bundle(path, cfg)
        assert main['version'] == 8
        r = main['recording']
        assert 'device_antennas' not in r
        assert list(r).index('devices') == list(r).index('events') + 1
        assert list(r)[-1] == 'notes'
        assert [t['name'] for t in r['layout']['tunnels']] == ['tunnel_1', 'tunnel_2', 'tunnel_3', 'tunnel_4']
        assert r['devices'][0] == dict(name='Lickometer-tunnel_5', description=None, device_type='Lickometer', antenna='I-1', position='tunnel_5', TTL_port=None)
        assert r['events'][0]['devices'] == ['Lickometer-tunnel_5']
        assert [b['position'] for b in r['events'][0]['bouts']] == [['tunnel_5'], None]
        jsonschema.validate(main, json.loads((Path(__file__).parents[1]/'docs/config_v8.schema.json').read_text()))
        cfg = load_config_bundle(path)
        assert cfg['recording']['layout']['tunnels'] == tunnels
        graph = PhysicalGraph(cfg['recording']['layout'], {1}, devices=cfg['recording']['devices'])
        assert graph.edges == baseline.edges
        assert graph.movement_edges == baseline.movement_edges


def test_real_v7_pair_migrates_without_losing_descriptions_or_routing(tmp_path):
    cfg = add_device_branch(valid_config())
    cfg['recording']['devices'][0]['description'] = 'Water reward port'
    cfg['acquisition']['preprocessing']['options']['ttl_routes'] = {'TTL1-1': 'TTL_data2'}
    path = tmp_path/'Test.config.json'
    main = save_config_bundle(path, cfg)
    state_path = tmp_path/'Test.acquisition_gui_state.json'
    state = json.loads(state_path.read_text())
    main['version'] = 7
    devices = main['recording'].pop('devices')
    for d in devices:
        d['name'] = d.pop('device_type')
        d['description'] = 'Water reward port'
    main['recording']['device_antennas'] = devices
    main['recording']['layout']['tunnels'] = [{k:t[k] for k in TUNNEL_KEYS if k in t} for t in cfg['recording']['layout']['tunnels']]
    state['state']['recording']['layout'].pop('_unlisted_tunnels')
    state['state']['recording']['device_antennas'] = state['state']['recording'].pop('devices', {})
    main['recording']['events'] = [dict(name='Added', description='', bouts=[dict(start='2026-09-01T12:00:00+02:00',end=None,position='tunnel_5')])]
    path.write_text(json.dumps(main))
    state_path.write_text(json.dumps(state))
    before = path.read_bytes(), state_path.read_bytes()
    loaded = load_config_bundle(path)
    assert before == (path.read_bytes(), state_path.read_bytes())
    assert loaded['recording']['devices'][0]['name'] == 'Lickometer-tunnel_5'
    assert loaded['recording']['devices'][0]['description'] == 'Water reward port'
    saved = save_config_bundle(path, loaded)
    assert saved['recording']['events'][0]['devices'] is None
    assert saved['recording']['events'][0]['bouts'][0]['position'] == ['tunnel_5']
    assert saved['recording']['devices'][0]['description'] == 'Water reward port'
    assert load_config_bundle(path)['acquisition']['preprocessing']['options']['ttl_routes'] == {'TTL1-1': 'TTL_data2'}


def test_generated_names_route_by_type_and_position_and_keep_explicit_overrides():
    cfg = valid_config()
    cfg['recording']['devices'] = normalize_devices([
        dict(device_type='lickometer', antenna='I-1', position='cage_1'),
        dict(device_type='lickometer', antenna='J-1', position='cage_4'),
        dict(device_type='lickometer', antenna='K-1', position='cage_1')])
    options = dict(ttl_routes={'TTL1-1':'TTL_data2'}, routes={'J-1':'device_antenna1'})
    a, _, _, _ = resolve_routes(cfg, {})
    assert a['I-1'] == a['K-1'] and a['I-1'] != a['J-1']
    b, ttl, _, _ = resolve_routes(cfg, options)
    assert b['J-1'] == 'device_antenna1' and ttl['TTL1-1'] == 'TTL_data2'
    cfg['recording']['events'] = [dict(name='Use',description='',devices=['lickometer-cage_1'],bouts=[])]
    cfg['recording']['devices'][0]['device_type'] = 'sensor'
    cfg['recording']['devices'][2]['device_type'] = 'sensor'
    renamed = migrate_config(cfg)
    assert renamed['recording']['events']['event_types'][0]['devices'] == ['sensor-cage_1']
    assert migrate_config(renamed) == renamed


def test_gui_type_name_preview_and_optional_event_devices(gui):
    from PySide6.QtWidgets import QLabel
    from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
    w = gui.window
    cfg = add_device_branch(valid_config())
    w.services.repo.replace(cfg)
    w._apply(cfg)
    layout = w.widgets['layout']
    dialog = AntennaAssignmentDialog(layout.ui, 'I-1', device=cfg['recording']['devices'][0], layout=layout.graph)
    assert isinstance(dialog.device_name, QLabel)
    dialog.device_type.setText('lickometer')
    dialog.device_position.setCurrentText('tunnel_5')
    assert dialog.device_name.text() == 'lickometer-tunnel_5'
    device = dialog.device_value()
    assert device['name'] == 'lickometer-tunnel_5' and device['description'] is None
    dialog.device_description.setText('Water reward port')
    described = dialog.device_value()
    assert described['description'] == 'Water reward port'
    assert described['name'] == device['name']
    dialog.device_description.clear()
    assert dialog.device_value()['description'] is None
    layout.devices = [device]
    events = w.widgets['events']
    events.resize_rows(2)
    for i in range(2):
        events.table.item(i, 0).setText('Lickometer_added')
        events.table.item(i, 2).setText(f'2026-09-0{i+1}T12:00:00+02:00')
        events.table.cellWidget(i, 4).set_position('tunnel_5')
    selector = events.table.cellWidget(0, 5)
    selector.set_devices(['lickometer-tunnel_5'])
    selector.changed.emit()
    assert events.table.cellWidget(1, 5).value() == ['lickometer-tunnel_5']
    data = events.read()['recording.events']
    assert data['event_types'][0]['devices'] == ['lickometer-tunnel_5']
    layout.devices[0]['position'] = 'cage_1'
    assert events.read()['recording.events']['event_types'][0]['devices'] == ['lickometer-cage_1']
    selector.set_devices(None)
    selector.changed.emit()
    assert events.read()['recording.events']['event_types'][0]['devices'] is None
    dialog.close()


def test_invalid_event_device_and_position_contracts_are_rejected():
    from deepecohab_recorder.config_bundle import validate_main
    cfg = valid_config()
    cfg['recording']['events'] = [dict(name='Use',description='',devices=['unknown-device'],bouts=[])]
    with pytest.raises(ValueError, match='unknown devices'):
        project(cfg, 'Test.config.json')
    cfg['recording']['events'][0]['devices'] = 'wrong scalar'
    with pytest.raises(ValueError, match='list of device names'):
        migrate_config(cfg)
    cfg['recording']['events'] = [dict(name='Use',description='',bouts=[dict(start='2026-09-01T12:00:00+02:00',position='cage_1')])]
    main = project(cfg, 'Test.config.json')[0]
    main['recording']['events'][0]['bouts'][0]['position'] = 'cage_1'
    with pytest.raises(ValueError, match='lists of compartment names'):
        validate_main(main)
