"""Authoritative visible-response COMMIT adapter for governed Codex turns."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _store_root() -> Path:
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        resolve_codex_hook_store_root,
    )

    return resolve_codex_hook_store_root()


def _load_control():
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        TurnControlError,
        bind_codex_host_payload,
        commit_turn,
        gap_receipt,
        persistent_change_system_message,
        persistent_change_system_notice,
        policy_state,
    )

    return (
        TurnControlError,
        bind_codex_host_payload,
        commit_turn,
        gap_receipt,
        persistent_change_system_message,
        persistent_change_system_notice,
        policy_state,
    )


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    root = _store_root()
    (
        TurnControlError,
        bind_codex_host_payload,
        commit_turn,
        gap_receipt,
        _,
        _,
        policy_state,
    ) = _load_control()
    raw_policy = policy_state(
        root,
        host_session_id=str(payload.get("session_id") or "").strip(),
        cwd=str(payload.get("cwd") or ""),
        transcript_path=str(
            payload.get("transcript_path")
            or payload.get("agent_transcript_path")
            or ""
        ),
    )
    try:
        normalized_payload, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="Stop",
            allow_alias_claim=False,
        )
    except TurnControlError as exc:
        return gap_receipt(
            root,
            host_payload=payload,
            error=exc,
            policy=raw_policy,
        )
    policy = policy_state(
        root,
        host_session_id=str(normalized_payload.get("session_id") or "").strip(),
        cwd=str(normalized_payload.get("cwd") or ""),
        transcript_path=str(
            normalized_payload.get("transcript_path")
            or normalized_payload.get("agent_transcript_path")
            or ""
        ),
    )
    if not policy.get("governed_session"):
        return {
            "state": "NOT_INDEXED",
            "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
            "private_reasoning_stored": False,
        }
    if not policy.get("strict_required"):
        return {
            "state": "TURN_CONTROL_NOT_REQUIRED_YET",
            "reason": "SEALED_MODE_PLUS_PLAN_NOT_ACTIVE",
            "project_id": policy.get("project_id"),
            "evidence_session_id": policy.get("evidence_session_id"),
            "private_reasoning_stored": False,
        }
    try:
        receipt = commit_turn(root, host_payload=normalized_payload)
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized_payload,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt = _record(payload)
    except Exception as exc:  # noqa: BLE001 - Stop must expose, not hide, a gap
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "TURN_CONTROL_MODULE_OR_POLICY_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "fail_closed": True,
            "automatic_commit_inferred": False,
            "private_reasoning_stored": False,
        }
    result: dict[str, Any] = {"continue": True}
    if receipt.get("state") not in {"NOT_INDEXED", "TURN_CONTROL_NOT_REQUIRED_YET"}:
        display = receipt.get("persistent_change_display")
        if isinstance(display, dict):
            (
                _,
                _,
                _,
                _,
                persistent_change_system_message,
                persistent_change_system_notice,
                _,
            ) = _load_control()
            notice = persistent_change_system_notice(
                display,
                phase="TURN_COMMIT",
                turn_receipt=receipt,
            )
            serialized = json.dumps(
                notice, sort_keys=True, separators=(",", ":")
            )
            result["systemMessage"] = persistent_change_system_message(notice)
            result["hookSpecificOutput"] = {
                "hookEventName": "Stop",
                "additionalContext": (
                    "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=" + serialized
                ),
            }
        else:
            result["systemMessage"] = "EVIDENCE_LANE_RESPONSE_COMMIT=" + json.dumps(
                receipt, sort_keys=True, separators=(",", ":")
            )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
