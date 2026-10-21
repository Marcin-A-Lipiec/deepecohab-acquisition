# DeepEcoHAB Acquisition

**Version 1.0.0** — Windows software for EcoHAB RFID/TTL recording, cameras,
lighting, experiment metadata and data preprocessing for DeepEcoHAB analysis.
Install it once, then open it by double-clicking its Desktop icon. You do not
need Python knowledge, a GitHub account or Git for the normal installation.

This guide targets **64-bit Windows 10/11 and Python 3.12**. uv downloads Python
and the required Python libraries for you. The package permits Python 3.10–3.12;
a camera's vendor wheel must match the selected Python version. Other operating
systems and their hardware integrations have not been validated.

- [Install uv](#1-install-uv-once-per-computer)
- [Install the application](#2-install-deepecohab-acquisition)
- [Camera and hardware setup](#3-optional-camera-and-hardware-setup)
- [Open and check the application](#4-open-and-check-the-application)
- [Find out about updates](#5-find-out-about-updates)
- [Update](#updating)
- [Install an older version](#7-return-to-an-earlier-version)
- [Uninstall](#8-uninstall)
- [Troubleshooting](#9-troubleshooting)
- [All routine commands](#10-command-reference)
- [Install a downloaded source folder](#11-install-a-downloaded-source-folder)

## Before you begin

Use a normal **PowerShell** window, under the Windows account that will run the
experiment. Administrator mode is normally unnecessary.

1. Open Start, type **PowerShell**, then open **Windows PowerShell** or PowerShell.
2. Copy a command from a code box, paste it into PowerShell and press Enter.
3. Wait until the `PS C:\...>` prompt returns before entering the next command.
4. Copy only the command, not the prompt or the triple-backtick markers.
5. If a command reports an error, stop and use [Troubleshooting](#9-troubleshooting).

Keep experiment recordings in their own experiment folders, outside application
source folders and Python/tool environments. Updating or uninstalling the tool
does not delete these experiments or the application's separate user preferences.

## 1. Install uv (once per computer)

uv manages the application's Python installation and libraries. Run the official
[uv installer](https://docs.astral.sh/uv/getting-started/installation/):

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

This downloads and runs Astral's uv installer. The execution-policy option applies
to that PowerShell process; it does not permanently change the computer's policy.

**Close PowerShell and open a new PowerShell window.** Then check:

```powershell
uv --version
```

You should see a uv version number. If uv was already installed and this command
works, continue to the next section.

## 2. Install DeepEcoHAB Acquisition

The recommended installer selects the latest **published stable GitHub Release**,
installs its wheel with that same release's tested dependency constraints, and
creates Desktop and Start menu shortcuts. It does not install an unfinished
development branch. No Git installation is needed.

Download the installation helper:

```powershell
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Marcin-A-Lipiec/deepecohab-acquisition/main/install_windows.ps1" -OutFile "$env:TEMP\deepecohab-install.ps1"
```

Then run it:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:TEMP\deepecohab-install.ps1" -ReleaseVersion latest -StartMenu
```

Wait for the success message. uv supplies Python and the declared libraries in
an isolated application environment. The normal installation needs an internet
connection; subsequent GUI launches do not. Download time depends on the connection.

Make the commands available in future PowerShell windows:

```powershell
uv tool update-shell
```

Close PowerShell and open it again. Confirm the installed version:

```powershell
deepecohab-acquisition-cli --version
```

The installer copies the application into uv's environment. Moving/deleting a
downloaded installer or source ZIP later does not break that installed copy.

## 3. Optional camera and hardware setup

**RFID/TTL and preprocessing do not require camera software.** The following
components are installed separately when the relevant hardware is used:

| Component | What to do |
|---|---|
| Teledyne Spinnaker SDK and SpinView | Install the manufacturer's Windows package. Confirm that the camera works in SpinView. |
| Teledyne PySpin wheel | Obtain the vendor's `.whl` matching your SDK, Windows x64 and selected Python version; Python 3.12 normally requires `cp312`. SpinView alone does not install this Python interface. |
| FFmpeg and ffprobe | Download a Windows build linked from the [official FFmpeg page](https://ffmpeg.org/download.html), extract it, then select both executable paths in **Experiment → System check**. |
| Board USB drivers and firmware | Follow the board-specific [firmware guide](docs/FIRMWARE.md). Firmware is not flashed automatically. |

These vendor runtimes, drivers and executables are not supplied by the Python
installation. Do not install the unrelated package named `pyspin` from PyPI.
See the [camera setup guide](docs/CAMERA_SETUP.md) and
[Teledyne's PySpin instructions](https://www.teledynevisionsolutions.com/support/support-center/technical-guidance/iis/installing-pyspin-for-the-spinnaker-sdk/).

To include the vendor wheel, close the GUI and run the installer with its exact
downloaded filename. **Replace the example path below** with your actual `.whl`:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:TEMP\deepecohab-install.ps1" -ReleaseVersion latest -Force -StartMenu -PySpinWheel "C:\Downloads\YOUR_MATCHING_TELEDYNE_WHEEL.whl"
```

If the vendor wheel needs Python 3.10 or 3.11, add `-PythonVersion 3.10` or
`-PythonVersion 3.11` to that command. Retain the same Python choice and matching
wheel on future updates. Keep the wheel outside uv's application environment.

## 4. Open and check the application

Double-click **DeepEcoHab Acquisition** on your Desktop or in the Start menu.
You can also launch from PowerShell:

```powershell
deepecohab-acquisition-cli launch
```

Open **Experiment → System check → Run system check**. For camera recording,
also choose the FFmpeg/ffprobe paths, click **Save paths for this computer**, and
test the camera and encoders before a real experiment. A camera working in
SpinView still needs PySpin inside this application's own environment.

To check core software from PowerShell while acquisition is stopped:

```powershell
deepecohab-acquisition-cli doctor
```

Missing optional camera components do not make this basic check fail. For camera
software, camera detection and a short encoder test:

```powershell
deepecohab-acquisition-cli doctor --require-camera --camera-devices
```

The [user guide](docs/USER_GUIDE.md) explains every acquisition page, metadata,
layout dragging, recording, preprocessing and the generated files.

## 5. Find out about updates

In the GUI, choose **Experiment → Check for updates**. The check displays the
installed version, the latest published stable version and whether an update
is available. It links to release notes and these installation instructions.
The terminal equivalent is:

```powershell
deepecohab-acquisition-cli updates
```

Checks run **only when requested**. They contact GitHub, which sees your IP
address and application version; no experiment files, tags or configuration are
sent. A failed/offline check is reported as unavailable, never as “up to date”.
An unfinished release without its installation wheel is not offered as ready
to install. Neither the check nor launching the GUI installs anything.

For notifications without opening the application, sign in to GitHub and open
the [repository](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition).
Choose **Watch → Custom → Releases → Apply** and enable email notifications in
your GitHub settings. You do not need this account or subscription to install a
public release. A source/README commit alone is not an application update notice.

## Updating

1. Read the new version's [release notes](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition/releases).
2. Finish recording/preprocessing, save the experiment configuration and close
   every DeepEcoHAB Acquisition window. Update between experiments.
3. Open PowerShell and download the current installation helper:

```powershell
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/Marcin-A-Lipiec/deepecohab-acquisition/main/install_windows.ps1" -OutFile "$env:TEMP\deepecohab-install.ps1"
```

4. Install the latest stable release, replacing the existing application:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:TEMP\deepecohab-install.ps1" -ReleaseVersion latest -Force -StartMenu
```

**Camera users:** use the command below instead of step 4, substituting your
matching vendor-wheel path and retaining `-PythonVersion` if you previously
selected a version other than 3.12:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:TEMP\deepecohab-install.ps1" -ReleaseVersion latest -Force -StartMenu -PySpinWheel "C:\Downloads\YOUR_MATCHING_TELEDYNE_WHEEL.whl"
```

If PySpin is detected in the existing installation, the installer refuses a
replacement without a supplied vendor wheel. This prevents silently
losing camera support. A failed preflight leaves the existing installation intact.

5. Confirm the version, reopen the Desktop icon and run System check:

```powershell
deepecohab-acquisition-cli --version
deepecohab-acquisition-cli doctor
```

Existing experiment files, saved per-user preferences and FFmpeg paths are
retained. Separate SDKs/drivers/FFmpeg remain installed. Updating the application
does not automatically update those external components or migrate scientific
file formats. There is no unattended or in-recording updater.

Do not use `uv tool upgrade deepecohab-acquisition` as the documented update
method: this application is installed from a particular GitHub release rather
than an unpinned PyPI package. Use the installer above to select the new release
and its matching tested constraints together.

## 7. Return to an earlier version

Close the GUI. Download the installation helper as in the updating section, then
request an existing published version explicitly. For example:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:TEMP\deepecohab-install.ps1" -ReleaseVersion 1.0.0 -Force -StartMenu
```

Camera users must also supply `-PySpinWheel` and the matching Python selection.
Only choose a version that exists on the Releases page. This reinstalls the
software; it does not restore older experiment data or undo preprocessing.
Check release notes before opening files written by a newer version.

## 8. Uninstall

1. Stop recording/preprocessing, save your configuration and close the GUI.
2. Remove this application's Desktop and Start menu shortcuts:

```powershell
deepecohab-acquisition-shortcut --remove --start-menu
```

3. Remove its uv-managed application environment:

```powershell
uv tool uninstall deepecohab-acquisition
```

4. Confirm that it is no longer listed:

```powershell
uv tool list
```

This retains experiment recordings/configurations/preprocessed files, application
preferences/logs, uv, shared Python downloads, SDKs, drivers and FFmpeg. Reinstall
using section 2. No recording-folder deletion command is part of uninstallation.
A shortcut created with `--directory` must be removed with that same directory.
If the application was already removed, delete its leftover Desktop/Start menu
shortcut manually; the shortcut-removal command may no longer exist.

## 9. Troubleshooting

| Problem | What to do |
|---|---|
| `uv` is not recognized | Close/reopen PowerShell. If needed run `& "$env:USERPROFILE\.local\bin\uv.exe" --version` and use that full executable path instead of `uv`, or repeat the uv installation. A custom uv installation directory needs its actual path. |
| An application command is not recognized | Run `uv tool update-shell`, then close/reopen PowerShell. Confirm installation using `uv tool list`. The installer resolves the shortcut command directly, even before refreshing PATH. |
| No release found / download returns 404 | Confirm that a public stable Release and its wheel have been published. A GitHub source repository, tag alone, draft or private release is insufficient for anonymous installation. Use a supplied source ZIP meanwhile. |
| Release package is missing | Wait for publication to finish; do not substitute an unrelated wheel or development branch. |
| Installer says the file has no `.ps1` extension | Copy the command exactly. The filename ends in `.ps1`, with no trailing `\`. For a source install, run it from the folder containing `install_windows.ps1`. |
| Internet/proxy/TLS/rate-limit error | Try again later or ask your IT administrator about access to GitHub, Astral and Python package downloads. The GUI itself can run offline after installation. |
| PySpin is required during update | Supply the matching vendor wheel using `-PySpinWheel`; the old camera-enabled environment is intentionally retained until then. |
| Camera is missing | Check SpinView first, then SDK/PySpin/Python compatibility and the application's System check. A system-wide PySpin installation does not supply this isolated tool. |
| FFmpeg or ffprobe is missing | Select both executables in **Experiment → System check** and save the paths. Installing OpenCV does not install these executables. |
| Desktop icon does not open | Run `deepecohab-acquisition-cli launch` for the error, then recreate shortcuts using the command below. Copying a `.lnk` from another computer does not install the application. |

Recreate the Desktop and Start menu shortcuts:

```powershell
deepecohab-acquisition-shortcut --start-menu
```

Show the locations of application logs, preferences and default experiment data:

```powershell
deepecohab-acquisition-cli paths
```

Report problems through [GitHub Issues](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition/issues).
Include the application version, Windows/Python/SDK versions if relevant, the
exact command and full error, and a System check report. Review reports before
sharing them: paths and device identifiers can be personal. Do not send private
recordings or credentials in public issues.

## 10. Command reference

Run application commands after installation and after refreshing PowerShell PATH.
The installation, update, rollback and uninstall commands are given in their
numbered sections above.

| Purpose | PowerShell command |
|---|---|
| Check uv | `uv --version` |
| Update uv itself (standalone installer) | `uv self update` |
| Refresh tool PATH for future terminals | `uv tool update-shell` |
| List installed uv tools | `uv tool list` |
| Show application version | `deepecohab-acquisition-cli --version` |
| Launch with terminal diagnostics | `deepecohab-acquisition-cli launch` |
| Check for a stable application update | `deepecohab-acquisition-cli updates` |
| Print the update check as JSON | `deepecohab-acquisition-cli updates --json` |
| Check core dependencies | `deepecohab-acquisition-cli doctor` |
| Check camera and video support | `deepecohab-acquisition-cli doctor --require-camera --camera-devices` |
| Print the System check as JSON | `deepecohab-acquisition-cli doctor --json` |
| Show application/data paths | `deepecohab-acquisition-cli paths` |
| Create/repair both shortcuts | `deepecohab-acquisition-shortcut --start-menu` |
| Remove both shortcuts | `deepecohab-acquisition-shortcut --remove --start-menu` |
| Uninstall the application | `uv tool uninstall deepecohab-acquisition` |

For board recovery and video verification commands, see the [user guide](docs/USER_GUIDE.md).
Updating uv is separate from updating DeepEcoHAB Acquisition. If uv was installed
through a package manager, update uv through that same package manager.

## 11. Install a downloaded source folder

Use this method for a maintainer-supplied ZIP, an offline transfer of the source
or before the first stable GitHub Release is published. **An internet connection
is still needed to download Python and dependencies** unless they are already
available in uv's cache; this ZIP is not a fully offline installer.

1. Install uv as in section 1.
2. Download/extract the source ZIP. If it contains another top-level folder, open
   that inner folder. Locate `install_windows.ps1`, `pyproject.toml` and `requirements.txt`.
3. In File Explorer, click the address bar, type `powershell`, then press Enter.
   Alternatively type `cd "C:\the actual extracted folder"` in PowerShell, replacing
   the example with your real folder path.
4. Check that you are in the correct folder:

```powershell
Test-Path .\install_windows.ps1
```

It should print `True`. Then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1 -StartMenu
```

This installs **that local source version**, using its own `requirements.txt`.
To replace an existing installation from a newly supplied source folder, add
`-Force`. Camera users also add `-PySpinWheel` and the matching Python version as
described above. Run `uv tool update-shell`, reopen PowerShell and verify the
version. Do not run a source update from the older extracted folder by mistake.

For an equivalent direct uv installation from this folder:

```powershell
uv tool install --python 3.12 --constraints .\requirements.txt .
deepecohab-acquisition-shortcut --start-menu
```

The helper is preferred for updates because it checks for an open GUI and helps
prevent losing a separately installed PySpin interface. Existing local `.venv`
launchers remain available for developers; they are separate from uv tool installs.

## Documentation and development

- [User guide](docs/USER_GUIDE.md): configuration, acquisition and preprocessing.
- [Installation details](docs/INSTALLATION.md) and [camera setup](docs/CAMERA_SETUP.md).
- [Changelog](CHANGELOG.md) and [release preparation](docs/RELEASING.md).
- [Architecture](ARCHITECTURE.md), [contributing](CONTRIBUTING.md) and [testing](TESTING.md).
- Scientific contracts: [config v8](docs/CONFIG_V8.md), GUI state v1,
  [diagnostics v12](docs/DIAGNOSTIC_V12.md), and Parquet schema v5. Application
  releases do not themselves change these contracts.
- [MIT project license](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).

Maintainers publish an immutable `vX.Y.Z` tag, matching packages and release notes
for every software release. README-only corrections can remain ordinary commits
without a version bump. Never replace an already distributed software release
with different code under the same version; publish the next version instead.
