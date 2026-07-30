"""Portable next-action contracts for host-owned prompt surfaces."""

from __future__ import annotations

from typing import Any

HIL_CHOICES = (
    "APPROVE",
    "APPROVE_WITH_DELTA",
    "MORE_RESEARCH",
    "ROLLBACK",
    "REJECT",
    "FAIL",
)

HIL_SUGGESTED_PROMPT = (
    "/evi-80-hil <APPROVE | APPROVE_WITH_DELTA: correction | "
    "MORE_RESEARCH: question | ROLLBACK[: PVn] | REJECT: reason | FAIL: gate>"
)


def hil_next_action(
    *,
    project_id: str,
    session_id: str,
    candidate_id: str,
    proposed_pv: str,
) -> dict[str, Any]:
    """Return a neutral HIL suggestion without choosing for the human."""

    return {
        "schema": "evidence-lane.next-action.v1",
        "state": "PRESENT_SIX_WAY_HIL",
        "display_position": "BEFORE_HIL_DECISION",
        "command": "/evi-80-hil",
        "suggested_next_prompt": HIL_SUGGESTED_PROMPT,
        "choices": list(HIL_CHOICES),
        "project_id": project_id,
        "session_id": session_id,
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "composer_authority": "HOST_OWNED",
        "documented_mcp_composer_mutation_supported": False,
        "auto_submit": False,
        "stop_and_wait": True,
    }


def state_travel_next_action(
    *,
    state: str,
    command: str,
    suggested_next_prompt: str,
    target_surface: str,
) -> dict[str, Any]:
    """Return the host-neutral prompt contract for State Travel."""

    return {
        "schema": "evidence-lane.next-action.v1",
        "state": state,
        "display_position": "FINAL_VISIBLE_ACTION",
        "command": command,
        "suggested_next_prompt": suggested_next_prompt,
        "target_surface": target_surface,
        "composer_authority": "HOST_OWNED",
        "documented_mcp_composer_mutation_supported": False,
        "auto_submit": False,
        "stop_and_wait": True,
    }
