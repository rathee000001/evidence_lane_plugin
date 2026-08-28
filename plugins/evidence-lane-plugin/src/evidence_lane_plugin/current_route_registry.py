"""Single installed-version registry for current cross-surface implementations."""

from __future__ import annotations

import hashlib
import json
from typing import Any

CURRENT_IMPLEMENTATION_REGISTRY_SCHEMA = (
    "evidence-lane.current-implementation-registry.v1"
)

_COMMON_CONSUMERS = (
    "PUBLIC_SCHEMA",
    "MCP",
    "OUTER_MCP_SCHEMA_ROUTER",
    "INTERNAL_SDK",
    "SERVICE",
    "SKILL",
    "COMMAND_OR_PROMPT_INTENT",
    "HOOK",
    "REMOTE_ADAPTER",
    "PACKAGE",
    "INSTALLED_HOST",
)


def _route(
    capability: str,
    current_route: str,
    owner: str,
    *,
    schema: str,
    public_tools: tuple[str, ...] = (),
    plan_families: tuple[str, ...] = (),
    consumers: tuple[str, ...] = _COMMON_CONSUMERS,
    verification: tuple[str, ...] = (),
    implementation_class: str = "INTERNAL_SDK_IMPLEMENTATION",
    outer_business_logic_allowed: bool = False,
) -> dict[str, Any]:
    return {
        "capability": capability,
        "status": "CURRENT_ROUTE",
        "current_route": current_route,
        "owner": owner,
        "schema": schema,
        "public_tools": list(public_tools),
        "plan_families": list(plan_families),
        "consumers": list(consumers),
        "verification": list(verification),
        "sdk_governance": {
            "implementation_class": implementation_class,
            "behavior_owner": "INTERNAL_SDK",
            "outer_business_logic_allowed": outer_business_logic_allowed,
            "outer_adapter_may_reason_about_lifecycle": False,
        },
    }


_ROUTES = (
    _route(
        "runtime_boot_and_attachment",
        "ATOMIC_DOCTOR_FLASH_SESSION_ATTACH_ACTIVATION",
        "evi-boot",
        schema="evidence-lane.atomic-boot-receipt.v2",
        public_tools=(
            "project_register",
            "pv_enroll_project",
            "runtime_doctor",
            "session_flash_status",
            "session_boot",
            "session_resume",
            "runtime_activation_status",
            "session_close",
        ),
        plan_families=("ENV_UOP", "RUNTIME_ATTACHMENT", "SESSION_LIFECYCLE"),
        verification=("ENV_UOP_ATTESTED", "EXACT_TASK_SESSION_PROJECT_BINDING"),
    ),
    _route(
        "project_bootstrap",
        "SOURCE_INTAKE_BUILD_PV0_NO_HIL",
        "evi-build",
        schema="evidence-lane.pv0-build-bootstrap.v1",
        public_tools=("pv_build_initial",),
        plan_families=("PROJECT_BOOTSTRAP", "SOURCE_INTAKE"),
        verification=(
            "ALL_SECTOR_LANES_MATERIALIZED",
            "NO_CANDIDATE_HIL_OVERLAY_OR_ACCEPTED_ARTIFACT",
        ),
    ),
    _route(
        "model_tool_compatibility",
        "CAPABILITY_AND_INSTALLED_PROFILE_QUALIFICATION",
        "evi-boot",
        schema="evidence-lane.model-compatibility.v1",
        plan_families=("RUNTIME_ATTACHMENT", "PUBLIC_SURFACE"),
        verification=(
            "OFFICIAL_MODEL_TOOL_CONTRACT",
            "INSTALLED_HOST_PROFILE_PROOF",
            "REASONING_EFFORT_AND_SPEED_MATRIX",
            "STATE_TRAVEL_EXACT_PROFILE_REPLAY",
            "CHATGPT_SURFACE_HIDDEN",
        ),
    ),
    _route(
        "governance_and_live_root_reads",
        "LIVE_ROOT_POINTER_BASELINE_BOUNDED_GOVERNANCE_READS",
        "evi",
        schema="evidence-lane.live-root-governance-query.v1",
        public_tools=(
            "pv_status",
            "pv_summary",
            "pv_diff",
            "pv_query",
            "lifecycle_transition_law",
        ),
        plan_families=("LIVE_ROOT_QUERY", "POINTER", "BOUNDED_OUTPUT"),
        verification=("ACCEPTED_POINTER_BASELINE_ONLY", "BOUNDED_PUBLIC_RESULT"),
    ),
    _route(
        "source_intake_and_graph_registration",
        "GENERALIZED_SOURCE_INTAKE_IDENTITY_GRAPH_PIPELINE",
        "evi-source-intake",
        schema="evidence-lane.source-intake.v2",
        public_tools=(
            "source_intake_classify",
            "source_identity_register",
            "source_sqlite_inspect",
            "source_git_history_build",
            "source_git_commit_impact",
            "source_custom_schema_compile",
            "source_graph_build",
            "source_graph_diff",
            "source_graph_impact",
            "source_intake_schema_configure",
        ),
        plan_families=("SOURCE_INTAKE", "CHATLINEAGE", "SOURCE_GRAPH"),
        verification=("CHATLINEAGE_ALWAYS_APPENDED", "HOOKS_OFF_EXPLICIT_ACTION"),
    ),
    _route(
        "code_source_and_study_brain_routing",
        "ONE_PRIMARY_CODE_PROJECT_PLUS_LANE_SCOPED_STUDY_BRAINS",
        "evi-source-intake",
        schema="evidence-lane.code-source-routing-batch.v1",
        plan_families=("SOURCE_INTAKE", "LANES", "GIT"),
        consumers=(
            "PUBLIC_SCHEMA",
            "MCP",
            "INTERNAL_SDK",
            "SERVICE",
            "SKILL",
            "COMMAND_OR_PROMPT_INTENT",
            "PACKAGE",
            "INSTALLED_HOST",
        ),
        verification=(
            "PUBLIC_UNOWNED_GIT_HISTORY_DISABLED",
            "ADDITIONAL_LOCAL_CODE_IS_LANE_STUDY_BRAIN",
            "PRIMARY_CHANGE_REQUIRES_NEW_PROJECT_PV",
            "BUILD_REFRESH_ARTIFACT_OWNERSHIP",
        ),
    ),
    _route(
        "live_sector_query",
        "EVIDENCE_LANE_LIVE_ROOT_SIX_AUTHORITY_QUERY_V1",
        "evi-source-intake",
        schema="evidence-lane.live-root-env-uop-six-way-query.v1",
        public_tools=(
            "lane_catalog",
            "lane_configure_routes",
            "lane_status",
            "prompt_index_status",
            "search",
            "lane_search",
            "fetch",
            "lane_fetch",
        ),
        plan_families=("LANES", "RETRIEVAL", "SIX_AUTHORITY"),
        verification=("18_SECTOR_LANES", "ENV_UOP_AUTHORITY_ROLE_SEPARATION"),
    ),
    _route(
        "public_actions",
        "CANONICAL_PUBLIC_ACTION_SCHEMA_CATALOG_V2",
        "public_action_schema_generator",
        schema="evidence-lane.public-action-schema-catalog.v2",
        plan_families=("PUBLIC_SURFACE", "MCP_SDK_SKILL_COMMAND"),
        verification=("88_ACTION_HOOKS_OFF_MATRIX", "MODEL_VISIBLE_SCHEMA_PARITY"),
    ),
    _route(
        "prompt_steer_dispatch",
        "SOURCE_INTAKE_ENV_UOP_CHATLINEAGE_THEN_IDEMPOTENT_PLAN_STEER",
        "source_intake_and_plan_runtime",
        schema="evidence-lane.prompt-steer-dispatch.v1",
        public_tools=("pv_plan_steer_delta",),
        plan_families=("PLAN", "PROMPT_STEER", "CHATLINEAGE"),
        verification=("QUESTION_NO_PLAN_STEER", "EXECUTION_CHANGE_ONE_STABLE_STEER"),
    ),
    _route(
        "delta_entry",
        "ADAPTIVE_DELTA_ENTRY_FROM_TASK_CLASSIFY",
        "adaptive_delta_entry",
        schema="evidence-lane.adaptive-delta-entry-receipt.v1",
        public_tools=("task_classify",),
        plan_families=("DELTA_ENTRY", "PLAN", "SIX_AUTHORITY"),
        verification=(
            "ALL_LIVE_AUTHORITIES_CONSUMED",
            "FULL_PV_BASELINE_SEPARATE_FROM_PROGRESSIVE_SUBPV_WATERMARK",
            "CURRENT_ACTIVE_DELTA_ABSENT_UNTIL_EXIT",
            "NO_ACCEPTED_ARCHIVE_QUERY",
        ),
    ),
    _route(
        "delta_exit_refresh",
        "ADAPTIVE_DELTA_EXIT_CHANGED_LANES_AND_INTELLIGENCE",
        "adaptive_delta_exit",
        schema="evidence-lane.adaptive-delta-exit-receipt.v1",
        public_tools=(
            "task_confirm_source_update",
            "adaptive_delta_exit",
            "pv_task_transition",
        ),
        plan_families=("DELTA_EXIT", "LANE_REFRESH", "SUB_PV"),
        verification=(
            "ORDINARY_NO_PROJECT_OVERLAY",
            "CONNECTOR_BRAIN_REFRESH",
            "SOURCE_TEST_NOT_INSTALLED_HOST_PROOF",
            "POSTINSTALL_EXACT_TASK_PROOF",
            "EVERY_GIT_TRACKED_PATH_CURRENT_WORKTREE_FINGERPRINT",
        ),
    ),
    _route(
        "hil_project_overlay",
        "TASK_COMPLETE_AND_REFRESH_HIL_LIVE_ROOT_OVERLAY",
        "evi-refresh",
        schema="evidence-lane.project-hil-proposal.v1",
        public_tools=("task_complete_and_refresh",),
        plan_families=("HIL", "PROJECT_OVERLAY", "DELTA_EXIT"),
        verification=("SAME_PROPOSAL_ID_FINALIZED", "HIL_ONLY_OVERLAY"),
    ),
    _route(
        "project_sub_pv",
        "PLAN_SQLITE_SUB_PV_ACCEPTANCE_POINT",
        "plan_runtime",
        schema="evidence-lane.sub-pv-acceptance.v1",
        consumers=("PLAN_SQLITE", "DELTA_ENTRY", "DELTA_EXIT", "AI_LEARNING"),
        plan_families=("SUB_PV", "PLAN"),
        verification=("NO_FILESYSTEM_ARTIFACT", "NO_POINTER_OR_HIL"),
    ),
    _route(
        "learning_delta_weave",
        "AUTO_ADMITTED_LEARNING_DELTAS_THEN_ONE_HIL_WEAVE",
        "agent_learning",
        schema="evidence-lane.learning-weave-receipt.v1",
        public_tools=(
            "learning_decide_candidate",
            "learning_inspect",
            "learning_record_host_memory_import",
            "learning_retrieve",
            "learning_revoke",
            "learning_seal_candidate",
        ),
        plan_families=("AI_LEARNING", "MEMORY"),
        verification=("MEMBER_SET_HASH", "ONE_PENDING_WEAVE"),
    ),
    _route(
        "project_memory",
        "INDEPENDENT_PROJECT_MEMORY_FTS5_LOCATOR_GRAPH",
        "evi-memory",
        schema="evidence-lane.project-memory.v1",
        public_tools=("project_memory_query", "project_memory_record_link"),
        plan_families=("MEMORY", "SIX_AUTHORITY", "CHATLINEAGE"),
        verification=(
            "BOUNDED_FTS5_BM25_LOCATORS",
            "STALE_ACTIVE_TASK_SUPPRESSION",
            "AUTHORITY_ROLE_SEPARATION",
            "DELTA_EXIT_REFRESH_OWNER",
        ),
    ),
    _route(
        "agents_and_host_memory_arms",
        "SCOPED_AGENTS_INSTRUCTIONS_PLUS_BOUNDED_HOST_MEMORY_RECALL",
        "evi-instructions",
        schema="evidence-lane.live-root-env-uop-six-way-query.v1",
        plan_families=("SIX_AUTHORITY", "MEMORY", "LIVE_ROOT_QUERY"),
        verification=(
            "SCOPED_AGENTS_SOURCE_CHAIN_HASH",
            "HOST_MEMORY_LOCATOR_HASH_ONLY",
            "NO_AUTOMATIC_HOST_MEMORY_IMPORT",
            "DELTA_ENTRY_AND_IN_WORK_QUERY_AVAILABLE",
        ),
    ),
    _route(
        "project_universe_and_connector_brain",
        "LINKED_PROJECT_UNIVERSE_AND_CONNECTOR_BRAIN_QUERY",
        "evi-universe",
        schema="evidence-lane.live-root-env-uop-six-way-query.v1",
        plan_families=("SIX_AUTHORITY", "CONNECTOR", "LIVE_ROOT_QUERY"),
        verification=(
            "BOUNDED_UNIVERSE_LOCATOR_SLICE",
            "BOUNDED_CONNECTOR_INTEGRITY_SLICE",
            "ADAPTIVE_DELTA_EXIT_REFRESH_OWNER",
            "NO_ACCEPTED_STORAGE_QUERY",
        ),
    ),
    _route(
        "dual_hil_fuse",
        "LEARNING_WEAVE_APPROVE_THEN_MATCHING_PROJECT_FUSE",
        "evi-build+evi-learning",
        schema="evidence-lane.dual-project-learning-hil.v1",
        public_tools=(
            "pv_fuse",
            "hil_decide",
            "hil_intent_classify",
            "hil_return_to_accepted",
        ),
        plan_families=("HIL", "AI_LEARNING", "FUSE"),
        verification=(
            "TWO_EXACT_HUMAN_DECISIONS",
            "SAME_TARGET_PV",
            "WEAVE_SUMMARY_PRESENTED",
            "PLAN_ROW_DUAL_ACCEPTANCE_STAMP",
            "ONE_ACCEPTED_ROOT_ZIP",
        ),
    ),
    _route(
        "project_candidate_storage",
        "LIVE_PROJECT_ROOT_PLUS_SEALED_PROPOSAL_RECEIPT",
        "project_authority",
        schema="evidence-lane.project-hil-proposal.v1",
        plan_families=("CANDIDATE", "LIVE_ROOT"),
        verification=("NO_CANDIDATE_FOLDER", "CANDIDATE_ID_PRESERVED"),
    ),
    _route(
        "accepted_storage",
        "ONE_NUMBERED_FULL_ROOT_ZIP_ROTATED_AT_APPROVED_HIL",
        "project_pv_storage",
        schema="evidence-lane.project-pv-archive.v1",
        plan_families=("ACCEPTED_STORAGE", "HIL"),
        verification=(
            "EXCLUDES_ACCEPTED_DIRECTORY",
            "EXACTLY_ONE_NUMBERED_ZIP",
            "SNAPSHOT_ONLY_NEVER_ENTRY_QUERY_OR_STATE_TRAVEL",
        ),
    ),
    _route(
        "state_travel",
        "SESSION_RESUME_THEN_SIX_FIELD_DIRECT_SAME_WORKTREE",
        "evi-state-travel",
        schema="evidence-lane.direct-forced-same-worktree-entry-receipt.v2",
        public_tools=(
            "pv_state_travel_direct_force_same_worktree",
        ),
        plan_families=("STATE_TRAVEL", "RUNTIME_ATTACHMENT", "GOAL"),
        verification=(
            "CANDIDATE_PENDING_HIL_PRESERVED",
            "REPLAY_AND_IDENTITY_NEGATIVES",
            "ONE_EVI_PLAN_BEFORE_IMPLEMENT_GATE",
            "NO_SECOND_EVI_PLAN_BEFORE_GOAL_RESUME",
            "DESTINATION_GOAL_THEN_FIXED_STEP_RELOCK",
            "SOURCE_OPTION2_METRICS_THEN_ONE_CONSOLIDATED_STEER",
        ),
    ),
    _route(
        "step_task_list",
        "CANONICAL_HEADER_PLUS_PERSISTED_FIXED_WINDOW",
        "host_plan_rehydration",
        schema="evidence-lane.host-plan-current-window.v2",
        public_tools=("pv_plan_tasks", "pv_task_backlog"),
        plan_families=("PLAN", "STEP_TASK_LIST", "GOAL"),
        verification=("IDENTICAL_REATTACHMENT_FINGERPRINT", "ONE_HEADER_PLUS_ROWS"),
    ),
    _route(
        "goal_metrics",
        "RESET_AWARE_RICH_GOAL_COMPLETION_METRICS",
        "goal_usage",
        schema="evidence-lane.rich-goal-completion-metrics.v1",
        plan_families=("GOAL", "METRICS", "CHATLINEAGE"),
        verification=(
            "COUNTER_RESET_EPOCHS",
            "NATIVE_TURN_RECONCILIATION",
            "SUPERSESSION_IDEMPOTENCE",
        ),
    ),
    _route(
        "hook_control",
        "NATIVE_HOOKS_LIST_CONFIG_READ_BATCH_WRITE_CAS",
        "codex_native_hook_control",
        schema="evidence-lane.native-hook-event-control.v1",
        plan_families=("HOOKS", "HOST_LIFECYCLE"),
        verification=(
            "11_EVENT_SOURCE_MATRIX",
            "11_EVENT_INSTALLED_HOST_MATRIX",
            "EVENT_COUNT_SEPARATE_FROM_NESTED_HANDLER_ACTION_COUNT",
            "HOOK_EVENT_DOT_ACTION_NUMBERING",
        ),
    ),
    _route(
        "local_install",
        "PLUGIN_CREATOR_STAGE_MATERIALIZE_PREPARE_TERMINAL_USER_RESTART_REATTACH",
        "plugin-creator",
        schema="evidence-lane.codex-stable-installation.v2",
        plan_families=("INSTALL", "PACKAGE", "LOCAL_SLOT"),
        verification=(
            "TARGET_RUNTIME_PREWARM",
            "HOOK_ISOLATION_SEALED",
            "EXACT_TASK_REATTACHMENT",
        ),
        implementation_class="SDK_GOVERNED_HOST_TRANSACTION_ADAPTER",
    ),
    _route(
        "restart_reattachment",
        "TERMINAL_SAFE_PREPARATION_THEN_USER_EXACT_CHANNEL_RESTART",
        "Prepare-EvidenceLaneCodexRestart.ps1",
        schema="evidence-lane.codex-terminal-safe-restart-preparation.v1",
        plan_families=("RESTART", "RUNTIME_ATTACHMENT", "GOAL"),
        verification=(
            "NO_PROGRAMMATIC_APP_STOP",
            "NO_TURN_DRAIN_UTILITY",
            "CURRENT_RESPONSE_TERMINAL_BEFORE_USER_RESTART",
            "POST_RESTART_NATIVE_EXACT_TASK_READBACK",
            "NO_HIDDEN_OVERLAY",
        ),
        implementation_class="SDK_GOVERNED_PREPARATION_ONLY_HOST_ADAPTER",
    ),
    _route(
        "tunnel_transport",
        "SDK_GOVERNED_CAPABILITY_GAP_TRANSPORT",
        "EvidenceLaneTunnelHost",
        schema="evidence-lane.tunnel-runtime.v1",
        plan_families=("HOST_LIFECYCLE", "RUNTIME_ATTACHMENT"),
        consumers=(
            "INTERNAL_SDK",
            "TUNNEL_ADAPTER",
            "PACKAGE",
            "INSTALLED_HOST",
        ),
        verification=(
            "HOST_MATRIX_CAPABILITY_GAP_ONLY",
            "RELEASE_AND_SLOT_FINGERPRINT",
            "NO_PROJECT_PLAN_GOAL_HIL_EFFECT",
        ),
        implementation_class="SDK_GOVERNED_THIN_TRANSPORT_ADAPTER",
    ),
    _route(
        "first_class_workflows",
        "ENV_UOP_FIRST_CLASS_WORKFLOW_EXECUTION",
        "first_class_workflows",
        schema="evidence-lane.first-class-workflows.v1",
        public_tools=(
            "formula_engine_run",
            "brain_scaling_select",
            "project_recipe_compile",
            "ai_toolchain_route",
            "bigger_universe_register",
            "bigger_universe_link",
        ),
        plan_families=("ENV_UOP", "AI_TOOLCHAIN", "UNIVERSE_FEDERATION"),
        verification=(
            "BOUNDED_FORMULA_EXECUTION",
            "DETERMINISTIC_BRAIN_SLICE",
            "PROJECT_RECIPE_COMPILED",
            "CONDITIONAL_TOOLCHAIN_ROUTE",
            "HASH_ONLY_CROSS_PROJECT_FEDERATION",
        ),
    ),
    _route(
        "remote_adapter",
        "FINGERPRINTED_READ_ONLY_PUBLIC_ACTION_PROJECTION",
        "remote_adapter",
        schema="evidence-lane.remote-public-action-registry.v1",
        plan_families=("REMOTE_ADAPTER", "PUBLIC_SURFACE"),
        verification=("SOURCE_CATALOG_SHA_MATCH", "DERIVED_PROJECTED_ACTIONS"),
        implementation_class="SDK_GOVERNED_READ_ONLY_PROJECTION_ADAPTER",
    ),
    _route(
        "canon_exchange",
        "TYPED_RECEIVER_OWNED_CANON_GRAPH_EXCHANGE",
        "evi-canon",
        schema="evidence-lane.canon-consequence-graph.v1",
        public_tools=(
            "canon_backfire_hil",
            "canon_bind_edge",
            "canon_classify",
            "canon_decide",
            "canon_dispatch_linked_task",
            "canon_graph",
            "canon_inbox",
            "canon_inspect",
            "canon_receive",
            "canon_register_contract",
            "canon_register_edge",
            "canon_restore_continuity",
            "canon_seal_continuity",
            "canon_seal_envelope",
            "canon_seal_result",
            "canon_supersede",
        ),
        plan_families=("CANON", "CROSS_TASK", "CONSEQUENCE_GRAPH"),
        verification=(
            "RECEIVER_OWNED_THREE_WAY_HIL",
            "EXACT_TASK_OR_AUTHORIZED_SUBAGENT_EDGE_BINDING",
            "CALLER_MEDIATED_NATIVE_HOST_DISPATCH_WHEN_NOT_INJECTED",
            "TYPED_UPSTREAM_DOWNSTREAM_LATERAL_MESSAGE_SCHEMA",
            "TYPED_BACKFIRE_AND_RESULT_RETURN_SCHEMA",
            "NO_OPAQUE_PUBLIC_PAYLOAD_SCHEMA",
        ),
    ),
    _route(
        "connector_grants",
        "BOUNDED_APPEND_ONLY_CONNECTOR_GRANT_REGISTRY",
        "evi-plugin",
        schema="evidence-lane.connector-plugin.v1",
        public_tools=(
            "connector_plugin_catalog",
            "connector_plugin_drop",
            "connector_plugin_register",
            "connector_plugin_route",
            "connector_plugin_settings",
        ),
        plan_families=("CONNECTOR", "PLUGIN"),
        verification=("MAX_EIGHT_ACTIVE", "APPEND_ONLY_HISTORY"),
    ),
    _route(
        "storage_routing",
        "PROJECT_SCOPED_TRANSACTIONAL_STORAGE_SELECTION",
        "evi-storage",
        schema="evidence-lane.storage-selection.v1",
        public_tools=("storage_connector_inspect", "storage_connector_select"),
        plan_families=("STORAGE", "RUNTIME_CLASSIFICATION"),
        verification=("LOCAL_DURABLE_PREFERRED", "DRIVE_MIRROR_ONLY"),
    ),
    _route(
        "mode_sidecar",
        "ORDERED_MODE_INTERSECTION_WITH_CHATLINEAGE",
        "evi-mode",
        schema="evidence-lane.mode-classification.v1",
        public_tools=("mode_classify",),
        plan_families=("MODE", "CHATLINEAGE"),
        verification=("RETURN_TO_PRIOR_LIFECYCLE", "NO_POINTER_OR_CANDIDATE"),
    ),
    _route(
        "task_activity_and_same_host_continuation",
        "VISIBLE_ACTIVITY_LEDGER_AND_EXPLICIT_SAME_HOST_CONTINUATION",
        "evidence-lane-code-lifecycle",
        schema="evidence-lane.task-activity.v1",
        public_tools=("pv_begin_next_turn", "task_record_activity"),
        plan_families=("CHATLINEAGE", "TASK_LIFECYCLE"),
        verification=("VISIBLE_ONLY", "EXPLICIT_USER_CONTINUATION"),
    ),
    _route(
        "pointer_rollback",
        "THREE_MODE_ROLLBACK_UNDER_ONE_EXISTING_OWNER",
        "evi-rollback",
        schema="evidence-lane.rollback-three-mode.v1",
        public_tools=("pv_rollback",),
        plan_families=("ROLLBACK", "POINTER", "SUB_PV", "PROJECT_OVERLAY"),
        verification=(
            "PLAN_STAMPED_FULL_AND_SUB_PV_TARGETS",
            "FULL_PV_OVERLAY_SUBPV_NO_OVERLAY",
            "CANDIDATE_TASK_POINTER_PRESERVED",
            "HARD_RESTORE_SEPARATE_USER_SELECTED_FULL_PV_ZIP_ONLY",
            "GIT_RESTORE_EXACT_REPOSITORY_BRANCH_COMMIT_FRESH_WORKSPACE",
            "HARD_AND_GIT_RESTORE_REQUIRE_FRESH_EVI_PLAN",
            "ROLLBACK_MODES_REMAIN_ONE_EXISTING_ROUTE_OWNER",
        ),
    ),
    _route(
        "governed_git_delivery",
        "EXACT_REGISTERED_BRANCH_GIT_DELIVERY",
        "evidence-lane-code-lifecycle",
        schema="evidence-lane.git-sync-receipt.v1",
        public_tools=(
            "git_sync_selected",
            "remote_git_prepare_push",
            "remote_git_execute_push",
        ),
        plan_families=("GIT", "GITHUB_APP", "RELEASE"),
        verification=("EXACT_BRANCH_ALLOWLIST", "NO_R265_GIT_INVOCATION"),
    ),
    _route(
        "project_and_runtime_rendering",
        "TWO_TRIGGER_READ_ONLY_RENDER_DISPATCH",
        "evidence-lane-code-lifecycle",
        schema="evidence-lane.render-invocation-policy.v1",
        public_tools=("render_project_panel", "render_runtime_panel"),
        plan_families=("RENDERER", "PUBLIC_PROOF"),
        verification=("EXPLICIT_OR_PHYSICAL_FINAL_HIL", "READ_ONLY"),
    ),
)


def current_implementation_registry() -> dict[str, Any]:
    """Return the exact immutable installed-version dispatch registry."""

    capabilities = [dict(route) for route in _ROUTES]
    current_ids = [str(route["current_route"]) for route in capabilities]
    if len(current_ids) != len(set(current_ids)):
        raise RuntimeError("CURRENT_IMPLEMENTATION_ROUTE_ID_DUPLICATE")
    tool_rows = [
        {
            "tool": str(tool),
            "capability": str(route["capability"]),
            "current_route": str(route["current_route"]),
            "owner": str(route["owner"]),
            "status": "CURRENT_ROUTE",
            "executable": True,
            "fallback_allowed": False,
        }
        for route in capabilities
        for tool in route["public_tools"]
    ]
    tool_ids = [row["tool"] for row in tool_rows]
    if len(tool_ids) != len(set(tool_ids)):
        raise RuntimeError("CURRENT_IMPLEMENTATION_PUBLIC_TOOL_ROUTE_DUPLICATE")
    core = {
        "schema": CURRENT_IMPLEMENTATION_REGISTRY_SCHEMA,
        "status": "PASS",
        "dispatch_law": "ONE_CURRENT_IMPLEMENTATION_PER_CAPABILITY",
        "plan_history_resolution": (
            "LATEST_NON_SUPERSEDED_EXECUTABLE_CONTRACT_WITH_IMMUTABLE_HISTORY"
        ),
        "capability_count": len(capabilities),
        "capabilities": capabilities,
        "public_tool_count": len(tool_rows),
        "public_tool_routes": tool_rows,
        "obsolete_public_tools": [],
        "all_public_tools_have_one_route": True,
        "obsolete_execution_allowed": False,
        "fallback_to_historical_route_allowed": False,
    }
    encoded = json.dumps(
        core,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        **core,
        "registry_sha256": hashlib.sha256(encoded).hexdigest().upper(),
    }


def current_route(capability: str) -> dict[str, Any]:
    exact = str(capability or "").strip()
    rows = [
        row
        for row in current_implementation_registry()["capabilities"]
        if row["capability"] == exact
    ]
    if len(rows) != 1:
        raise KeyError(f"CURRENT_IMPLEMENTATION_CAPABILITY_UNKNOWN:{exact}")
    return dict(rows[0])
