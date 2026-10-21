"""Installation boundaries: windowed errors, worker IPC and native shortcuts."""
import logging
import builtins
from pathlib import Path
import sys
import sysconfig

import pytest

from deepecohab_recorder import app, desktop, __version__
from deepecohab_recorder.models import ExperimentContext
from deepecohab_recorder.runtime_paths import default_experiment_directory, worker_python


def test_default_experiment_location_does_not_follow_shortcut_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Path(ExperimentContext().save_directory) == default_experiment_directory()
    assert Path(ExperimentContext().save_directory) != tmp_path


@pytest.fixture
def unavailable_qt(monkeypatch):
    original = builtins.__import__
    def importing(name, *args, **kwargs):
        if name.startswith('PySide6'):
            raise ModuleNotFoundError('PySide6 unavailable in simulated broken installation')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', importing)


def test_version_works_without_loading_qt(unavailable_qt, capsys):
    assert app.main(['--version']) == 0
    assert __version__ in capsys.readouterr().out


def test_startup_failure_is_logged_without_a_console(tmp_path, monkeypatch, unavailable_qt):
    monkeypatch.setenv('DEEPECOHAB_ACQUISITION_HOME', str(tmp_path / 'application'))
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    previous_hook = sys.excepthook
    logger = logging.getLogger('deepecohab_recorder')
    existing = list(logger.handlers)
    for handler in existing:
        logger.removeHandler(handler)
    try:
        result = tmp_path / 'failed.json'
        assert app.main(['--smoke-test', str(result)]) == 1
        assert 'could not start' in result.read_text(encoding='utf-8')
        log = tmp_path / 'application/logs/application.log'
        assert 'ModuleNotFoundError' in log.read_text(encoding='utf-8')
        assert sys.excepthook is previous_hook
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        for handler in existing:
            logger.addHandler(handler)


def test_windowed_worker_uses_console_python_in_same_environment(tmp_path, monkeypatch):
    console = tmp_path / 'environment with spaces/python.exe'
    console.parent.mkdir()
    console.touch()
    monkeypatch.setattr(sys, 'executable', str(console.with_name('pythonw.exe')))
    assert worker_python() == str(console)
    console.unlink()
    with pytest.raises(RuntimeError, match='Worker Python is missing'):
        worker_python()


def test_all_pages_and_worker_completion_cancellation_from_other_cwd(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from deepecohab_recorder.main_window import MainWindow
    from deepecohab_recorder.installation_check import check_installation
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setenv('DEEPECOHAB_ACQUISITION_HOME', str(tmp_path / 'application-data'))
    monkeypatch.chdir(tmp_path)
    window = MainWindow()
    try:
        report = check_installation(window)
        assert len(report['pages']) == 10
        assert report['workers'][-1]['kind'] == 'cancelled'
        assert report['workers'][-1]['exit_code'] == 2
        assert report['appearance']['fonts'] == ['Geist', 'Geist Mono']
        assert report['appearance']['modes'] == ['dark', 'light']
    finally:
        window.close()
        window.deleteLater()
        qt.processEvents()


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows shortcuts')
def test_shortcut_unicode_spaces_update_remove_preserve_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPECOHAB_ACQUISITION_HOME', str(tmp_path / 'app data ł漢字'))
    # Use an actual installed GUI wrapper on the first creation.
    launcher = Path(sysconfig.get_path('scripts')) / desktop.COMMAND
    assert launcher.is_file()
    folder = tmp_path / 'Desktop with spaces ł漢字'
    link = desktop.change_shortcuts(directory=folder)[0]
    saved = desktop.shell_link(link)
    assert Path(saved.GetPath(0)[0]).samefile(launcher)
    assert Path(saved.GetWorkingDirectory()).samefile(tmp_path / 'app data ł漢字')
    assert saved.GetArguments() == ''
    assert Path(saved.GetIconLocation()[0]).is_file()
    sentinel = tmp_path / 'app data ł漢字/user-data.txt'
    sentinel.write_text('keep', encoding='utf-8')
    updated = tmp_path / 'new environment with spaces ł漢字' / desktop.COMMAND
    updated.parent.mkdir()
    updated.touch()
    monkeypatch.setattr(desktop, 'installed_launcher', lambda: updated)
    assert desktop.change_shortcuts(directory=folder) == [link]
    assert Path(desktop.shell_link(link).GetPath(0)[0]).samefile(updated)
    desktop.change_shortcuts(directory=folder, remove=True)
    assert not link.exists()
    assert sentinel.read_text(encoding='utf-8') == 'keep'
    desktop.change_shortcuts(directory=folder)
    assert sentinel.read_text(encoding='utf-8') == 'keep'


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows shortcuts')
@pytest.mark.parametrize('remove', [False, True])
def test_shortcut_conflict_does_not_overwrite_or_remove_another_app(tmp_path, monkeypatch, remove):
    import pythoncom
    monkeypatch.setenv('DEEPECOHAB_ACQUISITION_HOME', str(tmp_path / 'app data'))
    folder = tmp_path / 'Desktop ł漢字'
    folder.mkdir()
    link = folder / f'{desktop.APP_NAME}.lnk'
    foreign = desktop.shell_link()
    foreign.SetPath(str(Path(sys.executable)))
    foreign.SetDescription('Another application')
    foreign.QueryInterface(pythoncom.IID_IPersistFile).Save(str(link), 0)
    before = link.read_bytes()
    with pytest.raises(FileExistsError, match='different shortcut'):
        desktop.change_shortcuts(directory=folder, remove=remove)
    assert link.read_bytes() == before
