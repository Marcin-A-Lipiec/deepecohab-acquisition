# Prepare this computer

Use **Run system check** to check this application's Python environment, output
storage, serial ports, camera software and video tools. RFID, metadata editing
and preprocessing remain available without the camera SDK. Ports are listed
without opening them. **Detect cameras** enumerates devices without initializing
them or acquiring images. Camera and encoder probes are deferred while work is
active. Keep the check window open until it finishes or cancels.

## FFmpeg and ffprobe

Download a Windows build from the links on the
[official FFmpeg download page](https://ffmpeg.org/download.html). Extract it,
then select **ffmpeg.exe** and **ffprobe.exe** using Browse. **Save paths for this
computer** retains them for desktop launches and video verification. Spaces
and non-English characters in paths are supported.

A blank FFmpeg field uses PATH. A blank ffprobe field first looks beside FFmpeg,
then on PATH. An explicit invalid path reports an error so that the program you
selected is never silently replaced. Editing a field only changes this check;
save it to use that path for subsequent recordings. Settings are separate from
experiment metadata. A running recording retains the paths chosen at its start.

**Test video encoders** writes three temporary synthetic frames for each
supported profile, decodes the result and verifies the frame count when ffprobe
is available. An encoder listed by FFmpeg can still fail because its runtime or
NVIDIA driver is missing. A successful CPU H.264 test can support the camera's
existing automatic NVENC fallback. These small checks do not certify recording
speed at your chosen resolution. After a real recording stops, use
`deepecohab-verify-video PATH_TO_EXPERIMENT.config.json` for complete decode and
frame-count verification.

## Teledyne camera software

Follow [Teledyne's PySpin installation guide](https://www.teledynevisionsolutions.com/support/support-center/technical-guidance/iis/installing-pyspin-for-the-spinnaker-sdk/).
Install the full **Spinnaker SDK**, camera transport drivers and **SpinView**.
Confirm that the camera can acquire frames in SpinView, then close SpinView
before acquisition in this application. Installing SpinView alone does not
place PySpin in the acquisition Python environment.

Obtain the vendor's **spinnaker_python** wheel matching the SDK release, Windows
x64 and your Python version. This application supports Python 3.10 through 3.12;
the tested base environment uses Python 3.12. A Python 3.12 Windows wheel must
support `cp312` and `win_amd64`. Keep NumPy below 2; the application's dependency
constraints already do this. Do not install the unrelated package named `pyspin`.

For a uv tool installation, close the GUI and recreate its environment with the
vendor wheel included. From the acquisition source folder, replace the example
wheel path with the file you downloaded:

```powershell
uv tool install --force --python 3.12 --constraints .\requirements.txt --with "C:\Downloads\MATCHING_VENDOR_WHEEL.whl" .
deepecohab-acquisition-shortcut
```

For a local development `.venv`, use
`uv pip install --python .venv\Scripts\python.exe PATH_TO_VENDOR_WHEEL` instead.
Installing PySpin in another Python environment, including the analysis tool,
does not make it available here. Use the
[uv tools guide](https://docs.astral.sh/uv/guides/tools/) for tool environment
management. Vendor SDK components and wheels are obtained separately; they
are not redistributed by this application.

System check reports the PySpin wheel and native SDK versions when available.
Different SDK/wheel release families require the vendor-matched pair. Build
number differences need confirmation against the vendor's supplied pairing;
they are reported for review. A DLL error can also indicate an incomplete SDK
installation or an architecture mismatch. A native probe failure is contained
in a child process and reported without terminating the GUI.

## Physical acceptance

Software checks do not certify your camera, trigger wiring, USB transport or
board firmware. Verify your normal resolution, frame rate, trigger settings,
preview, segmentation, restart and shutdown on the acquisition PC. Decode the
recorded segments and reconcile container counts with the frame ledger. Verify
RFID/TTL and lighting separately using the existing board instructions. A
missing camera or serial port is a hardware/setup result, not a failure of
metadata editing or offline preprocessing.
