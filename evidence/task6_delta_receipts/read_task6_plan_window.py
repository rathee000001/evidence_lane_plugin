from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ID = "test-codex-evidence-lane-plugin"
ROW_START = 220
ROW_END = 228
SOURCE_ROOT = Path(__file__).resolve().parents[2]
LIVE_STORE = Path(r"C:\Users\rathe\EvidenceLanePV")
PLAN_SQLITE = (
    LIVE_STORE
    / "projects"
    / PROJECT_ID
    / "plan_runtime_projection.sqlite"
)


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


def row_identities() -> list[tuple[int, str]]:
    uri = f"{PLAN_SQLITE.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT row_number, task_id
              FROM plan_execution_row
             WHERE projection_lane = 'GOAL'
               AND row_number BETWEEN ? AND ?
             ORDER BY row_number
            """,
            (ROW_START, ROW_END),
        ).fetchall()
    result = [(int(row), str(task_id)) for row, task_id in rows]
    assert [row for row, _ in result] == list(range(ROW_START, ROW_END + 1))
    return result


async def read() -> dict:
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
    identities = row_identities()
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        header = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": PROJECT_ID, "limit": 10},
            ),
            "canonical_plan_sha256",
        )
        exact_rows = []
        for row_number, task_id in identities:
            detail = unwrap(
                await session.call_tool(
                    "pv_task_backlog",
                    {
                        "project_id": PROJECT_ID,
                        "task_id": task_id,
                        "limit": 1,
                    },
                ),
                "contract",
            )
            row = detail["row"]
            contract = detail["contract"]
            assert int(row["row_number"]) == row_number
            exact_rows.append(
                {
                    "number": row_number,
                    "task_id": task_id,
                    "status": row["host_status"],
                    "lifecycle_status": row["lifecycle_status"],
                    "task_classification": row["task_classification"],
                    "requested_outcome": contract["requested_outcome"],
                    "plan_group": row["plan_group"],
                    "commit_batch_id": row["commit_batch_id"],
                    "dependencies": json.loads(row["dependencies_json"]),
                    "git_commit_stage": row["git_commit_stage"],
                    "panel_role": row["panel_role"],
                    "graph_pointer": (
                        "plan_runtime_projection.sqlite#plan_execution_row:"
                        f"{task_id}"
                    ),
                    "fts_locator": f"pv_task_backlog(task_id={task_id})",
                    "task_contract_sha256": row["task_contract_sha256"],
                    "steers_loaded": len(detail["steers"]),
                    "steers_truncated": detail["steer_result_truncated"],
                }
            )
    active_positions = [
        index
        for index, row in enumerate(exact_rows)
        if row["status"] == "in_progress"
    ]
    assert len(active_positions) == 1
    active_position = active_positions[0]
    assert all(
        row["status"] == "completed" for row in exact_rows[:active_position]
    )
    assert all(row["status"] == "pending" for row in exact_rows[active_position + 1 :])
    return {
        "status": "PASS",
        "schema": "evidence-lane.task6-host-plan-window-proof.v1",
        "header": {
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "canonical_task_count": header["canonical_task_count"],
            "total_executable_count": header["total_executable_count"],
            "history_task_count": header["history_task_count"],
            "canonical_plan_sha256": header["canonical_plan_sha256"],
            "executable_projection_sha256": header[
                "executable_projection_sha256"
            ],
            "absolute_active_row": header["absolute_active_row"],
            "absolute_active_task_id": header["absolute_active_task_id"],
            "physical_final_row": 249,
            "full_ledger_returned": False,
            "accepted_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
            "visible_batch_law": {
                "batch": "FIXED_BATCH_ROWS_220_228",
                "completed_rows": "CROSS_IN_PLACE",
                "advance_behavior": "MOVE_ACTIVE_MARKER_WITHIN_FIXED_BATCH",
                "rehydrate_only_when": [
                    "ROW229_ENTERS",
                    "USER_EXPLICITLY_REORDERS_CURRENT_QUEUE"
                ]
            },
        },
        "rows": exact_rows,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(read()), sort_keys=True))
