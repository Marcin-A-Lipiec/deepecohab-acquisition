> Historical contract. For current behavior see [compatibility](../COMPATIBILITY.md) and [config v8](../CONFIG_V8.md).

# Canonical experiment config v5

The user opens `<Experiment_name>.config.json`. The main file is the authoritative scientific record. `<Experiment_name>.acquisition_gui_state.json` preserves the acquisition application's remaining state. Relative paths resolve against the main file's directory, independent of the process working directory. Use forward slashes in stored paths.

## Main contract

`config_v5.schema.json` is the machine-readable scientific contract. Top-level fields are exactly `version: 5`, `recording` and `acquisition`. Unknown application fields belong in secondary state. The application permits saving incomplete drafts; passing the JSON schema does not replace topology/cohort checks or establish that an experiment is complete. In particular, unresolved draft antenna labels fail the canonical antenna pattern, while null start/end values are allowed before recording.

| Path | Contents |
|---|---|
| `recording.name`, `project_name`, `recording_location`, `notes` | Experiment-level descriptive strings |
| `recording.timeline` | Exactly `start_datetime`, `end_datetime`, `recording_timezone`, `start_from`, `phases`, `datetime_provenance` |
| `recording.timeline.datetime_provenance` | `start_source` and `end_source`; values describe selected bounds, such as manual override, imported config, acquisition event or raw detection evidence |
| `recording.cohort` | `n_mice` and `animals`; established animal fields, including string tags and row order |
| `recording.events` | List of `{name, description, bouts}`; bouts retain `{start, end, position}` |
| `recording.layout.cages` | `name`, `cell_id`, `cage_type`, `antennas` |
| `recording.layout.tunnels` | `name`, `tunnel_no`, `start_cell_id`, `end_cell_id`, `dead_end`, `antenna_count`, `antennas`; optional `antenna_distances_cm` |
| `recording.layout.junctions` | `id`, `parent_tunnel`, `position`; branch tunnel endpoints reference these junction IDs |
| `recording.layout.antenna_combinations`, `tunnels_map` | Existing direct physical relations; no interpolated combination maps |
| `acquisition.active_inputs` | Non-empty `rfid_boards` (integers), `auxiliary_rfid` and `ttl` (source IDs), where supplied |
| `acquisition._provenance` | Exactly `acquisition_gui_state_path` and `diagnostic_log_path` |

Example links:

```json
{
  "acquisition_gui_state_path": "Example.acquisition_gui_state.json",
  "diagnostic_log_path": "Example.diagnostic.json"
}
```

The diagnostic path is null before successful publication, then points to `<Experiment_name>.diagnostic.json` in the main config folder. GUI state also lives there. Data defaults to that folder; a custom output folder is represented by relative data paths in the diagnostic manifest. Move any custom output folder together with the experiment.

Logical source IDs combine the physical channel and positive Board number: `1-1`, `8-2`, `A-1`, `TTL2-1`. Hardware COM connections are secondary metadata. Legacy COM suffixes are resolved only through an unambiguous mapping; COM5 does not imply Board 5. Bare channels spanning several candidate boards require explicit layout assignment.

## Antenna distances (optional, Patch 14)

Each tunnel may contain `antenna_distances_cm`, an array of measured distances **along the tunnel between successive antenna centres**. Its order follows `antennas` from `start_cell_id` toward `end_cell_id`. Item `i` measures `antennas[i]` to `antennas[i+1]`. Distances are the same in both directions; no reverse copy is stored.

For example, `"antennas": ["1-1", "2-1"]` with `"antenna_distances_cm": [15.5]` means a 15.5 cm distance. Three antennas with `[12.0, null]` have a known first segment and unknown second segment. A missing field is valid for older configs and means no distances have been supplied. Null entries explicitly mean unknown; zero, negative, non-finite, nonnumeric and wrong-length measurements are rejected. A single-antenna tunnel has no antenna pair and uses an empty array if edited.

In EcoHAB layout, double-click the tunnel, enter its distances, click OK, then save the layout. Changing antenna count adjusts the distance rows. Measurements do not move drawing nodes, alter interpolation timing, or infer additional topology. Main config owns this field; it is not duplicated in secondary state. `config_v5.schema.json` includes this optional extension; the application also checks its length against the antenna list.

## Time and phases

Datetimes are ISO 8601 strings with an explicit UTC offset, or null for an unset boundary. `recording_timezone` retains the display timezone. `start_from` is `light_phase` or `dark_phase`; `phases` contains their initial `HH:mm` hours.

Cycles always repeat in fixed 86,400-second elapsed periods. Interpret phase hours in the fixed UTC offset of the selected `start_datetime`, then add elapsed durations. Do not reconstruct each subsequent day's phases in the timezone's changing civil offset. Select the analysis phase using `start_datetime`, `start_from` and `phases`; no separate effective analysis start is exported.

The secondary controller anchor preserves the actual cycle across restarts and manual boundary edits. When a manual start crosses a DST offset change, projection adjusts the main phase hours to the selected start's fixed offset so that the same cycle remains reconstructible from the main file alone. This does not shift the hardware cycle. Loading an externally edited main phase definition establishes the revised fixed schedule instead of restoring stale secondary phases.

Sources of the selected start/end remain in the main file. Detailed automatic evidence, underlying automatic values while manual mode is selected, and UI controls stay in secondary state; completed reconstruction decisions and coverage evidence belong in the diagnostic log.

## Secondary state and precedence

`acquisition_gui_state_v1.schema.json` defines an envelope with `format: "acquisition_gui_state"`, `version: 1`, `main_config` (main filename) and `state` (application-owned extras). It contains device settings, visual layout positions, optional setup fields, preprocessing assignments/options, timing controls and the fixed controller anchor.

Main fields are removed from the saved state instead of being duplicated. Extras attached to list rows retain identity/signature metadata. Public main values take precedence when loading; row extras restore only to corresponding rows. Event completion flags are not reapplied to a changed bout. A secondary file naming a different main file is rejected. Rename through Setup so ownership stays consistent.

Main-only loading with a missing secondary file preserves scientific metadata, reports the missing state and disables automatic device activation. It cannot recover absent hardware/editor settings. Importing an older single-file config is non-mutating; the next save projects it into the pair. Legacy `acqusition` spelling is accepted on input and corrected on output.

## Saving, history and diagnostics

`config_bundle.py` owns projection, loading, pair digests and recoverable paired writes. A temporary journal stores the previous pair; recovery rolls back an incomplete save. GUI saves detect edits to either file. Routine metadata CSVs, session snapshots, history directories and raw serial debug logs are no longer written.

Recording epochs and bounded runtime evidence live in `state.acquisition.recording_history` and `device_history`. Board starts are checkpointed immediately, stops update their epoch, and an unmatched start remains explicit after an abrupt exit. Legacy `session_configs/*/run.json` files are still read and imported into state. The diagnostic includes these histories, coverage evidence and final counters; the main config remains minimal.

Preprocessing stages data, the config pair, diagnostic and folder README before publication. Publication replaces the generated set with a rollback journal. Cancelled/failed jobs preserve previous completed files. A concurrent config/state edit blocks publication. Superseded outputs are removed only when owned by the previous completed manifest and their hashes still match; manually edited or unrelated files are not overwritten. Metadata loading and discovery recover interrupted publication before reading a potentially incomplete pair. No persistent backup tree is generated.

Diagnostic version 10 contains completed preprocessing settings/provenance, selected input evidence and hashes, output manifest/hashes, observed RFID/TTL spans, acquisition journal epochs, gaps, rotations, overlaps and interpolation/clock decisions. Confirmed inactive intervals require clean acquisition stop/start evidence without overlapping coverage. Detection silence alone remains unconfirmed. Journal epochs without selected files and unknown/failed boundaries remain visible. These fields are not copied into the main config.

No stable experiment UUID, derived analysis start, scheduling-policy choice, detailed channel-role model, completed preprocessing provenance or dataset manifest is generated in the main file. Optional setup metadata stays in secondary state; interventions and cohort changes can be recorded in main events.

Patch 14 adds complete `transition_medians` and `recording_boundaries` arrays. Median rows cover all configured/accepted observed mice and both directions of each successive physical tunnel antenna pair, including null medians for missing observations and medians below the interpolation sample threshold. The complete table is generated even with interpolation off. Cohort fallback estimates, when enabled, are separate from each mouse's actual median. Incomplete/missing layout is noted in `transition_medians_policy`.

Positive observed inter-file gaps shorter than 180 seconds are omitted by default from `recording_boundaries`, `missed_data_periods`, and `recording_coverage[*].observed_record_gaps`. Advanced options can disable the threshold or change it. Exactly-threshold gaps are included. `recording_boundary_policy` records the applied threshold and suppressed count. Source intervals, declared file rotations, clock epochs, and confirmed acquisition-journal interruptions remain available. The gap threshold changes reporting only. Five-entry `examples` remain previews, not complete inventories.
