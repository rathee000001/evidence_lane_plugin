from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from evidence_lane_plugin.mcp_server import create_mcp_server

from .conftest import build_and_approve_pv1


def _read_only_task(task_id: str, *, final_hil: bool = False) -> dict:
    task = {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": f"Verify {task_id} without source writes.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read", "test"],
        "acceptance_checks": [f"Verify {task_id}."],
        "stop_condition": f"Stop after {task_id} is verified.",
    }
    if final_hil:
        task["panel_role"] = "PHYSICALLY_FINAL_HIL"
    return task


def _replacement_contract() -> dict:
    return {
        "task_class": "fix_bug",
        "requested_outcome": (
            "Correct the shared mutable Goal-recovery manager so each helper "
            "invocation binds and re-enters its exact calling task while the "
            "separate installer helper retains its own lifecycle."
        ),
        "permitted_paths": ["src/app.py"],
        "permitted_tools": [
            "patch",
            "repository_read",
            "repository_write",
            "test",
        ],
        "acceptance_checks": [
            "The shared manager can retain bindings for multiple projects and tasks.",
            "Each invocation refreshes only its exact task-scoped reentry binding.",
            "The installer helper remains a separate component.",
        ],
        "stop_condition": (
            "Stop before candidate, HIL, pointer, Goal-completion, Git, install, "
            "helper-launch, or tunnel-launch effects."
        ),
        "plan_group": "TASK6_PARITY_EXECUTION",
        "commit_batch_id": "PV13_TASK6_PARITY",
        "dependencies": [],
        "git_commit_stage": "NO_COMMIT",
        "current_version": "3.0.0",
        "current_branch": "main",
    }


def _approval_receipt(
    source_repository: Path,
    *,
    session_id: str,
    task6_thread_id: str,
) -> tuple[str, str]:
    relative = "evidence/task6-active-contract-authority.json"
    path = source_repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "evidence-lane.visible-user-authority-receipt.v1",
        "authority_kind": "HOST_PLAN_EXECUTION_AND_CONTRACT_CORRECTION",
        "author": "user-test",
        "project_id": "book-faires",
        "session_id": session_id,
        "task6_thread_id": task6_thread_id,
        "authorized_effect": "Correct only the stale active execution contract.",
        "identity_invariants": {
            "same_project": True,
            "same_session": True,
            "same_task6": True,
            "same_active_plan_row": True,
            "same_dirty_worktree": True,
            "accepted_pv": "PV1",
            "pointer_generation": 1,
        },
        "not_authorized": [
            "HIL approval or inference",
            "candidate creation or acceptance",
            "PV pointer movement",
            "Goal completion",
            "Git stage, commit, or push",
            "plugin install, helper launch, or tunnel launch",
        ],
    }
    path.write_bytes(canonical_json_bytes(payload))
    return relative, sha256_file(path)


def _prepared_rebind(service, source_repository: Path) -> tuple[str, dict]:
    session_id, _ = build_and_approve_pv1(service)
    active = _read_only_task("active-read-only")
    final_hil = _read_only_task("physically-final-hil", final_hil=True)
    service.plan_tasks(
        "book-faires",
        tasks=[active, final_hil],
        planned_by="human-test",
        plan_id="active-contract-plan",
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
    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "PLAN_GROUP=legacy_group COMMIT_BATCH=legacy_batch "
            "GIT_STAGE=COMMIT"
        ),
        actor="human-test",
        delta_id="legacy-active-contract-directives",
        linked_task_id=active["task_id"],
    )
    session = service.sessions.load("book-faires", session_id)
    assert session.task is not None
    task6_thread_id = "host-session-state-travel-pv1"
    relative, approval_sha256 = _approval_receipt(
        source_repository,
        session_id=session_id,
        task6_thread_id=task6_thread_id,
    )
    backlog = service.task_backlog("book-faires")
    pointer = service.store.pointer("book-faires")
    contract = {
        "rebind_id": "active-contract-rebind-001",
        "session_id": session_id,
        "active_task_id": active["task_id"],
        "expected_runtime_task_id": session.task["task_id"],
        "task6_thread_id": task6_thread_id,
        "expected_backlog_sha256": sha256_bytes(
            canonical_json_bytes(
                service.store._load_backlog("book-faires")
            )
        ),
        "expected_canonical_plan_sha256": backlog[
            "canonical_plan_projection"
        ]["projection_sha256"],
        "expected_executable_projection_sha256": backlog[
            "goal_projection"
        ]["projection_sha256"],
        "expected_session_sha256": service.sessions.session_snapshot_sha256(
            session
        ),
        "expected_pointer_sha256": sha256_bytes(
            canonical_json_bytes(pointer.as_dict())
        ),
        "approval_receipt_path": relative,
        "approval_receipt_sha256": approval_sha256,
        "expected_candidate_absent": True,
        "expected_pending_hil": False,
        "replacement_contract": _replacement_contract(),
    }
    return session_id, contract


def _apply(service, contract: dict) -> dict:
    return service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="active-contract-rebind-plan",
        active_contract_rebind=contract,
    )


def test_active_contract_rebind_preserves_all_execution_identities(
    service,
    source_repository: Path,
) -> None:
    session_id, contract = _prepared_rebind(service, source_repository)
    pointer_before = service.store.pointer("book-faires").as_dict()
    before = service.task_backlog("book-faires")

    result = _apply(service, contract)
    receipt = result["active_contract_rebind"]
    assert receipt["status"] == "PASS"
    assert receipt["journal_phase"] == "COMMITTED"
    assert receipt["idempotent_replay"] is False
    assert receipt["active_task_id"] == "active-read-only"
    assert receipt["runtime_task_id"] == contract["expected_runtime_task_id"]
    assert receipt["task6_thread_id"] == "host-session-state-travel-pv1"
    assert receipt["task_count"] == 2
    assert result["event_count"] == before["event_count"] + 1
    assert [row["task_id"] for row in result["active"]] == [
        "active-read-only"
    ]
    assert result["tasks"][-1]["task_id"] == "physically-final-hil"
    active = result["active"][0]
    assert active["task_class"] == "fix_bug"
    assert active["current_contract_authority"] == "ACTIVE_CONTRACT_REBIND"
    assert active["git_commit_stage"] == "NO_COMMIT"
    assert len(active["task_contract_amendments"]) == 1
    runtime_row = service.store.plan_runtime_query(
        "book-faires", task_id="active-read-only"
    )["row"]
    assert runtime_row["plan_group"] == "TASK6_PARITY_EXECUTION"
    assert runtime_row["plan_group_source"] == "ACTIVE_CONTRACT_REBIND"
    assert runtime_row["commit_batch_id"] == "PV13_TASK6_PARITY"
    assert runtime_row["commit_batch_source"] == "ACTIVE_CONTRACT_REBIND"
    assert json.loads(runtime_row["dependencies_json"]) == []
    assert runtime_row["dependency_source"] == "ACTIVE_CONTRACT_REBIND"
    assert runtime_row["git_commit_stage"] == "NO_COMMIT"
    assert runtime_row["git_commit_stage_source"] == "ACTIVE_CONTRACT_REBIND"
    assert runtime_row["version_marker"] == "3.0.0"
    assert runtime_row["version_marker_source"] == "EXPLICIT_TASK_CONTRACT"
    assert runtime_row["branch_marker"] == "main"
    assert runtime_row["branch_marker_source"] == "EXPLICIT_TASK_CONTRACT"

    session = service.sessions.load("book-faires", session_id)
    assert session.task is not None
    assert session.task["task_id"] == contract["expected_runtime_task_id"]
    assert session.task["task_class"] == "fix_bug"
    assert session.task["write_boundary"] == "AUTHORIZED_SANDBOX_PATHS_ONLY"
    assert session.metadata["active_backlog_task_id"] == "active-read-only"
    assert (
        session.metadata["current_host_session_id"]
        == "host-session-state-travel-pv1"
    )
    binding = session.metadata["task_classification_binding"]
    assert binding["runtime_task_id"] == contract["expected_runtime_task_id"]
    assert binding["authority_boundary"]["write_boundary"] == (
        "AUTHORIZED_SANDBOX_PATHS_ONLY"
    )
    rebind = session.metadata["active_contract_rebinds"][0]
    assert rebind["recovery_binding_contract"] == {
        "manager_scope": "SHARED_MULTI_PROJECT_MULTI_TASK",
        "registry_mutability": "MUTABLE_APPEND_OR_REFRESH",
        "invocation_binding_scope": "EXACT_CALLING_TASK",
        "reentry_target": "host-session-state-travel-pv1",
        "installer_helper": "SEPARATE_COMPONENT",
    }
    assert session.candidate_id is None
    assert not session.metadata.get("pending_hil")
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    replay = _apply(service, contract)
    assert replay["active_contract_rebind"]["idempotent_replay"] is True
    assert replay["event_count"] == result["event_count"]
    assert len(replay["active"][0]["task_contract_amendments"]) == 1
    lineage_path = (
        service.store.project_root("book-faires")
        / "lineage"
        / f"{session_id}.jsonl"
    )
    lineage = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert sum(
        row["event_type"] == "task.active_contract.rebound"
        for row in lineage
    ) == 1


@pytest.mark.parametrize(
    "field",
    [
        "expected_backlog_sha256",
        "expected_canonical_plan_sha256",
        "expected_executable_projection_sha256",
        "expected_session_sha256",
        "expected_pointer_sha256",
    ],
)
def test_active_contract_rebind_hash_mismatch_writes_nothing(
    service,
    source_repository: Path,
    field: str,
) -> None:
    session_id, contract = _prepared_rebind(service, source_repository)
    bad = copy.deepcopy(contract)
    bad[field] = "0" * 64
    backlog_before = sha256_bytes(
        canonical_json_bytes(service.store._load_backlog("book-faires"))
    )
    session_before = service.sessions.session_snapshot_sha256(
        service.sessions.load("book-faires", session_id)
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        _apply(service, bad)

    assert blocked.value.code == "ACTIVE_CONTRACT_REBIND_PRECONDITION_MISMATCH"
    assert sha256_bytes(
        canonical_json_bytes(service.store._load_backlog("book-faires"))
    ) == backlog_before
    assert service.sessions.session_snapshot_sha256(
        service.sessions.load("book-faires", session_id)
    ) == session_before
    assert not (
        service.store.project_root("book-faires")
        / "active_contract_rebindings"
    ).exists()


def test_active_contract_rebind_recovers_after_plan_amend_before_phase_write(
    service,
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, contract = _prepared_rebind(service, source_repository)
    original = service.sessions._write_active_contract_rebind_journal
    crashed = False

    def interrupt_plan_amended(project_id, rebind_id, journal):
        nonlocal crashed
        if journal.get("phase") == "PLAN_AMENDED" and not crashed:
            crashed = True
            raise RuntimeError("simulated active-contract journal interruption")
        original(project_id, rebind_id, journal)

    monkeypatch.setattr(
        service.sessions,
        "_write_active_contract_rebind_journal",
        interrupt_plan_amended,
    )
    with pytest.raises(
        RuntimeError, match="simulated active-contract journal interruption"
    ):
        _apply(service, contract)
    amended = service.task_backlog("book-faires")
    assert len(amended["active"][0]["task_contract_amendments"]) == 1
    monkeypatch.setattr(
        service.sessions,
        "_write_active_contract_rebind_journal",
        original,
    )

    recovered = _apply(service, contract)
    assert recovered["active_contract_rebind"]["status"] == "PASS"
    assert recovered["active_contract_rebind"]["journal_phase"] == "COMMITTED"
    assert len(recovered["active"][0]["task_contract_amendments"]) == 1


def test_active_contract_rebind_binds_legacy_capture_route_before_lineage(
    service,
    source_repository: Path,
) -> None:
    _, contract = _prepared_rebind(service, source_repository)
    project_root = service.store.project_root("book-faires")
    (project_root / "capture_route.json").unlink()

    result = _apply(service, contract)

    receipt = result["active_contract_rebind"]
    assert receipt["status"] == "PASS"
    assert receipt["capture_route"] == "GOVERNED_PROJECT_FULL"
    assert len(receipt["capture_route_binding_sha256"]) == 64
    assert (project_root / "capture_route.json").is_file()
    assert (
        project_root / "lineage" / f"{contract['session_id']}.jsonl"
    ).is_file()


def test_committed_rebind_refreshes_only_stale_derived_plan_sqlite(
    service,
    source_repository: Path,
) -> None:
    _, contract = _prepared_rebind(service, source_repository)
    first = _apply(service, contract)
    event_count = first["event_count"]
    backlog_before = sha256_bytes(
        canonical_json_bytes(service.store._load_backlog("book-faires"))
    )
    projection_path = service.store._plan_runtime_path("book-faires")
    projection_path.unlink()

    replay = _apply(service, contract)

    receipt = replay["active_contract_rebind"]
    refresh = receipt["plan_runtime_refresh"]
    assert receipt["idempotent_replay"] is True
    assert refresh["status"] == "PASS"
    assert refresh["projection_rebuilt"] is True
    assert refresh["canonical_backlog_mutated"] is False
    assert refresh["goal_completion_mutated"] is False
    assert refresh["candidate_created"] is False
    assert refresh["hil_invoked"] is False
    assert refresh["pointer_moved"] is False
    assert refresh["git_executed"] is False
    assert refresh["install_executed"] is False
    assert projection_path.is_file()
    assert replay["event_count"] == event_count
    assert sha256_bytes(
        canonical_json_bytes(service.store._load_backlog("book-faires"))
    ) == backlog_before
    detail = service.store.plan_runtime_query(
        "book-faires", task_id="active-read-only"
    )
    assert detail["row"]["plan_group"] == "TASK6_PARITY_EXECUTION"
    assert detail["row"]["git_commit_stage"] == "NO_COMMIT"


def test_active_contract_rebind_extends_plan_tool_without_catalog_growth(
    service,
) -> None:
    tools = asyncio.run(create_mcp_server(service=service).list_tools())
    assert len(tools) == 83
    plan_tool = next(tool for tool in tools if tool.name == "pv_plan_tasks")
    assert "active_contract_rebind" in plan_tool.inputSchema["properties"]
