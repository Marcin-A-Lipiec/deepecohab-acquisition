"""Draft metadata may be saved and explicitly reviewed before preprocessing."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from deepecohab_recorder.config_bundle import load_config_bundle
from deepecohab_recorder.schema import default_config
from tests import test_metadata_and_gui as gui
from tests import test_preprocessing as old


class DraftMetadataGuiTests(unittest.TestCase):
    setUpClass = classmethod(gui.Patch6GuiTests.setUpClass.__func__)
    setUp = gui.Patch6GuiTests.setUp
    tearDown = gui.Patch6GuiTests.tearDown

    def test_setup_can_save_and_reload_before_animal_details_are_entered(self):
        config = default_config()
        config["recording"]["name"] = "Test"
        self.window.services.repo.replace(config)
        self.window._apply(config)
        self.window.widgets["setup"].directory_edit.setText(str(self.root))
        self.window.widgets["setup"].location.setText("Draft room")
        self.window.save_all()
        saved = load_config_bundle(self.root / "Test.config.json")
        self.assertEqual(saved["recording"]["recording_location"], "Draft room")
        self.assertTrue(all(not animal["tag"] for animal in saved["recording"]["cohort"]["animals"]))
        animals = self.window.widgets["animals"]
        animals.table.item(0, 1).setText("1A63A81904")
        self.window.save_section(animals)
        saved = load_config_bundle(self.root / "Test.config.json")
        self.assertEqual(saved["recording"]["cohort"]["animals"][0]["tag"], "1A63A81904")
        self.assertFalse(saved["recording"]["cohort"]["animals"][0]["genotype"])
        self.assertFalse(self.warning.called, self.warning.call_args)

    def prepare_draft_preprocessing(self):
        self.cfg["recording"]["cohort"] = dict(n_mice=1, animals=[])
        self.window.services.repo.replace(self.cfg)
        self.window._apply(self.cfg)
        self.window.save_all()
        path = self.root / "raw.csv"
        path.write_text("A-1;1A63A81904;1;2026-09-01T12:00:00+02:00;1\n"
                        "A-1;1A63A81904;B;2026-09-01T12:00:00.100+02:00;1\n")
        tab = self.window.widgets["preprocessing"]
        tab.add_paths([path])
        return tab

    def test_preprocessing_warns_about_missing_metadata_and_cancel_starts_no_worker(self):
        tab = self.prepare_draft_preprocessing()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Cancel) as question:
            tab.start()
        self.assertTrue(question.called)
        self.assertIn("tag filtering will be disabled", question.call_args.args[2])
        self.assertFalse(tab.background_busy())
        self.assertIsNone(tab._temporary)
        self.assertFalse((self.root / "data").exists())
        self.assertFalse(self.warning.called, self.warning.call_args)

    def test_explicitly_proceeding_preserves_reads_and_records_the_metadata_choice(self):
        tab = self.prepare_draft_preprocessing()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            tab.start()
        self.assertTrue(tab.process.waitForFinished(15000))
        self.app.processEvents()
        self.assertEqual(tab.process.exitCode(), 0, tab.log.toPlainText())
        self.assertIsNotNone(tab._job_result, tab.log.toPlainText())
        result = tab._job_result
        report = json.loads(Path(result["diagnostic_path"]).read_text())
        self.assertIn("incomplete_metadata_accepted", report["issues"])
        self.assertEqual(report["counts"].get("rejected_tag_reads", 0), 0)
        self.assertEqual(result["outputs"][0]["rows"], 1)
        saved = load_config_bundle(self.root / "Test.config.json")
        self.assertTrue(all(not animal["tag"] for animal in saved["recording"]["cohort"]["animals"]))
        self.assertFalse(self.warning.called, self.warning.call_args)


class DraftMetadataPipelineTests(unittest.TestCase):
    setUp = old.PreprocessingTests.setUp
    tearDown = old.PreprocessingTests.tearDown
    process = old.PreprocessingTests.process
    raw = old.PreprocessingTests.raw

    def test_missing_cohort_and_layout_can_stitch_observations_without_inference(self):
        self.cfg["recording"]["cohort"] = dict(n_mice=0, animals=[])
        self.cfg["recording"]["layout"] = {}
        source = self.raw("raw.csv", [(0, "A", old.TAG), (100, "A", old.TAG), (200, "B", old.OTHER)])
        rows = self.process([source], whitelist=True, interpolate=True, allow_incomplete_metadata=True)
        self.assertEqual({row["animal_id"] for row in rows}, {old.TAG, old.OTHER})
        self.assertEqual(self.inserted, [])
        self.assertEqual(self.summary["counts"].get("rejected_tag_reads", 0), 0)
        self.assertIn("incomplete_metadata_accepted", self.summary["issues"])
