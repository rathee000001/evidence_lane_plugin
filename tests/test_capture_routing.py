from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.capture_routing import (
    ENV_BUILDER_SPARSE,
    GOVERNED_PROJECT_FULL,
    CaptureRouteAuthority,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import atomic_write_json
from evidence_lane_plugin.lineage import ChatLineage
from evidence_lane_plugin.models import ProjectConfig
from evidence_lane_plugin.store import ProjectStore


def _bound_lineage(
    root: Path,
    *,
    project_id: str,
    route: str,
) -> tuple[ChatLineage, CaptureRouteAuthority]:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": project_id,
            "capture_route": route,
        },
    )
    authority = CaptureRouteAuthority(root)
    authority.bind(
        project_id=project_id,
        route=route,
        selected_by="test-user",
        reason="TEST_EXPLICIT_CAPTURE_ROUTE",
    )
    return ChatLineage(root / "lineage" / "session-test.jsonl"), authority


def _fts_count(path: Path, table: str, query: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {table} MATCH ?",
                (query,),
            ).fetchone()[0]
        )


def test_project_registration_normalizes_and_binds_route_before_ingestion(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path / "store")
    result = store.register_project(
        ProjectConfig(
            project_id="registered-env",
            display_name="Registered ENV",
            repository_path=str(tmp_path / "source"),
            expected_owner="example",
            expected_name="registered-env",
            allowed_branches=["main"],
            capture_route="env-builder-sparse",
        )
    )
    project_root = store.project_root("registered-env")
    project = json.loads((project_root / "project.json").read_text(encoding="utf-8"))

    assert project["capture_route"] == ENV_BUILDER_SPARSE
    assert result["capture_route_binding"]["capture_route"] == ENV_BUILDER_SPARSE
    assert result["capture_route_binding"]["state"] == "BOUND"
    assert (project_root / "capture_route.json").is_file()
    assert not (project_root / "lineage" / "session-test.jsonl").exists()


def test_governed_project_route_keeps_redacted_visible_units_and_fts(
    tmp_path: Path,
) -> None:
    lineage, authority = _bound_lineage(
        tmp_path / "project-full",
        project_id="project-full",
        route=GOVERNED_PROJECT_FULL,
    )
    event = lineage.append(
        event_type="turn.visible_user_prompt",
        visible_payload={
            "project_id": "project-full",
            "prompt": "full-route-searchable-evidence",
        },
        occurred_at="2026-08-14T00:00:00Z",
        session_id="session-test",
        event_id="full-visible-prompt",
    )

    assert event["event_type"] == "turn.visible_user_prompt"
    assert event["visible_payload"]["prompt"] == "full-route-searchable-evidence"
    assert _fts_count(lineage.sqlite_path, "lineage_fts", "searchable") == 1
    route_status = lineage.projection_status()["capture_route"]
    assert route_status["decision_count"] == route_status["included_count"] == 1
    assert route_status["excluded_count"] == 0
    decisions = authority._decisions()
    assert decisions[-1]["capture_route"] == GOVERNED_PROJECT_FULL
    assert decisions[-1]["included"] is True
    assert decisions[-1]["reason_code"].startswith("FULL_ROUTE_")
    assert "full-route-searchable-evidence" not in authority.decision_path.read_text(
        encoding="utf-8"
    )


def test_env_builder_route_keeps_authority_and_excludes_exploratory_text(
    tmp_path: Path,
) -> None:
    lineage, authority = _bound_lineage(
        tmp_path / "project-env",
        project_id="project-env",
        route=ENV_BUILDER_SPARSE,
    )
    prompt = lineage.append(
        event_type="turn.visible_user_prompt",
        visible_payload={
            "project_id": "project-env",
            "prompt": "exploratory-raw-text-must-not-enter-authority",
        },
        occurred_at="2026-08-14T00:00:00Z",
        session_id="session-test",
        event_id="sparse-visible-prompt",
    )
    steer = lineage.append(
        event_type="turn.visible_user_steer",
        visible_payload={
            "project_id": "project-env",
            "steer": "unaccepted-steer-must-not-enter-authority",
        },
        occurred_at="2026-08-14T00:00:01Z",
        session_id="session-test",
        event_id="sparse-visible-steer",
    )
    accepted_delta = lineage.append(
        event_type="delta.lifecycle",
        visible_payload={
            "project_id": "project-env",
            "capture_kind": "ACCEPTED_DELTA",
            "status": "ACCEPTED",
            "delta_id": "DELTA-TEST-001",
            "description": "accepted-delta-searchable-authority",
        },
        occurred_at="2026-08-14T00:00:02Z",
        session_id="session-test",
        event_id="sparse-accepted-delta",
    )
    receipt = lineage.append(
        event_type="task.checkpoint.completed",
        visible_payload={
            "project_id": "project-env",
            "receipt_sha256": "A" * 64,
            "description": "governed-receipt-searchable-authority",
        },
        occurred_at="2026-08-14T00:00:03Z",
        session_id="session-test",
        event_id="sparse-receipt",
    )

    assert prompt["event_type"] == steer["event_type"] == "capture.excluded"
    assert prompt["visible_payload"]["raw_visible_unit_stored"] is False
    assert accepted_delta["event_type"] == "delta.lifecycle"
    assert receipt["event_type"] == "task.checkpoint.completed"
    lineage_text = lineage.path.read_text(encoding="utf-8")
    assert "exploratory-raw-text-must-not-enter-authority" not in lineage_text
    assert "unaccepted-steer-must-not-enter-authority" not in lineage_text
    assert _fts_count(lineage.sqlite_path, "lineage_fts", "exploratory") == 0
    assert _fts_count(lineage.sqlite_path, "lineage_fts", "unaccepted") == 0
    assert _fts_count(lineage.sqlite_path, "lineage_fts", "accepted") == 1
    assert _fts_count(lineage.sqlite_path, "lineage_fts", "governed") == 1
    decisions = authority._decisions()
    assert [row["included"] for row in decisions] == [False, False, True, True]
    assert lineage.projection_status()["capture_route"]["excluded_count"] == 2
    assert decisions[0]["reason_code"] == "SPARSE_ROUTE_EXCLUDED_VISIBLE_PROMPT"
    assert decisions[1]["reason_code"] == "SPARSE_ROUTE_EXCLUDED_VISIBLE_STEER"


def test_capture_route_fails_closed_on_ambiguity_conflict_and_cross_project(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ambiguous-project"
    root.mkdir()
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": "ambiguous-project",
            "capture_route": GOVERNED_PROJECT_FULL,
        },
    )
    lineage = ChatLineage(root / "lineage" / "session-test.jsonl")
    with pytest.raises(EvidenceLaneError) as missing:
        lineage.append(
            event_type="user.prompt",
            visible_payload={"prompt": "must fail before ingestion"},
            occurred_at="2026-08-14T00:00:00Z",
            session_id="session-test",
        )
    assert missing.value.code == "CAPTURE_ROUTE_BINDING_MISSING"
    assert not lineage.path.exists()

    authority = CaptureRouteAuthority(root)
    authority.bind(
        project_id="ambiguous-project",
        route=GOVERNED_PROJECT_FULL,
        selected_by="test-user",
        reason="TEST_EXPLICIT_CAPTURE_ROUTE",
    )
    with pytest.raises(EvidenceLaneError) as conflict:
        authority.bind(
            project_id="ambiguous-project",
            route=ENV_BUILDER_SPARSE,
            selected_by="test-user",
            reason="CONFLICTING_ROUTE",
        )
    assert conflict.value.code == "CAPTURE_ROUTE_BINDING_CONFLICT"

    with pytest.raises(EvidenceLaneError) as crossed:
        lineage.append(
            event_type="user.prompt",
            visible_payload={
                "project_id": "other-project",
                "prompt": "must not cross projects",
            },
            occurred_at="2026-08-14T00:00:01Z",
            session_id="session-test",
        )
    assert crossed.value.code == "CAPTURE_ROUTE_CROSS_PROJECT_BLOCKED"
    assert not lineage.path.exists()


def test_boundary_only_exit_slips_and_source_project_provenance(tmp_path: Path) -> None:
    lineage, authority = _bound_lineage(
        tmp_path / "boundary-project",
        project_id="boundary-project",
        route=ENV_BUILDER_SPARSE,
    )
    with pytest.raises(EvidenceLaneError) as invalid:
        lineage.append(
            event_type="turn.lifecycle_exit_slip",
            visible_payload={
                "project_id": "boundary-project",
                "reason": "ORDINARY_TURN_END",
            },
            occurred_at="2026-08-14T00:00:00Z",
            session_id="session-test",
            event_id="invalid-exit",
        )
    assert invalid.value.code == "CAPTURE_ROUTE_EXIT_SLIP_BOUNDARY_INVALID"

    valid = lineage.append(
        event_type="turn.lifecycle_exit_slip",
        visible_payload={
            "project_id": "boundary-project",
            "source_project_id": "bounded-canon-source",
            "reason": "EXPLICIT_PAUSE",
            "visible_reason": "pause at the governed boundary",
        },
        occurred_at="2026-08-14T00:00:01Z",
        session_id="session-test",
        event_id="valid-exit",
    )
    assert valid["event_type"] == "turn.lifecycle_exit_slip"
    assert authority._decisions()[-1]["capture_kind"] == "EXIT_SLIP"
    assert authority._decisions()[-1]["included"] is True


def test_full_and_sparse_projects_keep_independent_capture_and_fts(tmp_path: Path) -> None:
    full, _ = _bound_lineage(
        tmp_path / "full-project",
        project_id="full-project",
        route=GOVERNED_PROJECT_FULL,
    )
    sparse, _ = _bound_lineage(
        tmp_path / "sparse-project",
        project_id="sparse-project",
        route=ENV_BUILDER_SPARSE,
    )
    full.append(
        event_type="turn.visible_user_prompt",
        visible_payload={"project_id": "full-project", "prompt": "fullonlytoken"},
        occurred_at="2026-08-14T00:00:00Z",
        session_id="session-test",
        event_id="full-only",
    )
    sparse.append(
        event_type="turn.visible_user_prompt",
        visible_payload={
            "project_id": "sparse-project",
            "prompt": "sparseexcludedtoken",
        },
        occurred_at="2026-08-14T00:00:00Z",
        session_id="session-test",
        event_id="sparse-only",
    )

    assert _fts_count(full.sqlite_path, "lineage_fts", "fullonlytoken") == 1
    assert _fts_count(full.sqlite_path, "lineage_fts", "sparseexcludedtoken") == 0
    assert _fts_count(sparse.sqlite_path, "lineage_fts", "sparseexcludedtoken") == 0
    assert _fts_count(sparse.sqlite_path, "lineage_fts", "fullonlytoken") == 0
    assert (
        full.projection_status()["project_authority"]["sqlite_path"]
        != sparse.projection_status()["project_authority"]["sqlite_path"]
    )


def test_capture_decision_replay_is_idempotent_and_hash_chained(tmp_path: Path) -> None:
    lineage, authority = _bound_lineage(
        tmp_path / "replay-project",
        project_id="replay-project",
        route=ENV_BUILDER_SPARSE,
    )
    kwargs = {
        "event_type": "turn.visible_user_prompt",
        "visible_payload": {
            "project_id": "replay-project",
            "prompt": "idempotent-excluded-text",
        },
        "occurred_at": "2026-08-14T00:00:00Z",
        "session_id": "session-test",
        "event_id": "replay-event",
    }
    first = lineage.append(**kwargs)
    second = lineage.append(**kwargs)
    decisions = authority._decisions()

    assert first == second
    assert len(decisions) == 1
    assert decisions[0]["previous_decision_sha256"] is None
    assert json.loads(authority.binding_path.read_text(encoding="utf-8"))[
        "binding_sha256"
    ] == decisions[0]["binding_sha256"]
