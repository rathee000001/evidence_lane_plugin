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


class ChatLineage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
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
    ) -> dict[str, Any]:
        require(
            bool(event_type.strip()),
            "LINEAGE_EVENT_TYPE_REQUIRED",
            "ChatLineage event_type is required.",
        )
        safe_payload = redact(visible_payload)
        if contains_secret(safe_payload):
            raise EvidenceLaneError(
                "LINEAGE_SECRET_REDACTION_FAILED",
                "A secret-like value remained after ChatLineage redaction.",
                status="BLOCKED",
            )
        event = {
            "schema": LINEAGE_SCHEMA,
            "event_id": event_id or prefixed_id("evt"),
            "event_type": event_type,
            "occurred_at": occurred_at,
            "session_id": session_id,
            "task_id": task_id,
            "run_id": run_id,
            "visible_payload": safe_payload,
        }
        event["event_sha256"] = sha256_bytes(
            canonical_json_bytes(
                {key: value for key, value in event.items() if key != "event_sha256"}
            )
        )
        events = self._events()
        matches = [
            existing
            for existing in events
            if existing.get("event_id") == event["event_id"]
        ]
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
