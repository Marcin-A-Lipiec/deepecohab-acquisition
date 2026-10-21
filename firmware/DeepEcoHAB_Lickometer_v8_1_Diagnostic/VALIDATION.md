# Diagnostic build validation

The production v8.1 calibration, range search and detection branches remain
unchanged. Added fields observe calibration results; sensor status is copied
from reads the driver already performs. No extra I2C transactions are made for
the diagnostic reports.

Changes are limited to passive observations, a number-letter matrix display and
optional Serial Monitor output. No Serial input/command handler exists. A 4096-byte
fixed queue discards reports under backpressure. Each USB write is limited to
the space reported available and at most 64 bytes. There is no USB connection
wait. Tests cover disconnected USB, zero available space, seven-byte writes and
reconnection while detection continues.

The UNO R4 core's first connected-Serial check can introduce a 10 ms delay;
this diagnostic build is therefore for bench investigation, not certified
acquisition timing. Opening/connecting USB can also change physical conditions,
which is why instructions request DC-only matrix observations first.

Native C++17 compilation uses `-Wall -Wextra -Werror`. **24 scenarios passed**:
the 21 v8.1 firmware checks, two reproductions of the reported failure patterns,
and a USB backpressure/reconnection check. Display assertions were adapted for
the diagnostic codes; detector expectations were retained. The reproductions
deliberately confirm that WAIT_RELEASE/NOISE remain visible, not that they are
fixed. Results are in `validation_logs/test_results.txt`.

To rerun with Python 3 and a C++17 g++ compiler:

```bash
python tests/run_tests.py
```

The sketch was also compiled for `arduino:renesas_uno:unor4wifi` with Arduino
UNO R4 Boards 1.6.0. Compiler output is in `validation_logs/firmware_build.log`.
The bundled WDT library's renesas/renesas_uno architecture metadata warning is
present; see the complete build result in that log.

No physical sensor, bottle, mouse, power supply or BNC hardware was available.
This is a measurement aid and does not establish that the lickometer is fixed.
