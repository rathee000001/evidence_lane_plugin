"""Deterministic SQLite physical-schema projections for every Evidence Lane.

The semantic MMD/DOT views remain lane-specific.  This module adds one shared,
schema-derived layer that accounts for every declared contract table and every
non-``sqlite_*`` table materialized by SQLite (including FTS auxiliary tables).
It is deliberately read-only and contains no source or lifecycle mutation path.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import CORE_SCHEMA_TABLES, LaneDefinition

PHYSICAL_SCHEMA_PROJECTION_SCHEMA = (
    "evidence-lane.sqlite-physical-schema-projection.v1"
)
_SAFE_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")


def _quoted_table(table: str) -> str:
    if not _SAFE_TABLE.fullmatch(table):
        raise ValueError(f"Unsafe physical schema table name: {table}")
    return table.replace('"', '""')


def _table_role(lane: LaneDefinition, table: str) -> str:
    if table in CORE_SCHEMA_TABLES:
        return "core_contract"
    if table == lane.fts_table:
        return "lane_fts_contract"
    if table in lane.schema_contract:
        return "lane_contract"
    return "sqlite_engine_auxiliary"


def physical_schema_projection(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
) -> dict[str, Any]:
    """Return the exact deterministic public-table projection for one lane DB."""

    table_rows = connection.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    actual_sql = {str(row["name"]): str(row["sql"] or "") for row in table_rows}
    contract_tables = list(lane.schema_contract)
    missing_contract_tables = [
        table for table in contract_tables if table not in actual_sql
    ]
    auxiliary_tables = sorted(set(actual_sql) - set(contract_tables))
    ordered_tables = [
        *[table for table in contract_tables if table in actual_sql],
        *auxiliary_tables,
    ]

    tables: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for ordinal, table in enumerate(ordered_tables):
        quoted = _quoted_table(table)
        columns = connection.execute(
            f'PRAGMA table_xinfo("{quoted}")'  # nosec B608
        ).fetchall()
        foreign_keys = connection.execute(
            f'PRAGMA foreign_key_list("{quoted}")'  # nosec B608
        ).fetchall()
        row_count = int(
            connection.execute(
                f'SELECT COUNT(*) FROM "{quoted}"'  # nosec B608
            ).fetchone()[0]
        )
        column_projection = [
            {
                "cid": int(row["cid"]),
                "name": str(row["name"]),
                "type": str(row["type"] or ""),
                "notnull": bool(row["notnull"]),
                "primary_key_ordinal": int(row["pk"]),
                "hidden": int(row["hidden"]) if "hidden" in row else 0,
            }
            for row in columns
        ]
        foreign_key_projection = [
            {
                "id": int(row["id"]),
                "sequence": int(row["seq"]),
                "from_column": str(row["from"]),
                "parent_table": str(row["table"]),
                "parent_column": str(row["to"] or ""),
                "on_update": str(row["on_update"]),
                "on_delete": str(row["on_delete"]),
                "match": str(row["match"]),
            }
            for row in foreign_keys
        ]
        table_projection = {
            "ordinal": ordinal,
            "table": table,
            "role": _table_role(lane, table),
            "contract_required": table in lane.schema_contract,
            "rows": row_count,
            "columns": column_projection,
            "foreign_keys": foreign_key_projection,
            "definition_sha256": sha256_bytes(actual_sql[table].encode("utf-8")),
        }
        tables.append(table_projection)
        for foreign_key in foreign_key_projection:
            relations.append(
                {
                    "child_table": table,
                    **foreign_key,
                }
            )

    core: dict[str, Any] = {
        "schema": PHYSICAL_SCHEMA_PROJECTION_SCHEMA,
        "lane_id": lane.canonical_lane_id,
        "contract_tables": contract_tables,
        "missing_contract_tables": missing_contract_tables,
        "auxiliary_tables": auxiliary_tables,
        "table_count": len(tables),
        "contract_table_count": len(contract_tables),
        "auxiliary_table_count": len(auxiliary_tables),
        "relation_count": len(relations),
        "tables": tables,
        "relations": relations,
    }
    return {
        **core,
        "projection_sha256": sha256_bytes(canonical_json_bytes(core)),
    }


def physical_table_node_ids(projection: dict[str, Any]) -> dict[str, str]:
    """Map table names to stable MMD/DOT IDs from projection ordinals."""

    return {
        str(row["table"]): f'PHYSICAL_TABLE_{int(row["ordinal"]):03d}'
        for row in projection["tables"]
    }
