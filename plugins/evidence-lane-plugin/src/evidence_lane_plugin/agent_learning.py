"""Project-isolated Agent Learning candidates and pointer-only Learning HIL.

Agent Learning is a separate authority from Project Truth, Canon Input, and
ChatLineage.  This module persists only evidence-backed learning candidates
and append-only lifecycle events.  It never reads as Project Truth, moves a
Project PV pointer, or invokes the Project six-way HIL.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .redaction import contains_secret

LEARNING_CANDIDATE_SCHEMA = "evidence-lane.learning-candidate.v1"
LEARNING_EVENT_SCHEMA = "evidence-lane.learning-event.v1"
LEARNING_POINTER_SCHEMA = "evidence-lane.learning-pointer.v1"
LEARNING_DECISION_RECEIPT_SCHEMA = "evidence-lane.learning-decision-receipt.v1"
LEARNING_RETRIEVAL_RECEIPT_SCHEMA = "evidence-lane.learning-retrieval-receipt.v1"
HOST_MEMORY_BOUNDARY_SCHEMA = "evidence-lane.host-memory-boundary.v1"
HOST_MEMORY_IMPORT_RECEIPT_SCHEMA = (
    "evidence-lane.host-memory-import-receipt.v1"
)
HOST_MEMORY_AUTHORITY = "NONAUTHORITATIVE_HELPFUL_RECALL_ONLY"
HOST_MEMORY_OFFICIAL_DOCS = (
    "https://learn.chatgpt.com/docs/customization/memories"
)
LEARNING_RUNTIME_CONTRACT_SCHEMA = "evidence-lane.learning-runtime-contract.v1"
LEARNING_LEDGER_SCHEMA = "evidence-lane.agent-learning-ledger.v1"
LEARNING_LEDGER_SCHEMA_VERSION = 1
LEARNING_EXPIRY_RECEIPT_SCHEMA = "evidence-lane.learning-expiry-receipt.v1"
LEARNING_EXPIRY_OWNER = "AGENT_LEARNING_AUTHORITY_MAINTENANCE"

_LEARNING_PUBLIC_ACTIONS = (
    "learning_inspect",
    "learning_retrieve",
    "learning_seal_candidate",
    "learning_decide_candidate",
    "learning_revoke",
)
_LEARNING_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS learning_schema_metadata(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version > 0),
    ddl_sha256 TEXT NOT NULL,
    schema_signature_sha256 TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS learning_candidate(
    candidate_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    candidate_sha256 TEXT NOT NULL UNIQUE,
    dedup_key_sha256 TEXT NOT NULL,
    tier TEXT NOT NULL,
    lesson_type TEXT NOT NULL,
    expires_at TEXT,
    candidate_json TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_learning_candidate_dedup
ON learning_candidate(project_id,dedup_key_sha256);
CREATE TABLE IF NOT EXISTS learning_event(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL
        REFERENCES learning_candidate(candidate_id),
    event_type TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    decision_key_sha256 TEXT,
    event_sha256 TEXT NOT NULL UNIQUE,
    event_json TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_decision_once
ON learning_event(decision_key_sha256)
WHERE decision_key_sha256 IS NOT NULL;
CREATE TABLE IF NOT EXISTS learning_pointer_history(
    project_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    candidate_id TEXT NOT NULL
        REFERENCES learning_candidate(candidate_id),
    candidate_sha256 TEXT NOT NULL,
    prior_generation INTEGER NOT NULL CHECK(prior_generation >= 0),
    reason TEXT NOT NULL,
    pointer_sha256 TEXT NOT NULL UNIQUE,
    pointer_json TEXT NOT NULL,
    PRIMARY KEY(project_id,generation)
) STRICT;
CREATE TABLE IF NOT EXISTS learning_decision_receipt(
    decision_key_sha256 TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL
        REFERENCES learning_candidate(candidate_id),
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL
) STRICT;
CREATE VIRTUAL TABLE IF NOT EXISTS learning_candidate_fts USING fts5(
    candidate_id UNINDEXED,
    project_id UNINDEXED,
    statement,
    lesson_type,
    scope_text,
    tokenize='unicode61'
);
"""
_LEARNING_EXPECTED_SCHEMA = {
    "tables": {
        "learning_schema_metadata": [
            ["singleton", "INTEGER"],
            ["schema_id", "TEXT"],
            ["schema_version", "INTEGER"],
            ["ddl_sha256", "TEXT"],
            ["schema_signature_sha256", "TEXT"],
        ],
        "learning_candidate": [
            ["candidate_id", "TEXT"],
            ["project_id", "TEXT"],
            ["candidate_sha256", "TEXT"],
            ["dedup_key_sha256", "TEXT"],
            ["tier", "TEXT"],
            ["lesson_type", "TEXT"],
            ["expires_at", "TEXT"],
            ["candidate_json", "TEXT"],
        ],
        "learning_event": [
            ["sequence", "INTEGER"],
            ["event_id", "TEXT"],
            ["candidate_id", "TEXT"],
            ["event_type", "TEXT"],
            ["lifecycle_state", "TEXT"],
            ["occurred_at", "TEXT"],
            ["decision_key_sha256", "TEXT"],
            ["event_sha256", "TEXT"],
            ["event_json", "TEXT"],
        ],
        "learning_pointer_history": [
            ["project_id", "TEXT"],
            ["generation", "INTEGER"],
            ["candidate_id", "TEXT"],
            ["candidate_sha256", "TEXT"],
            ["prior_generation", "INTEGER"],
            ["reason", "TEXT"],
            ["pointer_sha256", "TEXT"],
            ["pointer_json", "TEXT"],
        ],
        "learning_decision_receipt": [
            ["decision_key_sha256", "TEXT"],
            ["candidate_id", "TEXT"],
            ["receipt_sha256", "TEXT"],
            ["receipt_json", "TEXT"],
        ],
        "learning_candidate_fts": [
            ["candidate_id", ""],
            ["project_id", ""],
            ["statement", ""],
            ["lesson_type", ""],
            ["scope_text", ""],
        ],
    },
    "indexes": {
        "idx_learning_candidate_dedup": {
            "table": "learning_candidate",
            "columns": ["project_id", "dedup_key_sha256"],
            "unique": False,
            "partial": False,
        },
        "idx_learning_decision_once": {
            "table": "learning_event",
            "columns": ["decision_key_sha256"],
            "unique": True,
            "partial": True,
        },
    },
    "fts": {
        "table": "learning_candidate_fts",
        "engine": "fts5",
        "ranking": "bm25",
    },
}

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_PV_RE = re.compile(r"^PV[1-9][0-9]*$")
_ROLLBACK_RE = re.compile(r"^ROLLBACK: LGEN([1-9][0-9]*)$")
_PREFIXED_DECISIONS = {
    "APPROVE_WITH_DELTA": "CORRECTION_REQUESTED",
    "MORE_RESEARCH": "RESEARCH_REQUESTED",
    "REJECT": "REJECTED",
    "FAIL": "FAILED",
}
_LESSON_TYPES = {
    "PROCEDURAL",
    "FAILURE_AVOIDANCE",
    "RELATIONAL",
    "TOOL_ROUTING",
    "HOST_COMPATIBILITY",
}
_OUTCOMES = {"SUCCEEDED", "FAILED", "MIXED", "REJECTED", "SUPERSEDED"}
_SCOPE_KINDS = {"TASK", "PROJECT", "CROSS_PROJECT_PROPOSED"}
_PRIVACY_CLASSES = {
    "PROJECT_PRIVATE",
    "SHAREABLE_WITHIN_ORG",
    "PUBLIC_CANDIDATE",
}
_TIERS = {"DELTA_OBSERVATION", "CROSS_DELTA", "CROSS_PV"}
_TERMINAL_STATES = {"REJECTED", "FAILED", "SUPERSEDED", "EXPIRED", "REVOKED"}
_EVIDENCE_KEYS = {"project_id", "task_id", "delta_id", "pv_ref", "ref", "sha256"}
_CANDIDATE_KEYS = {
    "schema",
    "candidate_id",
    "project_id",
    "lesson_type",
    "statement",
    "scope",
    "evidence",
    "outcome",
    "confidence",
    "counterevidence",
    "contradictions",
    "temporal",
    "privacy",
    "supersedes",
    "candidate_sha256",
    "source_lineage_head_sha256",
}
_HOST_MEMORY_SOURCE_SCHEMES = {
    "CODEX_LOCAL_MEMORY": "codex-local-memory://",
    "CHATGPT_SAVED_MEMORY": "chatgpt-memory://",
    "CHATGPT_CHAT_HISTORY_MEMORY": "chatgpt-memory://",
}
_DIRECT_HOST_MEMORY_PREFIXES = (
    "codex-local-memory://",
    "chatgpt-memory://",
    "host-memory://",
)
_HOST_MEMORY_IMPORT_REF_RE = re.compile(
    r"^host-memory-import://(?P<sha256>[A-F0-9]{64})$"
)
_HOST_MEMORY_IMPORT_KEYS = {
    "schema",
    "status",
    "import_id",
    "project_id",
    "source",
    "import_context",
    "authority_boundary",
    "raw_host_memory_stored",
    "private_reasoning_stored",
    "receipt_sha256",
}


def _sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        bool(_SHA256_RE.fullmatch(exact)),
        "LEARNING_SHA256_INVALID",
        "An Agent Learning field is not one exact SHA-256.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _timestamp(value: Any, *, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    exact = str(value or "").strip()
    require(
        bool(exact),
        "LEARNING_TIMESTAMP_REQUIRED",
        "An Agent Learning timestamp is required.",
        status="BLOCKED",
        field=field,
    )
    try:
        parsed = datetime.fromisoformat(exact)
    except ValueError as exc:
        require(
            False,
            "LEARNING_TIMESTAMP_INVALID",
            "An Agent Learning timestamp is not valid ISO-8601.",
            status="MISMATCH",
            field=field,
        )
        raise AssertionError("unreachable") from exc
    require(
        parsed.tzinfo is not None,
        "LEARNING_TIMESTAMP_TIMEZONE_REQUIRED",
        "An Agent Learning timestamp requires an explicit timezone.",
        status="MISMATCH",
        field=field,
    )
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _project_root(project_root: str | Path, *, project_id: str) -> Path:
    root = Path(project_root).resolve()
    require(
        root.name == project_id,
        "LEARNING_CROSS_PROJECT_ROUTE_DENIED",
        "The Agent Learning root does not match the exact project identity.",
        status="BLOCKED",
        project_id=project_id,
    )
    return root


def _learning_root(root: Path) -> Path:
    return root / "learning"


def _ledger_path(root: Path) -> Path:
    return _learning_root(root) / "agent-learning.sqlite"


def _pointer_path(root: Path) -> Path:
    return _learning_root(root) / "active_pointer.json"


def _candidate_path(root: Path, candidate_id: str) -> Path:
    return _learning_root(root) / "candidates" / f"{candidate_id}.json"


def _receipt_path(root: Path, receipt_sha256: str) -> Path:
    return _learning_root(root) / "receipts" / f"{receipt_sha256}.json"


def _host_memory_import_path(root: Path, receipt_sha256: str) -> Path:
    return (
        _learning_root(root)
        / "host-memory-imports"
        / f"{receipt_sha256}.json"
    )


def learning_runtime_contract() -> dict[str, Any]:
    """Return the exact schema, public-route, search, and expiry ownership law."""

    ddl_sha256 = sha256_bytes(_LEARNING_SCHEMA_DDL.encode("utf-8"))
    signature_sha256 = sha256_bytes(
        canonical_json_bytes(_LEARNING_EXPECTED_SCHEMA)
    )
    body = {
        "schema": LEARNING_RUNTIME_CONTRACT_SCHEMA,
        "ledger_schema": LEARNING_LEDGER_SCHEMA,
        "ledger_schema_version": LEARNING_LEDGER_SCHEMA_VERSION,
        "ledger_ddl_sha256": ddl_sha256,
        "ledger_schema_signature_sha256": signature_sha256,
        "search": {
            "engine": "SQLITE_FTS5",
            "ranking": "BM25",
            "bounded_result_limit": [1, 20],
            "full_ledger_loaded_into_model_context": False,
        },
        "public_actions": list(_LEARNING_PUBLIC_ACTIONS),
        "public_action_count": len(_LEARNING_PUBLIC_ACTIONS),
        "expiry": {
            "logical_exclusion_owner": "LEARNING_RETRIEVAL_AS_OF_FILTER",
            "event_materialization_owner": LEARNING_EXPIRY_OWNER,
            "event_materialization_route": "INTERNAL_MAINTENANCE_ONLY",
            "public_action": None,
            "hook_owned": False,
            "scheduler_assumed": False,
            "project_truth_effect": "NONE",
            "learning_pointer_effect": "NONE",
        },
        "installed_only_at_governed_release_boundary": True,
    }
    return {
        **body,
        "contract_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _schema_columns(
    connection: sqlite3.Connection, table: str
) -> list[list[str]]:
    return [
        [str(row["name"]), str(row["type"])]
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    ]


def _schema_index(
    connection: sqlite3.Connection, *, table: str, index: str
) -> dict[str, Any] | None:
    row = next(
        (
            item
            for item in connection.execute(f'PRAGMA index_list("{table}")')
            if str(item["name"]) == index
        ),
        None,
    )
    if row is None:
        return None
    return {
        "table": table,
        "columns": [
            str(item["name"])
            for item in connection.execute(f'PRAGMA index_info("{index}")')
        ],
        "unique": bool(row["unique"]),
        "partial": bool(row["partial"]),
    }


def _learning_schema_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    expected_tables = cast(
        dict[str, list[list[str]]], _LEARNING_EXPECTED_SCHEMA["tables"]
    )
    expected_indexes = cast(
        dict[str, dict[str, Any]], _LEARNING_EXPECTED_SCHEMA["indexes"]
    )
    fts_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        ("learning_candidate_fts",),
    ).fetchone()
    fts_sql = str(fts_row["sql"] or "") if fts_row is not None else ""
    return {
        "tables": {
            table: _schema_columns(connection, table)
            for table in expected_tables
        },
        "indexes": {
            index: _schema_index(connection, table=value["table"], index=index)
            for index, value in expected_indexes.items()
        },
        "fts": {
            "table": "learning_candidate_fts",
            "engine": "fts5" if "USING fts5" in fts_sql else None,
            "ranking": "bm25" if "USING fts5" in fts_sql else None,
        },
    }


def _validate_learning_schema(connection: sqlite3.Connection) -> None:
    snapshot = _learning_schema_snapshot(connection)
    require(
        snapshot == _LEARNING_EXPECTED_SCHEMA,
        "LEARNING_LEDGER_SCHEMA_MISMATCH",
        "The Agent Learning ledger does not match its exact schema contract.",
        status="MISMATCH",
        expected_schema_signature_sha256=sha256_bytes(
            canonical_json_bytes(_LEARNING_EXPECTED_SCHEMA)
        ),
        actual_schema_signature_sha256=sha256_bytes(
            canonical_json_bytes(snapshot)
        ),
    )


def _fts_document(candidate: dict[str, Any]) -> tuple[str, str, str, str, str]:
    scope = cast(dict[str, Any], candidate["scope"])
    scope_text = " ".join(
        [str(scope["kind"]), *[str(item) for item in scope["selectors"]]]
    )
    return (
        str(candidate["candidate_id"]),
        str(candidate["project_id"]),
        str(candidate["statement"]),
        str(candidate["lesson_type"]),
        scope_text,
    )


def _rebuild_learning_fts(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM learning_candidate_fts")
    rows = connection.execute(
        "SELECT candidate_json FROM learning_candidate ORDER BY candidate_id"
    ).fetchall()
    for row in rows:
        candidate = cast(dict[str, Any], json.loads(str(row["candidate_json"])))
        _verify_candidate(candidate)
        connection.execute(
            """
            INSERT INTO learning_candidate_fts(
                candidate_id,project_id,statement,lesson_type,scope_text
            ) VALUES(?,?,?,?,?)
            """,
            _fts_document(candidate),
        )


def _apply_learning_schema(connection: sqlite3.Connection) -> None:
    metadata_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        ("learning_schema_metadata",),
    ).fetchone()
    contract = learning_runtime_contract()
    if metadata_exists is None:
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + _LEARNING_SCHEMA_DDL)
            _validate_learning_schema(connection)
            _rebuild_learning_fts(connection)
            connection.execute(
                """
                INSERT INTO learning_schema_metadata(
                    singleton,schema_id,schema_version,ddl_sha256,
                    schema_signature_sha256
                ) VALUES(1,?,?,?,?)
                """,
                (
                    LEARNING_LEDGER_SCHEMA,
                    LEARNING_LEDGER_SCHEMA_VERSION,
                    contract["ledger_ddl_sha256"],
                    contract["ledger_schema_signature_sha256"],
                ),
            )
            connection.commit()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            require(
                False,
                "LEARNING_LEDGER_SCHEMA_MISMATCH",
                "The Agent Learning v0-to-v1 schema migration failed closed.",
                status="MISMATCH",
                error_type=type(exc).__name__,
            )
        except Exception:
            connection.rollback()
            raise
        return

    rows = connection.execute(
        "SELECT * FROM learning_schema_metadata ORDER BY singleton"
    ).fetchall()
    require(
        len(rows) == 1
        and int(rows[0]["singleton"]) == 1
        and str(rows[0]["schema_id"]) == LEARNING_LEDGER_SCHEMA
        and int(rows[0]["schema_version"]) == LEARNING_LEDGER_SCHEMA_VERSION
        and str(rows[0]["ddl_sha256"]) == contract["ledger_ddl_sha256"]
        and str(rows[0]["schema_signature_sha256"])
        == contract["ledger_schema_signature_sha256"],
        "LEARNING_LEDGER_SCHEMA_VERSION_MISMATCH",
        "The Agent Learning ledger metadata is missing, newer, or byte-drifted.",
        status="MISMATCH",
    )
    _validate_learning_schema(connection)


def _connect(root: Path) -> sqlite3.Connection:
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    try:
        _apply_learning_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


def _immutable_json(path: Path, value: dict[str, Any]) -> None:
    encoded = canonical_json_bytes(value)
    if path.exists():
        require(
            path.read_bytes() == encoded,
            "LEARNING_IMMUTABLE_ARTIFACT_CONFLICT",
            "An immutable Agent Learning artifact already exists with other bytes.",
            status="MISMATCH",
            path=str(path),
        )
        return
    atomic_write_json(path, value)


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    require(path.is_file(), code, "A required Agent Learning artifact is missing.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        require(
            False,
            code,
            "A required Agent Learning artifact is invalid JSON.",
            status="MISMATCH",
            path=str(path),
            error_type=type(exc).__name__,
        )
        raise AssertionError("unreachable") from exc
    require(isinstance(value, dict), code, "Agent Learning JSON must be one object.")
    return cast(dict[str, Any], value)


def project_truth_pointer_sha256(
    project_root: str | Path, *, project_id: str
) -> str:
    """Hash the exact Project Truth pointer bytes without interpreting or moving it."""

    root = _project_root(project_root, project_id=project_id)
    path = root / "active_pointer.json"
    require(
        path.is_file(),
        "LEARNING_PROJECT_POINTER_REQUIRED",
        "Agent Learning requires the existing Project Truth pointer as a read-only guard.",
        status="BLOCKED",
    )
    return sha256_bytes(path.read_bytes())


def host_memory_boundary_contract() -> dict[str, Any]:
    """Return the fixed authority boundary for host-managed memory recall."""

    return {
        "schema": HOST_MEMORY_BOUNDARY_SCHEMA,
        "status": "PASS",
        "official_documentation": HOST_MEMORY_OFFICIAL_DOCS,
        "host_memory_authority": HOST_MEMORY_AUTHORITY,
        "host_memory_role": "OPTIONAL_GENERATED_RECALL_CONTEXT",
        "required_rule_authority": "AGENTS_MD_OR_CHECKED_IN_DOCUMENTATION",
        "automatic_import_allowed": False,
        "direct_evidence_reference_allowed": False,
        "explicit_provenance_receipt_required": True,
        "raw_host_memory_persistence_allowed": False,
        "learning_candidate_creation": "SEPARATE_EXPLICIT_ACTION_REQUIRED",
        "learning_hil_invocation": "SEPARATE_EXPLICIT_ACTION_REQUIRED",
        "project_hil_invocation": "FORBIDDEN",
        "project_truth_effect": "NONE",
        "learning_pointer_effect": "NONE",
        "project_truth_pointer_effect": "NONE",
    }


def _verify_host_memory_import_receipt(
    root: Path,
    *,
    project_id: str,
    receipt_sha256: str,
) -> dict[str, Any]:
    exact_sha256 = _sha256(receipt_sha256, field="host_memory_import_sha256")
    path = _host_memory_import_path(root, exact_sha256)
    receipt = _load_json(path, code="LEARNING_HOST_MEMORY_IMPORT_RECEIPT_REQUIRED")
    require(
        receipt.get("schema") == HOST_MEMORY_IMPORT_RECEIPT_SCHEMA
        and set(receipt) == _HOST_MEMORY_IMPORT_KEYS,
        "LEARNING_HOST_MEMORY_IMPORT_RECEIPT_SCHEMA_INVALID",
        "The host-memory import receipt does not match its exact schema.",
        status="MISMATCH",
    )
    claimed = _sha256(
        receipt.get("receipt_sha256"), field="host_memory_import_receipt_sha256"
    )
    body = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    require(
        claimed == exact_sha256
        and claimed == sha256_bytes(canonical_json_bytes(body))
        and path.name == f"{claimed}.json",
        "LEARNING_HOST_MEMORY_IMPORT_RECEIPT_HASH_MISMATCH",
        "The host-memory import receipt bytes do not match their immutable identity.",
        status="MISMATCH",
    )
    source = receipt.get("source")
    context = receipt.get("import_context")
    boundary = receipt.get("authority_boundary")
    require(
        isinstance(source, dict)
        and set(source)
        == {
            "kind",
            "locator",
            "locator_sha256",
            "record_sha256",
            "source_context_id_sha256",
            "observed_at",
        }
        and isinstance(context, dict)
        and set(context)
        == {
            "task_id",
            "delta_id",
            "pv_ref",
            "imported_at",
            "imported_by",
            "purpose",
            "project_truth_pointer_sha256",
        }
        and isinstance(boundary, dict)
        and boundary == {
            "host_memory_authority": HOST_MEMORY_AUTHORITY,
            "imported_role": "NONAUTHORITATIVE_EVIDENCE_REFERENCE_ONLY",
            "automatic_import": False,
            "accepted_learning": False,
            "learning_candidate_created": False,
            "learning_hil_invoked": False,
            "learning_pointer_moved": False,
            "project_hil_invoked": False,
            "project_truth_pointer_moved": False,
        },
        "LEARNING_HOST_MEMORY_IMPORT_RECEIPT_BOUNDARY_INVALID",
        "The host-memory receipt does not preserve its nonauthority boundary.",
        status="MISMATCH",
    )
    source = cast(dict[str, Any], source)
    context = cast(dict[str, Any], context)
    kind = str(source["kind"])
    locator = str(source["locator"])
    require(
        receipt.get("status") == "PASS"
        and receipt.get("project_id") == project_id
        and kind in _HOST_MEMORY_SOURCE_SCHEMES
        and locator.startswith(_HOST_MEMORY_SOURCE_SCHEMES[kind])
        and source["locator_sha256"]
        == sha256_bytes(locator.encode("utf-8"))
        and bool(_PV_RE.fullmatch(str(context["pv_ref"])))
        and receipt.get("raw_host_memory_stored") is False
        and receipt.get("private_reasoning_stored") is False
        and not contains_secret(receipt),
        "LEARNING_HOST_MEMORY_IMPORT_RECEIPT_INVALID",
        "The host-memory import receipt failed identity or privacy validation.",
        status="MISMATCH",
    )
    _sha256(source["record_sha256"], field="host_memory_source_record_sha256")
    _sha256(
        source["source_context_id_sha256"],
        field="host_memory_source_context_id_sha256",
    )
    _sha256(
        context["project_truth_pointer_sha256"],
        field="host_memory_project_truth_pointer_sha256",
    )
    observed_at = cast(
        str, _timestamp(source["observed_at"], field="host_memory_observed_at")
    )
    imported_at = cast(
        str, _timestamp(context["imported_at"], field="host_memory_imported_at")
    )
    require(
        observed_at <= imported_at,
        "LEARNING_HOST_MEMORY_IMPORT_TIME_INVALID",
        "Host-memory import time precedes the observed source record.",
        status="MISMATCH",
    )
    return receipt


def record_host_memory_import(
    project_root: str | Path,
    *,
    project_id: str,
    source_kind: str,
    source_locator: str,
    source_record_sha256: str,
    source_context_id: str,
    observed_at: str,
    imported_at: str,
    imported_by: str,
    purpose: str,
    task_id: str,
    delta_id: str,
    pv_ref: str,
) -> dict[str, Any]:
    """Seal provenance for one host-memory reference without promoting it."""

    root = _project_root(project_root, project_id=project_id)
    kind = str(source_kind).strip().upper()
    locator = str(source_locator).strip()
    context_id = str(source_context_id).strip()
    actor = str(imported_by).strip()
    exact_purpose = str(purpose).strip()
    exact_task_id = str(task_id).strip()
    exact_delta_id = str(delta_id).strip()
    exact_pv_ref = str(pv_ref).strip().upper()
    require(
        kind in _HOST_MEMORY_SOURCE_SCHEMES
        and locator.startswith(_HOST_MEMORY_SOURCE_SCHEMES.get(kind, "invalid://"))
        and bool(context_id)
        and bool(actor)
        and bool(exact_purpose)
        and len(exact_purpose) <= 500
        and bool(exact_task_id)
        and bool(exact_delta_id)
        and bool(_PV_RE.fullmatch(exact_pv_ref)),
        "LEARNING_HOST_MEMORY_IMPORT_PROVENANCE_INVALID",
        "A host-memory import requires exact source and governed task provenance.",
        status="BLOCKED",
    )
    source_hash = _sha256(
        source_record_sha256, field="host_memory_source_record_sha256"
    )
    exact_observed_at = cast(
        str, _timestamp(observed_at, field="host_memory_observed_at")
    )
    exact_imported_at = cast(
        str, _timestamp(imported_at, field="host_memory_imported_at")
    )
    require(
        exact_observed_at <= exact_imported_at,
        "LEARNING_HOST_MEMORY_IMPORT_TIME_INVALID",
        "Host-memory import time precedes the observed source record.",
        status="BLOCKED",
    )
    visible_input = {
        "source_kind": kind,
        "source_locator": locator,
        "imported_by": actor,
        "purpose": exact_purpose,
        "task_id": exact_task_id,
        "delta_id": exact_delta_id,
    }
    require(
        not contains_secret(visible_input),
        "LEARNING_HOST_MEMORY_IMPORT_SECRET_BLOCKED",
        "Secrets and credentials cannot enter a host-memory provenance receipt.",
        status="BLOCKED",
    )
    project_pointer_sha256 = project_truth_pointer_sha256(
        root, project_id=project_id
    )
    source = {
        "kind": kind,
        "locator": locator,
        "locator_sha256": sha256_bytes(locator.encode("utf-8")),
        "record_sha256": source_hash,
        "source_context_id_sha256": sha256_bytes(context_id.encode("utf-8")),
        "observed_at": exact_observed_at,
    }
    import_context = {
        "task_id": exact_task_id,
        "delta_id": exact_delta_id,
        "pv_ref": exact_pv_ref,
        "imported_at": exact_imported_at,
        "imported_by": actor,
        "purpose": exact_purpose,
        "project_truth_pointer_sha256": project_pointer_sha256,
    }
    import_identity = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "source": source,
                "import_context": import_context,
            }
        )
    )
    body: dict[str, Any] = {
        "schema": HOST_MEMORY_IMPORT_RECEIPT_SCHEMA,
        "status": "PASS",
        "import_id": f"hostmem_{import_identity[:24].lower()}",
        "project_id": project_id,
        "source": source,
        "import_context": import_context,
        "authority_boundary": {
            "host_memory_authority": HOST_MEMORY_AUTHORITY,
            "imported_role": "NONAUTHORITATIVE_EVIDENCE_REFERENCE_ONLY",
            "automatic_import": False,
            "accepted_learning": False,
            "learning_candidate_created": False,
            "learning_hil_invoked": False,
            "learning_pointer_moved": False,
            "project_hil_invoked": False,
            "project_truth_pointer_moved": False,
        },
        "raw_host_memory_stored": False,
        "private_reasoning_stored": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(body))
    receipt = {**body, "receipt_sha256": receipt_sha256}
    path = _host_memory_import_path(root, receipt_sha256)
    _immutable_json(path, receipt)
    _verify_host_memory_import_receipt(
        root,
        project_id=project_id,
        receipt_sha256=receipt_sha256,
    )
    return {
        "status": "PASS",
        "state": "IMPORTED_AS_NONAUTHORITATIVE_EVIDENCE_REFERENCE",
        "receipt": receipt,
        "receipt_path": str(path),
        "learning_evidence_reference": {
            "project_id": project_id,
            "task_id": exact_task_id,
            "delta_id": exact_delta_id,
            "pv_ref": exact_pv_ref,
            "ref": f"host-memory-import://{receipt_sha256}",
            "sha256": source_hash,
        },
        "candidate_created": False,
        "accepted_learning": False,
        "learning_hil_invoked": False,
        "learning_pointer_moved": False,
        "project_hil_invoked": False,
        "project_truth_pointer_moved": False,
        "raw_host_memory_stored": False,
        "private_reasoning_stored": False,
    }


def _validated_evidence(
    values: list[dict[str, Any]],
    *,
    root: Path,
    project_id: str,
    scope_kind: str,
    field: str,
    require_one: bool,
) -> list[dict[str, str]]:
    require(
        isinstance(values, list) and (bool(values) or not require_one),
        "LEARNING_EVIDENCE_REQUIRED",
        "A Learning Candidate requires bounded visible evidence.",
        status="BLOCKED",
        field=field,
    )
    result: list[dict[str, str]] = []
    for index, item in enumerate(values):
        require(
            isinstance(item, dict) and set(item) == _EVIDENCE_KEYS,
            "LEARNING_EVIDENCE_SHAPE_INVALID",
            "Learning evidence must use only the governed provenance fields.",
            status="BLOCKED",
            field=field,
            index=index,
        )
        exact = {key: str(item[key]).strip() for key in sorted(_EVIDENCE_KEYS)}
        require(
            all(exact.values()) and bool(_PV_RE.fullmatch(exact["pv_ref"])),
            "LEARNING_EVIDENCE_PROVENANCE_INVALID",
            "Learning evidence requires exact project/task/Delta/PV/ref provenance.",
            status="BLOCKED",
            field=field,
            index=index,
        )
        exact["sha256"] = _sha256(exact["sha256"], field=f"{field}[{index}].sha256")
        require(
            exact["project_id"] == project_id
            or scope_kind == "CROSS_PROJECT_PROPOSED",
            "LEARNING_CROSS_PROJECT_EVIDENCE_DENIED",
            "Cross-project evidence requires an explicit proposed cross-project scope.",
            status="BLOCKED",
            field=field,
            index=index,
        )
        reference = exact["ref"]
        direct_host_memory = any(
            reference.startswith(prefix)
            for prefix in _DIRECT_HOST_MEMORY_PREFIXES
        )
        require(
            not direct_host_memory,
            "LEARNING_HOST_MEMORY_PROVENANCE_REQUIRED",
            "Host memory may enter Learning only through one sealed provenance receipt.",
            status="BLOCKED",
            field=field,
            index=index,
        )
        if reference.startswith("host-memory-import://"):
            match = _HOST_MEMORY_IMPORT_REF_RE.fullmatch(reference)
            require(
                match is not None,
                "LEARNING_HOST_MEMORY_IMPORT_REFERENCE_INVALID",
                "A host-memory evidence reference must name one exact receipt SHA-256.",
                status="BLOCKED",
                field=field,
                index=index,
            )
            assert match is not None
            receipt = _verify_host_memory_import_receipt(
                root,
                project_id=project_id,
                receipt_sha256=match.group("sha256"),
            )
            context = cast(dict[str, Any], receipt["import_context"])
            source = cast(dict[str, Any], receipt["source"])
            require(
                exact["task_id"] == context["task_id"]
                and exact["delta_id"] == context["delta_id"]
                and exact["pv_ref"] == context["pv_ref"]
                and exact["sha256"] == source["record_sha256"],
                "LEARNING_HOST_MEMORY_IMPORT_EVIDENCE_MISMATCH",
                "Learning evidence does not match its host-memory import receipt.",
                status="MISMATCH",
                field=field,
                index=index,
            )
        result.append(exact)
    return result


def _host_memory_import_receipt_sha256s(
    *evidence_groups: list[dict[str, str]],
) -> list[str]:
    identities: set[str] = set()
    for group in evidence_groups:
        for item in group:
            match = _HOST_MEMORY_IMPORT_REF_RE.fullmatch(item["ref"])
            if match is not None:
                identities.add(match.group("sha256"))
    return sorted(identities)


def _candidate_hash(candidate: dict[str, Any]) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {key: value for key, value in candidate.items() if key != "candidate_sha256"}
        )
    )


def _verify_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    require(
        candidate.get("schema") == LEARNING_CANDIDATE_SCHEMA
        and set(candidate) == _CANDIDATE_KEYS,
        "LEARNING_CANDIDATE_SCHEMA_INVALID",
        "The Learning Candidate does not match the sealed schema exactly.",
        status="MISMATCH",
    )
    claimed = _sha256(candidate.get("candidate_sha256"), field="candidate_sha256")
    require(
        claimed == _candidate_hash(candidate),
        "LEARNING_CANDIDATE_HASH_MISMATCH",
        "The Learning Candidate bytes do not match its immutable seal.",
        status="MISMATCH",
    )
    require(
        not contains_secret(candidate),
        "LEARNING_CANDIDATE_SECRET_BLOCKED",
        "Secrets and credentials cannot enter Agent Learning.",
        status="BLOCKED",
    )
    return candidate


def _validate_candidate_evidence_authority(
    root: Path,
    candidate: dict[str, Any],
) -> list[str]:
    project_id = str(candidate["project_id"])
    scope_kind = str(candidate["scope"]["kind"])
    evidence = _validated_evidence(
        candidate["evidence"],
        root=root,
        project_id=project_id,
        scope_kind=scope_kind,
        field="evidence",
        require_one=True,
    )
    counterevidence = _validated_evidence(
        candidate["counterevidence"],
        root=root,
        project_id=project_id,
        scope_kind=scope_kind,
        field="counterevidence",
        require_one=False,
    )
    return _host_memory_import_receipt_sha256s(evidence, counterevidence)


def _append_event(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    candidate_id: str,
    event_type: str,
    lifecycle_state: str,
    occurred_at: str,
    details: dict[str, Any],
    decision_key_sha256: str | None = None,
) -> dict[str, Any]:
    body = {
        "schema": LEARNING_EVENT_SCHEMA,
        "project_id": project_id,
        "candidate_id": candidate_id,
        "event_type": event_type,
        "lifecycle_state": lifecycle_state,
        "occurred_at": occurred_at,
        "details": details,
        "decision_key_sha256": decision_key_sha256,
        "private_reasoning_stored": False,
        "project_truth_effect": "NONE",
    }
    event_sha256 = sha256_bytes(canonical_json_bytes(body))
    event = {
        **body,
        "event_id": f"learn_evt_{event_sha256[:24].lower()}",
        "event_sha256": event_sha256,
    }
    connection.execute(
        """
        INSERT INTO learning_event(
            event_id,candidate_id,event_type,lifecycle_state,occurred_at,
            decision_key_sha256,event_sha256,event_json
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            event["event_id"],
            candidate_id,
            event_type,
            lifecycle_state,
            occurred_at,
            decision_key_sha256,
            event_sha256,
            canonical_json_bytes(event).decode("utf-8"),
        ),
    )
    return event


def _current_state(connection: sqlite3.Connection, candidate_id: str) -> str:
    row = connection.execute(
        """
        SELECT lifecycle_state FROM learning_event
        WHERE candidate_id=? ORDER BY sequence DESC LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    require(
        row is not None,
        "LEARNING_CANDIDATE_NOT_FOUND",
        "The Learning Candidate has no lifecycle authority.",
        status="BLOCKED",
        candidate_id=candidate_id,
    )
    return str(row["lifecycle_state"])


def _read_pointer(root: Path, *, project_id: str) -> dict[str, Any]:
    path = _pointer_path(root)
    if not path.exists():
        return {
            "schema": LEARNING_POINTER_SCHEMA,
            "project_id": project_id,
            "generation": 0,
            "accepted_candidate_id": None,
            "accepted_candidate_sha256": None,
            "prior_generation": None,
            "pointer_sha256": None,
            "state": "NO_ACCEPTED_LEARNING",
        }
    pointer = _load_json(path, code="LEARNING_POINTER_INVALID")
    require(
        pointer.get("schema") == LEARNING_POINTER_SCHEMA
        and pointer.get("project_id") == project_id,
        "LEARNING_POINTER_BINDING_MISMATCH",
        "The Learning pointer does not belong to this project.",
        status="MISMATCH",
    )
    claimed = _sha256(pointer.get("pointer_sha256"), field="pointer_sha256")
    actual = sha256_bytes(
        canonical_json_bytes(
            {key: value for key, value in pointer.items() if key != "pointer_sha256"}
        )
    )
    require(
        claimed == actual,
        "LEARNING_POINTER_HASH_MISMATCH",
        "The Learning pointer failed its self-hash verification.",
        status="MISMATCH",
    )
    return pointer


def _write_pointer(
    connection: sqlite3.Connection,
    root: Path,
    *,
    project_id: str,
    candidate: dict[str, Any],
    prior_pointer: dict[str, Any],
    moved_at: str,
    reason: str,
    rollback_target_generation: int | None = None,
) -> dict[str, Any]:
    prior_generation = int(prior_pointer["generation"])
    body = {
        "schema": LEARNING_POINTER_SCHEMA,
        "project_id": project_id,
        "generation": prior_generation + 1,
        "accepted_candidate_id": candidate["candidate_id"],
        "accepted_candidate_sha256": candidate["candidate_sha256"],
        "prior_generation": prior_generation,
        "reason": reason,
        "rollback_target_generation": rollback_target_generation,
        "updated_at": moved_at,
        "project_truth_pointer_moved": False,
    }
    pointer = {**body, "pointer_sha256": sha256_bytes(canonical_json_bytes(body))}
    connection.execute(
        """
        INSERT INTO learning_pointer_history(
            project_id,generation,candidate_id,candidate_sha256,
            prior_generation,reason,pointer_sha256,pointer_json
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            project_id,
            pointer["generation"],
            pointer["accepted_candidate_id"],
            pointer["accepted_candidate_sha256"],
            prior_generation,
            reason,
            pointer["pointer_sha256"],
            canonical_json_bytes(pointer).decode("utf-8"),
        ),
    )
    atomic_write_json(_pointer_path(root), pointer)
    return pointer


def seal_learning_candidate(
    project_root: str | Path,
    *,
    project_id: str,
    tier: str,
    lesson_type: str,
    statement: str,
    scope: dict[str, Any],
    evidence: list[dict[str, Any]],
    outcome: str,
    confidence: float,
    counterevidence: list[dict[str, Any]],
    contradictions: list[str],
    temporal: dict[str, Any],
    privacy: str,
    source_lineage_head_sha256: str,
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Seal one immutable candidate; this is evidence, not accepted learning."""

    root = _project_root(project_root, project_id=project_id)
    exact_tier = str(tier).strip().upper()
    exact_type = str(lesson_type).strip().upper()
    exact_outcome = str(outcome).strip().upper()
    exact_privacy = str(privacy).strip().upper()
    exact_statement = str(statement).strip()
    require(
        exact_tier in _TIERS
        and exact_type in _LESSON_TYPES
        and exact_outcome in _OUTCOMES
        and exact_privacy in _PRIVACY_CLASSES,
        "LEARNING_CANDIDATE_ENUM_INVALID",
        "A Learning Candidate classification is outside the governed schema.",
        status="BLOCKED",
    )
    require(
        bool(exact_statement),
        "LEARNING_STATEMENT_REQUIRED",
        "A Learning Candidate requires one visible lesson statement.",
        status="BLOCKED",
    )
    require(
        isinstance(scope, dict)
        and set(scope) == {"kind", "selectors"}
        and str(scope.get("kind") or "").upper() in _SCOPE_KINDS
        and isinstance(scope.get("selectors"), list)
        and all(str(item).strip() for item in scope["selectors"]),
        "LEARNING_SCOPE_INVALID",
        "A Learning Candidate requires one bounded typed scope.",
        status="BLOCKED",
    )
    exact_scope: dict[str, Any] = {
        "kind": str(scope["kind"]).upper(),
        "selectors": sorted({str(item).strip() for item in scope["selectors"]}),
    }
    require(
        isinstance(confidence, (int, float)) and 0 <= float(confidence) <= 1,
        "LEARNING_CONFIDENCE_INVALID",
        "Learning confidence must be between zero and one.",
        status="BLOCKED",
    )
    require(
        isinstance(contradictions, list)
        and all(isinstance(item, str) and item.strip() for item in contradictions),
        "LEARNING_CONTRADICTIONS_INVALID",
        "Learning contradiction links must be visible non-empty strings.",
        status="BLOCKED",
    )
    require(
        isinstance(temporal, dict)
        and set(temporal) == {"observed_at", "valid_from", "expires_at"},
        "LEARNING_TEMPORAL_INVALID",
        "Learning temporal metadata must contain the exact governed fields.",
        status="BLOCKED",
    )
    exact_temporal = {
        "observed_at": _timestamp(temporal["observed_at"], field="observed_at"),
        "valid_from": _timestamp(temporal["valid_from"], field="valid_from"),
        "expires_at": _timestamp(
            temporal["expires_at"], field="expires_at", nullable=True
        ),
    }
    observed = cast(str, exact_temporal["observed_at"])
    valid_from = cast(str, exact_temporal["valid_from"])
    expires_at = cast(str | None, exact_temporal["expires_at"])
    require(
        observed <= valid_from and (expires_at is None or valid_from < expires_at),
        "LEARNING_TEMPORAL_ORDER_INVALID",
        "Learning temporal bounds are not in causal order.",
        status="BLOCKED",
    )
    exact_evidence = _validated_evidence(
        evidence,
        root=root,
        project_id=project_id,
        scope_kind=exact_scope["kind"],
        field="evidence",
        require_one=True,
    )
    exact_counterevidence = _validated_evidence(
        counterevidence,
        root=root,
        project_id=project_id,
        scope_kind=exact_scope["kind"],
        field="counterevidence",
        require_one=False,
    )
    host_memory_import_receipt_sha256s = _host_memory_import_receipt_sha256s(
        exact_evidence,
        exact_counterevidence,
    )
    lineage_head = _sha256(
        source_lineage_head_sha256,
        field="source_lineage_head_sha256",
    )
    if supersedes is not None:
        supersedes = str(supersedes).strip()
        require(
            bool(supersedes),
            "LEARNING_SUPERSEDES_INVALID",
            "A superseded Learning Candidate requires an exact identity.",
            status="BLOCKED",
        )
    base = {
        "schema": LEARNING_CANDIDATE_SCHEMA,
        "project_id": project_id,
        "lesson_type": exact_type,
        "statement": exact_statement,
        "scope": exact_scope,
        "evidence": exact_evidence,
        "outcome": exact_outcome,
        "confidence": float(confidence),
        "counterevidence": exact_counterevidence,
        "contradictions": sorted(set(contradictions)),
        "temporal": exact_temporal,
        "privacy": exact_privacy,
        "supersedes": supersedes,
        "source_lineage_head_sha256": lineage_head,
    }
    dedup_key_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "lesson_type": exact_type,
                "statement": exact_statement,
                "scope": exact_scope,
            }
        )
    )
    identity_sha256 = sha256_bytes(canonical_json_bytes(base))
    candidate: dict[str, Any] = {
        **base,
        "candidate_id": f"learn_{identity_sha256[:24].lower()}",
    }
    candidate["candidate_sha256"] = _candidate_hash(candidate)
    _verify_candidate(candidate)
    path = _candidate_path(root, candidate["candidate_id"])
    _immutable_json(path, candidate)

    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT candidate_sha256 FROM learning_candidate WHERE candidate_id=?",
            (candidate["candidate_id"],),
        ).fetchone()
        if existing is not None:
            require(
                existing["candidate_sha256"] == candidate["candidate_sha256"],
                "LEARNING_CANDIDATE_ID_CONFLICT",
                "The Learning Candidate identity already names other bytes.",
                status="MISMATCH",
            )
            connection.rollback()
            return {
                "status": "PASS",
                "state": _current_state(connection, candidate["candidate_id"]),
                "candidate": candidate,
                "candidate_path": str(path),
                "idempotent_reuse": True,
                "project_truth_pointer_moved": False,
                "project_hil_invoked": False,
                "host_memory_authority": HOST_MEMORY_AUTHORITY,
                "host_memory_import_receipt_sha256s": (
                    host_memory_import_receipt_sha256s
                ),
                "private_reasoning_stored": False,
            }
        connection.execute(
            """
            INSERT INTO learning_candidate(
                candidate_id,project_id,candidate_sha256,dedup_key_sha256,
                tier,lesson_type,expires_at,candidate_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                candidate["candidate_id"],
                project_id,
                candidate["candidate_sha256"],
                dedup_key_sha256,
                exact_tier,
                exact_type,
                expires_at,
                canonical_json_bytes(candidate).decode("utf-8"),
            ),
        )
        connection.execute(
            """
            INSERT INTO learning_candidate_fts(
                candidate_id,project_id,statement,lesson_type,scope_text
            ) VALUES(?,?,?,?,?)
            """,
            _fts_document(candidate),
        )
        event = _append_event(
            connection,
            project_id=project_id,
            candidate_id=candidate["candidate_id"],
            event_type="SEALED",
            lifecycle_state="PENDING_LEARNING_HIL",
            occurred_at=valid_from,
            details={
                "candidate_sha256": candidate["candidate_sha256"],
                "tier": exact_tier,
                "source_lineage_head_sha256": lineage_head,
                "host_memory_authority": HOST_MEMORY_AUTHORITY,
                "host_memory_import_receipt_sha256s": (
                    host_memory_import_receipt_sha256s
                ),
            },
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "status": "PASS",
        "state": "PENDING_LEARNING_HIL",
        "candidate": candidate,
        "candidate_path": str(path),
        "seal_event": event,
        "idempotent_reuse": False,
        "project_truth_pointer_moved": False,
        "project_hil_invoked": False,
        "host_memory_authority": HOST_MEMORY_AUTHORITY,
        "host_memory_import_receipt_sha256s": (
            host_memory_import_receipt_sha256s
        ),
        "private_reasoning_stored": False,
    }


def _candidate_from_db(
    connection: sqlite3.Connection, root: Path, candidate_id: str
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT candidate_json FROM learning_candidate WHERE candidate_id=?",
        (candidate_id,),
    ).fetchone()
    require(
        row is not None,
        "LEARNING_CANDIDATE_NOT_FOUND",
        "The requested Learning Candidate does not exist in this project.",
        status="BLOCKED",
        candidate_id=candidate_id,
    )
    candidate = cast(dict[str, Any], json.loads(str(row["candidate_json"])))
    _verify_candidate(candidate)
    _validate_candidate_evidence_authority(root, candidate)
    file_candidate = _load_json(
        _candidate_path(root, candidate_id), code="LEARNING_CANDIDATE_FILE_REQUIRED"
    )
    require(
        canonical_json_bytes(file_candidate) == canonical_json_bytes(candidate),
        "LEARNING_CANDIDATE_FILE_LEDGER_MISMATCH",
        "The Learning Candidate file and SQLite authority disagree.",
        status="MISMATCH",
        candidate_id=candidate_id,
    )
    return candidate


def _parse_decision(decision_token: str) -> tuple[str, str | int | None]:
    require(
        decision_token == decision_token.strip(),
        "LEARNING_DECISION_TOKEN_INVALID",
        "Learning HIL decisions must use one exact visible token.",
        status="BLOCKED",
    )
    if decision_token == "APPROVE":
        return "APPROVE", None
    rollback = _ROLLBACK_RE.fullmatch(decision_token)
    if rollback:
        return "ROLLBACK", int(rollback.group(1))
    for prefix in _PREFIXED_DECISIONS:
        marker = f"{prefix}: "
        if decision_token.startswith(marker) and decision_token[len(marker) :].strip():
            return prefix, decision_token[len(marker) :]
    require(
        False,
        "LEARNING_DECISION_TOKEN_INVALID",
        "The exact Learning six-way HIL token is not recognized.",
        status="BLOCKED",
        allowed=[
            "APPROVE",
            "APPROVE_WITH_DELTA: correction",
            "MORE_RESEARCH: question",
            "ROLLBACK: LGENn",
            "REJECT: reason",
            "FAIL: gate",
        ],
    )
    raise AssertionError("unreachable")


def decide_learning_candidate(
    project_root: str | Path,
    *,
    project_id: str,
    candidate_id: str,
    expected_candidate_sha256: str,
    decision_token: str,
    actor_id: str,
    decided_at: str,
    expected_project_truth_pointer_sha256: str,
) -> dict[str, Any]:
    """Record an explicit Learning HIL decision without invoking Project HIL."""

    root = _project_root(project_root, project_id=project_id)
    exact_candidate_sha256 = _sha256(
        expected_candidate_sha256, field="expected_candidate_sha256"
    )
    expected_project_sha256 = _sha256(
        expected_project_truth_pointer_sha256,
        field="expected_project_truth_pointer_sha256",
    )
    exact_decided_at = cast(str, _timestamp(decided_at, field="decided_at"))
    exact_actor = str(actor_id).strip()
    require(
        bool(exact_actor),
        "LEARNING_DECISION_ACTOR_REQUIRED",
        "A Learning HIL decision requires one visible actor identity.",
        status="BLOCKED",
    )
    action, detail = _parse_decision(decision_token)
    project_before = project_truth_pointer_sha256(root, project_id=project_id)
    require(
        project_before == expected_project_sha256,
        "LEARNING_PROJECT_POINTER_STALE",
        "Project Truth changed before the Learning decision could be recorded.",
        status="STALE",
    )
    decision_key = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "candidate_id": candidate_id,
                "expected_candidate_sha256": exact_candidate_sha256,
                "decision_token": decision_token,
                "actor_id": exact_actor,
                "decided_at": exact_decided_at,
            }
        )
    )
    connection = _connect(root)
    try:
        prior_receipt = connection.execute(
            """
            SELECT receipt_json FROM learning_decision_receipt
            WHERE decision_key_sha256=?
            """,
            (decision_key,),
        ).fetchone()
        if prior_receipt is not None:
            receipt = cast(dict[str, Any], json.loads(str(prior_receipt["receipt_json"])))
            return {"status": "PASS", "idempotent_reuse": True, "receipt": receipt}
        connection.execute("BEGIN IMMEDIATE")
        candidate = _candidate_from_db(connection, root, candidate_id)
        require(
            candidate["candidate_sha256"] == exact_candidate_sha256,
            "LEARNING_CANDIDATE_IDENTITY_MISMATCH",
            "The expected Learning Candidate hash does not match the sealed candidate.",
            status="MISMATCH",
        )
        state_before = _current_state(connection, candidate_id)
        require(
            state_before not in _TERMINAL_STATES
            and state_before
            not in {"CORRECTION_REQUESTED", "RESEARCH_REQUESTED", "ROLLED_BACK"},
            "LEARNING_CANDIDATE_NOT_DECIDABLE",
            "The Learning Candidate is no longer at an open decision boundary.",
            status="BLOCKED",
            state=state_before,
        )
        expires_at = candidate["temporal"]["expires_at"]
        require(
            expires_at is None or exact_decided_at < expires_at,
            "LEARNING_CANDIDATE_EXPIRED",
            "An expired Learning Candidate cannot be accepted or decided.",
            status="BLOCKED",
            expires_at=expires_at,
        )
        pointer_before = _read_pointer(root, project_id=project_id)
        pointer_after = pointer_before
        affected_events: list[dict[str, Any]] = []
        if action == "APPROVE":
            supersedes = candidate.get("supersedes")
            if supersedes:
                predecessor = _candidate_from_db(connection, root, str(supersedes))
                predecessor_state = _current_state(connection, str(supersedes))
                require(
                    predecessor_state == "ACCEPTED",
                    "LEARNING_SUPERSESSION_TARGET_NOT_ACCEPTED",
                    "A Learning Candidate may supersede only accepted learning.",
                    status="BLOCKED",
                    supersedes=supersedes,
                )
                affected_events.append(
                    _append_event(
                        connection,
                        project_id=project_id,
                        candidate_id=predecessor["candidate_id"],
                        event_type="SUPERSEDED",
                        lifecycle_state="SUPERSEDED",
                        occurred_at=exact_decided_at,
                        details={"superseded_by": candidate_id},
                    )
                )
            event = _append_event(
                connection,
                project_id=project_id,
                candidate_id=candidate_id,
                event_type="ACCEPTED",
                lifecycle_state="ACCEPTED",
                occurred_at=exact_decided_at,
                details={
                    "actor_id": exact_actor,
                    "decision_token_sha256": sha256_bytes(decision_token.encode()),
                },
                decision_key_sha256=decision_key,
            )
            pointer_after = _write_pointer(
                connection,
                root,
                project_id=project_id,
                candidate=candidate,
                prior_pointer=pointer_before,
                moved_at=exact_decided_at,
                reason="EXACT_LEARNING_APPROVE",
            )
            state_after = "ACCEPTED"
        elif action == "ROLLBACK":
            target_generation = cast(int, detail)
            require(
                int(pointer_before["generation"]) > target_generation,
                "LEARNING_ROLLBACK_TARGET_INVALID",
                "Learning rollback requires an earlier accepted Learning generation.",
                status="BLOCKED",
                current_generation=pointer_before["generation"],
                target_generation=target_generation,
            )
            target_row = connection.execute(
                """
                SELECT candidate_id FROM learning_pointer_history
                WHERE project_id=? AND generation=?
                """,
                (project_id, target_generation),
            ).fetchone()
            require(
                target_row is not None,
                "LEARNING_ROLLBACK_TARGET_NOT_FOUND",
                "The requested Learning generation is not in immutable pointer history.",
                status="BLOCKED",
                target_generation=target_generation,
            )
            require(
                pointer_before["accepted_candidate_id"] == candidate_id,
                "LEARNING_ROLLBACK_HEAD_MISMATCH",
                "Learning rollback must name the current accepted Learning head.",
                status="MISMATCH",
            )
            target = _candidate_from_db(connection, root, str(target_row["candidate_id"]))
            event = _append_event(
                connection,
                project_id=project_id,
                candidate_id=candidate_id,
                event_type="ROLLED_BACK",
                lifecycle_state="ROLLED_BACK",
                occurred_at=exact_decided_at,
                details={
                    "actor_id": exact_actor,
                    "target_generation": target_generation,
                    "target_candidate_id": target["candidate_id"],
                },
                decision_key_sha256=decision_key,
            )
            affected_events.append(
                _append_event(
                    connection,
                    project_id=project_id,
                    candidate_id=target["candidate_id"],
                    event_type="RESTORED_BY_ROLLBACK",
                    lifecycle_state="ACCEPTED",
                    occurred_at=exact_decided_at,
                    details={
                        "restored_from_generation": target_generation,
                        "rolled_back_candidate_id": candidate_id,
                    },
                )
            )
            pointer_after = _write_pointer(
                connection,
                root,
                project_id=project_id,
                candidate=target,
                prior_pointer=pointer_before,
                moved_at=exact_decided_at,
                reason="LEARNING_POINTER_ONLY_ROLLBACK",
                rollback_target_generation=target_generation,
            )
            state_after = "ROLLED_BACK"
        else:
            state_after = _PREFIXED_DECISIONS[action]
            event = _append_event(
                connection,
                project_id=project_id,
                candidate_id=candidate_id,
                event_type=action,
                lifecycle_state=state_after,
                occurred_at=exact_decided_at,
                details={"actor_id": exact_actor, "reason": detail},
                decision_key_sha256=decision_key,
            )

        project_after = project_truth_pointer_sha256(root, project_id=project_id)
        require(
            project_before == project_after,
            "LEARNING_PROJECT_POINTER_MUTATION_DETECTED",
            "A Learning decision changed Project Truth and was stopped.",
            status="MISMATCH",
        )
        receipt_body = {
            "schema": LEARNING_DECISION_RECEIPT_SCHEMA,
            "project_id": project_id,
            "candidate_id": candidate_id,
            "candidate_sha256": exact_candidate_sha256,
            "decision_token": decision_token,
            "decision_token_sha256": sha256_bytes(decision_token.encode()),
            "decision_key_sha256": decision_key,
            "actor_id": exact_actor,
            "decided_at": exact_decided_at,
            "state_before": state_before,
            "state_after": state_after,
            "event_sha256": event["event_sha256"],
            "affected_event_sha256s": [item["event_sha256"] for item in affected_events],
            "learning_pointer_before_sha256": pointer_before.get("pointer_sha256"),
            "learning_pointer_after_sha256": pointer_after.get("pointer_sha256"),
            "learning_pointer_moved": pointer_before != pointer_after,
            "project_truth_pointer_before_sha256": project_before,
            "project_truth_pointer_after_sha256": project_after,
            "project_truth_pointer_moved": False,
            "project_hil_invoked": False,
            "project_candidate_created": False,
            "private_reasoning_stored": False,
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_body))
        receipt = {**receipt_body, "receipt_sha256": receipt_sha256}
        _immutable_json(_receipt_path(root, receipt_sha256), receipt)
        connection.execute(
            """
            INSERT INTO learning_decision_receipt(
                decision_key_sha256,candidate_id,receipt_sha256,receipt_json
            ) VALUES(?,?,?,?)
            """,
            (
                decision_key,
                candidate_id,
                receipt_sha256,
                canonical_json_bytes(receipt).decode("utf-8"),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"status": "PASS", "idempotent_reuse": False, "receipt": receipt}


def expire_learning_candidates(
    project_root: str | Path,
    *,
    project_id: str,
    as_of: str,
    expiry_owner: str | None = None,
) -> dict[str, Any]:
    """Materialize expiry under its sole owner without changing either pointer."""

    root = _project_root(project_root, project_id=project_id)
    exact_owner = str(expiry_owner or "").strip()
    require(
        exact_owner == LEARNING_EXPIRY_OWNER,
        "LEARNING_EXPIRY_OWNER_REQUIRED",
        "Only the explicit Agent Learning maintenance owner may append expiry events.",
        status="BLOCKED",
        required_owner=LEARNING_EXPIRY_OWNER,
    )
    exact_as_of = cast(str, _timestamp(as_of, field="as_of"))
    project_before = project_truth_pointer_sha256(root, project_id=project_id)
    connection = _connect(root)
    expired: list[str] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT candidate_id FROM learning_candidate
            WHERE project_id=? AND expires_at IS NOT NULL AND expires_at<=?
            ORDER BY candidate_id
            """,
            (project_id, exact_as_of),
        ).fetchall()
        for row in rows:
            candidate_id = str(row["candidate_id"])
            state = _current_state(connection, candidate_id)
            if state in _TERMINAL_STATES or state == "ROLLED_BACK":
                continue
            _append_event(
                connection,
                project_id=project_id,
                candidate_id=candidate_id,
                event_type="EXPIRED",
                lifecycle_state="EXPIRED",
                occurred_at=exact_as_of,
                details={
                    "expiry_owner": exact_owner,
                    "expired_without_project_truth_effect": True,
                    "expired_without_learning_pointer_effect": True,
                },
            )
            expired.append(candidate_id)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    project_after = project_truth_pointer_sha256(root, project_id=project_id)
    require(
        project_before == project_after,
        "LEARNING_PROJECT_POINTER_MUTATION_DETECTED",
        "Learning expiry changed Project Truth and was stopped.",
        status="MISMATCH",
    )
    receipt_body = {
        "schema": LEARNING_EXPIRY_RECEIPT_SCHEMA,
        "status": "PASS",
        "project_id": project_id,
        "as_of": exact_as_of,
        "expiry_owner": exact_owner,
        "expired_candidate_ids": expired,
        "project_truth_pointer_moved": False,
        "learning_pointer_moved": False,
        "public_action_invoked": False,
        "hook_invoked": False,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    return {**receipt_body, "receipt": receipt}


def revoke_learning_candidate(
    project_root: str | Path,
    *,
    project_id: str,
    candidate_id: str,
    reason: str,
    revoked_at: str,
) -> dict[str, Any]:
    """Append a revocation event while preserving candidate and pointer history."""

    root = _project_root(project_root, project_id=project_id)
    exact_reason = str(reason).strip()
    require(
        bool(exact_reason),
        "LEARNING_REVOCATION_REASON_REQUIRED",
        "Learning revocation requires one visible reason.",
        status="BLOCKED",
    )
    exact_revoked_at = cast(str, _timestamp(revoked_at, field="revoked_at"))
    project_before = project_truth_pointer_sha256(root, project_id=project_id)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _candidate_from_db(connection, root, candidate_id)
        state = _current_state(connection, candidate_id)
        require(
            state == "ACCEPTED",
            "LEARNING_REVOCATION_STATE_INVALID",
            "Only accepted learning can be revoked.",
            status="BLOCKED",
            state=state,
        )
        event = _append_event(
            connection,
            project_id=project_id,
            candidate_id=candidate_id,
            event_type="REVOKED",
            lifecycle_state="REVOKED",
            occurred_at=exact_revoked_at,
            details={"reason": exact_reason},
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    require(
        project_before == project_truth_pointer_sha256(root, project_id=project_id),
        "LEARNING_PROJECT_POINTER_MUTATION_DETECTED",
        "Learning revocation changed Project Truth and was stopped.",
        status="MISMATCH",
    )
    return {
        "status": "PASS",
        "state": "REVOKED",
        "event": event,
        "project_truth_pointer_moved": False,
        "learning_pointer_moved": False,
    }


def retrieve_accepted_learning(
    project_root: str | Path,
    *,
    project_id: str,
    query: str,
    scope_selectors: list[str],
    as_of: str,
    project_truth_conflict_candidate_ids: list[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Return a bounded Learning-only slice and explicit contradiction receipts."""

    root = _project_root(project_root, project_id=project_id)
    exact_as_of = cast(str, _timestamp(as_of, field="as_of"))
    exact_query = str(query).strip().lower()
    require(
        bool(exact_query) and 1 <= limit <= 20,
        "LEARNING_RETRIEVAL_BOUNDS_INVALID",
        "Learning retrieval requires a query and a limit from one to twenty.",
        status="BLOCKED",
    )
    requested_scope = {str(item).strip() for item in scope_selectors if str(item).strip()}
    conflicts = set(project_truth_conflict_candidate_ids or [])
    query_terms = sorted(set(re.findall(r"[a-z0-9_]+", exact_query)))
    require(
        bool(query_terms),
        "LEARNING_RETRIEVAL_QUERY_TERMS_REQUIRED",
        "The Learning query has no indexable FTS5 term.",
        status="BLOCKED",
    )
    match_query = " OR ".join(f'"{term}"' for term in query_terms)
    connection = _connect(root)
    hits: list[tuple[float, dict[str, Any]]] = []
    suppressed: list[dict[str, Any]] = []
    try:
        rows = connection.execute(
            """
            SELECT
                candidate.candidate_id,
                candidate.candidate_json,
                bm25(learning_candidate_fts,0.0,0.0,5.0,2.0,1.0) AS rank
            FROM learning_candidate_fts
            JOIN learning_candidate AS candidate
              ON candidate.candidate_id=learning_candidate_fts.candidate_id
            WHERE learning_candidate_fts MATCH ?
              AND learning_candidate_fts.project_id=?
            ORDER BY rank,candidate.candidate_id
            """,
            (match_query, project_id),
        ).fetchall()
        for row in rows:
            candidate_id = str(row["candidate_id"])
            if _current_state(connection, candidate_id) != "ACCEPTED":
                continue
            candidate = cast(dict[str, Any], json.loads(str(row["candidate_json"])))
            _verify_candidate(candidate)
            _validate_candidate_evidence_authority(root, candidate)
            temporal = candidate["temporal"]
            temporal_state = None
            if exact_as_of < temporal["valid_from"]:
                temporal_state = "SUPPRESSED_NOT_YET_VALID"
            elif (
                temporal["expires_at"] is not None
                and exact_as_of >= temporal["expires_at"]
            ):
                temporal_state = "SUPPRESSED_TEMPORAL_EXPIRY"
            if temporal_state is not None:
                temporal_body = {
                    "schema": LEARNING_RETRIEVAL_RECEIPT_SCHEMA,
                    "project_id": project_id,
                    "candidate_id": candidate_id,
                    "state": temporal_state,
                    "candidate_sha256": candidate["candidate_sha256"],
                    "as_of": exact_as_of,
                    "valid_from": temporal["valid_from"],
                    "expires_at": temporal["expires_at"],
                    "expiry_event_materialized": False,
                    "project_truth_effect": "NONE",
                }
                suppressed.append(
                    {
                        **temporal_body,
                        "receipt_sha256": sha256_bytes(
                            canonical_json_bytes(temporal_body)
                        ),
                    }
                )
                continue
            selectors = set(candidate["scope"]["selectors"])
            if candidate["scope"]["kind"] == "TASK" and not (
                selectors & requested_scope
            ):
                scope_body = {
                    "schema": LEARNING_RETRIEVAL_RECEIPT_SCHEMA,
                    "project_id": project_id,
                    "candidate_id": candidate_id,
                    "state": "SUPPRESSED_SCOPE_MISMATCH",
                    "candidate_sha256": candidate["candidate_sha256"],
                    "requested_scope": sorted(requested_scope),
                    "candidate_scope": candidate["scope"],
                    "project_truth_effect": "NONE",
                }
                suppressed.append(
                    {
                        **scope_body,
                        "receipt_sha256": sha256_bytes(
                            canonical_json_bytes(scope_body)
                        ),
                    }
                )
                continue
            if candidate_id in conflicts:
                conflict_body = {
                    "schema": LEARNING_RETRIEVAL_RECEIPT_SCHEMA,
                    "project_id": project_id,
                    "candidate_id": candidate_id,
                    "state": "SUPPRESSED_PROJECT_TRUTH_CONTRADICTION",
                    "candidate_sha256": candidate["candidate_sha256"],
                    "project_truth_effect": "NONE",
                }
                suppressed.append(
                    {
                        **conflict_body,
                        "receipt_sha256": sha256_bytes(
                            canonical_json_bytes(conflict_body)
                        ),
                    }
                )
                continue
            hits.append((float(row["rank"]), candidate))
    finally:
        connection.close()
    selected = [
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": candidate["candidate_sha256"],
            "statement": candidate["statement"],
            "lesson_type": candidate["lesson_type"],
            "scope": candidate["scope"],
            "confidence": candidate["confidence"],
            "evidence": candidate["evidence"],
            "counterevidence": candidate["counterevidence"],
            "source_lineage_head_sha256": candidate[
                "source_lineage_head_sha256"
            ],
        }
        for _, candidate in sorted(
            hits,
            key=lambda item: (item[0], item[1]["candidate_id"]),
        )[:limit]
    ]
    return {
        "status": "PASS",
        "authority": "AGENT_LEARNING_ONLY",
        "project_id": project_id,
        "result": "HIT" if selected else "NO_HIT",
        "hits": selected,
        "suppressed": suppressed,
        "project_truth_slice": None,
        "project_truth_pointer_moved": False,
        "learning_pointer_moved": False,
        "host_memory_authority": HOST_MEMORY_AUTHORITY,
        "host_memory_promoted": False,
        "search_engine": "SQLITE_FTS5_BM25",
        "full_ledger_loaded_into_model_context": False,
        "expiry_owner": LEARNING_EXPIRY_OWNER,
        "brain_scaling": "BOUNDED_INDEXED_SLICING_NOT_TRAINING",
    }


def inspect_learning_authority(
    project_root: str | Path, *, project_id: str
) -> dict[str, Any]:
    """Verify SQLite, immutable candidate files, event seals, and pointer history."""

    root = _project_root(project_root, project_id=project_id)
    connection = _connect(root)
    try:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        candidates = connection.execute(
            "SELECT candidate_id,candidate_json FROM learning_candidate ORDER BY candidate_id"
        ).fetchall()
        events = connection.execute(
            "SELECT event_json FROM learning_event ORDER BY sequence"
        ).fetchall()
        pointer_rows = connection.execute(
            "SELECT pointer_json FROM learning_pointer_history ORDER BY generation"
        ).fetchall()
        states: dict[str, str] = {}
        expected_fts: list[tuple[str, str, str, str, str]] = []
        for row in candidates:
            candidate = cast(dict[str, Any], json.loads(str(row["candidate_json"])))
            _verify_candidate(candidate)
            _validate_candidate_evidence_authority(root, candidate)
            expected_fts.append(_fts_document(candidate))
            file_value = _load_json(
                _candidate_path(root, candidate["candidate_id"]),
                code="LEARNING_CANDIDATE_FILE_REQUIRED",
            )
            require(
                canonical_json_bytes(file_value) == canonical_json_bytes(candidate),
                "LEARNING_CANDIDATE_FILE_LEDGER_MISMATCH",
                "A Learning Candidate file differs from its SQLite row.",
                status="MISMATCH",
            )
            states[candidate["candidate_id"]] = _current_state(
                connection, candidate["candidate_id"]
            )
        indexed = [
            tuple(str(row[column]) for column in range(5))
            for row in connection.execute(
                """
                SELECT candidate_id,project_id,statement,lesson_type,scope_text
                FROM learning_candidate_fts ORDER BY candidate_id
                """
            )
        ]
        require(
            indexed == sorted(expected_fts),
            "LEARNING_FTS_INDEX_LEDGER_MISMATCH",
            "The Agent Learning FTS5 index does not match its candidate ledger.",
            status="MISMATCH",
        )
        for row in events:
            event = cast(dict[str, Any], json.loads(str(row["event_json"])))
            claimed = _sha256(event.get("event_sha256"), field="event_sha256")
            body = {
                key: value
                for key, value in event.items()
                if key not in {"event_id", "event_sha256"}
            }
            require(
                claimed == sha256_bytes(canonical_json_bytes(body)),
                "LEARNING_EVENT_HASH_MISMATCH",
                "An append-only Learning event failed its hash check.",
                status="MISMATCH",
            )
        for row in pointer_rows:
            pointer = cast(dict[str, Any], json.loads(str(row["pointer_json"])))
            claimed = _sha256(pointer.get("pointer_sha256"), field="pointer_sha256")
            require(
                claimed
                == sha256_bytes(
                    canonical_json_bytes(
                        {
                            key: value
                            for key, value in pointer.items()
                            if key != "pointer_sha256"
                        }
                    )
                ),
                "LEARNING_POINTER_HISTORY_HASH_MISMATCH",
                "A Learning pointer-history entry failed its hash check.",
                status="MISMATCH",
            )
        current_pointer = _read_pointer(root, project_id=project_id)
        import_root = _learning_root(root) / "host-memory-imports"
        host_memory_imports = sorted(import_root.glob("*.json")) if import_root.is_dir() else []
        for import_path in host_memory_imports:
            match = re.fullmatch(r"(?P<sha256>[A-F0-9]{64})\.json", import_path.name)
            require(
                match is not None,
                "LEARNING_HOST_MEMORY_IMPORT_FILENAME_INVALID",
                "A host-memory import artifact has a noncanonical filename.",
                status="MISMATCH",
            )
            assert match is not None
            _verify_host_memory_import_receipt(
                root,
                project_id=project_id,
                receipt_sha256=match.group("sha256"),
            )
    finally:
        connection.close()
    require(
        integrity == ["ok"] and not foreign_keys,
        "LEARNING_LEDGER_INTEGRITY_FAILED",
        "The Agent Learning SQLite authority failed integrity checks.",
        status="MISMATCH",
    )
    return {
        "status": "PASS",
        "project_id": project_id,
        "candidate_count": len(candidates),
        "indexed_candidate_count": len(indexed),
        "event_count": len(events),
        "pointer_generation_count": len(pointer_rows),
        "current_pointer": current_pointer,
        "candidate_states": states,
        "integrity": integrity,
        "foreign_key_errors": len(foreign_keys),
        "project_truth_authority": "SEPARATE_UNCHANGED",
        "canon_input_authority": "SEPARATE_UNCHANGED",
        "chat_lineage_authority": "VISIBLE_EVIDENCE_SOURCE_ONLY",
        "host_memory_authority": HOST_MEMORY_AUTHORITY,
        "host_memory_import_count": len(host_memory_imports),
        "host_memory_automatic_import": False,
        "host_memory_promoted": False,
        "formula_engine_role": "OPERATOR_ROUTER_NOT_LEARNER",
        "runtime_contract": learning_runtime_contract(),
        "brain_scaling": "BOUNDED_INDEXED_SLICING_NOT_TRAINING",
    }
