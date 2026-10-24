"""Four live DAQs and animal-entry controls retain scientific file contracts."""
import copy
import csv
import json
from pathlib import Path
import queue
import sys
from types import SimpleNamespace

import jsonschema
import pytest
from PySide6.QtCore import QPersistentModelIndex, Qt
from PySide6.QtTest import QTest

from deepecohab_recorder.config_bundle import load_config_bundle, project, save_config_bundle
from deepecohab_recorder.controllers.rfid import RFIDBoardController
from deepecohab_recorder.live_boards import RFID_BOARD_IDS, visible_board_count
from deepecohab_recorder.models import BoardConfig, DAQ_V15, NEW_DAQ, OLD_DAQ
from deepecohab_recorder.schema import default_config, migrate_config
from deepecohab_recorder.services.application import ApplicationServices
from deepecohab_recorder.services.matching import MatchSession
from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
from deepecohab_recorder.ui.theme import manager
from tests.helpers import SerialFactory, valid_config, wait_for
from tests.test_navigation import workspace, settle


def value(table, row, col, role=Qt.DisplayRole):
    return table.model().data(table.model().index(row, col), role)


def test_default_and_legacy_panel_counts_do_not_limit_archived_board_ids():
    cfg = default_config()
    assert visible_board_count(cfg['acquisition']) == 1
    assert [b['board_id'] for b in cfg['acquisition']['boards'].values() if b['active']] == [1]
    old = valid_config()
    old['acquisition']['ui'].pop('rfid_board_count', None)
    assert visible_board_count(migrate_config(old)['acquisition']) == 2
    old['acquisition']['boards']['archive'] = dict(board_id=999, active=False, hardware=OLD_DAQ)
    assert visible_board_count(migrate_config(old)['acquisition']) == 2
    for i in (3, 4):
        old['acquisition']['boards'][f'board_{i}'].update(active=True)
        assert visible_board_count(migrate_config(old)['acquisition']) == i


def test_gui_state_changes_create_no_extra_files_and_leave_canonical_config_identical(tmp_path):
    cfg = valid_config()
    path = tmp_path / 'Test.config.json'
    before, _ = project(copy.deepcopy(cfg), path)
    cfg['acquisition']['ui']['rfid_board_count'] = 4
    cfg['acquisition']['boards']['board_4'].update(com_port='COM99', hardware=DAQ_V15)
    cfg['acquisition']['boards']['board_4']['future_driver_setting'] = 'preserve me'
    save_config_bundle(path, cfg)
    main = json.loads(path.read_text())
    assert main == before
    files = {p.name for p in tmp_path.rglob('*') if p.is_file()}
    assert files == {'Test.config.json', 'Test.acquisition_gui_state.json'}
    restored = load_config_bundle(path)
    assert restored['acquisition']['ui']['rfid_board_count'] == 4
    assert restored['acquisition']['boards']['board_4']['future_driver_setting'] == 'preserve me'
    for i in RFID_BOARD_IDS:
        cfg['acquisition']['boards'][f'board_{i}']['active'] = True
    main, state = project(cfg, path)
    assert main['acquisition']['active_inputs']['rfid_boards'] == [1, 2, 3, 4]
    for document, schema in ((main, 'config_v8.schema.json'), (state, 'acquisition_gui_state_v1.schema.json')):
        jsonschema.validate(document, json.loads((Path(__file__).parents[1] / 'docs' / schema).read_text()))


def test_board_grid_hides_only_inactive_devices_and_preserves_saved_settings(workspace, monkeypatch):
    app, window, root = workspace
    tab = window.widgets['acquisition']
    tab.load(default_config())
    assert tab.board_count.value() == 1
    assert [i for i, p in tab.panels.items() if not p.isHidden()] == [1]
    for count in (2, 3, 4):
        tab.board_count.setValue(count)
        settle(app)
        assert not window.services.busy()
        for i in range(1, count + 1):
            item = next(j for j in range(tab.grid.count()) if tab.grid.itemAt(j).widget() is tab.panels[i])
            assert tab.grid.getItemPosition(item)[:2] == divmod(i - 1, 2)
            assert window.widgets['launch'].selected[f'board{i}'].isVisible() is False  # Other page.
            assert not window.widgets['launch'].selected[f'board{i}'].isHidden()
    tab.panels[4].hardware_combo.setCurrentText(DAQ_V15)
    tab.panels[4].com_combo.setCurrentText('COM44')
    tab._loaded_boards['board_4']['future_setting'] = 9
    tab.board_count.setValue(1)
    assert not tab.read()['acquisition.boards']['board_4']['active']
    assert not window.widgets['launch'].read()['acquisition.launch']['board4']
    tab.board_count.setValue(4)
    assert tab.panels[4].com_combo.currentText() == 'COM44'
    assert tab.read()['acquisition.boards']['board_4']['future_setting'] == 9
    window.services.ports.acquire('COM44', 'tag matching')
    try:
        tab.refresh()
        assert not tab.board_count.isEnabled()
        tab.board_count.setValue(1)
        assert tab.board_count.value() == 4
    finally:
        window.services.ports.release('COM44', 'tag matching')
    calls = []
    monkeypatch.setattr('deepecohab_recorder.tabs.acquisition.list_serial_ports', lambda: calls.append(1) or ['COM44'])
    tab.refresh_ports()
    assert calls == [1]
    tab.load(default_config('Second'))
    assert tab.board_count.value() == 1 and tab.panels[4].com_combo.currentText() == ''
    assert 'future_setting' not in tab.read()['acquisition.boards']['board_4']
    assert window.header.actions_button.text() == 'Other options'


def test_four_simultaneous_profiles_keep_board_identity_and_matching(workspace, monkeypatch):
    app, window, root = workspace
    tab = window.widgets['acquisition']
    tab.board_count.setValue(4)
    profiles = (DAQ_V15, NEW_DAQ, OLD_DAQ, DAQ_V15)
    tag = '1A63A81904'
    streams = [f'A;{tag};01\n10;01;02\n'.encode(),
               f'0;{tag};01\n10;01000000;02\n'.encode(),
               f'0-0000000001-{tag}\n'.encode(),
               f'P;{tag};01\n10;04;02\n'.encode()]
    factory = SerialFactory(streams)
    monkeypatch.setitem(sys.modules, 'serial', factory)
    for i, profile in enumerate(profiles, 1):
        tab.panels[i].hardware_combo.setCurrentText(profile)
        tab.panels[i].com_combo.setCurrentText(f'COM{i+4}')
        tab.panels[i].active_check.setChecked(True)
    window.save_all()
    window.start_devices(['board1', 'board2', 'board3', 'board4'])
    window.services._future.result(15)
    controllers = [window.services.controllers[f'board{i}'] for i in RFID_BOARD_IDS]
    wait_for(lambda: all(c.get_tag_counts_snapshot().get(tag) == 1 for c in controllers), 5)
    assert all(c.is_running() for c in controllers)
    assert len(window.services.ports.snapshot()) == 4
    assert window.services.repo.data['acquisition']['com_ports']['COM8'] == 'RFID_DAQ_4'
    assert controllers[0].get_ttl_counts_snapshot()['TTL1'] == 1
    assert controllers[1].get_ttl_counts_snapshot()['TTL1'] == 1
    assert controllers[2].get_ttl_counts_snapshot() == {}
    assert controllers[3].get_ttl_counts_snapshot()['TTL4'] == 1
    tab.refresh()
    assert not tab.board_count.isEnabled()
    tab.board_count.setValue(1)
    assert tab.board_count.value() == 4
    matcher = MatchSession(window.services, window.hardware_settings()['boards'])
    matcher.start()
    try:
        wait_for(lambda: matcher.is_running())
        factory.instances[3].buffer.extend(b'P;ABCDEF0123;03\n')
        found = matcher.results.get(timeout=5)
        assert found == ('ABCDEF0123', 'P-4', 4)
        assert controllers[3].is_running()
    finally:
        matcher.cancel.set()
        matcher.thread.join(5)
    window.stop_devices()
    window.services._future.result(15)
    assert not window.services.busy() and not window.services.ports.snapshot()
    files = [Path(c.data_file) for c in controllers]
    assert len(set(files)) == 4 and all(p.is_file() for p in files)
    for i, (path, channel) in enumerate(zip(files, ('A', '0', '1', 'P')), 1):
        lines = path.read_text().splitlines()
        assert any(line.startswith(f'{channel}-{i};{tag};') and line.endswith(f';{i}') for line in lines)
        assert all(len(line.split(';')) == 5 for line in lines)
    assert set(window.services.run.devices) == {'board1', 'board2', 'board3', 'board4'}


def test_four_board_duplicate_com_is_rejected_before_any_stream_starts(tmp_path):
    service = ApplicationServices()
    try:
        service.set_context('Test', str(tmp_path), 'Europe/Warsaw')
        cfg = valid_config()
        for i in RFID_BOARD_IDS:
            cfg['acquisition']['boards'][f'board_{i}'].update(active=True, com_port='COM5')
        with pytest.raises(ValueError, match='more than once'):
            service.start_devices(['board1', 'board2', 'board3', 'board4'], cfg['acquisition'])
        assert not service.controllers and not service.busy() and not service.ports.snapshot()
    finally:
        service.shutdown()


@pytest.mark.parametrize('profile,line,channel', [(NEW_DAQ, b'10;01000000;01', 'TTL1'),
                                               (DAQ_V15, b'10;04;01', 'TTL4')])
def test_ttl_counts_follow_parsed_records_without_changing_event_rows(profile, line, channel):
    controller = RFIDBoardController(BoardConfig(4, True, profile, 'COM8'))
    kind, text = controller._consume(line, '2026-10-23T12:00:00+02:00', 1)
    assert kind == 'event' and text.startswith(f'{channel}-4;') and text.endswith(';4\n')
    assert controller.get_ttl_counts_snapshot()[channel] == 1
    assert controller.get_tag_counts_snapshot() == {}
    for _ in range(3):
        controller.get_status_snapshot()
    assert controller.get_ttl_counts_snapshot()[channel] == 1
    with pytest.raises(ValueError):
        controller._consume(b'10;BAD;01', 'unused', 2)
    assert controller.get_ttl_counts_snapshot()[channel] == 1
    controller.reconfigure(BoardConfig(4, True, profile, 'COM8'))
    assert not any(controller.get_ttl_counts_snapshot().values())


def test_tag_colours_follow_unsaved_animal_tags_and_themes_without_filtering(workspace):
    app, window, root = workspace
    panel = window.widgets['acquisition'].panels[1]
    tags = {'1A63A81904': 19, 'FFFFFFFFFF': 4}
    controller = SimpleNamespace(status_text=lambda: 'Stopped', get_status_snapshot=lambda: {},
                                 get_tag_counts_snapshot=lambda: dict(tags),
                                 get_ttl_counts_snapshot=lambda: {'TTL1': 7, 'TTL2': 0, 'TTL3': 0, 'TTL4': 0})
    panel.update_from_controller(controller)
    assert value(panel.tag_table, 0, 0) == '1A63A81904'
    assert value(panel.tag_table, 0, 0, Qt.ForegroundRole) is None
    muted = value(panel.tag_table, 1, 0, Qt.ForegroundRole)
    assert muted is not None and 'not listed' in value(panel.tag_table, 1, 1, Qt.ToolTipRole)
    for theme in ('dark', 'light'):
        manager().set_mode(theme, persist=False)
        assert value(panel.tag_table, 1, 0, Qt.ForegroundRole) is not None
    animals = window.widgets['animals']
    animals.count.setValue(2)
    animals.table.item(1, 1).setText(' ffffffffff ')
    assert value(panel.tag_table, 1, 0, Qt.ForegroundRole) is None
    assert panel.tag_table.totals.counts == tags
    assert value(panel.ttl_table, 0, 1) == '7'
    # Reordering counters retains a selected tag, instead of selecting another mouse.
    current = QPersistentModelIndex(panel.tag_table.model().index(0, 0))
    panel.tag_table.totals.set_counts(dict(tags, FFFFFFFFFF=30))
    assert current.data() == '1A63A81904' and current.row() == 1
    panel.reset_experiment_display()
    assert panel.tag_table.model().rowCount() == 0
    assert all(count == 0 for count in panel.ttl_table.totals.counts.values())


def test_sharing_checkbox_follows_its_column_and_first_row_is_authoritative(workspace):
    app, window, root = workspace
    window.pages.set_current_key('animals')
    tab = window.widgets['animals']
    tab.count.setValue(3)
    tab.table.item(0, 4).setText('WT')
    tab.table.item(1, 4).setText('KO')
    tab.same['genotype'].setChecked(True)
    assert [tab.table.item(row, 4).text() for row in range(3)] == ['WT'] * 3
    assert not tab.table.item(1, 4).flags() & Qt.ItemIsEditable
    tab.table.item(0, 4).setText('HET')
    tab.table.item(2, 4).setText('must not propagate')
    assert [tab.table.item(row, 4).text() for row in range(3)] == ['HET'] * 3
    tab.count.setValue(4)
    assert tab.table.item(3, 4).text() == 'HET'
    header = tab.table.horizontalHeader()
    header.resizeSection(4, 230)
    tab.table.horizontalScrollBar().setValue(270)
    settle(app)
    box = tab.same['genotype']
    assert abs(box.geometry().center().x() - (header.sectionViewportPosition(4) + header.sectionSize(4) // 2)) <= 1
    header.setSectionsMovable(True)
    header.moveSection(header.visualIndex(4), 2)
    settle(app)
    assert abs(box.geometry().center().x() - (header.sectionViewportPosition(4) + header.sectionSize(4) // 2)) <= 1
    tab.same['genotype'].setChecked(False)
    tab.table.item(1, 4).setText('KO')
    assert tab.table.item(0, 4).text() == 'HET' and tab.table.item(1, 4).text() == 'KO'
    tab.load(valid_config())
    assert not tab.same['genotype'].isChecked()


def test_row_matching_targets_clicked_mouse_and_cancel_or_load_cannot_overwrite(workspace, monkeypatch):
    app, window, root = workspace
    window.pages.set_current_key('animals')
    tab = window.widgets['animals']
    tab.count.setValue(5)
    tab.table.setCurrentCell(0, 1)

    class Match:
        selected = ('ABCDEF0123', 'P-4', 4)
        accepted = True
        action = None

        def __init__(self, *args):
            pass

        def setWindowTitle(self, title):
            assert 'mouse' in title

        def exec(self):
            if self.action:
                self.action()
            return self.accepted

    monkeypatch.setattr('deepecohab_recorder.tabs.animals.MatchDialog', Match)
    monkeypatch.setitem(window.ui._actions, 'prepare_matching', lambda: True)
    before = tab.table.item(0, 1).text()
    QTest.mouseClick(tab.row_actions.buttons[4], Qt.LeftButton)
    assert tab.table.item(4, 1).text() == 'ABCDEF0123'
    assert tab.table.item(0, 1).text() == before
    Match.accepted = False
    tab.row_actions.buttons[1].click()
    assert tab.table.item(1, 1).text() == ''
    Match.accepted = True
    Match.action = lambda self: tab.load(valid_config())
    tab.match_row(0)
    assert tab.table.item(0, 1).text() == before


def test_compact_antenna_dialog_expands_and_contracts_without_changing_assignment(workspace):
    app, window, root = workspace
    dialog = AntennaAssignmentDialog(window.ui, 'P-4', window)
    try:
        dialog.show()
        settle(app)
        initial = dialog.height()
        assert dialog.value() == 'P-4' and initial < 500
        dialog.role.setCurrentIndex(1)
        settle(app)
        assert dialog.height() > initial
        assert dialog.height() <= dialog.screen().availableGeometry().height()
        dialog.role.setCurrentIndex(0)
        settle(app)
        assert dialog.height() == initial and dialog.value() == 'P-4'
        dialog.board_number.setValue(999)
        assert dialog.value() == 'P-999'
    finally:
        dialog.close()


def test_four_board_processing_uses_existing_output_columns_and_diagnostics():
    from tests.test_preprocessing import PreprocessingTests, TAG
    case = PreprocessingTests()
    case.setUp()
    try:
        for i in RFID_BOARD_IDS:
            case.cfg['acquisition']['boards'][f'board_{i}'].update(hardware=DAQ_V15)
        case.cfg['recording']['layout']['tunnels'][0]['antennas'] = ['A-1', 'A-2']
        case.cfg['recording']['layout']['tunnels'][1]['antennas'] = ['A-3', 'A-4']
        inputs = [case.raw(f'board{i}.txt', [(i * 1000, 'A', TAG), (i * 1000 + 100, 'A', TAG)],
                           board=i, port=f'COM{i+4}') for i in RFID_BOARD_IDS]
        case.process(inputs, parquet=True, csv=True, interpolate=False)
        names = set()
        for output in case.result['outputs']:
            if output['file'].endswith('.csv'):
                with (case.output / output['file']).open(newline='') as handle:
                    reader = csv.DictReader(handle)
                    assert reader.fieldnames[-2:] == ['internal_board_timestamp', 'inserted']
                    names.update(row['antenna'] for row in reader)
        assert names == {'A-1', 'A-2', 'A-3', 'A-4'}
        schema = json.loads((Path(__file__).parents[1] / 'docs/diagnostic_v12.schema.json').read_text())
        jsonschema.validate(case.summary, schema)
    finally:
        case.tearDown()
