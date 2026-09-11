from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import (
    HostPlanBind,
    HostPlanProjectionRead,
    PlanCreate,
    PlanStore,
    TaskDefinition,
)
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task(identity):
    return TaskDefinition(
        task_id=identity,
        title=f"Task {identity}",
        requested_outcome=f"Verify {identity}",
        acceptance_checks=["The selected result passes."],
    )


def test_project_plan_database_projects_the_complete_list_to_exact_bound_host_plan(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "project", source)
    plan = PlanStore(store)
    with WriterLease(store, "engine") as lease:
        plan.create(PlanCreate(title="Projection", tasks=[task("one"), task("two")]), lease, actor_id="client")
    prepared = plan.host_projection()
    assert prepared.state == "unbound"
    assert prepared.row_count == 2 and prepared.full_list and not prepared.partial_window

    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    host_task_id, host_plan_id = str(uuid4()), str(uuid4())
    target = codex_home / "plans" / host_task_id / host_plan_id / "PLAN.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Existing explicitly selected Plan\n", encoding="utf-8")
    before = digest(target)
    request = HostPlanBind(
        host_task_id=host_task_id,
        host_plan_id=host_plan_id,
        plan_path=str(target),
        expected_sha256=before,
    )
    with WriterLease(store, "engine") as lease:
        binding = plan.bind_host_plan(request, lease, actor_id="client")
    assert binding.projection.state == "confirmed"
    assert binding.projection.row_count == 2
    assert binding.projection.file_sha256 == digest(target) == binding.current_sha256
    text = target.read_text(encoding="utf-8")
    assert "No partial 1 + 9 projection" in text
    assert "| 001 | `one` | **queued** | `pending` |" in text
    assert "| 002 | `two` | **queued** | `pending` |" in text
    with WriterLease(store, "engine") as lease:
        repeated = plan.bind_host_plan(request, lease, actor_id="client")
    assert repeated.binding_id == binding.binding_id
    assert repeated.current_sha256 == binding.current_sha256

    with WriterLease(store, "engine") as lease:
        plan.transition("one", "active", lease, expected_revision=1, actor_id="client")
    active = plan.host_projection(HostPlanProjectionRead())
    assert active.state == "confirmed" and active.rows[0]["state"] == "active"
    assert active.rows[0]["host_status"] == "in_progress"
    assert active.rows[1]["host_status"] == "pending"
    assert active.file_sha256 == digest(target)

    target.write_text("# User collision that must be preserved\n", encoding="utf-8")
    collision_sha = digest(target)
    with WriterLease(store, "engine") as lease, pytest.raises(LaneError) as failure:
        plan.transition("one", "blocked", lease, expected_revision=1, actor_id="client")
    assert failure.value.code == "HOST_PLAN_CHANGED"
    pending = plan.host_projection()
    assert pending.state == "pending" and pending.last_error == "HOST_PLAN_CHANGED"
    assert plan.task("one", expected_revision=1).state == "blocked"
    assert digest(target) == collision_sha


def test_host_plan_binding_rejects_a_path_outside_the_current_codex_plan_tree(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "project", source)
    plan = PlanStore(store)
    with WriterLease(store, "engine") as lease:
        plan.create(PlanCreate(title="Projection", tasks=[task("one")]), lease, actor_id="client")
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    outside = tmp_path / "PLAN.md"
    outside.write_text("outside\n", encoding="utf-8")
    request = HostPlanBind(
        host_task_id=str(uuid4()), host_plan_id=str(uuid4()),
        plan_path=str(outside), expected_sha256=digest(outside))
    with WriterLease(store, "engine") as lease, pytest.raises(LaneError) as failure:
        plan.bind_host_plan(request, lease, actor_id="client")
    assert failure.value.code == "HOST_PLAN_PATH_INVALID"
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_host_plan_projection_escapes_task_text_before_markdown_rendering(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "project", source)
    plan = PlanStore(store)
    hostile = TaskDefinition(
        task_id="hostile",
        title='<img src=x onerror=alert(1)> [open](https://invalid.example)',
        requested_outcome='Keep | `code` and *markup* as text.',
        acceptance_checks=["The selected result passes."],
    )
    with WriterLease(store, "engine") as lease:
        plan.create(PlanCreate(title="Projection", tasks=[hostile]), lease, actor_id="client")
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    host_task_id, host_plan_id = str(uuid4()), str(uuid4())
    target = codex_home / "plans" / host_task_id / host_plan_id / "PLAN.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Existing\n", encoding="utf-8")
    with WriterLease(store, "engine") as lease:
        plan.bind_host_plan(HostPlanBind(
            host_task_id=host_task_id,
            host_plan_id=host_plan_id,
            plan_path=str(target),
            expected_sha256=digest(target),
        ), lease, actor_id="client")
    rendered = target.read_text(encoding="utf-8")
    assert "<img" not in rendered and "[open](" not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert r"\[open\](https://invalid.example)" in rendered
    assert r"Keep \| \`code\` and \*markup\* as text." in rendered
