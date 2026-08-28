"""Generate one canonical public-action catalog and bounded consumer projections."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
REMOTE_ADAPTER_ROOT = REPOSITORY_ROOT / "apps" / "evidence-lane-remote-adapter"
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _write_atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(value)
            handle.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _annotation(tool: Any) -> dict[str, Any]:
    annotations = tool.annotations
    if annotations is None:
        return {}
    if hasattr(annotations, "model_dump"):
        return annotations.model_dump(mode="json", exclude_none=True)
    return dict(annotations)


def build_catalog() -> dict[str, Any]:
    from evidence_lane_plugin.authority_support import AUTHORITY_SUPPORT_PROFILES
    from evidence_lane_plugin.current_route_registry import (
        current_implementation_registry,
    )
    from evidence_lane_plugin.internal_sdk import (
        runtime_workflow_sdk_registry,
        sdk_plane_registry,
    )
    from evidence_lane_plugin.lanes import LANE_REGISTRY
    from evidence_lane_plugin.mcp_server import SDK_NATIVE_ACTIONS, create_mcp_server

    server = create_mcp_server()
    tools = sorted(asyncio.run(server.list_tools()), key=lambda item: item.name)
    implementation_registry = current_implementation_registry()
    if len(tools) != int(implementation_registry["public_tool_count"]):
        raise RuntimeError(
            "The MCP catalog and current implementation registry action counts differ."
        )
    hook_configuration = json.loads(
        (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    logical_action_configuration = json.loads(
        (PLUGIN_ROOT / "hooks" / "logical-actions.json").read_text(encoding="utf-8")
    )
    if (
        set(hook_configuration) != {"description", "hooks"}
        or logical_action_configuration.get("schema")
        != "evidence-lane.hook-logical-action-registry.v1"
    ):
        raise RuntimeError("The host and internal hook registries are not separated.")
    logical_action_registry = dict(
        logical_action_configuration.get("logicalActions") or {}
    )
    logical_action_count = sum(
        len(actions)
        for actions in logical_action_registry.values()
        if isinstance(actions, list)
    )

    routing_path = (
        PLUGIN_ROOT / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
    )
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    owners = dict(routing.get("tool_owners") or {})
    if set(owners) != {tool.name for tool in tools}:
        raise RuntimeError("Public tool owners do not match the live MCP catalog.")
    sdk_routes = {
        str(row[0]): {
            "module_id": str(row[3]),
            "operation": str(row[4]),
            "read_only": bool(row[5]),
        }
        for row in SDK_NATIVE_ACTIONS
    }
    workflow_routes: dict[str, list[dict[str, Any]]] = {tool.name: [] for tool in tools}
    for skill_name, workflow in dict(routing.get("workflows") or {}).items():
        for group in list(dict(workflow).get("ordered_tool_groups") or []):
            for tool_name in list(dict(group).get("tools") or []):
                if tool_name in workflow_routes:
                    workflow_routes[tool_name].append(
                        {
                            "owner_skill": skill_name,
                            "workflow": str(group.get("workflow") or ""),
                            "order": int(group.get("order") or 0),
                        }
                    )

    records: list[dict[str, Any]] = []
    for tool in tools:
        annotations = _annotation(tool)
        body = {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.inputSchema,
            "output_schema": tool.outputSchema,
            "annotations": annotations,
            "route_contract": {
                "status": "CURRENT_ROUTE",
                "owner_skill": owners[tool.name],
                "mcp": {
                    "server_identity": "evidence-lane",
                    "tool_name": tool.name,
                },
                "internal_sdk": sdk_routes.get(tool.name),
                "service_dispatch": (
                    "INTERNAL_SDK_REGISTRY"
                    if tool.name in sdk_routes
                    else "PACKAGE_LOCAL_MCP_HANDLER"
                ),
                "skill_workflows": sorted(
                    workflow_routes[tool.name],
                    key=lambda row: (
                        str(row["owner_skill"]),
                        int(row["order"]),
                        str(row["workflow"]),
                    ),
                ),
                "executable": True,
            },
        }
        records.append(
            {
                **body,
                "schema_sha256": hashlib.sha256(_canonical(body)).hexdigest().upper(),
            }
        )

    read_count = sum(
        bool(record["annotations"].get("readOnlyHint")) for record in records
    )
    write_count = len(records) - read_count
    lanes = sorted(LANE_REGISTRY)
    sdk_planes = sdk_plane_registry()
    runtime_workflows = runtime_workflow_sdk_registry()

    body = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema": "evidence-lane.public-action-schema-catalog.v2",
        "tool_count": len(records),
        "read_tool_count": read_count,
        "write_tool_count": write_count,
        "counts_are_derived_not_fixed": True,
        "lane_count": len(lanes),
        "canonical_lanes": lanes,
        "env_uop_governed_six_way_arms": [
            "PROJECT_SECTORS_AND_ROOT_FILES",
            "AI_LEARNING",
            "CANON_GRAPH",
            "PROJECT_MEMORY_DB",
            "HOST_CONVERSATION_MEMORY_MD",
            "AGENTS_MD",
        ],
        "linked_operational_authorities": [
            "PROJECT_UNIVERSE",
            "CONNECTOR_BRAIN",
        ],
        "ordinary_live_authority_count": 8,
        "hil_only_authorities": ["PROJECT_OVERLAY"],
        "governance": ["ENV", "UOP"],
        "sdk_planes": sdk_planes,
        "runtime_workflow_sdk_registry": runtime_workflows,
        "public_action_and_env_uop_ai_action_planes_separate": True,
        "env_uop_operations_added_to_public_action_count": 0,
        "authority_support_systems": {
            "profiles": sorted(AUTHORITY_SUPPORT_PROFILES),
            "sector_lane_contract": (
                "POINTER_MANIFEST_THEN_MMD_DOT_THEN_EVOLVED_SQLITE"
            ),
            "tools_json_role": "BUILD_REFRESH_SCHEMA_AND_DEPENDENCY_PROVENANCE",
            "tools_json_selects_query": False,
            "bootstrap_schema_is_fixed_project_ceiling": False,
            "ordinary_delta_exit_excludes_project_overlay": True,
            "project_overlay_refresh_owner": "HIL_ONLY",
        },
        "hooks_required_for_explicit_actions": False,
        "accepted_hil_archive_queried_by_ordinary_actions": False,
        "shared_contracts": {
            "live_root_query": "live-root-env-uop-six-way-query.v001.json",
            "lane_fts5_bm25": "lane-search-fts5.v001.json",
            "canon": "canon/canon-consequence-graph.v1.sql",
            "memory": "memory/project-memory.v1.sql",
            "universe": "universe/project-universe.v1.sql",
        },
        "project_bootstrap": {
            "route": "REGISTER__EVI_PLAN__GOAL_STEP__SOURCE_INTAKE_BUILD_PV0_NO_HIL",
            "baseline_pv": "PV0",
            "pointer_generation": 0,
            "sector_lanes_materialized": True,
            "candidate_created": False,
            "human_hil_required": False,
            "project_overlay_refreshed": False,
            "accepted_artifact_created": False,
            "initial_pv1_candidate_compatibility_route_present": False,
            "pre_pv0_order": [
                "COLLECT_USER_BRIEF",
                "ENTER_NATIVE_PLAN_MODE",
                "RUN_EVI_PLAN_ON_FINISHED_PLAN",
                "EXPLICIT_HOST_PLAN_ACCEPTANCE",
                "START_OR_BIND_GOAL",
                "BIND_FIXED_STEP_TASK_LIST",
            ],
            "post_pv0_order": ["CONTINUE_ACTIVE_GOAL_PLAN_ROW"],
            "state_travel_reuses_registration": True,
            "state_travel_creates_project_or_pv0": False,
        },
        "rollback": {
            "distinct_modes": [
                "LOGICAL_LIVE_ROOT_STATE",
                "HARD_ACCEPTED_ZIP_RESTORE",
                "GIT_BRANCH_COMMIT_RESTORE",
            ],
            "logical_route": "PLAN_STAMP_OVERLAY_LOGICAL_ROLLBACK_CURSOR",
            "full_pv_acceptance_authority": "PLAN_DUAL_HIL_ACCEPTANCE_STAMP",
            "sub_pv_acceptance_authority": "PLAN_SQLITE_SUB_PV_ACCEPTANCE",
            "full_pv_comparison_authority": "PROJECT_OVERLAY",
            "sub_pv_overlay_claimed": False,
            "accepted_folder_queried_for_logical_rollback": False,
            "accepted_pointer_moved": False,
            "candidate_task_goal_source_preserved": True,
            "hard_restore": {
                "route": "SEPARATE_EXPLICIT_USER_SELECTED_FULL_PV_RESTORE",
                "sub_pv_allowed": False,
                "zip_source": "USER_SUPPLIED_MATCHING_FULL_PV_ACCEPTANCE_ZIP",
                "git_source": (
                    "EXACT_PLAN_BOUND_COMMIT_TREE_WITH_AUTHORIZED_GIT_ACCESS"
                ),
                "branch_name_alone_sufficient": False,
                "in_place_dirty_worktree_reset_allowed": False,
                "new_workspace_baseline_required": True,
                "post_restore_order": [
                    "SOURCE_INTAKE_REBIND",
                    "COLLECT_NEW_USER_BRIEF",
                    "EVI_PLAN",
                    "EXPLICIT_PLAN_ACCEPTANCE",
                    "NEW_GOAL_AND_DELTA_CYCLE",
                ],
            },
            "git_restore": {
                "route_owner": "EXISTING_PV_ROLLBACK_OWNER",
                "requires_exact_repository_branch_commit": True,
                "fresh_user_selected_workspace_required": True,
                "dirty_current_workspace_reset_allowed": False,
                "local_code_rebuilt_from_github_code_authority": True,
                "plan_history_preserved_through_commit_stamped_row": True,
                "later_rows_made_non_executable": True,
                "fresh_evi_plan_required": True,
            },
        },
        "prompt_and_steer_dispatch": {
            "source_intake_first": True,
            "env_uop_governed": True,
            "chat_lineage_always_appended": True,
            "execution_change_fields": [
                "OUTCOME",
                "DEPENDENCY",
                "ACCEPTANCE",
                "STOP_CONDITION",
                "RELEASE_ROUTE",
                "HIL_PATH",
            ],
            "execution_changing_action": "pv_plan_steer_delta",
            "stable_idempotent_id_required": True,
            "questions_create_plan_steer": False,
        },
        "source_intake_code_routing": {
            "schema": "evidence-lane.code-source-routing-batch.v1",
            "central_code_project_count_max": 1,
            "central_project_change_requires_new_project_pv": True,
            "additional_local_code_folders_role": "LANE_SCOPED_STUDY_BRAIN",
            "public_unowned_repository_role": "LANE_SCOPED_STUDY_BRAIN",
            "public_unowned_repository_history_allowed": False,
            "owned_or_explicitly_authorized_history_allowed": True,
            "local_code_and_github_code_distinct": True,
            "local_code_refresh_source": "CURRENT_DELTA_DIRTY_BYTES",
            "github_code_refresh_source": "EXACT_GOVERNED_GIT_CHECKPOINT_ONLY",
            "lane_owned_artifacts": [
                "LANE_SQLITE_FTS5",
                "LANE_MMD",
                "LANE_DOT",
                "TOOLS_JSON",
                "LANE_POINTER_JSON",
                "LANE_MANIFEST_JSON",
                "STUDY_BRAIN_JSON",
            ],
            "materialization_owners": {
                "initial": "SOURCE_INTAKE_THEN_BUILD_PV0",
                "delta": "SOURCE_INTAKE_THEN_ADAPTIVE_DELTA_EXIT_REFRESH",
                "hil": "DELTA_EXIT_PLUS_HIL_ONLY_PROJECT_OVERLAY",
            },
        },
        "delta_route_retirement": {
            "applies_to_every_delta": True,
            "new_workflow_updates_all_consumers_in_same_delta": True,
            "superseded_executor_present": False,
            "compatibility_fallback_present": False,
        },
        "delta_authority_time_boundary": {
            "full_pv_pointer_role": "IMMUTABLE_PV_N_MINUS_1_BASELINE",
            "live_sector_role": ("PROGRESSIVE_THROUGH_PREDECESSOR_DELTA_EXIT_SUBPV"),
            "predecessor_learning_role": "AUTO_ADMITTED_DELTA_LEARNING",
            "current_active_delta_present_in_lanes": False,
            "current_active_delta_source_role": "DIRTY_IMPLEMENTATION_BYTES",
            "source_tests_prove_installed_behavior": False,
            "installed_behavior_requires_local_package_and_reattachment": True,
            "delta_exit_per_file_fingerprint": (
                "GIT_INDEX_STAGE0_PATH_SET_WITH_CURRENT_WORKTREE_BYTES"
            ),
            "tracked_deletions_explicit": True,
            "untracked_paths_in_per_file_manifest": False,
        },
        "dual_hil_fuse": {
            "every_full_pv_hil_is_dual": True,
            "project_and_learning_decisions_separate": True,
            "learning_weave_approved_before_project_fuse": True,
            "project_fuse_requires_matching_target_pv": True,
            "individual_delta_learning_hil": False,
            "bounded_learning_weave_summary_presented": True,
            "acceptance_authority": "PLAN_ROW_DUAL_HIL_STAMP",
            "accepted_storage": "ONE_NUMBERED_ROOT_ZIP_EXCLUDING_ACCEPTED",
            "accepted_storage_role": "POST_APPROVAL_SNAPSHOT_ONLY_NEVER_QUERIED",
            "automatic_state_travel": False,
        },
        "state_travel_golden_protocol": {
            "destination_native_phase0_first": True,
            "evi_plan_invocation_count": 1,
            "visible_implement_control_required": True,
            "second_evi_plan_after_acceptance": False,
            "post_acceptance_order": [
                "NATIVE_ACCEPTANCE_RECEIPT",
                "CARRIED_GOAL_RESUME_RUNNING",
                "FIXED_HEADER_PLUS_BATCH_AND_CHANGES_RELOCK",
            ],
            "source_option2_waits_for_destination_goal": True,
            "source_metrics_route": "RESET_AWARE_RICH_GOAL_COMPLETION_METRICS",
            "late_source_steers_sent_once_after_destination_goal_resume": True,
            "accepted_zip_queried": False,
        },
        "hook_control": {
            "event_count": 11,
            "event_count_semantics": "OFFICIAL_REGISTERED_EVENT_TYPE_COUNT",
            "handler_action_count_semantics": (
                "TOTAL_NESTED_HANDLER_ACTION_COUNT_CAN_EXCEED_11"
            ),
            "numbering": "HOOK_EVENT_ORDINAL.ACTION_ORDINAL",
            "one_or_more_actions_per_event": True,
            "current_handler_action_count": sum(
                len(group.get("hooks") or [])
                for groups in dict(hook_configuration.get("hooks") or {}).values()
                for group in groups
            ),
            "current_logical_action_count": logical_action_count,
            "logical_action_numbering": "HOOK_EVENT_ORDINAL.L_ACTION_ORDINAL",
            "host_api": ["hooks/list", "config/read", "config/batchWrite"],
            "native_compare_and_swap_only": True,
            "install_enables_hooks": False,
            "failed_event_only_disabled_during_repair": True,
            "passing_event_reenabled_after_installed_host_proof": True,
        },
        "local_install": {
            "route": "PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW",
            "ordered_steps": [
                "PLUGIN_CREATOR_VALIDATE_AND_PACKAGE",
                "ONE_CACHEBUSTER",
                "CONFIGURED_LOCAL_SOURCE_STAGE",
                "SEALED_LOCAL_CACHE_MATERIALIZER",
                "TERMINAL_SAFE_RESTART_PREPARATION",
                "CURRENT_RESPONSE_TERMINAL_COMPLETION",
                "USER_EXACT_CHANNEL_CLOSE_REOPEN",
                "EXACT_TASK_REATTACHMENT",
            ],
            "direct_add_compatibility_route_present": False,
            "turn_drain_utility_present": False,
            "programmatic_app_stop_allowed": False,
        },
        "goal_metrics": {
            "current_route": "build_rich_goal_completion_metrics_receipt",
            "reset_aware_epoch_accounting_required": True,
            "native_turn_reconciliation_required": True,
            "correction_and_supersession_required": True,
            "compatibility_collector_present": False,
        },
        "consumer_surfaces": [
            "PUBLIC_ACTION_SCHEMA",
            "MCP",
            "INTERNAL_SDK",
            "SERVICE_HANDLER",
            "SKILL",
            "COMMAND_AND_PROMPT_INTENT",
            "HOOK",
            "REMOTE_ADAPTER",
            "PACKAGED_RUNTIME",
            "INSTALLED_MODEL_VISIBLE_CATALOG",
        ],
        "current_implementation_registry": implementation_registry,
        "schema_authority": {
            "canonical_path": "schemas/public-action-schemas.v001.json",
            "independent_runtime_duplicate_allowed": False,
            "remote_projection_path": (
                "apps/evidence-lane-remote-adapter/app/_data/public-action-registry.json"
            ),
            "remote_projection_is_execution_authority": False,
        },
        "tools": records,
    }
    return {
        **body,
        "catalog_sha256": hashlib.sha256(_canonical(body)).hexdigest().upper(),
    }


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    public_path = plugin_root / "schemas" / "public-action-schemas.v001.json"
    obsolete_runtime_path = (
        plugin_root
        / "src"
        / "evidence_lane_plugin"
        / "schemas"
        / "public-action-schemas.v001.json"
    )
    catalog = build_catalog()
    payload = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
    _write_atomic_text(public_path, payload)
    conformance_gate_path = (
        plugin_root
        / "skills"
        / "evi"
        / "references"
        / "public-tool-conformance-release-gate.v1.json"
    )
    conformance_gate = json.loads(conformance_gate_path.read_text(encoding="utf-8"))
    if (
        conformance_gate.get("schema")
        != "evidence-lane.public-tool-conformance-release-gate.v1"
        or conformance_gate.get("status") != "PASS_SOURCE_PREINSTALL"
    ):
        raise RuntimeError("The public-tool conformance gate is invalid.")
    conformance_gate["generated_from"]["public_catalog_sha256"] = (
        hashlib.sha256(public_path.read_bytes()).hexdigest().upper()
    )
    _write_atomic_text(
        conformance_gate_path,
        json.dumps(conformance_gate, indent=2, ensure_ascii=False) + "\n",
    )
    if obsolete_runtime_path.exists():
        raise RuntimeError(
            "OBSOLETE_ROUTE=INDEPENDENT_RUNTIME_PUBLIC_ACTION_SCHEMA_COPY;"
            "REQUIRED_CURRENT_ROUTE=schemas/public-action-schemas.v001.json"
        )
    remote_core = {
        "schema": "evidence-lane.remote-public-action-registry.v1",
        "status": "GENERATED_READ_ONLY_PROJECTION",
        "source_catalog_path": "schemas/public-action-schemas.v001.json",
        "source_catalog_sha256": catalog["catalog_sha256"],
        "tool_count": catalog["tool_count"],
        "read_tool_count": catalog["read_tool_count"],
        "write_tool_count": catalog["write_tool_count"],
        "governed_skill_count": len(list((plugin_root / "skills").glob("*/SKILL.md"))),
        "ordinary_live_authority_count": catalog["ordinary_live_authority_count"],
        "env_uop_governed_six_way_arms": catalog["env_uop_governed_six_way_arms"],
        "linked_operational_authorities": catalog["linked_operational_authorities"],
        "hil_only_authorities": catalog["hil_only_authorities"],
        "sdk_planes": catalog["sdk_planes"],
        "public_action_and_env_uop_ai_action_planes_separate": catalog[
            "public_action_and_env_uop_ai_action_planes_separate"
        ],
        "authority_support_systems": catalog["authority_support_systems"],
        "hook_control": catalog["hook_control"],
        "current_implementation_registry_sha256": catalog[
            "current_implementation_registry"
        ]["registry_sha256"],
        "actions": [
            {
                "name": row["name"],
                "title": row["title"],
                "read_only": bool(row["annotations"].get("readOnlyHint")),
                "owner_skill": row["route_contract"]["owner_skill"],
                "schema_sha256": row["schema_sha256"],
            }
            for row in catalog["tools"]
        ],
        "execution_authority": False,
    }
    remote = {
        **remote_core,
        "projection_sha256": hashlib.sha256(_canonical(remote_core))
        .hexdigest()
        .upper(),
    }
    remote_path = (
        plugin_root.parents[1]
        / "apps"
        / "evidence-lane-remote-adapter"
        / "app"
        / "_data"
        / "public-action-registry.json"
    )
    _write_atomic_text(
        remote_path,
        json.dumps(remote, indent=2, ensure_ascii=False) + "\n",
    )
    print(public_path)
    print(remote_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
