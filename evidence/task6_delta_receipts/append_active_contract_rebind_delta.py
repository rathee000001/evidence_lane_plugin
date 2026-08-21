from __future__ import annotations

import asyncio
import json
import os
import sys

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from verify_atomic_batch_on_native_store_clone import (
    LIVE_STORE,
    PHYSICAL_FINAL_TASK_ID,
    PROJECT_ID,
    SOURCE_ROOT,
)

TARGET_TASK_ID = "EL-CODEX-T6-PARITY-002-PER-DELTA-LOCAL-VERIFICATION"
TASK = {
    "task_id": "EL-CODEX-T6-PARITY-001A-ACTIVE-CONTRACT-REBIND",
    "task_class": "fix_bug",
    "requested_outcome": (
        "Active Contract Amendment and Rebind: add one recoverable, append-only, "
        "user-receipt-bound route that verifies exact backlog/projection/session/"
        "pointer/task identities, preserves the active Plan row and Task6 identity, "
        "records the prior contract immutably, and atomically rebinds the same active "
        "session to a bounded write-capable Goal contract without inferring HIL, "
        "moving PV12, or authorizing Git/install work."
    ),
    "permitted_paths": [
        "evidence/task6_delta_receipts/**",
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py",
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/service.py",
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/session.py",
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/store.py",
        "tests/test_active_task_contract_rebind.py",
        "tests/test_plan_*.py",
    ],
    "permitted_tools": [
        "patch",
        "repository_read",
        "repository_write",
        "terminal",
        "test",
    ],
    "acceptance_checks": [
        "Exact backlog, Plan projection, session snapshot, pointer, active-row, Task6, approval-receipt, and replacement-contract hashes are verified before write.",
        "The prior active task and session contracts remain in append-only amendment history; the same session/task/backlog identity is rebound exactly once.",
        "Crash recovery and idempotent replay converge without a split Plan/session contract.",
        "Targeted success, mismatch, replay, crash, and no-side-effect tests pass with a per-Delta receipt.",
        "No candidate, HIL, pointer, Goal-completion, Git, install, helper, tunnel, or selector action occurs.",
    ],
    "stop_condition": (
        "Fail closed on any authority, identity, receipt, contract, persistence, or "
        "test mismatch. Complete only after the exact local receipt passes; do not "
        "stage, commit, push, install, invoke HIL, move PV12, or mutate Goal completion."
    ),
    "plan_group": "PLAN_RUNTIME_CONTINUITY",
    "commit_batch_id": "PV13_TASK6_PARITY",
    "dependencies": [
        "EL-CODEX-T6-PARITY-001-PLAN-BATCH-PREHIL-INSERT-ROUTE"
    ],
    "git_commit_stage": "NONE",
    "panel_role": "STANDARD",
}
BATCH_SHA256 = sha256_bytes(canonical_json_bytes(TASK))
PLAN_ID = "T6-ACTIVE-CONTRACT-REBIND-DELTA-001"


def unwrap(result) -> dict:
    assert result.isError is False, result.content
    current = result.structuredContent or {}
    for _ in range(4):
        assert current.get("status") == "PASS", current
        if "canonical_plan_sha256" in current or "goal_projection" in current:
            return current
        nested = current.get("data")
        assert isinstance(nested, dict), current
        current = nested
    raise AssertionError("Unsupported Evidence Lane result envelope")


async def append() -> dict:
    backlog_path = LIVE_STORE / "projects" / PROJECT_ID / "task_backlog.json"
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(LIVE_STORE)
    runner = (
        SOURCE_ROOT
        / "plugins"
        / "evidence-lane-plugin"
        / "scripts"
        / "run_mcp.py"
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(runner), "--transport", "stdio"],
        env=environment,
    )
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        before = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": PROJECT_ID},
            )
        )
        assert before["canonical_task_count"] == 223
        assert before["total_executable_count"] == 168
        assert before["absolute_active_row"] == 196
        result = unwrap(
            await session.call_tool(
                "pv_plan_tasks",
                {
                    "project_id": PROJECT_ID,
                    "tasks": [],
                    "planned_by": "Codex_Task6_user_authorized_boundary_correction",
                    "plan_id": PLAN_ID,
                    "atomic_insertion": {
                        "batch_id": PLAN_ID,
                        "research_batch_sha256": BATCH_SHA256,
                        "expected_backlog_sha256": sha256_file(backlog_path),
                        "expected_canonical_plan_sha256": before[
                            "canonical_plan_sha256"
                        ],
                        "expected_executable_projection_sha256": before[
                            "executable_projection_sha256"
                        ],
                        "expected_physical_final_task_id": PHYSICAL_FINAL_TASK_ID,
                        "insertions": [
                            {
                                "insert_before_task_id": TARGET_TASK_ID,
                                "tasks": [TASK],
                            }
                        ],
                    },
                },
            )
        )
    receipt = result["atomic_insertion_receipt"]
    assert result["canonical_plan_projection"]["task_count"] == 224
    assert result["goal_projection"]["task_count"] == 169
    assert result["goal_projection"]["row_end"] == 249
    assert receipt["physical_final_row"] == 249
    return {
        "status": "PASS",
        "task_id": TASK["task_id"],
        "research_batch_sha256": BATCH_SHA256,
        "input_sha256": receipt["input_sha256"],
        "before_backlog_sha256": receipt["before_backlog_sha256"],
        "after_backlog_sha256": receipt["after_backlog_sha256"],
        "canonical_task_count": 224,
        "executable_task_count": 169,
        "active_row": 196,
        "inserted_row": 198,
        "physical_final_row": 249,
        "physical_final_task_id": PHYSICAL_FINAL_TASK_ID,
        "pointer_moved": receipt["pointer_moved"],
        "candidate_created": receipt["candidate_created"],
        "hil_invoked": receipt["hil_invoked"],
        "goal_mutated": receipt["goal_mutated"],
        "git_executed": receipt["git_executed"],
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(append()), sort_keys=True))
