from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.constants import (
    GOVERNED_SKILL_COUNT,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def test_v300_declares_exact_two_role_maintainer_slots() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    stable = contract["stable"]
    local_testing = contract["local_testing"]
    live_slots = contract["live_slot_policy"]

    assert stable["release"] == "3.0.0"
    assert stable["slot_role"] == "main-git-release"
    assert stable["codex_marketplace_slot"] == "evidence-lane-github"
    assert stable["marketplace_display_name"] == "Main Git Plugin Version"
    assert stable["install_source"] == (
        "GIT_MAIN_EXACT_COMMIT_AFTER_GOVERNED_MERGE"
    )
    assert stable["native_tool_count"] == NATIVE_TOOL_COUNT
    assert stable["skill_count"] == GOVERNED_SKILL_COUNT
    assert stable["direct_stdio_fallback_allowed"] is False

    assert "retired_branch_recovery" not in contract
    assert local_testing["release_line"] == "3.0.0"
    assert local_testing["slot_role"] == "versioned-local-testing"
    assert local_testing["codex_marketplace_slot"] == "evidence-lane-v300-testing-new"
    assert local_testing["helper_installs_plugin"] is False

    assert live_slots["exact_slot_count"] == 2
    assert live_slots["allowed_slots"] == [
        "main-git-release",
        "versioned-local-testing",
    ]
    assert live_slots["max_enabled_plugin_count"] == 1
    assert live_slots["exact_registered_plugin_count"] == 2
    assert live_slots["stable_selector_growth_allowed"] is False
    assert live_slots["max_active_native_mcp_count"] == 1
    assert live_slots["max_active_tunnel_count"] == 1
    assert live_slots["inactive_slot_remains_installed"] is True
    assert live_slots["manual_loaded_cache_deletion_allowed"] is False
    assert live_slots["obsolete_marketplace_registrations_must_be_absent"] is True


def test_v300_pv13_pv14_sequence_blocks_early_promotion() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    sequence = contract["pv_sequence_boundary"]

    assert sequence == {
        "release_line": "3.0.0",
        "entry_pointer": "PV12",
        "intermediate_candidate": "PV13",
        "intermediate_approve_effect": "FUSE_PV13_ONLY",
        "intermediate_main_merge_allowed": True,
        "intermediate_third_slot_creation_allowed": False,
        "physically_final_candidate": "PV14",
        "final_approve_required_before_main_or_fallback": True,
        "final_approve_effects": [
            "FUSE_PV14_GENERATION_14",
            "GOVERNED_NON_FORCE_MAIN_PROMOTION",
            "INSTALL_EXACT_ACCEPTED_3_0_IN_ENABLED_STABLE_GIT_MAIN_SLOT",
            "PRESERVE_VERSIONED_LOCAL_TESTING_SLOT_AS_THE_ONLY_SECOND_SLOT",
        ],
        "release_3_1_in_current_goal_allowed": False,
        "next_cycle_entry_pointer": "PV14",
        "next_cycle_candidate": "PV15",
        "next_cycle_requires_fresh_user_start": True,
    }

    readme = (ROOT / "README.md").read_text("utf-8")
    for internal_pointer in ("PV12", "PV13", "PV14", "PV15"):
        assert internal_pointer not in readme


def test_plugin_release_cycle_never_leaks_into_downstream_project_pvs() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    scope = contract["workflow_scope"]

    assert scope["plugin_release_cadence"] == (
        "ONE_AUTHORIZED_LOGICAL_RELEASE_COMMIT_BATCH"
    )
    assert scope["plugin_release_steps"][-1] == "PLUGIN_PV_HIL"
    assert scope["downstream_project_pv_inherits_plugin_release_cycle"] is False
    assert scope["downstream_project_controls"] == [
        "OWN_GIT_CI_DEPLOY_WORKFLOW",
        "GOVERNED_SCHEMA_AND_LANE_EVOLUTION",
        "BOUNDED_ADDITIONAL_PLUGINS",
        "STORAGE_CONNECTOR_SELECTION",
    ]
    assert scope["intermediate_pv13_install_hil_route"] == {
        "ci_prerequisite_row": 266,
        "execution_row": 267,
        "release": "3.0.0",
        "branch": "main",
        "source": (
            "EXACT_GIT_MAIN_COMMIT_AFTER_REQUIRED_CLEAN_CI_AND_"
            "GIT_TRIGGERED_VERCEL_PREVIEW"
        ),
        "slot_role": "main-git-release",
        "plugin_selector": "evidence-lane-plugin@evidence-lane-github",
        "versioned_local_testing_selector": (
            "evidence-lane-plugin@evidence-lane-v300-testing-new"
        ),
        "working_role_sync_required": True,
        "installed_version_must_equal_exact_package_version": True,
        "installed_catalog_must_equal": {
                "native_actions": NATIVE_TOOL_COUNT,
                "read_actions": NATIVE_READ_TOOL_COUNT,
                "write_actions": NATIVE_WRITE_TOOL_COUNT,
                "governed_skills": GOVERNED_SKILL_COUNT,
            "hook_events": 11,
            "separate_command_layer_present": False,
        },
        "installed_ui_readback_required_before_pv13_hil": True,
        "main_git_release_slot_mutation_allowed": True,
        "main_merge_allowed": True,
        "downstream_project_inherits_install": False,
    }
    assert scope["full_vercel_guide_refresh"] == "ASSIGNED_WEBSITE_DELTA_ONLY"
    assert scope["plan_or_pv_projection_update_is_full_site_refresh"] is False
    assert scope["env_uop_evolution_requires_new_sealed_identity"] is True
    assert scope["accepted_locked_env_uop_mutation_allowed"] is False

    readme = (ROOT / "README.md").read_text("utf-8")
    architecture = (ROOT / "ARCHITECTURE.md").read_text("utf-8")
    assert "A downstream project does not inherit" in readme.replace("\n", " ")
    assert "A downstream user's project PV does not reinstall" in architecture


def test_goal_completion_is_human_owned_and_independent_of_hil() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    policy = contract["goal_completion_policy"]

    assert policy == {
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
            "authoritative_current_route": (
                "build_rich_goal_completion_metrics_receipt"
            ),
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
    for skill_name in (
        "evi",
        "evi-state-travel",
        "evidence-lane-code-lifecycle",
    ):
        text = (PLUGIN / "skills" / skill_name / "SKILL.md").read_text("utf-8")
        assert "MARK GOAL COMPLETE" in text
        assert "COMPLETE_THIS_TASK_AND_STATE_TRAVEL" in text
        assert "COMPLETE_FULLY" in text
        assert "HIL approval" in text
        assert "build_rich_goal_completion_metrics_receipt" in text
        assert "build_goal_usage_receipt" not in text
        assert "OBSOLETE_ROUTE" not in text


def test_helper_tunnel_rotation_is_plugin_maintainer_only_and_final_hil_gated() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    helper = contract["helper_distribution_policy"]
    rotation = helper["post_hil_release_rotation"]
    runtime = helper["runtime_storage_boundary"]

    assert runtime == {
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
    }
    assert helper["user_stable_tunnel"]["release"] == "3.0.0"
    assert helper["user_stable_tunnel"]["release_token"] == "v300"
    assert helper["user_stable_tunnel"]["scheduled_task_name"] == (
        "EvidenceLane-Tunnel-v300-stable-build"
    )
    assert helper["user_stable_tunnel"]["at_logon"] is True
    assert helper["user_stable_tunnel"]["scheduled_task_transport_allowed"] is True
    assert helper["user_stable_tunnel"]["host_wide_project_neutral"] is True
    assert "retired_branch_recovery_transport" not in helper
    assert "user_goal_recovery_helper" not in helper
    assert rotation["applies_to_plugin_maintainer_route_only"] is True
    assert rotation["downstream_project_inherits_rotation"] is False
    assert rotation["local_test_green_can_promote_only_through_exact_main_merge"] is True
    assert rotation["pre_3_0_fallback_allowed"] is False
    assert rotation["final_gate"] == "PV14_EXACT_HUMAN_APPROVE_AND_FUSE"
    assert rotation["required_order"][-1] == (
        "VERIFY_EXACTLY_TWO_SLOTS_AND_ONE_ACTIVE_RUNTIME"
    )
    assert rotation["stable_git_main_must_equal_exact_merged_release"] is True
    assert rotation["helper_and_tunnel_release_must_match_owning_slot"] is True
    assert rotation["prior_versioned_helpers_and_tunnels_retained"] is False
    assert rotation["prior_versioned_helpers_and_tunnels_disabled"] is False
    assert rotation["prior_versioned_helpers_and_tunnels_deleted"] is True
    assert rotation["repeat_for_each_later_plugin_release_cycle"] is True
    assert rotation["current_row_may_execute_rotation"] is False


def test_historical_v210_bundle_matrix_is_not_a_current_public_document() -> None:
    assert not (ROOT / "docs" / "V210_LOGICAL_INTEGRATION_BUNDLE_MATRIX.json").exists()


def test_promotion_requires_matching_cross_surface_receipts_and_hil() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    promotion = contract["promotion_gate"]
    assert promotion["explicit_authority_hil_required"] is True
    assert promotion["required_catalog"] == {
        "tools": NATIVE_TOOL_COUNT,
        "read": NATIVE_READ_TOOL_COUNT,
        "write": NATIVE_WRITE_TOOL_COUNT,
        "skills": GOVERNED_SKILL_COUNT,
    }
    assert promotion["fail_closed_on_version_mismatch"] is True
    assert promotion["mode"] == "CODE"
    assert promotion["ci_cd_law"] == "CONTROLLED_REQUIRED"


def test_v200_host_storage_tunnel_matrix_keeps_routing_axes_independent() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    matrix = contract["host_storage_tunnel_matrix"]

    assert matrix["routing_axes_independent"] is True
    assert matrix["account_tier_affects_routing"] is False
    assert matrix["api_billing_affects_routing"] is False
    assert matrix["headless_api"] == {
        "local_or_persistent_pv_storage": "LOCAL_SQLITE_WHEN_DURABLE",
        "ephemeral_pv_storage": (
            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
        ),
        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
        "flash_frequency": "EVERY_INVOCATION_ENTRY",
    }
    assert matrix["interactive_codex_app_local_or_persistent"] == {
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
    }
    assert matrix["codex_cli_local_or_persistent"] == {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
        "native_mcp_available": {
            "tunnel_requirement": "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
        },
        "host_tool_gap": {
            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
            "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        },
    }
    assert matrix["interactive_codex_app_ephemeral_vm"] == {
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
    }
    assert matrix["desktop_container_surface_scope"] == {
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
    }


def test_v300_remote_git_policy_supersedes_only_historical_flash_sentence() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    policy = contract["remote_git_policy"]

    assert policy == {
        "effective_release": "3.0.0",
        "v150_flash_confirmation_sentence": (
            "HISTORICAL_HASH_LOCKED_COMPATIBILITY_BYTE_NOT_EFFECTIVE_V2_POLICY"
        ),
        "prepare_receipt_required": True,
        "per_push_confirmation_token_required": False,
        "automatic_push_scope": (
            "GITHUB_APP_GOVERNED_FEATURE_BRANCH_THEN_EXACT_MAIN_MERGE"
        ),
        "host_managed_credentials_only": True,
        "main_push_allowed": False,
        "merge_allowed": True,
        "pull_request_acceptance_allowed": True,
        "force_push_allowed": False,
    }


def test_tunnel_is_single_active_runtime_and_purges_prior_versions() -> None:
    installer = (
        PLUGIN / "scripts" / "windows_tunnel" / "Install-EvidenceLaneTunnel.ps1"
    ).read_text("utf-8")
    assert not (
        PLUGIN
        / "scripts"
        / "windows_tunnel"
        / "Manage-EvidenceLaneTunnelVersions.ps1"
    ).exists()
    assert "Remove-StoppedPriorTunnelRuntimes" in installer
    assert "Remove-StoppedPriorTunnelTasks" in installer
    assert "prior_versioned_runtimes_retained = $false" in installer
    assert "prior_versioned_tasks_retained = $false" in installer
    assert "prior_versioned_runtime_deletion_required = $true" in installer



def test_obsolete_failover_operator_is_physically_absent() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    assert "failover_operator" not in contract
    assert not (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Switch-EvidenceLaneCodexSlot.ps1"
    ).exists()
    restart = (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Prepare-EvidenceLaneCodexRestart.ps1"
    ).read_text("utf-8")
    assert "TERMINAL_SAFE_RESTART_PREPARED_NOT_EXECUTED" in restart
    assert "programmatic_process_stop_allowed = $false" in restart



def test_goal_continuation_uses_native_task_binding_without_user_helper() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    assert "goal_recovery" not in contract
    assert "user_goal_recovery_helper" not in contract["helper_distribution_policy"]
    assert not (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
    ).exists()
    turn_control = (
        PLUGIN / "src" / "evidence_lane_plugin" / "codex_turn_control.py"
    ).read_text("utf-8")
    assert "NATIVE_ACTIVE_GOAL_EXACT_TASK_BINDING" in turn_control
    assert "PRETOOLUSE_HOST_PAYLOAD" in turn_control
    assert "codex-goal-recovery-binding" not in turn_control



def test_codex_behavior_belongs_to_skills_and_hooks_remain_lifecycle_only() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    ownership = contract["behavior_ownership"]

    assert ownership == {
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
    assert contract["lifecycle_hook_matrix"] == {
        "schema": "evidence-lane.codex-hook-lifecycle-contract.v1",
        "contract_version": 1,
        "hooks_own_lifecycle_transport_only": True,
        "hook_adapters_import_behavior_functions": False,
        "skill_runtime_consumer": (
            "src/evidence_lane_plugin/hook_skill_runtime.py"
        ),
        "skill_runtime_owner": (
            "INSTALLED_EVIDENCE_LANE_CODE_LIFECYCLE_SKILL"
        ),
        "skills_own_prepare_behavior_native_reads_classification_plan_refresh_goal_and_hil": True,
            "required_events": [
                "SessionStart",
                "SubagentStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PermissionRequest",
                "PostToolUse",
                "PreCompact",
                "PostCompact",
                "SubagentStop",
                "Stop",
                "SessionEnd",
            ],
            "hook_transport_owners": {
                "SessionStart": "HOST_ENTRY_SIGNAL",
                "SubagentStart": (
                    "BOUND_OBSERVATION_ONLY_SUBAGENT_START_SIGNAL"
                ),
                "UserPromptSubmit": "VISIBLE_INPUT_SIGNAL",
                "PreToolUse": "PROSPECTIVE_TOOL_SIGNAL",
                "PermissionRequest": (
                    "BOUND_OBSERVATION_ONLY_PERMISSION_SIGNAL"
                ),
                "PostToolUse": "VISIBLE_TOOL_RESULT_SIGNAL",
                "PreCompact": "COMPACTION_SEAL_SIGNAL",
                "PostCompact": "COMPACTION_REENTRY_SIGNAL",
                "SubagentStop": (
                    "BOUND_OBSERVATION_ONLY_SUBAGENT_STOP_SIGNAL"
                ),
                "Stop": "VISIBLE_RESPONSE_STOP_SIGNAL",
                "SessionEnd": "SESSION_END_SIGNAL",
            },
            "skill_action_owners": {
                "SessionStart": "SKILL_BOOT_RESUME_OR_PANEL_REENTRY",
                "SubagentStart": "SKILL_BOUND_OBSERVATION_ONLY",
                "UserPromptSubmit": "SKILL_PREPARE_THEN_NATIVE_READ_SEQUENCE",
                "PreToolUse": "SKILL_BOUNDARY_AND_POLICY_OWNER",
                "PermissionRequest": (
                    "SKILL_BOUND_OBSERVATION_ONLY_NEVER_GRANT_OR_DENY"
                ),
                "PostToolUse": "SKILL_RECEIPT_AND_PLAN_REFRESH_OWNER",
                "PreCompact": "SKILL_CONTINUITY_SEAL_OWNER",
                "PostCompact": "SKILL_REBIND_AND_FULL_PLAN_REENTRY_OWNER",
                "SubagentStop": "SKILL_BOUND_OBSERVATION_ONLY",
                "Stop": "SKILL_IDEMPOTENT_COMMIT_OWNER",
                "SessionEnd": "SKILL_BEST_EFFORT_BOUNDARY_FLUSH_OWNER",
        },
        "goal_continuation_route": {
            "host_route": "thread/goal/set -> first PreToolUse boundary",
            "user_prompt_submit_observed": False,
            "pre_reasoning_dispatch_claimed": False,
            "authority": "NATIVE_ACTIVE_TASK_GOAL_BINDING_VERIFIED",
            "exact_task_install_plan_pointer_match_required": True,
            "raw_goal_objective_stored": False,
            "synthetic_prompt_allowed": False,
            "fail_closed_on_mismatch": True,
        },
        "session_end_delivery": "BEST_EFFORT_HOST_CAPABILITY_GATED",
            "permission_request_policy": "OBSERVE_ONLY_NEVER_GRANT_OR_DENY",
            "unavailable_event_state": "HOST_CAPABILITY_UNAVAILABLE",
            "subagent_events_in_scope": True,
            "subagent_event_policy": "BOUND_OBSERVATION_ONLY_NEVER_CONTROL",
            "session_end_runs_for_subagents": False,
            "supported_handler_type": "command",
            "prompt_and_agent_handlers_parsed_but_skipped": True,
        "transport_envelope_schema": (
            "evidence-lane.codex-hook-transport-envelope.v1"
        ),
        "max_transport_bytes": 65_536,
        "max_visible_input_chars": 32_768,
        "full_plan_in_hook_payload": False,
        "linked_delta_json_in_hook_payload": False,
        "raw_prompt_or_tool_payload_in_boundary_receipts": False,
        "private_reasoning_stored": False,
    }
    hook_config = json.loads((PLUGIN / "hooks" / "hooks.json").read_text("utf-8"))
    assert list(hook_config["hooks"]) == contract["lifecycle_hook_matrix"][
        "required_events"
    ]
    assert "PermissionRequest" in hook_config["hooks"]
    assert {"SubagentStart", "SubagentStop"}.issubset(hook_config["hooks"])

    lifecycle = (
        PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md"
    ).read_text("utf-8")
    assert "ordinary tasks" in lifecycle
    assert "dependency-coherent integration checkpoints" in lifecycle

    behavior_sources = [
        PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md",
        PLUGIN / "skills" / "evi" / "SKILL.md",
        PLUGIN / "skills" / "evi-state-travel" / "SKILL.md",
        PLUGIN / "skills" / "evi-plan" / "SKILL.md",
    ]
    for source in behavior_sources:
        text = source.read_text("utf-8")
        assert "pv_status" in text
        assert "pv_task_backlog" in text
        assert "pv_query" in text
        assert "update_plan" in text

    explicit_behavior_skills = {
        "evi",
        "evi-state-travel",
        "evidence-lane-code-lifecycle",
    }
    for skill in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        if skill.parent.name in explicit_behavior_skills:
            continue
        text = skill.read_text("utf-8")
        assert (
            "../evidence-lane-code-lifecycle/references/shared-boundaries.md" in text
        )

    hook_text = "\n".join(
        (PLUGIN / "hooks" / name).read_text("utf-8")
        for name in (
            "prompt_submit.py",
            "session_start.py",
            "pre_tool_use.py",
            "post_tool_use.py",
            "lifecycle_boundary.py",
        )
    )
    assert "EVIDENCE_LANE_HOST_STEP_TASK_LIST_PROJECTION=" not in hook_text
    assert "EVIDENCE_LANE_HOST_PLAN_ACTION=" not in hook_text
    assert "CALL_UPDATE_PLAN_WITH_THE_EXACT_ROWS_BEFORE_ANY_OTHER_ACTION" not in hook_text
    assert "update_plan" not in hook_text


def test_stable_activation_requires_git_ci_authority_and_runtime_prewarm() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )

    gate = contract["stable_activation_gate"]
    assert gate["delivery_cadence"] == "DEPENDENCY_COHERENT_INTEGRATION_BUNDLES"
    assert gate["per_delta_commit_ci_install_forbidden"] is True
    assert gate["bundle_count_is_derived_not_quota"] is True
    assert gate["bundle_boundary_derivation_axes"] == [
        "SOURCE_SCHEMA_RUNTIME_COUPLING",
        "CROSS_DELTA_TEST_GRAPH",
        "INSTALLED_HOST_PROOF_BOUNDARY",
    ]
    assert gate["row_acceptance_evidence_remains_individual"] is True
    assert gate["per_delta_governance_routes_preserved"] == [
        "PREPARE_CAPTURE_RETRIEVAL",
        "TASK_ROW_CURRENT_CHANGE_CLASSIFICATION",
        "PV_STATUS_BACKLOG_QUERY_READS",
        "CHATLINEAGE_ACTIVITY",
        "EXACT_ACCEPTANCE_EVIDENCE",
        "LIFECYCLE_TRANSITION",
        "PERSISTENT_PLAN_CURRENT_CHANGE_REPROJECTION",
    ]
    assert gate["cross_delta_verification_matrix_required"] is True
    assert gate["cross_delta_matrix_dimensions"] == [
        "TASK_ID",
        "CHANGED_SURFACES",
        "LOCAL_TESTS",
        "REMOTE_CHECKS",
        "INSTALLED_HOST_CHECKS",
        "OUTCOME",
        "FAILURE_OWNER",
    ]
    assert gate["bundle_failure_policy"] == (
        "ANY_INCLUDED_ROW_FAILURE_FAILS_BUNDLE_CLOSED"
    )
    assert gate["bundle_commit_syncs_root_and_repository_docs"] is True
    assert gate["stable_install_source"] == "EXACT_GIT_MAIN_COMMIT_PACKAGE_ONLY"
    assert gate["local_or_dirty_worktree_stable_install_allowed"] is False
    assert gate["all_configured_commit_checks_required_before_stable_install"] is True
    assert gate["one_stable_update_per_integration_bundle"] is True

    assert contract["stable_activation_gate"] == {
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
        "release_authority_schema": (
            "evidence-lane.codex-git-ci-vercel-release-authority.v2"
        ),
        "exact_clean_commit_required": True,
        "governed_native_remote_push_required": True,
        "successful_github_ci_required": True,
        "successful_vercel_branch_preview_required": True,
        "production_deployment_allowed": False,
        "exact_commit_git_marketplace_required": True,
        "git_marketplace_name": "evidence-lane-github",
        "git_marketplace_display_name": "Main Git Plugin Version",
        "git_marketplace_source": "rathee000001/evidence_lane_plugin",
        "one_time_legacy_stable_selector_migration_allowed": True,
        "post_proof_obsolete_cleanup_required": True,
        "same_stable_selector_required_after_migration": True,
        "installed_runtime_prewarm_required": True,
        "runtime_ready_before_task_reopen_required": True,
        "fallback_activation_inferred": False,
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
        "bundle_failure_policy": (
            "ANY_INCLUDED_ROW_FAILURE_FAILS_BUNDLE_CLOSED"
        ),
        "bundle_commit_syncs_root_and_repository_docs": True,
        "stable_install_source": "EXACT_GIT_MAIN_COMMIT_PACKAGE_ONLY",
        "local_or_dirty_worktree_stable_install_allowed": False,
        "all_configured_commit_checks_required_before_stable_install": True,
        "one_stable_update_per_integration_bundle": True,
    }
    assert contract["brand_identity"] == {
        "display_name": "Evidence Lane",
        "icon_path": "assets/evidence-lane-icon.png",
        "icon_sha256": (
            "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3D"
            "EF87B8A129C4FA"
        ),
        "resource_uri": "ui://evidence-lane/governed-console-v6.html",
        "manifest_icon_fields": ["interface.composerIcon", "interface.logo"],
        "required_at_stage": True,
        "required_at_runtime_prewarm": True,
    }
