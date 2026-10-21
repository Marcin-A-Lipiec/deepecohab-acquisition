# Changelog

## 1.0.0 — first public release (prepared locally; unpublished)

- Fixed Unicode Windows shortcut creation, update and ownership checks using
  the native Unicode shell-link API. Retained double-click launch and protected
  other applications' shortcuts. Added Unicode-path installed-wheel checks.
- Made valid fractional-second timestamps parse consistently on Python 3.10–3.12
  without changing recorded instants, timezone safeguards or output schemas.
- Corrected a regression assertion to recognize Windows short and long paths
  naming the same output directory; retained output-content verification.
- Added a step-by-step Windows README covering uv, installation, camera setup,
  update notifications, updating, rollback and removal. Preserved acquisition
  instructions in docs/USER_GUIDE.md.
- Added user-requested GUI/CLI stable-release checks. Offline failures and
  incomplete release packages are explicit; checks never install software or
  send experiment data. Added release selection to the Windows installer,
  pairing each wheel with its tagged constraints and protecting PySpin updates.
- Added an explicit per-source fixed UTC offset for historical recording PCs
  that did not adjust for daylight-saving changes. Offset-bearing timestamps
  retain their recorded offset; unresolved local times stop without dropping rows.
  The exceptional option is off by default inside Advanced options → Board clocks,
  with a visible explanation when enabled. Its choice uses the existing diagnostic
  issues section; canonical config, diagnostic and data structures are retained.
- Allowed incomplete animal metadata to be saved and RFID acquisition to start
  without metadata prompts. Preprocessing offers an explicit incomplete-metadata
  review, preserving tags and disabling unsupported interpolation when accepted.
- Made wheel input scroll forms instead of changing editor values, and moved
  Environmental settings into Experiment, between EcoHAB layout and Events.
- Refined antenna channel/indicator spacing, placed automatic layout labels
  close to their markers/tunnels without covering them, and pinned preprocessing
  below Launch at the sidebar footer. Retained manual label positions and panning.
- Assigned the first public version number and repository identity
  `Marcin-A-Lipiec/deepecohab-acquisition`. Earlier 3.2.x versions below are
  internal modernization builds; this numbering change does not migrate data.
- Retained all ten acquisition/workflow pages, RFID/TTL and camera controls,
  lighting, metadata, archived-board support, preprocessing/interpolation,
  maintenance commands, layout dragging and shared Light/Dark/System appearance.
- Prepared uv installation, native Desktop/Start menu shortcuts, pinned dependency
  constraints, external SDK/SpinView/PySpin and FFmpeg setup instructions.
- Added Windows CI for Python 3.10–3.12, installed-wheel/shortcut checks, issue and
  contribution guidance, repository metadata and a reproducible GitHub source ZIP.
- Excluded the supplied `Duplicate_test` and `Timezone_bug` recordings, reference checkout, local
  environments, caches and investigation evidence from public artifacts. Retained
  maintained scientific fixtures, firmware and bundled third-party notices.
- Protected bundled resource hashes, firmware and raw fixtures from Git's
  Windows line-ending conversion.
- Kept config v8, GUI state v1, diagnostic v12, Parquet v5, runtime dependencies,
  entry points, acquisition hardware paths and default stitching/interpolation behavior.
  The isolated startup timestamp observation remains under monitoring; it is not
  classified as an active preprocessing bug and no timing policy was changed.
- Licensed the application's original code under MIT, retaining the existing
  LICENSE and bundled third-party notices. Package metadata and release guidance
  reflect the owner's decision. Publication and physical-hardware acceptance
  remain separate.

## DeepEcoHab Acquisition 3.2.5 — modernization stage 5

- Restored layout panning immediately after Fit; added middle-drag panning over
  items and during connection tools without changing graph coordinates.
- Batched and bounded GUI notification display, retaining FIFO continuation
  between events and durable warnings/errors in the rotating application log.
- Added sequence-based, owned camera preview caching and a visible-page display
  timer honoring frozen preview FPS, retaining the original preview getter.
- Batched disk-backed output routing with bounded destination caching and
  cancellation checks; preserved scientific rows, parts, schemas and diagnostics.
- Consolidated documentation, renamed regressions by capability, removed obsolete
  patch/audit/build material and kept generated evidence under `.artifacts/`.
- Retained the local launcher environment, firmware, scientific fixtures and the
  supplied reference checkout excluded from packaging. GitHub publication remains
  pending release acceptance.

## DeepEcoHab Acquisition 3.2.4 — modernization stage 4

- Replaced horizontal page tabs with a persistent stack and collapsible sidebar
  grouped into Experiment, Devices and Workflow; retained all ten page names.
- Added a shared header with experiment identity, last configuration action,
  live device/matching/worker activity, experiment actions and pinned Stop all.
- Added compact navigation on small windows, full-name page selection, scoped
  keyboard shortcuts and per-user sidebar preferences independent of metadata.
- Preserved drafts, scene identities, manual layout navigation, preview pixels,
  recording sessions, ownership, readiness and cancellation/close behavior.
- Reflowed page controls using actual workspace dimensions. Kept the activity/error
  log visible and retained numeric page access for existing integrations.
- Included reference sidebar/brand icons and installed navigation checks, with
  no additional runtime dependencies or scientific-format changes.

## DeepEcoHab Acquisition 3.2.3 — modernization stage 3

- Added a shared DeepEcoHab visual theme: reference colors, bundled Geist/Geist
  Mono desktop fonts, SVG icons, consistent controls, tables and dialogs.
- Added Light/Dark/System selection in the status bar, live desktop appearance
  updates and saved per-user preferences alongside existing video-tool paths.
- Updated habitat graphics, activity indicators, timing previews, calendar
  affordances and camera surroundings to use shared semantic colors.
- Kept theme changes independent of scientific metadata, acquisition workers,
  preview pixels and unsaved edits. Theme changes recolor existing scene items.
- Reduced redundant activity-indicator styling, kept long save paths from
  widening Setup and improved layout fitting after hidden-tab resizing.
- Bundled font/icon licenses and resource provenance; no runtime downloads or
  additional Python dependencies. The existing ten pages and launchers remain.

## DeepEcoHab Acquisition 3.2.2 — modernization stage 2

- Added a modeless, cancellable System check, installed setup help and
  `deepecohab-acquisition-cli doctor` with separate base/camera readiness results.
- Added isolated import probes, Python/SDK compatibility reports, serial discovery,
  output checks and synthetic encode/decode/count tests for all five encoder profiles.
- Centralized FFmpeg/ffprobe discovery and per-user paths, frozen during recordings
  and shared with maintenance verification.
- Added vendor-wheel/Python installer options and expanded external SDK, SpinView,
  driver and video-tool setup instructions.
- Preserved scientific schemas, acquisition algorithms, preprocessing outputs,
  encoder profiles, hardware controls and ten acquisition pages.

## DeepEcoHab Acquisition 3.2.1 — modernization stage 1

- Added uv installation, a Windows windowed launcher, native Desktop/Start menu
  shortcut commands, installed icon/license assets, rotating logs and startup errors.
- Centralized software version; preserved config v8, GUI state v1, diagnostics v12,
  Parquet v5 and the legacy acquisition Patch 20 provenance marker.
- Made default experiment paths independent of working directory and launched
  preprocessing with the tool environment's console Python from writable job folders.
- Installed video verification and historical serial recovery commands; retained
  source launchers, console aliases and utility wrappers.
- Repaired seven obsolete regression expectations, switched validation to full
  pytest collection, and added installation/shortcut/worker checks.
- Added uv lockfile/generated dependency pins and installation/firmware/testing
  instructions. Archived native harnesses whose sketches are absent from this copy.

## Acquisition_GUI_v2.0 — Patch 3

- Fresh square four-cage layout, visible grid, reliable cage/tunnel/junction connections, explicit crossing selection and PNG export.
- Fixed elapsed-time phase schedule shared by lighting, analysis origin and daily lickometer maintenance, retaining config version 3.
- Independent GIGA lighting lifecycle with acknowledged settings, RTC continuity and persistent schedules.
- UNO R4 lickometer v6.2 with daily quiet-period calibration, configurable 90-second stuck-contact faults, bounded sensor retries and independent TTL operation.
- Versioned device status, bounded USB reconnect, identity checks, communication gaps, diagnostic history and configuration readback.
- Existing RFID/camera/preprocessing formats and algorithms retained.


## Acquisition_GUI_v2.0 — 2.0.0

- Introduced config schema v3 with canonical `config.json`, legacy-family detection, migration notices, atomic writes and config history/snapshots.
- Separated formal RFID run timing from peripheral file sessions; timezone-aware starts/stops, COM provenance, per-board epochs and durable config-override journals.
- Added RFID preflight to every start route, with Continue/Return handling for missing, malformed or incomplete metadata.
- Added v1.5 A–P protocol support and strict TTL channel/payload validation; retained v1.0 and old-board profiles.
- Made serial diagnostics mandatory and byte-faithful, with rotation under `diagnostic_logs/`; moved antenna CSVs into `raw_antenna_reads/`.
- Reworked serial lifetimes, writer readiness, bounded queues, drain-on-stop and COM ownership; matching is cancellable without leaving abandoned port readers.
- Fixed lickometer reset handling: the supplied log replays all 872 device EVENT records, including the 289 previously rejected after resets. Arduino hardware behavior is unchanged.
- Replaced Age inputs with birth dates; added subject name and the full requested animal metadata; retained RFID tag matching and optional metadata fields.
- Rebuilt EcoHAB layout editing: larger targets, readable cage text, independently movable labels, grouped tools, stable tunnel numbers, physical source IDs and branch/dead-end topology.
- Removed inferred/interpolated antenna combinations and SVG export. Added recording location and compact help.
- Added environmental schedule/timezone/analysis-origin settings and the explicit future advanced-control placeholder.
- Added Events with repeated types, multiple bouts, inferred ends with omission flags, unscheduled types and experiment notes.
- Added responsive layouts, scrollable sections, tab registration and a large resizable camera preview with collapsible settings.
- Preserved zero-valued duration/delay settings and legacy hardware aliases during migration.
- Made camera restarts use unique files; added encoder readiness, actual camera setting reports, source-frame submission ledgers and segment inventories. Forced shutdown terminates FFmpeg before pipe close.
- Added trigger acknowledgement/monitoring and frozen launch settings; optional STOP-capable trigger firmware is supplied separately from unchanged legacy sketches.
- Added regression tests, structural schema, complete example configs, raw-stream recovery and video validation tools, Windows setup/launch scripts and developer documentation.


## 0.1.4-alpha

- Scratched the previous lickometer firmware/protocol approach.
- Rebuilt the firmware directly from the last user-verified working four-channel code.
- Preserved the proven MPR121 setup, electrode mapping, debounce, BNC outputs and LED matrix.
- Removed heartbeat, START/STOP commands, configurable mapping and raw-mask telemetry.
- The Arduino now senses and drives BNC outputs whenever powered.
- LAUNCH starts/stops USB recording only.
- Added concise `EVENT,wrap,micros32,channel,state` messages.
- Added explicit micros wrap count so the PC can reconstruct 64-bit Arduino time without heartbeat.
- Retained one fire-and-forget command: `RECALIBRATE`.
- Added conservative 12-hour, long-active, high-rate and continuous-activity recalibration requests.
- Rewrote the Python lickometer parser and GUI display for the new protocol.
- Kept separate event, status and raw serial files.

## 0.1.3-alpha

- Previous diagnostic EVENT_ONLY implementation.
