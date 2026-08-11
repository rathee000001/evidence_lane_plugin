"""Authoritative visible-response COMMIT adapter for governed Codex turns."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _store_root() -> Path:
    return Path(
        os.environ.get("EVIDENCE_LANE_DATA_ROOT")
        or os.environ.get("PLUGIN_DATA")
        or Path.home() / "EvidenceLanePV"
    ).resolve()


def _load_control():
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        TurnControlError,
        commit_turn,
        gap_receipt,
        persistent_change_system_notice,
        policy_state,
    )

    return (
        TurnControlError,
        commit_turn,
        gap_receipt,
        persistent_change_system_notice,
        policy_state,
    )


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    root = _store_root()
    TurnControlError, commit_turn, gap_receipt, _, policy_state = _load_control()
    policy = policy_state(
        root,
        host_session_id=str(payload.get("session_id") or "").strip(),
        cwd=str(payload.get("cwd") or ""),
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
        return commit_turn(root, host_payload=payload)
    except TurnControlError as exc:
        return gap_receipt(
            root,
            host_payload=payload,
            error=exc,
            policy=policy,
        )


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
            _, _, _, persistent_change_system_notice, _ = _load_control()
            notice = persistent_change_system_notice(
                display,
                phase="TURN_COMMIT",
                turn_receipt=receipt,
            )
            result["systemMessage"] = (
                "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY="
                + json.dumps(notice, sort_keys=True, separators=(",", ":"))
            )
        else:
            result["systemMessage"] = "EVIDENCE_LANE_RESPONSE_COMMIT=" + json.dumps(
                receipt, sort_keys=True, separators=(",", ":")
            )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
