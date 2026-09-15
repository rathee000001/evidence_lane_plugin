"""Run the canonical plugin Hook package generator from the workspace root."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"
sys.path.insert(0, str(PLUGIN / "scripts"))

from regenerate_hook_packages import generate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(generate(root=PLUGIN, check=args.check)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
