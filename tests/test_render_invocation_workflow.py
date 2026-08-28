from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
ROUTING = PLUGIN / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"


def _workflow_tools(routing: dict[str, object], workflow: str) -> list[str]:
    groups = routing["workflows"][workflow]["ordered_tool_groups"]
    return [tool for group in groups for tool in group["tools"]]


def test_project_runtime_render_has_only_two_workflow_triggers() -> None:
    routing = json.loads(ROUTING.read_text(encoding="utf-8"))
    policy = routing["render_invocation_policy"]

    assert policy == {
        "schema": "evidence-lane.render-invocation-policy.v1",
        "owner": "evidence-lane-code-lifecycle",
        "allowed_triggers": [
            "PHYSICALLY_FINAL_PV_HIL_PRESENTATION",
            "EXPLICIT_USER_REQUEST",
        ],
        "ordinary_boot_or_resume_allowed": False,
        "ordinary_owner_discovery_allowed": False,
        "ordinary_goal_continuation_allowed": False,
        "state_travel_max_calls_per_render_tool": 0,
        "physical_final_hil_max_calls_per_render_tool": 1,
        "implicit_fallback_allowed": False,
    }
    assert routing["tool_owners"]["render_runtime_panel"] == (
        "evidence-lane-code-lifecycle"
    )
    assert routing["tool_owners"]["render_project_panel"] == (
        "evidence-lane-code-lifecycle"
    )

    boot_tools = _workflow_tools(routing, "evi-boot")
    assert "render_runtime_panel" not in boot_tools
    assert "render_project_panel" not in boot_tools

    state_travel_groups = routing["workflows"]["evi-state-travel"][
        "ordered_tool_groups"
    ]
    assert [
        group
        for group in state_travel_groups
        if group["workflow"] == "state-travel-final-authority-render-once"
    ] == []
    assert "render_runtime_panel" not in {
        tool for group in state_travel_groups for tool in group["tools"]
    }
    assert "render_project_panel" not in {
        tool for group in state_travel_groups for tool in group["tools"]
    }

    build_groups = routing["workflows"]["evi-build"]["ordered_tool_groups"]
    assert [
        group
        for group in build_groups
        if group["workflow"] == "physically-final-pv-hil-presentation-only"
    ] == [
        {
            "order": 2,
            "tools": ["render_runtime_panel", "render_project_panel"],
            "workflow": "physically-final-pv-hil-presentation-only",
        }
    ]


def test_owning_skills_fail_closed_outside_the_three_render_triggers() -> None:
    boot = (PLUGIN / "skills" / "evi-boot" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    state_travel = (
        PLUGIN / "skills" / "evi-state-travel" / "SKILL.md"
    ).read_text(encoding="utf-8")
    build = (PLUGIN / "skills" / "evi-build" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Ordinary Boot or Resume" in boot
    assert "only while presenting" in boot
    assert "State Travel uses native receipts and never invokes" in boot
    assert "physically final PV HIL" in boot
    assert "explicit user request" in boot
    assert "never" in state_travel and "render_runtime_panel" in state_travel
    assert "physically final PV HIL" in " ".join(build.split())
