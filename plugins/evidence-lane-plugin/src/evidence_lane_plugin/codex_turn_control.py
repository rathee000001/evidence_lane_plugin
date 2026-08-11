"""Deterministic Codex turn receipts over governed Evidence Lane sessions.

This module is deliberately host-specific.  It strengthens the documented Codex
hook path without pretending that tool hooks observe hosted tools or every
specialized host path.  The accepted pointer, Entry/Prepare receipts, active
ENV/UOP mode, and persistent Plan row remain the authority; task-window
scrollback and transcript files are never read here.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .constants import ENGINE_VERSION
from .errors import EvidenceLaneError
from .git_adapter import calculate_worktree_sha256, inspect_repository, run_git
from .hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lineage import ChatLineage
from .prompt_index import PromptIndex
from .redaction import contains_secret, redact_text
from .store import ProjectStore

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_TERM_RE = re.compile(r"[\w.$/@:-]+", flags=re.UNICODE)
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)|https?://[^\s)>]+")
_TURN_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:authorization|password|token|secret|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*[^\s,;]+"
)
_TURN_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_ACTIVE_PLAN_STATUSES = {"ACTIVE", "IN_PROGRESS"}
_MAX_VISIBLE_EVENT_CHARS = 12_000
_MAX_PERSISTENT_CHANGE_PATHS = 200
_TOKEN_METRIC_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
}
_GOAL_USAGE_SEMANTICS = {
    "GOAL_FINAL_COUNTER",
    "GOAL_CUMULATIVE_SNAPSHOT",
    "TURN_DELTA",
}
_PROJECT_TASK_PRIVATE_ANALYSIS = "PROJECT_TASK_PRIVATE_ANALYSIS"
_MEMORY_PLUS_LEARNING_RESEARCH_QUESTION = (
    "How can this governed project preserve exact task memory and measure learning "
    "continuity across its turns without cross-project disclosure, shared telemetry, "
    "or private-reasoning storage?"
)


class TurnControlError(RuntimeError):
    """One deterministic fail-closed Codex turn-control failure."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _turn_redact_text(value: str) -> str:
    safe = redact_text(value)
    safe = _TURN_SECRET_ASSIGNMENT_RE.sub("[REDACTED]", safe)
    return _TURN_BEARER_RE.sub("[REDACTED]", safe)


def _turn_redact(value: Any) -> Any:
    if isinstance(value, str):
        return _turn_redact_text(value)
    if isinstance(value, dict):
        return {str(key): _turn_redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_turn_redact(item) for item in value]
    return value


def _require(condition: bool, code: str, message: str, **details: Any) -> None:
    if not condition:
        raise TurnControlError(code, message, **details)


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise TurnControlError(
            "TURN_CONTROL_AUTHORITY_UNREADABLE",
            "A required governed authority file could not be verified.",
            path=str(path),
            error_type=type(exc).__name__,
        ) from exc
    _require(
        isinstance(payload, dict),
        "TURN_CONTROL_AUTHORITY_INVALID",
        "A required governed authority is not a JSON object.",
        path=str(path),
    )
    return payload


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _runtime_activation(root: Path) -> dict[str, Any]:
    path = root / "installation" / "runtime_activation.json"
    if not path.is_file():
        return {"state": "DETACHED", "active_sessions": []}
    try:
        payload = _json(path)
    except TurnControlError:
        return {
            "state": "DETACHED",
            "active_sessions": [],
            "reason": "RUNTIME_ACTIVATION_RECEIPT_INVALID",
        }
    sessions = payload.get("active_sessions")
    if (
        payload.get("schema") != "evidence-lane.runtime-activation.v1"
        or payload.get("plugin_id") != "evidence-lane-plugin"
        or payload.get("state") != "ACTIVE"
        or not isinstance(sessions, list)
        or not sessions
        or payload.get("prompt_capture_active") is not True
        or payload.get("visible_response_capture_active") is not True
    ):
        return {"state": "DETACHED", "active_sessions": []}
    return payload


def _package_surface_inventory() -> dict[str, Any]:
    plugin_root = Path(__file__).resolve().parents[2]
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        *sorted((plugin_root / "hooks").glob("*.py")),
    ]
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    _require(
        all(path.is_file() for path in hook_paths),
        "TURN_CONTROL_PACKAGE_HOOK_INVENTORY_REQUIRED",
        "The installed persistent hook inventory is incomplete.",
    )

    def inventory(paths: list[Path], *, skill: bool) -> dict[str, Any]:
        records = [
            {
                "name": path.parent.name if skill else path.name,
                "sha256": sha256_file(path),
            }
            for path in paths
        ]
        _require(
            len(records) == len({row["name"] for row in records}),
            "TURN_CONTROL_PACKAGE_SURFACE_DUPLICATE",
            "An installed hook or skill name is duplicated.",
        )
        return {
            "count": len(records),
            "records": records,
            "inventory_sha256": sha256_bytes(canonical_json_bytes(records)),
        }

    manifest = _json(plugin_root / ".codex-plugin" / "plugin.json")
    release_path = plugin_root / "scripts" / "codex-release-channel.json"
    release = _json(release_path)
    stable = dict(release.get("stable") or {})
    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": str(manifest.get("version") or ""),
        "hooks": inventory(hook_paths, skill=False),
        "skills": inventory(skill_paths, skill=True),
        "catalog": {
            "tools": stable.get("native_tool_count"),
            "read": stable.get("native_read_tool_count"),
            "write": stable.get("native_write_tool_count"),
            "skills": stable.get("skill_count"),
        },
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return {
        **core,
        "release_policy_sha256": sha256_file(release_path),
        "tunnel_channel": stable.get("tunnel_channel"),
    }


def _package_update_status(root: Path) -> dict[str, Any]:
    current = _package_surface_inventory()
    receipt_path = (
        root
        / "installations"
        / "codex-v200"
        / "CURRENT_INSTALLATION.json"
    )
    installation: dict[str, Any] | None = None
    change: dict[str, Any] | None = None
    if receipt_path.is_file():
        candidate = _json(receipt_path)
        claimed = str(candidate.get("receipt_sha256") or "")
        actual = sha256_bytes(
            canonical_json_bytes(
                {
                    key: value
                    for key, value in candidate.items()
                    if key != "receipt_sha256"
                }
            )
        )
        if claimed == actual:
            candidate_change = dict(candidate.get("surface_change_display") or {})
            if (
                candidate.get("schema")
                == "evidence-lane.codex-stable-installation.v2"
                and candidate.get("status") == "PASS"
                and candidate_change.get("schema")
                == "evidence-lane.codex-installed-surface-change-display.v2"
                and candidate_change.get("current_surface_inventory_sha256")
                == current["surface_inventory_sha256"]
                and candidate_change.get("raw_paths_included") is False
                and candidate_change.get("private_research_question_included")
                is False
            ):
                installation = candidate
                change = candidate_change

    activation = _runtime_activation(root)
    if installation is not None and change is not None:
        state = "INSTALLED_SURFACE_RECEIPT_VERIFIED"
        installed_version = str((installation.get("plugin") or {}).get("version") or "")
        hook_change = dict(change.get("hooks") or {})
        skill_change = dict(change.get("skills") or {})
        catalog = dict(change.get("catalog") or {})
        install_receipt_sha256 = installation.get("receipt_sha256")
        activation_state = str(
            (installation.get("activation") or {}).get("state") or "UNSPECIFIED"
        )
    else:
        state = "PACKAGE_SURFACE_VISIBLE_INSTALL_RECEIPT_UNAVAILABLE_OR_STALE"
        installed_version = None
        hook_change = {
            "count": current["hooks"]["count"],
            "added": [],
            "changed": [],
            "removed": [],
            "inventory_sha256": current["hooks"]["inventory_sha256"],
            "change_classification": "INSTALL_RECEIPT_UNAVAILABLE",
        }
        skill_change = {
            "count": current["skills"]["count"],
            "added": [],
            "changed": [],
            "removed": [],
            "inventory_sha256": current["skills"]["inventory_sha256"],
            "change_classification": "INSTALL_RECEIPT_UNAVAILABLE",
        }
        catalog = dict(current["catalog"])
        catalog["changed_from_previous"] = None
        install_receipt_sha256 = None
        activation_state = "INSTALL_RECEIPT_UNAVAILABLE"

    manifest_version = str(current["plugin_version"])
    version_state = (
        "EXACT"
        if installed_version == manifest_version
        and manifest_version.split("+", 1)[0] == ENGINE_VERSION
        else "SOURCE_RUNTIME_EXACT_INSTALL_RECEIPT_UNAVAILABLE"
        if installed_version is None
        and manifest_version.split("+", 1)[0] == ENGINE_VERSION
        else "MISMATCH"
    )
    core = {
        "schema": "evidence-lane.codex-package-update-status.v2",
        "state": state,
        "source_plugin_version": manifest_version,
        "installed_plugin_version": installed_version,
        "runtime_engine_version": ENGINE_VERSION,
        "version_state": version_state,
        "hooks": hook_change,
        "skills": skill_change,
        "catalog": catalog,
        "surface_inventory_sha256": current["surface_inventory_sha256"],
        "release_policy_sha256": current["release_policy_sha256"],
        "install_receipt_sha256": install_receipt_sha256,
        "installation_activation_state": activation_state,
        "runtime_activation_state": activation.get("state"),
        "tunnel_channel": current["tunnel_channel"],
        "refresh_state": "BOUND_LATER_TO_CURRENT_CANDIDATE_BOUNDARY",
        "raw_paths_included": False,
        "private_research_question_included": False,
        "private_reasoning_stored": False,
    }
    core["package_update_status_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def _session_candidates(
    root: Path,
    *,
    host_session_id: str,
    cwd: str,
) -> list[dict[str, Any]]:
    projects_root = root / "projects"
    if not projects_root.is_dir():
        return []
    exact: list[dict[str, Any]] = []
    cwd_matches: list[dict[str, Any]] = []
    current_cwd = Path(cwd).resolve() if cwd else None
    for project_root in sorted(projects_root.iterdir(), key=lambda item: item.name):
        if not project_root.is_dir():
            continue
        try:
            active = _json(project_root / "active_session.json")
            session = _json(project_root / "sessions" / f"{active['session_id']}.json")
            project = _json(project_root / "project.json")
        except (TurnControlError, KeyError):
            continue
        if session.get("metadata", {}).get("closed_at"):
            continue
        row = {
            "project_root": project_root,
            "project": project,
            "session": session,
            "binding_match": "UNRESOLVED",
        }
        if (
            session.get("metadata", {}).get("current_host_session_id")
            == host_session_id
        ):
            row["binding_match"] = "EXACT_HOST_SESSION"
            exact.append(row)
        elif current_cwd and _within(
            current_cwd, Path(str(project["repository_path"]))
        ):
            row["binding_match"] = "CWD_ONLY_STALE_OR_MISSING_HOST"
            cwd_matches.append(row)
    if exact:
        return exact
    return cwd_matches


def policy_state(
    store_root: str | Path,
    *,
    host_session_id: str,
    cwd: str,
) -> dict[str, Any]:
    """Return whether this host turn is governed and strict-control eligible."""

    root = Path(store_root).resolve()
    candidates = _session_candidates(
        root,
        host_session_id=host_session_id.strip(),
        cwd=cwd,
    )
    if not candidates:
        return {
            "governed_session": False,
            "strict_required": False,
            "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
        }
    if len(candidates) != 1:
        return {
            "governed_session": True,
            "strict_required": True,
            "reason": "AMBIGUOUS_GOVERNED_SESSION_BINDING",
        }
    candidate = candidates[0]
    session = candidate["session"]
    metadata = session.get("metadata") or {}
    active_mode = metadata.get("active_mode_binding")
    resume_contract = (metadata.get("state_travel") or {}).get("resume_contract") or {}
    task_list = resume_contract.get("task_list") or []
    active_rows = [
        row
        for row in task_list
        if isinstance(row, dict)
        and str(row.get("status") or "").upper() in _ACTIVE_PLAN_STATUSES
    ]
    required = metadata.get("turn_control_required") is True or (
        isinstance(active_mode, dict) and bool(active_mode) and bool(active_rows)
    )
    return {
        "governed_session": True,
        "strict_required": required,
        "binding_match": candidate.get("binding_match"),
        "reason": (
            "EXACT_GOVERNED_SESSION_BINDING"
            if candidate.get("binding_match") == "EXACT_HOST_SESSION"
            else "STALE_OR_MISSING_HOST_SESSION_NO_CWD_REBIND"
        ),
        "project_id": session.get("project_id"),
        "evidence_session_id": session.get("session_id"),
        "runtime_state": _runtime_activation(root).get("state"),
        "active_plan_row_count": len(active_rows),
    }


def _one_bound_session(
    root: Path,
    *,
    host_session_id: str,
    cwd: str,
) -> dict[str, Any]:
    candidates = _session_candidates(
        root,
        host_session_id=host_session_id,
        cwd=cwd,
    )
    _require(
        len(candidates) == 1,
        "TURN_CONTROL_SESSION_BINDING_REQUIRED",
        "The Codex host turn must bind exactly one governed Evidence Lane session.",
        matching_sessions=len(candidates),
    )
    candidate = candidates[0]
    _require(
        candidate.get("binding_match") == "EXACT_HOST_SESSION",
        "TURN_CONTROL_EXACT_HOST_BINDING_REQUIRED",
        "Repository location cannot substitute for the exact governed host-session identity.",
        binding_match=candidate.get("binding_match"),
        supplied_host_session_id=host_session_id,
    )
    activation = _runtime_activation(root)
    session = candidate["session"]
    attached = any(
        isinstance(row, dict)
        and row.get("project_id") == session.get("project_id")
        and row.get("session_id") == session.get("session_id")
        for row in activation.get("active_sessions", [])
    )
    _require(
        activation.get("state") == "ACTIVE" and attached,
        "TURN_CONTROL_RUNTIME_ATTACHMENT_REQUIRED",
        "The governed session is not attached to the active Evidence Lane runtime.",
        runtime_state=activation.get("state"),
    )
    return candidate


def _sha(value: Any, *, field: str) -> str:
    exact = str(value or "").upper()
    _require(
        bool(_SHA256_RE.fullmatch(exact)),
        "TURN_CONTROL_RECEIPT_SHA256_REQUIRED",
        "A required Entry, Prepare, mode, operator, or Plan receipt is missing.",
        field=field,
    )
    return exact


def _binding_snapshot(
    root: Path,
    bound: dict[str, Any],
) -> dict[str, Any]:
    project_root = Path(bound["project_root"])
    project = bound["project"]
    session = bound["session"]
    metadata = session.get("metadata") or {}
    pointer = _json(project_root / "active_pointer.json")
    project_id = str(session.get("project_id") or "")
    evidence_session_id = str(session.get("session_id") or "")
    accepted_pv = str(pointer.get("accepted_pv") or "")
    pointer_generation = int(pointer.get("generation") or 0)
    _require(
        project_id
        and evidence_session_id
        and project_id == project.get("project_id") == pointer.get("project_id"),
        "TURN_CONTROL_PROJECT_SESSION_POINTER_MISMATCH",
        "Project, session, and accepted-pointer identities do not agree.",
    )
    _require(
        accepted_pv
        and session.get("accepted_pv") == accepted_pv
        and int(session.get("accepted_pointer_generation") or 0) == pointer_generation
        and metadata.get("entry_pv") == accepted_pv,
        "TURN_CONTROL_ENTRY_POINTER_MISMATCH",
        "The current accepted pointer does not match the governed Entry boundary.",
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
    )
    manifest_path = project_root / "accepted" / accepted_pv / "manifest.json"
    _require(
        manifest_path.is_file(),
        "TURN_CONTROL_ACCEPTED_MANIFEST_REQUIRED",
        "The accepted Entry manifest is missing.",
        path=str(manifest_path),
    )
    entry_manifest_sha256 = _sha(
        metadata.get("entry_manifest_sha256"), field="entry_manifest_sha256"
    )
    _require(
        sha256_file(manifest_path) == entry_manifest_sha256
        and pointer.get("accepted_manifest_sha256") == entry_manifest_sha256,
        "TURN_CONTROL_ACCEPTED_MANIFEST_MISMATCH",
        "The accepted Entry manifest does not match its sealed SHA-256 identity.",
    )
    entry_package_sha256 = _sha(
        metadata.get("entry_package_sha256"), field="entry_package_sha256"
    )
    state_travel = metadata.get("state_travel") or {}
    prepare_sha256 = _sha(
        state_travel.get("verified_snapshot_sha256"),
        field="state_travel.verified_snapshot_sha256",
    )
    resume_contract = state_travel.get("resume_contract") or {}
    task_list = resume_contract.get("task_list") or []
    active_rows = [
        dict(row)
        for row in task_list
        if isinstance(row, dict)
        and str(row.get("status") or "").upper() in _ACTIVE_PLAN_STATUSES
    ]
    _require(
        len(active_rows) == 1,
        "TURN_CONTROL_PLAN_ROW_REQUIRED",
        "Exactly one persistent Plan row must be active before Codex execution.",
        active_plan_rows=len(active_rows),
    )
    sealed_plan_row = active_rows[0]
    state_travel_task_list_sha256 = _sha(
        resume_contract.get("task_list_sha256"),
        field="state_travel.resume_contract.task_list_sha256",
    )
    backlog_status = ProjectStore(root).backlog_status(project_id)
    goal_projection = backlog_status.get("goal_projection") or {}
    goal_rows = goal_projection.get("rows") or []
    current_plan_rows = [
        dict(row)
        for row in goal_rows
        if isinstance(row, dict)
        and row.get("task_id") == sealed_plan_row.get("task_id")
    ]
    _require(
        len(current_plan_rows) == 1,
        "TURN_CONTROL_CANONICAL_PLAN_ROW_MISMATCH",
        "The sealed resume row must resolve exactly once in the current append-only Plan Lane.",
        sealed_task_id=sealed_plan_row.get("task_id"),
        current_match_count=len(current_plan_rows),
    )
    plan_row = current_plan_rows[0]
    plan_projection_sha256 = _sha(
        goal_projection.get("projection_sha256"),
        field="goal_projection.projection_sha256",
    )
    plan_task_matches = [
        dict(row)
        for row in backlog_status.get("tasks") or []
        if isinstance(row, dict) and row.get("task_id") == plan_row.get("task_id")
    ]
    _require(
        len(plan_task_matches) == 1,
        "TURN_CONTROL_PLAN_TASK_CONTRACT_REQUIRED",
        "The active Plan row must resolve exactly one bounded task contract.",
        task_id=plan_row.get("task_id"),
        task_contract_count=len(plan_task_matches),
    )
    plan_task_contract = plan_task_matches[0]
    runtime_task = session.get("task") or {}
    if isinstance(runtime_task, dict) and runtime_task.get("task_id"):
        task = dict(runtime_task)
        task_binding_source = "SESSION_RUNTIME_TASK_PLUS_CANONICAL_PLAN_ROW"
    else:
        task = dict(plan_task_contract)
        task_binding_source = "CANONICAL_ACTIVE_PLAN_ROW_AFTER_STATE_TRAVEL"
    _require(
        bool(task.get("task_id"))
        and plan_row.get("task_id") == plan_task_contract.get("task_id"),
        "TURN_CONTROL_TASK_BINDING_REQUIRED",
        "The governed session has no uniquely resolved active task contract.",
    )
    active_mode = metadata.get("active_mode_binding") or {}
    _require(
        isinstance(active_mode, dict)
        and active_mode.get("schema") == "evidence-lane.active-mode-binding.v1",
        "TURN_CONTROL_MODE_BINDING_REQUIRED",
        "An exact active ENV/UOP Mode binding is required.",
    )
    mode_governance = active_mode.get("mode_governance") or {}
    contracts = mode_governance.get("contracts") or []
    _require(
        isinstance(contracts, list) and bool(contracts),
        "TURN_CONTROL_ENV_UOP_CONTRACT_REQUIRED",
        "The active Mode binding has no ENV/UOP governance contract.",
    )
    env_authorities = []
    for index, contract in enumerate(contracts):
        authority = (contract or {}).get("env_authority") or {}
        env_authorities.append(
            {
                "mode_id": contract.get("mode_id"),
                "canonical_lanes": sorted(contract.get("canonical_lanes") or []),
                "env_sqlite_sha256": _sha(
                    authority.get("env_sqlite_sha256"),
                    field=f"mode.contracts[{index}].env_sqlite_sha256",
                ),
                "uop_sqlite_sha256": _sha(
                    authority.get("uop_sqlite_sha256"),
                    field=f"mode.contracts[{index}].uop_sqlite_sha256",
                ),
                "mode_policy_projection_sha256": _sha(
                    authority.get("mode_policy_projection_sha256"),
                    field=f"mode.contracts[{index}].mode_policy_projection_sha256",
                ),
                "operator_families": sorted(contract.get("operator_families") or []),
                "operator_receipt_sha256": _sha(
                    contract.get("operator_receipt_sha256"),
                    field=f"mode.contracts[{index}].operator_receipt_sha256",
                ),
            }
        )
    steer_delta_receipts = [
        {
            "delta_id": str(row.get("delta_id") or ""),
            "classification": str(row.get("classification") or ""),
            "boundary": str(row.get("boundary") or ""),
            "text_sha256": sha256_bytes(str(row.get("text") or "").encode("utf-8")),
        }
        for row in plan_row.get("steer_deltas") or []
        if isinstance(row, dict) and str(row.get("delta_id") or "").strip()
    ]
    research_basis_text = "\n".join(
        [str(plan_row.get("step") or "")]
        + [
            str(row.get("text") or "")
            for row in plan_row.get("steer_deltas") or []
            if isinstance(row, dict)
        ]
    )
    research_basis_lower = research_basis_text.casefold()
    research_focus = (
        "MEMORY_PLUS_LEARNING"
        if "memory" in research_basis_lower and "learning" in research_basis_lower
        else "PROJECT_TASK_RESEARCH"
    )
    research_policy = {
        "enabled": (
            "research" in set(active_mode.get("canonical_lanes") or [])
            or "RS" in set(active_mode.get("selected_mode_ids") or [])
        ),
        "focus": research_focus,
        "focus_basis_sha256": sha256_bytes(research_basis_text.encode("utf-8")),
        "access_scope": _PROJECT_TASK_PRIVATE_ANALYSIS,
        "cross_project_retrieval": False,
        "shared_telemetry": False,
        "public_output": False,
        "private_reasoning_stored": False,
    }
    binding = {
        "schema": "evidence-lane.codex-turn-binding.v1",
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "task_id": task["task_id"],
        "plan_task_id": plan_row.get("task_id"),
        "task_binding_source": task_binding_source,
        "runtime_task_present": bool(runtime_task),
        "lifecycle_state": session.get("state"),
        "accepted_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "candidate_boundary": (
            {
                "state": "PENDING_CANDIDATE",
                "candidate_id": session.get("candidate_id"),
            }
            if session.get("candidate_id")
            else {"state": "NO_PENDING_CANDIDATE", "candidate_id": None}
        ),
        "entry_manifest_sha256": entry_manifest_sha256,
        "entry_package_sha256": entry_package_sha256,
        "prepare_verified_snapshot_sha256": prepare_sha256,
        "persistent_plan_row": {
            "position": int(plan_row.get("number") or 0),
            "task_id": plan_row.get("task_id"),
            "status": sealed_plan_row.get("status"),
            "step_sha256": sha256_bytes(
                str(plan_row.get("step") or "").encode("utf-8")
            ),
            "state_travel_task_list_sha256": state_travel_task_list_sha256,
            "goal_projection_sha256": plan_projection_sha256,
            "goal_projection_task_count": int(goal_projection.get("task_count") or 0),
            "linked_steer_delta_receipts": steer_delta_receipts,
            "bounded_write_scope": list(
                plan_task_contract.get("permitted_paths") or []
            ),
            "permitted_tools": list(plan_task_contract.get("permitted_tools") or []),
            "acceptance_checks_sha256": sha256_bytes(
                canonical_json_bytes(plan_task_contract.get("acceptance_checks") or [])
            ),
            "stop_condition_sha256": sha256_bytes(
                str(plan_task_contract.get("stop_condition") or "").encode("utf-8")
            ),
        },
        "mode_binding_receipt_sha256": _sha(
            active_mode.get("binding_receipt_sha256"),
            field="active_mode_binding.binding_receipt_sha256",
        ),
        "combined_operator_receipt_sha256": _sha(
            mode_governance.get("combined_operator_receipt_sha256"),
            field="active_mode_binding.combined_operator_receipt_sha256",
        ),
        "canonical_lanes": sorted(active_mode.get("canonical_lanes") or []),
        "selected_mode_ids": sorted(active_mode.get("selected_mode_ids") or []),
        "env_uop_authorities": env_authorities,
        "research_policy": research_policy,
        "package_update_status": _package_update_status(root),
        "entry_slip": {
            "accepted_pv": accepted_pv,
            "pointer_generation": pointer_generation,
            "entry_manifest_sha256": entry_manifest_sha256,
            "entry_package_sha256": entry_package_sha256,
            "state_travel_prepare_sha256": prepare_sha256,
            "mode_binding_receipt_sha256": _sha(
                active_mode.get("binding_receipt_sha256"),
                field="active_mode_binding.binding_receipt_sha256",
            ),
            "goal_projection_sha256": plan_projection_sha256,
        },
        "gates": {
            "source_mutation_requires_prepare": True,
            "candidate_acceptance_inferred": False,
            "fuse_inferred": False,
            "pointer_movement_inferred": False,
            "hil_inferred": False,
        },
        "repository_path_sha256": sha256_bytes(
            str(project.get("repository_path") or "").encode("utf-8")
        ),
        "scrollback_authority": False,
        "transcript_authority": False,
    }
    binding["binding_sha256"] = sha256_bytes(canonical_json_bytes(binding))
    return binding


def _warm_attach_receipt(
    root: Path,
    *,
    bound: dict[str, Any],
    binding: dict[str, Any],
    host_session_id: str,
    started_ns: int,
) -> dict[str, Any]:
    """Prove a warm native Codex attachment without waiting on the tunnel."""

    activation_path = root / "installation" / "runtime_activation.json"
    activation = _runtime_activation(root)
    _require(
        activation_path.is_file() and activation.get("state") == "ACTIVE",
        "TURN_CONTROL_WARM_ATTACH_RUNTIME_REQUIRED",
        "Warm Codex attachment requires an existing durable active-runtime receipt.",
    )
    matching_rows = [
        dict(row)
        for row in activation.get("active_sessions") or []
        if isinstance(row, dict)
        and row.get("project_id") == binding["project_id"]
        and row.get("session_id") == binding["evidence_session_id"]
    ]
    _require(
        len(matching_rows) == 1,
        "TURN_CONTROL_WARM_ATTACH_SESSION_REQUIRED",
        "Warm Codex attachment requires exactly one active project-session row.",
        matching_rows=len(matching_rows),
    )
    activation_row = matching_rows[0]
    _require(
        activation.get("flash_context_attached") is True
        and activation.get("prompt_capture_active") is True
        and activation.get("visible_response_capture_active") is True,
        "TURN_CONTROL_WARM_ATTACH_CAPTURE_REQUIRED",
        "Warm Codex attachment requires Flash plus visible turn capture to already be active.",
    )
    host_session_ids = [
        str(value) for value in activation_row.get("host_session_ids") or []
    ]
    _require(
        host_session_id in host_session_ids,
        "TURN_CONTROL_WARM_ATTACH_HOST_REQUIRED",
        "The active plugin runtime does not bind the exact Codex host session.",
        host_session_id_sha256=sha256_bytes(host_session_id.encode("utf-8")),
    )
    flash_receipt_sha256 = _sha(
        activation_row.get("flash_receipt_sha256"),
        field="runtime_activation.active_sessions.flash_receipt_sha256",
    )
    execution_profile = dict(
        (bound.get("session", {}).get("metadata") or {}).get("execution_profile")
        or {}
    )
    required_profile_fields = (
        "model",
        "submodel",
        "reasoning_effort",
        "reasoning_speed",
    )
    missing_profile_fields = [
        field
        for field in required_profile_fields
        if not str(execution_profile.get(field) or "").strip()
    ]
    _require(
        not missing_profile_fields,
        "TURN_CONTROL_WARM_ATTACH_PROFILE_REQUIRED",
        "The exact unfinished Codex execution profile is incomplete.",
        missing=missing_profile_fields,
    )
    duration_ns = max(0, time.perf_counter_ns() - started_ns)
    persistence_route = dict(
        (bound.get("session", {}).get("metadata") or {}).get("persistence_route")
        or {}
    )
    tunnel_requirement = str(
        persistence_route.get("tunnel_requirement")
        or "HOST_CAPABILITY_UNSPECIFIED"
    )
    interactive_tunnel = (
        tunnel_requirement == "REQUIRED_FOR_INTERACTIVE_CODEX_APP_ENVIRONMENT"
    )
    core = {
        "schema": "evidence-lane.codex-warm-attach-receipt.v1",
        "state": "WARM_ATTACHED_ZERO_TUNNEL_PROVISIONING_WAIT",
        "route": "PACKAGE_LOCAL_NATIVE_MCP_ONLY",
        "native_mcp_namespace": "mcp__evidence_lane__",
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "task_id": binding["task_id"],
        "plan_task_id": binding["plan_task_id"],
        "host_session_id_sha256": sha256_bytes(host_session_id.encode("utf-8")),
        "binding_sha256": binding["binding_sha256"],
        "execution_profile": execution_profile,
        "execution_profile_sha256": sha256_bytes(
            canonical_json_bytes(execution_profile)
        ),
        "runtime_already_active": True,
        "exact_host_session_already_attached": True,
        "runtime_activation_generation": int(activation.get("generation") or 0),
        "runtime_activation_receipt_sha256": sha256_file(activation_path),
        "flash_context_already_attached": activation.get("flash_context_attached")
        is True,
        "prompt_capture_already_active": activation.get("prompt_capture_active")
        is True,
        "visible_response_capture_already_active": activation.get(
            "visible_response_capture_active"
        )
        is True,
        "flash_receipt_sha256": flash_receipt_sha256,
        "accepted_pv": binding["accepted_pv"],
        "pointer_generation": binding["pointer_generation"],
        "boot_flash_pointer_plan_verified": True,
        "interaction_profile": persistence_route.get(
            "interaction_profile", "HOST_SURFACE_UNSPECIFIED"
        ),
        "vm_lifetime": persistence_route.get(
            "vm_lifetime", "LOCAL_OR_PERSISTENT"
        ),
        "tunnel_role": (
            "INTERACTIVE_ENVIRONMENT_PRECONDITION_NOT_LIFECYCLE_AUTHORITY"
            if interactive_tunnel
            else "NOT_IN_CODEX_NATIVE_LIFECYCLE_CRITICAL_PATH"
        ),
        "tunnel_requirement": tunnel_requirement,
        "tunnel_setup_frequency": persistence_route.get(
            "tunnel_setup_frequency", "HOST_CAPABILITY_UNSPECIFIED"
        ),
        "tunnel_key_retention": persistence_route.get(
            "tunnel_key_retention", "HOST_CAPABILITY_UNSPECIFIED"
        ),
        "tunnel_action": "NONE_IN_WARM_ATTACH_USE_HOST_ACTIVATION_ENVELOPE",
        "tunnel_onboarding_may_be_required": interactive_tunnel,
        "tunnel_state_queried": False,
        "tunnel_provisioning_wait_ns": 0,
        "tunnel_health_check_wait_ns": 0,
        "codex_tunnel_lifecycle_proof_allowed": False,
        "external_windows_tunnel_health_claimed": False,
        "attach_verification_duration_ns": duration_ns,
        "attach_verification_duration_ms": round(duration_ns / 1_000_000, 3),
        "zero_wall_clock_duration_claimed": False,
        "lifecycle_mutated": False,
        "source_mutated": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    core["receipt_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def _source_change_snapshot(
    root: Path,
    *,
    binding: dict[str, Any],
    cwd: str,
) -> dict[str, Any]:
    """Read one deterministic Git change surface without mutating the repository."""

    config = ProjectStore(root).config(str(binding["project_id"]))
    repository = Path(config.repository_path).resolve()
    try:
        identity = inspect_repository(
            repository,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
        )
        worktree_sha256 = calculate_worktree_sha256(repository)
        status = run_git(
            repository,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        ).stdout
    except EvidenceLaneError as exc:
        raise TurnControlError(
            "TURN_CONTROL_SOURCE_CHANGE_STATUS_UNAVAILABLE",
            "The exact governed repository change status could not be verified.",
            repository_path_sha256=binding["repository_path_sha256"],
            source_error_code=exc.code,
            source_error_status=exc.status,
        ) from exc

    changed_paths: list[dict[str, Any]] = []
    for line in status.splitlines():
        if not line.strip():
            continue
        status_code = line[:2] if len(line) >= 2 else "??"
        raw_path = line[3:].strip() if len(line) > 3 else ""
        raw_path_sha256 = sha256_bytes(raw_path.encode("utf-8"))
        visible_path = _turn_redact_text(raw_path)
        if not visible_path or contains_secret(visible_path):
            visible_path = f"[REDACTED_PATH:{raw_path_sha256[:16]}]"
        changed_paths.append(
            {
                "status": status_code,
                "path_after_redaction": visible_path,
                "path_sha256": raw_path_sha256,
            }
        )

    try:
        exact_cwd = Path(cwd).resolve() if cwd.strip() else None
    except OSError:
        exact_cwd = None
    if exact_cwd == repository:
        workspace_binding = "EXACT_REPOSITORY_ROOT"
    elif exact_cwd is not None and _within(exact_cwd, repository):
        workspace_binding = "INSIDE_REPOSITORY"
    else:
        workspace_binding = "PROJECTLESS_OR_EXTERNAL_TASK_WORKSPACE"

    core = {
        "schema": "evidence-lane.codex-source-change-snapshot.v1",
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "task_id": binding["task_id"],
        "plan_task_id": binding["plan_task_id"],
        "repository_path_sha256": binding["repository_path_sha256"],
        "branch": identity.branch,
        "commit_sha": identity.commit_sha,
        "tree_sha": identity.tree_sha,
        "worktree_sha256": worktree_sha256,
        "worktree_status_sha256": sha256_bytes(status.encode("utf-8")),
        "is_clean": identity.is_clean,
        "changed_path_count": len(changed_paths),
        "untracked_path_count": sum(row["status"] == "??" for row in changed_paths),
        "changed_paths_after_redaction": changed_paths[:_MAX_PERSISTENT_CHANGE_PATHS],
        "changed_paths_truncated": (len(changed_paths) > _MAX_PERSISTENT_CHANGE_PATHS),
        "task_workspace_binding": workspace_binding,
        "host_native_change_indicator_expected": workspace_binding
        in {"EXACT_REPOSITORY_ROOT", "INSIDE_REPOSITORY"},
        "source_mutated": False,
        "private_reasoning_stored": False,
    }
    core["source_change_snapshot_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def _persistent_change_display(
    *,
    binding: dict[str, Any],
    source_snapshot: dict[str, Any],
    turn_state: str,
    prompt_index: int | None,
    uncommitted_count: int,
    changed_since_prepare: bool | None,
    warm_attach_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pair the durable Plan row and read-only source change status for the host."""

    plan_row = dict(binding["persistent_plan_row"])
    changes = [
        {
            "delta_id": row["delta_id"],
            "classification": row["classification"],
            "boundary": row["boundary"],
            "text_sha256": row["text_sha256"],
        }
        for row in plan_row.get("linked_steer_delta_receipts") or []
    ]
    source_status = {
        key: source_snapshot[key]
        for key in (
            "source_change_snapshot_sha256",
            "branch",
            "commit_sha",
            "tree_sha",
            "worktree_sha256",
            "worktree_status_sha256",
            "is_clean",
            "changed_path_count",
            "untracked_path_count",
            "changed_paths_after_redaction",
            "changed_paths_truncated",
            "task_workspace_binding",
            "host_native_change_indicator_expected",
        )
    }
    display_state = (
        "PERSISTENT_CHANGES_PRESENT"
        if changes or not source_snapshot["is_clean"] or uncommitted_count
        else "NO_PERSISTENT_CHANGES"
    )
    package_change_status = dict(binding["package_update_status"])
    source_package_status_sha256 = package_change_status.pop(
        "package_update_status_sha256"
    )
    package_change_status.update(
        {
            "refresh_state": dict(binding["candidate_boundary"])["state"],
            "source_package_update_status_sha256": source_package_status_sha256,
        }
    )
    package_change_status["package_change_status_sha256"] = sha256_bytes(
        canonical_json_bytes(package_change_status)
    )
    core = {
        "schema": "evidence-lane.codex-persistent-change-display.v1",
        "state": display_state,
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "task_id": binding["task_id"],
        "plan_task_id": binding["plan_task_id"],
        "paired_step_task_list": {
            "task_count": plan_row["goal_projection_task_count"],
            "active_position": plan_row["position"],
            "active_task_id": plan_row["task_id"],
            "active_status": plan_row["status"],
            "state_travel_task_list_sha256": plan_row["state_travel_task_list_sha256"],
            "goal_projection_sha256": plan_row["goal_projection_sha256"],
        },
        "additive_change_summary": {
            "count": len(changes),
            "changes": changes,
            "raw_change_text_included": False,
        },
        "source_change_status": source_status,
        "turn_status": {
            "state": turn_state,
            "prompt_index": prompt_index,
            "uncommitted_count": uncommitted_count,
            "changed_since_prepare": changed_since_prepare,
        },
        "package_change_status": package_change_status,
        "rehydration_source": "SEALED_PROJECT_SESSION_TASK_TURN_CONTROL",
        "scrollback_authority": False,
        "transcript_authority": False,
        "access_scope": "EXACT_PROJECT_SESSION_TASK",
        "private_research_question_included": False,
        "private_reasoning_stored": False,
        "composer_mutated": False,
        "auto_submit": False,
        "lifecycle_mutated": False,
        "host_rendering_authority": "CODEX_HOST_OWNED",
    }
    if warm_attach_receipt is not None:
        core["warm_attach"] = warm_attach_receipt
    core["paired_projection_sha256"] = sha256_bytes(
        canonical_json_bytes(
            {
                "paired_step_task_list": core["paired_step_task_list"],
                "source_change_snapshot_sha256": source_status[
                    "source_change_snapshot_sha256"
                ],
                "additive_change_summary": core["additive_change_summary"],
            }
        )
    )
    core["display_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def persistent_change_system_notice(
    display: dict[str, Any],
    *,
    phase: str,
    turn_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one bounded host-visible notice from the sealed full display.

    Codex owns the exact UI placement of ``systemMessage``.  The plugin therefore
    requests the near-composer warning surface without claiming that it can edit
    or persist the composer itself.  Full source paths and raw Delta text stay in
    the durable receipt; this notice carries only their seals and bounded counts.
    """

    phase_value = str(phase or "").strip().upper()
    _require(
        phase_value in {"SESSION_START", "TURN_PREPARE", "TURN_COMMIT"},
        "TURN_CONTROL_CHANGE_NOTICE_PHASE_INVALID",
        "The persistent change notice phase is not supported.",
        phase=phase_value or None,
    )
    _require(
        display.get("schema") == "evidence-lane.codex-persistent-change-display.v1"
        and bool(display.get("display_sha256")),
        "TURN_CONTROL_CHANGE_DISPLAY_INVALID",
        "A sealed persistent change display is required for the host notice.",
    )
    paired = dict(display.get("paired_step_task_list") or {})
    additive = dict(display.get("additive_change_summary") or {})
    changes = [
        dict(row)
        for row in additive.get("changes") or []
        if isinstance(row, dict)
    ]
    source = dict(display.get("source_change_status") or {})
    tail = changes[-12:]
    receipt = dict(turn_receipt or {})
    core = {
        "schema": "evidence-lane.codex-persistent-change-system-notice.v2",
        "state": display.get("state"),
        "phase": phase_value,
        "requested_ui_surface": "CODEX_SYSTEM_MESSAGE_WARNING_NEAR_COMPOSER",
        "exact_above_prompt_bar_placement_claimed": False,
        "host_rendering_authority": "CODEX_HOST_OWNED",
        "composer_mutated": False,
        "paired_step_task_list": {
            "task_count": paired.get("task_count"),
            "active_position": paired.get("active_position"),
            "active_task_id": paired.get("active_task_id"),
            "active_status": paired.get("active_status"),
            "state_travel_task_list_sha256": paired.get(
                "state_travel_task_list_sha256"
            ),
            "goal_projection_sha256": paired.get("goal_projection_sha256"),
        },
        "linked_delta_status": {
            "count": len(changes),
            "active_delta": tail[-1] if tail else None,
            "delta_ids_tail": [row.get("delta_id") for row in tail],
            "tail_limit": 12,
            "truncated": len(changes) > len(tail),
            "delta_set_sha256": sha256_bytes(canonical_json_bytes(changes)),
            "raw_change_text_included": False,
        },
        "source_change_status": {
            "branch": source.get("branch"),
            "commit_sha": source.get("commit_sha"),
            "tree_sha": source.get("tree_sha"),
            "worktree_sha256": source.get("worktree_sha256"),
            "is_clean": source.get("is_clean"),
            "changed_path_count": source.get("changed_path_count"),
            "untracked_path_count": source.get("untracked_path_count"),
            "changed_paths_in_notice": False,
            "source_change_snapshot_sha256": source.get(
                "source_change_snapshot_sha256"
            ),
        },
        "turn_status": dict(display.get("turn_status") or {}),
        "package_change_status": dict(display.get("package_change_status") or {}),
        "turn_receipt": {
            key: receipt.get(key)
            for key in (
                "state",
                "prepare_state",
                "turn_id",
                "prompt_index",
                "control_record_sha256",
                "commit_sha256",
                "state_sha256",
            )
            if receipt.get(key) is not None
        },
        "warm_attach_receipt_sha256": (
            dict(display.get("warm_attach") or {}).get("receipt_sha256")
        ),
        "full_display_sha256": display["display_sha256"],
        "private_research_question_included": False,
        "private_reasoning_stored": False,
    }
    core["notice_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def _source_change_receipt(
    *,
    entry_snapshot: dict[str, Any],
    exit_snapshot: dict[str, Any],
) -> dict[str, Any]:
    core = {
        "schema": "evidence-lane.codex-turn-source-change.v1",
        "entry_source_change_snapshot_sha256": entry_snapshot[
            "source_change_snapshot_sha256"
        ],
        "exit_source_change_snapshot": exit_snapshot,
        "changed_since_prepare": (
            entry_snapshot["worktree_sha256"] != exit_snapshot["worktree_sha256"]
        ),
        "status_changed_since_prepare": (
            entry_snapshot["worktree_status_sha256"]
            != exit_snapshot["worktree_status_sha256"]
        ),
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "source_mutated_by_projection": False,
    }
    core["source_change_receipt_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def _fts_query(text: str) -> str | None:
    terms: list[str] = []
    for term in _TERM_RE.findall(text):
        normalized = term.strip().lower()
        if len(normalized) < 3 or normalized in terms:
            continue
        terms.append(normalized)
        if len(terms) == 8:
            break
    if not terms:
        return None
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _read_only(path: Path) -> sqlite3.Connection:
    _require(
        path.is_file(),
        "TURN_CONTROL_RETRIEVAL_AUTHORITY_REQUIRED",
        "A required governed SQLite retrieval authority is missing.",
        path=str(path),
    )
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _retrieval_receipt(
    *,
    project_root: Path,
    binding: dict[str, Any],
    visible_text: str,
) -> dict[str, Any]:
    query = _fts_query(visible_text)
    lineage_results: list[dict[str, Any]] = []
    code_results: list[dict[str, Any]] = []
    if query:
        lineage_path = project_root / "lineage" / "chat_lineage.sqlite"
        with _read_only(lineage_path) as connection:
            rows = connection.execute(
                """
                SELECT project_lineage_event.event_id AS event_id,
                       project_lineage_event.event_type AS event_type,
                       project_lineage_event.session_id AS session_id,
                       project_lineage_event.global_index AS global_index,
                       project_lineage_event.visible_payload_sha256 AS visible_payload_sha256,
                       project_lineage_event.event_sha256 AS event_sha256,
                       bm25(project_lineage_fts) AS rank
                FROM project_lineage_fts
                JOIN project_lineage_event USING(event_id)
                WHERE project_lineage_fts MATCH ?
                ORDER BY rank, global_index DESC LIMIT 6
                """,
                (query,),
            ).fetchall()
            lineage_results = [
                {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "session_id": row["session_id"],
                    "global_index": int(row["global_index"]),
                    "visible_payload_sha256": row["visible_payload_sha256"],
                    "event_sha256": row["event_sha256"],
                    "rank": float(row["rank"]),
                }
                for row in rows
            ]
        code_path = (
            project_root / "accepted" / str(binding["accepted_pv"]) / "code.sqlite"
        )
        with _read_only(code_path) as connection:
            rows = connection.execute(
                """
                SELECT chunks_fts.path AS path,
                       chunks_fts.chunk_id AS chunk_id,
                       c.start_line AS start_line,
                       c.end_line AS end_line,
                       c.sha256 AS chunk_sha256,
                       f.sha256 AS file_sha256,
                       bm25(chunks_fts) AS rank
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id=CAST(chunks_fts.chunk_id AS INTEGER)
                JOIN files f ON f.file_id=c.file_id
                WHERE chunks_fts MATCH ?
                ORDER BY rank, path, start_line LIMIT 6
                """,
                (query,),
            ).fetchall()
            code_results = [
                {
                    "ref_id": f"chunk:{row['chunk_id']}",
                    "path": row["path"],
                    "start_line": int(row["start_line"]),
                    "end_line": int(row["end_line"]),
                    "chunk_sha256": row["chunk_sha256"],
                    "file_sha256": row["file_sha256"],
                    "rank": float(row["rank"]),
                }
                for row in rows
            ]
    receipt = {
        "schema": "evidence-lane.codex-governed-retrieval.v1",
        "query_sha256": sha256_bytes(visible_text.encode("utf-8")),
        "query_terms_present": query is not None,
        "accepted_pv": binding["accepted_pv"],
        "pointer_generation": binding["pointer_generation"],
        "entry_manifest_sha256": binding["entry_manifest_sha256"],
        "lineage_results": lineage_results,
        "accepted_code_results": code_results,
        "lineage_result_count": len(lineage_results),
        "accepted_code_result_count": len(code_results),
        "bounded_result_limit_per_authority": 6,
        "scrollback_used": False,
        "transcript_used": False,
        "no_hit_is_valid": not lineage_results and not code_results,
    }
    receipt["retrieval_receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt


def _connection(project_root: Path) -> sqlite3.Connection:
    path = project_root / "lineage" / "codex_turn_control.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS turn_entry(
            control_record_sha256 TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            evidence_session_id TEXT NOT NULL,
            host_session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            input_kind TEXT NOT NULL CHECK(input_kind IN ('user_prompt','steer','goal')),
            prompt_index INTEGER NOT NULL CHECK(prompt_index > 0),
            prompt_record_sha256 TEXT NOT NULL UNIQUE,
            prior_control_record_sha256 TEXT REFERENCES turn_entry(control_record_sha256),
            binding_sha256 TEXT NOT NULL,
            retrieval_receipt_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(project_id, evidence_session_id, prompt_index)
        ) STRICT;
        CREATE VIRTUAL TABLE IF NOT EXISTS turn_entry_fts USING fts5(
            control_record_sha256 UNINDEXED,
            project_id UNINDEXED,
            evidence_session_id UNINDEXED,
            turn_id,
            input_kind,
            visible_text,
            tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS turn_tool_event(
            tool_event_sha256 TEXT PRIMARY KEY,
            control_record_sha256 TEXT NOT NULL REFERENCES turn_entry(control_record_sha256),
            tool_use_id TEXT NOT NULL,
            phase TEXT NOT NULL CHECK(phase IN ('before','after')),
            tool_name TEXT NOT NULL,
            lineage_event_sha256 TEXT NOT NULL,
            event_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(tool_use_id, phase)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS turn_commit(
            commit_sha256 TEXT PRIMARY KEY,
            control_record_sha256 TEXT NOT NULL REFERENCES turn_entry(control_record_sha256),
            response_record_sha256 TEXT NOT NULL UNIQUE,
            lineage_event_sha256 TEXT NOT NULL,
            commit_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE UNIQUE INDEX IF NOT EXISTS turn_commit_one_per_entry
            ON turn_commit(control_record_sha256);
        CREATE VIRTUAL TABLE IF NOT EXISTS turn_commit_fts USING fts5(
            commit_sha256 UNINDEXED,
            control_record_sha256 UNINDEXED,
            turn_id UNINDEXED,
            project_id UNINDEXED,
            visible_response,
            operational_links,
            tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS turn_research_question(
            research_record_sha256 TEXT PRIMARY KEY,
            control_record_sha256 TEXT NOT NULL UNIQUE
                REFERENCES turn_entry(control_record_sha256),
            project_id TEXT NOT NULL,
            evidence_session_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            prompt_index INTEGER NOT NULL CHECK(prompt_index > 0),
            access_scope TEXT NOT NULL
                CHECK(access_scope='PROJECT_TASK_PRIVATE_ANALYSIS'),
            research_focus TEXT NOT NULL,
            question_sha256_after_redaction TEXT NOT NULL,
            visible_question_after_redaction TEXT NOT NULL,
            prior_research_record_sha256 TEXT
                REFERENCES turn_research_question(research_record_sha256),
            record_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(project_id, evidence_session_id, task_id, prompt_index)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS turn_research_project_task_idx
            ON turn_research_question(project_id, evidence_session_id, task_id, prompt_index);
        CREATE TABLE IF NOT EXISTS turn_goal_usage(
            usage_record_sha256 TEXT PRIMARY KEY,
            control_record_sha256 TEXT NOT NULL UNIQUE
                REFERENCES turn_entry(control_record_sha256),
            project_id TEXT NOT NULL,
            evidence_session_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            prompt_index INTEGER NOT NULL CHECK(prompt_index > 0),
            availability TEXT NOT NULL CHECK(availability IN ('AVAILABLE','UNAVAILABLE')),
            goal_id TEXT,
            metric_semantics TEXT,
            goal_accounted_tokens INTEGER CHECK(goal_accounted_tokens >= 0),
            prior_usage_record_sha256 TEXT
                REFERENCES turn_goal_usage(usage_record_sha256),
            observation_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(project_id, evidence_session_id, prompt_index),
            CHECK(
                (availability='AVAILABLE' AND goal_id IS NOT NULL
                    AND metric_semantics IS NOT NULL
                    AND goal_accounted_tokens IS NOT NULL)
                OR
                (availability='UNAVAILABLE' AND goal_accounted_tokens IS NULL)
            )
        ) STRICT;
        CREATE INDEX IF NOT EXISTS turn_goal_usage_project_task_idx
            ON turn_goal_usage(project_id, evidence_session_id, task_id, prompt_index);
        """
    )
    return connection


def _ensure_research_question(
    connection: sqlite3.Connection,
    *,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Append one private project-task research question for a research-mode turn."""

    policy = (entry.get("binding") or {}).get("research_policy") or {}
    if policy.get("enabled") is not True:
        return {
            "state": "NOT_APPLICABLE",
            "reason": "RESEARCH_MODE_NOT_ACTIVE",
            "access_scope": _PROJECT_TASK_PRIVATE_ANALYSIS,
            "cross_project_retrieval": False,
        }
    existing = connection.execute(
        """
        SELECT record_json FROM turn_research_question
        WHERE control_record_sha256=?
        """,
        (entry["control_record_sha256"],),
    ).fetchone()
    if existing is not None:
        record = json.loads(existing["record_json"])
        claimed = str(record.get("research_record_sha256") or "")
        actual = sha256_bytes(
            canonical_json_bytes(
                {
                    key: value
                    for key, value in record.items()
                    if key != "research_record_sha256"
                }
            )
        )
        _require(
            claimed == actual,
            "TURN_CONTROL_RESEARCH_RECORD_MISMATCH",
            "A private project-task research record failed its SHA-256 verification.",
        )
        action = "RECORDED_IDEMPOTENT_REUSE"
    else:
        prior = connection.execute(
            """
            SELECT research_record_sha256 FROM turn_research_question
            WHERE project_id=? AND evidence_session_id=? AND task_id=?
            ORDER BY prompt_index DESC LIMIT 1
            """,
            (entry["project_id"], entry["evidence_session_id"], entry["task_id"]),
        ).fetchone()
        visible_question = str(entry["visible_input_after_redaction"])
        _require(
            not contains_secret(visible_question),
            "TURN_CONTROL_RESEARCH_REDACTION_FAILED",
            "A secret-like value remained in the private project-task research question.",
        )
        research_focus = str(policy.get("focus") or "PROJECT_TASK_RESEARCH")
        aligned_research_question = (
            _MEMORY_PLUS_LEARNING_RESEARCH_QUESTION
            if research_focus == "MEMORY_PLUS_LEARNING"
            else visible_question
        )
        _require(
            not contains_secret(aligned_research_question),
            "TURN_CONTROL_RESEARCH_ALIGNMENT_REDACTION_FAILED",
            "A secret-like value remained in the aligned private research question.",
        )
        record = {
            "schema": "evidence-lane.project-task-research-question.v2",
            "project_id": entry["project_id"],
            "evidence_session_id": entry["evidence_session_id"],
            "task_id": entry["task_id"],
            "plan_task_id": entry["plan_task_id"],
            "host_session_id_sha256": sha256_bytes(
                str(entry["host_session_id"]).encode("utf-8")
            ),
            "turn_id": entry["turn_id"],
            "prompt_index": entry["prompt_index"],
            "control_record_sha256": entry["control_record_sha256"],
            "question_kind": entry["input_kind"],
            "visible_question_after_redaction": visible_question,
            "question_sha256_after_redaction": sha256_bytes(
                visible_question.encode("utf-8")
            ),
            "research_focus": research_focus,
            "research_focus_basis_sha256": policy.get("focus_basis_sha256"),
            "aligned_research_question": aligned_research_question,
            "aligned_research_question_sha256": sha256_bytes(
                aligned_research_question.encode("utf-8")
            ),
            "alignment_basis": "PERSISTENT_PLAN_ROW_AND_LINKED_STEER_DELTAS",
            "autolog_scope": "CURRENT_PROJECT_SESSION_TASK_ONLY",
            "linked_steer_delta_receipts": entry["binding"]["persistent_plan_row"].get(
                "linked_steer_delta_receipts", []
            ),
            "access_scope": _PROJECT_TASK_PRIVATE_ANALYSIS,
            "cross_project_retrieval": False,
            "shared_global_telemetry": False,
            "shared_fts_indexed": False,
            "public_output_included": False,
            "candidate_claim_included": False,
            "raw_question_stored": False,
            "private_reasoning_stored": False,
            "prior_research_record_sha256": (
                prior["research_record_sha256"] if prior else None
            ),
            "recorded_at": entry["prepared_at"],
        }
        record["research_record_sha256"] = sha256_bytes(canonical_json_bytes(record))
        connection.execute(
            """
            INSERT INTO turn_research_question VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record["research_record_sha256"],
                record["control_record_sha256"],
                record["project_id"],
                record["evidence_session_id"],
                record["task_id"],
                record["turn_id"],
                record["prompt_index"],
                record["access_scope"],
                record["research_focus"],
                record["question_sha256_after_redaction"],
                record["visible_question_after_redaction"],
                record["prior_research_record_sha256"],
                json.dumps(record, sort_keys=True, separators=(",", ":")),
                record["recorded_at"],
            ),
        )
        action = "RECORDED"
    return {
        "state": action,
        "research_record_sha256": record["research_record_sha256"],
        "research_focus": record["research_focus"],
        "access_scope": record["access_scope"],
        "cross_project_retrieval": False,
        "shared_fts_indexed": False,
        "private_reasoning_stored": False,
    }


def _goal_usage_observation(host_payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize only trustworthy Goal-accounted counters exposed by the host."""

    raw = host_payload.get("goal_usage")
    if raw is None:
        observation = {
            "schema": "evidence-lane.goal-usage-observation.v1",
            "availability": "UNAVAILABLE",
            "reason": "HOST_GOAL_ACCOUNTED_COUNTER_NOT_EXPOSED",
            "accounting_basis": "GOAL_ACCOUNTED_TOKENS_ONLY",
            "goal_id": None,
            "metric_semantics": None,
            "goal_accounted_tokens": None,
            "provided_fields": [],
            "aggregation_rule": "NEVER_SUM_CUMULATIVE_SNAPSHOTS",
            "task_status_effect": "NONE",
            "goal_completion_effect": "NONE",
            "private_reasoning_stored": False,
        }
        observation["observation_sha256"] = sha256_bytes(
            canonical_json_bytes(observation)
        )
        return observation
    _require(
        isinstance(raw, dict),
        "TURN_CONTROL_GOAL_USAGE_INVALID",
        "Host Goal usage must be a structured provenance object.",
    )
    safe = _turn_redact(raw)
    serialized = json.dumps(
        safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    _require(
        not contains_secret(serialized),
        "TURN_CONTROL_GOAL_USAGE_REDACTION_FAILED",
        "A secret-like value remained in Goal usage provenance.",
    )
    goal_id = str(safe.get("goal_id") or "").strip()
    semantics = str(safe.get("metric_semantics") or "").strip().upper()
    tokens = safe.get("goal_accounted_tokens")
    provenance = safe.get("provenance")
    complete = (
        bool(goal_id)
        and semantics in _GOAL_USAGE_SEMANTICS
        and isinstance(tokens, int)
        and not isinstance(tokens, bool)
        and tokens >= 0
        and isinstance(provenance, dict)
        and bool(str(provenance.get("source") or "").strip())
    )
    if complete:
        elapsed_seconds = safe.get("elapsed_seconds")
        _require(
            elapsed_seconds is None
            or (
                isinstance(elapsed_seconds, int)
                and not isinstance(elapsed_seconds, bool)
                and elapsed_seconds >= 0
            ),
            "TURN_CONTROL_GOAL_ELAPSED_INVALID",
            "Goal elapsed seconds must be an exact non-negative integer when supplied.",
        )
        observation = {
            "schema": "evidence-lane.goal-usage-observation.v1",
            "availability": "AVAILABLE",
            "accounting_basis": "GOAL_ACCOUNTED_TOKENS_ONLY",
            "goal_id": goal_id,
            "metric_semantics": semantics,
            "goal_accounted_tokens": tokens,
            "elapsed_seconds": elapsed_seconds,
            "provenance": provenance,
            "aggregation_rule": "NEVER_SUM_CUMULATIVE_SNAPSHOTS",
            "task_status_effect": "NONE",
            "goal_completion_effect": "NONE",
            "exact_counts_preserved": True,
            "private_reasoning_stored": False,
        }
    else:
        observation = {
            "schema": "evidence-lane.goal-usage-observation.v1",
            "availability": "UNAVAILABLE",
            "reason": "INCOMPLETE_OR_UNTRUSTWORTHY_GOAL_USAGE_PROVENANCE",
            "accounting_basis": "GOAL_ACCOUNTED_TOKENS_ONLY",
            "goal_id": None,
            "metric_semantics": None,
            "goal_accounted_tokens": None,
            "provided_fields": sorted(str(key) for key in safe),
            "aggregation_rule": "NEVER_SUM_CUMULATIVE_SNAPSHOTS",
            "task_status_effect": "NONE",
            "goal_completion_effect": "NONE",
            "private_reasoning_stored": False,
        }
    observation["observation_sha256"] = sha256_bytes(canonical_json_bytes(observation))
    return observation


def _ensure_goal_usage(
    connection: sqlite3.Connection,
    *,
    entry: dict[str, Any],
    observation: dict[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    """Append one idempotent project-local Goal usage observation per committed turn."""

    existing = connection.execute(
        "SELECT record_json FROM turn_goal_usage WHERE control_record_sha256=?",
        (entry["control_record_sha256"],),
    ).fetchone()
    if existing is not None:
        record = json.loads(existing["record_json"])
        claimed = str(record.get("usage_record_sha256") or "")
        actual = sha256_bytes(
            canonical_json_bytes(
                {
                    key: value
                    for key, value in record.items()
                    if key != "usage_record_sha256"
                }
            )
        )
        _require(
            claimed == actual and record.get("observation") == observation,
            "TURN_CONTROL_GOAL_USAGE_RECORD_MISMATCH",
            "The turn already binds different Goal usage evidence.",
        )
        action = "RECORDED_IDEMPOTENT_REUSE"
    else:
        prior = connection.execute(
            """
            SELECT usage_record_sha256 FROM turn_goal_usage
            WHERE project_id=? AND evidence_session_id=?
            ORDER BY prompt_index DESC LIMIT 1
            """,
            (entry["project_id"], entry["evidence_session_id"]),
        ).fetchone()
        record = {
            "schema": "evidence-lane.project-goal-usage-record.v1",
            "project_id": entry["project_id"],
            "evidence_session_id": entry["evidence_session_id"],
            "task_id": entry["task_id"],
            "turn_id": entry["turn_id"],
            "prompt_index": entry["prompt_index"],
            "control_record_sha256": entry["control_record_sha256"],
            "observation": observation,
            "observation_sha256": observation["observation_sha256"],
            "prior_usage_record_sha256": (
                prior["usage_record_sha256"] if prior else None
            ),
            "project_local_only": True,
            "cross_project_aggregation": False,
            "intermediate_snapshot_double_count_prevented": True,
            "private_reasoning_stored": False,
            "recorded_at": recorded_at,
        }
        record["usage_record_sha256"] = sha256_bytes(canonical_json_bytes(record))
        connection.execute(
            """
            INSERT INTO turn_goal_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record["usage_record_sha256"],
                record["control_record_sha256"],
                record["project_id"],
                record["evidence_session_id"],
                record["task_id"],
                record["turn_id"],
                record["prompt_index"],
                observation["availability"],
                observation.get("goal_id"),
                observation.get("metric_semantics"),
                observation.get("goal_accounted_tokens"),
                record["prior_usage_record_sha256"],
                record["observation_sha256"],
                json.dumps(record, sort_keys=True, separators=(",", ":")),
                record["recorded_at"],
            ),
        )
        action = "RECORDED"
    return {
        "state": action,
        "availability": observation["availability"],
        "usage_record_sha256": record["usage_record_sha256"],
        "observation_sha256": observation["observation_sha256"],
        "goal_id": observation.get("goal_id"),
        "metric_semantics": observation.get("metric_semantics"),
        "goal_accounted_tokens": observation.get("goal_accounted_tokens"),
        "aggregation_rule": "NEVER_SUM_CUMULATIVE_SNAPSHOTS",
        "project_local_only": True,
        "private_reasoning_stored": False,
    }


@contextmanager
def _control_lock(project_root: Path):
    """Serialize one project's Prepare/Commit projection without a second writer."""

    path = project_root / "lineage" / "codex_turn_control.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    for _ in range(200):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            time.sleep(0.025)
    _require(
        descriptor is not None,
        "TURN_CONTROL_SINGLE_WRITER_BUSY",
        "The governed Codex turn-control writer is already active.",
        lock_path=str(path),
    )
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _input_kind(host_payload: dict[str, Any]) -> str:
    source = str(host_payload.get("source") or "user_prompt").strip().lower()
    if host_payload.get("is_goal") is True or "goal" in source:
        return "goal"
    if host_payload.get("is_steer") is True or "steer" in source:
        return "steer"
    return "user_prompt"


def _host_key(host_session_id: str) -> str:
    return "host-" + sha256_bytes(host_session_id.encode("utf-8"))[:40].lower()


def _projection_path(
    root: Path,
    *,
    kind: str,
    host_session_id: str,
    prompt_index: int,
    turn_id: str,
) -> Path:
    turn_key = sha256_bytes(turn_id.encode("utf-8"))[:16].lower()
    return (
        root
        / f"{kind}-index"
        / _host_key(host_session_id)
        / f"{prompt_index:08d}-{turn_key}.json"
    )


def record_non_strict_visible_input(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
) -> dict[str, Any]:
    """Retain the bounded v1 prompt index until sealed Mode plus Plan is active.

    This compatibility path records only the secret-redacted visible user input.
    It does not create a strict PREPARE, authorize source mutation, run research
    retrieval, or persist private reasoning.  Once strict turn control is active,
    ``prepare_turn`` is the sole prompt-entry writer.
    """

    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    turn_id = str(host_payload.get("turn_id") or "").strip()
    _require(
        bool(host_session_id and turn_id),
        "TURN_CONTROL_HOST_TURN_IDENTITY_REQUIRED",
        "Visible-input indexing requires exact host-session and turn identities.",
    )
    policy = policy_state(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    _require(
        policy.get("governed_session") is True
        and policy.get("strict_required") is False,
        "TURN_CONTROL_NON_STRICT_POLICY_NOT_ACTIVE",
        "Compatibility indexing is available only before sealed Mode plus Plan activates.",
        policy=policy,
    )
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    project_root = Path(bound["project_root"])
    project = bound["project"]
    session = bound["session"]
    metadata = session.get("metadata") or {}
    pointer = _json(project_root / "active_pointer.json")
    project_id = str(session.get("project_id") or "")
    evidence_session_id = str(session.get("session_id") or "")
    accepted_pv = pointer.get("accepted_pv")
    pointer_generation = int(pointer.get("generation") or 0)
    _require(
        bool(project_id and evidence_session_id)
        and project_id == project.get("project_id") == pointer.get("project_id"),
        "TURN_CONTROL_PROJECT_SESSION_POINTER_MISMATCH",
        "Project, session, and accepted-pointer identities do not agree.",
    )
    _require(
        session.get("accepted_pv") == accepted_pv
        and int(session.get("accepted_pointer_generation") or 0)
        == pointer_generation
        and metadata.get("entry_pv") == accepted_pv,
        "TURN_CONTROL_ENTRY_POINTER_MISMATCH",
        "The governed Entry boundary does not match the accepted pointer.",
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
    )
    visible_input = _turn_redact_text(str(host_payload.get("prompt") or ""))
    _require(
        bool(visible_input.strip()),
        "TURN_CONTROL_VISIBLE_INPUT_REQUIRED",
        "Visible-input indexing requires a non-empty prompt, steer, or Goal.",
    )
    _require(
        not contains_secret(visible_input),
        "TURN_CONTROL_SECRET_REDACTION_FAILED",
        "A secret-like value remained in the visible input after redaction.",
    )
    input_kind = _input_kind(host_payload)
    visible_input_sha256 = sha256_bytes(visible_input.encode("utf-8"))
    task = session.get("task") if isinstance(session.get("task"), dict) else {}
    task_id = str(task.get("task_id") or "") or None

    with _control_lock(project_root):
        try:
            all_records = PromptIndex(root)._all_records()
        except EvidenceLaneError as exc:
            raise TurnControlError(
                "TURN_CONTROL_PROMPT_INDEX_AUTHORITY_INVALID",
                "The existing prompt-index authority could not be verified.",
                upstream_code=exc.code,
                upstream_status=exc.status,
                **exc.details,
            ) from exc
        records = [
            row
            for row in all_records
            if row.get("project_id") == project_id
            and row.get("evidence_session_id") == evidence_session_id
        ]
        duplicate = next(
            (
                row
                for row in records
                if row.get("host_session_id") == host_session_id
                and row.get("turn_id") == turn_id
                and row.get("input_kind", "user_prompt") == input_kind
                and row.get("prompt_sha256_after_redaction")
                == visible_input_sha256
            ),
            None,
        )
        if duplicate is None:
            prior = records[-1] if records else None
            prompt_index = int(prior.get("prompt_index") or 0) + 1 if prior else 1
            recorded_at = _now()
            record = {
                "schema": "evidence-lane.prompt-index.v1",
                "host_session_id": host_session_id,
                "turn_id": turn_id,
                "input_kind": input_kind,
                "prompt_index": prompt_index,
                "visible_prompt_after_redaction": visible_input,
                "prompt_sha256_after_redaction": visible_input_sha256,
                "prompt_chars_after_redaction": len(visible_input),
                "raw_prompt_stored": False,
                "redacted_visible_prompt_stored": True,
                "project_id": project_id,
                "evidence_session_id": evidence_session_id,
                "entry_pv": accepted_pv,
                "pointer_generation": pointer_generation,
                "cwd_sha256": sha256_bytes(
                    str(host_payload.get("cwd") or "").encode("utf-8")
                ),
                "prior_record_sha256": (
                    prior.get("record_sha256") if prior is not None else None
                ),
                "recorded_at": recorded_at,
            }
            record["record_sha256"] = sha256_bytes(canonical_json_bytes(record))
            prompt_path = _projection_path(
                root,
                kind="prompt",
                host_session_id=host_session_id,
                prompt_index=prompt_index,
                turn_id=turn_id,
            )
            _require(
                not prompt_path.exists(),
                "TURN_CONTROL_PROMPT_PROJECTION_CONFLICT",
                "The next prompt-index projection path is already occupied.",
                prompt_index=prompt_index,
            )
            atomic_write_json(prompt_path, record)
            action = "INDEXED"
        else:
            record = duplicate
            prompt_index = int(record["prompt_index"])
            recorded_at = str(record["recorded_at"])
            action = "INDEXED_IDEMPOTENT_REUSE"

        event_id = (
            "evt_"
            + sha256_bytes(
                (str(record["record_sha256"]) + "\0non-strict-visible-input").encode(
                    "utf-8"
                )
            )[:26].lower()
        )
        lineage = ChatLineage(
            project_root / "lineage" / f"{evidence_session_id}.jsonl"
        )
        try:
            event = lineage.append(
                event_type={
                    "steer": "turn.visible_user_steer",
                    "goal": "turn.visible_user_goal",
                }.get(input_kind, "turn.visible_user_prompt"),
                visible_payload={
                    "turn_id": turn_id,
                    "prompt_index": prompt_index,
                    "input_kind": input_kind,
                    "visible_user_prompt_after_redaction": record[
                        "visible_prompt_after_redaction"
                    ],
                    "prompt_sha256_after_redaction": record[
                        "prompt_sha256_after_redaction"
                    ],
                    "prompt_record_sha256": record["record_sha256"],
                    "entry_pv": accepted_pv,
                    "pointer_generation": pointer_generation,
                    "lifecycle_state": session.get("state"),
                    "strict_turn_control_active": False,
                    "private_reasoning_excluded": True,
                },
                occurred_at=recorded_at,
                session_id=evidence_session_id,
                task_id=task_id,
                event_id=event_id,
                actor_type="user",
            )
        except EvidenceLaneError as exc:
            raise TurnControlError(
                "TURN_CONTROL_VISIBLE_INPUT_LINEAGE_INVALID",
                "The visible-input ChatLineage event could not be verified.",
                upstream_code=exc.code,
                upstream_status=exc.status,
                **exc.details,
            ) from exc

    return {
        "schema": "evidence-lane.non-strict-visible-input.v1",
        "state": action,
        "prompt_index": prompt_index,
        "turn_id": turn_id,
        "input_kind": input_kind,
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "entry_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "raw_prompt_stored": False,
        "redacted_visible_prompt_stored": True,
        "private_reasoning_stored": False,
        "strict_prepare_created": False,
        "source_mutation_authorized": False,
        "record_sha256": record["record_sha256"],
        "lineage_event_id": event["event_id"],
        "lineage_event_sha256": event["event_sha256"],
    }


def _attachment_identities(
    host_payload: dict[str, Any],
    *,
    repository_path: str,
) -> list[dict[str, Any]]:
    values: list[Any] = []
    for key in ("attachments", "files", "file_paths", "context_files"):
        raw = host_payload.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif raw is not None:
            values.append(raw)
    repository = Path(repository_path).resolve()
    identities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values[:200]:
        if isinstance(value, dict):
            identity: dict[str, Any] = {
                str(key): _turn_redact(item)
                for key, item in value.items()
                if str(key)
                in {
                    "id",
                    "name",
                    "path",
                    "file_path",
                    "mime_type",
                    "media_type",
                    "size",
                    "sha256",
                }
            }
            raw_path = identity.get("path") or identity.get("file_path")
        else:
            identity = {"path": _turn_redact_text(str(value))}
            raw_path = identity["path"]
        if isinstance(raw_path, str) and raw_path.strip():
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = repository / candidate
            try:
                resolved = candidate.resolve()
                if resolved.is_file():
                    identity["content_sha256"] = sha256_file(resolved)
                    identity["size"] = resolved.stat().st_size
                    identity["inside_governed_repository"] = _within(
                        resolved, repository
                    )
            except OSError:
                identity["content_identity_availability"] = "UNAVAILABLE"
        safe_json = json.dumps(
            _turn_redact(identity),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        _require(
            not contains_secret(safe_json),
            "TURN_CONTROL_ATTACHMENT_REDACTION_FAILED",
            "A secret-like value remained in an attachment identity.",
        )
        identity = json.loads(safe_json)
        identity["identity_sha256"] = sha256_bytes(canonical_json_bytes(identity))
        if identity["identity_sha256"] in seen:
            continue
        seen.add(identity["identity_sha256"])
        identities.append(identity)
    return sorted(identities, key=lambda row: str(row["identity_sha256"]))


def gap_receipt(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
    error: TurnControlError,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit one secret-free deterministic gap receipt instead of silent NOT_INDEXED."""

    root = Path(store_root).resolve()
    exact_policy = dict(policy or {})
    visible = _turn_redact_text(
        str(
            host_payload.get("prompt")
            or host_payload.get("last_assistant_message")
            or ""
        )
    )
    core = {
        "schema": "evidence-lane.codex-turn-control-gap.v1",
        "state": "TURN_CONTROL_GAP",
        "code": error.code,
        "message": error.message,
        "host_session_id_sha256": sha256_bytes(
            str(host_payload.get("session_id") or "").encode("utf-8")
        ),
        "turn_id_sha256": sha256_bytes(
            str(host_payload.get("turn_id") or "").encode("utf-8")
        ),
        "cwd_sha256": sha256_bytes(str(host_payload.get("cwd") or "").encode("utf-8")),
        "visible_subject_sha256_after_redaction": sha256_bytes(visible.encode("utf-8")),
        "input_kind": _input_kind(host_payload),
        "project_id": exact_policy.get("project_id"),
        "evidence_session_id": exact_policy.get("evidence_session_id"),
        "binding_match": exact_policy.get("binding_match"),
        "fail_closed": bool(
            exact_policy.get("governed_session") and exact_policy.get("strict_required")
        ),
        "source_mutation_authorized": False,
        "raw_secret_stored": False,
        "private_reasoning_stored": False,
        "scrollback_used": False,
        "transcript_used": False,
    }
    core["gap_receipt_sha256"] = sha256_bytes(canonical_json_bytes(core))
    if exact_policy.get("governed_session"):
        path = root / "turn-control-gaps" / f"{core['gap_receipt_sha256']}.json"
        if path.exists():
            stored = _json(path)
            _require(
                stored == core,
                "TURN_CONTROL_GAP_RECEIPT_CONFLICT",
                "A deterministic gap identity already contains different evidence.",
                path=str(path),
            )
        else:
            atomic_write_json(path, core)
        core["gap_receipt_path"] = str(path)
    return core


def _project_prepared_entry(
    root: Path,
    *,
    project_root: Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
    prompt_record = dict(entry["prompt_record"])
    prompt_path = _projection_path(
        root,
        kind="prompt",
        host_session_id=str(entry["host_session_id"]),
        prompt_index=int(entry["prompt_index"]),
        turn_id=str(entry["turn_id"]),
    )
    if prompt_path.exists():
        _require(
            _json(prompt_path) == prompt_record,
            "TURN_CONTROL_PROMPT_PROJECTION_CONFLICT",
            "The prompt projection already contains different visible evidence.",
            path=str(prompt_path),
        )
    else:
        atomic_write_json(prompt_path, prompt_record)
    lineage_path = project_root / "lineage" / f"{entry['evidence_session_id']}.jsonl"
    lineage = ChatLineage(lineage_path)
    input_event_id = (
        "evt_"
        + sha256_bytes(
            (str(entry["prompt_record_sha256"]) + "\0visible-input").encode("utf-8")
        )[:26].lower()
    )
    input_event = lineage.append(
        event_type={
            "steer": "turn.visible_user_steer",
            "goal": "turn.visible_user_goal",
        }.get(str(entry["input_kind"]), "turn.visible_user_prompt"),
        visible_payload={
            "turn_id": entry["turn_id"],
            "prompt_index": entry["prompt_index"],
            "input_kind": entry["input_kind"],
            "visible_input_after_redaction": entry["visible_input_after_redaction"],
            "visible_input_sha256_after_redaction": entry[
                "visible_input_sha256_after_redaction"
            ],
            "attachment_identities": entry["attachment_identities"],
            "prompt_record_sha256": entry["prompt_record_sha256"],
            "entry_slip": entry["entry_slip"],
            "private_reasoning_excluded": True,
        },
        occurred_at=entry["prepared_at"],
        session_id=entry["evidence_session_id"],
        task_id=entry["task_id"],
        event_id=input_event_id,
        actor_type="user",
    )
    prepare_event_id = (
        "evt_"
        + sha256_bytes(
            (str(entry["control_record_sha256"]) + "\0prepare").encode("utf-8")
        )[:26].lower()
    )
    prepare_event = lineage.append(
        event_type="turn.control_prepare",
        visible_payload={
            "state": "PREPARED_NOT_COMMITTED",
            "turn_id": entry["turn_id"],
            "input_kind": entry["input_kind"],
            "prompt_index": entry["prompt_index"],
            "prompt_record_sha256": entry["prompt_record_sha256"],
            "control_record_sha256": entry["control_record_sha256"],
            "binding_sha256": entry["binding_sha256"],
            "retrieval_receipt_sha256": entry["retrieval_receipt_sha256"],
            "prior_lineage_head_sha256": entry["prior_lineage_head_sha256"],
            "persistent_plan_row": entry["binding"]["persistent_plan_row"],
            "lane_classification": entry["lane_classification"],
            "mode_classification": entry["mode_classification"],
            "operators": entry["operators"],
            "gates": entry["gates"],
            "bounded_write_scope": entry["bounded_write_scope"],
            "source_change_entry_sha256": (
                (entry.get("source_change_entry") or {}).get(
                    "source_change_snapshot_sha256"
                )
            ),
            "scrollback_authority": False,
            "transcript_authority": False,
            "private_reasoning_excluded": True,
        },
        occurred_at=entry["prepared_at"],
        session_id=entry["evidence_session_id"],
        task_id=entry["task_id"],
        event_id=prepare_event_id,
        actor_type="system",
    )
    projection = lineage.projection_status()
    return {
        "prompt_projection_path": str(prompt_path),
        "visible_input_lineage_event_sha256": input_event["event_sha256"],
        "prepare_lineage_event_sha256": prepare_event["event_sha256"],
        "lineage_projection": projection,
    }


def prepare_turn(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
) -> dict[str, Any]:
    """Create exactly one secret-redacted PREPARE before governed reasoning."""

    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    turn_id = str(host_payload.get("turn_id") or "").strip()
    _require(
        bool(host_session_id and turn_id),
        "TURN_CONTROL_HOST_TURN_IDENTITY_REQUIRED",
        "A governed PREPARE requires exact host-session and turn identities.",
    )
    policy = policy_state(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    _require(
        policy.get("governed_session") is True
        and policy.get("strict_required") is True,
        "TURN_CONTROL_POLICY_NOT_ACTIVE",
        "The exact governed session has not activated the sealed Mode plus Plan contract.",
        policy=policy,
    )
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    binding = _binding_snapshot(root, bound)
    project_root = Path(bound["project_root"])
    raw_visible = str(host_payload.get("prompt") or "")
    visible_input = _turn_redact_text(raw_visible)
    _require(
        bool(visible_input.strip()),
        "TURN_CONTROL_VISIBLE_INPUT_REQUIRED",
        "A governed PREPARE requires a non-empty visible prompt, steer, or Goal.",
    )
    _require(
        not contains_secret(visible_input),
        "TURN_CONTROL_SECRET_REDACTION_FAILED",
        "A secret-like value remained in the visible input after redaction.",
    )
    input_kind = _input_kind(host_payload)
    visible_input_sha256 = sha256_bytes(visible_input.encode("utf-8"))
    attachments = _attachment_identities(
        host_payload,
        repository_path=str(bound["project"].get("repository_path") or ""),
    )
    live_source_snapshot = _source_change_snapshot(
        root,
        binding=binding,
        cwd=str(host_payload.get("cwd") or ""),
    )
    with _control_lock(project_root):
        try:
            prompt_records = [
                row
                for row in PromptIndex(root)._all_records()
                if row.get("project_id") == binding["project_id"]
                and row.get("evidence_session_id") == binding["evidence_session_id"]
            ]
        except EvidenceLaneError as exc:
            raise TurnControlError(
                "TURN_CONTROL_PROMPT_INDEX_AUTHORITY_INVALID",
                "The existing prompt-index authority could not be verified.",
                upstream_code=exc.code,
                upstream_status=exc.status,
                **exc.details,
            ) from exc
        with _connection(project_root) as connection:
            existing_rows = connection.execute(
                """
                SELECT record_json FROM turn_entry
                WHERE host_session_id=? AND turn_id=?
                ORDER BY prompt_index
                """,
                (host_session_id, turn_id),
            ).fetchall()
            existing_entry = None
            for row in existing_rows:
                candidate = json.loads(row["record_json"])
                if (
                    candidate.get("input_kind") == input_kind
                    and candidate.get("visible_input_sha256_after_redaction")
                    == visible_input_sha256
                    and candidate.get("attachment_identities") == attachments
                ):
                    existing_entry = candidate
                    break
            if existing_entry is not None:
                _require(
                    existing_entry.get("binding_sha256") == binding["binding_sha256"],
                    "TURN_CONTROL_BINDING_DRIFT",
                    "The exact PREPARE exists but its project, pointer, Mode, or Plan binding drifted.",
                )
                entry = existing_entry
                action = "PREPARED_IDEMPOTENT_REUSE"
            else:
                latest = connection.execute(
                    """
                    SELECT prompt_index, control_record_sha256, prompt_record_sha256
                    FROM turn_entry
                    WHERE project_id=? AND evidence_session_id=?
                    ORDER BY prompt_index DESC LIMIT 1
                    """,
                    (binding["project_id"], binding["evidence_session_id"]),
                ).fetchone()
                latest_prompt = prompt_records[-1] if prompt_records else None
                if latest is not None:
                    strict_prompt_matches = [
                        row
                        for row in prompt_records
                        if row.get("record_sha256")
                        == latest["prompt_record_sha256"]
                        and int(row.get("prompt_index") or 0)
                        == int(latest["prompt_index"])
                    ]
                    _require(
                        len(strict_prompt_matches) == 1,
                        "TURN_CONTROL_PROMPT_SQLITE_PROJECTION_MISMATCH",
                        "Strict turn-control SQLite does not match its prompt-index projection.",
                    )
                prompt_index = (
                    int(latest_prompt.get("prompt_index") or 0) + 1
                    if latest_prompt is not None
                    else 1
                )
                prior_control = latest["control_record_sha256"] if latest else None
                lineage_path = (
                    project_root / "lineage" / f"{binding['evidence_session_id']}.jsonl"
                )
                lineage_events = ChatLineage(lineage_path).events()
                prior_lineage_head = (
                    lineage_events[-1].get("event_sha256") if lineage_events else None
                )
                retrieval = _retrieval_receipt(
                    project_root=project_root,
                    binding=binding,
                    visible_text=visible_input,
                )
                prepared_at = _now()
                prompt_record = {
                    "schema": "evidence-lane.prompt-index.v2",
                    "host_session_id": host_session_id,
                    "turn_id": turn_id,
                    "input_kind": input_kind,
                    "prompt_index": prompt_index,
                    "visible_prompt_after_redaction": visible_input,
                    "prompt_sha256_after_redaction": visible_input_sha256,
                    "prompt_chars_after_redaction": len(visible_input),
                    "attachment_identities": attachments,
                    "raw_prompt_stored": False,
                    "redacted_visible_prompt_stored": True,
                    "project_id": binding["project_id"],
                    "evidence_session_id": binding["evidence_session_id"],
                    "entry_pv": binding["accepted_pv"],
                    "pointer_generation": binding["pointer_generation"],
                    "cwd_sha256": sha256_bytes(
                        str(host_payload.get("cwd") or "").encode("utf-8")
                    ),
                    "prior_record_sha256": (
                        latest_prompt.get("record_sha256")
                        if latest_prompt is not None
                        else None
                    ),
                    "recorded_at": prepared_at,
                }
                prompt_record["record_sha256"] = sha256_bytes(
                    canonical_json_bytes(prompt_record)
                )
                operators = [
                    {
                        "mode_id": row["mode_id"],
                        "operator_families": row["operator_families"],
                        "operator_receipt_sha256": row["operator_receipt_sha256"],
                    }
                    for row in binding["env_uop_authorities"]
                ]
                entry = {
                    "schema": "evidence-lane.codex-turn-entry.v2",
                    "prepare_state": "PREPARED_NOT_COMMITTED",
                    "project_id": binding["project_id"],
                    "evidence_session_id": binding["evidence_session_id"],
                    "task_id": binding["task_id"],
                    "plan_task_id": binding["plan_task_id"],
                    "host_session_id": host_session_id,
                    "turn_id": turn_id,
                    "input_kind": input_kind,
                    "prompt_index": prompt_index,
                    "prompt_record": prompt_record,
                    "prompt_record_sha256": prompt_record["record_sha256"],
                    "visible_input_after_redaction": visible_input,
                    "visible_input_sha256_after_redaction": visible_input_sha256,
                    "attachment_identities": attachments,
                    "prior_control_record_sha256": prior_control,
                    "prior_lineage_head_sha256": prior_lineage_head,
                    "binding": binding,
                    "binding_sha256": binding["binding_sha256"],
                    "entry_slip": binding["entry_slip"],
                    "retrieval": retrieval,
                    "retrieval_receipt_sha256": retrieval["retrieval_receipt_sha256"],
                    "lane_classification": {
                        "canonical_lanes": binding["canonical_lanes"],
                        "basis": "SEALED_ENV_UOP_MODE_BINDING",
                        "classified_before_reasoning": True,
                    },
                    "mode_classification": {
                        "selected_mode_ids": binding["selected_mode_ids"],
                        "mode_binding_receipt_sha256": binding[
                            "mode_binding_receipt_sha256"
                        ],
                        "classified_before_reasoning": True,
                    },
                    "operators": operators,
                    "gates": binding["gates"],
                    "bounded_write_scope": binding["persistent_plan_row"][
                        "bounded_write_scope"
                    ],
                    "source_change_entry": live_source_snapshot,
                    "prepared_at": prepared_at,
                    "scrollback_authority": False,
                    "transcript_authority": False,
                    "private_reasoning_stored": False,
                }
                entry["control_record_sha256"] = sha256_bytes(
                    canonical_json_bytes(entry)
                )
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO turn_entry(
                        control_record_sha256, project_id, evidence_session_id,
                        host_session_id, turn_id, input_kind, prompt_index,
                        prompt_record_sha256, prior_control_record_sha256,
                        binding_sha256, retrieval_receipt_sha256, record_json,
                        recorded_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        entry["control_record_sha256"],
                        entry["project_id"],
                        entry["evidence_session_id"],
                        entry["host_session_id"],
                        entry["turn_id"],
                        entry["input_kind"],
                        entry["prompt_index"],
                        entry["prompt_record_sha256"],
                        entry["prior_control_record_sha256"],
                        entry["binding_sha256"],
                        entry["retrieval_receipt_sha256"],
                        json.dumps(entry, sort_keys=True, separators=(",", ":")),
                        entry["prepared_at"],
                    ),
                )
                connection.execute(
                    "INSERT INTO turn_entry_fts VALUES(?,?,?,?,?,?)",
                    (
                        entry["control_record_sha256"],
                        entry["project_id"],
                        entry["evidence_session_id"],
                        entry["turn_id"],
                        entry["input_kind"],
                        visible_input,
                    ),
                )
                action = "PREPARED_NOT_COMMITTED"
            research_receipt = _ensure_research_question(
                connection,
                entry=entry,
            )
            connection.commit()
        projection = _project_prepared_entry(
            root,
            project_root=project_root,
            entry=entry,
        )
        with _connection(project_root) as connection:
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            entry_count = int(
                connection.execute("SELECT COUNT(*) FROM turn_entry").fetchone()[0]
            )
            fts_count = int(
                connection.execute("SELECT COUNT(*) FROM turn_entry_fts").fetchone()[0]
            )
        _require(
            integrity == ["ok"] and entry_count == fts_count,
            "TURN_CONTROL_SQLITE_INVALID",
            "PREPARE SQLite integrity or input FTS parity failed.",
            integrity=integrity,
            entry_count=entry_count,
            fts_count=fts_count,
        )
    entry_source_snapshot = dict(
        entry.get("source_change_entry") or live_source_snapshot
    )
    persistent_change_display = _persistent_change_display(
        binding=entry["binding"],
        source_snapshot=live_source_snapshot,
        turn_state="PREPARED_NOT_COMMITTED",
        prompt_index=int(entry["prompt_index"]),
        uncommitted_count=1,
        changed_since_prepare=(
            entry_source_snapshot["worktree_sha256"]
            != live_source_snapshot["worktree_sha256"]
        ),
    )
    return {
        "state": action,
        "schema": "evidence-lane.codex-turn-control-receipt.v2",
        "prepare_state": "PREPARED_NOT_COMMITTED",
        "project_id": entry["project_id"],
        "evidence_session_id": entry["evidence_session_id"],
        "turn_id": entry["turn_id"],
        "input_kind": entry["input_kind"],
        "prompt_index": entry["prompt_index"],
        "prompt_record_sha256": entry["prompt_record_sha256"],
        "record_sha256": entry["prompt_record_sha256"],
        "control_record_sha256": entry["control_record_sha256"],
        "binding_sha256": entry["binding_sha256"],
        "retrieval_receipt_sha256": entry["retrieval_receipt_sha256"],
        "persistent_plan_row": entry["binding"]["persistent_plan_row"],
        "accepted_pv": entry["binding"]["accepted_pv"],
        "entry_pv": entry["binding"]["accepted_pv"],
        "pointer_generation": entry["binding"]["pointer_generation"],
        "attachment_identity_count": len(entry["attachment_identities"]),
        "research_question": research_receipt,
        "persistent_change_display": persistent_change_display,
        "scrollback_authority": False,
        "transcript_authority": False,
        "private_reasoning_stored": False,
        **projection,
    }


def _turn_entry(
    store_root: str | Path,
    *,
    host_session_id: str,
    turn_id: str,
    cwd: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    root = Path(store_root).resolve()
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=cwd,
    )
    current_binding = _binding_snapshot(root, bound)
    project_root = Path(bound["project_root"])
    with _connection(project_root) as connection:
        rows = connection.execute(
            """
            SELECT record_json FROM turn_entry
            WHERE host_session_id=? AND turn_id=?
            ORDER BY prompt_index DESC
            """,
            (host_session_id, turn_id),
        ).fetchall()
    _require(
        bool(rows),
        "TURN_CONTROL_PREFLIGHT_REQUIRED",
        "No sealed prompt, steer, or Goal preflight exists for this Codex turn.",
        turn_id=turn_id,
    )
    entry = json.loads(rows[0]["record_json"])
    claimed = str(entry.pop("control_record_sha256"))
    actual = sha256_bytes(canonical_json_bytes(entry))
    entry["control_record_sha256"] = claimed
    _require(
        claimed == actual,
        "TURN_CONTROL_RECORD_MISMATCH",
        "The Codex turn-control receipt failed its SHA-256 check.",
    )
    _require(
        entry.get("binding_sha256") == current_binding.get("binding_sha256"),
        "TURN_CONTROL_BINDING_DRIFT",
        "Project, pointer, Entry/Prepare, mode/operator, task, or Plan binding changed after preflight.",
    )
    return entry, current_binding, project_root


def _bounded_visible(value: Any) -> dict[str, Any]:
    safe = _turn_redact(value)
    text = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _require(
        not contains_secret(text),
        "TURN_CONTROL_TOOL_SECRET_REDACTION_FAILED",
        "A secret-like value remained in a visible operational event.",
    )
    encoded = text[:_MAX_VISIBLE_EVENT_CHARS]
    return {
        "visible_json_after_redaction": encoded,
        "visible_json_sha256_after_redaction": sha256_bytes(text.encode("utf-8")),
        "visible_json_chars_after_redaction": len(text),
        "truncated": len(text) > _MAX_VISIBLE_EVENT_CHARS,
    }


def record_tool_event(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    _require(
        phase in {"before", "after"},
        "TURN_CONTROL_TOOL_PHASE_INVALID",
        "Tool activity phase must be before or after.",
    )
    host_session_id = str(host_payload.get("session_id") or "").strip()
    turn_id = str(host_payload.get("turn_id") or "").strip()
    tool_name = str(host_payload.get("tool_name") or "").strip()
    tool_use_id = str(host_payload.get("tool_use_id") or "").strip()
    _require(
        host_session_id and turn_id and tool_name and tool_use_id,
        "TURN_CONTROL_TOOL_IDENTITY_REQUIRED",
        "A tool event requires host session, turn, tool name, and tool-use identities.",
    )
    entry, binding, project_root = _turn_entry(
        store_root,
        host_session_id=host_session_id,
        turn_id=turn_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    visible = _bounded_visible(
        host_payload.get("tool_input")
        if phase == "before"
        else host_payload.get("tool_response")
    )
    event_payload = {
        "turn_id": turn_id,
        "tool_use_id": tool_use_id,
        "phase": phase,
        "tool_name": tool_name,
        "control_record_sha256": entry["control_record_sha256"],
        "binding_sha256": binding["binding_sha256"],
        **visible,
        "private_reasoning_excluded": True,
    }
    tool_event_sha256 = sha256_bytes(canonical_json_bytes(event_payload))
    event_id = (
        "evt_"
        + sha256_bytes(
            (tool_use_id + "\0" + phase + "\0" + tool_event_sha256).encode("utf-8")
        )[:26].lower()
    )
    lineage_path = project_root / "lineage" / f"{binding['evidence_session_id']}.jsonl"
    lineage_event = ChatLineage(lineage_path).append(
        event_type=f"turn.tool.{phase}",
        visible_payload=event_payload,
        occurred_at=_now(),
        session_id=binding["evidence_session_id"],
        task_id=binding["task_id"],
        event_id=event_id,
        actor_type="tool",
    )
    with _connection(project_root) as connection:
        existing = connection.execute(
            "SELECT event_json FROM turn_tool_event WHERE tool_use_id=? AND phase=?",
            (tool_use_id, phase),
        ).fetchone()
        row = {
            "tool_event_sha256": tool_event_sha256,
            "control_record_sha256": entry["control_record_sha256"],
            "tool_use_id": tool_use_id,
            "phase": phase,
            "tool_name": tool_name,
            "lineage_event_sha256": lineage_event["event_sha256"],
            "event_payload": event_payload,
            "recorded_at": _now(),
        }
        if existing:
            stored = json.loads(existing["event_json"])
            _require(
                stored["tool_event_sha256"] == tool_event_sha256,
                "TURN_CONTROL_TOOL_EVENT_CONFLICT",
                "A tool-use identity already binds different visible activity.",
            )
            action = "RECORDED_IDEMPOTENT_REUSE"
        else:
            connection.execute(
                "INSERT INTO turn_tool_event VALUES(?,?,?,?,?,?,?,?)",
                (
                    tool_event_sha256,
                    entry["control_record_sha256"],
                    tool_use_id,
                    phase,
                    tool_name,
                    lineage_event["event_sha256"],
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                    row["recorded_at"],
                ),
            )
            connection.commit()
            action = "RECORDED"
    return {
        "state": action,
        "tool_event_sha256": tool_event_sha256,
        "lineage_event_sha256": lineage_event["event_sha256"],
        "control_record_sha256": entry["control_record_sha256"],
        "phase": phase,
    }


def _response_telemetry(host_payload: dict[str, Any]) -> dict[str, Any]:
    raw_usage = host_payload.get("usage") or host_payload.get("token_usage") or {}
    metrics = (
        {
            str(key): value
            for key, value in raw_usage.items()
            if str(key) in _TOKEN_METRIC_KEYS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        }
        if isinstance(raw_usage, dict)
        else {}
    )
    return {
        "model": _turn_redact_text(
            str(host_payload.get("model") or host_payload.get("model_name") or "")
        )
        or None,
        "submodel": _turn_redact_text(
            str(host_payload.get("submodel") or host_payload.get("model_slug") or "")
        )
        or None,
        "token_metrics": (
            {"availability": "AVAILABLE", **metrics}
            if metrics
            else {"availability": "UNAVAILABLE"}
        ),
    }


def _operational_links(host_payload: dict[str, Any]) -> dict[str, Any]:
    exact: dict[str, Any] = {}
    for key in (
        "tool_events",
        "tools",
        "tool_outputs",
        "commands",
        "files",
        "tests",
        "builds",
        "outputs",
        "links",
    ):
        if key in host_payload:
            exact[key] = _turn_redact(host_payload[key])
    serialized = json.dumps(
        exact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    _require(
        not contains_secret(serialized),
        "TURN_CONTROL_OPERATIONAL_LINK_REDACTION_FAILED",
        "A secret-like value remained in tool, command, file, test, build, or output links.",
    )
    return {
        "availability": "AVAILABLE" if exact else "UNAVAILABLE",
        "items_after_redaction": exact,
        "items_sha256_after_redaction": sha256_bytes(serialized.encode("utf-8")),
    }


def _response_links(visible_response: str) -> list[str]:
    values: list[str] = []
    for match in _LINK_RE.finditer(visible_response):
        value = _turn_redact_text((match.group(1) or match.group(0)).strip())
        if value and value not in values:
            values.append(value)
    return values[:100]


def commit_turn(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
) -> dict[str, Any]:
    """COMMIT one exact visible response against the latest PREPARE for this turn."""

    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    turn_id = str(host_payload.get("turn_id") or "").strip()
    _require(
        bool(host_session_id and turn_id),
        "TURN_CONTROL_HOST_TURN_IDENTITY_REQUIRED",
        "A governed COMMIT requires exact host-session and turn identities.",
    )
    visible_response = _turn_redact_text(
        str(host_payload.get("last_assistant_message") or "")
    )
    _require(
        bool(visible_response.strip()),
        "TURN_CONTROL_VISIBLE_RESPONSE_REQUIRED",
        "No visible assistant response is available; the turn remains PREPARED_NOT_COMMITTED.",
    )
    _require(
        not contains_secret(visible_response),
        "TURN_CONTROL_RESPONSE_REDACTION_FAILED",
        "A secret-like value remained in the visible response after redaction.",
    )
    policy = policy_state(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    _require(
        policy.get("governed_session") is True
        and policy.get("strict_required") is True,
        "TURN_CONTROL_POLICY_NOT_ACTIVE",
        "The exact governed session has not activated the sealed Mode plus Plan contract.",
        policy=policy,
    )
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    binding = _binding_snapshot(root, bound)
    project_root = Path(bound["project_root"])
    telemetry = _response_telemetry(host_payload)
    operational = _operational_links(host_payload)
    goal_usage = _goal_usage_observation(host_payload)
    live_source_snapshot = _source_change_snapshot(
        root,
        binding=binding,
        cwd=str(host_payload.get("cwd") or ""),
    )
    response_sha256 = sha256_bytes(visible_response.encode("utf-8"))
    with _control_lock(project_root):
        with _connection(project_root) as connection:
            rows = connection.execute(
                """
                SELECT e.record_json AS entry_json, c.commit_json AS commit_json
                FROM turn_entry e
                LEFT JOIN turn_commit c
                  ON c.control_record_sha256=e.control_record_sha256
                WHERE e.host_session_id=? AND e.turn_id=?
                ORDER BY e.prompt_index DESC
                """,
                (host_session_id, turn_id),
            ).fetchall()
        _require(
            bool(rows),
            "TURN_CONTROL_PREFLIGHT_REQUIRED",
            "No sealed PREPARE exists for this exact Codex turn.",
            turn_id=turn_id,
        )
        selected = rows[0]
        entry = json.loads(selected["entry_json"])
        claimed_entry_sha = str(entry.get("control_record_sha256") or "")
        entry_without_sha = {
            key: value for key, value in entry.items() if key != "control_record_sha256"
        }
        _require(
            claimed_entry_sha == sha256_bytes(canonical_json_bytes(entry_without_sha)),
            "TURN_CONTROL_RECORD_MISMATCH",
            "The PREPARE receipt failed its SHA-256 verification.",
        )
        _require(
            entry.get("binding_sha256") == binding.get("binding_sha256"),
            "TURN_CONTROL_BINDING_DRIFT",
            "Project, pointer, Entry, Mode, operator, task, or Plan binding changed after PREPARE.",
        )
        prepare_projection = _project_prepared_entry(
            root,
            project_root=project_root,
            entry=entry,
        )
        response_path = _projection_path(
            root,
            kind="response",
            host_session_id=host_session_id,
            prompt_index=int(entry["prompt_index"]),
            turn_id=turn_id,
        )
        if response_path.exists():
            response_record = _json(response_path)
            _require(
                response_record.get("response_sha256_after_redaction")
                == response_sha256
                and response_record.get("control_record_sha256")
                == entry["control_record_sha256"],
                "TURN_CONTROL_RESPONSE_PROJECTION_CONFLICT",
                "This PREPARE already binds a different visible response.",
                path=str(response_path),
            )
            claimed_response_record_sha256 = str(
                response_record.get("record_sha256") or ""
            )
            _require(
                claimed_response_record_sha256
                == sha256_bytes(
                    canonical_json_bytes(
                        {
                            key: value
                            for key, value in response_record.items()
                            if key != "record_sha256"
                        }
                    )
                ),
                "TURN_CONTROL_RESPONSE_RECORD_MISMATCH",
                "The response projection failed its SHA-256 verification.",
                path=str(response_path),
            )
            operational = dict(
                response_record.get("operational_links")
                or {
                    "availability": "UNAVAILABLE",
                    "items_after_redaction": {},
                    "items_sha256_after_redaction": sha256_bytes(b"{}"),
                }
            )
            telemetry = {
                "model": response_record.get("model"),
                "submodel": response_record.get("submodel"),
                "token_metrics": response_record.get("token_metrics")
                or {"availability": "UNAVAILABLE"},
            }
            stored_goal_usage = response_record.get("goal_usage")
            goal_usage = (
                dict(stored_goal_usage)
                if isinstance(stored_goal_usage, dict)
                else _goal_usage_observation({})
            )
            source_change = dict(
                response_record.get("source_change")
                or _source_change_receipt(
                    entry_snapshot=dict(
                        entry.get("source_change_entry") or live_source_snapshot
                    ),
                    exit_snapshot=live_source_snapshot,
                )
            )
            persistent_change_display = dict(
                response_record.get("persistent_change_display")
                or _persistent_change_display(
                    binding=binding,
                    source_snapshot=source_change["exit_source_change_snapshot"],
                    turn_state="COMMITTED",
                    prompt_index=int(entry["prompt_index"]),
                    uncommitted_count=0,
                    changed_since_prepare=bool(source_change["changed_since_prepare"]),
                )
            )
            claimed_goal_observation = str(goal_usage.get("observation_sha256") or "")
            _require(
                claimed_goal_observation
                == sha256_bytes(
                    canonical_json_bytes(
                        {
                            key: value
                            for key, value in goal_usage.items()
                            if key != "observation_sha256"
                        }
                    )
                ),
                "TURN_CONTROL_GOAL_USAGE_OBSERVATION_MISMATCH",
                "The stored Goal usage observation failed SHA-256 verification.",
            )
        else:
            source_change = _source_change_receipt(
                entry_snapshot=dict(
                    entry.get("source_change_entry") or live_source_snapshot
                ),
                exit_snapshot=live_source_snapshot,
            )
            persistent_change_display = _persistent_change_display(
                binding=binding,
                source_snapshot=live_source_snapshot,
                turn_state="COMMITTED",
                prompt_index=int(entry["prompt_index"]),
                uncommitted_count=0,
                changed_since_prepare=source_change["changed_since_prepare"],
            )
            response_record = {
                "schema": "evidence-lane.response-index.v2",
                "turn_id": turn_id,
                "prompt_index": entry["prompt_index"],
                "prompt_record_sha256": entry["prompt_record_sha256"],
                "control_record_sha256": entry["control_record_sha256"],
                "visible_assistant_response_after_redaction": visible_response,
                "response_sha256_after_redaction": response_sha256,
                "response_chars_after_redaction": len(visible_response),
                "output_links": _response_links(visible_response),
                "operational_links": operational,
                "goal_usage": goal_usage,
                "source_change": source_change,
                "persistent_change_display": persistent_change_display,
                **telemetry,
                "raw_response_stored": False,
                "redacted_visible_response_stored": True,
                "private_reasoning_stored": False,
                "project_id": binding["project_id"],
                "evidence_session_id": binding["evidence_session_id"],
                "entry_pv": binding["accepted_pv"],
                "pointer_generation": binding["pointer_generation"],
                "recorded_at": _now(),
                "hook_continuation_requested": False,
                "composer_mutated": False,
            }
            response_record["record_sha256"] = sha256_bytes(
                canonical_json_bytes(response_record)
            )
            atomic_write_json(response_path, response_record)
        response_record_sha256 = _sha(
            response_record.get("record_sha256"), field="response.record_sha256"
        )
        lineage_path = (
            project_root / "lineage" / f"{binding['evidence_session_id']}.jsonl"
        )
        lineage = ChatLineage(lineage_path)
        lineage_before_response = lineage.events()
        previous_head = (
            lineage_before_response[-1].get("event_sha256")
            if lineage_before_response
            else None
        )
        response_event_id = (
            "evt_"
            + sha256_bytes(
                (response_record_sha256 + "\0visible-response").encode("utf-8")
            )[:26].lower()
        )
        response_event = lineage.append(
            event_type="turn.visible_assistant_response",
            visible_payload={
                "turn_id": turn_id,
                "prompt_index": entry["prompt_index"],
                "prompt_record_sha256": entry["prompt_record_sha256"],
                "control_record_sha256": entry["control_record_sha256"],
                "response_record_sha256": response_record_sha256,
                "visible_assistant_response_after_redaction": visible_response,
                "response_sha256_after_redaction": response_sha256,
                "output_links": response_record["output_links"],
                "operational_links": operational,
                "source_change_receipt_sha256": source_change[
                    "source_change_receipt_sha256"
                ],
                "persistent_change_display_sha256": persistent_change_display[
                    "display_sha256"
                ],
                "private_reasoning_excluded": True,
                "hook_continuation_requested": False,
                "composer_mutated": False,
            },
            occurred_at=response_record["recorded_at"],
            session_id=binding["evidence_session_id"],
            task_id=binding["task_id"],
            event_id=response_event_id,
            actor_type="assistant",
            model=telemetry["model"],
            submodel=telemetry["submodel"],
            token_metrics=telemetry["token_metrics"],
        )
        previous_head = response_event.get("previous_event_sha256")
        exit_state = {
            "control_record_sha256": entry["control_record_sha256"],
            "response_record_sha256": response_record_sha256,
            "binding_sha256": binding["binding_sha256"],
            "prepare_lineage_event_sha256": prepare_projection[
                "prepare_lineage_event_sha256"
            ],
            "response_lineage_event_sha256": response_event["event_sha256"],
            "prior_lineage_head_sha256": previous_head,
            "accepted_pv": binding["accepted_pv"],
            "pointer_generation": binding["pointer_generation"],
            "goal_usage_observation_sha256": goal_usage["observation_sha256"],
            "source_change_receipt_sha256": source_change[
                "source_change_receipt_sha256"
            ],
            "persistent_change_display_sha256": persistent_change_display[
                "display_sha256"
            ],
        }
        state_sha256 = sha256_bytes(canonical_json_bytes(exit_state))
        commit = {
            "schema": "evidence-lane.codex-turn-commit.v2",
            "state": "COMMITTED",
            "control_record_sha256": entry["control_record_sha256"],
            "response_record_sha256": response_record_sha256,
            "turn_id": turn_id,
            "prompt_index": entry["prompt_index"],
            "project_id": binding["project_id"],
            "evidence_session_id": binding["evidence_session_id"],
            "accepted_pv": binding["accepted_pv"],
            "pointer_generation": binding["pointer_generation"],
            "entry_slip": entry["entry_slip"],
            "exit_slip": {
                **exit_state,
                "state_sha256": state_sha256,
                "operational_links_sha256": operational["items_sha256_after_redaction"],
                "token_metrics": telemetry["token_metrics"],
                "goal_usage": goal_usage,
                "source_change": source_change,
                "persistent_change_display": persistent_change_display,
            },
            "state_sha256": state_sha256,
            "private_reasoning_stored": False,
            "committed_at": response_record["recorded_at"],
        }
        commit["commit_sha256"] = sha256_bytes(canonical_json_bytes(commit))
        commit_event_id = (
            "evt_"
            + sha256_bytes((commit["commit_sha256"] + "\0commit").encode("utf-8"))[
                :26
            ].lower()
        )
        commit_event = lineage.append(
            event_type="turn.control_commit",
            visible_payload=commit,
            occurred_at=commit["committed_at"],
            session_id=binding["evidence_session_id"],
            task_id=binding["task_id"],
            event_id=commit_event_id,
            actor_type="system",
            model=telemetry["model"],
            submodel=telemetry["submodel"],
            token_metrics=telemetry["token_metrics"],
        )
        with _connection(project_root) as connection:
            existing = connection.execute(
                "SELECT commit_json FROM turn_commit WHERE control_record_sha256=?",
                (entry["control_record_sha256"],),
            ).fetchone()
            if existing:
                stored = json.loads(existing["commit_json"])
                _require(
                    stored == commit,
                    "TURN_CONTROL_RESPONSE_COMMIT_CONFLICT",
                    "The PREPARE already binds a different COMMIT.",
                )
                action = "COMMITTED_IDEMPOTENT_REUSE"
            else:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO turn_commit VALUES(?,?,?,?,?,?)",
                    (
                        commit["commit_sha256"],
                        entry["control_record_sha256"],
                        response_record_sha256,
                        commit_event["event_sha256"],
                        json.dumps(commit, sort_keys=True, separators=(",", ":")),
                        commit["committed_at"],
                    ),
                )
                connection.execute(
                    "INSERT INTO turn_commit_fts VALUES(?,?,?,?,?,?)",
                    (
                        commit["commit_sha256"],
                        entry["control_record_sha256"],
                        turn_id,
                        binding["project_id"],
                        visible_response,
                        json.dumps(
                            operational["items_after_redaction"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                action = "COMMITTED"
            goal_usage_receipt = _ensure_goal_usage(
                connection,
                entry=entry,
                observation=goal_usage,
                recorded_at=response_record["recorded_at"],
            )
            connection.commit()
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            commit_count = int(
                connection.execute("SELECT COUNT(*) FROM turn_commit").fetchone()[0]
            )
            fts_count = int(
                connection.execute("SELECT COUNT(*) FROM turn_commit_fts").fetchone()[0]
            )
        _require(
            integrity == ["ok"] and commit_count == fts_count,
            "TURN_CONTROL_SQLITE_INVALID",
            "COMMIT SQLite integrity or response FTS parity failed.",
            integrity=integrity,
            commit_count=commit_count,
            fts_count=fts_count,
        )
        lineage_projection = lineage.projection_status()
    return {
        "state": action,
        "schema": "evidence-lane.codex-turn-control-commit-receipt.v2",
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "turn_id": turn_id,
        "prompt_index": entry["prompt_index"],
        "control_record_sha256": entry["control_record_sha256"],
        "response_record_sha256": response_record_sha256,
        "commit_sha256": commit["commit_sha256"],
        "state_sha256": state_sha256,
        "response_lineage_event_sha256": response_event["event_sha256"],
        "commit_lineage_event_sha256": commit_event["event_sha256"],
        "lineage_projection": lineage_projection,
        "response_projection_path": str(response_path),
        "operational_links": operational,
        "token_metrics": telemetry["token_metrics"],
        "goal_usage": goal_usage_receipt,
        "source_change": source_change,
        "persistent_change_display": persistent_change_display,
        "private_reasoning_stored": False,
    }


def session_start_control(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind one strict host session and visibly recover every uncommitted PREPARE."""

    started_ns = time.perf_counter_ns()
    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    _require(
        bool(host_session_id),
        "TURN_CONTROL_HOST_SESSION_ID_REQUIRED",
        "SessionStart requires the exact governed host-session identity.",
    )
    policy = policy_state(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    _require(
        policy.get("governed_session") is True
        and policy.get("strict_required") is True,
        "TURN_CONTROL_POLICY_NOT_ACTIVE",
        "The exact governed session has not activated strict Mode plus Plan turn control.",
        policy=policy,
    )
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    binding = _binding_snapshot(root, bound)
    project_root = Path(bound["project_root"])
    live_source_snapshot = _source_change_snapshot(
        root,
        binding=binding,
        cwd=str(host_payload.get("cwd") or ""),
    )
    recovered: list[dict[str, Any]] = []
    with _control_lock(project_root):
        with _connection(project_root) as connection:
            rows = connection.execute(
                """
                SELECT e.record_json
                FROM turn_entry e
                LEFT JOIN turn_commit c
                  ON c.control_record_sha256=e.control_record_sha256
                WHERE e.project_id=? AND e.evidence_session_id=?
                  AND c.commit_sha256 IS NULL
                ORDER BY e.prompt_index
                """,
                (binding["project_id"], binding["evidence_session_id"]),
            ).fetchall()
            latest_rows = connection.execute(
                """
                SELECT e.record_json AS entry_json, c.commit_json AS commit_json
                FROM turn_entry e
                LEFT JOIN turn_commit c
                  ON c.control_record_sha256=e.control_record_sha256
                WHERE e.project_id=? AND e.evidence_session_id=?
                ORDER BY e.prompt_index DESC
                """,
                (
                    binding["project_id"],
                    binding["evidence_session_id"],
                ),
            ).fetchall()
            latest_row = next(
                (
                    row
                    for row in latest_rows
                    if json.loads(row["entry_json"]).get("task_id")
                    == binding["task_id"]
                ),
                None,
            )
        lineage_path = (
            project_root / "lineage" / f"{binding['evidence_session_id']}.jsonl"
        )
        lineage = ChatLineage(lineage_path)
        for row in rows:
            entry = json.loads(row["record_json"])
            claimed = str(entry.get("control_record_sha256") or "")
            _require(
                claimed
                == sha256_bytes(
                    canonical_json_bytes(
                        {
                            key: value
                            for key, value in entry.items()
                            if key != "control_record_sha256"
                        }
                    )
                ),
                "TURN_CONTROL_RECORD_MISMATCH",
                "An uncommitted PREPARE failed its SHA-256 verification.",
            )
            _require(
                entry.get("binding_sha256") == binding.get("binding_sha256"),
                "TURN_CONTROL_BINDING_DRIFT",
                "An uncommitted PREPARE no longer matches the exact project, pointer, Mode, or Plan binding.",
                control_record_sha256=claimed,
            )
            prepare_projection = _project_prepared_entry(
                root,
                project_root=project_root,
                entry=entry,
            )
            recovery_event_id = (
                "evt_"
                + sha256_bytes(
                    (claimed + "\0prepared-not-committed-recovery").encode("utf-8")
                )[:26].lower()
            )
            recovery_event = lineage.append(
                event_type="turn.control_recovery",
                visible_payload={
                    "state": "PREPARED_NOT_COMMITTED",
                    "recovery_action": "VISIBLE_REPLAY_SAFE_HOLD",
                    "turn_id": entry["turn_id"],
                    "input_kind": entry["input_kind"],
                    "prompt_index": entry["prompt_index"],
                    "control_record_sha256": claimed,
                    "prepare_lineage_event_sha256": prepare_projection[
                        "prepare_lineage_event_sha256"
                    ],
                    "source_mutation_authorized": False,
                    "automatic_commit_inferred": False,
                    "private_reasoning_excluded": True,
                },
                occurred_at=entry["prepared_at"],
                session_id=binding["evidence_session_id"],
                task_id=binding["task_id"],
                event_id=recovery_event_id,
                actor_type="system",
            )
            recovered.append(
                {
                    "turn_id": entry["turn_id"],
                    "input_kind": entry["input_kind"],
                    "prompt_index": entry["prompt_index"],
                    "control_record_sha256": claimed,
                    "recovery_lineage_event_sha256": recovery_event["event_sha256"],
                    "state": "PREPARED_NOT_COMMITTED",
                }
            )
        projection = lineage.projection_status()
    latest_entry = (
        json.loads(latest_row["entry_json"]) if latest_row is not None else None
    )
    latest_commit = (
        json.loads(latest_row["commit_json"])
        if latest_row is not None and latest_row["commit_json"]
        else None
    )
    entry_source_snapshot = (
        dict(latest_entry.get("source_change_entry") or live_source_snapshot)
        if latest_entry is not None
        else live_source_snapshot
    )
    warm_attach_receipt = _warm_attach_receipt(
        root,
        bound=bound,
        binding=binding,
        host_session_id=host_session_id,
        started_ns=started_ns,
    )
    persistent_change_display = _persistent_change_display(
        binding=binding,
        source_snapshot=live_source_snapshot,
        turn_state=(
            "PREPARED_NOT_COMMITTED"
            if latest_entry is not None and latest_commit is None
            else "COMMITTED"
            if latest_commit is not None
            else "NO_RECORDED_TURN"
        ),
        prompt_index=(
            int(latest_entry["prompt_index"]) if latest_entry is not None else None
        ),
        uncommitted_count=len(recovered),
        changed_since_prepare=(
            entry_source_snapshot["worktree_sha256"]
            != live_source_snapshot["worktree_sha256"]
            if latest_entry is not None
            else None
        ),
        warm_attach_receipt=warm_attach_receipt,
    )
    return {
        "state": (
            "RECOVERED_PREPARED_NOT_COMMITTED"
            if recovered
            else "BOUND_NO_UNCOMMITTED_TURNS"
        ),
        "schema": "evidence-lane.codex-session-turn-control.v1",
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "host_session_id_sha256": sha256_bytes(host_session_id.encode("utf-8")),
        "binding_sha256": binding["binding_sha256"],
        "accepted_pv": binding["accepted_pv"],
        "pointer_generation": binding["pointer_generation"],
        "persistent_plan_row": binding["persistent_plan_row"],
        "uncommitted_count": len(recovered),
        "uncommitted": recovered,
        "lineage_projection": projection,
        "warm_attach_receipt": warm_attach_receipt,
        "persistent_change_display": persistent_change_display,
        "scrollback_authority": False,
        "transcript_authority": False,
        "private_reasoning_stored": False,
    }


def project_task_research_status(
    store_root: str | Path,
    *,
    host_session_id: str,
    cwd: str,
) -> dict[str, Any]:
    """Read only the exact bound project's current task research/usage ledger."""

    root = Path(store_root).resolve()
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id.strip(),
        cwd=cwd,
    )
    binding = _binding_snapshot(root, bound)
    database = Path(bound["project_root"]) / "lineage" / "codex_turn_control.sqlite"
    with _read_only(database) as connection:
        research_rows = connection.execute(
            """
            SELECT record_json FROM turn_research_question
            WHERE project_id=? AND evidence_session_id=? AND task_id=?
            ORDER BY prompt_index
            """,
            (
                binding["project_id"],
                binding["evidence_session_id"],
                binding["task_id"],
            ),
        ).fetchall()
        usage_rows = connection.execute(
            """
            SELECT record_json FROM turn_goal_usage
            WHERE project_id=? AND evidence_session_id=? AND task_id=?
            ORDER BY prompt_index
            """,
            (
                binding["project_id"],
                binding["evidence_session_id"],
                binding["task_id"],
            ),
        ).fetchall()
        leaked_research_fts = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='turn_research_question_fts'
            """
        ).fetchall()
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]

    def verified_records(
        rows: list[sqlite3.Row],
        *,
        sha_field: str,
        mismatch_code: str,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for row in rows:
            record = json.loads(row["record_json"])
            claimed = str(record.get(sha_field) or "")
            actual = sha256_bytes(
                canonical_json_bytes(
                    {key: value for key, value in record.items() if key != sha_field}
                )
            )
            _require(
                claimed == actual
                and record.get("project_id") == binding["project_id"]
                and record.get("evidence_session_id") == binding["evidence_session_id"]
                and record.get("task_id") == binding["task_id"],
                mismatch_code,
                "A project-task research ledger record failed identity or SHA-256 verification.",
            )
            records.append(record)
        return records

    research = verified_records(
        research_rows,
        sha_field="research_record_sha256",
        mismatch_code="TURN_CONTROL_RESEARCH_RECORD_MISMATCH",
    )
    usage = verified_records(
        usage_rows,
        sha_field="usage_record_sha256",
        mismatch_code="TURN_CONTROL_GOAL_USAGE_RECORD_MISMATCH",
    )
    _require(
        integrity == ["ok"] and not leaked_research_fts,
        "TURN_CONTROL_RESEARCH_SQLITE_INVALID",
        "The project-task research ledger failed integrity or private-index isolation.",
        integrity=integrity,
        leaked_research_fts=[row["name"] for row in leaked_research_fts],
    )
    final_counters: dict[str, dict[str, Any]] = {}
    for record in usage:
        observation = record.get("observation") or {}
        if (
            observation.get("availability") == "AVAILABLE"
            and observation.get("metric_semantics") == "GOAL_FINAL_COUNTER"
        ):
            final_counters[str(observation["goal_id"])] = {
                "goal_accounted_tokens": observation["goal_accounted_tokens"],
                "observation_sha256": observation["observation_sha256"],
                "usage_record_sha256": record["usage_record_sha256"],
            }
    return {
        "status": "PASS",
        "schema": "evidence-lane.project-task-private-research-status.v1",
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "task_id": binding["task_id"],
        "plan_task_id": binding["plan_task_id"],
        "access_scope": _PROJECT_TASK_PRIVATE_ANALYSIS,
        "research_focus": binding["research_policy"]["focus"],
        "research_questions": research,
        "goal_usage_records": usage,
        "final_goal_counters_by_goal_id": final_counters,
        "cumulative_snapshots_summed": False,
        "cross_project_retrieval": False,
        "shared_global_telemetry": False,
        "shared_fts_indexed": False,
        "public_output_included": False,
        "private_reasoning_stored": False,
        "sqlite_integrity": integrity,
    }
