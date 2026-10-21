# Testing

## Authoritative offline suite

Use a supported Python environment, preferably the tested Windows/Python 3.12
combination. uv installs both runtime and development dependencies:

```powershell
uv sync --locked --python 3.12 --group dev
uv run --locked python tools/run_validation.py
```

The runner uses **pytest** so both unittest classes and pytest function tests
are collected. It sets offscreen Qt and disables unrelated auto-loaded plugins.
It writes `.artifacts/current/regression/test_output.txt`, `pytest.xml` and
`test_results.json`, including real software/schema versions, dependency versions,
failed/skipped cases and the subprocess exit status. Use `--output PATH` to retain
a named run. Application preferences/logs are redirected into that output folder.
Generated evidence is excluded from packaging and Git tracking. Use fresh reports
for current claims; source/firmware fixtures remain part of the maintained suite.

An equivalent direct command is:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
uv run --locked python -m pytest tests -q
```

The suite covers serial parsing and recovery, ownership and worker lifetimes,
camera accounting and real FFmpeg encoding when available, GUI edit/save behavior,
config migration, scientific output contracts, publication rollback, current
lickometer compatibility and installation boundaries. On Windows, native COM
shortcut tests check Unicode/spaces, updates/removal and protection of an unrelated
shortcut. Sample experiment files are temporary; physical acquisition is not started.

Readiness tests cover saved paths without PATH, Unicode/spaces, frozen
paths through production writer drain, malformed preferences, optional camera
software, SDK release-family checks, CPU fallback, native probe exits/timeouts,
cancellation, responsive GUI progress and safe close. A real readiness report
can be generated while acquisition is stopped:

```powershell
deepecohab-acquisition-cli doctor --json --encode-test --output-dir "D:\Experiments"
```

This tests all five profiles with three synthetic 256×256 frames. Listed encoders
are distinct from runtime-tested encoders. Software readiness is distinct from
physical-camera, trigger and sustained-throughput acceptance.

### Appearance and display scaling

`tests/test_navigation.py` checks all ten stable page keys, mouse/keyboard and
compact-menu navigation, actual workspace resizing down to 640×480, visible Stop
and errors, saved sidebar settings and failed/corrupt preferences. It exercises
unsaved drafts, graph identities/selection/manual view transforms and camera image
retention, actual simulated RFID stop/drain, matching ownership/load locks, full
save/load shortcuts, System check cancellation and real preprocessing worker
completion, cancellation and deferred close. A test-only gate holds the worker
before execution to make overlap deterministic; it then runs the production worker.

`tests/test_theme.py` covers saved appearance/video settings, restart and malformed
preferences, desktop changes, font fallback, resource hashes, contrast, unsaved
metadata and graph state, camera image retention, active RFID recording controls,
and page reachability at 800×600 and 1280×800. Deferred Qt deletions are drained
between appearance tests so earlier closed test windows are released.

Generate local PNG/HTML previews of every page, System check, calendar and
enabled/disabled checkbox/radio states in a fresh process for each scale:

```powershell
uv run --locked python tools/capture_theme_preview.py --output .artifacts/current/appearance/scale-100 --scale 1
uv run --locked python tools/capture_theme_preview.py --output .artifacts/current/appearance/scale-125 --scale 1.25
uv run --locked python tools/capture_theme_preview.py --output .artifacts/current/appearance/scale-150 --scale 1.5
uv run --locked python tools/capture_theme_preview.py --output .artifacts/current/appearance/scale-200 --scale 2
```

Each run includes both themes at 640×480, 800×600 and 1280×800, plus manually
collapsed navigation, long experiment names/paths with synthetic active states,
and dialog previews. Stop all visibility is asserted in every window capture. Review
`index.html` or the original PNGs; overview images make the ten pages easier to
compare. Offscreen Qt can report platform/font-directory notices despite the
bundled fonts loading correctly. Parse/render failures require investigation.

### Native firmware

Install a C++17 `g++` compiler to run the two native firmware tests. Without it,
pytest explicitly skips them. The tests cover the included v8.0 standalone
sketch and all 24 scenarios in the v8.1 diagnostic package. The latter is a bench
diagnostic with an unresolved physical fault, not production certification.
Historical orphan harnesses are explained in `tests/legacy/README.md`.

## Installed-wheel and shortcut check

Build a wheel and source archive:

```powershell
uv build
```

Verify that the wheel and source archive match maintained files and exclude
the reference checkout, caches and generated evidence:

```powershell
uv run --locked python tools/verify_distribution.py --wheel dist/deepecohab_acquisition-1.0.0-py3-none-any.whl --sdist dist/deepecohab_acquisition-1.0.0.tar.gz
```

Add `--baseline-wheel PATH` to report changed runtime files and require unchanged
dependency metadata and entry points. Intentional dependency/launcher migrations
should instead compare those changes explicitly during their feature review.

`tests/test_release_packaging.py` checks that the curated GitHub ZIP excludes
local recordings, environments, secrets, links/junctions and the reference copy,
while preserving firmware, scientific fixtures, third-party notices and CI.
Run `uv run --locked python tools/prepare_github_release.py --tag v1.0.0` after
the distribution check to create that ZIP, a release manifest and `SHA256SUMS`.
Add `--require-license` when preparing a public release; the command intentionally
fails until the owner selects a license and adds the file and project metadata.
See [docs/RELEASING.md](docs/RELEASING.md).

The GitHub Actions workflow runs the suite on Windows with Python 3.10, 3.11 and
3.12. The 3.12 job also builds and audits the packages and exercises the installed
GUI and native shortcut. CI retains explicit software reports and release assets;
it neither publishes a release nor starts physical acquisition. Hosted CI results
only exist after this repository is uploaded and the workflow runs.

To avoid changing a personal tool installation, set `UV_TOOL_DIR` and
`UV_TOOL_BIN_DIR` to absolute directories within a temporary audit folder, then
install the wheel with `uv tool install --python 3.12 WHEEL_PATH`. Run:

```powershell
uv run --locked python tools/validate_installation.py --tool-root AUDIT_TOOL_DIR --output .artifacts/current/installed
```

The helper starts the installed Windows GUI wrapper outside the source directory,
checks its windowed executable format, ten pages, icon, antenna inventory,
preprocessing, cancellation, packaged System check/help and responsive progress.
It checks offline fonts/icons, Light/Dark switching, persistent preferences,
stable page identities, expanded/compact navigation and Stop all at small sizes.
It also runs the installed CLI doctor. It creates a native shortcut in an audit folder,
reads it back, launches both its target and the actual Windows .lnk association
without console handles, and checks installed maintenance entry points.
Test application data is redirected into the audit
directory. No shortcut is placed on the real Desktop by this helper.

To rehearse the PowerShell installer with separate tool directories, preserved
preferences/data and paths containing spaces/Unicode:

```powershell
uv run --locked python tools/validate_dependency_installer.py --uv "C:\Users\YOUR_NAME\.local\bin\uv.exe"
```

This helper uses the local cache offline, records installation/reinstallation,
missing-wheel rejection and removal, and confines its audit to the checkout.
It does not validate a real vendor SDK/wheel or start acquisition.

For a release acceptance, also install on a fresh Windows PC, double-click the
actual Desktop icon in Explorer, and confirm normal visible-window behavior.
Verify install/update/rollback/removal with experiment data retained, including
the exact camera vendor wheel used on that PC.

## Physical acquisition acceptance

On the working acquisition hardware, verify simultaneous RFID/camera/lighting/
trigger use, all TTL channels, readiness and delayed trigger, disconnect/reconnect,
normal stop while writers drain, close/relaunch, insufficient disk space,
interrupted writes and representative recording duration. Verify SpinView/PySpin,
CPU/NVENC encoders and a full video decode/frame-count check. Read the scientific
exports with the pinned downstream analysis release. Native/offscreen tests cannot
certify electrical timing, SDK drivers or mouse sensitivity.

Application logs, package resources, experiment files and scientific schema
versions must remain independent of the tool installation directory. New features
should add meaningful regressions at the service/protocol boundary affected.

## Performance and scientific equivalence

The display regressions exercise real background/middle/cage mouse gestures,
Fit/navigation/PNG state retention, queued notification ordering and event-loop
yielding, durable faults, preview ownership/unchanged frames/resize/replacement,
visible preview FPS and bounded/cancellable disk-backed routing. Existing
regressions are named by behavior; their assertions remain collected.

Measure five trials without hardware or timing assertions in the test suite:

```powershell
uv run --locked python tools/benchmark_performance.py --output .artifacts/current/performance
```

To compare with a prior release, extract its wheel to BASELINE_PACKAGE_PARENT,
then run both commands with the same interpreter/dependencies:

```powershell
uv run --locked python tools/benchmark_performance.py --package-parent BASELINE_PACKAGE_PARENT --output .artifacts/current/performance-before
uv run --locked python tools/compare_pipeline_outputs.py --baseline BASELINE_PACKAGE_PARENT --output .artifacts/current/pipeline-comparison
```

The comparator runs nine synthetic/legacy scenarios and requires identical CSV
and Parquet bytes, logical rows, schemas and diagnostic content. Only diagnostic
creation time, software identity and fixture absolute root are normalized. It
uses the checked-in regression fixtures; a standalone installed wheel does not
ship tests. See [docs/PERFORMANCE.md](docs/PERFORMANCE.md) for measured results.

## Version 1.0.0 local preparation evidence

Validated on 2026-10-20 with Windows 11 x64, Python 3.12.12 and uv 0.10.7:

| Check | Result |
|---|---|
| Full pytest collection | 403 tests and 11 subtests passed; two native firmware tests skipped because g++ is unavailable |
| Scientific comparison against internal 3.2.5 | Nine scenarios, 66 output files: identical CSV/Parquet bytes and equivalent normalized diagnostics |
| Wheel/source audit | Maintained runtime files/assets match source; runtime dependencies and entry points unchanged |
| Runtime comparison | Added explicit per-source raw clock interpretation, draft metadata/preprocessing review, wheel protection and navigation changes; default scientific outputs remain equivalent |
| Isolated pinned uv tool installation | All seven exposed commands installed; GUI and legacy launch commands checked |
| Installed GUI and native Windows shortcut | Ten pages, themes, workers, cancellation, responsive System check, packaged help and no-console .lnk launch passed |
| CI configuration | YAML parsed and embedded PowerShell syntax checked locally; hosted workflow has not run |

Detailed reports are retained locally under `.artifacts/prepublication-tweaks-1.0.0/` and are
excluded from public distributions. The user previously confirmed the visible
Desktop double-click launcher. Physical acquisition, a fresh-PC installation and
downstream application acceptance have not been performed by these release checks.
The original-code license remains undecided; this is local preparation, not a
published release. The observed startup timestamp incident is under monitoring
and remains separate from the explicit historical recording-PC clock option.

New regressions exercise ambiguous/nonexistent raw times, fixed offsets across
both DST boundaries, authoritative recorded offsets, portable source assignments,
exceptional-option defaults/cancellation, canonical file structure, draft saves, metadata-free RFID start, preprocessing
consent, preserved unlisted tags, disabled inference for incomplete layouts and
wheel scrolling over focused editors and editors inside fitted tables. The
repeated-window fixture releases unreachable Qt wrappers between windows,
outside Qt model construction, after intermittent native Qt failures during
development. Full regression, a 30-window lifecycle check and installed GUI /
shortcut checks passed; physical-hardware acceptance remains separate.
