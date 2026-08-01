from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.connector_governance import ConnectorGovernance
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.lineage import LINEAGE_SQLITE_SCHEMA, ChatLineage

from .conftest import boot_local


def test_chat_lineage_sqlite_is_hash_bound_searchable_and_secret_free(
    tmp_path: Path,
) -> None:
    lineage = ChatLineage(tmp_path / "lineage" / "session-test.jsonl")
    event = lineage.append(
        event_type="user.steer",
        visible_payload={
            "prompt": "pursue same HIL",
            "commands": ["python -m pytest"],
            "files": ["README.md"],
            "tests": ["all lanes"],
            "output_links": ["artifact://audit"],
        },
        occurred_at="2026-08-01T12:00:00Z",
        session_id="session-test",
        actor_type="user",
        model="gpt-test",
        submodel="codex",
        token_metrics={"available": 1000, "used": 40},
    )
    status = lineage.projection_status()
    assert status["status"] == "PASS"
    assert status["schema"] == LINEAGE_SQLITE_SCHEMA
    assert status["event_count"] == 1
    assert status["fts_count"] == 1
    assert status["private_reasoning_stored"] is False
    assert status["project_authority"]["event_count"] == 1
    assert status["project_authority"]["head_event_id"] == event["event_id"]
    connection = sqlite3.connect(lineage.sqlite_path)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM lineage_fts WHERE lineage_fts MATCH 'pursue'"
        ).fetchone()[0]
        == 1
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM lineage_link WHERE event_id=?", (event["event_id"],)
        ).fetchone()[0]
        == 4
    )
    connection.close()


def test_flash_projection_is_digest_keyed_and_outside_pv(service) -> None:
    status = service.session_flash_status()["runtime_projection"]
    assert status["status"] == "PASS"
    assert status["source_verification"] == "FULL_BEFORE_REUSE"
    assert status["integrity"] == ["ok"]
    assert status["foreign_key_errors"] == 0
    assert status["row_count"] == status["fts_count"]
    assert "installation" in Path(status["path"]).parts


def test_storage_sidecar_selects_local_and_fails_closed_on_ephemeral(service) -> None:
    initial = service.storage_connector_inspect(
        "book-faires",
        host="CODEX_DESKTOP",
        server_has_durable_filesystem=True,
    )
    assert initial["mode"] == "AUTO"
    assert initial["effective_route"]["mode"] == "local"
    selected = service.storage_connector_select(
        "book-faires",
        mode="LOCAL_SQLITE",
        selected_by="human-test",
        reason="Keep the Codex runtime local and Git-backed.",
        confirmation="SELECT_STORAGE:LOCAL_SQLITE",
    )
    assert selected["mode"] == "LOCAL_SQLITE"
    boot = boot_local(service)
    assert boot["persistence_route"]["mode"] == "local"
    assert boot["persistence_route"]["selection"]["mode"] == "LOCAL_SQLITE"
    assert boot["project_lineage_entry"]["status"] == "PASS"
    assert boot["project_lineage"]["event_count"] >= 1
    with pytest.raises(EvidenceLaneError) as blocked:
        service.storage_connector_inspect(
            "book-faires",
            host="CHATGPT",
            ephemeral=True,
            server_has_durable_filesystem=False,
        )
    assert blocked.value.code == "LOCAL_SQLITE_STORAGE_UNAVAILABLE"


def test_persistent_plugin_grant_records_scope_actions_and_expiry(
    tmp_path: Path,
) -> None:
    governance = ConnectorGovernance(tmp_path / "connector.sqlite")
    registration = governance.register(
        plugin_id="forensic-tool",
        name="Forensic Tool",
        plugin_kind="toolchain",
        description="Runs bounded repository audit checks.",
        config_env_keys=["FORENSIC_TOOL_TOKEN"],
        capabilities=["forensic-audit"],
        allowed_lanes=["github_code"],
        registered_by="human-test",
        purpose="Audit one explicitly named repository.",
        allowed_actions=["forensic-audit"],
        write_scope=["isolated-clone:local-only"],
        expires_at="NO_EXPIRY",
    )
    assert registration["purpose"] == "Audit one explicitly named repository."
    catalog = governance.catalog()
    grant = catalog["registrations"][0]
    assert grant["allowed_actions"] == ["forensic-audit"]
    assert grant["write_scope"] == ["isolated-clone:local-only"]
    assert grant["grant_live"] is True
    assert governance.route(
        capability="forensic-audit", canonical_lane_id="github_code"
    )["selected_plugin_id"] == "forensic-tool"
