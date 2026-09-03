"""Move misplaced project-root runtime history to recoverable external quarantine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file
from .project_root_binding import validate_project_root_binding
from .timeutil import utc_now

PROJECT_RUNTIME_QUARANTINE_CONFIRMATION = (
    "MOVE PROJECT ROOT RUNTIME HISTORY TO EXTERNAL QUARANTINE"
)


def _manifest(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(directory).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def quarantine_project_runtime_history(
    project_root: str | Path,
    *,
    project_id: str,
    quarantine_root: str | Path,
    confirmation: str,
) -> dict[str, Any]:
    """Atomically relocate the non-authoritative runtime tree without deleting it."""

    require(
        confirmation == PROJECT_RUNTIME_QUARANTINE_CONFIRMATION,
        "PROJECT_RUNTIME_QUARANTINE_CONFIRMATION_REQUIRED",
        "Runtime-history relocation requires the exact bounded confirmation.",
        status="BLOCKED",
    )
    root = validate_project_root_binding(
        project_root,
        project_id=project_id,
        error_code="PROJECT_RUNTIME_QUARANTINE_PROJECT_ROOT_INVALID",
    )
    source = root / "runtime"
    target = Path(quarantine_root).resolve()
    try:
        target.relative_to(root)
        target_inside_root = True
    except ValueError:
        target_inside_root = False
    require(
        source.is_dir()
        and not target.exists()
        and not target_inside_root
        and source.drive.casefold() == target.drive.casefold(),
        "PROJECT_RUNTIME_QUARANTINE_BOUNDARY_INVALID",
        "Runtime quarantine requires one existing source and absent same-volume external target.",
        status="MISMATCH",
        source=str(source),
        target=str(target),
    )
    before = _manifest(source)
    manifest_sha256 = sha256_bytes(canonical_json_bytes(before))
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    after = _manifest(target)
    require(
        before == after and not source.exists(),
        "PROJECT_RUNTIME_QUARANTINE_READBACK_MISMATCH",
        "The moved runtime history changed during quarantine.",
        status="FAIL",
    )
    manifest_path = target.with_name(target.name + ".manifest.json")
    manifest_body = {
        "schema": "evidence-lane.project-runtime-quarantine-manifest.v1",
        "project_id": project_id,
        "source_relative_path": "runtime",
        "quarantine_root": str(target),
        "file_count": len(before),
        "total_bytes": sum(int(row["bytes"]) for row in before.values()),
        "members": before,
        "members_sha256": manifest_sha256,
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": sha256_bytes(canonical_json_bytes(manifest_body)),
    }
    atomic_write_json(manifest_path, manifest)
    receipt_body = {
        "schema": "evidence-lane.project-runtime-quarantine.v1",
        "status": "PASS",
        "project_id": project_id,
        "source_relative_path": "runtime",
        "quarantine_root": str(target),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "file_count": len(before),
        "total_bytes": manifest_body["total_bytes"],
        "source_present_after": False,
        "recoverable": True,
        "project_truth_effect": "NONE",
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "recorded_at": utc_now(),
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    atomic_write_json(
        root / "receipts" / "project-authority" / "runtime-history-quarantine.json",
        receipt,
    )
    return receipt


__all__ = [
    "PROJECT_RUNTIME_QUARANTINE_CONFIRMATION",
    "quarantine_project_runtime_history",
]
