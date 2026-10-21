"""Navigation must preserve drafts, live resources and the existing close lifecycle."""
import copy
import gc
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from deepecohab_recorder import preferences
from deepecohab_recorder.main_window import MainWindow
from deepecohab_recorder.runtime_paths import worker_python
from deepecohab_recorder.ui.common import MatchDialog
from deepecohab_recorder.ui.registry import PAGES
from deepecohab_recorder.ui.theme import manager
from tests.helpers import SerialFactory, valid_config, wait_for


def settle(app, rounds=6):
    for _ in range(rounds):
        app.processEvents()


def qt_wait(app, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Qt worker did not finish within the deadline")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPECOHAB_ACQUISITION_HOME", str(tmp_path / "appdata"))
    app = QApplication.instance() or QApplication([])
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    # Dispose unreachable wrappers between windows, outside Qt model creation.
    gc.collect()
    theme = manager()
    original = theme.mode
    theme.set_mode("light", persist=False)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window = MainWindow()
    window.widgets["setup"].directory_edit.setText(str(tmp_path / "experiment"))
    config = valid_config()
    window.services.repo.replace(config)
    window._apply(config)
    window._sync_context()
    window.resize(1280, 800)
    window.show()
    settle(app)
    yield app, window, tmp_path
    if not window.services._shutdown.is_set():
        window.services.stop_devices()
    wait_for(lambda: not window.services.busy(), 10)
    tab = window.widgets["preprocessing"]
    tab.request_background_stop()
    qt_wait(app, lambda: not tab.background_busy())
    if window._system_check_dialog:
        window._system_check_dialog.request_background_stop()
        qt_wait(app, lambda: not window.system_check_busy(), 30)
    window.close()
    window.deleteLater()
    settle(app)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    theme.set_mode(original, persist=False)
    assert not warnings, warnings


def test_sidebar_and_keyboard_preserve_every_draft_view_and_preview(workspace):
    import numpy as np
    app, window, root = workspace
    assert window.sidebar.order[window.sidebar.order.index("layout") + 1] == "environment"
    assert window.sidebar.order[window.sidebar.order.index("environment") + 1] == "events"
    window.widgets["animals"].table.item(0, 2).setText("Unsaved subject")
    window.widgets["events"].notes.setPlainText("Unsaved notes ł")
    setup = window.widgets["setup"]
    setup.project_edit.setText("Unsaved project")
    layout = window.widgets["layout"]
    window.pages.set_current_key("layout")
    settle(app)
    layout.view.fitted = False
    layout.view.scale(1.4, 1.4)
    cage = next(item for key, item in layout._items.items() if key.startswith("cage:"))
    cage.setSelected(True)
    transform = layout.view.transform()
    scroll_values = (layout.view.horizontalScrollBar().value(), layout.view.verticalScrollBar().value())
    camera = window.widgets["camera"]
    camera.update_preview(SimpleNamespace(status_text=lambda: "Synthetic preview",
        get_preview_frame=lambda: np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)))
    preview = camera.preview_label.pixmap().toImage()
    drafts = copy.deepcopy({key: widget.read() for key, widget in window.widgets.items()})
    config = copy.deepcopy(window.services.repo.data)
    pages = {key: id(widget) for key, widget in window.widgets.items()}
    items = {key: id(item) for key, item in layout._items.items()}
    for key in window.sidebar.order:
        QTest.mouseClick(window.sidebar.buttons[key], Qt.LeftButton)
        settle(app)
        assert window.pages.current_key() == key
        assert window.sidebar.buttons[key].isChecked()
        assert window.header.page_title.text() == PAGES[window.pages.currentIndex()].title
    window.pages.set_current_key("setup")
    setup.project_edit.setFocus()
    QTest.keyClick(setup.project_edit, Qt.Key_Tab, Qt.ControlModifier)
    settle(app)
    assert window.pages.current_key() == "animals"
    QTest.keyClick(window, Qt.Key_Tab, Qt.ControlModifier | Qt.ShiftModifier)
    settle(app)
    assert window.pages.current_key() == "setup"
    window.pages.set_current_key("layout")
    settle(app)
    assert pages == {key: id(widget) for key, widget in window.widgets.items()}
    assert items == {key: id(item) for key, item in layout._items.items()}
    assert drafts == {key: widget.read() for key, widget in window.widgets.items()}
    assert config == window.services.repo.data
    assert layout.view.transform() == transform and cage.isSelected()
    assert scroll_values == (layout.view.horizontalScrollBar().value(), layout.view.verticalScrollBar().value())
    assert preview == camera.preview_label.pixmap().toImage()


def test_sidebar_arrows_focus_pages_and_enter_activates_without_stealing_edit_keys(workspace):
    app, window, root = workspace
    button = window.sidebar.buttons["setup"]
    button.setFocus()
    QTest.keyClick(button, Qt.Key_End)
    assert window.sidebar.buttons["preprocessing"].hasFocus()
    assert window.pages.current_key() == "setup"
    QTest.keyClick(window.sidebar.buttons["preprocessing"], Qt.Key_Return)
    settle(app)
    assert window.pages.current_key() == "preprocessing"
    QTest.keyClick(window.sidebar.buttons["preprocessing"], Qt.Key_Home)
    QTest.keyClick(window.sidebar.buttons["setup"], Qt.Key_Down)
    assert window.sidebar.buttons["animals"].hasFocus()
    window.pages.set_current_key("setup")
    field = window.widgets["setup"].project_edit
    field.setText("abcd")
    field.setCursorPosition(2)
    field.setFocus()
    QTest.keyClick(field, Qt.Key_Left)
    assert field.cursorPosition() == 1 and window.pages.current_key() == "setup"


def test_header_save_and_load_shortcuts_use_full_experiment_actions(workspace, monkeypatch):
    from deepecohab_recorder.config_bundle import load_config_bundle
    app, window, root = workspace
    events = window.widgets["events"]
    events.notes.setPlainText("Saved through the header shortcut")
    window.pages.set_current_key("events")
    events.notes.setFocus()
    QTest.keyClick(events.notes, Qt.Key_S, Qt.ControlModifier)
    settle(app)
    path = root / "experiment" / "Test.config.json"
    assert load_config_bundle(path)["recording"]["notes"] == events.notes.toPlainText()
    assert "Saved config v8" in window.config_status.text()
    assert "individual pages may contain unsaved edits" in window.config_status.toolTip().lower()
    events.notes.setPlainText("Replace this draft by loading")
    monkeypatch.setattr(window, "_choose_config", lambda: path)
    QTest.keyClick(events.notes, Qt.Key_O, Qt.ControlModifier)
    settle(app)
    assert events.notes.toPlainText() == "Saved through the header shortcut"
    assert window.pages.current_key() == "events"
    assert {action.text().split("\t")[0] for action in window.header.menu.actions()} == {
        "Load experiment configuration", "Save experiment configuration", "Check configuration", "System check", "Check for updates"}


def test_update_action_preserves_unsaved_experiment_and_does_not_run_on_startup(workspace, monkeypatch):
    from deepecohab_recorder.ui.update_check import UpdateCheckDialog
    app, window, root = workspace
    assert window._update_check_dialog is None
    window.widgets["events"].notes.setPlainText("Unsaved experiment remains intact")
    before = copy.deepcopy(window.services.repo.data)
    requested = []
    monkeypatch.setattr(UpdateCheckDialog, "check", lambda self: requested.append(self))
    action = next(action for action in window.header.menu.actions() if action.text() == "Check for updates")
    action.trigger()
    settle(app)
    assert requested == [window._update_check_dialog]
    assert window._update_check_dialog.isVisible()
    assert window.services.repo.data == before
    assert window.widgets["events"].notes.toPlainText() == "Unsaved experiment remains intact"
    window._update_check_dialog.close()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_compact_navigation_header_stop_and_log_fit_small_windows(workspace, mode):
    app, window, root = workspace
    manager().set_mode(mode, persist=False)
    setup = window.widgets["setup"]
    setup.experiment_edit.setText("Experiment " + "long name ł " * 30)
    setup.directory_edit.setText(str(root / ("long folder " * 12)))
    window._sync_context()
    for size in ((640, 480), (800, 600), (1280, 800)):
        window.resize(*size)
        settle(app)
        assert window.size().toTuple() == size
        assert window.sidebar.collapsed == (size[0] < window.sidebar.COMPACT_BELOW)
        for key in window.sidebar.order:
            window.pages.set_current_key(key)
            settle(app)
            stop = window.header.stop_button
            assert stop.isVisible() and stop.isEnabled()
            assert window.header.rect().contains(stop.geometry())
            assert window.rect().contains(stop.mapTo(window, stop.rect().bottomRight()))
            assert window.log.isVisible() and window.log.height() >= 40
            assert window.pages.currentWidget().isVisible()
            if not PAGES[window.pages.currentIndex()].scrollable:
                area = window.pages.currentWidget()
                page = window.widgets[key]
                assert page.height() >= page.minimumSizeHint().height()
                if page.height() > area.viewport().height():
                    assert area.verticalScrollBar().isVisible() and area.verticalScrollBar().maximum() > 0
                    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
                    settle(app)
                    assert area.verticalScrollBar().value() == area.verticalScrollBar().maximum()
                    area.verticalScrollBar().setValue(0)
        assert window.header.experiment.text() == setup.experiment_edit.text().strip()
        assert window.header.directory.toolTip() == setup.directory_edit.text().strip()
    window.resize(640, 480)
    settle(app)
    window.sidebar.toggle.click()
    settle(app)
    assert window.sidebar.menu.isVisible()
    assert {action.text() for action in window.sidebar.menu.actions() if action.isCheckable()} == {spec.title for spec in PAGES}
    window.sidebar.menu_actions["preprocessing"].trigger()
    window.sidebar.menu.close()
    assert window.pages.current_key() == "preprocessing"


def test_manual_collapse_survives_restart_and_automatic_compact_does_not_change_preferences(workspace):
    app, window, root = workspace
    preferences.update_section("video_tools", {"ffmpeg": "retained", "future": 17})
    preferences.update_section("appearance", {"theme": "light"})
    preferences.update_section("navigation", {"future": "retained"})
    window.sidebar.toggle.click()
    settle(app)
    assert window.sidebar.collapsed
    before = preferences.preferences_path().read_bytes()
    for size in ((640, 480), (1280, 800)):
        window.resize(*size)
        settle(app)
        assert window.sidebar.collapsed
    assert before == preferences.preferences_path().read_bytes()
    second = MainWindow()
    try:
        second.resize(1280, 800)
        second.show()
        settle(app)
        assert second.sidebar.manual_collapsed and second.sidebar.collapsed
        assert second.pages.current_key() == "setup"
    finally:
        second.close()
        second.deleteLater()
        settle(app)
    window.sidebar.set_collapsed(False)
    data = preferences.read_preferences()
    assert data["navigation"] == {"collapsed": False, "future": "retained"}
    assert data["video_tools"] == {"ffmpeg": "retained", "future": 17}
    assert data["appearance"] == {"theme": "light"}
    assert "navigation" not in window.services.repo.data


def test_failed_collapse_save_retains_current_layout_and_preferences(workspace, monkeypatch):
    import deepecohab_recorder.ui.shell as shell
    app, window, root = workspace
    preferences.update_section("navigation", {"collapsed": False})
    before = preferences.preferences_path().read_bytes()
    monkeypatch.setattr(shell, "update_section", lambda *args: (_ for _ in ()).throw(OSError("locked preferences")))
    window.sidebar.toggle.click()
    settle(app)
    assert not window.sidebar.collapsed
    assert preferences.preferences_path().read_bytes() == before
    assert "locked preferences" in window.log.toPlainText()


@pytest.mark.parametrize("contents", ['{"format":"wrong"}', '{"format":"deepecohab_application_preferences","version":1,"navigation":{"collapsed":"yes"}}'])
def test_invalid_preferences_leave_navigation_usable_and_retain_the_file(workspace, contents):
    app, window, root = workspace
    path = preferences.preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    second = MainWindow()
    try:
        second.resize(1280, 800)
        second.show()
        settle(app)
        assert not second.sidebar.manual_collapsed
        assert second.sidebar.startup_error and "Navigation settings" in second.log.toPlainText()
        for key in second.sidebar.order:
            second.sidebar.buttons[key].click()
            assert second.pages.current_key() == key
        assert path.read_text(encoding="utf-8") == contents
    finally:
        second.close()
        second.deleteLater()
        settle(app)


def test_navigation_during_recording_preserves_run_and_pinned_stop_flushes(workspace, monkeypatch):
    app, window, root = workspace
    window.services.repo.save()
    factory = SerialFactory([b"A;D5969C1A;01\nB;D5969C1A;02\n"])
    monkeypatch.setitem(sys.modules, "serial", factory)
    window.start_devices(["board1"])
    window.services._future.result(timeout=10)
    controller = window.services.controllers["board1"]
    wait_for(lambda: sum(controller.get_tag_counts_snapshot().values()) >= 2, 5)
    session, run_id = controller._session, window.services.run.run_id
    for key in window.sidebar.order:
        window.pages.set_current_key(key)
        settle(app)
        assert controller.is_running() and window.services.busy("board1")
        assert controller._session is session and window.services.run.run_id == run_id
        assert not window.widgets["setup"].experiment_edit.isEnabled()
        assert not window.widgets["acquisition"].panels[1].hardware_combo.isEnabled()
        assert "RFID board 1: running" in window.header.activity.text()
    window.pages.set_current_key("events")
    window.resize(640, 480)
    settle(app)
    window.header.stop_button.click()
    window.services._future.result(timeout=10)
    settle(app)
    window.refresh()
    assert not controller.is_running() and not window.services.ports.snapshot()
    assert Path(controller.data_file).is_file()
    assert "D5969C1A" in Path(controller.data_file).read_text(encoding="utf-8")
    assert "All acquisition devices stopped" == window.header.activity.text()


def test_matching_ownership_survives_navigation_and_blocks_loading(workspace, monkeypatch):
    app, window, root = workspace
    monkeypatch.setitem(sys.modules, "serial", SerialFactory([b"A;D5969C1A;01\n"]))
    dialog = MatchDialog(window.ui, window)
    errors = []
    monkeypatch.setattr(window, "_error", lambda error: errors.append(str(error)))
    try:
        qt_wait(app, lambda: bool(window.services.ports.snapshot()))
        for key in ("camera", "setup", "events", "launch"):
            window.pages.set_current_key(key)
            settle(app)
            assert "Tag matching: COM5" in window.header.activity.text()
            assert not window.widgets["setup"].directory_edit.isEnabled()
        window.load_all()
        assert errors and "tag matching" in errors[-1]
        assert window.services.ports.snapshot()["COM5"] == "tag matching"
    finally:
        dialog.reject()
        qt_wait(app, lambda: not dialog.worker.is_running())
        dialog.poll()
        dialog.deleteLater()
    window.refresh()
    assert not window.services.ports.snapshot()
    assert window.widgets["setup"].directory_edit.isEnabled()


@pytest.mark.parametrize("finish", ["complete", "cancel", "close"])
def test_navigation_with_real_preprocessing_worker_completion_cancel_and_close(workspace, monkeypatch, finish):
    import deepecohab_recorder.tabs.data_preprocessing as preprocessing
    app, window, root = workspace
    window.save_all()
    tab = window.widgets["preprocessing"]
    raw = root / "COM5_synthetic.csv"
    raw.write_text("A-1;1A63A81904;0;2025-06-01T12:00:00+02:00;1\nB-1;1A63A81904;A;2025-06-01T12:00:00.100+02:00;1\n", encoding="utf-8")
    tab.add_paths([raw])
    assert tab.table.rowCount() == 1
    tab.output_path.setText(str(root / "processed"))
    tab.options.update(whitelist=False, interpolate=False)
    ready, release = root / "worker ready", root / "release worker"
    wrapper = root / "gated worker.py"
    wrapper.write_text("from pathlib import Path\nimport sys,time\n"
        + f"Path({str(ready)!r}).touch()\n"
        + f"while not Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        + "from deepecohab_recorder.preprocessing.worker import main\nraise SystemExit(main())\n", encoding="utf-8")
    def gated_start(process, arguments, directory):
        process.setWorkingDirectory(str(directory))
        process.start(worker_python(), [str(wrapper), *arguments])
    monkeypatch.setattr(preprocessing, "start_preprocessing", gated_start)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    errors = []
    monkeypatch.setattr(tab, "error", lambda exc: errors.append(str(exc)))
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    try:
        tab.start()
        assert not errors, errors
        qt_wait(app, ready.exists)
        job_directory = Path(tab._temporary.name)
        process_id = tab.process.processId()
        before = job_directory.joinpath("settings.json").read_bytes()
        for key in window.sidebar.order:
            window.pages.set_current_key(key)
            settle(app)
            assert tab.process.processId() == process_id and tab.background_busy()
            assert "Preprocessing task running" in window.header.activity.text()
            assert before == job_directory.joinpath("settings.json").read_bytes()
        assert ticks
        assert not tab.start_button.isEnabled() and tab.cancel_button.isEnabled()
        if finish == "close":
            window.close()
            assert window._closing and window.isVisible()
            assert tab._cancel_file.exists() and job_directory.exists()
        elif finish == "cancel":
            window.pages.set_current_key("preprocessing")
            tab.cancel_button.click()
            assert tab._cancel_file.exists()
        release.touch()
        qt_wait(app, lambda: not tab.background_busy(), 30)
        assert tab._job_result["kind"] == ("completed" if finish == "complete" else "cancelled"), tab.log.toPlainText()
        assert not job_directory.exists() and not errors
        if finish == "close":
            qt_wait(app, lambda: not window.isVisible())
        else:
            window.refresh()
            assert "Preprocessing task running" not in window.header.activity.text()
            assert tab.start_button.isEnabled() and not tab.cancel_button.isEnabled()
    finally:
        release.touch()
        timer.stop()
        tab.request_background_stop()
        qt_wait(app, lambda: not tab.background_busy(), 30)


def test_system_check_activity_and_errors_stay_visible_when_switching_pages(workspace):
    app, window, root = workspace
    window.show_system_check()
    dialog = window._system_check_dialog
    dialog.encode_test.setChecked(False)
    dialog.run_checks()
    window.pages.set_current_key("animals")
    window.refresh()
    assert window.system_check_busy() and "System check running" in window.header.activity.text()
    dialog.close()
    qt_wait(app, lambda: not window.system_check_busy(), 30)
    window.services.notify("error", "Synthetic device failure")
    for key in ("layout", "camera", "events"):
        window.pages.set_current_key(key)
        settle(app)
        assert "Synthetic device failure" in window.log.toPlainText()
        assert window.log.isVisible()
    assert window.config_status.text() == "Synthetic device failure"
    window.log.parentWidget().setSizes([10000, 0])
    settle(app)
    assert window.log.height() >= 40
    window.log.parentWidget().setSizes([0, 10000])
    settle(app)
    assert window.pages.height() >= 80
