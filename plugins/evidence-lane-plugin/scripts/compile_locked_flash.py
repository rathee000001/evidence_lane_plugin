"""Compile/check full ENV/UOP through their original owning implementation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from evidence_lane_plugin.env_uop_graph import regenerate_env_uop_authorities


def generate(registry, *, check=False):
    return regenerate_env_uop_authorities(ROOT, registry=registry, check=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(regenerate_env_uop_authorities(ROOT, check=args.check)))


if __name__ == '__main__':
    main()
