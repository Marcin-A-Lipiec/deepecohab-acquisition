from tests.helpers import settle_discovery
from deepecohab_recorder.config_bundle import load_config_bundle
"""Legacy readers, folder continuity, virtual identities and Setup integration."""
import copy
import csv
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from tests import test_preprocessing as old
from tests import test_metadata_and_gui as gui
from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.preprocessing.models import InputFile, Settings, parse_pc
from deepecohab_recorder.preprocessing.readers import detect, records, scan
from deepecohab_recorder.preprocessing.sources import inspect_source, experiment_sources, serialize_source
from deepecohab_recorder.schema import validate_config, migrate_config
from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
from deepecohab_recorder.ui.preprocessing_advanced import AdvancedOptions

TAG = '1A63A81904'
BASE = datetime.fromisoformat('2023-05-17T13:00:00+02:00')


def legacy(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(f"{(BASE + timedelta(milliseconds=ms)).strftime('%Y%m%d_%H%M%S%f')[:-3]}\t{tag}\t{int(channel):02d}\n"
                            for ms, channel, tag in events), encoding='utf-8')
    return InputFile(str(path), 'COM6', profile='legacy_raw')


class LegacyTests(unittest.TestCase):
    def setUp(self):
        old.PreprocessingTests.setUp(self)
        self.cfg["recording"]["layout"] = {}
    tearDown = old.PreprocessingTests.tearDown
    process = old.PreprocessingTests.process

    def test_legacy_parser_and_pc_duration_independent_antennas(self):
        source = legacy(self.root / 'raw.txt', [(0, 1, TAG), (100, 2, TAG), (200, 1, TAG), (500, 1, TAG)])
        self.assertEqual(detect(source.path), 'legacy_raw')
        raw = list(records(source, 'Europe/Warsaw'))
        self.assertEqual(raw[0][0].antenna, '1-1')
        self.assertEqual(raw[0][0].pc_us, parse_pc(BASE.isoformat(), 'Europe/Warsaw'))
        self.assertIsNone(raw[0][0].ticks)
        rows = self.process([source], whitelist=False, parquet=True)
        self.assertEqual([(r['antenna'], int(r['time_spent'])) for r in rows], [('1-1', 200), ('2-1', 10), ('1-1', 10)])
        self.assertTrue(all(r['internal_board_timestamp'] == '' for r in rows))
        import pyarrow.parquet as pq
        table = pq.read_table(next(self.output.glob('*.parquet')))
        self.assertEqual(table.column('internal_board_timestamp').null_count, 3)
        self.assertEqual(table.schema.field('datetime').type.tz, 'Europe/Warsaw')
        self.assertIsNone(self.summary['clocks'][0]['tick_ms'])

    def test_folder_members_stitch_and_two_boards_keep_distinct_identities(self):
        a = self.root / 'raw_antenna_reads' / 'old_board_A'
        b = self.root / 'raw_antenna_reads' / 'old_board_B'
        legacy(a / 'z_first.txt', [(3599900, 1, TAG)])
        legacy(a / 'nested' / 'a_next.txt', [(3600050, 1, TAG)])
        legacy(b / 'hour.txt', [(3600000, 1, TAG)])
        inputs = []
        for board, folder in enumerate((a, b), 1):
            source, notes = inspect_source(folder, self.cfg, 'Europe/Warsaw')
            self.assertFalse(notes)
            self.assertFalse(source.prototype.port)
            inputs += source.expand(replace(source.prototype, port=f'COM{board+5}', board_id=board))
        rows = self.process(inputs, whitelist=False)
        self.assertEqual(len(self.result['outputs']), 1)
        self.assertEqual([(r['antenna'], int(r['time_spent'])) for r in rows], [('1-1', 150), ('1-2', 10)])
        self.assertEqual(self.summary['recording_boundaries'], [])
        self.assertEqual(len(self.summary['recordings']), 2)
        self.assertEqual([r['file_count'] for r in self.summary['recordings']], [2,1])
        self.assertNotIn('routine_file_boundaries', self.summary)
        self.assertEqual(len(experiment_sources(self.root / 'raw_antenna_reads')), 2)

    def test_hour_boundary_interpolation_uses_pc_clock_and_restart_blocks_it(self):
        events = [(ms, 'ABCD'.index(ant) + 1, TAG) for ms, ant, _ in old.PreprocessingTests.training_events(self)]
        folder = self.root / 'board'
        first = legacy(folder / 'hour1.txt', events[:-2])
        second = legacy(folder / 'hour2.txt', events[-2:])
        self.cfg['recording']['layout'] = json.loads(json.dumps(old.linear_layout()).replace('A-1', '1-1').replace('B-1', '2-1').replace('C-1', '3-1').replace('D-1', '4-1'))
        source, _ = inspect_source(folder, self.cfg, 'Europe/Warsaw')
        inputs = source.expand(replace(source.prototype, port='COM6'))
        rows = self.process(inputs, whitelist=False, interpolate=True)
        inserted, = self.inserted
        self.assertEqual(inserted['antenna'], '2-1')
        self.assertEqual(rows[inserted['row_number']]['datetime'], '2023-05-17T13:00:03.150000+02:00')
        self.assertTrue(all(r['internal_board_timestamp'] == '' for r in rows))
        self.assertEqual([r['datetime'] for r in rows], sorted(r['datetime'] for r in rows))
        # The same observations separated into explicit recordings must not infer
        # a transit across the unrecorded boundary, even when output is merged.
        first.part, second.part = 1, 2
        first.rotation_group, second.rotation_group = 'run1', 'run2'
        self.process([first, second], whitelist=False, interpolate=True, split_restarts=False, ignore_short_recording_gaps=False)
        self.assertEqual(self.inserted, [])
        self.assertEqual(len(self.summary['recording_boundaries']), 1)

    def test_loose_hourly_merge_can_be_disabled(self):
        a = legacy(self.root / 'a.txt', [(0, 1, TAG)])
        b = legacy(self.root / 'b.txt', [(100, 1, TAG)])
        self.process([copy.copy(a), copy.copy(b)], whitelist=False, split_restarts=True)
        self.assertEqual(len(self.result['outputs']), 2)
        rows = self.process([a, b], whitelist=False, split_restarts=False, flag_missed_data_periods=False)
        self.assertEqual(len(self.result['outputs']), 1)
        self.assertEqual([r['time_spent'] for r in rows], ['100'])
        self.assertNotIn('routine_file_boundaries', self.summary)
        self.assertEqual(self.summary['recording_boundaries'], [])

    def test_legacy_dst_ambiguity_stops_instead_of_dropping_an_hour(self):
        spec = legacy(self.root / 'dst.txt', [(0, 1, TAG)])
        Path(spec.path).write_text(f'20251026_023000000\t{TAG}\t01\n')
        with self.assertRaisesRegex(ValueError, 'Ambiguous or nonexistent'):
            list(records(spec, 'Europe/Warsaw'))

    def test_preprocessed_rejected_in_scan_and_worker_even_if_profile_overridden(self):
        bad = self.root / 'old_output.txt'
        shutil.copyfile(old.FIXTURES / 'user_compressed_sample.txt', bad)
        good = legacy(self.root / 'source' / 'raw.txt', [(0, 1, TAG)])
        shutil.copyfile(bad, self.root / 'source' / 'preprocessed.txt')
        notices = []
        self.assertEqual(scan([str(bad)], self.cfg, 'Europe/Warsaw', notices=notices), [])
        self.assertEqual(notices[0]['format'], 'compressed')
        source, notices = inspect_source(Path(good.path).parent, self.cfg, 'Europe/Warsaw')
        self.assertEqual(len(source.files), 1)
        self.assertEqual(notices[0]['format'], 'compressed')
        with self.assertRaisesRegex(ValueError, 'previously preprocessed legacy'):
            list(records(InputFile(str(bad), 'COM6'), 'Europe/Warsaw'))

    def test_v15_folder_can_infer_from_sibling_and_recorded_identity_is_preserved(self):
        folder = self.root / 'letters'
        folder.mkdir()
        for channel, second in (('A', 0), ('P', 1)):
            (folder / f'{channel}.txt').write_text(f'{channel}COM5;0000A819;{second+1:X};2023-05-17T13:00:0{second}+02:00;1\n')
        source, notes = inspect_source(folder, self.cfg, 'Europe/Warsaw')
        self.assertFalse(notes)
        self.assertEqual(source.prototype.profile, 'v1.5')
        self.assertEqual(source.channels, {'A', 'P'})
        with self.assertRaisesRegex(ValueError, 'recorded COM'):
            source.expand(replace(source.prototype, port='COM6'))
        (folder / 'other.txt').write_text('P-2;0000A819;1;2023-05-17T13:00:02+02:00;2\n')
        source, notes = inspect_source(folder, self.cfg, 'Europe/Warsaw')
        self.assertIsNone(source)
        self.assertEqual(notes[-1]['format'], 'conflicting_source')

    def test_portable_assignments_and_replacing_removed_options(self):
        folder = self.root / 'raw_antenna_reads' / 'board1'
        legacy(folder / 'hour.txt', [(0, 1, TAG)])
        source, _ = inspect_source(folder, self.cfg, 'Europe/Warsaw')
        saved = serialize_source(source, replace(source.prototype, port='COM6'), self.root)
        self.assertEqual(saved['path'], 'raw_antenna_reads/board1')
        repo = ConfigRepository(lambda: 'Example', lambda: str(self.root))
        repo.update_paths({'acquisition.preprocessing': {'sources': [saved], 'options': {'routes': {'1-1': 'spouts'}}}})
        repo.update_paths({'acquisition.preprocessing': {'sources': [saved], 'options': {'routes': {}}}})
        self.assertEqual(repo.data['acquisition']['preprocessing']['options']['routes'], {})
        _, path = repo.save()
        loaded = load_config_bundle(path)
        self.assertEqual(loaded['acquisition']['preprocessing']['sources'][0], saved)
        moved = self.root / 'moved'
        shutil.copytree(folder, moved / saved['path'])
        other, _ = inspect_source(moved / saved['path'], loaded, 'Europe/Warsaw', saved=saved)
        self.assertEqual(other.prototype.port, 'COM6')


class SetupGuiTests(unittest.TestCase):
    setUpClass = classmethod(gui.Patch6GuiTests.setUpClass.__func__)
    setUp = gui.Patch6GuiTests.setUp
    tearDown = gui.Patch6GuiTests.tearDown

    def folders(self):
        for i in (1, 2):
            for h in (1, 2):
                legacy(self.root / 'raw_antenna_reads' / f'board{i}' / f'hour{h}.txt', [(h*1000, i, TAG)])
        tab = self.window.widgets['preprocessing']
        tab.scan_directory()
        settle_discovery(tab)
        for row in range(2):
            tab.table.item(row, 2).setText(f'COM{row+6}')
        return tab

    def test_setup_first_and_full_config_roundtrip(self):
        self.assertEqual(self.window.tabs.tabText(0), 'Setup')
        setup = self.window.widgets['setup']
        self.assertFalse(setup.instructions.isVisible())
        self.assertFalse(hasattr(self.window.widgets['acquisition'], 'experiment_edit'))
        self.assertFalse(hasattr(self.window.widgets['layout'], 'location'))
        self.assertFalse(hasattr(self.window.widgets['environment'], 'zone'))
        setup.location.setText('Archive room')
        setup.start.setCurrentIndex(setup.start.findData('light_phase'))
        self.window.save_all()
        setup.location.clear()
        with patch.object(self.window, '_choose_config', return_value=self.root / 'Test.config.json'):
            self.window.load_all()
        self.app.processEvents()
        self.assertEqual(setup.location.text(), 'Archive room')
        self.assertEqual(setup.start.currentData(), 'light_phase')
        self.assertEqual(self.window.widgets['animals'].read()['recording.cohort']['animals'][0]['tag'], TAG)
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_folder_one_row_clock_options_per_folder_and_real_worker(self):
        tab = self.folders()
        self.assertEqual(tab.table.rowCount(), 2)
        self.assertEqual(len(tab.input_specs()), 4)
        self.assertEqual({s.part for s in tab.input_specs()}, {1})
        self.assertEqual({s.board_id for s in tab.input_specs()}, {1, 2})
        advanced = AdvancedOptions(tab)
        self.assertEqual(advanced.clocks.rowCount(), 2)
        self.assertFalse(advanced.clocks.item(0, 1).flags() & Qt.ItemIsEditable)
        self.assertEqual(advanced.values()[1], [(None, 0), (None, 0)])
        advanced.close()
        tab.options['interpolate'] = False
        tab.csv.setChecked(True)
        # This fixture tests legacy source folders, so its saved layout must
        # refer to those sources rather than the default current-board COM5.
        for i, tunnel in enumerate(self.window.widgets['layout'].graph['tunnels']):
            tunnel['antennas'] = [f'{2*i+1}COM6', f'{2*i+2}COM6']
        self.window.save_all()
        self.app.processEvents()
        settle_discovery(tab)
        self.assertEqual({s.port for s in tab.input_specs()}, {'COM6', 'COM7'})
        self.assertTrue(tab.csv.isChecked())
        self.assertEqual(self.window.services.repo.data['acquisition']['preprocessing']['formats'], {'parquet': True, 'csv': True})
        tab.start()
        self.assertTrue(tab.process.waitForFinished(15000))
        self.app.processEvents()
        self.assertEqual(tab.process.exitCode(), 0, tab.log.toPlainText())
        self.assertEqual({o['file'] for o in tab._job_result['outputs']}, {'Test.data.csv', 'Test.data.parquet'})
        report = json.loads(Path(tab._job_result['diagnostic_path']).read_text())
        self.assertEqual(len(report['recordings']), 2)
        self.assertNotIn('routine_file_boundaries', report)
        self.assertEqual(report['recording_boundaries'], [])
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_manual_layout_without_hardware_and_live_validation_stays_strict(self):
        tab = self.folders()
        # A currently configured v1.5 port must not reinterpret an archived
        # numeric channel as its first letter channel when opening the editor.
        archived = AntennaAssignmentDialog(self.window.ui, '1-1', self.window)
        self.assertTrue(archived.board_number.isEnabled())
        self.assertTrue(archived.match.isEnabled())
        self.assertEqual(archived.value(), '1-1')
        archived.close()
        cfg = self.window.services.repo.data
        for board in cfg['acquisition']['boards'].values():
            board['active'], board['com_port'] = False, ''
        self.window.services.repo.replace(cfg)
        self.window.widgets['acquisition'].load(cfg)
        dialog = AntennaAssignmentDialog(self.window.ui, '1-1', self.window)
        self.assertTrue(dialog.board_number.isEnabled())
        self.assertFalse(dialog.match.isEnabled())
        dialog.channel.setCurrentText('02')
        dialog.board_number.setValue(2)
        self.assertEqual(dialog.value(), '2-2')
        dialog.close()
        graph = self.window.widgets['layout'].graph
        for i, tunnel in enumerate(graph['tunnels']):
            tunnel['antennas'] = [f'{2*i+1}COM6', f'{2*i+2}COM6']
        self.window.save_all()
        saved = self.window.services.repo.data
        self.assertEqual(validate_config(saved), [])
        self.assertTrue(any('no configured' in e for e in validate_config(saved, saved['acquisition']['boards'])))
        self.assertFalse(any(b['active'] for b in saved['acquisition']['boards'].values()))
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_directory_auto_load_restores_assignments_and_blank_directory_does_not(self):
        tab = self.folders()
        tab.options['routes'] = {'1-1': 'spout'}
        self.window.save_all()
        self.app.processEvents()
        saved_root = self.root
        empty = self.root / 'empty'
        empty.mkdir()
        setup = self.window.widgets['setup']
        setup.directory_edit.setText(str(empty))
        self.window.directory_changed()
        settle_discovery(self.window.widgets['preprocessing'])
        self.app.processEvents()
        self.assertEqual(tab.table.rowCount(), 0)
        self.assertEqual(tab.options['routes'], {})
        setup.directory_edit.setText(str(saved_root))
        self.window.directory_changed()
        settle_discovery(self.window.widgets['preprocessing'])
        self.app.processEvents()
        self.assertEqual(tab.table.rowCount(), 2)
        self.assertEqual({s.port for s in tab.input_specs()}, {'COM6', 'COM7'})
        self.assertEqual(tab.options['routes'], {'1-1': 'device_antenna1'})
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_rescan_and_rename_keep_unsaved_folder_assignments(self):
        tab = self.folders()
        legacy(self.root / 'raw_antenna_reads' / 'board1' / 'hour3.txt', [(3000, 1, TAG)])
        tab.scan_directory()
        settle_discovery(tab)
        self.assertEqual(len(tab.input_specs()), 5)
        self.assertEqual(tab.table.rowCount(), 2)
        self.assertEqual(tab.table.item(0, 2).text(), 'COM6')
        setup = self.window.widgets['setup']
        setup.experiment_edit.setText('Renamed')
        self.window.experiment_name_changed()
        self.assertEqual(len(tab.input_specs()), 5)
        self.assertEqual(tab.table.item(0, 2).text(), 'COM6')
        self.assertFalse(self.warning.called, self.warning.call_args)
