from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))

from evidence_lane_plugin.capture_routing import HOOK_EVENT_ORDER
from evidence_lane_plugin.registry import WORKFLOWS

EXPECTED_SKILLS = {
    "evidence-lane",
    "open-project-session",
    "close-project-session",
    "run-project-lifecycle",
    "execute-project-plan",
    "manage-project-plan",
    "configure-project-workflow",
    "classify-project-work",
    "manage-project-sources",
    "refresh-project-evidence",
    "retrieve-project-evidence",
    "manage-project-memory",
    "manage-project-lessons",
    "inspect-project-instructions",
    "exchange-task-evidence",
    "handoff-project-work",
    "recover-project-state",
    "select-project-storage",
    "select-project-tools",
    "inspect-project-connectors",
    "configure-project-connector",
    "revoke-project-connector",
    "inspect-project-evidence-map",
    "link-project-evidence-network",
}


def load(relative):
    return json.loads((PLUGIN / relative).read_text(encoding="utf-8"))


def verify_members(document):
    for member in document["members"]:
        path = PLUGIN / member["path"]
        assert path.is_file(), member["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]


def test_business_skill_set_and_complete_distribution_manifests():
    assert {workflow.skill for workflow in WORKFLOWS} == EXPECTED_SKILLS
    actual = {path.parent.name for path in (PLUGIN / "skills").glob("*/SKILL.md")}
    assert actual == EXPECTED_SKILLS
    assert not any(name == "evi" or name.startswith("evi-") for name in actual)
    skill_surface = load("skills/skill-surface-registry.v4.json")
    assert skill_surface["skill_count"] == 24
    assert {row["name"] for row in skill_surface["skills"]} == EXPECTED_SKILLS

    mcp = load("mcp/mcp-manifest.v4.json")
    assert mcp["action_count"] == 297
    verify_members(mcp)

    sdk = load("sdk/sdk-manifest.v4.json")
    assert sdk["complete_family_surface"] and sdk["action_count"] == 297
    verify_members(sdk)
    sdk_paths = {row["path"] for row in sdk["members"]}
    assert {"sdk/plan/runtime.py", "sdk/hooks/runtime.py", "sdk/internal/runtime.py",
            "sdk/actions/action-manifest.v4.json", "sdk/sdk-surface-registry.v4.json"} <= sdk_paths
    sdk_surface = load("sdk/sdk-surface-registry.v4.json")
    assert sdk_surface["family_count"] == 13
    assert {row["name"] for row in sdk_surface["families"]} == {
        "actions", "authorities", "delta", "env_uop", "hooks", "host", "internal",
        "plan", "recovery", "routing", "skills", "transports", "workflows"}

    schemas = load("schemas/schema-manifest.v4.json")
    assert schemas["complete_family_surface"] and schemas["action_count"] == 297
    verify_members(schemas)
    family = load("schemas/schema-family-registry.v4.json")
    assert family["family_count"] >= 40
    assert load("schemas/hooks/hook-family.v4.json")["event_count"] == len(HOOK_EVENT_ORDER)
    assert load("schemas/authorities/plan.v4.json")["complete_owner_files_indexed"]
    assert load("schemas/project-sectors/local_code.v4.json")["complete_owner_files_indexed"]
