"""Isolated native imports. A DLL failure must not terminate the acquisition GUI."""
import argparse
import ctypes
import importlib
from importlib import metadata
import json
import os

PREFIX = 'DEEPECOHAB_PROBE_RESULT='


def imports(names):
    result = {}
    for name in names:
        try:
            module = importlib.import_module(name)
            result[name] = {'ok': True, 'version': str(getattr(module, '__version__', ''))}
        except Exception as exc:
            result[name] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    return result


def camera(detect=False):
    result = {'imports': imports(('numpy', 'cv2', 'PySpin')), 'devices_checked': False}
    if not result['imports']['PySpin']['ok']:
        return result
    import PySpin
    result['module_path'] = str(getattr(PySpin, '__file__', ''))
    try:
        result['wheel_version'] = metadata.version('spinnaker-python')
    except metadata.PackageNotFoundError:
        result['wheel_version'] = None
    system, cameras = None, None
    try:
        system = PySpin.System.GetInstance()
        version = system.GetLibraryVersion()
        result['sdk_version'] = '.'.join(str(getattr(version, key)) for key in ('major', 'minor', 'type', 'build'))
        if detect:
            cameras = system.GetCameras()
            result.update(devices_checked=True, device_count=cameras.GetSize())
    except Exception as exc:
        result['sdk_error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if cameras is not None:
            try:
                cameras.Clear()
            except Exception as exc:
                result['sdk_error'] = f'Camera-list cleanup failed: {exc}'
        if system is not None:
            try:
                system.ReleaseInstance()
            except Exception as exc:
                result['sdk_error'] = f'Spinnaker cleanup failed: {exc}'
    return result


def main(argv=None):
    if os.name == 'nt':
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=('core', 'camera'))
    parser.add_argument('--camera-devices', action='store_true')
    args = parser.parse_args(argv)
    result = (imports(('PySide6.QtWidgets', 'serial', 'pyarrow', 'tzdata', 'platformdirs'))
              if args.kind == 'core' else camera(args.camera_devices))
    print(PREFIX + json.dumps(result), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
