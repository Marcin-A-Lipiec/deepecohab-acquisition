"""Readiness boundaries: explicit tools, optional SDK, isolated failures and GUI IPC."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from deepecohab_recorder import cli, video_tools
from deepecohab_recorder.services import dependencies as checks
from deepecohab_recorder.services import dependency_probe


@pytest.fixture(autouse=True)
def application_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPECOHAB_ACQUISITION_HOME', str(tmp_path / 'application data ł'))


def fake_executable(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + ('.exe' if os.name == 'nt' else ''))
    path.touch()
    path.chmod(0o755)
    return str(path)


def test_saved_unicode_paths_and_sibling_probe_work_without_path(tmp_path, monkeypatch):
    encoder = fake_executable(tmp_path / 'video tools ł with spaces', 'ffmpeg')
    probe = fake_executable(Path(encoder).parent, 'ffprobe')
    monkeypatch.setenv('PATH', '')
    video_tools.save_video_paths(ffmpeg=encoder)
    result = video_tools.resolve_video_tools()
    assert result['ffmpeg'].require() == encoder
    assert result['ffmpeg'].source == 'configured'
    assert result['ffprobe'].require() == probe
    assert result['ffprobe'].source == 'beside FFmpeg'


def test_invalid_explicit_path_never_uses_another_program(tmp_path, monkeypatch):
    available = fake_executable(tmp_path, 'ffmpeg')
    monkeypatch.setattr(video_tools.shutil, 'which', lambda name: available)
    result = video_tools.resolve_video_tools(ffmpeg=str(tmp_path / 'missing.exe'))
    assert result['ffmpeg'].source == 'configured'
    with pytest.raises(video_tools.VideoToolError, match='Executable not found'):
        result['ffmpeg'].require()


def test_machine_paths_preserve_other_preferences_and_allow_reset(tmp_path):
    executable = fake_executable(tmp_path, 'ffmpeg')
    video_tools.save_video_paths(ffmpeg=executable)
    path = video_tools.preferences_path()
    data = json.loads(path.read_text(encoding='utf-8'))
    data['future_feature'] = {'value': 7}
    path.write_text(json.dumps(data), encoding='utf-8')
    video_tools.save_video_paths()
    assert video_tools.read_preferences()['future_feature'] == {'value': 7}
    assert video_tools.read_preferences()['video_tools'] == {'ffmpeg': '', 'ffprobe': ''}


@pytest.mark.parametrize('data', ['broken JSON', '[]', '{}',
    '{"format":"deepecohab_application_preferences","version":2}',
    '{"format":"deepecohab_application_preferences","version":1,"video_tools":{"ffmpeg":12}}'])
def test_corrupt_preferences_are_reported_and_retained(data):
    path = video_tools.preferences_path()
    path.parent.mkdir(parents=True)
    path.write_text(data, encoding='utf-8')
    with pytest.raises(video_tools.VideoToolError, match='Cannot read'):
        video_tools.save_video_paths()
    assert path.read_text(encoding='utf-8') == data


def test_relative_tool_path_is_rejected():
    with pytest.raises(video_tools.VideoToolError, match='absolute executable path'):
        video_tools.save_video_paths(ffmpeg='ffmpeg.exe')


@pytest.fixture
def simulated_machine(monkeypatch):
    core = {name: {'ok': True, 'version': '1'} for name in
        ('PySide6.QtWidgets', 'serial', 'pyarrow', 'tzdata', 'platformdirs')}
    camera = {'imports': {'numpy': {'ok': True, 'version': '1.26.4'},
        'cv2': {'ok': True, 'version': '4.11.0'},
        'PySpin': {'ok': False, 'error': 'ModuleNotFoundError: No module named PySpin'}}}
    machine = SimpleNamespace(core=core, camera=camera, probes=[], encoders=[])
    def imports(kind, detect=False):
        machine.probes.append((kind, detect))
        return copy.deepcopy(getattr(machine, kind))
    monkeypatch.setattr(checks, 'isolated_imports', imports)
    monkeypatch.setattr(checks, 'resolve_video_tools', lambda **kwargs: {
        name: video_tools.Executable(name, name, 'test') for name in ('ffmpeg', 'ffprobe')})
    def command(argv, **kwargs):
        if '-version' in argv:
            return dict(ok=True, stdout=argv[0] + ' version test', stderr='')
        return dict(ok=True, stdout='\n'.join(' V..... ' + name for name in
            ('hevc_nvenc', 'libx264', 'libx265', 'ffv1')), stderr='')
    monkeypatch.setattr(checks, 'run_command', command)
    def encoder(ffmpeg, ffprobe, option):
        machine.encoders.append(option.label)
        return dict(ok='NVENC' not in option.label, error='Simulated NVENC driver failure', frame_count_verified=True)
    monkeypatch.setattr(checks, 'test_encoder', encoder)
    import serial.tools.list_ports
    monkeypatch.setattr(serial.tools.list_ports, 'comports', lambda: [])
    return machine


def test_missing_sdk_does_not_block_rfid_preprocessing_or_cpu_fallback(tmp_path, simulated_machine):
    report = checks.collect_checks(output_directory=tmp_path, encode_test=True)
    assert report['core_ready']
    assert report['capabilities']['rfid_software']
    assert report['capabilities']['preprocessing']
    assert not report['capabilities']['camera_software']
    assert report['capabilities']['selected_video_encoding']
    assert len(simulated_machine.encoders) == 5
    assert not report['physical_acquisition_started']


def test_camera_required_exit_is_separate_from_base_readiness(tmp_path, simulated_machine, capsys):
    args = ['doctor', '--output-dir', str(tmp_path), '--json']
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)['core_ready']
    assert cli.main(args + ['--require-camera']) == 1
    assert not json.loads(capsys.readouterr().out)['capabilities']['camera_software']


def test_deferred_checks_do_not_import_sdk_or_encode(tmp_path, simulated_machine):
    report = checks.collect_checks(output_directory=tmp_path, encode_test=True,
        camera_devices=True, skip_camera=True, skip_video_tests=True)
    assert simulated_machine.probes == [('core', False)]
    assert simulated_machine.encoders == []
    assert report['capabilities']['camera_software'] is None
    assert report['capabilities']['selected_video_encoding'] is None


@pytest.mark.parametrize(('wheel', 'sdk', 'ready'), [('4.2.0.1', '4.3.0.1', False),
    ('4.3.0.1', '4.3.0.2', True), ('4.3.0.1', '4.3.0.1', True)])
def test_vendor_release_family_mismatch_is_distinct_from_build_review(tmp_path, simulated_machine, wheel, sdk, ready):
    simulated_machine.camera['imports']['PySpin'] = {'ok': True}
    simulated_machine.camera.update(wheel_version=wheel, sdk_version=sdk)
    report = checks.collect_checks(output_directory=tmp_path)
    assert report['capabilities']['camera_software'] is ready
    assert report['core_ready']


def test_native_camera_failure_is_reported_without_blocking_core(tmp_path, simulated_machine):
    simulated_machine.camera = dict(probe_error='Native probe exited without a report. Exit code: -1073741819.',
                                    return_code=-1073741819)
    report = checks.collect_checks(output_directory=tmp_path)
    assert report['core_ready'] and not report['capabilities']['camera_software']
    assert '-1073741819' in next(c for c in report['checks'] if c['key'] == 'camera')['detail']


def test_output_probe_checks_parent_without_creating_experiment(tmp_path):
    target = tmp_path / 'new experiment' / 'recording'
    report = checks.output_readiness(target)
    assert report['writable'] and not report['exists']
    assert not (tmp_path / 'new experiment').exists()
    assert not list(tmp_path.iterdir())


def test_output_file_conflict_and_low_space_are_actionable(tmp_path, simulated_machine, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_text('preserve', encoding='utf-8')
    assert not checks.output_readiness(path / 'child')['writable']
    assert path.read_text() == 'preserve'
    monkeypatch.setattr(checks.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10))
    report = checks.collect_checks(output_directory=tmp_path)
    assert not report['core_ready'] and not report['capabilities']['output']


def test_cancellation_terminates_the_child_and_restores_next_check(monkeypatch):
    real_popen = subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(checks.subprocess, 'Popen', popen)
    started = time.monotonic()
    token = checks._cancelled.set(lambda: time.monotonic() - started > 0.4)
    try:
        with pytest.raises(checks.CheckCancelled):
            checks.run_command([sys.executable, '-c', 'import time; time.sleep(30)'])
    finally:
        checks._cancelled.reset(token)
    assert children[0].poll() is not None
    assert time.monotonic() - started < 3
    assert checks.run_command([sys.executable, '-c', 'print(42)'])['stdout'].strip() == '42'


def test_hanging_native_probe_times_out_and_releases_child():
    started = time.monotonic()
    result = checks.run_command([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.3)
    assert not result['ok'] and 'timed out' in result['error']
    assert time.monotonic() - started < 3


def test_probe_not_reading_frame_input_still_times_out(monkeypatch):
    real_popen = subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(checks.subprocess, 'Popen', popen)
    started = time.monotonic()
    result = checks.run_command([sys.executable, '-c', 'import time; time.sleep(30)'],
                               payload=bytes(1024 * 1024), timeout=0.3)
    assert not result['ok'] and 'timed out' in result['error']
    assert children[0].poll() is not None
    assert time.monotonic() - started < 3


def test_abrupt_native_probe_exit_is_contained(monkeypatch):
    real_run = checks.run_command
    monkeypatch.setattr(checks, 'run_command', lambda *args, **kwargs:
        real_run([sys.executable, '-c', 'import os; os._exit(7)']))
    report = checks.isolated_imports('camera')
    assert report['return_code'] == 7
    assert 'Exit code: 7' in report['probe_error']


def test_pyspin_enumeration_releases_sdk_without_initializing_camera(monkeypatch):
    calls = []
    cameras = SimpleNamespace(GetSize=lambda: 2, Clear=lambda: calls.append('clear'))
    system = SimpleNamespace(GetLibraryVersion=lambda: SimpleNamespace(major=4, minor=3, type=0, build=1),
        GetCameras=lambda: cameras, ReleaseInstance=lambda: calls.append('release'))
    monkeypatch.setitem(sys.modules, 'PySpin', SimpleNamespace(System=SimpleNamespace(GetInstance=lambda: system)))
    report = dependency_probe.camera(detect=True)
    assert report['device_count'] == 2 and report['devices_checked']
    assert report['sdk_version'] == '4.3.0.1'
    assert calls == ['clear', 'release']


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Real video executables required')
def test_production_recording_freezes_explicit_paths_without_path(tmp_path, monkeypatch):
    from deepecohab_recorder.models import CameraConfig
    from tests.test_camera_recovery import SyntheticCamera
    from tests.helpers import snapshot, wait_for
    from deepecohab_recorder.maintenance.verify_video import verify
    encoder, probe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    video_tools.save_video_paths(encoder, probe)
    monkeypatch.setenv('PATH', '')
    controller = SyntheticCamera(CameraConfig(width=64, height=64, fps=30, encoding='CPU H.264 emergency fallback'), frame_queue_size=32)
    recording = tmp_path / 'experiment ł with spaces'
    try:
        controller.start(snapshot(recording), False)
        # Preferences change after start: the running encoder/count checks retain
        # the executable pair, including through writer drain and close.
        other = fake_executable(tmp_path / 'other tools', 'other')
        video_tools.save_video_paths(other, other)
        assert Path(controller._encoder_executable()) == Path(encoder)
        wait_for(lambda: controller._capture_done.is_set(), 10)
        controller.stop(12)
        assert not controller.is_running(), controller.last_error
        assert controller.frames_written == 30, controller.last_error
        report = verify(recording / 'Test.acquisition_gui_state.json',
            controller._session.file_prefix + ':camera_inventory', ffmpeg=encoder, ffprobe=probe)
        assert report['ok'], report
        inventory = json.loads((recording / 'Test.acquisition_gui_state.json').read_text())['state']['acquisition']['device_history']
        assert inventory[controller._session.file_prefix + ':camera_inventory']['parts'][0]['container_count_matches']
    finally:
        controller.stop(12)


def qt_wait(qt, predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('Qt check worker did not finish')


@pytest.fixture
def check_window(tmp_path):
    from PySide6.QtWidgets import QApplication
    from deepecohab_recorder.main_window import MainWindow
    qt = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.widgets['setup'].directory_edit.setText(str(tmp_path))
    window._sync_context()
    window.show_system_check()
    dialog = window._system_check_dialog
    yield qt, window, dialog
    dialog.request_background_stop()
    qt_wait(qt, lambda: not dialog.background_busy())
    dialog.close()
    window.close()
    window.deleteLater()
    qt.processEvents()


def test_gui_worker_keeps_timer_alive_and_scientific_config_unchanged(check_window):
    from PySide6.QtCore import QTimer
    qt, window, dialog = check_window
    before = copy.deepcopy(window.services.repo.data)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    dialog.encode_test.setChecked(False)
    dialog.run_checks()
    qt_wait(qt, lambda: not dialog.background_busy())
    timer.stop()
    assert ticks
    assert dialog._report['core_ready'], dialog.status.text()
    assert dialog.table.rowCount() > 10
    assert before == window.services.repo.data
    assert window.tabs.count() == 10


def test_active_work_defers_camera_encoder_probes_and_reports_owned_ports(check_window):
    qt, window, dialog = check_window
    window.services.states['camera'] = 'Running'
    window.services.ports.acquire('COM88', 'tag matching')
    try:
        dialog.update_activity()
        assert not dialog.detect_cameras.isEnabled()
        assert not dialog.encode_test.isEnabled()
        assert not dialog.save_paths.isEnabled()
        dialog.run_checks()
        qt_wait(qt, lambda: not dialog.background_busy())
        assert dialog._report['camera'] == {'skipped': True}
        assert dialog._report['owned_ports']['COM88'] == 'tag matching'
        assert all(not profile['tested'] for profile in dialog._report['encoders'].values())
    finally:
        window.services.states['camera'] = 'Stopped'
        window.services.ports.release('COM88', 'tag matching')


def test_closing_system_dialog_cancels_worker_before_releasing_files(check_window):
    qt, window, dialog = check_window
    dialog.run_checks()
    directory = Path(dialog._temporary.name)
    dialog.close()
    assert dialog.background_busy()
    qt_wait(qt, lambda: not dialog.background_busy())
    assert not directory.exists()
    assert 'cancelled' in dialog.status.text().lower()


def test_closing_main_window_drains_system_check_without_acquisition_prompt(check_window):
    qt, window, dialog = check_window
    dialog.run_checks()
    window.close()
    assert window._closing
    qt_wait(qt, lambda: not dialog.background_busy() and not window.timer.isActive())
    assert not window.system_check_busy()
