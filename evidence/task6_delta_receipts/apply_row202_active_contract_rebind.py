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
ACTIVE_TASK_ID = "EL-CODEX-T6-PARITY-005-LOCAL-TEST-TRANSACTIONAL-ACTIVATION"
DEPENDENCY_TASK_ID = "EL-CODEX-T6-PARITY-004-FALLBACK-SELECTOR-AUTHORITY"
PHYSICAL_FINAL_TASK_ID = (
    "EL-CODEX-NATIVE-FUSED-RELEASE-HIL-DELTA-141-NORMALIZED-SUCCESSOR"
)
REBIND_ID = "T6-ROW202-PATH-AUTHORITY-REBIND-001"
PLAN_ID = "T6-ROW202-PATH-AUTHORITY-REBIND-EXECUTION-001"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
LIVE_STORE = Path(r"C:\Users\rathe\EvidenceLanePV")
APPROVAL_RELATIVE_PATH = (
    "evidence/task6_delta_receipts/"
    "TASK6_ROW202_ACTIVE_CONTRACT_PATH_AUTHORITY.json"
)

REQUESTED_OUTCOME = (
    "Local Test Transactional Activation: implement the normalized Task6 parity "
    "correction and satisfy every evidence-linked requirement without broadening "
    "authority. Make runtime readiness false until host trust, restart/reload, "
    "exact plugin/catalog identity, prompt capture, and bounded smoke probes all "
    "pass; distinguish installed, restart-required, trusted, active, and ready "
    "states. Use a compare-and-swap transaction: preserve last-known-good stable "
    "activation, stage candidate disabled, prove trust/restart/native catalog, "
    "switch once, and write a rollback-capable receipt or restore the exact prior "
    "config atomically."
)
ACCEPTANCE_CHECKS = [
    "Make runtime readiness false until host trust, restart/reload, exact plugin/catalog identity, prompt capture, and bounded smoke probes all pass; distinguish installed, restart-required, trusted, active, and ready states.",
    "Use a compare-and-swap transaction: preserve last-known-good stable activation, stage candidate disabled, prove trust/restart/native catalog, switch once, and write a rollback-capable receipt or restore the exact prior config atomically.",
    "Targeted unit and integration selectors for this exact Delta pass.",
    "A per-Delta receipt binds changed paths, test selectors, outputs, and pre/post worktree identities.",
    "No HIL decision, PV pointer movement, candidate acceptance, secret persistence, or unrelated dirty-byte change occurs.",
    "Native contract normalization: research_class=fix_bug; native_class=fix_bug; host_tool_labels=apply_patch,rg,shell_command:python,shell_command:pytest-targeted; native_tools=patch,repository_read,repository_write,terminal,test; external installation paths remain governed-installer-only and are not repository write scope.",
]
STOP_CONDITION = (
    "Fail closed on authority, identity, dependency, selector, receipt, or test "
    "mismatch. Do not stage, commit, push, install, invoke HIL, or move the "
    "pointer. Complete only after the exact per-Delta local receipt passes."
)
REPLACEMENT_CONTRACT = {
    "task_class": "fix_bug",
    "requested_outcome": REQUESTED_OUTCOME,
    "permitted_paths": [
        "evidence/task6_delta_receipts/**",
        (
            "plugins/evidence-lane-plugin/scripts/codex_release/"
            "install_codex_stable.py"
        ),
        "tests/test_codex_v200_installation.py",
        "tests/test_local_install_transactional_activation.py",
    ],
    "permitted_tools": [
        "patch",
        "repository_read",
        "repository_write",
        "terminal",
        "test",
    ],
    "acceptance_checks": ACCEPTANCE_CHECKS,
    "stop_condition": STOP_CONDITION,
    "plan_group": "TASK6_PARITY",
    "commit_batch_id": "PV13_TASK6_PARITY",
    "dependencies": [DEPENDENCY_TASK_ID],
    "git_commit_stage": "NONE",
    "current_version": None,
    "current_branch": None,
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


async def apply() -> dict:
    project_root = LIVE_STORE / "projects" / PROJECT_ID
    backlog_path = project_root / "task_backlog.json"
    journal_path = (
        project_root / "active_contract_rebindings" / f"{REBIND_ID}.json"
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
        before_status = unwrap(
            await session.call_tool("pv_status", {"project_id": PROJECT_ID}),
            "active_session",
        )
        before_backlog = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": PROJECT_ID, "task_id": ACTIVE_TASK_ID, "limit": 1},
            ),
            "contract",
        )
        before_header = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": PROJECT_ID, "limit": 10},
            ),
            "canonical_plan_sha256",
        )
        active_session = before_status["active_session"]
        pointer = before_status["persistent_state_envelope"]
        active_row = before_backlog["row"]
        active_contract = before_backlog["contract"]
        assert active_session["session_id"] == SESSION_ID
        assert active_session["state"] == "TASK_CLASSIFIED"
        assert active_session["accepted_pv"] == "PV12"
        assert active_session["pointer_generation"] == 12
        assert active_session["candidate_id"] is None
        assert active_session["pending_hil"] is False
        assert active_session["backlog_task_id"] == ACTIVE_TASK_ID
        assert pointer["accepted_pv"] == "PV12"
        assert pointer["pointer_generation"] == 12
        assert pointer["pending_candidate"] is None
        assert pointer["pending_hil"] is False
        assert active_row["row_number"] == 202
        assert active_row["host_status"] == "in_progress"
        assert before_header["canonical_task_count"] == 224
        assert before_header["total_executable_count"] == 169
        assert before_header["absolute_active_row"] == 202
        assert before_header["absolute_active_task_id"] == ACTIVE_TASK_ID
        assert active_contract["requested_outcome"] == REQUESTED_OUTCOME
        assert json.loads(active_contract["acceptance_checks_json"]) == (
            ACCEPTANCE_CHECKS
        )
        assert json.loads(active_contract["dependencies_json"]) == [
            DEPENDENCY_TASK_ID
        ]
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
            else before_header["canonical_plan_sha256"]
        )
        expected_executable_projection_sha256 = (
            baseline["executable_projection_sha256"]
            if baseline is not None
            else before_header["executable_projection_sha256"]
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
        (202, ACTIVE_TASK_ID)
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
    assert after_status["persistent_state_envelope"][
        "pointer_snapshot_sha256"
    ] == pointer["pointer_snapshot_sha256"]
    return {
        "status": "PASS",
        "project_id": PROJECT_ID,
        "session_id": SESSION_ID,
        "task6_thread_id": TASK6_THREAD_ID,
        "active_plan_task_id": ACTIVE_TASK_ID,
        "runtime_task_id": receipt["runtime_task_id"],
        "canonical_task_count": result["canonical_plan_projection"]["task_count"],
        "executable_task_count": result["goal_projection"]["task_count"],
        "active_row": 202,
        "physical_final_row": 249,
        "physical_final_task_id": PHYSICAL_FINAL_TASK_ID,
        "recovered_existing_journal": journal is not None,
        "rebind_receipt": receipt,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(apply()), sort_keys=True))
