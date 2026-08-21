"""First-class bounded Project Memory authority.

Project Memory stores only content-addressed locators, typed edges, bounded
query receipts, and compaction continuity records.  It is independent from
Project Truth, Agent Learning, Canon, ChatLineage, and host-managed memory.
Legacy locator rows created inside the Agent Learning ledger are copied with
their original identities and immutable migration provenance; all new Memory
writes are owned here.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lanes import CANONICAL_LANE_IDS
from .project_authority import resolved_plan_runtime_path
from .redaction import contains_secret

MEMORY_AUTHORITY_SCHEMA = "evidence-lane.project-memory-authority.v1"
MEMORY_LOCATOR_SCHEMA = "evidence-lane.memory-locator.v1"
MEMORY_EDGE_SCHEMA = "evidence-lane.memory-edge.v1"
MEMORY_HEAD_SCHEMA = "evidence-lane.project-memory-head.v1"
MEMORY_MANIFEST_SCHEMA = "evidence-lane.project-memory-manifest.v1"
MEMORY_QUERY_RECEIPT_SCHEMA = "evidence-lane.memory-query-receipt.v1"
MEMORY_MIGRATION_RECEIPT_SCHEMA = "evidence-lane.memory-migration-receipt.v1"
MEMORY_CHECKPOINT_SCHEMA = "evidence-lane.memory-compaction-checkpoint.v1"
MEMORY_REHYDRATION_SCHEMA = "evidence-lane.memory-rehydration-receipt.v1"
MEMORY_TOOLS_SCHEMA = "evidence-lane.project-memory-tools.v1"

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_PV_RE = re.compile(r"^PV[1-9][0-9]*$")
_TASK_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_LOCATOR_KEYS = {
    "sector",
    "locator_kind",
    "locator_value",
    "revision_sha256",
    "label",
    "search_terms",
}
_EDGE_TYPES = {
    "DERIVED_FROM",
    "EVIDENCES",
    "LEARNED_FROM",
    "MAPS_TO",
    "RELATED_TO",
    "REVOKES",
    "SUPERSEDES",
    "SUPPRESSES",
}
_SUPPRESSING_EDGE_TYPES = {"REVOKES", "SUPERSEDES", "SUPPRESSES"}
_AUTHORITY_PREFIXES = {
    "CHAT_LINEAGE": "chat-lineage://",
    "PLAN": "plan://",
    "PROJECT_TRUTH": "project-truth://",
    "CANON": "canon://",
    "AGENT_LEARNING": "learning://",
    "PROJECT_UNIVERSE": "universe://",
    "RECEIPTS": "receipts://",
    "HOST_MEMORY": "host-memory-import://",
}
MEMORY_SECTOR_LOCATOR_PREFIXES = {
    **_AUTHORITY_PREFIXES,
    **{
        f"LANE_{lane_id.upper()}": f"sector://{lane_id.lower()}/"
        for lane_id in CANONICAL_LANE_IDS
    },
}


def _agent_learning_ledger_path(root: Path) -> Path:
    """Resolve the single AI Learning ledger with a pre-migration fallback."""

    canonical = root / "ai_learning" / "agent-learning.sqlite"
    legacy = root / "learning" / "agent-learning.sqlite"
    require(
        not (canonical.is_file() and legacy.is_file()),
        "MEMORY_AGENT_LEARNING_AUTHORITY_DUPLICATED",
        "Project Memory found both canonical and legacy Agent Learning ledgers.",
        status="MISMATCH",
        canonical_path=str(canonical),
        legacy_path=str(legacy),
    )
    return canonical if canonical.is_file() or not legacy.is_file() else legacy


def _sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        bool(_SHA256_RE.fullmatch(exact)),
        "MEMORY_SHA256_INVALID",
        "A Project Memory identity is not one exact SHA-256.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _timestamp(value: Any, *, field: str) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact),
        "MEMORY_TIMESTAMP_REQUIRED",
        "A Project Memory timestamp is required.",
        status="BLOCKED",
        field=field,
    )
    try:
        parsed = datetime.fromisoformat(exact)
    except ValueError as exc:
        require(
            False,
            "MEMORY_TIMESTAMP_INVALID",
            "A Project Memory timestamp is not valid ISO-8601.",
            status="MISMATCH",
            field=field,
        )
        raise AssertionError("unreachable") from exc
    require(
        parsed.tzinfo is not None,
        "MEMORY_TIMESTAMP_TIMEZONE_REQUIRED",
        "A Project Memory timestamp requires an explicit timezone.",
        status="MISMATCH",
        field=field,
    )
    return (
        parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _project_root(project_root: str | Path, *, project_id: str) -> Path:
    root = Path(project_root).resolve()
    require(
        root.name == project_id,
        "MEMORY_PROJECT_ROOT_MISMATCH",
        "The Project Memory root does not match the exact project identity.",
        status="MISMATCH",
        project_id=project_id,
    )
    return root


def _memory_root(root: Path) -> Path:
    return root / "memory"


def _database_path(root: Path) -> Path:
    return _memory_root(root) / "memory.sqlite"


def _schema_asset() -> tuple[Path, str]:
    candidates = (
        Path(__file__).resolve().parent
        / "schemas"
        / "memory"
        / "project-memory.v1.sql",
        Path(__file__).resolve().parents[3]
        / "schemas"
        / "memory"
        / "project-memory.v1.sql",
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    require(
        path is not None,
        "MEMORY_SCHEMA_ASSET_MISSING",
        "The first-class Project Memory SQL asset is missing.",
        status="MISMATCH",
    )
    exact = cast(Path, path)
    return exact, exact.read_text(encoding="utf-8")


def _connect(root: Path) -> sqlite3.Connection:
    path = _database_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    schema_path, sql = _schema_asset()
    ddl_sha256 = sha256_file(schema_path)
    metadata = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_schema_metadata'"
    ).fetchone()
    if metadata is None:
        try:
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO memory_schema_metadata VALUES(1,?,?,?)",
                (MEMORY_AUTHORITY_SCHEMA, 1, ddl_sha256),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
    else:
        row = connection.execute(
            "SELECT schema_id,schema_version,ddl_sha256 "
            "FROM memory_schema_metadata WHERE singleton=1"
        ).fetchone()
        require(
            row is not None
            and str(row["schema_id"]) == MEMORY_AUTHORITY_SCHEMA
            and int(row["schema_version"]) == 1
            and str(row["ddl_sha256"]) == ddl_sha256,
            "MEMORY_SCHEMA_VERSION_MISMATCH",
            "The Project Memory schema is missing, newer, or byte-drifted.",
            status="MISMATCH",
        )
    return connection


def _json(path: Path, *, code: str) -> dict[str, Any]:
    require(path.is_file(), code, "A required Project Memory input is missing.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        require(
            False,
            code,
            "A required Project Memory input is invalid JSON.",
            status="MISMATCH",
            path=str(path),
            error_type=type(exc).__name__,
        )
        raise AssertionError("unreachable") from exc
    require(isinstance(value, dict), code, "Project Memory JSON must be one object.")
    return cast(dict[str, Any], value)


def _immutable_json(path: Path, value: dict[str, Any]) -> None:
    encoded = canonical_json_bytes(value)
    if path.exists():
        require(
            path.read_bytes() == encoded,
            "MEMORY_IMMUTABLE_ARTIFACT_CONFLICT",
            "A content-addressed Project Memory artifact has different bytes.",
            status="MISMATCH",
            path=str(path),
        )
        return
    atomic_write_json(path, value)


def _normalize_locator(
    value: Mapping[str, Any], *, project_id: str, recorded_at: str
) -> dict[str, Any]:
    require(
        set(value) == _LOCATOR_KEYS,
        "MEMORY_LOCATOR_SHAPE_INVALID",
        "A Project Memory locator requires only the governed locator fields.",
        status="BLOCKED",
    )
    sector = str(value["sector"]).strip().upper()
    locator_kind = str(value["locator_kind"]).strip().upper()
    locator_value = str(value["locator_value"]).strip()
    label = str(value["label"]).strip()
    terms = value["search_terms"]
    require(
        sector in MEMORY_SECTOR_LOCATOR_PREFIXES
        and bool(re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", locator_kind))
        and locator_value.startswith(MEMORY_SECTOR_LOCATOR_PREFIXES[sector])
        and len(locator_value) <= 512
        and 1 <= len(label) <= 200
        and isinstance(terms, list)
        and 1 <= len(terms) <= 24,
        "MEMORY_LOCATOR_BOUNDARY_INVALID",
        "A Project Memory locator must use one typed URI and bounded labels.",
        status="BLOCKED",
    )
    exact_terms = [str(item).strip() for item in terms]
    require(
        all(exact_terms)
        and all(len(item) <= 192 for item in exact_terms)
        and len(exact_terms) == len(set(exact_terms)),
        "MEMORY_SEARCH_TERMS_INVALID",
        "Project Memory search terms must be nonempty, bounded, and unique.",
        status="BLOCKED",
    )
    revision_sha256 = _sha256(
        value["revision_sha256"], field="memory_locator_revision_sha256"
    )
    search_text = " ".join([label, sector, locator_kind, *exact_terms])
    require(
        len(search_text) <= 2048
        and not contains_secret(
            {
                "locator_value": locator_value,
                "label": label,
                "search_terms": exact_terms,
            }
        ),
        "MEMORY_LOCATOR_CONTENT_BLOCKED",
        "Secrets or unbounded source content cannot enter Project Memory.",
        status="BLOCKED",
    )
    body: dict[str, Any] = {
        "schema": MEMORY_LOCATOR_SCHEMA,
        "project_id": project_id,
        "sector": sector,
        "locator_kind": locator_kind,
        "locator_value": locator_value,
        "revision_sha256": revision_sha256,
        "label": label,
        "search_text": search_text,
        "recorded_at": recorded_at,
        "raw_payload_stored": False,
        "private_reasoning_stored": False,
    }
    digest = sha256_bytes(canonical_json_bytes(body))
    return {
        **body,
        "locator_id": f"memloc_{digest[:24].lower()}",
        "locator_sha256": digest,
    }


def _verify_locator(locator: Mapping[str, Any]) -> None:
    claimed = _sha256(locator.get("locator_sha256"), field="locator_sha256")
    body = {
        key: value
        for key, value in locator.items()
        if key not in {"locator_id", "locator_sha256"}
    }
    sector = str(locator.get("sector") or "")
    require(
        locator.get("schema") == MEMORY_LOCATOR_SCHEMA
        and sector in MEMORY_SECTOR_LOCATOR_PREFIXES
        and str(locator.get("locator_value") or "").startswith(
            MEMORY_SECTOR_LOCATOR_PREFIXES[sector]
        )
        and locator.get("raw_payload_stored") is False
        and locator.get("private_reasoning_stored") is False
        and claimed == sha256_bytes(canonical_json_bytes(body))
        and locator.get("locator_id") == f"memloc_{claimed[:24].lower()}",
        "MEMORY_LOCATOR_IDENTITY_MISMATCH",
        "A stored Project Memory locator failed its bounded identity contract.",
        status="MISMATCH",
    )


def _insert_locator(
    connection: sqlite3.Connection,
    locator: dict[str, Any],
    *,
    provenance: Mapping[str, Any],
) -> bool:
    _verify_locator(locator)
    existing = connection.execute(
        "SELECT locator_json,provenance_json FROM memory_locator WHERE locator_id=?",
        (locator["locator_id"],),
    ).fetchone()
    if existing is not None:
        require(
            canonical_json_bytes(json.loads(str(existing["locator_json"])))
            == canonical_json_bytes(locator),
            "MEMORY_LOCATOR_IDENTITY_CONFLICT",
            "One Project Memory locator ID maps to different bytes.",
            status="MISMATCH",
        )
        return False
    connection.execute(
        """
        INSERT INTO memory_locator(
            locator_id,project_id,sector,locator_kind,locator_value,
            revision_sha256,label,search_text,locator_sha256,locator_json,
            provenance_json,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            locator["locator_id"],
            locator["project_id"],
            locator["sector"],
            locator["locator_kind"],
            locator["locator_value"],
            locator["revision_sha256"],
            locator["label"],
            locator["search_text"],
            locator["locator_sha256"],
            canonical_json_bytes(locator).decode("utf-8"),
            canonical_json_bytes(dict(provenance)).decode("utf-8"),
            locator["recorded_at"],
        ),
    )
    connection.execute(
        "INSERT INTO memory_locator_fts VALUES(?,?,?,?,?,?)",
        (
            locator["locator_id"],
            locator["project_id"],
            locator["sector"],
            locator["locator_kind"],
            locator["label"],
            locator["search_text"],
        ),
    )
    return True


def _resolve_locator(
    connection: sqlite3.Connection,
    locator: dict[str, Any],
    *,
    provenance: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Reuse one semantic locator even when a later refresh has a new timestamp."""

    row = connection.execute(
        """
        SELECT locator_json FROM memory_locator
        WHERE project_id=? AND sector=? AND locator_kind=?
          AND locator_value=? AND revision_sha256=? AND label=? AND search_text=?
        ORDER BY recorded_at, locator_id LIMIT 1
        """,
        (
            locator["project_id"],
            locator["sector"],
            locator["locator_kind"],
            locator["locator_value"],
            locator["revision_sha256"],
            locator["label"],
            locator["search_text"],
        ),
    ).fetchone()
    if row is not None:
        existing = cast(dict[str, Any], json.loads(str(row["locator_json"])))
        _verify_locator(existing)
        return existing, False
    return locator, _insert_locator(connection, locator, provenance=provenance)


def _edge(
    *,
    project_id: str,
    source_locator_id: str,
    target_locator_id: str,
    edge_type: str,
    evidence_sha256: str,
    recorded_at: str,
) -> dict[str, Any]:
    exact_type = str(edge_type).strip().upper()
    require(
        exact_type in _EDGE_TYPES and source_locator_id != target_locator_id,
        "MEMORY_EDGE_INVALID",
        "A Project Memory edge must be typed and connect distinct locators.",
        status="BLOCKED",
    )
    body: dict[str, Any] = {
        "schema": MEMORY_EDGE_SCHEMA,
        "project_id": project_id,
        "source_locator_id": source_locator_id,
        "target_locator_id": target_locator_id,
        "edge_type": exact_type,
        "evidence_sha256": _sha256(
            evidence_sha256, field="memory_edge_evidence_sha256"
        ),
        "recorded_at": recorded_at,
        "raw_payload_stored": False,
        "private_reasoning_stored": False,
        "project_truth_pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
    }
    digest = sha256_bytes(canonical_json_bytes(body))
    return {
        **body,
        "edge_id": f"memedge_{digest[:24].lower()}",
        "edge_sha256": digest,
    }


def _insert_edge(connection: sqlite3.Connection, edge: dict[str, Any]) -> bool:
    existing = connection.execute(
        "SELECT edge_json FROM memory_edge WHERE edge_id=?", (edge["edge_id"],)
    ).fetchone()
    if existing is not None:
        require(
            canonical_json_bytes(json.loads(str(existing["edge_json"])))
            == canonical_json_bytes(edge),
            "MEMORY_EDGE_IDENTITY_CONFLICT",
            "One Project Memory edge ID maps to different bytes.",
            status="MISMATCH",
        )
        return False
    connection.execute(
        """
        INSERT INTO memory_edge(
            edge_id,project_id,source_locator_id,target_locator_id,edge_type,
            evidence_sha256,edge_sha256,edge_json,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            edge["edge_id"],
            edge["project_id"],
            edge["source_locator_id"],
            edge["target_locator_id"],
            edge["edge_type"],
            edge["evidence_sha256"],
            edge["edge_sha256"],
            canonical_json_bytes(edge).decode("utf-8"),
            edge["recorded_at"],
        ),
    )
    return True


def _resolve_edge(
    connection: sqlite3.Connection, edge: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Reuse one semantic edge instead of duplicating it on a later refresh."""

    row = connection.execute(
        """
        SELECT edge_json FROM memory_edge
        WHERE project_id=? AND source_locator_id=? AND target_locator_id=?
          AND edge_type=? AND evidence_sha256=?
        ORDER BY recorded_at, edge_id LIMIT 1
        """,
        (
            edge["project_id"],
            edge["source_locator_id"],
            edge["target_locator_id"],
            edge["edge_type"],
            edge["evidence_sha256"],
        ),
    ).fetchone()
    if row is not None:
        return cast(dict[str, Any], json.loads(str(row["edge_json"]))), False
    return edge, _insert_edge(connection, edge)


def _legacy_memory_rows(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    path = _agent_learning_ledger_path(root)
    if not path.is_file():
        return [], [], None
    digest = sha256_file(path)
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"memory_locator", "memory_edge"} <= tables:
            return [], [], digest
        locators = [
            cast(dict[str, Any], json.loads(str(row["locator_json"])))
            for row in connection.execute(
                "SELECT locator_json FROM memory_locator ORDER BY locator_id"
            ).fetchall()
        ]
        edges = [
            cast(dict[str, Any], json.loads(str(row["edge_json"])))
            for row in connection.execute(
                "SELECT edge_json FROM memory_edge ORDER BY edge_id"
            ).fetchall()
        ]
        return locators, edges, digest
    finally:
        connection.close()


def _migrate_legacy_memory(
    connection: sqlite3.Connection, *, root: Path, project_id: str
) -> dict[str, Any]:
    locators, edges, source_sha256 = _legacy_memory_rows(root)
    if source_sha256 is None:
        return {
            "state": "NO_LEGACY_MEMORY_LEDGER",
            "locator_count": 0,
            "edge_count": 0,
            "receipt_sha256": None,
        }
    inserted_locator_count = 0
    inserted_edge_count = 0
    for locator in locators:
        require(
            locator.get("project_id") == project_id,
            "MEMORY_LEGACY_PROJECT_MISMATCH",
            "A legacy Memory locator belongs to another project.",
            status="MISMATCH",
        )
        inserted_locator_count += int(
            _insert_locator(
                connection,
                locator,
                provenance={
                    "source_authority": "LEGACY_AGENT_LEARNING_MEMORY_V2",
                    "source_ledger_sha256": source_sha256,
                    "logical_record_id": locator["locator_id"],
                    "copied_not_moved": True,
                },
            )
        )
    for edge in edges:
        inserted_edge_count += int(_insert_edge(connection, edge))
    body = {
        "schema": MEMORY_MIGRATION_RECEIPT_SCHEMA,
        "status": "PASS",
        "project_id": project_id,
        "source_authority": "LEGACY_AGENT_LEARNING_MEMORY_V2",
        "source_ledger_sha256": source_sha256,
        "legacy_locator_count": len(locators),
        "legacy_edge_count": len(edges),
        "inserted_locator_count": inserted_locator_count,
        "inserted_edge_count": inserted_edge_count,
        "legacy_rows_retained": True,
        "active_memory_owner": "PROJECT_MEMORY",
        "logical_loss": False,
        "logical_duplication": False,
        "project_truth_pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(body))
    receipt = {**body, "receipt_sha256": receipt_sha256}
    existing = connection.execute(
        "SELECT receipt_json FROM memory_migration_receipt WHERE source_ledger_sha256=?",
        (source_sha256,),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO memory_migration_receipt VALUES(?,?,?,?,?,?)",
            (
                source_sha256,
                project_id,
                len(locators),
                len(edges),
                receipt_sha256,
                canonical_json_bytes(receipt).decode("utf-8"),
            ),
        )
    else:
        previous = cast(dict[str, Any], json.loads(str(existing["receipt_json"])))
        require(
            previous["legacy_locator_count"] == len(locators)
            and previous["legacy_edge_count"] == len(edges),
            "MEMORY_LEGACY_MIGRATION_REPLAY_MISMATCH",
            "A repeated legacy Memory migration changed its logical source rows.",
            status="MISMATCH",
        )
        receipt_sha256 = str(previous["receipt_sha256"])
    return {
        "state": "LEGACY_MEMORY_COPIED" if locators or edges else "LEGACY_MEMORY_EMPTY",
        "locator_count": len(locators),
        "edge_count": len(edges),
        "inserted_locator_count": inserted_locator_count,
        "inserted_edge_count": inserted_edge_count,
        "receipt_sha256": receipt_sha256,
        "source_ledger_sha256": source_sha256,
    }


def _plan_identity(root: Path, *, active_plan_task_id: str) -> tuple[str, str]:
    path = resolved_plan_runtime_path(root)
    require(
        path.is_file(),
        "MEMORY_PLAN_AUTHORITY_REQUIRED",
        "Project Memory requires the live Plan SQLite authority.",
        status="BLOCKED",
    )
    digest = sha256_file(path)
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT task_id,task_contract_sha256 FROM plan_execution_row "
            "WHERE lifecycle_status='ACTIVE' AND effective_for_execution=1"
        ).fetchall()
    finally:
        connection.close()
    require(
        len(rows) == 1 and str(rows[0]["task_id"]) == active_plan_task_id,
        "MEMORY_ACTIVE_PLAN_TASK_MISMATCH",
        "Project Memory requires one exact active Plan task.",
        status="MISMATCH",
        expected_active_task_id=active_plan_task_id,
        active_task_ids=[str(row["task_id"]) for row in rows],
    )
    return digest, _sha256(rows[0]["task_contract_sha256"], field="task_contract")


def _authority_locators(
    root: Path,
    *,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    accepted_manifest_sha256: str,
    active_plan_task_id: str,
    plan_sha256: str,
    task_contract_sha256: str,
    lineage_head_sha256: str,
    recorded_at: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    layout_path = root / "project_authority.json"
    layout = _json(layout_path, code="MEMORY_PROJECT_LAYOUT_REQUIRED")
    pointer = cast(dict[str, Any], layout.get("accepted_pointer") or {})
    require(
        layout.get("project_id") == project_id
        and pointer.get("accepted_pv") == accepted_pv
        and int(pointer.get("generation") or 0) == pointer_generation
        and pointer.get("accepted_manifest_sha256") == accepted_manifest_sha256,
        "MEMORY_ACCEPTED_POINTER_MISMATCH",
        "Project Memory cannot bind a different accepted pointer baseline.",
        status="MISMATCH",
    )
    values: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def add(value: dict[str, Any], source: str, source_sha256: str) -> None:
        values.append(
            (
                _normalize_locator(
                    value, project_id=project_id, recorded_at=recorded_at
                ),
                {
                    "source_authority": source,
                    "source_sha256": source_sha256,
                    "bounded_locator_only": True,
                },
            )
        )

    add(
        {
            "sector": "PROJECT_TRUTH",
            "locator_kind": "ACCEPTED_POINTER",
            "locator_value": (
                f"project-truth://{project_id}/{accepted_pv}/generation/{pointer_generation}"
            ),
            "revision_sha256": accepted_manifest_sha256,
            "label": f"{accepted_pv} generation {pointer_generation} accepted baseline",
            "search_terms": ["project", "truth", "accepted", accepted_pv.lower()],
        },
        "PROJECT_AUTHORITY_LAYOUT",
        sha256_file(layout_path),
    )
    add(
        {
            "sector": "PLAN",
            "locator_kind": "ACTIVE_TASK",
            "locator_value": f"plan://task/{active_plan_task_id}",
            "revision_sha256": task_contract_sha256,
            "label": f"Active Plan task {active_plan_task_id}",
            "search_terms": ["plan", "active", "task", active_plan_task_id],
        },
        "PLAN_SQLITE",
        plan_sha256,
    )
    for lane_id in CANONICAL_LANE_IDS:
        path = root / "sectors" / lane_id / "authority.ref.json"
        require(
            path.is_file(),
            "MEMORY_SECTOR_REFERENCE_REQUIRED",
            "Project Memory requires every governed sector reference.",
            status="BLOCKED",
            lane_id=lane_id,
        )
        digest = sha256_file(path)
        add(
            {
                "sector": f"LANE_{lane_id.upper()}",
                "locator_kind": "SECTOR_AUTHORITY",
                "locator_value": f"sector://{lane_id.lower()}/authority",
                "revision_sha256": digest,
                "label": f"{lane_id} project sector authority",
                "search_terms": ["sector", "lane", lane_id.lower(), "authority"],
            },
            "PROJECT_SECTOR_REFERENCE",
            digest,
        )
    learning_path = _agent_learning_ledger_path(root)
    if learning_path.is_file():
        digest = sha256_file(learning_path)
        add(
            {
                "sector": "AGENT_LEARNING",
                "locator_kind": "LEARNING_AUTHORITY",
                "locator_value": f"learning://authority/{digest}",
                "revision_sha256": digest,
                "label": "Agent Learning authority",
                "search_terms": ["agent", "learning", "authority", accepted_pv.lower()],
            },
            "AGENT_LEARNING_SQLITE",
            digest,
        )
    canon_path = root / "canon" / "consequence-graph-current.json"
    if canon_path.is_file():
        digest = sha256_file(canon_path)
        add(
            {
                "sector": "CANON",
                "locator_kind": "CONSEQUENCE_GRAPH_POINTER",
                "locator_value": f"canon://consequence-graph/{digest}",
                "revision_sha256": digest,
                "label": "Canon consequence graph pointer",
                "search_terms": ["canon", "consequence", "graph", "pointer"],
            },
            "CANON_CONSEQUENCE_POINTER",
            digest,
        )
    universe_files = sorted((root / "universe").glob("*.json"))
    if universe_files:
        digest = sha256_bytes(
            canonical_json_bytes(
                {path.name: sha256_file(path) for path in universe_files[:128]}
            )
        )
        add(
            {
                "sector": "PROJECT_UNIVERSE",
                "locator_kind": "UNIVERSE_AUTHORITY",
                "locator_value": f"universe://authority/{digest}",
                "revision_sha256": digest,
                "label": "Project Universe authority",
                "search_terms": ["project", "universe", "graph", "telemetry"],
            },
            "PROJECT_UNIVERSE_JSON_SET",
            digest,
        )
    receipt_files = sorted((root / "receipts").glob("*.json"))
    if receipt_files:
        digest = sha256_bytes(
            canonical_json_bytes(
                {path.name: sha256_file(path) for path in receipt_files[:512]}
            )
        )
        add(
            {
                "sector": "RECEIPTS",
                "locator_kind": "RECEIPT_AUTHORITY",
                "locator_value": f"receipts://authority/{digest}",
                "revision_sha256": digest,
                "label": "Project receipt authority",
                "search_terms": ["project", "receipts", "evidence", "authority"],
            },
            "PROJECT_RECEIPT_SET",
            digest,
        )
    add(
        {
            "sector": "CHAT_LINEAGE",
            "locator_kind": "LINEAGE_HEAD",
            "locator_value": f"chat-lineage://head/{lineage_head_sha256}",
            "revision_sha256": lineage_head_sha256,
            "label": "Bounded ChatLineage head",
            "search_terms": ["chat", "lineage", "head", "continuity"],
        },
        "CHAT_LINEAGE_HEAD",
        lineage_head_sha256,
    )
    return values


def _current_head(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT head_json FROM memory_authority_head ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    require(
        row is not None,
        "MEMORY_HEAD_REQUIRED",
        "Project Memory has not been bootstrapped.",
        status="BLOCKED",
    )
    return cast(dict[str, Any], json.loads(str(row["head_json"])))


def _render_mermaid(locators: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    lines = ["flowchart LR"]
    for locator in locators:
        label = f"{locator['sector']}\\n{locator['label']}".replace('"', "'")
        lines.append(f'  {locator["locator_id"]}["{label}"]')
    for edge in edges:
        lines.append(
            f"  {edge['source_locator_id']} -->|{edge['edge_type']}| "
            f"{edge['target_locator_id']}"
        )
    return "\n".join(lines) + "\n"


def _render_dot(locators: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    lines = ["digraph ProjectMemory {", "  rankdir=LR;"]
    for locator in locators:
        label = f"{locator['sector']}\\n{locator['label']}".replace('"', "'")
        lines.append(f'  {locator["locator_id"]} [label="{label}"];')
    for edge in edges:
        lines.append(
            f"  {edge['source_locator_id']} -> {edge['target_locator_id']} "
            f'[label="{edge["edge_type"]}"];'
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _refresh_projections(root: Path) -> dict[str, Any]:
    connection = _connect(root)
    try:
        head = _current_head(connection)
        locators = [
            cast(dict[str, Any], json.loads(str(row["locator_json"])))
            for row in connection.execute(
                "SELECT locator_json FROM memory_locator ORDER BY locator_id"
            ).fetchall()
        ]
        edges = [
            cast(dict[str, Any], json.loads(str(row["edge_json"])))
            for row in connection.execute(
                "SELECT edge_json FROM memory_edge ORDER BY edge_id"
            ).fetchall()
        ]
    finally:
        connection.close()
    memory_root = _memory_root(root)
    atomic_write_json(memory_root / "head.json", head)
    atomic_write_json(
        memory_root / "memory.json",
        {
            "schema": MEMORY_AUTHORITY_SCHEMA,
            "project_id": head["project_id"],
            "memory_head_sha256": head["head_sha256"],
            "locators": locators,
            "edges": edges,
            "raw_source_payloads_stored": False,
            "private_reasoning_stored": False,
        },
    )
    atomic_write_json(
        memory_root / "memory.tools.json",
        {
            "schema": MEMORY_TOOLS_SCHEMA,
            "query": {
                "engine": "SQLITE_FTS5_BM25",
                "bounded_limit": [1, 20],
                "sector_filter": sorted(MEMORY_SECTOR_LOCATOR_PREFIXES),
            },
            "compaction": {
                "precompact": "SEAL_EXACT_MEMORY_HEAD_AND_BOUNDED_LOCATORS",
                "postcompact": "VERIFY_HEAD_AND_REHYDRATE_BOUNDED_LOCATORS",
                "controls_host_wording": False,
            },
            "public_tool_count_changed": False,
        },
    )
    atomic_write_bytes(
        memory_root / "memory.mmd", _render_mermaid(locators, edges).encode("utf-8")
    )
    atomic_write_bytes(
        memory_root / "memory.dot", _render_dot(locators, edges).encode("utf-8")
    )
    member_names = (
        "memory.sqlite",
        "memory.json",
        "memory.mmd",
        "memory.dot",
        "memory.tools.json",
        "head.json",
    )
    members = [
        {
            "path": name,
            "bytes": (memory_root / name).stat().st_size,
            "sha256": sha256_file(memory_root / name),
        }
        for name in member_names
    ]
    manifest = {
        "schema": MEMORY_MANIFEST_SCHEMA,
        "project_id": head["project_id"],
        "memory_head_sha256": head["head_sha256"],
        "counts": {"locator_count": len(locators), "edge_count": len(edges)},
        "members": members,
        "raw_source_payloads_stored": False,
        "full_memory_loaded_into_model_context": False,
    }
    atomic_write_json(memory_root / "memory.manifest.json", manifest)
    return {
        "memory_head_sha256": head["head_sha256"],
        "manifest_sha256": sha256_file(memory_root / "memory.manifest.json"),
        "counts": manifest["counts"],
    }


def _authority_effects(project_memory: str = "NONE") -> dict[str, str]:
    return {
        "project_truth": "NONE",
        "canon_input": "NONE",
        "agent_learning": "NONE",
        "project_memory": project_memory,
        "chat_lineage": "NONE",
        "host_entry_continuity": "NONE",
    }


def bootstrap_project_memory(
    project_root: str | Path,
    *,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    accepted_manifest_sha256: str,
    active_plan_task_id: str,
    lineage_head_sha256: str,
    recorded_at: str,
) -> dict[str, Any]:
    """Build or refresh the bounded Memory dossier from exact live authorities."""

    root = _project_root(project_root, project_id=project_id)
    exact_pv = str(accepted_pv).strip().upper()
    require(
        bool(_PV_RE.fullmatch(exact_pv)) and pointer_generation > 0,
        "MEMORY_ACCEPTED_POINTER_INVALID",
        "Project Memory requires one accepted PV and positive generation.",
        status="MISMATCH",
    )
    exact_manifest = _sha256(accepted_manifest_sha256, field="accepted_manifest_sha256")
    exact_lineage = _sha256(lineage_head_sha256, field="lineage_head_sha256")
    exact_recorded_at = _timestamp(recorded_at, field="memory_recorded_at")
    plan_sha256, task_contract_sha256 = _plan_identity(
        root, active_plan_task_id=active_plan_task_id
    )
    authority_locators = _authority_locators(
        root,
        project_id=project_id,
        accepted_pv=exact_pv,
        pointer_generation=pointer_generation,
        accepted_manifest_sha256=exact_manifest,
        active_plan_task_id=active_plan_task_id,
        plan_sha256=plan_sha256,
        task_contract_sha256=task_contract_sha256,
        lineage_head_sha256=exact_lineage,
        recorded_at=exact_recorded_at,
    )
    connection = _connect(root)
    inserted_locators = 0
    inserted_edges = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        migration = _migrate_legacy_memory(connection, root=root, project_id=project_id)
        resolved_authority_locators: list[
            tuple[dict[str, Any], dict[str, Any]]
        ] = []
        for locator, provenance in authority_locators:
            resolved, inserted = _resolve_locator(
                connection, locator, provenance=provenance
            )
            inserted_locators += int(inserted)
            resolved_authority_locators.append((resolved, provenance))
        authority_locators = resolved_authority_locators
        by_sector = {locator["sector"]: locator for locator, _ in authority_locators}
        truth = by_sector["PROJECT_TRUTH"]
        plan = by_sector["PLAN"]
        structural_edges = [
            _edge(
                project_id=project_id,
                source_locator_id=truth["locator_id"],
                target_locator_id=plan["locator_id"],
                edge_type="MAPS_TO",
                evidence_sha256=exact_manifest,
                recorded_at=exact_recorded_at,
            )
        ]
        for locator, _ in authority_locators:
            if locator["locator_id"] in {truth["locator_id"], plan["locator_id"]}:
                continue
            structural_edges.append(
                _edge(
                    project_id=project_id,
                    source_locator_id=plan["locator_id"],
                    target_locator_id=locator["locator_id"],
                    edge_type="RELATED_TO",
                    evidence_sha256=str(locator["revision_sha256"]),
                    recorded_at=exact_recorded_at,
                )
            )
        for edge in structural_edges:
            _, inserted = _resolve_edge(connection, edge)
            inserted_edges += int(inserted)
        locator_hashes = [
            str(row[0])
            for row in connection.execute(
                "SELECT locator_sha256 FROM memory_locator ORDER BY locator_id"
            ).fetchall()
        ]
        edge_hashes = [
            str(row[0])
            for row in connection.execute(
                "SELECT edge_sha256 FROM memory_edge ORDER BY edge_id"
            ).fetchall()
        ]
        head_body = {
            "schema": MEMORY_HEAD_SCHEMA,
            "project_id": project_id,
            "accepted_pv": exact_pv,
            "pointer_generation": pointer_generation,
            "accepted_manifest_sha256": exact_manifest,
            "active_plan_task_id": active_plan_task_id,
            "plan_runtime_projection_sha256": plan_sha256,
            "lineage_head_sha256": exact_lineage,
            "locator_count": len(locator_hashes),
            "edge_count": len(edge_hashes),
            "locator_set_sha256": sha256_bytes(canonical_json_bytes(locator_hashes)),
            "edge_set_sha256": sha256_bytes(canonical_json_bytes(edge_hashes)),
            "recorded_at": exact_recorded_at,
            "raw_source_payloads_stored": False,
            "project_truth_pointer_moved": False,
        }
        head_sha256 = sha256_bytes(canonical_json_bytes(head_body))
        head = {**head_body, "head_sha256": head_sha256}
        existing = connection.execute(
            "SELECT head_json FROM memory_authority_head WHERE head_sha256=?",
            (head_sha256,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO memory_authority_head VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    head_sha256,
                    project_id,
                    len(locator_hashes),
                    len(edge_hashes),
                    exact_pv,
                    pointer_generation,
                    exact_manifest,
                    active_plan_task_id,
                    plan_sha256,
                    exact_lineage,
                    canonical_json_bytes(head).decode("utf-8"),
                    exact_recorded_at,
                ),
            )
        else:
            require(
                canonical_json_bytes(json.loads(str(existing["head_json"])))
                == canonical_json_bytes(head),
                "MEMORY_HEAD_IDENTITY_CONFLICT",
                "One Project Memory head maps to different bytes.",
                status="MISMATCH",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    projection = _refresh_projections(root)
    return {
        "status": "PASS",
        "state": (
            "MEMORY_AUTHORITY_CREATED"
            if inserted_locators or inserted_edges
            else "MEMORY_AUTHORITY_REUSED"
        ),
        "project_id": project_id,
        "memory_head_sha256": projection["memory_head_sha256"],
        "manifest_sha256": projection["manifest_sha256"],
        "counts": projection["counts"],
        "legacy_migration": migration,
        "inserted_locator_count": inserted_locators,
        "inserted_edge_count": inserted_edges,
        "authority_effects": _authority_effects("MEMORY_REFRESHED"),
        "raw_source_payloads_stored": False,
        "full_memory_loaded_into_model_context": False,
        "project_candidate_created": False,
        "hil_invoked": False,
        "project_truth_pointer_moved": False,
        "controls_codex_host_wording": False,
    }


def inspect_project_memory(
    project_root: str | Path, *, project_id: str
) -> dict[str, Any]:
    """Inspect the current Memory head and projection hashes without graph output."""

    root = _project_root(project_root, project_id=project_id)
    path = _database_path(root)
    if not path.is_file():
        return {
            "status": "PASS",
            "state": "NO_PROJECT_MEMORY",
            "project_id": project_id,
            "counts": {"locator_count": 0, "edge_count": 0},
            "authority_effects": _authority_effects(),
            "full_memory_loaded_into_model_context": False,
        }
    connection = _connect(root)
    try:
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        head = _current_head(connection)
    finally:
        connection.close()
    require(
        integrity == ["ok"] and not foreign_keys,
        "MEMORY_SQLITE_INVALID",
        "Project Memory failed SQLite integrity or foreign-key checks.",
        status="FAIL",
    )
    manifest_path = _memory_root(root) / "memory.manifest.json"
    manifest = _json(manifest_path, code="MEMORY_MANIFEST_REQUIRED")
    require(
        manifest.get("schema") == MEMORY_MANIFEST_SCHEMA
        and manifest.get("project_id") == project_id
        and manifest.get("memory_head_sha256") == head["head_sha256"],
        "MEMORY_MANIFEST_MISMATCH",
        "The Project Memory manifest does not match the current head.",
        status="MISMATCH",
    )
    for member in manifest.get("members") or []:
        member_path = _memory_root(root) / str(member.get("path") or "")
        require(
            member_path.is_file() and sha256_file(member_path) == member.get("sha256"),
            "MEMORY_MANIFEST_MEMBER_MISMATCH",
            "A Project Memory projection member failed its hash check.",
            status="MISMATCH",
            member=member.get("path"),
        )
    return {
        "status": "PASS",
        "state": "PROJECT_MEMORY_READY",
        "project_id": project_id,
        "memory_head_sha256": head["head_sha256"],
        "accepted_pv": head["accepted_pv"],
        "pointer_generation": head["pointer_generation"],
        "active_plan_task_id": head["active_plan_task_id"],
        "plan_runtime_projection_sha256": head["plan_runtime_projection_sha256"],
        "lineage_head_sha256": head["lineage_head_sha256"],
        "counts": {
            "locator_count": head["locator_count"],
            "edge_count": head["edge_count"],
        },
        "manifest_sha256": sha256_file(manifest_path),
        "schema_asset_sha256": sha256_file(_schema_asset()[0]),
        "authority_effects": _authority_effects(),
        "raw_source_payloads_stored": False,
        "full_memory_loaded_into_model_context": False,
        "project_truth_pointer_moved": False,
    }


def record_memory_link(
    project_root: str | Path,
    *,
    project_id: str,
    source: dict[str, Any],
    target: dict[str, Any],
    edge_type: str,
    evidence_sha256: str,
    recorded_at: str,
) -> dict[str, Any]:
    """Append one Memory-owned locator edge without storing source payloads."""

    root = _project_root(project_root, project_id=project_id)
    exact_recorded_at = _timestamp(recorded_at, field="memory_link_recorded_at")
    source_locator = _normalize_locator(
        source, project_id=project_id, recorded_at=exact_recorded_at
    )
    target_locator = _normalize_locator(
        target, project_id=project_id, recorded_at=exact_recorded_at
    )
    connection = _connect(root)
    try:
        _current_head(connection)
        connection.execute("BEGIN IMMEDIATE")
        source_locator, source_inserted = _resolve_locator(
            connection,
            source_locator,
            provenance={"source_authority": "EXPLICIT_MEMORY_LINK"},
        )
        target_locator, target_inserted = _resolve_locator(
            connection,
            target_locator,
            provenance={"source_authority": "EXPLICIT_MEMORY_LINK"},
        )
        inserted_locator_count = int(source_inserted) + int(target_inserted)
        edge = _edge(
            project_id=project_id,
            source_locator_id=source_locator["locator_id"],
            target_locator_id=target_locator["locator_id"],
            edge_type=edge_type,
            evidence_sha256=evidence_sha256,
            recorded_at=exact_recorded_at,
        )
        edge, inserted_edge = _resolve_edge(connection, edge)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    projection = _refresh_head_after_link(root, recorded_at=exact_recorded_at)
    return {
        "status": "PASS",
        "state": "MEMORY_LINK_RECORDED" if inserted_edge else "MEMORY_LINK_REUSED",
        "project_id": project_id,
        "source_locator_id": source_locator["locator_id"],
        "source_sector": source_locator["sector"],
        "target_locator_id": target_locator["locator_id"],
        "target_sector": target_locator["sector"],
        "edge_id": edge["edge_id"],
        "edge_type": edge["edge_type"],
        "edge_sha256": edge["edge_sha256"],
        "inserted_locator_count": inserted_locator_count,
        "idempotent_reuse": not inserted_edge,
        "memory_head_sha256": projection["memory_head_sha256"],
        "manifest_sha256": projection["manifest_sha256"],
        "authority_effects": _authority_effects("MEMORY_LINK_APPENDED"),
        "raw_payload_stored": False,
        "project_truth_pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
    }


def _refresh_head_after_link(root: Path, *, recorded_at: str) -> dict[str, Any]:
    connection = _connect(root)
    try:
        prior = _current_head(connection)
        locator_hashes = [
            str(row[0])
            for row in connection.execute(
                "SELECT locator_sha256 FROM memory_locator ORDER BY locator_id"
            ).fetchall()
        ]
        edge_hashes = [
            str(row[0])
            for row in connection.execute(
                "SELECT edge_sha256 FROM memory_edge ORDER BY edge_id"
            ).fetchall()
        ]
        body = {
            key: prior[key]
            for key in (
                "schema",
                "project_id",
                "accepted_pv",
                "pointer_generation",
                "accepted_manifest_sha256",
                "active_plan_task_id",
                "plan_runtime_projection_sha256",
                "lineage_head_sha256",
            )
        }
        body.update(
            {
                "locator_count": len(locator_hashes),
                "edge_count": len(edge_hashes),
                "locator_set_sha256": sha256_bytes(
                    canonical_json_bytes(locator_hashes)
                ),
                "edge_set_sha256": sha256_bytes(canonical_json_bytes(edge_hashes)),
                "recorded_at": recorded_at,
                "raw_source_payloads_stored": False,
                "project_truth_pointer_moved": False,
            }
        )
        digest = sha256_bytes(canonical_json_bytes(body))
        head = {**body, "head_sha256": digest}
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO memory_authority_head VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                digest,
                head["project_id"],
                len(locator_hashes),
                len(edge_hashes),
                head["accepted_pv"],
                head["pointer_generation"],
                head["accepted_manifest_sha256"],
                head["active_plan_task_id"],
                head["plan_runtime_projection_sha256"],
                head["lineage_head_sha256"],
                canonical_json_bytes(head).decode("utf-8"),
                recorded_at,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return _refresh_projections(root)


def query_memory_graph(
    project_root: str | Path,
    *,
    project_id: str,
    query: str,
    as_of: str,
    sectors: list[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Return one bounded FTS5/BM25 locator slice with typed edges."""

    root = _project_root(project_root, project_id=project_id)
    exact_as_of = _timestamp(as_of, field="memory_query_as_of")
    exact_query = str(query).strip().lower()
    require(
        bool(exact_query) and 1 <= limit <= 20,
        "MEMORY_QUERY_BOUNDS_INVALID",
        "A Memory query requires text and a limit from one to twenty.",
        status="BLOCKED",
    )
    query_terms = sorted(set(re.findall(r"[a-z0-9_]+", exact_query)))
    require(
        bool(query_terms),
        "MEMORY_QUERY_TERMS_REQUIRED",
        "The Memory query has no indexable FTS5 term.",
        status="BLOCKED",
    )
    requested_sectors = {
        str(item).strip().upper() for item in (sectors or []) if str(item).strip()
    }
    require(
        requested_sectors <= set(MEMORY_SECTOR_LOCATOR_PREFIXES),
        "MEMORY_QUERY_SECTOR_INVALID",
        "A Memory query requested an unknown project sector.",
        status="BLOCKED",
    )
    match_query = " OR ".join(f'"{term}"' for term in query_terms)
    connection = _connect(root)
    selected: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    seen_semantic_locators: set[tuple[str, str, str, str, str]] = set()
    try:
        head = _current_head(connection)
        rows = connection.execute(
            """
            SELECT locator.locator_id,locator.sector,locator.locator_kind,
                   locator.locator_value,locator.revision_sha256,locator.label,
                   bm25(memory_locator_fts,0.0,0.0,1.0,1.0,5.0,2.0) AS rank
            FROM memory_locator_fts
            JOIN memory_locator AS locator
              ON locator.locator_id=memory_locator_fts.locator_id
            WHERE memory_locator_fts MATCH ?
              AND memory_locator_fts.project_id=?
              AND locator.recorded_at<=?
            ORDER BY rank,locator.locator_id
            LIMIT ?
            """,
            (match_query, project_id, exact_as_of, min(80, limit * 4)),
        ).fetchall()
        candidates = [
            row
            for row in rows
            if not requested_sectors or str(row["sector"]) in requested_sectors
        ]
        candidate_ids = [str(row["locator_id"]) for row in candidates]
        suppression: dict[str, list[str]] = {}
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            for row in connection.execute(
                f"""
                SELECT target_locator_id,edge_type FROM memory_edge
                WHERE project_id=? AND recorded_at<=?
                  AND edge_type IN ('REVOKES','SUPERSEDES','SUPPRESSES')
                  AND target_locator_id IN ({placeholders})
                ORDER BY recorded_at,edge_id
                """,
                (project_id, exact_as_of, *candidate_ids),
            ).fetchall():
                suppression.setdefault(str(row["target_locator_id"]), []).append(
                    str(row["edge_type"])
                )
        for row in candidates:
            locator_id = str(row["locator_id"])
            semantic_key = (
                str(row["sector"]),
                str(row["locator_kind"]),
                str(row["locator_value"]),
                str(row["revision_sha256"]),
                str(row["label"]),
            )
            if semantic_key in seen_semantic_locators:
                continue
            seen_semantic_locators.add(semantic_key)
            if locator_id in suppression:
                if len(suppressed) < limit:
                    suppressed.append(
                        {
                            "locator_id": locator_id,
                            "state": "SUPPRESSED_MEMORY_LOCATOR",
                            "edge_types": suppression[locator_id],
                        }
                    )
                continue
            linked = connection.execute(
                """
                SELECT edge_id,edge_type,source_locator_id,target_locator_id,
                       evidence_sha256
                FROM memory_edge
                WHERE project_id=? AND recorded_at<=?
                  AND (source_locator_id=? OR target_locator_id=?)
                ORDER BY recorded_at,edge_id LIMIT 8
                """,
                (project_id, exact_as_of, locator_id, locator_id),
            ).fetchall()
            selected.append(
                {
                    "locator_id": locator_id,
                    "sector": str(row["sector"]),
                    "locator_kind": str(row["locator_kind"]),
                    "locator_value": str(row["locator_value"]),
                    "revision_sha256": str(row["revision_sha256"]),
                    "label": str(row["label"]),
                    "rank": float(row["rank"]),
                    "edges": [
                        {
                            "edge_id": str(edge["edge_id"]),
                            "edge_type": str(edge["edge_type"]),
                            "source_locator_id": str(edge["source_locator_id"]),
                            "target_locator_id": str(edge["target_locator_id"]),
                            "evidence_sha256": str(edge["evidence_sha256"]),
                        }
                        for edge in linked
                    ],
                }
            )
            if len(selected) >= limit:
                break
    finally:
        connection.close()
    receipt_body = {
        "schema": MEMORY_QUERY_RECEIPT_SCHEMA,
        "project_id": project_id,
        "memory_head_sha256": head["head_sha256"],
        "as_of": exact_as_of,
        "query_sha256": sha256_bytes(exact_query.encode("utf-8")),
        "requested_sectors": sorted(requested_sectors),
        "hit_count": len(selected),
        "suppressed_count": len(suppressed),
        "full_memory_loaded_into_model_context": False,
        "raw_database_or_markdown_returned": False,
        "project_truth_pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
    }
    return {
        "status": "PASS",
        "result": "HIT" if selected else "NO_HIT",
        "hits": selected,
        "suppressed": suppressed,
        "receipt": {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        },
        "search_engine": "SQLITE_FTS5_BM25",
        "authority_effects": _authority_effects(),
        "full_memory_loaded_into_model_context": False,
        "raw_database_or_markdown_returned": False,
    }


def seal_memory_checkpoint(
    project_root: str | Path,
    *,
    project_id: str,
    host_task_uuid: str,
    host_task_deep_link: str,
    active_plan_task_id: str,
    lineage_head_sha256: str,
    query: str,
    limit: int,
    sealed_at: str,
) -> dict[str, Any]:
    """Seal the exact Memory head and one bounded reentry locator slice."""

    root = _project_root(project_root, project_id=project_id)
    exact_task_uuid = str(host_task_uuid).strip().lower()
    exact_deep_link = str(host_task_deep_link).strip()
    require(
        bool(_TASK_UUID_RE.fullmatch(exact_task_uuid))
        and exact_deep_link == f"codex://threads/{exact_task_uuid}",
        "MEMORY_CHECKPOINT_HOST_TASK_MISMATCH",
        "A Memory checkpoint requires one exact host task UUID and deep link.",
        status="MISMATCH",
    )
    exact_lineage = _sha256(lineage_head_sha256, field="lineage_head_sha256")
    exact_sealed_at = _timestamp(sealed_at, field="memory_checkpoint_sealed_at")
    plan_sha256, _ = _plan_identity(root, active_plan_task_id=active_plan_task_id)
    query_result = query_memory_graph(
        root,
        project_id=project_id,
        query=query,
        as_of=exact_sealed_at,
        limit=limit,
    )
    connection = _connect(root)
    try:
        head = _current_head(connection)
        require(
            head["active_plan_task_id"] == active_plan_task_id
            and head["plan_runtime_projection_sha256"] == plan_sha256
            and head["lineage_head_sha256"] == exact_lineage,
            "MEMORY_CHECKPOINT_AUTHORITY_MISMATCH",
            "The live Plan or ChatLineage identity differs from the Memory head.",
            status="MISMATCH",
        )
        body = {
            "schema": MEMORY_CHECKPOINT_SCHEMA,
            "status": "PASS",
            "project_id": project_id,
            "memory_head_sha256": head["head_sha256"],
            "host_task_uuid": exact_task_uuid,
            "host_task_deep_link": exact_deep_link,
            "active_plan_task_id": active_plan_task_id,
            "plan_runtime_projection_sha256": plan_sha256,
            "lineage_head_sha256": exact_lineage,
            "bounded_locator_ids": [hit["locator_id"] for hit in query_result["hits"]],
            "query_receipt_sha256": query_result["receipt"]["receipt_sha256"],
            "sealed_at": exact_sealed_at,
            "full_transcript_replay_required": False,
            "raw_source_payloads_stored": False,
            "controls_codex_host_wording": False,
            "project_truth_pointer_moved": False,
            "candidate_created": False,
            "hil_invoked": False,
        }
        checkpoint_sha256 = sha256_bytes(canonical_json_bytes(body))
        checkpoint = {**body, "checkpoint_sha256": checkpoint_sha256}
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT checkpoint_json FROM memory_compaction_checkpoint "
            "WHERE checkpoint_sha256=?",
            (checkpoint_sha256,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO memory_compaction_checkpoint VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    checkpoint_sha256,
                    project_id,
                    head["head_sha256"],
                    exact_task_uuid,
                    exact_deep_link,
                    active_plan_task_id,
                    plan_sha256,
                    exact_lineage,
                    canonical_json_bytes(checkpoint).decode("utf-8"),
                    exact_sealed_at,
                ),
            )
        else:
            require(
                canonical_json_bytes(json.loads(str(existing["checkpoint_json"])))
                == canonical_json_bytes(checkpoint),
                "MEMORY_CHECKPOINT_IDENTITY_CONFLICT",
                "One Memory checkpoint identity maps to different bytes.",
                status="MISMATCH",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    _immutable_json(
        _memory_root(root) / "checkpoints" / f"{checkpoint_sha256}.json",
        checkpoint,
    )
    projection = _refresh_projections(root)
    return {
        "status": "PASS",
        "state": "MEMORY_CHECKPOINT_SEALED",
        "project_id": project_id,
        "checkpoint_sha256": checkpoint_sha256,
        "memory_head_sha256": head["head_sha256"],
        "bounded_locators": query_result["hits"],
        "bounded_locator_count": len(query_result["hits"]),
        "manifest_sha256": projection["manifest_sha256"],
        "authority_effects": _authority_effects("COMPACTION_CHECKPOINT_SEALED"),
        "full_transcript_replay_required": False,
        "controls_codex_host_wording": False,
        "project_truth_pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
    }


def rehydrate_memory_checkpoint(
    project_root: str | Path,
    *,
    project_id: str,
    checkpoint_sha256: str,
    host_task_uuid: str,
    host_task_deep_link: str,
    active_plan_task_id: str,
    lineage_head_sha256: str,
    rehydrated_at: str,
) -> dict[str, Any]:
    """Verify one sealed Memory checkpoint and return only its locator slice."""

    root = _project_root(project_root, project_id=project_id)
    exact_checkpoint = _sha256(checkpoint_sha256, field="checkpoint_sha256")
    path = _memory_root(root) / "checkpoints" / f"{exact_checkpoint}.json"
    checkpoint = _json(path, code="MEMORY_CHECKPOINT_REQUIRED")
    body = {
        key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"
    }
    exact_task_uuid = str(host_task_uuid).strip().lower()
    exact_deep_link = str(host_task_deep_link).strip()
    exact_lineage = _sha256(lineage_head_sha256, field="lineage_head_sha256")
    exact_rehydrated_at = _timestamp(
        rehydrated_at, field="memory_checkpoint_rehydrated_at"
    )
    require(
        checkpoint.get("schema") == MEMORY_CHECKPOINT_SCHEMA
        and checkpoint.get("project_id") == project_id
        and checkpoint.get("checkpoint_sha256") == exact_checkpoint
        and sha256_bytes(canonical_json_bytes(body)) == exact_checkpoint
        and checkpoint.get("host_task_uuid") == exact_task_uuid
        and checkpoint.get("host_task_deep_link") == exact_deep_link
        and checkpoint.get("active_plan_task_id") == active_plan_task_id
        and checkpoint.get("lineage_head_sha256") == exact_lineage,
        "MEMORY_REHYDRATION_BINDING_MISMATCH",
        "The Memory checkpoint does not match the exact reentry binding.",
        status="MISMATCH",
    )
    plan_sha256, _ = _plan_identity(root, active_plan_task_id=active_plan_task_id)
    connection = _connect(root)
    try:
        head = _current_head(connection)
        require(
            head["head_sha256"] == checkpoint["memory_head_sha256"]
            and plan_sha256 == checkpoint["plan_runtime_projection_sha256"]
            and head["lineage_head_sha256"] == exact_lineage,
            "MEMORY_REHYDRATION_HEAD_MISMATCH",
            "Project Memory, Plan, or ChatLineage changed after checkpoint sealing.",
            status="MISMATCH",
        )
        locators: list[dict[str, Any]] = []
        for locator_id in checkpoint["bounded_locator_ids"]:
            row = connection.execute(
                "SELECT locator_json FROM memory_locator WHERE locator_id=?",
                (locator_id,),
            ).fetchone()
            require(
                row is not None,
                "MEMORY_REHYDRATION_LOCATOR_MISSING",
                "A sealed Memory locator is no longer available.",
                status="MISMATCH",
            )
            locator = cast(dict[str, Any], json.loads(str(row["locator_json"])))
            locators.append(
                {
                    key: locator[key]
                    for key in (
                        "locator_id",
                        "sector",
                        "locator_kind",
                        "locator_value",
                        "revision_sha256",
                        "label",
                    )
                }
            )
        receipt_body = {
            "schema": MEMORY_REHYDRATION_SCHEMA,
            "status": "PASS",
            "project_id": project_id,
            "checkpoint_sha256": exact_checkpoint,
            "memory_head_sha256": head["head_sha256"],
            "host_task_uuid": exact_task_uuid,
            "active_plan_task_id": active_plan_task_id,
            "plan_runtime_projection_sha256": plan_sha256,
            "lineage_head_sha256": exact_lineage,
            "rehydrated_locator_count": len(locators),
            "rehydrated_locator_set_sha256": sha256_bytes(
                canonical_json_bytes(locators)
            ),
            "rehydrated_at": exact_rehydrated_at,
            "full_transcript_replayed": False,
            "raw_source_payloads_returned": False,
            "controls_codex_host_wording": False,
            "project_truth_pointer_moved": False,
            "candidate_created": False,
            "hil_invoked": False,
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_body))
        receipt = {**receipt_body, "receipt_sha256": receipt_sha256}
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT receipt_json FROM memory_rehydration_receipt WHERE receipt_sha256=?",
            (receipt_sha256,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO memory_rehydration_receipt VALUES(?,?,?,?,?,?)",
                (
                    receipt_sha256,
                    exact_checkpoint,
                    project_id,
                    head["head_sha256"],
                    canonical_json_bytes(receipt).decode("utf-8"),
                    exact_rehydrated_at,
                ),
            )
        else:
            require(
                canonical_json_bytes(json.loads(str(existing["receipt_json"])))
                == canonical_json_bytes(receipt),
                "MEMORY_REHYDRATION_RECEIPT_CONFLICT",
                "One Memory rehydration receipt maps to different bytes.",
                status="MISMATCH",
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    _immutable_json(
        _memory_root(root) / "rehydration" / f"{receipt_sha256}.json", receipt
    )
    projection = _refresh_projections(root)
    return {
        "status": "PASS",
        "state": "MEMORY_CHECKPOINT_REHYDRATED",
        "project_id": project_id,
        "checkpoint_sha256": exact_checkpoint,
        "memory_head_sha256": head["head_sha256"],
        "locators": locators,
        "receipt_sha256": receipt_sha256,
        "manifest_sha256": projection["manifest_sha256"],
        "authority_effects": _authority_effects("COMPACTION_CHECKPOINT_REHYDRATED"),
        "full_transcript_replayed": False,
        "raw_source_payloads_returned": False,
        "controls_codex_host_wording": False,
        "project_truth_pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
    }
