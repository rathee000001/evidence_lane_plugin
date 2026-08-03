from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.connector_governance import (
    ConnectorGovernance,
    validate_connector_brain,
)
from evidence_lane_plugin.constants import LINEAGE_SCHEMA
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import (
    atomic_write_bytes,
    canonical_json_bytes,
    sha256_bytes,
)
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


def test_chat_lineage_projects_legacy_events_without_rewriting_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lineage" / "legacy-session.jsonl"
    legacy = {
        "schema": LINEAGE_SCHEMA,
        "event_id": "legacy-user-prompt",
        "event_type": "user.prompt",
        "occurred_at": "2026-07-30T12:00:00Z",
        "session_id": "legacy-session",
        "task_id": None,
        "run_id": None,
        "visible_payload": {"prompt": "preserve this legacy event"},
    }
    legacy["event_sha256"] = sha256_bytes(canonical_json_bytes(legacy))
    original = canonical_json_bytes(legacy)
    atomic_write_bytes(path, original)

    lineage = ChatLineage(path)
    appended = lineage.append(
        event_type="assistant.response",
        visible_payload={"output": "projection compatibility restored"},
        occurred_at="2026-08-01T12:00:00Z",
        session_id="legacy-session",
        event_id="new-assistant-response",
        actor_type="assistant",
    )

    updated = path.read_bytes()
    assert updated.startswith(original)
    assert lineage.events()[0] == legacy
    assert appended["lineage_index"] == 2
    assert appended["previous_event_sha256"] == legacy["event_sha256"]
    status = lineage.projection_status()
    assert status["event_count"] == 2
    assert status["project_authority"]["event_count"] == 2

    connection = sqlite3.connect(lineage.sqlite_path)
    rows = connection.execute(
        "SELECT event_id,lineage_index,actor_type,visible_payload_sha256 "
        "FROM lineage_event ORDER BY lineage_index"
    ).fetchall()
    connection.close()
    assert rows == [
        (
            "legacy-user-prompt",
            1,
            "user",
            sha256_bytes(canonical_json_bytes(legacy["visible_payload"])),
        ),
        (
            "new-assistant-response",
            2,
            "assistant",
            appended["visible_payload_sha256"],
        ),
    ]


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


def test_connector_settings_separate_hosts_and_bind_role_schema_runtime(
    tmp_path: Path,
) -> None:
    path = tmp_path / "connector-settings.sqlite"
    governance = ConnectorGovernance(path)
    codex = governance.register(
        plugin_id="enterprise-sync",
        name="Enterprise Sync",
        plugin_kind="connector",
        description="Maps governed enterprise records into evidence references.",
        config_env_keys=["ENTERPRISE_SYNC_TOKEN"],
        capabilities=["record-sync"],
        allowed_lanes=["custom"],
        registered_by="human-test",
        purpose="Import named enterprise records for the current project.",
        role="enterprise_record_sync",
        role_schema={
            "record_id": "text",
            "recorded_at": "datetime",
            "source_hash": "blob_hash",
        },
        host_profiles=["CODEX"],
        backend_runtime="java",
    )
    chatgpt = governance.register(
        plugin_id="remote-research",
        name="Remote Research",
        plugin_kind="toolchain",
        description="Routes bounded remote research through a host connector.",
        config_env_keys=["REMOTE_RESEARCH_TOKEN"],
        capabilities=["record-sync"],
        allowed_lanes=["custom"],
        registered_by="human-test",
        purpose="Read research records selected by the user.",
        role="remote_research",
        role_schema={"citation_url": "text", "source_hash": "blob_hash"},
        host_profiles=["CHATGPT"],
        backend_runtime="external_mcp",
    )
    assert codex["purpose_recorded_once"] is True
    assert codex["backend_execution_authorized"] is False
    assert chatgpt["host_profiles"] == ["CHATGPT"]
    codex_settings = governance.settings(host_profile="CODEX")
    chatgpt_settings = governance.settings(host_profile="CHATGPT")
    assert codex_settings["configured_count"] == 1
    assert codex_settings["slots"][0]["plugin_id"] == "enterprise-sync"
    assert chatgpt_settings["configured_count"] == 1
    assert chatgpt_settings["slots"][0]["plugin_id"] == "remote-research"
    codex_route = governance.route(
        capability="record-sync",
        canonical_lane_id="custom",
        host_profile="CODEX",
    )
    chatgpt_route = governance.route(
        capability="record-sync",
        canonical_lane_id="custom",
        host_profile="CHATGPT",
    )
    assert codex_route["selected_plugin_id"] == "enterprise-sync"
    assert codex_route["selected_backend_runtime"] == "java"
    assert codex_route["backend_execution_authorized"] is False
    assert chatgpt_route["selected_plugin_id"] == "remote-research"
    validation = validate_connector_brain(path)
    assert validation["valid"] is True
    assert validation["role_schema_field_count"] == 5
    with pytest.raises(EvidenceLaneError) as secret_schema:
        governance.register(
            plugin_id="unsafe-schema",
            name="Unsafe Schema",
            plugin_kind="connector",
            description="Must fail before storing a credential-like field.",
            config_env_keys=["UNSAFE_SCHEMA_TOKEN"],
            capabilities=["unsafe"],
            allowed_lanes=["custom"],
            registered_by="human-test",
            role="unsafe_schema",
            role_schema={"api_key": "text"},
        )
    assert secret_schema.value.code == "PLUGIN_ROLE_SCHEMA_FIELD_INVALID"


def test_connector_route_ambiguity_fails_closed_and_records_ordered_guards(
    tmp_path: Path,
) -> None:
    path = tmp_path / "connector-route-guards.sqlite"
    governance = ConnectorGovernance(path)
    for plugin_id in ("alpha-research", "beta-research"):
        governance.register(
            plugin_id=plugin_id,
            name=plugin_id.replace("-", " ").title(),
            plugin_kind="connector",
            description="Reads one bounded research source.",
            config_env_keys=[f"{plugin_id.replace('-', '_').upper()}_TOKEN"],
            capabilities=["source-read"],
            allowed_lanes=["research"],
            registered_by="human-test",
            purpose="Read a source explicitly selected by the user.",
            allowed_actions=["source-read"],
            write_scope=["lane:research"],
            host_profiles=["CHATGPT"],
            backend_runtime="external_mcp",
        )

    ambiguous = governance.route(
        capability="source-read",
        canonical_lane_id="research",
        host_profile="CHATGPT",
    )
    assert ambiguous["selected_plugin_id"] is None
    assert ambiguous["decision"] == "AMBIGUOUS_FAIL_CLOSED"
    assert ambiguous["eligible_plugin_ids"] == [
        "alpha-research",
        "beta-research",
    ]
    assert ambiguous["guard_order"] == [
        "ACTIVE",
        "GRANT_LIVE",
        "CAPABILITY_AND_ACTION_ALLOWED",
        "LANE_ALLOWED",
        "HOST_ALLOWED",
    ]
    assert all(trace["eligible"] for trace in ambiguous["guard_trace"])

    selected = governance.route(
        capability="source-read",
        canonical_lane_id="research",
        host_profile="CHATGPT",
        preferred_plugin_id="beta-research",
    )
    assert selected["decision"] == "PERSISTENT_PLUGIN"
    assert selected["selected_plugin_id"] == "beta-research"
    assert selected["preferred_plugin_id"] == "beta-research"

    denied = governance.route(
        capability="source-write",
        canonical_lane_id="research",
        host_profile="CHATGPT",
        preferred_plugin_id="beta-research",
    )
    assert denied["selected_plugin_id"] is None
    assert denied["decision"] == "REQUESTED_PLUGIN_DENIED"
    beta_trace = next(
        trace
        for trace in denied["guard_trace"]
        if trace["plugin_id"] == "beta-research"
    )
    assert beta_trace["first_failed_guard"] == "CAPABILITY_AND_ACTION_ALLOWED"

    unknown = governance.route(
        capability="source-read",
        canonical_lane_id="research",
        host_profile="CHATGPT",
        preferred_plugin_id="missing-research",
    )
    assert unknown["selected_plugin_id"] is None
    assert unknown["decision"] == "REQUESTED_PLUGIN_NOT_FOUND"

    no_match = governance.route(
        capability="source-read",
        canonical_lane_id="research",
        host_profile="CODEX",
    )
    assert no_match["selected_plugin_id"] is None
    assert no_match["decision"] == "NO_MATCH_FAIL_CLOSED"
    assert all(
        trace["first_failed_guard"] == "HOST_ALLOWED"
        for trace in no_match["guard_trace"]
    )

    validation = validate_connector_brain(path)
    assert validation["valid"] is True
    assert validation["route_guard_audit_present"] is True
    assert validation["route_guard_audit_valid"] is True
    assert validation["legacy_route_guard_rows"] == 0
    assert validation["ambiguity_fails_closed"] is True

    connection = sqlite3.connect(path)
    connection.execute(
        """
        UPDATE route_decision
        SET candidate_plugin_ids_json = '["alpha-research"]'
        WHERE decision = 'AMBIGUOUS_FAIL_CLOSED'
        """
    )
    connection.commit()
    connection.close()
    tampered = validate_connector_brain(path)
    assert tampered["valid"] is False
    assert tampered["route_guard_audit_valid"] is False
