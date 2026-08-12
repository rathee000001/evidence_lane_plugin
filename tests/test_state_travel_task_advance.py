from __future__ import annotations

from typing import Any

import pytest
from evidence_lane_plugin.codex_turn_control import package_surface_inventory
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.git_adapter import GitResult
from evidence_lane_plugin.mcp_apps import build_project_panel_snapshot

from .conftest import build_and_approve_pv1, git


def _execution_profile() -> dict[str, str]:
    return {
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
        "service_tier": "standard",
    }


def _native_route_receipt(*, tool_count: int = 62) -> dict[str, Any]:
    return {
        "schema": "evidence-lane.native-mcp-route-receipt.v1",
        "status": "PASS",
        "server_identity": "evidence-lane",
        "canonical_tool_namespace": "mcp__evidence_lane__",
        "exposure_profile": "FULL_LIFECYCLE",
        "tool_count": tool_count,
        "tool_names_unique": True,
        "project_route_argument_required": True,
        "cross_project_fallback_allowed": False,
        "tool_catalog_sha256": "A" * 64,
    }


def _project_panel(service) -> dict[str, Any]:
    return build_project_panel_snapshot(
        project_id="book-faires",
        project_status=service.status("book-faires"),
        public_site_url="https://example.invalid/evidence-lane",
    )


def test_verified_state_travel_handoff_advances_without_candidate_and_replays_once(
    service,
    source_repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    handoff_task = {
        "task_id": "state-travel-handoff-row",
        "task_class": "verify_result",
        "requested_outcome": "Verify the exact unfinished-work handoff.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read", "test"],
        "acceptance_checks": ["The handoff is consumed exactly once."],
        "stop_condition": "Stop before successor implementation.",
    }
    successor_task = {
        "task_id": "successor-implementation-row",
        "task_class": "fix_bug",
        "requested_outcome": "Repair the bounded successor defect.",
        "permitted_paths": ["src/app.py"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["The bounded defect is corrected."],
        "stop_condition": "Stop at the next governed boundary.",
    }
    final_hil = {
        "task_id": "physically-final-hil-row",
        "task_class": "verify_result",
        "requested_outcome": "Present the physically final six-way HIL.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["All predecessors have passed."],
        "stop_condition": "Stop at the six-way HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[handoff_task, successor_task, final_hil],
        planned_by="human-test",
        plan_id="verified-state-travel-task-advance-plan",
    )
    classified = service.sessions.classify(
        "book-faires",
        session_id,
        task_class=handoff_task["task_class"],
        requested_outcome=handoff_task["requested_outcome"],
        permitted_paths=handoff_task["permitted_paths"],
        permitted_tools=handoff_task["permitted_tools"],
        acceptance_checks=handoff_task["acceptance_checks"],
        stop_condition=handoff_task["stop_condition"],
        backlog_task_id=handoff_task["task_id"],
    )
    prior_runtime_task_id = classified["task"]["task_id"]
    session = service.sessions.load("book-faires", session_id)
    session.metadata["active_backlog_task_status"] = "DONE"
    service.sessions._save(session)

    git(source_repository, "switch", "-c", "agent/state-travel-successor")
    head = git(source_repository, "rev-parse", "HEAD")
    from evidence_lane_plugin import session as session_module

    real_run_git = session_module.run_git

    def exact_remote_main(repository, args, **kwargs):
        exact_args = [str(value) for value in args]
        if exact_args == [
            "ls-remote",
            "--exit-code",
            "origin",
            "refs/heads/main",
        ]:
            return GitResult(
                args=tuple(exact_args),
                returncode=0,
                stdout=f"{head}\trefs/heads/main\n",
                stderr="",
            )
        return real_run_git(repository, args, **kwargs)

    monkeypatch.setattr(session_module, "run_git", exact_remote_main)
    profile = _execution_profile()
    prepared = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={"execution_profile": profile},
    )["state_travel"]
    resumed = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=prepared["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="state-travel-successor-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"execution_profile": profile},
    )
    assert resumed["status"] == "PASS"
    assert resumed["state_travel"]["status"] == "VERIFIED_RESUME_READY"

    pointer_before = service.store.pointer("book-faires").as_dict()
    backlog_before = service.task_backlog("book-faires")
    event_count_before = backlog_before["event_count"]
    assert [row["task_id"] for row in backlog_before["active"]] == [
        handoff_task["task_id"]
    ]

    classify_kwargs = {
        "task_class": successor_task["task_class"],
        "requested_outcome": successor_task["requested_outcome"],
        "permitted_paths": successor_task["permitted_paths"],
        "permitted_tools": successor_task["permitted_tools"],
        "acceptance_checks": successor_task["acceptance_checks"],
        "stop_condition": successor_task["stop_condition"],
        "backlog_task_id": successor_task["task_id"],
        "_installed_surface_inventory": package_surface_inventory(),
        "_project_panel_snapshot": _project_panel(service),
    }
    with pytest.raises(EvidenceLaneError) as invalid_route:
        service.sessions.classify(
            "book-faires",
            session_id,
            **classify_kwargs,
            _native_route_receipt=_native_route_receipt(tool_count=61),
        )
    assert invalid_route.value.code == "STATE_TRAVEL_TASK_ADVANCE_NATIVE_ROUTE_MISMATCH"
    assert service.task_backlog("book-faires")["event_count"] == event_count_before
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    real_lineage_append = session_module.ChatLineage.append
    failed_classification_event = False

    def fail_classification_lineage_once(lineage, **kwargs):
        nonlocal failed_classification_event
        if (
            kwargs.get("event_type") == "task.classified"
            and str(kwargs.get("event_id") or "").startswith(
                "evt_st_advance_classified_"
            )
            and not failed_classification_event
        ):
            failed_classification_event = True
            raise RuntimeError("simulated lineage projection interruption")
        return real_lineage_append(lineage, **kwargs)

    monkeypatch.setattr(
        session_module.ChatLineage,
        "append",
        fail_classification_lineage_once,
    )
    with pytest.raises(RuntimeError, match="simulated lineage"):
        service.sessions.classify(
            "book-faires",
            session_id,
            **classify_kwargs,
            _native_route_receipt=_native_route_receipt(),
        )
    persisted = service.sessions.load("book-faires", session_id)
    receipt = persisted.metadata["last_state_travel_task_advance"]
    assert receipt["schema"] == (
        "evidence-lane.verified-state-travel-task-advance.v1"
    )
    assert receipt["prior_runtime_task_id"] == prior_runtime_task_id
    assert receipt["candidate_created"] is False
    assert receipt["pending_hil"] is False
    assert receipt["pointer_moved"] is False
    assert receipt["hil_inferred"] is False
    assert persisted.candidate_id is None
    assert not persisted.metadata.get("pending_hil")
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    backlog_after = service.task_backlog("book-faires")
    assert backlog_after["event_count"] == event_count_before + 2
    assert backlog_after["counts"] == {"ACTIVE": 1, "DONE": 1, "QUEUED": 1}
    assert [row["task_id"] for row in backlog_after["active"]] == [
        successor_task["task_id"]
    ]
    completed = next(
        row
        for row in backlog_after["tasks"]
        if row["task_id"] == handoff_task["task_id"]
    )
    assert completed["status"] == "DONE"
    assert completed["state_travel_completion_receipt_sha256"] == (
        receipt["receipt_sha256"]
    )
    projection = backlog_after["goal_projection"]["rows"]
    assert [row["status"] for row in projection] == [
        "completed",
        "in_progress",
        "pending",
    ]
    assert projection[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"

    monkeypatch.setattr(
        session_module.ChatLineage,
        "append",
        real_lineage_append,
    )
    replay = service.sessions.classify(
        "book-faires",
        session_id,
        **{
            **classify_kwargs,
            "_project_panel_snapshot": _project_panel(service),
        },
        _native_route_receipt=_native_route_receipt(),
    )
    assert replay["state_travel_task_advance"]["idempotent_reuse"] is True
    assert replay["state_travel_task_advance"]["receipt"] == receipt
    lineage = session_module.ChatLineage(
        service.sessions._lineage_path("book-faires", session_id)
    ).events()
    advance_events = [
        row
        for row in lineage
        if str(row.get("event_id") or "").startswith("evt_st_advance_")
    ]
    assert [row["event_type"] for row in advance_events] == [
        "task.state_travel_handoff.completed",
        "task.classified",
    ]
    assert service.task_backlog("book-faires")["event_count"] == (
        backlog_after["event_count"]
    )
    assert service.store.pointer("book-faires").as_dict() == pointer_before
