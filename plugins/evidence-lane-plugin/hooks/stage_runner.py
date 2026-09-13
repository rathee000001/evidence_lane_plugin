"""Host-visible staged Hook launcher; generated from the v4 Hook contract."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from evidence_lane_plugin.hook_contract import HOOK_EVENT_NAMES
    from evidence_lane_plugin.hook_stage_runtime import HOST_HOOK_STAGES, main

    parser = argparse.ArgumentParser()
    parser.add_argument("event", choices=HOOK_EVENT_NAMES)
    parser.add_argument("stage", choices=[item["id"] for item in HOST_HOOK_STAGES])
    arguments = parser.parse_args()
    raise SystemExit(main(arguments.event, arguments.stage))
