"""Packaged PostCompact hook entrypoint; generated from the v4 hook contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

if __name__ == "__main__":
    from evidence_lane_plugin.hook_contract import main_for_handler
    from evidence_lane_plugin.hook_event_handlers import PostCompactHandler
    raise SystemExit(main_for_handler(PostCompactHandler))
