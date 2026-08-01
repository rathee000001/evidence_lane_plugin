"""Index the secret-redacted visible prompt without retaining raw secrets."""

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


def _safe_prompt(prompt: str) -> tuple[str, str, int]:
    redacted = prompt
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, _sha256(redacted.encode("utf-8")), len(redacted)


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
            if session.get("metadata", {}).get("closed_at"):
                continue
            project = json.loads(
                (project_root / "project.json").read_text(encoding="utf-8")
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
        binding = {
            "project_id": session.get("project_id"),
            "evidence_session_id": session.get("session_id"),
            "entry_pv": session.get("metadata", {}).get("entry_pv"),
            "pointer_generation": session.get("accepted_pointer_generation"),
            "task_id": (
                session.get("task", {}).get("task_id")
                if isinstance(session.get("task"), dict)
                else None
            ),
            "lifecycle_state": session.get("state"),
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


def _acquire_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(40):
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            time.sleep(0.025)
    raise TimeoutError("Evidence Lane prompt-lineage lock is busy.")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
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
    input_kind: str,
) -> dict[str, Any]:
    lineage_path = (
        Path(binding["project_root"])
        / "lineage"
        / f"{binding['evidence_session_id']}.jsonl"
    )
    lock_path = lineage_path.with_suffix(".jsonl.turn-index.lock")
    lock_descriptor = _acquire_lock(lock_path)
    try:
        events: list[dict[str, Any]] = []
        if lineage_path.exists():
            events = [
                json.loads(line)
                for line in lineage_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        event_id = (
            "evt_"
            + _sha256(
                (
                    str(binding["evidence_session_id"])
                    + "\0"
                    + str(record["turn_id"])
                    + "\0visible-user-prompt"
                ).encode("utf-8")
            )[:26].lower()
        )
        existing = next(
            (event for event in events if event.get("event_id") == event_id),
            None,
        )
        if existing is not None:
            return existing
        safe_payload = {
            "turn_id": record["turn_id"],
            "prompt_index": record["prompt_index"],
            "input_kind": input_kind,
            "visible_user_prompt_after_redaction": record[
                "visible_prompt_after_redaction"
            ],
            "prompt_sha256_after_redaction": record["prompt_sha256_after_redaction"],
            "prompt_record_sha256": record["record_sha256"],
            "entry_pv": record.get("entry_pv"),
            "pointer_generation": record.get("pointer_generation"),
            "lifecycle_state": binding.get("lifecycle_state"),
            "private_reasoning_excluded": True,
        }
        event = {
            "schema": "evidence-lane.chat-lineage.event.v1",
            "event_id": event_id,
            "event_type": "turn.visible_user_prompt",
            "occurred_at": record["recorded_at"],
            "session_id": binding["evidence_session_id"],
            "task_id": binding.get("task_id"),
            "run_id": None,
            "lineage_index": len(events) + 1,
            "previous_event_sha256": (
                events[-1].get("event_sha256") if events else None
            ),
            "actor_type": "user",
            "model": None,
            "submodel": None,
            "token_metrics": {"availability": "UNAVAILABLE"},
            "visible_payload": safe_payload,
            "visible_payload_sha256": _sha256(_canonical_bytes(safe_payload)),
            "private_reasoning_stored": False,
        }
        event["event_sha256"] = _sha256(
            _canonical_bytes(
                {key: value for key, value in event.items() if key != "event_sha256"}
            )
        )
        events.append(event)
        _atomic_write(lineage_path, b"".join(_canonical_bytes(item) for item in events))
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
    prompt = str(payload.get("prompt", ""))
    if not host_session_id or not turn_id:
        return {
            "state": "NOT_INDEXED",
            "reason": "HOST_SESSION_OR_TURN_ID_MISSING",
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
            "raw_prompt_stored": False,
        }
    host_key = f"host-{_sha256(host_session_id.encode('utf-8'))[:40].lower()}"
    folder = root / "prompt-index" / host_key
    folder.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    for path in (root / "prompt-index").glob("host-*/*.json"):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if candidate.get("project_id") == binding.get("project_id") and candidate.get(
            "evidence_session_id"
        ) == binding.get("evidence_session_id"):
            existing.append(candidate)
    existing.sort(key=lambda row: int(row.get("prompt_index", 0)))
    prior_hash = None
    prompt_index = 1
    if existing:
        prior = existing[-1]
        prior_hash = prior.get("record_sha256")
        prompt_index = int(prior.get("prompt_index", len(existing))) + 1
    visible_prompt, prompt_hash, prompt_chars = _safe_prompt(prompt)
    record = {
        "schema": "evidence-lane.prompt-index.v1",
        "host_session_id": host_session_id,
        "turn_id": turn_id,
        "prompt_index": prompt_index,
        "visible_prompt_after_redaction": visible_prompt,
        "prompt_sha256_after_redaction": prompt_hash,
        "prompt_chars_after_redaction": prompt_chars,
        "raw_prompt_stored": False,
        "redacted_visible_prompt_stored": True,
        "project_id": binding.get("project_id"),
        "evidence_session_id": binding.get("evidence_session_id"),
        "entry_pv": binding.get("entry_pv"),
        "pointer_generation": binding.get("pointer_generation"),
        "cwd_sha256": _sha256(str(payload.get("cwd", "")).encode("utf-8")),
        "prior_record_sha256": prior_hash,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    record["record_sha256"] = _sha256(_canonical_bytes(record))
    turn_key = _sha256(turn_id.encode("utf-8"))[:16].lower()
    while True:
        path = folder / f"{prompt_index:08d}-{turn_key}.json"
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            break
        except FileExistsError:
            prompt_index += 1
            record["prompt_index"] = prompt_index
            record["record_sha256"] = _sha256(
                _canonical_bytes(
                    {
                        key: value
                        for key, value in record.items()
                        if key != "record_sha256"
                    }
                )
            )
    try:
        os.write(descriptor, _canonical_bytes(record))
    finally:
        os.close(descriptor)
    event = _append_lineage(
        binding=binding,
        record=record,
        input_kind=str(payload.get("source") or "user_prompt"),
    )
    return {
        "state": "INDEXED",
        "prompt_index": prompt_index,
        "turn_id": turn_id,
        "project_id": record["project_id"],
        "evidence_session_id": record["evidence_session_id"],
        "entry_pv": record["entry_pv"],
        "raw_prompt_stored": False,
        "redacted_visible_prompt_stored": True,
        "record_sha256": record["record_sha256"],
        "lineage_event_id": event["event_id"],
        "lineage_event_sha256": event["event_sha256"],
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        indexed = _record(payload)
    except Exception as exc:  # noqa: BLE001 - hooks must fail open without prompt data
        indexed = {
            "state": "INDEX_FAILED",
            "error_type": type(exc).__name__,
            "raw_prompt_stored": False,
        }
    print(
        json.dumps(
            {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        "EVIDENCE_LANE_PROMPT_ENTRY="
                        + json.dumps(indexed, sort_keys=True, separators=(",", ":"))
                    ),
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
