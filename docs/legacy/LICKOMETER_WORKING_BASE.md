# Historical lickometer hardware reference

This reference describes older MPR121 hardware and its USB event format.
It does not describe the current acquisition interface. Current standalone
lickometer signals are recorded through RFID DAQ TTL inputs. See the
[firmware inventory](../FIRMWARE.md) and [compatibility guide](../COMPATIBILITY.md).

## Recorded hardware settings

- MPR121 address: `0x5A`.
- Electrode mapping: E1/E3/E5/E7.
- BNC output pins: D8/D9/D10/D11.
- Touch/release thresholds: 12/6.
- Autoconfiguration enabled; polling interval 5 ms.
- Two matching samples before accepting a transition.

## Historical USB records

The older firmware emitted:

```text
EVENT,wrap_count,micros32,channel,state
```

Historical GUI records used:

```text
channel_L1_COMxx;0000000001_or_0;arduino_time;pc_datetime;1
```

The historical control command was `RECALIBRATE`. These details support reading
archived recordings and identifying older hardware; they are not instructions
to substitute unverified firmware for a current acquisition device.
