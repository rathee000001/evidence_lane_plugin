"""Seal the project-neutral executable fingerprint gate for local installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.executable-fingerprint-refresh.v1"
REGRESSION_PASS_STATUSES = {
    "PASS_WITH_TARGETED_FAILURE_CLOSURE",
    "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED",
}


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
    parser.add_argument("--semantic-currentness-receipt", type=Path, required=True)
    parser.add_argument("--targeted-closure-receipt", type=Path, required=True)
    parser.add_argument("--alternate-index-file", type=Path, required=True)
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
    semantic_path = args.semantic_currentness_receipt.resolve()
    targeted_closure_path = args.targeted_closure_receipt.resolve()
    alternate_index = args.alternate_index_file.resolve()
    manifest = _json(manifest_path)
    executable = _json(executable_path)
    repository_fingerprints = _json(repository_path)
    source_impact = _json(source_impact_path)
    semantic = _json(semantic_path)
    targeted_closure = _json(targeted_closure_path)
    targeted_closure_status = str(targeted_closure.get("status") or "")
    deferred_publication = dict(targeted_closure.get("deferred_publication") or {})
    scoped_publication_deferral = (
        targeted_closure_status
        == "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED"
    )
    semantic_publication = dict(semantic.get("deferred_publication") or {})
    semantic_scoped_deferral = (
        semantic.get("status")
        == "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED"
    )
    environment = dict(os.environ)
    environment["GIT_INDEX_FILE"] = str(alternate_index)
    alternate_tree = subprocess.run(
        ["git", "-C", str(repository), "write-tree"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    repository_entries = {
        str(row["path"]): row for row in repository_fingerprints["entries"]
    }
    semantic_current = {
        str(row["path"]): row
        for row in semantic["entries"]
        if row["media_class"] != "PURGED_HEAD_MEMBER"
    }
    semantic_purged = {
        str(row["path"]): row
        for row in semantic["entries"]
        if row["semantic_status"] == "STALE_PURGED"
    }
    fingerprint_self = ".github/evidence-lane-repository-fingerprints.v1.json"
    path_sets_equal = set(repository_entries) == set(semantic_current) - {
        fingerprint_self
    }
    byte_semantic_join = path_sets_equal and all(
        semantic_current[path]["sha256"] == row["staged_sha256"]
        and int(semantic_current[path]["bytes"]) == int(row["staged_bytes"])
        for path, row in repository_entries.items()
    )
    executable_join = all(
        (
            path := f"plugins/evidence-lane-plugin/{row['path']}"
        ) in semantic_current
        and semantic_current[path]["sha256"] == row["sha256"]
        and int(semantic_current[path]["bytes"]) == int(row["bytes"])
        for row in executable["members"]
    )

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
        "semantic_currentness": (
            semantic.get("status")
            in {"PASS", "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED"}
            and int(semantic.get("stale_path_count", -1)) == 0
            and semantic.get("every_index_blob_content_inspected") is True
            and semantic.get("unchanged_blob_skip_allowed") is False
            and semantic.get("alternate_tree") == alternate_tree
            and (
                not semantic_scoped_deferral
                or (
                    scoped_publication_deferral
                    and semantic_publication.get("status") == "DEFERRED_NOT_PASSED"
                    and semantic_publication.get("publication_authorized") is False
                    and semantic_publication.get("documentation_generation_authorized")
                    is False
                    and semantic_publication.get("contract_file_sha256")
                    == deferred_publication.get("contract_file_sha256")
                )
            )
        ),
        "same_epoch_path_set": path_sets_equal,
        "same_epoch_byte_semantic_join": byte_semantic_join,
        "executable_subset_join": executable_join,
        "targeted_closure_receipt": (
            targeted_closure_status in REGRESSION_PASS_STATUSES
            and (targeted_closure.get("full_regression") or {}).get(
                "full_suite_rerun"
            )
            is False
            and int(
                ((targeted_closure.get("targeted_closure") or {}).get("counts") or {}).get(
                    "failures", -1
                )
            )
            == 0
            and (
                not scoped_publication_deferral
                or (
                    targeted_closure.get("publication_authorized") is False
                    and deferred_publication.get("status") == "DEFERRED_NOT_PASSED"
                    and deferred_publication.get("publication_authorized") is False
                    and deferred_publication.get("documentation_generation_authorized")
                    is False
                    and int(deferred_publication.get("selector_count", 0)) > 0
                )
            )
        ),
        "single_full_regression": args.full_passed > 0 and args.full_failed >= 0,
        "targeted_closure": args.targeted_passed > 0,
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
            "status": targeted_closure_status,
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
            "receipt_sha256": targeted_closure["receipt_sha256"],
            "file_sha256": _sha256(targeted_closure_path),
        },
        "publication_scope": {
            "status": (
                "DEFERRED_NOT_PASSED"
                if scoped_publication_deferral
                else "NOT_DEFERRED_BY_REGRESSION_CLOSURE"
            ),
            "publication_authorized": False if scoped_publication_deferral else None,
            "deferred_selector_count": (
                int(deferred_publication.get("selector_count", 0))
                if scoped_publication_deferral
                else 0
            ),
            "deferral_contract_file_sha256": (
                deferred_publication.get("contract_file_sha256")
                if scoped_publication_deferral
                else None
            ),
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
            "selection": repository_fingerprints["selection"],
            "pre_self_reference_tree": repository_fingerprints["source_tree_sha"],
        },
        "semantic_currentness": {
            "status": semantic["status"],
            "alternate_tree": alternate_tree,
            "current_index_path_count": semantic["current_index_path_count"],
            "purged_head_path_count": semantic["purged_head_path_count"],
            "classification_counts": semantic["classification_counts"],
            "path_set_sha256": semantic["path_set_sha256"],
            "semantic_receipt_set_sha256": semantic[
                "semantic_receipt_set_sha256"
            ],
            "receipt_sha256": semantic["receipt_sha256"],
            "file_sha256": _sha256(semantic_path),
            "fingerprint_self_reference_exclusion": fingerprint_self,
            "repository_and_semantic_path_sets_equal_after_self_exclusion": (
                path_sets_equal
            ),
            "every_repository_blob_joined_to_semantic_receipt": byte_semantic_join,
            "executable_members_joined_to_same_epoch_blobs": executable_join,
            "purged_paths": sorted(semantic_purged),
            "publication_authorized": (
                semantic_publication.get("publication_authorized")
                if semantic_scoped_deferral
                else None
            ),
            "deferred_publication_path_count": (
                int(semantic_publication.get("deferred_path_count", 0))
                if semantic_scoped_deferral
                else 0
            ),
        },
        "line_ending_normalization": {
            "policy": "EXACT_GIT_INDEX_BLOB_BYTES_AFTER_GIT_ATTRIBUTES_NORMALIZATION",
            "worktree_bytes_used": False,
            "repository_fingerprint_and_semantic_bytes_identical": (
                byte_semantic_join
            ),
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
            "unchanged_member_semantics_skipped": False,
        },
    }
    body = {**core, "receipt_sha256": hashlib.sha256(_canonical(core)).hexdigest().upper()}
    output = args.output.resolve()
    _write_atomic(output, _canonical(body))
    print(json.dumps({"status": "PASS", "output": str(output), "receipt_sha256": body["receipt_sha256"], "file_sha256": _sha256(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
