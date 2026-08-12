"""Installation-level runtime attachment kept separate from immutable Flash bytes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import atomic_write_json, sha256_file
from .timeutil import utc_now

RUNTIME_ACTIVATION_SCHEMA = "evidence-lane.runtime-activation.v1"
HOOK_TRUST_SCHEMA = "evidence-lane.codex-hook-trust.v1"
_EXPECTED_HOOK_EVENTS = {
    "postToolUse",
    "sessionStart",
    "stop",
    "userPromptSubmit",
}
_REQUIRED_PRE_REASONING_SURFACES = (
    {
        "surface": "USER_PROMPT_CORRECTION_OR_HIL_TOKEN",
        "host_route": "turn/start -> inspect_pending_input(TurnInput::UserInput)",
        "native_hook_event": "userPromptSubmit",
        "host_dispatch_supported": True,
    },
    {
        "surface": "MID_GOAL_STEER",
        "host_route": "turn/steer -> inspect_pending_input(TurnInput::UserInput)",
        "native_hook_event": "userPromptSubmit",
        "host_dispatch_supported": True,
    },
    {
        "surface": "GOAL_CONTINUATION",
        "host_route": (
            "turn/start(goal continuation) -> "
            "inspect_pending_input(TurnInput::UserInput)"
        ),
        "native_hook_event": "userPromptSubmit",
        "host_dispatch_supported": True,
    },
)
_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_CODEX_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _sealed_json_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()


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

    def host_hook_status(self) -> dict[str, Any]:
        """Project whether the current installed selector is actually runnable."""

        installation_path = (
            self.data_root
            / "installations"
            / "codex-v200"
            / "CURRENT_INSTALLATION.json"
        )
        unavailable = {
            "schema": "evidence-lane.codex-host-hook-status.v1",
            "status": "UNAVAILABLE",
            "trusted": False,
            "plugin_selector": None,
            "hook_count": 0,
            "registered_events": [],
            "installation_receipt_sha256": None,
            "hook_trust_receipt_sha256": None,
            "reason": "SEALED_CURRENT_INSTALLATION_HOOK_TRUST_UNAVAILABLE",
        }
        if not installation_path.is_file():
            return unavailable
        try:
            installation = json.loads(
                installation_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {**unavailable, "reason": "CURRENT_INSTALLATION_JSON_INVALID"}
        if not isinstance(installation, dict):
            return {**unavailable, "reason": "CURRENT_INSTALLATION_SHAPE_INVALID"}
        installation_core = dict(installation)
        claimed_installation_sha256 = str(
            installation_core.pop("receipt_sha256", "")
        ).upper()
        activation = dict(installation.get("activation") or {})
        plugin_add = dict(activation.get("plugin_add") or {})
        hook_trust = dict(activation.get("hook_trust") or {})
        hook_core = dict(hook_trust)
        claimed_hook_sha256 = str(hook_core.pop("receipt_sha256", "")).upper()
        records = hook_trust.get("records")
        if not isinstance(records, list):
            records = []
        events = {
            str(row.get("event_name") or "")
            for row in records
            if isinstance(row, dict)
        }
        keys = [
            str(row.get("hook_key") or "")
            for row in records
            if isinstance(row, dict)
        ]
        selector = str(hook_trust.get("plugin_selector") or "")
        valid = (
            installation.get("schema")
            == "evidence-lane.codex-stable-installation.v2"
            and installation.get("status") == "PASS"
            and _SHA256_RE.fullmatch(claimed_installation_sha256) is not None
            and claimed_installation_sha256 == _sealed_json_sha256(installation_core)
            and activation.get("state") == "INSTALLED_RESTART_REQUIRED"
            and hook_trust.get("schema") == HOOK_TRUST_SCHEMA
            and hook_trust.get("status") == "PASS"
            and _SHA256_RE.fullmatch(claimed_hook_sha256) is not None
            and claimed_hook_sha256 == _sealed_json_sha256(hook_core)
            and selector == plugin_add.get("pluginId")
            and selector.startswith("evidence-lane-plugin@")
            and hook_trust.get("hook_count") == 4
            and events == _EXPECTED_HOOK_EVENTS
            and len(records) == 4
            and len(keys) == len(set(keys)) == 4
            and hook_trust.get("after_trust_statuses") == ["trusted"]
            and all(
                isinstance(row, dict)
                and row.get("enabled") is True
                and row.get("trust_status") == "trusted"
                and str(row.get("hook_key") or "").startswith(f"{selector}:")
                and _CODEX_SHA256_RE.fullmatch(
                    str(row.get("current_hash") or "")
                )
                is not None
                for row in records
            )
        )
        if not valid:
            return {
                **unavailable,
                "status": "MISMATCH",
                "plugin_selector": selector or None,
                "reason": "CURRENT_INSTALLATION_HOOK_TRUST_SEAL_MISMATCH",
            }
        return {
            "schema": "evidence-lane.codex-host-hook-status.v1",
            "status": "TRUSTED",
            "trusted": True,
            "plugin_selector": selector,
            "hook_count": 4,
            "registered_events": sorted(events),
            "installation_receipt_sha256": claimed_installation_sha256,
            "hook_trust_receipt_sha256": claimed_hook_sha256,
            "reason": None,
        }

    def status_with_host_proof(self) -> dict[str, Any]:
        """Separate configured hook trust from complete pre-reasoning coverage."""

        configured = self.status()
        hook_status = self.host_hook_status()
        session_active = configured.get("state") == "ACTIVE"
        prompt_configured = configured.get("prompt_capture_active") is True
        response_configured = (
            configured.get("visible_response_capture_active") is True
        )
        hooks_trusted = hook_status.get("trusted") is True
        registered_events = set(hook_status.get("registered_events") or [])
        capture_surfaces: list[dict[str, Any]] = []
        for contract in _REQUIRED_PRE_REASONING_SURFACES:
            native_event = contract["native_hook_event"]
            registered = bool(native_event and native_event in registered_events)
            runnable = bool(
                session_active
                and prompt_configured
                and hooks_trusted
                and contract["host_dispatch_supported"]
                and registered
            )
            if not contract["host_dispatch_supported"]:
                state = "HOST_CAPABILITY_UNAVAILABLE"
            elif not registered:
                state = "REQUIRED_HOOK_NOT_REGISTERED"
            elif not hooks_trusted:
                state = "HOOK_NOT_SEALED_TRUSTED"
            elif not session_active or not prompt_configured:
                state = "GOVERNED_RUNTIME_NOT_ATTACHED"
            else:
                state = "RUNNABLE_REQUIRES_PER_INPUT_PREPARE_RECEIPT"
            capture_surfaces.append(
                {
                    **contract,
                    "native_hook_registered": registered,
                    "sealed_hook_trusted": bool(registered and hooks_trusted),
                    "pre_reasoning_dispatch_runnable": runnable,
                    "per_input_invocation_proven": False,
                    "state": state,
                }
            )
        complete_coverage = all(
            row["pre_reasoning_dispatch_runnable"] for row in capture_surfaces
        )
        missing_surfaces = [
            row["surface"]
            for row in capture_surfaces
            if not row["pre_reasoning_dispatch_runnable"]
        ]
        partial_capture = any(
            row["pre_reasoning_dispatch_runnable"] for row in capture_surfaces
        )
        projected = dict(configured)
        projected.update(
            {
                "prompt_capture_configured": prompt_configured,
                "visible_response_capture_configured": response_configured,
                "prompt_capture_active": (
                    session_active
                    and prompt_configured
                    and hooks_trusted
                    and complete_coverage
                ),
                "prompt_capture_partially_available": partial_capture,
                "required_pre_reasoning_capture_complete": complete_coverage,
                "required_pre_reasoning_capture_surfaces": capture_surfaces,
                "missing_required_pre_reasoning_surfaces": missing_surfaces,
                "per_input_prepare_receipt_required": True,
                "visible_response_capture_active": (
                    session_active and response_configured and hooks_trusted
                ),
                "host_hook_status": hook_status,
                "host_hooks_runnable": hooks_trusted,
                "capture_truth_law": (
                    "ALL_VISIBLE_INPUT_SURFACES_REQUIRE_PRE_REASONING_HOST_DISPATCH_"
                    "AND_ONE_SEALED_PREPARE_RECEIPT_PER_INPUT"
                ),
                "capture_gap_code": (
                    None
                    if complete_coverage
                    else "HOST_PRE_REASONING_USER_INPUT_HOOK_UNAVAILABLE"
                ),
            }
        )
        return projected

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
