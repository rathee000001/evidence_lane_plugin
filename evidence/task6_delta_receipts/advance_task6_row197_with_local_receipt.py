from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from evidence_lane_plugin.git_adapter import calculate_worktree_sha256
from evidence_lane_plugin.hashing import sha256_bytes, sha256_file
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ID = "test-codex-evidence-lane-plugin"
SESSION_ID = "session_01kz48pm60mt58v5fyzqq6yq1g"
ACTIVE_TASK_ID = "EL-CODEX-T6-PARITY-001-PLAN-BATCH-PREHIL-INSERT-ROUTE"
SUCCESSOR_TASK_ID = "EL-CODEX-T6-PARITY-001A-ACTIVE-CONTRACT-REBIND"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
LIVE_STORE = Path(r"C:\Users\rathe\EvidenceLanePV")
PRIOR_CHECKPOINT = (
    LIVE_STORE
    / "projects"
    / PROJECT_ID
    / "receipts"
    / "delta-verification"
    / "task6-row196-local-verification-001.json"
)

IMPLEMENTATION_PATHS = {
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/store.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/service.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py": "SOURCE",
    "tests/test_plan_atomic_insertions.py": "TEST",
    "evidence/task6_delta_receipts/EL-CODEX-T6-PARITY-001-PLAN-BATCH-PREHIL-INSERT-ROUTE.json": "RECEIPT",
    "evidence/task6_delta_receipts/advance_task6_row196_with_local_receipt.py": "RECEIPT",
    "evidence/task6_delta_receipts/read_task6_plan_window.py": "RECEIPT",
    "evidence/task6_delta_receipts/advance_task6_row197_with_local_receipt.py": "RECEIPT",
}


def unwrap(result, required_key: str) -> dict:
    assert result.isError is False, result.content
    current = result.structuredContent or {}
    for _ in range(6):
        assert current.get("status") == "PASS", current
        if required_key in current:
            return current
        nested = current.get("data")
        assert isinstance(nested, dict), current
        current = nested
    raise AssertionError(f"Missing {required_key} in Evidence Lane result")


def test_run(selector: str, command: str, output: str, negative: str) -> dict:
    return {
        "selector": selector,
        "command": command,
        "command_sha256": sha256_bytes(command.encode()),
        "status": "PASS",
        "output": output,
        "output_sha256": sha256_bytes(output.encode()),
        "negative_cases": [negative],
    }


def prior_post_worktree_sha256() -> str:
    checkpoint = json.loads(PRIOR_CHECKPOINT.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "PASS"
    delta = checkpoint["delta_verification"]
    assert delta["status"] == "PASS"
    return str(delta["post_worktree_sha256"])


async def advance() -> dict:
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
    changed_paths = []
    for relative, role in IMPLEMENTATION_PATHS.items():
        path = SOURCE_ROOT / Path(relative)
        assert path.is_file(), relative
        changed_paths.append(
            {"path": relative, "role": role, "sha256": sha256_file(path)}
        )
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        active_detail = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": PROJECT_ID, "task_id": ACTIVE_TASK_ID, "limit": 1},
            ),
            "contract",
        )
        successor_detail = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {
                    "project_id": PROJECT_ID,
                    "task_id": SUCCESSOR_TASK_ID,
                    "limit": 1,
                },
            ),
            "contract",
        )
        active_row = active_detail["row"]
        active_contract = active_detail["contract"]
        successor = successor_detail["contract"]
        assert active_row["host_status"] == "in_progress"
        verification = {
            "schema": "evidence-lane.per-delta-local-verification-input.v1",
            "receipt_id": "task6-row197-local-verification-001",
            "active_task_id": ACTIVE_TASK_ID,
            "task_contract_sha256": active_row["task_contract_sha256"],
            "dependency_generation": 12,
            "dependency_task_ids": json.loads(
                active_contract["dependencies_json"]
            ),
            "pre_worktree_sha256": prior_post_worktree_sha256(),
            "post_worktree_sha256": calculate_worktree_sha256(SOURCE_ROOT),
            "changed_paths": changed_paths,
            "test_runs": [
                test_run(
                    "tests/test_plan_atomic_insertions.py",
                    ".venv/Scripts/python.exe -m pytest -q tests/test_plan_atomic_insertions.py",
                    "8 passed in 3.72s",
                    "Wrong insertion target, dependency, batch hash, or replay writes no Plan batch.",
                ),
                test_run(
                    "tests/test_plan_steers.py;tests/test_mcp_plugin.py::test_mcp_tool_inventory_and_annotations",
                    ".venv/Scripts/python.exe -m pytest -q tests/test_plan_steers.py tests/test_mcp_plugin.py::test_mcp_tool_inventory_and_annotations",
                    "10 passed in 16.18s",
                    "The public pv_plan_tasks route remains one catalog arm and preserves steer/HIL boundaries.",
                ),
                test_run(
                    "ruff: atomic Plan source, tests, and bounded receipt scripts",
                    ".venv/Scripts/python.exe -m ruff check plugins/evidence-lane-plugin/src/evidence_lane_plugin/store.py plugins/evidence-lane-plugin/src/evidence_lane_plugin/service.py plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py tests/test_plan_atomic_insertions.py evidence/task6_delta_receipts/read_task6_plan_window.py evidence/task6_delta_receipts/advance_task6_row196_with_local_receipt.py",
                    "All checks passed!",
                    "Malformed or stale receipt helpers fail lint before checkpoint use.",
                ),
            ],
            "acceptance_checks": json.loads(
                active_contract["acceptance_checks_json"]
            ),
            "limitations": [
                "Installed stable bytes were not mutated; installed-runtime compatibility is bounded to the previously sealed clone proof and current public catalog regression.",
                "The worktree remains intentionally dirty; no candidate, HIL, pointer, Git, selector, helper, tunnel, or install action is authorized.",
            ],
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "hil_inferred": False,
            "git_executed": False,
            "install_executed": False,
        }
        result = unwrap(
            await session.call_tool(
                "task_classify",
                {
                    "project_id": PROJECT_ID,
                    "session_id": SESSION_ID,
                    "task_class": successor["task_class"],
                    "requested_outcome": successor["requested_outcome"],
                    "permitted_paths": json.loads(successor["permitted_paths_json"]),
                    "permitted_tools": json.loads(successor["permitted_tools_json"]),
                    "acceptance_checks": json.loads(
                        successor["acceptance_checks_json"]
                    ),
                    "stop_condition": successor["stop_condition"],
                    "backlog_task_id": SUCCESSOR_TASK_ID,
                    "active_delta_verification": verification,
                },
            ),
            "task_checkpoint_advance",
        )
        checkpoint = result["task_checkpoint_advance"]
        transition = checkpoint["plan_transition"]
        receipt = checkpoint["receipt"]
        proof = dict(receipt.get("verification_proof") or {})
        delta = dict(receipt.get("delta_verification") or proof.get("delta_verification") or {})
        assert receipt["verification_kind"] == "PER_DELTA_LOCAL_VERIFICATION"
        assert delta["status"] == "PASS"
        assert transition["completed_task"]["task_id"] == ACTIVE_TASK_ID
        assert transition["active_task"]["task_id"] == SUCCESSOR_TASK_ID
        assert receipt["candidate_created"] is False
        assert receipt["pending_hil"] is False
        assert receipt["pointer_moved"] is False
        header = unwrap(
            await session.call_tool(
                "pv_task_backlog", {"project_id": PROJECT_ID, "limit": 10}
            ),
            "absolute_active_task_id",
        )
        assert header["absolute_active_task_id"] == SUCCESSOR_TASK_ID
    return {
        "schema": "evidence-lane.task6-row197-local-advance-proof.v1",
        "status": "PASS",
        "completed_task_id": ACTIVE_TASK_ID,
        "active_task_id": SUCCESSOR_TASK_ID,
        "runtime_task_id": result["task"]["task_id"],
        "checkpoint_receipt_sha256": receipt["receipt_sha256"],
        "delta_verification_receipt_sha256": delta["receipt_sha256"],
        "canonical_plan_sha256": header["canonical_plan_sha256"],
        "executable_projection_sha256": header["executable_projection_sha256"],
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "git_executed": False,
        "install_executed": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(advance()), sort_keys=True))
