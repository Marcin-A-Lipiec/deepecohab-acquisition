> Historical contract. For current behavior see [compatibility](../COMPATIBILITY.md) and [config v8](../CONFIG_V8.md).

> Historical hardware/firmware notes retained from v0.1.4-alpha. For current GUI behavior, output filenames and reset handling, see README.md and TESTING.md. The working-base Arduino sketch is unchanged.

# Lickometer working-base rebuild

This version intentionally starts from the last code confirmed to operate the actual MPR121, LED matrix and four BNC outputs correctly.

## What was not changed

- MPR121 address `0x5A`.
- Electrode mapping E1/E3/E5/E7.
- Output pins D8/D9/D10/D11.
- Touch/release thresholds 12/6.
- `setAutoconfig(true)`.
- 5 ms polling.
- Two matching samples before accepting a transition.
- Direct use of `mpr121.touched()`.
- Matrix rows assigned in two-row blocks per channel.

## What was added

After `digitalWrite()` updates the BNC output, the Arduino prints:

```text
EVENT,wrap_count,micros32,channel,state
```

The GUI converts this into:

```text
channel_L1_COMxx;0000000001_or_0;arduino_time;pc_datetime;1
```

The firmware accepts only:

```text
RECALIBRATE
```

No response is required by the GUI.

## Automatic recalibration

Recalibration is requested conservatively. It normally waits for five quiet seconds before resetting the MPR121. During calibration all outputs are LOW and all debounce states are cleared.

Fault-triggered requests can force recovery after two minutes if the device remains permanently active. This is intended for a false stuck-high state, not ordinary licking.

## First hardware test

First confirm that the v6 firmware behaves exactly like the known-good base without any PC connection. Only then open the COM port from the GUI and verify USB event recording.
