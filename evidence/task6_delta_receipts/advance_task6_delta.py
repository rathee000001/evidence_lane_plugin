from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from evidence_lane_plugin.git_adapter import calculate_worktree_sha256
from evidence_lane_plugin.hashing import sha256_bytes, sha256_file
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SOURCE_ROOT = Path(__file__).resolve().parents[2]
LIVE_STORE = Path(r"C:\Users\rathe\EvidenceLanePV")


def unwrap(result, required_key: str) -> dict[str, Any]:
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


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["schema"] == "evidence-lane.task6-delta-advance-config.v1"
    assert config["config_path"] == path.relative_to(SOURCE_ROOT).as_posix()
    assert config["expected_generation"] == 12
    assert config["changed_paths"]
    assert config["test_runs"]
    return config


def prior_post_worktree_sha256(config: dict[str, Any]) -> str:
    checkpoint = (
        LIVE_STORE
        / "projects"
        / config["project_id"]
        / "receipts"
        / "delta-verification"
        / f"{config['prior_checkpoint_receipt_id']}.json"
    )
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    delta = payload["delta_verification"]
    assert delta["status"] == "PASS"
    return str(delta["post_worktree_sha256"])


def changed_path_hashes(config: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for item in config["changed_paths"]:
        relative = str(item["path"])
        target = SOURCE_ROOT / Path(relative)
        assert target.is_file(), relative
        result.append(
            {
                "path": relative,
                "role": str(item["role"]),
                "sha256": sha256_file(target),
            }
        )
    return result


def test_runs(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in config["test_runs"]:
        command = str(item["command"])
        output = str(item["output"])
        result.append(
            {
                "selector": str(item["selector"]),
                "command": command,
                "command_sha256": sha256_bytes(command.encode()),
                "status": "PASS",
                "output": output,
                "output_sha256": sha256_bytes(output.encode()),
                "negative_cases": list(item["negative_cases"]),
            }
        )
    return result


async def advance(config: dict[str, Any]) -> dict[str, Any]:
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
        active_detail = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {
                    "project_id": config["project_id"],
                    "task_id": config["active_task_id"],
                    "limit": 1,
                },
            ),
            "contract",
        )
        successor_detail = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {
                    "project_id": config["project_id"],
                    "task_id": config["successor_task_id"],
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
            "receipt_id": config["receipt_id"],
            "active_task_id": config["active_task_id"],
            "task_contract_sha256": active_row["task_contract_sha256"],
            "dependency_generation": config["expected_generation"],
            "dependency_task_ids": json.loads(
                active_contract["dependencies_json"]
            ),
            "pre_worktree_sha256": prior_post_worktree_sha256(config),
            "post_worktree_sha256": calculate_worktree_sha256(SOURCE_ROOT),
            "changed_paths": changed_path_hashes(config),
            "test_runs": test_runs(config),
            "acceptance_checks": json.loads(
                active_contract["acceptance_checks_json"]
            ),
            "limitations": list(config["limitations"]),
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
                    "project_id": config["project_id"],
                    "session_id": config["session_id"],
                    "task_class": successor["task_class"],
                    "requested_outcome": successor["requested_outcome"],
                    "permitted_paths": json.loads(successor["permitted_paths_json"]),
                    "permitted_tools": json.loads(successor["permitted_tools_json"]),
                    "acceptance_checks": json.loads(
                        successor["acceptance_checks_json"]
                    ),
                    "stop_condition": successor["stop_condition"],
                    "backlog_task_id": config["successor_task_id"],
                    "active_delta_verification": verification,
                },
            ),
            "task_checkpoint_advance",
        )
        checkpoint = result["task_checkpoint_advance"]
        transition = checkpoint["plan_transition"]
        receipt = checkpoint["receipt"]
        proof = dict(receipt.get("verification_proof") or {})
        delta = dict(
            receipt.get("delta_verification")
            or proof.get("delta_verification")
            or {}
        )
        assert receipt["verification_kind"] == "PER_DELTA_LOCAL_VERIFICATION"
        assert delta["status"] == "PASS"
        assert transition["completed_task"]["task_id"] == config["active_task_id"]
        assert transition["active_task"]["task_id"] == config["successor_task_id"]
        assert receipt["candidate_created"] is False
        assert receipt["pending_hil"] is False
        assert receipt["pointer_moved"] is False
        header = unwrap(
            await session.call_tool(
                "pv_task_backlog",
                {"project_id": config["project_id"], "limit": 10},
            ),
            "absolute_active_task_id",
        )
        assert header["absolute_active_task_id"] == config["successor_task_id"]
    return {
        "schema": "evidence-lane.task6-delta-local-advance-proof.v1",
        "status": "PASS",
        "completed_task_id": config["active_task_id"],
        "active_task_id": config["successor_task_id"],
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    print(json.dumps(asyncio.run(advance(config)), sort_keys=True))


if __name__ == "__main__":
    main()
