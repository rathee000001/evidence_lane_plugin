from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def test_v210_declares_exact_stable_build_and_v200_pv11_fallback_slots() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    stable = contract["stable"]
    fallback = contract["fallback"]
    live_slots = contract["live_slot_policy"]

    assert stable == {
        "release": "2.1.0",
        "slot_role": "stable-build",
        "codex_marketplace_slot": "evidence-lane-github",
        "marketplace_display_name": "GitLane Stable 2.1",
        "install_source": "GIT_EXACT_COMMIT",
        "enabled": True,
        "byte_frozen": False,
        "updates_require_verified_unique_build_identity": True,
        "stable_selector_is_persistent": True,
        "stable_updates_reinstall_in_place": True,
        "build_identity_is_receipt_not_selector": True,
        "native_server_identity": "evidence-lane",
        "native_tool_count": 62,
        "native_read_tool_count": 21,
        "native_write_tool_count": 41,
        "skill_count": 15,
        "codex_apps_allowed": False,
        "generated_namespace_allowed": False,
        "direct_stdio_fallback_allowed": False,
        "google_drive_bundled": False,
        "tunnel_release_must_match": True,
        "tunnel_channel": "stable-build",
    }
    assert fallback == {
        "release": "2.0.0",
        "slot_role": "fallback",
        "codex_marketplace_slot": "evidence-lane-pv11-fallback",
        "enabled": False,
        "materialization_gate": "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE",
        "accepted_pv": "PV11",
        "accepted_generation": 11,
        "byte_frozen": True,
        "package_must_equal_accepted_pv": True,
        "prewarmed_means_installed_verified_and_stopped": True,
        "simultaneous_mcp_allowed": False,
        "simultaneous_tunnel_allowed": False,
        "tunnel_channel": "fallback",
    }
    assert live_slots["exact_slot_count_after_pv11_acceptance"] == 2
    assert live_slots["allowed_slots"] == ["stable-build", "fallback"]
    assert live_slots["max_enabled_plugin_count"] == 1
    assert live_slots["exact_registered_plugin_count"] == 2
    assert live_slots["stable_selector_growth_allowed"] is False
    assert live_slots["max_active_native_mcp_count"] == 1
    assert live_slots["max_active_tunnel_count"] == 1
    assert live_slots["inactive_slot_remains_installed"] is True
    assert live_slots["manual_loaded_cache_deletion_allowed"] is False


def test_promotion_requires_matching_cross_surface_receipts_and_hil() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    promotion = contract["promotion_gate"]
    assert promotion["explicit_six_way_hil_required"] is True
    assert promotion["required_catalog"] == {
        "tools": 62,
        "read": 21,
        "write": 41,
        "skills": 15,
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
        "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        "tunnel_key_retention": "HOST_MANAGED_PERSISTENT_PROFILE",
    }
    assert matrix["interactive_codex_app_ephemeral_vm"] == {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
    }


def test_v200_remote_git_policy_supersedes_only_historical_flash_sentence() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    policy = contract["remote_git_policy"]

    assert policy == {
        "effective_release": "2.1.0",
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


def test_two_slot_operator_is_bounded_and_rejects_transient_auto_failover() -> None:
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

    assert policy["registry_schema"] == "evidence-lane.codex-two-slot-registry.v1"
    assert policy["deterministic_failure_minimum_consecutive_probes"] == 3
    assert policy["deterministic_failure_minimum_window_seconds"] == 30
    assert policy["deterministic_failure_minimum_distinct_probe_types"] == 2
    assert policy["single_transient_error_switch_allowed"] is False
    assert policy["stop_source_tunnel_before_start_target"] is True
    assert policy["target_tunnel_ready_before_plugin_switch"] is True
    assert policy["switch_failure_restores_source_slot"] is True
    assert '"stable-build", "fallback"' in operator
    assert "consecutive_failures -lt 3" in operator
    assert "sample_window_seconds -lt 30" in operator
    assert "distinct_probe_types -lt 2" in operator
    assert "single_transient_error -ne $false" in operator
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
        "requires_stable_enabled_fallback_disabled": True,
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
        "fail_closed_when_behavior_route_unavailable": True,
    }

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
        for name in ("prompt_submit.py", "session_start.py", "post_tool_use.py")
    )
    assert "EVIDENCE_LANE_HOST_STEP_TASK_LIST_PROJECTION=" not in hook_text
    assert "EVIDENCE_LANE_HOST_PLAN_ACTION=" not in hook_text
    assert "CALL_UPDATE_PLAN_WITH_THE_EXACT_ROWS_BEFORE_ANY_OTHER_ACTION" not in hook_text
    assert "update_plan" not in hook_text


def test_stable_activation_requires_git_ci_authority_and_runtime_prewarm() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )

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
        "stable_update_helper": (
            "scripts/codex_release/Update-EvidenceLaneCodexStableAndResume.ps1"
        ),
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
        "git_marketplace_display_name": "GitLane Stable 2.1",
        "git_marketplace_source": "rathee000001/evidence_lane_plugin",
        "one_time_legacy_stable_selector_migration_allowed": True,
        "post_proof_obsolete_cleanup_required": True,
        "same_stable_selector_required_after_migration": True,
        "installed_runtime_prewarm_required": True,
        "runtime_ready_before_task_reopen_required": True,
        "fallback_activation_inferred": False,
    }
    assert contract["brand_identity"] == {
        "display_name": "Evidence Lane",
        "icon_path": "assets/evidence-lane-icon.png",
        "icon_sha256": (
            "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3D"
            "EF87B8A129C4FA"
        ),
        "resource_uri": "ui://evidence-lane/governed-console-v3.html",
        "manifest_icon_fields": ["interface.composerIcon", "interface.logo"],
        "required_at_stage": True,
        "required_at_runtime_prewarm": True,
    }
