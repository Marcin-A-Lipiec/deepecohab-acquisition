"""Compatibility wrapper; also installed as deepecohab-verify-video."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepecohab_recorder.maintenance.verify_video import main, verify

if __name__ == "__main__":
    raise SystemExit(main())
