# Compatibility and migration

The current scientific contract is [config v8](CONFIG_V8.md), linked acquisition
GUI state v1, [diagnostic v12](DIAGNOSTIC_V12.md) and Parquet schema v5. Software
release numbers are separate. The historical `software_patch: 20` provenance
marker remains readable and is not the application version.

Load the experiment's main config. Relative links resolve against its directory.
Keep the main config, GUI state, diagnostic and any custom data directory together
when moving an experiment. Main scientific edits take precedence over stale GUI
state. Loading supported older files leaves their bytes unchanged; saving and
successful preprocessing publish the current recoverable file set. Missing GUI
state retains public metadata but cannot reconstruct hidden physical tunnels or
enable hardware by guessing settings.

Supported older formats include analysis-style v2, recorder-style monolithic
configs and canonical v5/v6/v7 pairs. Detection considers the format family and
version. Future unsupported versions fail clearly. Historical format contracts
and schemas remain in [legacy](legacy/README.md) for archived data readers.

Migration preserves unknown metadata, animal rows and tag strings, grouped event
bouts, device descriptions, explicit output routing and zero-valued settings.
Historical `device_antennas` become current `devices`; generated device names
retain references from events. Empty historical whole-habitat position lists
become null on save. Devices remain excluded from movement interpolation while
their physical branch connections remain part of the habitat.

Source identities combine physical channel and positive Board number, such as
`E-1` or `TTL2-2`. A historical COM suffix is resolved only through an unambiguous
mapping: COM5 does not imply Board 5. Bare channels shared by multiple boards
need explicit assignment in EcoHAB layout. Preprocessing checks the layout before
execution and offers only unambiguous repairs. Archives need no connected board.

Legacy raw, decimal and hex-counter readers remain supported. Already compressed
legacy durations cannot recreate raw detections and are rejected as raw input.
Per-board clock epochs, counter resets, rollover, source coverage and restart
part selection remain independent of USB connection names. Fixed phase cycles
use elapsed time across daylight-saving changes. Manual experiment bounds retain
automatic evidence and do not trim exported data.

Public aliases `acquisition-gui`, `deepecohab-recorder`, the source launch scripts,
numeric page access, `TABS`/`TabSpec`, passive USB controller compatibility and
`CameraController.get_preview_frame()` remain available. New code should use
stable page keys and the documented service/protocol boundaries.

## Recording PC clocks and daylight-saving time

Legacy raw `YYYYMMDD_HHMMSSmmm` timestamps contain no UTC offset or board
counter. The default parser uses the experiment's IANA timezone and rejects
ambiguous or nonexistent local times. This prevents an hour from being silently
dropped or assigned to an arbitrary instant. Current recordings with explicit
UTC offsets use those recorded offsets.

This is an exceptional option, off by default. If the recording PC kept a fixed
offset, first add its file or board folder in **Data preprocessing**. A source
whose preview contains an unresolved time stays in the list without a guessed
timestamp, and the log explains the issue; inventory and processing remain
strict. Open **Advanced options → Board clocks**, scroll the guidance to
**Exceptional fixed PC-clock offsets**, and enable it deliberately. The revealed
explanation describes the consequences. Enter the known signed offset, such as
`+02:00` or `+01:00`, only beside affected sources; blank retains normal rules.
Choose OK, then **Save preprocessing settings** to retain the declaration.
Unchecking the exceptional option clears its overrides when OK is selected.

The fixed offset applies to **all offset-free raw timestamps in that source**,
including dates outside the repeated hour. It does not change the experiment
timezone, affect timestamps already carrying offsets, or edit the original files.
Outputs remain timezone-aware in the experiment timezone, so their displayed
wall-clock hour may differ from a PC that did not follow daylight-saving rules.
The GUI-state source assignment and executed settings retain `naive_utc_offset`;
the existing diagnostic `issues.raw_pc_clock_override` category records the
choice once per source/offset. No recording-summary fields are added. Main
config structure, diagnostic envelope and CSV/Parquet fields remain canonical.

Only use this mode after establishing the recording PC's actual clock offset.
Disabling automatic changes does not establish whether it used standard or
summer time. A source whose clock actually changed needs separate, verified
offset periods; do not assign one fixed offset to the entire recording.

For example, Warsaw repeated 02:00–02:59 on 31 October 2021. A raw `02:03`
timestamp alone cannot identify which occurrence it represents. Monotonic
detections also cannot prove that the PC ignored the change when a long period
without reads could conceal the rollback. The application never infers that PC
setting from this error.

## Incomplete experiment metadata

Animal rows, including blank required fields, can be saved incrementally.
RFID recording requires valid device/port settings but does not require a
completed cohort or layout. The run journal preserves its original timing until
metadata is saved.

Preprocessing asks whether to continue at the user's own risk when metadata is
incomplete. Accepting retains all observed tags if the cohort tag list is
incomplete and disables interpolation when physical topology or antenna
assignments are unverified. Stitching of observations remains available.
The source metadata is preserved; the executed options and
`issues.incomplete_metadata_accepted` record the choice and effective changes.
Structural inconsistencies, invalid device wiring, active inputs and unsafe
output publication remain errors. Standalone worker settings can explicitly set
`allow_incomplete_metadata: true` to apply the same policy.
