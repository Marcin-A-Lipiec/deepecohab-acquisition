# Historical native harnesses

These files are retained for reference and are not active validation:

- `test_trigger_firmware.cpp` requires
  `firmware/Acquisition_GUI_v2_Trigger/Acquisition_GUI_v2_Trigger.ino`.
- `test_lickometer_v6_2.cpp` requires
  `firmware/Capacitive_Lickometer_UNO_R4_v6_2/Capacitive_Lickometer_UNO_R4_v6_2.ino`.

Neither sketch is present in this supplied acquisition copy. They must be
recovered from their authoritative release before these harnesses can certify
anything. Moving them here does not restore their relative include paths.

The current GUI supports trigger protocols, including STOP when a compatible
board advertises it. It does not ship or flash the missing trigger sketch.
Standalone lickometers send BNC TTL edges to the RFID DAQ. Active native tests
cover the included v8.0 sketch and the included v8.1 diagnostic sketch; a C++17
compiler is required. See `docs/FIRMWARE.md` for the supported inventory.
