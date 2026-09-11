#!/usr/bin/env python3
"""Verify canonical skill/workflow/action identities across executable projections."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.registry import WORKFLOWS

OLD_WORKFLOWS = {
    "evi", "boot", "build", "plan", "source-intake", "canon", "memory",
    "learning", "instructions", "project-recipe", "mode", "brain-scaling",
    "refresh", "plugin", "additional-plugin", "drop-additional-plugin",
    "toolchain", "storage", "universe", "bigger-universe", "state-travel",
    "recover", "exit-boot", "lifecycle",
}
OLD_ACTIONS = {
    "pv_diff", "pv_history", "pv_summary", "project_recipe", "mode_classify",
    "brain_slice_select", "cross_project_query", "linked_projects_read",
    "project_link", "project_unlink", "universe_inspect", "universe_links_verify",
    "universe_query", *{f"bigger_universe_{suffix}" for suffix in (
        "create", "grant", "link", "read", "register", "revoke", "verify")},
    *{f"canon_{suffix}" for suffix in (
        "backfire", "classify", "decide", "expect", "graph", "inbox", "inspect",
        "join", "packet_classify", "read", "receive", "send", "supersede",
        "task_edge_bind", "task_edge_register", "task_result")},
}


def structured_issues(value, path: str, issues: list[dict]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"workflow", "workflow_id"} and isinstance(item, str) and item in OLD_WORKFLOWS:
                issues.append({"path": path, "code": "retired-workflow-id", "value": item})
            if isinstance(item, str) and item in {"state-travel", "state_travel"}:
                issues.append({"path": path, "code": "retired-runtime-vocabulary", "value": item})
            structured_issues(item, path, issues)
    elif isinstance(value, list):
        for item in value:
            structured_issues(item, path, issues)


def verify() -> dict:
    issues = []
    with tempfile.TemporaryDirectory(prefix="evidence-lane-canonical-skills-") as temporary:
        engine = Engine(Path(temporary))
        actions = engine.registry.schemas()
        workflows = engine.registry.workflow_schemas()
        engine.clients.close()
        engine.provider_workers.close()
    expected = [row.skill for row in WORKFLOWS]
    if [row.name for row in WORKFLOWS] != expected or len(set(expected)) != 24:
        issues.append({"path": "src/evidence_lane_plugin/registry.py", "code": "workflow-skill-identity"})
    action_names = {row["name"] for row in actions}
    for old in sorted(OLD_ACTIONS & action_names):
        issues.append({"path": "engine_registry", "code": "retired-action-id", "value": old})
    if {row["name"] for row in workflows} != set(expected):
        issues.append({"path": "engine_registry", "code": "workflow-set"})
    json_roots = [PLUGIN / name for name in ("mcp", "sdk", "schemas", "manifests", "env", "uop", "provisioning", "studio")]
    json_files = []
    for root in json_roots:
        json_files.extend(root.rglob("*.json"))
    for path in json_files:
        relative = path.relative_to(PLUGIN).as_posix()
        if "state-travel" in relative or "state_travel" in relative:
            issues.append({"path": relative, "code": "retired-runtime-file"})
        structured_issues(json.loads(path.read_text(encoding="utf-8")), relative, issues)
    for old in OLD_ACTIONS:
        for relative in (
            f"mcp/actions/{old}.binding.v4.json",
            f"sdk/actions/{old}.action.v4.json",
            f"sdk/routing/actions/{old}.route.v4.json",
            f"schemas/actions/{old}.v4.schema.json",
        ):
            if (PLUGIN / relative).exists():
                issues.append({"path": relative, "code": "retired-action-file"})
    for old in OLD_WORKFLOWS:
        relative = f"sdk/workflows/{old}.workflow.v4.json"
        if (PLUGIN / relative).exists():
            issues.append({"path": relative, "code": "retired-workflow-file"})
    forbidden = ("evi-", "bigger universe", "Root PV", "State Travel", "Project Recipe", "Brain Scaling")
    public_files = [
        path
        for root in (PLUGIN / "skills", PLUGIN / "studio")
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".py"}
    ]
    for path in public_files:
        text = path.read_text(encoding="utf-8")
        for value in forbidden:
            if value in text:
                issues.append({"path": path.relative_to(PLUGIN).as_posix(), "code": "retired-public-vocabulary", "value": value})
    hook_manifest = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
    for event, groups in hook_manifest["hooks"].items():
        hook = groups[0]["hooks"][0]
        if "hooks/runner.mjs" not in hook["command"].replace("\\", "/"):
            issues.append({"path": "hooks/hooks.json", "code": "hook-runtime-bridge-missing", "value": event})
    return {
        "schema": "evidence-lane.canonical-skill-contract-audit.v4",
        "status": "PASS" if not issues else "FAIL",
        "workflow_count": len(workflows),
        "action_count": len(actions),
        "json_file_count": len(json_files),
        "public_file_count": len(public_files),
        "executable_legacy_aliases": False if not issues else None,
        "issues": issues,
    }


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
