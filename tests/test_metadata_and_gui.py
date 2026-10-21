from deepecohab_recorder.config_bundle import load_config_bundle
"""Patch 6 integration: named metadata, grouped bouts, Qt controls and real worker."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox
from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.event_metadata import compartment_names, storage_config
from deepecohab_recorder.experiment_files import discover_config, experiment_stem, unique_path
from deepecohab_recorder.main_window import MainWindow
from deepecohab_recorder.schema import migrate_config
from deepecohab_recorder.ui.preprocessing_outputs import OutputGroupsDialog
from tests.helpers import valid_config

ROOT = Path(__file__).resolve().parents[1]

class MetadataTests(unittest.TestCase):
    def test_supplied_grouped_example_and_schema_round_trip(self):
        import jsonschema
        source = json.loads((ROOT / 'tests/fixtures/grouped_events_example.json').read_text())
        original = copy.deepcopy(source)
        config = migrate_config(source)
        stored = storage_config(config)
        self.assertEqual(source, original)
        self.assertEqual(stored['version'], 8)
        self.assertEqual(len(stored['recording']['events']), 3)
        self.assertEqual(sum(len(e['bouts']) for e in stored['recording']['events']), 5)
        self.assertEqual(config['acquisition']['ui']['events_row_count'], 5)
        self.assertEqual(stored['recording']['events'][0]['bouts'][0]['position'], ['cage_1'])
        self.assertIsNone(stored['recording']['events'][1]['bouts'][0]['position'])
        self.assertIsNone(stored['recording']['events'][2]['bouts'][0]['position'])
        self.assertEqual(migrate_config(stored), config)
        schema = json.loads((ROOT / 'docs/config_v8.schema.json').read_text())
        from deepecohab_recorder.config_bundle import project
        from deepecohab_recorder.preprocessing.layout_check import check_layout, apply_layout_changes
        from deepecohab_recorder.preprocessing.models import InputFile
        config = apply_layout_changes(config, check_layout(config, [InputFile('unused', board_id=1, profile='legacy_raw')])['changes'])
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(project(config, Path("Example.json"))[0])

    def test_named_save_and_legacy_discovery_do_not_modify_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = valid_config()
            config['version'] = 3
            legacy = root / 'config.json'
            legacy.write_text(json.dumps(config))
            before = legacy.read_bytes()
            name = 'WT_M_TSC2_12_10_2026'
            repo = ConfigRepository(lambda: name, lambda: directory)
            self.assertEqual(repo.discover(), legacy)
            repo.import_file(legacy)
            self.assertIn('v8', repo.notice)
            csv_path, path = repo.save()
            self.assertEqual(path.name, name + '.config.json')
            self.assertEqual(csv_path.name, name + '.acquisition_gui_state.json')
            self.assertFalse(legacy.exists())
            self.assertFalse((root / 'session_configs').exists())
            self.assertEqual(repo.discover(), path)
            self.assertIsInstance(json.loads(path.read_text())['recording']['events'], list)
            repo.save()
            self.assertFalse((root / 'session_configs').exists())

    def test_discovery_rejects_ambiguous_and_invalid_named_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = storage_config(valid_config())
            for stem in ('first', 'second'):
                (root / (stem + '.json')).write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError, 'More than one'):
                discover_config(root, 'experiment')
            self.assertEqual(discover_config(root, 'first').name, 'first.json')
            (root / 'first.json').write_text('{bad')
            with self.assertRaises(ValueError):
                discover_config(root, 'first')

    def test_positions_multiple_and_inferred_end_survive_save(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = ConfigRepository(lambda: 'Test', lambda: directory)
            cfg = valid_config()
            names = compartment_names(cfg['recording']['layout'])[:2]
            cfg['recording']['timeline']['end_datetime'] = '2026-09-01T15:00:00+02:00'
            cfg['recording']['events'] = [{'name': 'Rough', 'description': 'Rough floor', 'bouts': [
                {'start': '2026-09-01T13:00:00+02:00', 'end': None, 'position': names}]}]
            repo.replace(migrate_config(cfg))
            path = repo.save()[1]
            bout = json.loads(path.read_text())['recording']['events'][0]['bouts'][0]
            self.assertEqual(bout['position'], names)
            self.assertNotIn('end_user_specified', bout)
            self.assertEqual(bout['end'], cfg['recording']['timeline']['end_datetime'])
            repo.import_file(path)
            self.assertFalse(repo.data['recording']['events']['bouts'][0]['end_user_specified'])
            repo.update_paths({'recording.events': [{'name': 'Bad', 'bouts': [
                {'start': '2026-09-01T13:00:00+02:00', 'end': None, 'position': 'missing_cage'}]}]})
            with self.assertRaisesRegex(ValueError, 'missing from the saved layout'):
                repo.save()

    def test_readable_names_and_collision_suffixes(self):
        self.assertEqual(experiment_stem('WT_M_TSC2_12_10_2026'), 'WT_M_TSC2_12_10_2026')
        for bad in ('x/y', 'COM3', 'bad?', 'end.'):
            with self.assertRaises(ValueError):
                experiment_stem(bad)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'Test.data.csv'
            path.touch()
            self.assertEqual(unique_path(path).name, 'Test.data_02.csv')

class Patch6GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.warning = patch.object(QMessageBox, 'warning').start()
        patch.object(QMessageBox, 'information').start()
        self.window = MainWindow()
        self.window.timer.stop()
        self.window.widgets['setup'].directory_edit.setText(str(self.root))
        self.cfg = valid_config()
        self.window.services.repo.replace(self.cfg)
        self.window._apply(self.cfg)
        self.window._sync_context()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        patch.stopall()
        self.temp.cleanup()

    def test_zero_to_three_event_rows_survive_save_reload(self):
        tab = self.window.widgets['events']
        for count in (0, 1, 2, 3):
            tab.count.setValue(count)
            self.window.save_section(tab)
            self.assertEqual(tab.table.rowCount(), count)
            path = self.root / 'Test.config.json'
            saved = load_config_bundle(path)
            tab.load(migrate_config(saved))
            self.assertEqual(tab.table.rowCount(), count)
            self.assertEqual(saved['acquisition']['ui']['events_row_count'], count)
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_compartments_and_description_sharing(self):
        tab = self.window.widgets['events']
        tab.count.setValue(3)
        for row, name in enumerate(('Rough', 'Smooth', 'Rough')):
            tab.table.item(row, 0).setText(name)
            tab.table.item(row, 2).setText(f'2026-09-01T1{row}:00:00+02:00')
        tab.table.item(0, 1).setText('Rough floor')
        self.assertEqual(tab.table.item(2, 1).text(), 'Rough floor')
        self.assertEqual(tab.table.item(1, 1).text(), '')
        names = tab.available_compartments()
        self.assertIn('tunnel_1', names)
        tab.table.cellWidget(0, 4).set_position(names[:2])
        self.window.save_section(tab)
        saved = json.loads((self.root / 'Test.config.json').read_text())
        self.assertEqual(len(saved['recording']['events']), 2)
        self.assertEqual(saved['recording']['events'][0]['bouts'][0]['position'], names[:2])
        self.assertIsNone(saved['recording']['events'][0]['bouts'][1]['position'])
        self.assertEqual(tab.table.columnWidth(2), tab.table.columnWidth(3))
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_name_edit_does_not_reload_old_name(self):
        self.window.services.repo.save()
        acquisition = self.window.widgets['setup']
        acquisition.experiment_edit.setText('Renamed')
        acquisition.experiment_edit.editingFinished.emit()
        self.assertEqual(acquisition.experiment_edit.text(), 'Renamed')
        self.window.save_all()
        self.assertTrue((self.root / 'Renamed.config.json').exists())
        self.assertFalse((self.root / 'Test.config.json').exists())
        self.assertFalse(self.warning.called, self.warning.call_args)

    def prepare_inputs(self):
        self.window.services.repo.save()
        raw = self.root / 'raw_antenna_reads'
        raw.mkdir()
        for part, hour in enumerate((12, 13), 1):
            (raw / f'Test_part{part}_COM5.csv').write_text(
                f'A;1A63A81904;{part * 1000:X};2026-09-01T{hour}:00:00+02:00;1\n'
                f'A;1A63A81904;{part * 1000 + 10:X};2026-09-01T{hour}:00:00.100+02:00;1\n'
                f'10;00000001;{part * 1000 + 11:X};2026-09-01T{hour}:00:00.110+02:00;1\n')
        (raw / 'not_raw.csv').write_text('antenna,animal_id,datetime,time_spent,internal_board_timestamp\n')
        tab = self.window.widgets['preprocessing']
        tab.configure_experiment(force=True)
        return tab

    def test_auto_discovery_output_groups_and_real_background_process(self):
        tab = self.prepare_inputs()
        tab.options["split_restarts"] = True  # Exercise the retained separate-parts option.
        self.assertEqual(tab.config_path.text(), str(self.root / 'Test.config.json'))
        self.assertEqual(tab.output_path.text(), str(self.root))
        self.assertEqual(tab.table.rowCount(), 2)
        self.assertEqual([s.group for s in tab.input_specs()], ['1', '1'])
        self.assertEqual([s.part for s in tab.input_specs()], [1, 2])
        tab.scan_directory()
        self.assertEqual(tab.table.rowCount(), 2)
        dialog = OutputGroupsDialog(tab)
        self.assertFalse(any(s.startswith('TTL') for s in dialog.sources))
        self.assertEqual(dialog.values(), {})
        dialog.table.selectRow(dialog.sources.index('A-1'))
        dialog.rfid.group_together()
        self.assertEqual(dialog.values(), {'A-1': 'device_antenna1'})
        dialog.rfid.separate()
        self.assertEqual(dialog.values(), {'A-1': 'device_antenna2'})
        dialog.rfid.assign('main')
        self.assertEqual(dialog.values(), {'A-1': 'main'})
        dialog.rfid.assign(None)
        self.assertEqual(dialog.values(), {})
        dialog.close()
        tab.options['interpolate'] = False
        tab.csv.setChecked(True)
        tab.start()
        self.assertTrue(tab.process.waitForFinished(15000))
        self.app.processEvents()
        self.assertEqual(tab.process.exitCode(), 0, tab.log.toPlainText())
        self.assertEqual(tab._job_result['kind'], 'completed')
        names = {f['file'] for f in tab._job_result['outputs']}
        self.assertEqual(names, {f'Test.{group}_part{part:02d}.{suffix}' for group in ('data', 'TTL_data1') for part in (1, 2) for suffix in ('csv', 'parquet')})
        self.assertNotIn('lickometer', self.window.widgets)
        self.assertFalse(self.warning.called, self.warning.call_args)
