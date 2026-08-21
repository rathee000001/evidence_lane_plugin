from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from evidence_lane_plugin.codex_turn_control import (
    _build_governed_activity_count_projection,
    _governed_activity_counts_from_database,
)


def _event(
    tool_use_id: str,
    tool_name: str,
    *,
    phase: str = "after",
    visible: str = "{}",
    recorded_at: str = "2026-08-16T10:00:00Z",
) -> dict[str, str]:
    return {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "phase": phase,
        "visible_json_after_redaction": visible,
        "recorded_at": recorded_at,
        "tool_event_sha256": hashlib.sha256(
            f"{tool_use_id}:{phase}:{tool_name}".encode()
        ).hexdigest().upper(),
    }


def test_governed_counts_attribute_without_reparenting_or_raw_payloads() -> None:
    events = [
        _event("git-1", "mcp__github__sync", phase="before"),
        _event("git-1", "mcp__github__sync"),
        _event("vercel-1", "mcp__vercel__deploy"),
        _event("render-1", "render_project_panel"),
        _event("plan-1", "mcp__evidence_lane__pv_status"),
        _event("lineage-1", "mcp__evidence_lane__chat_lineage_query"),
        _event("canon-1", "mcp__evidence_lane__canon_dispatch"),
        _event("learning-1", "mcp__evidence_lane__learning_query"),
        _event("memory-1", "mcp__evidence_lane__record_host_memory_import"),
        _event(
            "test-1",
            "shell_command",
            visible='{"command":"python -m pytest -q tests/test_panel.py"}',
        ),
        _event(
            "install-1",
            "shell_command",
            visible='{"command":"codex plugin add evidence-lane"}',
        ),
    ]

    projection = _build_governed_activity_count_projection(
        events,
        host_ui_supported=True,
    )
    counts = {
        row["class_id"]: row["count"] for row in projection["activity_classes"]
    }
    owners = {
        row["source_plugin"]: row["count"]
        for row in projection["source_plugin_groups"]
    }

    assert projection["projected_tool_use_count"] == 10
    assert projection["completed_tool_use_count"] == 10
    assert counts == {
        "plan_pv": 1,
        "chat_lineage": 1,
        "canon": 1,
        "learning": 1,
        "memory": 1,
        "git_sync": 1,
        "vercel_sync_deployment": 1,
        "render_panels": 1,
        "tests": 1,
        "installation": 1,
    }
    assert owners == {
        "Evidence Lane": 5,
        "GitHub": 1,
        "Vercel": 1,
        "Render": 1,
        "Codex host": 2,
    }
    assert projection["source_actions_reparented"] is False
    assert projection["attribution_only"] is True
    assert projection["host_grouping_capability"] == "SUPPORTED"
    assert projection["raw_tool_input_included"] is False
    assert projection["raw_tool_response_included"] is False
    encoded = json.dumps(projection, sort_keys=True)
    assert "visible_json_after_redaction" not in encoded
    assert "event_json" not in encoded


def test_governed_counts_are_deterministic_when_host_grouping_is_unsupported() -> None:
    events = [
        _event("plan-1", "pv_task_backlog", phase="before"),
        _event("plan-1", "pv_task_backlog", phase="after"),
        _event("plan-1", "pv_task_backlog", phase="after"),
    ]
    first = _build_governed_activity_count_projection(
        events,
        total_tool_use_count=1,
        host_ui_supported=False,
    )
    second = _build_governed_activity_count_projection(
        list(reversed(events)),
        total_tool_use_count=1,
        host_ui_supported=False,
    )

    assert first["projection_sha256"] == second["projection_sha256"]
    assert first["projected_tool_use_count"] == 1
    assert first["activity_classes"] == [
        {"class_id": "plan_pv", "label": "Plan and PV", "count": 1}
    ]
    assert first["host_grouping_capability"] == "UNSUPPORTED"
    assert first["fallback_surface"] == "BOUNDED_STRUCTURED_COUNTER_PROJECTION"
    assert first["lifecycle_mutated"] is False
    assert first["candidate_created_or_accepted"] is False
    assert first["pointer_moved"] is False
    assert first["hil_inferred"] is False


def test_database_projection_survives_reentry_without_writing_receipts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "codex_turn_control.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE turn_entry(
                control_record_sha256 TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                evidence_session_id TEXT NOT NULL
            );
            CREATE TABLE turn_tool_event(
                tool_event_sha256 TEXT PRIMARY KEY,
                control_record_sha256 TEXT NOT NULL,
                tool_use_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                event_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO turn_entry VALUES(?,?,?)",
            ("control-1", "project-1", "session-1"),
        )
        for event in (
            _event("git-1", "mcp__github__sync", phase="before"),
            _event("git-1", "mcp__github__sync", phase="after"),
            _event("render-1", "render_runtime_panel", phase="after"),
        ):
            event_json = json.dumps(
                {
                    "event_payload": {
                        "visible_json_after_redaction": event[
                            "visible_json_after_redaction"
                        ]
                    }
                },
                sort_keys=True,
            )
            connection.execute(
                "INSERT INTO turn_tool_event VALUES(?,?,?,?,?,?,?)",
                (
                    event["tool_event_sha256"],
                    "control-1",
                    event["tool_use_id"],
                    event["phase"],
                    event["tool_name"],
                    event_json,
                    event["recorded_at"],
                ),
            )
        connection.commit()

    before = hashlib.sha256(database.read_bytes()).hexdigest()
    first = _governed_activity_counts_from_database(
        database,
        project_id="project-1",
        evidence_session_id="session-1",
        host_ui_supported=None,
    )
    second = _governed_activity_counts_from_database(
        database,
        project_id="project-1",
        evidence_session_id="session-1",
        host_ui_supported=None,
    )
    after = hashlib.sha256(database.read_bytes()).hexdigest()

    assert before == after
    assert first == second
    assert first["projection_sha256"] == second["projection_sha256"]
    assert first["projected_tool_use_count"] == 2
    assert first["completed_tool_use_count"] == 2
    assert first["host_grouping_capability"] == "UNVERIFIED"
