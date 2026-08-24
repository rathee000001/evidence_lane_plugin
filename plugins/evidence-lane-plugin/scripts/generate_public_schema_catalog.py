"""Generate the mirrored public schema catalog from the live FastMCP registry."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXPECTED_TOOL_COUNT = 88
EXPECTED_READ_COUNT = 27
EXPECTED_WRITE_COUNT = 61
EXPECTED_LANE_COUNT = 18

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    )


def _annotation(tool: Any) -> dict[str, Any]:
    annotations = tool.annotations
    if annotations is None:
        return {}
    if hasattr(annotations, "model_dump"):
        return annotations.model_dump(mode="json", exclude_none=True)
    return dict(annotations)


def build_catalog() -> dict[str, Any]:
    from evidence_lane_plugin.lanes import LANE_REGISTRY
    from evidence_lane_plugin.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = sorted(asyncio.run(server.list_tools()), key=lambda item: item.name)
    if len(tools) != EXPECTED_TOOL_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_TOOL_COUNT} public MCP actions, found {len(tools)}."
        )

    records: list[dict[str, Any]] = []
    for tool in tools:
        annotations = _annotation(tool)
        body = {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.inputSchema,
            "output_schema": tool.outputSchema,
            "annotations": annotations,
        }
        records.append(
            {
                **body,
                "schema_sha256": hashlib.sha256(_canonical(body)).hexdigest().upper(),
            }
        )

    read_count = sum(
        bool(record["annotations"].get("readOnlyHint")) for record in records
    )
    write_count = len(records) - read_count
    if (read_count, write_count) != (EXPECTED_READ_COUNT, EXPECTED_WRITE_COUNT):
        raise RuntimeError(
            "Public read/write classification drifted: "
            f"{read_count} read / {write_count} write."
        )
    lanes = sorted(LANE_REGISTRY)
    if len(lanes) != EXPECTED_LANE_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_LANE_COUNT} canonical lanes, found {len(lanes)}."
        )

    body = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema": "evidence-lane.public-action-schema-catalog.v1",
        "tool_count": len(records),
        "read_tool_count": read_count,
        "write_tool_count": write_count,
        "lane_count": len(lanes),
        "canonical_lanes": lanes,
        "six_authorities": [
            "PROJECT_SECTORS_AND_PROJECT_ENGULF",
            "AI_LEARNING",
            "CANON_GRAPH",
            "PROJECT_MEMORY",
            "AGENTS_MD",
            "HOST_CONVERSATION_MEMORY_MD",
        ],
        "governance": ["ENV", "UOP"],
        "hooks_required_for_explicit_actions": False,
        "accepted_hil_archive_queried_by_ordinary_actions": False,
        "shared_contracts": {
            "live_root_query": "live-root-six-authority-query.v001.json",
            "lane_fts5_bm25": "lane-search-fts5.v001.json",
            "canon": "canon/canon-consequence-graph.v1.sql",
            "memory": "memory/project-memory.v1.sql",
            "universe": "universe/project-universe.v1.sql",
        },
        "tools": records,
    }
    return {
        **body,
        "catalog_sha256": hashlib.sha256(_canonical(body)).hexdigest().upper(),
    }


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    public_path = plugin_root / "schemas" / "public-action-schemas.v001.json"
    runtime_path = (
        plugin_root
        / "src"
        / "evidence_lane_plugin"
        / "schemas"
        / "public-action-schemas.v001.json"
    )
    payload = json.dumps(build_catalog(), indent=2, ensure_ascii=False) + "\n"
    public_path.write_text(payload, encoding="utf-8", newline="\n")
    runtime_path.write_text(payload, encoding="utf-8", newline="\n")
    print(public_path)
    print(runtime_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
