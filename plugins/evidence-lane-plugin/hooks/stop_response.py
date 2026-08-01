"""Index the exact visible assistant response without crossing a human gate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"(?i)\b(?:authorization|bearer|password|token|secret)\b\s*[:=]\s*\S+"),
)
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)|https?://[^\s)>]+")
_TOKEN_METRIC_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
}


def _store_root() -> Path:
    return Path(
        os.environ.get("EVIDENCE_LANE_DATA_ROOT")
        or os.environ.get("PLUGIN_DATA")
        or Path.home() / "EvidenceLanePV"
    ).resolve()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _redact(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _telemetry(
    payload: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any]]:
    model = str(payload.get("model") or payload.get("model_name") or "").strip() or None
    submodel = (
        str(payload.get("submodel") or payload.get("model_slug") or "").strip() or None
    )
    raw_usage = payload.get("usage") or payload.get("token_usage") or {}
    metrics = (
        {
            str(key): value
            for key, value in raw_usage.items()
            if str(key) in _TOKEN_METRIC_KEYS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        }
        if isinstance(raw_usage, dict)
        else {}
    )
    return (
        _redact(model) if model else None,
        _redact(submodel) if submodel else None,
        {"availability": "AVAILABLE", **metrics}
        if metrics
        else {"availability": "UNAVAILABLE"},
    )


def _output_links(text: str) -> list[str]:
    values: list[str] = []
    for match in _LINK_RE.finditer(text):
        value = (match.group(1) or match.group(0)).strip()
        if value and value not in values:
            values.append(value)
    return values[:100]


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _active_binding(
    root: Path,
    *,
    host_session_id: str,
    cwd: str,
) -> dict[str, Any]:
    projects_root = root / "projects"
    if not projects_root.is_dir():
        return {}
    exact_matches: list[dict[str, Any]] = []
    cwd_matches: list[dict[str, Any]] = []
    current_cwd = Path(cwd).resolve() if cwd else None
    for project_root in sorted(projects_root.iterdir(), key=lambda item: item.name):
        try:
            active = json.loads(
                (project_root / "active_session.json").read_text(encoding="utf-8")
            )
            session = json.loads(
                (project_root / "sessions" / f"{active['session_id']}.json").read_text(
                    encoding="utf-8"
                )
            )
            project = json.loads(
                (project_root / "project.json").read_text(encoding="utf-8")
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
        if session.get("metadata", {}).get("closed_at"):
            continue
        binding = {
            "project_id": session.get("project_id"),
            "evidence_session_id": session.get("session_id"),
            "entry_pv": session.get("metadata", {}).get("entry_pv"),
            "pointer_generation": session.get("accepted_pointer_generation"),
            "lifecycle_state": session.get("state"),
            "task_id": (
                session.get("task", {}).get("task_id")
                if isinstance(session.get("task"), dict)
                else None
            ),
            "project_root": project_root,
        }
        if (
            session.get("metadata", {}).get("current_host_session_id")
            == host_session_id
        ):
            exact_matches.append(binding)
        elif current_cwd and _within(current_cwd, Path(project["repository_path"])):
            cwd_matches.append(binding)
    if len(exact_matches) == 1:
        return exact_matches[0]
    if not exact_matches and len(cwd_matches) == 1:
        return cwd_matches[0]
    return {}


def _prompt_record(
    root: Path,
    *,
    host_session_id: str,
    turn_id: str,
    binding: dict[str, Any],
) -> dict[str, Any] | None:
    host_key = f"host-{_sha256(host_session_id.encode('utf-8'))[:40].lower()}"
    matches: list[dict[str, Any]] = []
    for path in sorted((root / "prompt-index" / host_key).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (
            record.get("turn_id") == turn_id
            and record.get("project_id") == binding.get("project_id")
            and record.get("evidence_session_id") == binding.get("evidence_session_id")
        ):
            matches.append(record)
    return matches[0] if len(matches) == 1 else None


def _acquire_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(40):
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            time.sleep(0.025)
    raise TimeoutError("Evidence Lane response-index lock is busy.")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _append_lineage(
    *,
    binding: dict[str, Any],
    record: dict[str, Any],
    occurred_at: str,
) -> dict[str, Any]:
    lineage_path = (
        Path(binding["project_root"])
        / "lineage"
        / f"{binding['evidence_session_id']}.jsonl"
    )
    lock_path = lineage_path.with_suffix(".jsonl.response-index.lock")
    lock_descriptor = _acquire_lock(lock_path)
    try:
        events: list[dict[str, Any]] = []
        if lineage_path.exists():
            for line in lineage_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        event_id = (
            "evt_"
            + _sha256(
                (
                    str(binding["evidence_session_id"])
                    + "\0"
                    + str(record["turn_id"])
                    + "\0visible-assistant-response"
                ).encode("utf-8")
            )[:26].lower()
        )
        existing = next(
            (event for event in events if event.get("event_id") == event_id),
            None,
        )
        if existing is not None:
            return existing
        event = {
            "schema": "evidence-lane.chat-lineage.event.v1",
            "event_id": event_id,
            "event_type": "turn.visible_assistant_response",
            "occurred_at": occurred_at,
            "session_id": binding["evidence_session_id"],
            "task_id": binding.get("task_id"),
            "run_id": None,
            "lineage_index": len(events) + 1,
            "previous_event_sha256": (
                events[-1].get("event_sha256") if events else None
            ),
            "actor_type": "assistant",
            "model": record.get("model"),
            "submodel": record.get("submodel"),
            "token_metrics": record.get("token_metrics")
            or {"availability": "UNAVAILABLE"},
            "visible_payload": {
                "turn_id": record["turn_id"],
                "prompt_index": record["prompt_index"],
                "prompt_record_sha256": record["prompt_record_sha256"],
                "visible_user_prompt_after_redaction": record.get(
                    "visible_user_prompt_after_redaction"
                ),
                "response_record_sha256": record["record_sha256"],
                "response_sha256_after_redaction": (
                    record["response_sha256_after_redaction"]
                ),
                "response_chars_after_redaction": (
                    record["response_chars_after_redaction"]
                ),
                "visible_assistant_response_after_redaction": (
                    record["visible_assistant_response_after_redaction"]
                ),
                "output_links": record.get("output_links", []),
                "entry_pv": record.get("entry_pv"),
                "pointer_generation": record.get("pointer_generation"),
                "lifecycle_state": record.get("lifecycle_state"),
                "previous_lineage_event_sha256": (
                    events[-1].get("event_sha256") if events else None
                ),
                "private_reasoning_excluded": True,
                "hook_continuation_requested": False,
                "composer_mutated": False,
            },
            "private_reasoning_stored": False,
        }
        event["visible_payload_sha256"] = _sha256(
            _canonical_bytes(event["visible_payload"])
        )
        event["event_sha256"] = _sha256(
            _canonical_bytes(
                {key: value for key, value in event.items() if key != "event_sha256"}
            )
        )
        events.append(event)
        _atomic_write(
            lineage_path,
            b"".join(_canonical_bytes(item) for item in events),
        )
        return event
    finally:
        os.close(lock_descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    host_session_id = str(payload.get("session_id", "")).strip()
    turn_id = str(payload.get("turn_id", "")).strip()
    response = str(payload.get("last_assistant_message", ""))
    if not host_session_id or not turn_id or not response:
        return {
            "state": "NOT_INDEXED",
            "reason": "HOST_SESSION_TURN_OR_VISIBLE_RESPONSE_MISSING",
            "private_reasoning_stored": False,
        }
    root = _store_root()
    binding = _active_binding(
        root,
        host_session_id=host_session_id,
        cwd=str(payload.get("cwd", "")),
    )
    if not binding:
        return {
            "state": "NOT_INDEXED",
            "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
            "private_reasoning_stored": False,
        }
    prompt_record = _prompt_record(
        root,
        host_session_id=host_session_id,
        turn_id=turn_id,
        binding=binding,
    )
    if prompt_record is None:
        return {
            "state": "NOT_INDEXED",
            "reason": "MATCHING_PROMPT_INDEX_REQUIRED",
            "private_reasoning_stored": False,
        }
    visible_response = _redact(response)
    model, submodel, token_metrics = _telemetry(payload)
    host_key = f"host-{_sha256(host_session_id.encode('utf-8'))[:40].lower()}"
    folder = root / "response-index" / host_key
    turn_key = _sha256(turn_id.encode("utf-8"))[:16].lower()
    path = folder / f"{int(prompt_record['prompt_index']):08d}-{turn_key}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("response_sha256_after_redaction") != _sha256(
            visible_response.encode("utf-8")
        ) or existing.get("prompt_record_sha256") != prompt_record.get("record_sha256"):
            return {
                "state": "INDEX_CONFLICT",
                "reason": "TURN_ID_ALREADY_BINDS_DIFFERENT_VISIBLE_RESPONSE",
                "private_reasoning_stored": False,
                "hook_continuation_requested": False,
            }
        event = _append_lineage(
            binding=binding,
            record=existing,
            occurred_at=str(existing["recorded_at"]),
        )
        return {
            "state": "INDEXED_EXISTING",
            "prompt_index": existing["prompt_index"],
            "turn_id": existing["turn_id"],
            "project_id": existing["project_id"],
            "evidence_session_id": existing["evidence_session_id"],
            "record_sha256": existing["record_sha256"],
            "lineage_event_id": event["event_id"],
            "lineage_event_sha256": event["event_sha256"],
            "private_reasoning_stored": False,
            "hook_continuation_requested": False,
        }
    occurred_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record = {
        "schema": "evidence-lane.response-index.v1",
        "turn_id": turn_id,
        "prompt_index": int(prompt_record["prompt_index"]),
        "prompt_record_sha256": prompt_record["record_sha256"],
        "visible_user_prompt_after_redaction": prompt_record.get(
            "visible_prompt_after_redaction"
        ),
        "visible_assistant_response_after_redaction": visible_response,
        "response_sha256_after_redaction": _sha256(visible_response.encode("utf-8")),
        "response_chars_after_redaction": len(visible_response),
        "output_links": _output_links(visible_response),
        "model": model,
        "submodel": submodel,
        "token_metrics": token_metrics,
        "raw_response_stored": False,
        "redacted_visible_response_stored": True,
        "private_reasoning_stored": False,
        "project_id": binding.get("project_id"),
        "evidence_session_id": binding.get("evidence_session_id"),
        "entry_pv": binding.get("entry_pv"),
        "pointer_generation": binding.get("pointer_generation"),
        "lifecycle_state": binding.get("lifecycle_state"),
        "recorded_at": occurred_at,
        "hook_continuation_requested": False,
        "composer_mutated": False,
    }
    record["record_sha256"] = _sha256(_canonical_bytes(record))
    _atomic_write(path, _canonical_bytes(record))
    event = _append_lineage(
        binding=binding,
        record=record,
        occurred_at=occurred_at,
    )
    return {
        "state": "INDEXED",
        "prompt_index": record["prompt_index"],
        "turn_id": turn_id,
        "project_id": record["project_id"],
        "evidence_session_id": record["evidence_session_id"],
        "record_sha256": record["record_sha256"],
        "lineage_event_id": event["event_id"],
        "lineage_event_sha256": event["event_sha256"],
        "private_reasoning_stored": False,
        "hook_continuation_requested": False,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        indexed = _record(payload)
    except Exception as exc:  # noqa: BLE001 - hook must always fail open
        indexed = {
            "state": "INDEX_FAILED",
            "error_type": type(exc).__name__,
            "private_reasoning_stored": False,
            "hook_continuation_requested": False,
        }
    print(json.dumps({"continue": True}, separators=(",", ":")))
    if os.environ.get("EVIDENCE_LANE_HOOK_DEBUG") == "1":
        print(
            json.dumps(
                {"evidence_lane_visible_response_index": indexed},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
