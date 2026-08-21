from __future__ import annotations

import sqlite3
from pathlib import Path

from evidence_lane_plugin import lane_engine as lane_engine_module
from evidence_lane_plugin.artifact_contract import (
    bind_tools_to_artifacts,
    build_four_file_contract,
    validate_four_file_contract,
)
from evidence_lane_plugin.hashing import atomic_write_bytes, atomic_write_json
from evidence_lane_plugin.lanes import LANE_REGISTRY


def _four_files(root: Path, lane_id: str = "chat_lineage") -> tuple[object, dict]:
    lane = LANE_REGISTRY[lane_id]
    root.mkdir()
    database_path = root / lane.sqlite_filename
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        lane_engine_module._create_lane_schema(connection, lane)
        connection.commit()
    finally:
        connection.close()
    mermaid, dot = lane_engine_module._lane_topology(lane, database_path, {})
    atomic_write_bytes(root / lane.mmd_filename, mermaid.encode("utf-8"))
    atomic_write_bytes(root / lane.dot_filename, dot.encode("utf-8"))
    tools = lane_engine_module._tool_identity(lane)
    atomic_write_json(root / "tools.json", bind_tools_to_artifacts(root, lane, tools))
    return lane, build_four_file_contract(root, lane)


def test_four_file_contract_binds_sqlite_mmd_dot_and_tools(tmp_path: Path) -> None:
    root = tmp_path / "lane"
    lane, contract = _four_files(root)

    report = validate_four_file_contract(root, lane, contract)

    assert report["status"] == "PASS"
    assert report["ordered_members"] == [
        lane.sqlite_filename,
        lane.mmd_filename,
        lane.dot_filename,
        "tools.json",
    ]
    assert report["tools_json_valid"] is True
    assert report["tools_artifact_authority_valid"] is True
    assert report["four_file_contract_valid"] is True


def test_manifest_reseal_cannot_hide_stale_tools_artifact_authority(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lane"
    lane, _contract = _four_files(root)
    dot_path = root / lane.dot_filename
    atomic_write_bytes(
        dot_path,
        dot_path.read_bytes() + b"// changed after tools binding\n",
    )
    resealed_contract = build_four_file_contract(root, lane)

    report = validate_four_file_contract(root, lane, resealed_contract)

    assert report["status"] == "FAIL"
    assert report["four_file_contract_valid"] is True
    assert report["tools_artifact_authority_valid"] is False
