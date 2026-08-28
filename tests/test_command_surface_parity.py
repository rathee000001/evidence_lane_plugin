from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def _json(relative: str) -> dict:
    return json.loads((PLUGIN / relative).read_text(encoding="utf-8"))


def test_evi_plan_is_one_native_skill_with_sdk_and_mcp_routing() -> None:
    routing = _json("skills/evi/references/mcp-tool-routing.v1.json")
    skill_registry = _json("skills/skill-surface-registry.v1.json")
    sdk_workflow = _json("sdk/workflows/evi-plan.workflow.v1.json")
    mcp = _json("mcp/mcp-manifest.v1.json")

    assert (PLUGIN / "skills" / "evi-plan" / "SKILL.md").is_file()
    assert (PLUGIN / "skills" / "evi-plan" / "agents" / "openai.yaml").is_file()
    assert routing["tool_owners"]["pv_plan_tasks"] == "evi-plan"
    assert routing["tool_owners"]["pv_plan_steer_delta"] == "evi-plan"
    routed = [
        tool
        for group in routing["workflows"]["evi-plan"]["ordered_tool_groups"]
        for tool in group["tools"]
    ]
    assert routed == [
        "pv_status",
        "pv_task_backlog",
        "pv_query",
        "search",
        "mode_classify",
        "pv_plan_tasks",
        "pv_plan_steer_delta",
    ]
    assert [row["name"] for row in sdk_workflow["actions"]] == routed
    assert skill_registry["skill_count"] == 26
    assert skill_registry["separate_command_count"] == 0
    assert skill_registry["legacy_command_surface_present"] is False
    assert mcp["separate_command_count"] == 0
    assert mcp["legacy_command_surface_present"] is False


def test_retired_command_surfaces_are_absent() -> None:
    assert not (PLUGIN / "commands").exists()
    assert not (PLUGIN / "schemas" / "commands").exists()
    assert not (PLUGIN / ".codex-plugin" / "migrated-command-skills").exists()
