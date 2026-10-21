"""Validate an isolated uv tool installation from outside the source directory.

No hardware is started and no shortcut is created on the user's real Desktop.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deepecohab_recorder import APP_NAME, __version__


def windows_subsystem(path):
    data = path.read_bytes()
    if data[:2] != b'MZ':
        raise AssertionError(f'Not a Windows executable: {path}')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    assert data[pe:pe + 4] == b'PE\x00\x00'
    return struct.unpack_from('<H', data, pe + 24 + 68)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/current/installed')
    parser.add_argument('--wheel', type=Path)
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('This acceptance helper targets Windows launchers and shortcuts.')
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    scripts = args.tool_root.resolve() / 'deepecohab-acquisition/Scripts'
    launcher = scripts / 'deepecohab-acquisition.exe'
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['DEEPECOHAB_ACQUISITION_HOME'] = str(directory / 'app data ł')
    environment['PYTHONIOENCODING'] = 'utf-8'
    outside = directory / 'outside source with spaces ł'
    outside.mkdir(exist_ok=True)
    operations = []

    def run(name, command, *, expected=0):
        result = subprocess.run([str(value) for value in command], cwd=outside,
                                env=environment, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=120)
        (directory / f'{name}.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
        operations.append(dict(operation=name, exit_code=result.returncode))
        if result.returncode != expected:
            raise AssertionError(f'{name} failed: {result.stdout}\n{result.stderr}')
        return result

    def smoke(name, target):
        path = directory / f'{name}.json'
        path.unlink(missing_ok=True)
        run(name, [target, '--smoke-test', path])
        result = json.loads(path.read_text(encoding='utf-8'))
        assert result['version'] == __version__
        assert Path(result['package']).resolve() != (ROOT / 'deepecohab_recorder').resolve()
        assert 'site-packages' in result['package']
        assert len(result['pages']) == 10
        assert result['window_icon_present']
        assert Path(result['worker_python']).parent == scripts
        assert Path(result['worker_python']).name.lower() == 'python.exe'
        assert [(job['kind'], job['exit_code']) for job in result['workers']] == [
            ('inventory_completed', 0), ('completed', 0), ('cancelled', 2)]
        assert result['system_check']['core_ready']
        assert result['system_check']['software_version'] == __version__
        assert result['system_check']['progress_timer_ticks'] > 0
        assert result['system_check']['help_packaged']
        assert not result['system_check']['physical_acquisition_started']
        assert result['appearance']['modes'] == ['dark', 'light']
        assert result['appearance']['fonts'] == ['Geist', 'Geist Mono']
        assert result['appearance']['icons_packaged']
        assert len(result['navigation']['page_keys']) == 10
        assert result['navigation']['mounted_once']
        assert result['navigation']['stop_always_visible']
        assert result['navigation']['compact_picker']
        assert all(result['display_optimizations'].values())
        return result

    report = dict(version=__version__, physical_hardware_tested=False,
                  tool_root=str(args.tool_root.resolve()), operations=operations)
    try:
        report['gui_subsystem'] = windows_subsystem(launcher)
        assert report['gui_subsystem'] == 2  # IMAGE_SUBSYSTEM_WINDOWS_GUI
        report['direct_launch'] = smoke('installed-smoke', launcher)
        run('console-version', [scripts / 'deepecohab-acquisition-cli.exe', '--version'])
        report['paths'] = json.loads(run('installed-paths', [scripts / 'deepecohab-acquisition-cli.exe', 'paths']).stdout)
        report['doctor'] = json.loads(run('installed-doctor', [scripts / 'deepecohab-acquisition-cli.exe',
            'doctor', '--json', '--encode-test', '--output-dir', outside]).stdout)
        assert report['doctor']['software_version'] == __version__
        assert report['doctor']['core_ready']
        shortcut_command = scripts / 'deepecohab-acquisition-shortcut.exe'
        shortcut_directory = directory / 'Desktop with spaces ł'
        run('shortcut-create', [shortcut_command, '--directory', shortcut_directory])
        import win32com.client
        shell = win32com.client.Dispatch('WScript.Shell')
        link = shortcut_directory / f'{APP_NAME}.lnk'
        saved = shell.CreateShortcut(str(link))
        assert Path(saved.TargetPath) == launcher
        assert not saved.Arguments
        assert Path(saved.WorkingDirectory) == Path(environment['DEEPECOHAB_ACQUISITION_HOME'])
        assert Path(saved.IconLocation.split(',')[0]).is_file()
        report['shortcut'] = dict(path=str(link), target=saved.TargetPath,
                                  working_directory=saved.WorkingDirectory, icon=saved.IconLocation)
        report['shortcut_launch'] = smoke('shortcut-target-smoke', Path(saved.TargetPath))
        # Exercise Windows' actual .lnk association with no redirected stdio,
        # as an Explorer double-click does. Use a temporary smoke argument and
        # offscreen Qt so acceptance does not start a visible interactive app.
        from win32com.shell import shell as windows_shell, shellcon
        import win32event
        import win32process
        native_result = directory / 'native-shortcut-smoke.json'
        native_result.unlink(missing_ok=True)
        saved.Arguments = subprocess.list2cmdline(['--smoke-test', str(native_result)])
        saved.Save()
        original_environment = os.environ.copy()
        handle = None
        try:
            os.environ.clear()
            os.environ.update(environment)
            started = windows_shell.ShellExecuteEx(lpFile=str(link),
                        fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, nShow=0)
            handle = started['hProcess']
            if win32event.WaitForSingleObject(handle, 60_000) != win32event.WAIT_OBJECT_0:
                win32process.TerminateProcess(handle, 1)
                raise AssertionError('Native shortcut launch did not finish in 60 seconds.')
            assert win32process.GetExitCodeProcess(handle) == 0
            report['native_shortcut_launch'] = json.loads(native_result.read_text(encoding='utf-8'))
            assert not report['native_shortcut_launch']['console_stdout_available']
            assert len(report['native_shortcut_launch']['workers']) == 3
            assert report['native_shortcut_launch']['system_check']['core_ready']
            assert report['native_shortcut_launch']['system_check']['progress_timer_ticks'] > 0
            operations.append(dict(operation='native-shortcut-launch', exit_code=0))
        finally:
            os.environ.clear()
            os.environ.update(original_environment)
            if handle:
                handle.Close()
            saved.Arguments = ''
            saved.Save()
        run('video-help', [scripts / 'deepecohab-verify-video.exe', '--help'])
        source = outside / 'historical_raw_serial.jsonl'
        output = outside / 'recovered.bin'
        payload = b'one;two;three\r\n\x00\xff'
        source.write_text(json.dumps({'data_b64': base64.b64encode(payload).decode('ascii')}) + '\n', encoding='utf-8')
        output.unlink(missing_ok=True)
        run('serial-recovery', [scripts / 'deepecohab-recover-serial.exe', source, output])
        assert output.read_bytes() == payload
        run('serial-no-overwrite', [scripts / 'deepecohab-recover-serial.exe', source, output], expected=1)
        assert output.read_bytes() == payload
        run('legacy-alias', [scripts / 'acquisition-gui.exe', '--version'])
        run('legacy-recorder-alias', [scripts / 'deepecohab-recorder.exe', '--version'])
        if args.wheel:
            with zipfile.ZipFile(args.wheel) as wheel:
                names = wheel.namelist()
                assert 'deepecohab_recorder/assets/acquisition.ico' in names
                assert 'deepecohab_recorder/assets/licenses/DeepEcoHab-LICENSE.txt' in names
                assert 'deepecohab_recorder/assets/help/camera_setup.md' in names
                for resource in ('theme.json', 'theme.qss', 'theme_resources.json',
                    'fonts/Geist-Regular.ttf', 'fonts/Geist-SemiBold.ttf', 'fonts/GeistMono-Regular.ttf',
                    'licenses/Geist-OFL.txt', 'licenses/Tabler-LICENSE.txt', 'icons/users.svg',
                    'theme/light-checkbox.svg', 'theme/dark-checkbox.svg', 'theme/dark-arrow-down.svg'):
                    assert 'deepecohab_recorder/assets/' + resource in names
                assert not any('DeepEcoHAB_reference_repository' in name for name in names)
                assert not any(name.startswith(('tests/', 'validation/', '.artifacts/', 'firmware/', 'DeepEcoHAB_reference_repository/')) for name in names)
                report['wheel'] = dict(path=str(args.wheel.resolve()), entries=len(names))
        report['passed'] = True
    except Exception as exc:
        report['passed'], report['error'] = False, str(exc)
    (directory / 'installation_results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
