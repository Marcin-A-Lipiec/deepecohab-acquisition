"""Recover exact received bytes from one rotated raw-serial diagnostic stream."""

import argparse
import base64
import json
from pathlib import Path


def recover(source, output):
    source, output = Path(source), Path(output)
    parts = [source] + sorted(
        source.parent.glob(f"{source.stem}_[0-9][0-9][0-9][0-9]{source.suffix}")
    )
    size = 0
    with output.open("xb") as target:
        for part in parts:
            with part.open(encoding="utf-8") as file:
                for line in file:
                    data = base64.b64decode(json.loads(line)["data_b64"], validate=True)
                    target.write(data)
                    size += len(data)
    return size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        help="The first *_raw_serial.jsonl file (subsequent rotation parts are included).",
    )
    parser.add_argument(
        "output",
        help="New binary or text output file; existing files are never overwritten.",
    )
    args = parser.parse_args(argv)
    print(f"Recovered {recover(args.source, args.output):,} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
