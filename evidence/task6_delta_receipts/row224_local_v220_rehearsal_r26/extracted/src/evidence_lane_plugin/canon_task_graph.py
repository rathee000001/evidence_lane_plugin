"""Typed task-to-task Canon graph and receiver-owned Canon Input HIL.

Canon is a bounded coordination authority.  It can carry immutable evidence
between independently governed task nodes, but it cannot promote Project
Truth, accept Agent Learning, grant source-write authority, replay another
task's HIL, or merge task ownership.  The receiving task owns admission.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .redaction import contains_secret

CANON_ENVELOPE_SCHEMA = "evidence-lane.canon-envelope.v2"
CANON_EXPECTED_CONTRACT_SCHEMA = "evidence-lane.canon-expected-contract.v1"
CANON_EVENT_SCHEMA = "evidence-lane.canon-input-event.v1"
CANON_DECISION_RECEIPT_SCHEMA = "evidence-lane.canon-decision-receipt.v1"
CANON_EDGE_SCHEMA = "evidence-lane.canon-task-edge.v1"
CANON_DISPATCH_RECEIPT_SCHEMA = "evidence-lane.canon-dispatch-receipt.v1"
CANON_DISPATCH_RECEIPT_SCHEMA_V2 = "evidence-lane.canon-dispatch-receipt.v2"
CODEX_HOST_CREATE_RECEIPT_SCHEMA = (
    "evidence-lane.codex-host-task-create-receipt.v1"
)
CODEX_HOST_CREATE_CAPABILITY = "CODEX_HOST_CREATE_LINKED_TASK_IDEMPOTENT_V1"
CANON_CONTINUITY_SCHEMA = "evidence-lane.canon-state-travel-continuity.v1"
CANON_RESTORE_RECEIPT_SCHEMA = (
    "evidence-lane.canon-state-travel-restore-receipt.v1"
)
CANON_SCHEMA_MANIFEST_SCHEMA = "evidence-lane.canon-schema-manifest.v1"
CANON_LEDGER_SCHEMA = "evidence-lane.canon-ledger.v1"
CANON_LEDGER_SCHEMA_VERSION = 1
CANON_RECEIPT_REGISTRY_SCHEMA = "evidence-lane.canon-receipt-schema-registry.v1"

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_PV_RE = re.compile(r"^PV[1-9][0-9]*$")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(authorization|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|password|private[_-]?key|client[_-]?secret)(?:$|[_-])"
)
_DIRECTIONS = {"UPSTREAM", "DOWNSTREAM", "LATERAL"}
_AUTHORITIES = {
    "BOUNDED_INPUT",
    "CORRECTION_PROPOSAL",
    "EVIDENCE_RESPONSE",
    "PLAN_STEER",
    "TASK_RESULT",
}
_DECISIONS = {"ACCEPT", "REJECT", "MORE_RESEARCH"}
_ADMITTED_STATES = {"EXPECTED_ADMITTED", "ACCEPTED_INPUT"}
_TERMINAL_STATES = {"REJECTED", "SUPERSEDED"}
_BACKFIRE_CLASSES = {
    "EXECUTION_FAILURE_REQUIRES_UPSTREAM_ACTION",
    "MISSING_INFORMATION_FROM_SOURCE",
    "NEW_REQUIREMENT_FROM_SOURCE",
    "LINKED_TASK_INPUT_REQUIRED",
}
_TASK_MODES = {"TOP_LEVEL_TASK", "SUBAGENT"}
_SCOPE_CLASSES = {"READ_ONLY", "GOVERNED_READ_WRITE"}
_FORBIDDEN_CANON_ACTIONS = {
    "SOURCE_WRITE",
    "GIT_WRITE",
    "CREATE_CANDIDATE",
    "DECIDE_PROJECT_HIL",
    "DECIDE_LEARNING_HIL",
    "MOVE_POINTER",
    "FUSE",
    "MAIN_PROMOTE",
    "INSTALL",
    "DEPLOY",
}
_FORBIDDEN_SUBAGENT_TOOLS = {
    "hil_decide",
    "pv_fuse",
    "pv_refresh",
    "remote_git_push",
    "state_travel_prepare",
    "state_travel_resume",
}
_AUTHORITY_EFFECTS_NONE = {
    "project_truth": "NONE",
    "canon_input": "NONE",
    "agent_learning": "NONE",
    "chat_lineage": "NONE",
    "host_entry_continuity": "NONE",
}
_CANON_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas" / "canon"
_CANON_RECEIPT_SCHEMAS = {
    CANON_DECISION_RECEIPT_SCHEMA,
    CANON_DISPATCH_RECEIPT_SCHEMA,
    CANON_DISPATCH_RECEIPT_SCHEMA_V2,
    CODEX_HOST_CREATE_RECEIPT_SCHEMA,
    CANON_RESTORE_RECEIPT_SCHEMA,
}


class CanonTaskDispatcher(Protocol):
    """Provider-neutral host seam for an explicitly authorized task launch."""

    host_kind: str
    capability: str

    def create_linked_task(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Create one task and return its exact UUID/deep-link binding."""


@dataclass(frozen=True)
class CodexHostDispatcher:
    """Exact injectable Codex host seam; unavailable without a host operation."""

    create_operation: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    host_kind: str = field(default="CODEX", init=False)
    capability: str = field(default=CODEX_HOST_CREATE_CAPABILITY, init=False)

    def create_linked_task(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        require(
            request.get("schema")
            == "evidence-lane.codex-host-linked-task-create-request.v1"
            and request.get("host_kind") == self.host_kind
            and request.get("operation") == "CREATE_LINKED_TASK"
            and request.get("capability") == self.capability,
            "CANON_CODEX_HOST_REQUEST_INVALID",
            "The Codex host adapter received an unsupported task-create request.",
            status="MISMATCH",
        )
        response = self.create_operation(dict(request))
        require(
            isinstance(response, Mapping),
            "CANON_CODEX_HOST_RECEIPT_REQUIRED",
            "The Codex host operation did not return one receipt object.",
            status="FAIL",
        )
        receipt = dict(response)
        validate_canon_receipt(receipt)
        require(
            receipt.get("schema") == CODEX_HOST_CREATE_RECEIPT_SCHEMA
            and receipt.get("host_kind") == self.host_kind
            and receipt.get("operation") == "CREATE_LINKED_TASK"
            and receipt.get("capability") == self.capability
            and receipt.get("idempotency_key") == request.get("idempotency_key")
            and receipt.get("request_sha256") == request.get("request_sha256")
            and receipt.get("created_once") is True
            and isinstance(receipt.get("destination"), Mapping),
            "CANON_CODEX_HOST_RECEIPT_BINDING_MISMATCH",
            "The Codex host receipt does not bind the exact idempotent request.",
            status="MISMATCH",
        )
        destination = _endpoint(
            cast(Mapping[str, Any], receipt["destination"]),
            field="host_receipt.destination",
        )
        return {
            **destination,
            "created_once": True,
            "host_creation_receipt": receipt,
        }


def _exact_text(value: Any, *, field: str) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact),
        "CANON_FIELD_REQUIRED",
        "A required Canon field is empty.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        bool(_SHA256_RE.fullmatch(exact)),
        "CANON_SHA256_INVALID",
        "A Canon identity field is not one exact SHA-256.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _timestamp(value: Any, *, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    exact = _exact_text(value, field=field)
    try:
        parsed = datetime.fromisoformat(exact)
    except ValueError as exc:
        require(
            False,
            "CANON_TIMESTAMP_INVALID",
            "A Canon timestamp is not valid ISO-8601.",
            status="MISMATCH",
            field=field,
        )
        raise AssertionError("unreachable") from exc
    require(
        parsed.tzinfo is not None,
        "CANON_TIMESTAMP_TIMEZONE_REQUIRED",
        "A Canon timestamp requires an explicit timezone.",
        status="MISMATCH",
        field=field,
    )
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _project_root(project_root: str | Path, *, project_id: str) -> Path:
    root = Path(project_root).resolve()
    require(
        root.name == project_id,
        "CANON_CROSS_PROJECT_AUTHORITY_DENIED",
        "The Canon authority root does not match the exact project identity.",
        status="BLOCKED",
        project_id=project_id,
        root=str(root),
    )
    return root


def _canon_root(root: Path) -> Path:
    return root / "canon"


def _ledger_path(root: Path) -> Path:
    return _canon_root(root) / "canon-input.sqlite"


def _load_canon_schema_contract() -> dict[str, Any]:
    manifest_path = _CANON_SCHEMA_ROOT / "canon-schema-manifest.v1.json"
    require(
        manifest_path.is_file(),
        "CANON_SCHEMA_MANIFEST_REQUIRED",
        "The first-class Canon schema manifest is missing.",
        status="MISMATCH",
        path=str(manifest_path),
    )
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = cast(dict[str, Any], json.loads(manifest_bytes))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        require(
            False,
            "CANON_SCHEMA_MANIFEST_INVALID",
            "The first-class Canon schema manifest is unreadable.",
            status="MISMATCH",
            path=str(manifest_path),
        )
        raise AssertionError("unreachable") from exc
    ledger = manifest.get("ledger")
    receipts = manifest.get("receipts")
    require(
        manifest.get("schema") == CANON_SCHEMA_MANIFEST_SCHEMA
        and manifest.get("schema_family") == "CANON"
        and manifest.get("manifest_version") == 1
        and isinstance(ledger, Mapping)
        and isinstance(receipts, Mapping),
        "CANON_SCHEMA_MANIFEST_INVALID",
        "The Canon schema manifest identity or sections are invalid.",
        status="MISMATCH",
    )
    ledger = cast(Mapping[str, Any], ledger)
    receipts = cast(Mapping[str, Any], receipts)
    ledger_asset_name = str(ledger.get("asset") or "")
    receipt_asset_name = str(receipts.get("asset") or "")
    ledger_path = (_CANON_SCHEMA_ROOT / ledger_asset_name).resolve()
    receipt_path = (_CANON_SCHEMA_ROOT / receipt_asset_name).resolve()
    require(
        Path(ledger_asset_name).name == ledger_asset_name
        and Path(receipt_asset_name).name == receipt_asset_name
        and ledger_path.parent == _CANON_SCHEMA_ROOT.resolve()
        and receipt_path.parent == _CANON_SCHEMA_ROOT.resolve()
        and ledger_path.is_file()
        and receipt_path.is_file(),
        "CANON_SCHEMA_ASSET_REQUIRED",
        "A Canon schema asset is absent or outside the schema authority root.",
        status="MISMATCH",
    )
    ledger_bytes = ledger_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()
    ledger_sha256 = sha256_bytes(ledger_bytes)
    receipt_sha256 = sha256_bytes(receipt_bytes)
    require(
        ledger.get("schema_id") == CANON_LEDGER_SCHEMA
        and ledger.get("sqlite_user_version") == CANON_LEDGER_SCHEMA_VERSION
        and ledger_sha256 == str(ledger.get("asset_sha256") or "").upper()
        and receipts.get("schema_id") == CANON_RECEIPT_REGISTRY_SCHEMA
        and receipts.get("registry_version") == 1
        and receipt_sha256 == str(receipts.get("asset_sha256") or "").upper(),
        "CANON_SCHEMA_ASSET_HASH_MISMATCH",
        "Canon schema asset bytes do not match the sealed manifest.",
        status="MISMATCH",
    )
    try:
        ledger_sql = ledger_bytes.decode("utf-8")
        receipt_schema = cast(dict[str, Any], json.loads(receipt_bytes))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        require(
            False,
            "CANON_SCHEMA_ASSET_INVALID",
            "A Canon schema asset is not valid UTF-8 SQL or JSON.",
            status="MISMATCH",
        )
        raise AssertionError("unreachable") from exc
    definitions = receipt_schema.get("$defs")
    require(
        receipt_schema.get("$schema")
        == "https://json-schema.org/draft/2020-12/schema"
        and receipt_schema.get("x-evidence-lane-version") == 1
        and receipt_schema.get("x-evidence-lane-hash-field") == "receipt_sha256"
        and isinstance(definitions, Mapping),
        "CANON_RECEIPT_SCHEMA_INVALID",
        "The Canon receipt registry is not the supported schema contract.",
        status="MISMATCH",
    )
    defined_receipt_schemas = {
        str((definition.get("properties") or {}).get("schema", {}).get("const"))
        for definition in cast(Mapping[str, Any], definitions).values()
        if isinstance(definition, Mapping)
        and isinstance(definition.get("properties"), Mapping)
        and isinstance(
            cast(Mapping[str, Any], definition.get("properties")).get("schema"),
            Mapping,
        )
    }
    supported_schema_ids = {
        str(value) for value in receipts.get("supported_schema_ids") or []
    }
    migration_policy = ledger.get("migration_policy")
    transitions = (
        migration_policy.get("transitions")
        if isinstance(migration_policy, Mapping)
        else None
    )
    require(
        defined_receipt_schemas == _CANON_RECEIPT_SCHEMAS
        and supported_schema_ids == _CANON_RECEIPT_SCHEMAS
        and isinstance(migration_policy, Mapping)
        and migration_policy.get("accepted_from_versions") == [0, 1]
        and transitions
        == [
            {
                "from_version": 0,
                "to_version": 1,
                "mode": "ADDITIVE_IDEMPOTENT_CREATE_ONLY",
                "data_rewrite": False,
                "drop_or_rename": False,
            }
        ]
        and migration_policy.get("newer_version")
        == "FAIL_CLOSED_RUNTIME_TOO_OLD"
        and migration_policy.get("rollback")
        == "UNSUPPORTED_NO_DESTRUCTIVE_REWRITE",
        "CANON_SCHEMA_MIGRATION_POLICY_INVALID",
        "Canon schema versions or migration rules are incomplete or mutable.",
        status="MISMATCH",
    )
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "ledger_sql": ledger_sql,
        "ledger_path": ledger_path,
        "ledger_sha256": ledger_sha256,
        "receipt_schema": receipt_schema,
        "receipt_path": receipt_path,
        "receipt_sha256": receipt_sha256,
    }


def _canon_sqlite_schema_signature(connection: sqlite3.Connection) -> str:
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
    return sha256_bytes(canonical_json_bytes(rows))


def _expected_canon_sqlite_schema_signature(ledger_sql: str) -> str:
    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    try:
        expected.execute("PRAGMA foreign_keys=ON")
        expected.executescript(ledger_sql)
        return _canon_sqlite_schema_signature(expected)
    finally:
        expected.close()


def _apply_canon_ledger_schema(
    connection: sqlite3.Connection,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    require(
        current_version in {0, CANON_LEDGER_SCHEMA_VERSION},
        "CANON_LEDGER_VERSION_UNSUPPORTED",
        "The Canon ledger requires one explicit supported migration path.",
        status="MISMATCH",
        current_version=current_version,
        supported_versions=[0, CANON_LEDGER_SCHEMA_VERSION],
    )
    ledger_sql = str(contract["ledger_sql"])
    expected_signature = _expected_canon_sqlite_schema_signature(ledger_sql)
    migration_mode = "ADDITIVE_IDEMPOTENT_CREATE_ONLY"
    if current_version == CANON_LEDGER_SCHEMA_VERSION:
        actual_signature = _canon_sqlite_schema_signature(connection)
        require(
            actual_signature == expected_signature,
            "CANON_LEDGER_BUILDER_SCHEMA_MISMATCH",
            "The live Canon ledger schema differs from the sealed DDL asset.",
            status="MISMATCH",
            expected_schema_signature_sha256=expected_signature,
            actual_schema_signature_sha256=actual_signature,
        )
    else:
        require(
            not connection.in_transaction,
            "CANON_LEDGER_MIGRATION_TRANSACTION_INVALID",
            "The Canon ledger migration requires an unused transaction boundary.",
            status="MISMATCH",
        )
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + ledger_sql)
            actual_signature = _canon_sqlite_schema_signature(connection)
            require(
                actual_signature == expected_signature,
                "CANON_LEDGER_BUILDER_SCHEMA_MISMATCH",
                "The additive Canon migration did not produce the sealed schema.",
                status="MISMATCH",
                expected_schema_signature_sha256=expected_signature,
                actual_schema_signature_sha256=actual_signature,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO canon_schema_migration(
                    schema_name,from_version,to_version,asset_sha256,
                    migration_mode,applied_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    CANON_LEDGER_SCHEMA,
                    0,
                    CANON_LEDGER_SCHEMA_VERSION,
                    contract["ledger_sha256"],
                    migration_mode,
                    datetime.now(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                ),
            )
            connection.execute(f"PRAGMA user_version={CANON_LEDGER_SCHEMA_VERSION}")
        except sqlite3.DatabaseError as exc:
            if connection.in_transaction:
                connection.rollback()
            require(
                False,
                "CANON_LEDGER_BUILDER_SCHEMA_MISMATCH",
                "The additive Canon migration could not produce the sealed schema.",
                status="MISMATCH",
                sqlite_error=type(exc).__name__,
            )
            raise AssertionError("unreachable") from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    try:
        migration = connection.execute(
            """
            SELECT from_version,to_version,asset_sha256,migration_mode
            FROM canon_schema_migration
            WHERE schema_name=? AND to_version=?
            """,
            (CANON_LEDGER_SCHEMA, CANON_LEDGER_SCHEMA_VERSION),
        ).fetchone()
        require(
            migration is not None
            and int(migration["from_version"]) == 0
            and int(migration["to_version"]) == CANON_LEDGER_SCHEMA_VERSION
            and str(migration["asset_sha256"]) == contract["ledger_sha256"]
            and str(migration["migration_mode"]) == migration_mode,
            "CANON_LEDGER_MIGRATION_RECEIPT_MISMATCH",
            "The Canon ledger lacks its exact additive migration receipt.",
            status="MISMATCH",
        )
        if current_version == 0:
            connection.commit()
    except Exception:
        if current_version == 0 and connection.in_transaction:
            connection.rollback()
        raise
    return {
        "schema": CANON_LEDGER_SCHEMA,
        "version_before": current_version,
        "version_after": CANON_LEDGER_SCHEMA_VERSION,
        "migration_mode": migration_mode,
        "ledger_asset_sha256": contract["ledger_sha256"],
        "schema_signature_sha256": actual_signature,
    }


def _connect(root: Path) -> sqlite3.Connection:
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    try:
        _apply_canon_ledger_schema(connection, _load_canon_schema_contract())
    except Exception:
        connection.close()
        raise
    return connection


def _json_schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "array": isinstance(value, list),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "object": isinstance(value, Mapping),
        "string": isinstance(value, str),
    }.get(expected, False)


def _validate_canon_schema_value(
    value: Any,
    node: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    *,
    field: str,
) -> None:
    reference = node.get("$ref")
    if reference is not None:
        parts = str(reference).split("/")
        require(
            len(parts) == 3
            and parts[:2] == ["#", "$defs"]
            and isinstance(root_schema.get("$defs"), Mapping)
            and isinstance(
                cast(Mapping[str, Any], root_schema["$defs"]).get(parts[2]),
                Mapping,
            ),
            "CANON_RECEIPT_SCHEMA_REFERENCE_INVALID",
            "A Canon receipt schema reference is unsupported.",
            status="MISMATCH",
            field=field,
            reference=reference,
        )
        _validate_canon_schema_value(
            value,
            cast(
                Mapping[str, Any],
                cast(Mapping[str, Any], root_schema["$defs"])[parts[2]],
            ),
            root_schema,
            field=field,
        )
        return
    declared_type = node.get("type")
    if declared_type is not None:
        allowed_types = (
            [str(item) for item in declared_type]
            if isinstance(declared_type, list)
            else [str(declared_type)]
        )
        require(
            any(_json_schema_type_matches(value, item) for item in allowed_types),
            "CANON_RECEIPT_FIELD_TYPE_INVALID",
            "A Canon receipt field violates its first-class schema type.",
            status="MISMATCH",
            field=field,
            allowed_types=allowed_types,
        )
    if "const" in node:
        require(
            value == node["const"],
            "CANON_RECEIPT_FIELD_CONST_INVALID",
            "A Canon receipt field violates its immutable schema constant.",
            status="MISMATCH",
            field=field,
        )
    if "enum" in node:
        require(
            value in node["enum"],
            "CANON_RECEIPT_FIELD_ENUM_INVALID",
            "A Canon receipt field is outside its schema enumeration.",
            status="MISMATCH",
            field=field,
        )
    if isinstance(value, str):
        require(
            len(value) >= int(node.get("minLength") or 0),
            "CANON_RECEIPT_FIELD_LENGTH_INVALID",
            "A Canon receipt text field is shorter than its schema contract.",
            status="MISMATCH",
            field=field,
        )
        if node.get("pattern") is not None:
            require(
                re.fullmatch(str(node["pattern"]), value) is not None,
                "CANON_RECEIPT_FIELD_PATTERN_INVALID",
                "A Canon receipt field does not match its schema pattern.",
                status="MISMATCH",
                field=field,
            )
        if node.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                require(
                    False,
                    "CANON_RECEIPT_TIMESTAMP_INVALID",
                    "A Canon receipt timestamp is not ISO-8601.",
                    status="MISMATCH",
                    field=field,
                )
                raise AssertionError("unreachable") from exc
            require(
                parsed.tzinfo is not None,
                "CANON_RECEIPT_TIMESTAMP_TIMEZONE_REQUIRED",
                "A Canon receipt timestamp requires an explicit timezone.",
                status="MISMATCH",
                field=field,
            )
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and node.get("minimum") is not None
    ):
        require(
            value >= node["minimum"],
            "CANON_RECEIPT_FIELD_MINIMUM_INVALID",
            "A Canon receipt number is below its schema minimum.",
            status="MISMATCH",
            field=field,
        )
    if isinstance(value, Mapping):
        properties = node.get("properties")
        required_fields = [str(item) for item in node.get("required") or []]
        require(
            all(item in value for item in required_fields),
            "CANON_RECEIPT_REQUIRED_FIELD_MISSING",
            "A Canon receipt is missing one schema-required field.",
            status="MISMATCH",
            field=field,
            required_fields=required_fields,
        )
        if properties is None:
            return
        require(
            isinstance(properties, Mapping),
            "CANON_RECEIPT_SCHEMA_PROPERTIES_INVALID",
            "A Canon receipt object schema has invalid properties.",
            status="MISMATCH",
            field=field,
        )
        property_map = cast(Mapping[str, Any], properties)
        if node.get("additionalProperties") is False:
            require(
                set(value) <= set(property_map),
                "CANON_RECEIPT_ADDITIONAL_FIELD_INVALID",
                "A Canon receipt contains a field outside its immutable schema.",
                status="MISMATCH",
                field=field,
                extra_fields=sorted(set(value) - set(property_map)),
            )
        for name, child in property_map.items():
            if name in value and isinstance(child, Mapping):
                _validate_canon_schema_value(
                    value[name],
                    cast(Mapping[str, Any], child),
                    root_schema,
                    field=f"{field}.{name}",
                )


def validate_canon_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one exact self-sealed receipt against the schema asset."""

    contract = _load_canon_schema_contract()
    receipt_schema = cast(Mapping[str, Any], contract["receipt_schema"])
    definitions = cast(Mapping[str, Any], receipt_schema["$defs"])
    exact = dict(value)
    schema_id = str(exact.get("schema") or "")
    candidates = [
        definition
        for definition in definitions.values()
        if isinstance(definition, Mapping)
        and isinstance(definition.get("properties"), Mapping)
        and isinstance(
            cast(Mapping[str, Any], definition["properties"]).get("schema"),
            Mapping,
        )
        and cast(
            Mapping[str, Any],
            cast(Mapping[str, Any], definition["properties"])["schema"],
        ).get("const")
        == schema_id
    ]
    require(
        len(candidates) == 1 and schema_id in _CANON_RECEIPT_SCHEMAS,
        "CANON_RECEIPT_SCHEMA_UNSUPPORTED",
        "The Canon receipt schema is not in the first-class registry.",
        status="MISMATCH",
        schema_id=schema_id or None,
    )
    _validate_canon_schema_value(
        exact,
        cast(Mapping[str, Any], candidates[0]),
        receipt_schema,
        field="receipt",
    )
    if schema_id == CANON_DISPATCH_RECEIPT_SCHEMA_V2:
        nested = exact.get("host_creation_receipt")
        require(
            isinstance(nested, Mapping),
            "CANON_CODEX_HOST_RECEIPT_REQUIRED",
            "A v2 Canon dispatch receipt requires its exact Codex host receipt.",
            status="MISMATCH",
        )
        validate_canon_receipt(cast(Mapping[str, Any], nested))
        require(
            exact.get("destination") == nested.get("destination")
            and exact.get("request_sha256") == nested.get("request_sha256")
            and exact.get("dispatch_id") == nested.get("idempotency_key"),
            "CANON_CODEX_HOST_RECEIPT_BINDING_MISMATCH",
            "The Canon dispatch receipt and Codex host receipt bind different work.",
            status="MISMATCH",
        )
    claimed_sha256 = _sha256(exact.get("receipt_sha256"), field="receipt_sha256")
    body = dict(exact)
    body.pop("receipt_sha256", None)
    require(
        claimed_sha256 == sha256_bytes(canonical_json_bytes(body)),
        "CANON_RECEIPT_SELF_SEAL_MISMATCH",
        "The Canon receipt does not match its self-sealed body bytes.",
        status="MISMATCH",
        schema_id=schema_id,
    )
    proof_body = {
        "schema": "evidence-lane.canon-receipt-schema-validation.v1",
        "status": "PASS",
        "receipt_schema": schema_id,
        "receipt_sha256": claimed_sha256,
        "registry_asset_sha256": contract["receipt_sha256"],
        "manifest_sha256": contract["manifest_sha256"],
    }
    return {
        **proof_body,
        "validation_receipt_sha256": sha256_bytes(canonical_json_bytes(proof_body)),
    }


def inspect_canon_schema_contract() -> dict[str, Any]:
    """Return the bounded first-class ledger/receipt schema authority."""

    contract = _load_canon_schema_contract()
    manifest = cast(Mapping[str, Any], contract["manifest"])
    ledger = cast(Mapping[str, Any], manifest["ledger"])
    receipts = cast(Mapping[str, Any], manifest["receipts"])
    body = {
        "schema": "evidence-lane.canon-schema-contract-receipt.v1",
        "status": "PASS",
        "manifest_schema": manifest["schema"],
        "manifest_sha256": contract["manifest_sha256"],
        "ledger_schema": ledger["schema_id"],
        "ledger_version": ledger["sqlite_user_version"],
        "ledger_asset_sha256": contract["ledger_sha256"],
        "ledger_schema_signature_sha256": (
            _expected_canon_sqlite_schema_signature(str(contract["ledger_sql"]))
        ),
        "ledger_migration_policy": ledger["migration_policy"],
        "receipt_registry_schema": receipts["schema_id"],
        "receipt_registry_version": receipts["registry_version"],
        "receipt_asset_sha256": contract["receipt_sha256"],
        "supported_receipt_schemas": sorted(_CANON_RECEIPT_SCHEMAS),
        "receipt_migration_policy": receipts["migration_policy"],
        "builder_executes_exact_asset_bytes": True,
        "unknown_version_behavior": "FAIL_CLOSED",
    }
    return {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _immutable_json(path: Path, value: dict[str, Any]) -> str:
    encoded = canonical_json_bytes(value)
    if path.exists():
        require(
            path.read_bytes() == encoded,
            "CANON_IMMUTABLE_ARTIFACT_CONFLICT",
            "A Canon immutable locator already contains different bytes.",
            status="MISMATCH",
            path=str(path),
        )
        return "SEALED_IDEMPOTENT_REUSE"
    atomic_write_json(path, value)
    return "SEALED"


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    require(path.is_file(), code, "A required Canon artifact is missing.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        require(
            False,
            code,
            "A required Canon artifact is invalid JSON.",
            status="MISMATCH",
            path=str(path),
            error_type=type(exc).__name__,
        )
        raise AssertionError("unreachable") from exc
    require(isinstance(value, dict), code, "Canon JSON must be one object.")
    return cast(dict[str, Any], value)


def _preflight_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    if not path.exists():
        return
    existing = _load_json(path, code="CANON_IMMUTABLE_ARTIFACT_INVALID")
    require(
        existing == dict(value),
        "CANON_IMMUTABLE_ARTIFACT_CONFLICT",
        "An immutable Canon artifact path already maps to other bytes.",
        status="MISMATCH",
        path=str(path),
    )


def _file_identity(path: Path) -> str:
    return sha256_bytes(path.read_bytes()) if path.is_file() else "ABSENT"


def _authority_snapshot(root: Path) -> dict[str, str]:
    return {
        "project_truth_pointer_sha256": _file_identity(root / "active_pointer.json"),
        "learning_pointer_sha256": _file_identity(
            root / "learning" / "active_pointer.json"
        ),
    }


def _require_authorities_unchanged(
    root: Path, before: dict[str, str], *, operation: str
) -> dict[str, str]:
    after = _authority_snapshot(root)
    require(
        after == before,
        "CANON_CROSS_AUTHORITY_MUTATION_BLOCKED",
        "A Canon operation changed Project Truth or Agent Learning authority.",
        status="FAIL",
        operation=operation,
        before=before,
        after=after,
    )
    return after


def _endpoint(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    endpoint = {
        "project_id": _exact_text(value.get("project_id"), field=f"{field}.project_id"),
        "task_uuid": _exact_text(value.get("task_uuid"), field=f"{field}.task_uuid"),
        "task_deep_link": _exact_text(
            value.get("task_deep_link"), field=f"{field}.task_deep_link"
        ),
        "lane_id": _exact_text(value.get("lane_id"), field=f"{field}.lane_id"),
        "delta_id": _exact_text(value.get("delta_id"), field=f"{field}.delta_id"),
        "session_id": str(value.get("session_id") or "").strip() or None,
    }
    return endpoint


def _node_key(endpoint: Mapping[str, Any]) -> str:
    return f"{endpoint['project_id']}:{endpoint['task_uuid']}"


def _safe_actions(values: Any, *, field: str) -> list[str]:
    require(
        isinstance(values, list),
        "CANON_ACTIONS_INVALID",
        "Canon permitted actions must be one bounded list.",
        status="BLOCKED",
        field=field,
    )
    actions = sorted({_exact_text(item, field=field).upper() for item in values})
    forbidden = sorted(set(actions) & _FORBIDDEN_CANON_ACTIONS)
    require(
        not forbidden,
        "CANON_AUTHORITY_ESCALATION_BLOCKED",
        "Canon cannot grant source-write, HIL, pointer, Fuse, install, or deploy authority.",
        status="BLOCKED",
        forbidden=forbidden,
    )
    return actions


def _evidence_refs(values: Any) -> list[dict[str, str]]:
    require(
        isinstance(values, list) and bool(values),
        "CANON_EVIDENCE_REQUIRED",
        "A Canon envelope requires at least one exact evidence reference.",
        status="BLOCKED",
    )
    result: list[dict[str, str]] = []
    for index, value in enumerate(values):
        require(
            isinstance(value, Mapping),
            "CANON_EVIDENCE_INVALID",
            "Every Canon evidence reference must be one object.",
            status="MISMATCH",
            index=index,
        )
        result.append(
            {
                "ref": _exact_text(value.get("ref"), field=f"evidence[{index}].ref"),
                "sha256": _sha256(
                    value.get("sha256"), field=f"evidence[{index}].sha256"
                ),
            }
        )
    return result


def _source_pointer(value: Mapping[str, Any]) -> dict[str, Any]:
    project_id = _exact_text(value.get("project_id"), field="source_pointer.project_id")
    pv_ref = _exact_text(value.get("pv_ref"), field="source_pointer.pv_ref")
    require(
        bool(_PV_RE.fullmatch(pv_ref)),
        "CANON_SOURCE_PV_INVALID",
        "The Canon source pointer is not one accepted PV reference.",
        status="MISMATCH",
    )
    generation = int(value.get("generation") or 0)
    require(
        generation > 0,
        "CANON_SOURCE_GENERATION_INVALID",
        "The Canon source pointer generation must be positive.",
        status="MISMATCH",
    )
    return {
        "project_id": project_id,
        "pv_ref": pv_ref,
        "generation": generation,
        "manifest_sha256": _sha256(
            value.get("manifest_sha256"), field="source_pointer.manifest_sha256"
        ),
        "package_sha256": _sha256(
            value.get("package_sha256"), field="source_pointer.package_sha256"
        ),
    }


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256_bytes(
        canonical_json_bytes({key: item for key, item in value.items() if key != field})
    )


def _contains_secret_material(value: Any) -> bool:
    if contains_secret(value):
        return True
    if isinstance(value, Mapping):
        return any(
            bool(_SECRET_KEY_RE.search(str(key))) or _contains_secret_material(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_material(item) for item in value)
    return False


def _normalise_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    require(
        value.get("schema") == CANON_EXPECTED_CONTRACT_SCHEMA,
        "CANON_CONTRACT_SCHEMA_INVALID",
        "The expected Canon contract schema is unsupported.",
        status="MISMATCH",
    )
    exact = dict(value)
    exact["contract_id"] = _exact_text(exact.get("contract_id"), field="contract_id")
    version = int(exact.get("contract_version") or 0)
    require(
        version > 0,
        "CANON_CONTRACT_VERSION_INVALID",
        "An expected Canon contract version must be positive.",
        status="MISMATCH",
    )
    exact["contract_version"] = version
    require(
        isinstance(exact.get("active"), bool),
        "CANON_CONTRACT_ACTIVE_INVALID",
        "An expected Canon contract requires one explicit active boolean.",
        status="MISMATCH",
    )
    destination = _endpoint(
        cast(Mapping[str, Any], exact.get("destination") or {}),
        field="destination",
    )
    source_allowlist = exact.get("source_allowlist")
    require(
        isinstance(source_allowlist, list) and bool(source_allowlist),
        "CANON_CONTRACT_SOURCE_ALLOWLIST_REQUIRED",
        "An expected Canon contract requires at least one exact source binding.",
        status="BLOCKED",
    )
    source_allowlist = cast(list[Any], source_allowlist)
    allowed_sources = [
        _endpoint(cast(Mapping[str, Any], item), field="source_allowlist")
        for item in source_allowlist
    ]
    source_pointers = exact.get("accepted_source_pointers")
    require(
        isinstance(source_pointers, list) and bool(source_pointers),
        "CANON_CONTRACT_SOURCE_POINTER_REQUIRED",
        "An expected Canon contract requires accepted-source pointer boundaries.",
        status="BLOCKED",
    )
    source_pointers = cast(list[Any], source_pointers)
    exact["destination"] = destination
    exact["source_allowlist"] = sorted(
        allowed_sources, key=lambda item: canonical_json_bytes(item)
    )
    accepted_source_pointers = [
        _source_pointer(cast(Mapping[str, Any], item)) for item in source_pointers
    ]
    exact["accepted_source_pointers"] = sorted(
        accepted_source_pointers, key=lambda item: canonical_json_bytes(item)
    )
    exact["schema_sha256"] = _sha256(
        exact.get("schema_sha256"), field="schema_sha256"
    )
    exact["schema_id"] = _exact_text(exact.get("schema_id"), field="schema_id")
    exact["schema_version"] = _exact_text(
        exact.get("schema_version"), field="schema_version"
    )
    canon_types = exact.get("canon_types")
    require(
        isinstance(canon_types, list) and bool(canon_types),
        "CANON_CONTRACT_TYPES_REQUIRED",
        "An expected Canon contract requires at least one Canon type.",
        status="BLOCKED",
    )
    canon_types = cast(list[Any], canon_types)
    exact["canon_types"] = sorted(
        {_exact_text(item, field="canon_types").upper() for item in canon_types}
    )
    authorities = exact.get("authority_requested")
    require(
        isinstance(authorities, list) and bool(authorities),
        "CANON_CONTRACT_AUTHORITIES_REQUIRED",
        "An expected Canon contract requires at least one authority class.",
        status="BLOCKED",
    )
    authorities = cast(list[Any], authorities)
    exact_authorities = {
        _exact_text(item, field="authority_requested").upper()
        for item in authorities
    }
    require(
        exact_authorities <= _AUTHORITIES,
        "CANON_CONTRACT_AUTHORITY_INVALID",
        "An expected Canon contract contains an unsupported authority class.",
        status="BLOCKED",
    )
    exact["authority_requested"] = sorted(exact_authorities)
    exact["permitted_actions"] = _safe_actions(
        exact.get("permitted_actions") or [], field="permitted_actions"
    )
    payload_keys = exact.get("permitted_payload_keys")
    require(
        isinstance(payload_keys, list),
        "CANON_CONTRACT_PAYLOAD_KEYS_INVALID",
        "The expected Canon payload-key policy must be one list.",
        status="MISMATCH",
    )
    payload_keys = cast(list[Any], payload_keys)
    exact["permitted_payload_keys"] = sorted(
        {_exact_text(item, field="permitted_payload_keys") for item in payload_keys}
    )
    exact["expires_at"] = _timestamp(
        exact.get("expires_at"), field="expires_at", nullable=True
    )
    if exact.get("expected_return_contract_sha256") is not None:
        exact["expected_return_contract_sha256"] = _sha256(
            exact.get("expected_return_contract_sha256"),
            field="expected_return_contract_sha256",
        )
    require(
        _exact_text(
            exact.get("independent_hil_owner_task_uuid"),
            field="independent_hil_owner_task_uuid",
        )
        == destination["task_uuid"],
        "CANON_HIL_OWNER_MUST_BE_RECEIVER",
        "The receiving top-level task must own its Canon Input HIL.",
        status="BLOCKED",
    )
    return exact


def _validate_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    exact = _normalise_contract(value)
    claimed = _sha256(exact.get("contract_sha256"), field="contract_sha256")
    require(
        claimed == _hash_without(exact, "contract_sha256"),
        "CANON_CONTRACT_HASH_MISMATCH",
        "The expected Canon contract failed its immutable hash check.",
        status="MISMATCH",
    )
    return exact


def register_expected_canon_contract(
    project_root: str | Path,
    *,
    project_id: str,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Register one immutable, versioned, receiver-owned expected contract."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    raw = dict(contract)
    raw.setdefault("schema", CANON_EXPECTED_CONTRACT_SCHEMA)
    raw.setdefault("active", True)
    raw.pop("contract_sha256", None)
    raw = _normalise_contract(raw)
    raw["contract_sha256"] = sha256_bytes(canonical_json_bytes(raw))
    exact = _validate_contract(raw)
    require(
        exact["destination"]["project_id"] == project_id,
        "CANON_CONTRACT_DESTINATION_PROJECT_MISMATCH",
        "An expected contract must be stored by its exact destination project.",
        status="BLOCKED",
    )
    path = (
        _canon_root(root)
        / "contracts"
        / f"{exact['contract_sha256'].lower()}.json"
    )
    _preflight_immutable_json(path, exact)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        version_row = connection.execute(
            """
            SELECT contract_json FROM canon_contract
            WHERE contract_id=? AND contract_version=? AND destination_task_uuid=?
            """,
            (
                exact["contract_id"],
                int(exact["contract_version"]),
                exact["destination"]["task_uuid"],
            ),
        ).fetchone()
        require(
            version_row is None
            or json.loads(str(version_row["contract_json"])) == exact,
            "CANON_CONTRACT_VERSION_IMMUTABILITY_CONFLICT",
            "The expected Canon contract version already maps to other bytes.",
            status="MISMATCH",
        )
        existing = connection.execute(
            "SELECT contract_json FROM canon_contract WHERE contract_sha256=?",
            (exact["contract_sha256"],),
        ).fetchone()
        if existing is not None:
            require(
                json.loads(str(existing["contract_json"])) == exact,
                "CANON_CONTRACT_IMMUTABILITY_CONFLICT",
                "The expected contract identity already maps to other bytes.",
                status="MISMATCH",
            )
        else:
            connection.execute(
                """
                INSERT INTO canon_contract(
                    contract_sha256,contract_id,contract_version,
                    destination_project_id,destination_task_uuid,active,contract_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    exact["contract_sha256"],
                    exact["contract_id"],
                    int(exact["contract_version"]),
                    project_id,
                    exact["destination"]["task_uuid"],
                    1 if exact.get("active") else 0,
                    canonical_json_bytes(exact).decode("utf-8"),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    state = _immutable_json(path, exact)
    after = _require_authorities_unchanged(root, before, operation="register_contract")
    return {
        "status": "PASS",
        "state": state,
        "contract": exact,
        "contract_path": str(path),
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": "CONTRACT_REGISTERED",
        },
    }


def _validate_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    require(
        value.get("schema") == CANON_ENVELOPE_SCHEMA,
        "CANON_ENVELOPE_SCHEMA_INVALID",
        "The Canon envelope schema is unsupported.",
        status="MISMATCH",
    )
    exact = dict(value)
    claimed = _sha256(exact.get("canon_sha256"), field="canon_sha256")
    require(
        claimed == _hash_without(exact, "canon_sha256"),
        "CANON_ENVELOPE_HASH_MISMATCH",
        "The Canon envelope failed its immutable hash check.",
        status="MISMATCH",
    )
    exact["source"] = _endpoint(
        cast(Mapping[str, Any], exact.get("source") or {}), field="source"
    )
    exact["destination"] = _endpoint(
        cast(Mapping[str, Any], exact.get("destination") or {}),
        field="destination",
    )
    direction = _exact_text(exact.get("direction"), field="direction").upper()
    require(
        direction in _DIRECTIONS,
        "CANON_DIRECTION_INVALID",
        "A Canon route direction is unsupported.",
        status="BLOCKED",
    )
    authority = _exact_text(
        exact.get("authority_requested"), field="authority_requested"
    ).upper()
    require(
        authority in _AUTHORITIES,
        "CANON_AUTHORITY_REQUEST_INVALID",
        "The requested Canon authority class is unsupported.",
        status="BLOCKED",
    )
    exact["direction"] = direction
    exact["authority_requested"] = authority
    exact["source_pointer"] = _source_pointer(
        cast(Mapping[str, Any], exact.get("source_pointer") or {})
    )
    require(
        exact["source_pointer"]["project_id"] == exact["source"]["project_id"],
        "CANON_SOURCE_POINTER_PROJECT_MISMATCH",
        "The accepted-source pointer and source task project differ.",
        status="MISMATCH",
    )
    exact["schema_sha256"] = _sha256(
        exact.get("schema_sha256"), field="schema_sha256"
    )
    exact["destination_contract_sha256"] = _sha256(
        exact.get("destination_contract_sha256"),
        field="destination_contract_sha256",
    )
    if exact.get("expected_return_contract_sha256") is not None:
        exact["expected_return_contract_sha256"] = _sha256(
            exact.get("expected_return_contract_sha256"),
            field="expected_return_contract_sha256",
        )
    exact["evidence_refs"] = _evidence_refs(exact.get("evidence_refs"))
    payload = exact.get("payload")
    require(
        isinstance(payload, Mapping) and not _contains_secret_material(payload),
        "CANON_PAYLOAD_INVALID_OR_SECRET",
        "Canon payload must be one bounded secret-free object.",
        status="BLOCKED",
    )
    payload = cast(Mapping[str, Any], payload)
    exact["payload"] = dict(payload)
    require(
        _sha256(exact.get("payload_sha256"), field="payload_sha256")
        == sha256_bytes(canonical_json_bytes(exact["payload"])),
        "CANON_PAYLOAD_HASH_MISMATCH",
        "The Canon payload does not match its exact hash.",
        status="MISMATCH",
    )
    exact["permitted_actions"] = _safe_actions(
        exact.get("permitted_actions") or [], field="permitted_actions"
    )
    revision = int(exact.get("revision") or 0)
    require(
        revision > 0,
        "CANON_REVISION_INVALID",
        "A Canon revision must be positive.",
        status="MISMATCH",
    )
    exact["revision"] = revision
    exact["created_at"] = _timestamp(exact.get("created_at"), field="created_at")
    exact["expires_at"] = _timestamp(
        exact.get("expires_at"), field="expires_at", nullable=True
    )
    if exact["expires_at"] is not None:
        require(
            _timestamp_value(exact["expires_at"])
            > _timestamp_value(cast(str, exact["created_at"])),
            "CANON_EXPIRY_INVALID",
            "Canon expiry must be later than packet creation.",
            status="BLOCKED",
        )
    require(
        exact.get("input_state") == "PROPOSED",
        "CANON_INITIAL_STATE_INVALID",
        "An immutable outbound Canon envelope must begin at PROPOSED.",
        status="MISMATCH",
    )
    require(
        exact.get("source_write_authority_granted") is False
        and exact.get("project_truth_pointer_moved") is False
        and exact.get("learning_pointer_moved") is False
        and exact.get("hil_replayed") is False,
        "CANON_AUTHORITY_EFFECT_INVALID",
        "A Canon envelope cannot carry promotion, write, pointer, or HIL effects.",
        status="BLOCKED",
    )
    route_trace = exact.get("route_trace")
    require(
        isinstance(route_trace, list)
        and bool(route_trace)
        and all(isinstance(item, str) and item.strip() for item in route_trace),
        "CANON_ROUTE_TRACE_INVALID",
        "A Canon envelope requires one ordered task route trace.",
        status="MISMATCH",
    )
    route_trace = cast(list[str], route_trace)
    exact["route_trace"] = list(route_trace)
    return exact


def _insert_packet(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    local_role: str,
    envelope: dict[str, Any],
) -> bool:
    existing = connection.execute(
        "SELECT * FROM canon_packet WHERE canon_id=?", (envelope["canon_id"],)
    ).fetchone()
    if existing is not None:
        require(
            str(existing["canon_sha256"]) == envelope["canon_sha256"]
            and json.loads(str(existing["envelope_json"])) == envelope,
            "CANON_PACKET_IMMUTABILITY_CONFLICT",
            "The Canon packet identity already maps to different bytes.",
            status="MISMATCH",
        )
        return False
    replay = connection.execute(
        """
        SELECT canon_id,canon_sha256 FROM canon_packet
        WHERE owner_project_id=? AND local_role=? AND idempotency_key=?
        """,
        (project_id, local_role, envelope["idempotency_key"]),
    ).fetchone()
    require(
        replay is None,
        "CANON_REPLAY_IDENTITY_CONFLICT",
        "The Canon replay identity is already bound to another immutable packet.",
        status="MISMATCH",
        prior_canon_id=str(replay["canon_id"]) if replay else None,
    )
    connection.execute(
        """
        INSERT INTO canon_packet(
            canon_id,canon_sha256,owner_project_id,local_role,
            source_project_id,source_task_uuid,destination_project_id,
            destination_task_uuid,destination_contract_sha256,canon_type,
            revision,idempotency_key,current_state,envelope_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            envelope["canon_id"],
            envelope["canon_sha256"],
            project_id,
            local_role,
            envelope["source"]["project_id"],
            envelope["source"]["task_uuid"],
            envelope["destination"]["project_id"],
            envelope["destination"]["task_uuid"],
            envelope["destination_contract_sha256"],
            envelope["canon_type"],
            envelope["revision"],
            envelope["idempotency_key"],
            "PROPOSED",
            canonical_json_bytes(envelope).decode("utf-8"),
        ),
    )
    return True


def _last_event_sha256(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT event_sha256 FROM canon_event ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    return str(row["event_sha256"]) if row is not None else None


def _append_event(
    connection: sqlite3.Connection,
    *,
    canon_id: str,
    event_type: str,
    from_state: str | None,
    to_state: str,
    occurred_at: str,
    details: Mapping[str, Any],
    decision_key_sha256: str | None = None,
) -> dict[str, Any]:
    previous = _last_event_sha256(connection)
    body = {
        "schema": CANON_EVENT_SCHEMA,
        "canon_id": canon_id,
        "event_type": event_type,
        "from_state": from_state,
        "to_state": to_state,
        "occurred_at": occurred_at,
        "details": dict(details),
        "decision_key_sha256": decision_key_sha256,
        "previous_event_sha256": previous,
    }
    event_id = "cevt_" + sha256_bytes(canonical_json_bytes(body))[:28].lower()
    event = {**body, "event_id": event_id}
    event["event_sha256"] = sha256_bytes(canonical_json_bytes(event))
    connection.execute(
        """
        INSERT INTO canon_event(
            event_id,canon_id,event_type,from_state,to_state,occurred_at,
            decision_key_sha256,previous_event_sha256,event_sha256,event_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_id,
            canon_id,
            event_type,
            from_state,
            to_state,
            occurred_at,
            decision_key_sha256,
            previous,
            event["event_sha256"],
            canonical_json_bytes(event).decode("utf-8"),
        ),
    )
    connection.execute(
        "UPDATE canon_packet SET current_state=? WHERE canon_id=?",
        (to_state, canon_id),
    )
    return event


def seal_canon_envelope(
    project_root: str | Path,
    *,
    project_id: str,
    source: Mapping[str, Any],
    destination: Mapping[str, Any],
    direction: str,
    canon_type: str,
    authority_requested: str,
    contract_id: str,
    contract_version: int,
    destination_contract_sha256: str,
    schema_id: str,
    schema_version: str,
    schema_sha256: str,
    source_pointer: Mapping[str, Any],
    evidence_refs: list[Mapping[str, Any]],
    payload: Mapping[str, Any],
    permitted_actions: list[str],
    dependency_ids: list[str],
    expected_return_contract_sha256: str | None,
    independent_hil_owner_task_uuid: str,
    revision: int,
    idempotency_key: str,
    created_at: str,
    expires_at: str | None,
    supersedes: str | None = None,
    edge_id: str | None = None,
    route_trace: list[str] | None = None,
) -> dict[str, Any]:
    """Seal one immutable outbound packet without admitting it anywhere."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    exact_source = _endpoint(source, field="source")
    exact_destination = _endpoint(destination, field="destination")
    require(
        exact_source["project_id"] == project_id,
        "CANON_SOURCE_PROJECT_MISMATCH",
        "An outbound Canon must be sealed by its exact source project.",
        status="BLOCKED",
    )
    exact_payload = dict(payload)
    require(
        not _contains_secret_material(exact_payload),
        "CANON_PAYLOAD_SECRET_BLOCKED",
        "Secret-like values cannot enter a Canon envelope.",
        status="BLOCKED",
    )
    exact_created = cast(str, _timestamp(created_at, field="created_at"))
    exact_expiry = _timestamp(expires_at, field="expires_at", nullable=True)
    exact_revision = int(revision)
    require(
        exact_revision > 0,
        "CANON_REVISION_INVALID",
        "A Canon revision must be positive.",
        status="BLOCKED",
    )
    trace = route_trace or [_node_key(exact_source), _node_key(exact_destination)]
    body = {
        "schema": CANON_ENVELOPE_SCHEMA,
        "canon_type": _exact_text(canon_type, field="canon_type").upper(),
        "contract_id": _exact_text(contract_id, field="contract_id"),
        "contract_version": int(contract_version),
        "schema_id": _exact_text(schema_id, field="schema_id"),
        "schema_version": _exact_text(schema_version, field="schema_version"),
        "schema_sha256": _sha256(schema_sha256, field="schema_sha256"),
        "source": exact_source,
        "destination": exact_destination,
        "direction": str(direction).strip().upper(),
        "authority_requested": str(authority_requested).strip().upper(),
        "source_pointer": _source_pointer(source_pointer),
        "evidence_refs": _evidence_refs(evidence_refs),
        "payload": exact_payload,
        "payload_sha256": sha256_bytes(canonical_json_bytes(exact_payload)),
        "permitted_actions": _safe_actions(
            permitted_actions, field="permitted_actions"
        ),
        "dependency_ids": sorted(
            {_exact_text(item, field="dependency_ids") for item in dependency_ids}
        ),
        "destination_contract_sha256": _sha256(
            destination_contract_sha256, field="destination_contract_sha256"
        ),
        "expected_return_contract_sha256": (
            _sha256(
                expected_return_contract_sha256,
                field="expected_return_contract_sha256",
            )
            if expected_return_contract_sha256 is not None
            else None
        ),
        "independent_hil_owner_task_uuid": _exact_text(
            independent_hil_owner_task_uuid,
            field="independent_hil_owner_task_uuid",
        ),
        "edge_id": edge_id,
        "revision": exact_revision,
        "supersedes": supersedes,
        "route_trace": trace,
        "idempotency_key": _exact_text(
            idempotency_key, field="idempotency_key"
        ),
        "created_at": exact_created,
        "expires_at": exact_expiry,
        "input_state": "PROPOSED",
        "source_write_authority_granted": False,
        "project_truth_pointer_moved": False,
        "learning_pointer_moved": False,
        "hil_replayed": False,
        "private_reasoning_stored": False,
    }
    identity = {
        "source": exact_source,
        "destination": exact_destination,
        "destination_contract_sha256": body["destination_contract_sha256"],
        "payload_sha256": body["payload_sha256"],
        "revision": exact_revision,
        "idempotency_key": body["idempotency_key"],
    }
    body["canon_id"] = "canon_" + sha256_bytes(
        canonical_json_bytes(identity)
    )[:28].lower()
    body["canon_sha256"] = sha256_bytes(canonical_json_bytes(body))
    exact = _validate_envelope(body)
    path = _canon_root(root) / "outbox" / f"{exact['canon_id']}.json"
    _preflight_immutable_json(path, exact)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        inserted = _insert_packet(
            connection,
            project_id=project_id,
            local_role="OUTBOX",
            envelope=exact,
        )
        if inserted:
            _append_event(
                connection,
                canon_id=exact["canon_id"],
                event_type="PROPOSED",
                from_state=None,
                to_state="PROPOSED",
                occurred_at=exact_created,
                details={"outbound_only": True},
            )
        connection.commit()
    finally:
        connection.close()
    state = _immutable_json(path, exact)
    after = _require_authorities_unchanged(root, before, operation="seal_envelope")
    return {
        "status": "PASS",
        "state": state if inserted else "SEALED_IDEMPOTENT_REUSE",
        "envelope": exact,
        "envelope_path": str(path),
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": "OUTBOX_PROPOSED",
        },
    }


def _contract_match(
    envelope: Mapping[str, Any],
    contract: Mapping[str, Any] | None,
    *,
    as_of: str,
) -> tuple[bool, list[str]]:
    if contract is None:
        return False, ["EXPECTED_CONTRACT_UNDEFINED"]
    reasons: list[str] = []
    if not contract.get("active"):
        reasons.append("EXPECTED_CONTRACT_INACTIVE")
    contract_expiry = contract.get("expires_at")
    if contract_expiry is not None and _timestamp_value(as_of) > _timestamp_value(
        str(contract_expiry)
    ):
        reasons.append("EXPECTED_CONTRACT_EXPIRED")
    if envelope["destination"] != contract["destination"]:
        reasons.append("DESTINATION_BINDING_MISMATCH")
    allowed_sources = contract.get("source_allowlist") or []
    if envelope["source"] not in allowed_sources:
        reasons.append("SOURCE_BINDING_MISMATCH")
    if envelope["schema_id"] != contract.get("schema_id"):
        reasons.append("SCHEMA_ID_MISMATCH")
    if envelope["schema_version"] != contract.get("schema_version"):
        reasons.append("SCHEMA_VERSION_MISMATCH")
    if envelope["schema_sha256"] != contract.get("schema_sha256"):
        reasons.append("SCHEMA_SHA256_MISMATCH")
    if envelope["canon_type"] not in (contract.get("canon_types") or []):
        reasons.append("CANON_TYPE_MISMATCH")
    if envelope["authority_requested"] not in (
        contract.get("authority_requested") or []
    ):
        reasons.append("AUTHORITY_REQUEST_MISMATCH")
    if envelope["source_pointer"] not in (
        contract.get("accepted_source_pointers") or []
    ):
        reasons.append("ACCEPTED_SOURCE_POINTER_MISMATCH")
    payload_keys = set(envelope["payload"])
    allowed_payload_keys = set(contract.get("permitted_payload_keys") or [])
    if not payload_keys <= allowed_payload_keys:
        reasons.append("PAYLOAD_KEY_SCOPE_MISMATCH")
    if not set(envelope["permitted_actions"]) <= set(
        contract.get("permitted_actions") or []
    ):
        reasons.append("ACTION_SCOPE_MISMATCH")
    if envelope.get("expected_return_contract_sha256") != contract.get(
        "expected_return_contract_sha256"
    ):
        reasons.append("RETURN_CONTRACT_MISMATCH")
    if envelope["independent_hil_owner_task_uuid"] != contract.get(
        "independent_hil_owner_task_uuid"
    ):
        reasons.append("HIL_OWNER_MISMATCH")
    return not reasons, reasons


def classify_canon_envelope(
    project_root: str | Path,
    *,
    project_id: str,
    envelope: Mapping[str, Any],
    as_of: str,
) -> dict[str, Any]:
    """Classify without storing; undefined/incompatible input requires local HIL."""

    root = _project_root(project_root, project_id=project_id)
    exact = _validate_envelope(envelope)
    require(
        exact["destination"]["project_id"] == project_id,
        "CANON_DESTINATION_PROJECT_MISMATCH",
        "The packet was delivered to a project other than its exact destination.",
        status="BLOCKED",
    )
    exact_as_of = cast(str, _timestamp(as_of, field="as_of"))
    if exact["expires_at"] is not None:
        require(
            _timestamp_value(exact_as_of) <= _timestamp_value(exact["expires_at"]),
            "CANON_PACKET_EXPIRED",
            "The Canon envelope expired before receipt.",
            status="BLOCKED",
        )
    connection = _connect(root)
    try:
        row = connection.execute(
            "SELECT contract_json FROM canon_contract WHERE contract_sha256=?",
            (exact["destination_contract_sha256"],),
        ).fetchone()
    finally:
        connection.close()
    contract = (
        _validate_contract(json.loads(str(row["contract_json"])))
        if row is not None
        else None
    )
    matched, reasons = _contract_match(exact, contract, as_of=exact_as_of)
    return {
        "status": "PASS",
        "classification": "EXPECTED" if matched else "UNDEFINED_OR_INCOMPATIBLE",
        "next_state": "EXPECTED_ADMITTED" if matched else "PENDING_HIL",
        "reasons": reasons,
        "receiver_owned_hil": not matched,
        "hil_owner_task_uuid": exact["independent_hil_owner_task_uuid"],
        "project_truth_effect": "NONE",
        "learning_effect": "NONE",
        "source_write_authority_granted": False,
        "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
    }


def _packet_row(connection: sqlite3.Connection, canon_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM canon_packet WHERE canon_id=?", (canon_id,)
    ).fetchone()
    require(
        row is not None,
        "CANON_PACKET_NOT_FOUND",
        "The requested Canon packet is not in this project authority.",
        status="BLOCKED",
        canon_id=canon_id,
    )
    return cast(sqlite3.Row, row)


def _events_for(
    connection: sqlite3.Connection, canon_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT event_json FROM canon_event WHERE canon_id=? ORDER BY sequence",
        (canon_id,),
    ).fetchall()
    return [cast(dict[str, Any], json.loads(str(row["event_json"]))) for row in rows]


def receive_canon_envelope(
    project_root: str | Path,
    *,
    project_id: str,
    envelope: Mapping[str, Any],
    received_at: str,
) -> dict[str, Any]:
    """Receive, validate, and either auto-admit or stop at Canon Input HIL."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    exact = _validate_envelope(envelope)
    classification = classify_canon_envelope(
        root,
        project_id=project_id,
        envelope=exact,
        as_of=received_at,
    )
    exact_received = cast(str, _timestamp(received_at, field="received_at"))
    inbox_path = _canon_root(root) / "inbox" / f"{exact['canon_id']}.json"
    _preflight_immutable_json(inbox_path, exact)
    connection = _connect(root)
    events: list[dict[str, Any]] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        inserted = _insert_packet(
            connection,
            project_id=project_id,
            local_role="INBOX",
            envelope=exact,
        )
        if not inserted:
            row = _packet_row(connection, exact["canon_id"])
            events = _events_for(connection, exact["canon_id"])
            connection.commit()
            artifact_state = _immutable_json(inbox_path, exact)
            after = _require_authorities_unchanged(
                root, before, operation="receive_envelope_replay"
            )
            return {
                "status": "PASS",
                "state": str(row["current_state"]),
                "idempotent_reuse": True,
                "envelope": exact,
                "events": events,
                "classification": classification,
                "authority_before": before,
                "authority_after": after,
                "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
            }
        from_state = "PROPOSED"
        if int(exact["revision"]) > 1:
            supersedes = str(exact.get("supersedes") or "")
            require(
                bool(supersedes),
                "CANON_REVISION_PREDECESSOR_REQUIRED",
                "A Canon revision after v1 must bind the exact prior packet.",
                status="BLOCKED",
            )
            prior = _packet_row(connection, supersedes)
            require(
                str(prior["current_state"]) == "MORE_RESEARCH"
                and int(prior["revision"]) + 1 == int(exact["revision"])
                and str(prior["source_project_id"])
                == exact["source"]["project_id"]
                and str(prior["source_task_uuid"]) == exact["source"]["task_uuid"]
                and str(prior["destination_project_id"])
                == exact["destination"]["project_id"]
                and str(prior["destination_task_uuid"])
                == exact["destination"]["task_uuid"],
                "CANON_REVISION_LINEAGE_INVALID",
                "The revised packet does not continue one exact MORE_RESEARCH lineage.",
                status="BLOCKED",
            )
            events.append(
                _append_event(
                    connection,
                    canon_id=exact["canon_id"],
                    event_type="REVISED",
                    from_state="PROPOSED",
                    to_state="REVISED",
                    occurred_at=exact_received,
                    details={"supersedes": supersedes},
                )
            )
            _append_event(
                connection,
                canon_id=supersedes,
                event_type="SUPERSEDED_BY_REVISION",
                from_state="MORE_RESEARCH",
                to_state="SUPERSEDED",
                occurred_at=exact_received,
                details={"superseded_by": exact["canon_id"]},
            )
            from_state = "REVISED"
        events.append(
            _append_event(
                connection,
                canon_id=exact["canon_id"],
                event_type="RECEIVED",
                from_state=from_state,
                to_state="RECEIVED",
                occurred_at=exact_received,
                details={"destination_verified": True},
            )
        )
        events.append(
            _append_event(
                connection,
                canon_id=exact["canon_id"],
                event_type="VALIDATED",
                from_state="RECEIVED",
                to_state="VALIDATED",
                occurred_at=exact_received,
                details={
                    "classification": classification["classification"],
                    "reasons": classification["reasons"],
                },
            )
        )
        next_state = str(classification["next_state"])
        events.append(
            _append_event(
                connection,
                canon_id=exact["canon_id"],
                event_type=next_state,
                from_state="VALIDATED",
                to_state=next_state,
                occurred_at=exact_received,
                details={
                    "receiver_owned_hil": next_state == "PENDING_HIL",
                    "hil_owner_task_uuid": exact[
                        "independent_hil_owner_task_uuid"
                    ],
                },
            )
        )
        connection.commit()
    finally:
        connection.close()
    artifact_state = _immutable_json(inbox_path, exact)
    after = _require_authorities_unchanged(root, before, operation="receive_envelope")
    return {
        "status": "PASS",
        "state": classification["next_state"],
        "idempotent_reuse": False,
        "artifact_state": artifact_state,
        "envelope": exact,
        "events": events,
        "classification": classification,
        "canon_input_hil_required": classification["next_state"] == "PENDING_HIL",
        "project_hil_invoked": False,
        "learning_hil_invoked": False,
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": str(classification["next_state"]),
        },
    }


def decide_canon_input(
    project_root: str | Path,
    *,
    project_id: str,
    canon_id: str,
    expected_canon_sha256: str,
    decision_token: str,
    actor_task_uuid: str,
    actor_id: str,
    decided_at: str,
    reason: str | None = None,
    research_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply exactly one receiver-owned ACCEPT/REJECT/MORE_RESEARCH decision."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    token = str(decision_token or "")
    require(
        token in _DECISIONS,
        "CANON_DECISION_TOKEN_INVALID",
        "Canon Input HIL accepts only exact ACCEPT, REJECT, or MORE_RESEARCH.",
        status="BLOCKED",
    )
    exact_at = cast(str, _timestamp(decided_at, field="decided_at"))
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = _packet_row(connection, canon_id)
        envelope = cast(dict[str, Any], json.loads(str(row["envelope_json"])))
        require(
            str(row["canon_sha256"])
            == _sha256(expected_canon_sha256, field="expected_canon_sha256"),
            "CANON_DECISION_PACKET_HASH_MISMATCH",
            "The Canon decision does not bind the exact pending packet bytes.",
            status="MISMATCH",
        )
        exact_actor_task_uuid = _exact_text(
            actor_task_uuid, field="actor_task_uuid"
        )
        exact_actor_id = _exact_text(actor_id, field="actor_id")
        require(
            exact_actor_task_uuid
            == envelope["independent_hil_owner_task_uuid"]
            == envelope["destination"]["task_uuid"],
            "CANON_DECISION_OWNER_MISMATCH",
            "Only the exact receiving top-level task owns this Canon Input HIL.",
            status="BLOCKED",
        )
        exact_reason = str(reason or "").strip() or None
        if token in {"REJECT", "MORE_RESEARCH"}:
            require(
                exact_reason is not None,
                "CANON_DECISION_REASON_REQUIRED",
                "REJECT and MORE_RESEARCH require one visible bounded reason.",
                status="BLOCKED",
            )
        request = dict(research_request or {})
        if token == "MORE_RESEARCH":
            require(
                bool(request)
                and isinstance(request.get("requested_fields"), list)
                and bool(request.get("requested_fields")),
                "CANON_RESEARCH_REQUEST_REQUIRED",
                "MORE_RESEARCH requires one bounded requested-field contract.",
                status="BLOCKED",
            )
        if token != "MORE_RESEARCH":
            require(
                not request,
                "CANON_RESEARCH_REQUEST_UNEXPECTED",
                "Only MORE_RESEARCH may carry a bounded research request.",
                status="BLOCKED",
            )
        decision_key = sha256_bytes(
            canonical_json_bytes(
                {
                    "canon_id": canon_id,
                    "canon_sha256": row["canon_sha256"],
                    "revision": row["revision"],
                    "decision": token,
                    "actor_task_uuid": exact_actor_task_uuid,
                    "actor_id": exact_actor_id,
                    "reason": exact_reason,
                    "research_request": request if token == "MORE_RESEARCH" else None,
                    "decided_at": exact_at,
                }
            )
        )
        prior = connection.execute(
            "SELECT event_json FROM canon_event WHERE decision_key_sha256=?",
            (decision_key,),
        ).fetchone()
        if prior is not None:
            event = cast(dict[str, Any], json.loads(str(prior["event_json"])))
            receipt_row = connection.execute(
                """
                SELECT receipt_json FROM canon_receipt
                WHERE canon_id=? AND receipt_type='CANON_INPUT_DECISION'
                """,
                (canon_id,),
            ).fetchone()
            receipt = (
                cast(dict[str, Any], json.loads(str(receipt_row["receipt_json"])))
                if receipt_row is not None
                else {}
            )
            validate_canon_receipt(receipt)
            connection.commit()
            after = _require_authorities_unchanged(
                root, before, operation="decide_input_replay"
            )
            return {
                "status": "PASS",
                "idempotent_reuse": True,
                "event": event,
                "receipt": receipt,
                "authority_before": before,
                "authority_after": after,
                "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
            }
        require(
            str(row["current_state"]) == "PENDING_HIL",
            "CANON_DECISION_STATE_INVALID",
            "A Canon decision is valid only for one pending receiver-owned input HIL.",
            status="BLOCKED",
            current_state=str(row["current_state"]),
        )
        next_state = {
            "ACCEPT": "ACCEPTED_INPUT",
            "REJECT": "REJECTED",
            "MORE_RESEARCH": "MORE_RESEARCH",
        }[token]
        details = {
            "decision": token,
            "actor_task_uuid": exact_actor_task_uuid,
            "actor_id": exact_actor_id,
            "reason": exact_reason,
            "research_request": request if token == "MORE_RESEARCH" else None,
            "next_required_revision": (
                int(row["revision"]) + 1 if token == "MORE_RESEARCH" else None
            ),
        }
        event = _append_event(
            connection,
            canon_id=canon_id,
            event_type=f"CANON_INPUT_{token}",
            from_state="PENDING_HIL",
            to_state=next_state,
            occurred_at=exact_at,
            details=details,
            decision_key_sha256=decision_key,
        )
        receipt_body = {
            "schema": CANON_DECISION_RECEIPT_SCHEMA,
            "project_id": project_id,
            "canon_id": canon_id,
            "canon_sha256": row["canon_sha256"],
            "revision": int(row["revision"]),
            "decision": token,
            "state_before": "PENDING_HIL",
            "state_after": next_state,
            "actor_task_uuid": exact_actor_task_uuid,
            "actor_id": exact_actor_id,
            "reason": exact_reason,
            "research_request": request if token == "MORE_RESEARCH" else None,
            "next_required_revision": details["next_required_revision"],
            "source_notification_required": token in {"REJECT", "MORE_RESEARCH"},
            "project_truth_pointer_moved": False,
            "learning_pointer_moved": False,
            "project_hil_invoked": False,
            "learning_hil_invoked": False,
            "other_task_hil_decided": False,
            "source_write_authority_granted": False,
            "decided_at": exact_at,
            "event_sha256": event["event_sha256"],
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        validate_canon_receipt(receipt)
        connection.execute(
            """
            INSERT INTO canon_receipt(
                receipt_sha256,canon_id,receipt_type,receipt_json
            ) VALUES(?,?,?,?)
            """,
            (
                receipt["receipt_sha256"],
                canon_id,
                "CANON_INPUT_DECISION",
                canonical_json_bytes(receipt).decode("utf-8"),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    receipt_path = (
        _canon_root(root)
        / "receipts"
        / f"{receipt['receipt_sha256'].lower()}.json"
    )
    _immutable_json(receipt_path, receipt)
    after = _require_authorities_unchanged(root, before, operation="decide_input")
    return {
        "status": "PASS",
        "idempotent_reuse": False,
        "event": event,
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": next_state,
        },
    }


def supersede_canon_input(
    project_root: str | Path,
    *,
    project_id: str,
    canon_id: str,
    superseded_by: str,
    occurred_at: str,
) -> dict[str, Any]:
    """Append immutable supersession without deleting the prior revision."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    exact_at = cast(str, _timestamp(occurred_at, field="occurred_at"))
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = _packet_row(connection, canon_id)
        successor = _packet_row(connection, superseded_by)
        require(
            str(successor["source_project_id"]) == str(row["source_project_id"])
            and str(successor["source_task_uuid"]) == str(row["source_task_uuid"])
            and str(successor["destination_project_id"])
            == str(row["destination_project_id"])
            and str(successor["destination_task_uuid"])
            == str(row["destination_task_uuid"])
            and int(successor["revision"]) > int(row["revision"]),
            "CANON_SUPERSESSION_LINEAGE_INVALID",
            "Canon supersession requires one newer packet in the same exact route.",
            status="BLOCKED",
        )
        current = str(row["current_state"])
        require(
            current in _ADMITTED_STATES | _TERMINAL_STATES | {"MORE_RESEARCH"},
            "CANON_SUPERSESSION_STATE_INVALID",
            "Only a decided or admitted Canon revision can be superseded.",
            status="BLOCKED",
            current_state=current,
        )
        if current == "SUPERSEDED":
            events = _events_for(connection, canon_id)
            supersession_events = [
                event
                for event in events
                if event.get("to_state") == "SUPERSEDED"
                and event.get("event_type")
                in {"SUPERSEDED", "SUPERSEDED_BY_REVISION"}
            ]
            require(
                bool(supersession_events)
                and supersession_events[-1].get("details", {}).get("superseded_by")
                == superseded_by,
                "CANON_SUPERSESSION_REPLAY_CONFLICT",
                "The Canon packet was already superseded by another revision.",
                status="MISMATCH",
            )
            connection.commit()
            after = _require_authorities_unchanged(
                root, before, operation="supersede_replay"
            )
            return {
                "status": "PASS",
                "idempotent_reuse": True,
                "events": events,
                "authority_before": before,
                "authority_after": after,
                "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
            }
        event = _append_event(
            connection,
            canon_id=canon_id,
            event_type="SUPERSEDED",
            from_state=current,
            to_state="SUPERSEDED",
            occurred_at=exact_at,
            details={"superseded_by": superseded_by},
        )
        connection.commit()
    finally:
        connection.close()
    after = _require_authorities_unchanged(root, before, operation="supersede")
    return {
        "status": "PASS",
        "idempotent_reuse": False,
        "event": event,
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": "SUPERSEDED",
        },
    }


def _normalise_edge(value: Mapping[str, Any]) -> dict[str, Any]:
    require(
        value.get("schema") == CANON_EDGE_SCHEMA,
        "CANON_EDGE_SCHEMA_INVALID",
        "The Canon task-edge schema is unsupported.",
        status="MISMATCH",
    )
    exact = dict(value)
    exact["edge_id"] = _exact_text(exact.get("edge_id"), field="edge_id")
    exact["source"] = _endpoint(
        cast(Mapping[str, Any], exact.get("source") or {}), field="source"
    )
    exact["destination"] = _endpoint(
        cast(Mapping[str, Any], exact.get("destination") or {}),
        field="destination",
    )
    direction = _exact_text(exact.get("direction"), field="direction").upper()
    require(
        direction in _DIRECTIONS,
        "CANON_EDGE_DIRECTION_INVALID",
        "A Canon task edge has an unsupported direction.",
        status="BLOCKED",
    )
    exact["direction"] = direction
    exact["contract_sha256"] = _sha256(
        exact.get("contract_sha256"), field="contract_sha256"
    )
    exact["schema_sha256"] = _sha256(
        exact.get("schema_sha256"), field="schema_sha256"
    )
    exact["expected_return_contract_sha256"] = _sha256(
        exact.get("expected_return_contract_sha256"),
        field="expected_return_contract_sha256",
    )
    exact["permitted_actions"] = _safe_actions(
        exact.get("permitted_actions") or [], field="permitted_actions"
    )
    edge_revision = int(exact.get("edge_revision") or 0)
    require(
        edge_revision > 0,
        "CANON_EDGE_REVISION_INVALID",
        "A Canon task edge revision must be positive.",
        status="MISMATCH",
    )
    exact["edge_revision"] = edge_revision
    dependency_ids = exact.get("dependency_ids")
    require(
        isinstance(dependency_ids, list),
        "CANON_EDGE_DEPENDENCIES_INVALID",
        "Canon edge dependency IDs must be one list.",
        status="MISMATCH",
    )
    dependency_ids = cast(list[Any], dependency_ids)
    exact["dependency_ids"] = sorted(
        {
            _exact_text(item, field="dependency_ids")
            for item in dependency_ids
        }
    )
    mode = _exact_text(exact.get("task_mode"), field="task_mode").upper()
    scope = _exact_text(exact.get("scope_class"), field="scope_class").upper()
    require(
        mode in _TASK_MODES and scope in _SCOPE_CLASSES,
        "CANON_EDGE_EXECUTION_CLASS_INVALID",
        "Canon task mode or execution scope is unsupported.",
        status="BLOCKED",
    )
    exact["task_mode"] = mode
    exact["scope_class"] = scope
    permitted_paths = exact.get("permitted_paths")
    permitted_tools = exact.get("permitted_tools")
    require(
        isinstance(permitted_paths, list) and isinstance(permitted_tools, list),
        "CANON_EDGE_SCOPE_LIST_INVALID",
        "Canon edge paths and tools must be bounded lists.",
        status="MISMATCH",
    )
    permitted_paths = cast(list[Any], permitted_paths)
    permitted_tools = cast(list[Any], permitted_tools)
    exact["permitted_paths"] = sorted(
        {
            _exact_text(item, field="permitted_paths")
            for item in permitted_paths
        }
    )
    exact["permitted_tools"] = sorted(
        {
            _exact_text(item, field="permitted_tools")
            for item in permitted_tools
        }
    )
    require(
        exact.get("one_writer") is True
        and exact.get("subagent_hil_allowed") is False
        and exact.get("approval_propagation_allowed") is False
        and exact.get("pointer_propagation_allowed") is False
        and exact.get("source_write_authority_granted") is False,
        "CANON_EDGE_AUTHORITY_BOUNDARY_INVALID",
        "A Canon edge must preserve one writer and forbid authority propagation.",
        status="BLOCKED",
    )
    owner = _exact_text(
        exact.get("independent_hil_owner_task_uuid"),
        field="independent_hil_owner_task_uuid",
    )
    if mode == "TOP_LEVEL_TASK":
        require(
            owner == exact["destination"]["task_uuid"],
            "CANON_TOP_LEVEL_HIL_OWNER_INVALID",
            "A linked top-level task owns only its own independent HIL.",
            status="BLOCKED",
        )
    else:
        require(
            owner == exact["source"]["task_uuid"]
            and exact.get("subagent_hil_allowed") is False,
            "CANON_SUBAGENT_HIL_FORBIDDEN",
            "A subagent cannot own HIL; results return to its top-level task.",
            status="BLOCKED",
        )
    exact["created_at"] = _timestamp(exact.get("created_at"), field="created_at")
    exact["expires_at"] = _timestamp(
        exact.get("expires_at"), field="expires_at", nullable=True
    )
    if exact["expires_at"] is not None:
        require(
            _timestamp_value(exact["expires_at"])
            > _timestamp_value(cast(str, exact["created_at"])),
            "CANON_EDGE_EXPIRY_INVALID",
            "A Canon task edge must expire after it is created.",
            status="BLOCKED",
        )
    if exact.get("host_write_authorization_sha256") is not None:
        exact["host_write_authorization_sha256"] = _sha256(
            exact.get("host_write_authorization_sha256"),
            field="host_write_authorization_sha256",
        )
    return exact


def _validate_edge(value: Mapping[str, Any]) -> dict[str, Any]:
    exact = _normalise_edge(value)
    claimed = _sha256(exact.get("edge_sha256"), field="edge_sha256")
    require(
        claimed == _hash_without(exact, "edge_sha256"),
        "CANON_EDGE_HASH_MISMATCH",
        "The Canon task edge failed its immutable hash check.",
        status="MISMATCH",
    )
    return exact


def _would_cycle(
    existing_edges: list[tuple[str, str]], source_node: str, destination_node: str
) -> bool:
    graph: dict[str, set[str]] = {}
    for source, destination in existing_edges:
        graph.setdefault(source, set()).add(destination)
    graph.setdefault(source_node, set()).add(destination_node)
    stack = [destination_node]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current == source_node:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return False


def register_canon_task_edge(
    project_root: str | Path,
    *,
    project_id: str,
    edge: Mapping[str, Any],
) -> dict[str, Any]:
    """Register one immutable acyclic task edge; fan-out and fan-in remain valid."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    raw = dict(edge)
    raw.setdefault("schema", CANON_EDGE_SCHEMA)
    raw.pop("edge_sha256", None)
    raw = _normalise_edge(raw)
    raw["edge_sha256"] = sha256_bytes(canonical_json_bytes(raw))
    exact = _validate_edge(raw)
    require(
        exact["source"]["project_id"] == project_id,
        "CANON_EDGE_SOURCE_PROJECT_MISMATCH",
        "The task graph edge must be registered by its exact source project.",
        status="BLOCKED",
    )
    source_node = _node_key(exact["source"])
    destination_node = _node_key(exact["destination"])
    require(
        source_node != destination_node,
        "CANON_TASK_GRAPH_SELF_CYCLE_BLOCKED",
        "A Canon task cannot route an edge to itself.",
        status="BLOCKED",
    )
    path = _canon_root(root) / "graph" / f"{exact['edge_id']}.json"
    _preflight_immutable_json(path, exact)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT edge_json FROM canon_edge WHERE edge_id=?",
            (exact["edge_id"],),
        ).fetchone()
        if existing is not None:
            require(
                json.loads(str(existing["edge_json"])) == exact,
                "CANON_EDGE_IMMUTABILITY_CONFLICT",
                "The Canon edge identity already maps to other bytes.",
                status="MISMATCH",
            )
            connection.commit()
        else:
            pairs = [
                (str(row["source_node"]), str(row["destination_node"]))
                for row in connection.execute(
                    "SELECT source_node,destination_node FROM canon_edge"
                ).fetchall()
            ]
            require(
                not _would_cycle(pairs, source_node, destination_node),
                "CANON_TASK_GRAPH_CYCLE_BLOCKED",
                "The proposed Canon edge would create a task-routing cycle.",
                status="BLOCKED",
                source_node=source_node,
                destination_node=destination_node,
            )
            connection.execute(
                """
                INSERT INTO canon_edge(
                    edge_id,edge_sha256,source_node,destination_node,
                    expected_return_contract_sha256,edge_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    exact["edge_id"],
                    exact["edge_sha256"],
                    source_node,
                    destination_node,
                    exact["expected_return_contract_sha256"],
                    canonical_json_bytes(exact).decode("utf-8"),
                ),
            )
            connection.commit()
    finally:
        connection.close()
    state = _immutable_json(path, exact)
    after = _require_authorities_unchanged(root, before, operation="register_edge")
    return {
        "status": "PASS",
        "state": state,
        "edge": exact,
        "edge_path": str(path),
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": "TASK_EDGE_REGISTERED",
        },
    }


def bind_received_canon_task_edge(
    project_root: str | Path,
    *,
    project_id: str,
    edge: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one exact source-sealed edge in its destination project authority."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    exact = _validate_edge(edge)
    require(
        exact["destination"]["project_id"] == project_id,
        "CANON_EDGE_DESTINATION_PROJECT_MISMATCH",
        "A received task edge must be bound by its exact destination project.",
        status="BLOCKED",
    )
    source_node = _node_key(exact["source"])
    destination_node = _node_key(exact["destination"])
    path = _canon_root(root) / "graph" / f"{exact['edge_id']}.json"
    _preflight_immutable_json(path, exact)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT edge_json FROM canon_edge WHERE edge_id=?",
            (exact["edge_id"],),
        ).fetchone()
        if existing is not None:
            require(
                json.loads(str(existing["edge_json"])) == exact,
                "CANON_EDGE_IMMUTABILITY_CONFLICT",
                "The received Canon edge identity already maps to other bytes.",
                status="MISMATCH",
            )
        else:
            pairs = [
                (str(row["source_node"]), str(row["destination_node"]))
                for row in connection.execute(
                    "SELECT source_node,destination_node FROM canon_edge"
                ).fetchall()
            ]
            require(
                not _would_cycle(pairs, source_node, destination_node),
                "CANON_TASK_GRAPH_CYCLE_BLOCKED",
                "The received Canon edge would create a task-routing cycle.",
                status="BLOCKED",
                source_node=source_node,
                destination_node=destination_node,
            )
            connection.execute(
                """
                INSERT INTO canon_edge(
                    edge_id,edge_sha256,source_node,destination_node,
                    expected_return_contract_sha256,edge_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    exact["edge_id"],
                    exact["edge_sha256"],
                    source_node,
                    destination_node,
                    exact["expected_return_contract_sha256"],
                    canonical_json_bytes(exact).decode("utf-8"),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    state = _immutable_json(path, exact)
    after = _require_authorities_unchanged(root, before, operation="bind_edge")
    return {
        "status": "PASS",
        "state": state,
        "edge": exact,
        "edge_path": str(path),
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": "TASK_EDGE_BOUND",
        },
    }


def dispatch_linked_canon_task(
    project_root: str | Path,
    *,
    project_id: str,
    source: Mapping[str, Any],
    dispatcher: CanonTaskDispatcher | None,
    task_title: str,
    task_mode: str,
    scope_class: str,
    permitted_paths: list[str],
    permitted_tools: list[str],
    user_subagent_authorized: bool,
    host_write_authorization_sha256: str | None,
    direction: str,
    contract_sha256: str,
    schema_sha256: str,
    edge_revision: int,
    permitted_actions: list[str],
    dependency_ids: list[str],
    expected_return_contract_sha256: str,
    expires_at: str | None,
    requested_at: str,
) -> dict[str, Any]:
    """Use one supported host seam, then bind the returned exact destination."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    exact_source = _endpoint(source, field="source")
    require(
        exact_source["project_id"] == project_id,
        "CANON_DISPATCH_SOURCE_PROJECT_MISMATCH",
        "A linked-task dispatch must originate in its exact project.",
        status="BLOCKED",
    )
    mode = str(task_mode).strip().upper()
    scope = str(scope_class).strip().upper()
    require(
        mode in _TASK_MODES and scope in _SCOPE_CLASSES,
        "CANON_DISPATCH_CLASS_INVALID",
        "The requested linked-task execution class is unsupported.",
        status="BLOCKED",
    )
    exact_tools = sorted({_exact_text(item, field="permitted_tools") for item in permitted_tools})
    forbidden_tools = sorted(
        {item.lower() for item in exact_tools} & _FORBIDDEN_SUBAGENT_TOOLS
    )
    exact_write_receipt = (
        _sha256(
            host_write_authorization_sha256,
            field="host_write_authorization_sha256",
        )
        if host_write_authorization_sha256 is not None
        else None
    )
    if scope == "GOVERNED_READ_WRITE":
        require(
            exact_write_receipt is not None and bool(permitted_paths),
            "CANON_LINKED_TASK_WRITE_AUTHORITY_REQUIRED",
            "Governed read-write linked tasks require separate host authority and paths.",
            status="BLOCKED",
        )
    if mode == "SUBAGENT":
        require(
            user_subagent_authorized,
            "CANON_SUBAGENT_USER_AUTHORITY_REQUIRED",
            "Canon cannot launch a subagent while current user policy forbids it.",
            status="BLOCKED",
        )
        require(
            not forbidden_tools,
            "CANON_SUBAGENT_HIL_OR_LIFECYCLE_TOOL_FORBIDDEN",
            "A Canon subagent cannot own HIL, Fuse, pointer, Git, or State Travel tools.",
            status="BLOCKED",
            forbidden_tools=forbidden_tools,
        )
    request_body = {
        "schema": "evidence-lane.canon-linked-task-dispatch-request.v1",
        "project_id": project_id,
        "source": exact_source,
        "task_title": _exact_text(task_title, field="task_title"),
        "task_mode": mode,
        "scope_class": scope,
        "permitted_paths": sorted(
            {_exact_text(item, field="permitted_paths") for item in permitted_paths}
        ),
        "permitted_tools": exact_tools,
        "one_writer": True,
        "user_subagent_authorized": bool(user_subagent_authorized),
        "host_write_authorization_sha256": exact_write_receipt,
        "canon_grants_source_write": False,
        "canon_grants_hil": False,
        "expected_return_contract_sha256": _sha256(
            expected_return_contract_sha256,
            field="expected_return_contract_sha256",
        ),
        "requested_at": cast(str, _timestamp(requested_at, field="requested_at")),
    }
    request_sha256 = sha256_bytes(canonical_json_bytes(request_body))
    dispatch_id = "cdispatch_" + request_sha256[:26].lower()
    connection = _connect(root)
    try:
        existing = connection.execute(
            "SELECT request_sha256,receipt_json FROM canon_dispatch WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if existing is not None:
            require(
                str(existing["request_sha256"]) == request_sha256,
                "CANON_DISPATCH_REPLAY_CONFLICT",
                "The Canon dispatch identity is already bound to another request.",
                status="MISMATCH",
            )
            receipt = cast(dict[str, Any], json.loads(str(existing["receipt_json"])))
            validate_canon_receipt(receipt)
            after = _require_authorities_unchanged(
                root, before, operation="dispatch_replay"
            )
            return {
                "status": "PASS",
                "idempotent_reuse": True,
                "receipt": receipt,
                "edge": receipt["edge"],
                "authority_before": before,
                "authority_after": after,
                "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
            }
    finally:
        connection.close()
    require(
        dispatcher is not None
        and callable(getattr(dispatcher, "create_linked_task", None))
        and getattr(dispatcher, "host_kind", None) == "CODEX"
        and getattr(dispatcher, "capability", None)
        == CODEX_HOST_CREATE_CAPABILITY,
        "HOST_CAPABILITY_UNAVAILABLE",
        "The host does not expose the supported idempotent Codex task-create operation.",
        status="UNAVAILABLE",
    )
    exact_dispatcher = cast(CanonTaskDispatcher, dispatcher)
    host_request = {
        "schema": "evidence-lane.codex-host-linked-task-create-request.v1",
        "host_kind": "CODEX",
        "operation": "CREATE_LINKED_TASK",
        "capability": CODEX_HOST_CREATE_CAPABILITY,
        "idempotency_key": dispatch_id,
        "request_sha256": request_sha256,
        "required_receipt_schema": CODEX_HOST_CREATE_RECEIPT_SCHEMA,
        "canon_request": request_body,
    }
    response = exact_dispatcher.create_linked_task(host_request)
    require(
        isinstance(response, Mapping),
        "CANON_DISPATCH_RESPONSE_INVALID",
        "The host linked-task response must be one exact binding object.",
        status="FAIL",
    )
    destination = _endpoint(response, field="destination")
    host_creation_receipt = response.get("host_creation_receipt")
    require(
        response.get("created_once") is True
        and isinstance(host_creation_receipt, Mapping),
        "CANON_DISPATCH_EXACT_ONCE_UNPROVEN",
        "The host did not return one exact idempotent destination receipt.",
        status="FAIL",
    )
    host_creation_receipt = cast(Mapping[str, Any], host_creation_receipt)
    validate_canon_receipt(host_creation_receipt)
    require(
        host_creation_receipt.get("schema") == CODEX_HOST_CREATE_RECEIPT_SCHEMA
        and host_creation_receipt.get("idempotency_key") == dispatch_id
        and host_creation_receipt.get("request_sha256") == request_sha256
        and host_creation_receipt.get("destination") == destination,
        "CANON_CODEX_HOST_RECEIPT_BINDING_MISMATCH",
        "The Codex host receipt does not bind the exact destination and request.",
        status="MISMATCH",
    )
    owner = (
        destination["task_uuid"] if mode == "TOP_LEVEL_TASK" else exact_source["task_uuid"]
    )
    edge_body = {
        "schema": CANON_EDGE_SCHEMA,
        "edge_id": "cedge_"
        + sha256_bytes(
            canonical_json_bytes(
                {
                    "source": exact_source,
                    "destination": destination,
                    "contract_sha256": contract_sha256,
                    "edge_revision": edge_revision,
                }
            )
        )[:26].lower(),
        "source": exact_source,
        "destination": destination,
        "direction": str(direction).strip().upper(),
        "contract_sha256": _sha256(contract_sha256, field="contract_sha256"),
        "schema_sha256": _sha256(schema_sha256, field="schema_sha256"),
        "edge_revision": int(edge_revision),
        "permitted_actions": _safe_actions(
            permitted_actions, field="permitted_actions"
        ),
        "dependency_ids": sorted(
            {_exact_text(item, field="dependency_ids") for item in dependency_ids}
        ),
        "expected_return_contract_sha256": request_body[
            "expected_return_contract_sha256"
        ],
        "independent_hil_owner_task_uuid": owner,
        "task_mode": mode,
        "scope_class": scope,
        "permitted_paths": request_body["permitted_paths"],
        "permitted_tools": exact_tools,
        "one_writer": True,
        "subagent_hil_allowed": False,
        "approval_propagation_allowed": False,
        "pointer_propagation_allowed": False,
        "source_write_authority_granted": False,
        "source_write_authority_source": (
            "HOST_TASK_CONTRACT" if scope == "GOVERNED_READ_WRITE" else "NONE"
        ),
        "host_write_authorization_sha256": exact_write_receipt,
        "expires_at": expires_at,
        "created_at": request_body["requested_at"],
    }
    registered = register_canon_task_edge(
        root,
        project_id=project_id,
        edge=edge_body,
    )
    receipt_body = {
        "schema": CANON_DISPATCH_RECEIPT_SCHEMA_V2,
        "dispatch_id": dispatch_id,
        "request_sha256": request_sha256,
        "source": exact_source,
        "destination": destination,
        "task_mode": mode,
        "scope_class": scope,
        "created_once": True,
        "host_creation_receipt": dict(host_creation_receipt),
        "edge": registered["edge"],
        "approval_propagated": False,
        "pointer_propagated": False,
        "source_write_authority_granted_by_canon": False,
        "subagent_hil_allowed": False,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    validate_canon_receipt(receipt)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO canon_dispatch(
                dispatch_id,request_sha256,receipt_sha256,receipt_json
            ) VALUES(?,?,?,?)
            """,
            (
                dispatch_id,
                request_sha256,
                receipt["receipt_sha256"],
                canonical_json_bytes(receipt).decode("utf-8"),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    after = _require_authorities_unchanged(root, before, operation="dispatch")
    return {
        "status": "PASS",
        "idempotent_reuse": False,
        "receipt": receipt,
        "edge": registered["edge"],
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": "LINKED_TASK_BOUND",
        },
    }


def raise_canon_backfire(
    project_root: str | Path,
    *,
    project_id: str,
    admitted_canon_id: str,
    failure_class: str,
    recipient: Mapping[str, Any],
    requested_contract_id: str,
    requested_contract_version: int,
    requested_contract_sha256: str,
    requested_schema_id: str,
    requested_schema_version: str,
    requested_schema_sha256: str,
    requested_revision: int,
    evidence_refs: list[Mapping[str, Any]],
    dependency_ids: list[str],
    return_route: Mapping[str, Any],
    source_pointer: Mapping[str, Any],
    expected_return_contract_sha256: str,
    expires_at: str,
    raised_at: str,
    backfire_trace: list[str] | None = None,
) -> dict[str, Any]:
    """Raise a conditional exact-recipient correction packet, never a retry loop."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    failure = str(failure_class).strip().upper()
    require(
        failure in _BACKFIRE_CLASSES,
        "CANON_BACKFIRE_CLASS_INVALID",
        "Canon backfire is allowed only for a bounded upstream input need.",
        status="BLOCKED",
    )
    exact_recipient = _endpoint(recipient, field="recipient")
    exact_return = _endpoint(return_route, field="return_route")
    connection = _connect(root)
    try:
        admitted_row = _packet_row(connection, admitted_canon_id)
        admitted = cast(
            dict[str, Any], json.loads(str(admitted_row["envelope_json"]))
        )
        require(
            str(admitted_row["current_state"]) in _ADMITTED_STATES,
            "CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT",
            "A backfire can originate only from an admitted Canon input.",
            status="BLOCKED",
            current_state=str(admitted_row["current_state"]),
        )
        require(
            admitted["destination"]["project_id"] == project_id,
            "CANON_BACKFIRE_SOURCE_PROJECT_MISMATCH",
            "The current project is not the admitted packet's receiving owner.",
            status="BLOCKED",
        )
    finally:
        connection.close()
    source = admitted["destination"]
    trace = list(backfire_trace or [])
    source_node = _node_key(source)
    recipient_node = _node_key(exact_recipient)
    require(
        recipient_node not in trace and source_node not in trace,
        "CANON_BACKFIRE_ROUTE_CYCLE_BLOCKED",
        "The backfire route repeats a task already present in its correction trace.",
        status="BLOCKED",
        backfire_trace=trace,
    )
    trace.extend([source_node, recipient_node])
    exact_requested_revision = int(requested_revision)
    require(
        exact_requested_revision > int(admitted["revision"]),
        "CANON_BACKFIRE_STALE_REVISION_BLOCKED",
        "A backfire must request a revision newer than the admitted packet.",
        status="BLOCKED",
    )
    dedup_key = sha256_bytes(
        canonical_json_bytes(
            {
                "recipient": exact_recipient,
                "requested_contract_sha256": _sha256(
                    requested_contract_sha256,
                    field="requested_contract_sha256",
                ),
                "requested_revision": exact_requested_revision,
            }
        )
    )
    request_identity = {
        "admitted_canon_id": admitted_canon_id,
        "failure_class": failure,
        "recipient": exact_recipient,
        "requested_contract_id": requested_contract_id,
        "requested_contract_version": requested_contract_version,
        "requested_contract_sha256": requested_contract_sha256,
        "requested_revision": exact_requested_revision,
        "evidence_refs": _evidence_refs(evidence_refs),
        "dependency_ids": sorted(dependency_ids),
        "return_route": exact_return,
        "backfire_trace": trace,
    }
    request_sha256 = sha256_bytes(canonical_json_bytes(request_identity))
    connection = _connect(root)
    try:
        prior = connection.execute(
            """
            SELECT request_sha256,canon_id,canon_sha256
            FROM canon_backfire_dedup WHERE dedup_key_sha256=?
            """,
            (dedup_key,),
        ).fetchone()
        if prior is not None:
            require(
                str(prior["request_sha256"]) == request_sha256,
                "CANON_BACKFIRE_DEDUP_CONFLICT",
                "The same recipient, contract, and revision carry different backfire bytes.",
                status="MISMATCH",
            )
            packet = _packet_row(connection, str(prior["canon_id"]))
            envelope = cast(
                dict[str, Any], json.loads(str(packet["envelope_json"]))
            )
            after = _require_authorities_unchanged(
                root, before, operation="backfire_replay"
            )
            return {
                "status": "PASS",
                "idempotent_reuse": True,
                "dedup_key_sha256": dedup_key,
                "envelope": envelope,
                "authority_before": before,
                "authority_after": after,
                "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
            }
    finally:
        connection.close()
    payload = {
        "conflict_code": "CANON_APPLICATION_CONFLICT",
        "admitted_canon_id": admitted_canon_id,
        "admitted_canon_sha256": admitted["canon_sha256"],
        "failure_class": failure,
        "exact_recipient": exact_recipient,
        "requested_contract_sha256": _sha256(
            requested_contract_sha256, field="requested_contract_sha256"
        ),
        "requested_revision": exact_requested_revision,
        "dependency_ids": sorted(
            {_exact_text(item, field="dependency_ids") for item in dependency_ids}
        ),
        "return_route": exact_return,
        "backfire_trace": trace,
        "automatic_retry_allowed": False,
    }
    sealed = seal_canon_envelope(
        root,
        project_id=project_id,
        source=source,
        destination=exact_recipient,
        direction="UPSTREAM",
        canon_type="CANON_BACKFIRE",
        authority_requested="CORRECTION_PROPOSAL",
        contract_id=requested_contract_id,
        contract_version=int(requested_contract_version),
        destination_contract_sha256=requested_contract_sha256,
        schema_id=requested_schema_id,
        schema_version=requested_schema_version,
        schema_sha256=requested_schema_sha256,
        source_pointer=source_pointer,
        evidence_refs=evidence_refs,
        payload=payload,
        permitted_actions=["REPORT_RESULT"],
        dependency_ids=dependency_ids,
        expected_return_contract_sha256=expected_return_contract_sha256,
        independent_hil_owner_task_uuid=exact_recipient["task_uuid"],
        revision=1,
        idempotency_key=dedup_key,
        created_at=raised_at,
        expires_at=expires_at,
        route_trace=trace,
    )
    envelope = sealed["envelope"]
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO canon_backfire_dedup(
                dedup_key_sha256,request_sha256,canon_id,canon_sha256
            ) VALUES(?,?,?,?)
            """,
            (
                dedup_key,
                request_sha256,
                envelope["canon_id"],
                envelope["canon_sha256"],
            ),
        )
        connection.commit()
    finally:
        connection.close()
    after = _require_authorities_unchanged(root, before, operation="backfire")
    return {
        "status": "PASS",
        "idempotent_reuse": False,
        "dedup_key_sha256": dedup_key,
        "envelope": envelope,
        "automatic_retry_allowed": False,
        "receiver_owns_any_required_hil": True,
        "authority_before": before,
        "authority_after": after,
        "authority_effects": {
            **_AUTHORITY_EFFECTS_NONE,
            "canon_input": "BACKFIRE_OUTBOX_PROPOSED",
        },
    }


def inspect_canon_inbox(
    project_root: str | Path,
    *,
    project_id: str,
    task_uuid: str | None = None,
    states: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return a bounded secret-free inbox and event projection."""

    root = _project_root(project_root, project_id=project_id)
    require(
        isinstance(limit, int) and 1 <= limit <= 500,
        "CANON_QUERY_LIMIT_INVALID",
        "Canon inbox queries are bounded to 1..500 packets.",
        status="BLOCKED",
    )
    clauses = ["local_role='INBOX'"]
    parameters: list[Any] = []
    if task_uuid is not None:
        clauses.append("destination_task_uuid=?")
        parameters.append(_exact_text(task_uuid, field="task_uuid"))
    exact_states = sorted({str(item).strip().upper() for item in states or []})
    if exact_states:
        placeholders = ",".join("?" for _ in exact_states)
        clauses.append(f"current_state IN ({placeholders})")
        parameters.extend(exact_states)
    parameters.append(limit)
    connection = _connect(root)
    try:
        rows = connection.execute(
            f"""
            SELECT * FROM canon_packet WHERE {' AND '.join(clauses)}
            ORDER BY rowid LIMIT ?
            """,
            parameters,
        ).fetchall()
        packets = []
        for row in rows:
            envelope = cast(
                dict[str, Any], json.loads(str(row["envelope_json"]))
            )
            packets.append(
                {
                    "canon_id": row["canon_id"],
                    "canon_sha256": row["canon_sha256"],
                    "canon_type": row["canon_type"],
                    "revision": row["revision"],
                    "state": row["current_state"],
                    "source": envelope["source"],
                    "destination": envelope["destination"],
                    "destination_contract_sha256": row[
                        "destination_contract_sha256"
                    ],
                    "payload_sha256": envelope["payload_sha256"],
                    "evidence_refs": envelope["evidence_refs"],
                    "events": _events_for(connection, str(row["canon_id"])),
                    "raw_payload_returned": False,
                }
            )
        event_head = _last_event_sha256(connection)
    finally:
        connection.close()
    body = {
        "status": "PASS",
        "project_id": project_id,
        "result": "HIT" if packets else "NO_HIT",
        "packet_count": len(packets),
        "packets": packets,
        "event_head_sha256": event_head,
        "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
    }
    return {**body, "projection_sha256": sha256_bytes(canonical_json_bytes(body))}


def inspect_canon_task_graph(
    project_root: str | Path,
    *,
    project_id: str,
) -> dict[str, Any]:
    """Return the acyclic edge graph and any outstanding typed return contracts."""

    root = _project_root(project_root, project_id=project_id)
    connection = _connect(root)
    try:
        edge_rows = connection.execute(
            "SELECT edge_json FROM canon_edge ORDER BY rowid"
        ).fetchall()
        edges = [
            _validate_edge(json.loads(str(row["edge_json"]))) for row in edge_rows
        ]
        result_rows = connection.execute(
            """
            SELECT envelope_json FROM canon_packet
            WHERE local_role='INBOX' AND canon_type='TASK_RESULT'
              AND current_state IN ('EXPECTED_ADMITTED','ACCEPTED_INPUT')
            """
        ).fetchall()
        result_envelopes = [
            cast(dict[str, Any], json.loads(str(row["envelope_json"])))
            for row in result_rows
        ]
    finally:
        connection.close()
    returned_edge_ids = {
        str(envelope.get("edge_id"))
        for envelope in result_envelopes
        if envelope.get("edge_id")
    }
    missing = [
        {
            "edge_id": edge["edge_id"],
            "destination": edge["destination"],
            "expected_return_contract_sha256": edge[
                "expected_return_contract_sha256"
            ],
            "state": "MISSING_RETURN",
        }
        for edge in edges
        if edge["edge_id"] not in returned_edge_ids
    ]
    nodes = sorted(
        {
            _node_key(endpoint)
            for edge in edges
            for endpoint in (edge["source"], edge["destination"])
        }
    )
    body = {
        "status": "PASS",
        "project_id": project_id,
        "nodes": nodes,
        "edges": edges,
        "missing_returns": missing,
        "fan_out_supported": True,
        "fan_in_supported": True,
        "cycles_allowed": False,
        "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
    }
    return {**body, "graph_sha256": sha256_bytes(canonical_json_bytes(body))}


def seal_canon_task_result(
    project_root: str | Path,
    *,
    project_id: str,
    edge_id: str,
    source_pointer: Mapping[str, Any],
    evidence_refs: list[Mapping[str, Any]],
    result_payload: Mapping[str, Any],
    schema_id: str,
    schema_version: str,
    schema_sha256: str,
    created_at: str,
    expires_at: str,
) -> dict[str, Any]:
    """Seal a typed result back to the source without propagating local approval."""

    root = _project_root(project_root, project_id=project_id)
    connection = _connect(root)
    try:
        row = connection.execute(
            "SELECT edge_json FROM canon_edge WHERE edge_id=?",
            (_exact_text(edge_id, field="edge_id"),),
        ).fetchone()
        require(
            row is not None,
            "CANON_RESULT_EDGE_NOT_FOUND",
            "A task result requires one exact registered Canon edge.",
            status="BLOCKED",
        )
        edge = _validate_edge(json.loads(str(row["edge_json"])))
    finally:
        connection.close()
    require(
        edge["destination"]["project_id"] == project_id,
        "CANON_RESULT_SOURCE_PROJECT_MISMATCH",
        "Only the linked destination task can seal its result envelope.",
        status="BLOCKED",
    )
    payload = dict(result_payload)
    payload.update(
        {
            "edge_id": edge_id,
            "local_project_hil_decision_propagated": False,
            "local_learning_hil_decision_propagated": False,
            "local_pointer_state_propagated": False,
            "source_write_authority_propagated": False,
        }
    )
    return seal_canon_envelope(
        root,
        project_id=project_id,
        source=edge["destination"],
        destination=edge["source"],
        direction="UPSTREAM",
        canon_type="TASK_RESULT",
        authority_requested="TASK_RESULT",
        contract_id=f"return:{edge_id}",
        contract_version=int(edge["edge_revision"]),
        destination_contract_sha256=edge["expected_return_contract_sha256"],
        schema_id=schema_id,
        schema_version=schema_version,
        schema_sha256=schema_sha256,
        source_pointer=source_pointer,
        evidence_refs=evidence_refs,
        payload=payload,
        permitted_actions=["REPORT_RESULT"],
        dependency_ids=[edge_id],
        expected_return_contract_sha256=None,
        independent_hil_owner_task_uuid=edge["source"]["task_uuid"],
        revision=1,
        idempotency_key=sha256_bytes(
            canonical_json_bytes(
                {
                    "edge_id": edge_id,
                    "result_payload_sha256": sha256_bytes(
                        canonical_json_bytes(payload)
                    ),
                }
            )
        ),
        created_at=created_at,
        expires_at=expires_at,
        edge_id=edge_id,
    )


def seal_canon_state_travel_continuity(
    project_root: str | Path,
    *,
    project_id: str,
    source_task_uuid: str,
    source_task_deep_link: str,
    handoff_id: str,
    accepted_pv: str,
    pointer_generation: int,
    created_at: str,
) -> dict[str, Any]:
    """Seal pending Canon locators as context; never carry a HIL decision token."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    pointer = _load_json(
        root / "active_pointer.json", code="CANON_PROJECT_POINTER_REQUIRED"
    )
    require(
        pointer.get("project_id") == project_id
        and pointer.get("accepted_pv") == accepted_pv
        and int(pointer.get("generation") or 0) == int(pointer_generation),
        "CANON_CONTINUITY_POINTER_MISMATCH",
        "Canon continuity must bind the exact accepted Project Truth pointer.",
        status="MISMATCH",
    )
    exact_task = _exact_text(source_task_uuid, field="source_task_uuid")
    connection = _connect(root)
    try:
        rows = connection.execute(
            """
            SELECT canon_id,canon_sha256,current_state,source_task_uuid,
                   destination_task_uuid,destination_contract_sha256,revision
            FROM canon_packet
            WHERE current_state IN ('PENDING_HIL','MORE_RESEARCH')
              AND (source_task_uuid=? OR destination_task_uuid=?)
            ORDER BY rowid
            """,
            (exact_task, exact_task),
        ).fetchall()
        pending = [dict(row) for row in rows]
        event_head = _last_event_sha256(connection)
    finally:
        connection.close()
    body = {
        "schema": CANON_CONTINUITY_SCHEMA,
        "project_id": project_id,
        "source_task_uuid": exact_task,
        "source_task_deep_link": _exact_text(
            source_task_deep_link, field="source_task_deep_link"
        ),
        "handoff_id": _exact_text(handoff_id, field="handoff_id"),
        "accepted_pv": accepted_pv,
        "pointer_generation": int(pointer_generation),
        "accepted_manifest_sha256": _sha256(
            pointer.get("accepted_manifest_sha256"),
            field="accepted_manifest_sha256",
        ),
        "pending_packets": pending,
        "pending_packets_sha256": sha256_bytes(canonical_json_bytes(pending)),
        "canon_event_head_sha256": event_head,
        "decision_tokens_carried": False,
        "canon_state_mutated": False,
        "project_pointer_moved": False,
        "created_at": cast(str, _timestamp(created_at, field="created_at")),
    }
    snapshot = {
        **body,
        "snapshot_sha256": sha256_bytes(canonical_json_bytes(body)),
    }
    path = (
        _canon_root(root)
        / "state-travel"
        / f"{cast(str, snapshot['snapshot_sha256']).lower()}.json"
    )
    state = _immutable_json(path, snapshot)
    after = _require_authorities_unchanged(root, before, operation="seal_continuity")
    return {
        "status": "PASS",
        "state": state,
        "snapshot": snapshot,
        "snapshot_path": str(path),
        "authority_before": before,
        "authority_after": after,
        "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
    }


def restore_canon_state_travel_continuity(
    project_root: str | Path,
    *,
    project_id: str,
    snapshot: Mapping[str, Any],
    destination_task_uuid: str,
    destination_task_deep_link: str,
    destination_host_session_id: str,
    restored_at: str,
) -> dict[str, Any]:
    """Verify a continuity snapshot once; leave every Canon decision untouched."""

    root = _project_root(project_root, project_id=project_id)
    before = _authority_snapshot(root)
    exact = dict(snapshot)
    require(
        exact.get("schema") == CANON_CONTINUITY_SCHEMA
        and exact.get("project_id") == project_id,
        "CANON_CONTINUITY_SCHEMA_OR_PROJECT_MISMATCH",
        "The Canon continuity snapshot belongs to another schema or project.",
        status="MISMATCH",
    )
    claimed = _sha256(exact.get("snapshot_sha256"), field="snapshot_sha256")
    require(
        claimed == _hash_without(exact, "snapshot_sha256")
        and exact.get("decision_tokens_carried") is False
        and exact.get("canon_state_mutated") is False
        and exact.get("project_pointer_moved") is False,
        "CANON_CONTINUITY_HASH_OR_AUTHORITY_INVALID",
        "The Canon continuity snapshot failed its no-decision authority seal.",
        status="MISMATCH",
    )
    pointer = _load_json(
        root / "active_pointer.json", code="CANON_PROJECT_POINTER_REQUIRED"
    )
    require(
        pointer.get("accepted_pv") == exact.get("accepted_pv")
        and int(pointer.get("generation") or 0)
        == int(exact.get("pointer_generation") or 0)
        and _sha256(
            pointer.get("accepted_manifest_sha256"),
            field="accepted_manifest_sha256",
        )
        == exact.get("accepted_manifest_sha256"),
        "CANON_CONTINUITY_POINTER_STALE",
        "The Project Truth pointer changed since Canon continuity was sealed.",
        status="STALE",
    )
    destination = {
        "task_uuid": _exact_text(destination_task_uuid, field="destination_task_uuid"),
        "task_deep_link": _exact_text(
            destination_task_deep_link, field="destination_task_deep_link"
        ),
        "host_session_id": _exact_text(
            destination_host_session_id, field="destination_host_session_id"
        ),
    }
    consumption_key = sha256_bytes(
        canonical_json_bytes(
            {
                "snapshot_sha256": claimed,
                "destination": destination,
            }
        )
    )
    connection = _connect(root)
    try:
        existing = connection.execute(
            """
            SELECT receipt_json FROM canon_continuity_receipt
            WHERE consumption_key_sha256=?
            """,
            (consumption_key,),
        ).fetchone()
        if existing is not None:
            receipt = cast(dict[str, Any], json.loads(str(existing["receipt_json"])))
            validate_canon_receipt(receipt)
            after = _require_authorities_unchanged(
                root, before, operation="restore_continuity_replay"
            )
            return {
                "status": "PASS",
                "idempotent_reuse": True,
                "receipt": receipt,
                "authority_before": before,
                "authority_after": after,
                "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
            }
    finally:
        connection.close()
    receipt_body = {
        "schema": CANON_RESTORE_RECEIPT_SCHEMA,
        "project_id": project_id,
        "snapshot_sha256": claimed,
        "destination": destination,
        "pending_packets": exact.get("pending_packets") or [],
        "restore_effect": "CONTEXT_ONLY",
        "canon_decision_replayed": False,
        "canon_packet_state_changed": False,
        "project_pointer_moved": False,
        "learning_pointer_moved": False,
        "restored_at": cast(str, _timestamp(restored_at, field="restored_at")),
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    validate_canon_receipt(receipt)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO canon_continuity_receipt(
                consumption_key_sha256,snapshot_sha256,receipt_sha256,receipt_json
            ) VALUES(?,?,?,?)
            """,
            (
                consumption_key,
                claimed,
                receipt["receipt_sha256"],
                canonical_json_bytes(receipt).decode("utf-8"),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    after = _require_authorities_unchanged(root, before, operation="restore_continuity")
    return {
        "status": "PASS",
        "idempotent_reuse": False,
        "receipt": receipt,
        "authority_before": before,
        "authority_after": after,
        "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
    }


def inspect_canon_authority(
    project_root: str | Path,
    *,
    project_id: str,
) -> dict[str, Any]:
    """Inspect contract, packet, graph, dispatch, receipt, and integrity state."""

    root = _project_root(project_root, project_id=project_id)
    connection = _connect(root)
    try:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        counts = {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in (
                "canon_contract",
                "canon_packet",
                "canon_event",
                "canon_receipt",
                "canon_edge",
                "canon_dispatch",
                "canon_backfire_dedup",
                "canon_continuity_receipt",
                "canon_schema_migration",
            )
        }
        event_head = _last_event_sha256(connection)
        states = {
            str(row["current_state"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT current_state,COUNT(*) AS count FROM canon_packet
                GROUP BY current_state ORDER BY current_state
                """
            ).fetchall()
        }
    finally:
        connection.close()
    graph = inspect_canon_task_graph(root, project_id=project_id)
    schema_contract = inspect_canon_schema_contract()
    body = {
        "status": "PASS" if integrity == ["ok"] and not foreign_keys else "FAIL",
        "project_id": project_id,
        "authority": "CANON_INPUT",
        "ledger_path": str(_ledger_path(root)),
        "ledger_sha256": _file_identity(_ledger_path(root)),
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "counts": counts,
        "packet_states": states,
        "event_head_sha256": event_head,
        "graph_sha256": graph["graph_sha256"],
        "schema_contract": {
            "manifest_sha256": schema_contract["manifest_sha256"],
            "ledger_schema": schema_contract["ledger_schema"],
            "ledger_version": schema_contract["ledger_version"],
            "ledger_asset_sha256": schema_contract["ledger_asset_sha256"],
            "ledger_schema_signature_sha256": schema_contract[
                "ledger_schema_signature_sha256"
            ],
            "receipt_registry_schema": schema_contract[
                "receipt_registry_schema"
            ],
            "receipt_registry_version": schema_contract[
                "receipt_registry_version"
            ],
            "receipt_asset_sha256": schema_contract["receipt_asset_sha256"],
            "supported_receipt_schemas": schema_contract[
                "supported_receipt_schemas"
            ],
            "receipt_sha256": schema_contract["receipt_sha256"],
        },
        "project_truth_pointer_sha256": _authority_snapshot(root)[
            "project_truth_pointer_sha256"
        ],
        "learning_pointer_sha256": _authority_snapshot(root)[
            "learning_pointer_sha256"
        ],
        "authority_merge_allowed": False,
        "project_truth_promotion_allowed": False,
        "learning_promotion_allowed": False,
        "source_write_authority_granted": False,
        "authority_effects": dict(_AUTHORITY_EFFECTS_NONE),
    }
    return {**body, "projection_sha256": sha256_bytes(canonical_json_bytes(body))}
