# Performance and project maintenance

Release 3.2.5 restores layout background dragging after Fit. Left-drag empty
space to reposition the canvas; middle-drag works over items and while a tunnel
tool is active. Cage/label/device movement remains separate and Fit restores
automatic framing. The view needs scrollable scene bounds even when its graph
fits the viewport; these are display bounds, not graph coordinates. See Qt's
[sceneRect documentation](https://doc.qt.io/qt-6/qgraphicsview.html#sceneRect-prop).

## Measured changes

Five trials used the same Windows/Python 3.12.12 interpreter and dependencies,
comparing the saved 3.2.4 wheel with 3.2.5 source. These hardware-free measurements
describe the named operations, not whole-application or acquisition throughput.

| Operation | Before median | After median |
|---|---:|---:|
| First GUI refresh with 10,000 pending messages | 431.84 ms | 0.95 ms |
| Complete display of that burst, yielding between batches | 447.02 ms | 91.67 ms |
| 200 updates of one unchanged 640 × 640 preview | 334.68 ms | 0.51 ms |
| Route/index 100,000 rows in disk-backed SQLite | 674.84 ms | 165.94 ms |

Message display retains FIFO order and schedules continuation between Qt events.
Each collection has a 200-message cap and an 8 ms budget. Append/status updates
are batched; warnings/errors also go to the rotating application log before
display. The on-screen log retains its existing 2,000-line bound. Acquisition
data is written by its existing separate queues and writers.

The camera producer still owns its copied preview pixels. A locked sequence
allows the GUI to skip unchanged snapshots, image conversion and scaling;
resizing uses its owned cached image. The visible camera page honors the frozen
preview FPS (maximum 30) on a separate display timer; hidden pages stop that
timer. Recording FPS, timestamps, queue size, writer drain, FFmpeg restart and
CPU fallback behavior remain unchanged.

Routing streams batches of at most 4,096 rows into SQLite using executemany,
with a bounded 512-entry destination cache and cancellation between batches.
It preserves destination policy, IDs, restart parts, output order and all
CSV/Parquet schemas. No whole-recording in-memory table is introduced.

Cross-version checks cover nine scenarios, all interpolation modes, split/merged
restarts, aliases, device/TTL groups, rejected tags and legacy input profiles.
All 66 CSV/Parquet files are byte-identical. Every diagnostic value matches
after excluding only creation time, software identity and temporary fixture
root paths. Preview pixels and routed-row fingerprints also match.

Reproduce measurements and comparisons with the developer commands in
[TESTING.md](../TESTING.md). Raw evidence lives under ignored `.artifacts/`.
Physical camera/DAQ throughput and electrical timing need hardware acceptance.

The full 3.2.5 Windows suite passes 369 tests and 11 subtests, with two native
firmware tests skipped because g++ is unavailable. All 32 original Python
regression modules retain their ASTs after import/filename renames. Isolated uv
installation, outside-source launch, native Windows shortcut launch, all ten
pages, worker completion/cancellation, System check and the installed display
checks pass. Both themes render at 100% and 200% scaling across the existing
small/wide window sizes. GitHub publication and physical acceptance remain pending.

## Folder policy

Runtime code/assets live in `deepecohab_recorder/`; hardware examples in
`firmware/`; tests and real stream fixtures in `tests/`; developer utilities in
`tools/`; current guides in `docs/`, with prior contracts in `docs/legacy/`.
Root documents cover getting started, architecture, changes, testing and notices.
Regression filenames describe capabilities rather than patch numbers.

The supplied `DeepEcoHAB_reference_repository/` is retained for comparison and
excluded from package discovery, source/wheel builds and Git tracking. Runtime
fonts/icons/help are already bundled and do not depend on that checkout.
Generated validation, installation rehearsals and benchmarks go in `.artifacts/`,
excluded from packaging/tracking. `dist/` contains local release artifacts;
build caches and old stage environments are disposable.

The working `.venv/` and its `.uv-python/` base interpreter are retained so the
local double-click shortcut continues to work. Do not remove the base interpreter
while that environment depends on it. The root shortcut uses the installed
windowed launcher and the bundled source icon, independent of old audit folders.

Obsolete root PATCH documents, stale manifests/reports, per-stage rollout
documents and generated stage environments are removed after checks. Their
useful behavior is retained in current guides, compatibility contracts, changelog
and regressions. Unique firmware diagnostic evidence and scientific fixtures
are preserved. Future patch work should update these maintained documents and
tests instead of adding root patch reports.
