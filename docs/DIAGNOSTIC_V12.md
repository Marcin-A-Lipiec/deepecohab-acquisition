# Preprocessing diagnostic v12

The main config is version 8; see CONFIG_V8.md. The diagnostic is `<experiment>.diagnostic.json`, beside `<experiment>.config.json`. It begins with definitions and contains measured preprocessing results. The partial machine-readable report contract is `diagnostic_v12.schema.json`; scientific arrays retain the v11 record structure except for the redundant tunnel label described below.

## Metadata ownership

An explicitly selected fixed recording-PC offset is recorded conservatively in
the existing `issues.raw_pc_clock_override` category; executed private GUI input
settings retain `naive_utc_offset`. No recording-summary fields are added.
Experiment timezone and recorded explicit offsets remain
authoritative. `issues.incomplete_metadata_accepted` records accepted missing
metadata and any disabled tag filtering or interpolation. These optional
observations use the existing diagnostic version 12 envelope.

| Information | Location |
| --- | --- |
| Experiment name, timezone, selected start/end, datetime provenance, animals, events, physical topology, device definitions | Main config; diagnostic `config_file` names it. |
| Executed options, aliases/routing, effective stitching windows, excluded movement inputs | `processing.settings_source` names the GUI state file and its `state.acquisition.preprocessing.completed_run` section. This snapshot survives later edits to GUI defaults. |
| Acquisition starts/stops, errors/counters and camera/other device history | GUI state recording_history and device_history. |
| Measured clock alignment, raw RFID bounds, gaps, uncertain stop events, counts, full medians, failed insertion counts, all inserted-row references, raw read quality and duration statistics | Diagnostic. |
| Published file identity, input membership, rows and generated parts | Diagnostic outputs. Device names, antenna IDs and animal IDs remain as necessary identifiers. |

The log no longer copies experiment name/timezone, selected/manual/automatic bounds, datetime provenance, GUI options, full acquisition/device history, duplicate paths or config backup fields. Transit median rows retain animal, from_antenna, to_antenna, median_ms, samples, eligible and optional fallback/exclusion fields. The tunnel label is obtained from the main layout instead of repeated in every row. Every mouse and both directions remain present, including missing medians and zero-count rows.

`experiment_timing` reports first/last valid raw RFID PC datetimes before tag filtering (TTLs excluded), recovery status, and any partial-selection warning/count. Raw observed limits can differ from selected experiment limits; those are distinct evidence. The main config owns the selected limits. Confirmed interruptions and clock resets remain in the diagnostic. Folder inputs remain one logical recording, without routine per-file boundaries.

For linked acquisition history, acquisition_evidence identifies uncertain stop events by run, board, device and epoch, optionally linked to a recording ID. Epoch is the zero-based index of previous_epochs followed by the current device epoch. It does not copy every stored event, counter or error. Read the referenced run's epochs in GUI recording_history for the reported start/stop/error. A last raw detection never proves the actual failure time. The embedded definitions explain these distinctions.

Without a main config, there is no metadata duplication to remove: standalone reports retain their experiment name, timezone and executed settings directly.

## Output parts

```json
{
  "file": "Example.data.parquet",
  "group": "rfid",
  "stream": "rfid:1",
  "sources": ["1-1", "2-1"],
  "part": {"number": 1, "total": 1},
  "rows": 456
}
```

Part numbering is chronological within each generated group. The total counts distinct generated parts, not CSV and Parquet copies. A group absent from some recording parts produces no empty files. For example, if only recording parts 1 and 3 contain movement data, their streams remain rfid:1 and rfid:3, and their part objects are 1/2 and 2/2. A device present only in recording part 3 reports 1/1. Existing filename part suffixes and stream identifiers are unchanged so interpolation row references retain their meaning. With merged output, every generated group reports 1/1.

## Internal integrity

No checksums, SHA fields, content fingerprints or similar internal fields are written to the diagnostic or worker result. Folder fingerprints and report-only config hashes are no longer calculated. Raw content checks remain in memory for duplicate-input detection. Output digests and config concurrency checks remain internal so edited files are not silently replaced.

Normal experiments keep output ownership under `state.acquisition.preprocessing._output_integrity` in the existing GUI state. No extra file/folder is created for this. Config-free preprocessing uses one hidden `.<experiment>.preprocessing_state` application file because no GUI-state file exists. Its replacement is part of the same recoverable publication transaction.

Completed older logs with output hashes still authorize a safe first reprocessing run. The next report uses v12 and stores integrity internally. Missing or mismatched internal ownership evidence blocks replacement of existing data; choosing a new output folder remains possible. File lists alone never authorize overwriting edited data.

Data schema 5 appends the Boolean inserted column while preserving the original five columns, their values and types. No converter is required. Downstream diagnostic consumers should accept version 12, read part.number and part.total, and follow settings_source when executed options are needed. Keeping the config pair and diagnostic together preserves that context.
