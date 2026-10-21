"""Disposable GUI check worker; progress uses stdout, the report uses a file."""
import argparse
import json
from pathlib import Path

from .dependencies import CheckCancelled, collect_checks


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cancel-file', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        options = json.loads(args.request.read_text(encoding='utf-8'))
        report = collect_checks(**options,
            progress=lambda message: print(json.dumps({'progress': message}), flush=True),
            cancelled=args.cancel_file.exists)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return 0
    except CheckCancelled:
        print(json.dumps({'progress': 'System check cancelled.'}), flush=True)
        return 2
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
