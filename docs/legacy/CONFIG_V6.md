> Historical contract. For current behavior see [compatibility](../COMPATIBILITY.md) and [config v8](../CONFIG_V8.md).

# Canonical experiment configuration v6

The main file is `<experiment>.config.json`. It has exactly `version`, `recording` and `acquisition` at the root. Load this file; its relative acquisition_gui_state_path and diagnostic_log_path links resolve the other files. Loading old supported configs leaves source files unchanged. Save writes v6 after required animal values are completed. Main fields win over corresponding GUI state.

The machine-readable contract is config_v6.schema.json. GUI state retains envelope version 1. Scientific completeness also requires cohort, source-identity and physical-topology validation; unfinished antenna assignments do not satisfy the canonical source pattern.

Changes from v5:

| Field | Contract |
| --- | --- |
| `recording.timeline.end_with` | Required `dark_phase` or `light_phase`; metadata only. Missing historical value defaults to dark_phase. |
| Animal `subject_name`, `treatment` | Required keys, string or null. Whitespace-only input becomes null. |
| Animal `notes` | Optional editor input; saved string, blank as empty string. |
| Animal `tag`, `mouse_line`, `genotype`, `genetic_background`, `date_of_birth`, `sex` | Required nonempty values; tag unique hexadecimal, DOB valid ISO date, sex Male/Female. |
| Tunnel `length` | Positive finite centimetres between centres of its two main antennas, or null. |
| `recording.device_antennas` | Required array, empty when none. Each row: name, description, antenna, position, TTL_port. |
| `acquisition.active_inputs.device_rfid` | Minimal list of device/separately routed RFID inputs; replaces auxiliary_rfid. |

A device name and an existing cage/tunnel position are required. Description may be empty; TTL_port is null or a board-qualified channel such as TTL1-2. Device antennas do not appear in movement tunnel/cage antenna lists or combinations. GUI marker positions, group-number registry and explicit routing overrides are secondary state. Physical roles do not change when a custom output destination changes.

A legacy one-element antenna_distances_cm list migrates directly to length for a two-antenna tunnel. If a tunnel has a different number of main antennas, length is null. Historical multiple distances remain in secondary state for review, never silently summed. The GUI creates at most two main positions per tunnel while preserving larger historical layouts.

All existing scientific fields remain nested as before: name, project_name, recording_location, timeline, cohort, events, layout, notes; layout retains cages, tunnels, junctions, antenna_combinations and tunnels_map. Only direct physical antenna combinations are written. End preference does not create effective analysis bounds. The fixed elapsed-time phase schedule, selected datetime provenance and automatic/manual times retain their existing meaning. HH:MM:00 phase strings normalize to HH:MM; nonzero seconds are not silently truncated.

The editable background presets are C57BL/6J, C57BL/6N, BALB/cJ, 129S1/SvImJ, DBA/2J, C3H/HeJ, FVB/NJ, CBA/J, NOD/ShiLtJ and Crl:CD1(ICR). These are practical choices, including the outbred CD-1 stock, not a frequency ranking. Custom and mixed backgrounds remain accepted. Genotype presets are KO, WT, HET and MUT.
