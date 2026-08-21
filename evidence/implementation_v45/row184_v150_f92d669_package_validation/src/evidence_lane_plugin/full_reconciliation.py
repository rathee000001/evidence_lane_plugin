"""Read-only reconciliation of all sources, all lanes, and sealed Git history."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .errors import require
from .forensic_audit import audit_lane_bundle
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY

RECONCILIATION_SCHEMA = "evidence-lane.full-reconciliation.v1"
SOURCE_MATRIX_SCHEMA = "evidence-lane.source-lane-matrix.v1"
FULL_HISTORY_SCHEMA = "evidence-lane.full-git-history-reconciliation.v1"

_EXPECTED_DELTA_RECEIPTS = (
    "DELTA067A_ARCHIVE_INTAKE_RECEIPT.json",
    "DELTA067B_SQLITE_FORENSIC_INTAKE_RECEIPT.json",
    "DELTA068_CUSTOM_SOURCE_SCHEMA_RECEIPT.json",
    "DELTA069_SOURCE_IDENTITY_MATRIX_RECEIPT.json",
    "DELTA070_MODE_GOVERNANCE_RECEIPT.json",
    "DELTA071_POLYGLOT_GRAPH_RECEIPT.json",
    "DELTA072_GIT_HISTORY_IMPACT_RECEIPT.json",
    "DELTA073_SCHEMA_DERIVED_TOPOLOGY_RECEIPT.json",
    "DELTA074_DETERMINISTIC_RENDER_RECEIPT.json",
    "DELTA075_FOUR_FILE_EVERY_TABLE_FORENSIC_RECEIPT.json",
    "DELTA076A_PINNED_CI_MCP_GH_AW_RECEIPT.json",
    "DELTA076B_CI_ADAPTERS_RECEIPT.json",
    "DELTA077_INTERACTIVE_OPERATOR_GUIDE_RECEIPT.json",
)


def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve()
    connection = sqlite3.connect(
        f"file:{target.as_posix()}?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _pointer_name(pointer: str) -> str:
    cleaned = pointer.rstrip("/\\")
    if "\\" in cleaned:
        return PureWindowsPath(cleaned).name
    return PurePosixPath(cleaned).name


def _rows(
    connection: sqlite3.Connection,
    query: str,
    parameters: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, parameters)]


def _root(rows: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(rows))


def _reported_delta_pass(filename: str, payload: Mapping[str, Any]) -> bool:
    status = str(payload.get("status") or "")
    if status.startswith("PASS"):
        return True
    if payload.get("all_pass") is True:
        return True
    if filename.startswith("DELTA074_"):
        render = payload.get("render_poc")
        stale = payload.get("stale_rejection")
        return bool(
            isinstance(render, Mapping)
            and isinstance(render.get("validation"), Mapping)
            and render["validation"].get("valid") is True
            and isinstance(stale, Mapping)
            and stale.get("test_status") == "PASS"
        )
    if filename.startswith("DELTA075_"):
        audit = payload.get("dummy_audit")
        return bool(
            isinstance(audit, Mapping) and audit.get("audit_status") == "PASS"
        )
    if filename.startswith("DELTA076A_"):
        supplied = payload.get("supplied_authority_validation")
        tests = payload.get("tests")
        return bool(
            isinstance(supplied, Mapping)
            and isinstance(supplied.get("gh_aw_locked_workflows"), Mapping)
            and supplied["gh_aw_locked_workflows"].get("status") == "PASS"
            and isinstance(tests, Mapping)
            and tests.get("ruff") == "PASS"
            and tests.get("mypy") == "PASS"
        )
    if filename.startswith("DELTA076B_"):
        execution = payload.get("ci_execution")
        preview = payload.get("preview_build")
        workflow = payload.get("workflow_pin_audit")
        return bool(
            isinstance(execution, Mapping)
            and execution.get("aggregate_status") == "PASS"
            and isinstance(preview, Mapping)
            and preview.get("status") == "PASS"
            and isinstance(workflow, Mapping)
            and workflow.get("status") == "PASS"
        )
    return False


def reconcile_delta_lineage(receipt_directory: str | Path) -> dict[str, Any]:
    """Verify the exact Delta 067A-077 evidence index without editing it."""

    root = Path(receipt_directory).resolve()
    entries: list[dict[str, Any]] = []
    for filename in _EXPECTED_DELTA_RECEIPTS:
        path = root / filename
        require(
            path.is_file(),
            "FULL_RECONCILIATION_DELTA_RECEIPT_MISSING",
            "The sealed Delta lineage is incomplete.",
            status="MISMATCH",
            filename=filename,
        )
        payload = _read_json(path)
        declared_receipt_sha256 = payload.get("receipt_sha256")
        self_seal_valid: bool | None = None
        if declared_receipt_sha256 is not None:
            core = dict(payload)
            core.pop("receipt_sha256")
            self_seal_valid = str(declared_receipt_sha256) == sha256_bytes(
                canonical_json_bytes(core)
            )
            require(
                self_seal_valid,
                "FULL_RECONCILIATION_DELTA_SELF_SEAL_INVALID",
                "A self-sealed Delta receipt does not match its canonical body.",
                status="MISMATCH",
                filename=filename,
            )
        reported_pass = _reported_delta_pass(filename, payload)
        require(
            reported_pass,
            "FULL_RECONCILIATION_DELTA_NOT_PASSING",
            "A required Delta receipt does not report a passing boundary.",
            status="MISMATCH",
            filename=filename,
        )
        entries.append(
            {
                "filename": filename,
                "schema": payload.get("schema"),
                "delta": payload.get("delta") or payload.get("delta_id"),
                "file_sha256": sha256_file(path),
                "declared_receipt_sha256": declared_receipt_sha256,
                "self_seal_valid": self_seal_valid,
                "reported_pass": reported_pass,
            }
        )
    return {
        "expected_receipt_count": len(_EXPECTED_DELTA_RECEIPTS),
        "receipt_count": len(entries),
        "entries": entries,
        "lineage_root_sha256": _root(entries),
        "status": "PASS",
    }


def reconcile_source_matrix(
    connection: sqlite3.Connection,
    crosswalk: Mapping[str, Any],
    *,
    expected_source_count: int = 48,
) -> dict[str, Any]:
    """Account every source against every canonical lane with explicit states."""

    batches = _rows(
        connection,
        "SELECT * FROM intake_batch ORDER BY created_at, batch_id",
    )
    require(
        bool(batches),
        "FULL_RECONCILIATION_BATCH_MISSING",
        "No source authority batch is registered.",
        status="MISMATCH",
    )
    batch = batches[-1]
    batch_id = str(batch["batch_id"])
    require(
        int(batch["source_count"]) == expected_source_count,
        "FULL_RECONCILIATION_SOURCE_COUNT_MISMATCH",
        "The source registry does not contain the expected source count.",
        status="MISMATCH",
        expected=expected_source_count,
        actual=batch["source_count"],
    )
    crosswalk_sources = crosswalk.get("sources")
    require(
        isinstance(crosswalk_sources, list)
        and len(crosswalk_sources) == expected_source_count
        and crosswalk.get("expected_source_count") == expected_source_count,
        "FULL_RECONCILIATION_CROSSWALK_COUNT_MISMATCH",
        "The all-source crosswalk does not match the registered source count.",
        status="MISMATCH",
    )
    assert isinstance(crosswalk_sources, list)
    crosswalk_by_ordinal = {
        int(row["ordinal"]): row
        for row in crosswalk_sources
        if isinstance(row, Mapping) and "ordinal" in row
    }
    require(
        sorted(crosswalk_by_ordinal) == list(range(1, expected_source_count + 1)),
        "FULL_RECONCILIATION_CROSSWALK_ORDINAL_GAP",
        "The crosswalk must preserve the exact contiguous source order.",
        status="MISMATCH",
    )
    occurrences = _rows(
        connection,
        """SELECT o.ordinal, o.object_id, o.supplied_pointer, o.lane_id,
        s.kind, s.identity_sha256, s.byte_sha256, s.size_bytes,
        s.member_count, s.included_member_count, s.excluded_member_count,
        p.receipt_sha256 AS policy_receipt_sha256,
        (SELECT COUNT(*) FROM source_provenance v
         WHERE v.batch_id=o.batch_id AND v.object_id=o.object_id) AS provenance_count
        FROM source_occurrence o
        JOIN source_object s ON s.object_id=o.object_id
        LEFT JOIN source_policy_receipt p ON p.object_id=o.object_id
        WHERE o.batch_id=? ORDER BY o.ordinal""",
        (batch_id,),
    )
    require(
        len(occurrences) == expected_source_count
        and [int(row["ordinal"]) for row in occurrences]
        == list(range(1, expected_source_count + 1)),
        "FULL_RECONCILIATION_OCCURRENCE_GAP",
        "Registered source occurrences are incomplete or out of order.",
        status="MISMATCH",
    )

    extension_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in connection.execute(
        """SELECT object_id, member_path FROM source_member
        WHERE policy_state='INCLUDED' ORDER BY object_id, member_path"""
    ):
        suffix = PurePosixPath(str(row["member_path"])).suffix.casefold()
        if suffix:
            extension_counts[str(row["object_id"])][suffix] += 1

    matrix: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    lane_aggregates: dict[str, dict[str, int]] = {
        lane_id: {
            "primary_source_count": 0,
            "compatible_source_count": 0,
            "member_extension_match_count": 0,
        }
        for lane_id in CANONICAL_LANE_IDS
    }
    for occurrence in occurrences:
        ordinal = int(occurrence["ordinal"])
        crosswalk_row = crosswalk_by_ordinal[ordinal]
        source_name = _pointer_name(str(occurrence["supplied_pointer"]))
        require(
            source_name == str(crosswalk_row.get("name")),
            "FULL_RECONCILIATION_SOURCE_NAME_MISMATCH",
            "A crosswalk source name does not match its registered pointer.",
            status="MISMATCH",
            ordinal=ordinal,
            crosswalk_name=crosswalk_row.get("name"),
            registered_name=source_name,
        )
        for field in (
            "reason_for_presence",
            "planned_use",
            "rejected_use",
            "license_state",
        ):
            require(
                bool(str(crosswalk_row.get(field) or "").strip()),
                "FULL_RECONCILIATION_CROSSWALK_FIELD_MISSING",
                "Each source requires an explicit use and rejection boundary.",
                status="MISMATCH",
                ordinal=ordinal,
                field=field,
            )
        require(
            occurrence["policy_receipt_sha256"] is not None
            and int(occurrence["provenance_count"]) > 0,
            "FULL_RECONCILIATION_SOURCE_GOVERNANCE_MISSING",
            "Every source requires policy and provenance evidence.",
            status="MISMATCH",
            ordinal=ordinal,
        )
        primary_lane = str(occurrence["lane_id"])
        require(
            primary_lane in LANE_REGISTRY,
            "FULL_RECONCILIATION_PRIMARY_LANE_INVALID",
            "A source occurrence resolves to an unknown lane.",
            status="MISMATCH",
            ordinal=ordinal,
            lane_id=primary_lane,
        )
        matches_by_lane: dict[str, int] = {}
        state_counts: Counter[str] = Counter()
        compatible_lanes: list[str] = []
        object_extensions = extension_counts[str(occurrence["object_id"])].copy()
        if occurrence["kind"] in {"file", "zip"}:
            source_suffix = PureWindowsPath(
                str(occurrence["supplied_pointer"])
            ).suffix.casefold()
            if source_suffix:
                object_extensions[source_suffix] += 1
        for lane_id in CANONICAL_LANE_IDS:
            lane = LANE_REGISTRY[lane_id]
            match_count = sum(
                object_extensions.get(extension.casefold(), 0)
                for extension in lane.extensions
            )
            primary = lane_id == primary_lane
            if primary:
                state = "PRIMARY_ROUTE"
            elif match_count:
                state = "COMPATIBLE_MEMBER_EVIDENCE"
            else:
                state = "ACCOUNTED_CONTEXT_ONLY"
            if primary or match_count:
                compatible_lanes.append(lane_id)
                lane_aggregates[lane_id]["compatible_source_count"] += 1
            if primary:
                lane_aggregates[lane_id]["primary_source_count"] += 1
            lane_aggregates[lane_id]["member_extension_match_count"] += match_count
            matches_by_lane[lane_id] = match_count
            state_counts[state] += 1
            matrix.append(
                {
                    "ordinal": ordinal,
                    "object_id": occurrence["object_id"],
                    "source_name": source_name,
                    "lane_id": lane_id,
                    "state": state,
                    "primary_route": primary,
                    "member_extension_match_count": match_count,
                    "reuse_boundary": (
                        "CROSSWALK_PLANNED_USE_AND_REJECTED_USE_APPLY; "
                        "NO_IMPLICIT_COPY_EXECUTION_OR_PROMOTION"
                    ),
                }
            )
        source_summaries.append(
            {
                "ordinal": ordinal,
                "name": source_name,
                "object_id": occurrence["object_id"],
                "kind": occurrence["kind"],
                "identity_sha256": occurrence["identity_sha256"],
                "primary_lane": primary_lane,
                "member_count": int(occurrence["member_count"]),
                "included_member_count": int(occurrence["included_member_count"]),
                "excluded_member_count": int(occurrence["excluded_member_count"]),
                "provenance_count": int(occurrence["provenance_count"]),
                "reason_for_presence": crosswalk_row["reason_for_presence"],
                "planned_use": crosswalk_row["planned_use"],
                "rejected_use": crosswalk_row["rejected_use"],
                "license_state": crosswalk_row["license_state"],
                "compatible_lanes": compatible_lanes,
                "member_extension_matches": matches_by_lane,
                "matrix_state_counts": dict(sorted(state_counts.items())),
            }
        )
    expected_cells = expected_source_count * len(CANONICAL_LANE_IDS)
    require(
        len(matrix) == expected_cells,
        "FULL_RECONCILIATION_MATRIX_INCOMPLETE",
        "The source-by-lane matrix is incomplete.",
        status="MISMATCH",
        expected=expected_cells,
        actual=len(matrix),
    )
    matrix_body = {
        "schema": SOURCE_MATRIX_SCHEMA,
        "batch_id": batch_id,
        "batch_sha256": batch["batch_sha256"],
        "source_count": expected_source_count,
        "lane_count": len(CANONICAL_LANE_IDS),
        "cell_count": len(matrix),
        "states": [
            "PRIMARY_ROUTE",
            "COMPATIBLE_MEMBER_EVIDENCE",
            "ACCOUNTED_CONTEXT_ONLY",
        ],
        "cells": matrix,
    }
    matrix_body["matrix_sha256"] = sha256_bytes(canonical_json_bytes(matrix_body))
    return {
        "batch_id": batch_id,
        "batch_sha256": batch["batch_sha256"],
        "source_count": expected_source_count,
        "lane_count": len(CANONICAL_LANE_IDS),
        "matrix_cell_count": len(matrix),
        "source_summaries": source_summaries,
        "lane_source_aggregates": [
            {"lane_id": lane_id, **lane_aggregates[lane_id]}
            for lane_id in CANONICAL_LANE_IDS
        ],
        "source_summary_root_sha256": _root(source_summaries),
        "matrix": matrix_body,
        "status": "PASS",
    }


def _git_root_rows(
    connection: sqlite3.Connection,
    snapshot_id: str,
) -> dict[str, list[dict[str, Any]]]:
    commits = _rows(
        connection,
        """SELECT commit_sha, ordinal, tree_sha, parent_count, commit_sha256
        FROM source_git_commit WHERE snapshot_id=? ORDER BY ordinal""",
        (snapshot_id,),
    )
    parents = _rows(
        connection,
        """SELECT p.commit_sha, p.parent_ordinal, p.parent_sha,
        p.parent_edge_sha256 FROM source_git_parent p
        JOIN source_git_commit c ON c.snapshot_id=p.snapshot_id
        AND c.commit_sha=p.commit_sha WHERE p.snapshot_id=?
        ORDER BY c.ordinal, p.parent_ordinal""",
        (snapshot_id,),
    )
    return {
        "refs": _rows(
            connection,
            """SELECT ref_name, object_sha, object_type, peeled_sha,
            peeled_type, ref_sha256 FROM source_git_ref
            WHERE snapshot_id=? ORDER BY ref_name""",
            (snapshot_id,),
        ),
        "commits": commits + parents,
        "objects": _rows(
            connection,
            """SELECT object_sha, object_type, size_bytes, content_sha256,
            object_sha256 FROM source_git_object WHERE snapshot_id=?
            ORDER BY object_sha""",
            (snapshot_id,),
        ),
        "trees": _rows(
            connection,
            """SELECT t.commit_sha, t.entry_ordinal, t.member_path,
            t.entry_sha256 FROM source_git_tree_entry t
            JOIN source_git_commit c ON c.snapshot_id=t.snapshot_id
            AND c.commit_sha=t.commit_sha WHERE t.snapshot_id=?
            ORDER BY c.ordinal, t.entry_ordinal""",
            (snapshot_id,),
        ),
        "changes": _rows(
            connection,
            """SELECT f.commit_sha, f.parent_ordinal, f.change_ordinal,
            f.change_sha256 FROM source_git_file_change f
            JOIN source_git_commit c ON c.snapshot_id=f.snapshot_id
            AND c.commit_sha=f.commit_sha WHERE f.snapshot_id=?
            ORDER BY c.ordinal, f.parent_ordinal, f.change_ordinal""",
            (snapshot_id,),
        ),
        "hunks": _rows(
            connection,
            """SELECT h.commit_sha, h.parent_ordinal, h.change_ordinal,
            h.hunk_ordinal, h.hunk_sha256 FROM source_git_hunk h
            JOIN source_git_commit c ON c.snapshot_id=h.snapshot_id
            AND c.commit_sha=h.commit_sha WHERE h.snapshot_id=?
            ORDER BY c.ordinal, h.parent_ordinal, h.change_ordinal,
            h.hunk_ordinal""",
            (snapshot_id,),
        ),
        "lines": _rows(
            connection,
            """SELECT l.commit_sha, l.parent_ordinal, l.change_ordinal,
            l.hunk_ordinal, l.line_ordinal, l.line_sha256
            FROM source_git_changed_line l JOIN source_git_commit c
            ON c.snapshot_id=l.snapshot_id AND c.commit_sha=l.commit_sha
            WHERE l.snapshot_id=? ORDER BY c.ordinal, l.parent_ordinal,
            l.change_ordinal, l.hunk_ordinal, l.line_ordinal""",
            (snapshot_id,),
        ),
    }


def reconcile_git_history(connection: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild every stored history root and prove relational closure."""

    snapshots = _rows(
        connection,
        "SELECT * FROM source_git_snapshot ORDER BY created_at, snapshot_id",
    )
    require(
        bool(snapshots),
        "FULL_RECONCILIATION_GIT_SNAPSHOT_MISSING",
        "No full-history snapshot is registered.",
        status="MISMATCH",
    )
    snapshot_reports: list[dict[str, Any]] = []
    full_commit_count = 0
    for snapshot in snapshots:
        snapshot_id = str(snapshot["snapshot_id"])
        roots_rows = _git_root_rows(connection, snapshot_id)
        roots = {name: _root(rows) for name, rows in roots_rows.items()}
        declared_roots = {
            "refs": snapshot["refs_root_sha256"],
            "commits": snapshot["commits_root_sha256"],
            "objects": snapshot["objects_root_sha256"],
            "trees": snapshot["trees_root_sha256"],
            "changes": snapshot["changes_root_sha256"],
            "hunks": snapshot["hunks_root_sha256"],
            "lines": snapshot["lines_root_sha256"],
        }
        require(
            roots == declared_roots,
            "FULL_RECONCILIATION_GIT_ROOT_MISMATCH",
            "Stored full-history rows do not reproduce the sealed roots.",
            status="MISMATCH",
            snapshot_id=snapshot_id,
            declared=declared_roots,
            actual=roots,
        )
        batch = connection.execute(
            "SELECT batch_sha256 FROM intake_batch WHERE batch_id=?",
            (snapshot["batch_id"],),
        ).fetchone()
        occurrence = connection.execute(
            """SELECT o.object_id, s.identity_sha256 FROM source_occurrence o
            JOIN source_object s ON s.object_id=o.object_id
            WHERE o.batch_id=? AND o.ordinal=?""",
            (snapshot["batch_id"], snapshot["occurrence_ordinal"]),
        ).fetchone()
        require(
            batch is not None and occurrence is not None,
            "FULL_RECONCILIATION_GIT_SOURCE_MISSING",
            "The history snapshot lost its source authority binding.",
            status="MISMATCH",
            snapshot_id=snapshot_id,
        )
        repository_identity = {
            "batch_id": snapshot["batch_id"],
            "batch_sha256": batch["batch_sha256"],
            "occurrence_ordinal": snapshot["occurrence_ordinal"],
            "object_id": occurrence["object_id"],
            "source_identity_sha256": occurrence["identity_sha256"],
            "head_commit_sha": snapshot["head_commit_sha"],
            "head_tree_sha": snapshot["head_tree_sha"],
            "branch": snapshot["branch"],
        }
        repository_identity_sha256 = sha256_bytes(
            canonical_json_bytes(repository_identity)
        )
        history_signature_sha256 = sha256_bytes(
            canonical_json_bytes(
                {
                    "refs_root_sha256": roots["refs"],
                    "commits_root_sha256": roots["commits"],
                    "objects_root_sha256": roots["objects"],
                }
            )
        )
        configuration = json.loads(str(snapshot["configuration_json"]))
        history_root_sha256 = sha256_bytes(
            canonical_json_bytes(
                {
                    "repository_identity_sha256": repository_identity_sha256,
                    "history_signature_sha256": history_signature_sha256,
                    "worktree_status_sha256": snapshot["worktree_status_sha256"],
                    "configuration": configuration,
                    "roots": roots,
                }
            )
        )
        require(
            repository_identity_sha256 == snapshot["repository_identity_sha256"]
            and history_signature_sha256 == snapshot["history_signature_sha256"]
            and history_root_sha256 == snapshot["history_root_sha256"],
            "FULL_RECONCILIATION_GIT_SEAL_MISMATCH",
            "The full-history identity or aggregate seal is invalid.",
            status="MISMATCH",
            snapshot_id=snapshot_id,
        )
        commits = _rows(
            connection,
            """SELECT * FROM source_git_commit WHERE snapshot_id=?
            ORDER BY ordinal""",
            (snapshot_id,),
        )
        parents = _rows(
            connection,
            """SELECT p.* FROM source_git_parent p JOIN source_git_commit c
            ON c.snapshot_id=p.snapshot_id AND c.commit_sha=p.commit_sha
            WHERE p.snapshot_id=? ORDER BY c.ordinal, p.parent_ordinal""",
            (snapshot_id,),
        )
        commit_ids = {str(row["commit_sha"]) for row in commits}
        require(
            [int(row["ordinal"]) for row in commits] == list(range(len(commits)))
            and str(snapshot["head_commit_sha"]) in commit_ids
            and all(str(row["parent_sha"]) in commit_ids for row in parents),
            "FULL_RECONCILIATION_GIT_COMMIT_CLOSURE_INVALID",
            "Commit ordinals, HEAD, or parent closure are invalid.",
            status="MISMATCH",
            snapshot_id=snapshot_id,
        )
        parent_counts = Counter(str(row["commit_sha"]) for row in parents)
        require(
            all(
                int(row["parent_count"])
                == parent_counts.get(str(row["commit_sha"]), 0)
                for row in commits
            ),
            "FULL_RECONCILIATION_GIT_PARENT_COUNT_MISMATCH",
            "A commit parent count disagrees with the sealed parent rows.",
            status="MISMATCH",
            snapshot_id=snapshot_id,
        )
        receipt = json.loads(str(snapshot["receipt_json"]))
        require(
            receipt.get("full_reachable_history") is True
            and receipt.get("changed_line_storage")
            == "SHA256_SIZE_AND_COORDINATES_ONLY"
            and receipt.get("source_bytes_mutated") is False
            and receipt.get("git_repository_mutated") is False,
            "FULL_RECONCILIATION_GIT_RECEIPT_BOUNDARY_INVALID",
            "The Git snapshot does not preserve the full-history read-only boundary.",
            status="MISMATCH",
            snapshot_id=snapshot_id,
        )
        table_counts = {
            "refs": len(roots_rows["refs"]),
            "commits": len(commits),
            "parent_edges": len(parents),
            "objects": len(roots_rows["objects"]),
            "tree_entries": len(roots_rows["trees"]),
            "file_changes": len(roots_rows["changes"]),
            "hunks": len(roots_rows["hunks"]),
            "changed_lines": len(roots_rows["lines"]),
        }
        declared_counts = {
            "refs": int(snapshot["ref_count"]),
            "commits": int(snapshot["commit_count"]),
            "parent_edges": int(snapshot["parent_edge_count"]),
            "objects": int(snapshot["object_count"]),
            "tree_entries": int(snapshot["tree_entry_count"]),
            "file_changes": int(snapshot["file_change_count"]),
            "hunks": int(snapshot["hunk_count"]),
            "changed_lines": int(snapshot["changed_line_count"]),
        }
        require(
            table_counts == declared_counts,
            "FULL_RECONCILIATION_GIT_COUNT_MISMATCH",
            "Full-history table counts disagree with the sealed snapshot.",
            status="MISMATCH",
            snapshot_id=snapshot_id,
            declared=declared_counts,
            actual=table_counts,
        )
        change_counts = Counter(
            str(row["commit_sha"]) for row in roots_rows["changes"]
        )
        hunk_counts = Counter(str(row["commit_sha"]) for row in roots_rows["hunks"])
        line_counts = Counter(str(row["commit_sha"]) for row in roots_rows["lines"])
        commit_summaries = [
            {
                "ordinal": int(row["ordinal"]),
                "commit_sha": row["commit_sha"],
                "tree_sha": row["tree_sha"],
                "authored_at": row["authored_at"],
                "committed_at": row["committed_at"],
                "message_redacted": row["message_redacted"],
                "message_sha256": row["message_sha256"],
                "parent_count": int(row["parent_count"]),
                "file_change_count": change_counts.get(str(row["commit_sha"]), 0),
                "hunk_count": hunk_counts.get(str(row["commit_sha"]), 0),
                "changed_line_count": line_counts.get(str(row["commit_sha"]), 0),
            }
            for row in commits
        ]
        refs = _rows(
            connection,
            """SELECT ref_name, object_sha, object_type, peeled_sha,
            peeled_type, ref_sha256 FROM source_git_ref
            WHERE snapshot_id=? ORDER BY ref_name""",
            (snapshot_id,),
        )
        impacts = _rows(
            connection,
            """SELECT impact_id, graph_id, commit_sha, parent_ordinal,
            mapped_path_count, unmapped_path_count, projection_sha256,
            receipt_sha256 FROM source_git_impact WHERE snapshot_id=?
            ORDER BY impact_id""",
            (snapshot_id,),
        )
        snapshot_reports.append(
            {
                "snapshot_id": snapshot_id,
                "status": snapshot["status"],
                "branch": snapshot["branch"],
                "head_commit_sha": snapshot["head_commit_sha"],
                "head_tree_sha": snapshot["head_tree_sha"],
                "full_reachable_history": True,
                "counts": table_counts,
                "roots": roots,
                "history_root_sha256": history_root_sha256,
                "commits": commit_summaries,
                "refs": refs,
                "impacts": impacts,
                "graph_boundary": (
                    "Git history is complete. Graph impact remains bound to the "
                    "explicit registered graph selection and is not a claim of a "
                    "full-worktree semantic graph."
                ),
            }
        )
        full_commit_count += len(commits)
    history_body = {
        "schema": FULL_HISTORY_SCHEMA,
        "status": "PASS_WITH_EXPLICIT_GRAPH_BOUNDARY",
        "snapshot_count": len(snapshot_reports),
        "commit_count": full_commit_count,
        "snapshots": snapshot_reports,
    }
    history_body["history_reconciliation_sha256"] = sha256_bytes(
        canonical_json_bytes(history_body)
    )
    return history_body


def _lane_reconciliation(
    lane_bundle: str | Path,
    source_reconciliation: Mapping[str, Any],
) -> dict[str, Any]:
    audit = audit_lane_bundle(
        lane_bundle,
        subject="Delta 078 all-source, all-lane, full-history reconciliation",
    )
    require(
        audit.get("status") == "PASS"
        and int(audit.get("lane_count") or 0) == len(CANONICAL_LANE_IDS),
        "FULL_RECONCILIATION_LANE_AUDIT_FAILED",
        "The 18-lane proof bundle does not pass its forensic audit.",
        status="MISMATCH",
        audit_status=audit.get("status"),
        lane_count=audit.get("lane_count"),
    )
    source_by_lane = {
        str(row["lane_id"]): row
        for row in source_reconciliation["lane_source_aggregates"]
    }
    lane_summaries: list[dict[str, Any]] = []
    for row in audit["lanes"]:
        lane_id = str(row["lane_id"])
        lane_summary = {
            "lane_id": lane_id,
            "status": row["status"],
            "audit_sha256": row["audit_sha256"],
            "four_file_contract_valid": row["four_file_contract"]["valid"],
            "sqlite_every_table_audited": row["sqlite"]["every_table_audited"],
            "sqlite_table_count": row["sqlite"]["table_count"],
            "unexpected_public_table_count": len(
                row["sqlite"]["unexpected_public_tables"]
            ),
            "topology_status": row["topology_reconciliation"]["status"],
            **source_by_lane[lane_id],
        }
        require(
            lane_summary["status"] == "PASS"
            and lane_summary["four_file_contract_valid"] is True
            and lane_summary["sqlite_every_table_audited"] is True
            and lane_summary["unexpected_public_table_count"] == 0
            and lane_summary["topology_status"] == "PASS",
            "FULL_RECONCILIATION_LANE_INCOMPLETE",
            "A lane failed its four-file, every-table, or topology boundary.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        lane_summaries.append(lane_summary)
    primary_route_lane_count = sum(
        int(row["primary_source_count"]) > 0 for row in lane_summaries
    )
    compatible_evidence_lane_count = sum(
        int(row["compatible_source_count"]) > 0 for row in lane_summaries
    )
    require(
        compatible_evidence_lane_count == len(CANONICAL_LANE_IDS),
        "FULL_RECONCILIATION_LANE_SOURCE_COVERAGE_INCOMPLETE",
        "At least one lane has no registered compatible source evidence.",
        status="MISMATCH",
        compatible_evidence_lane_count=compatible_evidence_lane_count,
    )
    return {
        "lane_count": len(lane_summaries),
        "bundle_sha256": audit["bundle_validation"]["bundle_sha256"],
        "bundle_audit_sha256": audit["audit_sha256"],
        "four_file_pass_count": sum(
            row["four_file_contract_valid"] is True for row in lane_summaries
        ),
        "every_table_pass_count": sum(
            row["sqlite_every_table_audited"] is True for row in lane_summaries
        ),
        "primary_route_lane_count": primary_route_lane_count,
        "compatible_evidence_lane_count": compatible_evidence_lane_count,
        "lanes_without_primary_route": [
            row["lane_id"]
            for row in lane_summaries
            if int(row["primary_source_count"]) == 0
        ],
        "routing_boundary": (
            "The immutable source batch preserves one primary route per source. "
            "Extension-backed compatible evidence covers all 18 lanes without "
            "rewriting historical primary routes."
        ),
        "lanes": lane_summaries,
        "lane_summary_root_sha256": _root(lane_summaries),
        "status": "PASS",
    }


def build_full_reconciliation(
    registry_path: str | Path,
    crosswalk_path: str | Path,
    lane_bundle: str | Path,
    receipt_directory: str | Path,
    *,
    expected_source_count: int = 48,
) -> dict[str, Any]:
    """Build a deterministic read-only reconciliation report in memory."""

    registry = Path(registry_path).resolve()
    crosswalk_file = Path(crosswalk_path).resolve()
    crosswalk = _read_json(crosswalk_file)
    with _connect_read_only(registry) as connection:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_key_errors = [
            tuple(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        require(
            integrity == ["ok"] and not foreign_key_errors,
            "FULL_RECONCILIATION_REGISTRY_INVALID",
            "The source authority registry failed SQLite integrity checks.",
            status="MISMATCH",
            integrity=integrity,
            foreign_key_error_count=len(foreign_key_errors),
        )
        sources = reconcile_source_matrix(
            connection,
            crosswalk,
            expected_source_count=expected_source_count,
        )
        history = reconcile_git_history(connection)
    lanes = _lane_reconciliation(lane_bundle, sources)
    lineage = reconcile_delta_lineage(receipt_directory)
    summary = {
        "schema": RECONCILIATION_SCHEMA,
        "status": "PASS_WITH_EXPLICIT_GRAPH_BOUNDARY",
        "verdict": "PURSUE",
        "confidence_percent": 99,
        "evidence_that_would_change_verdict": (
            "Any source/crosswalk mismatch, missing lane artifact or table, failed "
            "history root, broken foreign key, or a later full-worktree graph result "
            "that contradicts the bounded graph evidence."
        ),
        "inputs": {
            "registry_path": str(registry),
            "registry_file_sha256": sha256_file(registry),
            "crosswalk_path": str(crosswalk_file),
            "crosswalk_file_sha256": sha256_file(crosswalk_file),
            "lane_bundle": str(Path(lane_bundle).resolve()),
            "receipt_directory": str(Path(receipt_directory).resolve()),
        },
        "sqlite": {
            "integrity": integrity,
            "foreign_key_error_count": len(foreign_key_errors),
        },
        "sources": {
            key: value
            for key, value in sources.items()
            if key != "matrix"
        },
        "lanes": lanes,
        "history": {
            "schema": history["schema"],
            "status": history["status"],
            "snapshot_count": history["snapshot_count"],
            "commit_count": history["commit_count"],
            "history_reconciliation_sha256": history[
                "history_reconciliation_sha256"
            ],
            "graph_boundary": (
                "Complete reachable Git history is proven independently of the "
                "explicitly bounded semantic graph selection."
            ),
        },
        "delta_lineage": lineage,
        "governance": {
            "source_bytes_mutated": False,
            "source_payloads_copied": False,
            "source_code_executed": False,
            "registry_mutated": False,
            "candidate_created": False,
            "pointer_moved": False,
            "refresh_or_fuse": False,
            "main_merged": False,
            "deployed": False,
        },
    }
    return {"summary": summary, "matrix": sources["matrix"], "history": history}


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    sources = summary["sources"]
    lanes = summary["lanes"]
    history = report["history"]
    source_rows = "\n".join(
        "| {ordinal} | {name} | {kind} | {primary_lane} | {compatible} | {planned} | {rejected} |".format(
            ordinal=row["ordinal"],
            name=str(row["name"]).replace("|", "\\|"),
            kind=row["kind"],
            primary_lane=row["primary_lane"],
            compatible=len(row["compatible_lanes"]),
            planned=str(row["planned_use"]).replace("|", "\\|"),
            rejected=str(row["rejected_use"]).replace("|", "\\|"),
        )
        for row in sources["source_summaries"]
    )
    lane_rows = "\n".join(
        "| {lane_id} | {status} | {four} | {tables} | {primary} | {compatible} |".format(
            lane_id=row["lane_id"],
            status=row["status"],
            four="PASS" if row["four_file_contract_valid"] else "FAIL",
            tables="PASS" if row["sqlite_every_table_audited"] else "FAIL",
            primary=row["primary_source_count"],
            compatible=row["compatible_source_count"],
        )
        for row in lanes["lanes"]
    )
    history_rows = "\n".join(
        "| {ordinal} | `{commit}` | {parents} | {changes} | {hunks} | {lines} | {message} |".format(
            ordinal=row["ordinal"],
            commit=row["commit_sha"],
            parents=row["parent_count"],
            changes=row["file_change_count"],
            hunks=row["hunk_count"],
            lines=row["changed_line_count"],
            message=str(row["message_redacted"]).replace("|", "\\|"),
        )
        for snapshot in history["snapshots"]
        for row in snapshot["commits"]
    )
    return f"""# Delta 078 full reconciliation

- Status: **{summary['status']}**
- Verdict: **{summary['verdict']}** ({summary['confidence_percent']}% confidence)
- Sources: **{sources['source_count']}**
- Canonical lanes: **{lanes['lane_count']}**
- Source-lane cells: **{sources['matrix_cell_count']}**
- Lanes with immutable primary routes: **{lanes['primary_route_lane_count']}**
- Lanes with registered compatible evidence: **{lanes['compatible_evidence_lane_count']}**
- Full-history commits: **{history['commit_count']}**
- Delta receipts reconciled: **{summary['delta_lineage']['receipt_count']}**

The source registry, crosswalk, lane proof bundle, and Git-history rows were read
without source mutation, payload copying, candidate creation, pointer movement,
Refresh/Fuse, deployment, or a merge to `main`.

## Interpretation boundary

Every supplied source is accounted for once as a registered primary route and
against every lane as either primary, compatible member evidence, or explicit
context-only evidence. Compatibility is not permission to copy or execute source.
The original one-primary-route law is preserved; compatible evidence reaches all
18 lanes without rewriting the historical source batch.
Reachable Git history is complete; the semantic graph remains explicitly bounded
and is not misreported as a full-worktree graph.

## All 48 sources

| # | Source | Kind | Primary lane | Compatible lanes | Planned use | Rejected use |
|---:|---|---|---|---:|---|---|
{source_rows}

## All 18 lanes

| Lane | Audit | Four files | Every table | Primary sources | Compatible sources |
|---|---|---|---|---:|---:|
{lane_rows}

## Full reachable Git history

| # | Commit | Parents | Changes | Hunks | Changed lines | Redacted message |
|---:|---|---:|---:|---:|---:|---|
{history_rows}

## Verdict

**PURSUE.** The reconciliation passes with one preserved qualification: semantic
graph coverage is bounded, while the Git-history proof itself is complete.
Evidence that would change the verdict: {summary['evidence_that_would_change_verdict']}
"""


def write_full_reconciliation_reports(
    report: Mapping[str, Any],
    output_directory: str | Path,
) -> dict[str, Any]:
    """Write four deterministic, inspectable Delta 078 report artifacts."""

    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    matrix_path = output / "source-lane-matrix.json"
    history_path = output / "full-history.json"
    markdown_path = output / "full-reconciliation.md"
    summary_path = output / "full-reconciliation.json"
    atomic_write_json(matrix_path, report["matrix"])
    atomic_write_json(history_path, report["history"])
    atomic_write_bytes(markdown_path, (_markdown(report).rstrip() + "\n").encode("utf-8"))
    summary = dict(report["summary"])
    summary["report_files"] = {
        path.name: {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in (matrix_path, history_path, markdown_path)
    }
    summary["reconciliation_sha256"] = sha256_bytes(canonical_json_bytes(summary))
    atomic_write_json(summary_path, summary)
    files = {
        path.name: {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in (summary_path, matrix_path, history_path, markdown_path)
    }
    return {
        "schema": "evidence-lane.full-reconciliation-report-set.v1",
        "status": summary["status"],
        "output_directory": str(output),
        "reconciliation_sha256": summary["reconciliation_sha256"],
        "file_count": len(files),
        "files": files,
        "report_set_sha256": sha256_bytes(canonical_json_bytes(files)),
    }
