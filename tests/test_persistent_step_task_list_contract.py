from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SKILL = PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md"
BOUNDARY_HOOK = PLUGIN / "hooks" / "lifecycle_boundary.py"
HOOKS = PLUGIN / "hooks" / "hooks.json"


def test_full_step_task_list_reentry_is_skill_owned_and_goal_independent() -> None:
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
        "call host `update_plan` once with the complete executable projection",
    ]
    positions = [contract.index(item) for item in ordered]
    assert positions == sorted(positions)

    assert "canonical_authority=PLAN_LANE" in contract
    assert "exactly\n   one active row" in contract
    assert "persistent_until=NEXT_SIX_WAY_HIL_PRESENTED" in contract
    assert "PHYSICALLY_FINAL_HIL" in contract
    assert "Never replace the projection with a window, page, summary" in contract
    assert "Row <canonical row> / <task ID> — <exact description>" in contract
    assert "raw linked-Delta JSON" in contract
    assert "direct stdio as replacement behavior" in contract


def test_postcompact_hook_signals_reentry_without_owning_behavior() -> None:
    config = json.loads(HOOKS.read_text(encoding="utf-8"))
    postcompact = config["hooks"]["PostCompact"][0]["hooks"][0]
    assert "lifecycle_boundary.py" in postcompact["command"]
    assert postcompact["command"].endswith(" PostCompact")
    assert postcompact["commandWindows"].endswith(" PostCompact")

    hook = BOUNDARY_HOOK.read_text(encoding="utf-8")
    assert '"state": "SKILL_REENTRY_REQUIRED"' in hook
    assert '"goal_presence_required": False' in hook
    assert '"hook_scope": "LIFECYCLE_SIGNAL_ONLY"' in hook
    assert '"native_behavior_performed_by_hook": False' in hook
    assert '"host_behavior_performed_by_hook": False' in hook
    assert '"full_plan_rows_embedded_by_hook": False' in hook

    forbidden_behavior = (
        "mcp__evidence_lane__pv_status",
        "mcp__evidence_lane__pv_task_backlog",
        "mcp__evidence_lane__pv_query",
        "CALL_UPDATE_PLAN",
        "EVIDENCE_LANE_HOST_STEP_TASK_LIST_PROJECTION=",
    )
    assert all(item not in hook for item in forbidden_behavior)
