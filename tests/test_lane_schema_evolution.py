from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file
from evidence_lane_plugin.lane_engine import (
    LANE_SCHEMA_LEDGER_DDL_SHA256,
    LaneSchemaEvolutionError,
    _create_lane_schema,
    _validate_lane_database,
    apply_lane_schema_migration,
    build_lane_bundle,
    compile_lane_schema_migration,
    lane_schema_evolution_status,
)
from evidence_lane_plugin.lanes import (
    LANE_REGISTRY,
    LANE_SCHEMA_EVOLUTION_POLICY_PATH,
    LANE_SCHEMA_EVOLUTION_POLICY_SHA256,
    lane_schema_asset,
    lane_schema_evolution_contract,
)

T0 = "2026-08-15T16:00:00Z"
T1 = "2026-08-15T16:01:00Z"


def _bound_memory_lane(lane_id: str) -> sqlite3.Connection:
    lane = LANE_REGISTRY[lane_id]
    asset = lane_schema_asset(lane_id)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    _create_lane_schema(connection, lane)
    connection.executemany(
        "INSERT INTO lane_meta(key, value) VALUES (?, ?)",
        [
            ("lane_id", lane_id),
            ("lane_schema_asset_version", str(asset["schema_version"])),
        ],
    )
    return connection


def _create_annotations_migration(
    lane_id: str,
    *,
    migration_id: str | None = None,
    rebuild_fts: bool = True,
) -> dict:
    version = lane_schema_asset(lane_id)["schema_version"]
    prefix = f"elx_{lane_id}_"
    return {
        "migration_id": migration_id or f"{lane_id}.extension.annotations.v001",
        "from_version": version,
        "to_version": version + 1,
        "operations": [
            {
                "kind": "CREATE_TABLE",
                "table": f"{prefix}annotations",
                "columns": [
                    {
                        "name": "annotation_id",
                        "type": "INTEGER",
                        "nullable": False,
                        "primary_key": True,
                    },
                    {
                        "name": "source_id",
                        "type": "INTEGER",
                        "nullable": True,
                        "primary_key": False,
                    },
                    {
                        "name": "locator",
                        "type": "TEXT",
                        "nullable": False,
                        "primary_key": False,
                    },
                ],
                "foreign_keys": [
                    {
                        "columns": ["source_id"],
                        "referenced_table": "source_registry",
                        "referenced_columns": ["source_id"],
                        "on_delete": "CASCADE",
                    }
                ],
            },
            {
                "kind": "CREATE_INDEX",
                "index": f"{prefix}annotations_locator_idx",
                "table": f"{prefix}annotations",
                "columns": ["locator"],
                "unique": False,
            },
        ],
        "rebuild_fts": rebuild_fts,
    }


def test_schema_evolution_policy_is_hash_bound_and_lane_specific() -> None:
    assert (
        sha256_file(LANE_SCHEMA_EVOLUTION_POLICY_PATH)
        == LANE_SCHEMA_EVOLUTION_POLICY_SHA256
    )
    docs = lane_schema_evolution_contract("docs")
    local = lane_schema_evolution_contract("local_code")
    github = lane_schema_evolution_contract("github_code")

    assert docs["schema"] == "evidence-lane.lane-schema-evolution-policy.v1"
    assert docs["asset_sha256"] == LANE_SCHEMA_EVOLUTION_POLICY_SHA256
    assert docs["physical_name_prefix"] == "elx_docs_"
    assert docs["migration_id_prefix"] == "docs.extension."
    assert docs["explicit_user_confirmation_required"] is False
    assert local["physical_name_prefix"] == "elx_local_code_"
    assert local["explicit_user_confirmation_required"] is True
    assert github["physical_name_prefix"] == "elx_github_code_"
    assert github["explicit_user_confirmation_required"] is True
    assert docs["migration"]["raw_sql_allowed"] is False
    assert docs["ledger"]["hash_chained"] is True


def test_docs_migration_seals_ddl_fk_index_fts_compatibility_and_chain() -> None:
    lane = LANE_REGISTRY["docs"]
    connection = _bound_memory_lane("docs")
    try:
        connection.execute(
            """
            INSERT INTO source_registry(
                path, size_bytes, sha256, mime_type, extension, encoding,
                parser_state, exact_bytes, registered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "guide.md",
                5,
                "A" * 64,
                "text/markdown",
                ".md",
                "utf-8",
                "PASS",
                b"guide",
                T0,
            ),
        )
        source_id = int(
            connection.execute("SELECT source_id FROM source_registry").fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO chunk_index(
                source_id, locator, ordinal, char_start, char_end,
                text_content, sha256, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_id, "line:1", 0, 0, 5, "guide", "B" * 64, "{}"),
        )
        chunk_id = int(
            connection.execute("SELECT chunk_id FROM chunk_index").fetchone()[0]
        )
        connection.execute(
            f"""INSERT INTO {lane.fts_table}(
                    path, locator, text_content, chunk_id
                ) VALUES (?, ?, ?, ?)""",  # nosec B608
            ("guide.md", "line:1", "guide", chunk_id),
        )

        first_migration = _create_annotations_migration("docs")
        applied = apply_lane_schema_migration(
            connection,
            lane,
            first_migration,
            applied_by="row213-test",
            applied_at=T0,
        )
        receipt = applied["receipt"]
        assert applied["state"] == "APPLIED"
        assert (
            receipt["ddl_sha256"]
            == compile_lane_schema_migration(lane, first_migration)["ddl_sha256"]
        )
        assert receipt["ledger_ddl_sha256"] == LANE_SCHEMA_LEDGER_DDL_SHA256
        assert receipt["foreign_key_errors"] == []
        assert receipt["fts_rebuild_proof"]["status"] == "PASS"
        assert receipt["fts_rebuild_proof"]["before_row_count"] == 1
        assert receipt["fts_rebuild_proof"]["after_row_count"] == 1
        assert receipt["compatibility_proof"]["status"] == "PASS"
        assert (
            receipt["compatibility_proof"]["base_schema_builder_projection"]["status"]
            == "PASS"
        )
        assert receipt["explicit_user_confirmation_required"] is False
        assert receipt["candidate_created"] is False
        assert receipt["hil_invoked"] is False
        assert receipt["pointer_moved"] is False

        fk = connection.execute(
            'PRAGMA foreign_key_list("elx_docs_annotations")'
        ).fetchone()
        assert fk[2] == "source_registry"
        assert fk[3] == "source_id"
        assert fk[4] == "source_id"
        index = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='index' AND name='elx_docs_annotations_locator_idx'"""
        ).fetchone()
        assert index is not None

        second_migration = {
            "migration_id": "docs.extension.annotations.v002",
            "from_version": first_migration["to_version"],
            "to_version": first_migration["to_version"] + 1,
            "operations": [
                {
                    "kind": "ADD_COLUMN",
                    "table": "elx_docs_annotations",
                    "column": {
                        "name": "confidence",
                        "type": "REAL",
                        "nullable": True,
                        "primary_key": False,
                    },
                },
                {
                    "kind": "CREATE_INDEX",
                    "index": "elx_docs_annotations_confidence_idx",
                    "table": "elx_docs_annotations",
                    "columns": ["confidence"],
                    "unique": False,
                },
            ],
            "rebuild_fts": False,
        }
        second = apply_lane_schema_migration(
            connection,
            lane,
            second_migration,
            applied_by="row213-test",
            applied_at=T1,
        )
        status = lane_schema_evolution_status(connection, lane)
        assert second["receipt"]["prior_receipt_sha256"] == receipt["receipt_sha256"]
        assert status["status"] == "PASS"
        assert status["migration_count"] == 2
        assert status["receipt_chain_valid"] is True
        assert status["immutable_triggers_valid"] is True
        assert status["effective_schema_version"] == second_migration["to_version"]

        repeated = apply_lane_schema_migration(
            connection,
            lane,
            second_migration,
            applied_by="row213-test",
            applied_at=T1,
        )
        assert repeated["state"] == "ALREADY_APPLIED"
        assert repeated["receipt"] == second["receipt"]

        with pytest.raises(sqlite3.DatabaseError, match="ledger is immutable"):
            connection.execute(
                "UPDATE lane_schema_migration SET applied_by='tampered' WHERE sequence=1"
            )
        with pytest.raises(sqlite3.DatabaseError, match="ledger is immutable"):
            connection.execute("DELETE FROM lane_schema_migration WHERE sequence=1")
    finally:
        connection.close()


def test_protected_code_lane_requires_exact_confirmation_before_any_write() -> None:
    lane = LANE_REGISTRY["local_code"]
    connection = _bound_memory_lane("local_code")
    migration = _create_annotations_migration("local_code", rebuild_fts=False)
    compiled = compile_lane_schema_migration(lane, migration)
    before = sha256_bytes(
        canonical_json_bytes(
            [
                tuple(row)
                for row in connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
                )
            ]
        )
    )
    try:
        assert compiled["status"] == "USER_CONFIRMATION_REQUIRED"
        assert compiled["explicit_user_confirmation_required"] is True
        with pytest.raises(LaneSchemaEvolutionError) as missing:
            apply_lane_schema_migration(
                connection,
                lane,
                migration,
                applied_by="row213-test",
                applied_at=T0,
            )
        assert missing.value.code == ("LANE_SCHEMA_EXPLICIT_USER_CONFIRMATION_REQUIRED")
        after = sha256_bytes(
            canonical_json_bytes(
                [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
                    )
                ]
            )
        )
        assert after == before
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='lane_schema_migration'"
            ).fetchone()
            is None
        )

        applied = apply_lane_schema_migration(
            connection,
            lane,
            migration,
            applied_by="row213-test",
            applied_at=T0,
            explicit_user_confirmation=compiled["required_confirmation"],
        )
        receipt = applied["receipt"]
        assert receipt["explicit_user_confirmation_required"] is True
        assert len(receipt["explicit_user_confirmation_sha256"]) == 64
        assert receipt["raw_user_confirmation_persisted"] is False
    finally:
        connection.close()


def test_invalid_foreign_key_rolls_back_ledger_and_extension_ddl() -> None:
    lane = LANE_REGISTRY["docs"]
    connection = _bound_memory_lane("docs")
    migration = _create_annotations_migration("docs", rebuild_fts=False)
    migration["operations"][0]["foreign_keys"][0]["referenced_table"] = "missing_parent"
    before = list(
        connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
    )
    try:
        with pytest.raises(LaneSchemaEvolutionError) as failed:
            apply_lane_schema_migration(
                connection,
                lane,
                migration,
                applied_by="row213-test",
                applied_at=T0,
            )
        assert failed.value.code == "LANE_SCHEMA_FOREIGN_KEY_TARGET_INVALID"
        after = list(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            )
        )
        assert after == before
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='lane_schema_migration'"
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='elx_docs_annotations'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()


def test_compiler_rejects_raw_sql_core_alter_and_noncontiguous_versions() -> None:
    lane = LANE_REGISTRY["docs"]
    migration = _create_annotations_migration("docs")

    raw_sql = dict(migration)
    raw_sql["sql"] = "DROP TABLE source_registry"
    with pytest.raises(LaneSchemaEvolutionError) as raw:
        compile_lane_schema_migration(lane, raw_sql)
    assert raw.value.code == "LANE_SCHEMA_MIGRATION_CONTRACT_INVALID"

    core_alter = {
        **migration,
        "operations": [
            {
                "kind": "ADD_COLUMN",
                "table": "source_registry",
                "column": {
                    "name": "unsafe",
                    "type": "TEXT",
                    "nullable": True,
                    "primary_key": False,
                },
            }
        ],
    }
    with pytest.raises(LaneSchemaEvolutionError) as core:
        compile_lane_schema_migration(lane, core_alter)
    assert core.value.code == "LANE_SCHEMA_CORE_TABLE_ALTER_FORBIDDEN"

    noncontiguous = {**migration, "to_version": migration["to_version"] + 1}
    with pytest.raises(LaneSchemaEvolutionError) as version:
        compile_lane_schema_migration(lane, noncontiguous)
    assert version.value.code == "LANE_SCHEMA_VERSION_TRANSITION_INVALID"


def test_migrated_built_lane_retains_database_validation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.md").write_text("# governed migration\n", encoding="utf-8")
    output = tmp_path / "bundle"
    build_lane_bundle(
        repository_root=source,
        output_directory=output,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV-SCHEMA-EVOLUTION",
        pointer_generation=0,
        source_overrides={"guide.md": "docs"},
    )
    lane = LANE_REGISTRY["docs"]
    database = output / "docs" / lane.sqlite_filename
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        result = apply_lane_schema_migration(
            connection,
            lane,
            _create_annotations_migration("docs"),
            applied_by="row213-integration-test",
            applied_at=T0,
        )
        connection.commit()
    finally:
        connection.close()

    assert result["ledger"]["status"] == "PASS"
    validation = _validate_lane_database(database, lane)
    assert validation["status"] == "PASS"
    assert validation["lane_schema_evolution"]["status"] == "PASS"
    assert validation["lane_schema_evolution"]["migration_count"] == 1
