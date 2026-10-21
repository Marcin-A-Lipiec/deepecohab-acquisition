"""Run the real PowerShell helper against controlled releases and a simulated uv.

Only shortcuts in a temporary folder are created. No downloads, tool installs,
real Desktop changes or camera imports are performed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import venv

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/Marcin-A-Lipiec/deepecohab-acquisition"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell installer")


@pytest.fixture
def installer(tmp_path):
    wheel = "deepecohab_acquisition-1.0.0-py3-none-any.whl"
    payload = dict(tag_name="v1.0.0", draft=False, prerelease=False,
        assets=[dict(name=wheel, browser_download_url=f"{REPO_URL}/releases/download/v1.0.0/{wheel}")])
    release_file = tmp_path / "release.json"
    calls = tmp_path / "uv-calls.jsonl"
    icons = tmp_path / "Icons with spaces"
    mock_uv = tmp_path / "uv mock.ps1"
    mock_uv.write_text('''
[IO.File]::AppendAllText($env:DEEPECOHAB_TEST_CALLS, (ConvertTo-Json -InputObject @($args) -Compress) + [Environment]::NewLine)
$global:LASTEXITCODE = 0
if ($args[0] -eq 'tool' -and $args[1] -eq 'dir') {
    if ($args -contains '--bin') { Write-Output $env:DEEPECOHAB_TEST_BIN }
    else { Write-Output $env:DEEPECOHAB_TEST_TOOLS }
}
''', encoding="utf-8")
    harness = tmp_path / "invoke.ps1"
    harness.write_text('''
function Get-Command { param($Name, $ErrorAction) return [pscustomobject]@{ Source = $env:DEEPECOHAB_TEST_UV } }
function Get-Process { param($Name, $ErrorAction) if ($env:DEEPECOHAB_TEST_RUNNING -eq 'yes') { return [pscustomobject]@{ Name = $Name } } }
function Invoke-RestMethod { param($Uri, $TimeoutSec, $Headers) return (Get-Content -Raw -LiteralPath $env:DEEPECOHAB_TEST_RELEASE | ConvertFrom-Json) }
$options = @{}
$parsed = $env:DEEPECOHAB_TEST_OPTIONS | ConvertFrom-Json
foreach ($property in $parsed.PSObject.Properties) { $options[$property.Name] = $property.Value }
& $env:DEEPECOHAB_TEST_INSTALLER @options
exit $LASTEXITCODE
''', encoding="utf-8")
    environment = os.environ.copy()
    environment.update({
        "DEEPECOHAB_TEST_UV": str(mock_uv),
        "DEEPECOHAB_TEST_CALLS": str(calls),
        "DEEPECOHAB_TEST_BIN": sysconfig.get_path("scripts"),
        "DEEPECOHAB_TEST_TOOLS": str(tmp_path / "tools"),
        "DEEPECOHAB_TEST_INSTALLER": str(ROOT / "install_windows.ps1"),
        "DEEPECOHAB_TEST_RELEASE": str(release_file),
        "DEEPECOHAB_TEST_RUNNING": "no",
        "DEEPECOHAB_ACQUISITION_HOME": str(tmp_path / "application data"),
    })
    sentinel = tmp_path / "experiment.config.json"
    sentinel.write_bytes(b"retained experiment metadata")
    def run(*, release=None, running=False, **options):
        release_file.write_text(json.dumps(payload if release is None else release), encoding="utf-8")
        settings = dict(ReleaseVersion="latest", ShortcutDirectory=str(icons))
        settings.update(options)
        environment["DEEPECOHAB_TEST_OPTIONS"] = json.dumps(settings)
        environment["DEEPECOHAB_TEST_RUNNING"] = "yes" if running else "no"
        result = subprocess.run([shutil.which("powershell"), "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(harness)], env=environment,
            capture_output=True, timeout=30)
        recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8-sig").splitlines()] if calls.exists() else []
        assert sentinel.read_bytes() == b"retained experiment metadata"
        return result, recorded
    return run, tmp_path, payload, icons


@pytest.mark.parametrize("requested", ["latest", "1.0.0", "v1.0.0"])
def test_release_install_pairs_the_exact_wheel_and_constraints(installer, requested):
    run, root, payload, icons = installer
    result, calls = run(ReleaseVersion=requested)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    install = next(call for call in calls if call[:2] == ["tool", "install"])
    assert install[install.index("--constraints") + 1] == "https://raw.githubusercontent.com/Marcin-A-Lipiec/deepecohab-acquisition/v1.0.0/requirements.txt"
    assert install[-1] == payload["assets"][0]["browser_download_url"]
    assert list(icons.glob("*.lnk"))


def test_local_source_install_is_retained(installer):
    run, root, payload, icons = installer
    result, calls = run(ReleaseVersion="")
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    install = next(call for call in calls if call[:2] == ["tool", "install"])
    assert Path(install[-1]) == ROOT
    assert Path(install[install.index("--constraints") + 1]) == ROOT / "requirements.txt"


@pytest.mark.parametrize("change", ["missing-wheel", "foreign-wheel", "draft", "prerelease", "wrong-version"])
def test_invalid_release_stops_before_any_install(installer, change):
    run, root, payload, icons = installer
    if change == "missing-wheel":
        payload["assets"] = []
    elif change == "foreign-wheel":
        payload["assets"][0]["browser_download_url"] = "https://example.com/a.whl"
    elif change == "wrong-version":
        payload["tag_name"] = "v1.0.1"
    else:
        payload[change] = True
    result, calls = run(release=payload, ReleaseVersion="1.0.0")
    assert result.returncode != 0
    assert not any(call[:2] == ["tool", "install"] for call in calls)
    assert not icons.exists()


def test_running_gui_blocks_installation(installer):
    run, root, payload, icons = installer
    result, calls = run(running=True)
    assert result.returncode != 0
    assert not any(call[:2] == ["tool", "install"] for call in calls)


@pytest.mark.parametrize("force", [False, True])
def test_update_requires_vendor_wheel_when_pyspin_is_present(installer, force):
    run, root, payload, icons = installer
    environment = root / "tools/deepecohab-acquisition"
    venv.EnvBuilder(with_pip=False).create(environment)
    (environment / "Lib/site-packages/PySpin.py").write_text("# Presence probe; no camera import\n", encoding="utf-8")
    result, calls = run(Force=force)
    assert result.returncode != 0
    assert b"PySpin" in result.stderr
    assert not any(call[:2] == ["tool", "install"] for call in calls)
    assert (environment / "Lib/site-packages/PySpin.py").is_file()


def test_supplied_vendor_wheel_is_passed_to_uv(installer):
    run, root, payload, icons = installer
    vendor = root / "vendor wheel.whl"
    vendor.touch()
    result, calls = run(Force=True, PySpinWheel=str(vendor))
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    install = next(call for call in calls if call[:2] == ["tool", "install"])
    assert "--force" in install
    assert install[install.index("--reinstall-package") + 1] == "deepecohab-acquisition"
    assert install[install.index("--with") + 1] == str(vendor.resolve())
