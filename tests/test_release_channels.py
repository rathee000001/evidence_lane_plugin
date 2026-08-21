from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def test_v300_declares_exact_three_role_maintainer_slots() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    stable = contract["stable"]
    branch_recovery = contract["branch_recovery"]
    local_testing = contract["local_testing"]
    live_slots = contract["live_slot_policy"]

    assert stable["release"] == "3.0.0"
    assert stable["slot_role"] == "main-git-release"
    assert stable["codex_marketplace_slot"] == "evidence-lane-github"
    assert stable["marketplace_display_name"] == "Main Git Plugin Version"
    assert stable["install_source"] == "GIT_EXACT_COMMIT"
    assert stable["native_tool_count"] == 88
    assert stable["skill_count"] == 17
    assert stable["direct_stdio_fallback_allowed"] is False

    assert branch_recovery == {
        "release": "3.0.0",
        "slot_role": "branch-commit-recovery",
        "codex_marketplace_slot": "evidence-lane-v300-stable-recovery",
        "marketplace_display_name": "Branch Commit Git Recovery",
        "install_source": "GOVERNED_BRANCH_COMMIT_EXACT_PACKAGE",
        "enabled": False,
        "update_gate": "LOCAL_TEST_GREEN_AND_GOVERNED_BRANCH_COMMIT",
        "byte_frozen_between_branch_checkpoints": True,
        "must_not_follow_uncommitted_local_bytes": True,
        "recover_mutable_local_test_failure": True,
        "simultaneous_mcp_allowed": False,
        "simultaneous_tunnel_allowed": False,
    }
    assert local_testing["release_line"] == "3.0.0"
    assert local_testing["slot_role"] == "mutable-local-testing"
    assert local_testing["codex_marketplace_slot"] == "evidence-lane-v300-testing-new"
    assert local_testing["helper_installs_plugin"] is False

    assert live_slots["exact_slot_count"] == 3
    assert live_slots["allowed_slots"] == [
        "main-git-release",
        "branch-commit-recovery",
        "mutable-local-testing",
    ]
    assert live_slots["max_enabled_plugin_count"] == 1
    assert live_slots["exact_registered_plugin_count"] == 3
    assert live_slots["stable_selector_growth_allowed"] is False
    assert live_slots["max_active_native_mcp_count"] == 1
    assert live_slots["max_active_tunnel_count"] == 1
    assert live_slots["inactive_slot_remains_installed"] is True
    assert live_slots["manual_loaded_cache_deletion_allowed"] is False


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
        "intermediate_main_merge_allowed": False,
        "intermediate_fallback_replacement_allowed": False,
        "physically_final_candidate": "PV14",
        "final_approve_required_before_main_or_fallback": True,
        "final_approve_effects": [
            "FUSE_PV14_GENERATION_14",
            "GOVERNED_NON_FORCE_MAIN_PROMOTION",
            "INSTALL_EXACT_ACCEPTED_3_0_IN_ENABLED_STABLE_SLOT",
            "INSTALL_EXACT_ACCEPTED_3_0_IN_DISABLED_RECOVERABLE_FALLBACK_SLOT",
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
        "ci_prerequisite_row": 196,
        "execution_row": 197,
        "release": "3.0.0",
        "branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
        "source": (
            "EXACT_GIT_COMMIT_AFTER_REQUIRED_CLEAN_CI_AND_"
            "GIT_TRIGGERED_VERCEL_PREVIEW"
        ),
        "slot_role": "branch-commit-recovery",
        "plugin_selector": "evidence-lane-plugin@evidence-lane-v300-stable-recovery",
        "byte_identical_local_testing_selector": (
            "evidence-lane-plugin@evidence-lane-v300-testing-new"
        ),
        "working_role_sync_required": True,
        "installed_version_must_equal_exact_package_version": True,
        "installed_catalog_must_equal": {
            "native_actions": 88,
            "read_actions": 27,
            "write_actions": 61,
            "governed_skills": 17,
            "hook_events": 8,
            "migrated_command_skills": 1,
        },
        "installed_ui_readback_required_before_pv13_hil": True,
        "main_git_release_slot_mutation_allowed": False,
        "main_merge_allowed": False,
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
        "exact_task_reopen_count": 1,
        "black_terminal_popup_allowed": False,
    }
    assert helper["user_stable_tunnel"]["release"] == "3.0.0"
    assert helper["user_stable_tunnel"]["release_token"] == "v300"
    assert helper["branch_recovery_transport"]["release"] == "3.0.0"
    assert rotation["applies_to_plugin_maintainer_route_only"] is True
    assert rotation["downstream_project_inherits_rotation"] is False
    assert rotation["local_test_green_branch_checkpoint_can_converge_all_three_slots"] is True
    assert rotation["pre_3_0_fallback_allowed"] is False
    assert rotation["final_gate"] == "PV14_EXACT_HUMAN_APPROVE_AND_FUSE"
    assert rotation["required_order"][-1] == (
        "VERIFY_THREE_BYTE_IDENTICAL_SLOTS_AND_ONE_ACTIVE_RUNTIME"
    )
    assert rotation["all_three_slots_must_equal_exact_checkpoint_release"] is True
    assert rotation["helper_and_tunnel_release_must_match_owning_slot"] is True
    assert rotation["prior_versioned_helpers_and_tunnels_retained"] is True
    assert rotation["prior_versioned_helpers_and_tunnels_disabled"] is True
    assert rotation["prior_versioned_helpers_and_tunnels_deleted"] is False
    assert rotation["repeat_for_each_later_plugin_release_cycle"] is True
    assert rotation["current_row_may_execute_rotation"] is False


def test_v210_logical_integration_bundle_matrix_is_complete_and_fail_closed() -> None:
    matrix = json.loads(
        (ROOT / "docs" / "V210_LOGICAL_INTEGRATION_BUNDLE_MATRIX.json").read_text(
            "utf-8"
        )
    )
    assert matrix["schema"] == (
        "evidence-lane.codex-logical-integration-bundle-matrix.v1"
    )
    assert matrix["delivery_cadence"] == (
        "DEPENDENCY_COHERENT_INTEGRATION_BUNDLE"
    )
    assert matrix["accepted_reference"] == {
        "pv": "PV11",
        "role": "IMMUTABLE_FORENSIC_REFERENCE_AND_DISABLED_FALLBACK",
        "development_base": False,
        "stable_install_source": False,
    }
    assert matrix["commit_authority"] == {
        "source": "EXACT_GIT_COMMIT_PACKAGE_ONLY",
        "final_commit_sha": None,
        "state": "PENDING_LOGICAL_BUNDLE_COMMIT",
        "local_or_dirty_worktree_install_allowed": False,
    }
    assert matrix["plan_projection"] == {
        "canonical_authority": "PLAN_LANE",
        "row_start": 81,
        "row_end": 200,
        "task_count": 120,
        "active_row": 164,
        "physically_final_hil_row": 200,
        "persistent_until": "NEXT_SIX_WAY_HIL_PRESENTED",
        "executable_projection_sha256": (
            "0968F63D7F2CE0093BCDFE98FD45AE9C9D3C23CB1F8BCBB8B05D4F684EF8070C"
        ),
    }
    rows = matrix["rows"]
    assert [row["task_id"] for row in rows] == [
        "EL-CODEX-TURN_PREPARE_CAPTURE-PROPOSAL-04",
        "EL-CODEX-TURN_CLASSIFY_DELTA_BIND-PROPOSAL-05",
        "EL-CODEX-CHATLINEAGE_SQLITE_FTS_HASHCHAIN-PROPOSAL-06",
        "EL-CODEX-GOAL_METRIC_PRIVATE_RESEARCH_METADATA-PROPOSAL-07",
        "EL-CODEX-GOVERNED_RETRIEVAL-PROPOSAL-08",
        "EL-CODEX-EXECUTION_EVENT_COMMIT-PROPOSAL-09",
        "EL-CODEX-ENTRY_EXIT_RECOVERY-PROPOSAL-10",
        "EL-CODEX-PERSISTENT_PLAN_CURRENT_CHANGE-PROPOSAL-11",
    ]
    required = {
        "task_id",
        "changed_surfaces",
        "local_tests",
        "remote_checks",
        "installed_host_checks",
        "outcome",
        "failure_owner",
    }
    assert all(set(row) == required for row in rows)
    assert all(
        row["changed_surfaces"]
        and row["local_tests"]
        and row["remote_checks"]
        and row["installed_host_checks"]
        and row["outcome"] == "LOCAL_PASS_PENDING_REMOTE_AND_INSTALLED_HOST"
        and row["failure_owner"]
        for row in rows
    )
    assert matrix["bundle_gate"]["policy"] == (
        "ANY_INCLUDED_ROW_FAILURE_FAILS_BUNDLE_CLOSED"
    )
    assert matrix["bundle_gate"]["local_outcome"] == "PASS_EXACT_TREE"
    assert matrix["bundle_gate"]["remote_outcome"] == "PENDING_EXACT_COMMIT"
    assert matrix["bundle_gate"]["installed_host_outcome"] == "PENDING_REMOTE_PASS"
    assert matrix["bundle_gate"]["stable_selector_updates"] == 0
    validation = matrix["local_validation"]
    assert validation["pytest"] == {
        "status": "PASS",
        "test_count": 440,
        "execution": "SIX_BOUNDED_SHARDS_AFTER_MONOLITHIC_15_MINUTE_TIMEOUT",
        "stale_test_correction": "CURRENT_EXECUTION_ASSERTS_GENERATED_PLAN_AUTHORITY",
    }
    assert validation["website_plan_projection"]["status"] == "PASS"
    assert validation["prompt_studio"]["sqlite_integrity"] == "ok"
    assert set(validation["adapter_checks"].values()) == {
        "PASS",
        "PASS_22_ROUTES",
    }
    assert validation["diff_check"] == "PASS"


def test_promotion_requires_matching_cross_surface_receipts_and_hil() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    promotion = contract["promotion_gate"]
    assert promotion["explicit_six_way_hil_required"] is True
    assert promotion["required_catalog"] == {
        "tools": 88,
        "read": 27,
        "write": 61,
        "skills": 17,
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
            "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        ),
        "host_managed_credentials_only": True,
        "main_push_allowed": False,
        "merge_allowed": False,
        "pull_request_acceptance_allowed": False,
        "force_push_allowed": False,
    }


def test_tunnel_version_history_is_append_only_and_hash_chained() -> None:
    manager = (
        PLUGIN / "scripts" / "windows_tunnel" / "Manage-EvidenceLaneTunnelVersions.ps1"
    ).read_text("utf-8")
    assert 'schema = "evidence-lane.tunnel-version-event.v1"' in manager
    assert "previous_event_sha256" in manager
    assert "event_sha256" in manager
    assert 'EventType "REGISTERED_SAVED_VERSION"' in manager
    assert 'EventType "ACTIVATION_STARTED"' in manager
    assert 'EventType "ACTIVATED"' in manager
    assert 'EventType "ACTIVATION_FAILED"' in manager
    assert 'EventType "ROLLBACK_ACTIVATED"' in manager


def test_three_slot_operator_is_bounded_and_rejects_transient_auto_failover() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    policy = contract["failover_operator"]
    operator = (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Switch-EvidenceLaneCodexSlot.ps1"
    ).read_text("utf-8")

    assert policy["registry_schema"] == "evidence-lane.codex-three-slot-registry.v1"
    assert policy["failure_target_slot"] == "branch-commit-recovery"
    assert policy["mutable_local_failure_never_targets_main_git"] is True
    assert policy["deterministic_failure_minimum_consecutive_probes"] == 3
    assert policy["deterministic_failure_minimum_window_seconds"] == 30
    assert policy["deterministic_failure_minimum_distinct_probe_types"] == 2
    assert policy["single_transient_error_switch_allowed"] is False
    assert policy["stop_source_tunnel_before_start_target"] is True
    assert policy["target_tunnel_ready_before_plugin_switch"] is True
    assert policy["switch_failure_restores_source_slot"] is True
    assert '"main-git-release"' in operator
    assert '"branch-commit-recovery"' in operator
    assert '"mutable-local-testing"' in operator
    assert "$ConsecutiveFailures -lt 3" in operator
    assert "$SampleWindowSeconds -lt 30" in operator
    assert "$DistinctProbeTypes -lt 2" in operator
    assert "$SingleTransientError" in operator
    assert 'Invoke-Tunnel -Slot $source -TunnelAction "Stop"' in operator
    assert 'Invoke-Tunnel -Slot $target -TunnelAction "Start"' in operator
    assert "Restart-EvidenceLaneCodex.ps1" in operator
    assert "Rolled back to $sourceSlot" in operator


def test_goal_recovery_is_one_general_read_only_logon_manager() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    policy = contract["goal_recovery"]

    assert policy == {
        "script": "scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1",
        "scope": (
            "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_"
            "ON_THIS_WINDOWS_USER"
        ),
        "trigger": "AT_LOGON_CURRENT_WINDOWS_USER",
        "exact_task_uuid_required": True,
        "exact_host_app_binding_required": True,
        "supported_host_app_ids": [
            "OpenAI.Codex_2p2nqsd0c76g0!App",
            "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
        ],
        "persisted_goal_read_route": (
            "CODEX_APP_SERVER_THREAD_READ_PLUS_THREAD_GOAL_GET"
        ),
        "thread_resume_writer_allowed": False,
        "synthetic_prompt_allowed": False,
        "turn_start_allowed": False,
        "state_travel_allowed": False,
        "candidate_hil_pointer_or_git_mutation_allowed": False,
        "restart_task_binding_refresh_order": (
            "TASK_BINDING_WRITE_THEN_GOAL_BINDING_REGISTER_THEN_APP_STOP"
        ),
        "stale_task_binding_seal_allowed": False,
        "requires_exactly_one_enabled_allowed_three_slot_selector": True,
        "allowed_runtime_selectors": [
            "evidence-lane-plugin@evidence-lane-github",
            "evidence-lane-plugin@evidence-lane-v300-stable-recovery",
            "evidence-lane-plugin@evidence-lane-v300-testing-new",
        ],
        "stable_selector_growth_allowed": False,
        "raw_goal_objective_stored": False,
    }


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
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PreCompact",
            "PostCompact",
            "Stop",
            "SessionEnd",
        ],
        "hook_transport_owners": {
            "SessionStart": "HOST_ENTRY_SIGNAL",
            "UserPromptSubmit": "VISIBLE_INPUT_SIGNAL",
            "PreToolUse": "PROSPECTIVE_TOOL_SIGNAL",
            "PostToolUse": "VISIBLE_TOOL_RESULT_SIGNAL",
            "PreCompact": "COMPACTION_SEAL_SIGNAL",
            "PostCompact": "COMPACTION_REENTRY_SIGNAL",
            "Stop": "VISIBLE_RESPONSE_STOP_SIGNAL",
            "SessionEnd": "SESSION_END_SIGNAL",
        },
        "skill_action_owners": {
            "SessionStart": "SKILL_BOOT_RESUME_OR_PANEL_REENTRY",
            "UserPromptSubmit": "SKILL_PREPARE_THEN_NATIVE_READ_SEQUENCE",
            "PreToolUse": "SKILL_BOUNDARY_AND_POLICY_OWNER",
            "PostToolUse": "SKILL_RECEIPT_AND_PLAN_REFRESH_OWNER",
            "PreCompact": "SKILL_CONTINUITY_SEAL_OWNER",
            "PostCompact": "SKILL_REBIND_AND_FULL_PLAN_REENTRY_OWNER",
            "Stop": "SKILL_IDEMPOTENT_COMMIT_OWNER",
            "SessionEnd": "SKILL_BEST_EFFORT_BOUNDARY_FLUSH_OWNER",
        },
        "goal_continuation_route": {
            "host_route": "thread/goal/set -> first PreToolUse boundary",
            "user_prompt_submit_observed": False,
            "pre_reasoning_dispatch_claimed": False,
            "authority": "SEALED_ACTIVE_GOAL_RECOVERY_BINDING",
            "exact_task_install_plan_pointer_match_required": True,
            "raw_goal_objective_stored": False,
            "synthetic_prompt_allowed": False,
            "fail_closed_on_mismatch": True,
        },
        "session_end_delivery": "BEST_EFFORT_HOST_CAPABILITY_GATED",
        "permission_request_policy": (
            "CONDITIONAL_ONLY_AFTER_EXPLICIT_HOST_CAPABILITY_PROOF"
        ),
        "unavailable_event_state": "HOST_CAPABILITY_UNAVAILABLE",
        "subagent_events_in_scope": False,
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
    assert "PermissionRequest" not in hook_config["hooks"]
    assert all("Subagent" not in event for event in hook_config["hooks"])

    lifecycle = (
        PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md"
    ).read_text("utf-8")
    assert "ordinary tasks" in lifecycle
    assert "dependency-coherent integration checkpoints" in lifecycle

    behavior_sources = [
        PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md",
        PLUGIN / "skills" / "evi" / "SKILL.md",
        PLUGIN / "skills" / "evi-state-travel" / "SKILL.md",
        PLUGIN / "commands" / "evi-plan.md",
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
        assert "../evidence-lane-code-lifecycle/SKILL.md" in skill.read_text("utf-8")

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
    assert gate["stable_install_source"] == "EXACT_GIT_COMMIT_PACKAGE_ONLY"
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
        "stable_update_helper": "scripts/codex_release/Restart-EvidenceLaneCodex.ps1",
        "install_completed_before_restart_helper": True,
        "restart_helper_installs_plugin": False,
        "stable_update_reopens_same_bound_host_app": True,
        "stable_update_rebinds_general_goal_recovery": True,
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
        "stable_install_source": "EXACT_GIT_COMMIT_PACKAGE_ONLY",
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
