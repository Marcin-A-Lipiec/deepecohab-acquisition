# DeepEcoHAB lickometer v8.1-DIAG1

**Temporary diagnostic firmware. The reported fault is not yet fixed.**

The v8.1 range hotfix did not resolve the real device's problem. Two blinking
columns do not uniquely identify a range fault: the old display uses two columns
for every fault, changing only their height. In particular, a noise fault has
three blinking rows and no steady marks below it.

This build exposes the actual state and measurements. Its calibration and
detection decisions are intentionally identical to v8.1. It adds no GUI tab,
PC commands, settings handshake or USB experiment recording. BNC outputs follow
the existing detector, including remaining LOW on a disabled channel.

## One short test

Use a bench/testing interval, not experimental lick data that must be trusted.
Leave the same bottle on **port 1** for this test; further port swapping is not
needed yet.

1. Extract the ZIP. Open the `.ino` inside
   `firmware/Capacitive_Lickometer_UNO_R4_v8_1_Diagnostic` in Arduino IDE.
   Keep all five `.h` files beside it. Select **Arduino UNO R4 WiFi** and the
   board's COM port, then upload. The Arduino UNO R4 Boards package provides
   the required libraries; no extra library installation is needed.
2. Disconnect USB and power, arrange the bottle, spout lead and BNC connections
   as in the failing setup, then power normally. After **30 seconds untouched**,
   note the four matrix codes. The display now cycles through channels 1–4,
   showing each channel number and a letter for two seconds. For example,
   `1N`, `2O`, `3W`, `4O`. This also verifies the diagnostic sketch is running.
3. Attach USB and open **Tools → Serial Monitor**, **115200 baud**, in Arduino
   IDE. Do not send commands or deliberately reset the Arduino. Close any other
   program holding this Arduino's port. Text should appear automatically,
   beginning with `DEEPECOHAB V8.1-DIAG1` and repeating `V8.1-DIAG1` in reports.
4. Capture **10 seconds untouched**, then **three brief finger touches** to the
   spout, roughly one second apart, followed by **five seconds untouched**.
   These touches only test the sensor's response; they do not validate mouse
   sensitivity. Readings are reported even if the detector refuses to arm.
5. Send the Serial Monitor output, either pasted or attached as a `.txt` file,
   plus the four DC-only matrix codes. Say whether the codes or behavior changed
   when USB was connected. If no text appears, send the matrix codes and report
   that Serial Monitor was blank; those codes still provide useful information.

USB can affect the electrical conditions, so the DC-only observations are
important. The diagnostic program never waits for USB to be present. There is
no requirement to reopen the Acquisition GUI or reinstall any Python files.

If the matrix still shows the old four groups of blinking blocks instead of
cycling number-letter pairs, the diagnostic sketch is not the one executing.
Select/open the diagnostic sketch and verify that Arduino IDE reports a
successful upload to the intended board before collecting the readings.

## Matrix codes

| Letter after channel number | State |
| --- | --- |
| O | Detector ready; no contact currently detected |
| A | Detector reports an active contact |
| C | Startup settling or baseline calibration |
| T | Startup charge-range search |
| W | Waiting for release qualification |
| N | Noise protection rejected calibration, or a noise fault occurred |
| L | Low reading/baseline, or startup range search could not find a valid higher value |
| H | High reading/baseline, or startup range search could not find a valid lower value |
| R | Range fault; the report identifies the hardware flag and search result |
| S | Stuck-contact protection, waiting for release |
| I | Sensor communication/configuration recovery |

`O` is the letter O. A small top-left heartbeat and a steady top-right diagnostic
marker accompany the codes. The codes are visible with DC power alone.

## What the report means

One report per second contains a board summary and four channel lines. There
is no need to interpret the numbers before sending them.

- `adc`: most recent filtered electrode reading, **not capacitance in pF**.
- `lo`, `hi`, `n`: minimum, maximum and number of readings during the last
  reporting interval. The interval collects every successful sensing snapshot,
  rather than sampling the signal only once per second.
- `age_ms`: age of the last successful reading. If `n=0`, the last value is stale;
  `lo=0, hi=0` are empty-window placeholders, not measured zeros.
- `cal_ok`: whether any calibration has succeeded; this does not by itself
  mean the channel is READY or that mouse licks are detectable.
- `cal_attempt`, `cal_min`, `cal_max`, `cal_median_x10`, `cal_spread_x10`,
  `proposed_on_x10`: last completed calibration attempt, including rejected
  attempts. If `cal_attempt=0`, these fields have not been measured yet.
- `baseline_x10`, `on_x10`, `off_x10`: active software reference and thresholds.
  Every `_x10` field is in ADC counts multiplied by ten: `60` means 6.0 counts.
  Active/default thresholds can differ from the proposed threshold if the
  last calibration was rejected.
- `cdc`, `cdt_code`: selected charge current in microamps and encoded charge
  time. Time codes 1–7 mean 0.5, 1, 2, 4, 8, 16, 32 microseconds respectively.
- `ECR`, `OOR`, `touch_status`: observed MPR121 state registers. Only E1, E3,
  E5 and E7 drive lickometer channels 1–4; flags on unused electrodes need
  separate interpretation.
- `config_attempts`, `snapshots`, `trials`, `failed_mask`, `error`, `dropped`:
  sensor initialization, successful reads, range-search progress, failed logical
  channels, driver error and discarded diagnostic messages.
- `uptime_ms` is elapsed device time, **not a wall-clock datetime**. This bench
  report introduces no naive datetimes into experiment data.

The meanings of filtered data and charge parameters follow the
[MPR121 datasheet](https://cdn-shop.adafruit.com/datasheets/MPR121.pdf).
Every decision state and calibration statistic above comes from our firmware.

## Why measurements are necessary

Two behaviors have now been reproduced in the actual detector code with
synthetic inputs around a 720-count baseline:

| Synthetic input | Result after calibration |
| --- | --- |
| Constant 720 | READY |
| 50 Hz fluctuation of approximately ±4 counts | Learns touch threshold 6 and release threshold 3, then remains in WAIT_RELEASE for the full 60-second test |
| 50 Hz fluctuation of approximately ±8 counts | Proposes threshold 10, exceeds the configured maximum 8, reports NOISE |

The first case is a software logic defect: the release qualification can demand
an uninterrupted 100 ms quieter than the noise already accepted during
calibration. The second shows how the current noise limit disables a channel;
it does not establish whether that limit is appropriate for the real electrode.

These are demonstrations, not measurements of your hardware. Raising thresholds
without observing noise and contact amplitude could make the display look ready
while missing mouse licks. The diagnostic trace will distinguish these cases
from a genuine range or communication failure before the next production fix.

See VALIDATION.md for compilation and software checks. Retain the previous
firmware folders; flashing this sketch changes only the Arduino program.
