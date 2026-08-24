from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.mcp_server import create_mcp_server

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "evidence-lane-plugin"
PUBLIC = PLUGIN / "schemas" / "public-action-schemas.v001.json"
RUNTIME = (
    PLUGIN
    / "src"
    / "evidence_lane_plugin"
    / "schemas"
    / "public-action-schemas.v001.json"
)


def test_public_action_schema_catalog_is_mirrored_and_complete() -> None:
    assert PUBLIC.read_bytes() == RUNTIME.read_bytes()
    catalog = json.loads(PUBLIC.read_text(encoding="utf-8"))
    assert catalog["tool_count"] == 88
    assert catalog["read_tool_count"] == 27
    assert catalog["write_tool_count"] == 61
    assert catalog["lane_count"] == 18
    assert len(catalog["six_authorities"]) == 6
    assert catalog["governance"] == ["ENV", "UOP"]
    assert catalog["hooks_required_for_explicit_actions"] is False
    assert catalog["accepted_hil_archive_queried_by_ordinary_actions"] is False
    assert len(catalog["tools"]) == 88
    assert len({item["name"] for item in catalog["tools"]}) == 88
    assert all(item["input_schema"]["type"] == "object" for item in catalog["tools"])
    assert all(len(item["schema_sha256"]) == 64 for item in catalog["tools"])

    live_names = {
        tool.name for tool in create_mcp_server()._tool_manager.list_tools()
    }
    assert live_names == {item["name"] for item in catalog["tools"]}
