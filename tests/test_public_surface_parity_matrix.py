from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.constants import (
    GOVERNED_SKILL_COUNT,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from evidence_lane_plugin.hook_contract import HOOK_EVENT_NAMES
from evidence_lane_plugin.mcp_server import SDK_NATIVE_ACTIONS

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
MATRIX_PATH = ROOT / "docs" / "V220_PUBLIC_SURFACE_PARITY_MATRIX.json"


def test_public_surface_matrix_matches_executable_catalog() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    actions = matrix["native_actions"]
    assert (NATIVE_TOOL_COUNT, NATIVE_READ_TOOL_COUNT, NATIVE_WRITE_TOOL_COUNT) == (
        83,
        26,
        57,
    )
    assert actions == {
        "total": NATIVE_TOOL_COUNT,
        "read": NATIVE_READ_TOOL_COUNT,
        "write": NATIVE_WRITE_TOOL_COUNT,
        "increase_law": (
            "INCREASE_ONLY_WHEN_A_DISTINCT_IMPLEMENTED_NATIVE_OPERATION_HAS_ITS_OWN_"
            "CONTRACT_HANDLER_TEST_AND_PACKAGE_PROOF"
        ),
        "all_count_surfaces_must_change_together": True,
    }

    skill_names = sorted(
        path.parent.name for path in (PLUGIN / "skills").glob("*/SKILL.md")
    )
    assert len(skill_names) == GOVERNED_SKILL_COUNT == 17
    assert set(matrix["governed_skills"]["new_in_this_group"]) <= set(skill_names)

    hooks = matrix["lifecycle_hooks"]
    assert hooks["registered_event_count"] == len(HOOK_EVENT_NAMES) == 8
    assert hooks["event_names"] == list(HOOK_EVENT_NAMES)
    hook_files = [
        PLUGIN / "hooks" / "hooks.json",
        *(PLUGIN / "hooks").glob("*.py"),
        *(PLUGIN / "hooks").glob("*.ps1"),
    ]
    assert len({path.resolve() for path in hook_files}) == hooks["package_file_count"] == 9

    commands = sorted(path.name for path in (PLUGIN / "commands").glob("*.md"))
    assert commands == matrix["host_commands"]["command_files"] == ["evi-plan.md"]


def test_canon_and_learning_public_actions_match_sdk_registration() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    registered = {
        name: {"module": module, "operation": operation, "read": read_only}
        for name, _title, _description, module, operation, read_only in SDK_NATIVE_ACTIONS
    }
    declared: dict[str, dict[str, object]] = {}
    for module, arm in matrix["sdk_public_arms"].items():
        for effect in ("read", "write"):
            for name in arm[effect]:
                declared[name] = {
                    "module": module,
                    "operation": name.removeprefix("canon_").removeprefix("learning_"),
                    "read": effect == "read",
                }
    assert declared == registered
    assert len(registered) == 21
    assert sum(row["read"] is True for row in registered.values()) == 5


def test_every_current_group_declares_all_delta_surface_dimensions() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    required = set(matrix["delta_surface_classification_law"]["required_dimensions"])
    for row in matrix["current_group"]:
        assert set(row) == {"group", *required}
        assert all(str(row[key]).strip() for key in required)
    assert matrix["delta_surface_classification_law"]["classification_required_before_delta_exit"] is True


def test_row196_does_not_absorb_the_later_full_vercel_guide_refresh() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    boundary = matrix["vercel_phase_boundary"]

    assert boundary == {
        "current_row": 196,
        "current_scope": [
            "PLUGIN_PUBLIC_SURFACE_TRUTH",
            "CURRENT_DELTA_LEDGER_AND_PLAN_PROJECTION",
        ],
        "later_row": 197,
        "later_scope": [
            "FULL_GUIDE_REFRESH_FOR_EVERY_NAVIGATION_PAGE",
            "PAGE_SPECIFIC_CURRENT_PLUGIN_NARRATIVE",
            "PAGE_SPECIFIC_HERO_ICON_ORB_RING_ANIMATION",
            (
                "PAIN_POINT_LED_HOMEPAGE_STORY_DERIVED_FROM_CURRENT_"
                "ARCHITECTURE_AND_LANES"
            ),
        ],
        "current_row_may_claim_later_row_complete": False,
        "vercel_runtime_is_project_truth_authority": False,
    }
