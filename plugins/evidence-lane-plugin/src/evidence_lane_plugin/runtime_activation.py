"""Installation-level runtime attachment kept separate from immutable Flash bytes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import atomic_write_json, sha256_file
from .timeutil import utc_now

RUNTIME_ACTIVATION_SCHEMA = "evidence-lane.runtime-activation.v1"


class RuntimeActivation:
    """Track which governed sessions currently attach hooks and Flash context.

    The locked ENV/UOP package and its verification receipt are installation
    evidence.  This receipt controls only live prompt/response capture and
    SessionStart Flash attachment, allowing ``/evi-exit-boot`` to detach the
    runtime without deleting any immutable evidence.
    """

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root).resolve()
        self.path = self.data_root / "installation" / "runtime_activation.json"

    @staticmethod
    def _detached_default() -> dict[str, Any]:
        return {
            "schema": RUNTIME_ACTIVATION_SCHEMA,
            "plugin_id": "evidence-lane-plugin",
            "state": "DETACHED",
            "generation": 0,
            "active_sessions": [],
            "flash_context_attached": False,
            "prompt_capture_active": False,
            "visible_response_capture_active": False,
            "immutable_store_preserved": True,
            "plugin_installation_preserved": True,
            "hil_approval_inferred": False,
        }

    def status(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._detached_default()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EvidenceLaneError(
                "RUNTIME_ACTIVATION_JSON_INVALID",
                "The runtime-activation receipt is not valid JSON.",
                status="FAIL",
                details={"error": str(exc)},
            ) from exc
        sessions = payload.get("active_sessions")
        require(
            payload.get("schema") == RUNTIME_ACTIVATION_SCHEMA
            and payload.get("plugin_id") == "evidence-lane-plugin"
            and payload.get("state") in {"ACTIVE", "DETACHED"}
            and isinstance(payload.get("generation"), int)
            and int(payload["generation"]) >= 0
            and isinstance(sessions, list)
            and all(isinstance(row, dict) for row in sessions),
            "RUNTIME_ACTIVATION_RECEIPT_INVALID",
            "The runtime-activation receipt has an invalid identity or shape.",
            status="MISMATCH",
        )
        expected_state = "ACTIVE" if sessions else "DETACHED"
        active = expected_state == "ACTIVE"
        require(
            payload.get("state") == expected_state
            and payload.get("flash_context_attached") is active
            and payload.get("prompt_capture_active") is active
            and payload.get("visible_response_capture_active") is active
            and payload.get("immutable_store_preserved") is True
            and payload.get("plugin_installation_preserved") is True
            and payload.get("hil_approval_inferred") is False,
            "RUNTIME_ACTIVATION_STATE_INVALID",
            "Runtime attachment flags do not match the active-session set.",
            status="FAIL",
        )
        keys = [
            (str(row.get("project_id", "")), str(row.get("session_id", "")))
            for row in sessions
        ]
        require(
            all(project_id and session_id for project_id, session_id in keys)
            and len(keys) == len(set(keys)),
            "RUNTIME_ACTIVATION_SESSION_SET_INVALID",
            "Runtime attachment contains blank or duplicate session bindings.",
            status="FAIL",
        )
        return payload

    def activate(
        self,
        *,
        project_id: str,
        session_id: str,
        host_session_id: str | None,
        flash: dict[str, Any],
    ) -> dict[str, Any]:
        require(
            flash.get("status") == "PASS"
            and flash.get("receipt") is not None
            and str(flash.get("flash_state", "")).startswith("FLASHED_"),
            "RUNTIME_ACTIVATION_FLASH_REQUIRED",
            "Runtime activation requires a verified locked ENV/UOP Flash receipt.",
            status="BLOCKED",
        )
        current = self.status()
        sessions = [dict(row) for row in current.get("active_sessions", [])]
        conflicting = [
            row
            for row in sessions
            if row.get("project_id") == project_id
            and row.get("session_id") != session_id
        ]
        require(
            not conflicting,
            "RUNTIME_PROJECT_SESSION_CONFLICT",
            "A different governed session is already attached for this project.",
            status="BLOCKED",
            project_id=project_id,
            active_session_ids=[row.get("session_id") for row in conflicting],
        )
        now = utc_now()
        existing = next(
            (
                row
                for row in sessions
                if row.get("project_id") == project_id
                and row.get("session_id") == session_id
            ),
            None,
        )
        exact_host_session_id = str(host_session_id or "").strip()
        if existing is None:
            existing = {
                "project_id": project_id,
                "session_id": session_id,
                "host_session_ids": [],
                "attached_at": now,
            }
            sessions.append(existing)
        host_ids = [str(value) for value in existing.get("host_session_ids", [])]
        if exact_host_session_id and exact_host_session_id not in host_ids:
            host_ids.append(exact_host_session_id)
        existing.update(
            {
                "host_session_ids": host_ids,
                "last_attached_at": now,
                "flash_authority_version": flash.get("authority_version"),
                "flash_authority_digest": flash.get("authority_digest"),
                "flash_receipt_sha256": flash.get("receipt_sha256"),
            }
        )
        payload = {
            "schema": RUNTIME_ACTIVATION_SCHEMA,
            "plugin_id": "evidence-lane-plugin",
            "state": "ACTIVE",
            "generation": int(current.get("generation", 0)) + 1,
            "active_sessions": sorted(
                sessions,
                key=lambda row: (str(row["project_id"]), str(row["session_id"])),
            ),
            "flash_context_attached": True,
            "prompt_capture_active": True,
            "visible_response_capture_active": True,
            "immutable_store_preserved": True,
            "plugin_installation_preserved": True,
            "hil_approval_inferred": False,
            "updated_at": now,
            "last_transition": {
                "action": "ATTACH",
                "project_id": project_id,
                "session_id": session_id,
                "host_session_id": exact_host_session_id or None,
                "occurred_at": now,
            },
        }
        atomic_write_json(self.path, payload)
        result = self.status()
        result["receipt_sha256"] = sha256_file(self.path)
        return result

    def detach(
        self,
        *,
        project_id: str,
        session_id: str,
        reason: str,
    ) -> dict[str, Any]:
        require(
            bool(reason.strip()),
            "RUNTIME_DETACH_REASON_REQUIRED",
            "Runtime detachment requires a visible reason.",
            status="BLOCKED",
        )
        current = self.status()
        project_rows = [
            row
            for row in current.get("active_sessions", [])
            if row.get("project_id") == project_id
        ]
        require(
            not project_rows
            or any(row.get("session_id") == session_id for row in project_rows),
            "RUNTIME_DETACH_SESSION_MISMATCH",
            "Runtime detachment cannot remove a different active session.",
            status="BLOCKED",
            project_id=project_id,
            requested_session_id=session_id,
            active_session_ids=[row.get("session_id") for row in project_rows],
        )
        sessions = [
            dict(row)
            for row in current.get("active_sessions", [])
            if not (
                row.get("project_id") == project_id
                and row.get("session_id") == session_id
            )
        ]
        now = utc_now()
        active = bool(sessions)
        payload = {
            "schema": RUNTIME_ACTIVATION_SCHEMA,
            "plugin_id": "evidence-lane-plugin",
            "state": "ACTIVE" if active else "DETACHED",
            "generation": int(current.get("generation", 0)) + 1,
            "active_sessions": sessions,
            "flash_context_attached": active,
            "prompt_capture_active": active,
            "visible_response_capture_active": active,
            "immutable_store_preserved": True,
            "plugin_installation_preserved": True,
            "hil_approval_inferred": False,
            "updated_at": now,
            "last_transition": {
                "action": "DETACH",
                "project_id": project_id,
                "session_id": session_id,
                "reason": reason.strip(),
                "occurred_at": now,
            },
        }
        atomic_write_json(self.path, payload)
        result = self.status()
        result["receipt_sha256"] = sha256_file(self.path)
        return result
