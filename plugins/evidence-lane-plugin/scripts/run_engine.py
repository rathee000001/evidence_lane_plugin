"""Packaged engine executable entrypoint shared by plugin and Studio launch."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

if __name__ == '__main__':
    from evidence_lane_plugin.service import main
    main()
