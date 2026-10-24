"""Group-specific stitching, observed-antenna UI and per-board recording gaps."""
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QLabel
from tests import test_preprocessing as helper
from tests import test_metadata_and_gui as gui_helper
from deepecohab_recorder.preprocessing import COLUMNS, PARQUET_COLUMNS
from deepecohab_recorder.preprocessing.inventory import inventory, input_signature
from deepecohab_recorder.preprocessing.models import Settings, Cancelled, parse_pc
from deepecohab_recorder.preprocessing.recording_gaps import missed_data_periods
from deepecohab_recorder.tabs.data_preprocessing import AdvancedOptions
from deepecohab_recorder.ui.preprocessing_outputs import OutputGroupsDialog


class PipelineTests(unittest.TestCase):
    setUp = helper.PreprocessingTests.setUp
    tearDown = helper.PreprocessingTests.tearDown
    raw = helper.PreprocessingTests.raw
    process = helper.PreprocessingTests.process
    training_events = helper.PreprocessingTests.training_events

    def test_interpolated_rows_sorted_with_observed_csv_and_parquet(self):
        rows = self.process([self.raw('sort.txt', self.training_events())], interpolate=True, parquet=True)
        times = [datetime.fromisoformat(r['datetime']) for r in rows]
        self.assertEqual(times, sorted(times))
        inserted = self.inserted
        self.assertEqual(len(inserted), 1)
        index = inserted[0]['row_number']
        self.assertLess(index, len(rows) - 1)
        self.assertEqual(rows[index]['antenna'], 'B-1')
        import pyarrow.parquet as pq
        parquet = pq.read_table(next(self.output.glob('*.parquet')))
        self.assertEqual(parquet.column_names, list(PARQUET_COLUMNS))
        ptimes = parquet.column('datetime').to_pylist()
        self.assertEqual(ptimes, times)
        self.assertEqual(parquet.column('antenna').to_pylist(), [r['antenna'] for r in rows])
        self.assertEqual(parquet.schema.field('datetime').type.tz, 'Europe/Warsaw')
        self.assertIn('datetime ascending', self.summary['processing']['output_sort_order'])

    def test_group_windows_independent_antennas_tags_exact_threshold_and_ttl(self):
        events = [(t, a, helper.TAG) for a, ts in (('A', (0, 290, 590)), ('F', (0, 900, 1900)), ('P', (0, 500))) for t in ts]
        events += [(10, 'G', helper.TAG), (510, 'G', helper.OTHER), (910, 'G', helper.TAG),
                   (20, '10', '00000001'), (21, '10', '00000001')]
        rows = self.process([self.raw('groups.txt', sorted(events))],
            routes={'F-1': 'lickometers', 'G-1': 'lickometers', 'P-1': 'feeders'},
            group_windows_ms={'lickometers': 1000})
        durations = lambda ant: [int(r['time_spent']) for r in rows if r['antenna'] == ant]
        self.assertEqual(durations('A-1'), [290, 10])
        self.assertEqual(durations('F-1'), [900, 10])
        self.assertEqual(durations('G-1'), [10, 10, 10])
        self.assertEqual(durations('P-1'), [10, 10])
        self.assertEqual(durations('TTL1-1'), [0, 0])
        self.assertEqual(self.context['stitch_windows_ms'], {'main': 300, 'device_antenna2': 1000, 'device_antenna1': 300})
        outputs = {o['group']: o['file'] for o in self.result['outputs']}
        with (self.output / outputs['device_antenna2']).open() as f:
            self.assertEqual({r['antenna'] for r in csv.DictReader(f)}, {'F-1', 'G-1'})

    def test_separate_group_defaults_to_300_even_when_main_changes(self):
        rows = self.process([self.raw('defaults.txt', [(0, 'A', helper.TAG), (0, 'F', helper.TAG), (500, 'A', helper.TAG), (500, 'F', helper.TAG)])],
            window_ms=600, routes={'F-1': 'extra'})
        self.assertEqual([int(r['time_spent']) for r in rows if r['antenna'] == 'A-1'], [500])
        self.assertEqual([int(r['time_spent']) for r in rows if r['antenna'] == 'F-1'], [10, 10])
        self.assertEqual(self.context['stitch_windows_ms'], {'main': 600, 'device_antenna1': 300})

    def test_auxiliary_window_does_not_change_main_interpolation(self):
        events = self.training_events() + [(i, 'F', helper.TAG) for i in (20, 720, 1420, 2120, 2820)]
        spec = self.raw('aux_window.txt', sorted(events))
        mains, inserted, counts = [], [], []
        for window in (300, 1000):
            rows = self.process([spec], interpolate=True, routes={'F-1': 'lickometers'}, group_windows_ms={'lickometers': window})
            mains.append([r for r in rows if r['antenna'] != 'F-1'])
            inserted.append([(r['antenna'], r['datetime'], r['duration_ms']) for r in self.inserted])
            counts.append(len([r for r in rows if r['antenna'] == 'F-1']))
        self.assertEqual(mains[0], mains[1])
        self.assertEqual(inserted[0], inserted[1])
        self.assertEqual(len(inserted[0]), 1)
        self.assertEqual(counts, [5, 1])

    def test_invalid_group_windows_rejected_and_old_settings_default(self):
        spec = self.raw('validation.txt', [(0, 'A', helper.TAG)])
        for key, value in (('main', 1000), ('ttl', 1000), ('extra', 0), ('extra', True), ('extra', '1000'), ('extra', 60001)):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'Group stitching'):
                Settings([spec], str(self.root / 'out'), group_windows_ms={key: value}).validate()
        old = Settings.from_dict({'inputs': [spec.__dict__], 'output_dir': str(self.root / 'out')})
        self.assertEqual(old.group_windows_ms, {})
        self.assertTrue(old.flag_missed_data_periods)
        self.assertEqual(Settings.from_dict(old.to_dict()).to_dict(), old.to_dict())

    def test_gaps_use_ttl_boundaries_even_with_tag_filter_and_merged_output(self):
        first = self.raw('first.txt', [(0, 'A', helper.TAG), (1000, '10', '00000001')])
        second = self.raw('second.txt', [(3600000, '10', '00000001'), (3600100, 'A', helper.TAG)])
        for split in (True, False):
            self.process([first, second], split_restarts=split)
            gaps = self.summary['recording_boundaries']
            self.assertEqual(len(gaps), 1)
            gap = gaps[0]
            self.assertEqual(datetime.fromisoformat(gap['start']), helper.BASE + timedelta(seconds=1))
            self.assertEqual(datetime.fromisoformat(gap['end']), helper.BASE + timedelta(hours=1))
            self.assertEqual(gap['board'], 1)
            self.assertEqual(gap['seconds'], 3599)
            self.assertEqual(self.summary['recordings'][gap['previous_recording']-1]['part'], 1)
            self.assertEqual(self.summary['recordings'][gap['next_recording']-1]['part'], 2)
        self.assertEqual(len(self.result['outputs']), 2)  # One RFID and one TTL CSV.
        self.process([first, second], flag_missed_data_periods=False)
        self.assertEqual(self.summary['recording_boundaries'], [])
        self.assertFalse(self.context['options']['flag_missed_data_periods'])
        self.assertEqual(len(self.summary['recording_boundaries']), 0)

    def test_every_gap_retained_and_no_in_file_silence_reported(self):
        specs = [self.raw(f'part_{i}.txt', [(i * 100000, 'A', helper.TAG), (i * 100000 + 1000, 'A', helper.TAG)]) for i in range(9)]
        self.process(specs, ignore_short_recording_gaps=False)
        self.assertEqual(len(self.summary['recording_boundaries']), 8)
        self.assertEqual(len(self.summary['recording_boundaries']), 8)
        self.process([self.raw('quiet.txt', [(0, 'A', helper.TAG), (80000000, 'A', helper.TAG)])])
        self.assertEqual(self.summary['recording_boundaries'], [])

    def test_inventory_full_file_used_only_and_com_identity(self):
        a = self.raw('late.txt', [(i * 10, 'A', helper.TAG) for i in range(600)] + [(7000, 'P', helper.TAG), (7010, '10', '00000001')])
        b = self.raw('other.txt', [(0, 'A', helper.TAG)], port='COM5', board=2)
        with Path(a.path).open('a') as handle:
            handle.write('malformed row\n')
        result = inventory([a, b], 'Europe/Warsaw')
        self.assertEqual(result['antennas'], ['A-1', 'A-2', 'P-1'])
        self.assertEqual(result['ttl_reads'], 1)
        self.assertEqual(result['malformed_rows'], 1)
        self.assertEqual(result['rfid_reads'], 602)
        self.assertEqual(inventory([b], 'Europe/Warsaw')['antennas'], ['A-2'])
        self.assertNotEqual(input_signature([a], 'Europe/Warsaw'), input_signature([b], 'Europe/Warsaw'))

    def test_inventory_cancellation_and_changed_file(self):
        spec = self.raw('changing.txt', [(0, 'A', helper.TAG)])
        with self.assertRaises(Cancelled):
            inventory([spec], 'Europe/Warsaw', cancelled=lambda: True)
        def mutate(value, text):
            if value == 0:
                with Path(spec.path).open('a') as f:
                    f.write('A;0000A819;1;2025-06-01T12:00:00.010+02:00\n')
        with self.assertRaisesRegex(ValueError, 'changed during'):
            inventory([spec], 'Europe/Warsaw', progress=mutate)


class GapBoundaryTests(unittest.TestCase):
    def source(self, start, end, ident, port='COM3', board=1):
        return dict(first_pc_us=parse_pc(start, 'Europe/Warsaw'), last_pc_us=parse_pc(end, 'Europe/Warsaw'),
                    id=ident, path=f'part_{ident}_{port}.csv', recording_part=ident, port=port, board_id=board)

    def test_exact_user_power_outage_example(self):
        sources = [self.source('2026-10-10T12:00:00+02:00', '2026-10-10T23:00:00+02:00', 1),
                   self.source('2026-10-11T10:00:00+02:00', '2026-10-11T20:00:00+02:00', 2)]
        gap, = missed_data_periods(sources[::-1], 'Europe/Warsaw')
        self.assertEqual(gap['start'], '2026-10-10T23:00:00.000000+02:00')
        self.assertEqual(gap['end'], '2026-10-11T10:00:00.000000+02:00')
        self.assertEqual(gap['duration_seconds'], 11 * 3600)

    def test_overlap_nested_touching_ranges_and_separate_boards(self):
        def src(a, b, i, **extra):
            return self.source(f'2026-10-10T{a}:00:00+02:00', f'2026-10-10T{b}:00:00+02:00', i, **extra)
        sources = [src('10', '18', 1), src('11', '12', 2), src('13', '16', 3),
                   src('18', '19', 4), src('20', '21', 5), src('09', '23', 1, port='COM5', board=2)]
        gap, = missed_data_periods(sources, 'Europe/Warsaw')
        self.assertEqual(gap['board_id'], 1)
        self.assertEqual(gap['start'], '2026-10-10T19:00:00.000000+02:00')
        self.assertEqual(gap['end'], '2026-10-10T20:00:00.000000+02:00')
        self.assertEqual(gap['previous_file'], 'part_4_COM3.csv')

    def test_dst_gap_is_elapsed_time_not_local_clock_subtraction(self):
        sources = [self.source('2026-10-25T01:00:00+02:00', '2026-10-25T02:50:00+02:00', 1),
                   self.source('2026-10-25T02:10:00+01:00', '2026-10-25T03:00:00+01:00', 2)]
        gap, = missed_data_periods(sources, 'Europe/Warsaw')
        self.assertEqual(gap['duration_seconds'], 20 * 60)
        self.assertTrue(gap['start'].endswith('+02:00'))
        self.assertTrue(gap['end'].endswith('+01:00'))


class GuiTests(unittest.TestCase):
    setUpClass = classmethod(gui_helper.Patch6GuiTests.setUpClass.__func__)
    setUp = gui_helper.Patch6GuiTests.setUp
    tearDown = gui_helper.Patch6GuiTests.tearDown
    prepare_inputs = gui_helper.Patch6GuiTests.prepare_inputs

    def test_real_inventory_worker_cache_and_removed_file(self):
        tab = self.prepare_inputs()
        # This antenna is absent from the initial 500-record preview and layout.
        spec = tab._specs[1]
        with Path(spec.path).open('a') as f:
            for i in range(550):
                f.write(f'A;1A63A81904;{3000+i:X};2026-09-01T13:00:01+02:00;1\n')
            f.write('P;1A63A81904;1000;2026-09-01T13:00:02+02:00;1\n')
        with patch.object(tab, '_show_output_groups') as show:
            tab.output_groups()
            self.assertTrue(tab.background_busy())
            self.assertFalse(tab.start_button.isEnabled())
            self.assertTrue(tab.process.waitForFinished(15000))
            self.app.processEvents()
            self.assertEqual(tab._job_result['kind'], 'inventory_completed', tab.log.toPlainText())
            self.assertEqual(tab.antenna_names(), {'A-1', 'P-1'})
            self.assertEqual(show.call_count, 1)
            tab.output_groups()
            self.assertFalse(tab.background_busy())
            self.assertEqual(show.call_count, 2)
            tab.table.selectRow(1)
            tab.remove_selected()
            from tests.helpers import settle_discovery
            settle_discovery(tab)
            tab.output_groups()
            self.assertTrue(tab.process.waitForFinished(15000))
            self.app.processEvents()
            self.assertEqual(tab.antenna_names(), {'A-1'})
        self.assertTrue(tab.start_button.isEnabled())
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_group_rename_preserves_window_and_cancel_isolated(self):
        tab = self.prepare_inputs()
        tab.options["split_restarts"] = True  # Explicitly test separate output parts.
        tab.options['routes'] = {'A-1': 'lickometers'}
        tab.options['group_windows_ms'] = {'lickometers': 1000}
        dialog = OutputGroupsDialog(tab)
        self.assertEqual(dialog.windowTitle(), 'Manage RFID and TTL output grouping')
        self.assertIn('A-1', dialog.sources)
        self.assertEqual(dialog.values(), {'A-1': 'device_antenna1'})
        self.assertEqual(dialog.stitch_windows(), {'device_antenna1': 1000})
        self.assertIn('.device_antenna1_part01', dialog.table.item(dialog.sources.index('A-1'), 3).text())
        dialog.reject()
        self.assertEqual(tab.options['routes'], {'A-1': 'lickometers'})
        self.assertEqual(tab.options['group_windows_ms'], {'lickometers': 1000})

    def test_advanced_group_values_help_and_disabled_gap_flag(self):
        tab = self.prepare_inputs()
        tab.options['routes'] = {'A-1': 'lickometers', '9-1': 'other'}
        tab.options['group_windows_ms'] = {'lickometers': 1000}
        dialog = AdvancedOptions(tab)
        self.assertEqual(dialog.group_windows['device_antenna1'].value(), 1000)
        self.assertEqual(dialog.group_windows['device_antenna2'].value(), 300)
        self.assertTrue(dialog.flag_gaps.isChecked())
        dialog.window.setValue(400)
        dialog.group_windows['device_antenna2'].setValue(500)
        dialog.flag_gaps.setChecked(False)
        values, clocks = dialog.values()
        self.assertEqual(values['window_ms'], 400)
        self.assertEqual(values['group_windows_ms'], {'device_antenna1': 1000, 'device_antenna2': 500})
        self.assertFalse(values['flag_missed_data_periods'])
        self.assertEqual(clocks, [(None, 0), (None, 0)])
        help_text = '\n'.join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn('groups are local to each board', help_text)
        self.assertIn('not hardware synchronization', help_text)
        dialog.reject()
        self.assertTrue(tab.options['flag_missed_data_periods'])

    def test_worker_receives_aux_window_and_gap_option_from_advanced(self):
        tab = self.prepare_inputs()
        tab.options["split_restarts"] = True  # Explicitly test separate output parts.
        path = Path(tab._specs[0].path)
        text = path.read_text()
        text = text.replace('2026-09-01T12:00:00.100', '2026-09-01T12:00:00.900').replace(';3F2;', ';442;')
        path.write_text(text)
        tab.options['routes'] = {'A-1': 'lickometers'}
        dialog = AdvancedOptions(tab)
        dialog.group_windows['device_antenna1'].setValue(1000)
        values, _ = dialog.values()
        tab.options = values
        tab.options['interpolate'] = False
        tab.csv.setChecked(True)
        dialog.close()
        tab.start()
        self.assertTrue(tab.process.waitForFinished(15000))
        self.app.processEvents()
        self.assertEqual(tab.process.exitCode(), 0, tab.log.toPlainText())
        report = json.loads(Path(tab._job_result['diagnostic_path']).read_text())
        from tests.helpers import preprocessing_context
        self.assertEqual(preprocessing_context(report, tab._job_result['diagnostic_path'])['stitch_windows_ms']['device_antenna1'], 1000)
        self.assertEqual(len(report['recording_boundaries']), 1)
        self.assertEqual(report['counts']['TTL_events'], 2)
        output = next(o['file'] for o in tab._job_result['outputs'] if o['group'] == 'device_antenna1' and o['file'].endswith('_part01.csv'))
        with (Path(tab._output_directory) / output).open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r['time_spent'] for r in rows], ['900'])
        self.assertFalse(self.warning.called, self.warning.call_args)
