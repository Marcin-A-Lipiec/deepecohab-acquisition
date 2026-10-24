# Hardware and firmware inventory

The application does not change or flash firmware. Software tests simulate USB
transport; electrical timing, camera SDK operation and actual boards require
bench acceptance on the acquisition PC.

| Device/firmware reference | Location and role |
|---|---|
| UNO R4 standalone lickometer v8.0 | `firmware/Capacitive_Lickometer_UNO_R4_v8_0_Standalone` |
| UNO R4 standalone lickometer v8.1 | `firmware/Capacitive_Lickometer_UNO_R4_v8_1_Standalone`; later range-search code |
| UNO R4 v8.1-DIAG1 | `firmware/DeepEcoHAB_Lickometer_v8_1_Diagnostic`; temporary bench diagnostic build, with its own instructions and logs |
| GIGA lighting v5 | `firmware/DeepEcoHAB_GIGA_Lighting_v5` |
| RFID DAQ | Hardware profiles/protocols are supported by the application; no DAQ flashing sketch is included |
| Hardware trigger | Legacy and STOP-capable protocols are supported; no trigger flashing sketch is included |

The v8.1 diagnostic build's `READ_ME_FIRST.md` explicitly reports an unresolved
physical fault. Its existing native-test logs establish simulation results,
not a fix or certification for experimental acquisition. Preserve the known
working firmware on real boards until the corresponding hardware investigation
is complete. Installing the application does not require flashing these examples.

Standalone lickometers sense autonomously and send BNC rising edges to DAQ TTL
inputs. The production GUI has no USB lickometer-control tab. Retained passive
USB recorder classes are covered for historical compatibility; old configuration,
ARM and acknowledgement assumptions have been replaced with receive-only tests.
Stopping a GUI recording does not necessarily stop a separately powered output
or lighting schedule. The trigger UI reports the capabilities of the connected
firmware, including whether an actual STOP command is available.

For Arduino programming, use the board/core and libraries specified by the
sketch's own instructions. The diagnostic package requires **Arduino UNO R4 WiFi**
and the Arduino UNO R4 Boards package, which provides its libraries. Keep all
headers beside the `.ino`. Lighting uses the GIGA board's corresponding core.
Verify the selected board, port, installed firmware and wiring before acquisition.
On Windows, install the manufacturer's USB driver if the device does not appear
in Device Manager/COM ports. The GUI's Python serial dependency cannot provide
operating-system USB drivers.

Two historical C++ harnesses previously referenced absent sketches:
`Acquisition_GUI_v2_Trigger` and lickometer `v6_2`. Their files and stubs are retained
under `tests/legacy`, with an explicit explanation. They are no longer presented
as runnable validation. Restore sketches only from their authoritative release.
Current native tests compile the included v8.0 sketch and the included v8.1
diagnostic sketch when a C++17 `g++` compiler is available. See [../TESTING.md](../TESTING.md).

Historical working-base sensor/wiring notes are retained in
[legacy/LICKOMETER_WORKING_BASE.md](legacy/LICKOMETER_WORKING_BASE.md).

The supplied diagnostic ZIP is also retained: its `BenchDiagnostics.h` differs
from the unpacked copy. It is an original variant, not a disposable duplicate.
Both are included in the source archive; the installed Python tool does not
flash either build.
