from tests.helpers import settle_discovery
"""Patch 11: schema contract, confirmed layout repair and experiment-wide times."""
from deepecohab_recorder.config_bundle import load_config_bundle
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox
import pyarrow as pa
import pyarrow.parquet as pq
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.experiment_timing import select_timing, normalize_timing
from deepecohab_recorder.metadata_updates import write_config_update, config_digest
from deepecohab_recorder.preprocessing.layout_check import check_layout, apply_layout_changes, antenna_labels
from deepecohab_recorder.preprocessing.models import InputFile, Settings, Cancelled
from deepecohab_recorder.preprocessing.pipeline import run
from deepecohab_recorder.schema import default_config, migrate_config
from deepecohab_recorder.services.session import RunSession
from tests import test_preprocessing as old
from tests import test_metadata_and_gui as gui
from tests.test_legacy_sources import legacy, TAG


def bare_layout():
    layout = json.loads(json.dumps(old.linear_layout()).replace('A-1', '1').replace('B-1', '2').replace('C-1', '3').replace('D-1', '4'))
    for n, cage in enumerate(layout['cages'], 1):
        cage['name'] = f'cage_{n}'
    return layout


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.config = default_config()
        self.config['recording']['layout'] = bare_layout()

    def test_single_board_repair_regenerates_cages_and_combinations(self):
        config = migrate_config(self.config)
        report = check_layout(config, [InputFile('unused', 'COM5', profile='legacy_raw')])
        self.assertEqual(report['changes'], {str(n): f'{n}-1' for n in range(1, 5)})
        updated = apply_layout_changes(config, report['changes'])
        layout = updated['recording']['layout']
        self.assertEqual(layout['cages'][1]['antennas'], ['2-1', '3-1'])
        self.assertEqual(layout['antenna_combinations']['1-1_2-1'], 'c1_c2')
        self.assertEqual(antenna_labels(layout), {f'{n}-1' for n in range(1, 5)})
        self.assertEqual(self.config['recording']['layout'], bare_layout())

    def test_two_boards_never_guess_even_if_only_one_detected_a_channel(self):
        inputs = [InputFile('a', 'COM5', profile='legacy_raw'), InputFile('b', 'COM6', 2, 'legacy_raw')]
        report = check_layout(self.config, inputs)
        self.assertFalse(report['changes'])
        self.assertEqual(report['ambiguous']['1'], ['1', '2'])

    def test_connected_board_does_not_silently_resolve_archived_labels(self):
        self.config['acquisition']['boards']['board_1']['com_port'] = 'COM88'
        layout = migrate_config(self.config)['recording']['layout']
        self.assertIn('1', antenna_labels(layout))
        self.assertNotIn('1COM88', antenna_labels(layout))

    def test_output_alias_must_keep_com_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            spec = legacy(Path(folder) / 'raw.txt', [(0, 1, TAG)])
            with self.assertRaisesRegex(ValueError, 'board suffix'):
                Settings([spec], folder, aliases={'1-1': 'lickometer'}).validate()


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = ConfigRepository(lambda: 'Example', lambda: str(self.root))
        self.run = RunSession(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def controller(self, start, stop='', error=''):
        return SimpleNamespace(clock=ExperimentClock('Europe/Warsaw'), started_iso=start, stopped_iso=stop,
            data_file=self.root / 'raw.csv', last_error=error, lines_ok=3, lines_written=3, lines_dropped=0, lines_bad=0)

    def test_two_boards_restarts_and_relaunch_keep_experiment_bounds(self):
        a = self.controller('2026-09-01T12:00:00+02:00', '2026-09-01T13:00:00+02:00')
        b = self.controller('2026-09-01T12:05:00+02:00', '2026-09-01T14:00:00+02:00')
        self.run.board_started('board1', a, {})
        self.run.board_started('board2', b, {})
        self.run.board_stopped('board1', a)
        self.run.board_stopped('board2', b)
        c = self.controller('2026-09-02T12:00:00+02:00', '2026-09-02T14:00:00+02:00')
        self.run.board_started('board1', c, {})
        self.run.board_stopped('board1', c)
        timeline = self.repo.data['recording']['timeline']
        self.assertEqual(timeline['start_datetime'], a.started_iso)
        self.assertEqual(timeline['end_datetime'], c.stopped_iso)
        self.assertEqual(self.run.start, c.started_iso)  # Run identity remains distinct.
        restarted = ConfigRepository(lambda: 'Example', lambda: str(self.root))
        restarted.import_file(self.repo.paths()[1])
        run = RunSession(restarted)
        d = self.controller('2026-09-03T12:00:00+02:00', '2026-09-03T14:00:00+02:00')
        run.board_started('board1', d, {})
        run.board_stopped('board1', d)
        self.assertEqual(restarted.data['recording']['timeline']['start_datetime'], a.started_iso)
        self.assertEqual(restarted.data['recording']['timeline']['end_datetime'], d.stopped_iso)

    def test_manual_values_survive_recording_and_return_to_automatic(self):
        self.run.save_metadata({'recording.timeline.datetime_mode': 'manual',
            'recording.timeline.manual_start_datetime': '2026-09-01T11:00:00+02:00',
            'recording.timeline.manual_end_datetime': '2026-09-01T15:00:00+02:00'})
        board = self.controller('2026-09-01T12:00:00+02:00', '2026-09-01T14:00:00+02:00')
        self.run.board_started('board1', board, {})
        self.run.board_stopped('board1', board)
        timeline = self.repo.data['recording']['timeline']
        self.assertEqual(timeline['start_datetime'], '2026-09-01T11:00:00+02:00')
        self.assertEqual(timeline['end_datetime'], '2026-09-01T15:00:00+02:00')
        self.run.save_metadata({'recording.timeline.datetime_mode': 'automatic'})
        timeline = self.repo.data['recording']['timeline']
        self.assertEqual(timeline['start_datetime'], board.started_iso)
        self.assertEqual(timeline['end_datetime'], board.stopped_iso)

    def test_unclean_stop_is_marked_for_recovery(self):
        board = self.controller('2026-09-01T12:00:00+02:00', '2026-09-01T14:00:00+02:00', 'USB lost')
        self.run.board_started('board1', board, {})
        self.run.board_stopped('board1', board)
        timeline = self.repo.data['recording']['timeline']
        self.assertIsNone(timeline['end_datetime'])
        self.assertTrue(timeline['datetime_provenance']['end_requires_recovery'])

    def test_manual_validation_and_config_write_conflict(self):
        timeline = default_config()['recording']['timeline']
        with self.assertRaisesRegex(ValueError, 'both a start'):
            select_timing(dict(timeline, datetime_mode='manual'), require_manual=True)
        with self.assertRaisesRegex(ValueError, 'before'):
            select_timing(dict(timeline, datetime_mode='manual', manual_start_datetime='2026-09-02T12:00:00+02:00', manual_end_datetime='2026-09-01T12:00:00+02:00'), require_manual=True)
        path = self.repo.save()[1]
        expected = config_digest(path)
        changed = self.repo.data
        changed['recording']['notes'] = 'Someone edited this file'
        path.write_text(json.dumps(changed))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed during'):
            write_config_update(path, self.repo.data, expected)
        self.assertEqual(path.read_bytes(), before)


class PipelineTests(unittest.TestCase):
    setUp = old.PreprocessingTests.setUp
    tearDown = old.PreprocessingTests.tearDown
    raw = old.PreprocessingTests.raw
    process = old.PreprocessingTests.process

    def test_parquet_schema_duration_enum_multiple_batches_and_null_ttl(self):
        events = [(i*500, 'A', old.TAG if i % 2 else old.OTHER) for i in range(5010)] + [(2505000, '10', '00000001')]
        rows = self.process([self.raw('schema.txt', events)], parquet=True)
        p = next(o for o in self.result['outputs'] if o['file'].endswith('.parquet') and o['group']=='rfid')
        file = pq.ParquetFile(self.output / p['file'])
        self.assertEqual(file.num_row_groups, 2)
        self.assertEqual(file.schema_arrow.names, ['datetime','antenna','time_under','animal_id','internal_board_timestamp','inserted'])
        self.assertEqual(file.schema_arrow.field('antenna').type, pa.dictionary(pa.uint32(), pa.string()))
        self.assertEqual(file.schema_arrow.field('time_under').type, pa.duration('us'))
        enum = file.schema_arrow.field('animal_id')
        self.assertTrue(pa.types.is_dictionary(enum.type))
        self.assertIn(b'_PL_ENUM_VALUES2', enum.metadata)
        data = file.read()
        self.assertEqual(data.column('time_under').cast(pa.int64()).to_pylist(), [10000]*5010)
        self.assertEqual(data.column('animal_id').to_pylist(), [r['animal_id'] for r in rows if r['antenna']=='A-1'])
        ttl_file = next(o for o in self.result['outputs'] if o['file'].endswith('.parquet') and o['group']=='TTL_data1')
        ttl = pq.read_table(self.output / ttl_file['file'])
        self.assertEqual(ttl.column('time_under').cast(pa.int64()).to_pylist(), [0])
        self.assertEqual(ttl.column('animal_id').null_count, 1)

    def test_missing_bounds_recovered_from_rfid_not_ttl_and_backup_kept(self):
        spec = self.raw('bounds.txt', [(0, '10', '00000001'),(1000,'A',old.TAG),(3000,'B',old.TAG),(8000,'10','00000001')])
        self.process([spec])
        stored = json.loads((self.root/'experiment.config.json').read_text())['recording']['timeline']
        self.assertEqual(datetime.fromisoformat(stored['start_datetime']),old.BASE+timedelta(seconds=1))
        self.assertEqual(datetime.fromisoformat(stored['end_datetime']),old.BASE+timedelta(seconds=3))
        self.assertFalse((self.root/'session_configs').exists())
        self.assertEqual(self.summary['experiment_timing']['status'],'updated')

    def test_normal_stop_preserved_and_manual_bounds_not_changed(self):
        timeline = self.cfg['recording']['timeline']
        timeline.update(start_datetime=(old.BASE-timedelta(seconds=5)).isoformat(),end_datetime=(old.BASE+timedelta(seconds=20)).isoformat())
        spec=self.raw('normal.txt',[(0,'A',old.TAG),(1000,'B',old.TAG)])
        self.process([spec])
        report=load_config_bundle(self.root/'experiment.config.json')['recording']['timeline']
        self.assertEqual(report['end_datetime'],timeline['end_datetime'])
        timeline.update(datetime_mode='manual',manual_start_datetime=(old.BASE-timedelta(hours=1)).isoformat(),manual_end_datetime=(old.BASE+timedelta(hours=1)).isoformat(),datetime_provenance={'end_requires_recovery':True})
        self.process([spec])
        report=load_config_bundle(self.root/'experiment.config.json')['recording']['timeline']
        self.assertEqual(report['start_datetime'],timeline['manual_start_datetime'])
        self.assertEqual(report['end_datetime'],timeline['manual_end_datetime'])
        self.assertEqual(datetime.fromisoformat(report['automatic_end_datetime']),old.BASE+timedelta(seconds=1))

    def test_power_loss_and_partial_selection_do_not_shrink_experiment(self):
        folder=self.root/'raw_antenna_reads';folder.mkdir()
        a=self.raw('raw_antenna_reads/a.txt',[(0,'A',old.TAG),(1000,'B',old.TAG)])
        b=self.raw('raw_antenna_reads/b.txt',[(10000,'A',old.TAG),(11000,'B',old.TAG)])
        self.cfg['recording']['timeline'].update(end_datetime=(old.BASE+timedelta(seconds=2)).isoformat(),datetime_provenance={'end_requires_recovery':True})
        self.process([a])
        self.assertEqual(self.summary['experiment_timing']['status'],'partial_selection_not_updated')
        self.process([a,b])
        self.assertEqual(datetime.fromisoformat(json.loads((self.root/'experiment.config.json').read_text())['recording']['timeline']['end_datetime']),old.BASE+timedelta(seconds=11))

    def test_worker_rejects_bare_layout_even_without_interpolation(self):
        self.cfg['recording']['layout']=bare_layout()
        spec=legacy(self.root/'legacy.txt',[(0,1,TAG)])
        with self.assertRaisesRegex(ValueError,'Layout antennas need matching board'):
            self.process([spec],whitelist=False)

    def test_journals_recover_first_start_from_old_run_local_config(self):
        first_start = (old.BASE-timedelta(days=1)).isoformat()
        last_stop = (old.BASE+timedelta(seconds=5)).isoformat()
        self.cfg['recording']['timeline'].update(start_datetime=old.BASE.isoformat(), end_datetime=last_stop)
        for name, start, stop in [('run_first',first_start,(old.BASE-timedelta(hours=1)).isoformat()),
                                  ('run_last',old.BASE.isoformat(),last_stop)]:
            root=self.root/'session_configs'/name;root.mkdir(parents=True)
            (root/'run.json').write_text(json.dumps({'devices':{'board1':{'start_datetime':start,'end_datetime':stop}}}))
        self.process([self.raw('journals.txt',[(0,'A',old.TAG),(1000,'B',old.TAG)])])
        timing=json.loads((self.root/'experiment.config.json').read_text())['recording']['timeline']
        self.assertEqual(timing['start_datetime'],first_start)
        self.assertEqual(timing['end_datetime'],last_stop)

    def test_ttl_only_output_has_null_categorical_and_does_not_invent_times(self):
        self.cfg['recording']['cohort']={'n_mice':0,'animals':[]}
        self.process([self.raw('ttl.txt',[(0,'10','00000001')])],parquet=True,whitelist=False)
        table=pq.read_table(next(self.output.glob('*.parquet')))
        self.assertEqual(table.column('animal_id').null_count,1)
        self.assertTrue(pa.types.is_dictionary(table.schema.field('animal_id').type))
        self.assertEqual(self.summary['experiment_timing']['status'],'no_RFID_detections')


class GuiTests(unittest.TestCase):
    setUpClass=classmethod(gui.Patch6GuiTests.setUpClass.__func__)
    setUp=gui.Patch6GuiTests.setUp
    tearDown=gui.Patch6GuiTests.tearDown

    def test_project_and_manual_fields_roundtrip(self):
        setup=self.window.widgets['setup']
        self.assertFalse(setup.start_datetime.isEnabled())
        setup.project_edit.setText('Tsc2')
        setup.manual_times.setChecked(True)
        self.assertTrue(setup.start_datetime.isEnabled())
        setup.start_datetime.setText('2023-05-17T12:52:30+02:00')
        setup.end_datetime.setText('2023-05-22T09:21:09+02:00')
        self.window.save_all()
        saved=json.loads((self.root/'Test.config.json').read_text())
        self.assertEqual(saved['recording']['project_name'],'Tsc2')
        self.assertEqual(saved['recording']['timeline']['start_datetime'],setup.start_datetime.text())
        self.assertEqual(load_config_bundle(self.root/'Test.config.json')['recording']['timeline']['datetime_mode'],'manual')
        self.assertFalse(self.warning.called,self.warning.call_args)

    def test_layout_confirmation_updates_saved_config_and_visible_layout(self):
        folder=self.root/'raw_antenna_reads';folder.mkdir()
        legacy(folder/'raw.txt',[(0,1,TAG),(100,2,TAG)])
        self.window.widgets['layout'].graph=bare_layout()
        tab=self.window.widgets['preprocessing'];tab.scan_directory()
        settle_discovery(tab)
        tab.table.item(0,2).setText('COM6')
        self.window.save_all();self.app.processEvents()
        settle_discovery(tab)
        path=self.root/'Test.config.json';before=path.read_bytes()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Cancel):
            self.assertFalse(tab.confirm_layout_labels(tab.read_config(),tab.input_specs()))
        self.assertEqual(before,path.read_bytes())
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.assertTrue(tab.confirm_layout_labels(tab.read_config(),tab.input_specs()))
        stored=json.loads(path.read_text())
        self.assertEqual(antenna_labels(stored['recording']['layout']),{f'{n}-1' for n in range(1,5)})
        self.assertEqual(antenna_labels(self.window.widgets['layout'].graph),{f'{n}-1' for n in range(1,5)})
        self.assertFalse(self.warning.called,self.warning.call_args)

    def test_multiple_board_ambiguity_prompts_manual_layout_instead(self):
        cfg=copy.deepcopy(self.cfg);cfg['recording']['layout']=bare_layout()
        tab=self.window.widgets['preprocessing']
        specs=[InputFile('a','COM6',profile='legacy_raw'),InputFile('b','COM7',2,'legacy_raw')]
        with patch.object(QMessageBox,'question') as question:
            self.assertFalse(tab.confirm_layout_labels(cfg,specs))
            question.assert_not_called()
        self.assertIn('cannot distinguish',self.warning.call_args.args[2])
