from __future__ import annotations

from typing import Any

from evidence_lane_plugin.codex_turn_control import package_surface_inventory
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.mcp_apps import build_project_panel_snapshot

from .conftest import build_and_approve_pv1

EXACT_BINDING_TASK_ID = "EL-CODEX-EXACT_TASK_PROJECT_SESSION_BINDING-PROPOSAL-03"
PREPARE_TASK_ID = "EL-CODEX-TURN_PREPARE_CAPTURE-PROPOSAL-04"


def _native_route_receipt() -> dict[str, Any]:
    return {
        "schema": "evidence-lane.native-mcp-route-receipt.v1",
        "status": "PASS",
        "server_identity": "evidence-lane",
        "canonical_tool_namespace": "mcp__evidence_lane__",
        "exposure_profile": "FULL_LIFECYCLE",
        "tool_count": 62,
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


def _exact_binding_proof(service, session_id: str) -> dict[str, Any]:
    session = service.sessions.load("book-faires", session_id)
    pointer = service.store.pointer("book-faires")
    backlog = service.task_backlog("book-faires")
    active_goal = next(
        row
        for row in backlog["goal_projection"]["rows"]
        if row["status"] == "in_progress"
    )
    surface = package_surface_inventory()
    body = {
        "schema": "evidence-lane.codex-exact-task-project-session-binding.v1",
        "status": "PASS",
        "project_id": "book-faires",
        "evidence_session_id": session_id,
        "codex_thread_id": "019ff25a-30f6-7382-993d-12c5979d696d",
        "task_uri": "codex://threads/019ff25a-30f6-7382-993d-12c5979d696d",
        "task_uri_sha256": "B" * 64,
        "governed_host_session_id": session.metadata["current_host_session_id"],
        "active_plan_row": active_goal,
        "accepted_pointer": {
            "accepted_pv": pointer.accepted_pv,
            "generation": pointer.generation,
            "manifest_sha256": pointer.accepted_manifest_sha256,
            "package_sha256": "C" * 64,
        },
        "running_plugin": {
            "plugin_id": "evidence-lane-plugin",
            "plugin_version": surface["plugin_version"],
            "plugin_selector": "evidence-lane-plugin@test-stable",
            "archive_sha256": "D" * 64,
            "install_receipt_sha256": "E" * 64,
            "surface_inventory_sha256": surface["surface_inventory_sha256"],
            "catalog": surface["catalog"],
        },
        "runtime_task_id": session.task["task_id"],
        "task_binding_receipt_sha256": "F" * 64,
        "preparation_receipt_sha256": "1" * 64,
        "binding_epoch_sha256": "2" * 64,
        "turn_binding_sha256": "3" * 64,
        "identity_basis": (
            "EXACT_CODEX_THREAD_RECEIPT_PLUS_NATIVE_PLAN_AND_POINTER"
        ),
        "task_title_used": False,
        "cwd_used": False,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "sealed_at": "2026-08-12T09:40:00Z",
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def test_exact_binding_checkpoint_advances_without_candidate_and_replays(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    exact_binding = {
        "task_id": EXACT_BINDING_TASK_ID,
        "task_class": "add_bounded_feature",
        "requested_outcome": "Bind the exact task independently of title or CWD.",
        "permitted_paths": ["src/app.py"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["One sealed exact binding receipt."],
        "stop_condition": "Stop after exact binding proof.",
    }
    successor = {
        "task_id": PREPARE_TASK_ID,
        "task_class": "fix_bug",
        "requested_outcome": "Prepare and index every visible prompt before reasoning.",
        "permitted_paths": ["src/app.py"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["Every visible prompt has one PREPARE receipt."],
        "stop_condition": "Stop after PREPARE proof.",
    }
    final_hil = {
        "task_id": "physically-final-hil-row",
        "task_class": "verify_result",
        "requested_outcome": "Present the physically final HIL.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Every predecessor passed."],
        "stop_condition": "Stop at HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[exact_binding, successor, final_hil],
        planned_by="human-test",
        plan_id="exact-binding-checkpoint-plan",
    )
    classified = service.sessions.classify(
        "book-faires",
        session_id,
        task_class=exact_binding["task_class"],
        requested_outcome=exact_binding["requested_outcome"],
        permitted_paths=exact_binding["permitted_paths"],
        permitted_tools=exact_binding["permitted_tools"],
        acceptance_checks=exact_binding["acceptance_checks"],
        stop_condition=exact_binding["stop_condition"],
        backlog_task_id=exact_binding["task_id"],
    )
    prior_runtime_task_id = classified["task"]["task_id"]
    session = service.sessions.load("book-faires", session_id)
    session.metadata["current_host_session_id"] = "exact-binding-host-session"
    session.metadata["active_backlog_task_status"] = "ACTIVE"
    service.sessions._save(session)

    pointer_before = service.store.pointer("book-faires").as_dict()
    event_count_before = service.task_backlog("book-faires")["event_count"]
    proof = _exact_binding_proof(service, session_id)
    kwargs = {
        "task_class": successor["task_class"],
        "requested_outcome": successor["requested_outcome"],
        "permitted_paths": successor["permitted_paths"],
        "permitted_tools": successor["permitted_tools"],
        "acceptance_checks": successor["acceptance_checks"],
        "stop_condition": successor["stop_condition"],
        "backlog_task_id": successor["task_id"],
        "_native_route_receipt": _native_route_receipt(),
        "_installed_surface_inventory": package_surface_inventory(),
        "_project_panel_snapshot": _project_panel(service),
        "_task_checkpoint_proof": proof,
    }
    advanced = service.sessions.classify("book-faires", session_id, **kwargs)
    receipt = advanced["task_checkpoint_advance"]["receipt"]
    assert receipt["verification_kind"] == "EXACT_TASK_PROJECT_SESSION_BINDING"
    assert receipt["prior_runtime_task_id"] == prior_runtime_task_id
    assert receipt["verification_proof"] == proof
    assert receipt["candidate_created"] is False
    assert receipt["pending_hil"] is False
    assert receipt["pointer_moved"] is False
    assert receipt["hil_inferred"] is False
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    backlog = service.task_backlog("book-faires")
    assert backlog["event_count"] == event_count_before + 2
    assert backlog["counts"] == {"ACTIVE": 1, "DONE": 1, "QUEUED": 1}
    assert [row["task_id"] for row in backlog["active"]] == [successor["task_id"]]
    completed = next(
        row for row in backlog["tasks"] if row["task_id"] == exact_binding["task_id"]
    )
    assert completed["task_checkpoint_completion_receipt_sha256"] == receipt[
        "receipt_sha256"
    ]

    replay = service.sessions.classify(
        "book-faires",
        session_id,
        **{**kwargs, "_project_panel_snapshot": _project_panel(service)},
    )
    assert replay["task_checkpoint_advance"]["idempotent_reuse"] is True
    assert service.task_backlog("book-faires")["event_count"] == backlog["event_count"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before
