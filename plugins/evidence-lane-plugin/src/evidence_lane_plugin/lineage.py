"""Append-only, idempotent, secret-redacted ChatLineage records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .constants import LINEAGE_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import atomic_write_bytes, canonical_json_bytes, sha256_bytes
from .ids import prefixed_id
from .redaction import contains_secret, redact

_PRIVATE_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "internal_reasoning",
    "private_reasoning",
    "reasoning_content",
}


def _private_reasoning_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            path = f"{prefix}.{key}" if prefix else str(key)
            if normalized in _PRIVATE_REASONING_KEYS:
                paths.append(path)
            paths.extend(_private_reasoning_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_private_reasoning_paths(item, f"{prefix}[{index}]"))
    return paths


def _actor_for(event_type: str) -> str:
    lowered = event_type.lower()
    if "prompt" in lowered or "user" in lowered or "steer" in lowered:
        return "user"
    if "assistant" in lowered or lowered.endswith(".response"):
        return "assistant"
    if any(
        marker in lowered
        for marker in ("tool", "command", "file", "test", "build", "git", "usage")
    ):
        return "tool"
    return "system"


class ChatLineage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceLaneError(
                    "LINEAGE_INVALID_JSONL",
                    "ChatLineage contains invalid JSON.",
                    status="FAIL",
                    details={"line_number": line_number},
                ) from exc
            expected_hash = sha256_bytes(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in event.items()
                        if key != "event_sha256"
                    }
                )
            )
            require(
                event.get("event_sha256") == expected_hash,
                "LINEAGE_EVENT_HASH_MISMATCH",
                "ChatLineage contains an event whose content hash does not match.",
                status="MISMATCH",
                line_number=line_number,
                event_id=event.get("event_id"),
            )
            if "previous_event_sha256" in event:
                require(
                    event.get("previous_event_sha256")
                    == (events[-1].get("event_sha256") if events else None),
                    "LINEAGE_CHAIN_MISMATCH",
                    "ChatLineage previous-event linkage is not contiguous.",
                    status="MISMATCH",
                    line_number=line_number,
                    event_id=event.get("event_id"),
                )
            private_paths = _private_reasoning_paths(event.get("visible_payload") or {})
            require(
                not private_paths and event.get("private_reasoning_stored") is not True,
                "LINEAGE_PRIVATE_REASONING_FORBIDDEN",
                "ChatLineage contains hidden or private model reasoning fields.",
                status="BLOCKED",
                line_number=line_number,
                forbidden_paths=private_paths,
            )
            require(
                not contains_secret(event),
                "LINEAGE_SECRET_DETECTED",
                "ChatLineage contains an unredacted secret-like value.",
                status="BLOCKED",
                line_number=line_number,
                event_id=event.get("event_id"),
            )
            events.append(event)
        return events

    def append(
        self,
        *,
        event_type: str,
        visible_payload: dict[str, Any],
        occurred_at: str,
        session_id: str,
        task_id: str | None = None,
        run_id: str | None = None,
        event_id: str | None = None,
        actor_type: str | None = None,
        model: str | None = None,
        submodel: str | None = None,
        token_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        require(
            bool(event_type.strip()),
            "LINEAGE_EVENT_TYPE_REQUIRED",
            "ChatLineage event_type is required.",
        )
        private_paths = _private_reasoning_paths(visible_payload)
        require(
            not private_paths,
            "LINEAGE_PRIVATE_REASONING_FORBIDDEN",
            "Hidden or private model reasoning fields cannot enter ChatLineage.",
            status="BLOCKED",
            forbidden_paths=private_paths,
        )
        safe_payload = redact(visible_payload)
        if contains_secret(safe_payload):
            raise EvidenceLaneError(
                "LINEAGE_SECRET_REDACTION_FAILED",
                "A secret-like value remained after ChatLineage redaction.",
                status="BLOCKED",
            )
        events = self._events()
        exact_event_id = event_id or prefixed_id("evt")
        matches = [
            existing
            for existing in events
            if existing.get("event_id") == exact_event_id
        ]
        lineage_index = (
            int(matches[0].get("lineage_index", len(events)))
            if matches
            else len(events) + 1
        )
        previous_event_sha256 = (
            matches[0].get("previous_event_sha256")
            if matches
            else (events[-1].get("event_sha256") if events else None)
        )
        metrics = redact(token_metrics or {"availability": "UNAVAILABLE"})
        event = {
            "schema": LINEAGE_SCHEMA,
            "event_id": exact_event_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "session_id": session_id,
            "task_id": task_id,
            "run_id": run_id,
            "lineage_index": lineage_index,
            "previous_event_sha256": previous_event_sha256,
            "actor_type": actor_type or _actor_for(event_type),
            "model": model,
            "submodel": submodel,
            "token_metrics": metrics,
            "visible_payload": safe_payload,
            "visible_payload_sha256": sha256_bytes(canonical_json_bytes(safe_payload)),
            "private_reasoning_stored": False,
        }
        event["event_sha256"] = sha256_bytes(
            canonical_json_bytes(
                {key: value for key, value in event.items() if key != "event_sha256"}
            )
        )
        if matches:
            require(
                matches[0] == event,
                "LINEAGE_EVENT_ID_CONFLICT",
                "An existing ChatLineage event uses the same ID with different content.",
                status="BLOCKED",
                event_id=event["event_id"],
            )
            return matches[0]
        events.append(event)
        payload = b"".join(canonical_json_bytes(item) for item in events)
        atomic_write_bytes(self.path, payload)
        return event

    def copy_from(self, source: str | Path) -> None:
        source_path = Path(source)
        if not source_path.exists():
            atomic_write_bytes(self.path, b"")
            return
        source_events = ChatLineage(source_path)._events()
        seen: set[str] = set()
        for event in source_events:
            event_id = event.get("event_id")
            require(
                isinstance(event_id, str) and event_id not in seen,
                "LINEAGE_DUPLICATE_EVENT",
                "ChatLineage event IDs must be unique.",
                status="FAIL",
                event_id=event_id,
            )
            seen.add(cast(str, event_id))
        atomic_write_bytes(
            self.path,
            b"".join(canonical_json_bytes(item) for item in source_events),
        )

    def events(self) -> list[dict[str, Any]]:
        return self._events()


def lineage_sha256(path: str | Path) -> str:
    target = Path(path)
    return sha256_bytes(target.read_bytes() if target.exists() else b"")
