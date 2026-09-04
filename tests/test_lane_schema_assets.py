from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.lane_engine import (
    _create_lane_schema,
    build_lane_bundle,
    lane_schema_builder_projection,
    validate_lane_bundle,
)
from evidence_lane_plugin.lanes import (
    CANONICAL_LANE_IDS,
    LANE_REGISTRY,
    LANE_SCHEMA_REGISTRY_PATH,
    LANE_SCHEMA_REGISTRY_SHA256,
    PRIMARY_CODE_LANES,
    lane_schema_asset,
    lane_schema_registry_contract,
)


def test_all_lane_schema_assets_are_versioned_hash_bound_and_authorized() -> None:
    registry = lane_schema_registry_contract()
    raw = json.loads(LANE_SCHEMA_REGISTRY_PATH.read_text(encoding="utf-8"))

    assert registry == {
        "schema": "evidence-lane.lane-schema-registry.v1",
        "registry_version": 1,
        "asset_path": "schemas/lane-schema-registry.v001.json",
        "asset_sha256": LANE_SCHEMA_REGISTRY_SHA256,
        "lane_count": len(CANONICAL_LANE_IDS),
        "base_schema_id": "evidence-lane.universal-lane.v5",
        "entity_table_template_id": "GENERIC_ENTITY_RECORD_V1",
        "extension_model": "PER_LANE_NAMESPACED_ADDITIVE_VERSIONING",
    }
    assert sha256_file(LANE_SCHEMA_REGISTRY_PATH) == LANE_SCHEMA_REGISTRY_SHA256
    assert [row["lane_id"] for row in raw["lanes"]] == list(CANONICAL_LANE_IDS)

    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        asset = lane_schema_asset(lane_id)
        assert asset["schema_id"] == (
            f"evidence-lane.lane-schema.{lane_id}.v"
            f"{asset['schema_version']:03d}"
        )
        assert asset["schema_version"] == asset["migration_ledger"][-1]["to_version"]
        assert asset["base_schema_id"] == "evidence-lane.universal-lane.v5"
        assert asset["fts_table"] == lane.fts_table
        assert asset["tables"] == list(lane.schema_contract)
        assert len(asset["contract_sha256"]) == 64
        assert asset["extension_namespace"] == f"evidence_lane.{lane_id}.extensions"
        assert asset["mutation_policy"] == lane.mutation_policy
        assert asset["lane_table_builder"] == (
            "GIT_HISTORY_V2_PLUS_GENERIC_ENTITY_RECORD_V1"
            if lane_id in PRIMARY_CODE_LANES
            else "GENERIC_ENTITY_RECORD_V1"
        )
        assert asset["migration_ledger"][0]["operation"] == (
            "BASELINE_BIND_EXISTING_SCHEMA"
        )
        expected_migration_id = (
            f"{lane_id}.tool-route-execution-ledger."
            f"v{asset['schema_version']:03d}"
        )
        expected_operation = "REBUILD_WITH_TOOL_ROUTE_AND_EXECUTION_LEDGER"
        assert asset["migration_ledger"][-1] == {
            "migration_id": expected_migration_id,
            "sequence": len(asset["migration_ledger"]),
            "from_version": asset["schema_version"] - 1,
            "to_version": asset["schema_version"],
            "operation": expected_operation,
            "additive_only": False,
            "rebuild_required": True,
        }
        assert "source_content_cas" in asset["tables"]
        assert "authority_index_content_cas" in asset["tables"]
        assert len(asset["sqlite_master_projection_sha256"]) == 64


def test_lane_schema_asset_returns_detached_bytes() -> None:
    first = lane_schema_asset("docs")
    first["tables"].append("not_authority")
    first["migration_ledger"][0]["additive_only"] = False

    second = lane_schema_asset("docs")
    assert "not_authority" not in second["tables"]
    assert second["migration_ledger"][0]["additive_only"] is True


def test_builder_sqlite_master_bytes_match_all_eighteen_schema_assets() -> None:
    for lane_id in CANONICAL_LANE_IDS:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            lane = LANE_REGISTRY[lane_id]
            _create_lane_schema(connection, lane)
            projection = lane_schema_builder_projection(connection, lane)
        finally:
            connection.close()

        assert projection["status"] == "PASS"
        assert projection["builder_schema_byte_parity"] is True
        assert projection["missing_tables"] == []
        assert projection["table_count"] == len(lane.schema_contract)
        assert projection["actual_sqlite_master_projection_sha256"] == (
            lane_schema_asset(lane_id)["sqlite_master_projection_sha256"]
        )


def test_builder_projection_detects_a_missing_asset_table() -> None:
    lane = LANE_REGISTRY["discussion"]
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        _create_lane_schema(connection, lane)
        connection.execute("DROP TABLE discussion_item")
        projection = lane_schema_builder_projection(connection, lane)
    finally:
        connection.close()

    assert projection["status"] == "MISMATCH"
    assert projection["builder_schema_byte_parity"] is False
    assert projection["missing_tables"] == ["discussion_item"]
    assert projection["actual_sqlite_master_projection_sha256"] is None


def test_built_lane_binds_schema_asset_and_projection_in_sqlite(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.md").write_text("# Versioned lane schema\n", encoding="utf-8")
    output = tmp_path / "bundle"

    build_lane_bundle(
        repository_root=source,
        output_directory=output,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV-SCHEMA-ASSET",
        pointer_generation=0,
        source_overrides={"guide.md": "docs"},
    )
    lane = LANE_REGISTRY["docs"]
    asset = lane_schema_asset("docs")
    database = output / "docs" / lane.sqlite_filename
    with sqlite3.connect(database) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM lane_meta"))
        connection.row_factory = sqlite3.Row
        projection = lane_schema_builder_projection(connection, lane)

    assert metadata["lane_schema_id"] == asset["schema_id"]
    assert metadata["lane_schema_asset_version"] == str(asset["schema_version"])
    assert metadata["lane_schema_contract_sha256"] == asset["contract_sha256"]
    assert metadata["lane_schema_registry_sha256"] == LANE_SCHEMA_REGISTRY_SHA256
    assert metadata["lane_schema_sqlite_master_projection_sha256"] == asset[
        "sqlite_master_projection_sha256"
    ]
    assert metadata["lane_schema_extension_namespace"] == asset[
        "extension_namespace"
    ]
    assert metadata["lane_schema_migration_head"] == asset["migration_ledger"][-1][
        "migration_id"
    ]
    assert projection["status"] == "PASS"
    assert validate_lane_bundle(output)["valid"] is True

    tools = json.loads((output / "docs" / "tools.json").read_text("utf-8"))
    assert tools["lane_schema_asset"]["contract_sha256"] == asset[
        "contract_sha256"
    ]
    manifest = json.loads(
        (output / "docs" / "lane_manifest.json").read_text("utf-8")
    )
    assert manifest["lane"]["lane_schema_contract_sha256"] == asset[
        "contract_sha256"
    ]
