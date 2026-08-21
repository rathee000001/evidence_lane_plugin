from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.plan_runtime import (
    append_task_formula_event,
    plan_runtime_status,
    query_plan_runtime_projection,
    write_plan_runtime_projection,
)


def _backlog() -> dict:
    return {
        "schema": "evidence-lane.linear-task-backlog.v1",
        "project_id": "formula-project",
        "plans": [
            {
                "plan_id": "formula-plan",
                "planned_by": "human-test",
            }
        ],
        "tasks": [
            {
                "task_id": "DELTA-001",
                "sequence": 1,
                "plan_id": "formula-plan",
                "task_class": "fix_bug",
                "requested_outcome": "Prove task-specific formula lineage.",
                "permitted_paths": ["src"],
                "permitted_tools": ["repository_read", "test"],
                "acceptance_checks": ["Formula lineage is append-only."],
                "stop_condition": "Stop on formula mismatch.",
                "status": "ACTIVE",
                "planned_at": "2026-08-21T00:00:00Z",
                "panel_role": "STANDARD",
                "steer_deltas": [],
            }
        ],
    }


def _formula(expected: str) -> dict:
    return {
        "fired_modes": ["validation", "source_intake"],
        "operators": ["INTERSECTION", "VALIDATE"],
        "bounded_source_locators": ["working:local_code:chunk:1"],
        "sector_locators": ["local_code"],
        "env_uop_terms": ["ENV15", "UOP15"],
        "assumptions": ["workspace identity is exact"],
        "intended_validator": "targeted pytest plus Plan SQLite integrity",
        "expected_result": expected,
        "formula_expression": "V((MODE intersect SOURCE) intersect AUTHORITY)",
    }


def _exit_formula() -> dict:
    return {
        "formula_expression": "V_DELTA(TESTS intersect RECEIPTS) = PASS",
        "bounded_input_locators": ["workspace:src/app.py"],
        "modes_fired": ["validation"],
        "operators_fired": ["INTERSECTION", "VALIDATE"],
        "target_outcome": "complete one bounded Delta",
        "achieved_outcome": "targeted checks passed",
        "validator_results": [{"name": "pytest", "status": "PASS"}],
        "delta_ledger": {"PASS": ["tests"], "FAIL": [], "OPEN": []},
        "source_freshness": {"state": "STALE", "query_status": "PASS"},
        "code_test_install_receipts": {
            "code": "A" * 64,
            "tests": "B" * 64,
            "install": "INSTALL_DEFERRED_TO_VERIFIED_BATCH",
        },
    }


def test_task_formula_lineage_is_append_only_and_projected(tmp_path: Path) -> None:
    backlog = _backlog()
    entry = append_task_formula_event(
        backlog,
        task_id="DELTA-001",
        event_kind="ENTRY_FORMULA",
        source_event_id="chat-event-1",
        session_id="session-1",
        formula=_formula("entry passes"),
        actor="human-test",
        event_id="formula-entry-1",
    )
    replay = append_task_formula_event(
        backlog,
        task_id="DELTA-001",
        event_kind="ENTRY_FORMULA",
        source_event_id="chat-event-1",
        session_id="session-1",
        formula=_formula("entry passes"),
        actor="human-test",
        event_id="formula-entry-1",
    )
    assert replay == entry
    assert len(backlog["task_formula_events"]) == 1

    mutation = append_task_formula_event(
        backlog,
        task_id="DELTA-001",
        event_kind="MUTATION",
        source_event_id="chat-event-2",
        session_id="session-1",
        formula=_formula("mutated entry passes"),
        actor="human-test",
        prior_formula_sha256=entry["formula_sha256"],
        changed_terms={"expected_result": "mutated entry passes"},
        cause_evidence_locator="steer:TASK8_FORMULA_LAW",
        event_id="formula-mutation-1",
    )
    assert mutation["prior_formula_sha256"] == entry["formula_sha256"]

    projection = tmp_path / "plan_runtime_projection.sqlite"
    write_plan_runtime_projection(projection, backlog)
    status = plan_runtime_status(projection, backlog)
    assert status["status"] == "PASS"
    assert status["sqlite_user_version"] == 3
    assert status["task_formula_event_count"] == 2
    assert status["task_formula_lineage_indexed"] is True

    exact = query_plan_runtime_projection(projection, task_id="DELTA-001", limit=10)
    assert [row["event_kind"] for row in exact["formula_events"]] == [
        "ENTRY_FORMULA",
        "MUTATION",
    ]
    fts = query_plan_runtime_projection(
        projection, query="mutated entry passes", limit=10
    )
    assert any(hit["source_kind"] == "TASK_FORMULA" for hit in fts["hits"])
    connection = sqlite3.connect(projection)
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM task_formula_event").fetchone()[0]
            == 2
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM plan_execution_row").fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_task_formula_mutation_requires_exact_prior_hash() -> None:
    backlog = _backlog()
    append_task_formula_event(
        backlog,
        task_id="DELTA-001",
        event_kind="ENTRY_FORMULA",
        source_event_id="chat-event-1",
        session_id="session-1",
        formula=_formula("entry passes"),
        actor="human-test",
    )
    with pytest.raises(EvidenceLaneError) as raised:
        append_task_formula_event(
            backlog,
            task_id="DELTA-001",
            event_kind="MUTATION",
            source_event_id="chat-event-2",
            session_id="session-1",
            formula=_formula("wrong prior rejected"),
            actor="human-test",
            prior_formula_sha256="0" * 64,
            changed_terms={"expected_result": "wrong prior rejected"},
            cause_evidence_locator="steer:wrong",
        )
    assert raised.value.code == "TASK_FORMULA_MUTATION_INVALID"
    assert len(backlog["task_formula_events"]) == 1


def test_task_formula_exit_closes_lineage_and_blocks_later_mutation() -> None:
    backlog = _backlog()
    entry = append_task_formula_event(
        backlog,
        task_id="DELTA-001",
        event_kind="ENTRY_FORMULA",
        source_event_id="chat-event-1",
        session_id="session-1",
        formula=_formula("entry passes"),
        actor="human-test",
    )
    exit_event = append_task_formula_event(
        backlog,
        task_id="DELTA-001",
        event_kind="EXIT_FORMULA",
        source_event_id="exit-receipt-1",
        session_id="session-1",
        formula=_exit_formula(),
        actor="human-test",
        prior_formula_sha256=entry["formula_sha256"],
        changed_terms={},
        cause_evidence_locator="receipt:delta-exit-1",
    )
    assert exit_event["event_kind"] == "EXIT_FORMULA"
    assert (
        append_task_formula_event(
            backlog,
            task_id="DELTA-001",
            event_kind="EXIT_FORMULA",
            source_event_id="exit-receipt-1",
            session_id="session-1",
            formula=_exit_formula(),
            actor="human-test",
            prior_formula_sha256=entry["formula_sha256"],
            changed_terms={},
            cause_evidence_locator="receipt:delta-exit-1",
        )["event_sha256"]
        == exit_event["event_sha256"]
    )

    with pytest.raises(EvidenceLaneError) as raised:
        append_task_formula_event(
            backlog,
            task_id="DELTA-001",
            event_kind="MUTATION",
            source_event_id="chat-event-2",
            session_id="session-1",
            formula=_formula("late mutation rejected"),
            actor="human-test",
            prior_formula_sha256=exit_event["formula_sha256"],
            changed_terms={"expected_result": "late mutation rejected"},
            cause_evidence_locator="steer:late",
        )
    assert raised.value.code == "TASK_FORMULA_LINEAGE_CLOSED"
    assert len(backlog["task_formula_events"]) == 2
