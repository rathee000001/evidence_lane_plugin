"""Run one guarded working-project sector Refresh from an importable entrypoint.

Windows multiprocessing workers must be able to import the main module.  This
entrypoint therefore owns ``freeze_support`` and never launches the lane engine
from stdin.  It may also move only manifest-less interrupted staging trees to
an explicit external quarantine before invoking the normal project authority
migration.  Sealed stages, accepted storage, candidates, HIL, and pointers are
outside this command's mutation authority.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

from evidence_lane_plugin.hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from evidence_lane_plugin.project_authority import migrate_working_project_sectors
from evidence_lane_plugin.timeutil import utc_now


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--accepted-pv", required=True)
    parser.add_argument("--pointer-generation", required=True, type=int)
    parser.add_argument("--expected-branch")
    parser.add_argument("--expected-head")
    parser.add_argument("--include-untracked-sources", action="store_true")
    parser.add_argument("--include-git-history", action="store_true")
    parser.add_argument("--quarantine-root")
    return parser


def _quarantine_partial_stages(
    project_root: Path,
    *,
    quarantine_root: Path | None,
) -> dict[str, Any]:
    stages = sorted(project_root.glob(".sectors-working-*.staging"))
    if not stages:
        return {
            "status": "PASS",
            "state": "NO_INTERRUPTED_STAGE",
            "rows": [],
            "accepted_storage_changed": False,
        }
    if quarantine_root is None:
        raise RuntimeError(
            "Partial working-sector stages exist; --quarantine-root is required."
        )
    if not (project_root / "sectors").is_dir():
        raise RuntimeError(
            "Active sectors are absent; the core recovery route must classify the stage."
        )
    quarantine_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for stage in stages:
        # A sealed stage belongs to the core exact recovery route and must never
        # be treated as disposable partial output by this wrapper.
        sealed_members = [
            stage / "working_migration_receipt.json",
            stage / "manifest.json",
            stage / "SHA256SUMS.json",
        ]
        if all(path.exists() for path in sealed_members):
            rows.append(
                {
                    "state": "DEFERRED_TO_CORE_SEALED_STAGE_RECOVERY",
                    "path": str(stage),
                }
            )
            continue
        if any(path.exists() for path in sealed_members):
            raise RuntimeError(f"Partially sealed stage requires review: {stage}")
        files = sorted(path for path in stage.rglob("*") if path.is_file())
        members = [
            {
                "path": path.relative_to(stage).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in files
        ]
        destination = quarantine_root / stage.name
        if destination.exists():
            raise RuntimeError(f"Quarantine destination already exists: {destination}")
        source_resolved = stage.resolve()
        destination_resolved = destination.resolve(strict=False)
        if source_resolved.parent != project_root.resolve():
            raise RuntimeError(f"Stage escaped the selected project root: {stage}")
        if destination_resolved.parent != quarantine_root.resolve():
            raise RuntimeError(
                f"Quarantine destination escaped its selected root: {destination}"
            )
        os.replace(source_resolved, destination_resolved)
        row_body = {
            "schema": "evidence-lane.partial-working-stage-quarantine.v1",
            "state": "QUARANTINED_INCOMPLETE",
            "source_path": str(source_resolved),
            "quarantine_path": str(destination_resolved),
            "file_count": len(members),
            "byte_count": sum(int(row["bytes"]) for row in members),
            "members": members,
            "sealed_stage": False,
            "accepted_storage_changed": False,
            "candidate_created": False,
            "pointer_moved": False,
            "hil_inferred": False,
            "quarantined_at": utc_now(),
        }
        row = {
            **row_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(row_body)),
        }
        atomic_write_json(destination_resolved / "quarantine_receipt.json", row)
        rows.append(row)
    body = {
        "schema": "evidence-lane.partial-working-stage-quarantine-set.v1",
        "status": "PASS",
        "state": "INCOMPLETE_STAGES_QUARANTINED",
        "project_root": str(project_root),
        "quarantine_root": str(quarantine_root),
        "rows": rows,
        "accepted_storage_changed": False,
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    receipt = {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
    }
    receipt_directory = quarantine_root / "quarantine_set_receipts"
    receipt_directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        receipt_directory / f"{receipt['receipt_sha256']}.json",
        receipt,
    )
    canonical_receipt = quarantine_root / "quarantine_set_receipt.json"
    if not canonical_receipt.exists():
        atomic_write_json(canonical_receipt, receipt)
    return receipt


def main() -> int:
    args = _parser().parse_args()
    project_root = Path(args.project_root).resolve()
    quarantine_root = (
        Path(args.quarantine_root).resolve() if args.quarantine_root else None
    )
    quarantine = _quarantine_partial_stages(
        project_root,
        quarantine_root=quarantine_root,
    )
    migration = migrate_working_project_sectors(
        project_root,
        repository_root=Path(args.repository_root).resolve(),
        project_id=args.project_id,
        accepted_pv=args.accepted_pv,
        pointer_generation=args.pointer_generation,
        expected_branch=args.expected_branch,
        expected_head=args.expected_head,
        include_untracked_sources=args.include_untracked_sources,
        include_git_history=args.include_git_history,
    )
    print(
        json.dumps(
            {"status": "PASS", "quarantine": quarantine, "migration": migration},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
