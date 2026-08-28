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
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, cast

from .canon_runtime_continuity import (
    seal_host_exit_continuity_packet,
    seal_observed_experience_packet,
)
from .constants import (
    ENGINE_VERSION,
    GOVERNED_SKILL_COUNT,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from .conversation_memory import resolve_conversation_memory
from .errors import EvidenceLaneError
from .git_adapter import (
    calculate_worktree_change_identity,
    calculate_worktree_sha256,
    inspect_repository,
    run_git,
)
from .goal_usage import (
    TOKEN_COMPONENT_KEYS,
    build_component_token_accounting,
    build_profile_observed_usage_context,
    build_reset_aware_epoch_accounting,
)
from .hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .hook_contract import (
    HOOK_EVENT_NAMES,
    lifecycle_hook_contract,
    validate_hook_configuration,
)
from .host_plan_rehydration import prepare_host_plan_rehydration
from .install_deferral import adaptive_install_deferral_facts
from .lineage import ChatLineage
from .package_root import resolve_plugin_root
from .project_authority import resolved_chat_lineage_root, resolved_plan_backlog_path
from .project_memory import (
    rehydrate_memory_checkpoint,
    seal_memory_checkpoint,
)
from .prompt_index import PromptIndex
from .public_surface_registry import derive_public_surface_registry
from .redaction import contains_secret, redact_text
from .store import ProjectStore
from .task_binding_registry import (
    read_shared_task_binding,
    seal_or_refresh_shared_task_binding,
)

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_CODEX_TASK_ID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_DELTA_VERIFICATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,160}$")
_DELTA_VERIFICATION_ROLES = {
    "DOCUMENTATION",
    "METADATA",
    "RECEIPT",
    "SOURCE",
    "TEST",
}
_SUPPORTED_EXACT_TASK_BINDING_ACTIVATION_STATES = frozenset(
    {
        "INSTALLED_RESTART_REQUIRED",
    }
)
_GOVERNED_ACTIVITY_TOOL_LIMIT = 2048
_GOVERNED_ACTIVITY_CLASS_ORDER = (
    ("plan_pv", "Plan and PV"),
    ("chat_lineage", "ChatLineage"),
    ("canon", "Canon"),
    ("learning", "AI Learning"),
    ("memory", "Memory"),
    ("lifecycle", "Lifecycle"),
    ("git_sync", "Git sync"),
    ("vercel_sync_deployment", "Vercel sync and deployment"),
    ("render_panels", "Render panels"),
    ("tests", "Tests"),
    ("installation", "Installation evidence"),
    ("other_governed", "Other governed activity"),
)
_GOVERNED_ACTIVITY_SOURCE_ORDER = (
    "Evidence Lane",
    "GitHub",
    "Vercel",
    "Render",
    "Codex host",
)
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)|https?://[^\s)>]+")
_TURN_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:authorization|password|token|secret|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*[^\s,;]+"
)
_TURN_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_ACTIVE_PLAN_STATUSES = {"ACTIVE", "IN_PROGRESS"}
_MAX_VISIBLE_EVENT_CHARS = 12_000
_COMPACT_REENTRY_CONTEXT_BYTE_CEILING = 8_192
_COMPACT_SESSION_SOURCES = {
    "auto-compact",
    "compact",
    "compaction",
    "manual-compact",
    "post-compact",
    "postcompact",
}
_MAX_PERSISTENT_CHANGE_PATHS = 200
_TOKEN_METRIC_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
    "main_agent_tokens",
    "agent_tokens",
    "subagent_tokens",
}
_GOAL_USAGE_SEMANTICS = {
    "GOAL_FINAL_COUNTER",
    "GOAL_CUMULATIVE_SNAPSHOT",
    "TURN_DELTA",
}
_LIFECYCLE_EXIT_REASONS = {
    "HIL_WAIT",
    "EXPLICIT_PAUSE",
    "GENUINE_BLOCK",
    "GOVERNED_ERROR",
    "EXIT_BOOT",
    "STATE_TRAVEL_HANDOFF",
    "STATELESS_EPHEMERAL_END",
}
_NATIVE_TASK_GOAL_CONTINUATION_ORIGIN = "NATIVE_ACTIVE_GOAL_EXACT_TASK_BINDING"
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


def resolve_codex_hook_store_root(
    *,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve the governed local authority used by every Codex hook.

    Codex injects ``PLUGIN_DATA`` as installation-scoped private storage for
    each plugin selector, so that host value must never become Evidence Lane
    control or project authority. Hidden plugin control state uses
    ``EVIDENCE_LANE_RUNTIME_CONTROL_ROOT``. Each project then binds its separate
    user-selected Project/PV authority root through ``project_register``.
    """

    values = os.environ if environment is None else environment
    configured_root = values.get("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT")
    if configured_root is not None:
        configured_root = str(configured_root).strip()
        if not configured_root:
            raise TurnControlError(
                "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT_INVALID",
                "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT cannot be empty when configured.",
            )
        return Path(configured_root).expanduser().resolve()
    durable_home = (home or Path.home()).expanduser().resolve()
    return (
        durable_home / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
    ).resolve()


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


def _lineage_host_identity(host_payload: dict[str, Any]) -> dict[str, Any]:
    """Return a privacy-safe host identity for visible ChatLineage events."""

    runtime_context_value = host_payload.get("runtime_context")
    runtime_context: dict[str, Any] = (
        cast(dict[str, Any], runtime_context_value)
        if isinstance(runtime_context_value, dict)
        else {}
    )
    host_session_id = str(host_payload.get("session_id") or "").strip()
    identity = {
        "host_kind": _turn_redact_text(
            str(
                host_payload.get("host_kind")
                or host_payload.get("host")
                or runtime_context.get("host_kind")
                or "CODEX"
            )
        ),
        "host_profile": _turn_redact_text(
            str(
                host_payload.get("host_profile")
                or runtime_context.get("host_profile")
                or "UNAVAILABLE"
            )
        ),
        "host_app": _turn_redact_text(
            str(
                host_payload.get("host_app")
                or host_payload.get("app")
                or runtime_context.get("host_app")
                or "UNAVAILABLE"
            )
        ),
        "host_session_id_sha256": (
            sha256_bytes(host_session_id.encode("utf-8")) if host_session_id else None
        ),
        "raw_host_session_id_stored": False,
    }
    _require(
        not contains_secret(identity),
        "TURN_CONTROL_HOST_IDENTITY_REDACTION_FAILED",
        "A secret-like value remained in the privacy-safe host identity.",
    )
    return identity


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


def _project_authority_root(root: Path, project_id: str) -> Path:
    """Resolve one project through the registered live-authority route."""

    try:
        return ProjectStore(root).project_root(project_id)
    except EvidenceLaneError as exc:
        raise TurnControlError(
            exc.code,
            exc.message,
            **dict(exc.details),
        ) from exc


def _project_authority_routes(root: Path) -> list[tuple[str, Path]]:
    """Resolve host discovery across registered and legacy project routes."""

    try:
        return ProjectStore(root).project_authority_routes()
    except EvidenceLaneError as exc:
        raise TurnControlError(
            exc.code,
            exc.message,
            **dict(exc.details),
        ) from exc


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
    plugin_root = resolve_plugin_root(__file__)
    public_surface = derive_public_surface_registry(plugin_root)
    _require(
        public_surface["status"] == "PASS",
        "TURN_CONTROL_PUBLIC_SURFACE_REGISTRY_MISMATCH",
        "The installed package surface registry and release claims diverged.",
    )
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        plugin_root / "hooks" / "logical-actions.json",
        *sorted((plugin_root / "hooks").glob("*.exe")),
        *sorted((plugin_root / "hooks").glob("*.py")),
        *sorted((plugin_root / "hooks").glob("*.ps1")),
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

    hook_files = inventory(hook_paths, skill=False)
    hook_configuration = _json(plugin_root / "hooks" / "hooks.json")
    hook_contract_validation = validate_hook_configuration(hook_configuration)
    hook_events = dict(hook_configuration.get("hooks") or {})
    registered_events = sorted(hook_events)
    event_action_inventory: list[dict[str, Any]] = []
    for event_ordinal, event_name in enumerate(HOOK_EVENT_NAMES, start=1):
        actions: list[dict[str, Any]] = []
        for group_ordinal, group in enumerate(
            list(hook_events.get(event_name) or []), start=1
        ):
            for group_action_ordinal, handler in enumerate(
                list(group.get("hooks") or []), start=1
            ):
                action_ordinal = len(actions) + 1
                command_identity = str(
                    handler.get("commandWindows") or handler.get("command") or ""
                )
                actions.append(
                    {
                        "action_number": f"{event_ordinal}.{action_ordinal}",
                        "event_action_ordinal": action_ordinal,
                        "group_ordinal": group_ordinal,
                        "group_action_ordinal": group_action_ordinal,
                        "type": handler.get("type"),
                        "command_sha256": sha256_bytes(
                            command_identity.encode("utf-8")
                        ),
                        "raw_command_returned": False,
                    }
                )
        event_action_inventory.append(
            {
                "hook_number": event_ordinal,
                "event_name": event_name,
                "display_number": f"Hook {event_ordinal}",
                "action_count": len(actions),
                "actions": actions,
            }
        )
    handler_count = sum(
        int(row["action_count"]) for row in event_action_inventory
    )
    _require(
        registered_events == sorted(HOOK_EVENT_NAMES)
        and all(row["action_count"] >= 1 for row in event_action_inventory),
        "TURN_CONTROL_PACKAGE_HOOK_EVENT_INVENTORY_REQUIRED",
        "The installed persistent hook event inventory is not exact.",
    )
    hook_inventory = {
        "count": len(registered_events),
        "count_semantics": "REGISTERED_EVENT_COUNT",
        "registered_event_count": len(registered_events),
        "registered_events": registered_events,
        "handler_count": handler_count,
        "handler_count_semantics": "TOTAL_NESTED_HANDLER_ACTION_COUNT",
        "event_order": list(HOOK_EVENT_NAMES),
        "event_action_inventory": event_action_inventory,
        "event_action_inventory_sha256": sha256_bytes(
            canonical_json_bytes(event_action_inventory)
        ),
        "hook_file_count": hook_files["count"],
        "records": hook_files["records"],
        "file_inventory_sha256": hook_files["inventory_sha256"],
        "event_inventory_sha256": sha256_bytes(canonical_json_bytes(registered_events)),
        "lifecycle_contract": lifecycle_hook_contract(),
        "configuration_validation": hook_contract_validation,
    }
    hook_inventory["inventory_sha256"] = sha256_bytes(
        canonical_json_bytes(hook_inventory)
    )
    manifest = _json(plugin_root / ".codex-plugin" / "plugin.json")
    release_path = plugin_root / "scripts" / "codex-release-channel.json"
    catalog = {
        key: public_surface["catalog"][key]
        for key in ("tools", "read", "write", "skills")
    }
    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": str(manifest.get("version") or ""),
        "hooks": hook_inventory,
        "skills": inventory(skill_paths, skill=True),
        "catalog": catalog,
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return {
        **core,
        "surface_counts": dict(public_surface["catalog"]),
        "providers": public_surface["providers"],
        "public_surface_registry_sha256": public_surface["registry_sha256"],
        "release_catalog_matches_derived": public_surface[
            "release_catalog_matches_derived"
        ],
        "release_policy_sha256": sha256_file(release_path),
        "tunnel_channel": _json(release_path).get("stable", {}).get(
            "tunnel_channel"
        ),
    }


def package_surface_inventory() -> dict[str, Any]:
    """Return the sealed installed Codex surface inventory for native receipts."""

    return _package_surface_inventory()


def _powershell_ordered_json_sha256(value: dict[str, Any]) -> str:
    """Match ConvertTo-Json -Compress for the bounded ASCII recovery registry."""

    return sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    )


def _package_update_status(root: Path) -> dict[str, Any]:
    current = _package_surface_inventory()
    receipt_path = root / "installations" / "codex-v200" / "CURRENT_INSTALLATION.json"
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
                candidate.get("schema") == "evidence-lane.codex-stable-installation.v2"
                and candidate.get("status") == "PASS"
                and candidate_change.get("schema")
                == "evidence-lane.codex-installed-surface-change-display.v2"
                and candidate_change.get("current_surface_inventory_sha256")
                == current["surface_inventory_sha256"]
                and candidate_change.get("raw_paths_included") is False
                and candidate_change.get("private_research_question_included") is False
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
            "count_semantics": "REGISTERED_EVENT_COUNT",
            "registered_event_count": current["hooks"]["registered_event_count"],
            "registered_events": current["hooks"]["registered_events"],
            "handler_count": current["hooks"]["handler_count"],
            "hook_file_count": current["hooks"]["hook_file_count"],
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


def _host_binding_epoch(session: dict[str, Any]) -> str:
    """Seal the exact governed host binding without storing its raw identity."""

    metadata = dict(session.get("metadata") or {})
    direct_entry = dict(metadata.get("direct_forced_same_worktree_entry") or {})
    governed_host_session_id = str(
        metadata.get("current_host_session_id") or ""
    ).strip()
    core = {
        "schema": "evidence-lane.codex-host-binding-epoch.v1",
        "project_id": session.get("project_id"),
        "evidence_session_id": session.get("session_id"),
        "governed_host_session_id_sha256": sha256_bytes(
            governed_host_session_id.encode("utf-8")
        ),
        "accepted_pv": session.get("accepted_pv"),
        "accepted_pointer_generation": session.get("accepted_pointer_generation"),
        "active_backlog_task_id": metadata.get("active_backlog_task_id"),
        "active_backlog_task_status": metadata.get("active_backlog_task_status"),
        "runtime_task_id": (
            dict(session.get("task") or {}).get("task_id")
            if isinstance(session.get("task"), dict)
            else None
        ),
        "direct_entry_receipt_sha256": direct_entry.get("receipt_sha256"),
        "direct_destination_task_id_sha256": (
            sha256_bytes(str(direct_entry.get("destination_task_id") or "").encode())
            if direct_entry.get("destination_task_id")
            else None
        ),
    }
    return sha256_bytes(canonical_json_bytes(core))


def _read_codex_task_binding(
    root: Path,
    *,
    observed_host_session_id: str,
) -> dict[str, Any] | None:
    """Verify one installer-prepared exact Codex thread binding.

    A Codex task can intentionally use a task-shell workspace that is outside the
    governed repository.  In that case repository CWD is not a valid discovery
    signal.  The stable restart helper therefore prepares one receipt that binds
    the exact Codex thread UUID to the already-governed project/session before a
    restart.  This reader accepts only the current installed package and never
    searches by task title, CWD, or another active project.
    """

    task_id = str(observed_host_session_id or "").strip()
    if not _CODEX_TASK_ID_RE.fullmatch(task_id):
        return None
    installation_root = root / "installations" / "codex-v200"
    path = installation_root / "task-bindings" / f"{task_id.lower()}.json"
    if not path.is_file():
        return None
    binding = _json(path)
    _require(
        binding.get("schema") == "evidence-lane.codex-task-binding.v1"
        and binding.get("state") == "EXACT_TASK_BINDING_PREPARED"
        and binding.get("task_id") == task_id
        and binding.get("alias_claim_allowed") is True
        and binding.get("claim_scope") == "EXACT_CODEX_THREAD_ID_ONLY"
        and binding.get("source_mutated") is False
        and binding.get("candidate_created_or_accepted") is False
        and binding.get("pointer_moved") is False
        and binding.get("hil_inferred") is False,
        "TURN_CONTROL_CODEX_TASK_BINDING_INVALID",
        "The exact Codex task binding receipt is invalid.",
        task_id=task_id,
    )
    expected_task_uri_sha256 = sha256_bytes(f"codex://threads/{task_id}".encode())
    _require(
        binding.get("task_uri_sha256") == expected_task_uri_sha256,
        "TURN_CONTROL_CODEX_TASK_URI_MISMATCH",
        "The exact Codex task binding does not match its deeplink identity.",
    )
    preparation_path = Path(str(binding.get("preparation_receipt") or ""))
    install_path = Path(str(binding.get("install_receipt") or ""))
    _require(
        preparation_path.is_absolute()
        and install_path.is_absolute()
        and _within(preparation_path, installation_root)
        and _within(install_path, installation_root)
        and preparation_path.is_file()
        and install_path.is_file(),
        "TURN_CONTROL_CODEX_TASK_BINDING_AUTHORITY_REQUIRED",
        "The exact Codex task binding authorities are missing or out of scope.",
    )
    preparation_sha256 = _sha(
        binding.get("preparation_receipt_sha256"),
        field="task_binding.preparation_receipt_sha256",
    )
    install_sha256 = _sha(
        binding.get("install_receipt_sha256"),
        field="task_binding.install_receipt_sha256",
    )
    _require(
        sha256_file(preparation_path) == preparation_sha256
        and sha256_file(install_path) == install_sha256,
        "TURN_CONTROL_CODEX_TASK_BINDING_SEAL_MISMATCH",
        "The exact Codex task binding authority seal does not match.",
    )
    preparation = _json(preparation_path)
    installation = _json(install_path)
    current_installation_path = installation_root / "CURRENT_INSTALLATION.json"
    _require(
        current_installation_path.is_file()
        and sha256_file(current_installation_path) == install_sha256,
        "TURN_CONTROL_CODEX_TASK_BINDING_INSTALLATION_STALE",
        "The exact Codex task binding does not reference the current stable installation.",
    )
    plugin_manifest = _json(
        resolve_plugin_root(__file__) / ".codex-plugin" / "plugin.json"
    )
    plugin_version = str(plugin_manifest.get("version") or "")
    _require(
        preparation.get("schema") == "evidence-lane.codex-restart-preparation.v2"
        and preparation.get("state") == "PREPARED_NOT_RESTARTED"
        and preparation.get("project_id") == binding.get("project_id")
        and preparation.get("evidence_session_id") == binding.get("evidence_session_id")
        and preparation.get("task_id") == task_id
        and preparation.get("host_session_id")
        == binding.get("governed_host_session_id")
        and preparation.get("install_receipt_sha256") == install_sha256
        and preparation.get("plugin_version") == plugin_version
        and installation.get("schema") == "evidence-lane.codex-stable-installation.v2"
        and installation.get("status") == "PASS"
        and dict(installation.get("plugin") or {}).get("version") == plugin_version
        and binding.get("plugin_version") == plugin_version,
        "TURN_CONTROL_CODEX_TASK_BINDING_DRIFT",
        "The exact Codex task, preparation, and current stable installation do not agree.",
    )
    return {
        **binding,
        "task_binding_receipt_sha256": sha256_file(path),
        "preparation_receipt_sha256": preparation_sha256,
        "install_receipt_sha256": install_sha256,
    }


def _derive_active_task_goal_binding(
    root: Path,
    *,
    host_session_id: str,
    turn_binding: dict[str, Any],
    host_payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind native Goal continuation to the exact task, Plan row, and runtime.

    No user helper, scheduled task, synthetic prompt, transcript, or raw Goal
    objective participates. The host must provide the native active-Goal flag
    on the first PreToolUse boundary; otherwise the route fails closed.
    """

    task_id = str(host_session_id or "").strip()
    _require(
        _CODEX_TASK_ID_RE.fullmatch(task_id) is not None,
        "TURN_CONTROL_TASK_GOAL_ID_REQUIRED",
        "Goal continuation requires one exact Codex task UUID.",
    )
    _require(
        host_payload.get("host_goal_active") is True,
        "TURN_CONTROL_NATIVE_ACTIVE_GOAL_PROOF_REQUIRED",
        "The first non-prompt tool boundary has no native active-Goal proof.",
    )
    task_binding = _read_codex_task_binding(
        root,
        observed_host_session_id=task_id,
    )
    _require(
        isinstance(task_binding, dict),
        "TURN_CONTROL_TASK_GOAL_BINDING_REQUIRED",
        "The active Goal has no current exact-task installation binding.",
    )
    task_binding = cast(dict[str, Any], task_binding)
    active_plan = dict(turn_binding.get("persistent_plan_row") or {})
    task_uri_sha256 = sha256_bytes(f"codex://threads/{task_id}".encode())
    plugin_version = str(_package_surface_inventory().get("plugin_version") or "")
    _require(
        task_binding.get("project_id") == turn_binding.get("project_id")
        and task_binding.get("evidence_session_id")
        == turn_binding.get("evidence_session_id")
        and task_binding.get("task_id") == task_id
        and task_binding.get("governed_host_session_id") == task_id
        and task_binding.get("task_uri_sha256") == task_uri_sha256
        and task_binding.get("plugin_version") == plugin_version
        and active_plan.get("task_id") == turn_binding.get("plan_task_id")
        and active_plan.get("status") == "in_progress"
        and active_plan.get("lifecycle_status") == "ACTIVE",
        "TURN_CONTROL_TASK_GOAL_IDENTITY_MISMATCH",
        "Native Goal, task, project, installed package, or active Plan identity drifted.",
    )
    core = {
        "schema": "evidence-lane.codex-task-goal-continuation-authority.v1",
        "state": "NATIVE_ACTIVE_TASK_GOAL_BINDING_VERIFIED",
        "input_origin": _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN,
        "project_id": turn_binding["project_id"],
        "evidence_session_id": turn_binding["evidence_session_id"],
        "task_id": task_id,
        "active_plan_task_id": turn_binding["plan_task_id"],
        "plugin_version": plugin_version,
        "task_binding_receipt_sha256": task_binding[
            "task_binding_receipt_sha256"
        ],
        "task_uri_sha256": task_uri_sha256,
        "accepted_pv": turn_binding["accepted_pv"],
        "pointer_generation": turn_binding["pointer_generation"],
        "native_goal_status_source": "PRETOOLUSE_HOST_PAYLOAD",
        "host_goal_active": True,
        "raw_goal_objective_stored": False,
        "synthetic_prompt_used": False,
        "user_prompt_submit_observed": False,
        "turn_start_invoked": False,
        "thread_resume_invoked": False,
        "state_travel_invoked": False,
        "candidate_hil_pointer_or_git_mutated": False,
        "scrollback_used": False,
        "transcript_used": False,
        "private_reasoning_stored": False,
    }
    return {
        **core,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(core)),
    }



def _session_candidates(
    root: Path,
    *,
    host_session_id: str,
    cwd: str,
    transcript_path: str = "",
) -> list[dict[str, Any]]:
    project_routes = _project_authority_routes(root)
    if not project_routes:
        return []
    exact: list[dict[str, Any]] = []
    cwd_matches: list[dict[str, Any]] = []
    current_cwd = Path(cwd).resolve() if cwd else None
    for _, project_root in project_routes:
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
    transcript_path: str = "",
) -> dict[str, Any]:
    """Return whether this host turn is governed and strict-control eligible."""

    root = Path(store_root).resolve()
    candidates = _session_candidates(
        root,
        host_session_id=host_session_id.strip(),
        cwd=cwd,
        transcript_path=transcript_path,
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
    transcript_path: str = "",
) -> dict[str, Any]:
    candidates = _session_candidates(
        root,
        host_session_id=host_session_id,
        cwd=cwd,
        transcript_path=transcript_path,
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


def _prepare_bound_host_plan_rehydration(
    root: Path,
    *,
    bound: dict[str, Any],
    host_payload: dict[str, Any],
    trigger: str,
    trigger_event_id: str,
) -> dict[str, Any] | None:
    """Prepare a skill-owned host Plan request without executing host behavior."""

    session = cast(dict[str, Any], bound["session"])
    metadata = cast(dict[str, Any], session.get("metadata") or {})
    host_task_id = str(metadata.get("current_host_session_id") or "").strip()
    if not host_task_id:
        return None
    observation_value = host_payload.get("host_plan_artifact_observation")
    observation = (
        cast(dict[str, Any], observation_value)
        if isinstance(observation_value, dict)
        else None
    )
    try:
        return prepare_host_plan_rehydration(
            root,
            project_id=str(session["project_id"]),
            evidence_session_id=str(session["session_id"]),
            host_task_id=host_task_id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            host_capability=str(
                host_payload.get("host_plan_capability_status") or "SUPPORTED"
            ),
            observed_artifact=observation,
            host_goal_active=(
                bool(host_payload["host_goal_active"])
                if "host_goal_active" in host_payload
                else None
            ),
        )
    except EvidenceLaneError as exc:
        raise TurnControlError(
            exc.code,
            exc.message,
            **dict(exc.details),
        ) from exc


def bind_codex_host_payload(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
    event_name: str,
) -> tuple[dict[str, Any], None]:
    """Accept only the exact native governed task identity.

    Historical host-alias claims, transcript-derived rebinding, CWD rebinding,
    and installer-prepared identity substitution are intentionally absent.
    State Travel and native task attachment must establish the exact session id
    before any hook may enter strict turn control.
    """

    del event_name
    root = Path(store_root).resolve()
    normalized = dict(host_payload)
    observed_host_session_id = str(normalized.get("session_id") or "").strip()
    if not observed_host_session_id:
        return normalized, None
    candidates = _session_candidates(
        root,
        host_session_id=observed_host_session_id,
        cwd=str(normalized.get("cwd") or ""),
    )
    if (
        len(candidates) == 1
        and candidates[0].get("binding_match") == "EXACT_HOST_SESSION"
    ):
        return normalized, None
    return normalized, None


def _sha(value: Any, *, field: str) -> str:
    exact = str(value or "").upper()
    _require(
        bool(_SHA256_RE.fullmatch(exact)),
        "TURN_CONTROL_RECEIPT_SHA256_REQUIRED",
        "A required Entry, Prepare, mode, operator, or Plan receipt is missing.",
        field=field,
    )
    return exact


def _verified_direct_entry_authority(
    session: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Verify a direct-entry receipt as exact task-entry authority."""

    metadata = dict(session.get("metadata") or {})
    raw = metadata.get("direct_forced_same_worktree_entry")
    if not isinstance(raw, dict):
        return None
    receipt = dict(raw)
    receipt_body = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    host_binding = dict(receipt.get("host_task_binding") or {})
    destination = dict(host_binding.get("destination") or {})
    plan = dict(receipt.get("plan_task_proof") or {})
    runtime = dict(receipt.get("runtime_plugin_profile_proof") or {})
    attestation = dict(runtime.get("runtime_instance_attestation") or {})
    attestation_body = {
        key: value for key, value in attestation.items() if key != "receipt_sha256"
    }
    no_mutation = dict(receipt.get("no_mutation_flags") or {})
    preserved_candidate = dict(receipt.get("preserved_candidate_hil_state") or {})
    sealed_transport = dict(receipt.get("sealed_transport") or {})
    destination_task_id = str(destination.get("task_id") or "").strip()
    destination_deep_link = f"codex://threads/{destination_task_id}"
    receipt_sha256 = str(receipt.get("receipt_sha256") or "").strip().upper()
    attestation_sha256 = str(attestation.get("receipt_sha256") or "").strip().upper()
    _require(
        receipt.get("schema")
        in {
            "evidence-lane.direct-forced-same-worktree-entry-receipt.v1",
            "evidence-lane.direct-forced-same-worktree-entry-receipt.v2",
        }
        and receipt.get("status") == "PASS"
        and receipt.get("route") == "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK"
        and receipt.get("project_id") == session.get("project_id")
        and receipt.get("session_id") == session.get("session_id")
        and bool(_SHA256_RE.fullmatch(receipt_sha256))
        and receipt_sha256 == sha256_bytes(canonical_json_bytes(receipt_body))
        and _CODEX_TASK_ID_RE.fullmatch(destination_task_id) is not None
        and destination.get("deep_link") == destination_deep_link
        and destination.get("project_id") == session.get("project_id")
        and destination.get("fresh_local_task") is True
        and destination.get("fork") is False
        and destination.get("continued_from_chat") is False
        and metadata.get("current_host_session_id") == destination_task_id
        and bool(str(plan.get("active_task_id") or "").strip())
        and len(str(plan.get("identity_sha256") or "")) == 64
        and attestation.get("schema")
        == "evidence-lane.server-runtime-instance-attestation.v1"
        and attestation.get("status") == "PASS"
        and attestation.get("identity_source") == "SERVER_INTERNAL_PROCESS_LIFETIME"
        and attestation.get("caller_supplied") is False
        and attestation.get("process_id_exposed") is False
        and bool(_SHA256_RE.fullmatch(attestation_sha256))
        and attestation_sha256
        == sha256_bytes(canonical_json_bytes(attestation_body))
        and "host_process_instance_id" not in runtime
        and sealed_transport.get("prepare_called") is False
        and sealed_transport.get("resume_called") is False
        and sealed_transport.get("transport_envelope_created") is False
        and sealed_transport.get("transport_envelope_consumed") is False
        and no_mutation.get("source_mutated") is False
        and no_mutation.get("candidate_created") is False
        and (
            receipt.get("schema")
            == "evidence-lane.direct-forced-same-worktree-entry-receipt.v1"
            or (
                no_mutation.get("candidate_cleared") is False
                and no_mutation.get("candidate_rebuilt") is False
                and preserved_candidate.get("candidate_id")
                == session.get("candidate_id")
                and preserved_candidate.get("lifecycle_state")
                == session.get("state")
                and bool(preserved_candidate.get("pending_hil"))
                == bool(metadata.get("pending_hil"))
            )
        )
        and no_mutation.get("hil_inferred") is False
        and no_mutation.get("pointer_moved") is False
        and no_mutation.get("sealed_prepare_called") is False
        and no_mutation.get("sealed_resume_called") is False
        and no_mutation.get("git_executed") is False
        and no_mutation.get("install_executed") is False,
        "TURN_CONTROL_DIRECT_ENTRY_AUTHORITY_MISMATCH",
        "The stored direct State Travel receipt is not the exact sealed destination authority.",
    )
    expected_pending_hil = bool(
        preserved_candidate.get("pending_hil")
        if receipt.get("schema")
        == "evidence-lane.direct-forced-same-worktree-entry-receipt.v2"
        else False
    )

    def sealed_rebind(value: Mapping[str, Any]) -> bool:
        exact = dict(value)
        digest = str(exact.get("receipt_sha256") or "").strip().upper()
        body = {key: item for key, item in exact.items() if key != "receipt_sha256"}
        return bool(
            _SHA256_RE.fullmatch(digest)
            and digest == sha256_bytes(canonical_json_bytes(body))
        )

    current_active_task_id = str(metadata.get("active_backlog_task_id") or "")
    current_runtime_task_id = str(
        dict(session.get("task") or {}).get("task_id") or ""
    )
    current_rebind_raw = metadata.get("active_contract_rebind_receipt")
    if isinstance(current_rebind_raw, dict):
        current_rebind = dict(current_rebind_raw)
        current_task_binding = dict(
            current_rebind.get("task_binding_contract") or {}
        )
        current_direct = dict(current_rebind.get("direct_entry_authority") or {})
        direct_route = (
            current_rebind.get("authority_route")
            == "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK"
        )
        approval_route = (
            current_rebind.get("authority_route")
            == "PV_PLAN_TASKS_ACTIVE_CONTRACT_REBIND"
            and _SHA256_RE.fullmatch(
                str(current_rebind.get("approval_receipt_sha256") or "").upper()
            )
            is not None
        )
        _require(
            sealed_rebind(current_rebind)
            and current_rebind.get("schema")
            == "evidence-lane.active-contract-session-rebind.v1"
            and current_rebind.get("status") == "PASS"
            and current_rebind.get("project_id") == session.get("project_id")
            and current_rebind.get("session_id") == session.get("session_id")
            and current_rebind.get("host_task_id") == destination_task_id
            and current_rebind.get("active_plan_task_id")
            == current_active_task_id
            and current_rebind.get("runtime_task_id") == current_runtime_task_id
            and current_rebind.get("runtime_task_identity_preserved") is True
            and current_rebind.get("active_plan_row_identity_preserved") is True
            and current_rebind.get("governed_session_identity_preserved") is True
            and current_rebind.get("host_task_identity_preserved") is True
            and current_task_binding.get("manager_scope")
            == "SHARED_MULTI_PROJECT_MULTI_TASK"
            and current_task_binding.get("registry_mutability")
            == "MUTABLE_APPEND_OR_REFRESH"
            and current_task_binding.get("invocation_binding_scope")
            == "EXACT_CALLING_TASK"
            and current_task_binding.get("reentry_target") == destination_task_id
            and current_task_binding.get("installer_helper") == "SEPARATE_COMPONENT"
            and current_rebind.get("candidate_created") is False
            and current_rebind.get("candidate_id_preserved")
            == session.get("candidate_id")
            and current_rebind.get("candidate_state_preserved")
            == session.get("state")
            and bool(current_rebind.get("pending_hil")) == expected_pending_hil
            and current_rebind.get("pointer_moved") is False
            and current_rebind.get("goal_completion_mutated") is False
            and current_rebind.get("git_executed") is False
            and current_rebind.get("install_executed") is False
            and current_rebind.get("helper_launched") is False
            and current_rebind.get("tunnel_launched") is False
            and (direct_route or approval_route)
            and (
                not direct_route
                or (
                    current_direct.get(
                        "runtime_instance_attestation_receipt_sha256"
                    )
                    == attestation_sha256
                    and current_direct.get("runtime_instance_attestation_mode")
                    == "SERVER_DERIVED_ATTESTATION"
                    and current_direct.get("caller_supplied_runtime_identity")
                    is False
                )
            ),
            "TURN_CONTROL_DIRECT_ENTRY_TASK_BINDING_AUTHORITY_MISMATCH",
            "The current task-binding receipt is stale, cross-task, unsealed, or detached from the live Plan/runtime task.",
        )
        task_binding_receipt_sha256 = str(current_rebind["receipt_sha256"])
        task_binding_source = (
            "CURRENT_DIRECT_ENTRY_TASK_BINDING_RECEIPT"
            if direct_route
            else "CURRENT_USER_APPROVED_CONTRACT_REBIND_RECEIPT"
        )
    else:
        _require(
            current_active_task_id == plan.get("active_task_id"),
            "TURN_CONTROL_DIRECT_ENTRY_TASK_BINDING_AUTHORITY_MISMATCH",
            "The direct-entry receipt may stand alone only while its original Plan row remains active.",
        )
        task_binding_receipt_sha256 = receipt_sha256
        task_binding_source = "DIRECT_ENTRY_RECEIPT"
    return {
        "receipt": receipt,
        "receipt_sha256": receipt_sha256,
        "destination_task_id": destination_task_id,
        "destination_deep_link": destination_deep_link,
        "plan": plan,
        "runtime_instance_attestation_receipt_sha256": attestation_sha256,
        "task_binding_authority_receipt_sha256": task_binding_receipt_sha256,
        "task_binding_authority_source": task_binding_source,
    }


def _direct_entry_checkpoint_continuity(
    *,
    project_root: Path,
    project: Mapping[str, Any],
    session: Mapping[str, Any],
    binding: Mapping[str, Any],
    direct_entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebind a historical direct entry to the current pointer and worktree."""

    receipt = dict(direct_entry.get("receipt") or {})
    pointer_baseline = dict(receipt.get("accepted_pointer_baseline") or {})
    pointer_payload = _json(project_root / "active_pointer.json")
    pointer = {
        key: pointer_payload.get(key)
        for key in (
            "project_id",
            "accepted_pv",
            "accepted_manifest_sha256",
            "generation",
            "updated_at",
            "prior_generation",
        )
    }
    pointer_sha256 = sha256_bytes(canonical_json_bytes(pointer))
    metadata = dict(session.get("metadata") or {})
    candidate_boundary = dict(binding.get("candidate_boundary") or {})
    preserved_candidate = dict(receipt.get("preserved_candidate_hil_state") or {})
    _require(
        pointer_baseline.get("project_id") == session.get("project_id")
        and pointer_baseline.get("accepted_pv") == pointer.get("accepted_pv")
        and pointer_baseline.get("generation") == pointer.get("generation")
        and pointer_baseline.get("prior_generation")
        == pointer.get("prior_generation")
        and pointer_baseline.get("accepted_manifest_sha256")
        == pointer.get("accepted_manifest_sha256")
        and pointer_baseline.get("pointer_sha256") == pointer_sha256
        and pointer_baseline.get("moved") is False
        and session.get("accepted_pv") == pointer.get("accepted_pv")
        and session.get("accepted_pointer_generation") == pointer.get("generation"),
        "CODEX_DIRECT_ENTRY_POINTER_DRIFT",
        "The accepted pointer no longer matches the exact direct-entry baseline.",
    )
    candidate_present = bool(session.get("candidate_id"))
    if candidate_present:
        pending_task_sha256 = (
            sha256_bytes(canonical_json_bytes(metadata["pending_task"]))
            if isinstance(metadata.get("pending_task"), dict)
            else None
        )
        _require(
            receipt.get("schema")
            == "evidence-lane.direct-forced-same-worktree-entry-receipt.v2"
            and preserved_candidate.get("candidate_id") == session.get("candidate_id")
            and preserved_candidate.get("lifecycle_state") == session.get("state")
            and preserved_candidate.get("pending_hil") is True
            and bool(metadata.get("pending_hil")) is True
            and preserved_candidate.get("pending_task_sha256")
            == pending_task_sha256
            and candidate_boundary
            == {
                "state": "PENDING_CANDIDATE_PRESERVED",
                "candidate_id": session.get("candidate_id"),
            },
            "CODEX_DIRECT_ENTRY_CANDIDATE_PRESERVATION_MISMATCH",
            "The direct checkpoint candidate boundary does not match the preserved pending-HIL state.",
        )
    else:
        _require(
            not bool(metadata.get("pending_hil"))
            and not isinstance(metadata.get("pending_task"), dict)
            and candidate_boundary
            == {"state": "NO_PENDING_CANDIDATE", "candidate_id": None},
            "CODEX_DIRECT_ENTRY_CANDIDATE_OR_HIL_PRESENT",
            "The direct checkpoint candidate boundary is inconsistent.",
        )

    destination = dict(
        dict(receipt.get("host_task_binding") or {}).get("destination") or {}
    )
    entry_source = dict(receipt.get("live_dirty_source_proof") or {})
    repository = Path(str(project.get("repository_path") or "")).resolve()
    destination_workspace = Path(str(destination.get("workspace_path") or "")).resolve()
    entry_repository = Path(str(entry_source.get("repository_path") or "")).resolve()
    _require(
        repository.is_dir()
        and os.path.normcase(str(repository))
        == os.path.normcase(str(destination_workspace))
        == os.path.normcase(str(entry_repository)),
        "CODEX_DIRECT_ENTRY_WORKSPACE_MISMATCH",
        "The current registered repository is not the exact direct-entry worktree.",
    )
    current_source = calculate_worktree_change_identity(repository)
    _require(
        current_source.get("branch") == entry_source.get("branch")
        and current_source.get("head") == entry_source.get("commit_sha")
        and current_source.get("tree") == entry_source.get("tree_sha"),
        "CODEX_DIRECT_ENTRY_SOURCE_BASE_DRIFT",
        "The direct checkpoint must remain on the same repository branch, commit, and tree while allowing governed dirty-byte work.",
        current_branch=current_source.get("branch"),
        current_commit_sha=current_source.get("head"),
        current_tree_sha=current_source.get("tree"),
    )
    return {
        "schema": "evidence-lane.direct-entry-checkpoint-continuity.v1",
        "status": "PASS",
        "pointer": {
            "accepted_pv": pointer.get("accepted_pv"),
            "generation": pointer.get("generation"),
            "manifest_sha256": pointer.get("accepted_manifest_sha256"),
            "pointer_sha256": pointer_sha256,
            "candidate_absent": not candidate_present,
            "candidate_id": session.get("candidate_id"),
            "pending_hil": bool(metadata.get("pending_hil")),
            "candidate_preserved": candidate_present,
        },
        "source": {
            "repository_path_sha256": sha256_bytes(str(repository).encode("utf-8")),
            "branch": current_source.get("branch"),
            "commit_sha": current_source.get("head"),
            "tree_sha": current_source.get("tree"),
            "entry_worktree_sha256": entry_source.get("worktree_sha256"),
            "current_worktree_sha256": current_source.get(
                "working_identity_sha256"
            ),
            "worktree_changed_since_entry": current_source.get(
                "working_identity_sha256"
            )
            != entry_source.get("worktree_sha256"),
            "complete_path_set_sha256": current_source.get(
                "complete_path_set_sha256"
            ),
            "complete_path_count": current_source.get("path_count"),
            "dirty_path_set_sha256": current_source.get("dirty_path_set_sha256"),
            "dirty_path_count": current_source.get("dirty_path_count"),
            "dirty_content_sha256": current_source.get("dirty_content_sha256"),
            "raw_paths_persisted": False,
        },
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }


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
        bool(
            project_id
            and evidence_session_id
            and project_id == project.get("project_id") == pointer.get("project_id")
        ),
        "TURN_CONTROL_PROJECT_SESSION_POINTER_MISMATCH",
        "Project, session, and accepted-pointer identities do not agree.",
    )
    _require(
        bool(
            accepted_pv
            and session.get("accepted_pv") == accepted_pv
            and int(session.get("accepted_pointer_generation") or 0)
            == pointer_generation
            and metadata.get("entry_pv") == accepted_pv
        ),
        "TURN_CONTROL_ENTRY_POINTER_MISMATCH",
        "The current accepted pointer does not match the governed Entry boundary.",
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
    )
    entry_manifest_sha256 = _sha(
        metadata.get("entry_manifest_sha256"), field="entry_manifest_sha256"
    )
    _require(
        pointer.get("accepted_manifest_sha256") == entry_manifest_sha256,
        "TURN_CONTROL_ACCEPTED_MANIFEST_MISMATCH",
        "The accepted Entry identity does not match the sealed pointer SHA-256.",
    )
    entry_package_sha256 = _sha(
        metadata.get("entry_package_sha256"), field="entry_package_sha256"
    )
    direct_entry = _verified_direct_entry_authority(session)
    sealed_resume_row: dict[str, Any]
    if isinstance(direct_entry, dict):
        direct_plan = dict(direct_entry["plan"])
        prepare_sha256 = str(direct_entry["receipt_sha256"])
        state_travel_task_list_sha256 = _sha(
            direct_plan.get("identity_sha256"),
            field="direct_forced_same_worktree_entry.plan_task_proof.identity_sha256",
        )
        sealed_resume_row = {
            "position": int(direct_plan.get("active_row") or 0),
            "task_id": str(direct_plan.get("active_task_id") or ""),
            "status": "IN_PROGRESS",
        }
        entry_route = "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK"
    else:
        state_travel = metadata.get("state_travel") or {}
        prepare_sha256 = _sha(
            state_travel.get("verified_snapshot_sha256"),
            field="state_travel.verified_snapshot_sha256",
        )
        resume_contract = state_travel.get("resume_contract") or {}
        task_list = resume_contract.get("task_list") or []
        resume_active_rows = [
            dict(row)
            for row in task_list
            if isinstance(row, dict)
            and str(row.get("status") or "").upper() in _ACTIVE_PLAN_STATUSES
        ]
        _require(
            len(resume_active_rows) == 1,
            "TURN_CONTROL_PLAN_ROW_REQUIRED",
            "The State Travel contract must preserve exactly one original resume row.",
            active_plan_rows=len(resume_active_rows),
        )
        sealed_resume_row = resume_active_rows[0]
        state_travel_task_list_sha256 = _sha(
            resume_contract.get("task_list_sha256"),
            field="state_travel.resume_contract.task_list_sha256",
        )
        entry_route = "SEALED_STATE_TRAVEL_PREPARE_RESUME"
    backlog_status = ProjectStore(root).backlog_status(project_id)
    goal_projection = backlog_status.get("goal_projection") or {}
    goal_rows = goal_projection.get("rows") or []
    resume_row_matches = [
        dict(row)
        for row in goal_rows
        if isinstance(row, dict)
        and row.get("task_id") == sealed_resume_row.get("task_id")
    ]
    _require(
        len(resume_row_matches) == 1,
        "TURN_CONTROL_STATE_TRAVEL_RESUME_ROW_MISMATCH",
        "The original State Travel resume row must remain exactly once in Plan history.",
        sealed_task_id=sealed_resume_row.get("task_id"),
        current_match_count=len(resume_row_matches),
    )
    current_active_rows = [
        dict(row)
        for row in goal_rows
        if isinstance(row, dict)
        and str(row.get("status") or "").lower() == "in_progress"
        and str(row.get("lifecycle_status") or "").upper() == "ACTIVE"
    ]
    _require(
        len(current_active_rows) == 1,
        "TURN_CONTROL_CANONICAL_ACTIVE_PLAN_ROW_REQUIRED",
        "Exactly one current canonical Plan row must be active for this Codex turn.",
        current_active_plan_rows=len(current_active_rows),
    )
    plan_row = current_active_rows[0]
    _require(
        metadata.get("active_backlog_task_id") == plan_row.get("task_id")
        and metadata.get("active_backlog_task_status") == "ACTIVE",
        "TURN_CONTROL_SESSION_ACTIVE_PLAN_BINDING_MISMATCH",
        "The governed session and current canonical Plan row do not agree.",
        session_active_task_id=metadata.get("active_backlog_task_id"),
        canonical_active_task_id=plan_row.get("task_id"),
    )
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
        task_binding_source = "SESSION_RUNTIME_TASK_PLUS_CANONICAL_ACTIVE_PLAN_ROW"
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
        "entry_route": entry_route,
        "direct_entry_authority": direct_entry,
        "persistent_plan_row": {
            "position": int(plan_row.get("number") or 0),
            "task_id": plan_row.get("task_id"),
            "status": plan_row.get("status"),
            "lifecycle_status": plan_row.get("lifecycle_status"),
            "step_sha256": sha256_bytes(
                str(plan_row.get("step") or "").encode("utf-8")
            ),
            "state_travel_task_list_sha256": state_travel_task_list_sha256,
            "goal_projection_sha256": plan_projection_sha256,
            "canonical_plan_sha256": goal_projection.get("canonical_plan_sha256"),
            "event_head_sha256": backlog_status.get("event_head_sha256"),
            "goal_projection_task_count": int(goal_projection.get("task_count") or 0),
            "persistent_until": goal_projection.get("persistent_until"),
            "state_travel_resume_row": {
                "position": int(sealed_resume_row.get("position") or 0),
                "task_id": sealed_resume_row.get("task_id"),
                "sealed_status": sealed_resume_row.get("status"),
                "current_status": resume_row_matches[0].get("status"),
                "current_lifecycle_status": resume_row_matches[0].get(
                    "lifecycle_status"
                ),
            },
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
            "entry_route": entry_route,
            "mode_binding_receipt_sha256": _sha(
                active_mode.get("binding_receipt_sha256"),
                field="active_mode_binding.binding_receipt_sha256",
            ),
            "goal_projection_sha256": plan_projection_sha256,
        },
        "gates": {
            "source_mutation_requires_prepare": direct_entry is None,
            "source_mutation_requires_verified_entry": True,
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


def seal_exact_task_project_session_binding(
    root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    expected_active_task_id: str,
    running_surface_inventory: Mapping[str, Any] | None = None,
    expected_surface_catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal one exact Codex task/project/session/Plan/runtime binding.

    Discovery is deliberately limited to installer-prepared exact Codex task
    receipts whose project, governed session, and governed host-session identity
    already match the active local authority.  Task titles and repository CWDs are
    not accepted as identity signals.  Multiple matching receipts fail closed.
    """

    exact_root = Path(root).resolve()
    project_root = _project_authority_root(exact_root, project_id)
    active_session_path = project_root / "active_session.json"
    project_path = project_root / "project.json"
    _require(
        active_session_path.is_file() and project_path.is_file(),
        "CODEX_EXACT_BINDING_PROJECT_AUTHORITY_REQUIRED",
        "The active project/session authority is unavailable.",
    )
    active_session = _json(active_session_path)
    _require(
        active_session.get("project_id") == project_id
        and active_session.get("session_id") == evidence_session_id,
        "CODEX_EXACT_BINDING_ACTIVE_SESSION_MISMATCH",
        "The requested governed session is not the active project session.",
    )
    session_path = project_root / "sessions" / f"{evidence_session_id}.json"
    _require(
        session_path.is_file(),
        "CODEX_EXACT_BINDING_SESSION_REQUIRED",
        "The exact governed session record is missing.",
    )
    session = _json(session_path)
    project = _json(project_path)
    metadata = dict(session.get("metadata") or {})
    governed_host_session_id = str(
        metadata.get("current_host_session_id") or ""
    ).strip()
    _require(
        session.get("project_id") == project_id
        and session.get("session_id") == evidence_session_id
        and not metadata.get("closed_at")
        and bool(governed_host_session_id),
        "CODEX_EXACT_BINDING_SESSION_IDENTITY_MISMATCH",
        "The active governed project, session, and host-session identities do not agree.",
    )

    turn_binding = _binding_snapshot(
        exact_root,
        {
            "project_root": project_root,
            "project": project,
            "session": session,
            "binding_match": "EXACT_INSTALLER_PREPARED_CODEX_TASK",
        },
    )
    active_plan = dict(turn_binding.get("persistent_plan_row") or {})
    _require(
        active_plan.get("task_id") == expected_active_task_id
        and active_plan.get("status") == "in_progress"
        and active_plan.get("lifecycle_status") == "ACTIVE"
        and active_plan.get("persistent_until") == "NEXT_SIX_WAY_HIL_PRESENTED",
        "CODEX_EXACT_BINDING_ACTIVE_PLAN_MISMATCH",
        "The requested task is not the sole current executable Plan row.",
        expected_active_task_id=expected_active_task_id,
        actual_active_task_id=active_plan.get("task_id"),
    )

    running_surface = (
        dict(running_surface_inventory)
        if isinstance(running_surface_inventory, Mapping)
        else _package_surface_inventory()
    )
    direct_entry = turn_binding.get("direct_entry_authority")
    if isinstance(direct_entry, dict):
        expected_catalog = (
            dict(expected_surface_catalog)
            if isinstance(expected_surface_catalog, Mapping)
            else {
                "tools": NATIVE_TOOL_COUNT,
                "read": NATIVE_READ_TOOL_COUNT,
                "write": NATIVE_WRITE_TOOL_COUNT,
                "skills": GOVERNED_SKILL_COUNT,
            }
        )
        surface_core = {
            key: running_surface.get(key)
            for key in (
                "schema",
                "plugin_version",
                "hooks",
                "skills",
                "catalog",
                "raw_paths_included",
            )
        }
        _require(
            running_surface.get("schema")
            == "evidence-lane.codex-installed-surface-inventory.v2"
            and running_surface.get("catalog") == expected_catalog
            and running_surface.get("raw_paths_included") is False
            and running_surface.get("release_catalog_matches_derived") is True
            and len(
                str(running_surface.get("public_surface_registry_sha256") or "")
            )
            == 64
            and len(str(running_surface.get("release_policy_sha256") or "")) == 64
            and running_surface.get("surface_inventory_sha256")
            == sha256_bytes(canonical_json_bytes(surface_core)),
            "CODEX_DIRECT_ENTRY_RUNNING_SURFACE_MISMATCH",
            "The current running surface cannot inherit the exact direct-entry task authority.",
        )
        checkpoint_continuity = _direct_entry_checkpoint_continuity(
            project_root=project_root,
            project=project,
            session=session,
            binding=turn_binding,
            direct_entry=direct_entry,
        )
        codex_task_id = str(direct_entry["destination_task_id"])
        task_uri = str(direct_entry["destination_deep_link"])
        direct_receipt = dict(direct_entry["receipt"])
        receipt_body = {
            "schema": "evidence-lane.codex-exact-task-project-session-binding.v1",
            "status": "PASS",
            "project_id": project_id,
            "evidence_session_id": evidence_session_id,
            "codex_thread_id": codex_task_id,
            "task_uri": task_uri,
            "task_uri_sha256": sha256_bytes(task_uri.encode()),
            "governed_host_session_id": governed_host_session_id,
            "active_plan_row": active_plan,
            "accepted_pointer": {
                **dict(checkpoint_continuity["pointer"]),
                "package_sha256": turn_binding.get("entry_package_sha256"),
            },
            "checkpoint_continuity": checkpoint_continuity,
            "running_plugin": {
                "plugin_id": "evidence-lane-plugin",
                "plugin_version": running_surface.get("plugin_version"),
                "plugin_selector": "RUNNING_PLUGIN_SURFACE",
                "archive_sha256": None,
                "install_receipt_sha256": None,
                "surface_inventory_sha256": running_surface.get(
                    "surface_inventory_sha256"
                ),
                "catalog": running_surface.get("catalog"),
            },
            "runtime_task_id": dict(session.get("task") or {}).get("task_id"),
            "task_binding_receipt_sha256": direct_entry["receipt_sha256"],
            "binding_authority_receipt_sha256": direct_entry[
                "task_binding_authority_receipt_sha256"
            ],
            "preparation_receipt_sha256": None,
            "release_authority_id": None,
            "release_authority_sha256": None,
            "task_binding_registry_revision": None,
            "binding_epoch_sha256": sha256_bytes(
                canonical_json_bytes(
                    {
                        "host_binding_epoch_sha256": _host_binding_epoch(session),
                        "pointer_sha256": dict(checkpoint_continuity["pointer"])[
                            "pointer_sha256"
                        ],
                        "worktree_sha256": dict(checkpoint_continuity["source"])[
                            "current_worktree_sha256"
                        ],
                        "binding_authority_receipt_sha256": direct_entry[
                            "task_binding_authority_receipt_sha256"
                        ],
                    }
                )
            ),
            "turn_binding_sha256": turn_binding.get("binding_sha256"),
            "identity_basis": (
                "SERVER_ATTESTED_DIRECT_ENTRY_PLUS_CURRENT_RUNNING_SURFACE_"
                "AND_NATIVE_PLAN_POINTER"
            ),
            "direct_entry_authority": {
                "receipt_sha256": direct_entry["receipt_sha256"],
                "task_binding_authority_receipt_sha256": direct_entry[
                    "task_binding_authority_receipt_sha256"
                ],
                "task_binding_authority_source": direct_entry[
                    "task_binding_authority_source"
                ],
                "runtime_instance_attestation_receipt_sha256": direct_entry[
                    "runtime_instance_attestation_receipt_sha256"
                ],
                "original_active_plan_task_id": dict(direct_entry["plan"])[
                    "active_task_id"
                ],
            },
            "task_title_used": False,
            "cwd_used": False,
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "hil_inferred": False,
            "installer_helper_invoked": False,
            "sealed_at": direct_receipt.get("bound_at"),
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        binding_epoch_sha256 = str(receipt_body["binding_epoch_sha256"])
        receipt_path = (
            project_root
            / "receipts"
            / "codex-task-bindings"
            / (
                f"{codex_task_id.lower()}__"
                f"{binding_epoch_sha256[:24].lower()}__direct.json"
            )
        )
        if receipt_path.is_file():
            _require(
                _json(receipt_path) == receipt,
                "CODEX_DIRECT_ENTRY_BINDING_RECEIPT_CONFLICT",
                "The direct-entry binding epoch already contains different sealed bytes.",
            )
        else:
            atomic_write_json(receipt_path, receipt)
        return receipt
    try:
        shared_task_binding = read_shared_task_binding(
            exact_root,
            project_id=project_id,
            evidence_session_id=evidence_session_id,
            task_id=governed_host_session_id,
            expected_active_plan_task_id=expected_active_task_id,
            surface=running_surface,
            expected_surface_catalog=expected_surface_catalog,
        )
    except EvidenceLaneError as exc:
        raise TurnControlError(exc.code, exc.message, **exc.details) from exc
    except ValueError as exc:
        raise TurnControlError(
            "CODEX_SHARED_TASK_BINDING_INVALID",
            "The shared exact-task registry could not be verified.",
            error_type=type(exc).__name__,
        ) from exc
    if isinstance(shared_task_binding, dict):
        codex_task_id = str(shared_task_binding["task_id"])
        task_uri = f"codex://threads/{codex_task_id}"
        release = dict(shared_task_binding["release_authority"])
        receipt_body = {
            "schema": "evidence-lane.codex-exact-task-project-session-binding.v1",
            "status": "PASS",
            "project_id": project_id,
            "evidence_session_id": evidence_session_id,
            "codex_thread_id": codex_task_id,
            "task_uri": task_uri,
            "task_uri_sha256": shared_task_binding.get("task_uri_sha256"),
            "governed_host_session_id": governed_host_session_id,
            "active_plan_row": active_plan,
            "accepted_pointer": {
                "accepted_pv": turn_binding.get("accepted_pv"),
                "generation": turn_binding.get("pointer_generation"),
                "manifest_sha256": turn_binding.get("entry_manifest_sha256"),
                "package_sha256": turn_binding.get("entry_package_sha256"),
            },
            "running_plugin": {
                "plugin_id": release.get("plugin_id"),
                "plugin_version": release.get("plugin_version"),
                "plugin_selector": "RUNNING_PLUGIN_SURFACE",
                "archive_sha256": None,
                "install_receipt_sha256": None,
                "surface_inventory_sha256": release.get("surface_inventory_sha256"),
                "catalog": release.get("catalog"),
            },
            "runtime_task_id": dict(session.get("task") or {}).get("task_id"),
            "task_binding_receipt_sha256": shared_task_binding.get(
                "task_binding_receipt_sha256"
            ),
            "binding_authority_receipt_sha256": shared_task_binding.get(
                "active_contract_rebind_receipt_sha256"
            ),
            "preparation_receipt_sha256": None,
            "release_authority_id": release.get("authority_id"),
            "release_authority_sha256": release.get("release_authority_sha256"),
            "task_binding_registry_revision": shared_task_binding.get("revision"),
            "binding_epoch_sha256": sha256_bytes(
                canonical_json_bytes(
                    {
                        "host_binding_epoch_sha256": _host_binding_epoch(session),
                        "shared_task_binding_epoch_sha256": shared_task_binding.get(
                            "binding_epoch_sha256"
                        ),
                        "binding_authority_receipt_sha256": shared_task_binding.get(
                            "active_contract_rebind_receipt_sha256"
                        ),
                    }
                )
            ),
            "turn_binding_sha256": turn_binding.get("binding_sha256"),
            "identity_basis": (
                "SHARED_EXACT_TASK_REGISTRY_PLUS_IMMUTABLE_RUNNING_RELEASE_"
                "AND_NATIVE_PLAN_POINTER"
            ),
            "task_title_used": False,
            "cwd_used": False,
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "hil_inferred": False,
            "installer_helper_invoked": False,
            "sealed_at": shared_task_binding.get("refreshed_at"),
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        binding_epoch_sha256 = str(receipt_body["binding_epoch_sha256"])
        receipt_path = (
            project_root
            / "receipts"
            / "codex-task-bindings"
            / (
                f"{codex_task_id.lower()}__"
                f"{binding_epoch_sha256[:24].lower()}__shared.json"
            )
        )
        if receipt_path.is_file():
            _require(
                _json(receipt_path) == receipt,
                "CODEX_SHARED_EXACT_BINDING_RECEIPT_CONFLICT",
                "The shared exact binding epoch already contains different bytes.",
            )
        else:
            atomic_write_json(receipt_path, receipt)
        return receipt

    installation_root = exact_root / "installations" / "codex-v200"
    binding_root = installation_root / "task-bindings"
    _require(
        binding_root.is_dir(),
        "CODEX_EXACT_BINDING_INSTALLER_RECEIPTS_REQUIRED",
        "The installer-prepared Codex task-binding authority is missing.",
    )
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(binding_root.glob("*.json"), key=lambda item: item.name):
        try:
            raw = _json(path)
        except TurnControlError:
            continue
        if (
            raw.get("schema") == "evidence-lane.codex-task-binding.v1"
            and raw.get("project_id") == project_id
            and raw.get("evidence_session_id") == evidence_session_id
            and raw.get("governed_host_session_id") == governed_host_session_id
        ):
            candidates.append((path, raw))
    _require(
        len(candidates) == 1,
        "CODEX_EXACT_BINDING_AMBIGUOUS_OR_MISSING",
        "Exactly one installer-prepared Codex task receipt must bind this project, session, and host session.",
        matching_task_bindings=len(candidates),
    )
    task_binding_path, raw_task_binding = candidates[0]
    codex_task_id = str(raw_task_binding.get("task_id") or "").strip()
    _require(
        _CODEX_TASK_ID_RE.fullmatch(codex_task_id) is not None
        and task_binding_path.stem.lower() == codex_task_id.lower(),
        "CODEX_EXACT_BINDING_THREAD_ID_MISMATCH",
        "The installer receipt filename and exact Codex thread UUID do not agree.",
    )
    task_binding = _read_codex_task_binding(
        exact_root,
        observed_host_session_id=codex_task_id,
    )
    _require(
        isinstance(task_binding, dict)
        and task_binding.get("project_id") == project_id
        and task_binding.get("evidence_session_id") == evidence_session_id
        and task_binding.get("governed_host_session_id") == governed_host_session_id,
        "CODEX_EXACT_BINDING_TASK_RECEIPT_MISMATCH",
        "The exact Codex thread receipt does not bind the active governed identities.",
    )
    assert task_binding is not None

    install_path = Path(str(task_binding["install_receipt"]))
    installation = _json(install_path)
    plugin = dict(installation.get("plugin") or {})
    activation = dict(installation.get("activation") or {})
    plugin_add = dict(activation.get("plugin_add") or {})
    installed_path = Path(str(plugin_add.get("installedPath") or ""))
    running_plugin_root = resolve_plugin_root(__file__)
    _require(
        activation.get("state") in _SUPPORTED_EXACT_TASK_BINDING_ACTIVATION_STATES
        and plugin.get("version") == task_binding.get("plugin_version")
        and plugin_add.get("version") == plugin.get("version")
        and str(plugin_add.get("pluginId") or "").startswith("evidence-lane-plugin@")
        and installed_path.is_absolute()
        and installed_path.is_dir()
        and installed_path.resolve() == running_plugin_root.resolve(),
        "CODEX_EXACT_BINDING_RUNNING_BUILD_MISMATCH",
        "The running plugin is not the exact installer-bound supported build.",
    )
    surface = _package_surface_inventory()
    _require(
        surface.get("plugin_version") == plugin.get("version")
        and surface.get("catalog")
        == {
            "tools": NATIVE_TOOL_COUNT,
            "read": NATIVE_READ_TOOL_COUNT,
            "write": NATIVE_WRITE_TOOL_COUNT,
            "skills": GOVERNED_SKILL_COUNT,
        },
        "CODEX_EXACT_BINDING_RUNNING_SURFACE_MISMATCH",
        "The running plugin surface does not match the exact v2 catalog.",
    )
    task_uri = f"codex://threads/{codex_task_id}"
    _require(
        task_binding.get("task_uri_sha256") == sha256_bytes(task_uri.encode("utf-8")),
        "CODEX_EXACT_BINDING_DEEPLINK_MISMATCH",
        "The exact Codex task UUID and deep-link identity do not agree.",
    )

    receipt_body = {
        "schema": "evidence-lane.codex-exact-task-project-session-binding.v1",
        "status": "PASS",
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "codex_thread_id": codex_task_id,
        "task_uri": task_uri,
        "task_uri_sha256": task_binding.get("task_uri_sha256"),
        "governed_host_session_id": governed_host_session_id,
        "active_plan_row": active_plan,
        "accepted_pointer": {
            "accepted_pv": turn_binding.get("accepted_pv"),
            "generation": turn_binding.get("pointer_generation"),
            "manifest_sha256": turn_binding.get("entry_manifest_sha256"),
            "package_sha256": turn_binding.get("entry_package_sha256"),
        },
        "running_plugin": {
            "plugin_id": plugin.get("plugin_id"),
            "plugin_version": plugin.get("version"),
            "plugin_selector": plugin_add.get("pluginId"),
            "archive_sha256": installation.get("archive_sha256"),
            "install_receipt_sha256": task_binding.get("install_receipt_sha256"),
            "surface_inventory_sha256": surface.get("surface_inventory_sha256"),
            "catalog": surface.get("catalog"),
        },
        "runtime_task_id": dict(session.get("task") or {}).get("task_id"),
        "task_binding_receipt_sha256": task_binding.get("task_binding_receipt_sha256"),
        "preparation_receipt_sha256": task_binding.get("preparation_receipt_sha256"),
        "binding_epoch_sha256": _host_binding_epoch(session),
        "turn_binding_sha256": turn_binding.get("binding_sha256"),
        "identity_basis": "EXACT_CODEX_THREAD_RECEIPT_PLUS_NATIVE_PLAN_AND_POINTER",
        "task_title_used": False,
        "cwd_used": False,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "sealed_at": task_binding.get("prepared_at_utc"),
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    binding_epoch_sha256 = str(receipt_body["binding_epoch_sha256"])
    receipt_path = (
        project_root
        / "receipts"
        / "codex-task-bindings"
        / f"{codex_task_id.lower()}__{binding_epoch_sha256[:24].lower()}.json"
    )
    if receipt_path.is_file():
        _require(
            _json(receipt_path) == receipt,
            "CODEX_EXACT_BINDING_RECEIPT_CONFLICT",
            "The exact binding epoch already contains different sealed bytes.",
        )
    else:
        atomic_write_json(receipt_path, receipt)
    return receipt


def seal_active_task_acceptance_checkpoint(
    root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    expected_active_task_id: str,
) -> dict[str, Any]:
    """Seal one acceptance-backed, candidate-free active-task checkpoint.

    The exact Codex task binding remains the identity authority.  Advancement
    additionally requires one current-run ChatLineage activity whose visible
    payload covers the active Plan row's complete acceptance contract.  A
    generic task transition therefore cannot be manufactured from title, CWD,
    an old run, or an unrelated PASS event.
    """

    exact_root = Path(root).resolve()
    project_root = _project_authority_root(exact_root, project_id)
    session_path = project_root / "sessions" / f"{evidence_session_id}.json"
    backlog_path = resolved_plan_backlog_path(project_root)
    _require(
        session_path.is_file() and backlog_path.is_file(),
        "CODEX_TASK_CHECKPOINT_AUTHORITY_REQUIRED",
        "The active session and Plan authority are required for checkpoint sealing.",
    )
    session = _json(session_path)
    backlog = _json(backlog_path)
    metadata = dict(session.get("metadata") or {})
    runtime_task = dict(session.get("task") or {})
    runtime_task_id = str(runtime_task.get("task_id") or "").strip()
    run_id = str(metadata.get("run_id") or "").strip()
    active_rows = [
        dict(row)
        for row in backlog.get("tasks") or []
        if isinstance(row, dict) and row.get("status") == "ACTIVE"
    ]
    _require(
        len(active_rows) == 1
        and active_rows[0].get("task_id") == expected_active_task_id
        and metadata.get("active_backlog_task_id") == expected_active_task_id
        and metadata.get("active_backlog_task_status") == "ACTIVE"
        and bool(runtime_task_id)
        and bool(run_id),
        "CODEX_TASK_CHECKPOINT_ACTIVE_ROW_MISMATCH",
        "Exactly one active Plan row and current runtime task must match the checkpoint.",
        expected_active_task_id=expected_active_task_id,
    )
    active_task = active_rows[0]
    acceptance_checks = [
        str(value).strip()
        for value in active_task.get("acceptance_checks") or []
        if str(value).strip()
    ]
    _require(
        bool(acceptance_checks),
        "CODEX_TASK_CHECKPOINT_ACCEPTANCE_REQUIRED",
        "The active Plan row has no exact acceptance contract to verify.",
    )

    lineage_path = (
        resolved_chat_lineage_root(project_root) / f"{evidence_session_id}.jsonl"
    )
    events = ChatLineage(lineage_path).events()
    matching: list[dict[str, Any]] = []
    for event in events:
        if (
            event.get("session_id") != evidence_session_id
            or event.get("task_id") != runtime_task_id
            or event.get("run_id") != run_id
            or event.get("event_type") not in {"task.test.output", "task.build.output"}
        ):
            continue
        payload = dict(event.get("visible_payload") or {})
        covered = payload.get("acceptance_checks")
        if not isinstance(covered, list):
            singular = str(payload.get("acceptance_check") or "").strip()
            covered = [singular] if singular else []
        exact_covered = [str(value).strip() for value in covered if str(value).strip()]
        if (
            payload.get("active_task_id", payload.get("task_id"))
            == expected_active_task_id
            and payload.get("result") == "PASS"
            and exact_covered == acceptance_checks
            and payload.get("candidate_created") is False
            and payload.get("pending_hil") is False
            and payload.get("pointer_moved") is False
            and payload.get("hil_inferred") is False
        ):
            matching.append(event)
    _require(
        bool(matching),
        "CODEX_TASK_CHECKPOINT_ACCEPTANCE_EVIDENCE_REQUIRED",
        "No current-run activity proves the active row's complete acceptance contract.",
        expected_active_task_id=expected_active_task_id,
        acceptance_checks=acceptance_checks,
    )
    evidence = matching[-1]
    binding = seal_exact_task_project_session_binding(
        exact_root,
        project_id=project_id,
        evidence_session_id=evidence_session_id,
        expected_active_task_id=expected_active_task_id,
    )
    receipt_body = {
        "schema": "evidence-lane.active-task-acceptance-checkpoint.v1",
        "status": "PASS",
        "verification_kind": "ACTIVE_TASK_ACCEPTANCE",
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "governed_host_session_id": binding["governed_host_session_id"],
        "active_plan_row": binding["active_plan_row"],
        "accepted_pointer": binding["accepted_pointer"],
        "running_plugin": binding["running_plugin"],
        "runtime_task_id": runtime_task_id,
        "run_id": run_id,
        "acceptance_contract": {
            "checks": acceptance_checks,
            "checks_sha256": sha256_bytes(canonical_json_bytes(acceptance_checks)),
        },
        "acceptance_evidence": {
            key: evidence.get(key)
            for key in (
                "event_id",
                "event_type",
                "occurred_at",
                "lineage_index",
                "visible_payload_sha256",
                "event_sha256",
            )
        },
        "exact_binding_receipt_sha256": binding["receipt_sha256"],
        "identity_basis": (
            "EXACT_CODEX_TASK_BINDING_PLUS_CURRENT_RUN_ACCEPTANCE_ACTIVITY"
        ),
        "task_title_used": False,
        "cwd_used": False,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "sealed_at": evidence["occurred_at"],
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    receipt_path = (
        project_root
        / "receipts"
        / "task-checkpoints"
        / f"{str(evidence['event_sha256'])[:32].lower()}.json"
    )
    if receipt_path.is_file():
        _require(
            _json(receipt_path) == receipt,
            "CODEX_TASK_CHECKPOINT_RECEIPT_CONFLICT",
            "The acceptance event already seals different checkpoint bytes.",
        )
    else:
        atomic_write_json(receipt_path, receipt)
    return receipt


def requires_per_delta_local_verification(task: Mapping[str, Any]) -> bool:
    """Return whether one Plan row uses the strict package-parity proof law.

    Historical Task6 IDs remain stable Plan identities; they are recognized as
    data, never as the current host-task or public routing authority.
    """

    task_id = str(task.get("task_id") or "").strip()
    return (
        task_id.startswith("EL-CODEX-T6-PARITY-")
        or str(task.get("commit_batch_id") or "").strip() == "PV13_TASK6_PARITY"
        or str(task.get("current_contract_authority") or "").strip()
        == "ACTIVE_CONTRACT_REBIND"
    )


def seal_per_delta_local_verification_checkpoint(
    root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    expected_active_task_id: str,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal one exact, candidate-free local verification checkpoint.

    This is the stronger per-Delta package-parity successor to generic acceptance
    activity. It verifies the live worktree and every named source/test byte,
    binds bounded test commands and outputs, checks the dependency generation,
    and writes one immutable receipt before task advancement is permitted.
    """

    exact_root = Path(root).resolve()
    store = ProjectStore(exact_root)
    project_root = store.project_root(project_id)
    session_path = project_root / "sessions" / f"{evidence_session_id}.json"
    _require(
        session_path.is_file(),
        "DELTA_VERIFICATION_SESSION_REQUIRED",
        "The governed session is required for per-Delta verification.",
    )
    session = _json(session_path)
    metadata = dict(session.get("metadata") or {})
    backlog = store.backlog_status(project_id)
    raw_tasks = [
        dict(row) for row in backlog.get("tasks") or [] if isinstance(row, dict)
    ]
    active_rows = [row for row in raw_tasks if row.get("status") == "ACTIVE"]
    _require(
        len(active_rows) == 1
        and active_rows[0].get("task_id") == expected_active_task_id
        and metadata.get("active_backlog_task_id") == expected_active_task_id
        and metadata.get("active_backlog_task_status") == "ACTIVE",
        "DELTA_VERIFICATION_ACTIVE_ROW_MISMATCH",
        "Per-Delta verification must bind the sole active Plan row.",
        expected_active_task_id=expected_active_task_id,
    )
    active_task = active_rows[0]
    _require(
        requires_per_delta_local_verification(active_task),
        "DELTA_VERIFICATION_TASK_NOT_GOVERNED",
        "This row does not use the normalized per-Delta verification law.",
        task_id=expected_active_task_id,
    )
    _require(
        verification.get("schema")
        == "evidence-lane.per-delta-local-verification-input.v1"
        and not contains_secret(dict(verification)),
        "DELTA_VERIFICATION_INPUT_INVALID",
        "The per-Delta verification envelope is malformed or contains secrets.",
    )
    receipt_id = str(verification.get("receipt_id") or "").strip()
    _require(
        _DELTA_VERIFICATION_ID_RE.fullmatch(receipt_id) is not None,
        "DELTA_VERIFICATION_RECEIPT_ID_INVALID",
        "The per-Delta receipt ID must be one bounded stable identifier.",
    )
    _require(
        verification.get("active_task_id") == expected_active_task_id,
        "DELTA_VERIFICATION_TASK_BINDING_MISMATCH",
        "The verification envelope names a different active task.",
    )
    source_catalog = {
        "tools": NATIVE_TOOL_COUNT,
        "read": NATIVE_READ_TOOL_COUNT,
        "write": NATIVE_WRITE_TOOL_COUNT,
        "skills": GOVERNED_SKILL_COUNT,
    }
    supplied_source_catalog = verification.get("source_catalog")
    _require(
        supplied_source_catalog is None
        or (
            isinstance(supplied_source_catalog, Mapping)
            and dict(supplied_source_catalog) == source_catalog
        ),
        "DELTA_VERIFICATION_SOURCE_CATALOG_MISMATCH",
        "The per-Delta source catalog must bind the exact current source constants.",
    )
    adaptive_receipt_supplied = verification.get("adaptive_delta_exit_receipt")
    adaptive_receipt = (
        dict(adaptive_receipt_supplied)
        if isinstance(adaptive_receipt_supplied, Mapping)
        else None
    )
    install_deferral = adaptive_install_deferral_facts(
        adaptive_receipt,
        project_id=project_id,
        session_id=evidence_session_id,
        active_task_id=expected_active_task_id,
        plan_tasks=raw_tasks,
    )
    _require(
        adaptive_receipt is None or install_deferral["valid"] is True,
        "DELTA_VERIFICATION_INSTALL_DEFERRAL_MISMATCH",
        "An install deferral must be one exact self-hashed adaptive-exit receipt targeting a later queued Plan row.",
    )
    deferred_surface: dict[str, Any] | None = None
    deferred_catalog: dict[str, Any] | None = None
    if install_deferral["valid"] is True:
        supplied_surface = verification.get("installed_surface_inventory")
        _require(
            isinstance(supplied_surface, Mapping),
            "DELTA_VERIFICATION_DEFERRED_SURFACE_REQUIRED",
            "A grouped install deferral must bind the exact currently attached installed surface.",
        )
        deferred_surface = dict(cast(Mapping[str, Any], supplied_surface))
        deferred_catalog = dict(deferred_surface.get("catalog") or {})
        surface_core = {
            key: deferred_surface.get(key)
            for key in (
                "schema",
                "plugin_version",
                "hooks",
                "skills",
                "catalog",
                "raw_paths_included",
            )
        }
        _require(
            deferred_surface.get("schema")
            == "evidence-lane.codex-installed-surface-inventory.v2"
            and str(deferred_surface.get("plugin_version") or "").split("+", 1)[0]
            == ENGINE_VERSION
            and deferred_surface.get("raw_paths_included") is False
            and deferred_surface.get("surface_inventory_sha256")
            == sha256_bytes(canonical_json_bytes(surface_core))
            and len(str(deferred_surface.get("release_policy_sha256") or "")) == 64
            and set(deferred_catalog) == {"tools", "read", "write", "skills"}
            and all(isinstance(value, int) for value in deferred_catalog.values())
            and deferred_catalog["tools"]
            == deferred_catalog["read"] + deferred_catalog["write"]
            and 0 < deferred_catalog["tools"] <= source_catalog["tools"]
            and 0 <= deferred_catalog["read"] <= source_catalog["read"]
            and 0 <= deferred_catalog["write"] <= source_catalog["write"]
            and deferred_catalog["skills"] == source_catalog["skills"],
            "DELTA_VERIFICATION_DEFERRED_SURFACE_MISMATCH",
            "The deferred installed surface is not a self-hashed exact predecessor of the current source catalog.",
        )

    goal_rows = [
        dict(row)
        for row in (backlog.get("goal_projection") or {}).get("rows") or []
        if isinstance(row, dict)
    ]
    active_goal = next(
        (row for row in goal_rows if row.get("task_id") == expected_active_task_id),
        None,
    )
    runtime_contract = store.plan_runtime_query(
        project_id,
        task_id=expected_active_task_id,
        limit=1,
    )["contract"]
    task_contract_sha256 = str(
        runtime_contract.get("task_contract_sha256") or ""
    ).upper()
    _require(
        isinstance(active_goal, dict)
        and active_goal.get("status") == "in_progress"
        and _SHA256_RE.fullmatch(task_contract_sha256) is not None
        and str(verification.get("task_contract_sha256") or "").upper()
        == task_contract_sha256,
        "DELTA_VERIFICATION_CONTRACT_MISMATCH",
        "The verification envelope does not bind the current projected task contract.",
    )

    pointer = store.pointer(project_id)
    dependencies = [
        str(value).strip()
        for value in active_task.get("dependencies") or []
        if str(value).strip()
    ]
    supplied_dependencies = [
        str(value).strip()
        for value in verification.get("dependency_task_ids") or []
        if str(value).strip()
    ]
    by_id = {str(row.get("task_id")): row for row in raw_tasks}
    dependency_statuses = {
        task_id: str((by_id.get(task_id) or {}).get("status") or "MISSING")
        for task_id in dependencies
    }
    _require(
        verification.get("dependency_generation") == pointer.generation
        and supplied_dependencies == dependencies
        and all(
            status in {"ACCEPTED", "DONE"} for status in dependency_statuses.values()
        ),
        "DELTA_VERIFICATION_DEPENDENCY_MISMATCH",
        "The verification envelope does not bind the exact completed dependency generation.",
        expected_generation=pointer.generation,
        expected_dependencies=dependencies,
        dependency_statuses=dependency_statuses,
    )

    prior_receipt: dict[str, Any] | None = None
    for row in sorted(raw_tasks, key=lambda item: int(item.get("sequence", 0))):
        if int(row.get("sequence", 0)) >= int(active_task.get("sequence", 0)):
            break
        checkpoint = row.get("task_checkpoint_completion_receipt")
        proof = (
            checkpoint.get("verification_proof")
            if isinstance(checkpoint, dict)
            else None
        )
        delta = proof.get("delta_verification") if isinstance(proof, dict) else None
        if isinstance(delta, dict) and delta.get("status") == "PASS":
            prior_receipt = delta
    entry_freshness = dict(metadata.get("entry_freshness") or {})
    expected_pre_worktree_sha256 = str(
        (prior_receipt or {}).get("post_worktree_sha256")
        or entry_freshness.get("bound_worktree_sha256")
        or ""
    ).upper()
    pre_worktree_sha256 = str(verification.get("pre_worktree_sha256") or "").upper()
    post_worktree_sha256 = str(verification.get("post_worktree_sha256") or "").upper()
    repository = Path(store.config(project_id).repository_path).resolve()
    live_worktree_sha256 = calculate_worktree_sha256(repository)
    _require(
        _SHA256_RE.fullmatch(expected_pre_worktree_sha256) is not None
        and pre_worktree_sha256 == expected_pre_worktree_sha256
        and post_worktree_sha256 == live_worktree_sha256,
        "DELTA_VERIFICATION_WORKTREE_MISMATCH",
        "The per-Delta pre/post worktree chain does not match live authority.",
        expected_pre_worktree_sha256=expected_pre_worktree_sha256,
        live_post_worktree_sha256=live_worktree_sha256,
    )

    permitted_paths = [
        str(value).replace("\\", "/").strip()
        for value in active_task.get("permitted_paths") or []
        if str(value).strip()
    ]
    changed_paths = verification.get("changed_paths")
    _require(
        isinstance(changed_paths, list) and 1 <= len(changed_paths) <= 128,
        "DELTA_VERIFICATION_CHANGED_PATHS_REQUIRED",
        "Each Delta requires a bounded non-empty changed-path hash set.",
    )
    normalized_paths: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw in cast(list[Any], changed_paths):
        _require(
            isinstance(raw, Mapping),
            "DELTA_VERIFICATION_CHANGED_PATH_INVALID",
            "Every changed-path entry must be one object.",
        )
        relative = str(raw.get("path") or "").replace("\\", "/").strip("/")
        role = str(raw.get("role") or "").strip().upper()
        supplied_sha256 = str(raw.get("sha256") or "").strip().upper()
        target = (repository / Path(relative)).resolve()
        _require(
            bool(relative)
            and relative not in seen_paths
            and ".." not in Path(relative).parts
            and target.is_relative_to(repository)
            and target.is_file()
            and role in _DELTA_VERIFICATION_ROLES
            and _SHA256_RE.fullmatch(supplied_sha256) is not None
            and supplied_sha256 == sha256_file(target)
            and any(
                fnmatchcase(relative, pattern)
                or (
                    not any(marker in pattern for marker in "*?[")
                    and relative.startswith(pattern.rstrip("/") + "/")
                )
                for pattern in permitted_paths
            ),
            "DELTA_VERIFICATION_CHANGED_PATH_MISMATCH",
            "A changed path is duplicate, outside authority, absent, or hash-mismatched.",
            path=relative or None,
            role=role or None,
        )
        seen_paths.add(relative)
        normalized_paths.append(
            {
                "path": relative,
                "role": role,
                "sha256": supplied_sha256,
                "byte_count": target.stat().st_size,
            }
        )
    roles = {row["role"] for row in normalized_paths}
    _require(
        "SOURCE" in roles and "TEST" in roles,
        "DELTA_VERIFICATION_SOURCE_AND_TEST_HASHES_REQUIRED",
        "A code Delta must bind at least one live source hash and one live test hash.",
        roles=sorted(roles),
    )

    test_runs = verification.get("test_runs")
    _require(
        isinstance(test_runs, list) and 1 <= len(test_runs) <= 32,
        "DELTA_VERIFICATION_TEST_RUNS_REQUIRED",
        "Each Delta requires one bounded exact local test run.",
    )
    normalized_runs: list[dict[str, Any]] = []
    for raw in cast(list[Any], test_runs):
        _require(
            isinstance(raw, Mapping),
            "DELTA_VERIFICATION_TEST_RUN_INVALID",
            "Every test run must be one object.",
        )
        selector = str(raw.get("selector") or "").strip()
        command = str(raw.get("command") or "").strip()
        output = str(raw.get("output") or "").strip()
        command_sha256 = str(raw.get("command_sha256") or "").strip().upper()
        output_sha256 = str(raw.get("output_sha256") or "").strip().upper()
        negative_cases = [
            str(value).strip()
            for value in raw.get("negative_cases") or []
            if str(value).strip()
        ]
        _require(
            raw.get("status") == "PASS"
            and 1 <= len(selector) <= 2048
            and 1 <= len(command) <= 4096
            and 1 <= len(output) <= _MAX_VISIBLE_EVENT_CHARS
            and command_sha256 == sha256_bytes(command.encode("utf-8"))
            and output_sha256 == sha256_bytes(output.encode("utf-8"))
            and 1 <= len(negative_cases) <= 64
            and all(len(value) <= 2048 for value in negative_cases),
            "DELTA_VERIFICATION_TEST_RUN_MISMATCH",
            "A test run lacks exact PASS output, command hash, selector, or negative cases.",
            selector=selector or None,
        )
        normalized_runs.append(
            {
                "selector": selector,
                "command": command,
                "command_sha256": command_sha256,
                "status": "PASS",
                "output": output,
                "output_sha256": output_sha256,
                "negative_cases": negative_cases,
            }
        )

    acceptance_checks = [
        str(value).strip()
        for value in verification.get("acceptance_checks") or []
        if str(value).strip()
    ]
    limitations = [
        str(value).strip()
        for value in verification.get("limitations") or []
        if str(value).strip()
    ]
    _require(
        acceptance_checks
        == [
            str(value).strip()
            for value in active_task.get("acceptance_checks") or []
            if str(value).strip()
        ]
        and len(limitations) <= 32
        and all(len(value) <= 2048 for value in limitations)
        and verification.get("candidate_created") is False
        and verification.get("pending_hil") is False
        and verification.get("pointer_moved") is False
        and verification.get("hil_inferred") is False
        and verification.get("git_executed") is False
        and verification.get("install_executed") is False,
        "DELTA_VERIFICATION_BOUNDARY_MISMATCH",
        "The verification envelope does not preserve acceptance or lifecycle boundaries.",
    )

    if install_deferral["valid"] is True:
        assert deferred_surface is not None and deferred_catalog is not None
        binding = seal_exact_task_project_session_binding(
            exact_root,
            project_id=project_id,
            evidence_session_id=evidence_session_id,
            expected_active_task_id=expected_active_task_id,
            running_surface_inventory=deferred_surface,
            expected_surface_catalog=deferred_catalog,
        )
    else:
        try:
            seal_or_refresh_shared_task_binding(
                exact_root,
                project_id=project_id,
                evidence_session_id=evidence_session_id,
                task_id=str(metadata.get("current_host_session_id") or ""),
                surface=_package_surface_inventory(),
                bound_by="PER_DELTA_LOCAL_VERIFICATION",
            )
        except EvidenceLaneError as exc:
            raise TurnControlError(exc.code, exc.message, **exc.details) from exc
        except ValueError as exc:
            raise TurnControlError(
                "DELTA_VERIFICATION_SHARED_TASK_BINDING_INVALID",
                "The exact shared task binding could not be created or refreshed.",
                error_type=type(exc).__name__,
            ) from exc
        binding = seal_exact_task_project_session_binding(
            exact_root,
            project_id=project_id,
            evidence_session_id=evidence_session_id,
            expected_active_task_id=expected_active_task_id,
        )
    active_plan_row = {
        **dict(binding["active_plan_row"]),
        "task_contract_sha256": task_contract_sha256,
    }
    delta_body = {
        "schema": "evidence-lane.per-delta-local-verification.v1",
        "status": "PASS",
        "receipt_id": receipt_id,
        "active_task_id": expected_active_task_id,
        "task_contract_sha256": task_contract_sha256,
        "dependency_generation": pointer.generation,
        "dependency_task_ids": dependencies,
        "dependency_statuses": dependency_statuses,
        "pre_worktree_sha256": pre_worktree_sha256,
        "post_worktree_sha256": post_worktree_sha256,
        "worktree_changed": pre_worktree_sha256 != post_worktree_sha256,
        "changed_paths": normalized_paths,
        "changed_paths_sha256": sha256_bytes(canonical_json_bytes(normalized_paths)),
        "test_runs": normalized_runs,
        "test_runs_sha256": sha256_bytes(canonical_json_bytes(normalized_runs)),
        "source_catalog": source_catalog,
        "install_disposition": (
            dict(adaptive_receipt.get("install_disposition") or {})
            if isinstance(adaptive_receipt, dict)
            else None
        ),
        "adaptive_delta_exit_receipt_sha256": install_deferral[
            "receipt_sha256"
        ]
        if install_deferral["valid"] is True
        else None,
        "acceptance_checks": acceptance_checks,
        "acceptance_checks_sha256": sha256_bytes(
            canonical_json_bytes(acceptance_checks)
        ),
        "limitations": limitations,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "git_executed": False,
        "install_executed": False,
    }
    delta_receipt = {
        **delta_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(delta_body)),
    }
    sealed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    receipt_path = (
        project_root / "receipts" / "delta-verification" / f"{receipt_id}.json"
    )
    receipt_body = {
        "schema": "evidence-lane.per-delta-local-verification-checkpoint.v1",
        "status": "PASS",
        "verification_kind": "PER_DELTA_LOCAL_VERIFICATION",
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "governed_host_session_id": binding["governed_host_session_id"],
        "active_plan_row": active_plan_row,
        "accepted_pointer": binding["accepted_pointer"],
        "running_plugin": binding["running_plugin"],
        "adaptive_delta_exit_receipt": (
            adaptive_receipt if install_deferral["valid"] is True else None
        ),
        "runtime_task_id": dict(session.get("task") or {}).get("task_id"),
        "run_id": metadata.get("run_id"),
        "delta_verification": delta_receipt,
        "exact_binding_receipt_sha256": binding["receipt_sha256"],
        "identity_basis": (
            "EXACT_CODEX_TASK_BINDING_PLUS_LIVE_PER_DELTA_CODE_TEST_EVIDENCE"
        ),
        "task_title_used": False,
        "cwd_used": False,
        "candidate_created": False,
        "pending_hil": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "sealed_at": sealed_at,
        "receipt_path": receipt_path.relative_to(project_root).as_posix(),
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    if receipt_path.is_file():
        _require(
            _json(receipt_path) == receipt,
            "DELTA_VERIFICATION_RECEIPT_CONFLICT",
            "The per-Delta receipt ID already binds different evidence.",
        )
    else:
        atomic_write_json(receipt_path, receipt)
    return receipt


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
        (bound.get("session", {}).get("metadata") or {}).get("execution_profile") or {}
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
        (bound.get("session", {}).get("metadata") or {}).get("persistence_route") or {}
    )
    tunnel_requirement = str(
        persistence_route.get("tunnel_requirement") or "HOST_CAPABILITY_UNSPECIFIED"
    )
    host_tool_transport = str(
        persistence_route.get("host_tool_transport") or "HOST_CAPABILITY_UNSPECIFIED"
    )
    native_mcp_available = persistence_route.get("native_mcp_available") is True
    tool_gap_route = persistence_route.get("tool_gap_route") is True
    interactive_tunnel = (
        tunnel_requirement == "REQUIRED_FOR_HOST_TOOL_GAP"
        and host_tool_transport == "HOST_TOOL_GAP"
        and tool_gap_route
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
        "vm_lifetime": persistence_route.get("vm_lifetime", "LOCAL_OR_PERSISTENT"),
        "tunnel_role": (
            "INTERACTIVE_ENVIRONMENT_PRECONDITION_NOT_LIFECYCLE_AUTHORITY"
            if interactive_tunnel
            else "NOT_IN_CODEX_NATIVE_LIFECYCLE_CRITICAL_PATH"
        ),
        "tunnel_requirement": tunnel_requirement,
        "host_tool_transport": host_tool_transport,
        "native_mcp_available": native_mcp_available,
        "tool_gap_route": tool_gap_route,
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
        change_identity = calculate_worktree_change_identity(repository)
        status = run_git(
            repository,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        ).stdout
        numstat = run_git(
            repository,
            ["diff", "--numstat", "HEAD", "--"],
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

    line_additions = 0
    line_deletions = 0
    binary_change_count = 0
    tracked_diff_path_count = 0
    for line in numstat.splitlines():
        columns = line.split("\t", 2)
        if len(columns) != 3:
            continue
        tracked_diff_path_count += 1
        added, deleted, _ = columns
        if added == "-" or deleted == "-":
            binary_change_count += 1
            continue
        line_additions += int(added)
        line_deletions += int(deleted)

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
        "git_change_identity_schema": change_identity["schema"],
        "git_change_identity_sha256": change_identity["working_identity_sha256"],
        "complete_path_set_sha256": change_identity["complete_path_set_sha256"],
        "complete_path_count": change_identity["path_count"],
        "status_porcelain_v2_sha256": change_identity["status_sha256"],
        "cached_diff_sha256": change_identity["cached_diff_sha256"],
        "unstaged_diff_sha256": change_identity["unstaged_diff_sha256"],
        "tracked_head_diff_sha256": change_identity["tracked_head_diff_sha256"],
        "dirty_path_set_sha256": change_identity["dirty_path_set_sha256"],
        "dirty_content_sha256": change_identity["dirty_content_sha256"],
        "tracked_dirty_content_sha256": change_identity[
            "tracked_dirty_content_sha256"
        ],
        "untracked_content_sha256": change_identity["untracked_content_sha256"],
        "staged_path_count": change_identity["staged_path_count"],
        "unstaged_path_count": change_identity["unstaged_path_count"],
        "content_identity_count": change_identity["content_identity_count"],
        "raw_dirty_paths_persisted": change_identity["raw_paths_persisted"],
        "ignored_paths_included": change_identity["ignored_paths_included"],
        "is_clean": identity.is_clean,
        "changed_path_count": len(changed_paths),
        "untracked_path_count": sum(row["status"] == "??" for row in changed_paths),
        "tracked_diff_path_count": tracked_diff_path_count,
        "line_additions": line_additions,
        "line_deletions": line_deletions,
        "binary_change_count": binary_change_count,
        "line_delta_scope": "TRACKED_HEAD_DIFF_WITH_EXACT_DIRTY_CONTENT_IDENTITY",
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
            "git_change_identity_schema",
            "git_change_identity_sha256",
            "complete_path_set_sha256",
            "complete_path_count",
            "status_porcelain_v2_sha256",
            "cached_diff_sha256",
            "unstaged_diff_sha256",
            "tracked_head_diff_sha256",
            "dirty_path_set_sha256",
            "dirty_content_sha256",
            "tracked_dirty_content_sha256",
            "untracked_content_sha256",
            "staged_path_count",
            "unstaged_path_count",
            "content_identity_count",
            "raw_dirty_paths_persisted",
            "ignored_paths_included",
            "is_clean",
            "changed_path_count",
            "untracked_path_count",
            "tracked_diff_path_count",
            "line_additions",
            "line_deletions",
            "binary_change_count",
            "line_delta_scope",
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
        phase_value
        in {"SESSION_START", "TURN_PREPARE", "POST_TOOL_USE", "TURN_COMMIT"},
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
        dict(row) for row in additive.get("changes") or [] if isinstance(row, dict)
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
            "tracked_diff_path_count": source.get("tracked_diff_path_count"),
            "line_additions": source.get("line_additions"),
            "line_deletions": source.get("line_deletions"),
            "binary_change_count": source.get("binary_change_count"),
            "line_delta_scope": source.get("line_delta_scope"),
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
                "projection_sha256",
            )
            if receipt.get(key) is not None
        },
        "tool_projection": {
            "tool_name": receipt.get("tool_name"),
            "tool_use_id_sha256": receipt.get("tool_use_id_sha256"),
            "read_only_projection": receipt.get("read_only_projection"),
            "tool_input_stored": False,
            "tool_response_stored": False,
        }
        if phase_value == "POST_TOOL_USE"
        else None,
        "host_binding": {
            "state": dict(receipt.get("host_binding") or {}).get("state"),
            "alias_receipt_sha256": dict(receipt.get("host_binding") or {}).get(
                "alias_receipt_sha256"
            ),
            "binding_epoch_sha256": dict(receipt.get("host_binding") or {}).get(
                "binding_epoch_sha256"
            ),
            "raw_host_identity_stored": False,
            "raw_transcript_path_stored": False,
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


def persistent_change_system_message(notice: dict[str, Any]) -> str:
    """Return one bounded secret-free host warning for the current change.

    ``systemMessage`` is the documented hook output that Codex surfaces in its
    UI. The full sealed projection remains in ``additionalContext``; this string
    stays short enough to remain readable near the composer.
    """

    paired = dict(notice.get("paired_step_task_list") or {})
    linked = dict(notice.get("linked_delta_status") or {})
    active_delta = dict(linked.get("active_delta") or {})
    source = dict(notice.get("source_change_status") or {})
    position = paired.get("active_position") or "?"
    task_count = paired.get("task_count") or "?"
    active_task_id = str(paired.get("active_task_id") or "NO_ACTIVE_TASK")
    delta_id = str(active_delta.get("delta_id") or "NO_LINKED_DELTA")
    changed_paths = int(source.get("changed_path_count") or 0)
    additions = int(source.get("line_additions") or 0)
    deletions = int(source.get("line_deletions") or 0)
    untracked = int(source.get("untracked_path_count") or 0)
    binary = int(source.get("binary_change_count") or 0)
    phase = str(notice.get("phase") or "CURRENT")
    seal = str(notice.get("notice_sha256") or "UNSEALED")[:16]
    return (
        "Evidence Lane CURRENT CHANGE | "
        f"Step {position}/{task_count} | {active_task_id} | "
        f"Delta {delta_id} | {changed_paths} files "
        f"(+{additions}/-{deletions}, {binary} binary, {untracked} untracked) | "
        f"{phase} | seal {seal}"
    )


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


def _behavior_query_handoff_receipt(
    *,
    binding: dict[str, Any],
    visible_text: str,
) -> dict[str, Any]:
    """Seal the handoff from lifecycle PREPARE to skill-owned behavior.

    PREPARE intentionally performs no project or accepted-PV retrieval. The
    active Evidence Lane skill must make the visible native MCP reads and then
    refresh the host Step Task List. Keeping the compatibility receipt field
    avoids rewriting existing turn-control SQLite while making ownership and
    the unsatisfied behavior gate explicit.
    """

    receipt = {
        "schema": "evidence-lane.codex-native-behavior-query-handoff.v1",
        "outcome": "PENDING_NATIVE_SKILL_QUERY",
        "query_sha256": sha256_bytes(visible_text.encode("utf-8")),
        "query_terms_present": False,
        "accepted_pv": binding["accepted_pv"],
        "pointer_generation": binding["pointer_generation"],
        "entry_manifest_sha256": binding["entry_manifest_sha256"],
        "lineage_results": [],
        "accepted_code_results": [],
        "lineage_result_count": 0,
        "accepted_code_result_count": 0,
        "bounded_result_limit_per_authority": 0,
        "candidate_overlay_used": False,
        "scrollback_used": False,
        "transcript_used": False,
        "no_hit_is_valid": False,
        "query_owner": "SKILL",
        "hook_lookup_performed": False,
        "native_behavior_query_required": True,
        "native_behavior_query_satisfied": False,
        "required_native_read_sequence": [
            "pv_status",
            "pv_task_backlog",
            "pv_query",
        ],
        "host_plan_refresh_owner": "SKILL",
        "host_plan_tool": "update_plan",
    }
    receipt["retrieval_receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt


def _connection(project_root: Path) -> sqlite3.Connection:
    path = resolved_chat_lineage_root(project_root) / "codex_turn_control.sqlite"
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

    if entry.get("input_origin") == _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN:
        return {
            "state": "NOT_APPLICABLE",
            "reason": "NO_NEW_VISIBLE_USER_RESEARCH_QUESTION",
            "access_scope": _PROJECT_TASK_PRIVATE_ANALYSIS,
            "cross_project_retrieval": False,
            "synthetic_question_created": False,
        }
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


def _goal_usage_observation(
    host_payload: dict[str, Any],
    *,
    binding: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize trustworthy Goal totals and independently exposed components."""

    raw = host_payload.get("goal_usage")
    if raw is None:
        observation: dict[str, Any] = {
            "schema": "evidence-lane.goal-usage-observation.v3",
            "availability": "UNAVAILABLE",
            "reason": "HOST_GOAL_ACCOUNTED_COUNTER_NOT_EXPOSED",
            "accounting_basis": "HOST_EXPOSED_COMPONENTS_AND_FINAL_TOTAL_ONLY",
            "goal_id": None,
            "metric_semantics": None,
            "goal_accounted_tokens": None,
            "component_values": {key: None for key in TOKEN_COMPONENT_KEYS},
            "host_total_tokens": None,
            "profile_observed_context": {
                "availability": "UNAVAILABLE",
                "reason": "PROFILE_OBSERVATION_NOT_SUPPLIED",
                "causal_attribution_inferred": False,
            },
            "provided_fields": [],
            "aggregation_rule": (
                "HOST_TOTAL_ELSE_NON_OVERLAPPING_AGENT_TOTALS_ELSE_INPUT_PLUS_OUTPUT"
            ),
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
    provenance = safe.get("provenance")
    raw_profile_observations = safe.get("profile_observations")
    raw_user_attribution = safe.get("user_exclusive_attribution")
    profile_observed_context: dict[str, Any]
    if raw_profile_observations is None and raw_user_attribution is None:
        profile_observed_context = {
            "availability": "UNAVAILABLE",
            "reason": "PROFILE_OBSERVATION_NOT_SUPPLIED",
            "causal_attribution_inferred": False,
        }
    else:
        _require(
            isinstance(raw_profile_observations, list)
            and isinstance(raw_user_attribution, str)
            and bool(raw_user_attribution.strip())
            and isinstance(binding, dict),
            "TURN_CONTROL_PROFILE_OBSERVED_CONTEXT_INVALID",
            "Profile-observed usage requires observations, a user attribution, and exact governed binding.",
        )
        try:
            profile_observed_context = build_profile_observed_usage_context(
                observations=cast(list[Mapping[str, object]], raw_profile_observations),
                user_exclusive_attribution=raw_user_attribution,
                binding=cast(Mapping[str, object], binding),
            )
        except (TypeError, ValueError) as exc:
            raise TurnControlError(
                "TURN_CONTROL_PROFILE_OBSERVED_CONTEXT_INVALID",
                str(exc),
            ) from exc
    raw_components = safe.get("components")
    if raw_components is None:
        raw_components = {}
    _require(
        isinstance(raw_components, dict),
        "TURN_CONTROL_GOAL_USAGE_COMPONENTS_INVALID",
        "Goal usage components must be one structured object when supplied.",
    )
    raw_cumulative_samples = safe.get("cumulative_token_samples")
    reset_accounting: dict[str, Any] | None = None
    reset_component_values: dict[str, int] = {}
    if raw_cumulative_samples is not None:
        try:
            reset_accounting = build_reset_aware_epoch_accounting(
                cumulative_samples=raw_cumulative_samples,
                timezone_name=str(
                    safe.get("daily_reconciliation_timezone")
                    or "America/New_York"
                ),
            )
        except (TypeError, ValueError) as exc:
            raise TurnControlError(
                "TURN_CONTROL_RESET_AWARE_GOAL_USAGE_INVALID",
                str(exc),
            ) from exc
        reset_totals = dict(reset_accounting["totals"])
        reset_component_values = {
            "input_tokens": int(reset_totals["input_tokens"]),
            "cached_input_tokens": int(reset_totals["cached_input_tokens"]),
            "output_tokens": int(reset_totals["output_tokens"]),
            "reasoning_tokens": int(reset_totals["reasoning_output_tokens"]),
        }
    component_values: dict[str, int | None] = {}
    for key in TOKEN_COMPONENT_KEYS:
        aliases = ("agent_tokens",) if key == "main_agent_tokens" else ()
        supplied = next(
            (
                source[name]
                for source in (raw_components, safe)
                for name in (key, *aliases)
                if name in source
            ),
            None,
        )
        _require(
            supplied is None
            or supplied == "UNAVAILABLE"
            or (
                isinstance(supplied, int)
                and not isinstance(supplied, bool)
                and supplied >= 0
            ),
            "TURN_CONTROL_GOAL_USAGE_COMPONENT_INVALID",
            "Each supplied Goal usage component must be a non-negative integer or UNAVAILABLE.",
            component=key,
        )
        component_values[key] = (
            supplied
            if isinstance(supplied, int) and not isinstance(supplied, bool)
            else None
        )
        reset_value = reset_component_values.get(key)
        if reset_value is not None:
            _require(
                component_values[key] in {None, reset_value},
                "TURN_CONTROL_RESET_AWARE_COMPONENT_MISMATCH",
                "A supplied Goal component does not match reset-aware epoch accounting.",
                component=key,
            )
            component_values[key] = reset_value

    legacy_total = safe.get("goal_accounted_tokens")
    host_total = safe.get("total_tokens", legacy_total)
    _require(
        host_total is None
        or host_total == "UNAVAILABLE"
        or (
            isinstance(host_total, int)
            and not isinstance(host_total, bool)
            and host_total >= 0
        ),
        "TURN_CONTROL_GOAL_TOTAL_INVALID",
        "A supplied Goal final total must be a non-negative integer or UNAVAILABLE.",
    )
    exact_host_total = (
        host_total
        if isinstance(host_total, int) and not isinstance(host_total, bool)
        else None
    )
    main_agent = component_values["main_agent_tokens"]
    subagent = component_values["subagent_tokens"]
    input_tokens = component_values["input_tokens"]
    output_tokens = component_values["output_tokens"]
    if exact_host_total is not None:
        aggregate_tokens = exact_host_total
        aggregate_basis = "HOST_EXPOSED_FINAL_TOTAL"
    elif main_agent is not None and subagent is not None:
        aggregate_tokens = main_agent + subagent
        aggregate_basis = "SUM_NON_OVERLAPPING_MAIN_AGENT_AND_SUBAGENT_TOTALS"
    elif (
        main_agent is None
        and subagent is None
        and input_tokens is not None
        and output_tokens is not None
    ):
        aggregate_tokens = input_tokens + output_tokens
        aggregate_basis = "SUM_NON_OVERLAPPING_INPUT_AND_OUTPUT"
    else:
        aggregate_tokens = None
        aggregate_basis = "UNAVAILABLE_INSUFFICIENT_NON_OVERLAPPING_COMPONENTS"

    trustworthy_identity = (
        bool(goal_id)
        and semantics in _GOAL_USAGE_SEMANTICS
        and isinstance(provenance, dict)
        and bool(str(provenance.get("source") or "").strip())
    )
    if trustworthy_identity:
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
            "schema": "evidence-lane.goal-usage-observation.v3",
            "availability": (
                "AVAILABLE" if aggregate_tokens is not None else "UNAVAILABLE"
            ),
            "reason": (
                None
                if aggregate_tokens is not None
                else "FINAL_AGGREGATE_NOT_EXPOSED_OR_PROVABLY_DERIVABLE"
            ),
            "accounting_basis": "HOST_EXPOSED_COMPONENTS_AND_FINAL_TOTAL_ONLY",
            "goal_id": goal_id,
            "metric_semantics": semantics,
            "goal_accounted_tokens": aggregate_tokens,
            "component_values": component_values,
            "host_total_tokens": exact_host_total,
            "profile_observed_context": profile_observed_context,
            "reset_aware_epoch_accounting": (
                reset_accounting
                if reset_accounting is not None
                else {
                    "schema": "evidence-lane.reset-aware-token-epochs.v1",
                    "status": "UNAVAILABLE",
                    "reason": "CUMULATIVE_TOKEN_SAMPLES_NOT_SUPPLIED",
                    "final_minus_initial_used": False,
                }
            ),
            "native_turn_reconciliation": (
                safe.get("native_turn_evidence")
                if isinstance(safe.get("native_turn_evidence"), dict)
                else {
                    "status": "UNAVAILABLE",
                    "unavailable_values_coerced_to_zero": False,
                }
            ),
            "aggregate_basis": aggregate_basis,
            "elapsed_seconds": elapsed_seconds,
            "provenance": provenance,
            "aggregation_rule": (
                "HOST_TOTAL_ELSE_NON_OVERLAPPING_AGENT_TOTALS_ELSE_INPUT_PLUS_OUTPUT"
            ),
            "cached_input_is_subset_of_input": True,
            "reasoning_is_subset_of_output": True,
            "output_only_is_total": False,
            "task_status_effect": "NONE",
            "goal_completion_effect": "NONE",
            "exact_counts_preserved": True,
            "private_reasoning_stored": False,
        }
    else:
        observation = {
            "schema": "evidence-lane.goal-usage-observation.v3",
            "availability": "UNAVAILABLE",
            "reason": "INCOMPLETE_OR_UNTRUSTWORTHY_GOAL_USAGE_PROVENANCE",
            "accounting_basis": "HOST_EXPOSED_COMPONENTS_AND_FINAL_TOTAL_ONLY",
            "goal_id": None,
            "metric_semantics": None,
            "goal_accounted_tokens": None,
            "component_values": component_values,
            "host_total_tokens": exact_host_total,
            "profile_observed_context": profile_observed_context,
            "reset_aware_epoch_accounting": (
                reset_accounting
                if reset_accounting is not None
                else {
                    "schema": "evidence-lane.reset-aware-token-epochs.v1",
                    "status": "UNAVAILABLE",
                    "reason": "CUMULATIVE_TOKEN_SAMPLES_NOT_SUPPLIED",
                    "final_minus_initial_used": False,
                }
            ),
            "native_turn_reconciliation": (
                safe.get("native_turn_evidence")
                if isinstance(safe.get("native_turn_evidence"), dict)
                else {
                    "status": "UNAVAILABLE",
                    "unavailable_values_coerced_to_zero": False,
                }
            ),
            "provided_fields": sorted(str(key) for key in safe),
            "aggregation_rule": (
                "HOST_TOTAL_ELSE_NON_OVERLAPPING_AGENT_TOTALS_ELSE_INPUT_PLUS_OUTPUT"
            ),
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
        component_accounting = None
        provenance = observation.get("provenance")
        if isinstance(provenance, dict) and str(provenance.get("source") or "").strip():
            component_accounting = build_component_token_accounting(
                components=cast(
                    dict[str, object], observation.get("component_values") or {}
                ),
                total_tokens=observation.get("host_total_tokens"),
                provenance=provenance,
                binding={
                    "project_id": entry["project_id"],
                    "evidence_session_id": entry["evidence_session_id"],
                    "task_id": entry["task_id"],
                    "host_session_id_sha256": sha256_bytes(
                        str(entry["host_session_id"]).encode("utf-8")
                    ),
                },
            )
        record = {
            "schema": "evidence-lane.project-goal-usage-record.v2",
            "project_id": entry["project_id"],
            "evidence_session_id": entry["evidence_session_id"],
            "task_id": entry["task_id"],
            "plan_task_id": entry.get("plan_task_id"),
            "host_session_id_sha256": sha256_bytes(
                str(entry["host_session_id"]).encode("utf-8")
            ),
            "turn_id": entry["turn_id"],
            "prompt_index": entry["prompt_index"],
            "control_record_sha256": entry["control_record_sha256"],
            "observation": observation,
            "observation_sha256": observation["observation_sha256"],
            "component_accounting": component_accounting,
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
        "component_accounting": record.get("component_accounting"),
        "reset_aware_epoch_accounting": observation.get(
            "reset_aware_epoch_accounting"
        ),
        "native_turn_reconciliation": observation.get(
            "native_turn_reconciliation"
        ),
        "profile_observed_context": observation.get("profile_observed_context"),
        "aggregation_rule": observation.get("aggregation_rule"),
        "project_local_only": True,
        "private_reasoning_stored": False,
    }


@contextmanager
def _control_lock(project_root: Path):
    """Serialize one project's Prepare/Commit projection without a second writer."""

    path = resolved_chat_lineage_root(project_root) / "codex_turn_control.lock"
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


_GOAL_CONTEXT_MARKER = re.compile(
    r"^\s*<codex_internal_context\b(?=[^>]*\bsource=(?:\"goal\"|'goal'))[^>]*>",
    flags=re.IGNORECASE,
)


def _goal_context_marker_present(visible_input: str) -> bool:
    return _GOAL_CONTEXT_MARKER.match(visible_input) is not None


def _input_kind(host_payload: dict[str, Any]) -> str:
    """Return only the classification knowable without sealed turn history.

    This is used by deterministic gap receipts.  A caller's ``source`` or
    ``is_steer``/``is_goal`` flags are deliberately ignored.
    """

    visible = _turn_redact_text(str(host_payload.get("prompt") or ""))
    return "goal" if _goal_context_marker_present(visible) else "user_prompt"


def _derive_input_kind(
    visible_input: str,
    *,
    prior_same_turn_input: bool,
) -> tuple[str, str]:
    """Derive prompt/steer/Goal from native input plus sealed turn state."""

    if _goal_context_marker_present(visible_input):
        return "goal", "CODEX_INTERNAL_GOAL_CONTEXT_MARKER"
    if prior_same_turn_input:
        return "steer", "PRIOR_SEALED_INPUT_FOR_SAME_HOST_TURN"
    return "user_prompt", "FIRST_SEALED_INPUT_FOR_HOST_TURN"


def _capture_dispatch(
    host_payload: dict[str, Any], *, input_kind: str, classification_basis: str
) -> dict[str, Any]:
    """Seal truthful host-dispatch provenance for one visible input.

    Codex's pending-input dispatcher invokes ``UserPromptSubmit`` for each
    ``TurnInput::UserInput`` before the model sees it. The hook adapter can
    prove that it executed against a host-shaped payload; only a separate
    installed-host acceptance correlation may claim independent dispatch
    proof. Goal control uses ``thread/goal/set`` and fails closed here.
    """

    expected_by_kind: dict[str, dict[str, Any]] = {
        "user_prompt": {
            "surface": "USER_PROMPT_CORRECTION_OR_HIL_TOKEN",
            "host_route": "turn/start -> inspect_pending_input(TurnInput::UserInput)",
            "native_hook_event": "UserPromptSubmit",
        },
        "steer": {
            "surface": "MID_GOAL_STEER",
            "host_route": "turn/steer -> inspect_pending_input(TurnInput::UserInput)",
            "native_hook_event": "UserPromptSubmit",
        },
        "goal": {
            "surface": "GOAL_CONTINUATION",
            "host_route": "thread/goal/set (not TurnInput::UserInput)",
            "native_hook_event": None,
        },
    }
    expected = expected_by_kind[input_kind]
    _require(
        input_kind != "goal",
        "TURN_CONTROL_GOAL_PRE_REASONING_HOOK_UNAVAILABLE",
        "Codex Goal control bypasses UserPromptSubmit; governed Goal PREPARE is unavailable and must fail closed.",
        surface=expected["surface"],
        host_route=expected["host_route"],
        host_capability="UNAVAILABLE",
    )
    supplied = host_payload.get("evidence_lane_capture_dispatch")
    if not isinstance(supplied, dict):
        return {
            **expected,
            "native_dispatch_surface": None,
            "native_dispatch_route": None,
            "host_dispatch_supported": False,
            "pre_reasoning_dispatch_proven": False,
            "adapter_invocation_observed": False,
            "installed_host_dispatch_independently_proven": False,
            "input_kind_derived_from_sealed_state": True,
            "classification_basis": classification_basis,
            "caller_input_kind_authority": False,
            "state": "CALLER_HAS_NO_NATIVE_DISPATCH_RECEIPT",
        }
    exact = {
        "native_dispatch_surface": str(supplied.get("surface") or ""),
        "native_dispatch_route": str(supplied.get("host_route") or ""),
        "native_hook_event": supplied.get("native_hook_event"),
        "host_payload_hook_event_name": str(
            supplied.get("host_payload_hook_event_name") or ""
        ),
        "adapter_invocation_observed": supplied.get("adapter_invocation_observed")
        is True,
        "installed_host_dispatch_independently_proven": supplied.get(
            "installed_host_dispatch_independently_proven"
        )
        is True,
        "input_kind_derived_from_sealed_state": supplied.get(
            "input_kind_derived_from_sealed_state"
        )
        is True,
        "caller_input_kind_authority": supplied.get("caller_input_kind_authority")
        is True,
    }
    proven = (
        exact["native_dispatch_surface"] == "PENDING_VISIBLE_USER_INPUT"
        and exact["native_dispatch_route"]
        == "inspect_pending_input(TurnInput::UserInput)"
        and exact["native_hook_event"] == expected["native_hook_event"]
        and exact["host_payload_hook_event_name"] == "UserPromptSubmit"
        and str(host_payload.get("hook_event_name") or "") == "UserPromptSubmit"
        and exact["adapter_invocation_observed"] is True
        and exact["installed_host_dispatch_independently_proven"] is False
        and exact["input_kind_derived_from_sealed_state"] is True
        and supplied.get("caller_input_kind_authority") is False
        and exact["caller_input_kind_authority"] is False
    )
    _require(
        proven,
        "TURN_CONTROL_NATIVE_DISPATCH_RECEIPT_INVALID",
        "A visible input cannot claim pre-reasoning capture through the wrong host surface.",
        input_kind=input_kind,
        expected_surface="PENDING_VISIBLE_USER_INPUT",
        supplied_surface=exact["native_dispatch_surface"],
    )
    return {
        **expected,
        **exact,
        "host_dispatch_supported": True,
        "pre_reasoning_dispatch_proven": True,
        "pre_reasoning_proof_basis": "VALIDATED_USERPROMPTSUBMIT_HOST_PAYLOAD_AND_ADAPTER_INVOCATION",
        "independent_installed_host_proof_required": True,
        "classification_basis": classification_basis,
        "state": "USERPROMPTSUBMIT_ADAPTER_INVOKED",
    }


def _goal_continuation_dispatch(
    authority: dict[str, Any],
) -> dict[str, Any]:
    """Describe the truthful non-prompt route used by automatic Goal work."""

    _require(
        authority.get("schema")
        == "evidence-lane.codex-task-goal-continuation-authority.v1"
        and authority.get("state") == "NATIVE_ACTIVE_TASK_GOAL_BINDING_VERIFIED"
        and authority.get("input_origin") == _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN
        and authority.get("synthetic_prompt_used") is False
        and authority.get("user_prompt_submit_observed") is False
        and _SHA256_RE.fullmatch(str(authority.get("receipt_sha256") or "").upper())
        is not None,
        "TURN_CONTROL_GOAL_CONTINUATION_AUTHORITY_INVALID",
        "A Goal continuation entry requires one verified native task/Goal authority.",
    )
    return {
        "surface": "GOAL_CONTINUATION",
        "host_route": "thread/goal/set -> first PreToolUse boundary",
        "native_hook_event": "PreToolUse",
        "native_dispatch_surface": "FIRST_GOAL_TOOL_BOUNDARY",
        "native_dispatch_route": "PreToolUse",
        "host_dispatch_supported": True,
        "pre_reasoning_dispatch_proven": False,
        "pre_reasoning_proof_basis": "NOT_CLAIMED_GOAL_BYPASSES_USERPROMPTSUBMIT",
        "tool_boundary_continuation_proven": True,
        "tool_boundary_proof_basis": "NATIVE_ACTIVE_GOAL_EXACT_TASK_BINDING",
        "user_prompt_submit_observed": False,
        "adapter_invocation_observed": True,
        "installed_host_dispatch_independently_proven": False,
        "input_kind_derived_from_sealed_state": True,
        "classification_basis": _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN,
        "caller_input_kind_authority": False,
        "synthetic_prompt_used": False,
        "task_goal_authority_receipt_sha256": authority["receipt_sha256"],
        "state": "GOAL_CONTINUATION_BOUND_AT_FIRST_TOOL",
    }


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
        and int(session.get("accepted_pointer_generation") or 0) == pointer_generation
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
        same_turn_records = [
            row
            for row in records
            if row.get("host_session_id") == host_session_id
            and row.get("turn_id") == turn_id
        ]
        duplicate = next(
            (
                row
                for row in same_turn_records
                if row.get("prompt_sha256_after_redaction") == visible_input_sha256
            ),
            None,
        )
        if duplicate is None:
            input_kind, classification_basis = _derive_input_kind(
                visible_input,
                prior_same_turn_input=bool(same_turn_records),
            )
            capture_dispatch = _capture_dispatch(
                host_payload,
                input_kind=input_kind,
                classification_basis=classification_basis,
            )
            prior = records[-1] if records else None
            prompt_index = int(prior.get("prompt_index") or 0) + 1 if prior else 1
            recorded_at = _now()
            lineage_telemetry = _response_telemetry(host_payload)
            record = {
                "schema": "evidence-lane.prompt-index.v1",
                "host_session_id": host_session_id,
                "turn_id": turn_id,
                "input_kind": input_kind,
                "input_kind_classification_basis": classification_basis,
                "capture_dispatch": capture_dispatch,
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
                "lineage_host_identity": _lineage_host_identity(host_payload),
                "lineage_model": lineage_telemetry["model"],
                "lineage_submodel": lineage_telemetry["submodel"],
                "lineage_token_metrics": lineage_telemetry["token_metrics"],
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
            input_kind = str(record.get("input_kind") or "user_prompt")
            classification_basis = str(
                record.get("input_kind_classification_basis")
                or (record.get("capture_dispatch") or {}).get("classification_basis")
                or "LEGACY_EXACT_INPUT_REPLAY"
            )
            capture_dispatch = _capture_dispatch(
                host_payload,
                input_kind=input_kind,
                classification_basis=classification_basis,
            )
            if isinstance(record.get("capture_dispatch"), dict):
                _require(
                    record["capture_dispatch"] == capture_dispatch,
                    "TURN_CONTROL_NATIVE_DISPATCH_RECEIPT_DRIFT",
                    "The replayed visible input no longer matches its native dispatch receipt.",
                )
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
            resolved_chat_lineage_root(project_root) / f"{evidence_session_id}.jsonl"
        )
        host_identity = record.get("lineage_host_identity")
        lineage_model_value = record.get("lineage_model")
        lineage_submodel_value = record.get("lineage_submodel")
        lineage_metrics_value = record.get("lineage_token_metrics")
        lineage_model = (
            lineage_model_value if isinstance(lineage_model_value, str) else None
        )
        lineage_submodel = (
            lineage_submodel_value if isinstance(lineage_submodel_value, str) else None
        )
        lineage_token_metrics = (
            cast(dict[str, Any], lineage_metrics_value)
            if isinstance(lineage_metrics_value, dict)
            else {"availability": "UNAVAILABLE"}
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
                    **(
                        {"host_identity": host_identity}
                        if isinstance(host_identity, dict)
                        else {}
                    ),
                    "strict_turn_control_active": False,
                    "private_reasoning_excluded": True,
                },
                occurred_at=recorded_at,
                session_id=evidence_session_id,
                task_id=task_id,
                event_id=event_id,
                actor_type="user",
                model=lineage_model,
                submodel=lineage_submodel,
                token_metrics=lineage_token_metrics,
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
        "input_kind_classification_basis": classification_basis,
        "capture_dispatch": capture_dispatch,
        "pre_reasoning_host_dispatch_proven": capture_dispatch[
            "pre_reasoning_dispatch_proven"
        ],
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
    lineage_path = (
        resolved_chat_lineage_root(project_root)
        / f"{entry['evidence_session_id']}.jsonl"
    )
    lineage = ChatLineage(lineage_path)
    sealed_goal_continuation = (
        entry.get("input_origin") == _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN
    )
    input_event_id = (
        "evt_"
        + sha256_bytes(
            (
                str(entry["prompt_record_sha256"])
                + (
                    "\0sealed-goal-continuation"
                    if sealed_goal_continuation
                    else "\0visible-input"
                )
            ).encode("utf-8")
        )[:26].lower()
    )
    input_visible_payload = {
        "turn_id": entry["turn_id"],
        "prompt_index": entry["prompt_index"],
        "input_kind": entry["input_kind"],
        "input_origin": entry.get("input_origin", "VISIBLE_USER_INPUT"),
        "capture_dispatch": entry["capture_dispatch"],
        **(
            {
                "continuation_descriptor_after_redaction": entry[
                    "visible_input_after_redaction"
                ],
                "continuation_descriptor_sha256_after_redaction": entry[
                    "visible_input_sha256_after_redaction"
                ],
                "task_goal_authority": entry["task_goal_authority"],
                "visible_user_input_stored": False,
                "raw_goal_objective_stored": False,
                "synthetic_prompt_used": False,
            }
            if sealed_goal_continuation
            else {
                "visible_input_after_redaction": entry["visible_input_after_redaction"],
                "visible_input_sha256_after_redaction": entry[
                    "visible_input_sha256_after_redaction"
                ],
            }
        ),
        "attachment_identities": entry["attachment_identities"],
        "prompt_record_sha256": entry["prompt_record_sha256"],
        "entry_slip": entry["entry_slip"],
        **(
            {"host_identity": entry["lineage_host_identity"]}
            if isinstance(entry.get("lineage_host_identity"), dict)
            else {}
        ),
        "private_reasoning_excluded": True,
    }
    input_event = lineage.append(
        event_type=(
            "turn.sealed_goal_continuation"
            if sealed_goal_continuation
            else {
                "steer": "turn.visible_user_steer",
                "goal": "turn.visible_user_goal",
            }.get(str(entry["input_kind"]), "turn.visible_user_prompt")
        ),
        visible_payload=input_visible_payload,
        occurred_at=entry["prepared_at"],
        session_id=entry["evidence_session_id"],
        task_id=entry["task_id"],
        event_id=input_event_id,
        actor_type="system" if sealed_goal_continuation else "user",
        model=entry.get("lineage_model"),
        submodel=entry.get("lineage_submodel"),
        token_metrics=entry.get("lineage_token_metrics"),
    )
    prepare_event_id = (
        "evt_"
        + sha256_bytes(
            (str(entry["control_record_sha256"]) + "\0prepare").encode("utf-8")
        )[:26].lower()
    )
    prepare_event = lineage.append(
        event_type=(
            "turn.control_goal_continuation_entry"
            if sealed_goal_continuation
            else "turn.control_prepare"
        ),
        visible_payload={
            "state": entry.get("prepare_state", "PREPARED_NOT_COMMITTED"),
            "turn_id": entry["turn_id"],
            "input_kind": entry["input_kind"],
            "input_origin": entry.get("input_origin", "VISIBLE_USER_INPUT"),
            "capture_dispatch": entry["capture_dispatch"],
            "prompt_index": entry["prompt_index"],
            "prompt_record_sha256": entry["prompt_record_sha256"],
            "control_record_sha256": entry["control_record_sha256"],
            "binding_sha256": entry["binding_sha256"],
            "retrieval_receipt_sha256": entry["retrieval_receipt_sha256"],
            "behavior_query_owner": entry["retrieval"]["query_owner"],
            "hook_lookup_performed": entry["retrieval"]["hook_lookup_performed"],
            "native_behavior_query_required": entry["retrieval"][
                "native_behavior_query_required"
            ],
            "native_behavior_query_satisfied": entry["retrieval"][
                "native_behavior_query_satisfied"
            ],
            "prior_lineage_head_sha256": entry["prior_lineage_head_sha256"],
            "persistent_plan_row": entry["binding"]["persistent_plan_row"],
            "lane_classification": entry["lane_classification"],
            "mode_classification": entry["mode_classification"],
            "operators": entry["operators"],
            "gates": entry["gates"],
            "bounded_write_scope": entry["bounded_write_scope"],
            **(
                {
                    "task_goal_authority_receipt_sha256": entry[
                        "task_goal_authority"
                    ]["receipt_sha256"],
                    "raw_goal_objective_stored": False,
                    "synthetic_prompt_used": False,
                }
                if sealed_goal_continuation
                else {}
            ),
            "source_change_entry_sha256": (
                (entry.get("source_change_entry") or {}).get(
                    "source_change_snapshot_sha256"
                )
            ),
            **(
                {"host_identity": entry["lineage_host_identity"]}
                if isinstance(entry.get("lineage_host_identity"), dict)
                else {}
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
        model=entry.get("lineage_model"),
        submodel=entry.get("lineage_submodel"),
        token_metrics=entry.get("lineage_token_metrics"),
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
    lineage_host_identity = _lineage_host_identity(host_payload)
    lineage_telemetry = _response_telemetry(host_payload)
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
            existing_entries = [json.loads(row["record_json"]) for row in existing_rows]
            exact_entries = [
                candidate
                for candidate in existing_entries
                if candidate.get("visible_input_sha256_after_redaction")
                == visible_input_sha256
                and candidate.get("attachment_identities") == attachments
            ]
            _require(
                len(exact_entries) <= 1,
                "TURN_CONTROL_EXACT_INPUT_REPLAY_AMBIGUOUS",
                "The same visible input resolves to multiple PREPARE records for one host turn.",
                matching_records=len(exact_entries),
            )
            existing_entry = exact_entries[0] if exact_entries else None
            if existing_entry is not None:
                input_kind = str(existing_entry.get("input_kind") or "user_prompt")
                classification_basis = str(
                    (existing_entry.get("capture_dispatch") or {}).get(
                        "classification_basis"
                    )
                    or "LEGACY_EXACT_INPUT_REPLAY"
                )
                capture_dispatch = _capture_dispatch(
                    host_payload,
                    input_kind=input_kind,
                    classification_basis=classification_basis,
                )
                _require(
                    existing_entry.get("capture_dispatch") == capture_dispatch,
                    "TURN_CONTROL_NATIVE_DISPATCH_RECEIPT_DRIFT",
                    "The replayed PREPARE no longer matches its native dispatch receipt.",
                )
                _require(
                    existing_entry.get("binding_sha256") == binding["binding_sha256"],
                    "TURN_CONTROL_BINDING_DRIFT",
                    "The exact PREPARE exists but its project, pointer, Mode, or Plan binding drifted.",
                )
                entry = existing_entry
                action = "PREPARED_IDEMPOTENT_REUSE"
            else:
                prior_same_turn_prompt = any(
                    row.get("host_session_id") == host_session_id
                    and row.get("turn_id") == turn_id
                    for row in prompt_records
                )
                input_kind, classification_basis = _derive_input_kind(
                    visible_input,
                    prior_same_turn_input=bool(
                        existing_entries or prior_same_turn_prompt
                    ),
                )
                capture_dispatch = _capture_dispatch(
                    host_payload,
                    input_kind=input_kind,
                    classification_basis=classification_basis,
                )
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
                        if row.get("record_sha256") == latest["prompt_record_sha256"]
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
                    resolved_chat_lineage_root(project_root)
                    / f"{binding['evidence_session_id']}.jsonl"
                )
                lineage_events = ChatLineage(lineage_path).events()
                prior_lineage_head = (
                    lineage_events[-1].get("event_sha256") if lineage_events else None
                )
                retrieval = _behavior_query_handoff_receipt(
                    binding=binding,
                    visible_text=visible_input,
                )
                prepared_at = _now()
                prompt_record = {
                    "schema": "evidence-lane.prompt-index.v2",
                    "host_session_id": host_session_id,
                    "turn_id": turn_id,
                    "input_kind": input_kind,
                    "capture_dispatch": capture_dispatch,
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
                    "capture_dispatch": capture_dispatch,
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
                    "lineage_host_identity": lineage_host_identity,
                    "lineage_model": lineage_telemetry["model"],
                    "lineage_submodel": lineage_telemetry["submodel"],
                    "lineage_token_metrics": lineage_telemetry["token_metrics"],
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
    host_plan_rehydration = _prepare_bound_host_plan_rehydration(
        root,
        bound=bound,
        host_payload=host_payload,
        trigger="USER_PROMPT_TURN",
        trigger_event_id=str(entry["control_record_sha256"]),
    )
    _require(
        host_plan_rehydration is not None
        and host_plan_rehydration.get("receipt", {}).get("status") == "PASS",
        "TURN_CONTROL_PROMPT_PLAN_RELOCK_REQUIRED",
        "A new governed prompt must seal the exact native Plan relock request before work continues.",
    )
    return {
        "state": action,
        "schema": "evidence-lane.codex-turn-control-receipt.v2",
        "prepare_state": "PREPARED_NOT_COMMITTED",
        "project_id": entry["project_id"],
        "evidence_session_id": entry["evidence_session_id"],
        "turn_id": entry["turn_id"],
        "input_kind": entry["input_kind"],
        "capture_dispatch": entry["capture_dispatch"],
        "pre_reasoning_host_dispatch_proven": entry["capture_dispatch"][
            "pre_reasoning_dispatch_proven"
        ],
        "prompt_index": entry["prompt_index"],
        "prompt_record_sha256": entry["prompt_record_sha256"],
        "record_sha256": entry["prompt_record_sha256"],
        "control_record_sha256": entry["control_record_sha256"],
        "binding_sha256": entry["binding_sha256"],
        "retrieval_receipt_sha256": entry["retrieval_receipt_sha256"],
        "retrieval_outcome": entry["retrieval"]["outcome"],
        "retrieval_lineage_result_count": entry["retrieval"]["lineage_result_count"],
        "retrieval_accepted_code_result_count": entry["retrieval"][
            "accepted_code_result_count"
        ],
        "retrieval_candidate_overlay_used": entry["retrieval"][
            "candidate_overlay_used"
        ],
        "behavior_query_owner": entry["retrieval"]["query_owner"],
        "hook_lookup_performed": entry["retrieval"]["hook_lookup_performed"],
        "native_behavior_query_required": entry["retrieval"][
            "native_behavior_query_required"
        ],
        "native_behavior_query_satisfied": entry["retrieval"][
            "native_behavior_query_satisfied"
        ],
        "required_native_read_sequence": entry["retrieval"][
            "required_native_read_sequence"
        ],
        "host_plan_refresh_owner": entry["retrieval"]["host_plan_refresh_owner"],
        "host_plan_tool": entry["retrieval"]["host_plan_tool"],
        "persistent_plan_row": entry["binding"]["persistent_plan_row"],
        "accepted_pv": entry["binding"]["accepted_pv"],
        "entry_pv": entry["binding"]["accepted_pv"],
        "pointer_generation": entry["binding"]["pointer_generation"],
        "attachment_identity_count": len(entry["attachment_identities"]),
        "research_question": research_receipt,
        "persistent_change_display": persistent_change_display,
        "host_plan_rehydration": host_plan_rehydration,
        "host_plan_behavior_owner": "ACTIVE_EVIDENCE_LANE_SKILL",
        "hook_performed_host_update_plan": False,
        "host_plan_relock_precedes_prompt_work": True,
        "scrollback_authority": False,
        "transcript_authority": False,
        "private_reasoning_stored": False,
        **projection,
    }


def prepare_goal_continuation_turn(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind an automatic Goal at its first tool boundary without a prompt.

    Codex does not emit ``UserPromptSubmit`` for ``thread/goal/set`` work.  This
    route is therefore deliberately separate from :func:`prepare_turn`: it
    consumes the exact native active-Goal flag plus task binding, stores no Goal
    objective, creates no synthetic user input, and remains fail-closed on any
    task, installation, pointer, or active-Plan drift.
    """

    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    turn_id = str(host_payload.get("turn_id") or "").strip()
    _require(
        bool(host_session_id and turn_id)
        and str(host_payload.get("hook_event_name") or "") == "PreToolUse"
        and bool(str(host_payload.get("tool_name") or "").strip())
        and bool(str(host_payload.get("tool_use_id") or "").strip()),
        "TURN_CONTROL_GOAL_TOOL_BOUNDARY_IDENTITY_REQUIRED",
        "Goal continuation binding requires the exact first PreToolUse identity.",
    )
    _require(
        not str(host_payload.get("prompt") or "").strip(),
        "TURN_CONTROL_GOAL_SYNTHETIC_PROMPT_FORBIDDEN",
        "The non-prompt Goal continuation route cannot consume prompt text.",
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
        "The exact governed session has not activated strict Mode plus Plan control.",
        policy=policy,
    )
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    binding = _binding_snapshot(root, bound)
    authority = _derive_active_task_goal_binding(
        root,
        host_session_id=host_session_id,
        turn_binding=binding,
        host_payload=host_payload,
    )
    capture_dispatch = _goal_continuation_dispatch(authority)
    project_root = Path(bound["project_root"])
    descriptor = "GOAL_CONTINUATION " + str(binding["plan_task_id"])
    descriptor_sha256 = sha256_bytes(descriptor.encode("utf-8"))
    live_source_snapshot = _source_change_snapshot(
        root,
        binding=binding,
        cwd=str(host_payload.get("cwd") or ""),
    )
    lineage_host_identity = _lineage_host_identity(host_payload)
    lineage_telemetry = _response_telemetry(host_payload)
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
            existing_entries = [json.loads(row["record_json"]) for row in existing_rows]
            exact_entries = [
                candidate
                for candidate in existing_entries
                if candidate.get("input_kind") == "goal"
                and candidate.get("input_origin")
                == _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN
                and dict(candidate.get("task_goal_authority") or {}).get(
                    "receipt_sha256"
                )
                == authority["receipt_sha256"]
            ]
            _require(
                len(existing_entries) == len(exact_entries) <= 1,
                "TURN_CONTROL_GOAL_CONTINUATION_REPLAY_AMBIGUOUS",
                "The host turn already contains a different or duplicate control entry.",
                existing_records=len(existing_entries),
                matching_records=len(exact_entries),
            )
            existing_entry = exact_entries[0] if exact_entries else None
            if existing_entry is not None:
                _require(
                    existing_entry.get("capture_dispatch") == capture_dispatch
                    and existing_entry.get("binding_sha256")
                    == binding["binding_sha256"],
                    "TURN_CONTROL_GOAL_CONTINUATION_BINDING_DRIFT",
                    "The existing Goal continuation entry no longer matches its sealed authority.",
                )
                entry = existing_entry
                action = "GOAL_CONTINUATION_BOUND_IDEMPOTENT_REUSE"
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
                        if row.get("record_sha256") == latest["prompt_record_sha256"]
                        and int(row.get("prompt_index") or 0)
                        == int(latest["prompt_index"])
                    ]
                    _require(
                        len(strict_prompt_matches) == 1,
                        "TURN_CONTROL_PROMPT_SQLITE_PROJECTION_MISMATCH",
                        "Strict turn-control SQLite does not match its indexed projection.",
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
                retrieval = _behavior_query_handoff_receipt(
                    binding=binding,
                    visible_text=descriptor,
                )
                prepared_at = _now()
                prompt_record = {
                    "schema": "evidence-lane.prompt-index.v2",
                    "record_kind": "NATIVE_TASK_GOAL_CONTINUATION",
                    "host_session_id": host_session_id,
                    "turn_id": turn_id,
                    "input_kind": "goal",
                    "input_origin": _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN,
                    "capture_dispatch": capture_dispatch,
                    "prompt_index": prompt_index,
                    "visible_prompt_after_redaction": None,
                    "prompt_sha256_after_redaction": None,
                    "prompt_chars_after_redaction": 0,
                    "continuation_descriptor_after_redaction": descriptor,
                    "continuation_descriptor_sha256_after_redaction": (
                        descriptor_sha256
                    ),
                    "attachment_identities": [],
                    "task_goal_authority_receipt_sha256": authority[
                        "receipt_sha256"
                    ],
                    "raw_prompt_stored": False,
                    "redacted_visible_prompt_stored": False,
                    "raw_goal_objective_stored": False,
                    "synthetic_prompt_used": False,
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
                    "entry_kind": "NATIVE_TASK_GOAL_CONTINUATION_ENTRY",
                    "prepare_state": "GOAL_CONTINUATION_BOUND_NOT_COMMITTED",
                    "project_id": binding["project_id"],
                    "evidence_session_id": binding["evidence_session_id"],
                    "task_id": binding["task_id"],
                    "plan_task_id": binding["plan_task_id"],
                    "host_session_id": host_session_id,
                    "turn_id": turn_id,
                    "input_kind": "goal",
                    "input_origin": _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN,
                    "capture_dispatch": capture_dispatch,
                    "task_goal_authority": authority,
                    "prompt_index": prompt_index,
                    "prompt_record": prompt_record,
                    "prompt_record_sha256": prompt_record["record_sha256"],
                    "visible_input_after_redaction": descriptor,
                    "visible_input_sha256_after_redaction": descriptor_sha256,
                    "attachment_identities": [],
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
                        "classified_before_reasoning": False,
                        "classification_timing": "FIRST_GOAL_TOOL_BOUNDARY",
                    },
                    "mode_classification": {
                        "selected_mode_ids": binding["selected_mode_ids"],
                        "mode_binding_receipt_sha256": binding[
                            "mode_binding_receipt_sha256"
                        ],
                        "classified_before_reasoning": False,
                        "classification_timing": "FIRST_GOAL_TOOL_BOUNDARY",
                    },
                    "operators": operators,
                    "gates": {
                        **binding["gates"],
                        "source_mutation_requires_prompt_prepare": False,
                        "source_mutation_requires_native_task_goal_binding": True,
                    },
                    "bounded_write_scope": binding["persistent_plan_row"][
                        "bounded_write_scope"
                    ],
                    "source_change_entry": live_source_snapshot,
                    "lineage_host_identity": lineage_host_identity,
                    "lineage_model": lineage_telemetry["model"],
                    "lineage_submodel": lineage_telemetry["submodel"],
                    "lineage_token_metrics": lineage_telemetry["token_metrics"],
                    "prepared_at": prepared_at,
                    "raw_goal_objective_stored": False,
                    "synthetic_prompt_used": False,
                    "user_prompt_submit_observed": False,
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
                        descriptor,
                    ),
                )
                action = "GOAL_CONTINUATION_BOUND_NOT_COMMITTED"
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
            "Goal continuation SQLite integrity or FTS parity failed.",
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
        turn_state="GOAL_CONTINUATION_BOUND_NOT_COMMITTED",
        prompt_index=int(entry["prompt_index"]),
        uncommitted_count=1,
        changed_since_prepare=(
            entry_source_snapshot["worktree_sha256"]
            != live_source_snapshot["worktree_sha256"]
        ),
    )
    host_plan_rehydration = _prepare_bound_host_plan_rehydration(
        root,
        bound=bound,
        host_payload={**host_payload, "host_goal_active": True},
        trigger="GOAL_ACTIVE_TURN",
        trigger_event_id=str(entry["control_record_sha256"]),
    )
    _require(
        host_plan_rehydration is not None
        and host_plan_rehydration.get("receipt", {}).get("status") == "PASS",
        "TURN_CONTROL_GOAL_PLAN_RELOCK_REQUIRED",
        "A resumed Goal must seal the exact native Plan relock request before work continues.",
    )
    return {
        "state": action,
        "schema": "evidence-lane.codex-goal-continuation-entry.v1",
        "prepare_state": "GOAL_CONTINUATION_BOUND_NOT_COMMITTED",
        "project_id": entry["project_id"],
        "evidence_session_id": entry["evidence_session_id"],
        "turn_id": entry["turn_id"],
        "input_kind": "goal",
        "input_origin": _NATIVE_TASK_GOAL_CONTINUATION_ORIGIN,
        "capture_dispatch": entry["capture_dispatch"],
        "pre_reasoning_host_dispatch_proven": False,
        "tool_boundary_continuation_proven": True,
        "prompt_index": entry["prompt_index"],
        "prompt_record_sha256": entry["prompt_record_sha256"],
        "control_record_sha256": entry["control_record_sha256"],
        "binding_sha256": entry["binding_sha256"],
        "task_goal_authority": entry["task_goal_authority"],
        "retrieval_receipt_sha256": entry["retrieval_receipt_sha256"],
        "persistent_plan_row": entry["binding"]["persistent_plan_row"],
        "accepted_pv": entry["binding"]["accepted_pv"],
        "pointer_generation": entry["binding"]["pointer_generation"],
        "research_question": research_receipt,
        "persistent_change_display": persistent_change_display,
        "host_plan_rehydration": host_plan_rehydration,
        "host_plan_behavior_owner": "ACTIVE_EVIDENCE_LANE_SKILL",
        "hook_performed_host_update_plan": False,
        "host_plan_relock_precedes_goal_work": True,
        "raw_goal_objective_stored": False,
        "synthetic_prompt_used": False,
        "user_prompt_submit_observed": False,
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


def _governed_activity_class(tool_name: str, visible_json: str) -> str:
    normalized = tool_name.strip().lower().replace("-", "_")
    command_text = (
        visible_json.lower()
        if normalized
        in {
            "shell_command",
            "functions.shell_command",
            "mcp__codex__shell_command",
            "terminal",
        }
        else ""
    )
    combined = f"{normalized}\n{command_text}"
    if "render_project_panel" in normalized or "render_runtime_panel" in normalized:
        return "render_panels"
    if "vercel" in normalized or re.search(r"\bvercel(?:\.exe)?\s", command_text):
        return "vercel_sync_deployment"
    if (
        "github" in normalized
        or normalized.startswith("git_")
        or re.search(
            r"\bgit\s+(?:add|commit|diff|fetch|merge|pull|push|status|switch)\b",
            command_text,
        )
    ):
        return "git_sync"
    if any(
        marker in command_text
        for marker in (
            " pytest",
            "pytest ",
            "ruff check",
            "npm test",
            "pnpm test",
            "test-current-execution-panel",
        )
    ):
        return "tests"
    if any(
        marker in combined
        for marker in (
            "plugin_add",
            "plugin add",
            "plugin install",
            "marketplace add",
            "pip install",
            "npm install",
            "install_evidence",
            "install-evidence",
        )
    ):
        return "installation"
    if (
        "chatlineage" in combined
        or "chat_lineage" in combined
        or "lineage" in normalized
    ):
        return "chat_lineage"
    if "record_host_memory_import" in combined or "memory_" in normalized:
        return "memory"
    if "canon" in normalized:
        return "canon"
    if "learning" in normalized:
        return "learning"
    if any(
        marker in normalized
        for marker in ("pv_", "plan", "task_backlog", "task_classify")
    ):
        return "plan_pv"
    if any(
        marker in normalized
        for marker in (
            "boot",
            "build",
            "fuse",
            "refresh",
            "rollback",
            "runtime",
            "session",
            "state_travel",
            "transition",
        )
    ):
        return "lifecycle"
    return "other_governed"


def _governed_activity_source_plugin(tool_name: str) -> str:
    normalized = tool_name.strip().lower().replace("-", "_")
    # Tool ownership is determined by the provider route, not by an action
    # verb embedded in the tool name.  In particular, render_project_panel and
    # render_runtime_panel are Evidence Lane MCP actions; assigning them to the
    # external Render provider makes the host Sources projection lie about
    # which plugin executed the call.
    if (
        normalized in {"render_project_panel", "render_runtime_panel"}
        or re.match(
            r"^(?:mcp__)?evidence_lane(?:_[a-f0-9]{8,64})?(?:__|\.)",
            normalized,
        )
    ):
        return "Evidence Lane"
    if (
        re.match(r"^(?:mcp__)?github(?:__|\.)", normalized)
        or normalized.startswith(("github_", "git_"))
    ):
        return "GitHub"
    if (
        re.match(r"^(?:mcp__)?vercel(?:__|\.)", normalized)
        or normalized.startswith("vercel_")
    ):
        return "Vercel"
    if (
        re.match(r"^(?:mcp__)?render(?:__|\.)", normalized)
        or normalized.startswith("render_")
    ):
        return "Render"
    if normalized in {
        "shell_command",
        "functions.shell_command",
        "mcp__codex__shell_command",
        "terminal",
    }:
        return "Codex host"
    return "Evidence Lane"


def _is_evidence_lane_render_action(tool_name: str) -> bool:
    normalized = tool_name.strip().lower().replace("-", "_")
    if normalized in {"render_project_panel", "render_runtime_panel"}:
        return True
    return bool(
        re.match(
            r"^(?:mcp__)?evidence_lane(?:_[a-f0-9]{8,64})?(?:__|\.)"
            r"render_(?:project|runtime)_panel$",
            normalized,
        )
    )


def _build_governed_activity_count_projection(
    events: list[Mapping[str, Any]],
    *,
    total_tool_use_count: int | None = None,
    host_ui_supported: bool | None = None,
) -> dict[str, Any]:
    hook_counts = {"PreToolUse": 0, "PostToolUse": 0}
    seen_hook_phases: set[tuple[str, str]] = set()
    for event in events:
        tool_use_id = str(event.get("tool_use_id") or "").strip()
        phase = str(event.get("phase") or "").strip().lower()
        hook_event = {"before": "PreToolUse", "after": "PostToolUse"}.get(phase)
        if not tool_use_id or hook_event is None:
            continue
        phase_identity = (tool_use_id, phase)
        if phase_identity in seen_hook_phases:
            continue
        seen_hook_phases.add(phase_identity)
        hook_counts[hook_event] += 1

    selected: dict[str, Mapping[str, Any]] = {}
    selected_order: dict[str, tuple[int, str, str]] = {}
    for event in events:
        tool_use_id = str(event.get("tool_use_id") or "").strip()
        phase = str(event.get("phase") or "").strip().lower()
        if not tool_use_id or phase not in {"before", "after"}:
            continue
        rank = (
            1 if phase == "after" else 0,
            str(event.get("recorded_at") or ""),
            str(event.get("tool_event_sha256") or ""),
        )
        if tool_use_id not in selected or rank > selected_order[tool_use_id]:
            selected[tool_use_id] = event
            selected_order[tool_use_id] = rank

    class_counts = {key: 0 for key, _ in _GOVERNED_ACTIVITY_CLASS_ORDER}
    source_counts = {key: 0 for key in _GOVERNED_ACTIVITY_SOURCE_ORDER}
    completed_count = 0
    evidence_lane_render_leaks: list[str] = []
    for event in selected.values():
        tool_name = str(event.get("tool_name") or "")
        visible_json = str(event.get("visible_json_after_redaction") or "")
        class_counts[_governed_activity_class(tool_name, visible_json)] += 1
        source_plugin = _governed_activity_source_plugin(tool_name)
        source_counts[source_plugin] += 1
        if _is_evidence_lane_render_action(tool_name) and source_plugin != "Evidence Lane":
            evidence_lane_render_leaks.append(tool_name)
        completed_count += int(str(event.get("phase") or "").lower() == "after")

    exact_total = max(int(total_tool_use_count or len(selected)), len(selected))
    projected = len(selected)
    provider_action_counts = [
        {"provider": source, "raw_action_count": source_counts[source]}
        for source in _GOVERNED_ACTIVITY_SOURCE_ORDER
        if source_counts[source]
    ]
    governed_routing_totals = {
        "distinct_tool_use_count": projected,
        "completed_tool_use_count": completed_count,
        "in_flight_tool_use_count": projected - completed_count,
        "offloaded_tool_use_count": exact_total - projected,
    }
    per_hook_usage_counts = [
        {"hook_event": hook_event, "usage_count": hook_counts[hook_event]}
        for hook_event in ("PreToolUse", "PostToolUse")
        if hook_counts[hook_event]
    ]
    projection_consistent = (
        sum(source_counts.values()) == projected
        and completed_count + (projected - completed_count) == projected
        and projected + (exact_total - projected) == exact_total
        and not evidence_lane_render_leaks
    )
    core = {
        "schema": "evidence-lane.codex-governed-activity-counts.v1",
        "status": "PASS" if projection_consistent else "MISMATCH",
        "state": "BOUNDED_ATTRIBUTION_COUNTS_PROJECTED",
        "group_id": "evidence_lane",
        "group_label": "Evidence Lane",
        "activity_classes": [
            {"class_id": key, "label": label, "count": class_counts[key]}
            for key, label in _GOVERNED_ACTIVITY_CLASS_ORDER
            if class_counts[key]
        ],
        "source_plugin_groups": [
            {"source_plugin": source, "count": source_counts[source]}
            for source in _GOVERNED_ACTIVITY_SOURCE_ORDER
            if source_counts[source]
        ],
        "raw_provider_action_counts": provider_action_counts,
        "raw_provider_action_count_total": projected,
        "governed_routing_totals": governed_routing_totals,
        "per_hook_usage_counts": per_hook_usage_counts,
        "per_hook_usage_count_total": sum(hook_counts.values()),
        "per_hook_usage_scope": "TOOL_USE_HOOK_EVENTS_ONLY",
        "count_dimensions_conflated": False,
        "provider_ownership_basis": "EXACT_PROVIDER_ROUTE_OR_EVIDENCE_LANE_NATIVE_ACTION",
        "evidence_lane_render_tools_owned_by_render_provider": bool(
            evidence_lane_render_leaks
        ),
        "evidence_lane_render_provider_leak_count": len(evidence_lane_render_leaks),
        "projected_tool_use_count": projected,
        "completed_tool_use_count": completed_count,
        "in_flight_tool_use_count": projected - completed_count,
        "offloaded_tool_use_count": exact_total - projected,
        "deduplicated_by": "tool_use_id_after_phase_preferred",
        "attribution_only": True,
        "source_actions_reparented": False,
        "completed_detail_offloaded": True,
        "raw_receipts_included": False,
        "raw_tool_input_included": False,
        "raw_tool_response_included": False,
        "model_context_blob_included": False,
        "host_grouping_capability": (
            "SUPPORTED"
            if host_ui_supported is True
            else "UNSUPPORTED"
            if host_ui_supported is False
            else "UNVERIFIED"
        ),
        "host_rendering_authority": "CODEX_HOST_OWNED",
        "fallback_surface": "BOUNDED_STRUCTURED_COUNTER_PROJECTION",
        "read_only_projection": True,
        "lifecycle_mutated": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    core["projection_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def _governed_activity_counts_from_database(
    database: Path,
    *,
    project_id: str,
    evidence_session_id: str,
    host_ui_supported: bool | None,
) -> dict[str, Any]:
    if not database.is_file():
        return _build_governed_activity_count_projection(
            [],
            total_tool_use_count=0,
            host_ui_supported=host_ui_supported,
        )
    with _read_only(database) as connection:
        total = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT t.tool_use_id)
                FROM turn_tool_event t
                JOIN turn_entry e
                  ON e.control_record_sha256=t.control_record_sha256
                WHERE e.project_id=? AND e.evidence_session_id=?
                """,
                (project_id, evidence_session_id),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT t.tool_use_id, t.phase, t.tool_name, t.tool_event_sha256,
                   t.event_json, t.recorded_at
            FROM turn_tool_event t
            JOIN turn_entry e
              ON e.control_record_sha256=t.control_record_sha256
            WHERE e.project_id=? AND e.evidence_session_id=?
            ORDER BY t.recorded_at DESC, t.tool_use_id, t.phase DESC
            LIMIT ?
            """,
            (
                project_id,
                evidence_session_id,
                _GOVERNED_ACTIVITY_TOOL_LIMIT * 2,
            ),
        ).fetchall()
    events: list[Mapping[str, Any]] = []
    for row in rows:
        record = json.loads(row["event_json"])
        event_payload = record.get("event_payload")
        _require(
            isinstance(event_payload, dict),
            "TURN_CONTROL_ACTIVITY_EVENT_INVALID",
            "A governed activity receipt has no bounded event payload.",
        )
        events.append(
            {
                "tool_use_id": row["tool_use_id"],
                "phase": row["phase"],
                "tool_name": row["tool_name"],
                "tool_event_sha256": row["tool_event_sha256"],
                "recorded_at": row["recorded_at"],
                "visible_json_after_redaction": event_payload.get(
                    "visible_json_after_redaction"
                ),
            }
        )
    return _build_governed_activity_count_projection(
        events,
        total_tool_use_count=total,
        host_ui_supported=host_ui_supported,
    )


def project_governed_activity_counts(
    store_root: str | Path,
    *,
    host_session_id: str,
    cwd: str,
    host_ui_supported: bool | None = None,
) -> dict[str, Any]:
    """Project compact attributed counts without reparenting source actions."""

    root = Path(store_root).resolve()
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id.strip(),
        cwd=cwd,
    )
    binding = _binding_snapshot(root, bound)
    database = (
        resolved_chat_lineage_root(Path(bound["project_root"]))
        / "codex_turn_control.sqlite"
    )
    return _governed_activity_counts_from_database(
        database,
        project_id=str(binding["project_id"]),
        evidence_session_id=str(binding["evidence_session_id"]),
        host_ui_supported=host_ui_supported,
    )


def _host_activity_group_support(host_payload: Mapping[str, Any]) -> bool | None:
    value = host_payload.get("host_ui_supports_activity_groups")
    return value if isinstance(value, bool) else None


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
        bool(host_session_id and turn_id and tool_name and tool_use_id),
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
    telemetry = _response_telemetry(host_payload)
    event_payload = {
        "turn_id": turn_id,
        "tool_use_id": tool_use_id,
        "phase": phase,
        "tool_name": tool_name,
        "control_record_sha256": entry["control_record_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "host_identity": _lineage_host_identity(host_payload),
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
    with _connection(project_root) as connection:
        existing = connection.execute(
            "SELECT event_json FROM turn_tool_event WHERE tool_use_id=? AND phase=?",
            (tool_use_id, phase),
        ).fetchone()
    if existing:
        stored = json.loads(existing["event_json"])
        stored_payload = stored.get("event_payload")
        _require(
            isinstance(stored_payload, dict)
            and sha256_bytes(canonical_json_bytes(stored_payload))
            == stored.get("tool_event_sha256"),
            "TURN_CONTROL_TOOL_EVENT_INTEGRITY_FAILED",
            "The existing tool event failed its payload SHA-256 verification.",
        )
        comparable_payload = event_payload
        if "host_identity" not in stored_payload:
            # v2.0 tool events predate the additive privacy-safe host-identity
            # projection. Replaying that exact visible event must reuse the
            # immutable record instead of appending a second lineage event.
            comparable_payload = {
                key: value
                for key, value in event_payload.items()
                if key != "host_identity"
            }
        _require(
            stored_payload == comparable_payload
            and stored.get("control_record_sha256") == entry["control_record_sha256"]
            and stored.get("tool_use_id") == tool_use_id
            and stored.get("phase") == phase
            and stored.get("tool_name") == tool_name,
            "TURN_CONTROL_TOOL_EVENT_CONFLICT",
            "A tool-use identity already binds different visible activity.",
        )
        activity_counts = _governed_activity_counts_from_database(
            resolved_chat_lineage_root(project_root) / "codex_turn_control.sqlite",
            project_id=str(binding["project_id"]),
            evidence_session_id=str(binding["evidence_session_id"]),
            host_ui_supported=None,
        )
        return {
            "state": "RECORDED_IDEMPOTENT_REUSE",
            "tool_event_sha256": stored["tool_event_sha256"],
            "lineage_event_sha256": stored["lineage_event_sha256"],
            "control_record_sha256": entry["control_record_sha256"],
            "phase": phase,
            "governed_activity_counts": activity_counts,
        }
    lineage_path = (
        resolved_chat_lineage_root(project_root)
        / f"{binding['evidence_session_id']}.jsonl"
    )
    lineage_event = ChatLineage(lineage_path).append(
        event_type=f"turn.tool.{phase}",
        visible_payload=event_payload,
        occurred_at=_now(),
        session_id=binding["evidence_session_id"],
        task_id=binding["task_id"],
        event_id=event_id,
        actor_type="tool",
        model=telemetry["model"],
        submodel=telemetry["submodel"],
        token_metrics=telemetry["token_metrics"],
    )
    with _connection(project_root) as connection:
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
    activity_counts = _governed_activity_counts_from_database(
        resolved_chat_lineage_root(project_root) / "codex_turn_control.sqlite",
        project_id=str(binding["project_id"]),
        evidence_session_id=str(binding["evidence_session_id"]),
        host_ui_supported=None,
    )
    return {
        "state": "RECORDED",
        "tool_event_sha256": tool_event_sha256,
        "lineage_event_sha256": lineage_event["event_sha256"],
        "control_record_sha256": entry["control_record_sha256"],
        "phase": phase,
        "governed_activity_counts": activity_counts,
    }


def _is_compact_session_source(value: Any) -> bool:
    normalized = str(value or "").strip().lower().replace("_", "-")
    return normalized in _COMPACT_SESSION_SOURCES


def _compact_locator(project_root: Path, relative_path: str) -> dict[str, Any]:
    """Return one project-relative locator without loading its payload."""

    relative = Path(relative_path)
    _require(
        not relative.is_absolute() and ".." not in relative.parts,
        "TURN_CONTROL_COMPACT_LOCATOR_INVALID",
        "A compact re-entry locator must stay inside the governed project.",
        relative_path=relative_path,
    )
    path = project_root / relative
    exists = path.is_file()
    locator: dict[str, Any] = {
        "relative_path": relative.as_posix(),
        "state": "AVAILABLE" if exists else "MISSING",
        "exists": exists,
    }
    if exists:
        stat = path.stat()
        locator.update(
            {
                "size_bytes": int(stat.st_size),
                "content_sha256": sha256_file(path),
            }
        )
    locator["locator_sha256"] = sha256_bytes(canonical_json_bytes(locator))
    return locator


def _compact_lineage_cursor(
    project_root: Path,
    *,
    evidence_session_id: str,
) -> dict[str, Any]:
    relative_path = f"lineage/{evidence_session_id}.sqlite"
    locator = _compact_locator(project_root, relative_path)
    if not locator["exists"]:
        return {
            "state": "MISSING",
            "locator": locator,
            "raw_event_payload_included": False,
        }
    try:
        with _read_only(project_root / relative_path) as connection:
            row = connection.execute(
                """
                SELECT event_count, head_event_sha256, jsonl_sha256,
                       projection_sha256
                FROM lineage_head
                WHERE singleton=1
                """
            ).fetchone()
    except sqlite3.Error as exc:
        raise TurnControlError(
            "TURN_CONTROL_COMPACT_LINEAGE_CURSOR_INVALID",
            "The compact ChatLineage cursor could not be read safely.",
            error_type=type(exc).__name__,
        ) from exc
    if row is None:
        return {
            "state": "EMPTY",
            "locator": locator,
            "event_count": 0,
            "raw_event_payload_included": False,
        }
    return {
        "state": "AVAILABLE",
        "locator": locator,
        "event_count": int(row["event_count"]),
        "head_event_sha256": row["head_event_sha256"],
        "jsonl_sha256": row["jsonl_sha256"],
        "projection_sha256": row["projection_sha256"],
        "raw_event_payload_included": False,
    }


def _compact_task_memory_cursor(
    project_root: Path,
    *,
    project_id: str,
    evidence_session_id: str,
    runtime_task_id: str | None,
) -> dict[str, Any]:
    database = resolved_chat_lineage_root(project_root) / "codex_turn_control.sqlite"
    relative_path = database.relative_to(project_root).as_posix()
    locator = _compact_locator(project_root, relative_path)
    if not locator["exists"]:
        return {
            "state": "MISSING",
            "locator": locator,
        }
    try:
        with _read_only(project_root / relative_path) as connection:
            row = connection.execute(
                """
                SELECT e.prompt_index, e.control_record_sha256,
                       e.prompt_record_sha256, e.binding_sha256,
                       e.retrieval_receipt_sha256,
                       c.commit_sha256, c.response_record_sha256,
                       c.lineage_event_sha256
                FROM turn_entry e
                LEFT JOIN turn_commit c
                  ON c.control_record_sha256=e.control_record_sha256
                WHERE e.project_id=? AND e.evidence_session_id=?
                  AND (? IS NULL OR json_extract(e.record_json, '$.task_id')=?)
                ORDER BY e.prompt_index DESC
                LIMIT 1
                """,
                (
                    project_id,
                    evidence_session_id,
                    runtime_task_id,
                    runtime_task_id,
                ),
            ).fetchone()
    except sqlite3.Error as exc:
        raise TurnControlError(
            "TURN_CONTROL_COMPACT_TASK_MEMORY_CURSOR_INVALID",
            "The compact task-memory cursor could not be read safely.",
            error_type=type(exc).__name__,
        ) from exc
    if row is None:
        return {
            "state": "EMPTY",
            "locator": locator,
            "runtime_task_id": runtime_task_id,
        }
    return {
        "state": "AVAILABLE",
        "locator": locator,
        "runtime_task_id": runtime_task_id,
        "prompt_index": int(row["prompt_index"]),
        "control_record_sha256": row["control_record_sha256"],
        "prompt_record_sha256": row["prompt_record_sha256"],
        "binding_sha256": row["binding_sha256"],
        "retrieval_receipt_sha256": row["retrieval_receipt_sha256"],
        "commit_sha256": row["commit_sha256"],
        "response_record_sha256": row["response_record_sha256"],
        "lineage_event_sha256": row["lineage_event_sha256"],
        "turn_state": "COMMITTED" if row["commit_sha256"] else "PREPARED_NOT_COMMITTED",
    }


def _compact_plan_window(session: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(session.get("metadata") or {})
    active_task_id = str(metadata.get("active_backlog_task_id") or "").strip()
    _require(
        bool(active_task_id)
        and str(metadata.get("active_backlog_task_status") or "").upper() == "ACTIVE",
        "TURN_CONTROL_COMPACT_ACTIVE_TASK_REQUIRED",
        "Compact re-entry requires one exact active Plan task.",
    )
    value = metadata.get("host_plan_window")
    if isinstance(value, dict) and value:
        window = dict(value)
        task_ids = [str(item) for item in window.get("window_task_ids") or []]
        _require(
            window.get("schema") == "evidence-lane.host-plan-window-state.v1"
            and active_task_id in task_ids
            and len(task_ids) <= 9,
            "TURN_CONTROL_COMPACT_PLAN_WINDOW_MISMATCH",
            "The bounded host Plan window does not contain the exact active task.",
            active_task_id=active_task_id,
        )
        return {
            "state": "HOST_PLAN_WINDOW_BOUND",
            "canonical_plan_sha256": window.get("canonical_plan_sha256"),
            "executable_projection_sha256": window.get("executable_projection_sha256"),
            "window_projection_sha256": window.get("projection_sha256"),
            "window_ui_fingerprint_sha256": window.get("window_ui_fingerprint_sha256"),
            "window_receipt_sha256": window.get("receipt_sha256"),
            "row_start": window.get("row_start"),
            "row_end": window.get("row_end"),
            "sole_active_row": window.get("sole_active_row"),
            "active_task_id": active_task_id,
            "window_task_count": len(task_ids),
            "full_plan_rows_included": False,
        }
    travel = dict(metadata.get("state_travel") or {})
    resume_contract = dict(travel.get("resume_contract") or {})
    task_list = resume_contract.get("task_list") or []
    active_rows = [
        dict(row)
        for row in task_list
        if isinstance(row, dict)
        and str(row.get("status") or "").upper() in _ACTIVE_PLAN_STATUSES
    ]
    _require(
        len(active_rows) == 1,
        "TURN_CONTROL_COMPACT_PLAN_WINDOW_REQUIRED",
        "No bounded host Plan window or single sealed State Travel row is available.",
        active_row_count=len(active_rows),
    )
    return {
        "state": "SEALED_STATE_TRAVEL_WINDOW_FALLBACK",
        "task_list_sha256": resume_contract.get("task_list_sha256"),
        "active_task_id": active_task_id,
        "sealed_resume_task_id": active_rows[0].get("task_id"),
        "full_plan_rows_included": False,
    }


def _seal_compact_context_size(core: dict[str, Any]) -> dict[str, Any]:
    sized = {**core, "serialized_bytes": 0, "compact_context_sha256": "0" * 64}
    for _ in range(4):
        size = len(canonical_json_bytes(sized))
        if sized["serialized_bytes"] == size:
            break
        sized["serialized_bytes"] = size
    hash_basis = {
        key: value for key, value in sized.items() if key != "compact_context_sha256"
    }
    sized["compact_context_sha256"] = sha256_bytes(canonical_json_bytes(hash_basis))
    final_size = len(canonical_json_bytes(sized))
    if final_size != sized["serialized_bytes"]:
        sized["serialized_bytes"] = final_size
        hash_basis = {
            key: value
            for key, value in sized.items()
            if key != "compact_context_sha256"
        }
        sized["compact_context_sha256"] = sha256_bytes(canonical_json_bytes(hash_basis))
    _require(
        len(canonical_json_bytes(sized)) <= _COMPACT_REENTRY_CONTEXT_BYTE_CEILING,
        "TURN_CONTROL_COMPACT_CONTEXT_BYTE_CEILING_EXCEEDED",
        "The compact re-entry context exceeded its sealed byte ceiling.",
        byte_ceiling=_COMPACT_REENTRY_CONTEXT_BYTE_CEILING,
        serialized_bytes=len(canonical_json_bytes(sized)),
    )
    return sized


def _seal_compact_project_memory(
    project_root: Path,
    *,
    host_session_id: str,
    compact_context: dict[str, Any],
) -> dict[str, Any]:
    locator = _compact_locator(project_root, "memory/head.json")
    if not locator["exists"]:
        return {
            "state": "MEMORY_AUTHORITY_MISSING",
            "head_locator": locator,
            "checkpoint_sealed": False,
            "controls_codex_host_wording": False,
        }
    if not _CODEX_TASK_ID_RE.fullmatch(host_session_id):
        return {
            "state": "HOST_TASK_UUID_UNAVAILABLE",
            "head_locator": locator,
            "checkpoint_sealed": False,
            "controls_codex_host_wording": False,
        }
    head = _json(project_root / "memory" / "head.json")
    active_task_id = str(
        compact_context.get("active_authority", {}).get("active_plan_task_id") or ""
    )
    result = seal_memory_checkpoint(
        project_root,
        project_id=str(compact_context["project_id"]),
        host_task_uuid=host_session_id,
        host_task_deep_link=f"codex://threads/{host_session_id.lower()}",
        active_plan_task_id=active_task_id,
        lineage_head_sha256=str(head.get("lineage_head_sha256") or ""),
        query="active plan memory continuity",
        limit=4,
        sealed_at=_now(),
    )
    return {
        "state": "MEMORY_CHECKPOINT_SEALED",
        "head_locator": locator,
        "checkpoint_sealed": True,
        "checkpoint_sha256": result["checkpoint_sha256"],
        "memory_head_sha256": result["memory_head_sha256"],
        "bounded_locator_count": result["bounded_locator_count"],
        "bounded_locator_ids": [
            row["locator_id"] for row in result["bounded_locators"]
        ],
        "full_transcript_replay_required": False,
        "raw_source_payloads_included": False,
        "controls_codex_host_wording": False,
    }


def _rehydrate_compact_project_memory(
    project_root: Path,
    *,
    host_session_id: str,
    compact_context: dict[str, Any],
) -> dict[str, Any]:
    checkpoint = cast(
        dict[str, Any], compact_context.get("project_memory_checkpoint") or {}
    )
    if checkpoint.get("state") != "MEMORY_CHECKPOINT_SEALED":
        return {
            "state": "MEMORY_REHYDRATION_NOT_APPLICABLE",
            "checkpoint_state": checkpoint.get("state") or "MISSING",
            "rehydrated": False,
            "controls_codex_host_wording": False,
        }
    head = _json(project_root / "memory" / "head.json")
    active_task_id = str(
        compact_context.get("active_authority", {}).get("active_plan_task_id") or ""
    )
    result = rehydrate_memory_checkpoint(
        project_root,
        project_id=str(compact_context["project_id"]),
        checkpoint_sha256=str(checkpoint["checkpoint_sha256"]),
        host_task_uuid=host_session_id,
        host_task_deep_link=f"codex://threads/{host_session_id.lower()}",
        active_plan_task_id=active_task_id,
        lineage_head_sha256=str(head.get("lineage_head_sha256") or ""),
        rehydrated_at=_now(),
    )
    return {
        "state": "MEMORY_CHECKPOINT_REHYDRATED",
        "rehydrated": True,
        "checkpoint_sha256": result["checkpoint_sha256"],
        "memory_head_sha256": result["memory_head_sha256"],
        "rehydrated_locator_count": len(result["locators"]),
        "rehydrated_locator_ids": [row["locator_id"] for row in result["locators"]],
        "receipt_sha256": result["receipt_sha256"],
        "full_transcript_replayed": False,
        "raw_source_payloads_returned": False,
        "controls_codex_host_wording": False,
    }


def _compact_authority_context(
    root: Path,
    *,
    bound: dict[str, Any],
    host_session_id: str,
    working_directory: str | None = None,
) -> dict[str, Any]:
    session = cast(dict[str, Any], bound["session"])
    metadata = cast(dict[str, Any], session.get("metadata") or {})
    project_id = str(session.get("project_id") or "")
    evidence_session_id = str(session.get("session_id") or "")
    project_root = Path(bound["project_root"])
    pointer = _json(project_root / "active_pointer.json")
    _require(
        pointer.get("project_id") == project_id
        and pointer.get("accepted_pv") == session.get("accepted_pv")
        and int(pointer.get("generation") or 0)
        == int(session.get("accepted_pointer_generation") or 0),
        "TURN_CONTROL_COMPACT_POINTER_MISMATCH",
        "Compact re-entry identities do not match the accepted pointer.",
    )
    runtime_task = session.get("task") if isinstance(session.get("task"), dict) else {}
    runtime_task_id = (
        str(cast(dict[str, Any], runtime_task).get("task_id") or "") or None
    )
    active_task_id = str(metadata.get("active_backlog_task_id") or "")
    repository_root = Path(ProjectStore(root).config(project_id).repository_path).resolve()
    workspace_path = Path(str(session.get("workspace_id") or ""))
    current_working_directory = (
        Path(str(working_directory)).resolve()
        if str(working_directory or "").strip()
        else workspace_path.resolve()
        if workspace_path.is_absolute() and workspace_path.exists()
        else repository_root
    )
    profile_value = metadata.get("execution_profile")
    execution_profile = (
        dict(profile_value) if isinstance(profile_value, dict) else {}
    )
    conversation_memory = resolve_conversation_memory(
        codex_home=(
            Path(str(os.environ.get("CODEX_HOME") or "").strip())
            if str(os.environ.get("CODEX_HOME") or "").strip()
            else Path.home() / ".codex"
        ),
        project_root=repository_root,
        cwd=current_working_directory,
        project_id=project_id,
        governed_session_id=evidence_session_id,
        host_task_id=host_session_id,
        host_task_deep_link=f"codex://threads/{host_session_id}",
        host_session_id=host_session_id,
        workspace_id=str(session.get("workspace_id") or repository_root),
        active_plan_task_id=active_task_id,
        execution_profile=execution_profile,
    ).receipt
    task_contract = dict(runtime_task or {})
    classification = dict(metadata.get("task_classification_binding") or {})
    rebind = dict(metadata.get("active_contract_rebind_receipt") or {})
    core = {
        "schema": "evidence-lane.codex-compact-reentry-context.v1",
        "state": "SEALED_BOUNDED_COMPACT_AUTHORITY",
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "host_session_id_sha256": sha256_bytes(host_session_id.encode("utf-8")),
        "accepted_pv": session.get("accepted_pv"),
        "pointer_generation": session.get("accepted_pointer_generation"),
        "accepted_manifest_sha256": pointer.get("accepted_manifest_sha256"),
        "binding_epoch_sha256": _host_binding_epoch(session),
        "active_authority": {
            "active_plan_task_id": active_task_id,
            "active_plan_task_status": metadata.get("active_backlog_task_status"),
            "runtime_task_id": runtime_task_id,
            "runtime_task_contract_sha256": (
                sha256_bytes(canonical_json_bytes(task_contract))
                if task_contract
                else None
            ),
            "task_classification_receipt_sha256": classification.get("receipt_sha256"),
            "active_contract_rebind_receipt_sha256": rebind.get("receipt_sha256"),
        },
        "plan_window": _compact_plan_window(session),
        "chat_lineage_cursor": _compact_lineage_cursor(
            project_root,
            evidence_session_id=evidence_session_id,
        ),
        "task_memory_cursor": _compact_task_memory_cursor(
            project_root,
            project_id=project_id,
            evidence_session_id=evidence_session_id,
            runtime_task_id=runtime_task_id,
        ),
        "canon_locator": _compact_locator(project_root, "canon/canon-input.sqlite"),
        "learning_locator": _compact_locator(
            project_root, "ai_learning/agent-learning.sqlite"
        ),
        "learning_pointer_locator": _compact_locator(
            project_root, "ai_learning/active_pointer.json"
        ),
        "project_memory_locator": _compact_locator(project_root, "memory/head.json"),
        "conversation_memory_authority": {
            "schema": conversation_memory["schema"],
            "status": conversation_memory["status"],
            "binding_sha256": conversation_memory["binding_sha256"],
            "source_chain_sha256": conversation_memory["source_chain_sha256"],
            "merged_guidance_sha256": conversation_memory[
                "merged_guidance_sha256"
            ],
            "conversation_memory_authority_sha256": conversation_memory[
                "conversation_memory_authority_sha256"
            ],
            "source_count": conversation_memory["source_count"],
            "causal_attribution": conversation_memory[
                "user_observed_host_evidence"
            ]["causal_attribution"],
            "raw_guidance_text_included": False,
            "host_compaction_disabled": False,
        },
        "immediate_continuation": {
            "action": "CONTINUE_EXACT_ACTIVE_TASK_WITH_BOUNDED_QUERIES_ONLY",
            "active_plan_task_id": active_task_id,
            "runtime_task_id": runtime_task_id,
            "authority_reconstruction_allowed": False,
        },
        "byte_ceiling": _COMPACT_REENTRY_CONTEXT_BYTE_CEILING,
        "full_plan_included": False,
        "full_env_uop_included": False,
        "full_runtime_envelope_included": False,
        "raw_prompt_included": False,
        "private_reasoning_included": False,
    }
    return _seal_compact_context_size(core)


def _compact_latest_path(project_root: Path) -> Path:
    return resolved_chat_lineage_root(project_root) / "compact_reentry" / "latest.json"


def _write_compact_latest_pointer(
    project_root: Path,
    *,
    receipt_path: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    relative = receipt_path.relative_to(project_root).as_posix()
    context = cast(dict[str, Any], receipt.get("compact_reentry_context") or {})
    existing: dict[str, Any] = {}
    latest_path = _compact_latest_path(project_root)
    if latest_path.is_file():
        existing = _json(latest_path)
    same_precompact = existing.get("precompact_receipt_sha256") == receipt.get(
        "receipt_sha256"
    )
    core = {
        "schema": "evidence-lane.codex-compact-reentry-pointer.v1",
        "project_id": receipt.get("project_id"),
        "evidence_session_id": receipt.get("evidence_session_id"),
        "active_task_id": receipt.get("active_task_id"),
        "binding_epoch_sha256": receipt.get("binding_epoch_sha256"),
        "precompact_receipt_relative_path": relative,
        "precompact_receipt_sha256": receipt.get("receipt_sha256"),
        "compact_context_sha256": context.get("compact_context_sha256"),
        "postcompact_receipt_relative_path": existing.get(
            "postcompact_receipt_relative_path"
        )
        if same_precompact
        else None,
        "postcompact_receipt_sha256": existing.get("postcompact_receipt_sha256")
        if same_precompact
        else None,
        "updated_at": _now(),
    }
    core["pointer_sha256"] = sha256_bytes(canonical_json_bytes(core))
    atomic_write_json(latest_path, core)
    return core


def _load_compact_precompact_receipt(
    project_root: Path,
    *,
    session: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    latest_path = _compact_latest_path(project_root)
    _require(
        latest_path.is_file(),
        "TURN_CONTROL_COMPACT_PRECOMPACT_POINTER_REQUIRED",
        "Compact SessionStart requires a sealed PreCompact pointer.",
    )
    pointer = _json(latest_path)
    claimed_pointer_sha = str(pointer.get("pointer_sha256") or "")
    actual_pointer_sha = sha256_bytes(
        canonical_json_bytes(
            {key: value for key, value in pointer.items() if key != "pointer_sha256"}
        )
    )
    metadata = dict(session.get("metadata") or {})
    _require(
        claimed_pointer_sha == actual_pointer_sha
        and pointer.get("project_id") == session.get("project_id")
        and pointer.get("evidence_session_id") == session.get("session_id")
        and pointer.get("active_task_id") == metadata.get("active_backlog_task_id")
        and pointer.get("binding_epoch_sha256") == _host_binding_epoch(session),
        "TURN_CONTROL_COMPACT_PRECOMPACT_POINTER_STALE",
        "The sealed PreCompact pointer no longer matches the active authority.",
    )
    relative = Path(str(pointer.get("precompact_receipt_relative_path") or ""))
    _require(
        not relative.is_absolute() and ".." not in relative.parts,
        "TURN_CONTROL_COMPACT_PRECOMPACT_PATH_INVALID",
        "The sealed PreCompact receipt path escaped the governed project.",
    )
    receipt_path = project_root / relative
    _require(
        receipt_path.is_file(),
        "TURN_CONTROL_COMPACT_PRECOMPACT_RECEIPT_REQUIRED",
        "The sealed PreCompact receipt is missing.",
    )
    receipt = _json(receipt_path)
    claimed_receipt_sha = str(receipt.get("receipt_sha256") or "")
    actual_receipt_sha = sha256_bytes(
        canonical_json_bytes(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )
    )
    _require(
        claimed_receipt_sha
        == actual_receipt_sha
        == pointer.get("precompact_receipt_sha256")
        and receipt.get("event_name") == "PreCompact"
        and receipt.get("binding_epoch_sha256") == _host_binding_epoch(session),
        "TURN_CONTROL_COMPACT_PRECOMPACT_RECEIPT_STALE",
        "The sealed PreCompact receipt failed identity or SHA-256 verification.",
    )
    return pointer, receipt, receipt_path


def _write_compact_postcompact_completion(
    project_root: Path,
    *,
    receipt_path: Path,
    receipt: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    pointer, _, _ = _load_compact_precompact_receipt(
        project_root,
        session=session,
    )
    updated = {key: value for key, value in pointer.items() if key != "pointer_sha256"}
    updated.update(
        {
            "postcompact_receipt_relative_path": receipt_path.relative_to(
                project_root
            ).as_posix(),
            "postcompact_receipt_sha256": receipt.get("receipt_sha256"),
            "updated_at": _now(),
        }
    )
    updated["pointer_sha256"] = sha256_bytes(canonical_json_bytes(updated))
    atomic_write_json(_compact_latest_path(project_root), updated)
    return updated


def _compact_session_reentry_context(
    root: Path,
    *,
    bound: dict[str, Any],
    host_session_id: str,
    working_directory: str | None = None,
) -> dict[str, Any]:
    session = cast(dict[str, Any], bound["session"])
    project_root = Path(bound["project_root"])
    pointer, precompact_receipt, _ = _load_compact_precompact_receipt(
        project_root,
        session=session,
    )
    sealed_context = cast(
        dict[str, Any], precompact_receipt.get("compact_reentry_context") or {}
    )
    sealed_context_sha = str(sealed_context.get("compact_context_sha256") or "")
    sealed_context_basis = {
        key: value
        for key, value in sealed_context.items()
        if key != "compact_context_sha256"
    }
    _require(
        bool(sealed_context)
        and sealed_context_sha
        == sha256_bytes(canonical_json_bytes(sealed_context_basis))
        == pointer.get("compact_context_sha256")
        and int(sealed_context.get("serialized_bytes") or 0)
        == len(canonical_json_bytes(sealed_context))
        and len(canonical_json_bytes(sealed_context))
        <= _COMPACT_REENTRY_CONTEXT_BYTE_CEILING,
        "TURN_CONTROL_COMPACT_CONTEXT_STALE",
        "The sealed compact context failed its hash or byte-ceiling contract.",
    )
    post_relative = Path(str(pointer.get("postcompact_receipt_relative_path") or ""))
    _require(
        bool(str(post_relative))
        and not post_relative.is_absolute()
        and ".." not in post_relative.parts,
        "TURN_CONTROL_COMPACT_POSTCOMPACT_REQUIRED",
        "Compact SessionStart requires a matching PostCompact completion.",
    )
    post_path = project_root / post_relative
    _require(
        post_path.is_file(),
        "TURN_CONTROL_COMPACT_POSTCOMPACT_REQUIRED",
        "The matching PostCompact completion receipt is missing.",
    )
    post_receipt = _json(post_path)
    post_claimed_sha = str(post_receipt.get("receipt_sha256") or "")
    post_actual_sha = sha256_bytes(
        canonical_json_bytes(
            {
                key: value
                for key, value in post_receipt.items()
                if key != "receipt_sha256"
            }
        )
    )
    _require(
        post_claimed_sha == post_actual_sha == pointer.get("postcompact_receipt_sha256")
        and post_receipt.get("event_name") == "PostCompact"
        and post_receipt.get("precompact_receipt_sha256")
        == precompact_receipt.get("receipt_sha256"),
        "TURN_CONTROL_COMPACT_POSTCOMPACT_STALE",
        "The PostCompact completion no longer matches the sealed PreCompact receipt.",
    )
    sealed_memory = cast(
        dict[str, Any], sealed_context.get("project_memory_checkpoint") or {}
    )
    post_memory = cast(
        dict[str, Any], post_receipt.get("project_memory_rehydration") or {}
    )
    if sealed_memory.get("state") == "MEMORY_CHECKPOINT_SEALED":
        _require(
            post_memory.get("state") == "MEMORY_CHECKPOINT_REHYDRATED"
            and post_memory.get("checkpoint_sha256")
            == sealed_memory.get("checkpoint_sha256")
            and post_memory.get("memory_head_sha256")
            == sealed_memory.get("memory_head_sha256"),
            "TURN_CONTROL_COMPACT_MEMORY_REHYDRATION_STALE",
            "The PostCompact Memory rehydration does not match its sealed checkpoint.",
        )
    current = _compact_authority_context(
        root,
        bound=bound,
        host_session_id=host_session_id,
        working_directory=working_directory,
    )
    sealed_conversation_memory = cast(
        dict[str, Any], sealed_context.get("conversation_memory_authority") or {}
    )
    current_conversation_memory = cast(
        dict[str, Any], current.get("conversation_memory_authority") or {}
    )
    _require(
        bool(sealed_conversation_memory)
        and current_conversation_memory.get(
            "conversation_memory_authority_sha256"
        )
        == sealed_conversation_memory.get("conversation_memory_authority_sha256")
        and current_conversation_memory.get("source_chain_sha256")
        == sealed_conversation_memory.get("source_chain_sha256")
        and current_conversation_memory.get("binding_sha256")
        == sealed_conversation_memory.get("binding_sha256"),
        "TURN_CONTROL_COMPACT_CONVERSATION_MEMORY_STALE",
        "Compact re-entry MEMORY.md authority differs from the sealed PreCompact chain.",
    )
    current.pop("serialized_bytes", None)
    current.pop("compact_context_sha256", None)
    current.update(
        {
            "state": "COMPACT_REENTRY_READY",
            "precompact_receipt_sha256": precompact_receipt.get("receipt_sha256"),
            "postcompact_receipt_sha256": post_claimed_sha,
            "sealed_precompact_context_sha256": sealed_context_sha,
            "authority_reconstructed": False,
        }
    )
    return _seal_compact_context_size(current)


_LIFECYCLE_BOUNDARY_PHASES = {
    "PreCompact": "COMPACTION_SEAL",
    "PostCompact": "COMPACTION_REHYDRATION",
    "SessionEnd": "BEST_EFFORT_SESSION_BOUNDARY_FLUSH",
}


def record_lifecycle_boundary_event(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
    event_name: str,
) -> dict[str, Any]:
    """Seal one privacy-safe host lifecycle boundary without owning behavior.

    These receipts transport lifecycle state only. They never run PV queries,
    classify a task, mutate Plan Lane, refresh a candidate, or move a pointer;
    those behavior decisions remain owned by the installed Evidence Lane skill.
    """

    phase = _LIFECYCLE_BOUNDARY_PHASES.get(event_name)
    _require(
        bool(phase),
        "TURN_CONTROL_LIFECYCLE_EVENT_UNSUPPORTED",
        "The lifecycle boundary event is not part of the approved hook matrix.",
        event_name=event_name,
    )
    host_payload["_evidence_lane_boundary_event"] = event_name
    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    _require(
        bool(host_session_id),
        "TURN_CONTROL_HOST_SESSION_REQUIRED",
        "A lifecycle boundary requires the exact Codex host-session identity.",
    )
    candidate = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
        transcript_path=str(
            host_payload.get("transcript_path")
            or host_payload.get("agent_transcript_path")
            or ""
        ),
    )
    session = candidate["session"]
    metadata = dict(session.get("metadata") or {})
    project_id = str(session["project_id"])
    evidence_session_id = str(session["session_id"])
    project_root = Path(candidate["project_root"]).resolve()
    occurrence_source = str(
        host_payload.get("hook_event_id")
        or host_payload.get("event_id")
        or host_payload.get("turn_id")
        or host_payload.get("tool_use_id")
        or host_payload.get("source")
        or event_name
    ).strip()
    occurrence_sha256 = sha256_bytes(occurrence_source.encode("utf-8"))
    receipt_id = (
        "lifecycle_"
        + sha256_bytes(
            (
                event_name
                + "\0"
                + evidence_session_id
                + "\0"
                + occurrence_sha256
                + "\0"
                + _host_binding_epoch(session)
            ).encode("utf-8")
        )[:26].lower()
    )
    receipt_path = (
        resolved_chat_lineage_root(project_root)
        / "lifecycle_hooks"
        / f"{receipt_id}.json"
    )
    if receipt_path.is_file():
        receipt = _json(receipt_path)
        claimed = str(receipt.get("receipt_sha256") or "")
        actual = sha256_bytes(
            canonical_json_bytes(
                {
                    key: value
                    for key, value in receipt.items()
                    if key != "receipt_sha256"
                }
            )
        )
        _require(
            claimed == actual
            and receipt.get("event_name") == event_name
            and receipt.get("project_id") == project_id
            and receipt.get("evidence_session_id") == evidence_session_id
            and receipt.get("occurrence_sha256") == occurrence_sha256,
            "TURN_CONTROL_LIFECYCLE_RECEIPT_CONFLICT",
            "The existing lifecycle boundary receipt failed identity or SHA-256 verification.",
        )
        if event_name == "PreCompact":
            _require(
                isinstance(receipt.get("compact_reentry_context"), dict),
                "TURN_CONTROL_COMPACT_CONTEXT_REQUIRED",
                "The existing PreCompact receipt predates the bounded compact contract.",
            )
            _write_compact_latest_pointer(
                project_root,
                receipt_path=receipt_path,
                receipt=receipt,
            )
        elif event_name == "PostCompact":
            _write_compact_postcompact_completion(
                project_root,
                receipt_path=receipt_path,
                receipt=receipt,
                session=session,
            )
        return {"state": "SEALED_IDEMPOTENT_REUSE", "receipt": receipt}

    core: dict[str, Any] = {
        "schema": "evidence-lane.codex-lifecycle-boundary.v1",
        "receipt_id": receipt_id,
        "event_name": event_name,
        "phase": phase,
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "active_task_id": metadata.get("active_backlog_task_id"),
        "binding_epoch_sha256": _host_binding_epoch(session),
        "occurrence_sha256": occurrence_sha256,
        "host_identity": _lineage_host_identity(host_payload),
        "hook_owns_lifecycle_transport_only": True,
        "skill_owns_behavior_and_native_reads": True,
        "plan_or_delta_mutated": False,
        "candidate_created_or_refreshed": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "raw_prompt_stored": False,
        "raw_tool_payload_stored": False,
        "private_reasoning_stored": False,
        "sealed_at": _now(),
    }
    if event_name == "PreCompact":
        compact_context = _compact_authority_context(
            root,
            bound=candidate,
            host_session_id=host_session_id,
            working_directory=str(host_payload.get("cwd") or "") or None,
        )
        compact_context.pop("serialized_bytes", None)
        compact_context.pop("compact_context_sha256", None)
        compact_context["project_memory_checkpoint"] = _seal_compact_project_memory(
            project_root,
            host_session_id=host_session_id,
            compact_context=compact_context,
        )
        core["compact_reentry_context"] = _seal_compact_context_size(compact_context)
        core["hook_performed_host_update_plan"] = False
        core["host_plan_behavior_owner"] = "ACTIVE_EVIDENCE_LANE_SKILL"
        core["authority_reconstructed"] = False
        core["full_plan_embedded"] = False
        core["full_env_uop_embedded"] = False
    elif event_name == "PostCompact":
        compact_pointer, precompact_receipt, _ = _load_compact_precompact_receipt(
            project_root,
            session=session,
        )
        core["precompact_receipt_sha256"] = precompact_receipt.get("receipt_sha256")
        core["compact_context_sha256"] = compact_pointer.get("compact_context_sha256")
        core["compact_completion"] = "RECORDED_WITHOUT_AUTHORITY_RECONSTRUCTION"
        sealed_context = cast(
            dict[str, Any], precompact_receipt.get("compact_reentry_context") or {}
        )
        core["project_memory_rehydration"] = _rehydrate_compact_project_memory(
            project_root,
            host_session_id=host_session_id,
            compact_context=sealed_context,
        )
        current_compact_context = _compact_authority_context(
            root,
            bound=candidate,
            host_session_id=host_session_id,
            working_directory=str(host_payload.get("cwd") or "") or None,
        )
        sealed_conversation_memory = cast(
            dict[str, Any],
            sealed_context.get("conversation_memory_authority") or {},
        )
        current_conversation_memory = cast(
            dict[str, Any],
            current_compact_context.get("conversation_memory_authority") or {},
        )
        _require(
            bool(sealed_conversation_memory)
            and current_conversation_memory.get(
                "conversation_memory_authority_sha256"
            )
            == sealed_conversation_memory.get(
                "conversation_memory_authority_sha256"
            )
            and current_conversation_memory.get("source_chain_sha256")
            == sealed_conversation_memory.get("source_chain_sha256")
            and current_conversation_memory.get("binding_sha256")
            == sealed_conversation_memory.get("binding_sha256"),
            "TURN_CONTROL_POSTCOMPACT_CONVERSATION_MEMORY_STALE",
            "PostCompact MEMORY.md authority differs from the sealed PreCompact chain.",
        )
        core["conversation_memory_rehydration"] = {
            **current_conversation_memory,
            "state": "CONVERSATION_MEMORY_AUTHORITY_REHYDRATED",
            "precompact_authority_sha256": sealed_conversation_memory.get(
                "conversation_memory_authority_sha256"
            ),
            "retrieval_route": "BOUNDED_CONTINUATION_GUIDANCE",
            "authority_reconstructed": False,
        }
        core["hook_performed_host_update_plan"] = False
        core["host_plan_behavior_owner"] = "ACTIVE_EVIDENCE_LANE_SKILL"
        core["authority_reconstructed"] = False
        core["full_plan_embedded"] = False
        core["full_env_uop_embedded"] = False
    lineage = ChatLineage(
        resolved_chat_lineage_root(project_root) / f"{evidence_session_id}.jsonl"
    ).append(
        event_type=f"turn.lifecycle.{event_name.lower()}",
        visible_payload=core,
        occurred_at=core["sealed_at"],
        session_id=evidence_session_id,
        task_id=str(metadata.get("active_backlog_task_id") or "") or None,
        event_id="evt_" + receipt_id,
        actor_type="host",
        model=_response_telemetry(host_payload)["model"],
        submodel=_response_telemetry(host_payload)["submodel"],
        token_metrics={"availability": "UNAVAILABLE"},
    )
    receipt = {
        **core,
        "lineage_event_sha256": lineage["event_sha256"],
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(receipt_path, receipt)
    if event_name == "PreCompact":
        _write_compact_latest_pointer(
            project_root,
            receipt_path=receipt_path,
            receipt=receipt,
        )
    elif event_name == "PostCompact":
        _write_compact_postcompact_completion(
            project_root,
            receipt_path=receipt_path,
            receipt=receipt,
            session=session,
        )
    return {"state": "SEALED", "receipt": receipt}


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
    usage_binding = {
        "project_id": str(binding["project_id"]),
        "evidence_session_id": str(binding["evidence_session_id"]),
        "task_id": str(binding["task_id"]),
        "host_session_id_sha256": sha256_bytes(host_session_id.encode("utf-8")),
    }
    goal_usage = _goal_usage_observation(host_payload, binding=usage_binding)
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
        existing_commit = (
            json.loads(selected["commit_json"])
            if selected["commit_json"] is not None
            else None
        )
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
                "host_identity": _lineage_host_identity(host_payload),
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
            resolved_chat_lineage_root(project_root)
            / f"{binding['evidence_session_id']}.jsonl"
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
                **(
                    {"host_identity": response_record["host_identity"]}
                    if isinstance(response_record.get("host_identity"), dict)
                    else {}
                ),
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
        continuity_state = {
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
        state_sha256 = sha256_bytes(canonical_json_bytes(continuity_state))
        if existing_commit is not None:
            claimed_commit_sha256 = str(existing_commit.get("commit_sha256") or "")
            _require(
                claimed_commit_sha256
                == sha256_bytes(
                    canonical_json_bytes(
                        {
                            key: value
                            for key, value in existing_commit.items()
                            if key != "commit_sha256"
                        }
                    )
                ),
                "TURN_CONTROL_RESPONSE_COMMIT_MISMATCH",
                "The existing ordinary-turn COMMIT failed its SHA-256 verification.",
            )
            commit = existing_commit
            state_sha256 = str(commit["state_sha256"])
        else:
            commit = {
                "schema": "evidence-lane.codex-turn-commit.v3",
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
                **(
                    {"host_identity": response_record["host_identity"]}
                    if isinstance(response_record.get("host_identity"), dict)
                    else {}
                ),
                "continuity_commit_receipt": {
                    **continuity_state,
                    "state_sha256": state_sha256,
                    "operational_links_sha256": operational[
                        "items_sha256_after_redaction"
                    ],
                    "token_metrics": telemetry["token_metrics"],
                    "goal_usage": goal_usage,
                    "source_change": source_change,
                    "persistent_change_display": persistent_change_display,
                    "receipt_role": "ORDINARY_TURN_COMMIT_NOT_LIFECYCLE_EXIT",
                },
                "lifecycle_exit_slip_emitted": False,
                "lifecycle_exit_reason": None,
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
        observed_experience = seal_observed_experience_packet(
            project_root,
            project_id=str(binding["project_id"]),
            evidence_session_id=str(binding["evidence_session_id"]),
            runtime_task_id=str(binding["task_id"]),
            plan_task_id=str(binding["plan_task_id"]),
            active_plan=cast(dict[str, Any], binding["persistent_plan_row"]),
            event=commit_event,
            expected_accepted_pv=str(binding["accepted_pv"]),
            expected_pointer_generation=int(binding["pointer_generation"]),
            input_kind=str(entry.get("input_kind") or "user_prompt"),
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
        "schema": "evidence-lane.codex-turn-control-commit-receipt.v3",
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
        "observed_experience": observed_experience,
        "lineage_projection": lineage_projection,
        "response_projection_path": str(response_path),
        "operational_links": operational,
        "token_metrics": telemetry["token_metrics"],
        "goal_usage": goal_usage_receipt,
        "source_change": source_change,
        "persistent_change_display": persistent_change_display,
        "ordinary_turn_commit_receipt": (
            commit.get("continuity_commit_receipt") or commit.get("exit_slip")
        ),
        "lifecycle_exit_slip_emitted": bool(
            commit.get("lifecycle_exit_slip_emitted", False)
        ),
        "historical_exit_slip_alias_reused": (
            "exit_slip" in commit and "continuity_commit_receipt" not in commit
        ),
        "private_reasoning_stored": False,
    }


def seal_lifecycle_exit_slip(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
    reason: str,
    visible_reason: str,
) -> dict[str, Any]:
    """Seal one genuine lifecycle exit boundary without advancing the Plan row."""

    exact_reason = reason.strip().upper()
    _require(
        exact_reason in _LIFECYCLE_EXIT_REASONS,
        "TURN_CONTROL_LIFECYCLE_EXIT_REASON_INVALID",
        "Ordinary turn completion is not a lifecycle Exit Slip boundary.",
        allowed_reasons=sorted(_LIFECYCLE_EXIT_REASONS),
    )
    safe_visible_reason = _turn_redact_text(visible_reason.strip())
    _require(
        bool(safe_visible_reason) and not contains_secret(safe_visible_reason),
        "TURN_CONTROL_LIFECYCLE_EXIT_VISIBLE_REASON_REQUIRED",
        "A lifecycle Exit Slip requires one secret-redacted visible reason.",
    )
    stateless_ephemeral_proof: dict[str, Any] | None = None
    if exact_reason == "STATELESS_EPHEMERAL_END":
        runtime_context_value = host_payload.get("runtime_context")
        runtime_context: dict[str, Any] = (
            cast(dict[str, Any], runtime_context_value)
            if isinstance(runtime_context_value, dict)
            else {}
        )
        ephemeral = (
            host_payload.get("ephemeral") is True
            or runtime_context.get("ephemeral") is True
        )
        stateless = (
            host_payload.get("stateless") is True
            or host_payload.get("stateless_invocation") is True
            or runtime_context.get("stateless") is True
            or runtime_context.get("stateless_invocation") is True
        )
        interaction_profile = (
            str(
                host_payload.get("interaction_profile")
                or runtime_context.get("interaction_profile")
                or ""
            )
            .strip()
            .upper()
        )
        _require(
            ephemeral
            and stateless
            and interaction_profile in {"HEADLESS_API", "DIRECT_CLI_API"},
            "TURN_CONTROL_STATELESS_EPHEMERAL_PROOF_REQUIRED",
            "A stateless invocation Exit Slip requires explicit ephemeral, stateless, "
            "and headless/API interaction proof.",
            ephemeral=ephemeral,
            stateless=stateless,
            interaction_profile=interaction_profile or "UNAVAILABLE",
        )
        stateless_ephemeral_proof = {
            "ephemeral": True,
            "stateless": True,
            "interaction_profile": interaction_profile,
            "proof_source": "EXPLICIT_HOST_OR_RUNTIME_CONTEXT",
        }
    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    _require(
        bool(host_session_id),
        "TURN_CONTROL_HOST_TURN_IDENTITY_REQUIRED",
        "A lifecycle Exit Slip requires the exact governed host-session identity.",
    )
    bound = _one_bound_session(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
    )
    binding = _binding_snapshot(root, bound)
    project_root = Path(bound["project_root"])
    turn_id = str(host_payload.get("turn_id") or "").strip() or None
    latest_control_record_sha256: str | None = None
    latest_turn_state = "NO_TURN_RECEIPT"
    with _connection(project_root) as connection:
        latest_rows = connection.execute(
            """
            SELECT e.record_json, c.commit_sha256
            FROM turn_entry e
            LEFT JOIN turn_commit c
              ON c.control_record_sha256=e.control_record_sha256
            WHERE e.host_session_id=?
            ORDER BY e.prompt_index DESC
            """,
            (host_session_id,),
        ).fetchall()
        latest = next(
            (
                row
                for row in latest_rows
                if json.loads(row["record_json"]).get("task_id") == binding["task_id"]
            ),
            None,
        )
    if latest is not None:
        latest_entry = json.loads(latest["record_json"])
        latest_control_record_sha256 = str(latest_entry["control_record_sha256"])
        latest_turn_state = (
            "COMMITTED" if latest["commit_sha256"] else "PREPARED_NOT_COMMITTED"
        )
    active_row = binding["persistent_plan_row"]
    identity = {
        "schema": "evidence-lane.codex-lifecycle-exit-identity.v1",
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "task_id": binding["task_id"],
        "plan_task_id": binding["plan_task_id"],
        "active_row": active_row["position"],
        "host_session_id_sha256": sha256_bytes(host_session_id.encode("utf-8")),
        "turn_id": turn_id,
        "reason": exact_reason,
        "visible_reason_sha256": sha256_bytes(safe_visible_reason.encode("utf-8")),
        "latest_control_record_sha256": latest_control_record_sha256,
        "stateless_ephemeral_proof": stateless_ephemeral_proof,
    }
    exit_identity_sha256 = sha256_bytes(canonical_json_bytes(identity))
    exit_path = (
        project_root
        / "lineage"
        / "lifecycle-exit-slips"
        / f"{exit_identity_sha256}.json"
    )
    if exit_path.exists():
        receipt = _json(exit_path)
        claimed = str(receipt.get("exit_slip_sha256") or "")
        _require(
            claimed
            == sha256_bytes(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in receipt.items()
                        if key != "exit_slip_sha256"
                    }
                )
            )
            and receipt.get("exit_identity_sha256") == exit_identity_sha256,
            "TURN_CONTROL_LIFECYCLE_EXIT_RECEIPT_MISMATCH",
            "The existing lifecycle Exit Slip failed identity or SHA-256 verification.",
        )
        state = "SEALED_IDEMPOTENT_REUSE"
    else:
        receipt = {
            **identity,
            "schema": "evidence-lane.codex-lifecycle-exit-slip.v1",
            "exit_identity_sha256": exit_identity_sha256,
            "visible_reason_after_redaction": safe_visible_reason,
            "host_identity": _lineage_host_identity(host_payload),
            "latest_turn_state": latest_turn_state,
            "accepted_pv": binding["accepted_pv"],
            "pointer_generation": binding["pointer_generation"],
            "resume_same_plan_task_id": binding["plan_task_id"],
            "resume_same_row_required": True,
            "active_task_transitioned": False,
            "source_mutated": False,
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
            "private_reasoning_stored": False,
            "emitted_at": _now(),
        }
        receipt["exit_slip_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
        atomic_write_json(exit_path, receipt)
        state = "SEALED"
    event_id = (
        "evt_"
        + sha256_bytes(
            (str(receipt["exit_slip_sha256"]) + "\0lifecycle-exit").encode("utf-8")
        )[:26].lower()
    )
    telemetry = _response_telemetry(host_payload)
    lineage_event = ChatLineage(
        resolved_chat_lineage_root(project_root)
        / f"{binding['evidence_session_id']}.jsonl"
    ).append(
        event_type="turn.lifecycle_exit_slip",
        visible_payload=receipt,
        occurred_at=str(receipt["emitted_at"]),
        session_id=binding["evidence_session_id"],
        task_id=binding["task_id"],
        event_id=event_id,
        actor_type="system",
        model=telemetry["model"],
        submodel=telemetry["submodel"],
        token_metrics=telemetry["token_metrics"],
    )
    session_metadata = cast(
        dict[str, Any], (bound.get("session") or {}).get("metadata") or {}
    )
    host_exit_continuity = seal_host_exit_continuity_packet(
        project_root,
        project_id=str(binding["project_id"]),
        evidence_session_id=str(binding["evidence_session_id"]),
        runtime_task_id=str(binding["task_id"]),
        plan_task_id=str(binding["plan_task_id"]),
        active_plan=cast(dict[str, Any], binding["persistent_plan_row"]),
        event=lineage_event,
        exit_slip=receipt,
        persistence_route=cast(
            dict[str, Any], session_metadata.get("persistence_route") or {}
        ),
        expected_accepted_pv=str(binding["accepted_pv"]),
        expected_pointer_generation=int(binding["pointer_generation"]),
    )
    return {
        "status": "PASS",
        "state": state,
        "receipt": receipt,
        "exit_slip_path": str(exit_path),
        "lineage_event_sha256": lineage_event["event_sha256"],
        "host_exit_continuity": host_exit_continuity,
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
    if _is_compact_session_source(host_payload.get("source")):
        compact_context = _compact_session_reentry_context(
            root,
            bound=bound,
            host_session_id=host_session_id,
            working_directory=str(host_payload.get("cwd") or "") or None,
        )
        return {
            "state": "COMPACT_REENTRY_READY",
            "schema": "evidence-lane.codex-session-compact-reentry.v1",
            "project_id": compact_context["project_id"],
            "evidence_session_id": compact_context["evidence_session_id"],
            "host_session_id_sha256": compact_context["host_session_id_sha256"],
            "binding_epoch_sha256": compact_context["binding_epoch_sha256"],
            "compact_reentry_context": compact_context,
            "authority_reconstructed": False,
            "full_plan_included": False,
            "full_env_uop_included": False,
            "full_runtime_envelope_included": False,
            "scrollback_authority": False,
            "transcript_authority": False,
            "private_reasoning_stored": False,
        }
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
            resolved_chat_lineage_root(project_root)
            / f"{binding['evidence_session_id']}.jsonl"
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
    session_source = str(host_payload.get("source") or "").strip().lower()
    session_trigger = (
        "HOT_REATTACH"
        if "reattach" in session_source
        else "SESSION_START_COLD"
        if session_source in {"cold", "new", "startup"}
        else "SESSION_START_WARM"
    )
    host_plan_rehydration = _prepare_bound_host_plan_rehydration(
        root,
        bound=bound,
        host_payload=host_payload,
        trigger=session_trigger,
        trigger_event_id=str(warm_attach_receipt["receipt_sha256"]),
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
        "host_plan_rehydration": host_plan_rehydration,
        "host_plan_behavior_owner": "ACTIVE_EVIDENCE_LANE_SKILL",
        "hook_performed_host_update_plan": False,
        "persistent_change_display": persistent_change_display,
        "scrollback_authority": False,
        "transcript_authority": False,
        "private_reasoning_stored": False,
    }


def current_persistent_change_display(
    store_root: str | Path,
    *,
    host_payload: dict[str, Any],
) -> dict[str, Any]:
    """Read the current Plan/Delta/source display without advancing lifecycle state.

    This projection exists for PostToolUse because a user can steer a running
    Goal without creating a fresh UserPromptSubmit event.  It deliberately reads
    no tool input, tool output, prompt text, assistant response, or transcript.
    """

    root = Path(store_root).resolve()
    host_session_id = str(host_payload.get("session_id") or "").strip()
    _require(
        bool(host_session_id),
        "TURN_CONTROL_HOST_SESSION_ID_REQUIRED",
        "PostToolUse projection requires the exact governed host-session identity.",
    )
    policy = policy_state(
        root,
        host_session_id=host_session_id,
        cwd=str(host_payload.get("cwd") or ""),
        transcript_path=str(
            host_payload.get("transcript_path")
            or host_payload.get("agent_transcript_path")
            or ""
        ),
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
        transcript_path=str(
            host_payload.get("transcript_path")
            or host_payload.get("agent_transcript_path")
            or ""
        ),
    )
    if host_payload.get(
        "_evidence_lane_boundary_event"
    ) == "PostCompact" or _is_compact_session_source(host_payload.get("source")):
        session = cast(dict[str, Any], bound["session"])
        project_root = Path(bound["project_root"])
        governed_activity_counts = _governed_activity_counts_from_database(
            resolved_chat_lineage_root(project_root) / "codex_turn_control.sqlite",
            project_id=str(session.get("project_id") or ""),
            evidence_session_id=str(session.get("session_id") or ""),
            host_ui_supported=_host_activity_group_support(host_payload),
        )
        pointer, precompact_receipt, _ = _load_compact_precompact_receipt(
            project_root,
            session=session,
        )
        compact_display = {
            "schema": "evidence-lane.codex-compact-postcompact-projection.v1",
            "state": "COMPACT_COMPLETION_PROJECTED_READ_ONLY",
            "project_id": session.get("project_id"),
            "evidence_session_id": session.get("session_id"),
            "active_task_id": (session.get("metadata") or {}).get(
                "active_backlog_task_id"
            ),
            "binding_epoch_sha256": _host_binding_epoch(session),
            "precompact_receipt_sha256": precompact_receipt.get("receipt_sha256"),
            "postcompact_receipt_sha256": pointer.get("postcompact_receipt_sha256"),
            "authority_reconstructed": False,
            "full_plan_included": False,
            "full_env_uop_included": False,
            "source_mutated": False,
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
            "private_reasoning_stored": False,
        }
        compact_display["projection_sha256"] = sha256_bytes(
            canonical_json_bytes(compact_display)
        )
        return {
            "schema": "evidence-lane.codex-persistent-change-tool-projection.v1",
            "state": "COMPACT_COMPLETION_PROJECTED_READ_ONLY",
            "persistent_change_display": compact_display,
            "governed_activity_counts": governed_activity_counts,
            "read_only_projection": True,
            "authority_reconstructed": False,
            "full_plan_rows_embedded": False,
            "private_reasoning_stored": False,
        }
    binding = _binding_snapshot(root, bound)
    project_root = Path(bound["project_root"])
    live_source_snapshot = _source_change_snapshot(
        root,
        binding=binding,
        cwd=str(host_payload.get("cwd") or ""),
    )
    relevant: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    database = resolved_chat_lineage_root(project_root) / "codex_turn_control.sqlite"
    if database.is_file():
        connection = _read_only(database)
        try:
            rows = connection.execute(
                """
                SELECT e.record_json AS entry_json, c.commit_json AS commit_json
                FROM turn_entry e
                LEFT JOIN turn_commit c
                  ON c.control_record_sha256=e.control_record_sha256
                WHERE e.project_id=? AND e.evidence_session_id=?
                ORDER BY e.prompt_index DESC
                """,
                (binding["project_id"], binding["evidence_session_id"]),
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            entry = json.loads(row["entry_json"])
            if entry.get("task_id") != binding["task_id"]:
                continue
            commit = json.loads(row["commit_json"]) if row["commit_json"] else None
            relevant.append((entry, commit))
    latest_entry, latest_commit = relevant[0] if relevant else (None, None)
    uncommitted_count = sum(commit is None for _, commit in relevant)
    entry_source_snapshot = (
        dict(latest_entry.get("source_change_entry") or live_source_snapshot)
        if latest_entry is not None
        else live_source_snapshot
    )
    display = _persistent_change_display(
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
        uncommitted_count=uncommitted_count,
        changed_since_prepare=(
            entry_source_snapshot["worktree_sha256"]
            != live_source_snapshot["worktree_sha256"]
            if latest_entry is not None
            else None
        ),
    )
    tool_name = str(host_payload.get("tool_name") or "").strip() or None
    tool_use_id = str(host_payload.get("tool_use_id") or "").strip()
    governed_activity_counts = _governed_activity_counts_from_database(
        database,
        project_id=str(binding["project_id"]),
        evidence_session_id=str(binding["evidence_session_id"]),
        host_ui_supported=_host_activity_group_support(host_payload),
    )
    core = {
        "schema": "evidence-lane.codex-persistent-change-tool-projection.v1",
        "state": "PROJECTED_READ_ONLY_AFTER_TOOL_USE",
        "project_id": binding["project_id"],
        "evidence_session_id": binding["evidence_session_id"],
        "task_id": binding["task_id"],
        "plan_task_id": binding["plan_task_id"],
        "tool_name": tool_name,
        "tool_use_id_sha256": (
            sha256_bytes(tool_use_id.encode("utf-8")) if tool_use_id else None
        ),
        "persistent_change_display": display,
        "governed_activity_counts": governed_activity_counts,
        "read_only_projection": True,
        "tool_input_stored": False,
        "tool_response_stored": False,
        "transcript_read": False,
        "source_mutated": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "private_reasoning_stored": False,
    }
    core["projection_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


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
    database = (
        resolved_chat_lineage_root(Path(bound["project_root"]))
        / "codex_turn_control.sqlite"
    )
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
