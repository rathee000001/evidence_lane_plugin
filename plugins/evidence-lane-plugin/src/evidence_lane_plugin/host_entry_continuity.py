"""Exact-once host Exit-to-Entry continuity without Project-PV promotion.

The host-entry envelope is a fourth, host-scoped continuity authority.  It is
not Project Truth, Canon Input, Agent Learning, or a HIL decision.  Durable
local hosts do not need it.  Ephemeral and stateless hosts must persist and
consume it through a transactional connector before governed work starts.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .project_authority import resolved_chat_lineage_root
from .redaction import contains_secret

HOST_ENTRY_ENVELOPE_SCHEMA = "evidence-lane.host-entry-envelope.v2"
HOST_ENTRY_CONSUMPTION_SCHEMA = "evidence-lane.host-entry-consumption.v1"
HOST_ENTRY_GENERATION_ROLL_SCHEMA = "evidence-lane.host-entry-generation-roll.v1"

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_INTERACTION_PROFILES = {
    "DURABLE_LOCAL",
    "INTERACTIVE_EPHEMERAL",
    "STATELESS_HEADLESS",
}


class TransactionalHostEntryBackend(Protocol):
    """Minimum remote capability needed for exact-once host entry."""

    runtime_state_capable: bool

    def put(
        self,
        *,
        project_id: str,
        category: str,
        name: str,
        content: bytes,
        mime_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...

    def claim_once(
        self,
        *,
        project_id: str,
        namespace: str,
        key: str,
        value_sha256: str,
        claimant_sha256: str,
    ) -> dict[str, Any]: ...


def _sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        bool(_SHA256_RE.fullmatch(exact)),
        "HOST_ENTRY_SHA256_INVALID",
        "A host-entry field is not one exact SHA-256.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _timestamp(value: Any, *, field: str) -> datetime:
    exact = str(value or "").strip()
    require(
        bool(exact),
        "HOST_ENTRY_TIMESTAMP_REQUIRED",
        "A host-entry timestamp is required.",
        status="BLOCKED",
        field=field,
    )
    try:
        parsed = datetime.fromisoformat(exact)
    except ValueError as exc:
        require(
            False,
            "HOST_ENTRY_TIMESTAMP_INVALID",
            "A host-entry timestamp is not valid ISO-8601.",
            status="MISMATCH",
            field=field,
        )
        raise AssertionError("unreachable") from exc
    require(
        parsed.tzinfo is not None,
        "HOST_ENTRY_TIMESTAMP_TIMEZONE_REQUIRED",
        "A host-entry timestamp requires an explicit timezone.",
        status="MISMATCH",
        field=field,
    )
    return parsed.astimezone(UTC)


def _ledger_path(project_root: Path) -> Path:
    return resolved_chat_lineage_root(project_root) / "host-entry-continuity.sqlite"


def _connect(project_root: Path) -> sqlite3.Connection:
    path = _ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS host_entry_envelope(
            envelope_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            plan_task_id TEXT NOT NULL,
            host_binding_id TEXT NOT NULL,
            pointer_generation INTEGER NOT NULL CHECK(pointer_generation >= 0),
            envelope_sha256 TEXT NOT NULL UNIQUE,
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('ISSUED','INVALIDATED')),
            invalidated_reason TEXT,
            envelope_json TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_host_entry_generation
        ON host_entry_envelope(project_id,pointer_generation,state);
        CREATE TABLE IF NOT EXISTS host_entry_consumption(
            envelope_id TEXT PRIMARY KEY
                REFERENCES host_entry_envelope(envelope_id),
            consumer_binding_sha256 TEXT NOT NULL,
            consumed_at TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            receipt_json TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS host_entry_generation_roll(
            roll_sha256 TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            accepted_pointer_generation INTEGER NOT NULL,
            invalidated_count INTEGER NOT NULL,
            reissued_envelope_ids_json TEXT NOT NULL,
            rolled_at TEXT NOT NULL,
            receipt_json TEXT NOT NULL
        ) STRICT;
        """
    )
    return connection


def _project_root(project_root: str | Path, *, project_id: str) -> Path:
    root = Path(project_root).resolve()
    require(
        root.name == project_id,
        "HOST_ENTRY_PROJECT_ROOT_MISMATCH",
        "The host-entry project root does not match the exact project identity.",
        status="BLOCKED",
        project_id=project_id,
    )
    return root


def derive_host_entry_authority_heads(
    project_root: str | Path,
    *,
    project_id: str,
    accepted_pointer: dict[str, Any],
    chat_lineage_head_sha256: str,
) -> dict[str, str]:
    """Derive the four independent current heads without inventing promotion."""

    root = _project_root(project_root, project_id=project_id)

    def head_or_absent(label: str, candidates: tuple[Path, ...]) -> str:
        existing = next((path for path in candidates if path.is_file()), None)
        if existing is not None:
            return sha256_bytes(existing.read_bytes())
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "project_id": project_id,
                    "authority": label,
                    "state": "NO_ACCEPTED_HEAD",
                }
            )
        )

    return {
        "project_truth_pointer_sha256": sha256_bytes(
            canonical_json_bytes(accepted_pointer)
        ),
        "canon_input_head_sha256": head_or_absent(
            "CANON_INPUT",
            (
                root / "canon" / "active_pointer.json",
                root / "canon_input" / "active_pointer.json",
            ),
        ),
        "agent_learning_pointer_sha256": head_or_absent(
            "AGENT_LEARNING",
            (
                root / "learning" / "active_pointer.json",
                root / "agent_learning" / "active_pointer.json",
            ),
        ),
        "chat_lineage_head_sha256": _sha256(
            chat_lineage_head_sha256,
            field="chat_lineage_head_sha256",
        ),
    }


def derive_host_entry_env_uop(flash: dict[str, Any]) -> dict[str, str]:
    """Derive separate source ENV/UOP and Codex projection identities."""

    dual = cast(dict[str, Any], flash.get("dual_identity") or {})
    source = cast(dict[str, Any], dual.get("source_authority") or {})
    source_manifest = cast(dict[str, Any], source.get("manifest") or {})
    members = source_manifest.get("members")
    projection = cast(dict[str, Any], dual.get("codex_projection") or {})
    require(
        isinstance(members, list)
        and bool(members)
        and bool(str(projection.get("identity_sha256") or ""))
        and bool(str(flash.get("receipt_sha256") or "")),
        "HOST_ENTRY_FLASH_IDENTITY_INCOMPLETE",
        "Host entry requires the verified dual ENV/UOP and Flash receipt identities.",
        status="MISMATCH",
    )
    members = cast(list[Any], members)
    env_members = sorted(
        (
            dict(item)
            for item in members
            if str(item.get("path") or "").startswith("env/")
        ),
        key=lambda item: str(item["path"]),
    )
    uop_members = sorted(
        (
            dict(item)
            for item in members
            if str(item.get("path") or "").startswith("uop/")
        ),
        key=lambda item: str(item["path"]),
    )
    require(
        bool(env_members) and bool(uop_members),
        "HOST_ENTRY_FLASH_AUTHORITY_SPLIT_INCOMPLETE",
        "Host entry requires independently identifiable ENV and UOP source members.",
        status="MISMATCH",
    )
    return {
        "env_authority_sha256": sha256_bytes(canonical_json_bytes(env_members)),
        "uop_authority_sha256": sha256_bytes(canonical_json_bytes(uop_members)),
        "derived_projection_sha256": _sha256(
            projection.get("identity_sha256"),
            field="codex_projection.identity_sha256",
        ),
        "flash_receipt_sha256": _sha256(
            flash.get("receipt_sha256"), field="flash.receipt_sha256"
        ),
    }


def validate_host_entry_envelope(
    value: dict[str, Any],
    *,
    expected_project_id: str | None = None,
) -> dict[str, Any]:
    require(
        value.get("schema") == HOST_ENTRY_ENVELOPE_SCHEMA,
        "HOST_ENTRY_ENVELOPE_SCHEMA_INVALID",
        "The host-entry envelope schema is unsupported.",
        status="MISMATCH",
    )
    if expected_project_id is not None:
        require(
            value.get("project_id") == expected_project_id,
            "HOST_ENTRY_CROSS_PROJECT_ROUTE_DENIED",
            "The host-entry envelope belongs to another project.",
            status="BLOCKED",
        )
    claimed = _sha256(value.get("envelope_sha256"), field="envelope_sha256")
    actual = sha256_bytes(
        canonical_json_bytes(
            {key: item for key, item in value.items() if key != "envelope_sha256"}
        )
    )
    pointer = cast(dict[str, Any], value.get("accepted_pointer") or {})
    active_plan = cast(dict[str, Any], value.get("active_plan") or {})
    env_uop = cast(dict[str, Any], value.get("env_uop") or {})
    worktree = cast(dict[str, Any], value.get("worktree") or {})
    authority_heads = cast(dict[str, Any], value.get("authority_heads") or {})
    storage = cast(dict[str, Any], value.get("storage") or {})
    effects = cast(dict[str, Any], value.get("authority_effects") or {})
    source_task = cast(dict[str, Any], value.get("source_task") or {})
    destination_task = cast(dict[str, Any], value.get("destination_task") or {})
    profile = str(value.get("interaction_profile") or "")
    require(
        claimed == actual
        and profile in _INTERACTION_PROFILES
        and bool(str(value.get("project_id") or ""))
        and bool(str(value.get("evidence_session_id") or ""))
        and bool(str(value.get("plan_task_id") or ""))
        and bool(str(value.get("host_binding_id") or ""))
        and int(value.get("host_entry_ordinal") or 0) >= 1
        and bool(str(pointer.get("accepted_pv") or ""))
        and int(pointer.get("generation") or 0) >= 1
        and int(active_plan.get("row") or 0) >= 1
        and active_plan.get("task_id") == value.get("plan_task_id")
        and worktree.get("dirty_untracked_bytes_preserved") is True
        and storage.get("google_drive_primary_runtime") is False
        and effects
        == {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "host_entry": "ISSUED_NOT_CONSUMED",
        }
        and value.get("candidate_promoted") is False
        and value.get("pointer_moved") is False
        and value.get("hil_inferred") is False
        and value.get("private_reasoning_stored") is False
        and bool(source_task)
        and bool(destination_task),
        "HOST_ENTRY_ENVELOPE_BOUNDARY_INVALID",
        "The host-entry envelope crossed an identity, authority, HIL, or pointer boundary.",
        status="FAIL",
    )
    remote_profile = profile in {"INTERACTIVE_EPHEMERAL", "STATELESS_HEADLESS"}
    require(
        (
            profile == "DURABLE_LOCAL"
            and storage.get("mode") == "local"
            and storage.get("transactional_exact_once_required") is False
        )
        or (
            remote_profile
            and storage.get("mode") == "configured_durable_connector"
            and storage.get("transactional_exact_once_required") is True
            and bool(str(storage.get("connector_id") or ""))
        ),
        "HOST_ENTRY_STORAGE_ROUTE_INVALID",
        "The host-entry storage route does not match the measured interaction profile.",
        status="MISMATCH",
        interaction_profile=profile,
    )
    require(
        bool(str(source_task.get("task_id") or ""))
        and bool(str(destination_task.get("task_id") or ""))
        and "repository_path" not in worktree
        and "raw_status" not in worktree,
        "HOST_ENTRY_SECRET_SAFE_IDENTITY_INVALID",
        "Host-entry identity must use exact task IDs and hashed, secret-safe source fields.",
        status="BLOCKED",
    )
    for field, item in (
        ("accepted_pointer.manifest_sha256", pointer.get("manifest_sha256")),
        ("accepted_pointer.pointer_sha256", pointer.get("pointer_sha256")),
        ("env_uop.env_authority_sha256", env_uop.get("env_authority_sha256")),
        ("env_uop.uop_authority_sha256", env_uop.get("uop_authority_sha256")),
        (
            "env_uop.derived_projection_sha256",
            env_uop.get("derived_projection_sha256"),
        ),
        ("env_uop.flash_receipt_sha256", env_uop.get("flash_receipt_sha256")),
        ("worktree.source_identity_sha256", worktree.get("source_identity_sha256")),
        ("worktree.worktree_sha256", worktree.get("worktree_sha256")),
        ("worktree.status_sha256", worktree.get("status_sha256")),
        ("source_task.task_deep_link_sha256", source_task.get("task_deep_link_sha256")),
        (
            "destination_task.task_deep_link_sha256",
            destination_task.get("task_deep_link_sha256"),
        ),
        (
            "authority_heads.project_truth_pointer_sha256",
            authority_heads.get("project_truth_pointer_sha256"),
        ),
        (
            "authority_heads.canon_input_head_sha256",
            authority_heads.get("canon_input_head_sha256"),
        ),
        (
            "authority_heads.agent_learning_pointer_sha256",
            authority_heads.get("agent_learning_pointer_sha256"),
        ),
        (
            "authority_heads.chat_lineage_head_sha256",
            authority_heads.get("chat_lineage_head_sha256"),
        ),
        ("previous_exit_sha256", value.get("previous_exit_sha256")),
        ("storage.selection_receipt_sha256", storage.get("selection_receipt_sha256")),
        ("replay_nonce_sha256", value.get("replay_nonce_sha256")),
        ("idempotency_key", value.get("idempotency_key")),
    ):
        _sha256(item, field=field)
    issued = _timestamp(value.get("issued_at"), field="issued_at")
    expires = _timestamp(value.get("expires_at"), field="expires_at")
    require(
        expires > issued,
        "HOST_ENTRY_EXPIRY_INVALID",
        "The host-entry envelope must expire after it is issued.",
        status="MISMATCH",
    )
    require(
        not contains_secret(value),
        "HOST_ENTRY_SECRET_MATERIAL_FORBIDDEN",
        "A host-entry envelope cannot contain credentials or secret material.",
        status="BLOCKED",
    )
    return value


def issue_host_entry_envelope(
    project_root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    plan_task_id: str,
    host_binding_id: str,
    host_entry_ordinal: int,
    interaction_profile: str,
    accepted_pointer: dict[str, Any],
    active_plan_row: int,
    source_task: dict[str, Any],
    destination_task: dict[str, Any],
    env_uop: dict[str, Any],
    worktree: dict[str, Any],
    authority_heads: dict[str, Any],
    previous_exit_sha256: str,
    storage: dict[str, Any],
    replay_nonce_sha256: str,
    issued_at: str,
    expires_at: str,
    candidate_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue one immutable next-entry envelope without moving any authority."""

    root = _project_root(project_root, project_id=project_id)
    identity = {
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "plan_task_id": plan_task_id,
        "host_binding_id": host_binding_id,
        "host_entry_ordinal": host_entry_ordinal,
        "accepted_pointer_generation": accepted_pointer.get("generation"),
        "previous_exit_sha256": previous_exit_sha256,
        "replay_nonce_sha256": replay_nonce_sha256,
    }
    envelope_id = "hentry_" + sha256_bytes(canonical_json_bytes(identity))[:28].lower()
    idempotency_key = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "envelope_id": envelope_id,
                "host_binding_id": host_binding_id,
                "host_entry_ordinal": host_entry_ordinal,
            }
        )
    )
    envelope = {
        "schema": HOST_ENTRY_ENVELOPE_SCHEMA,
        "envelope_id": envelope_id,
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "plan_task_id": plan_task_id,
        "host_binding_id": host_binding_id,
        "host_entry_ordinal": host_entry_ordinal,
        "interaction_profile": interaction_profile.strip().upper(),
        "accepted_pointer": dict(accepted_pointer),
        "active_plan": {
            "row": active_plan_row,
            "task_id": plan_task_id,
        },
        "source_task": dict(source_task),
        "destination_task": dict(destination_task),
        "env_uop": dict(env_uop),
        "worktree": dict(worktree),
        "authority_heads": dict(authority_heads),
        "candidate_overlay": candidate_overlay,
        "previous_exit_sha256": previous_exit_sha256,
        "storage": dict(storage),
        "replay_nonce_sha256": replay_nonce_sha256,
        "idempotency_key": idempotency_key,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "authority_effects": {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "host_entry": "ISSUED_NOT_CONSUMED",
        },
        "candidate_promoted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "private_reasoning_stored": False,
    }
    envelope["envelope_sha256"] = sha256_bytes(canonical_json_bytes(envelope))
    validate_host_entry_envelope(envelope, expected_project_id=project_id)
    path = (
        resolved_chat_lineage_root(root)
        / "host-entry-continuity"
        / f"{envelope_id}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT envelope_sha256,envelope_json FROM host_entry_envelope "
            "WHERE envelope_id=?",
            (envelope_id,),
        ).fetchone()
        if existing is not None:
            require(
                existing["envelope_sha256"] == envelope["envelope_sha256"]
                and json.loads(existing["envelope_json"]) == envelope,
                "HOST_ENTRY_ENVELOPE_IMMUTABILITY_CONFLICT",
                "The host-entry ID already binds different immutable bytes.",
                status="BLOCKED",
                envelope_id=envelope_id,
            )
            state = "ISSUED_IDEMPOTENT_REUSE"
        else:
            connection.execute(
                "INSERT INTO host_entry_envelope VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    envelope_id,
                    project_id,
                    plan_task_id,
                    host_binding_id,
                    int(accepted_pointer.get("generation") or 0),
                    envelope["envelope_sha256"],
                    issued_at,
                    expires_at,
                    "ISSUED",
                    None,
                    json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                ),
            )
            state = "ISSUED"
        connection.commit()
    finally:
        connection.close()
    if path.exists():
        require(
            json.loads(path.read_text(encoding="utf-8")) == envelope,
            "HOST_ENTRY_ENVELOPE_IMMUTABILITY_CONFLICT",
            "The host-entry file already contains different immutable bytes.",
            status="BLOCKED",
            path=str(path),
        )
    else:
        atomic_write_json(path, envelope)
    return {
        "status": "PASS",
        "state": state,
        "envelope": envelope,
        "envelope_path": str(path),
        "pointer_moved": False,
        "hil_inferred": False,
    }


def persist_host_entry_envelope(
    envelope: dict[str, Any],
    *,
    backend: TransactionalHostEntryBackend,
) -> dict[str, Any]:
    """Persist one envelope through a connector that can also claim it once."""

    validate_host_entry_envelope(envelope)
    require(
        getattr(backend, "runtime_state_capable", False) is True
        and callable(getattr(backend, "claim_once", None)),
        "HOST_ENTRY_TRANSACTIONAL_CONNECTOR_REQUIRED",
        "Remote host-entry continuity requires a transactional exact-once connector.",
        status="BLOCKED",
    )
    payload = canonical_json_bytes(envelope)
    stored = backend.put(
        project_id=str(envelope["project_id"]),
        category="receipts",
        name=f"{envelope['envelope_id']}.json",
        content=payload,
        mime_type="application/json",
        metadata={
            "schema": HOST_ENTRY_ENVELOPE_SCHEMA,
            "sha256": sha256_bytes(payload),
            "pointer_generation": envelope["accepted_pointer"]["generation"],
            "secret_material_persisted": False,
        },
    )
    require(
        str(stored.get("sha256") or "").upper() == sha256_bytes(payload),
        "HOST_ENTRY_DURABLE_PERSISTENCE_MISMATCH",
        "The durable connector did not attest the exact host-entry bytes.",
        status="MISMATCH",
    )
    body = {
        "schema": "evidence-lane.host-entry-persistence.v1",
        "project_id": envelope["project_id"],
        "envelope_id": envelope["envelope_id"],
        "envelope_sha256": envelope["envelope_sha256"],
        "connector_receipt": stored,
        "durable_persisted": True,
        "continuity_claimed": True,
        "google_drive_primary_runtime": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def seal_next_host_entry_from_exit(
    project_root: str | Path,
    host_exit_packet: dict[str, Any],
    *,
    host_binding_id: str,
    host_entry_ordinal: int,
    source_task: dict[str, Any],
    destination_task: dict[str, Any],
    env_uop: dict[str, Any],
    worktree: dict[str, Any],
    authority_heads: dict[str, Any],
    storage: dict[str, Any],
    replay_nonce_sha256: str,
    issued_at: str,
    expires_at: str,
    backend: TransactionalHostEntryBackend,
    candidate_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Close one remote exit only after the next entry is durably persisted."""

    # Imported here to keep the existing exit-packet module independent from
    # its later exact consumer and avoid a module cycle.
    from .canon_runtime_continuity import validate_host_exit_continuity_packet

    packet = validate_host_exit_continuity_packet(host_exit_packet)
    project_id = str(packet["project_id"])
    root = _project_root(project_root, project_id=project_id)
    persistence = cast(dict[str, Any], packet.get("persistence") or {})
    route = cast(dict[str, Any], packet.get("route") or {})
    pointer = cast(dict[str, Any], packet.get("pointer") or {})
    active_plan = cast(dict[str, Any], packet.get("active_plan") or {})
    require(
        persistence.get("state") == "AWAITING_LATER_DURABLE_CONNECTOR_PERSISTENCE"
        and persistence.get("durable_persisted") is False
        and persistence.get("exit_complete") is False
        and route.get("storage_connector_required") is True
        and route.get("interaction_profile")
        in {"INTERACTIVE_EPHEMERAL", "STATELESS_HEADLESS"},
        "HOST_EXIT_NOT_ELIGIBLE_FOR_REMOTE_ENTRY_SEAL",
        "Only an incomplete remote host exit can issue a durably persisted next entry.",
        status="BLOCKED",
    )
    pointer_payload = {
        "accepted_pv": pointer.get("accepted_pv"),
        "generation": pointer.get("pointer_generation"),
        "manifest_sha256": pointer.get("accepted_manifest_sha256"),
        "pointer_sha256": sha256_bytes(canonical_json_bytes(pointer)),
    }
    issued = issue_host_entry_envelope(
        root,
        project_id=project_id,
        evidence_session_id=str(packet["evidence_session_id"]),
        plan_task_id=str(packet["plan_task_id"]),
        host_binding_id=host_binding_id,
        host_entry_ordinal=host_entry_ordinal,
        interaction_profile=str(route["interaction_profile"]),
        accepted_pointer=pointer_payload,
        active_plan_row=int(active_plan["row"]),
        source_task=source_task,
        destination_task=destination_task,
        env_uop=env_uop,
        worktree=worktree,
        authority_heads=authority_heads,
        previous_exit_sha256=str(packet["packet_sha256"]),
        storage=storage,
        replay_nonce_sha256=replay_nonce_sha256,
        issued_at=issued_at,
        expires_at=expires_at,
        candidate_overlay=candidate_overlay,
    )
    persisted = persist_host_entry_envelope(issued["envelope"], backend=backend)
    completion_body = {
        "schema": "evidence-lane.host-exit-persistence-completion.v1",
        "project_id": project_id,
        "host_exit_packet_id": packet["packet_id"],
        "host_exit_packet_sha256": packet["packet_sha256"],
        "host_entry_envelope_id": issued["envelope"]["envelope_id"],
        "host_entry_envelope_sha256": issued["envelope"]["envelope_sha256"],
        "persistence_receipt_sha256": persisted["receipt_sha256"],
        "durable_persisted": True,
        "exit_complete": True,
        "continuity_claimed": True,
        "source_exit_packet_mutated": False,
        "project_truth_promoted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "completed_at": issued_at,
    }
    completion = {
        **completion_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(completion_body)),
    }
    completion_path = (
        root
        / "lineage"
        / "host-exit-continuity"
        / "completions"
        / f"{packet['packet_id']}.json"
    )
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    if completion_path.exists():
        require(
            json.loads(completion_path.read_text(encoding="utf-8")) == completion,
            "HOST_EXIT_COMPLETION_IMMUTABILITY_CONFLICT",
            "The remote host exit already binds a different completion receipt.",
            status="BLOCKED",
        )
        state = "SEALED_IDEMPOTENT_REUSE"
    else:
        atomic_write_json(completion_path, completion)
        state = "SEALED"
    return {
        "status": "PASS",
        "state": state,
        "host_exit_packet": packet,
        "host_entry": issued,
        "persistence": persisted,
        "completion": completion,
        "completion_path": str(completion_path),
    }


def consume_host_entry_envelope(
    project_root: str | Path,
    envelope: dict[str, Any],
    *,
    expected_project_id: str,
    expected_evidence_session_id: str,
    expected_plan_task_id: str,
    expected_pointer: dict[str, Any],
    expected_active_plan_row: int,
    expected_env_uop: dict[str, Any],
    expected_worktree_sha256: str,
    expected_authority_heads: dict[str, Any],
    consumer_binding: dict[str, Any],
    consumed_at: str,
    backend: TransactionalHostEntryBackend | None = None,
    expected_candidate_overlay_sha256: str | None = None,
) -> dict[str, Any]:
    """Consume one exact envelope once; exact retries return the prior receipt."""

    root = _project_root(project_root, project_id=expected_project_id)
    validate_host_entry_envelope(envelope, expected_project_id=expected_project_id)
    consumed = _timestamp(consumed_at, field="consumed_at")
    expires = _timestamp(envelope.get("expires_at"), field="expires_at")
    require(
        consumed <= expires,
        "HOST_ENTRY_ENVELOPE_EXPIRED",
        "The host-entry envelope expired before consumption.",
        status="STALE",
        envelope_id=envelope.get("envelope_id"),
    )
    pointer = cast(dict[str, Any], envelope["accepted_pointer"])
    expected_manifest = _sha256(
        expected_pointer.get("manifest_sha256"),
        field="expected_pointer.manifest_sha256",
    )
    expected_pointer_sha = _sha256(
        expected_pointer.get("pointer_sha256"),
        field="expected_pointer.pointer_sha256",
    )
    require(
        envelope.get("evidence_session_id") == expected_evidence_session_id
        and envelope.get("plan_task_id") == expected_plan_task_id
        and pointer.get("accepted_pv") == expected_pointer.get("accepted_pv")
        and int(pointer.get("generation") or 0)
        == int(expected_pointer.get("generation") or -1)
        and pointer.get("manifest_sha256") == expected_manifest
        and pointer.get("pointer_sha256") == expected_pointer_sha
        and envelope.get("active_plan", {}).get("row") == expected_active_plan_row
        and envelope.get("env_uop") == expected_env_uop
        and envelope.get("worktree", {}).get("worktree_sha256")
        == _sha256(expected_worktree_sha256, field="expected_worktree_sha256")
        and envelope.get("authority_heads") == expected_authority_heads,
        "HOST_ENTRY_BINDING_MISMATCH",
        "The host-entry envelope does not match the exact session, pointer, Plan, ENV/UOP, worktree, and authority heads.",
        status="MISMATCH",
    )
    overlay = envelope.get("candidate_overlay")
    if expected_candidate_overlay_sha256 is None:
        require(
            overlay is None,
            "HOST_ENTRY_CANDIDATE_OVERLAY_UNAUTHORIZED",
            "An entry candidate overlay was supplied without exact authorization.",
            status="BLOCKED",
        )
    else:
        require(
            isinstance(overlay, dict)
            and overlay.get("candidate_sha256")
            == _sha256(
                expected_candidate_overlay_sha256,
                field="expected_candidate_overlay_sha256",
            ),
            "HOST_ENTRY_CANDIDATE_OVERLAY_MISMATCH",
            "The candidate overlay does not match the explicitly authorized candidate.",
            status="MISMATCH",
        )
    destination = cast(dict[str, Any], envelope["destination_task"])
    require(
        destination.get("task_id") == consumer_binding.get("task_id")
        and destination.get("task_deep_link_sha256")
        == consumer_binding.get("task_deep_link_sha256")
        and envelope.get("host_binding_id") == consumer_binding.get("host_binding_id"),
        "HOST_ENTRY_DESTINATION_MISMATCH",
        "The host-entry envelope is bound to a different destination task or host.",
        status="MISMATCH",
    )
    consumer_binding_sha256 = sha256_bytes(canonical_json_bytes(consumer_binding))
    remote_claim: dict[str, Any] | None = None
    remote_required = envelope.get("interaction_profile") != "DURABLE_LOCAL"
    if remote_required:
        require(
            backend is not None
            and getattr(backend, "runtime_state_capable", False) is True
            and callable(getattr(backend, "claim_once", None)),
            "HOST_ENTRY_TRANSACTIONAL_CONNECTOR_REQUIRED",
            "Ephemeral or stateless entry requires the configured transactional connector.",
            status="BLOCKED",
        )
        assert backend is not None
        remote_claim = backend.claim_once(
            project_id=expected_project_id,
            namespace="host-entry-consumption",
            key=str(envelope["envelope_id"]),
            value_sha256=sha256_bytes(canonical_json_bytes(envelope)),
            claimant_sha256=consumer_binding_sha256,
        )
        require(
            remote_claim.get("claimed") is True
            and remote_claim.get("claimant_sha256") == consumer_binding_sha256,
            "HOST_ENTRY_REMOTE_CLAIM_MISMATCH",
            "The transactional connector did not preserve the exact host-entry claim.",
            status="MISMATCH",
        )
    remote_claim_sha256 = (
        sha256_bytes(
            canonical_json_bytes(
                {key: item for key, item in remote_claim.items() if key != "idempotent"}
            )
        )
        if remote_claim
        else None
    )
    receipt_body = {
        "schema": HOST_ENTRY_CONSUMPTION_SCHEMA,
        "project_id": expected_project_id,
        "envelope_id": envelope["envelope_id"],
        "envelope_sha256": envelope["envelope_sha256"],
        "evidence_session_id": expected_evidence_session_id,
        "plan_task_id": expected_plan_task_id,
        "active_plan_row": expected_active_plan_row,
        "accepted_pv": pointer["accepted_pv"],
        "accepted_pointer_generation": pointer["generation"],
        "consumer_binding_sha256": consumer_binding_sha256,
        "remote_claim_sha256": remote_claim_sha256,
        "candidate_overlay_state": (
            "NONE" if overlay is None else "EXACT_AUTHORIZED_OVERLAY"
        ),
        "consumed_at": consumed_at,
        "mutations_replayed": False,
        "project_truth_promoted": False,
        "learning_promoted": False,
        "canon_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "private_reasoning_stored": False,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT state FROM host_entry_envelope WHERE envelope_id=?",
            (envelope["envelope_id"],),
        ).fetchone()
        require(
            row is not None and row["state"] == "ISSUED",
            "HOST_ENTRY_ENVELOPE_INVALIDATED_OR_UNKNOWN",
            "The host-entry envelope is unknown or invalidated.",
            status="STALE",
            envelope_id=envelope["envelope_id"],
        )
        existing = connection.execute(
            "SELECT consumer_binding_sha256,receipt_json "
            "FROM host_entry_consumption WHERE envelope_id=?",
            (envelope["envelope_id"],),
        ).fetchone()
        if existing is not None:
            require(
                existing["consumer_binding_sha256"] == consumer_binding_sha256,
                "HOST_ENTRY_DUPLICATE_CONSUMER_MISMATCH",
                "The consumed envelope cannot be rebound to another consumer.",
                status="BLOCKED",
                envelope_id=envelope["envelope_id"],
            )
            prior = json.loads(existing["receipt_json"])
            connection.rollback()
            return {
                "status": "PASS",
                "state": "CONSUMED_IDEMPOTENT_REUSE",
                "receipt": prior,
                "mutations_replayed": False,
            }
        connection.execute(
            "INSERT INTO host_entry_consumption VALUES(?,?,?,?,?)",
            (
                envelope["envelope_id"],
                consumer_binding_sha256,
                consumed_at,
                receipt["receipt_sha256"],
                json.dumps(receipt, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "status": "PASS",
        "state": "CONSUMED",
        "receipt": receipt,
        "mutations_replayed": False,
    }


def roll_host_entry_generation(
    project_root: str | Path,
    *,
    project_id: str,
    accepted_pointer_generation: int,
    reissue_contracts: list[dict[str, Any]],
    rolled_at: str,
) -> dict[str, Any]:
    """Invalidate prior generations and issue replacements for active bindings."""

    root = _project_root(project_root, project_id=project_id)
    _timestamp(rolled_at, field="rolled_at")
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "UPDATE host_entry_envelope SET state='INVALIDATED', "
            "invalidated_reason='ACCEPTED_POINTER_GENERATION_ADVANCED' "
            "WHERE project_id=? AND pointer_generation<>? AND state='ISSUED'",
            (project_id, accepted_pointer_generation),
        )
        invalidated_count = int(cursor.rowcount)
        connection.commit()
    finally:
        connection.close()
    issued: list[dict[str, Any]] = []
    for contract in reissue_contracts:
        pointer = cast(dict[str, Any], contract.get("accepted_pointer") or {})
        require(
            int(pointer.get("generation") or -1) == accepted_pointer_generation,
            "HOST_ENTRY_REISSUE_GENERATION_MISMATCH",
            "Every reissued host-entry envelope must bind the new accepted generation.",
            status="MISMATCH",
        )
        issued.append(
            issue_host_entry_envelope(root, project_id=project_id, **contract)
        )
    body = {
        "schema": HOST_ENTRY_GENERATION_ROLL_SCHEMA,
        "project_id": project_id,
        "accepted_pointer_generation": accepted_pointer_generation,
        "invalidated_count": invalidated_count,
        "reissued_envelope_ids": [row["envelope"]["envelope_id"] for row in issued],
        "rolled_at": rolled_at,
        "project_truth_pointer_moved_by_this_action": False,
        "hil_inferred": False,
    }
    receipt = {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}
    connection = _connect(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO host_entry_generation_roll VALUES(?,?,?,?,?,?,?)",
            (
                receipt["receipt_sha256"],
                project_id,
                accepted_pointer_generation,
                invalidated_count,
                json.dumps(body["reissued_envelope_ids"], separators=(",", ":")),
                rolled_at,
                json.dumps(receipt, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "status": "PASS",
        "receipt": receipt,
        "reissued": issued,
    }


def inspect_host_entry_continuity(
    project_root: str | Path, *, project_id: str
) -> dict[str, Any]:
    """Read and integrity-check the project-scoped host-entry ledger."""

    root = _project_root(project_root, project_id=project_id)
    path = _ledger_path(root)
    if not path.is_file():
        return {
            "status": "PASS",
            "project_id": project_id,
            "envelope_count": 0,
            "consumption_count": 0,
            "generation_roll_count": 0,
            "integrity": ["ok"],
            "foreign_key_errors": 0,
        }
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        envelope_count = int(
            connection.execute("SELECT COUNT(*) FROM host_entry_envelope").fetchone()[0]
        )
        consumption_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM host_entry_consumption"
            ).fetchone()[0]
        )
        roll_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM host_entry_generation_roll"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    require(
        integrity == ["ok"] and not foreign_keys,
        "HOST_ENTRY_LEDGER_INVALID",
        "The host-entry continuity ledger failed integrity validation.",
        status="FAIL",
        integrity=integrity,
        foreign_key_errors=len(foreign_keys),
    )
    return {
        "status": "PASS",
        "project_id": project_id,
        "envelope_count": envelope_count,
        "consumption_count": consumption_count,
        "generation_roll_count": roll_count,
        "integrity": integrity,
        "foreign_key_errors": 0,
    }
