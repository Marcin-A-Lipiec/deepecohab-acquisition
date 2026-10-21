> Historical contract. For current behavior see [compatibility](../COMPATIBILITY.md) and [config v8](../CONFIG_V8.md).

# Canonical experiment configuration v7

The main file remains `<experiment>.config.json`. Load this file to restore its linked acquisition GUI state. Version 5 and 6 pairs remain readable; saving writes version 7. Loading alone does not rewrite older files. See `config_v7.schema.json` for the current machine-readable contract and `CONFIG_V6.md` for the retained scientific fields.

Changes from version 6:

| Field | Contract |
| --- | --- |
| `acquisition.datetime_provenance` | Required object containing `start_source` and `end_source`. Describes the selected experiment bounds. Moved from `recording.timeline.datetime_provenance`. |
| `recording.notes` | Same string field and logical location. The writer emits root sections in the order `version`, `acquisition`, `recording`, and writes notes last within recording. Notes is therefore the final field in the whole JSON document. |

Automatic timing evidence and recovery flags remain in the linked GUI state. Manual bounds export `manual_override` as their source; switching back to automatic preserves the underlying automatic evidence. Main scientific fields take precedence over stale GUI state. With a missing state file, selected bounds and their public sources still load.

Device antenna records remain under `recording.device_antennas`; they do not appear in movement antenna lists or supply interpolation or transit-median evidence. Physical device branches remain in `layout.tunnels` and `layout.junctions` with `antennas: []`, `antenna_count: 0` and `length: null` when no main antennas remain. Empty dead-end spurs are valid even when their device position names a cage. An empty through tunnel requires a device positioned on that tunnel. Its connections remain part of the graph to avoid inventing or removing movement routes. Missing or unassigned antennas on main through tunnels, invalid endpoints and invalid junction attachments still produce validation errors.

GUI-state envelope version 1 and all five Parquet columns and their types are unchanged. Diagnostic format is independently versioned; see ../DIAGNOSTIC_V12.md for the current report and linked preprocessing settings. No data converter is required. Downstream validators must use the v7 provenance location.
