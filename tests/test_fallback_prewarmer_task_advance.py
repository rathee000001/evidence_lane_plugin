from __future__ import annotations

from typing import Any

from evidence_lane_plugin.codex_turn_control import package_surface_inventory
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.mcp_apps import build_project_panel_snapshot

from .conftest import build_and_approve_pv1

FALLBACK_TASK_ID = "EL-CODEX-PV11-FALLBACK-SLOT-INSTALL-PREWARM-DELTA-149"


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


def _fallback_proof(service, session_id: str) -> dict[str, Any]:
    session = service.sessions.load("book-faires", session_id)
    pointer = service.store.pointer("book-faires")
    body = {
        "schema": "evidence-lane.codex-fallback-prewarm-proof.v1",
        "status": "PASS",
        "project_id": "book-faires",
        "session_id": session_id,
        "task_id": "019ff25a-30f6-7382-993d-12c5979d696d",
        "host_session_id": session.metadata["current_host_session_id"],
        "accepted_pv": pointer.accepted_pv,
        "accepted_generation": pointer.generation,
        "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
        "registry_sha256": "B" * 64,
        "fallback_install_receipt_sha256": "C" * 64,
        "task_binding_receipt_sha256": "D" * 64,
        "restart_preparation_receipt_sha256": "E" * 64,
        "config_sha256": "F" * 64,
        "stable_plugin_selector": "evidence-lane-plugin@stable",
        "fallback_plugin_selector": "evidence-lane-plugin@fallback",
        "fallback_package_sha256": "1" * 64,
        "fallback_byte_frozen": True,
        "stable_enabled": True,
        "fallback_enabled": False,
        "enabled_evidence_lane_plugin_count": 1,
        "active_tunnel_count": 0,
        "tunnel_required": False,
        "current_task_recovery_prepared": True,
        "restart_invoked": False,
        "fallback_activated": False,
        "source_mutated": False,
        "git_mutated": False,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "verified_at": "2026-08-12T08:30:17Z",
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def test_verified_disabled_fallback_advances_without_candidate_and_replays(
    service,
    source_repository,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    fallback = {
        "task_id": FALLBACK_TASK_ID,
        "task_class": "verify_result",
        "requested_outcome": "Install and verify the disabled accepted fallback.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read", "test"],
        "acceptance_checks": ["Stable remains enabled and fallback remains disabled."],
        "stop_condition": "Stop without activating fallback or creating a candidate.",
    }
    successor = {
        "task_id": "exact-task-binding-successor",
        "task_class": "fix_bug",
        "requested_outcome": "Bind the exact task independently of title or CWD.",
        "permitted_paths": ["src/app.py"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["The exact task identity is bound."],
        "stop_condition": "Stop at the next governed row.",
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
        tasks=[fallback, successor, final_hil],
        planned_by="human-test",
        plan_id="fallback-prewarmer-task-advance-plan",
    )
    classified = service.sessions.classify(
        "book-faires",
        session_id,
        task_class=fallback["task_class"],
        requested_outcome=fallback["requested_outcome"],
        permitted_paths=fallback["permitted_paths"],
        permitted_tools=fallback["permitted_tools"],
        acceptance_checks=fallback["acceptance_checks"],
        stop_condition=fallback["stop_condition"],
        backlog_task_id=fallback["task_id"],
    )
    prior_runtime_task_id = classified["task"]["task_id"]
    session = service.sessions.load("book-faires", session_id)
    session.metadata["active_backlog_task_status"] = "ACTIVE"
    # A completed State Travel handoff remains immutable session history after
    # its successor becomes active.  That history must never steal dispatch
    # precedence from the independently active fallback-prewarm closeout.
    session.metadata["state_travel"] = {
        "status": "VERIFIED_RESUME_READY",
        "handoff_id": "travel_completed_before_fallback",
    }
    session.metadata["last_state_travel_task_advance"] = {
        "handoff_id": "travel_completed_before_fallback",
        "replacement_backlog_task_id": FALLBACK_TASK_ID,
        "receipt_sha256": "9" * 64,
    }
    service.sessions._save(session)

    pointer_before = service.store.pointer("book-faires").as_dict()
    event_count_before = service.task_backlog("book-faires")["event_count"]
    proof = _fallback_proof(service, session_id)
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
        "_fallback_prewarm_proof": proof,
    }
    advanced = service.sessions.classify("book-faires", session_id, **kwargs)
    receipt = advanced["fallback_prewarmer_task_advance"]["receipt"]
    assert receipt["prior_runtime_task_id"] == prior_runtime_task_id
    assert receipt["fallback_prewarmer_proof"] == proof
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
        row for row in backlog["tasks"] if row["task_id"] == fallback["task_id"]
    )
    assert completed["fallback_prewarmer_completion_receipt_sha256"] == receipt[
        "receipt_sha256"
    ]

    replay = service.sessions.classify(
        "book-faires",
        session_id,
        **{**kwargs, "_project_panel_snapshot": _project_panel(service)},
    )
    assert replay["fallback_prewarmer_task_advance"]["idempotent_reuse"] is True
    assert service.task_backlog("book-faires")["event_count"] == backlog["event_count"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before
