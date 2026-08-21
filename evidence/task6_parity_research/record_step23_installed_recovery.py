"""Record Step 23 installed-runtime, slot, binding, and recovery evidence.

Only the separate Task6 research SQLite is written.  Plugin configuration,
installation receipts, task bindings, scheduled-helper artifacts, source, and
installed package bytes are read-only authorities.  No helper, tunnel, State
Travel, lifecycle, Goal, Plan, installer, or slot-switch action is invoked.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from research_db import (
    append_audit_event,
    connect,
    sha256_file,
    transition_step,
    upsert_fts,
    utc_now,
)


WORKSPACE = Path(r"F:\test codex")
PLUGIN = WORKSPACE / "plugins" / "evidence-lane-plugin"
INSTALL_ROOT = Path(r"C:\Users\rathe\EvidenceLanePV\installations\codex-v200")
CONFIG = Path(r"C:\Users\rathe\.codex\config.toml")
SLOT_REGISTRY = INSTALL_ROOT / "two-slot" / "CODEX_TWO_SLOT_REGISTRY.json"
CURRENT_INSTALL = INSTALL_ROOT / "CURRENT_INSTALLATION.json"
TEST_INSTALL_A = INSTALL_ROOT / "INSTALL_6153DD6156FF0CF1.json"
TEST_INSTALL_B = INSTALL_ROOT / "INSTALL_D44D1C8A9289B2DA.json"
SUPERSEDED_TEST_INSTALL = (
    INSTALL_ROOT
    / "correction-history"
    / "CURRENT_INSTALLATION.superseded-v220-78321F200F7F9D5DA0C8BF85324CD9B7C3136A9E1C1B3599479CA36003FDA931.json"
)
TASK_BINDINGS = INSTALL_ROOT / "task-bindings"
GOAL_ROOT = INSTALL_ROOT / "goal-recovery"
INSTALLED_GOAL_MANAGER = GOAL_ROOT / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
STALE_GOAL_BINDING = (
    GOAL_ROOT / "bindings" / "019ff25a-30f6-7382-993d-12c5979d696d.json"
)
STALE_GOAL_RECEIPT = (
    GOAL_ROOT
    / "receipts"
    / "RECOVERY_019ff25a-30f6-7382-993d-12c5979d696d_20260813T134245174Z.json"
)

SOURCE_INSTALLER = PLUGIN / "scripts" / "codex_release" / "install_codex_stable.py"
SOURCE_SWITCH = PLUGIN / "scripts" / "codex_release" / "Switch-EvidenceLaneCodexSlot.ps1"
SOURCE_GOAL_MANAGER = (
    PLUGIN / "scripts" / "codex_release" / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)
SOURCE_STATE_TRAVEL_SKILL = PLUGIN / "skills" / "evi-state-travel" / "SKILL.md"
SOURCE_STATE_TRAVEL_CONTRACT = (
    PLUGIN / "src" / "evidence_lane_plugin" / "state_travel_contract.py"
)
SOURCE_MCP = PLUGIN / "src" / "evidence_lane_plugin" / "mcp_server.py"

STABLE_ROOT = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-github"
    r"\evidence-lane-plugin\2.1.0+codex.20260812193232"
)
FALLBACK_ROOT = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-pv11-fallback"
    r"\evidence-lane-plugin\2.1.0+codex.20260812193232"
)
TEST_ROOT = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-v220-testing-new"
    r"\evidence-lane-plugin\2.2.0+codex.20260814082900"
)
STABLE_MANIFEST = STABLE_ROOT / ".codex-plugin" / "plugin.json"
FALLBACK_MANIFEST = FALLBACK_ROOT / ".codex-plugin" / "plugin.json"
TEST_MANIFEST = TEST_ROOT / ".codex-plugin" / "plugin.json"

TASK2_ID = "019ff25a-30f6-7382-993d-12c5979d696d"
TASK3_ID = "019ffc89-407e-7ae3-b45d-fca78391d656"
TASK6_ID = "01a0036f-32fa-79b2-8846-9c716d4fe777"
PROJECT_ID = "test-codex-evidence-lane-plugin"
SESSION_ID = "session_01kz48pm60mt58v5fyzqq6yq1g"
CURRENT_CONFIG_SHA256 = "194E5C6713E86ADC7266020F40049611C939B41A0DA8BCC50669FC9391461C77"

SCHEDULED_GOAL_RECOVERY_SNAPSHOT = {
    "task_name": "Evidence Lane Codex Goal Recovery",
    "state": "Ready",
    "enabled": True,
    "restart_count": 3,
    "restart_interval": "PT1M",
    "multiple_instances": "IgnoreNew",
    "start_when_available": True,
    "last_run_time_utc": "2026-08-13T13:42:16.0000000Z",
    "last_task_result": 1,
    "action": str(INSTALLED_GOAL_MANAGER),
}

TUNNEL_SCHEDULED_TASK_SNAPSHOT = {
    "EvidenceLane-Tunnel-v130": "Disabled",
    "EvidenceLane-Tunnel-v140": "Disabled",
    "EvidenceLane-Tunnel-v150": "Disabled",
    "EvidenceLane-Tunnel-v220-stable-build": "Disabled",
    "matching_live_process_count": 0,
}

# These bounded fields were returned by the active installed native server in
# this exact Task6 research turn.  The raw response is deliberately not stored.
LIVE_NATIVE_RUNTIME = {
    "server": "evidence-lane",
    "release": "2.1.0",
    "commit": "7b0274c90a0528746d0525c3a07920b87bcfd202",
    "tool_count": 62,
    "read_tool_count": 21,
    "write_tool_count": 41,
    "skill_count": 15,
    "runtime_doctor_status": "PASS",
    "env_uop_status": "PASS_LOCKED",
    "secret_persisted": False,
    "runtime_activation_status": "FAIL",
    "runtime_state": "ACTIVE",
    "task6_host_attached": True,
    "task6_bound_host_session_count": 20,
    "task6_indexed_visible_input_count": 0,
    "task6_validated_invocation_count": 0,
    "prompt_capture_active": False,
    "host_hook_registration_count": 8,
    "independent_host_dispatch_proven": False,
    "goal_continuation_host_capability": "UNAVAILABLE",
}

USER_RECOVERY_MANAGER_AUTHORITY = (
    "Goal recovery is one shared mutable manager for any number of governed projects. "
    "Each exact task invocation creates or refreshes its own sealed task binding; the "
    "manager must enumerate and isolate those bindings like the shared tunnel manager. "
    "A vanished Goal closes or tombstones only that task binding and cannot poison, retry, "
    "or prevent recovery of other valid project/task bindings."
)

USER_PREHIL_AUTHORITY = (
    "PV13 pre-HIL work performs per-Delta local tests, then one final governed Git "
    "commit and push, installs the exact committed package, waits for required Git CI "
    "PASS, and only then presents the fresh PV13 HIL. A failed v2.2 candidate must "
    "recover by disabling the failed candidate and returning to the verified stable or "
    "fallback slot without a restart, hook, installer, or helper loop."
)

USER_RESEARCH_TRANSITION_AUTHORITY = (
    "The separate parity research needs no acceptance gate. When its evidence and "
    "normalization finish, append only the verified real implementation Deltas once to "
    "the native Plan, refresh the fixed header plus active and next eight queued rows, "
    "then resume execution. The row count is evidence-derived, never estimated."
)


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def route(
    route_id: str,
    domain: str,
    capability: str,
    core_symbol: str | None,
    core_status: str,
    source_locator: str,
    *,
    mcp_tool: str | None = None,
    mcp_status: str = "ABSENT",
    sdk_module: str | None = None,
    sdk_operation: str | None = None,
    sdk_status: str = "ABSENT",
    skill_path: str | None = None,
    skill_status: str = "ABSENT",
    command_path: str | None = None,
    command_status: str = "ABSENT",
    installed_status: str = "ABSENT",
    runtime_status: str = "UNPROVEN",
    test_status: str = "NOT_EXECUTED_THIS_RESEARCH_PHASE",
    test_locators: str = "",
    gap_class: str = "NONE",
    decision: str = "RETAIN",
    proposed_delta_key: str | None = None,
    notes: str = "",
    now: str,
) -> tuple[object, ...]:
    return (
        route_id,
        domain,
        capability,
        core_symbol,
        core_status,
        source_locator,
        mcp_tool,
        mcp_status,
        sdk_module,
        sdk_operation,
        sdk_status,
        skill_path,
        skill_status,
        command_path,
        command_status,
        installed_status,
        runtime_status,
        test_status,
        test_locators,
        gap_class,
        decision,
        proposed_delta_key,
        notes,
        now,
    )


def authority_row(
    authority_id: str,
    kind: str,
    path: Path,
    freshness: str,
    role: str,
    notes: str,
    now: str,
) -> tuple[object, ...]:
    return (
        authority_id,
        kind,
        str(path),
        sha256_file(path),
        path.stat().st_size,
        freshness,
        role,
        notes,
        now,
    )


def direct_authority_row(
    authority_id: str,
    locator: str,
    role: str,
    body: str,
    now: str,
) -> tuple[object, ...]:
    encoded = body.encode("utf-8")
    return (
        authority_id,
        "direct_user_authority",
        locator,
        hashlib.sha256(encoded).hexdigest().upper(),
        len(encoded),
        "CURRENT_BINDING_STEER",
        role,
        body,
        now,
    )


def main() -> None:
    now = utc_now()
    config_sha = sha256_file(CONFIG)
    if config_sha != CURRENT_CONFIG_SHA256:
        raise RuntimeError(
            f"Config changed during Step23: expected {CURRENT_CONFIG_SHA256}, got {config_sha}"
        )
    with CONFIG.open("rb") as handle:
        config = tomllib.load(handle)
    plugins = config.get("plugins") or {}
    selectors = {
        selector: {
            "enabled": bool((plugins.get(selector) or {}).get("enabled")),
            "mcp_enabled": bool(
                (((plugins.get(selector) or {}).get("mcp_servers") or {}).get("evidence-lane") or {}).get("enabled")
            ),
        }
        for selector in (
            "evidence-lane-plugin@evidence-lane-github",
            "evidence-lane-plugin@evidence-lane-pv11-fallback",
            "evidence-lane-plugin@evidence-lane-v220-testing-new",
        )
    }

    registry = load_json(SLOT_REGISTRY)
    current_install = load_json(CURRENT_INSTALL)
    test_install_a = load_json(TEST_INSTALL_A)
    test_install_b = load_json(TEST_INSTALL_B)
    stale_goal_binding = load_json(STALE_GOAL_BINDING)
    stale_goal_receipt = load_json(STALE_GOAL_RECEIPT)
    task3_binding = load_json(TASK_BINDINGS / f"{TASK3_ID}.json")
    source_goal_text = SOURCE_GOAL_MANAGER.read_text(encoding="utf-8")
    installed_goal_text = INSTALLED_GOAL_MANAGER.read_text(encoding="utf-8")
    source_installer_text = SOURCE_INSTALLER.read_text(encoding="utf-8")
    source_switch_text = SOURCE_SWITCH.read_text(encoding="utf-8")
    source_state_travel_text = SOURCE_STATE_TRAVEL_SKILL.read_text(encoding="utf-8")
    source_mcp_text = SOURCE_MCP.read_text(encoding="utf-8")

    stable_manifest_sha = sha256_file(STABLE_MANIFEST)
    fallback_manifest_sha = sha256_file(FALLBACK_MANIFEST)
    test_manifest_sha = sha256_file(TEST_MANIFEST)
    task_binding_presence = {
        "task2": (TASK_BINDINGS / f"{TASK2_ID}.json").exists(),
        "task3": (TASK_BINDINGS / f"{TASK3_ID}.json").exists(),
        "task6": (TASK_BINDINGS / f"{TASK6_ID}.json").exists(),
    }

    registry_slots = registry.get("slots") or {}
    stable_slot = registry_slots.get("stable-build") or {}
    fallback_slot = registry_slots.get("fallback") or {}
    registry_config_sha = str((registry.get("activation_proof") or {}).get("config_sha256") or "")
    registry_task_id = str(registry.get("task_id") or "")
    registry_project_id = str(registry.get("project_id") or "")
    registry_session_id = str(registry.get("evidence_session_id") or "")
    source_multi_binding_loop = all(
        marker in source_goal_text
        for marker in (
            'Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "bindings"',
            'Get-ChildItem -LiteralPath $bindingDirectory -Filter "*.json" -File',
            "foreach ($record in $records)",
        )
    )
    source_registry_task_coupling = (
        '[string]$registry.task_id -ne [string]$Binding.task_id' in source_goal_text
    )
    source_failure_aggregates_exit_one = all(
        marker in source_goal_text
        for marker in ("$failures += 1", "if ($failures -gt 0) { exit 1 }")
    )
    source_unregister_is_explicit_only = (
        'if ($Action -eq "Unregister")' in source_goal_text
        and "CLOSED_PRESERVED_HISTORY" in source_goal_text
        and "SKIPPED_GOAL_NOT_ACTIVE" in source_goal_text
    )
    source_has_automatic_failed_candidate_rollback = all(
        marker in source_installer_text
        for marker in (
            "automatic_failed_candidate_rollback",
            "rollback_to_verified_stable_after_failure",
        )
    )
    source_test_reinstall_removes_target = (
        "target_removed_for_exact_reinstall" in source_installer_text
        and "accepted_two_slot_registry_mutated" in source_installer_text
    )
    source_test_exclusive_batch_write = (
        "config/batchWrite" in source_installer_text
        and "enabled_evidence_lane_count" in source_installer_text
    )
    source_switch_requires_sealed_prepare = all(
        marker in source_switch_text
        for marker in ("Prepare", "Switch", "Verify", "handoff", "TaskId", "HostSession")
    )
    forced_same_worktree_route_present = any(
        marker in (source_state_travel_text + "\n" + source_mcp_text).lower()
        for marker in (
            "forced_same_worktree",
            "forced same-worktree",
            "direct_forced_state_travel",
            "same_worktree_force_resume",
        )
    )

    last_test_activation = test_install_b.get("activation") or {}
    last_test_authority = test_install_b.get("activation_authority") or {}
    hook_trust = last_test_activation.get("hook_trust") or {}
    test_runtime_ready = bool(test_install_b.get("runtime_ready_before_task_reopen"))
    test_hook_trust_pending = str(hook_trust.get("status") or "") == "USER_TRUST_PENDING"
    test_selector_enabled_then = str(
        (last_test_activation.get("exclusive_channel") or {}).get("enabled_selector") or ""
    )
    current_install_version = str((current_install.get("plugin") or {}).get("version") or "")
    test_install_version = str((test_install_b.get("plugin") or {}).get("version") or "")
    repeated_test_installs_same_version = (
        str((test_install_a.get("plugin") or {}).get("version") or "") == test_install_version
        and sha256_file(TEST_INSTALL_A) != sha256_file(TEST_INSTALL_B)
    )
    correction_receipt_binds_current_config = CURRENT_CONFIG_SHA256 in SUPERSEDED_TEST_INSTALL.read_text(
        encoding="utf-8-sig"
    )

    stale_payload = stale_goal_binding.get("payload") or {}
    stale_goal_state = str(stale_payload.get("state") or "")
    stale_goal_task_id = str(stale_payload.get("task_id") or "")
    stale_goal_failure = str(stale_goal_receipt.get("failure") or "")
    four_attempt_mechanism_matches = (
        SCHEDULED_GOAL_RECOVERY_SNAPSHOT["restart_count"] == 3
        and SCHEDULED_GOAL_RECOVERY_SNAPSHOT["last_task_result"] == 1
        and stale_goal_state == "ACTIVE_GOAL_BOUND"
        and stale_goal_failure == "The exact Codex task has no persisted Goal."
    )

    routes = [
        route(
            "INSTALL-SLOT-AUTHORITY",
            "installation",
            "stable and fallback slot authority independent of task bindings",
            "Read-TwoSlotAuthority",
            "IMPLEMENTED_BUT_TASK_COUPLED_AND_STALE",
            f"{SLOT_REGISTRY};{SOURCE_GOAL_MANAGER};{SOURCE_SWITCH}",
            installed_status="STABLE_2_1_ENABLED_FALLBACK_DISABLED_TEST_DISABLED",
            runtime_status="LIVE_CONFIG_AND_REGISTRY_DIVERGE",
            test_status="LIVE_READ_ONLY_PROFILE",
            test_locators=f"{CONFIG};{SLOT_REGISTRY}",
            gap_class="SLOT_REGISTRY_TASK_COUPLING_AND_FRESHNESS_GAP",
            decision="SEPARATE_RELEASE_SLOT_AUTHORITY_FROM_PER_TASK_RECOVERY_BINDINGS",
            proposed_delta_key="DELTA-RELEASE-SLOT-REGISTRY-SEPARATION",
            notes=f"Registry task={registry_task_id}, project={registry_project_id}, session={registry_session_id}, config_sha_matches={registry_config_sha == config_sha}; Task6 has no registry identity. The shared recovery manager must bind many exact tasks without rewriting one install-level slot authority per task.",
            now=now,
        ),
        route(
            "INSTALL-LOCAL-TEST-TRANSACTION",
            "installation",
            "transactional candidate-slot activation with stable recovery retained",
            "install_codex_stable local-test route",
            "PARTIAL_NONTRANSACTIONAL",
            str(SOURCE_INSTALLER),
            command_path="install_codex_stable.py --local-test",
            command_status="PRESENT",
            installed_status="TWO_REPEATED_V2_2_RECEIPTS",
            runtime_status="CURRENTLY_MANUALLY_RETURNED_TO_STABLE_2_1",
            test_status="RECEIPT_AND_SOURCE_AUDIT",
            test_locators=f"{TEST_INSTALL_A};{TEST_INSTALL_B};{CONFIG}",
            gap_class="CANDIDATE_CHANNEL_TRANSACTION_GAP",
            decision="MAKE_TEST_ACTIVATION_COMPARE_AND_SWAP_WITH_DURABLE_ROLLBACK_RECEIPT",
            proposed_delta_key="DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION",
            notes=f"The local route removes/re-adds its selector and makes it exclusive while the accepted two-slot registry remains untouched. Repeated same-version installs={repeated_test_installs_same_version}.",
            now=now,
        ),
        route(
            "INSTALL-HOOK-TRUST-READINESS",
            "installation",
            "installed candidate readiness after host hook trust and runtime probe",
            "install_codex_stable activation receipt",
            "CONTRADICTORY_RECEIPT",
            str(TEST_INSTALL_B),
            installed_status="HOOK_TRUST_USER_PENDING_RUNTIME_READY_TRUE",
            runtime_status="CANDIDATE_DISABLED_NOW",
            test_status="RECEIPT_AUDIT",
            test_locators=str(TEST_INSTALL_B),
            gap_class="READINESS_GATE_GAP",
            decision="FAIL_CLOSED_UNTIL_TRUST_RESTART_AND_NATIVE_PROBE_PASS",
            proposed_delta_key="DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION",
            notes=f"hook_trust_pending={test_hook_trust_pending}; runtime_ready={test_runtime_ready}; prior_enabled_selector={test_selector_enabled_then}.",
            now=now,
        ),
        route(
            "INSTALL-FAILED-CANDIDATE-ROLLBACK",
            "installation",
            "automatic failed-candidate disable and verified stable/fallback recovery",
            None,
            "ABSENT",
            str(SOURCE_INSTALLER),
            command_path="install_codex_stable.py;Switch-EvidenceLaneCodexSlot.ps1",
            command_status="MANUAL_PATHS_ONLY",
            installed_status="NO_BOUND_ROLLBACK_RECEIPT",
            runtime_status="USER_MANUALLY_DISABLED_TEST_AND_RESTORED_STABLE",
            test_status="ABSENCE_AND_RECEIPT_AUDIT",
            test_locators=f"{SOURCE_INSTALLER};{SUPERSEDED_TEST_INSTALL};{CURRENT_INSTALL}",
            gap_class="AUTOMATIC_RECOVERY_GAP",
            decision="ADD_IDEMPOTENT_FAILURE_ROLLBACK_WITH_BACKOFF_AND_LAST_KNOWN_GOOD_PROBE",
            proposed_delta_key="DELTA-CANDIDATE-SELF-ROLLBACK",
            notes=f"No automatic rollback markers found={not source_has_automatic_failed_candidate_rollback}; no correction receipt binds current config={not correction_receipt_binds_current_config}.",
            now=now,
        ),
        route(
            "RECOVERY-GOAL-MULTI-BINDING-MANAGER",
            "goal_recovery",
            "one shared manager with isolated per-task mutable bindings across projects",
            "Manage-EvidenceLaneCodexGoalRecovery RecoverAtLogon",
            "MULTI_BINDING_LOOP_PRESENT_BACKING_AUTHORITY_INCOMPLETE",
            f"{SOURCE_GOAL_MANAGER};{INSTALLED_GOAL_MANAGER}",
            command_path="Manage-EvidenceLaneCodexGoalRecovery.ps1",
            command_status="PRESENT",
            installed_status="ONE_STALE_TASK2_BINDING_ONLY",
            runtime_status="SCHEDULED_READY_LAST_RESULT_1",
            test_status="SOURCE_INSTALLED_AND_SCHEDULER_READ_ONLY_AUDIT",
            test_locators=f"{STALE_GOAL_BINDING};{STALE_GOAL_RECEIPT}",
            gap_class="MULTI_BINDING_LIFECYCLE_AND_FAILURE_ISOLATION_GAP",
            decision="RETAIN_SHARED_MANAGER_FIX_BINDING_AUTHORITY_TOMBSTONE_AND_PER_BINDING_RESULT_ISOLATION",
            proposed_delta_key="DELTA-GOAL-RECOVERY-MULTI-BINDING",
            notes=f"Source enumerates many active binding files={source_multi_binding_loop}, but requires each to equal one singleton registry task={source_registry_task_coupling}; one stale failure makes the manager exit nonzero={source_failure_aggregates_exit_one}.",
            now=now,
        ),
        route(
            "RECOVERY-FOUR-ATTEMPT-CAUSE",
            "goal_recovery",
            "correlate scheduled retries, hooks, and task-open attempts without false attribution",
            "Windows Task Scheduler restart policy",
            "STRONG_ALTERNATIVE_CAUSE_NOT_CONCLUSIVE",
            "windows-scheduled-task://Evidence Lane Codex Goal Recovery",
            installed_status="INITIAL_RUN_PLUS_THREE_RETRIES_POSSIBLE",
            runtime_status="LAST_RESULT_1_STALE_GOAL_FAILURE",
            test_status="LIVE_READ_ONLY_SCHEDULER_PROFILE",
            test_locators=str(STALE_GOAL_RECEIPT),
            gap_class="EVENT_CORRELATION_GAP",
            decision="ADD_RUN_CORRELATION_ID_EVENT_OWNER_AND_NO_DUPLICATE_RECOVERY_DISPATCH",
            proposed_delta_key="DELTA-GOAL-RECOVERY-RETRY-ISOLATION",
            notes="RestartCount=3 plus the initial run can explain exactly four attempts. This is stronger than the unproven four-hook attribution, but exact timestamps/stderr are still required for causal proof.",
            now=now,
        ),
        route(
            "RECOVERY-SOURCE-INSTALLED-PARITY",
            "goal_recovery",
            "installed helper matches current release manager and active plugin catalog",
            "Manage-EvidenceLaneCodexGoalRecovery",
            "SOURCE_INSTALLED_DRIFT",
            f"{SOURCE_GOAL_MANAGER};{INSTALLED_GOAL_MANAGER}",
            installed_status="OLD_UNVERSIONED_MANAGER_2_1_EXPECTATIONS",
            runtime_status="ACTIVE_PLUGIN_2_1_62_TOOLS",
            test_status="SHA_AND_CONTRACT_COMPARISON",
            test_locators=f"{SOURCE_GOAL_MANAGER};{INSTALLED_GOAL_MANAGER}",
            gap_class="INSTALLED_HELPER_VERSION_DRIFT",
            decision="VERSION_MANAGER_AND_BINDINGS_AT_INSTALL_WITH_FORWARD_MIGRATION",
            proposed_delta_key="DELTA-GOAL-RECOVERY-INSTALLED-PARITY",
            notes=f"source_sha={sha256_file(SOURCE_GOAL_MANAGER)} installed_sha={sha256_file(INSTALLED_GOAL_MANAGER)}; equal={sha256_file(SOURCE_GOAL_MANAGER) == sha256_file(INSTALLED_GOAL_MANAGER)}.",
            now=now,
        ),
        route(
            "TASK6-EXACT-BINDING",
            "task_binding",
            "durable exact Task6 host/project/session/worktree binding",
            None,
            "MISSING",
            str(TASK_BINDINGS),
            installed_status="TASK3_PRESENT_TASK6_ABSENT",
            runtime_status="TASK6_HOST_ATTACHED_ONLY_IN_EPHEMERAL_RUNTIME_STATUS",
            test_status="FILE_PRESENCE_AND_NATIVE_RUNTIME_STATUS",
            test_locators=f"{TASK_BINDINGS};mcp://evidence-lane/runtime_activation_status",
            gap_class="DURABLE_CONTINUITY_BINDING_GAP",
            decision="CREATE_EXACT_TASK_BINDING_ONLY_AT_AUTHORIZED_TASK_ENTRY_AND_KEEP_STALE_IDS_FORBIDDEN",
            proposed_delta_key="DELTA-TASK-BINDING-EXACT-ATTACHMENT",
            notes=f"presence={task_binding_presence}; live runtime reports Task6 attached but the durable binding receipt is absent.",
            now=now,
        ),
        route(
            "STATE-TRAVEL-FORCED-SAME-WORKTREE",
            "state_travel",
            "deterministic direct/forced same-worktree continuation without a sealed handoff",
            None,
            "ABSENT",
            f"{SOURCE_STATE_TRAVEL_SKILL};{SOURCE_STATE_TRAVEL_CONTRACT};{SOURCE_MCP}",
            mcp_tool="pv_state_travel_prepare,pv_state_travel_resume",
            mcp_status="SEALED_PREPARE_RESUME_ONLY",
            skill_path="evi-state-travel/SKILL.md",
            skill_status="REQUIRES_PREPARED_HANDOFF",
            installed_status="NO_CALLABLE_FORCED_ROUTE_PROOF",
            runtime_status="NOT_INVOKED",
            test_status="SOURCE_AND_CATALOG_ABSENCE_AUDIT",
            test_locators=f"{SOURCE_STATE_TRAVEL_SKILL};{SOURCE_MCP}",
            gap_class="REQUESTED_PUBLIC_ROUTE_ABSENT",
            decision="IMPLEMENT_EXPLICIT_FAIL_CLOSED_SAME_WORKTREE_ROUTE_WITH_EXACT_IDENTITY_AND_DIRTY_SEAL",
            proposed_delta_key="DELTA-STATE-TRAVEL-FORCED-SAME-WORKTREE",
            notes=f"Exact forced-route marker present={forced_same_worktree_route_present}; current skill and MCP expose sealed prepare/resume only.",
            now=now,
        ),
        route(
            "TUNNEL-SHARED-MANAGER",
            "tunnel",
            "shared multi-project tunnel manager remains separate from per-task recovery bindings",
            "Manage-EvidenceLaneTunnelVersions",
            "PRESENT_NOT_ACTIVE",
            str(PLUGIN / "scripts" / "windows_tunnel" / "Manage-EvidenceLaneTunnelVersions.ps1"),
            command_path="Manage-EvidenceLaneTunnelVersions.ps1",
            command_status="PRESENT",
            installed_status="FOUR_VERSIONED_TASKS_DISABLED",
            runtime_status="NO_MATCHING_LIVE_PROCESS",
            test_status="LIVE_READ_ONLY_SCHEDULER_AND_PROCESS_PROFILE",
            test_locators="windows-scheduled-task://EvidenceLane-Tunnel-*",
            gap_class="NONE_CURRENT_RUNTIME",
            decision="RETAIN_SHARED_MANAGER_AND_KEEP_TASK_BINDING_SEPARATE",
            notes="Tunnel is not needed for the current local durable Evidence Lane runtime. Its shared multi-project lifecycle is a design analogy, not authority to activate it now.",
            now=now,
        ),
        route(
            "INSTALL-PREHIL-EXACT-COMMIT-CI",
            "release",
            "PV13 pre-HIL local tests, exact commit/push, exact install, CI PASS, then HIL",
            "install_codex_stable stable route",
            "PARTIAL",
            str(SOURCE_INSTALLER),
            command_path="install_codex_stable.py",
            command_status="EXACT_GIT_STABLE_ROUTE_PRESENT",
            installed_status="NOT_EXECUTED_RESEARCH_PHASE",
            runtime_status="PV12_POINTER_UNMOVED",
            test_status="USER_CONTRACT_RECORDED_ONLY",
            test_locators=f"codex://thread/{TASK6_ID}/prehil-contract",
            gap_class="END_TO_END_PREHIL_ORCHESTRATION_UNVERIFIED",
            decision="IMPLEMENT_AND_TEST_LATER_PER_NORMALIZED_DELTA_ORDER",
            proposed_delta_key="DELTA-PV13-PREHIL-RELEASE-CHAIN",
            notes=USER_PREHIL_AUTHORITY,
            now=now,
        ),
    ]

    findings = [
        (
            "GAP-TWO-SLOT-REGISTRY-LIVE-DRIFT",
            "installation",
            "CRITICAL",
            "INSTALL_AUTHORITY_AND_TASK_BINDING_COUPLED",
            f"The accepted two-slot registry is healthy as a PV12 stable/fallback byte authority but still binds Task2 and config SHA {registry_config_sha}; current Task6 config SHA is {config_sha} and includes a third disabled test selector.",
            f"{SLOT_REGISTRY};{CONFIG};{SOURCE_GOAL_MANAGER}",
            "A shared recovery manager cannot reliably serve multiple projects/tasks when every binding must equal one mutable singleton registry task identity.",
            "Separate immutable release/slot authority from a many-row task-binding registry; bind each task to the release authority by hash; allow multiple projects/tasks while keeping one enabled Evidence Lane channel.",
            "DELTA-RELEASE-SLOT-REGISTRY-SEPARATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-LOCAL-TEST-NONTRANSACTIONAL-CHANNEL-SWITCH",
            "installation",
            "CRITICAL",
            "TEST_SELECTOR_EXCLUSIVE_BEFORE_FULL_READINESS",
            "The v2.2 local-test route removes/re-adds the test selector and disables every other Evidence Lane selector through config/batchWrite while deliberately leaving the accepted two-slot registry unchanged.",
            f"{SOURCE_INSTALLER};{TEST_INSTALL_A};{TEST_INSTALL_B}",
            "A later trust, restart, or native-probe failure can leave the accepted stable/fallback channels disabled and the candidate as the only configured active route.",
            "Use a compare-and-swap transaction: preserve last-known-good stable activation, stage candidate disabled, prove trust/restart/native catalog, switch once, and write a rollback-capable receipt or restore the exact prior config atomically.",
            "DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-LOCAL-TEST-FALSE-RUNTIME-READY",
            "installation",
            "CRITICAL",
            "RECEIPT_CONTRADICTION",
            f"The latest v2.2 receipt records hook trust {hook_trust.get('status')} and runtime_ready_before_task_reopen={test_runtime_ready} in the same PASS installation.",
            str(TEST_INSTALL_B),
            "The host may reopen into an untrusted or undispatched hook runtime while the receipt claims readiness, masking the failure boundary.",
            "Make runtime readiness false until host trust, restart/reload, exact plugin/catalog identity, prompt capture, and bounded smoke probes all pass; distinguish installed, restart-required, trusted, active, and ready states.",
            "DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-FAILED-CANDIDATE-AUTOMATIC-ROLLBACK",
            "installation",
            "CRITICAL",
            "NO_SELF_RECOVERY_TRANSACTION",
            "No source contract or receipt proves that a failed local candidate disables itself and restores the verified stable/fallback activation without repeating installers, hooks, helpers, or restarts.",
            f"{SOURCE_INSTALLER};{SOURCE_SWITCH};{SUPERSEDED_TEST_INSTALL};{CURRENT_INSTALL}",
            "A broken candidate can strand the host or create an install/restart loop; manual plugin removal becomes the recovery mechanism.",
            "Add an idempotent failure controller with bounded attempts, per-attempt correlation IDs, automatic candidate disable, exact prior-config restore, stable health probe, optional byte-frozen fallback only under its law, and one terminal fail-closed receipt.",
            "DELTA-CANDIDATE-SELF-ROLLBACK",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-INSTALL-CURRENT-AUTHORITY-CORRECTION-RECEIPT",
            "installation",
            "HIGH",
            "LIVE_CONFIG_RECOVERY_NOT_RECEIPT_BOUND",
            f"Current config is back on stable 2.1, but CURRENT_INSTALLATION predates both v2.2 test receipts and correction-history only preserves the superseded v2.2 receipt; no recovery receipt binds config SHA {config_sha} to the restored stable state.",
            f"{CONFIG};{CURRENT_INSTALL};{SUPERSEDED_TEST_INSTALL}",
            "The operator can observe a recovered configuration but cannot prove which governed action restored it or whether caches, hooks, and bindings match.",
            "Every corrective slot/config action must atomically seal before/after config hashes, selectors, cache/plugin identities, hook trust, task bindings, and native runtime proof; CURRENT_INSTALLATION must point to that correction generation.",
            "DELTA-INSTALL-CORRECTION-RECEIPT",
            "OPEN",
            "MEDIUM_HIGH",
            now,
        ),
        (
            "GAP-GOAL-RECOVERY-MULTI-BINDING-LIFECYCLE",
            "goal_recovery",
            "CRITICAL",
            "SHARED_MANAGER_PARTIAL_MULTI_PROJECT_IMPLEMENTATION",
            "The manager correctly enumerates many per-task binding files, but each binding must match one registry project/session/task ID. The only installed active binding is stale Task2; Task6 has none.",
            f"{SOURCE_GOAL_MANAGER};{STALE_GOAL_BINDING};{SLOT_REGISTRY};{TASK_BINDINGS}",
            "The advertised all-governed-task manager cannot concurrently recover independent project/task bindings and cannot move cleanly when a task Goal ends.",
            USER_RECOVERY_MANAGER_AUTHORITY,
            "DELTA-GOAL-RECOVERY-MULTI-BINDING",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-GOAL-RECOVERY-FOUR-RETRY-LOOP",
            "goal_recovery",
            "CRITICAL",
            "STALE_BINDING_POISONS_SHARED_SCHEDULED_RUN",
            "The installed scheduled manager has RestartCount=3, one-minute interval, last result 1, and an ACTIVE_GOAL_BOUND Task2 binding whose latest receipt fails because the exact task has no persisted Goal.",
            f"windows-scheduled-task://Evidence Lane Codex Goal Recovery;{STALE_GOAL_BINDING};{STALE_GOAL_RECEIPT}",
            "One initial run plus three scheduler retries can produce exactly four visible attempts and can repeat at later logons; one stale task failure also makes the shared run fail for every other binding.",
            "Treat a missing/ended Goal as a terminal per-binding tombstone, never a shared-manager retry; isolate failures, preserve history, continue other bindings, apply bounded backoff, and return success when no recoverable binding remains.",
            "DELTA-GOAL-RECOVERY-RETRY-ISOLATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-RECOVERY-EVENT-CORRELATION",
            "hooks",
            "HIGH",
            "HOOK_HELPER_TASK_OPEN_ATTRIBUTION_AMBIGUOUS",
            "The observed four failures cannot be assigned to hooks from current evidence. Scheduler policy plus the stale Goal failure is a strong alternative cause, while hook event isolation/reentrancy gaps remain independently real.",
            f"{STALE_GOAL_RECEIPT};windows-scheduled-task://Evidence Lane Codex Goal Recovery;GAP-HOOK-EVENT-ISOLATION-LOOP",
            "Without event owner, attempt, task, boot, selector, and correlation IDs, hook loops, helper retries, installers, and task-open requests collapse into one ambiguous failure story.",
            "Give every host event and helper attempt a stable correlation envelope; enforce per-owner idempotency and kill switches; never infer hook causation from a restart count alone.",
            "DELTA-GOAL-RECOVERY-RETRY-ISOLATION",
            "OPEN",
            "MEDIUM_HIGH",
            now,
        ),
        (
            "GAP-GOAL-RECOVERY-SOURCE-INSTALLED-DRIFT",
            "goal_recovery",
            "HIGH",
            "VERSIONED_HELPER_NOT_LIVE",
            f"Source and installed Goal managers differ ({sha256_file(SOURCE_GOAL_MANAGER)} vs {sha256_file(INSTALLED_GOAL_MANAGER)}). Source defaults to release 2.2 and a versioned helper root/task; installed uses the older unversioned 2.1 location and binding.",
            f"{SOURCE_GOAL_MANAGER};{INSTALLED_GOAL_MANAGER}",
            "Source tests can pass while the enabled scheduled helper executes older lifecycle laws and catalog expectations.",
            "Package, install, migrate, verify, and retire helper versions transactionally; bindings must carry manager version and be migrated or tombstoned without deletion.",
            "DELTA-GOAL-RECOVERY-INSTALLED-PARITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-TASK6-EXACT-BINDING-MISSING",
            "task_binding",
            "CRITICAL",
            "EPHEMERAL_HOST_ATTACHMENT_WITHOUT_DURABLE_TASK_RECEIPT",
            "The active 2.1 runtime reports Task6 attached, but the durable task-bindings directory contains Task3 and no Task6 receipt; stale Task4/Task5 are absent as required.",
            f"{TASK_BINDINGS};mcp://evidence-lane/runtime_activation_status",
            "Goal recovery, State Travel, host rehydration, and install continuity cannot all prove the same Task6 identity after restart.",
            "At authorized entry, write one exact sealed Task6 binding covering UUID/deep link, project/session, worktree/dirty identity, accepted PV/generation, active row, plugin selector/version, and host session; never reuse stale task IDs.",
            "DELTA-TASK-BINDING-EXACT-ATTACHMENT",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-FORCED-SAME-WORKTREE-STATE-TRAVEL-ABSENT",
            "state_travel",
            "CRITICAL",
            "REQUESTED_DIRECT_ROUTE_NOT_PUBLICLY_IMPLEMENTED",
            "Current source and installed Evidence Lane expose sealed prepare/resume State Travel. No exact public skill, MCP action, command, or tested contract implements the user-defined direct/forced same-worktree route without a prepared handoff.",
            f"{SOURCE_STATE_TRAVEL_SKILL};{SOURCE_STATE_TRAVEL_CONTRACT};{SOURCE_MCP}",
            "An agent can either fabricate a receipt, violate the current sealed contract, or fail to continue when the host has the exact same dirty worktree but no eligible handoff.",
            "Implement a distinct explicitly named route that fail-closes on any UUID/deep-link/project/session/PV/active-row/plugin/host/dirty mismatch, consumes once, never replays HIL or moves the pointer, and is independently tested. Until then, do not claim the route exists.",
            "DELTA-STATE-TRAVEL-FORCED-SAME-WORKTREE",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-FALLBACK-SELECTOR-PV-IDENTITY-DRIFT",
            "installation",
            "MEDIUM",
            "STALE_PUBLIC_SELECTOR_NAME",
            f"The fallback selector name says pv11 while its registry slot carries accepted PV{fallback_slot.get('accepted_generation')} / {fallback_slot.get('accepted_pv')} and the same 2.1 manifest as stable.",
            f"{SLOT_REGISTRY};{FALLBACK_MANIFEST}",
            "Operators and automation can confuse selector labels with accepted authority and hard-code a stale PV generation.",
            "Make fallback identity generation-neutral or migrate aliases transactionally; always authorize by sealed manifest/package/PV fields rather than selector spelling.",
            "DELTA-FALLBACK-SELECTOR-AUTHORITY",
            "OPEN",
            "HIGH",
            now,
        ),
    ]

    tests = [
        (
            "TEST-INSTALLED-RUNTIME-DOCTOR-STEP23",
            "installed_runtime",
            "mcp://evidence-lane/runtime_doctor",
            "bounded live native runtime identity and ENV/UOP health",
            "LIVE_READ_ONLY_NATIVE",
            "PASS",
            "The enabled installed runtime is Evidence Lane 2.1, commit 7b0274c, 62 tools, 15 skills, ENV/UOP locked, and no secret persisted.",
            "Does not prove prompt capture, host dispatch, Task6 binding, v2.2 readiness, or candidate rollback.",
            now,
        ),
        (
            "TEST-RUNTIME-ACTIVATION-STEP23",
            "installed_runtime",
            "mcp://evidence-lane/runtime_activation_status",
            "bounded Task6 attachment and prompt-capture activation status",
            "LIVE_READ_ONLY_NATIVE",
            "FAIL_EXPECTED_GAPS_EXPOSED",
            "Task6 is attached to the active runtime and eight host hooks are registered.",
            "Prompt capture is inactive, independent host dispatch is unproven, no visible input is indexed, and Goal continuation capability is unavailable.",
            now,
        ),
        (
            "TEST-TWO-SLOT-CONFIG-PROFILE-STEP23",
            "installation",
            f"{CONFIG};{SLOT_REGISTRY}",
            "hash, selector enablement, registry identity, slot manifest, and config-freshness comparison",
            "LIVE_READ_ONLY",
            "PASS_DRIFT_CONFIRMED",
            "Stable is the only enabled runtime; fallback and test are disabled; stable/fallback bytes match; registry remains PV12.",
            "The registry task/config proof is stale and is not a current Task6 binding.",
            now,
        ),
        (
            "TEST-LOCAL-V220-RECEIPTS-STEP23",
            "installation",
            f"{TEST_INSTALL_A};{TEST_INSTALL_B};{SUPERSEDED_TEST_INSTALL}",
            "compare repeated v2.2 local-test install receipts and correction history",
            "READ_ONLY_RECEIPT_AUDIT",
            "PASS_CONTRADICTION_CONFIRMED",
            "Both receipts target the same v2.2 package and show exclusive test-channel activation with no credentials.",
            "They do not prove safe rollback; hook trust is pending while readiness is true; correction history does not bind the current restored config.",
            now,
        ),
        (
            "TEST-GOAL-RECOVERY-MULTI-BINDING-STEP23",
            "goal_recovery",
            f"{SOURCE_GOAL_MANAGER};{INSTALLED_GOAL_MANAGER};{STALE_GOAL_BINDING};{STALE_GOAL_RECEIPT}",
            "source/installed manager, binding loop, registry coupling, scheduler retry, and stale-Goal comparison",
            "READ_ONLY_SOURCE_AND_HOST_PROFILE",
            "PASS_GAPS_CONFIRMED",
            "The manager architecture enumerates multiple task bindings and keeps their files as history.",
            "It does not isolate a terminal stale binding, cannot serve task bindings that differ from the singleton registry task, and the installed helper is version-stale.",
            now,
        ),
        (
            "TEST-STATE-TRAVEL-FORCED-ROUTE-STEP23",
            "state_travel",
            f"{SOURCE_STATE_TRAVEL_SKILL};{SOURCE_STATE_TRAVEL_CONTRACT};{SOURCE_MCP}",
            "search public skill, MCP, and contract surface for an exact forced same-worktree route",
            "READ_ONLY_ABSENCE_AUDIT",
            "FAIL_ROUTE_ABSENT",
            "The sealed prepare/resume route remains explicit and fail-closed.",
            "No supported direct/forced no-handoff same-worktree route is exposed or tested.",
            now,
        ),
        (
            "TEST-TUNNEL-IDLE-STEP23",
            "tunnel",
            "windows-scheduled-task://EvidenceLane-Tunnel-*",
            "versioned scheduled-task state and matching process count",
            "LIVE_READ_ONLY_HOST_PROFILE",
            "PASS_IDLE",
            "All four versioned tunnel tasks are disabled and no matching process is active.",
            "Does not validate future tunnel upgrade/multi-project routing, which is outside this local-runtime research action.",
            now,
        ),
    ]

    authorities = [
        authority_row("AUTH-CODEX-CONFIG-STEP23", "live_host_config", CONFIG, "LIVE_READ_ONLY", "Current plugin selector and hook trust authority", json.dumps(selectors, sort_keys=True, separators=(",", ":")), now),
        authority_row("AUTH-TWO-SLOT-REGISTRY-STEP23", "installed_registry", SLOT_REGISTRY, "LIVE_READ_ONLY_STALE_TASK_BINDING", "Accepted stable/fallback byte authority", f"accepted_pv={registry.get('accepted_pv')}; task_id={registry_task_id}; config_sha={registry_config_sha}", now),
        authority_row("AUTH-CURRENT-INSTALL-STEP23", "installed_receipt", CURRENT_INSTALL, "CURRENT_POINTER_FILE_OLDER_THAN_TEST_RECEIPTS", "Current stable installation pointer", f"version={current_install_version}; receipt_sha={current_install.get('receipt_sha256')}", now),
        authority_row("AUTH-V220-INSTALL-A-STEP23", "installed_receipt", TEST_INSTALL_A, "SUPERSEDED_TEST_ATTEMPT", "First repeated v2.2 local-test install", f"version={(test_install_a.get('plugin') or {}).get('version')}", now),
        authority_row("AUTH-V220-INSTALL-B-STEP23", "installed_receipt", TEST_INSTALL_B, "SUPERSEDED_TEST_ATTEMPT", "Latest repeated v2.2 local-test install", f"version={test_install_version}; hook_trust={hook_trust.get('status')}; runtime_ready={test_runtime_ready}", now),
        authority_row("AUTH-V220-CORRECTION-HISTORY-STEP23", "installed_history", SUPERSEDED_TEST_INSTALL, "PRESERVED_SUPERSEDED_RECEIPT", "Correction-history copy of last v2.2 pointer", f"binds_current_config={correction_receipt_binds_current_config}", now),
        authority_row("AUTH-SOURCE-INSTALLER-STEP23", "project_source", SOURCE_INSTALLER, "DIRTY_WORKTREE_READ_ONLY", "Candidate/stable installation implementation", "Local-test and exact-Git stable paths", now),
        authority_row("AUTH-SOURCE-SLOT-SWITCH-STEP23", "project_source", SOURCE_SWITCH, "DIRTY_WORKTREE_READ_ONLY", "Explicit stable/fallback switch implementation", f"sealed_prepare_required={source_switch_requires_sealed_prepare}", now),
        authority_row("AUTH-SOURCE-GOAL-MANAGER-STEP23", "project_source", SOURCE_GOAL_MANAGER, "DIRTY_WORKTREE_READ_ONLY", "Current shared versioned Goal recovery manager", f"multi_binding_loop={source_multi_binding_loop}; singleton_registry_task_coupling={source_registry_task_coupling}", now),
        authority_row("AUTH-INSTALLED-GOAL-MANAGER-STEP23", "installed_helper", INSTALLED_GOAL_MANAGER, "LIVE_INSTALLED_OLDER_MANAGER", "Scheduled shared Goal recovery manager", f"scheduled={json.dumps(SCHEDULED_GOAL_RECOVERY_SNAPSHOT, sort_keys=True, separators=(',', ':'))}", now),
        authority_row("AUTH-STALE-GOAL-BINDING-STEP23", "installed_binding", STALE_GOAL_BINDING, "LIVE_STALE_ACTIVE", "Task2 per-task Goal recovery binding", f"state={stale_goal_state}; task={stale_goal_task_id}", now),
        authority_row("AUTH-STALE-GOAL-RECEIPT-STEP23", "installed_receipt", STALE_GOAL_RECEIPT, "LATEST_FAILED_CLOSED", "Task2 missing-Goal recovery evidence", f"failure={stale_goal_failure}", now),
        authority_row("AUTH-TASK3-BINDING-STEP23", "installed_binding", TASK_BINDINGS / f"{TASK3_ID}.json", "PRESENT_NOT_TASK6", "Existing exact Task3 binding comparison", f"state={task3_binding.get('state')}; install_receipt={task3_binding.get('install_receipt')}", now),
        authority_row("AUTH-SOURCE-STATE-TRAVEL-SKILL-STEP23", "project_skill", SOURCE_STATE_TRAVEL_SKILL, "DIRTY_WORKTREE_READ_ONLY", "Current State Travel user contract", "Prepared sealed handoff route only", now),
        authority_row("AUTH-SOURCE-STATE-TRAVEL-CONTRACT-STEP23", "project_source", SOURCE_STATE_TRAVEL_CONTRACT, "DIRTY_WORKTREE_READ_ONLY", "State Travel identity and consumption contract", "No direct/forced no-handoff route marker", now),
        authority_row("AUTH-STABLE-MANIFEST-STEP23", "installed_manifest", STABLE_MANIFEST, "LIVE_ENABLED", "Stable 2.1 plugin manifest", stable_manifest_sha, now),
        authority_row("AUTH-FALLBACK-MANIFEST-STEP23", "installed_manifest", FALLBACK_MANIFEST, "LIVE_DISABLED_BYTE_FROZEN", "Fallback 2.1 plugin manifest", fallback_manifest_sha, now),
        authority_row("AUTH-TEST-MANIFEST-STEP23", "installed_manifest", TEST_MANIFEST, "LIVE_DISABLED_SUPERSEDED", "Candidate 2.2 plugin manifest", test_manifest_sha, now),
        direct_authority_row("AUTH-TASK6-USER-RECOVERY-MANAGER", f"codex://thread/{TASK6_ID}/recovery-manager-correction", "Shared mutable multi-project manager with isolated exact-task bindings", USER_RECOVERY_MANAGER_AUTHORITY, now),
        direct_authority_row("AUTH-TASK6-USER-PREHIL-CHAIN", f"codex://thread/{TASK6_ID}/pv13-prehil-chain", "PV13 local-test, Git, exact-install, CI, and HIL order", USER_PREHIL_AUTHORITY, now),
        direct_authority_row("AUTH-TASK6-USER-RESEARCH-TRANSITION", f"codex://thread/{TASK6_ID}/research-auto-append", "No-acceptance research normalization and one native Plan append", USER_RESEARCH_TRANSITION_AUTHORITY, now),
    ]

    fts_rows = [
        (
            "FTS-STEP23-SLOTS",
            "research_finding",
            "Live slots, config, and installation authority drift",
            json.dumps(
                {
                    "config_sha256": config_sha,
                    "selectors": selectors,
                    "registry_sha256": sha256_file(SLOT_REGISTRY),
                    "registry_config_sha256": registry_config_sha,
                    "registry_task_id": registry_task_id,
                    "registry_accepted_pv": registry.get("accepted_pv"),
                    "stable_manifest_sha256": stable_manifest_sha,
                    "fallback_manifest_sha256": fallback_manifest_sha,
                    "test_manifest_sha256": test_manifest_sha,
                    "current_install_version": current_install_version,
                    "test_install_version": test_install_version,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "GAP-TWO-SLOT-REGISTRY-LIVE-DRIFT;GAP-INSTALL-CURRENT-AUTHORITY-CORRECTION-RECEIPT",
        ),
        (
            "FTS-STEP23-LOCAL-TEST-RECOVERY",
            "research_finding",
            "v2.2 local-test activation is not a rollback-safe transaction",
            f"repeated_same_version_installs={repeated_test_installs_same_version}; test_reinstall_removes_target={source_test_reinstall_removes_target}; exclusive_batch_write={source_test_exclusive_batch_write}; hook_trust={hook_trust.get('status')}; runtime_ready={test_runtime_ready}; automatic_failed_candidate_rollback={source_has_automatic_failed_candidate_rollback}; correction_receipt_binds_current_config={correction_receipt_binds_current_config}.",
            "GAP-LOCAL-TEST-NONTRANSACTIONAL-CHANNEL-SWITCH;GAP-LOCAL-TEST-FALSE-RUNTIME-READY;GAP-FAILED-CANDIDATE-AUTOMATIC-ROLLBACK",
        ),
        (
            "FTS-STEP23-GOAL-RECOVERY",
            "research_finding",
            "Shared Goal manager has partial multi-binding implementation",
            f"{USER_RECOVERY_MANAGER_AUTHORITY} source_multi_binding_loop={source_multi_binding_loop}; registry_task_coupling={source_registry_task_coupling}; failure_aggregates_exit_one={source_failure_aggregates_exit_one}; explicit_unregister_only={source_unregister_is_explicit_only}; installed_binding={stale_goal_task_id}:{stale_goal_state}; Task6_binding={task_binding_presence['task6']}.",
            "GAP-GOAL-RECOVERY-MULTI-BINDING-LIFECYCLE;GAP-GOAL-RECOVERY-FOUR-RETRY-LOOP;GAP-TASK6-EXACT-BINDING-MISSING",
        ),
        (
            "FTS-STEP23-FOUR-ATTEMPTS",
            "research_inference",
            "Four attempts align with scheduled Goal-recovery retries",
            f"scheduler={json.dumps(SCHEDULED_GOAL_RECOVERY_SNAPSHOT, sort_keys=True, separators=(',', ':'))}; stale_failure={stale_goal_failure}; mechanism_matches={four_attempt_mechanism_matches}. This is a strong alternative cause, not conclusive attribution; matching timestamps and stderr would settle it.",
            "GAP-GOAL-RECOVERY-FOUR-RETRY-LOOP;GAP-RECOVERY-EVENT-CORRELATION",
        ),
        (
            "FTS-STEP23-STATE-TRAVEL",
            "research_finding",
            "Forced same-worktree State Travel route is absent",
            f"forced_route_present={forced_same_worktree_route_present}; current public route requires sealed pv_state_travel_prepare then one pv_state_travel_resume. The requested forced route must be implemented and tested as a distinct fail-closed contract; it cannot be inferred.",
            "GAP-FORCED-SAME-WORKTREE-STATE-TRAVEL-ABSENT",
        ),
        (
            "FTS-STEP23-RUNTIME",
            "live_native_read",
            "Installed 2.1 runtime identity and Task6 activation gaps",
            json.dumps(LIVE_NATIVE_RUNTIME, sort_keys=True, separators=(",", ":")),
            "GAP-TASK6-EXACT-BINDING-MISSING;GAP-HOOK-INSTALLED-RUNTIME",
        ),
        (
            "FTS-STEP23-PREHIL",
            "direct_user_contract",
            "PV13 pre-HIL exact release chain and candidate recovery",
            USER_PREHIL_AUTHORITY,
            "DELTA-PV13-PREHIL-RELEASE-CHAIN;DELTA-CANDIDATE-SELF-ROLLBACK",
        ),
    ]

    with connect() as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO capability_route VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            routes,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO gap_finding VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            findings,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO test_evidence VALUES (?,?,?,?,?,?,?,?,?)",
            tests,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            authorities,
        )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-LIVE-NATIVE-RUNTIME-STEP23",
                "native_server_read",
                "mcp://evidence-lane/runtime_doctor+runtime_activation_status",
                None,
                None,
                "LIVE_BOUNDED_READ_2026-08-15",
                "Enabled installed runtime and Task6 attachment proof",
                json.dumps(LIVE_NATIVE_RUNTIME, sort_keys=True, separators=(",", ":")),
                now,
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-SCHEDULED-GOAL-RECOVERY-STEP23",
                "windows_scheduled_task_read",
                "windows-scheduled-task://Evidence Lane Codex Goal Recovery",
                None,
                None,
                "LIVE_READ_ONLY_2026-08-15",
                "Shared manager retry and last-result proof",
                json.dumps(SCHEDULED_GOAL_RECOVERY_SNAPSHOT, sort_keys=True, separators=(",", ":")),
                now,
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-TUNNEL-TASKS-STEP23",
                "windows_scheduled_task_read",
                "windows-scheduled-task://EvidenceLane-Tunnel-*",
                None,
                None,
                "LIVE_READ_ONLY_2026-08-15",
                "Current idle tunnel-runtime boundary",
                json.dumps(TUNNEL_SCHEDULED_TASK_SNAPSHOT, sort_keys=True, separators=(",", ":")),
                now,
            ),
        )
        for doc_id, doc_type, title, body, locator in fts_rows:
            upsert_fts(
                connection,
                doc_id=doc_id,
                doc_type=doc_type,
                title=title,
                body=body,
                evidence_locator=locator,
            )
        transition_step(
            connection,
            step_no=23,
            to_status="completed",
            evidence_locator="FTS-STEP23-SLOTS;FTS-STEP23-LOCAL-TEST-RECOVERY;FTS-STEP23-GOAL-RECOVERY;FTS-STEP23-FOUR-ATTEMPTS;FTS-STEP23-STATE-TRAVEL;FTS-STEP23-RUNTIME",
            event_id="EVT-STEP23-INSTALLED-RECOVERY-COMPLETE",
            occurred_at=now,
        )
        transition_step(
            connection,
            step_no=24,
            to_status="in_progress",
            evidence_locator="Official plugin conformance and full local test parity matrix",
            event_id="EVT-STEP24-TEST-PARITY-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEP23-INSTALLED-RECOVERY",
            event_type="RESEARCH_STEP_COMPLETED",
            payload={
                "step": 23,
                "config_sha256": config_sha,
                "selectors": selectors,
                "slot_registry_sha256": sha256_file(SLOT_REGISTRY),
                "slot_registry_task_id": registry_task_id,
                "slot_registry_project_id": registry_project_id,
                "slot_registry_session_id": registry_session_id,
                "slot_registry_config_sha256": registry_config_sha,
                "stable_manifest_sha256": stable_manifest_sha,
                "fallback_manifest_sha256": fallback_manifest_sha,
                "test_manifest_sha256": test_manifest_sha,
                "task_binding_presence": task_binding_presence,
                "source_multi_binding_loop": source_multi_binding_loop,
                "source_registry_task_coupling": source_registry_task_coupling,
                "source_failure_aggregates_exit_one": source_failure_aggregates_exit_one,
                "scheduled_goal_recovery": SCHEDULED_GOAL_RECOVERY_SNAPSHOT,
                "four_attempt_mechanism_matches": four_attempt_mechanism_matches,
                "four_attempt_causal_attribution": "UNPROVEN_WITH_STRONG_SCHEDULED_HELPER_ALTERNATIVE",
                "live_native_runtime": LIVE_NATIVE_RUNTIME,
                "tunnel_runtime": TUNNEL_SCHEDULED_TASK_SNAPSHOT,
                "forced_same_worktree_route_present": forced_same_worktree_route_present,
                "credential_value_observed_or_stored": False,
                "canonical_plan_mutated": False,
                "native_goal_mutated": False,
                "helper_or_tunnel_invoked": False,
                "state_travel_invoked": False,
            },
            occurred_at=now,
        )
        check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if check != "ok":
            raise RuntimeError(f"quick_check failed: {check}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "completed_step": 23,
                "in_progress_step": 24,
                "live_runtime": {
                    "release": LIVE_NATIVE_RUNTIME["release"],
                    "tools": LIVE_NATIVE_RUNTIME["tool_count"],
                    "skills": LIVE_NATIVE_RUNTIME["skill_count"],
                    "doctor": LIVE_NATIVE_RUNTIME["runtime_doctor_status"],
                    "activation": LIVE_NATIVE_RUNTIME["runtime_activation_status"],
                },
                "selectors": selectors,
                "registry_task_id": registry_task_id,
                "Task6_binding_present": task_binding_presence["task6"],
                "shared_manager_multi_binding_loop": source_multi_binding_loop,
                "shared_manager_registry_task_coupled": source_registry_task_coupling,
                "stale_binding": f"{stale_goal_task_id}:{stale_goal_state}",
                "scheduled_restart_count": SCHEDULED_GOAL_RECOVERY_SNAPSHOT["restart_count"],
                "four_attempt_mechanism_matches": four_attempt_mechanism_matches,
                "four_attempt_causation": "NOT_CONCLUSIVE",
                "forced_same_worktree_route_present": forced_same_worktree_route_present,
                "tunnel_active": False,
                "credential_value_observed_or_stored": False,
                "canonical_plan_mutated": False,
                "native_goal_mutated": False,
                "helper_or_tunnel_invoked": False,
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
