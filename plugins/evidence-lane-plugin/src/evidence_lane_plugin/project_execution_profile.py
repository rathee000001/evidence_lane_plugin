"""Pair Project Recipe and Mode into one ENV/UOP execution profile."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import CANONICAL_LANE_IDS
from .next_actions import PROJECT_HIL_DECISION_TOKENS

PROJECT_EXECUTION_PROFILE_SCHEMA = "evidence-lane.project-execution-profile.v1"
ATOMIC_PV_REFRESH_SCHEMA = "evidence-lane.atomic-pv-member-refresh.v1"
_SHA256 = re.compile(r"[A-F0-9]{64}")


def _hashes(values: Mapping[str, str], *, label: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw_path, raw_sha in values.items():
        path = str(raw_path).replace("\\", "/").strip("/")
        sha = str(raw_sha).strip().upper()
        if not path or ".." in path.split("/"):
            raise ValueError(f"{label} contains an unsafe project-relative path.")
        if _SHA256.fullmatch(sha) is None:
            raise ValueError(f"{label} contains an invalid SHA-256 for {path}.")
        rows[path] = sha
    return dict(sorted(rows.items()))


def compile_atomic_pv_refresh(
    *,
    project_id: str,
    current_members: Mapping[str, str],
    desired_members: Mapping[str, str],
) -> dict[str, Any]:
    """Compile an atomic generation refresh without writing project files."""

    exact_project = project_id.strip()
    if not exact_project:
        raise ValueError("PV refresh requires a project identity.")
    current = _hashes(current_members, label="current_members")
    desired = _hashes(desired_members, label="desired_members")
    reused = [path for path in desired if current.get(path) == desired[path]]
    changed = [
        path for path in desired if path in current and current[path] != desired[path]
    ]
    added = [path for path in desired if path not in current]
    removed = [path for path in current if path not in desired]
    body = {
        "schema": ATOMIC_PV_REFRESH_SCHEMA,
        "status": "PASS",
        "project_id": exact_project,
        "current_members": current,
        "desired_members": desired,
        "reused_members": reused,
        "changed_members": changed,
        "added_members": added,
        "removed_members": removed,
        "reused_count": len(reused),
        "changed_count": len(changed),
        "added_count": len(added),
        "removed_count": len(removed),
        "staging_generation_required": bool(changed or added or removed),
        "pointer_swap_after_full_validation_only": True,
        "partial_live_tree_write_allowed": False,
        "unchanged_content_addressed_atoms_reused": True,
        "write_performed": False,
    }
    return {**body, "plan_sha256": sha256_bytes(canonical_json_bytes(body))}


def compile_project_execution_profile(
    *,
    project_recipe: Mapping[str, Any],
    mode_classification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Synchronize distinct Recipe and Mode outputs under ENV/UOP policy."""

    project_id = str(project_recipe.get("project_id") or "").strip()
    project_type = str(project_recipe.get("project_type") or "").strip()
    recipe_lanes = [str(value) for value in project_recipe.get("canonical_lanes") or []]
    if not project_id or not project_type or not recipe_lanes:
        raise ValueError("Project execution profile requires a complete project recipe.")
    mode_lanes = [
        str(value)
        for value in (mode_classification or {}).get("canonical_lanes", [])
    ]
    active_lanes = list(dict.fromkeys([*recipe_lanes, *mode_lanes]))
    if not set(active_lanes) <= set(CANONICAL_LANE_IDS):
        raise ValueError("Project execution profile contains a non-canonical lane.")
    mode_governance = dict((mode_classification or {}).get("mode_governance") or {})
    contracts = [dict(row) for row in mode_governance.get("contracts") or []]
    operator_rows = [
        dict(operator)
        for contract in contracts
        for operator in contract.get("operators") or []
    ]
    operator_groups = list(
        dict.fromkeys(
            str(group)
            for contract in contracts
            for group in contract.get("operator_groups") or []
        )
    )
    operator_text = " ".join(
        str(value).casefold()
        for row in operator_rows
        for value in (row.get("chapter"), row.get("effect"), row.get("declared_effect"))
    )
    ci_contracts = [dict(contract.get("ci_cd") or {}) for contract in contracts]
    ci_required = any(bool(row.get("required")) for row in ci_contracts)
    lane_toolchains = [
        dict(binding)
        for contract in contracts
        for binding in dict(contract.get("conditional_toolchain") or {}).get(
            "lane_bindings", []
        )
    ]
    hil_presentations = [dict(contract.get("hil") or {}) for contract in contracts]
    for hil in hil_presentations:
        tokens = [str(row["token"]) for row in hil.get("choices") or []]
        if tokens != list(PROJECT_HIL_DECISION_TOKENS):
            raise ValueError(
                "Mode-specific HIL presentation changed Project-authority policy."
            )
    behavior = {
        "ci_loop_required": ci_required,
        "analysis_operator_required": "analysis" in active_lanes,
        "probability_operator_required": "probability" in operator_text,
        "pcm_operator_group_required": "PCM" in operator_groups,
        "mba_operator_group_required": "MBA" in operator_groups,
        "project_type": project_type,
        "selected_only_from_user_intent_recipe_mode_and_env_uop": True,
    }
    core = {
        "schema": PROJECT_EXECUTION_PROFILE_SCHEMA,
        "status": "PASS",
        "project_id": project_id,
        "project_type": project_type,
        "recipe_sha256": str(project_recipe.get("receipt_sha256") or ""),
        "mode_selection_sha256": str(
            mode_governance.get("combined_operator_receipt_sha256") or ""
        ),
        "recipe_lanes": recipe_lanes,
        "mode_lanes": mode_lanes,
        "active_lanes": active_lanes,
        "mode_added_lanes": [lane for lane in mode_lanes if lane not in recipe_lanes],
        "operator_rows": operator_rows,
        "operator_groups": operator_groups,
        "ci_contracts": ci_contracts,
        "conditional_lane_toolchains": lane_toolchains,
        "hil_presentations": hil_presentations,
        "behavior": behavior,
        "project_recipe_and_mode_are_distinct": True,
        "project_recipe_and_mode_are_synchronized": True,
        "env_selects_environment_context": True,
        "uop_applies_governance_operators_and_gates": True,
        "project_authority_hil_tokens": list(PROJECT_HIL_DECISION_TOKENS),
        "decision_count_is_behavior_ceiling": False,
        "mode_or_recipe_selection_is_hil_approval": False,
        "candidate_created": False,
        "pointer_moved": False,
        "workflow_or_count_ceiling": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "ATOMIC_PV_REFRESH_SCHEMA",
    "PROJECT_EXECUTION_PROFILE_SCHEMA",
    "compile_atomic_pv_refresh",
    "compile_project_execution_profile",
]
