"""Run offline recovery from the installed plugin with its configured Python."""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN/'src'))


def main():
    from evidence_lane_plugin.database_recovery import recovery_main
    return recovery_main()


if __name__ == '__main__':
    raise SystemExit(main())
