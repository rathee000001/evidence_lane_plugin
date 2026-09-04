from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.live_root_normalization import (
    LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    execute_live_root_normalization,
    plan_live_root_normalization,
)
from evidence_lane_plugin.receipt_ledger import (
    PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS,
    append_project_authority_operational_receipt,
    append_receipt_bytes,
    backfill_project_authority_operational_receipts,
    initialize_receipt_ledger,
    read_receipt,
)


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "custom-project-folder"
    _write(root / "accepted" / "must-not-open.txt", b"forbidden")
    _write(root / "internal_sources" / "old.txt", b"source-history")
    _write(
        root
        / "internal_sources"
        / "remote_adapter_generated_caches_20260827"
        / "cache.bin",
        b"generated-cache",
    )
    _write(root / "receipts" / "old.json", b'{"schema":"old-receipt"}')
    initialize_receipt_ledger(root / "receipts" / "receipt-ledger.sqlite")
    ledger = sqlite3.connect(root / "receipts" / "receipt-ledger.sqlite")
    try:
        append_receipt_bytes(
            ledger,
            logical_path="earlier/old.json",
            data=b'{"schema":"old-receipt"}',
            receipt_kind="EARLIER_MIGRATION",
            project_id="wrong-project-id",
        )
        ledger.commit()
    finally:
        ledger.close()
    _write(
        root / "receipts" / "candidate-overlays" / "PV13_CANDIDATE.json",
        b'{"candidate_id":"PV13_CANDIDATE"}',
    )
    _write(
        root / "sessions" / "session-a.json",
        json.dumps(
            {
                "session_id": "session-a",
                "project_id": "project-a",
                "state": "ACTIVE",
                "metadata": {"current_host_session_id": "task-a"},
                "updated_at": "2026-08-27T00:00:00Z",
            }
        ).encode(),
    )
    _write(
        root / "direct_state_travel_entries" / "direct-a.json",
        json.dumps(
            {
                "status": "PASS",
                "receipt": {
                    "session_id": "session-a",
                    "route": "DIRECT",
                    "host_task_binding": {
                        "authoritative_source": {"task_id": "source-a"}
                    },
                    "destination_orchestration": {
                        "destination": {"task_id": "task-a"}
                    },
                },
            }
        ).encode(),
    )
    _write(
        root / "project.json",
        json.dumps(
            {
                "schema": "evidence-lane.project-registry.v1",
                "project_id": "project-a",
                "project_authority_root": str(root),
            },
            separators=(",", ":"),
        ).encode(),
    )
    _write(root / "active_pointer.json", b'{"accepted_pv":"PV12"}')
    _write(root / "active_session.json", b'{"session_id":"session-a"}')
    _write(root / "profiles" / "profile.json", b'{"profile":"study"}')
    _write(
        root / "sectors" / "local_code" / "accepted_history" / "PV12" / "x.json",
        b'{"historical":true}',
    )
    lane = root / "sectors" / "local_code" / "local-code.sqlite"
    lane.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(lane).close()
    connector = root / "connector_brain.sqlite"
    connection = sqlite3.connect(connector)
    connection.execute("CREATE TABLE brain(id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    return root


def _operational_receipt(
    *,
    schema: str,
    project_id: str | None,
    revision: int = 1,
) -> bytes:
    body: dict[str, object] = {
        "schema": schema,
        "status": "PASS",
        "revision": revision,
        "recorded_at": f"2026-09-04T00:00:0{revision}Z",
    }
    if project_id is not None:
        body["project_id"] = project_id
    return canonical_json_bytes(
        {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}
    )


def test_plan_never_enters_accepted_and_is_content_addressed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    plan = plan_live_root_normalization(root)
    paths = {row["source_relative_path"] for row in plan["rows"]}
    assert not any(path.startswith("accepted/") for path in paths)
    assert plan["accepted_folder_queried"] is False
    assert plan["accepted_archive_used_as_authority"] is False
    assert len(plan["plan_sha256"]) == 64
    assert "internal_sources/old.txt" in paths
    assert plan["purge_only_file_count"] == 1
    assert plan["purge_only_rows"][0]["source_relative_path"].endswith(
        "remote_adapter_generated_caches_20260827/cache.bin"
    )
    assert "direct_state_travel_entries/direct-a.json" in paths
    assert "sectors/local_code/accepted_history/PV12/x.json" not in paths
    assert plan["preserved_root_nested_history_lane_ids"] == ["local_code"]
    assert plan["root_nested_accepted_history_ingested_into_live_rows"] is False
    assert plan["preserved_operational_file_count"] == 5


def test_execution_ingests_reads_back_then_purges_exact_sources(tmp_path: Path) -> None:
    root = _root(tmp_path)
    plan = plan_live_root_normalization(root)
    receipt = execute_live_root_normalization(
        root,
        expected_plan_sha256=plan["plan_sha256"],
        confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    )
    assert receipt["state"] == "MIGRATED_AND_PURGED_AFTER_READBACK"
    assert receipt["project_id"] == "project-a"
    assert receipt["accepted_folder_queried"] is False
    assert (root / "accepted" / "must-not-open.txt").read_bytes() == b"forbidden"
    assert not (root / "internal_sources").exists()
    assert not (root / "direct_state_travel_entries").exists()
    assert not (root / "connector_brain.sqlite").exists()
    assert (root / "connector_brain" / "connector-brain.sqlite").is_file()
    assert (
        root / "sectors" / "local_code" / "accepted_history" / "PV12" / "x.json"
    ).is_file()
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))[
        "project_id"
    ] == "project-a"
    assert (root / "active_pointer.json").read_bytes() == b'{"accepted_pv":"PV12"}'
    assert (root / "active_session.json").read_bytes() == b'{"session_id":"session-a"}'
    assert (root / "sessions" / "session-a.json").is_file()
    assert (
        root / "receipts" / "candidate-overlays" / "PV13_CANDIDATE.json"
    ).is_file()
    assert receipt["preserved_operational_file_count"] == 5

    migrated = read_receipt(
        root / "receipts" / "receipt-ledger.sqlite",
        logical_path="historical-live-root/old.json",
    )
    assert migrated["schema"] == "old-receipt"
    ledger = sqlite3.connect(root / "receipts" / "receipt-ledger.sqlite")
    try:
        assert ledger.execute(
            "SELECT DISTINCT project_id FROM receipt_record"
        ).fetchall() == [("project-a",), ("wrong-project-id",)]
        old_receipt_sha256 = ledger.execute(
            "SELECT receipt_sha256 FROM receipt_record WHERE logical_path='earlier/old.json'"
        ).fetchone()[0]
        assert ledger.execute(
            "SELECT COUNT(*) FROM receipt_record WHERE receipt_sha256=?",
            (old_receipt_sha256,),
        ).fetchone()[0] == 1
        relations = {
            row[0]
            for row in ledger.execute(
                "SELECT relation FROM receipt_link WHERE source_receipt_sha256=?",
                (old_receipt_sha256,),
            )
        }
        assert any(value.startswith("LOGICAL_ALIAS:") for value in relations)
        assert any(value.startswith("PROJECT_ID_CORRECTION:") for value in relations)
    finally:
        ledger.close()
    session = sqlite3.connect(root / "sessions" / "session-authority.sqlite")
    try:
        assert session.execute(
            "SELECT state FROM session_record WHERE session_id='session-a'"
        ).fetchone()[0] == "ACTIVE"
        assert session.execute("SELECT COUNT(*) FROM state_travel_entry").fetchone()[0] == 1
    finally:
        session.close()


def test_execution_rejects_changed_plan_or_missing_confirmation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    plan = plan_live_root_normalization(root)
    with pytest.raises(ValueError, match="CONFIRMATION_REQUIRED"):
        execute_live_root_normalization(
            root,
            expected_plan_sha256=plan["plan_sha256"],
            confirmation="wrong",
        )
    _write(root / "internal_sources" / "late.txt", b"changed")
    with pytest.raises(RuntimeError, match="PLAN_CHANGED"):
        execute_live_root_normalization(
            root,
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
        )


def test_preserved_operational_file_appends_content_addressed_version(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    first_plan = plan_live_root_normalization(root)
    execute_live_root_normalization(
        root,
        expected_plan_sha256=first_plan["plan_sha256"],
        confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    )
    _write(root / "active_session.json", b'{"session_id":"session-b"}')
    second_plan = plan_live_root_normalization(root)
    second = execute_live_root_normalization(
        root,
        expected_plan_sha256=second_plan["plan_sha256"],
        confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    )

    assert second["status"] == "PASS"
    authority = sqlite3.connect(
        root / "project_authority" / "project-authority.sqlite"
    )
    try:
        rows = authority.execute(
            "SELECT logical_path,source_sha256 FROM authority_file_migration "
            "WHERE logical_path LIKE 'legacy-root/active_session.json%' "
            "ORDER BY logical_path"
        ).fetchall()
    finally:
        authority.close()
    assert len(rows) == 2
    assert rows[0][0] == "legacy-root/active_session.json"
    assert rows[1][0].startswith("legacy-root/active_session.json@")
    assert rows[0][1] != rows[1][1]


def test_changed_operational_receipt_uses_versioned_logical_path(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    first_plan = plan_live_root_normalization(root)
    execute_live_root_normalization(
        root,
        expected_plan_sha256=first_plan["plan_sha256"],
        confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    )
    source = (
        root
        / "receipts"
        / "project-authority"
        / "working-sector-migration.json"
    )
    _write(source, b'{"schema":"working-migration","revision":1}')
    first_receipt_plan = plan_live_root_normalization(root)
    execute_live_root_normalization(
        root,
        expected_plan_sha256=first_receipt_plan["plan_sha256"],
        confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    )
    _write(source, b'{"schema":"working-migration","revision":2}')

    second_receipt_plan = plan_live_root_normalization(root)
    row = next(
        item
        for item in second_receipt_plan["rows"]
        if item["source_relative_path"]
        == "receipts/project-authority/working-sector-migration.json"
    )

    assert row["preserve_source_after_readback"] is True
    assert row["logical_path"].startswith(
        "historical-live-root/project-authority/working-sector-migration.json@"
    )
    second = execute_live_root_normalization(
        root,
        expected_plan_sha256=second_receipt_plan["plan_sha256"],
        confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    )
    assert second["status"] == "PASS"
    assert source.is_file()
    assert read_receipt(
        root / "receipts" / "receipt-ledger.sqlite",
        logical_path=row["logical_path"],
    )["revision"] == 2


def test_project_authority_operational_receipt_backfill_is_allowlisted_and_idempotent(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    receipt_root = root / "receipts" / "project-authority"
    source_bytes: dict[str, bytes] = {}
    for filename, (schema, _) in PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS.items():
        data = _operational_receipt(
            schema=schema,
            project_id=(None if filename == "source-scaffold-retirement.json" else "project-a"),
        )
        source_bytes[filename] = data
        _write(receipt_root / filename, data)
    _write(
        receipt_root / "not-allowlisted.json",
        _operational_receipt(
            schema="evidence-lane.not-allowlisted.v1",
            project_id="project-a",
        ),
    )

    first = backfill_project_authority_operational_receipts(
        root,
        project_id="project-a",
    )

    assert first["present_filenames"] == list(
        PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS
    )
    assert first["new_receipt_record_count"] == 4
    assert first["source_files_mutated"] is False
    assert first["recursive_scan_used"] is False
    for filename, data in source_bytes.items():
        content_sha256 = sha256_bytes(data)
        role = filename.removesuffix(".json")
        logical_path = (
            f"project-authority/operational/{role}/{content_sha256.lower()}.json"
        )
        assert logical_path in first["logical_paths"]
        assert canonical_json_bytes(
            read_receipt(
                root / "receipts" / "receipt-ledger.sqlite",
                logical_path=logical_path,
            )
        ) == data
        assert (receipt_root / filename).read_bytes() == data

    database_before = (root / "receipts" / "receipt-ledger.sqlite").read_bytes()
    second = backfill_project_authority_operational_receipts(
        root,
        project_id="project-a",
    )
    assert second["new_receipt_record_count"] == 0
    assert second["receipt_record_count_after"] == first["receipt_record_count_after"]
    assert (root / "receipts" / "receipt-ledger.sqlite").read_bytes() == database_before


def test_project_authority_operational_receipt_versions_preserve_prior_bytes(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    filename = "working-sector-migration.json"
    schema, receipt_kind = PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS[filename]
    source = root / "receipts" / "project-authority" / filename
    first_bytes = _operational_receipt(
        schema=schema,
        project_id="project-a",
        revision=1,
    )
    second_bytes = _operational_receipt(
        schema=schema,
        project_id="project-a",
        revision=2,
    )
    _write(source, first_bytes)
    first = append_project_authority_operational_receipt(
        root,
        project_id="project-a",
        filename=filename,
    )
    _write(source, second_bytes)
    second = append_project_authority_operational_receipt(
        root,
        project_id="project-a",
        filename=filename,
    )

    assert first["state"] == "APPENDED"
    assert second["state"] == "APPENDED"
    assert second["prior_operational_receipt_sha256"] == sha256_bytes(first_bytes)
    database = sqlite3.connect(root / "receipts" / "receipt-ledger.sqlite")
    try:
        rows = database.execute(
            "SELECT receipt_sha256,prior_receipt_sha256,supersedes_receipt_sha256 "
            "FROM receipt_record WHERE receipt_kind=? ORDER BY sequence",
            (receipt_kind,),
        ).fetchall()
    finally:
        database.close()
    assert rows == [
        (sha256_bytes(first_bytes), None, None),
        (sha256_bytes(second_bytes), sha256_bytes(first_bytes), sha256_bytes(first_bytes)),
    ]
    assert source.read_bytes() == second_bytes
    assert read_receipt(
        root / "receipts" / "receipt-ledger.sqlite",
        logical_path=first["logical_path"],
    )["revision"] == 1


def test_project_authority_operational_receipt_rejects_unknown_or_unsealed_input(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    with pytest.raises(ValueError, match="NOT_ALLOWLISTED"):
        append_project_authority_operational_receipt(
            root,
            project_id="project-a",
            filename="unknown.json",
        )

    filename = "working-sector-recovery.json"
    schema, _ = PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS[filename]
    _write(
        root / "receipts" / "project-authority" / filename,
        canonical_json_bytes(
            {
                "schema": schema,
                "project_id": "project-a",
                "receipt_sha256": "0" * 64,
            }
        ),
    )
    with pytest.raises(ValueError, match="SEAL_MISMATCH"):
        append_project_authority_operational_receipt(
            root,
            project_id="project-a",
            filename=filename,
        )
