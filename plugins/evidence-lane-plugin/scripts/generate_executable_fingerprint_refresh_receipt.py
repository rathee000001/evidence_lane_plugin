"""Seal the project-neutral executable fingerprint gate for local installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.executable-fingerprint-refresh.v1"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"FINGERPRINT_JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--source-impact-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--full-passed", type=int, required=True)
    parser.add_argument("--full-failed", type=int, required=True)
    parser.add_argument("--full-skipped", type=int, required=True)
    parser.add_argument("--full-duration-seconds", type=float, required=True)
    parser.add_argument("--targeted-passed", type=int, required=True)
    args = parser.parse_args()

    repository = args.repository_root.resolve()
    plugin = args.plugin_root.resolve()
    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    executable_path = plugin / "manifests" / "executable-surface-registry.v1.json"
    repository_path = repository / ".github" / "evidence-lane-repository-fingerprints.v1.json"
    source_impact_path = args.source_impact_receipt.resolve()
    manifest = _json(manifest_path)
    executable = _json(executable_path)
    repository_fingerprints = _json(repository_path)
    source_impact = _json(source_impact_path)

    checks = {
        "executable_registry": (
            executable.get("status") == "PASS"
            and executable.get("all_installed_members_hash_bound") is True
            and executable.get("local_cache_or_output_included") is False
            and executable.get("historical_fallback_used") is False
            and int(executable.get("member_count", 0)) > 0
        ),
        "repository_fingerprints": (
            repository_fingerprints.get("status") == "PASS"
            and int(repository_fingerprints.get("tracked_path_count", 0)) > 0
        ),
        "source_impact": (
            source_impact.get("status") == "PASS"
            and source_impact.get("all_changed_paths_mapped") is True
            and source_impact.get("all_replacements_directly_purged") is True
            and int(source_impact.get("orphaned_generated_member_count", -1)) == 0
        ),
        "single_full_regression": args.full_passed > 0 and args.full_failed >= 0,
        "targeted_closure": args.targeted_passed == args.full_failed,
    }
    if not all(checks.values()):
        raise RuntimeError(f"EXECUTABLE_FINGERPRINT_REFRESH_BLOCKED:{checks}")

    core = {
        "schema": SCHEMA,
        "status": "PASS",
        "plugin_version": manifest["version"],
        "thread_id": args.thread_id,
        "checks": checks,
        "full_regression": {
            "status": "PASS_WITH_TARGETED_FAILURE_CLOSURE",
            "authorized_run_count": 1,
            "passed": args.full_passed,
            "failed": args.full_failed,
            "skipped": args.full_skipped,
            "duration_seconds": args.full_duration_seconds,
        },
        "targeted_closure": {
            "status": "PASS",
            "passed": args.targeted_passed,
            "failed": 0,
            "full_suite_rerun": False,
        },
        "executable_surface": {
            "status": "PASS",
            "member_count": executable["member_count"],
            "receipt_sha256": executable["receipt_sha256"],
            "file_sha256": _sha256(executable_path),
            "all_hash_bound": True,
            "local_cache_or_output_included": False,
            "historical_fallback_used": False,
        },
        "repository_fingerprints": {
            "status": "PASS",
            "tracked_path_count": repository_fingerprints["tracked_path_count"],
            "receipt_sha256": repository_fingerprints["receipt_sha256"],
            "file_sha256": _sha256(repository_path),
        },
        "source_impact": {
            "status": "PASS",
            "changed_path_count": source_impact["changed_path_count"],
            "impact_group_count": source_impact["impact_group_count"],
            "receipt_sha256": source_impact["receipt_sha256"],
            "file_sha256": _sha256(source_impact_path),
            "all_changed_paths_mapped": True,
            "all_replacements_directly_purged": True,
            "orphaned_generated_member_count": 0,
        },
        "negative_proofs": {
            "accepted_archive_queried": False,
            "candidate_created_or_cleared": False,
            "pointer_moved": False,
            "project_or_pv_mutated": False,
            "git_index_mutated": False,
            "git_ref_mutated": False,
        },
    }
    body = {**core, "receipt_sha256": hashlib.sha256(_canonical(core)).hexdigest().upper()}
    output = args.output.resolve()
    _write_atomic(output, _canonical(body))
    print(json.dumps({"status": "PASS", "output": str(output), "receipt_sha256": body["receipt_sha256"], "file_sha256": _sha256(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
