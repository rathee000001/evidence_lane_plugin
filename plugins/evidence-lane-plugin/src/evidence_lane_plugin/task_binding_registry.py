"""Shared exact-task bindings separated from immutable release authority."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .constants import (
    GOVERNED_SKILL_COUNT,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from .errors import require
from .hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .public_surface_registry import derive_public_surface_registry
from .store import ProjectStore
from .timeutil import utc_now

_TASK_ID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid task-binding authority: {path}") from exc
    require(
        isinstance(value, dict),
        "SHARED_TASK_BINDING_JSON_INVALID",
        "A task-binding authority is not one JSON object.",
        status="MISMATCH",
        path=str(path),
    )
    return value


def _sealed(payload: Mapping[str, Any], field: str) -> bool:
    claimed = str(payload.get(field) or "").strip().upper()
    body = {key: value for key, value in payload.items() if key != field}
    return claimed == sha256_bytes(canonical_json_bytes(body))


def _surface_core(surface: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: surface.get(key)
        for key in (
            "schema",
            "plugin_version",
            "hooks",
            "skills",
            "catalog",
            "raw_paths_included",
        )
    }


def _validate_surface(
    surface: Mapping[str, Any],
    *,
    expected_catalog: Mapping[str, Any] | None = None,
) -> None:
    exact_catalog = dict(expected_catalog) if expected_catalog is not None else {
        "tools": NATIVE_TOOL_COUNT,
        "read": NATIVE_READ_TOOL_COUNT,
        "write": NATIVE_WRITE_TOOL_COUNT,
        "skills": GOVERNED_SKILL_COUNT,
    }
    derived_surface_counts = dict(derive_public_surface_registry()["catalog"])
    expected_surface_counts = {**derived_surface_counts, **exact_catalog}
    require(
        surface.get("schema")
        == "evidence-lane.codex-installed-surface-inventory.v2"
        and surface.get("catalog") == exact_catalog
        and surface.get("raw_paths_included") is False
        and surface.get("surface_counts") == expected_surface_counts
        and surface.get("release_catalog_matches_derived") is True
        and len(str(surface.get("public_surface_registry_sha256") or "")) == 64
        and surface.get("surface_inventory_sha256")
        == sha256_bytes(canonical_json_bytes(_surface_core(surface)))
        and len(str(surface.get("release_policy_sha256") or "")) == 64,
        "SHARED_RELEASE_SURFACE_INVALID",
        "The running release surface is incomplete or self-hash mismatched.",
        status="MISMATCH",
    )


def _active_rebind_authority(
    session: Mapping[str, Any],
    *,
    project_id: str,
    evidence_session_id: str,
    task_id: str,
    expected_active_plan_task_id: str,
    expected_runtime_task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = dict(session.get("metadata") or {})
    rebind = dict(metadata.get("active_contract_rebind_receipt") or {})
    task_binding = dict(rebind.get("task_binding_contract") or {})
    history_matches = [
        dict(value)
        for value in metadata.get("active_contract_rebinds") or []
        if isinstance(value, dict)
        and value.get("receipt_sha256") == rebind.get("receipt_sha256")
    ]
    authority_route = str(rebind.get("authority_route") or "")
    require(
        session.get("project_id") == project_id
        and session.get("session_id") == evidence_session_id
        and metadata.get("current_host_session_id") == task_id
        and rebind.get("schema")
        == "evidence-lane.active-contract-session-rebind.v1"
        and rebind.get("status") == "PASS"
        and rebind.get("project_id") == project_id
        and rebind.get("session_id") == evidence_session_id
        and rebind.get("host_task_id") == task_id
        and rebind.get("active_plan_task_id") == expected_active_plan_task_id
        and rebind.get("runtime_task_id") == expected_runtime_task_id
        and _sealed(rebind, "receipt_sha256")
        and len(history_matches) == 1
        and history_matches[0] == rebind
        and rebind.get("runtime_task_identity_preserved") is True
        and rebind.get("active_plan_row_identity_preserved") is True
        and rebind.get("governed_session_identity_preserved") is True
        and rebind.get("host_task_identity_preserved") is True
        and authority_route
        in {
            "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK",
            "PV_PLAN_TASKS_ACTIVE_CONTRACT_REBIND",
        }
        and (
            authority_route != "PV_PLAN_TASKS_ACTIVE_CONTRACT_REBIND"
            or re.fullmatch(
                r"^[A-F0-9]{64}$",
                str(rebind.get("approval_receipt_sha256") or "").upper(),
            )
            is not None
        )
        and task_binding.get("manager_scope") == "SHARED_MULTI_PROJECT_MULTI_TASK"
        and task_binding.get("registry_mutability") == "MUTABLE_APPEND_OR_REFRESH"
        and task_binding.get("invocation_binding_scope") == "EXACT_CALLING_TASK"
        and task_binding.get("reentry_target") == task_id
        and task_binding.get("installer_helper") == "SEPARATE_COMPONENT"
        and rebind.get("candidate_created") is False
        and (
            (
                authority_route == "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK"
                and bool(rebind.get("pending_hil"))
                == bool(metadata.get("pending_hil"))
                and rebind.get("candidate_id_preserved")
                == session.get("candidate_id")
                and rebind.get("candidate_state_preserved") == session.get("state")
            )
            or (
                authority_route == "PV_PLAN_TASKS_ACTIVE_CONTRACT_REBIND"
                and rebind.get("pending_hil") is False
            )
        )
        and rebind.get("pointer_moved") is False
        and rebind.get("goal_completion_mutated") is False
        and rebind.get("git_executed") is False
        and rebind.get("install_executed") is False
        and rebind.get("helper_launched") is False
        and rebind.get("tunnel_launched") is False,
        "SHARED_TASK_BINDING_REBIND_AUTHORITY_MISMATCH",
        "The current session lacks the exact calling-task binding authority.",
        status="MISMATCH",
    )
    return metadata, rebind


def seal_running_release_authority(
    root: str | Path,
    *,
    surface: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal one immutable running release/surface authority by content hash."""

    exact_root = Path(root).resolve()
    _validate_surface(surface)
    plugin_root = Path(__file__).resolve().parents[2]
    manifest = plugin_root / ".codex-plugin" / "plugin.json"
    require(
        manifest.is_file(),
        "SHARED_RELEASE_MANIFEST_REQUIRED",
        "The running plugin manifest is unavailable.",
        status="MISMATCH",
    )
    release_core = {
        "schema": "evidence-lane.running-release-authority.v1",
        "authority_kind": "RUNNING_PLUGIN_SURFACE",
        "plugin_id": "evidence-lane-plugin",
        "plugin_version": surface.get("plugin_version"),
        "plugin_manifest_sha256": sha256_file(manifest),
        "surface_inventory_sha256": surface.get("surface_inventory_sha256"),
        "release_policy_sha256": surface.get("release_policy_sha256"),
        "catalog": surface.get("catalog"),
        "surface_counts": surface.get("surface_counts"),
        "public_surface_registry_sha256": surface.get(
            "public_surface_registry_sha256"
        ),
        "one_enabled_channel_required": True,
        "channel_activation_mutated": False,
        "installer_helper_invoked": False,
        "source_mutated": False,
        "git_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    authority_id = "release_" + sha256_bytes(
        canonical_json_bytes(release_core)
    )[:40].lower()
    body = {**release_core, "authority_id": authority_id}
    receipt = {
        **body,
        "release_authority_sha256": sha256_bytes(canonical_json_bytes(body)),
    }
    path = exact_root / "release-authorities" / f"{authority_id}.json"
    if path.is_file():
        require(
            _json(path) == receipt,
            "SHARED_RELEASE_AUTHORITY_CONFLICT",
            "The immutable running release ID already contains different bytes.",
            status="MISMATCH",
            authority_id=authority_id,
        )
    else:
        atomic_write_json(path, receipt)
    return {**receipt, "authority_path": str(path)}


def seal_or_refresh_shared_task_binding(
    root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    task_id: str,
    surface: Mapping[str, Any],
    bound_by: str,
) -> dict[str, Any]:
    """Create or refresh one exact calling-task row in the shared registry."""

    exact_root = Path(root).resolve()
    exact_task_id = str(task_id or "").strip()
    require(
        _TASK_ID_RE.fullmatch(exact_task_id) is not None,
        "SHARED_TASK_BINDING_TASK_ID_INVALID",
        "The shared task registry requires one exact Codex task UUID.",
        status="MISMATCH",
    )
    store = ProjectStore(exact_root)
    session_path = (
        store.project_root(project_id)
        / "sessions"
        / f"{evidence_session_id}.json"
    )
    require(
        session_path.is_file(),
        "SHARED_TASK_BINDING_SESSION_REQUIRED",
        "The governed session is unavailable for exact task binding.",
        status="MISMATCH",
    )
    session = _json(session_path)
    metadata = dict(session.get("metadata") or {})
    backlog = store.backlog_status(project_id)
    active = [
        dict(row)
        for row in backlog.get("active") or []
        if isinstance(row, dict)
    ]
    runtime_task_id = str(dict(session.get("task") or {}).get("task_id") or "")
    require(
        len(active) == 1
        and bool(runtime_task_id)
        and metadata.get("active_backlog_task_id") == active[0].get("task_id")
        and metadata.get("active_backlog_task_status") == "ACTIVE",
        "SHARED_TASK_BINDING_ACTIVE_PLAN_MISMATCH",
        "The task binding cannot refresh without one exact active Plan row.",
        status="MISMATCH",
        active_task_ids=[str(row.get("task_id") or "") for row in active],
        runtime_task_id=runtime_task_id,
        session_active_backlog_task_id=str(
            metadata.get("active_backlog_task_id") or ""
        ),
        session_active_backlog_task_status=str(
            metadata.get("active_backlog_task_status") or ""
        ),
    )
    metadata, rebind = _active_rebind_authority(
        session,
        project_id=project_id,
        evidence_session_id=evidence_session_id,
        task_id=exact_task_id,
        expected_active_plan_task_id=str(active[0]["task_id"]),
        expected_runtime_task_id=runtime_task_id,
    )
    pointer = store.pointer(project_id)
    release = seal_running_release_authority(exact_root, surface=surface)
    registry_root = exact_root / "task-binding-registry"
    current_path = registry_root / f"{exact_task_id.lower()}.json"
    prior = _json(current_path) if current_path.is_file() else None
    stable_fields = {
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "task_id": exact_task_id,
        "task_uri_sha256": sha256_bytes(
            f"codex://threads/{exact_task_id}".encode()
        ),
        "governed_host_session_id": exact_task_id,
        "active_plan_task_id": active[0]["task_id"],
        "runtime_task_id": runtime_task_id,
        "accepted_pv": pointer.accepted_pv,
        "pointer_generation": pointer.generation,
        "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
        "release_authority_id": release["authority_id"],
        "release_authority_sha256": release["release_authority_sha256"],
        "active_contract_rebind_receipt_sha256": rebind["receipt_sha256"],
        "approval_receipt_sha256": rebind.get("approval_receipt_sha256"),
        "binding_epoch_sha256": sha256_bytes(
            canonical_json_bytes(
                {
                    "project_id": project_id,
                    "session_id": evidence_session_id,
                    "task_id": exact_task_id,
                    "active_plan_task_id": active[0]["task_id"],
                    "runtime_task_id": runtime_task_id,
                    "pointer_generation": pointer.generation,
                    "active_contract_rebind_receipt_sha256": rebind[
                        "receipt_sha256"
                    ],
                    "release_authority_sha256": release[
                        "release_authority_sha256"
                    ],
                }
            )
        ),
    }
    if isinstance(prior, dict):
        require(
            prior.get("schema") == "evidence-lane.shared-task-binding.v1"
            and _sealed(prior, "task_binding_receipt_sha256"),
            "SHARED_TASK_BINDING_CURRENT_INVALID",
            "The mutable task-binding registry row is not a valid sealed receipt.",
            status="MISMATCH",
        )
        if all(prior.get(key) == value for key, value in stable_fields.items()):
            return {
                **prior,
                "idempotent_reuse": True,
                "current_path": str(current_path),
                "release_authority": release,
            }
        history_path = (
            registry_root
            / "history"
            / exact_task_id.lower()
            / f"{prior['task_binding_receipt_sha256']}.json"
        )
        if history_path.is_file():
            require(
                _json(history_path) == prior,
                "SHARED_TASK_BINDING_HISTORY_CONFLICT",
                "The immutable prior task-binding revision conflicts.",
                status="MISMATCH",
            )
        else:
            atomic_write_json(history_path, prior)
    revision = int((prior or {}).get("revision") or 0) + 1
    body = {
        "schema": "evidence-lane.shared-task-binding.v1",
        "state": "EXACT_TASK_BINDING_ACTIVE",
        "manager_scope": "SHARED_MULTI_PROJECT_MULTI_TASK",
        "registry_mutability": "MUTABLE_APPEND_OR_REFRESH",
        "invocation_binding_scope": "EXACT_CALLING_TASK",
        "reentry_target": exact_task_id,
        "installer_helper": "SEPARATE_COMPONENT",
        **stable_fields,
        "revision": revision,
        "prior_task_binding_receipt_sha256": (
            prior.get("task_binding_receipt_sha256")
            if isinstance(prior, dict)
            else None
        ),
        "bound_by": str(bound_by or "native-route"),
        "refreshed_at": utc_now(),
        "source_mutated": False,
        "git_mutated": False,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "install_executed": False,
        "helper_launched": False,
        "tunnel_launched": False,
    }
    receipt = {
        **body,
        "task_binding_receipt_sha256": sha256_bytes(
            canonical_json_bytes(body)
        ),
    }
    atomic_write_json(current_path, receipt)
    return {
        **receipt,
        "idempotent_reuse": False,
        "current_path": str(current_path),
        "release_authority": release,
    }


def read_shared_task_binding(
    root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    task_id: str,
    expected_active_plan_task_id: str,
    surface: Mapping[str, Any],
    expected_surface_catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Read and verify one current exact-task row and its immutable release."""

    exact_root = Path(root).resolve()
    exact_task_id = str(task_id or "").strip()
    if _TASK_ID_RE.fullmatch(exact_task_id) is None:
        return None
    path = exact_root / "task-binding-registry" / f"{exact_task_id.lower()}.json"
    if not path.is_file():
        return None
    binding = _json(path)
    store = ProjectStore(exact_root)
    session_path = (
        store.project_root(project_id)
        / "sessions"
        / f"{evidence_session_id}.json"
    )
    require(
        session_path.is_file(),
        "SHARED_TASK_BINDING_SESSION_REQUIRED",
        "The governed session is unavailable for exact task binding.",
        status="MISMATCH",
    )
    session = _json(session_path)
    active = [
        dict(row)
        for row in store.backlog_status(project_id).get("active") or []
        if isinstance(row, dict)
    ]
    pointer = store.pointer(project_id)
    runtime_task_id = str(dict(session.get("task") or {}).get("task_id") or "")
    require(
        len(active) == 1
        and active[0].get("task_id") == expected_active_plan_task_id
        and bool(runtime_task_id),
        "SHARED_TASK_BINDING_ACTIVE_PLAN_MISMATCH",
        "The shared task binding cannot resolve one current Plan/runtime task.",
        status="MISMATCH",
    )
    metadata, rebind = _active_rebind_authority(
        session,
        project_id=project_id,
        evidence_session_id=evidence_session_id,
        task_id=exact_task_id,
        expected_active_plan_task_id=expected_active_plan_task_id,
        expected_runtime_task_id=runtime_task_id,
    )
    require(
        binding.get("schema") == "evidence-lane.shared-task-binding.v1"
        and binding.get("state") == "EXACT_TASK_BINDING_ACTIVE"
        and _sealed(binding, "task_binding_receipt_sha256")
        and binding.get("project_id") == project_id
        and binding.get("evidence_session_id") == evidence_session_id
        and binding.get("task_id") == exact_task_id
        and binding.get("governed_host_session_id") == exact_task_id
        and binding.get("active_plan_task_id") == expected_active_plan_task_id
        and len(active) == 1
        and active[0].get("task_id") == expected_active_plan_task_id
        and metadata.get("active_backlog_task_id") == expected_active_plan_task_id
        and metadata.get("active_backlog_task_status") == "ACTIVE"
        and binding.get("runtime_task_id") == runtime_task_id
        and binding.get("accepted_pv") == pointer.accepted_pv
        and binding.get("pointer_generation") == pointer.generation
        and binding.get("accepted_manifest_sha256")
        == pointer.accepted_manifest_sha256
        and binding.get("active_contract_rebind_receipt_sha256")
        == rebind.get("receipt_sha256")
        and binding.get("approval_receipt_sha256")
        == rebind.get("approval_receipt_sha256")
        and binding.get("manager_scope") == "SHARED_MULTI_PROJECT_MULTI_TASK"
        and binding.get("registry_mutability") == "MUTABLE_APPEND_OR_REFRESH"
        and binding.get("invocation_binding_scope") == "EXACT_CALLING_TASK"
        and binding.get("reentry_target") == exact_task_id
        and binding.get("installer_helper") == "SEPARATE_COMPONENT"
        and binding.get("source_mutated") is False
        and binding.get("candidate_created") is False
        and binding.get("pending_hil") is False
        and binding.get("pointer_moved") is False
        and binding.get("hil_inferred") is False,
        "SHARED_TASK_BINDING_MISMATCH",
        "The shared exact-task row is stale, cross-task, or promoting.",
        status="MISMATCH",
    )
    release_id = str(binding.get("release_authority_id") or "")
    release_path = exact_root / "release-authorities" / f"{release_id}.json"
    require(
        release_path.is_file(),
        "SHARED_TASK_BINDING_RELEASE_REQUIRED",
        "The task row's immutable release authority is missing.",
        status="MISMATCH",
    )
    release = _json(release_path)
    _validate_surface(surface, expected_catalog=expected_surface_catalog)
    require(
        release.get("schema") == "evidence-lane.running-release-authority.v1"
        and release.get("authority_id") == release_id
        and _sealed(release, "release_authority_sha256")
        and release.get("release_authority_sha256")
        == binding.get("release_authority_sha256")
        and release.get("plugin_version") == surface.get("plugin_version")
        and release.get("surface_inventory_sha256")
        == surface.get("surface_inventory_sha256")
        and release.get("release_policy_sha256")
        == surface.get("release_policy_sha256")
        and release.get("catalog") == surface.get("catalog"),
        "SHARED_TASK_BINDING_RELEASE_MISMATCH",
        "The exact task row does not bind the current running release surface.",
        status="MISMATCH",
    )
    return {
        **binding,
        "current_path": str(path),
        "release_authority": release,
        "release_authority_path": str(release_path),
    }
