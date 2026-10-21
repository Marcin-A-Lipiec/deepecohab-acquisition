# DeepEcoHab Acquisition 1.0.0

First public version of the Windows acquisition application for DeepEcoHab.
Earlier 3.2.x numbers identify internal modernization builds.

- RFID/TTL, camera and lighting acquisition with experiment metadata and layout
  editing, archived-board preprocessing, interpolation and diagnostic exports.
- DeepEcoHab appearance with bundled offline fonts/icons, Light/Dark/System
  themes, a responsive sidebar and persistent acquisition controls.
- uv tool installation, a native double-click Desktop icon, startup logs and a
  cancellable System check for computer setup.
- Config v8, acquisition GUI state v1, diagnostics v12 and Parquet schema v5.
  Scientific behavior and hardware interfaces are retained from the working
  internal acquisition build.
- Draft metadata saves and RFID recording without metadata prompts; preprocessing
  explicitly reviews incomplete metadata and records accepted limitations.
- Per-source fixed UTC offsets for legacy PCs that did not adjust their clocks,
  without guessing offsets or modifying recordings.
- Wheel scrolling preserves editor values. Environmental settings follows EcoHAB
  layout in the Experiment sidebar group.

Use the commands in the [installation guide](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition/blob/v1.0.0/docs/INSTALLATION.md). Windows x64/Python 3.12 is
the locally validated software target; metadata permits Python 3.10–3.12.
Teledyne Spinnaker/SpinView, the matching vendor PySpin wheel, camera/USB drivers
and FFmpeg/ffprobe require separate installation. RFID and offline preprocessing
do not require the camera SDK.

These notes are prepared locally and must be reviewed before publication. The
original-code license is still undecided; see [LICENSE_STATUS.md](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition/blob/v1.0.0/LICENSE_STATUS.md).
Software test results do not certify a particular acquisition PC, physical camera
or electrical timing. See [TESTING.md](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition/blob/v1.0.0/TESTING.md) for acceptance checks.
