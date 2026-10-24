"""Create a curated GitHub source ZIP and checksums without publishing anything."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import zipfile

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "https://github.com/Marcin-A-Lipiec/deepecohab-acquisition"
ROOT_FILES = (
    ".editorconfig", ".gitattributes", ".gitignore", ".python-version",
    "ARCHITECTURE.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE_STATUS.md",
    "MANIFEST.in", "README.md", "SECURITY.md", "TESTING.md", "THIRD_PARTY_NOTICES.md",
    "install_windows.ps1", "pyproject.toml", "requirements.txt", "run_acquisition_gui.py",
    "run_deepecohab.py", "run_windows.bat", "setup_windows.bat", "uv.lock",
)
SOURCE_RULES = {
    "deepecohab_recorder": {".py", ".svg", ".ico", ".json", ".qss", ".txt", ".md", ".ttf"},
    "docs": {".md", ".json"},
    "examples": {".json"},
    "firmware": {".ino", ".h", ".cpp", ".py", ".md", ".txt", ".json", ".log", ".zip"},
    "tests": {".py", ".cpp", ".h", ".json", ".csv", ".log", ".txt", ".md"},
    "tools": {".py", ".ps1"},
    ".github": {".yml", ".md"},
}
EXCLUDED = {
    "DeepEcoHAB_reference_repository", "Duplicate_test", "Timezone_bug", "DeepEcoHAB_data",
    "raw_antenna_reads", ".artifacts", "validation", ".venv", ".uv-python",
    ".uv-cache", ".pytest_cache", ".cache", "build", "dist", ".git", "__pycache__",
}
PRIVATE_SOURCE_PATHS = frozenset({
    "docs/releasing.md", "docs/roadmap.md", "docs/preview_testing.md",
    "tools/run_preview.py", "tools/setup_preview.ps1",
})
PRIVATE_DOCUMENT_TEXT = re.compile(
    r"(?:\b[A-Z]:[\\/]+Users[\\/]|"
    r"prepared for local acceptance|modernization stage\s+\d|PUBLISH_v\d)",
    re.IGNORECASE,
)


def private_source_path(name):
    parts = Path(name).as_posix().lower().split('/')
    return ('/'.join(parts) in PRIVATE_SOURCE_PATHS or
            (len(parts) > 1 and parts[0] == 'docs' and parts[1] in ('private', 'internal')) or
            (parts[-1].endswith('.md') and parts[-1].startswith(
                ('publish', 'publication_guide', 'private_', 'internal_'))))


def _check_public_document(path, name):
    if path.suffix.lower() == '.md' and PRIVATE_DOCUMENT_TEXT.search(path.read_text(encoding='utf-8-sig')):
        raise ValueError(f'Remove local paths or private development notes before publication: {name}')


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_path(root, path):
    # Refuse links/junctions rather than copying their potentially private targets.
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ValueError(f"Linked release path is not allowed: {path.relative_to(root)}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Release path leaves the source directory: {path}")


def collect_source_files(root):
    """Return only maintained source roots, preserving fixture and firmware bytes."""
    root = root.resolve()
    files = {}
    for name in ROOT_FILES + (("LICENSE",) if (root / "LICENSE").exists() else ()):
        path = root / name
        _check_path(root, path)
        if not path.is_file():
            raise ValueError(f"Required release file is missing: {name}")
        _check_public_document(path, name)
        files[name] = path
    for folder, suffixes in SOURCE_RULES.items():
        start = root / folder
        _check_path(root, start)
        if not start.is_dir():
            raise ValueError(f"Required source directory is missing: {folder}")
        for current, directories, names in os.walk(start, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in EXCLUDED
                and not name.startswith((".stage", ".venv")) and not name.endswith(".egg-info")
                and not private_source_path((Path(current) / name).relative_to(root).as_posix()))
            for name in directories:
                _check_path(root, Path(current) / name)
            for name in sorted(names):
                path = Path(current) / name
                relative = path.relative_to(root).as_posix()
                if private_source_path(relative):
                    continue
                if path.suffix.lower() not in suffixes or name.startswith((".env", "PATCH_")):
                    continue
                _check_path(root, path)
                if path.is_file():
                    _check_public_document(path, relative)
                    files[relative] = path
    return dict(sorted(files.items()))


def read_version(root):
    tree = ast.parse((root / "deepecohab_recorder/__init__.py").read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == "__version__" for target in statement.targets):
            version = ast.literal_eval(statement.value)
            if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+[a-zA-Z0-9.+-]*", version):
                return version
    raise ValueError("Missing or invalid literal software version")


def license_status(root):
    license_file = root / "LICENSE"
    project = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = project.split("[project]", 1)[-1].split("\n[", 1)[0]
    file_present = license_file.is_file() and bool(license_file.read_text(encoding="utf-8").strip())
    metadata_present = bool(re.search(r"(?m)^license\s*=\s*\S", project))
    return dict(license_file_present=file_present, license_metadata_present=metadata_present,
                decision_pending=not (file_present and metadata_present))


def write_source_archive(root, destination):
    version = read_version(root)
    files = collect_source_files(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in files.items():
            entry = zipfile.ZipInfo(f"deepecohab-acquisition-{version}/{name}", (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, path.read_bytes())
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", help="Require this exact software tag, e.g. v1.0.0")
    parser.add_argument("--require-license", action="store_true",
                        help="Refuse release preparation until LICENSE and project license metadata exist")
    args = parser.parse_args(argv)
    version = read_version(ROOT)
    if args.tag and args.tag != f"v{version}":
        parser.error(f"Tag {args.tag!r} does not match v{version}")
    licensing = license_status(ROOT)
    if args.require_license and licensing["decision_pending"]:
        parser.error("The original-code license is undecided; add LICENSE and project license metadata first.")
    directory = args.output_dir.resolve()
    artifacts = [directory / f"deepecohab_acquisition-{version}-py3-none-any.whl",
                 directory / f"deepecohab_acquisition-{version}.tar.gz"]
    for path in artifacts:
        if not path.is_file():
            parser.error(f"Build the packages first with uv build; missing {path.name}")
    source_zip = directory / f"deepecohab-acquisition-{version}-github.zip"
    files = write_source_archive(ROOT, source_zip)
    artifacts.append(source_zip)
    notes = ROOT / 'docs' / f'patch_notes_v{version}.md'
    if notes.is_file():
        release_notes = directory / notes.name
        if notes.resolve() != release_notes.resolve():
            shutil.copyfile(notes, release_notes)
        artifacts.append(release_notes)
    report = dict(version=version, tag=f"v{version}", repository=REPOSITORY,
                  licensing=licensing,
                  artifacts=[dict(file=path.name, bytes=path.stat().st_size, sha256=sha256(path))
                             for path in artifacts])
    manifest = directory / "release-manifest.json"
    manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    artifacts.append(manifest)
    (directory / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in artifacts),
                                            encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
