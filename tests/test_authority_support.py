from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.authority_support import (
    AUTHORITY_SUPPORT_PROFILES,
    materialize_missing_authority_tools_contract,
    refresh_authority_support,
    refresh_delta_exit_authority_supports,
    validate_authority_support,
    validate_delta_exit_authority_supports,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_file


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA journal_mode=DELETE;
        CREATE TABLE authority_item(
            item_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE authority_item_fts USING fts5(title, body);
        INSERT INTO authority_item(title,body) VALUES('route','bounded evidence');
        INSERT INTO authority_item_fts(rowid,title,body)
            SELECT item_id,title,body FROM authority_item;
        """
    )
    connection.commit()
    connection.close()


def test_authority_support_rebuilds_graph_from_sqlite_and_tracks_schema(
    tmp_path: Path,
) -> None:
    profile = AUTHORITY_SUPPORT_PROFILES["connector_brain"]
    database = tmp_path / profile.database
    _database(database)
    (tmp_path / profile.mmd).write_text(
        'flowchart TD\n  semantic["Connector semantic route"]\n',
        encoding="utf-8",
    )
    (tmp_path / profile.dot).write_text(
        'digraph connector {\n  semantic [label="Connector semantic route"];\n}\n',
        encoding="utf-8",
    )

    refreshed = refresh_authority_support(tmp_path, "connector_brain")
    traversed = validate_authority_support(tmp_path, "connector_brain")

    assert refreshed["status"] == traversed["status"] == "PASS"
    assert refreshed["semantic_graph_rebuilt_from_owning_sqlite"] is True
    assert refreshed["graph_pipeline_receipt"]["status"] == "PASS"
    assert refreshed["llama_index_refresh_receipt"]["status"] == "PASS"
    assert traversed["tools_role"] == "BUILD_REFRESH_ONLY"
    assert traversed["tools_select_query"] is False
    assert traversed["bootstrap_schema_is_fixed_project_ceiling"] is False
    mmd = (tmp_path / profile.mmd).read_text(encoding="utf-8")
    dot = (tmp_path / profile.dot).read_text(encoding="utf-8")
    assert "Connector semantic route" not in mmd
    assert "authority_item" in mmd
    assert "EVIDENCE_LANE_GRAPH_ENGINE=" in mmd
    assert "EVIDENCE_LANE_GRAPH_ENGINE=PYTHON_GRAPHVIZ" in dot

    connection = sqlite3.connect(database)
    connection.execute("ALTER TABLE authority_item ADD COLUMN project_note TEXT")
    connection.commit()
    connection.close()
    with pytest.raises(EvidenceLaneError) as stale:
        validate_authority_support(tmp_path, "connector_brain")
    assert stale.value.code == "AUTHORITY_SUPPORT_SYSTEM_MISMATCH"

    refresh_authority_support(tmp_path, "connector_brain")
    repaired = validate_authority_support(tmp_path, "connector_brain")
    assert repaired["status"] == "PASS"
    assert "project_note" in (tmp_path / profile.mmd).read_text(encoding="utf-8")


def test_missing_overlay_tools_contract_does_not_refresh_authority_content(
    tmp_path: Path,
) -> None:
    profile = AUTHORITY_SUPPORT_PROFILES["project_overlay"]
    database = tmp_path / profile.database
    _database(database)
    mmd_path = tmp_path / profile.mmd
    dot_path = tmp_path / profile.dot
    manifest_path = tmp_path / profile.manifest
    mmd_path.parent.mkdir(parents=True, exist_ok=True)
    mmd_path.write_text("flowchart LR\n  overlay\n", encoding="utf-8")
    dot_path.write_text("digraph overlay { overlay; }\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps({"schema": "project-overlay-test", "members": []}),
        encoding="utf-8",
    )
    before = {
        "database": sha256_file(database),
        "mmd": sha256_file(mmd_path),
        "dot": sha256_file(dot_path),
    }

    receipt = materialize_missing_authority_tools_contract(
        tmp_path, "project_overlay"
    )

    assert receipt["status"] == "PASS"
    assert receipt["authority_content_refreshed"] is False
    assert receipt["content_sha256s"] == before
    assert sha256_file(database) == before["database"]
    assert sha256_file(mmd_path) == before["mmd"]
    assert sha256_file(dot_path) == before["dot"]
    assert (tmp_path / profile.tools).is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["missing_tools_contract_materialization"] == receipt


def test_delta_exit_support_refresh_excludes_project_overlay(tmp_path: Path) -> None:
    for authority_id in (
        "agent_learning",
        "canon_input",
        "project_memory",
        "source_authority",
        "project_universe",
        "connector_brain",
    ):
        _database(tmp_path / AUTHORITY_SUPPORT_PROFILES[authority_id].database)

    receipt = refresh_delta_exit_authority_supports(tmp_path)
    validation = validate_delta_exit_authority_supports(tmp_path)

    assert receipt["status"] == validation["status"] == "PASS"
    assert receipt["project_overlay_refreshed"] is False
    assert "project_overlay" not in receipt["authority_ids"]
    assert len(validation["traversals"]) == 6
    assert all(row["tools_select_query"] is False for row in validation["traversals"])
