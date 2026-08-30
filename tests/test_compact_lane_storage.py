from __future__ import annotations

from pathlib import Path

from evidence_lane_plugin.compact_storage import (
    read_source_record,
    verify_lane_compact_storage,
)
from evidence_lane_plugin.lane_engine import (
    _insert_source,
    _open_lane,
    _rebuild_retrieval,
)
from evidence_lane_plugin.lanes import LANE_REGISTRY


def test_shared_lane_storage_is_lossless_compressed_and_contentless_fts(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    payload = ("def current_route():\n    return 'registry-derived'\n" * 2_000).encode()
    (repository / "route.py").write_bytes(payload)
    lane = LANE_REGISTRY["local_code"]
    database = tmp_path / lane.sqlite_filename
    connection = _open_lane(database, lane, initialize=True)
    try:
        _insert_source(
            connection,
            lane,
            repository,
            "route.py",
            registered_at="2026-08-28T00:00:00+00:00",
            snapshot_ref="TEST-STAGED-TREE",
            host_profile="CODEX_DESKTOP",
        )
        _rebuild_retrieval(connection, lane)
        connection.commit()
        report = verify_lane_compact_storage(connection, fts_table=lane.fts_table)
        row, restored = read_source_record(connection, path="route.py")
        columns = {
            str(item[1])
            for item in connection.execute("PRAGMA table_info(source_registry)")
        }
        hits = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {lane.fts_table} "  # nosec B608
                f"WHERE {lane.fts_table} MATCH 'registry'"  # nosec B608
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert report["status"] == "PASS"
    assert report["source_compressed_bytes"] < report["source_raw_bytes"]
    assert report["chunk_compressed_bytes"] < report["chunk_raw_bytes"]
    assert report["fts_contentless"] is True
    assert report["duplicate_inline_source_blob_present"] is False
    assert "exact_bytes" not in columns
    assert row is not None and row["sha256"]
    assert restored == payload
    assert hits > 0
