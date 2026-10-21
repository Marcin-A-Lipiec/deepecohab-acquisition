"""Recording-PC clock choices must preserve elapsed time and never guess DST."""

from dataclasses import replace
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import pytest

from deepecohab_recorder.preprocessing.inventory import input_signature, inventory
from deepecohab_recorder.preprocessing.models import InputFile, Settings, parse_pc
from deepecohab_recorder.preprocessing.raw_clock import RawClockError, fixed_utc_offset
from deepecohab_recorder.preprocessing.readers import records
from deepecohab_recorder.preprocessing.sources import inspect_source, serialize_source
from tests import test_preprocessing as old
from tests import test_metadata_and_gui as gui
from PySide6.QtWidgets import QDialog
from deepecohab_recorder.ui.preprocessing_advanced import AdvancedOptions

TAG = "1A63A81904"


def raw(path, timestamps):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{stamp}\t{TAG}\t01\n" for stamp in timestamps), encoding="utf-8")
    return InputFile(str(path), profile="legacy_raw")


@pytest.mark.parametrize("stamp", ["20211031_023000000", "20210328_023000000"])
def test_default_rejects_unresolvable_legacy_wall_time(tmp_path, stamp):
    spec = raw(tmp_path / "raw.txt", [stamp])
    with pytest.raises(RawClockError, match="raw.txt, line 1:.*No timestamp is being guessed"):
        list(records(spec, "Europe/Warsaw"))


def test_counter_files_do_not_silently_discard_ambiguous_wall_times(tmp_path):
    path = tmp_path / "current.csv"
    path.write_text("A-1;0000A819;01;2021-10-31T02:30:00;1\n")
    with pytest.raises(RawClockError, match="Advanced options"):
        list(records(InputFile(str(path)), "Europe/Warsaw"))


@pytest.mark.parametrize("first,second,offset", [
    ("20211031_025959900", "20211031_030000100", "+02:00"),
    ("20210328_015959900", "20210328_020000100", "+01:00"),
])
def test_fixed_pc_clock_keeps_200ms_across_both_dst_boundaries(tmp_path, first, second, offset):
    spec = replace(raw(tmp_path / "raw.txt", [first, second]), naive_utc_offset=offset)
    rows = [row for row, error, _ in records(spec, "Europe/Warsaw") if row]
    assert len(rows) == 2
    assert rows[1].pc_us - rows[0].pc_us == 200_000
    assert all(row.ticks is None for row in rows)


def test_recorded_offset_is_authoritative_and_fractional_offsets_are_supported():
    aware = "2021-10-31T02:30:00.123456+01:00"
    assert parse_pc(aware, "Europe/Warsaw", "+02:00") == parse_pc(aware, "Europe/Warsaw")
    assert parse_pc("2021-10-31T02:30:00", "Europe/Warsaw", "-03:30") == parse_pc("2021-10-31T06:00:00Z", "Europe/Warsaw")


@pytest.mark.parametrize("offset", [None, "+2:00", "+24:00", "+02:60", "Europe/Warsaw", "02:00"])
def test_malformed_offsets_are_rejected(offset):
    with pytest.raises(ValueError):
        fixed_utc_offset(offset)


def test_folder_choice_is_portable_and_invalidates_inventory(tmp_path):
    folder = tmp_path / "board"
    raw(folder / "first.txt", ["20211031_015959900"])
    raw(folder / "next.txt", ["20211031_020000100"])
    from deepecohab_recorder.schema import default_config
    config = default_config()
    with pytest.raises(RawClockError):
        inspect_source(folder, config, "Europe/Warsaw")
    source, notices = inspect_source(folder, config, "Europe/Warsaw", saved={"naive_utc_offset": "+02:00"})
    assert not notices
    saved = serialize_source(source, source.prototype, tmp_path)
    assert saved["path"] == "board"
    restored, _ = inspect_source(tmp_path / saved["path"], config, "Europe/Warsaw", saved=saved)
    inputs = restored.expand(restored.prototype)
    assert all(spec.naive_utc_offset == "+02:00" for spec in inputs)
    assert inventory(inputs, "Europe/Warsaw")["rfid_reads"] == 2
    normal = [replace(spec, naive_utc_offset="") for spec in inputs]
    assert input_signature(inputs, "Europe/Warsaw") != input_signature(normal, "Europe/Warsaw")
    settings = Settings(inputs=inputs, output_dir=str(tmp_path / "out"))
    assert all(spec.naive_utc_offset == "+02:00" for spec in Settings.from_dict(settings.to_dict()).inputs)
    assert "naive_utc_offset" not in Settings(inputs=normal, output_dir="").to_dict()["inputs"][0]


class RecordingClockPipelineTests(unittest.TestCase):
    setUp = old.PreprocessingTests.setUp
    tearDown = old.PreprocessingTests.tearDown
    process = old.PreprocessingTests.process

    def test_fixed_clock_stitches_across_dst_without_an_artificial_hour(self):
        self.cfg["recording"]["layout"] = {}
        spec = raw(self.root / "clock.txt", ["20211031_025959900", "20211031_030000100"])
        original = Path(spec.path).read_bytes()
        spec.naive_utc_offset = "+02:00"
        rows = self.process([spec], whitelist=False, interpolate=False, parquet=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["time_spent"], "200")
        self.assertEqual(rows[0]["datetime"], "2021-10-31T02:59:59.900000+02:00")
        self.assertEqual(rows[0]["internal_board_timestamp"], "")
        self.assertIn("raw_pc_clock_override", self.summary["issues"])
        self.assertNotIn("raw_pc_fixed_utc_offsets", self.summary["recordings"][0])
        self.assertEqual(self.summary["counts"].get("malformed_rows", 0), 0)
        self.assertEqual(Path(spec.path).read_bytes(), original)

    def test_override_preserves_canonical_config_diagnostic_and_data_structure(self):
        import pyarrow.parquet as pq

        def shape(value):
            if isinstance(value, dict):
                return {key: shape(item) for key, item in value.items()}
            if isinstance(value, list):
                return [shape(item) for item in value]
            return type(value).__name__

        self.cfg["recording"]["layout"] = {}
        spec = raw(self.root / "clock.txt", ["20211031_010000000", "20211031_010000100"])
        self.process([spec], whitelist=False, interpolate=False, parquet=True)
        main_shape = shape(json.loads((self.root / "experiment.config.json").read_text()))
        diagnostic_keys = set(self.summary)
        recording_keys = set(self.summary["recordings"][0])
        definitions = self.summary["definitions"]
        table = pq.read_table(next(self.output.glob("*.parquet")))
        csv_before = next(self.output.glob("*.csv")).read_bytes()
        self.process([replace(spec, naive_utc_offset="+02:00")], whitelist=False, interpolate=False, parquet=True)
        self.assertEqual(shape(json.loads((self.root / "experiment.config.json").read_text())), main_shape)
        self.assertEqual(set(self.summary), diagnostic_keys)
        self.assertEqual(set(self.summary["recordings"][0]), recording_keys)
        self.assertEqual(self.summary["definitions"], definitions)
        self.assertEqual(pq.read_table(next(self.output.glob("*.parquet"))).schema, table.schema)
        self.assertEqual(next(self.output.glob("*.csv")).read_bytes(), csv_before)
        self.assertIn("raw_pc_clock_override", self.summary["issues"])


class RecordingClockGuiTests(unittest.TestCase):
    setUpClass = classmethod(gui.Patch6GuiTests.setUpClass.__func__)
    setUp = gui.Patch6GuiTests.setUp
    tearDown = gui.Patch6GuiTests.tearDown

    def test_ambiguous_source_has_no_automatic_clock_override_or_normal_page_control(self):
        spec = raw(self.root / "clock" / "raw.txt", ["20211031_023000000"])
        tab = self.window.widgets["preprocessing"]
        tab.add_paths([spec.path])
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertFalse(hasattr(tab, "raw_clock_button"))
        self.assertEqual(tab.input_specs()[0].naive_utc_offset, "")
        self.assertIn("Advanced options", tab.log.toPlainText())
        with self.assertRaises(RawClockError):
            inventory(tab.input_specs(), "Europe/Warsaw")
        dialog = AdvancedOptions(tab)
        self.assertFalse(dialog.raw_clock_enabled.isChecked())
        self.assertTrue(dialog.raw_clock_details.isHidden())
        dialog.raw_clock_enabled.setChecked(True)
        self.assertFalse(dialog.raw_clock_details.isHidden())
        self.assertEqual(dialog.raw_clock_offsets.item(0, 1).text(), "")
        dialog.raw_clock_offsets.item(0, 1).setText("+02:00")
        with patch("deepecohab_recorder.tabs.data_preprocessing.AdvancedOptions", return_value=dialog), \
             patch.object(dialog, "exec", return_value=QDialog.Accepted):
            tab.advanced()
        self.assertEqual(tab.input_specs()[0].naive_utc_offset, "+02:00")
        self.assertEqual(tab.read()["acquisition.preprocessing"]["sources"][0]["naive_utc_offset"], "+02:00")
        self.assertEqual(inventory(tab.input_specs(), "Europe/Warsaw")["rfid_reads"], 1)
        dialog.close()
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_cancelled_exceptional_clock_options_preserve_the_default(self):
        spec = raw(self.root / "clock" / "raw.txt", ["20211031_010000000"])
        tab = self.window.widgets["preprocessing"]
        tab.add_paths([spec.path])
        tab._inventory_signature = ("cached",)
        dialog = AdvancedOptions(tab)
        dialog.raw_clock_enabled.setChecked(True)
        dialog.raw_clock_offsets.item(0, 1).setText("+01:00")
        with patch("deepecohab_recorder.tabs.data_preprocessing.AdvancedOptions", return_value=dialog), \
             patch.object(dialog, "exec", return_value=QDialog.Rejected):
            tab.advanced()
        self.assertEqual(tab.input_specs()[0].naive_utc_offset, "")
        self.assertEqual(tab._inventory_signature, ("cached",))
        dialog.close()
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_exceptional_mode_chooses_no_offset_and_unchecking_clears_it(self):
        spec = raw(self.root / "clock" / "raw.txt", ["20211031_010000000"])
        tab = self.window.widgets["preprocessing"]
        tab.add_paths([spec.path])
        dialog = AdvancedOptions(tab)
        dialog.raw_clock_enabled.setChecked(True)
        self.assertEqual(dialog.recording_offsets(), [""])
        dialog.raw_clock_offsets.item(0, 1).setText("+02:00")
        self.assertEqual(dialog.recording_offsets(), ["+02:00"])
        dialog.raw_clock_enabled.setChecked(False)
        self.assertEqual(dialog.recording_offsets(), [""])
        dialog.close()
