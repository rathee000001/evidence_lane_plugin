from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from evidence_lane_plugin.hashing import sha256_file


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_json(database: Path, table: str, column: str) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = connection.execute(
            f'SELECT "{column}" FROM "{table}" ORDER BY sequence DESC LIMIT 1'
        ).fetchone()
        assert row is not None
        return json.loads(str(row[0]))
    finally:
        connection.close()


def test_env_uop_toolchain_staging_and_indexes_are_executable() -> None:
    env = PLUGIN_ROOT / "env" / "env_sqlite.sqlite"
    uop = PLUGIN_ROOT / "uop" / "uop_sqlite.sqlite"
    toolchain = _latest_json(
        env,
        "ai_toolchain_sync_receipt_v16",
        "receipt_json",
    )
    assert toolchain["status"] == "PASS"
    assert (toolchain["tool_count"], toolchain["action_count"], toolchain["lane_count"]) == (
        95,
        91,
        18,
    )
    assert toolchain["duckdb_staging"]["status"] == "PASS"
    assert toolchain["duckdb_staging"]["persistent_authority"] is False
    assert toolchain["duckdb_staging"]["sqlite_remains_authority"] is True
    assert toolchain["llama_index_authority_receipts"]["env"]["status"] == "PASS"
    assert toolchain["llama_index_authority_receipts"]["uop"]["status"] == "PASS"
    assert toolchain["llama_index_authority_receipts"]["env"]["sqlite_backend"] == (
        "FTS5_BM25"
    )
    assert toolchain["llama_index_authority_receipts"]["uop"]["sqlite_backend"] == (
        "FTS5_BM25"
    )
    for database, authority in ((env, "env"), (uop, "uop")):
        indexed = _latest_json(
            database,
            "authority_index_refresh_receipt",
            "receipt_json",
        )
        assert indexed["authority_id"] == authority
        assert indexed["source_count"] > 0
        assert indexed["node_count"] > 0


def test_env_uop_graph_flash_and_packaged_manifests_share_current_bytes() -> None:
    flash = _json(PLUGIN_ROOT / "env" / "SESSION_FLASH_MANIFEST.json")
    assert flash["ai_toolchain"]["tool_count"] == 95
    assert flash["ai_toolchain"]["action_count"] == 91
    assert flash["ai_toolchain"]["lane_count"] == 18
    for authority in ("env", "uop"):
        database = PLUGIN_ROOT / authority / f"{authority}_sqlite.sqlite"
        graph = _latest_json(
            database,
            "semantic_graph_render_receipt_v16",
            "graph_pipeline_receipt_json",
        )
        assert graph["status"] == "PASS"
        assert graph["mermaid_exporter"] == "LANGGRAPH_STATEGRAPH"
        assert graph["dot_exporter"] == "PYTHON_GRAPHVIZ"
        assert graph["langgraph_version"] == "1.2.11"
        assert graph["python_graphviz_version"] == "0.21"
        assert graph["node_count"] > 0
        assert graph["edge_count"] > 0

        packaged = _json(
            PLUGIN_ROOT / authority / "authority-manifest.v1.json"
        )
        members = {row["path"]: row for row in packaged["members"]}
        relative = f"{authority}/{authority}_sqlite.sqlite"
        assert members[relative]["sha256"] == sha256_file(database)
        assert packaged["duplicate_authority_copy_present"] is False
