# DeepEcoHAB Acquisition v1.0.1

- Fixed false missing-board warnings when preprocessing archived experiments.
- Improved responsiveness when loading large recordings through cached background file discovery.
- Fixed Rescan experiment so added, changed and removed raw files are recognised without restarting.
- Fixed experiment switching to clear previous experiment state and prevent preprocessing data carryover.
- Simplified antenna assignment: editable channel and board number, direct tag matching and a compact dialog.
- Added support for up to four RFID DAQ boards; new experiments display one panel by default.
- Added Total TTL detections beside Total tag detections; tags absent from Animal details appear muted.
- Added a Match tag button for each mouse and column-aligned sharing checkboxes that follow the first mouse's values.
- Improved panel scaling, green/red RFID DAQ controls and the renamed Other options menu.
- Preserved canonical config, diagnostic and data formats. GUI settings use the existing GUI-state file; no additional experiment files are created.
