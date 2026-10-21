"""Compare a baseline wheel with source using the same scientific fixtures/dependencies.

Only diagnostic creation time, software identity and the fixture root are
normalized. CSV/Parquet bytes, schemas, counts and every other diagnostic value
must match. This developer check requires the checked-in regression fixtures.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def worker(package_parent, directory):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(package_parent.resolve()))
    import pyarrow.parquet as pq
    from deepecohab_recorder import __version__
    from deepecohab_recorder.preprocessing.models import InputFile
    from tests.test_preprocessing import PreprocessingTests, TAG, FIXTURES

    cases = [(f"{mode}-{split}", mode, split) for mode in ("none", "standard", "extended")
             for split in (False, True)]
    cases += [("v1-sample", "none", False), ("old-decimal", "none", False), ("old-hex", "none", True)]
    reports = {}
    for name, mode, split in cases:
        case = PreprocessingTests()
        case.setUp()
        case.temp.cleanup()
        case.root = directory / name
        case.root.mkdir(parents=True, exist_ok=True)
        options = dict(parquet=True, csv=True, interpolation_mode=mode, interpolate=mode != "none",
            split_restarts=split)
        if name == "v1-sample":
            inputs = [InputFile(str(FIXTURES / "user_v1_sample.txt"), "COM3")]
            options["whitelist"] = False
        elif name == "old-decimal":
            case.cfg["recording"]["layout"] = {}
            inputs = [InputFile(str(FIXTURES / "user_old_decimal.csv"), "COM3", profile="old_decimal")]
            options["whitelist"] = False
        elif name == "old-hex":
            case.cfg["recording"]["layout"] = {}
            inputs = [case.raw("old.txt", [(0, "1", "A967A81904", 10000),
                (100, "1", "A967A81904", 10200)], profile="old_hex", tick_ms=0.5)]
            options["whitelist"] = False
        else:
            events = sorted(case.training_events() + [(3100, "I", TAG), (3200, "I", TAG),
                (3300, "TTL1", "00000001"), (3310, "TTL2", "00000002"), (3400, "A", "FFFFFFFF")])
            inputs = [case.raw("first.txt", events, group="1"),
                case.raw("second.txt", [(7000, "A", TAG), (8000, "B", TAG), (8100, "TTL2", "00000003")], group="2")]
            options.update(routes={"I-1": "device_antenna1"}, ttl_routes={"TTL2-1": "TTL_data2"},
                aliases={"A-1": "A_main-1"})
        case.process(inputs, **options)
        outputs = {}
        for entry in case.result["outputs"]:
            path = case.output / entry["file"]
            value = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), rows=entry["rows"])
            if path.suffix == ".parquet":
                table = pq.read_table(path)
                value.update(schema=str(table.schema), logical_rows=fingerprint(table.to_pylist()))
            outputs[entry["file"]] = value
        diagnostic = case.summary.copy()
        diagnostic.pop("created_at", None)
        diagnostic.pop("software", None)
        def normalize(value):
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, str):
                return value.replace(str(case.root), "{fixture}").replace(case.root.as_posix(), "{fixture}")
            return value
        reports[name] = dict(outputs=outputs, diagnostic=normalize(diagnostic))
    (directory / "results.json").write_text(json.dumps(dict(version=__version__, cases=reports),
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.baseline, args.output.resolve())
        return
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for label, package in (("before", args.baseline.resolve()), ("after", ROOT)):
        result = subprocess.run([sys.executable, __file__, "--worker", "--baseline", str(package),
            "--output", str(directory / label)], cwd=ROOT, env=os.environ.copy(),
            capture_output=True, encoding="utf-8", timeout=120)
        (directory / f"{label}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"{label} failed: {result.stderr}")
    before = json.loads((directory / "before/results.json").read_text(encoding="utf-8"))
    after = json.loads((directory / "after/results.json").read_text(encoding="utf-8"))
    failures = [name for name in before["cases"] if before["cases"][name] != after["cases"][name]]
    report = dict(baseline_version=before["version"], source_version=after["version"],
        cases=len(before["cases"]), data_files=sum(len(case["outputs"]) for case in before["cases"].values()),
        identical_csv_parquet_and_diagnostics=not failures, differences=failures,
        normalized=["diagnostic.created_at", "diagnostic.software", "fixture absolute root"],
        physical_hardware_tested=False)
    (directory / "comparison.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
