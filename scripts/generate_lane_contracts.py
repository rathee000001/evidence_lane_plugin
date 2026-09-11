"""Maintainer CLI binding to the original package-owned lane contract compiler."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/evidence-lane-plugin/scripts'))
from regenerate_compact_lane_schema_registry import exports, generate, main

__all__ = ['exports', 'generate']

if __name__ == '__main__':
    main()
