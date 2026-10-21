"""Complete diagnostics, reporting thresholds, merge defaults and tunnel measures."""
import copy
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from deepecohab_recorder.config_bundle import load_config_bundle, project, save_config_bundle
from deepecohab_recorder.preprocessing.diagnostics import Diagnostics
from deepecohab_recorder.preprocessing.models import Settings, parse_pc
from deepecohab_recorder.preprocessing.recording_gaps import recording_boundaries, recording_coverage
from deepecohab_recorder.topology import normalize_layout, validate_layout
from tests import test_preprocessing as old
from tests import test_metadata_and_gui as gui_old
from tests.test_config_bundle_and_evidence import make_pair


@pytest.fixture
def experiment():
    e = old.PreprocessingTests()
    e.setUp()
    try:
        yield e
    finally:
        e.tearDown()


@pytest.fixture
def gui():
    gui_old.Patch6GuiTests.setUpClass()
    e = gui_old.Patch6GuiTests()
    e.setUp()
    try:
        yield e
    finally:
        e.tearDown()


def observations():
    return [(ms, ant, old.TAG) for ms, ant in (
        (0, 'A'), (100, 'A'), (200, 'B'), (250, 'B'),
        (1000, 'A'), (1100, 'A'), (1300, 'B'), (1400, 'B'),
        (2400, 'A'), (2700, 'B'), (4000, 'A'), (4400, 'B'))]


@pytest.mark.parametrize('interpolate', [False, True])
def test_complete_medians_without_interpolation_demand(experiment, interpolate):
    e = experiment
    absent = '0000C819'
    e.cfg['recording']['cohort']['animals'].append(dict(e.cfg['recording']['cohort']['animals'][0], tag=absent,subject_name='absent'))
    e.cfg['recording']['cohort']['n_mice'] = 3
    events = observations() + [(500, 'C', old.OTHER), (850, 'D', old.OTHER)]
    e.process([e.raw('medians.txt', sorted(events))], interpolate=interpolate)
    rows = e.summary['transition_medians']
    assert len(rows) == 3 * 2 * 2 > Diagnostics.EXAMPLE_LIMIT
    by_key = {(r['animal'], r['from_antenna'], r['to_antenna']): r for r in rows}
    for tag in (old.TAG, old.OTHER, absent):
        assert {(a, b) for t, a, b in by_key if t == tag} == {('A-1', 'B-1'), ('B-1', 'A-1'), ('C-1', 'D-1'), ('D-1', 'C-1')}
    forward, backward = by_key[old.TAG, 'A-1', 'B-1'], by_key[old.TAG, 'B-1', 'A-1']
    assert (forward['median_ms'], forward['samples'], forward['eligible']) == (250, 4, True)
    assert (backward['median_ms'], backward['samples'], backward['eligible']) == (1000, 3, True)
    sparse = by_key[old.OTHER, 'C-1', 'D-1']
    assert (sparse['median_ms'], sparse['samples'], sparse['eligible']) == (350, 1, False)
    assert not sparse['eligible']
    assert all(r['median_ms'] is None and r['samples'] == 0
               for r in rows if r['animal'] == absent)
    assert e.inserted == []
    assert 'transit_median' not in e.summary['issues']


def test_cohort_estimate_does_not_replace_individual_median(experiment):
    e = experiment
    e.process([e.raw('cohort.txt', sorted(observations() + [(6000, 'A', old.OTHER), (6800, 'B', old.OTHER)]))],
              cohort_fallback=True)
    row = next(r for r in e.summary['transition_medians'] if (r['animal'], r['from_antenna'], r['to_antenna']) == (old.OTHER, 'A-1', 'B-1'))
    assert (row['median_ms'], row['samples'], row['eligible']) == (800, 1, False)
    assert e.summary['cohort_medians'][row['cohort_fallback']] == dict(from_antenna='A-1',to_antenna='B-1',median_ms=300,samples=5,eligible=True)


def test_unknown_accepted_mouse_and_excluded_antenna_are_explicit(experiment):
    e = experiment
    extra = '0000D819'
    e.process([e.raw('extra.txt', [(0, 'A', extra), (500, 'B', extra)])], whitelist=False, routes={'B-1': 'auxiliary'})
    rows = e.summary['transition_medians']
    assert len(rows) == 12
    excluded = [r for r in rows if 'B-1' in (r['from_antenna'], r['to_antenna'])]
    assert len(excluded) == 6
    assert all(r['samples'] == 0 and r['excluded_from_inference'] for r in excluded)


def test_report_does_not_require_layout_when_interpolation_off(experiment):
    e = experiment
    e.cfg['recording']['layout'] = {}
    e.process([e.raw('no_layout.txt', [(0, 'A', old.TAG)])])
    assert e.summary['transition_medians'] == []
    assert e.summary['issues']['layout']


def test_default_merge_can_be_disabled_and_clock_checks_unchanged(experiment):
    e = experiment
    a = e.raw('a.txt', [(0, 'A', old.TAG), (100, 'A', old.TAG)])
    b = e.raw('b.txt', [(400000, 'B', old.TAG), (400100, 'B', old.TAG)], group='2')
    default_rows = e.process([replace(a), replace(b)])
    assert [r['file'] for r in e.result['outputs']] == ['experiment.data.csv']
    clocks = copy.deepcopy(e.summary['clocks'])
    parts = {r['part'] for r in e.summary['recordings']}
    assert e.context['options']['split_restarts'] is False
    split_rows = e.process([replace(a), replace(b)], split_restarts=True)
    assert [r['file'] for r in e.result['outputs']] == ['experiment.data_part01.csv', 'experiment.data_part02.csv']
    assert split_rows == default_rows
    assert e.summary['clocks'] == clocks
    assert {r['part'] for r in e.summary['recordings']} == parts


def test_short_gap_filter_boundaries_custom_threshold_and_outputs(experiment):
    e = experiment
    # Last-to-first gaps are 179.999, 180 and 180.001 seconds.
    specs = [e.raw(f'part{i}.txt', [(start, 'A', old.TAG), (start + 10, 'A', old.TAG)])
             for i, start in enumerate((0, 180009, 360019, 540030))]
    baseline = e.process([replace(s) for s in specs])
    assert [b['seconds'] for b in e.summary['recording_boundaries']] == [180, 180.001]
    assert e.context['options']['minimum_recording_gap_seconds'] == 180
    assert 'missed_data_periods' not in e.summary and 'recording_coverage' not in e.summary
    clocks = e.summary['clocks']
    assert e.process([replace(s) for s in specs], ignore_short_recording_gaps=False) == baseline
    assert [b['seconds'] for b in e.summary['recording_boundaries']] == [179.999, 180, 180.001]
    assert len(e.summary['recording_boundaries']) == 3
    assert e.summary['clocks'] == clocks
    assert e.process([replace(s) for s in specs], minimum_recording_gap_seconds=180.001) == baseline
    assert [b['seconds'] for b in e.summary['recording_boundaries']] == [180.001]
    e.process([replace(s) for s in specs], minimum_recording_gap_seconds=30)
    assert len(e.summary['recording_boundaries']) == 3


def test_full_boundary_list_exceeds_preview_and_rotations_preserved(experiment):
    e = experiment
    specs = [e.raw(f'hour{i}.txt', [(i * 1000, 'A', old.TAG)]) for i in range(10)]
    for source in specs:
        source.rotation_group, source.part = 'hourly', 1
    e.process(specs)
    assert e.summary['recording_boundaries'] == []
    assert len(e.summary['recordings']) == 10
    assert 'routine_file_boundaries' not in e.summary
    e.process(specs, ignore_short_recording_gaps=False)
    assert len(e.summary['recording_boundaries']) == 9
    assert all(r['kind']=='observed_record_gap' for r in e.summary['recording_boundaries'])


def source(start, end, ident, board=1):
    base = parse_pc('2025-06-01T12:00:00+02:00', 'Europe/Warsaw')
    return dict(first_pc_us=base + start * 1_000_000, last_pc_us=base + end * 1_000_000,
                id=ident, path=f'{ident}.txt', recording_part=ident, board_id=board, port=f'COM{board}')


def test_gap_union_board_isolation_and_confirmed_short_daq_stop():
    sources = [source(0, 600, 1), source(100, 120, 2), source(200, 300, 3),
               source(780, 1000, 4), source(0, 1000, 5, board=2)]
    gap, = recording_boundaries(sources, 'Europe/Warsaw', 180)
    assert (gap['previous_file'], gap['next_file'], gap['record_gap_seconds'], gap['board_id']) == ('1.txt', '4.txt', 180, 1)
    journals = [dict(board_id=1, recording_start='2025-06-01T12:00:00+02:00', recording_end='2025-06-01T12:10:00+02:00', stop_status='clean', data_file='first.txt'),
                dict(board_id=1, recording_start='2025-06-01T12:10:20+02:00', recording_end=None, stop_status='unknown', data_file='second.txt')]
    coverage, = recording_coverage([source(0, 600, 1), source(620, 1000, 2)], 'Europe/Warsaw', journals, 180)
    assert coverage['observed_record_gaps'] == []
    assert coverage['confirmed_interruptions'][0]['duration_seconds'] == 20


@pytest.mark.parametrize('value', [-1, True, '180', float('nan'), float('inf')])
def test_invalid_gap_threshold_rejected(experiment, value):
    e = experiment
    spec = e.raw('invalid.txt', [(0, 'A', old.TAG)])
    with pytest.raises(ValueError, match='Minimum recording gap'):
        Settings([spec], str(e.root / 'out'), minimum_recording_gap_seconds=value).validate()


def test_windows_newline_and_unicode_summary_rewrite_preserves_stream(tmp_path, monkeypatch):
    import sqlite3
    original_open = Path.open
    def windows_open(path, mode='r', *args, **kwargs):
        if 'w' in mode and 'b' not in mode and 'newline' not in kwargs: kwargs['newline']='\r\n'
        return original_open(path,mode,*args,**kwargs)
    monkeypatch.setattr(Path,'open',windows_open)
    from deepecohab_recorder.preprocessing.pipeline import SQL
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript(SQL)
    db.executescript("CREATE TABLE raw_counts(antenna,kind,tag,reads,accepted); CREATE TABLE output_rows(id,destination,part,row_number); INSERT INTO output_rows VALUES(1,'rfid',1,0),(2,'rfid',1,1);")
    for ident in (1,2): db.execute('INSERT INTO passes(id,antenna,tag,pc_end,kind,interpolated,duration) VALUES(?,?,?,?,?,?,?)',(ident,'A-1','0000A819',ident*1000000,'rfid',0,10))
    d=Diagnostics(tmp_path,'Europe/Warsaw');d.info['experiment_output_name']='Zażółć — myszy'
    for i in range(6000):
        d.write('output_record',is_interpolated=True,row_number=i,stream='rfid:1',animal_id='0000A819',method=dict(previous_observed_id=1,next_observed_id=2))
    d.finish(db);db.close();path=tmp_path/'diagnostic_log.json';suffix=path.read_bytes()[d._header_bytes:]
    for state in ('published','metadata_updated'):
        d.info['status']=state;d.rewrite_summary(tmp_path);result=json.loads(path.read_bytes())
        assert result['status']==state and result['experiment_output_name']=='Zażółć — myszy'
        assert [r['row'] for r in result['interpolations']]==list(range(6000))
        assert path.read_bytes()[d._header_bytes:]==suffix
    assert not path.with_suffix('.json.partial').exists()


def test_tunnel_distances_main_only_roundtrip_and_legacy_absence(tmp_path):
    path,cfg=make_pair(tmp_path)
    cfg['recording']['layout']['tunnels'][0].pop('length')
    cfg['recording']['layout']['tunnels'][0]['antenna_distances_cm']=[12.345]
    cfg['recording']['layout']['tunnels'][1].update(antennas=['C-1','D-1','E-1'],antenna_distances_cm=[10,None])
    save_config_bundle(path,cfg);main=json.loads(path.read_text());loaded=load_config_bundle(path)
    assert main['recording']['layout']['tunnels'][0]['length']==12.345
    assert main['recording']['layout']['tunnels'][1]['length'] is None
    assert loaded['recording']['layout']['tunnels'][1]['antenna_distances_cm']==[10,None]
    assert 'antenna_distances_cm' not in main['recording']['layout']['tunnels'][0]
    main['recording']['layout']['tunnels'][0]['length']=25
    path.write_text(json.dumps(main))
    loaded=load_config_bundle(path)
    assert loaded['recording']['layout']['tunnels'][0]['antenna_distances_cm']==[25]
    assert not [s for s in validate_layout(old.linear_layout()) if 'distance' in s]


@pytest.mark.parametrize('value', [[], [0], [-2], [True], ['12'], [float('nan')], [float('inf')], [1, 2], None])
def test_malformed_tunnel_measurements_cannot_be_saved(value, tmp_path):
    layout = old.linear_layout()
    layout['tunnels'][0]['antenna_distances_cm'] = value
    assert any('distance' in s for s in validate_layout(layout))
    with pytest.raises(ValueError, match='distance'):
        normalize_layout(layout)


def test_advanced_defaults_and_persisted_user_choices(gui):
    from deepecohab_recorder.ui.preprocessing_advanced import AdvancedOptions
    e = gui
    tab = e.prepare_inputs()
    dialog = AdvancedOptions(tab)
    assert dialog.split.isChecked()
    assert dialog.ignore_short_gaps.isChecked()
    assert dialog.gap_minutes.value() == 3
    dialog.split.setChecked(False)
    dialog.gap_minutes.setValue(0.5)
    dialog.ignore_short_gaps.setChecked(False)
    assert not dialog.gap_minutes.isEnabled()
    values, _ = dialog.values()
    assert values['split_restarts'] is True
    assert values['minimum_recording_gap_seconds'] == 30
    assert values['ignore_short_recording_gaps'] is False
    dialog.reject()
    cfg = copy.deepcopy(e.window.services.repo.data)
    cfg['acquisition'].setdefault('preprocessing', {})['options'] = values
    save_config_bundle(e.root / 'Test.config.json', cfg)
    loaded = load_config_bundle(e.root / 'Test.config.json')
    e.window.services.repo.replace(loaded)
    e.window._apply(loaded)
    e.window._sync_context()
    tab.configure_experiment(force=True)
    assert tab.options['split_restarts'] is True
    assert tab.options['minimum_recording_gap_seconds'] == 30
    assert tab.options['ignore_short_recording_gaps'] is False


def test_tunnel_dialog_commits_measurements_and_count_changes(gui):
    from PySide6.QtWidgets import QDialog, QSpinBox
    from deepecohab_recorder.ui.tunnel_distances import TunnelDistances
    e=gui;tab=e.window.widgets['layout'];tunnel=tab.graph['tunnels'][0]
    def accept(dialog):
        table=dialog.findChild(TunnelDistances)
        assert table.values()==[None]
        table.cellWidget(0,1).setValue(15.5)
        assert dialog.findChild(QSpinBox).maximum()==2
        return QDialog.Accepted
    with patch.object(QDialog,'exec',accept):tab.edit_tunnel(tunnel)
    assert tunnel['length']==15.5
    e.window.save_section(tab)
    assert json.loads((e.root/'Test.config.json').read_text())['recording']['layout']['tunnels'][0]['length']==15.5
    before=copy.deepcopy(tunnel)
    with patch.object(QDialog,'exec',return_value=QDialog.Rejected):tab.edit_tunnel(tunnel)
    assert tunnel==before and not e.warning.called
