"""Compile the actual sketch against small hardware stubs and run each scenario.

Requires a C++17 compiler (g++). Run from any directory; no Python packages needed.
These simulations cannot validate mouse sensitivity, wiring or the power supply.
"""
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
CASES = (
    "sensing debounce noise stuck faults startup autorange readback range bus "
    "wraps daily low_valid large_bottle wide_baseline failed_low failed_high "
    "tune_link multi_search reasons range_bounds noisy_wait noisy_fault usb_backpressure"
).split()


def main():
    with tempfile.TemporaryDirectory() as temp:
        binary = Path(temp) / "test_firmware"
        subprocess.run([
            "g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
            "-I", str(HERE / "standalone_stubs"),
            str(HERE / "test_standalone_lickometer_firmware.cpp"), "-o", str(binary),
        ], check=True)
        for case in CASES:
            subprocess.run([str(binary), case], check=True)
    print(f"{len(CASES)} scenarios passed")


if __name__ == "__main__":
    main()
