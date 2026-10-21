"""Single child process; CPU/disk preprocessing does not run on the GUI thread."""

import argparse
import json
from pathlib import Path
import sys

from .models import Cancelled, Settings
from .pipeline import run
from .inventory import inventory


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    parser.add_argument("--cancel-file", required=True)
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    try:
        settings = Settings.from_dict(
            json.loads(Path(args.settings).read_text(encoding="utf-8"))
        )
        progress = lambda value, text: emit("progress", value=value, text=text)
        cancelled = lambda: Path(args.cancel_file).exists()
        if args.inventory:
            emit("inventory_completed", **inventory(settings.inputs, settings.timezone, progress, cancelled))
            return 0
        result = run(
            settings,
            progress,
            cancelled,
        )
        emit("completed", **result)
        return 0
    except Cancelled as exc:
        emit("cancelled", text=str(exc))
        return 2
    except Exception as exc:
        emit("error", text=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
