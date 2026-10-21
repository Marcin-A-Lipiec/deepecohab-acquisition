# Config version 8 and preprocessing schema 5

Load `<experiment>.config.json` as before. It links to `<experiment>.acquisition_gui_state.json` and the diagnostic. Config versions 5, 6 and 7 remain readable, as do the supported older monolithic formats. Loading does not rewrite source files; saving or preprocessing writes version 8.

## Devices and events

Animal rows may be saved as drafts with blank required fields. The version 8
schema checks their field names and value types; **Check configuration** and the
preprocessing review check completeness, unique valid tags, sex and birth dates.
Saving a draft does not imply that its scientific metadata is complete.

`recording.devices` replaces `recording.device_antennas` and follows `recording.events` immediately. `recording.notes` remains the last field in the file. Example:

```json
{
  "events": [
    {
      "name": "Lickometer_added",
      "description": "Lickometer branch installed.",
      "devices": ["lickometer-tunnel_5"],
      "bouts": [
        {
          "start": "2026-09-14T12:35:30+02:00",
          "end": "2026-09-18T23:53:46.821452+02:00",
          "position": ["tunnel_4"]
        }
      ]
    }
  ],
  "devices": [
    {
      "name": "lickometer-tunnel_5",
      "description": null,
      "device_type": "lickometer",
      "antenna": "3-1",
      "position": "tunnel_5",
      "TTL_port": null
    }
  ]
}
```

This is an excerpt from recording, not an entire config. A device's own position is still a single string; an event bout's position is a nonempty list of selected compartments or `null` when no position is specified. Patch 20 writes `null` for the Whole habitat selection; empty lists from Patch 19 remain loadable and are normalized to null on the next save. An event with no selected devices has `devices: null`; a missing optional key is also accepted on load and normalized to null on save. Device selections belong to the event type and apply to all its bouts. Event device names must identify configured devices.

The layout dialog accepts Device type, an optional Description and Position, and shows a generated, read-only name. In Patch 20 the main device record includes description immediately after name; blank descriptions are null. Description is independent of the generated name. Names combine trimmed type and the exact stored position with a hyphen. Case and underscores are preserved; `Lickometer` and `tunnel_5` produce `Lickometer-tunnel_5`. This patch supports the existing antenna and optional linked TTL combination only.

Legacy device names become initial types. Patch 20 restores old free-text descriptions from the private GUI metadata retained by Patch 19. Explicitly clearing the description saves null. Old Patch 19 records without the description key remain loadable. Known prior names allow event references to follow type or location edits. Users can still group or separate any supported inputs in Manage RFID and TTL output grouping. Defaults group equal generated names, using the existing case-insensitive matching, and route associated TTLs with their device. Different positions generate different names and default groups. Explicit per-input overrides remain authoritative.

## Movement tunnels and physical editor state

Main `recording.layout.tunnels` lists only tunnels with an assigned main antenna. Tunnels that contain devices only, empty terminal spurs and unassigned tunnels do not appear in that list. Existing cage, junction, direct antenna-combination and tunnel-map structures are retained.

Unlisted tunnel records, their ordering and drawing data are stored in the linked GUI state. Loading the pair reconstructs the full physical graph before layout validation and preprocessing. This preserves through connections, nested branch attachments and editing. A main-file tunnel with the same name takes precedence over a stored unlisted record. Removing a tunnel from the main list does not remove it from the physical habitat. Devices remain excluded from movement interpolation. Invalid or incomplete main assignments still fail preprocessing validation.

Keep the config pair together. Without GUI state, hidden physical tunnels cannot be reconstructed from device names alone. The program does not guess their connections.

## Boolean inserted column

The last column of every generated file is `inserted`:

| Value | Meaning |
| --- | --- |
| `True` | This preprocessing run inserted the row by interpolation. |
| `False` | The row derives from input records, including stitched RFID observations, observations given a minimum duration, or TTL rising-edge events. |

Parquet schema 5 has these columns in order: `datetime`, `antenna`, `time_under`, `animal_id`, `internal_board_timestamp`, `inserted`. Existing types are unchanged; inserted is a non-null Arrow Boolean / Polars Boolean. The antenna remains Categorical and animal_id remains Enum. Timezone-aware datetimes and microsecond durations are unchanged.

CSV retains its original column order and units, with inserted appended: `antenna,animal_id,datetime,time_spent,internal_board_timestamp,inserted`. CSV has no intrinsic column types; Boolean values are encoded as `True` and `False`. The raw acquisition file format is unchanged.

Detailed interpolation row references and methods remain in diagnostic version 12. Its format description and the generated DATATYPE READ ME explain inserted. Output names, merge/split controls and part counts remain unchanged. GUI-state envelope stays version 1.
