from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("g++"), "Native C++ compiler is unavailable")
class FirmwareTests(unittest.TestCase):
    def test_diagnostic_v81_native_scenarios(self):
        root = Path(__file__).resolve().parent.parent
        import sys
        runner = root / "firmware/DeepEcoHAB_Lickometer_v8_1_Diagnostic/tests/run_tests.py"
        result = subprocess.run([sys.executable, str(runner)], capture_output=True,
                                text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("24 scenarios passed", result.stdout)
