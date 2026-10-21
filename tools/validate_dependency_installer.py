"""Rehearse the PowerShell installer using isolated uv directories."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deepecohab_recorder import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uv', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/current/installer')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    # All audit writes/tool removal are confined to this checkout.
    if not output.is_relative_to(ROOT):
        parser.error('The installer audit output must be inside the workspace.')
    output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(UV_TOOL_DIR=str(output / 'tool environments with spaces ł'),
        UV_TOOL_BIN_DIR=str(output / 'tool commands with spaces ł'),
        UV_CACHE_DIR=str(ROOT / '.uv-cache'), UV_PYTHON_INSTALL_DIR=str(ROOT / '.uv-python'),
        UV_OFFLINE='1', DEEPECOHAB_ACQUISITION_HOME=str(output / 'application data ł'),
        PATH=str(args.uv.resolve().parent) + os.pathsep + environment.get('PATH', ''),
        PYTHONIOENCODING='utf-8')
    application = Path(environment['DEEPECOHAB_ACQUISITION_HOME'])
    application.mkdir(parents=True, exist_ok=True)
    preferences = application / 'preferences.json'
    if not preferences.exists():
        preferences.write_text(json.dumps(dict(format='deepecohab_application_preferences', version=1,
            video_tools={}, future_feature={'retained': True})), encoding='utf-8')
    sentinel = output / 'experiment data retained.txt'
    if not sentinel.exists():
        sentinel.write_text('Existing experiment data must survive installation.', encoding='utf-8')
    originals = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (preferences, sentinel)}
    shortcut_directory = output / 'Desktop with spaces ł'
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    installer = [powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ROOT / 'install_windows.ps1',
                 '-ShortcutDirectory', shortcut_directory, '-PythonVersion', '3.12', '-Force']
    operations = []

    def run(label, command, expected=0):
        result = subprocess.run([str(item) for item in command], env=environment, cwd=output,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
        (output / (label + '.txt')).write_text(result.stdout + result.stderr, encoding='utf-8')
        operations.append(dict(operation=label, exit_code=result.returncode))
        if result.returncode != expected:
            raise AssertionError(f'{label}: {result.stdout}\n{result.stderr}')
        return result

    try:
        for label in ('install', 'reinstall'):
            run(label, installer)
            for path, digest in originals.items():
                assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            link = shortcut_directory / 'DeepEcoHab Acquisition.lnk'
            import win32com.client
            shortcut = win32com.client.Dispatch('WScript.Shell').CreateShortcut(str(link))
            target = Path(environment['UV_TOOL_DIR']) / 'deepecohab-acquisition/Scripts/deepecohab-acquisition.exe'
            assert Path(shortcut.TargetPath) == target
            assert not shortcut.Arguments
            assert Path(shortcut.WorkingDirectory) == application
            command = Path(environment['UV_TOOL_BIN_DIR']) / 'deepecohab-acquisition-cli.exe'
            assert __version__ in run(label + '-version', [command, '--version']).stdout
        # A missing vendor wheel must fail before replacing the installed tool.
        run('missing-vendor-wheel', installer + ['-PySpinWheel', output / 'absent vendor wheel.whl'], expected=1)
        assert target.exists()
        shortcut_command = Path(environment['UV_TOOL_BIN_DIR']) / 'deepecohab-acquisition-shortcut.exe'
        run('remove-shortcut', [shortcut_command, '--directory', shortcut_directory, '--remove'])
        assert not link.exists()
        run('uninstall', [args.uv, 'tool', 'uninstall', 'deepecohab-acquisition'])
        assert not target.exists()
        for path, digest in originals.items():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        report = dict(passed=True, operations=operations, preferences_retained=True,
                      experiment_sentinel_retained=True, vendor_wheel_supplied=False, physical_hardware_tested=False)
    except Exception as exc:
        report = dict(passed=False, operations=operations, error=str(exc))
    (output / 'installer_results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
