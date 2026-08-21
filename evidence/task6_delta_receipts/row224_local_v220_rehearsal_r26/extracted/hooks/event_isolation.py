"""Cross-process isolation for the eight governed Codex hook events.

The launcher validates the current OpenAI Codex wire shapes, claims one
content-addressed occurrence in SQLite before executing its handler, and
replays only a previously validated terminal output.  Raw hook input and
stderr are never persisted.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self

POLICY_SCHEMA: Final = "evidence-lane.codex-hook-event-isolation-policy.v1"
KILL_SWITCH_SCHEMA: Final = "evidence-lane.codex-hook-kill-switch.v1"
EVENT_RECEIPT_SCHEMA: Final = "evidence-lane.codex-hook-event-receipt.v1"
SQLITE_SCHEMA: Final = "evidence-lane.codex-hook-event-isolation-sqlite.v1"

_HOOKS_ROOT = Path(__file__).resolve().parent
_POLICY_PATH = _HOOKS_ROOT / "event_isolation_policy.json"
_POLICY_BYTES = _POLICY_PATH.read_bytes()
POLICY_SHA256: Final = hashlib.sha256(_POLICY_BYTES).hexdigest().upper()
_POLICY = json.loads(_POLICY_BYTES)
_STOP_CONTRACT = dict(_POLICY.get("stop_contract") or {})
STOP_EXACT_OUTPUT: Final[dict[str, Any]] = dict(
    _STOP_CONTRACT.get("evidence_lane_exact_output") or {}
)
STOP_REPLAY_IGNORED_FIELDS: Final = tuple(
    _STOP_CONTRACT.get("replay_identity_ignored_fields") or ()
)

EVENT_ORDER: Final = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "Stop",
    "SessionEnd",
)
MAX_INPUT_BYTES: Final = 1_048_576
MAX_OUTPUT_BYTES: Final = 32_768
DEFAULT_HANDLER_TIMEOUT_SECONDS: Final = 8
SESSION_END_HANDLER_TIMEOUT_SECONDS: Final = 2


class HookEventIsolationError(RuntimeError):
    """One bounded, secret-safe hook-isolation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest().upper()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_policy() -> None:
    if _POLICY.get("schema") != POLICY_SCHEMA:
        raise HookEventIsolationError("HOOK_EVENT_ISOLATION_POLICY_SCHEMA_MISMATCH")
    events = tuple(row.get("event_name") for row in _POLICY.get("events", []))
    if events != EVENT_ORDER:
        raise HookEventIsolationError("HOOK_EVENT_ISOLATION_POLICY_ORDER_MISMATCH")
    if _POLICY.get("input", {}).get("max_bytes") != MAX_INPUT_BYTES:
        raise HookEventIsolationError("HOOK_EVENT_ISOLATION_INPUT_BOUND_MISMATCH")
    if _POLICY.get("output", {}).get("max_bytes") != MAX_OUTPUT_BYTES:
        raise HookEventIsolationError("HOOK_EVENT_ISOLATION_OUTPUT_BOUND_MISMATCH")
    if (
        _STOP_CONTRACT.get("official_input_schema")
        != "stop.command.input.schema.json"
        or _STOP_CONTRACT.get("official_output_schema")
        != "stop.command.output.schema.json"
        or STOP_EXACT_OUTPUT != {}
        or _STOP_CONTRACT.get("evidence_lane_control_fields_emitted") is not False
        or _STOP_CONTRACT.get("continuation_requested") is not False
        or STOP_REPLAY_IGNORED_FIELDS != ("stop_hook_active",)
        or _STOP_CONTRACT.get("first_and_replay_share_occurrence") is not True
        or _STOP_CONTRACT.get("first_and_replay_output_identical") is not True
    ):
        raise HookEventIsolationError("HOOK_STOP_CONTRACT_MISMATCH")
    if _POLICY.get("host_memory_boundary") != {
        "official_documentation": (
            "https://learn.chatgpt.com/docs/customization/memories"
        ),
        "authority": "NONAUTHORITATIVE_HELPFUL_RECALL_ONLY",
        "automatic_import": False,
        "hook_import": False,
        "explicit_provenance_receipt_required": True,
        "raw_host_memory_stored": False,
        "learning_candidate_creation": "SEPARATE_EXPLICIT_ACTION_REQUIRED",
        "learning_hil_invocation": "SEPARATE_EXPLICIT_ACTION_REQUIRED",
        "project_hil_invocation": "FORBIDDEN",
    }:
        raise HookEventIsolationError("HOOK_HOST_MEMORY_BOUNDARY_MISMATCH")


_validate_policy()


_PERMISSION_MODES = {"default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"}
_NULLABLE_STRING = (str, type(None))
_INPUT_RULES: Final[dict[str, dict[str, Any]]] = {
    "SessionStart": {
        "required": {
            "cwd",
            "hook_event_name",
            "model",
            "permission_mode",
            "session_id",
            "source",
            "transcript_path",
        },
        "optional": set(),
        "types": {
            "cwd": str,
            "hook_event_name": str,
            "model": str,
            "permission_mode": str,
            "session_id": str,
            "source": str,
            "transcript_path": _NULLABLE_STRING,
        },
    },
    "UserPromptSubmit": {
        "required": {
            "cwd",
            "hook_event_name",
            "model",
            "permission_mode",
            "prompt",
            "session_id",
            "transcript_path",
            "turn_id",
        },
        "optional": {"agent_id", "agent_type"},
        "types": {
            "agent_id": str,
            "agent_type": str,
            "cwd": str,
            "hook_event_name": str,
            "model": str,
            "permission_mode": str,
            "prompt": str,
            "session_id": str,
            "transcript_path": _NULLABLE_STRING,
            "turn_id": str,
        },
    },
    "PreToolUse": {
        "required": {
            "cwd",
            "hook_event_name",
            "model",
            "permission_mode",
            "session_id",
            "tool_input",
            "tool_name",
            "tool_use_id",
            "transcript_path",
            "turn_id",
        },
        "optional": {"agent_id", "agent_type"},
        "types": {
            "agent_id": str,
            "agent_type": str,
            "cwd": str,
            "hook_event_name": str,
            "model": str,
            "permission_mode": str,
            "session_id": str,
            "tool_name": str,
            "tool_use_id": str,
            "transcript_path": _NULLABLE_STRING,
            "turn_id": str,
        },
    },
    "PostToolUse": {
        "required": {
            "cwd",
            "hook_event_name",
            "model",
            "permission_mode",
            "session_id",
            "tool_input",
            "tool_name",
            "tool_response",
            "tool_use_id",
            "transcript_path",
            "turn_id",
        },
        "optional": {"agent_id", "agent_type"},
        "types": {
            "agent_id": str,
            "agent_type": str,
            "cwd": str,
            "hook_event_name": str,
            "model": str,
            "permission_mode": str,
            "session_id": str,
            "tool_name": str,
            "tool_use_id": str,
            "transcript_path": _NULLABLE_STRING,
            "turn_id": str,
        },
    },
    "PreCompact": {
        "required": {
            "cwd",
            "hook_event_name",
            "model",
            "session_id",
            "transcript_path",
            "trigger",
            "turn_id",
        },
        "optional": {"agent_id", "agent_type"},
        "types": {
            "agent_id": str,
            "agent_type": str,
            "cwd": str,
            "hook_event_name": str,
            "model": str,
            "session_id": str,
            "transcript_path": _NULLABLE_STRING,
            "trigger": str,
            "turn_id": str,
        },
    },
    "PostCompact": {
        "required": {
            "cwd",
            "hook_event_name",
            "model",
            "session_id",
            "transcript_path",
            "trigger",
            "turn_id",
        },
        "optional": {"agent_id", "agent_type"},
        "types": {
            "agent_id": str,
            "agent_type": str,
            "cwd": str,
            "hook_event_name": str,
            "model": str,
            "session_id": str,
            "transcript_path": _NULLABLE_STRING,
            "trigger": str,
            "turn_id": str,
        },
    },
    "Stop": {
        "required": {
            "cwd",
            "hook_event_name",
            "last_assistant_message",
            "model",
            "permission_mode",
            "session_id",
            "stop_hook_active",
            "transcript_path",
            "turn_id",
        },
        "optional": set(),
        "types": {
            "cwd": str,
            "hook_event_name": str,
            "last_assistant_message": _NULLABLE_STRING,
            "model": str,
            "permission_mode": str,
            "session_id": str,
            "stop_hook_active": bool,
            "transcript_path": _NULLABLE_STRING,
            "turn_id": str,
        },
    },
    "SessionEnd": {
        "required": {
            "cwd",
            "hook_event_name",
            "reason",
            "session_id",
            "transcript_path",
        },
        "optional": set(),
        "types": {
            "cwd": str,
            "hook_event_name": str,
            "reason": str,
            "session_id": str,
            "transcript_path": _NULLABLE_STRING,
        },
    },
}

_COMMON_OUTPUT_TYPES: Final = {
    "continue": bool,
    "stopReason": _NULLABLE_STRING,
    "suppressOutput": bool,
    "systemMessage": _NULLABLE_STRING,
}
_OUTPUT_KEYS: Final = {
    "SessionStart": {*_COMMON_OUTPUT_TYPES, "hookSpecificOutput"},
    "UserPromptSubmit": {
        *_COMMON_OUTPUT_TYPES,
        "decision",
        "reason",
        "hookSpecificOutput",
    },
    "PreToolUse": {
        *_COMMON_OUTPUT_TYPES,
        "decision",
        "reason",
        "hookSpecificOutput",
    },
    "PostToolUse": {
        *_COMMON_OUTPUT_TYPES,
        "decision",
        "reason",
        "hookSpecificOutput",
    },
    "PreCompact": set(_COMMON_OUTPUT_TYPES),
    "PostCompact": set(_COMMON_OUTPUT_TYPES),
    "Stop": set(STOP_EXACT_OUTPUT),
    "SessionEnd": set(),
}


def validate_input(event_name: str, raw_payload: str) -> tuple[dict[str, Any], str]:
    encoded = raw_payload.encode("utf-8")
    if not encoded or len(encoded) > MAX_INPUT_BYTES:
        raise HookEventIsolationError("HOOK_EVENT_INPUT_BOUND_INVALID")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise HookEventIsolationError("HOOK_EVENT_INPUT_JSON_INVALID") from exc
    if not isinstance(payload, dict):
        raise HookEventIsolationError("HOOK_EVENT_INPUT_OBJECT_REQUIRED")
    rules = _INPUT_RULES.get(event_name)
    if rules is None:
        raise HookEventIsolationError("HOOK_EVENT_UNSUPPORTED")
    keys = set(payload)
    required = rules["required"]
    allowed = required | rules["optional"]
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise HookEventIsolationError("HOOK_EVENT_INPUT_SCHEMA_MISMATCH")
    for key, expected in rules["types"].items():
        if key in payload and not isinstance(payload[key], expected):
            raise HookEventIsolationError("HOOK_EVENT_INPUT_TYPE_MISMATCH")
    if payload["hook_event_name"] != event_name:
        raise HookEventIsolationError("HOOK_EVENT_INPUT_NAME_MISMATCH")
    if "permission_mode" in payload and payload["permission_mode"] not in _PERMISSION_MODES:
        raise HookEventIsolationError("HOOK_EVENT_PERMISSION_MODE_INVALID")
    if event_name == "SessionStart" and payload["source"] not in {
        "startup",
        "resume",
        "clear",
        "compact",
    }:
        raise HookEventIsolationError("HOOK_EVENT_SESSION_SOURCE_INVALID")
    if event_name in {"PreCompact", "PostCompact"} and payload["trigger"] not in {
        "manual",
        "auto",
    }:
        raise HookEventIsolationError("HOOK_EVENT_COMPACTION_TRIGGER_INVALID")
    if event_name == "SessionEnd" and payload["reason"] != "other":
        raise HookEventIsolationError("HOOK_EVENT_SESSION_END_REASON_INVALID")
    occurrence_payload = dict(payload)
    if event_name == "Stop":
        for field in STOP_REPLAY_IGNORED_FIELDS:
            occurrence_payload.pop(field, None)
    return payload, _sha256(_canonical_bytes(occurrence_payload))


def validate_output(event_name: str, output: Mapping[str, Any]) -> None:
    if not isinstance(output, Mapping):
        raise HookEventIsolationError("HOOK_EVENT_OUTPUT_OBJECT_REQUIRED")
    if event_name == "Stop" and dict(output) != STOP_EXACT_OUTPUT:
        raise HookEventIsolationError("HOOK_EVENT_STOP_OUTPUT_CONTRACT_MISMATCH")
    if event_name == "SessionEnd" and output:
        raise HookEventIsolationError("HOOK_EVENT_TERMINAL_OUTPUT_MUST_BE_EMPTY")
    if set(output).difference(_OUTPUT_KEYS[event_name]):
        raise HookEventIsolationError("HOOK_EVENT_OUTPUT_SCHEMA_MISMATCH")
    for key, expected in _COMMON_OUTPUT_TYPES.items():
        if key in output and not isinstance(output[key], expected):
            raise HookEventIsolationError("HOOK_EVENT_OUTPUT_TYPE_MISMATCH")
    decision = output.get("decision")
    if event_name == "PreToolUse" and decision not in {None, "approve", "block"}:
        raise HookEventIsolationError("HOOK_EVENT_PRE_TOOL_DECISION_INVALID")
    if event_name in {"UserPromptSubmit", "PostToolUse"} and decision not in {
        None,
        "block",
    }:
        raise HookEventIsolationError("HOOK_EVENT_BLOCK_DECISION_INVALID")
    if decision == "block" and not isinstance(output.get("reason"), str):
        raise HookEventIsolationError("HOOK_EVENT_BLOCK_REASON_REQUIRED")
    specific = output.get("hookSpecificOutput")
    if specific is None:
        return
    if not isinstance(specific, Mapping) or specific.get("hookEventName") != event_name:
        raise HookEventIsolationError("HOOK_EVENT_SPECIFIC_OUTPUT_MISMATCH")
    allowed_specific = {
        "SessionStart": {"hookEventName", "additionalContext"},
        "UserPromptSubmit": {"hookEventName", "additionalContext"},
        "PreToolUse": {
            "hookEventName",
            "additionalContext",
            "permissionDecision",
            "permissionDecisionReason",
            "updatedInput",
        },
        "PostToolUse": {
            "hookEventName",
            "additionalContext",
            "updatedMCPToolOutput",
        },
    }.get(event_name, set())
    if set(specific).difference(allowed_specific):
        raise HookEventIsolationError("HOOK_EVENT_SPECIFIC_OUTPUT_SCHEMA_MISMATCH")
    if event_name == "PreToolUse" and specific.get("permissionDecision") not in {
        None,
        "allow",
        "deny",
        "ask",
    }:
        raise HookEventIsolationError("HOOK_EVENT_PERMISSION_DECISION_INVALID")


def _data_root() -> Path:
    configured = os.environ.get("EVIDENCE_LANE_DATA_ROOT", "").strip()
    return Path(configured).resolve() if configured else (Path.home() / "EvidenceLanePV")


def _control_root() -> Path:
    return _data_root() / "hook-event-isolation"


def kill_switch_path() -> Path:
    configured = os.environ.get("EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT", "").strip()
    return Path(configured).resolve() if configured else _control_root() / "KILL_SWITCH.json"


def _read_kill_switch(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        body = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise HookEventIsolationError("HOOK_KILL_SWITCH_RECEIPT_JSON_INVALID") from exc
    if not isinstance(body, dict):
        raise HookEventIsolationError("HOOK_KILL_SWITCH_RECEIPT_INVALID")
    payload = body.get("payload")
    generation = payload.get("generation") if isinstance(payload, dict) else None
    if (
        body.get("schema") != KILL_SWITCH_SCHEMA
        or not isinstance(payload, dict)
        or payload.get("schema") != KILL_SWITCH_SCHEMA
        or body.get("payload_sha256") != _sha256(_canonical_bytes(payload))
        or payload.get("policy_sha256") != POLICY_SHA256
        or payload.get("owner") != "EVIDENCE_LANE_INSTALLED_HOOK_RUNTIME"
        or payload.get("state") not in {"INACTIVE", "ACTIVE"}
        or not isinstance(payload.get("installation_id"), str)
        or not payload["installation_id"].strip()
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        raise HookEventIsolationError("HOOK_KILL_SWITCH_RECEIPT_INVALID")
    return body, payload


def initialize_inactive_kill_switch(
    data_root: Path,
    *,
    installation_id: str,
) -> dict[str, Any]:
    """Create or monotonically rotate the receipt before hook activation.

    An ACTIVE or malformed existing receipt is never overwritten. Repeating
    the exact installation is idempotent; a new installation advances the
    generation while keeping the global kill switch inactive.
    """

    installation_id = installation_id.strip()
    if not installation_id:
        raise HookEventIsolationError("HOOK_KILL_SWITCH_INSTALLATION_ID_REQUIRED")

    path = data_root.resolve() / "hook-event-isolation" / "KILL_SWITCH.json"
    generation = 1
    prior_file_sha256: str | None = None
    if path.exists():
        if not path.is_file():
            raise HookEventIsolationError("HOOK_KILL_SWITCH_RECEIPT_INVALID")
        _body, prior_payload = _read_kill_switch(path)
        if prior_payload["state"] == "ACTIVE":
            raise HookEventIsolationError("HOOK_KILL_SWITCH_ACTIVE")
        prior_file_sha256 = _sha256(path.read_bytes())
        if prior_payload["installation_id"] == installation_id:
            return {
                "schema": KILL_SWITCH_SCHEMA,
                "status": "PASS",
                "state": "INACTIVE_KILL_SWITCH_REUSED",
                "path": str(path),
                "file_sha256": prior_file_sha256,
                "policy_sha256": POLICY_SHA256,
                "installation_id": installation_id,
                "generation": prior_payload["generation"],
                "prior_file_sha256": prior_file_sha256,
            }
        generation = int(prior_payload["generation"]) + 1

    payload = {
        "schema": KILL_SWITCH_SCHEMA,
        "state": "INACTIVE",
        "installation_id": installation_id,
        "policy_sha256": POLICY_SHA256,
        "owner": "EVIDENCE_LANE_INSTALLED_HOOK_RUNTIME",
        "generation": generation,
        "created_at_utc": _utc_now(),
    }
    body = {
        "schema": KILL_SWITCH_SCHEMA,
        "payload": payload,
        "payload_sha256": _sha256(_canonical_bytes(payload)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(_canonical_bytes(body))
    os.replace(temporary, path)
    return {
        "schema": KILL_SWITCH_SCHEMA,
        "status": "PASS",
        "state": "INACTIVE_KILL_SWITCH_INITIALIZED",
        "path": str(path),
        "file_sha256": _sha256(path.read_bytes()),
        "policy_sha256": POLICY_SHA256,
        "installation_id": installation_id,
        "generation": generation,
        "prior_file_sha256": prior_file_sha256,
    }


def verify_inactive_kill_switch() -> dict[str, Any]:
    path = kill_switch_path()
    if not path.is_file():
        raise HookEventIsolationError("HOOK_KILL_SWITCH_RECEIPT_MISSING")
    expected_file_sha256 = os.environ.get(
        "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT_SHA256", ""
    ).strip().upper()
    actual_file_sha256 = _sha256(path.read_bytes())
    if expected_file_sha256 and actual_file_sha256 != expected_file_sha256:
        raise HookEventIsolationError("HOOK_KILL_SWITCH_RECEIPT_SHA_MISMATCH")
    expected_policy_sha256 = os.environ.get(
        "EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256", ""
    ).strip().upper()
    if expected_policy_sha256 and expected_policy_sha256 != POLICY_SHA256:
        raise HookEventIsolationError("HOOK_EVENT_ISOLATION_POLICY_SHA_MISMATCH")
    _body, payload = _read_kill_switch(path)
    if payload["state"] == "ACTIVE":
        raise HookEventIsolationError("HOOK_KILL_SWITCH_ACTIVE")
    return {
        "path": str(path),
        "file_sha256": actual_file_sha256,
        "policy_sha256": POLICY_SHA256,
        "state": "INACTIVE",
        "installation_id": payload["installation_id"],
        "generation": payload["generation"],
    }


class _OwnerLock(AbstractContextManager["_OwnerLock"]):
    def __init__(self, owner_sha256: str) -> None:
        self._path = _control_root() / "locks" / f"{owner_sha256}.lock"
        self._handle: Any = None

    def __enter__(self) -> Self:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a+b")
        self._handle.seek(0)
        if self._path.stat().st_size == 0:
            self._handle.write(b"\0")
            self._handle.flush()
        self._handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise HookEventIsolationError("HOOK_EVENT_REENTRANCY_DENIED") from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def _database() -> sqlite3.Connection:
    root = _control_root()
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "event_receipts.sqlite", timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=2000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS isolation_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_receipt (
            correlation_id TEXT PRIMARY KEY,
            event_name TEXT NOT NULL,
            owner_sha256 TEXT NOT NULL,
            occurrence_input_sha256 TEXT NOT NULL,
            policy_sha256 TEXT NOT NULL,
            kill_switch_sha256 TEXT NOT NULL,
            input_schema TEXT NOT NULL,
            output_schema TEXT,
            output_json TEXT,
            output_sha256 TEXT,
            status TEXT NOT NULL CHECK(status IN ('IN_PROGRESS','COMPLETE','FAIL_CLOSED')),
            failure_code TEXT,
            created_at_utc TEXT NOT NULL,
            completed_at_utc TEXT
        );
        CREATE INDEX IF NOT EXISTS event_receipt_owner_event
            ON event_receipt(owner_sha256, event_name, created_at_utc);
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO isolation_meta(key,value) VALUES('schema',?)",
        (SQLITE_SCHEMA,),
    )
    connection.execute(
        "INSERT OR REPLACE INTO isolation_meta(key,value) VALUES('policy_sha256',?)",
        (POLICY_SHA256,),
    )
    connection.commit()
    return connection


def _identity(
    event_name: str,
    payload: Mapping[str, Any],
    occurrence_input_sha256: str,
) -> tuple[str, str]:
    owner_sha256 = _sha256(
        f"{payload.get('session_id', '')}|{payload.get('cwd', '')}"
    )
    correlation_id = "hook_" + _sha256(
        "|".join(
            (
                event_name,
                owner_sha256,
                str(payload.get("turn_id") or ""),
                str(payload.get("tool_use_id") or ""),
                str(payload.get("source") or ""),
                str(payload.get("trigger") or ""),
                str(payload.get("reason") or ""),
                occurrence_input_sha256,
            )
        )
    )[:40].lower()
    return owner_sha256, correlation_id


def _claim(
    connection: sqlite3.Connection,
    *,
    event_name: str,
    owner_sha256: str,
    correlation_id: str,
    occurrence_input_sha256: str,
    kill_switch_sha256: str,
) -> dict[str, Any] | None:
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT * FROM event_receipt WHERE correlation_id=?",
        (correlation_id,),
    ).fetchone()
    if row is not None:
        connection.commit()
        if (
            row["event_name"] != event_name
            or row["owner_sha256"] != owner_sha256
            or row["occurrence_input_sha256"] != occurrence_input_sha256
            or row["policy_sha256"] != POLICY_SHA256
        ):
            raise HookEventIsolationError("HOOK_EVENT_CORRELATION_COLLISION")
        if row["status"] == "IN_PROGRESS":
            raise HookEventIsolationError("HOOK_EVENT_EXACTLY_ONCE_IN_PROGRESS")
        output = json.loads(row["output_json"] or "{}")
        validate_output(event_name, output)
        return output
    input_schema = next(
        row["input_schema"] for row in _POLICY["events"] if row["event_name"] == event_name
    )
    connection.execute(
        """
        INSERT INTO event_receipt(
            correlation_id,event_name,owner_sha256,occurrence_input_sha256,
            policy_sha256,kill_switch_sha256,input_schema,status,created_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            correlation_id,
            event_name,
            owner_sha256,
            occurrence_input_sha256,
            POLICY_SHA256,
            kill_switch_sha256,
            input_schema,
            "IN_PROGRESS",
            _utc_now(),
        ),
    )
    connection.commit()
    return None


def _finish(
    connection: sqlite3.Connection,
    *,
    event_name: str,
    correlation_id: str,
    output: Mapping[str, Any],
    status: str,
    failure_code: str | None,
) -> None:
    output_bytes = _canonical_bytes(dict(output))
    output_schema = next(
        row["output_schema"] for row in _POLICY["events"] if row["event_name"] == event_name
    )
    connection.execute("BEGIN IMMEDIATE")
    cursor = connection.execute(
        """
        UPDATE event_receipt
        SET output_schema=?, output_json=?, output_sha256=?, status=?,
            failure_code=?, completed_at_utc=?
        WHERE correlation_id=? AND status='IN_PROGRESS'
        """,
        (
            output_schema,
            output_bytes.decode("utf-8"),
            _sha256(output_bytes),
            status,
            failure_code,
            _utc_now(),
            correlation_id,
        ),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise HookEventIsolationError("HOOK_EVENT_TERMINAL_RECEIPT_UPDATE_MISMATCH")
    connection.commit()


def stop_output() -> dict[str, Any]:
    """Return a fresh copy of the strict no-continuation Stop output."""

    return dict(STOP_EXACT_OUTPUT)


def failure_output(event_name: str, code: str) -> dict[str, Any]:
    if event_name == "Stop":
        return stop_output()
    if event_name == "SessionEnd":
        return {}
    diagnostic = (
        "EVIDENCE_LANE_HOOK_EVENT_ISOLATION="
        + _canonical_bytes(
            {
                "schema": EVENT_RECEIPT_SCHEMA,
                "status": "FAIL_CLOSED",
                "event_name": event_name,
                "code": code,
                "raw_payload_stored": False,
                "raw_stderr_stored": False,
                "restart_count_used_for_causation": False,
            }
        ).decode("utf-8")
    )
    if event_name == "PreToolUse":
        return {
            "systemMessage": diagnostic,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Evidence Lane hook isolation failed closed: {code}."
                ),
            },
        }
    return {"systemMessage": diagnostic}


def _run_handler(
    event_name: str,
    handler: Path,
    handler_args: tuple[str, ...],
    raw_payload: str,
    *,
    correlation_id: str,
    owner_sha256: str,
    occurrence_input_sha256: str,
) -> dict[str, Any]:
    timeout = (
        SESSION_END_HANDLER_TIMEOUT_SECONDS
        if event_name == "SessionEnd"
        else DEFAULT_HANDLER_TIMEOUT_SECONDS
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_HOOK_EVENT_CORRELATION_ID"] = correlation_id
    environment["EVIDENCE_LANE_HOOK_EVENT_OWNER_SHA256"] = owner_sha256
    environment["EVIDENCE_LANE_HOOK_EVENT_INPUT_SHA256"] = occurrence_input_sha256
    environment["EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256"] = POLICY_SHA256
    try:
        completed = subprocess.run(
            [sys.executable, str(handler), *handler_args],
            input=raw_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HookEventIsolationError("HOOK_EVENT_HANDLER_TIMEOUT") from exc
    if completed.returncode != 0:
        raise HookEventIsolationError("HOOK_EVENT_HANDLER_NONZERO_EXIT")
    serialized = completed.stdout.strip()
    if not serialized or len(serialized.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise HookEventIsolationError("HOOK_EVENT_OUTPUT_BOUND_INVALID")
    try:
        output = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise HookEventIsolationError("HOOK_EVENT_OUTPUT_JSON_INVALID") from exc
    if not isinstance(output, dict):
        raise HookEventIsolationError("HOOK_EVENT_OUTPUT_OBJECT_REQUIRED")
    validate_output(event_name, output)
    return output


def execute_isolated_hook(
    event_name: str,
    handler: Path,
    handler_args: tuple[str, ...],
    raw_payload: str,
) -> dict[str, Any]:
    payload, occurrence_input_sha256 = validate_input(event_name, raw_payload)
    kill_switch = verify_inactive_kill_switch()
    owner_sha256, correlation_id = _identity(
        event_name,
        payload,
        occurrence_input_sha256,
    )
    with _OwnerLock(owner_sha256), _database() as connection:
        replay = _claim(
            connection,
            event_name=event_name,
            owner_sha256=owner_sha256,
            correlation_id=correlation_id,
            occurrence_input_sha256=occurrence_input_sha256,
            kill_switch_sha256=kill_switch["file_sha256"],
        )
        if replay is not None:
            return replay
        try:
            output = _run_handler(
                event_name,
                handler,
                handler_args,
                raw_payload,
                correlation_id=correlation_id,
                owner_sha256=owner_sha256,
                occurrence_input_sha256=occurrence_input_sha256,
            )
        except HookEventIsolationError as exc:
            output = failure_output(event_name, exc.code)
            validate_output(event_name, output)
            _finish(
                connection,
                event_name=event_name,
                correlation_id=correlation_id,
                output=output,
                status="FAIL_CLOSED",
                failure_code=exc.code,
            )
            return output
        _finish(
            connection,
            event_name=event_name,
            correlation_id=correlation_id,
            output=output,
            status="COMPLETE",
            failure_code=None,
        )
        return output
