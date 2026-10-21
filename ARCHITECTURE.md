# Architecture — DeepEcoHab Acquisition

## Module boundaries

| Module | Responsibility |
|---|---|
| `app.py`, `main_window.py` | Qt startup, composition, generic config actions, GUI-thread dialogs and periodic display refresh |
| `ui/registry.py` | Stable page keys, factories, display titles, groups, icons and scrolling policy |
| `ui/shell.py` | Persistent page stack, responsive sidebar, experiment header and scoped navigation shortcuts |
| `ui/notifications.py`, `ui/camera_preview.py` | Bounded notification display and GUI-owned, versioned preview cache |
| `ui/context.py`, `ui/common.py` | Narrow UI actions, reusable config controls and cancellable matching dialog |
| `tabs/*.py`, `widgets/*.py` | Widgets and user interaction; no device acquisition loop |
| `models.py` | Hardware settings and immutable file-session snapshot |
| `schema.py`, `config.py`, `config_bundle.py` | Version detection/migration, validation, owned-path updates, recoverable scientific config/GUI-state saves |
| `clock.py` | IANA timezone conversion, validation and phase-origin calculation |
| `topology.py` | Physical graph normalization, branch validation and direct relation export |
| `services/application.py` | Frozen launch commands, device state, readiness, stop coordination and health monitoring |
| `services/session.py` | Formal RFID run, event-end finalization, timing/provenance snapshots and journal |
| `services/ports.py`, `services/matching.py` | Exclusive COM ownership and cancellable tag/source discovery |
| `preferences.py` | Atomic per-user preferences with independent sections and unknown-key retention |
| `video_tools.py` | Shared executable resolution and video path preferences, separate from scientific metadata |
| `ui/theme.py`, `assets/theme.json`, `assets/theme.qss` | Qt palette, fonts, SVG icons, shared colors and live appearance preferences |
| `services/dependencies.py`, `dependency_probe.py`, `system_check_worker.py` | Readiness reports, isolated native imports and cancellable child-process checks |
| `ui/system_check.py` | Modeless check dialog, machine path settings and installed setup help |
| `protocols/*.py` | Pure parsers; no GUI or serial dependency |
| `controllers/serial_base.py` | Shared byte capture, diagnostics, bounded queue, batched writer and stop/drain logic |
| `controllers/rfid.py`, `camera.py`, `trigger.py` | Device-specific acquisition and status; `lickometer.py` is retained receive-only USB compatibility code |

The internal package name `deepecohab_recorder` is retained for compatibility.
The distribution is `deepecohab-acquisition`; `__version__` is the single source
for package, window and processing software identity. Scientific file-schema
versions remain independent. The first public release is 1.0.0; 3.2.x identifies
the preceding internal modernization stages.

`app.py` loads Qt lazily, configures rotating logs and reports startup failures.
`runtime_paths.py` resolves installed assets and per-user writable storage.
`desktop.py` creates Windows shortcuts using the native COM API, preserving
spaces and Unicode and targeting this environment's installed windowed wrapper.
`worker_process.py` launches preprocessing and system checks with the same environment's console
Python and a writable temporary working directory, preserving JSON-line progress.
Installed maintenance commands live under `maintenance/`; `tools/` retains
compatibility wrappers. Distribution entry points are declared in `pyproject.toml`.

`preferences.json` lives in per-user application storage, separate from config
and GUI-state schemas. `preferences.py` preserves unknown keys and refuses malformed
settings. `video_tools.py` resolves explicit paths before automatic discovery. A blank ffprobe
setting uses the FFmpeg directory before PATH. Camera start freezes both resolved
paths for the entire recording, including restarts and inventory finalization.
Maintenance verification uses the same resolver.

Appearance is a separate `appearance.theme` preference, defaulting to `system`.
`ThemeManager` owns the application palette and stylesheet, loads packaged fonts,
and follows Qt desktop theme notifications with a Windows fallback. Custom
graphics subscribe to its change signal and recolor existing items in place.
Workers, device commands and scientific serialization do not depend on the theme.
See [docs/APPEARANCE.md](docs/APPEARANCE.md) for extending the visual system.

System checks use QProcess; native imports and encoder probes run in bounded
child processes so DLL faults do not terminate the GUI. A cancellation file
stops the current probe before releasing temporary files. The dialog participates
in shutdown, and heavy checks are deferred during active work. Reports distinguish
core software, camera software, encoder runtime and physical devices. Add checks
in the service without adding experiment fields or acquisition tabs.

## Add a tab

Implement a QWidget accepting `ui` and declare `paths`, the config paths owned by the tab. Implement `read()` returning a dictionary of path → JSON-compatible value, and `load(config)` to display a complete normalized config. Optional `refresh()` updates status on the GUI thread. Hardware-setting tabs set `hardware = True`; their settings are captured before launch. Optional `viewport_size(width, height)` supports reflow.

Register `PageSpec('unique_key', 'Visible title', PageClass, scrollable=True,
group='Experiment', symbol='notes')` in `ui/registry.py`'s `PAGES`. Groups follow
their first occurrence; items follow registry order within each group. Page keys
remain stable when display order changes. The factory is called once; scrolling
wrappers and widgets remain mounted in `PageStack`. Use
`window.pages.set_current_key('unique_key')` to navigate. `TabSpec`, `TABS` and
`window.tabs` retain existing numeric access for integrations; the main window
uses keys. Put reusable behavior in a service/model instead of importing a sibling
page. A new hardware device also needs a config model, controller, service key/launch
integration and protocol/ownership tests; it does not require rewriting unrelated pages.

`ui/shell.py` owns presentation only. It never imports configuration into a page,
starts/stops workers, or recreates graphics on navigation. The header's Stop all
button calls the original device-stop service. Header activity uses existing state,
port ownership and worker readiness; QProcess state signals provide immediate task
updates. Display refresh continues at 250 ms. Expensive views refresh when visible;
the existing Setup/Acquisition/Environment refreshes keep shared locks current.
Viewport callbacks receive the available page dimensions after layout settles,
including changes caused by the sidebar. Manual layout zoom and pan remain intact.
Pages with their own internal scrolling use `PageViewport` to add whole-page
scrolling only when their minimum dimensions exceed the available workspace.

`ui/wheel_scroll.py` provides the common value editors. Import numeric, date/time
and combo-box controls from this module when adding controls. Their wheel events
scroll the surrounding form; deliberate keyboard/click editing and list/canvas
scrolling retain their normal behavior. Qt construction, models and non-wheel
event handling stay native.

Preprocessing's `raw_clock.py` validates an explicit per-source fixed PC offset;
`InputFile.naive_utc_offset` carries it through discovery, inventory, worker
settings and diagnostics. It affects offset-free raw timestamps only. Shared
experiment/event clock validation remains strict. `metadata_review.py` prepares
per-job limitations when the user accepts incomplete metadata; source scientific
metadata is preserved and the executed choice is recorded. Draft completeness
is separate from canonical field structure and hardware/port safety.

Sidebar collapse is saved in the independent `navigation` preferences section.
Automatic compact mode below 1100 logical pixels does not write preferences.
On narrow windows its toggle opens a full-name page menu. The header elides long
identity/config/activity text, retaining full tooltips and accessible text. Stop all
is a layout widget outside menu overflow. The global activity/error log remains
visible with a minimum height of 40 pixels. See `docs/NAVIGATION.md`.

Qt signals and widgets stay on the GUI thread. Controllers receive copied dataclasses and a frozen `SessionSnapshot`; they never read GUI fields. Services communicate status/errors via a queue. The main window connects tab actions through `UiContext`, rather than passing itself to acquisition workers.

Notifications retain FIFO order. A display turn takes at most 200 messages,
with an 8 ms collection budget; a queued timer continues remaining display work
between other Qt events. The text append and status update are batched. Warnings
and errors also reach the rotating application log directly from the service.
These display limits do not change serial/video writer queues or drop messages.

Camera preview publication retains its owned pixel copy and increments a sequence
under the preview lock. The GUI copies only new snapshots, keeps its own QImage,
and rescales when the frame or preview size changes. The visible camera page has
an independent display timer honoring the frozen preview FPS (up to 30); hiding
the page stops that timer. Recording FPS, capture ownership and writer drain
remain independent. The old `get_preview_frame()` API remains available.

Layout panning changes view scrollbar bounds and offsets, never physical graph
coordinates. Left-drag on empty space and middle-drag over any item/tool work
even immediately after Fit. Fit restores automatic scene bounds and framing.
Item dragging, grid snapping, tunnel tools and PNG export retain their ownership.

## Two kinds of session

A **file session** freezes the experiment name, directory and timezone while any device owns resources. Unique tokens distinguish every serial start and camera restart.

A **formal run** starts on the first successfully initialized RFID board, ends when the last RFID acquisition stops, and has its own UUID. Peripherals can span multiple formal RFID runs without renaming their open files. Per-board intervals, previous epochs, file paths and errors are recorded in the run provenance/journal. Changing experiment identity after all devices stop detaches the old runtime state before new metadata can be saved.

A config override is durable provenance, not an invented complete configuration. Run timing and snapshots remain available even when canonical config writing is deliberately deferred. A later Save attaches real timing. Historical snapshots are never renamed into a later run.

## Concurrency and ownership

- A serialized device-command executor handles starts/stops, with a cancellation event checked during asynchronous readiness and trigger delays.
- The port registry is shared by acquisition and matching. Ports are released only after all relevant workers exit.
- Serial capture opens data writers before device initialization. Compact counters, gaps and bounded malformed samples are persisted in GUI state. Reader and writer lifetimes are independent; stopping the producer allows the writer to drain its accepted queue.
- RFID queues are bounded at 200,000 records; retained passive USB lickometer compatibility queues at 100,000. Writer flushes occur around every 0.2 s or 2,000 rows. Production standalone lickometers are acquired through DAQ TTL inputs; routine raw-byte copies are not generated.
- The camera queue is bounded at 32 frames by default. Frame timestamps, submission ledger and inventory keep input/output accounting distinct. Forced FFmpeg shutdown terminates the process before closing the pipe, avoiding buffered-stdin deadlock.
- A health worker checks resource completion and periodically records counters/free disk space. Low space is reported; below 100 MiB it requests acquisition stops. This does not promise recovery from an inaccessible or failing disk.
- RFID status refresh is batched; tag totals update at most once per second. Detailed live camera/lickometer views refresh when visible. Device acquisition continues independently of tab visibility.

## Config edits and migration

The repository returns copies and protects updates/saves with a reentrant lock.
Known tab-owned values replace their corresponding fields, while unrelated/unknown
metadata is retained. Start/stop timestamps are owned by the run service. The
normalized editable model is partitioned into a canonical scientific config v8
and linked GUI state v1, with recoverable writes and conflict detection. Routine
metadata CSV/history/session snapshots are no longer generated. Preprocessing
publishes a recoverable data/metadata/diagnostic file set. See `docs/CONFIG_V8.md`.

Migration detects the **format family and version**. The supplied analysis-style v2 and older recorder-style v4 are both older formats; the latter is not rejected merely because its unrelated numeric version is larger than 3. Unsupported future recording schemas fail clearly. Legacy aliases are normalized before defaults, preserving zero-day durations and zero camera trigger delay.

## Physical graph

Cages have stable cell IDs; tunnels have automatically allocated stable `tunnel_N` identities. Antenna order follows start to end. A branch uses a junction referencing its parent tunnel plus a fraction along it. The parent remains intact; the new branch defaults to one antenna and a dead-end tip. Cyclic attachments and duplicated antenna assignments are rejected.

Only adjacent antenna transitions in real cage-to-cage tunnels and direct cage-side relations populate `antenna_combinations` and `tunnels_map`. Branch/dead-end geometry is saved without adding inferred behavioral transitions. The editable model holds position/label information under `layout.editor`; persistence keeps editor details in GUI state, separate from scientific topology.
