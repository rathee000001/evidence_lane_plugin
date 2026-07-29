"""Index prompt entry state without retaining raw prompt text or secrets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
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


def _safe_prompt_hash(prompt: str) -> tuple[str, int]:
    redacted = prompt
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return _sha256(redacted.encode("utf-8")), len(redacted)


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
    prompt_hash, prompt_chars = _safe_prompt_hash(prompt)
    record = {
        "schema": "evidence-lane.prompt-index.v1",
        "host_session_id": host_session_id,
        "turn_id": turn_id,
        "prompt_index": prompt_index,
        "prompt_sha256_after_redaction": prompt_hash,
        "prompt_chars_after_redaction": prompt_chars,
        "raw_prompt_stored": False,
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
    return {
        "state": "INDEXED",
        "prompt_index": prompt_index,
        "turn_id": turn_id,
        "project_id": record["project_id"],
        "evidence_session_id": record["evidence_session_id"],
        "entry_pv": record["entry_pv"],
        "raw_prompt_stored": False,
        "record_sha256": record["record_sha256"],
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
