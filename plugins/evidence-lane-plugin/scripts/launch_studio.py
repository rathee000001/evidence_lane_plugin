"""Installed release launcher for the persistent engine and visible read-only Studio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args(argv)
    from evidence_lane_plugin.launcher import ensure_local_engine
    from evidence_lane_plugin.service import request_owner_control

    health = ensure_local_engine(args.runtime_root)
    result = request_owner_control(args.runtime_root, "open_studio")
    print(
        json.dumps(
            {
                "status": "PASS",
                "engine_instance_id": health["instance_id"],
                "engine_phase": health["phase"],
                "studio": result,
                "project_state_changed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
