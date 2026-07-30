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

SOURCE_INTAKE_COMMANDS = (
    "/evi-02-git",
    "/evi-03-local",
    "/evi-04-sqlite-pv-candidate-loader",
    "/evi-05-chat-lineage",
    "/evi-06-discussion",
    "/evi-07-analysis",
    "/evi-08-plan",
    "/evi-09-docs",
    "/evi-10-data-excel",
    "/evi-11-ppt",
    "/evi-12-pdf-ocr",
    "/evi-13-images-ocr",
    "/evi-14-artifacts",
    "/evi-15-custom",
    "/evi-16-research",
    "/evi-17-project-engulf",
    "/evi-18-sqlite-brain",
)

BOOT_SUGGESTED_PROMPT = (
    "Choose /evi-02-git with one exact repository and branch, /evi-03-local "
    "with one exact local Git path, or another displayed source-intake lane."
)


def boot_next_action(*, entry_action: str) -> dict[str, Any]:
    """Return the ordered source-intake contract after atomic Boot/Flash."""

    return {
        "schema": "evidence-lane.next-action.v1",
        "state": "SOURCE_INTAKE_READY",
        "display_position": "AFTER_ATOMIC_BOOT_FLASH",
        "command": "USER_SELECTS_ONE_OR_MORE_ORDERED_SOURCE_INTAKE_COMMANDS",
        "suggested_next_prompt": BOOT_SUGGESTED_PROMPT,
        "ordered_source_intake_commands": list(SOURCE_INTAKE_COMMANDS),
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
