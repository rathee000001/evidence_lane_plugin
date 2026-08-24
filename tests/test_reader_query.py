from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.lane_engine import build_lane_bundle

from .conftest import build_and_approve_pv1


def _materialize_live_root(service, source_repository: Path) -> None:
    pointer = service.store.pointer("book-faires")
    build_lane_bundle(
        repository_root=source_repository,
        output_directory=service.store.project_root("book-faires") / "sectors",
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=pointer.accepted_pv,
        proposed_pv=f"{pointer.accepted_pv}_WORKING",
        pointer_generation=pointer.generation,
        include_untracked=False,
        materialize_all_lanes=True,
        index_git_history=False,
    )


def _ready(service, source_repository: Path) -> None:
    build_and_approve_pv1(service)
    _materialize_live_root(service, source_repository)


def test_reader_queries_live_root_and_never_accepted_archive(
    service,
    source_repository: Path,
) -> None:
    _ready(service, source_repository)

    result = service.reader.search("book-faires", "list_books", limit=5)

    assert result["status"] == "PASS"
    assert result["authority_state"] == "LIVE_PROJECT_ROOT"
    assert result["live_working_truth"] is True
    assert result["accepted_truth"] is False
    assert result["accepted_archive_opened"] is False
    assert result["accepted_archive_queried"] is False
    assert result["results"]
    assert result["results"][0]["provenance"]["authority"] == (
        "LIVE_ROOT_LOCAL_CODE"
    )


@pytest.mark.parametrize(
    "pv_ref",
    ["PV1", "PV2_CANDIDATE__RUN_FORBIDDEN"],
)
def test_reader_rejects_accepted_and_candidate_refs(
    service,
    source_repository: Path,
    pv_ref: str,
) -> None:
    _ready(service, source_repository)

    with pytest.raises(EvidenceLaneError) as blocked:
        service.reader.search("book-faires", "list_books", pv_ref=pv_ref)

    assert blocked.value.code == "PV_ARCHIVE_QUERY_OBSOLETE"


def test_reader_uses_current_tracked_worktree_bytes(
    service,
    source_repository: Path,
) -> None:
    build_and_approve_pv1(service)
    app_path = source_repository / "src" / "app.py"
    app_path.write_text(
        app_path.read_text(encoding="utf-8")
        + "\nLIVE_ROOT_ONLY_SENTINEL = 'working authority'\n",
        encoding="utf-8",
    )
    _materialize_live_root(service, source_repository)

    result = service.reader.search("book-faires", "LIVE_ROOT_ONLY_SENTINEL")

    assert result["result_state"] == "HITS"
    assert result["live_source_used"] is True
    assert result["retrieval_authority"] == "LIVE_ROOT_SECTORS_LOCAL_CODE"
    assert result["browser_history_used"] is False
    assert result["scrollback_used"] is False
    assert result["transcript_used"] is False


def test_fetch_query_summary_and_receipts_are_bounded(
    service,
    source_repository: Path,
) -> None:
    _ready(service, source_repository)
    search = service.reader.search("book-faires", "list_books", limit=5)
    chunk = next(row for row in search["results"] if row["id"].startswith("chunk:"))

    chunk_result = service.reader.fetch("book-faires", chunk["id"], max_bytes=500)
    file_result = service.reader.fetch(
        "book-faires",
        "file:src/app.py",
        start_line=5,
        end_line=20,
        max_lines=2,
    )
    imports = service.reader.query("book-faires", "imports", value="flask")
    receipts = service.reader.query("book-faires", "receipts", limit=2)
    summary = service.reader.project_summary("book-faires")

    assert "def list_books" in chunk_result["text"]
    assert file_result["start_line"] == 5
    assert file_result["end_line"] == 6
    assert imports["rows"][0]["path"] == "src/app.py"
    assert "payload" in imports["rows"][0]
    assert all("details" not in row and "details_json" not in row for row in receipts["rows"])
    assert summary["counts"]["source_registry"] >= 2
    assert summary["accepted_archive_opened"] is False


def test_project_overlay_diff_is_live_root_only(
    service,
    source_repository: Path,
) -> None:
    _ready(service, source_repository)
    overlay = service.store.project_root("book-faires") / "project_overlay"
    overlay.mkdir()
    database = overlay / "project_overlay.sqlite"
    import sqlite3

    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE overlay_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sector_candidate_snapshot(sector_id TEXT PRIMARY KEY);
        CREATE TABLE fusion_receipt(receipt_id TEXT PRIMARY KEY);
        INSERT INTO overlay_meta VALUES('from_pv','PV0'),('to_pv','PV1');
        INSERT INTO sector_candidate_snapshot VALUES('local_code');
        INSERT INTO fusion_receipt VALUES('receipt-1');
        """
    )
    connection.commit()
    connection.close()

    result = service.reader.diff("book-faires", "PV0", "PV1")

    assert result["comparison_authority"] == "LIVE_ROOT_PROJECT_OVERLAY"
    assert result["accepted_archive_opened"] is False
    assert result["accepted_archive_queried"] is False
