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

_DIRECT_COMMAND_ROUTES = (
    {
        "command": "/evi-boot",
        "skill": "evi-boot",
        "inferred_phrases": (
            "boot evidence lane",
            "start evidence lane",
            "resume evidence lane session",
        ),
    },
    {
        "command": "/evi-rollback",
        "skill": "evi-rollback",
        "inferred_phrases": (
            "rollback accepted pv",
            "roll back accepted pv",
            "move the accepted pointer back",
        ),
    },
    {
        "command": "/evi-build",
        "skill": "evi-build",
        "inferred_phrases": (
            "build an evidence lane candidate",
            "build a pv candidate",
            "create an unaccepted pv candidate",
        ),
    },
    {
        "command": "/evi-refresh",
        "skill": "evi-refresh",
        "inferred_phrases": (
            "refresh governed evidence",
            "refresh the evidence candidate",
            "rebuild changed evidence",
            "run incremental evidence refresh",
        ),
    },
    {
        "command": "/evi-mode",
        "skill": "evi-mode",
        "inferred_phrases": (
            "change evidence lane mode",
            "set evidence lane mode",
            "apply an evidence lane mode",
        ),
    },
    {
        "command": "/evi-source-intake",
        "skill": "evi-source-intake",
        "inferred_phrases": (
            "ingest this source into evidence lane",
            "add this source to evidence lane",
            "classify these evidence lane sources",
            "index this repository in evidence lane",
        ),
    },
)

BOOT_SUGGESTED_PROMPT = (
    "Use /evi-source-intake with one or more ordered sources; allow auto-detection "
    "or name exact per-source lane overrides. Chat Lineage is always included."
)


def direct_command_map() -> dict[str, Any]:
    """Return the one deterministic prompt-to-primary-control map."""

    return {
        "schema": "evidence-lane.direct-command-map.v1",
        "ordered_controls": list(PUBLIC_CONTROLS),
        "routes": [
            {
                "command": route["command"],
                "skill": route["skill"],
                "explicit_invocation": route["command"],
                "inferred_phrases": list(route["inferred_phrases"]),
            }
            for route in _DIRECT_COMMAND_ROUTES
        ],
        "explicit_and_inferred_share_skill": True,
        "route_selection_changes_authority": False,
        "selected_skill_must_run_native_gates": True,
        "host_plan_sync_is_evi_refresh": False,
    }


def resolve_direct_command_route(prompt: str) -> dict[str, Any]:
    """Resolve one explicit or conservative inferred primary-control route.

    This function selects a skill only. It executes no lifecycle action and
    grants no HIL, pointer, Git, installation, or deployment authority.
    """

    normalized = " ".join(str(prompt or "").strip().lower().split())
    first_token = normalized.split(" ", 1)[0] if normalized else ""
    explicit = [
        route for route in _DIRECT_COMMAND_ROUTES if first_token == route["command"]
    ]
    if explicit:
        route = explicit[0]
        return {
            "schema": "evidence-lane.direct-command-route.v1",
            "status": "ROUTED",
            "route_kind": "EXPLICIT_COMMAND",
            "command": route["command"],
            "skill": route["skill"],
            "matched_phrase": first_token,
            "lifecycle_action_executed": False,
            "selected_skill_must_run_native_gates": True,
        }

    # UI/Plan reactivation is a host-surface operation and must never be
    # inferred as the governed changed-evidence Refresh lifecycle.
    plan_surface_terms = (
        "plan panel",
        "step task list",
        "right side plan",
        "right-side plan",
        "refresh the plan",
        "rehydrate the plan",
    )
    if any(phrase in normalized for phrase in plan_surface_terms):
        return {
            "schema": "evidence-lane.direct-command-route.v1",
            "status": "HOST_PLAN_SURFACE_OPERATION",
            "route_kind": "NO_EVI_REFRESH_ALIAS",
            "command": None,
            "skill": "evidence-lane-code-lifecycle",
            "matched_phrase": next(
                phrase for phrase in plan_surface_terms if phrase in normalized
            ),
            "lifecycle_action_executed": False,
            "selected_skill_must_run_native_gates": True,
        }

    matches = [
        (route, phrase)
        for route in _DIRECT_COMMAND_ROUTES
        for phrase in route["inferred_phrases"]
        if phrase in normalized
    ]
    matched_commands = {str(route["command"]) for route, _ in matches}
    if len(matched_commands) != 1:
        return {
            "schema": "evidence-lane.direct-command-route.v1",
            "status": "NO_ROUTE" if not matches else "AMBIGUOUS_FAIL_CLOSED",
            "route_kind": "NONE" if not matches else "AMBIGUOUS",
            "command": None,
            "skill": None,
            "matched_phrase": None,
            "lifecycle_action_executed": False,
            "selected_skill_must_run_native_gates": True,
        }
    route, phrase = matches[0]
    return {
        "schema": "evidence-lane.direct-command-route.v1",
        "status": "ROUTED",
        "route_kind": "INFERRED_VISIBLE_INTENT",
        "command": route["command"],
        "skill": route["skill"],
        "matched_phrase": phrase,
        "lifecycle_action_executed": False,
        "selected_skill_must_run_native_gates": True,
    }


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
        "direct_command_map": direct_command_map(),
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
        route = "USER_MEDIATED_SOURCE"
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
    display_position: str = "FINAL_VISIBLE_ACTION",
    stop_and_wait: bool = True,
    task_panel_reactivation: dict[str, Any] | None = None,
    execution_writer_boundary: dict[str, Any] | None = None,
    goal_continuity: dict[str, Any] | None = None,
    destination_orchestration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the host-neutral prompt contract for State Travel."""

    contract: dict[str, Any] = {
        "schema": "evidence-lane.next-action.v1",
        "state": state,
        "display_position": display_position,
        "command": command,
        "suggested_next_prompt": suggested_next_prompt,
        "target_surface": target_surface,
        "composer_authority": "HOST_OWNED",
        "documented_mcp_composer_mutation_supported": False,
        "auto_submit": False,
        "stop_and_wait": stop_and_wait,
    }
    if task_panel_reactivation is not None:
        contract["task_panel_reactivation"] = task_panel_reactivation
    if execution_writer_boundary is not None:
        contract["execution_writer_boundary"] = execution_writer_boundary
    if goal_continuity is not None:
        contract["goal_continuity"] = goal_continuity
    if destination_orchestration is not None:
        contract["destination_orchestration"] = destination_orchestration
    return contract
