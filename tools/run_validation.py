"""Run every pytest regression and retain current, machine-readable evidence."""
import argparse
import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/current/regression')
    args, pytest_args = parser.parse_known_args(argv)
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    environment = os.environ.copy()
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['DEEPECOHAB_ACQUISITION_HOME'] = str(directory / 'application-data')
    junit = directory / 'pytest.xml'
    junit.unlink(missing_ok=True)
    command = [sys.executable, '-m', 'pytest', 'tests', '-q', f'--junitxml={junit}', *pytest_args]
    with (directory / 'test_output.txt').open('w', encoding='utf-8') as output:
        result = subprocess.run(command, cwd=ROOT, env=environment,
                                stdout=output, stderr=subprocess.STDOUT)
    counts = dict(tests_run=0, failures=0, errors=0, skipped_count=0)
    skipped, failed = [], []
    if junit.exists():
        tree = ET.parse(junit)
        for suite in tree.iter('testsuite'):
            for key, attribute in (('tests_run', 'tests'), ('failures', 'failures'),
                                   ('errors', 'errors'), ('skipped_count', 'skipped')):
                counts[key] += int(suite.get(attribute, 0))
        for case in tree.iter('testcase'):
            name = f"{case.get('classname', '')}.{case.get('name', '')}"
            for entry in case:
                if entry.tag == 'skipped':
                    skipped.append(dict(test=name, reason=entry.get('message', '')))
                elif entry.tag in ('error', 'failure'):
                    failed.append(dict(test=name, kind=entry.tag, message=entry.get('message', '')))
    versions = {}
    for name in ('PySide6', 'numpy', 'tzdata', 'pyserial', 'opencv-python-headless',
                 'pyarrow', 'platformdirs', 'pywin32', 'pytest', 'polars', 'jsonschema'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'not installed'
    commands = {}
    for program in ('ffmpeg', 'ffprobe', 'g++'):
        try:
            output = subprocess.run([program, '--version' if program == 'g++' else '-version'],
                                    capture_output=True, text=True, timeout=5)
            commands[program] = output.stdout.splitlines()[0]
        except (OSError, IndexError, subprocess.TimeoutExpired):
            commands[program] = 'not available'
    from deepecohab_recorder import SOFTWARE_LABEL
    from deepecohab_recorder.schema import CONFIG_VERSION
    report = dict(release=SOFTWARE_LABEL, config_version=CONFIG_VERSION,
                  validated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  python=platform.python_version(), platform=platform.platform(),
                  dependencies=versions, tools=commands, command=command, **counts,
                  passed_count=counts['tests_run'] - counts['failures'] - counts['errors'] - counts['skipped_count'],
                  skipped=skipped, failed_tests=failed,
                  passed=result.returncode == 0 and junit.exists(), exit_code=result.returncode,
                  elapsed_seconds=round(time.monotonic() - started, 3),
                  physical_hardware_tested=False,
                  camera_test='Synthetic frames through production FFmpeg writer; real encode/decode when FFmpeg is available.',
                  serial_test='Simulated serial transport and historical stream replay.')
    (directory / 'test_results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print((directory / 'test_output.txt').read_text(encoding='utf-8'))
    print(f"Validation {'passed' if report['passed'] else 'failed'}: {directory / 'test_results.json'}")
    return result.returncode if result.returncode else (0 if report['passed'] else 1)


if __name__ == '__main__':
    raise SystemExit(main())
