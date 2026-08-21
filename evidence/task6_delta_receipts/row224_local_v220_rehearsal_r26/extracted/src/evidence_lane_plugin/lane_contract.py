"""Truthful disposition contract for all eighteen canonical lanes."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .artifact_contract import stable_artifact_names
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY, LaneDefinition

LANE_DISPOSITION_SCHEMA = "evidence-lane.eighteen-lane-disposition.v1"
LANE_DISPOSITIONS = ("PRESERVED", "PARTIAL", "MISSING", "DEFERRED")
LANE_REQUIREMENT_CLASSES = ("REQUIRED", "CONDITIONAL", "OPTIONAL")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected one JSON object: {path.name}")
    return value


def _lane_runtime_truth(lane_root: Path, lane: LaneDefinition) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{(lane_root / lane.sqlite_filename).resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        parser_states = {
            str(row["parser_state"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT parser_state, COUNT(*) AS count
                FROM source_registry GROUP BY parser_state ORDER BY parser_state
                """
            )
        }
        counts = {
            "sources": int(
                connection.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
            ),
            "chunks": int(
                connection.execute("SELECT COUNT(*) FROM chunk_index").fetchone()[0]
            ),
            "chunk_cas": int(
                connection.execute("SELECT COUNT(*) FROM chunk_content_cas").fetchone()[
                    0
                ]
            ),
            "chunk_history": int(
                connection.execute("SELECT COUNT(*) FROM chunk_history").fetchone()[0]
            ),
            "fts": int(
                connection.execute(  # nosec B608 - immutable registry identifier
                    f"SELECT COUNT(*) FROM {lane.fts_table}"
                ).fetchone()[0]
            ),
            "refresh_receipts": int(
                connection.execute("SELECT COUNT(*) FROM refresh_receipt").fetchone()[0]
            ),
        }
    finally:
        connection.close()
    return {"parser_states": parser_states, "counts": counts}


def _emitted_lane_row(
    bundle_root: Path,
    lane: LaneDefinition,
    report: dict[str, Any],
) -> dict[str, Any]:
    lane_root = bundle_root / lane.canonical_lane_id
    tools = _read_json(lane_root / "tools.json")
    lane_manifest = _read_json(lane_root / "lane_manifest.json")
    runtime = _lane_runtime_truth(lane_root, lane)
    capabilities = [dict(row) for row in tools.get("capabilities") or []]
    capability_groups = {
        requirement: [
            row for row in capabilities if row.get("requirement") == requirement
        ]
        for requirement in LANE_REQUIREMENT_CLASSES
    }
    unclassified_capabilities = [
        row
        for row in capabilities
        if row.get("requirement") not in LANE_REQUIREMENT_CLASSES
    ]
    required_all_active = bool(capability_groups["REQUIRED"]) and all(
        row.get("state") == "ACTIVE" for row in capability_groups["REQUIRED"]
    )
    blocked_states = {
        state: count
        for state, count in runtime["parser_states"].items()
        if state.startswith(("BLOCKED", "PARSE_FAILED"))
    }
    disposition = (
        "PRESERVED"
        if required_all_active and not blocked_states and not unclassified_capabilities
        else "PARTIAL"
    )
    four_file_contract = dict(lane_manifest.get("four_file_contract") or {})
    ordered_authorities = list(stable_artifact_names(lane))
    receipts = ["lane_pointer.json", "refresh_receipt.json", "lane_manifest.json"]
    authority_files_present = all(
        (lane_root / name).is_file() for name in ordered_authorities
    )
    receipt_files_present = all((lane_root / name).is_file() for name in receipts)
    retrieval = runtime["counts"]
    return {
        "lane_id": lane.canonical_lane_id,
        "display_label": lane.display_label,
        "state": "EMITTED",
        "disposition": disposition,
        "source_count": retrieval["sources"],
        "parser": {
            "parser_id": lane.parser_id,
            "parser_state_counts": runtime["parser_states"],
            "blocked_or_failed_state_counts": blocked_states,
            "exact_source_bytes_stored": True,
        },
        "capability_contract": {
            "requirements": capability_groups,
            "unclassified": unclassified_capabilities,
            "required_all_active": required_all_active,
            "actual_capability_count": len(capabilities),
        },
        "retrieval_contract": {
            "chunker_version": lane.chunker_version,
            "fts_table": lane.fts_table,
            "chunk_count": retrieval["chunks"],
            "chunk_cas_count": retrieval["chunk_cas"],
            "chunk_history_count": retrieval["chunk_history"],
            "fts_row_count": retrieval["fts"],
            "fts_matches_chunk_count": retrieval["fts"] == retrieval["chunks"],
        },
        "authority_contract": {
            "ordered_members": ordered_authorities,
            "all_present": authority_files_present,
            "four_file_contract_sha256": four_file_contract.get("contract_sha256"),
            "sqlite_authority": lane.sqlite_filename,
            "mermaid_projection": lane.mmd_filename,
            "dot_projection": lane.dot_filename,
            "tools_authority": "tools.json",
        },
        "topology_contract": {
            "mermaid_present": (lane_root / lane.mmd_filename).is_file(),
            "dot_present": (lane_root / lane.dot_filename).is_file(),
        },
        "receipt_contract": {
            "required": receipts,
            "all_present": receipt_files_present,
            "sqlite_refresh_receipt_count": retrieval["refresh_receipts"],
        },
        "absence_contract": {
            "lane_directory_absent": False,
            "placeholder_artifacts_emitted": False,
        },
        "build_mode": report.get("build_mode"),
    }


def _absent_lane_row(
    bundle_root: Path,
    lane: LaneDefinition,
    *,
    deferred: bool,
) -> dict[str, Any]:
    lane_root = bundle_root / lane.canonical_lane_id
    return {
        "lane_id": lane.canonical_lane_id,
        "display_label": lane.display_label,
        "state": "NOT_EMITTED",
        "disposition": "DEFERRED" if deferred else "MISSING",
        "source_count": 0,
        "parser": {
            "parser_id": lane.parser_id,
            "parser_state_counts": {},
            "blocked_or_failed_state_counts": {},
            "exact_source_bytes_stored": False,
            "measurement": "NOT_MEASURED_NO_CURRENT_ROUTED_SOURCE",
        },
        "capability_contract": {
            "requirements": {
                requirement: [] for requirement in LANE_REQUIREMENT_CLASSES
            },
            "unclassified": [],
            "required_all_active": False,
            "actual_capability_count": 0,
            "measurement": "NOT_MEASURED_NO_CURRENT_ROUTED_SOURCE",
        },
        "retrieval_contract": None,
        "authority_contract": {
            "ordered_members": [],
            "all_present": False,
            "four_file_contract_sha256": None,
        },
        "topology_contract": None,
        "receipt_contract": None,
        "absence_contract": {
            "reason": (
                "REMOVED_FROM_CURRENT_SOURCE_SET_HISTORY_RETAINED"
                if deferred
                else "NO_CURRENT_OR_PARENT_ROUTED_SOURCE"
            ),
            "lane_directory_absent": not lane_root.exists(),
            "placeholder_artifacts_emitted": lane_root.exists(),
        },
        "build_mode": None,
    }


def build_lane_disposition_projection(
    bundle_root: str | Path,
    *,
    emitted_lane_ids: tuple[str, ...] | list[str],
    removed_lane_ids: tuple[str, ...] | list[str],
    reports_by_lane: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build one canonical disposition row without fabricating omitted lanes."""

    root = Path(bundle_root).resolve()
    emitted = set(emitted_lane_ids)
    removed = set(removed_lane_ids)
    rows = [
        (
            _emitted_lane_row(root, LANE_REGISTRY[lane_id], reports_by_lane[lane_id])
            if lane_id in emitted
            else _absent_lane_row(
                root,
                LANE_REGISTRY[lane_id],
                deferred=lane_id in removed,
            )
        )
        for lane_id in CANONICAL_LANE_IDS
    ]
    counts = Counter(str(row["disposition"]) for row in rows)
    body = {
        "schema": LANE_DISPOSITION_SCHEMA,
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "ordered_lane_ids": list(CANONICAL_LANE_IDS),
        "disposition_classes": list(LANE_DISPOSITIONS),
        "requirement_classes": list(LANE_REQUIREMENT_CLASSES),
        "lane_emission_policy": "LOADED_OR_DETECTED_ONLY",
        "unloaded_lane_artifacts_fabricated": any(
            bool(row["absence_contract"]["placeholder_artifacts_emitted"])
            for row in rows
            if row["state"] == "NOT_EMITTED"
        ),
        "disposition_counts": {
            disposition: int(counts.get(disposition, 0))
            for disposition in LANE_DISPOSITIONS
        },
        "rows": rows,
    }
    body["projection_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def validate_lane_disposition_projection(
    bundle_root: str | Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Validate the additive contract while retaining V1/V2/V3 history."""

    declaration = manifest.get("lane_disposition_contract")
    if not isinstance(declaration, dict):
        return {
            "status": "PASS",
            "valid": True,
            "enforced": False,
            "compatibility": "HISTORICAL_V1_V2_V3_CONTRACT_ABSENT",
        }
    relative_path = str(declaration.get("path") or "")
    if relative_path != "lane_dispositions.json":
        return {
            "status": "FAIL",
            "valid": False,
            "enforced": True,
            "reason": "LANE_DISPOSITION_PATH_INVALID",
        }
    path = Path(bundle_root).resolve() / relative_path
    try:
        declared = _read_json(path)
        reports_by_lane = {
            str(row["lane_id"]): dict(row)
            for row in manifest.get("reports") or []
            if isinstance(row, dict) and row.get("lane_id")
        }
        computed = build_lane_disposition_projection(
            bundle_root,
            emitted_lane_ids=list(manifest.get("emitted_lane_ids") or []),
            removed_lane_ids=list(
                (manifest.get("summary") or {}).get("removed_lane_ids") or []
            ),
            reports_by_lane=reports_by_lane,
        )
    except (
        OSError,
        TypeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        sqlite3.Error,
    ) as exc:
        return {
            "status": "FAIL",
            "valid": False,
            "enforced": True,
            "reason": f"LANE_DISPOSITION_READ_OR_REBUILD_FAILED:{type(exc).__name__}",
        }
    declaration_valid = declaration == {
        "schema": LANE_DISPOSITION_SCHEMA,
        "path": relative_path,
        "projection_sha256": declared.get("projection_sha256"),
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
    }
    exact_match = declared == computed
    declared_rows = declared.get("rows")
    valid = bool(
        declaration_valid
        and exact_match
        and declared.get("schema") == LANE_DISPOSITION_SCHEMA
        and declared.get("ordered_lane_ids") == list(CANONICAL_LANE_IDS)
        and declared.get("canonical_lane_count") == len(CANONICAL_LANE_IDS)
        and declared.get("unloaded_lane_artifacts_fabricated") is False
        and isinstance(declared_rows, list)
        and len(declared_rows) == len(CANONICAL_LANE_IDS)
        and all(
            isinstance(row, dict)
            and row.get("disposition") in LANE_DISPOSITIONS
            for row in declared_rows
        )
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "enforced": True,
        "compatibility": None,
        "declaration_valid": declaration_valid,
        "exact_projection_match": exact_match,
        "projection_sha256": computed.get("projection_sha256"),
        "declared_projection_sha256": declared.get("projection_sha256"),
        "disposition_counts": declared.get("disposition_counts"),
        "rows": declared.get("rows"),
    }
