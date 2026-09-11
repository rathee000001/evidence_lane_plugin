"""One packaged native hook entrypoint; the persistent engine owns capture."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

if __name__ == '__main__':
    from evidence_lane_plugin.hook_contract import main
    raise SystemExit(main())
