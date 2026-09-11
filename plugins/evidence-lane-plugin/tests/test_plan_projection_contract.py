from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from uuid import uuid4

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))

from evidence_lane_plugin.plan_runtime import (
    HostPlanBind,
    PlanCreate,
    PlanStore,
    TaskDefinition,
)
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease


def test_plan_database_precedes_one_full_exact_host_plan_projection(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "project", source)
    plan = PlanStore(store)
    definition = TaskDefinition(
        task_id="work", title="Current work", requested_outcome="Verified work",
        acceptance_checks=["Verification passes."])
    with WriterLease(store, "engine") as lease:
        plan.create(PlanCreate(title="Project", tasks=[definition]), lease, actor_id="client")
    assert plan.host_projection().state == "unbound"

    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    task_id, plan_id = str(uuid4()), str(uuid4())
    target = home / "plans" / task_id / plan_id / "PLAN.md"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    with WriterLease(store, "engine") as lease:
        result = plan.bind_host_plan(HostPlanBind(
            host_task_id=task_id, host_plan_id=plan_id,
            plan_path=str(target), expected_sha256=before), lease, actor_id="client")
    assert result.projection.state == "confirmed"
    assert result.projection.row_count == 1 and result.projection.full_list
    assert not result.projection.partial_window
    assert result.projection.file_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert "| 001 | `work` | **queued** | `pending` |" in target.read_text(encoding="utf-8")
