from __future__ import annotations

import shutil
import sqlite3
import zipfile
from pathlib import Path

from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    register_source_batch,
)
from evidence_lane_plugin.source_sqlite import inspect_registered_sqlite_assets


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE parent(id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE child(
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parent(id),
                value TEXT NOT NULL
            );
            CREATE INDEX child_parent_idx ON child(parent_id);
            CREATE VIEW child_view AS SELECT id, value FROM child;
            CREATE VIRTUAL TABLE search USING fts5(body);
            INSERT INTO parent VALUES (1, 'p');
            INSERT INTO child VALUES (1, 1, 'c');
            INSERT INTO search(body) VALUES ('indexed evidence');
            PRAGMA user_version=7;
            """
        )
        connection.commit()
    finally:
        connection.close()


def _spec(path: Path, ordinal: int) -> SourceAuthoritySpec:
    return SourceAuthoritySpec(
        source=str(path),
        ordinal=ordinal,
        lane_id="sqlite_brain",
    )


def test_direct_sqlite_assets_are_inspected_once_per_exact_byte_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "first.sqlite"
    second = source / "second.sqlite"
    _database(first)
    shutil.copyfile(first, second)
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(registry, [_spec(source, 1)])

    receipt = inspect_registered_sqlite_assets(registry, batch["batch_id"])
    repeated = inspect_registered_sqlite_assets(registry, batch["batch_id"])

    assert receipt["status"] == "PASS"
    assert receipt["asset_occurrence_count"] == 2
    assert receipt["unique_byte_authority_count"] == 1
    assert receipt["canonical_inspection_count"] == 1
    assert receipt["canonical_pass_count"] == 1
    assert repeated["append_status"] == "IDEMPOTENT_REUSE"
    with sqlite3.connect(registry) as connection:
        states = {
            row[0]
            for row in connection.execute(
                "SELECT inspection_state FROM source_sqlite_asset"
            )
        }
        receipt_row = connection.execute(
            "SELECT * FROM source_sqlite_receipt"
        ).fetchone()
        foreign_key_count = connection.execute(
            "SELECT COUNT(*) FROM source_sqlite_foreign_key"
        ).fetchone()[0]
        exact_count_tables = connection.execute(
            "SELECT COUNT(*) FROM source_sqlite_table_stat WHERE count_state='EXACT'"
        ).fetchone()[0]
    assert states == {"CANONICAL_PASS", "DEDUP_REUSE_PASS"}
    assert receipt_row is not None
    assert foreign_key_count == 1
    assert exact_count_tables >= 2


def test_zip_sqlite_is_skipped_only_with_exact_extracted_counterpart(
    tmp_path: Path,
) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    database = extracted / "brain.sqlite"
    _database(database)
    archive = tmp_path / "brain.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(database, "brain/brain.sqlite")
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(
        registry,
        [_spec(extracted, 1), _spec(archive, 2)],
    )

    receipt = inspect_registered_sqlite_assets(registry, batch["batch_id"])

    assert receipt["status"] == "PASS"
    assert receipt["asset_occurrence_count"] == 2
    assert receipt["skip_exact_counterpart_asset_count"] == 1
    assert receipt["canonical_inspection_count"] == 1
    with sqlite3.connect(registry) as connection:
        states = {
            row[0]
            for row in connection.execute(
                "SELECT inspection_state FROM source_sqlite_asset"
            )
        }
    assert states == {
        "CANONICAL_PASS",
        "SKIPPED_EXACT_EXTRACTED_COUNTERPART",
    }


def test_independent_embedded_sqlite_is_deserialized_in_memory(tmp_path: Path) -> None:
    database = tmp_path / "brain.sqlite"
    _database(database)
    archive = tmp_path / "independent.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(database, "package/brain.sqlite")
    database.unlink()
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(registry, [_spec(archive, 1)])

    receipt = inspect_registered_sqlite_assets(registry, batch["batch_id"])

    assert receipt["status"] == "PASS"
    assert receipt["canonical_inspection_count"] == 1
    with sqlite3.connect(registry) as connection:
        mode = connection.execute(
            "SELECT inspection_mode FROM source_sqlite_receipt"
        ).fetchone()[0]
    assert mode == "sqlite_in_memory_deserialize_query_only"
    assert not (tmp_path / "package").exists()


def test_invalid_sqlite_header_is_recorded_as_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "not-a-database.sqlite").write_bytes(b"not sqlite")
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(registry, [_spec(source, 1)])

    receipt = inspect_registered_sqlite_assets(registry, batch["batch_id"])

    assert receipt["status"] == "MISMATCH"
    assert receipt["canonical_failure_count"] == 1
    assert receipt["failure_samples"][0]["error_code"] == (
        "SOURCE_SQLITE_HEADER_INVALID"
    )
    with sqlite3.connect(registry) as connection:
        status = connection.execute(
            "SELECT status FROM source_sqlite_receipt"
        ).fetchone()[0]
    assert status == "MISMATCH"
