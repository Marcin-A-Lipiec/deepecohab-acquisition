# Camera and video setup

Open **System check** under Experiment in the acquisition header for executable paths, readiness
checks and **Setup help**. The installed help is maintained in
[`assets/help/camera_setup.md`](../deepecohab_recorder/assets/help/camera_setup.md);
it is available in the wheel without a source checkout or internet connection.
[INSTALLATION.md](INSTALLATION.md) gives the installation and CLI commands.

## Compatibility and evidence

| Component | Supported requirement | Validation in this copy |
|---|---|---|
| Windows/Python | Windows x64; Python 3.10–3.12, matched to vendor wheel | Windows 11 x64, Python 3.12.12 |
| NumPy/OpenCV | NumPy below 2; headless OpenCV below 4.12 | NumPy 1.26.4, OpenCV 4.11.0.86 |
| Core GUI/RFID/preprocessing packages | Declared dependencies and tested constraints | Full regressions and isolated imports |
| Spinnaker/PySpin | Vendor-matched SDK/runtime and Python ABI/architecture | PySpin is absent from the new Python 3.12 acquisition environment; physical camera acceptance remains pending |
| Video executables | FFmpeg plus ffprobe; explicit paths or automatic discovery | Native executables, synthetic encode/decode/frame counts, production writer tests |
| NVIDIA NVENC | Driver/GPU compatible with the FFmpeg build | Both existing HEVC profiles pass the small synthetic runtime check on this PC |
| CPU/lossless encoders | libx265, libx264, ffv1 | All three existing profiles pass the small synthetic runtime check |

The original `camera` Conda environment contains a PySpin 4.2.0.88 installation
tagged `cp310-cp310-win_amd64`. That installed binary targets Python 3.10 and is
not a Python 3.12 wheel. It has not been copied into the new environment. This
observation does not certify its native SDK, current physical camera or drivers.
The original environment is unchanged. To use that SDK family, obtain the vendor
wheel for the chosen supported Python version and recreate the acquisition tool
with `--with`, as documented. A compatible vendor installation is a required
physical-camera acceptance gate, not an automatically delivered dependency.

System check reports actual wheel/runtime versions. Different major/minor
families fail camera software readiness; build differences are flagged for
review against the vendor pairing. The compatibility reports have simulated
regressions, not validation of every permitted SDK/Python combination.

## Before physical recording

1. Install the full vendor SDK, transport/USB drivers and SpinView. Confirm real
   acquisition in SpinView, then close it to release camera access.
2. Include the matching vendor wheel in this application's uv tool environment.
   Do not install the unrelated package named `pyspin`.
3. Choose and save the FFmpeg/ffprobe files in System check. Run the check with
   video tests enabled and optionally **Detect cameras** while acquisition is stopped.
4. Verify normal ROI, resolution/FPS, preview, segmentation, external triggers,
   CPU/NVENC modes and fallback on actual hardware. Exercise stop, restart,
   disconnect and recovery; check the frame ledger and decode all recorded video.

Readiness checks use three 256×256 synthetic frames at 30 fps, with the existing
profile arguments and corresponding MP4/MKV containers. They do not benchmark
sustained acquisition or certify electrical timing. RFID, metadata and offline
preprocessing do not need Spinnaker. Firmware/lighting/DAQ acceptance is described
in [FIRMWARE.md](FIRMWARE.md) and [TESTING.md](../TESTING.md).
