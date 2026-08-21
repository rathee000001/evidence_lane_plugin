from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.service import EvidenceLaneService

PROJECT_ID = "test-codex-evidence-lane-plugin"
RESEARCH_BATCH_SHA256 = (
    "E5725883DA8343128FE632F4081AF422AEDC8F1944207D950DD547F39380D6D8"
)
PHYSICAL_FINAL_TASK_ID = (
    "EL-CODEX-NATIVE-FUSED-RELEASE-HIL-DELTA-141-NORMALIZED-SUCCESSOR"
)
SOURCE_ROOT = Path(__file__).resolve().parents[2]
LIVE_STORE = Path(r"C:\Users\rathe\EvidenceLanePV")
RESEARCH_DB = (
    SOURCE_ROOT
    / "evidence"
    / "task6_parity_research"
    / "TASK6_PARITY_RESEARCH.sqlite"
)
INSTALLED_21_SRC = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-github"
    r"\evidence-lane-plugin\2.1.0+codex.20260812193232"
)

HOST_TOOL_TO_NATIVE = {
    "apply_patch": {"patch", "repository_write"},
    "rg": {"repository_read"},
    "shell_command:python": {"terminal"},
    "shell_command:pytest-targeted": {"test"},
    "PowerShell governed installer": {"build", "terminal"},
    "native Evidence Lane diagnostics": {"pv_query", "terminal"},
    "git status": {"git_diff"},
    "git diff": {"git_diff"},
    "git add": {"repository_write", "terminal"},
    "git commit": {"repository_write", "terminal"},
    "git push": {"terminal"},
    "GitHub Actions read": {"terminal"},
}


def native_task_contract(contract: dict) -> dict:
    """Map research-facing labels to fail-closed native task authorities."""

    original_class = str(contract["task_class"])
    original_tools = [str(value) for value in contract["permitted_tools"]]
    git_stage = str(contract.get("git_commit_stage") or "NONE")
    if original_class == "verify_result":
        contract["task_class"] = (
            "prepare_patch" if git_stage != "NONE" else "modify_code"
        )
    native_tools: set[str] = set()
    for label in original_tools:
        assert label in HOST_TOOL_TO_NATIVE, label
        native_tools.update(HOST_TOOL_TO_NATIVE[label])
    if contract["task_class"] in {
        "modify_code",
        "fix_bug",
        "add_bounded_feature",
        "prepare_patch",
    }:
        native_tools.add("repository_write")
    contract["permitted_tools"] = sorted(native_tools)
    external_paths = [
        str(value)
        for value in contract["permitted_paths"]
        if re.match(r"^[A-Za-z]:/", str(value))
    ]
    contract["permitted_paths"] = [
        str(value)
        for value in contract["permitted_paths"]
        if str(value) not in external_paths
    ]
    normalization = (
        "Native contract normalization: "
        f"research_class={original_class}; native_class={contract['task_class']}; "
        f"host_tool_labels={','.join(original_tools)}; "
        f"native_tools={','.join(contract['permitted_tools'])}; "
        "external installation paths remain governed-installer-only and are not "
        "repository write scope"
    )
    if external_paths:
        normalization += f" ({','.join(external_paths)})"
    contract["acceptance_checks"] = [
        *contract["acceptance_checks"],
        normalization + ".",
    ]
    return contract


def pending_insertions() -> list[dict]:
    connection = sqlite3.connect(
        f"file:{RESEARCH_DB.resolve()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        batch = connection.execute(
            """
            SELECT proposal_sha256
            FROM delta_append_batch
            WHERE batch_id='T6-PARITY-NORMALIZATION-BATCH-001'
            """
        ).fetchone()
        assert batch is not None
        assert str(batch["proposal_sha256"]) == RESEARCH_BATCH_SHA256
        rows = connection.execute(
            """
            WITH latest_map AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY proposal_id
                    ORDER BY recorded_at DESC, rowid DESC
                ) AS rn
                FROM delta_normalization_map
            ), receipted AS (
                SELECT DISTINCT proposal_id FROM native_plan_receipt
            )
            SELECT p.proposal_id, p.contract_json
            FROM delta_proposal AS p
            JOIN latest_map AS m USING(proposal_id)
            LEFT JOIN receipted AS r USING(proposal_id)
            WHERE m.rn=1
              AND m.disposition='APPEND_NEW_ROW'
              AND r.proposal_id IS NULL
            ORDER BY CAST(json_extract(p.contract_json, '$.proposal_order') AS INTEGER)
            """
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 37
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        contract = json.loads(str(row["contract_json"]))
        expected_contract_sha256 = str(contract.pop("contract_sha256"))
        observed_contract_sha256 = hashlib.sha256(
            json.dumps(
                contract,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest().upper()
        assert observed_contract_sha256 == expected_contract_sha256
        contract["contract_sha256"] = expected_contract_sha256
        contract = native_task_contract(contract)
        target = str(contract["insert_before_task_id"])
        grouped.setdefault(target, []).append(contract)
    assert [len(tasks) for tasks in grouped.values()] == [31, 6]
    return [
        {"insert_before_task_id": target, "tasks": tasks}
        for target, tasks in grouped.items()
    ]


def copy_plan_authority(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LIVE_STORE / "registry.json", destination / "registry.json")
    source_project = LIVE_STORE / "projects" / PROJECT_ID
    target_project = destination / "projects" / PROJECT_ID
    target_project.mkdir(parents=True)
    for name in (
        "project.json",
        "task_backlog.json",
        "plan_runtime_projection.sqlite",
    ):
        shutil.copy2(source_project / name, target_project / name)


def installed_21_read(clone: Path) -> dict:
    code = f"""
import json
from evidence_lane_plugin.service import EvidenceLaneService
service = EvidenceLaneService(data_root={str(clone)!r})
status = service.task_backlog({PROJECT_ID!r})
rows = status['goal_projection']['rows']
print(json.dumps({{
  'module': __import__('evidence_lane_plugin').__file__,
  'canonical_count': status['canonical_plan_projection']['task_count'],
  'executable_count': status['goal_projection']['task_count'],
  'row_end': status['goal_projection']['row_end'],
  'active': [{{'number': row['number'], 'task_id': row['task_id']}} for row in rows if row['status'] == 'in_progress'],
  'physical_final': [{{'number': row['number'], 'task_id': row['task_id']}} for row in rows if row.get('panel_role') == 'PHYSICALLY_FINAL_HIL'],
  'runtime_status': status['plan_runtime_projection']['status'],
}}, sort_keys=True))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(INSTALLED_21_SRC / "src")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=clone,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def main() -> None:
    assert LIVE_STORE.is_dir()
    assert RESEARCH_DB.is_file()
    assert INSTALLED_21_SRC.is_dir()
    live_backlog = LIVE_STORE / "projects" / PROJECT_ID / "task_backlog.json"
    live_before_sha256 = sha256_file(live_backlog)
    with tempfile.TemporaryDirectory(prefix="evidence-lane-t6-plan-clone-") as raw:
        clone = Path(raw).resolve()
        copy_plan_authority(clone)
        service = EvidenceLaneService(data_root=clone)
        before = service.task_backlog(PROJECT_ID)
        clone_backlog = clone / "projects" / PROJECT_ID / "task_backlog.json"
        result = service.plan_tasks(
            PROJECT_ID,
            tasks=[],
            planned_by="Codex_Task6_atomic_research_batch",
            plan_id="T6-PARITY-NORMALIZED-ATOMIC-REMAINDER-001",
            atomic_insertion={
                "batch_id": "T6-PARITY-NORMALIZED-ATOMIC-REMAINDER-001",
                "research_batch_sha256": RESEARCH_BATCH_SHA256,
                "expected_backlog_sha256": sha256_file(clone_backlog),
                "expected_canonical_plan_sha256": before[
                    "canonical_plan_projection"
                ]["projection_sha256"],
                "expected_executable_projection_sha256": before[
                    "goal_projection"
                ]["projection_sha256"],
                "expected_physical_final_task_id": PHYSICAL_FINAL_TASK_ID,
                "insertions": pending_insertions(),
            },
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
        installed = installed_21_read(clone)
        assert str(INSTALLED_21_SRC).lower() in str(installed["module"]).lower()
        assert installed["canonical_count"] == 223
        assert installed["executable_count"] == 168
        assert installed["row_end"] == 248
        assert installed["active"] == [
            {
                "number": 196,
                "task_id": "EL-CODEX-GITHUB_ACTIONS_CLEAN_CI-PROPOSAL-31",
            }
        ]
        assert installed["physical_final"] == [
            {"number": 248, "task_id": PHYSICAL_FINAL_TASK_ID}
        ]
        output = {
            "status": "PASS",
            "research_batch_sha256": RESEARCH_BATCH_SHA256,
            "source_atomic_receipt": {
                key: receipt[key]
                for key in (
                    "batch_id",
                    "input_sha256",
                    "group_count",
                    "task_count",
                    "before_backlog_sha256",
                    "after_backlog_sha256",
                    "after_canonical_plan_sha256",
                    "after_executable_projection_sha256",
                    "physical_final_task_id",
                    "physical_final_row",
                    "pointer_moved",
                    "candidate_created",
                    "hil_invoked",
                    "goal_mutated",
                    "git_executed",
                )
            },
            "installed_2_1_backward_read": installed,
            "live_store_unchanged": sha256_file(live_backlog) == live_before_sha256,
        }
        print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
