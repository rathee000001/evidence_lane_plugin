"""Stage and activate one sealed Evidence Lane 2.2 Codex marketplace package.

The script uses the supported Codex marketplace and plugin commands. Stable
package bytes are reinstalled under one persistent stable selector; build
hashes belong in receipts, never in new plugin identities.  The separately
sealed PV12 fallback selector remains installed and disabled.  The script never
writes the generated plugin cache directly and never asks for or stores Git,
OpenAI, OAuth, PAT, or tunnel credentials.
"""

from __future__ import annotations

import argparse
import ast
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

BASE_RELEASE = "2.2.0"
MARKETPLACE_NAME = "evidence-lane-github"
MARKETPLACE_DISPLAY_NAME = "Main Git Plugin Version"
LOCAL_TESTING_MARKETPLACE_NAME = "evidence-lane-v220-testing-new"
LOCAL_TESTING_MARKETPLACE_DISPLAY_NAME = "Local Testing Slot"
LOCAL_RECOVERY_MARKETPLACE_NAME = "evidence-lane-v220-stable-recovery"
LOCAL_RECOVERY_MARKETPLACE_DISPLAY_NAME = "Branch Commit Git Recovery"
LOCAL_RECOVERY_SELECTOR = f"evidence-lane-plugin@{LOCAL_RECOVERY_MARKETPLACE_NAME}"
LOCAL_RECOVERY_REGISTRY_SCHEMA = "evidence-lane.codex-local-v220-recovery-registry.v1"
LOCAL_SUCCESSOR_MARKETPLACE_NAME = "evidence-lane-v220-local-successor"
LOCAL_SUCCESSOR_MARKETPLACE_DISPLAY_NAME = "Local 2.2 Verified Successor"
LOCAL_SUCCESSOR_SELECTOR = (
    f"evidence-lane-plugin@{LOCAL_SUCCESSOR_MARKETPLACE_NAME}"
)
LOCAL_SUCCESSOR_STAGE_SCHEMA = "evidence-lane.codex-local-successor-stage.v1"
LOCAL_TEST_STAGE_SCHEMA = "evidence-lane.codex-local-test-cas-transaction.v1"
LOCAL_TEST_HOST_PROOF_SCHEMA = "evidence-lane.codex-local-test-host-proof.v1"
LOCAL_TEST_COMMIT_SCHEMA = "evidence-lane.codex-local-test-cas-commit.v1"
LOCAL_TEST_RECOVERY_SCHEMA = "evidence-lane.codex-local-test-self-rollback.v1"
LOCAL_TEST_RECOVERY_STATE_SCHEMA = (
    "evidence-lane.codex-local-test-self-rollback-state.v1"
)
LOCAL_TEST_RECOVERY_MAX_ATTEMPTS = 2
LOCAL_TEST_DISABLED_HOOK_RECOVERY_SCHEMA = (
    "evidence-lane.codex-local-test-disabled-hook-recovery.v1"
)
LOCAL_TEST_DISABLED_HOOK_RECOVERY_CONFIRMATION = (
    "EXPLICIT_DISABLED_LOCAL_2_2_HOOK_RECOVERY"
)
INSTALL_CORRECTION_GENERATION_SCHEMA = (
    "evidence-lane.codex-install-correction-generation.v1"
)
MARKETPLACE_SOURCE = "rathee000001/evidence_lane_plugin"
PLUGIN_NAME = "evidence-lane-plugin"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
GENERATION_NEUTRAL_FALLBACK_SELECTOR = f"{PLUGIN_NAME}@evidence-lane-fallback"
LEGACY_FALLBACK_SELECTOR_RE = re.compile(
    rf"^{re.escape(PLUGIN_NAME)}@evidence-lane-pv[1-9][0-9]*-fallback$"
)
EXPECTED_CATALOG = {"tools": 83, "read": 26, "write": 57, "skills": 17}
EXPECTED_WORKFLOW_SCOPE = {
    "plugin_release_cadence": "ONE_AUTHORIZED_LOGICAL_RELEASE_COMMIT_BATCH",
    "plugin_release_steps": [
        "GOVERNED_EXACT_BRANCH_COMMIT_PUSH",
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
        "ci_prerequisite_row": 196,
        "execution_row": 197,
        "release": BASE_RELEASE,
        "branch": "agent/evi-v220-systemwide-release-hil-v2.2.0",
        "source": (
            "EXACT_GIT_COMMIT_AFTER_REQUIRED_CLEAN_CI_AND_"
            "GIT_TRIGGERED_VERCEL_PREVIEW"
        ),
        "slot_role": "stable-build",
        "plugin_selector": PLUGIN_SELECTOR,
        "installed_version_must_equal_exact_package_version": True,
        "installed_catalog_must_equal": {
            "native_actions": 83,
            "read_actions": 26,
            "write_actions": 57,
            "governed_skills": 17,
            "hook_events": 8,
            "migrated_command_skills": 1,
        },
        "installed_ui_readback_required_before_pv13_hil": True,
        "fallback_mutation_allowed": False,
        "main_merge_allowed": False,
        "downstream_project_inherits_install": False,
    },
    "full_vercel_guide_refresh": "ASSIGNED_WEBSITE_DELTA_ONLY",
    "plan_or_pv_projection_update_is_full_site_refresh": False,
    "env_uop_evolution_requires_new_sealed_identity": True,
    "accepted_locked_env_uop_mutation_allowed": False,
}
EXPECTED_GOAL_COMPLETION_POLICY = {
    "schema": "evidence-lane.human-goal-completion-policy.v1",
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
}
EXPECTED_HELPER_DISTRIBUTION_POLICY = {
    "schema": "evidence-lane.helper-distribution-policy.v1",
    "maintainer_release_helper": {
        "script": "scripts/codex_release/Restart-EvidenceLaneCodex.ps1",
        "audience": "EVIDENCE_LANE_MAINTAINER_ONLY",
        "used_in_governed_development_build": True,
        "public_marketplace_user_surface": False,
        "release_bound": True,
        "supported_update_modes": [
            "RestartInstalledMainGit",
            "RestartInstalledLocalTesting",
            "RestartInstalledBranchRecovery",
        ],
        "install_completed_before_helper": True,
        "helper_installs_plugin": False,
        "same_local_testing_marketplace_new_version_required": True,
        "single_flight": True,
        "exact_app_stop_count": 1,
        "exact_task_reopen_count": 1,
        "fixed_delay_allowed": False,
        "condition_driven_readiness_polling": True,
        "version_matched_tunnel_starts_before_host": True,
        "maximized_full_window_required": True,
        "combined_post_stop_installer_retired": True,
    },
    "user_goal_recovery_helper": {
        "script": "scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1",
        "audience": "GOVERNED_CODEX_USER",
        "public_marketplace_user_surface": True,
        "release": BASE_RELEASE,
        "release_token": "v220",
        "scheduled_task_name": "Evidence Lane Codex Goal Recovery v220",
        "at_logon": True,
        "persistent_or_hidden_no_transient_console": True,
        "prior_versions_retained": True,
        "prior_versions_disabled": True,
        "prior_versions_deleted": False,
        "manager_scope": "SHARED_MULTI_PROJECT_MULTI_TASK",
        "binding_registry": "MUTABLE_EXACT_TASK_ROWS",
        "each_invocation_reopens_its_exact_calling_task": True,
        "release_registry_origin_identity_is_task_authority": False,
        "installer_helper_is_separate": True,
        "tunnel_is_separate": True,
    },
    "user_stable_tunnel": {
        "script": "scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1",
        "audience": "GOVERNED_CODEX_USER",
        "public_marketplace_user_surface": True,
        "release": BASE_RELEASE,
        "release_token": "v220",
        "runtime_root_suffix": "tunnel-runtime-v220-stable-build",
        "scheduled_task_name": "EvidenceLane-Tunnel-v220-stable-build",
        "at_logon": True,
        "persistent_or_hidden_no_transient_console": True,
        "one_active_version": True,
        "prior_versions_retained": True,
        "prior_versions_deleted": False,
    },
    "branch_recovery_transport": {
        "audience": "MAINTAINER_RECOVERY_ONLY",
        "release": BASE_RELEASE,
        "release_token": "v220",
        "runtime_root_suffix": "tunnel-runtime-v220-stable-build",
        "scheduled_task_name": "EvidenceLane-Tunnel-v220-stable-build",
        "target_slot_restaged_before_switch": True,
        "one_version_matched_tunnel": True,
        "public_marketplace_user_surface": False,
    },
    "post_hil_release_rotation": {
        "schema": "evidence-lane.plugin-slot-helper-tunnel-rotation.v1",
        "applies_to_plugin_maintainer_route_only": True,
        "downstream_project_inherits_rotation": False,
        "local_test_green_branch_checkpoint_can_converge_all_three_slots": True,
        "pre_2_2_fallback_allowed": False,
        "final_gate": "PV14_EXACT_HUMAN_APPROVE_AND_FUSE",
        "required_order": [
            "FUSE_EXACT_ACCEPTED_PLUGIN_PV",
            "GOVERNED_NON_FORCE_MAIN_PROMOTION",
            "VERIFY_MAIN_EQUALS_ACCEPTED_COMMIT",
            "INSTALL_ACCEPTED_RELEASE_IN_MAIN_GIT_SLOT",
            "INSTALL_SAME_ACCEPTED_RELEASE_IN_BRANCH_RECOVERY_SLOT",
            "INSTALL_SAME_ACCEPTED_RELEASE_IN_LOCAL_TESTING_SLOT",
            "ROTATE_MATCHING_HELPER_AND_TUNNEL_IDENTITIES",
            "VERIFY_THREE_BYTE_IDENTICAL_SLOTS_AND_ONE_ACTIVE_RUNTIME",
        ],
        "all_three_slots_must_equal_exact_checkpoint_release": True,
        "helper_and_tunnel_release_must_match_owning_slot": True,
        "prior_versioned_helpers_and_tunnels_retained": True,
        "prior_versioned_helpers_and_tunnels_disabled": True,
        "prior_versioned_helpers_and_tunnels_deleted": False,
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
    "stable_update_helper": "scripts/codex_release/Restart-EvidenceLaneCodex.ps1",
    "install_completed_before_restart_helper": True,
    "restart_helper_installs_plugin": False,
    "stable_update_reopens_same_bound_host_app": True,
    "stable_update_rebinds_general_goal_recovery": True,
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
    "stable_install_source": "EXACT_GIT_COMMIT_PACKAGE_ONLY",
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
        "tunnel_requirement": "NOT_REQUIRED_FOR_LOCAL_CODEX_NATIVE_LAYER",
        "tunnel_setup_frequency": "NONE",
        "tunnel_key_retention": "NOT_APPLICABLE",
        "tunnel_runtime_lifetime": "NOT_APPLICABLE",
    },
    "interactive_codex_app_ephemeral_vm": {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
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
INSTALL_SCHEMA = "evidence-lane.codex-stable-installation.v2"
HOOK_TRUST_SCHEMA = "evidence-lane.codex-hook-trust.v1"
EXPECTED_CODEX_HOST_HOOK_EVENTS = {
    "postCompact",
    "postToolUse",
    "preCompact",
    "preToolUse",
    "sessionEnd",
    "sessionStart",
    "stop",
    "userPromptSubmit",
}
EXPECTED_PACKAGE_HOOK_EVENTS = {
    "PostCompact",
    "PostToolUse",
    "PreCompact",
    "PreToolUse",
    "SessionEnd",
    "SessionStart",
    "Stop",
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
    """

    rows = []
    ignored_python_runtime_artifacts = 0
    for path in sorted(row for row in root.rglob("*") if row.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("_evidence_lane_rehearsal/"):
            continue
        if "__pycache__" in relative.split("/") or path.suffix.lower() in {
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
    }


def _assert_exact_git_marketplace_source(
    *,
    extracted_inventory: dict[str, Any],
    marketplace_root: Path,
    expected_git_manifest_sha256: str,
    expected_git_file_count: int,
) -> dict[str, Any]:
    plugin_root = marketplace_root / "plugins" / PLUGIN_NAME
    if not plugin_root.is_dir():
        raise InstallationError("The Git marketplace lacks the Evidence Lane plugin root.")
    marketplace_inventory = _source_inventory(plugin_root)
    normalized_expected_manifest = expected_git_manifest_sha256.strip().upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", normalized_expected_manifest) is None
        or not isinstance(expected_git_file_count, int)
        or expected_git_file_count < 1
        or marketplace_inventory["file_count"] != expected_git_file_count
        or marketplace_inventory["manifest_sha256"]
        != normalized_expected_manifest
    ):
        raise InstallationError(
            "The Git marketplace bytes do not match the complete exact Git commit tree."
        )
    marketplace_by_path = {
        str(row["path"]): row for row in marketplace_inventory["files"]
    }
    package_files = extracted_inventory.get("files")
    if not isinstance(package_files, list) or any(
        not isinstance(row, dict)
        or marketplace_by_path.get(str(row.get("path") or "")) != row
        for row in package_files
    ) or len(package_files) != extracted_inventory.get("file_count"):
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
    }


def _surface_inventory(plugin_root: Path, *, version: str) -> dict[str, Any]:
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        *sorted((plugin_root / "hooks").glob("*.exe")),
        *sorted((plugin_root / "hooks").glob("*.py")),
        *sorted((plugin_root / "hooks").glob("*.ps1")),
    ]
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
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
                "post_tool_use.py",
                "pre_tool_use.py",
                "prompt_submit.py",
                "session_start.py",
                "stop_response.py",
            }
        ),
    }:
        raise InstallationError("The persistent hook inventory is not exact.")
    if len(skill_paths) != EXPECTED_CATALOG["skills"]:
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
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in hook_events.values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    )
    hook_inventory = {
        "count": len(registered_events),
        "count_semantics": "REGISTERED_EVENT_COUNT",
        "registered_event_count": len(registered_events),
        "registered_events": registered_events,
        "handler_count": handler_count,
        "hook_file_count": hook_files["count"],
        "records": hook_files["records"],
        "file_inventory_sha256": hook_files["inventory_sha256"],
        "event_inventory_sha256": hashlib.sha256(
            _json_bytes(registered_events)
        ).hexdigest().upper(),
    }
    hook_inventory["inventory_sha256"] = hashlib.sha256(
        _json_bytes(hook_inventory)
    ).hexdigest().upper()
    release_channel_path = plugin_root / "scripts" / "codex-release-channel.json"
    try:
        release_channel = json.loads(release_channel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError("The Codex release-channel contract is missing.") from exc
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
        "catalog": dict(EXPECTED_CATALOG),
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    return core


def _historical_search_toolchain_absence() -> dict[str, Any]:
    body = {
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
        raise InstallationError("The governed search toolchain manifest is missing.") from exc
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
                raise InstallationError("A search tool escaped the plugin package.") from exc
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
            rows.append(
                {"name": name_node.value, "annotation": annotation_node.id}
            )
    sdk_actions: Any = None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
        )
        if not any(
            isinstance(target, ast.Name)
            and target.id == "SDK_NATIVE_ACTIONS"
            for target in targets
        ):
            continue
        try:
            sdk_actions = ast.literal_eval(node.value)
        except (TypeError, ValueError, SyntaxError) as exc:
            raise InstallationError(
                "SDK_NATIVE_ACTIONS must be one literal immutable catalog."
            ) from exc
        break
    if sdk_actions is not None and not isinstance(sdk_actions, tuple):
        raise InstallationError("The SDK native action catalog is invalid.")
    for action in sdk_actions or ():
        if not (
            isinstance(action, tuple)
            and len(action) == 6
            and all(isinstance(value, str) for value in action[:5])
            and isinstance(action[5], bool)
        ):
            raise InstallationError("An SDK native action declaration is invalid.")
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
        current_rows = {
            row["name"]: row["sha256"] for row in current[kind]["records"]
        }
        previous_rows = (
            {
                row["name"]: row["sha256"]
                for row in previous[kind]["records"]
            }
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
                    "registered_event_count": current[kind][
                        "registered_event_count"
                    ],
                    "registered_events": current[kind]["registered_events"],
                    "handler_count": current[kind]["handler_count"],
                    "hook_file_count": current[kind]["hook_file_count"],
                    "added_events": sorted(current_events - previous_events),
                    "removed_events": sorted(previous_events - current_events),
                    "file_inventory_sha256": current[kind][
                        "file_inventory_sha256"
                    ],
                    "event_inventory_sha256": current[kind][
                        "event_inventory_sha256"
                    ],
                }
            )
        return result

    current_search = dict(current["search_toolchain"])
    previous_search = (
        dict(previous.get("search_toolchain") or {})
        if previous is not None
        else {}
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
            previous.get("surface_inventory_sha256")
            if previous is not None
            else None
        ),
        "current_surface_inventory_sha256": current[
            "surface_inventory_sha256"
        ],
        "raw_paths_included": False,
        "private_research_question_included": False,
    }
    core["change_display_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
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
        value.get("schema")
        != "evidence-lane.codex-installed-surface-change-display.v2"
        or observed_sha256 != expected_sha256
        or value.get("current_plugin_version") != current["plugin_version"]
        or value.get("current_surface_inventory_sha256")
        != current["surface_inventory_sha256"]
        or value.get("raw_paths_included") is not False
        or value.get("private_research_question_included") is not False
        or hooks.get("count") != current["hooks"]["count"]
        or hooks.get("registered_event_count")
        != current["hooks"]["registered_event_count"]
        or hooks.get("registered_events")
        != current["hooks"]["registered_events"]
        or hooks.get("handler_count") != current["hooks"]["handler_count"]
        or hooks.get("hook_file_count") != current["hooks"]["hook_file_count"]
        or hooks.get("inventory_sha256")
        != current["hooks"]["inventory_sha256"]
        or skills.get("count") != current["skills"]["count"]
        or skills.get("inventory_sha256")
        != current["skills"]["inventory_sha256"]
        or search_toolchain.get("status")
        != current["search_toolchain"]["status"]
        or search_toolchain.get("record_count")
        != current["search_toolchain"]["record_count"]
        or search_toolchain.get("manifest_sha256")
        != current["search_toolchain"]["manifest_sha256"]
        or search_toolchain.get("inventory_sha256")
        != current["search_toolchain"]["inventory_sha256"]
        or search_toolchain.get("fts_authority")
        != current["search_toolchain"].get("fts_authority")
        or search_toolchain.get("records")
        != current["search_toolchain"]["records"]
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
        and exact_export.get("git_archive_member_count")
        == plugin_source_member_count
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
            and hashlib.sha256(_json_bytes(core)).hexdigest().upper()
            == receipt_sha256
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
    calculated_receipt_sha256 = hashlib.sha256(
        _json_bytes(authority_body)
    ).hexdigest().upper()
    source = dict(authority.get("source") or {})
    remote = dict(authority.get("remote_git") or {})
    ci = dict(authority.get("github_ci") or {})
    preview = dict(authority.get("vercel_preview") or {})
    source_commit = str(source.get("commit") or "").lower()
    source_tree = str(source.get("tree") or "").lower()
    branch = str(source.get("branch") or "")
    base_anchor = dict(package_receipt.get("base_anchor") or {})
    package_export = dict(package_receipt.get("exact_commit_export") or {})
    required_check_count = int(ci.get("required_check_count") or 0)
    successful_check_count = int(ci.get("successful_check_count") or 0)
    if (
        authority.get("schema")
        != "evidence-lane.codex-git-ci-vercel-release-authority.v2"
        or package_receipt.get("schema")
        != "evidence-lane.codex-exact-commit-package.v1.receipt"
        or package_receipt.get("boundary")
        != "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
        or authority.get("status") != "PASS"
        or authority.get("boundary")
        != "GOVERNED_GIT_BRANCH_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT"
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
        or branch in {"main", "master"}
        or not branch
        or source.get("exact_commit_export") is not True
        or source.get("exact_commit_projection_clean") is not True
        or source.get("working_checkout_clean_required") is not False
        or source.get("untracked_bytes_excluded") is not True
        or str(base_anchor.get("commit") or "").lower() != source_commit
        or str(base_anchor.get("tree") or "").lower() != source_tree
        or remote.get("route") != "NATIVE_GOVERNED_REMOTE_GIT"
        or remote.get("push_status") != "EXECUTED"
        or str(remote.get("remote_branch_commit") or "").lower() != source_commit
        or remote.get("protected_branch") is not False
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(remote.get("native_receipt_sha256") or "").upper(),
        )
        is None
        or ci.get("status") != "PASS"
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
        or preview.get("branch") != branch
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
            "Activation requires one exact clean Git commit, governed native push, "
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
    archive_root = (
        data_root
        / "installations"
        / "codex-v200"
        / "marketplace-archives"
    )
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
        enriched = _surface_inventory(plugin_root, version=version)
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
        "receipt_surface_inventory_sha256": legacy_surface[
            "surface_inventory_sha256"
        ],
    }


def _load_comparison_baseline(
    *,
    path: Path,
    expected_sha256: str,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one explicitly sealed prior host-stable installation surface."""

    expected = str(expected_sha256 or "").strip().upper()
    authority_root = data_root / "installations" / "codex-v200"
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
    surface_sha256 = str(
        surface_core.pop("surface_inventory_sha256", "")
    ).upper()
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
        or activation.get("state") != "INSTALLED_RESTART_REQUIRED"
        or plugin.get("plugin_id") != PLUGIN_NAME
        or plugin.get("version") != surface.get("plugin_version")
        or surface.get("schema")
        != "evidence-lane.codex-installed-surface-inventory.v2"
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
        "baseline_role": "EXACT_PRIOR_HOST_STABLE_INSTALLATION",
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
            if relative.is_absolute() or ".." in relative.parts or "\\" in info.filename:
                raise InstallationError(f"Unsafe archive member: {info.filename}")
            destination = (target / relative).resolve()
            if not _inside(destination, target):
                raise InstallationError(f"Archive member escaped extraction: {info.filename}")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


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
    branch_recovery = contract.get("branch_recovery") or {}
    local_testing = contract.get("local_testing") or {}
    live_slots = contract.get("live_slot_policy") or {}
    failover = contract.get("failover_operator") or {}
    goal_recovery = contract.get("goal_recovery") or {}
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
        plugin_root
        / "scripts"
        / "codex_release"
        / "seal_external_release_receipts.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "Update-EvidenceLaneCodexStableAndResume.ps1",
        plugin_root / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1",
        plugin_root
        / "scripts"
        / "codex_release"
        / "Manage-EvidenceLaneCodexGoalRecovery.ps1",
        plugin_root
        / "scripts"
        / "codex_release"
        / "Switch-EvidenceLaneCodexSlot.ps1",
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
        or stable.get("install_source") != "GIT_EXACT_COMMIT"
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
        or branch_recovery.get("release") != BASE_RELEASE
        or branch_recovery.get("slot_role") != "branch-commit-recovery"
        or branch_recovery.get("codex_marketplace_slot")
        != LOCAL_RECOVERY_MARKETPLACE_NAME
        or branch_recovery.get("marketplace_display_name")
        != LOCAL_RECOVERY_MARKETPLACE_DISPLAY_NAME
        or branch_recovery.get("install_source")
        != "GOVERNED_BRANCH_COMMIT_EXACT_PACKAGE"
        or branch_recovery.get("enabled") is not False
        or branch_recovery.get("update_gate")
        != "LOCAL_TEST_GREEN_AND_GOVERNED_BRANCH_COMMIT"
        or branch_recovery.get("byte_frozen_between_branch_checkpoints") is not True
        or branch_recovery.get("must_not_follow_uncommitted_local_bytes") is not True
        or branch_recovery.get("recover_mutable_local_test_failure") is not True
        or branch_recovery.get("simultaneous_mcp_allowed") is not False
        or branch_recovery.get("simultaneous_tunnel_allowed") is not False
        or local_testing.get("release_line") != BASE_RELEASE
        or local_testing.get("slot_role") != "mutable-local-testing"
        or local_testing.get("codex_marketplace_slot")
        != LOCAL_TESTING_MARKETPLACE_NAME
        or local_testing.get("marketplace_display_name")
        != LOCAL_TESTING_MARKETPLACE_DISPLAY_NAME
        or local_testing.get("install_source") != "FRESH_VERSIONED_LOCAL_PACKAGE"
        or local_testing.get("same_marketplace_selector_reused") is not True
        or local_testing.get("fresh_package_version_per_local_build") is not True
        or local_testing.get("enabled_only_during_local_test") is not True
        or local_testing.get("branch_recovery_mutation_allowed_during_local_build")
        is not False
        or local_testing.get("helper_installs_plugin") is not False
        or live_slots.get("exact_slot_count") != 3
        or live_slots.get("allowed_slots")
        != [
            "main-git-release",
            "branch-commit-recovery",
            "mutable-local-testing",
        ]
        or live_slots.get("allowed_marketplaces")
        != [
            MARKETPLACE_NAME,
            LOCAL_RECOVERY_MARKETPLACE_NAME,
            LOCAL_TESTING_MARKETPLACE_NAME,
        ]
        or live_slots.get("max_enabled_plugin_count") != 1
        or live_slots.get("exact_registered_plugin_count") != 3
        or live_slots.get("stable_selector_growth_allowed") is not False
        or live_slots.get("max_active_native_mcp_count") != 1
        or live_slots.get("max_active_tunnel_count") != 1
        or live_slots.get("inactive_slot_remains_installed") is not True
        or live_slots.get("manual_loaded_cache_deletion_allowed") is not False
        or live_slots.get("forbidden_obsolete_marketplaces")
        != [
            LOCAL_SUCCESSOR_MARKETPLACE_NAME,
            "evidence-lane-pv11-fallback",
        ]
        or failover.get("script")
        != "scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1"
        or failover.get("registry_schema")
        != "evidence-lane.codex-three-slot-registry.v1"
        or failover.get("failure_target_slot") != "branch-commit-recovery"
        or failover.get("mutable_local_failure_never_targets_main_git") is not True
        or failover.get("single_transient_error_switch_allowed") is not False
        or failover.get("stop_source_tunnel_before_start_target") is not True
        or failover.get("target_tunnel_ready_before_plugin_switch") is not True
        or failover.get("controlled_exact_task_restart_required") is not True
        or failover.get("switch_failure_restores_source_slot") is not True
        or goal_recovery.get("script")
        != "scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1"
        or goal_recovery.get("scope")
        != "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_ON_THIS_WINDOWS_USER"
        or goal_recovery.get("trigger") != "AT_LOGON_CURRENT_WINDOWS_USER"
        or goal_recovery.get("exact_task_uuid_required") is not True
        or goal_recovery.get("exact_host_app_binding_required") is not True
        or goal_recovery.get("supported_host_app_ids")
        != [
            "OpenAI.Codex_2p2nqsd0c76g0!App",
            "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
        ]
        or goal_recovery.get("persisted_goal_read_route")
        != "CODEX_APP_SERVER_THREAD_READ_PLUS_THREAD_GOAL_GET"
        or goal_recovery.get("thread_resume_writer_allowed") is not False
        or goal_recovery.get("synthetic_prompt_allowed") is not False
        or goal_recovery.get("turn_start_allowed") is not False
        or goal_recovery.get("state_travel_allowed") is not False
        or goal_recovery.get("candidate_hil_pointer_or_git_mutation_allowed")
        is not False
        or goal_recovery.get(
            "requires_exactly_one_enabled_allowed_three_slot_selector"
        )
        is not True
        or goal_recovery.get("allowed_runtime_selectors")
        != [
            PLUGIN_SELECTOR,
            LOCAL_RECOVERY_SELECTOR,
            f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}",
        ]
        or goal_recovery.get("stable_selector_growth_allowed") is not False
        or goal_recovery.get("raw_goal_objective_stored") is not False
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
        != "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        or remote_git.get("host_managed_credentials_only") is not True
        or remote_git.get("main_push_allowed") is not False
        or remote_git.get("merge_allowed") is not False
        or remote_git.get("pull_request_acceptance_allowed") is not False
        or remote_git.get("force_push_allowed") is not False
        or promotion.get("mode") != "CODE"
        or promotion.get("ci_cd_law") != "CONTROLLED_REQUIRED"
        or promotion.get("explicit_six_way_hil_required") is not True
        or not all(path.is_file() for path in release_helpers)
    ):
        raise InstallationError("The extracted v2 plugin or release contract drifted.")
    native = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    if set(native.get("mcpServers") or {}) != {"evidence-lane"}:
        raise InstallationError("The package must contain one native evidence-lane MCP.")
    forbidden = (
        plugin_root / "release-channels.json",
        plugin_root / "remote_adapter",
        plugin_root / "evidence",
    )
    external_app_artifacts = tuple(plugin_root.glob("*-app-connection.json")) + tuple(
        plugin_root.glob("*-app-submission.json")
    )
    if any(path.exists() for path in forbidden) or external_app_artifacts:
        raise InstallationError("A separate app, website, or evidence surface leaked in.")
    hooks = json.loads((plugin_root / "hooks" / "hooks.json").read_text("utf-8"))
    hook_events = dict(hooks.get("hooks") or {})
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
        or handler_count != len(EXPECTED_PACKAGE_HOOK_EVENTS)
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
            raise InstallationError(f"{name} does not emit the persistent change notice.")
    return {
        "plugin_id": PLUGIN_NAME,
        "version": version,
        "manifest_sha256": _sha256(manifest_path),
        "catalog": catalog,
        "surface_inventory": _surface_inventory(plugin_root, version=version),
    }


def _marketplace_bytes(marketplace_name: str) -> bytes:
    display_name = {
        LOCAL_TESTING_MARKETPLACE_NAME: LOCAL_TESTING_MARKETPLACE_DISPLAY_NAME,
        LOCAL_RECOVERY_MARKETPLACE_NAME: LOCAL_RECOVERY_MARKETPLACE_DISPLAY_NAME,
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
        raise InstallationError("Generated Codex plugin cache is immutable to this installer.")
    staging = marketplace_root.parent / (
        f".{marketplace_root.name}.staging-{uuid.uuid4().hex}"
    )
    staging_plugin = staging / "plugins" / PLUGIN_NAME
    staging_plugin.parent.mkdir(parents=True, exist_ok=True)
    prior_plugin = marketplace_root / "plugins" / PLUGIN_NAME
    previous_surface = comparison_surface
    if previous_surface is None and prior_plugin.is_dir():
        prior_manifest = json.loads(
            (prior_plugin / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        previous_surface = _surface_inventory(
            prior_plugin,
            version=str(prior_manifest.get("version") or "UNVERIFIED"),
        )
    surface_change = _surface_change_display(
        previous=previous_surface,
        current=identity["surface_inventory"],
    )
    try:
        shutil.copytree(
            extracted,
            staging_plugin,
            ignore=shutil.ignore_patterns("_evidence_lane_rehearsal"),
        )
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
                    LOCAL_RECOVERY_MARKETPLACE_NAME: (
                        LOCAL_RECOVERY_MARKETPLACE_DISPLAY_NAME
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
                        "comparison_baseline": comparison_baseline,
                        "surface_change_display": preserved_change,
                    }
            archive_root = (
                data_root
                / "installations"
                / "codex-v200"
                / "marketplace-archives"
            )
            archive_root.mkdir(parents=True, exist_ok=True)
            prior_sha = _sha256(current_stage) if current_stage.is_file() else "UNSEALED"
            archive_target = archive_root / f"{marketplace_root.name}-{prior_sha[:16]}"
            if archive_target.exists():
                raise InstallationError("The exact prior marketplace archive already exists.")
            if not _inside(marketplace_root, expected_parent):
                raise InstallationError("Refusing to move an uncontained marketplace root.")
            os.replace(marketplace_root, archive_target)
            prior_archived = True
        else:
            prior_archived = False
        os.replace(staging, marketplace_root)
        return {
            "state": "STAGED",
            "prior_marketplace_archived": prior_archived,
            "comparison_baseline": comparison_baseline,
            "surface_change_display": surface_change,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _windows_hidden_creationflags() -> int:
    """Return the no-console flag for every installer-owned child process."""

    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run_codex(
    executable: Path,
    codex_home: Path,
    arguments: list[str],
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    timeout_seconds = (
        480
        if arguments[:3] == ["plugin", "marketplace", "add"]
        else 120
    )
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
        raise InstallationError("Codex did not return the requested JSON receipt.") from exc


def _local_test_runtime_readiness(
    *,
    installed: bool,
    restart_or_reload_completed: bool,
    hooks_trusted: bool,
    exact_identity_verified: bool,
    exact_catalog_verified: bool,
    prompt_capture_verified: bool,
    smoke_probes_passed: bool,
    active: bool,
) -> dict[str, Any]:
    """Return one explicit local-test lifecycle state without inferring ready."""

    gates = {
        "installed": installed,
        "restart_or_reload_completed": restart_or_reload_completed,
        "hooks_trusted": hooks_trusted,
        "exact_identity_verified": exact_identity_verified,
        "exact_catalog_verified": exact_catalog_verified,
        "prompt_capture_verified": prompt_capture_verified,
        "smoke_probes_passed": smoke_probes_passed,
        "active": active,
    }
    ready = all(gates.values())
    if not installed:
        state = "NOT_INSTALLED"
    elif not restart_or_reload_completed:
        state = "INSTALLED_RESTART_OR_RELOAD_REQUIRED"
    elif not hooks_trusted:
        state = "INSTALLED_UNTRUSTED"
    elif not active:
        state = "TRUSTED_INACTIVE"
    elif not (exact_identity_verified and exact_catalog_verified):
        state = "ACTIVE_IDENTITY_OR_CATALOG_UNVERIFIED"
    elif not prompt_capture_verified:
        state = "ACTIVE_PROMPT_CAPTURE_UNVERIFIED"
    elif not smoke_probes_passed:
        state = "ACTIVE_SMOKE_PROBES_UNVERIFIED"
    else:
        state = "READY"
    return {
        "schema": "evidence-lane.codex-local-test-runtime-readiness.v1",
        "state": state,
        "gates": gates,
        "ready": ready,
        "runtime_ready_before_task_reopen": ready,
        "readiness_inferred_from_install_or_prewarm": False,
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


def _load_local_test_stage_authority(
    *,
    stage_receipt_path: Path,
    stage_receipt_sha256: str,
    data_root: Path,
) -> dict[str, Any]:
    """Load exactly one uncommitted rollback-capable local-test stage."""

    authority_root = data_root.resolve() / "installations" / "codex-v200"
    stage, resolved_stage, stage_file_sha256 = _load_self_sealed_json(
        path=stage_receipt_path,
        expected_file_sha256=stage_receipt_sha256,
        authority_root=authority_root,
        label="local-test stage receipt",
    )
    activation = dict(stage.get("activation") or {})
    transaction = dict(activation.get("transaction") or {})
    candidate_selector = str(transaction.get("candidate_selector") or "")
    last_known_good_selector = str(
        transaction.get("last_known_good_selector") or ""
    )
    rollback_backup = Path(
        str(transaction.get("rollback_config_backup") or "")
    ).resolve()
    rollback_backup_sha256 = str(
        transaction.get("rollback_config_backup_sha256") or ""
    ).upper()
    if (
        stage.get("schema") != INSTALL_SCHEMA
        or stage.get("status") != "PASS"
        or activation.get("state")
        != "CANDIDATE_STAGED_DISABLED_RESTART_TRUST_REQUIRED"
        or transaction.get("schema") != LOCAL_TEST_STAGE_SCHEMA
        or transaction.get("state") != "CANDIDATE_STAGED_DISABLED"
        or transaction.get("compare_and_swap") is not True
        or transaction.get("rollback_capable") is not True
        or transaction.get("candidate_enabled") is not False
        or transaction.get("switch_count") != 0
        or candidate_selector
        != f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"
        or not last_known_good_selector.startswith(f"{PLUGIN_NAME}@")
        or last_known_good_selector == candidate_selector
        or not rollback_backup.is_file()
        or not _inside(rollback_backup, authority_root)
        or re.fullmatch(r"[A-F0-9]{64}", rollback_backup_sha256) is None
        or _sha256(rollback_backup) != rollback_backup_sha256
        or stage.get("candidate_created_or_accepted") is not False
        or stage.get("pointer_moved") is not False
        or stage.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "The local-test stage receipt is not an uncommitted rollback-capable "
            "candidate authority."
        )
    return {
        "stage": stage,
        "stage_path": resolved_stage,
        "stage_file_sha256": stage_file_sha256,
        "transaction": transaction,
        "candidate_selector": candidate_selector,
        "last_known_good_selector": last_known_good_selector,
        "last_known_good_config_sha256": str(
            transaction.get("last_known_good_config_sha256") or ""
        ).upper(),
        "prepared_rollback_config_backup": rollback_backup,
    }


def _load_local_test_commit_authority(
    *,
    stage_receipt_path: Path,
    stage_receipt_sha256: str,
    host_proof_path: Path,
    host_proof_sha256: str,
    data_root: Path,
) -> dict[str, Any]:
    """Join one staged candidate to one fresh task-independent host proof."""

    stage_authority = _load_local_test_stage_authority(
        stage_receipt_path=stage_receipt_path,
        stage_receipt_sha256=stage_receipt_sha256,
        data_root=data_root,
    )
    authority_root = data_root.resolve() / "installations" / "codex-v200"
    transaction = stage_authority["transaction"]
    candidate_selector = stage_authority["candidate_selector"]
    last_known_good_selector = stage_authority["last_known_good_selector"]

    proof, resolved_proof, proof_file_sha256 = _load_self_sealed_json(
        path=host_proof_path,
        expected_file_sha256=host_proof_sha256,
        authority_root=authority_root,
        label="local-test host proof",
    )
    gates = dict(proof.get("gates") or {})
    commit_baseline_config_sha256 = str(
        proof.get("commit_baseline_config_sha256") or ""
    ).upper()
    if (
        proof.get("schema") != LOCAL_TEST_HOST_PROOF_SCHEMA
        or proof.get("status") != "PASS"
        or proof.get("transaction_id") != transaction.get("transaction_id")
        or proof.get("stage_installation_receipt_sha256")
        != stage_authority["stage_file_sha256"]
        or proof.get("candidate_selector") != candidate_selector
        or proof.get("last_known_good_selector") != last_known_good_selector
        or re.fullmatch(r"[A-F0-9]{64}", commit_baseline_config_sha256)
        is None
        or gates.get("human_hook_trust_completed") is not True
        or gates.get("host_restart_or_reload_completed") is not True
        or gates.get("exact_plugin_identity_verified") is not True
        or gates.get("exact_native_catalog_verified") is not True
        or dict(proof.get("catalog") or {}) != EXPECTED_CATALOG
        or proof.get("task_binding_used_for_authorization") is not False
        or proof.get("goal_recovery_invoked") is not False
        or proof.get("installer_helper_is_separate") is not True
        or proof.get("tunnel_invoked") is not False
        or proof.get("candidate_created_or_accepted") is not False
        or proof.get("pointer_moved") is not False
        or proof.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "The local-test host proof does not authorize one exact CAS commit."
        )
    return {
        **stage_authority,
        "host_proof": proof,
        "host_proof_path": resolved_proof,
        "host_proof_file_sha256": proof_file_sha256,
        "commit_baseline_config_sha256": commit_baseline_config_sha256,
    }


def _prepare_local_test_reinstall(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    plugin_selector: str,
    marketplace_name: str,
    marketplace_root: Path,
    disabled_hook_recovery_commit: Path | None = None,
    disabled_hook_recovery_commit_sha256: str | None = None,
) -> dict[str, Any]:
    """Refresh only the explicit maintainer test selector through Codex APIs.

    The accepted stable/fallback registry is deliberately outside this route.  An
    already installed *disabled* test selector is removed before it is re-added
    from newly sealed marketplace bytes.  The one enabled last-known-good route
    and exact pre-transaction config are retained for compare-and-swap recovery.
    """

    if (disabled_hook_recovery_commit is None) != (
        disabled_hook_recovery_commit_sha256 is None
    ):
        raise InstallationError(
            "Disabled-local hook recovery requires both the prior commit receipt "
            "and its file SHA-256."
        )
    disabled_hook_recovery = disabled_hook_recovery_commit is not None
    recovery_authority = (
        _load_local_test_recovery_authority(
            commit_receipt_path=Path(disabled_hook_recovery_commit),
            commit_receipt_sha256=str(disabled_hook_recovery_commit_sha256),
            data_root=data_root,
        )
        if disabled_hook_recovery
        else None
    )
    if (
        recovery_authority is not None
        and recovery_authority["candidate_selector"] != plugin_selector
    ):
        raise InstallationError(
            "The prior local-test commit does not bind this exact recovery selector."
        )

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
    target_rows = [
        row for row in evidence_plugins if row.get("pluginId") == plugin_selector
    ]
    enabled_rows = [row for row in evidence_plugins if row.get("enabled") is True]
    if len(target_rows) > 1:
        raise InstallationError("Codex exposed duplicate local-test plugin identities.")
    plugin_list_post_add_transient = (
        disabled_hook_recovery
        and len(enabled_rows) == 1
        and enabled_rows[0].get("pluginId") == plugin_selector
    )
    if disabled_hook_recovery:
        if (
            len(target_rows) != 1
            or (enabled_rows and not plugin_list_post_add_transient)
        ):
            raise InstallationError(
                "Disabled-local hook recovery requires the installed local selector "
                "and either every selector disabled or the exact local post-add "
                "transient."
            )
        last_known_good_selector = str(
            dict(recovery_authority or {}).get("last_known_good_selector") or ""
        )
        last_known_good_rows = [
            row
            for row in evidence_plugins
            if row.get("pluginId") == last_known_good_selector
        ]
        if (
            len(last_known_good_rows) != 1
            or last_known_good_rows[0].get("enabled") is not False
        ):
            raise InstallationError(
                "The sealed prior stable selector is not installed and disabled."
            )
        last_known_good_version = str(
            last_known_good_rows[0].get("version") or ""
        )
    else:
        if (
            len(enabled_rows) != 1
            or enabled_rows[0].get("pluginId") == plugin_selector
        ):
            raise InstallationError(
                "Local-test preparation requires one different enabled last-known-good "
                "Evidence Lane selector."
            )
        last_known_good_selector = str(enabled_rows[0].get("pluginId") or "")
        last_known_good_version = str(enabled_rows[0].get("version") or "")
    target_was_installed = bool(target_rows)
    target_was_enabled = bool(target_rows and target_rows[0].get("enabled") is True)
    if target_was_enabled and not plugin_list_post_add_transient:
        raise InstallationError(
            "The local-test candidate is already enabled; use the bounded recovery "
            "controller instead of overwriting its state."
        )

    config_path = codex_home / "config.toml"
    config_bytes = config_path.read_bytes()
    config_raw = config_bytes.decode("utf-8")
    config_sha256 = hashlib.sha256(config_bytes).hexdigest().upper()
    plugin_config = dict(tomllib.loads(config_raw).get("plugins") or {})
    config_archive = (
        data_root / "installations" / "codex-v200" / "config-archives"
    )
    pre_normalization_backup = config_archive / f"config-{config_sha256}.toml"
    if not pre_normalization_backup.exists():
        _write_atomic(pre_normalization_backup, config_bytes)
    if _sha256(pre_normalization_backup) != config_sha256:
        raise InstallationError(
            "The pre-recovery config archive does not match its sealed snapshot."
        )
    disabled_mcp_boundary_normalization: dict[str, Any] | None = None
    try:
        activation_states = _evidence_plugin_activation_states(plugin_config)
    except InstallationError:
        if not disabled_hook_recovery:
            raise
        try:
            normalized_plugins = _normalize_disabled_local_mcp_boundary(
                plugin_config,
                plugin_selector=plugin_selector,
            )
            normalization_mode = "STALE_LOCAL_MCP_AFTER_UI_DISABLE"
            plugin_enabled_before = False
            mcp_enabled_before = True
        except InstallationError:
            normalized_plugins = _normalize_local_post_add_boundary(
                plugin_config,
                plugin_selector=plugin_selector,
            )
            normalization_mode = "LOCAL_PLUGIN_ADD_TRANSIENT"
            plugin_enabled_before = True
            mcp_enabled_before = False
        if plugin_list_post_add_transient is not (
            normalization_mode == "LOCAL_PLUGIN_ADD_TRANSIENT"
        ):
            raise InstallationError(
                "The plugin list and config disagree on the local recovery transient."
            )
        normalization_write = _batch_write_recovery_plugins(
            executable=executable,
            codex_home=codex_home,
            expected_before_sha256=config_sha256,
            plugins=normalized_plugins,
        )
        normalized_bytes = config_path.read_bytes()
        normalized_raw = normalized_bytes.decode("utf-8")
        normalized_sha256 = hashlib.sha256(normalized_bytes).hexdigest().upper()
        plugin_config = dict(
            tomllib.loads(normalized_raw).get("plugins") or {}
        )
        activation_states = _evidence_plugin_activation_states(plugin_config)
        if any(activation_states.values()):
            raise InstallationError(
                "The local MCP recovery boundary did not leave every selector disabled."
            )
        disabled_mcp_boundary_normalization = {
            "schema": "evidence-lane.codex-disabled-local-mcp-normalization.v1",
            "status": "PASS",
            "selector": plugin_selector,
            "normalization_mode": normalization_mode,
            "plugin_enabled_before": plugin_enabled_before,
            "mcp_enabled_before": mcp_enabled_before,
            "plugin_enabled_after": False,
            "mcp_enabled_after": False,
            "all_evidence_lane_selectors_disabled_after": True,
            "supported_codex_api": "config/batchWrite",
            "before_sha256": config_sha256,
            "after_sha256": normalized_sha256,
            "pre_normalization_backup": str(pre_normalization_backup),
            "pre_normalization_backup_sha256": _sha256(
                pre_normalization_backup
            ),
            "config_write": normalization_write,
            "unrelated_selector_mutated": False,
        }
        config_bytes = normalized_bytes
        config_raw = normalized_raw
        config_sha256 = normalized_sha256
    if (
        disabled_hook_recovery
        and [
            selector
            for selector, enabled in activation_states.items()
            if enabled
        ]
        == [plugin_selector]
    ):
        normalized_plugins = _normalize_active_local_reinstall_boundary(
            plugin_config,
            plugin_selector=plugin_selector,
        )
        normalization_write = _batch_write_recovery_plugins(
            executable=executable,
            codex_home=codex_home,
            expected_before_sha256=config_sha256,
            plugins=normalized_plugins,
        )
        normalized_bytes = config_path.read_bytes()
        normalized_raw = normalized_bytes.decode("utf-8")
        normalized_sha256 = hashlib.sha256(normalized_bytes).hexdigest().upper()
        plugin_config = dict(
            tomllib.loads(normalized_raw).get("plugins") or {}
        )
        activation_states = _evidence_plugin_activation_states(plugin_config)
        if any(activation_states.values()):
            raise InstallationError(
                "The active local reinstall boundary did not leave every selector disabled."
            )
        disabled_mcp_boundary_normalization = {
            "schema": "evidence-lane.codex-disabled-local-mcp-normalization.v1",
            "status": "PASS",
            "selector": plugin_selector,
            "normalization_mode": "ACTIVE_LOCAL_SELECTOR_REINSTALL_BOUNDARY",
            "plugin_enabled_before": True,
            "mcp_enabled_before": True,
            "plugin_enabled_after": False,
            "mcp_enabled_after": False,
            "all_evidence_lane_selectors_disabled_after": True,
            "supported_codex_api": "config/batchWrite",
            "before_sha256": config_sha256,
            "after_sha256": normalized_sha256,
            "pre_normalization_backup": str(pre_normalization_backup),
            "pre_normalization_backup_sha256": _sha256(
                pre_normalization_backup
            ),
            "config_write": normalization_write,
            "unrelated_selector_mutated": False,
        }
        config_bytes = normalized_bytes
        config_raw = normalized_raw
        config_sha256 = normalized_sha256
    configured_enabled = [
        selector for selector, enabled in activation_states.items() if enabled
    ]
    if disabled_hook_recovery:
        if (
            plugin_selector not in activation_states
            or last_known_good_selector not in activation_states
            or configured_enabled
        ):
            raise InstallationError(
                "The Codex config is not the exact all-disabled recovery boundary."
            )
    elif configured_enabled != [last_known_good_selector]:
        raise InstallationError(
            "The Codex config does not match the one enabled last-known-good selector."
        )
    config_backup = config_archive / f"config-{config_sha256}.toml"
    if not config_backup.exists():
        _write_atomic(config_backup, config_bytes)
    if _sha256(config_backup) != config_sha256:
        raise InstallationError(
            "The last-known-good rollback config does not match its sealed snapshot."
        )
    if target_was_installed:
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
    exact_rows = [
        dict(row)
        for row in marketplaces
        if isinstance(row, dict) and row.get("name") == marketplace_name
    ]
    if len(exact_rows) > 1:
        raise InstallationError("Codex exposed duplicate local-test marketplaces.")
    marketplace_preexisting = bool(exact_rows)
    if marketplace_preexisting:
        configured_root = Path(str(exact_rows[0].get("root") or "")).resolve()
        if configured_root != marketplace_root.resolve():
            raise InstallationError(
                "The configured local-test marketplace root does not match the "
                "sealed staging root."
            )

    receipt = {
        "schema": "evidence-lane.codex-local-test-reinstall.v1",
        "status": "PASS",
        "plugin_selector": plugin_selector,
        "marketplace_name": marketplace_name,
        "marketplace_root": str(marketplace_root.resolve()),
        "target_was_installed": target_was_installed,
        "target_was_enabled": target_was_enabled,
        "plugin_list_post_add_transient": plugin_list_post_add_transient,
        "target_removed_for_exact_reinstall": target_was_installed,
        "marketplace_preexisting": marketplace_preexisting,
        "marketplace_root_verified": marketplace_preexisting,
        "marketplace_add_required": not marketplace_preexisting,
        "accepted_two_slot_registry_mutated": False,
        "stable_or_fallback_selector_removed": False,
        "transaction_mode": (
            "DISABLED_LOCAL_HOOK_RECOVERY"
            if disabled_hook_recovery
            else "COMPARE_AND_SWAP"
        ),
        "last_known_good_selector": last_known_good_selector,
        "last_known_good_version": last_known_good_version,
        "last_known_good_config_sha256": config_sha256,
        "rollback_config_backup": str(config_backup),
        "rollback_config_backup_sha256": _sha256(config_backup),
        "candidate_must_remain_disabled_until_commit": not disabled_hook_recovery,
        "all_evidence_lane_selectors_disabled_before_reinstall": (
            disabled_hook_recovery
        ),
        "recovery_switch_target": (
            plugin_selector if disabled_hook_recovery else None
        ),
        "recovery_rollback_state": (
            "ALL_EVIDENCE_LANE_SELECTORS_DISABLED"
            if disabled_hook_recovery
            else None
        ),
        "disabled_mcp_boundary_normalization": (
            disabled_mcp_boundary_normalization
        ),
        "prior_commit_authority": (
            {
                "path": str(recovery_authority["commit_path"]),
                "file_sha256": recovery_authority["commit_file_sha256"],
                "transaction_id": recovery_authority["transaction_id"],
                "failed_candidate_config_sha256": recovery_authority[
                    "failed_candidate_config_sha256"
                ],
            }
            if recovery_authority is not None
            else None
        ),
        "switch_count": 0,
    }
    transaction_prefix = (
        "local_hook_recovery_" if disabled_hook_recovery else "local_test_tx_"
    )
    receipt["transaction_id"] = transaction_prefix + hashlib.sha256(
        _json_bytes(
            {
                "candidate_selector": plugin_selector,
                "last_known_good_config_sha256": config_sha256,
                "last_known_good_selector": last_known_good_selector,
                "marketplace_root": str(marketplace_root.resolve()),
                "prior_commit_file_sha256": (
                    recovery_authority["commit_file_sha256"]
                    if recovery_authority is not None
                    else None
                ),
            }
        )
    ).hexdigest()[:40].lower()
    receipt["receipt_sha256"] = hashlib.sha256(_json_bytes(receipt)).hexdigest().upper()
    return receipt


def _prewarm_installed_runtime(
    plugin_root: Path,
    *,
    data_root: Path,
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
    started = time.monotonic()
    bootstrap_attempts: list[dict[str, Any]] = []
    prewarm_environment = os.environ.copy()
    prewarm_environment["EVIDENCE_LANE_DATA_ROOT"] = str(data_root.resolve())
    boot: subprocess.CompletedProcess[bytes] | None = None
    for attempt in range(1, 3):
        try:
            boot = subprocess.run(
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
                "The installed runtime bootstrap did not complete before task reopen."
            ) from exc
        bootstrap_attempts.append(
            {
                "attempt": attempt,
                "returncode": int(boot.returncode),
                "stdout_sha256": hashlib.sha256(boot.stdout).hexdigest().upper(),
                "stderr_sha256": hashlib.sha256(boot.stderr).hexdigest().upper(),
            }
        )
        if boot.returncode == 0:
            break
    if boot is None or boot.returncode != 0:
        raise InstallationError(
            "The installed runtime bootstrap failed twice on the same sealed bytes "
            f"before task reopen (attempts={bootstrap_attempts})."
        )
    try:
        prewarm_lines = [
            line for line in boot.stdout.decode("utf-8").splitlines() if line.strip()
        ]
        prewarm_result = json.loads(prewarm_lines[-1])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The installed runtime prewarm returned no exact JSON receipt."
        ) from exc
    if (
        prewarm_result.get("schema")
        != "evidence-lane.codex-native-runtime-prewarm.v1"
        or prewarm_result.get("status") != "PASS"
    ):
        raise InstallationError("The installed runtime prewarm identity drifted.")
    runtime_python = Path(str(prewarm_result.get("runtime_python") or "")).resolve()
    runtime_projection_root = Path(
        str(prewarm_result.get("runtime_projection_root") or "")
    ).resolve()
    runtime_environment = Path(
        str(prewarm_result.get("runtime_environment") or "")
    ).resolve()
    runtime_identity = dict(prewarm_result.get("runtime_identity") or {})
    expected_runtime_parent = (data_root / "runtime" / "codex").resolve()
    expected_lock_sha256 = _sha256(plugin_root / "requirements.lock.txt")
    if not runtime_python.is_file():
        raise InstallationError("The governed bootstrap did not create its runtime.")
    if (
        not _inside(runtime_projection_root, expected_runtime_parent)
        or runtime_environment != runtime_projection_root / "venv"
        or not _inside(runtime_python, runtime_environment)
        or runtime_identity.get("schema")
        != "evidence-lane.codex-native-runtime.v1"
        or runtime_identity.get("requirements_lock_sha256")
        != expected_lock_sha256
        or re.fullmatch(
            r"[A-F0-9]{64}", str(runtime_identity.get("runtime_key") or "")
        )
        is None
    ):
        raise InstallationError(
            "The derived runtime is outside its sealed durable authority."
        )
    environment = os.environ.copy()
    source = str((plugin_root / "src").resolve())
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source, existing_pythonpath) if part
    )
    probe_source = (
        "import json; "
        "from evidence_lane_plugin.constants import ENGINE_VERSION; "
        "from evidence_lane_plugin.lane_engine import prewarm_native_dependencies; "
        "from evidence_lane_plugin.mcp_server import "
        "CODEX_READ_TOOL_NAMES,NATIVE_MCP_SERVER_IDENTITY,create_mcp_server; "
        "prewarm_native_dependencies(); "
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
        "'native_dependency_prewarm_completed':True"
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
    if result_without_catalog_seal != expected_result:
        raise InstallationError("The installed native runtime identity drifted.")
    if re.fullmatch(r"[A-F0-9]{64}", str(catalog_seal)) is None:
        raise InstallationError("The installed native catalog seal is missing.")
    core = {
        "schema": "evidence-lane.codex-installed-runtime-prewarm.v1",
        "status": "PASS",
        "runtime_ready_before_task_reopen": True,
        "plugin_root_sha256": hashlib.sha256(
            str(plugin_root.resolve()).encode("utf-8")
        ).hexdigest().upper(),
        "runtime_python_sha256": _sha256(runtime_python),
        "runtime_projection_root": str(runtime_projection_root),
        "runtime_identity": runtime_identity,
        "bootstrap_attempt_count": len(bootstrap_attempts),
        "bootstrap_attempts": bootstrap_attempts,
        "bootstrap_stdout_sha256": hashlib.sha256(boot.stdout).hexdigest().upper(),
        "bootstrap_stderr_sha256": hashlib.sha256(boot.stderr).hexdigest().upper(),
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
    module_name = "_evidence_lane_installed_hook_isolation_" + hashlib.sha256(
        str(module_path.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise InstallationError(
            "The installed hook-event isolation runtime could not be loaded."
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        initialized = module.initialize_inactive_kill_switch(
            data_root.resolve(),
            installation_id=installation_id,
        )
    except Exception as exc:
        raise InstallationError(
            "The persistent hook kill switch could not be initialized safely."
        ) from exc

    receipt_path = Path(str(initialized.get("path") or "")).resolve()
    expected_path = (
        data_root.resolve() / "hook-event-isolation" / "KILL_SWITCH.json"
    )
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
        or str(initialized.get("file_sha256") or "").upper()
        != _sha256(receipt_path)
        or str(initialized.get("policy_sha256") or "").upper()
        != policy_sha256
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
    module_name = "_evidence_lane_recovery_hook_isolation_" + hashlib.sha256(
        str(module_path.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise InstallationError(
            "The installed recovery hook-event isolation runtime could not be loaded."
        )
    module = importlib.util.module_from_spec(spec)
    receipt_path = (
        data_root.resolve() / "hook-event-isolation" / "KILL_SWITCH.json"
    )
    if not receipt_path.is_file():
        raise InstallationError(
            "The primary persistent hook kill-switch receipt is unavailable."
        )
    before_bytes = receipt_path.read_bytes()
    before_sha256 = hashlib.sha256(before_bytes).hexdigest().upper()
    policy_sha256 = _sha256(policy_path)
    try:
        spec.loader.exec_module(module)
        _body, payload = module._read_kill_switch(receipt_path)
    except Exception as exc:
        raise InstallationError(
            "The recovery bytes could not verify the primary hook kill switch."
        ) from exc
    after_bytes = receipt_path.read_bytes()
    after_sha256 = hashlib.sha256(after_bytes).hexdigest().upper()

    primary_receipt_path = Path(
        str(primary_hook_isolation.get("kill_switch_receipt_path") or "")
    ).resolve()
    primary_generation = primary_hook_isolation.get("generation")
    if (
        primary_hook_isolation.get("status") != "PASS"
        or primary_hook_isolation.get("state")
        != "INACTIVE_KILL_SWITCH_VERIFIED"
        or primary_hook_isolation.get("verified_before_install_activation")
        is not True
        or primary_hook_isolation.get("persistent_kill_switch") is not True
        or primary_receipt_path != receipt_path
        or str(
            primary_hook_isolation.get("kill_switch_receipt_sha256") or ""
        ).upper()
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
    stable = dict((registry.get("slots") or {}).get("stable-build") or {})
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
    """Preflight a canonical stable update without pruning historical slots.

    During the one-time migration the currently working stable route remains
    installed until the canonical Git route has been installed, prewarmed,
    selected exclusively, and hook-trusted.  A later refresh of the already
    canonical selector removes and re-adds that same selector while Codex is
    closed; the immutable disabled fallback remains untouched.
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
    fallback_selector: str | None = None
    prior_stable_selector: str | None = None
    if two_slot_authority is not None:
        registry = dict(two_slot_authority.get("registry") or {})
        slots = dict(registry.get("slots") or {})
        stable = dict(slots.get("stable-build") or {})
        fallback = dict(slots.get("fallback") or {})
        prior_stable_selector = str(stable.get("plugin_selector") or "")
        fallback_selector = str(fallback.get("plugin_selector") or "")
        by_selector = {
            str(row.get("pluginId") or ""): row for row in evidence_plugins
        }
        if (
            prior_stable_selector not in by_selector
            or by_selector[prior_stable_selector].get("enabled") is not True
            or fallback_selector not in by_selector
            or by_selector[fallback_selector].get("enabled") is not False
            or any(
                row.get("enabled") is not False
                for row in evidence_plugins
                if row.get("pluginId") != prior_stable_selector
            )
        ):
            raise InstallationError(
                "The sealed stable/fallback slots are not safe for an in-place update."
            )

    target_was_installed = any(
        row.get("pluginId") == plugin_selector for row in evidence_plugins
    )
    same_selector_refresh = prior_stable_selector == plugin_selector
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
                "The pre-existing canonical marketplace is not the governed Git "
                "source."
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
        "stable_removed_for_same_selector_reinstall": (
            same_selector_refresh and target_was_installed
        ),
        "target_marketplace_removed_for_exact_ref_refresh": target_marketplace_removed,
        "target_marketplace_preexisting": target_marketplace_preexisting,
        "target_marketplace_source_verified": target_marketplace_source_verified,
        "fallback_selector": fallback_selector,
        "fallback_removed": False,
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
    fallback_selector: str,
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
        or fallback_selector not in by_selector
        or by_selector[fallback_selector].get("enabled") is not False
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
    for selector in sorted(set(by_selector) - {plugin_selector, fallback_selector}):
        _run_codex(executable, codex_home, ["plugin", "remove", selector, "--json"])
        removed_selectors.append(selector)

    fallback_marketplace = fallback_selector.split("@", 1)[1]
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
        if (
            not name.startswith("evidence-lane-")
            or name in {MARKETPLACE_NAME, fallback_marketplace}
        ):
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
    final_by_selector = {
        str(row.get("pluginId") or ""): row for row in final_evidence
    }
    if (
        set(final_by_selector) != {plugin_selector, fallback_selector}
        or final_by_selector[plugin_selector].get("enabled") is not True
        or final_by_selector[fallback_selector].get("enabled") is not False
    ):
        raise InstallationError("Post-proof cleanup did not leave exactly two slots.")
    receipt = {
        "schema": "evidence-lane.codex-post-proof-slot-cleanup.v1",
        "status": "PASS",
        "canonical_stable_selector": plugin_selector,
        "fallback_selector": fallback_selector,
        "removed_obsolete_selectors": removed_selectors,
        "removed_obsolete_marketplaces": removed_marketplaces,
        "exact_installed_slot_count": 2,
        "enabled_slot_count": 1,
        "fallback_enabled": False,
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
            error_data = dict(error.get("data") or {}) if isinstance(error, dict) else {}
            conflict = error_data.get("config_write_error_code") == "configVersionConflict"
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
        len(hooks) != len(EXPECTED_CODEX_HOST_HOOK_EVENTS)
        or events != EXPECTED_CODEX_HOST_HOOK_EVENTS
        or len(keys) != len(set(keys))
        or any(not key.startswith(f"{plugin_selector}:") for key in keys)
    ):
        raise InstallationError("Codex plugin/read returned a drifted hook inventory.")
    return sorted(hooks, key=lambda row: str(row["eventName"]))


def _trusted_disabled_hook_records(
    *,
    parsed_config: dict[str, Any],
    plugin_selector: str,
    hook_inventory: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Bind native UI trust state to one sealed disabled-plugin inventory."""

    hooks_table = dict(parsed_config.get("hooks") or {})
    trust_state = dict(hooks_table.get("state") or {})
    expected_keys = {str(row["key"]) for row in hook_inventory}
    selector_keys = {
        str(key)
        for key in trust_state
        if str(key).startswith(f"{plugin_selector}:")
    }
    if selector_keys != expected_keys:
        raise InstallationError(
            "The disabled candidate does not have exact native UI hook trust."
        )
    records: list[dict[str, Any]] = []
    for row in hook_inventory:
        key = str(row["key"])
        trusted_hash = str(dict(trust_state.get(key) or {}).get("trusted_hash") or "")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", trusted_hash) is None:
            raise InstallationError(
                "The disabled candidate hook trust hash is absent or invalid."
            )
        records.append(
            {
                "event_name": str(row["eventName"]),
                "hook_key": key,
                "current_hash": trusted_hash,
                "enabled": False,
                "trust_status": "trusted",
            }
        )
    return records


def _trust_sealed_plugin_hooks(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    hook_cwd: Path,
    plugin_selector: str,
    defer_hook_trust_to_user: bool = False,
    activation_mode: str = "SWITCH_EXCLUSIVE",
    last_known_good_selector: str | None = None,
    last_known_good_config_sha256: str | None = None,
    rollback_config_backup: str | None = None,
    expected_commit_config_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write one CAS activation state and optionally trust exact hook hashes.

    Codex intentionally treats each cache-busted plugin selector as a new hook
    authority.  Merely installing and enabling the plugin therefore does not
    make its unmanaged hooks runnable.  This helper uses the same supported
    app-server ``plugin/read``, ``hooks/list``, and ``config/batchWrite`` routes
    used by Codex's plugin and hook review UI. It never edits ``config.toml``
    directly and never trusts a hook outside the exact selector supplied by the
    caller. Local-test preparation reads the disabled candidate through
    ``plugin/read``, keeps the last-known-good selector active, and never treats
    installation or isolated prewarm as host readiness.
    """

    if activation_mode not in {
        "SWITCH_EXCLUSIVE",
        "STAGE_CANDIDATE_DISABLED",
        "VERIFY_STAGED_CANDIDATE",
        "COMMIT_CANDIDATE",
        "RECOVER_DISABLED_LOCAL",
        "PREPARE_RECOVERY_DISABLED",
        "VERIFY_DISABLED_SUCCESSOR_FROM_SEALED_PRIMARY",
    }:
        raise InstallationError("The plugin activation transaction mode is invalid.")
    transactional_local_test = activation_mode in {
        "STAGE_CANDIDATE_DISABLED",
        "VERIFY_STAGED_CANDIDATE",
        "COMMIT_CANDIDATE",
    }
    disabled_local_recovery = activation_mode == "RECOVER_DISABLED_LOCAL"
    disabled_recovery_copy = activation_mode == "PREPARE_RECOVERY_DISABLED"
    disabled_successor_verification = (
        activation_mode == "VERIFY_DISABLED_SUCCESSOR_FROM_SEALED_PRIMARY"
    )
    if transactional_local_test and (
        not last_known_good_selector
        or last_known_good_selector == plugin_selector
        or not last_known_good_config_sha256
        or re.fullmatch(r"[A-F0-9]{64}", last_known_good_config_sha256) is None
        or not rollback_config_backup
    ):
        raise InstallationError(
            "Candidate staging requires one sealed last-known-good activation."
        )
    if activation_mode in {"VERIFY_STAGED_CANDIDATE", "COMMIT_CANDIDATE"} and (
        defer_hook_trust_to_user
        or re.fullmatch(
            r"[A-F0-9]{64}", str(expected_commit_config_sha256 or "").upper()
        )
        is None
    ):
        raise InstallationError(
            "Candidate commit requires one fresh exact config baseline and "
            "pre-existing visible hook trust."
        )
    if disabled_local_recovery and (
        defer_hook_trust_to_user
        or not last_known_good_selector
        or last_known_good_selector == plugin_selector
        or re.fullmatch(
            r"[A-F0-9]{64}", str(last_known_good_config_sha256 or "").upper()
        )
        is None
        or not rollback_config_backup
        or re.fullmatch(
            r"[A-F0-9]{64}", str(expected_commit_config_sha256 or "").upper()
        )
        is None
    ):
        raise InstallationError(
            "Disabled-local hook recovery requires one sealed all-disabled "
            "compare-and-swap baseline."
        )
    if disabled_recovery_copy and (
        defer_hook_trust_to_user
        or not last_known_good_selector
        or last_known_good_selector == plugin_selector
        or re.fullmatch(
            r"[A-F0-9]{64}", str(last_known_good_config_sha256 or "").upper()
        )
        is None
        or not rollback_config_backup
    ):
        raise InstallationError(
            "A disabled recovery copy requires one sealed primary-local baseline."
        )
    if transactional_local_test or disabled_local_recovery or disabled_recovery_copy:
        exact_backup = Path(str(rollback_config_backup)).resolve()
        if (
            not exact_backup.is_file()
            or not _inside(exact_backup, data_root.resolve())
            or _sha256(exact_backup) != last_known_good_config_sha256
        ):
            raise InstallationError(
                "The compare-and-swap rollback config is absent, outside authority, "
                "or hash-mismatched."
            )

    resolved_cwd = hook_cwd.resolve()
    if not resolved_cwd.is_dir():
        raise InstallationError("The exact hook workspace is unavailable.")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    process = subprocess.Popen(
        [str(executable), "app-server", "--stdio"],
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

    stdout_reader = threading.Thread(target=read_stdout, daemon=True)
    stderr_reader = threading.Thread(target=read_stderr, daemon=True)
    stdout_reader.start()
    stderr_reader.start()

    def send(payload: dict[str, Any]) -> None:
        try:
            stdin.write(
                json.dumps(payload, separators=(",", ":")) + "\n"
            )
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
            "Codex app-server did not return the required hook receipt"
            + (f": {detail}" if detail else ".")
        )

    def selector_hooks(
        reply: dict[str, Any],
        expected_selector: str = plugin_selector,
        expected_enabled: bool | None = True,
    ) -> list[dict[str, Any]]:
        data = dict(reply.get("result") or {}).get("data")
        if not isinstance(data, list):
            raise InstallationError("Codex hooks/list returned an invalid shape.")
        resolved_key = os.path.normcase(str(resolved_cwd))
        entries = [
            row
            for row in data
            if isinstance(row, dict)
            and os.path.normcase(str(Path(str(row.get("cwd") or "")).resolve()))
            == resolved_key
        ]
        if len(entries) != 1:
            raise InstallationError("Codex did not return one clean hook workspace.")
        marketplace_name = expected_selector.split("@", 1)[-1].lower()

        def relevant_diagnostics(values: Any) -> list[str]:
            diagnostics = [str(value) for value in values or []]
            return [
                value
                for value in diagnostics
                if marketplace_name in value.lower()
                or "evidence-lane-" not in value.lower()
            ]

        if relevant_diagnostics(entries[0].get("errors")):
            raise InstallationError("Codex returned an installed-hook error.")
        if relevant_diagnostics(entries[0].get("warnings")):
            raise InstallationError(
                "Codex returned an installed-hook warning; installation cannot pass."
            )
        hooks = [
            dict(row)
            for row in entries[0].get("hooks") or []
            if isinstance(row, dict) and row.get("pluginId") == expected_selector
        ]
        events = {str(row.get("eventName") or "") for row in hooks}
        keys = [str(row.get("key") or "") for row in hooks]
        if (
            len(hooks) != len(EXPECTED_CODEX_HOST_HOOK_EVENTS)
            or events != EXPECTED_CODEX_HOST_HOOK_EVENTS
            or len(keys) != len(set(keys))
            or any(
                row.get("source") != "plugin"
                or row.get("isManaged") is not False
                or (
                    expected_enabled is not None
                    and row.get("enabled") is not expected_enabled
                )
                or not str(row.get("key") or "").startswith(
                    f"{expected_selector}:"
                )
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(row.get("currentHash") or ""),
                )
                is None
                for row in hooks
            )
        ):
            raise InstallationError(
                "The exact installed selector's eight-hook authority drifted."
            )
        return sorted(hooks, key=lambda row: str(row["eventName"]))

    commit_mode = activation_mode == "COMMIT_CANDIDATE"
    verify_mode = activation_mode == "VERIFY_STAGED_CANDIDATE"
    atomic_switch_applied = False
    commit_pre_switch_plugins: dict[str, Any] | None = None
    commit_pre_switch_hooks: dict[str, Any] | None = None
    commit_pre_switch_config_sha256: str | None = None
    pre_switch_trusted_records: list[dict[str, Any]] | None = None
    plugin_add_transient_detected = False

    try:
        send(
            {
                "method": "initialize",
                "id": 1000,
                "params": {
                    "clientInfo": {
                        "name": "evidence_lane_installer",
                        "title": "Evidence Lane Installer",
                        "version": BASE_RELEASE,
                    }
                },
            }
        )
        wait_for(1000)
        send({"method": "initialized", "params": {}})
        config_path = codex_home / "config.toml"
        config_bytes = config_path.read_bytes()
        config_raw = config_bytes.decode("utf-8")
        parsed_config = tomllib.loads(config_raw)
        plugins = dict(parsed_config.get("plugins") or {})
        before_config_sha256 = hashlib.sha256(config_bytes).hexdigest().upper()
        if (
            (commit_mode or verify_mode or disabled_local_recovery)
            and before_config_sha256
            != str(expected_commit_config_sha256 or "").upper()
        ):
            raise InstallationError(
                "The local-test commit config changed after its host proof."
            )
        if plugin_selector not in plugins:
            raise InstallationError("Codex did not persist the v2 plugin selector.")
        enabled_selector = (
            str(last_known_good_selector)
            if activation_mode
            in {
                "STAGE_CANDIDATE_DISABLED",
                "VERIFY_STAGED_CANDIDATE",
                "PREPARE_RECOVERY_DISABLED",
                "VERIFY_DISABLED_SUCCESSOR_FROM_SEALED_PRIMARY",
            }
            else plugin_selector
        )
        if enabled_selector not in plugins:
            raise InstallationError(
                "The compare-and-swap activation target is absent from Codex config."
            )
        if commit_mode or verify_mode or disabled_local_recovery:
            evidence_states: dict[str, bool] = {}
            evidence_mcp_states: dict[str, bool] = {}
            for selector, raw_settings in plugins.items():
                if not selector.startswith(f"{PLUGIN_NAME}@"):
                    continue
                settings = dict(raw_settings or {})
                evidence_server = dict(
                    dict(settings.get("mcp_servers") or {}).get("evidence-lane")
                    or {}
                )
                plugin_enabled = bool(settings.get("enabled"))
                mcp_enabled = bool(evidence_server.get("enabled"))
                exact_plugin_add_transient = (
                    disabled_local_recovery
                    and selector == plugin_selector
                    and plugin_enabled is True
                    and mcp_enabled is False
                )
                if (
                    plugin_enabled is not mcp_enabled
                    and not exact_plugin_add_transient
                ):
                    raise InstallationError(
                        "The staged plugin and MCP activation states diverged."
                    )
                evidence_states[selector] = plugin_enabled
                evidence_mcp_states[selector] = mcp_enabled
                plugin_add_transient_detected = (
                    plugin_add_transient_detected
                    or exact_plugin_add_transient
                )
            if disabled_local_recovery:
                if (
                    plugin_selector not in evidence_states
                    or str(last_known_good_selector) not in evidence_states
                    or evidence_mcp_states.get(plugin_selector) is not False
                    or any(evidence_mcp_states.values())
                    or any(
                        enabled
                        for selector, enabled in evidence_states.items()
                        if selector != plugin_selector
                    )
                ):
                    raise InstallationError(
                        "Disabled-local hook recovery did not preserve the exact "
                        "all-disabled or plugin-add transient boundary."
                    )
            elif (
                evidence_states.get(plugin_selector) is not False
                or evidence_states.get(str(last_known_good_selector)) is not True
                or sum(evidence_states.values()) != 1
            ):
                raise InstallationError(
                    "The candidate is not disabled beside exactly one last-known-good "
                    "activation."
                )
            if disabled_local_recovery:
                rollback_config = tomllib.loads(
                    exact_backup.read_text(encoding="utf-8")
                )
                rollback_plugins = dict(rollback_config.get("plugins") or {})
                rollback_states = _evidence_plugin_activation_states(
                    rollback_plugins
                )
                if (
                    rollback_states.get(plugin_selector) is not False
                    or rollback_states.get(str(last_known_good_selector)) is not False
                    or any(rollback_states.values())
                ):
                    raise InstallationError(
                        "The disabled-local rollback archive is not all-disabled."
                    )
                commit_pre_switch_plugins = json.loads(
                    json.dumps(rollback_plugins)
                )
                commit_pre_switch_hooks = json.loads(
                    json.dumps(dict(rollback_config.get("hooks") or {}))
                )
                commit_pre_switch_config_sha256 = str(
                    last_known_good_config_sha256
                ).upper()
            else:
                commit_pre_switch_plugins = json.loads(json.dumps(plugins))
                commit_pre_switch_hooks = json.loads(
                    json.dumps(dict(parsed_config.get("hooks") or {}))
                )
                commit_pre_switch_config_sha256 = before_config_sha256
            if not disabled_local_recovery:
                disabled_inventory = _read_codex_plugin_hook_inventory(
                    send=send,
                    wait_for=wait_for,
                    codex_home=codex_home,
                    plugin_selector=plugin_selector,
                    expected_enabled=False,
                    request_id=1010,
                )
                pre_switch_trusted_records = _trusted_disabled_hook_records(
                    parsed_config=parsed_config,
                    plugin_selector=plugin_selector,
                    hook_inventory=disabled_inventory,
                )
            if verify_mode:
                receipt = {
                    "schema": HOOK_TRUST_SCHEMA,
                    "status": "PASS",
                    "plugin_selector": plugin_selector,
                    "hook_count": len(pre_switch_trusted_records),
                    "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
                    "records": pre_switch_trusted_records,
                    "before_trust_statuses": ["trusted"],
                    "after_trust_statuses": ["trusted"],
                    "supported_codex_api": ["plugin/read"],
                    "hook_trust_config_mutated": False,
                    "exclusive_channel_config_mutated": False,
                    "activation_mode": activation_mode,
                    "candidate_enabled": False,
                    "last_known_good_selector": last_known_good_selector,
                    "runtime_ready": False,
                    "workspace_sha256": hashlib.sha256(
                        os.path.normcase(str(resolved_cwd)).encode("utf-8")
                    ).hexdigest().upper(),
                    "raw_workspace_path_included": False,
                    "hook_commands_included": False,
                    "source_paths_included": False,
                    "unrelated_hook_state_mutated": False,
                }
                receipt["receipt_sha256"] = hashlib.sha256(
                    _json_bytes(receipt)
                ).hexdigest().upper()
                return receipt, {
                    "before_sha256": before_config_sha256,
                    "after_sha256": before_config_sha256,
                    "supported_codex_api": "plugin/read",
                    "compare_and_swap": False,
                    "config_mutated": False,
                    "activation_mode": activation_mode,
                    "candidate_selector": plugin_selector,
                    "candidate_enabled_after_read": False,
                    "last_known_good_selector": last_known_good_selector,
                    "last_known_good_enabled_after_read": True,
                    "switch_count": 0,
                }
        elif disabled_recovery_copy:
            evidence_states: dict[str, bool] = {}
            evidence_mcp_states: dict[str, bool] = {}
            for selector, raw_settings in plugins.items():
                if not selector.startswith(f"{PLUGIN_NAME}@"):
                    continue
                settings = dict(raw_settings or {})
                evidence_server = dict(
                    dict(settings.get("mcp_servers") or {}).get("evidence-lane")
                    or {}
                )
                plugin_enabled = bool(settings.get("enabled"))
                mcp_enabled = bool(evidence_server.get("enabled"))
                exact_plugin_add_transient = (
                    selector == plugin_selector
                    and plugin_enabled is True
                    and mcp_enabled is False
                )
                if plugin_enabled is not mcp_enabled and not exact_plugin_add_transient:
                    raise InstallationError(
                        "The recovery-copy plugin and MCP activation states diverged."
                    )
                evidence_states[selector] = plugin_enabled
                evidence_mcp_states[selector] = mcp_enabled
                plugin_add_transient_detected = (
                    plugin_add_transient_detected or exact_plugin_add_transient
                )
            if (
                plugin_selector not in evidence_states
                or str(last_known_good_selector) not in evidence_states
                or evidence_states.get(str(last_known_good_selector)) is not True
                or evidence_mcp_states.get(str(last_known_good_selector)) is not True
                or any(
                    enabled
                    for selector, enabled in evidence_states.items()
                    if selector not in {plugin_selector, str(last_known_good_selector)}
                )
            ):
                raise InstallationError(
                    "The recovery copy is not staged beside exactly one active "
                    "primary local selector."
                )
        changed_selectors: list[str] = []
        for selector, raw_settings in list(plugins.items()):
            if not selector.startswith(f"{PLUGIN_NAME}@"):
                continue
            settings = dict(raw_settings or {})
            desired = selector == enabled_selector
            servers = dict(settings.get("mcp_servers") or {})
            evidence_server = dict(servers.get("evidence-lane") or {})
            if (
                bool(settings.get("enabled")) is not desired
                or bool(evidence_server.get("enabled")) is not desired
            ):
                changed_selectors.append(selector)
            settings["enabled"] = desired
            evidence_server["enabled"] = desired
            servers["evidence-lane"] = evidence_server
            settings["mcp_servers"] = servers
            plugins[selector] = settings
        config_archive = (
            data_root
            / "installations"
            / "codex-v200"
            / "config-archives"
        )
        config_backup = config_archive / f"config-{before_config_sha256}.toml"
        if not config_backup.exists():
            _write_atomic(config_backup, config_bytes)
        channel_write = _batch_write_with_one_config_refresh(
            send=send,
            wait_for=wait_for,
            config_path=config_path,
            expected_raw_sha256=before_config_sha256,
            edits=[
                {
                    "keyPath": "plugins",
                    "value": plugins,
                    "mergeStrategy": (
                        "replace"
                        if commit_mode or disabled_local_recovery
                        else "upsert"
                    ),
                }
            ],
            request_ids=((1090, 1091), (1092, 1093)),
        )
        channel_config_version = str(channel_write["config_version"])
        expected_config_version = str(channel_write["cas_expected_version"])
        atomic_switch_applied = commit_mode or disabled_local_recovery
        verified_plugins = dict(
            tomllib.loads(config_path.read_text(encoding="utf-8")).get("plugins")
            or {}
        )
        for selector, settings in verified_plugins.items():
            if not selector.startswith(f"{PLUGIN_NAME}@"):
                continue
            desired = selector == enabled_selector
            mcp = dict(settings.get("mcp_servers") or {}).get("evidence-lane")
            if (
                bool(settings.get("enabled")) is not desired
                or not isinstance(mcp, dict)
                or bool(mcp.get("enabled")) is not desired
            ):
                raise InstallationError(
                    "The supported exclusive Evidence Lane channel write did not persist."
                )
        config_receipt = {
            "before_sha256": before_config_sha256,
            "after_sha256": _sha256(config_path),
            "backup": str(config_backup),
            "changed_selectors": sorted(set(changed_selectors)),
            "supported_codex_api": "config/batchWrite",
            "config_version": channel_config_version,
            "compare_and_swap": True,
            "activation_mode": activation_mode,
            "cas_expected_version": expected_config_version,
            "config_read_count": channel_write["config_read_count"],
            "config_write_attempt_count": channel_write[
                "config_write_attempt_count"
            ],
            "config_conflict_retry_count": channel_write[
                "config_conflict_retry_count"
            ],
            "candidate_selector": plugin_selector,
            "candidate_enabled_after_write": (
                enabled_selector == plugin_selector
            ),
            "last_known_good_selector": last_known_good_selector,
            "last_known_good_enabled_after_write": (
                bool(last_known_good_selector)
                and enabled_selector == last_known_good_selector
            ),
            "last_known_good_config_sha256": last_known_good_config_sha256,
            "prepared_rollback_config_backup": rollback_config_backup,
            "rollback_config_backup": str(config_backup),
            "rollback_config_backup_sha256": _sha256(config_backup),
            "commit_baseline_config_sha256": (
                before_config_sha256
                if commit_mode or disabled_local_recovery
                else None
            ),
            "plugin_add_transient_detected": (
                plugin_add_transient_detected
            ),
            "switch_count": (
                0
                if activation_mode
                in {
                    "STAGE_CANDIDATE_DISABLED",
                    "PREPARE_RECOVERY_DISABLED",
                    "VERIFY_DISABLED_SUCCESSOR_FROM_SEALED_PRIMARY",
                }
                else 1
            ),
            "previous_release_cache_deleted": False,
        }
        hook_inventory_bootstrap: dict[str, Any] | None = None
        # RECOVER_DISABLED_LOCAL deliberately discovers the newly installed
        # selector through hooks/list before it writes trust.  Requiring stale
        # hooks.state rows here creates a deadlock when the user has disabled
        # the old hooks (or when plugin/add correctly starts with no trust
        # rows).  hooks/list is the native currentHash authority; the single
        # trust write below seals and enables exactly those eight discovered
        # hashes, and the post-write hooks/list read proves the result.
        if activation_mode == "STAGE_CANDIDATE_DISABLED":
            staged_inventory = _read_codex_plugin_hook_inventory(
                send=send,
                wait_for=wait_for,
                codex_home=codex_home,
                plugin_selector=plugin_selector,
                expected_enabled=False,
                request_id=1002,
            )
            records = [
                {
                    "event_name": str(row["eventName"]),
                    "hook_key": str(row["key"]),
                    "current_hash": None,
                    "enabled": False,
                    "trust_status": "pending_native_ui_review",
                }
                for row in staged_inventory
            ]
            receipt = {
                "schema": "evidence-lane.codex-local-test-hook-trust.v1",
                "status": "USER_TRUST_PENDING",
                "decision_owner": "HUMAN_CODEX_UI",
                "plugin_selector": plugin_selector,
                "hook_count": len(records),
                "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
                "records": records,
                "before_trust_statuses": ["not_read_for_disabled_candidate"],
                "after_trust_statuses": ["pending_native_ui_review"],
                "supported_codex_api": ["plugin/read", "config/batchWrite"],
                "hook_trust_config_mutated": False,
                "exclusive_channel_config_mutated": True,
                "activation_mode": activation_mode,
                "candidate_enabled": False,
                "last_known_good_selector": last_known_good_selector,
                "runtime_ready": False,
                "workspace_sha256": hashlib.sha256(
                    os.path.normcase(str(resolved_cwd)).encode("utf-8")
                ).hexdigest().upper(),
                "raw_workspace_path_included": False,
                "hook_commands_included": False,
                "source_paths_included": False,
                "unrelated_hook_state_mutated": False,
                "hook_inventory_bootstrap": hook_inventory_bootstrap,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                _json_bytes(receipt)
            ).hexdigest().upper()
            return receipt, config_receipt
        if disabled_successor_verification:
            disabled_inventory = _read_codex_plugin_hook_inventory(
                send=send,
                wait_for=wait_for,
                codex_home=codex_home,
                plugin_selector=plugin_selector,
                expected_enabled=False,
                request_id=1002,
            )
            primary_selector = str(last_known_good_selector or "")
            if "@" not in primary_selector:
                raise InstallationError(
                    "The disabled successor has no sealed primary hook authority."
                )
            primary_marketplace = primary_selector.split("@", 1)[1]
            successor_marketplace = plugin_selector.split("@", 1)[1]
            primary_hooks_file = (
                codex_home
                / "local-marketplaces"
                / primary_marketplace
                / "plugins"
                / PLUGIN_NAME
                / "hooks"
                / "hooks.json"
            )
            successor_hooks_file = (
                codex_home
                / "local-marketplaces"
                / successor_marketplace
                / "plugins"
                / PLUGIN_NAME
                / "hooks"
                / "hooks.json"
            )
            if (
                not primary_hooks_file.is_file()
                or not successor_hooks_file.is_file()
                or _sha256(primary_hooks_file) != _sha256(successor_hooks_file)
            ):
                raise InstallationError(
                    "The loaded primary and disabled successor hook declarations "
                    "are not byte-identical."
                )
            current_config = tomllib.loads(
                config_path.read_text(encoding="utf-8")
            )
            current_states = dict(
                dict(current_config.get("hooks") or {}).get("state") or {}
            )
            primary_prefix = f"{primary_selector}:"
            primary_states = {
                str(key): dict(value or {})
                for key, value in current_states.items()
                if str(key).startswith(primary_prefix)
            }
            primary_by_suffix = {
                key[len(primary_selector) :]: value
                for key, value in primary_states.items()
            }
            if (
                len(primary_states) != len(EXPECTED_CODEX_HOST_HOOK_EVENTS)
                or any(
                    re.fullmatch(
                        r"sha256:[0-9a-f]{64}",
                        str(value.get("trusted_hash") or ""),
                    )
                    is None
                    for value in primary_states.values()
                )
            ):
                raise InstallationError(
                    "The loaded primary does not expose eight sealed hook hashes."
                )
            trust_value: dict[str, dict[str, Any]] = {}
            for key, value in primary_states.items():
                preserved = dict(value)
                preserved["enabled"] = False
                trust_value[key] = preserved
            records: list[dict[str, Any]] = []
            for row in disabled_inventory:
                key = str(row.get("key") or "")
                suffix = key[len(plugin_selector) :]
                primary_state = dict(primary_by_suffix.get(suffix) or {})
                trusted_hash = str(primary_state.get("trusted_hash") or "")
                if re.fullmatch(r"sha256:[0-9a-f]{64}", trusted_hash) is None:
                    raise InstallationError(
                        "The disabled successor hook cannot bind its primary hash."
                    )
                trust_value[key] = {
                    "trusted_hash": trusted_hash,
                    "enabled": False,
                }
                records.append(
                    {
                        "event_name": str(row.get("eventName") or ""),
                        "hook_key": key,
                        "current_hash": trusted_hash,
                        "enabled": False,
                        "trust_status": "trusted",
                    }
                )
            trust_before_sha256 = _sha256(config_path)
            trust_write = _batch_write_with_one_config_refresh(
                send=send,
                wait_for=wait_for,
                config_path=config_path,
                expected_raw_sha256=trust_before_sha256,
                edits=[
                    {
                        "keyPath": "hooks.state",
                        "value": trust_value,
                        "mergeStrategy": "upsert",
                    }
                ],
                request_ids=((1190, 1191), (1192, 1193)),
            )
            verified_config = tomllib.loads(
                config_path.read_text(encoding="utf-8")
            )
            verified_states = dict(
                dict(verified_config.get("hooks") or {}).get("state") or {}
            )
            if any(
                str(dict(verified_states.get(key) or {}).get("trusted_hash") or "")
                != str(value.get("trusted_hash") or "")
                or dict(verified_states.get(key) or {}).get("enabled") is not False
                for key, value in trust_value.items()
            ):
                raise InstallationError(
                    "The disabled successor trust or all-disabled hook boundary "
                    "did not persist."
                )
            receipt = {
                "schema": HOOK_TRUST_SCHEMA,
                "status": "PASS",
                "plugin_selector": plugin_selector,
                "hook_count": len(records),
                "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
                "records": sorted(records, key=lambda row: row["event_name"]),
                "before_trust_statuses": ["sealed_primary_hash_authority"],
                "after_trust_statuses": ["trusted"],
                "supported_codex_api": ["plugin/read", "config/batchWrite"],
                "hash_authority_selector": primary_selector,
                "byte_identical_hook_declarations": True,
                "hook_declaration_sha256": _sha256(primary_hooks_file),
                "hash_identity_excludes_plugin_selector_and_root": True,
                "config_version": str(trust_write["config_version"]),
                "hook_trust_config_mutated": True,
                "exclusive_channel_config_mutated": True,
                "activation_mode": activation_mode,
                "candidate_enabled": False,
                "last_known_good_selector": last_known_good_selector,
                "loaded_primary_hook_count": len(primary_states),
                "loaded_primary_hooks_all_disabled": True,
                "successor_hooks_all_disabled": True,
                "runtime_ready": False,
                "workspace_sha256": hashlib.sha256(
                    os.path.normcase(str(resolved_cwd)).encode("utf-8")
                ).hexdigest().upper(),
                "raw_workspace_path_included": False,
                "hook_commands_included": False,
                "source_paths_included": False,
                "unrelated_hook_state_mutated": False,
                "hook_inventory_bootstrap": None,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                _json_bytes(receipt)
            ).hexdigest().upper()
            return receipt, config_receipt
        if disabled_recovery_copy:
            disabled_inventory = _read_codex_plugin_hook_inventory(
                send=send,
                wait_for=wait_for,
                codex_home=codex_home,
                plugin_selector=plugin_selector,
                expected_enabled=False,
                request_id=1002,
            )
            primary_selector = str(last_known_good_selector or "")
            if "@" not in primary_selector:
                raise InstallationError(
                    "The disabled recovery copy has no exact primary hook authority."
                )
            primary_marketplace = primary_selector.split("@", 1)[1]
            recovery_marketplace = plugin_selector.split("@", 1)[1]
            primary_hooks_file = (
                codex_home
                / "local-marketplaces"
                / primary_marketplace
                / "plugins"
                / PLUGIN_NAME
                / "hooks"
                / "hooks.json"
            )
            recovery_hooks_file = (
                codex_home
                / "local-marketplaces"
                / recovery_marketplace
                / "plugins"
                / PLUGIN_NAME
                / "hooks"
                / "hooks.json"
            )
            if (
                not primary_hooks_file.is_file()
                or not recovery_hooks_file.is_file()
                or _sha256(primary_hooks_file) != _sha256(recovery_hooks_file)
            ):
                raise InstallationError(
                    "The primary and disabled recovery hook declarations are not byte-identical."
                )
            # Codex hashes the normalized declaration before substituting plugin-root
            # environment values.  Therefore a byte-identical declaration has the
            # same native currentHash under both selector keys.  plugin/read exposes
            # only the disabled selector's lightweight keys; hooks/list remains the
            # native hash authority for the active primary selector.
            send(
                {
                    "method": "hooks/list",
                    "id": 1003,
                    "params": {"cwds": [str(resolved_cwd)]},
                }
            )
            primary_inventory = selector_hooks(
                wait_for(1003),
                expected_selector=primary_selector,
            )
            if {
                str(row.get("trustStatus") or "") for row in primary_inventory
            } != {"trusted"}:
                raise InstallationError(
                    "The active primary hook hash authority is not trusted."
                )
            primary_hashes = {
                str(row.get("eventName") or ""): str(row.get("currentHash") or "")
                for row in primary_inventory
            }
            trust_value: dict[str, dict[str, str]] = {}
            records: list[dict[str, Any]] = []
            for row in disabled_inventory:
                event_name = str(row.get("eventName") or "")
                current_hash = primary_hashes.get(event_name, "")
                key = str(row.get("key") or "")
                if (
                    re.fullmatch(r"sha256:[0-9a-f]{64}", current_hash) is None
                    or not key.startswith(f"{plugin_selector}:")
                ):
                    raise InstallationError(
                        "The disabled recovery hook inventory is not sealable."
                    )
                trust_value[key] = {"trusted_hash": current_hash}
                records.append(
                    {
                        "event_name": event_name,
                        "hook_key": key,
                        "current_hash": current_hash,
                        "enabled": False,
                        "trust_status": "trusted",
                    }
                )
            trust_before_sha256 = _sha256(config_path)
            trust_write = _batch_write_with_one_config_refresh(
                send=send,
                wait_for=wait_for,
                config_path=config_path,
                expected_raw_sha256=trust_before_sha256,
                edits=[
                    {
                        "keyPath": "hooks.state",
                        "value": trust_value,
                        "mergeStrategy": "upsert",
                    }
                ],
                request_ids=((1190, 1191), (1192, 1193)),
            )
            verified_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            verified_plugins = dict(verified_config.get("plugins") or {})
            verified_hooks = dict(dict(verified_config.get("hooks") or {}).get("state") or {})
            for selector, settings in verified_plugins.items():
                if not selector.startswith(f"{PLUGIN_NAME}@"):
                    continue
                desired = selector == str(last_known_good_selector)
                server = dict(
                    dict(dict(settings or {}).get("mcp_servers") or {}).get(
                        "evidence-lane"
                    )
                    or {}
                )
                if (
                    bool(dict(settings or {}).get("enabled")) is not desired
                    or bool(server.get("enabled")) is not desired
                ):
                    raise InstallationError(
                        "The disabled recovery-copy channel state did not persist."
                    )
            if any(
                str(dict(verified_hooks.get(key) or {}).get("trusted_hash") or "")
                != value["trusted_hash"]
                for key, value in trust_value.items()
            ):
                raise InstallationError(
                    "The disabled recovery-copy hook trust did not persist."
                )
            receipt = {
                "schema": HOOK_TRUST_SCHEMA,
                "status": "PASS",
                "plugin_selector": plugin_selector,
                "hook_count": len(records),
                "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
                "records": sorted(records, key=lambda row: row["event_name"]),
                "before_trust_statuses": ["disabled_inventory_read"],
                "after_trust_statuses": ["trusted"],
                "supported_codex_api": [
                    "plugin/read",
                    "hooks/list",
                    "config/batchWrite",
                ],
                "hash_authority_selector": primary_selector,
                "byte_identical_hook_declarations": True,
                "hook_declaration_sha256": _sha256(primary_hooks_file),
                "hash_identity_excludes_plugin_selector_and_root": True,
                "config_version": str(trust_write["config_version"]),
                "hook_trust_config_mutated": True,
                "exclusive_channel_config_mutated": True,
                "activation_mode": activation_mode,
                "candidate_enabled": False,
                "last_known_good_selector": last_known_good_selector,
                "runtime_ready": False,
                "workspace_sha256": hashlib.sha256(
                    os.path.normcase(str(resolved_cwd)).encode("utf-8")
                ).hexdigest().upper(),
                "raw_workspace_path_included": False,
                "hook_commands_included": False,
                "source_paths_included": False,
                "unrelated_hook_state_mutated": False,
                "hook_inventory_bootstrap": hook_inventory_bootstrap,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                _json_bytes(receipt)
            ).hexdigest().upper()
            return receipt, config_receipt
        send(
            {
                "method": "hooks/list",
                "id": 1002,
                "params": {"cwds": [str(resolved_cwd)]},
            }
        )
        before = selector_hooks(
            wait_for(1002),
            expected_enabled=(None if disabled_local_recovery else True),
        )
        before_statuses = sorted(
            {str(row.get("trustStatus") or "unknown") for row in before}
        )
        before_enabled_states = sorted(
            {bool(row.get("enabled")) for row in before}
        )
        if commit_mode:
            if before_statuses != ["trusted"]:
                raise InstallationError(
                    "The candidate hook trust changed during the CAS switch."
                )
            records = [
                {
                    "event_name": str(row["eventName"]),
                    "hook_key": str(row["key"]),
                    "current_hash": str(row["currentHash"]),
                    "enabled": True,
                    "trust_status": "trusted",
                }
                for row in before
            ]
            receipt = {
                "schema": HOOK_TRUST_SCHEMA,
                "status": "PASS",
                "plugin_selector": plugin_selector,
                "hook_count": len(records),
                "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
                "records": records,
                "before_trust_statuses": ["trusted"],
                "after_trust_statuses": ["trusted"],
                "supported_codex_api": ["hooks/list", "config/batchWrite"],
                "config_version": channel_config_version,
                "hook_trust_config_mutated": False,
                "exclusive_channel_config_mutated": True,
                "activation_mode": activation_mode,
                "candidate_enabled": True,
                "last_known_good_selector": last_known_good_selector,
                "runtime_ready": False,
                "workspace_sha256": hashlib.sha256(
                    os.path.normcase(str(resolved_cwd)).encode("utf-8")
                ).hexdigest().upper(),
                "raw_workspace_path_included": False,
                "hook_commands_included": False,
                "source_paths_included": False,
                "unrelated_hook_state_mutated": False,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                _json_bytes(receipt)
            ).hexdigest().upper()
            return receipt, config_receipt
        if defer_hook_trust_to_user:
            records = [
                {
                    "event_name": str(row["eventName"]),
                    "hook_key": str(row["key"]),
                    "current_hash": str(row["currentHash"]),
                    "enabled": True,
                    "trust_status": str(row.get("trustStatus") or "unknown"),
                }
                for row in before
            ]
            receipt = {
                "schema": "evidence-lane.codex-local-test-hook-trust.v1",
                "status": "USER_TRUST_PENDING",
                "decision_owner": "HUMAN_CODEX_UI",
                "plugin_selector": plugin_selector,
                "hook_count": len(records),
                "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
                "records": records,
                "before_trust_statuses": before_statuses,
                "after_trust_statuses": before_statuses,
                "supported_codex_api": ["hooks/list", "config/batchWrite"],
                "config_version": channel_config_version,
                "hook_trust_config_mutated": False,
                "exclusive_channel_config_mutated": True,
                "activation_mode": activation_mode,
                "candidate_enabled": enabled_selector == plugin_selector,
                "last_known_good_selector": last_known_good_selector,
                "runtime_ready": False,
                "workspace_sha256": hashlib.sha256(
                    os.path.normcase(str(resolved_cwd)).encode("utf-8")
                ).hexdigest().upper(),
                "raw_workspace_path_included": False,
                "hook_commands_included": False,
                "source_paths_included": False,
                "unrelated_hook_state_mutated": False,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                _json_bytes(receipt)
            ).hexdigest().upper()
            return receipt, config_receipt
        trust_value = {
            str(row["key"]): {
                "trusted_hash": str(row["currentHash"]),
                "enabled": True,
            }
            for row in before
        }
        trust_before_sha256 = _sha256(config_path)
        trust_write = _batch_write_with_one_config_refresh(
            send=send,
            wait_for=wait_for,
            config_path=config_path,
            expected_raw_sha256=trust_before_sha256,
            edits=[
                {
                    "keyPath": "hooks.state",
                    "value": trust_value,
                    "mergeStrategy": "upsert",
                }
            ],
            request_ids=((1190, 1191), (1192, 1193)),
        )
        config_version = str(trust_write["config_version"])
        send(
            {
                "method": "hooks/list",
                "id": 1004,
                "params": {"cwds": [str(resolved_cwd)]},
            }
        )
        after = selector_hooks(wait_for(1004))
        if any(row.get("trustStatus") != "trusted" for row in after):
            raise InstallationError("The exact installed hooks remain untrusted.")
        records = [
            {
                "event_name": str(row["eventName"]),
                "hook_key": str(row["key"]),
                "current_hash": str(row["currentHash"]),
                "enabled": True,
                "trust_status": "trusted",
            }
            for row in after
        ]
        receipt = {
            "schema": HOOK_TRUST_SCHEMA,
            "status": "PASS",
            "plugin_selector": plugin_selector,
            "hook_count": len(records),
            "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
            "records": records,
            "before_trust_statuses": before_statuses,
            "before_enabled_states": before_enabled_states,
            "after_trust_statuses": ["trusted"],
            "after_enabled_states": [True],
            "supported_codex_api": ["hooks/list", "config/batchWrite"],
            "config_version": config_version,
            "workspace_sha256": hashlib.sha256(
                os.path.normcase(str(resolved_cwd)).encode("utf-8")
            ).hexdigest().upper(),
            "raw_workspace_path_included": False,
            "hook_commands_included": False,
            "source_paths_included": False,
            "unrelated_hook_state_mutated": False,
            "activation_mode": activation_mode,
            "candidate_enabled": enabled_selector == plugin_selector,
            "last_known_good_selector": last_known_good_selector,
            "disabled_local_recovery": disabled_local_recovery,
            "plugin_add_transient_detected": (
                plugin_add_transient_detected
            ),
            "hook_inventory_bootstrap": hook_inventory_bootstrap,
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            _json_bytes(receipt)
        ).hexdigest().upper()
        return receipt, config_receipt
    except Exception as exc:
        if (
            atomic_switch_applied
            and commit_pre_switch_plugins is not None
            and commit_pre_switch_config_sha256 is not None
        ):
            try:
                current_sha256 = _sha256(codex_home / "config.toml")
                rollback_write = _batch_write_with_one_config_refresh(
                    send=send,
                    wait_for=wait_for,
                    config_path=codex_home / "config.toml",
                    expected_raw_sha256=current_sha256,
                    edits=[
                        {
                            "keyPath": "plugins",
                            "value": commit_pre_switch_plugins,
                            "mergeStrategy": "replace",
                        },
                        *(
                            [
                                {
                                    "keyPath": "hooks",
                                    "value": commit_pre_switch_hooks or {},
                                    "mergeStrategy": "replace",
                                }
                            ]
                            if disabled_local_recovery
                            else []
                        ),
                    ],
                    request_ids=((1290, 1291), (1292, 1293)),
                )
                restored_config = tomllib.loads(
                    (codex_home / "config.toml").read_text(encoding="utf-8")
                )
                exact_restore = (
                    dict(restored_config.get("plugins") or {})
                    == commit_pre_switch_plugins
                    and (
                        not disabled_local_recovery
                        or dict(restored_config.get("hooks") or {})
                        == (commit_pre_switch_hooks or {})
                    )
                )
                if rollback_write["result"].get("status") != "ok" or not exact_restore:
                    raise InstallationError(
                        "Codex did not restore the exact pre-switch plugin/hook authority."
                    )
                if (
                    not disabled_local_recovery
                    and _sha256(codex_home / "config.toml")
                    != commit_pre_switch_config_sha256
                ):
                    raise InstallationError(
                        "Codex did not restore the exact pre-switch config."
                    )
            except Exception as rollback_exc:
                raise InstallationError(
                    "The local-test commit failed after switching and its supported "
                    "atomic rollback also failed closed."
                ) from rollback_exc
            boundary = (
                "disabled-local hook recovery"
                if disabled_local_recovery
                else "local-test commit"
            )
            raise InstallationError(
                f"The {boundary} failed after switching; Codex restored the "
                "exact pre-switch authority atomically."
            ) from exc
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def _seal_local_test_host_proof(
    *,
    stage_receipt_path: Path,
    stage_receipt_sha256: str,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    hook_cwd: Path,
) -> dict[str, Any]:
    """Prove a disabled candidate is trusted and exact without switching it."""

    exact_executable = executable.resolve()
    exact_codex_home = codex_home.resolve()
    exact_data_root = data_root.resolve()
    config_path = exact_codex_home / "config.toml"
    if not exact_executable.is_file() or not config_path.is_file():
        raise InstallationError(
            "Local-test host proof requires the exact Codex executable and config."
        )
    authority = _load_local_test_stage_authority(
        stage_receipt_path=stage_receipt_path,
        stage_receipt_sha256=stage_receipt_sha256,
        data_root=exact_data_root,
    )
    candidate_selector = authority["candidate_selector"]
    last_known_good_selector = authority["last_known_good_selector"]
    baseline_sha256 = _sha256(config_path)
    hook_trust, config_read = _trust_sealed_plugin_hooks(
        executable=exact_executable,
        codex_home=exact_codex_home,
        data_root=exact_data_root,
        hook_cwd=hook_cwd,
        plugin_selector=candidate_selector,
        defer_hook_trust_to_user=False,
        activation_mode="VERIFY_STAGED_CANDIDATE",
        last_known_good_selector=last_known_good_selector,
        last_known_good_config_sha256=authority[
            "last_known_good_config_sha256"
        ],
        rollback_config_backup=str(
            authority["prepared_rollback_config_backup"]
        ),
        expected_commit_config_sha256=baseline_sha256,
    )
    plugin_list = _run_codex(
        exact_executable,
        exact_codex_home,
        ["plugin", "list", "--json"],
    )
    installed_rows = plugin_list.get("installed")
    if not isinstance(installed_rows, list):
        raise InstallationError("Codex did not expose installed local-test identity.")
    evidence_rows = [
        dict(row)
        for row in installed_rows
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    candidate_rows = [
        row for row in evidence_rows if row.get("pluginId") == candidate_selector
    ]
    enabled_rows = [row for row in evidence_rows if row.get("enabled") is True]
    if (
        len(candidate_rows) != 1
        or candidate_rows[0].get("enabled") is not False
        or len(enabled_rows) != 1
        or enabled_rows[0].get("pluginId") != last_known_good_selector
    ):
        raise InstallationError(
            "Host proof requires one disabled candidate beside one enabled "
            "last-known-good selector."
        )
    stage = authority["stage"]
    expected_version = str(dict(stage.get("plugin") or {}).get("version") or "")
    installed_path_value = str(candidate_rows[0].get("installedPath") or "")
    if not installed_path_value:
        installed_path_value = str(
            dict(dict(stage.get("activation") or {}).get("plugin_add") or {}).get(
                "installedPath"
            )
            or ""
        )
    installed_path = Path(installed_path_value).resolve()
    expected_cache = (
        exact_codex_home
        / "plugins"
        / "cache"
        / LOCAL_TESTING_MARKETPLACE_NAME
        / PLUGIN_NAME
    )
    if (
        not installed_path.is_dir()
        or not _inside(installed_path, expected_cache)
        or candidate_rows[0].get("version") != expected_version
    ):
        raise InstallationError("The staged candidate cache identity drifted.")
    runtime_prewarm = _prewarm_installed_runtime(
        installed_path,
        data_root=exact_data_root,
    )
    if (
        runtime_prewarm.get("status") != "PASS"
        or runtime_prewarm.get("tool_count") != EXPECTED_CATALOG["tools"]
        or dict(runtime_prewarm.get("catalog_expected") or {}) != EXPECTED_CATALOG
    ):
        raise InstallationError("The staged candidate native catalog proof drifted.")
    readiness = _local_test_runtime_readiness(
        installed=True,
        restart_or_reload_completed=True,
        hooks_trusted=True,
        exact_identity_verified=True,
        exact_catalog_verified=True,
        prompt_capture_verified=False,
        smoke_probes_passed=False,
        active=False,
    )
    proof = {
        "schema": LOCAL_TEST_HOST_PROOF_SCHEMA,
        "status": "PASS",
        "transaction_id": authority["transaction"]["transaction_id"],
        "stage_installation_receipt_sha256": authority["stage_file_sha256"],
        "candidate_selector": candidate_selector,
        "last_known_good_selector": last_known_good_selector,
        "commit_baseline_config_sha256": baseline_sha256,
        "gates": {
            "human_hook_trust_completed": True,
            "host_restart_or_reload_completed": True,
            "exact_plugin_identity_verified": True,
            "exact_native_catalog_verified": True,
        },
        "host_reload_evidence": (
            "FRESH_APP_SERVER_PROCESS_LOADED_CURRENT_USER_CONFIG"
        ),
        "catalog": dict(EXPECTED_CATALOG),
        "hook_trust": hook_trust,
        "config_read": config_read,
        "runtime_prewarm": runtime_prewarm,
        "readiness": readiness,
        "runtime_ready_before_task_reopen": False,
        "prompt_capture_verified": False,
        "bounded_smoke_probes_passed": False,
        "task_binding_used_for_authorization": False,
        "goal_recovery_invoked": False,
        "installer_helper_is_separate": True,
        "tunnel_invoked": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    proof["receipt_sha256"] = hashlib.sha256(
        _json_bytes(proof)
    ).hexdigest().upper()
    proof_root = (
        exact_data_root
        / "installations"
        / "codex-v200"
        / "local-test-transactions"
    )
    proof_path = proof_root / (
        "HOST_PROOF_"
        + str(authority["transaction"]["transaction_id"])
        + "_"
        + uuid.uuid4().hex
        + ".json"
    )
    _write_atomic(proof_path, _json_bytes(proof))
    return {
        **proof,
        "receipt_path": str(proof_path),
        "receipt_file_sha256": _sha256(proof_path),
    }


def _commit_local_test_transaction(
    *,
    stage_receipt_path: Path,
    stage_receipt_sha256: str,
    host_proof_path: Path,
    host_proof_sha256: str,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    hook_cwd: Path,
) -> dict[str, Any]:
    """Commit one staged local candidate without borrowing Goal/tunnel identity."""

    exact_executable = executable.resolve()
    exact_codex_home = codex_home.resolve()
    exact_data_root = data_root.resolve()
    config_path = exact_codex_home / "config.toml"
    if not exact_executable.is_file() or not config_path.is_file():
        raise InstallationError(
            "The local-test commit requires the exact Codex executable and config."
        )
    authority = _load_local_test_commit_authority(
        stage_receipt_path=stage_receipt_path,
        stage_receipt_sha256=stage_receipt_sha256,
        host_proof_path=host_proof_path,
        host_proof_sha256=host_proof_sha256,
        data_root=exact_data_root,
    )
    baseline_sha256 = _sha256(config_path)
    if baseline_sha256 != authority["commit_baseline_config_sha256"]:
        raise InstallationError(
            "The config changed after the sealed local-test host proof."
        )
    transaction = authority["transaction"]
    transaction_id = str(transaction.get("transaction_id") or "")
    if re.fullmatch(r"local_test_tx_[0-9a-f]{40}", transaction_id) is None:
        raise InstallationError("The local-test transaction identity is invalid.")

    transaction_root = (
        exact_data_root
        / "installations"
        / "codex-v200"
        / "local-test-transactions"
    )
    rollback_root = (
        exact_data_root / "installations" / "codex-v200" / "config-archives"
    )
    rollback_backup = rollback_root / f"config-{baseline_sha256}.toml"
    if rollback_backup.exists():
        if _sha256(rollback_backup) != baseline_sha256:
            raise InstallationError("The local-test commit rollback archive drifted.")
    else:
        _write_atomic(rollback_backup, config_path.read_bytes())
    final_path = transaction_root / f"COMMIT_{transaction_id}.json"
    if final_path.exists():
        raise InstallationError(
            "The local-test transaction already has a commit receipt; replay is forbidden."
        )
    intent = {
        "schema": "evidence-lane.codex-local-test-cas-commit-intent.v1",
        "status": "PREPARED",
        "transaction_id": transaction_id,
        "stage_installation_receipt_sha256": authority["stage_file_sha256"],
        "host_proof_sha256": authority["host_proof_file_sha256"],
        "candidate_selector": authority["candidate_selector"],
        "last_known_good_selector": authority["last_known_good_selector"],
        "expected_config_sha256": baseline_sha256,
        "rollback_config_backup": str(rollback_backup),
        "rollback_config_backup_sha256": _sha256(rollback_backup),
        "switch_count_before": 0,
        "compare_and_swap": True,
        "task_binding_used_for_authorization": False,
        "goal_recovery_invoked": False,
        "installer_helper_is_separate": True,
        "tunnel_invoked": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    intent["receipt_sha256"] = hashlib.sha256(
        _json_bytes(intent)
    ).hexdigest().upper()
    intent_path = transaction_root / f"INTENT_{transaction_id}.json"
    if intent_path.exists():
        existing_intent = json.loads(intent_path.read_text(encoding="utf-8"))
        if existing_intent != intent:
            raise InstallationError("The local-test commit intent drifted.")
    else:
        _write_atomic(intent_path, _json_bytes(intent))

    hook_trust, config_receipt = _trust_sealed_plugin_hooks(
        executable=exact_executable,
        codex_home=exact_codex_home,
        data_root=exact_data_root,
        hook_cwd=hook_cwd,
        plugin_selector=authority["candidate_selector"],
        defer_hook_trust_to_user=False,
        activation_mode="COMMIT_CANDIDATE",
        last_known_good_selector=authority["last_known_good_selector"],
        last_known_good_config_sha256=authority[
            "last_known_good_config_sha256"
        ],
        rollback_config_backup=str(
            authority["prepared_rollback_config_backup"]
        ),
        expected_commit_config_sha256=baseline_sha256,
    )
    plugin_list = _run_codex(
        exact_executable,
        exact_codex_home,
        ["plugin", "list", "--json"],
    )
    installed_rows = plugin_list.get("installed")
    if not isinstance(installed_rows, list):
        raise InstallationError("Codex did not expose installed plugins after commit.")
    evidence_rows = [
        dict(row)
        for row in installed_rows
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    enabled_rows = [row for row in evidence_rows if row.get("enabled") is True]
    if (
        len(enabled_rows) != 1
        or enabled_rows[0].get("pluginId") != authority["candidate_selector"]
    ):
        raise InstallationError(
            "The local-test commit did not leave exactly the candidate enabled."
        )
    readiness = _local_test_runtime_readiness(
        installed=True,
        restart_or_reload_completed=False,
        hooks_trusted=True,
        exact_identity_verified=True,
        exact_catalog_verified=True,
        prompt_capture_verified=False,
        smoke_probes_passed=False,
        active=False,
    )
    receipt = {
        "schema": LOCAL_TEST_COMMIT_SCHEMA,
        "status": "PASS",
        "state": "CANDIDATE_SWITCHED_ONCE_RESTART_OR_RELOAD_REQUIRED",
        "transaction_id": transaction_id,
        "candidate_selector": authority["candidate_selector"],
        "last_known_good_selector": authority["last_known_good_selector"],
        "stage_installation_receipt_sha256": authority["stage_file_sha256"],
        "host_proof_sha256": authority["host_proof_file_sha256"],
        "commit_intent_sha256": _sha256(intent_path),
        "compare_and_swap": True,
        "switch_count": 1,
        "candidate_enabled": True,
        "last_known_good_enabled": False,
        "config": config_receipt,
        "hook_trust": hook_trust,
        "rollback_capable": True,
        "rollback_config_backup": str(rollback_backup),
        "rollback_config_backup_sha256": _sha256(rollback_backup),
        "readiness": readiness,
        "runtime_ready_before_task_reopen": False,
        "next_required_proof": [
            "POST_SWITCH_HOST_RESTART_OR_RELOAD",
            "EXACT_ACTIVE_PLUGIN_AND_NATIVE_CATALOG",
            "PROMPT_CAPTURE",
            "BOUNDED_SMOKE_PROBES",
        ],
        "accepted_two_slot_registry_mutated": False,
        "task_binding_used_for_authorization": False,
        "goal_recovery_invoked": False,
        "installer_helper_is_separate": True,
        "tunnel_invoked": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        _json_bytes(receipt)
    ).hexdigest().upper()
    _write_atomic(final_path, _json_bytes(receipt))
    return {
        **receipt,
        "receipt_path": str(final_path),
        "receipt_file_sha256": _sha256(final_path),
    }


def _load_local_test_recovery_authority(
    *,
    commit_receipt_path: Path,
    commit_receipt_sha256: str,
    data_root: Path,
) -> dict[str, Any]:
    """Load one committed local-test switch as self-rollback authority."""

    authority_root = data_root.resolve() / "installations" / "codex-v200"
    commit, resolved_commit, commit_file_sha256 = _load_self_sealed_json(
        path=commit_receipt_path,
        expected_file_sha256=commit_receipt_sha256,
        authority_root=authority_root,
        label="local-test commit receipt",
    )
    config = dict(commit.get("config") or {})
    readiness = dict(commit.get("readiness") or {})
    transaction_id = str(commit.get("transaction_id") or "")
    candidate_selector = str(commit.get("candidate_selector") or "")
    last_known_good_selector = str(
        commit.get("last_known_good_selector") or ""
    )
    rollback_backup = Path(
        str(commit.get("rollback_config_backup") or "")
    ).resolve()
    rollback_sha256 = str(
        commit.get("rollback_config_backup_sha256") or ""
    ).upper()
    failed_candidate_config_sha256 = str(config.get("after_sha256") or "").upper()
    if (
        commit.get("schema") != LOCAL_TEST_COMMIT_SCHEMA
        or commit.get("status") != "PASS"
        or commit.get("state")
        != "CANDIDATE_SWITCHED_ONCE_RESTART_OR_RELOAD_REQUIRED"
        or re.fullmatch(r"local_test_tx_[0-9a-f]{40}", transaction_id) is None
        or candidate_selector
        != f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"
        or not last_known_good_selector.startswith(f"{PLUGIN_NAME}@")
        or last_known_good_selector == candidate_selector
        or commit.get("compare_and_swap") is not True
        or commit.get("switch_count") != 1
        or commit.get("candidate_enabled") is not True
        or commit.get("last_known_good_enabled") is not False
        or commit.get("rollback_capable") is not True
        or not rollback_backup.is_file()
        or not _inside(rollback_backup, authority_root)
        or re.fullmatch(r"[A-F0-9]{64}", rollback_sha256) is None
        or _sha256(rollback_backup) != rollback_sha256
        or re.fullmatch(r"[A-F0-9]{64}", failed_candidate_config_sha256)
        is None
        or readiness.get("ready") is not False
        or commit.get("runtime_ready_before_task_reopen") is not False
        or commit.get("candidate_created_or_accepted") is not False
        or commit.get("pointer_moved") is not False
        or commit.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "The local-test commit receipt is not exact self-rollback authority."
        )
    return {
        "commit": commit,
        "commit_path": resolved_commit,
        "commit_file_sha256": commit_file_sha256,
        "transaction_id": transaction_id,
        "candidate_selector": candidate_selector,
        "last_known_good_selector": last_known_good_selector,
        "rollback_config_backup": rollback_backup,
        "rollback_config_backup_sha256": rollback_sha256,
        "failed_candidate_config_sha256": failed_candidate_config_sha256,
    }


def _evidence_plugin_activation_states(
    plugins: dict[str, Any],
) -> dict[str, bool]:
    """Return exact Evidence Lane plugin/MCP states or fail on divergence.

    Current Codex normalizes an explicitly disabled plugin by omitting its
    materialized ``mcp_servers`` block.  That absence is an exact disabled MCP
    state, not split-brain evidence.  It is accepted only while the plugin's
    own ``enabled`` value is explicitly ``False``; an enabled selector must
    always retain its native Evidence Lane MCP entry.
    """

    states: dict[str, bool] = {}
    for selector, raw_settings in plugins.items():
        if not selector.startswith(f"{PLUGIN_NAME}@"):
            continue
        settings = dict(raw_settings or {})
        servers = dict(settings.get("mcp_servers") or {})
        evidence_server = servers.get("evidence-lane")
        if evidence_server is None and settings.get("enabled") is False:
            states[selector] = False
            continue
        if not isinstance(evidence_server, dict):
            raise InstallationError(
                "An Evidence Lane selector has no matching native MCP state."
            )
        plugin_enabled = bool(settings.get("enabled"))
        mcp_enabled = bool(evidence_server.get("enabled"))
        if plugin_enabled is not mcp_enabled:
            raise InstallationError(
                "An Evidence Lane plugin/MCP activation pair is inconsistent."
            )
        states[selector] = plugin_enabled
    if not states:
        raise InstallationError("Codex config has no Evidence Lane selectors.")
    return states


def _normalize_disabled_local_mcp_boundary(
    plugins: dict[str, Any],
    *,
    plugin_selector: str,
) -> dict[str, Any]:
    """Normalize only Codex's exact stale local-MCP disabled-plugin split.

    A host restart may leave the local MCP bit true after the user disables the
    plugin selector.  Recovery may clear that one bit only when every Evidence
    Lane plugin bit is already explicitly false and every other materialized
    MCP bit is false.  The returned table is written through Codex's supported
    config API; this helper never writes configuration itself.
    """

    normalized = json.loads(json.dumps(plugins))
    evidence_selectors = [
        selector
        for selector in normalized
        if selector.startswith(f"{PLUGIN_NAME}@")
    ]
    if plugin_selector not in evidence_selectors:
        raise InstallationError(
            "The disabled local MCP normalization target is absent."
        )
    changed: list[str] = []
    for selector in evidence_selectors:
        settings = dict(normalized.get(selector) or {})
        if settings.get("enabled") is not False:
            raise InstallationError(
                "Disabled local MCP normalization requires every plugin disabled."
            )
        servers = dict(settings.get("mcp_servers") or {})
        evidence_server = servers.get("evidence-lane")
        if evidence_server is None:
            continue
        if not isinstance(evidence_server, dict):
            raise InstallationError(
                "Disabled local MCP normalization found an invalid MCP state."
            )
        mcp_enabled = evidence_server.get("enabled")
        if mcp_enabled is True:
            if selector != plugin_selector:
                raise InstallationError(
                    "Disabled local MCP normalization found a non-local live MCP."
                )
            evidence_server = dict(evidence_server)
            evidence_server["enabled"] = False
            servers["evidence-lane"] = evidence_server
            settings["mcp_servers"] = servers
            normalized[selector] = settings
            changed.append(selector)
        elif mcp_enabled is not False:
            raise InstallationError(
                "Disabled local MCP normalization found an ambiguous MCP bit."
            )
    if changed != [plugin_selector]:
        raise InstallationError(
            "Disabled local MCP normalization requires exactly one stale local MCP."
        )
    states = _evidence_plugin_activation_states(normalized)
    if any(states.values()):
        raise InstallationError(
            "Disabled local MCP normalization did not produce all-disabled state."
        )
    return normalized


def _normalize_local_post_add_boundary(
    plugins: dict[str, Any],
    *,
    plugin_selector: str,
) -> dict[str, Any]:
    """Clear only Codex's exact enabled-plugin/no-MCP post-add transient."""

    normalized = json.loads(json.dumps(plugins))
    evidence_selectors = [
        selector
        for selector in normalized
        if selector.startswith(f"{PLUGIN_NAME}@")
    ]
    if plugin_selector not in evidence_selectors:
        raise InstallationError(
            "The local post-add normalization target is absent."
        )
    for selector in evidence_selectors:
        settings = dict(normalized.get(selector) or {})
        servers = dict(settings.get("mcp_servers") or {})
        evidence_server = servers.get("evidence-lane")
        plugin_enabled = settings.get("enabled")
        if selector == plugin_selector:
            if plugin_enabled is not True:
                raise InstallationError(
                    "Local post-add normalization requires its plugin bit enabled."
                )
            if evidence_server is not None and (
                not isinstance(evidence_server, dict)
                or evidence_server.get("enabled") is not False
            ):
                raise InstallationError(
                    "Local post-add normalization requires its MCP absent or disabled."
                )
            settings["enabled"] = False
            exact_server = dict(evidence_server or {})
            exact_server["enabled"] = False
            servers["evidence-lane"] = exact_server
            settings["mcp_servers"] = servers
            normalized[selector] = settings
            continue
        if plugin_enabled is not False:
            raise InstallationError(
                "Local post-add normalization found another enabled plugin."
            )
        if evidence_server is not None and (
            not isinstance(evidence_server, dict)
            or evidence_server.get("enabled") is not False
        ):
            raise InstallationError(
                "Local post-add normalization found another live MCP."
            )
    states = _evidence_plugin_activation_states(normalized)
    if any(states.values()):
        raise InstallationError(
            "Local post-add normalization did not produce all-disabled state."
        )
    return normalized


def _normalize_active_local_reinstall_boundary(
    plugins: dict[str, Any],
    *,
    plugin_selector: str,
) -> dict[str, Any]:
    """Move one aligned active local selector to an exact all-disabled boundary."""

    normalized = json.loads(json.dumps(plugins))
    states = _evidence_plugin_activation_states(normalized)
    enabled = [selector for selector, active in states.items() if active]
    if enabled != [plugin_selector]:
        raise InstallationError(
            "Active local reinstall normalization requires only its exact selector enabled."
        )
    settings = dict(normalized.get(plugin_selector) or {})
    servers = dict(settings.get("mcp_servers") or {})
    evidence_server = dict(servers.get("evidence-lane") or {})
    if settings.get("enabled") is not True or evidence_server.get("enabled") is not True:
        raise InstallationError(
            "Active local reinstall normalization requires an aligned plugin/MCP pair."
        )
    settings["enabled"] = False
    evidence_server["enabled"] = False
    servers["evidence-lane"] = evidence_server
    settings["mcp_servers"] = servers
    normalized[plugin_selector] = settings
    final_states = _evidence_plugin_activation_states(normalized)
    if any(final_states.values()):
        raise InstallationError(
            "Active local reinstall normalization did not produce all-disabled state."
        )
    return normalized


def _batch_write_recovery_plugins(
    *,
    executable: Path,
    codex_home: Path,
    expected_before_sha256: str,
    plugins: dict[str, Any],
    exact_after_sha256: str | None = None,
) -> dict[str, Any]:
    """CAS-replace only the plugin table through Codex's supported app server."""

    config_path = codex_home.resolve() / "config.toml"
    expected_before = str(expected_before_sha256 or "").upper()
    expected_after = str(exact_after_sha256 or "").upper() or None
    if (
        not executable.resolve().is_file()
        or not config_path.is_file()
        or re.fullmatch(r"[A-F0-9]{64}", expected_before) is None
        or _sha256(config_path) != expected_before
        or (
            expected_after is not None
            and re.fullmatch(r"[A-F0-9]{64}", expected_after) is None
        )
    ):
        raise InstallationError(
            "The recovery config compare-and-swap baseline is absent or drifted."
        )
    _evidence_plugin_activation_states(plugins)
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home.resolve())
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
        raise InstallationError("Codex recovery app-server stdio was unavailable.")

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
            raise InstallationError(
                "Codex recovery app-server closed unexpectedly."
            ) from exc

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
                        "Codex recovery request failed: "
                        + json.dumps(payload["error"], sort_keys=True)
                    )
                return payload
        detail = " | ".join(stderr_lines[-3:])
        raise InstallationError(
            "Codex did not return the required recovery receipt"
            + (f": {detail}" if detail else ".")
        )

    try:
        send(
            {
                "method": "initialize",
                "id": 2000,
                "params": {
                    "clientInfo": {
                        "name": "evidence_lane_recovery",
                        "title": "Evidence Lane Candidate Self Rollback",
                        "version": BASE_RELEASE,
                    }
                },
            }
        )
        wait_for(2000)
        send({"method": "initialized", "params": {}})
        recovery_write = _batch_write_with_one_config_refresh(
            send=send,
            wait_for=wait_for,
            config_path=config_path,
            expected_raw_sha256=expected_before,
            edits=[
                {
                    "keyPath": "plugins",
                    "value": plugins,
                    "mergeStrategy": "replace",
                }
            ],
            request_ids=((2090, 2091), (2092, 2093)),
        )
        version = str(recovery_write["config_version"])
        after_sha256 = _sha256(config_path)
        if expected_after is not None and after_sha256 != expected_after:
            raise InstallationError(
                "Codex did not restore the exact prior config bytes."
            )
        persisted_plugins = dict(
            tomllib.loads(config_path.read_text(encoding="utf-8")).get(
                "plugins"
            )
            or {}
        )
        if persisted_plugins != plugins:
            raise InstallationError(
                "Codex did not persist the exact recovery plugin table."
            )
        return {
            "schema": "evidence-lane.codex-config-recovery-write.v1",
            "status": "PASS",
            "supported_codex_api": "config/batchWrite",
            "merge_strategy": "replace",
            "compare_and_swap": True,
            "before_sha256": expected_before,
            "after_sha256": after_sha256,
            "exact_after_sha256_required": expected_after,
            "config_version": version,
            "plugin_table_write_count": 1,
            "config_read_count": recovery_write["config_read_count"],
            "config_write_attempt_count": recovery_write[
                "config_write_attempt_count"
            ],
            "config_conflict_retry_count": recovery_write[
                "config_conflict_retry_count"
            ],
            "reload_user_config": True,
            "host_restart_requested": False,
            "unrelated_config_key_mutated": False,
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def _restore_exact_prior_local_test_config(
    *,
    authority: dict[str, Any],
    executable: Path,
    codex_home: Path,
) -> dict[str, Any]:
    """Disable the failed candidate and restore exact pre-switch config bytes."""

    config_path = codex_home.resolve() / "config.toml"
    backup = Path(authority["rollback_config_backup"]).resolve()
    backup_sha256 = str(authority["rollback_config_backup_sha256"])
    backup_plugins = dict(
        tomllib.loads(backup.read_text(encoding="utf-8")).get("plugins") or {}
    )
    states = _evidence_plugin_activation_states(backup_plugins)
    candidate_selector = str(authority["candidate_selector"])
    last_known_good_selector = str(authority["last_known_good_selector"])
    if (
        states.get(candidate_selector) is not False
        or states.get(last_known_good_selector) is not True
        or sum(states.values()) != 1
    ):
        raise InstallationError(
            "The rollback archive does not select exactly the last-known-good route."
        )
    current_sha256 = _sha256(config_path)
    if current_sha256 == backup_sha256:
        config_write = {
            "schema": "evidence-lane.codex-config-recovery-write.v1",
            "status": "PASS",
            "supported_codex_api": "NO_WRITE_ALREADY_EXACT",
            "compare_and_swap": True,
            "before_sha256": current_sha256,
            "after_sha256": current_sha256,
            "exact_after_sha256_required": backup_sha256,
            "plugin_table_write_count": 0,
            "reload_user_config": False,
            "host_restart_requested": False,
            "unrelated_config_key_mutated": False,
        }
    else:
        if current_sha256 != authority["failed_candidate_config_sha256"]:
            raise InstallationError(
                "The failed candidate config drifted before exact rollback."
            )
        config_write = _batch_write_recovery_plugins(
            executable=executable,
            codex_home=codex_home,
            expected_before_sha256=current_sha256,
            plugins=backup_plugins,
            exact_after_sha256=backup_sha256,
        )
    restored_plugins = dict(
        tomllib.loads(config_path.read_text(encoding="utf-8")).get("plugins") or {}
    )
    restored_states = _evidence_plugin_activation_states(restored_plugins)
    if (
        _sha256(config_path) != backup_sha256
        or restored_states.get(candidate_selector) is not False
        or restored_states.get(last_known_good_selector) is not True
        or sum(restored_states.values()) != 1
    ):
        raise InstallationError(
            "The exact prior config was not restored with the candidate disabled."
        )
    return {
        "status": "PASS",
        "candidate_selector": candidate_selector,
        "candidate_disabled": True,
        "restored_selector": last_known_good_selector,
        "restored_selector_enabled": True,
        "exact_prior_config_restored": True,
        "prior_config_sha256": backup_sha256,
        "config": config_write,
    }


def _load_byte_frozen_fallback_recovery_authority(
    *, data_root: Path
) -> dict[str, Any]:
    """Authorize fallback recovery only from the sealed immutable two-slot law."""

    registry_path = (
        data_root.resolve()
        / "installations"
        / "codex-v200"
        / "two-slot"
        / "CODEX_TWO_SLOT_REGISTRY.json"
    )
    if not registry_path.is_file():
        raise InstallationError(
            "The optional byte-frozen fallback has no sealed two-slot registry."
        )
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The optional fallback registry is unreadable."
        ) from exc
    if not isinstance(registry, dict):
        raise InstallationError("The optional fallback registry is not an object.")
    without_seal = {key: value for key, value in registry.items() if key != "seal"}
    without_internal_hashes = {
        key: value
        for key, value in registry.items()
        if key not in {"registry_body_sha256", "seal"}
    }
    seal = dict(registry.get("seal") or {})
    slots = dict(registry.get("slots") or {})
    stable = dict(slots.get("stable-build") or {})
    fallback = dict(slots.get("fallback") or {})
    accepted_pv = str(registry.get("accepted_pv") or "")
    accepted_generation = registry.get("accepted_generation")
    fallback_install = Path(str(fallback.get("install_receipt") or ""))
    fallback_install_sha256 = str(
        fallback.get("install_receipt_sha256") or ""
    ).upper()
    fallback_authority = _fallback_release_authority(
        registry=registry,
        fallback=fallback,
    )
    if (
        registry.get("schema") != "evidence-lane.codex-two-slot-registry.v1"
        or registry.get("status") != "PASS"
        or registry.get("state")
        != "STABLE_ACTIVE_FALLBACK_PREWARMED_DISABLED"
        or registry.get("post_fuse_materialized") is not True
        or re.fullmatch(r"PV[1-9][0-9]*", accepted_pv) is None
        or not isinstance(accepted_generation, int)
        or accepted_generation != int(accepted_pv[2:])
        or registry.get("exact_live_slot_count") != 2
        or registry.get("max_enabled_plugin_count") != 1
        or set(slots) != {"stable-build", "fallback"}
        or seal.get("algorithm") != "SHA256"
        or seal.get("body_sha256") != _ordered_json_sha256(without_seal)
        or registry.get("registry_body_sha256")
        != _ordered_json_sha256(without_internal_hashes)
        or stable.get("slot_role") != "stable-build"
        or stable.get("enabled") is not True
        or fallback.get("slot_role") != "fallback"
        or fallback.get("byte_frozen") is not True
        or fallback.get("enabled") is not False
        or fallback.get("native_mcp_enabled") is not False
        or fallback.get("accepted_pv") != accepted_pv
        or fallback.get("accepted_generation") != accepted_generation
        or fallback.get("package_sha256")
        != registry.get("accepted_package_sha256")
        or not fallback_install.is_file()
        or re.fullmatch(r"[A-F0-9]{64}", fallback_install_sha256) is None
        or _sha256(fallback_install) != fallback_install_sha256
        or fallback_authority.get("manifest_evidence_complete") is not True
    ):
        raise InstallationError(
            "The optional fallback is not exact sealed byte-frozen authority."
        )
    return {
        "registry_path": registry_path,
        "registry_file_sha256": _sha256(registry_path),
        "fallback_selector": str(fallback.get("plugin_selector") or ""),
        "fallback_version": str(fallback.get("plugin_version") or ""),
        "fallback_authority": fallback_authority,
        "accepted_pv": accepted_pv,
        "accepted_generation": accepted_generation,
    }


def _activate_byte_frozen_recovery_fallback(
    *,
    recovery_authority: dict[str, Any],
    fallback_authority: dict[str, Any],
    executable: Path,
    codex_home: Path,
) -> dict[str, Any]:
    """CAS-select the authorized fallback after exact stable restore failed health."""

    config_path = codex_home.resolve() / "config.toml"
    prior_sha256 = str(recovery_authority["rollback_config_backup_sha256"])
    if _sha256(config_path) != prior_sha256:
        raise InstallationError(
            "Fallback activation requires the exact prior config to be restored first."
        )
    plugins = dict(
        tomllib.loads(config_path.read_text(encoding="utf-8")).get("plugins") or {}
    )
    states = _evidence_plugin_activation_states(plugins)
    candidate_selector = str(recovery_authority["candidate_selector"])
    fallback_selector = str(fallback_authority["fallback_selector"])
    if (
        fallback_selector not in states
        or states.get(candidate_selector) is not False
        or states.get(fallback_selector) is not False
    ):
        raise InstallationError(
            "The sealed fallback locator is not installed disabled beside the rollback."
        )
    for selector, raw_settings in list(plugins.items()):
        if not selector.startswith(f"{PLUGIN_NAME}@"):
            continue
        settings = dict(raw_settings or {})
        servers = dict(settings.get("mcp_servers") or {})
        evidence_server = dict(servers.get("evidence-lane") or {})
        desired = selector == fallback_selector
        settings["enabled"] = desired
        evidence_server["enabled"] = desired
        servers["evidence-lane"] = evidence_server
        settings["mcp_servers"] = servers
        plugins[selector] = settings
    write = _batch_write_recovery_plugins(
        executable=executable,
        codex_home=codex_home,
        expected_before_sha256=prior_sha256,
        plugins=plugins,
    )
    final_plugins = dict(
        tomllib.loads(config_path.read_text(encoding="utf-8")).get("plugins") or {}
    )
    final_states = _evidence_plugin_activation_states(final_plugins)
    if (
        final_states.get(candidate_selector) is not False
        or final_states.get(fallback_selector) is not True
        or sum(final_states.values()) != 1
    ):
        raise InstallationError(
            "The byte-frozen fallback was not selected exclusively."
        )
    return {
        "status": "PASS",
        "candidate_disabled": True,
        "fallback_selector": fallback_selector,
        "fallback_enabled": True,
        "fallback_authority_sha256": dict(
            fallback_authority["fallback_authority"]
        )["fallback_authority_sha256"],
        "config": write,
    }


def _probe_recovered_selector_health(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    selected_selector: str,
    candidate_selector: str,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Probe one restored selector's installed identity and native catalog."""

    plugin_list = _run_codex(
        executable.resolve(),
        codex_home.resolve(),
        ["plugin", "list", "--json"],
    )
    installed = plugin_list.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex recovery health has no plugin list.")
    evidence_rows = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    selectors = [str(row.get("pluginId") or "") for row in evidence_rows]
    selected_rows = [
        row for row in evidence_rows if row.get("pluginId") == selected_selector
    ]
    enabled_rows = [row for row in evidence_rows if row.get("enabled") is True]
    candidate_rows = [
        row for row in evidence_rows if row.get("pluginId") == candidate_selector
    ]
    if (
        len(selectors) != len(set(selectors))
        or len(selected_rows) != 1
        or len(enabled_rows) != 1
        or enabled_rows[0].get("pluginId") != selected_selector
        or len(candidate_rows) != 1
        or candidate_rows[0].get("enabled") is not False
        or (
            expected_version is not None
            and selected_rows[0].get("version") != expected_version
        )
    ):
        raise InstallationError(
            "The recovered selector is not the sole exact active Evidence Lane route."
        )
    installed_path = Path(str(selected_rows[0].get("installedPath") or "")).resolve()
    if not installed_path.is_dir():
        raise InstallationError(
            "The recovered selector has no exact installed cache identity."
        )
    prewarm = _prewarm_installed_runtime(installed_path, data_root=data_root.resolve())
    if (
        prewarm.get("status") != "PASS"
        or prewarm.get("tool_count") != EXPECTED_CATALOG["tools"]
        or dict(prewarm.get("catalog_expected") or {}) != EXPECTED_CATALOG
    ):
        raise InstallationError(
            "The recovered selector failed its exact native catalog health probe."
        )
    return {
        "schema": "evidence-lane.codex-recovered-selector-health.v1",
        "status": "PASS",
        "selected_selector": selected_selector,
        "selected_version": str(selected_rows[0].get("version") or ""),
        "candidate_selector": candidate_selector,
        "candidate_disabled": True,
        "sole_enabled_evidence_lane_selector": True,
        "installed_path_sha256": hashlib.sha256(
            os.path.normcase(str(installed_path)).encode("utf-8")
        ).hexdigest().upper(),
        "runtime_prewarm_receipt_sha256": str(
            prewarm.get("receipt_sha256") or ""
        ),
        "native_catalog": dict(EXPECTED_CATALOG),
        "host_restart_requested": False,
        "task_reopened": False,
    }


def _recovery_attempt_correlation_id(
    *, transaction_id: str, commit_file_sha256: str, ordinal: int, target: str
) -> str:
    body = {
        "transaction_id": transaction_id,
        "commit_file_sha256": commit_file_sha256,
        "ordinal": ordinal,
        "target": target,
    }
    return "local_test_recovery_" + hashlib.sha256(_json_bytes(body)).hexdigest()[:40]


def _write_self_sealed_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    sealed = {key: item for key, item in value.items() if key != "receipt_sha256"}
    sealed["receipt_sha256"] = hashlib.sha256(
        _json_bytes(sealed)
    ).hexdigest().upper()
    _write_atomic(path, _json_bytes(sealed))
    return sealed


def _load_or_create_local_test_recovery_state(
    *, authority: dict[str, Any], state_path: Path
) -> dict[str, Any]:
    if state_path.exists():
        state, _, _ = _load_self_sealed_json(
            path=state_path,
            expected_file_sha256=_sha256(state_path),
            authority_root=state_path.parent,
            label="local-test recovery state",
        )
        if (
            state.get("schema") != LOCAL_TEST_RECOVERY_STATE_SCHEMA
            or state.get("transaction_id") != authority["transaction_id"]
            or state.get("commit_receipt_sha256")
            != authority["commit_file_sha256"]
            or state.get("max_attempts") != LOCAL_TEST_RECOVERY_MAX_ATTEMPTS
            or not isinstance(state.get("attempts"), list)
            or len(state["attempts"]) > LOCAL_TEST_RECOVERY_MAX_ATTEMPTS
        ):
            raise InstallationError("The local-test recovery state drifted.")
        return state
    return _write_self_sealed_json(
        state_path,
        {
            "schema": LOCAL_TEST_RECOVERY_STATE_SCHEMA,
            "status": "ACTIVE",
            "transaction_id": authority["transaction_id"],
            "commit_receipt_sha256": authority["commit_file_sha256"],
            "max_attempts": LOCAL_TEST_RECOVERY_MAX_ATTEMPTS,
            "attempts": [],
            "terminal_receipt_written": False,
            "restart_loop_started": False,
            "task_reopened": False,
        },
    )


def _record_local_test_recovery_attempt(
    *,
    state: dict[str, Any],
    state_path: Path,
    ordinal: int,
    target_kind: str,
    target_selector: str,
    correlation_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempts = [dict(row) for row in state.get("attempts") or []]
    matches = [row for row in attempts if row.get("ordinal") == ordinal]
    if len(matches) > 1:
        raise InstallationError("A recovery attempt ordinal was duplicated.")
    if matches:
        attempt = matches[0]
        if (
            attempt.get("target_kind") != target_kind
            or attempt.get("target_selector") != target_selector
            or attempt.get("correlation_id") != correlation_id
        ):
            raise InstallationError("A recovery attempt identity drifted.")
        return state, attempt
    if len(attempts) >= LOCAL_TEST_RECOVERY_MAX_ATTEMPTS:
        raise InstallationError("The local-test recovery attempt bound is exhausted.")
    attempt = {
        "ordinal": ordinal,
        "correlation_id": correlation_id,
        "target_kind": target_kind,
        "target_selector": target_selector,
        "status": "STARTED",
        "host_restart_requested": False,
        "task_reopened": False,
    }
    attempts.append(attempt)
    state["attempts"] = attempts
    state = _write_self_sealed_json(state_path, state)
    return state, attempt


def _finish_local_test_recovery_attempt(
    *,
    state: dict[str, Any],
    state_path: Path,
    ordinal: int,
    status: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    attempts = [dict(row) for row in state.get("attempts") or []]
    matches = [index for index, row in enumerate(attempts) if row.get("ordinal") == ordinal]
    if len(matches) != 1:
        raise InstallationError("The recovery attempt cannot be finalized exactly once.")
    index = matches[0]
    current = attempts[index]
    if current.get("status") == "PASS":
        return state
    current["status"] = status
    current["evidence"] = evidence
    attempts[index] = current
    state["attempts"] = attempts
    return _write_self_sealed_json(state_path, state)


def _terminal_local_test_recovery_receipt(
    *,
    authority: dict[str, Any],
    state: dict[str, Any],
    terminal_path: Path,
    fallback_authority: dict[str, Any] | None,
    fallback_authority_error: Exception | None,
) -> dict[str, Any]:
    attempts = [dict(row) for row in state.get("attempts") or []]
    passed = [row for row in attempts if row.get("status") == "PASS"]
    selected = passed[-1] if passed else None
    fallback_used = bool(
        selected and selected.get("target_kind") == "BYTE_FROZEN_FALLBACK"
    )
    exact_prior_restored = any(
        dict(dict(row.get("evidence") or {}).get("restore") or {}).get(
            "exact_prior_config_restored"
        )
        is True
        for row in attempts
    )
    candidate_disabled = any(
        dict(row.get("evidence") or {}).get("candidate_disabled") is True
        for row in attempts
    )
    status = "PASS" if selected is not None else "FAIL_CLOSED"
    if status == "PASS":
        final_state = (
            "RECOVERED_BYTE_FROZEN_FALLBACK"
            if fallback_used
            else "RECOVERED_EXACT_LAST_KNOWN_GOOD"
        )
    else:
        final_state = "TERMINAL_RECOVERY_EXHAUSTED_FAIL_CLOSED"
    fallback_error = None
    if fallback_authority_error is not None:
        fallback_error = {
            "error_class": type(fallback_authority_error).__name__,
            "error_sha256": hashlib.sha256(
                str(fallback_authority_error).encode("utf-8")
            ).hexdigest().upper(),
        }
    terminal = {
        "schema": LOCAL_TEST_RECOVERY_SCHEMA,
        "status": status,
        "state": final_state,
        "transaction_id": authority["transaction_id"],
        "commit_receipt_sha256": authority["commit_file_sha256"],
        "candidate_selector": authority["candidate_selector"],
        "last_known_good_selector": authority["last_known_good_selector"],
        "selected_selector": (
            str(selected.get("target_selector")) if selected is not None else None
        ),
        "controller_idempotent": True,
        "max_attempts": LOCAL_TEST_RECOVERY_MAX_ATTEMPTS,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "all_attempts_have_unique_correlation_ids": len(
            {str(row.get("correlation_id")) for row in attempts}
        )
        == len(attempts),
        "candidate_disabled": candidate_disabled,
        "exact_prior_config_restored": exact_prior_restored,
        "byte_frozen_fallback_used": fallback_used,
        "fallback_authority_sha256": (
            dict(fallback_authority["fallback_authority"])[
                "fallback_authority_sha256"
            ]
            if fallback_authority is not None
            else None
        ),
        "fallback_authority_error": fallback_error,
        "one_terminal_receipt": True,
        "restart_loop_started": False,
        "restart_or_reload_requests": 0,
        "task_reopened": False,
        "install_correction_generation_required": status == "PASS",
        "current_installation_updated": False,
        "runtime_ready_before_correction_generation": False,
        "accepted_two_slot_registry_mutated": False,
        "task_binding_used_for_authorization": False,
        "goal_recovery_invoked": False,
        "installer_helper_is_separate": True,
        "tunnel_invoked": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    sealed = _write_self_sealed_json(terminal_path, terminal)
    state["status"] = "TERMINAL"
    state["terminal_receipt_written"] = True
    state["terminal_receipt_sha256"] = _sha256(terminal_path)
    _write_self_sealed_json(
        terminal_path.parent / f"STATE_{authority['transaction_id']}.json",
        state,
    )
    return {
        **sealed,
        "receipt_path": str(terminal_path),
        "receipt_file_sha256": _sha256(terminal_path),
        "replayed": False,
    }


def _recover_local_test_candidate_failure(
    *,
    commit_receipt_path: Path,
    commit_receipt_sha256: str,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    allow_byte_frozen_fallback: bool,
) -> dict[str, Any]:
    """Run one idempotent bounded candidate self-rollback controller."""

    exact_executable = executable.resolve()
    exact_codex_home = codex_home.resolve()
    exact_data_root = data_root.resolve()
    config_path = exact_codex_home / "config.toml"
    if not exact_executable.is_file() or not config_path.is_file():
        raise InstallationError(
            "Candidate self-rollback requires the exact Codex executable and config."
        )
    authority = _load_local_test_recovery_authority(
        commit_receipt_path=commit_receipt_path,
        commit_receipt_sha256=commit_receipt_sha256,
        data_root=exact_data_root,
    )
    transaction_root = (
        exact_data_root
        / "installations"
        / "codex-v200"
        / "local-test-transactions"
    )
    terminal_path = transaction_root / f"RECOVERY_{authority['transaction_id']}.json"
    if terminal_path.exists():
        terminal, _, terminal_file_sha256 = _load_self_sealed_json(
            path=terminal_path,
            expected_file_sha256=_sha256(terminal_path),
            authority_root=transaction_root,
            label="terminal local-test recovery receipt",
        )
        if (
            terminal.get("schema") != LOCAL_TEST_RECOVERY_SCHEMA
            or terminal.get("transaction_id") != authority["transaction_id"]
            or terminal.get("commit_receipt_sha256")
            != authority["commit_file_sha256"]
            or terminal.get("one_terminal_receipt") is not True
        ):
            raise InstallationError("The terminal local-test recovery receipt drifted.")
        return {
            **terminal,
            "receipt_path": str(terminal_path),
            "receipt_file_sha256": terminal_file_sha256,
            "replayed": True,
        }

    state_path = transaction_root / f"STATE_{authority['transaction_id']}.json"
    state = _load_or_create_local_test_recovery_state(
        authority=authority,
        state_path=state_path,
    )
    fallback_authority: dict[str, Any] | None = None
    fallback_authority_error: Exception | None = None

    stable_correlation = _recovery_attempt_correlation_id(
        transaction_id=authority["transaction_id"],
        commit_file_sha256=authority["commit_file_sha256"],
        ordinal=1,
        target=authority["last_known_good_selector"],
    )
    state, stable_attempt = _record_local_test_recovery_attempt(
        state=state,
        state_path=state_path,
        ordinal=1,
        target_kind="EXACT_LAST_KNOWN_GOOD",
        target_selector=authority["last_known_good_selector"],
        correlation_id=stable_correlation,
    )
    if stable_attempt.get("status") == "STARTED":
        restore: dict[str, Any] | None = None
        try:
            restore = _restore_exact_prior_local_test_config(
                authority=authority,
                executable=exact_executable,
                codex_home=exact_codex_home,
            )
            health = _probe_recovered_selector_health(
                executable=exact_executable,
                codex_home=exact_codex_home,
                data_root=exact_data_root,
                selected_selector=authority["last_known_good_selector"],
                candidate_selector=authority["candidate_selector"],
            )
            state = _finish_local_test_recovery_attempt(
                state=state,
                state_path=state_path,
                ordinal=1,
                status="PASS",
                evidence={
                    "candidate_disabled": True,
                    "restore": restore,
                    "health": health,
                },
            )
        except InstallationError as exc:
            state = _finish_local_test_recovery_attempt(
                state=state,
                state_path=state_path,
                ordinal=1,
                status="FAILED",
                evidence={
                    "candidate_disabled": bool(
                        restore and restore.get("candidate_disabled") is True
                    ),
                    "restore": restore,
                    "error_class": type(exc).__name__,
                    "error_sha256": hashlib.sha256(
                        str(exc).encode("utf-8")
                    ).hexdigest().upper(),
                },
            )

    if any(row.get("status") == "PASS" for row in state["attempts"]):
        return _terminal_local_test_recovery_receipt(
            authority=authority,
            state=state,
            terminal_path=terminal_path,
            fallback_authority=None,
            fallback_authority_error=None,
        )

    exact_prior_restored = any(
        dict(dict(row.get("evidence") or {}).get("restore") or {}).get(
            "exact_prior_config_restored"
        )
        is True
        for row in state["attempts"]
    )
    if allow_byte_frozen_fallback and exact_prior_restored:
        try:
            fallback_authority = _load_byte_frozen_fallback_recovery_authority(
                data_root=exact_data_root
            )
            fallback_selector = str(fallback_authority["fallback_selector"])
            fallback_correlation = _recovery_attempt_correlation_id(
                transaction_id=authority["transaction_id"],
                commit_file_sha256=authority["commit_file_sha256"],
                ordinal=2,
                target=fallback_selector,
            )
            state, fallback_attempt = _record_local_test_recovery_attempt(
                state=state,
                state_path=state_path,
                ordinal=2,
                target_kind="BYTE_FROZEN_FALLBACK",
                target_selector=fallback_selector,
                correlation_id=fallback_correlation,
            )
            if fallback_attempt.get("status") == "STARTED":
                try:
                    activation = _activate_byte_frozen_recovery_fallback(
                        recovery_authority=authority,
                        fallback_authority=fallback_authority,
                        executable=exact_executable,
                        codex_home=exact_codex_home,
                    )
                    health = _probe_recovered_selector_health(
                        executable=exact_executable,
                        codex_home=exact_codex_home,
                        data_root=exact_data_root,
                        selected_selector=fallback_selector,
                        candidate_selector=authority["candidate_selector"],
                        expected_version=str(
                            fallback_authority["fallback_version"]
                        ),
                    )
                    state = _finish_local_test_recovery_attempt(
                        state=state,
                        state_path=state_path,
                        ordinal=2,
                        status="PASS",
                        evidence={
                            "candidate_disabled": True,
                            "fallback_activation": activation,
                            "health": health,
                        },
                    )
                except InstallationError as exc:
                    state = _finish_local_test_recovery_attempt(
                        state=state,
                        state_path=state_path,
                        ordinal=2,
                        status="FAILED",
                        evidence={
                            "candidate_disabled": True,
                            "error_class": type(exc).__name__,
                            "error_sha256": hashlib.sha256(
                                str(exc).encode("utf-8")
                            ).hexdigest().upper(),
                        },
                    )
        except InstallationError as exc:
            fallback_authority_error = exc

    return _terminal_local_test_recovery_receipt(
        authority=authority,
        state=state,
        terminal_path=terminal_path,
        fallback_authority=fallback_authority,
        fallback_authority_error=fallback_authority_error,
    )


def _probe_selector_hook_trust(
    *,
    executable: Path,
    codex_home: Path,
    hook_cwd: Path,
    plugin_selector: str,
) -> dict[str, Any]:
    """Read one selector's exact trusted eight-hook host authority."""

    resolved_cwd = hook_cwd.resolve()
    if not executable.resolve().is_file() or not resolved_cwd.is_dir():
        raise InstallationError(
            "Install correction hook proof requires exact executable and workspace."
        )
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home.resolve())
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
        raise InstallationError("Codex hook-proof app-server stdio was unavailable.")
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
            raise InstallationError(
                "Codex hook-proof app-server closed unexpectedly."
            ) from exc

    def wait_for(request_id: int, *, timeout: float = 20.0) -> dict[str, Any]:
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
                if "error" in payload:
                    raise InstallationError(
                        "Codex hook-proof request failed: "
                        + json.dumps(payload["error"], sort_keys=True)
                    )
                return payload
        detail = " | ".join(stderr_lines[-3:])
        raise InstallationError(
            "Codex did not return the correction hook proof"
            + (f": {detail}" if detail else ".")
        )

    try:
        send(
            {
                "method": "initialize",
                "id": 3000,
                "params": {
                    "clientInfo": {
                        "name": "evidence_lane_correction",
                        "title": "Evidence Lane Install Correction",
                        "version": BASE_RELEASE,
                    }
                },
            }
        )
        wait_for(3000)
        send({"method": "initialized", "params": {}})
        send(
            {
                "method": "hooks/list",
                "id": 3001,
                "params": {"cwds": [str(resolved_cwd)]},
            }
        )
        data = dict(wait_for(3001).get("result") or {}).get("data")
        if not isinstance(data, list):
            raise InstallationError("Codex correction hooks/list shape is invalid.")
        resolved_key = os.path.normcase(str(resolved_cwd))
        workspaces = [
            row
            for row in data
            if isinstance(row, dict)
            and os.path.normcase(str(Path(str(row.get("cwd") or "")).resolve()))
            == resolved_key
        ]
        if (
            len(workspaces) != 1
            or workspaces[0].get("errors")
            or workspaces[0].get("warnings")
        ):
            raise InstallationError(
                "Codex did not expose one clean correction hook workspace."
            )
        hooks = [
            dict(row)
            for row in workspaces[0].get("hooks") or []
            if isinstance(row, dict) and row.get("pluginId") == plugin_selector
        ]
        events = {str(row.get("eventName") or "") for row in hooks}
        keys = [str(row.get("key") or "") for row in hooks]
        if (
            len(hooks) != len(EXPECTED_CODEX_HOST_HOOK_EVENTS)
            or events != EXPECTED_CODEX_HOST_HOOK_EVENTS
            or len(keys) != len(set(keys))
            or any(
                row.get("source") != "plugin"
                or row.get("isManaged") is not False
                or row.get("enabled") is not True
                or row.get("trustStatus") != "trusted"
                or not str(row.get("key") or "").startswith(
                    f"{plugin_selector}:"
                )
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(row.get("currentHash") or ""),
                )
                is None
                for row in hooks
            )
        ):
            raise InstallationError(
                "The correction selector's exact trusted hook authority drifted."
            )
        receipt = {
            "schema": HOOK_TRUST_SCHEMA,
            "status": "PASS",
            "plugin_selector": plugin_selector,
            "hook_count": len(hooks),
            "registered_events": sorted(events),
            "records": [
                {
                    "event_name": str(row["eventName"]),
                    "hook_key": str(row["key"]),
                    "current_hash": str(row["currentHash"]),
                    "enabled": True,
                    "trust_status": "trusted",
                }
                for row in sorted(hooks, key=lambda item: str(item["eventName"]))
            ],
            "before_trust_statuses": ["trusted"],
            "after_trust_statuses": ["trusted"],
            "supported_codex_api": ["hooks/list"],
            "hook_trust_config_mutated": False,
            "workspace_sha256": hashlib.sha256(
                resolved_key.encode("utf-8")
            ).hexdigest().upper(),
            "raw_workspace_path_included": False,
            "unrelated_hook_state_mutated": False,
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            _json_bytes(receipt)
        ).hexdigest().upper()
        return receipt
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def _task_binding_inventory(data_root: Path) -> dict[str, Any]:
    """Seal installer and Goal-recovery task rows without mutating either registry."""

    installation_root = data_root.resolve() / "installations" / "codex-v200"
    registries = {
        "INSTALLER_TASK_BINDING": installation_root / "task-bindings",
        "GOAL_RECOVERY_BINDING": installation_root / "goal-recovery" / "bindings",
    }
    records: list[dict[str, Any]] = []
    for registry_kind, root in registries.items():
        if not root.exists():
            continue
        if not root.is_dir() or not _inside(root, installation_root):
            raise InstallationError("A correction task-binding registry is invalid.")
        for path in sorted(root.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise InstallationError(
                    "A correction task-binding row is unreadable."
                ) from exc
            if not isinstance(raw, dict):
                raise InstallationError(
                    "A correction task-binding row has an invalid shape."
                )
            payload = raw
            payload_sha256: str | None = None
            if registry_kind == "GOAL_RECOVERY_BINDING":
                payload = dict(raw.get("payload") or {})
                payload_sha256 = str(raw.get("payload_sha256") or "").upper()
                if (
                    raw.get("schema")
                    != "evidence-lane.codex-goal-recovery-binding.v1"
                    or re.fullmatch(r"[A-F0-9]{64}", payload_sha256) is None
                    or _ordered_json_sha256(payload) != payload_sha256
                ):
                    raise InstallationError(
                        "A Goal-recovery task binding has a drifted payload seal."
                    )
            elif raw.get("schema") != "evidence-lane.codex-task-binding.v1":
                raise InstallationError(
                    "An installer task binding has an unexpected schema."
                )
            task_id = str(payload.get("task_id") or "")
            if not task_id or path.stem.lower() != task_id.lower():
                raise InstallationError(
                    "A correction task-binding filename does not match its task."
                )
            records.append(
                {
                    "registry": registry_kind,
                    "task_id": task_id,
                    "project_id": str(payload.get("project_id") or ""),
                    "evidence_session_id": str(
                        payload.get("evidence_session_id") or ""
                    ),
                    "state": str(payload.get("state") or ""),
                    "plugin_version": str(payload.get("plugin_version") or ""),
                    "task_uri_sha256": str(payload.get("task_uri_sha256") or ""),
                    "install_receipt_sha256": str(
                        payload.get("install_receipt_sha256") or ""
                    ),
                    "payload_sha256": payload_sha256,
                    "file_sha256": _sha256(path),
                    "relative_locator": path.relative_to(
                        installation_root
                    ).as_posix(),
                }
            )
    inventory = {
        "schema": "evidence-lane.codex-task-binding-inventory.v1",
        "status": "PASS",
        "record_count": len(records),
        "records": records,
        "registry_bytes_mutated": False,
        "exact_task_refresh_required_before_task_reopen": True,
    }
    inventory["inventory_sha256"] = hashlib.sha256(
        _json_bytes(inventory)
    ).hexdigest().upper()
    return inventory


def _recovery_config_lineage(recovery: dict[str, Any]) -> dict[str, Any]:
    """Project the sealed before/after hashes of every recovery config action."""

    attempts = [dict(row) for row in recovery.get("attempts") or []]
    selected = [row for row in attempts if row.get("status") == "PASS"]
    if len(selected) != 1:
        raise InstallationError(
            "The successful recovery does not select one exact correction attempt."
        )
    actions: list[dict[str, Any]] = []
    for attempt in attempts:
        evidence = dict(attempt.get("evidence") or {})
        restore = dict(evidence.get("restore") or {})
        restore_config = dict(restore.get("config") or {})
        if restore_config:
            actions.append(
                {
                    "ordinal": len(actions) + 1,
                    "correlation_id": attempt.get("correlation_id"),
                    "kind": "EXACT_PRIOR_CONFIG_RESTORE",
                    "before_sha256": str(
                        restore_config.get("before_sha256") or ""
                    ).upper(),
                    "after_sha256": str(
                        restore_config.get("after_sha256") or ""
                    ).upper(),
                    "supported_codex_api": restore_config.get(
                        "supported_codex_api"
                    ),
                    "write_count": int(
                        restore_config.get("plugin_table_write_count") or 0
                    ),
                }
            )
        fallback = dict(evidence.get("fallback_activation") or {})
        fallback_config = dict(fallback.get("config") or {})
        if fallback_config:
            actions.append(
                {
                    "ordinal": len(actions) + 1,
                    "correlation_id": attempt.get("correlation_id"),
                    "kind": "BYTE_FROZEN_FALLBACK_SELECTION",
                    "before_sha256": str(
                        fallback_config.get("before_sha256") or ""
                    ).upper(),
                    "after_sha256": str(
                        fallback_config.get("after_sha256") or ""
                    ).upper(),
                    "supported_codex_api": fallback_config.get(
                        "supported_codex_api"
                    ),
                    "write_count": int(
                        fallback_config.get("plugin_table_write_count") or 0
                    ),
                }
            )
    if not actions:
        raise InstallationError("The recovery has no sealed config correction action.")
    for index, action in enumerate(actions):
        if (
            re.fullmatch(r"[A-F0-9]{64}", action["before_sha256"]) is None
            or re.fullmatch(r"[A-F0-9]{64}", action["after_sha256"]) is None
            or action["supported_codex_api"]
            not in {"config/batchWrite", "NO_WRITE_ALREADY_EXACT"}
            or action["write_count"] not in {0, 1}
            or (
                action["supported_codex_api"] == "config/batchWrite"
                and action["write_count"] != 1
            )
            or (
                index > 0
                and actions[index - 1]["after_sha256"]
                != action["before_sha256"]
            )
        ):
            raise InstallationError(
                "A recovery config correction action is unsealed or discontinuous."
            )
    return {
        "schema": "evidence-lane.codex-correction-config-lineage.v1",
        "status": "PASS",
        "action_count": len(actions),
        "actions": actions,
        "before_sha256": actions[0]["before_sha256"],
        "after_sha256": actions[-1]["after_sha256"],
        "all_actions_compare_and_swap": True,
        "direct_config_write_used": False,
    }


def _load_install_correction_authority(
    *,
    recovery_receipt_path: Path,
    recovery_receipt_sha256: str,
    base_installation_path: Path,
    base_installation_sha256: str,
    data_root: Path,
) -> dict[str, Any]:
    """Join one successful rollback to one exact selected installation base."""

    installation_root = data_root.resolve() / "installations" / "codex-v200"
    recovery, resolved_recovery, recovery_file_sha256 = _load_self_sealed_json(
        path=recovery_receipt_path,
        expected_file_sha256=recovery_receipt_sha256,
        authority_root=installation_root,
        label="successful local-test recovery receipt",
    )
    base, resolved_base, base_file_sha256 = _load_self_sealed_json(
        path=base_installation_path,
        expected_file_sha256=base_installation_sha256,
        authority_root=installation_root,
        label="corrected installation base receipt",
    )
    selected_selector = str(recovery.get("selected_selector") or "")
    candidate_selector = str(recovery.get("candidate_selector") or "")
    base_activation = dict(base.get("activation") or {})
    base_plugin_add = dict(base_activation.get("plugin_add") or {})
    if (
        recovery.get("schema") != LOCAL_TEST_RECOVERY_SCHEMA
        or recovery.get("status") != "PASS"
        or recovery.get("one_terminal_receipt") is not True
        or recovery.get("candidate_disabled") is not True
        or recovery.get("exact_prior_config_restored") is not True
        or recovery.get("install_correction_generation_required") is not True
        or recovery.get("current_installation_updated") is not False
        or not selected_selector.startswith(f"{PLUGIN_NAME}@")
        or candidate_selector
        != f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"
        or selected_selector == candidate_selector
        or base.get("schema") != INSTALL_SCHEMA
        or base.get("status") != "PASS"
        or base_plugin_add.get("pluginId") != selected_selector
        or not str(dict(base.get("plugin") or {}).get("version") or "")
    ):
        raise InstallationError(
            "The recovery and installation base do not authorize a correction generation."
        )
    return {
        "recovery": recovery,
        "recovery_path": resolved_recovery,
        "recovery_file_sha256": recovery_file_sha256,
        "base": base,
        "base_path": resolved_base,
        "base_file_sha256": base_file_sha256,
        "selected_selector": selected_selector,
        "candidate_selector": candidate_selector,
        "config_lineage": _recovery_config_lineage(recovery),
    }


def _probe_install_correction_host_state(
    *,
    authority: dict[str, Any],
    executable: Path,
    codex_home: Path,
    data_root: Path,
    hook_cwd: Path,
) -> dict[str, Any]:
    """Freshly prove config, selector, cache, hooks, and native runtime."""

    config_path = codex_home.resolve() / "config.toml"
    config_sha256 = _sha256(config_path)
    if config_sha256 != authority["config_lineage"]["after_sha256"]:
        raise InstallationError(
            "The live config does not equal the correction lineage's final hash."
        )
    plugins = dict(
        tomllib.loads(config_path.read_text(encoding="utf-8")).get("plugins") or {}
    )
    states = _evidence_plugin_activation_states(plugins)
    selected_selector = str(authority["selected_selector"])
    candidate_selector = str(authority["candidate_selector"])
    if (
        states.get(selected_selector) is not True
        or states.get(candidate_selector) is not False
        or sum(states.values()) != 1
    ):
        raise InstallationError(
            "The correction config does not select exactly the recovered route."
        )
    plugin_list = _run_codex(
        executable.resolve(),
        codex_home.resolve(),
        ["plugin", "list", "--json"],
    )
    installed = plugin_list.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex correction proof has no installed plugin list.")
    evidence_rows = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    selected_rows = [
        row for row in evidence_rows if row.get("pluginId") == selected_selector
    ]
    candidate_rows = [
        row for row in evidence_rows if row.get("pluginId") == candidate_selector
    ]
    enabled_rows = [row for row in evidence_rows if row.get("enabled") is True]
    if (
        len({str(row.get("pluginId") or "") for row in evidence_rows})
        != len(evidence_rows)
        or len(selected_rows) != 1
        or len(candidate_rows) != 1
        or candidate_rows[0].get("enabled") is not False
        or len(enabled_rows) != 1
        or enabled_rows[0].get("pluginId") != selected_selector
    ):
        raise InstallationError(
            "The correction host does not expose one recovered selector."
        )
    selected = selected_rows[0]
    installed_path = Path(str(selected.get("installedPath") or "")).resolve()
    plugin_version = str(selected.get("version") or "")
    base_version = str(dict(authority["base"].get("plugin") or {}).get("version") or "")
    manifest_path = installed_path / ".codex-plugin" / "plugin.json"
    if (
        not installed_path.is_dir()
        or not manifest_path.is_file()
        or plugin_version != base_version
    ):
        raise InstallationError(
            "The correction cache/plugin identity does not match its sealed base."
        )
    hook_trust = _probe_selector_hook_trust(
        executable=executable,
        codex_home=codex_home,
        hook_cwd=hook_cwd,
        plugin_selector=selected_selector,
    )
    runtime = _prewarm_installed_runtime(
        installed_path,
        data_root=data_root.resolve(),
    )
    if (
        runtime.get("status") != "PASS"
        or runtime.get("tool_count") != EXPECTED_CATALOG["tools"]
        or dict(runtime.get("catalog_expected") or {}) != EXPECTED_CATALOG
    ):
        raise InstallationError(
            "The correction native runtime proof does not match the exact catalog."
        )
    proof = {
        "schema": "evidence-lane.codex-install-correction-host-proof.v1",
        "status": "PASS",
        "config_sha256": config_sha256,
        "selected_selector": selected_selector,
        "candidate_selector": candidate_selector,
        "candidate_disabled": True,
        "sole_enabled_evidence_lane_selector": True,
        "plugin_identity": {
            "plugin_id": PLUGIN_NAME,
            "plugin_selector": selected_selector,
            "version": plugin_version,
            "plugin_manifest_sha256": _sha256(manifest_path),
        },
        "cache_identity": {
            "installed_path": str(installed_path),
            "installed_path_sha256": hashlib.sha256(
                os.path.normcase(str(installed_path)).encode("utf-8")
            ).hexdigest().upper(),
            "generated_cache_written_directly": False,
        },
        "hook_trust": hook_trust,
        "native_runtime": {
            "receipt_sha256": runtime.get("receipt_sha256"),
            "runtime_identity": runtime.get("runtime_identity"),
            "tool_catalog_sha256": runtime.get("tool_catalog_sha256"),
            "catalog": dict(EXPECTED_CATALOG),
            "task_reopened": False,
        },
        "fresh_app_server_hook_read": True,
        "host_restart_requested": False,
        "task_reopened": False,
    }
    proof["receipt_sha256"] = hashlib.sha256(
        _json_bytes(proof)
    ).hexdigest().upper()
    return proof


def _seal_install_correction_generation(
    *,
    recovery_receipt_path: Path,
    recovery_receipt_sha256: str,
    base_installation_path: Path,
    base_installation_sha256: str,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    hook_cwd: Path,
) -> dict[str, Any]:
    """Seal one corrected installation generation and atomically repoint CURRENT."""

    exact_data_root = data_root.resolve()
    installation_root = exact_data_root / "installations" / "codex-v200"
    current_path = installation_root / "CURRENT_INSTALLATION.json"
    if not executable.resolve().is_file() or not current_path.is_file():
        raise InstallationError(
            "Install correction requires the exact Codex executable and CURRENT receipt."
        )
    authority = _load_install_correction_authority(
        recovery_receipt_path=recovery_receipt_path,
        recovery_receipt_sha256=recovery_receipt_sha256,
        base_installation_path=base_installation_path,
        base_installation_sha256=base_installation_sha256,
        data_root=exact_data_root,
    )
    current_before, _, current_before_sha256 = _load_self_sealed_json(
        path=current_path,
        expected_file_sha256=_sha256(current_path),
        authority_root=installation_root,
        label="current installation before correction",
    )
    if (
        current_before.get("schema") != INSTALL_SCHEMA
        or current_before.get("status") != "PASS"
    ):
        raise InstallationError(
            "The current installation before correction is not a sealed generation."
        )
    existing_generation = dict(current_before.get("correction_generation") or {})
    if existing_generation.get("recovery_receipt_sha256") == authority[
        "recovery_file_sha256"
    ]:
        if (
            existing_generation.get("schema") != INSTALL_CORRECTION_GENERATION_SCHEMA
            or existing_generation.get("status") != "PASS"
            or existing_generation.get("current_installation_pointer_committed")
            is not True
        ):
            raise InstallationError("The existing correction generation drifted.")
        return {
            "schema": INSTALL_CORRECTION_GENERATION_SCHEMA,
            "status": "PASS",
            "generation_id": existing_generation.get("generation_id"),
            "correction_installation_path": str(current_path),
            "correction_installation_file_sha256": current_before_sha256,
            "current_installation_path": str(current_path),
            "current_installation_file_sha256": current_before_sha256,
            "current_installation_points_to_correction_generation": True,
            "replayed": True,
        }

    host_proof = _probe_install_correction_host_state(
        authority=authority,
        executable=executable,
        codex_home=codex_home,
        data_root=exact_data_root,
        hook_cwd=hook_cwd,
    )
    bindings_before = _task_binding_inventory(exact_data_root)
    generation_material = {
        "recovery_receipt_sha256": authority["recovery_file_sha256"],
        "base_installation_sha256": authority["base_file_sha256"],
        "current_installation_before_sha256": current_before_sha256,
        "config_after_sha256": authority["config_lineage"]["after_sha256"],
        "host_proof_sha256": host_proof["receipt_sha256"],
        "task_binding_inventory_sha256": bindings_before["inventory_sha256"],
    }
    generation_id = "install_correction_" + hashlib.sha256(
        _json_bytes(generation_material)
    ).hexdigest()[:40].lower()
    generation_root = installation_root / "correction-generations"
    generation_path = generation_root / f"INSTALL_CORRECTION_{generation_id}.json"
    intent_path = generation_root / f"INTENT_{generation_id}.json"
    pointer_receipt_path = generation_root / f"POINTER_{generation_id}.json"
    history_path = (
        installation_root
        / "correction-history"
        / f"CURRENT_INSTALLATION.before-{generation_id}-{current_before_sha256}.json"
    )
    intent = {
        "schema": "evidence-lane.codex-install-correction-intent.v1",
        "status": "PREPARED",
        "generation_id": generation_id,
        **generation_material,
        "current_installation_path_sha256": hashlib.sha256(
            os.path.normcase(str(current_path)).encode("utf-8")
        ).hexdigest().upper(),
        "pointer_write_atomic": True,
        "task_binding_registry_mutation_allowed": False,
        "task_reopened": False,
    }
    intent = _write_self_sealed_json(intent_path, intent)
    if history_path.exists():
        if _sha256(history_path) != current_before_sha256:
            raise InstallationError("The correction history snapshot drifted.")
    else:
        _write_atomic(history_path, current_path.read_bytes())

    corrected = deepcopy(authority["base"])
    corrected.pop("receipt_sha256", None)
    corrected_activation = dict(corrected.get("activation") or {})
    selected_plugin = host_proof["plugin_identity"]
    base_plugin_add = dict(corrected_activation.get("plugin_add") or {})
    base_plugin_add["pluginId"] = authority["selected_selector"]
    base_plugin_add["version"] = selected_plugin["version"]
    base_plugin_add["installedPath"] = host_proof["cache_identity"][
        "installed_path"
    ]
    corrected_activation["plugin_add"] = base_plugin_add
    corrected_activation["hook_trust"] = host_proof["hook_trust"]
    corrected_activation["runtime_prewarm"] = host_proof["native_runtime"]
    corrected_activation["state"] = (
        "CORRECTION_GENERATION_RESTART_OR_RELOAD_REQUIRED"
    )
    corrected_activation["runtime_ready_before_task_reopen"] = False
    corrected["activation"] = corrected_activation
    corrected["restart_required"] = True
    corrected["runtime_ready_before_task_reopen"] = False
    corrected["correction_generation"] = {
        "schema": INSTALL_CORRECTION_GENERATION_SCHEMA,
        "status": "PASS",
        "generation_id": generation_id,
        "recovery_receipt_sha256": authority["recovery_file_sha256"],
        "base_installation_sha256": authority["base_file_sha256"],
        "current_installation_before_sha256": current_before_sha256,
        "config_lineage": authority["config_lineage"],
        "selectors": {
            "failed_candidate": authority["candidate_selector"],
            "corrected_selected": authority["selected_selector"],
            "candidate_disabled": True,
            "sole_enabled_evidence_lane_selector": True,
        },
        "plugin_identity": host_proof["plugin_identity"],
        "cache_identity": host_proof["cache_identity"],
        "hook_trust_receipt_sha256": host_proof["hook_trust"][
            "receipt_sha256"
        ],
        "native_runtime": host_proof["native_runtime"],
        "host_proof_sha256": host_proof["receipt_sha256"],
        "task_bindings_before": bindings_before,
        "task_bindings_after": {
            **bindings_before,
            "registry_bytes_mutated": False,
            "exact_task_refresh_required_before_task_reopen": True,
        },
        "task_binding_registry_bytes_mutated": False,
        "goal_recovery_manager_must_refresh_exact_calling_task": True,
        "installer_helper_is_separate": True,
        "tunnel_invoked": False,
        "current_installation_pointer_target": generation_path.name,
        "current_installation_pointer_committed": True,
        "pointer_write_atomic": True,
        "intent_receipt_sha256": _sha256(intent_path),
        "history_snapshot_sha256": _sha256(history_path),
        "runtime_ready_before_task_reopen": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    corrected["candidate_created_or_accepted"] = False
    corrected["pointer_moved"] = False
    corrected["hil_inferred"] = False
    corrected["receipt_sha256"] = hashlib.sha256(
        _json_bytes(corrected)
    ).hexdigest().upper()
    corrected_bytes = _json_bytes(corrected)
    if generation_path.exists():
        if generation_path.read_bytes() != corrected_bytes:
            raise InstallationError("The correction generation file drifted.")
    else:
        _write_atomic(generation_path, corrected_bytes)
    _write_atomic(current_path, corrected_bytes)
    generation_file_sha256 = _sha256(generation_path)
    current_after_sha256 = _sha256(current_path)
    if current_after_sha256 != generation_file_sha256:
        raise InstallationError(
            "CURRENT_INSTALLATION does not point to the correction generation."
        )
    bindings_after = _task_binding_inventory(exact_data_root)
    if bindings_after != bindings_before:
        raise InstallationError(
            "The install correction unexpectedly mutated task-binding registry bytes."
        )
    pointer_receipt = _write_self_sealed_json(
        pointer_receipt_path,
        {
            "schema": "evidence-lane.codex-install-correction-pointer.v1",
            "status": "PASS",
            "generation_id": generation_id,
            "current_installation_before_sha256": current_before_sha256,
            "correction_installation_file_sha256": generation_file_sha256,
            "current_installation_after_sha256": current_after_sha256,
            "current_equals_correction_generation": True,
            "task_bindings_before_sha256": bindings_before[
                "inventory_sha256"
            ],
            "task_bindings_after_sha256": bindings_after["inventory_sha256"],
            "task_binding_registry_bytes_mutated": False,
            "exact_task_refresh_required_before_task_reopen": True,
            "runtime_ready_before_task_reopen": False,
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )
    return {
        "schema": INSTALL_CORRECTION_GENERATION_SCHEMA,
        "status": "PASS",
        "generation_id": generation_id,
        "correction_installation_path": str(generation_path),
        "correction_installation_file_sha256": generation_file_sha256,
        "current_installation_path": str(current_path),
        "current_installation_file_sha256": current_after_sha256,
        "current_installation_points_to_correction_generation": True,
        "pointer_receipt_path": str(pointer_receipt_path),
        "pointer_receipt_file_sha256": _sha256(pointer_receipt_path),
        "pointer_receipt_sha256": pointer_receipt["receipt_sha256"],
        "task_binding_inventory_sha256": bindings_after["inventory_sha256"],
        "task_binding_registry_bytes_mutated": False,
        "exact_task_refresh_required_before_task_reopen": True,
        "runtime_ready_before_task_reopen": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "replayed": False,
    }


def _ordered_json_sha256(value: Any) -> str:
    """Match the v1 two-slot registry's preserved-property-order seal."""

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()


def _fallback_selector_class(selector: str) -> str:
    """Classify a fallback selector as a locator, never release authority."""

    if selector == GENERATION_NEUTRAL_FALLBACK_SELECTOR:
        return "GENERATION_NEUTRAL_LOCATOR"
    if LEGACY_FALLBACK_SELECTOR_RE.fullmatch(selector):
        return "LEGACY_GENERATION_ALIAS_LOCATOR"
    raise InstallationError(
        "The fallback selector is neither the generation-neutral locator nor a "
        "recognized legacy generation alias."
    )


def _fallback_release_authority(
    *,
    registry: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Derive fallback authority without using selector spelling or paths.

    Historical registries may omit one or more manifest fields.  Those fields
    remain explicit empty values in the authority payload so their evidence
    completeness is visible; current governed registries carry all of them.
    """

    selector = str(fallback.get("plugin_selector") or "")
    selector_class = _fallback_selector_class(selector)
    accepted_pv = str(registry.get("accepted_pv") or "")
    accepted_generation = registry.get("accepted_generation")
    fallback_pv = str(fallback.get("accepted_pv") or "")
    fallback_generation = fallback.get("accepted_generation")
    accepted_package = str(registry.get("accepted_package_sha256") or "").upper()
    package = str(fallback.get("package_sha256") or "").upper()
    accepted_plugin_version = str(
        registry.get("accepted_plugin_version") or fallback.get("plugin_version") or ""
    )
    plugin_version = str(fallback.get("plugin_version") or "")
    if (
        re.fullmatch(r"PV[1-9][0-9]*", accepted_pv) is None
        or not isinstance(accepted_generation, int)
        or accepted_generation != int(accepted_pv[2:])
        or fallback_pv != accepted_pv
        or fallback_generation != accepted_generation
        or re.fullmatch(r"[A-F0-9]{64}", accepted_package) is None
        or package != accepted_package
        or not plugin_version
        or accepted_plugin_version != plugin_version
        or fallback.get("slot_role") != "fallback"
        or fallback.get("byte_frozen") is not True
        or fallback.get("enabled") is not False
        or fallback.get("native_mcp_enabled") is not False
    ):
        raise InstallationError(
            "The fallback release authority does not match the sealed PV/package boundary."
        )

    optional_hashes = {
        "accepted_manifest_sha256": str(
            registry.get("accepted_manifest_sha256") or ""
        ).upper(),
        "accepted_universal_pv_package_sha256": str(
            registry.get("accepted_universal_pv_package_sha256") or ""
        ).upper(),
        "cache_authority_manifest_sha256": str(
            fallback.get("cache_authority_manifest_sha256") or ""
        ).upper(),
        "install_receipt_sha256": str(
            fallback.get("install_receipt_sha256") or ""
        ).upper(),
        "plugin_manifest_sha256": str(
            fallback.get("plugin_manifest_sha256") or ""
        ).upper(),
    }
    if any(
        value and re.fullmatch(r"[A-F0-9]{64}", value) is None
        for value in optional_hashes.values()
    ):
        raise InstallationError("A sealed fallback authority hash is malformed.")

    # Alphabetical insertion order intentionally matches the PowerShell helper's
    # compact JSON authority seal.
    authority_payload = {
        "accepted_generation": accepted_generation,
        "accepted_manifest_sha256": optional_hashes[
            "accepted_manifest_sha256"
        ],
        "accepted_package_sha256": accepted_package,
        "accepted_plugin_version": accepted_plugin_version,
        "accepted_pv": accepted_pv,
        "accepted_universal_pv_package_sha256": optional_hashes[
            "accepted_universal_pv_package_sha256"
        ],
        "byte_frozen": True,
        "cache_authority_manifest_sha256": optional_hashes[
            "cache_authority_manifest_sha256"
        ],
        "enabled": False,
        "install_receipt_sha256": optional_hashes["install_receipt_sha256"],
        "native_mcp_enabled": False,
        "package_sha256": package,
        "plugin_manifest_sha256": optional_hashes["plugin_manifest_sha256"],
        "plugin_version": plugin_version,
        "schema": "evidence-lane.fallback-release-authority.v1",
        "slot_role": "fallback",
    }
    authority_sha256 = hashlib.sha256(
        json.dumps(
            authority_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()
    return {
        **authority_payload,
        "authority_id": "fallback_" + authority_sha256[:40].lower(),
        "fallback_authority_sha256": authority_sha256,
        "manifest_evidence_complete": all(optional_hashes.values()),
        "selector": selector,
        "selector_class": selector_class,
        "selector_is_authority": False,
    }


def _load_two_slot_update_authority(
    *,
    data_root: Path,
    comparison_baseline: dict[str, Any] | None,
) -> dict[str, Any] | None:
    registry_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / "CODEX_TWO_SLOT_REGISTRY.json"
    )
    if not registry_path.is_file():
        return None
    if comparison_baseline is None:
        raise InstallationError(
            "The materialized two-slot registry requires the exact prior stable baseline."
        )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict):
        raise InstallationError("The two-slot registry is not a JSON object.")
    without_seal = {key: value for key, value in registry.items() if key != "seal"}
    without_internal_hashes = {
        key: value
        for key, value in registry.items()
        if key not in {"registry_body_sha256", "seal"}
    }
    seal = dict(registry.get("seal") or {})
    slots = dict(registry.get("slots") or {})
    stable = dict(slots.get("stable-build") or {})
    fallback = dict(slots.get("fallback") or {})
    fallback_authority = _fallback_release_authority(
        registry=registry,
        fallback=fallback,
    )
    baseline_path = Path(str(comparison_baseline.get("installation_receipt") or ""))
    stable_install_path = Path(str(stable.get("install_receipt") or ""))
    fallback_install_path = Path(str(fallback.get("install_receipt") or ""))
    fallback_install_sha256 = str(fallback.get("install_receipt_sha256") or "")
    if (
        registry.get("schema") != "evidence-lane.codex-two-slot-registry.v1"
        or registry.get("status") != "PASS"
        or registry.get("state") != "STABLE_ACTIVE_FALLBACK_PREWARMED_DISABLED"
        or registry.get("post_fuse_materialized") is not True
        or registry.get("accepted_pv") != "PV12"
        or registry.get("accepted_generation") != 12
        or registry.get("exact_live_slot_count") != 2
        or registry.get("max_enabled_plugin_count") != 1
        or set(slots) != {"stable-build", "fallback"}
        or seal.get("algorithm") != "SHA256"
        or seal.get("body_sha256") != _ordered_json_sha256(without_seal)
        or registry.get("registry_body_sha256")
        != _ordered_json_sha256(without_internal_hashes)
        or stable.get("slot_role") != "stable-build"
        or stable.get("byte_frozen") is not False
        or stable.get("enabled") is not True
        or stable.get("native_mcp_enabled") is not True
        or not stable_install_path.is_file()
        or stable_install_path.resolve() != baseline_path.resolve()
        or stable.get("install_receipt_sha256")
        != comparison_baseline.get("installation_receipt_sha256")
        or _sha256(stable_install_path)
        != comparison_baseline.get("installation_receipt_sha256")
        or fallback.get("slot_role") != "fallback"
        or fallback.get("byte_frozen") is not True
        or fallback.get("enabled") is not False
        or fallback.get("native_mcp_enabled") is not False
        or fallback.get("accepted_pv") != "PV12"
        or fallback.get("accepted_generation") != 12
        or fallback.get("package_sha256")
        != registry.get("accepted_package_sha256")
        or not fallback_install_path.is_file()
        or re.fullmatch(r"[A-F0-9]{64}", fallback_install_sha256) is None
        or _sha256(fallback_install_path) != fallback_install_sha256
    ):
        raise InstallationError(
            "The exact stable/fallback registry is not eligible for stable advancement."
        )
    return {
        "path": registry_path,
        "before_file_sha256": _sha256(registry_path),
        "registry": registry,
        "prior_stable_selector": stable.get("plugin_selector"),
        "fallback_authority": fallback_authority,
        "fallback_snapshot_sha256": hashlib.sha256(
            _json_bytes(fallback)
        ).hexdigest().upper(),
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
    fallback = dict(slots["fallback"])
    fallback_selector = str(fallback.get("plugin_selector") or "")
    fallback_authority = _fallback_release_authority(
        registry=registry,
        fallback=fallback,
    )
    if fallback_authority["fallback_authority_sha256"] != dict(
        authority.get("fallback_authority") or {}
    ).get("fallback_authority_sha256"):
        raise InstallationError(
            "The sealed fallback release authority changed during stable activation."
        )
    prior_stable_selector = str(authority.get("prior_stable_selector") or "")
    canonical_migration = (
        prior_stable_selector != plugin_selector
        and plugin_selector == PLUGIN_SELECTOR
        and prior_stable_selector.startswith(f"{PLUGIN_NAME}@evidence-lane-v200-")
    )
    by_selector = {
        str(row.get("pluginId") or ""): row for row in evidence_plugins
    }
    if (
        (plugin_selector != prior_stable_selector and not canonical_migration)
        or set(by_selector) != {plugin_selector, fallback_selector}
        or len(enabled) != 1
        or enabled[0].get("pluginId") != plugin_selector
        or plugin_selector not in by_selector
        or fallback_selector not in by_selector
        or by_selector[fallback_selector].get("enabled") is not False
        or any(
            row.get("enabled") is not False
            for row in evidence_plugins
            if row.get("pluginId") != plugin_selector
        )
    ):
        raise InstallationError(
            "Codex does not expose one stable enabled and one fallback disabled."
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

    stable = dict(slots["stable-build"])
    stable.update(
        {
            "byte_frozen": False,
            "cache_authority_manifest_sha256": source_manifest_sha256,
            "cache_root": str(installed_path),
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
            "slot_role": "stable-build",
        }
    )
    slots["stable-build"] = stable
    slots["fallback"] = fallback
    registry["slots"] = slots
    registry["fallback_release_authority"] = fallback_authority
    registry["active_slot"] = "stable-build"
    registry["state"] = "STABLE_ACTIVE_FALLBACK_PREWARMED_DISABLED"
    registry["live_registered_selectors"] = sorted(by_selector)
    registry["exact_registered_plugin_count"] = 2
    registry["activation_proof"] = {
        "command": "codex plugin list --json",
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path),
        "fallback_native_mcp_enabled": False,
        "fallback_plugin_enabled": False,
        "plugin_list_json_utf8_sha256": hashlib.sha256(
            _json_bytes(plugin_list)
        ).hexdigest().upper(),
        "plugin_list_json_canonicalized": True,
        "stable_native_mcp_enabled": True,
        "stable_plugin_enabled": True,
    }
    fallback_snapshot_sha256 = hashlib.sha256(
        _json_bytes(fallback)
    ).hexdigest().upper()
    if fallback_snapshot_sha256 != authority["fallback_snapshot_sha256"]:
        raise InstallationError("The immutable fallback registry object changed.")
    registry["registry_body_sha256"] = _ordered_json_sha256(
        {
            key: value
            for key, value in registry.items()
            if key not in {"registry_body_sha256", "seal"}
        }
    )
    seal = dict(registry.get("seal") or {})
    seal["body_sha256"] = _ordered_json_sha256(
        {key: value for key, value in registry.items() if key != "seal"}
    )
    registry["seal"] = seal
    registry_path = Path(authority["path"])
    _write_atomic(
        registry_path,
        (
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    )
    after_file_sha256 = _sha256(registry_path)
    update = {
        "schema": "evidence-lane.codex-two-slot-stable-update.v1",
        "status": "PASS",
        "before_registry_file_sha256": authority["before_file_sha256"],
        "after_registry_file_sha256": after_file_sha256,
        "registry_body_sha256": registry["registry_body_sha256"],
        "registry_seal_body_sha256": seal["body_sha256"],
        "prior_stable_selector": authority["prior_stable_selector"],
        "current_stable_selector": plugin_selector,
        "stable_selector_reused": (
            authority["prior_stable_selector"] == plugin_selector
        ),
        "stable_selector_migrated_to_canonical_git": canonical_migration,
        "same_stable_selector_required_after_migration": True,
        "legacy_stable_removed_after_new_route_proof": canonical_migration,
        "new_stable_selector_created": False,
        "current_stable_install_receipt_sha256": _sha256(install_receipt),
        "fallback_selector": fallback_selector,
        "fallback_selector_class": fallback_authority["selector_class"],
        "fallback_selector_is_authority": False,
        "fallback_authority_id": fallback_authority["authority_id"],
        "fallback_authority_sha256": fallback_authority[
            "fallback_authority_sha256"
        ],
        "fallback_manifest_evidence_complete": fallback_authority[
            "manifest_evidence_complete"
        ],
        "fallback_snapshot_sha256": fallback_snapshot_sha256,
        "fallback_enabled": False,
        "fallback_byte_frozen": True,
        "enabled_plugin_count": 1,
        "registered_plugin_count": 2,
        "active_tunnel_count": 0,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_invoked": False,
    }
    update["receipt_sha256"] = hashlib.sha256(
        _json_bytes(update)
    ).hexdigest().upper()
    update_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / f"STABLE_REGISTRY_UPDATE_{archive_sha256[:16]}.json"
    )
    _write_atomic(update_path, _json_bytes(update))
    return {
        **update,
        "registry_path": str(registry_path),
        "receipt_path": str(update_path),
        "receipt_file_sha256": _sha256(update_path),
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
    activate_local_test = bool(getattr(args, "activate_local_test", False))
    disabled_hook_recovery_commit = getattr(
        args, "recover_disabled_local_hooks_from", None
    )
    disabled_hook_recovery_commit_sha256 = getattr(
        args, "recover_disabled_local_hooks_from_sha256", None
    )
    if (disabled_hook_recovery_commit is None) != (
        disabled_hook_recovery_commit_sha256 is None
    ):
        raise InstallationError(
            "Disabled-local hook recovery requires the prior commit receipt and "
            "its exact file SHA-256."
        )
    disabled_local_hook_recovery = disabled_hook_recovery_commit is not None
    activation_requested = bool(args.activate) or activate_local_test
    if bool(args.activate) and activate_local_test:
        raise InstallationError(
            "Git-stable activation and local-test activation are mutually exclusive."
        )
    trust_sealed_hooks = bool(getattr(args, "trust_sealed_hooks", False))
    defer_hook_trust = bool(getattr(args, "defer_hook_trust_to_user", False))
    if bool(args.activate) != trust_sealed_hooks:
        raise InstallationError(
            "Activation and explicit sealed-hook trust must be requested together."
        )
    if activate_local_test:
        if disabled_local_hook_recovery:
            if trust_sealed_hooks or defer_hook_trust:
                raise InstallationError(
                    "Disabled-local hook recovery owns one exact supported hook-trust "
                    "write and cannot mix another trust mode."
                )
            if (
                str(getattr(args, "confirm_local_test_rotation", ""))
                != LOCAL_TEST_DISABLED_HOOK_RECOVERY_CONFIRMATION
            ):
                raise InstallationError(
                    "Disabled-local hook recovery requires its exact confirmation token."
                )
        else:
            if trust_sealed_hooks or not defer_hook_trust:
                raise InstallationError(
                    "Local-test activation must defer the eight hook trust decisions "
                    "to the visible Codex UI."
                )
            if (
                str(getattr(args, "confirm_local_test_rotation", ""))
                != "EXPLICIT_ONE_TIME_LOCAL_TEST_ROTATION"
            ):
                raise InstallationError(
                    "Local-test activation requires the exact one-time rotation token."
                )
    elif disabled_local_hook_recovery:
        raise InstallationError(
            "Disabled-local hook recovery is valid only with --activate-local-test."
        )
    elif defer_hook_trust:
        raise InstallationError(
            "Deferred hook trust is allowed only for explicit local-test activation."
        )
    if _inside(archive, codex_home / "plugins" / "cache"):
        raise InstallationError("A generated cache package cannot be installation input.")
    rehearsal = _load_receipt(
        receipt_path,
        archive,
        activation=bool(args.activate),
        local_test_activation=activate_local_test,
    )
    release_authority_path = getattr(args, "release_authority_receipt", None)
    release_authority_file_sha256 = getattr(
        args, "release_authority_receipt_sha256", None
    )
    if (release_authority_path is None) != (
        release_authority_file_sha256 is None
    ):
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
    baseline_sha256 = getattr(
        args, "baseline_installation_receipt_sha256", None
    )
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
    # stable/fallback two-slot authority.  That authority is promotion state
    # and remains mandatory for an actual governed activation.
    two_slot_authority = (
        _load_two_slot_update_authority(
            data_root=data_root,
            comparison_baseline=comparison_baseline,
        )
        if args.activate
        else None
    )
    if requested_marketplace_name == LOCAL_TESTING_MARKETPLACE_NAME:
        if args.activate:
            raise InstallationError(
                "The local testing selector cannot use the Git-stable activation route."
            )
        if activate_local_test is False and defer_hook_trust:
            raise InstallationError("A staged local package cannot defer hook trust.")
        marketplace_name = LOCAL_TESTING_MARKETPLACE_NAME
    else:
        if activate_local_test:
            raise InstallationError(
                "Explicit local-test activation is fixed to the versioned testing selector."
            )
        marketplace_name = _resolve_stable_marketplace_name(
            requested_name=requested_marketplace_name,
            two_slot_authority=two_slot_authority,
        )
    plugin_selector = f"{PLUGIN_NAME}@{marketplace_name}"
    marketplace_root = codex_home / "local-marketplaces" / marketplace_name
    extracted_inventory: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="evidence-lane-v200-install-") as raw:
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
    if activate_local_test:
        executable = args.codex_executable.resolve()
        if not executable.is_file():
            raise InstallationError("The exact Codex executable is unavailable.")
        local_test_reinstall = _prepare_local_test_reinstall(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            plugin_selector=plugin_selector,
            marketplace_name=marketplace_name,
            marketplace_root=marketplace_root,
            disabled_hook_recovery_commit=(
                Path(disabled_hook_recovery_commit)
                if disabled_local_hook_recovery
                else None
            ),
            disabled_hook_recovery_commit_sha256=(
                str(disabled_hook_recovery_commit_sha256)
                if disabled_local_hook_recovery
                else None
            ),
        )
        marketplace_add = (
            _run_codex(
                executable,
                codex_home,
                ["plugin", "marketplace", "add", str(marketplace_root), "--json"],
            )
            if local_test_reinstall["marketplace_add_required"]
            else {
                "status": "REUSED_EXACT_LOCAL_MARKETPLACE",
                "name": marketplace_name,
                "root": str(marketplace_root.resolve()),
            }
        )
        plugin_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "add", plugin_selector, "--json"],
        )
        installed_path = Path(str(plugin_add.get("installedPath") or "")).resolve()
        expected_cache = codex_home / "plugins" / "cache" / marketplace_name / PLUGIN_NAME
        if (
            plugin_add.get("pluginId") != plugin_selector
            or plugin_add.get("version") != identity["version"]
            or not _inside(installed_path, expected_cache)
        ):
            raise InstallationError("Codex installed a mismatched local-test cache identity.")
        runtime_prewarm = _prewarm_installed_runtime(
            installed_path,
            data_root=data_root,
        )
        hook_event_isolation = _initialize_installed_hook_event_isolation(
            installed_path,
            data_root=data_root,
            installation_id=str(local_test_reinstall["transaction_id"]),
        )
        hook_trust, config_receipt = _trust_sealed_plugin_hooks(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            hook_cwd=Path(getattr(args, "hook_cwd", Path.cwd())),
            plugin_selector=plugin_selector,
            defer_hook_trust_to_user=not disabled_local_hook_recovery,
            activation_mode=(
                "RECOVER_DISABLED_LOCAL"
                if disabled_local_hook_recovery
                else "STAGE_CANDIDATE_DISABLED"
            ),
            last_known_good_selector=local_test_reinstall[
                "last_known_good_selector"
            ],
            last_known_good_config_sha256=local_test_reinstall[
                "last_known_good_config_sha256"
            ],
            rollback_config_backup=local_test_reinstall[
                "rollback_config_backup"
            ],
            expected_commit_config_sha256=(
                _sha256(codex_home / "config.toml")
                if disabled_local_hook_recovery
                else None
            ),
        )
        plugin_list = _run_codex(
            executable,
            codex_home,
            ["plugin", "list", "--json"],
        )
        installed_rows = plugin_list.get("installed")
        if not isinstance(installed_rows, list):
            raise InstallationError("Codex plugin list did not expose installed plugins.")
        evidence_rows = [
            dict(row)
            for row in installed_rows
            if isinstance(row, dict)
            and str(row.get("pluginId") or "").startswith("evidence-lane-plugin@")
        ]
        exact_installed = [
            row for row in evidence_rows if row.get("pluginId") == plugin_selector
        ]
        enabled_evidence = [row for row in evidence_rows if row.get("enabled") is True]
        if disabled_local_hook_recovery:
            if (
                len(exact_installed) != 1
                or exact_installed[0].get("enabled") is not True
                or len(enabled_evidence) != 1
                or enabled_evidence[0].get("pluginId") != plugin_selector
            ):
                raise InstallationError(
                    "Disabled-local hook recovery did not enable only the local "
                    "2.2 selector."
                )
        elif (
            len(exact_installed) != 1
            or exact_installed[0].get("enabled") is not False
            or len(enabled_evidence) != 1
            or enabled_evidence[0].get("pluginId")
            != local_test_reinstall["last_known_good_selector"]
        ):
            raise InstallationError(
                "The candidate was not staged disabled beside the one enabled "
                "last-known-good Evidence Lane selector."
            )
        readiness = _local_test_runtime_readiness(
            installed=True,
            restart_or_reload_completed=False,
            hooks_trusted=disabled_local_hook_recovery,
            exact_identity_verified=True,
            exact_catalog_verified=(
                runtime_prewarm.get("status") == "PASS"
                and runtime_prewarm.get("tool_count") == EXPECTED_CATALOG["tools"]
            ),
            prompt_capture_verified=False,
            smoke_probes_passed=False,
            active=False,
        )
        if disabled_local_hook_recovery:
            activation = {
                "state": "LOCAL_2_2_HOOK_RECOVERY_SWITCHED_RESTART_REQUIRED",
                "plugin_add_invoked": True,
                "marketplace_add": marketplace_add,
                "plugin_add": plugin_add,
                "local_test_reinstall": local_test_reinstall,
                "exclusive_channel": {
                    "status": "LOCAL_SELECTOR_SWITCHED_ONCE",
                    "enabled_selector": plugin_selector,
                    "enabled_evidence_lane_count": 1,
                    "candidate_selector": plugin_selector,
                    "candidate_enabled": True,
                    "stable_and_fallback_enabled": False,
                    "switch_count": 1,
                    "accepted_two_slot_registry_mutated": False,
                    "config_receipt": config_receipt,
                },
                "hook_trust": hook_trust,
                "hook_event_isolation": hook_event_isolation,
                "runtime_prewarm": runtime_prewarm,
                "local_test_marketplace_source": {
                    "status": "PASS",
                    "source_type": "local",
                    "marketplace_name": marketplace_name,
                    "marketplace_root_sha256": _sha256(
                        marketplace_root / ".agents" / "plugins" / "marketplace.json"
                    ),
                },
                "transaction": {
                    "schema": LOCAL_TEST_DISABLED_HOOK_RECOVERY_SCHEMA,
                    "transaction_id": local_test_reinstall["transaction_id"],
                    "state": "LOCAL_SELECTOR_SWITCHED_ONCE",
                    "prior_commit_authority": local_test_reinstall[
                        "prior_commit_authority"
                    ],
                    "candidate_selector": plugin_selector,
                    "candidate_enabled": True,
                    "stable_and_fallback_enabled": False,
                    "switch_count": 1,
                    "compare_and_swap": True,
                    "rollback_capable": True,
                    "rollback_state": "ALL_EVIDENCE_LANE_SELECTORS_DISABLED",
                    "rollback_config_backup": local_test_reinstall[
                        "rollback_config_backup"
                    ],
                    "rollback_config_backup_sha256": local_test_reinstall[
                        "rollback_config_backup_sha256"
                    ],
                    "hook_event_isolation_verified_before_activation": True,
                    "exact_eight_hook_hashes_trusted": True,
                    "restart_or_reload_required": True,
                    "candidate_created_or_accepted": False,
                    "pointer_moved": False,
                    "hil_inferred": False,
                },
                "readiness": readiness,
                "runtime_ready_before_task_reopen": False,
                "prompt_capture_runnable_after_restart": True,
                "hot_reload_claimed": False,
            }
        else:
            activation = {
            "state": "CANDIDATE_STAGED_DISABLED_RESTART_TRUST_REQUIRED",
            "plugin_add_invoked": True,
            "marketplace_add": marketplace_add,
            "plugin_add": plugin_add,
            "local_test_reinstall": local_test_reinstall,
            "exclusive_channel": {
                "status": "CANDIDATE_NOT_SWITCHED",
                "enabled_selector": local_test_reinstall[
                    "last_known_good_selector"
                ],
                "enabled_evidence_lane_count": 1,
                "candidate_selector": plugin_selector,
                "candidate_enabled": False,
                "switch_count": 0,
                "accepted_two_slot_registry_mutated": False,
                "config_receipt": config_receipt,
            },
            "hook_trust": hook_trust,
            "hook_event_isolation": hook_event_isolation,
            "runtime_prewarm": runtime_prewarm,
            "local_test_marketplace_source": {
                "status": "PASS",
                "source_type": "local",
                "marketplace_name": marketplace_name,
                "marketplace_root_sha256": _sha256(
                    marketplace_root / ".agents" / "plugins" / "marketplace.json"
                ),
            },
            "transaction": {
                "schema": LOCAL_TEST_STAGE_SCHEMA,
                "transaction_id": local_test_reinstall["transaction_id"],
                "state": "CANDIDATE_STAGED_DISABLED",
                "last_known_good_selector": local_test_reinstall[
                    "last_known_good_selector"
                ],
                "last_known_good_config_sha256": local_test_reinstall[
                    "last_known_good_config_sha256"
                ],
                "candidate_selector": plugin_selector,
                "candidate_enabled": False,
                "switch_count": 0,
                "compare_and_swap": True,
                "rollback_capable": True,
                "rollback_config_backup": local_test_reinstall[
                    "rollback_config_backup"
                ],
                "rollback_config_backup_sha256": local_test_reinstall[
                    "rollback_config_backup_sha256"
                ],
                "commit_requires": [
                    "HUMAN_HOOK_TRUST",
                    "HOST_RESTART_OR_RELOAD",
                    "EXACT_PLUGIN_AND_CATALOG_IDENTITY",
                    "PROMPT_CAPTURE_PROOF",
                    "BOUNDED_SMOKE_PROBES",
                    "UNCHANGED_CAS_BASELINE",
                ],
            },
            "readiness": readiness,
            "runtime_ready_before_task_reopen": False,
            "prompt_capture_runnable_after_user_hook_trust": False,
            "hot_reload_claimed": False,
            }
    elif args.activate:
        executable = args.codex_executable.resolve()
        if not executable.is_file():
            raise InstallationError("The exact Codex executable is unavailable.")
        if two_slot_authority is None:
            raise InstallationError(
                "Activation requires the materialized stable/fallback two-slot authority."
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
        known = {row["name"]: Path(row["root"]).resolve() for row in listed["marketplaces"]}
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
            raise InstallationError("Codex did not configure one canonical Git marketplace.")
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
            raise InstallationError("Codex installed a mismatched plugin cache identity.")
        runtime_prewarm = _prewarm_installed_runtime(
            installed_path,
            data_root=data_root,
        )
        hook_event_isolation = _initialize_installed_hook_event_isolation(
            installed_path,
            data_root=data_root,
            installation_id=(
                "stable_install_" + str(_sha256(archive))[:40].lower()
            ),
        )
        hook_trust, config_receipt = _trust_sealed_plugin_hooks(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            hook_cwd=Path(getattr(args, "hook_cwd", Path.cwd())),
            plugin_selector=plugin_selector,
        )
        plugin_list = _run_codex(
            executable,
            codex_home,
            ["plugin", "list", "--json"],
        )
        installed_rows = plugin_list.get("installed")
        if not isinstance(installed_rows, list):
            raise InstallationError("Codex plugin list did not expose installed plugins.")
        exact_installed = [
            dict(row)
            for row in installed_rows
            if isinstance(row, dict) and row.get("pluginId") == plugin_selector
        ]
        if len(exact_installed) != 1:
            raise InstallationError("Codex did not expose one canonical installed plugin.")
        marketplace_source = dict(exact_installed[0].get("marketplaceSource") or {})
        if (
            marketplace_source.get("sourceType") != "git"
            or MARKETPLACE_SOURCE.lower()
            not in str(marketplace_source.get("source") or "").lower()
        ):
            raise InstallationError("The installed stable route is not Git-backed.")
        fallback_selector = str(
            (two_slot_authority["registry"]["slots"]["fallback"] or {}).get(
                "plugin_selector"
            )
            or ""
        )
        post_proof_cleanup, plugin_list = _cleanup_obsolete_after_new_route_proof(
            executable=executable,
            codex_home=codex_home,
            plugin_selector=plugin_selector,
            fallback_selector=fallback_selector,
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
        "activation_authority": (
            {
                "status": release_authority["status"],
                "boundary": release_authority["boundary"],
                "source_commit": release_authority["source"]["commit"],
                "source_tree": release_authority["source"]["tree"],
                "branch": release_authority["source"]["branch"],
                "github_repository": release_authority["github_ci"]["repository"],
                "vercel_preview_deployment_id": release_authority[
                    "vercel_preview"
                ]["deployment_id"],
                "vercel_preview_url": release_authority["vercel_preview"]["url"],
                "vercel_preview_ready": True,
                "production_deployment": False,
                "receipt_sha256": release_authority["receipt_sha256"],
                "receipt_file_sha256": str(
                    release_authority_file_sha256 or ""
                ).upper(),
            }
            if release_authority is not None
            else {
                "status": "PASS",
                "boundary": (
                    LOCAL_TEST_DISABLED_HOOK_RECOVERY_CONFIRMATION
                    if disabled_local_hook_recovery
                    else "EXPLICIT_ONE_TIME_LOCAL_TEST_ROTATION"
                ),
                "selector": plugin_selector,
                "package_receipt_sha256": _sha256(receipt_path),
                "git_write_invoked": False,
                "github_ci_required": False,
                "vercel_preview_required": False,
                "accepted_two_slot_registry_mutated": False,
                "hook_trust": (
                    "SEALED_EIGHT_HASHES_TRUSTED"
                    if disabled_local_hook_recovery
                    else "USER_TRUST_PENDING"
                ),
            }
            if activate_local_test
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
        "fallback_materialization_gate": (
            "POST_EXACT_PV12_APPROVE_AND_NATIVE_FUSE"
        ),
        "fallback_materialized": False,
        "live_cache_cleanup_deferred_until_exact_pv12_acceptance": True,
        "two_slot_operator_packaged": True,
        "previous_release_cache_deleted": False,
        "stable_selector_reused": bool(
            two_slot_authority is not None
            and two_slot_authority.get("prior_stable_selector") == plugin_selector
        ),
        "stable_selector_migrated_to_canonical_git": bool(
            two_slot_authority is not None
            and two_slot_authority.get("prior_stable_selector") != plugin_selector
        ),
        "post_proof_obsolete_cleanup_completed": bool(
            post_proof_cleanup and post_proof_cleanup.get("status") == "PASS"
        ),
        "obsolete_cleanup_used_supported_codex_apis": bool(args.activate),
        "stable_build_identity_location": "SEALED_INSTALL_RECEIPT_NOT_SELECTOR",
        "new_stable_selector_created": (
            activate_local_test and not disabled_local_hook_recovery
        ),
        "generated_cache_written_directly": False,
        "credential_requested_or_stored": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_required": activation_requested,
        "runtime_ready_before_task_reopen": bool(
            activation.get("runtime_ready_before_task_reopen") is True
        ),
    }
    body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    receipt_dir = data_root / "installations" / "codex-v200"
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
            "reason": (
                "LOCAL_TEST_ROTATION_PRESERVES_ACCEPTED_TWO_SLOT_AUTHORITY"
                if activate_local_test
                else "STAGING_PASS_ONLY"
            ),
        }
    )
    return {
        **body,
        "receipt_path": str(install_receipt),
        "two_slot_registry_update": two_slot_update,
    }


def _stage_loaded_local_successor(args: argparse.Namespace) -> dict[str, Any]:
    """Install and verify one inactive successor before the bound host stops.

    The active desktop may retain the primary selector's cache.  This route uses
    a fixed, reusable successor selector with its own cache, verifies the exact
    package/runtime/eight-hook surface while the desktop remains open, and keeps
    the successor disabled.  A separately sealed post-stop helper may then rotate
    those proved bytes into the primary and recovery slots before reopening the
    exact task.
    """

    if (
        args.confirm_local_successor_stage
        != "EXPLICIT_INSTALL_DISABLED_LOCAL_SUCCESSOR_BEFORE_RESTART"
    ):
        raise InstallationError(
            "Loaded-local successor staging requires its exact confirmation token."
        )
    if (
        args.archive is None
        or (args.package_receipt is None and args.rehearsal_receipt is None)
        or args.primary_installation_receipt is None
        or not args.primary_installation_receipt_sha256
        or args.codex_executable is None
    ):
        raise InstallationError(
            "Loaded-local successor staging requires the package, primary receipt "
            "and seal, and Codex executable."
        )
    if (
        args.activate
        or args.activate_local_test
        or args.trust_sealed_hooks
        or args.defer_hook_trust_to_user
        or args.recover_disabled_local_hooks_from is not None
        or args.materialize_local_recovery_copy
    ):
        raise InstallationError(
            "Loaded-local successor staging cannot be mixed with another activation "
            "or recovery operation."
        )

    archive = args.archive.resolve()
    package_receipt_path = Path(
        args.package_receipt or args.rehearsal_receipt
    ).resolve()
    codex_home = args.codex_home.resolve()
    data_root = args.data_root.resolve()
    executable = args.codex_executable.resolve()
    hook_cwd = Path(args.hook_cwd).resolve()
    if not executable.is_file() or not hook_cwd.is_dir():
        raise InstallationError(
            "The exact Codex executable or governed hook workspace is unavailable."
        )
    package = _load_receipt(
        package_receipt_path,
        archive,
        activation=False,
        local_test_activation=True,
    )
    primary_receipt_path = args.primary_installation_receipt.resolve()
    primary_receipt_sha256 = _sha256(primary_receipt_path)
    if (
        primary_receipt_sha256
        != str(args.primary_installation_receipt_sha256).upper()
        or not _inside(
            primary_receipt_path,
            data_root / "installations" / "codex-v200",
        )
    ):
        raise InstallationError(
            "The primary local installation receipt is outside authority or drifted."
        )
    primary_receipt = json.loads(primary_receipt_path.read_text(encoding="utf-8"))
    primary_activation = dict(primary_receipt.get("activation") or {})
    primary_plugin_add = dict(primary_activation.get("plugin_add") or {})
    primary_selector = str(primary_plugin_add.get("pluginId") or "")
    primary_cache = Path(str(primary_plugin_add.get("installedPath") or "")).resolve()
    if (
        primary_receipt.get("schema") != INSTALL_SCHEMA
        or primary_receipt.get("status") != "PASS"
        or primary_selector
        != f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"
        or not primary_cache.is_dir()
        or not _inside(
            primary_cache,
            codex_home
            / "plugins"
            / "cache"
            / LOCAL_TESTING_MARKETPLACE_NAME
            / PLUGIN_NAME,
        )
        or primary_receipt.get("candidate_created_or_accepted") is not False
        or primary_receipt.get("pointer_moved") is not False
        or primary_receipt.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "The supplied primary local installation is not successor-stage eligible."
        )

    two_slot_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / "CODEX_TWO_SLOT_REGISTRY.json"
    )
    two_slot_before = _sha256(two_slot_path) if two_slot_path.is_file() else None
    before_plugin_list = _run_codex(
        executable, codex_home, ["plugin", "list", "--json"]
    )
    before_rows = [
        dict(row)
        for row in before_plugin_list.get("installed") or []
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    primary_rows = [row for row in before_rows if row.get("pluginId") == primary_selector]
    successor_rows = [
        row for row in before_rows if row.get("pluginId") == LOCAL_SUCCESSOR_SELECTOR
    ]
    enabled_before = [row for row in before_rows if row.get("enabled") is True]
    if (
        len(primary_rows) != 1
        or len(successor_rows) > 1
        or any(row.get("enabled") is True for row in successor_rows)
        or any(
            row.get("pluginId") != primary_selector
            for row in enabled_before
        )
    ):
        raise InstallationError(
            "Successor staging requires one installed primary and no other active "
            "Evidence Lane selector."
        )
    if successor_rows:
        _run_codex(
            executable,
            codex_home,
            ["plugin", "remove", LOCAL_SUCCESSOR_SELECTOR, "--json"],
        )

    successor_root = (
        codex_home / "local-marketplaces" / LOCAL_SUCCESSOR_MARKETPLACE_NAME
    )
    with tempfile.TemporaryDirectory(prefix="evidence-lane-v220-successor-") as raw:
        extracted = Path(raw) / "plugin"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        identity = _validate_plugin(extracted)
        extracted_inventory = _source_inventory(extracted)
        stage = _stage_marketplace(
            extracted=extracted,
            marketplace_root=successor_root,
            data_root=data_root,
            identity=identity,
            archive_sha256=_sha256(archive),
            marketplace_name=LOCAL_SUCCESSOR_MARKETPLACE_NAME,
            comparison_surface=identity["surface_inventory"],
            comparison_baseline={
                "primary_installation_receipt_sha256": primary_receipt_sha256,
                "primary_selector": primary_selector,
                "purpose": "PRE_STOP_LOADED_LOCAL_SUCCESSOR",
            },
        )

    marketplaces = _run_codex(
        executable, codex_home, ["plugin", "marketplace", "list", "--json"]
    )
    exact_marketplaces = [
        dict(row)
        for row in marketplaces.get("marketplaces") or []
        if isinstance(row, dict)
        and row.get("name") == LOCAL_SUCCESSOR_MARKETPLACE_NAME
    ]
    if len(exact_marketplaces) > 1:
        raise InstallationError("Codex exposed duplicate local successor marketplaces.")
    if exact_marketplaces:
        if (
            Path(str(exact_marketplaces[0].get("root") or "")).resolve()
            != successor_root.resolve()
        ):
            raise InstallationError(
                "The configured successor marketplace root does not match staging."
            )
        marketplace_add: dict[str, Any] = {
            "status": "REUSED_EXACT_LOCAL_MARKETPLACE",
            "name": LOCAL_SUCCESSOR_MARKETPLACE_NAME,
            "root": str(successor_root.resolve()),
        }
    else:
        marketplace_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "add", str(successor_root), "--json"],
        )

    try:
        plugin_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "add", LOCAL_SUCCESSOR_SELECTOR, "--json"],
        )
        successor_cache = Path(str(plugin_add.get("installedPath") or "")).resolve()
        expected_cache = (
            codex_home
            / "plugins"
            / "cache"
            / LOCAL_SUCCESSOR_MARKETPLACE_NAME
            / PLUGIN_NAME
        )
        if (
            plugin_add.get("pluginId") != LOCAL_SUCCESSOR_SELECTOR
            or plugin_add.get("version") != identity["version"]
            or not successor_cache.is_dir()
            or not _inside(successor_cache, expected_cache)
        ):
            raise InstallationError(
                "Codex installed a mismatched local successor cache identity."
            )

        config_path = codex_home / "config.toml"
        before_config_sha256 = _sha256(config_path)
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        plugins = json.loads(json.dumps(dict(parsed.get("plugins") or {})))
        if primary_selector not in plugins or LOCAL_SUCCESSOR_SELECTOR not in plugins:
            raise InstallationError(
                "The primary or successor selector is absent from Codex config."
            )
        for selector, raw_settings in list(plugins.items()):
            if not selector.startswith(f"{PLUGIN_NAME}@"):
                continue
            desired = selector == primary_selector
            settings = dict(raw_settings or {})
            servers = dict(settings.get("mcp_servers") or {})
            evidence_server = dict(servers.get("evidence-lane") or {})
            settings["enabled"] = desired
            evidence_server["enabled"] = desired
            servers["evidence-lane"] = evidence_server
            settings["mcp_servers"] = servers
            plugins[selector] = settings
        primary_write = _batch_write_recovery_plugins(
            executable=executable,
            codex_home=codex_home,
            expected_before_sha256=before_config_sha256,
            plugins=plugins,
        )
        primary_config_sha256 = _sha256(config_path)
        config_archive = (
            data_root / "installations" / "codex-v200" / "config-archives"
        )
        primary_config_backup = (
            config_archive / f"config-{primary_config_sha256}.toml"
        )
        if not primary_config_backup.exists():
            _write_atomic(primary_config_backup, config_path.read_bytes())
        if _sha256(primary_config_backup) != primary_config_sha256:
            raise InstallationError(
                "The normalized primary config backup seal does not match."
            )
        runtime_prewarm = _prewarm_installed_runtime(
            successor_cache,
            data_root=data_root,
        )
        hook_trust, hook_config_receipt = _trust_sealed_plugin_hooks(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            hook_cwd=hook_cwd,
            plugin_selector=LOCAL_SUCCESSOR_SELECTOR,
            activation_mode="VERIFY_DISABLED_SUCCESSOR_FROM_SEALED_PRIMARY",
            last_known_good_selector=primary_selector,
            last_known_good_config_sha256=primary_config_sha256,
            rollback_config_backup=str(primary_config_backup),
        )
    except Exception:
        try:
            _run_codex(
                executable,
                codex_home,
                ["plugin", "remove", LOCAL_SUCCESSOR_SELECTOR, "--json"],
            )
        except Exception:
            pass
        raise

    final_plugin_list = _run_codex(
        executable, codex_home, ["plugin", "list", "--json"]
    )
    final_rows = [
        dict(row)
        for row in final_plugin_list.get("installed") or []
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    final_by_selector = {str(row.get("pluginId") or ""): row for row in final_rows}
    enabled_final = [row for row in final_rows if row.get("enabled") is True]
    hook_records = list(hook_trust.get("records") or [])
    if (
        primary_selector not in final_by_selector
        or LOCAL_SUCCESSOR_SELECTOR not in final_by_selector
        or final_by_selector[primary_selector].get("enabled") is not True
        or final_by_selector[LOCAL_SUCCESSOR_SELECTOR].get("enabled") is not False
        or len(enabled_final) != 1
        or enabled_final[0].get("pluginId") != primary_selector
        or hook_trust.get("status") != "PASS"
        or hook_trust.get("hook_count") != 8
        or len(hook_records) != 8
        or any(
            row.get("enabled") is not False
            or row.get("trust_status") != "trusted"
            for row in hook_records
        )
        or runtime_prewarm.get("status") != "PASS"
        or runtime_prewarm.get("tool_count") != EXPECTED_CATALOG["tools"]
    ):
        raise InstallationError(
            "The installed local successor did not remain disabled with eight "
            "verified hooks beside the sole active primary."
        )
    if (
        two_slot_before is not None
        and _sha256(two_slot_path) != two_slot_before
    ):
        raise InstallationError(
            "The accepted stable/fallback registry changed during successor staging."
        )
    successor_inventory = _source_inventory(successor_cache)
    if (
        successor_inventory["manifest_sha256"]
        != extracted_inventory["manifest_sha256"]
        or successor_inventory["file_count"] != extracted_inventory["file_count"]
    ):
        raise InstallationError(
            "The installed successor bytes do not match the sealed package."
        )

    body: dict[str, Any] = {
        "schema": LOCAL_SUCCESSOR_STAGE_SCHEMA,
        "status": "PASS",
        "state": "SUCCESSOR_INSTALLED_DISABLED_EIGHT_HOOKS_VERIFIED_HOST_STILL_OPEN",
        "package": {
            "archive_sha256": _sha256(archive),
            "package_receipt_sha256": _sha256(package_receipt_path),
            "plugin_version": identity["version"],
            "source_manifest_sha256": successor_inventory["manifest_sha256"],
            "source_file_count": successor_inventory["file_count"],
            "package_receipt_schema": package.get("schema"),
        },
        "primary": {
            "selector": primary_selector,
            "enabled": True,
            "installation_receipt": str(primary_receipt_path),
            "installation_receipt_sha256": primary_receipt_sha256,
        },
        "successor": {
            "selector": LOCAL_SUCCESSOR_SELECTOR,
            "enabled": False,
            "marketplace_root": str(successor_root),
            "marketplace_stage": stage,
            "marketplace_add": marketplace_add,
            "plugin_add": plugin_add,
            "cache_root": str(successor_cache),
            "cache_manifest_sha256": successor_inventory["manifest_sha256"],
            "hook_trust": hook_trust,
            "runtime_prewarm": runtime_prewarm,
            "primary_activation_write": primary_write,
            "hook_config_receipt": hook_config_receipt,
        },
        "restart_gate": {
            "helper_may_be_scheduled": True,
            "exact_host_stop_occurred": False,
            "task_reopened": False,
            "successor_must_be_revalidated_after_host_stop": True,
        },
        "accepted_two_slot_registry": {
            "path": str(two_slot_path) if two_slot_path.is_file() else None,
            "sha256": two_slot_before,
            "mutated": False,
        },
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "git_invoked": False,
    }
    body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    stage_root = (
        data_root / "installations" / "codex-v200" / "local-successor-stages"
    )
    receipt_path = stage_root / f"STAGE_{_sha256(archive)[:16]}.json"
    _write_atomic(receipt_path, _json_bytes(body))
    return {
        **body,
        "receipt_path": str(receipt_path),
        "receipt_file_sha256": _sha256(receipt_path),
    }


def _materialize_local_recovery_copy(args: argparse.Namespace) -> dict[str, Any]:
    """Install one disabled byte-identical recovery copy beside local 2.2.

    This is maintainer-local recovery authority, not accepted PV/Git stable or
    fallback authority.  It uses only supported Codex marketplace, plugin, and
    config APIs, keeps the primary local selector solely enabled, and never
    rewrites ``CURRENT_INSTALLATION`` or the accepted two-slot registry.
    """

    if (
        args.confirm_local_recovery_copy
        != "EXPLICIT_BYTE_IDENTICAL_LOCAL_2_2_RECOVERY"
    ):
        raise InstallationError(
            "Local recovery materialization requires its exact confirmation token."
        )
    if (
        args.archive is None
        or (args.package_receipt is None and args.rehearsal_receipt is None)
        or args.primary_installation_receipt is None
        or not args.primary_installation_receipt_sha256
        or args.codex_executable is None
    ):
        raise InstallationError(
            "Local recovery materialization requires the archive, package receipt, "
            "primary installation receipt and seal, and Codex executable."
        )
    if (
        args.activate
        or args.activate_local_test
        or args.trust_sealed_hooks
        or args.defer_hook_trust_to_user
        or args.recover_disabled_local_hooks_from is not None
    ):
        raise InstallationError(
            "Local recovery materialization cannot be mixed with another install, "
            "activation, or hook-recovery operation."
        )

    archive = args.archive.resolve()
    package_receipt_path = Path(
        args.package_receipt or args.rehearsal_receipt
    ).resolve()
    codex_home = args.codex_home.resolve()
    data_root = args.data_root.resolve()
    executable = args.codex_executable.resolve()
    hook_cwd = Path(args.hook_cwd).resolve()
    if not executable.is_file() or not hook_cwd.is_dir():
        raise InstallationError(
            "The exact Codex executable or governed hook workspace is unavailable."
        )
    _load_receipt(
        package_receipt_path,
        archive,
        activation=False,
        local_test_activation=True,
    )
    primary_receipt_path = args.primary_installation_receipt.resolve()
    primary_receipt_sha256 = _sha256(primary_receipt_path)
    if (
        primary_receipt_sha256
        != str(args.primary_installation_receipt_sha256).upper()
    ):
        raise InstallationError(
            "The primary local installation receipt file seal does not match."
        )
    if not _inside(
        primary_receipt_path,
        data_root / "installations" / "codex-v200",
    ):
        raise InstallationError(
            "The primary local installation receipt is outside durable authority."
        )
    primary_receipt = json.loads(primary_receipt_path.read_text(encoding="utf-8"))
    primary_activation = dict(primary_receipt.get("activation") or {})
    primary_transaction = dict(primary_activation.get("transaction") or {})
    primary_plugin_add = dict(primary_activation.get("plugin_add") or {})
    primary_hook_isolation = dict(
        primary_activation.get("hook_event_isolation") or {}
    )
    primary_selector = str(primary_transaction.get("candidate_selector") or "")
    primary_version = str(dict(primary_receipt.get("plugin") or {}).get("version") or "")
    primary_cache = Path(str(primary_plugin_add.get("installedPath") or "")).resolve()
    if (
        primary_receipt.get("schema") != INSTALL_SCHEMA
        or primary_receipt.get("status") != "PASS"
        or primary_receipt.get("archive_sha256") != _sha256(archive)
        or primary_selector
        != f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}"
        or primary_plugin_add.get("pluginId") != primary_selector
        or primary_plugin_add.get("version") != primary_version
        or not primary_cache.is_dir()
        or not _inside(
            primary_cache,
            codex_home
            / "plugins"
            / "cache"
            / LOCAL_TESTING_MARKETPLACE_NAME
            / PLUGIN_NAME,
        )
        or primary_receipt.get("candidate_created_or_accepted") is not False
        or primary_receipt.get("pointer_moved") is not False
        or primary_receipt.get("hil_inferred") is not False
        or dict(primary_receipt.get("activation_authority") or {}).get(
            "accepted_two_slot_registry_mutated"
        )
        is not False
    ):
        raise InstallationError(
            "The supplied primary local installation is not recovery-copy eligible."
        )

    two_slot_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / "CODEX_TWO_SLOT_REGISTRY.json"
    )
    two_slot_before = _sha256(two_slot_path) if two_slot_path.is_file() else None
    before_plugin_list = _run_codex(
        executable, codex_home, ["plugin", "list", "--json"]
    )
    before_rows = [
        dict(row)
        for row in before_plugin_list.get("installed") or []
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    primary_rows = [row for row in before_rows if row.get("pluginId") == primary_selector]
    recovery_rows = [
        row for row in before_rows if row.get("pluginId") == LOCAL_RECOVERY_SELECTOR
    ]
    enabled_before = [row for row in before_rows if row.get("enabled") is True]
    if (
        len(primary_rows) != 1
        or primary_rows[0].get("enabled") is not True
        or len(recovery_rows) > 1
        or any(row.get("enabled") is True for row in recovery_rows)
        or len(enabled_before) != 1
        or enabled_before[0].get("pluginId") != primary_selector
    ):
        raise InstallationError(
            "Recovery materialization requires one active primary local selector "
            "and no active recovery selector."
        )
    if recovery_rows:
        _run_codex(
            executable,
            codex_home,
            ["plugin", "remove", LOCAL_RECOVERY_SELECTOR, "--json"],
        )

    config_path = codex_home / "config.toml"
    primary_config_bytes = config_path.read_bytes()
    primary_config_sha256 = hashlib.sha256(primary_config_bytes).hexdigest().upper()
    config_archive = data_root / "installations" / "codex-v200" / "config-archives"
    primary_config_backup = config_archive / f"config-{primary_config_sha256}.toml"
    if not primary_config_backup.exists():
        _write_atomic(primary_config_backup, primary_config_bytes)
    if _sha256(primary_config_backup) != primary_config_sha256:
        raise InstallationError("The primary-local config backup seal does not match.")

    recovery_root = (
        codex_home / "local-marketplaces" / LOCAL_RECOVERY_MARKETPLACE_NAME
    )
    with tempfile.TemporaryDirectory(prefix="evidence-lane-v220-recovery-") as raw:
        extracted = Path(raw) / "plugin"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        identity = _validate_plugin(extracted)
        extracted_inventory = _source_inventory(extracted)
        stage = _stage_marketplace(
            extracted=extracted,
            marketplace_root=recovery_root,
            data_root=data_root,
            identity=identity,
            archive_sha256=_sha256(archive),
            marketplace_name=LOCAL_RECOVERY_MARKETPLACE_NAME,
            comparison_surface=identity["surface_inventory"],
            comparison_baseline={
                "primary_installation_receipt_sha256": primary_receipt_sha256,
                "primary_selector": primary_selector,
            },
        )

    marketplaces = _run_codex(
        executable, codex_home, ["plugin", "marketplace", "list", "--json"]
    )
    exact_marketplaces = [
        dict(row)
        for row in marketplaces.get("marketplaces") or []
        if isinstance(row, dict)
        and row.get("name") == LOCAL_RECOVERY_MARKETPLACE_NAME
    ]
    if len(exact_marketplaces) > 1:
        raise InstallationError("Codex exposed duplicate local recovery marketplaces.")
    if exact_marketplaces:
        if Path(str(exact_marketplaces[0].get("root") or "")).resolve() != recovery_root.resolve():
            raise InstallationError(
                "The configured recovery marketplace root does not match staging."
            )
        marketplace_add: dict[str, Any] = {
            "status": "REUSED_EXACT_LOCAL_MARKETPLACE",
            "name": LOCAL_RECOVERY_MARKETPLACE_NAME,
            "root": str(recovery_root.resolve()),
        }
    else:
        marketplace_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "add", str(recovery_root), "--json"],
        )

    try:
        plugin_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "add", LOCAL_RECOVERY_SELECTOR, "--json"],
        )
        recovery_cache = Path(str(plugin_add.get("installedPath") or "")).resolve()
        expected_recovery_cache = (
            codex_home
            / "plugins"
            / "cache"
            / LOCAL_RECOVERY_MARKETPLACE_NAME
            / PLUGIN_NAME
        )
        if (
            plugin_add.get("pluginId") != LOCAL_RECOVERY_SELECTOR
            or plugin_add.get("version") != primary_version
            or not recovery_cache.is_dir()
            or not _inside(recovery_cache, expected_recovery_cache)
        ):
            raise InstallationError(
                "Codex installed a mismatched local recovery cache identity."
            )
        runtime_prewarm = _prewarm_installed_runtime(
            recovery_cache,
            data_root=data_root,
        )
        hook_isolation = _verify_recovery_copy_hook_event_isolation(
            recovery_cache,
            data_root=data_root,
            primary_hook_isolation=primary_hook_isolation,
        )
        hook_trust, config_receipt = _trust_sealed_plugin_hooks(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            hook_cwd=hook_cwd,
            plugin_selector=LOCAL_RECOVERY_SELECTOR,
            activation_mode="PREPARE_RECOVERY_DISABLED",
            last_known_good_selector=primary_selector,
            last_known_good_config_sha256=primary_config_sha256,
            rollback_config_backup=str(primary_config_backup),
        )
    except Exception:
        try:
            _run_codex(
                executable,
                codex_home,
                ["plugin", "remove", LOCAL_RECOVERY_SELECTOR, "--json"],
            )
        except Exception:
            pass
        raise

    final_plugin_list = _run_codex(
        executable, codex_home, ["plugin", "list", "--json"]
    )
    final_rows = [
        dict(row)
        for row in final_plugin_list.get("installed") or []
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    final_by_selector = {str(row.get("pluginId") or ""): row for row in final_rows}
    enabled_final = [row for row in final_rows if row.get("enabled") is True]
    if (
        primary_selector not in final_by_selector
        or LOCAL_RECOVERY_SELECTOR not in final_by_selector
        or final_by_selector[primary_selector].get("enabled") is not True
        or final_by_selector[LOCAL_RECOVERY_SELECTOR].get("enabled") is not False
        or len(enabled_final) != 1
        or enabled_final[0].get("pluginId") != primary_selector
    ):
        raise InstallationError(
            "The final local primary/recovery activation state is not exclusive."
        )
    primary_inventory = _source_inventory(primary_cache)
    recovery_inventory = _source_inventory(recovery_cache)
    marketplace_inventory = _source_inventory(
        recovery_root / "plugins" / PLUGIN_NAME
    )
    inventory_identities = {
        extracted_inventory["manifest_sha256"],
        primary_inventory["manifest_sha256"],
        recovery_inventory["manifest_sha256"],
        marketplace_inventory["manifest_sha256"],
    }
    inventory_counts = {
        extracted_inventory["file_count"],
        primary_inventory["file_count"],
        recovery_inventory["file_count"],
        marketplace_inventory["file_count"],
    }
    if len(inventory_identities) != 1 or len(inventory_counts) != 1:
        raise InstallationError(
            "The local primary and disabled recovery plugin bytes are not identical."
        )
    two_slot_after = _sha256(two_slot_path) if two_slot_path.is_file() else None
    if two_slot_after != two_slot_before:
        raise InstallationError(
            "The accepted stable/fallback two-slot registry changed during local recovery materialization."
        )

    body: dict[str, Any] = {
        "schema": LOCAL_RECOVERY_REGISTRY_SCHEMA,
        "status": "PASS",
        "state": "PRIMARY_LOCAL_ACTIVE_RECOVERY_DISABLED_BYTE_IDENTICAL",
        "package": {
            "archive_sha256": _sha256(archive),
            "package_receipt_sha256": _sha256(package_receipt_path),
            "plugin_version": primary_version,
            "source_manifest_sha256": extracted_inventory["manifest_sha256"],
            "source_file_count": extracted_inventory["file_count"],
        },
        "primary": {
            "selector": primary_selector,
            "enabled": True,
            "native_mcp_enabled": True,
            "installation_receipt": str(primary_receipt_path),
            "installation_receipt_sha256": primary_receipt_sha256,
            "cache_root": str(primary_cache),
            "cache_manifest_sha256": primary_inventory["manifest_sha256"],
        },
        "recovery": {
            "selector": LOCAL_RECOVERY_SELECTOR,
            "slot_role": "local-stable-recovery",
            "enabled": False,
            "native_mcp_enabled": False,
            "byte_identical_to_primary": True,
            "marketplace_root": str(recovery_root),
            "marketplace_stage": stage,
            "marketplace_add": marketplace_add,
            "plugin_add": plugin_add,
            "cache_root": str(recovery_cache),
            "cache_manifest_sha256": recovery_inventory["manifest_sha256"],
            "hook_trust": hook_trust,
            "hook_isolation": hook_isolation,
            "runtime_prewarm": runtime_prewarm,
            "config_receipt": config_receipt,
        },
        "selection_law": {
            "primary_first": primary_selector,
            "recovery_second": LOCAL_RECOVERY_SELECTOR,
            "accepted_2_1_stable_or_fallback_automatic_recovery_allowed": False,
            "switch_requires_primary_failure_and_exact_registry_seal": True,
            "host_restart_required_after_selector_switch": True,
            "restart_loop_allowed": False,
        },
        "accepted_two_slot_registry": {
            "path": str(two_slot_path) if two_slot_path.is_file() else None,
            "before_sha256": two_slot_before,
            "after_sha256": two_slot_after,
            "mutated": False,
        },
        "scope": {
            "maintainer_local_only": True,
            "accepted_pv_or_git_stable_authority": False,
            "openai_host_tooling_absorbed": False,
            "mcp_action_catalog_treated_as_dependency_toolchain": False,
        },
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "git_invoked": False,
    }
    body["registry_body_sha256"] = _ordered_json_sha256(body)
    body["seal"] = {
        "algorithm": "SHA256",
        "body_sha256": _ordered_json_sha256(body),
    }
    recovery_registry_root = (
        data_root / "installations" / "codex-v200" / "local-v220-recovery"
    )
    receipt_path = (
        recovery_registry_root
        / f"LOCAL_V220_RECOVERY_{_sha256(archive)[:16]}.json"
    )
    _write_atomic(receipt_path, _json_bytes(body))
    current_path = recovery_registry_root / "CURRENT_LOCAL_V220_RECOVERY.json"
    _write_atomic(current_path, _json_bytes(body))
    return {
        **body,
        "receipt_path": str(receipt_path),
        "receipt_file_sha256": _sha256(receipt_path),
        "current_registry_path": str(current_path),
        "current_registry_file_sha256": _sha256(current_path),
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
            os.environ.get("EVIDENCE_LANE_DATA_ROOT")
            or Path.home() / "EvidenceLanePV"
        ),
    )
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument(
        "--materialize-local-recovery-copy",
        action="store_true",
        help=(
            "Install one disabled byte-identical local 2.2 recovery selector "
            "without mutating accepted stable/fallback authority."
        ),
    )
    parser.add_argument(
        "--stage-loaded-local-successor",
        action="store_true",
        help=(
            "Install one inactive fixed local-successor slot and verify its "
            "runtime and eight hooks before the loaded desktop is stopped."
        ),
    )
    parser.add_argument("--primary-installation-receipt", type=Path)
    parser.add_argument("--primary-installation-receipt-sha256")
    parser.add_argument("--confirm-local-recovery-copy")
    parser.add_argument("--confirm-local-successor-stage")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument(
        "--activate-local-test",
        action="store_true",
        help=(
            "Activate only evidence-lane-v220-testing-new from a sealed local "
            "rehearsal without changing accepted stable/fallback authority."
        ),
    )
    parser.add_argument("--confirm-local-test-rotation")
    parser.add_argument(
        "--recover-disabled-local-hooks-from",
        type=Path,
        help=(
            "Reinstall and reactivate only the disabled local 2.2 selector from "
            "one exact prior committed-local receipt after hook correction."
        ),
    )
    parser.add_argument("--recover-disabled-local-hooks-from-sha256")
    parser.add_argument(
        "--seal-local-test-host-proof",
        type=Path,
        help=(
            "Read one staged receipt, prove its disabled candidate through fresh "
            "hooks/plugin/catalog reads, and emit the commit authority."
        ),
    )
    parser.add_argument("--seal-local-test-host-proof-sha256")
    parser.add_argument(
        "--commit-local-test-transaction",
        type=Path,
        help=(
            "Commit one sealed disabled local-test candidate after visible hook "
            "trust and exact host proof; this is separate from Goal recovery."
        ),
    )
    parser.add_argument("--commit-local-test-transaction-sha256")
    parser.add_argument("--local-test-host-proof", type=Path)
    parser.add_argument("--local-test-host-proof-sha256")
    parser.add_argument(
        "--recover-local-test-transaction",
        type=Path,
        help=(
            "Recover one failed committed local-test candidate through the "
            "idempotent bounded self-rollback controller."
        ),
    )
    parser.add_argument("--recover-local-test-transaction-sha256")
    parser.add_argument(
        "--allow-byte-frozen-fallback",
        action="store_true",
        help=(
            "Permit the second bounded recovery attempt only when the installed "
            "two-slot registry proves an exact byte-frozen fallback."
        ),
    )
    parser.add_argument(
        "--seal-install-correction",
        type=Path,
        help=(
            "Consume one successful candidate self-rollback and atomically point "
            "CURRENT_INSTALLATION at its fully proven correction generation."
        ),
    )
    parser.add_argument("--seal-install-correction-sha256")
    parser.add_argument("--corrected-installation-base", type=Path)
    parser.add_argument("--corrected-installation-base-sha256")
    parser.add_argument(
        "--defer-hook-trust-to-user",
        action="store_true",
        help="Leave the exact eight hook trust decisions for the visible Codex UI.",
    )
    parser.add_argument(
        "--trust-sealed-hooks",
        action="store_true",
        help=(
            "Trust exactly the installed selector's eight current hook hashes "
            "through Codex hooks/list plus config/batchWrite. Required with --activate."
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
    if args.stage_loaded_local_successor:
        raise InstallationError(
            "The local-successor marketplace is retired. Reinstall a fresh package "
            "version into evidence-lane-v220-testing-new before the restart-only helper."
        )
    if args.materialize_local_recovery_copy:
        raise InstallationError(
            "Per-build local recovery copying is retired. The branch-commit recovery "
            "slot must remain byte-frozen at its prior governed Git checkpoint until "
            "the next explicit checkpoint convergence."
        )
    if (
        args.primary_installation_receipt is not None
        or args.primary_installation_receipt_sha256 is not None
        or args.confirm_local_recovery_copy is not None
        or args.confirm_local_successor_stage is not None
    ):
        raise InstallationError(
            "Local recovery inputs require their exact operation flag; the successor "
            "route is retired."
        )
    proof_requested = args.seal_local_test_host_proof is not None
    commit_requested = args.commit_local_test_transaction is not None
    recovery_requested = args.recover_local_test_transaction is not None
    correction_requested = args.seal_install_correction is not None
    disabled_hook_recovery_requested = (
        args.recover_disabled_local_hooks_from is not None
    )
    if (args.recover_disabled_local_hooks_from is None) != (
        args.recover_disabled_local_hooks_from_sha256 is None
    ):
        raise InstallationError(
            "Disabled-local hook recovery requires the exact prior commit path "
            "and file SHA-256."
        )
    if disabled_hook_recovery_requested and any(
        [proof_requested, commit_requested, recovery_requested, correction_requested]
    ):
        raise InstallationError(
            "Disabled-local hook recovery cannot be mixed with another local-test "
            "proof, commit, rollback, or correction operation."
        )
    if (
        sum(
            [
                proof_requested,
                commit_requested,
                recovery_requested,
                correction_requested,
            ]
        )
        > 1
    ):
        raise InstallationError(
            "Host proof, candidate commit, self-rollback, and correction sealing "
            "are separate operations."
        )
    if args.allow_byte_frozen_fallback and not recovery_requested:
        raise InstallationError(
            "Byte-frozen fallback authority is valid only for candidate self-rollback."
        )
    if proof_requested:
        if (
            args.seal_local_test_host_proof_sha256 is None
            or args.codex_executable is None
        ):
            raise InstallationError(
                "Local-test host proof requires the staged receipt file seal and "
                "--codex-executable."
            )
        if (
            args.archive is not None
            or args.package_receipt is not None
            or args.rehearsal_receipt is not None
            or args.activate
            or args.activate_local_test
            or args.trust_sealed_hooks
            or args.defer_hook_trust_to_user
        ):
            raise InstallationError(
                "Local-test host proof is read/probe-only and cannot be mixed with "
                "staging, installation, or Goal-recovery controls."
            )
        result = _seal_local_test_host_proof(
            stage_receipt_path=args.seal_local_test_host_proof,
            stage_receipt_sha256=args.seal_local_test_host_proof_sha256,
            executable=args.codex_executable,
            codex_home=args.codex_home,
            data_root=args.data_root,
            hook_cwd=args.hook_cwd,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if commit_requested:
        if (
            args.commit_local_test_transaction_sha256 is None
            or args.local_test_host_proof is None
            or args.local_test_host_proof_sha256 is None
            or args.codex_executable is None
        ):
            raise InstallationError(
                "Local-test commit requires the exact staged receipt and host-proof "
                "paths, both file seals, and --codex-executable."
            )
        if (
            args.archive is not None
            or args.package_receipt is not None
            or args.rehearsal_receipt is not None
            or args.activate
            or args.activate_local_test
            or args.trust_sealed_hooks
            or args.defer_hook_trust_to_user
        ):
            raise InstallationError(
                "Local-test commit is a separate CAS operation and cannot be mixed "
                "with staging, installation, or Goal-recovery controls."
            )
        result = _commit_local_test_transaction(
            stage_receipt_path=args.commit_local_test_transaction,
            stage_receipt_sha256=args.commit_local_test_transaction_sha256,
            host_proof_path=args.local_test_host_proof,
            host_proof_sha256=args.local_test_host_proof_sha256,
            executable=args.codex_executable,
            codex_home=args.codex_home,
            data_root=args.data_root,
            hook_cwd=args.hook_cwd,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if recovery_requested:
        if (
            args.recover_local_test_transaction_sha256 is None
            or args.codex_executable is None
        ):
            raise InstallationError(
                "Local-test recovery requires the exact commit receipt file seal "
                "and --codex-executable."
            )
        if (
            args.archive is not None
            or args.package_receipt is not None
            or args.rehearsal_receipt is not None
            or args.activate
            or args.activate_local_test
            or args.trust_sealed_hooks
            or args.defer_hook_trust_to_user
            or args.local_test_host_proof is not None
            or args.local_test_host_proof_sha256 is not None
        ):
            raise InstallationError(
                "Candidate self-rollback cannot be mixed with staging, install, "
                "hook trust, host proof, or Goal-recovery controls."
            )
        result = _recover_local_test_candidate_failure(
            commit_receipt_path=args.recover_local_test_transaction,
            commit_receipt_sha256=(
                args.recover_local_test_transaction_sha256
            ),
            executable=args.codex_executable,
            codex_home=args.codex_home,
            data_root=args.data_root,
            allow_byte_frozen_fallback=args.allow_byte_frozen_fallback,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "PASS" else 2
    if correction_requested:
        if (
            args.seal_install_correction_sha256 is None
            or args.corrected_installation_base is None
            or args.corrected_installation_base_sha256 is None
            or args.codex_executable is None
        ):
            raise InstallationError(
                "Install correction sealing requires the recovery and base receipt "
                "paths, both file seals, and --codex-executable."
            )
        if (
            args.archive is not None
            or args.package_receipt is not None
            or args.rehearsal_receipt is not None
            or args.activate
            or args.activate_local_test
            or args.trust_sealed_hooks
            or args.defer_hook_trust_to_user
            or args.local_test_host_proof is not None
            or args.local_test_host_proof_sha256 is not None
            or args.allow_byte_frozen_fallback
        ):
            raise InstallationError(
                "Install correction sealing cannot be mixed with staging, install, "
                "hook trust, host proof, fallback selection, or Goal controls."
            )
        result = _seal_install_correction_generation(
            recovery_receipt_path=args.seal_install_correction,
            recovery_receipt_sha256=args.seal_install_correction_sha256,
            base_installation_path=args.corrected_installation_base,
            base_installation_sha256=args.corrected_installation_base_sha256,
            executable=args.codex_executable,
            codex_home=args.codex_home,
            data_root=args.data_root,
            hook_cwd=args.hook_cwd,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.archive is None or (
        args.package_receipt is None and args.rehearsal_receipt is None
    ):
        raise InstallationError(
            "Staging or activation requires --archive and exactly one package receipt."
        )
    if (args.activate or args.activate_local_test) and args.codex_executable is None:
        raise InstallationError("Activation requires --codex-executable.")
    if args.activate != args.trust_sealed_hooks:
        raise InstallationError(
            "--activate and --trust-sealed-hooks must be supplied together."
        )
    if args.activate and (
        args.release_authority_receipt is None
        or args.release_authority_receipt_sha256 is None
    ):
        raise InstallationError(
            "--activate requires the governed Git/CI/Vercel release-authority receipt "
            "and its file SHA-256."
        )
    if args.activate_local_test:
        if args.marketplace_name != LOCAL_TESTING_MARKETPLACE_NAME:
            raise InstallationError(
                "Local-test activation requires the fixed local testing selector."
            )
        if disabled_hook_recovery_requested:
            if (
                args.confirm_local_test_rotation
                != LOCAL_TEST_DISABLED_HOOK_RECOVERY_CONFIRMATION
                or args.defer_hook_trust_to_user
            ):
                raise InstallationError(
                    "Disabled-local hook recovery requires its exact token and "
                    "installer-owned sealed hook trust."
                )
        elif (
            args.confirm_local_test_rotation
            != "EXPLICIT_ONE_TIME_LOCAL_TEST_ROTATION"
            or not args.defer_hook_trust_to_user
        ):
            raise InstallationError(
                "Local-test activation requires the fixed selector, exact confirmation "
                "token, and visible user hook-trust boundary."
            )
    elif disabled_hook_recovery_requested:
        raise InstallationError(
            "Disabled-local hook recovery requires --activate-local-test."
        )
    print(json.dumps(install(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
