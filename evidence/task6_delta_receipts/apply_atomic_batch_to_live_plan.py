from __future__ import annotations

import asyncio
import json
import os
import sys

from evidence_lane_plugin.hashing import sha256_file
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from verify_atomic_batch_on_native_store_clone import (
    LIVE_STORE,
    PHYSICAL_FINAL_TASK_ID,
    PROJECT_ID,
    RESEARCH_BATCH_SHA256,
    SOURCE_ROOT,
    pending_insertions,
)

PLAN_ID = "T6-PARITY-NORMALIZED-ATOMIC-REMAINDER-001"
EXPECTED_BACKLOG_SHA256 = (
    "1FCA941F73067CA5BE21C60F9E6BF3150E84683562A112A59E85E01C54E981B0"
)
EXPECTED_CANONICAL_PLAN_SHA256 = (
    "251F036F150364A426792CF136DD6027828E30B2E79A193E8A4441E5B280222D"
)
EXPECTED_EXECUTABLE_PROJECTION_SHA256 = (
    "15F4E8650222D12FDC6C9FE5236E1AC47FF53DC0FA678932F245DA8AFB664F85"
)


def tool_data(result) -> dict:
    assert result.isError is False, result.content
    current = result.structuredContent or {}
    for _ in range(4):
        assert current.get("status") == "PASS", current
        if (
            "goal_projection" in current
            or "canonical_plan_sha256" in current
        ):
            return current
        nested = current.get("data")
        if not isinstance(nested, dict):
            break
        current = nested
    raise AssertionError(f"Unsupported Evidence Lane result envelope: {current.keys()}")


async def apply() -> dict:
    backlog_path = LIVE_STORE / "projects" / PROJECT_ID / "task_backlog.json"
    assert sha256_file(backlog_path) == EXPECTED_BACKLOG_SHA256
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
        plan_tool = next(tool for tool in catalog.tools if tool.name == "pv_plan_tasks")
        assert "atomic_insertion" in plan_tool.inputSchema["properties"]
        before = tool_data(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": PROJECT_ID},
            )
        )
        assert before["canonical_plan_sha256"] == (
            EXPECTED_CANONICAL_PLAN_SHA256
        )
        assert before["executable_projection_sha256"] == (
            EXPECTED_EXECUTABLE_PROJECTION_SHA256
        )
        assert before["canonical_task_count"] == 186
        assert before["total_executable_count"] == 131
        assert before["absolute_active_row"] == 196
        result = tool_data(
            await session.call_tool(
                "pv_plan_tasks",
                {
                    "project_id": PROJECT_ID,
                    "tasks": [],
                    "planned_by": "Codex_Task6_atomic_research_batch",
                    "plan_id": PLAN_ID,
                    "atomic_insertion": {
                        "batch_id": PLAN_ID,
                        "research_batch_sha256": RESEARCH_BATCH_SHA256,
                        "expected_backlog_sha256": EXPECTED_BACKLOG_SHA256,
                        "expected_canonical_plan_sha256": (
                            EXPECTED_CANONICAL_PLAN_SHA256
                        ),
                        "expected_executable_projection_sha256": (
                            EXPECTED_EXECUTABLE_PROJECTION_SHA256
                        ),
                        "expected_physical_final_task_id": (
                            PHYSICAL_FINAL_TASK_ID
                        ),
                        "insertions": pending_insertions(),
                    },
                },
            )
        )
    rows = result["goal_projection"]["rows"]
    receipt = result["atomic_insertion_receipt"]
    assert result["canonical_plan_projection"]["task_count"] == 223
    assert result["goal_projection"]["task_count"] == 168
    assert result["goal_projection"]["row_end"] == 248
    assert receipt["task_count"] == 37
    assert receipt["physical_final_row"] == 248
    assert [
        (row["number"], row["task_id"])
        for row in rows
        if row["status"] == "in_progress"
    ] == [(196, "EL-CODEX-GITHUB_ACTIONS_CLEAN_CI-PROPOSAL-31")]
    assert [
        (row["number"], row["task_id"])
        for row in rows
        if row.get("panel_role") == "PHYSICALLY_FINAL_HIL"
    ] == [(248, PHYSICAL_FINAL_TASK_ID)]
    return {
        "status": "PASS",
        "catalog_tool_count": len(catalog.tools),
        "research_batch_sha256": RESEARCH_BATCH_SHA256,
        "canonical_task_count": result["canonical_plan_projection"]["task_count"],
        "executable_task_count": result["goal_projection"]["task_count"],
        "row_end": result["goal_projection"]["row_end"],
        "active_row": 196,
        "physical_final_row": receipt["physical_final_row"],
        "atomic_insertion_receipt": {
            key: receipt[key]
            for key in (
                "schema",
                "batch_id",
                "input_sha256",
                "group_count",
                "task_count",
                "before_backlog_sha256",
                "after_backlog_sha256",
                "before_canonical_plan_sha256",
                "after_canonical_plan_sha256",
                "before_executable_projection_sha256",
                "after_executable_projection_sha256",
                "physical_final_task_id",
                "physical_final_row",
                "pointer_moved",
                "candidate_created",
                "hil_invoked",
                "goal_mutated",
                "git_executed",
                "idempotent_replay",
                "recovered_prepared_insertion",
            )
        },
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(apply()), sort_keys=True))
