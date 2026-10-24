"""v1.0.1: historical identity, asynchronous discovery and experiment isolation."""
import copy
import csv
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog

from deepecohab_recorder.config_bundle import load_config_bundle, save_config_bundle
from deepecohab_recorder.main_window import MainWindow
from deepecohab_recorder.preprocessing import readers
from deepecohab_recorder.preprocessing.discovery import discover
from deepecohab_recorder.preprocessing.layout_check import check_layout
from deepecohab_recorder.preprocessing.metadata_review import review_metadata, apply_metadata_override
from deepecohab_recorder.preprocessing.models import InputFile, Settings
from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
from tests.helpers import valid_config, settle_discovery
from tests.test_navigation import workspace, qt_wait


def legacy_config(name='Archive'):
    config = valid_config()
    config['recording']['name'] = name
    for i, tunnel in enumerate(config['recording']['layout']['tunnels']):
        tunnel['antennas'] = [f'{2*i+1}-1', f'{2*i+2}-1']
    from deepecohab_recorder.topology import normalize_layout
    config['recording']['layout'] = normalize_layout(config['recording']['layout'])
    return config


def raw(path, channel='P', second=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{channel}-1;1A63A81904;{second+1:X};2023-05-17T13:00:{second:02d}+02:00;1\n', encoding='utf-8')
    return path


def request(root, **updates):
    return dict(root=str(root), name='Archive', config=valid_config(), generation=1,
                revision=0, config_path='', manual_config=False, **updates)


@pytest.mark.parametrize('live', [False, True])
def test_selected_legacy_board_does_not_require_saved_sources_or_live_hardware(tmp_path, live):
    config = legacy_config()
    for board in config['acquisition']['boards'].values():
        board['active'] = live
        if not live:
            board['com_port'] = ''
    spec = InputFile(str(tmp_path / 'unused.txt'), board_id=1, profile='legacy_raw')
    settings = Settings([spec], str(tmp_path))
    review = review_metadata(config, settings)
    assert review == dict(issues=[], changes={}, incomplete_layout=False)
    apply_metadata_override(config, settings)
    assert settings.effective_interpolation_mode == 'standard'


def test_current_inputs_replace_stale_profiles_but_conflicts_within_job_stay_errors(tmp_path):
    config = legacy_config()
    config['acquisition']['preprocessing']['sources'] = [{'board_id': 1, 'profile': 'v1.5'}]
    old = InputFile(str(tmp_path / 'old.txt'), board_id=1, profile='legacy_raw')
    assert not any(check_layout(config, [old]).values())
    assert not review_metadata(config, Settings([old], str(tmp_path)))['issues']
    conflicting = InputFile(str(tmp_path / 'new.txt'), board_id=1, profile='v1.5')
    review = review_metadata(config, Settings([old, conflicting], str(tmp_path)))
    assert any('conflicting preprocessing hardware' in issue for issue in review['issues'])
    assert review['changes']['interpolation_mode'] == 'none'


def test_discovery_caches_unchanged_files_and_revises_automatic_profile(tmp_path, monkeypatch):
    folder = tmp_path / 'raw_antenna_reads' / 'board1'
    first = raw(folder / 'first.txt', 'A')
    config = request(tmp_path)
    cache = {}
    initial = discover(config, cache)['entries'][0]['source']
    assert initial['files'][0].get('profile', 'v1.0') == 'v1.0'
    opened = []
    original_open = Path.open

    def tracked_open(path, *args, **kwargs):
        if path.suffix == '.txt':
            opened.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', tracked_open)
    discover(config, cache)
    assert opened == []
    raw(folder / 'next.txt', 'P', 1)
    opened.clear()
    updated = discover(config, cache)['entries'][0]
    assert len(updated['source']['files']) == 2
    assert {s['profile'] for s in updated['source']['files']} == {'v1.5'}
    opened.clear()
    discover(config, cache)
    assert opened == []
    explicit = dict(config, assignments={str(folder): {'profile': 'v1.0'}}, profile_overrides=[str(folder)])
    assert discover(explicit, cache)['entries'][0]['invalid']
    first.unlink()
    rescanned = discover(dict(config, force=True), cache)['entries'][0]
    assert [Path(s['path']).name for s in rescanned['source']['files']] == ['next.txt']


def test_preview_is_bounded_but_processing_reader_still_reads_all_data(tmp_path):
    path = tmp_path / 'blank_prefix.txt'
    path.write_text('\n' * 5000 + '20230517_130000000\t1A63A81904\t01\n', encoding='utf-8')
    assert readers.detect(path, preview=True) == 'unknown'
    assert readers.detect(path) == 'legacy_raw'
    rows = list(readers.records(InputFile(str(path), profile='legacy_raw'), 'Europe/Warsaw'))
    assert len(rows) == 1 and rows[0][0].antenna == '1-1'
    huge = tmp_path / 'long_line.txt'
    huge.write_text('A' * 1_000_000, encoding='utf-8')
    assert readers.detect(huge, preview=True) == 'unknown'
    # An explicit user rescan can recover an inconclusive automatic preview;
    # the broader probe still runs entirely in the read-only worker.
    config = request(tmp_path, manual_paths=[str(path)])
    assert discover(config, {})['entries'][0]['source'] is None
    manual = discover(dict(config, force=True), {})['entries'][0]
    assert manual['source']['files'][0]['profile'] == 'legacy_raw'


def test_actual_rescan_button_recognises_added_v15_file(workspace):
    app, window, root = workspace
    directory = Path(window.services.context.save_directory) / 'raw_antenna_reads' / 'board1'
    raw(directory / 'first.txt', 'A')
    tab = window.widgets['preprocessing']
    tab.scan_button.click()
    settle_discovery(tab)
    assert len(tab.input_specs()) == 1
    raw(directory / 'next.txt', 'P', 1)
    tab.scan_button.click()
    settle_discovery(tab)
    assert len(tab.input_specs()) == 2
    assert {s.profile for s in tab.input_specs()} == {'v1.5'}
    tab.table.item(0, 4).setText('custom-clock')
    settle_discovery(tab)
    assert {s.group for s in tab.input_specs()} == {'custom-clock'}


def test_discovery_does_not_block_gui_timers_and_old_results_cannot_change_context(workspace, monkeypatch):
    app, window, root = workspace
    tab = window.widgets['preprocessing']
    settle_discovery(tab)
    tab.discovery.reset()
    folder = Path(window.services.context.save_directory) / 'raw_antenna_reads' / 'many-files'
    for number in range(256):
        raw(folder / f'{number:04d}.txt')
    original_open = Path.open

    def no_gui_raw_reads(path, *args, **kwargs):
        if path.is_relative_to(folder):
            raise AssertionError('Raw file preview ran in the GUI process.')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', no_gui_raw_reads)
    import deepecohab_recorder.services.discovery as service
    from deepecohab_recorder.runtime_paths import worker_python
    code = "import time,runpy; time.sleep(.4); runpy.run_module('deepecohab_recorder.preprocessing.discovery',run_name='__main__')"

    def delayed(process, module, args, directory):
        process.start(worker_python(), ['-c', code])

    monkeypatch.setattr(service, 'start_module', delayed)
    beats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(1))
    timer.start()
    try:
        tab.scan_directory()
        settle_discovery(tab)
        assert len(beats) >= 15
        assert len(tab.input_specs()) == 256
        old = tab._generation
        tab.load(valid_config())
        tab._discovery_completed(dict(generation=old, revision=0))
        assert tab._generation != old
        assert tab._sources == []
    finally:
        timer.stop()


def test_existing_source_recovers_after_live_file_is_released(workspace):
    from types import SimpleNamespace
    app, window, root = workspace
    path = raw(Path(window.services.context.save_directory) / 'raw_antenna_reads' / 'board1' / 'raw.txt')
    tab = window.widgets['preprocessing']
    tab.scan_directory(only_changed=True)
    settle_discovery(tab)
    running = [True]
    window.services.controllers['board1'] = SimpleNamespace(data_file=str(path), is_running=lambda: running[0])
    try:
        tab.scan_directory(only_changed=True)
        settle_discovery(tab)
        assert tab._invalid_sources and not tab.start_button.isEnabled()
        with pytest.raises(ValueError, match='unavailable or conflicting'):
            tab.input_specs()
        running[0] = False
        tab.scan_directory(only_changed=True)
        settle_discovery(tab)
        assert not tab._invalid_sources and tab.start_button.isEnabled()
        assert len(tab.input_specs()) == 1
    finally:
        window.services.controllers.pop('board1')


def test_matching_populates_both_fields_without_mode_or_board_selectors(workspace):
    app, window, root = workspace
    dialog = AntennaAssignmentDialog(window.ui, '1-1', window)
    assert not hasattr(dialog, 'mode') and not hasattr(dialog, 'board')
    assert dialog.value() == '1-1' and dialog.match.isEnabled()
    assert dialog.board_number.isEnabled()
    matcher = type('Matcher', (), dict(selected=('TAG', 'P-2', 2), exec=lambda self: QDialog.Accepted))()
    with patch('deepecohab_recorder.ui.antenna_assignment.MatchDialog', return_value=matcher):
        dialog.match_antenna()
    assert dialog.value() == 'P-2'
    dialog.board_number.setValue(9)
    assert dialog.value() == 'P-9'
    dialog.close()


def experiment(root, name, tag):
    directory = root / name
    config = legacy_config(name)
    config['recording']['project_name'] = 'Project ' + name
    config['recording']['cohort']['animals'][0]['tag'] = tag
    config['acquisition']['preprocessing'] = dict(sources=[], options=dict(
        interpolate=False, interpolation_mode='none', window_ms=123 if name == 'A' else 456),
        formats=dict(parquet=False, csv=True))
    path = directory / (name + '.config.json')
    save_config_bundle(path, config)
    raw_path = directory / 'raw_antenna_reads' / 'board1' / 'hour.txt'
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(f'20230517_130000000\t{tag}\t01\n20230517_130000100\t{tag}\t01\n', encoding='utf-8')
    return path


def load(window, path):
    with patch.object(window, '_choose_config', return_value=path):
        window.load_all()


def process(app, tab):
    settle_discovery(tab)
    tab.start()
    qt_wait(app, lambda: not tab.background_busy(), timeout=20)
    assert tab._job_result is not None, tab.log.toPlainText()
    assert tab._job_result['kind'] == 'completed', tab.log.toPlainText()
    path = Path(tab._job_result['directory']) / next(e['file'] for e in tab._job_result['outputs'] if e['file'].endswith('.csv'))
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def test_loading_b_after_a_matches_a_fresh_b_scientific_result(workspace):
    app, window, root = workspace
    first, second = experiment(root, 'A', 'AAAAAAAAAA'), experiment(root, 'B', 'BBBBBBBBBB')
    load(window, first)
    tab = window.widgets['preprocessing']
    settle_discovery(tab)
    tab.options['aliases'] = {'A-only': 'P-2'}
    tab.options['routes'] = {'A-only': 'A-device'}
    tab._job_result = dict(kind='completed', directory=str(first.parent))
    tab._output_directory = str(first.parent)
    tab.open_button.setEnabled(True)
    tab.log.appendPlainText('ONLY EXPERIMENT A')
    load(window, second)
    # Assert before returning to Qt; the reset must not wait for a timer.
    assert window.services.context.experiment_name == 'B'
    assert tab.config_snapshot['recording']['name'] == 'B'
    assert tab._sources == [] and tab._job_result is None
    assert tab._output_directory == '' and not tab.open_button.isEnabled()
    assert 'ONLY EXPERIMENT A' not in tab.log.toPlainText()
    assert tab.options['aliases'] == {} and tab.options['routes'] == {}
    assert tab.options['window_ms'] == 456
    with patch.object(tab, 'error') as error, patch('deepecohab_recorder.tabs.data_preprocessing.start_preprocessing') as start:
        tab.start()
        error.assert_called_once()
        start.assert_not_called()
    sequential = process(app, tab)
    assert {row['animal_id'] for row in sequential} == {'BBBBBBBBBB'}
    after_sequential = load_config_bundle(second)['recording']
    fresh = MainWindow()
    fresh.timer.stop()
    try:
        load(fresh, second)
        direct = process(app, fresh.widgets['preprocessing'])
        assert sequential == direct
        assert load_config_bundle(second)['recording'] == after_sequential
    finally:
        fresh.close()
        fresh.deleteLater()
        app.processEvents()
