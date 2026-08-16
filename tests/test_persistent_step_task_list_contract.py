from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SKILL = PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md"
BOUNDARY_HOOK = PLUGIN / "hooks" / "lifecycle_boundary.py"
SKILL_RUNTIME = (
    PLUGIN
    / "src"
    / "evidence_lane_plugin"
    / "hook_skill_runtime.py"
)
HOOKS = PLUGIN / "hooks" / "hooks.json"


def test_bounded_step_task_list_reentry_is_skill_owned_and_goal_independent() -> None:
    contract = SKILL.read_text(encoding="utf-8")

    assert "### Persistent Step Task List re-entry" in contract
    assert "An active Codex Goal is neither a prerequisite" in contract
    assert "host-automatic compaction" in contract
    assert "panel restoration is the first\nskill-owned behavior" in contract
    assert "do not wait for\nor fabricate a prompt, Goal, or PREPARE receipt" in contract

    ordered = [
        "call the installed native `pv_status`",
        "call the installed native `pv_task_backlog`",
        "call one bounded installed-native `pv_query`",
        "derive the aligned current window",
        "call host `update_plan` only when the receipt says ACTIVATE",
    ]
    positions = [contract.index(item) for item in ordered]
    assert positions == sorted(positions)

    assert "canonical_authority=PLAN_LANE" in contract
    assert "exactly\n   one active row" in contract
    assert "persistent_until=NEXT_SIX_WAY_HIL_PRESENTED" in contract
    assert "PHYSICALLY_FINAL_HIL" in contract
    assert "aligned window of at\nmost ten executable rows" in contract
    assert "complete native Plan Lane/Delta ledger is durable authority" in contract
    assert "never load the raw PV" in contract
    assert "ROW=<absolute row>" in contract
    assert "TASK=<stable task ID>" in contract
    assert "STATE=<native/host status>" in contract
    assert "CLASS=<classification>" in contract
    assert "GROUP=<Plan group>" in contract
    assert "BATCH=<commit batch or UNASSIGNED>" in contract
    assert "ROLE=<panel role>" in contract
    assert "DEP=<validated graph dependency>" in contract
    assert "GRAPH=<Plan SQLite graph pointer>" in contract
    assert "FTS=<exact task/FTS locator>" in contract
    assert "only to the exact row where Git actually\nexecutes" in contract
    assert "current non-superseded Plan authority" in contract
    assert "CONFLICTING_DECLARATIONS@RECONCILIATION_REQUIRED" in contract
    assert "every governed project and corpus" in contract
    assert "raw linked-Delta JSON" in contract
    assert "direct stdio as replacement behavior" in contract


def test_postcompact_hook_signals_reentry_without_owning_behavior() -> None:
    config = json.loads(HOOKS.read_text(encoding="utf-8"))
    postcompact = config["hooks"]["PostCompact"][0]["hooks"][0]
    assert postcompact["command"].endswith(
        "--event PostCompact --handler lifecycle_boundary.py"
    )
    assert postcompact["commandWindows"].endswith(
        'PostCompact lifecycle_boundary.py'
    )
    assert "-WindowStyle Hidden" in postcompact["commandWindows"]

    hook = BOUNDARY_HOOK.read_text(encoding="utf-8")
    runtime = SKILL_RUNTIME.read_text(encoding="utf-8")
    assert "consume_boundary_transport" in hook
    assert '"state": "SKILL_REENTRY_REQUIRED"' in runtime
    assert '"goal_presence_required": False' in runtime
    assert '"hook_scope": "LIFECYCLE_SIGNAL_ONLY"' in runtime
    assert '"native_behavior_performed_by_hook": False' in runtime
    assert '"host_behavior_performed_by_hook": False' in runtime
    assert '"full_plan_rows_embedded_by_hook": False' in runtime

    forbidden_behavior = (
        "mcp__evidence_lane__pv_status",
        "mcp__evidence_lane__pv_task_backlog",
        "mcp__evidence_lane__pv_query",
        "CALL_UPDATE_PLAN",
        "EVIDENCE_LANE_HOST_STEP_TASK_LIST_PROJECTION=",
    )
    assert all(item not in hook for item in forbidden_behavior)
