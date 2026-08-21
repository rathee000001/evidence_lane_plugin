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
ACTIVE_TASK_ID = "EL-CODEX-GITHUB_ACTIONS_CLEAN_CI-PROPOSAL-31"
SUCCESSOR_TASK_ID = "EL-CODEX-T6-PARITY-001-PLAN-BATCH-PREHIL-INSERT-ROUTE"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
LIVE_STORE = Path(r"C:\Users\rathe\EvidenceLanePV")

IMPLEMENTATION_PATHS = {
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/codex_turn_control.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_apps.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/plan_runtime.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/service.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/session.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/store.py": "SOURCE",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/task_binding_registry.py": "SOURCE",
    "plugins/evidence-lane-plugin/skills/evidence-lane-code-lifecycle/SKILL.md": "DOCUMENTATION",
    "tests/test_active_task_contract_rebind.py": "TEST",
    "tests/test_exact_task_binding_task_advance.py": "TEST",
    "tests/test_mcp_plugin.py": "TEST",
    "tests/test_per_delta_local_verification.py": "TEST",
    "tests/test_persistent_step_task_list_contract.py": "TEST",
    "tests/test_plan_atomic_insertions.py": "TEST",
    "tests/test_task_binding_registry.py": "TEST",
    "evidence/task6_delta_receipts/EL-CODEX-T6-PARITY-001-PLAN-BATCH-PREHIL-INSERT-ROUTE.json": "RECEIPT",
    "evidence/task6_delta_receipts/EL-CODEX-T6-PARITY-001A-ACTIVE-CONTRACT-REBIND.json": "RECEIPT",
    "evidence/task6_delta_receipts/EL-CODEX-T6-PARITY-001B-PLAN-SQLITE-ACTIVE-CONTRACT-PARITY.json": "RECEIPT",
    "evidence/task6_delta_receipts/apply_active_contract_rebind_live.py": "RECEIPT",
    "evidence/task6_delta_receipts/read_task6_plan_window.py": "RECEIPT",
    "evidence/task6_delta_receipts/advance_task6_row196_with_local_receipt.py": "RECEIPT",
    "evidence/task6_parity_research/TASK6_PARITY_RESEARCH.sqlite": "METADATA",
    "evidence/task6_parity_research/record_active_contract_rebind.py": "METADATA",
    "evidence/task6_parity_research/record_plan_sqlite_parity_followup.py": "METADATA",
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
        "command_sha256": sha256_bytes(command.encode("utf-8")),
        "status": "PASS",
        "output": output,
        "output_sha256": sha256_bytes(output.encode("utf-8")),
        "negative_cases": [negative],
    }


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
    session_path = (
        LIVE_STORE
        / "projects"
        / PROJECT_ID
        / "sessions"
        / f"{SESSION_ID}.json"
    )
    governed_session = json.loads(session_path.read_text(encoding="utf-8"))
    pre_worktree_sha256 = governed_session["metadata"]["entry_freshness"][
        "bound_worktree_sha256"
    ]
    changed_paths = []
    for relative, role in IMPLEMENTATION_PATHS.items():
        path = SOURCE_ROOT / Path(relative)
        assert path.is_file(), relative
        changed_paths.append(
            {
                "path": relative,
                "role": role,
                "sha256": sha256_file(path),
            }
        )
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        active_detail = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {
                    "project_id": PROJECT_ID,
                    "task_id": ACTIVE_TASK_ID,
                    "limit": 1,
                },
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
        verification = {
            "schema": "evidence-lane.per-delta-local-verification-input.v1",
            "receipt_id": "task6-row196-local-verification-001",
            "active_task_id": ACTIVE_TASK_ID,
            "task_contract_sha256": active_row["task_contract_sha256"],
            "dependency_generation": 12,
            "dependency_task_ids": json.loads(active_row["dependencies_json"]),
            "pre_worktree_sha256": pre_worktree_sha256,
            "post_worktree_sha256": calculate_worktree_sha256(SOURCE_ROOT),
            "changed_paths": changed_paths,
            "test_runs": [
                test_run(
                    "tests/test_plan_atomic_insertions.py",
                    ".venv/Scripts/python.exe -m pytest -q tests/test_plan_atomic_insertions.py",
                    "8 passed in 3.65s",
                    "Hash or insertion-target mismatch writes no Plan batch.",
                ),
                test_run(
                    "tests/test_active_task_contract_rebind.py;tests/test_plan_steers.py;tests/test_plan_normalization.py;tests/test_host_plan_rehydration.py",
                    ".venv/Scripts/python.exe -m pytest -q tests/test_active_task_contract_rebind.py tests/test_plan_steers.py tests/test_plan_normalization.py tests/test_host_plan_rehydration.py",
                    "47 passed across focused rebind, steer, normalization, and host rehydration suites",
                    "Stale derived SQLite refreshes only with exact CAS and receipt proof.",
                ),
                test_run(
                    "tests/test_per_delta_local_verification.py;tests/test_exact_task_binding_task_advance.py;tests/test_mcp_plugin.py::test_mcp_tool_inventory_and_annotations;tests/test_persistent_step_task_list_contract.py;tests/test_direct_command_routing.py;tests/test_release_channels.py",
                    ".venv/Scripts/python.exe -m pytest -q tests/test_per_delta_local_verification.py tests/test_exact_task_binding_task_advance.py tests/test_mcp_plugin.py::test_mcp_tool_inventory_and_annotations tests/test_persistent_step_task_list_contract.py tests/test_direct_command_routing.py tests/test_release_channels.py",
                    "27 passed in 74.60s",
                    "Generic PASS and tampered live-file hashes cannot advance a normalized Task6 row.",
                ),
                test_run(
                    "tests/test_task_binding_registry.py",
                    ".venv/Scripts/python.exe -m pytest -q tests/test_task_binding_registry.py",
                    "3 passed across exact-task mutability, multi-project reuse, tamper rejection, and installer-free binding",
                    "A stale calling-task rebind, modified task row, or modified release authority fails closed.",
                ),
            ],
            "acceptance_checks": json.loads(
                active_contract["acceptance_checks_json"]
            ),
            "limitations": [
                "The worktree remains intentionally dirty and includes preserved user/pre-Task6 bytes outside this bounded changed-path set.",
                "No candidate, HIL, pointer movement, Git mutation, install, helper, or tunnel action is part of this checkpoint.",
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
                    "permitted_paths": json.loads(
                        successor["permitted_paths_json"]
                    ),
                    "permitted_tools": json.loads(
                        successor["permitted_tools_json"]
                    ),
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
        plan_transition = checkpoint["plan_transition"]
        receipt = checkpoint["receipt"]
        verification_proof = dict(receipt.get("verification_proof") or {})
        delta_verification = dict(
            receipt.get("delta_verification")
            or verification_proof.get("delta_verification")
            or {}
        )
        assert receipt["verification_kind"] == "PER_DELTA_LOCAL_VERIFICATION"
        assert delta_verification["status"] == "PASS"
        assert plan_transition["completed_task"]["task_id"] == ACTIVE_TASK_ID
        assert plan_transition["active_task"]["task_id"] == SUCCESSOR_TASK_ID
        assert receipt["candidate_created"] is False
        assert receipt["pending_hil"] is False
        assert receipt["pointer_moved"] is False
        header = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": PROJECT_ID, "limit": 10},
            ),
            "absolute_active_task_id",
        )
        assert header["absolute_active_task_id"] == SUCCESSOR_TASK_ID
    return {
        "status": "PASS",
        "schema": "evidence-lane.task6-row196-local-advance-proof.v1",
        "completed_task_id": ACTIVE_TASK_ID,
        "active_task_id": SUCCESSOR_TASK_ID,
        "runtime_task_id": result["task"]["task_id"],
        "checkpoint_receipt_sha256": receipt["receipt_sha256"],
        "delta_verification_receipt_sha256": delta_verification["receipt_sha256"],
        "post_worktree_sha256": verification["post_worktree_sha256"],
        "canonical_plan_sha256": header["canonical_plan_sha256"],
        "executable_projection_sha256": header["executable_projection_sha256"],
        "canonical_task_count": header["canonical_task_count"],
        "executable_task_count": header["total_executable_count"],
        "history_task_count": header["history_task_count"],
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "git_executed": False,
        "install_executed": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(advance()), sort_keys=True))
