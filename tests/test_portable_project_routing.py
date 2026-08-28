from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.service import EvidenceLaneService

from .conftest import git


def _repository(parent: Path, name: str) -> Path:
    repository = parent / name
    repository.mkdir(parents=True)
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Evidence Lane Route Test")
    git(repository, "config", "user.email", "route-test@example.invalid")
    git(
        repository,
        "remote",
        "add",
        "origin",
        f"https://github.com/example/{name}.git",
    )
    (repository / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", f"Initialize {name}")
    return repository


def _register(
    service: EvidenceLaneService,
    project_id: str,
    repository: Path,
) -> dict:
    return service.register_project(
        project_id=project_id,
        display_name=project_id,
        repository_path=str(repository),
        expected_owner="example",
        expected_name=repository.name,
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )


def _planned_task(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": f"Verify isolated route {task_id}.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["The project route remains isolated."],
        "stop_condition": "Stop without creating a candidate.",
    }


def test_arbitrary_root_concurrent_projects_and_fresh_process_are_isolated(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "portable root with spaces" / "EvidenceLanePV"
    repositories = tmp_path / "repositories"
    repositories.mkdir()
    alpha_repository = _repository(repositories, "alpha-source")
    beta_repository = _repository(repositories, "beta-source")
    service = EvidenceLaneService(data_root=store_root)

    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha_future = executor.submit(
            _register,
            service,
            "project-alpha",
            alpha_repository,
        )
        beta_future = executor.submit(
            _register,
            service,
            "project-beta",
            beta_repository,
        )
        assert alpha_future.result()["status"] == "PASS"
        assert beta_future.result()["status"] == "PASS"

    registry = json.loads((store_root / "registry.json").read_text(encoding="utf-8"))
    assert sorted(registry["projects"]) == ["project-alpha", "project-beta"]
    assert registry["projects"]["project-alpha"]["canonical_project_key"] == (
        "project-alpha"
    )
    assert service.store.project_root("project-alpha") != service.store.project_root(
        "project-beta"
    )

    service.plan_tasks(
        "project-alpha",
        tasks=[_planned_task("alpha-task")],
        planned_by="route-test",
        plan_id="alpha-plan",
    )
    service.plan_tasks(
        "project-beta",
        tasks=[_planned_task("beta-task")],
        planned_by="route-test",
        plan_id="beta-plan",
    )
    assert [
        task["task_id"]
        for task in service.store.backlog_status("project-alpha")["tasks"]
    ] == ["alpha-task"]
    assert [
        task["task_id"]
        for task in service.store.backlog_status("project-beta")["tasks"]
    ] == ["beta-task"]

    alpha_boot = service.boot_session(
        project_id="project-alpha",
        user_id="route-test",
        workspace_id="alpha-workspace",
        host="CODEX_DESKTOP",
        agent_id="codex-native-alpha",
        sandbox_id="alpha-sandbox",
        ephemeral=False,
        runtime_context={"purpose": "two-project-route-proof"},
        host_session_id="alpha-host-session",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    beta_boot = service.boot_session(
        project_id="project-beta",
        user_id="route-test",
        workspace_id="beta-workspace",
        host="CODEX_DESKTOP",
        agent_id="codex-native-beta",
        sandbox_id="beta-sandbox",
        ephemeral=False,
        runtime_context={"purpose": "two-project-route-proof"},
        host_session_id="beta-host-session",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    alpha_session = alpha_boot["session"]["session_id"]
    beta_session = beta_boot["session"]["session_id"]
    assert alpha_session != beta_session
    assert alpha_boot["persistence_route"]["project_route"]["project_id"] == (
        "project-alpha"
    )
    assert beta_boot["persistence_route"]["project_route"]["project_id"] == (
        "project-beta"
    )
    assert (
        alpha_boot["session"]["metadata"]["runtime_continuity"]["project"][
            "relative_project_route"
        ]
        == "projects/project-alpha"
    )
    assert (
        beta_boot["session"]["metadata"]["runtime_continuity"]["project"][
            "relative_project_route"
        ]
        == "projects/project-beta"
    )
    with pytest.raises(EvidenceLaneError) as cross_project_session:
        service.sessions.load("project-beta", alpha_session)
    assert cross_project_session.value.code == "SESSION_NOT_FOUND"

    activation = service.runtime_activation_status()["active_sessions"]
    assert {(row["project_id"], row["session_id"]) for row in activation} == {
        ("project-alpha", alpha_session),
        ("project-beta", beta_session),
    }

    script = """
import json
from evidence_lane_plugin.service import EvidenceLaneService

service = EvidenceLaneService()
print(json.dumps({
    "root": service.doctor()["store_routing"],
    "alpha": service.status("project-alpha"),
    "beta": service.status("project-beta"),
}, sort_keys=True))
"""
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = str(store_root)
    source_root = (
        Path(__file__).resolve().parents[1] / "plugins" / "evidence-lane-plugin" / "src"
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in [str(source_root), environment.get("PYTHONPATH", "")] if part
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert completed.returncode == 0, completed.stderr
    restarted = json.loads(completed.stdout)
    assert restarted["root"]["resolved_root"] == str(store_root.resolve())
    assert restarted["root"]["configuration_source"] == (
        "HIDDEN_PLUGIN_RUNTIME_CONTROL_ROOT"
    )
    assert restarted["root"]["registered_project_count"] == 2
    assert restarted["alpha"]["project_route"]["relative_project_route"] == (
        "projects/project-alpha"
    )
    assert restarted["beta"]["project_route"]["relative_project_route"] == (
        "projects/project-beta"
    )
    assert restarted["alpha"]["task_backlog"]["count"] == 1
    assert restarted["beta"]["task_backlog"]["count"] == 1


def test_project_and_store_route_injection_collisions_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _repository(tmp_path, "exact-source")
    service = EvidenceLaneService(data_root=tmp_path / "route-store")

    for unsafe_id in (
        "",
        ".hidden",
        "..",
        "../escape",
        "/absolute",
        "C:\\absolute",
        "project/child",
        "project\\child",
        "prøject",
    ):
        with pytest.raises(EvidenceLaneError) as unsafe:
            _register(service, unsafe_id, source)
        assert unsafe.value.code == "PROJECT_ID_INVALID"

    assert _register(service, "Project-Exact", source)["status"] == "PASS"
    with pytest.raises(EvidenceLaneError) as case_collision:
        service.status("project-exact")
    assert case_collision.value.code == "PROJECT_ID_COLLISION"

    with pytest.raises(EvidenceLaneError) as duplicate_source:
        _register(service, "different-project", source)
    assert duplicate_source.value.code == "PROJECT_SOURCE_BINDING_DUPLICATE"
    assert not (service.store.root / "projects" / "different-project").exists()

    unavailable = tmp_path / "not-a-store"
    unavailable.write_text("file boundary\n", encoding="utf-8")
    with pytest.raises(EvidenceLaneError) as unavailable_root:
        EvidenceLaneService(data_root=unavailable)
    assert unavailable_root.value.code == "EVIDENCE_LANE_DATA_ROOT_UNAVAILABLE"

    monkeypatch.setenv("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT", "   ")
    with pytest.raises(EvidenceLaneError) as empty_environment_root:
        EvidenceLaneService()
    assert empty_environment_root.value.code == (
        "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT_INVALID"
    )
