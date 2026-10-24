# System and data checks

## Before recording

Open **Other options > System check** to check core packages, available video
tools and optional camera support. On v1.0.0 the menu is called Experiment.
For terminal diagnostics:

```powershell
deepecohab-acquisition-cli doctor
```

Check the selected COM ports, board hardware versions, antenna assignments and
animal tags. Record a short sample with the devices used in the experiment.
Verify RFID/TTL detections, camera frames, expected filenames and a clean stop.
Camera SDK, vendor-wheel and encoder requirements are in the
[camera setup guide](docs/CAMERA_SETUP.md).

## Check preprocessing

Use a copy of an experiment when first checking a new installation. Review its
metadata, input-board assignments, timezone and processing options. Inspect the
diagnostic for interruptions, rejected reads and interpolation results, then
open representative outputs in the downstream analysis software.

Canonical config, diagnostic and data contracts are documented in
[CONFIG_V8.md](docs/CONFIG_V8.md), [DIAGNOSTIC_V12.md](docs/DIAGNOSTIC_V12.md)
and the [user guide](docs/USER_GUIDE.md). An automated software check does not
certify a particular camera, electrical connection or acquisition PC.

## Automated tests

Contributors can run the regression suite from a source checkout:

```powershell
uv sync --locked --group dev --python 3.12
uv run --locked python tools/run_validation.py
```

The suite covers acquisition ownership, stop/drain behavior, config round trips,
supported historical inputs, scientific output contracts and GUI interactions.
GitHub validation runs on supported Python versions. Native firmware tests need
a C++17 compiler and report a skip when that compiler is unavailable. Tests use
fixtures and simulated devices; hardware checks remain necessary on the target PC.
