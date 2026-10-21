# Installation and launch

For complete beginner instructions, follow the [README](../README.md). It is
the primary guide for installation, update notifications, updating, rollback
and uninstallation. This page provides additional setup and diagnostic detail.

The Windows helper supports `-ReleaseVersion latest` or a specific stable version
such as `-ReleaseVersion 1.0.0`. It downloads release metadata, uses that release's
wheel and tagged dependency constraints, and does not need Git. Without that
option it installs the local source folder as before. A source upload or tag
alone is not a published installation package. Public release assets must exist.

Use `-Force` for an update/rollback, after closing the GUI. Retain the matching
Python selection and pass `-PySpinWheel` for camera-enabled installs. The helper
blocks a reinstall that would silently remove detected PySpin. The
README's commands download the helper and explain this complete workflow.

This application is validated on 64-bit Windows with Python 3.12. uv supplies Python
if needed and installs the Python dependencies in a separate tool environment.
Python 3.10–3.12 is permitted; a camera's vendor wheel must match the chosen
Python version and architecture. Physical camera compatibility is a separate
acceptance check.

## Install this copy

Install [uv using its official instructions](https://docs.astral.sh/uv/getting-started/installation/).
Open PowerShell in the acquisition folder:

```powershell
uv tool install --python 3.12 --constraints requirements.txt .
deepecohab-acquisition-shortcut
```

The shortcut launches the installed GUI directly, without an activated terminal
or a network request. Use `deepecohab-acquisition-shortcut --start-menu` to add
both Desktop and Start menu entries. Repeating the command updates its own
shortcuts. It refuses to replace a different application with the same name.

The constraints select the tested Windows dependency versions. Alternatively run
the provided local installer:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
```

It uses those constraints and creates a Desktop icon. Add `-StartMenu` or
`-ShortcutDirectory "C:\My folder"` when invoking the script. No machine-wide
PowerShell policy is changed. If an exposed command is not found, run
`uv tool update-shell`, then open a new terminal. The installer also resolves
the shortcut command directly in uv's executable directory.

## Version 1.0.0 wheel and GitHub release

The built wheel can be installed without Git:

```powershell
uv tool install --python 3.12 --constraints requirements.txt .\dist\deepecohab_acquisition-1.0.0-py3-none-any.whl
deepecohab-acquisition-shortcut
```

The intended repository is `Marcin-A-Lipiec/deepecohab-acquisition`. The following
URLs become available after the maintainer publishes tag **v1.0.0** and uploads
the built wheel to its GitHub Release. They do not work before publication.
Users will be able to install without downloading the source or installing Git:

```powershell
uv tool install --python 3.12 --constraints "https://raw.githubusercontent.com/Marcin-A-Lipiec/deepecohab-acquisition/v1.0.0/requirements.txt" "https://github.com/Marcin-A-Lipiec/deepecohab-acquisition/releases/download/v1.0.0/deepecohab_acquisition-1.0.0-py3-none-any.whl"
deepecohab-acquisition-shortcut
```

For installation directly from the tagged source, install Git and use:

```powershell
uv tool install --python 3.12 --constraints "https://raw.githubusercontent.com/Marcin-A-Lipiec/deepecohab-acquisition/v1.0.0/requirements.txt" "git+https://github.com/Marcin-A-Lipiec/deepecohab-acquisition.git@v1.0.0"
deepecohab-acquisition-shortcut
```

There is no PyPI publication requirement. The project license is
[undecided](../LICENSE_STATUS.md); third-party reference, icon and font notices
are preserved. See [uv's tools guide](https://docs.astral.sh/uv/guides/tools/).
`uv.lock` controls development builds; tool installations resolve the package
metadata. Use the tested requirements constraints to reproduce the lab environment.

## Automatically installed and external components

The package declares PySide6, pyserial, NumPy, headless OpenCV, tzdata, PyArrow,
platformdirs and, on Windows, pywin32. NumPy stays below 2 and OpenCV below 4.12
to avoid the newer OpenCV dependency on NumPy 2. The current tested versions are
listed in `requirements.txt`; do not maintain that generated file separately.

| Component | Setup |
|---|---|
| RFID/TTL and metadata/preprocessing | Python dependencies above; no camera SDK required |
| Spinnaker SDK, transport drivers and SpinView | Obtain the matching Windows x64 SDK from Teledyne and install its runtime/drivers |
| PySpin | Use Teledyne's matching Python wheel in the acquisition environment |
| FFmpeg and ffprobe | Download a Windows build linked from the official FFmpeg page; select the executables in **System check**, or add its `bin` directory to user PATH |
| GPU encoding | Install the appropriate NVIDIA driver and verify the required NVENC encoder on the acquisition PC |
| DAQ/lighting USB drivers and Arduino firmware | Follow the board-specific setup in [FIRMWARE.md](FIRMWARE.md); the application does not flash boards |

### Teledyne cameras

Follow [Teledyne's PySpin installation guide](https://www.teledynevisionsolutions.com/support/support-center/technical-guidance/iis/installing-pyspin-for-the-spinnaker-sdk/).
Install the SDK/runtime and confirm that the camera acquires frames in **SpinView**.
SpinView alone does not install the Python camera interface. Download the wheel
whose Python ABI, Windows x64 architecture and SDK version match the application.
For Python 3.12, the wheel must declare a compatible `cp312` ABI; choose another
supported Python version if the vendor's SDK does not provide that wheel.

Add that local wheel when creating/recreating the tool. Replace the wheel path:

```powershell
uv tool install --force --python 3.12 --constraints .\requirements.txt --with "C:\Downloads\MATCHING_VENDOR_WHEEL.whl" .
deepecohab-acquisition-shortcut
```

Close the GUI before recreating its environment. Keep the vendor wheel for
future installations. Do not install the unrelated PyPI package named `pyspin`.
For the published installation, use the same release URL and constraints URL
shown above, adding `--with "C:\Downloads\MATCHING_VENDOR_WHEEL.whl"`. The vendor
wheel remains a separate download because it is not redistributed here.
Installing the vendor wheel into system Python or the analysis tool does not
make it available in the acquisition tool environment. For the local `.venv`
workflow, use `uv pip install --python .venv\Scripts\python.exe PATH_TO_VENDOR_WHEEL`
after installing the SDK. Neither path certifies a particular physical camera.

The local installer can include the vendor wheel and a supported Python version:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1 -Force -PythonVersion 3.12 -PySpinWheel "C:\Downloads\MATCHING_VENDOR_WHEEL.whl"
```

`-Force` recreates an existing tool environment; close acquisition first.
uv validates the wheel's Python/platform compatibility. The native SDK and USB
drivers are installed separately. See [CAMERA_SETUP.md](CAMERA_SETUP.md) for the
tested base environment and camera acceptance limits.

### FFmpeg

Use the builds linked by [FFmpeg's official download page](https://ffmpeg.org/download.html).
Extract the download, open **System check** under Experiment in the GUI header, browse to
**ffmpeg.exe** and **ffprobe.exe**, then click **Save paths for this computer**.
These settings work for desktop launches, recording, encoder preflight, container
counts and the installed verification command. Experiment configs do not contain
these machine paths.

With blank fields, FFmpeg uses PATH; ffprobe first looks beside FFmpeg, then on
PATH. Invalid explicit paths report a problem. A running recording retains its
original paths; settings are locked while the camera runs. To use PATH instead,
add the `bin` folder containing both executables, open a new terminal, and verify:

```powershell
ffmpeg -version
ffprobe -version
```

Then reopen the GUI. After a recording stops, `deepecohab-verify-video EXPERIMENT.config.json`
decodes recorded files and checks frame counts. The Python OpenCV package does
not supply the application's external FFmpeg/ffprobe executables.

### System check

Open **System check** under Experiment in the header and click **Run system check**. It checks
imports in isolated processes, Python/package versions, writable output storage
and free space, serial ports, PySpin/SDK versions, video tools and all five encoder
profiles. **Test video encoders** verifies three synthetic 256×256 frames at 30 fps;
**Detect cameras** enumerates without initialization or acquisition. Camera and
encoder probes are deferred during acquisition, matching or preprocessing. The
dialog stays responsive, supports cancellation and can save a report. Setup help
is included in installed packages.

Equivalent terminal checks:

```powershell
deepecohab-acquisition-cli doctor
deepecohab-acquisition-cli doctor --json --encode-test --output-dir "D:\Experiments"
deepecohab-acquisition-cli doctor --require-camera --camera-devices
```

`doctor` returns 0 when the interface, RFID/preprocessing libraries and output
storage are ready. Missing optional camera software or encoders are reported
without making that base check fail. `--require-camera` additionally requires
camera software and a working default encoder or enabled CPU fallback; it runs
encoder tests. Combining it with `--camera-devices` also requires a detected camera.
These checks do not certify trigger timing or recording speed. Do not run terminal
encoder/device probes while acquiring. `--ffmpeg PATH` and `--ffprobe PATH` apply
to that check without saving settings; video verification accepts these overrides.

## Paths, diagnosis and removal

Experiments default to `%USERPROFILE%\DeepEcoHAB_data`; choose another writable
folder on Setup. Application logs/icons use the user's local application data,
independent of the folder in which the shortcut opens. To show exact paths:

```powershell
deepecohab-acquisition-cli paths
deepecohab-acquisition-cli --version
deepecohab-acquisition-cli launch
```

Startup errors appear in a dialog and `application.log`, with rotating backups.
The CLI launch keeps a console for diagnosis. The windowed command is
`deepecohab-acquisition`. The optional `DEEPECOHAB_ACQUISITION_HOME` environment
variable redirects application logs/icons for testing; it does not redirect
experiment output. It also redirects `preferences.json`, which holds per-user
video-tool paths and the appearance choice. Reinstalling the tool retains these preferences and experiments.

Stop devices and preprocessing and close the GUI before an update. Reinstall a
new wheel/tag with `uv tool install --force --python 3.12 NEW_RELEASE`, retaining
constraints and `--with` for the vendor wheel if used, then recreate shortcuts.
For rollback, install the previous wheel/tag the same way. Experiments and logs
are separate from the tool environment and are retained.

To check for a new stable release without installing anything, choose
**Experiment → Check for updates** or run:

```powershell
deepecohab-acquisition-cli updates
```

Only an explicit user request contacts GitHub. Checks are asynchronous in the
GUI, have a timeout and bounded response size, and show offline/no-release/
incomplete-package states separately from “up to date”. The README explains
optional GitHub release-email notifications. No background updater changes the
acquisition environment.

Remove shortcuts before uninstalling:

```powershell
deepecohab-acquisition-shortcut --remove --start-menu
uv tool uninstall deepecohab-acquisition
```

Removing the tool or shortcuts does not delete experiment data. A shortcut made
with `--directory` is removed with the same `--directory`. App data can be retained
for troubleshooting. If a startup import fails after a manually altered tool
environment, recreate it with the documented command and matching vendor wheel.
