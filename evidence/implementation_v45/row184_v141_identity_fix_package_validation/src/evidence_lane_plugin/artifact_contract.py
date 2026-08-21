"""Exact, non-cyclic contract for each lane's four stable authorities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import LaneDefinition

TOOLS_ARTIFACT_AUTHORITY_SCHEMA = "evidence-lane.tools-artifact-authority.v1"
FOUR_FILE_CONTRACT_SCHEMA = "evidence-lane.lane-four-file-contract.v1"


def stable_artifact_names(lane: LaneDefinition) -> tuple[str, str, str, str]:
    return (
        lane.sqlite_filename,
        lane.mmd_filename,
        lane.dot_filename,
        "tools.json",
    )


def _member(path: Path, root: Path, *, role: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _tools_authority_core(
    lane_root: Path,
    lane: LaneDefinition,
    *,
    tool_identity_sha256: str,
) -> dict[str, Any]:
    members = [
        _member(lane_root / lane.sqlite_filename, lane_root, role="sqlite_authority"),
        _member(lane_root / lane.mmd_filename, lane_root, role="mermaid_projection"),
        _member(lane_root / lane.dot_filename, lane_root, role="dot_projection"),
    ]
    return {
        "schema": TOOLS_ARTIFACT_AUTHORITY_SCHEMA,
        "lane_id": lane.canonical_lane_id,
        "tool_identity_sha256": tool_identity_sha256,
        "ordered_members": [row["path"] for row in members],
        "members": members,
    }


def bind_tools_to_artifacts(
    lane_root: str | Path,
    lane: LaneDefinition,
    tools: dict[str, Any],
) -> dict[str, Any]:
    """Bind a tool identity to SQLite/MMD/DOT before writing tools.json."""

    root = Path(lane_root).resolve()
    core = _tools_authority_core(
        root,
        lane,
        tool_identity_sha256=str(tools["sha256"]),
    )
    authority = {
        **core,
        "authority_sha256": sha256_bytes(canonical_json_bytes(core)),
    }
    return {**tools, "artifact_authority": authority}


def build_four_file_contract(
    lane_root: str | Path,
    lane: LaneDefinition,
) -> dict[str, Any]:
    """Hash all four stable authorities after tools.json has been written."""

    root = Path(lane_root).resolve()
    roles = (
        "sqlite_authority",
        "mermaid_projection",
        "dot_projection",
        "toolchain_identity",
    )
    members = [
        _member(root / name, root, role=role)
        for name, role in zip(stable_artifact_names(lane), roles, strict=True)
    ]
    core = {
        "schema": FOUR_FILE_CONTRACT_SCHEMA,
        "lane_id": lane.canonical_lane_id,
        "ordered_members": [row["path"] for row in members],
        "members": members,
    }
    return {
        **core,
        "contract_sha256": sha256_bytes(canonical_json_bytes(core)),
    }


def _tool_identity_core(tools: dict[str, Any]) -> dict[str, Any]:
    return {
        key: tools.get(key)
        for key in (
            "lane",
            "capabilities",
            "lane_schema_version",
            "topology_generator",
            "artifact_contract",
        )
    }


def validate_four_file_contract(
    lane_root: str | Path,
    lane: LaneDefinition,
    declared_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    """Independently recompute tool and four-file bindings read-only."""

    root = Path(lane_root).resolve()
    expected_names = list(stable_artifact_names(lane))
    missing = [name for name in expected_names if not (root / name).is_file()]
    if missing:
        return {
            "schema": FOUR_FILE_CONTRACT_SCHEMA,
            "lane_id": lane.canonical_lane_id,
            "valid": False,
            "status": "FAIL",
            "missing": missing,
            "tools_json_valid": False,
            "tools_artifact_authority_valid": False,
            "four_file_contract_valid": False,
        }
    try:
        tools = json.loads((root / "tools.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        tools = {}
    tool_identity_core = _tool_identity_core(tools)
    computed_tool_identity_sha256 = sha256_bytes(
        canonical_json_bytes(tool_identity_core)
    )
    tools_json_valid = bool(
        tools.get("lane", {}).get("canonical_lane_id") == lane.canonical_lane_id
        and tools.get("sha256") == computed_tool_identity_sha256
    )
    expected_tools_core = _tools_authority_core(
        root,
        lane,
        tool_identity_sha256=str(tools.get("sha256") or ""),
    )
    expected_tools_authority = {
        **expected_tools_core,
        "authority_sha256": sha256_bytes(
            canonical_json_bytes(expected_tools_core)
        ),
    }
    declared_tools_authority = tools.get("artifact_authority")
    tools_artifact_authority_valid = (
        declared_tools_authority == expected_tools_authority
    )
    computed_contract = build_four_file_contract(root, lane)
    four_file_contract_valid = declared_contract == computed_contract
    valid = bool(
        tools_json_valid
        and tools_artifact_authority_valid
        and four_file_contract_valid
    )
    return {
        "schema": FOUR_FILE_CONTRACT_SCHEMA,
        "lane_id": lane.canonical_lane_id,
        "ordered_members": expected_names,
        "members": computed_contract["members"],
        "computed_contract_sha256": computed_contract["contract_sha256"],
        "declared_contract_sha256": (
            declared_contract.get("contract_sha256")
            if isinstance(declared_contract, dict)
            else None
        ),
        "computed_tool_identity_sha256": computed_tool_identity_sha256,
        "declared_tool_identity_sha256": tools.get("sha256"),
        "computed_tools_authority_sha256": expected_tools_authority[
            "authority_sha256"
        ],
        "declared_tools_authority_sha256": (
            declared_tools_authority.get("authority_sha256")
            if isinstance(declared_tools_authority, dict)
            else None
        ),
        "missing": [],
        "tools_json_valid": tools_json_valid,
        "tools_artifact_authority_valid": tools_artifact_authority_valid,
        "four_file_contract_valid": four_file_contract_valid,
        "valid": valid,
        "status": "PASS" if valid else "FAIL",
    }
