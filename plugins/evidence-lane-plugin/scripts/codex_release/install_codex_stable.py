"""Stage and activate one sealed Evidence Lane 3.0 Codex marketplace package.

The script uses supported Codex marketplace, plugin, hook, and configuration
APIs. Maintainer-local execution has exactly two persistent selectors: exact
verified Git ``main`` and versioned local testing. The retired branch-recovery
selector is purge-only and can never be installed or selected. Build hashes belong in
receipts, never in new plugin identities.  The script never writes a generated
plugin cache directly. For each newly materialized local version it launches the
installed tunnel helper in one visible console so the user can enter the Runtime
API key directly into a SecureString/DPAPI envelope; plaintext credentials never
enter this process, arguments, environment output, logs, source, or receipts.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

BASE_RELEASE = "3.0.0"
LOCAL_REGRESSION_PASS_STATUSES = {
    "PASS_WITH_TARGETED_FAILURE_CLOSURE",
    "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED",
}
MARKETPLACE_NAME = "evidence-lane-github"
MARKETPLACE_DISPLAY_NAME = "Main Git Plugin Version"
LOCAL_TESTING_MARKETPLACE_NAME = "evidence-lane-v300-testing-new"
LOCAL_TESTING_MARKETPLACE_DISPLAY_NAME = "Local Testing Slot"
RETIRED_COMMAND_ROOTS = ("commands", ".codex-plugin/migrated-command-skills")
PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW = "PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW"
PLUGIN_CREATOR_LOCAL_CACHE_RESTART_STATE = (
    "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED"
)
PLUGIN_CREATOR_LOCAL_CACHE_RESTART_CONFIRMATION = (
    "EXPLICIT_PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART"
)
LOCAL_INSTALLED_STATIC_TUNNEL_PENDING_STATE = (
    "LOCAL_INSTALLED_STATIC_ACCEPTANCE_TUNNEL_PENDING"
)
LOCAL_TUNNEL_ACTIVATION_SCHEMA = (
    "evidence-lane.plugin-creator-local-tunnel-activation.v1"
)
LOCAL_TUNNEL_FAILURE_SCHEMA = (
    "evidence-lane.plugin-creator-local-tunnel-failure.v1"
)
NPM_CODEX_CLI_RELATIVE_PATH = Path(
    "npm/node_modules/@openai/codex/node_modules/@openai/"
    "codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
)
MARKETPLACE_SOURCE = "rathee000001/evidence_lane_plugin"
PLUGIN_NAME = "evidence-lane-plugin"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
TWO_SLOT_REGISTRY_SCHEMA = "evidence-lane.codex-two-slot-main-local-registry.v1"
TWO_SLOT_SELECTORS = {
    "stable-git-main": PLUGIN_SELECTOR,
    "versioned-local-testing": (f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"),
}


def _require_hidden_runtime_control_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    expected = (
        Path.home() / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
    ).resolve()
    if resolved != expected:
        raise InstallationError(
            "Codex installation and runtime control must use the exact hidden "
            "~/.codex/plugins/runtime/evidence-lane-plugin root."
        )
    return resolved


def _derive_public_surface(plugin_root: Path) -> dict[str, Any]:
    module_path = (
        plugin_root / "src" / "evidence_lane_plugin" / "public_surface_registry.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_installer_public_surface_registry",
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
    key: EXPECTED_SURFACE_COUNTS[key] for key in ("tools", "read", "write", "skills")
}
EXPECTED_WORKFLOW_SCOPE = {
    "plugin_release_cadence": "ONE_AUTHORIZED_LOGICAL_RELEASE_COMMIT_BATCH",
    "plugin_release_steps": [
        "GOVERNED_FEATURE_COMMIT_AND_EXACT_MAIN_MERGE",
        "CLEAN_CI_AND_SECURITY",
        "GIT_TRIGGERED_VERCEL_PREVIEW",
        "EXACT_PACKAGE_BUILD",
        "STABLE_SLOT_INSTALL_AND_HOT_REATTACH",
        "PERSISTENT_OR_TRULY_HIDDEN_EVIDENCE_LANE_HELPERS_AND_TUNNEL",
        "PLUGIN_PV_HIL",
    ],
    "downstream_project_pv_inherits_plugin_release_cycle": False,
    "downstream_project_controls": [
        "OWN_GIT_CI_DEPLOY_WORKFLOW",
        "GOVERNED_SCHEMA_AND_LANE_EVOLUTION",
        "BOUNDED_ADDITIONAL_PLUGINS",
        "STORAGE_CONNECTOR_SELECTION",
    ],
    "intermediate_pv13_install_hil_route": {
        "ci_prerequisite_row": 266,
        "execution_row": 267,
        "release": BASE_RELEASE,
        "branch": "main",
        "source": (
            "EXACT_GIT_MAIN_COMMIT_AFTER_REQUIRED_CLEAN_CI_AND_"
            "GIT_TRIGGERED_VERCEL_PREVIEW"
        ),
        "slot_role": "main-git-release",
        "plugin_selector": PLUGIN_SELECTOR,
        "versioned_local_testing_selector": (
            f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"
        ),
        "working_role_sync_required": True,
        "installed_version_must_equal_exact_package_version": True,
        "installed_catalog_must_equal": {
            "native_actions": EXPECTED_SURFACE_COUNTS["tools"],
            "read_actions": EXPECTED_SURFACE_COUNTS["read"],
            "write_actions": EXPECTED_SURFACE_COUNTS["write"],
            "governed_skills": EXPECTED_SURFACE_COUNTS["skills"],
            "hook_events": EXPECTED_SURFACE_COUNTS["hook_events"],
            "separate_command_layer_present": False,
        },
        "installed_ui_readback_required_before_pv13_hil": True,
        "main_git_release_slot_mutation_allowed": True,
        "main_merge_allowed": True,
        "downstream_project_inherits_install": False,
    },
    "full_vercel_guide_refresh": "ASSIGNED_WEBSITE_DELTA_ONLY",
    "plan_or_pv_projection_update_is_full_site_refresh": False,
    "env_uop_evolution_requires_new_sealed_identity": True,
    "accepted_locked_env_uop_mutation_allowed": False,
}
EXPECTED_GOAL_COMPLETION_POLICY = {
    "schema": "evidence-lane.human-goal-completion-policy.v2",
    "scope": "ALL_GOVERNED_GOALS",
    "exact_visible_command": "MARK GOAL COMPLETE",
    "authorization_actor": "HUMAN_ONLY",
    "dispositions": [
        "COMPLETE_THIS_TASK_AND_STATE_TRAVEL",
        "COMPLETE_FULLY",
    ],
    "hil_candidate_plan_test_or_automation_can_complete": False,
    "pause_or_stall_can_complete": False,
    "pause_or_stall_without_completion_allowed": True,
    "completion_implies_hil_approval_fuse_or_pointer_move": False,
    "completion_implies_git_install_merge_or_deploy": False,
    "completion_display": {
        "authoritative_current_route": ("build_rich_goal_completion_metrics_receipt"),
        "receipt_schema": "evidence-lane.rich-goal-completion-metrics.v1",
        "compatibility_collector_present": False,
        "display_route_has_completion_authority": False,
        "already_complete_reuses_persisted_receipt": True,
        "incomplete_telemetry_returns_structured_missing_fields": True,
        "reasoning_output_is_subset_of_output": True,
        "host_accounted_tokens_kept_separate_from_raw_model_traffic": True,
        "unknown_host_conversion_formula_must_remain_unknown": True,
        "reset_aware_positive_delta_epoch_accounting": True,
        "native_turn_and_compaction_reconciliation": True,
        "daily_timezone_reconciliation": True,
        "first_native_complete_goal_receipt_required": True,
        "aborted_turns_from_native_turn_aborted_only": True,
        "duration_authorities_separate": [
            "USER_CONFIRMED_ACTIVE_UI_RUNTIME",
            "HOST_COMPLETED_TIME_USED",
            "GOAL_CALENDAR_SPAN",
            "NATIVE_COMPLETED_TURN_OVERLAP",
        ],
        "preferred_duration_formula": (
            "COALESCE(USER_CONFIRMED_ACTIVE_UI_RUNTIME,HOST_COMPLETED_TIME_USED)"
        ),
        "active_ui_timer_class": "CLIENT_EXTRAPOLATED_PRESENTATION",
        "duration_authority_alias_or_sum_allowed": False,
        "full_option_2_projection_required": True,
        "correction_supersedes_without_goal_recompletion": True,
        "superseded_and_quarantined_excluded_from_consolidated_formula": True,
    },
}
EXPECTED_HELPER_DISTRIBUTION_POLICY = {
    "schema": "evidence-lane.helper-distribution-policy.v1",
    "runtime_storage_boundary": {
        "root_relative_to_user_profile": (
            ".codex\\plugins\\runtime\\evidence-lane-plugin"
        ),
        "host_managed_hidden": True,
        "project_authority_may_share_root": False,
        "installed_cache_mutation_for_runtime_state_allowed": False,
        "one_active_native_mcp_count": 1,
        "one_active_tunnel_count": 1,
        "failed_windowsapps_cli_probe_allowed": False,
        "npm_native_codex_cli_required": True,
        "exact_task_reopen_count": 0,
        "black_terminal_popup_allowed": False,
    },
    "manual_restart_boundary": {
        "restart_helper_present": False,
        "user_performs_app_restart": True,
        "install_receipt_is_restart_boundary": True,
        "terminal_response_required_before_user_restart": True,
        "programmatic_process_stop_allowed": False,
        "same_local_testing_marketplace_new_version_required": True,
        "version_matched_tunnel_starts_before_host": True,
        "combined_post_stop_installer_retired": True,
        "plugin_creator_local_update_route": {
            "route_law": PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW,
            "plugin_creator_packing_required_every_local_build": True,
            "fresh_cachebuster_before_each_pack_required": True,
            "direct_cache_edit_allowed": False,
            "standalone_fallback_install_route_allowed": False,
            "source_sync": "IN_PLACE_EXISTING_LOCAL_MARKETPLACE",
            "cache_materialization": "CODEX_PLUGIN_ADD",
            "loaded_old_cache_boundary": (PLUGIN_CREATOR_LOCAL_CACHE_RESTART_STATE),
            "restart_authority_mode": "INSTALL_RECEIPT_THEN_USER_MANUAL_RESTART",
            "windows_marketplace_root_rotation_allowed": False,
            "exact_same_task_hidden_restart_required": False,
            "terminal_response_required_before_user_restart": True,
            "manual_exact_channel_restart_required": True,
            "restart_helper_present": False,
            "child_lease_acknowledgement_before_app_stop_required": False,
            "child_launch_shape": "ABSENT",
            "redirected_parent_pipe_handles_allowed": False,
            "terminal_success_or_failure_receipt_required": False,
            "preparation_receipt_required": False,
            "post_restart_native_readback_required": True,
            "windows_ui_control_allowed": False,
            "cross_task_rehydration_allowed": False,
            "local_install_tunnel_activation_required": True,
            "local_install_tunnel_activation_before_user_restart": True,
            "visible_runtime_key_entry_policy": (
                "FIRST_REGISTRATION_OR_MISSING_INVALID_CREDENTIAL_ONLY"
            ),
            "compatible_runtime_key_envelope_reuse_allowed": True,
            "tunnel_rebuild_trigger": "CAPABILITY_FINGERPRINT_CHANGED_ONLY",
            "exact_plugin_rebind_required_every_local_install": True,
            "persistent_tunnel_runtime_hidden": True,
            "remote_tunnel_crud_authorized": False,
        },
    },
    "user_stable_tunnel": {
        "script": "scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1",
        "audience": "GOVERNED_CODEX_USER",
        "public_marketplace_user_surface": True,
        "release": BASE_RELEASE,
        "release_token": "v300",
        "runtime_root_suffix_template": (
            "tunnel-runtime-{release_token}-{slot_role}-abi-"
            "{tunnel_compatibility_digest}"
        ),
        "scheduled_task_name_template": (
            "EvidenceLane-Tunnel-{release_token}-{slot_role}-abi-"
            "{tunnel_compatibility_digest}"
        ),
        "tunnel_compatibility_schema": (
            "evidence-lane.tunnel-capability-compatibility.v1"
        ),
        "tunnel_rebuild_trigger": "CAPABILITY_FINGERPRINT_CHANGED_ONLY",
        "exact_plugin_rebind_required_every_install": True,
        "compatible_runtime_and_key_reuse_allowed": True,
        "at_logon": True,
        "scheduled_task_transport_allowed": True,
        "host_wide_project_neutral": True,
        "multi_project_and_task_routing": (
            "EXPLICIT_PLUGIN_PROJECT_ID_AND_TASK_BINDINGS"
        ),
        "persistent_or_hidden_no_transient_console": True,
        "one_active_version": True,
        "prior_versions_retained": True,
        "prior_versions_disabled": True,
        "prior_versions_deleted": False,
    },
    "post_hil_release_rotation": {
        "schema": "evidence-lane.plugin-slot-tunnel-rotation.v1",
        "applies_to_plugin_maintainer_route_only": True,
        "downstream_project_inherits_rotation": False,
        "local_test_green_can_promote_only_through_exact_main_merge": True,
        "pre_3_0_fallback_allowed": False,
        "final_gate": "PV14_EXACT_HUMAN_APPROVE_AND_FUSE",
        "required_order": [
            "FUSE_EXACT_ACCEPTED_PLUGIN_PV",
            "GOVERNED_NON_FORCE_MAIN_PROMOTION",
            "VERIFY_MAIN_EQUALS_ACCEPTED_COMMIT",
            "INSTALL_ACCEPTED_RELEASE_IN_STABLE_GIT_MAIN_SLOT",
            "PRESERVE_VERSIONED_LOCAL_TESTING_AS_THE_ONLY_SECOND_SLOT",
            "ROTATE_MATCHING_TUNNEL_IDENTITY",
            "VERIFY_EXACTLY_TWO_SLOTS_AND_ONE_ACTIVE_RUNTIME",
        ],
        "stable_git_main_must_equal_exact_merged_release": True,
        "tunnel_release_must_match_owning_slot": True,
        "prior_versioned_tunnels_retained": True,
        "prior_versioned_tunnels_disabled": True,
        "prior_versioned_tunnels_deleted": False,
        "repeat_for_each_later_plugin_release_cycle": True,
        "current_row_may_execute_rotation": False,
    },
    "external_tester_distribution_gate": (
        "SEPARATELY_AUTHORIZED_GITHUB_APP_ROUTE_AFTER_PLUGIN_HIL"
    ),
    "official_marketplace_submission_gate": (
        "SEPARATELY_AUTHORIZED_OPENAI_CHANNEL_AFTER_EXTERNAL_TEST_EVIDENCE"
    ),
    "current_row_authorizes_external_distribution_or_submission": False,
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
    "restart_helper_present": False,
    "install_receipt_is_restart_boundary": True,
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
    "git_marketplace_source": MARKETPLACE_SOURCE,
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
        "authority_binding": ("EXACT_HOST_SESSION_PLUS_NATIVE_EVIDENCE_LANE_MCP_ROUTE"),
        "process_package_title_cwd_authority": False,
    },
}
INSTALL_SCHEMA = "evidence-lane.codex-stable-installation.v2"
HOOK_TRUST_SCHEMA = "evidence-lane.codex-hook-trust.v1"
EXPECTED_CODEX_HOST_HOOK_EVENT_ORDER = (
    "sessionStart",
    "subagentStart",
    "userPromptSubmit",
    "preToolUse",
    "permissionRequest",
    "postToolUse",
    "preCompact",
    "postCompact",
    "subagentStop",
    "stop",
    "sessionEnd",
)
EXPECTED_CODEX_HOST_HOOK_EVENTS = set(EXPECTED_CODEX_HOST_HOOK_EVENT_ORDER)
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


class InstallationError(RuntimeError):
    """Raised before restart when a stable-install invariant is not exact."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _source_inventory(root: Path) -> dict[str, Any]:
    """Return one deterministic inventory of canonical shipped source files.

    Python bytecode is a runtime cache, not a shipped source member.  Its payload
    embeds the absolute cache root, so two otherwise byte-identical selector
    installations necessarily produce different ``.pyc`` bytes.

    The retired command and migrated-command surfaces are never source authority
    and are rejected rather than ignored.
    """

    rows = []
    ignored_python_runtime_artifacts = 0
    ignored_runtime_cache_directories = {
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
    for path in sorted(row for row in root.rglob("*") if row.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("_evidence_lane_rehearsal/"):
            continue
        if any(
            relative == retired or relative.startswith(f"{retired}/")
            for retired in RETIRED_COMMAND_ROOTS
        ):
            raise InstallationError("The package contains the retired command layer.")
        if set(
            relative.split("/")
        ) & ignored_runtime_cache_directories or path.suffix.lower() in {
            ".pyc",
            ".pyo",
        }:
            ignored_python_runtime_artifacts += 1
            continue
        rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "file_count": len(rows),
        "manifest_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
        "files": rows,
        "ignored_python_runtime_artifact_count": ignored_python_runtime_artifacts,
        "python_runtime_artifacts_are_source_authority": False,
        "retired_command_surface_count": 0,
    }


def _require_no_plugin_cache_artifacts(root: Path) -> dict[str, Any]:
    forbidden: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(
            relative == retired or relative.startswith(f"{retired}/")
            for retired in RETIRED_COMMAND_ROOTS
        ):
            forbidden.append(relative)
            continue
        if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts) or (
            path.is_file() and path.suffix.casefold() in {".pyc", ".pyo"}
        ):
            forbidden.append(relative)
    if forbidden:
        raise InstallationError(
            "The generated plugin cache contains forbidden Python/pytest artifacts: "
            + json.dumps(forbidden[:25])
        )
    return {
        "status": "PASS",
        "root": str(root.resolve()),
        "forbidden_cache_artifact_count": 0,
        "python_bytecode_write_disabled": True,
        "pytest_cache_provider_disabled_for_installed_verification": True,
        "retired_command_surface_count": 0,
    }


def _assert_exact_git_marketplace_source(
    *,
    extracted_inventory: dict[str, Any],
    marketplace_root: Path,
    expected_git_manifest_sha256: str,
    expected_git_file_count: int,
    source_version: str | None = None,
    package_version: str | None = None,
) -> dict[str, Any]:
    plugin_root = marketplace_root / "plugins" / PLUGIN_NAME
    if not plugin_root.is_dir():
        raise InstallationError(
            "The Git marketplace lacks the Evidence Lane plugin root."
        )
    marketplace_inventory = _source_inventory(plugin_root)
    normalized_expected_manifest = expected_git_manifest_sha256.strip().upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", normalized_expected_manifest) is None
        or not isinstance(expected_git_file_count, int)
        or expected_git_file_count < 1
        or marketplace_inventory["file_count"] != expected_git_file_count
        or marketplace_inventory["manifest_sha256"] != normalized_expected_manifest
    ):
        raise InstallationError(
            "The Git marketplace bytes do not match the complete exact Git commit tree."
        )
    marketplace_by_path = {
        str(row["path"]): row for row in marketplace_inventory["files"]
    }
    package_files = extracted_inventory.get("files")
    package_by_path = {
        str(row.get("path") or ""): row
        for row in package_files or []
        if isinstance(row, dict)
    }
    mismatched_paths = sorted(
        path
        for path, row in package_by_path.items()
        if marketplace_by_path.get(path) != row
    )
    manifest_override: dict[str, str] | None = None
    if mismatched_paths == [".codex-plugin/plugin.json"]:
        git_manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        git_version = str(git_manifest.get("version") or "")
        exact_source_version = str(source_version or "")
        exact_package_version = str(package_version or "")
        if (
            git_version != exact_source_version
            or not exact_source_version.startswith("3.0.0+codex.")
            or not exact_package_version.startswith("3.0.0+codex.")
            or exact_source_version == exact_package_version
        ):
            raise InstallationError(
                "The package-local manifest changed more than its fresh cachebuster version."
            )
        manifest_override = {
            "path": ".codex-plugin/plugin.json",
            "git_version": exact_source_version,
            "package_version": exact_package_version,
            "release_line": "3.0.0",
        }
    if (
        not isinstance(package_files, list)
        or len(package_by_path) != len(package_files)
        or any(path not in marketplace_by_path for path in package_by_path)
        or mismatched_paths not in ([], [".codex-plugin/plugin.json"])
        or len(package_files) != extracted_inventory.get("file_count")
    ):
        raise InstallationError(
            "The install-package subset does not match the exact Git marketplace."
        )
    return {
        "status": "PASS",
        "source_type": "git",
        "repository": MARKETPLACE_SOURCE,
        "marketplace_name": MARKETPLACE_NAME,
        "file_count": marketplace_inventory["file_count"],
        "manifest_sha256": marketplace_inventory["manifest_sha256"],
        "package_subset_file_count": extracted_inventory["file_count"],
        "git_only_file_count": (
            marketplace_inventory["file_count"] - extracted_inventory["file_count"]
        ),
        "exact_git_commit_tree_match": True,
        "exact_commit_package_bytes_match": True,
        "package_local_manifest_version_override": manifest_override,
    }


def _surface_inventory(
    plugin_root: Path,
    *,
    version: str,
    allow_pre_logical_action_upgrade_baseline: bool = False,
) -> dict[str, Any]:
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
    release_channel_path = plugin_root / "scripts" / "codex-release-channel.json"
    try:
        release_channel = json.loads(release_channel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The Codex release-channel contract is missing."
        ) from exc
    surface_catalog = dict(EXPECTED_CATALOG)
    if allow_pre_logical_action_upgrade_baseline:
        prior_stable = dict(release_channel.get("stable") or {})
        prior_catalog = {
            "tools": prior_stable.get("native_tool_count"),
            "read": prior_stable.get("native_read_tool_count"),
            "write": prior_stable.get("native_write_tool_count"),
            "skills": prior_stable.get("skill_count"),
        }
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in prior_catalog.values()
        ):
            raise InstallationError(
                "The prior package does not declare one exact public catalog."
            )
        surface_catalog = prior_catalog
    if not all(path.is_file() for path in hook_paths):
        raise InstallationError("The persistent hook inventory is incomplete.")
    hook_names = {path.name for path in hook_paths}
    if frozenset(hook_names) not in {
        frozenset(
            {
                "hooks.json",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "hooks.json",
                "post_tool_use.py",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "hooks.json",
                "invoke_hook.ps1",
                "invoke_hook.py",
                "lifecycle_boundary.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "prompt_submit.py",
                "session_start.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "behavior_handoff.py",
                "event_isolation.py",
                "hooks.json",
                "invoke_hook.ps1",
                "invoke_hook.py",
                "lifecycle_boundary.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "prompt_submit.py",
                "session_start.py",
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
                "prompt_submit.py",
                "session_start.py",
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
        raise InstallationError("The persistent hook inventory is not exact.")
    if len(skill_paths) != surface_catalog["skills"]:
        raise InstallationError("The governed skill inventory is not exact.")

    def inventory(paths: list[Path], *, skill: bool) -> dict[str, Any]:
        rows = [
            {
                "name": path.parent.name if skill else path.name,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        if len(rows) != len({row["name"] for row in rows}):
            raise InstallationError("A hook or skill inventory name is duplicated.")
        return {
            "count": len(rows),
            "records": rows,
            "inventory_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
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
            raise InstallationError("The host and internal hook registries overlap.")
    else:
        declared_logical_actions = hook_configuration.get("logicalActions")
    logical_action_registry_state = "CURRENT_NUMBERED_LOGICAL_ACTION_REGISTRY"
    if declared_logical_actions is None and allow_pre_logical_action_upgrade_baseline:
        declared_logical_actions = {
            name: ["LEGACY_PRE_LOGICAL_ACTION_REGISTRY"] for name in event_order
        }
        logical_action_registry_state = "LEGACY_ABSENT_UPGRADE_BASELINE"
    if (
        not isinstance(declared_logical_actions, dict)
        or not set(event_order).issubset(declared_logical_actions)
        or any(
            not isinstance(declared_logical_actions.get(name), list)
            or not declared_logical_actions[name]
            for name in event_order
        )
    ):
        raise InstallationError("The hook logical-action registry is invalid.")
    event_action_inventory: list[dict[str, Any]] = []
    for event_ordinal, event_name in enumerate(event_order, start=1):
        actions: list[dict[str, Any]] = []
        groups = hook_events[event_name]
        if not isinstance(groups, list) or not groups:
            raise InstallationError("A hook event has no handler group.")
        for group_ordinal, group in enumerate(groups, start=1):
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list) or not handlers:
                raise InstallationError("A hook event group has no handler action.")
            for group_action_ordinal, handler in enumerate(handlers, start=1):
                if not isinstance(handler, dict):
                    raise InstallationError("A hook handler action is invalid.")
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
                        "command_sha256": hashlib.sha256(
                            command_identity.encode("utf-8")
                        )
                        .hexdigest()
                        .upper(),
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
    handler_count = sum(int(row["action_count"]) for row in event_action_inventory)
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
        "event_action_inventory_sha256": hashlib.sha256(
            _json_bytes(event_action_inventory)
        )
        .hexdigest()
        .upper(),
        "logical_action_count": sum(
            int(row["logical_action_count"]) for row in logical_action_inventory
        ),
        "logical_action_count_semantics": (
            "NUMBERED_SERIAL_TRANSPORT_STEPS_INSIDE_HANDLER_ACTIONS"
        ),
        "logical_action_registry_state": logical_action_registry_state,
        "logical_action_inventory": logical_action_inventory,
        "logical_action_inventory_sha256": hashlib.sha256(
            _json_bytes(logical_action_inventory)
        )
        .hexdigest()
        .upper(),
        "hook_file_count": hook_files["count"],
        "records": hook_files["records"],
        "file_inventory_sha256": hook_files["inventory_sha256"],
        "event_inventory_sha256": hashlib.sha256(_json_bytes(registered_events))
        .hexdigest()
        .upper(),
    }
    hook_inventory["inventory_sha256"] = (
        hashlib.sha256(_json_bytes(hook_inventory)).hexdigest().upper()
    )
    search_contract = dict(
        (release_channel.get("dependency_toolchains") or {}).get("search_v1") or {}
    )
    search_required = search_contract.get("required") is True
    if search_required:
        if (
            search_contract.get("scope") != "ALL_GOVERNED_PROJECTS"
            or search_contract.get("manifest") != "toolchains/search-tools.v1.json"
            or search_contract.get("fallbacks_required") is not True
            or search_contract.get("path_lookup_allowed") is not False
            or search_contract.get("auto_download_during_mcp_handshake") is not False
        ):
            raise InstallationError("The governed search dependency contract drifted.")
        search_toolchain = _search_toolchain_inventory(plugin_root)
    else:
        manifest_path = plugin_root / "toolchains" / "search-tools.v1.json"
        search_toolchain = (
            _search_toolchain_inventory(plugin_root)
            if manifest_path.is_file()
            else _historical_search_toolchain_absence()
        )
    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": version,
        "hooks": hook_inventory,
        "skills": inventory(skill_paths, skill=True),
        "search_toolchain": search_toolchain,
        "catalog": surface_catalog,
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = (
        hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    )
    return core


def _historical_search_toolchain_absence() -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": "evidence-lane.codex-packaged-search-toolchain.v1",
        "status": "NOT_DECLARED_HISTORICAL_PACKAGE",
        "manifest_sha256": None,
        "resolution_order": [],
        "records": [],
        "record_count": 0,
        "path_lookup_allowed": False,
        "auto_download_during_mcp_handshake": False,
        "fallbacks_required": False,
    }
    body["inventory_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    return body


def _search_toolchain_inventory(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / "toolchains" / "search-tools.v1.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The governed search toolchain manifest is missing."
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
        or [row.get("tool_id") for row in tools if isinstance(row, dict)] != ["ripgrep"]
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
        raise InstallationError("The governed search toolchain contract drifted.")
    records: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise InstallationError("The search toolchain record is invalid.")
        package_binaries = tool.get("package_binaries")
        if not isinstance(package_binaries, dict):
            raise InstallationError("The search tool package inventory is invalid.")
        for platform_id, binary_record in sorted(package_binaries.items()):
            if not isinstance(binary_record, dict):
                raise InstallationError("The search tool binary record is invalid.")
            binary = (plugin_root / str(binary_record.get("path") or "")).resolve()
            try:
                binary.relative_to(plugin_root.resolve())
            except ValueError as exc:
                raise InstallationError(
                    "A search tool escaped the plugin package."
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
                raise InstallationError("A packaged search tool identity drifted.")
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
    body["inventory_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
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
                raise InstallationError(
                    "Every native tool needs a literal name and annotation."
                )
            rows.append({"name": name_node.value, "annotation": annotation_node.id})
    specialized_actions: Any = None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "SPECIALIZED_NATIVE_ACTIONS"
            for target in targets
        ):
            continue
        value_node = node.value
        if value_node is None:
            raise InstallationError(
                "SPECIALIZED_NATIVE_ACTIONS must have one literal value."
            )
        try:
            specialized_actions = ast.literal_eval(value_node)
        except (TypeError, ValueError, SyntaxError) as exc:
            raise InstallationError(
                "SPECIALIZED_NATIVE_ACTIONS must be one literal immutable subset catalog."
            ) from exc
        break
    if specialized_actions is not None and not isinstance(specialized_actions, tuple):
        raise InstallationError("The specialized native action subset is invalid.")
    for action in specialized_actions or ():
        if not (
            isinstance(action, tuple)
            and len(action) == 6
            and all(isinstance(value, str) for value in action[:5])
            and isinstance(action[5], bool)
        ):
            raise InstallationError(
                "A specialized native action declaration is invalid."
            )
        rows.append(
            {
                "name": action[0],
                "annotation": "_READ_ONLY" if action[5] else "_LOCAL_WRITE",
            }
        )
    rows.sort(key=lambda row: row["name"])
    read = sum(row["annotation"] == "_READ_ONLY" for row in rows)
    write = len(rows) - read
    if (
        len(rows) != EXPECTED_CATALOG["tools"]
        or len({row["name"] for row in rows}) != len(rows)
        or read != EXPECTED_CATALOG["read"]
        or write != EXPECTED_CATALOG["write"]
    ):
        raise InstallationError(
            "The native "
            f"{EXPECTED_CATALOG['tools']}/{EXPECTED_CATALOG['read']}/"
            f"{EXPECTED_CATALOG['write']} tool catalog drifted."
        )
    return {
        "tools": len(rows),
        "read": read,
        "write": write,
        "skills": EXPECTED_CATALOG["skills"],
        "tool_names_unique": True,
        "static_catalog_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
    }


def _surface_change_display(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    def changes(kind: str) -> dict[str, Any]:
        current_rows = {row["name"]: row["sha256"] for row in current[kind]["records"]}
        previous_rows = (
            {row["name"]: row["sha256"] for row in previous[kind]["records"]}
            if previous is not None
            else {}
        )
        added_files = sorted(current_rows.keys() - previous_rows.keys())
        changed_files = sorted(
            name
            for name in current_rows.keys() & previous_rows.keys()
            if current_rows[name] != previous_rows[name]
        )
        removed_files = sorted(previous_rows.keys() - current_rows.keys())
        result = {
            "count": current[kind]["count"],
            "count_semantics": current[kind].get(
                "count_semantics", "SURFACE_RECORD_COUNT"
            ),
            "added": sorted(current_rows.keys() - previous_rows.keys()),
            "changed": changed_files,
            "removed": removed_files,
            "added_files": added_files,
            "changed_files": changed_files,
            "removed_files": removed_files,
            "inventory_sha256": current[kind]["inventory_sha256"],
        }
        if kind == "hooks":
            current_events = set(current[kind]["registered_events"])
            previous_events = (
                set(previous[kind]["registered_events"])
                if previous is not None
                else set()
            )
            result.update(
                {
                    "registered_event_count": current[kind]["registered_event_count"],
                    "registered_events": current[kind]["registered_events"],
                    "handler_count": current[kind]["handler_count"],
                    "handler_count_semantics": current[kind].get(
                        "handler_count_semantics",
                        "TOTAL_NESTED_HANDLER_ACTION_COUNT",
                    ),
                    "event_order": current[kind].get(
                        "event_order", current[kind]["registered_events"]
                    ),
                    "event_action_inventory": current[kind].get(
                        "event_action_inventory", []
                    ),
                    "event_action_inventory_sha256": current[kind].get(
                        "event_action_inventory_sha256",
                        hashlib.sha256(_json_bytes([])).hexdigest().upper(),
                    ),
                    "logical_action_count": current[kind].get(
                        "logical_action_count", 0
                    ),
                    "logical_action_count_semantics": current[kind].get(
                        "logical_action_count_semantics",
                        "UNAVAILABLE_LEGACY_SURFACE",
                    ),
                    "logical_action_inventory": current[kind].get(
                        "logical_action_inventory", []
                    ),
                    "logical_action_inventory_sha256": current[kind].get(
                        "logical_action_inventory_sha256",
                        hashlib.sha256(_json_bytes([])).hexdigest().upper(),
                    ),
                    "hook_file_count": current[kind]["hook_file_count"],
                    "added_events": sorted(current_events - previous_events),
                    "removed_events": sorted(previous_events - current_events),
                    "file_inventory_sha256": current[kind]["file_inventory_sha256"],
                    "event_inventory_sha256": current[kind]["event_inventory_sha256"],
                }
            )
        return result

    current_search = dict(current["search_toolchain"])
    previous_search = (
        dict(previous.get("search_toolchain") or {}) if previous is not None else {}
    )
    search_display = {
        "status": current_search["status"],
        "record_count": current_search["record_count"],
        "manifest_sha256": current_search["manifest_sha256"],
        "inventory_sha256": current_search["inventory_sha256"],
        "fts_authority": current_search.get("fts_authority"),
        "resolution_order": current_search["resolution_order"],
        "records": current_search["records"],
        "fallbacks_required": current_search["fallbacks_required"],
        "changed_from_previous": (
            previous is not None
            and previous_search.get("inventory_sha256")
            != current_search["inventory_sha256"]
        ),
        "raw_paths_included": False,
    }
    core = {
        "schema": "evidence-lane.codex-installed-surface-change-display.v2",
        "state": "INITIAL_V2_BASELINE" if previous is None else "VERSIONED_UPDATE",
        "previous_plugin_version": (
            previous.get("plugin_version") if previous is not None else None
        ),
        "current_plugin_version": current["plugin_version"],
        "hooks": changes("hooks"),
        "skills": changes("skills"),
        "search_toolchain": search_display,
        "catalog": {
            **current["catalog"],
            "changed_from_previous": (
                previous is not None and previous.get("catalog") != current["catalog"]
            ),
        },
        "previous_surface_inventory_sha256": (
            previous.get("surface_inventory_sha256") if previous is not None else None
        ),
        "current_surface_inventory_sha256": current["surface_inventory_sha256"],
        "raw_paths_included": False,
        "private_research_question_included": False,
    }
    core["change_display_sha256"] = (
        hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    )
    return core


def _verified_staged_surface_change_display(
    value: Any,
    *,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Verify the preflight diff before reusing it during activation.

    Installation is deliberately a two-pass operation.  Once the first pass has
    staged the new marketplace, recomputing a diff during the activation pass
    compares the package with itself and erases the actual version change.  The
    stage receipt therefore carries the original content-addressed display and
    the second pass must verify and reuse it byte-for-byte.
    """

    if not isinstance(value, dict):
        raise InstallationError(
            "The exact staged marketplace lacks its sealed surface change display."
        )
    core = dict(value)
    observed_sha256 = str(core.pop("change_display_sha256", "")).upper()
    expected_sha256 = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    hooks = dict(value.get("hooks") or {})
    skills = dict(value.get("skills") or {})
    catalog = dict(value.get("catalog") or {})
    search_toolchain = dict(value.get("search_toolchain") or {})
    if (
        value.get("schema") != "evidence-lane.codex-installed-surface-change-display.v2"
        or observed_sha256 != expected_sha256
        or value.get("current_plugin_version") != current["plugin_version"]
        or value.get("current_surface_inventory_sha256")
        != current["surface_inventory_sha256"]
        or value.get("raw_paths_included") is not False
        or value.get("private_research_question_included") is not False
        or hooks.get("count") != current["hooks"]["count"]
        or hooks.get("registered_event_count")
        != current["hooks"]["registered_event_count"]
        or hooks.get("registered_events") != current["hooks"]["registered_events"]
        or hooks.get("handler_count") != current["hooks"]["handler_count"]
        or hooks.get("handler_count_semantics")
        != current["hooks"]["handler_count_semantics"]
        or hooks.get("event_order") != current["hooks"]["event_order"]
        or hooks.get("event_action_inventory")
        != current["hooks"]["event_action_inventory"]
        or hooks.get("event_action_inventory_sha256")
        != current["hooks"]["event_action_inventory_sha256"]
        or hooks.get("logical_action_count") != current["hooks"]["logical_action_count"]
        or hooks.get("logical_action_count_semantics")
        != current["hooks"]["logical_action_count_semantics"]
        or hooks.get("logical_action_inventory")
        != current["hooks"]["logical_action_inventory"]
        or hooks.get("logical_action_inventory_sha256")
        != current["hooks"]["logical_action_inventory_sha256"]
        or hooks.get("hook_file_count") != current["hooks"]["hook_file_count"]
        or hooks.get("inventory_sha256") != current["hooks"]["inventory_sha256"]
        or skills.get("count") != current["skills"]["count"]
        or skills.get("inventory_sha256") != current["skills"]["inventory_sha256"]
        or search_toolchain.get("status") != current["search_toolchain"]["status"]
        or search_toolchain.get("record_count")
        != current["search_toolchain"]["record_count"]
        or search_toolchain.get("manifest_sha256")
        != current["search_toolchain"]["manifest_sha256"]
        or search_toolchain.get("inventory_sha256")
        != current["search_toolchain"]["inventory_sha256"]
        or search_toolchain.get("fts_authority")
        != current["search_toolchain"].get("fts_authority")
        or search_toolchain.get("records") != current["search_toolchain"]["records"]
        or search_toolchain.get("raw_paths_included") is not False
        or any(catalog.get(key) != current["catalog"][key] for key in EXPECTED_CATALOG)
    ):
        raise InstallationError("The exact staged surface change display drifted.")
    return value


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _load_receipt(
    receipt_path: Path,
    archive: Path,
    *,
    activation: bool,
    local_test_activation: bool = False,
) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    sealed = receipt.get("archive") or {}
    schema = receipt.get("schema")
    boundary = receipt.get("boundary")
    exact_commit_package = (
        schema == "evidence-lane.codex-exact-commit-package.v1.receipt"
        and boundary == "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
    )
    exact_export = dict(receipt.get("exact_commit_export") or {})
    plugin_source_manifest_sha256 = str(
        exact_export.get("plugin_source_manifest_sha256") or ""
    ).upper()
    plugin_source_member_count = exact_export.get("plugin_source_member_count")
    exact_export_valid = (not exact_commit_package) or (
        re.fullmatch(r"[A-F0-9]{64}", plugin_source_manifest_sha256) is not None
        and isinstance(plugin_source_member_count, int)
        and plugin_source_member_count >= int(receipt.get("source_member_count") or 0)
        and exact_export.get("git_archive_member_count") == plugin_source_member_count
        and exact_export.get("plugin_path") == "plugins/evidence-lane-plugin"
        and exact_export.get("projection_clean") is True
        and exact_export.get("working_checkout_bytes_used") is False
        and exact_export.get("untracked_bytes_used") is False
    )
    local_rehearsal = (
        schema == "evidence-lane.non-lifecycle-local-package-rehearsal.v1.receipt"
        and boundary == "NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL"
    )
    git_boundary_valid = (
        exact_commit_package
        and receipt.get("git_invoked") is True
        and receipt.get("git_write_invoked") is False
    ) or (
        local_rehearsal
        and receipt.get("git_invoked") is False
        and receipt.get("git_write_invoked") in (None, False)
    )
    self_seal_valid = True
    if exact_commit_package:
        receipt_sha256 = str(receipt.get("receipt_sha256") or "").upper()
        core = dict(receipt)
        core.pop("receipt_sha256", None)
        self_seal_valid = (
            re.fullmatch(r"[A-F0-9]{64}", receipt_sha256) is not None
            and hashlib.sha256(_json_bytes(core)).hexdigest().upper() == receipt_sha256
        )
    if (
        not (exact_commit_package or local_rehearsal)
        or (activation and not exact_commit_package)
        or (local_test_activation and not local_rehearsal)
        or not self_seal_valid
        or not exact_export_valid
        or receipt.get("status") != "PASS"
        or sealed.get("filename") != archive.name
        or sealed.get("sha256") != _sha256(archive)
        or receipt.get("governed_candidate_created") is not False
        or not git_boundary_valid
        or receipt.get("accepted_pointer_moved") is not False
    ):
        raise InstallationError(
            "The package receipt does not seal this archive or is not eligible "
            "for the requested staging/activation boundary."
        )
    return receipt


def _require_local_systemwide_route_audit(receipt: dict[str, Any]) -> dict[str, Any]:
    audit = receipt.get("systemwide_route_audit")
    if (
        not isinstance(audit, dict)
        or audit.get("schema") != "evidence-lane.systemwide-route-audit.v1"
        or audit.get("status") != "PASS"
        or audit.get("public_tool_count") != EXPECTED_CATALOG["tools"]
        or audit.get("consumer_parity_status") != "PASS"
        or audit.get("obsolete_route_purge_status") != "PASS"
        or audit.get("systemwide_regression_status") != "PASS"
        or audit.get("skill_current_route_audit_status") != "PASS"
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(audit.get("skill_current_route_audit_sha256") or ""),
        )
        is None
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(audit.get("systemwide_regression_file_sha256") or ""),
        )
        is None
        or audit.get("accepted_archive_queried") is not False
        or audit.get("candidate_created_or_cleared") is not False
        or audit.get("pointer_moved") is not False
        or re.fullmatch(r"[A-F0-9]{64}", str(audit.get("receipt_sha256") or "")) is None
        or re.fullmatch(r"[A-F0-9]{64}", str(audit.get("file_sha256") or "")) is None
    ):
        raise InstallationError(
            "The configured local slot requires the passing system-wide "
            "Plan-history/current-route audit sealed by the package receipt."
        )
    return dict(audit)


def _require_local_executable_fingerprint_refresh(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    audit = receipt.get("executable_fingerprint_refresh")
    scoped_publication_deferral = (
        isinstance(audit, dict)
        and audit.get("full_regression_status")
        == "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED"
    )
    if (
        not isinstance(audit, dict)
        or audit.get("schema") != "evidence-lane.executable-fingerprint-refresh.v1"
        or audit.get("status") != "PASS"
        or audit.get("full_regression_status") not in LOCAL_REGRESSION_PASS_STATUSES
        or (
            scoped_publication_deferral
            and (
                audit.get("publication_scope_status") != "DEFERRED_NOT_PASSED"
                or audit.get("publication_authorized") is not False
                or int(audit.get("deferred_publication_selector_count", 0)) <= 0
                or re.fullmatch(
                    r"[A-F0-9]{64}",
                    str(audit.get("deferral_contract_file_sha256") or ""),
                )
                is None
            )
        )
        or audit.get("authorized_run_count") != 1
        or audit.get("targeted_closure_status") != "PASS"
        or int(audit.get("executable_member_count", 0)) <= 0
        or audit.get("accepted_archive_queried") is not False
        or audit.get("candidate_created_or_cleared") is not False
        or audit.get("pointer_moved") is not False
        or audit.get("project_or_pv_mutated") is not False
        or any(
            re.fullmatch(r"[A-F0-9]{64}", str(audit.get(key) or "")) is None
            for key in (
                "receipt_sha256",
                "file_sha256",
                "executable_registry_receipt_sha256",
                "repository_fingerprint_receipt_sha256",
                "source_impact_receipt_sha256",
            )
        )
    ):
        raise InstallationError(
            "The configured local slot requires a passing executable fingerprint Refresh."
        )
    return dict(audit)


def _load_release_authority(
    *,
    authority_path: Path,
    authority_file_sha256: str,
    archive: Path,
    package_receipt_path: Path,
    package_receipt: dict[str, Any],
) -> dict[str, Any]:
    expected_file_sha256 = authority_file_sha256.strip().upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", expected_file_sha256) is None
        or _sha256(authority_path) != expected_file_sha256
    ):
        raise InstallationError(
            "The governed Git/CI/Vercel release-authority file seal drifted."
        )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    receipt_sha256 = str(authority.get("receipt_sha256") or "").upper()
    authority_body = dict(authority)
    authority_body.pop("receipt_sha256", None)
    calculated_receipt_sha256 = (
        hashlib.sha256(_json_bytes(authority_body)).hexdigest().upper()
    )
    source = dict(authority.get("source") or {})
    remote = dict(authority.get("remote_git") or {})
    ci = dict(authority.get("github_ci") or {})
    preview = dict(authority.get("vercel_preview") or {})
    source_commit = str(source.get("commit") or "").lower()
    source_tree = str(source.get("tree") or "").lower()
    branch = str(source.get("branch") or "")
    proof_branch = str(remote.get("source_branch") or "")
    base_anchor = dict(package_receipt.get("base_anchor") or {})
    package_export = dict(package_receipt.get("exact_commit_export") or {})
    required_check_count = int(ci.get("required_check_count") or 0)
    successful_check_count = int(ci.get("successful_check_count") or 0)
    if (
        authority.get("schema")
        != "evidence-lane.codex-git-ci-vercel-release-authority.v2"
        or package_receipt.get("schema")
        != "evidence-lane.codex-exact-commit-package.v1.receipt"
        or package_receipt.get("boundary") != "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
        or authority.get("status") != "PASS"
        or authority.get("boundary")
        != "GOVERNED_GIT_MAIN_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT"
        or receipt_sha256 != calculated_receipt_sha256
        or authority.get("archive_sha256") != _sha256(archive)
        or authority.get("package_receipt_sha256") != _sha256(package_receipt_path)
        or authority.get("working_source_manifest_sha256")
        != package_receipt.get("working_source_manifest_sha256")
        or authority.get("plugin_source_manifest_sha256")
        != package_export.get("plugin_source_manifest_sha256")
        or authority.get("plugin_source_member_count")
        != package_export.get("plugin_source_member_count")
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        or re.fullmatch(r"[0-9a-f]{40}", source_tree) is None
        or branch != "main"
        or package_export.get("stable_main_only") is not True
        or package_export.get("origin_main_attested") is not True
        or source.get("exact_commit_export") is not True
        or source.get("exact_commit_projection_clean") is not True
        or source.get("working_checkout_clean_required") is not False
        or source.get("untracked_bytes_excluded") is not True
        or str(base_anchor.get("commit") or "").lower() != source_commit
        or str(base_anchor.get("tree") or "").lower() != source_tree
        or remote.get("route") != "github_app_main_fast_forward_v3"
        or remote.get("promotion_status") != "FAST_FORWARDED"
        or remote.get("target_branch") != "main"
        or not proof_branch.startswith("agent/")
        or str(remote.get("remote_branch_commit") or "").lower() != source_commit
        or remote.get("protected_branch") is not True
        or remote.get("force_push") is not False
        or remote.get("source_tree_reused") is not True
        or remote.get("blob_reupload_count") != 0
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(remote.get("native_receipt_sha256") or "").upper(),
        )
        is None
        or ci.get("status") != "PASS"
        or ci.get("branch") != proof_branch
        or str(ci.get("head_sha") or "").lower() != source_commit
        or ci.get("required_checks_complete") is not True
        or required_check_count < 1
        or successful_check_count != required_check_count
        or int(ci.get("failed_check_count") or 0) != 0
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(ci.get("receipt_sha256") or "").upper(),
        )
        is None
        or str(ci.get("repository") or "") != MARKETPLACE_SOURCE
        or preview.get("status") != "PASS"
        or preview.get("state") != "READY"
        or preview.get("target") != "PREVIEW"
        or preview.get("repository") != MARKETPLACE_SOURCE
        or preview.get("branch") != proof_branch
        or str(preview.get("head_sha") or "").lower() != source_commit
        or preview.get("git_integration") is not True
        or preview.get("manual_deploy") is not False
        or preview.get("production_deployment") is not False
        or not str(preview.get("deployment_id") or "").startswith("dpl_")
        or not str(preview.get("url") or "").endswith(".vercel.app")
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(preview.get("receipt_sha256") or "").upper(),
        )
        is None
        or authority.get("governed_candidate_created") is not False
        or authority.get("accepted_pointer_moved") is not False
        or authority.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "Activation requires one exact clean main commit, governed GitHub-App "
            "non-force fast-forward, "
            "successful GitHub CI, and one READY non-production Vercel Git preview "
            "bound to this archive."
        )
    return authority


def _enrich_legacy_hook_surface(
    *,
    receipt: dict[str, Any],
    legacy_surface: dict[str, Any],
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recover event semantics from exact archived marketplace bytes.

    Early v2 installation receipts counted hook files and did not yet include
    registered event names.  The archived marketplace is acceptable only when
    its stage archive SHA matches the installation receipt and every hook and
    skill file hash reproduces the legacy receipt inventory.
    """

    archive_sha256 = str(receipt.get("archive_sha256") or "").upper()
    archive_root = data_root / "installations" / "codex-v300" / "marketplace-archives"
    matches: list[tuple[dict[str, Any], Path]] = []
    for stage_path in sorted(archive_root.glob("*/EVIDENCE_LANE_STAGE.json")):
        stage = json.loads(stage_path.read_text(encoding="utf-8"))
        if (
            stage.get("schema") != "evidence-lane.codex-marketplace-stage.v2"
            or str(stage.get("archive_sha256") or "").upper() != archive_sha256
        ):
            continue
        plugin_root = stage_path.parent / "plugins" / PLUGIN_NAME
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        if not manifest_path.is_file():
            raise InstallationError(
                "The archived legacy marketplace plugin manifest is missing."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str((receipt.get("plugin") or {}).get("version") or "")
        if manifest.get("version") != version:
            raise InstallationError(
                "The archived legacy marketplace version does not match its receipt."
            )
        enriched = _surface_inventory(
            plugin_root,
            version=version,
            allow_pre_logical_action_upgrade_baseline=True,
        )
        expected_hook_files = {
            "count": enriched["hooks"]["hook_file_count"],
            "records": enriched["hooks"]["records"],
            "inventory_sha256": enriched["hooks"]["file_inventory_sha256"],
        }
        if (
            dict(legacy_surface.get("hooks") or {}) != expected_hook_files
            or dict(legacy_surface.get("skills") or {}) != enriched["skills"]
            or dict(legacy_surface.get("catalog") or {}) != enriched["catalog"]
        ):
            raise InstallationError(
                "The archived legacy marketplace bytes do not reproduce the sealed receipt."
            )
        matches.append((enriched, stage_path))
    if len(matches) != 1:
        raise InstallationError(
            "Exactly one archived legacy marketplace must match the baseline archive."
        )
    enriched, stage_path = matches[0]
    return enriched, {
        "surface_enrichment": "VERIFIED_ARCHIVED_MARKETPLACE_EVENT_INVENTORY",
        "archived_stage_receipt": str(stage_path.resolve()),
        "archived_stage_receipt_sha256": _sha256(stage_path),
        "receipt_surface_inventory_sha256": legacy_surface["surface_inventory_sha256"],
    }


def _load_comparison_baseline(
    *,
    path: Path,
    expected_sha256: str,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one explicitly sealed prior host-stable installation surface."""

    expected = str(expected_sha256 or "").strip().upper()
    authority_root = data_root / "installations" / "codex-v300"
    resolved = path.resolve()
    if (
        re.fullmatch(r"[A-F0-9]{64}", expected) is None
        or not resolved.is_file()
        or not _inside(resolved, authority_root)
        or not resolved.name.startswith("INSTALL_")
        or _sha256(resolved) != expected
    ):
        raise InstallationError(
            "The comparison baseline installation receipt is unavailable or unsealed."
        )
    receipt = json.loads(resolved.read_text(encoding="utf-8"))
    receipt_core = dict(receipt)
    receipt_sha256 = str(receipt_core.pop("receipt_sha256", "")).upper()
    surface = dict((receipt.get("plugin") or {}).get("surface_inventory") or {})
    surface_core = dict(surface)
    surface_sha256 = str(surface_core.pop("surface_inventory_sha256", "")).upper()
    plugin = dict(receipt.get("plugin") or {})
    activation = dict(receipt.get("activation") or {})
    if (
        receipt.get("schema") != INSTALL_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt_sha256
        != hashlib.sha256(_json_bytes(receipt_core)).hexdigest().upper()
        or receipt.get("candidate_created_or_accepted") is not False
        or receipt.get("pointer_moved") is not False
        or receipt.get("hil_inferred") is not False
        or activation.get("state")
        not in {
            "INSTALLED_RESTART_REQUIRED",
            "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED",
        }
        or plugin.get("plugin_id") != PLUGIN_NAME
        or plugin.get("version") != surface.get("plugin_version")
        or surface.get("schema") != "evidence-lane.codex-installed-surface-inventory.v2"
        or surface.get("raw_paths_included") is not False
        or surface_sha256
        != hashlib.sha256(_json_bytes(surface_core)).hexdigest().upper()
        or dict(surface.get("catalog") or {}) != EXPECTED_CATALOG
    ):
        raise InstallationError("The comparison baseline installation receipt drifted.")
    enrichment: dict[str, Any] = {
        "surface_enrichment": "NOT_REQUIRED_EVENT_INVENTORY_ALREADY_SEALED",
        "receipt_surface_inventory_sha256": surface["surface_inventory_sha256"],
    }
    if "registered_events" not in dict(surface.get("hooks") or {}):
        surface, enrichment = _enrich_legacy_hook_surface(
            receipt=receipt,
            legacy_surface=surface,
            data_root=data_root,
        )
    identity = {
        "installation_receipt": str(resolved),
        "installation_receipt_sha256": expected,
        "plugin_version": plugin["version"],
        "surface_inventory_sha256": surface["surface_inventory_sha256"],
        "baseline_role": (
            "EXACT_PRIOR_HOST_ACTIVE_LOCAL_TESTING_INSTALLATION"
            if activation.get("state")
            == "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED"
            else "EXACT_PRIOR_HOST_STABLE_INSTALLATION"
        ),
        **enrichment,
    }
    return surface, identity


def _safe_extract(archive_path: Path, target: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            raise InstallationError("Archive members are not sorted and unique.")
        for info in archive.infolist():
            relative = Path(info.filename)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in info.filename
                or (relative.parts and relative.parts[0] == "_evidence_lane_rehearsal")
            ):
                raise InstallationError(f"Unsafe archive member: {info.filename}")
            destination = (target / relative).resolve()
            if not _inside(destination, target):
                raise InstallationError(
                    f"Archive member escaped extraction: {info.filename}"
                )
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _validate_package_proof_manifests(plugin_root: Path) -> dict[str, Any]:
    root = plugin_root / "manifests" / "package"
    expected = {
        "source-manifest.json": (
            "evidence-lane.non-lifecycle-local-package-rehearsal.v1.source-manifest"
        ),
        "skill-inventory.json": (
            "evidence-lane.non-lifecycle-local-package-rehearsal.v1.skill-inventory"
        ),
        "package-surface-coherence.json": (
            "evidence-lane.package-surface-coherence.v1"
        ),
        "exit-slip.json": (
            "evidence-lane.non-lifecycle-local-package-rehearsal.v1.exit-slip"
        ),
    }
    records: list[dict[str, Any]] = []
    for name, schema in expected.items():
        path = root / name
        if not path.is_file():
            raise InstallationError(f"Required package proof is missing: {name}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != schema:
            raise InstallationError(f"Required package proof drifted: {name}")
        if (
            name
            in {
                "package-surface-coherence.json",
            }
            and value.get("status") != "PASS"
        ):
            raise InstallationError(f"Required package proof did not pass: {name}")
        records.append(
            {
                "path": path.relative_to(plugin_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    audit_members = {
        "systemwide-route-audit.json": "evidence-lane.systemwide-route-audit.v1",
        "executable-fingerprint-refresh.json": (
            "evidence-lane.executable-fingerprint-refresh.v1"
        ),
    }
    present_audits = [name for name in audit_members if (root / name).is_file()]
    if len(present_audits) != 1:
        raise InstallationError(
            "The package must contain exactly one current-route or executable "
            "fingerprint proof."
        )
    audit_name = present_audits[0]
    audit_path = root / audit_name
    audit_value = json.loads(audit_path.read_text(encoding="utf-8"))
    if (
        audit_value.get("schema") != audit_members[audit_name]
        or audit_value.get("status") != "PASS"
    ):
        raise InstallationError(f"Required package proof drifted: {audit_name}")
    records.append(
        {
            "path": audit_path.relative_to(plugin_root).as_posix(),
            "bytes": audit_path.stat().st_size,
            "sha256": _sha256(audit_path),
        }
    )
    return {
        "status": "PASS",
        "proof_count": len(records),
        "records": records,
        "rehearsal_root_present": (plugin_root / "_evidence_lane_rehearsal").exists(),
    }


def _validate_plugin(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    contract = json.loads(
        (plugin_root / "scripts" / "codex-release-channel.json").read_text(
            encoding="utf-8"
        )
    )
    stable = contract.get("stable") or {}
    local_testing = contract.get("local_testing") or {}
    live_slots = contract.get("live_slot_policy") or {}
    workflow_scope = contract.get("workflow_scope") or {}
    goal_completion_policy = contract.get("goal_completion_policy") or {}
    helper_distribution = contract.get("helper_distribution_policy") or {}
    behavior_ownership = contract.get("behavior_ownership") or {}
    stable_activation_gate = contract.get("stable_activation_gate") or {}
    brand_identity = contract.get("brand_identity") or {}
    remote_git = contract.get("remote_git_policy") or {}
    promotion = contract.get("promotion_gate") or {}
    skill_count = len(list((plugin_root / "skills").glob("*/SKILL.md")))
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text("utf-8"))
    constants = (
        plugin_root / "src" / "evidence_lane_plugin" / "constants.py"
    ).read_text(encoding="utf-8")
    engine_match = re.search(
        r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
        constants,
        flags=re.MULTILINE,
    )
    catalog = _catalog(plugin_root)
    interface = dict(manifest.get("interface") or {})
    brand_icon = plugin_root / str(brand_identity.get("icon_path") or "")
    package_proofs = _validate_package_proof_manifests(plugin_root)
    release_helpers = (
        plugin_root / "scripts" / "codex_release" / "install_codex_stable.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "build_codex_exact_commit_package.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "seal_codex_git_ci_release_authority.py",
        plugin_root / "scripts" / "codex_release" / "seal_external_release_receipts.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "seal_github_app_production_delivery.py",
        plugin_root / "scripts" / "codex_release" / "accept_codex_stable.py",
    )
    if (
        manifest.get("name") != PLUGIN_NAME
        or not version.startswith(f"{BASE_RELEASE}+codex.")
        or manifest.get("mcpServers") != "./.mcp.json"
        or "apps" in manifest
        or (plugin_root / ".app.json").exists()
        or contract.get("schema") != "evidence-lane.codex-release-channel.v2"
        or stable.get("release") != BASE_RELEASE
        or stable.get("slot_role") != "main-git-release"
        or stable.get("codex_marketplace_slot") != MARKETPLACE_NAME
        or stable.get("marketplace_display_name") != MARKETPLACE_DISPLAY_NAME
        or stable.get("install_source") != "GIT_MAIN_EXACT_COMMIT_AFTER_GOVERNED_MERGE"
        or stable.get("byte_frozen") is not False
        or stable.get("updates_require_verified_unique_build_identity") is not True
        or stable.get("stable_selector_is_persistent") is not True
        or stable.get("stable_updates_reinstall_in_place") is not True
        or stable.get("build_identity_is_receipt_not_selector") is not True
        or stable.get("native_tool_count") != EXPECTED_CATALOG["tools"]
        or stable.get("native_read_tool_count") != EXPECTED_CATALOG["read"]
        or stable.get("native_write_tool_count") != EXPECTED_CATALOG["write"]
        or stable.get("skill_count") != EXPECTED_CATALOG["skills"]
        or skill_count != EXPECTED_CATALOG["skills"]
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or project.get("project", {}).get("version") != BASE_RELEASE
        or engine_match is None
        or engine_match.group("version") != BASE_RELEASE
        or local_testing.get("release_line") != BASE_RELEASE
        or local_testing.get("slot_role") != "versioned-local-testing"
        or local_testing.get("codex_marketplace_slot") != LOCAL_TESTING_MARKETPLACE_NAME
        or local_testing.get("marketplace_display_name")
        != LOCAL_TESTING_MARKETPLACE_DISPLAY_NAME
        or local_testing.get("install_source") != "FRESH_VERSIONED_LOCAL_PACKAGE"
        or local_testing.get("same_marketplace_selector_reused") is not True
        or local_testing.get("fresh_package_version_per_local_build") is not True
        or local_testing.get("enabled_only_during_local_test") is not True
        or local_testing.get("stable_git_main_mutation_allowed_during_local_build")
        is not False
        or local_testing.get("helper_installs_plugin") is not False
        or live_slots.get("exact_slot_count") != 2
        or live_slots.get("allowed_slots")
        != [
            "main-git-release",
            "versioned-local-testing",
        ]
        or live_slots.get("allowed_marketplaces")
        != [
            MARKETPLACE_NAME,
            LOCAL_TESTING_MARKETPLACE_NAME,
        ]
        or live_slots.get("max_enabled_plugin_count") != 1
        or live_slots.get("exact_registered_plugin_count") != 2
        or live_slots.get("stable_selector_growth_allowed") is not False
        or live_slots.get("max_active_native_mcp_count") != 1
        or live_slots.get("max_active_tunnel_count") != 1
        or live_slots.get("inactive_slot_remains_installed") is not True
        or live_slots.get("manual_loaded_cache_deletion_allowed") is not False
        or live_slots.get("obsolete_marketplace_registrations_must_be_absent")
        is not True
        or workflow_scope != EXPECTED_WORKFLOW_SCOPE
        or goal_completion_policy != EXPECTED_GOAL_COMPLETION_POLICY
        or helper_distribution != EXPECTED_HELPER_DISTRIBUTION_POLICY
        or behavior_ownership != EXPECTED_BEHAVIOR_OWNERSHIP
        or stable_activation_gate != EXPECTED_STABLE_ACTIVATION_GATE
        or brand_identity != EXPECTED_BRAND_IDENTITY
        or interface.get("displayName") != brand_identity.get("display_name")
        or interface.get("composerIcon") != "./assets/evidence-lane-icon.png"
        or interface.get("logo") != "./assets/evidence-lane-icon.png"
        or not brand_icon.is_file()
        or _sha256(brand_icon) != brand_identity.get("icon_sha256")
        or contract.get("host_storage_tunnel_matrix")
        != EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
        or remote_git.get("effective_release") != BASE_RELEASE
        or remote_git.get("per_push_confirmation_token_required") is not False
        or remote_git.get("automatic_push_scope")
        != "GITHUB_APP_GOVERNED_FEATURE_BRANCH_THEN_EXACT_MAIN_MERGE"
        or remote_git.get("host_managed_credentials_only") is not True
        or remote_git.get("main_push_allowed") is not False
        or remote_git.get("merge_allowed") is not True
        or remote_git.get("pull_request_acceptance_allowed") is not True
        or remote_git.get("force_push_allowed") is not False
        or promotion.get("mode") != "CODE"
        or promotion.get("ci_cd_law") != "CONTROLLED_REQUIRED"
        or promotion.get("explicit_authority_hil_required") is not True
        or not all(path.is_file() for path in release_helpers)
        or package_proofs["proof_count"] != 5
        or package_proofs["rehearsal_root_present"] is not False
    ):
        raise InstallationError("The extracted v2 plugin or release contract drifted.")
    native = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    if set(native.get("mcpServers") or {}) != {"evidence-lane"}:
        raise InstallationError(
            "The package must contain one native evidence-lane MCP."
        )
    forbidden = (
        plugin_root / "release-channels.json",
        plugin_root / "remote_adapter",
        plugin_root / "evidence",
    )
    external_app_artifacts = tuple(plugin_root.glob("*-app-connection.json")) + tuple(
        plugin_root.glob("*-app-submission.json")
    )
    if any(path.exists() for path in forbidden) or external_app_artifacts:
        raise InstallationError(
            "A separate app, website, or evidence surface leaked in."
        )
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
        or (logical_action_path.is_file() and set(hooks) != {"description", "hooks"})
        or not isinstance(logical_actions, dict)
        or list(logical_actions) != list(hook_events)
        or any(
            not isinstance(logical_actions.get(name), list) or not logical_actions[name]
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
        raise InstallationError("The persistent hook event set drifted.")
    persistent_notice_markers = {
        "session_start.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_NOTICE=",
        "prompt_submit.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
        "post_tool_use.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=",
    }
    for name, marker in persistent_notice_markers.items():
        source = (plugin_root / "hooks" / name).read_text(encoding="utf-8")
        required = (
            "render_persistent_notice",
            'result["systemMessage"]',
            marker,
        )
        if any(value not in source for value in required):
            raise InstallationError(
                f"{name} does not emit the persistent change notice."
            )
    return {
        "plugin_id": PLUGIN_NAME,
        "version": version,
        "manifest_sha256": _sha256(manifest_path),
        "catalog": catalog,
        "surface_inventory": _surface_inventory(plugin_root, version=version),
        "package_proofs": package_proofs,
    }


def _marketplace_bytes(marketplace_name: str) -> bytes:
    display_name = {
        LOCAL_TESTING_MARKETPLACE_NAME: LOCAL_TESTING_MARKETPLACE_DISPLAY_NAME,
    }.get(marketplace_name, MARKETPLACE_DISPLAY_NAME)
    return _json_bytes(
        {
            "name": marketplace_name,
            "interface": {"displayName": display_name},
            "plugins": [
                {
                    "name": PLUGIN_NAME,
                    "source": {
                        "source": "local",
                        "path": "./plugins/evidence-lane-plugin",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Developer Tools",
                }
            ],
        }
    )


def _marketplace_file_inventory(root: Path) -> dict[str, str]:
    inventory: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise InstallationError(
                "Local plugin update refuses symbolic links and reparse aliases."
            )
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = _sha256(path)
    return inventory


def _sync_plugin_creator_local_source(*, source: Path, target: Path) -> None:
    """Apply the plugin-creator local update without renaming its open root."""

    source_inventory = _marketplace_file_inventory(source)
    target_inventory = _marketplace_file_inventory(target)
    marker = "EVIDENCE_LANE_STAGE.json"
    for relative in sorted(set(target_inventory) - set(source_inventory)):
        (target / Path(relative)).unlink()
    for relative in sorted(source_inventory):
        if relative == marker:
            continue
        _write_atomic(target / Path(relative), (source / Path(relative)).read_bytes())
    source_directories = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_dir() and not path.is_symlink()
    }
    for directory in sorted(
        (path for path in target.rglob("*") if path.is_dir() and not path.is_symlink()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if directory.relative_to(target).as_posix() not in source_directories:
            directory.rmdir()
    if marker in source_inventory:
        _write_atomic(target / marker, (source / marker).read_bytes())
    if _marketplace_file_inventory(target) != source_inventory:
        raise InstallationError(
            "The plugin-creator local source update did not reproduce staged bytes."
        )


def _stage_marketplace(
    *,
    extracted: Path,
    marketplace_root: Path,
    data_root: Path,
    identity: dict[str, Any],
    archive_sha256: str,
    marketplace_name: str = MARKETPLACE_NAME,
    comparison_surface: dict[str, Any] | None = None,
    comparison_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    codex_home = marketplace_root.parent.parent.resolve()
    expected_parent = codex_home / "local-marketplaces"
    if marketplace_root.parent.resolve() != expected_parent.resolve():
        raise InstallationError("Marketplace root is outside Codex local-marketplaces.")
    cache_root = codex_home / "plugins" / "cache"
    if _inside(marketplace_root, cache_root):
        raise InstallationError(
            "Generated Codex plugin cache is immutable to this installer."
        )
    staging = marketplace_root.parent / (
        f".{marketplace_root.name}.staging-{uuid.uuid4().hex}"
    )
    staging_plugin = staging / "plugins" / PLUGIN_NAME
    staging_plugin.parent.mkdir(parents=True, exist_ok=True)
    prior_plugin = marketplace_root / "plugins" / PLUGIN_NAME
    previous_surface = comparison_surface
    if previous_surface is None and prior_plugin.is_dir():
        prior_manifest = json.loads(
            (prior_plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        previous_surface = _surface_inventory(
            prior_plugin,
            version=str(prior_manifest.get("version") or "UNVERIFIED"),
            allow_pre_logical_action_upgrade_baseline=True,
        )
    surface_change = _surface_change_display(
        previous=previous_surface,
        current=identity["surface_inventory"],
    )
    try:
        shutil.copytree(extracted, staging_plugin)
        marketplace_path = staging / ".agents" / "plugins" / "marketplace.json"
        _write_atomic(marketplace_path, _marketplace_bytes(marketplace_name))
        prepared = {
            "schema": "evidence-lane.codex-marketplace-stage.v2",
            "state": "STAGED_NOT_HOST_ACTIVE",
            "marketplace": marketplace_name,
            "marketplace_display_name": (
                {
                    LOCAL_TESTING_MARKETPLACE_NAME: (
                        LOCAL_TESTING_MARKETPLACE_DISPLAY_NAME
                    ),
                }.get(marketplace_name, MARKETPLACE_DISPLAY_NAME)
            ),
            "plugin": identity,
            "archive_sha256": archive_sha256,
            "comparison_baseline": comparison_baseline,
            "surface_change_display": surface_change,
            "generated_cache_written_directly": False,
            "prior_release_deleted": False,
        }
        _write_atomic(staging / "EVIDENCE_LANE_STAGE.json", _json_bytes(prepared))
        if marketplace_root.exists():
            current_stage = marketplace_root / "EVIDENCE_LANE_STAGE.json"
            if current_stage.is_file():
                current = json.loads(current_stage.read_text(encoding="utf-8"))
                if current.get("archive_sha256") == archive_sha256:
                    if current.get("comparison_baseline") != comparison_baseline:
                        raise InstallationError(
                            "The exact staged marketplace comparison baseline drifted."
                        )
                    preserved_change = _verified_staged_surface_change_display(
                        current.get("surface_change_display"),
                        current=identity["surface_inventory"],
                    )
                    shutil.rmtree(staging)
                    return {
                        "state": "ALREADY_STAGED_EXACT",
                        "prior_marketplace_archived": False,
                        "marketplace_rotation_mode": (
                            "PLUGIN_CREATOR_LOCAL_SOURCE_UPDATE"
                            if marketplace_name == LOCAL_TESTING_MARKETPLACE_NAME
                            else "ALREADY_STAGED_EXACT"
                        ),
                        "route_law": (
                            PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW
                            if marketplace_name == LOCAL_TESTING_MARKETPLACE_NAME
                            else None
                        ),
                        "windows_root_rotation_attempted": False,
                        "comparison_baseline": comparison_baseline,
                        "surface_change_display": preserved_change,
                    }
            archive_root = (
                data_root / "installations" / "codex-v300" / "marketplace-archives"
            )
            archive_root.mkdir(parents=True, exist_ok=True)
            prior_sha = (
                _sha256(current_stage) if current_stage.is_file() else "UNSEALED"
            )
            archive_target = archive_root / f"{marketplace_root.name}-{prior_sha[:16]}"
            if archive_target.exists():
                raise InstallationError(
                    "The exact prior marketplace archive already exists."
                )
            if not _inside(marketplace_root, expected_parent):
                raise InstallationError(
                    "Refusing to move an uncontained marketplace root."
                )
            if marketplace_name == LOCAL_TESTING_MARKETPLACE_NAME:
                # PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW: a live Codex task holds
                # the configured local marketplace skill directory open. Local
                # updates preserve that root, archive its exact bytes, update
                # its source in place, and let `codex plugin add` materialize the
                # fresh cachebuster. The historical Windows whole-root rotation
                # is never attempted or selected as a fallback.
                shutil.copytree(marketplace_root, archive_target)
                prior_inventory = _marketplace_file_inventory(marketplace_root)
                if _marketplace_file_inventory(archive_target) != prior_inventory:
                    raise InstallationError(
                        "The prior local marketplace archive is not byte-exact."
                    )
                try:
                    _sync_plugin_creator_local_source(
                        source=staging,
                        target=marketplace_root,
                    )
                except (OSError, InstallationError) as update_error:
                    try:
                        _sync_plugin_creator_local_source(
                            source=archive_target,
                            target=marketplace_root,
                        )
                    except (OSError, InstallationError) as restore_error:
                        raise InstallationError(
                            "Plugin-creator local update failed and archived-byte "
                            f"restoration also failed: {restore_error}"
                        ) from update_error
                    raise InstallationError(
                        "Plugin-creator local update failed; prior bytes were restored."
                    ) from update_error
                rotation_mode = "PLUGIN_CREATOR_LOCAL_SOURCE_UPDATE"
            else:
                os.replace(marketplace_root, archive_target)
                rotation_mode = "ATOMIC_ROOT_ROTATION"
            prior_archived = True
        else:
            prior_archived = False
            rotation_mode = "NEW_MARKETPLACE_ROOT"
        if rotation_mode != "PLUGIN_CREATOR_LOCAL_SOURCE_UPDATE":
            os.replace(staging, marketplace_root)
        return {
            "state": "STAGED",
            "prior_marketplace_archived": prior_archived,
            "marketplace_rotation_mode": rotation_mode,
            "route_law": (
                PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW
                if marketplace_name == LOCAL_TESTING_MARKETPLACE_NAME
                else None
            ),
            "windows_root_rotation_attempted": False
            if marketplace_name == LOCAL_TESTING_MARKETPLACE_NAME
            else rotation_mode == "ATOMIC_ROOT_ROTATION",
            "comparison_baseline": comparison_baseline,
            "surface_change_display": surface_change,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _windows_hidden_creationflags() -> int:
    """Return the no-console flag for every installer-owned child process."""

    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _default_codex_cli_executable() -> Path:
    """Resolve the supported npm-native Codex CLI without probing dead routes."""

    appdata = os.environ.get("APPDATA")
    appdata_root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return appdata_root / NPM_CODEX_CLI_RELATIVE_PATH


def _resolve_codex_cli_executable(
    executable: Path | None,
    *,
    verify_version: bool,
) -> Path:
    """Fail before mutation unless the supported native CLI is executable.

    The packaged desktop app also contains a ``codex.exe`` below WindowsApps,
    but Windows denies direct execution of that binary and it is not the Codex
    CLI update surface.  Never probe it and never use it as a fallback.
    """

    resolved = Path(executable or _default_codex_cli_executable()).resolve()
    normalized = str(resolved).replace("/", "\\").casefold()
    if "\\windowsapps\\" in normalized:
        raise InstallationError(
            "The packaged WindowsApps codex.exe is a forbidden install route; "
            "use the npm-native Codex CLI."
        )
    if not resolved.is_file():
        raise InstallationError(
            "The supported npm-native Codex CLI executable is unavailable."
        )
    if not verify_version:
        return resolved
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            creationflags=_windows_hidden_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            "The npm-native Codex CLI version probe failed before mutation."
        ) from exc
    version_output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if (
        completed.returncode != 0
        or re.search(r"(?m)^codex-cli\s+\S+\s*$", version_output) is None
    ):
        raise InstallationError(
            "The resolved executable is not the supported npm-native Codex CLI."
        )
    return resolved


def _run_codex(
    executable: Path,
    codex_home: Path,
    arguments: list[str],
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    timeout_seconds = 480 if arguments[:3] == ["plugin", "marketplace", "add"] else 120
    completed = subprocess.run(
        [str(executable), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=timeout_seconds,
        creationflags=_windows_hidden_creationflags(),
    )
    if completed.returncode != 0:
        raise InstallationError(
            f"Codex command failed ({arguments[:3]}): {completed.stderr.strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallationError(
            "Codex did not return the requested JSON receipt."
        ) from exc


def _run_plugin_creator_local_cache_materialization(
    *,
    executable: Path,
    codex_home: Path,
    plugin_selector: str,
    target_version: str,
    old_active_version: str,
) -> dict[str, Any]:
    """Call the current Codex installer route once and classify its host boundary.

    The desktop may keep the prior cache loaded.  Codex still materializes the
    new cache, then returns a bounded activation error.  That error is an exact
    restart boundary, not authority to rotate or delete the loaded cache.
    """

    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    completed = subprocess.run(
        [str(executable), "plugin", "add", plugin_selector, "--json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
        creationflags=_windows_hidden_creationflags(),
    )
    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    if completed.returncode == 0:
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise InstallationError(
                "Codex materialized the local cache without a JSON receipt."
            ) from exc
        if (
            result.get("pluginId") != plugin_selector
            or result.get("version") != target_version
        ):
            raise InstallationError(
                "Codex activated a local cache identity other than the staged build."
            )
        outcome = "TARGET_SELECTED_HOST_RESTART_REQUIRED"
    else:
        lowered = stderr.casefold()
        if (
            "failed to activate updated plugin cache version" not in lowered
            or "remains active" not in lowered
            or target_version.casefold() not in lowered
            or old_active_version.casefold() not in lowered
        ):
            raise InstallationError(
                "Codex plugin add failed outside the loaded-old-cache restart boundary."
            )
        result = None
        outcome = "OLD_SELECTED_TARGET_CACHE_MATERIALIZED_HOST_RESTART_REQUIRED"
    return {
        "status": "PASS",
        "route": "CODEX_PLUGIN_ADD",
        "invocation_count": 1,
        "outcome": outcome,
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest().upper(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest().upper(),
        "plugin_add": result,
    }


def _disabled_hook_state(
    *,
    codex_home: Path,
    selector: str,
    expected_hooks: dict[str, Any],
) -> dict[str, Any]:
    """Prove the configured selector's complete hook set remains OFF."""

    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        raise InstallationError("The Codex hook configuration is unavailable.")
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    states = dict(dict(config.get("hooks") or {}).get("state") or {})
    prefix = f"{selector}:hooks/hooks.json:"
    rows = [
        (key, dict(value))
        for key, value in sorted(states.items())
        if str(key).startswith(prefix)
    ]
    expected_event_tokens = {
        re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
        for event in EXPECTED_PACKAGE_HOOK_EVENTS
    }
    observed_event_tokens = {
        str(key)[len(prefix) :].split(":", 1)[0] for key, _value in rows
    }
    expected_event_count = int(expected_hooks.get("count") or 0)
    expected_handler_count = int(expected_hooks.get("handler_count") or 0)
    if (
        expected_event_count != len(EXPECTED_PACKAGE_HOOK_EVENTS)
        or expected_handler_count < expected_event_count
        or len(rows) != expected_handler_count
        or observed_event_tokens != expected_event_tokens
        or any(value.get("enabled") is not False for _, value in rows)
    ):
        raise InstallationError(
            "The complete local selector hook set is not truthfully OFF."
        )
    return {
        "status": "PASS",
        "selector": selector,
        "hook_count": expected_event_count,
        "hook_handler_count": len(rows),
        "hook_count_semantics": "REGISTERED_EVENT_COUNT",
        "hook_handler_count_semantics": "TOTAL_NESTED_HANDLER_ACTION_COUNT",
        "registered_event_tokens": sorted(observed_event_tokens),
        "all_enabled": False,
        "hooks_enabled_by_update": False,
        "config_sha256": _sha256(config_path),
    }


def _load_self_sealed_json(
    *,
    path: Path,
    expected_file_sha256: str,
    authority_root: Path,
    label: str,
) -> tuple[dict[str, Any], Path, str]:
    """Load one authority receipt without accepting a path or seal substitution."""

    resolved = path.resolve()
    expected = str(expected_file_sha256 or "").strip().upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", expected) is None
        or not resolved.is_file()
        or not _inside(resolved, authority_root.resolve())
        or _sha256(resolved) != expected
    ):
        raise InstallationError(f"The {label} file authority is absent or drifted.")
    try:
        receipt = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError(f"The {label} is not readable sealed JSON.") from exc
    if not isinstance(receipt, dict):
        raise InstallationError(f"The {label} has an invalid JSON shape.")
    core = dict(receipt)
    self_seal = str(core.pop("receipt_sha256", "")).upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", self_seal) is None
        or hashlib.sha256(_json_bytes(core)).hexdigest().upper() != self_seal
    ):
        raise InstallationError(f"The {label} self-seal drifted.")
    return receipt, resolved, expected


def _plugin_creator_active_local_slot_update(
    plugin_list: dict[str, Any],
    *,
    plugin_selector: str,
    target_version: str,
) -> dict[str, Any] | None:
    """Detect the supported one-enabled-local, Main-disabled update boundary."""

    installed = plugin_list.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence_rows = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    target_rows = [
        row for row in evidence_rows if row.get("pluginId") == plugin_selector
    ]
    enabled_rows = [row for row in evidence_rows if row.get("enabled") is True]
    if (
        len(target_rows) == 1
        and len(enabled_rows) == 1
        and enabled_rows[0].get("pluginId") == plugin_selector
        and str(target_rows[0].get("version") or "") != target_version
        and all(
            row.get("enabled") is False
            for row in evidence_rows
            if row.get("pluginId") != plugin_selector
        )
    ):
        return {
            "status": "PASS",
            "route": "PLUGIN_CREATOR_ACTIVE_LOCAL_SLOT_UPDATE",
            "selector": plugin_selector,
            "old_active_version": str(target_rows[0].get("version") or ""),
            "target_version": target_version,
            "enabled_evidence_lane_count": 1,
            "main_selector_enabled": False,
            "standalone_plugin_add_invoked": False,
            "cache_materialization_deferred_to_staged_receipt_route": True,
        }
    return None


def _mcp_server_info_matches_exact_plugin(
    server_info: dict[str, Any],
    *,
    expected_plugin_version: str,
) -> bool:
    return (
        server_info.get("name") == "Evidence Lane"
        and server_info.get("version") == expected_plugin_version
        and re.fullmatch(
            r"\d+\.\d+\.\d+\+codex\.[0-9A-Za-z](?:[0-9A-Za-z.-]*[0-9A-Za-z])?",
            expected_plugin_version,
        )
        is not None
    )


def _prewarm_installed_runtime(
    plugin_root: Path,
    *,
    data_root: Path,
    expected_plugin_version: str,
) -> dict[str, Any]:
    """Build and probe the installed cache before any task can be reopened."""

    runner = plugin_root / "scripts" / "run_mcp.py"
    brand_icon = plugin_root / str(EXPECTED_BRAND_IDENTITY["icon_path"])
    if not runner.is_file():
        raise InstallationError("The installed package has no governed MCP runner.")
    if (
        not brand_icon.is_file()
        or _sha256(brand_icon) != EXPECTED_BRAND_IDENTITY["icon_sha256"]
    ):
        raise InstallationError(
            "The installed Evidence Lane icon is missing or changed before prewarm."
        )
    cache_hygiene_before = _require_no_plugin_cache_artifacts(plugin_root)
    started = time.monotonic()
    prewarm_environment = os.environ.copy()
    prewarm_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    prewarm_environment["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    runtime_control_root = str(data_root.resolve())
    prewarm_environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = runtime_control_root
    try:
        bootstrap_process = subprocess.run(
            [sys.executable, str(runner), "--bootstrap-only"],
            check=False,
            cwd=plugin_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=900,
            env=prewarm_environment,
            creationflags=_windows_hidden_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            "The installed runtime bootstrap did not complete before task reopen."
        ) from exc
    bootstrap_attempts = [
        {
            "attempt": 1,
            "returncode": int(bootstrap_process.returncode),
            "stdout_sha256": hashlib.sha256(bootstrap_process.stdout)
            .hexdigest()
            .upper(),
            "stderr_sha256": hashlib.sha256(bootstrap_process.stderr)
            .hexdigest()
            .upper(),
        }
    ]
    if bootstrap_process.returncode != 0:
        raise InstallationError(
            "The installed runtime bootstrap failed on the sealed package bytes "
            f"before task reopen (attempts={bootstrap_attempts})."
        )
    try:
        bootstrap_lines = [
            line
            for line in bootstrap_process.stdout.decode("utf-8").splitlines()
            if line.strip()
        ]
        bootstrap_result = json.loads(bootstrap_lines[-1])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The installed runtime bootstrap returned no exact JSON receipt."
        ) from exc
    if (
        bootstrap_result.get("schema") != "evidence-lane.codex-runtime-bootstrap.v1"
        or bootstrap_result.get("status") != "PASS"
        or bootstrap_result.get("toolchain_inspected") is not False
        or bootstrap_result.get("native_toolchain_required") is not False
    ):
        raise InstallationError("The installed runtime bootstrap identity drifted.")
    runtime_authority = dict(bootstrap_result.get("runtime_authority") or {})
    if (
        runtime_authority.get("schema")
        != "evidence-lane.codex-installed-runtime-authority-prewarm.v1"
        or runtime_authority.get("status") != "PASS"
        or runtime_authority.get("installation_version")
        != expected_plugin_version
        or runtime_authority.get("flash_plugin_version") != expected_plugin_version
        or runtime_authority.get("flash_action")
        not in {"CREATED", "REUSED", "BUILD_IDENTITY_MIGRATED"}
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(runtime_authority.get("flash_receipt_sha256") or ""),
        )
        is None
        or runtime_authority.get("project_state_mutated") is not False
        or runtime_authority.get("candidate_mutated") is not False
        or runtime_authority.get("pointer_moved") is not False
    ):
        raise InstallationError(
            "The installed runtime did not activate the exact package Flash authority before task reopen."
        )
    runtime_python = Path(str(bootstrap_result.get("runtime_python") or "")).resolve()
    runtime_projection_root = Path(
        str(bootstrap_result.get("runtime_projection_root") or "")
    ).resolve()
    runtime_environment = Path(
        str(bootstrap_result.get("runtime_environment") or "")
    ).resolve()
    runtime_identity = dict(bootstrap_result.get("runtime_identity") or {})
    expected_runtime_parent = (data_root / "runtime" / "codex").resolve()
    expected_lock_sha256 = _sha256(plugin_root / "requirements.lock.txt")
    expected_torch_cpu_lock_sha256 = _sha256(
        plugin_root / "requirements.torch-cpu.lock.txt"
    )
    expected_torch_nvidia_lock_sha256 = _sha256(
        plugin_root / "requirements.torch-nvidia.lock.txt"
    )
    expected_onnx_directml_lock_sha256 = _sha256(
        plugin_root / "requirements.onnx-directml.lock.txt"
    )
    expected_accelerator_profile = str(
        runtime_identity.get("accelerator_profile") or ""
    )
    expected_selected_torch_lock_sha256 = (
        expected_torch_nvidia_lock_sha256
        if expected_accelerator_profile == "NVIDIA"
        else expected_torch_cpu_lock_sha256
    )
    expected_toolchain_lock_sha256 = _sha256(
        plugin_root / "requirements.toolchain.lock.txt"
    )
    if not runtime_python.is_file():
        raise InstallationError("The governed bootstrap did not create its runtime.")
    if (
        not _inside(runtime_projection_root, expected_runtime_parent)
        or runtime_environment != runtime_projection_root / "venv"
        or not _inside(runtime_python, runtime_environment)
        or runtime_identity.get("schema") != "evidence-lane.codex-native-runtime.v1"
        or runtime_identity.get("requirements_lock_sha256") != expected_lock_sha256
        or runtime_identity.get("requirements_torch_cpu_lock_sha256")
        != expected_torch_cpu_lock_sha256
        or runtime_identity.get("requirements_torch_nvidia_lock_sha256")
        != expected_torch_nvidia_lock_sha256
        or runtime_identity.get("requirements_onnx_directml_lock_sha256")
        != expected_onnx_directml_lock_sha256
        or runtime_identity.get("selected_torch_lock_sha256")
        != expected_selected_torch_lock_sha256
        or runtime_identity.get("requirements_toolchain_lock_sha256")
        != expected_toolchain_lock_sha256
        or re.fullmatch(r"[A-F0-9]{64}", str(runtime_identity.get("runtime_key") or ""))
        is None
    ):
        raise InstallationError(
            "The derived runtime is outside its sealed durable authority."
        )
    tool_license_inventory_path = (
        plugin_root / "toolchains" / "tool-license-inventory.v1.json"
    )
    expected_tool_license_inventory_sha256 = _sha256(tool_license_inventory_path)
    expected_tool_license_entry_count = int(
        json.loads(tool_license_inventory_path.read_text(encoding="utf-8"))[
            "tool_requirement_count"
        ]
    )
    license_script = plugin_root / "scripts" / "generate_runtime_license_bundle.py"
    runtime_key = str(runtime_identity["runtime_key"])
    license_output = (data_root / "runtime" / "licenses" / runtime_key).resolve()
    if not license_script.is_file() or not _inside(license_output, data_root.resolve()):
        raise InstallationError(
            "The installed runtime license-bundle route is missing or escaped."
        )
    license_manifest_path = license_output / "manifest.v1.json"
    reuse_existing_license_bundle = False
    stale_runtime_license_bundle_purged = False
    if license_manifest_path.is_file():
        existing_runtime_licenses = json.loads(
            license_manifest_path.read_text(encoding="utf-8")
        )
        reuse_existing_license_bundle = (
            existing_runtime_licenses.get("schema")
            == "evidence-lane.installed-runtime-license-bundle.v1"
            and existing_runtime_licenses.get("status") == "PASS"
            and existing_runtime_licenses.get("accelerator_profile")
            == expected_accelerator_profile
            and existing_runtime_licenses.get("requirements_lock_sha256")
            == expected_lock_sha256
            and existing_runtime_licenses.get("requirements_torch_cpu_lock_sha256")
            == expected_torch_cpu_lock_sha256
            and existing_runtime_licenses.get("requirements_torch_nvidia_lock_sha256")
            == expected_torch_nvidia_lock_sha256
            and existing_runtime_licenses.get("requirements_onnx_directml_lock_sha256")
            == expected_onnx_directml_lock_sha256
            and existing_runtime_licenses.get("requirements_toolchain_lock_sha256")
            == expected_toolchain_lock_sha256
            and existing_runtime_licenses.get("tool_license_inventory_sha256")
            == expected_tool_license_inventory_sha256
            and int(existing_runtime_licenses.get("tool_license_entry_count") or 0)
            == expected_tool_license_entry_count
            and existing_runtime_licenses.get(
                "all_tool_requirements_have_physical_license_records"
            )
            is True
            and int(
                existing_runtime_licenses.get("tool_requirement_license_record_count")
                or 0
            )
            == expected_tool_license_entry_count
        )
        if reuse_existing_license_bundle:
            runtime_licenses = existing_runtime_licenses
            runtime_licenses["output"] = str(license_output)
            runtime_licenses["manifest_sha256"] = _sha256(license_manifest_path)
            runtime_licenses["reused"] = True
            runtime_licenses["stale_runtime_license_bundle_purged"] = False
        else:
            if not _inside(
                license_output, (data_root / "runtime" / "licenses").resolve()
            ):
                raise InstallationError(
                    "The stale runtime license bundle escaped its exact derived root."
                )
            shutil.rmtree(license_output)
            stale_runtime_license_bundle_purged = True
    if not reuse_existing_license_bundle:
        license_output.parent.mkdir(parents=True, exist_ok=True)
        try:
            license_process = subprocess.run(
                [
                    str(runtime_python),
                    str(license_script),
                    "--output",
                    str(license_output),
                    "--plugin-root",
                    str(plugin_root),
                ],
                check=False,
                cwd=plugin_root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=300,
                env=prewarm_environment,
                creationflags=_windows_hidden_creationflags(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallationError(
                "The installed runtime license bundle did not complete."
            ) from exc
        if license_process.returncode != 0:
            raise InstallationError(
                "The installed runtime license bundle failed before task reopen."
            )
        try:
            license_lines = [
                line
                for line in license_process.stdout.decode("utf-8").splitlines()
                if line.strip()
            ]
            runtime_licenses = json.loads(license_lines[-1])
        except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstallationError(
                "The installed runtime license bundle returned no exact receipt."
            ) from exc
        runtime_licenses["reused"] = False
        runtime_licenses["stale_runtime_license_bundle_purged"] = (
            stale_runtime_license_bundle_purged
        )
    if (
        runtime_licenses.get("schema")
        != "evidence-lane.installed-runtime-license-bundle.v1"
        or runtime_licenses.get("status") != "PASS"
        or runtime_licenses.get("accelerator_profile") != expected_accelerator_profile
        or runtime_licenses.get("requirements_lock_sha256") != expected_lock_sha256
        or runtime_licenses.get("requirements_torch_cpu_lock_sha256")
        != expected_torch_cpu_lock_sha256
        or runtime_licenses.get("requirements_torch_nvidia_lock_sha256")
        != expected_torch_nvidia_lock_sha256
        or runtime_licenses.get("requirements_onnx_directml_lock_sha256")
        != expected_onnx_directml_lock_sha256
        or runtime_licenses.get("requirements_toolchain_lock_sha256")
        != expected_toolchain_lock_sha256
        or runtime_licenses.get("tool_license_inventory_sha256")
        != expected_tool_license_inventory_sha256
        or int(runtime_licenses.get("tool_license_entry_count") or 0)
        != expected_tool_license_entry_count
        or runtime_licenses.get("all_tool_requirements_license_classified") is not True
        or runtime_licenses.get("all_tool_requirements_have_physical_license_records")
        is not True
        or int(runtime_licenses.get("tool_requirement_license_record_count") or 0)
        != expected_tool_license_entry_count
        or runtime_licenses.get("mcp_inventory_separate") is not True
        or int(runtime_licenses.get("distribution_count") or 0) < 1
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(runtime_licenses.get("receipt_sha256") or ""),
        )
        is None
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(runtime_licenses.get("manifest_sha256") or ""),
        )
        is None
    ):
        raise InstallationError(
            "The installed runtime license bundle failed exact validation."
        )
    native_script = (
        plugin_root / "scripts" / "codex_release" / "install_native_toolchain.py"
    )
    native_manifest = plugin_root / "toolchains" / "native-tools.v1.json"
    native_output = (
        data_root
        / "runtime"
        / "native-toolchain"
        / runtime_key
        / "install-receipt.json"
    ).resolve()
    if (
        not native_script.is_file()
        or not native_manifest.is_file()
        or not _inside(native_output, data_root.resolve())
    ):
        raise InstallationError(
            "The installed native-toolchain route is missing or escaped."
        )
    native_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        native_process = subprocess.run(
            [
                str(runtime_python),
                str(native_script),
                "--plugin-root",
                str(plugin_root),
                "--runtime-root",
                str(data_root),
                "--manifest",
                str(native_manifest),
                "--allow-network",
                "--output",
                str(native_output),
            ],
            check=False,
            cwd=plugin_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=900,
            env=prewarm_environment,
            creationflags=_windows_hidden_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            "The hidden native-toolchain install did not complete."
        ) from exc
    if native_process.returncode != 0 or not native_output.is_file():
        raise InstallationError(
            "The hidden native-toolchain install failed before task reopen."
        )
    native_toolchain = json.loads(native_output.read_text(encoding="utf-8"))
    native_rows = list(native_toolchain.get("tools") or [])
    if (
        native_toolchain.get("schema") != "evidence-lane.installed-native-toolchain.v1"
        or native_toolchain.get("status") != "PASS"
        or native_toolchain.get("hidden_runtime_only") is not True
        or native_toolchain.get("workspace_install_used") is not False
        or native_toolchain.get("path_mutated") is not False
        or sum(row.get("status") == "PASS" for row in native_rows) < 7
        or not any(
            row.get("tool_id") == "ghostscript"
            and row.get("status") == "SKIPPED_LICENSE_GRANT_REQUIRED"
            for row in native_rows
        )
    ):
        raise InstallationError(
            "The hidden native-toolchain receipt failed exact validation."
        )
    try:
        prewarm_process = subprocess.run(
            [sys.executable, str(runner), "--prewarm-only"],
            check=False,
            cwd=plugin_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=900,
            env=prewarm_environment,
            creationflags=_windows_hidden_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            "The installed runtime toolchain prewarm did not complete after the "
            "native toolchain was installed."
        ) from exc
    if prewarm_process.returncode != 0:
        raise InstallationError(
            "The installed runtime toolchain prewarm failed after native-toolchain "
            "installation."
        )
    try:
        prewarm_lines = [
            line
            for line in prewarm_process.stdout.decode("utf-8").splitlines()
            if line.strip()
        ]
        prewarm_result = json.loads(prewarm_lines[-1])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The installed runtime toolchain prewarm returned no exact JSON receipt."
        ) from exc
    prewarm_toolchain = dict(prewarm_result.get("runtime_toolchain") or {})
    tool_matrix = plugin_root / "toolchains" / "tool-requirement-matrix.v1.json"
    if (
        prewarm_result.get("schema") != "evidence-lane.codex-native-runtime-prewarm.v1"
        or prewarm_result.get("status") != "PASS"
        or dict(prewarm_result.get("runtime_identity") or {}) != runtime_identity
        or Path(str(prewarm_result.get("runtime_projection_root") or "")).resolve()
        != runtime_projection_root
        or Path(str(prewarm_result.get("runtime_environment") or "")).resolve()
        != runtime_environment
        or Path(str(prewarm_result.get("runtime_python") or "")).resolve()
        != runtime_python
        or prewarm_toolchain.get("schema")
        != "evidence-lane.runtime-toolchain-prewarm.v1"
        or prewarm_toolchain.get("status") != "PASS"
        or prewarm_toolchain.get("matrix_sha256") != _sha256(tool_matrix)
        or int(prewarm_toolchain.get("requirement_count") or 0) < 50
        or prewarm_toolchain.get("failure_count") != 0
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(prewarm_toolchain.get("receipt_sha256") or ""),
        )
        is None
    ):
        raise InstallationError(
            "The installed runtime toolchain prewarm failed exact post-install "
            "validation."
        )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    source = str((plugin_root / "src").resolve())
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source, existing_pythonpath) if part
    )
    environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = str(data_root.resolve())
    environment["EVIDENCE_LANE_HOST_PROFILE"] = "CODEX_DESKTOP"
    probe_source = (
        "import json; "
        "from evidence_lane_plugin.constants import ENGINE_VERSION; "
        "from evidence_lane_plugin.mcp_server import "
        "CODEX_READ_TOOL_NAMES,NATIVE_MCP_SERVER_IDENTITY,create_mcp_server; "
        "from evidence_lane_plugin.runtime_toolchain import inspect_runtime_toolchain; "
        "toolchain=inspect_runtime_toolchain(prewarm_native=True); "
        "server=create_mcp_server(); "
        "route=server._evidence_lane_native_route_receipt; "
        "print(json.dumps({"
        "'engine_version':ENGINE_VERSION,"
        "'native_server_identity':NATIVE_MCP_SERVER_IDENTITY,"
        "'read_tool_count':len(CODEX_READ_TOOL_NAMES),"
        "'tool_count':route['tool_count'],"
        "'tool_catalog_sha256':route['tool_catalog_sha256'],"
        "'route_status':route['status'],"
        "'resource_uri':route['mcp_apps_resource_uri'],"
        "'native_dependency_prewarm_completed':True,"
        "'runtime_toolchain':toolchain"
        "},sort_keys=True))"
    )
    try:
        probe = subprocess.run(
            [str(runtime_python), "-c", probe_source],
            check=False,
            cwd=plugin_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=300,
            creationflags=_windows_hidden_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            "The installed native runtime probe did not complete before task reopen."
        ) from exc
    if probe.returncode != 0:
        raise InstallationError(
            "The installed native runtime probe failed before task reopen "
            f"(exit {probe.returncode})."
        )
    try:
        output_lines = [
            line for line in probe.stdout.decode("utf-8").splitlines() if line.strip()
        ]
        result = json.loads(output_lines[-1])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The installed native runtime probe returned no exact JSON receipt."
        ) from exc
    expected_result = {
        "engine_version": BASE_RELEASE,
        "native_server_identity": "evidence-lane",
        "read_tool_count": EXPECTED_CATALOG["read"],
        "tool_count": EXPECTED_CATALOG["tools"],
        "route_status": "PASS",
        "resource_uri": EXPECTED_BRAND_IDENTITY["resource_uri"],
        "native_dependency_prewarm_completed": True,
    }
    result_without_catalog_seal = dict(result)
    catalog_seal = result_without_catalog_seal.pop("tool_catalog_sha256", None)
    runtime_toolchain = dict(
        result_without_catalog_seal.pop("runtime_toolchain", None) or {}
    )
    if result_without_catalog_seal != expected_result:
        raise InstallationError("The installed native runtime identity drifted.")
    if re.fullmatch(r"[A-F0-9]{64}", str(catalog_seal)) is None:
        raise InstallationError("The installed native catalog seal is missing.")
    tool_matrix = plugin_root / "toolchains" / "tool-requirement-matrix.v1.json"
    if (
        runtime_toolchain.get("schema") != "evidence-lane.runtime-toolchain-prewarm.v1"
        or runtime_toolchain.get("status") != "PASS"
        or runtime_toolchain.get("matrix_sha256") != _sha256(tool_matrix)
        or int(runtime_toolchain.get("requirement_count") or 0) < 50
        or runtime_toolchain.get("failure_count") != 0
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(runtime_toolchain.get("receipt_sha256") or ""),
        )
        is None
    ):
        raise InstallationError(
            "The installed non-MCP runtime/toolchain prewarm failed parity."
        )
    mcp_messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "evidence_lane_installed_mcp_readiness",
                    "version": BASE_RELEASE,
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
    ]
    mcp_input = "".join(
        json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n"
        for message in mcp_messages
    ).encode("utf-8")
    mcp_started = time.monotonic()
    try:
        mcp_process = subprocess.run(
            [sys.executable, str(runner), "--transport", "stdio"],
            input=mcp_input,
            check=False,
            cwd=plugin_root,
            capture_output=True,
            timeout=120,
            env=prewarm_environment,
            creationflags=_windows_hidden_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            "The installed MCP stdio initialize/tools-list readiness proof did "
            "not complete before same-task restart."
        ) from exc
    if mcp_process.returncode != 0:
        raise InstallationError(
            "The installed MCP stdio readiness process failed before same-task "
            f"restart (exit {mcp_process.returncode})."
        )
    try:
        mcp_responses = [
            json.loads(line)
            for line in mcp_process.stdout.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The installed MCP stdio readiness proof returned invalid JSONL."
        ) from exc
    initialize_response = next(
        (row for row in mcp_responses if row.get("id") == 1), None
    )
    tools_response = next((row for row in mcp_responses if row.get("id") == 2), None)
    initialize_result = (
        dict(initialize_response.get("result") or {})
        if isinstance(initialize_response, dict)
        else {}
    )
    server_info = dict(initialize_result.get("serverInfo") or {})
    listed_tools = (
        list((tools_response.get("result") or {}).get("tools") or [])
        if isinstance(tools_response, dict)
        else []
    )
    listed_tool_names = [str(row.get("name") or "") for row in listed_tools]
    public_schema = json.loads(
        (plugin_root / "schemas" / "public-action-schemas.v001.json").read_text(
            encoding="utf-8"
        )
    )
    expected_tool_names = [
        str(row.get("name") or "") for row in public_schema.get("tools") or []
    ]
    if (
        initialize_response is None
        or initialize_response.get("error") is not None
        or tools_response is None
        or tools_response.get("error") is not None
        or not _mcp_server_info_matches_exact_plugin(
            server_info,
            expected_plugin_version=expected_plugin_version,
        )
        or len(set(listed_tool_names)) != len(listed_tool_names)
        or set(listed_tool_names) != set(expected_tool_names)
        or len(listed_tool_names) != EXPECTED_CATALOG["tools"]
        or "runtime_doctor" not in listed_tool_names
    ):
        raise InstallationError(
            "The installed package did not expose its exact native MCP catalog "
            "through a real stdio initialize/tools-list exchange before "
            "same-task restart."
        )
    mcp_stdio_readiness = {
        "schema": "evidence-lane.codex-installed-mcp-stdio-readiness.v1",
        "status": "PASS",
        "initialize_completed": True,
        "tools_list_completed": True,
        "server_name": server_info["name"],
        "server_version": server_info["version"],
        "tool_count": len(listed_tool_names),
        "tool_names_sha256": hashlib.sha256(_json_bytes(sorted(listed_tool_names)))
        .hexdigest()
        .upper(),
        "runtime_doctor_present": True,
        "process_returncode": int(mcp_process.returncode),
        "stdout_sha256": hashlib.sha256(mcp_process.stdout).hexdigest().upper(),
        "stderr_sha256": hashlib.sha256(mcp_process.stderr).hexdigest().upper(),
        "duration_ms": int((time.monotonic() - mcp_started) * 1000),
        "same_task_restart_eligible": True,
        "new_task_required": False,
    }
    mcp_stdio_readiness["receipt_sha256"] = (
        hashlib.sha256(_json_bytes(mcp_stdio_readiness)).hexdigest().upper()
    )
    cache_hygiene_after = _require_no_plugin_cache_artifacts(plugin_root)
    core = {
        "schema": "evidence-lane.codex-installed-runtime-prewarm.v1",
        "status": "PASS",
        "runtime_ready_before_task_reopen": True,
        "plugin_root_sha256": hashlib.sha256(str(plugin_root.resolve()).encode("utf-8"))
        .hexdigest()
        .upper(),
        "runtime_python_sha256": _sha256(runtime_python),
        "runtime_projection_root": str(runtime_projection_root),
        "runtime_identity": runtime_identity,
        "bootstrap_attempt_count": len(bootstrap_attempts),
        "bootstrap_attempts": bootstrap_attempts,
        "bootstrap_stdout_sha256": hashlib.sha256(bootstrap_process.stdout)
        .hexdigest()
        .upper(),
        "bootstrap_stderr_sha256": hashlib.sha256(bootstrap_process.stderr)
        .hexdigest()
        .upper(),
        "runtime_authority": runtime_authority,
        "toolchain_prewarm_after_native_install": True,
        "prewarm_stdout_sha256": hashlib.sha256(prewarm_process.stdout)
        .hexdigest()
        .upper(),
        "prewarm_stderr_sha256": hashlib.sha256(prewarm_process.stderr)
        .hexdigest()
        .upper(),
        "prewarm_toolchain_receipt_sha256": prewarm_toolchain["receipt_sha256"],
        "probe_stdout_sha256": hashlib.sha256(probe.stdout).hexdigest().upper(),
        "probe_stderr_sha256": hashlib.sha256(probe.stderr).hexdigest().upper(),
        "engine_version": result["engine_version"],
        "native_server_identity": result["native_server_identity"],
        "tool_count": result["tool_count"],
        "tool_catalog_sha256": catalog_seal,
        "resource_uri": result["resource_uri"],
        "brand_icon_sha256": _sha256(brand_icon),
        "catalog_expected": dict(EXPECTED_CATALOG),
        "native_dependency_prewarm_completed": True,
        "mcp_stdio_ready_before_task_reopen": True,
        "mcp_stdio_readiness": mcp_stdio_readiness,
        "same_task_restart_eligible": True,
        "new_task_required": False,
        "runtime_toolchain": runtime_toolchain,
        "runtime_toolchain_receipt_sha256": runtime_toolchain["receipt_sha256"],
        "runtime_licenses": runtime_licenses,
        "runtime_license_manifest_sha256": runtime_licenses["manifest_sha256"],
        "native_toolchain": native_toolchain,
        "native_toolchain_receipt_sha256": native_toolchain["receipt_sha256"],
        "cache_hygiene_before": cache_hygiene_before,
        "cache_hygiene_after": cache_hygiene_after,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "task_reopened": False,
    }
    core["receipt_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    return core


def _initialize_installed_hook_event_isolation(
    plugin_root: Path,
    *,
    data_root: Path,
    installation_id: str,
) -> dict[str, Any]:
    """Seal the persistent inactive kill switch before enabling any hooks."""

    module_path = plugin_root / "hooks" / "event_isolation.py"
    policy_path = plugin_root / "hooks" / "event_isolation_policy.json"
    if not module_path.is_file() or not policy_path.is_file():
        raise InstallationError(
            "The installed hook-event isolation runtime is incomplete."
        )
    module_name = (
        "_evidence_lane_installed_hook_isolation_"
        + hashlib.sha256(str(module_path.resolve()).encode("utf-8")).hexdigest()[:16]
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise InstallationError(
            "The installed hook-event isolation runtime could not be loaded."
        )
    module = importlib.util.module_from_spec(spec)
    prior_dont_write_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
        initialized = module.initialize_inactive_kill_switch(
            data_root.resolve(),
            installation_id=installation_id,
        )
    except Exception as exc:
        raise InstallationError(
            "The persistent hook kill switch could not be initialized safely."
        ) from exc
    finally:
        sys.dont_write_bytecode = prior_dont_write_bytecode

    receipt_path = Path(str(initialized.get("path") or "")).resolve()
    expected_path = data_root.resolve() / "hook-event-isolation" / "KILL_SWITCH.json"
    policy_sha256 = _sha256(policy_path)
    if (
        initialized.get("status") != "PASS"
        or initialized.get("state")
        not in {
            "INACTIVE_KILL_SWITCH_INITIALIZED",
            "INACTIVE_KILL_SWITCH_REUSED",
        }
        or receipt_path != expected_path
        or not receipt_path.is_file()
        or str(initialized.get("file_sha256") or "").upper() != _sha256(receipt_path)
        or str(initialized.get("policy_sha256") or "").upper() != policy_sha256
        or str(initialized.get("installation_id") or "") != installation_id
        or not isinstance(initialized.get("generation"), int)
        or isinstance(initialized.get("generation"), bool)
        or int(initialized["generation"]) < 1
    ):
        raise InstallationError(
            "The persistent hook kill-switch receipt failed installed-byte validation."
        )
    try:
        body = json.loads(receipt_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The persistent hook kill-switch receipt is unreadable."
        ) from exc
    if not isinstance(body, dict):
        raise InstallationError(
            "The persistent hook kill-switch payload is not inactive and exact."
        )
    payload = body.get("payload")
    if (
        body.get("schema") != "evidence-lane.codex-hook-kill-switch.v1"
        or not isinstance(payload, dict)
        or payload.get("state") != "INACTIVE"
        or payload.get("installation_id") != installation_id
        or payload.get("generation") != initialized["generation"]
        or payload.get("policy_sha256") != policy_sha256
        or payload.get("owner") != "EVIDENCE_LANE_INSTALLED_HOOK_RUNTIME"
    ):
        raise InstallationError(
            "The persistent hook kill-switch payload is not inactive and exact."
        )
    return {
        "schema": "evidence-lane.codex-installed-hook-event-isolation.v1",
        "status": "PASS",
        "state": "INACTIVE_KILL_SWITCH_VERIFIED",
        "verified_before_install_activation": True,
        "persistent_kill_switch": True,
        "kill_switch_receipt_path": str(receipt_path),
        "kill_switch_receipt_sha256": _sha256(receipt_path),
        "policy_sha256": policy_sha256,
        "installation_id": installation_id,
        "generation": initialized["generation"],
        "initialization_state": initialized["state"],
        "active_receipt_overwritten": False,
    }


def _verify_recovery_copy_hook_event_isolation(
    plugin_root: Path,
    *,
    data_root: Path,
    primary_hook_isolation: dict[str, Any],
) -> dict[str, Any]:
    """Verify the primary kill switch through recovery bytes without rotating it.

    The local primary and disabled recovery selectors share one durable hook
    control root.  Materializing the disabled copy must therefore consume the
    already-sealed primary kill switch as read-only authority.  Giving the
    recovery selector a new installation id would rotate that shared file and
    invalidate the primary installation receipt used by ``invoke_hook.ps1``.
    """

    module_path = plugin_root / "hooks" / "event_isolation.py"
    policy_path = plugin_root / "hooks" / "event_isolation_policy.json"
    if not module_path.is_file() or not policy_path.is_file():
        raise InstallationError(
            "The installed recovery hook-event isolation runtime is incomplete."
        )
    module_name = (
        "_evidence_lane_recovery_hook_isolation_"
        + hashlib.sha256(str(module_path.resolve()).encode("utf-8")).hexdigest()[:16]
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise InstallationError(
            "The installed recovery hook-event isolation runtime could not be loaded."
        )
    module = importlib.util.module_from_spec(spec)
    receipt_path = data_root.resolve() / "hook-event-isolation" / "KILL_SWITCH.json"
    if not receipt_path.is_file():
        raise InstallationError(
            "The primary persistent hook kill-switch receipt is unavailable."
        )
    before_bytes = receipt_path.read_bytes()
    before_sha256 = hashlib.sha256(before_bytes).hexdigest().upper()
    policy_sha256 = _sha256(policy_path)
    prior_dont_write_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
        _body, payload = module._read_kill_switch(receipt_path)
    except Exception as exc:
        raise InstallationError(
            "The recovery bytes could not verify the primary hook kill switch."
        ) from exc
    finally:
        sys.dont_write_bytecode = prior_dont_write_bytecode
    after_bytes = receipt_path.read_bytes()
    after_sha256 = hashlib.sha256(after_bytes).hexdigest().upper()

    primary_receipt_path = Path(
        str(primary_hook_isolation.get("kill_switch_receipt_path") or "")
    ).resolve()
    primary_generation = primary_hook_isolation.get("generation")
    if (
        primary_hook_isolation.get("status") != "PASS"
        or primary_hook_isolation.get("state") != "INACTIVE_KILL_SWITCH_VERIFIED"
        or primary_hook_isolation.get("verified_before_install_activation") is not True
        or primary_hook_isolation.get("persistent_kill_switch") is not True
        or primary_receipt_path != receipt_path
        or str(primary_hook_isolation.get("kill_switch_receipt_sha256") or "").upper()
        != before_sha256
        or str(primary_hook_isolation.get("policy_sha256") or "").upper()
        != policy_sha256
        or str(primary_hook_isolation.get("installation_id") or "")
        != str(payload.get("installation_id") or "")
        or not isinstance(primary_generation, int)
        or isinstance(primary_generation, bool)
        or primary_generation != payload.get("generation")
        or payload.get("state") != "INACTIVE"
        or payload.get("policy_sha256") != policy_sha256
        or payload.get("owner") != "EVIDENCE_LANE_INSTALLED_HOOK_RUNTIME"
        or before_bytes != after_bytes
        or before_sha256 != after_sha256
    ):
        raise InstallationError(
            "The disabled recovery copy did not preserve the exact primary hook "
            "kill-switch authority."
        )
    return {
        "schema": "evidence-lane.codex-installed-hook-event-isolation.v1",
        "status": "PASS",
        "state": "PRIMARY_INACTIVE_KILL_SWITCH_REUSED_UNCHANGED",
        "verified_before_install_activation": True,
        "persistent_kill_switch": True,
        "kill_switch_receipt_path": str(receipt_path),
        "kill_switch_receipt_sha256": after_sha256,
        "policy_sha256": policy_sha256,
        "installation_id": payload["installation_id"],
        "generation": payload["generation"],
        "initialization_state": "NOT_INVOKED_RECOVERY_READ_ONLY_VERIFICATION",
        "active_receipt_overwritten": False,
        "global_kill_switch_rotated": False,
        "recovery_copy_disabled": True,
        "verified_by_recovery_bytes": True,
        "primary_installation_receipt_bound": True,
        "bytes_unchanged": True,
    }


def _resolve_stable_marketplace_name(
    *,
    requested_name: str,
    two_slot_authority: dict[str, Any] | None,
) -> str:
    """Return the one canonical Git stable name, allowing one legacy migration."""

    if two_slot_authority is None:
        if requested_name != MARKETPLACE_NAME:
            raise InstallationError(
                "A build hash must not create a new stable marketplace identity."
            )
        return MARKETPLACE_NAME

    registry = dict(two_slot_authority.get("registry") or {})
    stable = dict((registry.get("slots") or {}).get("stable-git-main") or {})
    prior_selector = str(stable.get("plugin_selector") or "")
    if not prior_selector.startswith(f"{PLUGIN_NAME}@"):
        raise InstallationError("The two-slot registry has no exact stable selector.")
    if requested_name != MARKETPLACE_NAME:
        raise InstallationError(
            "The stable route must use the canonical GitLane marketplace identity."
        )
    return MARKETPLACE_NAME


def _prepare_in_place_stable_reinstall(
    *,
    executable: Path,
    codex_home: Path,
    plugin_selector: str,
    marketplace_name: str,
    two_slot_authority: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preflight a canonical stable-main update while preserving local testing.

    During the one-time migration the currently working stable route remains
    installed until the canonical Git route has been installed, prewarmed,
    selected exclusively, and hook-trusted. A later refresh removes and re-adds
    that same selector while Codex is closed. The versioned local-testing slot
    remains installed and the retired branch-recovery selector is never treated
    as authority.
    """

    before_plugins = _run_codex(executable, codex_home, ["plugin", "list", "--json"])
    installed = before_plugins.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence_plugins = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    local_testing_selector: str | None = None
    prior_stable_selector: str | None = None
    if two_slot_authority is not None:
        registry = dict(two_slot_authority.get("registry") or {})
        slots = dict(registry.get("slots") or {})
        stable = dict(slots.get("stable-git-main") or {})
        local_testing = dict(slots.get("versioned-local-testing") or {})
        prior_stable_selector = str(stable.get("plugin_selector") or "")
        local_testing_selector = str(local_testing.get("plugin_selector") or "")
        by_selector = {str(row.get("pluginId") or ""): row for row in evidence_plugins}
        enabled_rows = [row for row in evidence_plugins if row.get("enabled") is True]
        stable_missing_recovery = (
            prior_stable_selector not in by_selector
            and set(by_selector) == {local_testing_selector}
            and len(enabled_rows) == 1
            and enabled_rows[0].get("pluginId") == local_testing_selector
        )
        ordinary_safe_state = (
            prior_stable_selector in by_selector
            and local_testing_selector in by_selector
            and len(enabled_rows) == 1
            and all(
                row.get("pluginId") in {prior_stable_selector, local_testing_selector}
                for row in enabled_rows
            )
        )
        if not ordinary_safe_state and not stable_missing_recovery:
            raise InstallationError(
                "The sealed stable-main/local-testing slots are not safe for an "
                "in-place update."
            )

    target_was_installed = any(
        row.get("pluginId") == plugin_selector for row in evidence_plugins
    )
    same_selector_refresh = prior_stable_selector == plugin_selector
    stable_missing_recovery = bool(
        two_slot_authority is not None
        and same_selector_refresh
        and not target_was_installed
        and local_testing_selector is not None
        and {str(row.get("pluginId") or "") for row in evidence_plugins}
        == {local_testing_selector}
    )
    if target_was_installed and not same_selector_refresh:
        raise InstallationError(
            "The canonical Git selector is unexpectedly installed during legacy "
            "stable migration."
        )
    if same_selector_refresh and target_was_installed:
        _run_codex(
            executable,
            codex_home,
            ["plugin", "remove", plugin_selector, "--json"],
        )

    marketplace_list = _run_codex(
        executable,
        codex_home,
        ["plugin", "marketplace", "list", "--json"],
    )
    marketplaces = marketplace_list.get("marketplaces")
    if not isinstance(marketplaces, list):
        raise InstallationError("Codex marketplace list has an invalid shape.")
    marketplace_rows = [dict(row) for row in marketplaces if isinstance(row, dict)]
    target_marketplace_rows = [
        row for row in marketplace_rows if row.get("name") == marketplace_name
    ]
    if len(target_marketplace_rows) > 1:
        raise InstallationError(
            "Codex exposed more than one canonical Git marketplace identity."
        )
    target_marketplace_preexisting = bool(target_marketplace_rows)
    target_marketplace_source_verified = False
    if target_marketplace_preexisting:
        marketplace_source = dict(
            target_marketplace_rows[0].get("marketplaceSource") or {}
        )
        source_value = str(marketplace_source.get("source") or "").lower()
        if (
            marketplace_source.get("sourceType") != "git"
            or MARKETPLACE_SOURCE.lower() not in source_value
        ):
            raise InstallationError(
                "The pre-existing canonical marketplace is not the governed Git source."
            )
        target_marketplace_source_verified = True
    target_marketplace_removed = False
    legacy_selector_migration = (
        bool(prior_stable_selector) and prior_stable_selector != plugin_selector
    )
    if target_marketplace_preexisting and (
        same_selector_refresh or legacy_selector_migration
    ):
        _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "remove", marketplace_name, "--json"],
        )
        target_marketplace_removed = True

    receipt: dict[str, Any] = {
        "schema": "evidence-lane.codex-stable-in-place-update.v1",
        "stable_selector": plugin_selector,
        "stable_marketplace": marketplace_name,
        "prior_stable_selector": prior_stable_selector,
        "target_was_installed": target_was_installed,
        "one_time_legacy_selector_migration": legacy_selector_migration,
        "same_selector_refresh": same_selector_refresh,
        "recovered_after_prior_stable_selector_removal": stable_missing_recovery,
        "stable_removed_for_same_selector_reinstall": (
            same_selector_refresh and target_was_installed
        ),
        "target_marketplace_removed_for_exact_ref_refresh": target_marketplace_removed,
        "target_marketplace_preexisting": target_marketplace_preexisting,
        "target_marketplace_source_verified": target_marketplace_source_verified,
        "local_testing_selector": local_testing_selector,
        "local_testing_removed": False,
        "obsolete_selector_present": False,
        "removed_obsolete_selectors": [],
        "removed_obsolete_marketplaces": [],
        "obsolete_cleanup_deferred_until_new_route_proof": True,
        "new_stable_selector_created": False,
        "generated_cache_written_directly": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_json_bytes(receipt)).hexdigest().upper()
    return receipt


def _cleanup_obsolete_after_new_route_proof(
    *,
    executable: Path,
    codex_home: Path,
    plugin_selector: str,
    local_testing_selector: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove obsolete registrations only after the new route is proven live."""

    before = _run_codex(executable, codex_home, ["plugin", "list", "--json"])
    installed = before.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    by_selector = {str(row.get("pluginId") or ""): row for row in evidence}
    if (
        plugin_selector not in by_selector
        or by_selector[plugin_selector].get("enabled") is not True
        or local_testing_selector not in by_selector
        or by_selector[local_testing_selector].get("enabled") is not False
        or any(
            row.get("enabled") is not False
            for row in evidence
            if row.get("pluginId") != plugin_selector
        )
    ):
        raise InstallationError(
            "The canonical stable route is not exclusively proven before cleanup."
        )

    removed_selectors: list[str] = []
    for selector in sorted(
        set(by_selector) - {plugin_selector, local_testing_selector}
    ):
        _run_codex(executable, codex_home, ["plugin", "remove", selector, "--json"])
        removed_selectors.append(selector)

    local_testing_marketplace = local_testing_selector.split("@", 1)[1]
    marketplace_list = _run_codex(
        executable, codex_home, ["plugin", "marketplace", "list", "--json"]
    )
    marketplaces = marketplace_list.get("marketplaces")
    if not isinstance(marketplaces, list):
        raise InstallationError("Codex marketplace list has an invalid shape.")
    removed_marketplaces: list[str] = []
    for row in marketplaces:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if not name.startswith("evidence-lane-") or name in {
            MARKETPLACE_NAME,
            local_testing_marketplace,
        }:
            continue
        _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "remove", name, "--json"],
        )
        removed_marketplaces.append(name)

    after = _run_codex(executable, codex_home, ["plugin", "list", "--json"])
    after_installed = after.get("installed")
    if not isinstance(after_installed, list):
        raise InstallationError("Codex plugin list did not expose final plugins.")
    final_evidence = [
        dict(row)
        for row in after_installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    final_by_selector = {str(row.get("pluginId") or ""): row for row in final_evidence}
    if (
        set(final_by_selector) != {plugin_selector, local_testing_selector}
        or final_by_selector[plugin_selector].get("enabled") is not True
        or final_by_selector[local_testing_selector].get("enabled") is not False
    ):
        raise InstallationError("Post-proof cleanup did not leave exactly two slots.")
    receipt = {
        "schema": "evidence-lane.codex-post-proof-slot-cleanup.v1",
        "status": "PASS",
        "canonical_stable_selector": plugin_selector,
        "versioned_local_testing_selector": local_testing_selector,
        "removed_obsolete_selectors": removed_selectors,
        "removed_obsolete_marketplaces": removed_marketplaces,
        "exact_installed_slot_count": 2,
        "enabled_slot_count": 1,
        "versioned_local_testing_enabled": False,
        "generated_cache_deleted_directly": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_json_bytes(receipt)).hexdigest().upper()
    return receipt, after


def _read_codex_user_config_version(
    *,
    send: Any,
    wait_for: Any,
    config_path: Path,
    request_id: int,
) -> str:
    """Return Codex's semantic version for the exact writable user layer."""

    exact_config = config_path.resolve()
    send(
        {
            "method": "config/read",
            "id": request_id,
            "params": {"cwd": None, "includeLayers": True},
        }
    )
    result = dict(wait_for(request_id).get("result") or {})
    matches: list[str] = []
    for raw_layer in result.get("layers") or []:
        if not isinstance(raw_layer, dict):
            continue
        name = raw_layer.get("name")
        if not isinstance(name, dict) or name.get("type") != "user":
            continue
        raw_file = str(name.get("file") or "")
        if not raw_file:
            continue
        if os.path.normcase(str(Path(raw_file).resolve())) != os.path.normcase(
            str(exact_config)
        ):
            continue
        version = str(raw_layer.get("version") or "")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", version) is None:
            raise InstallationError(
                "Codex returned an invalid writable user-config version."
            )
        matches.append(version)
    if len(matches) != 1:
        raise InstallationError(
            "Codex did not expose one exact writable user-config layer."
        )
    return matches[0]


def _batch_write_with_one_config_refresh(
    *,
    send: Any,
    wait_for: Any,
    config_path: Path,
    expected_raw_sha256: str,
    edits: list[dict[str, Any]],
    request_ids: tuple[tuple[int, int], tuple[int, int]],
) -> dict[str, Any]:
    """CAS-write after config/read, retrying one version conflict at most once."""

    expected_raw = str(expected_raw_sha256 or "").upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", expected_raw) is None
        or _sha256(config_path) != expected_raw
    ):
        raise InstallationError("The config write raw-byte baseline drifted.")
    versions: list[str] = []
    for attempt_index, (read_id, write_id) in enumerate(request_ids):
        if _sha256(config_path) != expected_raw:
            raise InstallationError(
                "The config changed before the bounded version refresh completed."
            )
        expected_version = _read_codex_user_config_version(
            send=send,
            wait_for=wait_for,
            config_path=config_path,
            request_id=read_id,
        )
        versions.append(expected_version)
        if _sha256(config_path) != expected_raw:
            raise InstallationError(
                "The config changed after its version was read and before CAS write."
            )
        send(
            {
                "method": "config/batchWrite",
                "id": write_id,
                "params": {
                    "edits": edits,
                    "filePath": None,
                    "expectedVersion": expected_version,
                    "reloadUserConfig": True,
                },
            }
        )
        reply = wait_for(write_id, allow_error=True)
        error = reply.get("error")
        if error is not None:
            error_data = (
                dict(error.get("data") or {}) if isinstance(error, dict) else {}
            )
            conflict = (
                error_data.get("config_write_error_code") == "configVersionConflict"
            )
            if conflict and attempt_index == 0:
                if _sha256(config_path) != expected_raw:
                    raise InstallationError(
                        "Codex reported a config-version conflict after raw bytes changed."
                    )
                continue
            raise InstallationError(
                "Codex config write failed: " + json.dumps(error, sort_keys=True)
            )
        result = dict(reply.get("result") or {})
        written_version = str(result.get("version") or "")
        if (
            result.get("status") != "ok"
            or re.fullmatch(r"sha256:[0-9a-f]{64}", written_version) is None
        ):
            raise InstallationError("Codex did not seal the bounded config write.")
        return {
            "reply": reply,
            "result": result,
            "config_version": written_version,
            "cas_expected_version": expected_version,
            "config_read_count": attempt_index + 1,
            "config_write_attempt_count": attempt_index + 1,
            "config_conflict_retry_count": attempt_index,
            "config_versions_read": versions,
        }
    raise InstallationError(
        "Codex config write exhausted its single version-conflict retry."
    )


def _set_native_hook_event_states(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    hook_cwd: Path,
    plugin_selector: str,
    desired_event_states: dict[str, bool],
    verified_event_receipts: dict[str, str],
    changed_by: str,
) -> dict[str, Any]:
    """CAS-update only exact installed hook events through Codex native APIs."""

    exact_events = set(desired_event_states)
    if (
        not exact_events
        or not exact_events.issubset(EXPECTED_CODEX_HOST_HOOK_EVENTS)
        or any(not isinstance(value, bool) for value in desired_event_states.values())
        or not str(changed_by or "").strip()
    ):
        raise InstallationError("Native hook event control input is invalid.")
    enabled_events = {
        event for event, enabled in desired_event_states.items() if enabled
    }
    if set(verified_event_receipts) != enabled_events or any(
        re.fullmatch(r"[A-F0-9]{64}", str(value or "").upper()) is None
        for value in verified_event_receipts.values()
    ):
        raise InstallationError(
            "Every hook enabled by native control requires one exact installed-event proof."
        )
    exact_codex_home = codex_home.resolve()
    exact_data_root = data_root.resolve()
    resolved_cwd = hook_cwd.resolve()
    if not resolved_cwd.is_dir() or "@" not in plugin_selector:
        raise InstallationError("Native hook event control binding is invalid.")

    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(exact_codex_home)
    process = subprocess.Popen(
        [str(executable.resolve()), "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=environment,
        creationflags=_windows_hidden_creationflags(),
    )
    stdin = process.stdin
    stdout = process.stdout
    stderr = process.stderr
    if stdin is None or stdout is None or stderr is None:
        process.kill()
        raise InstallationError("Codex app-server stdio was not available.")
    responses: queue.Queue[dict[str, Any]] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        for line in stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                responses.put(payload)

    def read_stderr() -> None:
        for line in stderr:
            stderr_lines.append(line.rstrip())

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def send(payload: dict[str, Any]) -> None:
        try:
            stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise InstallationError("Codex app-server closed unexpectedly.") from exc

    def wait_for(
        request_id: int,
        *,
        timeout: float = 20.0,
        allow_error: bool = False,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                payload = responses.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if payload.get("id") == request_id:
                if "error" in payload and not allow_error:
                    raise InstallationError(
                        "Codex app-server request failed: "
                        + json.dumps(payload["error"], sort_keys=True)
                    )
                return payload
        detail = " | ".join(stderr_lines[-3:])
        raise InstallationError(
            "Codex app-server did not return the native hook control receipt"
            + (f": {detail}" if detail else ".")
        )

    def read_inventory(request_id: int) -> list[dict[str, Any]]:
        send(
            {
                "method": "hooks/list",
                "id": request_id,
                "params": {"cwds": [str(resolved_cwd)]},
            }
        )
        data = dict(wait_for(request_id).get("result") or {}).get("data")
        if not isinstance(data, list):
            raise InstallationError("Codex hooks/list returned an invalid shape.")
        entries = [
            dict(row)
            for row in data
            if isinstance(row, dict)
            and os.path.normcase(str(Path(str(row.get("cwd") or "")).resolve()))
            == os.path.normcase(str(resolved_cwd))
        ]
        if len(entries) != 1 or entries[0].get("errors"):
            raise InstallationError("Codex did not return one clean hook workspace.")
        hooks = [
            dict(row)
            for row in entries[0].get("hooks") or []
            if isinstance(row, dict) and row.get("pluginId") == plugin_selector
        ]
        event_names = {str(row.get("eventName") or "") for row in hooks}
        if (
            len(hooks) < len(EXPECTED_CODEX_HOST_HOOK_EVENTS)
            or event_names != EXPECTED_CODEX_HOST_HOOK_EVENTS
            or len({str(row.get("key") or "") for row in hooks}) != len(hooks)
            or any(
                re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("currentHash") or ""))
                is None
                or not str(row.get("key") or "").startswith(f"{plugin_selector}:")
                for row in hooks
            )
        ):
            raise InstallationError("The installed hook inventory drifted.")
        event_order = {
            event_name: ordinal
            for ordinal, event_name in enumerate(
                EXPECTED_CODEX_HOST_HOOK_EVENT_ORDER, start=1
            )
        }
        return sorted(
            hooks,
            key=lambda row: (
                event_order[str(row["eventName"])],
                str(row["key"]),
            ),
        )

    def event_states(rows: list[dict[str, Any]]) -> dict[str, bool]:
        grouped: dict[str, set[bool]] = {
            event_name: set() for event_name in EXPECTED_CODEX_HOST_HOOK_EVENT_ORDER
        }
        for row in rows:
            grouped[str(row["eventName"])].add(bool(row.get("enabled")))
        if any(len(values) != 1 for values in grouped.values()):
            raise InstallationError(
                "All handler actions under one hook event must share its enabled state."
            )
        return {event: next(iter(values)) for event, values in grouped.items()}

    try:
        send(
            {
                "method": "initialize",
                "id": 1000,
                "params": {
                    "clientInfo": {
                        "name": "evidence_lane_native_hook_control",
                        "title": "Evidence Lane Native Hook Control",
                        "version": BASE_RELEASE,
                    }
                },
            }
        )
        wait_for(1000)
        send({"method": "initialized", "params": {}})
        before = read_inventory(1001)
        before_states = event_states(before)
        trust_value: dict[str, dict[str, Any]] = {}
        for row in before:
            event_name = str(row["eventName"])
            desired = desired_event_states.get(event_name, before_states[event_name])
            trust_value[str(row["key"])] = {
                "trusted_hash": str(row["currentHash"]),
                "enabled": desired,
            }
        config_path = exact_codex_home / "config.toml"
        before_config_sha256 = _sha256(config_path)
        write = _batch_write_with_one_config_refresh(
            send=send,
            wait_for=wait_for,
            config_path=config_path,
            expected_raw_sha256=before_config_sha256,
            edits=[
                {
                    "keyPath": "hooks.state",
                    "value": trust_value,
                    "mergeStrategy": "upsert",
                }
            ],
            request_ids=((1010, 1011), (1012, 1013)),
        )
        after = read_inventory(1020)
        after_states = event_states(after)
        expected_after = {
            event: desired_event_states.get(event, before_states[event])
            for event in EXPECTED_CODEX_HOST_HOOK_EVENTS
        }
        if after_states != expected_after or any(
            str(row.get("trustStatus") or "") != "trusted" for row in after
        ):
            raise InstallationError(
                "The native hook event state or trust readback did not persist."
            )
        body = {
            "schema": "evidence-lane.native-hook-event-control.v1",
            "status": "PASS",
            "plugin_selector": plugin_selector,
            "workspace_sha256": hashlib.sha256(
                os.path.normcase(str(resolved_cwd)).encode("utf-8")
            )
            .hexdigest()
            .upper(),
            "before_states": before_states,
            "requested_states": dict(sorted(desired_event_states.items())),
            "after_states": after_states,
            "registered_event_count": len(EXPECTED_CODEX_HOST_HOOK_EVENT_ORDER),
            "handler_action_count": len(after),
            "hook_count_semantics": "REGISTERED_EVENT_TYPE_COUNT",
            "handler_action_count_semantics": ("TOTAL_NESTED_HANDLER_ACTION_COUNT"),
            "event_action_inventory": [
                {
                    "hook_number": event_ordinal,
                    "event_name": event_name,
                    "display_number": f"Hook {event_ordinal}",
                    "action_count": len(
                        [row for row in after if row["eventName"] == event_name]
                    ),
                    "actions": [
                        {
                            "action_number": f"{event_ordinal}.{action_ordinal}",
                            "event_action_ordinal": action_ordinal,
                            "hook_key_sha256": hashlib.sha256(
                                str(row["key"]).encode("utf-8")
                            )
                            .hexdigest()
                            .upper(),
                            "current_hash": row["currentHash"],
                        }
                        for action_ordinal, row in enumerate(
                            [row for row in after if row["eventName"] == event_name],
                            start=1,
                        )
                    ],
                }
                for event_ordinal, event_name in enumerate(
                    EXPECTED_CODEX_HOST_HOOK_EVENT_ORDER, start=1
                )
            ],
            "verified_event_receipts": {
                key: str(value).upper()
                for key, value in sorted(verified_event_receipts.items())
            },
            "changed_by": str(changed_by).strip(),
            "supported_codex_api": [
                "hooks/list",
                "config/read",
                "config/batchWrite",
            ],
            "config_version": write["config_version"],
            "config_conflict_retry_count": write["config_conflict_retry_count"],
            "direct_config_file_write": False,
            "windows_ui_control_used": False,
            "unrelated_plugin_state_mutated": False,
            "candidate_created": False,
            "pointer_moved": False,
            "hil_inferred": False,
        }
        body["event_action_inventory_sha256"] = (
            hashlib.sha256(_json_bytes(body["event_action_inventory"]))
            .hexdigest()
            .upper()
        )
        receipt = {
            **body,
            "receipt_sha256": hashlib.sha256(_json_bytes(body)).hexdigest().upper(),
        }
        receipt_path = (
            exact_data_root
            / "installations"
            / "codex-v300"
            / "hook-event-control"
            / f"HOOK_EVENT_CONTROL_{receipt['receipt_sha256'][:16]}.json"
        )
        _write_atomic(receipt_path, _json_bytes(receipt))
        return {
            **receipt,
            "receipt_path": str(receipt_path),
            "receipt_file_sha256": _sha256(receipt_path),
        }
    finally:
        try:
            stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def _read_codex_plugin_hook_inventory(
    *,
    send: Any,
    wait_for: Any,
    codex_home: Path,
    plugin_selector: str,
    expected_enabled: bool,
    request_id: int,
) -> list[dict[str, str]]:
    """Read an installed plugin's hooks even when its selector is disabled."""

    if "@" not in plugin_selector:
        raise InstallationError("The plugin selector has no marketplace identity.")
    marketplace_name = plugin_selector.split("@", 1)[1]
    marketplace_file = (
        codex_home.resolve()
        / "local-marketplaces"
        / marketplace_name
        / ".agents"
        / "plugins"
        / "marketplace.json"
    )
    if not marketplace_file.is_file():
        raise InstallationError(
            "The exact local marketplace manifest is unavailable for plugin review."
        )
    send(
        {
            "method": "plugin/read",
            "id": request_id,
            "params": {
                "pluginName": PLUGIN_NAME,
                "marketplacePath": str(marketplace_file),
                "remoteMarketplaceName": None,
            },
        }
    )
    plugin = dict(dict(wait_for(request_id).get("result") or {}).get("plugin") or {})
    summary = dict(plugin.get("summary") or {})
    raw_hooks = plugin.get("hooks")
    if (
        summary.get("id") != plugin_selector
        or summary.get("installed") is not True
        or summary.get("enabled") is not expected_enabled
        or plugin.get("marketplaceName") != marketplace_name
        or not isinstance(raw_hooks, list)
    ):
        raise InstallationError("Codex plugin/read returned a mismatched selector.")
    hooks = [dict(row) for row in raw_hooks if isinstance(row, dict)]
    events = {str(row.get("eventName") or "") for row in hooks}
    keys = [str(row.get("key") or "") for row in hooks]
    if (
        len(hooks) < len(EXPECTED_CODEX_HOST_HOOK_EVENTS)
        or events != EXPECTED_CODEX_HOST_HOOK_EVENTS
        or len(keys) != len(set(keys))
        or any(not key.startswith(f"{plugin_selector}:") for key in keys)
    ):
        raise InstallationError("Codex plugin/read returned a drifted hook inventory.")
    return sorted(hooks, key=lambda row: str(row["eventName"]))


def _write_self_sealed_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    """Write one canonical self-sealed current-route receipt."""

    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    sealed = {
        **body,
        "receipt_sha256": hashlib.sha256(_json_bytes(body)).hexdigest().upper(),
    }
    _write_atomic(path, _json_bytes(sealed))
    return sealed


def _ordered_json_sha256(value: Any) -> str:
    """Match the v1 two-slot registry's preserved-property-order seal."""

    return (
        hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
        .upper()
    )


def _load_two_slot_update_authority(
    *,
    data_root: Path,
    comparison_baseline: dict[str, Any] | None,
) -> dict[str, Any] | None:
    registry_path = (
        data_root
        / "installations"
        / "codex-v300"
        / "two-slot-main-local"
        / "CODEX_TWO_SLOT_MAIN_LOCAL_REGISTRY.json"
    )
    if not registry_path.is_file():
        return None
    if comparison_baseline is None:
        raise InstallationError(
            "The materialized main/local registry requires the exact prior stable baseline."
        )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict):
        raise InstallationError("The main/local registry is not a JSON object.")
    receipt_sha256 = str(registry.get("receipt_sha256") or "").upper()
    registry_core = {
        key: value for key, value in registry.items() if key != "receipt_sha256"
    }
    slots = dict(registry.get("slots") or {})
    stable = dict(slots.get("stable-git-main") or {})
    local_testing = dict(slots.get("versioned-local-testing") or {})
    stable_selector = str(stable.get("plugin_selector") or "")
    local_testing_selector = str(local_testing.get("plugin_selector") or "")
    enabled_slots = [
        role
        for role, row in slots.items()
        if isinstance(row, dict) and row.get("enabled") is True
    ]
    if (
        registry.get("schema") != TWO_SLOT_REGISTRY_SCHEMA
        or registry.get("status") != "PASS"
        or registry.get("exact_live_slot_count") != 2
        or registry.get("max_enabled_plugin_count") != 1
        or set(slots) != {"stable-git-main", "versioned-local-testing"}
        or stable.get("slot_role") != "stable-git-main"
        or stable_selector != TWO_SLOT_SELECTORS["stable-git-main"]
        or stable.get("byte_frozen") is not True
        or local_testing.get("slot_role") != "versioned-local-testing"
        or local_testing_selector != TWO_SLOT_SELECTORS["versioned-local-testing"]
        or local_testing.get("byte_frozen") is not False
        or len(enabled_slots) != 1
        or registry.get("active_slot") != enabled_slots[0]
        or registry.get("failure_target_slot") != "stable-git-main"
        or registry.get("local_failure_targets_verified_main_only") is not True
        or registry.get("obsolete_selector_present") is not False
        or registry.get("pre_3_0_fallback_allowed") is not False
        or receipt_sha256
        != hashlib.sha256(_json_bytes(registry_core)).hexdigest().upper()
    ):
        raise InstallationError(
            "The exact stable-main/local-testing registry is not eligible for "
            "stable-main advancement."
        )
    return {
        "path": registry_path,
        "before_file_sha256": _sha256(registry_path),
        "registry": registry,
        "prior_stable_selector": stable_selector,
        "local_testing_selector": local_testing_selector,
        "local_testing_identity_sha256": hashlib.sha256(
            _json_bytes(
                {
                    key: value
                    for key, value in local_testing.items()
                    if key not in {"enabled", "native_mcp_enabled"}
                }
            )
        )
        .hexdigest()
        .upper(),
    }


def _bootstrap_two_slot_update_authority(
    *,
    data_root: Path,
    codex_home: Path,
    plugin_list: dict[str, Any],
    comparison_baseline: dict[str, Any],
    comparison_surface: dict[str, Any],
) -> dict[str, Any]:
    """Seal the first durable two-slot registry from exact live Codex readback."""

    registry_path = (
        data_root
        / "installations"
        / "codex-v300"
        / "two-slot-main-local"
        / "CODEX_TWO_SLOT_MAIN_LOCAL_REGISTRY.json"
    )
    if registry_path.exists():
        return {
            "status": "NOT_REQUIRED_ALREADY_MATERIALIZED",
            "registry_path": str(registry_path),
            "registry_file_sha256": _sha256(registry_path),
        }
    installed = plugin_list.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence_plugins = {
        str(row.get("pluginId") or ""): dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    }
    stable_selector = TWO_SLOT_SELECTORS["stable-git-main"]
    local_selector = TWO_SLOT_SELECTORS["versioned-local-testing"]
    if set(evidence_plugins) != {stable_selector, local_selector}:
        raise InstallationError(
            "First stable bootstrap requires exactly the stable-main and local-testing selectors."
        )
    stable_row = evidence_plugins[stable_selector]
    local_row = evidence_plugins[local_selector]
    stable_source = dict(stable_row.get("source") or {})
    local_source = dict(local_row.get("source") or {})
    stable_marketplace_source = dict(stable_row.get("marketplaceSource") or {})
    local_marketplace_source = dict(local_row.get("marketplaceSource") or {})
    stable_path = Path(str(stable_source.get("path") or "")).resolve()
    local_path = Path(str(local_source.get("path") or "")).resolve()
    stable_version = str(stable_row.get("version") or "")
    local_version = str(local_row.get("version") or "")
    if (
        stable_row.get("enabled") is not False
        or local_row.get("enabled") is not True
        or stable_marketplace_source.get("sourceType") != "git"
        or local_marketplace_source.get("sourceType") != "local"
        or not stable_path.is_dir()
        or not local_path.is_dir()
        or not stable_version.startswith("3.0.0+codex.")
        or not local_version.startswith("3.0.0+codex.")
        or comparison_baseline.get("baseline_role")
        != "EXACT_PRIOR_HOST_ACTIVE_LOCAL_TESTING_INSTALLATION"
        or comparison_baseline.get("plugin_version") != local_version
        or comparison_baseline.get("surface_inventory_sha256")
        != comparison_surface.get("surface_inventory_sha256")
    ):
        raise InstallationError(
            "The live selectors and sealed active-local baseline cannot bootstrap two-slot authority."
        )

    def slot(
        *,
        role: str,
        selector: str,
        row: dict[str, Any],
        path: Path,
        source_type: str,
        byte_frozen: bool,
        enabled: bool,
        update_gate: str,
    ) -> dict[str, Any]:
        return {
            "slot_role": role,
            "plugin_selector": selector,
            "plugin_version": str(row["version"]),
            "marketplace_name": str(row["marketplaceName"]),
            "installed_path_sha256": hashlib.sha256(
                os.path.normcase(str(path)).encode("utf-8")
            )
            .hexdigest()
            .upper(),
            "marketplace_source_type": source_type,
            "byte_frozen": byte_frozen,
            "enabled": enabled,
            "native_mcp_enabled": enabled,
            "update_gate": update_gate,
        }

    registry: dict[str, Any] = {
        "schema": TWO_SLOT_REGISTRY_SCHEMA,
        "status": "PASS",
        "active_slot": "versioned-local-testing",
        "active_selector": local_selector,
        "exact_live_slot_count": 2,
        "max_enabled_plugin_count": 1,
        "failure_target_slot": "stable-git-main",
        "local_failure_targets_verified_main_only": True,
        "obsolete_selector_present": False,
        "pre_3_0_fallback_allowed": False,
        "slots": {
            "stable-git-main": slot(
                role="stable-git-main",
                selector=stable_selector,
                row=stable_row,
                path=stable_path,
                source_type="git",
                byte_frozen=True,
                enabled=False,
                update_gate="GOVERNED_VERIFIED_MAIN_FAST_FORWARD",
            ),
            "versioned-local-testing": slot(
                role="versioned-local-testing",
                selector=local_selector,
                row=local_row,
                path=local_path,
                source_type="local",
                byte_frozen=False,
                enabled=True,
                update_gate="FRESH_VERSIONED_LOCAL_PACKAGE",
            ),
        },
        "bootstrap_proof": {
            "source": "CODEX_PLUGIN_LIST_AND_SEALED_ACTIVE_LOCAL_RECEIPT",
            "codex_home": str(codex_home.resolve()),
            "plugin_list_sha256": hashlib.sha256(_json_bytes(plugin_list))
            .hexdigest()
            .upper(),
            "comparison_receipt_sha256": comparison_baseline[
                "installation_receipt_sha256"
            ],
            "comparison_surface_inventory_sha256": comparison_surface[
                "surface_inventory_sha256"
            ],
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    }
    registry["receipt_sha256"] = (
        hashlib.sha256(_json_bytes(registry)).hexdigest().upper()
    )
    _write_atomic(registry_path, _json_bytes(registry))
    return {
        "schema": "evidence-lane.codex-two-slot-main-local-bootstrap.v1",
        "status": "PASS",
        "registry_path": str(registry_path),
        "registry_file_sha256": _sha256(registry_path),
        "registry_receipt_sha256": registry["receipt_sha256"],
        "stable_selector": stable_selector,
        "local_testing_selector": local_selector,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }


def _advance_two_slot_stable_registry(
    *,
    authority: dict[str, Any] | None,
    plugin_selector: str,
    plugin_version: str,
    installed_path: Path,
    marketplace_root: Path,
    install_receipt: Path,
    archive_sha256: str,
    source_manifest_sha256: str,
    codex_home: Path,
    data_root: Path,
    plugin_list: dict[str, Any],
) -> dict[str, Any]:
    if authority is None:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "POST_FUSE_TWO_SLOT_REGISTRY_NOT_MATERIALIZED",
        }
    installed = plugin_list.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence_plugins = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    enabled = [row for row in evidence_plugins if row.get("enabled") is True]
    registry = dict(authority["registry"])
    slots = dict(registry["slots"])
    local_testing = dict(slots["versioned-local-testing"])
    local_testing_selector = str(local_testing.get("plugin_selector") or "")
    prior_stable_selector = str(authority.get("prior_stable_selector") or "")
    by_selector = {str(row.get("pluginId") or ""): row for row in evidence_plugins}
    if (
        plugin_selector != prior_stable_selector
        or plugin_selector != TWO_SLOT_SELECTORS["stable-git-main"]
        or local_testing_selector != TWO_SLOT_SELECTORS["versioned-local-testing"]
        or set(by_selector) != {plugin_selector, local_testing_selector}
        or len(enabled) != 1
        or enabled[0].get("pluginId") != plugin_selector
        or plugin_selector not in by_selector
        or local_testing_selector not in by_selector
        or by_selector[local_testing_selector].get("enabled") is not False
        or any(
            row.get("enabled") is not False
            for row in evidence_plugins
            if row.get("pluginId") != plugin_selector
        )
    ):
        raise InstallationError(
            "Codex does not expose one stable-main enabled and one local-testing "
            "selector disabled."
        )
    config_path = codex_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    plugin_config = dict(config.get("plugins") or {})
    for selector, settings in plugin_config.items():
        if not selector.startswith(f"{PLUGIN_NAME}@"):
            continue
        expected = selector == plugin_selector
        mcp = dict(settings.get("mcp_servers") or {}).get("evidence-lane")
        if (
            bool(settings.get("enabled")) is not expected
            or not isinstance(mcp, dict)
            or bool(mcp.get("enabled")) is not expected
        ):
            raise InstallationError(
                "The plugin and native MCP activation are not exactly exclusive."
            )
    expected_cache = (
        codex_home
        / "plugins"
        / "cache"
        / plugin_selector.split("@", 1)[1]
        / PLUGIN_NAME
    )
    if (
        not installed_path.is_dir()
        or not _inside(installed_path, expected_cache)
        or not install_receipt.is_file()
        or re.fullmatch(r"[A-F0-9]{64}", source_manifest_sha256) is None
    ):
        raise InstallationError("The new stable slot authority is incomplete.")
    marketplace_catalog = marketplace_root / ".agents" / "plugins" / "marketplace.json"
    plugin_manifest = installed_path / ".codex-plugin" / "plugin.json"
    if not marketplace_catalog.is_file() or not plugin_manifest.is_file():
        raise InstallationError("The new stable marketplace identity is incomplete.")

    stable = dict(slots["stable-git-main"])
    stable.update(
        {
            "byte_frozen": True,
            "cache_authority_manifest_sha256": source_manifest_sha256,
            "installed_path_sha256": hashlib.sha256(
                os.path.normcase(str(installed_path)).encode("utf-8")
            )
            .hexdigest()
            .upper(),
            "enabled": True,
            "install_receipt": str(install_receipt),
            "install_receipt_sha256": _sha256(install_receipt),
            "marketplace_catalog_sha256": _sha256(marketplace_catalog),
            "marketplace_root": str(marketplace_root),
            "native_mcp_enabled": True,
            "package_sha256": archive_sha256,
            "plugin_manifest_sha256": _sha256(plugin_manifest),
            "plugin_selector": plugin_selector,
            "plugin_version": plugin_version,
            "slot_role": "stable-git-main",
            "update_gate": "GOVERNED_VERIFIED_MAIN_FAST_FORWARD",
        }
    )
    slots["stable-git-main"] = stable
    slots["versioned-local-testing"] = local_testing
    registry["slots"] = slots
    registry["active_slot"] = "stable-git-main"
    registry["active_selector"] = plugin_selector
    registry["live_registered_selectors"] = sorted(by_selector)
    registry["exact_live_slot_count"] = 2
    registry["obsolete_selector_present"] = False
    registry["config_sha256"] = _sha256(config_path)
    registry["activation_proof"] = {
        "command": "codex plugin list --json",
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path),
        "local_testing_native_mcp_enabled": False,
        "local_testing_plugin_enabled": False,
        "plugin_list_json_utf8_sha256": hashlib.sha256(_json_bytes(plugin_list))
        .hexdigest()
        .upper(),
        "plugin_list_json_canonicalized": True,
        "stable_native_mcp_enabled": True,
        "stable_plugin_enabled": True,
    }
    local_testing_identity_sha256 = (
        hashlib.sha256(
            _json_bytes(
                {
                    key: value
                    for key, value in local_testing.items()
                    if key not in {"enabled", "native_mcp_enabled"}
                }
            )
        )
        .hexdigest()
        .upper()
    )
    if local_testing_identity_sha256 != authority["local_testing_identity_sha256"]:
        raise InstallationError("The local-testing slot identity changed.")
    local_testing["enabled"] = False
    local_testing["native_mcp_enabled"] = False
    slots["versioned-local-testing"] = local_testing
    registry["slots"] = slots
    registry.pop("receipt_sha256", None)
    registry["receipt_sha256"] = (
        hashlib.sha256(_json_bytes(registry)).hexdigest().upper()
    )
    registry_path = Path(authority["path"])
    _write_atomic(
        registry_path,
        (json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    after_file_sha256 = _sha256(registry_path)
    update = {
        "schema": "evidence-lane.codex-two-slot-main-local-stable-update.v1",
        "status": "PASS",
        "before_registry_file_sha256": authority["before_file_sha256"],
        "after_registry_file_sha256": after_file_sha256,
        "registry_receipt_sha256": registry["receipt_sha256"],
        "prior_stable_selector": authority["prior_stable_selector"],
        "current_stable_selector": plugin_selector,
        "stable_selector_reused": True,
        "stable_selector_migrated_to_canonical_git": False,
        "same_stable_main_selector_required": True,
        "new_stable_selector_created": False,
        "current_stable_install_receipt_sha256": _sha256(install_receipt),
        "local_testing_selector": local_testing_selector,
        "local_testing_identity_sha256": local_testing_identity_sha256,
        "local_testing_enabled": False,
        "obsolete_selector_present": False,
        "enabled_plugin_count": 1,
        "registered_plugin_count": 2,
        "active_tunnel_count": 0,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_invoked": False,
    }
    update["receipt_sha256"] = hashlib.sha256(_json_bytes(update)).hexdigest().upper()
    update_path = (
        data_root
        / "installations"
        / "codex-v300"
        / "two-slot-main-local"
        / f"STABLE_MAIN_REGISTRY_UPDATE_{archive_sha256[:16]}.json"
    )
    _write_atomic(update_path, _json_bytes(update))
    return {
        **update,
        "registry_path": str(registry_path),
        "receipt_path": str(update_path),
        "receipt_file_sha256": _sha256(update_path),
    }


def _versioned_local_tunnel_identity(
    *, plugin_version: str, plugin_root: Path, data_root: Path
) -> dict[str, str]:
    if not plugin_version.startswith(f"{BASE_RELEASE}+codex."):
        raise InstallationError(
            "Local tunnel activation requires the exact cachebuster package version."
        )
    release_token = "v" + BASE_RELEASE.replace(".", "")
    tunnel_manifest_path = plugin_root / "tunnel" / "tunnel-manifest.v1.json"
    try:
        tunnel_manifest = json.loads(tunnel_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError("The tunnel compatibility manifest is unavailable.") from exc
    compatibility_sha256 = str(
        tunnel_manifest.get("tunnel_compatibility_sha256") or ""
    )
    if (
        tunnel_manifest.get("schema") != "evidence-lane.installed-tunnel-surface.v1"
        or tunnel_manifest.get("status") != "PASS"
        or tunnel_manifest.get("tunnel_compatibility_schema")
        != "evidence-lane.tunnel-capability-compatibility.v1"
        or re.fullmatch(r"[A-F0-9]{64}", compatibility_sha256) is None
    ):
        raise InstallationError("The tunnel compatibility identity is invalid.")
    plugin_digest = hashlib.sha256(plugin_version.encode("utf-8")).hexdigest()[:12]
    compatibility_digest = compatibility_sha256[:12].casefold()
    token = f"{release_token}-versioned-local-testing-abi-{compatibility_digest}"
    prefix = "evidence_lane_" + token.replace("-", "_")
    return {
        "release_token": release_token,
        "plugin_version_digest": plugin_digest,
        "tunnel_compatibility_sha256": compatibility_sha256,
        "tunnel_compatibility_digest": compatibility_digest,
        "tunnel_version_token": token,
        "runtime_root": str((data_root / f"tunnel-runtime-{token}").resolve()),
        "task_name": f"EvidenceLane-Tunnel-{token}",
        "profile_name": f"{prefix}_transport",
    }


def _resolve_local_tunnel_bootstrap_material(
    *, data_root: Path, precreated_tunnel_id: str | None
) -> dict[str, Any]:
    explicit = str(precreated_tunnel_id or "").strip()
    if explicit and re.fullmatch(r"tunnel_[A-Za-z0-9]+", explicit) is None:
        raise InstallationError("The precreated tunnel ID is malformed.")
    candidates: list[dict[str, Any]] = []
    tunnel_ids: set[str] = set()
    for marker_path in sorted(
        data_root.glob("tunnel-runtime-*/evidence-lane-tunnel-installation.json")
    ):
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        tunnel_id = str(marker.get("tunnel_id") or "")
        license_path = Path(str(marker.get("tunnel_client_license") or ""))
        license_sha256 = str(marker.get("tunnel_client_license_sha256") or "").upper()
        client_path = Path(str(marker.get("stable_client") or ""))
        client_sha256 = str(marker.get("stable_client_sha256") or "").upper()
        if (
            re.fullmatch(r"tunnel_[A-Za-z0-9]+", tunnel_id) is None
            or re.fullmatch(r"[A-F0-9]{64}", license_sha256) is None
            or not license_path.is_file()
            or _sha256(license_path) != license_sha256
            or re.fullmatch(r"[A-F0-9]{64}", client_sha256) is None
            or not client_path.is_file()
            or _sha256(client_path) != client_sha256
        ):
            continue
        tunnel_ids.add(tunnel_id)
        candidates.append(
            {
                "marker_path": str(marker_path.resolve()),
                "tunnel_id": tunnel_id,
                "license_path": str(license_path.resolve()),
                "license_sha256": license_sha256,
                "client_path": str(client_path.resolve()),
                "client_sha256": client_sha256,
            }
        )
    if explicit:
        tunnel_id = explicit
        tunnel_id_source = "EXPLICIT_PRECREATED_TUNNEL_ID"
    elif len(tunnel_ids) == 1:
        tunnel_id = next(iter(tunnel_ids))
        tunnel_id_source = "REUSED_PRIOR_PRECREATED_TUNNEL_ID"
    else:
        raise InstallationError(
            "Local tunnel activation requires one reusable precreated tunnel ID or "
            "an explicitly supplied precreated tunnel ID; remote CRUD is not authorized."
        )
    if not candidates:
        raise InstallationError(
            "The pinned tunnel client and license require one verified retained local source."
        )
    material = candidates[-1]
    return {
        **material,
        "tunnel_id": tunnel_id,
        "tunnel_id_source": tunnel_id_source,
        "tunnel_id_sha256": hashlib.sha256(tunnel_id.encode("utf-8"))
        .hexdigest()
        .upper(),
        "remote_identity_mode": "REUSED_PRECREATED_REMOTE_TUNNEL",
        "remote_version_history_retained": False,
        "prior_remote_version_disabled": False,
        "prior_local_runtime_retained_disabled": True,
        "remote_crud_invoked": False,
    }


def _load_json_process_output(completed: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    try:
        lines = completed.stdout.decode("utf-8").splitlines()
        return json.loads("\n".join(line for line in lines if line.strip()))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The version-bound tunnel command returned no exact JSON receipt."
        ) from exc


def _windows_powershell_environment() -> dict[str, str]:
    """Return a Windows PowerShell-only module path for tunnel subprocesses.

    Codex Desktop prepends its bundled PowerShell 7 module directory. Windows
    PowerShell 5.1 can then select the incompatible 7.x Security module before
    its own 3.x module, which breaks ``Get-Acl`` before an interactive prompt or
    scheduled-task activation. Tunnel helpers need the Windows PowerShell module
    roots while retaining every unrelated host environment variable.
    """

    environment = os.environ.copy()
    candidates = [
        part
        for part in environment.get("PSModulePath", "").split(os.pathsep)
        if part and "windowspowershell" in part.casefold().replace("/", "\\")
    ]
    system_root = Path(environment.get("SystemRoot") or r"C:\Windows")
    candidates.append(
        str(system_root / "System32" / "WindowsPowerShell" / "v1.0" / "Modules")
    )
    program_files = environment.get("ProgramFiles")
    if program_files:
        candidates.append(str(Path(program_files) / "WindowsPowerShell" / "Modules"))
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(Path(candidate)).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(str(Path(candidate)))
    environment["PSModulePath"] = os.pathsep.join(unique)
    return environment


def _run_visible_powershell(
    arguments: list[str], *, timeout: int
) -> subprocess.CompletedProcess[bytes]:
    """Launch one interactive Windows PowerShell in its own visible console.

    The installer itself runs under a host-owned pipe. ``CREATE_NEW_CONSOLE``
    alone preserves that closed standard-input handle, so ``Read-Host`` exits
    before the user can type. A hidden, non-interactive PowerShell parent uses
    ``Start-Process`` to allocate the child console and waits for its exact exit
    code. Only paths and fixed switches enter the encoded launcher; credentials
    remain confined to the child console and DPAPI envelope.
    """

    if not arguments:
        raise InstallationError("The visible PowerShell command is empty.")
    executable = Path(arguments[0]).resolve()
    if not executable.is_file():
        raise InstallationError("The visible PowerShell executable is missing.")

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    argument_line = subprocess.list2cmdline(arguments[1:])
    launcher = (
        f"$process = Start-Process -FilePath {literal(str(executable))} "
        f"-ArgumentList {literal(argument_line)} -Wait -PassThru; "
        "exit $process.ExitCode"
    )
    encoded = base64.b64encode(launcher.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=_windows_hidden_creationflags(),
        env=_windows_powershell_environment(),
        timeout=timeout,
    )


def _ensure_interactive_local_tunnel_runtime_key(
    *,
    target_cache: Path,
    target_version: str,
    data_root: Path,
    pending_receipt_path: Path,
    pending_receipt_sha256: str,
    archive_sha256: str,
    force_runtime_key_entry: bool = False,
) -> dict[str, Any]:
    identity = _versioned_local_tunnel_identity(
        plugin_version=target_version, plugin_root=target_cache, data_root=data_root
    )
    runtime_root = Path(identity["runtime_root"])
    envelope = runtime_root / "secrets" / "control-plane-runtime-key.dpapi"
    authority_root = data_root / "installations" / "codex-v300"
    receipt_path = (
        authority_root
        / "tunnel"
        / f"LOCAL_TUNNEL_KEY_ENTRY_{archive_sha256[:16]}.json"
    )
    if receipt_path.is_file():
        existing, exact_path, exact_sha256 = _load_self_sealed_json(
            path=receipt_path,
            expected_file_sha256=_sha256(receipt_path),
            authority_root=authority_root,
            label="Local tunnel interactive Runtime-key entry receipt",
        )
        retained_state = str(existing.get("state") or "")
        prior_source = Path(str(existing.get("runtime_key_envelope_source") or ""))
        target_envelope_matches = (
            envelope.is_file()
            and existing.get("encrypted_envelope_file_sha256") == _sha256(envelope)
        )
        pending_prior_source_matches = (
            retained_state
            == "PRIOR_PERSISTENT_RUNTIME_KEY_REUSE_PENDING_ACTIVATION"
            and prior_source.is_file()
            and existing.get("encrypted_envelope_file_sha256")
            == _sha256(prior_source)
        )
        if (
            existing.get("schema")
            != "evidence-lane.plugin-creator-local-tunnel-key-entry.v1"
            or existing.get("status") != "PASS"
            or existing.get("plugin_version") != target_version
            or existing.get("tunnel_compatibility_sha256")
            != identity["tunnel_compatibility_sha256"]
            or existing.get("pending_receipt_sha256") != pending_receipt_sha256
            or not (target_envelope_matches or pending_prior_source_matches)
            or existing.get("runtime_key_plaintext_written") is not False
            or existing.get("runtime_key_argument_used") is not False
            or existing.get("runtime_key_environment_output") is not False
        ):
            raise InstallationError(
                "The retained interactive Runtime-key entry receipt no longer matches."
            )
        return {
            **existing,
            "receipt_path": str(exact_path),
            "receipt_file_sha256": exact_sha256,
            "reused_for_same_materialized_version": True,
        }
    if envelope.is_file() and not force_runtime_key_entry:
        body = {
            "schema": "evidence-lane.plugin-creator-local-tunnel-key-entry.v1",
            "status": "PASS",
            "state": "COMPATIBLE_TUNNEL_RUNTIME_KEY_REUSED_ACTIVATION_PENDING",
            "plugin_version": target_version,
            "tunnel_version_token": identity["tunnel_version_token"],
            "tunnel_compatibility_sha256": identity[
                "tunnel_compatibility_sha256"
            ],
            "runtime_root": str(runtime_root),
            "pending_receipt_path": str(pending_receipt_path.resolve()),
            "pending_receipt_sha256": pending_receipt_sha256,
            "entry_terminal_visible": False,
            "persistent_runtime_hidden": True,
            "runtime_key_plaintext_written": False,
            "runtime_key_argument_used": False,
            "runtime_key_environment_output": False,
            "runtime_key_reused_from_compatible_tunnel": True,
            "first_registration_or_invalid_credential_prompt": False,
            "encrypted_envelope_file_sha256": _sha256(envelope),
            "remote_crud_invoked": False,
            "restart_authority_created": False,
        }
        sealed = _write_self_sealed_json(receipt_path, body)
        return {
            **sealed,
            "receipt_path": str(receipt_path.resolve()),
            "receipt_file_sha256": _sha256(receipt_path),
            "reused_for_same_materialized_version": False,
        }
    if not force_runtime_key_entry:
        prior_candidates: list[tuple[int, Path, Path]] = []
        for prior_envelope in data_root.glob(
            "tunnel-runtime-*/secrets/control-plane-runtime-key.dpapi"
        ):
            if prior_envelope.resolve() == envelope.resolve() or not prior_envelope.is_file():
                continue
            prior_root = prior_envelope.parent.parent
            marker_path = prior_root / "evidence-lane-tunnel-installation.json"
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if (
                marker.get("schema")
                != "evidence-lane.versioned-secure-mcp-tunnel-installation.v2"
                or marker.get("host_lifetime") != "PERSISTENT"
                or marker.get("runtime_key_plaintext_written") is not False
                or marker.get("reusable_without_reinstall") is not True
                or marker.get("tunnel_key_retention")
                != "CURRENT_WINDOWS_USER_DPAPI_PROFILE"
                or marker.get("host_wide_project_neutral") is not True
                or re.fullmatch(r"[A-F0-9]{64}", str(marker.get("runtime_key") or ""))
                is None
            ):
                continue
            prior_candidates.append(
                (marker_path.stat().st_mtime_ns, prior_envelope, marker_path)
            )
        if prior_candidates:
            selected_prior = max(prior_candidates, key=lambda row: row[0])
            prior_envelope = selected_prior[1].resolve()
            prior_marker_path = selected_prior[2].resolve()
            body = {
                "schema": "evidence-lane.plugin-creator-local-tunnel-key-entry.v1",
                "status": "PASS",
                "state": "PRIOR_PERSISTENT_RUNTIME_KEY_REUSE_PENDING_ACTIVATION",
                "plugin_version": target_version,
                "tunnel_version_token": identity["tunnel_version_token"],
                "tunnel_compatibility_sha256": identity[
                    "tunnel_compatibility_sha256"
                ],
                "runtime_root": str(runtime_root),
                "pending_receipt_path": str(pending_receipt_path.resolve()),
                "pending_receipt_sha256": pending_receipt_sha256,
                "entry_terminal_visible": False,
                "persistent_runtime_hidden": True,
                "runtime_key_plaintext_written": False,
                "runtime_key_argument_used": False,
                "runtime_key_environment_output": False,
                "runtime_key_reused_from_compatible_tunnel": False,
                "runtime_key_reused_from_prior_persistent_host": True,
                "prior_marker_file_sha256": _sha256(prior_marker_path),
                "runtime_key_envelope_source": str(prior_envelope),
                "first_registration_or_invalid_credential_prompt": False,
                "encrypted_envelope_file_sha256": _sha256(prior_envelope),
                "remote_crud_invoked": False,
                "restart_authority_created": False,
            }
            sealed = _write_self_sealed_json(receipt_path, body)
            return {
                **sealed,
                "receipt_path": str(receipt_path.resolve()),
                "receipt_file_sha256": _sha256(receipt_path),
                "reused_for_same_materialized_version": False,
            }
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    installer = target_cache / "scripts" / "windows_tunnel" / "Install-EvidenceLaneTunnel.ps1"
    create_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    if not powershell.is_file() or not installer.is_file() or create_console == 0:
        raise InstallationError(
            "Visible PowerShell Runtime-key entry is unavailable on this host."
        )
    capture_arguments = [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-PluginRoot",
            str(target_cache.resolve()),
            "-RuntimeControlRoot",
            str(data_root.resolve()),
            "-RuntimeRoot",
            str(runtime_root.resolve()),
            "-SlotRole",
            "versioned-local-testing",
            "-HostLifetime",
            "Persistent",
            "-InteractionProfile",
            "CODEX_APP_INTERACTIVE",
            "-HostToolTransport",
            "HOST_TOOL_GAP",
            "-CaptureRuntimeKeyOnly",
        ]
    if force_runtime_key_entry:
        capture_arguments.append("-RotateRuntimeKey")
    completed = _run_visible_powershell(capture_arguments, timeout=300)
    if completed.returncode != 0 or not envelope.is_file():
        raise InstallationError(
            "Visible Runtime-key entry did not complete; no restart authority was created."
        )
    body = {
        "schema": "evidence-lane.plugin-creator-local-tunnel-key-entry.v1",
        "status": "PASS",
        "state": "INTERACTIVE_RUNTIME_KEY_ENTRY_COMPLETE_ACTIVATION_PENDING",
        "plugin_version": target_version,
        "tunnel_version_token": identity["tunnel_version_token"],
        "tunnel_compatibility_sha256": identity["tunnel_compatibility_sha256"],
        "runtime_root": str(runtime_root),
        "pending_receipt_path": str(pending_receipt_path.resolve()),
        "pending_receipt_sha256": pending_receipt_sha256,
        "entry_terminal_visible": True,
        "persistent_runtime_hidden": True,
        "runtime_key_plaintext_written": False,
        "runtime_key_argument_used": False,
        "runtime_key_environment_output": False,
        "runtime_key_reused_from_compatible_tunnel": False,
        "runtime_key_reused_from_prior_persistent_host": False,
        "first_registration_or_invalid_credential_prompt": True,
        "encrypted_envelope_file_sha256": _sha256(envelope),
        "remote_crud_invoked": False,
        "restart_authority_created": False,
    }
    sealed = _write_self_sealed_json(receipt_path, body)
    return {
        **sealed,
        "receipt_path": str(receipt_path.resolve()),
        "receipt_file_sha256": _sha256(receipt_path),
        "reused_for_same_materialized_version": False,
    }


def _activate_version_bound_local_tunnel(
    *,
    target_cache: Path,
    target_version: str,
    selector: str,
    data_root: Path,
    pending_receipt_path: Path,
    pending_receipt_sha256: str,
    archive_sha256: str,
    precreated_tunnel_id: str | None,
    force_runtime_key_entry: bool = False,
) -> dict[str, Any]:
    identity = _versioned_local_tunnel_identity(
        plugin_version=target_version, plugin_root=target_cache, data_root=data_root
    )
    key_entry = _ensure_interactive_local_tunnel_runtime_key(
        target_cache=target_cache,
        target_version=target_version,
        data_root=data_root,
        pending_receipt_path=pending_receipt_path,
        pending_receipt_sha256=pending_receipt_sha256,
        archive_sha256=archive_sha256,
        force_runtime_key_entry=force_runtime_key_entry,
    )
    material = _resolve_local_tunnel_bootstrap_material(
        data_root=data_root, precreated_tunnel_id=precreated_tunnel_id
    )
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    installer = target_cache / "scripts" / "windows_tunnel" / "Install-EvidenceLaneTunnel.ps1"
    if not powershell.is_file() or not installer.is_file():
        raise InstallationError(
            "The exact installed Windows tunnel launcher is unavailable."
        )
    arguments = [
        str(powershell),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer),
        "-PluginRoot",
        str(target_cache.resolve()),
        "-RuntimeControlRoot",
        str(data_root.resolve()),
        "-RuntimeRoot",
        str(Path(identity["runtime_root"]).resolve()),
        "-SlotRole",
        "versioned-local-testing",
        "-TunnelClientSource",
        str(material["client_path"]),
        "-TunnelClientLicenseSource",
        str(material["license_path"]),
        "-ExpectedTunnelClientLicenseSha256",
        str(material["license_sha256"]),
        "-TunnelId",
        str(material["tunnel_id"]),
        "-HostLifetime",
        "Persistent",
        "-InteractionProfile",
        "CODEX_APP_INTERACTIVE",
        "-HostToolTransport",
        "HOST_TOOL_GAP",
    ]
    prior_envelope_source = str(key_entry.get("runtime_key_envelope_source") or "")
    if prior_envelope_source:
        arguments.extend(["-RuntimeKeyEnvelopeSource", prior_envelope_source])
    else:
        arguments.append("-RequirePreparedRuntimeKey")
    arguments.append("-Activate")
    completed = subprocess.run(
        arguments,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=240,
        creationflags=_windows_hidden_creationflags(),
        env=_windows_powershell_environment(),
    )
    if completed.returncode != 0:
        try:
            installer_failure = _load_json_process_output(completed)
        except InstallationError:
            installer_failure = {}
        rollback_status = str(
            installer_failure.get("prior_local_tunnel_rollback_status") or "UNPROVEN"
        )
        failure = {
            "schema": LOCAL_TUNNEL_FAILURE_SCHEMA,
            "status": "FAIL",
            "state": "LOCAL_TUNNEL_ACTIVATION_FAILED_NO_RESTART_AUTHORITY",
            "plugin_version": target_version,
            "selector": selector,
            "pending_receipt_path": str(pending_receipt_path.resolve()),
            "pending_receipt_sha256": pending_receipt_sha256,
            "installer_sha256": _sha256(installer),
            "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest().upper(),
            "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest().upper(),
            "returncode": completed.returncode,
            "failed_new_local_tunnel_disabled": (
                installer_failure.get("failed_new_local_tunnel_disabled") is True
            ),
            "prior_local_tunnel_rollback_status": rollback_status,
            "prior_local_tunnel_restore_count": int(
                installer_failure.get("prior_local_tunnel_restore_count") or 0
            ),
            "prior_local_tunnel_restore_proven": rollback_status
            in {"PASS", "NOT_APPLICABLE"},
            "restart_authority_created": False,
            "remote_crud_invoked": False,
        }
        failure_path = (
            data_root
            / "installations"
            / "codex-v300"
            / "tunnel"
            / f"LOCAL_TUNNEL_FAILURE_{archive_sha256[:16]}.json"
        )
        _write_self_sealed_json(failure_path, failure)
        raise InstallationError(
            "The exact version-bound local tunnel failed; no restart authority was created."
        )
    installed = _load_json_process_output(completed)
    runtime_root = Path(identity["runtime_root"])
    activated_envelope = runtime_root / "secrets" / "control-plane-runtime-key.dpapi"
    if (
        not activated_envelope.is_file()
        or _sha256(activated_envelope)
        != key_entry["encrypted_envelope_file_sha256"]
    ):
        raise InstallationError(
            "The activated tunnel did not preserve the verified DPAPI Runtime-key envelope."
        )
    marker_path = runtime_root / "evidence-lane-tunnel-installation.json"
    manager = runtime_root / "Manage-EvidenceLaneTunnel.ps1"
    if not marker_path.is_file() or not manager.is_file():
        raise InstallationError("The activated tunnel marker or manager is missing.")
    marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
    status_process = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(manager),
            "-Action",
            "Status",
            "-RuntimeRoot",
            str(runtime_root),
            "-ProfileName",
            identity["profile_name"],
            "-ReleaseToken",
            identity["release_token"],
            "-TaskName",
            identity["task_name"],
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        creationflags=_windows_hidden_creationflags(),
        env=_windows_powershell_environment(),
    )
    status = _load_json_process_output(status_process)
    if (
        status_process.returncode != 0
        or installed.get("status") != "PASS"
        or installed.get("plugin_version") != target_version
        or installed.get("installed_selector") != selector
        or Path(str(installed.get("installed_cache_root") or "")).resolve()
        != target_cache.resolve()
        or installed.get("tunnel_version_token") != identity["tunnel_version_token"]
        or Path(str(installed.get("runtime_root") or "")).resolve() != runtime_root
        or installed.get("task_name") != identity["task_name"]
        or installed.get("profile") != identity["profile_name"]
        or marker.get("plugin_version") != target_version
        or marker.get("installed_selector") != selector
        or Path(str(marker.get("installed_cache_root") or "")).resolve()
        != target_cache.resolve()
        or marker.get("installed_receipt_file_sha256") != pending_receipt_sha256
        or status.get("status") != "PASS"
        or status.get("plugin_version") != target_version
        or status.get("tunnel_version_token") != identity["tunnel_version_token"]
        or status.get("task_name") != identity["task_name"]
        or status.get("task_state") != "Running"
        or status.get("process_running") is not True
        or status.get("control_plane_poll_ready") is not True
    ):
        raise InstallationError(
            "The local tunnel activation/readback did not match the installed package."
        )
    body = {
        "schema": LOCAL_TUNNEL_ACTIVATION_SCHEMA,
        "status": "PASS",
        "state": "VERSION_BOUND_LOCAL_TUNNEL_READY_BEFORE_APP_RESTART",
        "plugin_version": target_version,
        "selector": selector,
        "installed_cache_root": str(target_cache.resolve()),
        "pending_receipt_path": str(pending_receipt_path.resolve()),
        "pending_receipt_sha256": pending_receipt_sha256,
        "interactive_runtime_key_entry_receipt_path": key_entry["receipt_path"],
        "interactive_runtime_key_entry_receipt_file_sha256": key_entry[
            "receipt_file_sha256"
        ],
        "interactive_runtime_key_entry_required": bool(
            key_entry["entry_terminal_visible"]
        ),
        "runtime_key_reused_from_compatible_tunnel": bool(
            key_entry["runtime_key_reused_from_compatible_tunnel"]
        ),
        "runtime_key_reused_from_prior_persistent_host": bool(
            key_entry.get("runtime_key_reused_from_prior_persistent_host")
        ),
        "tunnel_version_token": identity["tunnel_version_token"],
        "plugin_version_digest": identity["plugin_version_digest"],
        "tunnel_compatibility_sha256": identity["tunnel_compatibility_sha256"],
        "tunnel_compatibility_digest": identity["tunnel_compatibility_digest"],
        "runtime_root": str(runtime_root),
        "task_name": identity["task_name"],
        "profile_name": identity["profile_name"],
        "marker_path": str(marker_path.resolve()),
        "marker_file_sha256": _sha256(marker_path),
        "manager_path": str(manager.resolve()),
        "manager_file_sha256": _sha256(manager),
        "installer_path": str(installer.resolve()),
        "installer_file_sha256": _sha256(installer),
        "task_state": status["task_state"],
        "process_running": True,
        "control_plane_poll_ready": True,
        "exact_active_local_task_count": 1,
        "exact_active_local_process_count": 1,
        "prior_local_bindings": list(installed.get("prior_local_bindings") or []),
        "remote_tunnel_id_sha256": material["tunnel_id_sha256"],
        "remote_tunnel_id_source": material["tunnel_id_source"],
        "remote_identity_mode": material["remote_identity_mode"],
        "remote_version_history_retained": False,
        "prior_remote_version_disabled": False,
        "prior_local_runtime_retained_disabled": True,
        "remote_crud_invoked": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_invoked": False,
    }
    receipt_path = (
        data_root
        / "installations"
        / "codex-v300"
        / "tunnel"
        / f"LOCAL_TUNNEL_ACTIVATION_{archive_sha256[:16]}.json"
    )
    sealed = _write_self_sealed_json(receipt_path, body)
    return {
        **sealed,
        "receipt_path": str(receipt_path.resolve()),
        "receipt_file_sha256": _sha256(receipt_path),
    }


def _rollback_version_bound_local_tunnel(
    *, tunnel: dict[str, Any], data_root: Path
) -> dict[str, Any]:
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    runtime_root = Path(str(tunnel.get("runtime_root") or "")).resolve()
    expected_parent = data_root.resolve()
    if not _inside(runtime_root, expected_parent):
        raise InstallationError("Tunnel rollback escaped the hidden runtime root.")
    manager = runtime_root / "Manage-EvidenceLaneTunnel.ps1"
    stop = subprocess.run(
        [
            str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", str(manager), "-Action", "Stop", "-RuntimeRoot", str(runtime_root),
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        creationflags=_windows_hidden_creationflags(),
        env=_windows_powershell_environment(),
    )
    try:
        stop_result = _load_json_process_output(stop) if stop.returncode == 0 else {}
    except InstallationError:
        stop_result = {}
    failures = (
        []
        if stop.returncode == 0 and stop_result.get("status") == "STOPPED_SAVED"
        else ["FAILED_NEW_TUNNEL_STOP"]
    )
    restored = 0
    for prior in list(tunnel.get("prior_local_bindings") or []):
        prior_root = Path(str(prior.get("runtime_root") or "")).resolve()
        if not _inside(prior_root, expected_parent):
            failures.append("PRIOR_RUNTIME_ESCAPED_HIDDEN_ROOT")
            continue
        prior_manager = prior_root / "Manage-EvidenceLaneTunnel.ps1"
        start = subprocess.run(
            [
                str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                "-File", str(prior_manager), "-Action", "Start",
                "-RuntimeRoot", str(prior_root),
                "-ProfileName", str(prior.get("profile_name") or ""),
                "-ReleaseToken", str(prior.get("release_token") or ""),
                "-TaskName", str(prior.get("task_name") or ""),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
            creationflags=_windows_hidden_creationflags(),
            env=_windows_powershell_environment(),
        )
        try:
            start_result = (
                _load_json_process_output(start) if start.returncode == 0 else {}
            )
        except InstallationError:
            start_result = {}
        if (
            start.returncode == 0
            and start_result.get("status") == "PASS"
            and start_result.get("control_plane_poll_ready") is True
            and start_result.get("task_name") == prior.get("task_name")
        ):
            restored += 1
        else:
            failures.append("PRIOR_TUNNEL_RESTORE_FAILED")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failed_new_tunnel_disabled": stop.returncode == 0,
        "prior_local_tunnel_restored_count": restored,
        "failure_codes": failures,
        "remote_crud_invoked": False,
    }


def _seal_plugin_creator_local_cache_restart(
    *,
    stage_receipt_path: Path,
    stage_receipt_sha256: str,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    precreated_tunnel_id: str | None = None,
    force_local_tunnel_key_entry: bool = False,
) -> dict[str, Any]:
    """Materialize one fresh local cache and seal exact-task restart authority."""

    exact_codex_home = codex_home.resolve()
    exact_data_root = data_root.resolve()
    authority_root = exact_data_root / "installations" / "codex-v300"
    stage, exact_stage, exact_stage_sha256 = _load_self_sealed_json(
        path=stage_receipt_path,
        expected_file_sha256=stage_receipt_sha256,
        authority_root=authority_root,
        label="Plugin Creator local-update staging receipt",
    )
    marketplace = dict(stage.get("marketplace") or {})
    plugin = dict(stage.get("plugin") or {})
    stage_activation = dict(stage.get("activation") or {})
    selector = f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"
    target_version = str(plugin.get("version") or "")
    marketplace_root = (
        exact_codex_home / "local-marketplaces" / LOCAL_TESTING_MARKETPLACE_NAME
    )
    marketplace_plugin = marketplace_root / "plugins" / PLUGIN_NAME
    if (
        stage.get("schema") != INSTALL_SCHEMA
        or stage.get("status") != "PASS"
        or stage_activation.get("state") != "STAGED_RESTART_NOT_YET_REQUIRED"
        or stage_activation.get("plugin_add_invoked") is not False
        or stage.get("restart_required") is not False
        or stage.get("runtime_ready_before_task_reopen") is not False
        or plugin.get("plugin_id") != PLUGIN_NAME
        or not target_version.startswith(f"{BASE_RELEASE}+codex.")
        or marketplace.get("name") != LOCAL_TESTING_MARKETPLACE_NAME
        or Path(str(marketplace.get("root") or "")).resolve()
        != marketplace_root.resolve()
        or marketplace.get("route_law") != PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW
        or marketplace.get("marketplace_rotation_mode")
        != "PLUGIN_CREATOR_LOCAL_SOURCE_UPDATE"
        or marketplace.get("windows_root_rotation_attempted") is not False
        or stage.get("generated_cache_written_directly") is not False
        or stage.get("previous_release_cache_deleted") is not False
        or stage.get("candidate_created_or_accepted") is not False
        or stage.get("pointer_moved") is not False
        or stage.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "The supplied staging receipt is not the current Plugin Creator local route."
        )
    source_identity = _validate_plugin(marketplace_plugin)
    if source_identity != plugin:
        raise InstallationError(
            "The configured local marketplace no longer reproduces its staging receipt."
        )

    target_cache = (
        exact_codex_home
        / "plugins"
        / "cache"
        / LOCAL_TESTING_MARKETPLACE_NAME
        / PLUGIN_NAME
        / target_version
    )
    recovery_receipt_path = authority_root / (
        f"INSTALL_{str(stage.get('archive_sha256') or '')[:16]}_"
        "PLUGIN_CREATOR_LOCAL_RESTART.json"
    )
    recovering_materialized_cache = target_cache.is_dir()
    if recovering_materialized_cache and recovery_receipt_path.exists():
        raise InstallationError(
            "The target local cache and its sealed restart receipt already exist; "
            "the completed materialization route cannot be replayed."
        )
    before_list = _run_codex(
        executable,
        exact_codex_home,
        ["plugin", "list", "--json"],
    )
    before_rows = [
        dict(row)
        for row in before_list.get("installed") or []
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    local_before = [row for row in before_rows if row.get("pluginId") == selector]
    enabled_before = [row for row in before_rows if row.get("enabled") is True]
    current_dispatch: dict[str, Any] | None
    if recovering_materialized_cache:
        if (
            len(local_before) != 1
            or len(enabled_before) != 1
            or enabled_before[0].get("pluginId") != selector
            or local_before[0].get("version") != target_version
            or any(
                row.get("enabled") is not False
                for row in before_rows
                if row.get("pluginId") != selector
            )
        ):
            raise InstallationError(
                "Materialized-cache recovery requires the exact target selector to "
                "be the sole enabled Evidence Lane plugin."
            )
        old_active_version = str(
            dict(marketplace.get("surface_change_display") or {}).get(
                "previous_plugin_version"
            )
            or target_version
        )
        current_dispatch = {
            "status": "PASS",
            "route": "PLUGIN_CREATOR_ACTIVE_LOCAL_SLOT_UPDATE_RECOVERY",
            "selector": selector,
            "old_active_version": old_active_version,
            "target_version": target_version,
            "enabled_evidence_lane_count": 1,
            "main_selector_enabled": False,
            "standalone_plugin_add_invoked": False,
            "target_cache_already_materialized": True,
            "completed_restart_receipt_preexisted": False,
        }
        materialization = {
            "status": "PASS",
            "route": "CODEX_PLUGIN_ADD_RECOVERY_READBACK",
            "invocation_count": 0,
            "prior_materialization_invocation_count": 1,
            "outcome": "TARGET_SELECTED_HOST_RESTART_REQUIRED",
            "returncode": 0,
            "stdout_sha256": None,
            "stderr_sha256": None,
            "plugin_add": None,
            "recovered_after_prior_materialization_failure": True,
            "plugin_add_replayed": False,
        }
    else:
        current_dispatch = _plugin_creator_active_local_slot_update(
            before_list,
            plugin_selector=selector,
            target_version=target_version,
        )
        if (
            current_dispatch is None
            or len(local_before) != 1
            or len(enabled_before) != 1
        ):
            raise InstallationError(
                "Local cache materialization requires one older active local selector."
            )
        old_active_version = str(local_before[0].get("version") or "")
        materialization = _run_plugin_creator_local_cache_materialization(
            executable=executable,
            codex_home=exact_codex_home,
            plugin_selector=selector,
            target_version=target_version,
            old_active_version=old_active_version,
        )
    if not target_cache.is_dir():
        raise InstallationError(
            "Codex plugin add did not materialize the exact fresh cache."
        )
    cache_identity = _validate_plugin(target_cache)
    if (
        cache_identity != source_identity
        or _source_inventory(target_cache)["manifest_sha256"]
        != _source_inventory(marketplace_plugin)["manifest_sha256"]
    ):
        raise InstallationError(
            "The materialized cache does not reproduce the configured local source."
        )

    # The Plugin Creator route must seal the derived runtime and hook-isolation
    # authority before the desktop can reopen the target cache.  Older local
    # materialization receipts omitted these records, so the Windows hook host
    # could not bind the newly active plugin root and failed every event with
    # SEALED_RUNTIME_INTERPRETER_NOT_FOUND.  This is package preparation, not
    # host activation: hooks remain disabled and exact-task reattachment is
    # still required after the user manually restarts the app.
    runtime_prewarm = _prewarm_installed_runtime(
        target_cache,
        data_root=exact_data_root,
        expected_plugin_version=target_version,
    )
    hook_event_isolation = _initialize_installed_hook_event_isolation(
        target_cache,
        data_root=exact_data_root,
        installation_id=(
            "plugin_creator_local_"
            + str(stage.get("archive_sha256") or "")[:40].lower()
        ),
    )

    after_list = _run_codex(
        executable,
        exact_codex_home,
        ["plugin", "list", "--json"],
    )
    after_rows = [
        dict(row)
        for row in after_list.get("installed") or []
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    local_after = [row for row in after_rows if row.get("pluginId") == selector]
    enabled_after = [row for row in after_rows if row.get("enabled") is True]
    allowed_after_version = (
        target_version
        if materialization["outcome"] == "TARGET_SELECTED_HOST_RESTART_REQUIRED"
        else old_active_version
    )
    if (
        len(local_after) != 1
        or len(enabled_after) != 1
        or enabled_after[0].get("pluginId") != selector
        or local_after[0].get("version") != allowed_after_version
    ):
        raise InstallationError(
            "Codex selector state drifted while sealing the restart boundary."
        )
    hook_state = _disabled_hook_state(
        codex_home=exact_codex_home,
        selector=selector,
        expected_hooks=dict(
            dict(source_identity.get("surface_inventory") or {}).get("hooks") or {}
        ),
    )
    tunnel_identity = _versioned_local_tunnel_identity(
        plugin_version=target_version,
        plugin_root=target_cache,
        data_root=exact_data_root,
    )
    compatible_envelope_present = (
        Path(tunnel_identity["runtime_root"])
        / "secrets"
        / "control-plane-runtime-key.dpapi"
    ).is_file()
    interactive_key_required = (
        force_local_tunnel_key_entry or not compatible_envelope_present
    )

    pending_path = authority_root / (
        f"INSTALL_{str(stage.get('archive_sha256') or '')[:16]}_"
        "LOCAL_INSTALLED_STATIC_ACCEPTANCE_TUNNEL_PENDING.json"
    )
    pending_body = deepcopy(stage)
    pending_body.pop("receipt_sha256", None)
    pending_body["activation_authority"] = {
        "status": "PASS",
        "boundary": "LOCAL_INSTALLED_STATIC_ACCEPTANCE_TUNNEL_PENDING",
        "selector": selector,
        "route_law": PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW,
        "current_route_dispatch": current_dispatch,
        "staging_receipt": str(exact_stage),
        "staging_receipt_sha256": exact_stage_sha256,
        "accepted_two_slot_registry_mutated": False,
        "state_travel_invoked": False,
    }
    pending_body["activation"] = {
        "state": LOCAL_INSTALLED_STATIC_TUNNEL_PENDING_STATE,
        "plugin_add_invoked": True,
        "plugin_selector": selector,
        "installed_path": str(target_cache.resolve()),
        "plugin_add": {
            "status": "CACHE_MATERIALIZED_TUNNEL_PENDING_NO_RESTART_AUTHORITY",
            "pluginId": selector,
            "version": target_version,
            "installedPath": str(target_cache.resolve()),
            "old_active_version": old_active_version,
            "registry_version_before_restart": allowed_after_version,
            "activation_completed": False,
        },
        "plugin_creator_local_update": materialization,
        "hook_state": hook_state,
        "runtime_prewarm": runtime_prewarm,
        "hook_event_isolation": hook_event_isolation,
        "tunnel": {
            "status": (
                "PENDING_INTERACTIVE_RUNTIME_KEY_ENTRY"
                if interactive_key_required
                else "PENDING_COMPATIBLE_TUNNEL_REBIND"
            ),
            "tunnel_compatibility_sha256": tunnel_identity[
                "tunnel_compatibility_sha256"
            ],
            "compatible_runtime_present": compatible_envelope_present,
            "interactive_runtime_key_entry_required": interactive_key_required,
            "persistent_runtime_hidden": True,
            "remote_crud_authorized": False,
        },
        "runtime_ready_before_task_reopen": False,
    }
    pending_body["restart_required"] = False
    pending_body["runtime_ready_before_task_reopen"] = False
    pending_body["hooks_enabled_by_update"] = False
    pending_body["generated_cache_written_directly"] = False
    pending_body["previous_release_cache_deleted"] = False
    pending_body["candidate_created_or_accepted"] = False
    pending_body["pointer_moved"] = False
    pending_body["hil_inferred"] = False
    pending_body["task_reopened"] = False
    pending_body["state_travel_invoked"] = False
    pending_body["accepted_two_slot_registry_mutated"] = False
    if pending_path.is_file():
        pending, exact_pending, pending_file_sha256 = _load_self_sealed_json(
            path=pending_path,
            expected_file_sha256=_sha256(pending_path),
            authority_root=authority_root,
            label="Plugin Creator installed static tunnel-pending receipt",
        )
        if (
            pending.get("status") != "PASS"
            or dict(pending.get("activation") or {}).get("state")
            != LOCAL_INSTALLED_STATIC_TUNNEL_PENDING_STATE
            or dict(pending.get("plugin") or {}) != plugin
            or Path(
                str(dict(pending.get("activation") or {}).get("installed_path") or "")
            ).resolve()
            != target_cache.resolve()
            or pending.get("restart_required") is not False
            or pending.get("candidate_created_or_accepted") is not False
            or pending.get("pointer_moved") is not False
            or pending.get("hil_inferred") is not False
        ):
            raise InstallationError(
                "The retained tunnel-pending static acceptance receipt drifted."
            )
    else:
        pending = _write_self_sealed_json(pending_path, pending_body)
        exact_pending = pending_path.resolve()
        pending_file_sha256 = _sha256(pending_path)

    tunnel = _activate_version_bound_local_tunnel(
        target_cache=target_cache,
        target_version=target_version,
        selector=selector,
        data_root=exact_data_root,
        pending_receipt_path=exact_pending,
        pending_receipt_sha256=pending_file_sha256,
        archive_sha256=str(stage.get("archive_sha256") or ""),
        precreated_tunnel_id=precreated_tunnel_id,
        force_runtime_key_entry=force_local_tunnel_key_entry,
    )

    receipt = deepcopy(stage)
    receipt.pop("receipt_sha256", None)
    receipt["activation_authority"] = {
        "status": "PASS",
        "boundary": ("PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_EXACT_TASK_RESTART"),
        "selector": selector,
        "route_law": PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW,
        "current_route_dispatch": current_dispatch,
        "staging_receipt": str(exact_stage),
        "staging_receipt_sha256": exact_stage_sha256,
        "accepted_two_slot_registry_mutated": False,
        "state_travel_invoked": False,
    }
    receipt["activation"] = {
        "state": PLUGIN_CREATOR_LOCAL_CACHE_RESTART_STATE,
        "plugin_add_invoked": True,
        "plugin_selector": selector,
        "installed_path": str(target_cache.resolve()),
        "plugin_add": {
            "status": "CACHE_MATERIALIZED_RESTART_REQUIRED",
            "pluginId": selector,
            "version": target_version,
            "installedPath": str(target_cache.resolve()),
            "old_active_version": old_active_version,
            "registry_version_before_restart": allowed_after_version,
            "activation_completed": False,
        },
        "plugin_creator_local_update": materialization,
        "hook_state": hook_state,
        "runtime_prewarm": runtime_prewarm,
        "hook_event_isolation": hook_event_isolation,
        "tunnel": tunnel,
        "runtime_ready_before_task_reopen": False,
    }
    receipt["restart_required"] = True
    receipt["runtime_ready_before_task_reopen"] = False
    receipt["hooks_enabled_by_update"] = False
    receipt["generated_cache_written_directly"] = False
    receipt["previous_release_cache_deleted"] = False
    receipt["candidate_created_or_accepted"] = False
    receipt["pointer_moved"] = False
    receipt["hil_inferred"] = False
    receipt["task_reopened"] = False
    receipt["state_travel_invoked"] = False
    receipt["accepted_two_slot_registry_mutated"] = False
    receipt["credential_requested_or_stored"] = bool(
        tunnel["interactive_runtime_key_entry_required"]
    )
    receipt["credential_handling"] = {
        "interactive_runtime_key_entry_required": bool(
            tunnel["interactive_runtime_key_entry_required"]
        ),
        "entry_owner": (
            "INSTALLED_TUNNEL_HELPER_VISIBLE_SECURESTRING_PROMPT"
            if tunnel["interactive_runtime_key_entry_required"]
            else "COMPATIBLE_CAPABILITY_BOUND_DPAPI_ENVELOPE"
        ),
        "plaintext_received_by_installer_process": False,
        "plaintext_written_to_arguments": False,
        "plaintext_written_to_environment_output": False,
        "plaintext_written_to_logs_or_receipts": False,
        "capability_bound_dpapi_envelope_persisted": True,
        "compatible_runtime_key_envelope_reused": bool(
            tunnel["runtime_key_reused_from_compatible_tunnel"]
        ),
    }
    receipt_path = recovery_receipt_path
    try:
        sealed = _write_self_sealed_json(receipt_path, receipt)
        _write_atomic(authority_root / "CURRENT_INSTALLATION.json", _json_bytes(sealed))
    except OSError as exc:
        receipt_path.unlink(missing_ok=True)
        rollback = _rollback_version_bound_local_tunnel(
            tunnel=tunnel, data_root=exact_data_root
        )
        failure_path = (
            authority_root
            / "tunnel"
            / f"LOCAL_TUNNEL_FINAL_PROMOTION_FAILURE_{str(stage.get('archive_sha256') or '')[:16]}.json"
        )
        failure = {
            "schema": LOCAL_TUNNEL_FAILURE_SCHEMA,
            "status": "FAIL",
            "state": "FINAL_RESTART_RECEIPT_PROMOTION_FAILED_NO_RESTART_AUTHORITY",
            "plugin_version": target_version,
            "pending_receipt_path": str(exact_pending),
            "pending_receipt_sha256": pending_file_sha256,
            "tunnel_activation_receipt_path": tunnel["receipt_path"],
            "tunnel_activation_receipt_file_sha256": tunnel["receipt_file_sha256"],
            "rollback": rollback,
            "restart_authority_created": False,
            "remote_crud_invoked": False,
        }
        _write_self_sealed_json(failure_path, failure)
        raise InstallationError(
            "The final local restart receipt could not be promoted; tunnel rollback "
            "completed and no restart authority remains."
        ) from exc
    return {
        **sealed,
        "receipt_path": str(receipt_path.resolve()),
        "receipt_file_sha256": _sha256(receipt_path),
    }


def install(args: argparse.Namespace) -> dict[str, Any]:
    archive = args.archive.resolve()
    receipt_argument = getattr(args, "package_receipt", None) or getattr(
        args, "rehearsal_receipt", None
    )
    if receipt_argument is None:
        raise InstallationError("An exact package receipt is required.")
    receipt_path = Path(receipt_argument).resolve()
    codex_home = args.codex_home.resolve()
    data_root = args.data_root.resolve()
    requested_marketplace_name = str(
        getattr(args, "marketplace_name", MARKETPLACE_NAME)
    )
    if _inside(archive, codex_home / "plugins" / "cache"):
        raise InstallationError(
            "A generated cache package cannot be installation input."
        )
    rehearsal = _load_receipt(
        receipt_path,
        archive,
        activation=bool(args.activate),
        local_test_activation=False,
    )
    release_authority_path = getattr(args, "release_authority_receipt", None)
    release_authority_file_sha256 = getattr(
        args, "release_authority_receipt_sha256", None
    )
    if (release_authority_path is None) != (release_authority_file_sha256 is None):
        raise InstallationError(
            "The governed Git/CI/Vercel release-authority receipt and file SHA-256 "
            "must be supplied together."
        )
    release_authority: dict[str, Any] | None = None
    if args.activate:
        if release_authority_path is None:
            raise InstallationError(
                "A local rehearsal may be staged but activation requires the "
                "governed Git/CI/Vercel release-authority receipt."
            )
        release_authority = _load_release_authority(
            authority_path=Path(release_authority_path).resolve(),
            authority_file_sha256=str(release_authority_file_sha256),
            archive=archive,
            package_receipt_path=receipt_path,
            package_receipt=rehearsal,
        )
    baseline_path = getattr(args, "baseline_installation_receipt", None)
    baseline_sha256 = getattr(args, "baseline_installation_receipt_sha256", None)
    if (baseline_path is None) != (baseline_sha256 is None):
        raise InstallationError(
            "The comparison baseline receipt and SHA-256 must be supplied together."
        )
    comparison_surface: dict[str, Any] | None = None
    comparison_baseline: dict[str, Any] | None = None
    if baseline_path is not None:
        comparison_surface, comparison_baseline = _load_comparison_baseline(
            path=Path(baseline_path),
            expected_sha256=str(baseline_sha256),
            data_root=data_root,
        )
    # A staging-only local rehearsal validates and materializes only the new
    # sealed package.  It must not require, compare, or mutate the live
    # stable-main/local-testing two-slot authority. That authority is promotion state
    # and remains mandatory for an actual governed activation.
    activation_executable: Path | None = None
    two_slot_bootstrap: dict[str, Any] | None = None
    if args.activate:
        activation_executable = _resolve_codex_cli_executable(
            getattr(args, "codex_executable", None),
            verify_version=False,
        )
        registry_path = (
            data_root
            / "installations"
            / "codex-v300"
            / "two-slot-main-local"
            / "CODEX_TWO_SLOT_MAIN_LOCAL_REGISTRY.json"
        )
        if not registry_path.is_file():
            if comparison_baseline is None or comparison_surface is None:
                raise InstallationError(
                    "First stable activation requires the sealed active-local baseline."
                )
            plugin_list_before = _run_codex(
                activation_executable,
                codex_home,
                ["plugin", "list", "--json"],
            )
            two_slot_bootstrap = _bootstrap_two_slot_update_authority(
                data_root=data_root,
                codex_home=codex_home,
                plugin_list=plugin_list_before,
                comparison_baseline=comparison_baseline,
                comparison_surface=comparison_surface,
            )
        two_slot_authority = _load_two_slot_update_authority(
            data_root=data_root,
            comparison_baseline=comparison_baseline,
        )
    else:
        two_slot_authority = None
    if requested_marketplace_name == LOCAL_TESTING_MARKETPLACE_NAME:
        if args.activate:
            raise InstallationError(
                "The local testing selector cannot use the Git-stable activation route."
            )
        marketplace_name = LOCAL_TESTING_MARKETPLACE_NAME
        if rehearsal.get("executable_fingerprint_refresh") is not None:
            executable_fingerprint_refresh = (
                _require_local_executable_fingerprint_refresh(rehearsal)
            )
            systemwide_route_audit = None
        else:
            systemwide_route_audit = _require_local_systemwide_route_audit(rehearsal)
            executable_fingerprint_refresh = None
    else:
        marketplace_name = _resolve_stable_marketplace_name(
            requested_name=requested_marketplace_name,
            two_slot_authority=two_slot_authority,
        )
        systemwide_route_audit = None
        executable_fingerprint_refresh = None
    plugin_selector = f"{PLUGIN_NAME}@{marketplace_name}"
    marketplace_root = codex_home / "local-marketplaces" / marketplace_name
    extracted_inventory: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="evidence-lane-v300-install-") as raw:
        extracted = Path(raw) / "plugin"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        identity = _validate_plugin(extracted)
        extracted_inventory = _source_inventory(extracted)
        if args.activate:
            stage = {
                "schema": "evidence-lane.codex-git-marketplace-stage.v1",
                "state": "EXACT_COMMIT_GIT_MARKETPLACE_PENDING",
                "marketplace": MARKETPLACE_NAME,
                "marketplace_display_name": MARKETPLACE_DISPLAY_NAME,
                "repository": MARKETPLACE_SOURCE,
                "plugin": identity,
                "archive_sha256": _sha256(archive),
                "comparison_baseline": comparison_baseline,
                "surface_change_display": _surface_change_display(
                    previous=comparison_surface,
                    current=identity["surface_inventory"],
                ),
                "exact_commit_package_inventory_sha256": extracted_inventory[
                    "manifest_sha256"
                ],
                "local_marketplace_staged": False,
                "generated_cache_written_directly": False,
                "prior_release_deleted": False,
            }
        else:
            stage = _stage_marketplace(
                extracted=extracted,
                marketplace_root=marketplace_root,
                data_root=data_root,
                identity=identity,
                archive_sha256=_sha256(archive),
                marketplace_name=marketplace_name,
                comparison_surface=comparison_surface,
                comparison_baseline=comparison_baseline,
            )
    activation: dict[str, Any] = {
        "state": "STAGED_RESTART_NOT_YET_REQUIRED",
        "plugin_add_invoked": False,
    }
    config_receipt: dict[str, Any] | None = None
    plugin_list: dict[str, Any] | None = None
    installed_path: Path | None = None
    in_place_update: dict[str, Any] | None = None
    runtime_prewarm: dict[str, Any] | None = None
    git_marketplace_source: dict[str, Any] | None = None
    post_proof_cleanup: dict[str, Any] | None = None
    hook_event_isolation: dict[str, Any] | None = None
    two_slot_main_local_registry: dict[str, Any] | None = two_slot_bootstrap
    if args.activate:
        if activation_executable is None:
            raise InstallationError("Activation executable was not resolved.")
        executable = activation_executable
        if two_slot_authority is None:
            raise InstallationError(
                "Activation requires the materialized stable-main/local-testing "
                "two-slot authority."
            )
        in_place_update = _prepare_in_place_stable_reinstall(
            executable=executable,
            codex_home=codex_home,
            plugin_selector=plugin_selector,
            marketplace_name=marketplace_name,
            two_slot_authority=two_slot_authority,
        )
        listed = _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "list", "--json"],
        )
        known = {
            row["name"]: Path(row["root"]).resolve() for row in listed["marketplaces"]
        }
        if marketplace_name in known:
            raise InstallationError(
                "The canonical Git marketplace still exists after update preflight."
            )
        if release_authority is None:
            raise InstallationError("The exact release authority was not loaded.")
        source_commit = str(release_authority["source"]["commit"])
        marketplace_add = _run_codex(
            executable,
            codex_home,
            [
                "plugin",
                "marketplace",
                "add",
                MARKETPLACE_SOURCE,
                "--ref",
                source_commit,
                "--json",
            ],
        )
        refreshed_marketplaces = _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "list", "--json"],
        )
        refreshed_rows = refreshed_marketplaces.get("marketplaces")
        if not isinstance(refreshed_rows, list):
            raise InstallationError("Codex marketplace list has an invalid shape.")
        exact_rows = [
            dict(row)
            for row in refreshed_rows
            if isinstance(row, dict) and row.get("name") == MARKETPLACE_NAME
        ]
        if len(exact_rows) != 1:
            raise InstallationError(
                "Codex did not configure one canonical Git marketplace."
            )
        marketplace_root = Path(str(exact_rows[0].get("root") or "")).resolve()
        git_marketplace_source = _assert_exact_git_marketplace_source(
            extracted_inventory=extracted_inventory,
            marketplace_root=marketplace_root,
            expected_git_manifest_sha256=str(
                rehearsal.get("exact_commit_export", {}).get(
                    "plugin_source_manifest_sha256"
                )
                or ""
            ),
            expected_git_file_count=int(
                rehearsal.get("exact_commit_export", {}).get(
                    "plugin_source_member_count"
                )
                or 0
            ),
            source_version=str(rehearsal.get("source_version") or ""),
            package_version=str(rehearsal.get("package_version") or ""),
        )
        git_marketplace_source["commit"] = source_commit
        plugin_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "add", plugin_selector, "--json"],
        )
        installed_path = Path(str(plugin_add.get("installedPath") or "")).resolve()
        expected_cache = (
            codex_home / "plugins" / "cache" / marketplace_name / PLUGIN_NAME
        )
        if (
            plugin_add.get("pluginId") != plugin_selector
            or plugin_add.get("version") != identity["version"]
            or not _inside(installed_path, expected_cache)
        ):
            raise InstallationError(
                "Codex installed a mismatched plugin cache identity."
            )
        runtime_prewarm = _prewarm_installed_runtime(
            installed_path,
            data_root=data_root,
            expected_plugin_version=str(identity["version"]),
        )
        hook_event_isolation = _initialize_installed_hook_event_isolation(
            installed_path,
            data_root=data_root,
            installation_id=("stable_install_" + str(_sha256(archive))[:40].lower()),
        )
        hook_trust = {
            "schema": HOOK_TRUST_SCHEMA,
            "status": "PENDING_NATIVE_POST_RESTART_VERIFICATION",
            "plugin_selector": plugin_selector,
            "hook_count": len(EXPECTED_CODEX_HOST_HOOK_EVENTS),
            "records": [],
            "all_enabled": False,
            "hooks_enabled_by_update": False,
            "native_post_restart_verification_required": True,
        }
        config_receipt = {
            "status": "NOT_MUTATED_BY_INSTALLER",
            "native_post_restart_hook_control_required": True,
        }
        plugin_list = _run_codex(
            executable,
            codex_home,
            ["plugin", "list", "--json"],
        )
        installed_rows = plugin_list.get("installed")
        if not isinstance(installed_rows, list):
            raise InstallationError(
                "Codex plugin list did not expose installed plugins."
            )
        exact_installed = [
            dict(row)
            for row in installed_rows
            if isinstance(row, dict) and row.get("pluginId") == plugin_selector
        ]
        if len(exact_installed) != 1:
            raise InstallationError(
                "Codex did not expose one canonical installed plugin."
            )
        marketplace_source = dict(exact_installed[0].get("marketplaceSource") or {})
        if (
            marketplace_source.get("sourceType") != "git"
            or MARKETPLACE_SOURCE.lower()
            not in str(marketplace_source.get("source") or "").lower()
        ):
            raise InstallationError("The installed stable route is not Git-backed.")
        post_proof_cleanup, plugin_list = _cleanup_obsolete_after_new_route_proof(
            executable=executable,
            codex_home=codex_home,
            plugin_selector=plugin_selector,
            local_testing_selector=TWO_SLOT_SELECTORS["versioned-local-testing"],
        )
        activation = {
            "state": "INSTALLED_RESTART_REQUIRED",
            "plugin_add_invoked": True,
            "marketplace_add": marketplace_add,
            "plugin_add": plugin_add,
            "exclusive_channel": config_receipt,
            "hook_trust": hook_trust,
            "hook_event_isolation": hook_event_isolation,
            "in_place_update": in_place_update,
            "runtime_prewarm": runtime_prewarm,
            "git_marketplace_source": git_marketplace_source,
            "post_proof_cleanup": post_proof_cleanup,
            "runtime_ready_before_task_reopen": True,
            "prompt_capture_runnable_after_restart": True,
            "hot_reload_claimed": False,
        }
    body = {
        "schema": INSTALL_SCHEMA,
        "status": "PASS",
        "plugin": identity,
        "catalog_expected": dict(EXPECTED_CATALOG),
        "package_receipt_sha256": _sha256(receipt_path),
        "systemwide_route_audit": systemwide_route_audit,
        "executable_fingerprint_refresh": executable_fingerprint_refresh,
        "activation_authority": (
            {
                "status": release_authority["status"],
                "boundary": release_authority["boundary"],
                "source_commit": release_authority["source"]["commit"],
                "source_tree": release_authority["source"]["tree"],
                "branch": release_authority["source"]["branch"],
                "github_repository": release_authority["github_ci"]["repository"],
                "vercel_preview_deployment_id": release_authority["vercel_preview"][
                    "deployment_id"
                ],
                "vercel_preview_url": release_authority["vercel_preview"]["url"],
                "vercel_preview_ready": True,
                "production_deployment": False,
                "receipt_sha256": release_authority["receipt_sha256"],
                "receipt_file_sha256": str(release_authority_file_sha256 or "").upper(),
            }
            if release_authority is not None
            else {
                "status": "NOT_APPLICABLE",
                "reason": "STAGING_ONLY_LOCAL_REHEARSAL",
            }
        ),
        "archive_sha256": _sha256(archive),
        "marketplace": {
            "name": marketplace_name,
            "root": str(marketplace_root),
            **stage,
        },
        "surface_change_display": stage["surface_change_display"],
        "comparison_baseline": stage.get("comparison_baseline"),
        "activation": activation,
        "two_slot_main_local_registry": two_slot_main_local_registry,
        "live_slot_contract": {
            "schema": TWO_SLOT_REGISTRY_SCHEMA,
            "status": ("PASS" if args.activate else "NOT_APPLICABLE"),
            "exact_live_slot_count": 2,
            "stable_slot": "stable-git-main",
            "stable_selector": TWO_SLOT_SELECTORS["stable-git-main"],
            "local_slot": "versioned-local-testing",
            "local_testing_selector": TWO_SLOT_SELECTORS["versioned-local-testing"],
            "obsolete_selector_present": False,
            "pre_3_0_fallback_allowed": False,
        },
        "previous_release_cache_deleted": False,
        "stable_selector_reused": bool(
            two_slot_authority is not None
            and two_slot_authority.get("prior_stable_selector") == plugin_selector
        ),
        "stable_selector_migrated_to_canonical_git": False,
        "post_proof_obsolete_cleanup_completed": bool(
            post_proof_cleanup and post_proof_cleanup.get("status") == "PASS"
        ),
        "obsolete_cleanup_used_supported_codex_apis": bool(args.activate),
        "stable_main_identity_location": "SEALED_INSTALL_RECEIPT_NOT_SELECTOR",
        "new_stable_selector_created": False,
        "generated_cache_written_directly": False,
        "credential_requested_or_stored": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_required": bool(args.activate),
        "runtime_ready_before_task_reopen": bool(
            activation.get("runtime_ready_before_task_reopen") is True
        ),
    }
    body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    receipt_dir = data_root / "installations" / "codex-v300"
    install_receipt = receipt_dir / f"INSTALL_{body['archive_sha256'][:16]}.json"
    _write_atomic(install_receipt, _json_bytes(body))
    _write_atomic(receipt_dir / "CURRENT_INSTALLATION.json", _json_bytes(body))
    two_slot_update = (
        _advance_two_slot_stable_registry(
            authority=two_slot_authority,
            plugin_selector=plugin_selector,
            plugin_version=str(identity["version"]),
            installed_path=installed_path,
            marketplace_root=marketplace_root,
            install_receipt=install_receipt,
            archive_sha256=str(body["archive_sha256"]),
            source_manifest_sha256=str(
                rehearsal.get("working_source_manifest_sha256") or ""
            ),
            codex_home=codex_home,
            data_root=data_root,
            plugin_list=plugin_list,
        )
        if args.activate and installed_path is not None and plugin_list is not None
        else {
            "status": "NOT_APPLICABLE",
            "reason": "STAGING_PASS_ONLY",
        }
    )
    return {
        **body,
        "receipt_path": str(install_receipt),
        "two_slot_registry_update": two_slot_update,
    }


def _load_exact_commit_package_receipt(
    *,
    receipt_path: Path,
    receipt_file_sha256: str,
    archive: Path,
    package_receipt_path: Path,
    data_root: Path,
) -> dict[str, Any]:
    """Verify one immutable exact-main package before stable-slot mutation."""

    exact_path = receipt_path.resolve()
    expected_file_sha256 = receipt_file_sha256.strip().upper()
    authority_root = (
        data_root.resolve() / "installations" / "codex-v300" / "exact-commit-packages"
    )
    if (
        not exact_path.is_file()
        or not _inside(exact_path, authority_root)
        or not re.fullmatch(r"[0-9A-F]{64}", expected_file_sha256)
        or _sha256(exact_path) != expected_file_sha256
    ):
        raise InstallationError(
            "The exact-commit package receipt is absent, outside durable authority, "
            "or drifted."
        )
    receipt = json.loads(exact_path.read_text(encoding="utf-8"))
    internal_sha256 = str(receipt.get("receipt_sha256") or "").upper()
    core = dict(receipt)
    core.pop("receipt_sha256", None)
    export = dict(receipt.get("exact_commit_export") or {})
    packaged_archive = dict(receipt.get("archive") or {})
    package_receipt = json.loads(package_receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("schema") != "evidence-lane.codex-exact-commit-package.v1.receipt"
        or receipt.get("boundary") != "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
        or receipt.get("status") != "PASS"
        # Exact-commit package receipts are emitted by
        # build_codex_exact_commit_package._json_bytes: sorted keys plus one
        # trailing newline.  Do not reuse the preserved-order registry seal
        # here; parsing sorted JSON and then hashing a different serialization
        # rejects an otherwise intact builder receipt.
        or internal_sha256 != hashlib.sha256(_json_bytes(core)).hexdigest().upper()
        or packaged_archive.get("sha256") != _sha256(archive)
        or packaged_archive.get("sha256")
        != dict(package_receipt.get("archive") or {}).get("sha256")
        or receipt.get("local_rehearsal_receipt_sha256")
        != _sha256(package_receipt_path)
        or re.fullmatch(r"[0-9a-f]{40}", str(export.get("commit") or "")) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(export.get("tree") or "")) is None
        or export.get("branch") != "main"
        or export.get("source_ref") != "refs/remotes/origin/main"
        or export.get("stable_main_only") is not True
        or export.get("local_main_attested") is not True
        or export.get("origin_main_attested") is not True
        or export.get("projection_clean") is not True
        or export.get("working_checkout_bytes_used") is not False
        or export.get("untracked_bytes_used") is not False
        or receipt.get("git_write_invoked") is not False
        or receipt.get("governed_candidate_created") is not False
        or receipt.get("accepted_pointer_moved") is not False
        or receipt.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "The exact-commit package receipt does not satisfy the verified-main "
            "stable-delivery boundary."
        )
    return {
        **receipt,
        "receipt_path": str(exact_path),
        "receipt_file_sha256": expected_file_sha256,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    package = parser.add_mutually_exclusive_group()
    package.add_argument("--package-receipt", type=Path)
    package.add_argument(
        "--rehearsal-receipt",
        type=Path,
        help="Compatibility name for staging-only local rehearsal receipts.",
    )
    parser.add_argument(
        "--release-authority-receipt",
        type=Path,
        help=(
            "Sealed exact-commit governed-push and successful-GitHub-CI "
            "authority. Required for activation; never required for staging."
        ),
    )
    parser.add_argument("--release-authority-receipt-sha256")
    parser.add_argument("--baseline-installation-receipt", type=Path)
    parser.add_argument("--baseline-installation-receipt-sha256")
    parser.add_argument(
        "--marketplace-name",
        default=MARKETPLACE_NAME,
        help=(
            "Compatibility input for the one stable marketplace selector. "
            "A different build-specific selector is rejected; after PV11 the "
            "sealed two-slot registry supplies the persistent stable identity."
        ),
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.environ.get("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT")
            or Path.home() / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
        ),
        help=(
            "Hidden plugin runtime-control root. Project/PV authority is bound "
            "separately by project_register and must not be stored here."
        ),
    )
    parser.add_argument(
        "--codex-executable",
        type=Path,
        help=(
            "Optional npm-native Codex CLI override. When omitted, the installer "
            "uses the supported @openai/codex npm runtime; WindowsApps binaries "
            "are rejected before mutation."
        ),
    )
    parser.add_argument("--activate", action="store_true")
    parser.add_argument(
        "--seal-plugin-creator-local-cache-restart",
        type=Path,
        help=(
            "Run codex plugin add once for one staged local update, verify the "
            "fresh cache beside the loaded prior cache, and seal exact-task restart."
        ),
    )
    parser.add_argument("--seal-plugin-creator-local-cache-restart-sha256")
    parser.add_argument("--confirm-plugin-creator-local-cache-restart")
    parser.add_argument(
        "--precreated-tunnel-id",
        help=(
            "Optional already-provisioned tunnel ID for the local version. This "
            "does not authorize remote tunnel CRUD and is never written in receipts."
        ),
    )
    parser.add_argument(
        "--force-local-tunnel-key-entry",
        action="store_true",
        help=(
            "Force one visible Runtime-key recapture for this local materialization. "
            "Normally a compatible capability-bound encrypted envelope is reused."
        ),
    )
    parser.add_argument(
        "--hook-cwd",
        type=Path,
        default=Path.cwd(),
        help="Exact governed workspace used for Codex hook discovery.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    args.data_root = _require_hidden_runtime_control_root(args.data_root)
    plugin_creator_restart_requested = (
        args.seal_plugin_creator_local_cache_restart is not None
    )
    if (args.seal_plugin_creator_local_cache_restart is None) != (
        args.seal_plugin_creator_local_cache_restart_sha256 is None
    ):
        raise InstallationError(
            "Plugin Creator local cache materialization requires its staging "
            "receipt path and file SHA-256 together."
        )
    if args.activate or plugin_creator_restart_requested:
        args.codex_executable = _resolve_codex_cli_executable(
            args.codex_executable,
            verify_version=True,
        )
    if plugin_creator_restart_requested:
        if (
            args.confirm_plugin_creator_local_cache_restart
            != PLUGIN_CREATOR_LOCAL_CACHE_RESTART_CONFIRMATION
        ):
            raise InstallationError(
                "Plugin Creator local cache materialization requires the exact "
                "staging receipt seal, Codex CLI, and confirmation token."
            )
        if (
            args.archive is not None
            or args.package_receipt is not None
            or args.rehearsal_receipt is not None
            or args.activate
        ):
            raise InstallationError(
                "Plugin Creator cache materialization is a separate current-route "
                "operation and cannot be mixed with staging or stable activation."
            )
        result = _seal_plugin_creator_local_cache_restart(
            stage_receipt_path=args.seal_plugin_creator_local_cache_restart,
            stage_receipt_sha256=(args.seal_plugin_creator_local_cache_restart_sha256),
            executable=args.codex_executable,
            codex_home=args.codex_home,
            data_root=args.data_root,
            precreated_tunnel_id=args.precreated_tunnel_id,
            force_local_tunnel_key_entry=args.force_local_tunnel_key_entry,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.archive is None or (
        args.package_receipt is None and args.rehearsal_receipt is None
    ):
        raise InstallationError(
            "Staging or activation requires --archive and exactly one package receipt."
        )
    if args.precreated_tunnel_id is not None:
        raise InstallationError(
            "--precreated-tunnel-id is allowed only for Plugin Creator local cache materialization."
        )
    if args.force_local_tunnel_key_entry:
        raise InstallationError(
            "--force-local-tunnel-key-entry is allowed only for Plugin Creator local cache materialization."
        )
    if args.activate and args.codex_executable is None:
        raise InstallationError("Activation requires --codex-executable.")
    if args.activate and (
        args.release_authority_receipt is None
        or args.release_authority_receipt_sha256 is None
    ):
        raise InstallationError(
            "--activate requires the governed Git/CI/Vercel release-authority receipt "
            "and its file SHA-256."
        )
    print(json.dumps(install(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
