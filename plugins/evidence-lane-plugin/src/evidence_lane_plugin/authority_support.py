"""MMD/DOT/tooling support systems for non-sector SQLite authorities."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import require
from .graph_pipeline import SemanticGraph
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .sqlite_indexing import (
    LLAMA_INDEX_CORE_VERSION,
    rebuild_sqlite_authority_index,
)

_MMD_BEGIN = "%% EVIDENCE_LANE_SQLITE_SCHEMA_BEGIN"
_MMD_END = "%% EVIDENCE_LANE_SQLITE_SCHEMA_END"
_DOT_BEGIN = "// EVIDENCE_LANE_SQLITE_SCHEMA_BEGIN"
_DOT_END = "// EVIDENCE_LANE_SQLITE_SCHEMA_END"


@dataclass(frozen=True, slots=True)
class AuthoritySupportProfile:
    authority_id: str
    database: str
    mmd: str
    dot: str
    tools: str
    manifest: str


AUTHORITY_SUPPORT_PROFILES: dict[str, AuthoritySupportProfile] = {
    "agent_learning": AuthoritySupportProfile(
        "agent_learning",
        "ai_learning/agent-learning.sqlite",
        "ai_learning/agent-learning.mmd",
        "ai_learning/agent-learning.dot",
        "ai_learning/agent-learning.tools.json",
        "ai_learning/agent-learning.manifest.json",
    ),
    "canon_input": AuthoritySupportProfile(
        "canon_input",
        "canon/canon-input.sqlite",
        "canon/canon-input.mmd",
        "canon/canon-input.dot",
        "canon/canon-input.tools.json",
        "canon/canon-input.manifest.json",
    ),
    "project_memory": AuthoritySupportProfile(
        "project_memory",
        "memory/memory.sqlite",
        "memory/memory.mmd",
        "memory/memory.dot",
        "memory/memory.tools.json",
        "memory/memory.manifest.json",
    ),
    "project_overlay": AuthoritySupportProfile(
        "project_overlay",
        "project_overlay/project_overlay.sqlite",
        "project_overlay/project_overlay.mmd",
        "project_overlay/project_overlay.dot",
        "project_overlay/project_overlay.tools.json",
        "project_overlay/manifest.json",
    ),
    "source_authority": AuthoritySupportProfile(
        "source_authority",
        "sources/source_authority.sqlite",
        "sources/source_authority.mmd",
        "sources/source_authority.dot",
        "sources/source_authority.tools.json",
        "sources/source_authority.manifest.json",
    ),
    "project_universe": AuthoritySupportProfile(
        "project_universe",
        "universe/project_universe.sqlite",
        "universe/project_universe.mmd",
        "universe/project_universe.dot",
        "universe/project_universe.tools.json",
        "universe/PROJECT_UNIVERSE_MANIFEST.json",
    ),
    "connector_brain": AuthoritySupportProfile(
        "connector_brain",
        "connector_brain/connector-brain.sqlite",
        "connector_brain/connector_brain.mmd",
        "connector_brain/connector_brain.dot",
        "connector_brain/connector_brain.tools.json",
        "connector_brain/manifest.json",
    ),
    "project_authority": AuthoritySupportProfile(
        "project_authority",
        "project_authority/project-authority.sqlite",
        "project_authority/project_authority.mmd",
        "project_authority/project_authority.dot",
        "project_authority/project_authority.tools.json",
        "project_authority/manifest.json",
    ),
    "receipt_ledger": AuthoritySupportProfile(
        "receipt_ledger",
        "receipts/receipt-ledger.sqlite",
        "receipts/receipt-ledger.mmd",
        "receipts/receipt-ledger.dot",
        "receipts/receipt-ledger.tools.json",
        "receipts/manifest.json",
    ),
    "session_authority": AuthoritySupportProfile(
        "session_authority",
        "sessions/session-authority.sqlite",
        "sessions/session-authority.mmd",
        "sessions/session-authority.dot",
        "sessions/session-authority.tools.json",
        "sessions/manifest.json",
    ),
}


def _identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "node"


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', "'").replace("\n", " ")


def _schema_snapshot(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        require(
            integrity == ["ok"],
            "AUTHORITY_SUPPORT_SQLITE_INTEGRITY_FAILED",
            "An auxiliary authority failed SQLite integrity validation.",
            status="FAIL",
            database=str(database),
        )
        objects = [
            dict(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]
        tables: list[dict[str, Any]] = []
        for row in objects:
            if row["type"] != "table":
                continue
            table = str(row["name"])
            escaped = table.replace('"', '""')
            columns = [
                {
                    "cid": int(column["cid"]),
                    "name": str(column["name"]),
                    "type": str(column["type"] or ""),
                    "not_null": bool(column["notnull"]),
                    "primary_key": int(column["pk"]),
                }
                for column in connection.execute(f'PRAGMA table_info("{escaped}")')
            ]
            foreign_keys = [
                {
                    "from": str(foreign_key["from"]),
                    "table": str(foreign_key["table"]),
                    "to": str(foreign_key["to"]),
                }
                for foreign_key in connection.execute(
                    f'PRAGMA foreign_key_list("{escaped}")'
                )
            ]
            tables.append(
                {
                    "name": table,
                    "virtual_fts5": "USING FTS5" in str(row.get("sql") or "").upper(),
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                }
            )
    finally:
        connection.close()
    core = {
        "schema": "evidence-lane.sqlite-authority-schema-snapshot.v1",
        "database_sha256": sha256_file(database),
        "objects": objects,
        "tables": tables,
        "table_count": len(tables),
        "fts5_tables": [row["name"] for row in tables if row["virtual_fts5"]],
    }
    return {**core, "schema_sha256": sha256_bytes(canonical_json_bytes(core))}


def _schema_graph(
    authority_id: str, snapshot: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    graph = SemanticGraph(
        f"{_identifier(authority_id)}_authority",
        direction="TB",
        role="AUTHORITY_TRAVERSAL",
    )
    graph.add_node("AUTHORITY_ROOT", f"{authority_id} SQLite authority", "root")
    graph.begin_group(
        f"SUPPORT_{_identifier(authority_id)}",
        f"{authority_id} SQLite schema",
        direction="TB",
    )
    for table in snapshot["tables"]:
        table_node = f"support_table_{_identifier(table['name'])}"
        table_label = f"{table['name']}" + (" [FTS5]" if table["virtual_fts5"] else "")
        graph.add_node(table_node, table_label, "table")
        graph.add_edge("AUTHORITY_ROOT", table_node, "owns")
        for column in table["columns"]:
            column_node = f"{table_node}_column_{_identifier(column['name'])}"
            column_label = f"{column['name']} : {column['type'] or 'ANY'}"
            graph.add_node(column_node, column_label, "column")
            graph.add_edge(table_node, column_node, "column")
        for foreign_key in table["foreign_keys"]:
            target = f"support_table_{_identifier(foreign_key['table'])}"
            edge = f"FK {foreign_key['from']}->{foreign_key['to']}"
            graph.add_edge(table_node, target, edge)
    graph.end_group()
    return graph.render_pair()


def _replace_block(text: str, begin: str, end: str, block: str) -> str:
    if begin in text and end in text:
        start = text.index(begin)
        finish = text.index(end, start) + len(end)
        return text[:start].rstrip() + "\n\n" + block + text[finish:].lstrip("\n")
    return text.rstrip() + "\n\n" + block


def refresh_authority_support(
    project_root: str | Path,
    authority_id: str,
) -> dict[str, Any]:
    """Refresh one support system after its owning SQLite write has completed."""

    root = Path(project_root).resolve()
    profile = AUTHORITY_SUPPORT_PROFILES[authority_id]
    database = root / profile.database
    mmd_path = root / profile.mmd
    dot_path = root / profile.dot
    tools_path = root / profile.tools
    manifest_path = root / profile.manifest
    require(
        database.is_file(),
        "AUTHORITY_SUPPORT_DATABASE_MISSING",
        "The support-system builder requires its exact SQLite authority.",
        status="MISMATCH",
        authority_id=authority_id,
        database=str(database),
    )
    llama_index_receipt = rebuild_sqlite_authority_index(
        database,
        authority_id=authority_id,
    )
    snapshot = _schema_snapshot(database)
    mmd_text, dot_text, graph_receipt = _schema_graph(authority_id, snapshot)
    mmd_path.parent.mkdir(parents=True, exist_ok=True)
    dot_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(mmd_path, mmd_text.encode("utf-8"))
    atomic_write_bytes(dot_path, dot_text.encode("utf-8"))
    prior_tools = (
        json.loads(tools_path.read_text(encoding="utf-8"))
        if tools_path.is_file()
        else {}
    )
    tooling = {
        **prior_tools,
        "schema": "evidence-lane.authority-build-refresh-tools.v1",
        "authority_id": authority_id,
        "role": "BUILD_REFRESH_SCHEMA_AND_DEPENDENCY_TOOLING",
        "query_selector": False,
        "bootstrap_schema_is_fixed_project_ceiling": False,
        "schema_evolution": "PROJECT_SCOPED_ADDITIVE_WITH_OWNING_USER_GATE",
        "dependencies": [
            "python:sqlite3",
            "sqlite:fts5",
            f"llama-index-core=={LLAMA_INDEX_CORE_VERSION}",
            "langgraph",
            "graphviz",
        ],
        "database": profile.database,
        "schema_snapshot": snapshot,
    }
    tooling["tools_sha256"] = sha256_bytes(canonical_json_bytes(tooling))
    atomic_write_json(tools_path, tooling)
    support = {
        "schema": "evidence-lane.sqlite-authority-support-system.v1",
        "status": "PASS",
        "authority_id": authority_id,
        "database": {"path": profile.database, "sha256": sha256_file(database)},
        "mmd": {"path": profile.mmd, "sha256": sha256_file(mmd_path)},
        "dot": {"path": profile.dot, "sha256": sha256_file(dot_path)},
        "tools": {"path": profile.tools, "sha256": sha256_file(tools_path)},
        "schema_sha256": snapshot["schema_sha256"],
        "llama_index_refresh_receipt": llama_index_receipt,
        "graph_pipeline_receipt": graph_receipt,
        "semantic_graph_rebuilt_from_owning_sqlite": True,
        "sqlite_schema_graph_appended": False,
        "tools_role": "BUILD_REFRESH_ONLY",
        "query_role": "MMD_DOT_TRAVERSAL_THEN_BOUNDED_SQLITE",
    }
    support["receipt_sha256"] = sha256_bytes(canonical_json_bytes(support))
    prior_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    manifest = {
        **prior_manifest,
        "sqlite_authority_support_system": support,
    }
    members = manifest.get("members")
    support_paths = {
        Path(profile.database).name: database,
        Path(profile.mmd).name: mmd_path,
        Path(profile.dot).name: dot_path,
        Path(profile.tools).name: tools_path,
    }
    if isinstance(members, list):
        by_path = {
            str(row.get("path")): dict(row) for row in members if isinstance(row, dict)
        }
        for name, path in support_paths.items():
            by_path[name] = {
                "path": name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        manifest["members"] = [by_path[name] for name in sorted(by_path)]
    elif isinstance(members, dict):
        manifest["members"] = {
            **members,
            **{name: sha256_file(path) for name, path in support_paths.items()},
        }
    if "manifest_sha256" in manifest:
        manifest_body = {
            key: value for key, value in manifest.items() if key != "manifest_sha256"
        }
        manifest["manifest_sha256"] = sha256_bytes(canonical_json_bytes(manifest_body))
    atomic_write_json(manifest_path, manifest)
    return support


def validate_authority_support(
    project_root: str | Path,
    authority_id: str,
) -> dict[str, Any]:
    """Validate and traverse one already-refreshed non-sector authority."""

    root = Path(project_root).resolve()
    profile = AUTHORITY_SUPPORT_PROFILES[authority_id]
    paths = {
        "database": root / profile.database,
        "mmd": root / profile.mmd,
        "dot": root / profile.dot,
        "tools": root / profile.tools,
        "manifest": root / profile.manifest,
    }
    require(
        all(path.is_file() for path in paths.values()),
        "AUTHORITY_SUPPORT_SYSTEM_MISSING",
        "The auxiliary authority lacks its SQLite/MMD/DOT/tools support system.",
        status="MISMATCH",
        authority_id=authority_id,
        missing=[name for name, path in paths.items() if not path.is_file()],
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    support = manifest.get("sqlite_authority_support_system")
    tools = json.loads(paths["tools"].read_text(encoding="utf-8"))
    snapshot = _schema_snapshot(paths["database"])
    require(
        isinstance(support, dict)
        and support.get("status") == "PASS"
        and support.get("authority_id") == authority_id
        and support.get("database", {}).get("sha256") == sha256_file(paths["database"])
        and support.get("mmd", {}).get("sha256") == sha256_file(paths["mmd"])
        and support.get("dot", {}).get("sha256") == sha256_file(paths["dot"])
        and support.get("tools", {}).get("sha256") == sha256_file(paths["tools"])
        and support.get("schema_sha256") == snapshot["schema_sha256"]
        and tools.get("authority_id") == authority_id
        and tools.get("role") == "BUILD_REFRESH_SCHEMA_AND_DEPENDENCY_TOOLING"
        and tools.get("query_selector") is False
        and "EVIDENCE_LANE_GRAPH_ENGINE="
        in paths["mmd"].read_text(encoding="utf-8")
        and "EVIDENCE_LANE_GRAPH_ENGINE=PYTHON_GRAPHVIZ"
        in paths["dot"].read_text(encoding="utf-8"),
        "AUTHORITY_SUPPORT_SYSTEM_MISMATCH",
        "The auxiliary authority support-system hashes or schema no longer match.",
        status="MISMATCH",
        authority_id=authority_id,
    )
    core = {
        "schema": "evidence-lane.sqlite-authority-traversal.v1",
        "status": "PASS",
        "authority_id": authority_id,
        "traversal_order": [profile.mmd, profile.dot, profile.database],
        "schema_sha256": snapshot["schema_sha256"],
        "fts5_tables": snapshot["fts5_tables"],
        "mmd_dot_graph_engine_markers_present": True,
        "tools_role": "BUILD_REFRESH_ONLY",
        "tools_select_query": False,
        "sqlite_query_mode": "READ_ONLY_IMMUTABLE_BOUNDED",
        "bootstrap_schema_is_fixed_project_ceiling": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def refresh_delta_exit_authority_supports(project_root: str | Path) -> dict[str, Any]:
    """Refresh changed ordinary-Delta authorities; Project Overlay stays HIL-only."""

    authority_ids = (
        "agent_learning",
        "canon_input",
        "project_memory",
        "source_authority",
        "project_universe",
        "connector_brain",
    )
    root = Path(project_root).resolve()
    rows = [
        refresh_authority_support(root, authority_id) for authority_id in authority_ids
    ]
    core = {
        "schema": "evidence-lane.delta-exit-authority-support-refresh.v1",
        "status": "PASS",
        "authority_ids": list(authority_ids),
        "receipts": rows,
        "project_overlay_refreshed": False,
        "project_overlay_owner": "HIL_ONLY",
    }
    receipt = {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    head = root / "runtime" / "authority-support-head.json"
    head.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(head, receipt)
    return receipt


def validate_delta_exit_authority_supports(
    project_root: str | Path,
) -> dict[str, Any]:
    """Validate the current post-Delta auxiliary support-system head."""

    root = Path(project_root).resolve()
    head = root / "runtime" / "authority-support-head.json"
    if not head.is_file():
        return {
            "schema": "evidence-lane.delta-exit-authority-support-validation.v1",
            "status": "PENDING_FIRST_DELTA_EXIT_ACTIVATION",
            "enforced": False,
            "traversals": [],
            "project_overlay_included": False,
        }
    receipt = json.loads(head.read_text(encoding="utf-8"))
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    require(
        receipt.get("schema") == "evidence-lane.delta-exit-authority-support-refresh.v1"
        and receipt.get("receipt_sha256") == sha256_bytes(canonical_json_bytes(body)),
        "AUTHORITY_SUPPORT_HEAD_INVALID",
        "The current auxiliary support-system head failed its immutable hash.",
        status="MISMATCH",
    )
    authority_ids = [str(item) for item in receipt.get("authority_ids") or []]
    traversals = [validate_authority_support(root, item) for item in authority_ids]
    core = {
        "schema": "evidence-lane.delta-exit-authority-support-validation.v1",
        "status": "PASS",
        "enforced": True,
        "head_receipt_sha256": receipt["receipt_sha256"],
        "authority_ids": authority_ids,
        "traversals": traversals,
        "project_overlay_included": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "AUTHORITY_SUPPORT_PROFILES",
    "refresh_authority_support",
    "refresh_delta_exit_authority_supports",
    "validate_authority_support",
    "validate_delta_exit_authority_supports",
]
