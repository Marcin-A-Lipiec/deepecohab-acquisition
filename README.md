# DeepEcoHab Acquisition 1.0.0

Windows Qt application for RFID/TTL and camera acquisition, experiment metadata
and preprocessing before DeepEcoHab analysis. Install with uv and launch from a
Desktop icon. Startup logging and a cancellable **System check** help with
dependency setup. The Light, Dark and System themes share DeepEcoHab's colors,
Geist typography and icons. A collapsible sidebar keeps every acquisition page
mounted, with experiment identity, live activity and a pinned **Stop all** control
in the header. Recording and verification share saved FFmpeg/ffprobe paths.
Layout background dragging works after Fit; middle-drag pans in every layout
tool. Display bursts and repeated camera frames use bounded, cached updates.
See [docs/PERFORMANCE.md](docs/PERFORMANCE.md) for measured changes and
[ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries and extension guidance.

Scientific formats remain config v8, acquisition GUI state v1, diagnostics v12
and Parquet schema v5. Current contracts are documented in
[docs/CONFIG_V8.md](docs/CONFIG_V8.md) and [docs/DIAGNOSTIC_V12.md](docs/DIAGNOSTIC_V12.md).
Software version is independent of these formats.

Version **1.0.0** is the first public release number; earlier 3.2.x numbers identify
internal modernization stages. The intended repository is
[`Marcin-A-Lipiec/deepecohab-acquisition`](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition).
This copy is prepared locally; GitHub publication has not taken place. The original
code's license is [still being decided](LICENSE_STATUS.md).

## Start on Windows

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), open
PowerShell in this application folder, and run:

```powershell
uv tool install --python 3.12 --constraints requirements.txt .
deepecohab-acquisition-shortcut
```

Double-click **DeepEcoHab Acquisition** on the Desktop. Python and the declared
Python dependencies are installed into a dedicated tool environment. If a
command is not found, run `uv tool update-shell` and open a new terminal.
The local installer can perform both steps using the tested dependency pins:
`powershell -ExecutionPolicy Bypass -File .\install_windows.ps1`.

[docs/INSTALLATION.md](docs/INSTALLATION.md) includes the exact v1.0.0 release-wheel
and GitHub installation commands to use after publication, camera prerequisites,
updates, removal and troubleshooting. The local commands above work with this copy.
Python 3.12 on 64-bit Windows is the validated target;
package metadata also permits Python 3.10–3.11.

For cameras, separately install the matching Teledyne Spinnaker SDK/SpinView and
vendor PySpin wheel, plus **ffmpeg.exe and ffprobe.exe** selected in **System check**
or available on PATH. RFID/TTL and
preprocessing work without the camera SDK. Firmware/USB drivers are described
in [docs/FIRMWARE.md](docs/FIRMWARE.md).

Open **Experiment → System check** in the header to check the computer, test video encoders
and read installed setup help. The equivalent command is
`deepecohab-acquisition-cli doctor --encode-test`. See
[docs/CAMERA_SETUP.md](docs/CAMERA_SETUP.md) for SDK/wheel compatibility and
physical acceptance requirements.

The existing `setup_windows.bat` and `run_windows.bat` support a local `.venv`
installation. Source launchers `run_acquisition_gui.py` and `run_deepecohab.py`
and console aliases `acquisition-gui` / `deepecohab-recorder` remain available.

Choose **Theme: System, Light or Dark** at the bottom right. System follows the
desktop appearance; your selection is retained across restarts and reinstalls.
Fonts, icons and both themes are bundled in the installation and work offline.
Switching themes preserves unsaved edits, layout placement, camera previews and
running device controls. See [docs/APPEARANCE.md](docs/APPEARANCE.md).

Use the sidebar's **Experiment**, **Devices** and **Workflow** groups to navigate.
On a narrow window it becomes an icon rail; its bottom button opens a menu with
all page names. **Ctrl+Tab** / **Ctrl+Shift+Tab** cycle pages, **Ctrl+B** controls
the sidebar, and **Ctrl+S** / **Ctrl+O** save/load the full experiment configuration.
The header and activity/error log stay visible on every page. See
[docs/NAVIGATION.md](docs/NAVIGATION.md).

## Prepare an experiment

- **Setup:** enter the experiment name, project name, save directory, recording location, timezone and analysis start phase. Experiment recording times are automatic by default. Check **Set start and end datetimes manually** to enable the two datetime fields, then save both overrides. Load/Save experiment configuration operates on all tabs. Selecting a directory with a compatible configuration loads it automatically. The collapsed Quick start section explains new and archived experiments and how recording times are determined.
- **Acquisition:** set the connected boards' COM ports and hardware versions. v1.5 uses physical **A–P** channel names; v1.0 uses **0–9/A–F**; the old board displays **1–8**. Refreshing ports preserves the current choice, including a blank one. Archived data does not require a connected board.
- **Animal details:** enter or match RFID tags and complete mouse line, genotype, genetic background, sex and birth date. Subject name, Treatment and Notes are optional; blank Subject name and Treatment save as null. Dates use `YYYY-MM-DD`; there is no Age field. Tag strings are retained as supplied; use Match tag to check the identifiers actually reported by your boards.
- **EcoHAB layout:** create cages and tunnels, then assign each antenna to a physical channel and Board number. Identifiers look like `1-1`, `8-2` or `E-1`; the hyphen is inserted automatically. For archived boards, select Manual / archived board and use the same Board number as in Data preprocessing. The four-cage preset supplies geometry with unassigned antennas. Double-click a cage, tunnel or antenna to edit it. Labels can be dragged independently, and cage/antenna graphics remain readable while zooming. Drag empty canvas space to pan; middle-drag pans over any item or tool. Fit restores automatic framing. On small displays use the table/section scrollbars.
- **Environmental settings:** choose the initial light/dark phase hours and configure optional Arduino lighting control. All cycles use fixed elapsed time; daylight-saving changes cannot shorten, lengthen or realign them. There is no local-clock scheduling mode. Timezone and analysis start phase are on Setup. Temperature/humidity recording remains a future extension.
- **Events:** enter event types and bouts. Repeated names must use one description. A name/description without times defines an unscheduled type. A bout requires a start. Leaving the end blank means “until the RFID recording ends”; GUI completion flags are retained in the secondary config. Select optional devices for each event. Selected bout compartments are saved as lists; no position (Whole habitat) saves as null. Experiment notes are saved with the Events tab.
- **Data preprocessing:** preprocess raw recordings before downstream analysis. Place archived files in `raw_antenna_reads`; each immediate subfolder is one board's continuous recording. Assign distinct Board numbers to simultaneous historical boards, and use the same recording part for them. No COM label is needed; Port metadata is read-only connection information. Save these assignments with the metadata. Before processing, the GUI checks layout antenna labels and offers to add missing board suffixes when each antenna has one possible board. Shared channel numbers across boards require manual assignment in EcoHAB layout and a configuration save. See [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).
- Use the prominent Load/Save buttons in each metadata tab, or the full-config controls at the top. Section saves preserve the other sections. **Save experiment configuration** saves all current tab values. Incomplete drafts can be saved; **Check configuration** checks the on-disk metadata, including archived board assignments. Starting RFID also checks the actual acquisition hardware settings.

The main metadata filename is **<Experiment_name>.config.json**, in the **Experiment's save directory**. It links to **<Experiment_name>.acquisition_gui_state.json** and, after successful preprocessing, **<Experiment_name>.diagnostic.json** in the same folder. Load only the main config; keep the experiment folder together when moving it. Older names and the legacy `acqusition` spelling remain readable. Loading leaves sources unchanged. Saving writes the version 8 pair under the new names; successful migration retires the previous main file and its owned sidecar. No routine history snapshots are created. See [docs/CONFIG_V8.md](docs/CONFIG_V8.md) for the downstream contract.

## Start and stop

RFID recording can start with missing or incomplete experiment metadata, without a metadata confirmation. Hardware settings and port ownership are still checked before devices start. Raw data and original timing are retained in a run journal; a later metadata save attaches the original RFID start time. Animal details can be saved incrementally, including blank fields. **Check configuration** remains available for a readiness review.

Before preprocessing, missing metadata produces a **Proceed at your own risk** choice. Cancel returns to editing. Proceeding with an incomplete RFID tag list disables tag filtering for that job, preserving all observed tags; an incomplete or unverified layout disables interpolation. These decisions are recorded in diagnostics. Correct inconsistent device wiring and conflicting metadata values before saving or processing.

For legacy timestamps without UTC offsets, timezone transitions can require an explicit interpretation. An exceptional option is available under **Data preprocessing → Advanced options → Board clocks**. It is off by default; enabling it reveals the explanation and fields for verified fixed recording-PC offsets. An unresolved source can be added for inspection, but processing stops until its clock is resolved. Do not infer a fixed offset from the error itself. See [recording clock guidance](docs/COMPATIBILITY.md#recording-pc-clocks-and-daylight-saving-time).

In automatic mode, the first successfully initialized RFID board defines the whole experiment's `recording.timeline.start_datetime`. Starting another board, restarting recording or reopening the same experiment preserves that first start. `end_datetime` follows the latest normal RFID stop across runs. A detected failure marks the end for recovery; after a crash or power loss, preprocessing the complete experiment uses the last valid raw RFID PC datetime. For archives without acquisition journals, raw detections supply missing bounds. TTLs, cameras and trigger controllers do not define these times. Select all experiment raw sources when recovering bounds; known omitted sources leave the saved bounds unchanged.

Manual overrides on Setup remain selected during recording and preprocessing. Automatic evidence is retained separately, so unchecking the override and saving returns to automatic times. These metadata values do not trim exported data. Restarting RFID after all RFID boards stop still creates a separate acquisition run and journal. Writers may continue draining after a normal stop boundary.

In a coordinated launch, the camera and encoder become ready before the external trigger is armed. The configured delay starts after readiness; settings are frozen when the command is requested. A failure partway through launch is reported and any devices already recording remain visible and can be stopped individually.

Experiment name, directory and timezone are locked while a device or tag-matching connection is active. Device settings are locked while its workers are active. Closing the application requests stops and waits for file writers; a timed-out worker stays locked instead of being reported as safely closed.

**Physical-output behavior:** stopping USB acquisition does not necessarily stop the independently powered hardware. Legacy trigger firmware may have no STOP command; the GUI reports that its programmed train may continue until completion or hardware reset. STOP-capable protocol support is retained, but its historical optional sketch is absent from this copy. The standalone lickometer senses and drives BNC outputs whenever powered; RFID DAQ rising-edge inputs record the contacts. It has no production USB acquisition tab or separate CSV writer. Lighting v5 also continues its schedule when USB recording stops or the GUI closes. No firmware is flashed automatically. See [docs/FIRMWARE.md](docs/FIRMWARE.md) for the actual inventory and the v8.1 diagnostic build's limitations.

## Files written

| Location | Contents |
|---|---|
| `<Experiment_name>.config.json` | Canonical scientific metadata and relative file links |
| `<Experiment_name>.acquisition_gui_state.json` | Hardware and editor settings, grouping choices, recording epochs, health summaries and camera inventories |
| `<Experiment_name>.data.parquet` / `.csv` | Main movement RFID output |
| `<Experiment_name>.device_antenna1.parquet` / `.csv` | A numbered device RFID group and its associated TTL inputs; further groups use 2, 3, etc. |
| `<Experiment_name>.TTL_data1.parquet` / `.csv` | A numbered group of instantaneous TTL events; further groups use 2, 3, etc. |
| `<Experiment_name>.diagnostic.json` | Completed preprocessing checks, all transit medians, recording coverage and output manifest |
| `DATATYPE READ ME.txt` | Concise descriptions of actual files and output-group inputs |
| `raw_antenna_reads/` | Original RFID/TTL streams, one file per board start; archives may use a subfolder per board |
| Experiment root | Camera video segments, timestamps and frame ledger |

Data preprocessing defaults to the experiment root. The diagnostic and linked config pair always stay there, even if a custom data-output folder is selected. When Merge recording parts is disabled, the type includes a part suffix, for example `<Experiment_name>.data_part01.parquet`.

Set each antenna role in **EcoHAB layout**. Main is the default. Device reveals its type, optional description, position, generated type-position name and optional board-qualified TTL association. Devices with the same generated name share a numbered device file, including linked TTLs. Tunnels without assigned main antennas are kept in GUI state and omitted from the main tunnel list. **Manage RFID and TTL output grouping** previews these defaults and can override each input independently, including detaching TTLs. Restore layout defaults to clear overrides. Unassociated TTLs default to TTL_data1. Only groups with records create files. TTLs never enter movement data and are never stitched or interpolated.

Successful reprocessing replaces the current generated file set and retires obsolete outputs from its previous diagnostic manifest. Raw/unrelated files and externally modified generated data are protected. Failed/cancelled jobs keep previous completed results. A temporary recovery journal restores an interrupted publication on reload or retry. Existing archived folders and logs are not swept or deleted. New runs create no metadata CSV, `acquisition/`, `session_configs/`, `diagnostic_logs/` or default `preprocessed_data/` folder.

Raw acquisition RFID CSV rows remain five semicolon-separated columns:

```text
physical_source;tag_or_ttl_payload;device_counter;pc_datetime;board_id
P-1;D5969C1A;07DF6948;2026-09-10T13:20:00.123456+02:00;1
TTL2-1;00000002;07DF6950;2026-09-10T13:20:00.123456+02:00;1
```

New source identifiers use the physical channel and logical Board number, independent of the USB COM port. Legacy `A_1_COM5`, `ACOM5` and bare labels remain readable; conversion requires an unambiguous board assignment. Changing USB ports does not rename a board. Device counters and tag/TTL payloads are preserved, and host calendar timestamps include the experiment timezone offset. Acquisition counters, RFID start/stop epochs and a bounded sample of malformed records are saved in GUI state, then included in preprocessing diagnostics. Routine serial-byte copies are no longer generated. Historical raw logs remain readable with `tools/recover_serial.py`.

Preprocessed **Parquet** columns, in order, are `datetime`, `antenna`, `time_under`, `animal_id`, `internal_board_timestamp`, `inserted`. The last field is Boolean: True for rows created by interpolation, False for observations derived from raw records. `antenna` is a dictionary-encoded string field read by Polars as Categorical, `time_under` is a microsecond duration, and `animal_id` is a dictionary categorical with Polars Enum metadata. Antenna labels such as `1-1` keep their exact text values. Both timestamp columns use microseconds and the experiment timezone. Legacy sources without a board counter retain null internal board timestamps. Preprocessed **CSV** columns are `antenna`, `animal_id`, `datetime`, `time_spent`, `internal_board_timestamp`, `inserted`; `time_spent` remains integer milliseconds. Device RFIDs and their linked TTLs share device outputs by default; custom overrides can separate them. Completed settings are linked from the diagnostic to GUI state. Coverage, interruptions and interpolation details remain in the diagnostic; integrity checks stay internal.

Camera segment prefixes include a unique token on every start, preventing overwrites after restart. The timestamp CSV records source and hardware frame IDs and whether each frame entered the queue. The output ledger links source frames to encoder parts and input order; an inventory records clean/forced closure and container counts. “Submitted” means accepted by the encoder pipe, not independently confirmed as decodable. For a full read-only decode/count check after stopping:

```bat
deepecohab-verify-video PATH_TO_EXPERIMENT.config.json
```

Files are flushed in batches; this is not a guarantee of zero loss during power failure. Queues are bounded, and overflow, parse failures, disk pressure and worker failures are surfaced. Maintain sufficient disk capacity for raw recordings, preprocessing and camera recording.

## Extension and schema documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): modules, tab registration and concurrency contracts.
- [docs/CONFIG_V8.md](docs/CONFIG_V8.md): current canonical/secondary file ownership and fixed-cycle semantics.
- [docs/config_v8.schema.json](docs/config_v8.schema.json) and [docs/acquisition_gui_state_v1.schema.json](docs/acquisition_gui_state_v1.schema.json): machine-readable contracts.
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md): supported historical formats and migration behavior.
- [TESTING.md](TESTING.md): reproducible offline tests and hardware acceptance.
- [CHANGELOG.md](CHANGELOG.md): public release and internal modernization history.
- [CONTRIBUTING.md](CONTRIBUTING.md): development setup and compatibility expectations.
- [docs/RELEASING.md](docs/RELEASING.md): reproducible builds and GitHub publication steps.

Pages are separate Python modules assembled by one registry. New pages normally require one module and one registry entry; production code does not depend on chaining Jupyter notebooks.

Animal details require tag, mouse line, genotype, genetic background, sex and date of birth. Subject name and Treatment are optional and save as null when blank; Notes remain optional. Genotype/background presets are editable; Sex is Male or Female only. Older incomplete animal records remain loadable but must be completed before saving.

Setup’s **Define experiment’s end moment** saves `end_with` for downstream analysis only. It does not stop acquisition or trim data. The fixed elapsed-time light/dark cycle and automatic/manual recording datetimes retain their existing behavior.

A tunnel’s `length` is the distance in centimetres between the centres of its two main antennas. Unknown or inapplicable length is null. Historical multi-antenna measurements remain in GUI state for review.

Add a whole board folder to report its raw files as one recording. Routine member files and internal boundaries are omitted from the diagnostic; real acquisition interruptions and clock resets remain. Separately selected files retain the adjustable three-minute gap reporting threshold. See [docs/DIAGNOSTIC_V12.md](docs/DIAGNOSTIC_V12.md) for the compact report contract and its embedded glossary.

## Project layout

Runtime code and packaged assets are in `deepecohab_recorder/`; maintained guides
are in `docs/`; regression fixtures/tests, firmware and developer commands are
in `tests/`, `firmware/` and `tools/`. Historical format contracts are in
`docs/legacy/`. Generated evidence belongs in ignored `.artifacts/`. The supplied
reference checkout is retained and excluded from packaging and tracking.
`Duplicate_test/` contains local investigation data and is also excluded; it is
not part of the regression suite or a public example.
See [docs/PERFORMANCE.md](docs/PERFORMANCE.md) for maintenance details and
[docs/ROADMAP.md](docs/ROADMAP.md) for the remaining release stage.
