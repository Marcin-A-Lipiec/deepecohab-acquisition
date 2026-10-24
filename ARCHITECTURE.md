# Application structure

DeepEcoHAB Acquisition is a native Qt application for RFID/TTL acquisition,
camera recording, experiment metadata and preprocessing.

## Components

| Component | Responsibility |
| --- | --- |
| `tabs/`, `widgets/`, `ui/` | User interface, layout editing and live displays |
| `controllers/`, `protocols/` | Hardware communication, message parsing and recording |
| `services/` | Device lifecycle, COM-port ownership and experiment sessions |
| `config.py`, `config_bundle.py`, `schema.py` | Configuration loading, validation and saving |
| `preprocessing/` | Source discovery, clock alignment, stitching, interpolation and export |
| `assets/` | Bundled fonts, icons, themes and setup help |

Acquisition readers and writers run independently of GUI display refreshes.
Changing pages or themes does not restart a recording. A COM port can be owned
by one acquisition or matching operation at a time. Stop waits for accepted
records to drain before releasing device resources.

The first active RFID board starts an experiment run; the last RFID board to
stop closes it. Camera and trigger settings are captured when their devices
start. Up to four RFID DAQ boards can be configured for live acquisition.

## Experiment files

The main config owns scientific metadata. The linked acquisition GUI-state file
stores hardware settings, editing preferences and supported processing state.
Load the main config and keep the linked files together when moving experiments.

Software versions and scientific format versions are independent. See the
[config](docs/CONFIG_V8.md), [diagnostic](docs/DIAGNOSTIC_V12.md) and
[compatibility](docs/COMPATIBILITY.md) documentation for the published contracts.
Installation and camera requirements are described in the [README](README.md).
