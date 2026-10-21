"""Verify build contents against maintained source and report baseline differences."""
import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deepecohab_recorder import __version__
from tools.prepare_github_release import EXCLUDED, collect_source_files


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_content(archive):
    return {name: archive.read(name) for name in archive.namelist()
        if name.startswith("deepecohab_recorder/")}


def metadata(archive):
    path = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
    document = Parser().parsestr(archive.read(path).decode("utf-8"))
    entries = next(name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt"))
    return document, archive.read(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--baseline-wheel", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/current/distribution.json")
    args = parser.parse_args()
    report = dict(version=__version__, passed=False)
    try:
        with zipfile.ZipFile(args.wheel) as wheel:
            assert wheel.testzip() is None, "Damaged wheel entry"
            current = runtime_content(wheel)
            expected = {path.relative_to(ROOT).as_posix(): path.read_bytes()
                for path in (ROOT / "deepecohab_recorder").rglob("*") if path.is_file()
                and "__pycache__" not in path.parts and (path.suffix == ".py"
                    or "assets" in path.relative_to(ROOT).parts)}
            assert current == expected, "Wheel runtime/assets differ from maintained source"
            assert not any(set(Path(name).parts) & EXCLUDED for name in wheel.namelist())
            document, entry_points = metadata(wheel)
            assert document["Version"] == __version__
            assert document["Name"] == "deepecohab-acquisition"
            report["wheel"] = dict(path=str(args.wheel.resolve()), sha256=checksum(args.wheel),
                entries=len(wheel.namelist()), runtime_assets=sum("/assets/" in name for name in current),
                all_runtime_files_match_source=True)
            if args.baseline_wheel:
                with zipfile.ZipFile(args.baseline_wheel) as baseline:
                    original = runtime_content(baseline)
                    old_metadata, old_entries = metadata(baseline)
                    assert document.get_all("Requires-Dist") == old_metadata.get_all("Requires-Dist")
                    assert entry_points == old_entries
                    report["baseline"] = dict(version=old_metadata["Version"],
                        sha256=checksum(args.baseline_wheel),
                        runtime_dependencies_unchanged=True, launch_entry_points_unchanged=True,
                        added=sorted(current.keys() - original.keys()),
                        removed=sorted(original.keys() - current.keys()),
                        changed=sorted(name for name in current.keys() & original.keys() if current[name] != original[name]),
                        unchanged=sorted(name for name in current.keys() & original.keys() if current[name] == original[name]))
        with tarfile.open(args.sdist) as archive:
            source = {Path(*Path(member.name).parts[1:]).as_posix(): archive.extractfile(member).read()
                for member in archive.getmembers() if member.isfile()}
            assert not any(set(Path(name).parts) & EXCLUDED or any(part.startswith(".stage") for part in Path(name).parts)
                or Path(name).name.startswith("PATCH_") for name in source)
            expected = {name: path.read_bytes() for name, path in collect_source_files(ROOT).items()}
            missing = sorted(name for name in expected if source.get(name) != expected[name])
            assert not missing, "Source archive missing/stale maintained files: " + ", ".join(missing)
            generated = {name for name in source if name in {"PKG-INFO", "setup.cfg"}
                or (len(Path(name).parts) == 2 and Path(name).parts[0] == "deepecohab_acquisition.egg-info")}
            unexpected = sorted(source.keys() - expected.keys() - generated)
            assert not unexpected, "Unreviewed source archive files: " + ", ".join(unexpected)
            report["source_archive"] = dict(path=str(args.sdist.resolve()), sha256=checksum(args.sdist),
                files=len(source), maintained_files_checked=len(expected), all_match_source=True,
                reference_recordings_and_generated_files_excluded=True)
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "baseline"}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
