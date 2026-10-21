"""Publication boundaries must preserve source fixtures without exporting local data."""
from pathlib import Path
import zipfile

import pytest

from tools import prepare_github_release as release


@pytest.fixture
def source(tmp_path):
    for name in release.ROOT_FILES:
        (tmp_path / name).write_text("maintained\n", encoding="utf-8")
    for folder in release.SOURCE_RULES:
        (tmp_path / folder).mkdir()
    (tmp_path / "deepecohab_recorder/__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "deepecohab-acquisition"\n', encoding="utf-8")
    return tmp_path


def test_source_zip_excludes_recordings_environments_secrets_and_reference(source):
    private = (
        "Duplicate_test/config.json", "Duplicate_test/raw_antenna_reads/recording.txt",
        "DeepEcoHAB_reference_repository/app.py", ".venv/Lib/private.py", ".env",
        "personal-experiment/config.json", "dist/vendor.whl", "tests/__pycache__/test.pyc",
        "tests/Duplicate_test/recording.json", "tools/.env.secret.json", "docs/PATCH_99.md",
        "deepecohab_recorder/recording.parquet", "deepecohab_recorder/recording.mp4",
    )
    for name in private:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private data")
    retained = {
        "tests/fixtures/raw/observation.txt": b"antenna;tag;123\r\n",
        "firmware/device/MANIFEST.json": b'{"sha256":"unchanged"}\r\n',
        "firmware/diagnostic.zip": b"original firmware archive",
        ".github/workflows/ci.yml": b"name: CI\n",
        "deepecohab_recorder/assets/licenses/vendor.txt": b"third-party notice\n",
    }
    for name, data in retained.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    destination = source / "dist/source.zip"
    files = release.write_source_archive(source, destination)
    assert not set(private) & files.keys()
    with zipfile.ZipFile(destination) as archive:
        for name, data in retained.items():
            assert archive.read(f"deepecohab-acquisition-1.0.0/{name}") == data
        assert archive.testzip() is None
    original = destination.read_bytes()
    release.write_source_archive(source, destination)
    assert destination.read_bytes() == original


def test_publication_gate_requires_both_license_file_and_project_metadata(source, monkeypatch):
    monkeypatch.setattr(release, "ROOT", source)
    assert release.license_status(source)["decision_pending"]
    with pytest.raises(SystemExit) as error:
        release.main(["--require-license", "--output-dir", str(source / "dist")])
    assert error.value.code == 2
    assert not (source / "dist").exists()
    (source / "LICENSE").write_text("Owner-selected license\n", encoding="utf-8")
    assert release.license_status(source)["decision_pending"]
    with (source / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write('license = {file = "LICENSE"}\n')
    assert not release.license_status(source)["decision_pending"]
    assert "LICENSE" in release.collect_source_files(source)


def test_mismatched_tag_stops_before_creating_release(source, monkeypatch):
    monkeypatch.setattr(release, "ROOT", source)
    with pytest.raises(SystemExit) as error:
        release.main(["--tag", "v3.2.5", "--output-dir", str(source / "dist")])
    assert error.value.code == 2
    assert not (source / "dist").exists()


def test_source_zip_refuses_linked_private_targets(source, monkeypatch):
    # Simulate Windows junction attributes without requiring symlink privileges.
    private = source / "Duplicate_test"
    private.mkdir()
    path = source / "docs/linked"
    path.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == path or original(self))
    with pytest.raises(ValueError, match="Linked release path"):
        release.collect_source_files(source)
