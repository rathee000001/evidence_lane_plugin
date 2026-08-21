from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from evidence_lane_plugin.hashing import sha256_file
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ID = "test-codex-evidence-lane-plugin"
SESSION_ID = "session_01kz48pm60mt58v5fyzqq6yq1g"
TASK6_THREAD_ID = "01a0036f-32fa-79b2-8846-9c716d4fe777"
ACTIVE_TASK_ID = "EL-CODEX-GITHUB_ACTIONS_CLEAN_CI-PROPOSAL-31"
PHYSICAL_FINAL_TASK_ID = (
    "EL-CODEX-NATIVE-FUSED-RELEASE-HIL-DELTA-141-NORMALIZED-SUCCESSOR"
)
REBIND_ID = "T6-ACTIVE-CONTRACT-REBIND-001"
PLAN_ID = "T6-ACTIVE-CONTRACT-REBIND-EXECUTION-001"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
LIVE_STORE = Path(r"C:\Users\rathe\EvidenceLanePV")
APPROVAL_RELATIVE_PATH = (
    "evidence/task6_delta_receipts/"
    "TASK6_ACTIVE_CONTRACT_REBIND_USER_AUTHORITY.json"
)

REPLACEMENT_CONTRACT = {
    "task_class": "fix_bug",
    "requested_outcome": (
        "Continue the exact Task6 Goal from its unchanged active Plan row and "
        "execute the normalized pre-PV13 parity Deltas in native dependency order. "
        "For Goal recovery, keep one shared mutable multi-project/multi-task manager; "
        "make every helper invocation create or refresh only its exact calling-task "
        "binding and re-enter that same task; and keep the installer helper as a "
        "separate lifecycle component. Preserve all dirty bytes and emit bounded "
        "per-Delta local verification receipts."
    ),
    "permitted_paths": [
        ".github/workflows/**",
        "docs/**",
        "evidence/task6_delta_receipts/**",
        "evidence/task6_parity_research/**",
        "plugins/evidence-lane-plugin/**",
        "pyproject.toml",
        "README.md",
        "tests/**",
    ],
    "permitted_tools": [
        "build",
        "git_diff",
        "patch",
        "pv_fetch",
        "pv_query",
        "pv_search",
        "repository_read",
        "repository_write",
        "terminal",
        "test",
    ],
    "acceptance_checks": [
        "Execute only the current native Plan row and its dependency-ordered successors; query Plan SQLite/FTS rather than loading the full backlog into model context.",
        "Write one bounded local receipt for every implemented Delta and run that row's exact local selectors before advancing.",
        "Keep Goal-recovery manager state shared and mutable across projects/tasks while every invocation binding remains exact-task scoped and re-enters the calling Task6 identity.",
        "Keep installer-helper, tunnel, Goal-recovery manager, and task-binding lifecycles separate; do not auto-launch any of them from this rebind.",
        "Perform Git and install actions only on their exact later native Plan rows; do not infer HIL or move accepted PV12/generation12.",
        "Preserve the sole writer, all dirty/untracked bytes, active Row196 identity, Task6 host identity, runtime task identity, and physically final HIL task.",
    ],
    "stop_condition": (
        "Fail closed on any Plan/session/pointer/task/receipt mismatch. Stop at the "
        "next fresh unaccepted HIL after the dependency-ordered pre-PV13 rows; never "
        "infer approval, create a candidate early, move PV12, or execute Git/install "
        "outside their exact Plan rows."
    ),
    "plan_group": "TASK6_PARITY_EXECUTION",
    "commit_batch_id": "PV13_TASK6_PARITY",
    "dependencies": [],
    "git_commit_stage": "NO_COMMIT",
    "current_version": "2.2.0",
    "current_branch": "agent/evi-v220-systemwide-release-hil-v2.2.0",
}


def unwrap(result, required_key: str) -> dict:
    assert result.isError is False, result.content
    current = result.structuredContent or {}
    for _ in range(5):
        assert current.get("status") == "PASS", current
        if required_key in current:
            return current
        nested = current.get("data")
        assert isinstance(nested, dict), current
        current = nested
    raise AssertionError(f"Missing {required_key} in Evidence Lane result")


async def apply() -> dict:
    backlog_path = LIVE_STORE / "projects" / PROJECT_ID / "task_backlog.json"
    journal_path = (
        LIVE_STORE
        / "projects"
        / PROJECT_ID
        / "active_contract_rebindings"
        / f"{REBIND_ID}.json"
    )
    approval_path = SOURCE_ROOT / APPROVAL_RELATIVE_PATH
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
        catalog = await session.list_tools()
        plan_tool = next(
            tool for tool in catalog.tools if tool.name == "pv_plan_tasks"
        )
        assert len(catalog.tools) == 83
        assert "active_contract_rebind" in plan_tool.inputSchema["properties"]
        before_status = unwrap(
            await session.call_tool("pv_status", {"project_id": PROJECT_ID}),
            "active_session",
        )
        before_backlog = unwrap(
            await session.call_tool(
                "pv_task_backlog", {"project_id": PROJECT_ID}
            ),
            "canonical_plan_sha256",
        )
        active_session = before_status["active_session"]
        pointer = before_status["persistent_state_envelope"]
        assert active_session == {
            **active_session,
            "session_id": SESSION_ID,
            "state": "TASK_CLASSIFIED",
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "candidate_id": None,
            "pending_hil": False,
            "backlog_task_id": ACTIVE_TASK_ID,
        }
        assert active_session["task_id"] == "task_ck_13ade249939f51e87e7bec3776"
        assert pointer["accepted_pv"] == "PV12"
        assert pointer["pointer_generation"] == 12
        assert pointer["pending_candidate"] is None
        assert pointer["pending_hil"] is False
        assert before_backlog["canonical_task_count"] == 224
        assert before_backlog["total_executable_count"] == 169
        assert before_backlog["absolute_active_row"] == 196
        journal = (
            json.loads(journal_path.read_text(encoding="utf-8"))
            if journal_path.is_file()
            else None
        )
        baseline = journal["baseline"] if journal is not None else None
        expected_backlog_sha256 = (
            baseline["backlog_sha256"]
            if baseline is not None
            else sha256_file(backlog_path)
        )
        expected_canonical_plan_sha256 = (
            baseline["canonical_plan_sha256"]
            if baseline is not None
            else before_backlog["canonical_plan_sha256"]
        )
        expected_executable_projection_sha256 = (
            baseline["executable_projection_sha256"]
            if baseline is not None
            else before_backlog["executable_projection_sha256"]
        )
        expected_session_sha256 = (
            baseline["session_sha256"]
            if baseline is not None
            else active_session["session_snapshot_sha256"]
        )
        expected_pointer_sha256 = (
            baseline["pointer_sha256"]
            if baseline is not None
            else pointer["pointer_snapshot_sha256"]
        )
        result = unwrap(
            await session.call_tool(
                "pv_plan_tasks",
                {
                    "project_id": PROJECT_ID,
                    "tasks": [],
                    "planned_by": "Codex_Task6_visible_user_authority",
                    "plan_id": PLAN_ID,
                    "active_contract_rebind": {
                        "rebind_id": REBIND_ID,
                        "session_id": SESSION_ID,
                        "active_task_id": ACTIVE_TASK_ID,
                        "expected_runtime_task_id": active_session["task_id"],
                        "task6_thread_id": TASK6_THREAD_ID,
                        "expected_backlog_sha256": expected_backlog_sha256,
                        "expected_canonical_plan_sha256": (
                            expected_canonical_plan_sha256
                        ),
                        "expected_executable_projection_sha256": (
                            expected_executable_projection_sha256
                        ),
                        "expected_session_sha256": expected_session_sha256,
                        "expected_pointer_sha256": expected_pointer_sha256,
                        "approval_receipt_path": APPROVAL_RELATIVE_PATH,
                        "approval_receipt_sha256": sha256_file(approval_path),
                        "expected_candidate_absent": True,
                        "expected_pending_hil": False,
                        "replacement_contract": REPLACEMENT_CONTRACT,
                    },
                },
            ),
            "active_contract_rebind",
        )
        after_status = unwrap(
            await session.call_tool("pv_status", {"project_id": PROJECT_ID}),
            "active_session",
        )
    receipt = result["active_contract_rebind"]
    active_rows = [
        row
        for row in result["goal_projection"]["rows"]
        if row["status"] == "in_progress"
    ]
    assert receipt["status"] == "PASS"
    assert receipt["journal_phase"] == "COMMITTED"
    assert receipt["active_task_id"] == ACTIVE_TASK_ID
    assert receipt["runtime_task_id"] == active_session["task_id"]
    assert receipt["task6_thread_id"] == TASK6_THREAD_ID
    assert receipt["task_count"] == 224
    assert receipt["physically_final_task_id"] == PHYSICAL_FINAL_TASK_ID
    assert receipt["candidate_created"] is False
    assert receipt["pending_hil"] is False
    assert receipt["pointer_moved"] is False
    assert receipt["goal_completion_mutated"] is False
    assert receipt["git_executed"] is False
    assert receipt["install_executed"] is False
    assert receipt["helper_launched"] is False
    assert receipt["tunnel_launched"] is False
    assert [(row["number"], row["task_id"]) for row in active_rows] == [
        (196, ACTIVE_TASK_ID)
    ]
    assert result["goal_projection"]["rows"][-1]["number"] == 249
    assert result["goal_projection"]["rows"][-1]["task_id"] == (
        PHYSICAL_FINAL_TASK_ID
    )
    after_active = after_status["active_session"]
    assert after_active["session_id"] == SESSION_ID
    assert after_active["task_id"] == active_session["task_id"]
    assert after_active["backlog_task_id"] == ACTIVE_TASK_ID
    assert after_active["candidate_id"] is None
    assert after_active["pending_hil"] is False
    assert after_status["persistent_state_envelope"]["pointer_snapshot_sha256"] == (
        pointer["pointer_snapshot_sha256"]
    )
    return {
        "status": "PASS",
        "catalog_tool_count": len(catalog.tools),
        "project_id": PROJECT_ID,
        "session_id": SESSION_ID,
        "task6_thread_id": TASK6_THREAD_ID,
        "active_plan_task_id": ACTIVE_TASK_ID,
        "runtime_task_id": receipt["runtime_task_id"],
        "canonical_task_count": result["canonical_plan_projection"][
            "task_count"
        ],
        "executable_task_count": result["goal_projection"]["task_count"],
        "active_row": 196,
        "physical_final_row": 249,
        "physical_final_task_id": PHYSICAL_FINAL_TASK_ID,
        "recovered_existing_journal": journal is not None,
        "rebind_receipt": receipt,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(apply()), sort_keys=True))
