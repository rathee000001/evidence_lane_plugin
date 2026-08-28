"""Executable support-system traversal for one Evidence Lane sector."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .topology_reconciliation import reconcile_lane_topology

_SAFE_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_lane_query_traversal(
    lane_root: str | Path,
    lane: Any,
) -> dict[str, Any]:
    """Bind pointer, MMD, DOT, tools, manifest, and SQLite before a lane read."""

    root = Path(lane_root).resolve()
    manifest_path = root / "lane_manifest.json"
    pointer_path = root / "lane_pointer.json"
    tools_path = root / "tools.json"
    mmd_path = root / lane.mmd_filename
    dot_path = root / lane.dot_filename
    database_path = root / lane.sqlite_filename
    required = (
        manifest_path,
        pointer_path,
        tools_path,
        mmd_path,
        dot_path,
        database_path,
    )
    require(
        all(path.is_file() for path in required),
        "LANE_TRAVERSAL_SUPPORT_MISSING",
        "The lane query requires pointer, manifest, MMD, DOT, tools, and SQLite.",
        status="MISMATCH",
        lane_id=lane.canonical_lane_id,
        missing=[path.name for path in required if not path.is_file()],
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    tools = json.loads(tools_path.read_text(encoding="utf-8"))
    stable = cast(dict[str, Any], manifest.get("stable_artifacts") or {})
    expected_hashes = {
        lane.sqlite_filename: sha256_file(database_path),
        lane.mmd_filename: sha256_file(mmd_path),
        lane.dot_filename: sha256_file(dot_path),
        "tools.json": sha256_file(tools_path),
    }
    tool_lane = cast(dict[str, Any], tools.get("lane") or {})
    manifest_lane = cast(dict[str, Any], manifest.get("lane") or {})
    artifact_authority = cast(dict[str, Any], tools.get("artifact_authority") or {})
    member_hashes = {
        str(row.get("path")): str(row.get("sha256"))
        for row in artifact_authority.get("members") or []
        if isinstance(row, dict)
    }
    require(
        manifest.get("schema") == "evidence-lane.lane-manifest.v3"
        and pointer.get("lane_id") == lane.canonical_lane_id
        and tool_lane.get("canonical_lane_id") == lane.canonical_lane_id
        and manifest_lane.get("canonical_lane_id") == lane.canonical_lane_id
        and all(stable.get(name) == digest for name, digest in expected_hashes.items())
        and all(
            member_hashes.get(name) == digest
            for name, digest in expected_hashes.items()
            if name != "tools.json"
        ),
        "LANE_TRAVERSAL_IDENTITY_MISMATCH",
        "The lane support artifacts do not bind one exact query authority.",
        status="MISMATCH",
        lane_id=lane.canonical_lane_id,
    )
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        lane_meta = {
            str(key): str(value)
            for key, value in connection.execute("SELECT key,value FROM lane_meta")
        }
        fts_tables = {
            str(name)
            for name, sql in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if "USING FTS5" in str(sql or "").upper()
        }
    finally:
        connection.close()
    selected_fts_table = str(manifest_lane.get("fts_table") or "").strip()
    require(
        integrity == ["ok"]
        and lane_meta.get("lane_id") == lane.canonical_lane_id
        and bool(_SAFE_SQL_IDENTIFIER.fullmatch(selected_fts_table))
        and selected_fts_table in fts_tables
        and lane_meta.get("lane_schema_contract_sha256")
        == manifest_lane.get("lane_schema_contract_sha256"),
        "LANE_TRAVERSAL_SQLITE_SCHEMA_MISMATCH",
        "The current pointer/manifest does not match the evolved lane SQLite schema.",
        status="MISMATCH",
        lane_id=lane.canonical_lane_id,
        selected_fts_table=selected_fts_table or None,
        available_fts_tables=sorted(fts_tables),
    )
    topology = reconcile_lane_topology(
        root,
        lane_id=lane.canonical_lane_id,
        mmd_filename=lane.mmd_filename,
        dot_filename=lane.dot_filename,
        sqlite_filename=lane.sqlite_filename,
    )
    require(
        topology.get("status") == "PASS"
        and topology.get("rendering_parity", {}).get("status") == "PASS"
        and topology.get("schema_derived_contract", {}).get("status") == "PASS",
        "LANE_TRAVERSAL_TOPOLOGY_MISMATCH",
        "MMD and DOT do not reconcile with the lane SQLite schema.",
        status="MISMATCH",
        lane_id=lane.canonical_lane_id,
    )
    core = {
        "schema": "evidence-lane.lane-query-traversal.v1",
        "status": "PASS",
        "lane_id": lane.canonical_lane_id,
        "traversal_order": [
            "lane_pointer.json",
            "lane_manifest.json",
            lane.mmd_filename,
            lane.dot_filename,
            "tools.json",
            lane.sqlite_filename,
        ],
        "pointer": pointer,
        "artifact_hashes": expected_hashes,
        "tool_identity_sha256": tools.get("sha256"),
        "artifact_authority_sha256": artifact_authority.get("authority_sha256"),
        "selected_fts_table": selected_fts_table,
        "selected_fts_table_source": "CURRENT_LANE_MANIFEST_PLUS_SQLITE_SCHEMA",
        "schema_contract_sha256": lane_meta.get("lane_schema_contract_sha256"),
        "schema_registry_sha256": lane_meta.get("lane_schema_registry_sha256"),
        "schema_migration_head": lane_meta.get("lane_schema_migration_head"),
        "available_fts_tables": sorted(fts_tables),
        "topology": {
            "claims_checked": topology.get("claims_checked"),
            "claims_failed": topology.get("claims_failed"),
            "mmd_dot_parity": topology["rendering_parity"]["status"],
            "logical_code_contract": topology["logical_code_contract"]["status"],
            "physical_schema_contract": topology["physical_schema_contract"]["status"],
            "schema_derived_contract": topology["schema_derived_contract"]["status"],
        },
        "sqlite_open_mode": "READ_ONLY_IMMUTABLE_QUERY_ONLY",
        "mmd_and_dot_used_as_traversal_maps": True,
        "tools_json_role": "BUILD_REFRESH_TOOLCHAIN_AND_SCHEMA_PROVENANCE",
        "tools_json_selects_query": False,
        "bootstrap_schema_is_fixed_project_ceiling": False,
        "evolved_lane_schema_allowed": True,
        "sqlite_used_as_bounded_data_authority": True,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = ["validate_lane_query_traversal"]
