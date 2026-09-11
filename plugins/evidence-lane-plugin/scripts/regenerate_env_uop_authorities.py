#!/usr/bin/env python3
"""Regenerate ENV/UOP executable authorities from current repository source."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.env_uop_graph import regenerate_env_uop_authorities


def main() -> int:
    receipt = regenerate_env_uop_authorities(PLUGIN_ROOT)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
