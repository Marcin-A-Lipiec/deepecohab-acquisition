"""Software readiness checks, separate from recording and scientific metadata."""
from datetime import datetime, timezone
from contextvars import ContextVar
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

from deepecohab_recorder import __version__
from deepecohab_recorder.runtime_paths import default_experiment_directory, worker_python
from deepecohab_recorder.video_tools import preferences_path, resolve_video_tools, subprocess_options
from .dependency_probe import PREFIX

CAMERA_GUIDE = 'https://www.teledynevisionsolutions.com/support/support-center/technical-guidance/iis/installing-pyspin-for-the-spinnaker-sdk/'
FFMPEG_GUIDE = 'https://ffmpeg.org/download.html'
MODULE_LABELS = {'PySide6.QtWidgets': 'Qt interface', 'serial': 'Serial library',
                 'pyarrow': 'Parquet library', 'tzdata': 'Timezones', 'platformdirs': 'Application storage',
                 'numpy': 'NumPy', 'cv2': 'OpenCV', 'PySpin': 'PySpin camera interface'}
DISTRIBUTIONS = ('PySide6', 'pyserial', 'pyarrow', 'tzdata', 'platformdirs',
                 'numpy', 'opencv-python-headless', 'pywin32', 'spinnaker-python')
_cancelled = ContextVar('system_check_cancelled', default=lambda: False)


class CheckCancelled(Exception):
    pass


def child_environment():
    environment = os.environ.copy()
    parent = str(Path(__file__).resolve().parents[2])
    previous = environment.get('PYTHONPATH')
    environment['PYTHONPATH'] = parent + (os.pathsep + previous if previous else '')
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['PYTHONUNBUFFERED'] = '1'
    return environment


def run_command(command, *, timeout=10, payload=None):
    process, input_file = None, None
    try:
        if _cancelled.get()():
            raise CheckCancelled('System check cancelled.')
        if payload is not None:
            # On Windows communicate(input=...) can block in a synchronous pipe
            # write before its timeout applies. A small file also lets a stalled
            # encoder be cancelled without an abandoned input-writer thread.
            input_file = tempfile.TemporaryFile()
            input_file.write(payload)
            input_file.seek(0)
        process = subprocess.Popen(command, stdin=input_file if input_file is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_environment(), **subprocess_options())
        deadline = time.monotonic() + timeout
        while True:
            if _cancelled.get()():
                raise CheckCancelled('System check cancelled.')
            if time.monotonic() >= deadline:
                return {'ok': False, 'error': f'Check timed out after {timeout:g} seconds.'}
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                return {'ok': process.returncode == 0, 'return_code': process.returncode,
                        'stdout': stdout.decode('utf-8', errors='replace'),
                        'stderr': stderr.decode('utf-8', errors='replace')}
            except subprocess.TimeoutExpired:
                pass
    except OSError as exc:
        return {'ok': False, 'error': str(exc)}
    finally:
        try:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
        finally:
            if input_file is not None:
                input_file.close()


def isolated_imports(kind, detect=False):
    command = [worker_python(), '-m', 'deepecohab_recorder.services.dependency_probe', kind]
    if detect:
        command.append('--camera-devices')
    result = run_command(command, timeout=20)
    if result['ok']:
        for line in reversed(result['stdout'].splitlines()):
            if line.startswith(PREFIX):
                try:
                    return json.loads(line[len(PREFIX):])
                except ValueError:
                    break
    error = result.get('error') or result.get('stderr', '')[-2000:] or 'The native import process did not produce a report.'
    if result.get('return_code'):
        error += f" Exit code: {result['return_code']}."
    return {'probe_error': error, 'return_code': result.get('return_code')}


def output_readiness(directory):
    target = Path(directory).expanduser().resolve()
    ancestor = target
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    result = {'path': str(target), 'exists': target.exists(), 'checked_directory': str(ancestor), 'writable': False}
    try:
        if not ancestor.is_dir():
            raise OSError(f'This path is a file, not an output directory: {ancestor}')
        with tempfile.TemporaryFile(dir=ancestor) as probe:
            probe.write(b'DeepEcoHab output check\n')
            probe.flush()
            os.fsync(probe.fileno())
        result['writable'] = True
        result['free_bytes'] = shutil.disk_usage(ancestor).free
    except OSError as exc:
        result['error'] = str(exc)
    return result


def tool_check(executable):
    result = executable.to_dict()
    result['ok'] = False
    if not executable.path:
        return result
    probe = run_command([executable.path, '-version'], timeout=5)
    line = next(iter(probe.get('stdout', '').splitlines()), '')
    result.update(version=line)
    result['ok'] = probe['ok'] and line.lower().startswith(executable.name + ' version ')
    if not result['ok']:
        result['error'] = probe.get('error') or probe.get('stderr', '')[-1500:] or f'This file does not identify itself as {executable.name}.'
    return result


def test_encoder(ffmpeg, ffprobe, option):
    """Small encode/decode/count check. This is not a recording-speed benchmark."""
    with tempfile.TemporaryDirectory(prefix='deepecohab_encoder_check_') as temporary:
        # HEVC NVENC rejects very small frames on otherwise working GPUs.
        size = 256
        movie = Path(temporary) / ('synthetic.' + option.extension)
        args = list(option.encoder_args)
        if 'libx265' in args:
            args += ['-x265-params', 'pools=1:frame-threads=1']
        encoded = run_command([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', f'{size}x{size}', '-r', '30', '-i', '-',
            *args, '-threads', '1', '-frames:v', '3', str(movie)],
            payload=bytes(size * size * 3), timeout=12)
        if not encoded['ok']:
            return {'ok': False, 'error': encoded.get('error') or encoded.get('stderr', '')[-2000:] or 'Encoder failed.'}
        decoded = run_command([ffmpeg, '-nostdin', '-v', 'error', '-i', str(movie), '-f', 'null', '-'], timeout=8)
        if not decoded['ok'] or decoded.get('stderr', '').strip():
            return {'ok': False, 'error': decoded.get('error') or decoded.get('stderr', '')[-2000:] or 'Decode failed.'}
        result = {'ok': True, 'frames': None, 'frame_count_verified': False, 'resolution': f'{size}x{size}', 'fps': 30,
                  'container': option.extension}
        if ffprobe:
            counted = run_command([ffprobe, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(movie)], timeout=8)
            try:
                frames = int(json.loads(counted.get('stdout', ''))['streams'][0]['nb_read_frames'])
                result.update(frames=frames, frame_count_verified=counted['ok'] and frames == 3)
                result['ok'] = result['frame_count_verified']
            except (ValueError, TypeError, KeyError, IndexError):
                result.update(ok=False, error='ffprobe could not verify the three synthetic frames.')
        return result


def _collect_checks(*, output_directory=None, ffmpeg=None, ffprobe=None,
                   encoding='DeepEcoHAB_default (NVENC HQ)', auto_cpu_fallback=True,
                   encode_test=False, camera_devices=False, skip_camera=False,
                   skip_video_tests=False, owned_ports=None, progress=lambda message: None):
    from deepecohab_recorder.controllers.camera import ENCODING_OPTIONS
    checks = []

    def add(key, label, status, detail, help_url=None):
        checks.append(dict(key=key, label=label, status=status, detail=str(detail), help_url=help_url))

    supported = (3, 10) <= sys.version_info[:2] < (3, 13)
    bits = struct.calcsize('P') * 8
    report = dict(report_version=1, software_version=__version__, checked_at=datetime.now(timezone.utc).isoformat(),
                  python=dict(executable=sys.executable, version=platform.python_version(), bits=bits,
                              platform=platform.platform(), architecture=platform.machine()),
                  checks=checks, physical_acquisition_started=False, preferences_file=str(preferences_path()))
    add('python', 'Python', 'ok' if supported else 'error', f'{platform.python_version()} ({bits}-bit); supported versions: 3.10–3.12.')
    versions = {}
    for name in DISTRIBUTIONS:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    report['packages'] = versions
    progress('Checking the interface, serial and preprocessing libraries…')
    core = isolated_imports('core')
    report['core_imports'] = core
    if 'probe_error' in core:
        add('core_imports', 'Core libraries', 'error', core['probe_error'])
    else:
        for name, value in core.items():
            add(name, MODULE_LABELS[name], 'ok' if value['ok'] else 'error',
                value.get('version') or ('Import succeeded.' if value['ok'] else value['error']))

    progress('Checking output storage and serial-port discovery…')
    report['owned_ports'] = dict(owned_ports or {})
    output = output_readiness(output_directory or default_experiment_directory())
    report['output'] = output
    output_ok = output['writable'] and output.get('free_bytes', 0) >= 100 * 1024 * 1024
    add('output', 'Experiment output', 'ok' if output_ok else 'error',
        f"{output['path']}: writable; {output.get('free_bytes', 0) / 1024**3:.1f} GiB free."
        + (' Folder will be created when needed.' if not output['exists'] else '')
        if output_ok else output.get('error', 'Less than 100 MiB free; choose an output disk with space.'))
    try:
        from serial.tools.list_ports import comports
        ports = [dict(port=p.device, description=p.description, owner=(owned_ports or {}).get(p.device, '')) for p in comports()]
        report['serial_ports'] = ports
        add('serial_ports', 'Serial ports', 'ok' if ports else 'warning',
            '; '.join(f"{p['port']} ({p['description']})" + (f" — used by {p['owner']} in this GUI" if p['owner'] else '') for p in ports)
            or 'No serial ports detected. Connect a board and check its USB driver. Ports are listed without opening them.')
    except Exception as exc:
        report['serial_ports'] = []
        add('serial_ports', 'Serial ports', 'error', str(exc))

    progress('Checking camera software in an isolated process…')
    camera = {'skipped': True} if skip_camera else isolated_imports('camera', camera_devices)
    report['camera'] = camera
    camera_ok = False
    if skip_camera:
        add('camera', 'Camera software', 'not_checked', 'Deferred while a camera is running or changing state.')
    elif 'probe_error' in camera:
        add('camera', 'Camera software', 'warning', camera['probe_error'], CAMERA_GUIDE)
    else:
        imports = camera.get('imports', {})
        camera_ok = all(imports.get(name, {}).get('ok', False) for name in ('numpy', 'cv2', 'PySpin')) and not camera.get('sdk_error')
        for name, value in imports.items():
            add(name, MODULE_LABELS[name], 'ok' if value['ok'] else 'warning',
                value.get('version') or ('Import succeeded.' if value['ok'] else value['error']),
                CAMERA_GUIDE if name == 'PySpin' else None)
        numpy_version = imports.get('numpy', {}).get('version', '')
        if numpy_version and int(numpy_version.split('.')[0]) >= 2:
            camera_ok = False
            add('numpy_camera', 'Camera NumPy compatibility', 'warning', 'This package uses NumPy below 2; recreate the environment with the tested constraints.', CAMERA_GUIDE)
        if camera.get('sdk_error'):
            add('camera_sdk', 'Spinnaker runtime', 'warning', camera['sdk_error'], CAMERA_GUIDE)
        elif camera.get('sdk_version'):
            add('camera_sdk', 'Spinnaker runtime', 'ok', camera['sdk_version'], CAMERA_GUIDE)
        wheel, sdk = camera.get('wheel_version'), camera.get('sdk_version')
        if wheel and sdk and wheel.split('.')[:2] != sdk.split('.')[:2]:
            camera_ok = False
            add('sdk_match', 'SDK/wheel compatibility', 'warning', f'PySpin {wheel} and SDK {sdk} are different release families. Install the vendor-matched pair.', CAMERA_GUIDE)
        elif wheel and sdk and wheel != sdk:
            add('sdk_match', 'SDK/wheel compatibility', 'warning', f'PySpin {wheel}, SDK {sdk}: confirm this build pairing with the vendor.', CAMERA_GUIDE)
        if bits != 64 and sys.platform == 'win32':
            camera_ok = False
            add('camera_architecture', 'Camera architecture', 'warning', 'Use Windows x64 Python and the matching vendor wheel.', CAMERA_GUIDE)
        if camera.get('devices_checked'):
            count = camera.get('device_count', 0)
            add('camera_devices', 'Connected cameras', 'ok' if count else 'warning',
                f'{count} camera(s) detected; acquisition access is checked on start.' if count else 'No cameras detected. Verify connection and acquisition in SpinView.', CAMERA_GUIDE)
        else:
            add('camera_devices', 'Connected cameras', 'not_checked', 'Use Detect cameras when acquisition is stopped. No camera is initialized or recording during this check.')

    progress('Checking FFmpeg, ffprobe and available encoders…')
    tools = {}
    try:
        resolutions = resolve_video_tools(ffmpeg=ffmpeg, ffprobe=ffprobe)
        tools = {name: tool_check(value) for name, value in resolutions.items()}
    except ValueError as exc:
        add('video_preferences', 'Video-tool settings', 'warning', str(exc))
    report['video_tools'] = tools
    for name, value in tools.items():
        add(name, name, 'ok' if value['ok'] else 'warning',
            f"{value['path']} ({value['source']}); {value.get('version', '')}" if value['ok'] else value.get('error', 'Unavailable.'), FFMPEG_GUIDE)
    encoders = {}
    ffmpeg_path = tools.get('ffmpeg', {}).get('path') if tools.get('ffmpeg', {}).get('ok') else None
    ffprobe_path = tools.get('ffprobe', {}).get('path') if tools.get('ffprobe', {}).get('ok') else None
    if ffmpeg_path:
        available = run_command([ffmpeg_path, '-hide_banner', '-encoders'], timeout=8)
        if available['ok']:
            names = set(re.findall(r'^\s*[A-Z.]{6}\s+(\S+)', available.get('stdout', ''), flags=re.MULTILINE))
            for label, option in ENCODING_OPTIONS.items():
                codec = option.encoder_args[option.encoder_args.index('-c:v') + 1]
                result = {'codec': codec, 'listed': codec in names, 'tested': False}
                if encode_test and not skip_video_tests and result['listed']:
                    progress(f'Testing {label} with three synthetic frames…')
                    result.update(test_encoder(ffmpeg_path, ffprobe_path, option), tested=True)
                encoders[label] = result
                status = ('ok' if result.get('ok') else 'warning') if result['tested'] else ('not_checked' if result['listed'] else 'warning')
                detail = ('Synthetic encode/decode passed' + ('; 3 frames verified.' if result.get('frame_count_verified') else '; frame count not verified.')) if result.get('ok') else result.get('error') or (
                    'Listed in FFmpeg; runtime test deferred while acquisition/preprocessing is active.' if skip_video_tests else
                    'Listed in FFmpeg; run Test video encoders to verify driver/runtime support.' if result['listed'] else 'This FFmpeg build does not provide the encoder.')
                add('encoder:' + label, label, status, detail, FFMPEG_GUIDE)
        else:
            add('encoders', 'Video encoders', 'warning', available.get('error') or available.get('stderr', '')[-1500:])
    report['encoders'] = encoders
    selected = encoders.get(encoding, {})
    fallback = encoders.get('CPU H.264 emergency fallback', {})
    encoding_ok = bool(selected.get('ok') or (auto_cpu_fallback and 'NVENC' in encoding.upper() and fallback.get('ok')))
    report['selected_encoding'] = dict(label=encoding, auto_cpu_fallback=auto_cpu_fallback,
                                       runtime_verified=encoding_ok)
    qt_ok = core.get('PySide6.QtWidgets', {}).get('ok', False)
    serial_ok = core.get('serial', {}).get('ok', False)
    parquet_ok = core.get('pyarrow', {}).get('ok', False)
    timezone_ok = core.get('tzdata', {}).get('ok', False)
    report['capabilities'] = dict(interface=bool(supported and qt_ok), rfid_software=bool(supported and serial_ok and timezone_ok),
        preprocessing=bool(supported and parquet_ok and timezone_ok), output=output_ok,
        camera_software=None if skip_camera else bool(supported and camera_ok and ffmpeg_path),
        selected_video_encoding=encoding_ok if encode_test and not skip_video_tests else None,
        video_verification=bool(ffmpeg_path and ffprobe_path))
    report['core_ready'] = all(report['capabilities'][key] for key in ('interface', 'rfid_software', 'preprocessing', 'output'))
    progress('System check finished.')
    return report


def collect_checks(*, cancelled=lambda: False, **options):
    token = _cancelled.set(cancelled)
    original_progress = options.pop('progress', lambda message: None)
    def progress(message):
        if cancelled():
            raise CheckCancelled('System check cancelled.')
        original_progress(message)
    try:
        return _collect_checks(progress=progress, **options)
    finally:
        _cancelled.reset(token)


def format_report(report):
    lines = [f"DeepEcoHab Acquisition {report['software_version']} — System check",
             f"Python: {report['python']['executable']}", '']
    for item in report['checks']:
        lines.append(f"[{item['status'].upper()}] {item['label']}: {item['detail']}")
    lines += ['', 'Core software/output: ' + ('ready' if report['core_ready'] else 'needs attention'),
              'Camera software and encoder tests are separate from physical acquisition/throughput acceptance.']
    return '\n'.join(lines)
