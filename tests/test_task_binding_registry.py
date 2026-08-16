from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from evidence_lane_plugin.codex_turn_control import (
    package_surface_inventory,
    seal_exact_task_project_session_binding,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.task_binding_registry import (
    read_shared_task_binding,
    seal_or_refresh_shared_task_binding,
)

from .test_codex_turn_control import _strict_state_travel_session

TASK_A = "019ff25a-30f6-7382-993d-12c5979d696d"
TASK_B = "01a0036f-32fa-79b2-8846-9c716d4fe777"
TASK_C = "01a0039c-c6a9-7c5e-a810-1f442a54df21"


def _rebind(
    project_id: str,
    session_id: str,
    task_id: str,
    *,
    approval: str = "A" * 64,
) -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.active-contract-session-rebind.v1",
        "status": "PASS",
        "project_id": project_id,
        "session_id": session_id,
        "task6_thread_id": task_id,
        "approval_receipt_sha256": approval,
        "recovery_binding_contract": {
            "manager_scope": "SHARED_MULTI_PROJECT_MULTI_TASK",
            "registry_mutability": "MUTABLE_APPEND_OR_REFRESH",
            "invocation_binding_scope": "EXACT_CALLING_TASK",
            "reentry_target": task_id,
            "installer_helper": "SEPARATE_COMPONENT",
        },
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _prepare_context(
    application,
    *,
    project_id: str,
    task_id: str,
    plan_task_id: str,
) -> str:
    boot = application.boot_session(
        project_id=project_id,
        user_id="binding-registry-user",
        workspace_id="binding-registry-workspace",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id=f"sandbox-{project_id}",
        ephemeral=False,
        runtime_context={"source": "task-binding-registry-test"},
        host_session_id=f"initial-{project_id}",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    if application.store.pointer(project_id).accepted_pv is None:
        application.build_initial(project_id, session_id)
        decision = application.decide(
            project_id,
            session_id,
            decision="APPROVE",
            decided_by="human-test",
            decision_id=f"decision-binding-{project_id}",
        )
        handoff = decision["state_travel_handoff"]["state_travel"]
        application.resume_state_travel(
            project_id=project_id,
            session_id=session_id,
            handoff_id=handoff["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id=f"accepted-{project_id}",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={"source": "binding-registry-test-entry"},
        )
    active = {
        "task_id": plan_task_id,
        "task_class": "fix_bug",
        "requested_outcome": "Bind the exact calling task to shared authority.",
        "permitted_paths": ["src/**", "tests/**"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["The exact mutable task row is sealed."],
        "stop_condition": "Stop without install, helper, tunnel, HIL, or pointer action.",
    }
    final_hil = {
        "task_id": f"{plan_task_id}-FINAL-HIL",
        "task_class": "verify_result",
        "requested_outcome": "Present the physically final HIL.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Every predecessor passed."],
        "stop_condition": "Stop at HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    application.plan_tasks(
        project_id,
        tasks=[active, final_hil],
        planned_by="human-test",
        plan_id=f"binding-plan-{project_id}",
    )
    application.sessions.classify(
        project_id,
        session_id,
        task_class=active["task_class"],
        requested_outcome=active["requested_outcome"],
        permitted_paths=active["permitted_paths"],
        permitted_tools=active["permitted_tools"],
        acceptance_checks=active["acceptance_checks"],
        stop_condition=active["stop_condition"],
        backlog_task_id=active["task_id"],
    )
    session = application.sessions.load(project_id, session_id)
    session.metadata["current_host_session_id"] = task_id
    session.metadata["active_backlog_task_status"] = "ACTIVE"
    session.metadata["active_contract_rebind_receipt"] = _rebind(
        project_id,
        session_id,
        task_id,
    )
    application.sessions._save(session)
    return session_id


def _switch_task(application, project_id: str, session_id: str, task_id: str) -> None:
    session = application.sessions.load(project_id, session_id)
    session.metadata["current_host_session_id"] = task_id
    session.metadata["active_contract_rebind_receipt"] = _rebind(
        project_id,
        session_id,
        task_id,
    )
    application.sessions._save(session)


def _clone_project(source: Path, destination: Path) -> None:
    subprocess.run(
        ["git", "clone", "--quiet", str(source), str(destination)],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "remote",
            "set-url",
            "origin",
            "https://github.com/example/second-project.git",
        ],
        check=True,
    )


def test_shared_manager_keeps_mutable_exact_task_rows_and_one_release(
    service,
    source_repository: Path,
    tmp_path: Path,
) -> None:
    root = service.store.root
    surface = package_surface_inventory()
    session_a = _prepare_context(
        service,
        project_id="book-faires",
        task_id=TASK_A,
        plan_task_id="EL-TASK-BINDING-REGISTRY-A",
    )
    first = seal_or_refresh_shared_task_binding(
        root,
        project_id="book-faires",
        evidence_session_id=session_a,
        task_id=TASK_A,
        surface=surface,
        bound_by="TEST_FIRST_BIND",
    )
    assert first["revision"] == 1
    assert first["idempotent_reuse"] is False
    assert first["manager_scope"] == "SHARED_MULTI_PROJECT_MULTI_TASK"
    assert first["registry_mutability"] == "MUTABLE_APPEND_OR_REFRESH"
    assert first["invocation_binding_scope"] == "EXACT_CALLING_TASK"
    assert first["installer_helper"] == "SEPARATE_COMPONENT"
    assert first["helper_launched"] is False
    assert first["tunnel_launched"] is False
    assert first["install_executed"] is False

    reused = seal_or_refresh_shared_task_binding(
        root,
        project_id="book-faires",
        evidence_session_id=session_a,
        task_id=TASK_A,
        surface=surface,
        bound_by="TEST_IDEMPOTENT_REENTRY",
    )
    assert reused["idempotent_reuse"] is True
    assert reused["task_binding_receipt_sha256"] == first["task_binding_receipt_sha256"]

    session = service.sessions.load("book-faires", session_a)
    session.metadata["active_contract_rebind_receipt"] = _rebind(
        "book-faires",
        session_a,
        TASK_A,
        approval="B" * 64,
    )
    service.sessions._save(session)
    refreshed = seal_or_refresh_shared_task_binding(
        root,
        project_id="book-faires",
        evidence_session_id=session_a,
        task_id=TASK_A,
        surface=surface,
        bound_by="TEST_REFRESH",
    )
    assert refreshed["revision"] == 2
    assert (
        refreshed["prior_task_binding_receipt_sha256"]
        == first["task_binding_receipt_sha256"]
    )
    history = list((root / "task-binding-registry" / "history" / TASK_A).glob("*.json"))
    assert len(history) == 1

    _switch_task(service, "book-faires", session_a, TASK_B)
    second_task = seal_or_refresh_shared_task_binding(
        root,
        project_id="book-faires",
        evidence_session_id=session_a,
        task_id=TASK_B,
        surface=surface,
        bound_by="TEST_SECOND_TASK",
    )
    assert second_task["revision"] == 1
    assert second_task["release_authority_id"] == first["release_authority_id"]
    with pytest.raises(EvidenceLaneError) as stale:
        read_shared_task_binding(
            root,
            project_id="book-faires",
            evidence_session_id=session_a,
            task_id=TASK_A,
            expected_active_plan_task_id="EL-TASK-BINDING-REGISTRY-A",
            surface=surface,
        )
    assert stale.value.code == "SHARED_TASK_BINDING_REBIND_AUTHORITY_MISMATCH"

    second_repository = tmp_path / "second-project"
    _clone_project(source_repository, second_repository)
    registered = service.register_project(
        project_id="second-project",
        display_name="Second Project",
        repository_path=str(second_repository),
        expected_owner="example",
        expected_name="second-project",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    assert registered["status"] == "PASS"
    session_c = _prepare_context(
        service,
        project_id="second-project",
        task_id=TASK_C,
        plan_task_id="EL-TASK-BINDING-REGISTRY-C",
    )
    second_project = seal_or_refresh_shared_task_binding(
        root,
        project_id="second-project",
        evidence_session_id=session_c,
        task_id=TASK_C,
        surface=surface,
        bound_by="TEST_SECOND_PROJECT",
    )
    assert second_project["release_authority_id"] == first["release_authority_id"]
    assert len(list((root / "release-authorities").glob("*.json"))) == 1
    assert len(list((root / "task-binding-registry").glob("*.json"))) == 3
    assert not (root / "installations").exists()
    assert not (root / "tunnels").exists()


def test_shared_task_binding_and_release_tampering_fail_closed(service) -> None:
    root = service.store.root
    surface = package_surface_inventory()
    session_id = _prepare_context(
        service,
        project_id="book-faires",
        task_id=TASK_A,
        plan_task_id="EL-TASK-BINDING-TAMPER",
    )
    sealed = seal_or_refresh_shared_task_binding(
        root,
        project_id="book-faires",
        evidence_session_id=session_id,
        task_id=TASK_A,
        surface=surface,
        bound_by="TEST_TAMPER",
    )
    current_path = Path(sealed["current_path"])
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    payload["project_id"] = "wrong-project"
    current_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceLaneError) as task_error:
        read_shared_task_binding(
            root,
            project_id="book-faires",
            evidence_session_id=session_id,
            task_id=TASK_A,
            expected_active_plan_task_id="EL-TASK-BINDING-TAMPER",
            surface=surface,
        )
    assert task_error.value.code == "SHARED_TASK_BINDING_MISMATCH"

    current_path.write_text(
        json.dumps(
            {
                k: v
                for k, v in sealed.items()
                if k not in {"idempotent_reuse", "current_path", "release_authority"}
            }
        ),
        encoding="utf-8",
    )
    release_path = Path(sealed["release_authority"]["authority_path"])
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["plugin_version"] = "tampered"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    with pytest.raises(EvidenceLaneError) as release_error:
        read_shared_task_binding(
            root,
            project_id="book-faires",
            evidence_session_id=session_id,
            task_id=TASK_A,
            expected_active_plan_task_id="EL-TASK-BINDING-TAMPER",
            surface=surface,
        )
    assert release_error.value.code == "SHARED_TASK_BINDING_RELEASE_MISMATCH"


def test_exact_binding_checkpoint_uses_shared_registry_without_installer(service) -> None:
    root = service.store.root
    surface = package_surface_inventory()
    active_task_id = "turn-control-row"
    session_id, _ = _strict_state_travel_session(service)
    session = service.sessions.load("book-faires", session_id)
    session.metadata["current_host_session_id"] = TASK_B
    session.metadata["active_contract_rebind_receipt"] = _rebind(
        "book-faires",
        session_id,
        TASK_B,
    )
    if not dict(session.task or {}).get("task_id"):
        session.task = {
            "task_id": "task_ck_shared_registry_integration",
            "task_class": "modify_code",
            "requested_outcome": "Exercise the installer-free exact binding route.",
            "permitted_paths": ["src/**", "tests/**"],
            "permitted_tools": ["repository_write", "test"],
            "acceptance_checks": ["The shared exact task binding passes."],
            "stop_condition": "Stop without install or lifecycle promotion.",
        }
    service.sessions._save(session)
    shared = seal_or_refresh_shared_task_binding(
        root,
        project_id="book-faires",
        evidence_session_id=session_id,
        task_id=TASK_B,
        surface=surface,
        bound_by="TEST_EXACT_BINDING_NO_INSTALLER",
    )
    exact = seal_exact_task_project_session_binding(
        root,
        project_id="book-faires",
        evidence_session_id=session_id,
        expected_active_task_id=active_task_id,
    )
    assert exact["status"] == "PASS"
    assert exact["codex_thread_id"] == TASK_B
    assert exact["task_binding_receipt_sha256"] == shared[
        "task_binding_receipt_sha256"
    ]
    assert exact["identity_basis"].startswith("SHARED_EXACT_TASK_REGISTRY")
    assert exact["installer_helper_invoked"] is False
    assert exact["running_plugin"]["install_receipt_sha256"] is None
    assert not (root / "installations").exists()
