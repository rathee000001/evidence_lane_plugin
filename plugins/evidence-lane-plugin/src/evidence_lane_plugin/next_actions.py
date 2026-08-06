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
    "/evi-build <APPROVE | APPROVE_WITH_DELTA: correction | "
    "MORE_RESEARCH: question | ROLLBACK[: PVn] | REJECT: reason | FAIL: gate>"
)

PUBLIC_CONTROLS = (
    "/evi-boot",
    "/evi-rollback",
    "/evi-build",
    "/evi-refresh",
    "/evi-mode",
    "/evi-source-intake",
)
SOURCE_INTAKE_COMMANDS = ("/evi-source-intake",)

BOOT_SUGGESTED_PROMPT = (
    "Use /evi-source-intake with one or more ordered sources; allow auto-detection "
    "or name exact per-source lane overrides. Chat Lineage is always included."
)


def boot_next_action(*, entry_action: str) -> dict[str, Any]:
    """Return the ordered source-intake contract after atomic Boot/Flash."""

    return {
        "schema": "evidence-lane.next-action.v1",
        "state": "SOURCE_INTAKE_READY",
        "display_position": "AFTER_ATOMIC_BOOT_FLASH",
        "command": "/evi-source-intake",
        "suggested_next_prompt": BOOT_SUGGESTED_PROMPT,
        "ordered_source_intake_commands": list(SOURCE_INTAKE_COMMANDS),
        "public_controls": list(PUBLIC_CONTROLS),
        # Retain the compatibility key while making the no-auto-travel law
        # explicit for hosts that consumed the v0.6 contract.
        "state_travel_conditional_first": None,
        "state_travel_available_command": "/evi-state-travel",
        "state_travel_eligibility_is_not_invocation": True,
        "state_travel_allowed_triggers": [
            "EXPLICIT_USER_REQUEST",
            "GENUINE_HOST_CONTEXT_EXHAUSTION",
        ],
        "state_travel_auto_selected": False,
        "entry_action": entry_action,
        "mode_sidecar": "/evi-mode",
        "composer_authority": "HOST_OWNED",
        "documented_mcp_composer_mutation_supported": False,
        "auto_submit": False,
        "stop_and_wait": True,
    }


def hil_next_action(
    *,
    project_id: str,
    session_id: str,
    candidate_id: str,
    proposed_pv: str,
    mode_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a neutral HIL suggestion without choosing for the human."""

    result = {
        "schema": "evidence-lane.next-action.v1",
        "state": "PRESENT_SIX_WAY_HIL",
        "display_position": "BEFORE_HIL_DECISION",
        "command": "/evi-build",
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
    if mode_execution is not None:
        result["mode_execution"] = mode_execution
        result["visible_formula_response"] = list(
            mode_execution.get("visible_formula_response") or []
        )
        result["lane_hil_contracts"] = list(
            mode_execution.get("lane_hil_contracts") or []
        )
        result["hil_semantics"] = (
            "UNIVERSAL_EXACT_TOKENS_WITH_SELECTED_LANE_SPECIFIC_EFFECTS"
        )
    else:
        result["hil_semantics"] = "UNIVERSAL_EXACT_TOKENS_NO_MODE_SELECTED"
    return result


def refresh_output_handoff(
    *,
    host_kind: str,
    client_can_edit_source: bool,
    candidate_id: str,
    proposed_pv: str,
) -> dict[str, Any]:
    """Return a truthful host-specific output and source-update handoff."""

    host = host_kind.strip().lower()
    if host in {"codex_desktop", "codex_cli"} and client_can_edit_source:
        route = "CODEX_LOCAL_GIT_BACKED"
        confirmation = "HOST_SANDBOX_FINAL_STATE_CONFIRMED"
        delivery = "LOCAL_CANDIDATE_PACKAGE_AND_GIT_EVIDENCE"
    else:
        route = "CHATGPT_OR_USER_MEDIATED_SOURCE"
        confirmation = "USER_APPLIED_AND_PULL_CONFIRMED"
        delivery = "VISIBLE_OUTPUT_LINKS_PLUS_DURABLE_MCP_READBACK"
    return {
        "schema": "evidence-lane.host-output-handoff.v1",
        "host_kind": host_kind,
        "route": route,
        "source_update_confirmation": confirmation,
        "output_delivery": delivery,
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "candidate_is_unaccepted": True,
        "pointer_moved": False,
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
