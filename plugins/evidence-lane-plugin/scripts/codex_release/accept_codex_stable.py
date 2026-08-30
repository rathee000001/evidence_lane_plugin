"""Verify the installed Evidence Lane 3.0 Codex Git release before final HIL.

This checker is read-only except for its explicit receipt output. It compares the
exact Git marketplace checkout with Codex's generated installed cache, validates
the enabled canonical selector, derives the exact public catalog from package
registries, and optionally binds a post-restart native route receipt. It
never calls lifecycle, Git, tunnel, candidate, pointer, or HIL actions.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

BASE_RELEASE = "3.0.0"
PLUGIN_NAME = "evidence-lane-plugin"
MARKETPLACE_NAME = "evidence-lane-github"
MARKETPLACE_DISPLAY_NAME = "Main Git Plugin Version"
LOCAL_TESTING_MARKETPLACE_NAME = "evidence-lane-v300-testing-new"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
HOOK_TRUST_SCHEMA = "evidence-lane.codex-hook-trust.v1"
EXPECTED_CODEX_HOST_HOOK_EVENTS = {
    "permissionRequest",
    "postCompact",
    "postToolUse",
    "preCompact",
    "preToolUse",
    "sessionEnd",
    "sessionStart",
    "stop",
    "subagentStart",
    "subagentStop",
    "userPromptSubmit",
}
EXPECTED_PACKAGE_HOOK_EVENTS = {
    "PermissionRequest",
    "PostCompact",
    "PostToolUse",
    "PreCompact",
    "PreToolUse",
    "SessionEnd",
    "SessionStart",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
}
EXPECTED_PLUGIN_CREATOR_LOCAL_UPDATE_ROUTE = {
    "route_law": "PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW",
    "plugin_creator_packing_required_every_local_build": True,
    "fresh_cachebuster_before_each_pack_required": True,
    "direct_cache_edit_allowed": False,
    "standalone_fallback_install_route_allowed": False,
    "source_sync": "IN_PLACE_EXISTING_LOCAL_MARKETPLACE",
    "cache_materialization": "CODEX_PLUGIN_ADD",
    "loaded_old_cache_boundary": (
        "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED"
    ),
    "restart_authority_mode": (
        "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_EXACT_TASK_RESTART"
    ),
    "windows_marketplace_root_rotation_allowed": False,
    "exact_same_task_hidden_restart_required": False,
    "terminal_response_required_before_user_restart": True,
    "manual_exact_channel_restart_required": True,
    "helper_scope": "PREPARE_ONLY_NO_PROCESS_CONTROL",
    "separate_exact_task_turn_drain_required_before_helper": False,
    "helper_installs_plugin": False,
    "child_lease_acknowledgement_before_app_stop_required": False,
    "child_launch_shape": "ABSENT",
    "redirected_parent_pipe_handles_allowed": False,
    "terminal_success_or_failure_receipt_required": False,
    "preparation_receipt_required": True,
    "post_restart_native_readback_required": True,
    "windows_ui_control_allowed": False,
    "cross_task_rehydration_allowed": False,
    "tunnel_start_allowed": False,
}


def _derive_public_surface(plugin_root: Path) -> dict[str, Any]:
    module_path = (
        plugin_root
        / "src"
        / "evidence_lane_plugin"
        / "public_surface_registry.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_accept_public_surface_registry",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("The package public-surface registry is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = module.derive_public_surface_registry(plugin_root)
    if receipt.get("status") != "PASS":
        raise RuntimeError("The package public-surface release totals are stale.")
    return receipt


_SOURCE_PUBLIC_SURFACE = _derive_public_surface(Path(__file__).resolve().parents[2])
EXPECTED_SURFACE_COUNTS = dict(_SOURCE_PUBLIC_SURFACE["catalog"])
EXPECTED_CATALOG = {
    key: EXPECTED_SURFACE_COUNTS[key]
    for key in ("tools", "read", "write", "skills")
}
EXPECTED_BEHAVIOR_OWNERSHIP = {
    "hooks": "LIFECYCLE_CAPTURE_AND_SEALED_EVENTS_ONLY",
    "skills": "NATIVE_PV_READS_AND_HOST_BEHAVIOR",
    "prompt_and_steer_native_reads": [
        "pv_status",
        "pv_task_backlog",
        "pv_query",
        "search",
    ],
    "query_must_use_native_mcp_route": True,
    "internal_hook_lookup_satisfies_native_query": False,
    "host_plan_tool": "update_plan",
    "hook_may_embed_full_plan_rows": False,
    "hook_may_call_or_instruct_host_behavior": False,
    "skill_must_refresh_after_every_prompt_or_steer": True,
    "ordinary_question_appends_plan_delta": False,
    "plan_steer_requires_executable_goal_contract_change": True,
    "plan_steer_refreshes_full_panel_and_current_change_once": True,
    "fail_closed_when_behavior_route_unavailable": True,
    "chat_only_execution_change_allowed": False,
    "source_intake_may_dispatch_one_stable_idempotent_linked_steer": True,
}
EXPECTED_STABLE_ACTIVATION_GATE = {
    "local_rehearsal_stage_only": True,
    "local_rehearsal_activation_allowed": False,
    "exact_commit_package_builder": (
        "scripts/codex_release/build_codex_exact_commit_package.py"
    ),
    "release_authority_joiner": (
        "scripts/codex_release/seal_codex_git_ci_release_authority.py"
    ),
    "external_release_receipt_sealer": (
        "scripts/codex_release/seal_external_release_receipts.py"
    ),
    "stable_install_command": "scripts/codex_release/install_codex_stable.py",
    "stable_update_helper": "scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1",
    "install_completed_before_restart_helper": True,
    "restart_helper_installs_plugin": False,
    "stable_update_reopens_same_bound_host_app": False,
    "stable_update_requires_user_restart_after_terminal_response": True,
    "stable_update_rebinds_exact_task_via_native_binding": True,
    "release_authority_schema": "evidence-lane.codex-git-ci-vercel-release-authority.v2",
    "exact_clean_commit_required": True,
    "governed_native_remote_push_required": True,
    "successful_github_ci_required": True,
    "successful_vercel_branch_preview_required": True,
    "production_deployment_allowed": False,
    "exact_commit_git_marketplace_required": True,
    "delivery_cadence": "DEPENDENCY_COHERENT_INTEGRATION_BUNDLES",
    "per_delta_commit_ci_install_forbidden": True,
    "bundle_count_is_derived_not_quota": True,
    "bundle_boundary_derivation_axes": [
        "SOURCE_SCHEMA_RUNTIME_COUPLING",
        "CROSS_DELTA_TEST_GRAPH",
        "INSTALLED_HOST_PROOF_BOUNDARY",
    ],
    "row_acceptance_evidence_remains_individual": True,
    "per_delta_governance_routes_preserved": [
        "PREPARE_CAPTURE_RETRIEVAL",
        "TASK_ROW_CURRENT_CHANGE_CLASSIFICATION",
        "PV_STATUS_BACKLOG_QUERY_READS",
        "CHATLINEAGE_ACTIVITY",
        "EXACT_ACCEPTANCE_EVIDENCE",
        "LIFECYCLE_TRANSITION",
        "PERSISTENT_PLAN_CURRENT_CHANGE_REPROJECTION",
    ],
    "cross_delta_verification_matrix_required": True,
    "cross_delta_matrix_dimensions": [
        "TASK_ID",
        "CHANGED_SURFACES",
        "LOCAL_TESTS",
        "REMOTE_CHECKS",
        "INSTALLED_HOST_CHECKS",
        "OUTCOME",
        "FAILURE_OWNER",
    ],
    "bundle_failure_policy": "ANY_INCLUDED_ROW_FAILURE_FAILS_BUNDLE_CLOSED",
    "bundle_commit_syncs_root_and_repository_docs": True,
    "stable_install_source": "EXACT_GIT_MAIN_COMMIT_PACKAGE_ONLY",
    "local_or_dirty_worktree_stable_install_allowed": False,
    "all_configured_commit_checks_required_before_stable_install": True,
    "one_stable_update_per_integration_bundle": True,
    "git_marketplace_name": MARKETPLACE_NAME,
    "git_marketplace_display_name": MARKETPLACE_DISPLAY_NAME,
    "git_marketplace_source": "rathee000001/evidence_lane_plugin",
    "one_time_legacy_stable_selector_migration_allowed": True,
    "post_proof_obsolete_cleanup_required": True,
    "same_stable_selector_required_after_migration": True,
    "installed_runtime_prewarm_required": True,
    "runtime_ready_before_task_reopen_required": True,
    "fallback_activation_inferred": False,
}
EXPECTED_BRAND_IDENTITY = {
    "display_name": "Evidence Lane",
    "icon_path": "assets/evidence-lane-icon.png",
    "icon_sha256": "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3DEF87B8A129C4FA",
    "resource_uri": "ui://evidence-lane/governed-console-v6.html",
    "manifest_icon_fields": ["interface.composerIcon", "interface.logo"],
    "required_at_stage": True,
    "required_at_runtime_prewarm": True,
}
EXPECTED_HOST_STORAGE_TUNNEL_MATRIX = {
    "routing_axes_independent": True,
    "account_tier_affects_routing": False,
    "api_billing_affects_routing": False,
    "headless_api": {
        "local_or_persistent_pv_storage": "LOCAL_SQLITE_WHEN_DURABLE",
        "ephemeral_pv_storage": (
            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
        ),
        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
        "flash_frequency": "EVERY_INVOCATION_ENTRY",
    },
    "interactive_codex_app_local_or_persistent": {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
        "host_profile": "CODEX_DESKTOP",
        "desktop_app_variants": {
            "stable": "OpenAI.Codex_2p2nqsd0c76g0!App",
            "beta": "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
            "shared_plugin_contract": True,
            "shared_host_wide_tunnel": True,
            "per_app_tunnel_allowed": False,
            "per_project_or_task_tunnel_allowed": False,
            "helper_requires_exact_requested_app_id": True,
            "cross_app_fallback_allowed": False,
        },
        "native_mcp_available": {
            "tunnel_requirement": "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
            "tunnel_setup_frequency": "NONE",
            "tunnel_key_retention": "NOT_APPLICABLE",
            "tunnel_runtime_lifetime": "NOT_APPLICABLE",
        },
        "host_tool_gap": {
            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
            "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
            "tunnel_key_retention": "CURRENT_WINDOWS_USER_DPAPI_PROFILE",
            "tunnel_runtime_lifetime": "WINDOWS_LOGON_MANAGED_PERSISTENT_HOST",
        },
    },
    "codex_cli_local_or_persistent": {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
        "native_mcp_available": {
            "tunnel_requirement": "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
        },
        "host_tool_gap": {
            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
            "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        },
    },
    "interactive_codex_app_ephemeral_vm": {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
        "native_mcp_available": {
            "tunnel_requirement": "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
        },
        "host_tool_gap": {
            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
            "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
            "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
            "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
        },
    },
    "desktop_container_surface_scope": {
        "supported_container_channels": [
            "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
            "CHATGPT_DESKTOP_BETA",
        ],
        "active_surface": "CODEX",
        "chatgpt_chat_work_scope": "OUT_OF_SCOPE_DEFERRED",
        "authority_binding": (
            "EXACT_HOST_SESSION_PLUS_NATIVE_EVIDENCE_LANE_MCP_ROUTE"
        ),
        "process_package_title_cwd_authority": False,
    },
}
SCHEMA = "evidence-lane.codex-installed-acceptance.v2"
_IGNORED_DIRECTORIES = frozenset({".venv", "__pycache__", ".pytest_cache"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".tmp"})
_RETIRED_COMMAND_ROOTS = ("commands", ".codex-plugin/migrated-command-skills")


class AcceptanceError(RuntimeError):
    """Raised when installed stable identity is not exact."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sealed_json(path: Path, *, hash_field: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(value.get(hash_field) or "")
    body = {key: item for key, item in value.items() if key != hash_field}
    if not claimed or claimed != _sha256_bytes(_json_bytes(body)):
        raise AcceptanceError(f"{path.name} failed its {hash_field} seal.")
    return value


def _inventory(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        if any(part in _IGNORED_DIRECTORIES for part in relative_path.parts):
            continue
        if path.suffix.casefold() in _IGNORED_SUFFIXES:
            continue
        relative = relative_path.as_posix()
        rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "file_count": len(rows),
        "manifest_sha256": _sha256_bytes(_json_bytes(rows)),
        "files": rows,
    }


def _verified_cache_inventory(installed: Path, marketplace: Path) -> dict[str, Any]:
    """Prove exact source parity and absence of the retired command surfaces."""

    installed_inventory = _inventory(installed)
    marketplace_inventory = _inventory(marketplace)
    installed_files = {
        str(row["path"]): row for row in installed_inventory["files"]
    }
    marketplace_files = {
        str(row["path"]): row for row in marketplace_inventory["files"]
    }
    for root in _RETIRED_COMMAND_ROOTS:
        if any(path == root or path.startswith(f"{root}/") for path in marketplace_files):
            raise AcceptanceError("The marketplace contains the retired command layer.")
        if any(path == root or path.startswith(f"{root}/") for path in installed_files):
            raise AcceptanceError("The installed cache contains the retired command layer.")
    source_drift = [
        path
        for path, expected in marketplace_files.items()
        if installed_files.get(path) != expected
    ]
    if source_drift:
        raise AcceptanceError("Codex cache source bytes differ from the marketplace.")
    actual_extra = set(installed_files).difference(marketplace_files)
    if actual_extra:
        raise AcceptanceError("Codex cache contains non-source package extras.")
    return {
        "installed_file_count": installed_inventory["file_count"],
        "installed_manifest_sha256": installed_inventory["manifest_sha256"],
        "marketplace_file_count": marketplace_inventory["file_count"],
        "marketplace_manifest_sha256": marketplace_inventory["manifest_sha256"],
        "source_bytes_match_marketplace": True,
        "retired_command_surface_count": 0,
        "cache_matches_marketplace_exactly": True,
    }


def _surface_inventory(plugin_root: Path, *, version: str) -> dict[str, Any]:
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        *(
            [plugin_root / "hooks" / "logical-actions.json"]
            if (plugin_root / "hooks" / "logical-actions.json").is_file()
            else []
        ),
        *sorted((plugin_root / "hooks").glob("*.exe")),
        *sorted((plugin_root / "hooks").glob("*.py")),
        *sorted((plugin_root / "hooks").glob("*.ps1")),
    ]
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    if not all(path.is_file() for path in hook_paths):
        raise AcceptanceError("The installed persistent hook inventory is incomplete.")
    if frozenset(path.name for path in hook_paths) not in {
        frozenset(
            {
                "hooks.json",
                "invoke_hook.ps1",
                "invoke_hook.py",
                "lifecycle_boundary.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "behavior_handoff.py",
                "event_isolation.py",
                "EvidenceLaneHookHost.exe",
                "hooks.json",
                "invoke_hook.ps1",
                "invoke_hook.py",
                "lifecycle_boundary.py",
                "optional_event_observer.py",
                "permission_request.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "prompt_submit.py",
                "session_start.py",
                "stop_response.py",
                "subagent_start.py",
                "subagent_stop.py",
            }
        ),
        frozenset(
            {
                "behavior_handoff.py",
                "event_isolation.py",
                "EvidenceLaneHookHost.exe",
                "hooks.json",
                "logical-actions.json",
                "invoke_hook.ps1",
                "invoke_hook.py",
                "lifecycle_boundary.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "behavior_handoff.py",
                "event_isolation.py",
                "EvidenceLaneHookHost.exe",
                "hooks.json",
                "logical-actions.json",
                "invoke_hook.ps1",
                "invoke_hook.py",
                "lifecycle_boundary.py",
                "optional_event_observer.py",
                "permission_request.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "prompt_submit.py",
                "session_start.py",
                "stop_response.py",
                "subagent_start.py",
                "subagent_stop.py",
            }
        ),
        frozenset(
            {
                "behavior_handoff.py",
                "event_isolation.py",
                "EvidenceLaneHookHost.exe",
                "hooks.json",
                "logical-actions.json",
                "invoke_hook.ps1",
                "invoke_hook.py",
                "lifecycle_boundary.py",
                "optional_event_observer.py",
                "permission_request.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "prompt_submit.py",
                "session_start.py",
                "stop_response.py",
                "subagent_start.py",
                "subagent_stop.py",
                "subhook_emit.py",
                "subhook_pipeline.py",
                "subhook_seal.py",
                "subhook_transport.py",
                "subhook_validate.py",
            }
        ),
    }:
        raise AcceptanceError("The installed persistent hook inventory is not exact.")

    def inventory(paths: list[Path], *, skill: bool) -> dict[str, Any]:
        rows = [
            {
                "name": path.parent.name if skill else path.name,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        if len(rows) != len({row["name"] for row in rows}):
            raise AcceptanceError("An installed hook or skill name is duplicated.")
        return {
            "count": len(rows),
            "records": rows,
            "inventory_sha256": _sha256_bytes(_json_bytes(rows)),
        }

    hook_files = inventory(hook_paths, skill=False)
    hook_configuration = json.loads(
        (plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    hook_events = dict(hook_configuration.get("hooks") or {})
    registered_events = sorted(hook_events)
    event_order = list(hook_events)
    logical_action_path = plugin_root / "hooks" / "logical-actions.json"
    if logical_action_path.is_file():
        logical_action_configuration = json.loads(
            logical_action_path.read_text(encoding="utf-8")
        )
        declared_logical_actions = logical_action_configuration.get("logicalActions")
        if (
            set(hook_configuration) != {"description", "hooks"}
            or logical_action_configuration.get("schema")
            != "evidence-lane.hook-logical-action-registry.v1"
        ):
            raise AcceptanceError("The host and internal hook registries overlap.")
    else:
        declared_logical_actions = hook_configuration.get("logicalActions")
    if (
        not isinstance(declared_logical_actions, dict)
        or list(declared_logical_actions) != event_order
        or any(
            not isinstance(declared_logical_actions.get(name), list)
            or not declared_logical_actions[name]
            for name in event_order
        )
    ):
        raise AcceptanceError("The installed hook logical-action registry is invalid.")
    event_action_inventory: list[dict[str, Any]] = []
    for event_ordinal, event_name in enumerate(event_order, start=1):
        actions: list[dict[str, Any]] = []
        groups = hook_events[event_name]
        if not isinstance(groups, list) or not groups:
            raise AcceptanceError("An installed hook event has no handler group.")
        for group_ordinal, group in enumerate(groups, start=1):
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list) or not handlers:
                raise AcceptanceError("An installed hook group has no handler action.")
            for group_action_ordinal, handler in enumerate(handlers, start=1):
                if not isinstance(handler, dict):
                    raise AcceptanceError("An installed hook handler action is invalid.")
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
                        "command_sha256": _sha256_bytes(
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
    logical_action_inventory = [
        {
            "hook_number": event_ordinal,
            "event_name": event_name,
            "display_number": f"Hook {event_ordinal}",
            "logical_action_count": len(declared_logical_actions[event_name]),
            "logical_actions": [
                {
                    "logical_action_number": f"{event_ordinal}.L{action_ordinal}",
                    "event_logical_action_ordinal": action_ordinal,
                    "action": action,
                    "project_plan_goal_hil_effect": "NONE",
                }
                for action_ordinal, action in enumerate(
                    declared_logical_actions[event_name], start=1
                )
            ],
        }
        for event_ordinal, event_name in enumerate(event_order, start=1)
    ]
    hook_inventory = {
        "count": len(registered_events),
        "count_semantics": "REGISTERED_EVENT_COUNT",
        "registered_event_count": len(registered_events),
        "registered_events": registered_events,
        "handler_count": handler_count,
        "handler_count_semantics": "TOTAL_NESTED_HANDLER_ACTION_COUNT",
        "event_order": event_order,
        "event_action_inventory": event_action_inventory,
        "event_action_inventory_sha256": _sha256_bytes(
            _json_bytes(event_action_inventory)
        ),
        "logical_action_count": sum(
            int(row["logical_action_count"]) for row in logical_action_inventory
        ),
        "logical_action_count_semantics": (
            "NUMBERED_SERIAL_TRANSPORT_STEPS_INSIDE_HANDLER_ACTIONS"
        ),
        "logical_action_inventory": logical_action_inventory,
        "logical_action_inventory_sha256": _sha256_bytes(
            _json_bytes(logical_action_inventory)
        ),
        "hook_file_count": hook_files["count"],
        "records": hook_files["records"],
        "file_inventory_sha256": hook_files["inventory_sha256"],
        "event_inventory_sha256": _sha256_bytes(_json_bytes(registered_events)),
    }
    hook_inventory["inventory_sha256"] = _sha256_bytes(
        _json_bytes(hook_inventory)
    )
    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": version,
        "hooks": hook_inventory,
        "skills": inventory(skill_paths, skill=True),
        "search_toolchain": _search_toolchain_inventory(plugin_root),
        "catalog": dict(EXPECTED_CATALOG),
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = _sha256_bytes(_json_bytes(core))
    return core


def _search_toolchain_inventory(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / "toolchains" / "search-tools.v1.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(
            "The installed governed search toolchain manifest is missing."
        ) from exc
    tools = manifest.get("tools")
    if (
        manifest.get("schema") != "evidence-lane.search-toolchain-manifest.v1"
        or manifest.get("scope") != "ALL_GOVERNED_PROJECTS"
        or manifest.get("resolution_order")
        != [
            "PACKAGE_LOCAL_VERIFIED_BINARY",
            "EXPLICIT_CONFIGURED_VERIFIED_HOST_BINARY",
            "DETERMINISTIC_BUILTIN_FALLBACK",
        ]
        or manifest.get("auto_download_during_mcp_handshake") is not False
        or manifest.get("path_lookup_allowed") is not False
        or manifest.get("shell_execution_allowed") is not False
        or not isinstance(tools, list)
        or [row.get("tool_id") for row in tools if isinstance(row, dict)]
        != ["ripgrep"]
        or manifest.get("fts_authority")
        != {
            "backend": "SQLITE_FTS5",
            "query_mode": "BOUNDED_FTS5",
            "scope": "PLAN_LANE_CHATLINEAGE_AND_PROJECT_SECTORS",
            "pointer_and_locator_required": True,
            "model_context_policy": "BOUNDED_QUERY_RESULTS_ONLY",
            "pv_package_loaded_into_model_context": False,
            "fallback": "FAIL_CLOSED_WHEN_SQLITE_FTS5_UNAVAILABLE",
        }
    ):
        raise AcceptanceError("The installed governed search toolchain drifted.")
    records: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise AcceptanceError("An installed search tool record is invalid.")
        package_binaries = tool.get("package_binaries")
        if not isinstance(package_binaries, dict):
            raise AcceptanceError("An installed search binary inventory is invalid.")
        for platform_id, binary_record in sorted(package_binaries.items()):
            if not isinstance(binary_record, dict):
                raise AcceptanceError("An installed search binary record is invalid.")
            binary = (plugin_root / str(binary_record.get("path") or "")).resolve()
            try:
                binary.relative_to(plugin_root.resolve())
            except ValueError as exc:
                raise AcceptanceError(
                    "An installed search tool escaped the plugin package."
                ) from exc
            licenses = [
                (plugin_root / str(item)).resolve()
                for item in binary_record.get("licenses") or []
            ]
            if (
                not binary.is_file()
                or binary.stat().st_size != binary_record.get("size_bytes")
                or _sha256(binary) != binary_record.get("sha256")
                or not licenses
                or any(not path.is_file() for path in licenses)
            ):
                raise AcceptanceError("An installed search tool identity drifted.")
            records.append(
                {
                    "tool_id": tool["tool_id"],
                    "platform_id": platform_id,
                    "version": tool["version"],
                    "role": tool["role"],
                    "license_spdx": tool["license_spdx"],
                    "binary_sha256": binary_record["sha256"],
                    "size_bytes": binary_record["size_bytes"],
                    "license_sha256": sorted(_sha256(path) for path in licenses),
                    "fallback_backend": tool["fallback_backend"],
                }
            )
    body = {
        "schema": "evidence-lane.codex-packaged-search-toolchain.v1",
        "status": "PASS",
        "manifest_sha256": _sha256(manifest_path),
        "fts_authority": manifest["fts_authority"],
        "resolution_order": manifest["resolution_order"],
        "records": records,
        "record_count": len(records),
        "path_lookup_allowed": False,
        "auto_download_during_mcp_handshake": False,
        "fallbacks_required": True,
    }
    body["inventory_sha256"] = _sha256_bytes(_json_bytes(body))
    return body


def _catalog(plugin_root: Path) -> dict[str, Any]:
    path = plugin_root / "src" / "evidence_lane_plugin" / "mcp_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rows: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                continue
            keywords = {item.arg: item.value for item in decorator.keywords if item.arg}
            name_node = keywords.get("name")
            annotation_node = keywords.get("annotations")
            if not (
                isinstance(name_node, ast.Constant)
                and isinstance(name_node.value, str)
                and isinstance(annotation_node, ast.Name)
            ):
                raise AcceptanceError("Every native tool needs a literal name and annotation.")
            rows.append(
                {
                    "name": name_node.value,
                    "annotation": annotation_node.id,
                }
            )
    rows.sort(key=lambda row: row["name"])
    names = [row["name"] for row in rows]
    read = sum(row["annotation"] == "_READ_ONLY" for row in rows)
    write = len(rows) - read
    if (
        len(rows) != EXPECTED_CATALOG["tools"]
        or len(names) != len(set(names))
        or read != EXPECTED_CATALOG["read"]
        or write != EXPECTED_CATALOG["write"]
    ):
        raise AcceptanceError(
            "The installed native "
            f"{EXPECTED_CATALOG['tools']}/{EXPECTED_CATALOG['read']}/"
            f"{EXPECTED_CATALOG['write']} tool catalog drifted."
        )
    return {
        "tools": len(rows),
        "read": read,
        "write": write,
        "tool_names_unique": True,
        "static_catalog_sha256": _sha256_bytes(_json_bytes(rows)),
    }


def _engine_version(plugin_root: Path) -> str:
    path = plugin_root / "src" / "evidence_lane_plugin" / "constants.py"
    match = re.search(
        r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if match is None:
        raise AcceptanceError("ENGINE_VERSION is unavailable.")
    return match.group("version")


def _validate_plugin(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text("utf-8"))
    release = json.loads(
        (plugin_root / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    stable = dict(release.get("stable") or {})
    local_testing = dict(release.get("local_testing") or {})
    live_slots = dict(release.get("live_slot_policy") or {})
    helper_distribution = dict(release.get("helper_distribution_policy") or {})
    maintainer_helper = dict(
        helper_distribution.get("maintainer_release_helper") or {}
    )
    plugin_creator_local_update = dict(
        maintainer_helper.get("plugin_creator_local_update_route") or {}
    )
    behavior_ownership = dict(release.get("behavior_ownership") or {})
    stable_activation_gate = dict(release.get("stable_activation_gate") or {})
    brand_identity = dict(release.get("brand_identity") or {})
    promotion = dict(release.get("promotion_gate") or {})
    remote_git = dict(release.get("remote_git_policy") or {})
    skills = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    native = json.loads((plugin_root / ".mcp.json").read_text("utf-8"))
    interface = dict(manifest.get("interface") or {})
    brand_icon = plugin_root / str(brand_identity.get("icon_path") or "")
    required_release_helpers = (
        plugin_root / "scripts" / "codex_release" / "install_codex_stable.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "build_codex_exact_commit_package.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "seal_codex_git_ci_release_authority.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "seal_external_release_receipts.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "Prepare-EvidenceLaneCodexRestart.ps1",
        plugin_root / "scripts" / "codex_release" / "accept_codex_stable.py",
    )
    forbidden = (
        plugin_root / ".app.json",
        plugin_root / "release-channels.json",
        plugin_root / "remote_adapter",
        plugin_root / "evidence",
    )
    external_app_artifacts = tuple(plugin_root.glob("*-app-connection.json")) + tuple(
        plugin_root.glob("*-app-submission.json")
    )
    if (
        manifest.get("name") != PLUGIN_NAME
        or not version.startswith(f"{BASE_RELEASE}+codex.")
        or manifest.get("mcpServers") != "./.mcp.json"
        or "apps" in manifest
        or project.get("project", {}).get("version") != BASE_RELEASE
        or _engine_version(plugin_root) != BASE_RELEASE
        or release.get("schema") != "evidence-lane.codex-release-channel.v2"
        or stable.get("release") != BASE_RELEASE
        or stable.get("slot_role") != "main-git-release"
        or stable.get("codex_marketplace_slot") != MARKETPLACE_NAME
        or stable.get("marketplace_display_name") != MARKETPLACE_DISPLAY_NAME
        or stable.get("install_source")
        != "GIT_MAIN_EXACT_COMMIT_AFTER_GOVERNED_MERGE"
        or stable.get("byte_frozen") is not False
        or stable.get("updates_require_verified_unique_build_identity") is not True
        or stable.get("stable_selector_is_persistent") is not True
        or stable.get("stable_updates_reinstall_in_place") is not True
        or stable.get("build_identity_is_receipt_not_selector") is not True
        or stable.get("native_tool_count") != EXPECTED_CATALOG["tools"]
        or stable.get("native_read_tool_count") != EXPECTED_CATALOG["read"]
        or stable.get("native_write_tool_count") != EXPECTED_CATALOG["write"]
        or stable.get("skill_count") != EXPECTED_CATALOG["skills"]
        or len(skills) != EXPECTED_CATALOG["skills"]
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or local_testing.get("release_line") != BASE_RELEASE
        or local_testing.get("slot_role") != "versioned-local-testing"
        or local_testing.get("codex_marketplace_slot")
        != LOCAL_TESTING_MARKETPLACE_NAME
        or local_testing.get("marketplace_display_name") != "Local Testing Slot"
        or local_testing.get("fresh_package_version_per_local_build") is not True
        or local_testing.get("stable_git_main_mutation_allowed_during_local_build")
        is not False
        or local_testing.get("helper_installs_plugin") is not False
        or live_slots.get("exact_slot_count") != 2
        or live_slots.get("allowed_slots")
        != [
            "main-git-release",
            "versioned-local-testing",
        ]
        or live_slots.get("max_enabled_plugin_count") != 1
        or live_slots.get("exact_registered_plugin_count") != 2
        or live_slots.get("stable_selector_growth_allowed") is not False
        or live_slots.get("max_active_native_mcp_count") != 1
        or live_slots.get("max_active_tunnel_count") != 1
        or live_slots.get("inactive_slot_remains_installed") is not True
        or live_slots.get("obsolete_marketplace_registrations_must_be_absent")
        is not True
        or plugin_creator_local_update
        != EXPECTED_PLUGIN_CREATOR_LOCAL_UPDATE_ROUTE
        or behavior_ownership != EXPECTED_BEHAVIOR_OWNERSHIP
        or stable_activation_gate != EXPECTED_STABLE_ACTIVATION_GATE
        or brand_identity != EXPECTED_BRAND_IDENTITY
        or interface.get("displayName") != brand_identity.get("display_name")
        or interface.get("composerIcon") != "./assets/evidence-lane-icon.png"
        or interface.get("logo") != "./assets/evidence-lane-icon.png"
        or not brand_icon.is_file()
        or _sha256(brand_icon) != brand_identity.get("icon_sha256")
        or release.get("host_storage_tunnel_matrix")
        != EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
        or promotion.get("mode") != "CODE"
        or promotion.get("ci_cd_law") != "CONTROLLED_REQUIRED"
        or promotion.get("explicit_authority_hil_required") is not True
        or remote_git.get("per_push_confirmation_token_required") is not False
        or remote_git.get("automatic_push_scope")
        != "GITHUB_APP_GOVERNED_FEATURE_BRANCH_THEN_EXACT_MAIN_MERGE"
        or remote_git.get("host_managed_credentials_only") is not True
        or remote_git.get("main_push_allowed") is not False
        or remote_git.get("merge_allowed") is not True
        or set(native.get("mcpServers") or {}) != {"evidence-lane"}
        or not all(path.is_file() for path in required_release_helpers)
        or any(path.exists() for path in forbidden)
        or bool(external_app_artifacts)
    ):
        raise AcceptanceError("The installed v2 package identity or boundary drifted.")
    hooks = json.loads((plugin_root / "hooks" / "hooks.json").read_text("utf-8"))
    hook_events = dict(hooks.get("hooks") or {})
    logical_action_path = plugin_root / "hooks" / "logical-actions.json"
    logical_actions = (
        json.loads(logical_action_path.read_text("utf-8")).get("logicalActions")
        if logical_action_path.is_file()
        else hooks.get("logicalActions")
    )
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in hook_events.values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    )
    post_groups = hook_events.get("PostToolUse") or []
    post_matcher = str(post_groups[0].get("matcher") or "") if post_groups else ""
    if (
        set(hook_events) != EXPECTED_PACKAGE_HOOK_EVENTS
        or (
            logical_action_path.is_file()
            and set(hooks) != {"description", "hooks"}
        )
        or not isinstance(logical_actions, dict)
        or list(logical_actions) != list(hook_events)
        or any(
            not isinstance(logical_actions.get(name), list)
            or not logical_actions[name]
            for name in hook_events
        )
        or handler_count < len(EXPECTED_PACKAGE_HOOK_EVENTS)
        or any(
            not isinstance(groups, list)
            or not groups
            or any(
                not isinstance(group, dict)
                or not isinstance(group.get("hooks"), list)
                or not group["hooks"]
                for group in groups
            )
            for groups in hook_events.values()
        )
        or post_matcher
    ):
        raise AcceptanceError("The installed persistent turn hooks drifted.")
    persistent_notice_markers = {
        "session_start.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_NOTICE=",
        "prompt_submit.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
        "post_tool_use.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=",
        "stop_response.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
    }
    for name, marker in persistent_notice_markers.items():
        source = (plugin_root / "hooks" / name).read_text(encoding="utf-8")
        required = (
            "render_persistent_notice",
            'result["systemMessage"]',
            marker,
        )
        if any(value not in source for value in required):
            raise AcceptanceError(f"{name} does not emit the persistent change notice.")
    return {
        "plugin_id": PLUGIN_NAME,
        "version": version,
        "manifest_sha256": _sha256(manifest_path),
        "release_contract_sha256": _sha256(
            plugin_root / "scripts" / "codex-release-channel.json"
        ),
        "skills": len(skills),
        "catalog": _catalog(plugin_root),
        "hook_events": [
            "PostToolUse",
            "SessionStart",
            "Stop",
            "UserPromptSubmit",
        ],
        "mode": "CODE",
        "ci_cd_law": "CONTROLLED_REQUIRED",
        "host_storage_tunnel_matrix": EXPECTED_HOST_STORAGE_TUNNEL_MATRIX,
        "surface_inventory": _surface_inventory(plugin_root, version=version),
    }


def _native_receipt(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    route = payload.get("mcp_route_identity") or payload
    if (
        route.get("schema") != "evidence-lane.native-mcp-route-receipt.v1"
        or route.get("status") != "PASS"
        or route.get("server_identity") != "evidence-lane"
        or route.get("canonical_tool_namespace") != "mcp__evidence_lane__"
        or route.get("exposure_profile") != "FULL_LIFECYCLE"
        or route.get("tool_count") != EXPECTED_CATALOG["tools"]
        or route.get("tool_names_unique") is not True
        or route.get("project_route_argument_required") is not True
        or route.get("cross_project_fallback_allowed") is not False
        or not route.get("tool_catalog_sha256")
    ):
        raise AcceptanceError("The post-restart native route receipt drifted.")
    return {
        "status": "PASS",
        "server_identity": route["server_identity"],
        "canonical_tool_namespace": route["canonical_tool_namespace"],
        "tool_count": route["tool_count"],
        "tool_catalog_sha256": route["tool_catalog_sha256"],
        "receipt_file_sha256": _sha256(path),
    }


def _native_hook_control_receipt(
    path: Path | None,
    *,
    plugin_selector: str,
) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = _sealed_json(path, hash_field="receipt_sha256")
    after_states = dict(payload.get("after_states") or {})
    verified = dict(payload.get("verified_event_receipts") or {})
    if (
        payload.get("schema") != "evidence-lane.native-hook-event-control.v1"
        or payload.get("status") != "PASS"
        or payload.get("plugin_selector") != plugin_selector
        or set(after_states) != set(EXPECTED_CODEX_HOST_HOOK_EVENTS)
        or any(value is not True for value in after_states.values())
        or set(verified) != set(EXPECTED_CODEX_HOST_HOOK_EVENTS)
        or any(
            re.fullmatch(r"[A-F0-9]{64}", str(value).upper()) is None
            for value in verified.values()
        )
        or payload.get("direct_config_file_write") is not False
        or payload.get("windows_ui_control_used") is not False
        or payload.get("unrelated_plugin_state_mutated") is not False
    ):
        raise AcceptanceError("The native post-restart hook-control receipt drifted.")
    return {
        "status": "PASS",
        "plugin_selector": plugin_selector,
        "enabled_event_count": len(after_states),
        "registered_events": sorted(after_states),
        "receipt_sha256": payload["receipt_sha256"],
        "receipt_file_sha256": _sha256(path),
    }


def accept(args: argparse.Namespace) -> dict[str, Any]:
    installed = args.installed_plugin.resolve()
    marketplace = args.marketplace_plugin.resolve()
    config = args.codex_config.resolve()
    archive = args.archive.resolve()
    package_argument = getattr(args, "package_receipt", None) or getattr(
        args, "rehearsal_receipt", None
    )
    if package_argument is None:
        raise AcceptanceError("An exact package receipt is required.")
    rehearsal_path = Path(package_argument).resolve()
    install_path = args.installation_receipt.resolve()
    rehearsal = json.loads(rehearsal_path.read_text(encoding="utf-8"))
    installation = _sealed_json(install_path, hash_field="receipt_sha256")
    activation = dict(installation.get("activation") or {})
    activation_authority = dict(installation.get("activation_authority") or {})
    runtime_prewarm = dict(activation.get("runtime_prewarm") or {})
    runtime_prewarm_core = dict(runtime_prewarm)
    runtime_prewarm_sha256 = str(
        runtime_prewarm_core.pop("receipt_sha256", "")
    ).upper()
    plugin_add = dict(activation.get("plugin_add") or {})
    exact_selector = str(plugin_add.get("pluginId") or "")
    hook_trust = dict(activation.get("hook_trust") or {})
    hook_control = _native_hook_control_receipt(
        getattr(args, "hook_control_receipt", None),
        plugin_selector=exact_selector,
    )
    surface_change = dict(installation.get("surface_change_display") or {})
    live_slot_contract = dict(installation.get("live_slot_contract") or {})
    if (
        rehearsal.get("status") != "PASS"
        or rehearsal.get("archive", {}).get("filename") != archive.name
        or rehearsal.get("archive", {}).get("sha256") != _sha256(archive)
        or installation.get("status") != "PASS"
        or installation.get("schema") != "evidence-lane.codex-stable-installation.v2"
        or installation.get("archive_sha256") != _sha256(archive)
        or activation_authority.get("status") != "PASS"
        or activation_authority.get("boundary")
        != "GOVERNED_GIT_MAIN_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT"
        or activation_authority.get("vercel_preview_ready") is not True
        or activation_authority.get("production_deployment") is not False
        or activation.get("state") != "INSTALLED_RESTART_REQUIRED"
        or activation.get("runtime_ready_before_task_reopen") is not True
        or installation.get("runtime_ready_before_task_reopen") is not True
        or runtime_prewarm.get("status") != "PASS"
        or runtime_prewarm.get("runtime_ready_before_task_reopen") is not True
        or runtime_prewarm.get("task_reopened") is not False
        or runtime_prewarm.get("tool_count") != EXPECTED_CATALOG["tools"]
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(runtime_prewarm.get("tool_catalog_sha256") or ""),
        )
        is None
        or runtime_prewarm.get("resource_uri")
        != EXPECTED_BRAND_IDENTITY["resource_uri"]
        or runtime_prewarm.get("brand_icon_sha256")
        != EXPECTED_BRAND_IDENTITY["icon_sha256"]
        or runtime_prewarm_sha256
        != _sha256_bytes(_json_bytes(runtime_prewarm_core))
        or exact_selector != PLUGIN_SELECTOR
        or plugin_add.get("installedPath") is None
        or Path(plugin_add["installedPath"]).resolve() != installed
        or installation.get("generated_cache_written_directly") is not False
        or installation.get("previous_release_cache_deleted") is not False
        or live_slot_contract.get("schema")
        != "evidence-lane.codex-two-slot-main-local-registry.v1"
        or live_slot_contract.get("status") != "PASS"
        or live_slot_contract.get("exact_live_slot_count") != 2
        or live_slot_contract.get("stable_slot") != "stable-git-main"
        or live_slot_contract.get("stable_selector") != PLUGIN_SELECTOR
        or live_slot_contract.get("local_slot") != "versioned-local-testing"
        or live_slot_contract.get("local_testing_selector")
        != "evidence-lane-plugin@evidence-lane-v300-testing-new"
        or live_slot_contract.get("obsolete_selector_present") is not False
        or live_slot_contract.get("pre_3_0_fallback_allowed") is not False
        or installation.get("post_proof_obsolete_cleanup_completed") is not True
        or installation.get("obsolete_cleanup_used_supported_codex_apis") is not True
        or installation.get("credential_requested_or_stored") is not False
        or surface_change.get("schema")
        != "evidence-lane.codex-installed-surface-change-display.v2"
        or surface_change.get("raw_paths_included") is not False
        or surface_change.get("private_research_question_included") is not False
        or hook_trust.get("schema") != HOOK_TRUST_SCHEMA
        or hook_trust.get("status")
        != "PENDING_NATIVE_POST_RESTART_VERIFICATION"
        or hook_trust.get("plugin_selector") != exact_selector
        or hook_trust.get("hook_count")
        != len(EXPECTED_CODEX_HOST_HOOK_EVENTS)
        or hook_trust.get("records") != []
        or hook_trust.get("all_enabled") is not False
        or hook_trust.get("hooks_enabled_by_update") is not False
        or hook_trust.get("native_post_restart_verification_required") is not True
        or dict(activation.get("git_marketplace_source") or {}).get("status")
        != "PASS"
        or dict(activation.get("git_marketplace_source") or {}).get("source_type")
        != "git"
        or dict(activation.get("git_marketplace_source") or {}).get(
            "exact_commit_package_bytes_match"
        )
        is not True
        or dict(activation.get("post_proof_cleanup") or {}).get("status") != "PASS"
        or dict(activation.get("post_proof_cleanup") or {}).get(
            "exact_installed_slot_count"
        )
        != 2
    ):
        raise AcceptanceError("The archive or supported installation receipt drifted.")
    configured = tomllib.loads(config.read_text(encoding="utf-8"))
    plugin_settings = dict(configured.get("plugins") or {})
    enabled = sorted(
        selector
        for selector, value in plugin_settings.items()
        if selector.startswith(f"{PLUGIN_NAME}@") and value.get("enabled") is True
    )
    exact_mcp = dict(plugin_settings.get(exact_selector) or {}).get("mcp_servers")
    exact_mcp_settings = (
        dict(exact_mcp).get("evidence-lane")
        if isinstance(exact_mcp, dict)
        else None
    )
    inactive_mcp_enabled = [
        selector
        for selector, value in plugin_settings.items()
        if selector.startswith(f"{PLUGIN_NAME}@")
        and selector != exact_selector
        and isinstance(value, dict)
        and isinstance(value.get("mcp_servers"), dict)
        and isinstance(value["mcp_servers"].get("evidence-lane"), dict)
        and value["mcp_servers"]["evidence-lane"].get("enabled") is True
    ]
    if (
        enabled != [exact_selector]
        or not isinstance(exact_mcp_settings, dict)
        or exact_mcp_settings.get("enabled") is not True
        or inactive_mcp_enabled
    ):
        raise AcceptanceError("Exactly the v2 Evidence Lane selector must be enabled.")
    installed_identity = _validate_plugin(installed)
    marketplace_identity = _validate_plugin(marketplace)
    if (
        surface_change.get("current_plugin_version")
        != installed_identity["version"]
        or surface_change.get("current_surface_inventory_sha256")
        != installed_identity["surface_inventory"]["surface_inventory_sha256"]
        or surface_change.get("hooks", {}).get("count")
        != installed_identity["surface_inventory"]["hooks"]["count"]
        or surface_change.get("hooks", {}).get("count_semantics")
        != "REGISTERED_EVENT_COUNT"
        or surface_change.get("hooks", {}).get("registered_event_count")
        != len(EXPECTED_PACKAGE_HOOK_EVENTS)
        or surface_change.get("hooks", {}).get("registered_events")
        != sorted(EXPECTED_PACKAGE_HOOK_EVENTS)
        or surface_change.get("hooks", {}).get("handler_count")
        != installed_identity["surface_inventory"]["hooks"]["handler_count"]
        or surface_change.get("hooks", {}).get("handler_count_semantics")
        != "TOTAL_NESTED_HANDLER_ACTION_COUNT"
        or surface_change.get("hooks", {}).get("event_order")
        != installed_identity["surface_inventory"]["hooks"]["event_order"]
        or surface_change.get("hooks", {}).get("event_action_inventory")
        != installed_identity["surface_inventory"]["hooks"][
            "event_action_inventory"
        ]
        or surface_change.get("hooks", {}).get("event_action_inventory_sha256")
        != installed_identity["surface_inventory"]["hooks"][
            "event_action_inventory_sha256"
        ]
        or surface_change.get("hooks", {}).get("logical_action_count")
        != installed_identity["surface_inventory"]["hooks"][
            "logical_action_count"
        ]
        or surface_change.get("hooks", {}).get("logical_action_count_semantics")
        != "NUMBERED_SERIAL_TRANSPORT_STEPS_INSIDE_HANDLER_ACTIONS"
        or surface_change.get("hooks", {}).get("logical_action_inventory")
        != installed_identity["surface_inventory"]["hooks"][
            "logical_action_inventory"
        ]
        or surface_change.get("hooks", {}).get("logical_action_inventory_sha256")
        != installed_identity["surface_inventory"]["hooks"][
            "logical_action_inventory_sha256"
        ]
        or surface_change.get("skills", {}).get("count")
        != EXPECTED_CATALOG["skills"]
        or surface_change.get("search_toolchain", {}).get("status") != "PASS"
        or surface_change.get("search_toolchain", {}).get("record_count") != 1
        or surface_change.get("search_toolchain", {}).get("inventory_sha256")
        != installed_identity["surface_inventory"]["search_toolchain"][
            "inventory_sha256"
        ]
        or surface_change.get("search_toolchain", {}).get("fts_authority")
        != installed_identity["surface_inventory"]["search_toolchain"][
            "fts_authority"
        ]
        or surface_change.get("search_toolchain", {}).get("raw_paths_included")
        is not False
        or surface_change.get("catalog", {}).get("tools")
        != EXPECTED_CATALOG["tools"]
    ):
        raise AcceptanceError("The installed surface-change display drifted.")
    package_inventory = _verified_cache_inventory(installed, marketplace)
    native = _native_receipt(args.native_route_receipt)
    state = (
        "POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL"
        if native is not None and hook_control is not None
        else "POST_RESTART_NATIVE_HOOK_CONTROL_REQUIRED"
        if native is not None
        else "PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED"
    )
    body = {
        "schema": SCHEMA,
        "status": "PASS",
        "state": state,
        "installed_plugin": installed_identity,
        "marketplace_plugin": marketplace_identity,
        "package_inventory": package_inventory,
        "catalog": dict(EXPECTED_CATALOG),
        "surface_change_display": surface_change,
        "enabled_selector": exact_selector,
        "hook_control_receipt_sha256": (
            hook_control["receipt_sha256"] if hook_control is not None else None
        ),
        "hook_trust": {
            "status": (
                "PASS" if hook_control is not None else "PENDING_NATIVE_CONTROL"
            ),
            "hook_count": len(EXPECTED_CODEX_HOST_HOOK_EVENTS),
            "registered_events": (
                hook_control["registered_events"]
                if hook_control is not None
                else sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS)
            ),
            "all_enabled": hook_control is not None,
            "selector": exact_selector,
        },
        "archive_sha256": _sha256(archive),
        "package_receipt_sha256": _sha256(rehearsal_path),
        "installation_receipt_sha256": _sha256(install_path),
        "native_route": native,
        "native_hook_control": hook_control,
        "restart_verified": native is not None,
        "ready_for_hil": native is not None and hook_control is not None,
        "installed_host_hil_required": True,
        "hil_inferred": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "git_invoked": False,
        "tunnel_invoked": False,
        "source_mutated": False,
    }
    body["receipt_sha256"] = _sha256_bytes(_json_bytes(body))
    _write_atomic(args.output.resolve(), body)
    return body


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-plugin", type=Path, required=True)
    parser.add_argument("--marketplace-plugin", type=Path, required=True)
    parser.add_argument("--codex-config", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    package = parser.add_mutually_exclusive_group(required=True)
    package.add_argument("--package-receipt", type=Path)
    package.add_argument(
        "--rehearsal-receipt",
        type=Path,
        help="Compatibility name for historical staging receipts.",
    )
    parser.add_argument("--installation-receipt", type=Path, required=True)
    parser.add_argument("--native-route-receipt", type=Path)
    parser.add_argument("--hook-control-receipt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    print(json.dumps(accept(_parser().parse_args()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
