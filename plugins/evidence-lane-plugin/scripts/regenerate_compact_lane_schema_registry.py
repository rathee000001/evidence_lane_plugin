"""Regenerate the shared lane schema registry for compact CAS storage."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = PLUGIN_ROOT / "src"
if str(PACKAGE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SOURCE))

REGISTRY = PLUGIN_ROOT / "schemas" / "lane-schema-registry.v001.json"
SOURCE_COMPACT_TABLE = "source_content_cas"
AUTHORITY_COMPACT_TABLE = "authority_index_content_cas"
TOOL_ROUTE_TABLE = "tool_route_contract"
TOOL_EXECUTION_TABLE = "tool_execution_receipt"
PRIMARY_CODE_LANES = {"github_code", "local_code"}


def _bootstrap_registry_contract() -> None:
    """Make the versioned registry importable before computing new SQL hashes."""

    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    base_tables = list(payload["base_schema"]["tables"])
    for table in (TOOL_ROUTE_TABLE, TOOL_EXECUTION_TABLE):
        if table in base_tables:
            base_tables.remove(table)
    parser_index = base_tables.index("parser_capability") + 1
    base_tables[parser_index:parser_index] = [TOOL_ROUTE_TABLE, TOOL_EXECUTION_TABLE]
    payload["base_schema"]["schema_id"] = "evidence-lane.universal-lane.v5"
    payload["base_schema"]["tables"] = sorted(base_tables)
    for row in payload["lanes"]:
        tables = list(row["tables"])
        for table in (TOOL_ROUTE_TABLE, TOOL_EXECUTION_TABLE):
            if table in tables:
                tables.remove(table)
        parser_index = tables.index("parser_capability") + 1
        tables[parser_index:parser_index] = [TOOL_ROUTE_TABLE, TOOL_EXECUTION_TABLE]
        migrations = list(row["migration_ledger"])
        if not any(
            item.get("operation") == "REBUILD_WITH_TOOL_ROUTE_AND_EXECUTION_LEDGER"
            for item in migrations
        ):
            current_version = int(migrations[-1]["to_version"])
            migrations.append(
                {
                    "migration_id": (
                        f"{row['lane_id']}.tool-route-execution-ledger."
                        f"v{current_version + 1:03d}"
                    ),
                    "sequence": len(migrations) + 1,
                    "from_version": current_version,
                    "to_version": current_version + 1,
                    "operation": "REBUILD_WITH_TOOL_ROUTE_AND_EXECUTION_LEDGER",
                    "additive_only": False,
                    "rebuild_required": True,
                }
            )
        version = int(migrations[-1]["to_version"])
        row.update(
            {
                "base_schema_id": "evidence-lane.universal-lane.v5",
                "schema_version": version,
                "schema_id": (
                    f"evidence-lane.lane-schema.{row['lane_id']}.v{version:03d}"
                ),
                "tables": tables,
                "migration_ledger": migrations,
            }
        )
    REGISTRY.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )


_bootstrap_registry_contract()

from evidence_lane_plugin.hashing import (
    atomic_write_bytes,
    canonical_json_bytes,
    sha256_bytes,
)
from evidence_lane_plugin.lane_engine import _create_lane_schema
from evidence_lane_plugin.lanes import LANE_REGISTRY

OBSOLETE_CODE_SCHEMA_TABLES = {
    "sector_meta",
    "sector_head",
    "artifact_registry",
    "relation_edge",
    "code_source_registry",
    "code_file_snapshot",
    "code_chunk",
    "code_route_api_boundary",
    "code_config_build_test_chunk",
    "code_index_checkpoint",
    "code_source_active_head",
    "code_workflow_edge",
    "code_semantic_diff",
    "code_synthetic_snapshot_file",
    "code_snapshot_history",
    "code_good_snapshot",
    "snapshot_git_bridge",
    "git_patch_hunk",
    "git_exact_line_change",
    "git_push_event",
    "git_route_impact",
    "git_symbol_impact",
    "git_dependency_impact",
    "git_test_impact",
    "git_artifact_impact",
}


def _with_compact_tables(tables: list[str]) -> list[str]:
    rows = [
        table
        for table in tables
        if table
        not in {
            SOURCE_COMPACT_TABLE,
            AUTHORITY_COMPACT_TABLE,
        }
    ]
    rows.insert(rows.index("lane_pointer") + 1, SOURCE_COMPACT_TABLE)
    rows.insert(rows.index("authority_index_source"), AUTHORITY_COMPACT_TABLE)
    for table in (TOOL_ROUTE_TABLE, TOOL_EXECUTION_TABLE):
        if table in rows:
            rows.remove(table)
    parser_index = rows.index("parser_capability") + 1
    rows[parser_index:parser_index] = [TOOL_ROUTE_TABLE, TOOL_EXECUTION_TABLE]
    return rows


def _projection(database: Path, lane_id: str, tables: list[str]) -> str:
    connection = sqlite3.connect(database)
    try:
        table_rows = []
        for table in tables:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Missing compact lane table: {lane_id}:{table}")
            table_rows.append({"table": table, "sql": str(row[0] or "")})
        return sha256_bytes(
            canonical_json_bytes({"lane_id": lane_id, "tables": table_rows})
        )
    finally:
        connection.close()


def main() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload["base_schema"]["schema_id"] = "evidence-lane.universal-lane.v5"
    payload["base_schema"]["tables"] = sorted(
        _with_compact_tables(list(payload["base_schema"]["tables"]))
    )
    with tempfile.TemporaryDirectory(prefix="evidence-lane-compact-schema-") as raw:
        temporary = Path(raw)
        for row in payload["lanes"]:
            lane_id = str(row["lane_id"])
            lane = LANE_REGISTRY[lane_id]
            database = temporary / f"{lane_id}.sqlite"
            connection = sqlite3.connect(database)
            try:
                _create_lane_schema(connection, lane, verify_schema_asset=False)
                connection.commit()
            finally:
                connection.close()
            tables = _with_compact_tables(list(row["tables"]))
            if lane_id in PRIMARY_CODE_LANES:
                tables = [
                    table
                    for table in tables
                    if table not in OBSOLETE_CODE_SCHEMA_TABLES
                ]
            migrations = [dict(item) for item in row["migration_ledger"]]
            if not any(
                item.get("operation")
                == "REBUILD_COMPACT_CONTENT_CAS_AND_CONTENTLESS_FTS"
                for item in migrations
            ):
                migrations.append(
                    {
                        "migration_id": f"{lane_id}.compact-storage.v002",
                        "sequence": len(migrations) + 1,
                        "from_version": int(row["schema_version"]),
                        "to_version": int(row["schema_version"]) + 1,
                        "operation": "REBUILD_COMPACT_CONTENT_CAS_AND_CONTENTLESS_FTS",
                        "additive_only": False,
                        "rebuild_required": True,
                    }
                )
            if not any(
                item.get("operation")
                == "REBUILD_REFERENCE_ONLY_CHUNKS_AND_COMPACT_AUTHORITY_INDEX"
                for item in migrations
            ):
                current_version = int(migrations[-1]["to_version"])
                migrations.append(
                    {
                        "migration_id": f"{lane_id}.reference-only-index.v003",
                        "sequence": len(migrations) + 1,
                        "from_version": current_version,
                        "to_version": current_version + 1,
                        "operation": (
                            "REBUILD_REFERENCE_ONLY_CHUNKS_AND_COMPACT_AUTHORITY_INDEX"
                        ),
                        "additive_only": False,
                        "rebuild_required": True,
                    }
                )
            if lane_id in PRIMARY_CODE_LANES and not any(
                item.get("operation")
                == "REBUILD_WITHOUT_UNREACHABLE_CODE_PLACEHOLDER_TABLES"
                for item in migrations
            ):
                current_version = int(migrations[-1]["to_version"])
                migrations.append(
                    {
                        "migration_id": (
                            f"{lane_id}.purge-unreachable-code-tables.v004"
                        ),
                        "sequence": len(migrations) + 1,
                        "from_version": current_version,
                        "to_version": current_version + 1,
                        "operation": (
                            "REBUILD_WITHOUT_UNREACHABLE_CODE_PLACEHOLDER_TABLES"
                        ),
                        "additive_only": False,
                        "rebuild_required": True,
                    }
                )
            if not any(
                item.get("operation")
                == "REBUILD_WITH_DIRECT_PURGE_CURRENT_ONLY"
                for item in migrations
            ):
                current_version = int(migrations[-1]["to_version"])
                migrations.append(
                    {
                        "migration_id": (
                            f"{lane_id}.direct-purge-current-only."
                            f"v{current_version + 1:03d}"
                        ),
                        "sequence": len(migrations) + 1,
                        "from_version": current_version,
                        "to_version": current_version + 1,
                        "operation": (
                            "REBUILD_WITH_DIRECT_PURGE_CURRENT_ONLY"
                        ),
                        "additive_only": False,
                        "rebuild_required": True,
                    }
                )
            if not any(
                item.get("operation")
                == "REBUILD_WITH_TOOL_ROUTE_AND_EXECUTION_LEDGER"
                for item in migrations
            ):
                current_version = int(migrations[-1]["to_version"])
                migrations.append(
                    {
                        "migration_id": (
                            f"{lane_id}.tool-route-execution-ledger."
                            f"v{current_version + 1:03d}"
                        ),
                        "sequence": len(migrations) + 1,
                        "from_version": current_version,
                        "to_version": current_version + 1,
                        "operation": (
                            "REBUILD_WITH_TOOL_ROUTE_AND_EXECUTION_LEDGER"
                        ),
                        "additive_only": False,
                        "rebuild_required": True,
                    }
                )
            version = int(migrations[-1]["to_version"])
            row.update(
                {
                    "base_schema_id": "evidence-lane.universal-lane.v5",
                    "schema_id": (
                        f"evidence-lane.lane-schema.{lane_id}.v{version:03d}"
                    ),
                    "schema_version": version,
                    "tables": tables,
                    "migration_ledger": migrations,
                    "sqlite_master_projection_sha256": _projection(
                        database,
                        lane_id,
                        tables,
                    ),
                }
            )
    atomic_write_bytes(REGISTRY, canonical_json_bytes(payload))
    print(
        json.dumps(
            {
                "status": "PASS",
                "registry": REGISTRY.as_posix(),
                "lane_count": len(payload["lanes"]),
                "schema_version": sorted(
                    {int(row["schema_version"]) for row in payload["lanes"]}
                ),
                "compact_tables": [
                    SOURCE_COMPACT_TABLE,
                    AUTHORITY_COMPACT_TABLE,
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
