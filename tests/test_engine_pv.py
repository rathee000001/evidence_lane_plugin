from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin import database
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.pv_package import validate_pv_package

from .conftest import boot_local


def test_database_context_closes_connection_and_wal_handles(tmp_path: Path) -> None:
    path = tmp_path / "code.sqlite"
    database.initialize(path)

    with database.connect(path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("close_probe", "PASS"),
        )

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_initial_pv_captures_svelte_exact_bytes_and_fts(
    service,
    source_repository: Path,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    result = service.build_initial("book-faires", session_id)
    candidate = Path(result["candidate"]["stored_path"])
    validation = validate_pv_package(candidate)
    assert validation["status"] == "PASS"
    assert validation["proposed_pv"] == "PV1"
    assert validation["rendering_status"] in {
        "PASS",
        "RENDER_SKIPPED",
        "RENDER_FAILED",
    }
    assert sorted(path.name for path in candidate.iterdir() if path.is_file())[:1]
    with sqlite3.connect(candidate / "code.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        svelte = connection.execute(
            """
            SELECT path, exact_bytes, sha256, code_family, ingestion_status
            FROM files WHERE path = 'src/Counter.svelte'
            """
        ).fetchone()
        assert svelte is not None
        assert svelte["code_family"] == "svelte"
        assert svelte["ingestion_status"] == "EXACT_TEXT_CHUNKED"
        assert (
            bytes(svelte["exact_bytes"])
            == (source_repository / "src" / "Counter.svelte").read_bytes()
        )
        chunks = connection.execute(
            """
            SELECT COUNT(*)
            FROM chunks c JOIN files f ON f.file_id = c.file_id
            WHERE f.path = 'src/Counter.svelte'
            """
        ).fetchone()[0]
        assert chunks >= 1
        search = connection.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'increment'"
        ).fetchone()[0]
        assert search >= 1
        symbols = connection.execute(
            """
            SELECT COUNT(*)
            FROM symbols s JOIN files f ON f.file_id = s.file_id
            WHERE f.path = 'src/Counter.svelte' AND s.name = 'increment'
            """
        ).fetchone()[0]
        assert symbols == 1
    forbidden = [
        path.name.lower()
        for path in candidate.rglob("*")
        if path.is_file() and path.name.lower() in {"env.json", "uop.json"}
    ]
    assert forbidden == []


def test_tamper_is_detected(service) -> None:
    boot = boot_local(service)
    result = service.build_initial("book-faires", boot["session"]["session_id"])
    candidate = Path(result["candidate"]["stored_path"])
    receipt = candidate / "entry_slip.json"
    receipt.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(EvidenceLaneError) as error:
        validate_pv_package(candidate)
    assert error.value.code == "PV_CHECKSUM_MISMATCH"


def test_renderer_failure_is_warning_not_sqlite_failure(
    service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MERMAID_CLI", raising=False)
    boot = boot_local(service)
    result = service.build_initial("book-faires", boot["session"]["session_id"])
    candidate = Path(result["candidate"]["stored_path"])
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    receipt = json.loads((candidate / "pv_receipt.json").read_text(encoding="utf-8"))
    assert manifest["rendering"]["status"] == "RENDER_SKIPPED"
    assert receipt["status"] == "PASS"
    assert receipt["database_validation"]["valid"] is True
