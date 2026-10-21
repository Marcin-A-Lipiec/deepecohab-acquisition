"""Terminal entry point that works without importing Qt or camera libraries."""
import argparse
import json
import sys
from pathlib import Path

from . import SOFTWARE_LABEL
from .runtime_paths import app_data_directory, default_experiment_directory, log_directory, worker_python


def main(argv=None):
    parser = argparse.ArgumentParser(description=SOFTWARE_LABEL)
    parser.add_argument("--version", action="version", version=SOFTWARE_LABEL)
    parser.add_argument("command", choices=("launch", "paths", "doctor", "updates"), nargs="?", default="launch")
    parser.add_argument('--json', action='store_true', help='Print the System check report as JSON.')
    parser.add_argument('--output-dir', type=Path, help='Experiment output directory to check.')
    parser.add_argument('--encode-test', action='store_true', help='Encode, decode and count three synthetic frames per profile.')
    parser.add_argument('--camera-devices', action='store_true', help='Enumerate cameras without initializing or acquiring.')
    parser.add_argument('--ffmpeg', help='Temporary FFmpeg executable override for this check.')
    parser.add_argument('--ffprobe', help='Temporary ffprobe executable override for this check.')
    parser.add_argument('--require-camera', action='store_true', help='Fail if camera software or selected/fallback encoding is unavailable; includes encoder tests.')
    args = parser.parse_args(argv)
    if args.command == 'updates':
        from .services.updates import UpdateCheckError, check_updates
        try:
            result = check_updates()
        except UpdateCheckError as exc:
            print(json.dumps({'status': 'error', 'message': str(exc)}) if args.json else str(exc))
            return 1
        print(json.dumps(result.as_dict(), indent=2) if args.json else
            f'Installed: {result.installed_version}\nLatest stable: {result.latest_version or "not available"}\n'
            f'{result.message}\nRelease notes: {result.release_url}\nUpdate instructions: {result.instructions_url}')
        return 0
    if args.command == 'doctor':
        from .services.dependencies import collect_checks, format_report
        report = collect_checks(output_directory=args.output_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                                encode_test=args.encode_test or args.require_camera, camera_devices=args.camera_devices)
        print(json.dumps(report, indent=2) if args.json else format_report(report))
        ready = report['core_ready']
        if args.require_camera:
            ready = ready and report['capabilities']['camera_software'] and report['capabilities']['selected_video_encoding']
            if args.camera_devices:
                ready = ready and report['camera'].get('device_count', 0) > 0
        return 0 if ready else 1
    if args.command == "paths":
        print(json.dumps({
            "python": sys.executable, "worker_python": worker_python(),
            "application_data": str(app_data_directory()), "logs": str(log_directory()),
            "default_experiments": str(default_experiment_directory()),
        }, indent=2))
        return 0
    from .app import main as launch
    return launch([])


if __name__ == "__main__":
    raise SystemExit(main())
