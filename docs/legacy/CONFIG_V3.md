> Historical contract. For current behavior see [compatibility](../COMPATIBILITY.md) and [config v8](../CONFIG_V8.md).

# Configuration version 3

The canonical file is `config.json`. The structural schema is supplied as `config_v3.schema.json`. The application uses its own migration/semantic validators, with no runtime dependency on a JSON Schema library. Saving an incomplete draft is allowed; RFID preflight additionally requires usable animal and physical layout data.

```json
{
  "version": 3,
  "recording": {
    "name": "experiment",
    "recording_location": "",
    "notes": "",
    "timeline": {
      "start_datetime": null,
      "end_datetime": null,
      "recording_timezone": "Europe/Warsaw",
      "start_from": "dark_phase",
      "phases": {"light_phase": "00:00", "dark_phase": "12:00"}
    },
    "cohort": {"n_mice": 0, "animals": []},
    "layout": {
      "cages": [], "tunnels": [], "junctions": [],
      "antenna_combinations": {}, "tunnels_map": {}, "editor": {}
    },
    "events": {"event_types": [], "bouts": []}
  },
  "acquisition": {
    "boards": {}, "camera": {}, "hardware_trigger": {}, "lickometer": {},
    "environment": {"lighting": {"enabled": false, "com_port": "", "baudrate": 115200, "device_uid": null}}, "launch": {},
    "com_ports": {}, "_provenance": {}
  }
}
```

`acquisition.com_ports` is a JSON **object**, for example `{"COM3":"RFID_DAQ_1","COM6":"Hardware_trigger"}`. It records serial devices opened by the application for the run. A configured placeholder controller is not presented as a connected device. Detailed board configuration and actual RFID intervals live under `acquisition.boards` and `_provenance.rfid_devices`.

## Animals

`recording.cohort.animals` is a list. Each row has `tag`, `row_index`, `mouse_line`, `genotype`, `subject_name`, `sex`, `date_of_birth`, `genetic_background`, `treatment` and `notes`. The count must equal the number of rows. Tag and subject name must be nonempty, and a valid calendar birth date is required for successful RFID preflight; optional values may be empty strings. Tags are compared case-insensitively for duplicate detection. Migration preserves duplicate/incomplete rows for correction instead of discarding them by dictionary key. Legacy Mouse ID becomes subject name; age fields are removed.

Birth dates stay date-only. New calendar datetimes include UTC offsets; daily phase hours stay `HH:MM`. Local event datetime input is interpreted in the selected experiment timezone. Ambiguous or nonexistent local datetime input around daylight-saving changes is rejected: enter an explicit offset to identify the intended event instant.

## Actual recording time and analysis origin

The first RFID initialization time and last RFID stop time are written to `start_datetime` / `end_datetime`; peripherals do not define them. The software records host initialization/closure times, not a hardware-synchronized absolute clock or a guarantee of timing accuracy beyond the PC clock.

The service also writes `analysis_start_datetime`, `missing_initial_seconds` and `recorded_before_origin_seconds`. If the selected phase is active at initialization, use that phase's onset. Otherwise use the next onset. Differences use absolute elapsed seconds, including timezone offset changes. For a 13:20 start inside the dark phase beginning at 12:00, the actual start stays 13:20 and analysis origin is 12:00 with 4,800 missing seconds. No raw data is discarded.

New recording cycles use one fixed anchor and compute phase boundaries in elapsed seconds. Previously saved configs without a fixed anchor retain `phase_schedule.mode: "legacy_local"` on import. Starting a new RFID run or applying Arduino lighting establishes a fixed schedule; importing an old experiment alone does not reinterpret its history. See the current [firmware inventory](../FIRMWARE.md). This historical control protocol was superseded by passive acquisition.

## Passive lickometer recording

`acquisition.lickometer.control_mode` is `"passive_usb"`. The GUI saves the COM port, enabled flag and fixed channel mapping, and records unsolicited observations at 115200 baud. Config version remains 3; `acquisition._provenance.software_patch` is 4.

Old `device_uid`, `automatic_recalibration` and `connection_monitoring` fields are accepted on import as historical metadata. They are never applied to the Arduino and are cleared to `null` when saving settings from the current lickometer tab. The autonomous defaults are defined in firmware v7.0: 24 hours of powered operation between routine maintenance requests, five quiet seconds before routine calibration, and forced sensor reinitialization after 90 seconds of uninterrupted activation. There is no PC clock or phase-schedule dependency for the lickometer. See the current [firmware inventory](../FIRMWARE.md).

## Events

Event types are unique by name with one description. Bouts refer to an existing type and have `start`, `end`, and `end_user_specified`. A type may have no bouts yet. Multiple bouts may share a type; overlapping bouts are allowed. Explicit end times cannot precede their starts.

An unspecified end is `null` during a running recording. On the last RFID stop it resolves to the formal end, retaining `end_user_specified: false`. `finalized_run_id` identifies the run used for that resolution; reopening RFID later does not extend an already finalized historical bout. If a scheduled bout starts after recording has ended, it keeps a null end and is flagged `recording_ended_before_event: true` instead of inventing a negative-duration interval. Event finalization runs independently of the visible tab. A late event save after recording ends also resolves omitted ends to that run's end.

Notes entered in Events are stored at `recording.notes`.

## Layout

Cage records contain `name`, `cell_id`, `cage_type` and directly incident `antennas`. Tunnels contain `name`, `tunnel_no`, `start_cell_id`, `end_cell_id`, `dead_end`, `antennas` and a derived `antenna_count`. Source labels concatenate physical channel and COM port. Arrays follow tunnel direction from start to end; endpoint order changes the directional mapping.

A branch attachment is `{ "id":"junction_1", "parent_tunnel":"tunnel_1", "position":0.5 }`. Its new tunnel starts at `junction_1`, has `end_cell_id: null`, `dead_end: true` and one antenna by default. Cage positions, dead-end tip positions, independently movable labels and the next tunnel number live under `layout.editor`. Parent IDs and surviving tunnel numbers remain stable after deletion.

`antenna_combinations` and `tunnels_map` contain direct physical relations only. Both former interpolation aliases are removed. A standard four-cage/eight-antenna ring has 24 direct combinations. Special interpretation of one-antenna dead ends is deferred to later analysis work; no inferred graph path is substituted.

## Migration and preservation

Analysis-style v2, older recognized recording formats and the supplied recorder-style v4 can be imported. Unversioned known structures are accepted. The `acqusition` spelling is corrected. Conflicting old/new sections or conflicting phase schedules produce an error rather than silently choosing one. Unknown metadata is retained where it does not replace canonical content explicitly edited by the user.

Bare legacy antenna labels are assigned a COM suffix only when board identity is unambiguous. Otherwise they remain visible for manual assignment, and preflight explains that ports are missing. The example supplied with the task has no usable DAQ COM mapping; it must be bound to real ports on the acquisition computer.

Loading is read-only with respect to the source file. Saving writes v3 to the chosen Data directory and retains a backup of any previous canonical file. Historical timestamps in imported templates are preserved until a new RFID initialization establishes a new run. Configuration snapshots/journals keep the earlier run's evidence.
