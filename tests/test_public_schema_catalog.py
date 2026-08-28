from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.constants import (
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from evidence_lane_plugin.mcp_server import create_mcp_server

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
PUBLIC = PLUGIN / "schemas" / "public-action-schemas.v001.json"
OBSOLETE_RUNTIME_COPY = (
    PLUGIN
    / "src"
    / "evidence_lane_plugin"
    / "schemas"
    / "public-action-schemas.v001.json"
)
REMOTE = ROOT / "apps" / "evidence-lane-remote-adapter" / "app" / "_data" / "public-action-registry.json"
LIVE_AUTHORITY = (
    PLUGIN / "schemas" / "live-root-env-uop-six-way-query.v001.json"
)
OBSOLETE_LIVE_AUTHORITY_COPY = (
    PLUGIN
    / "src"
    / "evidence_lane_plugin"
    / "schemas"
    / "live-root-env-uop-six-way-query.v001.json"
)


def test_public_action_schema_catalog_is_canonical_and_complete() -> None:
    runtime_schema_root = PLUGIN / "src" / "evidence_lane_plugin" / "schemas"
    assert not any(path.is_file() for path in runtime_schema_root.rglob("*"))
    assert not OBSOLETE_RUNTIME_COPY.exists()
    assert not OBSOLETE_LIVE_AUTHORITY_COPY.exists()
    catalog = json.loads(PUBLIC.read_text(encoding="utf-8"))
    assert catalog["schema"] == "evidence-lane.public-action-schema-catalog.v2"
    assert catalog["tool_count"] == NATIVE_TOOL_COUNT
    assert catalog["read_tool_count"] == NATIVE_READ_TOOL_COUNT
    assert catalog["write_tool_count"] == NATIVE_WRITE_TOOL_COUNT
    assert catalog["lane_count"] == 18
    assert len(catalog["env_uop_governed_six_way_arms"]) == 6
    assert catalog["linked_operational_authorities"] == [
        "PROJECT_UNIVERSE",
        "CONNECTOR_BRAIN",
    ]
    assert catalog["ordinary_live_authority_count"] == 8
    assert catalog["hil_only_authorities"] == ["PROJECT_OVERLAY"]
    assert catalog["governance"] == ["ENV", "UOP"]
    assert catalog["hooks_required_for_explicit_actions"] is False
    assert catalog["accepted_hil_archive_queried_by_ordinary_actions"] is False
    assert catalog["current_implementation_registry"]["status"] == "PASS"
    assert (
        catalog["current_implementation_registry"]["obsolete_execution_allowed"]
        is False
    )
    assert len(catalog["tools"]) == NATIVE_TOOL_COUNT
    assert len({item["name"] for item in catalog["tools"]}) == NATIVE_TOOL_COUNT
    assert all(item["input_schema"]["type"] == "object" for item in catalog["tools"])
    assert all(len(item["schema_sha256"]) == 64 for item in catalog["tools"])
    statuses = {
        item["name"]: item["route_contract"]["status"] for item in catalog["tools"]
    }
    assert set(statuses.values()) == {"CURRENT_ROUTE"}
    assert all(item["route_contract"]["executable"] is True for item in catalog["tools"])
    assert all(
        "obsolete_executable_fallback_allowed" not in item["route_contract"]
        for item in catalog["tools"]
    )

    live_names = {tool.name for tool in create_mcp_server()._tool_manager.list_tools()}
    assert live_names == {item["name"] for item in catalog["tools"]}

    remote = json.loads(REMOTE.read_text(encoding="utf-8"))
    assert remote["source_catalog_sha256"] == catalog["catalog_sha256"]
    assert (
        remote["current_implementation_registry_sha256"]
        == catalog["current_implementation_registry"]["registry_sha256"]
    )
    assert remote["tool_count"] == NATIVE_TOOL_COUNT
    assert len(remote["actions"]) == NATIVE_TOOL_COUNT
    assert remote["execution_authority"] is False

    live_authority = json.loads(LIVE_AUTHORITY.read_text(encoding="utf-8"))
    contract = live_authority["x-evidence-lane-contract"]
    assert contract["schema"] == (
        "evidence-lane.live-root-env-uop-six-way-query.v1"
    )
    assert contract["ordinary_working_layer_count"] == 8
    assert contract["env_uop_role"] == (
        "GOVERNING_CONTROL_PLANE_NOT_AUTHORITY_ARMS"
    )
    assert contract["linked_operational_layers"] == [
        {"id": "PROJECT_UNIVERSE"},
        {"id": "CONNECTOR_BRAIN"},
    ]
    assert contract["hil_only_layers"] == ["PROJECT_OVERLAY"]
