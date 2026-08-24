"""Commit one exact project-root WORKING authority into all 18 sectors.

This maintainer route is pointer-neutral. It uses the accepted PV lane bundle
as historical parent, indexes the Git index's tracked path set using current
worktree bytes, migrates Plan and ChatLineage into their canonical sectors,
and removes only the verified legacy duplicates named by the migration receipt.
Untracked test, evidence, and POC files remain locator-only working evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evidence_lane_plugin.project_authority import migrate_working_project_sectors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--accepted-pv", required=True)
    parser.add_argument("--pointer-generation", type=int, required=True)
    parser.add_argument("--expected-branch")
    parser.add_argument("--expected-head")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    result = migrate_working_project_sectors(
        arguments.project_root,
        repository_root=arguments.repository_root,
        project_id=arguments.project_id,
        accepted_pv=arguments.accepted_pv,
        pointer_generation=arguments.pointer_generation,
        expected_branch=arguments.expected_branch,
        expected_head=arguments.expected_head,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
