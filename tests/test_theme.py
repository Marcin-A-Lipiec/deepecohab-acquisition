"""Appearance changes must preserve scientific edits, live controls and offline use."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from deepecohab_recorder import preferences, video_tools
from deepecohab_recorder.runtime_paths import asset
from deepecohab_recorder.ui import theme
from tests.helpers import SerialFactory, valid_config, wait_for


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPECOHAB_ACQUISITION_HOME', str(tmp_path / 'appearance settings with spaces ł'))


@pytest.fixture
def qt():
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    manager = theme.manager()
    original = manager.mode
    manager.set_mode('light', persist=False)
    yield app, manager
    manager.set_mode(original, persist=False)
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def window(qt, tmp_path):
    from deepecohab_recorder.main_window import MainWindow
    app, manager = qt
    window = MainWindow()
    window.widgets['setup'].directory_edit.setText(str(tmp_path / 'experiment'))
    config = valid_config()
    window.services.repo.replace(config)
    window._apply(config)
    window._sync_context()
    window.resize(1280, 800)
    window.show()
    for _ in range(6):
        app.processEvents()
    yield window
    window.services.stop_devices()
    wait_for(lambda: not window.services.busy(), 10)
    window.close()
    window.deleteLater()
    app.processEvents()


def startup_theme():
    """Use a fresh process to exercise saved startup preferences and resource loading."""
    code = '''import json
from PySide6.QtWidgets import QApplication
from deepecohab_recorder.ui.theme import manager
app = QApplication([])
m = manager()
print(json.dumps(dict(mode=m.mode, effective=m.effective, fonts=[m.sans,m.mono], error=m.preference_error)))
'''
    environment = os.environ.copy()
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run([sys.executable, '-c', code], env=environment,
        capture_output=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_appearance_and_video_settings_survive_independent_updates_and_restart(qt, tmp_path):
    app, manager = qt
    executable = tmp_path / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    executable.touch()
    executable.chmod(0o755)
    video_tools.save_video_paths(ffmpeg=str(executable))
    preferences.update_section('appearance', {'future_setting': 'retain me'})
    preferences.update_section('another_feature', {'value': 17})
    manager.set_mode('dark')
    video_tools.save_video_paths(ffmpeg=str(executable))
    data = preferences.read_preferences()
    assert data['appearance'] == {'future_setting': 'retain me', 'theme': 'dark'}
    assert data['another_feature'] == {'value': 17}
    assert video_tools.resolve_video_tools()['ffmpeg'].require() == str(executable.resolve())
    restarted = startup_theme()
    assert restarted['mode'] == restarted['effective'] == 'dark'
    assert restarted['fonts'] == ['Geist', 'Geist Mono']


@pytest.mark.parametrize('contents', ['broken JSON', '[]', '{}',
    '{"format":"deepecohab_application_preferences","version":2}'])
def test_corrupt_preferences_use_system_without_overwriting_the_file(contents, qt):
    app, manager = qt
    path = preferences.preferences_path()
    path.parent.mkdir(parents=True)
    path.write_text(contents, encoding='utf-8')
    result = startup_theme()
    assert result['mode'] == 'system' and result['error']
    before = manager.mode
    with pytest.raises(preferences.PreferencesError):
        manager.set_mode('dark')
    assert manager.mode == before
    assert path.read_text(encoding='utf-8') == contents


def test_system_mode_follows_desktop_changes_and_explicit_modes_remain_fixed(qt, monkeypatch):
    app, manager = qt
    scheme = ['light']
    monkeypatch.setattr(theme, 'system_theme', lambda: scheme[0])
    manager.set_mode('system')
    scheme[0] = 'dark'
    manager.refresh_system()
    assert manager.effective == 'dark'
    manager.set_mode('light')
    manager.refresh_system()
    assert manager.effective == 'light'
    manager.set_mode('system')
    assert manager.effective == 'dark'
    assert startup_theme()['mode'] == 'system'


def test_failed_preference_write_keeps_the_current_theme_and_selector(window, qt, monkeypatch):
    app, manager = qt
    def fail(*args):
        raise OSError('simulated read-only preferences directory')
    monkeypatch.setattr(preferences, 'atomic_write_json', fail)
    window.theme_selector.setCurrentIndex(window.theme_selector.findData('dark'))
    assert manager.mode == manager.effective == 'light'
    assert window.theme_selector.currentData() == 'light'
    assert 'read-only' in window.log.toPlainText()


def test_theme_switch_preserves_unsaved_pages_graph_selection_and_camera_image(window, qt):
    import numpy as np
    from PySide6.QtCore import QPointF
    app, manager = qt
    animals = window.widgets['animals']
    animals.table.item(0, 2).setText('Unsaved mouse name')
    events = window.widgets['events']
    events.notes.setPlainText('Unsaved note: ł')
    window.widgets['setup'].project_edit.setText('Unsaved project')
    layout = window.widgets['layout']
    window.tabs.setCurrentIndex(3)
    for _ in range(6):
        app.processEvents()
    layout.view.fitted = False
    layout.view.scale(1.15, 1.15)
    layout.graph['editor']['label_offsets']['tunnel_1'] = [30, -40]
    layout.draw()
    cage = next(item for key, item in layout._items.items() if key.startswith('cage:'))
    cage.setSelected(True)
    window.widgets['camera'].trigger_delay_spin.setValue(0)
    frame = np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)
    window.widgets['camera'].update_preview(SimpleNamespace(
        status_text=lambda: 'Synthetic preview', get_preview_frame=lambda: frame))
    image = window.widgets['camera'].preview_label.pixmap().toImage()
    before = copy.deepcopy({key: widget.read() for key, widget in window.widgets.items()})
    repository = copy.deepcopy(window.services.repo.data)
    pages = {key: id(widget) for key, widget in window.widgets.items()}
    scene_items = {key: id(item) for key, item in layout._items.items()}
    view_transform = layout.view.transform()
    positions = {key: QPointF(item.pos()) for key, item in layout._items.items()}
    for mode in ('dark', 'light', 'dark'):
        window.theme_selector.setCurrentIndex(window.theme_selector.findData(mode))
        for _ in range(6):
            app.processEvents()
        assert window.tabs.currentIndex() == 3
        assert pages == {key: id(widget) for key, widget in window.widgets.items()}
        assert scene_items == {key: id(item) for key, item in layout._items.items()}
        assert before == {key: widget.read() for key, widget in window.widgets.items()}
        assert repository == window.services.repo.data
        assert layout.view.transform() == view_transform
        assert positions == {key: item.pos() for key, item in layout._items.items()}
        assert cage.isSelected()
        assert image == window.widgets['camera'].preview_label.pixmap().toImage()
    assert preferences.read_preferences()['appearance']['theme'] == 'dark'
    assert 'appearance' not in window.services.repo.data


def test_theme_changes_leave_a_simulated_recording_running_and_stop_controls_available(window, qt, monkeypatch):
    app, manager = qt
    window.services.repo.save()
    factory = SerialFactory([b'A;D5969C1A;01\nB;D5969C1A;02\n'])
    monkeypatch.setitem(sys.modules, 'serial', factory)
    window.start_devices(['board1'])
    window.services._future.result(timeout=10)
    controller = window.services.controllers['board1']
    wait_for(lambda: sum(controller.get_tag_counts_snapshot().values()) >= 2, 5)
    state, session = window.services.run.run_id, controller._session
    for mode in ('dark', 'light'):
        manager.set_mode(mode)
        window.refresh()
        app.processEvents()
        assert window.services.busy('board1') and controller.is_running()
        assert window.services.run.run_id == state and controller._session is session
        assert not window.widgets['acquisition'].panels[1].hardware_combo.isEnabled()
        assert window.widgets['launch'].stops['board1'].isEnabled()
        assert sum(controller.get_tag_counts_snapshot().values()) >= 2
    window.stop_devices()
    window.services._future.result(timeout=10)
    assert not controller.is_running()


def test_active_indicator_recolors_without_resetting_status_or_counts(window, qt):
    app, manager = qt
    panel = window.widgets['acquisition'].panels[1]
    controller = SimpleNamespace(status_text=lambda: 'Running: test data',
        get_status_snapshot=lambda: {'A': {'t': time.monotonic()}},
        get_tag_counts_snapshot=lambda: {'TEST': 19})
    panel._last_totals_update = 0
    panel.update_from_controller(controller)
    led = panel._rfid_labels['A']
    assert led.property('activity')
    for mode in ('dark', 'light'):
        manager.set_mode(mode, persist=False)
        assert theme.TOKENS[mode]['good'] in led.styleSheet()
        assert led.property('activity')
        assert panel.status_label.text() == 'Running: test data'
        assert panel.tag_table.item(0, 1).text() == '19'


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_all_pages_remain_reachable_at_small_and_large_sizes(window, qt, mode):
    app, manager = qt
    manager.set_mode(mode, persist=False)
    setup = window.widgets['setup']
    setup.directory_edit.setText(str(Path(setup.directory_edit.text()) / ('long_folder_name_' * 8)))
    window._sync_context()
    for size in ((800, 600), (1280, 800)):
        window.resize(*size)
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            for _ in range(8):
                app.processEvents()
            assert window.tabs.currentIndex() == index and window.tabs.currentWidget().isVisible()
            assert not window.tabs.tabIcon(index).isNull()
            if index == 0:
                assert setup.width() <= window.tabs.currentWidget().viewport().width()
            if index == 3:
                layout = window.widgets['layout']
                view = layout.view
                assert view.viewport().rect().contains(view.content_bounds().toAlignedRect())
                assert layout.rect().contains(view.geometry())
        assert window.minimumWidth() <= 800


def test_bundled_resources_have_recorded_hashes_and_qt_loads_the_fonts_and_icons(qt):
    from PySide6.QtGui import QFontInfo, QIcon
    app, manager = qt
    manifest = json.loads(asset('theme_resources.json').read_text(encoding='utf-8'))
    for entry in manifest['resources']:
        assert hashlib.sha256(asset(entry['file']).read_bytes()).hexdigest() == entry['sha256']
    assert QFontInfo(theme.font()).family() == 'Geist'
    assert QFontInfo(theme.font(mono=True)).family() == 'Geist Mono'
    for mode in ('light', 'dark'):
        manager.set_mode(mode, persist=False)
        for name in ('users', 'grid-dots', 'calendar', 'circle-x'):
            symbol = theme.icon(name)
            assert not symbol.isNull()
            assert not symbol.pixmap(20, QIcon.Disabled).isNull()


def test_missing_font_files_fall_back_to_desktop_fonts_without_stopping_startup(qt, monkeypatch, caplog):
    from PySide6.QtGui import QFont
    app, manager = qt
    original_asset = theme.asset
    def missing(name):
        if name.startswith('fonts/'):
            raise OSError('synthetic missing font file')
        return original_asset(name)
    monkeypatch.setattr(theme, 'asset', missing)
    fonts = theme.load_fonts(SimpleNamespace(font=lambda: QFont('Arial')))
    assert len(fonts) == 2 and all(fonts)
    assert 'desktop fallback' in caplog.text


def test_calendar_selection_and_numeric_arrow_controls_work_in_both_themes(qt):
    from PySide6.QtCore import QDate, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QSpinBox, QStyleOptionSpinBox, QStyle
    app, manager = qt
    dialog = QDialog()
    row = QVBoxLayout(dialog)
    calendar = theme.ThemedCalendar()
    calendar.setSelectedDate(QDate(2030, 3, 31))
    spin = QSpinBox()
    spin.setValue(40)
    row.addWidget(calendar)
    row.addWidget(spin)
    dialog.show()
    try:
        for mode, expected in (('light', 41), ('dark', 42)):
            manager.set_mode(mode, persist=False)
            app.processEvents()
            assert calendar.selectedDate() == QDate(2030, 3, 31)
            assert calendar.weekdayTextFormat(Qt.Sunday).foreground().color() == theme.color('bad')
            option = QStyleOptionSpinBox()
            spin.initStyleOption(option)
            target = spin.style().subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, spin)
            QTest.mouseClick(spin, Qt.LeftButton, pos=target.center())
            assert spin.value() == expected
    finally:
        dialog.close()
        dialog.deleteLater()


def contrast(a, b):
    def luminance(value):
        channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_body_action_and_semantic_text_have_readable_contrast(mode):
    c = theme.TOKENS[mode]
    for foreground, background in (('ink', 'surface'), ('ink-2', 'ground'),
        ('accent-ink', 'accent'), ('accent-text', 'accent-soft'), ('good', 'surface'),
        ('warn', 'surface'), ('bad', 'bad-soft'), ('device', 'surface'), ('preview-ink', 'preview')):
        assert contrast(c[foreground], c[background]) >= 4.5, (mode, foreground, background)
