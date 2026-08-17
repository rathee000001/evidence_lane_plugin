from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.mcp_server import (
    SKILL_MCP_ROUTING_SCHEMA,
    create_mcp_server,
    inspect_skill_mcp_routing,
    resolve_skill_mcp_plugin_root,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SKILLS = PLUGIN / "skills"
MANIFEST = SKILLS / "evi" / "references" / "mcp-tool-routing.v1.json"
DEPENDENCY_FRAGMENT = (
    "dependencies:\n"
    "  tools:\n"
    "    - type: \"mcp\"\n"
    "      value: \"evidence-lane\"\n"
    "      description: \"Evidence Lane native MCP server\""
)


def _quoted_yaml_value(text: str, key: str) -> str:
    line = next(
        line.strip() for line in text.splitlines() if line.strip().startswith(f"{key}:")
    )
    value = line.split(":", 1)[1].strip()
    parsed = ast.literal_eval(value)
    assert isinstance(parsed, str)
    return parsed


def _registered_tool_names() -> list[str]:
    server = create_mcp_server()
    return sorted(tool.name for tool in server._tool_manager.list_tools())


def _copy_plugin_skills(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    shutil.copytree(SKILLS, plugin / "skills")
    return plugin


def test_every_skill_declares_exact_bundled_mcp_dependency() -> None:
    skill_paths = sorted(SKILLS.glob("*/SKILL.md"))

    assert len(skill_paths) == 17
    for skill_path in skill_paths:
        skill_name = skill_path.parent.name
        skill_text = skill_path.read_text(encoding="utf-8")
        openai_text = (
            skill_path.parent / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8").replace("\r\n", "\n")

        assert openai_text.count(DEPENDENCY_FRAGMENT) == 1
        assert "transport:" not in openai_text
        assert "url:" not in openai_text
        short_description = _quoted_yaml_value(openai_text, "short_description")
        default_prompt = _quoted_yaml_value(openai_text, "default_prompt")
        assert 25 <= len(short_description) <= 64
        assert f"${skill_name}" in default_prompt
        assert "mcp-tool-routing.v1.json" in skill_text
        assert "MCP_ROUTING_FAIL_CLOSED" in skill_text


def test_shared_manifest_owns_and_routes_the_exact_catalog() -> None:
    registered = _registered_tool_names()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["schema"] == SKILL_MCP_ROUTING_SCHEMA
    assert manifest["server_identity"] == "evidence-lane"
    assert manifest["catalog_contract"]["tool_count"] == 87
    assert set(manifest["tool_owners"]) == set(registered)
    assert len(manifest["tool_owners"]) == 87
    assert len(manifest["low_level_tools"]) == 21
    assert set(manifest["workflows"]) == {
        path.parent.name for path in SKILLS.glob("*/SKILL.md")
    }

    for skill_name, workflow in manifest["workflows"].items():
        groups = workflow["ordered_tool_groups"]
        assert [group["order"] for group in groups] == list(
            range(1, len(groups) + 1)
        )
        routed = {
            tool_name
            for group in groups
            for tool_name in group["tools"]
        }
        assert routed <= set(registered)
        owned = {
            tool_name
            for tool_name, owner in manifest["tool_owners"].items()
            if owner == skill_name
        }
        assert owned <= routed
        assert workflow["missing_tool_behavior"].startswith("FAIL_CLOSED")

    for tool_name, low_level_route in manifest["low_level_tools"].items():
        assert low_level_route["owner_skill"] == manifest["tool_owners"][tool_name]
        assert low_level_route["reached_via"]


def test_mcp_construction_attaches_bounded_skill_routing_receipt() -> None:
    server = create_mcp_server()
    review = server._evidence_lane_skill_mcp_routing_review  # type: ignore[attr-defined]
    route = server._evidence_lane_native_route_receipt  # type: ignore[attr-defined]

    assert review["status"] == "PASS"
    assert review["skill_count"] == 17
    assert review["tool_count"] == 87
    assert review["owned_tool_count"] == 87
    assert review["low_level_tool_count"] == 21
    assert route["skill_mcp_routing"] == {
        "schema": review["schema"],
        "status": "PASS",
        "routing_schema": SKILL_MCP_ROUTING_SCHEMA,
        "manifest_format": review["manifest_format"],
        "server_identity": "evidence-lane",
        "skill_count": 17,
        "tool_count": 87,
        "owned_tool_count": 87,
        "low_level_tool_count": 21,
        "missing_tool_behavior": "FAIL_CLOSED_NO_ALIAS_NO_PREFIX_REWRITE",
        "manifest_sha256": review["manifest_sha256"],
        "receipt_sha256": review["receipt_sha256"],
    }


def test_mcp_construction_uses_explicit_plugin_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIDENCE_LANE_PLUGIN_ROOT", str(PLUGIN.resolve()))

    server = create_mcp_server()
    review = server._evidence_lane_skill_mcp_routing_review  # type: ignore[attr-defined]

    assert review["status"] == "PASS"
    assert review["skill_count"] == 17
    assert review["tool_count"] == 87


def test_mcp_construction_rejects_relative_explicit_plugin_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIDENCE_LANE_PLUGIN_ROOT", "relative/plugin")

    with pytest.raises(EvidenceLaneError) as caught:
        resolve_skill_mcp_plugin_root()

    assert caught.value.code == "SKILL_MCP_ROUTING_INVALID"


@pytest.mark.parametrize("catalog_drift", ["missing", "renamed", "extra"])
def test_catalog_drift_fails_closed(catalog_drift: str) -> None:
    registered = _registered_tool_names()
    if catalog_drift == "missing":
        drifted = registered[1:]
    elif catalog_drift == "renamed":
        drifted = ["renamed_canon_backfire_hil", *registered[1:]]
    else:
        drifted = [*registered, "undeclared_extra_tool"]

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_skill_mcp_routing(PLUGIN, drifted)

    assert caught.value.code == "SKILL_MCP_ROUTING_INVALID"
    assert caught.value.message


@pytest.mark.parametrize(
    "corruption",
    ["missing_dependency", "unowned_tool", "missing_marker", "low_level_route"],
)
def test_bundled_skill_route_corruption_fails_closed(
    tmp_path: Path,
    corruption: str,
) -> None:
    plugin = _copy_plugin_skills(tmp_path)
    manifest_path = (
        plugin / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
    )

    if corruption == "missing_dependency":
        openai_path = plugin / "skills" / "evi" / "agents" / "openai.yaml"
        openai_path.write_text(
            openai_path.read_text(encoding="utf-8").replace(DEPENDENCY_FRAGMENT, ""),
            encoding="utf-8",
        )
    elif corruption == "missing_marker":
        skill_path = plugin / "skills" / "evi" / "SKILL.md"
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "MCP_ROUTING_FAIL_CLOSED", "ROUTING_MARKER_REMOVED"
            ),
            encoding="utf-8",
        )
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if corruption == "unowned_tool":
            manifest["tool_owners"].pop("canon_backfire_hil")
        else:
            manifest["low_level_tools"]["git_sync_selected"]["reached_via"] = ""
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_skill_mcp_routing(plugin, _registered_tool_names())

    assert caught.value.code == "SKILL_MCP_ROUTING_INVALID"
