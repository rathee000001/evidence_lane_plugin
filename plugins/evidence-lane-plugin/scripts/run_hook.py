"""Installed package entrypoint for visible native lifecycle capture."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evidence_lane_plugin.hook_contract import main

if __name__ == "__main__":
    raise SystemExit(main())
