from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin import canon_task_graph
from evidence_lane_plugin.canon_task_graph import (
    CANON_DECISION_RECEIPT_SCHEMA,
    CANON_LEDGER_SCHEMA,
    CANON_LEDGER_SCHEMA_VERSION,
    inspect_canon_authority,
    inspect_canon_schema_contract,
    validate_canon_receipt,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from jsonschema import Draft202012Validator

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "evidence-lane-plugin"
SCHEMAS = PLUGIN / "schemas" / "canon"
MANIFEST = SCHEMAS / "canon-schema-manifest.v1.json"
LEDGER_SQL = SCHEMAS / "canon-ledger.v1.sql"
RECEIPT_SCHEMA = SCHEMAS / "canon-receipts.v1.schema.json"


def _error_code(caught: pytest.ExceptionInfo[EvidenceLaneError]) -> str:
    return caught.value.code


def _root(tmp_path: Path, project_id: str = "canon-schema-project") -> Path:
    root = tmp_path / project_id
    root.mkdir(parents=True)
    return root


def _decision_receipt() -> dict[str, object]:
    body: dict[str, object] = {
        "schema": CANON_DECISION_RECEIPT_SCHEMA,
        "project_id": "canon-schema-project",
        "canon_id": "canon_fixture",
        "canon_sha256": sha256_bytes(b"canon"),
        "revision": 1,
        "decision": "ACCEPT",
        "state_before": "PENDING_HIL",
        "state_after": "ACCEPTED_INPUT",
        "actor_task_uuid": "task-fixture",
        "actor_id": "user",
        "reason": None,
        "research_request": None,
        "next_required_revision": None,
        "source_notification_required": False,
        "project_truth_pointer_moved": False,
        "learning_pointer_moved": False,
        "project_hil_invoked": False,
        "learning_hil_invoked": False,
        "other_task_hil_decided": False,
        "source_write_authority_granted": False,
        "decided_at": "2026-08-15T00:00:00.000000Z",
        "event_sha256": sha256_bytes(b"event"),
    }
    return {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def test_first_class_assets_match_manifest_and_built_ledger(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    receipt_schema = json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(receipt_schema)
    contract = inspect_canon_schema_contract()

    assert contract["status"] == "PASS"
    assert contract["ledger_schema"] == CANON_LEDGER_SCHEMA
    assert contract["ledger_version"] == CANON_LEDGER_SCHEMA_VERSION
    assert manifest["ledger"]["asset_sha256"] == sha256_bytes(
        LEDGER_SQL.read_bytes()
    )
    assert manifest["receipts"]["asset_sha256"] == sha256_bytes(
        RECEIPT_SCHEMA.read_bytes()
    )
    assert contract["ledger_asset_sha256"] == manifest["ledger"]["asset_sha256"]
    assert contract["receipt_asset_sha256"] == manifest["receipts"][
        "asset_sha256"
    ]

    root = _root(tmp_path)
    inspect_canon_authority(root, project_id=root.name)
    connection = sqlite3.connect(root / "canon" / "canon-input.sqlite")
    connection.row_factory = sqlite3.Row
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        migration = connection.execute(
            """
            SELECT from_version,to_version,asset_sha256,migration_mode
            FROM canon_schema_migration
            WHERE schema_name=? AND to_version=?
            """,
            (CANON_LEDGER_SCHEMA, CANON_LEDGER_SCHEMA_VERSION),
        ).fetchone()
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT type,name,tbl_name,sql
                FROM sqlite_master
                WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'
                ORDER BY type,name
                """
            ).fetchall()
        ]
    finally:
        connection.close()

    assert version == CANON_LEDGER_SCHEMA_VERSION
    assert migration is not None
    assert dict(migration) == {
        "from_version": 0,
        "to_version": CANON_LEDGER_SCHEMA_VERSION,
        "asset_sha256": contract["ledger_asset_sha256"],
        "migration_mode": "ADDITIVE_IDEMPOTENT_CREATE_ONLY",
    }
    assert sha256_bytes(canonical_json_bytes(rows)) == contract[
        "ledger_schema_signature_sha256"
    ]
    source = Path(canon_task_graph.__file__).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS canon_contract(" not in source
    assert 'executescript("BEGIN IMMEDIATE;\\n" + ledger_sql)' in source


def test_v0_to_v1_migration_is_additive_and_preserves_existing_rows(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    ledger = root / "canon" / "canon-input.sqlite"
    ledger.parent.mkdir(parents=True)
    sql = LEDGER_SQL.read_text(encoding="utf-8")
    legacy_sql = sql.split(";", 1)[1]
    connection = sqlite3.connect(ledger)
    try:
        connection.executescript(legacy_sql)
        connection.execute(
            """
            INSERT INTO canon_contract(
                contract_sha256,contract_id,contract_version,
                destination_project_id,destination_task_uuid,active,contract_json
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                sha256_bytes(b"legacy-contract"),
                "legacy-contract",
                1,
                root.name,
                "task-legacy",
                1,
                "{}",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    inspect_canon_authority(root, project_id=root.name)
    connection = sqlite3.connect(ledger)
    try:
        preserved = connection.execute(
            "SELECT contract_id FROM canon_contract"
        ).fetchone()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()

    assert preserved == ("legacy-contract",)
    assert version == CANON_LEDGER_SCHEMA_VERSION


def test_unknown_newer_ledger_version_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    ledger = root / "canon" / "canon-input.sqlite"
    ledger.parent.mkdir(parents=True)
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("PRAGMA user_version=2")
    finally:
        connection.close()

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_canon_authority(root, project_id=root.name)
    assert _error_code(caught) == "CANON_LEDGER_VERSION_UNSUPPORTED"


def test_schema_asset_byte_drift_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "schemas" / "canon"
    shutil.copytree(SCHEMAS, copied)
    ledger = copied / "canon-ledger.v1.sql"
    ledger.write_bytes(ledger.read_bytes() + b"\n")
    monkeypatch.setattr(canon_task_graph, "_CANON_SCHEMA_ROOT", copied)

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_canon_schema_contract()
    assert _error_code(caught) == "CANON_SCHEMA_ASSET_HASH_MISMATCH"


def test_migration_receipt_tamper_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    inspect_canon_authority(root, project_id=root.name)
    ledger = root / "canon" / "canon-input.sqlite"
    connection = sqlite3.connect(ledger)
    try:
        connection.execute(
            "UPDATE canon_schema_migration SET asset_sha256=?",
            ("A" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_canon_authority(root, project_id=root.name)
    assert _error_code(caught) == "CANON_LEDGER_MIGRATION_RECEIPT_MISMATCH"


def test_same_version_schema_drift_fails_without_repair(tmp_path: Path) -> None:
    root = _root(tmp_path)
    inspect_canon_authority(root, project_id=root.name)
    ledger = root / "canon" / "canon-input.sqlite"
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("DROP TABLE canon_edge")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_canon_authority(root, project_id=root.name)
    assert _error_code(caught) == "CANON_LEDGER_BUILDER_SCHEMA_MISMATCH"

    connection = sqlite3.connect(ledger)
    try:
        repaired = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canon_edge'"
        ).fetchone()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
    assert repaired is None
    assert version == CANON_LEDGER_SCHEMA_VERSION


def test_authority_support_index_schema_is_not_canon_owned(tmp_path: Path) -> None:
    root = _root(tmp_path)
    inspect_canon_authority(root, project_id=root.name)
    ledger = root / "canon" / "canon-input.sqlite"
    connection = sqlite3.connect(ledger)
    try:
        connection.execute(
            "CREATE TABLE authority_index_probe(identity TEXT PRIMARY KEY) STRICT"
        )
        connection.execute(
            "CREATE INDEX authority_index_probe_identity_idx "
            "ON authority_index_probe(identity)"
        )
        connection.execute(
            "INSERT INTO authority_index_probe(identity) VALUES('preserved')"
        )
        connection.commit()
    finally:
        connection.close()

    inspected = inspect_canon_authority(root, project_id=root.name)
    assert inspected["status"] == "PASS"
    connection = sqlite3.connect(ledger)
    try:
        preserved = connection.execute(
            "SELECT identity FROM authority_index_probe"
        ).fetchone()
    finally:
        connection.close()
    assert preserved == ("preserved",)


def test_failed_v0_migration_rolls_back_every_schema_change(tmp_path: Path) -> None:
    root = _root(tmp_path)
    ledger = root / "canon" / "canon-input.sqlite"
    ledger.parent.mkdir(parents=True)
    connection = sqlite3.connect(ledger)
    try:
        connection.execute(
            "CREATE TABLE canon_contract(contract_sha256 TEXT PRIMARY KEY, marker TEXT)"
        )
        connection.execute(
            "INSERT INTO canon_contract(contract_sha256,marker) VALUES(?,?)",
            ("legacy", "preserve"),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_canon_authority(root, project_id=root.name)
    assert _error_code(caught) == "CANON_LEDGER_BUILDER_SCHEMA_MISMATCH"

    connection = sqlite3.connect(ledger)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        preserved = connection.execute(
            "SELECT contract_sha256,marker FROM canon_contract"
        ).fetchone()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
    assert tables == {"canon_contract"}
    assert preserved == ("legacy", "preserve")
    assert version == 0


def test_receipt_registry_enforces_shape_identity_and_self_seal() -> None:
    receipt = _decision_receipt()
    proof = validate_canon_receipt(receipt)
    assert proof["status"] == "PASS"
    assert proof["receipt_schema"] == CANON_DECISION_RECEIPT_SCHEMA
    assert proof["receipt_sha256"] == receipt["receipt_sha256"]

    missing = dict(receipt)
    missing.pop("actor_id")
    missing_body = dict(missing)
    missing_body.pop("receipt_sha256")
    missing["receipt_sha256"] = sha256_bytes(canonical_json_bytes(missing_body))
    with pytest.raises(EvidenceLaneError) as missing_error:
        validate_canon_receipt(missing)
    assert _error_code(missing_error) == "CANON_RECEIPT_REQUIRED_FIELD_MISSING"

    unsealed = dict(receipt)
    unsealed["reason"] = "changed after sealing"
    with pytest.raises(EvidenceLaneError) as seal_error:
        validate_canon_receipt(unsealed)
    assert _error_code(seal_error) == "CANON_RECEIPT_SELF_SEAL_MISMATCH"

    unknown = dict(receipt)
    unknown["schema"] = "evidence-lane.canon-decision-receipt.v2"
    unknown_body = dict(unknown)
    unknown_body.pop("receipt_sha256")
    unknown["receipt_sha256"] = sha256_bytes(canonical_json_bytes(unknown_body))
    with pytest.raises(EvidenceLaneError) as schema_error:
        validate_canon_receipt(unknown)
    assert _error_code(schema_error) == "CANON_RECEIPT_SCHEMA_UNSUPPORTED"
