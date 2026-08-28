from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from evidence_lane_plugin import codex_turn_control
from evidence_lane_plugin.codex_turn_control import (
    TurnControlError,
    package_surface_inventory,
    seal_per_delta_local_verification_checkpoint,
)
from evidence_lane_plugin.constants import (
    GOVERNED_SKILL_COUNT,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.git_adapter import calculate_worktree_sha256
from evidence_lane_plugin.hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from evidence_lane_plugin.hook_contract import HOOK_EVENT_NAMES
from evidence_lane_plugin.mcp_apps import build_project_panel_snapshot

from .conftest import build_and_approve_pv1

ACTIVE_TASK_ID = "EL-CODEX-T6-PARITY-TEST-ACTIVE"
SUCCESSOR_TASK_ID = "EL-CODEX-T6-PARITY-TEST-SUCCESSOR"
HOST_TASK_ID = "019ff25a-30f6-7382-993d-12c5979d696d"


def _active_rebind_receipt(session: Any) -> dict[str, Any]:
    runtime_task_id = str(dict(session.task or {}).get("task_id") or "")
    assert runtime_task_id
    body = {
        "schema": "evidence-lane.active-contract-session-rebind.v1",
        "status": "PASS",
        "project_id": "book-faires",
        "session_id": session.session_id,
        "host_task_id": HOST_TASK_ID,
        "active_plan_task_id": ACTIVE_TASK_ID,
        "runtime_task_id": runtime_task_id,
        "authority_route": "PV_PLAN_TASKS_ACTIVE_CONTRACT_REBIND",
        "approval_receipt_sha256": "A" * 64,
        "runtime_task_identity_preserved": True,
        "active_plan_row_identity_preserved": True,
        "governed_session_identity_preserved": True,
        "host_task_identity_preserved": True,
        "task_binding_contract": {
            "manager_scope": "SHARED_MULTI_PROJECT_MULTI_TASK",
            "registry_mutability": "MUTABLE_APPEND_OR_REFRESH",
            "invocation_binding_scope": "EXACT_CALLING_TASK",
            "reentry_target": HOST_TASK_ID,
            "installer_helper": "SEPARATE_COMPONENT",
        },
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "goal_completion_mutated": False,
        "git_executed": False,
        "install_executed": False,
        "helper_launched": False,
        "tunnel_launched": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _native_route_receipt(tool_count: int = NATIVE_TOOL_COUNT) -> dict[str, Any]:
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
        plan_backlog=service.task_backlog("book-faires"),
    )


def _tasks() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    active = {
        "task_id": ACTIVE_TASK_ID,
        "task_class": "fix_bug",
        "requested_outcome": "Implement one normalized parity correction.",
        # A directory authority is equivalent to its bounded descendants. This
        # mirrors live Plan rows that name ``tests`` instead of ``tests/**``.
        "permitted_paths": ["src", "tests"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": [
            "The exact source and test hashes pass bounded local verification."
        ],
        "stop_condition": "Stop before candidate, HIL, pointer, Git, or install.",
        "plan_group": "PV13_RELEASE_GATES",
        "commit_batch_id": "PV13_TASK6_PARITY",
        "dependencies": [],
        "git_commit_stage": "NONE",
    }
    successor = {
        "task_id": SUCCESSOR_TASK_ID,
        "task_class": "modify_code",
        "requested_outcome": "Continue to the exact normalized successor.",
        "permitted_paths": ["src/**", "tests/**"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["The successor remains candidate-free."],
        "stop_condition": "Stop at the next exact local verification boundary.",
        "plan_group": "PV13_RELEASE_GATES",
        "commit_batch_id": "PV13_TASK6_PARITY",
        "dependencies": [ACTIVE_TASK_ID],
        "git_commit_stage": "NONE",
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
    return active, successor, final_hil


def _prepare(service) -> tuple[str, dict[str, Any], dict[str, Any]]:
    session_id, _ = build_and_approve_pv1(service)
    active, successor, final_hil = _tasks()
    service.plan_tasks(
        "book-faires",
        tasks=[active, successor, final_hil],
        planned_by="human-test",
        plan_id="per-delta-local-verification-plan",
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=active["task_class"],
        requested_outcome=active["requested_outcome"],
        permitted_paths=active["permitted_paths"],
        permitted_tools=active["permitted_tools"],
        acceptance_checks=active["acceptance_checks"],
        stop_condition=active["stop_condition"],
        backlog_task_id=active["task_id"],
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["current_host_session_id"] = HOST_TASK_ID
    session.metadata["active_backlog_task_status"] = "ACTIVE"
    rebind = _active_rebind_receipt(session)
    session.metadata["active_contract_rebind_receipt"] = rebind
    session.metadata.setdefault("active_contract_rebinds", []).append(rebind)
    service.sessions._save(session)
    return session_id, active, successor


def _binding(
    service,
    session_id: str,
    *,
    surface: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = service.sessions.load("book-faires", session_id)
    pointer = service.store.pointer("book-faires")
    active_goal = next(
        row
        for row in service.task_backlog("book-faires")["goal_projection"]["rows"]
        if row["task_id"] == ACTIVE_TASK_ID
    )
    active_goal = {
        **active_goal,
        "task_contract_sha256": service.store.plan_runtime_query(
            "book-faires",
            task_id=ACTIVE_TASK_ID,
            limit=1,
        )["contract"]["task_contract_sha256"],
    }
    surface = surface or package_surface_inventory()
    return {
        "governed_host_session_id": session.metadata["current_host_session_id"],
        "active_plan_row": active_goal,
        "accepted_pointer": {
            "accepted_pv": pointer.accepted_pv,
            "generation": pointer.generation,
            "manifest_sha256": pointer.accepted_manifest_sha256,
            "package_sha256": "B" * 64,
        },
        "running_plugin": {
            "plugin_version": surface["plugin_version"],
            "surface_inventory_sha256": surface["surface_inventory_sha256"],
        },
        "receipt_sha256": "C" * 64,
    }


def _verification(service, session_id: str, repository: Path) -> dict[str, Any]:
    session = service.sessions.load("book-faires", session_id)
    pre_worktree_sha256 = session.metadata["entry_freshness"][
        "bound_worktree_sha256"
    ]
    source = repository / "src" / "app.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "verified": True}',
        ),
        encoding="utf-8",
    )
    test_path = repository / "tests" / "test_verified.py"
    test_path.parent.mkdir(exist_ok=True)
    test_path.write_text(
        "def test_verified():\n    assert True\n",
        encoding="utf-8",
    )
    task_contract_sha256 = service.store.plan_runtime_query(
        "book-faires",
        task_id=ACTIVE_TASK_ID,
        limit=1,
    )["contract"]["task_contract_sha256"]
    command = "pytest -q tests/test_verified.py"
    output = "1 passed; negative tampered-hash case rejected"
    active = next(
        row
        for row in service.task_backlog("book-faires")["tasks"]
        if row["task_id"] == ACTIVE_TASK_ID
    )
    return {
        "schema": "evidence-lane.per-delta-local-verification-input.v1",
        "receipt_id": "test-per-delta-local-verification-001",
        "active_task_id": ACTIVE_TASK_ID,
        "task_contract_sha256": task_contract_sha256,
        "dependency_generation": service.store.pointer("book-faires").generation,
        "dependency_task_ids": [],
        "pre_worktree_sha256": pre_worktree_sha256,
        "post_worktree_sha256": calculate_worktree_sha256(repository),
        "changed_paths": [
            {
                "path": "src/app.py",
                "role": "SOURCE",
                "sha256": sha256_file(source),
            },
            {
                "path": "tests/test_verified.py",
                "role": "TEST",
                "sha256": sha256_file(test_path),
            },
        ],
        "test_runs": [
            {
                "selector": "tests/test_verified.py",
                "command": command,
                "command_sha256": sha256_bytes(command.encode("utf-8")),
                "status": "PASS",
                "output": output,
                "output_sha256": sha256_bytes(output.encode("utf-8")),
                "negative_cases": ["A tampered source hash fails before transition."],
            }
        ],
        "acceptance_checks": active["acceptance_checks"],
        "limitations": ["Candidate and HIL remain outside this checkpoint."],
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "git_executed": False,
        "install_executed": False,
    }


def _advance(
    service,
    session_id: str,
    successor: dict[str, Any],
    proof: dict[str, Any],
    *,
    route: dict[str, Any] | None = None,
    surface: dict[str, Any] | None = None,
):
    return service.sessions.classify(
        "book-faires",
        session_id,
        task_class=successor["task_class"],
        requested_outcome=successor["requested_outcome"],
        permitted_paths=successor["permitted_paths"],
        permitted_tools=successor["permitted_tools"],
        acceptance_checks=successor["acceptance_checks"],
        stop_condition=successor["stop_condition"],
        backlog_task_id=successor["task_id"],
        _native_route_receipt=route or _native_route_receipt(),
        _installed_surface_inventory=surface or package_surface_inventory(),
        _project_panel_snapshot=_project_panel(service),
        _task_checkpoint_proof=proof,
    )


def _prior_installed_surface() -> dict[str, Any]:
    surface = deepcopy(package_surface_inventory())
    surface["catalog"] = {
        "tools": NATIVE_TOOL_COUNT - 1,
        "read": NATIVE_READ_TOOL_COUNT,
        "write": NATIVE_WRITE_TOOL_COUNT - 1,
        "skills": GOVERNED_SKILL_COUNT,
    }
    core = {
        key: surface.get(key)
        for key in (
            "schema",
            "plugin_version",
            "hooks",
            "skills",
            "catalog",
            "raw_paths_included",
        )
    }
    surface["surface_inventory_sha256"] = sha256_bytes(
        canonical_json_bytes(core)
    )
    return surface


def _adaptive_deferral_receipt(
    service,
    session_id: str,
    *,
    deferred_to_task_id: str = SUCCESSOR_TASK_ID,
) -> dict[str, Any]:
    pointer = service.store.pointer("book-faires").as_dict()
    hook_names = list(HOOK_EVENT_NAMES)
    body = {
        "schema": "evidence-lane.adaptive-delta-exit-receipt.v1",
        "status": "PASS",
        "project_id": "book-faires",
        "session_id": session_id,
        "task_id": ACTIVE_TASK_ID,
        "install_disposition": {
            "status": "DEFERRED_TO_VERIFIED_BATCH",
            "deferred_to_task_id": deferred_to_task_id,
            "covered_task_ids": [ACTIVE_TASK_ID],
            "install_performed": False,
            "source_scope_sha256": "D" * 64,
        },
        "pointer_before": pointer,
        "pointer_after": pointer,
        "repository_identity_unchanged": True,
        "candidate_created": False,
        "pending_hil_mutated": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "git_mutated": False,
        "plan_task_advanced": False,
        "hook_registry_count": len(hook_names),
        "hook_progression": [
            {
                "hook_name": name,
                "state": "UNCHANGED_INACTIVE",
                "verification_status": "UNVERIFIED",
            }
            for name in hook_names
        ],
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def test_per_delta_receipt_binds_live_hashes_and_advances_exact_successor(
    service,
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _, successor = _prepare(service)
    verification = _verification(service, session_id, source_repository)
    monkeypatch.setattr(
        codex_turn_control,
        "seal_exact_task_project_session_binding",
        lambda *args, **kwargs: _binding(service, session_id),
    )
    pointer_before = service.store.pointer("book-faires").as_dict()
    proof = seal_per_delta_local_verification_checkpoint(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        expected_active_task_id=ACTIVE_TASK_ID,
        verification=verification,
    )
    assert proof["verification_kind"] == "PER_DELTA_LOCAL_VERIFICATION"
    assert proof["delta_verification"]["status"] == "PASS"
    assert proof["delta_verification"]["changed_paths_sha256"] == sha256_bytes(
        canonical_json_bytes(proof["delta_verification"]["changed_paths"])
    )
    advanced = _advance(service, session_id, successor, proof)
    assert advanced["task_checkpoint_advance"]["receipt"][
        "verification_kind"
    ] == "PER_DELTA_LOCAL_VERIFICATION"
    backlog = service.task_backlog("book-faires")
    assert backlog["counts"] == {"ACTIVE": 1, "DONE": 1, "QUEUED": 1}
    assert backlog["active"][0]["task_id"] == SUCCESSOR_TASK_ID
    assert service.store.pointer("book-faires").as_dict() == pointer_before


def test_per_delta_receipt_rejects_tampered_file_without_plan_or_session_write(
    service,
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _, _ = _prepare(service)
    verification = _verification(service, session_id, source_repository)
    verification["changed_paths"][0]["sha256"] = "0" * 64
    monkeypatch.setattr(
        codex_turn_control,
        "seal_exact_task_project_session_binding",
        lambda *args, **kwargs: _binding(service, session_id),
    )
    backlog_path = service.store.project_root("book-faires") / "task_backlog.json"
    session_path = (
        service.store.project_root("book-faires")
        / "sessions"
        / f"{session_id}.json"
    )
    backlog_before = sha256_file(backlog_path)
    session_before = sha256_file(session_path)
    with pytest.raises(TurnControlError) as exc:
        seal_per_delta_local_verification_checkpoint(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            expected_active_task_id=ACTIVE_TASK_ID,
            verification=verification,
        )
    assert exc.value.code == "DELTA_VERIFICATION_CHANGED_PATH_MISMATCH"
    assert sha256_file(backlog_path) == backlog_before
    assert sha256_file(session_path) == session_before


def test_grouped_install_deferral_binds_prior_surface_and_advances(
    service,
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _, successor = _prepare(service)
    prior_surface = _prior_installed_surface()
    verification = _verification(service, session_id, source_repository)
    verification["source_catalog"] = {
        "tools": NATIVE_TOOL_COUNT,
        "read": NATIVE_READ_TOOL_COUNT,
        "write": NATIVE_WRITE_TOOL_COUNT,
        "skills": GOVERNED_SKILL_COUNT,
    }
    verification["adaptive_delta_exit_receipt"] = _adaptive_deferral_receipt(
        service, session_id
    )
    verification["installed_surface_inventory"] = prior_surface
    monkeypatch.setattr(
        codex_turn_control,
        "seal_exact_task_project_session_binding",
        lambda *args, **kwargs: _binding(
            service, session_id, surface=prior_surface
        ),
    )
    proof = seal_per_delta_local_verification_checkpoint(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        expected_active_task_id=ACTIVE_TASK_ID,
        verification=verification,
    )
    assert proof["delta_verification"]["source_catalog"]["tools"] == (
        NATIVE_TOOL_COUNT
    )
    assert proof["running_plugin"]["surface_inventory_sha256"] == (
        prior_surface["surface_inventory_sha256"]
    )
    advanced = _advance(
        service,
        session_id,
        successor,
        proof,
        route=_native_route_receipt(NATIVE_TOOL_COUNT - 1),
        surface=prior_surface,
    )
    assert advanced["task_checkpoint_advance"]["receipt"]["install_deferral"][
        "deferred_to_task_id"
    ] == SUCCESSOR_TASK_ID
    assert service.task_backlog("book-faires")["active"][0]["task_id"] == (
        SUCCESSOR_TASK_ID
    )


def test_grouped_install_deferral_rejects_wrong_or_nonqueued_target(
    service,
    source_repository: Path,
) -> None:
    session_id, _, _ = _prepare(service)
    verification = _verification(service, session_id, source_repository)
    verification["adaptive_delta_exit_receipt"] = _adaptive_deferral_receipt(
        service,
        session_id,
        deferred_to_task_id="not-a-queued-plan-row",
    )
    verification["installed_surface_inventory"] = _prior_installed_surface()
    with pytest.raises(TurnControlError) as exc:
        seal_per_delta_local_verification_checkpoint(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            expected_active_task_id=ACTIVE_TASK_ID,
            verification=verification,
        )
    assert exc.value.code == "DELTA_VERIFICATION_INSTALL_DEFERRAL_MISMATCH"
    assert service.task_backlog("book-faires")["active"][0]["task_id"] == (
        ACTIVE_TASK_ID
    )


def test_normalized_delta_rejects_generic_pass_checkpoint(service) -> None:
    session_id, active, successor = _prepare(service)
    session = service.sessions.load("book-faires", session_id)
    event = service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="test.output",
        event_id="generic-pass-cannot-complete-normalized-delta",
        visible_payload={
            "active_task_id": ACTIVE_TASK_ID,
            "acceptance_checks": active["acceptance_checks"],
            "result": "PASS",
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )["event"]
    binding = _binding(service, session_id)
    proof_body = {
        "schema": "evidence-lane.active-task-acceptance-checkpoint.v1",
        "status": "PASS",
        "verification_kind": "ACTIVE_TASK_ACCEPTANCE",
        "project_id": "book-faires",
        "evidence_session_id": session_id,
        "governed_host_session_id": session.metadata["current_host_session_id"],
        "active_plan_row": binding["active_plan_row"],
        "accepted_pointer": binding["accepted_pointer"],
        "running_plugin": binding["running_plugin"],
        "runtime_task_id": session.task["task_id"],
        "run_id": session.metadata["run_id"],
        "acceptance_contract": {
            "checks": active["acceptance_checks"],
            "checks_sha256": sha256_bytes(
                canonical_json_bytes(active["acceptance_checks"])
            ),
        },
        "acceptance_evidence": {
            key: event.get(key)
            for key in (
                "event_id",
                "event_type",
                "occurred_at",
                "lineage_index",
                "visible_payload_sha256",
                "event_sha256",
            )
        },
        "exact_binding_receipt_sha256": binding["receipt_sha256"],
        "identity_basis": "GENERIC_PASS_ACTIVITY",
        "task_title_used": False,
        "cwd_used": False,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "sealed_at": event["occurred_at"],
    }
    proof = {
        **proof_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(proof_body)),
    }
    with pytest.raises(EvidenceLaneError) as exc:
        _advance(service, session_id, successor, proof)
    assert exc.value.code == "TASK_CHECKPOINT_ADVANCE_DELTA_VERIFICATION_REQUIRED"
    backlog = service.task_backlog("book-faires")
    assert backlog["active"][0]["task_id"] == ACTIVE_TASK_ID
    assert backlog["counts"] == {"ACTIVE": 1, "QUEUED": 2}
